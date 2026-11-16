#!/usr/bin/env python3
"""V150：一条知识的单位是键，还是具体编辑。

V149 §216.3 撞到：同一个抽象键 `('C1', "'…' + s[-n:]")` 在坏实现上有 `1.00` 的实例、
在好实现上只有 `0.80` ⇒「这条知识对不对」在键粒度上没有唯一答案。

本轮只问表示层：一个键在**同一个实现**上对应几个具体源码？分数跨度多大？
若跨度普遍 > 0，则「按键记三值」必然依赖 MAX / FIRST / MIN —— 这是表示缺陷，不是实现缺陷。

两个臂（同一搜索器 / 记忆 / 25 实现）：
  ASK         关 `s0>=1.0` 短路、不早停（= V149，2710）  ← 主臂，实例覆盖最全
  ASK_EARLY   关短路 + 早停（= V149，609）              ← 量预算能截断几个实例

`search_trace` 相对 V149 **只加一处**：事件里记 `src`（被判的具体源码）。

⛔ 不新造分数（仍是 `v119.ev`）；⛔ 不改 `gen`/`abstract`/记忆；⛔ 不碰 V79。
"""
from __future__ import annotations

import ast
import collections
import json
import pathlib
import sys
import time

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
BASELINE = ("?", "<baseline>")


def search_trace(tid, h, code, Sx, chains, exprs, mine,
                 stop_on_hit=True, ignore_baseline=False) -> dict:
    """与 `v121.search` 逐行同构。差别四处（都显式）：
    (1) 记录事件 (2) 事件里带 `src` (3) `stop_on_hit` 可关 (4) `ignore_baseline` 可关。
    """
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0 and not ignore_baseline:
        return dict(s0=s0, s1=s0, n_eval=0, hit=True, hit_at=0, n_dup=0, events=[])
    frontier = [dict(code=code, s=s0)]
    visited = {code}
    n_eval = 0
    n_dup = 0
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
                events.append(dict(tid=tid, h=h, key=list(k), score=s, rnd=rnd, src=src))
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


def closure_d1(tid, code, Sx, chains, exprs):
    """d=1 生成闭包，按抽象键分组（零判分）。返回 {key: set(src)}。"""
    per = collections.defaultdict(set)
    for lab, src in X.gen(tid, code, Sx, chains, exprs):
        per[X.abstract(lab)].add(src)
    return per


def cell_instances(r, key):
    """该臂里这个键被判过的实例：按出现顺序去重 src，返回 (scores_in_order, distinct_srcs)。"""
    scores, srcs, seen = [], [], set()
    for e in r["events"]:
        if tuple(e["key"]) != key:
            continue
        scores.append(e["score"])
        if e["src"] not in seen:
            seen.add(e["src"])
            srcs.append(e["src"])
    return scores, srcs


def agg(scores, how):
    if not scores:
        return "UNTRIED", None
    v = {"MAX": max(scores), "FIRST": scores[0], "MIN": min(scores),
         "MEAN": sum(scores) / len(scores)}[how]
    return ("VALID" if v >= 1.0 else "INVALID"), v


def main() -> None:
    t_start = time.time()
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

    V.CACHE.clear()
    arms = {
        "ASK":       run_impls(impls, corpus, mem, False, True),
        "ASK_EARLY": run_impls(impls, corpus, mem, True, True),
    }
    tot = {n: sum(v["n_eval"] for v in a.values()) for n, a in arms.items()}
    to1 = {n: sum(1 for v in a.values() if v["s1"] >= 1.0) for n, a in arms.items()}
    base = {(t, h) for (t, h), r in arms["ASK"].items() if r["s0"] >= 1.0}

    print("## §0 装置自校验（⛔ 不成立则下面全部作废）")
    print(f"  ASK n_eval = {tot['ASK']}（V149 记 2710）｜到 1.0 = {to1['ASK']}/25（记 21）")
    print(f"  ASK_EARLY n_eval = {tot['ASK_EARLY']}（V149 记 609）｜到 1.0 = {to1['ASK_EARLY']}/25（记 21）")
    print(f"  基线已 1.0 的实现 = {len(base)}/25（记 10）｜真实格 universe_real = {len(uni_real)}（记 35）"
          f"｜基线格 = {sum(1 for c in uni_real if (c[0], c[1]) in base)}（记 16）")

    # ------------------------------------------------ §1 结构多重性（零判分）
    print("\n## §1 结构多重性：一个键在生成闭包里对应几个源码（零判分）")
    clos = {}
    n_key_cells = 0
    n_multi = 0
    n_inst = 0
    dist = collections.Counter()
    t0 = time.time()
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ch = V.harvest_chains(corpus, tid, h)
        ex = W.harvest_exprs(corpus, tid, h)
        per = closure_d1(tid, code, Sx, ch, ex)
        clos[(tid, h)] = per
        for k, srcs in per.items():
            n_key_cells += 1
            n_inst += len(srcs)
            dist[len(srcs)] += 1
            if len(srcs) >= 2:
                n_multi += 1
    print(f"  d=1：键-格 {n_key_cells}（跑前量测记 528）｜实例（键×源码）{n_inst}（记 1093）"
          f"｜多实例键 {n_multi}（记 386，{n_multi / n_key_cells:.1%}）")
    print(f"  每格实例数分布 = {dict(sorted(dist.items()))}")
    print(f"  抽象压缩比 = {n_inst / n_key_cells:.2f} 个具体编辑 / 1 个键")
    print(f"  （耗时 {time.time() - t0:.1f}s）")

    # ------------------------------------------------ §2 判分口径
    print("\n## §2 判分口径：每个格的实例构成（ASK 臂）")
    cells = sorted(uni_real)
    info = {}
    for c in cells:
        tid, h, key = c
        scores, srcs = cell_instances(arms["ASK"][(tid, h)], key)
        info[c] = dict(scores=scores, srcs=srcs)
    judged = [c for c in cells if info[c]["scores"]]
    multi_j = [c for c in judged if len(info[c]["srcs"]) >= 2]
    multi_c = [c for c in cells if len(clos[(c[0], c[1])].get(c[2], ())) >= 2]
    print(f"  真实格 {len(cells)}｜有判分 {len(judged)}｜无判分（UNTRIED）{len(cells) - len(judged)}")
    print(f"  ⭐ 判分口径「≥2 个实例」= {len(multi_j)}｜闭包口径「≥2 个实例」= {len(multi_c)}"
          f" ⇒ 两个分母差 {len(multi_c) - len(multi_j)}")
    print(f"  判分口径的实例数分布 = {dict(sorted(collections.Counter(len(info[c]['srcs']) for c in judged).items()))}")
    print("\n  多实例格明细（判分口径，按跨度降序）：")
    rows = []
    for c in multi_j:
        sc = info[c]["scores"]
        rows.append((max(sc) - min(sc), c, sc))
    rows.sort(key=lambda x: -x[0])
    print("  | 实现 | 键 | 实例数 | 判分序列 | 跨度 |")
    print("  |---|---|---|---|---|")
    for sp, c, sc in rows:
        print(f"  | {c[0][:12]}::{c[1]} | {c[2]} | {len(info[c]['srcs'])} | "
              f"{[f'{x:.2f}' for x in sc]} | {sp:.2f} |")

    # ------------------------------------------------ §2b 跑后诊断：W3 押破的成因
    print("\n## §2b W3 押破的成因：判分实例为什么会超过 d=1 闭包（跑后补，⛔ 未预注册）")
    outside = []
    for c in judged:
        cl = clos[(c[0], c[1])].get(c[2], set())
        out = [s for s in info[c]["srcs"] if s not in cl]
        outside.append((c, len(info[c]["srcs"]), len(cl), len(out)))
    print(f"  逐格三口径：判到 {sum(a for _c, a, _b, _d in outside)}｜d=1 闭包 {sum(b for _c, _a, b, _d in outside)}"
          f"｜判到但不在 d=1 闭包里 {sum(d for _c, _a, _b, d in outside)}")
    print(f"  判分实例全部来自 d=1 闭包的格 = {sum(1 for _c, a, _b, d in outside if d == 0)}/{len(judged)}")
    print("  | 实现 | 键 | 判到 | d=1 闭包 | 其中不在闭包 |")
    print("  |---|---|---|---|---|")
    for c, a, b, d in sorted(outside, key=lambda x: -x[3])[:12]:
        print(f"  | {c[0][:12]}::{c[1]} | {c[2]} | {a} | {b} | {d} |")
    print("  ⇒ 机制：`search` 会对 **frontier 上的新代码**再跑一次 `gen`，")
    print("    同一个键在新代码上会产出**新的具体源码**（V148 已量：d=2 给已有键新增 79644 个实例、0 个新键）。")
    print("    ⇒ **一个键的实例集不是 d=1 闭包的函数，而是搜索深度的函数** —— ")
    print("    这比预注册说的「判到的是子集」更糟：**分母本身没有上界**。")

    # ------------------------------------------------ §3 三聚合
    print("\n## §3 三个聚合各给出几个三值")
    tab = {how: collections.Counter() for how in ("MAX", "FIRST", "MIN")}
    for c in cells:
        for how in tab:
            tab[how][agg(info[c]["scores"], how)[0]] += 1
    print("  | 聚合 | VALID | INVALID | UNTRIED |")
    print("  |---|---|---|---|")
    for how in ("MAX", "FIRST", "MIN"):
        t = tab[how]
        print(f"  | {how} | {t['VALID']} | {t['INVALID']} | {t['UNTRIED']} |")
    print("  ⛔ MEAN 不单列：阈值 1.0 时 `mean >= 1.0 ⟺ min >= 1.0`（分数 ∈ [0,1]），")
    print("     它不是第三个独立读数 —— 这一点本身要写进结论，免得有人以为「取平均」能救。")

    diff_mf = [c for c in cells if agg(info[c]["scores"], "MAX")[0] != agg(info[c]["scores"], "FIRST")[0]]
    diff_mm = [c for c in cells if agg(info[c]["scores"], "MAX")[0] != agg(info[c]["scores"], "MIN")[0]]
    both = [c for c in cells
            if agg(info[c]["scores"], "MAX")[0] == "VALID" and agg(info[c]["scores"], "MIN")[0] == "INVALID"]
    print(f"\n  MAX≠FIRST 的格 = {len(diff_mf)}｜MAX≠MIN 的格 = {len(diff_mm)}"
          f"｜MAX=VALID 且 MIN=INVALID 的格 = {len(both)}")
    for c in sorted(set(diff_mm) | set(diff_mf)):
        sc = info[c]["scores"]
        print(f"    {c[0][:12]}::{c[1]} {c[2]}  MAX={agg(sc, 'MAX')[0]} FIRST={agg(sc, 'FIRST')[0]}"
              f" MIN={agg(sc, 'MIN')[0]}  S={[f'{x:.2f}' for x in sc]}")

    # ------------------------------------------------ §4 预算
    print("\n## §4 早停能截断几个实例（ASK vs ASK_EARLY，逐格）")
    e_inst = {}
    for c in cells:
        e_inst[c] = len(cell_instances(arms["ASK_EARLY"][(c[0], c[1])], c[2])[1])
    cut = [c for c in cells if e_inst[c] < len(info[c]["srcs"])]
    flip = []
    for c in cells:
        a = agg(info[c]["scores"], "MAX")[0]
        b = agg(cell_instances(arms["ASK_EARLY"][(c[0], c[1])], c[2])[0], "MAX")[0]
        if a != b:
            flip.append((c, a, b))
    print(f"  早停后被截断的格 = {len(cut)}｜三值不同的格 = {len(flip)}")
    for c, a, b in flip:
        sc = cell_instances(arms["ASK_EARLY"][(c[0], c[1])], c[2])[0]
        print(f"    {c[0][:12]}::{c[1]} {c[2]}  ASK={a}（{len(info[c]['srcs'])} 实例）"
              f" → ASK_EARLY={b}（{e_inst[c]} 实例，S={[f'{x:.2f}' for x in sc]}）")

    print("\n  ⭐ 两个聚合随预算的走向（同 35 格，换臂只换预算）：")
    print("  | 聚合 | ASK(2710) VALID | ASK_EARLY(609) VALID | 差 |")
    print("  |---|---|---|---|")
    for how in ("MAX", "FIRST", "MIN"):
        va = sum(1 for c in cells
                 if agg(info[c]["scores"], how)[0] == "VALID")
        ve = sum(1 for c in cells
                 if agg(cell_instances(arms["ASK_EARLY"][(c[0], c[1])], c[2])[0], how)[0] == "VALID")
        print(f"  | {how} | {va} | {ve} | {ve - va:+d} |")

    # ------------------------------------------------ §5 反证组
    print("\n## §5 反证组")
    fake = ("Z9", "nonsense")
    fake_hit = sum(1 for (tid, h), r in arms["ASK"].items() for e in r["events"]
                   if tuple(e["key"]) == fake)
    print(f"  C1 伪键 {fake} 被判过 {fake_hit} 次 ⇒ "
          f"{'⛔ 装置分不出真假' if fake_hit else '✅ 键集合不是「什么都收」'}")
    zero_spread = [c for c in multi_j if max(info[c]["scores"]) == min(info[c]["scores"])]
    print(f"  C2 装置能输出同分：多实例格里 spread==0 的 = {len(zero_spread)}"
          f"（若为 0 则说明「跨度>0」是检测器的必然输出，O2 无意义）")
    mono = all(e_inst[c] <= len(info[c]["srcs"]) for c in cells)
    print(f"  C3 早停只会更少：ASK_EARLY 实例数 ≤ ASK 逐格成立 = {'✅' if mono else '⛔'}")
    print(f"  C4 换单位就换分母：按键 {n_key_cells} 格 vs 按具体编辑 {n_inst} 格"
          f"（×{n_inst / n_key_cells:.2f}）⇒ 单位选择真的改分母")

    # ------------------------------------------------ §6 判据
    print("\n## §6 判据（预注册 §4）")
    w1 = (tot["ASK"] == 2710 and to1["ASK"] == 21 and tot["ASK_EARLY"] == 609
          and to1["ASK_EARLY"] == 21 and len(base) == 10 and len(uni_real) == 35)
    w2 = bool(diff_mf or diff_mm)
    w3 = len(multi_j) < len(multi_c)
    w4 = bool(flip)
    w5 = (fake_hit == 0) and bool(zero_spread) and mono and n_inst > n_key_cells
    w6 = bool(both)

    def row(tag, ok, txt):
        print(f"  {tag} {'✅' if ok else '⛔'} {txt}")
    row("W1", w1, f"自校验 ASK {tot['ASK']}/{to1['ASK']}、ASK_EARLY {tot['ASK_EARLY']}/{to1['ASK_EARLY']}、"
                  f"基线 {len(base)}/25、universe_real {len(uni_real)}")
    row("W2", w2, f"三聚合至少两两不同：MAX≠FIRST {len(diff_mf)} 格、MAX≠MIN {len(diff_mm)} 格")
    row("W3", w3, f"两个分母不同：判分口径多实例 {len(multi_j)} < 闭包口径 {len(multi_c)}")
    row("W4", w4, f"预算能截断实例并翻三值：截断 {len(cut)} 格、翻 {len(flip)} 格")
    row("W5", w5, f"反证四条：伪键 {fake_hit}｜同分多实例格 {len(zero_spread)}｜"
                  f"早停单调 {'✅' if mono else '⛔'}｜按源码 {n_inst} > 按键 {n_key_cells}")
    row("W6", w6, f"同一格 MAX=VALID 且 MIN=INVALID = {len(both)} 格")

    out = dict(n_impl=len(impls), n_universe_real=len(uni_real), n_base=len(base),
               tot=tot, to1=to1,
               n_key_cells=n_key_cells, n_inst=n_inst, n_multi=n_multi,
               dist={str(k): v for k, v in sorted(dist.items())},
               n_judged=len(judged), n_multi_judged=len(multi_j), n_multi_closure=len(multi_c),
               tab={how: dict(t) for how, t in tab.items()},
               diff_mf=len(diff_mf), diff_mm=len(diff_mm), both=len(both),
               cells={f"{c[0]}::{c[1]}::{c[2]}": dict(
                   scores=info[c]["scores"], n_src=len(info[c]["srcs"]),
                   closure=len(clos[(c[0], c[1])].get(c[2], ())),
                   max=agg(info[c]["scores"], "MAX")[0],
                   first=agg(info[c]["scores"], "FIRST")[0],
                   min=agg(info[c]["scores"], "MIN")[0]) for c in cells},
               cut=len(cut), flip=[f"{c[0]}::{c[1]}::{c[2]}" for c, _a, _b in flip],
               w1=w1, w2=w2, w3=w3, w4=w4, w5=w5, w6=w6,
               wall_s=time.time() - t_start)
    (HERE / "readings.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print(f"\n  已落盘 `readings.json`（{out['wall_s']:.1f}s）")


if __name__ == "__main__":
    main()
