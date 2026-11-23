#!/usr/bin/env python3
"""V234 装置自测（§305）：**执行轨迹** + **假诊断的两处修复**。

每条都打印 `PASS/FAIL`，退出码 = 失败条数（0 = 全通）。
⛔ 判据必须能**变红**：T10 专门证明「旧装置编出来的 SyntaxError」是真的存在过，
   否则 T1 的「修好了」只是一句自述。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT))

from mw.reflect import (as_code, build_proposal_prompt, failed_actuals,  # noqa: E402
                        trace_failure, verify)

FAILS = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global FAILS
    if not ok:
        FAILS += 1
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail and not ok else ""))


def load_task(tid: str) -> dict:
    for ln in open(HERE / "suite_sub2.jsonl", encoding="utf-8"):
        d = json.loads(ln)
        if d["id"] == tid:
            return d
    raise SystemExit(f"suite 里没有 {tid}")


def load_answer(tid: str, path: Path) -> str:
    for ln in open(path, encoding="utf-8"):
        d = json.loads(ln)
        if d["task_id"] == tid:
            return d["answer"]
    raise SystemExit(f"账本里没有 {tid}")


T01 = load_task("t01_parse_duration")
#: 起点 = V231 R2 落盘的那一份（4/6）。两臂都用它，⛔ 不换起点。
BASE = load_answer("t01_parse_duration", HERE / "crit_v231_r2_after.jsonl")
ORACLE = T01["oracle"]
TESTS = T01["tests"]

# ── T1/T2/T10：围栏必须先剥（V232 的假 SyntaxError 就是没剥造成的）───────────
old = failed_actuals.__wrapped__ if hasattr(failed_actuals, "__wrapped__") else None  # noqa: F841
acts = failed_actuals(BASE, TESTS)
check("T1 剥围栏后实测值里没有假 SyntaxError", not any("SyntaxError" in a for a in acts),
      f"acts={acts}")
check("T1b 两条失败断言都拿到实际值", len(acts) == 2 and "None" in acts[1], f"acts={acts}")
check("T2 剥围栏后起点 = 4/6", verify(BASE, TESTS) == 4, f"got={verify(BASE, TESTS)}")

try:
    compile("```python\ndef f():\n    return 1\n```", "<old>", "exec")
    old_raises = False
except SyntaxError:
    old_raises = True
check("T10 旧装置确实会因围栏抛 SyntaxError（反证：bug 真存在过）", old_raises)
check("T10b 新装置对同一段围栏算得出 1/1",
      verify("```python\ndef f():\n    return 1\n```", ["assert f() == 1"]) == 1)

# ── T3/T4：轨迹必须**指认到根因**（不是"多给了一段文字"）───────────────────
segs = trace_failure(BASE, TESTS)
check("T3 两条失败各一段轨迹", len(segs) == 2, f"segs={len(segs)}")
seg_m = next((s for s in segs if "10m" in s), "")
seg_h = next((s for s in segs if "1h30m" in s), "")
check("T3b 10m 段指到 `int('')` 那一行", "第 17 行" in seg_m and "int(parts[1]" in seg_m,
      seg_m)
check("T3c 10m 段给出当时 parts=['10', '']", "parts=['10', '']" in seg_m, seg_m)
check("T3d 10m 段给出被吞掉的 ValueError", "ValueError" in seg_m and "invalid literal" in seg_m,
      seg_m)
check("T4 1h30m 段给出 parts=['1', '30m']", "parts=['1', '30m']" in seg_h, seg_h)
check("T4b 1h30m 段给出真正的返回值 3600（少算了 1800）", "⇒返回=3600" in seg_h, seg_h)

# ── T5/T9：确定性与预算 ───────────────────────────────────────────────────
check("T5 同一份代码两次轨迹逐字节相同", trace_failure(BASE, TESTS) == segs)
check("T9 每段行数受 max_rows 约束",
      all(len(s.splitlines()) <= 8 for s in segs), f"{[len(s.splitlines()) for s in segs]}")
check("T6 对照：满分代码没有轨迹（负例）", trace_failure(ORACLE, TESTS) == [],
      f"{trace_failure(ORACLE, TESTS)}")

# ── T7/T8：prompt 的增量必须**只有那一段** ────────────────────────────────
goal = T01["prompt"]
why = "通过 4/6；失败: assert parse_duration('1h30m') == 5400 ⇒ AssertionError"
p233 = build_proposal_prompt(goal, BASE, why, acts)
p233b = build_proposal_prompt(goal, BASE, why, acts, trace=())
check("T7 trace 为空时与 V233 的入参**逐字节相同**", p233 == p233b)
p234 = build_proposal_prompt(goal, BASE, why, acts, trace=segs)
head = p233.split("要求：")[0]
check("T8 加轨迹后只在【执行轨迹】处多一段",
      p234.startswith(head) and "【执行轨迹】" in p234 and "第 17 行" in p234)
check("T8b 「要求：」之后的原文一字不差（只是前面插了一段）",
      p233.split("要求：")[-1] in p234)

# ── T11：`as_code` 与打分器同一个口径 ────────────────────────────────────
from mw.verify import extract_code  # noqa: E402
check("T11 as_code 与打分器 extract_code 逐字节一致",
      as_code(BASE) == extract_code(BASE), "两者不一致 ⇒ 自检与判分不同源")

print(f"\nFAILS={FAILS}")
sys.exit(FAILS)
