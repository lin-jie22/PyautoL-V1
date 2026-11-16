#!/usr/bin/env python3
"""V166 用量探针：`T5`/`T6` 的候选各自走到哪一步（生成 / 判分 / 入池 / 在命中路径上）。

纯测量：不改算子、不设判据。逐标签类记账，且**分母分四层**（纪律 64）：
  生成 ⊇ 判分 ⊇ 入池 ⊇ 终态前沿（≈路径）。
"""
import ast
import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198", "v6_s210", "v6_s236"):
    sys.path.insert(0, str(EV / d))
import idiom_transfer as I          # noqa: E402
import search as S                  # noqa: E402
import v119 as V                    # noqa: E402
import v120 as W                    # noqa: E402
import v121 as X                    # noqa: E402
sys.path.insert(0, str(EV / "v6_s249"))
import v161 as M                    # noqa: E402
sys.path.insert(0, str(EV / "v6_s251"))
import v164 as U                    # noqa: E402
import v165 as N                    # noqa: E402

TID = "g7d3_fmt_amount"
KEEP, ROUNDS = 2, 6
SKIP134 = frozenset({"T1", "T3", "T4"})


def traced(tid, code, Sx, ch, ex, meta, mine, genfn, kinds):
    """与 `v164.replay_gen` 逐行相同，唯一差别：按 kind 记 生成/判分/入池/路径。"""
    C = collections.Counter()
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, s1=s0, n_eval=0, hit=True, trace={}), C
    n_sib = meta["n_sib"]
    frontier = [dict(code=code, s=s0)]
    visited, n = {code}, 0
    trace = {code: (None, None)}
    accepted = set()
    for _ in range(ROUNDS):
        pool, seen = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, srcc) for lab, srcc in
                     genfn(tid, node["code"], Sx, ch, ex)]
            # ⚠ 生成数必须**在这个节点开跑之前**一次性数完：早先版本在候选循环里逐个 +1，
            # 一旦命中就 return，命中点之后的候选没被数到（72 记成 65，纪律 61）。
            for kk in kinds:
                C[f"{kk}_gen"] += sum(1 for _a, lb, _c in cands if lb.split(":")[0] == kk)
            for key, lab, srcc in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                k = lab.split(":")[0]
                if srcc in visited or srcc in seen:
                    continue
                if k in SKIP134:
                    continue
                if k == "B":
                    npv, _best = meta["m"].get(lab.split(":", 2)[2], (0, 0.0))
                    if npv == n_sib:
                        continue
                sc, _ = V.ev(tid, srcc)
                n += 1
                if k in kinds:
                    C[f"{k}_eval"] += 1
                if sc > node["s"]:
                    pool.append(dict(code=srcc, s=sc))
                    seen.add(srcc)
                    trace.setdefault(srcc, (node["code"], lab))
                    accepted.add(srcc)
                    if k in kinds:
                        C[f"{k}_pool"] += 1
                if sc >= 1.0:
                    C.update({f"{k}_path": 0 for k in kinds})
                    for c in trace:
                        pass
                    # 命中路径上的标签
                    cur, labs = srcc, []
                    while cur is not None and trace.get(cur, (None, None))[0] is not None:
                        par, lb = trace[cur]
                        labs.append(lb)
                        cur = par
                    for lb in labs:
                        kk = lb.split(":")[0]
                        if kk in kinds:
                            C[f"{kk}_path"] += 1
                    return dict(s0=s0, s1=sc, n_eval=n, hit=True, trace=trace), C
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:KEEP]
        visited |= {d["code"] for d in pool}
    best = max(frontier, key=lambda d: d["s"])
    # 非命中臂：终态前沿里「被接受过」的标签
    for c in frontier:
        par = trace.get(c["code"], (None, None))[0]
        if par is None:
            continue
        lb = trace[c["code"]][1]
        kk = lb.split(":")[0]
        if kk in kinds:
            C[f"{kk}_front"] += 1
    return dict(s0=s0, s1=best["s"], n_eval=n, hit=False, trace=trace), C


def main():
    corpus = I.harvest()
    impls = []
    for tid in V.TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                continue
            impls.append((tid, h))
    s0 = {(t, h): V.ev(t, corpus[t][h])[0] for t, h in impls}
    mem = json.loads(X.MEM_FILE.read_text())

    def mines(tid, h):
        return {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}

    kinds = {"T5", "T6"}
    T = collections.Counter()
    per = {}
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ch, m = M.chains_meta(corpus, tid, h, s0)
        ex = W.harvest_exprs(corpus, tid, h)
        meta = dict(m=m, n_sib=len(corpus[tid]) - 1)
        r, C = traced(tid, code, Sx, ch, ex, meta, mines(tid, h), N.gen6, kinds)
        T.update(C)
        per[(tid, h)] = (r, C)

    print("## §1 四层分母（全 25 个实现，`U6` 生成器）")
    print("  | 类 | 生成 | 判分 | 入池 | 命中路径 | 终态前沿(未命中臂) |")
    print("  |---|---|---|---|---|---|")
    for k in ("T5", "T6"):
        print(f"  | {k} | {T[k+'_gen']} | {T[k+'_eval']} | {T[k+'_pool']} |"
              f" {T[k+'_path']} | {T[k+'_front']} |")

    print("\n## §2 逐实现：只在有该算子的行")
    print("  | impl | s0 | hit | s1 | T5 生/判/池/路 | T6 生/判/池/路 |")
    print("  |---|---|---|---|---|---|")
    for (tid, h), (r, C) in per.items():
        if not (C["T5_gen"] or C["T6_gen"]):
            continue
        print(f"  | {h[:8]} | {s0[(tid,h)]:.2f} | {'✅' if r['hit'] else '❌'} |"
              f" {r['s1']:.2f} | {C['T5_gen']}/{C['T5_eval']}/{C['T5_pool']}/{C['T5_path']} |"
              f" {C['T6_gen']}/{C['T6_eval']}/{C['T6_pool']}/{C['T6_path']} |")

    tot_g = T["T5_gen"] + T["T6_gen"]
    tot_e = T["T5_eval"] + T["T6_eval"]
    tot_p = T["T5_pool"] + T["T6_pool"]
    tot_h = T["T5_path"] + T["T6_path"]
    print(f"\n  合计：生成 {tot_g}｜判分 {tot_e}（{tot_e/tot_g*100:.1f}% 的生成）｜"
          f"入池 {tot_p}｜路径 {tot_h}")
    print(f"  ⭐ 从未进入任何路径的判分候选：{tot_e - tot_h}/{tot_e}"
          f"（{(tot_e-tot_h)/tot_e*100:.1f}%）")

    print("\n## §3 基线复算（须与 V165 一致：25/25、判分 489）")
    hits = sum(1 for r, _ in per.values() if r["hit"])
    ev = sum(r["n_eval"] for r, _ in per.values())
    print(f"  命中 {hits}/25｜判分 {ev}")


if __name__ == "__main__":
    main()
