#!/usr/bin/env python3
"""V98 读数：六臂 × 4 题，逐格 + 五条判据（按 PREREG 逐字）。"""
from __future__ import annotations

import json
import pathlib
import sys

ARMS = ["N0", "N1", "N2", "N3", "N4", "N5"]
RUNS = [f"{a}{s}" for a in ARMS for s in "ab"]
TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]
SHORT = {t: t.split("_")[0] for t in TASKS}
BASE = {"g7d2_dedupe_keep": 1.0, "g7d3_fmt_amount": 0.4,
        "g7e1_parse_flag": 1.0, "g7e2_truncate_tail": 1.0}
WHAT = {"N0": "无（基线）", "N1": "ZK2 句末（=V97 M3）", "N2": "ZK1 句末",
        "N3": "ZK3 句末", "N4": "ZK4 句末", "N5": "四条全换（4 字）"}


def scores(d: pathlib.Path) -> dict[str, float]:
    p = d / "bench" / "runs.jsonl"
    if not p.exists():
        return {}
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    per = (rec.get("report") or {}).get("per_task") or []
    return {t["id"]: t.get("score") for t in per if t.get("score") is not None}


def answers(d: pathlib.Path) -> dict[str, str]:
    for p in (d / "bench" / "answers.jsonl", d / "answers.jsonl"):
        if p.exists():
            out = {}
            for ln in p.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                r = json.loads(ln)
                if r.get("kind") != "answer":
                    continue
                out[r["task_id"]] = r.get("answer") or ""
            return out
    return {}


def main() -> None:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v98")
    got = {r: scores(root / r) for r in RUNS}
    got = {k: v for k, v in got.items() if v}

    print("## §1 逐跑")
    print(f"{'跑':6s}" + "".join(f"{SHORT[t]:>10s}" for t in TASKS))
    for r in RUNS:
        if r not in got:
            print(f"{r:6s}  (缺)")
            continue
        print(f"{r:6s}" + "".join(
            f"{got[r][t]:10.1f}" if got[r].get(t) is not None else f"{'-':>10s}" for t in TASKS))

    print("\n## §2 臂均值（2 跑）")
    agg: dict[str, dict[str, float]] = {}
    for a in ARMS:
        runs = [got[r] for r in RUNS if r.startswith(a) and r in got]
        if not runs:
            continue
        cell = {t: round(sum(x.get(t, 0) for x in runs) / len(runs), 4) for t in TASKS}
        agg[a] = cell | {"mean": round(sum(cell.values()) / len(TASKS), 4)}
        print(f"{a} {WHAT[a]:20s} " + "  ".join(f"{SHORT[t]}={cell[t]:.2f}" for t in TASKS)
              + f"  mean={agg[a]['mean']:.4f}")

    print("\n## §3 判据（按 PREREG 原文）")
    # P0
    if "N1" in agg:
        same = all(abs(agg["N1"][t] - 1.0) < 1e-9 for t in TASKS)
        print(f"  P0 N1 逐格 = 1.0/1.0/1.0/1.0："
              f"{'✓ V97 的 M3 可复现' if same else '⛔ 不复现 ⇒ 本轮无效'}")
        if not same:
            print("     实得 " + "  ".join(f"{SHORT[t]}={agg['N1'][t]:.2f}" for t in TASKS))
    else:
        print("  P0 缺 N1 ⇒ 无效")

    # P1
    full = [a for a in ("N2", "N3", "N4") if a in agg and abs(agg[a]["mean"] - 1.0) < 1e-9]
    n1 = len(full)
    if n1 == 0:
        v = "⛔ 0（效应锁死在 ZK2 这一条）"
    elif n1 <= 2:
        v = "✓ 命中（1~2 ⇒ 部分通用）"
    else:
        v = "⚠️ 3（与位置无关）"
    print(f"  P1 N2/N3/N4 里 mean=1.00 的个数 = {n1}/3（押 1~2）⇒ {v} {full}")

    # P2
    if "N5" in agg:
        ok = abs(agg["N5"]["mean"] - 1.0) < 1e-9
        print(f"  P2 N5（四条全换）mean={agg['N5']['mean']:.4f} ⇒ "
              f"{'✓ 1.00 ⇒ 可写成一条通用写端规则' if ok else '⛔ 未到 1.00'}")

    # P3
    ok3 = True
    for a in ARMS:
        pair = [r for r in RUNS if r.startswith(a) and r in got]
        if len(pair) == 2 and not all(got[pair[0]].get(t) == got[pair[1]].get(t) for t in TASKS):
            ok3 = False
            print(f"  P3 ⛔ {a} 两跑有翻动")
    print(f"  P3 六臂两跑逐格相同：{'✓（第 10 次确认）' if ok3 else '⛔ 见上'}")

    # P4
    print("  P4 逐臂翻转位置（相对 N0）：")
    if "N0" in agg:
        for a in ("N1", "N2", "N3", "N4", "N5"):
            if a not in agg:
                continue
            cells = [SHORT[t] for t in TASKS if abs(agg["N0"][t] - agg[a][t]) > 1e-9]
            mark = "" if a == "N1" else ("  ← 改的不是 ZK2，却动了 g7d3？" if "g7d3" in cells else "")
            print(f"     {a} {WHAT[a]:20s} 翻转 {len(cells)} 格 {cells}{mark}")

    print("\n## §4 答案本体指纹（`--answers`，纪律 39）")
    import hashlib
    for a in ARMS:
        A = answers(root / f"{a}a")
        if not A:
            print(f"  {a}a 无答案文件")
            continue
        print(f"  {a}a  " + "  ".join(f"{t[:4]}={hashlib.sha256(v.encode()).hexdigest()[:8]}"
                                      for t, v in A.items()))


if __name__ == "__main__":
    main()
