#!/usr/bin/env python3
"""V215b 机制普查（免费，读已有 state，不跑模型）：交出去的附言里到底有什么。

⛔ 口径：只看 `state_b_*.jsonl` 里 `[外部评审]` 块的字面内容；
   这些块正是 `mw/core.py:handoff_text()` 会原样附到题面后的那几段（`parts[1:]`）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

D = Path(__file__).resolve().parent


def blocks_of(state: str) -> list[str]:
    """按 `_state()` 的块切分：以 `[xxx]` 行首块名开头，切到下一个块名。"""
    idx = [m.start() for m in re.finditer(r"(?m)^\[[^\]]+\]", state)]
    if not idx:
        return []
    segs = [state[a:b] for a, b in zip(idx, idx[1:] + [len(state)])]
    return [s for s in segs if s.startswith(("[外部评审]", "[语义记忆]", "[经验]"))]


for arm in ("off", "blocks"):
    p = D / f"state_b_{arm}.jsonl"
    recs = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    hs = [b for r in recs for b in blocks_of(r["state"])]
    print(f"\n=== {arm}：{len(recs)} 条 state 记录，累计块 {len(hs)} 段")
    if not hs:
        continue
    lens = sorted(len(b) for b in hs)
    ast = sum(1 for b in hs if "assert " in b)
    exc = sum(1 for b in hs if re.search(r"(Error|Exception|Traceback)", b))
    other = sum(1 for b in hs if re.search(r"\[coding_small/t0[0-9]", b))
    print(f"   长度 中位 {lens[len(lens)//2]} 字（min {lens[0]} / max {lens[-1]}）")
    print(f"   含 `assert ` {ast}/{len(hs)}   含异常名 {exc}/{len(hs)}   含别题条目 {other}/{len(hs)}")
    print(f"   去重后不同文本 {len(set(hs))} 种")
    print(f"   含目标题名 t01 {sum(1 for b in hs if 't01_parse_duration' in b)}/{len(hs)}")

# 逐块配对：t01 三格
base = int((D / "start_runs_b.txt").read_text().strip())
rows = [json.loads(l) for l in open("bench_out/runs.jsonl", encoding="utf-8") if l.strip()][base:]
order = [l.rstrip("\n").split("\t") for l in open(D / "order_b.txt", encoding="utf-8") if l.strip()]
cell: dict[tuple[str, str], dict] = {}
for o, r in zip(order, rows):
    per = {t["id"]: t for t in r["report"]["per_task"]}
    cell[(o[0], o[2])] = {tid: list(per[tid]["vec"]) for tid in per}
print("\n=== 逐块配对（t01 第 0/2/3 格）")
print(" 块 | off 0 2 3 | blocks 0 2 3")
flip = 0
for k in sorted({a for a, _ in cell}, key=int):
    a = cell[(k, "off")]["t01_parse_duration"]
    b = cell[(k, "blocks")]["t01_parse_duration"]
    if a[0] != b[0] or a[2] != b[2] or a[3] != b[3]:
        flip += 1
    print(f" {k:>2} |  {a[0]} {a[2]} {a[3]}      |   {b[0]} {b[2]} {b[3]}")
print(f" 三格中任一格不同的块：{flip}/20")
