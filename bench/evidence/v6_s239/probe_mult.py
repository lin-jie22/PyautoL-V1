#!/usr/bin/env python3
"""V150 跑前量测（零判分）：一个抽象键在生成闭包里对应几个不同源码？"""
import ast, collections, pathlib, sys
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

def closure(tid, code, Sx, chains, exprs, depth):
    seen = {code}; frontier = [code]; per_key = collections.defaultdict(set)
    for _ in range(depth):
        nxt = []
        for c in frontier:
            for lab, src in X.gen(tid, c, Sx, chains, exprs):
                per_key[X.abstract(lab)].add(src)
                if src not in seen:
                    seen.add(src); nxt.append(src)
        frontier = nxt
    return per_key, len(seen)

print("| 实现 | d1 源码 | d1 键 | 多实例键(≥2) | 最多实例 | d2 源码 | d2 新增键 | d2 已有键的新实例 |")
print("|---|---|---|---|---|---|---|---|")
tot_impl_multi = 0; tot_cells = 0; tot_extra = 0; d2_key_new = 0; d2_inst_new = 0
import time
t0 = time.time()
for tid, h in impls:
    code = corpus[tid][h]
    Sx, _ = S.wordset(tid, code, True)
    ch = V.harvest_chains(corpus, tid, h); ex = W.harvest_exprs(corpus, tid, h)
    k1, n1 = closure(tid, code, Sx, ch, ex, 1)
    k2, n2 = closure(tid, code, Sx, ch, ex, 2)
    multi = sum(1 for s in k1.values() if len(s) >= 2)
    tot_cells += len(k1); tot_impl_multi += multi
    tot_extra += sum(len(s) - 1 for s in k1.values())
    newk = len(set(k2) - set(k1))
    inst_new = sum(len(k2[k] - k1.get(k, set())) for k in k2)
    d2_key_new += newk; d2_inst_new += inst_new
    print(f"| {tid[:12]}::{h} | {n1} | {len(k1)} | {multi} | {max(len(s) for s in k1.values())} "
          f"| {n2} | {newk} | {inst_new} |")
print(f"\n合计：键-格 {tot_cells} 个，其中多实例键 {tot_impl_multi}（{tot_impl_multi/tot_cells:.1%}），"
      f"冗余实例 {tot_extra} 个")
print(f"d2 新增键 {d2_key_new}｜d2 给已有键新增实例 {d2_inst_new}")
print(f"耗时 {time.time()-t0:.1f}s")
