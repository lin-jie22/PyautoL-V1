"""目标与价值层 —— 让内核**想做对的事**，而不是把能做的事都做一遍。

## 它不是什么

**不是评分器。** 本模块任何地方都不产生 `score: float`，也不回答
"我这次做得好不好"。那个问题必须由**外部**尺子回答（`mw/bench.py`），
内核自己回答它就是自评分 —— 那是 V5 的死因：一个永远说"我好"的信号，
最后 54/54 轮 `gain=0` 而每轮交付"全绿"。

## 它是什么

两条**能判的**否决，加一个**偏序**：

| 判据 | 问题 | 客观依据 |
|---|---|---|
| `answer` | 这个收尾陈述是在敷衍吗？ | 目标含"写/解释/列出"这类要求展开的动词，而答案长度不到目标的 1/5 |
| `irreversible` | 这一步撤得回来吗？目标授权了吗？ | 工具是否只读 + 参数里有没有不可逆方向动词 + 目标有没有正向授权 |
| `rank` | 先做哪个？ | 可撤销的排前面（`rank=0`），不可逆的排后面（`rank=1`） |

输出只有 `Judgement(allow, kind, reason)` 与一个整数 `rank`。
**整数不是分数**：它只用来排先后，不做加权平均，不参与"这轮几分"的读数。

## 为什么没有「这个动作是否偏题」这条判据（曾经有，删了）

第一版写了 `check_aim`：目标里的实体必须出现在动作的参数值里，否则否决。
试跑第一条就把它证伪了：

    goal="1+1"   answer="2"   ⇒ 判"答案未出现任何目标实体（['1']）" ⇒ 否决

`1+1` 的正确答案是 `2`，而 `2` 不可能是目标里的实体 —— **正确答案与目标的
字面重合是零，这是算题的常态**。同类误杀：`goal="把 [3,1,2] 排序"` 与
`write_file(content="def sort(x): ...")`（通用实现当然不含具体数组）。

这与缺陷 #12 是**同一个错误**：拿字面重合去判语义相关，结构上必然出错 ——
世界模型那边代价是 9/9 次失配恒红且反相关，这边代价是误杀合法动作。
所以删掉，而不是调阈值。**调阈值就是把尺子下移。**

## 那「想做对的事」靠什么

不是靠一个更聪明的检查器，而是三件彼此独立的事：

    1. 目标每回合都在上下文里（`core._state` 的 `[目标]` 段）—— 它不会被执行
       过程中产生的子目标顶替。V5 的 `hyp_exp` 无限重派，正是"开始服务
       '让这个假设成功'而不是'完成任务'"。
    2. 不可逆动作需要目标授权（本文件）—— 没人要你删，你就不许删。
    3. 外部尺子告诉它什么算对（`bench/`）—— 这是唯一能判"对错"的东西。

第 1 条已经在 `core.py` 里（`_state` 每回合重建 `[目标]`，`guard.check_goal`
每步审 mutating/external 动作）；第 2 条在本文件；第 3 条在内核**之外**。
三条都不在内核内部产生"我做得对"的判断 —— 那是刻意的。

## 与 `guard` 的边界（写死，不许重叠）

    guard  = 安全底线。阈值保守、不随任务变、宁可误拒。问"会不会造成伤害"。
    values = 效用与方向。随任务变、可解释。问"目标有没有让你这么做"。

同一件事在两边都可能被拦（例如 `rm -rf`），但**问的不是同一个问题**：
guard 问"这条命令本身危险吗"，values 问"目标让你删了吗"。
两处都保留是对的；把其中一处删掉，另一处就得同时承担两种语义 —— 那才是重复。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 目标里出现这些词 ⇒ 它要求**展开**，答案过短就是敷衍。
#: 只列"要求产出一段话"的动词；刻意不含"计算/输出/返回/排序"这类
#: 期望短答案的词 —— 那正是第一版误杀 `1+1 → 2` 的原因。
_EXPAND = re.compile(
    r"(写|撰写|列出|解释|说明|描述|总结|分析|论证|生成一段|"
    r"write|explain|describe|list|summar|analyz|draft)", re.I)

#: 目标里出现这些词 ⇒ 该方向被显式授权。**否定形式要排除**（"不要删除"）。
_AUTH = {
    "delete": re.compile(r"(删|移除|清理|delete|remove|rm\b)", re.I),
    "overwrite": re.compile(r"(覆盖|重写|替换|overwrite|replace)", re.I),
    "send": re.compile(r"(发送|提交|发布|上传|寄出|send|submit|publish|deploy)", re.I),
    "pay": re.compile(r"(支付|付款|转账|下单|purchase|pay\b)", re.I),
}
_NEG = re.compile(r"(不要|禁止|别|切勿|不许|avoid|don'?t|never)", re.I)

#: 不可逆方向 → 中文标签（日志与读数值用，测试也从此派生）
_IRREVERSIBLE: dict[str, str] = {
    "delete": "删除",
    "overwrite": "覆盖",
    "send": "外发",
    "pay": "支付",
}

#: 这些工具天然可撤销，不做不可逆审查。
#: ⚠️ 与 `guard` 里 `spec.mutating or spec.external` 的口径**同源**（契约钉住），
#:    否则会漂移成"guard 认为可撤销、values 认为不可撤销"的两套真相。
_READONLY_TOOLS = frozenset({"recall", "note", "finish", "read_file", "list_dir", "search"})

#: 动作参数里的**作用对象**：路径、通配符、以及看起来像标识符的片段。
#: 只用于不可逆动作的范围审查（见 `check_irreversible`），不做别的判断。
_TARGET = re.compile(r"(?:~|\.{0,2}/)[^\s;|&]*|[*?][^\s;|&]*|\b[\w.-]{2,}\.(?:py|txt|md|json|log|db|csv)\b")


@dataclass(frozen=True)
class Judgement:
    """一次价值判定。`rank` 是**偏序键**，不是分数：

        0  可做，且优先（可撤销）
        1  可做，但排在后面（不可逆，已授权）
        99 淘汰（未授权 / 敷衍）
    """
    allow: bool
    kind: str
    reason: str = ""
    rank: int = 0

    def as_line(self) -> str:
        tag = "允许" if self.allow else "否决"
        return f"[values:{self.kind}] {tag}" + (f" — {self.reason}" if self.reason else "")


ALLOW = Judgement(True, "-", "", 0)


def _authorized(goal: str, direction: str) -> bool:
    """目标是**正向**授权了这个方向吗（排除"不要删除"这种否定）。

    判据刻意简单到可以逐条复核：命中动词、且该动词前面 6 个字内没有否定词。
    """
    pat = _AUTH.get(direction)
    if pat is None:
        return True
    g = goal or ""
    for m in pat.finditer(g):
        head = g[max(0, m.start() - 6):m.start()]
        if not _NEG.search(head):
            return True
    return False


class Values:
    """价值层。**无阈值可调** —— 每个判据都只看传入的客观事实。

    为什么无阈值：一旦引入"偏离度 < 0.3 就放行"这类旋钮，它就会像 V5 的
    尺子那样被慢慢调到"什么都放行"。这里的判据是长度比与正则命中，
    不存在可调区间。
    """

    def __init__(self) -> None:
        self.judged = 0
        self.denied = 0
        #: 每个判据各自拦了多少 —— 一个"总拦截数"无法归因，读数必须分项。
        self.by_kind: dict[str, int] = {}
        #: 出现过的不可逆动作数（**不是**否决数）。用来回答"这轮差点动了什么"。
        self.irreversible_seen = 0

    # ------------------------------------------------------------------
    def reversibility(self, tool: str, args: dict) -> int:
        """0 可撤销 / 1 写但可回滚 / 2 不可逆。

        只看两样客观事实：工具名、参数里有没有指向不可逆方向的动词。
        **不猜** LLM 的意图 —— 猜意图的判据无法复核。
        """
        if tool in _READONLY_TOOLS:
            return 0
        blob = " ".join(str(v) for v in (args or {}).values())
        for direction in _IRREVERSIBLE:
            if _AUTH[direction].search(blob):
                return 2
        return 1

    # ------------------------------------------------------------------
    def check_answer(self, *, answer: str, goal: str) -> Judgement:
        """收尾陈述是否在**敷衍**。只判"说得够不够"，**不判对错**。

        唯一判据：目标要求展开（含"写/解释/列出"…），而答案长度不到目标的 1/5。

        ⚠️ 1/5 不是"感觉合适"，而是为了避开第一版的两类误杀：
            `goal="把 [3,1,2] 排序后输出"`（14 字）不触发（无展开动词），
            所以 `answer="1 2 3"` 通过 —— 它本来就该通过，答案没错，只是短。
            而 `goal="写一段 python 快排"` + `answer="好"` 会触发，那是敷衍。
        判"对不对"永远不在这里 —— 那需要外部尺子。
        """
        a = (answer or "").strip()
        if not a:
            return Judgement(False, "answer", "空答案", 99)
        if _EXPAND.search(goal) and len(a) * 5 < len(goal):
            return Judgement(
                False, "answer",
                f"目标要求展开（{len(goal)} 字），答案只有 {len(a)} 字", 99)
        return ALLOW

    def check_irreversible(self, *, tool: str, args: dict, goal: str) -> Judgement:
        """不可逆动作必须有目标里的**显式**授权，**且作用范围不得越界**。

        这是"想做对的事"最实的一条：**目标没让你删，你就不许删。**

        两道，缺一不可：

            1. 方向授权  —— 目标里有"删/覆盖/发送/支付"这类词（且不是否定式）
            2. 范围授权  —— 动作的**作用对象**（路径/通配符）在目标里出现过

        第 2 条是实测逼出来的（2026-10-17 消融）：
        目标 `"分析并清理 python 截断问题的临时文件"` 里有"清理"二字，
        于是 `rm -rf ~` 通过了第 1 条 —— **方向被授权了，范围没有**。
        目标要清理的是"临时文件"，动作删的是整个 home。
        这类越界在真实系统里是最常见的破坏方式：**意图对，作用域错**。

        ⚠️ 第 2 条只对**不可逆**动作生效，所以误杀面很小：
        它不会去审查 `write_file(content=...)` 里写了什么，只审查
        "你要删/覆盖/发送的**东西**，目标点名了吗"。
        """
        level = self.reversibility(tool, args)
        if level < 2:
            return ALLOW
        self.irreversible_seen += 1
        blob = " ".join(str(v) for v in (args or {}).values())
        for direction, label in _IRREVERSIBLE.items():
            if not _AUTH[direction].search(blob):
                continue
            if not _authorized(goal, direction):
                return Judgement(
                    False, "irreversible",
                    f"{label}类动作，但目标未授权该方向 ⇒ 改成可预览的等价动作", 99)
            targets = {t for t in _TARGET.findall(blob) if t.strip()}
            if targets and not any(t in goal for t in targets):
                return Judgement(
                    False, "irreversible",
                    f"目标授权了{label}，但作用对象 {sorted(targets)[:3]} 不在目标里"
                    f" ⇒ 范围越界", 99)
        # 已授权 ⇒ 仍排在后面：先做可撤销的探索，再动不可逆的。
        return Judgement(True, "irreversible", "不可逆动作，方向与范围均已授权", 1)

    # ------------------------------------------------------------------
    def judge(self, *, tool: str, args: dict, goal: str,
              is_finish: bool = False) -> Judgement:
        """入口。收尾动作只查"敷衍"，其余动作只查"不可逆"。

        ⚠️ 这里**不**做"动作是否偏题"的判断 —— 理由见模块 docstring：
        用字面重合判语义相关，结构上必然错（与缺陷 #12 同构）。
        偏题这件事已由 `core._state` 的 `[目标]` 常驻 + `guard.check_goal` 承担。
        """
        self.judged += 1
        j = (self.check_answer(answer=str((args or {}).get("answer", "")), goal=goal)
             if is_finish else self.check_irreversible(tool=tool, args=args, goal=goal))
        if not j.allow:
            self.denied += 1
            self.by_kind[j.kind] = self.by_kind.get(j.kind, 0) + 1
        return j

    # ------------------------------------------------------------------
    def report(self) -> dict:
        return {
            "judged": self.judged,
            "denied": self.denied,
            "by_kind": dict(self.by_kind),
            # 分母同时给出：1 次否决 / 3 次判定，和 1 次否决 / 300 次判定不是一回事
            "deny_rate": round(self.denied / self.judged, 3) if self.judged else None,
            "irreversible_seen": self.irreversible_seen,
        }
