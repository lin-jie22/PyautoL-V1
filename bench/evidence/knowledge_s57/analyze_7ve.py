#!/usr/bin/env python3
"""★7v-e 四臂分析（零模型）：最小对 —— 「逐条」vs「逐项」，只改一个字。

判据逐字照 DESIGN §19novies 的 7v-e 登记（跑之前写死）：
  · 复现 = g7e2 **三次 rep 全 1.0** 且 n4 均值 **≥ 0.65**（与 7v-c/d 同一门槛）。
  · N1 复现、N2 不复现 ⇒ **字面词形是必要条件** ⇒ 写"脆的提示效应：依赖『逐条』二字"，
    ⛔ 不进产品叙事。
  · N1 复现、N2 也复现 ⇒ **语义（逐项枚举）承载**，词形可换 ⇒ 才可写"机制"；
    7v-d 的 S1 失败降为"句式太散"的**解释**（登记不认领）。
  · N1 不复现 ⇒ 连"逐条 + 可执行框架"都不够 ⇒ **A 句字面特异**，转内核，不再调 prompt。
  · N2 复现而 N1 不复现 ⇒ 反直觉 ⇒ 先复跑确认，不直接采信。
  · 前置：装置自检 100%、rep 逐位一致、n0 不回归、**A 正对照复现**（任一不成立 ⇒ 本窗作废）。
⛔ 不跨窗做差（R7）：7v-d 的 0.7000/0.5000 只作方向参照，不进判据。
"""
import json
import sys

D = "/tmp/v1_7ve"
N1S = "上方状态请逐条对照执行，每一条都按原文原样落地，不要自行改写。"
N2S = "上方状态请逐项对照执行，每一条都按原文原样落地，不要自行改写。"
MARK = {"A":  "上方状态按原文顺序依次阅读，逐条都保持其原有排列顺序与文字内容不变。",
        "N1": N1S,
        "N2": N2S}
RUNS = {arm: f"{D}/snap/runs_{arm}.jsonl" for arm in ("A", "N1", "N2", "B")}
REPS = ("r1", "r2", "r3")
GOAL = "g7e2_truncate_tail"
FULL = 0.65
ARMS = ("A", "N1", "N2", "B")
ORDER = ("B", "N1", "N2", "A")

# 跑前自证：两条串必须等长且唯一差异位是第 7 字
assert len(N1S) == len(N2S) == 31, ("串长", len(N1S), len(N2S))
assert [i for i, (a, b) in enumerate(zip(N1S, N2S)) if a != b] == [6], "差异位不是唯一第7字"


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
    rows = load(RUNS[arm], 4)
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


print("\n装置自检（每条补救/收尾请求里**只**带自己那一句，且请求数 ≥1）:")
dev = {}
for arm in ARMS:
    if arm == "B":
        pred = lambda u: not any(m in u for m in MARK.values())
    else:
        others = [m for k, m in MARK.items() if k != arm]
        own = MARK[arm]
        pred = lambda u, own=own, oth=others: own in u and not any(m in u for m in oth)
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
            for a in ("A", "N1", "N2") for k in ("g7d2_dedupe_keep", "g7d3_fmt_amount"))
gate = status["A"] == "复现"
print(f"\n前置: 装置={dev_ok} rep逐位全一致={pre_stable} "
      f"总极差={max(arms[a]['spread'] for a in ARMS):.4f} 邻域不回归={n0_ok}")
print(f"★窗口闸门（A 正对照复现）: {gate}  A g7e2={arms['A']['per_id'].get(GOAL)}")


def decide(n1, n2):
    r1, r2 = (s == "复现" for s in (n1, n2))
    p1, p2 = (s == "部分" for s in (n1, n2))
    if r1 and r2:
        return "语义承载（换同义词仍复现）⇒ 可写机制；7v-d S1 失败降为『句式太散』的解释"
    if r1 and not r2:
        return "字面词形是必要条件（换同义词即失效）⇒ 脆的提示效应，⛔ 不进产品叙事"
    if r2 and not r1:
        return "反直觉（换词反而更好）⇒ 先复跑一轮确认，不直接采信"
    if p1 or p2:
        return "未定位 + 部分复现（有 1.0 出现但未达全复现门槛 ⇒ 只登记现象）"
    return "N1 不复现 ⇒ 『逐条 + 可执行框架』仍不够，A 句字面特异 ⇒ 转内核，不再调 prompt"


verdict_ok = dev_ok and n0_ok and gate
print("\n★7v-e【按字面登记】: "
      + (decide(status["N1"], status["N2"])
         if verdict_ok else "作废 —— 前置不成立（装置/窗口闸门/邻域）"))

print("\n逐格极差（>0 即为窗内不稳）:")
dirty = [(arm, tid) for arm in ARMS for tid, v in arms[arm]["per_id"].items()
         if max(v) - min(v) > 0]
for arm, tid in dirty:
    print(f"  {arm}/{tid}: {arms[arm]['per_id'][tid]}")
if not dirty:
    print("  无（全格极差 0）")

print("\n逐题明细（n4 三 rep）:")
for arm in ORDER:
    tag = ("基线/无附加句" if arm == "B"
           else ("A 34字常量(逐条)" if arm == "A"
                 else ("N1 31字(第7字=条)" if arm == "N1" else "N2 31字(第7字=项)")))
    print(f"  {arm:2s} {tag}  mean={arms[arm]['mean']:.4f}")
    for tid, v in arms[arm]["per_id"].items():
        print(f"      {tid:22s} {v}")

# 通道取证：wall/空答案否决/收尾，是否有分叉（7v-c 起固定登记）
print("\n通道标记（kernel_closeout.by_reason / kernel_transport）:")
for arm in ORDER:
    for t, r in zip(REPS, load(RUNS[arm], 4)[1:]):
        co = r.get("kernel_closeout") or {}
        tr = r.get("kernel_transport") or {}
        print(f"  {arm:2s}/{t}: 收尾触发={co.get('triggered')} 理由={co.get('by_reason')}"
              f" wall拒首={tr.get('wall_refused_first')} wall缩={tr.get('wall_shortened')}")

sys.exit(0 if verdict_ok else 1)
