#!/usr/bin/env python3
"""V151 跑前量测：把 MAX_ROUNDS 抬高，成本与格数怎么变（先只跑 6 个实现）。

零交付改动：只在本脚本里改 round 上限（v121.MAX_ROUNDS 是模块常量，子进程/局部变量绕开）。
"""
import ast, collections, json, pathlib, sys, time
HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for _d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198", "v6_s210", "v6_s236"):
    sys.path.insert(0, str(EV / _d))
import idiom_transfer as I, search as S, v119 as V, v120 as W, v121 as X

kept = [(a, b) for a, b in __import__("itertools").product([], [])]
corpus = I.harvest()
impls = []
for tid in V.TIDS:
    for h in sorted(corpus[tid]):
        try: ast.parse(corpus[tid][h])
        except SyntaxError: continue
        impls.append((tid, h))
mem = json.loads(X.MEM_FILE.read_text(encoding="utf-8"))


def search(tid, h, code, Sx, chains, exprs, mine, rounds, stop=False):
    s0, _ = V.ev(tid, code)
    frontier = [dict(code=code, s=s0)]
    visited = {code}; n_eval = 0; best = s0; events = []
    for rnd in range(1, rounds + 1):
        pool, seen_src = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, src) for lab, src in X.gen(tid, node["code"], Sx, chains, exprs)]
            for k, _l, src in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                if src in visited or src in seen_src: continue
                s, _ = V.ev(tid, src); n_eval += 1
                events.append((tuple(k), s, rnd))
                if s > best: best = s
                if s >= 1.0 and stop: return dict(s0=s0, n_eval=n_eval, best=best, events=events)
                if s > node["s"]:
                    pool.append(dict(code=src, s=s)); seen_src.add(src)
        if not pool: break
        pool.sort(key=lambda d: -d["s"]); frontier = pool[:2]
        visited |= {d["code"] for d in pool}
    return dict(s0=s0, n_eval=n_eval, best=best, events=events)


V.CACHE.clear()
print("| 实现 | s0 | R6 ev | R8 ev | R12 ev | R20 ev | R6 best | R20 best | R6 格 | R20 格 | R20 最后新格轮次 |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
tot = collections.Counter()
t0 = time.time()
for tid, h in impls[:6]:
    code = corpus[tid][h]
    mine = {(a, b) for k in mem[tid] if k["h"] != h for a, b in [tuple(k["act"])]}
    Sx, _ = S.wordset(tid, code, True)
    ch = V.harvest_chains(corpus, tid, h); ex = W.harvest_exprs(corpus, tid, h)
    r = {n: search(tid, h, code, Sx, ch, ex, mine, n) for n in (6, 8, 12, 20)}
    k6 = {e[0] for e in r[6]["events"]}; k20 = {e[0] for e in r[20]["events"]}
    new_rounds = [e[2] for e in r[20]["events"] if e[0] not in k6]
    tot["new_cells"] += len(k20 - k6)
    print(f"| {tid[:12]}::{h} | {r[6]['s0']:.2f} | {r[6]['n_eval']} | {r[8]['n_eval']} | "
          f"{r[12]['n_eval']} | {r[20]['n_eval']} | {r[6]['best']:.2f} | {r[20]['best']:.2f} | "
          f"{len(k6)} | {len(k20)} | {max(new_rounds) if new_rounds else '-'} |")
print(f"\n新增键 {tot['new_cells']}｜耗时 {time.time()-t0:.1f}s（前 6 个实现）")
