#!/usr/bin/env python3
"""断言打分器的分辨力探针（与 calibrate 同一套纪律）。

一把尺子必须先证明自己**分辨得出中段**，否则它拿到的任何分数都没有意义 ——
只证明"满分 vs 零分可分"太弱：那与一把五天不动的尺子没有本质区别。

本探针用任务自带的三档校准材料（oracle / partial / null）跑一遍，
要求三者**严格有序**，并把逐条明细打出来，证明"为什么错"是可归因的。

用法：python3 bench/probe_assertion.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mw.bench import Suite  # noqa: E402

CMD = [sys.executable, str(ROOT / "bench" / "scorers" / "assertion.py")]
SUITE = ROOT / "bench" / "tasks_lang" / "explain.jsonl"


def score(task, answer: str) -> tuple[float | None, dict | str]:
    payload = json.dumps({"task": task.as_dict(), "answer": answer},
                         ensure_ascii=False)
    p = subprocess.run(CMD, input=payload, capture_output=True, text=True)
    if p.returncode != 0:
        return None, f"rc={p.returncode} {p.stderr.strip()[:100]}"
    d = json.loads(p.stdout.strip().splitlines()[-1])
    return float(d["score"]), d


def main() -> int:
    s = Suite.load(SUITE)
    print(f"任务集 {s.name}（{len(s)} 题，sha={s.sha()[:12]}）")
    print()
    print(f"{'task':22} {'oracle':>7} {'partial':>8} {'null':>6}  有序?")
    ok_all = True
    for t in s.tasks:
        got: list[float] = []
        for ans in t.calibration_material():
            sc, _ = score(t, ans)
            got.append(-1.0 if sc is None else sc)
        ok = got[0] > got[1] > got[2]
        ok_all &= ok
        print(f"{t.id:22} {got[0]:7.4f} {got[1]:8.4f} {got[2]:6.4f}"
              f"  {'✅' if ok else '❌'}")

    print()
    print("中段有序（oracle > partial > null）：", "✅ 全部成立" if ok_all else "❌ 有反例")

    # 逐条明细：证明"为什么错"是可归因的，而不是一句"我不对"
    t0 = s.tasks[0]
    _, d0 = score(t0, t0.partial)
    print()
    print(f"样例 —— {t0.id} 的 partial 答案：")
    print(f"  {t0.partial}")
    print(f"  why: {d0['why']}")
    for x in d0["detail"]:
        print(f"    {'✔' if x['ok'] else '✘'} {x['id']}: {x['why']}")

    # 反证：空任务（缺 assertions）必须判"不可判"，不是 0 分
    p = subprocess.run(CMD, input=json.dumps({"task": {"id": "x"}, "answer": "a"}),
                       capture_output=True, text=True)
    print()
    print(f"缺 assertions ⇒ rc={p.returncode}（2 = 不可判，不是 0 分）"
          f"  {'✅' if p.returncode == 2 else '❌'}")

    # 反证：未知 kind 也必须判不可判
    p2 = subprocess.run(CMD, input=json.dumps(
        {"task": {"id": "x", "assertions": [{"id": "k", "kind": "magic", "why": "w"}]},
         "answer": "a"}), capture_output=True, text=True)
    print(f"未知 kind     ⇒ rc={p2.returncode}（2 = 不可判）"
          f"  {'✅' if p2.returncode == 2 else '❌'}")

    return 0 if (ok_all and p.returncode == 2 and p2.returncode == 2) else 1


if __name__ == "__main__":
    raise SystemExit(main())
