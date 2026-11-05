#!/usr/bin/env python3
"""V69 分析器 —— 只读冻结产物，不写回证据路径（纪律 30）。

输入：/tmp/v69/e{0,1,2}/r{1,2,3}/{log/core.log,bench/runs.jsonl,state_dump.jsonl}
用途：判 P0（等长到位）/ P1（首步是不是 finish）/ P3（臂内 2 跑逐格相同）+ 登记分数。

⛔ 分数只登记，不用于复核 P1（预注册 §3 P2）。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v69")
ARMS = (0, 1, 2)
RUNS = (1, 2, 3)
ORDER = ("g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail")


def parse_core(p: Path):
    """按 `▶ 任务开始` 切段，返回 [{title, steps:[{i,cand,kind,tools:[(name,intent)]}]}]"""
    tasks, cur = [], None
    if not p.exists():
        return tasks
    for line in open(p, encoding="utf-8", errors="replace"):
        line = line.rstrip("\n")
        if "▶ 任务开始" in line:
            cur = {"title": line.split("▶ 任务开始:", 1)[-1][:24], "steps": []}
            tasks.append(cur)
            continue
        if cur is None:
            continue
        m = re.search(r"\[(\d+)\] 通道=\S+ 候选=(\d+) 种类=(\d+)", line)
        if m:
            cur["steps"].append({"i": int(m.group(1)), "cand": int(m.group(2)),
                                 "kind": int(m.group(3)), "tools": []})
            continue
        m = re.search(r"↳ (\w+) ok=(\w+) ?(.*)$", line)
        if m and cur["steps"]:
            cur["steps"][-1]["tools"].append((m.group(1), m.group(2), m.group(3)[:46]))
    return tasks


def seq_of(t):
    if not t["steps"]:
        return "∅"
    parts = []
    for s in t["steps"]:
        tl = "+".join(x[0] for x in s["tools"]) or "-"
        parts.append(f"[{s['i']}]c{s['cand']}k{s['kind']}:{tl}")
    return " | ".join(parts)


def first_tool(t):
    for s in t["steps"]:
        for x in s["tools"]:
            return x[0]
    return "∅"


def runs_last(p: Path):
    if not p.exists():
        return None
    rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    return rows[-1] if rows else None


print("=" * 96)
print("V69 —— 三臂 V1_REWARD_ECHO 0/1/2 × 3 跑，固定 seed 记忆库（4 题，g7e_heldout）")
print("=" * 96)

plans, scores, sigs = {}, {}, {}
for a in ARMS:
    for r in RUNS:
        d = ROOT / f"e{a}" / f"r{r}"
        ts = parse_core(d / "log" / "core.log")
        plans[(a, r)] = ts
        last = runs_last(d / "bench" / "runs.jsonl")
        if last:
            scores[(a, r)] = {t["id"]: t["score"] for t in last["report"]["per_task"]}
            sigs[(a, r)] = last.get("caliber_sig")

print("\n--- 计划序列（逐臂逐跑逐题）---")
for a in ARMS:
    for r in RUNS:
        ts = plans[(a, r)]
        print(f"  e{a} r{r}  题数={len(ts)}")
        for t in ts:
            print(f"      {t['title'][:16]:18s} {seq_of(t)}")

print("\n--- P3：臂内 2 跑（r1/r2）是否逐格相同 ---")
for a in ARMS:
    s1 = [seq_of(t) for t in plans[(a, 1)]]
    s2 = [seq_of(t) for t in plans[(a, 2)]]
    same = s1 == s2 and len(s1) > 0
    print(f"  e{a}: {'逐格相同 ✅' if same else '不同 ❗'}")
    if not same:
        for x, y in zip(s1, s2):
            if x != y:
                print(f"      r1 {x}\n      r2 {y}")

print("\n--- P1：首步是不是 finish（e1 真载荷 vs e2 等长安慰剂）---")
rows = []
for a in ARMS:
    ts = plans[(a, 1)]
    rows.append((a, [first_tool(t) for t in ts]))
if all(len(v) == 4 for _, v in rows):
    print(f"  {'题':24s} " + " ".join(f"e{a:<8d}" for a in ARMS))
    for i, tid in enumerate(ORDER):
        print(f"  {tid:24s} " + " ".join(f"{v[i]:<9s}" for _, v in rows))
    up = [(ORDER[i]) for i in range(4) if rows[1][1][i] != "finish" and rows[2][1][i] == "finish"]
    down = [(ORDER[i]) for i in range(4) if rows[2][1][i] != "finish" and rows[1][1][i] == "finish"]
    print(f"\n  ⇒ 升（e1 非 finish、e2 finish）：{up or '无'}")
    print(f"  ⇒ 降（e2 非 finish、e1 finish）：{down or '无'}")
    if up and not down:
        print("  ⇒ P1 = **升**")
    elif down and not up:
        print("  ⇒ P1 = **降**")
    elif not up and not down:
        print("  ⇒ P1 = **平**")
    else:
        print("  ⇒ P1 = **混合**（升与降同时出现 ⇒ 按预注册逐题解释，不聚总）")
else:
    print("  题数不齐，无法对齐 ⇒ UNKNOWN")

print("\n--- P0：state-dump 里载荷是否等长且到位（每臂 r3）---")
for a in ARMS:
    p = ROOT / f"e{a}" / "r3" / "state_dump.jsonl"
    if not p.exists():
        print(f"  e{a}: 无 dump")
        continue
    rows_d = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    print(f"  e{a}: {len(rows_d)} 行")
    for d in rows_d:
        g = d.get("guidance") or ""
        print(f"      {str(d.get('task')):22s} it={d.get('it')} len={len(g):4d} "
              f"AssertionError={'AssertionError' in g} 例会={'例会' in g} 空={g == ''}")

print("\n--- 登记：分数（⛔ 不用于复核 P1）---")
for a in ARMS:
    for r in RUNS:
        pt = scores.get((a, r))
        if not pt:
            print(f"  e{a} r{r}: 无 runs")
            continue
        mean = sum(pt.values()) / len(pt)
        cells = "  ".join(f"{k.split('_')[0]}={pt[k]:.2f}" for k in ORDER if k in pt)
        print(f"  e{a} r{r}  mean={mean:.4f}  {cells}  sig={sigs.get((a, r))}")
