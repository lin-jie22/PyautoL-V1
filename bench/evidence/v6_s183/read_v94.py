#!/usr/bin/env python3
"""V94 读数：三臂 × 4 题，逐格 + 四条判据。判据按 PREREG 逐字，不放宽作用域。"""
from __future__ import annotations

import json
import pathlib
import sys

ARMS = ["C1a", "C1b", "C2a", "C2b", "C3a", "C3b"]
TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]
SHORT = {t: t.split("_")[0] for t in TASKS}
#: V93 的 R1（纯规则）逐格，本轮回测九次一致
R1 = {"g7d2_dedupe_keep": 1.0, "g7d3_fmt_amount": 0.4,
      "g7e1_parse_flag": 1.0, "g7e2_truncate_tail": 1.0}


def scores(d: pathlib.Path) -> dict[str, float]:
    p = d / "bench" / "runs.jsonl"
    if not p.exists():
        return {}
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    per = (rec.get("report") or {}).get("per_task") or []
    return {t["id"]: t.get("score") for t in per if t.get("score") is not None}


def main() -> None:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v94")
    got = {a: scores(root / a) for a in ARMS}
    got = {k: v for k, v in got.items() if v}

    print("## §1 逐格")
    print(f"{'臂':6s}" + "".join(f"{SHORT[t]:>10s}" for t in TASKS) + f"{'mean':>8s}")
    for a in ARMS:
        if a not in got:
            print(f"{a:6s}  (缺)")
            continue
        row = [got[a].get(t) for t in TASKS]
        m = round(sum(x for x in row if x is not None) / len(TASKS), 4)
        print(f"{a:6s}" + "".join(f"{x:10.1f}" if x is not None else f"{'-':>10s}" for x in row) + f"{m:8.4f}")

    print("\n## §2 臂均值")
    agg: dict[str, dict[str, float]] = {}
    for base in ("C1", "C2", "C3"):
        runs = [got[a] for a in ARMS if a.startswith(base) and a in got]
        if not runs:
            continue
        cell = {t: round(sum(r.get(t, 0) for r in runs) / len(runs), 4) for t in TASKS}
        agg[base] = cell | {"mean": round(sum(cell.values()) / len(TASKS), 4)}
        print(f"{base}: " + "  ".join(f"{SHORT[t]}={cell[t]:.2f}" for t in TASKS) + f"  mean={cell and agg[base]['mean']:.4f}")
    print(f"R1（V93 参照）: " + "  ".join(f"{SHORT[t]}={R1[t]:.2f}" for t in TASKS) + "  mean=0.8500")

    print("\n## §3 判据（按 PREREG 原文）")
    c1, c2, c3 = (agg.get(k, {}) for k in ("C1", "C2", "C3"))

    # P1：C1.mean ≥ 0.85 且四题全 ≥0.8
    all_ok = all(c1.get(t, 0) >= 0.8 for t in TASKS)
    print(f"  P1 C1.mean={c1.get('mean'):.4f}（押≥0.85）四题全≥0.8={'是' if all_ok else '否'} "
          f"⇒ {'✓ 命中' if c1.get('mean', 0) >= 0.85 and all_ok else '⛔ 未命中'}")

    # P2：C3 逐格 = R1
    same = all(abs(c3.get(t, -1) - R1[t]) < 1e-9 for t in TASKS)
    print(f"  P2 C3={[c3.get(t) for t in TASKS]} vs R1={[R1[t] for t in TASKS]} "
          f"⇒ {'✓ 命中（长度无害）' if same else '⛔ 未命中（长度/填充有影响）'}")

    # P3：C2 至少一题 ≤0.4
    low = [SHORT[t] for t in TASKS if c2.get(t, 9) <= 0.4]
    print(f"  P3 C2 掉到 ≤0.4 的题={low or '无'} ⇒ {'✓ 命中' if low else '⛔ 未命中（内容不起作用）'}")

    # P4：两跑逐格相同
    for base in ("C1", "C2", "C3"):
        pair = [a for a in ARMS if a.startswith(base) and a in got]
        if len(pair) == 2:
            okk = all(got[pair[0]].get(t) == got[pair[1]].get(t) for t in TASKS)
            print(f"  P4 {base} 两跑逐格相同: {'✓' if okk else '⛔ 有翻动'}")

    print("\n## §4 诊断（P1 未中时看这个）")
    if c1 and c3:
        gap_len = round(c3.get("mean", 0) - R1["g7d2_dedupe_keep"] * 0, 4)
        d_len = round(c3["mean"] - 0.85, 4)
        d_con = round(c1["mean"] - c3["mean"], 4)
        print(f"  R1=0.85 → C3(纯规则+等长填充)={c3['mean']:.4f}  ⇒ 长度带来的 Δ={d_len:+.4f}")
        print(f"  C3 → C1（加上 2 条示例）={c1['mean'] - c3['mean']:+.4f}  ⇒ 示例内容带来的 Δ={d_con:+.4f}")
        print(f"  R1 → C1（净变化）={c1['mean'] - 0.85:+.4f}")


if __name__ == "__main__":
    main()
