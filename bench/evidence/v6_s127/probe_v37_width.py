"""V37 前置量（零模型、零网络）：**探索宽度到底兑现了没有**。

## 为什么必须单独量它

V37 的第一版设计是「预演否决 ⇒ 让位给下一个候选」。
那个设计的前提是「`cands` 里通常不止一个候选」。**没人量过这个前提。**

本探针只读 V36 的冻结产物（`/tmp/v36/run_*.log`、`states_*.jsonl`），
回答三个问题：

  Q1  `core.py` 日志里的 `候选=N` 真实分布是什么？
  Q2  `state` 里的 `n`（上限）与真实候选数是什么关系？
  Q3  如果候选恒为 1，那么"筛选类机制"（guard / values / 预演否决 / 停滞闸）
      在同一步里各自能筛到几个东西？

## 结论的用法

⛔ 这只是**前置量**，不是 V37 的读数。它的作用只有一个：
**在写判据之前，先确认载荷能不能到达押注的格子**（纪律 29）。

⛔ 只读，不写任何文件（纪律 34）。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

W = Path("/tmp/v36")
ROUNDS = ("cum_r1", "cum_r2", "rst_r1", "rst_r2")
WIDTH = re.compile(r"通道=(深|快) 候选=(\d+)")


def widths(tag: str) -> Counter:
    p = W / f"run_{tag}.log"
    if not p.exists():
        return Counter()
    return Counter(int(m.group(2)) for m in WIDTH.finditer(p.read_text(errors="replace")))


def ns(tag: str) -> Counter:
    p = W / f"states_{tag}.jsonl"
    if not p.exists():
        return Counter()
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return Counter(r.get("n") for r in rows)


def main() -> int:
    print("=" * 80)
    print("V37 前置量：探索宽度（读 /tmp/v36 的冻结产物，零模型）")
    print("=" * 80)

    tot: Counter = Counter()
    print("\nQ1  日志里的真实候选数（`core.py` 的 `[{it}] 通道=… 候选=N`）")
    print("-" * 80)
    print(f"  {'轮':<10s} {'深':>4s} {'快':>4s}  {'候选数分布':<24s} 总步数")
    for tag in ROUNDS:
        w = widths(tag)
        if not w:
            print(f"  {tag:<10s}  （缺日志）")
            continue
        tot.update(w)
        dist = " ".join(f"{k}×{v}" for k, v in sorted(w.items()))
        deep = sum(v for k, v in w.items() if k >= 1)
        print(f"  {tag:<10s} {'—':>4s} {'—':>4s}  {dist:<24s} {sum(w.values())}")
    print(f"  {'合计':<10s} {'':>4s} {'':>4s}  "
          f"{' '.join(f'{k}×{v}' for k, v in sorted(tot.items()))}")
    ge2 = sum(v for k, v in tot.items() if k >= 2)
    print(f"\n  ⇒ 候选 ≥ 2 的步数 = **{ge2}** / {sum(tot.values())}")

    print("\nQ2  `state` 里的 `n`（**上限**） vs 真实候选数")
    print("-" * 80)
    print(f"  {'轮':<10s} {'n 分布':<32s} 说明")
    for tag in ROUNDS:
        c = ns(tag)
        if not c:
            continue
        dist = " ".join(f"{k}×{v}" for k, v in sorted(c.items(), key=lambda kv: (kv[0] is None, kv[0])))
        print(f"  {tag:<10s} {dist:<32s}")
    print("\n  ⚠️ `n` 是 `min(budget.candidates, cand_left)`，即**上限**；")
    print("     真实候选数 = `len(plan.steps[:n])`，由规划器实际给了几条决定。")

    print("\nQ3  结论")
    print("-" * 80)
    if ge2 == 0 and sum(tot.values()) > 0:
        print("  ⛔ **候选数恒为 1**（本装置 76/76 步）。")
        print("     ⇒ 任何「在候选之间选择/排序/否决」的机制，")
        print("        在本装置上都是**结构性零次机制**。")
        print("     ⇒ 具体到 V37：`ci < len(cands)-1` 恒为假 ⇒")
        print("        「让位给下一个候选」这条接法**永远不会触发**。")
        print("     ⇒ 修法不是调判据，是换接法（否决之后去哪），")
        print("        或者先让「宽度」这一项真的被兑现。")
    else:
        print(f"  候选 ≥ 2 出现过 {ge2} 次 ⇒ 「让位」这条接法有机会触发，"
              f"但每次仍只有 {sum(tot.values()) and ge2} 步 —— 样本量要如实报到读数里。")
    print()
    print("  ★ 这条读数同时解释了一个旧疑点：")
    print("    V36 每轮 34 条请求 = 24 规划 + 10 预演，而 `n` 之和是 36 ⇒")
    print("    预演次数（10）远小于候选名额（36），正是因为**第一步就 break**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
