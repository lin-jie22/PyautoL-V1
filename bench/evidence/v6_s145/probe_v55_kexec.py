#!/usr/bin/env python3
"""V55（§121）零模型探针：按题的「下发次数 × 验收结论」进不进得了账本。

⛔ 零模型调用：`_Stub` 是记录型桩 —— 规划请求返回候选 JSON，`answer()` 请求
   返回固定文本，其它（预演）返回空串。全脚本不发一次网络请求。

三格（都是**真实** `Core.run`，只是模型被换成桩）：
  S1 一道 0 下发的题（纯文本题，模型只会提结构上不适用的候选）⇒ `zero_dispatch_tasks=1`
  S2 一道 3 次成功下发的题（带 `report.md` 约定产物）⇒ `zero_dispatch_tasks=0`（反向闸）
  S3 把 S1+S2 并进同一个累加器 ⇒ 合计与两个 `by_*` 字典分列

改前（HEAD）没有 `_kexec_absorb` ⇒ 本探针**必然红**（这正是 p0 的读数）。

用法（两态各跑一次，`--root` 指向该态的工作树）：
  python3 probe_v55_kexec.py --root /tmp/v55/wtA   > p0_before.txt
  python3 probe_v55_kexec.py                        > p1_after.txt
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses as dc
import hashlib
import io
import json
import sys
import tempfile
from pathlib import Path

_ap = argparse.ArgumentParser()
_ap.add_argument("--root", default="")
_args, _ = _ap.parse_known_args()
ROOT = Path(_args.root).resolve() if _args.root else Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import run as RUN                                    # noqa: E402
from mw import config as cfg                         # noqa: E402
from mw import planner as PL                         # noqa: E402
from mw.core import Core                             # noqa: E402
from mw.memory import MemoryStore                    # noqa: E402
from mw.tools import catalog_sha                     # noqa: E402

GOAL_TEXT = "用一段话说明快速排序的平均时间复杂度为什么是 O(n log n)。"
GOAL_ART = ("工作目录里有 sales.csv（列为 月份,区域,金额）。请计算每个区域的金额合计，"
            "按从高到低生成一张 markdown 表格，写入工作目录下的 report.md。"
            "表格两列为 区域|合计。")
CSV = "月份,区域,金额\n1月,华北,1200\n2月,华北,1500\n1月,华南,800\n2月,华南,1200\n1月,华东,700\n"
ANSWER_TEXT = "平均 O(n log n)：每层划分代价 O(n)，期望递归深度 O(log n)，两者相乘。"

#: 结构上**不适用**于上面两道题的候选（题面没有「函数名必须是 X」）。
NA_CAND = {"intent": "交给 code_agent 生成实现", "tool": "agent:code_agent",
           "args": {"task": "写一份实现"}, "rationale": "它能跑代码"}

FAILS: list[str] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


class _Sink:
    """日志收集器。⛔ `Core.log` 默认写**文件**，只 `redirect_stdout` 收不到。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, msg) -> None:
        self.lines.append(str(msg))

    def text(self) -> str:
        return "\n".join(self.lines)


class _Stub:
    """规划器桩。`n_ok` = 前几轮给"能跑通"的候选，之后只给结构上不适用的。"""

    def __init__(self, n_ok: int = 0) -> None:
        self.n_ok = n_ok
        self.planning = 0

    @staticmethod
    def _txt(m) -> str:
        return str(m.get("content", "")) if isinstance(m, dict) else str(
            getattr(m, "content", ""))

    def complete(self, messages, max_tokens=None):     # noqa: ANN001, ARG002
        sysmsg = self._txt(messages[0]) if messages else ""
        if sysmsg == PL._ANSWER_SYS:
            return ANSWER_TEXT
        if "规划器" not in sysmsg:
            return ""
        self.planning += 1
        k = self.planning
        if k <= self.n_ok:
            # ⚠️ 每轮**一个**候选：主循环一旦有候选成功就停在本轮。要三次下发给三轮。
            # ⚠️ query 逐轮不同：同一签名会被"重复闸"跳过（缺席冒充零）。
            return json.dumps({"candidates": [
                {"intent": f"检索第{k}条相关记忆", "tool": "recall",
                 "args": {"query": f"v55-{k}"}}]}, ensure_ascii=False)
        return json.dumps({"candidates": [dict(NA_CAND)]}, ensure_ascii=False)


def _cell(tag: str, goal: str, *, n_ok: int, csv: bool) -> dict:
    d = Path(tempfile.mkdtemp(prefix=f"v55_{tag}_"))
    ws = d / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    if csv:
        (ws / "sales.csv").write_text(CSV, encoding="utf-8")
    s = cfg.settings()
    lim = dc.replace(s.limits, max_steps=10, state_chars=2400)
    s2 = dc.replace(s, limits=lim, paths=dc.replace(s.paths, workspace=ws))
    sink = _Sink()
    stub = _Stub(n_ok=n_ok)                     # ⚠️ 留住引用：`core.llm` 未必叫这个名字
    core = Core(s2, llm=stub, memory=MemoryStore(d / "m.db"),
                memory_mode="raw", log=sink)
    with contextlib.redirect_stdout(io.StringIO()):
        out = core.run(goal, budget_factor=2)
    st = out.stats or {}
    return {"tag": tag, "out": out, "status": out.status, "verdict": out.verdict,
            "exec": st.get("exec") or {},
            "answer_sha": hashlib.sha256((out.answer or "").encode()).hexdigest()[:16],
            "answer_len": len(out.answer or ""), "log": sink.text(),
            "planning": stub.planning}





def main() -> int:
    print(f"ROOT={ROOT}")
    print(f"run.py 有 `_kexec_absorb`? {hasattr(RUN, '_kexec_absorb')}")
    print("── 三格读数（真实 Core.run，模型换桩）──")
    s1 = _cell("s1", GOAL_TEXT, n_ok=0, csv=False)
    s2 = _cell("s2", GOAL_ART, n_ok=3, csv=True)
    for r in (s1, s2):
        print(f"  [{r['tag']}] status={r['status']} verdict={r['verdict']}"
              f" exec={json.dumps(r['exec'], ensure_ascii=False)}"
              f" 规划={r['planning']}次 答案={r['answer_len']}字 sha={r['answer_sha']}")

    # ── S3：把两格并进同一个累加器（等价于一次 2 题跑批的账本）──
    acc: dict = {}
    err = ""
    try:
        acc = RUN._kexec_zero()
        zero_keys = sorted(acc.keys())
        RUN._kexec_absorb(acc, s1["out"])
        RUN._kexec_absorb(acc, s2["out"])
    except Exception as e:                       # noqa: BLE001
        zero_keys, err = [], f"{type(e).__name__}: {e}"
    print(f"  [s3] 累加器={json.dumps(acc, ensure_ascii=False, default=str)}"
          f" err={err or '无'}")
    print(f"       零点键={zero_keys}")
    print()
    print("── 判定 ──")
    _check("S1 0 下发（装置给的格）", s1["exec"].get("dispatched") == 0,
           json.dumps(s1["exec"], ensure_ascii=False))
    _check("S2 3 次下发（反向闸的格）", s2["exec"].get("dispatched") == 3,
           json.dumps(s2["exec"], ensure_ascii=False))
    _check("C1 `run` 模块有 `_kexec_zero` / `_kexec_absorb`",
           hasattr(RUN, "_kexec_zero") and hasattr(RUN, "_kexec_absorb"),
           "缺函数" if err else "ok")
    _check("C1 零点含六个键（tasks/dispatched/ok/fail/zero/两个字典）",
           zero_keys == sorted(["tasks", "dispatched", "ok", "fail",
                                "zero_dispatch_tasks", "by_verdict",
                                "zero_dispatch_by_verdict"]), str(zero_keys))
    _check("C2 合计：tasks=2 dispatched=3 ok=3 fail=0",
           (acc.get("tasks"), acc.get("dispatched"), acc.get("ok"), acc.get("fail"))
           == (2, 3, 3, 0), json.dumps(acc, ensure_ascii=False, default=str))
    _check("C3 反向闸：0 下发那格被记成 zero_dispatch_tasks=1",
           acc.get("zero_dispatch_tasks") == 1, str(acc.get("zero_dispatch_tasks")))
    _check("C3 零下发按验收结论分列（ANSWER:1）",
           (acc.get("zero_dispatch_by_verdict") or {}).get("ANSWER") == 1,
           json.dumps(acc.get("zero_dispatch_by_verdict") or {}, ensure_ascii=False))
    _check("C4 两个字典**分列**：by_verdict 记两题、zero_dispatch_by_verdict 只记一题",
           sum((acc.get("by_verdict") or {}).values()) == 2
           and sum((acc.get("zero_dispatch_by_verdict") or {}).values()) == 1,
           f"{acc.get('by_verdict')} / {acc.get('zero_dispatch_by_verdict')}")
    _check("C4 有下发的那题也进了 by_verdict（否则合计对不上 tasks）",
           (acc.get("by_verdict") or {}).get(s2["verdict"], 0) >= 1,
           json.dumps(acc.get("by_verdict") or {}, ensure_ascii=False))
    print()
    print("── C5 交付物指纹（两态必须逐字节相同）──")
    for r in (s1, s2):
        print(f"  {r['tag']} answer_sha={r['answer_sha']} len={r['answer_len']}"
              f" verdict={r['verdict']} status={r['status']}")
    print()
    sha_no = catalog_sha(wire_external=False, agents_path=str(ROOT / "agents.json"))
    sha_ext = catalog_sha(wire_external=True, agents_path=str(ROOT / "agents.json"))
    _check("C6 catalog_sha 两个口径不变（⇒ 这不是「改提示词」）",
           (sha_no, sha_ext) == ("8decb60895d4a972", "29091404966ca8e1"),
           f"{sha_no[:16]} / {sha_ext[:16]}")
    print()
    if FAILS:
        print(f"⛔ 未通过（{len(FAILS)} 条）：{FAILS}")
        return 1
    print("✅ 本态全部断言通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
