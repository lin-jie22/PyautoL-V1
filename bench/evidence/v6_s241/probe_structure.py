#!/usr/bin/env python3
"""V152 跑前量测：只量**结构**（零分数），用来决定这一刀还能不能砍。

§218.8 登记 V152 = 「一条键的 1.0 占比有多少由键本身决定」，做法是按键分组做方差分解。
跑前先问三件事（都是 V151 已落盘的结构信息，零新判分）：
  P1 键有多少条？每条键覆盖几个实现？（分母样本量）
  P2 键与题是不是分离的？—— 若不分离，所谓「键效应」就是「题效应」
  P3 对比组还剩多少格？（单键题上「键级统计量」恒等于「题级统计量」⇒ 不是证据，纪律 4）

⛔ 本脚本不调用 `v119.ev`，一个分数都不算。
"""
from __future__ import annotations

import collections
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "v6_s240" / "readings.json"


def main() -> None:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    cells = d["cells"]
    print(f"# V152 跑前量测（来源 {SRC.parent.name}/readings.json，C_FULL 臂的 35 格）")
    print(f"格总数 = {len(cells)}｜有实例的格 = {sum(1 for c in cells if c['n_c'] > 0)}")

    # ---- P1 键的样本量
    by_key = collections.defaultdict(list)
    for c in cells:
        by_key[tuple(c["cell"][2])].append(c)
    print(f"\n## P1 键的样本量：不同的键 = {len(by_key)} 条")
    print("| 键 | 题 | 覆盖实现数 | n_c 明细 |")
    print("|---|---|---|---|")
    for k, v in sorted(by_key.items(), key=lambda kv: -len(kv[1])):
        tids = sorted({c["cell"][0] for c in v})
        ns = sorted(c["n_c"] for c in v)
        print(f"| `{k}` | {','.join(tids)} | {len(v)} | {ns} |")

    # ---- P2 键 ⊂ 题？
    k2t = {k: frozenset(c["cell"][0] for c in v) for k, v in by_key.items()}
    nested = all(len(t) == 1 for t in k2t.values())
    by_tid = collections.defaultdict(set)
    for k, t in k2t.items():
        for x in t:
            by_tid[x].add(k)
    print(f"\n## P2 键与题可分离吗：每条键只落在 1 个题上 = {nested}")
    print(f"题 → 键数：{ {t: len(ks) for t, ks in by_tid.items()} }")
    dual = {t: ks for t, ks in by_tid.items() if len(ks) >= 2}
    mono = [t for t, ks in by_tid.items() if len(ks) == 1]
    n_dual_cells = sum(len(v) for k, v in by_key.items() if k2t[k] & set(dual))
    n_mono_cells = len(cells) - n_dual_cells
    print(f"⇒ 有键内对比的题 = {sorted(dual)}（格 {n_dual_cells}）"
          f"｜单键题 = {sorted(mono)}（格 {n_mono_cells}，这些格上「键级」= 「题级」恒等）")

    # ---- P3 每个实现有几条键
    by_impl = collections.defaultdict(list)
    for c in cells:
        by_impl[(c["cell"][0], c["cell"][1])].append(c)
    hist = collections.Counter(len(v) for v in by_impl.values())
    print(f"\n## P3 每个实现的键数：{dict(sorted(hist.items()))}（实现-键格 = {len(by_impl)}）")

    # ---- P4 LOO 的可用样本
    print("\n## P4 留一法（LOO）每格可用的「同键其他格」数")
    loo = collections.Counter()
    for c in cells:
        k = tuple(c["cell"][2])
        loo[len(by_key[k]) - 1] += 1
    print(f"  每个格的同键其他格数分布 = {dict(sorted(loo.items()))}"
          f"｜最小 {min(loo)}｜中位 {sorted(loo.elements())[len(cells)//2]}")

    # ---- P5 n_c 的量级
    ns = sorted(c["n_c"] for c in cells if c["n_c"] > 0)
    print(f"\n## P5 分母 n_c（有实例的 {len(ns)} 格）：min {ns[0]}｜median {ns[len(ns)//2]}"
          f"｜max {ns[-1]}｜极差 {ns[-1] / ns[0]:.0f}×")
    print(f"  按题：")
    for t in sorted(by_tid):
        sub = sorted(c["n_c"] for c in cells if c["cell"][0] == t and c["n_c"] > 0)
        print(f"    {t:20s} n={len(sub)}  min {sub[0]}｜max {sub[-1]}")

    # ---- P6 成本
    print(f"\n## P6 成本：V151 已量过，C_FULL 的重算 = 39072 次 `ev`（618 ev/s ⇒ ~63s）")
    print("  本轮不新增任何判分口径，只把「每格 1.0 实例数」记下来（V151 只记了 max/min）。")


if __name__ == "__main__":
    main()
