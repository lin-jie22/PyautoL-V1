"""V245 机制探针：改了哪里？（不是判据，只解释读数）

⛔ 为什么要有这一步：主跑只存了 sha，没存代码 ⇒ 「改了但没对」这句话
   无法指认改在哪。这是装置缺陷（纪律 216 的同型：结论要读的东西必须落盘）。
   本探针**不参与判据**，只回答「非照抄的那几条，动的是哪一行」。

⛔ 每臂 6 次（不是 16）：这里要的是「改在哪」的定性，不是命中率。
"""
from __future__ import annotations

import difflib
import json
import pathlib
import sys

D = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(D))
import run_v245 as R  # noqa: E402

task = json.loads([x for x in R.SUITE.read_text(encoding="utf-8").splitlines() if x.strip()][0])
goal, tests = task["prompt"], task["tests"]
code = R.START_CODE.read_text(encoding="utf-8")
why = ("通过 5/6；失败: assert parse_duration('10m') == 600 ⇒ AssertionError")
actuals = R.failed_actuals(code, tests)
trace = R.trace_failure(code, tests)

prompts = {
    "A": R.build_proposal_prompt(goal, code, why, actuals),
    "B": R.build_proposal_prompt(goal, code, why, actuals, trace=trace),
    "C": R.build_proposal_prompt(goal, code, why, actuals, trace=trace) + R.C_TAIL,
}

out = D / "mech_v245.jsonl"
out.write_text("", encoding="utf-8")
for arm in ("A", "B", "C"):
    for i in range(6):
        txt, meta = R._ask("qwen2.5:7b", prompts[arm], 900)
        stripped = R._ROOT_CAUSE.sub("", txt)
        cand, _ = R.parse_proposal(stripped if stripped != txt else txt)
        got = R.verify(cand, tests) if cand.strip() else 0
        diff = [d for d in difflib.unified_diff(
            code.splitlines(), cand.splitlines(), "start", arm, n=0, lineterm="")]
        row = {"arm": arm, "i": i, "score": got, "same": cand == code,
               "sha": R._sha(cand) if cand.strip() else None,
               "diff": "\n".join(diff)[:1800], **meta}
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"  {arm}#{i} {got}/6 same={row['same']} diff行={len(diff)}", flush=True)
print("→", out)
