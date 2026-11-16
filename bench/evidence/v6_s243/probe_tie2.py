#!/usr/bin/env python3
"""V154 核账：`(s0,n)` 规则的拟合**并列度** —— 32/33 这个数稳不稳。

问题：二维规则 `VALID iff s0>=τ1 and n>=τ2` 有 6×|n支撑| 个候选；在 33 格里，
**很多组合在留出训练集上并列最优**，最后选哪个由并列规则决定 ⇒ 头条数字可能是「并列规则决定的」。

本脚本对每个留出格：
  ① 列出所有**训练集上最优**的 (τ1,τ2)；
  ② 看这些并列规则对**该格**给出的预测是否一致（determined / undetermined）；
  ③ 给出「最小 τ」「最大 τ」两种全局约定下的准确率区间。
⛔ 不新造分数。
"""
from __future__ import annotations

import collections
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "v6_s241" / "readings.json"
TAUS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]


def main() -> None:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    cells = [c for c in d["cells"] if c["n"] > 0]
    for c in cells:
        c["tid"], c["h"] = c["cell"][0], c["cell"][1]
        c["valid"] = c["max_c"] >= 1.0

    print("# V154 核账：二维规则的并列度")
    rows = []
    for c in cells:
        o = [x for x in cells if x["tid"] == c["tid"] and x["h"] != c["h"]]
        cands = []
        for t1 in TAUS:
            for t2 in sorted({x["n"] for x in o}):
                ok = sum(1 for x in o if ((x["s0"] >= t1) and (x["n"] >= t2)) == x["valid"])
                cands.append((ok, t1, t2))
        best = max(x[0] for x in cands)
        arg = [(t1, t2) for ok, t1, t2 in cands if ok == best]
        preds = {(c["s0"] >= t1) and (c["n"] >= t2) for t1, t2 in arg}
        rows.append((c, len(arg), len(preds), len(o), best, len(o)))

    print("\n## ① 每个留出格的并列最优规则数，以及它们对该格的预测是否唯一")
    print("  | 实现 | 题 | s0 | n | 训练格 | 并列规则数 | 不同预测数 | 训练准确率 | 真值 |")
    print("  |---|---|---|---|---|---|---|---|---|")
    for c, narg, npred, ntr, best, _ in rows:
        print(f"  | {c['h']} | {c['tid'][:6]} | {c['s0']:.2f} | {c['n']} | {ntr} | {narg} | "
              f"{npred} | {best}/{ntr} | {'V' if c['valid'] else 'I'} |")

    undet = [r for r in rows if r[2] > 1]
    print(f"\n  留出格的预测**不唯一**（并列规则给出不同答案）的 = {len(undet)}/{len(rows)}")
    print(f"  并列规则数中位 = {sorted(r[1] for r in rows)[len(rows) // 2]}")

    def acc(pick):
        ok = 0
        for c in cells:
            o = [x for x in cells if x["tid"] == c["tid"] and x["h"] != c["h"]]
            cands = [(sum(1 for x in o if ((x["s0"] >= t1) and (x["n"] >= t2)) == x["valid"]), t1, t2)
                     for t1 in TAUS for t2 in sorted({x["n"] for x in o})]
            best = max(x[0] for x in cands)
            arg = sorted([(t1, t2) for ok, t1, t2 in cands if ok == best], key=pick)
            t1, t2 = arg[0]
            ok += ((c["s0"] >= t1) and (c["n"] >= t2)) == c["valid"]
        return ok

    lo = acc(lambda p: (p[0], p[1]))          # 最小 τ1、最小 τ2
    hi = acc(lambda p: (-p[0], -p[1]))        # 最大 τ1、最大 τ2
    print(f"\n## ② 两种全局并列约定的准确率：最小 τ = {lo}/33｜最大 τ = {hi}/33")
    print("  ⇒ 同一条规则族在**同一批格**上能给出不同答案 ⇒ 32/33 这个数必须带上这个区间读。")

    print("\n## ③ 逐格看：并列规则在哪些格上分歧")
    for c, narg, npred, ntr, best, _ in rows:
        if npred > 1:
            print(f"  {c['tid'][:12]}::{c['h']} s0={c['s0']:.2f} n={c['n']} 真值="
                  f"{'V' if c['valid'] else 'I'}｜{narg} 条并列规则给出 {npred} 种预测")

    out = dict(undetermined=len(undet), n=len(rows), acc_min_tau=lo, acc_max_tau=hi,
               tie_median=sorted(r[1] for r in rows)[len(rows) // 2])
    (HERE / "readings_tie.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
