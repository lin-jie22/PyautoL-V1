#!/usr/bin/env python3
"""V256 补充分析 —— 只读 readings_v256.json，不重新跑装置。

回答三个问题（都是 PREREG 里没写死、但主判据被打空后必须追的）：
  Q1 阶段 2 的 S=11 里，有多少是「同一句 + 不同计数」撑出来的？（预注册的 S 判据写窄了）
  Q2 39 题里有多少格**一命中都没有**（= 结构不可达，拿不到任何回灌）？
  Q3 拿到的「AssertionError 历史均值」与目标题分数，单调相关吗？（Spearman，⛔ 不报 p）
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R = json.loads((HERE / "readings_v256.json").read_text(encoding="utf-8"))

NUM = re.compile(r"出现过 \d+ 次|均值 \d+\.\d+")


def skel(s: str) -> str:
    return NUM.sub("<NUM>", s or "")


print("=" * 70)
print("Q1  阶段 2：S=11 是句子多，还是计数多？")
lines = R["phase2"]["lines"]
sk = sorted(set(skel(x) for x in lines))
print(f"  原始行 S = {len(lines)}")
print(f"  去数值骨架 S_skel = {len(sk)}")
for x in sk:
    print(f"    {x}")
raw_cls = set()
for x in lines:
    m = re.match(r"- 同类题里 `([^`]+)`", x)
    if m:
        raw_cls.add(m.group(1))
print(f"  出现的类别名 = {len(raw_cls)} 个 {sorted(raw_cls)}")
print(f"  ⇒ 结论：S=11 全部由「`其他` 的计数 1~5」撑出，"
      f"骨架只有 {len(sk)} 种（预注册写的 ≤6 判据对的是骨架，我漏了抹数值这一步）")

print("=" * 70)
print("Q2  阶段 1：有没有格一条都命中不了？")
rows1 = {r["task_id"]: r for r in R["phase1"]["rows"]}
zero = [k for k, v in rows1.items() if v["seen"] == 0]
print(f"  rewards_seen==0 的格数 = {len(zero)}/39")
for k in zero:
    print(f"    {k}   rkey={rows1[k]['rkey']}")
seen = sorted(v["seen"] for v in rows1.values())
print(f"  seen 分布（升序）= {seen}")
print(f"  skipped_self 合计 = {sum(v['skipped_self'] for v in rows1.values())}"
      f"   skipped_success 合计 = {sum(v['skipped_success'] for v in rows1.values())}")

print("=" * 70)
print("Q3  「AssertionError 历史均值」与目标题分数的单调关系")
scores = {}
# 分数从 low/high 分组与 class_of_low 里拿不全，直接重读 runs
import collections
RUNS = Path("/tmp/v253_a/bench/runs.jsonl")
best: dict[str, float] = {}
for line in RUNS.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        continue
    for t in (d.get("report") or {}).get("per_task") or []:
        cid, sc = t.get("id"), t.get("score")
        if cid and isinstance(sc, (int, float)):
            best[cid] = max(best.get(cid, -1.0), float(sc))
assert len(best) == 39

MA = re.compile(r"`AssertionError` 出现过 \d+ 次（历史得分均值 ([\d.]+)）")
pairs = []
for tid, r in rows1.items():
    m = MA.search(r["text"] or "")
    if m:
        pairs.append((float(m.group(1)), best[tid], tid))
print(f"  有 AssertionError 均值行的格 = {len(pairs)}/39")
for mu, sc, tid in sorted(pairs):
    print(f"    均值={mu:<6} 分数={sc:<7} {tid}")


def rank(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    rk = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            rk[order[k]] = avg
        i = j + 1
    return rk


if len(pairs) >= 5:
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    rx, ry = rank(xs), rank(ys)
    n = len(pairs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    rho = num / den if den else float("nan")
    print(f"\n  Spearman rho(历史均值, 目标题分数) = {rho:.4f}  (n={n}；⛔ 确定性装置，不报 p)")
    print("  读法：rho>0 ⇒ 历史失败的均值越高，这题越可能拿高分（= 均值是难度先验）")
    # 分组均值
    by = collections.defaultdict(list)
    for mu, sc, tid in pairs:
        by["low13" if sc < 1.0 else "high26"].append(mu)
    for g, v in by.items():
        print(f"    {g}: n={len(v)} 均值线={sorted(v)}")
