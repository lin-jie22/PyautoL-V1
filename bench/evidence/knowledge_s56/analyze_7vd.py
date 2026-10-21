#!/usr/bin/env python3
"""★7v-d 五臂分析（零模型）：字面 vs 语义 —— "逐条"到底是不是两个字的事。

判据逐字照 DESIGN §19septies 的 7v-d 登记（跑之前写死）：
  · 复现 = g7e2 **三次 rep 全 1.0** 且 n4 均值 **≥ 0.65**（与 7v-c 同一门槛）。
  · S1（同义替换、**不含**"逐条"）复现 ⇒ 语义（逐项枚举）承载 ⇒ 可写"机制"。
  · S2（含"逐条"但明说"不构成要求"）复现 ⇒ 字面词法启动 ⇒ 结论降为**脆的提示效应**。
  · S3（"逐条"×16 纯重复）复现 ⇒ 极端词法，增益与语义无关。
  · S1 与 S2 都不复现 ⇒ 未定位（如实登记，重开方向）。
  · S1 与 S2 都复现 ⇒ 两者皆可，仍不许把"逐条"当机制写进对外文档。
  · ★A 正对照（登记时补）：A 不复现 ⇒ **本窗作废**，不出任何结论。
⛔ 不跨窗做差（R7）：7v-c 的 0.7000/0.5333 只作方向参照，不进判据。
"""
import json
import sys

D = "/tmp/v1_7vd"
MARK = {"A":  "上方状态按原文顺序依次阅读",
        "S1": "上方状态里的每一条内容都彼此独立存在",
        "S2": "上方状态里的内容逐条排列",
        "S3": "逐条逐条逐条逐条"}          # 只取 8 字，足以区分且不会被 S2 命中
RUNS = {arm: (f"{D}/snap/runs_{arm}.jsonl", f"cap{arm}_r1.jsonl")
        for arm in ("A", "S1", "S2", "S3", "B")}
#: ⛔ 只读**快照**（7v-c 的教训：活文件会被追加，读"最近 N 行"等于把污染洗成干净）。
#: 本轮的 cap 文件是**每次 run 一个**（cap{arm}_{tag}.jsonl），不是整臂一个。
REPS = ("r1", "r2", "r3")
GOAL = "g7e2_truncate_tail"
FULL = 0.65
ARMS = ("A", "S1", "S2", "S3", "B")
ORDER = ("B", "S1", "S2", "S3", "A")


def per_map(row):
    return {t["id"]: t["score"]
            for t in (row.get("report") or {}).get("per_task", [])}


def load(path, n):
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    if len(rows) != n:
        print(f"!! {path} 行数 {len(rows)} != 期望 {n} ⇒ 快照取错，终止")
        sys.exit(2)
    return rows


arms = {}
for arm in ARMS:
    rows = load(RUNS[arm][0], 4)
    maps = {t: per_map(r) for t, r in zip(("n0",) + REPS, rows)}
    n4 = [maps[t] for t in REPS]
    flat = [s for m in n4 for s in m.values()]
    spread = max(max(m.get(i) for m in n4) - min(m.get(i) for m in n4) for i in n4[0])
    arms[arm] = {"mean": sum(flat) / len(flat),
                 "per_id": {i: [m.get(i) for m in n4] for i in sorted(n4[0])},
                 "bitwise": all(m == n4[0] for m in n4),
                 "spread": spread, "n0": maps.get("n0")}
    a = arms[arm]
    print(f"臂 {arm:2s}: n4 mean={a['mean']:.4f} rep逐位={a['bitwise']} 极差={spread:.4f}")
    for i, v in a["per_id"].items():
        print(f"    {i}: {v}")

print("\nn0 邻域（前置：处理臂的 g7d2/g7d3 不低于 B）:")
for arm in ORDER:
    n0 = arms[arm]["n0"] or {}
    print(f"  {arm:2s}: " + ", ".join(f"{k}={v}" for k, v in sorted(n0.items())))


def cap_check(paths, pred):
    n = ok = 0
    for p in paths:
        try:
            fh = open(p, encoding="utf-8")
        except FileNotFoundError:
            continue
        with fh:
            for line in fh:
                ev = json.loads(line)
                if "#resp" in ev["path"] or not ev["path"].endswith("/chat/completions"):
                    continue
                msgs = json.loads(ev["body"]).get("messages", [])
                if msgs and "只输出最终答案本身" in msgs[0].get("content", ""):
                    n += 1
                    ok += 1 if pred(msgs[-1]["content"]) else 0
    return n, ok


print("\n装置自检（每条补救/收尾请求里**只**带自己那一句，且补救请求数 ≥1）:")
dev = {}
for arm in ARMS:
    others = [m for k, m in MARK.items() if k != arm]
    pred = ((lambda u, own=MARK[arm], oth=others: own in u and not any(m in u for m in oth))
            if arm != "B" else (lambda u: not any(m in u for m in MARK.values())))
    paths = [f"{D}/cap{arm}_{t}.jsonl" for t in ("n0",) + REPS]
    n, ok = cap_check(paths, pred)
    dev[arm] = (n > 0 and ok == n)
    print(f"  {arm:2s}: {ok}/{n} {'✅' if dev[arm] else '❌'}")
dev_ok = all(dev.values())

# ── 复现判定（逐字照登记）────────────────────────────────────────────
status = {}
for arm in ARMS:
    g = arms[arm]["per_id"].get(GOAL, [])
    all1 = len(g) == 3 and all(v == 1.0 for v in g)
    m = arms[arm]["mean"]
    if all1 and m >= FULL:
        status[arm] = "复现"
    elif m >= 0.60 or any(v == 1.0 for v in g):
        status[arm] = "部分"
    else:
        status[arm] = "未复现"
print("\n复现判定（复现 = g7e2 3/3 全 1.0 且 mean ≥ 0.65）:")
for arm in ORDER:
    print(f"  {arm:2s}: {status[arm]:4s}  g7e2={arms[arm]['per_id'].get(GOAL)}"
          f"  mean={arms[arm]['mean']:.4f}")

pre_stable = all(arms[a]["bitwise"] for a in ARMS) and max(arms[a]["spread"] for a in ARMS) == 0.0
nb = arms["B"]["n0"] or {}
n0_ok = all((arms[a]["n0"] or {}).get(k, 0) >= nb.get(k, 0)
            for a in ("A", "S1", "S2", "S3") for k in ("g7d2_dedupe_keep", "g7d3_fmt_amount"))
gate = status["A"] == "复现"
print(f"\n前置: 装置={dev_ok} rep逐位全一致={pre_stable} "
      f"总极差={max(arms[a]['spread'] for a in ARMS):.4f} 邻域不回归={n0_ok}")
print(f"★窗口闸门（A 正对照复现）: {gate}  A g7e2={arms['A']['per_id'].get(GOAL)}")


def decide(s1, s2, s3):
    r1, r2, r3 = (s == "复现" for s in (s1, s2, s3))
    if r1 and r2:
        return "两者皆可（语义与字面都通）⇒ 仍不许把『逐条』当机制写进对外文档"
    if r1 and not r2:
        return "语义承载（S1 同义替换复现、S2 语义否定不复现）⇒ 可写机制"
    if r2 and not r1:
        return "字面词法启动（S2 明说『不构成要求』仍复现、S1 不复现）⇒ 脆的提示效应"
    if r3 and not r1 and not r2:
        return "极端词法（只有纯重复复现）⇒ 增益与语义完全无关"
    if any(s == "部分" for s in (s1, s2, s3)):
        return "未定位 + 部分复现（有 1.0 出现但未达全复现门槛 ⇒ 只登记现象）"
    return "未定位（S1/S2/S3 都不复现）⇒ 重开方向"


verdict_ok = dev_ok and n0_ok and gate
print("\n★7v-d【按字面登记】: "
      + (decide(status["S1"], status["S2"], status["S3"])
         if verdict_ok else "作废 —— 前置不成立（装置/窗口闸门/邻域）"))

# ── 逐格极差（>0 即窗内不稳；7v-c 学到基线臂也会翻）──────────────────
print("\n逐格极差（>0 即为窗内不稳）:")
dirty = [(arm, tid) for arm in ARMS for tid, v in arms[arm]["per_id"].items()
         if max(v) - min(v) > 0]
for arm, tid in dirty:
    print(f"  {arm}/{tid}: {arms[arm]['per_id'][tid]}")
if not dirty:
    print("  无（全格极差 0）")

# ── 逐题明细表（便于人眼复核）───────────────────────────────────────
print("\n逐题明细（n4 三 rep）:")
for arm in ORDER:
    print(f"  {arm:2s} 含逐条={'—' if arm == 'B' else ('否' if arm == 'S1' else '是')}"
          f"  加句={'否' if arm == 'B' else '是'}  mean={arms[arm]['mean']:.4f}")
    for tid, v in arms[arm]["per_id"].items():
        print(f"      {tid:22s} {v}")

sys.exit(0 if verdict_ok else 1)
