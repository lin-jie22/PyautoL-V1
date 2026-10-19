#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§17-7h 的**反证组**：同一套装置，只把题边界清空关掉 ⇒ 第 2 题的 state 必须脏。

为什么不能只用 AZ18
-------------------
AZ18 断言的是"修好之后是干净的"。只证明这一半，等于只说"现在是绿的"——
它挡不住"这个干净是**别的原因**造成的"（例如 StubLLM 恰好没调 `recall`）。
所以必须再做一个**只改一件事**的对照：

    正常        ：`Core.run()` 进题先 `reset_working()`
    no-op 对照  ：把 `MemoryStore.reset_working` 换成一个**只返回计数、不清空**的桩

其余全部相同（同一份 7f 种子、同一把 hint suite、同一个 StubLLM、同样的 4–5 步）。
判据（写死）：
    · 正常臂   第 2 题**第 1 步**：`[语义记忆]` 之外**没有**任何惯例文本；
    · no-op 臂 第 2 题**第 1 步**：**出现**非本题惯例
      ⇒ 两条同时成立才算"反证组成立"（只成立一半说明这个对照什么都没测到）。

⛔ 零模型调用（StubLLM），不写库、不动证据、不进任何判定。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
for _p in (str(V1), str(V1 / "bench")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if not os.environ.get("V1_STATE_DIR"):
    os.environ["V1_STATE_DIR"] = tempfile.mkdtemp(prefix="v1_7h_ctl_")

from mw.abstractor import ABSTRACT_SOURCE                    # noqa: E402
from mw.core import Core                                     # noqa: E402
from mw.llm import StubLLM                                   # noqa: E402
from mw.memory import MemoryStore                            # noqa: E402
from build_g7d_seed import VARIANTS                          # noqa: E402
from state_audit import audit_state, format_audit            # noqa: E402


def _seed_db(facts: dict[str, str]) -> Path:
    p = Path(tempfile.mkdtemp(prefix="v1_7h_seed_")) / "memory.db"
    m = MemoryStore(p)
    for v in facts.values():
        m.commit_fact(v, source=ABSTRACT_SOURCE, confidence=0.5)
    m.close()
    return p


def _run_two(db: Path, t1: dict, t2: dict, *, disable_reset: bool) -> dict:
    """跑两题，返回第 2 题**每一步**的审计结果（第 1 步单独标出来）。"""
    mem = MemoryStore(db)
    core = Core(llm=StubLLM(), memory=mem, memory_mode="raw")
    if disable_reset:
        # ⛔ 只改这一件事：计数照报，清空不发生。
        mem.reset_working = lambda: 0            # type: ignore[method-assign]
    core.run(t1["prompt"], task_id=t1["id"])
    n_work1 = len(mem.working_view())
    out2 = core.run(t2["prompt"], task_id=t2["id"])
    steps = [audit_state(st, FACTS, t2["id"]) for _it, st in core.state_log]
    a = steps[0] if steps else {"ok": False, "outside_all": [], "own_in_semantic": False}
    a["work_after_t1"] = n_work1
    a["cleared"] = int(out2.stats.get("working_cross_task") or 0)
    a["steps"] = len(steps)
    #: 第 2 步及以后，非语义块里出现的**其它**惯例（同一题内、来自本题自己的 recall）
    a["later"] = sorted({k for s in steps[1:] for k in s["outside"]})
    mem.close()
    return a


def main() -> int:
    rows = [json.loads(l) for l in
            (V1 / "bench" / "heldout" / "g7e_hint_heldout.jsonl")
            .read_text(encoding="utf-8").splitlines() if l.strip()]
    t1 = next(r for r in rows if r["id"] == "g7d2_dedupe_keep")
    t2 = next(r for r in rows if r["id"] == "g7e2_truncate_tail")
    db = _seed_db(FACTS)

    print("== 正常臂（题边界清空生效）==")
    a_ok = _run_two(db, t1, t2, disable_reset=False)
    print("  " + format_audit(a_ok)
          + f"  第1题留下={a_ok['work_after_t1']}条 第2题清掉={a_ok['cleared']}条"
          f" 第2题共={a_ok['steps']}步")
    print(f"     第 2–{max(a_ok['steps'], 2)} 步里，**语义块之外**出现的其它惯例："
          f"{','.join(a_ok['later']) if a_ok['later'] else '无'}"
          f"  ← 若非空：来自本题自己的 `recall` 工具输出（通道行为，**本轮没修**）")

    print("\n== no-op 对照（只关掉清空，其余不动）==")
    a_bad = _run_two(db, t1, t2, disable_reset=True)
    print("  " + format_audit(a_bad)
          + f"  第1题留下={a_bad['work_after_t1']}条 第2题清掉={a_bad['cleared']}条")

    good = (a_ok["ok"] and not a_ok["outside_all"]
            and (not a_bad["ok"]) and bool(a_bad["outside_all"]))
    print("\n== 结论 ==\n  " + (
        "✅ 反证组成立：清空在时第 2 题第 1 步干净、关掉时**脏**"
        f"（漏出 {','.join(a_bad['outside_all'])}）"
        if good else
        "❌ 反证组不成立 ⇒ 这个对照没测到任何东西，AZ18 的'干净'也就无法归因"))
    return 0 if good else 1


FACTS = VARIANTS["7f"]

if __name__ == "__main__":
    raise SystemExit(main())
