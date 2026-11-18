#!/usr/bin/env python3
"""V197 汇总：N（V194 冻结）/ L（题面+真 why）/ M（题面+只有计数）。

⛔ 分母不同不比（V196 自踩第 1 条）：先打印三臂的 `len`，再算差。
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
BASE = OUT.parent / "l3_s282" / "readings_v194.json"


def main() -> int:
    base = {(r["suite"], r["id"]): float(r["score"])
            for r in json.loads(BASE.read_text(encoding="utf-8"))["rows"]}
    pool = sorted(k for k, v in base.items() if v < 1.0)
    rows = [json.loads(l) for l in
            (OUT / "cells_v197.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    by: dict[tuple[str, str], dict] = {}
    for r in rows:
        by.setdefault((r["suite"], r["id"]), {})[r["arm"]] = r

    print(f"池 {len(pool)} 格；已跑 {len(by)} 格；行数 {len(rows)}")
    nL = sum(1 for c in by if "L" in by[c] and by[c]["L"].get("score") is not None)
    nM = sum(1 for c in by if "M" in by[c] and by[c]["M"].get("score") is not None)
    print(f"各臂有效格数：N={len(pool)}  L={nL}  M={nM}   ⛔ 分母不同不比")

    ok_cells = [c for c in by
                if by[c].get("L", {}).get("score") is not None
                and by[c].get("M", {}).get("score") is not None]
    print(f"成对分母（L 与 M 都有分）= {len(ok_cells)}；剔除 {len(pool) - len(ok_cells)} 格")
    if not ok_cells:
        return 1

    def mean(arm: str) -> float:
        v = [float(by[c][arm]["score"]) for c in ok_cells]
        return sum(v) / len(v)

    mN = sum(base[c] for c in ok_cells) / len(ok_cells)
    mL, mM = mean("L"), mean("M")
    print(f"\n配对 denom={len(ok_cells)}")
    print(f"  N = {mN:.4f}   L = {mL:.4f}   M = {mM:.4f}")
    print(f"  L−N = {mL - mN:+.4f}   (P4 押 ≥ +0.10)")
    print(f"  M−N = {mM - mN:+.4f}   (P5 押 ≤ +0.05)")
    print(f"  L−M = {mL - mM:+.4f}   (P6 押 ≥ +0.08)")

    for arm in ("L", "M"):
        up = sum(1 for c in ok_cells if float(by[c][arm]["score"]) > base[c] + 1e-9)
        dn = sum(1 for c in ok_cells if float(by[c][arm]["score"]) < base[c] - 1e-9)
        print(f"  {arm} vs N 翻转：涨 {up} / 平 {len(ok_cells) - up - dn} / 跌 {dn}")

    # 事后登记（⛔ 不算押）
    echo = sum(1 for c in ok_cells for a in ("L", "M") if by[c][a].get("echo_assert"))
    echo_L = sum(1 for c in ok_cells if by[c]["L"].get("echo_assert"))
    print(f"\n事后登记 echo_assert（交付答案里原样出现隐藏测试串）：L {echo_L}/{len(ok_cells)}，"
          f"两臂合计 {echo}/{2 * len(ok_cells)}")

    # 逐格明细
    print("\nsuite/id                          N      L      M     echoL")
    for c in sorted(ok_cells):
        print(f"{c[0][:14]:14s}/{c[1][:20]:20s} {base[c]:.3f} "
              f"{float(by[c]['L']['score']):.3f} {float(by[c]['M']['score']):.3f}"
              f"   {by[c]['L'].get('echo_assert')}")

    # 拒交付的格
    bad = [(c, a) for c in by for a in ("L", "M")
           if by[c][a].get("score") is None]
    if bad:
        print(f"\n拒交付/无分：{len(bad)} 项")
        for c, a in bad[:8]:
            print(f"  {a} {c} :: {str(by[c][a].get('err') or by[c][a].get('why'))[:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
