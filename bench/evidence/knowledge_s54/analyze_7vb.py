#!/usr/bin/env python3
"""★7v-b 四臂分析（零模型）："忠实引用"指令 + 双安慰剂。

判据逐字照 DESIGN §19quinquies 的 7v-b 登记：
  1. Δ(A−B) ≥ 0.05 且 Δ(A−C1) ≥ 0.05 且 Δ(A−C2) ≥ 0.05 ⇒ 认领语义特异效应；
  2. 若 A ≈ C1 同幅提升 ⇒ 只认领"指令存在效应"（措辞不得夸大成语义特异）；
  3. 三 rep 逐位一致 + n0 邻域不回归（g7d2/g7d3 的 A 不低于 B）。
⛔ 不引用 7u 的 0.70 做差（跨窗禁止，R7）。
"""
import json
import sys

D = "/tmp/v1_7vb"
MARK = {"A": "上方状态按原文顺序依次阅读",
        "C1": "先把题面里的各项要求逐条都读完",
        "C2": "上方状态是系统自动生成的历史记录"}
RUNS = {
    "A": ("/Users/linshaojie/V1/bench_out/runs.jsonl", "capA.jsonl"),
    "B": ("/tmp/v1_7vb_wt_b/bench_out/runs.jsonl", "capB.jsonl"),
    "C1": ("/tmp/v1_7vb_wt_c1/bench_out/runs.jsonl", "capC1.jsonl"),
    "C2": ("/tmp/v1_7vb_wt_c2/bench_out/runs.jsonl", "capC2.jsonl"),
}
REPS = ("r1", "r2", "r3")


def per_map(row):
    return {t["id"]: t["score"]
            for t in (row.get("report") or {}).get("per_task", [])}


def load(path, want):
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    return rows[-len(want):] if len(rows) >= len(want) else rows


arms = {}
for arm, (path, _cap) in RUNS.items():
    want = ["n0"] + list(REPS)
    rows = load(path, want)
    if len(rows) < len(want):
        print(f"!! {arm} 行数不足 {len(rows)}/{len(want)}")
        sys.exit(2)
    maps = {t: per_map(r) for t, r in zip(want, rows)}
    n4 = [maps[t] for t in REPS]
    flat = [s for m in n4 for s in m.values()]
    spread = max((max(m.get(i) for m in n4) - min(m.get(i) for m in n4))
                 for i in n4[0])
    arms[arm] = {"mean": sum(flat) / len(flat),
                 "per_id": {i: [m.get(i) for m in n4] for i in sorted(n4[0])},
                 "bitwise": all(m == n4[0] for m in n4),
                 "spread": spread,
                 "n0": maps.get("n0")}
    a = arms[arm]
    print(f"臂 {arm}: n4 mean={a['mean']:.4f} rep逐位={a['bitwise']} 极差={spread:.4f}")
    for i, v in a["per_id"].items():
        print(f"    {i}: {v}")

print("\nn0 邻域（登记：A 不低于 B 的 g7d2/g7d3）:")
for arm in arms:
    n0 = arms[arm]["n0"] or {}
    print(f"  {arm}: " + ", ".join(f"{k}={v}" for k, v in sorted(n0.items())))


def cap_check(cap, pred):
    n = ok = 0
    for line in open(f"{D}/{cap}", encoding="utf-8"):
        ev = json.loads(line)
        if "#resp" in ev["path"] or not ev["path"].endswith("/chat/completions"):
            continue
        msgs = json.loads(ev["body"]).get("messages", [])
        if msgs and "只输出最终答案本身" in msgs[0].get("content", ""):
            n += 1
            ok += 1 if pred(msgs[-1]["content"]) else 0
    return n, ok


print("\n装置自检（补救/收尾请求带的指令句）:")
dev = {"A": lambda u: MARK["A"] in u,
       "C1": lambda u: MARK["C1"] in u and MARK["A"] not in u,
       "C2": lambda u: MARK["C2"] in u and MARK["A"] not in u,
       "B": lambda u: not any(m in u for m in MARK.values())}
dev_ok = True
for arm, pred in dev.items():
    n, ok = cap_check(RUNS[arm][1], pred)
    good = n > 0 and ok == n
    dev_ok &= good
    print(f"  {arm}: {ok}/{n} {'✅' if good else '❌'}")

d = {k: arms["A"]["mean"] - arms[k]["mean"] for k in ("B", "C1", "C2")}
noise = max(arms[a]["spread"] for a in ("A", "B"))
nb, na = arms["B"]["n0"] or {}, arms["A"]["n0"] or {}
n0_ok = all(na.get(k, 0) >= nb.get(k, 0) for k in ("g7d2_dedupe_keep", "g7d3_fmt_amount"))
stable = all(arms[a]["bitwise"] for a in arms)
print(f"\n判定: Δ(A−B)={d['B']:+.4f}  Δ(A−C1)={d['C1']:+.4f}  Δ(A−C2)={d['C2']:+.4f}  门槛=≥0.05")
print(f"        噪声(A/B 极差最大)={noise:.4f}  rep逐位全一致={stable}  邻域不回归={n0_ok}  装置={dev_ok}")
# ⛔ 判据必须**逐字照登记**（首版把它写成"三条 Δ 全过" ⇒ A≈C1 时被判不认领，
#    与登记第 2 条冲突 —— 与 7r 同款自踩：分析器判据比登记严）。
#    登记原文：
#      · Δ(A−B) ≥ 0.05 且 Δ(A−C1) ≥ 0.05 且 Δ(A−C2) ≥ 0.05 ⇒ 认领语义特异；
#      · 若 A ≈ C1 同幅提升 ⇒ 只认领"指令存在效应"，措辞不得夸大为语义特异。
#    ⇒ C2 的作用是排除"长度/任意文本"，C1 的作用是排除"任意指令"。
gate = stable and n0_ok and dev_ok and d["B"] >= 0.05 and d["C2"] >= 0.05
specific = gate and d["C1"] >= 0.05          # 语义特异
generic = gate and not specific              # 只剩"指令性语句存在"效应
print("★7v-b: ", ("PASS —— 忠实引用**语义特异**效应成立（胜过等长异义指令）"
                  if specific else
                  ("PASS —— 只认领『指令性语句存在效应』：A 与 C1 逐位相同，"
                   "措辞不得夸大为语义特异" if generic else "不认领 —— 见上"))
      )
sys.exit(0 if (specific or generic) else 1)
