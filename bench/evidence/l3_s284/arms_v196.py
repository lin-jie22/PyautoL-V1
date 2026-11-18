#!/usr/bin/env python3
"""V196 —— 中继值不值：N（基线，冻结）/ R（本题旧答案当草稿）/ P（别题旧答案当安慰剂）。

⛔ 不改内核、不改 agents.json。本轮只做手工反事实：
   直接把拼接好的 stdin 送给 `external/code_agent.py`，答案交给**真打分器** CLI。

断点续跑：每跑完一格就 append 一行到 `cells_v196.jsonl`；
重跑时已存在的 (suite,id,arm) 直接跳过 —— 崩了不用重头烧模型时间。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
CELLS = OUT / "cells_v196.jsonl"
AGENT = ROOT / "external" / "code_agent.py"
SCORER = ROOT / "bench" / "scorers" / "code_exec.py"
FROZEN = ROOT / "bench" / "evidence" / "l3_s282" / "answers_v194.jsonl"
READINGS = ROOT / "bench" / "evidence" / "l3_s282" / "readings_v194.json"
SUITE_DIR = ROOT / "bench" / "heldout"
EXTRA_SUITE = ROOT / "bench" / "tasks" / "coding_small.jsonl"
DEADLINE_S = 90
PROC_TIMEOUT = 150


def load_suites() -> dict[str, dict[str, dict]]:
    """suite 名（stem）→ {task_id: 题记录}。⚠️ suite 名是 **stem**，不是文件名（V194 自踩 2）。"""
    out: dict[str, dict[str, dict]] = {}
    for p in sorted(SUITE_DIR.glob("*.jsonl")) + [EXTRA_SUITE]:
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        out[p.stem] = {str(r["id"]): r for r in rows}
    return out


def load_frozen() -> tuple[dict[tuple[str, str], dict], list[dict]]:
    """(suite, task_id) → 冻结答案行。套件靠 header 的 run_id 反查（⛔ 不许只用 task_id）。"""
    rows = [json.loads(l) for l in FROZEN.read_text(encoding="utf-8").splitlines() if l.strip()]
    run2suite = {r["run_id"]: str((r.get("meta") or {}).get("suite") or "")
                 for r in rows if r.get("kind") == "header"}
    ans = [r for r in rows if r.get("kind") == "answer"]
    m: dict[tuple[str, str], dict] = {}
    for r in ans:
        suite = run2suite.get(r.get("run_id"), "")
        m[(suite, str(r["task_id"]))] = r
    return m, ans


def load_scores() -> dict[tuple[str, str], dict]:
    d = json.loads(READINGS.read_text(encoding="utf-8"))
    return {(str(r["suite"]), str(r["id"])): r for r in d["rows"]}


def pick_placebo(cell: tuple[str, str], ans: list[dict], run2suite: dict[str, str],
                 own_chars: int) -> dict:
    """别题答案里 `|chars 差|` 最小的；平票按 (task_id, suite) 字典序。⛔ 排除同 task_id。"""
    own_id = cell[1]
    cands = []
    for r in ans:
        if str(r["task_id"]) == own_id:
            continue
        cands.append((abs(int(r.get("chars") or len(str(r.get("answer") or ""))) - own_chars),
                      str(r["task_id"]), run2suite.get(r.get("run_id"), ""), r))
    cands.sort(key=lambda x: (x[0], x[1], x[2]))
    return cands[0][3]


def call_agent(payload: str, env_extra: dict) -> dict:
    env = {**os.environ, **env_extra}
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, str(AGENT)], input=payload,
                           capture_output=True, text=True,
                           timeout=PROC_TIMEOUT, cwd=str(ROOT), env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"进程超时 >{PROC_TIMEOUT}s", "elapsed_s": round(time.time() - t0, 1)}
    raw = (p.stdout or "").strip()
    el = round(time.time() - t0, 1)
    if not raw:
        tail = " | ".join((p.stderr or "").strip().splitlines()[-3:])[:300]
        return {"ok": False, "error": f"rc={p.returncode} stderr={tail}", "elapsed_s": el}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"stdout 不是 JSON: {raw[:160]!r}", "elapsed_s": el}
    err = str(data.get("error") or "")
    ans = str(data.get("answer") or "")
    return {"ok": not err and bool(ans), "error": err, "answer": ans,
            "meta": data.get("meta") or {}, "elapsed_s": el}


def score_answer(task: dict, answer: str) -> dict:
    payload = json.dumps({"task": task, "answer": answer}, ensure_ascii=False)
    p = subprocess.run([sys.executable, str(SCORER)], input=payload,
                       capture_output=True, text=True, timeout=180)
    raw = (p.stdout or "").strip()
    if not raw:
        return {"score": None, "why": f"打分器无输出 rc={p.returncode} {(p.stderr or '')[:120]}"}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"score": None, "why": f"打分器输出非 JSON: {raw[:120]}"}


def done_keys() -> set[tuple[str, str, str]]:
    if not CELLS.exists():
        return set()
    s = set()
    for line in CELLS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        s.add((r["suite"], r["id"], r["arm"]))
    return s


def append(row: dict) -> None:
    with CELLS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()


def main() -> int:
    suites = load_suites()
    frozen, ans_rows = load_frozen()
    scores = load_scores()
    run2suite = {}
    for r in [json.loads(l) for l in FROZEN.read_text(encoding="utf-8").splitlines() if l.strip()]:
        if r.get("kind") == "header":
            run2suite[r["run_id"]] = str((r.get("meta") or {}).get("suite") or "")

    cells = [(s, i) for (s, i), r in scores.items() if float(r["score"]) < 1.0]
    cells.sort()
    print(f"分母 denom={len(cells)}（V194 全池 33 格，剔除 score==1.0 的 10 格）")

    # 预检：每格都得有题面、冻结答案、分数 —— 缺一个就是分母虚（纪律 67）
    miss = []
    for c in cells:
        t = suites.get(c[0], {}).get(c[1])
        f = frozen.get(c)
        if t is None or f is None or not str(t.get("prompt") or "").strip():
            miss.append((c, bool(t), bool(f)))
    if miss:
        print("⛔ 缺料:", miss)
        return 2

    done = done_keys()
    # ⚠️ ollama 不校验 key，但 `code_agent._chat` 见空 key 会**直接拒答**
    #    （"未配置 V1_LLM_API_KEY ⇒ 外部 agent 没有模型可用"）。冒烟时踩到过一次：
    #    不设它，48 次调用会全部变成"未交付"，而读数看起来像"中继没用"。
    _key = os.environ.get("V1_LLM_API_KEY") or "ollama"
    env_goal_only = {"AGENT_DEADLINE_S": str(DEADLINE_S),
                     "V1_LLM_API_KEY": _key,
                     "V1_LLM_MODEL": os.environ.get("V1_LLM_MODEL") or "qwen2.5:7b"}
    total = len(cells) * 2
    n = 0
    for suite, tid in cells:
        task = suites[suite][tid]
        goal = str(task["prompt"])
        own = frozen[(suite, tid)]
        draft = str(own.get("answer") or "")
        plc = pick_placebo((suite, tid), ans_rows, run2suite,
                           int(own.get("chars") or len(draft)))
        plc_draft = str(plc.get("answer") or "")

        for arm, extra in (("R", draft), ("P", plc_draft)):
            key = (suite, tid, arm)
            n += 1
            if key in done:
                print(f"[{n}/{total}] {suite}/{tid} {arm} 已有 ⇒ 跳过")
                continue
            payload = f"{goal}\n\n【上游草稿】\n{extra}"
            res = call_agent(payload, env_goal_only)
            sc = score_answer(task, res.get("answer") or "") if res.get("ok") else {"score": None, "why": res.get("error")}
            row = {
                "suite": suite, "id": tid, "arm": arm,
                "baseline_score": float(scores[(suite, tid)]["score"]),
                "answer_sha": __import__("hashlib").sha256(
                    str(res.get("answer") or "").encode()).hexdigest()[:16] if res.get("ok") else "",
                "answer_chars": len(str(res.get("answer") or "")),
                # 读数（不是判据）：交回来的东西是不是**逐字照抄**了送进去的草稿。
                # ⚠️ 事后加的记录项：预注册里没有这条押，结果里必须标「事后」。
                "draft_echo": (str(res.get("answer") or "").strip() == extra.strip()) if res.get("ok") else None,
                "meta_tests": (res.get("meta") or {}).get("tests"),
                "score": sc.get("score"), "why": str(sc.get("why") or "")[:160],
                "ok": bool(res.get("ok")), "err": str(res.get("error") or "")[:200],
                "model": str((res.get("meta") or {}).get("model") or ""),
                "samples": (res.get("meta") or {}).get("pass_counts"),
                "elapsed_s": res.get("elapsed_s"),
                "draft_from": "own" if arm == "R" else f"{plc.get('task_id')}",
                "draft_chars": len(extra),
            }
            append(row)
            print(f"[{n}/{total}] {suite}/{tid} {arm} score={row['score']} "
                  f"(base {row['baseline_score']}) ok={row['ok']} {row['elapsed_s']}s")
    print("主循环结束。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
