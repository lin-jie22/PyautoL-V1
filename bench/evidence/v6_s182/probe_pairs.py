#!/usr/bin/env python3
"""V93 前置量测：能不能从 `per_task.why` 里**机械**抽出「输入 → 期望输出」对？

为什么这条路值得先量（纪律 3：先量 B 存在过没有）：

V87 让模型「从满分实现里提炼惯例」，探针 4/4 回答 NONE —— 因为实现与题面高度重合，
模型没有参照系。但**失败断言不一样**：`assert f(1234.5) == '1 234.50'` 里同时有
输入、期望输出、以及「这条我错了」这个事实。这是外部真值，不是模型自省的产物。

如果这些对能被机械抽出来（不需要模型参与），那 L4 的写端就可以**完全不依赖
模型的判断力** —— 绕开 §4.4 那堵「同源自评」的墙。

本脚本零模型调用、零内核改动、只读文本证据。
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[3]
SRC = ROOT / "bench/evidence/v6_s180/arms/L0/bench/runs.jsonl"

#: `assert fmt_amount(1234.5) == '1 234.50' ⇒ AssertionError`
#: 只认「单参数 + 字面量期望」这一种；多参数/复杂表达式的解析留到后面再说。
RE_ONE = re.compile(
    r"assert\s+(\w+)\(([^()]*)\)\s*==\s*('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|-?\d+(?:\.\d+)?|True|False|None)"
)
#: 失败段落的分隔符
SPLIT = " | "


def parse_why(why: str) -> list[tuple[str, str, str]]:
    out = []
    for chunk in why.split(SPLIT):
        for m in RE_ONE.finditer(chunk):
            fn, args, want = m.group(1), m.group(2).strip(), m.group(3)
            out.append((fn, args, want))
    return out


def main() -> None:
    d = json.loads(SRC.read_text(encoding="utf-8").splitlines()[0])
    per = (d.get("report") or {}).get("per_task") or []
    total = 0
    for t in per:
        pairs = parse_why(t.get("why") or "")
        total += len(pairs)
        print(f"== {t['id']}  score={t['score']}  抽出 {len(pairs)} 对")
        for fn, args, want in pairs:
            print(f"   {fn}({args}) -> {want}")
        if not pairs and t["score"] < 1.0:
            print(f"   ⚠️ 失败但抽不出：{(t['why'] or '')[:160]}")
        print()
    print(f"# 合计抽出 {total} 对")
    print("# 判读：若 ≥3 对且可解析，B 段的第一刀就是「用这些对当知识」，"
          "而不是「让模型写惯例」。")


if __name__ == "__main__":
    main()
