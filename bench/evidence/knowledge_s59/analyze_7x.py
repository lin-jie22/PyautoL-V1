#!/usr/bin/env python3
"""★7x 分析（零模型）：检索的判别力。

判据**逐字照 DESIGN《★7x 登记》**（跑之前写死）：
  · 机制成立 = ① g7d3 三 rep 全 1.0 ② R2 的 n4 mean >= 0.85 ③ 三 rep 逐位一致
  · ① 成立而 ② 不成立 ⇒ 机制认领、收益不认领
  · R1 也成立 ⇒ 分不开"条数"与"排序" ⇒ 不认领机制
  · R2 不成立 ⇒ 检索不是瓶颈，转 verify 判据层
  · M0 ≈ A ⇒ 那一条事实没在做功 ⇒ 本窗所有 Δ 作废
  · A 必须复现 0.7000，否则本窗作废
⛔ 不跨窗做差（R7）。
"""
import glob
import json
import os
import sys

D = "/tmp/v1_7x"
RUNS = {a: f"{D}/snap/runs_{a}.jsonl" for a in ("A", "R1", "R2", "M0")}
ARMS = ("A", "R1", "R2", "M0")
REPS = ("r1", "r2", "r3")
GATE = "g7e2_truncate_tail"          # A 的判别变量（复现闸门）
DISCRIM = "g7d3_fmt_amount"          # 本轮判别题：R1 会拿到错误的 ZK1
THRESH_MEAN = 0.85

MARKS = {"ZK1": "去重惯例", "ZK2": "金额惯例", "ZK3": "开关惯例", "ZK4": "截断惯例"}
TASKS = {"g7d2_dedupe_keep": "dedupe_keep", "g7d3_fmt_amount": "fmt_amount",
         "g7e1_parse_flag": "parse_flag", "g7e2_truncate_tail": "truncate_tail"}
EXPECT_KEY = {"g7d2_dedupe_keep": "ZK1", "g7d3_fmt_amount": "ZK2",
              "g7e1_parse_flag": "ZK3", "g7e2_truncate_tail": "ZK4"}


def per_map(row):
    return {t["id"]: t["score"] for t in (row.get("report") or {}).get("per_task", [])}


def load(path, n):
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    if len(rows) != n:
        print(f"!! {path} 行数 {len(rows)} != 期望 {n} ⇒ 快照取错，终止")
        sys.exit(2)
    return rows


# ---------------------------------------------------------------- 装置自检
def device_check(arm):
    """直接看捕获：每一次请求的 prompt 里，四条惯例各在不在。"""
    counts = {}
    per_task = {t: [] for t in TASKS}
    for p in sorted(glob.glob(f"{D}/cap{arm}_*.jsonl")):
        for line in open(p, encoding="utf-8"):
            ev = json.loads(line)
            if "#resp" in ev["path"] or not ev["path"].endswith("/chat/completions"):
                continue
            msgs = json.loads(ev["body"]).get("messages", [])
            if not msgs:
                continue
            blob = " ".join(m.get("content", "") for m in msgs)
            present = tuple(k for k, v in MARKS.items() if v in blob)
            counts[len(present)] = counts.get(len(present), 0) + 1
            for tid, frag in TASKS.items():
                if frag in msgs[-1].get("content", ""):
                    per_task[tid].append(present)
    return counts, per_task


# ---------------------------------------------------------------- 读数
arms = {}
for arm in ARMS:
    rows = load(RUNS[arm], 4)
    maps = {t: per_map(r) for t, r in zip(("n0",) + REPS, rows)}
    n4 = [maps[t] for t in REPS]
    flat = [s for m in n4 for s in m.values()]
    spread = max(max(m.get(i) for m in n4) - min(m.get(i) for m in n4) for i in n4[0])
    arms[arm] = {
        "mean": sum(flat) / len(flat),
        "per_id": {i: [m.get(i) for m in n4] for i in sorted(n4[0])},
        "bitwise": all(m == n4[0] for m in n4),
        "spread": spread,
        "n0": maps.get("n0"),
    }

print("=" * 78)
print("★7x 读数（四臂同窗，n4 = g7j_n4_heldout）")
print("=" * 78)
hdr = f"{'臂':3s} {'mean':>7s} {'极差':>5s} {'逐位':>4s}  " + \
      "  ".join(f"{i.split('_')[0]:>10s}" for i in sorted(arms['A']['per_id']))
print(hdr)
for arm in ("A", "R1", "R2", "M0"):
    a = arms[arm]
    cells = "  ".join(f"{str(a['per_id'][i]):>10s}" for i in sorted(a["per_id"]))
    print(f"{arm:3s} {a['mean']:7.4f} {a['spread']:5.2f} {'是' if a['bitwise'] else '否':>4s}  {cells}")

print()
print("-" * 78)
print("n0 邻域（不回归检查）")
for arm in ARMS:
    n0 = arms[arm]["n0"]
    print(f"  {arm:3s} n0 mean={sum(n0.values()) / len(n0):.4f}  {n0}")

# ---------------------------------------------------------------- 装置自检输出
print()
print("-" * 78)
print("装置自检：每次请求的 prompt 里带了几条惯例（按 ZK 标记数）")
for arm in ARMS:
    counts, _ = device_check(arm)
    print(f"  {arm:3s} 条数分布 {dict(sorted(counts.items()))}")
for arm in ARMS:
    print()
    print(f"装置自检 {arm}：每题**最后一次请求**带的是哪几条（末次生成决定交付）+ 全序列")
    _, pt = device_check(arm)
    for tid, seq in sorted(pt.items()):
        last = seq[-1] if seq else ()
        clean = last == (EXPECT_KEY[tid],)
        print(f"  {tid:22s} 期望={EXPECT_KEY[tid]} 末次={last} 末次正确={'OK' if clean else 'BAD'}"
              f"  全序列={sorted(set(seq))} 请求数={len(seq)}")

# ---------------------------------------------------------------- 判读
print()
print("=" * 78)
print("按登记判据判读")
print("=" * 78)
gate_ok = arms["A"]["per_id"][GATE] == [1.0, 1.0, 1.0] and abs(arms["A"]["mean"] - 0.70) < 1e-9
print(f"A 窗口闸门（必须 g7e2=[1,1,1] 且 mean=0.7000）："
      f"{'✅ 复现' if gate_ok else '❌ 未复现 ⇒ 本窗作废'}")

m0_dead = abs(arms["M0"]["mean"] - arms["A"]["mean"]) < 1e-9
print(f"M0 ≈ A（记忆块没在做功 ⇒ 全部 Δ 作废）：{'❌ 成立，本窗作废' if m0_dead else '✅ 不成立'}")

r2 = arms["R2"]
c1 = r2["per_id"][DISCRIM] == [1.0, 1.0, 1.0]
c2 = r2["mean"] >= THRESH_MEAN
c3 = r2["bitwise"]
r1_ok = arms["R1"]["per_id"][DISCRIM] == [1.0, 1.0, 1.0] and arms["R1"]["mean"] >= THRESH_MEAN

print(f"① R2 的 g7d3 三 rep 全 1.0：{r2['per_id'][DISCRIM]} → {'✅' if c1 else '❌'}")
print(f"② R2 mean >= {THRESH_MEAN}：{r2['mean']:.4f} → {'✅' if c2 else '❌'}")
print(f"③ R2 三 rep 逐位一致：{'✅' if c3 else '❌'}")
print(f"R1 也成立（分不开条数/排序）：{'❌ 是' if r1_ok else '✅ 否'}")

print()
if not gate_ok:
    print("⇒ 判读：**本窗作废**（A 未复现，窗口已漂移，任何 Δ 不许读）")
elif m0_dead:
    print("⇒ 判读：**本窗作废**（M0 与 A 逐位相同 ⇒ 那条事实根本没在做功）")
elif c1 and c2 and c3 and not r1_ok:
    print("⇒ 判读：**机制成立**（收益来自排序，不是条数）—— 但只能写成"
          "'一个词法重排把 3/4 修成 4/4'，⛔ 不许写成'学会了语义检索'")
elif c1 and not c2:
    print("⇒ 判读：**机制认领、收益不认领**（排序修好了，别的题没跟上 —— 两件事分开写）")
elif r1_ok and c1:
    print("⇒ 判读：**条数/排序未分离**（R1 与 R2 都成立）⇒ 不认领机制")
else:
    print("⇒ 判读：**R2 不成立** ⇒ 检索不是瓶颈 ⇒ K 层回到'模型不用在场知识'，转 verify 判据层")
