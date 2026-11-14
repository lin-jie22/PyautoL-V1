#!/usr/bin/env python3
"""V127：给预测器加一道「值不值得写」的门（基线已满分的不写），其余全不动。

零搜索、零模型、零网络。真值读 `v6_s210/search.json`，`n_cand` 由 `v121.gen` 生成。
"""
from __future__ import annotations

import ast
import json
import pathlib
import statistics
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for d in ("v6_s205", "v6_s208", "v6_s209", "v6_s210", "v6_s211", "v6_s198"):
    sys.path.insert(0, str(EV / d))
import search as S            # noqa: E402
import idiom_transfer as I     # noqa: E402
import v119 as V              # noqa: E402
import v120 as W              # noqa: E402
import v121 as M              # noqa: E402

TIDS = V.TIDS


def _rank(v):
    s = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and v[s[j + 1]] == v[s[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for t in range(i, j + 1):
            r[s[t]] = avg
        i = j + 1
    return r


def spearman(xs, ys):
    rx, ry = _rank(xs), _rank(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def kendall(xs, ys):
    n = len(xs)
    c = d = 0
    for i in range(n):
        for j in range(i + 1, n):
            a = (xs[i] - xs[j]) * (ys[i] - ys[j])
            c += a > 0
            d += a < 0
    tot = n * (n - 1) / 2
    return (c - d) / tot if tot else 0.0


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
            n_cand = len(M.gen(tid, corpus[tid][h], Sx, chains, exprs))   # ⛔ 不判分
            c = cold[key]
            rows.append(dict(tid=tid, h=h, n_cand=n_cand, s0=c["s0"],
                             cold=c["n_eval"], warm=warm[key]["n_eval"],
                             saved=c["n_eval"] - warm[key]["n_eval"]))

    gated = [r for r in rows if r["s0"] < 1.0]
    print(f"## §0 分母（纪律 46：先打印）")
    print(f"  n_total = {len(rows)}｜n_baseline_full = {len(rows) - len(gated)}｜n_gated = {len(gated)}")

    print("\n## §0b gated 子集（基线未满分的 15 个，按 n_cand 降序）")
    for r in sorted(gated, key=lambda r: -r["n_cand"]):
        print(f"  {r['tid']:18s} {r['h']} n_cand={r['n_cand']:3d} s0={r['s0']:.1f}"
              f" cold={r['cold']:3d} warm={r['warm']:3d} saved={r['saved']:3d}")

    print("\n## §0c 被门挡掉的（基线已满分，n_cand 白大）")
    for r in sorted([r for r in rows if r["s0"] >= 1.0], key=lambda r: -r["n_cand"])[:4]:
        print(f"  {r['tid']:18s} {r['h']} n_cand={r['n_cand']:3d} saved={r['saved']}")

    top_pred = [r["h"] for r in sorted(gated, key=lambda r: -r["n_cand"])[:4]]
    top_true = [r["h"] for r in sorted(rows, key=lambda r: -r["saved"])[:4]]

    print("\n## §1 判决")
    print(f"  预测 top4（gated，按 n_cand）= {top_pred}")
    print(f"  真值 top4（全部 25，按 saved）= {top_true}")
    w1 = set(top_pred) == set(top_true)
    print(f"  **W1 集合相等** ⇒ {'✅ 押中' if w1 else '⛔ 押破'}（交集 {len(set(top_pred) & set(top_true))}/4）")

    rho_g = spearman([r["n_cand"] for r in gated], [r["saved"] for r in gated])
    rho_u = spearman([r["n_cand"] for r in rows], [r["saved"] for r in rows])
    w2 = rho_g > rho_u
    print(f"  **W2 gated ρ = {rho_g:+.3f} > ungated ρ = {rho_u:+.3f}** ⇒ {'✅ 押中' if w2 else '⛔ 押破'}"
          f"（分母 {len(gated)} vs {len(rows)}，⛔ 不混比）")

    g = [r for r in gated if r["tid"].startswith("g7e1")]
    tau = kendall([r["n_cand"] for r in g], [r["saved"] for r in g])
    w3 = abs(tau - 1.0) < 1e-9
    print(f"  **W3 题内 g7e1（n={len(g)}）τ = {tau:+.3f} == +1.0** ⇒ {'✅ 押中' if w3 else '⛔ 押破'}")
    print(f"      {[(r['h'][:6], r['n_cand'], r['saved']) for r in sorted(g, key=lambda r: -r['n_cand'])]}")

    rho_a = spearman([float(int(r["h"][:6], 16)) for r in gated], [r["saved"] for r in gated])
    w4 = abs(rho_a) < 0.2
    print(f"  **W4 零假设（哈希序）|ρ_alpha| = {abs(rho_a):+.3f} < 0.2** ⇒ {'✅ 押中' if w4 else '⛔ 押破'}")

    print("\n## §2 汇总")
    print(f"  W1 {'✅' if w1 else '⛔'}　W2 {'✅' if w2 else '⛔'}　W3 {'✅' if w3 else '⛔'}　W4 {'✅' if w4 else '⛔'}")
    cost = len(rows)
    saved_total = sum(r["saved"] for r in rows)
    print(f"  预测成本 = {cost} 次判分（每个实现跑一次基线）｜它预测的节省 = {saved_total} 次"
          f" ⇒ 回报 {saved_total/cost:.1f}×")
    if w1 and w3:
        print("  ⇒ 预测循环第一格闭合：**先判一次基线 + 按候选数排序**，可事前决定写不写这条记忆")
    elif w1:
        print("  ⇒ 头部对、题内不稳 ⇒ 只能当跨题粗排序")
    else:
        print("  ⇒ ⛔ 预测不成立，须换输入")

    (HERE / "pred.json").write_text(json.dumps(
        dict(rows=rows, gated=[r["h"] for r in gated], rho_gated=rho_g, rho_ungated=rho_u,
             tau_g7e1=tau, rho_alpha=rho_a, top_pred=top_pred, top_true=top_true,
             n_total=len(rows), n_gated=len(gated), saved_total=saved_total),
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  原始读数已落盘 `pred.json`")


if __name__ == "__main__":
    main()
