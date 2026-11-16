#!/usr/bin/env python3
"""V146 前置量测 2（⛔ 不是判据）：注入点里哪些键**真的无效**？

「不是答案」不等于「有害」。要求两件事都量出来：
  A 该抽象键在本题每个实现上，**所有**对应候选的最高分（<1.0 才算无效）
  B 该键覆盖了多少个实现（= 一条错知识会被多少个实现看见）

只有 A 全 <1.0 且 B 足够大的键，才是可用的「错知识」。
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

CAND = {
    "g7e1_parse_flag": [("T4", "lower"), ("C1", "False"), ("T2", "'y'")],
    "g7d3_fmt_amount": [("T3", "strip"), ("T3", "strip().lower"), ("T3", "lower")],
    "g7d2_dedupe_keep": [("T4", "lower"), ("T4", "strip")],
    "g7e2_truncate_tail": [("C1", "s"), ("T3", "lower")],
}


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

    print("## 每个候选键：在本题各实现上的**最高分**（<1.0 才是真无效）")
    print("   （分母 = 该 tid 的可解析实现数；括号里是「最高分 <1.0 的实现数」）\n")
    verdict = {}
    for tid, keys in CAND.items():
        hs = [h for t, h in impls if t == tid]
        # 先为该 tid 的每个实现 gen 一次，按 abstract 键分桶
        bucket: dict[tuple, dict[str, list]] = {k: {h: [] for h in hs} for k in keys}
        for h in hs:
            Sx, _ = S.wordset(tid, corpus[tid][h], True)
            chains = V.harvest_chains(corpus, tid, h)
            exprs = W.harvest_exprs(corpus, tid, h)
            for lab, src in X.gen(tid, corpus[tid][h], Sx, chains, exprs):
                a = tuple(X.abstract(lab))
                if a in bucket:
                    bucket[a][h].append(src)
        print(f"  {tid}  ({len(hs)} 个实现)")
        for k in keys:
            best, dead = {}, 0
            for h in hs:
                srcs = bucket[k][h]
                if not srcs:
                    best[h] = None
                    continue
                s = max(V.ev(tid, s)[0] for s in srcs)
                best[h] = s
                if s < 1.0:
                    dead += 1
            seen = sum(1 for h in hs if best[h] is not None)
            mx = max([s for s in best.values() if s is not None], default=None)
            print(f"    {str(k):26s} 看见 {seen}/{len(hs)}｜最高分<1.0 {dead}/{seen}"
                  f"｜全实现最高分 max={mx}")
            verdict[f"{tid}::{k}"] = dict(seen=seen, dead=dead, n=len(hs), max=mx)
        print()

    print("## 结论：可用作「错知识」的键（看见 = 全部实现，且最高分全 <1.0）")
    ok = [k for k, v in verdict.items() if v["seen"] == v["n"] and v["dead"] == v["n"]]
    for k in ok:
        print(f"  ✅ {k}  n_impl={verdict[k]['n']}")
    if not ok:
        print("  ⛔ 没有 —— 说明「覆盖广」的键往往是能到 1.0 的，注入会变成正知识")
    (HERE / "key_effect.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
