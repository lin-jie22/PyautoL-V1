#!/usr/bin/env python3
"""V52 零模型探针：交付被打回之后，走的是「让位」还是「重规划」。

⛔ 零模型调用：`_RecLLM` 是记录型桩，每次规划都返回**同一形状**的 3 个 `finish` 候选。
⛔ 每个候选的 `answer` 带**本次规划的序号**：否则第 2 轮的同一条会被
   `attempted_sigs` 判成「重复动作」直接跳过（`repeats_skipped`），
   读数会变成「模型什么都没提」而真相是「我们把它拦了」——缺席冒充零的镜像。

同一份脚本必须在**改前 / 改后**各跑一次（`p0_before.txt` / `p1_after.txt`）：
  A 态 = HEAD + 交付闸（`cand_failover` 分支仍是 `continue`）
  B 态 = A 态 + 该支改成 `break`（带错误原文重规划）
"""
from __future__ import annotations

import contextlib
import dataclasses as dc
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw import config as cfg                       # noqa: E402
from mw import verify as vfy                       # noqa: E402
from mw.core import Core                           # noqa: E402
from mw.memory import MemoryStore                  # noqa: E402
from mw.tools import ToolResult, catalog_sha       # noqa: E402

GOAL = ("工作目录里有 sales.csv（列为 月份,区域,金额）。请计算每个区域的金额合计，"
        "按合计从高到低生成一张 markdown 表格，写入工作目录下的 report.md。"
        "表格两列为 区域|合计。")
CSV = "月份,区域,金额\n1月,华北,1200\n2月,华北,1500\n1月,华南,800\n2月,华南,1200\n1月,华东,700\n"
PLANNER_MARK = "规划器"

FAILS: list[str] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


class _RecLLM:
    """记录型桩：**只对规划器**给候选，其它调用返回空串。"""

    def __init__(self) -> None:
        self.planner_calls: list[str] = []      # 每次规划请求的**全文**（拼好的 messages）
        self.other_calls = 0

    @staticmethod
    def _txt(m) -> str:
        if isinstance(m, dict):
            return str(m.get("content", ""))
        return str(getattr(m, "content", ""))

    def complete(self, messages, max_tokens=None):
        sysmsg = self._txt(messages[0]) if messages else ""
        if PLANNER_MARK not in sysmsg:
            self.other_calls += 1
            return ""
        body = "\n".join(self._txt(m) for m in messages)
        self.planner_calls.append(body)
        k = len(self.planner_calls)                     # 本次是第几次规划
        cands = [{"intent": f"直接交第{k}版第{i}稿", "tool": "finish",
                  "args": {"answer": f"| 区域 | 合计 |\n|---|---|\n| 华北 | 2700 |\n(第{k}版第{i}稿)"}}
                 for i in (1, 2, 3)]
        return json.dumps({"candidates": cands}, ensure_ascii=False)


def _run(tag: str):
    d = Path(tempfile.mkdtemp(prefix=f"v52_{tag}_"))
    ws = d / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "sales.csv").write_text(CSV, encoding="utf-8")

    s = cfg.settings()
    limits = dc.replace(s.limits, cand_failover=1, max_steps=8, state_chars=2400)
    s2 = dc.replace(s, limits=limits, paths=dc.replace(s.paths, workspace=ws))
    llm = _RecLLM()
    core = Core(s2, llm=llm, memory=MemoryStore(d / "m.db"), memory_mode="raw")

    hits: list[tuple[int, str, dict]] = []
    inner = core.tools

    class _Reg:
        def __getattr__(self, name):
            return getattr(inner, name)

        def dispatch(self, name, args):
            hits.append((int(core.ledger.steps_used), str(name), dict(args)))
            return inner.dispatch(name, args)

    core.tools = _Reg()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = core.run(GOAL, budget_factor=2)
    return core, out, hits, llm, buf.getvalue(), ws


def _gate_raises(tag: str) -> None:
    """S5：判据自己炸掉时**不许**冒充任何一种结论（不拦交付）。"""
    d = Path(tempfile.mkdtemp(prefix=f"v52_{tag}_"))
    ws = d / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    s = cfg.settings()
    s2 = dc.replace(s, paths=dc.replace(s.paths, workspace=ws))
    core = Core(s2, llm=_RecLLM(), memory=MemoryStore(d / "m.db"), memory_mode="raw")
    core.log = lambda m: None                        # noqa: ARG005
    core._goal_text = GOAL
    core._reset_verify(GOAL)
    orig = vfy.check_delivery

    def _boom(*a, **k):
        raise RuntimeError("判据自己炸了")

    vfy.check_delivery = _boom
    try:
        r = core._gate_finish(ToolResult(True, "非空答案"))
    finally:
        vfy.check_delivery = orig
    _check("S5 判据异常不拦（计数 + 放行）",
           r.ok is True and core.verify_stats["artifact_error"] == 1
           and core.verify_stats["artifact_failed"] == 0,
           f"ok={r.ok} err={core.verify_stats['artifact_error']}"
           f" failed={core.verify_stats['artifact_failed']}")


def main() -> int:
    core, out, hits, llm, log, ws = _run("probe")
    st = out.stats
    vfy_st = st.get("verify") or {}
    fo = st.get("failover") or {}
    plan = st.get("plan") or {}

    fin = [h for h in hits if h[1] == "finish"]
    shell = [h for h in hits if h[1] == "run_shell"]
    steps_of_fin = [h[0] for h in fin]
    same_round = len(steps_of_fin) >= 2 and len(set(steps_of_fin)) < len(steps_of_fin)
    err_text = [c for c in llm.planner_calls if "题面要求的产物不存在" in c]

    print("── 读数 ──")
    print(f"  status={out.status} verdict={(st.get('delivery') or {}).get('verdict')}")
    print(f"  verify.artifact_checks={vfy_st.get('artifact_checks')}"
          f" artifact_failed={vfy_st.get('artifact_failed')}"
          f" failed={vfy_st.get('failed')} exhausted={vfy_st.get('exhausted')}")
    print(f"  plan.rounds={plan.get('rounds')} cands_total={plan.get('cands_total')}"
          f" by_tool={json.dumps(plan.get('by_tool') or {}, ensure_ascii=False)}")
    print(f"  failover.used={fo.get('used')} rescued_step={fo.get('rescued_step')}")
    print(f"  规划调用次数={len(llm.planner_calls)}  含错误原文的规划请求={len(err_text)}")
    print(f"  dispatch 序列={[(s, t) for s, t, _ in hits]}")
    print(f"  finish 的步号={steps_of_fin}  run_shell={len(shell)}")
    print(f"  report.md 存在={(ws / 'report.md').exists()}")
    print()

    _check("S1 闸真的打回", int(vfy_st.get("artifact_failed") or 0) >= 1,
           f"artifact_failed={vfy_st.get('artifact_failed')}")
    _check("S2/S3 两态互斥（同一轮多候选 ⇔ 每打回一次问一次）",
           (same_round and int(fo.get("used") or 0) >= 1)
           or ((not same_round) and int(fo.get("used") or 0) == 0),
           f"same_round={same_round} used={fo.get('used')} 规划={len(llm.planner_calls)}")
    if same_round:
        print("     ⇒ 本态 = A（让位）：同一轮里 dispatch 了 "
              f"{len(fin)} 次 finish，步号 {steps_of_fin}")
    else:
        print(f"     ⇒ 本态 = B（重规划）：{len(llm.planner_calls)} 次规划，"
              f"finish 分布在步 {steps_of_fin}")
        _check("S4 重规划请求里带着**缺哪个产物**", len(err_text) >= 1,
               f"命中 {len(err_text)}/{len(llm.planner_calls)}")

    # ⛔ S6 不许写成 `or True`：恒真断言是「红=无」的第一种形态（纪律 2）。
    #    两个口径在 V51 收尾时冻结为下面这两个值，本轮两态都必须逐字符相同。
    sha_no = catalog_sha(wire_external=False, agents_path="agents.json")
    sha_ext = catalog_sha(wire_external=True, agents_path="agents.json")
    _check("S6 catalog_sha 两个口径逐字符不变（⇒ 这不是「改提示词」）",
           sha_no == "8decb60895d4a972" and sha_ext == "29091404966ca8e1",
           f"{sha_no[:16]} / {sha_ext[:16]}")

    _gate_raises("gate")
    print()
    if FAILS:
        print(f"⛔ 未通过：{FAILS}")
        return 1
    print("✅ 本态全部断言通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
