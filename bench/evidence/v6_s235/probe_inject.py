#!/usr/bin/env python3
"""V146 前置量测（⛔ 不是判据）：本题候选里有没有「可匹配但错」的键？

V121 的错记忆臂读数是 `n_eval 1381 == 1381`（一次没省），DESIGN 记作「扰动太弱」。
本探针把它量成一句话：

    注入的键在本题生成的候选里**出现过几次**。0 次 ⇒ 没被用上 ⇒ 不存在「用错了」这回事。

输出两件事（零判分，只 gen）：
  Q1 每个 tid 的可匹配非答案键有多少个
  Q2 其中能在 ≥3 个实现上被匹配到的有几个（= 一条错知识能真实影响几个实现）
"""
from __future__ import annotations

import ast
import collections
import json
import pathlib
import sys

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


def main() -> None:
    corpus = I.harvest()
    tids = V.TIDS
    impls = []
    for tid in tids:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                continue
            impls.append((tid, h))
    mem = json.loads(X.MEM_FILE.read_text(encoding="utf-8"))

    keys: dict[str, dict[str, list]] = {t: {} for t in tids}
    for tid, h in impls:
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        chains = V.harvest_chains(corpus, tid, h)
        exprs = W.harvest_exprs(corpus, tid, h)
        got = []
        for lab, _src in X.gen(tid, corpus[tid][h], Sx, chains, exprs):
            got.append(X.abstract(lab))
        keys[tid][h] = got

    print("## Q0 分母")
    print(f"  tids = {len(tids)}｜parseable impls = {len(impls)}")

    print("\n## Q1/Q2 每个 tid：可匹配的非答案键")
    print(f"  {'tid':22s} {'实现':>4s} {'不同键':>7s} {'答案键':>7s} "
          f"{'非答案键':>9s} {'≥3实现':>8s}  {'覆盖率最高的键'}")
    for tid in tids:
        ans = {tuple(k["act"]) for k in mem[tid] if tuple(k["act"]) != ("?", "<baseline>")}
        cov: collections.Counter = collections.Counter()
        allk = set()
        for h in keys[tid]:
            s = set(keys[tid][h])
            allk |= s
            for k in s:
                cov[k] += 1
        nonans = allk - ans
        top = [(k, cov[k]) for k in nonans if cov[k] >= 3]
        top.sort(key=lambda x: -x[1])
        best = f"{top[0][1]}/{len(keys[tid])} 实现 {top[0][0]!r}" if top else "—"
        print(f"  {tid:22s} {len(keys[tid]):4d} {len(allk):7d} {len(ans):7d} "
              f"{len(nonans):9d} {len(top):8d}  {best}")

    print("\n## Q3 V121 错记忆臂为什么「一次没省」——直接数出来")
    tot_cross = 0
    for tid in tids:
        bad = set()
        for t2 in tids:
            if t2 == tid:
                continue
            for k in mem[t2]:
                bad.add(tuple(k["act"]))
        hit = sum(1 for h in keys[tid] if bad & set(keys[tid][h]))
        tot_cross += hit
        print(f"  {tid:22s} 别题键 {len(bad):3d} 个｜本题能被它们匹配到的实现 {hit}/{len(keys[tid])}")
    print(f"  ⇒ 合计 {tot_cross} 个实现「被别题键排序影响」")

    print("\n## Q4 判据可承载的注入点（按覆盖实现数排序，取前 3/tid）")
    out = {}
    for tid in tids:
        ans = {tuple(k["act"]) for k in mem[tid] if tuple(k["act"]) != ("?", "<baseline>")}
        cov: collections.Counter = collections.Counter()
        for h in keys[tid]:
            for k in set(keys[tid][h]):
                cov[k] += 1
        cand = sorted(((k, c) for k, c in cov.items() if c >= 3 and k not in ans),
                      key=lambda x: -x[1])[:3]
        out[tid] = [{"key": list(k), "n_impl": c} for k, c in cand]
        for k, c in cand:
            print(f"  {tid:22s} n_impl={c:2d} key={k!r}")
    (HERE / "inject_points.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  注入点已落盘 `inject_points.json`")


if __name__ == "__main__":
    main()
