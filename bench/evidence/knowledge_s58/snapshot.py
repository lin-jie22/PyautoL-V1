#!/usr/bin/env python3
"""★7w 读数快照：五臂 runs.jsonl 固化，**强制行数断言**。"""
import sys

BASE = {
    "A":  ("/Users/linshaojie/V1/bench_out/runs.jsonl", 4, 62),
    "B0": ("/tmp/v1_7w_wt_b0/bench_out/runs.jsonl", 4, 0),
    "W1": ("/tmp/v1_7w_wt_w1/bench_out/runs.jsonl", 4, 0),
    "W2": ("/tmp/v1_7w_wt_w2/bench_out/runs.jsonl", 4, 0),
    "W3": ("/tmp/v1_7w_wt_w3/bench_out/runs.jsonl", 4, 0),
}

bad = False
for arm, (src, new, before) in BASE.items():
    rows = [l for l in open(src, encoding="utf-8") if l.strip()]
    exp = before + new
    if len(rows) != exp:
        print(f"!! {arm}: 行数 {len(rows)} != 期望 {exp}（已有 {before} + 本次 {new}）")
        bad = True
        continue
    sel = rows[before:]
    out = f"/tmp/v1_7w/snap/runs_{arm}.jsonl"
    open(out, "w", encoding="utf-8").writelines(sel)
    print(f"{arm:2s}: 取 rows[{before}:{exp}] -> {out}（{len(sel)} 行）")
sys.exit(2 if bad else 0)
