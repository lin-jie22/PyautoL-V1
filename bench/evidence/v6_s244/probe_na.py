#!/usr/bin/env python3
"""V155 跑前量测（零分数）：V154 的 `n` 增量，在**部署分辨率**下还剩多少？

V154 的增益来自 `n_c`（满 d=2 闭包下这条键的实例数）。但 V151 已证 `n` 是**搜索深度的函数**：
部署搜索（A_DEPLOY）每条键只判 1~8 个实例。若增益只在 `n_c` 上有，那它要求「先满枚举」——
代价是 8.2s 的 `gen`（0 次判分，但不是 0 成本）。

本脚本只量结构：
  P1 逐格的 `n_c`（满枚举）与 `n_a`（部署），以及两者的分档是否同序
  P2 `g7d3`（V154 增益的主要来源）在两种分辨率下还分不分得开
  P3 题内重排 `n` 的可行样本量（每题的格数）
⛔ 不调用 `v119.ev`。
"""
from __future__ import annotations

import collections
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
C = HERE.parent / "v6_s241" / "readings.json"
A = HERE.parent / "v6_s240" / "readings.json"


def key_of(cell):
    return (cell[0], cell[1], tuple(cell[2]))


def main() -> None:
    c = json.loads(C.read_text(encoding="utf-8"))
    a = json.loads(A.read_text(encoding="utf-8"))
    cells = [x for x in c["cells"] if x["n"] > 0]
    na = {key_of(x["cell"]): x["n_a"] for x in a["cells"]}
    for x in cells:
        x["na"] = na.get(key_of(x["cell"]), 0)
        x["valid"] = x["max_c"] >= 1.0

    print("# V155 跑前量测：`n` 在部署分辨率下还剩什么")
    print(f"格 {len(cells)}｜VALID {sum(1 for x in cells if x['valid'])}")

    print("\n## P1 `n_c`（满枚举）vs `n_a`（部署）逐题")
    print("  | 题 | 格数 | n_c 明细 | n_a 明细 | 同序（秩相关） |")
    print("  |---|---|---|---|---|")
    for t in sorted({x["cell"][0] for x in cells}):
        cs = [x for x in cells if x["cell"][0] == t]
        nc = [x["n"] for x in cs]
        nb = [x["na"] for x in cs]
        same = sum(1 for i in range(len(cs)) for j in range(i + 1, len(cs))
                   if (nc[i] - nc[j]) * (nb[i] - nb[j]) > 0)
        tot = sum(1 for i in range(len(cs)) for j in range(i + 1, len(cs))
                  if nc[i] != nc[j])
        print(f"  | {t[:14]} | {len(cs)} | {nc} | {nb} | {same}/{tot} 对同序 |")

    print("\n## P2 `g7d3`：V154 增益的主要来源，两种分辨率下还分不分得开")
    print("  | 实现 | s0 | n_c | n_a | 真值 |")
    print("  |---|---|---|---|---|")
    for x in sorted([y for y in cells if y["cell"][0] == "g7d3_fmt_amount"],
                    key=lambda y: -y["n"]):
        print(f"  | {x['cell'][1]} | {x['s0']:.2f} | {x['n']} | {x['na']} | "
              f"{'V' if x['valid'] else 'I'} |")
    inv = [x for x in cells if x["cell"][0] == "g7d3_fmt_amount" and not x["valid"]]
    val = [x for x in cells if x["cell"][0] == "g7d3_fmt_amount" and x["valid"]]
    print(f"  ⇒ g7d3 的 INVALID n_c = {[x['n'] for x in inv]}｜VALID n_c = {[x['n'] for x in val]}")
    print(f"  ⇒ g7d3 的 INVALID n_a = {[x['na'] for x in inv]}｜VALID n_a = {[x['na'] for x in val]}")

    print("\n## P3 题内重排的样本量")
    for t in sorted({x["cell"][0] for x in cells}):
        cs = [x for x in cells if x["cell"][0] == t]
        print(f"  {t[:20]:22s} 格 {len(cs):2d}｜不同 n_c 取值 {sorted({x['n'] for x in cs})}")

    print("\n## P4 成本：满枚举 8.2s / 0 次判分（V151 P3）；部署搜索只看 1~8 个实例")


if __name__ == "__main__":
    main()
