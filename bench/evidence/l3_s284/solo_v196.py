#!/usr/bin/env python3
"""V196 事后补的对照臂 **S（solo）**：题面原文、不带任何草稿，同一个外部能力跑一遍。

为什么必须有它（事后登记）：预注册只写了 N/R/P，而 **N 是内核的答案、R/P 是外部能力的答案**
⇒ `R − N` 把「换流程」与「加草稿」混在一起。S 把「换流程」单独拉出来：

    R − S = 草稿内容的净效（同一流程、只差草稿）
    P − S = 无关草稿的净效
    S − N = 纯换流程的效应

⛔ 不改预注册的 6 条押；S 只用于解释。
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_arms():
    spec = importlib.util.spec_from_file_location("a196", HERE / "arms_v196.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> int:
    m = load_arms()
    suites = m.load_suites()
    _, _ = m.load_frozen()
    scores = m.load_scores()
    cells = sorted([(s, i) for (s, i), r in scores.items() if float(r["score"]) < 1.0])
    done = m.done_keys()
    for suite, tid in cells:
        if (suite, tid, "S") in done:
            print(f"{suite}/{tid} S 已有 ⇒ 跳过")
            continue
        task = suites[suite][tid]
        res = m.call_agent(str(task["prompt"]),
                           {"AGENT_DEADLINE_S": "90", "V1_LLM_API_KEY": "ollama",
                            "V1_LLM_MODEL": "qwen2.5:7b"})
        sc = m.score_answer(task, res.get("answer") or "") if res.get("ok") else {"score": None}
        row = {"suite": suite, "id": tid, "arm": "S",
               "baseline_score": float(scores[(suite, tid)]["score"]),
               "answer_sha": hashlib.sha256(str(res.get("answer") or "").encode()).hexdigest()[:16],
               "answer_chars": len(str(res.get("answer") or "")),
               "draft_echo": None,
               "meta_tests": (res.get("meta") or {}).get("tests"),
               "score": sc.get("score"), "why": str(sc.get("why") or "")[:160],
               "ok": bool(res.get("ok")), "err": str(res.get("error") or "")[:200],
               "model": str((res.get("meta") or {}).get("model") or ""),
               "elapsed_s": res.get("elapsed_s"), "draft_from": "", "draft_chars": 0}
        m.append(row)
        print(f"{suite}/{tid} S score={row['score']} (N={row['baseline_score']}) {row['elapsed_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
