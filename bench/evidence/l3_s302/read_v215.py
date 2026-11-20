#!/usr/bin/env python3
"""V215（§283）读数：把累积块交给「真正解题的那一端」—— 配对块。

口径（⛔ 先写死再跑，不许事后改）：
  观测单位 = **块**（同一块里 `off` 与 `blocks` 前后脚跑，唯一差别是 `handoff`）；
  判据行    = `t04[0]`（配对 McNemar）+ `t01[1..3]`（非劣）+ 三条装置闸。

⛔ 分母双报（纪律 67/97）：`有效块` = 两臂**都**发生了外包（`kernel_external.calls≥1`）
   的块。没外包的块对 P2 不含信息 —— 但**不许**从总表里删掉，只许并列。
"""
from __future__ import annotations

import json
import sys
from math import comb
from pathlib import Path

D = Path(__file__).resolve().parent
TASKS = ("t01_parse_duration", "t04_merge_ranges")


def fisher(a: int, b: int, c: int, d: int) -> float:
    n = a + b + c + d
    p0 = comb(a + b, a) * comb(c + d, c) / comb(n, a + c)
    tot = 0.0
    for x in range(max(0, a + c - (c + d)), min(a + b, a + c) + 1):
        p = comb(a + b, x) * comb(c + d, a + c - x) / comb(n, a + c)
        if p <= p0 + 1e-12:
            tot += p
    return min(1.0, tot)


def mcnemar(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


base = int((D / "start_runs.txt").read_text().strip())
rows = [json.loads(l) for l in open("bench_out/runs.jsonl", encoding="utf-8") if l.strip()]
new = rows[base:]
order = [l.rstrip("\n").split("\t") for l in open(D / "order.txt", encoding="utf-8") if l.strip()]

print(f"runs.jsonl {base} → {len(rows)}（新增 {len(new)}）   order.txt {len(order)} 行")
if len(new) != len(order):
    print(f"⛔ 行数不匹配：新增 {len(new)} vs order {len(order)} ⇒ 读数不可用")
    sys.exit(1)

# ── P1a：每行 rc + 条数 ───────────────────────────────────────────────────
bad_rc = [o for o in order if o[3] != "rc=0"]
print(f"P1a 调度表 vs 实跑：{len(order)} 行，非零退出 {len(bad_rc)} 次 "
      f"{'✅' if len(order) == 40 and not bad_rc else '❌'}")

# ── 按块聚合 ──────────────────────────────────────────────────────────────
blocks: dict[str, dict[str, dict]] = {}
for o, r in zip(order, new):
    blk, _pos, arm = o[0], o[1], o[2]
    per = {t["id"]: t for t in r["report"]["per_task"]}
    blocks.setdefault(blk, {})[arm] = {
        "run_id": r.get("run_id"),
        "mean": r["measurement"]["mean"],
        "calls": int((r.get("kernel_external") or {}).get("calls") or 0),
        "vec": {tid: list(per[tid]["vec"]) for tid in TASKS if tid in per},
    }
blks = sorted(blocks, key=lambda x: int(x))
both = [b for b in blks if blocks[b].get("off", {}).get("calls", 0) >= 1
        and blocks[b].get("blocks", {}).get("calls", 0) >= 1]
any_call = [b for b in blks if any(blocks[b].get(a, {}).get("calls", 0) >= 1 for a in ("off", "blocks"))]
print(f"P1c 外包覆盖：两臂都外包的块 **{len(both)}/20**；至少一臂外包 {len(any_call)}/20 "
      f"{'✅' if len(both) >= 14 else '❌'}")


def cell(b: str, arm: str, tid: str, idx: int) -> int:
    v = blocks[b].get(arm, {}).get("vec", {}).get(tid) or []
    return int(v[idx]) if len(v) > idx else 0


def combo(arm: str, tid: str, idxs) -> int:
    return sum(cell(b, arm, tid, i) for b in blks for i in idxs)


print("\n── 逐格（全部 20 块，含未外包的块；分母写全）")
for tid, n in (("t01_parse_duration", 6), ("t04_merge_ranges", 6)):
    for i in range(n):
        o = sum(cell(b, "off", tid, i) for b in blks)
        p = sum(cell(b, "blocks", tid, i) for b in blks)
        print(f"   {tid}[{i}]  off {o:2d}/20   blocks {p:2d}/20")

print("\n── 判据")
# P2：t04[0] 配对
b_cnt = sum(1 for k in blks if cell(k, "blocks", "t04_merge_ranges", 0)
            and not cell(k, "off", "t04_merge_ranges", 0))
c_cnt = sum(1 for k in blks if cell(k, "off", "t04_merge_ranges", 0)
            and not cell(k, "blocks", "t04_merge_ranges", 0))
agree = sum(1 for k in blks if cell(k, "blocks", "t04_merge_ranges", 0)
            == cell(k, "off", "t04_merge_ranges", 0))
p2 = mcnemar(b_cnt, c_cnt)
print(f"P2  t04[0] 配对：b(blocks 过/off 不过)={b_cnt}  c(off 过/blocks 不过)={c_cnt}  "
      f"两臂一致 {agree}/20  McNemar p={p2:.4f}  "
      f"{'✅' if b_cnt >= 6 and p2 < 0.05 else '❌'}")
# 同一格在「两臂都外包」的子集上（分母双报）
b2 = sum(1 for k in both if cell(k, "blocks", "t04_merge_ranges", 0)
         and not cell(k, "off", "t04_merge_ranges", 0))
c2 = sum(1 for k in both if cell(k, "off", "t04_merge_ranges", 0)
         and not cell(k, "blocks", "t04_merge_ranges", 0))
print(f"     子集（两臂都外包 n={len(both)}）：b={b2} c={c2} p={mcnemar(b2, c2):.4f}")

# P3：t01[1..3] 非劣
o13 = combo("off", "t01_parse_duration", [1, 2, 3])
p13 = combo("blocks", "t01_parse_duration", [1, 2, 3])
d13 = (p13 - o13) / 60
print(f"P3  t01[1..3]：off {o13}/60  blocks {p13}/60  Δ={d13:+.4f}  "
      f"{'✅' if d13 >= -0.10 else '❌'}")

# 参考量（非判据）
print("\n── 参考（非判据）")
for a in ("off", "blocks"):
    print(f"   {a:6s} 总分均值 {sum(blocks[k][a]['mean'] for k in blks if a in blocks[k]) / len(blks):.4f}"
          f"  外包次数合计 {sum(blocks[k][a]['calls'] for k in blks if a in blocks[k])}")
print(f"   t01 全 6 格：off {combo('off','t01_parse_duration',range(6))}/120 "
      f"blocks {combo('blocks','t01_parse_duration',range(6))}/120")
print(f"   t04 全 6 格：off {combo('off','t04_merge_ranges',range(6))}/120 "
      f"blocks {combo('blocks','t04_merge_ranges',range(6))}/120")
print(f"   Fisher(t01 全格) p={fisher(combo('blocks','t01_parse_duration',range(6)), 120 - combo('blocks','t01_parse_duration',range(6)), combo('off','t01_parse_duration',range(6)), 120 - combo('off','t01_parse_duration',range(6))):.4f}")
print(f"   Fisher(t04 全格) p={fisher(combo('blocks','t04_merge_ranges',range(6)), 120 - combo('blocks','t04_merge_ranges',range(6)), combo('off','t04_merge_ranges',range(6)), 120 - combo('off','t04_merge_ranges',range(6))):.4f}")
