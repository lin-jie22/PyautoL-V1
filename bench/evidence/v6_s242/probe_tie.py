#!/usr/bin/env python3
"""V153 核账：预注册写「并列取更保守的 τ」，实现里写成了**最小 τ**（`>` 而非 `>=`）。

这不许藏（纪律 1）。本脚本把两种并列规则都跑一遍，并逐格列出误判，
用于判定 W3 的桶在**预注册口径**下是否仍成立。
"""
from __future__ import annotations

import collections
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "v6_s241" / "readings.json"
TAUS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]


def main() -> None:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    cells = [c for c in d["cells"] if c["n"] > 0]
    for c in cells:
        c["tid"], c["h"] = c["cell"][0], c["cell"][1]
        c["key"] = tuple(c["cell"][2])

    def others(c):
        return [x for x in cells if x["tid"] == c["tid"] and x["h"] != c["h"]]

    def fit(c, conservative: bool):
        o = others(c)
        best, bt = -1, TAUS[-1]
        for tau in TAUS:                       # 升序：`>` 保首个（最小 τ）；`>=` 保末个（最大 τ）
            ok = sum(1 for x in o if (x["s0"] >= tau) == (x["max_c"] >= 1.0))
            if (ok > best) if not conservative else (ok >= best):
                best, bt = ok, tau
        return bt

    print("# V153 核账：τ 的并列规则（预注册=保守=更大的 τ）")
    for tag, cons in (("实现（最小 τ）", False), ("预注册（最大 τ）", True)):
        hit, errs = 0, []
        tau_hist = collections.defaultdict(list)
        for c in cells:
            tau = fit(c, cons)
            tau_hist[c["tid"]].append(tau)
            ok = (c["s0"] >= tau) == (c["max_c"] >= 1.0)
            hit += ok
            if not ok:
                errs.append(c)
        print(f"\n## {tag}：{hit}/33 = {hit / 33 * 100:.1f}%"
              f"｜τ 分布 {{{', '.join(f'{t}: {dict(collections.Counter(v))}' for t, v in tau_hist.items())}}}")
        print("  | 题 | 实现 | s0 | p | 真值 |")
        print("  |---|---|---|---|---|")
        for c in errs:
            print(f"  | {c['tid'][:12]} | {c['h']} | {c['s0']:.2f} | {c['p']:.3f} | "
                  f"{'VALID' if c['max_c'] >= 1.0 else 'INVALID'} |")

    print("\n## 结构：s0 与真值的关系（这是 1 次判分能提供信息的全部来源）")
    g = collections.defaultdict(list)
    for c in cells:
        g[round(c["s0"], 2)].append(c)
    print("  | s0 | 格数 | VALID | INVALID |")
    print("  |---|---|---|---|")
    for s0, cs in sorted(g.items()):
        v = sum(1 for c in cs if c["max_c"] >= 1.0)
        print(f"  | {s0} | {len(cs)} | {v} | {len(cs) - v} |")


if __name__ == "__main__":
    main()
