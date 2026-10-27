#!/usr/bin/env python3
"""「换形态再问」的入口条件在真实回合里一次也没被满足 —— 量分母，不量措辞。

## 它量的是什么

第六十八轮加了一条出口（`mw/core.py:1161-1195`）：

    本轮**全部**候选都是已试过的动作 ⇒ `stalled += 1`
      ├─ stalled < max_stall  ⇒ 填 `reseek_lines`（把刚被丢掉的动作原文摆回去，
      │                         并告诉模型哪些工具一次没试过）⇒ `continue`
      ├─ stalled ≥ max_stall，墙钟还够一次答案 ⇒ `deliver`（转向交付）
      └─ stalled ≥ max_stall，墙钟不够     ⇒ `give_up`（如实交零）

第九十七轮（§64.6）留了一句待查：「`reseek` 触发之后一次调用都没发出去」。
本轮先把**入口条件**量清楚 —— 结论比那句话更硬：

    `reseek` 的入口是 `repeats == len(cands)`，一个**全等**判据；
    而真实回合里出现的是**混合**（1 个重复 + 1 个被否决/被执行）。

    混合情形掉进 `else` 支（`mw/core.py:1196-1199`）：
       `consecutive_failures += 1` + `⛔ 全部候选被拦`
       ⇒ `stalled` **一次都不加** ⇒ `reseek_lines` **一直是空的**。

⇒ 「换形态再问」这条**唯一**会改变输入（不是改措辞、不是改预算）的出口，
   在真实回合里结构上走不到；能兜底的只剩**熔断**（它数的是"连续做错"，
   出口是交空）与**墙钟**（出口是收尾调用）。

## 为什么这不是"读日志的错觉"

日志只能说明"没发生"，不能说明"为什么没发生"。所以这里**驱动真内核**：
用脚本化模型（零网络）构造两种候选组合，直接看内核走哪一支、哪个计数器动了。

| 臂 | 第 2 轮候选 | 期望走哪一支 | 判据 |
|---|---|---|---|
| `A_allrepeat` | [重复] | all-repeat ⇒ 停滞 | `stalled == 1`、`reseek == 1`、提示词里有 reseek 块 |
| `B_mixed` | [重复, 空答案 finish] | else ⇒ 全部候选被拦 | `stalled == 0`、`reseek == 0`、提示词里**没有** reseek 块 |

两条臂**只差一个候选**（`B` 多一个会被价值层否决的 `finish`），其余逐字相同。

## 反证（`--self-check`，零模型零网络）

| # | 反证 | 防的是什么 |
|---|---|---|
| P1 | `A` 里 `stalled==1` 且提示词**含** reseek 块 | 探针根本没驱动到那一支 ⇒ 后面两条断言恒真 |
| P2 | `B` 里 `stalled==0` 且提示词**不含** reseek 块（同一个探针、同一套断言，两向） | 断言恒假/恒真（只验一个方向等于没验） |
| P3 | 把 `B` 的 `finish` 答案改成非空 ⇒ 那一步会**执行** `finish` ⇒ `B` 的断言必须**红** | 断言被"只要有重复就算停滞"这种粗判据蒙对 |
| P4 | 把 `A` 的第 2 轮加一个 `note` 候选 ⇒ 不再是全等 ⇒ `A` 的断言必须**红** | 全等判据被悄悄放宽 |
| P5 | `max_stall=1` 时 `A` 不进 reseek 而转向交付 ⇒ `A` 的断言必须**红** | reseek 只是一个必然触发的常量而不是闸控出口 |

⛔ P3/P4/P5 是**故意让检测器红**：一个从不红的检测器和一个恒真的断言同罪。

## 用法

    python3 bench/probe_stallpath.py                 # 两臂对拍（零模型零网络）
    python3 bench/probe_stallpath.py --self-check    # P1~P5，五条反证
    python3 bench/probe_stallpath.py --logs <log>…   # 真实回合的分母（只读日志）
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 收尾/规划/预演三个通道的**区分标记**。⛔ 不按调用序数猜：
#: 数法会随"某一步没有预演"而错位，标记法不会（`DemoLLM` 的注释踩过同一个坑）。
_MARK_ANSWER = "请直接给出最终答案"
_MARK_PREDICT = "准备执行的动作"
_MARK_PLAN = "请给出"

#: reseek 块在**实际喂进去的提示词**里的锚点（`mw/planner.py:217`）。
#: ⛔ 用这一句而不是"reseek"这个词：`reseek` 只出现在代码里，
#:    提示词里一个英文字都没有 —— 断言必须锚在**模型真能看见的字**上。
_ANCHOR_RESEK = "你上一次的回答被**拒绝**了"
_ANCHOR_ALLBLOCK = "全部候选被拦"
_ANCHOR_STALL = "个候选全部是已试过的动作"

_ENV = ("V1_STATE_DIR", "V1_LOG_DIR", "V1_WORKSPACE")


@contextlib.contextmanager
def isolated_state(tmp: Path):
    """每个臂一套独立的 state/logs/workspace。

    ⚠️ 必须隔离：`facts` 跨轮累积，两臂共用一套目录会让 B 臂继承 A 臂的记忆，
    那时量到的差异是"记忆多少"，不是"候选组合"。
    """
    old = {k: os.environ.get(k) for k in _ENV}
    os.environ["V1_STATE_DIR"] = str(tmp / "state")
    os.environ["V1_LOG_DIR"] = str(tmp / "logs")
    os.environ["V1_WORKSPACE"] = str(tmp / "workspace")
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _plan(*cands: dict) -> str:
    return json.dumps({"candidates": list(cands)}, ensure_ascii=False)


def _recall(q: str, intent: str = "找已有结论") -> dict:
    return {"intent": intent, "tool": "recall", "args": {"query": q},
            "rationale": "先看有没有现成结论"}


def _finish_empty() -> dict:
    #: 空答案 ⇒ `values.judge` 的 `answer` 否决（真实日志里的同一行：
    #: `⊘ [values:answer] 否决 — 空答案`）。
    return {"intent": "交付", "tool": "finish", "args": {"answer": ""},
            "rationale": "已经够了"}


def _note(text: str = "结论") -> dict:
    return {"intent": "沉淀结论", "tool": "note",
            "args": {"text": text, "source": "selftest", "confidence": 0.9},
            "rationale": "写回记忆"}


class Script:
    """按**调用类型**分派响应的脚本化模型（不是按调用序数）。

    ⚠️ 按序数分派会错位：某一轮若没走预演，后面全部串位 ——
    那时两种候选组合的差别会被"剧本错位"污染，读数就成了装置误差。

    `answers` 是按序消费的**答案通道**剧本。为什么答案通道也要可编排：
    规划器有一条「finish 却没给 answer」的**定向补救**（`mw/planner.py:369-394`），
    它会在内核看到候选之前就把空答案填上 —— 空答案因此**根本到不了价值层**。
    要复现"空答案被否决"这一格，必须让那一发补救返回空（`answer_retry_fail`）。
    """

    def __init__(self, plans: list, answer: str | list[str] = "") -> None:
        self.plans = list(plans)
        self.answers = [answer] if isinstance(answer, str) else list(answer)
        self.n_plan = 0
        self.n_predict = 0
        self.n_answer = 0
        self.plan_prompts: list[str] = []

    def complete(self, messages, *, max_tokens: int = 512,
                 temperature: float | None = None,
                 timeout_s: float | None = None) -> str:  # noqa: D401
        blob = "\n".join(getattr(m, "content", str(m)) for m in messages)
        if _MARK_ANSWER in blob:
            i = min(self.n_answer, len(self.answers) - 1)
            self.n_answer += 1
            return self.answers[i] if self.answers else ""
        if _MARK_PREDICT in blob:
            self.n_predict += 1
            return json.dumps({"expect_ok": True, "expected": "会成功",
                               "confidence": 0.9}, ensure_ascii=False)
        assert _MARK_PLAN in blob, f"未识别的通道：{blob[:120]!r}"
        self.plan_prompts.append(blob)
        #: `ordinal` 是**不饱和**的调用序数；索引会饱和到剧本末位。
        #: ⚠️ 两者混用会让"逐轮变化"的剧本在第 N 轮之后变成**常量**
        #:    —— 那次它自己造出了重复，反证看起来"没有差异"（踩过）。
        ordinal = self.n_plan
        self.n_plan += 1
        got = self.plans[min(ordinal, len(self.plans) - 1)]
        #: 剧本元素可以是**函数**（`ordinal` ⇒ 响应）。为什么需要它：
        #: 反证 P4 要求"每轮都有一个新动作" ⇒ 剧本必须能逐轮变化；
        #: 写死一串就会在串尾重放，而重放**自己**会造出全等（反证失效）。
        return got(ordinal) if callable(got) else got


#: 目标必须**多子句** ⇒ `looks_complex` 为真 ⇒ 走深通道 ⇒ `n=2`，
#: 否则规划器的两个候选会被 `cands = plan.steps[:n]` 截成一个，
#: 「混合」这种组合**根本不可能出现**（截断就是隐形的第一道闸）。
GOAL = ("分析并清理历史遗留的临时文件，然后总结根因与修复方案，"
        "最后把结论写回记忆")


def run_arm(plans: list, *, max_stall: int | None = None,
            answer: str | list[str] = "```python\nprint(1)\n```",
            cf_max: int = 1, cand_budget: int | None = None) -> dict:
    """跑一个臂，返回读数。**每条读数都来自内核自己**（stats / 提示词原文）。

    `cf_max=1`（`V1_MAX_CONSECUTIVE_FAILURES`）是**两臂共用的**测量条件，不是变体：
    它让「混合那一步」之后立刻熔断，于是这一臂的读数**只包含**我们要看的那一支。
    ⚠️ 不加这个旋钮会怎样：候选预算是**累计**的（`spend_candidate`），
       第 2 步用掉 2 个候选后 `cand_left = max(1, 3-3) = 1` ⇒ 第 3 步宽度塌到 1
       ⇒ 混合自己变成了全等 ⇒ 停滞与 reseek **在同一个臂里又发生了**。
       那时终局 stats 已经分不清"哪一步记了停滞"，判据会看起来像红的。
       （这条不是猜想：第一版就是这么撞的，见 RESULTS.md §4 的自踩。）
    """
    from mw import config as cfg
    from mw.core import Core

    with tempfile.TemporaryDirectory() as td:
        with isolated_state(Path(td)):
            if max_stall is not None:
                os.environ["V1_MAX_STALL"] = str(max_stall)
            os.environ["V1_MAX_CONSECUTIVE_FAILURES"] = str(cf_max)
            if cand_budget is not None:
                os.environ["V1_MAX_CANDIDATES"] = str(cand_budget)
            try:
                s = cfg.settings()
                llm = Script(plans, answer=answer)
                core = Core(s, llm=llm, log=None)
                lines: list[str] = []
                core.log = lambda m: lines.append(m)
                out = core.run(GOAL)
            finally:
                if max_stall is not None:
                    os.environ.pop("V1_MAX_STALL", None)
                os.environ.pop("V1_MAX_CONSECUTIVE_FAILURES", None)
                if cand_budget is not None:
                    os.environ.pop("V1_MAX_CANDIDATES", None)

    st = out.stats or {}
    loop = st.get("loop") or {}
    prompts = "\n".join(llm.plan_prompts)
    return {
        "status": out.status,
        # ⚠️ 两个计数器都**不在** stats 顶层：`stalled`/`repeats_skipped` 在
        #    `loop` 里（`_done` 的 extra），`reseek` 在 `planner` 里。
        #    第一版按顶层取，取到 None 却"没报错" —— 三个判据同时假红。
        "stalled": loop.get("stalled"),
        "repeats_skipped": loop.get("repeats_skipped"),
        "reseek": (st.get("planner") or {}).get("reseek"),
        "iterations": out.iterations,
        "n_plan": llm.n_plan,
        "plan_prompts": len(llm.plan_prompts),
        "has_reseek_prompt": _ANCHOR_RESEK in prompts,
        "has_allblock_log": any(_ANCHOR_ALLBLOCK in ln for ln in lines),
        "has_stall_log": any(_ANCHOR_STALL in ln for ln in lines),
        "last_prompt": prompts,
        "lines": lines,
        "answer": out.answer,
    }


#: 两臂只差**一个候选**：`B` 在重复的 `recall` 后面多一个空答案 `finish`。
#: 目标多子句 ⇒ 深通道 ⇒ `n=2`（否则第二个候选会被 `plan.steps[:n]` 截掉，
#: 「混合」这种组合**根本不可能出现**）。两臂的第 3 份剧本都是**最后一份**，
#: 桩会一直重放它 —— 路径长度因此由内核的闸决定，不由剧本长度决定。
_HINT = _recall("python 截断", "再找一次")
ARM_A = [_plan(_recall("python 截断")), _plan(_HINT), _plan(_note())]
#: ⚠️ 空答案那一步要**让规划器的定向补救返回空**（`answer_retry_fail`），
#: 否则它在内核看到候选之前就被填上，价值层那格永远不会触发。
ARM_B = [_plan(_recall("python 截断")),
         _plan(_HINT, _finish_empty())]
#: `B` 的答案剧本：第一发（定向补救）返回空，之后正常。
_B_ANSWERS = ["", "```python\nprint(1)\n```"]


def _assert_a(r: dict) -> list[str]:
    """`A` 的判据：全等 ⇒ 停滞 ⇒ reseek 真的进了提示词。"""
    bad = []
    if not r["stalled"]:
        bad.append(f"A: stalled={r['stalled']}（期望 ≥1：全等候选必须记停滞）")
    if r["reseek"] != 1:
        bad.append(f"A: planner.reseek={r['reseek']}（期望 1）")
    if not r["has_reseek_prompt"]:
        bad.append("A: 提示词里没有 reseek 块（`reseek_lines` 没进提问）")
    if not r["has_stall_log"]:
        bad.append("A: 日志里没有`停滞 n/2`行")
    if r["status"] != "stalled":
        bad.append(f"A: status={r['status']}（期望 stalled：停滞判定成立后转向交付）")
    return bad


def _assert_b(r: dict) -> list[str]:
    """`B` 的判据：混合 ⇒ else 支 ⇒ 停滞与 reseek **都不该动**。"""
    bad = []
    if r["stalled"]:
        bad.append(f"B: stalled={r['stalled']}（期望 0：混合不该算停滞）")
    if r["reseek"]:
        bad.append(f"B: planner.reseek={r['reseek']}（期望 0）")
    if r["has_reseek_prompt"]:
        bad.append("B: 提示词里出现了 reseek 块（混合情形不该有）")
    if not r["has_allblock_log"]:
        bad.append("B: 日志里没有`全部候选被拦`（说明没走 else 支）")
    if r["status"] != "cascade":
        bad.append(f"B: status={r['status']}（期望 cascade：混合情形由熔断兜底）")
    return bad


def measure() -> dict:
    a = run_arm(ARM_A)
    b = run_arm(ARM_B, answer=_B_ANSWERS)
    return {"A": a, "B": b}


def _fmt(r: dict) -> str:
    return (f"status={r['status']:16s} iters={r['iterations']} "
            f"stalled={r['stalled']} repeats={r['repeats_skipped']} "
            f"reseek={r['reseek']} plan调用={r['plan_prompts']} "
            f"提示词含reseek={r['has_reseek_prompt']} "
            f"日志含停滞={r['has_stall_log']} 日志含全拦={r['has_allblock_log']}")


# ---------------------------------------------------------------------------
# 日志口径：真实回合里这两种组合各出现几次（只读，零模型）
# ---------------------------------------------------------------------------
#: ⚠️ 用 `search` 而不是 `^…`：真实日志的每一行都带 `HH:MM:SS ` 前缀。
#:    第一版用了 `^\s*\[` ⇒ 一步都没认出来，而"0 步"看起来像"没有重复"
#:    —— 分母归零时**所有比例都是 0**，那是最容易被当成好消息的坏消息。
_STEP = re.compile(r"\[(\d+)\] 通道=")
_CANDS = re.compile(r"候选=(\d+)")


def parse_episodes(text: str) -> dict:
    """把一份 run 日志切成「回合 → 步骤 → 候选结局」。

    只认**写死在日志里的字**（`mw/core.py` 的 6/8 缩进是格式的一部分）：
      · `[N] 通道=… 候选=K`      一步开始
      · `⏭ 重复动作已跳过`       一个候选是重复
      · `⊘ [`                    一个候选被价值层否决
      · `✋`                      一个候选被守卫拦
      · `↳ <tool> ok=`           一个候选被执行
    挂不上任何一步的行进 `orphan`（**分母诚实**：静默丢掉会让比例变好看）。
    """
    out = {"steps": [], "orphan": [], "stall_logs": 0, "allblock_logs": 0,
           "stall_widths": []}
    cur: dict | None = None
    for ln in text.splitlines():
        m = _STEP.search(ln)
        if m:
            if cur is not None:
                out["steps"].append(cur)
            k = _CANDS.search(ln)
            cur = {"it": int(m.group(1)), "cands": int(k.group(1)) if k else None,
                   "repeat": 0, "veto": 0, "guard": 0, "executed": 0,
                   "nobudget": 0, "terminal": ""}
            continue
        if _ANCHOR_STALL in ln:
            out["stall_logs"] += 1
            #: 停滞那一轮的**候选宽度**。这个数回答"全等是在多宽的时候成立的" ——
            #: 宽度 1 意味着"这一步根本没有第二个候选可换"，而换形态正是要靠它。
            out["stall_widths"].append(cur["cands"] if cur else None)
            if cur is not None:
                cur["terminal"] = "stall"
            continue
        if _ANCHOR_ALLBLOCK in ln:
            out["allblock_logs"] += 1
            if cur is not None:
                cur["terminal"] = "allblock"
            continue
        if "预算耗尽" in ln or "触发收尾余量" in ln:
            if cur is not None and not cur["terminal"]:
                cur["terminal"] = ("budget" if "预算耗尽" in ln else "reserve")
            continue
        if cur is None:
            if any(k in ln for k in ("⏭ 重复动作已跳过", "⊘ [", "✋", "↳ ")):
                out["orphan"].append(ln.strip()[:80])
            continue
        if "⏭ 重复动作已跳过" in ln:
            cur["repeat"] += 1
        elif "⊘ [" in ln:
            cur["veto"] += 1
        elif "✋" in ln:
            cur["guard"] += 1
        elif "↳ " in ln:
            cur["executed"] += 1
        elif "额度 <" in ln or "都因额度不足未执行" in ln:
            cur["nobudget"] += 1
    if cur is not None:
        out["steps"].append(cur)
    return out


def _classify(steps: list[dict]) -> dict:
    """把步骤分成「全等」「混合」「无重复」三档。⛔ 三档互斥且穷尽。"""
    n = {"all_repeat": 0, "mixed": 0, "no_repeat": 0, "unknown_width": 0}
    for s in steps:
        if s["repeat"] == 0:
            n["no_repeat"] += 1
        elif s["cands"] is None:
            n["unknown_width"] += 1
        elif s["repeat"] == s["cands"]:
            n["all_repeat"] += 1
        else:
            n["mixed"] += 1
    return n


def measure_logs(paths: list[Path]) -> int:
    print(f"{'文件':46s} {'步':>4s} {'重复':>4s} {'全等':>4s} {'混合':>4s} "
          f"{'停滞行':>6s} {'全拦行':>6s} {'孤儿':>4s}")
    tot = {"steps": 0, "repeat": 0, "all_repeat": 0, "mixed": 0,
           "stall": 0, "allblock": 0, "orphan": 0, "files": 0}
    widths: dict = {}
    for p in paths:
        text = p.read_text(encoding="utf-8", errors="replace")
        if "▶ 任务开始" not in text:
            continue                      # 不是"跑任务"的日志，不进分母
        ep = parse_episodes(text)
        k = _classify(ep["steps"])
        rep = sum(s["repeat"] for s in ep["steps"])
        print(f"{str(p)[-46:]:46s} {len(ep['steps']):4d} {rep:4d} "
              f"{k['all_repeat']:4d} {k['mixed']:4d} {ep['stall_logs']:6d} "
              f"{ep['allblock_logs']:6d} {len(ep['orphan']):4d}")
        for w in ep["stall_widths"]:
            widths[w] = widths.get(w, 0) + 1
        tot["files"] += 1
        tot["steps"] += len(ep["steps"])
        tot["repeat"] += rep
        tot["all_repeat"] += k["all_repeat"]
        tot["mixed"] += k["mixed"]
        tot["stall"] += ep["stall_logs"]
        tot["allblock"] += ep["allblock_logs"]
        tot["orphan"] += len(ep["orphan"])
        if k["unknown_width"]:
            print(f"    ⚠️ 宽度未知 {k['unknown_width']} 步（不计入两档，留在分母外）")
    print("-" * 100)
    print(f"合计 {tot['files']} 份日志 / {tot['steps']} 步 / 重复候选 {tot['repeat']} 次 "
          f"⇒ 全等 {tot['all_repeat']} 步、混合 {tot['mixed']} 步；"
          f"停滞行 {tot['stall']}、全拦行 {tot['allblock']}、孤儿 {tot['orphan']}")
    print(f"停滞那一轮的候选宽度分布：{dict(sorted(widths.items(), key=lambda kv: -kv[1]))}")
    return 0 if tot["orphan"] == 0 else 1


# ---------------------------------------------------------------------------
# 反证
# ---------------------------------------------------------------------------
def self_check() -> int:
    fails: list[str] = []
    ok: list[str] = []

    def one(name: str, bad: list[str], expect_red: bool) -> None:
        red = bool(bad)
        if red == expect_red:
            ok.append(f"{name}: {'红（如预期）' if red else '绿（如预期）'}"
                      + (f" — {bad[0]}" if bad else ""))
        else:
            fails.append(f"{name}: 期望{'红' if expect_red else '绿'}，实际"
                         f"{'红' if red else '绿'} — {bad or '无差异'}")

    # P1 / P2：两向
    base = measure()
    one("P1 A_allrepeat 判据", _assert_a(base["A"]), False)
    one("P2 B_mixed 判据", _assert_b(base["B"]), False)

    # P3：把 B 的空答案改成非空的 ⇒ 那一步会执行 finish ⇒ B 判据必须红
    arm_b_filled = [_plan(_recall("python 截断")),
                    _plan(_recall("python 截断", "再找一次"),
                          {"intent": "交付", "tool": "finish",
                           "args": {"answer": "```python\nprint(2)\n```"},
                           "rationale": "已经够了"}),
                    _plan(_note())]
    r3 = run_arm(arm_b_filled)
    one("P3 B 的空答案换成非空（混合消失）", _assert_b(r3), True)

    # P4：让"全等"**永远不可能**发生 —— 每一轮都带一个**新的** note 候选
    #     ⇒ 该步必然被执行 ⇒ `repeats` 恒小于 `len(cands)` ⇒ A 判据必须红。
    #     ⚠️ 第一版写的是"第二轮多一个 note"，那只是把停滞**推迟一步**：
    #        note 执行完，下一份剧本又全是重复 ⇒ 停滞照样发生。
    #        反证设计错在"以为改一处就会变"，实测是"路径换了个顺序"。
    #     ⚠️ 还必须同时把**候选预算**抬上去：默认宽 3 是**累计**的，
    #        第 3 步 `cand_left` 就塌到 1 ⇒ 混合被截成单候选 ⇒ 全等**又**成立。
    #        这个塌缩本身是一条读数（见 RESULTS.md §3），但在反证里它会掩盖变量。
    arm_a_never = [_plan(_recall("python 截断")),
                   (lambda i: _plan(_HINT, _note(f"第 {i} 轮结论"))),
                   (lambda i: _plan(_HINT, _note(f"第 {i} 轮结论")))]
    r4 = run_arm(arm_a_never, cand_budget=99)
    one("P4 每轮都有新动作（全等永不可能）", _assert_a(r4), True)

    # P5：max_stall=1 ⇒ 第一次停滞就到阈值 ⇒ 转向交付，不进 reseek ⇒ A 判据必须红
    r5 = run_arm(ARM_A, max_stall=1)
    one("P5 max_stall=1（阈值一到就收尾）", _assert_a(r5), True)

    # P6：日志口径的分母诚实 —— 一段没有任何步骤头的日志必须进 orphan 而不是消失
    fake = "\n".join(["      ⏭ 重复动作已跳过（不产生新信息）: finish"])
    ep = parse_episodes(fake)
    one("P6 无步骤头的重复行进 orphan（不静默丢）",
        [] if ep["orphan"] and not ep["steps"] else ["孤儿行被丢掉了"], False)

    # P7：三档分类互斥且穷尽 —— 手造三种步，各归各档
    s3 = [{"repeat": 1, "cands": 1}, {"repeat": 1, "cands": 2},
          {"repeat": 0, "cands": 2}]
    k = _classify(s3)  # type: ignore[arg-type]
    one("P7 全等/混合/无重复 各一档",
        [] if (k["all_repeat"], k["mixed"], k["no_repeat"]) == (1, 1, 1)
        else [f"分类错：{k}"], False)

    print("\n=== 两臂读数 ===")
    print("  A_allrepeat:", _fmt(base["A"]))
    print("  B_mixed    :", _fmt(base["B"]))
    print("\n=== 反证 ===")
    for line in ok:
        print("  ✅", line)
    for line in fails:
        print("  ❌", line)
    print(f"\n{'全部通过' if not fails else '有反证未过'}：{len(ok)} 过 / {len(fails)} 红")
    return 0 if not fails else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="停滞/换形态这条出口的入口条件")
    ap.add_argument("--self-check", action="store_true", help="P1~P7 反证")
    ap.add_argument("--logs", nargs="*", default=None, help="真实 run 日志路径")
    args = ap.parse_args()
    if args.self_check:
        return self_check()
    if args.logs:
        return measure_logs([Path(p) for p in args.logs])
    r = measure()
    bad = _assert_a(r["A"]) + _assert_b(r["B"])
    print("  A_allrepeat:", _fmt(r["A"]))
    print("  B_mixed    :", _fmt(r["B"]))
    for b in bad:
        print("  ❌", b)
    print("全部通过" if not bad else "有判据未过")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
