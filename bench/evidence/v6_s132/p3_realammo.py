#!/usr/bin/env python3
"""V42 前置量（零成本，读的是 **V41 冻结的四轮日志**）：

> 真实日志里，`failover` 到底有多少**弹药**？

判据很窄：每一次"自检未过（第 k 次打回）"都落在某个 `候选=N` 的轮次里。
只有 `N ≥ 2` 且**当前候选不是最后一个**时，`cand_failover` 才有东西可换。

⛔ 为什么这一步必须做在跑批之前：V41 §107.3 已经量到 `候选≠1` 是 **0/64**，
但它统计的是"整轮"，而本轮要的是"**打回发生的那一轮**"的 N ——
两者只有在"打回总是发生在第 1 个候选上"时才相等，而那是需要**量**的，
不是可以推的（纪律 14：判据的窗口也是判据的一部分）。

⚠️ 解析的是**机制打印的名字**（`候选=N` / `自检未过（第`），
不是读数标签 —— 这两句都由 `core.py` 直接写死，见 §107.6 第 4 条的边界说明。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE.parent / "v6_s131" / "logs"

RE_CAND = re.compile(r"候选=(\d+)")
RE_REJECT = re.compile(r"自检未过（第")
RE_LIMIT = re.compile(r"自检连续")


def main() -> None:
    logs = sorted(LOGS.glob("*.log"))
    if not logs:
        print(f"⛔ 找不到日志：{LOGS}"); sys.exit(2)
    print("=" * 96)
    print("真实日志里 failover 的弹药量（V41 冻结的四轮：a1/a3 × r1/r2）")
    print("=" * 96)
    tot = {"rounds": 0, "rej": 0, "rej_ge2": 0, "limit": 0, "limit_ge2": 0}
    for p in logs:
        text = p.read_text(encoding="utf-8", errors="replace")
        cur = None
        n_round = n_rej = n_rej2 = n_lim = n_lim2 = 0
        for line in text.split("\n"):
            m = RE_CAND.search(line)
            if m:
                cur = int(m.group(1))
                n_round += 1
                continue
            if RE_REJECT.search(line):
                n_rej += 1
                if cur is not None and cur >= 2:
                    n_rej2 += 1
            elif RE_LIMIT.search(line):
                n_lim += 1
                if cur is not None and cur >= 2:
                    n_lim2 += 1
        print(f"  {p.name:16} 轮={n_round:>3} 打回={n_rej:>3}（其中 轮内候选≥2: {n_rej2}）"
              f" 超限={n_lim:>2}（其中 ≥2: {n_lim2}）")
        tot["rounds"] += n_round
        tot["rej"] += n_rej
        tot["rej_ge2"] += n_rej2
        tot["limit"] += n_lim
        tot["limit_ge2"] += n_lim2
    print("  " + "-" * 92)
    print(f"  合计            轮={tot['rounds']:>3} 打回={tot['rej']:>3}"
          f"（其中 轮内候选≥2: {tot['rej_ge2']}）"
          f" 超限={tot['limit']:>2}（其中 ≥2: {tot['limit_ge2']}）")
    print()
    print("⇒ 判决：" + (
        "**真实日志里一次弹药都没有** —— 打回全部发生在单候选轮次上，"
        "`cand_failover` 在这些窗口上的作用必然是 0（不是 0 因为机制没通，"
        "而是 0 因为**没有第二个候选可换**）。"
        if tot["rej_ge2"] == 0 and tot["limit_ge2"] == 0 else
        f"**有弹药**：{tot['rej_ge2'] + tot['limit_ge2']} 个窗口里轮内有 ≥2 个候选。"))


if __name__ == "__main__":
    main()
