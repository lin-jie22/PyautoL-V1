#!/usr/bin/env python3
"""V181 STAGE0 —— 两个问题，都在**真装置**上问，不靠读代码推断。

Q1（census）：产品账本 `state/score_ledger.jsonl` 里 `impl_sha` / `vec` 各有几行？
Q2（存在性，纪律 87：判「这条路走不通」前先把那一步跑一遍）：四个 solver 各跑到
    **临时账本**，看谁真的往里写行 —— 参照点 0 行、`llm-direct` >0 行才算
    「不是装置哑火」（纪律 88）。

⛔ 本脚本不碰产品账本（`--ledger` 一律指向 tmpdir）；⛔ 不落答案本体（`--no-answers`）。

用法：python3 bench/evidence/l3_s269/probe_v181_stage0.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LEDGER = ROOT / "state" / "score_ledger.jsonl"
SUITE = ROOT / "bench" / "tasks" / "coding_small.jsonl"


def census(path: Path) -> dict:
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    keys = Counter()
    for r in rows:
        keys.update(r.keys())
    cells = {str(r.get("task_id")) for r in rows}
    both = {str(r.get("task_id")) for r in rows if r.get("impl_sha") and "vec" in r}
    return {"path": str(path), "n_rows": len(rows),
            "n_rows_has_impl": sum(1 for r in rows if r.get("impl_sha")),
            "n_rows_has_vec": sum(1 for r in rows if "vec" in r),
            "n_rows_both": sum(1 for r in rows if r.get("impl_sha") and "vec" in r),
            "n_cells": len(cells), "n_cells_both": len(both),
            "keys": dict(sorted(keys.items())),
            "sessions": sorted({r.get("session") for r in rows}),
            "families": dict(Counter(r.get("family") for r in rows).most_common())}


def arm(solver: str, ledger: Path) -> dict:
    cmd = [sys.executable, str(ROOT / "run.py"), "--bench",
           "--suite", str(SUITE), "--solver", solver,
           "--repeat", "1", "--ledger", str(ledger), "--no-answers",
           "--temperature", "0.9"]
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=1800)
    n = 0
    if ledger.exists():
        n = len([ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()])
    tail = [ln for ln in p.stdout.splitlines() if ln.strip()][-3:]
    return {"solver": solver, "rc": p.returncode, "n_rows_appended": n,
            "ledger_exists": ledger.exists(), "stdout_tail": tail,
            "stderr_tail": p.stderr.strip().splitlines()[-2:] if p.stderr else []}


def main() -> int:
    print("=" * 74)
    print("Q1 产品账本 census")
    print("=" * 74)
    c = census(LEDGER)
    print(json.dumps(c, ensure_ascii=False, indent=1))
    print()

    print("=" * 74)
    print("Q2 四个 solver × 临时账本（谁是零行、谁真写）")
    print("=" * 74)
    out = []
    with tempfile.TemporaryDirectory(prefix="v181_") as td:
        for s in ("oracle", "null", "partial", "llm-direct"):
            r = arm(s, Path(td) / f"{s.replace('-', '_')}.jsonl")
            out.append(r)
            print(f"  {s:<11} rc={r['rc']} 追加 {r['n_rows_appended']} 行"
                  f"  ledger={'有' if r['ledger_exists'] else '无'}")
            for ln in r["stdout_tail"]:
                print(f"      | {ln}")
            if r["stderr_tail"]:
                print(f"      ! {r['stderr_tail']}")
    print()
    summary = {"census": c, "arms": out,
               "zero_model_solvers_write": {r["solver"]: r["n_rows_appended"]
                                            for r in out if r["solver"] != "llm-direct"},
               "llm_direct_writes": next(r["n_rows_appended"] for r in out
                                         if r["solver"] == "llm-direct")}
    outp = Path(__file__).with_name("stage0_v181.json")
    outp.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"读数 ⇒ {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
