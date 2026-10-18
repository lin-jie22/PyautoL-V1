"""经验抽象器 —— 把 episodic 原文蒸馏成**只含自然语言**的经验。

## 为什么要这个模块（第十六轮的动机，不是洁癖）

第十五轮跑出本项目第一个正的跨任务增益 Δ=+0.2000（隔离 0.5600 → 累积 0.7600），
但排序对照把它解剖了：把受益的 `z03_parse_query` 挪到**第一题**，它立刻掉回
0.00，mean 回到隔离基线。

⇒ 那个增益属于**位置**（前面有几道题做完了），不属于那道题。

当时的机制证据：累积记忆库存的是一组 `(goal, finish)`，每条 `finish` 带着
**上一道题的完整正确答案**。检索到它 ⇒ 等于给下一道题开卷一段同结构的范文。

这不是学习，是**照抄**。它与"何种差别在于：

    答案    只能迁移到同一道题（trivial）
    经验    "值里出现 % 时要先做替换表，不能链式 replace" —— 能迁移到
            函数名、数据格式完全不同的另一道题

## 本模块做什么

对每条 episode，让 LLM 把它蒸馏成**一句话的可迁移经验**，并物理地挡住代码：

    ┌ 含 ``` 代码块                ⇒ dropped_code（拒收，计数）
    ├ 含 Python 语法形态的行        ⇒ dropped_code
    ├ 空 / 太短                    ⇒ dropped_empty
    └ 其余                         ⇒ 写进语义记忆，source=ABSTRACT_SOURCE

⛔ 拒收一律**计数并归因**，不静默丢弃（无静默兜底纪律）。一个你看不见的丢弃，
等价于"凭空认领了一条被你悄悄扔掉的证据"。

## ⛔ 刻意**不做**的事

1. **不**verifying 经验对不对 —— 那是外部打分器的工作（"变好"要有外部依据，
   整个项目的立论）。本模块只负责"提炼得干净不干净"。
2. **不**读答案去加权。.higher-scoring episodes 并不获得更高 confidence ——
   那会让"被打分者"按分数给自己挑例子，绕回 V5 的自评分陷阱。
3. **不**把原文留下痕迹。`strip_code` 是**删除**，不是"标注为代码"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .llm import LLM, Message
from .memory import MemoryStore

#: 抽象条目的 source。**所有下游过滤只看这个值**，不许看文本长相。
ABSTRACT_SOURCE: str = "abstract"

#: `Core(memory_mode=...)` 取这个值 ⇒ 只读抽象层。
#: 两个常量放在一起是因为它们必须**同时**改：source 是写侧承诺的事实，
#: mode 是读侧的口径，两边分开声明就会在某一次改动后悄悄对不上。
ABSTRACT_MODE: str = "abstract"

#: 一句话经验的最大字符数。超过就截断 —— 太长的话它多半在复述原文。
MAX_ABSTRACT_CHARS: int = 160

#: 低于这个长度说明模型什么都没说（"好的。" / "明白了。"）
MIN_ABSTRACT_CHARS: int = 8

#: 下划线标识符（函数名/变量名形态）。
#: ⛔ 这是**实测抓到**的泄漏：抽象第 1 条写成了「调用 deep_get 时，先确认…」
#: —— `deep_get` 就是训练题 z04 的函数名。那不是经验，那是**点名**：
#: 假如下一批考试题里恰好没有 deep_get，它就迁移不过去。
#: 真正的经验应该写成"按路径逐段下探时，先确认路径字符串的格式"。
_IDENTIFIER = re.compile(r"\b[A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_]+\b")

_CODE_FENCE = re.compile(r"```.*?```", re.S)
#: 行级 Python 形态。宁可多挡也不会漏：一条漏掉的答案就足以让整场对照作废。
_CODE_LINE = re.compile(
    r"^\s*(def |class |import |from \w+ import |return |if __name__|lambda )"
    r"|^\s*\w+\s*=\s*[\w\.\[\(]"
    r"|^\s*for \w+ in "
    r"|^\s*while "
    r"|`[^`]*`",
)
#: 明确要剔除的"这不是经验"的开场白
_FILLER = re.compile(r"^(好的|明白|收到|以下是|这条经验是|经验：|答案)", re.M)


def strip_code(text: str) -> str:
    """删掉代码块，返回剩下的自然语言。

    注意：是**删除**，不是替换成占位符或标注。留下 "[代码已省略]" 这类标记
    等于告诉读者"这里原本有一段（可能有用的）代码"，那也会被当成信号。
    """
    out = _CODE_FENCE.sub(" ", text)
    kept = [ln for ln in out.splitlines() if ln.strip() and not _CODE_LINE.search(ln)]
    return re.sub(r"\s+", " ", " ".join(kept)).strip()


def looks_like_code(text: str) -> bool:
    """这条"经验"里还带着可执行的形态吗？带着 ⇒ 它可能是答案的变体。"""
    if "```" in text:
        return True
    return any(_CODE_LINE.search(ln) for ln in text.splitlines() if ln.strip())


@dataclass
class DistillStats:
    """蒸馏的可归因读数。**每个拒收都有名字**，没有沉默的总数。"""

    considered: int = 0
    kept: int = 0
    dropped_code: int = 0        # 含代码块/可执行形态 ⇒ 可能是答案
    dropped_identifier: int = 0  # 含下划线标识符 ⇒ **点名某道题**而不是提炼经验
    dropped_empty: int = 0       # 模型什么都没说
    dropped_invalid: int = 0     # 既不算空、也不算含代码，但整理后不成句
    failed_llm: int = 0          # LLM 调用异常/返回不符(expected str)

    #: 被拒收的样例原文（截断保存，含"代码"与"点名"两类），供人工复查。
    #: ⛔ 不留的话，"漏判"永远查不出来 —— 你只知道它被拒了，不知道拒掉了什么。
    examples_dropped_code: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "considered": self.considered,
            "kept": self.kept,
            "dropped_code": self.dropped_code,
            "dropped_identifier": self.dropped_identifier,
            "dropped_empty": self.dropped_empty,
            "dropped_invalid": self.dropped_invalid,
            "failed_llm": self.failed_llm,
        }

    def as_line(self) -> str:
        return (f"蒸馏 输入={self.considered} 采纳={self.kept} "
                f"（含代码={self.dropped_code} 点名={self.dropped_identifier} "
                f"空={self.dropped_empty} "
                f"其它={self.dropped_invalid} LLM失败={self.failed_llm}）")


_DISTILL_SYSTEM = (
    "你是经验提炼器。给你一段 AI 智能体自己过去的执行记录，"
    "请把它提炼成**一条可以迁移到别的任务上的经验**。\n"
    "要求：\n"
    "1. 一句话，以动词开头，形式如『遇见到 X 时，先 Y』或『做 X 之前检查 Y』。\n"
    "2. **绝对不要写代码**、不要出现函数名、变量名、代码块。\n"
    "3. 不要复述任务的答案，只写**下次该怎么避免同一个坑**。\n"
    "4. 不要写『好的』这类客套话，直接给那一句经验。\n"
    "5. 如果这段记录里没有任何可迁移的教训，就只输出一个词：NONE"
)


def distill(memory: MemoryStore, llm: LLM, *,
            sources: Sequence[str] | None = None,
            max_tokens: int = 160,
            keep_code_examples: int = 3) -> DistillStats:
    """把 episodic 原文档蒸馏成抽象经验，写进同一份记忆的语义层。

    `sources` 为 None ⇒ 取**所有** kind（工具输出、think、finish 都算原材料）。
    返回 DistillStats —— ⛔ 调用方**必须**把它念出来，不许只看"成功条数"。
    """
    st = DistillStats()
    for ep in _source_episodes(memory, sources):
        st.considered += 1
        raw = str(getattr(ep, "content", "") or "").strip()
        if not raw:
            st.dropped_empty += 1
            continue
        try:
            out = llm.complete(
                [Message("system", _DISTILL_SYSTEM),
                 Message("user",
                         f"执行记录（kind={getattr(ep, 'kind', '?')}）：\n"
                         f"{raw[:1200]}")],
                max_tokens=max_tokens, temperature=0.0,
            )
            text = str(getattr(out, "content", out)).strip()
        except Exception:                     # noqa: BLE001 — 见 README 边界
            st.failed_llm += 1
            continue
        if not text or text.upper().startswith("NONE"):
            st.dropped_empty += 1
            continue
        if looks_like_code(text):
            st.dropped_code += 1
            if len(st.examples_dropped_code) < keep_code_examples:
                st.examples_dropped_code.append(text[:120])
            continue
        if _IDENTIFIER.search(text):
            st.dropped_identifier += 1
            if len(st.examples_dropped_code) < keep_code_examples:
                st.examples_dropped_code.append(text[:120])
            continue
        line = _to_one_line(text)
        if len(line) < MIN_ABSTRACT_CHARS:
            st.dropped_empty += 1
            continue
        if not line:
            st.dropped_invalid += 1
            continue
        memory.commit_fact(line, source=ABSTRACT_SOURCE, confidence=0.5)
        st.kept += 1
    return st


def _source_episodes(memory: MemoryStore,
                     sources: Sequence[str] | None) -> Iterable:
    """原材料 —— **一律取 episodic 原文**：不预先过滤含代码块的行 ——
    那正是要交给 LLM 去丢掉的东西；本模块只负责把**产出**里的代码挡住。

    ★ 第二十七轮（§17-6 6b）：**失败类排最前**。实测（真模型 13 进 1 出）：
    原料按 id 倒序喂进去，失败记录混在 goal/finish/outcome 里，
    11 条被模型判 NONE —— 不是模型挑剔，是成功记录本来就没什么可提的。
    失败记录（打回/超限，content 现在带错误原文）才是"怎么避免坑"的原料，
    它们必须排在注意力最高的位置。
    """
    try:
        eps = list(memory.episodes(limit=500))
    except Exception:                          # noqa: BLE001 — 记忆库损坏不是 distill 的错
        return []
    _FAIL_KINDS = ("failure",)
    eps.sort(key=lambda e: (0 if getattr(e, "kind", "") in _FAIL_KINDS else 1,
                            -int(getattr(e, "id", 0) or 0)))
    return eps


def _to_one_line(text: str) -> str:
    """整理成一行：`填充词` 去掉、多行合并、超长截断。"""
    cleaned = _FILLER.sub("", text)
    cleaned = _CODE_FENCE.sub(" ", cleaned)
    line = re.sub(r"\s+", " ", cleaned).strip(" -•*\t")
    if len(line) > MAX_ABSTRACT_CHARS:
        cut = line[:MAX_ABSTRACT_CHARS]
        # 截断到最后一个句子分隔符，避免留下半句话误导下游
        for sep in ("；", "。", "，", ","):
            idx = cut.rfind(sep)
            if idx > MIN_ABSTRACT_CHARS:
                line = cut[:idx]
                break
        else:
            line = cut
    return line


def abstract_only(facts: Iterable) -> list:
    """洪水 accepted facts ⇒ 只要抽象层。给决策链的单一入口，不许到处 filter。"""
    return [f for f in facts if getattr(f, "source", None) == ABSTRACT_SOURCE]
