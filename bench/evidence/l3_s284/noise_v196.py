#!/usr/bin/env python3
"""V196 P6 噪声地板：把 2 格 R 重跑一次（arm=R2），量同处理下的重复差异。

⛔ 只多跑 2 格，不扩分母、不改判据。选格规则跑前定死：按 (suite,id) 排序取**前 2 格**。
"""
from __future__ import annotations

import importlib.util
import json
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
    frozen, ans_rows = m.load_frozen()
    scores = m.load_scores()
    cells = sorted([(s, i) for (s, i), r in scores.items() if float(r["score"]) < 1.0])[:2]
    done = m.done_keys()
    for suite, tid in cells:
        if (suite, tid, "R2") in done:
            print(f"{suite}/{tid} R2 已有 ⇒ 跳过")
            continue
        task = suites[suite][tid]
        draft = str(frozen[(suite, tid)].get("answer") or "")
        payload = f"{task['prompt']}\n\n【上游草稿】\n{draft}"
        res = m.call_agent(payload, {"AGENT_DEADLINE_S": "90",
                                     "V1_LLM_API_KEY": "ollama",
                                     "V1_LLM_MODEL": "qwen2.5:7b"})
        sc = m.score_answer(task, res.get("answer") or "") if res.get("ok") else {"score": None}
        row = {"suite": suite, "id": tid, "arm": "R2",
               "baseline_score": float(scores[(suite, tid)]["score"]),
               "answer_sha": "", "answer_chars": len(str(res.get("answer") or "")),
               "draft_echo": (str(res.get("answer") or "").strip() == draft.strip()) if res.get("ok") else None,
               "meta_tests": (res.get("meta") or {}).get("tests"),
               "score": sc.get("score"), "why": str(sc.get("why") or "")[:160],
               "ok": bool(res.get("ok")), "err": str(res.get("error") or "")[:200],
               "model": str((res.get("meta") or {}).get("model") or ""),
               "elapsed_s": res.get("elapsed_s"), "draft_from": "own",
               "draft_chars": len(draft)}
        if row["ok"]:
            import hashlib
            row["answer_sha"] = hashlib.sha256(str(res["answer"]).encode()).hexdigest()[:16]
        m.append(row)
        print(f"{suite}/{tid} R2 score={row['score']} echo={row['draft_echo']} {row['elapsed_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
