#!/usr/bin/env python3
"""【内容无关的自检】安慰剂臂 —— 第二十一轮。

## 它要排除什么

真模型上，开/关自检在 `struct_convert` 上给出 `0.5600 → 0.7600`，
位移**只落在一道题**（z03 从 0.00 变 1.00）。这有两种解释：

  (a) **自检的内容**在起作用 —— 它指出"编译失败 line 7: unicodeescape"，
      模型据此定位并改对；
  (b) **多给了一次改写机会** —— 只要让模型再答一遍，答案就会变好，
      与"告诉它错在哪"无关。

第十七~十九轮在这里连栽三次（+0.05 落在安慰剂带内、+0.2000 被假记忆臂撤回、
形状臂证明形状也无贡献）。**同一个误读不许犯第四次。**

## 这一臂的造法：**只换掉那句理由**

⛔ 关键不是"随便打回一次"，而是**与 ON 臂完全同构**：

| 维度 | ON 臂 | 本臂（PL） |
|---|---|---|
| 触发条件 | 判据不过 | 判据不过（**同一个函数、同一份判据**） |
| 打回时机 | 第 1 次 `finish` | 第 1 次 `finish` |
| 打回次数 | 受 `verify_retries` 限 | 受 `verify_retries` 限 |
| 答案保命 | `_salvage` | `_salvage` |
| **打回时说的那句话** | **「编译失败 line 7: …」** | **「请再检查一遍，确认满足全部要求」** |

⇒ 单因子 = **那句话里有没有关于这个答案的信息**。T=0 ⇒ 第一版答案逐字相同、
打回时机逐位相同，唯一的差别是模型第二次看到的是什么。

    PL ≈ 0.76  ⇒ 收益属于 (b)：多一次改写就够，**自检的内容贡献 0** ⇒ 不得认领
    PL ≈ 0.56  ⇒ 收益属于 (a)：内容在起作用
    两者之间   ⇒ UNKNOWN，如实登记，不许挑一个

## ⛔ 两条纪律

1. 本臂只**读**内核，不改 `mw/` 任何文件（进程内打补丁，与 `bench/placebo/patch.py` 同款）。
2. 安慰剂的理由必须与真理由**同量级长度**：若只有四个字，
   "上下文里少了一段字"本身就成了第二个变量（第十七轮那条 0.1500 的噪声）。

用法：
    python3 bench/placebo_verify.py --bench --solver llm \\
        --suite bench/tasks/struct_convert.jsonl --temperature 0 --fresh
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mw import verify as vfy                                        # noqa: E402
from mw.core import Core                                            # noqa: E402
from mw.tools import ToolResult                                     # noqa: E402

#: 本臂读数（进程内）
PL: dict = {"rejects": 0, "runs": 0, "exhausted": 0}

#: 安慰剂理由 —— **刻意不含任何关于这个答案的信息**，但长度与真报告同量级。
#: ⚠️ 它必须仍然是一条"可执行"的指令（"再检查一遍"），否则模型会把它当成
#:    格式错误而拒绝继续 —— 那时测到的是"模型听不懂"，不是"内容无用"。
_GENERIC = ("自检未通过：请重新检查你刚才给出的答案，逐条核对题目里的每一项要求"
            "（函数名、输出形态、边界情形）是否都满足，然后再交付一次。"
            "如果你认为它已经满足，请原样再交一次。")

_orig_gate = Core._gate_finish


def _gate(self, result):                                            # noqa: ANN001
    """与 `_gate_finish` **逐行同构**，只把最后那句报告换成固定文本。"""
    checks = getattr(self, "_checks", ())
    if not checks:
        return result
    v = vfy.run(result.output, checks)
    self.verify_stats["runs"] += 1
    if v.ok:
        self.verify_stats["passed"] += 1
        return result
    self.verify_stats["failed"] += 1
    self._salvage = result.output
    PL["rejects"] += 1
    cap = max(0, int(self.s.limits.verify_retries))
    if self.verify_stats["failed"] > cap:
        self.verify_stats["exhausted"] += 1
        PL["exhausted"] += 1
        self._unverified = _GENERIC
        return result
    # ★ 唯一被换掉的东西：这句理由里**没有**行号、没有错误消息、没有判据名。
    return ToolResult(False, error=_GENERIC)


def install() -> None:
    Core._gate_finish = _gate                                       # type: ignore[assignment]


def dump_stats(rc: int) -> None:
    print(f"[placebo_verify] rejects={PL['rejects']} exhausted={PL['exhausted']} "
          f"rc={rc} （应与 ON 臂的 打回次数 完全一致）")


def main() -> int:
    install()
    print(f"[placebo_verify] 内容无关的自检：理由 {len(_GENERIC)} 字，"
          f"**不含**行号/错误消息/判据名")
    sys.argv = [sys.argv[0]] + sys.argv[1:]
    import run                                                      # noqa: E402
    rc = 0
    try:
        rc = run.main()
    finally:
        dump_stats(rc)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
