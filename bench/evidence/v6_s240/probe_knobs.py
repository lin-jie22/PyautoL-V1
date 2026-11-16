#!/usr/bin/env python3
"""V151 跑前量测（二）：既然 MAX_ROUNDS 从来不是约束，那**哪个旋钮真的动**？
试 frontier 宽度 k（部署是 2）。"""
import ast, collections, json, pathlib, sys, time
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


def search(tid, h, code, Sx, chains, exprs, mine, rounds, k):
    s0, _ = V.ev(tid, code)
    frontier = [dict(code=code, s=s0)]
    visited = {code}; n_eval = 0; events = []; depth = 0
    for rnd in range(1, rounds + 1):
        pool, seen_src = [], set()
        for node in frontier:
            for lab, src in X.gen(tid, node["code"], Sx, chains, exprs):
                kk = X.abstract(lab)
                if src in visited or src in seen_src: continue
                s, _ = V.ev(tid, src); n_eval += 1
                events.append((kk, s, rnd, src))
                if s > node["s"]:
                    pool.append(dict(code=src, s=s)); seen_src.add(src)
        if not pool: break
        depth = rnd
        pool.sort(key=lambda d: -d["s"]); frontier = pool[:k]
        visited |= {d["code"] for d in pool}
    return dict(s0=s0, n_eval=n_eval, events=events, depth=depth)


V.CACHE.clear()
print("| 实现 | 键(k=2,R6) | 键(k=8,R6) | 键(k=99,R40) | ev k2 | ev k8 | ev k99 | 深度 k99 | s1 k2 | s1 k99 |")
print("|---|---|---|---|---|---|---|---|---|---|")
t0 = time.time(); tot = collections.Counter()
for tid, h in impls:
    code = corpus[tid][h]
    mine = {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}
    Sx, _ = S.wordset(tid, code, True)
    ch = V.harvest_chains(corpus, tid, h); ex = W.harvest_exprs(corpus, tid, h)
    a = search(tid, h, code, Sx, ch, ex, mine, 6, 2)
    b = search(tid, h, code, Sx, ch, ex, mine, 6, 8)
    c = search(tid, h, code, Sx, ch, ex, mine, 40, 99)
    ka, kb, kc = ({e[0] for e in r["events"]} for r in (a, b, c))
    tot["new_k8"] += len(kb - ka); tot["new_k99"] += len(kc - ka)
    tot["ev"] += c["n_eval"]
    best = lambda r: max([r["s0"]] + [e[1] for e in r["events"]])
    print(f"| {tid[:12]}::{h} | {len(ka)} | {len(kb)} | {len(kc)} | {a['n_eval']} | {b['n_eval']} | "
          f"{c['n_eval']} | {c['depth']} | {best(a):.2f} | {best(c):.2f} |")
    if tid.endswith("parse_flag") and h in ("a579b07a",):
        break
print(f"\nk=8 新增键 {tot['new_k8']}｜k=99,R40 新增键 {tot['new_k99']}｜k99 判分合计 {tot['ev']}｜"
      f"耗时 {time.time()-t0:.1f}s")
