#!/usr/bin/env python3
"""V213 的**事后**探针（⛔ 不是判据）。

判据只有 PREREG_v213.md 里那三条，已经全部读完。本脚本只回答两个**读数一致性**问题：
1. P3 用 `x = x1+x4` 做序列，但 V212 的 κ=0.625 是**单格 `t04[0]`** 上的 ——
   同一批 30 次运行里，`t04[0]` 有没有相邻聚簇？（若没有，V212 的 0.85 一致率更可能是 n=20 的偶然）
2. 预注册自踩预案 4 要求报前后半差；这里把它做成检验（趋势 vs 聚簇），并分开报两题。
"""
from __future__ import annotations

import json
import random
import statistics as st
from pathlib import Path

D = Path(__file__).resolve().parent
BASE = int((D / "start_runs.txt").read_text().strip())
N = 30

runs = [json.loads(l) for l in open("bench_out/runs.jsonl", encoding="utf-8") if l.strip()][BASE:]
rows = []
for r in runs:
    per = {t["id"]: t for t in r["report"]["per_task"]}
    v1 = per["t01_parse_duration"]["vec"]
    v4 = per["t04_merge_ranges"]["vec"]
    rows.append({"x1": sum(map(bool, v1)), "x4": sum(map(bool, v4)),
                 "p4a": int(bool(v4[0])), "v1": "".join("1" if b else "0" for b in v1),
                 "v4": "".join("1" if b else "0" for b in v4)})

xs = [r["x1"] + r["x4"] for r in rows]
x1 = [r["x1"] for r in rows]
x4 = [r["x4"] for r in rows]
p4 = [r["p4a"] for r in rows]


def perm_adj_lower(seq, trials=100_000, seed=20261120):
    """相邻差均值 S 的下尾 p（与原预注册同一个统计量）。"""
    def S(v):
        return sum(abs(v[i] - v[i + 1]) for i in range(len(v) - 1)) / (len(v) - 1)
    obs = S(seq)
    rng = random.Random(seed)
    v = seq[:]
    hit = 0
    for _ in range(trials):
        rng.shuffle(v)
        if S(v) <= obs + 1e-12:
            hit += 1
    return obs, (hit + 1) / (trials + 1)


def perm_half(seq, trials=100_000, seed=20261121):
    """前半 vs 后半均值差的双侧置换 p（打乱顺序）。"""
    obs = abs(st.mean(seq[15:]) - st.mean(seq[:15]))
    rng = random.Random(seed)
    v = seq[:]
    hit = 0
    for _ in range(trials):
        rng.shuffle(v)
        if abs(st.mean(v[15:]) - st.mean(v[:15])) >= obs - 1e-12:
            hit += 1
    return obs, (hit + 1) / (trials + 1)


def lag1(seq):
    if st.pstdev(seq) == 0:
        return float("nan")
    a, b = seq[:-1], seq[1:]
    return st.correlation(a, b)


print("=== ① 单格 t04[0]（n=30，正例 %d/30）===" % sum(p4))
print("  序列:", "".join(str(v) for v in p4))
agree = sum(1 for i in range(29) if p4[i] == p4[i + 1])
print(f"  相邻一致 {agree}/29 = {agree/29:.3f}；机会一致 = "
      f"{sum(p4)/30**2 + (1-sum(p4)/30)**2:.3f}")
S4, p4v = perm_adj_lower(p4)
print(f"  S_t04[0] = {S4:.4f}  置换下尾 p = {p4v:.4f}  "
      f"⇒ {'有相邻聚簇' if p4v < 0.05 else '无相邻聚簇'}")
print(f"  lag-1 自相关（Pearson，二值）= {lag1(p4):+.4f}")

print("\n=== ② 三个序列分开看 ===")
for nm, seq in (("x=x1+x4", xs), ("x1", x1), ("x4", x4)):
    S, p = perm_adj_lower(seq)
    print(f"  {nm:8s} mean={st.mean(seq):5.3f} sd={st.pstdev(seq):5.3f} "
          f"S={S:6.3f} p_adj={p:.4f} lag1={lag1(seq):+.3f}")

print("\n=== ③ 前后半差（趋势检验，非聚簇）===")
for nm, seq in (("x=x1+x4", xs), ("x1", x1), ("x4", x4), ("t04[0]", p4)):
    d, p = perm_half(seq)
    print(f"  {nm:8s} 前15={st.mean(seq[:15]):5.3f} 后15={st.mean(seq[15:]):5.3f} "
          f"|Δ|={d:5.3f} 置换双侧 p={p:.4f}")

print("\n=== ④ t01 / t04 逐断言通过率（30 次）===")
for k in range(6):
    print(f"  t01[{k}] {sum(1 for r in rows if r['v1'][k] == '1'):2d}/30   "
          f"t04[{k}] {sum(1 for r in rows if r['v4'][k] == '1'):2d}/30")

print("\n=== ⑤ 形状去重（30 次里有几种答案形状）===")
print("  t01:", {v: [r['v1'] for r in rows].count(v) for v in sorted({r['v1'] for r in rows})})
print("  t04:", {v: [r['v4'] for r in rows].count(v) for v in sorted({r['v4'] for r in rows})})

json.dump({"t04_first_adj_p": p4v, "adj_agree": agree,
           "series": {"x": xs, "x1": x1, "x4": x4, "p4": p4}},
          open(D / "explore_v213.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
