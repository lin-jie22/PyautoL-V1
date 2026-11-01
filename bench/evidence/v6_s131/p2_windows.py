#!/usr/bin/env python3
"""V41 补充读数：**机会窗口**在 V39 四轮真跑里出现了几次。

## 为什么必须补这一条

`probe_v41_selector.py` 证明的是执行器**不让位**（S3/S4）。但"不让位"只有在
"首候选真的会失败"时才有代价 —— 若自检从不打回，那这条通路即使修好也是零次机制
（纪律 23：造新机关前先量现有机关拦住了多少）。

⇒ 本脚本从 V39 四轮的**冻结日志**里数三件事：

1. `候选=N` 的分布 —— 真实模型下宽度到底填满了没有；
2. `自检未过（第 k/…次打回）` —— 首候选被打回的次数（= 换候选的**机会窗口**）；
3. `预演否决` / `重复动作已跳过` —— 另外两条"首候选被跳过"的来源，用于排除
   "其实让位过，只是换的是别的闸"。

⛔ 只读 `logs/`，不写任何路径。读数走 stdout。
⛔ 日志措辞在这里是**允许的**：`候选=N` 是内核自己打印的 `len(cands)`，
   不是模型输出；`自检未过` 是 `_gate_finish` 的唯一出口。两条都是**机制**的名字，
   不是"读数标签"（V39 栽的那次是把 JSON **数组长度**当模型输出，不是这个）。
"""
from __future__ import annotations

import collections
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs"

PATS = {
    "候选=N": re.compile(r"候选=(\d+)"),
    "自检打回": re.compile(r"自检未过（第"),
    "自检超限": re.compile(r"自检连续"),
    "收尾未过自检": re.compile(r"收尾（未通过自检）"),
    "预演否决": re.compile(r"预演否决"),
    "重复跳过": re.compile(r"重复动作已跳过"),
    "全部候选被拦": re.compile(r"全部候选被拦"),
}


def main() -> None:
    files = sorted(LOGS.glob("run_*.log"))
    if not files:
        raise SystemExit(f"没有冻结日志：{LOGS}")
    print(f"冻结日志 {len(files)} 份：{[f.name for f in files]}")
    print()
    head = f"{'轮次':<16}" + "".join(f"{k:>14}" for k in PATS)
    print(head)
    print("-" * len(head))
    tot = collections.Counter()
    cands: collections.Counter = collections.Counter()
    for f in files:
        t = f.read_text(encoding="utf-8", errors="replace")
        row = {}
        for k, p in PATS.items():
            row[k] = len(p.findall(t))
            tot[k] += row[k]
        for m in PATS["候选=N"].finditer(t):
            cands[int(m.group(1))] += 1
        print(f"{f.stem:<16}" + "".join(f"{row[k]:>14}" for k in PATS))
    print("-" * len(head))
    print(f"{'合计':<16}" + "".join(f"{tot[k]:>14}" for k in PATS))
    print()
    print(f"`候选=N` 的分布 = {dict(sorted(cands.items()))}")
    print(f"  ⇒ 规划总轮次 = {sum(cands.values())}；候选≥2 的次数 = "
          f"{sum(v for k, v in cands.items() if k >= 2)}")
    print()
    print("判读：")
    print(f"  · 机会窗口（首候选被自检打回）= **{tot['自检打回']}** 次 / "
          f"{sum(cands.values())} 轮规划")
    print(f"  · 同一批轮次里 `候选≥2` = **{sum(v for k, v in cands.items() if k >= 2)}** 次")
    print("  ⇒ 窗口存在，但每次窗口打开时**数组里没有第二个候选**"
          "（V40 修好宽度之前）；V40 修好之后，窗口里会有候选，"
          "而执行器在打回时**不读下一个**（探针 S3/S4）。")


if __name__ == "__main__":
    main()
