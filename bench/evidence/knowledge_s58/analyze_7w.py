#!/usr/bin/env python3
"""★7w 五臂分析（零模型）：补救通道闭路反馈。

判据逐字照 DESIGN §19《★7w 登记》（跑之前写死）：
  · 单臂「成立」= 相对 B0：g7e2 三 rep 全 1.0 且 mean ≥ 0.65 **且 g7d3 ≥ 0.6**
    （不许用"多一句话"的 −0.2 换分 —— 7v-c/7v-e 已两次量到这笔代价）。
  · W1 与 W2 都成立 ⇒ 反馈本身承载；仅 W2 ⇒ 只需"看到上次答案"；仅 W1 ⇒ 原因更有用；
  · 全不成立 ⇒ 登记"盲发不是瓶颈"，转 verify 判据层。
  · A **只作窗口闸门**，不参与「成立」判定（它带 `_ANSWER_FIDELITY`，g7d3 本来就 0.4）。
⛔ 不跨窗做差（R7）：7v-e 的 0.70/0.55 只作方向参照。
"""
import json
import sys

D = "/tmp/v1_7w"
RUNS = {a: f"{D}/snap/runs_{a}.jsonl" for a in ("A", "B0", "W1", "W2", "W3")}
REPS = ("r1", "r2", "r3")
GOAL = "g7e2_truncate_tail"
FULL = 0.65
FLOOR_D3 = 0.6
ARMS = ("A", "B0", "W1", "W2", "W3")
ORDER = ("B0", "W1", "W2", "W3", "A")
FAIL_MARK = "上一次答案未通过："
ANS_MARK = "上一次的答案（已被否决）："


def per_map(row):
    return {t["id"]: t["score"] for t in (row.get("report") or {}).get("per_task", [])}


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

print("\nn0 邻域（前置：处理臂的 g7d2/g7d3 不低于 B0）:")
for arm in ORDER:
    n0 = arms[arm]["n0"] or {}
    print(f"  {arm:2s}: " + ", ".join(f"{k}={v}" for k, v in sorted(n0.items())))


def cap_scan(paths):
    """统计每条补救请求里是否带了两种反馈标记。"""
    n = with_fail = with_ans = 0
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
                    u = msgs[-1]["content"]
                    n += 1
                    with_fail += 1 if FAIL_MARK in u else 0
                    with_ans += 1 if ANS_MARK in u else 0
    return n, with_fail, with_ans


print("\n装置自检（补救请求里反馈标记的出现情况；首次补救按设计不带）:")
dev = {}
EXPECT = {"A": (0, 0), "B0": (0, 0), "W1": ("fail", 0), "W2": (0, "ans"), "W3": ("fail", "ans")}
for arm in ARMS:
    n, wf, wa = cap_scan([f"{D}/cap{arm}_{t}.jsonl" for t in ("n0",) + REPS])
    ef, ea = EXPECT[arm]
    want_fail = ef not in (0,)
    want_ans = ea not in (0,)
    ok = (n > 0
          and (wf > 0 if want_fail else wf == 0)
          and (wa > 0 if want_ans else wa == 0))
    dev[arm] = ok
    print(f"  {arm:2s}: 补救={n:3d} 带失败行={wf:3d} 带上次答案={wa:3d} "
          f"期望(fail={'要' if want_fail else '不要'},ans={'要' if want_ans else '不要'}) "
          f"{'✅' if ok else '❌'}")
dev_ok = all(dev.values())

status = {}
for arm in ARMS:
    g = arms[arm]["per_id"].get(GOAL, [])
    d3 = arms[arm]["per_id"].get("g7d3_fmt_amount", [])
    all1 = len(g) == 3 and all(v == 1.0 for v in g)
    m = arms[arm]["mean"]
    d3_ok = bool(d3) and min(d3) >= FLOOR_D3
    if all1 and m >= FULL and d3_ok:
        status[arm] = "成立"
    elif all1 and m >= FULL:
        status[arm] = "分数够但 g7d3 掉破 0.6（拿代价换的分，不算成立）"
    elif m >= 0.60 or any(v == 1.0 for v in g):
        status[arm] = "部分"
    else:
        status[arm] = "未成立"
print("\n成立判定（g7e2 3/3=1.0 且 mean≥0.65 且 g7d3≥0.6；A 只作闸门）:")
for arm in ORDER:
    print(f"  {arm:2s}: {status[arm]}  g7e2={arms[arm]['per_id'].get(GOAL)}"
          f"  g7d3={arms[arm]['per_id'].get('g7d3_fmt_amount')}  mean={arms[arm]['mean']:.4f}")

pre_stable = all(arms[a]["bitwise"] for a in ARMS) and max(arms[a]["spread"] for a in ARMS) == 0.0
nb = arms["B0"]["n0"] or {}
n0_ok = all((arms[a]["n0"] or {}).get(k, 0) >= nb.get(k, 0)
            for a in ("A", "W1", "W2", "W3") for k in ("g7d2_dedupe_keep", "g7d3_fmt_amount"))
gate = (len(arms["A"]["per_id"].get(GOAL, [])) == 3
        and all(v == 1.0 for v in arms["A"]["per_id"][GOAL]))
print(f"\n前置: 装置={dev_ok} rep逐位全一致={pre_stable} "
      f"总极差={max(arms[a]['spread'] for a in ARMS):.4f} 邻域不回归={n0_ok}")
print(f"★窗口闸门（A 正对照复现）: {gate}  A g7e2={arms['A']['per_id'].get(GOAL)}")


def decide(s1, s2, s3):
    r1, r2, r3 = (s == "成立" for s in (s1, s2, s3))
    if r1 and r2:
        return "反馈本身承载（W1、W2 都成立）"
    if r2 and not r1:
        return "只需『看到上一次答案』，与失败原因无关（仅 W2 成立）"
    if r1 and not r2:
        return "失败原因比答案原文更有用（仅 W1 成立）"
    if r3 and not r1 and not r2:
        return "只有两者同时在场才成立（仅 W3）⇒ 反馈是组合效应"
    if any(s == "部分" for s in (s1, s2, s3)):
        return "未成立 + 部分复现（有 1.0 出现但未达门槛 ⇒ 只登记现象）"
    return "全不成立 ⇒ 盲发不是瓶颈，转 verify 判据层（`def f(): pass` 能过四项检查）"


verdict_ok = dev_ok and n0_ok and gate
print("\n★7w【按字面登记】: "
      + (decide(status["W1"], status["W2"], status["W3"])
         if verdict_ok else "作废 —— 前置不成立（装置/窗口闸门/邻域）"))

print("\n逐格极差（>0 即为窗内不稳）:")
dirty = [(arm, tid) for arm in ARMS for tid, v in arms[arm]["per_id"].items()
         if max(v) - min(v) > 0]
for arm, tid in dirty:
    print(f"  {arm}/{tid}: {arms[arm]['per_id'][tid]}")
if not dirty:
    print("  无（全格极差 0）")

print("\n通道标记（kernel_closeout / kernel_transport）:")
for arm in ORDER:
    for t, r in zip(REPS, load(RUNS[arm], 4)[1:]):
        co = r.get("kernel_closeout") or {}
        tr = r.get("kernel_transport") or {}
        print(f"  {arm:2s}/{t}: 收尾={co.get('triggered')} 理由={co.get('by_reason')}"
              f" wall拒首={tr.get('wall_refused_first')} wall缩={tr.get('wall_shortened')}")

sys.exit(0 if verdict_ok else 1)
