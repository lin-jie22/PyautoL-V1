#!/usr/bin/env python3
"""V68 分析器 —— 只读冻结产物，不写回证据路径（纪律 30）。

输入：/tmp/v68stage2/r{1..5}/bench/runs.jsonl + c2/{bench/runs.jsonl,state_dump.jsonl}
用途：判 P1（载荷落点）/ P2（分数）/ P3（rewards_seen 线性）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v68stage2")
NEED = ("g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail", "g7d2_dedupe_keep")


def last(p: Path):
    rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    return rows[-1] if rows else None


def one(p: Path, tag: str):
    d = last(p)
    if d is None:
        print(f"{tag}: 无记录")
        return None
    m = d["measurement"]
    w = d.get("kernel_worldmodel") or {}
    kp = d.get("kernel_plan") or {}
    pt = {t["id"]: t["score"] for t in d["report"]["per_task"]}
    print(f"{tag:14s} mean={m['mean']:.4f}  pred={w.get('predictions')} scored={w.get('scored')} "
          f"hits={w.get('hits')} rate={w.get('hit_rate')} mism={w.get('mismatches')} "
          f"rw_seen={w.get('rewards_seen')} rw_rec={w.get('rewards_recorded')} "
          f"轮={kp.get('rounds')} sig={d.get('caliber_sig')}")
    print(f"{'':14s} 逐题 " + "  ".join(f"{k.split('_')[0]}={v:.3f}" for k, v in pt.items()))
    return {"d": d, "pt": pt, "w": w, "mean": m["mean"]}


print("=" * 78)
print("V68 阶段 2 —— 5 连跑累积（4 题，失败成族）+ C2 state-dump")
print("=" * 78)
runs = []
for i in range(1, 6):
    r = one(ROOT / f"r{i}" / "bench" / "runs.jsonl", f"r{i}")
    if r:
        runs.append(r)
c2 = one(ROOT / "c2" / "bench" / "runs.jsonl", "C2(dump)")

print()
print("--- P3：rewards_seen 是否线性（押 +4/跑）---")
seq = [r["w"].get("rewards_seen") for r in runs]
print("  ", seq, " 差分", [b - a for a, b in zip(seq, seq[1:])] if len(seq) > 1 else "-")

print()
print("--- P2：mean 与逐题是否逐格相同 ---")
means = {r["mean"] for r in runs}
print("  五跑 mean 集合:", means, " ⇒ 全等" if len(means) == 1 else " ⇒ 不一致")
base = runs[0]["pt"] if runs else {}
diff = []
for i, r in enumerate(runs[1:], 2):
    for k in base:
        if r["pt"].get(k) != base[k]:
            diff.append((i, k, base[k], r["pt"][k]))
print("  相对 r1 的逐格差异:", diff if diff else "无（逐格相同）")

print()
print("--- P1：C2 每道题收到载荷的轮数（关键判据）---")
dump = ROOT / "c2" / "state_dump.jsonl"
if not dump.exists():
    print("  ⛔ 没有 state_dump.jsonl ⇒ P1 无法判")
else:
    rows = [json.loads(l) for l in open(dump, encoding="utf-8") if l.strip()]
    KEY = "历史同类题的外部得分"
    per = {}
    samples = {}
    for r in rows:
        g = r.get("guidance") or ""
        t = r.get("task")
        per.setdefault(t, [0, 0])
        per[t][1] += 1
        if KEY in g:
            per[t][0] += 1
            samples.setdefault(t, g)
    print(f"  {'题':<24} {'轮数':>4} {'收到载荷':>8}")
    for t in NEED:
        got, tot = per.get(t, [0, 0])
        print(f"  {t:<24} {tot:>4} {got:>8}  {'← 命中' if got else ''}")
    print()
    print("  载荷原文样例（第一条）:")
    for t, g in list(samples.items())[:2]:
        print(f"  [{t}]")
        for line in g.splitlines()[:6]:
            print("     ", line)
