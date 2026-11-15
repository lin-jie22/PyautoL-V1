#!/usr/bin/env python3
"""V132 跑后诊断（⛔ 未押）：R²=0.808 到底由几个点撑着。

§A 留一法：每次去掉一个点重拟合，看 R² 掉多少 —— 找出「撑起 R²」的那个点
§B 去掉 200ebf6e（saved=0 但 n_cand=95）之后的 R² 与三箱
§C MAG 为什么只剩 1/25：逐实现看「限额 vs 实际需要」
"""
from __future__ import annotations
import json, pathlib, statistics

HERE = pathlib.Path(__file__).resolve().parent
d = json.loads((HERE / "calib.json").read_text(encoding="utf-8"))
rows = [r for r in d["rows"] if r["s0"] < 1.0]


def ols(xs, ys):
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    a = sxy / sxx if sxx else 0.0
    b = my - a * mx
    resid = [y - (a * x + b) for x, y in zip(xs, ys)]
    ss_res = sum(r * r for r in resid)
    ss_tot = sum((y - my) ** 2 for y in ys)
    return a, b, (1 - ss_res / ss_tot if ss_tot else 0.0), resid


xs = [r["n_cand"] for r in rows]
ys = [r["saved"] for r in rows]
a, b, r2, resid = ols(xs, ys)
print("## §A 留一法：去掉一个点后的 R²（Δ 越大 = 这个点越撑）")
full = r2
out = []
for i, r in enumerate(rows):
    kx = [x for j, x in enumerate(xs) if j != i]
    ky = [y for j, y in enumerate(ys) if j != i]
    out.append((r["tid"][:6], r["h"], r["n_cand"], r["saved"], ols(kx, ky)[2]))
for t, h, nc, sv, rr in sorted(out, key=lambda x: x[4]):
    print(f"  去掉 {t} {h} n_cand={nc:3d} saved={sv:4d} → R² {rr:.3f}（Δ {rr-full:+.3f}）")

print("\n## §B 去掉 200ebf6e（saved=0 但 n_cand=95）之后")
keep = [r for r in rows if r["h"] != "200ebf6e"]
kx = [r["n_cand"] for r in keep]
ky = [r["saved"] for r in keep]
a2, b2, r22, res2 = ols(kx, ky)
print(f"  n = {len(keep)}｜y = {a2:.2f}·n_cand {b2:+.1f}｜R² = {r22:.3f}（全量 {full:.3f}）")
srt = sorted(keep, key=lambda r: r["n_cand"])
boxes = [srt[i*5:(i+1)*5] for i in range(len(srt)//5)]
for i, bx in enumerate(boxes):
    print(f"  箱{i+1} n_cand {bx[0]['n_cand']:3d}~{bx[-1]['n_cand']:3d}｜实测均值 "
          f"{statistics.mean(r['saved'] for r in bx):6.1f}｜"
          f"{[r['saved'] for r in bx]}")

print("\n## §C MAG 为什么只剩 1/25（限额 vs 实际需要）")
pred = d["pred"]
for r in sorted(rows, key=lambda r: -r["n_cand"]):
    lim = max(1, round(pred.get(r["h"], 1)))
    print(f"  {r['tid'][:6]} {r['h']} n_cand={r['n_cand']:3d} saved={r['saved']:4d} "
          f"pred={pred.get(r['h'],0):7.1f} lim={lim:4d}")
print("\n  ⇒ 限额超过 saved 的（给了也用不完）与限额不足的（做不完）各占多少，见上表对齐看")
