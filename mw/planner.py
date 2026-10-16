"""动态规划 + 重规划。

依据：RedLineSoft 2026 —— "静态线性执行循环（vanilla ReAct）已被动态分层规划取代"。
关键不是"会拆任务"，而是**失败时不崩，而是改写任务图**：
    Execution Monitoring → 分析状态增量 → 重规划（rewrite the task graph on the fly）

V1 的实现刻意轻：不做完整 HTN，不改写整张图。只做两件在实测中真正管用的事：
    1. 每步产出 N 个**候选动作**（这就是 test-time compute 的落点：
       不是一个想法，而是几个想法，然后用 guard + 世界模型筛）。
    2. 失败后带着**错误原文与已试过的动作**重规划，而不是原地重试同一个动作
       —— 这正是 V5 的 hyp_exp "同一假设无限重派" 的反面。

规划器**必须**能降级：LLM 输出不可解析时，回退到确定性单步计划，
而不是抛异常把整轮打断。但降级会被计数（degraded），不许静默。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from .llm import LLM, Message, parse_json


@dataclass
class Step:
    intent: str
    tool: str
    args: dict
    rationale: str = ""

    def as_line(self) -> str:
        return f"{self.tool}({self.args}) — {self.intent}"


@dataclass
class Plan:
    steps: list[Step] = field(default_factory=list)
    degraded: bool = False
    note: str = ""

    def __bool__(self) -> bool:
        return bool(self.steps)


_SYS = (
    "你是一个 agent 的规划器。只输出 JSON，不要解释。\n"
    'JSON 结构：{"candidates": [{"intent": "...", "tool": "工具名", '
    '"args": {...}, "rationale": "..."}]}\n'
    "规则：\n"
    "  · 候选之间必须是**不同**的做法，不是同一动作的措辞变化\n"
    "  · 只能使用给定的工具名，不得发明\n"
    "  · 已经失败过的做法不要重复提出\n"
    "  · 信息足够时就给出 finish 动作"
)


class Planner:
    def __init__(self, llm: LLM, tools: Any) -> None:
        self.llm = llm
        self.tools = tools
        self.calls = 0
        self.degraded = 0

    # ------------------------------------------------------------------
    def propose(self, *, goal: str, state: str, history: Sequence[str],
                n: int = 1, guidance: str = "") -> Plan:
        """产出 n 个候选步骤（各不相同）。n>1 就是 test-time compute 的落点。"""
        self.calls += 1
        avoid = "\n".join(f"  · 已失败/已试过：{h}" for h in list(history)[-6:])
        prompt = (
            f"目标：{goal}\n"
            f"当前状态：\n{state[:1200]}\n"
            f"{('已知易错点：' + guidance) if guidance else ''}\n"
            f"{avoid}\n"
            f"可用工具：\n{self.tools.catalog()}\n\n"
            f"请给出 {max(1, n)} 个**互不相同**的候选项。"
        )
        raw = self.llm.complete(
            [Message("system", _SYS), Message("user", prompt)], max_tokens=700
        )
        data = parse_json(raw, fallback=None)
        steps = self._extract(data)
        if not steps:
            self.degraded += 1
            return Plan(self._fallback(steps_used=len(history)), degraded=True,
                        note="LLM 输出不可解析，已降级为确定性单步计划")
        return Plan(steps)

    def replan(self, *, goal: str, failed: Step, error: str,
               history: Sequence[str], n: int = 1) -> Plan:
        """失败后重规划。**必须带上错误原文**，否则等于原地打转。"""
        augmented = list(history) + [f"{failed.as_line()} ⇒ 失败: {error[:200]}"]
        plan = self.propose(goal=goal, state="(上一步失败，见下)", history=augmented, n=n)
        if plan:
            plan.note = f"因「{failed.tool}」失败而重规划"
        return plan

    # ------------------------------------------------------------------
    def _extract(self, data: Any) -> list[Step]:
        if not isinstance(data, dict):
            return []
        raw = data.get("candidates") or data.get("steps") or []
        if not isinstance(raw, list):
            return []
        known = set(self.tools.names())
        out: list[Step] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool", ""))
            if tool not in known:
                continue                      # 不许发明工具
            args = item.get("args")
            if not isinstance(args, dict):
                args = {}
            intent = str(item.get("intent", ""))[:300]
            sig = f"{tool}|{sorted(args.items())}"
            if sig in seen:                   # 去重：候选必须是不同的做法
                continue
            seen.add(sig)
            out.append(Step(intent, tool, args, str(item.get("rationale", ""))[:200]))
        return out

    def _fallback(self, *, steps_used: int) -> list[Step]:
        """确定性兜底：先尽量从记忆里捞，再直接收尾。

        两条硬约束（第二条是契约测试 G2 抓出来的真实缺陷）：
          ① 降级**仍然产出合法动作** —— 降级不等于瘫痪
          ② 产出的工具名**必须已注册** —— 否则降级路径会造出非法动作，
             后面 dispatch 才失败，等于把缺陷推迟到更难定位的地方
        """
        names = set(self.tools.names())
        if steps_used == 0 and "recall" in names:
            return [Step("先在记忆中查找与目标相关的既有结论", "recall",
                         {"query": "goal"}, "降级兜底：先检索")]
        if "finish" in names:
            return [Step("信息不足，直接给出当前最优答复", "finish",
                         {"answer": "（规划降级：模型输出不可解析，未能形成有效计划）"},
                         "降级兜底：收尾")]
        # 连 finish 都没注册（内核被裁剪过）⇒ 用任意可用工具，仍不产出非法名字
        for n in sorted(names):
            return [Step("降级兜底：调用唯一可用工具", n, {}, "降级兜底")]
        return []

    def stats(self) -> dict:
        return {"calls": self.calls, "degraded": self.degraded,
                "degrade_rate": round(self.degraded / self.calls, 3) if self.calls else 0.0}


def looks_complex(goal: str, prior_failures: int) -> bool:
    """ACT 的复杂度判据 —— 决定走快通道还是深想通道。

    刻意简单可解释：长目标 / 多步动词 / 有失败历史 ⇒ 复杂。
    复杂 ⇒ 走 N 候选 + 走世界模型预演；简单 ⇒ 单候选直通。
    这就是 2026 的 Adaptive Computation Time 在内核里的最小可验证形态
    —— 简单任务不为难自己，难任务才舍得花预算。
    """
    if prior_failures >= 1:
        return True
    if len(goal) > 120:
        return True
    multi = len(re.findall(r"[，,、]|然后|接着|并且|以及|再", goal))
    return multi >= 2
