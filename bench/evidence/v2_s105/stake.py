#!/usr/bin/env python3
"""探索性派生量：把「名字类机会」换算成**格**。

⚠️ **未预注册**。它不改任何判据，只把 `table.json` 里的 `base` 换算成
「这一题最多值几格」，再除以尺子的总格数。作用是把 `share`（按交付物计）与
§72/§73/§74 的读数（按格计）放在同一把算尺上 —— 但**不许**用它替代判据。

换算：对每个 `(suite, task)`，若存在 `NAME_FIXED` 交付物，
     机会格数 = (1 − base) × 该题 test 数（取 `base` 最小的那份，即最坏情况的上界）；
     再除以尺子总格数（题数 × 每题 test 数）。

⛔ 它假设「修复不引入新错误」——所以是**上界**（与 PREREG §2.3 的偏向同一条）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPECGEN = ROOT / "bench/specgen"


def main() -> int:
    table = json.loads((ROOT / "bench/evidence/v2_s105/table.json").read_text(encoding="utf-8"))
    suites = {}
    for p in sorted(SPECGEN.glob("tasks_v*.jsonl")):
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        suites[p.stem] = rows

    print("=== 机会换算成格（探索性，未预注册）===")
    for name, rows in suites.items():
        n_tests = {r["id"]: len(r["tests"]) for r in rows}
        total = sum(n_tests.values())
        fixed = [r for r in table if r["suite"] == name and r["family"] == "NAME_FIXED"]
        if not fixed:
            print(f"  {name:9s} 总格 {total:3d}  机会 0 格（无 NAME_FIXED）")
            continue
        by_task: dict[str, float] = {}
        for r in fixed:
            gain = (1.0 - r["base"]) * n_tests[r["task"]]
            by_task[r["task"]] = max(by_task.get(r["task"], 0.0), gain)
        print(f"  {name:9s} 总格 {total:3d}  命中题 {len(by_task)}/{len(rows)}  "
              f"机会 {sum(by_task.values()):.0f} 格 = {sum(by_task.values()) / total:.1%}")
        for tid, g in sorted(by_task.items()):
            print(f"              {tid}: {g:.0f} 格 / {n_tests[tid]} 格一题")
    return 0


if __name__ == "__main__":
    sys.exit(main())
