#!/usr/bin/env python3
"""★7x 读数快照：四臂 runs.jsonl 固化，**强制行数断言**。

⛔ 教训（7v-c 踩过）：分析器读"最近 N 行"会被后续追加污染成"干净"读数。
   所以必须先快照 + 断言行数，不符即终止（退出码 2）。
"""
import sys

BASE = {
    "A":  ("/Users/linshaojie/V1/bench_out/runs.jsonl", 4, 66),
    "R1": ("/tmp/v1_7x_wt_r1/bench_out/runs.jsonl", 4, 0),
    "R2": ("/tmp/v1_7x_wt_r2/bench_out/runs.jsonl", 4, 0),
    "M0": ("/tmp/v1_7x_wt_m0/bench_out/runs.jsonl", 4, 0),
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
    out = f"/tmp/v1_7x/snap/runs_{arm}.jsonl"
    open(out, "w", encoding="utf-8").writelines(sel)
    print(f"{arm:2s}: 取 rows[{before}:{exp}] -> {out}（{len(sel)} 行）")
sys.exit(2 if bad else 0)
