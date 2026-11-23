"""自省（reflect）—— 从一次失败里写出**可执行的修订说明**。

## 它补的是哪一格

§301（V230）现场：`why` 逐字点名了 `assert parse_duration('1h30m') == 5400` 与
`assert parse_duration('10m') == 600`，那两行就在同一个 prompt 里 —— 而 `t01`
**一格没修**，它只修了「体里一眼看得出的错」（错常量、少一行排序）。

这不是模型笨。Reflexion（Shinn et al. 2023）的消融把这件事说清楚了：

    「只给失败轨迹（episodic memory）而不写反思」⇒ 性能**不提升**；
    多出来的 8% 全部来自**写反思**那一步。

我们此前有 Actor（求解）与 Evaluator（打分 + `why`），**缺 Reflector**。
`why` 是**症状**（`⇒ AssertionError`），Reflector 的产物是**根因 + 动作**。
两者差一步推理，而这一步正是「自省 → 累积」那条边。

## 三条边界

1. **Reflector 不产出代码。** 只产出自然语言修订说明。让它写代码等于在账本之外
   再造一条求解路径 —— 那样「下一版变好」到底是谁改的，就不可归因了。
   违约（输出含代码围栏或 `def `）要**计数**，⛔ 不许静默裁剪后照用。
2. **说明必须绑定它读到的那份代码。** `based_on_sha` 记下代码指纹；注入时校验
   账本里那份的 sha 与之相同，不同就**不注入**并计 `miss`。
   理由：`freeze_pass` 会把旧输入冻在账本里（§301.6），而反思是针对**新产出**写的
   ⇒ 不校验就会把「针对 A 的建议」贴在 B 上（纪律 152 的同型陷阱）。
3. **纯逻辑，不调模型。** 这里只构造 prompt、解析输出、存取。
   真正的那一次调用在调用方 —— 这样契约可以对它单独钉「发什么」「收什么」，
   也不会让内核在跑题过程中多出一次隐式调用。

## 为什么默认 off

`why` 已经在 prompt 里（`[外部评审]`），再加一段**衍生物**是改产品行为。
必须先把「加了这一段，分数动没动」量出来，才能决定默认值。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

#: 修订说明的长度上限。太短装不下根因，太长会把 `[上版代码]` 挤出预算。
#: 取 600：`why` 上限是 400，说明比它长一点是合理的（要写根因 + 动作）。
MAX_LESSON = 600

#: 代码围栏 / 函数定义 —— Reflector 一旦写出这两个，就说明它越界去解题了。
_CODE_MARK = re.compile(r"```|\bdef\s+\w+\s*\(|\bclass\s+\w+\s*[\(:]")

#: 压掉模型爱加的开场白（"好的，以下是修订说明："）。只删**行首**这一种形态：
#: 删正文里的同类子串会把句子剪断（§123 的同型错误）。
_PREAMBLE = re.compile(
    r"^\s*(?:好的[，,]?\s*)?(?:以下|下面)?\s*(?:是|为)?\s*(?:修订说明|修改建议|反思|分析)\s*[:：]\s*",
)


@dataclass(frozen=True)
class Lesson:
    """一条针对**某一份具体代码**写的修订说明。"""

    scorer_sha: str
    suite_sha: str
    task_id: str
    #: 这份说明是针对哪一份代码写的（`sha256(answer)` 前 16 位）。
    #: ⛔ 没有它，说明会被贴到另一份代码上 —— 那等于给错的方向。
    based_on_sha: str
    lesson: str
    #: 写这条说明时那份代码的分数与格位（证据：说明是从哪个失败里长出来的）。
    at_score: float = 0.0
    at_vec: str = ""

    def key(self) -> str:
        return f"{self.scorer_sha}|{self.suite_sha}|{self.task_id}"


def sha_of(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 实测返回值 —— 把「断言失败」升级成「断言失败，实得 X」
#
# ⛔ 边界：只执行**已经落盘的那份答案**与**该题自己声明的断言**。
#    不执行运行期的任何外部输入 —— 这两样都是本轮之前就存在的事实，
#    不是"让内核去跑外面的东西"。它算的是**诊断信息**，不产生分数。
# ---------------------------------------------------------------------------
def _split_assert(test_src: str) -> tuple[str, str] | None:
    """把 `assert <lhs> == <rhs>` 拆成两段源码。不是这个形状 ⇒ `None`。"""
    try:
        tree = ast.parse(test_src)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        t = node.test
        if isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], ast.Eq):
            try:
                return ast.unparse(t.left), ast.unparse(t.comparators[0])
            except Exception:                       # noqa: BLE001
                return None
    return None


def failed_actuals(code: str, tests: Sequence[str], *, limit: int = 6) -> list[str]:
    """对每条**失败**的断言，给出它实得什么。

    返回形如 `["parse_duration('10m') 实得 None（期望 600）", ...]`。
    通过的断言不出现 —— 它们没有诊断价值，还会把 prompt 撑长。

    ⚠️ 断言不是 `==` 形状时**退回异常名**（`⇒ ValueError`），⛔ 不许凭空编一个值：
       一个编造的实际值比没有更糟，它会把 Reflector 引向不存在的分支。
    """
    out: list[str] = []
    for t in tests:
        ns: dict = {}
        passed = False
        try:
            exec(compile(code, "<answer>", "exec"), ns)     # noqa: S102
            exec(compile(t, "<test>", "exec"), ns)          # noqa: S102
            passed = True
        except AssertionError:
            # ⚠️ 断言失败是**预期分支**（这个函数的整个目的就是描述失败），
            #    不是兜底：下面要继续取它的实际值。⛔ 不写 `pass` ——
            #    J10 把「except 体是单独的 pass」判成静默兜底，那条判据是对的，
            #    所以这里的意图必须**写在代码里**（`passed = False`）而不是留空。
            passed = False
        except Exception as e:                              # noqa: BLE001
            out.append(f"{t.strip()} ⇒ 抛 {type(e).__name__}")
            if len(out) >= limit:
                break
            continue
        if passed:
            continue                                        # 通过 ⇒ 不记（没诊断价值）
        pair = _split_assert(t)
        if pair is None:
            out.append(f"{t.strip()} ⇒ AssertionError（拿不到实际值）")
        else:
            lhs, rhs = pair
            ns2: dict = {}
            try:
                exec(compile(code, "<answer>", "exec"), ns2)  # noqa: S102
                got = eval(compile(lhs, "<lhs>", "eval"), ns2)  # noqa: S307
                out.append(f"{lhs} 实得 {got!r}（期望 {rhs}）")
            except Exception as e:                          # noqa: BLE001
                out.append(f"{lhs} ⇒ 抛 {type(e).__name__}: {e}")
        if len(out) >= limit:
            break
    return out


def build_prompt(goal: str, code: str, why: str, actuals: Sequence[str] = ()) -> str:
    """Reflector 的入参。

    ⚠️ 只给**这一段代码**与**这一次失败**，不给别的题、不给历史 ——
       多给一样就多一个自变量，「说明有没有用」就不可归因。
    ⛔ 明确禁止输出代码：那不是格式洁癖，是**可归因**的前提（见模块头边界 1）。

    `actuals` 是**实测返回值**（见 `failed_actuals`）。为什么要它（§303）：
    `why` 只有「断言失败」——`assert f('10m') == 600 ⇒ AssertionError` 里
    **没有实际值**。V231 现场量到：缺这一项时，7b 的 Reflector 把
    「`m` 分支把 `parts[0]` 当小时、`int('')` 抛 ValueError 被吞成 None」
    误诊成「程序误认为含 `h` 分隔符」，然后模型**忠实执行了这个错误诊断**
    （只做了等价重构，分数一格没动）⇒ 说明有牙，但牙咬错了地方。
    这正是 §16 组纪律 135 说的：「真实账本的诊断粒度决定自省上限」。
    """
    parts = [
        "你在审查一段 Python 函数实现。它的测试没有全部通过。\n",
        f"【题目】\n{goal}\n",
        f"【上一版代码】\n{code}\n",
        f"【测试结果】\n{why}\n",
    ]
    if actuals:
        parts.append("【实测返回值】\n" + "\n".join(f"- {a}" for a in actuals) + "\n")
        parts.append(
            "请先根据实测返回值判断：这个返回值说明代码走到了哪一条分支、"
            "在哪里产生了它。\n")
    parts.append(
        "请用 3~5 行中文写一段**修订说明**，包含两件事：\n"
        "1) 失败的根本原因在代码的哪一处逻辑；\n"
        "2) 下一版应该把那一处改成什么语义（用自然语言描述，不要写代码）。\n"
        "严格要求：不要输出任何代码、不要粘贴测试用例原文、不要复述整个函数。\n"
        "直接给结论，不要开场白。")
    return "".join(parts)


def parse_lesson(text: str) -> tuple[str, bool]:
    """把 Reflector 的原始输出清成一段说明。返回 `(lesson, violated)`。

    `violated=True` 表示它写出了代码 —— 调用方**必须**计数，
    ⛔ 不许当成「格式小问题」静默用掉：那意味着这一轮的「变好」不是通道给的。
    """
    src = (text or "").strip()
    violated = bool(_CODE_MARK.search(src))
    src = _PREAMBLE.sub("", src, count=1).strip()
    return src[:MAX_LESSON], violated


# ---------------------------------------------------------------------------
# 落盘：旁挂一份，不改评审账本的 schema
#
# 为什么不塞进账本行：账本行是**证据**（打分器的原始输出 + 那一版答案），
# 反思是**派生物**。混在一行里，读账本的人就分不清哪半是打分器说的、
# 哪半是模型自己说的 —— 而这条区分正是「他者反馈」与「自省」的分界。
# ---------------------------------------------------------------------------
def save_lessons(path: Path | str, items: list[Lesson]) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    #: 同键覆盖 —— 与账本同一条去重纪律（键覆盖，不是插完再清理）。
    merged: dict[str, Lesson] = {}
    for it in load_lessons(p):
        merged[it.key()] = it
    for it in items:
        merged[it.key()] = it
    with open(p, "w", encoding="utf-8") as f:
        for it in merged.values():
            f.write(json.dumps({
                "scorer_sha": it.scorer_sha, "suite_sha": it.suite_sha,
                "task_id": it.task_id, "based_on_sha": it.based_on_sha,
                "lesson": it.lesson, "at_score": it.at_score, "at_vec": it.at_vec,
            }, ensure_ascii=False) + "\n")
    return len(merged)


def load_lessons(path: Path | str) -> list[Lesson]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[Lesson] = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
            out.append(Lesson(
                scorer_sha=d["scorer_sha"], suite_sha=d["suite_sha"],
                task_id=d["task_id"], based_on_sha=d.get("based_on_sha", ""),
                lesson=d.get("lesson", ""), at_score=float(d.get("at_score", 0.0)),
                at_vec=d.get("at_vec", "")))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return out


def index_lessons(items: list[Lesson]) -> dict[str, Lesson]:
    return {it.key(): it for it in items}


# ===========================================================================
# 提议-验证-采纳（§304）
#
# V231/V232 两轮把「自然语言说明」这条路量到底了：
#   · V231：`why` 只有 `⇒ AssertionError` ⇒ 7b 的诊断**错**（说「误认为含 h」），
#           模型**忠实执行错诊断**（只做等价重构）⇒ 4/6 不动；
#   · V232：补上实测值 ⇒ 诊断**更错**（跑偏到代码里并不存在的 `SyntaxError`），
#           模型干脆回到**照抄** ⇒ 4/6 不动。
# ⇒ 瓶颈不在「通道能不能把话递过去」，而在**那句话对不对**，而 7b 的自我诊断不可靠。
#
# 所以不再问「它说得对不对」，改问「它给的东西能不能过判分」：
#   ① 让它提出一个**候选实现**（允许写代码）；
#   ② 用**该题自己声明的断言**离线判分（零模型、零网络）；
#   ③ **只有严格更好才采纳**（promote-after-proof），否则丢弃并计数。
#
# ⛔ 这不是"让 Reflector 去解题"：它只在**已有一份能跑的失败实现**之后被调用一次，
#    它的产出必须**过同一个判分器**才能进账本。提议不成立时与没提议完全等价。
# ===========================================================================
def build_proposal_prompt(goal: str, code: str, why: str,
                          actuals: Sequence[str] = ()) -> str:
    """让模型给一份**完整可运行**的候选实现。

    ⚠️ 与 `build_prompt` 的差别是刻意的：那一版**禁止**写代码（要的是诊断），
       这一版**要求**写代码（要的是产物）。两者不能混用 —— 混了就没有对照。
    """
    parts = [
        "下面这个 Python 函数没有通过全部测试。请给出**修正后的完整函数**。\n",
        f"【题目】\n{goal}\n",
        f"【当前代码】\n{code}\n",
        f"【测试结果】\n{why}\n",
    ]
    if actuals:
        parts.append("【实测返回值】\n" + "\n".join(f"- {a}" for a in actuals) + "\n")
    parts.append(
        "要求：\n"
        "1) 只输出一个 ```python 代码块，里面是**完整的**函数定义（不要省略任何行）；\n"
        "2) 保持函数名与签名不变；\n"
        "3) 不要解释，不要输出任何其他文字。")
    return "".join(parts)


_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)


def parse_proposal(text: str) -> tuple[str, bool]:
    """从模型输出里抽出候选函数源码。返回 `(code, fenced)`。

    ⚠️ `fenced=False` 表示它没按格式给代码块 —— 这时**整段**当代码用，
       但调用方要计数：格式不守与「给不出东西」不是一回事。
    ⛔ 抽不出来就返回空串，由调用方当成「没有提议」（**不许**用原文兜底：
       一段夹着解释文字的东西喂给判分器，只会得到 SyntaxError，那不是它的能力）。
    """
    m = _FENCE.search(text or "")
    if m:
        return m.group(1).strip(), True
    src = (text or "").strip()
    if re.search(r"^\s*def\s+\w+\s*\(", src, re.M):
        return src, False
    return "", False


def verify(code: str, tests: Sequence[str]) -> int:
    """用该题自己声明的断言离线判分，返回**通过条数**。

    ⛔ 与打分器同口径：每条断言在**全新命名空间**里跑（否则前一题的变量会漏进来）。
    ⛔ 不返回分数比例：这里只需要「更好/不更好」，用整数比较避免浮点误差参与判据。
    """
    if not code.strip():
        return 0
    n = 0
    for t in tests:
        ns: dict = {}
        try:
            exec(compile(code, "<cand>", "exec"), ns)      # noqa: S102
            exec(compile(t, "<test>", "exec"), ns)         # noqa: S102
            n += 1
        except Exception:                                   # noqa: BLE001
            continue
    return n


def pick(index: dict[str, Lesson], *, scorer_sha: str, suite_sha: str,
         task_id: str, answer: str) -> tuple[str, str]:
    """取**当前这份代码**能用的说明。返回 `(lesson, reason)`。

    `reason` 只有三种，⛔ 不许合并：三者对应三种完全不同的处置 ——

        "hit"     说明在、且是针对这份代码写的 ⇒ 用它
        "stale"   说明在，但它是针对**另一份**代码写的 ⇒ 不用（`freeze_pass` 会造出这种）
        "absent"  这道题根本没有说明（例如上一轮是满分、没有失败） ⇒ 不用

    合并成「没命中」会让「冻结把输入换了」和「本来就没失败」看起来一样。
    """
    it = index.get(f"{scorer_sha}|{suite_sha}|{task_id}")
    if it is None or not it.lesson:
        return "", "absent"
    if it.based_on_sha and it.based_on_sha != sha_of(answer):
        return "", "stale"
    return it.lesson, "hit"
