#!/usr/bin/env python3
"""V151 跑前量测（三）：**满覆盖**要多少钱？d=1/d=2 的不同源码数（去重后）。"""
import ast, collections, pathlib, sys, time
HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for _d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198", "v6_s210", "v6_s236"):
    sys.path.insert(0, str(EV / _d))
import idiom_transfer as I, search as S, v119 as V, v120 as W, v121 as X

corpus = I.harvest()
impls = []
for tid in V.TIDS:
    for h in sorted(corpus[tid]):
        try: ast.parse(corpus[tid][h])
        except SyntaxError: continue
        impls.append((tid, h))

def closure(tid, code, Sx, ch, ex, depth):
    frontier = {code}; seen = {code}; per = collections.defaultdict(set)
    for _ in range(depth):
        nxt = set()
        for c in frontier:
            for lab, src in X.gen(tid, c, Sx, ch, ex):
                per[X.abstract(lab)].add(src)
                if src not in seen:
                    seen.add(src); nxt.add(src)
        frontier = nxt
        print(f"    depth step: frontier={len(frontier)} seen={len(seen)}")
    return per, len(seen)

for tid, h in [("g7e1_parse_flag", "a579b07a"), ("g7d3_fmt_amount", "075b4099"),
               ("g7e2_truncate_tail", "8184ec4d")]:
    code = corpus[tid][h]
    Sx, _ = S.wordset(tid, code, True)
    ch = V.harvest_chains(corpus, tid, h); ex = W.harvest_exprs(corpus, tid, h)
    t0 = time.time()
    print(f"{tid}::{h}")
    per, n = closure(tid, code, Sx, ch, ex, 2)
    print(f"  distinct srcs={n} 键={len(per)} 实例对={sum(len(s) for s in per.values())} 耗时={time.time()-t0:.2f}s")
