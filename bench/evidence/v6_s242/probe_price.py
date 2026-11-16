#!/usr/bin/env python3
"""V153 跑前量测：只量**结构**（零分数），决定「0/1/k 次判分」这三档能不能比。

三档要能比，必须先确认三件事（全部来自 V152 已落盘的 `s0` 与 `n`，一个分数都不算）：
  P1 **实现级留一**（LOIO）之后，每个待预测的格还剩几个同题异实现可参考？
     —— 若某题只剩 1 个实现，留一后 1-NN 无定义 ⇒ 该格只能用常数，必须报出来。
  P2 每个题的 `s0` 支撑集有多大？（映射的「信息量上限」由它决定：`s0` 只有 6 个取值）
  P3 k 次采样的可模拟性：每格 `n_i` 与 `k` 的关系（`n_i < k` 的格不能用二项近似，须用超几何全取）。

⛔ 不调用 `v119.ev`。
"""
from __future__ import annotations

import collections
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "v6_s241" / "readings.json"
KS = [1, 2, 4, 8, 16, 32]


def main() -> None:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    cells = [c for c in d["cells"] if c["n"] > 0]
    print("# V153 跑前量测（来源 v6_s241/readings.json；C_FULL 的 33 个有实例格）")

    by_tid = collections.defaultdict(list)
    for c in cells:
        by_tid[c["cell"][0]].append(c)

    print("\n## P1 实现级留一（LOIO）之后还剩多少参考格")
    print("| 题 | 实现数 | 格数 | 每个留出实现可用的同题异实现格（min~max） | s0 支撑集 |")
    print("|---|---|---|---|---|")
    loio_ok = 0
    for t, cs in sorted(by_tid.items()):
        impls = sorted({c["cell"][1] for c in cs})
        per = []
        for h in impls:
            others = [c for c in cs if c["cell"][1] != h]
            per.append(len(others))
        s0s = sorted({round(c["s0"], 2) for c in cs})
        print(f"| {t} | {len(impls)} | {len(cs)} | {min(per)}~{max(per)} | {s0s} |")
        loio_ok += sum(1 for x in per if x > 0)
    print(f"⇒ 留一后仍有参考格的（实现, 题）组合 = {loio_ok}（总共 "
          f"{sum(len({c['cell'][1] for c in cs}) for cs in by_tid.values())}）")

    print("\n## P2 每个题的 s0 → p 对照（映射能用的全部信息就这张表）")
    for t, cs in sorted(by_tid.items()):
        g = collections.defaultdict(list)
        for c in cs:
            g[round(c["s0"], 2)].append(c["p"])
        print(f"  {t}: " + "｜".join(
            f"s0={k}: p={[round(x, 3) for x in v]}" for k, v in sorted(g.items())))

    print("\n## P3 k 次采样：n_i 与 k 的关系（n_i < k 的格必须全取，不能近似）")
    for k in KS:
        small = [c for c in cells if c["n"] < k]
        print(f"  k={k:2d}: n_i<k 的格 = {len(small)}/{len(cells)}"
              f"（{sorted({c['cell'][0][:4] for c in small})}）")

    print("\n## P4 成本：三档里只有 Tk 需要判分，而它可以从**既有分数**模拟")
    print("  k 次采样 = 从该格的 n_i 个实例里无放回抽 k 个 ⇒ 超几何，零新增 `ev`。")
    print("  ⇒ 本轮全部读数的判分成本 = **0**（T1 的 `s0` 也是 V152 已算过的）。")


if __name__ == "__main__":
    main()
