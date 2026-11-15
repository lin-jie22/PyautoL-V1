#!/usr/bin/env python3
"""V132：让系统算自己预测的误差 —— 「这个数还能不能用来做决定」。

纯离线取真值（v6_s210/search.json）+ 免费重算 n_cand（不判分）做拟合；
决策臂只跑 `v122.search(..., budget)`，唯一变量 = 预算怎么分配。
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
for d in ("v6_s205", "v6_s208", "v6_s209", "v6_s210", "v6_s211", "v6_s198", "v6_s217"):
    sys.path.insert(0, str(EV / d))
import search as S            # noqa: E402
import idiom_transfer as I     # noqa: E402
import v119 as V              # noqa: E402
import v120 as W              # noqa: E402
import v121 as M              # noqa: E402
import v122 as B              # noqa: E402

TIDS = V.TIDS
GS = [150, 300, 450]
N_PERM = 30


def ols(xs, ys):
    """最小二乘 y = a x + b；返回 (a, b, R²)。"""
    n = len(xs)
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    a = sxy / sxx if sxx else 0.0
    b = my - a * mx
    resid = [y - (a * x + b) for x, y in zip(xs, ys)]
    ss_res = sum(r * r for r in resid)
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    return a, b, r2, resid


def main() -> None:
    corpus = I.harvest()
    raw = json.loads((EV / "v6_s210" / "search.json").read_text(encoding="utf-8"))
    cold, warm = raw["cold"], raw["warm"]

    impls, rows = [], []
    for tid in TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                continue
            impls.append((tid, h))
            key = f"{tid}::{h}"
            Sx, _ = S.wordset(tid, corpus[tid][h], True)
            chains = V.harvest_chains(corpus, tid, h)
            exprs = W.harvest_exprs(corpus, tid, h)
            n_cand = len(M.gen(tid, corpus[tid][h], Sx, chains, exprs))   # ⛔ 不判分
            rows.append(dict(tid=tid, h=h, Sx=Sx, chains=chains, exprs=exprs,
                             n_cand=n_cand, s0=cold[key]["s0"],
                             saved=cold[key]["n_eval"] - warm[key]["n_eval"]))

    gated = [r for r in rows if r["s0"] < 1.0]
    n_full = len(rows) - len(gated)
    print(f"## §0 分母（先打印，纪律 46）")
    print(f"  n_total = {len(rows)}｜n_baseline_full = {n_full}｜n_gated = {len(gated)}")

    # ---------------- 第一步：拟合（用全部 15 个点 = 过拟合上界）
    xs = [r["n_cand"] for r in gated]
    ys = [r["saved"] for r in gated]
    a, b, r2, resid = ols(xs, ys)
    print(f"\n## §1 过拟合上界（y = {a:.2f}·n_cand {b:+.1f}）")
    print(f"  R² = {r2:.3f}｜残差均值 {statistics.mean(resid):+.1f}｜残差中位绝对值 "
          f"{statistics.median([abs(v) for v in resid]):.1f}｜实测 saved 中位 {statistics.median(ys):.1f}")
    for r, e in sorted(zip(gated, resid), key=lambda t: -t[0]["n_cand"])[:6]:
        print(f"    {r['tid'][:6]} {r['h']} n_cand={r['n_cand']:3d} saved={r['saved']:4d}"
              f" pred={a*r['n_cand']+b:7.1f} 残差 {e:+7.1f}")

    # ---------------- 第二步：三箱
    print("\n## §2 三箱（按 n_cand 升序，5/5/5）")
    srt = sorted(gated, key=lambda r: r["n_cand"])
    boxes = [srt[i*5:(i+1)*5] for i in range(3)]
    box_obs = []
    for i, bx in enumerate(boxes):
        pred_m = statistics.mean(a * r["n_cand"] + b for r in bx)
        obs_m = statistics.mean(r["saved"] for r in bx)
        box_obs.append(obs_m)
        print(f"  箱{i+1} n_cand {bx[0]['n_cand']:3d}~{bx[-1]['n_cand']:3d}｜预测均值 {pred_m:6.1f}"
              f"｜**实测均值 {obs_m:6.1f}**｜实测区间 {min(r['saved'] for r in bx)}~{max(r['saved'] for r in bx)}")

    # ---------------- 第三步：两臂决策
    pred = {r["h"]: max(1.0, a * r["n_cand"] + b) for r in gated}
    order = [r for r in srt]                      # n_cand 升序，两臂同序

    def run_rank(G):
        left, reached, spent = G, 0, 0
        for r in order:
            if left <= 0:
                break
            V.CACHE.clear()
            res = B.search(r["tid"], corpus[r["tid"]][r["h"]], r["Sx"], r["chains"], r["exprs"],
                           lambda x: 0, left)
            left -= res["n_eval"]
            spent += res["n_eval"]
            if res["s1"] >= 1.0:
                reached += 1
        return reached, spent

    def run_mag():
        reached, spent = 0, 0
        for r in order:
            lim = int(max(1, round(pred[r["h"]])))
            V.CACHE.clear()
            res = B.search(r["tid"], corpus[r["tid"]][r["h"]], r["Sx"], r["chains"], r["exprs"],
                           lambda x: 0, lim)
            spent += res["n_eval"]
            if res["s1"] >= 1.0:
                reached += 1
        return reached, spent

    print("\n## §3 决策臂（分母 25 = 15 gated 里修好的 + 10 个本来就满分）")
    mag_to1, c_mag = run_mag()
    print(f"  MAG  （每条按预测限额）→ 到 1.0 {mag_to1 + n_full}/25｜花掉 {c_mag} 次")
    rank_at = {}
    for G in sorted(set(GS + [c_mag])):
        to1, spent = run_rank(G)
        rank_at[G] = dict(to1=to1 + n_full, spent=spent)
        tag = "  ← 同预算对照点" if G == c_mag else ""
        print(f"  RANK(G={G:3d}) → 到 1.0 {to1 + n_full}/25｜花掉 {spent} 次{tag}")

    # ---------------- 第四步：置换零假设
    rng = random.Random(0)
    perm_r2 = []
    for _ in range(N_PERM):
        px = xs[:]
        rng.shuffle(px)
        perm_r2.append(ols(px, ys)[2])
    perm_r2.sort()
    p95 = perm_r2[int(0.95 * (len(perm_r2) - 1))]

    # ---------------- 判决
    print("\n## §4 判决")
    w1 = r2 < 0.5
    print(f"  **W1 过拟合 R² = {r2:.3f} < 0.5**（幅度不可用）⇒ {'✅ 押中' if w1 else '⛔ 押破'}")

    mono = box_obs[0] < box_obs[1] < box_obs[2]
    print(f"  **W2 三箱实测均值严格单调 {['%.1f' % v for v in box_obs]}** ⇒ {'✅ 押中' if mono else '⛔ 押破'}")

    w3 = mag_to1 < rank_at[c_mag]["to1"]
    print(f"  **W3 MAG {mag_to1}/25 < RANK(G={c_mag}) {rank_at[c_mag]['to1']}/25（同预算）** ⇒ "
          f"{'✅ 押中' if w3 else '⛔ 押破'}")

    w4 = c_mag >= 150
    print(f"  **W4 C_mag = {c_mag} ≥ 150**（同预算对照有意义）⇒ {'✅ 押中' if w4 else '⛔ 押破'}")

    w5 = r2 >= p95
    print(f"  **W5 真实 R² {r2:.3f} ≥ 置换分布 p95 {p95:.3f}**"
          f"（均值 {statistics.mean(perm_r2):.3f}｜max {perm_r2[-1]:.3f}）⇒ {'✅ 押中' if w5 else '⛔ 押破'}")

    print("\n## §5 汇总")
    print(f"  W1 {'✅' if w1 else '⛔'}　W2 {'✅' if mono else '⛔'}　W3 {'✅' if w3 else '⛔'}　"
          f"W4 {'✅' if w4 else '⛔'}　W5 {'✅' if w5 else '⛔'}")
    if (not w1) and w3:
        print("  ⇒ **R² 高 ≠ 能用来做决定**：拟合优度 0.808，按它分配预算却只做成 1/25（同预算 19/25）。")
        print("     ⇒ 元结论：判「这个预测能不能用」必须看「用它做决定会不会变差」，**不能看 R²**。")
    elif w1 and mono and w3:
        print("  ⇒ **元结论成立：这个预测只有「序」，没有「数」。系统应当只保留排序，丢弃幅度。**")
    elif mono and not w1:
        print("  ⇒ 幅度看起来可用（R² 高），⛔ 但 W3 未成立/未测 ⇒ 见 §3 决策臂")
    else:
        print("  ⇒ ⛔ 见失败模式（PREREG §3）")

    (HERE / "calib.json").write_text(json.dumps(dict(
        n_total=len(rows), n_gated=len(gated), n_full=n_full,
        fit=dict(a=a, b=b, r2=r2,
                 resid_med_abs=statistics.median([abs(v) for v in resid])),
        boxes=box_obs, pred={k: v for k, v in pred.items()},
        MAG=dict(to1=mag_to1 + n_full, spent=c_mag),
        RANK={str(k): v for k, v in rank_at.items()},
        perm=dict(mean=statistics.mean(perm_r2), p95=p95, mx=perm_r2[-1], n=N_PERM),
        rows=[{k: r[k] for k in ("tid", "h", "n_cand", "s0", "saved")} for r in rows],
    ), ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  原始读数已落盘 `calib.json`")


if __name__ == "__main__":
    main()
