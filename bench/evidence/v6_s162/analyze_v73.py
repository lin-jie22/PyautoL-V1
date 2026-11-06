#!/usr/bin/env python3
"""V73 读数 —— 打回后重写不带状态（`V1_REJECT_CARRY=2`）vs V67 基线（`=1`）。

口径（与 PREREG.md §3/§4 逐条对应，⛔ 不在这里另发明字段）：
  P1  `diff / redelivered`（次 / 次）
  P2  `t04_merge_ranges` 分数（题级）
  P3  `example_failed`（次 / 跑）
  P4  题级 mean（**只登记，不作判决**，见 PREREG §0.3）

⛔ 基线是历史跑批（V67 八跑），不是同轮并行两臂 —— RESULTS 里必须写这句。
"""
import json
import glob
import sys
from pathlib import Path

BASE = Path("bench/evidence/v6_s156")
ARM = Path("/tmp/v73carry")
task_short = "t04_merge_ranges"


def load(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def summarize(runs, label):
    out = {
        "arm": label,
        "n_runs": 0,
        "redelivered": 0,
        "same": 0,
        "diff": 0,
        "dup_skipped": 0,
        "carried": 0,
        "passed_after": 0,
        "example_runs": 0,
        "example_failed": 0,
        "t04_scores": [],
        "means": [],
    }
    for r in runs:
        rw = r.get("kernel_rework") or {}
        kv = r.get("kernel_verify") or {}
        rep = r.get("report") or {}
        if not isinstance(rw, dict) or not rw:
            continue
        out["n_runs"] += 1
        for k in ("redelivered", "same", "diff", "dup_skipped", "carried",
                  "passed_after"):
            out[k] += int(rw.get(k) or 0)
        out["example_runs"] += int(kv.get("example_runs") or 0)
        out["example_failed"] += int(kv.get("example_failed") or 0)
        per = rep.get("per_task") or []
        sc = [float(x.get("score") or 0.0) for x in per if isinstance(x, dict)]
        if sc:
            out["means"].append(sum(sc) / len(sc))
        for x in per:
            if isinstance(x, dict) and x.get("id") == task_short:
                out["t04_scores"].append(float(x.get("score") or 0.0))
    return out


def line(s):
    ratio = (f"{s['diff']}/{s['redelivered']}"
             if s["redelivered"] else "0/0")
    t04 = (",".join(f"{v:.4f}" for v in s["t04_scores"]) or "-")
    mean = (",".join(f"{v:.4f}" for v in s["means"]) or "-")
    return (f"{s['arm']:<14} 跑={s['n_runs']:<2} "
            f"redelivered={s['redelivered']:<3} same={s['same']:<3} "
            f"diff={s['diff']:<3} P1(diff/redel)={ratio:<6} "
            f"dup={s['dup_skipped']:<3} carried={s['carried']:<3} "
            f"after={s['passed_after']:<2} "
            f"ex_run={s['example_runs']:<3} ex_fail={s['example_failed']:<3}\n"
            f"{'':<14} t04={t04}\n{'':<14} mean={mean}")


def main():
    base_runs = []
    for f in sorted(glob.glob(str(BASE / "runs_v67_*.jsonl"))):
        base_runs.extend(load(f))
    arm_runs = []
    for f in sorted(glob.glob(str(ARM / "r*/bench/runs.jsonl"))):
        arm_runs.extend(load(f))
    if not arm_runs:
        print("⛔ 臂 B 还没有产物（或路径不对）")
        return 1
    a = summarize(base_runs, "A=V67(rc=1)")
    b = summarize(arm_runs, "B=V73(rc=2)")
    print(line(a))
    print("-" * 78)
    print(line(b))
    print("-" * 78)
    # 通电自检（PREREG §5 第一步）：日志里有没有那行
    hits = 0
    for f in glob.glob(str(ARM / "r*/log/core.log")):
        try:
            txt = Path(f).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits += txt.count("打回重写：不带状态")
    print(f"通电自检：`↻ 打回重写：不带状态` 出现 {hits} 次"
          f"（{len(glob.glob(str(ARM / 'r*/log/core.log')))} 个日志）")
    if hits == 0 and b["redelivered"] == 0:
        print("⇒ 记 UNKNOWN：开关没走到（PREREG §5.1）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
