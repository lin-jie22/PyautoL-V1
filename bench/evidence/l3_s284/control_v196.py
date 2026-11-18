#!/usr/bin/env python3
"""V196 装置自检（**事后补**，不是预注册的押）：草稿到底有没有进模型。

两格，第三臂 **C（伤害性草稿）**：把该题函数交成一个空壳 `def fn(*a, **k): return None`。

判据（跑前写死）：
  · 若 `C` 的分数 **低于** 同格 `N` 且答案与 `R` **逐字不同** ⇒ 草稿真的进了模型（装置没哑火）
  · 若 `C` 的答案与 `R` **逐字相同**（sha 相同）⇒ 草稿没进模型 ⇒ **本轮 R/P 两臂全部作废**
"""
from __future__ import annotations

import hashlib
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
    frozen, _ = m.load_frozen()
    scores = m.load_scores()
    cells = sorted([(s, i) for (s, i), r in scores.items() if float(r["score"]) < 1.0])[:2]
    done = m.done_keys()
    for suite, tid in cells:
        if (suite, tid, "C") in done:
            print(f"{suite}/{tid} C 已有 ⇒ 跳过")
            continue
        task = suites[suite][tid]
        fn = (scores[(suite, tid)].get("fn") or "").strip() or "solve"
        draft = f"```python\ndef {fn}(*a, **k):\n    return None\n```"
        payload = f"{task['prompt']}\n\n【上游草稿】\n{draft}"
        res = m.call_agent(payload, {"AGENT_DEADLINE_S": "90",
                                     "V1_LLM_API_KEY": "ollama",
                                     "V1_LLM_MODEL": "qwen2.5:7b"})
        sc = m.score_answer(task, res.get("answer") or "") if res.get("ok") else {"score": None}
        row = {"suite": suite, "id": tid, "arm": "C",
               "baseline_score": float(scores[(suite, tid)]["score"]),
               "answer_sha": hashlib.sha256(str(res.get("answer") or "").encode()).hexdigest()[:16],
               "answer_chars": len(str(res.get("answer") or "")),
               "draft_echo": (str(res.get("answer") or "").strip() == draft.strip()) if res.get("ok") else None,
               "meta_tests": (res.get("meta") or {}).get("tests"),
               "score": sc.get("score"), "why": str(sc.get("why") or "")[:160],
               "ok": bool(res.get("ok")), "err": str(res.get("error") or "")[:200],
               "model": str((res.get("meta") or {}).get("model") or ""),
               "elapsed_s": res.get("elapsed_s"), "draft_from": "sabotage",
               "draft_chars": len(draft)}
        m.append(row)
        print(f"{suite}/{tid} C score={row['score']} (N={row['baseline_score']}) "
              f"sha={row['answer_sha'][:8]} echo={row['draft_echo']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
