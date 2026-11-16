#!/usr/bin/env python3
"""V146 分析：一条记忆条目「能匹配到多大范围」vs「在这个范围里真的有用」。

定义（口径写进字段名，纪律 46）：
  spread(k)  = 轨迹里出现过键 k 的**实现数**（可匹配面）
  useful(k)  = 其中**至少有一个候选拿到 1.0** 的实现数（有效面）
  ratio      = useful / spread

⛔ 不用分数、不用 test、不用人写的规则 —— 只用系统自己跑出来的判分记录。
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
sys.path.insert(0, str(EV / "v6_s210"))

import v121 as X   # noqa: E402


def main() -> None:
    d = json.loads((HERE / "trace.json").read_text(encoding="utf-8"))
    trace = d["trace"]
    mem = json.loads(X.MEM_FILE.read_text(encoding="utf-8"))

    # (tid, h) -> set(keys) / dict(key -> max score)
    seen: dict[tuple, set] = collections.defaultdict(set)
    best: dict[tuple, dict[tuple, float]] = collections.defaultdict(dict)
    for r in trace:
        k = tuple(r["key"])
        seen[(r["tid"], r["h"])].add(k)
        b = best[(r["tid"], r["h"])]
        if k not in b or r["score"] > b[k]:
            b[k] = r["score"]

    impls_by_tid = collections.defaultdict(list)
    for tid, h in seen:
        impls_by_tid[tid].append(h)

    print("## 记忆条目：可匹配面 vs 有效面（轨迹口径 = 冷启动实际判过的候选）\n")
    print(f"  {'tid':22s} {'源':9s} {'键':26s} {'spread':>7s} {'useful':>7s} {'ratio':>6s}")
    rows = []
    for tid, items in mem.items():
        hs = sorted(impls_by_tid[tid])
        for it in items:
            k = tuple(it["act"])
            sp = [h for h in hs if k in seen[(tid, h)]]
            us = [h for h in sp if best[(tid, h)].get(k, 0.0) >= 1.0]
            rows.append(dict(tid=tid, src=it["h"], key=list(k),
                             spread=len(sp), useful=len(us),
                             n_impl=len(hs),
                             ratio=(len(us) / len(sp)) if sp else None))
    rows.sort(key=lambda r: (-(r["spread"]), r["ratio"] if r["ratio"] is not None else 9))
    for r in rows:
        ratio = "—" if r["ratio"] is None else f"{r['ratio']:.2f}"
        print(f"  {r['tid']:22s} {r['src']:9s} {str(tuple(r['key'])):26s} "
              f"{r['spread']:7d} {r['useful']:7d} {ratio:>6s}")

    print("\n## 汇总")
    nb = [r for r in rows if tuple(r["key"]) == ("?", "<baseline>")]
    real = [r for r in rows if tuple(r["key"]) != ("?", "<baseline>")]
    print(f"  条目总数 = {len(rows)}｜`('?','<baseline>')` = {len(nb)}｜其余 = {len(real)}")
    print(f"  spread == 0（一条也匹配不上）= {sum(1 for r in rows if r['spread'] == 0)}")
    part = [r for r in real if r["spread"] and r["useful"] < r["spread"]]
    print(f"  ⭐ 可匹配但**不是每个实现上都有效**的条目 = {len(part)}/{len(real)}")
    print(f"     其中 useful == 0（完全无效）= {sum(1 for r in real if r['spread'] and r['useful'] == 0)}")
    print(f"     useful == spread（全有效）= {sum(1 for r in real if r['spread'] and r['useful'] == r['spread'])}")

    print("\n## 每条「部分有效」条目：无效面在哪几个实现上")
    for r in part:
        tid, k = r["tid"], tuple(r["key"])
        hs = sorted(impls_by_tid[tid])
        sp = [h for h in hs if k in seen[(tid, h)]]
        us = [h for h in sp if best[(tid, h)].get(k, 0.0) >= 1.0]
        miss = [h for h in sp if h not in us]
        print(f"  {tid} {k}：spread={len(sp)} useful={len(us)}｜无效 {miss}")

    (HERE / "utility.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    print("\n  已落盘 `utility.json`")


if __name__ == "__main__":
    main()
