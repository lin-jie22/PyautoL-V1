#!/usr/bin/env python3
"""V148 跑前量测：键集是否随深度单调（在 4 个实现上试）。"""
import ast, pathlib, sys, time
HERE=pathlib.Path(__file__).resolve().parent; EV=HERE.parent
for _d in ("v6_s205","v6_s208","v6_s209","v6_s198"): sys.path.insert(0,str(EV/_d))
sys.path.insert(0,str(EV/"v6_s210"))
import idiom_transfer as I, search as S, v119 as V, v120 as W, v121 as X
corpus=I.harvest()
def keys_at(tid,code,Sx,chains,exprs,depth,seen=None):
    out=set(); frontier=[code]; seen=set([code])
    for _ in range(depth):
        nxt=[]
        for c in frontier:
            for lab,src in X.gen(tid,c,Sx,chains,exprs):
                out.add(X.abstract(lab))
                if src not in seen:
                    seen.add(src); nxt.append(src)
        frontier=nxt
    return out,len(seen)
for tid,h in [("g7d2_dedupe_keep","1ac9e8da"),("g7e1_parse_flag","644f0618"),
              ("g7d3_fmt_amount","309f1027"),("g7e2_truncate_tail","c21ee3b8")]:
    code=corpus[tid][h]
    Sx,_=S.wordset(tid,code,True); ch=V.harvest_chains(corpus,tid,h); ex=W.harvest_exprs(corpus,tid,h)
    t0=time.time(); k1,n1=keys_at(tid,code,Sx,ch,ex,1)
    t1=time.time(); k2,n2=keys_at(tid,code,Sx,ch,ex,2)
    t2=time.time()
    print(f"{tid[:6]} {h}: d1 keys={len(k1)} nodes={n1} ({t1-t0:.1f}s)｜d2 keys={len(k2)} nodes={n2} ({t2-t1:.1f}s)"
          f"｜k2⊆k1? {k2<=k1}｜新增={sorted(k2-k1)[:4]}")
