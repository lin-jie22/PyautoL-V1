#!/usr/bin/env python3
"""V70 分析器：两臂逐题分数 / 载荷落位 / 首步计划。

口径全在字段名里（预注册 §3）：
  mean_4tasks / tasks_below_1 / rewards_seen_tasks / first_step_finish_tasks
⛔ 不与 coding_small 0.8611 相减；⛔ 不与 §2197 的 0.8000 相减（Caliber 不同）。

⚠️ 量具说明：`runs.jsonl` 每跑**只有一行**（汇总），逐题分数在 `report.per_task` 里，
`kernel_worldmodel` 也是**每跑一个**（不是每题一个）⇒ P2 只能记「这跑有没有读到载荷」，
记不了「几题读到」。这一格分辨率不够，已写进 RESULTS。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TASKS = ["k01_diff_tree", "k02_encode_cookie", "k03_unflatten", "k04_to_ini"]


def load_run(p: Path) -> dict:
    if not p.exists():
        return {}
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows[-1] if rows else {}


def first_step(arm_dir: Path) -> dict[str, str]:
    """每题**第 1 轮**的候选形状 + 首动作，从 core.log 重建（按 ▶ 任务开始 切段）。"""
    p = arm_dir / "log" / "core.log"
    if not p.exists():
        return {}
    cur = None
    shape: dict[str, str] = {}
    act: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if "▶ 任务开始" in line:
            cur = line.split("▶ 任务开始:", 1)[-1].strip()[:24]
            continue
        if cur is None:
            continue
        m = re.search(r"\[\s*1\]\s+通道=\S+\s+候选=(\d+)\s+种类=(\d+)", line)
        if m and cur not in shape:
            shape[cur] = f"c{m.group(1)}k{m.group(2)}"
            continue
        m = re.search(r"↳ (\w+) ok=", line)
        if m and cur not in act:
            act[cur] = m.group(1)
    return {k: f"{shape.get(k, '?')}:{act.get(k, '?')}" for k in shape}


def key_of(title: str) -> str | None:
    for t in TASKS:
        fn = t.split("_", 1)[1].split("_")[0]
        if fn in title:
            return t
    if "diff_tree" in title:
        return "k01_diff_tree"
    if "encode_cookie" in title:
        return "k02_encode_cookie"
    if "unflatten" in title:
        return "k03_unflatten"
    if "to_ini" in title:
        return "k04_to_ini"
    return None


def arm_report(name: str, arm_dir: Path) -> dict:
    r = load_run(arm_dir / "bench" / "runs.jsonl")
    pt = ((r.get("report") or {}).get("per_task")) or []
    sc = {x["id"]: float(x["score"]) for x in pt if x.get("score") is not None}
    wm = r.get("kernel_worldmodel") or {}
    plan = r.get("kernel_plan") or {}
    fs = {key_of(k) or k: v for k, v in first_step(arm_dir).items()}
    vals = [sc.get(t) for t in TASKS]
    mean = (sum(v for v in vals if v is not None) / len(TASKS)) if any(v is not None for v in vals) else None
    return {
        "arm": name,
        "scores": sc,
        "mean_4tasks": mean,
        "tasks_below_1": sum(1 for v in vals if v is not None and v < 1.0),
        "rewards_seen": wm.get("rewards_seen"),
        "rewards_recorded": wm.get("rewards_recorded"),
        "rewards_skipped_self": wm.get("rewards_skipped_self"),
        "first_step": fs,
        "first_step_finish_tasks": sum(1 for t in TASKS if fs.get(t, "").endswith(":finish")),
        "rounds": plan.get("rounds"),
        "cands_total": plan.get("cands_total"),
        "sig": (r.get("caliber_sig") or "")[:12],
    }


def fmt(d: dict) -> str:
    if not d.get("scores"):
        return f"{d['arm']:6s} （无产物）"
    sc = "  ".join(f"{t.split('_')[0]}={d['scores'].get(t, float('nan')):.2f}" for t in TASKS)
    m = "n/a" if d["mean_4tasks"] is None else f"{d['mean_4tasks']:.4f}"
    return (f"{d['arm']:6s} {sc}  mean_4tasks={m}  tasks_below_1={d['tasks_below_1']}"
            f"  rewards_seen={d['rewards_seen']}  first_finish={d['first_step_finish_tasks']}"
            f"  轮={d['rounds']} 候选={d['cands_total']}  sig={d['sig']}")


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "stage1"
    dirs: list[tuple[str, Path]] = []
    if which in ("stage1", "all"):
        dirs += [(f"C{i}", Path(f"/tmp/v70stage1/c{i}")) for i in (1, 2)]
    if which in ("stage2", "all"):
        dirs += [(f"A{i}", Path(f"/tmp/v70stage2/r{i}")) for i in (1, 2, 3, 4)]
        dirs += [("A5dump", Path("/tmp/v70stage2/c2"))]
    print("=" * 108)
    rows = []
    for name, d in dirs:
        r = arm_report(name, d)
        rows.append(r)
        print(fmt(r))
    print("=" * 108)
    print("首步计划（候选形状:首动作）")
    for r in rows:
        if r.get("scores"):
            print(f"  {r['arm']:6s} " + "  ".join(
                f"{t.split('_')[0]}={r['first_step'].get(t, '?')}" for t in TASKS))
    print("=" * 108)
    c1 = next((r for r in rows if r["arm"] == "C1"), None)
    a4 = next((r for r in rows if r["arm"] == "A4"), None)
    if c1 and a4 and c1["mean_4tasks"] is not None and a4["mean_4tasks"] is not None:
        print(f"P1 Δ(mean_4tasks) A4-C1 = {a4['mean_4tasks'] - c1['mean_4tasks']:+.4f}"
              f"   分辨率：1 条断言 = 0.0500")
    if which in ("stage2", "all"):
        print("P2 rewards_seen 轨迹（读取量）：" +
              "  ".join(f"{r['arm']}={r['rewards_seen']}" for r in rows if r.get("scores")))
        print("P3 first_step_finish_tasks 轨迹：" +
              "  ".join(f"{r['arm']}={r['first_step_finish_tasks']}" for r in rows if r.get("scores")))


if __name__ == "__main__":
    main()
