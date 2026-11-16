#!/usr/bin/env python3
"""V151 跑前量测（四）：满覆盖 d=1/d=2 的**去重后**判分预算（零判分，不算任何分数）。"""
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
    return per, seen

t0 = time.time()
by_tid = collections.defaultdict(set)
n_pairs_d1 = n_pairs_d2 = 0
for tid, h in impls:
    code = corpus[tid][h]
    Sx, _ = S.wordset(tid, code, True)
    ch = V.harvest_chains(corpus, tid, h); ex = W.harvest_exprs(corpus, tid, h)
    p1, s1 = closure(tid, code, Sx, ch, ex, 1)
    p2, s2 = closure(tid, code, Sx, ch, ex, 2)
    by_tid[tid] |= s2
    n_pairs_d1 += sum(len(v) for v in p1.values())
    n_pairs_d2 += sum(len(v) for v in p2.values())
    print(f"  {tid[:14]:14s}::{h}  srcs d1={len(s1):5d} d2={len(s2):6d}  实例对 d1={sum(len(v) for v in p1.values()):6d} d2={sum(len(v) for v in p2.values()):7d}")
tot_unique = sum(len(v) for v in by_tid.values())
print(f"\n按 tid 去重后的待判源码总数 = {tot_unique}（CACHE 键是 (tid, code)）")
print(f"实例对（键×源码）d1={n_pairs_d1} d2={n_pairs_d2}｜建闭包耗时 {time.time()-t0:.1f}s")
