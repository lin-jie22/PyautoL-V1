#!/usr/bin/env python3
"""V85 三臂种子库 —— A 空 / B 正知识（ZK 原文）/ C 等长安慰剂（同形状、常识内容）。

⛔ B 的四条与 `bench/evidence/knowledge_s39/ev_n0/seed_H.db` **逐字相同**（V83 已在该库上
   实测 recall 4/4），换一个字两轮就不可比。
⛔ C 的四条：同前缀（`（ZKk）…惯例：`）、同量级字数、内容取「模型本来就会那么写」的常识
   ⇒ 白送同样多的注意力预算，但**不含**本题需要的暗规则。
用法：python3 bench/evidence/v6_s174/make_seed_v85.py /tmp/v85
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.memory import MemoryStore  # noqa: E402

ZK = [
    "（ZK1）去重惯例：比较前先去掉首尾空白并转小写，遇到重复时保留第一次出现的那条原文。",
    "（ZK2）金额惯例：千分位用空格分隔，保留两位小数，负数用圆括号包裹、不写负号。",
    "（ZK3）开关惯例：只把 y/yes/1 当成真，'true'、'on' 这类历史脏值一律算假，其余无法识别的返回 None。",
    "（ZK4）截断惯例：超长时保留最后 n 个字符，前面加一个省略号字符 '…'（U+2026），不超长时原样返回。",
]
# 等长安慰剂：同标号、同句法、同量级字数；内容是**常识默认**（模型不靠记忆也会这么写）
PLACEBO = [
    "（ZK1）去重惯例：按原样逐项比较、不改变大小写，遇到重复时保留最后一次出现的那条原文。",
    "（ZK2）金额惯例：千分位用逗号分隔，保留两位小数，负数在数字前面直接加一个负号。",
    "（ZK3）开关惯例：'true'、'on'、'yes' 都算真，'false'、'off'、'no' 都算假，其余一律返回 None。",
    "（ZK4）截断惯例：超长时保留开头 n 个字符，后面加三个英文句点 '...'，不超长时按原样返回整个字符串。",
]
ARMS = {"A": [], "B": ZK, "C": PLACEBO}


def build(root: Path) -> None:
    for tag, facts in ARMS.items():
        d = root / tag
        d.mkdir(parents=True, exist_ok=True)
        db = d / "memory.db"
        if db.exists():
            db.unlink()
        m = MemoryStore(db)
        for t in facts:
            m.commit_fact(t, source="abstract", confidence=0.6)
        st = m.stats()
        m.close()
        print(f"{tag}: facts={st['facts']} chars={sum(len(x) for x in facts)}")
        assert st["facts"] == len(facts), (tag, st)


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v85"))
