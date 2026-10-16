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
import json
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mw import config as cfg                      # noqa: E402
from mw.core import Core                          # noqa: E402
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
            return json.dumps({"expected": "会返回与截断相关的历史结论条目",
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


def _build(goal: str, args, *, allow_shell: bool = False) -> Core:
    s = cfg.settings()
    if args.offline or not s.llm.usable():
        from mw.guard import Guard
        from mw.llm import StubLLM
        core = Core(s, llm=StubLLM())
        core.guard = Guard(allow_shell=allow_shell, workspace=s.paths.workspace,
                           max_consecutive_failures=s.limits.max_consecutive_failures)
    else:
        core = Core(s)
    return core


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

    root = Path(__file__).resolve().parent
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
    elif use_llm:
        label = f"真模型 {s.llm.model}"
        if args.fresh:
            def solver(t):                                         # type: ignore[misc]
                return _build("", args, allow_shell=args.allow_shell).run(
                    t.prompt, budget_factor=args.budget).answer
            label += "（每题新建，记忆不跨题）"
        else:
            _core = _build("", args, allow_shell=args.allow_shell)
            solver = lambda t: _core.run(t.prompt, budget_factor=args.budget).answer  # noqa: E731
            label += "（复用同一内核，记忆跨题累积）"
    else:
        solver = lambda t: t.oracle                                # noqa: E731
        label = "oracle（上界参照）"
        print()
        print("  ℹ️ 未配置真模型（V1_LLM_API_KEY）⇒ 本轮跑的是**参照点**，不是模型成绩。")

    print()
    print(f"--- 第 2 步  跑任务集  solver={label}  预算档={tag}  repeat={repeat_n} ---")
    m, rep = run_suite(solver, scorer, suite, repeat_n=repeat_n,
                       budget_tag=tag, candidate=label)
    print(f"  {m.as_line()}")
    print(f"  {rep.summary_line()}")
    if not rep.trustworthy():
        print("  ⚠️ 本轮存在早退（没量到的条目）⇒ 这个读数**不得**用于判定。")
        for e in rep.errors[:5]:
            print(f"     · {e}")

    # ── 第 3 步：口径闸 ------------------------------------------------
    caliber = Caliber(scorer_sha=scorer.sha(), suite_sha=suite.sha(),
                      repeat=repeat_n, budget_tag=tag, fresh=bool(args.fresh))
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
    p.add_argument("--scorer", default="", help="外部打分器可执行文件（stdin/stdout 走 JSON）")
    p.add_argument("--solver", default="auto",
                   choices=["auto", "llm", "oracle", "partial", "null"],
                   help="谁来答题：auto=有真模型则用真模型，否则用 oracle 参照")
    p.add_argument("--baseline", default="", help="基线文件（默认 $V1_BENCH_DIR/bench_baseline.json）")
    p.add_argument("--save-baseline", action="store_true", help="把本次读数写入基线（过口径闸）")
    p.add_argument("--compare", action="store_true", help="与基线比较并给出判定")
    p.add_argument("--allow-supersede", action="store_true",
                   help="口径变更时允许替换基线（旧值会另存 superseded）")
    p.add_argument("--fresh", action="store_true",
                   help="每题新建内核（记忆不跨题）；同时进入口径签名")
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
