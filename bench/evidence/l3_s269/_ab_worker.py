#!/usr/bin/env python3
"""V181 两臂工作进程 —— 在一份**指定的** `mw/goals.py` 上做同一件事。

为什么子进程：`coerce()` 是模块级函数，换实现只能换文件。
⚠️ 纪律 65：装置自报「我导入了谁」（`goals.__file__` + sha256），父进程比对，
⛔ 不靠 `sys.path[0]` 猜。

输入（stdin JSON）：{"ledger":..., "goal_log":..., "suite":...}
输出（最后一行 stdout JSON）：
  {"sha":..., "file":..., "n_obs_has_impl":..., "n_obs_has_vec":...,
   "esc": {cell: {"reason":..., "axes":[...], "beh":..., "claim":...}},
   "esc_cells": [...], "propose_gids": [...], "stats": {cell: {...}},
   "evaluate": {gid: verdict}}
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw import goals as G          # noqa: E402


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    d = json.load(sys.stdin)
    rows = G.load(Path(d["ledger"]))
    log_rows = G.load(Path(d["goal_log"]))
    suite_ids = [json.loads(ln)["id"] for ln in
                 Path(d["suite"]).read_text(encoding="utf-8").splitlines() if ln.strip()]

    obs, bad = G.coerce_all(rows)
    esc = G.escalations(log_rows, obs)
    done = G.done_gids(log_rows)
    goals = G.propose(obs, suite_ids, done=done, escalated=sorted(esc))
    ev = {}
    for g in goals:
        v, _rd = G.evaluate(g, obs)
        ev[g.gid] = v
    src = Path(G.__file__).resolve()
    print(json.dumps({
        "sha": sha(src), "file": str(src), "n_bad": bad, "n_obs": len(obs),
        "n_obs_has_impl": sum(1 for o in obs if o.get("impl_sha")),
        "n_obs_has_vec": sum(1 for o in obs if isinstance(o.get("vec"), (list, tuple))),
        "esc_cells": sorted(esc),
        "esc": {c: {"reason": e.get("reason"),
                    "claim": (e.get("movability") or {}).get("claim"),
                    "beh": ((e.get("movability") or {}).get("beh") or {}).get("kind"),
                    "axes": list(G.repair_axes(str(e.get("reason") or ""),
                                               e.get("movability")))}
                for c, e in sorted(esc.items())},
        "propose_gids": [g.gid for g in goals],
        "stats": {c: G.stats(obs, c) for c in sorted({o["task_id"] for o in obs})},
        "evaluate": ev,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
