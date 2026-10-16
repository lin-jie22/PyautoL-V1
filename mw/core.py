"""内核主循环 —— 把前面所有零件接成一个可跑的认知循环。

2026 的 "Cognitive Operating System" 说法是：LLM 不再是自动补全，而是
复杂认知操作系统的 CPU。这个文件就是那块主板，它自己**不做**任何具体能力，
只负责把信号在正确的顺序上流过正确的部件：

    perceive  →  assemble state（目标 + 工作记忆 + 检索到的语义记忆 + 上一步观测）
    think     →  ACT 判复杂度 → 决定 N（快通道 1 个 / 深通道 budget.candidates 个）
    plan      →  planner.propose / replan（失败必带错误原文，不原地重试）
    gate      →  guard 四道检查点（目标劫持 / 工具滥用 / 记忆投毒 / 级联熔断）
    foresee   →  worldmodel 预演 + selective foresight（低置信预测不进决策链）
    act       →  tools.dispatch（永不抛异常）
    observe   →  worldmodel.observe（失配 ⇒ 沉淀 episodic + semantic）
    persist   →  memory（情景记忆必写；语义记忆走去重写入）

七条纪律，每条都对应 V5 一次实测翻车：
  1. 预算是硬约束，花完就停并**如实报告**，不假装完成
  2. 被 guard 拦下的动作必须留正向信号（进 blocks 计数 + 日志）
  3. 不原地重试 —— 失败动作进 history，下一次规划显式避开
  4. 连续失败达阈值即熔断，防级联失败（OWASP Agentic Top-10 之一）
  5. 所有异常在**当前步**被吸收成观测，不许中断整轮
  6. 每步都写日志与情景记忆，读数可回放对账
  7. LLM 调用次数从**唯一**的账本出，不在别处另算
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from . import config as cfg
from .budget import Budget, BudgetExhausted, Ledger
from .guard import Guard
from .llm import LLM, Message, StubLLM, build_llm
from .memory import MemoryStore
from .planner import Planner, Step, looks_complex
from .tools import ToolRegistry, register_core_tools, register_external_agents
from .worldmodel import WorldModel


class _Log:
    """极简日志：同时进文件与内存（供测试断言）。"""
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.lines: list[str] = []

    def __call__(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        self.lines.append(line)
        print(line)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")


class _LedgeredLLM:
    """把 LLM 调用计数收敛到唯一账本。

    不把 ledger 传给 HTTPLLM 是**刻意的**：两处计数 = 两处真相 = 迟早对不上。
    """

    def __init__(self, inner: LLM, ledger: Ledger) -> None:
        self.inner = inner
        self.ledger = ledger

    def complete(self, messages: Sequence[Message], **kw: Any) -> str:
        self.ledger.spend_llm()
        return self.inner.complete(messages, **kw)


@dataclass
class Outcome:
    status: str                      # done | budget_exhausted | cascade | blocked | error
    answer: str = ""
    reason: str = ""
    iterations: int = 0
    stats: dict = field(default_factory=dict)

    def as_line(self) -> str:
        return (f"status={self.status} iter={self.iterations} "
                f"{('answer=' + self.answer[:60]) if self.answer else ('reason=' + self.reason[:80])}")


class Core:
    def __init__(
        self,
        settings: cfg.Settings | None = None,
        *,
        llm: LLM | None = None,
        memory: MemoryStore | None = None,
        tools: ToolRegistry | None = None,
        guard: Guard | None = None,
        log: _Log | None = None,
        wire_external: bool = True,
    ) -> None:
        self.s = settings or cfg.settings()
        self.s.paths.ensure()

        self.memory = memory or MemoryStore(self.s.paths.db,
                                            working_items=self.s.limits.working_memory_items)
        self.tools = tools or ToolRegistry()
        self.log = log or _Log(self.s.paths.log_file)

        # 临时占位，构造完 ledger 后再换真链
        self.llm: LLM = llm or StubLLM()
        self._llm_is_stub = isinstance(self.llm, StubLLM)

        self.guard = guard or Guard(
            allow_shell=False,
            workspace=self.s.paths.workspace,
            max_consecutive_failures=self.s.limits.max_consecutive_failures,
        )

        register_core_tools(self.tools, memory=self.memory, workspace=self.s.paths.workspace)
        self.external_agents: list[str] = []
        if wire_external:
            self.external_agents = register_external_agents(
                self.tools, self.s.paths.root / "agents.json"
            )

        self.ledger: Ledger | None = None   # run() 里构造（预算属于单次任务）
        self.planner = Planner(self.llm, self.tools)
        self.wm = WorldModel(self.memory, self.llm)

    # ------------------------------------------------------------------
    def run(self, goal: str, *, budget_factor: float = 1.0, tag: str | None = None) -> Outcome:
        base = Budget(
            steps=self.s.limits.max_steps,
            llm_calls=self.s.limits.max_llm_calls,
            wall_s=self.s.limits.max_wall_s,
            candidates=self.s.limits.max_candidates,
        )
        budget = base.scaled(budget_factor, tag or ("1x" if budget_factor == 1 else f"{budget_factor:g}x"))
        self.ledger = Ledger(budget)

        # 预算/账本变了 ⇒ planner 与 wm 要看到同一个 LLM 计数口径
        counted = _LedgeredLLM(self.llm, self.ledger)
        self.planner.llm = counted
        self.wm.llm = counted

        self.log(f"▶ 任务开始: {goal[:120]}")
        self.log(f"  {self.ledger.summary()} | llm={'stub(离线)' if self._llm_is_stub else 'http'}"
                 f" | 外部能力 {len(self.external_agents)} 个")
        self.memory.record("goal", goal, budget_tag=budget.tag)

        history: list[str] = []
        last_error = ""
        consecutive_failures = 0
        answer = ""

        try:
            while True:
                self.ledger.check()
                self.ledger.spend_step()
                it = self.ledger.steps_used

                # ⑦ 熔断（级联失败）
                v = self.guard.check_cascade(consecutive_failures)
                if not v.allow:
                    self.log(f"  ✋ {v.as_line()}")
                    return self._finish("cascade", "", v.reason, history)

                # ① 组装状态
                state = self._state(goal, history, it)

                # ② ACT：快通道还是深通道。
                #    同时受**候选预算**约束 —— 否则 candidates 这个维度就是装饰：
                #    声明了上限却从不消费，正是 V5 "字段无消费者" 的同类病灶。
                complex_task = looks_complex(goal, consecutive_failures)
                cand_left = max(1, budget.candidates - self.ledger.candidates_used)
                n = min(budget.candidates, cand_left) if complex_task else 1

                # ③ 规划（失败过就带错误原文重规划，不原地重试）
                if consecutive_failures and last_error:
                    plan = self.planner.replan(
                        goal=goal, failed=Step("(上一步)", "?", {}),
                        error=last_error, history=history, n=n)
                else:
                    plan = self.planner.propose(
                        goal=goal, state=state, history=history, n=n,
                        guidance=self.wm.guidance(action=goal))
                if plan.degraded:
                    self.log(f"  ⚠️ {plan.note}")
                if not plan:
                    consecutive_failures += 1
                    last_error = "规划器未产出可用步骤"
                    history.append(f"[step {it}] 规划失败：无可用候选")
                    continue

                # 探索宽度**真实生效**：规划器可能多给了候选，这里按 n 截断。
                # （只声明上限却不截断 = 字段无消费者，V5 的同类病灶。）
                cands = plan.steps[:n]
                self.ledger.spend_candidate(len(cands))
                self.log(f"  [{it}] 通道={'深' if complex_task else '快'} 候选={len(cands)}")

                # ④⑤⑥ 逐候选过闸 → 预演 → 执行
                executed = False
                for cand in cands:
                    spec = self.tools.get(cand.tool)

                    # 目标劫持：**只审会改变外部世界的动作**。
                    # 理由：goal-hijack 的危害在于"干了不该干的事"；一个纯粹的
                    # 收尾陈述（finish）或只读检索（recall）不改变外部世界，
                    # 拿它去做词汇重合检查只会误杀合法的终止动作 ——
                    # 这正是契约测试 I1 抓出来的真实缺陷（级联熔断把正常收尾掐死）。
                    if spec is not None and (spec.mutating or spec.external):
                        gv = self.guard.check_goal(action=cand.as_line(), goal=goal)
                        if not gv.allow:
                            self.log(f"      ✋ {gv.as_line()}")
                            continue
                    # 工具滥用
                    if cand.tool == "run_shell":
                        sv = self.guard.check_shell(str(cand.args.get("cmd", "")))
                        if not sv.allow:
                            self.log(f"      ✋ {sv.as_line()}")
                            continue
                    # 记忆投毒
                    if cand.tool == "note":
                        mv = self.guard.check_memory_write(
                            source=str(cand.args.get("source", "agent")),
                            confidence=float(cand.args.get("confidence", 0.6)))
                        if not mv.allow:
                            self.log(f"      ✋ {mv.as_line()}")
                            continue

                    # 预演（只有深通道才付这个成本 —— 这就是 ACT 的实际意义）
                    pred = None
                    if complex_task:
                        pred = self.wm.predict(state=state, action=cand.as_line(), use_llm=True)
                        if not pred.trustworthy:
                            self.wm.suppressed += 1
                            self.log(f"      🔮 预演置信度 {pred.confidence:.2f} 过低 ⇒ 不进决策链")

                    result = self.tools.dispatch(cand.tool, cand.args)
                    self.log(f"      ↳ {cand.tool} ok={result.ok} {cand.intent[:60]}")

                    # 观测 + 失配沉淀
                    if pred is not None and pred.trustworthy:
                        mm = self.wm.observe(pred, result.as_observation())
                        if mm:
                            self.log(f"      🔁 失配 一致度={mm.agreement:.2f} ⇒ 已沉淀为启发式")

                    history.append(f"[step {it}] {cand.as_line()} ⇒ {'成功' if result.ok else '失败'}")

                    if not result.ok:
                        consecutive_failures += 1
                        last_error = result.error
                        self.memory.record("failure", cand.as_line(),
                                           error=result.error[:300])
                        executed = True
                        break
                    consecutive_failures = 0
                    last_error = ""

                    if cand.tool == "finish":
                        answer = result.output
                        self.log(f"  ✅ 收尾: {answer[:120]}")
                        return self._finish("done", answer, "", history)

                    self.memory.record("outcome", result.as_observation()[:1000],
                                       tool=cand.tool, intent=cand.intent[:200])
                    executed = True
                    break

                if not executed:
                    consecutive_failures += 1
                    last_error = "全部候选均被 guard 拦截"
                    history.append(f"[step {it}] 全部候选被拦")
                    self.log(f"  ⛔ 全部候选被拦 ({consecutive_failures} 次连续失败)")

        except BudgetExhausted as e:
            # 预算是硬约束：如实报告"没做完"，不假装完成
            self.log(f"  ⏳ {e}")
            return self._finish("budget_exhausted", "", str(e), history)
        except Exception as e:  # noqa: BLE001
            self.log(f"  💥 内核异常 {type(e).__name__}: {e}")
            return self._finish("error", "", f"{type(e).__name__}: {e}", history)

    # ------------------------------------------------------------------
    def _state(self, goal: str, history: Sequence[str], it: int) -> str:
        parts = [f"[目标] {goal}"]
        wm = self.memory.working_view()
        if wm:
            parts.append("[工作记忆] " + " ｜ ".join(wm[-6:]))
        recalled = self.memory.recall(goal, k=self.s.limits.recall_k)
        if recalled:
            parts.append("[语义记忆] " + " ｜ ".join(f.text[:120] for f in recalled))
        if history:
            parts.append("[轨迹] " + " ｜ ".join(history[-6:]))
        parts.append(f"[第 {it}/{self.ledger.budget.steps if self.ledger else '?'} 步]")
        return "\n".join(parts)

    def _finish(self, status: str, answer: str, reason: str, history: Sequence[str]) -> Outcome:
        stats = {
            "budget": self.ledger.snapshot() if self.ledger else {},
            "guard": self.guard.report(),
            "worldmodel": self.wm.stats(),
            "planner": self.planner.stats(),
            "memory": self.memory.stats(),
            "llm": "stub" if self._llm_is_stub else "http",
            "external_agents": self.external_agents,
        }
        self.memory.record("finish", f"{status}: {answer or reason}"[:1000],
                           status=status, stats=stats)
        self.log(f"■ {status} | steps={len(history)} | guard_blocked={stats['guard']['total']}"
                 f" | 失配={stats['worldmodel']['mismatches']} | 语义记忆={stats['memory']['facts']}")
        return Outcome(status, answer, reason, len(history), stats)
