#!/usr/bin/env python3
"""V95 读数：两臂 × 4 题，逐格 + 四条判据。判据按 PREREG 逐字。"""
from __future__ import annotations

import json
import pathlib
import sys

ARMS = ["S1a", "S1b", "S3a", "S3b"]
TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]
SHORT = {t: t.split("_")[0] for t in TASKS}
#: V93 的 R1（原规则 211 字）逐格
R1 = {"g7d2_dedupe_keep": 1.0, "g7d3_fmt_amount": 0.4,
      "g7e1_parse_flag": 1.0, "g7e2_truncate_tail": 1.0}
BLOCK = {"S1": 165, "R1": 211, "S3": 362}


def scores(d: pathlib.Path) -> dict[str, float]:
    p = d / "bench" / "runs.jsonl"
    if not p.exists():
        return {}
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    per = (rec.get("report") or {}).get("per_task") or []
    return {t["id"]: t.get("score") for t in per if t.get("score") is not None}


def main() -> None:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v95")
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
    for base in ("S1", "S3"):
        runs = [got[a] for a in ARMS if a.startswith(base) and a in got]
        if not runs:
            continue
        cell = {t: round(sum(r.get(t, 0) for r in runs) / len(runs), 4) for t in TASKS}
        agg[base] = cell | {"mean": round(sum(cell.values()) / len(TASKS), 4)}
        print(f"{base}: " + "  ".join(f"{SHORT[t]}={cell[t]:.2f}" for t in TASKS) + f"  mean={agg[base]['mean']:.4f}")
    print("R1: " + "  ".join(f"{SHORT[t]}={R1[t]:.2f}" for t in TASKS) + "  mean=0.8500")

    print("\n## §3 三点长度曲线（同一份语义）")
    for tag, blk, m in (("S1", BLOCK["S1"], agg.get("S1", {}).get("mean")),
                        ("R1", BLOCK["R1"], 0.85),
                        ("S3", BLOCK["S3"], agg.get("S3", {}).get("mean"))):
        print(f"  {tag:3s} 块={blk:3d} 字  mean={m if m is None else f'{m:.4f}'}")

    print("\n## §4 判据（按 PREREG 原文）")
    s1, s3 = agg.get("S1", {}), agg.get("S3", {})

    v = s1.get("mean")
    print(f"  T1 S1.mean={v if v is None else f'{v:.4f}'}（押≥0.85）⇒ "
          f"{'✓ 命中（短不劣）' if v is not None and v >= 0.85 else '⛔ 未命中'}")

    if v is not None and s3.get("mean") is not None:
        ok = v > s3["mean"]
        print(f"  T2 S1 > S3：{v:.4f} vs {s3['mean']:.4f} ⇒ {'✓ 命中（长度剂量关系）' if ok else '⛔ 未命中（长度无效应）'}")
    else:
        print("  T2 缺臂，无法判")

    diff = [SHORT[t] for t in TASKS if abs(s1.get(t, -1) - R1[t]) > 1e-9]
    print(f"  T3 S1 vs R1 逐格差异 = {len(diff)} 格 {diff} ⇒ {'✓ 压缩没丢信息' if len(diff) <= 1 else '⛔ 超过 1 格'}")

    for base in ("S1", "S3"):
        pair = [a for a in ARMS if a.startswith(base) and a in got]
        if len(pair) == 2:
            okk = all(got[pair[0]].get(t) == got[pair[1]].get(t) for t in TASKS)
            print(f"  T4 {base} 两跑逐格相同: {'✓' if okk else '⛔ 有翻动'}")


if __name__ == "__main__":
    main()
