#!/usr/bin/env python3
"""V139 汇总：四档 + 调用图对「真盲区」的覆盖率。

四档（V138 只有三档，漏了「崩」）：
    NOOP       轨迹一格没变
    SELF_ONLY  只有自身返回值变
    SPILL_RED  下游变了 且 判据红
    SPILL_GREEN下游变了 且 判据绿（真盲区）
    SPILL_CRASH下游变了 且 **判据根本没跑完**（未捕获异常，EXIT=1）
"""
from __future__ import annotations

import json
import pathlib
import random
import statistics
from collections import deque

EVID = pathlib.Path(__file__).resolve().parent
V138 = EVID.parent / "v6_s227"


def reach(fwd, f):
    seen, dq = {f}, deque([f])
    while dq:
        cur = dq.popleft()
        for n in fwd.get(cur, ()):
            if n not in seen:
                seen.add(n)
                dq.append(n)
    return seen


def main() -> int:
    tiers = json.loads((EVID / "tiers.json").read_text())
    tm, own = tiers["tiers"], tiers["owner"]
    rows = json.loads((V138 / "analysis.json").read_text())["rows"]
    readings = {}
    for line in (V138 / "readings.jsonl").read_text().splitlines():
        d = json.loads(line)
        if "site" in d and "value" not in d:
            readings[d["tag"]] = d
    bykey = {r["key"]: r for r in rows}

    def kind(key: str) -> str:
        r = bykey[key]
        t = tm[key]
        if t != "SPILL":
            return t
        rd = readings.get(key, {})
        # 判据态三值：崩 / 红 / 绿
        if rd.get("exit") not in (0,) and not str(rd.get("verdict", "")).startswith("6"):
            return "SPILL_CRASH"
        return "SPILL_RED" if not str(rd.get("verdict", "")).endswith("/0") else "SPILL_GREEN"

    from collections import Counter
    k_all = Counter(kind(r["key"]) for r in rows)
    print("=== 89 站四档（判据态分 绿/红/崩）===")
    for k in ("SPILL_RED", "SPILL_GREEN", "SPILL_CRASH", "SELF_ONLY", "NOOP"):
        print(f"  {k:12s} {k_all[k]:3d}")
    print("\n按 V137 标签：")
    for tag, want in (("COV", True), ("BLIND", False)):
        sub = [r for r in rows if r["cov"] is want]
        c = Counter(kind(r["key"]) for r in sub)
        print(f"  {tag:6s} n={len(sub):3d} " +
              " ".join(f"{k}={c[k]}" for k in
                       ("SPILL_RED", "SPILL_GREEN", "SPILL_CRASH", "SELF_ONLY", "NOOP")))

    # ---- 调用图（实际边）
    d = json.loads(pathlib.Path("/tmp/v139_edges.json").read_text())
    fwd, rev = {}, {}
    for a, b in d["edges"]:
        fwd.setdefault(a, set()).add(b)
        rev.setdefault(b, set()).add(a)
    universe = set(json.loads((V138 / "baseline.json").read_text())["base_per_fn"])

    def eval_group(keys, label):
        rng = random.Random(20261115)
        agg = {k: [0, 0, 0] for k in ("up", "down", "both")}
        rich = {k: [] for k in agg}
        for key in keys:
            truth = set(bykey[key]["diff"])
            f = own[key]
            preds = {"up": reach(rev, f), "down": reach(fwd, f)}
            preds["both"] = preds["up"] | preds["down"]
            for tag, pred in preds.items():
                hit = len(pred & truth)
                agg[tag][0] += hit
                agg[tag][1] += len(pred)
                agg[tag][2] += len(truth)
                p = hit / len(pred) if pred else 0
                zero = [len(pred & set(rng.sample(sorted(universe), min(len(truth), len(universe)))))
                        / len(pred) if pred else 0 for _ in range(200)]
                z = statistics.mean(zero)
                rich[tag].append(p / z if z else 0)
        print(f"\n=== 调用图（实际边）· {label}（{len(keys)} 站）===")
        for tag in ("up", "down", "both"):
            h, p, t = agg[tag]
            print(f"  {tag:5s} 命中 {h:4d}/{p:5d} 精度 {h/p if p else 0:6.3f} | "
                  f"召回 {h/t if t else 0:6.3f} | 富集中位 {statistics.median(rich[tag]):5.2f}×")
        base = agg["up"][2] / (len(keys) * len(universe))
        print(f"  基线率（|真值|均值 / 331）= {base:.3f}")
        return agg

    all_spill = [r["key"] for r in rows if tm[r["key"]] == "SPILL"]
    blind = [k for k in all_spill if kind(k) == "SPILL_GREEN"]
    eval_group(all_spill, "全部 SPILL")
    eval_group(blind, "真盲区（绿∧SPILL）")

    print("\n=== 真盲区逐站 ===")
    for key in blind:
        truth = set(bykey[key]["diff"])
        f = own[key]
        up = reach(rev, f)
        print(f"  {key:18s} f={f:38s} |T|={len(truth):2d} |up|={len(up):2d} "
              f"命中={len(up & truth)} 漏={sorted(truth - up)[:3]}")

    (EVID / "final.json").write_text(json.dumps({
        "kinds": dict(k_all),
        "per_site": {r["key"]: kind(r["key"]) for r in rows},
        "blind": blind,
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
