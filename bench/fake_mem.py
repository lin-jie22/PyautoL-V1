#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【假记忆】臂 runner —— 本文件现在只是一层**薄壳**。

原来的全部机制（等长闸门 / 写入即替换 / 题边界水位 / 统计落盘）
已并进 `bench/placebo/patch.py`（DESIGN §17-16）。
这里只剩：解析臂名 → `install()` → 跑 `run.main()` → `dump_stats()`。

★ 它要回答的三个问题（三臂各一个）

  | 臂 | 记忆内容 | 回答 |
  |---|---|---|
  | E1 | 逐字双射替换（保结构、灭语义） | 语义有没有贡献 |
  | E2 | 同字符多重集打乱 | 排除"多了一堆字"而不是"多了答案" |
  | F  | 保字符序列、破行几何           | **形状**有没有贡献（§17-15） |
  | T  | 只读探针，mapper 恒等          | 装测量本身有没有改变被测对象 |

  加上两个不走本 runner 的臂（E0 空记忆、E3 真累积），
  才构成一次完整的"存在性 / 语义 / 形状"三分实验。

★ 用法
    python3 bench/fake_mem.py F --bench --solver llm \
        --suite bench/tasks/struct_convert.jsonl --memory-dir /tmp/x/mem

    # E0 / E3：直接 `python3 run.py …`（本 runner **拒绝**它们，见 ARMS）

返回值：透传 run.main() 的返回码；违反不变量时直接抛异常
      （Traceback 是**特性**：它意味着"这一臂的数据作废"，而不是"假装无事发生"）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from bench.placebo.patch import (ARMS, WORKING_MODES, dump_stats,   # noqa: E402
                                 install)

#: 兼容旧调用点：`from fake_mem import ARMS` 之类不该再出现，
#: 但契约测试仍在用 `_FM.ARMS` 做"E0/E3 必须被拒绝"的断言。
__all__ = ["ARMS", "WORKING_MODES", "main"]


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2
    arm = sys.argv[1]
    rest = sys.argv[2:]

    fake_working = "prev"
    for i, a in enumerate(rest):
        # ⛔ 必须从传给 run.py 的参数里摘掉 —— 否则 argparse 会把它当未知参数。
        #   早先这里写的是 `if "--fake-working" in rest`（布尔开关），
        #   而 install 现在要三个档 ⇒ 开关与值必须一次摘干净，不能只摘前者。
        if a == "--fake-working":
            if i + 1 >= len(rest):
                print(f"❌ --fake-working 需要一个值：{WORKING_MODES}")
                return 2
            fake_working = rest[i + 1]
            if fake_working not in WORKING_MODES:
                print(f"❌ --fake-working 只接受 {WORKING_MODES}，收到 {fake_working!r}")
                return 2
            rest = rest[:i] + rest[i + 2:]
            break
        if a.startswith("--fake-working="):
            fake_working = a.split("=", 1)[1]
            if fake_working not in WORKING_MODES:
                print(f"❌ --fake-working 只接受 {WORKING_MODES}，收到 {fake_working!r}")
                return 2
            rest = rest[:i] + rest[i + 1:]
            break

    install(arm, fake_working=fake_working)
    print(f"[fake_mem] 臂={arm} working={fake_working}")

    # sys.argv[1:] 会被 run.main() 的 argparse 读到 ⇒ 先卸掉已被我吃掉的前两项
    sys.argv = [sys.argv[0]] + rest
    import run                                                       # noqa: E402
    rc = 0
    try:
        rc = run.main()
    finally:
        # 即使中途炸了也要落盘 —— 那时 violations 可能非空，
        # 而"这一臂到底有没有被污染过"正是事后最需要核对的东西。
        dump_stats(arm, rc)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
