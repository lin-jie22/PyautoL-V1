#!/usr/bin/env python3
"""★7u 三臂分析（零模型）：补救 prompt 显式消歧指令（R-i-2）。

判据逐字照 DESIGN §19quater 登记（四条全过才认领）：
  1. Δ(A−B) ≥ 0.05 且 Δ(A−C) ≥ 0.05（双对照；C = 等长中性指令 ⇒ 隔离长度效应）
  2. min(Δ) > max(A 窗内极差, B 窗内极差)（信号 > 噪声，7t 教训）
  3. n0 邻域不回归：g7d2/g7d3 的 A 不低于 B（K>0 格不许被补救通道改动拖累）
  4. 装置自检：A 含消歧句 / C 含中性句且不含消歧句 / B 两者皆无
"""
import json
import sys

D = "/tmp/v1_7u"
DISAMBIG = "只采用与本次目标直接相关的那一条"
NEUTRAL = "上方状态按原文顺序依次阅读"
RUNS = {
    "A": ("/Users/linshaojie/V1/bench_out/runs.jsonl", ["n0", "r1", "r2", "r3"]),
    "B": ("/tmp/v1_7u_wt_b/bench_out/runs.jsonl", ["n0", "r1", "r2", "r3"]),
    "C": ("/tmp/v1_7u_wt_c/bench_out/runs.jsonl", ["n0", "r1", "r2", "r3"]),
}
REPS = ("r1", "r2", "r3")


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
        print(f"!! {arm} 行数不足 {len(rows)}/{len(want)}")
        sys.exit(2)
    maps = {t: per_map(r) for t, r in zip(want, rows)}
    n4 = [maps[t] for t in REPS]
    flat = [s for m in n4 for s in m.values()]
    # 窗内极差：逐题跨 rep 的最大抖动（同一题在不同 rep 上的最大差）
    spread = max((max(m.get(i) for m in n4) - min(m.get(i) for m in n4))
                 for i in n4[0])
    rep_means = [sum(m.values()) / len(m) for m in n4]
    arms[arm] = {"mean": sum(flat) / len(flat),
                 "per_id": {i: [m.get(i) for m in n4] for i in sorted(n4[0])},
                 "bitwise": all(m == n4[0] for m in n4),
                 "spread": spread,
                 "rep_means": rep_means,
                 "rep_spread": max(rep_means) - min(rep_means),
                 "n0": maps.get("n0")}
    a = arms[arm]
    print(f"臂 {arm}: n4 mean={a['mean']:.4f} rep均值={[f'{x:.4f}' for x in rep_means]} "
          f"rep逐位={a['bitwise']} 逐题极差={spread:.4f} rep均值极差={a['rep_spread']:.4f}")
    for i, v in a["per_id"].items():
        print(f"    {i}: {v}")

print("\nn0 邻域（K>0 格，登记：A 不低于 B）:")
for arm in ("A", "B", "C"):
    n0 = arms[arm]["n0"] or {}
    print(f"  {arm}: " + ", ".join(f"{k}={v}" for k, v in sorted(n0.items())))


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


print("\n装置自检（补救/收尾请求是否带指令句）:")
dev = {"A": lambda u: DISAMBIG in u and "当前状态：" in u,
       "C": lambda u: NEUTRAL in u and DISAMBIG not in u,
       "B": lambda u: DISAMBIG not in u and NEUTRAL not in u}
dev_ok = True
for arm, pred in dev.items():
    n, ok = cap_check(f"{D}/cap{arm}.jsonl", pred)
    good = n > 0 and ok == n
    dev_ok &= good
    print(f"  {arm}: {ok}/{n} {'✅' if good else '❌'}")

d_ab = arms["A"]["mean"] - arms["B"]["mean"]
d_ac = arms["A"]["mean"] - arms["C"]["mean"]
noise = max(arms["A"]["spread"], arms["B"]["spread"])
nb = arms["B"]["n0"] or {}
na = arms["A"]["n0"] or {}
n0_ok = all(na.get(k, 0) >= nb.get(k, 0) for k in nb)

print(f"\n判定: Δ(A−B)={d_ab:+.4f}  Δ(A−C)={d_ac:+.4f}  门槛=≥0.05")
print(f"        噪声: A极差={arms['A']['spread']:.4f} B极差={arms['B']['spread']:.4f} "
      f"⇒ 需 min(Δ) > {noise:.4f}")
print(f"        邻域不回归: {n0_ok}   装置自检: {dev_ok}")
c1 = d_ab >= 0.05 and d_ac >= 0.05
c2 = min(d_ab, d_ac) > noise
claim = c1 and c2 and n0_ok and dev_ok
print("★7u: ", "PASS —— 消歧指令效应成立，可认领" if claim else
      "不认领 —— 见上，逐条如实登记")
sys.exit(0 if claim else 1)
