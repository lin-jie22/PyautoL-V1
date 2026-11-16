#!/usr/bin/env python3
"""V152 附带核算（纪律 64 同型）：「闭包里构造过的源码」≠「真的判过的源码」。

V151 的读数里有一句 `按 (tid, code) 去重后（CACHE 实际判分次数）39072`。
本轮跑下来总耗时 13.3s，而 V151 自己量过 618 ev/s ⇒ 39072 次要 63s。
⇒ 两者不可能同时成立。这个脚本把两个分母分开数：
  ① 闭包里出现过多少个不同源码（构造量）
  ② 其中被 `v119.ev` 真判过多少个（判分量）
⛔ 不新造分数、不改任何东西，只数。
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for _d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198", "v6_s210", "v6_s236", "v6_s240"):
    sys.path.insert(0, str(EV / _d))

import idiom_transfer as I     # noqa: E402
import search as S             # noqa: E402
import v119 as V               # noqa: E402
import v120 as W               # noqa: E402
import v121 as X               # noqa: E402
import v151 as P               # noqa: E402


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
    mem = json.loads(X.MEM_FILE.read_text(encoding="utf-8"))
    uni = {(t, h, tuple(kk["act"])) for t, h in impls for kk in mem[t] if kk["h"] != h}
    uni_real = sorted(c for c in uni if c[2] != ("?", "<baseline>"))

    V.CACHE.clear()
    clo = {}
    per_impl_keys = 0
    pairs_all = 0
    by_tid = {}
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ch = V.harvest_chains(corpus, tid, h)
        ex = W.harvest_exprs(corpus, tid, h)
        per, seen = P.closure2(tid, code, Sx, ch, ex)
        clo[(tid, h)] = per
        per_impl_keys += len(per)
        pairs_all += sum(len(v) for v in per.values())
        by_tid.setdefault(tid, set()).update(seen)
    constructed = sum(len(v) for v in by_tid.values())

    calls = 0
    for tid, h, key in uni_real:
        for s in clo[(tid, h)].get(key, set()):
            V.ev(tid, s)
            calls += 1
    print("# 「构造」与「判过」两个分母（V151/V152 的 C_FULL 臂）")
    print(f"  ① 闭包里出现过的不同源码（按题去重）= **{constructed}**（V151 记 39072）")
    print(f"  ② 35 个真实格的实例对总数 = **{calls}**（V151 记 n_inst_c = 3142）")
    print(f"  ③ 其中**真正调用了** `ev` 的不同 `(tid, code)` = **{len(V.CACHE)}**")
    print(f"  ④ 全部键×源码实例对（含 35 格以外的键）= {pairs_all}（V151 记 n_pairs_c = 80737）"
          f"｜实现-键对 = {per_impl_keys}")
    print(f"  ⇒ 「覆盖 3142 个实例」成立；「判了 39072 次」**从未发生** "
          f"（39072 是构造量，不是判分量）。按 618 ev/s，39072 次要 63s，实测整个脚本 13.3s。")
    (HERE / "readings_counts.json").write_text(json.dumps(dict(
        constructed=constructed, cell_pairs=calls, ev_calls=len(V.CACHE),
        pairs_all=pairs_all, per_impl_keys=per_impl_keys), indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
