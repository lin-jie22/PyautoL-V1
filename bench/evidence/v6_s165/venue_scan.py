#!/usr/bin/env python3
"""「收尾口出现在哪些题集里」—— 跨全仓既有产物的普查（V76 的前置，不是新探针）。

为什么需要：V74 的 D 格复合臂**只作用于收尾交付**那条路。要复现它、或要判断
「这条结论能不能外推」，就必须先知道**哪些题集的跑批里收尾真的触发过**。
⛔ 不造题集、不造探针：只读 `bench/evidence/*/runs*.jsonl`。

口径（写进字段名，纪律 4）：
  `n_runs`          该题集在证据里的**跑数**（每份 runs 文件一行 = 一跑）
  `runs_with_close` 其中 `kernel_closeout.triggered > 0` 的跑数
  `close_total`     `triggered` 之和（一次收尾 = 一条）
  `mean_set`        该题集出现过的 `mean` 取值集合（判断确定性）

题集身份用 `caliber.suite_sha`（内容哈希，见 `mw/bench.py:442`），
不按文件名 —— 同名的重复跑（`--repeat`）会得到不同 sha，这正是要分开的。
"""
import collections
import glob
import json
import sys


def main():
    rows = []
    for f in glob.glob("bench/evidence/*/runs*.jsonl"):
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            cal = rec.get("caliber") or {}
            close = rec.get("kernel_closeout") or {}
            per_task = (rec.get("report") or {}).get("per_task") or []
            ids = tuple(sorted(x.get("id") for x in per_task if x.get("id")))
            rows.append({
                "sha": str(cal.get("suite_sha"))[:12],
                "ids": ids,
                "triggered": int(close.get("triggered") or 0),
                "mean": (rec.get("measurement") or {}).get("mean"),
            })

    grouped = collections.defaultdict(list)
    for r in rows:
        grouped[(r["sha"], r["ids"])].append(r)

    print(f"总跑数 {len(rows)}   题集组数 {len(grouped)}\n")
    print(f"{'suite_sha':14s} {'n_runs':>6} {'runs_with_close':>15} {'close_total':>11} "
          f"{'题数':>4} {'mean 取值':>22}  前 4 个题号")
    for (sha, ids), group in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        n = len(group)
        with_close = sum(1 for x in group if x["triggered"] > 0)
        total = sum(x["triggered"] for x in group)
        means = sorted({round(m, 3) for m in (x["mean"] for x in group) if m is not None})
        shown = ",".join(f"{m:g}" for m in means[:4]) + ("…" if len(means) > 4 else "")
        print(f"{sha:14s} {n:6d} {with_close:15d} {total:11d} {len(set(ids)):4d} "
              f"{shown:>22}  {list(ids[:4])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
