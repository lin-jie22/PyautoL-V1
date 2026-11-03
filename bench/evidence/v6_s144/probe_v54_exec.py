#!/usr/bin/env python3
"""V54（§120）零模型探针：进收尾之前**到底下发过几次**。

⛔ 零模型调用：`_Stub` 是记录型桩 —— 规划请求返回候选 JSON，`answer()` 请求
   返回一段固定文本，其它（预演）返回空串。全脚本不发一次网络请求。

四格：
  S1 零下发 + **无约定产物** ⇒ 准入空轮出口（`verdict` 应仍是 `ANSWER`：本轮不改判据）
  S2 零下发 + 有约定产物（`report.md`）⇒ 同一出口（`verdict=FAIL`）
  S3 **三次成功下发** + 无约定产物 ⇒ 同一出口（`zero_exec` 必须是 0 —— 反向闸）
  S4 直接调 `_close_out`（`ledger=None`，AS20 那种单元级入口）⇒ 不许炸

用法（两态各跑一次，`--root` 指向该态的工作树）：
  python3 probe_v54_exec.py --root /tmp/v54/wtA   > p0_before.txt
  python3 probe_v54_exec.py --root <repo>          > p1_after.txt
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

from mw import config as cfg                        # noqa: E402
from mw import planner as PL                        # noqa: E402
from mw.core import Core                            # noqa: E402
from mw.memory import MemoryStore                   # noqa: E402
from mw.tools import catalog_sha                    # noqa: E402

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
    """日志收集器。⛔ `Core.log` 默认写**文件**（`_Log(paths.log_file)`），
    不是 stdout —— 只 `redirect_stdout` 会收到空字符串，读起来像"一行日志都没有"。
    """

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
        self.planning: list[str] = []
        self.answer_calls = 0

    @staticmethod
    def _txt(m) -> str:
        return str(m.get("content", "")) if isinstance(m, dict) else str(getattr(m, "content", ""))

    def complete(self, messages, max_tokens=None):
        sysmsg = self._txt(messages[0]) if messages else ""
        if sysmsg == PL._ANSWER_SYS:
            self.answer_calls += 1
            return ANSWER_TEXT
        if "规划器" not in sysmsg:
            return ""
        self.planning.append(sysmsg)
        k = len(self.planning)
        if k <= self.n_ok:
            # ⚠️ 每轮**一个**候选：主循环一旦有候选成功就停在本轮（其余候选是
            #    "失败后的替代品"）⇒ 一轮塞三个只会执行一个。要三次下发就三轮。
            # ⚠️ query 逐轮不同：同一签名会被"重复闸"跳过（缺席冒充零）。
            return json.dumps({"candidates": [
                {"intent": f"检索第{k}条相关记忆", "tool": "recall",
                 "args": {"query": f"v54-{k}"}}]},
                ensure_ascii=False)
        return json.dumps({"candidates": [dict(NA_CAND)]}, ensure_ascii=False)


def _mk(tag: str, goal: str, csv: bool):
    d = Path(tempfile.mkdtemp(prefix=f"v54_{tag}_"))
    ws = d / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    if csv:
        (ws / "sales.csv").write_text(CSV, encoding="utf-8")
    s = cfg.settings()
    lim = dc.replace(s.limits, max_steps=10, state_chars=2400)
    s2 = dc.replace(s, limits=lim, paths=dc.replace(s.paths, workspace=ws))
    return d, ws, s2


def _run(tag: str, goal: str, *, n_ok: int, csv: bool):
    d, ws, s2 = _mk(tag, goal, csv)
    llm = _Stub(n_ok=n_ok)
    sink = _Sink()
    core = Core(s2, llm=llm, memory=MemoryStore(d / "m.db"), memory_mode="raw",
                log=sink)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = core.run(goal, budget_factor=2)
    return core, out, llm, sink.text(), ws


def _cell(tag: str, goal: str, *, n_ok: int, csv: bool) -> dict:
    core, out, llm, log, ws = _run(tag, goal, n_ok=n_ok, csv=csv)
    st = out.stats or {}
    co = st.get("closeout") or {}
    ex = st.get("exec") or {}
    rec = {
        "tag": tag,
        "status": out.status,
        "verdict": out.verdict,
        "reason": out.reason,
        "exec": ex,
        "zero_exec": co.get("zero_exec"),
        "zero_exec_by_reason": co.get("zero_exec_by_reason"),
        "by_reason": co.get("by_reason"),
        "answer_sha": hashlib.sha256((out.answer or "").encode("utf-8")).hexdigest()[:16],
        "answer_len": len(out.answer or ""),
        "log": log,
        "planning": len(llm.planning),
    }
    return rec


def _direct_closeout(tag: str) -> dict:
    """S4：`ledger=None` 的单元级入口（AS20 的形态）。"""
    d, ws, s2 = _mk(tag, GOAL_TEXT, False)
    core = Core(s2, llm=_Stub(n_ok=0), memory=MemoryStore(d / "m.db"), memory_mode="raw")
    core.log = lambda m: None                       # noqa: ARG005
    core._goal_text = GOAL_TEXT
    core._reset_verify(GOAL_TEXT)

    def _done(status: str, ans: str, why: str):      # `_finish` 还要 history 参数
        return core._finish(status, ans, why, [])

    out = core._close_out(GOAL_TEXT, ["[step 1] 只有一行历史"], _done,
                          reason="not_applicable", detail="单元级")
    co = (out.stats or {}).get("closeout") or {}
    return {"status": out.status, "verdict": out.verdict, "reason": out.reason,
            "zero_exec": co.get("zero_exec"),
            "zero_exec_by_reason": co.get("zero_exec_by_reason")}


def main() -> int:
    print(f"ROOT={ROOT}")
    print("── 四格读数 ──")
    s1 = _cell("s1", GOAL_TEXT, n_ok=0, csv=False)
    s2 = _cell("s2", GOAL_ART, n_ok=0, csv=True)
    s3 = _cell("s3", GOAL_TEXT, n_ok=3, csv=False)
    try:
        s4 = _direct_closeout("s4")
        s4_err = ""
    except Exception as e:                          # noqa: BLE001
        s4, s4_err = {}, f"{type(e).__name__}: {e}"

    for r in (s1, s2, s3):
        print(f"  [{r['tag']}] status={r['status']} verdict={r['verdict']}"
              f" exec={json.dumps(r['exec'], ensure_ascii=False)}"
              f" zero_exec={r['zero_exec']}"
              f" by_reason={json.dumps(r['zero_exec_by_reason'], ensure_ascii=False)}"
              f" 规划={r['planning']}次 答案={r['answer_len']}字 sha={r['answer_sha']}")
        print(f"        reason={r['reason']}")
    print(f"  [s4] status={s4.get('status')} verdict={s4.get('verdict')}"
          f" zero_exec={s4.get('zero_exec')} err={s4_err or '无'}")
    print(f"        reason={s4.get('reason')}")
    print()
    print("── 日志锚点 ──")
    for r in (s1, s3):
        for ln in r["log"].splitlines():
            if "⊞" in ln or ln.lstrip().startswith("■"):
                print(f"  [{r['tag']}] {ln.strip()[:180]}")
    print()
    print("── 判定 ──")
    _check("S1 verdict 仍是 ANSWER（本轮**不改判据**）", s1["verdict"] == "ANSWER",
           f"verdict={s1['verdict']}")
    _check("S1 零下发被记成一次计数", s1["zero_exec"] == 1,
           f"zero_exec={s1['zero_exec']}")
    _check("S1 零下发按入口分列到 not_applicable",
           (s1["zero_exec_by_reason"] or {}).get("not_applicable") == 1,
           f"{s1['zero_exec_by_reason']}")
    _check("S1 reason 写明「一次都没下发」", "一次都没下发" in (s1["reason"] or ""),
           (s1["reason"] or "")[:90])
    _check("S1 收尾入口日志写着「一次都没下发」", "⊞ 此前**一次都没下发**" in s1["log"],
           "命中" if "⊞" in s1["log"] else "一个字都没有")
    _check("S2 有约定产物时 verdict 仍是 FAIL（判据不动）",
           s2["verdict"] == "FAIL", f"verdict={s2['verdict']}")
    _check("S2 零下发同样计数", s2["zero_exec"] == 1, f"zero_exec={s2['zero_exec']}")
    _check("S3 三次成功下发 ⇒ exec=3/3/0",
           s3["exec"] == {"dispatched": 3, "ok": 3, "fail": 0},
           json.dumps(s3["exec"], ensure_ascii=False))
    _check("S3 反向闸：做过事就不许记成零下发的收尾", s3["zero_exec"] == 0,
           f"zero_exec={s3['zero_exec']}")
    _check("S3 reason 写明下发次数与成败",
           "此前已下发 3 次（成功 3、失败 0）" in (s3["reason"] or ""),
           (s3["reason"] or "")[:90])
    _check("S3 `■` 行有「执行=」段", "执行=3下发/3成/0败" in s3["log"],
           "命中" if "执行=" in s3["log"] else "没有这一段")
    _check("S4 单元级入口（ledger=None）不炸", not s4_err, s4_err or "ok")
    _check("S4 也写出「一次都没下发」",
           "一次都没下发" in (s4.get("reason") or ""), (s4.get("reason") or "")[:90])
    print()
    print("── P5 交付物指纹（两态必须逐字节相同）──")
    for r in (s1, s2, s3):
        print(f"  {r['tag']} answer_sha={r['answer_sha']} len={r['answer_len']}")
    print()
    sha_no = catalog_sha(wire_external=False, agents_path=str(ROOT / "agents.json"))
    sha_ext = catalog_sha(wire_external=True, agents_path=str(ROOT / "agents.json"))
    _check("catalog_sha 两个口径不变（⇒ 这不是「改提示词」）",
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
