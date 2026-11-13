#!/usr/bin/env python3
"""V87 串味量化 —— 每份实现里出现了多少个「别题」的标志 token。

背景（V87 实测）：删掉一条 ZK 后，模型会把**别的题的惯例**串进本题实现
（M4 的 truncate_tail 里出现 `startswith(('y','yes','1'))`）。
这份脚本把它变成可复算的数：串味指数 = 本题实现里命中的「别题标志」个数。

标志（每条只属于一道题，取自该题的正知识 ZK 原文的字面物）：
  ZK1 去重   → strip( 与 lower(
  ZK2 金额   → replace(',', ) 或 {: 格式串（千分位相关）
  ZK3 开关   → 'yes' / 'y' 真值表
  ZK4 截断   → '…' 与 [-n:]

用法：python3 bench/evidence/v6_s176/crosscontam.py <V87 根目录> [对照库…]
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]

#: 判定「某题的标志出现在代码里」的正则。顺序与 TASKS 对齐。
MARK = {
    "g7d2_dedupe_keep": [r"\.strip\(", r"\.lower\("],
    "g7d3_fmt_amount": [r"\{:,", r"replace\(\s*','", r"\)\s*\+\s*['\"]\)"],
    "g7e1_parse_flag": [r"['\"]yes['\"]", r"['\"]y['\"]"],
    "g7e2_truncate_tail": [r"…", r"\[\s*-n\s*:\s*\]|\[\s*-\(?\s*n"],
}


def finishes(db: Path) -> list[str]:
    tmp = Path(tempfile.mkdtemp()) / "s.db"
    shutil.copy(db, tmp)                          # ⛔ 先 copy 再开（纪律 31）
    c = sqlite3.connect(str(tmp))
    c.row_factory = sqlite3.Row
    rows = [r["content"] for r in c.execute(
        "SELECT content FROM episodes WHERE kind='finish' ORDER BY id")]
    c.close()
    return [r.split("```python")[-1].split("```")[0] if "```" in r else r for r in rows]


#: ⚠️ 标志的**唯一性**不是自动成立的：`strip().lower()` 既属于 ZK1，
#: 又出现在 g7e1 的题面里（「入参大小写不敏感、首尾空白忽略」）。
#: ⇒ 在 g7e1 的实现里看到 strip/lower **不算串味**，那是题面要求。
#: 这张表是**白名单**：题 → 出现在本题却不算串味的别题标志。
ALLOWED = {"g7e1_parse_flag": {"g7d2_dedupe_keep"}}


def score(code: str) -> tuple[list[str], int]:
    """返回（命中的题集合, 串味指数=命中别题的标志数）。"""
    owner = None
    hits = []
    for tid, pats in MARK.items():
        if any(re.search(p, code) for p in pats):
            hits.append(tid)
    return hits, len(hits)


def main() -> None:
    root = Path(sys.argv[1])
    arms = [p.name for p in sorted(root.iterdir())
            if p.is_dir() and (p / "memory_after.db").exists()]
    print("| 臂 | 题 | 命中标志的题 | 串味指数 |")
    print("|---|---|---|---|")
    total = {}
    for a in arms:
        fs = finishes(root / a / "memory_after.db")
        for i, tid in enumerate(TASKS):
            if i >= len(fs):
                continue
            hits, d = score(fs[i])
            others = [h for h in hits
                      if h != tid and h not in ALLOWED.get(tid, set())]
            total[a] = total.get(a, 0) + len(others)
            print(f"| {a} | {tid[:12]} | {[h[:6] for h in hits]} | **{len(others)}**"
                  + (f"（串入 {[o[:6] for o in others]}）" if others else "") + " |")
    print("\n| 臂 | 串味总数 |")
    print("|---|---|")
    for a, v in total.items():
        print(f"| {a} | **{v}** |")


if __name__ == "__main__":
    main()
