#!/usr/bin/env python3
"""V157 修正：部署版已在 1.0 早停 ⇒ 只剩「命中前花在永不中的键上」这部分可省。
零新判分口径：重放（确定性），只数事件。"""
import ast, collections, json, pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent; EV = HERE.parent
for d in ("v6_s205","v6_s208","v6_s209","v6_s198","v6_s210","v6_s236"):
    sys.path.insert(0, str(EV/d))
import idiom_transfer as I, search as S, v119 as V, v120 as W, v121 as X

def search_early(tid, code, Sx, chains, exprs, mine, rounds=6, k=2):
    s0,_ = V.ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, n_eval=0, hit=True, events=[], stop=0)
    frontier=[dict(code=code,s=s0)]; visited={code}; n=0; ev=[]
    for rnd in range(1, rounds+1):
        pool, seen = [], set()
        for node in frontier:
            cands=[(X.abstract(lab),lab,src) for lab,src in X.gen(tid,node["code"],Sx,chains,exprs)]
            for key,_l,src in sorted(cands,key=lambda x:(0 if x[0] in mine else 1,)):
                if src in visited or src in seen: continue
                s,_=V.ev(tid,src); n+=1; ev.append((key,s,src,s>node["s"]))
                if s>=1.0:
                    return dict(s0=s0,n_eval=n,hit=True,events=ev,stop=rnd)
                if s>node["s"]:
                    pool.append(dict(code=src,s=s)); seen.add(src)
        if not pool: break
        pool.sort(key=lambda d:-d["s"]); frontier=pool[:k]; visited|={d["code"] for d in pool}
    return dict(s0=s0,n_eval=n,hit=False,events=ev,stop=-1)

corpus=I.harvest(); impls=[]
for tid in V.TIDS:
    for h in sorted(corpus[tid]):
        try: ast.parse(corpus[tid][h])
        except SyntaxError: continue
        impls.append((tid,h))
mem=json.loads(X.MEM_FILE.read_text())
V.CACHE.clear()
out={}
for tid,h in impls:
    mine={(a,b) for kk in mem[tid] if kk["h"]!=h for a,b in [tuple(kk["act"])]}
    Sx,_=S.wordset(tid,corpus[tid][h],True)
    out[(tid,h)]=search_early(tid,corpus[tid][h],Sx,V.harvest_chains(corpus,tid,h),
                              W.harvest_exprs(corpus,tid,h),mine)
tot=sum(r["n_eval"] for r in out.values()); hit=sum(1 for r in out.values() if r["hit"])
print(f"## 部署口径（早停开）自校验：n_eval = {tot}｜到 1.0 = {hit}/25（V121 WARM 记 588/21）")
# 每实现：命中前的判分里，有多少花在「该实现上永不中的键」
waste=0; per=[]
for im,r in out.items():
    ks=collections.Counter(k for k,_s,_src,_i in r["events"])
    if r["hit"]:
        # 该实现哪些键在本实现（早停前）从未命中 1.0 ⇒ 属于「花了但没中」
        hitkeys={k for k,s,_,_ in r["events"] if s>=1.0}
        w=sum(1 for k,s,_,_ in r["events"] if k not in hitkeys)
        waste+=w; per.append((im,r["n_eval"],w))
noimp=sum(1 for r in out.values() for e in r["events"] if not e[3] and e[1] < 1.0)
print(f"  命中前判分 {tot}｜花在「本次没中的键」上 = {waste}（{waste/tot*100:.1f}%）"
      f"⚠️ 松上界（含踏脚石）")
print(f"  ⭐ 严格口径：判了但**没改进**且没到 1.0 = {noimp}（{noimp/tot*100:.1f}%）")
print(f"     其中「没改进且该键本次没中」= "
      f"{sum(1 for r in out.values() for e in r['events'] if not e[3] and e[1] < 1.0 and e[0] not in {k for k,s,_,_ in r['events'] if s>=1.0})}")
print(f"  ⇒ 「跳过无用键」的剩余天花板 = {waste}/{tot} = {waste/tot*100:.1f}%")
print(f"  对照：早停本身已经省掉的 = 2710 − {tot} = {2710-tot}（{(2710-tot)/2710*100:.1f}%）")
for im,n,w in sorted(per,key=lambda x:-x[2])[:8]:
    print(f"    {im[0][:10]}::{im[1]} 判 {n}｜其中没中的 {w}")
json.dump(dict(tot=tot,hit=hit,waste=waste,noimp=noimp,
               impls={f"{t}::{h}":dict(n_eval=r["n_eval"],hit=r["hit"],
                       events=[[list(k),s,bool(im)] for k,s,_src,im in r["events"]])
                       for (t,h),r in out.items()}),
          open(HERE/"readings_remain.json","w"),ensure_ascii=False,indent=1)
