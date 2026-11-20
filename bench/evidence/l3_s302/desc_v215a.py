#!/usr/bin/env python3
"""V215 第 1 次尝试（装置失效）的**描述性**读数 —— ⛔ 不作任何命题的证据。

口径：只取 `rc=0` 的 23 次运行；它们按顺序对应 `runs.jsonl[277:]`。
崩溃的 17 次没有 `runs.jsonl` 行，不进任何分母。
"""
import json
from math import comb
from pathlib import Path

D = Path(__file__).resolve().parent
TASKS = ("t01_parse_duration", "t04_merge_ranges")
base = int((D / "start_runs.txt").read_text().strip())
rows = [json.loads(l) for l in open("/Users/linshaojie/V1/bench_out/runs.jsonl", encoding="utf-8") if l.strip()]
new = rows[base:]
order = [l.rstrip("\n").split("\t") for l in open(D / "order.txt", encoding="utf-8") if l.strip()]
ok = [o for o in order if o[3] == "rc=0"]

print(f"order.txt {len(order)} 行，其中 rc=0 **{len(ok)}**；runs.jsonl 新增 **{len(new)}**")
assert len(ok) == len(new), "行数不匹配 ⇒ 读数不可用"

blocks = {}
for o, r in zip(ok, new):
    blk, arm = o[0], o[2]
    per = {t["id"]: t for t in r["report"]["per_task"]}
    blocks.setdefault(blk, {})[arm] = {
        "mean": r["measurement"]["mean"],
        "calls": int((r.get("kernel_external") or {}).get("calls") or 0),
        "vec": {tid: list(per[tid]["vec"]) for tid in TASKS if tid in per},
    }
full = sorted([b for b in blocks if {"off", "blocks"} <= set(blocks[b])], key=int)
print(f"两臂齐全的块 **{len(full)}/20**：{full}")

def cell(b, arm, tid, i):
    v = blocks[b].get(arm, {}).get("vec", {}).get(tid) or []
    return int(v[i]) if len(v) > i else 0

print("\n── 逐格（分母=两臂齐全的块数）")
for tid, n in (("t01_parse_duration", 6), ("t04_merge_ranges", 6)):
    for i in range(n):
        o = sum(cell(b, "off", tid, i) for b in full)
        p = sum(cell(b, "blocks", tid, i) for b in full)
        print(f"   {tid}[{i}]  off {o:2d}/{len(full)}   blocks {p:2d}/{len(full)}")

b_cnt = sum(1 for k in full if cell(k, "blocks", "t04_merge_ranges", 0) and not cell(k, "off", "t04_merge_ranges", 0))
c_cnt = sum(1 for k in full if cell(k, "off", "t04_merge_ranges", 0) and not cell(k, "blocks", "t04_merge_ranges", 0))
agree = sum(1 for k in full if cell(k, "blocks", "t04_merge_ranges", 0) == cell(k, "off", "t04_merge_ranges", 0))
n = b_cnt + c_cnt
p = 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, i) for i in range(min(b_cnt, c_cnt) + 1)) / 2 ** n)
print(f"\n描述：t04[0] 配对 b={b_cnt} c={c_cnt} 两臂一致 {agree}/{len(full)}  McNemar p={p:.4f}")
o13 = sum(cell(b, "off", "t01_parse_duration", i) for b in full for i in (1, 2, 3))
p13 = sum(cell(b, "blocks", "t01_parse_duration", i) for b in full for i in (1, 2, 3))
tot = 3 * len(full)
print(f"描述：t01[1..3] off {o13}/{tot} blocks {p13}/{tot} Δ={(p13-o13)/tot:+.4f}")
print("描述：外包次数 " + "  ".join(
    f"{a}={sum(blocks[b].get(a, {}).get('calls', 0) for b in full)}" for a in ("off", "blocks")))
both = [b for b in full if blocks[b]["off"]["calls"] >= 1 and blocks[b]["blocks"]["calls"] >= 1]
print(f"描述：两臂都外包的块 {len(both)}/{len(full)}")
print("\n⛔ 以上全部只作描述：该次尝试 17 次非零退出 ⇒ P1a 不成立 ⇒ 整轮 INVALID。")
