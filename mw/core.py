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

七条纪律，每条都对应一次实测翻车：
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
from .abstractor import ABSTRACT_MODE, abstract_only
from .budget import Budget, BudgetExhausted, Ledger
from .critique import Critique, CritiqueLedger
from .flow import FlowMeter
from .guard import Guard
from .ledger import ScoreLedger
from .llm import HTTPLLM, LLM, LLMError, Message, StubLLM, build_llm
from .memory import BlobIntegrityError, MemoryStore
from .metacog import SelfModel, load_evidence
from .planner import Planner, Step, action_signature, looks_complex
from .tools import (ToolRegistry, ToolResult, external_agent_names,
                    external_last_meta, register_core_tools,
                    register_external_agents)
from .values import Values
from . import verify as vfy
from .worldmodel import WorldModel


#: 单条观测写进工作记忆时保留的字符上限。
#:
#: ⚠️ 这是一个**硬瓶颈**，不是随手写的数字：`perceive>memory` 的覆盖率由它
#: 直接决定（5000 字的工具输出 ⇒ 0.06），而下一步关于上一步的一切都只来自
#: 这 300 字。提成常量有两个理由：
#:   ① 让 `bench/capability.py` 的探针与实现读**同一个事实源** ——
#:      探针里再抄一个 300，就会漂移成两套真相；
#:   ② 修法一旦改成"存索引 + 按需取回全文"，这个常量的语义要跟着变，
#:      那时只有一个地方要改。
WORKING_CHAR_CAP = 300

#: 每次 `_state()` 的产物最多留几步（第三十六轮 §17-7h）。
#:
#: 为什么是 12：`limits.max_steps` 的默认值就是 12，超过它没有任何意义
#: （多出来的状态不可能被生成）。留的是一个**上界**，不是一个调参旋钮：
#: 它的唯一用途是"runner 可以在任务结束后把这批 state 取走落盘"，
#: 供**内核之外**的探针按块审计（R1 第二层）。⛔ 内核不读它做任何决策。
STATE_LOG_CAP = 24

#: 按需取回时单条观测最多回看多少字符（第二十轮，DESIGN §16.19）。
#:
#: ⚠️ 为什么不把它并进 `WORKING_CHAR_CAP`：两者回答的是**两个不同的问题**。
#:   `WORKING_CHAR_CAP` = "每一步**无条件**占多少上下文"（宽度，必须小且稳定）；
#:   `EXPAND_CHAR_CAP`  = "**一旦觉得意外**，允许临时多要多少"（可回溯深度）。
#:   合并成一个数就等于"把每一步都变长" —— 那会让一步吃掉整个上下文，
#:   而 §17-9 早就写死了：修法**不是**把 300 调大。
#: ⚠️ 600 而不是 1200：它要和 `cfg.STATE_CHAR_CAP`（1600）配得上。
#: 回看块 + 工作记忆索引 + 目标 ≈ 1000 字，剩下的才留给语义记忆与轨迹。
#: 定得比这更大，回看就会把别的块挤掉 —— 那不是"多给了信息"，
#: 是"用一部分上下文换另一部分"，而两者的价值没有可比性。
EXPAND_CHAR_CAP = 600

#: 工作记忆里「已被按需回看升级」的条目标记。**唯一事实源**：
#: 写入侧用它做前缀，渲染侧用它决定"这一条还截不截"。
#: ⚠️ 用前缀而不是另一个集合：窗口滑动会丢条目，按下标记的集合会错位。
EXPAND_MARK = "[回看 "

#: 单个任务最多回看几次。**必须有上限**，否则"按需取回"就是"迟早把窗口塞满"。
EXPAND_MAX_PER_TASK = 3


def recall_trigger(*, ok: bool, error: str, mismatched: bool) -> str:
    """要不要按需回看？返回取回窗口（`"tail"` / `"whole"` / `""` = 不回看）。

    ⛔ 抽成纯函数有三个理由（不是为了好看）：
      ① **可被单独测**：否则触发条件埋在 `run()` 的循环体里，只能靠端到端撞，
         而端到端撞不到的时候，机制"装了但一次不触发"和"没装"长得一模一样。
      ② **只有一处**：写在循环体里，下一次改动就会在旁边再写一遍。
      ③ **不含任何模型输出**：让模型自己决定"要不要看原文"，它会在 100% 的
         情况下自信地说不用 —— 于是机制恒为 0 次，而读数上只显示"没触发"。

    两条触发都要求**信息已经丢了**才动作：
      · T1 `ok=False` 且错误原文长过工作记忆宽度 ⇒ 决定性信息可能在结尾；
      · T2 世界模型的预演被推翻（失配）⇒ 没预料到的细节在原文里，取头尾各一半。
    """
    if not ok and len(error or "") > WORKING_CHAR_CAP:
        return "tail"
    if mismatched:
        return "whole"
    return ""


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
    而不是原地重发。把两者混在一起重试，就是"同一假设无限重派"。

    ── 墙钟：重试链本身也必须守预算（第二十五轮）────────────────────────
    实测事故：`llm_retries=2` + `timeout_s=120` ⇒ 一次**逻辑**调用串起
    `3 × 120 + 退避 1.5 = 361.5s`，而 `wall_s` 也是 120。日志上只留一个
    `LLMError: TimeoutError`、`steps=0`，看起来像"模型太慢"，实际是
    **重试链把预算超支了 3 倍**（第二十四轮 `h01`/`k01` 各被这样打掉一次）。

    所以每次尝试前做两件事，缺一不可：
      1. `check_wall()`：墙钟已尽 ⇒ 抛 `BudgetExhausted`（**可归因**，不是 error）
      2. 按剩余预算**裁剪**本次 `timeout_s` ⇒ 单次尝试永远不会吃光整题
         （`wall_reserve_s` 那部分留给收尾兜底，否则"准时失败"会退化成"没答案"）
    """

    #: 剩余不足这么多秒就**不再开**下一次尝试 —— 一次几乎注定超时的请求，
    #: 只会把最后一点收尾预算也花掉。5s 是"连一次握手都不够"的量级。
    MIN_ATTEMPT_S: float = 5.0

    def __init__(self, inner: LLM, ledger: Ledger, *,
                 retries: int = 0, backoff_s: float = 0.5,
                 wall_reserve_s: float = 0.0,
                 close_overrun_s: float = 0.0) -> None:
        self.inner = inner
        self.ledger = ledger
        self.retries = max(0, int(retries))
        self.backoff_s = max(0.0, float(backoff_s))
        #: 留给收尾兜底的秒数。**不进口径签名**（它不改变量的是什么，只改变
        #: 失败时怎么退场），但它的值来自 `limits.close_reserve_s`（已在签名里）。
        self.wall_reserve_s = max(0.0, float(wall_reserve_s))
        #: 收尾那一次调用允许超出墙钟的秒数（第九十六轮 §63）。
        #: `0.0` ⇒ 行为与旧版**逐位相同**（这是本轮 A/B 的对照组，也是反证）。
        #: ⚠️ 它只在 `closing` 为真时生效 —— 探索阶段不许超预算，一秒都不许。
        self.close_overrun_s = max(0.0, float(close_overrun_s))
        #: 是否已进入收尾阶段。**由 `reserve_release()` 置真**，不另设入口：
        #: 「余量放出来了」与「允许超支」是同一件事的两个面，分成两个开关
        #: 迟早会有人只拨一个。
        self.closing = False
        #: 读数字段：抖动重试了几次、最终仍失败几次。**有消费者**（进 stats）。
        self.transient_retries = 0
        self.transient_exhausted = 0
        # ── 墙钟相关的三个计数（第二十五轮）──────────────────────────────
        # ⛔ 为什么是**三个**而不是一个"被墙钟拦住的次数"：
        #    它们的修法完全不同，合并即不可归因。
        #: ① 本次尝试的超时上限**被裁短**，但请求**真的发出去了**。
        #:    ⇒ 说明预算比"一次调用的正常耗时"还紧：要么减工作量，要么升档。
        #:    ⚠️ 这个字段是 R2/P3 的唯一证据：若只在"放弃"时计数，那么
        #:    「裁到 75s 后照发并超时」与「用满 120s 后超时」在读数上一模一样
        #:    —— 我最初就是这么写的，等于让证据看不见它要证明的那件事。
        self.wall_shortened = 0
        #: ② 连**第一次**都没发出去就放弃（预算连一次起步价都不够）。
        self.wall_refused_first = 0
        #: ③ 放弃的是**重试**（第一次发出去了，但墙钟已不够再试一次）。
        self.wall_refused_retry = 0
        # ── 宽限的两个读数（第九十六轮 §63）────────────────────────────
        #: ④ 宽限**真的抬高**了这一发的上限几次。
        #:    ⚠️ 没有它，"宽限配了但一次没生效"与"宽限救了 5 次"在读数上同形
        #:    —— 而这两种情况要求的下一步动作相反（前者要查配置接线，
        #:    后者说明当前预算确实太紧）。
        self.wall_overrun_grants = 0
        #: ⑤ 本 run 里收尾调用实际**超出** `wall_s` 的最大秒数（0 = 没超）。
        #:    它与 ④ 必须分开：④ 是"给了多少"，⑤ 是"用掉多少"。
        #:    给了 30s 却一秒没用 = 那一发本来就跑得完（宽限是白给的，
        #:    说明真正的问题不在这里）。
        self.overrun_used_s = 0.0

    def _left_s(self, *, honor_overrun: bool) -> float:
        """剩余秒数。**唯一**的剩余量算法（两条路径都走它）。

        `honor_overrun=True` ⇒ 用 `wall_s + close_overrun_s`（仅在收尾阶段）；
        否则用 `wall_s` —— 也就是第二十五轮以来的旧公式。
        ⚠️ 分成两个分支而不是就地加一个 `if`，是为了让「探索阶段逐位不变」
        这件事可以被**逐位比较**（探针 C1/C5 就是这么验的）。
        """
        wall = float(self.ledger.budget.wall_s)
        if honor_overrun:
            wall = self._wall_deadline()
        return wall - self.ledger.elapsed() - self.wall_reserve_s

    def _wall_deadline(self) -> float:
        """当前的墙钟上限（秒）。收尾阶段 = `wall_s + close_overrun_s`。"""
        wall = float(self.ledger.budget.wall_s)
        return wall + (self.close_overrun_s if self.closing else 0.0)

    def _note_overrun(self) -> None:
        """记下"这一发真的超出墙钟多少秒"。⛔ 只在收尾阶段有意义。"""
        if not self.closing:
            return
        over = self.ledger.elapsed() - float(self.ledger.budget.wall_s)
        if over > self.overrun_used_s:
            self.overrun_used_s = over

    def _attempt_timeout(self) -> float:
        """本次尝试许用多少秒。**唯一**的裁剪处。

        上限 = `min(inner 的默认上限, 剩余 − 收尾余量)`。
        剩余 ≤ `MIN_ATTEMPT_S` ⇒ 返回 0 表示"别试了"（由调用方决定抛什么）。
        """
        base = getattr(self.inner, "timeout_s", None)
        if base is None:                     # 桩/无网络：不裁剪
            return float("inf")
        left = self._left_s(honor_overrun=True)
        if left <= self.MIN_ATTEMPT_S:
            return 0.0
        return min(float(base), float(left))

    def reserve_release(self) -> None:
        """进入**收尾阶段**：把留给收尾的余量放出来给这一次调用用。

        ⚠️ 没有这一步，`wall_reserve_s` 就是装饰字段（只声明、不兑现）：
        探索阶段把它扣下来，收尾阶段若还扣着，收尾调用照样拿不到时间。
        第二十五轮的实测动机正是这个 —— "留了 45s"和"45s 能被用上"是两件事。

        ★ 第九十六轮：同时把 `closing` 置真 —— 从这一刻起墙钟上限变成
        `wall_s + close_overrun_s`。**两个动作放在同一个方法里**是刻意的：
        「放出余量」与「允许超支」都是"进入收尾"这一个事件的后果。
        """
        self.wall_reserve_s = 0.0
        self.closing = True

    def complete(self, messages: Sequence[Message], **kw: Any) -> str:
        # ⚠️ 这里刻意用 `for` 而**不是** `while`：内核「只准有一个 while」
        #    是一条被 AST 钉死的不变量（契约 J6）。重试本来就是有界的，
        #    写成 for 既表达"最多 N+1 次"，又不让那条不变量被悄悄破掉。
        #    （把 J6 放宽去容纳一个 while = 尺子下移，那不是修法。）
        for attempt in range(self.retries + 1):
            # ① 墙钟闸：已尽 ⇒ 这是一等公民的预算事件，不是传输层吵闹。
            #    ★ 第九十六轮：收尾阶段改用 `wall_s + close_overrun_s`。
            #    ⛔ 非收尾分支**必须**仍然调 `ledger.check_wall()`（逐字不变）：
            #       换成 `self.ledger.elapsed() >= wall` 写法看着等价，
            #       但 AU1 钉的是同一个函数 —— 复制一份判断就是第二处实现。
            if self.closing:
                _dl = self._wall_deadline()
                if self.ledger.elapsed() >= _dl:
                    raise BudgetExhausted("wall_s", self.ledger.elapsed(), _dl)
            else:
                self.ledger.check_wall()
            # ② 裁剪：把本次尝试装进"剩余 − 收尾余量"。
            _to = self._attempt_timeout()
            if _to == 0.0:
                if attempt == 0:
                    self.wall_refused_first += 1
                else:
                    self.wall_refused_retry += 1
                raise BudgetExhausted("wall_s", self.ledger.elapsed(),
                                      self.ledger.budget.wall_s)
            # ②b 记账：这一发的上限里有多少是**宽限**给的。
            #     ⚠️ 必须与旧公式逐位比一遍，不能"收尾就计一次" ——
            #        那会把"宽限没用上"记成"用上了"（本轮最容易自欺的读数）。
            if self.closing and self.close_overrun_s > 0.0:
                _base = getattr(self.inner, "timeout_s", None)
                _left_old = self._left_s(honor_overrun=False)
                if _base is not None and _to != float("inf"):
                    _cap_old = (0.0 if _left_old <= self.MIN_ATTEMPT_S
                                else min(float(_base), _left_old))
                    if float(_to) > _cap_old:
                        self.wall_overrun_grants += 1
            self.ledger.spend_llm()
            try:
                if _to == float("inf"):
                    return self.inner.complete(messages, **kw)
                # ⚠️ 只有真的裁短了才计数：`_to == base` 说明这一发没被预算影响，
                #    把它也记进 `wall_shortened` 就让这个计数失去分辨率
                #    （"每发都算"= 没有信息）。
                _base = getattr(self.inner, "timeout_s", None)
                if _base is not None and float(_to) < float(_base):
                    self.wall_shortened += 1
                return self.inner.complete(messages, timeout_s=_to, **kw)
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
            finally:
                # ⑤ 这一发实际超出墙钟多少（只在收尾阶段记）。
                self._note_overrun()
        raise AssertionError("不可达：retries ≥ 0 保证循环至少执行一次并 return 或 raise")


#: 外部能力失败原因的**保留上限**（去重后）。
#: 为什么要有上限：一个反复失败的臂会写几十条同义句子，读数会被噪声塞满；
#: 而 6 条足以覆盖"超时 / 空返回 / 解析失败 / 未交付 / 非零退出"这几种病。
_ERR_REASON_CAP = 6


def tool_result_line(tool: str, ok: bool, intent: str = "", error: str = "",
                     *, cap: int = 160) -> str:
    """一行 `↳` 日志：成功说干了什么，**失败必须说为什么**。

    ⛔ 这条函数存在的理由是一次实测代价（第六十四/六十五轮）：两批真实曲线里
       `agent:code_agent` 共 **21 次 `ok=False`**，日志每一行只有 `ok=False` + 意图
       —— **零条原因**。为了知道 t04/t05 为什么死，我只能手工把题面喂给
       `external/code_agent.py` 复现两遍（20.8s / 23.9s）。
       **不可归因的失败等于没测**：它既不能指导修法，也不能被下一次读数推翻。

    ⚠️ `error` 为空时写 `(无错误信息)`，**不留空**：留空与"没失败"在 grep 结果里
       完全同形，而它们是两种病（工具不给理由 vs 工具没坏）。
    ⚠️ 成功时**不许**带原因字段：`ok=True 原因=…` 是自相矛盾的行，比不写更坏。
    ⚠️ 换行必须压成一行：日志被 `grep`/`awk` 按行切，一条折行的错误会把它下面
       那几行挤成"同一条记录"，读数就串了。
    """
    tail = f" {intent[:60]}" if intent else ""
    if ok:
        return f"      ↳ {tool} ok=True{tail}"
    why = " ".join((error or "").split())
    return f"      ↳ {tool} ok=False 原因={(why[:cap] or '(无错误信息)')}{tail}"


def external_summary(stats: dict) -> str:
    """`■` 汇总行里的外部能力段。空串 ⇒ 这一段不印。

    ⛔ 判据是**"实际调用过"**，不是**"配置了"**（第六十六轮修正）。
       旧条件是 `if ex.get("tool")` —— 只看配置项。于是第六十四/六十五轮两批真实
       日志里，规划器自己挑了 `agent:code_agent`、21 次全部 `ok=False`，
       而汇总行**一个字都没印**（`grep -c "外部["` = 0）：没配外部能力 ⇒ 整段消失
       ⇒ 21 次失败在证据里看起来像"没发生过"。
       这与「配了却没调用 = 接线断了」是同一件事的两面，两边都得能看见。

    ⚠️ 配置项与实际调用**不一致**时必须显式说出来：只印配置名会让读者以为
       失败来自那个被配置的能力，而它可能一次都没被调用。
    """
    ex = (stats or {}).get("external") or {}
    cfg = str(ex.get("tool") or "")
    seen = [str(x) for x in (ex.get("tools_seen") or [])]
    if not cfg and not seen:
        return ""
    if seen and cfg and cfg not in seen:
        label = f"配置={cfg} 实际={','.join(seen)}"
    else:
        label = cfg or ",".join(seen)
    line = (f" | 外部[{label}] 调={ex.get('calls', 0)}"
            f" 成={ex.get('ok', 0)} 败={ex.get('err', 0)}"
            f" 采用={ex.get('used_as_answer', 0)} {ex.get('chars', 0)}字")
    if seen and not cfg:
        line += " (规划器自选，未配置)"
    # ★ `份=k→n` 是**配置 vs 实际**：只印 `k` 会让人以为采样齐了。
    #   第二十三轮实测：`k=3` 但时间不够只生成 1 份，读数却写着 samples=3。
    _m = ex.get("meta") or {}
    if _m:
        line += (f" 份={_m.get('k')}→{len(_m.get('pass_counts') or [])}"
                 f" 用例={_m.get('tests')}")
        if _m.get("exec_skipped"):
            line += f" ⚠️筛选跳过({_m['exec_skipped']})"
        if _m.get("gen_stopped_early"):
            line += " 生成提前停"
    if int(ex.get("skipped_no_budget") or 0):
        line += f" ⛔额度不足未注入={ex['skipped_no_budget']}次"
    if ex.get("err"):
        _rs = ex.get("err_reasons") or []
        _head = (_rs[0] if _rs else str(ex.get("last_error") or ""))[:60]
        line += f" ⚠️{ex['err']}败/{len(_rs)}种: {_head}"
    return line


# ── 答案型外部能力：入参与额度的**唯一**构造处（第六十七轮）──────────────
#:
#: ⛔ 为什么必须收成一处：第六十六轮的 A/B 证明同一个能力**有两扇门**。
#:    · 门 B（`limits.external_answer_tool`，内核写死在第 1 步注入）
#:      传 `{"task": goal}` —— 题面**原文**；
#:    · 门 A（规划器自己在工具目录里挑中了它）传的是规划器**改写过的**话。
#:    同一道题、同一模型、同一把尺子：门 B `1.0000`，门 A `0.0000`。
#:    原因不是能力，是交接：`external/code_agent.py` 的契约是"从原文里抽
#:    『函数名必须是 X』，抽不到就拒绝"——**拒绝得对**。
#:    修法不是"在提示词里教规划器把原文抄过去"（那仍然两处实现、两个未来），
#:    而是让答案型外部能力的 **`task` 只有一个来源：题面原文**。
#:
#: ⚠️ `deadline_s` 与 `task` 必须同时归一化。旧门 A 不带 `deadline_s`，
#:    于是 `tools.py` 那条注入路径不会设 `AGENT_DEADLINE_S`、超时退回 spec 的
#:    `timeout_s`（上限，不是预算）⇒ 一个**可能跑穿整个预算**的外部进程。
#:    "谁把任务交过去"与"给它多少时间"是同一件事的两面，不能只修一半。
_ANSWER_CAP_S = 85.0
_ANSWER_MIN_S = 20.0


def answer_task(goal: str, deadline_s: float | None = None) -> dict:
    """答案型外部能力的**唯一**入参构造器。

    ⚠️ `deadline_s` 为 `None` 时**不写这个键**，而不是写 0：
       `tools.py` 的判据是 `if args.get("deadline_s") is not None`，
       而 `0.0` 是**假值但不是 None** —— 它会设出 `AGENT_DEADLINE_S=0.0`
       并让 `_to = max(5.0, min(_to, 15.0))`，即"给外部能力 5 秒"。
       那是最坏的一种"准时失败"。
    """
    out: dict = {"task": str(goal)}
    if deadline_s is not None:
        out["deadline_s"] = round(float(deadline_s), 1)
    return out


def answer_deadline(*, wall_s: float, elapsed: float, max_wall_s: float,
                    close_reserve_s: float) -> float | None:
    """本次外部能力能拿多少秒；`None` ⇒ **连注入都不要**。

    ⚠️ 上限随预算档缩放（不是写死 85）：1x 档恰好 85，与第二十四轮逐字相同；
       2x 档的外部件才真的是 2x 的大小，否则 2x 实验测的还是"1x 的外部能力"。
    ⚠️ 余量 `< _ANSWER_MIN_S` 时返回 `None`：给 5s 的外部能力只会**准时地失败**
       并白吃 5s，宁可让内核自己解（至少会交个答案）。
    """
    cap = _ANSWER_CAP_S * max(1.0, wall_s / max(1.0, max_wall_s))
    left = wall_s - elapsed
    dl = max(5.0, min(cap, left - close_reserve_s))
    return dl if dl >= _ANSWER_MIN_S else None


#: 停滞之后还要不要试着交付：一次专门的答案调用大约要多少秒。
#: 本机实测生成 ≈ 25 字符/秒，一道题最低可用的答案 ≈ 500 字 ⇒ ≈ 20s。
#: ⛔ 刻意**不复用** `_ANSWER_MIN_S`：那个数是**外部能力**的下限。
#:    两个数一旦共用一个常量，改一个会悄悄改掉另一件事的语义
#:    （本项目反复踩的"同一事实两处声明"的镜像：不同事实被迫一处声明）。
_STALL_ANSWER_MIN_S = 20.0


def stall_verdict(*, stalled: int, max_stall: int, wall_left: float,
                  min_answer_s: float = _STALL_ANSWER_MIN_S) -> str:
    """停滞到这个份上，下一步做什么 —— **唯一**的判定点。

    返回三选一：

    | 值 | 含义 |
    |---|---|
    | `"continue"` | 还没到阈值 ⇒ 换形态再问一次 |
    | `"deliver"`  | 到阈值，但**还剩额度够一次答案调用** ⇒ 停止探索、转向交付 |
    | `"give_up"`  | 到阈值且额度不够 ⇒ 如实交零 |

    ⚠️ 为什么"到阈值"不等于"交零"：`stalled` 的语义是**探索到此为止**
    （不再向规划器要动作），它和**交付**是两件事。第六十六轮的 A 臂实测：
    `■ stalled | steps=3`、`mean=0.0000`，而那一刻墙钟还剩 **85s** ——
    那 85s 里一次答案调用都没发生过。**手段停了，目的也跟着停了。**

    ⚠️ 这个判定必须在**一处**声明：它是"停滞之后还能不能救"的唯一答案。
       分散在分支里写两遍，两遍迟早不一致，而读数只在那一题掉下去时才知道。
    """
    if stalled < max(1, int(max_stall)):
        return "continue"
    return "deliver" if float(wall_left) >= max(0.0, float(min_answer_s)) else "give_up"


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
        critique_ledger: CritiqueLedger | None = None,
        score_ledger: ScoreLedger | None = None,
        caliber_sig: str = "",
        memory_mode: str = "raw",
    ) -> None:
        #: 本次运行**允许读到记忆的哪一层**：
        #:
        #:   "raw"      （默认）episodic 原文与全部语义记忆都进决策链 ——
        #:              第十五轮那个 Δ=+0.2000 就处在这个模式。
        #:   "abstract" 只读由 `mw.abstractor` 蒸馏出的**自然语言经验**，
        #:              episodic 原文与其余 facts 一律不进决策链。
        #:
        #: 为什么必须是内核参数、而不是脚本换一个空目录：
        #:   换目录会把**记忆状态**也换掉，于是"隔离"与"有用 muisti" 同时消失，
        #:   测得的是 2 个变量的和。同一个记忆状态 + 只换读哪一层
        #:   ⇒ **唯一变量**的真对照（第十五轮排序对照的正面推论）。
        self.memory_mode: str = str(memory_mode)
        #: 读层读数：语义记忆被检索到几条 / 其中属于抽象层几条。
        #: ⛔ 它**必须有消费者**（run.py 会念），否则又是一个"只声明不读"的装饰字段。
        self.memory_layer_view: dict[str, int] = {"recalled": 0, "abstract": 0}
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
        # 静默降级 + 假绿的同构体就在这里，所以修法与防线都钉死在这：
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

        register_core_tools(self.tools, memory=self.memory,
                            workspace=self.s.paths.workspace,
                            allow_note=self.s.limits.allow_note)
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
        #: 判据与自检读数（第九十一轮收进 `_reset_verify` 一处）。
        #: ⚠️ 为什么在 `__init__` 里就建：`_close_out` 从第九十一轮起**在交付前
        #:    跑一次闸**，而它是可单独调用的单元（AS20 正是这么测它的语义的）⇒
        #:    只把 `_checks`/`verify_stats` 建在 `run()` 里，直接构造的实例一调
        #:    `_close_out` 就 AttributeError —— 收尾路径自己炸掉就等于交付零。
        self._reset_verify("")
        # 收尾兜底的读数（第二十二轮）：在 `__init__` 里就要存在 ——
        # `_close_out` 是一个可以**单独调用**的单元（契约 AS20 正是这么测它的语义的），
        # 只把状态建在 `run()` 里会让"没跑过 run 的实例"一调就 AttributeError。
        self._reset_closeout()
        #: 外部能力读数（第二十三轮，路线①）。与收尾读数同宗理由建在 `__init__`：
        #: 单轮读数也要能被单独构造的实例读到，否则一处 AttributeError 就会
        #: 把整条汇总行带走（AS20 那条教训）。
        self._reset_external()
        #: ★题边界清掉的**上一题残留**工作记忆条数（第三十六轮 §17-7h）。
        #: 与上面两条同宗理由建在 `__init__`：`_finish` 可以被单独调用（AS20 的教训），
        #: 只把它建在 `run()` 里会让"没跑过 run 的实例"一调就 AttributeError。
        #: **有消费者**：`_finish` 的 stats + `■` 汇总行（零值不印）。
        self.working_cross_task: int = 0
        #: ★每次 `_state()` 的产物（第三十六轮 §17-7h）。**只读证据出口**：
        #: 内核不读它做决策；runner 在任务结束后取走落盘，由外部探针审计。
        #: 建在 `__init__` 的理由同上面两条（单独调用 `_state` 的探针也会用到）。
        self.state_log: list[tuple[int, str]] = []
        self.planner = Planner(self.llm, self.tools,
                               max_tokens=self.s.limits.llm_max_tokens)
        self.wm = WorldModel(self.memory, self.llm,
                             allow_learn=self.s.limits.allow_learn)

        # ── 三个"调制层"（不是相位）────────────────────────────────────
        # 相位是环上的必经点，缺了就断流；调制层只改变**怎么走**。
        # 这个区分是本轮对"模块是否必要"的答案，写在这里是因为代码结构
        # 必须与那个答案一致 —— 否则文档说一套、依赖图是另一套。
        #
        #: 价值层：方向的否决与偏序。**不做评分**（`values.py` 首屏写死）。
        self.values = Values()
        #: 通量计：环上每条边真的过去了多少。它是**尺子**，不参与决策。
        self.flow = FlowMeter()
        # ── 自我模型：**起跑时**从跨会话账本冻结一次历史证据 ──────────
        #
        # 之前这里写的是 `SelfModel()` —— 空着起跑，于是自我模型的判据
        # 只能退到代理量（被咨询过几次）。§16.7 第三条判它"判据不成立"。
        #
        # ⚠️ 为什么是**起跑冻结**而不是"每次用的时候现读"：
        #    若当前轮里能读到本轮新写入的分数，第 6 题就拿到了第 1 题的成绩，
        #    `repeat` 立刻从"同一条件重复 N 次"变成"顺次影响 N 次"，
        #    量到的是顺序效应而不是能力 —— 这正是 §16.7 说的
        #    "轮内使用会破坏测量一致性"。
        #    ⇒ 快照在**构造时**取一次，之后本轮无论账本怎么变都不再更新。
        #
        # ⚠️ 为什么 `Core` 里**没有**写账本的调用点：见 `mw/ledger.py` 模块头。
        #    内核给自己记分的那一天，就是这套测量体系失效的那一天。
        #: 当前口径签名（由外部锚层传入；空串 ⇒ 一条历史都不采纳）
        self.caliber_sig: str = str(caliber_sig or "")
        self.score_ledger = score_ledger
        self._ledger_snapshot: tuple[dict, ...] = (
            score_ledger.snapshot(self.caliber_sig) if score_ledger is not None else ())
        self.selfmodel = SelfModel(load_evidence(list(self._ledger_snapshot)))
        #: 外部评审账本：他者的过程性反馈，**只读输入**。
        #: None = 本次运行不启用（默认）—— 见 `run(task_id=...)` 的说明。
        self.critique_ledger = critique_ledger

    # ------------------------------------------------------------------
    def run(self, goal: str, *, budget_factor: float = 1.0, tag: str | None = None,
            family: str | None = None, task_id: str | None = None,
            scorer_sha: str = "", suite_sha: str = "",
            features: frozenset[str] | set[str] | None = None) -> Outcome:
        """跑一个任务。

        `family` 是**能力族标签**（如 `numeric_logic`），只给自我模型做资源建议用。
        ⚠️ 刻意**不**从 goal 里猜族：一旦自我模型读得到题目内容，
        它就会在"看题之后"给自己分配资源，那就退化成自评分的入口了。
        调用方不给 ⇒ 传 None ⇒ 自我模型返回默认值并明说"样本不足"。

        `features` 是**题面结构特征**（由 `mw.features` 从题面算出），
        用来检索历史外部奖励（§17-5）。⛔ 内核**绝不自己算**它 ——
        一旦内核能从目标文本里给自己造特征，它就能给自己挑"看起来像"的先例，
        检索这件事就退化成又一次自证。来源必须是外部锚层。
        调用方不给 ⇒ 奖励通道这一路不参与（另一侧照常工作），不静默降级。

        `task_id` / `scorer_sha` / `suite_sha` 是外部评审的**口径签名三件套**：
        · `task_id` 用于挡掉"当前题自己的评审"（防自评，第一道闸）；
        · 两个 sha 用于挡掉"别的尺子给的评审"（防跨口径）。
        三者任一缺失 ⇒ 评审一律不进上下文，**而不是放宽条件放行**。
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
        # 混在一起会出现"这一轮 0 次否决"而那个 0 是上一轮留下的。
        self.flow = FlowMeter()
        self.values = Values()
        # 第二十轮：按需回看是**单轮**读数（预算也是单轮的）。
        #: 因预算不足被丢掉的块/条目数（单轮）。**有消费者**：进 stats。
        #: 没有这个计数，"预算吃紧"就是静默的 —— 而静默的降级正是最常见的病。
        self.state_trimmed = 0
        # ★单轮读数：每次 `_state()` 的产物属于**这一轮**（与预算同源）。
        # 不清空的话，第 k 题的 dump 里会混进第 k−1 题的 state ——
        # 那正是本轮在修的同一类错误（跨题残留），不能在新字段上重演一遍。
        self.state_log = []
        self.expand_stats: dict = {
            "calls": 0, "chars": 0, "errors": 0,
            "by_trigger": {"mismatch": 0, "fail_tail": 0},
            "skipped": {"mode": 0, "budget": 0, "no_entry": 0},
        }

        # ── 校验（第二十一轮）：判据从**目标文本**派生，一次，之后不变 ──────
        # ⛔ 必须在这里派生、而不是每次 finish 时现算：
        #    现算就给了"答案反过来影响判据"的入口 —— 那正是自评分的形态。
        #    判据只认目标，与答案无关；`V1_VERIFY_RETRIES=0` ⇒ 判据为空 ⇒ 整条闸不存在。
        # ⚠️ `verify_static`（第八十轮）只是**多派生一条**判据，不是一个新分叉：
        #    0=不派生（默认，与之前逐位相同）/1=报名字/2=盲报（等长安慰剂臂）。
        # ⚠️ 第九十一轮：判据与读数收进 `_reset_verify()` **一处**声明。
        #    `_close_out` 现在要在交付前跑闸，而它可以被单独调用（AS20）⇒
        #    属性只建在 `run()` 里的话，收尾路径会 AttributeError，
        #    而那条路径存在的唯一目的就是"别交付零"。
        self._reset_verify(goal)
        # ⚠️ `_salvage` / `_unverified` 也挪进了 `_reset_verify()`（第九十一轮）：
        #    `_close_out` 本来就读 `self._salvage`（`overrode_rejected` 的比较基准），
        #    而它只建在 `run()` 里 ⇒ **直接构造的实例一调收尾就 AttributeError**。
        #    这个洞是第九十一轮写 AZ92 时当场撞出来的（`'Core' object has no
        #    attribute '_salvage'`），不是推理出来的：AS20 一直绿，是因为它自己
        #    先手工赋了 `_salvage` —— 那条断言顺带把洞挡住了。
        # ── 收尾兜底（第二十二轮）：把一条**一直在跑、却只有日志没有读数**的路接上
        # ⛔ 为什么必须结构化：第二十一轮的整条位移都来自这条路（`finish` 被打回 ⇒
        #    重规划 ⇒ 价值层否决 ⇒ 收尾余量 ⇒ `_close_out` ⇒ 交付），而当时
        #    全项目对它的记录**只有两句中文日志**。日志能证明"走到过"，不能回答
        #    "走到过几次、几次真的改了交付物"。而后者才是"它有没有用"的第一个问题。
        self._reset_closeout()
        # 外部能力读数是**单轮**的（与预算同源）：跨轮累计会让"这一轮 0 次调用"
        # 显示成上一轮的数字（读数混了时间维度）。
        self._reset_external()

        # 预算/账本变了 ⇒ planner 与 wm 要看到同一个 LLM 计数口径
        counted = _LedgeredLLM(self.llm, self.ledger,
                               retries=self.s.limits.llm_retries,
                               backoff_s=self.s.limits.llm_retry_backoff_s,
                               # 重试链不许把收尾预算吃掉 —— 否则"准时失败"
                               # 会退化成"没答案"（§16.24 ③）。
                               wall_reserve_s=self.s.limits.close_reserve_s,
                               # 收尾那一次调用允许超出墙钟的有界宽限（§63）。
                               # ⛔ 不给 0 以外的值就等于退回旧版（反证组就是这么跑的）。
                               close_overrun_s=self.s.limits.close_overrun_s)
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
        # ── 题边界：工作记忆是**本题的**草稿纸，不是上一题的 ──────────────
        # ★ 第三十六轮（§17-7h 装置修正⑭①）。三条实测支撑，缺一条都不该改内核：
        #   · `MemoryStore.working` 是**进程级** deque，而 bench 复用**同一个** Core
        #     连跑多题 ⇒ 上一题的工具输出留在下一题的 `[工作记忆]` 块里；
        #   · `recall` 工具成功时返回的是**全部** facts 的拼接（实测 389 字），
        #     `_state()` 只取每条输出的**尾 200 字** ⇒ 漏出去的是"尾巴那段"，
        #     取决于上一题恰好返回了什么；
        #   · 实测（`probe_g7n_leak.py`，零模型调用）：第 4 题的 state 里出现
        #     **非本题**惯例 2/3 条，而 R1 只查 `recall()` **看不见**这条路。
        # ⇒ 改"邻域长度"会同时改变"漏出去的是哪一段" = 那条轴从来不是单变量。
        # ⛔ 清空点必须在**写侧**（这里）。改在 `_state()` 里过滤会让泄漏**隐身**
        #    （计数恒 0、泄漏照旧）—— 那正是"修完等于没修"的标准形态。
        # ⚠️ 返回值必须被消费：`stats["working_cross_task"]` 是"上一题残留了几条"
        #    的唯一读数，零值也要进 stats（否则"没残留"与"没接线"一样）。
        self.working_cross_task = self.memory.reset_working()
        if self.working_cross_task:
            self.log(f"  🧹 题边界：清掉上一题残留的工作记忆"
                     f" {self.working_cross_task} 条")
        self.memory.record("goal", goal, budget_tag=budget.tag)

        history: list[str] = []
        last_error = ""
        consecutive_failures = 0
        answer = ""
        #: 已**尝试过**的动作签名（含被 guard 拦下的）—— 跨步骤的"不许原地打转"。
        #: 为什么连被拦的也要记：重复提出一个注定被拦的动作，同样不产生新信息。
        attempted_sigs: set[str] = set()
        repeats_skipped = 0
        #: 全程被 guard / 价值层拦下的候选数（第九十九轮 §66）。
        #: ⚠️ 与 `repeats_skipped` 分工不同：那个数"这一步是旧的"，
        #:    这个数"这一步被拒绝了"。两者**都**意味着"没能执行"，
        #:    但只有前者说明"模型在原地打转" —— 判据因此以 `repeats >= 1` 为前置。
        #: ⛔ 不含"额度不足"（`skipped_now`）：那是**没试**，不是被拒。
        blocked_skipped = 0
        stalled = 0
        #: 「本轮全灭」之后要**换形态**再问的载荷：被内核丢掉的那几个动作的原文行。
        #: 只活一轮（喂给下一次提问就清空）—— 不留着，否则它会变成又一段
        #: 永久提示词噪声，而噪声里没有一个字是"刚刚发生的事"。
        #: ⛔ 为什么需要它：`blocked` 是一段静态清单，要求模型"别重复它自己将要说的话"
        #:    是一种**自比对**（实测：`attr_s66` A 臂连续两轮照提不误）。
        #:    换形态不是把话说重，是给模型一个它此前拿不到的信息：哪些工具还没试过。
        reseek_lines: list[str] = []
        #: 本轮"因为额度不足而根本没试"的候选数。它**不是失败**（§17-6 同宗）：
        #: "没试"与"试了失败"必须分开记账，否则读数会把预算不足算成能力不足。
        #: ⚠️ 与 `external_stats["skipped_no_budget"]` 分工不同：那个是全局台账，
        #:    这个是**本轮**的判定依据（"一个都没试"⇒ 该收尾，不该继续问）。
        skipped_now = 0

        def _done(status: str, ans: str, why: str) -> Outcome:
            """所有出口都从这里走 —— 保证"停滞/重复"这类计数**任何状态都带得走**。
            两处各自拼 stats 就会漂移。
            """
            return self._finish(status, ans, why, history, extra={
                "repeats_skipped": repeats_skipped,
                "stalled": stalled,
                # ★ 第九十九轮 §66：零值也在 —— "一次没遇到被拦"与"配了没生效"
                #   在读数上必须分得开（同 `wall_overrun_grants` 的理由）。
                "blocked_skipped": blocked_skipped,
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
                state = self._state(goal, history, it,
                                    task_id=task_id, scorer_sha=scorer_sha,
                                    suite_sha=suite_sha)

                # ② ACT：快通道还是深通道。
                #    同时受**候选预算**约束 —— 否则 candidates 这个维度就是装饰：
                #    声明了上限却从不消费，正是"字段无消费者"的同类病灶。
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
                        rejected=reseek_lines,
                        # ⚠️ 重规划**更**需要易错点：它正是"刚栽了"的那一次。
                        #    原来这里不传 ⇒ guidance 只在首次尝试时有效。
                        guidance=self.wm.guidance(
                            action=goal, features=features, task_id=task_id),
                        # ★ 失败之后**更**需要状态，不是更不需要 ——
                        #   回看取回的报错原文就拼在状态里（第二十轮）。
                        state=state)
                else:
                    plan = self.planner.propose(
                        goal=goal, state=state, history=history, n=n,
                        guidance=self.wm.guidance(
                            action=goal, features=features, task_id=task_id),
                        blocked=sorted(attempted_sigs),
                        rejected=reseek_lines)
                # 「刚刚发生的事」只喂一次：下一次提问若还是全灭，会重新填。
                # ⛔ 不清空就等于把"上一轮被拒"变成永久背景，那正是 `blocked`
                #    已经犯过的错（静态清单被无视），只是换个位置再犯一遍。
                reseek_lines = []
                skipped_now = 0
                blocked_now = 0
                if plan.degraded:
                    self.log(f"  ⚠️ {plan.note}")
                if not plan:
                    consecutive_failures += 1
                    last_error = "规划器未产出可用步骤"
                    history.append(f"[step {it}] 规划失败：无可用候选")
                    continue

                # 探索宽度**真实生效**：规划器可能多给了候选，这里按 n 截断。
                # （只声明上限却不截断 = 字段无消费者。）
                cands = plan.steps[:n]
                # ── 外部能力作为**第一来源**（第二十三轮，路线①）──────────────
                # ⛔ 为什么写死在这里、不交给规划器选：第十三轮已经证实过 ——
                #    交给模型选，机制会恒 0 次触发，而读数只会说"没被选到"，
                #    读起来像"外部能力没用"。位置必须与 `_gate_finish` 同样是**写死的**。
                # ⚠️ 只注入第 1 步：外部能力是"另一个答案来源"，不是"每一步都问一遍"。
                #    后面几步仍由规划器决定（它可以改写、可以收尾）。
                _ext = self.s.limits.external_answer_tool
                if _ext and it == 1 and self.tools.get(_ext) is not None:
                    # ★ 把**剩余墙钟**一起交给外部能力：它必须知道现在几点了。
                    #   `min(85, 剩余 − 收尾余量)`：85 是"外部能力最贵值多少"的上限，
                    #   剩下的时间要留给自检、重规划与交付 —— 否则外部能力会
                    #   把预算吃光并以"没答案"收场（k04 实测）。
                    # ⚠️ 上限从 60 提到 85 是**实测逼出来的**：单次生成 ≈ 25 字符/秒，
                    #   一道题输出 400~800 字 ⇒ 单次 16~32s；k=2 也要 2×32+8+2 ≈ 74s。
                    #   60s 的上限会让 agent **恒**在 `gen_stopped_early` 或
                    #   `tests=0` 状态交付 ⇒ 执行筛选从来跑不到。
                    #   85 仍守得住 45s 收尾余量：it==1 时 wall_left≈117 ⇒ 117−45=72。
                    # ★ 第二十四轮：上限**随预算档缩放**，不再写死 85。
                    #   写死等于"2x 档的外部件仍然是 1x 的大小" —— 那样 2x 实验
                    #   测的还是"1x 下的外部能力"，只不过内核自己多想了想。
                    #   缩放基准取 `wall_s / max_wall_s`（1x ⇒ 恰好 85，与上一轮逐字相同）。
                    # ★ 第六十七轮：额度与入参都挪进 `answer_deadline()` /
                    #   `answer_task()` —— 与下面"执行处"共用同一对函数，
                    #   否则这里改一个数、那里没改，就是又一次两处实现。
                    _dl = answer_deadline(
                        wall_s=budget.wall_s, elapsed=self.ledger.elapsed(),
                        max_wall_s=self.s.limits.max_wall_s,
                        close_reserve_s=self.s.limits.close_reserve_s)
                    # ⛔ 额度不够时**根本不注入**：给 5s 的外部能力只会
                    #    "准时地失败"并且白吃 5s。宁可让内核自己解（至少会交个答案）。
                    if _dl is not None:
                        cands = [Step("整题交给外部能力", _ext,
                                      answer_task(goal, _dl))] + list(cands)
                    else:
                        self.external_stats["skipped_no_budget"] = (
                            self.external_stats.get("skipped_no_budget", 0) + 1)
                self.ledger.spend_candidate(len(cands))
                self.log(f"  [{it}] 通道={'深' if complex_task else '快'} 候选={len(cands)}")
                # 通量：推理交了 N 个想法出去（下游是否真的做，看 `reason>act` 的消费）
                self.flow.emit(it, "reason>act", len(cands), sum(len(c.as_line()) for c in cands))

                # ④⑤⑥ 逐候选过闸 → 预演 → 执行
                executed = False
                repeats = 0
                for cand in cands:
                    spec = self.tools.get(cand.tool)

                    # ── 同一扇门（第六十七轮）──────────────────────────────
                    # 上面那条"内核注入"是门 B；规划器**自己**从工具目录里挑中
                    # 答案型外部能力，是门 A。两扇门的选择可以不同，
                    # **但交过去的东西必须逐字相同** —— 否则同一道题会因为
                    # "谁去交"而拿不同分数（§29.2 实测：1.0000 vs 0.0000）。
                    # ⇒ 在执行点无条件覆写：`task` = 题面原文，`deadline_s` = 现算。
                    # ⚠️ 覆写必须发生在 `action_signature()` **之前**：签名是
                    #   "已试过的动作"的判据，若它算的是改写文本，同一件事会被
                    #   当成两件不同的事反复执行（那正是停滞闸没拦住 A 臂的原因之一）。
                    if spec is not None and spec.produces_answer:
                        _dl2 = answer_deadline(
                            wall_s=budget.wall_s, elapsed=self.ledger.elapsed(),
                            max_wall_s=self.s.limits.max_wall_s,
                            close_reserve_s=self.s.limits.close_reserve_s)
                        if _dl2 is None:
                            # 额度不够 ≠ 失败。记一笔就跳过，让后面的候选照跑；
                            # ⛔ 绝不能"不带 deadline 照发" —— 那会退回 spec 的
                            #    `timeout_s`（上限，不是预算），跑穿整个预算。
                            skipped_now += 1
                            self.external_stats["skipped_no_budget"] = (
                                self.external_stats.get("skipped_no_budget", 0) + 1)
                            self.log(f"      ⛔ {cand.tool} 剩余额度 < "
                                     f"{_ANSWER_MIN_S:g}s ⇒ 不注入（不是失败，是没试）")
                            continue
                        cand.args = answer_task(goal, _dl2)
                    sig = action_signature(cand.tool, cand.args)

                    # ── 停滞闸：这个动作已经尝试过了 ────────────────────────
                    # 实测背景：内核曾把同一 `recall` 重派 5 次、每次 ok=True，
                    # 靠 `consecutive_failures` 完全拦不住（它只数失败），
                    # 最后烧光 120s 才以 budget_exhausted 收场 ——
                    # 这就是"同一假设无限重派"的同构体。
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
                            blocked_now += 1
                            blocked_skipped += 1
                            continue
                    # 工具滥用
                    if cand.tool == "run_shell":
                        sv = self.guard.check_shell(str(cand.args.get("cmd", "")))
                        if not sv.allow:
                            self.log(f"      ✋ {sv.as_line()}")
                            blocked_now += 1
                            blocked_skipped += 1
                            continue
                    # 记忆投毒
                    if cand.tool == "note":
                        mv = self.guard.check_memory_write(
                            source=str(cand.args.get("source", "agent")),
                            confidence=float(cand.args.get("confidence", 0.6)))
                        if not mv.allow:
                            self.log(f"      ✋ {mv.as_line()}")
                            blocked_now += 1
                            blocked_skipped += 1
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
                        blocked_now += 1
                        blocked_skipped += 1
                        continue

                    # 预演（只有深通道才付这个成本 —— 这就是 ACT 的实际意义）
                    pred = None
                    if complex_task:
                        pred = self.wm.predict(state=state, action=cand.as_line(), use_llm=True)
                        if not pred.trustworthy:
                            self.wm.suppressed += 1
                            self.log(f"      🔮 预演置信度 {pred.confidence:.2f} 过低 ⇒ 不进决策链")

                    result = self.tools.dispatch(cand.tool, cand.args)
                    self.log(tool_result_line(cand.tool, result.ok,
                                              cand.intent, result.error))

                    # ── ⑦ 校验闸（第二十一轮）：`finish` 的唯一出口 ─────────
                    # 位置是**写死**的，不交给规划器选：交给它 = 它永远不选自检
                    # ⇒ 机制恒 0 次而读数上只显示"没触发"（`recall_trigger` 那条同宗）。
                    # ⚠️ 被自检打回时**不能立刻丢掉答案**：答案原文进 `_salvage`，
                    #    万一重写失败（停滞/预算耗尽/熔断），最终交付退回它。
                    #    否则一个本来能拿 0.2 分的答案会被装置变成 0 分。
                    # ★ 第二十三轮：`produces_answer` 的外部能力走**同一道闸**。
                    #   外部能力换的是"答案从哪来"，不是"判据松不松"。
                    _produces = cand.tool == "finish" or bool(
                        spec is not None and spec.produces_answer)
                    if spec is not None and spec.produces_answer:
                        # 外部能力的读数：**尝试**次数与成功次数分开记 ——
                        # 只记成功会让"它一直在失败"看起来像"它没被调用"。
                        _es = self.external_stats
                        _es["calls"] += 1
                        # ⚠️ 记在**调用**处而不是配置处：这一行回答的是
                        #    "这次到底是谁在跑"，而 `tool` 只回答"配了谁"。
                        if cand.tool not in _es["tools_seen"]:
                            _es["tools_seen"].append(cand.tool)
                        if result.ok:
                            _es["ok"] += 1
                            _es["chars"] += len(result.output or "")
                            _es["meta"] = external_last_meta(cand.tool)
                        else:
                            _es["err"] += 1
                            _es["last_error"] = (result.error or "")[:200]
                            # 原文进证据（去重、限量）：一条都留不下来 = 不可归因。
                            # ⚠️ **不再**加 `<tool>: ` 前缀：`tools.dispatch` 的三条失败
                            #   路径已经全部以工具名开头（`{name} 缺参数` / `{name} 执行异常` /
                            #   `未知工具 {name!r}`）。实测第一版加上前缀后变成
                            #   `agent:code_agent: agent:code_agent 执行异常 …` —— 同一个名字
                            #   印两遍，而 `err_reasons` 是按 240 字截断的字段，重复等于
                            #   挤掉真正有用的后半句。
                            _why = (result.error or "（无错误信息）")[:240]
                            _rs = _es["err_reasons"]
                            if _why not in _rs and len(_rs) < _ERR_REASON_CAP:
                                _rs.append(_why)
                    if _produces and result.ok and self._checks:
                        result = self._gate_finish(result)

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
                    mm = None
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
                    # ★ 第二十轮：**先整段存下来，再决定下一步能看见多少**。
                    #   在此之前这里只有 `stored = full[:300]`，于是 5000 字的观测
                    #   有 94% 当场消失且**不可找回**（§17-9 实测覆盖率 0.06）。
                    #   现在那 94% 进了 `blobs`，工作记忆里留一个**可寻址的索引**：
                    #     `[b#7 5025字 sha=4baa3718fdb3] <前 300 字>`
                    #   ⇒ 宽度**一点没变**（300 仍是 300），变的是"要得回来"。
                    handle = self.memory.capture(full, kind="observation")
                    index = f"[{handle.ref} {handle.chars}字 sha={handle.sha}] "
                    stored = (index + full[:WORKING_CHAR_CAP])[:WORKING_CHAR_CAP + 80]
                    self.flow.emit(it, "perceive>memory", 1, len(full))
                    self.flow.take("perceive>memory", 1, len(stored))
                    self.memory.think(stored)

                    # ── 按需回看：只在**意外**发生时要回原文（第二十轮）──────
                    # ⛔ 触发条件必须写死且**可证伪**，绝不交给模型自己决定要不要看：
                    #    交给模型 = 它会自信地不看，然后机制恒为 0 次而没人发现。
                    #   T1 失配：世界模型的预演被推翻 —— 意外正说明"没预料到的细节在里面"。
                    #   T2 失败且错误被截断：报错原文长过工作记忆宽度 ⇒ 决定性信息可能已经丢了。
                    #   ⚠️ 两条都只在"被截断"时才有意义，否则取回的就是已经在眼前的字。
                    recall_where = recall_trigger(
                        ok=result.ok, error=result.error, mismatched=mm is not None)
                    if recall_where:
                        self._recall_for(it, handle, where=recall_where,
                                         trigger=("fail_tail" if recall_where == "tail"
                                                  else "mismatch"))

                    if not result.ok:
                        consecutive_failures += 1
                        last_error = result.error
                        # ★ 第二十七轮（§17-6 6a）：错误原文必须进 **content** ——
                        #   蒸馏器只读 content，meta 它看不见。实测（离线探针）：
                        #   打回明明落了 failure 行，content 却只有 `finish({...}) — v0`
                        #   这种动作行 ⇒ 蒸馏器看到的是零信息行，输出 NONE 是必然。
                        #   "知识原料断供"的第一处断点就在这一列。
                        self.memory.record(
                            "failure",
                            f"{cand.as_line()} — {result.error[:200]}",
                            error=result.error[:300])
                        executed = True
                        break
                    consecutive_failures = 0
                    last_error = ""

                    if _produces:
                        # 两个来源（内核自己的 finish / 外部能力的答案）走**同一条**
                        # 交付路径：同一道自检闸、同一个 `done_unverified` 语义。
                        # ⛔ 外部能力**不许**有"免检通道" —— 那就成了"换个来源就换把尺子"。
                        answer = result.output
                        if spec is not None and spec.produces_answer:
                            self.external_stats["used_as_answer"] += 1
                        if self._unverified:
                            # 自检反复不过 ⇒ 交付内容是真的，但**没过闸**。
                            # 状态必须说出来：报 done 就等于把"没检查过"说成"检查过了"。
                            self.log(f"  ⚠️ 收尾（未通过自检）: {answer[:100]}")
                            return _done("done_unverified", answer, self._unverified)
                        self.log(f"  ✅ 收尾: {answer[:120]}")
                        return _done("done", answer, "")

                    self.memory.record("outcome", result.as_observation()[:1000],
                                       tool=cand.tool, intent=cand.intent[:200])
                    executed = True
                    break

                if not executed:
                    if skipped_now and skipped_now == len(cands):
                        # 一个候选都没跑起来，原因**不是**"失败"而是"没额度"。
                        # ⛔ 记成失败会做两件坏事：①把预算不足算进能力读数；
                        #   ②连续 3 轮就撞级联熔断 —— 第六十八轮的基线真跑正是这样：
                        #   `⛔ 全部候选被拦 (3 次连续失败)` ⇒ `cascade` ⇒ 交付为空，
                        #   而事实是"重规划一次就花掉 29s，把剩下的额度吃光了"。
                        #   级联熔断的语义是"连续做错"，这里一次都没做。
                        # ⇒ 不计失败、不假装有进展：如实说"没试"，然后继续
                        #   （下一轮的收尾余量判断会接管；它不看这一步的记账）。
                        self.log(f"  ⛔ 本轮 {skipped_now} 个候选都因额度不足未执行"
                                 f"（不是失败，是没试）")
                        history.append(f"[step {it}] 全部候选因额度不足未执行（不是失败）")
                        continue
                    # ── 停滞：这一步"一个候选都没能执行"──────────────────────
                    # ★ 第九十九轮（§65/§66）：判据从**全等**放宽成
                    #   「有重复 且 重复 + 被拦 == 候选数」，但那要**显式开闸**
                    #   （`V1_STALL_ON_BLOCKED=1`）。默认 0 ⇒ 与旧版逐位相同。
                    #   动机（309 份任务日志 / 2108 步 / 81 次重复候选）：
                    #     全等 48 步，**宽度全是 1** ⇒ 触发时那一步根本没有
                    #       第二个候选可换（换形态正是要靠它）；
                    #     混合 33 步，**宽度全是 2** ⇒ 掉进下面的 `else`，
                    #       把"重复"记成"连续失败"，由熔断兜底（出口是交空）。
                    #   ⇒ 机制没错，**触发条件与用途错位**。
                    # ⛔ 前置 `repeats` 不是形式主义：`repeats == 0` 的全拦是
                    #   "这些动作都不许做"，一个重复都没有。把它记成停滞会吞掉
                    #   「全部候选被拦」这条**独立**的真信号 —— 两者出口不同
                    #   （`_close_out` vs 熔断），修法也不同，必须分得开。
                    _blocked_eff = (blocked_now
                                    if self.s.limits.stall_on_blocked >= 1 else 0)
                    if repeats and (repeats + _blocked_eff) == len(cands):
                        # 本轮**全部**候选都是已试过的动作 ⇒ 停滞。
                        # 不停在原地继续烧预算，而是计数并按阈值收尾 ——
                        # 如实报告"没有进展"，不假装在做事情。
                        stalled += 1
                        self.log(f"  🧊 本轮 {repeats} 个候选全部是已试过的动作"
                                 # 只在真有被拦的候选参与判据时才多说这一句：
                                 # 关闸时日志**逐字**与旧版相同（对照臂要能逐行 diff）。
                                 + (f"（另有 {blocked_now} 个被拦）"
                                    if _blocked_eff else "")
                                 + f" ⇒ 停滞 {stalled}/{self.s.limits.max_stall}")
                        # ★ 第六十九轮（§31.7）：**停滞 ≠ 交零**。
                        #   旧代码到这里就 `return _done("stalled", "", …)`，
                        #   而第六十六轮的 A 臂实测那一刻还剩 85s —— 那 85s
                        #   一次答案调用都没发生过。探索是手段、交付是目的，
                        #   手段停了不该把目的也停掉（与 `_closing_reserve_hit`
                        #   那次事故同一个形状：手段吃掉了目的）。
                        #   判定只在 `stall_verdict()` 一处声明。
                        _sv = stall_verdict(
                            stalled=stalled, max_stall=self.s.limits.max_stall,
                            wall_left=(budget.wall_s - self.ledger.elapsed()))
                        if _sv == "deliver":
                            return self._close_out(
                                goal, history, _done, reason="stalled",
                                detail=f"连续 {stalled} 轮没有新动作，"
                                       f"已跳过 {repeats_skipped} 次重复")
                        if _sv == "give_up":
                            return _done(
                                "stalled", "",
                                f"拒绝原地打转：连续 {stalled} 轮没有任何新动作"
                                f"（已跳过 {repeats_skipped} 次重复；"
                                f"剩余额度不足一次答案调用）")
                        # ★ 第六十八轮：**换形态再问**，而不是把同一个问题再发一遍。
                        #   同一个问题 + 同一个模型（T=0）得到同一个答案，这不是
                        #   猜想，是第六十六轮的实测（A 臂连续两轮只给同一个动作）。
                        #   要它变，就得让**输入**变：把刚被丢掉的动作**原文**摆出来，
                        #   再告诉它哪些工具一次都没试过（它此前拿不到这个信息）。
                        reseek_lines = [c.as_line() for c in cands]
                        continue
                    consecutive_failures += 1
                    last_error = "全部候选均被 guard 拦截"
                    history.append(f"[step {it}] 全部候选被拦")
                    self.log(f"  ⛔ 全部候选被拦 ({consecutive_failures} 次连续失败)")

        except BudgetExhausted as e:
            # 预算是硬约束：如实报告"没做完"，不假装完成
            self.log(f"  ⏳ {e}")
            # ★ 第二十五轮：墙钟在**调用内部**被重试链挡住时（`spent < limit`
            #   说明预算并没有真用尽，只是被 `wall_reserve_s` 保护着），
            #   余量其实还在 —— 既然留了就必须用掉，否则那份余量是装饰字段。
            #   ⛔ 判据刻意用「剩余 > 一次尝试」而**不是** `_closing_reserve_hit()`：
            #      后者的阈值是 `wall_left <= reserve`，会在 `(reserve, reserve+5]`
            #      这段区间返回 False ⇒ 明明还有 47s 却被判成"没余量"。
            #      实测算过：`reserve=45`、`left=2` 时就是这一格。
            #   只对 `wall_s` 生效：steps / llm_calls 用尽时没有"时间还在"这回事。
            if getattr(e, "kind", "") == "wall_s":
                _left = self.ledger.budget.wall_s - self.ledger.elapsed()
                if _left > _LedgeredLLM.MIN_ATTEMPT_S:
                    self._reserve_reason = "wall_retry"
                    self.log(f"  ⏱ 墙钟被重试链挡住，但还剩 {_left:.0f}s ⇒ 转向收尾")
                    return self._close_out(goal, history, _done)
            return _done("budget_exhausted", "", str(e))
        except Exception as e:  # noqa: BLE001
            self.log(f"  💥 内核异常 {type(e).__name__}: {e}")
            return _done("error", "", f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------------
    def _admissible_critiques(self, *, task_id: str | None, scorer_sha: str,
                              suite_sha: str) -> list[Critique]:
        """外部评审进上下文前的三道闸。**缺任一签名 ⇒ 一律不放行。**

        不放行的理由是结构性的，不是保守：
        · 放行当前题的评审 = 内核照着自己的答案改答案（自评分）；
        · 放行别的尺子的评审 = 两把尺子的理由混用，教训变成噪声。
        """
        led = self.critique_ledger
        if led is None:
            return []
        if not task_id or not scorer_sha or not suite_sha:
            # 口径不全 ⇒ 静默返回空是**不行的**（那是静默降级）。
            # 但这里发生在每次状态构建，打日志会刷屏；计入账本的 calls 已经留痕。
            led.calls += 1
            return []
        return led.admissible(scorer_sha=scorer_sha, suite_sha=suite_sha,
                              exclude_task_id=task_id)

    # ------------------------------------------------------------------
    def _gate_finish(self, result: ToolResult) -> ToolResult:
        """`finish` 之后、交付之前的那道闸。通过 ⇒ 原样返回；不过 ⇒ 变成一次失败。

        ## 为什么"变成失败"而不是"另起一条提示"

        把它折成 `ToolResult(ok=False, error=报告)` 有四个连带好处，全都不用新写代码：

        | 已有的机制 | 自动得到的效果 |
        |---|---|
        | `wm.observe(..., ok=False)` | 世界模型**第一次**有了"知道会成功却失败了"的样本 ⇒ 边界 10 的学习面开了口 |
        | `consecutive_failures += 1` | 连续打回会被熔断兜住，不会无限重写 |
        | `memory.record("failure", …, error=…)` | 失败的**原文**进情景记忆，可回放 |
        | `replan(error=…)` | 下一轮带**错误原文**重写，而不是原地把同一段答案再交一遍 |

        ## 三条不许省

        ① **打回次数必须有上限**（`limits.verify_retries`）。一个答不对的题可以
           永远写得"结构正确"，没有上限的重写就是在烧交付的配额。
        ② **打回的答案必须留着**（`self._salvage`）。重写失败时退回它 ——
           装置不许把一个能拿分的答案变成 0 分。
        ③ **未通过自检的交付不许报 `done`**。状态是读数的一部分；把它报成 done
           就是"每轮全绿而目标数字五天不动一格"的同一形态。
        """
        v: vfy.Verdict = vfy.run(result.output, self._checks)
        self.verify_stats["runs"] += 1
        if v.ok:
            self.verify_stats["passed"] += 1
            return result

        self.verify_stats["failed"] += 1
        self._salvage = result.output
        cap = max(0, int(self.s.limits.verify_retries))
        if self.verify_stats["failed"] > cap:
            # 超限：**如实交付**，并把"没过自检"写进状态。
            self.verify_stats["exhausted"] += 1
            self._unverified = v.report
            # ★ 第二十七轮（§17-6 6a）：超限交付不进主循环的失败分支
            #   （result.ok=True ⇒ 走交付）⇒ 不落 failure 的话，
            #   "最终为什么没过闸"这件事在情景记忆里**零痕迹** ——
            #   而它恰恰是这题最该被蒸馏成经验的信息。
            self.memory.record(
                "failure", f"自检超限未过（{self.verify_stats['failed']}次）— {v.report[:200]}",
                error=v.report[:300])
            self.log(f"      ⚠️ 自检连续 {self.verify_stats['failed']} 次未过 "
                     f"（上限 {cap}）⇒ 不再重写，如实交付未通过自检的答案")
            return result
        self.log(f"      🧪 自检未过（第 {self.verify_stats['failed']}/{cap} 次打回）"
                 f" ⇒ 退回重写：{v.report[:120]}")
        return ToolResult(False, error=f"自检未通过：{v.report}")

    # ------------------------------------------------------------------
    def _recall_for(self, it: int, handle, *, where: str, trigger: str) -> None:
        """按句柄取回原文的一窗，放进**下一回合**的上下文。

        ⛔ 三条不许省：
          ① **上限**：`EXPAND_MAX_PER_TASK`。没有上限的"按需"迟早把窗口塞满，
             那时多步任务反而变成"一步烧完全部预算"。
          ② **失败要留正向信号**：句柄丢了 / 库被动过 ⇒ 计数 + 日志，
             不许 `except: pass`（那等于"取回失败了也照常跑"，读数还是 0，没人知道）。
          ③ **截断要明示**：回看窗口小于原文时，块头写清"原文 N 字"，否则
             模型会以为它看到的就是全部 —— 那就是把"看不见"伪装成"看完了"。
        """
        if self.memory_mode == ABSTRACT_MODE:
            # ⚠️ abstract 臂的语义是"只读蒸馏层"。允许它回看原文，这个臂就不是对照了
            #    —— 两个臂会同时读得到原文，差值变成噪声。
            self.expand_stats["skipped"]["mode"] += 1
            return
        if self.expand_stats["calls"] >= EXPAND_MAX_PER_TASK:
            self.expand_stats["skipped"]["budget"] += 1
            self.log(f"      📎 回看已达上限 {EXPAND_MAX_PER_TASK} 次 ⇒ 不再取回")
            return
        try:
            text, truncated = self.memory.expand(handle.ref, cap=EXPAND_CHAR_CAP,
                                                 where=where)
        except (KeyError, BlobIntegrityError) as e:
            self.expand_stats["errors"] += 1
            self.log(f"      ⚠️ 回看失败[{type(e).__name__}] {e} ⇒ 上下文里不会有这段")
            return
        head = f"{EXPAND_MARK}{handle.ref} 原文{handle.chars}字 窗={where}"
        head += f" 已截断到{len(text)}字" if truncated else " 未截断"
        head += f" 因由={trigger}]"
        if not self.memory.upgrade_last(f"{head}\n{text}"):
            # 没有可升级的条目（例如上层关掉了工作记忆写入）⇒ 计数，不假装成功。
            self.expand_stats["skipped"]["no_entry"] += 1
            self.log("      📎 回看取到了正文但工作记忆为空 ⇒ 无处安放（已计数）")
            return
        self.expand_stats["calls"] += 1
        self.expand_stats["chars"] += len(text)
        self.expand_stats["by_trigger"][trigger] = (
            self.expand_stats["by_trigger"].get(trigger, 0) + 1)
        self.log(f"      📎 回看 {handle.ref} 因由={trigger} 窗={where} "
                 f"{len(text)}/{handle.chars} 字")

    # ------------------------------------------------------------------
    def _state(self, goal: str, history: Sequence[str], it: int, *,
               task_id: str | None = None, scorer_sha: str = "",
               suite_sha: str = "") -> str:
        parts = [f"[目标] {goal}"]
        # 从记忆进到推理的**实际条数**：只在真正拼进上下文的地方累加，
        # 不在上游估一个数 —— 估出来的数会让"覆盖率"变成自我安慰。
        mem_items = 0
        mem_chars = 0
        # ── 预算：`cfg.STATE_CHAR_CAP`（唯一声明处），按**优先级**取舍 ──────
        # ⚠️ 第二十轮之前这里没有预算，而规划器那边有一句 `state[:1200]`。
        #    两侧各有一套"能放多少"的真相 ⇒ 被剪掉的东西**不会有任何报错**。
        #    实测：回看正文拼在状态尾部，被规划器一剪，一个字都没进模型，
        #    而读数上写着"取回成功"。⇒ 预算必须在这里显式执行。
        # 优先级（高 → 低）：目标 > 工作记忆（含回看升级） > 步号 > 语义记忆 > 轨迹
        #   顺序的理由：目标与"下一步的动作依据"是**不可替代**的；
        #   语义记忆与轨迹是"有更好、没有也能继续"的辅助。
        cap = int(cfg.STATE_CHAR_CAP)
        wm = [] if self.memory_mode == ABSTRACT_MODE else self.memory.working_view()
        if wm:
            # ⚠️ **必须逐条截断**：工作记忆存的是工具输出的原文，不截断的话
            #    一个大文件/一段长代码就能把整个上下文窗口吃掉
            #    —— 那时"多步任务的上下文"反而变成"一步就把预算烧完"。
            recent = list(wm[-6:])
            # ⚠️ 宽度**按条**决定，不是按全局：普通条目仍是尾 200 字（省上下文），
            #    被「按需回看」升级过的那一条已经是预算内的定稿，**不许再二次截断**
            #    —— 否则取回来的正文会被这里悄悄剪掉，机制看起来装了其实等于没装。
            def _render(ws: Sequence[str]) -> str:
                return " ｜ ".join(
                    w if w.startswith(EXPAND_MARK) else w[-200:] for w in ws)

            # 预算不够就**先丢最老的条目**（新的比旧的更接近"现在"）；
            # 而不是从尾部截断字符串 —— 尾部正是回看正文所在的位置。
            # ⚠️ 用 `for` 而**不是** `while`：内核「只准有一个 while」是 AST 钉死的
            #    不变量（契约 J6）。这里本来就最多丢 5 条，for 既表达上界又不破规。
            for _ in range(5):
                if len(recent) <= 1 or len(parts[0]) + len(_render(recent)) <= cap - 200:
                    break
                recent = recent[1:]
                self.state_trimmed += 1
            block = _render(recent)
            parts.append("[工作记忆] " + block)
            mem_items += len(recent)
            mem_chars += len(block)
        recalled = self.memory.recall(goal, k=self.s.limits.recall_k)
        if self.memory_mode == ABSTRACT_MODE:
            # 只留抽象层。⛔ 过滤**只看 source**，不看文本长相 ——
            #   "长得像经验"是个由运气决定的判据，source 才是写侧承诺的事实。
            self.memory_layer_view["recalled"] += len(recalled)
            recalled = abstract_only(recalled)
            self.memory_layer_view["abstract"] += len(recalled)
        if recalled:
            block = " ｜ ".join(f.text[:120] for f in recalled)
            head = "[经验]" if self.memory_mode == ABSTRACT_MODE else "[语义记忆]"
            parts.append(head + " " + block)
            mem_items += len(recalled)
            mem_chars += len(block)
        crit = self._admissible_critiques(task_id=task_id, scorer_sha=scorer_sha,
                                         suite_sha=suite_sha)
        if crit:
            # 只放**已完成**的题的理由，且每条都带分数与族 ——
            # 没有来源的"教训"就是知识层失控的起点（重复堆积、无法归因）。
            parts.append("[外部评审] " + " ｜ ".join(c.as_line()[:140] for c in crit[-3:]))
            mem_items += len(crit[-3:])
            mem_chars += sum(len(c.as_line()[:140]) for c in crit[-3:])
        if history:
            parts.append("[轨迹] " + " ｜ ".join(history[-6:]))
        step_line = f"[第 {it}/{self.ledger.budget.steps if self.ledger else '?'} 步]"
        # 低优先级的块**只在预算内**才拼进去：宁可少一段辅助信息，
        # 也不许把高优先级的块（工作记忆/回看正文）挤出去。
        keep, used = [], len(parts[0]) + len(step_line)
        for p_ in parts[1:]:
            if used + len(p_) + 1 <= cap:
                keep.append(p_)
                used += len(p_) + 1
            else:
                self.state_trimmed += 1
        parts = [parts[0]] + keep + [step_line]
        state = "\n".join(parts)
        # ★ 第三十六轮（§17-7h 装置修正⑭②）：把**真正拼给模型的那段文本**留下。
        # 为什么必须有它：R1 之前只有两条二手的证据 —— `recall()` 返回了什么、
        # 以及"`want[:120] in state`"（整段 state）。后者会把经工作记忆漏进来的
        # 也算成"送达"，于是**第二条注入路径**在读数里完全不可见（s35 实测）。
        # ⛔ 内核**不**写盘、**不**判断"这段对不对"：它只留证据，
        #    由 runner 取走落盘、由**内核之外**的探针按块切开判定。
        # ⚠️ 有界：只留最近 `STATE_LOG_CAP` 步（预算的步数上限是 12）。
        self.state_log.append((it, state))
        if len(self.state_log) > STATE_LOG_CAP:
            del self.state_log[:-STATE_LOG_CAP]
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
    def _reset_verify(self, goal: str = "") -> None:
        """判据与自检读数的**唯一**声明处（`__init__` 与 `run()` 都调它）。

        ⚠️ 为什么收成一处（第九十一轮）：`_close_out` 从这一轮起在交付前跑一次闸，
        而它可以被单独调用（AS20）⇒ `_checks` / `verify_stats` 不在构造时存在，
        收尾路径就会 AttributeError —— 而它存在的唯一目的就是"别交付零"。
        同时两处各写一份字典必然漂移（`run()` 加了键而 `__init__` 没加）。
        """
        self._checks: tuple[vfy.Check, ...] = (
            vfy.derive(goal, int(self.s.limits.verify_static))
            if int(self.s.limits.verify_retries) > 0 else ())
        self.verify_stats: dict = {
            "runs": 0, "passed": 0, "failed": 0, "exhausted": 0,
            #: ★第九十一轮（§57）：**收尾那一次**闸的读数。
            #: ⛔ 必须与上面三个数分列：混在一起就回答不了"没过的那次是哪条路交的"。
            #:    第九十轮的实况就是"11 次收尾交付、0 次过闸"在只念 `校验=` 时
            #:    完全看不见（A 臂 6 次全是 `1跑/0过/1打回/0放弃`，
            #:    而那 1 次打回发生在**主循环**，交付物却换了来源）。
            "closeout_runs": 0, "closeout_failed": 0,
            "checks": [c.name for c in self._checks],
        }
        #: 每次 `finish` 的答案原文 —— 自检打回时**留着**，供最终交付兜底。
        #: ⚠️ 不保留它的话，"自检打回 ⇒ 重写 ⇒ 停滞 ⇒ 交付空"这条路径会把
        #:    一个本来能拿分的答案变成 0 分，而分数下降会被读成"自检有害"。
        #:    装置不该吃掉效应，这是第十七轮（共用记忆目录）的同一条纪律。
        #: ⚠️ 第九十一轮挪到这里：`_close_out` 拿它当 `overrode_rejected` 的比较基准，
        #:    所以它必须在**构造时**就存在（否则可单独调用的收尾路径 AttributeError）。
        self._salvage: str = ""
        #: 交付时的自检说明。非空 ⇒ 这次交付**没有通过自检**，状态要如实标出来。
        #: ★第九十一轮起语义收紧：它描述的是**交出去的那一份**，不是"本轮发生过什么"
        #:   —— 收尾若把答案换成一个过闸的版本，这里必须清空（`_close_out`）。
        self._unverified: str = ""

    # ------------------------------------------------------------------
    def _reset_closeout(self) -> None:
        """收尾兜底读数的**唯一**初始化处（`__init__` 与 `run()` 都调它）。

        ⚠️ 两处各写一份 = 迟早漂移：`run()` 加了字段而 `__init__` 没加，
        单独调 `_close_out` 的那个测试就会 KeyError —— 而它测的恰恰是语义。
        """
        self.closeout_stats: dict = {
            "triggered": 0,
            "by_reason": {"steps": 0, "wall": 0},
            "origin": {},              # {"收尾调用"/"episodic"/"history"/"空": 次数}
            "chars": 0,
            "empty": 0,
            #: 与 `_salvage`（被闸打回的那一版）**逐字**比较的结果。
            #: ⚠️ 只主张**不同**，不主张**更好** —— 更好只有外部打分器能判。
            "overrode_rejected": 0,
            "same_as_rejected": 0,
            #: ★第九十一轮（§57）：收尾交付物**过闸**的次数 / 其中没过的次数。
            #: 为什么是独立两个键而不是并进 `verify_stats`：`verify_stats` 回答
            #: "本轮一共跑了几次闸"，这里回答"交出去的那一份过没过"——
            #: 第九十轮的病正是**前者的数字看着正常、后者根本没有**。
            "gate_checked": 0,
            "gate_failed": 0,
        }
        #: 本次进入收尾余量是哪条阈值触发的（`steps` / `wall`）。由
        #: `_closing_reserve_hit()` 写、`_close_out()` 读 —— 条件只在**一处**声明。
        self._reserve_reason: str = ""

    def _reset_external(self) -> None:
        """外部能力读数的**唯一**初始化处（`__init__` 与 `run()` 都调它）。

        ⚠️ `calls` 与 `ok` 必须分列：只记成功会让"它一直在失败"看起来像
        "它没被调用"，而这两种病（没接上 / 接上但坏了）的修法完全不同。
        ⚠️ `meta` 是外部进程的**自述**，不是判据。
        """
        self.external_stats: dict = {
            "tool": self.s.limits.external_answer_tool,
            "calls": 0, "ok": 0, "err": 0, "chars": 0,
            "used_as_answer": 0,
            #: **实际被调用过**的答案型外部工具名（按首次出现排序、去重）。
            #:
            #: ⛔ 为什么不能只有 `tool`（配置项）：第六十四/六十五轮两批真实日志里，
            #:    规划器自己挑了 `agent:code_agent`、21 次全部失败，而汇总行按
            #:    `if ex.get("tool")` 判"印不印这一段" ⇒ 没配置 ⇒ 整段消失
            #:    ⇒ **21 次失败在证据里一个字都没有**。
            #:    "配置了"与"用上了"是两件事，读数必须分开记（见 `external_summary`）。
            "tools_seen": [],
            #: 因为**额度不够**而根本没被注入的次数（不是失败，是没试）：
            #: 与 `err` 分开计 —— "没试"和"试了失败"的修法完全不同。
            "skipped_no_budget": 0,
            #: 逐次失败的**原因原文**（去重、上限 `_ERR_REASON_CAP` 条）。
            #:
            #: ⛔ 为什么不能只留 `last_error`（第二十三轮实测代价）：ho_on 7 次调用
            #:    5 次失败，可证据里只剩**最后一条**，而且判读只看 `ok==0` 的臂
            #:    ⇒ "7 调 2 成 5 败"这件事在证据里退化成一句笼统的话，
            #:    我必须手工复现才能知道到底是超时 / 空返回 / 解析失败。
            #:    三种病的修法完全不同（调预算 / 换模型 / 改正则）——
            #:    **不可归因的失败等于没测**。
            "err_reasons": [],
            "last_error": "",
            "meta": {},
        }

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
        # ⚠️ 这里写 `self._reserve_reason` 是**有意的副作用**：条件（"预算够不够收尾"）
        #    只许在**一处**声明，而读数要区分"步数触发的"与"时间触发的" ——
        #    两者修法完全不同（前者是任务太大，后者是调用太慢）。
        #    把条件抄第二遍去算原因，就是本项目反复踩的"同一事实声明两处"。
        if lim.close_reserve_steps > 0 and steps_left <= lim.close_reserve_steps:
            self._reserve_reason = "steps"
            return True
        if lim.close_reserve_s > 0 and wall_left <= lim.close_reserve_s:
            self._reserve_reason = "wall"
            return True
        self._reserve_reason = ""
        return False

    def _close_out(self, goal: str, history: Sequence[str],
                   done: Callable[[str, str, str], "Outcome"],
                   *, reason: str = "closing_reserve",
                   detail: str = "") -> Outcome:
        """进入收尾（预算只够交付，或**停滞**）后的交付 —— 尽力把答案交出去。

        不是"假装完成"：答案要么来自一次专门索要答案的调用（余量就是留给它的），
        要么来自已收集到的材料；两者都拿不到就如实报空。

        ★ 第六十九轮：多了一个入口 `reason="stalled"`（探索停滞，但**还有额度**）。
        两者共用同一条交付通道，但**原因必须分开说**：
        · `closing_reserve` ⇒ "预算不足"是实话；
        · `stalled` ⇒ 预算并不不足，说成"预算不足"就是**假归因**
          （§29.7 第 2 条的病根就是它：`stalled` 时还剩 85s 却直接交零）。

        ⚠️ `done` 必须由调用方显式传入，**不能**在这里引用 `run()` 里的局部闭包：
        本方法曾是 `run()` 内的嵌套函数写法搬到类上时漏改的一处（Q 组实测抓到的
        `NameError: _done is not defined`）—— 收尾路径一旦炸，交付就退回零。
        收尾是"最后一道网"，它自己的作用域绝不能再依赖调用栈。
        """
        _stalled_entry = (reason == "stalled")
        if _stalled_entry:
            self.log("  🧊 停滞 ⇒ 探索到此为止，但**还有额度** ⇒ 转向交付（不是预算不足）")
        else:
            self.log("  ⏱ 触发收尾余量 ⇒ 转向交付（探索到此为止）")
        # ★ 把留给收尾的时间**放出来**（第二十五轮）：探索阶段扣下它是对的，
        #   收尾阶段还扣着就等于「留了余量但不用」。契约 AU7 钉住本条。
        if self._counted is not None:
            self._counted.reserve_release()
        co = self.closeout_stats
        co["triggered"] += 1
        # ⛔ 这里**必须**用 `.get(..., 0) + 1`，不许写成 `[...] += 1`。
        #    实测（第二十五轮 AU8）：新加的 `_reserve_reason="wall_retry"`
        #    （重试链被墙钟挡住、但余量还在）撞上零点里只有 `steps`/`wall`
        #    两个键的字典 ⇒ `KeyError` ⇒ **收尾路径自己炸掉** ⇒ status=error、
        #    交付零东西。而这个方法存在的唯一目的就是"别交付零"。
        #    ⇒ 「新增一个原因值」与「新增一个字典键」是两处声明，必须解耦：
        #      原因值可以自由增加，读数统计按出现即计数。契约 AU8 钉住本条。
        # ★ 第六十九轮：`stalled` 这个原因**来自调用方**，不来自 `_reserve_reason`
        #   —— 停滞时预算并没有进收尾区间，`_reserve_reason` 是空串，
        #   落到 `or "steps"` 就会把"停滞"记成"步数用尽"（假归因）。
        _why = reason if _stalled_entry else (self._reserve_reason or "steps")
        co["by_reason"][_why] = int(co["by_reason"].get(_why, 0)) + 1
        best = ""
        origin = ""
        try:
            # ★ 第四十五轮（§17-7q）：收尾调用原来只传 goal —— 与 propose 的
            #   定向补救同宗的"记忆盲"断点（最需要上下文的那一次，上下文被扔了）。
            #   这里现算一份完整 state（含 [语义记忆]/[工作记忆] 块）带进补救。
            #   state_log 会多一条收尾时刻的快照 —— 这是**如实**记录：
            #   补救请求里真的放了这些内容，读数就该看得见。
            #   ⚠️ `_close_out` 可被单独调用（AS20 的单元级验证）⇒ ledger 可能
            #      还是 None —— 取步号必须防 None，否则收尾路径自己炸掉，
            #      而它存在的唯一目的就是"别交付零"。
            _co_it = self.ledger.steps_used if self.ledger is not None else 0
            _co_state = self._state(goal, history, _co_it)
            best = self.planner.answer(goal, state=_co_state).strip()
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
        # ── 交付前**必须**过同一道闸（第九十一轮，§57）──────────────────────
        # ⛔ 第九十轮实测：这条路交付的 **11 份里 0 份过过闸**，而
        #    `unverified_delivery` 11 次全是 false。因为它只描述"主循环那道闸
        #    有没有放弃过"，**不描述交出去的那一份过没过**：
        #      · A 臂 6 次 `closed` 全部是 `校验=1跑/0过/1打回/0放弃` ——
        #        那 1 次打回发生在主循环，交付物却是收尾重新要来的另一份；
        #      · B 臂 5 次 `closed` 全部是 `校验=0跑` —— 闸一次都没跑过。
        #    `sg10` 第 2 轮交付的**就是刚被闸打回的那一份**（419 字符，
        #    sha 38f40729…，`改交付=0(与被闸打回那版同=1)`）：14 条断言里 13 条
        #    是同一句 `NameError: name 're' is not defined`。那一份值 13 格，
        #    而它同时是 A 臂噪声极差 15 格里的 13 格 —— 判"可分辨"用的正是它。
        #
        # ✔ **只记状态，不重试**：重试会改变交付物 ⇒ 改变分数 ⇒ 这一轮就变成
        #    "修读数"与"改能力"的混合实验（§45.9 混口径的老账）。下面这段
        #    对 `best` **一个字符都不许改**，本轮的可证伪预测就是"交付物逐字不变"。
        #
        # ⚠️ 两个必须跳过的条件，一个是"判错对象"，一个是"判错语义"：
        #    · `not best`：交付物是空串时 `_finish` 会用 `_salvage` 顶替 ⇒
        #      这里判的就不是最终交付物。
        #    · `not self._checks`：`vfy.run(x, ())` 返回 **ok=False**（"无判据"≠通过）
        #      ⇒ 关掉 `verify_retries` 的用户会拿到一个**假失败**（自造假失败，
        #      与"关掉判据也能刷出 100% 通过率"是同一枚硬币的两面）。
        if best and self._checks:
            _co_v = vfy.run(best, self._checks)
            self.verify_stats["runs"] += 1
            self.verify_stats["closeout_runs"] += 1
            co["gate_checked"] += 1
            if _co_v.ok:
                self.verify_stats["passed"] += 1
                # ★ 清标：`_unverified` 描述的是**交出去的那一份**。
                #   预置的旧标属于"主循环放弃过的那一版"，而那一版已经被
                #   `best` 顶掉了 —— 继续挂着它就是把"交了好答案"报成
                #   "交了坏答案"。假指控与假绿是同一种病（§21 的 `_salvage`
                #   那次教训的另一面）。
                self._unverified = ""
                self.log("  🧪 收尾交付过闸")
            else:
                self.verify_stats["failed"] += 1
                self.verify_stats["closeout_failed"] += 1
                co["gate_failed"] += 1
                self._unverified = _co_v.report
                # 与 `_gate_finish` 超限分支同一条理由（第二十七轮 §17-6 6a）：
                # "最终为什么没过闸"是这一题最该被蒸馏成经验的信息 ——
                # 不落 failure，它在情景记忆里就是零痕迹。
                self.memory.record(
                    "failure", f"收尾交付未过自检 — {_co_v.report[:200]}",
                    error=_co_v.report[:300])
                self.log(f"  🧪 收尾交付**未过闸**：{_co_v.report[:120]}")
        #: 交付物过没过闸，必须写进 `reason` —— 状态名仍然说"怎么结束的"
        #: （`closed` / `stalled` 本来就没说"已校验"），这句话补的是"过没过"。
        _co_gate_note = " ｜ 该答案**未通过自检**" if self._unverified else ""
        # ── 读数（第二十二轮）：来源分布 + 交付量 + **有没有改掉被打回的那一版** ──
        # ⚠️ `overrode_rejected` 的语义严格限定为"不同"：它与 `_salvage` 逐字比较，
        #    只能说"收尾让交付物变了"，**不能说**"收尾救回了这道题"。
        #    把"不同"读成"更好"是本项目最容易被自己骗过去的一步。
        co["origin"][origin] = co["origin"].get(origin, 0) + 1
        co["chars"] += len(best)
        if not best:
            co["empty"] += 1
        if self._salvage:
            key = ("same_as_rejected" if best.strip() == self._salvage.strip()
                   else "overrode_rejected")
            co[key] += 1
        self.log(f"  📦 收尾交付 {len(best)} 字符（来源={origin}）")
        if _stalled_entry:
            # ⛔ 不许说"预算不足"：这条路上的额度是**够**的（判据见 `stall_verdict`）。
            #    状态仍是 `stalled`（O3 的语义：如实报告没有进展），
            #    但"没进展"与"交零"是两件事 —— 后者是旧代码的额外损失。
            _left_s = (self.ledger.budget.wall_s - self.ledger.elapsed()
                       if self.ledger is not None else 0.0)
            return done("stalled", best,
                        f"拒绝原地打转{('（' + detail + '）') if detail else ''}"
                        f"，但仍有 {_left_s:.0f}s 额度 ⇒ 走答案通道交付"
                        f"（不是预算不足）{_co_gate_note}")
        return done("closed", best,
                    f"closing_reserve：预算不足，交付已有材料{_co_gate_note}")

    # ------------------------------------------------------------------
    def _finish(self, status: str, answer: str, reason: str, history: Sequence[str],
                *, extra: dict | None = None) -> Outcome:
        # ── 兜底：自检打回过的答案不许因为"重写失败"而一起消失 ──────────────
        # 为什么这条必须在 stats 之前：它改变的是**交付物本身**，
        # 而 stats 里的 `verify.exhausted` 是"这次交付没过闸"的记录。
        # 两者一起才说清"交了什么"与"它有没有过闸"。
        if not answer and self._salvage:
            answer = self._salvage
            note = "交付的是**未通过自检**的答案（重写未成功，退回原文）"
            reason = f"{reason} ｜ {note}" if reason else note
            if not self._unverified:
                self._unverified = note
        stats = {
            "budget": self.ledger.snapshot() if self.ledger else {},
            "guard": self.guard.report(),
            "values": self.values.report(),
            "flow": self.flow.report(),
            "selfmodel": self.selfmodel.report(),
            # 跨会话账本：**采纳数与被拒数一起**给。
            # 只报"采纳了多少"，你永远看不出它有没有在挡东西 ——
            # 而"从没挡过东西的闸"和没有闸是同一件事。
            "score_ledger": (self.score_ledger.report()
                             if self.score_ledger is not None
                             else {"admitted": 0, "total": 0,
                                   "rejected": {"missing_fields": 0,
                                                "no_caliber_sig": 0,
                                                "diff_caliber_sig": 0},
                                   "bad_lines": 0, "path": "",
                                   "caliber_sig": self.caliber_sig}),
            "critique": (self.critique_ledger.stats()
                         if self.critique_ledger else {}),
            "worldmodel": self.wm.stats(),
            "planner": self.planner.stats(),
            "memory": self.memory.stats(),
            # 第二十轮：按需回看。**必须报跳过原因** —— 只报 calls 的话，
            # "机制装了但一次没触发"和"机制根本没装"看起来一模一样。
            "expand": {**dict(self.expand_stats),
                       "state_trimmed": self.state_trimmed},
            # 第二十一轮：校验。**连同判据一起报** ——
            # 只报"通过 3 次"而说不出"用哪几条判据"，那个 3 没有意义
            # （关掉全部判据也能让通过率变成 100%）。
            "verify": {**dict(self.verify_stats),
                       "unverified_delivery": bool(self._unverified),
                       "salvaged": bool(self._salvage)},
            # 第二十二轮：收尾兜底。**零值也进 stats**（汇总行才按需省）——
            # `origin` 是字典而不是单个字符串：只记"最后一次从哪来"会掩盖
            # "三次里两次退到 history"这种退化（那是"绝不返回空"的代价面）。
            "closeout": {**dict(self.closeout_stats),
                         "origin": dict(self.closeout_stats["origin"]),
                         "by_reason": dict(self.closeout_stats["by_reason"])},
            # 第二十三轮（路线①）：外部能力。**零值也进 stats**，但汇总行只在
            # 配了外部能力时念 —— 没配的时候印"外部=0次"是噪声。
            # ⛔ `used_as_answer` 必须与 `ok` 分列：调用成功但答案没被采用
            #    （例如被自检打回、或被规划器改写）是完全不同的一件事。
            "external": {**dict(self.external_stats),
                         "tools_seen": list(self.external_stats.get("tools_seen") or []),
                         "err_reasons": list(self.external_stats.get("err_reasons") or []),
                         "meta": dict(self.external_stats.get("meta") or {})},
            "llm": "stub" if self._llm_is_stub else "http",
            "llm_kind": self.llm_kind,   # 与 "llm" 同源；保留两个键是为了兼容旧读法
            "external_agents": self.external_agents,
            # 停滞/重复 —— "重复的成功不是进展"这条的读数出口
            "loop": dict(extra or {}),
            # ★ 题边界清掉的上一题残留工作记忆（第三十六轮 §17-7h）。
            # 零值也要进 stats：没有这个计数，"清干净了"与"没接线"完全同形。
            "working_cross_task": self.working_cross_task,
            # 传输层抖动 —— "基础设施的锅 ≠ 模型的锅"这条的读数出口。
            # 有消费者：`■` 汇总行会把它打印出来（零值不显示，避免噪声）。
            # ★ 第二十五轮：把"端点病"与"预算病"分开计 —— 这两者的修法
            #   相反（修端点 vs 减工作量），混成一个数就等于没读。
            "transport": ({
                "retries": self._counted.transient_retries,
                "exhausted": self._counted.transient_exhausted,
                "wall_shortened": self._counted.wall_shortened,
                "wall_refused_first": self._counted.wall_refused_first,
                "wall_refused_retry": self._counted.wall_refused_retry,
                # ★ 第九十六轮 §63：宽限给没给（④次）、用掉多少（⑤秒）。
                #   两个都要，且必须分开：给了 30s 却一秒没用 ⇒ 那一发本来就
                #   跑得完 ⇒ 真正的问题不在这里。
                "wall_overrun_grants": self._counted.wall_overrun_grants,
                "close_overrun_used_s": round(self._counted.overrun_used_s, 2),
            } if self._counted is not None else
                {"retries": 0, "exhausted": 0,
                 "wall_shortened": 0, "wall_refused_first": 0,
                 "wall_refused_retry": 0,
                 "wall_overrun_grants": 0, "close_overrun_used_s": 0.0}),
        }
        self.memory.record("finish", f"{status}: {answer or reason}"[:1000],
                           status=status, stats=stats)
        tx = stats["transport"]
        tx_line = ""
        if tx["retries"] or tx["exhausted"]:
            tx_line = f" | 抖动重试={tx['retries']} 重试后仍失败={tx['exhausted']}"
        # ⛔ 墙钟类必须单独念：并进"抖动重试"会让"预算不够"看起来像"端点抖动"，
        #    而这两者的修法完全相反（减工作量 vs 修端点）。
        # ⚠️ `wall_shortened` 与另外两个**不是**同一件事：前者是"发出去了但上限
        #    被裁短"（预算紧），后两者是"根本没发/没重试"（预算尽）。分开印。
        if tx.get("wall_shortened"):
            tx_line += f" | 上限被裁短={tx['wall_shortened']}"
        if tx.get("wall_refused_first") or tx.get("wall_refused_retry"):
            tx_line += (f" | 墙钟放弃首发={tx['wall_refused_first']}"
                        f"/重试={tx['wall_refused_retry']}")
        # ★ 第九十六轮 §63：宽限**给了几次**与**实际用掉几秒**必须一起念。
        #   只印前者会让"配了但没用上"看起来像"救回来了"。
        if tx.get("wall_overrun_grants"):
            tx_line += (f" | 宽限生效={tx['wall_overrun_grants']}次"
                        f"(超出{tx.get('close_overrun_used_s', 0):g}s)")
        # ★ 题边界清空（§17-7h）：零值不印（正常单题跑恒 0），非零必须念 ——
        #   它是"上一题的工具输出确实漏进了本题上下文"的唯一读数。
        if stats.get("working_cross_task"):
            tx_line += f" | 题边界清空工作记忆={stats['working_cross_task']}条"
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
        # 账本读数：**零值不显示**（避免每行噪声），但一旦有内容就必须带全四项
        _sl = stats["score_ledger"]
        _sl_r = _sl.get("rejected") or {}
        _sl_n = int(_sl.get("admitted") or 0)
        _sl_dedup = int(_sl.get("deduped_on_read") or 0)
        _sl_line = ""
        if _sl_n or any(int(_sl_r.get(k) or 0) for k in
                        ("missing_fields", "no_caliber_sig", "diff_caliber_sig")):
            _sl_line = (f" | 账本采纳={_sl_n} 拒(缺字段={_sl_r.get('missing_fields', 0)}"
                        f"/无签名={_sl_r.get('no_caliber_sig', 0)}"
                        f"/异口径={_sl_r.get('diff_caliber_sig', 0)})")
        # 读侧去重**单独念**：它意味着文件里躺着重复行，而未经去重的读数不可信。
        # 混进"拒"里就看不出这是"历史脏"还是"挡下来的" —— 两者修法完全不同。
        if _sl_dedup:
            _sl_line += f" ⚠️读侧去重={_sl_dedup}"
        # 自我模型：**同时**念两个数 —— 闸开没开（`usable`）与尺子读数（Δ）。
        # 只印"闸关着"看不出它是没量到还是量到了但没有；只印 Δ 看不出
        # 内核到底有没有听它的。两者一起才是"自我模型这一轮说了什么"。
        _sm = stats["selfmodel"]
        _pw = _sm.get("family_power") or {}
        _sm_line = ""
        if _sm.get("samples"):
            _d = _pw.get("delta")
            _sm_line = (f" | 自我[闸={'开' if _pw.get('usable') else '关'}"
                        f" Δ={'n/a' if _d is None else f'{_d:+.3f}'}"
                        f" 阈值={_pw.get('margin')} {_pw.get('state')}"
                        f" 影响={_sm.get('influenced')}/{_sm.get('consulted')}]")
        # 按需回看读数：**零值也念**。它是"可回溯"这个声明的唯一证据 ——
        # 只在非零时打印，就会让"一次都没取回"看起来和"没装"一样。
        _ex = stats["expand"]
        _ex_line = (f" | 回看={_ex['calls']}次/{_ex['chars']}字"
                    f"(失配{_ex['by_trigger'].get('mismatch', 0)}"
                    f"/报错{_ex['by_trigger'].get('fail_tail', 0)})"
                    f" 跳过={_ex['skipped']['budget']}(超限)"
                    f"{_ex['skipped']['mode']}(模式)"
                    f" 错误={_ex['errors']}")
        # 校验读数：**零值也念**，并且**把判据念出来**。
        # 只念"通过率"是一个可以靠关掉判据刷上去的数 —— 那不是读数，那是作文。
        _vf = stats["verify"]
        _vf_line = (f" | 校验={_vf['runs']}跑/{_vf['passed']}过/{_vf['failed']}打回"
                    f"/{_vf['exhausted']}放弃 判据={len(_vf['checks'])}项")
        if _vf["checks"]:
            _vf_line += f"({'/'.join(_vf['checks'])})"
        if _vf["unverified_delivery"]:
            _vf_line += " ⚠️本次交付未过自检"
        # 收尾兜底读数（第二十二轮）：**触发时才印**（`status=closed` 已经是它的
        # 存在性证据，每行都印"收尾=0次"只会变成噪声）。但一旦触发就必须带全：
        # 哪条阈值、从哪来、多少字符、有没有改掉被打回的那一版。
        _co = stats["closeout"]
        _co_line = ""
        if _co["triggered"]:
            _co_line = (f" | 收尾={_co['triggered']}次(步{_co['by_reason']['steps']}"
                        f"/时{_co['by_reason']['wall']}"
                        # ⚠️ `steps`/`wall` 之外的触发原因必须**自动**补进来。
                        #    第二十五轮加 `wall_retry`（重试链被墙钟挡住、但余量还在）
                        #    时就踩到了：只印固定的两项 ⇒ 日志写着"收尾=1次(步0/时0)"，
                        #    自相矛盾，而矛盾的那一位正是修好这次事故的那条路径。
                        + "".join(f"/{k}{v}" for k, v in _co["by_reason"].items()
                                  if k not in ("steps", "wall") and v)
                        + f" 来源{','.join(f'{k}:{v}' for k, v in _co['origin'].items()) or '-'}"
                        + f" {_co['chars']}字)")
            if _co["overrode_rejected"] or _co["same_as_rejected"]:
                _co_line += (f" 改交付={_co['overrode_rejected']}"
                             f"(与被闸打回那版同={_co['same_as_rejected']})")
            if _co["empty"]:
                _co_line += f" ⚠️交了空={_co['empty']}"
            # ★第九十一轮（§57）：收尾那一次闸的读数。
            # ⛔ 与上面的 `校验=` **分列**念：那个是"全轮跑了多少次闸"，这个是
            #    "交出去的那一份过没过"。第九十轮的实况是"11 次收尾交付、0 次过闸"，
            #    而 `校验=` 那一段在 B 臂的 5 次上写着 `0跑` —— 只说"没跑"，
            #    不说"没跑就交了"。分列之后这两件事各自可读。
            if _co.get("gate_checked"):
                _co_line += (f" 收尾闸={_co['gate_checked']}跑"
                             f"/{_co['gate_checked'] - _co.get('gate_failed', 0)}过"
                             f"/{_co.get('gate_failed', 0)}未过")
        # 外部能力读数（第二十三轮；第六十六轮改为按「实际调用过」判）：
        # 判据搬进 `external_summary()` 是为了**能被契约直接钉住** ——
        # 放在 `_finish` 的函数体里只能靠读源码，读源码的断言恒真。
        _ex2_line = external_summary(stats)
        self.log(f"■ {status} | steps={len(history)} | guard_blocked={stats['guard']['total']}"
                 f"{wm_line}{v_line}{f_line} | 语义记忆={stats['memory']['facts']}"
                 f"{tx_line}{_sl_line}{_sm_line}{_ex_line}{_vf_line}{_co_line}{_ex2_line}")
        return Outcome(status, answer, reason, len(history), stats)
