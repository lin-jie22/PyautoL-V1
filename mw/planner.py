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

from .llm import ANSWER_MARK, LLM, Message, parse_json, split_answer


@dataclass
class Step:
    intent: str
    tool: str
    args: dict
    rationale: str = ""

    def as_line(self) -> str:
        return f"{self.tool}({self.args}) — {self.intent}"


def action_signature(tool: str, args: Any) -> str:
    """动作签名 —— 判断"是不是同一个动作"的**唯一**判据。

    为什么必须是单一事实源：内核用它判"这个动作执行过了、不许原地打转"，
    规划器用它判"候选之间是不是不同做法"。两处口径若不一致，
    "已试过"就会形同虚设 —— 一个用 `repr`、一个用排序，同样的动作会被当成两个。

    ⚠️ 实测背景：内核曾在一次任务里把 `recall(同一个 query)` 重派了 5 次，
    烧掉 120s 预算直到 `budget_exhausted`。`consecutive_failures` 拦不住它 ——
    因为那几次 `recall` **全都 ok=True**。**重复的"成功"不是进展**，
    而当时内核里没有任何机制表达这句话。
    """
    try:
        items = sorted(dict(args).items())
    except TypeError:                     # 值之间有不可比较类型时按 key 排
        items = sorted(dict(args).items(), key=lambda kv: str(kv[0]))
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


class Planner:
    def __init__(self, llm: LLM, tools: Any, *, max_tokens: int = 2048) -> None:
        self.llm = llm
        self.tools = tools
        #: 单次规划调用的 token 上限。**从 config 注入**，不在这里写死 ——
        #: 实测教训：写死 700 导致含代码的 JSON 被截断，却被误读成"模型不会写 JSON"。
        self.max_tokens = int(max_tokens)
        self.calls = 0
        self.degraded = 0
        # ── 降级原因**分项计数**，不许合并成一个 degraded ──────────────────
        # 理由（V5 换来的通则）：修法不同的分支合并，就再也分不清该修哪里。
        #   json_fail      没产出合法 JSON（也不是截断形状）⇒ 改提示词/解析器
        #   truncated      开了头但括号不闭合          ⇒ ★调大 max_tokens
        #   no_tool_fit    想要的能力不在工具目录里     ⇒ 加工具/改工具描述（★实测命中）
        #   empty_plan     合法 JSON 但没有可用候选     ⇒ 改提示词
        # ⚠️ 刻意**没有** "all_duplicate" 这个计数器：同一次响应里第一个候选永远
        #    不是重复，所以"全是重复"不可达 ⇒ 那会是一个永不增长的装饰字段。
        #    重复只在响应内部被消掉（G4），不足以构成一次降级。
        self.json_fail = 0
        self.truncated = 0
        self.no_tool_fit = 0
        self.empty_plan = 0
        #: 定向补救（"选了 finish 却没给答案"）：成功 / 失败分开计。
        #: 它**不算降级**（计划本身是合法的，只是缺一块载荷），所以不进 cause_balances。
        self.answer_retry_ok = 0
        self.answer_retry_fail = 0
        self.last_error = ""
        #: 模型点名但未注册的工具名 —— 失败可归因的最小证据
        self.last_bad_tools: list[str] = []

    # ------------------------------------------------------------------
    def propose(self, *, goal: str, state: str, history: Sequence[str],
                n: int = 1, guidance: str = "",
                blocked: Sequence[str] = ()) -> Plan:
        """产出 n 个候选步骤（各不相同）。n>1 就是 test-time compute 的落点。

        `blocked` 是**机器生成**的"已尝试过的动作签名"清单。为什么不只靠
        `history` 里的自然语言：实测里模型会无视"已失败/已试过：…"那几行，
        然后把同一个 `recall` 再提一遍。给签名是精确的 —— 它可以直接比对。
        """
        self.calls += 1
        avoid = "\n".join(f"  · 已失败/已试过：{h}" for h in list(history)[-6:])
        banned = ("\n已执行过、**禁止**再提出的动作（tool|args）：\n"
                  + "\n".join(f"  · {b}" for b in list(blocked)[-12:])
                  ) if blocked else ""
        prompt = (
            f"目标：{goal}\n"
            f"当前状态：\n{state[:1200]}\n"
            f"{('已知易错点：' + guidance) if guidance else ''}\n"
            f"{avoid}{banned}\n"
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
                    steps_used=len(history))
            self.json_fail += 1
            # 把原文片段带进 note：只说"不可解析"等于什么都没说 ——
            # 上一版就是因此把"模型填了 tool=None"误读成"模型不会写 JSON"。
            # 失败必须自解释，否则每次都要重跑一遍去抓原文。
            return self._degrade(
                "模型输出不是合法 JSON（⇒ 改提示词/解析器）；原文片段: "
                + repr(raw[:200]),
                steps_used=len(history))
        steps, why, bad = self._extract(data)
        self.last_bad_tools = bad
        if not steps:
            if why == "unknown_tool":
                self.no_tool_fit += 1
                return self._degrade(
                    f"模型想要的工具未注册: {bad[:3]}（能力缺口 ⇒ 加工具/改工具描述）",
                    steps_used=len(history))
            self.empty_plan += 1
            return self._degrade("合法 JSON 但没有可用候选（⇒ 改提示词）",
                                 steps_used=len(history))
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
                    got = self.answer(goal)
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

    def _degrade(self, note: str, *, steps_used: int) -> Plan:
        """降级出口唯一 —— 保证 self.degraded 与分项计数**同源**，不会两处漂移。"""
        self.degraded += 1
        return Plan(self._fallback(steps_used=steps_used), degraded=True, note=note)

    def replan(self, *, goal: str, failed: Step, error: str,
               history: Sequence[str], n: int = 1,
               blocked: Sequence[str] = (), guidance: str = "") -> Plan:
        """失败后重规划。**必须带上错误原文**，否则等于原地打转。

        ⚠️ `guidance` 必须一起转发（2026-10-17 接线审计抓到的断点）：
        原来这里调 `propose` 时**不传** guidance ⇒ 走默认空串 ⇒
        **恰恰是"刚栽过一次"的重规划拿不到"已知易错点"**。
        那是逻辑上最荒谬的一处断点：guidance 记的是"哪儿容易栽"，
        而重规划正是"刚栽了"的那一次。断在这里，等于教训只在第一次尝试时有用。
        """
        augmented = list(history) + [f"{failed.as_line()} ⇒ 失败: {error[:200]}"]
        plan = self.propose(goal=goal, state="(上一步失败，见下)", history=augmented,
                            n=n, blocked=blocked, guidance=guidance)
        if plan:
            plan.note = f"因「{failed.tool}」失败而重规划"
        return plan

    def answer(self, goal: str) -> str:
        """专门索要**答案原文**（不带 JSON 协议）。

        收尾与定向补救共用这一条路 —— 单一事实源：两处各写一遍提示词，
        迟早会漂移成"两种提问形态"，那两边都得重测。
        """
        return self.llm.complete(
            [Message("system", _ANSWER_SYS),
             Message("user", f"目标：{goal}\n请直接给出最终答案。")],
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
        causes = (self.json_fail + self.truncated + self.no_tool_fit
                  + self.empty_plan)
        return {
            "calls": self.calls, "degraded": self.degraded,
            "max_tokens": self.max_tokens,
            "degrade_rate": round(self.degraded / self.calls, 3) if self.calls else 0.0,
            # 分项：修法不同的分支必须分开报（V5 通则）
            "json_fail": self.json_fail, "truncated": self.truncated,
            "no_tool_fit": self.no_tool_fit, "empty_plan": self.empty_plan,
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
