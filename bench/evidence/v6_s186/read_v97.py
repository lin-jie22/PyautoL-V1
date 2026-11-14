#!/usr/bin/env python3
"""V97 读数：四臂 × 4 题，逐格 + 四条判据（按 PREREG 逐字）。"""
from __future__ import annotations

import json
import pathlib
import sys

ARMS = ["M0", "M1", "M2", "M3"]
RUNS = [f"{a}{s}" for a in ARMS for s in "ab"]
TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]
SHORT = {t: t.split("_")[0] for t in TASKS}
#: V93 R1 = V96 W2 = 本轮 M0（三者逐字节相同）
BASE = {"g7d2_dedupe_keep": 1.0, "g7d3_fmt_amount": 0.4,
        "g7e1_parse_flag": 1.0, "g7e2_truncate_tail": 1.0}
DIFF = {"M0": "—", "M1": "两→2（位置 21）", "M2": "写→加（位置 36）", "M3": "。→.（位置 39）"}


def scores(d: pathlib.Path) -> dict[str, float]:
    p = d / "bench" / "runs.jsonl"
    if not p.exists():
        return {}
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    per = (rec.get("report") or {}).get("per_task") or []
    return {t["id"]: t.get("score") for t in per if t.get("score") is not None}


def answers(d: pathlib.Path) -> dict[str, str]:
    """落盘答案本体（V97 新开 `--answers`）：{task_id: answer}。只吃 `kind="answer"` 行。"""
    for p in (d / "bench" / "answers.jsonl", d / "answers.jsonl",
              d / "bench_out" / "answers.jsonl"):
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
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v97")
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
        print(f"{a} {DIFF[a]:18s} " + "  ".join(f"{SHORT[t]}={cell[t]:.2f}" for t in TASKS)
              + f"  mean={agg[a]['mean']:.4f}")

    print("\n## §3 判据（按 PREREG 原文）")
    # W0
    if "M0" in agg:
        same = all(abs(agg["M0"][t] - BASE[t]) < 1e-9 for t in TASKS)
        print(f"  W0 M0 逐格 = 1.0/0.4/1.0/1.0："
              f"{'✓ 条件可复现' if same else '⛔ 不复现 ⇒ 全场 UNKNOWN'}")
        if not same:
            print("     实得 " + "  ".join(f"{SHORT[t]}={agg['M0'][t]:.2f}" for t in TASKS))
    else:
        print("  W0 缺 M0 ⇒ UNKNOWN")

    # W1：相对 M0 有翻转的臂数
    flipped = []
    if "M0" in agg:
        for a in ("M1", "M2", "M3"):
            if a not in agg:
                continue
            cells = [(SHORT[t], agg["M0"][t], agg[a][t]) for t in TASKS
                     if abs(agg["M0"][t] - agg[a][t]) > 1e-9]
            if cells:
                flipped.append((a, cells))
    n = len(flipped)
    if n == 0:
        v = "✓ 命中（0 ⇒ 单字不动，存在最小扰动尺度）"
    elif n <= 2:
        v = "⚠️ 1~2（部分字符敏感，逐个登记见下）"
    else:
        v = "⛔ 3（单字就翻 ⇒ 字符级混沌）"
    print(f"  W1 相对 M0 翻转的臂数 = {n}/3（押 0）⇒ {v}")
    for a, cells in flipped:
        print(f"     {a} {DIFF[a]}：" + "，".join(f"{t} {x:.1f}→{y:.1f}" for t, x, y in cells))

    # W2
    ok = True
    for a in ARMS:
        pair = [r for r in RUNS if r.startswith(a) and r in got]
        if len(pair) == 2 and not all(got[pair[0]].get(t) == got[pair[1]].get(t) for t in TASKS):
            ok = False
            print(f"  W2 ⛔ {a} 两跑有翻动")
    print(f"  W2 四臂两跑逐格相同：{'✓（第 9 次确认噪声地板 0）' if ok else '⛔ 见上'}")

    # W3：答案本体逐字节比对
    print("\n## §4 答案本体（`--answers` 新开，纪律 39）")
    a0 = answers(root / "M0a")
    if not a0:
        print("  ⚠️ 没找到 answers.jsonl ⇒ 本节无读数（须登记）")
    else:
        print(f"  M0a 落了 {len(a0)} 题答案")
        for a in flipped:
            aa = answers(root / f"{a[0]}a")
            if not aa:
                print(f"  {a[0]}a：没找到答案本体")
                continue
            for tid, x, y in a[1]:
                t2 = next((k for k in a0 if k.startswith(tid)), None)
                same = None
                if t2 and t2 in aa:
                    same = a0[t2] == aa[t2]
                print(f"  {a[0]} {tid}: 分数 {x:.1f}→{y:.1f}；答案本体"
                      + ("逐字节**不同**" if same is False else
                         "逐字节**相同**" if same is True else "未能配对"))

    print("\n## §5 与 V96 的对照（同一基线，不同扰动幅度）")
    print(f"  V96 六种**多字改写**：g7e1 掉到 0.4 的 4/6，mean 天花板 0.85")
    if "M0" in agg:
        worst = min(agg[a]["mean"] for a in agg)
        print(f"  V97 三种**单字替换**：四臂 mean 区间 {worst:.2f}~{max(agg[a]['mean'] for a in agg):.2f}")


if __name__ == "__main__":
    main()
