#!/usr/bin/env python3
"""V148 跑前量测：有界枚举的成本与规模（零判分）。"""
import ast, pathlib, sys, time
HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for _d in ("v6_s205","v6_s208","v6_s209","v6_s198"):
    sys.path.insert(0, str(EV/_d))
sys.path.insert(0, str(EV/"v6_s210"))
import idiom_transfer as I, search as S, v119 as V, v120 as W, v121 as X

corpus = I.harvest()
impls=[]
for tid in V.TIDS:
    for h in sorted(corpus[tid]):
        try: ast.parse(corpus[tid][h])
        except SyntaxError: continue
        impls.append((tid,h))

for tid,h in impls[:6]:
    code=corpus[tid][h]
    Sx,_=S.wordset(tid,code,True); chains=V.harvest_chains(corpus,tid,h); exprs=W.harvest_exprs(corpus,tid,h)
    t0=time.time(); c1=X.gen(tid,code,Sx,chains,exprs); t1=time.time()
    k1={X.abstract(lab) for lab,_ in c1}
    print(f"{tid[:6]} {h}  depth1={len(c1):4d} keys={len(k1):3d}  {t1-t0:.2f}s"
          f"  chains={len(chains)} exprs={len(exprs)}")
