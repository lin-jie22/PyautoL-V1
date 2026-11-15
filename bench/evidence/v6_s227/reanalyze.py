#!/usr/bin/env python3
"""V138 重算：剔除「源码派生」函数后的生效性判据。

为什么必须重算：轨迹里含 `mw.loop.harness_sha`（`mw/*.py` 源码指纹）。
任何编辑都会改它 ⇒ 原始判据下 89/89 全是 EFFECT，等于在量「文件变了没有」。
控制变异（往 llm.py 末尾追加一行永不执行的注释）证明：源码派生集合恰好 3 个函数。

本脚本对**同一批已落盘的轨迹**重算，不重跑（轨迹是读数，重算是过滤）。
同时保留原始（含 SRC）判决，供对照与登记。
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

EVID = Path(__file__).resolve().parent
COV_FILE = EVID.parent / "v6_s226" / "READINGS_coverage.txt"


def v137_labels() -> dict[str, bool]:
    out = {}
    for line in COV_FILE.read_text().splitlines():
        m = re.match(r"\s*([\w.]+):(\d+)\s+\[:2\]\s+->\s+(红|绿)", line)
        if m:
            out[f"{m.group(1)}:{int(m.group(2))}"] = (m.group(3) == "红")
    return out


def digest(per: dict, drop: set[str]) -> str:
    return hashlib.sha1("\n".join(
        f"{k}\t{per[k]['n']}\t{per[k]['h']}" for k in sorted(per) if k not in drop
    ).encode()).hexdigest()


def main() -> int:
    bl = json.loads((EVID / "baseline.json").read_text())
    src = set(json.loads((EVID / "src_set.json").read_text())["src"])
    tim = set(json.loads((EVID / "time_set.json").read_text())["hit"])
    drop = set(bl["noise"]) | src | tim
    base_per = bl["base_per_fn"]
    base_exec = digest(base_per, drop)
    base_all = bl["clean"]

    print(f"剔除集合 = 噪声 {len(bl['noise'])} + 源码派生 {len(src)} + 时间量 {len(tim)} = {len(drop)}")
    print(f"基线 calls={bl['calls']}  判据={bl['verdict']}")

    lab = v137_labels()
    rows = []
    for f in sorted(Path("/tmp").glob("tr_*.json")):
        if f.name in ("tr__control.json",):
            continue
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        per = d.get("per_fn")
        if not per:
            continue
        tag = f.name[3:-5]
        m = re.match(r"([\w.]+)_(\d+)$", tag)
        if not m:
            continue
        mod, ln = m.group(1), int(m.group(2))
        key = f"{mod}:{ln}"
        if key not in lab:
            continue
        diffs = sorted(k for k in per if k not in drop and per[k] != base_per.get(k))
        rows.append({
            "key": key, "mod": mod, "ln": ln,
            "verdict": "631/0" if d["exit"] == 0 else "红",
            "calls": d["n_calls"],
            "cov": lab[key],
            "eff_all": d["digest_clean"] != base_all,
            "eff": digest(per, drop) != base_exec,
            "diff": diffs,
        })
    print(f"参与重算的站点: {len(rows)}")

    tab_all = Counter((("COV" if r["cov"] else "BLIND"), ("EFFECT" if r["eff_all"] else "NOOP")) for r in rows)
    tab = Counter((("COV" if r["cov"] else "BLIND"), ("EFFECT" if r["eff"] else "NOOP")) for r in rows)
    print("\n=== 原始判据（含源码指纹，作废）===")
    for c in ("COV", "BLIND"):
        print(f"  {c:6s} EFFECT={tab_all[(c,'EFFECT')]:3d} NOOP={tab_all[(c,'NOOP')]:3d}")
    print("=== 剔除源码派生后 ===")
    for c in ("COV", "BLIND"):
        print(f"  {c:6s} EFFECT={tab[(c,'EFFECT')]:3d} NOOP={tab[(c,'NOOP')]:3d}")

    cov = [r for r in rows if r["cov"]]
    blr = [r for r in rows if not r["cov"]]
    s1 = sum(1 for r in cov if r["eff"])
    print(f"\nS1 COV∩EFFECT = {s1}/{len(cov)}  {'✅' if s1 == len(cov) else '⛔'}")
    for r in cov:
        if not r["eff"]:
            print(f"   ⚠️ COV 却 NOOP: {r['key']}")
    be = sum(1 for r in blr if r["eff"])
    print(f"S2 BLIND: EFFECT={be} NOOP={len(blr)-be}  "
          f"{'✅ 两类都非空' if be and be < len(blr) else '⛔'}")
    for key in ("llm.py:118", "llm.py:130"):
        r = next((x for x in rows if x["key"] == key), None)
        print(f"S3 {key}: " + ("未测" if not r else
              f"{'NOOP ✅' if not r['eff'] else 'EFFECT ⛔押破'} 判据={r['verdict']} diff={r['diff']}"))
    r = next((x for x in rows if x["key"] == "features.py:184"), None)
    if r:
        print(f"S4 features.py:184: {'EFFECT ✅' if r['eff'] else 'NOOP ⛔押破'} "
              f"判据={r['verdict']} diff={r['diff']}")

    print("\n=== BLIND 的模块分布（EFFECT/总） ===")
    pm = defaultdict(lambda: [0, 0])
    for r in blr:
        pm[r["mod"]][0] += 1
        if r["eff"]:
            pm[r["mod"]][1] += 1
    for mod, (tot, e) in sorted(pm.items(), key=lambda kv: (-kv[1][1], kv[0])):
        print(f"  {mod:16s} {e:2d}/{tot:2d}")

    print("\n=== BLIND∩EFFECT（判据绿、行为改了）===")
    for r in sorted(blr, key=lambda x: x["key"]):
        if r["eff"]:
            print(f"  {r['key']:20s} 判据={r['verdict']:6s} diff={r['diff'][:3]}")
    print("\n=== BLIND∩NOOP（连执行都没进）===")
    for r in sorted(blr, key=lambda x: x["key"]):
        if not r["eff"]:
            print(f"  {r['key']:20s} 判据={r['verdict']}")

    (EVID / "analysis.json").write_text(json.dumps(
        {"rows": rows, "src": sorted(src), "noise": bl["noise"],
         "base_exec": base_exec, "base_all": base_all}, ensure_ascii=False, indent=1))
    print("\n落盘 analysis.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
