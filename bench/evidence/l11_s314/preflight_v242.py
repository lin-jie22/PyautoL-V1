#!/usr/bin/env python3
"""V242 静态预检：**每一条撤回锚点在当前源码里是否仍恰好出现一次**。

## 为什么需要它（本轮实测逼出来的）

全量 382 条动态复跑要跑 382 次完整契约套件。实测单条 0.5~2 分钟
（有些变异会把预算/墙钟路径改成真的去等）⇒ 全量 **6~10 小时**。
本轮两次尝试分别被沙箱「执行能力过期」（4 分区跑了 6.5 小时、一行结果没留下）
与工具 10 分钟上限杀掉。

但**锚点腐烂**（V240 那 18 条的病）根本不需要跑套件：它只是
「`old` 在目标文件里出现 0 次」。这一步是 O(秒) 的，而且它正是
**唯一会静默通过**的那一类（出现 0 次 ⇒ 撤回请求被忽略 ⇒ 基线照绿）。

⇒ 本检把「全量」拆成两层：
  1. **静态**（本脚本，全部 382 条，几秒）：锚点唯一性；
  2. **动态**（`withdraw_check.py <tags>`，逐条）：目标是否真的转红。
⛔ 静态绿**不能**替代动态：锚点唯一但目标不红 = 断言恒真。
"""

from __future__ import annotations

import sys
from pathlib import Path

V1 = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(V1 / "bench"))

import withdraw_check as W  # noqa: E402


def main() -> int:
    bad: list[tuple[str, str, str]] = []
    for tag, rel, old, new in W.CASES:
        path = V1 / rel
        if not path.exists():
            bad.append((tag, rel, "目标文件不存在"))
            continue
        src = path.read_text(encoding="utf-8")
        if old == "":
            # 追加型：没有锚点可查，只要求目标文件存在
            continue
        _, ns, err = W._apply_mutation(src, old, new)
        if err:
            bad.append((tag, rel, err))

    n = len(W.CASES)
    print(f"静态预检：{n} 条撤回锚点（⛔ 不跑套件）")
    if not bad:
        print(f"  ✅ {n}/{n} 条锚点在当前源码里**恰好出现一次**")
        return 0
    print(f"  ❌ {len(bad)} 条不合格：")
    for tag, rel, why in bad:
        print(f"     {tag:6s} {rel:26s} {why}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
