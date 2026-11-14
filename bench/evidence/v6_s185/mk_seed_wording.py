#!/usr/bin/env python3
"""V96 六臂种子库：**同一个 ZK2 语义，六种措辞**。

问题：V95 §163.6 登记 g7d3 上已有**三种完全不同的写法**都拿到 1.0（示例 / 规则+示例 /
极简规则），也有三种停在 0.4 或 0.0。要问的是 **1.0 是「可达」还是「碰运气」** ——
即「写端能不能靠设计保证效果」，还是只能靠**试**。

装置：只有 ZK2 变；ZK1/ZK3/ZK4 **逐字等于 V93 R1**（换一个字就不可比）。
⇒ 任何一个格子的差异都只能来自 ZK2 的措辞（或噪声，而噪声地板 = 0）。

| 臂 | 风格 | 与历史的关系 |
|---|---|---|
| W1 极简 | 最短、信息项不删 | 新（只压 ZK2 ⇒ ⚠️ **不等于** V95 S1，那个臂四题全压了） |
| W2 原规则 | 完整书面句 | ⭐ **逐字节 = V93 R1**（历史 g7d3 = 0.4，块 211 字、位序同） ⇒ 同批次校准锚 |
| W3 全符号化 | 键=值 + 单位 + 码位 | 新 |
| W4 算法式 | 分步写「怎么做」 | 新 |
| W5 英文 | 全英文 | 新 |
| W6 列举式 | 三个子规则各列一句，**不出现任何期望输出** | 新 |

⛔ W6 刻意**不**给 `1234.5 得 '1 234.50'`：那是 V93 R2 的形态（test leakage）。
   本轮要量的是「措辞」，不是「给答案」。

用法：python3 bench/evidence/v6_s185/mk_seed_wording.py <root>
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.memory import MemoryStore  # noqa: E402

#: ⛔ 三题**逐字**固定，等于 V93 R1 / V85 B 臂
ZK_FIXED = [
    "（ZK1）去重惯例：比较前先去掉首尾空白并转小写，遇到重复时保留第一次出现的那条原文。",
    "（ZK3）开关惯例：只把 y/yes/1 当成真，'true'、'on' 这类历史脏值一律算假，其余无法识别的返回 None。",
    "（ZK4）截断惯例：超长时保留最后 n 个字符，前面加一个省略号字符 '…'（U+2026），不超长时原样返回。",
]

#: 六个 ZK2 变体 —— 语义完全相同（空格千分位 / 2 位小数 / 负数圆括号 / 不写负号）
ZK2 = {
    "W1": "（ZK2）金额惯例：千分位用空格，两位小数，负数圆括号且不带负号。",
    "W2": "（ZK2）金额惯例：千分位用空格分隔，保留两位小数，负数用圆括号包裹、不写负号。",
    "W3": "（ZK2）金额惯例：分组符=' '（U+0020，每 3 位一组）；小数位=2；负数=圆括号包裹，括号内不出现 '-'。",
    "W4": "（ZK2）金额惯例：先取绝对值保留两位小数；整数部分自右向左每三位插一个空格；原值为负则整串加圆括号，括号里不写 '-'。",
    "W5": "（ZK2）amount rule: thousands grouped by a space (U+0020); 2 decimals; negatives wrapped in parentheses, no minus sign.",
    "W6": "（ZK2）金额惯例：三位一组的千分位之间用的是空格不是逗号；小数位固定两位、不足补零；负数写成圆括号包住的串，括号里没有减号。",
}

#: 组装顺序 = ZK1, ZK2, ZK3, ZK4（与 V93 R1 同序）
ORDER = ("ZK1", "ZK2", "ZK3", "ZK4")


def facts_of(arm: str) -> list[str]:
    body = {**{f"ZK{i}": t for i, t in ((1, ZK_FIXED[0]), (3, ZK_FIXED[1]), (4, ZK_FIXED[2]))},
            "ZK2": ZK2[arm]}
    return [body[k] for k in ORDER]


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v96/seeds")
    bad = [(a, len(t)) for a, t in ZK2.items() if len(t) > 120]
    if bad:
        print(f"⛔ ZK2 有变体超过 120 字（会被 f.text[:120] 截尾）：{bad}")
        raise SystemExit(1)

    for arm in ZK2:
        facts = facts_of(arm)
        d = root / arm
        d.mkdir(parents=True, exist_ok=True)
        db = d / "memory.db"
        if db.exists():
            db.unlink()
        m = MemoryStore(db)
        for t in facts:
            m.commit_fact(t, source="abstract", confidence=0.6)
        m.close()
        print(f"{arm}: ZK2={len(ZK2[arm]):3d} 字  总={sum(len(t) for t in facts):3d} 字")
        print(f"     {ZK2[arm]}")

    print("\n对照（逐字固定项，六臂相同）：")
    for t in ZK_FIXED:
        print(f"  [{len(t):3d}] {t}")
    print("\nZK2 字数排序：", "  ".join(f"{a}={len(t)}" for a, t in sorted(ZK2.items(), key=lambda kv: len(kv[1]))))


if __name__ == "__main__":
    main()
