#!/usr/bin/env python3
"""V151：`MAX` 能不能被证伪 —— 登记的那把刀砍空了，改问满覆盖下的极值。

§217.8 登记「把 MAX_ROUNDS 6 → 更大」。跑前量测（PREREG §1）显示 round 从来不是约束
（25/25 实现在 depth ≤2 就 `pool` 空），所以照原计划只会读到「6 == 40」。
⇒ 改问：把覆盖推到「≤2 轮 gen」这一类搜索的上界（满 d=2 闭包），MAX 还动不动。

四臂（同一 gen / abstract / 25 实现 / 35 个真实格）：
  A_DEPLOY  k=2,  rounds=6    部署搜索（须复现 V150 的 2710/21）
  A_R40     k=2,  rounds=40   §217.8 原登记的那把刀
  B_WIDE    k=99, rounds=40   宽 frontier 反事实
  C_FULL    满 d=2 闭包（不剪枝），39072 个待判源码

⛔ 不新造分数（只用 `v119.ev >= 1.0`）；⛔ 不改 gen/abstract/记忆；⛔ 不碰 V79。
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

BASELINE = ("?", "<baseline>")
D3_CAP = 40      # d=3 探针每格最多取多少实例源码（已看/未看两个分母都要报）


def search(tid, h, code, Sx, chains, exprs, mine, rounds, k) -> dict:
    """与 `v121.search` 同构；差异显式：不短路基线、不早停、记录事件与深度、frontier 宽度 k 可调。"""
    s0, _ = V.ev(tid, code)
    frontier = [dict(code=code, s=s0)]
    visited = {code}
    n_eval = 0
    events: list[tuple] = []
    depth = 0
    for rnd in range(1, rounds + 1):
        pool, seen_src = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, src)
                     for lab, src in X.gen(tid, node["code"], Sx, chains, exprs)]
            for key, _lab, src in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                if src in visited or src in seen_src:
                    continue
                s, _ = V.ev(tid, src)
                n_eval += 1
                events.append((key, s, src, rnd))
                if s > node["s"]:
                    pool.append(dict(code=src, s=s))
                    seen_src.add(src)
        if not pool:
            break
        depth = rnd
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:k]
        visited |= {d["code"] for d in pool}
    best = max([s0] + [e[1] for e in events]) if events else s0
    return dict(s0=s0, s1=best, n_eval=n_eval, depth=depth, events=events)


def run_arm(impls, corpus, mem, rounds, k):
    out = {}
    for tid, h in impls:
        mine = {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        ch = V.harvest_chains(corpus, tid, h)
        ex = W.harvest_exprs(corpus, tid, h)
        out[(tid, h)] = search(tid, h, corpus[tid][h], Sx, ch, ex, mine, rounds, k)
    return out


def closure2(tid, code, Sx, chains, exprs):
    """满 d=2 闭包：第 1、2 轮都不剪枝。返回 {key: set(src)} 与该实现见过的全部源码。"""
    frontier = {code}
    seen = {code}
    per = collections.defaultdict(set)
    for _ in range(2):
        nxt = set()
        for c in sorted(frontier):
            for lab, src in X.gen(tid, c, Sx, chains, exprs):
                per[X.abstract(lab)].add(src)
                if src not in seen:
                    seen.add(src)
                    nxt.add(src)
        frontier = nxt
    return per, seen


def inst_from_events(r, key):
    """该臂里这个键被判过的实例：按出现顺序去重源码。"""
    scores, srcs, seen = [], [], set()
    for k, s, src, _rnd in r["events"]:
        if k != key:
            continue
        scores.append(s)
        if src not in seen:
            seen.add(src)
            srcs.append(src)
    return scores, srcs


def verdict(scores):
    if not scores:
        return "UNTRIED"
    return "VALID" if max(scores) >= 1.0 else "INVALID"


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
        for kk in mem[tid]:
            if kk["h"] != h:
                uni_all.add((tid, h, tuple(kk["act"])))
    uni_real = sorted(c for c in uni_all if c[2] != BASELINE)

    V.CACHE.clear()
    t = time.time()
    A = run_arm(impls, corpus, mem, rounds=6, k=2)
    A40 = run_arm(impls, corpus, mem, rounds=40, k=2)
    B = run_arm(impls, corpus, mem, rounds=40, k=99)
    t_arms = time.time() - t

    # ------------------------------------------------ §0 自校验
    tot = {n: sum(v["n_eval"] for v in a.values()) for n, a in
           (("A", A), ("A_R40", A40), ("B_WIDE", B))}
    to1 = {n: sum(1 for v in a.values() if v["s1"] >= 1.0) for n, a in
           (("A", A), ("A_R40", A40), ("B_WIDE", B))}
    base = {(t_, h) for (t_, h), r in A.items() if r["s0"] >= 1.0}
    n_base_cells = sum(1 for c in uni_real if (c[0], c[1]) in base)
    depth_hist = dict(sorted(collections.Counter(r["depth"] for r in A.values()).items()))

    print("## §0 装置自校验（⛔ 不成立则下面全部作废）")
    print(f"  A_DEPLOY n_eval = {tot['A']}（V150 记 2710）｜到 1.0 = {to1['A']}/25（记 21）")
    print(f"  基线已 1.0 的实现 = {len(base)}/25（记 10）｜真实格 = {len(uni_real)}（记 35）"
          f"｜其中基线格 = {n_base_cells}（记 16）")
    print(f"  A 的深度分布（`pool` 非空的最大轮）= {depth_hist}｜run 三个臂耗时 {t_arms:.1f}s")

    # ------------------------------------------------ §1 登记的那把刀
    print("\n## §1 §217.8 登记的那把刀：MAX_ROUNDS 6 → 40")
    same_ev = sum(1 for k_ in A if A[k_]["events"] == A40[k_]["events"])
    print(f"  A 与 A_R40 的**事件序列逐格相同** = {same_ev}/25"
          f"｜n_eval {tot['A']} vs {tot['A_R40']}")
    print(f"  宽 frontier（k=99, R40）B_WIDE：n_eval = {tot['B_WIDE']}"
          f"｜到 1.0 = {to1['B_WIDE']}/25（只作对照，不是部署建议）")
    w2 = (same_ev == len(A))

    # ------------------------------------------------ §2 满覆盖
    print("\n## §2 满 d=2 闭包（C_FULL）：这一类搜索的覆盖上界")
    t = time.time()
    clo = {}
    keys_c = collections.defaultdict(set)
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ch = V.harvest_chains(corpus, tid, h)
        ex = W.harvest_exprs(corpus, tid, h)
        per, seen = closure2(tid, code, Sx, ch, ex)
        clo[(tid, h)] = per
        keys_c[(tid, h)] = seen
    per_impl = sum(len(v) for v in keys_c.values())
    by_tid = collections.defaultdict(set)
    for (tid, _h), seen in keys_c.items():
        by_tid[tid] |= seen
    uniq = sum(len(v) for v in by_tid.values())
    n_pairs = sum(len(srcs) for per in clo.values() for srcs in per.values())
    print(f"  待判源码：按实现计 {per_impl}｜按 `(tid, code)` 去重后（CACHE 实际判分次数）{uniq}"
          f"（跑前量测记 39072）｜键×源码 实例对 {n_pairs}（记 80737）")
    print(f"  建闭包 {time.time() - t:.1f}s（判分走 CACHE，键 = (tid, code)）")

    # ------------------------------------------------ §3 每格读数
    print("\n## §3 每格读数（35 个真实格）")
    rows = []
    for c in uni_real:
        tid, h, key = c
        sc_a, src_a = inst_from_events(A[(tid, h)], key)
        sc_b, src_b = inst_from_events(B[(tid, h)], key)
        src_c = sorted(clo[(tid, h)].get(key, set()))
        sc_c = [V.ev(tid, s)[0] for s in src_c]
        rows.append(dict(cell=c, sc_a=sc_a, n_a=len(src_a), sc_b=sc_b, n_b=len(src_b),
                         sc_c=sc_c, n_c=len(src_c), src_c=src_c))
    n_inst_a = sum(r["n_a"] for r in rows)
    n_inst_c = sum(r["n_c"] for r in rows)
    print(f"  实例总数：A = {n_inst_a}｜C = {n_inst_c}（×{n_inst_c / max(n_inst_a, 1):.1f}）")

    tab = collections.Counter()
    for r in rows:
        tab[verdict(r["sc_a"])] += 1
    tab_b = collections.Counter()
    for r in rows:
        tab_b[verdict(r["sc_b"])] += 1
    tab_c = collections.Counter()
    for r in rows:
        tab_c[verdict(r["sc_c"])] += 1
    n_inst_b = sum(r["n_b"] for r in rows)
    ns = sorted(r["n_c"] for r in rows)
    print(f"  C 的每格实例数：min {ns[0]}｜median {ns[len(ns) // 2]}｜max {ns[-1]}"
          f"｜<20 个实例的格 = {sum(1 for x in ns if x < 20)}/35")
    print("\n  | 口径 | 实例(35格) | VALID | INVALID | UNTRIED |")
    print("  |---|---|---|---|---|")
    for tag, t_, nb in (("A_DEPLOY k=2,R6", tab, n_inst_a),
                        ("B_WIDE k=99,R40", tab_b, n_inst_b),
                        ("C_FULL d=2 闭包", tab_c, n_inst_c)):
        print(f"  | {tag} | {nb} | {t_['VALID']} | {t_['INVALID']} | {t_['UNTRIED']} |")

    n_valid_c = tab_c["VALID"]
    n_valid_a = tab["VALID"]
    print(f"\n  ⭐ MAX=VALID：A {n_valid_a} → C {n_valid_c}（差 {n_valid_c - n_valid_a:+d}）")
    bucket = ("(a) 26 不动" if n_valid_c <= 26 else
              "(c) 33 到顶" if n_valid_c >= 33 else "(b) 27~32 动但未到顶")
    print(f"  预注册桶：{bucket}")

    print("\n  MIN 在 C 上的读数（描述，不押）：")
    n_min_valid = sum(1 for r in rows if r["sc_c"] and min(r["sc_c"]) >= 1.0)
    print(f"    MIN=VALID 的格 = {n_min_valid}/{len(rows)}"
          f"｜MIN=INVALID = {sum(1 for r in rows if r['sc_c'] and min(r['sc_c']) < 1.0)}"
          f"｜UNTRIED = {sum(1 for r in rows if not r['sc_c'])}")

    print("\n  ⭐ 两个极值随覆盖的走向（同 35 格，只换覆盖）：")
    print("  | 臂 | 实例 | MAX=VALID | MIN=VALID | 同时 MAX=VALID 且 MIN=INVALID |")
    print("  |---|---|---|---|---|")
    min_line = {}
    for tag, fld in (("A_DEPLOY", "sc_a"), ("B_WIDE", "sc_b"), ("C_FULL", "sc_c")):
        mv = sum(1 for r in rows if r[fld] and min(r[fld]) >= 1.0)
        xv = sum(1 for r in rows if r[fld] and max(r[fld]) >= 1.0)
        both_ = sum(1 for r in rows if r[fld] and max(r[fld]) >= 1.0 and min(r[fld]) < 1.0)
        nb = sum(r["n_" + fld[-1]] for r in rows)
        min_line[tag] = dict(max_valid=xv, min_valid=mv, both=both_)
        print(f"  | {tag} | {nb} | {xv} | {mv} | **{both_}** |")

    print("\n  §3c 交叉表（目标正确性 × 知识三值），A 与 C 各一张：")
    for tag, fld in (("A_DEPLOY", "sc_a"), ("C_FULL", "sc_c")):
        ct = collections.Counter()
        for r in rows:
            tgt = "目标已正确" if (r["cell"][0], r["cell"][1]) in base else "目标待修"
            ct[(tgt, verdict(r[fld]))] += 1
        print(f"    {tag}: 待修 VALID {ct[('目标待修', 'VALID')]}/INVALID {ct[('目标待修', 'INVALID')]}"
              f"/UNTRIED {ct[('目标待修', 'UNTRIED')]}｜"
              f"已正确 VALID {ct[('目标已正确', 'VALID')]}/INVALID {ct[('目标已正确', 'INVALID')]}"
              f"/UNTRIED {ct[('目标已正确', 'UNTRIED')]}")

    # ------------------------------------------------ §4 翻转
    print("\n## §4 三个臂之间的三值翻转（A → B → C）")
    flips = [(r, verdict(r["sc_a"]), verdict(r["sc_b"]), verdict(r["sc_c"])) for r in rows
             if len({verdict(r["sc_a"]), verdict(r["sc_b"]), verdict(r["sc_c"])}) > 1]
    print(f"  三臂不完全一致的格 = {len(flips)}/{len(rows)}"
          f"｜A≠C = {sum(1 for r in rows if verdict(r['sc_a']) != verdict(r['sc_c']))}"
          f"｜A≠B = {sum(1 for r in rows if verdict(r['sc_a']) != verdict(r['sc_b']))}"
          f"｜B≠C = {sum(1 for r in rows if verdict(r['sc_b']) != verdict(r['sc_c']))}")
    print("  | 实现 | 键 | A 实例/三值 | B 实例/三值 | C 实例/三值 | C MAX |")
    print("  |---|---|---|---|---|---|")
    for r, va, vb, vc in flips:
        c = r["cell"]
        print(f"  | {c[0][:12]}::{c[1]} | {c[2]} | {r['n_a']}/{va} | {r['n_b']}/{vb} | "
              f"{r['n_c']}/{vc} | {(max(r['sc_c']) if r['sc_c'] else float('nan')):.2f} |")

    # ------------------------------------------------ §5 硬墙 + d=3 探针
    print("\n## §5 C 上仍非 VALID 的格：是「没实例」还是「有实例到不了 1.0」")
    bad = [r for r in rows if verdict(r["sc_c"]) != "VALID"]
    zero = [r for r in bad if not r["sc_c"]]
    nonz = [r for r in bad if r["sc_c"]]
    print(f"  仍非 VALID = {len(bad)}：其中 **0 实例** = {len(zero)}、有实例 = {len(nonz)}")
    for r in zero:
        print(f"    ZERO  {r['cell'][0][:20]} {r['cell'][1]} {r['cell'][2]}  （C 里一个实例都没有）")
    for r in nonz:
        sc = r["sc_c"]
        print(f"    NONZ  {r['cell'][0][:20]} {r['cell'][1]} {r['cell'][2]}  "
              f"实例 {r['n_c']}｜max={max(sc):.2f}｜分布 {dict(sorted(collections.Counter(sc).items()))}"
              f"｜B 臂实例 {r['n_b']}、max={max(r['sc_b']) if r['sc_b'] else float('nan'):.2f}")

    print(f"\n  d=3 探针（每格取 C 实例前 {D3_CAP} 个源码，各再跑一轮 gen）：")
    d3 = []
    for r in nonz:
        tid, h, key = r["cell"]
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        ch = V.harvest_chains(corpus, tid, h)
        ex = W.harvest_exprs(corpus, tid, h)
        srcs = r["src_c"][:D3_CAP]
        best, seen_children = -1.0, set()
        for s in srcs:
            for lab, child in X.gen(tid, s, Sx, ch, ex):
                if X.abstract(lab) != key or child in seen_children:
                    continue
                seen_children.add(child)
                best = max(best, V.ev(tid, child)[0])
        d3.append((r["cell"], len(srcs), r["n_c"], len(seen_children), best))
        print(f"    {r['cell'][0][:20]} {r['cell'][2]}：已看 {len(srcs)}/{r['n_c']} 个父源码 "
              f"⇒ 子代里同键源码 {len(seen_children)} 个，最高分 "
              f"{best if best >= 0 else float('nan'):.2f}")

    # ------------------------------------------------ §6 反证
    print("\n## §6 反证 / 阴性对照")
    fake = ("Z9", "nonsense")
    fake_hit = sum(1 for a in (A, B) for r in a.values() for e in r["events"] if e[0] == fake)
    print(f"  C1 伪键 {fake} 命中 = {fake_hit} ⇒ "
          f"{'✅' if fake_hit == 0 else '⛔ 键集合什么都收'}")
    mono = sum(1 for r in rows if (max(r["sc_a"]) if r["sc_a"] else -1) <= (max(r["sc_c"]) if r["sc_c"] else -1))
    print(f"  C2 单调 MAX(A) ≤ MAX(C)（结构性）：{mono}/{len(rows)}"
          f" {'✅' if mono == len(rows) else '⛔ C 不是 A 的超集'}")
    grew = sum(1 for r in rows if r["n_c"] > r["n_a"])
    same_n = sum(1 for r in rows if r["n_c"] == r["n_a"])
    print(f"  C3 分母变大：n_inst(C) > n_inst(A) 的格 = {grew}/{len(rows)}｜相同的 = {same_n}")
    print(f"  C4 d=3 探针已看/未看：每格 {D3_CAP} / n_c（见 §5 逐行）")
    inside = 0
    outside = 0
    for (tid, h), r in B.items():
        cset = {(k, s) for k, srcs in clo[(tid, h)].items() for s in srcs}
        for e in r["events"]:
            if (e[0], e[2]) in cset:
                inside += 1
            else:
                outside += 1
    bdepth = dict(sorted(collections.Counter(r["depth"] for r in B.values()).items()))
    print(f"  C5 B_WIDE 的 (键,源码) 落在 C 里 = {inside}｜落在 C 外 = {outside}"
          f"｜B 的深度分布 {bdepth} ⇒ "
          f"{'✅ C 是上界' if outside == 0 else '⛔ C 不是上界'}")
    if outside:
        print("     ⚠️ 成因（跑后发现的口径错）：`depth` 只记 `pool` **非空**的最大轮，")
        print("        而空池的那一轮**仍然会把候选判一遍** ⇒ 部署搜索实际评估到深一层的源码。")
        print("        ⇒ C 是「覆盖率上的一个点」，**不是**这一类搜索的上界；本轮的结论据此改写。")

    # ------------------------------------------------ §7 判据
    print("\n## §7 判据（预注册 §4）")
    w1 = (tot["A"] == 2710 and to1["A"] == 21 and len(base) == 10
          and len(uni_real) == 35 and n_base_cells == 16)
    w3 = n_valid_c
    w4 = (len(zero) == 2 and all(r["cell"][0].startswith("g7d2_dedupe_keep") for r in zero))
    w5 = n_inst_c >= 10 * max(n_inst_a, 1)
    w6 = "reachability" if (len(nonz) == 0 and len(zero) > 0) else "knowledge"

    def row(tag, ok, txt):
        print(f"  {tag} {'✅' if ok else '⛔'} {txt}")
    row("W1", w1, f"自校验 A {tot['A']}/{to1['A']}、基线 {len(base)}/25、真实格 {len(uni_real)}、基线格 {n_base_cells}")
    row("W2", w2, f"死旋钮：A 与 A_R40 事件逐格相同 {same_ev}/25")
    print(f"  W3 {'✅' if n_valid_c == 33 else '⛔'} MAX=VALID 在满覆盖下 = {n_valid_c}"
          f"（押 (c) 33；实际桶 {bucket}）")
    row("W4", w4, f"两个硬墙在 C 里 0 实例：{len(zero)} 格")
    row("W5", w5, f"分母操作检验：实例 {n_inst_a} → {n_inst_c}（×{n_inst_c / max(n_inst_a, 1):.1f}，要求 ≥10×）")
    print(f"  W6 判定域 = {w6}"
          f"（'reachability' ⇒ 非 VALID 全是 0 实例；'knowledge' ⇒ 存在有实例到不了 1.0 的格）")

    out = dict(
        n_impl=len(impls), n_universe_real=len(uni_real), n_base=len(base),
        n_base_cells=n_base_cells, tot=tot, to1=to1, depth_hist={str(k): v for k, v in depth_hist.items()},
        same_events=same_ev, uniq_src_c=uniq, n_pairs_c=n_pairs,
        n_inst_a=n_inst_a, n_inst_c=n_inst_c,
        tab_a=dict(tab), tab_b=dict(tab_b), tab_c=dict(tab_c),
        n_min_valid_c=n_min_valid, extremes=min_line,
        bucket=bucket, domain=w6,
        cells=[dict(cell=list(r["cell"]), n_a=r["n_a"], n_c=r["n_c"],
                    v_a=verdict(r["sc_a"]), v_c=verdict(r["sc_c"]),
                    max_a=(max(r["sc_a"]) if r["sc_a"] else None),
                    max_c=(max(r["sc_c"]) if r["sc_c"] else None),
                    min_c=(min(r["sc_c"]) if r["sc_c"] else None)) for r in rows],
        flips=[[list(r["cell"]), va, vb, vc] for r, va, vb, vc in flips],
        zero=[[list(r["cell"]), r["n_c"]] for r in zero],
        nonz=[[list(r["cell"]), r["n_c"], max(r["sc_c"])] for r in nonz],
        d3=[[list(c), int(seen), int(tot_), int(kids), (None if b < 0 else b)]
            for c, seen, tot_, kids, b in d3],
        w1=w1, w2=w2, n_valid_c=n_valid_c, w4=w4, w5=w5,
        wall_s=time.time() - t_start)
    (HERE / "readings.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n  已落盘 `readings.json`（{out['wall_s']:.1f}s）")


if __name__ == "__main__":
    main()
