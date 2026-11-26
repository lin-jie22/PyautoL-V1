#!/usr/bin/env python3
"""Mechanism probe V251 -- why did the `line` arm fail 18/18?

The A/B run did NOT save the raw model text (third repeat of 纪律 227), so the
18/18 syntax_fail cannot be attributed from the run alone.  This probe rebuilds
the SAME prompt for the same 6 cells with the SAME `why` string the device uses,
calls once per cell, and saves the raw text.

Readings, per cell:
  raw_len, n_lines_after_EXPR (does the model want a multi-line patch?),
  parsed line/expr, apply err, score after edit.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path("/Users/linshaojie/V1")
sys.path.insert(0, str(ROOT))

from mw.config import settings                      # noqa: E402
from mw.llm import Message as Msg, build_llm        # noqa: E402
from mw.reflect import (apply_edit, build_proposal_prompt, failed_actuals,  # noqa: E402
                        parse_edit, trace_failure, verify)

D = pathlib.Path(__file__).resolve().parent
STATE = pathlib.Path("/tmp/v251_a")
SUITE = pathlib.Path("/tmp/v251_suite_d")

tasks = {t["id"]: t for t in
         (json.loads(l) for l in SUITE.joinpath("g7d_spec_incomplete.jsonl")
          .read_text(encoding="utf-8").splitlines() if l.strip())}
obs = [json.loads(l) for l in
       (STATE / "goal_obs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
best: dict[str, float] = {}
for r in obs:
    best[r["task_id"]] = max(best.get(r["task_id"], -1.0), float(r["score"]))
impl = {}
for r in obs:
    if float(r["score"]) == best[r["task_id"]]:
        impl[r["task_id"]] = r["impl_sha"]

cells = sorted(k for k, v in best.items() if v < 1.0)
llm = build_llm(settings().llm)
out = []
for c in cells:
    t = tasks[c]
    code = (STATE / "candidates" / (impl[c] + ".py")).read_text(encoding="utf-8")
    acts = failed_actuals(code, t["tests"])
    tr = trace_failure(code, t["tests"])
    # exact string from run.py::_swap_candidates (start_src != degraded)
    why = (f"当前最好的一次是 {best[c]:.4f}（共 {len(t['tests'])} 条断言）。"
           f"请给出**把剩下的失败断言一起修好**的完整实现。")
    p = build_proposal_prompt(t["prompt"], code, why, acts, trace=tr, edit=True)
    txt = llm.complete([Msg(role="user", content=p)], max_tokens=900)
    ln, ex = parse_edit(txt)
    new, err = apply_edit(code, ln, ex)
    tail = txt.split("EXPR", 1)[1].split("\n")[1:] if "EXPR" in txt else []
    rec = {"cell": c, "base": best[c], "prompt_bytes": len(p.encode()),
           "raw": txt, "raw_lines": len(txt.splitlines()),
           "extra_lines_after_expr": len([x for x in tail if x.strip()]),
           "line": ln, "expr": ex, "err": err,
           "score_after": verify(new, t["tests"]) if new else None}
    out.append(rec)
    print(f"{c:24s} base={best[c]:.2f} lines={rec['raw_lines']} "
          f"extra={rec['extra_lines_after_expr']} LINE={ln} err={err!r} "
          f"after={rec['score_after']}")
    print(f"    raw={txt[:220]!r}")

(D / "mech_v251.jsonl").write_text(
    "\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n", encoding="utf-8")
print(f"\nsaved mech_v251.jsonl ({len(out)} cells)")
