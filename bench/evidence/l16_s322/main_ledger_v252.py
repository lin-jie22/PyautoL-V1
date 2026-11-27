"""主账本（唯一带 caliber 的账本）逐行口径表。

判什么：V251 §2 用它排除了其它套件（"struct_convert 1.0/0.6/1.0/1.0/1.0、
g7_numeric 全 1.0"）。若那些 1.0 的模型/尺子与 V251 的装置不同，
"饱和"就是**跨口径结论**，不能用来排除套件。
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

LEDGER = Path("/Users/linshaojie/V1/state/score_ledger.jsonl")


def main() -> int:
    rows = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    print(f"主账本 {len(rows)} 行")
    print(f"{'task':30} {'score':>6} {'model':18} {'ssha':8} {'T':>4} {'budget':6} {'fam':22}")
    print("-" * 106)
    for d in sorted(rows, key=lambda x: (x.get("family", ""), x.get("task_id", ""))):
        cal = d.get("caliber") or {}
        model = (cal.get("llm_cond") or {}).get("model") or "?"
        ssha = str(cal.get("scorer_sha") or "?")[:8]
        temp = cal.get("temperature")
        budget = cal.get("budget_tag") or "?"
        print(f"{str(d.get('task_id'))[:30]:30} {float(d.get('score', -1)):6.2f} "
              f"{model:18} {ssha:8} {('-' if temp is None else f'{temp:g}'):>4} "
              f"{budget:6} {str(d.get('family'))[:22]:22}")

    print()
    g: dict[str, list[float]] = collections.defaultdict(list)
    for d in rows:
        cal = d.get("caliber") or {}
        model = (cal.get("llm_cond") or {}).get("model") or "?"
        ssha = str(cal.get("scorer_sha") or "?")[:8]
        g[f"{model}|ssha={ssha}|T={cal.get('temperature')}"].append(float(d["score"]))
    print(f"{'口径':50} {'n':>4} {'mean':>6} {'min':>5} {'max':>5}")
    for k, xs in sorted(g.items(), key=lambda kv: -len(kv[1])):
        print(f"{k[:50]:50} {len(xs):4d} {sum(xs)/len(xs):6.3f} {min(xs):5.2f} {max(xs):5.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
