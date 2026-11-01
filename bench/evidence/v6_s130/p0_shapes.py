#!/usr/bin/env python3
"""V40 前置量：把 15 份冻结原文按**形态**分类（只做正则计数，不跑内核）。

## 为什么要单独一步

`PREREG.md` §二的分类表必须能被人复核。它是"读数之前"的事实：
**输入长什么样**。跑内核之后就不能再改它了（改了就变成事后叙事）。

⛔ 只读 `raw/`，不写任何证据路径（纪律 32/34）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

TQ = '"' * 3
RAW = Path(__file__).resolve().parent / "raw"


def shape_of(text: str) -> str:
    """返回形态标签（A/B/C/D/E）。判据顺序即优先级，写死在这里。"""
    if text.count('"tool"') == 0:
        return "E 无JSON"
    if TQ in text:
        return "C 三引号"
    if re.search(r'"finish"\s*:\s*\{', text) and text.count("{") - text.count("}") != 0:
        return "D 兄弟finish"
    if re.search(r"-{2,}\s*ANSWER\s*-{2,}", text):
        return "B 分隔符"
    try:
        json.loads(text, strict=False)
        return "A 已可解析"
    except (json.JSONDecodeError, ValueError):
        return "? 未分类"


def main() -> int:
    files = sorted(RAW.glob("*.txt"))
    if not files:
        print(f"⛔ 没有原文：{RAW}")
        return 2
    print("=" * 100)
    print("V40 前置量：15 份冻结原文的形态分类（读数之前冻结）")
    print("=" * 100)
    print(f"{'样本':20}{'字符':>6}{'tool数':>7}{'三引号':>7}{'花括号差':>9}"
          f"{'形态':>14}")
    print("-" * 100)
    tally: dict[str, int] = {}
    for p in files:
        t = p.read_text(encoding="utf-8", errors="replace")
        s = shape_of(t)
        tally[s] = tally.get(s, 0) + 1
        print(f"{p.name:20}{len(t):>6}{t.count(chr(34) + 'tool' + chr(34)):>7}"
              f"{t.count(TQ):>7}{t.count('{') - t.count('}'):>9}{s:>14}")
    print("-" * 100)
    print(f"合计 {len(files)} 份："
          + " / ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    print("读法：只有 C（三引号）与 D（兄弟 finish）两组需要修；")
    print("      B 组在探针里报失败是探针自己的口径错（内核先 split_answer）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
