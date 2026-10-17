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

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from . import config as cfg
from .budget import Budget, BudgetExhausted, Ledger
from .flow import FlowMeter
from .guard import Guard
from .llm import HTTPLLM, LLM, LLMError, Message, StubLLM, build_llm
from .memory import MemoryStore
from .metacog import SelfModel
from .planner import Planner, Step, action_signature, looks_complex
from .tools import (ToolRegistry, external_agent_names, register_core_tools,
                    register_external_agents)
from .values import Values
from .worldmodel import WorldModel


#: 单条观测写进工作记忆时保留的字符上限。
#:
#: ⚠️ 这是一个**硬瓶颈**，不是随手写的数字：`perceive>memory` 的覆盖率由它
#: 直接决定（5000 字的工具输出 ⇒ 0.06），而下一步关于上一步的一切都只来自
#: 这 300 字。提成常量有两个理由：
#:   ① 让 `bench/capability.py` 的探针与实现读**同一个事实源** ——
#:      探针里再抄一个 300，就会漂移成两套真相（V5 的老毛病）；
#:   ② 修法一旦改成"存索引 + 按需取回全文"，这个常量的语义要跟着变，
#:      那时只有一个地方要改。
WORKING_CHAR_CAP = 300


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
    """把 LLM 调用计数收敛到唯一账本，并在这里做**传输层**的有界重试。

    不把 ledger 传给 HTTPLLM 是**刻意的**：两处计数 = 两处真相 = 迟早对不上。
    所以重试也放在这一层 —— 每次尝试都 `spend_llm()`，账本记得住"实际发了几次请求"，
    而不是"成功了几次"。这两者的差就是抖动率，是高价值读数。

    ⚠️ 只重试 `transient=True` 的错误（超时 / 连接断 / 5xx / 429）。
    模型侧失败（4xx 参数错、响应结构不符）**一律不重试** —— 重发同样的请求
    只会得到同样的错；那种情况正确的动作是**换路径**（改提示词 / 改协议），
    而不是原地重发。把两者混在一起重试，就是 V5 的"同一假设无限重派"。
    """

    def __init__(self, inner: LLM, ledger: Ledger, *,
                 retries: int = 0, backoff_s: float = 0.5) -> None:
        self.inner = inner
        self.ledger = ledger
        self.retries = max(0, int(retries))
        self.backoff_s = max(0.0, float(backoff_s))
        #: 读数字段：抖动重试了几次、最终仍失败几次。**有消费者**（进 stats）。
        self.transient_retries = 0
        self.transient_exhausted = 0

    def complete(self, messages: Sequence[Message], **kw: Any) -> str:
        # ⚠️ 这里刻意用 `for` 而**不是** `while`：内核「只准有一个 while」
        #    是一条被 AST 钉死的不变量（契约 J6）。重试本来就是有界的，
        #    写成 for 既表达"最多 N+1 次"，又不让那条不变量被悄悄破掉。
        #    （把 J6 放宽去容纳一个 while = 尺子下移，那不是修法。）
        for attempt in range(self.retries + 1):
            self.ledger.spend_llm()
            try:
                return self.inner.complete(messages, **kw)
            except LLMError as e:
                transient = bool(getattr(e, "transient", False))
                if not transient:
                    # 模型侧失败：重发同样的请求只会得到同样的错 ⇒ 立刻抛，换路径。
                    raise
                if attempt >= self.retries:
                    self.transient_exhausted += 1
                    raise
                self.transient_retries += 1
                # 退避用线性增长：本地/低价端点抖动通常是瞬时的，指数退避过于激进。
                time.sleep(self.backoff_s * (attempt + 1))
        raise AssertionError("不可达：retries ≥ 0 保证循环至少执行一次并 return 或 raise")


@dataclass
class Outcome:
    status: str                      # done | budget_exhausted | cascade | blocked
                                     # | error | stalled（拒绝原地打转）
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

        # ⚠️ 必须走 build_llm（唯一构造入口）。**不许**写 `llm or StubLLM()`。
        #
        # 这里曾经就是 `llm or StubLLM()`，后果是一次真实事故（2026-10-17 实测抓到）：
        #   配置了真模型，但 `--goal` 与 `--solver llm`（内核路径）**全程用离线桩**，
        #   1 秒"跑完" 6 题、每轮报 done、读数 mean=0.0000 却标 `可信=✅`，
        #   而真模型一次都没被调用 —— 连 `build_llm` 都只 import 不使用（死导入）。
        # 这正是 V5 的病（静默降级 + 假绿）在 V1 里的同构体，所以修法与防线都钉死在这：
        #   · 构造只此一条路（K1/K3 行为钉死：spy 必须被调用）
        #   · llm_kind 让"实际用的是什么"成为可读证据（K5），标签再也不能撒谎
        self.llm: LLM = llm if llm is not None else build_llm(self.s.llm)
        self._llm_is_stub = isinstance(self.llm, StubLLM)
        #: 实际使用的模型通道，供读数血缘使用。**这是事实，不是声明。**
        #: ⚠️ 刻意**不是**二值（stub/http）：离线自检用的 DemoLLM 既不是桩也不是 HTTP，
        #:    若写成 `"stub" if ... else "http"`，它就会被标成 http —— 又一个"标签撒谎"。
        #:    认不出来的东西就报 "local"，不许往"看起来更好"的那一档上贴。
        if self._llm_is_stub:
            self.llm_kind: str = "stub"
        elif isinstance(self.llm, HTTPLLM):
            self.llm_kind = "http"
        else:
            self.llm_kind = "local"

        self.guard = guard or Guard(
            allow_shell=False,
            workspace=self.s.paths.workspace,
            max_consecutive_failures=self.s.limits.max_consecutive_failures,
        )

        register_core_tools(self.tools, memory=self.memory, workspace=self.s.paths.workspace)
        if wire_external:
            register_external_agents(self.tools, self.s.paths.root / "agents.json")
        # 外部能力以**注册表**为准，而不是以"本次调用新增了什么"为准。
        # ⚠️ 这里原先是 `self.external_agents = register_external_agents(...)` 的返回值 ——
        #    调用方若预置了注册表（或在自检里复用 registry），这个计数就在说谎。
        #    `external_agent_names()` 的文档里写着"别处不要再自己 filter"，
        #    用返回值正好违反了它自己定的单一口径（契约 L6 钉死）。
        self.external_agents: list[str] = external_agent_names(self.tools)

        self.ledger: Ledger | None = None   # run() 里构造（预算属于单次任务）
        self._counted: _LedgeredLLM | None = None  # run() 里构造（抖动重试的读数出口）
        self.planner = Planner(self.llm, self.tools,
                               max_tokens=self.s.limits.llm_max_tokens)
        self.wm = WorldModel(self.memory, self.llm)

        # ── 三个"调制层"（不是相位）────────────────────────────────────
        # 相位是环上的必经点，缺了就断流；调制层只改变**怎么走**。
        # 这个区分是本轮对"模块是否必要"的答案，写在这里是因为代码结构
        # 必须与那个答案一致 —— 否则文档说一套、依赖图是另一套（V5 的老毛病）。
        #
        #: 价值层：方向的否决与偏序。**不做评分**（`values.py` 首屏写死）。
        self.values = Values()
        #: 通量计：环上每条边真的过去了多少。它是**尺子**，不参与决策。
        self.flow = FlowMeter()
        #: 自我模型：只读**已完成**任务的外部读数。物理上拿不到当前 goal。
        self.selfmodel = SelfModel()

    # ------------------------------------------------------------------
    def run(self, goal: str, *, budget_factor: float = 1.0, tag: str | None = None,
            family: str | None = None) -> Outcome:
        """跑一个任务。

        `family` 是**能力族标签**（如 `numeric_logic`），只给自我模型做资源建议用。
        ⚠️ 刻意**不**从 goal 里猜族：一旦自我模型读得到题目内容，
        它就会在"看题之后"给自己分配资源，那就退化成自评分的入口了。
        调用方不给 ⇒ 传 None ⇒ 自我模型返回默认值并明说"样本不足"。
        """
        base = Budget(
            steps=self.s.limits.max_steps,
            llm_calls=self.s.limits.max_llm_calls,
            wall_s=self.s.limits.max_wall_s,
            candidates=self.s.limits.max_candidates,
        )
        budget = base.scaled(budget_factor, tag or ("1x" if budget_factor == 1 else f"{budget_factor:g}x"))
        self.ledger = Ledger(budget)
        # 通量与价值层的计数是**单轮**读数（预算也是单轮的）；
        # 自我模型是**跨轮资产**，所以它不在这里重置。
        # 混在一起会出现"这一轮 0 次否决"而那个 0 是上一轮留下的 —— V5 的老毛病。
        self.flow = FlowMeter()
        self.values = Values()

        # 预算/账本变了 ⇒ planner 与 wm 要看到同一个 LLM 计数口径
        counted = _LedgeredLLM(self.llm, self.ledger,
                               retries=self.s.limits.llm_retries,
                               backoff_s=self.s.limits.llm_retry_backoff_s)
        self._counted = counted
        self.planner.llm = counted
        self.wm.llm = counted

        self.log(f"▶ 任务开始: {goal[:120]}")
        self.log(f"  {self.ledger.summary()} | llm_kind={self.llm_kind}"
                 f" | 外部能力 {len(self.external_agents)} 个")
        if self._llm_is_stub and self.s.llm.usable():
            # 配置了真模型却拿到桩 ⇒ 必须**显式喊出来**。
            # 静默降级 = 缺陷（不是特性）：它让读数看起来像模型成绩，实际什么也没测。
            self.log("  ⚠️ 配置了真模型（V1_LLM_API_KEY 已设）但内核使用离线桩"
                     " ⇒ 本次读数**不得**当作模型能力")
        self.memory.record("goal", goal, budget_tag=budget.tag)

        history: list[str] = []
        last_error = ""
        consecutive_failures = 0
        answer = ""
        #: 已**尝试过**的动作签名（含被 guard 拦下的）—— 跨步骤的"不许原地打转"。
        #: 为什么连被拦的也要记：重复提出一个注定被拦的动作，同样不产生新信息。
        attempted_sigs: set[str] = set()
        repeats_skipped = 0
        stalled = 0

        def _done(status: str, ans: str, why: str) -> Outcome:
            """所有出口都从这里走 —— 保证"停滞/重复"这类计数**任何状态都带得走**。
            两处各自拼 stats 就会漂移（V5 的老毛病）。
            """
            return self._finish(status, ans, why, history, extra={
                "repeats_skipped": repeats_skipped,
                "stalled": stalled,
                "attempted_actions": len(attempted_sigs),
            })

        try:
            while True:
                self.ledger.check()
                self.ledger.spend_step()
                it = self.ledger.steps_used

                # ⑦.5 收尾余量 —— 必须在**任何**探索动作之前判断。
                # 实测：有一题把 120s 全花在 recall 上，最后 budget_exhausted 交付了零。
                # 探索是手段、交付是目的；当预算只够收尾时，就该转向收尾。
                if self._closing_reserve_hit():
                    return self._close_out(goal, history, _done)

                # ⑦ 熔断（级联失败）
                v = self.guard.check_cascade(consecutive_failures)
                if not v.allow:
                    self.log(f"  ✋ {v.as_line()}")
                    return _done("cascade", "", v.reason)

                # ① 组装状态
                state = self._state(goal, history, it)

                # ② ACT：快通道还是深通道。
                #    同时受**候选预算**约束 —— 否则 candidates 这个维度就是装饰：
                #    声明了上限却从不消费，正是 V5 "字段无消费者" 的同类病灶。
                complex_task = looks_complex(goal, consecutive_failures)
                cand_left = max(1, budget.candidates - self.ledger.candidates_used)
                n = min(budget.candidates, cand_left) if complex_task else 1

                # ⑧ 元认知：**只在有证据时**才让它覆盖上面的判断。
                #    `adv.confident` 是显式开关，不是靠比较 `basis` 字符串 ——
                #    一个"样本不足但语气笃定"的建议会把 ACT 的判定整体抹掉，
                #    而日志上写着"样本不足"，决策已经悄悄变了。
                adv = self.selfmodel.advise(family=family,
                                            max_candidates=budget.candidates)
                if adv.confident:
                    complex_task = adv.deep
                    n = (min(budget.candidates, cand_left, adv.candidates)
                         if complex_task else 1)
                    self.log(f"  {adv.as_line()}")

                # ③ 规划（失败过就带错误原文重规划，不原地重试）
                #    同时把"已尝试过的动作签名"精确交给规划器 —— 不是靠自由文本
                #    提醒（实测里模型会无视"已试过：…"的自然语言）。
                if consecutive_failures and last_error:
                    plan = self.planner.replan(
                        goal=goal, failed=Step("(上一步)", "?", {}),
                        error=last_error, history=history, n=n,
                        blocked=sorted(attempted_sigs),
                        # ⚠️ 重规划**更**需要易错点：它正是"刚栽了"的那一次。
                        #    原来这里不传 ⇒ guidance 只在首次尝试时有效。
                        guidance=self.wm.guidance(action=goal))
                else:
                    plan = self.planner.propose(
                        goal=goal, state=state, history=history, n=n,
                        guidance=self.wm.guidance(action=goal),
                        blocked=sorted(attempted_sigs))
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
                # 通量：推理交了 N 个想法出去（下游是否真的做，看 `reason>act` 的消费）
                self.flow.emit(it, "reason>act", len(cands), sum(len(c.as_line()) for c in cands))

                # ④⑤⑥ 逐候选过闸 → 预演 → 执行
                executed = False
                repeats = 0
                for cand in cands:
                    spec = self.tools.get(cand.tool)
                    sig = action_signature(cand.tool, cand.args)

                    # ── 停滞闸：这个动作已经尝试过了 ────────────────────────
                    # 实测背景：内核曾把同一 `recall` 重派 5 次、每次 ok=True，
                    # 靠 `consecutive_failures` 完全拦不住（它只数失败），
                    # 最后烧光 120s 才以 budget_exhausted 收场 ——
                    # 这就是 V5 的"同一假设无限重派"在 V1 里的同构体。
                    # 这里**显式打标并跳过**：重复执行不产生任何新信息。
                    if sig in attempted_sigs:
                        repeats += 1
                        repeats_skipped += 1
                        self.log(f"      ⏭ 重复动作已跳过（不产生新信息）: {cand.tool}")
                        history.append(f"[step {it}] 重复动作被跳过：{cand.as_line()}")
                        continue
                    attempted_sigs.add(sig)

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

                    # ── 价值层：方向 → 授权 ────────────────────────────────
                    # 放在 guard **之后**是刻意的：安全是绝对底线，先过；
                    # 过了才问"值不值得做、是不是在回应目标"。
                    # 顺序反过来会让一个偏题的删除动作先被"偏题"拦下，
                    # 于是它看起来像个方向问题而不是安全问题 —— 而它本来就
                    # 该因为"危险"被绝对拒绝。**两种否决不能互相顶替。**
                    vj = self.values.judge(tool=cand.tool, args=cand.args, goal=goal,
                                           is_finish=(cand.tool == "finish"))
                    if not vj.allow:
                        self.log(f"      ⊘ {vj.as_line()}")
                        history.append(f"[step {it}] 价值层否决[{vj.kind}]：{cand.intent[:40]}")
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

                    # 通量：N 个想法里，这一个真的被执行了（`reason>act` 的消费端）
                    self.flow.take("reason>act", 1, len(cand.as_line()))
                    # 回边的原料：本步的观测原文。它是否真的活到下一回合，
                    # 由下一次 `_state()` 按 token 覆盖率计量（见 `sample_reuse`）。
                    obs_text = result.as_observation()
                    self.flow.note_result(obs_text)

                    # 观测 + 失配沉淀
                    #
                    # ⚠️ `ok=result.ok` 是**必须**传的（缺陷 #12 的结构性根因）：
                    #    不传，世界模型就无从知道这一步到底做对了没有 ——
                    #    它只能退回去比"措辞"，而那种比对**结构上必错**。
                    #    `result.ok` 是内核自己持有的地面真相，没有理由不给它。
                    if pred is not None and pred.trustworthy:
                        mm = self.wm.observe(pred, result.as_observation(), ok=result.ok)
                        if mm:
                            self.log(f"      🔁 失配[{mm.kind}] {mm.as_line()}"
                                     f" ⇒ 已沉淀为启发式")

                    history.append(f"[step {it}] {cand.as_line()} ⇒ {'成功' if result.ok else '失败'}")

                    # ── 工作记忆：本步的**真实输出**必须进入下一步的上下文 ──────
                    # 为什么这行是"协同"的要害（2026-10-17 接线审计实测）：
                    #   `history` 只记**动作 + 成败**（`recall(...) ⇒ 成功`），
                    #   模型在下一步**看不到工具返回了什么**；
                    #   而 `record("outcome", …)` 写的是 **episodes 表**，
                    #   `recall()` 只查 **facts 表** ⇒ 情景记忆在生产路径上一次都读不到
                    #   （真模型实测：5 题写了 16 条 outcome，进入提示词的 0 条）。
                    #   于是"三层记忆"里 working 这层**只读不写**、episodes **只写不读** ——
                    #   多步任务的上下文连续性实际上靠的是 `history` 里那行"成功/失败"。
                    # 修法不是再加一条通道，而是让本来为此设计的那层**通电**。
                    # 通量：感知写进记忆。**产出按观测原文长度、消费按实际存下的长度**，
                    #    两者的比就是"写入截断率"。这个比不是形式主义的指标：
                    #    它直接回答"工具返回的 5000 字里，下一步还看得见多少"。
                    full = f"{cand.tool} ⇒ {'✔' if result.ok else '✘'} {result.as_observation()}"
                    stored = full[:WORKING_CHAR_CAP]
                    self.flow.emit(it, "perceive>memory", 1, len(full))
                    self.flow.take("perceive>memory", 1, len(stored))
                    self.memory.think(stored)

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
                        return _done("done", answer, "")

                    self.memory.record("outcome", result.as_observation()[:1000],
                                       tool=cand.tool, intent=cand.intent[:200])
                    executed = True
                    break

                if not executed:
                    if repeats and repeats == len(cands):
                        # 本轮**全部**候选都是已试过的动作 ⇒ 停滞。
                        # 不停在原地继续烧预算，而是计数并按阈值收尾 ——
                        # 如实报告"没有进展"，不假装在做事情。
                        stalled += 1
                        self.log(f"  🧊 本轮 {repeats} 个候选全部是已试过的动作"
                                 f" ⇒ 停滞 {stalled}/{self.s.limits.max_stall}")
                        if stalled >= self.s.limits.max_stall:
                            return _done(
                                "stalled", "",
                                f"拒绝原地打转：连续 {stalled} 轮没有任何新动作"
                                f"（已跳过 {repeats_skipped} 次重复）")
                        continue
                    consecutive_failures += 1
                    last_error = "全部候选均被 guard 拦截"
                    history.append(f"[step {it}] 全部候选被拦")
                    self.log(f"  ⛔ 全部候选被拦 ({consecutive_failures} 次连续失败)")

        except BudgetExhausted as e:
            # 预算是硬约束：如实报告"没做完"，不假装完成
            self.log(f"  ⏳ {e}")
            return _done("budget_exhausted", "", str(e))
        except Exception as e:  # noqa: BLE001
            self.log(f"  💥 内核异常 {type(e).__name__}: {e}")
            return _done("error", "", f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------------
    def _state(self, goal: str, history: Sequence[str], it: int) -> str:
        parts = [f"[目标] {goal}"]
        # 从记忆进到推理的**实际条数**：只在真正拼进上下文的地方累加，
        # 不在上游估一个数 —— 估出来的数会让"覆盖率"变成自我安慰。
        mem_items = 0
        mem_chars = 0
        wm = self.memory.working_view()
        if wm:
            # ⚠️ **必须逐条截断**：工作记忆存的是工具输出的原文，不截断的话
            #    一个大文件/一段长代码就能把整个上下文窗口吃掉
            #    —— 那时"多步任务的上下文"反而变成"一步就把预算烧完"。
            recent = wm[-6:]
            block = " ｜ ".join(w[-200:] for w in recent)
            parts.append("[工作记忆] " + block)
            mem_items += len(recent)
            mem_chars += len(block)
        recalled = self.memory.recall(goal, k=self.s.limits.recall_k)
        if recalled:
            block = " ｜ ".join(f.text[:120] for f in recalled)
            parts.append("[语义记忆] " + block)
            mem_items += len(recalled)
            mem_chars += len(block)
        if history:
            parts.append("[轨迹] " + " ｜ ".join(history[-6:]))
        parts.append(f"[第 {it}/{self.ledger.budget.steps if self.ledger else '?'} 步]")
        state = "\n".join(parts)
        # 通量：记忆 → 推理。分子 = 真的拼进上下文的条数，分母 = 池子里可用的条数。
        # ⚠️ 这个比值**不是健康度**（库越大比例越小），只回答"这条线通没通"。
        self.flow.emit(it, "memory>reason", self.memory.pool_size(), 0)
        if mem_items:
            self.flow.take("memory>reason", mem_items, mem_chars)
        # 通量回边：上一回合的**行动结果**，在本回合上下文里还剩多少 token。
        # 这是「流畅」唯一的量化判据 —— 若它恒为 0，闭环实际上是开链。
        self.flow.sample_reuse(state)
        return state

    # ------------------------------------------------------------------
    def _closing_reserve_hit(self) -> bool:
        """预算是否已进入"只够收尾"的区间。

        ⚠️ 这条闸的存在理由是一次实测：有一题把 120s 预算全花在 `recall` 上，
        最后以 `budget_exhausted` 收场 —— **交付了零东西**。
        "如实报告没做完"本身是对的（不假装完成），但**探索把交付的配额也吃掉了**，
        那是设计缺陷而不是诚实：手段吃掉了目的。

        ⚠️ 两个阈值**取 0 表示关闭**（本轮实测修正的一处语义坑）：
        原来写的是 `steps_left <= close_reserve_steps`，于是 `close_reserve_steps=0`
        在**刚进入循环时**（steps_left 恰好为 0）就为真 ⇒ 置 0 反而"立即收尾"，
        与"关闭"完全相反。契约 I10b（关掉余量后纯耗尽路径仍可达）就是钉这一点的。
        """
        if self.ledger is None:
            return False
        b = self.ledger.budget
        lim = self.s.limits
        steps_left = b.steps - self.ledger.steps_used
        wall_left = b.wall_s - self.ledger.elapsed()
        if lim.close_reserve_steps > 0 and steps_left <= lim.close_reserve_steps:
            return True
        if lim.close_reserve_s > 0 and wall_left <= lim.close_reserve_s:
            return True
        return False

    def _close_out(self, goal: str, history: Sequence[str],
                   done: Callable[[str, str, str], "Outcome"]) -> Outcome:
        """进入收尾余量后的交付 —— 尽力把答案交出去，并**如实说明**是被预算切断的。

        不是"假装完成"：答案要么来自一次专门索要答案的调用（余量就是留给它的），
        要么来自已收集到的材料；两者都拿不到就如实报空。

        ⚠️ `done` 必须由调用方显式传入，**不能**在这里引用 `run()` 里的局部闭包：
        本方法曾是 `run()` 内的嵌套函数写法搬到类上时漏改的一处（Q 组实测抓到的
        `NameError: _done is not defined`）—— 收尾路径一旦炸，交付就退回零。
        收尾是"最后一道网"，它自己的作用域绝不能再依赖调用栈。
        """
        self.log("  ⏱ 触发收尾余量 ⇒ 转向交付（探索到此为止）")
        best = ""
        origin = ""
        try:
            best = self.planner.answer(goal).strip()
            origin = "收尾调用"
        except Exception as e:  # noqa: BLE001
            self.log(f"      收尾调用失败 {type(e).__name__}: {e}")
        if not best:
            # 兜底一：已沉淀的 outcome 情景记忆（最接近"结论"的材料）
            eps = self.memory.episodes(kind="outcome", limit=1)
            best = eps[0].content if eps else ""
            origin = "episodic"
        if not best and history:
            # 兜底二：轨迹最后一条。这条兜底存在的意义是**绝不返回空**——
            # 让 `history` 这个参数有真实消费者，而不是装饰参数。
            best = history[-1]
            origin = "history"
        if not best:
            origin = "空"
        self.log(f"  📦 收尾交付 {len(best)} 字符（来源={origin}）")
        return done("closed", best, "closing_reserve：预算不足，交付已有材料")

    # ------------------------------------------------------------------
    def _finish(self, status: str, answer: str, reason: str, history: Sequence[str],
                *, extra: dict | None = None) -> Outcome:
        stats = {
            "budget": self.ledger.snapshot() if self.ledger else {},
            "guard": self.guard.report(),
            "values": self.values.report(),
            "flow": self.flow.report(),
            "selfmodel": self.selfmodel.report(),
            "worldmodel": self.wm.stats(),
            "planner": self.planner.stats(),
            "memory": self.memory.stats(),
            "llm": "stub" if self._llm_is_stub else "http",
            "llm_kind": self.llm_kind,   # 与 "llm" 同源；保留两个键是为了兼容旧读法
            "external_agents": self.external_agents,
            # 停滞/重复 —— "重复的成功不是进展"这条的读数出口
            "loop": dict(extra or {}),
            # 传输层抖动 —— "基础设施的锅 ≠ 模型的锅"这条的读数出口。
            # 有消费者：`■` 汇总行会把它打印出来（零值不显示，避免噪声）。
            "transport": ({
                "retries": self._counted.transient_retries,
                "exhausted": self._counted.transient_exhausted,
            } if self._counted is not None else {"retries": 0, "exhausted": 0}),
        }
        self.memory.record("finish", f"{status}: {answer or reason}"[:1000],
                           status=status, stats=stats)
        tx = stats["transport"]
        tx_line = ""
        if tx["retries"] or tx["exhausted"]:
            tx_line = f" | 抖动重试={tx['retries']} 重试后仍失败={tx['exhausted']}"
        # 世界模型读数：**同时**打印分母。
        # 只印 `失配=3` 是假读数 —— 3 次预演里错 3 次、和 30 次里错 3 次，
        # 是两件完全不同的事；前者说明信号恒红，后者说明它在工作。
        wms = stats["worldmodel"]
        wm_line = (f" | 预演={wms['predictions']} 检验={wms['scored']}"
                   f" 命中={wms['hits']}"
                   f" 命中率={'n/a' if wms['hit_rate'] is None else wms['hit_rate']}"
                   f" 失配={wms['mismatches']}(成败{wms['mismatches_outcome']}"
                   f"/文本{wms['mismatches_text']})")
        if wms["unscored"]:
            wm_line += f" ⚠️未检验={wms['unscored']}"
        # 价值层与通量：**两者都必须带分母**。
        # `denied=1` 在 3 次判定里和在 300 次判定里是两件完全不同的事；
        # 通量同理 —— 覆盖率离开产出量就没有意义。
        vs = stats["values"]
        v_line = (f" | 价值判定={vs['judged']} 否决={vs['denied']}"
                  f"({','.join(f'{k}:{v}' for k, v in vs['by_kind'].items()) or '-'})")
        f_line = f" | {self.flow.as_line()}"
        self.log(f"■ {status} | steps={len(history)} | guard_blocked={stats['guard']['total']}"
                 f"{wm_line}{v_line}{f_line} | 语义记忆={stats['memory']['facts']}"
                 f"{tx_line}")
        return Outcome(status, answer, reason, len(history), stats)
