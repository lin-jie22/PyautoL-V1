#!/usr/bin/env python3
"""V97 四臂种子库：**最小扰动** —— 只改一个字。

V96 §164.4 的读数：改一条**与本题无关**的事实（ZK2），4/6 次把 g7e1 从 1.0 打到 0.4。
那些改写都是 20+ 字的**重写**（Z|K2 33~117 字），所以还分不清是
  （a）语义/结构变了，还是（b）**字节串**本身变了。

本轮把扰动压到极限：**每条只与基线差一个字符，语义完全相同**。

| 臂 | 与 M0 的差 | 字数 | 词元数 |
|---|---|---|---|
| **M0** | 基线 = V93 R1 的 ZK2（逐字节） | 40 | 6 |
| **M1** | 「保留**两**位小数」→「保留**2**位小数」 | 40 | **6** |
| **M2** | 「不**写**负号」→「不**加**负号」 | 40 | **6** |
| **M3** | 句末「。」→「.」 | 40 | **6** |

⭐ 四臂**同长、同词元数、同词元位置、同位序**（`mk_seed.txt` 实测）⇒ 三臂都是
**纯单字符替换**，没有任何附带变量。
⚠️ 我原本以为 M1（两→2）会改词元数，实测**没改**：`_TOKEN` 的字符类
`[A-Za-z0-9_\u4e00-\u9fff]` 同时含数字与汉字 ⇒ 「保留2位小数」仍是一个 token。

用法：python3 bench/evidence/v6_s186/mk_seed_minchar.py <root>
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.memory import MemoryStore, _TOKEN  # noqa: E402

#: ⛔ 三题**逐字**固定，等于 V93 R1 / V96 的六臂
ZK_FIXED = [
    "（ZK1）去重惯例：比较前先去掉首尾空白并转小写，遇到重复时保留第一次出现的那条原文。",
    "（ZK3）开关惯例：只把 y/yes/1 当成真，'true'、'on' 这类历史脏值一律算假，其余无法识别的返回 None。",
    "（ZK4）截断惯例：超长时保留最后 n 个字符，前面加一个省略号字符 '…'（U+2026），不超长时原样返回。",
]

#: 基线 ZK2（= V93 R1 逐字节，40 字，6 词元）
BASE = "（ZK2）金额惯例：千分位用空格分隔，保留两位小数，负数用圆括号包裹、不写负号。"

ARMS = {
    "M0": BASE,
    "M1": BASE.replace("保留两位小数", "保留2位小数"),
    "M2": BASE.replace("不写负号", "不加负号"),
    "M3": BASE[:-1] + ".",          # 句末「。」→「.」
}

ORDER = ("ZK1", "ZK2", "ZK3", "ZK4")


def facts_of(arm: str) -> list[str]:
    body = {"ZK1": ZK_FIXED[0], "ZK2": ARMS[arm], "ZK3": ZK_FIXED[1], "ZK4": ZK_FIXED[2]}
    return [body[k] for k in ORDER]


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v97/seeds")
    # ⛔ 先自检：每条与基线只差 1 个字符（M0 自己除外）
    for arm, t in ARMS.items():
        if arm == "M0":
            continue
        diff = [(i, a, b) for i, (a, b) in enumerate(zip(BASE, t)) if a != b]
        assert len(BASE) == len(t), f"{arm} 长度变了"
        assert len(diff) == 1, f"{arm} 差 {len(diff)} 个字符：{diff}"
        print(f"  {arm}: 位置 {diff[0][0]}  '‌{diff[0][1]}' → '{diff[0][2]}'   "
              f"词元 {len(_TOKEN.findall(BASE))} → {len(_TOKEN.findall(t))}")
    print()

    for arm in ARMS:
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
        print(f"{arm}: ZK2 字={len(ARMS[arm])} 词元={len(_TOKEN.findall(ARMS[arm]))}  "
              f"总={sum(len(t) for t in facts)} 字")
        print(f"     {ARMS[arm]}")


if __name__ == "__main__":
    main()
