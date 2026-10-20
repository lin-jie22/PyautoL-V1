#!/usr/bin/env python3
"""★7r 三臂分析（零模型）：Δ_D 双对照判定 + 装置自检 + 邻域复核。
判据全部出自 DESIGN.md §19bis 事前登记，脚本只做机械核对。"""
import json
import re
import sys

D = "/tmp/v1_7r"
RUNS = {  # 臂 → (runs.jsonl 路径, 期望行数按序)
    "A": ("/Users/linshaojie/V1/bench_out/runs.jsonl", ["n0", "r1", "r2"]),
    "B": ("/tmp/v1_7r_wt/bench_out/runs.jsonl", ["n0", "r1", "r2"]),
    "C": ("/tmp/v1_7r_wt_c/bench_out/runs.jsonl", ["r1", "r2"]),
}

def load_rows(path, want):
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    return rows[-len(want):] if len(rows) >= len(want) else rows

def per_map(row):
    return {t["id"]: t["score"] for t in (row.get("report") or {}).get("per_task", [])}

def mean(scores):
    return sum(scores) / len(scores) if scores else float("nan")

# ── 1) 臂级读数 ──────────────────────────────────────────────
arms = {}
for arm, (path, want) in RUNS.items():
    rows = load_rows(path, want)
    if len(rows) < len(want):
        print(f"!! {arm} 臂 runs 行数不足: {len(rows)}/{len(want)}")
        sys.exit(2)
    maps = {tag: per_map(row) for tag, row in zip(want, rows)}
    n4 = [maps[t] for t in ("r1", "r2")]
    all_ids = sorted(set().union(*[set(m) for m in n4]))
    per_id = {i: [m.get(i) for m in n4] for i in all_ids}
    flat = [s for m in n4 for s in m.values()]
    arms[arm] = {
        "n4_mean": mean(flat),
        "per_id": per_id,
        "reps_bitwise": all(maps["r1"] == maps["r2"] for _ in [0]),
        "n0": per_map(rows[0]) if "n0" in want else None,
    }
    print(f"臂 {arm}: n4 mean={arms[arm]['n4_mean']:.4f}  "
          f"rep逐位一致={arms[arm]['reps_bitwise']}")
    for i in all_ids:
        print(f"    {i}: {[repr(s) for s in per_id[i]]}")

# ── 2) 邻域复核（登记原文：K>0 格不受影响 = g7d2/g7d3 两臂一致；──
# ──    B 不变差；A 不回归。K=0 格 A>B = 泛化观察，不计入闸门）────
print("\n邻域复核（n0；K>0 格 = g7d2/g7d3）:")
n0_ok = True
K_POS = {"g7d2_dedupe_keep", "g7d3_fmt_amount"}   # 7o 实测的 K>0 格
if arms["A"]["n0"] and arms["B"]["n0"]:
    for tid in sorted(set(arms["A"]["n0"]) | set(arms["B"]["n0"])):
        a, b = arms["A"]["n0"].get(tid), arms["B"]["n0"].get(tid)
        if tid in K_POS:
            same = (a is not None and b is not None and abs(a - b) <= 0.05)
            n0_ok &= same
            print(f"  {tid}: A={a} B={b} {'✅一致' if same else '❌回归'}")
        else:
            gen = (a is not None and b is not None and a >= b)
            print(f"  {tid}: A={a} B={b} {'📈泛化提升' if gen else '(观察)'}")
else:
    n0_ok = False
    print("  !! 缺 n0 行")

# ── 3) 装置自检（捕获里的补救/收尾请求）──────────────────────
ANS_SYS = "只输出最终答案本身"
def check_cap(path, expect):
    n = ok = 0
    for line in open(path, encoding="utf-8"):
        if "#resp" in line:
            continue
        ev = json.loads(line)
        if not ev["path"].endswith("/chat/completions"):
            continue
        msgs = json.loads(ev["body"]).get("messages", [])
        if msgs and ANS_SYS in msgs[0].get("content", ""):
            n += 1
            u = msgs[-1]["content"]
            if expect(u):
                ok += 1
    return n, ok

checks = {
    "A": (lambda u: "[语义记忆]" in u),
    "B": (lambda u: "[语义记忆]" not in u and "当前状态：" not in u),
    "C": (lambda u: "当前状态：" in u and "[语义记忆]" not in u
          and "长度对照文本" in u),
}
print("\n装置自检（补救/收尾请求内容）:")
dev_ok = True
for arm, fn in checks.items():
    n, ok = check_cap(f"{D}/cap{arm}.jsonl", fn)
    good = n > 0 and ok == n
    dev_ok &= good
    print(f"  {arm}: {ok}/{n} {'✅' if good else '❌'}")

# ── 4) 判定（登记门槛：Δ≥0.05 双对照 + rep 一致 + 邻域 + 装置）─
d_ab = arms["A"]["n4_mean"] - arms["B"]["n4_mean"]
d_ac = arms["A"]["n4_mean"] - arms["C"]["n4_mean"]
print(f"\n判定：Δ(A−B)={d_ab:+.4f}  Δ(A−C)={d_ac:+.4f}  "
      f"门槛=双对照≥+0.05")
gate = (d_ab >= 0.05 and d_ac >= 0.05
        and all(a["reps_bitwise"] for a in arms.values())
        and n0_ok and dev_ok)
print("★7r 判读：", "PASS —— H7r 成立，可认领行为 Δ（补救带语义记忆提升交付分）"
      if gate else
      "FAIL/不认领 —— 读数如实登记，见上方逐项")
sys.exit(0 if gate else 1)
