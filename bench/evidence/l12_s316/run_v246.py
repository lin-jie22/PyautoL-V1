"""V246 装置：自验回路（最多 3 轮，每轮把自己产出的失败喂回去）。

⛔ 首轮提示与 v245 的 B 臂**逐字节相同**（同一构造函数、同一 why、同一 actuals、同一 trace）。
⛔ 回路自锁的处理（装置决定，写进 RESULTS）：若新一轮代码与上一轮**完全同**，
   则下一步 prompt 也完全相同 ⇒ 确定性装置上必然同产出（纪律 133）。
   ⇒ 该条到此为止并记 `stalled=True`，不空烧调用。
   这不是「帮它」，是把「重复同一个 prompt」这一步省掉；成效仍要自己挣。
"""
from __future__ import annotations

import json
import pathlib
import sys

D = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(D))
import run_v245 as R  # noqa: E402

MAX_ROUNDS = 3


def _failing(code: str, tests) -> list[str]:
    """按生产同一口径（每断言独立命名空间）列出**失败**的断言原文。"""
    out = []
    for t in tests:
        ns: dict = {}
        try:
            exec(compile(code, "<cand>", "exec"), ns)      # noqa: S102
            exec(compile(t, "<test>", "exec"), ns)         # noqa: S102
        except Exception:                                  # noqa: BLE001
            out.append(t)
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5:7b")
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--out", default=str(D / "readings_v246.jsonl"))
    a = ap.parse_args()
    task = json.loads([x for x in R.SUITE.read_text(encoding="utf-8").splitlines()
                       if x.strip()][0])
    goal, tests = task["prompt"], task["tests"]
    start = R.START_CODE.read_text(encoding="utf-8")
    assert R.verify(start, tests) == 5, "起点不是 5/6 ⇒ PREREG §5 UNKNOWN"

    #: 首轮与 v245 的 B 臂逐字节同：why 用生产里那句，actuals/trace 由起点算。
    why0 = "通过 5/6；失败: assert parse_duration('10m') == 600 ⇒ AssertionError"
    p0 = R.build_proposal_prompt(goal, start, why0, R.failed_actuals(start, tests),
                                 trace=R.trace_failure(start, tests))
    #: 与 v245 的 B 臂对齐的凭据：两者必须逐字节同（2307 bytes / 1bdea4fc92d67817）
    print(f"首轮 prompt {len(p0.encode())} bytes sha={R._sha(p0)}"
          f"（v245 B 臂 = 2307 / 1bdea4fc92d67817）")
    assert R._sha(p0) == "1bdea4fc92d67817", "首轮与 v245 B 臂不同 ⇒ 不是单变量"

    out = pathlib.Path(a.out)
    out.write_text("", encoding="utf-8")
    for i in range(a.n):
        code, rounds, scores, stalled = start, 0, [], False
        prompt = p0
        for r in range(MAX_ROUNDS):
            txt, meta = R._ask(a.model, prompt, 900)
            cand, _ = R.parse_proposal(txt)
            if not cand.strip():
                scores.append(0)
                rounds += 1
                break
            got = R.verify(cand, tests)
            scores.append(got)
            rounds += 1
            if got == len(tests):
                code = cand
                break
            if cand == code:                       # 回路自锁：下一步 prompt 会一模一样
                stalled = True
                break
            code = cand
            fails = _failing(code, tests)
            why_r = (f"通过 {got}/{len(tests)}；失败: {fails[0]} ⇒ AssertionError"
                     if fails else f"通过 {got}/{len(tests)}")
            prompt = R.build_proposal_prompt(goal, code, why_r,
                                             R.failed_actuals(code, tests),
                                             trace=R.trace_failure(code, tests))
        row = {"i": i, "model": a.model, "rounds": rounds, "scores": scores,
               "final": scores[-1] if scores else 0,
               "stalled": stalled, "sha": R._sha(code),
               "code": code, **meta}
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"  D#{i:02d} 轮={rounds} 逐轮={scores} 自锁={stalled}", flush=True)

    rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]
    r0 = [r["scores"][0] for r in rows if r["scores"]]
    fin = [r["final"] for r in rows]
    print(f"\n首轮 6/6 = {sum(1 for s in r0 if s == 6)}/{len(r0)}"
          f"  最终 6/6 = {sum(1 for s in fin if s == 6)}/{len(fin)}"
          f"  自锁 {sum(1 for r in rows if r['stalled'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
