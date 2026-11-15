#!/usr/bin/env python3
"""V133：把预测目标从「收益」换成「成本」，看它能不能用来分配预算。

与 V132 唯一差别：限额预测的是 `cold_n_eval`（要花多少），不是 `saved`（省多少）。
零模型、零网络、零子进程。
"""
from __future__ import annotations

import ast
import json
import pathlib
import random
import statistics
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for d in ("v6_s205", "v6_s208", "v6_s209", "v6_s210", "v6_s211", "v6_s198", "v6_s217", "v6_s221"):
    sys.path.insert(0, str(EV / d))
import search as S            # noqa: E402
import idiom_transfer as I     # noqa: E402
import v119 as V              # noqa: E402
import v120 as W              # noqa: E402
import v121 as M              # noqa: E402
import v122 as B              # noqa: E402

TIDS = V.TIDS
N_PERM = 30
V132_MAG = 11          # 跑前写死：V132/v6_s221 calib.json 的 MAG.to1


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


def main() -> None:
    corpus = I.harvest()
    raw = json.loads((EV / "v6_s210" / "search.json").read_text(encoding="utf-8"))
    cold, warm = raw["cold"], raw["warm"]

    rows = []
    for tid in TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                continue
            key = f"{tid}::{h}"
            Sx, _ = S.wordset(tid, corpus[tid][h], True)
            chains = V.harvest_chains(corpus, tid, h)
            exprs = W.harvest_exprs(corpus, tid, h)
            n_cand = len(M.gen(tid, corpus[tid][h], Sx, chains, exprs))     # ⛔ 不判分
            rows.append(dict(tid=tid, h=h, Sx=Sx, chains=chains, exprs=exprs,
                             n_cand=n_cand, s0=cold[key]["s0"],
                             cost=cold[key]["n_eval"],
                             saved=cold[key]["n_eval"] - warm[key]["n_eval"]))

    gated = [r for r in rows if r["s0"] < 1.0]
    n_full = len(rows) - len(gated)
    print("## §0 分母（纪律 46：两个量分列）")
    print(f"  n_total = {len(rows)}｜n_baseline_full = {n_full}｜n_gated = {len(gated)}")
    print(f"  Σcost(gated) = {sum(r['cost'] for r in gated)}｜Σsaved(gated) = {sum(r['saved'] for r in gated)}")

    xs = [r["n_cand"] for r in gated]
    costs = [r["cost"] for r in gated]
    a, b, r2, resid = ols(xs, costs)
    print("\n## §1 成本拟合（目标 = cold_n_eval，不是 saved）")
    print(f"  y = {a:.3f}·n_cand {b:+.1f}｜**R² = {r2:.3f}**｜残差中位绝对值 "
          f"{statistics.median([abs(v) for v in resid]):.1f}｜cost 中位 {statistics.median(costs):.0f}")
    for r, e in sorted(zip(gated, resid), key=lambda t: -abs(t[1]))[:5]:
        print(f"    {r['tid'][:6]} {r['h']} n_cand={r['n_cand']:3d} cost={r['cost']:4d}"
              f" pred={a*r['n_cand']+b:7.1f} 残差 {e:+7.1f}")

    pred = {r["h"]: max(1.0, a * r["n_cand"] + b) for r in gated}
    order = sorted(gated, key=lambda r: r["n_cand"])

    def run_mag_cost():
        reached, spent, detail = 0, 0, []
        for r in order:
            lim = int(max(1, round(pred[r["h"]])))
            V.CACHE.clear()
            res = B.search(r["tid"], corpus[r["tid"]][r["h"]], r["Sx"], r["chains"], r["exprs"],
                           lambda x: 0, lim)
            spent += res["n_eval"]
            ok = res["s1"] >= 1.0
            reached += ok
            detail.append((r["tid"][:6], r["h"], lim, res["n_eval"], r["cost"], ok))
        return reached, spent, detail

    def run_rank(G):
        left, reached = G, 0
        for r in order:
            if left <= 0:
                break
            V.CACHE.clear()
            res = B.search(r["tid"], corpus[r["tid"]][r["h"]], r["Sx"], r["chains"], r["exprs"],
                           lambda x: 0, left)
            left -= res["n_eval"]
            if res["s1"] >= 1.0:
                reached += 1
        return reached

    print("\n## §2 MAG_COST（限额 = 预测的成本）")
    mc, c_mag2, detail = run_mag_cost()
    print(f"  → 到 1.0 {mc + n_full}/25｜花掉 {c_mag2} 次")
    print(f"  {'tid':7s} {'h':9s} {'lim':>4s} {'用了':>5s} {'实际cost':>8s}  到1.0")
    for t, h, lim, used, cost, ok in detail:
        print(f"  {t:7s} {h} {lim:4d} {used:5d} {cost:8d}  {'✅' if ok else '✗'}")

    r_same = run_rank(c_mag2)
    print(f"\n  RANK(G={c_mag2}) → 到 1.0 {r_same + n_full}/25（同预算对照）")
    r150 = run_rank(150)
    print(f"  RANK(G=150) → 到 1.0 {r150 + n_full}/25（参照）")

    rng = random.Random(0)
    perm = []
    for _ in range(N_PERM):
        px = xs[:]
        rng.shuffle(px)
        perm.append(ols(px, costs)[2])
    perm.sort()
    p95 = perm[int(0.95 * (len(perm) - 1))]

    print("\n## §3 判决")
    w1 = r2 >= 0.5
    print(f"  **W1 成本拟合 R² = {r2:.3f} ≥ 0.5**（成本可预测）⇒ {'✅ 押中' if w1 else '⛔ 押破'}")

    w2 = (mc + n_full) >= (r_same + n_full)
    print(f"  **W2 MAG_COST {mc + n_full}/25 ≥ RANK(G={c_mag2}) {r_same + n_full}/25** ⇒ "
          f"{'✅ 押中' if w2 else '⛔ 押破'}")

    w3 = (mc + n_full) > V132_MAG
    print(f"  **W3 MAG_COST {mc + n_full}/25 > V132 的 MAG {V132_MAG}/25**（只差量纲）⇒ "
          f"{'✅ 押中' if w3 else '⛔ 押破'}")

    w4 = 150 <= c_mag2 <= 800
    print(f"  **W4 C_mag2 = {c_mag2} ∈ [150,800]** ⇒ {'✅ 押中' if w4 else '⛔ 押破'}")

    w5 = r2 >= p95
    print(f"  **W5 真实 R² {r2:.3f} ≥ 置换 p95 {p95:.3f}**（均值 {statistics.mean(perm):.3f}）⇒ "
          f"{'✅ 押中' if w5 else '⛔ 押破'}")

    print("\n## §4 汇总")
    print(f"  W1 {'✅' if w1 else '⛔'}　W2 {'✅' if w2 else '⛔'}　W3 {'✅' if w3 else '⛔'}　"
          f"W4 {'✅' if w4 else '⛔'}　W5 {'✅' if w5 else '⛔'}")
    print(f"  唯一变量 = 限额预测的是成本还是收益：MAG {V132_MAG}/25 → MAG_COST {mc + n_full}/25")

    (HERE / "cost.json").write_text(json.dumps(dict(
        n_total=len(rows), n_gated=len(gated), n_full=n_full,
        fit=dict(a=a, b=b, r2=r2, resid_med_abs=statistics.median([abs(v) for v in resid])),
        MAG_COST=dict(to1=mc + n_full, spent=c_mag2),
        RANK_same=r_same + n_full, RANK_150=r150 + n_full,
        V132_MAG=V132_MAG, pred=pred,
        perm=dict(mean=statistics.mean(perm), p95=p95, mx=perm[-1], n=N_PERM),
        detail=[dict(tid=t, h=h, lim=lim, used=used, cost=cost, ok=ok)
                for t, h, lim, used, cost, ok in detail],
        rows=[{k: r[k] for k in ("tid", "h", "n_cand", "s0", "cost", "saved")} for r in rows],
    ), ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  原始读数已落盘 `cost.json`")


if __name__ == "__main__":
    main()
