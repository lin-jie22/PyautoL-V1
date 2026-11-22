#!/usr/bin/env python3
"""V220 读数：真实账本上的跨轮自省，两遍的附言与分数。

⛔ 不改判据（判据在 PREREG_v220.md）。本脚本只做三件事：
   ① 从 `order_k.txt` 念装置；
   ② 从探针文件取 `code_agent` 收到的**真实 stdin**，切出追加段；
   ③ 从 `runs.jsonl` 取每题分数。
⚠️ 切边界的口径（V219 的教训 124）：追加段前面有 `answer_task()` 拼的 `"\\n\\n"`。
   这里用「首次出现 `[外部评审]`/`[语义记忆]` 的位置」定位，不靠长度差 ——
   长度差正是 V219 首版翻车的地方。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

D = Path("bench/evidence/l3_s302")
SUITE = [json.loads(l) for l in (D / "suite_sub2.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
PROMPT = {t["id"]: t["prompt"] for t in SUITE}
IDS = list(PROMPT)
MARK = ("[外部评审]", "[语义记忆]")


def split_tail(task: str, task_id: str) -> str:
    """切出「题面之后」的那一段（含分隔符），用于判 P5/P6。"""
    tail = task[len(PROMPT[task_id]):]
    # 去掉 answer_task 的 "\n\n" 连接符：只认"以块标记开头"的部分
    for m in MARK:
        k = tail.find(m)
        if k >= 0:
            return tail[k:]
    return ""


def which_task(task: str) -> str | None:
    for tid, p in PROMPT.items():
        if task.startswith(p):
            return tid
    return None


def main() -> None:
    print("=" * 72)
    print("V220 真实账本跨轮自省 —— 读数")
    print("=" * 72)

    order = [l.split("\t") for l in (D / "order_k.txt").read_text().splitlines() if l.strip()]
    print("\n== 装置（两遍）==")
    for o in order:
        print(f"  pass={o[0]}  {o[1]}  {o[2]}  {o[3]}->{o[4]}")

    crit = D / "crit_v220.jsonl"
    lines = [l for l in crit.read_text(encoding="utf-8").splitlines() if l.strip()] if crit.exists() else []
    print(f"\n== 评审账本（两遍共写）== {len(lines)} 行")
    for l in lines:
        r = json.loads(l)
        print(f"  {r.get('task_id'):24s} score={r.get('score')} why={str(r.get('why'))[:60]}")

    rows = [json.loads(l) for l in Path("bench_out/runs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    base = int((D / "start_runs_k.txt").read_text().split("=")[-1].strip())
    runs = rows[base:base + len(order)]

    print("\n== 前置闸 ==")
    p1 = len(runs) == len(order) and all("rc=0" in o[1] for o in order)
    print(f"  P1 rc=0 两遍：{'PASS' if p1 else 'FAIL'}")

    stats = []
    for r, o in zip(runs, order):
        ext = r.get("kernel_external") or {}
        pt = {t["id"]: t for t in r["report"]["per_task"]}
        log = (D / f"log_k_{o[0]}.log").read_text(encoding="utf-8", errors="replace")
        stats.append({"calls": ext.get("calls"), "tasks": ext.get("tasks"),
                      "na": ext.get("skipped_not_applicable"),
                      "nob": ext.get("skipped_no_budget"),
                      "steps1": log.count("steps=1 执行="),
                      "steps0": log.count("steps=0 执行="),
                      "pt": pt})
    p2 = all(s["calls"] == 2 for s in stats)
    p3 = all(s["steps1"] == 2 and s["steps0"] == 0 for s in stats)
    print(f"  P2 calls==2 两遍：{'PASS' if p2 else 'FAIL'}  {[(s['calls'], s['tasks'], s['na'], s['nob']) for s in stats]}")
    print(f"  P3 steps==1 两遍：{'PASS' if p3 else 'FAIL'}  steps1={[s['steps1'] for s in stats]} steps0={[s['steps0'] for s in stats]}")

    print("\n== 探针：code_agent 收到的真实 stdin ==")
    probe = {}
    for pass_no in ("1", "2"):
        p = D / f"probe_k_{pass_no}.jsonl"
        calls = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []
        probe[pass_no] = []
        print(f"\n  --- pass {pass_no}：{len(calls)} 次调用 ---")
        for c in calls:
            tid = which_task(c["task"])
            tail = split_tail(c["task"], tid) if tid else ""
            probe[pass_no].append((tid, tail))
            print(f"    {str(tid):24s} 入参 {c['task_chars']:3d} 字  追加 {len(tail):3d} 字")
            if tail:
                for ln in tail.splitlines():
                    print(f"      | {ln}")
    p4 = all(len(v) == 2 for v in probe.values())
    print(f"\n  P4 4 次调用全部落盘：{'PASS' if p4 else 'FAIL'}")

    # ── P5 / P6 ────────────────────────────────────────────────────────
    print("\n== 主判据 ==")
    tail1 = dict(probe["1"])
    tail2 = dict(probe["2"])
    t01 = next(i for i in IDS if i.startswith("t01"))
    a, b = tail1.get(t01, ""), tail2.get(t01, "")
    p5 = (a == "")
    print(f"  P5 第①遍 {t01} 追加段为空：{'PASS' if p5 else 'FAIL'}（{len(a)} 字）")

    cond = {
        "(a) 以 [外部评审] 开头": b.startswith("[外部评审]"),
        "(b) 含 通过 与 /6": ("通过" in b and "/6" in b),
        "(c) 通过数 < 6": False,
        "(d) 不含 assert": ("assert" not in b),
    }
    m = re.search(r"通过\s*(\d+)\s*/\s*(\d+)", b)
    if m:
        cond["(c) 通过数 < 6"] = int(m.group(1)) < 6
    p6 = bool(b) and all(cond.values())
    print(f"  P6 第②遍 {t01} 附言非空且是失败形状：{'PASS' if p6 else 'FAIL'}（{len(b)} 字）")
    for k, v in cond.items():
        print(f"      {k}: {'✅' if v else '⛔'}")

    # ── P7 ────────────────────────────────────────────────────────────
    s1 = stats[0]["pt"][t01]["score"]
    s2 = stats[1]["pt"][t01]["score"]
    n1 = sum(1 for x in stats[0]["pt"][t01]["vec"] if x)
    n2 = sum(1 for x in stats[1]["pt"][t01]["vec"] if x)
    p7 = s2 >= s1
    print(f"  P7 第②遍 {t01} 得分 ≥ 第①遍：{'PASS' if p7 else 'FAIL'}"
          f"（{n1}/6 → {n2}/6，score {s1:.4f} → {s2:.4f}）")

    print("\n== 另一题（t04，天花板 6/6，只能当未变块）==")
    t04 = next(i for i in IDS if i.startswith("t04"))
    o1, o2 = tail1.get(t04, ""), tail2.get(t04, "")
    x1 = sum(1 for x in stats[0]["pt"][t04]["vec"] if x)
    x2 = sum(1 for x in stats[1]["pt"][t04]["vec"] if x)
    print(f"  附言 {len(o1)} → {len(o2)} 字；{x1}/6 → {x2}/6")

    verdict = "成立" if (p1 and p2 and p3 and p4 and p5 and p6 and p7) else "UNKNOWN"
    print("\n" + "=" * 72)
    print(f"判决：{verdict}")
    print("  成立 = 真实账本上的跨轮自省链路通了（第②遍自动拿到第①遍的失败形状）")
    print("=" * 72)


if __name__ == "__main__":
    main()
