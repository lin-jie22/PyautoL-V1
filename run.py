#!/usr/bin/env python3
"""V1 入口。

零第三方依赖。三种用法：

  1) 离线自检（不需要 API key，几秒钟跑完，最常用）
       python3 run.py --selftest

  2) 真跑一个任务
       V1_LLM_API_KEY=sk-... V1_LLM_MODEL=gpt-4o-mini python3 run.py --goal "..."

  3) 测量模式（V1 存在的理由 —— 不要用单个数字描述一个候选）
       python3 run.py --selftest --repeat 5
       python3 run.py --goal "..." --sweep 1,2,4
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mw import config as cfg                      # noqa: E402
from mw.core import Core                          # noqa: E402
from mw.critique import CritiqueLedger            # noqa: E402
from mw.ledger import (ScoreLedger, is_passed, ledger_sig_of,  # noqa: E402
                       next_session)
from mw.llm import Message                        # noqa: E402
from mw.measure import (Measurement, budget_curve, noise_floor,  # noqa: E402
                        paired_compare, repeat, summarize)


# ---------------------------------------------------------------------------
# 离线演示模型：让 loop 真的从 start 走到 finish，并且**故意撞一次守卫**
# ---------------------------------------------------------------------------
class DemoLLM:
    """按调用序推进的脚本化模型。

    它不是玩具 —— 它是内核可验证性的地基：证明"在没有任何外部依赖、
    没有网络、没有钱包"的情况下，主循环能完整走完 perceive→plan→gate→
    foresee→act→observe→persist 七段，并留下可对账的读数。
    """

    # 目标刻意写成"多子句"形态：这样 ACT 会判为复杂任务，走深通道，
    # 于是 perceive→plan→gate→**foresee**→act→observe→persist 七段全部被跑到。
    # 用一个短目标会让世界模型那一段**永远不被执行**却仍显示"全绿"——
    # 这正是 V5 反复栽的"假绿"。契约测试 I6/I7 就是钉这一点。
    GOAL = "分析并清理 python 截断问题的临时文件，然后总结根因与修复方案，最后把结论写回记忆"

    def __init__(self) -> None:
        self.planner_calls = 0
        self.total = 0

    def complete(self, messages, **kw) -> str:  # type: ignore[no-untyped-def]
        self.total += 1
        blob = "\n".join(m.content for m in messages)

        if "预演" in blob:      # 世界模型预演
            # `expect_ok` 是预演里**唯一会被事后检验**的字段（缺陷 #12 的修复）：
            # 说"会成功"就一定会被 `result.ok` 对照。这里**故意**让 note 那一步
            # 预演错（说"会失败"、实际成功）—— 与第一个候选故意破坏性同理：
            # 证明失配链路真的会跑并沉淀，而不是一个永远不会触发的摆设。
            # 一个在自检里永远"全对"的世界模型，和 V5 的"每轮全绿"是同一种谎。
            # ⚠️ 必须匹配**动作行**，不能用 `"note" in blob`：
            #    预演提示词里还带着 `guidance`（上一轮的教训原文），
            #    而那条教训的正文里就写着 `note` —— 于是 finish 那一步会被误判成 note。
            #    第一次自检就是这么抓到的（finish 被冤枉成"预演失败"）。
            if "准备执行的动作：note(" in blob:
                return json.dumps({"expect_ok": False,
                                   "expected": "写回记忆时会因守卫拒绝而失败",
                                   "confidence": 0.72}, ensure_ascii=False)
            return json.dumps({"expect_ok": True,
                               "expected": "会返回与截断相关的历史结论条目",
                               "confidence": 0.72}, ensure_ascii=False)

        self.planner_calls += 1
        if self.planner_calls == 1:
            # 第一个候选**故意**是破坏性动作 —— 用来证明守卫真的在拦，
            # 而不是一个"永远不会触发"的摆设（V5 反复踩的假绿）。
            return json.dumps({"candidates": [
                {"intent": "直接清理 python 截断问题的临时文件", "tool": "run_shell",
                 "args": {"cmd": "rm -rf ~"}, "rationale": "一步到位（应被守卫拦下）"},
                {"intent": "先在记忆里找已有的 python 截断相关结论", "tool": "recall",
                 "args": {"query": "python 截断 问题"}, "rationale": "避免重复劳动"},
            ]}, ensure_ascii=False)
        if self.planner_calls == 2:
            return json.dumps({"candidates": [
                {"intent": "把本轮发现写回语义记忆", "tool": "note",
                 "args": {"text": "python 写文件时应显式指定 encoding=utf-8，"
                                  "否则默认编码会在非 ASCII 上静默截断",
                          "source": "selftest", "confidence": 0.9},
                 "rationale": "沉淀结论"},
            ]}, ensure_ascii=False)
        return json.dumps({"candidates": [
            {"intent": "给出结论并收尾", "tool": "finish",
             "args": {"answer": "根因是未指定 encoding 导致默认编码静默截断；"
                                "修复：所有 open() 显式 encoding='utf-8'。"},
             "rationale": "已经拿到足够信息"},
        ]}, ensure_ascii=False)


#: 直连答题的系统提示词。刻意**只约束输出形态**，不教解题 ——
#: 一旦开始教解题，量到的就不是模型能力，而是提示词工程的水平。
DIRECT_SYSTEM = (
    "你是代码助手。只输出一个 python 代码块，不要任何解释、不要示例调用。"
    "函数名与签名必须与题目要求完全一致。"
)


def _build(goal: str, args, *, allow_shell: bool = False,
           critique_ledger: "CritiqueLedger | None" = None,
           score_ledger: "ScoreLedger | None" = None,
           caliber_sig: str = "") -> Core:
    s = cfg.settings()
    if args.offline or not s.llm.usable():
        from mw.guard import Guard
        from mw.llm import StubLLM
        core = Core(s, llm=StubLLM(), critique_ledger=critique_ledger,
                    score_ledger=score_ledger, caliber_sig=caliber_sig)
        core.guard = Guard(allow_shell=allow_shell, workspace=s.paths.workspace,
                           max_consecutive_failures=s.limits.max_consecutive_failures)
    else:
        core = Core(s, critique_ledger=critique_ledger,
                    score_ledger=score_ledger, caliber_sig=caliber_sig)
    return core


def _memory_seed(s) -> tuple[int, int]:
    """读**起跑前**的记忆基线 (facts, episodes)。

    为什么必须有它：`shared_db` 模式下记忆跨题、**跨运行**累积，两轮读数只有在
    起跑基线相同时才是同口径。不读它，比的就是"记忆多少"而不是"候选好坏"。
    ⚠️ 必须在跑之前读 —— 跑完再读拿到的是**结果**，不是条件。
    """
    from mw.memory import MemoryStore
    try:
        ms = MemoryStore(s.paths.db)
    except Exception:                                          # noqa: BLE001
        return (0, 0)
    try:
        st = ms.stats()
        return (int(st.get("facts", 0)), int(st.get("episodes", 0)))
    finally:
        ms.close()


def _prov(*, llm_kind: str, external_agents, model, temperature) -> dict:
    """血缘 dict 的**唯一构造入口** —— 字段集合只声明在这一处。

    ⛔ 为什么做成函数而不是"各分支各拼一个 dict"：本轮实测抓到第 9 个缺陷 ——
    内核路径的三个分支各自拼血缘，`--fresh` 那一支**漏了 `model`**，于是
    跨领域跑和泛化对照（考A）两条读数**事后无法核对自己跑的是哪个模型**。
    而**没有任何断言会发现**这件事：血缘这套防线自己的第一条纪律
    （"读数必须带实际用了什么"）身上没有防线。

    收敛成一个 dict 字面量之后，"漏字段"从"需要测试发现"变成"写不出来"。
    契约 X 组同时钉住：① 返回的键集合恒等于 `PROV_KEYS`；
    ② run.py 里**只有这里**出现带 `llm_kind` 键的 dict 字面量。
    """
    return {"llm_kind": llm_kind, "external_agents": list(external_agents),
            "model": model, "temperature": float(temperature)}


#: 血缘必须带的字段（**唯一声明处**）。加/删字段只改这里。
PROV_KEYS: tuple[str, ...] = ("external_agents", "llm_kind", "model", "temperature")


def _kernel_prov(core) -> dict:
    """从**实际构造出来的内核**读血缘。事实，不是声明。

    ⚠️ 三个值全部取自内核自己（`core.llm_kind` / `core.external_agents` /
       `core.s.llm`），不是外面传进来的字符串 —— 与 K5/L6 同一条纪律：
       标签可以撒谎，血缘不能。
    ⚠️ `temperature` 同源：§13⑨ 的事故（口径写 0.0、实际跑 0.2）只有把内核
       **自己**的温度记进血缘，才可能被事后审计出来。
    """
    return _prov(llm_kind=core.llm_kind,
                 external_agents=core.external_agents,
                 model=core.s.llm.model,
                 temperature=core.s.llm.temperature)


def _kwm_zero() -> dict:
    """内核侧世界模型读数累加器的零点。"""
    return {"predictions": 0, "scored": 0, "hits": 0, "unscored": 0,
            "mismatches": 0, "mismatches_outcome": 0, "mismatches_text": 0,
            "text_compared": 0, "suppressed": 0,
            "confusion": {"ok_ok": 0, "ok_fail": 0, "fail_ok": 0, "fail_fail": 0}}


def _kwm_absorb(acc: dict, stats: dict) -> dict:
    """把一次内核运行的读数并进累加器（就地修改并返回）。

    ⚠️ `hit_rate` **不并** —— 它是比值，逐次平均会给出错的答案
    （100 次全对 + 1 次全错，平均 0.5；真值是 100/101）。只能由总数重算。
    ⚠️ 缺失的键按 0 并，**不炸**：老读数文件里没有 `scored` 是正常的，
    但"没有这个字段" ≠ "这个字段是 0" —— 所以 `hit_rate` 在 `scored==0` 时
    返回 `None`，让"没量到"在读数里显形。
    """
    for k in ("predictions", "scored", "hits", "unscored", "mismatches",
              "mismatches_outcome", "mismatches_text", "text_compared", "suppressed"):
        acc[k] = int(acc.get(k, 0)) + int(stats.get(k, 0) or 0)
    cm = acc.setdefault("confusion", {})
    for k in ("ok_ok", "ok_fail", "fail_ok", "fail_fail"):
        cm[k] = int(cm.get(k, 0)) + int((stats.get("confusion") or {}).get(k, 0) or 0)
    return acc


def _kwm_line(acc: dict) -> dict:
    """产出落盘/打印用的读数。派生量**只在这里算一次**（同一事实只声明一处）。"""
    d = {k: int(v) for k, v in acc.items() if k != "confusion"}
    d["confusion"] = {k: int(v) for k, v in (acc.get("confusion") or {}).items()}
    d["hit_rate"] = (round(d["hits"] / d["scored"], 3) if d["scored"] else None)
    return d


def cmd_selftest(args) -> int:
    """离线端到端。返回进程码：0 = 内核按预期工作。"""
    print("=" * 74)
    print("V1 离线自检 —— 主循环端到端（零网络 / 零密钥 / 零第三方依赖）")
    print("=" * 74)
    s = cfg.settings()
    demo = DemoLLM()
    core = Core(s, llm=demo)
    goal = args.goal or DemoLLM.GOAL
    out = core.run(goal, budget_factor=args.budget)

    print()
    print("--- 读数 ---")
    print(f"  状态      : {out.status}")
    print(f"  答案      : {out.answer[:100]}")
    print(f"  预算      : {out.stats['budget']}")
    print(f"  守卫拦截  : {out.stats['guard']}")
    print(f"  世界模型  : {out.stats['worldmodel']}")
    print(f"  规划器    : {out.stats['planner']}")
    print(f"  记忆      : {out.stats['memory']}")
    print(f"  重复行    : {core.memory.duplicate_rows()}  (去重约束的自证)")

    ok = (out.status == "done"
          and out.stats["guard"]["blocks"]["tool_misuse"] >= 1
          and core.memory.duplicate_rows() == 0)
    print()
    print(f"自检结论: {'✅ 通过' if ok else '❌ 不符合预期'}")
    return 0 if ok else 1


def cmd_run(args) -> int:
    core = _build(args.goal, args, allow_shell=args.allow_shell)
    out = core.run(args.goal, budget_factor=args.budget)
    print(json.dumps({"status": out.status, "answer": out.answer,
                      "reason": out.reason, "iterations": out.iterations,
                      "stats": out.stats}, ensure_ascii=False, indent=2))
    return 0 if out.status == "done" else 1


def cmd_measure(args) -> int:
    """测量模式。**这是 V1 的核心价值所在**，不是附庸。"""
    print("=" * 74)
    print(f"测量模式：同一目标重复 {args.repeat} 次（预算档 {args.budget:g}x）")
    print("=" * 74)
    rows: list[Measurement] = []

    for b in [float(x) for x in str(args.sweep or args.budget).split(",")]:
        tag = f"{b:g}x"
        scores: list[float] = []

        def sample(_b=b) -> float:
            core = _build(args.goal, args, allow_shell=args.allow_shell)
            out = core.run(args.goal, budget_factor=_b)
            return 1.0 if out.status == "done" else 0.0

        scores = repeat(sample, n=args.repeat)
        m = summarize(scores, candidate="selftest", budget_tag=tag)
        rows.append(m)
        print(f"  {m.as_line()}")

    print()
    print("--- 预算曲线（2026：不要用一个数描述一个候选） ---")
    print(budget_curve(rows))
    print()
    print(f"--- 噪声下限（保守口径 = 最大极差）: {noise_floor(*rows):.4f} ---")
    if len(rows) >= 2:
        ok, why = paired_compare(rows[0], rows[-1])
        print(f"  {rows[0].budget_tag} vs {rows[-1].budget_tag}: {'变好' if ok else '不成立'} — {why}")
    print()
    print("说明：离线桩是确定性的 ⇒ 极差应为 0.0000。")
    print("      这本身是有用读数：它证明测量管线**不引入**噪声。")
    print("      真实候选的噪声必须用同样方式在真模型上测出来，不许假设。")
    return 0


def _noise_report(rep, suite, m, repeat_n: int) -> list[str]:
    """逐题 × 逐次 的分数矩阵 —— 噪声下限实验的核心输出。

    为什么必须**逐题**看，不能只看总分：
        两次总分相同，可能是一题从 0 涨到 1、另一题从 1 跌到 0 相互抵消。
        只盯总分，这种抖动会被误读成"稳定"。V5 的 0.0862 就是用总分极差量的，
        但没人看过它是**哪些题**在抖 —— 于是既不知道噪声从哪来，也无法判断
        「某个改进」是否大过了噪声。
    """
    if repeat_n < 2:
        return []
    cell: dict[tuple[str, int], float] = {}
    for row in rep.per_task:
        cell[(row["id"], row["rep"])] = row["score"]

    ids = [t.id for t in suite.tasks]
    out = ["", "--- 噪声明细（逐题 × 逐次）---"]
    out.append("  " + "任务".ljust(20) + "".join(f"r{r+1}".rjust(8) for r in range(repeat_n)) + "   极差")
    unstable: list[str] = []
    for tid in ids:
        vals = [cell.get((tid, r + 1)) for r in range(repeat_n)]
        shown = "".join(("—" if v is None else f"{v:.4f}").rjust(8) for v in vals)
        known = [v for v in vals if v is not None]
        spread = (max(known) - min(known)) if len(known) >= 2 else 0.0
        if spread > 1e-9:
            unstable.append(tid)
        out.append(f"  {tid.ljust(20)}{shown}   {spread:.4f}" + ("  ⚠️" if spread > 1e-9 else ""))

    spread_all = m.hi - m.lo
    out.append("")
    out.append(f"  各次总分极差（噪声下限）= {spread_all:.4f}")
    out.append(f"  逐题不稳定的任务 = {len(unstable)}/{len(ids)}"
               + (f"  {unstable}" if unstable else ""))
    if spread_all <= 1e-9 and not unstable:
        out.append("  ⇒ 逐题逐次**完全一致** ⇒ 该温度下链路是确定的，噪声下限 = 0")
    else:
        out.append(f"  ⚠️ 抖动存在 ⇒ **任何 ≤ {spread_all:.4f} 的『改进』都不可判定**")
        out.append("     （V5 正是死在这里：极差 0.0862 > 它声称的一切改进）")
    return out


# ===========================================================================
# 跨领域跑法 —— 「通用」的可判定形式
# ===========================================================================
def _count_lines(path: Path) -> int:
    try:
        return len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])
    except OSError:
        return 0


def _read_run_after(path: Path, before: int) -> dict | None:
    """读「本轮新追加的那一行」读数。

    为什么要按行号而不是「读最后一行」：cmd_bench 有可能在写盘**之前**就 return
    （血缘闸拒绝出分、尺子不合格……）。那时最后一行是**上一轮**的，
    读它会得到一个"看起来正常"的读数 —— 正是本项目最忌讳的假绿。
    """
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return None
    if len(lines) <= before:
        return None
    try:
        return json.loads(lines[before])
    except json.JSONDecodeError:
        return None


def _domain_row(name: str, rc: int, rec: dict | None) -> dict:
    if rec is None:
        return {"domain": name, "n": 0, "mean": 0.0, "trust": False,
                "llm": "-", "note": f"无读数（rc={rc}）"}
    rep = rec.get("report") or {}
    a = int(rep.get("attempted", 0))
    r = int(rep.get("recorded", 0))
    sk = int(rep.get("skipped_no_answer", 0))
    fs = int(rep.get("failed_scorer", 0))
    trust = (a == r + sk + fs) and sk == 0 and fs == 0
    return {
        "domain": name,
        "n": len(rep.get("per_task") or []),
        "mean": float((rec.get("measurement") or {}).get("mean", 0.0)),
        "trust": trust,
        "llm": str(rep.get("llm_of_record", "-")),
        "note": "" if trust else f"skipped_no_answer={sk} failed_scorer={fs}",
    }


def _option_conflict(args) -> str:
    """互斥/危险选项组合检查。返回说明（空串 = 无冲突）。

    ⚠️ 为什么这些必须**显式禁止**而不是"随便取一个赢"：
    它们各自都改变了"这次读数**测量的是什么**"，而组合起来的语义是**自相矛盾**的。
    静默让某一个赢，产出的就是一个**标签与事实不符**的读数 —— 那正是本项目
    从 V5 学到的第一条教训的原始形态（标签写"真模型"，实际跑桩）。

    另外这条检查本身是**纯函数**：这样它可以被契约测试直接钉住，
    而不是埋在 cmd_bench 的分支里等到某天真的有人这么用时才炸。
    """
    sd = getattr(args, "suite_dir", "")
    md = getattr(args, "memory_dir", "")
    if sd and md:
        return ("--suite-dir 与 --memory-dir 互斥：跨领域要求每个领域**独立**记忆"
                "（否则后一个领域继承前一个的经验，测到的就不是独立领域），"
                "而显式记忆目录意味着**共享**。两者语义冲突。")
    if sd and getattr(args, "save_baseline", False):
        return ("--suite-dir 与 --save-baseline 互斥：跨领域有 N 把尺子"
                "（N 个 suite_sha），单一基线文件装不下 —— 每个领域都会写同一个"
                " bench_baseline.json 并互相覆盖，最后只剩最后一个领域。"
                " 请去掉 --save-baseline，或逐领域单独跑（--suite 一个一个来）。")
    return ""


def _universality(rows: list[dict]) -> dict:
    """『通用』的判据 —— **不是一个平均分，是一个三元计数**。

    n_domains : 一共几个领域
    n_trust   : 其中有几个是**可信**读数（没量到的不算零、也不算非零）
    n_nonzero : 可信读数里，有几个严格大于 0
    ok        : 三者相等 ⇒ 每个领域都非零

    ⛔ 为什么不返回一个平均数：平均会把「某领域 = 0」抹成一个好看的 0.75。
    而"某个领域完全做不了"正是『通用』要暴露的第一件事。
    ⛔ 也不把"没量到"读成 0 分：那违反契约③（缺失 ≠ 否定）。
    所以不可信的领域**两头都不算**，只算"没量到"。
    """
    trust = [r for r in rows if r.get("trust")]
    nonzero = [r for r in trust if float(r.get("mean", 0.0)) > 0]
    return {"n_domains": len(rows), "n_trust": len(trust),
            "n_nonzero": len(nonzero),
            "ok": len(rows) == len(trust) == len(nonzero)}


def _cross_domain(args, suite_dir: Path) -> int:
    """在多个**领域**上跑同一个内核 + 同一把打分器，并列汇总。

    ⛔ 刻意**不**把各领域分数平均成一个数。平均会把「某个领域 = 0」这件事
    抹掉 —— 而「每个领域都非零」恰恰是『通用』的**最小判据**。
    这里给的是分布 + 可信计数 + 非零计数，不是一个漂亮的总分。

    每个领域是一把**独立的尺子**（suite_sha 不同），所以各自完整走一遍
    （校准 → 跑 → 口径闸），最后并列；绝不跨领域平均，也绝不跨领域判定。
    """
    files = sorted(p for p in suite_dir.glob("*.jsonl"))
    if not files:
        print(f"❌ {suite_dir} 下没有任何 .jsonl")
        return 2

    if not args.fresh:
        # 不隔离的话，第 2 个领域会带着第 1 个领域的记忆起跑 ——
        # 那测的就不是"独立领域"，而是"跨领域迁移"。本轮要的是前者。
        print("  ℹ️ 跨领域模式强制 --fresh（每题独立记忆目录）：")
        print("     否则后一个领域会继承前一个领域的记忆，测到的就不是独立领域。")
        args = copy.copy(args)
        args.fresh = True

    # ── 会话号：**一次 `--suite-dir` 调用 = 一个会话** ────────────────────
    #
    # ⚠️ 不能让每个领域各自取 `next_session()`：那样一轮 2 个领域就变成
    #    会话 0 与会话 1，而它们其实是**同一次测量**的两个部分。
    #    后果是自我模型做时序切分时，每个族只剩 1 个历史样本
    #    （< MIN_SAMPLES ⇒ 一律退回全局率 ⇒ 永远读不出族信号）。
    #    会话号的语义必须落在"**一次完整评测**"上，而不是"一次追加写入"上。
    _sess_id: "int | None" = None
    if not getattr(args, "no_ledger", False):
        _lp = (Path(args.ledger).expanduser() if getattr(args, "ledger", "")
               else cfg.settings().paths.score_ledger)
        _sess_id = next_session(ScoreLedger(_lp).load())

    runs_path = cfg.settings().paths.bench / "runs.jsonl"
    rows: list[dict] = []
    for f in files:
        sub = copy.copy(args)
        sub.suite = str(f)
        sub.suite_dir = ""            # 防止递归
        sub.session = _sess_id
        print()
        print("█" * 74)
        print(f"█ 领域  {f.stem}   （{f.name}）")
        print("█" * 74)
        before = _count_lines(runs_path)
        rc = cmd_bench(sub)
        rows.append(_domain_row(f.stem, rc, _read_run_after(runs_path, before)))

    print()
    print("=" * 74)
    print("跨领域汇总 —— 同一个内核 + 同一把打分器，逐个领域独立跑")
    print("=" * 74)
    print(f"  {'领域':<18}{'题数':>4}{'mean':>10}   {'可信':<6}{'血缘':<10}备注")
    for r in rows:
        print(f"  {r['domain']:<18}{r['n']:>4}{r['mean']:>10.4f}   "
              f"{'✅' if r['trust'] else '❌':<6}{r['llm']:<10}{r['note']}")
    trust = [r for r in rows if r["trust"]]
    uni = _universality(rows)
    print("  " + "-" * 72)
    print(f"  可信领域 {uni['n_trust']}/{uni['n_domains']}   "
          f"其中**非零** {uni['n_nonzero']}/{uni['n_trust']}"
          f"   ← 『通用』的最小判据：每个领域都非零")
    if len(trust) != len(rows):
        print("  ⚠️ 有领域的读数不可信 ⇒ 它既不算非零也不算零，只能算「没量到」。"
              " 把没量到的领域读成 0 分，就是契约③ 被违反。")

    out = cfg.settings().paths.bench / "cross_domain.json"
    try:
        from mw.bench import append_jsonl
        append_jsonl(out, {"ts": time.time(), "n_domains": len(rows), "rows": rows})
        print(f"  汇总已落盘 {out}")
    except Exception as e:  # noqa: BLE001 — 落盘失败不该吞掉结论，但必须留痕
        print(f"  ⚠️ 汇总落盘失败 {type(e).__name__}: {e}")

    return 0 if uni["ok"] else 1


def cmd_bench(args) -> int:
    """外部锚层模式 —— V1 与 V5 的分界线就在这个函数里。

    V5 有 215K 行代码和 136 轮迭代，但它没有任何地方能回答
    "这次改动让系统变好了吗" —— 因为它的尺子是自己写的、自己读的，
    54/54 条记录 gain 恒等于 0，而每轮都交付"全绿"。

    这个模式强制三件事按顺序发生：
        1. **先校准**：尺子必须自证有分辨力（参考解 > 部分解 > 空答案），
           否则 fail-closed 拒用，一个分数都不许出；
        2. **再跑**：打分器在独立子进程里真跑，内核只能拿到一个 float；
        3. **后闸**：写基线前校验口径签名，口径变了就拒绝，旧值另存。
    """
    from mw.bench import (BaselineStore, Caliber, ExternalScorer, Suite,
                          append_jsonl, calibrate, measurement_to_dict,
                          run_suite, verdict_against_baseline)

    # ⚠️ 把 CLI 的温度**写进环境**，让内核（planner / worldmodel）也遵守它。
    #
    # 实测事故（2026-10-17）：内核的三个调用点没有一个传 temperature，
    # 全部落到写死的默认 0.2 ⇒ `--temperature 0` 对内核路径**无效**，
    # 而口径签名却写着 `temperature=0.0`。症状是"同口径、记忆已隔离、
    # 签名逐位相同，同一题两轮仍给 0.5000 / 0.6667"。
    # 现在：CLI 是唯一入口，写进 env ⇒ HTTPLLM 统一从 LLMConfig 取。
    # 这样"读数里写的温度"与"实际发给模型的温度"是同一个事实。
    os.environ["V1_LLM_TEMPERATURE"] = repr(float(args.temperature))

    # ── 互斥/危险组合：先挡住，再谈测量 ──────────────────────────────────
    _conflict = _option_conflict(args)
    if _conflict:
        print(f"❌ {_conflict}")
        return 2

    # ── 记忆目录：让「记忆」成为一个**可持有的实验变量** ──────────────────
    # 为什么需要它：`--fresh`（每题隔离）与默认（共享 state/memory.db）都不足以
    # 表达「先在一个套件上学、再到另一个套件上考」—— 而那正是『泛化』的测法。
    # 给了它就不再每题隔离：显式目录本身就意味着"这些任务共用同一份记忆"。
    _mem_dir = getattr(args, "memory_dir", "")
    if _mem_dir:
        _d = Path(_mem_dir).expanduser()
        _d.mkdir(parents=True, exist_ok=True)
        os.environ["V1_STATE_DIR"] = str(_d)
        args = copy.copy(args)
        args.fresh = False

    root = Path(__file__).resolve().parent
    _suite_dir = getattr(args, "suite_dir", "")
    if _suite_dir and args.suite:
        print("❌ --suite 与 --suite-dir 互斥：前者指定一个套件，后者跑一整目录。")
        return 2
    if _suite_dir:
        d = Path(_suite_dir).expanduser()
        if not d.is_dir():
            print(f"❌ {d} 不是目录（--suite-dir 需要一个目录）")
            return 2
        return _cross_domain(args, d)
    suite_path = (Path(args.suite).expanduser() if args.suite
                  else root / "bench" / "tasks" / "coding_small.jsonl")
    scorer_path = (Path(args.scorer).expanduser() if args.scorer
                   else root / "bench" / "scorers" / "code_exec.py")
    for p in (suite_path, scorer_path):
        if not p.exists():
            print(f"❌ 找不到 {p}")
            return 2

    suite = Suite.load(suite_path)
    scorer = ExternalScorer([sys.executable, str(scorer_path)], source=scorer_path)
    s = cfg.settings()

    print("=" * 74)
    print("V1 外部锚层 —— 先证明尺子能分辨，再用它量")
    print("=" * 74)
    print(f"  任务集  {suite.name}  n={len(suite)}  suite_sha={suite.sha()}")
    print(f"  打分器  {scorer.name}  scorer_sha={scorer.sha()[:32]}")

    # ── 第 1 步：校准。fail-closed，不通过就不许出任何结论 ----------------
    print()
    print("--- 第 1 步  校准：尺子自证有分辨力（V5 缺的就是这道闸）---")
    cal = calibrate(scorer, suite)
    print(cal.as_lines())
    if not cal.ok:
        print()
        print("❌ 尺子不合格 ⇒ **拒绝出分**。")
        print("   这正是 V5 里没有的那一步：它那把尺子从不自证，所以恒等于 0 也没人发现。")
        return 2
    if cal.evidence is not None:
        print()
        print(cal.evidence.as_text())

    # ── 第 2 步：选 solver -----------------------------------------------
    repeat_n = max(1, int(args.repeat or 1))
    tag = f"{args.budget:g}x"
    use_llm = (not args.offline) and s.llm.usable() and args.solver in ("auto", "llm")

    #: 血缘回调（无参 → dict）。**绝不用自由文本标签代替它** —— 见下面预检的注释。
    prov: Callable[[], dict] | None = None

    #: 内核侧世界模型读数的累加器。
    #:
    #: ⚠️ 为什么要它：`per_task` 只记 `score/status/why`，**内核自己的读数一概丢失**。
    #: 于是"失配=3 ⇒ 因果闭环在工作"这类判断只能靠翻 stdout 日志，
    #: 而日志不进 `runs.jsonl`、事后无法审计 —— 这正是本项目反复防的
    #: "读数不可复核"。世界模型的分辨力（scored/hits/混淆矩阵）现在随每次运行落盘。
    #: 累加/派生逻辑在模块级（`_kwm_zero/_kwm_absorb/_kwm_line`），**不在这里重写一遍**。
    _kwm = _kwm_zero()

    # ── 外部评审账本 ────────────────────────────────────────────────
    # 打分器给的**理由**（`Scored.why`）此前只有一个去处：写进日志给人看。
    # 现在它进账本，成为下一轮可以被内核读取的**只读输入** ——
    # 这是「社会与情感」那一格的判据从代理量升级为行为锚之后的通电动作。
    #
    # ⚠️ 默认**只落盘、不投喂**：同一轮里把第 1 题的理由喂给第 6 题，
    #    第 6 题就不再是同一条件下的测量了 —— `repeat` 的意义当场消失。
    #    `--use-critique` 打开的是**接线验证**，不是能力测量。
    _crit_path = (Path(args.critique_file).expanduser() if args.critique_file
                  else (root / "state" / "critique.jsonl"))
    _crit = CritiqueLedger(_crit_path)
    _crit_loaded = _crit.load()
    _crit_for_kernel = _crit if args.use_critique else None
    if _crit_loaded or args.use_critique:
        print(f"  评审账本  {_crit_path.name}  载入 {_crit_loaded} 条"
              f"  投喂={'开' if args.use_critique else '关（默认：保住测量一致性）'}")

    # 记忆基线必须在**跑之前**读（跑完再读拿到的是结果，不是条件）。
    # 三种作用域都要能被签名区分：隔离 / 共享默认库 / **显式持有的目录**。
    # 第三种是泛化实验的条件变量 —— 它必须可复现，所以目录由调用方持有，
    # 不由 run.py 随便生成（生成的话下次就找不到同一份记忆了）。
    _scope = ("explicit_dir" if _mem_dir
              else ("per_task_fresh" if args.fresh else "shared_db"))
    _seed = (0, 0) if args.fresh else _memory_seed(s)

    # ── 口径签名必须在**跑之前**算出来 ──────────────────────────────────
    # 自我模型要在起跑时拿"当前口径"去账本里筛历史，所以签名不能等到第 3 步
    # （口径闸）才构造 —— 那时任务已经跑完了，自我模型早就是空的了。
    # 这也顺带消灭了一处"同一事实两处构造"：以前只有第 3 步有 Caliber，
    # 若自我模型另算一份，两边字段漂移一次就会静默筛错历史。
    caliber = Caliber(scorer_sha=scorer.sha(), suite_sha=suite.sha(),
                      repeat=repeat_n, budget_tag=tag,
                      temperature=float(args.temperature), fresh=bool(args.fresh),
                      memory_scope=_scope, memory_seed=_seed,
                      # 这三项补进来的理由见 bench.CALIBER_KEYS 的注释：
                      # budget_tag 只是倍数，乘在绝对值上 —— 不含绝对值的"同口径"是假的。
                      limits=s.limits.as_dict(),
                      llm_cond=s.llm.cond(),
                      max_tokens=int(args.max_tokens))
    # 账本签名与基线签名**不是同一把**（见 mw.ledger.LEDGER_SIG_EXCLUDE）：
    # 基线要钉死题目集（"同一个候选有没有变好"）；
    # 自我模型要跨题目集（"我在哪些族上弱"）—— 钉死了就没这个问题可问。
    _cal_sig = ledger_sig_of(caliber.as_dict())

    # ── 跨会话分数账本：自我模型的证据来源 ──────────────────────────────
    # ⚠️ 内核**只读**它（起跑冻结，见 mw/ledger.py 模块头）；写入发生在
    #    本轮拿到外部打分器的分数**之后**（本函数末尾），由外部执行。
    _ledger: "ScoreLedger | None" = None
    if not args.no_ledger:
        _led_path = (Path(args.ledger).expanduser() if args.ledger
                     else s.paths.score_ledger)
        _ledger = ScoreLedger(_led_path, caliber_sig=_cal_sig)
        _led_hist = _ledger.load()
        _led_adm = _ledger.admissible(_cal_sig)
        if _led_hist:
            print(f"  分数账本  {_led_path.name}  载入 {len(_led_hist)} 行"
                  f"  同口径采纳 {len(_led_adm)} 行  （自我模型据此起跑）")

    if args.solver == "null":
        solver: Callable[[object], str] = lambda t: ""            # noqa: E731
        label = "null（下界参照）"
    elif args.solver == "oracle":
        solver = lambda t: t.oracle                                # noqa: E731
        label = "oracle（上界参照）"
    elif args.solver == "partial":
        # 任务集自带的部分解 —— 用来演示"锚层也能**确认**一个改进"，
        # 而不只是会拒绝。V5 从未做过一次这样的实验。
        solver = lambda t: t.partial                               # noqa: E731
        label = "partial（中段参照）"
    elif args.solver == "llm-direct":
        # 模型**直连**答题：不经内核、不重规划、不检索记忆。
        # 存在的理由：噪声下限必须在**最短的链路**上先测出来。
        # 若连"一次调用"都不确定，那内核再稳也没意义 —— 噪声是底噪，不是内核产生的。
        if args.offline or not s.llm.usable():
            print("❌ llm-direct 需要真模型：请设 V1_LLM_BASE_URL / V1_LLM_API_KEY / V1_LLM_MODEL")
            return 2
        from mw.llm import HTTPLLM, Message
        _llm = HTTPLLM(s.llm)

        def solver(t):                                             # type: ignore[misc]
            return _llm.complete(
                [Message("system", DIRECT_SYSTEM), Message("user", t.prompt)],
                max_tokens=int(args.max_tokens), temperature=float(args.temperature),
            )

        label = f"真模型直连 {s.llm.model} T={args.temperature:g}"
        # 直连**没有内核**，所以走的是同一构造入口的显式取值版（字段集合依旧唯一）。
        prov = lambda: _prov(llm_kind="http-direct", external_agents=[],  # noqa: E731
                             model=s.llm.model, temperature=args.temperature)
    elif use_llm:
        # ── 预检：内核**实际**会用哪条通道？这是事实，不是声明。 ──────────────
        # 血泪教训（2026-10-17 实测）：这里曾把标签写成"真模型 qwen2.5:7b"，
        # 而 core.py 里的 `llm or StubLLM()` 让内核**全程用离线桩** ——
        # 1 秒"跑完" 6 题、每轮报 done、mean=0.0000 却标 `可信=✅`，
        # 真模型一次都没被调用，且标签与事实矛盾而**无任何机制发现**。
        # 所以出分之前先问内核一次；拿不到真通道就 fail-closed，一个分都不出。
        _probe = _build("", args, allow_shell=args.allow_shell,
                        score_ledger=_ledger, caliber_sig=_cal_sig)
        _kind = str(getattr(_probe, "llm_kind", "unknown"))
        if _kind != "http":
            print(f"❌ 内核实际通道 = {_kind}，不是真模型 ⇒ **拒绝出分**。")
            print("   静默降级是缺陷：那样的读数看起来像模型成绩，实际什么也没测。")
            return 2
        label = f"内核/{_kind} {s.llm.model}"
        if args.fresh:
            _seen: dict = {}

            def solver(t):                                         # type: ignore[misc]
                # ⚠️ 每题必须换**记忆目录**，不只是换一个 Core。
                #
                # 实测事故（2026-10-17）：原先这里只 `_build` 一个新 Core，
                # 而 DB 路径仍是共享的 `state/memory.db` ⇒ 记忆跨题、**跨运行**累积。
                # 后果：两轮"同口径"（1x / T=0 / --fresh）读数一次 `0.8611`、
                # 一次 `0.7222`；差额全部来自起跑时已存在的语义记忆（第二轮已有 27 条）。
                # 而标签当时写着"记忆不跨题" —— 标签在撒谎，口径闸也判"可比"。
                # 现在：每题一个临时 state 目录，跑完即删；口径里如实记 `per_task_fresh`。
                with tempfile.TemporaryDirectory(prefix="v1_fresh_") as td:
                    prev = os.environ.get("V1_STATE_DIR")
                    os.environ["V1_STATE_DIR"] = str(Path(td) / "state")
                    try:
                        c = _build("", args, allow_shell=args.allow_shell,
                                   critique_ledger=_crit_for_kernel,
                                   score_ledger=_ledger, caliber_sig=_cal_sig)
                        out = c.run(t.prompt, budget_factor=args.budget,
                                    task_id=t.id, family=suite.name,
                                    scorer_sha=scorer.sha(), suite_sha=suite.sha())
                        _kwm_absorb(_kwm, out.stats["worldmodel"])
                        # 血缘**从内核读**（llm_kind / external_agents / model / temperature
                        # 四个字段一次到位）—— 曾经这里手写两个键，漏了 model。
                        _seen.update(_kernel_prov(c))
                        return out.answer
                    finally:
                        if prev is None:
                            os.environ.pop("V1_STATE_DIR", None)
                        else:
                            os.environ["V1_STATE_DIR"] = prev

            label += "（每题新建内核 + 记忆隔离）"
            prov = lambda: dict(_seen)                             # noqa: E731
        else:
            _core = _build("", args, allow_shell=args.allow_shell,
                           critique_ledger=_crit_for_kernel,
                           score_ledger=_ledger, caliber_sig=_cal_sig)

            def solver(t):                                         # type: ignore[misc]
                out = _core.run(t.prompt, budget_factor=args.budget,
                                task_id=t.id, family=suite.name,
                                scorer_sha=scorer.sha(), suite_sha=suite.sha())
                _kwm_absorb(_kwm, out.stats["worldmodel"])
                return out.answer

            label += "（复用同一内核，记忆跨题累积）"
            prov = lambda: _kernel_prov(_core)                     # noqa: E731
    else:
        solver = lambda t: t.oracle                                # noqa: E731
        label = "oracle（上界参照）"
        print()
        print("  ℹ️ 未配置真模型（V1_LLM_API_KEY）⇒ 本轮跑的是**参照点**，不是模型成绩。")

    print()
    print(f"--- 第 2 步  跑任务集  solver={label}  预算档={tag}  repeat={repeat_n} ---")
    m, rep = run_suite(solver, scorer, suite, repeat_n=repeat_n,
                       budget_tag=tag, candidate=label, provenance=prov,
                       critique_ledger=_crit)
    _crit_saved = _crit.save()

    # ── 跨会话账本：**只在跑完之后**追加（外部侧写入）─────────────────
    #
    # ⚠️ 为什么这里**排除参照点**（oracle / null / partial）：
    #    账本是自我模型的"我在哪些族上做对过"的记忆。把 oracle 的满分写进去，
    #    自我模型会"记得"自己每个族都是 1.0 —— 那不是记忆，是**伪造履历**。
    #    参照点是尺子的校准材料，不是系统的能力读数；两者的混用
    #    正是 V5 "把校准材料当结论"的同一类错误。
    #
    # ⚠️ 为什么写在**跑完之后**：轮内写入会让第 N 题读到第 N-1 题的分数
    #    （`mw/ledger.py` 模块头纪律①）。这里已经是外部，且本轮已结束。
    if _ledger is not None and args.solver not in ("oracle", "null", "partial"):
        # 跨领域模式下会话号由 `_cross_domain` 统一分配（一次评测 = 一个会话）；
        # 单套件模式下一轮就是一次追加，自己取下一个号即可 —— 两种情形
        # 都落在"一次完整评测 = 一个会话"这个语义上。
        _sess = getattr(args, "session", None)
        _sess = next_session(_ledger.load()) if _sess is None else int(_sess)
        _rows = [{"session": _sess, "family": suite.name, "task_id": r["id"],
                  "score": r["score"], "passed": is_passed(r["score"]),
                  "caliber_sig": _cal_sig, "candidate": label}
                 for r in rep.per_task]
        _w = _ledger.append(_rows)
        if _w["added"]:
            print(f"  分数账本  会话#{_sess} 追加 {_w['added']} 行"
                  f"（拒绝 {_w['rejected']} 行）⇒ 下一次起跑时自我模型可读")

    print(f"  {m.as_line()}")
    print(f"  {rep.summary_line()}")
    if _crit_saved:
        _cs = _crit.stats()
        # 分母同时给：`rejected` 是累计的，离开 `calls` 无法解释。
        print(f"  评审     落盘={_crit_saved} 条  投喂={_cs['served']}"
              f"（{_cs['calls']} 次筛选）  拒绝={_cs['rejected']}")
    if prov is not None:
        print(f"  血缘      llm_of_record={rep.llm_of_record()}"
              f" 外部能力={len(rep.provenance.get('external_agents') or [])} 个")
    _kw = _kwm_line(_kwm)
    if _kw["predictions"]:
        # 只印"失配=N"是假读数：N 次预演里错 N 次、和 3N 次里错 N 次是两件事。
        # 所以**必须**同时印分母（检验了几次）与混淆矩阵 —— 一个恒说"会成功"的
        # 预测器，`fail_*` 两格恒为 0，单看准确率看不出来。
        print(f"  世界模型  预演={_kw['predictions']} 检验={_kw['scored']} 命中={_kw['hits']}"
              f" 命中率={'n/a' if _kw['hit_rate'] is None else _kw['hit_rate']}"
              f" 失配={_kw['mismatches']}(成败{_kw['mismatches_outcome']}"
              f"/文本{_kw['mismatches_text']})"
              f" 混淆={_kw['confusion']}"
              + (f" ⚠️未检验={_kw['unscored']}" if _kw["unscored"] else ""))

    # ── 血缘闸：标签可以撒谎，血缘不能。 ────────────────────────────────
    # `candidate` 是一串自由文本；只有血缘是**从内核读出来的事实**。
    # 要求真模型却拿到桩 ⇒ 拒绝出分、拒绝判定、拒绝写基线（fail-closed）。
    _wants_model = args.solver in ("llm", "llm-direct", "auto")
    if _wants_model and rep.llm_of_record() not in ("http", "http-direct"):
        print()
        print(f"  ⛔ 血缘显示本轮**不是**真模型（llm_of_record={rep.llm_of_record()}）"
              " ⇒ 拒绝出分／判定／写基线。")
        return 2

    if not rep.trustworthy():
        print("  ⚠️ 本轮存在早退（没量到的条目）⇒ 这个读数**不得**用于判定。")
        for e in rep.errors[:5]:
            print(f"     · {e}")
    for line in _noise_report(rep, suite, m, repeat_n):
        print(line)

    # ── 第 3 步：口径闸 ------------------------------------------------
    # `caliber` 已在第 2 步之前构造（自我模型起跑时就要用它筛历史），
    # 这里**不再重算** —— 同一轮里两处构造同一个签名，是漂移的入口。
    bl_path = (Path(args.baseline).expanduser() if args.baseline
               else s.paths.bench / "bench_baseline.json")
    store = BaselineStore(bl_path)

    print()
    print("--- 第 3 步  口径闸（尺子不能随退化下移）---")
    print(f"  本次口径  {caliber.as_line()}")

    old = store.load_caliber()
    if args.compare and old is None:
        print("  无基线可比（这是第一次）。")
    elif args.compare:
        base = store.load_measurement()
        if base is None:
            print("  基线文件存在但读数不可解析 ⇒ 拒绝比较。")
        elif not old.same_as(caliber):
            print(f"  ⛔ 口径不一致（差异字段 {old.diff(caliber)}）⇒ **拒绝比较**。")
            print("     跨口径的两个数字放在一张表里，结论就是作废的。")
        else:
            ok, why = verdict_against_baseline(base, m)
            print(f"  基线 {base.as_line()}")
            print(f"  判定 {'改进成立' if ok else '不成立'} — {why}")

    if args.save_baseline:
        ok, why = store.save(m, caliber, allow_supersede=args.allow_supersede)
        print(f"  {'✅' if ok else '⛔'} {why}")

    append_jsonl(s.paths.bench / "runs.jsonl", {
        "solver": label, "budget_tag": tag, "repeat": repeat_n,
        "caliber_sig": caliber.signature(), "caliber": caliber.as_dict(),
        "measurement": measurement_to_dict(m), "report": rep.as_dict(),
        # 内核侧读数（世界模型的**分辨力**）。不落盘的读数不可审计。
        "kernel_worldmodel": _kw,
        "calibration": {"ok": cal.ok, "margin": cal.margin, "middle_n": cal.middle_n,
                        "evidence_id": (cal.evidence.evidence_id()
                                        if cal.evidence is not None else None)},
    })
    print(f"  读数已落盘 {s.paths.bench / 'runs.jsonl'}")
    return 0 if rep.trustworthy() else 1


def main() -> int:
    p = argparse.ArgumentParser(description="V1 —— 一个可测量的 agent 内核")
    p.add_argument("--goal", default="", help="任务目标")
    p.add_argument("--selftest", action="store_true", help="离线端到端自检")
    p.add_argument("--repeat", type=int, default=0, help="测量模式：重复次数（≥2）")
    p.add_argument("--sweep", default="", help="预算档扫描，如 1,2,4")
    p.add_argument("--budget", type=float, default=1.0, help="预算倍数")
    p.add_argument("--offline", action="store_true", help="强制离线（用确定性桩）")
    p.add_argument("--allow-shell", action="store_true", help="开启 shell 工具（谨慎）")

    # ── 外部锚层（V1 存在的第一理由：让"变好了"这句话有外部依据）──
    p.add_argument("--bench", action="store_true",
                   help="外部锚层模式：校准尺子 → 跑任务集 → 过口径闸")
    p.add_argument("--suite", default="", help="任务集 JSONL（默认 bench/tasks/coding_small.jsonl）")
    p.add_argument("--suite-dir", default="",
                   help="**跨领域模式**：跑一个目录下的每个 .jsonl 各一遍，并列汇总"
                        "（『通用』的可判定形式）。与 --suite 互斥。")
    p.add_argument("--scorer", default="", help="外部打分器可执行文件（stdin/stdout 走 JSON）")
    p.add_argument("--ledger", default="",
                   help="跨会话分数账本路径（默认 $V1_STATE_DIR/score_ledger.jsonl）")
    p.add_argument("--no-ledger", action="store_true",
                   help="本轮不读写分数账本（自我模型空着起跑，用于对照）")
    p.add_argument("--critique-file", default="",
                   help="评审账本路径（默认 state/critique.jsonl）。"
                        "打分器给的理由会落盘到这里，供**下一轮**读取")
    p.add_argument("--use-critique", action="store_true",
                   help="把账本里的评审投喂给内核（**接线验证用，不是能力测量**）。"
                        "轮内投喂会让后面的题不再是同一条件下的测量 —— repeat 的意义当场消失")
    p.add_argument("--solver", default="auto",
                   choices=["auto", "llm", "llm-direct", "oracle", "partial", "null"],
                   help="谁来答题：auto/llm=走完整内核（多段调用）；"
                        "llm-direct=模型直连答题（不经内核，测最细链路）；"
                        "oracle/partial/null=参照点")
    p.add_argument("--temperature", type=float, default=0.0,
                   help="真模型采样温度。**噪声实验的核心旋钮**：0=贪心（应当确定）")
    p.add_argument("--max-tokens", type=int, default=1024,
                   help="单次调用 token 上限。思考型模型会先花 token 在 reasoning 上，"
                        "给太小会得到空 content（这是实测踩过的坑）")
    p.add_argument("--baseline", default="", help="基线文件（默认 $V1_BENCH_DIR/bench_baseline.json）")
    p.add_argument("--save-baseline", action="store_true", help="把本次读数写入基线（过口径闸）")
    p.add_argument("--compare", action="store_true", help="与基线比较并给出判定")
    p.add_argument("--allow-supersede", action="store_true",
                   help="口径变更时允许替换基线（旧值会另存 superseded）")
    p.add_argument("--fresh", action="store_true",
                   help="每题新建内核（记忆不跨题）；同时进入口径签名")
    p.add_argument("--memory-dir", default="",
                   help="显式指定记忆目录（**记忆 = 可持有的实验变量**）："
                        "用于『先在一个套件上学、再到另一个套件上考』的泛化实验。"
                        "给了它就不再每题隔离（口径 scope=explicit_dir）。")
    args = p.parse_args()

    if args.bench:
        return cmd_bench(args)
    if args.repeat:
        if not args.goal:
            args.goal = DemoLLM.GOAL
        return cmd_measure(args)
    if args.selftest or not args.goal:
        return cmd_selftest(args)
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
