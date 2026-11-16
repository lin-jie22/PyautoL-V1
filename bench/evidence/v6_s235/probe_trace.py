#!/usr/bin/env python3
"""V146 量测 3：跑一次冷启动，把**每个候选的抽象键与它的分数**记下来。

为什么要这一份轨迹：`search.json` 只存了每个实现的汇总（s1 / n_eval / 生效标签），
没有存「哪些候选被判了多少分」。而本轮要问的是：

    一条记忆（抽象键）被写下来之后，它指向的候选在**别的实现**上还成不成立？

⛔ 这不是新尺子：判分用的还是 `v119.ev`，候选还是 `v121.gen`，唯一多的是**记录**。
   与 V131 同一形态（旁观者，不判分额外成本）。

落盘 `trace.json`，之后的分析全部离线、零判分。
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for _d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198"):
    sys.path.insert(0, str(EV / _d))
sys.path.insert(0, str(EV / "v6_s210"))

import idiom_transfer as I     # noqa: E402
import search as S             # noqa: E402
import v119 as V               # noqa: E402
import v120 as W               # noqa: E402
import v121 as X               # noqa: E402

MAX_ROUNDS = X.MAX_ROUNDS
TRACE: list[dict] = []


def search_traced(tid, h, code, Sx, chains, exprs) -> dict:
    """与 `v121.search` 逐行同构，只多记一条 TRACE（⛔ 不改任何分支）。"""
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, s1=s0, n_eval=0, lab="<baseline>", hit=True)
    frontier = [dict(code=code, s=s0)]
    visited = {code}
    n_eval = 0
    rnd = 0
    for rnd in range(1, MAX_ROUNDS + 1):
        pool, seen_src = [], set()
        for node in frontier:
            cands = X.gen(tid, code=node["code"], Sx=Sx, chains=chains, exprs=exprs)
            cands = sorted(cands, key=lambda x: 0)
            for lab, src in cands:
                if src in visited or src in seen_src:
                    continue
                s, _ = V.ev(tid, src)
                n_eval += 1
                TRACE.append(dict(tid=tid, h=h, rnd=rnd, lab=lab,
                                  key=list(X.abstract(lab)), score=s))
                if s >= 1.0:
                    return dict(s0=s0, s1=s, n_eval=n_eval, lab=lab, hit=True)
                if s > node["s"]:
                    pool.append(dict(code=src, s=s))
                    seen_src.add(src)
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:2]
        visited |= {d["code"] for d in pool}
    best = max(frontier, key=lambda d: d["s"])
    return dict(s0=s0, s1=best["s"], n_eval=n_eval, lab="<none>", hit=False)


def main() -> None:
    corpus = I.harvest()
    impls = []
    for tid in V.TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                continue
            impls.append((tid, h))

    t0 = time.time()
    res = {}
    for i, (tid, h) in enumerate(impls, 1):
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        chains = V.harvest_chains(corpus, tid, h)
        exprs = W.harvest_exprs(corpus, tid, h)
        r = search_traced(tid, h, corpus[tid][h], Sx, chains, exprs)
        res[f"{tid}::{h}"] = r
        if i % 5 == 0 or i == len(impls):
            print(f"  [{i}/{len(impls)}] {time.time() - t0:6.1f}s  "
                  f"n_eval 累计 {sum(v['n_eval'] for v in res.values())}")

    tot = sum(v["n_eval"] for v in res.values())
    to1 = sum(1 for v in res.values() if v["s1"] >= 1.0)
    print(f"\n## 自校验（必须与 V121 记录一致）")
    print(f"  n_impl = {len(res)}｜n_eval 总 = {tot}（V121 记 1381）")
    print(f"  到 1.0 = {to1}/{len(res)}（V121 记 21）")
    print(f"  轨迹条数 = {len(TRACE)}｜墙钟 {time.time() - t0:.1f}s")

    (HERE / "trace.json").write_text(json.dumps(dict(
        search=res, trace=TRACE), ensure_ascii=False), encoding="utf-8")
    print(f"  已落盘 trace.json（{len(json.dumps(TRACE))} 字节）")


if __name__ == "__main__":
    main()
