#!/usr/bin/env python3
"""V138 分析：把轨迹判决与 V137 的判据覆盖标签交叉。

COV/BLIND 直接读 V137 的 READINGS_coverage.txt（不重新判定，纪律 32）。
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

EVID = Path(__file__).resolve().parent
COV_FILE = EVID.parent / "v6_s226" / "READINGS_coverage.txt"


def v137_labels() -> dict[str, bool]:
    """site 'mod:line' -> True(COV, v=2 变红) / False(BLIND)"""
    out = {}
    for line in COV_FILE.read_text().splitlines():
        m = re.match(r"\s*([\w.]+):(\d+)\s+\[:2\]\s+->\s+(红|绿)", line)
        if m:
            out[f"{m.group(1)}:{int(m.group(2))}"] = (m.group(3) == "红")
    return out


def main() -> int:
    lab = v137_labels()
    n_cov = sum(1 for v in lab.values() if v)
    print(f"V137 标签: 共 {len(lab)} 站，COV={n_cov}，BLIND={len(lab)-n_cov}")

    rows = []
    for line in (EVID / "readings.jsonl").read_text().splitlines():
        r = json.loads(line)
        if "value" in r:
            continue
        mod, ln, orig, fn = r["site"]
        key = f"{mod}:{ln}"
        r["cov"] = lab.get(key)
        r["key"] = key
        rows.append(r)
    print(f"轨迹读数: {len(rows)} 站（v=2）")
    missing = [r["key"] for r in rows if r["cov"] is None]
    if missing:
        print("⚠️ 无 V137 标签:", missing[:5])

    tab = Counter()
    for r in rows:
        tab[("COV" if r["cov"] else "BLIND", "EFFECT" if r["effect"] else "NOOP")] += 1
    print("\n=== 2×2 ===")
    print(f"{'':8s}{'EFFECT':>8s}{'NOOP':>8s}")
    for c in ("COV", "BLIND"):
        print(f"{c:8s}{tab[(c,'EFFECT')]:8d}{tab[(c,'NOOP')]:8d}")

    # S1
    cov_rows = [r for r in rows if r["cov"]]
    s1_hit = sum(1 for r in cov_rows if r["effect"])
    print(f"\nS1 COV∩EFFECT = {s1_hit}/{len(cov_rows)}  "
          f"{'✅' if s1_hit == len(cov_rows) else '⛔'}")
    for r in cov_rows:
        if not r["effect"]:
            print(f"   ⚠️ COV 却 NOOP: {r['key']} 判据={r['verdict']}")

    # S2
    bl = [r for r in rows if r["cov"] is False]
    be = sum(1 for r in bl if r["effect"])
    bn = len(bl) - be
    print(f"S2 BLIND 内 EFFECT={be} NOOP={bn}  "
          f"{'✅ 两类都非空' if be and bn else '⛔'}")

    # S3
    for key in ("llm.py:118", "llm.py:130"):
        r = next((x for x in rows if x["key"] == key), None)
        if r:
            print(f"S3 {key}: {'NOOP ✅' if not r['effect'] else 'EFFECT ⛔押破'} "
                  f"判据={r['verdict']} diff={r['diff_fns'][:3]}")
        else:
            print(f"S3 {key}: 未测")

    # S4
    r = next((x for x in rows if x["key"] == "features.py:184"), None)
    if r:
        print(f"S4 features.py:184: {'EFFECT ✅' if r['effect'] else 'NOOP ⛔押破'} "
              f"判据={r['verdict']} diff={r['diff_fns']}")

    print("\n=== BLIND 中 EFFECT 的分布 ===")
    per_mod = defaultdict(lambda: [0, 0])
    for r in bl:
        per_mod[r["site"][0]][0] += 1
        if r["effect"]:
            per_mod[r["site"][0]][1] += 1
    for mod, (tot, eff) in sorted(per_mod.items(), key=lambda kv: -kv[1][1]):
        bar = "#" * eff
        print(f"  {mod:18s} {eff:2d}/{tot:2d} {bar}")

    print("\n=== BLIND∩EFFECT 站点清单 ===")
    for r in sorted(bl, key=lambda x: x["key"]):
        if r["effect"]:
            print(f"  {r['key']:22s} 原{r['site'][2]:5d} {r['site'][3]:26s} "
                  f"diff={len(r['diff_fns'])} {r['diff_fns'][:2]}")
    print("\n=== BLIND∩NOOP 站点清单 ===")
    for r in sorted(bl, key=lambda x: x["key"]):
        if not r["effect"]:
            print(f"  {r['key']:22s} 原{r['site'][2]:5d} {r['site'][3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
