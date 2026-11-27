"""V253 Stage A preflight（纯离线，0 模型）：

问一件事：`bench_out/runs.jsonl` 436 行里，有没有**已经存在**的
「qwen2.5:7b + T=0 + spec-inline 39 题」同口径读数？

为什么先问这个：V252 的结论是「题集上累积不可测」。若已经有同口径读数，
就不该重跑；若没有，重跑才是必须的，而且要先量出「有多少格 <1.0」再决定
要不要做 Stage B（否则又是拿错分母开工）。

输出：stdout 表格 + `preflight_v253.json`
"""
import json
import sys
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
RUNS = ROOT / "bench_out" / "runs.jsonl"
OUT = ROOT / "bench/evidence/l17_s323" / "preflight_v253.json"


def load_runs():
    rows = []
    with open(RUNS, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def prov_of(d):
    rep = d.get("report") or {}
    p = rep.get("provenance") or {}
    return {
        "model": p.get("model") or "",
        "llm_kind": p.get("llm_kind") or "",
        "temperature": p.get("temperature"),
        "scorer_sha": ((d.get("caliber") or {}).get("scorer_sha")
                       or ((d.get("caliber") or {}).get("scorer") or "")),
        "candidate": (rep.get("candidate") or "")[:40],
        "fresh": (d.get("caliber") or {}).get("fresh"),
    }


def main():
    rows = load_runs()
    print(f"runs.jsonl 行数={len(rows)}")

    # 每行的口径 + 覆盖格
    info = []
    for d in rows:
        rep = d.get("report") or {}
        pt = rep.get("per_task") or []
        ids = [t.get("id") for t in pt if t.get("id")]
        info.append({
            "run_id": d.get("run_id"),
            "ts": d.get("verdict_at"),
            "verdict": d.get("verdict"),
            "n_cells": len(ids),
            "ids": ids,
            **prov_of(d),
        })

    # 口径分组：按 (model, temperature) 聚合「曾经被量过的格」
    groups = {}
    for it in info:
        key = (it["model"], it["temperature"])
        g = groups.setdefault(key, {"runs": 0, "cells": {}})
        g["runs"] += 1
        for cid in it["ids"]:
            g["cells"].setdefault(cid, 0)
            g["cells"][cid] += 1

    print(f"\n{'model':<14}{'T':>6}{'runs':>6}{'cells':>7}  说明")
    for (model, temp), g in sorted(groups.items(), key=lambda x: -len(x[1]["cells"])):
        print(f"{(model or '?'):<14}{str(temp):>6}{g['runs']:>6}{len(g['cells']):>7}")

    # 主套件 39 题 id（spec-inline）
    task_dir = ROOT / "bench" / "tasks"
    skip = {"g7d_spec_incomplete.jsonl"}   # V252：7 题惯例不可得，不进分母
    target = {}
    for p in sorted(task_dir.glob("*.jsonl")):
        if p.name in skip:
            continue
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            target[r["id"]] = p.stem
    print(f"\nspec-inline 目标格数={len(target)}")

    # 单一口径下，覆盖了多少目标格
    print(f"\n{'model':<14}{'T':>6}{'covered':>9}/39  缺的格（前 8）")
    best = None
    for (model, temp), g in groups.items():
        cov = [c for c in target if c in g["cells"]]
        if best is None or len(cov) > best[0]:
            best = (len(cov), model, temp)
        miss = [c for c in target if c not in g["cells"]]
        print(f"{(model or '?'):<14}{str(temp):>6}{len(cov):>9}      {','.join(miss[:8])}")

    print(f"\n覆盖最多：{best[1]} T={best[2]} ⇒ {best[0]}/{len(target)}")
    verdict = "ALREADY_MEASURED" if best and best[0] >= len(target) else "NEED_RUN"
    print(f"VERDICT={verdict}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({
            "runs": len(rows),
            "target_cells": sorted(target),
            "target_n": len(target),
            "groups": {f"{m}|{t}": {"runs": g["runs"], "cells": sorted(g["cells"])}
                       for (m, t), g in groups.items()},
            "best_cover": {"model": best[1], "temperature": best[2], "n": best[0]},
            "verdict": verdict,
        }, fh, ensure_ascii=False, indent=2)
    print(f"落盘 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
