#!/usr/bin/env python3
"""V151 跑前量测（零判分，除 ev 缓存外）：搜索实际走多深？round 上限抬到 40 还会不会有新东西？"""
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


def search(tid, h, code, Sx, chains, exprs, mine, rounds):
    s0, _ = V.ev(tid, code)
    frontier = [dict(code=code, s=s0)]
    visited = {code}; n_eval = 0; events = []
    last_pool_round = 0
    for rnd in range(1, rounds + 1):
        pool, seen_src = [], set()
        for node in frontier:
            for lab, src in X.gen(tid, node["code"], Sx, chains, exprs):
                k = X.abstract(lab)
                if src in visited or src in seen_src: continue
                s, _ = V.ev(tid, src); n_eval += 1
                events.append((k, s, rnd, src))
                if s > node["s"]:
                    pool.append(dict(code=src, s=s)); seen_src.add(src)
        if not pool: break
        last_pool_round = rnd
        pool.sort(key=lambda d: -d["s"]); frontier = pool[:2]
        visited |= {d["code"] for d in pool}
    return dict(s0=s0, n_eval=n_eval, events=events, depth=last_pool_round)


V.CACHE.clear()
print("| 实现 | s0 | 深度(pool 非空的最大轮) | R6 ev | R40 ev | R6 键 | R40 键 | 分数阶梯 distinct |")
print("|---|---|---|---|---|---|---|---|")
t0 = time.time(); tot = collections.Counter()
for tid, h in impls:
    code = corpus[tid][h]
    mine = {(a, b) for k in mem[tid] if k["h"] != h for a, b in [tuple(k["act"])]}
    Sx, _ = S.wordset(tid, code, True)
    ch = V.harvest_chains(corpus, tid, h); ex = W.harvest_exprs(corpus, tid, h)
    r6 = search(tid, h, code, Sx, ch, ex, mine, 6)
    r40 = search(tid, h, code, Sx, ch, ex, mine, 40)
    k6 = {e[0] for e in r6["events"]}; k40 = {e[0] for e in r40["events"]}
    ladder = sorted({e[1] for e in r40["events"]})
    tot["new"] += len(k40 - k6); tot["ev40"] += r40["n_eval"]; tot["ev6"] += r6["n_eval"]
    tot["depth_lt6"] += (r40["depth"] < 6)
    print(f"| {tid[:12]}::{h} | {r6['s0']:.2f} | {r40['depth']} | {r6['n_eval']} | {r40['n_eval']} | "
          f"{len(k6)} | {len(k40)} | {len(ladder)} |")
print(f"\nR6 ev={tot['ev6']}｜R40 ev={tot['ev40']}｜新增键={tot['new']}｜深度<6 的实现={tot['depth_lt6']}/25")
print(f"耗时 {time.time()-t0:.1f}s")
