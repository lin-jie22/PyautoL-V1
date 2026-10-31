"""动态规划 + 重规划。

依据：RedLineSoft 2026 —— "静态线性执行循环（vanilla ReAct）已被动态分层规划取代"。
关键不是"会拆任务"，而是**失败时不崩，而是改写任务图**：
    Execution Monitoring → 分析状态增量 → 重规划（rewrite the task graph on the fly）

V1 的实现刻意轻：不做完整 HTN，不改写整张图。只做两件在实测中真正管用的事：
    1. 每步产出 N 个**候选动作**（这就是 test-time compute 的落点：
       不是一个想法，而是几个想法，然后用 guard + 世界模型筛）。
    2. 失败后带着**错误原文与已试过的动作**重规划，而不是原地重试同一个动作
       —— 这正是"同一假设无限重派"的反面。

规划器**必须**能降级：LLM 输出不可解析时，回退到确定性单步计划，
而不是抛异常把整轮打断。但降级会被计数（degraded），不许静默。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from . import config as cfg
from .llm import ANSWER_MARK, LLM, Message, parse_json, split_answer


@dataclass
class Step:
    intent: str
    tool: str
    args: dict
    rationale: str = ""

    def as_line(self) -> str:
        return f"{self.tool}({self.args}) — {self.intent}"


#: **不进签名**的参数：它们不是"这个动作是什么"的一部分，而是"这一次给了它多少预算"。
#:
#: ⛔ 这份集合是第六十八轮用真跑逼出来的（证据 `bench/evidence/reseek_s68/`）。
#:    第六十七轮把答案型外部能力的入参统一成 `answer_task(goal, deadline)` 之后，
#:    `deadline_s` 里是**现算的剩余墙钟** ⇒ 同一件事每次的签名都不一样
#:    ⇒ `attempted_sigs` 再也认不出重复。实测后果：`agent:code_agent` 被同一个
#:    （已被外部拒绝的）任务**连续执行 3 次**，`repeats_skipped=0`、`attempted=2`，
#:    最后撞上级联熔断 `cascade`，交付为空。
#:    这是"防打转的闸被一个预算字段悄悄拆掉"——**闸还在，判据被污染了**。
#:
#: 入集的条件必须同时满足三条（缺一条就说明它其实是动作的一部分）：
#:   ① 由**内核**写进去（不是模型想干什么）；
#:   ② 会随时间/环境变（所以天生不能当身份）；
#:   ③ 改了它不改变"动作对世界做了什么"。
_NON_IDENTITY_ARGS: frozenset[str] = frozenset({"deadline_s"})


def action_signature(tool: str, args: Any) -> str:
    """动作签名 —— 判断"是不是同一个动作"的**唯一**判据。

    为什么必须是单一事实源：内核用它判"这个动作执行过了、不许原地打转"，
    规划器用它判"候选之间是不是不同做法"。两处口径若不一致，
    "已试过"就会形同虚设 —— 一个用 `repr`、一个用排序，同样的动作会被当成两个。

    ⚠️ 实测背景：内核曾在一次任务里把 `recall(同一个 query)` 重派了 5 次，
    烧掉 120s 预算直到 `budget_exhausted`。`consecutive_failures` 拦不住它 ——
    因为那几次 `recall` **全都 ok=True**。**重复的"成功"不是进展**，
    而当时内核里没有任何机制表达这句话。

    ⚠️ 第二课（第六十八轮）：**签名里只许放身份字段**。`deadline_s` 是内核现算的
    剩余墙钟，把它算进身份 ⇒ 同一件事每次都被判成新动作 ⇒ 防打转的闸形同虚设。
    详见 `_NON_IDENTITY_ARGS`。
    """
    try:
        pairs = dict(args).items()
    except (TypeError, ValueError):       # args 不是映射（模型偶尔给字符串/列表）
        pairs = ()
    pairs = [(k, v) for k, v in pairs if k not in _NON_IDENTITY_ARGS]
    try:
        items = sorted(pairs)
    except TypeError:                     # 值之间有不可比较类型时按 key 排
        items = sorted(pairs, key=lambda kv: str(kv[0]))
    return f"{tool}|{items}"


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
    "  · tool 必须是**下面列出的工具名之一** —— 不得发明，"
    "不得写 null / None / \"\"（实测踩过：模型想要一个“写代码”的工具，"
    "目录里没有，于是就填了 None，整轮因此退化成兜底）\n"
    "  · 已经失败过的做法不要重复提出\n"
    "  · **如果你已经能直接完成任务**（目标只是要一段代码、一个结论或一段文本），\n"
    "     就调用 finish，把**完整答案原文**放进 args.answer。\n"
    "     不要为了“用工具”而用工具 —— 工具是手段，不是目的。\n"
    "  · ⚠️ **绝对不要把代码或长文本写进 JSON 字符串。**\n"
    "     若 action 是 finish：JSON 里 args 留空 {}（只写 intent/tool/rationale），\n"
    f"     然后在 JSON **之后**另起一行写 {ANSWER_MARK}，再往下**原样**写最终答案。\n"
    "     答案部分不需要转义、不需要代码块、不需要再解释。"
)


def _looks_truncated(text: str) -> bool:
    """粗判"响应被截断"：JSON 开了头，但括号/引号不闭合。

    为什么要单独判这一种：它和"模型不会写 JSON"**修法完全不同** ——
    一个调 `max_tokens`，一个改提示词/解析器。而实测里两者都会被
    `parse_json` 归成同一个"返回 fallback"，于是日志只说"不可解析"，
    看起来像模型的锅，实际是预算给少了。

    扫描时**必须跳过字符串内部**：JSON 的字符串里可以合法地出现 `{` `}`
    （代码示例、模板），不跳就会被内容误导。
    """
    t = (text or "").strip()
    if "{" not in t:
        return False
    depth = 0
    in_str = False
    esc = False
    for ch in t:
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    return depth != 0 or in_str


_ANSWER_SYS = (
    "只输出最终答案本身：不要 JSON、不要解释、不要道歉、不要代码块围栏。"
    "如果是代码，直接输出代码原文。"
)

#: ★ 第五十轮（§17-7v-b）：语义记忆的**忠实引用**指令（34 字）。
#:
#: 来源与定位：它先是 7u 的**安慰剂臂 C**（当时当作"中性串"），实测却让
#: g7e2 三 rep 全对（0.2 ⇒ 1.0）、两个通道都对 ⇒ 事后发现 ⇒ 7u **不认领**。
#: 7v-a 复测确认基线噪声底 ≈ 0 ⇒ 7v-b 把它升为**登记的处理臂**重新验证。
#:
#: 机制假设（**7v-c 已修正一半，读下面第 4 条**）：原句强调"保持原有排列顺序
#: 与文字内容不变"，一度被认为靠**忠实引用**起效 —— 7v-b 的等长异义指令 C1
#: 拿到**逐位相同**的分，把"忠实引用"这一具体语义排除了。
#:
#: ⛔ 四条边界：
#:   1. 不点名任何 suite / 函数名 / 钥匙 —— 内核不许按单一 suite 定制。
#:   2. 零信息损失：召回条目与顺序一字不改，只在指令层加约束。
#:   3. 唯一事实源：只在这里定义，由 `answer()` 拼进 prompt。
#:   4. ★7v-c（证据 s55，四臂 A/P1/P2/B）曾把"祈使句式"与"逐条"拆开：P2（祈使、
#:      无"逐条"）= 0.2，P1（**陈述**、有"逐条"）= 1.0 与 A 逐位相同 ⇒ 当时判
#:      **承载是『逐条』**。**7v-d 把这一条也推翻了**（证据 s56，五臂同窗）：
#:      S1（同义替换，无"逐条"）= 0.2、S2（含"逐条"但明说"不构成要求"）= 0.2、
#:      S3（"逐条"×16 纯重复）= 0.2，只有**本常量这一句** = 1.0（A 正对照 3/3）。
#:      ⇒ ⛔ **"逐条"两个字单独不够，语义单独也不够**；本轮登记判据的结论是
#:      **未定位**（不许写成"已解释机制"，也不许把"逐条"当承载者）。
#:      过程证据（s56/shapes_7vd.txt）：A 的末次补救全部收敛到 `s[-n:]`，
#:      而 S1/S3/B 是"先对后错"、S2 从头错 ⇒ 本句的作用更像**收敛闸门**。
#:      ⛔ 因此本常量的措辞**一个字都不许改**：没有任何证据表明换说法更好，
#:      而 7v-d 恰恰证明换说法会掉回 0.2。
_ANSWER_FIDELITY = "上方状态按原文顺序依次阅读，逐条都保持其原有排列顺序与文字内容不变。"

#: ⛔ 第四十九轮（§17-7u / R-i-2）**试过并撤回**，留档防重复：
#: 曾在这里加过一句"多条经验只采用与本次目标直接相关的那一条"的显式消歧指令
#: （契约 AZ45 同步撤销）。三臂实测（证据 s52）Δ(A−B) 仅 +0.0167 未过门槛，
#: 且让 g7e2 的输出在 `s[-n:]`（对）与 `s[-(n-3):]`（错）之间**双稳翻转**
#: （窗内极差 0.8 > 任何信号）⇒ 未证明有益 + 引入不稳 ⇒ 撤回。
#: 真正的病源另有其人：s52 坐实**墙钟预算与通道耦合**（同一题不同 rep 走
#: finish / 收尾 / wall 三条不同的路）⇒ 先修量具，再谈 prompt 调优。


#: 被拒动作在提示词里的条数上限。为什么不随轮数增长：这是"证据"，不是"台账"；
#: 三条足够让模型认出"我上一次提的就是这几个"，再长只是挤掉状态与工具目录。
_REJECTED_SHOWN = 3


def reseek_block(rejected: Sequence[str], untried: Sequence[str]) -> str:
    """「本轮全部候选都被拒」之后，下一次提问必须**换成另一句话**。

    ## 为什么这是一个函数，而不是提示词里再加一段

    `blocked` 现在的形态是一段静态清单（`tool|[(...)]` 的 repr）。要求模型
    "别重复它自己将要说的话"是一种**自比对**：它得先把自己脑子里还没写出来的
    候选序列化成同一个 repr 才能比。实测（`attr_s66` A 臂）它没这么干 ——
    第 2、3 步又把同一个 `agent:code_agent` 提了一遍，被内核的硬闸连续丢掉两次。

    ⇒ 所以这里不做"更仔细的提醒"，而是**换提问形态**：把上一轮**实际被丢掉的动作原文**
    摆出来（不是签名，是 `tool({args}) — intent` 这行），并给出一个模型此前
    **拿不到的信息** —— 哪些工具到现在一次都没试过。

    ⚠️ 这是"负约束 → 正约束"的转换：只说"不要重复"不给出口，模型最容易的
    合规动作就是再提一次同样的东西（它至少是合法的 JSON）；给了"这些还没试过"
    与"做不完就 finish 交出已知内容"，它才有第二条路可走。
    """
    if not rejected:
        return ""
    shown = list(rejected)[-_REJECTED_SHOWN:]
    lines = "\n".join(f"  · {str(r)[:160]}" for r in shown)
    out = ("\n⛔ 你上一次的回答被**拒绝**了：下面这些动作已经执行过，"
           "再提一次只会被内核丢弃（那不是新尝试）：\n"
           f"{lines}\n")
    if untried:
        out += ("**到目前为止一次都没试过的工具**：" + " / ".join(untried) + "\n")
    out += ("请给出与上面那些动作**不同**的候选；"
            "若确实无法再推进，就直接调用 finish，把已知内容作为答案交出来。\n")
    return out


class Planner:
    def __init__(self, llm: LLM, tools: Any, *, max_tokens: int = 2048,
                 state_chars: int | None = None) -> None:
        self.llm = llm
        self.tools = tools
        #: 单次规划调用的 token 上限。**从 config 注入**，不在这里写死 ——
        #: 实测教训：写死 700 导致含代码的 JSON 被截断，却被误读成"模型不会写 JSON"。
        self.max_tokens = int(max_tokens)
        #: 拼提示词时 `state` 的截断上限。**必须与 `core._state()` 用同一个数**：
        #: 否则"内核以为放得下"和"规划器实际收下多少"是两套真相，
        #: 而被剪掉的东西不会有任何报错（第二十轮实测栽过一次）。
        #: ⚠️ 第 120 轮（§93）：真读数来自 `settings().limits.state_chars`（进口径签名）。
        #:    `None` ⇒ 回退到模块常量（**只**为不传参的独立探针保留；Core 一定传）。
        #: ⛔ 不写 `int(state_chars) or cfg.STATE_CHAR_CAP` —— `0` 是假值，
        #:    那会把"显式设成 0"与"没传"混成一条路（纪律 30 的坑形态）。
        self.state_chars = int(cfg.STATE_CHAR_CAP if state_chars is None
                               else state_chars)
        self.calls = 0
        self.degraded = 0
        # ── 降级原因**分项计数**，不许合并成一个 degraded ──────────────────
        # 理由（一条通则）：修法不同的分支合并，就再也分不清该修哪里。
        #   json_fail      没产出合法 JSON（也不是截断形状）⇒ 改提示词/解析器
        #   truncated      开了头但括号不闭合          ⇒ ★调大 max_tokens
        #   no_tool_fit    想要的能力不在工具目录里     ⇒ 加工具/改工具描述（★实测命中）
        #   empty_plan     合法 JSON 但没有可用候选     ⇒ 改提示词
        # ⚠️ 刻意**没有** "all_duplicate" 这个降级计数器：它不改变"这次回答能不能用"，
        #    只改变"下一次提问长什么样"，所以它不属于 `degraded` 的分支。
        # ⛔ 但原来这里写的是"全是重复不可达"—— 那句话是**错的**，而且错得很安静：
        #    它把**同一响应内部**的去重（G4：响应里第一个候选永不重复，成立）
        #    当成了**跨轮**的事实。跨轮完全可以全灭：`attr_s66` A 臂第 2、3 步
        #    规划器连续两次只给同一个 `agent:code_agent`，两次都被硬闸丢掉
        #    ⇒ `🧊 停滞 1/2`、`2/2`。这个错误判断正是"不留计数器"的借口。
        # ⇒ 现在有了 `reseek`（换形态再问的次数）：它**不是**降级，是可归因读数。
        self.json_fail = 0
        self.truncated = 0
        self.no_tool_fit = 0
        self.empty_plan = 0
        #: 「本轮全灭」之后换形态再问的次数。有界：≤ 停滞轮数（内核只在那一支填 rejected）。
        self.reseek = 0
        #: 定向补救（"选了 finish 却没给答案"）：成功 / 失败分开计。
        #: 它**不算降级**（计划本身是合法的，只是缺一块载荷），所以不进 cause_balances。
        self.answer_retry_ok = 0
        self.answer_retry_fail = 0
        self.last_error = ""
        #: 模型点名但未注册的工具名 —— 失败可归因的最小证据
        self.last_bad_tools: list[str] = []

    # ------------------------------------------------------------------
    def _untried_tools(self, blocked: Sequence[str]) -> list[str]:
        """目录里**一次都没试过**的工具名。

        签名格式（`tool|[(...)]`）由本模块的 `action_signature()` 定义，
        所以在这里按 `|` 取工具名不是"第二处实现"，而是同一处事实的另一种读法。
        ⚠️ 别把它改成"再问一次模型哪些试过" —— 那是把机器已知的事实交回给猜测。
        """
        if not blocked:
            return []
        tried = {str(b).split("|", 1)[0] for b in blocked}
        names = getattr(self.tools, "names", None)
        if not callable(names):
            return []
        return [n for n in names() if n not in tried]

    def propose(self, *, goal: str, state: str, history: Sequence[str],
                n: int = 1, guidance: str = "",
                blocked: Sequence[str] = (),
                rejected: Sequence[str] = ()) -> Plan:
        """产出 n 个候选步骤（各不相同）。n>1 就是 test-time compute 的落点。

        `blocked` 是**机器生成**的"已尝试过的动作签名"清单。为什么不只靠
        `history` 里的自然语言：实测里模型会无视"已失败/已试过：…"那几行，
        然后把同一个 `recall` 再提一遍。给签名是精确的 —— 它可以直接比对。

        `rejected` 是**内核刚刚丢掉的那几个动作的原文行**（不是签名）。
        两者的分工：`blocked` 是"档案"（全部历史，给模型做约束）；
        `rejected` 是"刚刚发生的事"（本轮全灭 ⇒ 下一次提问必须换形态）。
        只在"本轮一个候选都没能执行"时由内核填，且**只喂一次**
        （见 `reseek_block` 的说明与 `core` 的 `_rejected_once`）。
        """
        self.calls += 1
        reseek = reseek_block(rejected, self._untried_tools(blocked))
        if reseek:
            self.reseek += 1
        avoid = "\n".join(f"  · 已失败/已试过：{h}" for h in list(history)[-6:])
        banned = ("\n已执行过、**禁止**再提出的动作（tool|args）：\n"
                  + "\n".join(f"  · {b}" for b in list(blocked)[-12:])
                  ) if blocked else ""
        prompt = (
            f"目标：{goal}\n"
            # ⚠️ 这里的上限**必须**与 `core._state()` 用同一个数：
            #    否则"内核以为放得下"和"规划器实际收下多少"是两套真相，
            #    而被剪掉的东西不会有任何报错（第二十轮实测栽过一次）。
            #    第 120 轮起这个数由 `Limits.state_chars` 单点注入（进签名）。
            f"当前状态：\n{state[:self.state_chars]}\n"
            f"{('已知易错点：' + guidance) if guidance else ''}\n"
            f"{avoid}{banned}{reseek}\n"
            f"可用工具：\n{self.tools.catalog()}\n\n"
            f"请给出 {max(1, n)} 个**互不相同**的候选项。"
            f"{' 若没有新的做法，就直接 finish。' if blocked else ''}"
        )
        raw = self.llm.complete(
            [Message("system", _SYS), Message("user", prompt)], max_tokens=self.max_tokens
        )
        # ── 先拆双通道：「结构」走 JSON，「载荷（代码/长文本）」原样走分隔符之后 ──
        # 这一步是整个"内容不进 JSON"修复的落点：解析只作用在结构上，
        # 于是裸换行、非法转义、长代码都不再能弄坏解析。
        head, payload = split_answer(raw)
        data = parse_json(head, fallback=None)
        if data is None and payload:
            data = parse_json(raw, fallback=None)   # 兼容：模型把 answer 也塞进了 JSON
        if data is None:
            # ── 「截断」与「不会写 JSON」是两种病，修法完全不同，必须分开计 ──
            # 实测：规划器写死 700 token，而它要求把整段代码塞进 JSON 字符串 ——
            # 响应断在半截 token 上，日志却只说"不可解析"，看起来像模型的锅。
            if _looks_truncated(raw):
                self.truncated += 1
                return self._degrade(
                    f"模型输出被**截断**（⇒ 调大 max_tokens，当前 {self.max_tokens}）；"
                    "原文片段: " + repr(raw[-120:]),
                    steps_used=len(history), goal=goal)
            self.json_fail += 1
            # 把原文片段带进 note：只说"不可解析"等于什么都没说 ——
            # 上一版就是因此把"模型填了 tool=None"误读成"模型不会写 JSON"。
            # 失败必须自解释，否则每次都要重跑一遍去抓原文。
            return self._degrade(
                "模型输出不是合法 JSON（⇒ 改提示词/解析器）；原文片段: "
                + repr(raw[:200]),
                steps_used=len(history), goal=goal)
        steps, why, bad = self._extract(data)
        self.last_bad_tools = bad
        if not steps:
            if why == "unknown_tool":
                self.no_tool_fit += 1
                return self._degrade(
                    f"模型想要的工具未注册: {bad[:3]}（能力缺口 ⇒ 加工具/改工具描述）",
                    steps_used=len(history), goal=goal)
            self.empty_plan += 1
            return self._degrade("合法 JSON 但没有可用候选（⇒ 改提示词）",
                                 steps_used=len(history), goal=goal)
        if payload:
            # 载荷**原样**注入 finish 的 answer，覆盖 JSON 里的占位。
            # 注意这里没有做任何转义/解码/剥壳 —— 代码从模型直达打分器。
            for st in steps:
                if st.tool == "finish":
                    st.args = dict(st.args)
                    st.args["answer"] = payload
        else:
            # ── 定向补救：结构化计划里选了 finish，却没给答案 ──────────────────
            # 实测（2026-10-17）：模型会在 args 里留空 {}、又忘了写分隔符，
            # 于是 finish 因缺参失败，接着被重规划成 recall 循环烧光预算。
            # 修法**不是**把同一个请求再发一遍（那是原地重试），
            # 而是**换一种提问形态**：只要求答案本身。有界（每轮至多一次）。
            blank = [s for s in steps if s.tool == "finish"
                     and not str(s.args.get("answer", "")).strip()]
            if blank:
                got = ""
                _raised = False
                try:
                    # ★ 第四十五轮（§17-7q）：这里原来调 `self.answer(goal)`，
                    #   补救 prompt 只有 goal —— 而 7n/7o 实锤（16/16 格零例外）：
                    #   K=0（模型没往 ANSWER 段写代码）的格**确定性**落到这条路，
                    #   它拿不到 propose 明明手里的 state（含 [语义记忆]），
                    #   于是只能交基线答案 ⇒ D=0.2。这与第二十轮 replan 丢 state、
                    #   guidance 断点**同宗**：最需要上下文的那一次，上下文被扔了。
                    #   修法 = 把 state 原样带进补救（截断在 answer() 内部，
                    #   用与这里同一个 `self.state_chars` —— 单一事实源）。
                    got = self.answer(goal, state=state)
                except Exception as e:  # noqa: BLE001 —— 模型抖动不该带走整轮
                    _raised = True
                    self.answer_retry_fail += 1
                    self.last_error = f"{type(e).__name__}: {e}"
                if not _raised:
                    if got.strip():
                        self.answer_retry_ok += 1
                        for st in blank:
                            st.args = dict(st.args)
                            st.args["answer"] = got.strip()
                    else:
                        self.answer_retry_fail += 1
        return Plan(steps)

    def _degrade(self, note: str, *, steps_used: int, goal: str = "") -> Plan:
        """降级出口唯一 —— 保证 self.degraded 与分项计数**同源**，不会两处漂移。"""
        self.degraded += 1
        return Plan(self._fallback(steps_used=steps_used, goal=goal),
                    degraded=True, note=note)

    def replan(self, *, goal: str, failed: Step, error: str,
               history: Sequence[str], n: int = 1,
               blocked: Sequence[str] = (), guidance: str = "",
               state: str = "", rejected: Sequence[str] = ()) -> Plan:
        """失败后重规划。**必须带上错误原文**，否则等于原地打转。

        ⚠️ `guidance` 必须一起转发（2026-10-17 接线审计抓到的断点）：
        原来这里调 `propose` 时**不传** guidance ⇒ 走默认空串 ⇒
        **恰恰是"刚栽过一次"的重规划拿不到"已知易错点"**。
        那是逻辑上最荒谬的一处断点：guidance 记的是"哪儿容易栽"，
        而重规划正是"刚栽了"的那一次。断在这里，等于教训只在第一次尝试时有用。
        """
        augmented = list(history) + [f"{failed.as_line()} ⇒ 失败: {error[:200]}"]
        # ⛔ 第二十轮修掉的一处断点：这里原本传 `state="(上一步失败，见下)"`，
        #    也就是**把整个状态丢掉**只留一句占位。
        #    后果在第二十轮实测里直接撞上：按需回看把报错原文尾部取回、放进状态，
        #    而失败之后的下一轮**恰恰走的就是 replan** ⇒ 模型一个字都没看到，
        #    读数上却写着"取回成功"。
        #    这与本函数 docstring 里那条 guidance 断点同宗：**最需要上下文的那一次，
        #    上下文被扔了**。没有理由：错误原文已经在 augmented 里，
        #    状态是**额外**的信息，不是它的替代品。
        plan = self.propose(goal=goal, state=(state or "(上一步失败，见下)"),
                            history=augmented, n=n, blocked=blocked,
                            guidance=guidance, rejected=rejected)
        if plan:
            plan.note = f"因「{failed.tool}」失败而重规划"
        return plan

    def answer(self, goal: str, state: str = "") -> str:
        """专门索要**答案原文**（不带 JSON 协议）。

        收尾与定向补救共用这一条路 —— 单一事实源：两处各写一遍提示词，
        迟早会漂移成"两种提问形态"，那两边都得重测。

        ★ 第四十五轮（§17-7q）：`state` 不再可扔。7o 定律（16/16 格）：
        K=0 格的交付**确定性**走本方法，而旧版 prompt 只有 goal ⇒
        记忆盲补救 ⇒ 基线交付。调用方（propose 的补救 / core 的收尾）
        必须把各自手里的 state 带进来；截断用 `self.state_chars`，
        与 `propose()` 拼提示词处同一个数 —— 否则又是两套真相。
        """
        prompt = f"目标：{goal}\n"
        if state:
            prompt += f"当前状态：\n{state[:self.state_chars]}\n"
            #: 忠实引用指令只跟在**状态块之后**（同 7q 的 state 约定）。
            prompt += _ANSWER_FIDELITY + "\n"
        prompt += "请直接给出最终答案。"
        return self.llm.complete(
            [Message("system", _ANSWER_SYS),
             Message("user", prompt)],
            max_tokens=self.max_tokens) or ""

    # ------------------------------------------------------------------
    def _extract(self, data: Any) -> tuple[list[Step], str, list[str]]:
        """返回 (可用步骤, 空的原因, 模型点名但未注册的工具)。

        为什么要把"原因"和"坏工具名"一起返回：
            只看一个空列表，你无法区分 "模型不会写 JSON"、"模型想要一个不存在的工具"
            与 "候选全是重复的" —— 而这三者的修法完全不同。实测就栽在这里：
            模型填了 `"tool": "None"`，日志只说"输出不可解析"，看起来像模型的锅，
            实际是工具目录里没有它需要的能力 + 没告诉它可以直接 finish。
        """
        if not isinstance(data, dict):
            return [], "empty", []
        raw = data.get("candidates") or data.get("steps") or []
        if not isinstance(raw, list):
            return [], "empty", []
        known = set(self.tools.names())
        out: list[Step] = []
        seen: set[str] = set()
        bad: list[str] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool", ""))
            if tool not in known:
                bad.append(tool or "(空)")
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
        if out:
            return out, "", bad
        if bad:
            return [], "unknown_tool", bad
        return [], "empty", []

    def _fallback(self, *, steps_used: int, goal: str = "") -> list[Step]:
        """确定性兜底：先尽量从记忆里捞，再直接收尾。

        两条硬约束（第二条是契约测试 G2 抓出来的真实缺陷）：
          ① 降级**仍然产出合法动作** —— 降级不等于瘫痪
          ② 产出的工具名**必须已注册** —— 否则降级路径会造出非法动作，
             后面 dispatch 才失败，等于把缺陷推迟到更难定位的地方
        """
        names = set(self.tools.names())
        if steps_used == 0 and "recall" in names:
            # ★ 第三十七轮（§17-7i）：这里原来写的是**字面量** `"goal"`。
            #   它与任何 fact 都零交集 ⇒ 每一次降级 recall 都落到 `recall()` 的
            #   第三级全表兜底（实测：7d–7g 的 **33 次** recall 全部返回 4 条）。
            #   兜底删掉之后它变成"永远空"⇒ 那条通道等于没接线，而日志仍写
            #   `recall ok=True`（"成功"的语义被掏空）。
            #   ⇒ 用**真正的目标文本**：这才是"先在记忆中查找与目标相关的既有结论"。
            _q = (goal or "goal").strip()[:64] or "goal"
            return [Step("先在记忆中查找与目标相关的既有结论", "recall",
                         {"query": _q}, "降级兜底：先检索")]
        if "finish" in names:
            return [Step("信息不足，直接给出当前最优答复", "finish",
                         {"answer": "（规划降级：模型输出不可解析，未能形成有效计划）"},
                         "降级兜底：收尾")]
        # 连 finish 都没注册（内核被裁剪过）⇒ 用任意可用工具，仍不产出非法名字
        for n in sorted(names):
            return [Step("降级兜底：调用唯一可用工具", n, {}, "降级兜底")]
        return []

    def stats(self) -> dict:
        causes = (self.json_fail + self.truncated + self.no_tool_fit
                  + self.empty_plan)
        return {
            "calls": self.calls, "degraded": self.degraded,
            "max_tokens": self.max_tokens,
            "degrade_rate": round(self.degraded / self.calls, 3) if self.calls else 0.0,
            # 分项：修法不同的分支必须分开报
            "json_fail": self.json_fail, "truncated": self.truncated,
            "no_tool_fit": self.no_tool_fit, "empty_plan": self.empty_plan,
            # 「本轮全灭 ⇒ 换形态再问」的次数。**不是降级**：它不改这次回答能不能用，
            # 只改下一次提问长什么样。放进 stats 才算有消费者（否则又一个装饰字段）。
            "reseek": self.reseek,
            "answer_retry_ok": self.answer_retry_ok,
            "answer_retry_fail": self.answer_retry_fail,
            "last_bad_tools": list(self.last_bad_tools)[:5],
            # 不变量：总分项数必须等于 degraded，否则说明有降级走了旁路（漏记）
            "cause_balances": causes == self.degraded,
        }


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
