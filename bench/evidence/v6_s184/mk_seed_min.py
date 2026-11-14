#!/usr/bin/env python3
"""V95 三臂种子库：**极简规则** —— 写端的第一条纪律是短。

依据 V94 §162.2：C3 只把块从 211 字拉到 376 字（语义完全没变），g7d3 就从 0.4 掉到 0.0。
⇒ 若「短」本身有利，极简规则应当 ≥ 0.85（R1 的历史值）。

S1 = 极简规则（每条 ≤40 字，信息不减）
S3 = 极简规则 + 中性填充到**原规则的 1.75 倍**（≈353 字）⇒ 三点长度曲线的上端
S2 = 原规则 = V93 的 R1（历史值 0.85，本轮回测十次一致，不重跑）

用法：python3 bench/evidence/v6_s184/mk_seed_min.py <root>
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.memory import MemoryStore  # noqa: E402

#: 原规则（= V85 B / V93 R1），只用来定 S3 的目标长度
RULE = [
    "（ZK1）去重惯例：比较前先去掉首尾空白并转小写，遇到重复时保留第一次出现的那条原文。",
    "（ZK2）金额惯例：千分位用空格分隔，保留两位小数，负数用圆括号包裹、不写负号。",
    "（ZK3）开关惯例：只把 y/yes/1 当成真，'true'、'on' 这类历史脏值一律算假，其余无法识别的返回 None。",
    "（ZK4）截断惯例：超长时保留最后 n 个字符，前面加一个省略号字符 '…'（U+2026），不超长时原样返回。",
]

#: 极简规则 —— 逐条对照原规则，**信息项一个不少**
MIN = [
    "（ZK1）去重惯例：比较前 strip 并转小写，重复时保留首次出现的原文。",
    "（ZK2）金额惯例：千分位用空格，两位小数，负数圆括号且不带负号。",
    "（ZK3）开关惯例：只有 y/yes/1 为真，'true'/'on' 等为假，其余 None。",
    "（ZK4）截断惯例：超长时前置 '…' 并保留末尾 n 个字符，否则原样。",
]

#: 中性填充（不含题目判别词）
FILLER = "本条惯例长期有效，遇到同类问题一律照此处理。"


def pad_to(s: str, n: int) -> str:
    out = s
    while len(out) < n:
        out += FILLER
    return out[:n]


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/v95/seeds")
    s1 = list(MIN)
    # S3 = 极简规则 + 填充到**原规则的 1.75 倍**（≈353 字），
    # 与 S1（156）/ S2（R1, 211, 历史值）构成三点长度曲线。
    s3 = [pad_to(m, int(len(r) * 1.75)) for m, r in zip(MIN, RULE)]
    arms = {"S1": s1, "S3": s3}
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
        print(f"{tag}: 总 {sum(len(t) for t in facts)} 字")
        for t in facts:
            flag = "⛔>120" if len(t) > 120 else "✓"
            print(f"   [{len(t):3d} {flag}] {t}")
        print()
    print("信息项对照（极简 vs 原）：")
    for i, (mn, ru) in enumerate(zip(MIN, RULE), 1):
        print(f"  ZK{i}: {len(mn)} / {len(ru)} 字  = {len(mn)/len(ru):.0%}")
    print(f"合计: {sum(len(x) for x in MIN)} / {sum(len(x) for x in RULE)} 字")


if __name__ == "__main__":
    main()
