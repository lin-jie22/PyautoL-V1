#!/usr/bin/env python3
"""V198b —— 事后补的一格（**非预注册**，结果里必须标「事后」）。

G3 证明空实现 33/33 过闸。但「过闸」只说它格式合格。
本臂问下一句：**闸放过去的东西，值多少分？**
零模型：空实现直接交给真打分器 `bench/scorers/code_exec.py`。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]
SCORER = ROOT / "bench" / "scorers" / "code_exec.py"
SUITE_DIR = ROOT / "bench" / "heldout"
EXTRA = ROOT / "bench" / "tasks" / "coding_small.jsonl"

sys.path.insert(0, str(OUT))
from arms_v198 import fn_name, load_answers, load_scores, load_suites  # noqa: E402


def score(task: dict, answer: str) -> dict:
    payload = json.dumps({"task": task, "answer": answer}, ensure_ascii=False)
    p = subprocess.run([sys.executable, str(SCORER)], input=payload,
                       capture_output=True, text=True, timeout=180)
    raw = (p.stdout or "").strip()
    if not raw:
        return {"score": None, "why": f"打分器无输出 rc={p.returncode}"}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"score": None, "why": raw[:120]}


def main() -> int:
    suites, answers, scores = load_suites(), load_answers(), load_scores()
    keys = sorted(k for k in answers if k in scores and k[0] in suites)
    rows = []
    for suite, tid in keys:
        task = suites[suite][tid]
        goal = str(task.get("prompt") or "")
        fn = fn_name(goal)
        ans = "```python\ndef %s(*a, **k):\n    return None\n```" % fn
        r = score(task, ans)
        rows.append({"suite": suite, "id": tid, "fn": fn,
                     "gate_ok": True,          # 已由 G3 实测
                     "nil_score": r.get("score"), "why": str(r.get("why") or "")[:120],
                     "baseline_score": scores[(suite, tid)]})
        print(f"  {suite}/{tid:24s} nil={r.get('score')} base={scores[(suite,tid)]:.3f}")

    ok = [x for x in rows if isinstance(x["nil_score"], (int, float))]
    n = len(ok)
    mean_nil = sum(float(x["nil_score"]) for x in ok) / n if n else float("nan")
    nz = sum(1 for x in ok if float(x["nil_score"]) > 1e-9)
    print()
    print("=" * 70)
    print(f"G4 空实现打分：n={len(rows)}  有效={n}  mean={mean_nil:.4f}  得分>0 的格={nz}")
    print(f"   对照：同池真实答案 mean={sum(scores[k] for k in keys)/len(keys):.4f}")
    if ok:
        print(f"   空实现最高分={max(float(x['nil_score']) for x in ok):.4f} "
              f"最低={min(float(x['nil_score']) for x in ok):.4f}")
        for x in sorted(ok, key=lambda y: -float(y["nil_score"]))[:5]:
            print(f"     {x['suite']}/{x['id']:22s} nil={x['nil_score']} base={x['baseline_score']:.3f}")
    (OUT / "nil_scores_v198.json").write_text(
        json.dumps({"rows": rows, "mean_nil": mean_nil, "n": n}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print("\n→", OUT / "nil_scores_v198.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
