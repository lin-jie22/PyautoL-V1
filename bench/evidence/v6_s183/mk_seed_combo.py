#!/usr/bin/env python3
"""V94 三臂种子库：规则 + 示例的组合形态。

C1 = 规则句 + 「例：」+ **前 2 条**正确断言（示例与 V93 的 R2 逐字一致）
C2 = 规则句 + 「例：」+ **前 2 条错**断言（自然默认，同长度）
C3 = 规则句 + **等长中性填充**（长度逐条对齐 C1，只去掉「示例」这个成分）

⭐ 为什么每条只给 2 条示例：`f.text[:120]` 是硬截断。V93 的 R2 用 3 条时 ZK1=89/ZK4=77
   尚可，但拼上规则句后 ZK1 会到 124、ZK4 到 125 ⇒ 截断会切掉最后一条示例，污染对照。
   `probe_block.py` 会逐条打印实际块字数，跑前可见。

用法：python3 bench/evidence/v6_s183/mk_seed_combo.py <root>
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.memory import MemoryStore  # noqa: E402

#: 与 V85 B / V93 R1 **逐字相同**
RULE = [
    "（ZK1）去重惯例：比较前先去掉首尾空白并转小写，遇到重复时保留第一次出现的那条原文。",
    "（ZK2）金额惯例：千分位用空格分隔，保留两位小数，负数用圆括号包裹、不写负号。",
    "（ZK3）开关惯例：只把 y/yes/1 当成真，'true'、'on' 这类历史脏值一律算假，其余无法识别的返回 None。",
    "（ZK4）截断惯例：超长时保留最后 n 个字符，前面加一个省略号字符 '…'（U+2026），不超长时原样返回。",
]

#: 前 2 条**正确**示例（与 V93 R2 的前 2 条逐字一致）
EX_OK = [
    "['A','a'] 得 ['A']；['b',' a ','A'] 得 ['b',' a ']。",
    "1234.5 得 '1 234.50'；-1234.5 得 '(1 234.50)'。",
    "'y' 得 True；'true' 得 False。",
    "('abc',5) 得 'abc'；('abcdef',3) 得 '…def'。",
]

#: 前 2 条**错**示例（模型的自然默认，长度与 EX_OK 基本相同）
EX_BAD = [
    "['A','a'] 得 ['A','a']；['b',' a ','A'] 得 ['b',' a ','A']。",
    "1234.5 得 '1,234.50'；-1234.5 得 '-1,234.50'。",
    "'y' 得 True；'true' 得 True。",
    "('abc',5) 得 'abc'；('abcdef',3) 得 'abc...'。",
]

#: 中性填充（不含任何题目判别词）；只用来补长度
FILLER = "本条惯例长期有效，遇到同类问题一律照此处理。"


def head_body(rule: str) -> tuple[str, str]:
    i = rule.index("：") + 1
    return rule[:i], rule[i:]


def pad_to(n: int) -> str:
    """补到恰好 n 个字的中性填充。"""
    s = ""
    while len(s) < n:
        s += FILLER
    return s[:n]


def build() -> dict[str, list[str]]:
    c1, c2, c3 = [], [], []
    for i, rule in enumerate(RULE):
        head, body = head_body(rule)
        a = head + body + "例：" + EX_OK[i]
        b = head + body + "例：" + EX_BAD[i]
        need = len(a) - len(rule)
        c = rule + pad_to(need)
        c1.append(a)
        c2.append(b)
        c3.append(c)
    return {"C1": c1, "C2": c2, "C3": c3}


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/v94/seeds")
    arms = build()
    for tag, facts in arms.items():
        d = root / tag
        d.mkdir(parents=True, exist_ok=True)
        db = d / "memory.db"
        if db.exists():
            db.unlink()
        m = MemoryStore(db)
        for t in facts:
            m.commit_fact(t, source="abstract", confidence=0.6)
        m.close()
        print(f"{tag}: {len(facts)} 条")
        for t in facts:
            flag = "⛔ 超 120 会被截断" if len(t) > 120 else "✓"
            print(f"   [{len(t):3d} 字 {flag}] {t}")
        print()
    # 长度对照必须成立，否则 C1 vs C3 分不开
    for i in range(4):
        assert len(arms["C1"][i]) == len(arms["C3"][i]), f"ZK{i+1} C1/C3 不等长"
    print("✓ C1 与 C3 逐条等长")


if __name__ == "__main__":
    main()
