#!/usr/bin/env python3
"""V254 —— 给定外部判定原文，13 格修得动吗（可修性上界）。

用法：
  python3 run_v254.py --dry      # 预检：建提示 + 断言剂量 + 尺子三档校准，0 次模型调用
  python3 run_v254.py            # 真跑：2 臂 × 13 格 = 26 次生成

⛔ 预注册见 PREREG_v254.md（跑前落盘）。本脚本不写记忆、不写账本。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import statistics
import sys
import time
import urllib.request

ROOT = pathlib.Path("/Users/linshaojie/V1")
sys.path.insert(0, str(ROOT))

EV = ROOT / "bench" / "evidence" / "l18_s324"
ANSWERS = ROOT / "bench" / "evidence" / "l17_s323" / "answers_a.jsonl"
ANALYSE = ROOT / "bench" / "evidence" / "l17_s323" / "analyse_v253.json"
SUITE = pathlib.Path("/tmp/v253_suite/all39.jsonl")

ARMS = ("A", "B", "C")


# ── 提示构造 ────────────────────────────────────────────────────────────────
def first_failure(why: str) -> str:
    """取 `通过 k/n；失败: <assert …> ⇒ <Err> | …` 里的**第一段**，一字不改。"""
    body = str(why or "")
    idx = body.find("失败:")
    if idx < 0:
        return ""
    body = body[idx + len("失败:"):].strip()
    return body.split(" | ")[0].strip()


def build_prompt(arm: str, task: dict, answer: str, fail: str, other_fail: str) -> str:
    """三臂共用同一前缀（题面 + 当前实现）；A 只多一句要求，B/C 多一行判定。

    ⚠️ B 与 C 必须**等长**：C 是「同模板、换一条别格断言」的安慰剂。
    第一版只往短的一侧补空格 ⇒ 当别格断言更长时 C 反而超长（实测 5/13 不等），
    那就不再是安慰剂而多了一个「长度」变量。现在按两行中**较长者**对齐。
    """
    head = (f"题面：\n{task['prompt']}\n\n当前实现：\n{answer}\n\n")
    if arm == "A":
        return head + "请重新实现一遍。只输出完整代码。"
    tail_b = f"自检未通过：1 项未过：{fail}"
    tail_c = f"自检未通过：1 项未过：{other_fail}"
    width = max(len(tail_b), len(tail_c))
    tail_b = tail_b + " " * (width - len(tail_b))
    tail_c = tail_c + " " * (width - len(tail_c))
    tail = tail_b if arm == "B" else tail_c
    return head + tail + "\n请修正后给出完整代码。"


# ── 打分 ────────────────────────────────────────────────────────────────────
def make_scorer():
    from mw.bench import ExternalScorer
    path = ROOT / "bench" / "scorers" / "code_exec.py"
    return ExternalScorer([sys.executable, str(path)], source=path)


def speed_gate() -> float:
    vals = []
    for _ in range(3):
        body = json.dumps({"model": "qwen2.5:7b",
                           "prompt": "Count from 1 to 120, comma separated, nothing else.",
                           "stream": False,
                           "options": {"num_predict": 220, "temperature": 0}}).encode()
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as fh:
            d = json.load(fh)
        ec, ed = int(d.get("eval_count") or 0), float(d.get("eval_duration") or 0) / 1e9
        v = round(ec / ed, 2) if ed > 0 else 0.0
        vals.append(v)
        print(f"  speed {v} tok/s", flush=True)
    med = statistics.median(vals)
    print(f"  median={med} gate(>=15)={med >= 15}")
    return med


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    tasks = {t["id"]: t for t in
             (json.loads(l) for l in SUITE.read_text(encoding="utf-8").splitlines() if l.strip())}
    an = json.loads(ANALYSE.read_text(encoding="utf-8"))
    cells = sorted(k for k, v in an["per_cell"].items()
                   if float((v.get("A") or {}).get("score") or 0.0) < 1.0)
    answers = {}
    for line in ANSWERS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("kind") == "answer":
            answers[r["task_id"]] = r["answer"]

    print(f"cells={len(cells)} answers={len(answers)}")
    missing = [c for c in cells if c not in answers]
    if missing:
        print(f"❌ 缺产物：{missing}")
        return 2

    # 每格的失败断言 + 别格的失败断言（C 臂用：取 id 排序后的下一个）
    fails = {c: first_failure(an["per_cell"][c]["A"]["why"]) for c in cells}
    empty = [c for c in cells if not fails[c]]
    if empty:
        print(f"❌ 取不到失败断言：{empty}")
        return 2

    plan = {c: {arm: build_prompt(arm, tasks[c], answers[c], fails[c],
                                 fails[cells[(cells.index(c) + 1) % len(cells)]])
                for arm in ARMS} for c in cells}

    # ── P1：剂量断言 ────────────────────────────────────────────────────
    bad_p1 = []
    for c in cells:
        if fails[c] not in plan[c]["B"]:
            bad_p1.append((c, "B 不含断言"))
        if fails[c] in plan[c]["A"]:
            bad_p1.append((c, "A 含断言"))
        if fails[c] in plan[c]["C"]:
            bad_p1.append((c, "C 含本格断言"))
        if len(plan[c]["C"]) != len(plan[c]["B"]):
            bad_p1.append((c, f"C/B 长度不等 {len(plan[c]['C'])}/{len(plan[c]['B'])}"))
    print(f"P1 剂量断言：{'OK 13/13' if not bad_p1 else bad_p1}")
    if bad_p1:
        return 3

    print(f"{'cell':26}{'A':>6}{'B':>6}{'C':>6}  first_failure")
    for c in cells:
        print(f"{c:26}{len(plan[c]['A']):6d}{len(plan[c]['B']):6d}{len(plan[c]['C']):6d}  "
              f"{fails[c][:70]}")

    # ── 尺子三档校准（复现 V253 的 oracle/partial/null）──────────────────
    sc = make_scorer()
    bad_cal = []
    for c in cells:
        t = tasks[c]
        got = {"oracle": sc.score(t, t["oracle"]).score,
               "partial": sc.score(t, t["partial"]).score,
               "null": sc.score(t, t["null"]).score}
        if not (got["oracle"] == 1.0 and got["null"] == 0.0 and got["partial"] < 1.0):
            bad_cal.append((c, got))
    print(f"尺子校准：{'OK 13/13' if not bad_cal else bad_cal}")
    if bad_cal:
        return 4

    if args.dry:
        print("DRY OK（0 次模型调用）")
        return 0

    # ── 速度闸 ──────────────────────────────────────────────────────────
    if speed_gate() < 15:
        print("NO_RUN: 速度闸未过（预注册 §3）")
        return 5

    # ── 真跑 ────────────────────────────────────────────────────────────
    from mw.config import Settings
    from mw.llm import HTTPLLM, Message
    from run import DIRECT_SYSTEM

    s = Settings()
    llm = HTTPLLM(s.llm)
    rows = []
    t0 = time.time()
    for i, c in enumerate(cells, 1):
        for arm in ("A", "B"):
            st = time.time()
            txt = llm.complete([Message("system", DIRECT_SYSTEM),
                                Message("user", plan[c][arm])],
                               max_tokens=int(s.limits.llm_max_tokens),
                               temperature=0.0)
            dt = round(time.time() - st, 1)
            got = sc.score(tasks[c], txt)
            row = {"cell": c, "arm": arm, "score": got.score,
                   "why": (got.why or "")[:400],
                   "vec": list(getattr(got, "vec", []) or []),
                   "sha256": hashlib.sha256(txt.encode("utf-8")).hexdigest()[:12],
                   "chars": len(txt), "secs": dt, "answer": txt}
            rows.append(row)
            print(f"[{i}/13] {c:24} {arm} score={got.score:.4f} {dt:5.1f}s "
                  f"chars={len(txt)}", flush=True)
    print(f"总耗时 {time.time() - t0:.1f}s")

    # ── 条件臂 C：仅在 Δ ≥ 1 时跑 ────────────────────────────────────────
    by = {(r["cell"], r["arm"]): r for r in rows}
    delta = [c for c in cells if by[(c, "B")]["score"] > by[(c, "A")]["score"]]
    print(f"Δ = {len(delta)}：{delta}")
    if delta:
        for c in delta:
            txt = llm.complete([Message("system", DIRECT_SYSTEM),
                                Message("user", plan[c]["C"])],
                               max_tokens=int(s.limits.llm_max_tokens),
                               temperature=0.0)
            got = sc.score(tasks[c], txt)
            rows.append({"cell": c, "arm": "C", "score": got.score,
                         "why": (got.why or "")[:400],
                         "vec": list(getattr(got, "vec", []) or []),
                         "sha256": hashlib.sha256(txt.encode("utf-8")).hexdigest()[:12],
                         "chars": len(txt), "secs": 0.0, "answer": txt})
            print(f"  C {c:24} score={got.score:.4f}", flush=True)

    out = {"model": s.llm.model, "temperature": 0.0,
           "scorer_sha": sc.sha(), "cells": cells,
           "fails": fails, "rows": rows}
    (EV / "readings_v254.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"⇒ 写 {EV / 'readings_v254.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
