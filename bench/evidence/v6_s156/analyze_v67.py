#!/usr/bin/env python3
"""V67 读数提取 —— 只读产物，不重跑、不推断。

用法：python3 bench/evidence/v6_s156/analyze_v67.py [/tmp/v67accum]

判据见同目录 PREREG.md：
  S1 累积被读到（rewards_seen>0）  S2 预测准确率轨迹  S3 mean 轨迹
⛔ `rewards_recorded` 不进判据：它写在另一个 WorldModel 实例上（见 PREREG §4）。
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v67accum")

FIELDS = ("predictions", "scored", "hits", "hit_rate", "mismatches",
          "mismatches_outcome", "unscored", "suppressed",
          "rewards_seen", "rewards_skipped_self", "rewards_skipped_success",
          "rewards_rejected_missing", "rewards_rejected_source",
          "layer_recalled", "layer_abstract")


def load(run: int):
    p = ROOT / f"r{run}" / "bench" / "runs.jsonl"
    if not p.exists():
        return None
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows[-1] if rows else None


def db_counts():
    p = ROOT / "mem" / "memory.db"
    if not p.exists():
        return {}, {}
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    kinds = {k: n for k, n in c.execute(
        "select kind, count(*) from episodes group by kind")}
    srcs = {s: n for s, n in c.execute(
        "select source, count(*) from facts group by source")} if _has(c, "facts") else {}
    c.close()
    return kinds, srcs


def _has(c, t):
    return bool(list(c.execute(
        "select 1 from sqlite_master where type='table' and name=?", (t,))))


def main():
    print("=" * 96)
    print("V67 五连累积曲线 —— 全为本机实测（/tmp/v67accum）")
    print("=" * 96)

    print(f"\n{'跑':>3} {'mean':>7} {'pred':>5} {'scored':>6} {'hits':>5} "
          f"{'hit_rate':>8} {'mism':>5} {'rw_seen':>7} {'sk_self':>7} {'sk_succ':>7} "
          f"{'rec':>4} {'abs':>4} {'轮':>3}")
    print("-" * 96)
    for r in range(1, 6):
        d = load(r)
        if d is None:
            print(f"{r:>3}  (未产出)")
            continue
        w = d.get("kernel_worldmodel") or {}
        kp = d.get("kernel_plan") or {}
        mean = (d.get("measurement") or {}).get("mean")
        hr = w.get("hit_rate")
        print(f"{r:>3} {mean if mean is not None else float('nan'):>7.4f} "
              f"{w.get('predictions',0):>5} {w.get('scored',0):>6} {w.get('hits',0):>5} "
              f"{hr if hr is not None else float('nan'):>8.4f} {w.get('mismatches',0):>5} "
              f"{w.get('rewards_seen',0):>7} {w.get('rewards_skipped_self',0):>7} "
              f"{w.get('rewards_skipped_success',0):>7} "
              f"{w.get('layer_recalled',0):>4} {w.get('layer_abstract',0):>4} "
              f"{kp.get('rounds',0):>3}")

    print("\n逐题（id / score / why 前 48 字）—— 用来查「同题是否逐字相同」")
    print("-" * 96)
    per = {}
    for r in range(1, 6):
        d = load(r)
        if d is None:
            continue
        for t in ((d.get("report") or {}).get("per_task") or []):
            per.setdefault((t.get("id"), t.get("rep")), {})[r] = (t.get("score"), t.get("why"))
    runs = sorted({r for v in per.values() for r in v})
    for (tid, rep), v in sorted(per.items()):
        cells = " ".join(f"{v[r][0]:.4f}" if r in v else "  --  " for r in runs)
        whys = {v[r][1] for r in v if r in v}
        same = "同" if len(whys) == 1 else f"异({len(whys)})"
        print(f"  {tid:<20} rep{rep} {cells}  why:{same}")

    print("\n记忆库（跨跑累积的真凭据）")
    print("-" * 96)
    kinds, srcs = db_counts()
    print("  episodes by kind:", json.dumps(kinds, ensure_ascii=False, sort_keys=True))
    print("  facts   by source:", json.dumps(srcs, ensure_ascii=False, sort_keys=True))

    print("\nPREREG 判据对照")
    print("-" * 96)
    ds = [load(r) for r in range(1, 6)]
    ds = [d for d in ds if d]
    if ds:
        s1 = [((d.get("kernel_worldmodel") or {}).get("rewards_seen") or 0) for d in ds]
        sc = [((d.get("kernel_worldmodel") or {}).get("scored") or 0) for d in ds]
        hi = [((d.get("kernel_worldmodel") or {}).get("hits") or 0) for d in ds]
        mn = [((d.get("measurement") or {}).get("mean")) for d in ds]
        print(f"  S1 rewards_seen 序列 = {s1}  ⇒ {'真' if any(x>0 for x in s1) else '假（边断在检索侧）'}")
        print(f"  S2 (hits,scored) 序列 = {list(zip(hi,sc))} ⇒ "
              f"{'平' if len(set(zip(hi,sc)))==1 else '有变化'}")
        print(f"  S3 mean 序列 = {mn} ⇒ {'平' if len(set(mn))==1 else '有变化'}")


if __name__ == "__main__":
    main()
