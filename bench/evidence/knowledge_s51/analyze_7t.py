#!/usr/bin/env python3
"""★7t 三臂分析（零模型）：语义记忆呈现结构消融。
判据逐字照 DESIGN §7t 登记：Δ(D−A) ≥ 0.05 ⇒ 认领；=0 ⇒ 不认领转 R-i。"""
import json
import sys

D = "/tmp/v1_7t"
RUNS = {
    "A": ("/Users/linshaojie/V1/bench_out/runs.jsonl", ["r1", "r2"]),
    "D": ("/tmp/v1_7t_wt_d/bench_out/runs.jsonl", ["n0", "r1", "r2"]),
    "B": ("/tmp/v1_7t_wt_b/bench_out/runs.jsonl", ["r1", "r2"]),
}

def per_map(row):
    return {t["id"]: t["score"]
            for t in (row.get("report") or {}).get("per_task", [])}

def load(path, want):
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    return rows[-len(want):] if len(rows) >= len(want) else rows

arms = {}
for arm, (path, want) in RUNS.items():
    rows = load(path, want)
    if len(rows) < len(want):
        print(f"!! {arm} 行数不足 {len(rows)}/{len(want)}"); sys.exit(2)
    maps = {t: per_map(r) for t, r in zip(want, rows)}
    n4 = [maps[t] for t in ("r1", "r2")]
    flat = [s for m in n4 for s in m.values()]
    arms[arm] = {"mean": sum(flat) / len(flat),
                 "per_id": {i: [m.get(i) for m in n4] for i in sorted(n4[0])},
                 "bitwise": n4[0] == n4[1],
                 "n0": maps.get("n0")}
    print(f"臂 {arm}: n4 mean={arms[arm]['mean']:.4f} rep逐位={arms[arm]['bitwise']}")
    for i, v in arms[arm]["per_id"].items():
        print(f"    {i}: {v}")

print("\nD 臂 n0 邻域（登记：只查不回归）:")
if arms["D"]["n0"]:
    for tid, sc in sorted(arms["D"]["n0"].items()):
        print(f"    {tid}: {sc}")

def cap_check(path, pred):
    n = ok = 0
    for line in open(path, encoding="utf-8"):
        ev = json.loads(line)
        if "#resp" in ev["path"]:
            continue
        if not ev["path"].endswith("/chat/completions"):
            continue
        msgs = json.loads(ev["body"]).get("messages", [])
        if msgs and "只输出最终答案本身" in msgs[0].get("content", ""):
            n += 1
            ok += 1 if pred(msgs[-1]["content"]) else 0
    return n, ok

print("\n装置自检（补救请求语义记忆块形态）:")
dev = {"A": lambda u: " ｜ " in u and "[语义记忆]" in u,
       # 实际形态 = "[语义记忆] \n1. ..."（head 后有空格 + block 以 \n 开头）。
       # 首版判据漏了那个空格 ⇒ 0/15 假红（自踩：静态串必须打印核对，不心算）。
       "D": lambda u: "[语义记忆] \n1." in u and " ｜ " not in u,
       "B": lambda u: "[语义记忆]" not in u and "当前状态：" not in u}
dev_ok = True
for arm, pred in dev.items():
    n, ok = cap_check(f"{D}/cap{arm}.jsonl", pred)
    good = n > 0 and ok == n
    dev_ok &= good
    print(f"  {arm}: {ok}/{n} {'✅' if good else '❌'}")

d_da = arms["D"]["mean"] - arms["A"]["mean"]
d_db = arms["D"]["mean"] - arms["B"]["mean"]
print(f"\n判定: Δ(D−A)={d_da:+.4f}  Δ(D−B)={d_db:+.4f}  门槛=Δ(D−A)≥0.05")
claim = d_da >= 0.05 and dev_ok and all(a["bitwise"] for a in arms.values())
print("★7t: ", "PASS —— 呈现结构效应成立（分块编号 > 串接）" if claim else
      "不认领 —— Δ(D−A) 未达门槛，病源不在呈现结构 ⇒ 下一轮转 R-i（过滤）")
sys.exit(0 if claim else 1)
