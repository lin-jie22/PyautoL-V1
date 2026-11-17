#!/usr/bin/env python3
"""V181 两臂：`coerce()` 透传**前 / 后**，同一份真账本、同一个工作进程。

旧臂 = `git show HEAD:mw/goals.py` 写回文件后**真跑**（不是推理），跑完立刻还原，
并比对还原后的 sha。⛔ 只改 `coerce()` 那一个函数，别的行不动（`AZ142e` 钉）。

用法：python3 bench/evidence/l3_s269/ab_v181.py
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
GOALS = ROOT / "mw" / "goals.py"
WORKER = HERE / "_ab_worker.py"
LEDGER = ROOT / "state" / "score_ledger.jsonl"
GOAL_LOG = ROOT / "state" / "goal_ledger.jsonl"
SUITE = ROOT / "bench" / "tasks" / "coding_small.jsonl"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_arm() -> dict:
    payload = json.dumps({"ledger": str(LEDGER), "goal_log": str(GOAL_LOG),
                          "suite": str(SUITE)})
    p = subprocess.run([sys.executable, str(WORKER)], input=payload,
                       capture_output=True, text=True, cwd=str(ROOT), timeout=600)
    if p.returncode != 0:
        raise SystemExit(f"worker 失败 rc={p.returncode}\n{p.stderr[-2000:]}")
    line = [ln for ln in p.stdout.splitlines() if ln.strip().startswith("{")][-1]
    return json.loads(line)


def main() -> int:
    new_sha_before = sha(GOALS)
    cur = GOALS.read_text(encoding="utf-8")
    old_src = subprocess.run(["git", "show", "HEAD:mw/goals.py"], cwd=str(ROOT),
                             capture_output=True, text=True, check=True).stdout

    arm_new = run_arm()
    print(f"新臂 sha={arm_new['sha'][:12]} file={Path(arm_new['file']).relative_to(ROOT)}")
    assert arm_new["sha"] == new_sha_before, "新臂导入的不是当前文件（纪律 65）"

    GOALS.write_text(old_src, encoding="utf-8")
    try:
        arm_old = run_arm()
    finally:
        GOALS.write_text(cur, encoding="utf-8")
    print(f"旧臂 sha={arm_old['sha'][:12]} file={Path(arm_old['file']).relative_to(ROOT)}")
    assert arm_old["sha"] != new_sha_before, "旧臂 sha 与新臂相同 ⇒ 换文件没生效（装置哑火）"
    back = sha(GOALS)
    print(f"还原后 sha 一致={back == new_sha_before}")

    same_esc = arm_new["esc_cells"] == arm_old["esc_cells"]
    same_gids = arm_new["propose_gids"] == arm_old["propose_gids"]
    same_stats = arm_new["stats"] == arm_old["stats"]
    same_eval = arm_new["evaluate"] == arm_old["evaluate"]
    axes_new = {c: v["axes"] for c, v in arm_new["esc"].items()}
    axes_old = {c: v["axes"] for c, v in arm_old["esc"].items()}
    diff_cells = {c: [axes_old.get(c), axes_new.get(c)]
                  for c in set(axes_old) | set(axes_new) if axes_old.get(c) != axes_new.get(c)}

    print()
    print(f"obs 有 impl：旧 {arm_old['n_obs_has_impl']} → 新 {arm_new['n_obs_has_impl']}")
    print(f"obs 有 vec ：旧 {arm_old['n_obs_has_vec']} → 新 {arm_new['n_obs_has_vec']}")
    print(f"转运维格：旧 {arm_old['esc_cells']}")
    print(f"          新 {arm_new['esc_cells']}  逐格相同={same_esc}")
    print(f"propose gid 逐字相同={same_gids}（{len(arm_new['propose_gids'])} 条）")
    print(f"stats 逐格相同={same_stats}｜evaluate 逐条相同={same_eval}")
    print(f"轴不同的格（{len(diff_cells)}）：")
    for c, (o, n) in sorted(diff_cells.items()):
        print(f"  {c:<22} {o} → {n}   beh 旧={arm_old['esc'][c]['beh']} 新={arm_new['esc'][c]['beh']}")

    out = {"new_sha": new_sha_before, "old_sha": arm_old["sha"],
           "restored_sha": back, "restored_ok": back == new_sha_before,
           "arm_new": arm_new, "arm_old": arm_old,
           "same": {"esc_cells": same_esc, "propose_gids": same_gids,
                    "stats": same_stats, "evaluate": same_eval},
           "axes_diff": diff_cells}
    p = HERE / "ab_v181.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"读数 ⇒ {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
