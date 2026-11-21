#!/usr/bin/env python3
"""V215b（§284）读数：判据逐字沿用 PREREG_v215.md §2，只换装置（_b 文件族）。

与 read_v215.py 的唯一差别：
  ① 读 `order_b.txt`（第 1 次尝试的 `order.txt` 保持原样，当装置失效证据）；
  ② 基线取 `start_runs_b.txt`（=301，跳过第 1 次尝试的 23 行 + 修脚本前的 1 行）；
  ③ **补上 P1b**：`read_v215.py` 漏了这一条（预注册里有、脚本里没有 ⇒ 判据缺失得当场补，
     不是事后改门槛 —— 门槛仍是「两臂 state 各 ≥1 行含 `[外部评审]`」）。
统计量与判据极性一个字未改。
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


base = int((D / "start_runs_b.txt").read_text().strip())
rows = [json.loads(l) for l in open("bench_out/runs.jsonl", encoding="utf-8") if l.strip()]
new = rows[base:]
op = D / "order_b.txt"
order = [l.rstrip("\n").split("\t") for l in open(op, encoding="utf-8") if l.strip()]

print(f"runs.jsonl {base} → {len(rows)}（新增 {len(new)}）   order_b.txt {len(order)} 行")
if len(new) != len(order):
    print(f"⛔ 行数不匹配：新增 {len(new)} vs order {len(order)} ⇒ 读数不可用")
    sys.exit(1)

# ── P1a：每行 rc + 条数 ───────────────────────────────────────────────────
bad_rc = [o for o in order if o[3] != "rc=0"]
print(f"P1a 调度表 vs 实跑：{len(order)} 行，非零退出 {len(bad_rc)} 次 "
      f"{'✅' if len(order) == 40 and not bad_rc else '❌'}")
if bad_rc:
    for o in bad_rc:
        print(f"      ⛔ 非零：blk={o[0]} arm={o[2]} {o[3]} {o[4]}")

# ── P1b：两臂 state 都有评审块（read_v215.py 漏项，这里补）───────────────
print("P1b 两臂 state 含评审块：", end="")
ok_b = True
for a in ("off", "blocks"):
    f = D / f"state_b_{a}.jsonl"
    if not f.exists():
        print(f"  ⛔ 缺 {f.name}", end="")
        ok_b = False
        continue
    n = sum(1 for l in open(f, encoding="utf-8") if l.strip() and "[外部评审]" in l)
    tot = sum(1 for l in open(f, encoding="utf-8") if l.strip())
    print(f"  {a} {n}/{tot}", end="")
    ok_b = ok_b and n >= 1
print(f"  {'✅' if ok_b else '❌'}")

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
print(f"P1c 外包覆盖：两臂都外包的块 **{len(both)}/{len(blks)}**；至少一臂外包 {len(any_call)}/{len(blks)} "
      f"{'✅' if len(both) >= 14 else '❌'}")
print(f"     分母双报：{len(order)} 次运行 / {len(blks)} 块 / 其中两臂都外包 {len(both)} 块"
      f"（未外包的块不删，下面逐格仍按 {len(blks)} 块计）")


def cell(b: str, arm: str, tid: str, idx: int) -> int:
    v = blocks[b].get(arm, {}).get("vec", {}).get(tid) or []
    return int(v[idx]) if len(v) > idx else 0


def combo(arm: str, tid: str, idxs) -> int:
    return sum(cell(b, arm, tid, i) for b in blks for i in idxs)


print(f"\n── 逐格（全部 {len(blks)} 块，含未外包的块）")
for tid, n in (("t01_parse_duration", 6), ("t04_merge_ranges", 6)):
    for i in range(n):
        o = sum(cell(b, "off", tid, i) for b in blks)
        p = sum(cell(b, "blocks", tid, i) for b in blks)
        print(f"   {tid}[{i}]  off {o:2d}/{len(blks)}   blocks {p:2d}/{len(blks)}")

print("\n── 判据")
b_cnt = sum(1 for k in blks if cell(k, "blocks", "t04_merge_ranges", 0)
            and not cell(k, "off", "t04_merge_ranges", 0))
c_cnt = sum(1 for k in blks if cell(k, "off", "t04_merge_ranges", 0)
            and not cell(k, "blocks", "t04_merge_ranges", 0))
agree = sum(1 for k in blks if cell(k, "blocks", "t04_merge_ranges", 0)
            == cell(k, "off", "t04_merge_ranges", 0))
p2 = mcnemar(b_cnt, c_cnt)
print(f"P2  t04[0] 配对：b(blocks 过/off 不过)={b_cnt}  c(off 过/blocks 不过)={c_cnt}  "
      f"两臂一致 {agree}/{len(blks)}  McNemar p={p2:.4f}  "
      f"{'✅' if b_cnt >= 6 and p2 < 0.05 else '❌'}")
b2 = sum(1 for k in both if cell(k, "blocks", "t04_merge_ranges", 0)
         and not cell(k, "off", "t04_merge_ranges", 0))
c2 = sum(1 for k in both if cell(k, "off", "t04_merge_ranges", 0)
         and not cell(k, "blocks", "t04_merge_ranges", 0))
print(f"     子集（两臂都外包 n={len(both)}）：b={b2} c={c2} p={mcnemar(b2, c2):.4f}")

o13 = combo("off", "t01_parse_duration", [1, 2, 3])
p13 = combo("blocks", "t01_parse_duration", [1, 2, 3])
d13 = (p13 - o13) / (3 * len(blks))
print(f"P3  t01[1..3]：off {o13}/{3 * len(blks)}  blocks {p13}/{3 * len(blks)}  Δ={d13:+.4f}  "
      f"{'✅' if d13 >= -0.10 else '❌'}")

print("\n── 参考（非判据）")
for a in ("off", "blocks"):
    ks = [k for k in blks if a in blocks[k]]
    print(f"   {a:6s} 总分均值 {sum(blocks[k][a]['mean'] for k in ks) / len(ks):.4f}"
          f"  外包次数合计 {sum(blocks[k][a]['calls'] for k in ks)}")
print(f"   t01 全 6 格：off {combo('off', 't01_parse_duration', range(6))}/{6 * len(blks)} "
      f"blocks {combo('blocks', 't01_parse_duration', range(6))}/{6 * len(blks)}")
print(f"   t04 全 6 格：off {combo('off', 't04_merge_ranges', range(6))}/{6 * len(blks)} "
      f"blocks {combo('blocks', 't04_merge_ranges', range(6))}/{6 * len(blks)}")
for tid in TASKS:
    N = 6 * len(blks)
    a_ = combo("off", tid, range(6))
    b_ = combo("blocks", tid, range(6))
    print(f"   Fisher({tid}) p={fisher(b_, N - b_, a_, N - a_):.4f}")

print("\n── 逐臂耗时（装置体检，非判据）")
durs = {}
for o in order:
    durs.setdefault(o[2], []).append(int(o[4].rstrip("s")))
for a, v in durs.items():
    print(f"   {a:6s} n={len(v)} 中位 {sorted(v)[len(v) // 2]}s 最大 {max(v)}s")
