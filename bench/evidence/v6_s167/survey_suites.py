"""按 `suite_sha` 汇总全仓已有读数：还有哪把尺子留着空间。

只读 `bench/evidence/**` 里已归档的 `runs.jsonl`，不跑任何东西。
"""
import collections
import glob
import json
import os

rows = collections.defaultdict(list)
files = glob.glob("bench/evidence/*/runs.jsonl")
files += glob.glob("bench/evidence/*/*/bench/runs.jsonl")
print("候选 runs.jsonl 份数:", len(files))

for path in files:
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
                rows[suite].append((os.path.dirname(path), sum(vals) / len(vals), len(vals)))
    except Exception:
        continue

for suite, items in sorted(rows.items(), key=lambda kv: -len(kv[1])):
    means = [i[1] for i in items]
    uniq = sorted({round(m, 4) for m in means})
    print(f"{suite}  n={len(items):3d} tasks={items[0][2]:2d} "
          f"min={min(means):.4f} max={max(means):.4f} uniq={uniq[:6]}")
