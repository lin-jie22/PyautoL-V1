#!/usr/bin/env python3
"""V93 四臂种子库。

R0 空 / R1 规则（= V85 B 臂逐字相同）/ R2 示例（**从题面 tests 机械取前 3 条**）/
R3 示例·安慰剂（同形式、内容取模型的自然默认）。

⭐ 关键：R2 的文本**不经人手写、不经模型**，由 `ast` 从 `bench/heldout/g7j_n4_heldout.jsonl`
   的 `tests` 字段解析出「输入 → 期望输出」，再渲染成一句话。这是 L4 写端的原型：
   失败 → 期望值 → 机械提取 → 写库。

用法：python3 bench/evidence/v6_s182/mk_seed_examples.py <root>
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.memory import MemoryStore  # noqa: E402

SUITE = ROOT / "bench/heldout/g7j_n4_heldout.jsonl"

#: R1：与 V85 B 臂**逐字相同**（换一个字两轮就不可比）
ZK_RULES = [
    "（ZK1）去重惯例：比较前先去掉首尾空白并转小写，遇到重复时保留第一次出现的那条原文。",
    "（ZK2）金额惯例：千分位用空格分隔，保留两位小数，负数用圆括号包裹、不写负号。",
    "（ZK3）开关惯例：只把 y/yes/1 当成真，'true'、'on' 这类历史脏值一律算假，其余无法识别的返回 None。",
    "（ZK4）截断惯例：超长时保留最后 n 个字符，前面加一个省略号字符 '…'（U+2026），不超长时原样返回。",
]

#: R3：同形式、同标号，内容是**模型不靠记忆也会这么写**的自然默认（答错）
ZK_PLACEBO = [
    "（ZK1）去重惯例：['A','a'] 得 ['A','a']；['b',' a ','A'] 得 ['b',' a ','A']；['x','y','X','y '] 得 ['x','y','X','y']。",
    "（ZK2）金额惯例：1234.5 得 '1,234.50'；-1234.5 得 '-1,234.50'；0 得 '0.00'。",
    "（ZK3）开关惯例：'y' 得 True；'true' 得 True；'TRUE' 得 True。",
    "（ZK4）截断惯例：('abc',5) 得 'abc'；('abcdef',3) 得 'abc...'；('0123456789',4) 得 '0123...'。",
]

#: 每题取前几条断言当示例
N_EXAMPLES = 3

#: 标号 ↔ 题（题面里写死「按记忆里记着的（ZK1）…（ZK4）惯例处理」）
TAG = {"g7d2_dedupe_keep": "ZK1", "g7d3_fmt_amount": "ZK2",
       "g7e1_parse_flag": "ZK3", "g7e2_truncate_tail": "ZK4"}
LABEL = {"ZK1": "去重惯例", "ZK2": "金额惯例", "ZK3": "开关惯例", "ZK4": "截断惯例"}


def parse_assert(line: str) -> tuple[str, list, object]:
    """`assert f(a, b) == want` / `assert f(a) is True` → (fname, args, want)."""
    node = ast.parse(line.strip()).body[0]
    cmp = node.test                       # ast.Compare
    call = cmp.left                       # ast.Call
    args = [ast.literal_eval(a) for a in call.args]
    want = ast.literal_eval(cmp.comparators[0])
    return call.func.id, args, want


def render(args: list, want: object) -> str:
    """`['A','a'] 得 ['A']` / `('abc',5) 得 'abc'`。"""
    parts = [repr(a).replace(", ", ",") for a in args]
    left = parts[0] if len(parts) == 1 else "(" + ",".join(parts) + ")"
    return f"{left} 得 {repr(want).replace(', ', ',')}"


def build_examples() -> list[str]:
    """从题面 tests 机械取前 N_EXAMPLES 条，按 ZK1..ZK4 顺序拼成四条事实。"""
    import json
    rows = [json.loads(x) for x in SUITE.read_text(encoding="utf-8").splitlines() if x.strip()]
    out = []
    for r in rows:
        tag = TAG[r["id"]]
        pairs = [render(*parse_assert(t)[1:]) for t in r["tests"][:N_EXAMPLES]]
        out.append(f"（{tag}）{LABEL[tag]}：" + "；".join(pairs) + "。")
    return out


ARMS = {"R0": [], "R1": ZK_RULES, "R2": None, "R3": ZK_PLACEBO}


def build(root: Path) -> None:
    examples = build_examples()
    ARMS["R2"] = examples
    for tag, facts in ARMS.items():
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
            print(f"   [{len(t):3d} 字] {t}")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/v93")
    build(out)
    print(f"\n库已建在 {out}")
