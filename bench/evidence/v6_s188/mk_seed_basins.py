#!/usr/bin/env python3
"""V99 九臂种子库：**盆的枚举** —— 把「选盆」变成可数对象。

V98 §166.3 的读数：答案不是连续敏感，而是落在**少数几个离散实现**（盆）上；
分数是盆的函数，扰动只是选盆。

本轮不押幅度（连续三轮押幅度都押错），只**枚举**：

| 臂 | 单字符改动 | 位置 | 已知盆？ |
|---|---|---|---|
| **O0** | 无（基线） | — | `075b4099`（0.4） |
| **O1** | 。→ `.` | 39 | ✅ `8089eee6`（**1.0**，V97 M3 / V98 N1） |
| **O2** | 两→ `2` | 21 | ✅ `5755e033`（0.6，V97 M1） |
| **O3** | （→ `(` | 0 | 新 |
| **O4** | ）→ `)` | 4 | 新 |
| **O5** | ：→ `:` | 9 | 新 |
| **O6** | 第一个 ，→ `,` | 18 | 新 |
| **O7** | 第二个 ，→ `,` | 25 | 新 |
| **O8** | 、→ `,` | 34 | 新 |

⛔ 位置与字符表**事先定死、不看结果**：就是 ZK2 里全部 7 个全角标点的 ASCII 对应，
外加一个已知盆（两→2）做复现锚。**没有挑过。**

用法：python3 bench/evidence/v6_s188/mk_seed_basins.py <root>
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.memory import MemoryStore  # noqa: E402

FIXED = {
    "ZK1": "（ZK1）去重惯例：比较前先去掉首尾空白并转小写，遇到重复时保留第一次出现的那条原文。",
    "ZK3": "（ZK3）开关惯例：只把 y/yes/1 当成真，'true'、'on' 这类历史脏值一律算假，其余无法识别的返回 None。",
    "ZK4": "（ZK4）截断惯例：超长时保留最后 n 个字符，前面加一个省略号字符 '…'（U+2026），不超长时原样返回。",
}
BASE = "（ZK2）金额惯例：千分位用空格分隔，保留两位小数，负数用圆括号包裹、不写负号。"

#: (位置, 原字符, 新字符) —— 位置由 `probe` 实测得到，事先定死
EDITS = {
    "O0": None,
    "O1": (39, "。", "."),
    "O2": (21, "两", "2"),
    "O3": (0, "（", "("),
    "O4": (4, "）", ")"),
    "O5": (9, "：", ":"),
    "O6": (18, "，", ","),
    "O7": (25, "，", ","),
    "O8": (34, "、", ","),
}
ORDER = ("ZK1", "ZK2", "ZK3", "ZK4")


def zk2_of(arm: str) -> str:
    e = EDITS[arm]
    if e is None:
        return BASE
    pos, old, new = e
    assert BASE[pos] == old, f"{arm}: 位置 {pos} 上不是 {old!r}，而是 {BASE[pos]!r}"
    return BASE[:pos] + new + BASE[pos + 1:]


def facts_of(arm: str) -> list[str]:
    body = dict(FIXED)
    body["ZK2"] = zk2_of(arm)
    return [body[k] for k in ORDER]


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v99/seeds")
    for arm in EDITS:
        t = zk2_of(arm)
        assert len(t) == len(BASE), f"{arm} 长度变了"
        diff = [i for i, (a, b) in enumerate(zip(BASE, t)) if a != b]
        assert len(diff) == (0 if EDITS[arm] is None else 1), f"{arm} 改了 {len(diff)} 处"
        d = root / arm
        d.mkdir(parents=True, exist_ok=True)
        db = d / "memory.db"
        if db.exists():
            db.unlink()
        m = MemoryStore(db)
        for f in facts_of(arm):
            m.commit_fact(f, source="abstract", confidence=0.6)
        m.close()
        mark = "" if EDITS[arm] is None else f"  位置 {diff[0]}: {BASE[diff[0]]} → {t[diff[0]]}"
        print(f"{arm}: {t}{mark}")


if __name__ == "__main__":
    main()
