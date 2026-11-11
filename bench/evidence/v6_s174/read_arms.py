#!/usr/bin/env python3
"""读 V85 三臂逐题分数 + 每跑 state 里的 `[语义记忆]` 块（真读数，不是日志标签）。"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path("/tmp/v85")
TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]


def main() -> int:
    out: dict = {"arms": {}, "tasks": TASKS}
    for tag in "ABC":
        cell: dict = {}
        for d in sorted(ROOT.glob(f"{tag}/r*")):
            rj = d / "bench/runs.jsonl"
            if not rj.exists():
                continue
            rows = [json.loads(x) for x in rj.read_text(encoding="utf-8").splitlines() if x.strip()]
            per = {}
            for r in rows:
                for t in (r.get("report") or {}).get("per_task") or []:
                    per[t["id"]] = t.get("score")
            # state 里的块（真读数）
            blocks = []
            sd = d / "state_dump.jsonl"
            if sd.exists():
                for line in sd.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    s = json.loads(line).get("state", "")
                    m = re.search(r"\[语义记忆\](.*?)(?=\n\[|\Z)", s, re.S)
                    blocks.append(len(m.group(1).strip()) if m else 0)
            cell[d.name] = {"per_task": per, "state_mem_chars": blocks}
        out["arms"][tag] = cell

    print("| 臂/跑 | " + " | ".join(TASKS) + " | 满分题数 | state 块字数 |")
    print("|---" * (len(TASKS) + 3) + "|")
    for tag in "ABC":
        for rep, c in out["arms"][tag].items():
            sc = [c["per_task"].get(t) for t in TASKS]
            nfull = sum(1 for s in sc if s is not None and s >= 0.999)
            print(f"| {tag}/{rep} | " + " | ".join("—" if s is None else f"{s:g}" for s in sc)
                  + f" | **{nfull}/4** | {c['state_mem_chars']} |")
    # 按臂汇总
    print()
    for tag in "ABC":
        cells = [c["per_task"] for c in out["arms"][tag].values()]
        for t in TASKS:
            vals = [c.get(t) for c in cells]
            print(f"  {tag} {t:22s} {vals}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
