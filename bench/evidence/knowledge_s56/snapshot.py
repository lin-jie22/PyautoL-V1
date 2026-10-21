#!/usr/bin/env python3
"""★7v-d 读数快照：把五臂的 runs.jsonl 固化，**强制行数断言**。

⛔ 7v-c 的自踩：活文件会被后续 run 追加，"读最近 N 行"会把新增行当成主窗
   ⇒ 把污染洗成干净。这里改成：跑完立刻快照 + 行数必须精确相等，不等即终止。
"""
import sys

BASE = {  # 臂 -> (源文件, 期望本次新增行数, 本次之前已有行数)
    "A":  ("/Users/linshaojie/V1/bench_out/runs.jsonl", 4, 54),
    "S1": ("/tmp/v1_7vd_wt_s1/bench_out/runs.jsonl", 4, 0),
    "S2": ("/tmp/v1_7vd_wt_s2/bench_out/runs.jsonl", 4, 0),
    "S3": ("/tmp/v1_7vd_wt_s3/bench_out/runs.jsonl", 4, 0),
    "B":  ("/tmp/v1_7vd_wt_b/bench_out/runs.jsonl", 4, 0),
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
    out = f"/tmp/v1_7vd/snap/runs_{arm}.jsonl"
    open(out, "w", encoding="utf-8").writelines(sel)
    print(f"{arm:2s}: 取 rows[{before}:{exp}] -> {out}（{len(sel)} 行）")
sys.exit(2 if bad else 0)
