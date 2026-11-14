#!/usr/bin/env python3
"""V126：预测循环第一格 —— 写之前用 `n_cand` 预测「这条记忆能省多少判分」。

零搜索、零模型、零网络：真值直接读 `v6_s210/search.json`（V121 落盘），
输入 `n_cand` 只由 `v121.gen` 生成（不判分）。
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


def spearman(xs, ys):
    """秩相关（平均秩处理并列）。"""
    def rank(v):
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
    rx, ry = rank(xs), rank(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def kendall(xs, ys):
    """Kendall tau-a（不处理并列，题内 5 个实现的 n_cand 互异）。"""
    n = len(xs)
    c = d = 0
    for i in range(n):
        for j in range(i + 1, n):
            a = (xs[i] - xs[j]) * (ys[i] - ys[j])
            if a > 0:
                c += 1
            elif a < 0:
                d += 1
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
            c0 = cold[key]["n_eval"]
            c1 = warm[key]["n_eval"]
            rows.append(dict(tid=tid, h=h, n_cand=n_cand, cold=c0, warm=c1, saved=c0 - c1,
                             lab=cold[key]["lab"]))

    print("## §0 逐实现表（n_cand 不判分｜saved 读自已落盘的 search.json）")
    print(f"  {'tid':8s} {'h':9s} {'n_cand':>6s} {'cold':>5s} {'warm':>5s} {'saved':>6s}  生效动作")
    for r in sorted(rows, key=lambda r: -r["n_cand"]):
        print(f"  {r['tid']:8s} {r['h']:9s} {r['n_cand']:6d} {r['cold']:5d} {r['warm']:5d}"
              f" {r['saved']:6d}  {r['lab'][:44]}")
    print(f"  合计 saved = {sum(r['saved'] for r in rows)}｜n = {len(rows)}")

    xs = [r["n_cand"] for r in rows]
    ys = [r["saved"] for r in rows]
    top_pred = [r["h"] for r in sorted(rows, key=lambda r: -r["n_cand"])[:4]]
    top_true = [r["h"] for r in sorted(rows, key=lambda r: -r["saved"])[:4]]
    alpha = [r["h"] for r in sorted(rows, key=lambda r: r["h"])[:4]]

    print("\n## §1 判决")
    print(f"  预测 top4（按 n_cand）= {top_pred}")
    print(f"  真值 top4（按 saved） = {top_true}")
    w1 = set(top_pred) == set(top_true)
    print(f"  **W1 集合相等** ⇒ {'✅ 押中' if w1 else '⛔ 押破'}（交集 {len(set(top_pred) & set(top_true))}/4）")

    rho = spearman(xs, ys)
    w2 = rho > 0
    print(f"  **W2 Spearman ρ(n_cand, saved) = {rho:+.3f} > 0** ⇒ {'✅ 押中' if w2 else '⛔ 押破'}")

    rho_a = spearman([float(int(h[:6], 16)) for h in [r["h"] for r in rows]], ys)
    inter = len(set(alpha) & set(top_true))
    w3 = inter < 4 and rho_a <= 0
    print(f"  **W3 零假设臂**：字母序 top4 交集 {inter}/4（须 <4）｜ρ_alpha = {rho_a:+.3f}（须 ≤0）"
          f" ⇒ {'✅ 押中' if w3 else '⛔ 押破'}")

    g = [r for r in rows if r["tid"].startswith("g7e1")]
    tau = kendall([r["n_cand"] for r in g], [r["saved"] for r in g])
    print(f"  **W4 题内对照（g7e1，n={len(g)}）Kendall τ = {tau:+.3f} > 0** ⇒ "
          f"{'✅ 押中' if tau > 0 else '⛔ 押破'}")
    print(f"      g7e1 明细：{[(r['h'][:6], r['n_cand'], r['saved']) for r in sorted(g, key=lambda r: -r['n_cand'])]}")

    print("\n## §2 汇总")
    print(f"  W1 {'✅' if w1 else '⛔'}　W2 {'✅' if w2 else '⛔'}　"
          f"W3 {'✅' if w3 else '⛔'}　W4 {'✅' if tau > 0 else '⛔'}")
    if w1 and w2 and w3 and tau > 0:
        print("  ⇒ 预测循环第一格闭合：`n_cand` 是可用的事前预测量（题内也成立）")
    elif w1 and w2:
        print("  ⇒ 只成立「跨题量级差可预测」，⛔ 不成立「能预测省多少」")
    else:
        print("  ⇒ 预测不成立")

    (HERE / "pred.json").write_text(json.dumps(
        dict(rows=rows, rho=rho, rho_alpha=rho_a, tau_g7e1=tau,
             top_pred=top_pred, top_true=top_true, top_alpha=alpha),
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  原始读数已落盘 `pred.json`")


if __name__ == "__main__":
    main()
