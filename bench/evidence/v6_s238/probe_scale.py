#!/usr/bin/env python3
"""V149 跑前量测（零判分）：去掉 s0>=1.0 短路后，10 个基线实现要判多少次？"""
import ast, json, pathlib, sys
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
mem = json.loads(X.MEM_FILE.read_text(encoding="utf-8"))
BASELINE = ("?", "<baseline>")

tot_base = tot_act = 0
n_base = 0
rows = []
for tid, h in impls:
    code = corpus[tid][h]
    s0, _ = V.ev(tid, code)
    Sx, _ = S.wordset(tid, code, True)
    ch = V.harvest_chains(corpus, tid, h)
    ex = W.harvest_exprs(corpus, tid, h)
    cands = X.gen(tid, code, Sx, ch, ex)
    # 搜索器只判 visited/seen_src 之外的源码 ⇒ 先按出现顺序去重（与 search 同口径）
    uniq, seen = 0, set()
    for lab, src in cands:
        if src in seen or src == code: continue
        seen.add(src); uniq += 1
    if s0 >= 1.0:
        n_base += 1; tot_base += uniq
        rows.append((tid, h, s0, len(cands), uniq, True))
    else:
        tot_act += uniq
        rows.append((tid, h, s0, len(cands), uniq, False))
for tid, h, s0, nraw, nuq, isb in rows:
    tag = "BASE" if isb else "act "
    print(f"  {tag} {tid[:22]:22s} {h} s0={s0:.2f} gen={nraw:4d} 去重后可判={nuq:4d}")
print(f"\n基线实现 = {n_base}/{len(impls)}｜它们的 d=1 可判候选合计 = {tot_base}")
print(f"活跃实现的 d=1 可判候选合计 = {tot_act}（对比：STOP 实际判 588、NOSTOP 2301）")
