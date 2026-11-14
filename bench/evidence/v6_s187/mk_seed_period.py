#!/usr/bin/env python3
"""V98 六臂种子库：**句点归因** —— M3 的 1.00 是「那个位置」还是「ASCII 句点」这件事本身。

V97 §165.3 的读数：把 ZK2 句末的中文句号「。」换成 ASCII「.」，g7d3 从 0.4 → 1.0，
mean 到 **1.00**（96 轮首次满分）。但我**解释不了为什么**。

本轮的判别很干脆：把同一个字符替换**搬到别的位置**。

| 臂 | 改的是哪一条的句末 | 与 M0 的差 |
|---|---|---|
| **N0** | 无（基线 = V93 R1 逐字节） | — |
| **N1** | ZK2 句末（= V97 的 M3，**已知 1.00**） | 1 字 |
| **N2** | ZK1 句末 | 1 字 |
| **N3** | ZK3 句末 | 1 字 |
| **N4** | ZK4 句末 | 1 字 |
| **N5** | **四条全换** | 4 字 |

- 若只有 N1 = 1.00 ⇒ 效应**锁在 ZK2 这一条的渲染上**，不是通用规则。
- 若 N2/N3/N4 也有 1.00 ⇒ 它跟「哪条」无关，是「块里出现 ASCII 句点」这件事。
- 若 N5 = 1.00 ⇒ **可以写成一条通用写端规则**（这是本轮最想要的结果）。

⚠️ N5 与 N1~N4 不是同一类扰动（改 4 个字），它的作用是「通用性检验」，不作因果对照。

用法：python3 bench/evidence/v6_s187/mk_seed_period.py <root>
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.memory import MemoryStore  # noqa: E402

BASE = {
    "ZK1": "（ZK1）去重惯例：比较前先去掉首尾空白并转小写，遇到重复时保留第一次出现的那条原文。",
    "ZK2": "（ZK2）金额惯例：千分位用空格分隔，保留两位小数，负数用圆括号包裹、不写负号。",
    "ZK3": "（ZK3）开关惯例：只把 y/yes/1 当成真，'true'、'on' 这类历史脏值一律算假，其余无法识别的返回 None。",
    "ZK4": "（ZK4）截断惯例：超长时保留最后 n 个字符，前面加一个省略号字符 '…'（U+2026），不超长时原样返回。",
}
ORDER = ("ZK1", "ZK2", "ZK3", "ZK4")

#: 每条改哪一条的句末
CHANGE = {"N0": (), "N1": ("ZK2",), "N2": ("ZK1",), "N3": ("ZK3",),
          "N4": ("ZK4",), "N5": ("ZK1", "ZK2", "ZK3", "ZK4")}


def facts_of(arm: str) -> list[str]:
    body = dict(BASE)
    for tag in CHANGE[arm]:
        assert body[tag].endswith("。"), tag
        body[tag] = body[tag][:-1] + "."
    return [body[k] for k in ORDER]


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v98/seeds")
    for arm in CHANGE:
        facts = facts_of(arm)
        # 自检：每条只差句末一个字符
        for tag, t in zip(ORDER, facts):
            if t == BASE[tag]:
                continue
            assert len(t) == len(BASE[tag]), f"{arm}/{tag} 长度变了"
            diff = [i for i, (a, b) in enumerate(zip(BASE[tag], t)) if a != b]
            assert diff == [len(t) - 1], f"{arm}/{tag} 改的不止句末：{diff}"
        d = root / arm
        d.mkdir(parents=True, exist_ok=True)
        db = d / "memory.db"
        if db.exists():
            db.unlink()
        m = MemoryStore(db)
        for t in facts:
            m.commit_fact(t, source="abstract", confidence=0.6)
        m.close()
        print(f"{arm}: 改 {CHANGE[arm] or '无'}  总={sum(len(t) for t in facts)} 字")
        for tag, t in zip(ORDER, facts):
            mark = "  ← 改" if t != BASE[tag] else ""
            print(f"     [{len(t):3d}] {tag} …{t[-8:]}{mark}")
        print()


if __name__ == "__main__":
    main()
