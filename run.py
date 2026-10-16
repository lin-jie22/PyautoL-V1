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


def main() -> int:
    p = argparse.ArgumentParser(description="V1 —— 一个可测量的 agent 内核")
    p.add_argument("--goal", default="", help="任务目标")
    p.add_argument("--selftest", action="store_true", help="离线端到端自检")
    p.add_argument("--repeat", type=int, default=0, help="测量模式：重复次数（≥2）")
    p.add_argument("--sweep", default="", help="预算档扫描，如 1,2,4")
    p.add_argument("--budget", type=float, default=1.0, help="预算倍数")
    p.add_argument("--offline", action="store_true", help="强制离线（用确定性桩）")
    p.add_argument("--allow-shell", action="store_true", help="开启 shell 工具（谨慎）")
    args = p.parse_args()

    if args.repeat:
        if not args.goal:
            args.goal = DemoLLM.GOAL
        return cmd_measure(args)
    if args.selftest or not args.goal:
        return cmd_selftest(args)
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
