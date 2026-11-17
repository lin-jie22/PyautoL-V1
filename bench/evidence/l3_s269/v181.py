#!/usr/bin/env python3
"""V181 判据台 —— 真账本上的逐格表 + 部署路径 vs 裸行路径的差。

三个口径（同一个真实账本，⛔ 不合成任何行）：

  ① `raw`     ：账本原始行直接喂 `goals.escalations()`（`scoreinfo` 的完整输入）
  ② `deploy`  ：`G.coerce_all(G.load(ledger))` 之后再喂 —— 这就是 `run.py --goals`
                真正做的事（归一化闸在中间）
  ③ 变异两臂  ：`raw` 上内存里分别抹掉 `vec` / `impl_sha`（⛔ 不写盘）

①与②的差 = `coerce()` 丢掉了什么（X1/X2）；③ 证明读数看得见缺席（纪律 88）。

用法：python3 bench/evidence/l3_s269/v181.py --tag pre
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw import goals as G            # noqa: E402
from mw import scoreinfo as SI       # noqa: E402

LEDGER = ROOT / "state" / "score_ledger.jsonl"
GOAL_LOG = ROOT / "state" / "goal_ledger.jsonl"
GOAL_OBS = ROOT / "state" / "goal_obs.jsonl"
SUITE = ROOT / "bench" / "tasks" / "coding_small.jsonl"
MIN_OBS = G.MIN_OBS


def ledger_census(rows: list[dict]) -> dict:
    cells = sorted({str(r.get("task_id")) for r in rows})
    both = sorted({str(r.get("task_id")) for r in rows
                   if r.get("impl_sha") and isinstance(r.get("vec"), (list, tuple))})
    per_cell = {}
    for c in cells:
        rs = [r for r in rows if str(r.get("task_id")) == c]
        impls = {str(r.get("impl_sha")) for r in rs if r.get("impl_sha")}
        per_cell[c] = {"n_rows": len(rs),
                       "n_has_impl": sum(1 for r in rs if r.get("impl_sha")),
                       "n_has_vec": sum(1 for r in rs if isinstance(r.get("vec"), (list, tuple))),
                       "n_impls": len(impls)}
    return {"n_rows": len(rows),
            "n_rows_has_impl": sum(1 for r in rows if r.get("impl_sha")),
            "n_rows_has_vec": sum(1 for r in rows if isinstance(r.get("vec"), (list, tuple))),
            "n_rows_both": sum(1 for r in rows
                               if r.get("impl_sha") and isinstance(r.get("vec"), (list, tuple))),
            "n_cells": len(cells), "n_cells_both": len(both),
            "cells_both": both, "per_cell": per_cell}


def cell_table(rows: list[dict], cells: list[str], reasons: dict | None = None) -> dict:
    """逐格：分数级 + 行为级 + 轴（全部走真模块，⛔ 不重写判据）。

    `reasons` 给的是**真** `escalations()` 判出的 reason（`CRITERION_UNREACHABLE` /
    `STUCK_MEANS`）；格不在其中的（没转运维）用空串 —— 这样 `repair_axes` 的
    两个入口（reason / overlay）都与部署路径同源，⛔ 不靠本脚本自己编 reason。
    """
    reasons = reasons or {}
    out = {}
    for c in cells:
        pool_b, cnt = SI.beh_pool_from_rows(rows, cell=c)
        beh = SI.behavioral(pool_b) if pool_b else {"kind": SI.KIND_UNKNOWN,
                                                   "reason": "NO_POOL"}
        mov = SI.movability(rows, cell=c)
        overlay = None if mov.get("label") == SI.KIND_UNKNOWN else dict(mov)
        if overlay is not None and beh.get("kind") != SI.KIND_UNKNOWN:
            overlay["beh"] = {"kind": beh["kind"], "n_behaviors": beh.get("n_behaviors"),
                              "claim": beh.get("claim")}
        reason = str(reasons.get(c) or "")
        axes = list(G.repair_axes(reason, overlay)) if overlay else list(G.repair_axes(reason))
        out[c] = {"reason": reason or None,
                  "score_label": mov["label"], "score_reason": mov["reason"],
                  "n_impls": mov["n_impls"], "n_scores": mov["n_scores"],
                  "score_claim": mov["claim"],
                  "bucket_value": (min(s for _, s in SI.pool_from_rows(rows, cell=c)[0])
                                   if SI.pool_from_rows(rows, cell=c)[0] else None),
                  "beh_kind": beh.get("kind"), "beh_n_behaviors": beh.get("n_behaviors"),
                  "beh_claim": beh.get("claim"), "beh_reason": beh.get("reason"),
                  "pool_counts": cnt, "axes": axes}
    return out


def deploy_view(rows: list[dict], log_rows: list[dict]) -> dict:
    """`run.py --goals` 的两条路径：裸行 vs coerce 之后。"""
    obs_deploy, _bad = G.coerce_all(rows)
    res = {}
    for name, obs in (("raw", rows), ("deploy", obs_deploy)):
        esc = G.escalations(log_rows, obs)
        res[name] = {
            "n_esc": len(esc),
            "n_esc_with_overlay": sum(1 for e in esc.values() if e.get("movability")),
            "n_esc_with_beh": sum(1 for e in esc.values()
                                  if (e.get("movability") or {}).get("beh")),
            "n_esc_with_claim": sum(1 for e in esc.values()
                                    if (e.get("movability") or {}).get("claim")),
            "n_esc_with_ruler": sum(1 for e in esc.values()
                                    if "RULER" in G.repair_axes(
                                        str(e.get("reason") or ""), e.get("movability"))),
            "n_esc_with_criterion": sum(1 for e in esc.values()
                                        if "CRITERION" in G.repair_axes(
                                            str(e.get("reason") or ""), e.get("movability"))),
            "cells": sorted(esc),
            "axes": {c: list(G.repair_axes(str(e.get("reason") or ""), e.get("movability")))
                     for c, e in sorted(esc.items())},
            "beh_kind": {c: ((e.get("movability") or {}).get("beh") or {}).get("kind")
                         for c, e in sorted(esc.items())},
        }
        # obs 里到底有没有那两个键 —— 这一条是 X1/X2 的直接原因
        res[name]["n_obs_has_impl"] = sum(1 for o in obs if o.get("impl_sha"))
        res[name]["n_obs_has_vec"] = sum(1 for o in obs if isinstance(o.get("vec"), (list, tuple)))
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="pre", help="这次读数的标签（pre/post）")
    args = ap.parse_args()

    rows = G.load(LEDGER)
    log_rows = G.load(GOAL_LOG)
    obs_g, _ = G.coerce_all(G.load(GOAL_OBS))
    log_rows = list(log_rows)

    cen = ledger_census(rows)
    cells = sorted(cen["per_cell"])
    suite_ids = [t["id"] for t in (json.loads(ln) for ln in
                                   SUITE.read_text(encoding="utf-8").splitlines() if ln.strip())]
    tbl = cell_table(rows, cells, {c: e.get("reason")
                                   for c, e in G.escalations(log_rows, rows).items()})
    dep = deploy_view(rows, log_rows)

    # ── 变异两臂（内存，⛔ 不写盘）──────────────────────────────────────
    no_vec = [{k: v for k, v in r.items() if k != "vec"} for r in rows]
    no_impl = [{k: v for k, v in r.items() if k != "impl_sha"} for r in rows]
    mut = {"no_vec": cell_table(no_vec, cells, {c: v["reason"] for c, v in tbl.items()}),
           "no_impl": cell_table(no_impl, cells, {c: v["reason"] for c, v in tbl.items()})}
    mut_sum = {}
    for arm, t in mut.items():
        mut_sum[arm] = {
            "n_cells_ruler": sum(1 for v in t.values() if "RULER" in v["axes"]),
            "n_cells_criterion": sum(1 for v in t.values() if "CRITERION" in v["axes"]),
            "n_cells_beh_known": sum(1 for v in t.values() if v["beh_kind"] not in (None, SI.KIND_UNKNOWN)),
        }

    print("=" * 78)
    print(f"V181 [{args.tag}] 账本 {LEDGER.name}：{cen['n_rows']} 行 / {cen['n_cells']} 格"
          f"｜行有 impl={cen['n_rows_has_impl']} vec={cen['n_rows_has_vec']}"
          f" 两样={cen['n_rows_both']}｜格两样={cen['n_cells_both']}")
    print("=" * 78)
    print(f"{'格':<22}{'行':>3}{'实现':>5}{'分数级':>10}{'claim':>16}"
          f"{'行为级':>10}{'类数':>5}{'bcl':>6}  轴")
    for c, v in tbl.items():
        print(f"{c:<22}{cen['per_cell'][c]['n_rows']:>3}{v['n_impls'] or 0:>5}"
              f"{v['score_label']:>10}{str(v['score_claim'] or '-'):>16}"
              f"{str(v['beh_kind']):>10}{v['beh_n_behaviors'] or 0:>5}"
              f"{str(v['beh_claim'] or '-'):>6}  {v['axes']}")
    print()
    for name in ("raw", "deploy"):
        d = dep[name]
        print(f"[{name:<6}] obs 有 impl={d['n_obs_has_impl']} vec={d['n_obs_has_vec']}"
              f"｜转运维 {d['n_esc']} 格｜overlay {d['n_esc_with_overlay']}"
              f"｜beh {d['n_esc_with_beh']}｜claim {d['n_esc_with_claim']}"
              f"｜RULER {d['n_esc_with_ruler']}｜CRITERION {d['n_esc_with_criterion']}")
    print()
    print(f"变异 no_vec  ：RULER {mut_sum['no_vec']['n_cells_ruler']}"
          f" CRITERION {mut_sum['no_vec']['n_cells_criterion']}"
          f" 行为级非UNKNOWN {mut_sum['no_vec']['n_cells_beh_known']}")
    print(f"变异 no_impl ：RULER {mut_sum['no_impl']['n_cells_ruler']}"
          f" CRITERION {mut_sum['no_impl']['n_cells_criterion']}"
          f" 行为级非UNKNOWN {mut_sum['no_impl']['n_cells_beh_known']}")
    print()
    summary = {"tag": args.tag, "ledger": str(LEDGER), "census": cen,
               "suite": {"name": SUITE.stem, "ids": suite_ids},
               "cells": tbl, "deploy": dep, "mutants": mut_sum,
               "totals": {
                   "n_cells": len(cells),
                   "n_cells_both": cen["n_cells_both"],
                   "n_impls_min": min((v["n_impls"] or 0) for v in tbl.values()),
                   "n_impls_max": max((v["n_impls"] or 0) for v in tbl.values()),
                   "n_cells_score_constant": sum(1 for v in tbl.values()
                                                 if v["score_label"] == SI.KIND_CONSTANT),
                   "n_cells_score_coarse": sum(1 for v in tbl.values()
                                               if v["score_label"] == SI.KIND_COARSE),
                   "n_cells_beh_coarse": sum(1 for v in tbl.values()
                                             if v["beh_kind"] == SI.KIND_COARSE),
                   "n_cells_beh_known": sum(1 for v in tbl.values()
                                            if v["beh_kind"] not in (None, SI.KIND_UNKNOWN)),
                   "n_cells_ruler": sum(1 for v in tbl.values() if "RULER" in v["axes"]),
                   "n_cells_criterion": sum(1 for v in tbl.values() if "CRITERION" in v["axes"]),
               }}
    outp = Path(__file__).with_name(f"readings_v181_{args.tag}.json")
    outp.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"读数 ⇒ {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
