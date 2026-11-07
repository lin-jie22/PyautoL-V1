"""列出每个 `suite_sha` 下的逐跑明细（哪条证据、均值、有没有累积）。

只读归档 `runs.jsonl`；用于在开跑前确认「累积效应在历史数据里出现过没有」。
"""
import collections
import glob
import json

rows = collections.defaultdict(list)
for path in glob.glob("bench/evidence/*/runs.jsonl") + glob.glob("bench/evidence/*/*/bench/runs.jsonl"):
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                rep = rec.get("report") or {}
                cal = rec.get("caliber") or {}
                suite = str(cal.get("suite_sha") or "")[:12]
                per = rep.get("per_task") or []
                if not suite or not per:
                    continue
                vals = [x.get("score") for x in per]
                rows[suite].append(
                    (path, sum(vals) / len(vals), len(vals),
                     int((rec.get("kernel_rework") or {}).get("carried") or 0))
                )
    except Exception:
        continue

for suite in ("cee95582ab25", "ade419351103", "2cd880d764e9", "521d93e3a334"):
    items = rows.get(suite) or []
    print(f"===== {suite}  n={len(items)} =====")
    for path, mean, ntasks, carried in items:
        print(f"  {mean:.4f}  tasks={ntasks:2d} carried={carried:2d}  {path}")
