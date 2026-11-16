#!/usr/bin/env python3
"""V160 探索性补量（**不进判据**）：随机跳掉与 NO_T1 / NO_134 **同样的比例**，命中会掉多少。

A6 只对 NO_T3 的 8.5% 做了置换。NO_134 跳 49.6%，必须同比例对照，否则「跳得多所以省」无法排除。
"""
import ast
import json
import pathlib
import random
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

ROUNDS, KEEP, SEEDS = 6, 2, 200


def run(tid, code, Sx, ch, ex, mine, p=None, rnd=None):
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0:
        return 0, True
    frontier = [dict(code=code, s=s0)]
    visited, n = {code}, 0
    for _ in range(ROUNDS):
        pool, seen = [], set()
        for node in frontier:
            cands = [(X.abstract(l), l, s) for l, s in X.gen(tid, node["code"], Sx, ch, ex)]
            for key, _l, src in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                if src in visited or src in seen:
                    continue
                if p is not None and rnd.random() < p:
                    continue
                s, _ = V.ev(tid, src)
                n += 1
                if s >= 1.0:
                    return n, True
                if s > node["s"]:
                    pool.append(dict(code=src, s=s))
                    seen.add(src)
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:KEEP]
        visited |= {d["code"] for d in pool}
    return n, False


def main() -> None:
    corpus = I.harvest()
    mem = json.loads(X.MEM_FILE.read_text())
    impls = []
    for tid in V.TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                continue
            impls.append((tid, h))

    def mines(tid, h):
        return {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}

    ctx, tot = {}, 0
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ch, ex = V.harvest_chains(corpus, tid, h), W.harvest_exprs(corpus, tid, h)
        ctx[(tid, h)] = (Sx, ch, ex)
        s0, _ = V.ev(tid, code)
        if s0 < 1.0:
            tot += len(X.gen(tid, code, Sx, ch, ex))
    for name, nskip in (("T1", 249), ("T1+T3+T4", 333)):
        p = nskip / tot
        nh = []
        for seed in range(SEEDS):
            rnd = random.Random(seed)
            h = 0
            for tid, hh in impls:
                Sx, ch, ex = ctx[(tid, hh)]
                _n, hit = run(tid, corpus[tid][hh], Sx, ch, ex, mines(tid, hh), p=p, rnd=rnd)
                h += hit
            nh.append(h)
        nh.sort()
        print(f"  NULL(p={p*100:.1f}%, {nskip} 条)：命中 min {nh[0]}｜p5 {nh[int(.05*SEEDS)]}"
              f"｜中位 {nh[SEEDS//2]}｜p95 {nh[int(.95*SEEDS)-1]}｜max {nh[-1]}"
              f"｜≥21 的种子 {sum(1 for x in nh if x >= 21)}/{SEEDS}")
    print("  对照：NO_T1 = 21、NO_134 = 21")


if __name__ == "__main__":
    main()
