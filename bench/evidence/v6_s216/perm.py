#!/usr/bin/env python3
"""V127 跑后诊断（⛔ 未押，不参与判决）：为什么哈希序不是干净的零假设。

两件事：
  §A 用 1000 次随机重排造真零分布，看 ρ_gated=0.751 落在哪
  §B 哈希序 ρ=0.367 的成分：是不是「g7e1 4 个高值恰好散在中间」的巧合
"""
from __future__ import annotations
import json, pathlib, random, statistics

HERE = pathlib.Path(__file__).resolve().parent
d = json.loads((HERE / "pred.json").read_text(encoding="utf-8"))
gated = [r for r in d["rows"] if r["s0"] < 1.0]


def _rank(v):
    s = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v); i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and v[s[j + 1]] == v[s[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for t in range(i, j + 1):
            r[s[t]] = avg
        i = j + 1
    return r


def sp(xs, ys):
    rx, ry = _rank(xs), _rank(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


ys = [r["saved"] for r in gated]
xs = [r["n_cand"] for r in gated]
rho = sp(xs, ys)
rng = random.Random(0)
nul = []
for _ in range(1000):
    perm = xs[:]; rng.shuffle(perm)
    nul.append(sp(perm, ys))
nul.sort()
ge = sum(1 for v in nul if v >= rho)
print("## §A 重排零分布（1000 次，n=15）")
print(f"  ρ(n_cand, saved) = {rho:+.3f}")
print(f"  零分布：均值 {statistics.mean(nul):+.3f}｜p5 {nul[50]:+.3f}｜p95 {nul[950]:+.3f}"
      f"｜max {nul[-1]:+.3f}")
print(f"  超过 {rho:+.3f} 的重排数 = {ge}/1000 ⇒ 经验 p = {ge/1000:.3f}")

print("\n## §B 哈希序 ρ=0.367 的成分")
alpha = sorted(gated, key=lambda r: r["h"])
print("  按哈希排序后的 saved：")
for i, r in enumerate(alpha):
    print(f"    {i+1:2d} {r['h'][:8]} n_cand={r['n_cand']:3d} saved={r['saved']:3d}")
rho_a = sp([float(int(r["h"][:6], 16)) for r in gated], ys)
print(f"  ρ_alpha = {rho_a:+.3f}")
top4 = {r["h"] for r in sorted(gated, key=lambda r: -r["saved"])[:4]}
a4 = {r["h"] for r in alpha[:4]}
print(f"  哈希序 top4 与真 top4 交集 = {len(top4 & a4)}/4 ⇒ {sorted(a4)}")
