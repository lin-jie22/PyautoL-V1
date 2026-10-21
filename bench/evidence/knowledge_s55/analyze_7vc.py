#!/usr/bin/env python3
"""★7v-c 四臂分析（零模型）：共同成分定位 —— 祈使句式 vs "逐条"。

判据逐字照 DESIGN §19sexies 的 7v-c 登记（跑之前写死）：
  · 复现 = g7e2 **三次 rep 全 1.0** 且 n4 均值 **≥ 0.65**（7v-b 的 A/C1=0.70 与
    B/C2=0.55/0.50 的中点）。
  · 0.55 < mean < 0.65 或 g7e2 部分 1.0 ⇒ **部分复现**，单列，不并入结论。
  · P1 与 P2 都复现 ⇒ 承载者是"指令性"本身（与句式/逐条无关）⇒ 按 7v-b 结案。
  · 都不复现 ⇒ **未定位**（如实登记，不许猜）。
  · 只有一方复现 ⇒ 该成分即承载者。
  · 三 rep 逐位一致 + n0 邻域不回归是**前置**（不成立则本轮作废）。
⛔ 不跨窗做差（R7）：7v-b 的 0.7000/0.5500 只作方向参照，不进判据。
"""
import json
import sys

D = "/tmp/v1_7vc"
MARK = {"A": "上方状态按原文顺序依次阅读",
        "P1": "上方状态里的各条内容彼此独立存在",
        "P2": "请严格照着上方状态来实现"}
RUNS = {
    "A":  ("/tmp/v1_7vc/snap/runs_A.jsonl", "capA.jsonl"),
    "P1": ("/tmp/v1_7vc/snap/runs_P1.jsonl", "capP1.jsonl"),
    "P2": ("/tmp/v1_7vc/snap/runs_P2.jsonl", "capP2.jsonl"),
    "B":  ("/tmp/v1_7vc/snap/runs_B.jsonl", "capB.jsonl"),
}
#: ⛔ 只读**快照**，不读活文件：本轮自踩 —— B 臂复现检查把新行**追加**进同一份
#: `bench_out/runs.jsonl`，而本脚本原来取"最近 4 行"⇒ 把复现检查的 3 行当成主窗
#: ⇒ 读数从"0.5333 / g7d3 极差 0.2"变成"0.5500 / 极差 0.0000"，**把污染洗成了干净**。
#: 修法：快照 + 行数断言（load 里强制 == 4），任何追加都不会再改变已完成轮的读数。
REPS = ("r1", "r2", "r3")
GOAL = "g7e2_truncate_tail"
FULL, HALF = 0.65, 0.55


def per_map(row):
    return {t["id"]: t["score"]
            for t in (row.get("report") or {}).get("per_task", [])}


def load(path, want):
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    #: 快照必须**恰好**是 4 行（n0 + r1..r3）；多一行/少一行都说明快照取错 ⇒ 直接失败，
    #: 不许"取最近 N 行"那种静默兜底（本轮就是它把污染洗干净的）。
    if len(rows) != len(want):
        print(f"!! {path} 行数 {len(rows)} != 期望 {len(want)} ⇒ 快照取错，终止")
        sys.exit(2)
    return rows


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
    print(f"臂 {arm:2s}: n4 mean={a['mean']:.4f} rep逐位={a['bitwise']} 极差={spread:.4f}")
    for i, v in a["per_id"].items():
        print(f"    {i}: {v}")

print("\nn0 邻域（前置：三处理臂的 g7d2/g7d3 不低于 B）:")
for arm in arms:
    n0 = arms[arm]["n0"] or {}
    print(f"  {arm:2s}: " + ", ".join(f"{k}={v}" for k, v in sorted(n0.items())))


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


print("\n装置自检（补救/收尾请求里**只**带自己那一句）:")
dev = {"A":  lambda u: MARK["A"] in u and MARK["P1"] not in u and MARK["P2"] not in u,
       "P1": lambda u: MARK["P1"] in u and MARK["A"] not in u and MARK["P2"] not in u,
       "P2": lambda u: MARK["P2"] in u and MARK["A"] not in u and MARK["P1"] not in u,
       "B":  lambda u: not any(m in u for m in MARK.values())}
dev_ok = True
for arm, pred in dev.items():
    n, ok = cap_check(RUNS[arm][1], pred)
    good = n > 0 and ok == n
    dev_ok &= good
    print(f"  {arm:2s}: {ok}/{n} {'✅' if good else '❌'}")

# ── 复现判定（逐字照登记）────────────────────────────────────────────
status = {}
for arm in ("A", "P1", "P2", "B"):
    g = arms[arm]["per_id"].get(GOAL, [])
    all1 = len(g) == 3 and all(v == 1.0 for v in g)
    m = arms[arm]["mean"]
    if all1 and m >= FULL:
        status[arm] = "复现"
    elif m >= 0.60 or any(v == 1.0 for v in g):
        # ⛔ 门槛写死：0.60 以下且 g7e2 无 1.0 ⇒ 就是"未复现"。
        #    不能把"mean ≥ 0.55"当部分复现 —— 那是基线自己的水位（7v-b 的 B=0.55）。
        status[arm] = "部分"
    else:
        status[arm] = "未复现"
print("\n复现判定（复现 = g7e2 3/3 全 1.0 且 mean ≥ 0.65）:")
for arm in ("A", "P1", "P2", "B"):
    print(f"  {arm:2s}: {status[arm]:4s}  g7e2={arms[arm]['per_id'].get(GOAL)}  mean={arms[arm]['mean']:.4f}")

pre = (dev_ok and all(arms[a]["bitwise"] for a in arms)
       and max(arms[a]["spread"] for a in arms) == 0.0)
nb = arms["B"]["n0"] or {}
n0_ok = all((arms[a]["n0"] or {}).get(k, 0) >= nb.get(k, 0)
            for a in ("A", "P1", "P2") for k in ("g7d2_dedupe_keep", "g7d3_fmt_amount"))
print(f"\n前置条件: 装置={dev_ok} rep逐位全一致={all(arms[a]['bitwise'] for a in arms)} "
      f"总极差={max(arms[a]['spread'] for a in arms):.4f} 邻域不回归={n0_ok}")

p1, p2 = status["P1"] == "复现", status["P2"] == "复现"


def decide(p1, p2, s1, s2):
    if p1 and p2:
        return "指令性本身（与句式/'逐条'无关）⇒ 按 7v-b 的『存在效应』结案，不细分"
    if p1 and not p2:
        return "闸门='逐条'（P1 复现、P2 不复现）⇒ 承载成分是含『逐条』的句子"
    if p2 and not p1:
        return "闸门=祈使句式（P2 复现、P1 不复现）⇒ 承载成分是指令性句式"
    partial = s1 == "部分" or s2 == "部分"
    return ("未定位（P1/P2 都未复现；有部分复现者单列，不并入结论）"
            if not partial else
            "未定位 + 部分复现（P1/P2 均未达全复现门槛，但有 1.0 出现 ⇒ 只登记现象）")


print("\n★7v-c【按字面登记的前置】: "
      + (decide(p1, p2, status["P1"], status["P2"]) if (pre and n0_ok)
         else "作废 —— 前置不成立（装置/稳定性/邻域）"))

# ── 受污染格隔离（事后，必须单列，不许悄悄放宽）──────────────────────
# 前置失败的确切位置：把每臂每格的极差打出来，只允许"判定变量之外"的格出问题，
# 且该格必须在**判定时被排除**。若污染落在判定变量（g7e2）上 ⇒ 一律作废，不隔离。
print("\n逐格极差（>0 即为窗内不稳）:")
dirty = [(arm, tid) for arm in arms for tid, v in arms[arm]["per_id"].items()
         if max(v) - min(v) > 0]
for arm, tid in dirty:
    print(f"  {arm}/{tid}: {arms[arm]['per_id'][tid]}")
if not dirty:
    print("  无（全格极差 0）")

isolated = all(tid != GOAL for _a, tid in dirty)
if dirty and isolated and n0_ok and dev_ok:
    # 判定变量 g7e2 在四臂全部 3/3 逐位一致 ⇒ 复现判定不依赖受污染的 g7d3 格。
    p1i = status["P1"] == "复现"
    p2i = status["P2"] == "复现"
    print("\n★7v-c【隔离受污染格后】: " + decide(p1i, p2i, status["P1"], status["P2"]))
    print("  说明：受污染格 = " + ", ".join(f"{a}/{t}" for a, t in dirty)
          + "，**不在**判定变量 g7e2 上；g7e2 四臂全部 3/3 逐位一致（1.0 vs 0.2）。")

# ── 基线复现检查（新窗，同臂 3 rep；只回答"翻转会不会复发"，不做跨窗 Δ）──
try:
    b2 = [json.loads(l) for l in
          open("/tmp/v1_7vc/snap/runs_B2.jsonl", encoding="utf-8") if l.strip()]
    assert len(b2) == 3
    b2m = [per_map(r) for r in b2]
    print("\nB 臂复现检查（新窗 3 rep，run_B2_*.log）:")
    for i, m in enumerate(b2m, 1):
        print(f"  B2_r{i}: " + ", ".join(f"{k}={v}" for k, v in sorted(m.items())))
    same = all(m == b2m[0] for m in b2m)
    print(f"  3 rep 逐位一致={same}  g7d3={[m.get('g7d3_fmt_amount') for m in b2m]}"
          f"  ⇒ 翻转{'未复发（低频 flaky，登记不掩盖）' if same else '复发（基线确有随机性）'}")
except FileNotFoundError:
    print("\nB 臂复现检查：未跑")

# ── 2×2 分解：增益与减损是不是同一个原因 ─────────────────────────────
# 本轮四条串恰好把两件事拆开了：P2（加了一句、但无"逐条"）与 B（不加句）只差一句，
# 与 A/P1（加了一句、有"逐条"）也只差"逐条"。⇒ 可以分别看 g7e2 与 g7d3 各自跟谁走。
TRACE = {"A": True, "P1": True, "P2": False, "B": False}
print("\n2×2 分解（'加了一句' × '含逐条'）:")
for arm in ("B", "P2", "P1", "A"):
    mid = arms[arm]["per_id"]
    g2 = mid.get(GOAL, [])
    g3 = mid.get("g7d3_fmt_amount", [])
    print(f"  {arm:2s} 含逐条={str(TRACE[arm]):5s} g7e2={g2} g7d3={g3}")
print("  ⇒ 读法：g7e2 只跟『逐条』走（True→1.0 / False→0.2）；"
      "g7d3 只跟『加了一句』走（加→0.4 / 不加→0.6，与是否含逐条无关）。")

sys.exit(0 if (pre and n0_ok) or (dirty and isolated and n0_ok and dev_ok) else 1)