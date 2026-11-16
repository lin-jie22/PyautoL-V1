#!/usr/bin/env python3
"""V149：已答对的实现要不要问 —— 不问，是省了钱，还是省了「知道」。

V148 §215.7 登记的缺口：`v121.search:77` 在 `s0 >= 1.0` 时**直接返回**，
⇒ 25 个实现里 10 个从不进入自省；记忆库永远收不到「在已经正确的实现上，
这条动作成不成立」的证据 ⇒ 验证样本有选择偏差。

四个臂（同一搜索器、同一动作空间、同一记忆、同一 25 实现）：

  STOP        部署态：`s0>=1.0` 短路 + 一到 1.0 就 return        （= V146/V147，588）
  NOSTOP      反事实：只关早停，短路仍在                          （= V147，2301）
  ASK         反事实：只关短路（`ignore_baseline=True`），不早停   ← 本轮主臂
  ASK_EARLY   反事实：关短路 + 早停（若真部署「也问」，这是最省形态）

三值（对每个记忆格，口径 = ASK 臂里该 (实现,键) 的全部判分）：
  VALID       最高分 ≥ 1.0  ⇒ 这条动作**不破坏**已经正确的实现
  INVALID     有判分但最高分 < 1.0 ⇒ 这条动作**弄坏了**它
  UNTRIED     一次都没被判过（产不出该键，或没排到）

⛔ 不改 `gen` / `abstract` / `v121.py`（那是证据）；⛔ 不碰 V79；
⛔ 不新造分数：判分仍是 `v119.ev`，候选仍是 `v121.gen`，记忆仍是 `v121.MEM_FILE`。
"""
from __future__ import annotations

import ast
import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for _d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198", "v6_s210", "v6_s236"):
    sys.path.insert(0, str(EV / _d))

import idiom_transfer as I     # noqa: E402
import search as S             # noqa: E402
import v119 as V               # noqa: E402
import v120 as W               # noqa: E402
import v121 as X               # noqa: E402

MAX_ROUNDS = X.MAX_ROUNDS
BASELINE = ("?", "<baseline>")          # 哨兵键：`abstract()` 的六条正则都产不出它


def search_trace(tid, h, code, Sx, chains, exprs, mine,
                 stop_on_hit=True, ignore_baseline=False) -> dict:
    """与 `v121.search` 逐行同构。差别只有三处（都显式）：
    (1) 记录事件 (2) `stop_on_hit` 可关 (3) `ignore_baseline` 可关掉 `s0>=1.0` 短路。
    """
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0 and not ignore_baseline:
        return dict(s0=s0, s1=s0, n_eval=0, hit=True, hit_at=0, n_dup=0, events=[])
    frontier = [dict(code=code, s=s0)]
    visited = {code}
    n_eval = 0
    n_dup = 0                      # 同一源码被重复判分的次数（`seen_src` 只在提分时才登记）
    seen_eval = {code}
    events: list[dict] = []
    best_s = s0
    hit_at = -1
    for rnd in range(1, MAX_ROUNDS + 1):
        pool, seen_src = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, src)
                     for lab, src in X.gen(tid, node["code"], Sx, chains, exprs)]
            for k, _lab, src in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                if src in visited or src in seen_src:
                    continue
                s, _ = V.ev(tid, src)
                n_eval += 1
                if src in seen_eval:
                    n_dup += 1
                else:
                    seen_eval.add(src)
                events.append(dict(tid=tid, h=h, key=list(k), score=s, rnd=rnd))
                if s > best_s:
                    best_s = s
                if s >= 1.0 and hit_at < 0:
                    hit_at = n_eval
                    if stop_on_hit:
                        return dict(s0=s0, s1=s, n_eval=n_eval, hit=True, n_dup=n_dup,
                                    hit_at=hit_at, events=events)
                if s > node["s"]:
                    pool.append(dict(code=src, s=s))
                    seen_src.add(src)
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:2]
        visited |= {d["code"] for d in pool}
    return dict(s0=s0, s1=best_s, n_eval=n_eval, hit=hit_at > 0, hit_at=hit_at,
                n_dup=n_dup, events=events)


def run_impls(impls, corpus, mem, stop_on_hit, ignore_baseline):
    out = {}
    for tid, h in impls:
        mine = {(a, b) for k in mem[tid] if k["h"] != h for a, b in [tuple(k["act"])]}
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        chains = V.harvest_chains(corpus, tid, h)
        exprs = W.harvest_exprs(corpus, tid, h)
        out[(tid, h)] = search_trace(tid, h, corpus[tid][h], Sx, chains, exprs,
                                     mine, stop_on_hit=stop_on_hit,
                                     ignore_baseline=ignore_baseline)
    return out


# ---------------------------------------------------------------- 反证组 C4：正例路径
def alpha_rename(code: str) -> str:
    """把函数参数与函数内被赋值的名字统一改名。语义等价变换，**不改语料**（只用于自检）。"""
    tree = ast.parse(code)
    fn = tree.body[0]
    assert isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)), type(fn)
    local = {a.arg for a in fn.args.args}
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            local.add(n.id)
    if not local:
        return code
    mapping = {name: f"_z{i}" for i, name in enumerate(sorted(local))}

    class R(ast.NodeTransformer):
        def visit_Name(self, n):
            if n.id in mapping:
                return ast.copy_location(ast.Name(id=mapping[n.id], ctx=n.ctx), n)
            return n

        def visit_arg(self, n):
            if n.arg in mapping:
                n.arg = mapping[n.arg]
            return n

    new = R().visit(tree)
    ast.fix_missing_locations(new)
    return ast.unparse(new)


def main() -> None:
    corpus = I.harvest()
    impls = []
    for tid in V.TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                continue
            impls.append((tid, h))
    mem = json.loads(X.MEM_FILE.read_text(encoding="utf-8"))

    uni_all = set()
    for tid, h in impls:
        for k in mem[tid]:
            if k["h"] != h:
                uni_all.add((tid, h, tuple(k["act"])))
    uni_real = {c for c in uni_all if c[2] != BASELINE}

    V.CACHE.clear()                      # 只清一次；⚠️ 之后各臂共享缓存 ⇒ 墙钟不可比
    arms = {
        "STOP":       run_impls(impls, corpus, mem, True, False),
        "NOSTOP":     run_impls(impls, corpus, mem, False, False),
        "ASK":        run_impls(impls, corpus, mem, False, True),
        "ASK_EARLY":  run_impls(impls, corpus, mem, True, True),
    }
    tot = {n: sum(v["n_eval"] for v in a.values()) for n, a in arms.items()}
    to1 = {n: sum(1 for v in a.values() if v["s1"] >= 1.0) for n, a in arms.items()}
    base = {(t, h) for (t, h), r in arms["STOP"].items() if r["s0"] >= 1.0}

    print("## §0 装置自校验（⛔ 不成立则下面全部作废）")
    print(f"  基线已 1.0 的实现 = {len(base)}/{len(impls)}"
          f"（V148 记 10）｜真实格 universe_real = {len(uni_real)}（记 35）"
          f"｜基线格 = {sum(1 for c in uni_real if (c[0], c[1]) in base)}（记 16）")
    for n in ("STOP", "NOSTOP", "ASK", "ASK_EARLY"):
        print(f"  {n:10s} n_eval = {tot[n]:5d}｜到 1.0 = {to1[n]}/25")
    print("  ⭐ STOP 588 / 21、NOSTOP 2301 / 21 必须逐字复现（V146/V147）")

    # 前缀/等价性：只关短路的臂，在**活跃实现**上必须与原臂逐事件相同
    bad = []
    for tid, h in impls:
        if (tid, h) in base:
            continue
        if arms["NOSTOP"][(tid, h)]["events"] != arms["ASK"][(tid, h)]["events"]:
            bad.append((tid, h))
    print(f"  等价性（活跃实现的 NOSTOP 事件 == ASK 事件）："
          f"{'✅' if not bad else '⛔'}（坏 {len(bad)}）")

    print("\n## §1 成本：短路省了多少次判分（分母 = NOSTOP 的 2301）")
    for n in ("ASK", "ASK_EARLY"):
        d = tot[n] - tot["NOSTOP"]
        print(f"  {n:10s} 比 NOSTOP 多 {d:4d} 次（×{tot[n] / tot['NOSTOP']:.2f}，"
              f"占 NOSTOP 的 {d / tot['NOSTOP']:.1%}）")
    print("  基线实现各自的事件数（STOP/NOSTOP 应为 0，ASK 应为该实现的 d=1 候选数）：")
    print("  | 实现 | s0 | STOP | NOSTOP | ASK | ASK_EARLY |")
    print("  |---|---|---|---|---|---|")
    for tid, h in impls:
        if (tid, h) not in base:
            continue
        row = [arms[n][(tid, h)]["n_eval"] for n in ("STOP", "NOSTOP", "ASK", "ASK_EARLY")]
        print(f"  | {tid[:6]}::{h} | {arms['STOP'][(tid, h)]['s0']:.2f} | "
              f"{row[0]} | {row[1]} | {row[2]} | {row[3]} |")

    dup = {n: sum(v["n_dup"] for v in a.values()) for n, a in arms.items()}
    print("\n## §1b 多出来的 49 次是什么（跑后补，⛔ 未预注册）")
    print("  重复判分（同一源码在同一实现里被判两次）："
          + "｜".join(f"{n}={dup[n]}" for n in ("STOP", "NOSTOP", "ASK", "ASK_EARLY")))
    print("  机制：`v121.search` 的 `seen_src.add(src)` 写在 `if s > node[\"s\"]:` **里面**；")
    print("        基线实现 s0=1.0，没有任何候选能超过它 ⇒ 去重表永远是空的 ⇒")
    print("        文本重复的候选被逐个重判。这是**短路之外**的另一笔浪费，只在问基线实现时才付。")

    print("\n## §2 14 个可寻址格得到什么三值（口径 = ASK 臂对该 (实现,键) 的全部判分）")
    askev = collections.defaultdict(list)
    for (tid, h), r in arms["ASK"].items():
        for e in r["events"]:
            askev[(tid, h, tuple(e["key"]))].append(e["score"])
    base_cells = sorted(c for c in uni_real if (c[0], c[1]) in base)
    verdict: dict = {}
    for c in base_cells:
        sc = askev.get(c, [])
        if not sc:
            verdict[c] = ("UNTRIED", None, len(sc))
        elif max(sc) >= 1.0:
            verdict[c] = ("VALID", max(sc), len(sc))
        else:
            verdict[c] = ("INVALID", max(sc), len(sc))
    cnt = collections.Counter(v[0] for v in verdict.values())
    print(f"  基线格 {len(base_cells)} 个：VALID={cnt['VALID']}  INVALID={cnt['INVALID']}"
          f"  UNTRIED={cnt['UNTRIED']}")
    for c in base_cells:
        v, mx, n = verdict[c]
        print(f"    {v:8s} max={'-' if mx is None else f'{mx:.2f}'} n={n}  "
              f"{c[0][:20]:20s} {c[1]}  {c[2]}")

    print("\n## §2b 「也问，但早停」会漏掉什么（跑后补，⛔ 未预注册）")
    earlyev = collections.defaultdict(list)
    for (tid, h), r in arms["ASK_EARLY"].items():
        for e in r["events"]:
            earlyev[(tid, h, tuple(e["key"]))].append(e["score"])
    ev_cnt = collections.Counter()
    for c in base_cells:
        sc = earlyev.get(c, [])
        ev_cnt["UNTRIED" if not sc else ("VALID" if max(sc) >= 1.0 else "INVALID")] += 1
    print(f"  ASK_EARLY 判分 = {tot['ASK_EARLY']}（STOP {tot['STOP']} ⇒ 只多 "
          f"{tot['ASK_EARLY'] - tot['STOP']} 次）")
    print(f"  三值：VALID={ev_cnt['VALID']}  INVALID={ev_cnt['INVALID']}  "
          f"UNTRIED={ev_cnt['UNTRIED']}（ASK 是 12/2/2）")
    lost = [c for c in base_cells if verdict[c][0] != ("UNTRIED" if not earlyev.get(c)
                                                       else ("VALID" if max(earlyev[c]) >= 1.0
                                                             else "INVALID"))]
    print(f"  两臂结论不同的格 = {len(lost)}")
    for c in lost:
        print(f"    {c[0][:20]:20s} {c[1]}  {c[2]}  ASK={verdict[c][0]}"
              f"  ASK_EARLY={('UNTRIED' if not earlyev.get(c) else ('VALID' if max(earlyev[c]) >= 1.0 else 'INVALID'))}")

    print("\n## §3 ⭐ 交叉表：目标正确性 × 知识有效性（35 个真实格）")
    def knot(c):
        sc = askev.get(c, [])
        if not sc:
            return "UNTRIED"
        return "VALID" if max(sc) >= 1.0 else "INVALID"
    tab = collections.Counter()
    for c in uni_real:
        tab[("目标已正确" if (c[0], c[1]) in base else "目标待修", knot(c))] += 1
    print("  | | VALID | INVALID | UNTRIED |")
    print("  |---|---|---|---|")
    for ax in ("目标待修", "目标已正确"):
        print(f"  | {ax} | {tab[(ax, 'VALID')]} | {tab[(ax, 'INVALID')]} | {tab[(ax, 'UNTRIED')]} |")
    print("  ⇒ 若两轴可互相推出，表里只会有一条对角线；四格都非空即证明两件事必须分开记。")

    print("\n## §3b 唯一的两条「弄坏了」长什么样（跑后补，⛔ 未预注册）")
    for c in base_cells:
        if verdict[c][0] != "INVALID":
            continue
        tid, h, key = c
        src_h = [k["h"] for k in mem[tid] if tuple(k["act"]) == key]
        print(f"  {tid} :: {key}（这条动作来自 {src_h}）")
        for h2 in sorted(corpus[tid]):
            sc = askev.get((tid, h2, key), [])
            s0 = arms["STOP"][(tid, h2)]["s0"]
            print(f"    {h2}  s0={s0:.2f}  {key} 的得分 = "
                  f"{[f'{x:.2f}' for x in sc] if sc else '未判（产不出/已到 1.0 不搜）'}")

    print("\n## §4 这笔钱买到什么：每次判分的去向（ASK 臂，基线实现的判分）")
    b_ev = [e for (tid, h), r in arms["ASK"].items() if (tid, h) in base for e in r["events"]]
    uni_impl = collections.defaultdict(set)
    for c in uni_real:
        uni_impl[(c[0], c[1])].add(c[2])
    nonmem = sum(1 for e in b_ev if tuple(e["key"]) not in uni_impl[(e["tid"], e["h"])])
    print(f"  基线实现共判 {len(b_ev)} 次｜其中键不落在本实现的任何记忆格上 = {nonmem}"
          f"（{nonmem / max(1, len(b_ev)):.1%}）")
    print(f"  买到 VALID/INVALID 判定的格 = {cnt['VALID'] + cnt['INVALID']}"
          f" ⇒ {len(b_ev) / max(1, cnt['VALID'] + cnt['INVALID']):.1f} 次判分 / 格")
    print(f"  答案（s1）变化：STOP → ASK 逐实现 s1 有差异 = "
          f"{sum(1 for k in impls if arms['STOP'][k]['s1'] != arms['ASK'][k]['s1'])} 个")
    print(f"  轮次（结构性）：ASK 臂基线实现的事件轮次分布 = "
          f"{dict(collections.Counter(e['rnd'] for e in b_ev))}")

    print("\n## §5 反证组")
    fake = ("Z9", "nonsense")
    fake_hit = sum(1 for e in b_ev if tuple(e["key"]) == fake)
    print(f"  C1 伪键 {fake} 在 ASK 臂被判过 {fake_hit} 次"
          f" ⇒ {'⛔ 装置分不出真假' if fake_hit else '✅ 「没判定」不是「判定成伪键」'}")
    saved = tot["ASK"] - tot["NOSTOP"]
    zero_base = sum(arms['NOSTOP'][k]['n_eval'] for k in impls if k in base)
    print(f"  C2 短路确实省了钱：NOSTOP 里基线实现的判分 = {zero_base}（应为 0）"
          f"｜ASK 里 = {saved} ⇒ 省下的正是这 {saved} 次")
    print(f"  C3 计数器两侧都非空：拿到判定的格 = {cnt['VALID'] + cnt['INVALID']}"
          f"｜一直没判到的格 = {cnt['UNTRIED']}（V148 枚举预测 2 个硬墙）")
    same = diff = 0
    for tid, h in impls:
        if (tid, h) not in base:
            continue
        a = alpha_rename(corpus[tid][h])
        s0, bm0 = V.ev(tid, corpus[tid][h])
        s1, bm1 = V.ev(tid, a)
        if bm0 == bm1:
            same += 1
        else:
            diff += 1
    print(f"  C4 正例路径（语义等价变换能被判成 VALID）：{same}/{same + diff} 个基线实现"
          f"的 alpha 改名版 bitmap 逐位相同 ⇒ {'✅ 检测器能输出 VALID' if diff == 0 else '⛔ 检测器坏了'}")

    print("\n## §6 判据（预注册 §4）")
    w1 = (tot["STOP"] == 588 and to1["STOP"] == 21 and tot["NOSTOP"] == 2301
          and to1["NOSTOP"] == 21 and not bad)
    w2 = all(r["rnd"] == 1 for r in b_ev) and bool(b_ev)
    w3 = (cnt["VALID"] + cnt["INVALID"]) == 14 and cnt["UNTRIED"] == 2
    w4 = all(arms["STOP"][k]["s1"] == arms["ASK"][k]["s1"] for k in impls)
    w5 = (fake_hit == 0) and (zero_base == 0) and (diff == 0)
    w6 = tab[("目标已正确", "VALID")] > 0 and tab[("目标已正确", "INVALID")] > 0
    ok_valid = tab[("目标已正确", "VALID")]
    ok_invalid = tab[("目标已正确", "INVALID")]
    def row(tag, ok, txt):
        print(f"  {tag} {'✅' if ok else '⛔'} {txt}")
    row("W1", w1, f"自校验 STOP {tot['STOP']}/{to1['STOP']}、"
                  f"NOSTOP {tot['NOSTOP']}/{to1['NOSTOP']}、等价性{'✅' if not bad else '⛔'}")
    row("W2", w2, f"不短路也不会更深：基线实现的事件轮次分布 = "
                  f"{dict(collections.Counter(e['rnd'] for e in b_ev))}（押全在 rnd=1）")
    row("W3", w3, f"16 格里拿到三值 = {cnt['VALID'] + cnt['INVALID']}、一直没判到 = "
                  f"{cnt['UNTRIED']}（押 14 / 2，与 V148 枚举独立吻合）")
    row("W4", w4, f"答案不变：25 个实现 s1 逐实现相同 = "
                  f"{sum(1 for k in impls if arms['STOP'][k]['s1'] == arms['ASK'][k]['s1'])}/25")
    row("W5", w5, f"反证三条：伪键 {fake_hit} 次｜NOSTOP 基线判分 {zero_base}｜"
                  f"alpha 正例 {same}/{same + diff}")
    row("W6", w6, f"两轴不可互推：目标已正确 × VALID {ok_valid} / INVALID {ok_invalid}"
                  f"（四格都非空才算）")

    out = dict(n_impl=len(impls), n_universe_real=len(uni_real), n_base_impl=len(base),
               n_base_cells=len(base_cells),
               tot=tot, to1=to1,
               verdict={f"{c[0]}::{c[1]}::{c[2]}": list(v) for c, v in verdict.items()},
               tab={f"{a}|{b}": n for (a, b), n in tab.items()},
               base_evals=len(b_ev), nonmem=nonmem,
               alpha_ok=same, alpha_bad=diff,
               w1=w1, w2=w2, w3=w3, w4=w4, w5=w5, w6=w6)
    (HERE / "readings.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print("\n  已落盘 `readings.json`")


if __name__ == "__main__":
    main()
