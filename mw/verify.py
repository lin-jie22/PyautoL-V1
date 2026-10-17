"""校验 —— `tools.py` 首屏那句"预算、记忆、规划、**校验**"里的最后一项。

在此之前，这一项是**零实现**。这不是"能力不够"，是**声明了没有**：

    grep -c "verify\\|check_answer" mw/*.py   →  0

而另外三条腿都在，而且都有读数。所以内核一直缺一条腿，只是没人把它念出来。
三个已登记的读数其实是**同一个洞**的三个切面：

| 已登记的现象 | 登记处 | 机制 |
|---|---|---|
| 4x 预算下分数与 1x 一字不差 | README 边界 6 | 没有"花预算去验证"这个位置 ⇒ 预算只能是上限，不能是资源 |
| `finish` 永不失败 ⇒ 没有可学的失败 ⇒ 语义记忆恒空 | README 边界 10 / §17-5 | `result.ok` 恒 True ⇒ 世界模型没有失败面 |
| facts 是现象描述、不可执行 | §17-6 | 没有任何"执行知识"的位置 |

⇒ 修法是**一条**，不是三条：给 `finish` 加一道**确定性**的校验闸。

---

## 判据从哪来：**题面本身**，不是尺子

这是本模块唯一的立论，也是最容易被做歪的地方。

⛔ 绝不允许读打分器的三样东西（任何一样都等于开卷考）：
    · 参考答案（参考解）
    · 断言集（每题自带的那组判分语句）
    · 分数 / 失败理由

✔ 允许读的只有一样：**目标文本里本来就有的约束**。实测的代码题清一色写着
   "只输出代码，函数名必须是 `flatten`" —— 那是**出题人交给做题人的要求**，
   不是判分器事后才有的信息。从它派生判据，等于"做题人自己读题"。

这与 §16.9 那条教训是同一件事：**判据的来源比判据的松紧更重要。**
判据不由测量者发明，就没人能靠调松判据让分数变好看。

## 判据都只做"结构性"判定

| kind | 判什么 | 凭什么能做 |
|---|---|---|
| `nonempty` | 答案非空 | 平凡但真实：空交付就是 0 分 |
| `fence_lang` | 代码块语言标签是 python | 题说"写一个函数" ⇒ C 代码不可能是答案 |
| `compiles` | 抽出的代码能 `compile()` | 语法错误是**确定性**的，不需要判断对错 |
| `defines` | 出现 `def <题面指定的名字>` | 题面点名了函数名 |

⛔ 它们**都不判断"答案对不对"**。`def f(): pass` 能过全部四项检查。
所以本模块**不产生任何分数**，也**不允许**被读成分数 ——
"自检通过"这句话的全部含义是"结构上过得去"，不是"做对了"。
把它读成后者，就是 V1 版本的"自己给自己发分"。

## 为什么必须是闸，不是"建议"

第二十轮的 `recall_trigger` 已经写过一遍同样的理由：**交给模型决定，
它会在 100% 的情况下说不用**。这里同理 —— 如果"要不要自检"由规划器选，
那它永远不会选，机制恒为 0 次，而读数上只显示"没触发"。

所以位置是**写死**的：`finish` 被 dispatch 之后、交付之前。
理由不是"更严格"，是"可证伪"：一个恒不触发的机制和一个没装的机制长得一模一样。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 从 Markdown 围栏里抽代码。**必须与外部打分器同构** —— 否则自检比尺子更严，
#: 就会把"打分器本来会接受的答案"判成失败，那不是自检，那是自造假失败。
#: 同构这条不变量由契约交叉核验（测试可以 import 锚层，内核不可以）。
_FENCE_PY = re.compile(r"```(?:python|py)?[ \t]*\r?\n(.*?)```", re.S)

#: **任意**语言标签的围栏 —— 只用来发现"标签不是 python"这一件事。
#: 它和上面那条是**两个问题**：上面那条回答"代码在哪"，这条回答"标签写了什么"。
#: ⚠️ 合成一条会让抽取逻辑依赖标签，而打分器不依赖 —— 那就不同构了。
_FENCE_ANY = re.compile(r"```[ \t]*([A-Za-z0-9_+#.-]*)[ \t]*\r?\n", re.S)

_CODE_ONLY = re.compile(
    r"只输出代码|只要代码|只给代码|仅输出代码|只返回代码"
    r"|only (?:output|return|give) (?:the )?code", re.I)

#: 题面点名函数名的两种写法（"必须是 X" / "必须为 X"），允许反引号包裹。
_FUNC_NAME = re.compile(
    r"函数名(?:必须|应)(?:是|为)\s*`?\s*([A-Za-z_][A-Za-z0-9_]*)", re.I)

#: Python 语境的判据：题面在要"函数/代码"时才算。
_PY_CONTEXT = re.compile(r"写一个函数|写个函数|一个函数|python", re.I)

#: 允许出现的围栏标签。空标签（```）算合法 —— 模型常常不写语言。
_ALLOWED_TAGS = frozenset({"", "python", "py"})


@dataclass(frozen=True)
class Check:
    """一条判据。`name` 是给人读的，进日志与读数；`kind` 是给代码分支用的。"""

    name: str
    kind: str
    arg: str = ""

    def as_line(self) -> str:
        return f"{self.name}<{self.kind}{(':' + self.arg) if self.arg else ''}>"


@dataclass(frozen=True)
class Verdict:
    ok: bool
    failed: tuple[str, ...]
    report: str

    def as_line(self) -> str:
        return "通过" if self.ok else f"未过 {len(self.failed)} 项：{self.report}"


def extract_code(answer: str) -> str:
    """抽代码。抽不到围栏就**整段当代码**（模型常常直接给裸代码）。

    ⚠️ 这两句的每一个字都与 `bench/scorers/code_exec.py:extract_code` 对齐。
    不对齐的代价是单向的：自检更严 ⇒ 正确答案被判失败 ⇒ 分数下降，
    而读数上看起来像是"自检有害"。这类"装置本身吃掉了效应"的坑，
    本项目的第十七轮（共用记忆目录）已经踩过一次。
    """
    blocks = _FENCE_PY.findall(answer or "")
    if blocks:
        return "\n\n".join(b.strip() for b in blocks if b.strip())
    return (answer or "").strip()


def derive(goal: str) -> tuple[Check, ...]:
    """从**目标文本**派生判据。派生规则只有这一处。

    ⚠️ `nonempty` 恒开：它不是形式主义 —— `finish` 带空答案是一个真实发生过的
    失败（模型给出结构化计划却把 `answer` 留空，见 `planner.propose` 的"定向补救"）。
    在它之前，那种失败会被当成一次**成功**收尾，然后交给打分器去发现。
    """
    checks: list[Check] = [Check("非空", "nonempty")]
    m = _FUNC_NAME.search(goal or "")
    if m:
        checks.append(Check(f"函数名 {m.group(1)}", "defines", m.group(1)))
    if _CODE_ONLY.search(goal or ""):
        # 语言标签排在编译之前：标签错时"编译失败"的报错是无信息的，
        # 而"你写了 ```c"是可直接改的。**报错的价值在于能据此改动作。**
        if _PY_CONTEXT.search(goal or ""):
            checks.append(Check("语言标签", "fence_lang", "python"))
        checks.append(Check("可编译", "compiles"))
    return tuple(checks)


# ---------------------------------------------------------------------------
def _check_one(c: Check, answer: str, code: str) -> str:
    """跑一条判据。返回空串 = 通过；返回非空 = **可直接据此改动作**的说明。

    ⛔ 这里**绝不抛异常**：判据自己崩了会被读成"答案有问题"，
    而那是把装置的故障记到被测对象头上。所有异常路径都折成明确的说明。
    """
    if c.kind == "nonempty":
        return "" if (answer or "").strip() else "答案为空"

    if c.kind == "fence_lang":
        tags = [t.lower() for t in _FENCE_ANY.findall(answer or "")]
        bad = [t for t in tags if t not in _ALLOWED_TAGS]
        if not bad:
            return ""
        return f"代码块语言标签是 {bad[0]!r}（本题要 Python）"

    if c.kind == "compiles":
        if not code.strip():
            return "抽不出可执行代码"
        try:
            compile(code, "<answer>", "exec")
        except SyntaxError as e:
            line = ""
            try:
                src = (code.splitlines() or [""])[max(0, int(e.lineno or 1) - 1)]
                line = src.strip()[:80]
            except (IndexError, TypeError, ValueError):
                line = ""
            # 报错必须带**行号 + 原文**：只说"语法错误"等于让做题人重新猜一遍。
            return (f"编译失败 line {e.lineno}: {e.msg}"
                    + (f" ⇒ {line}" if line else ""))
        except (ValueError, TypeError) as e:      # 源码含 NUL 等
            return f"无法编译（{type(e).__name__}: {e}）"
        return ""

    if c.kind == "defines":
        pat = re.compile(rf"\bdef\s+{re.escape(c.arg)}\s*\(")
        if pat.search(code):
            return ""
        return f"缺少 def {c.arg}(...)"

    # 未知 kind：**显式失败**，不静默放行。
    # 一个静默放行的判据比没有判据更坏 —— 它让"检查过了"变成谎话。
    return f"未知判据类型 {c.kind!r}（未实现 ⇒ 不能算通过）"


def run(answer: str, checks: tuple[Check, ...] | list[Check]) -> Verdict:
    """跑全部判据。**不看模型、不看分数、不调网络**。"""
    checks = tuple(checks or ())
    if not checks:
        # 没有判据 ⇒ 如实报"没检查"，不报"通过"。
        # 这两件事在读数上必须分开，否则"自检通过率 100%"可以靠关掉判据拿到。
        return Verdict(False, ("(无判据)",), "本轮没有派生到任何判据")
    code = extract_code(answer) if any(
        c.kind in ("compiles", "defines") for c in checks) else ""
    failed: list[str] = []
    reports: list[str] = []
    for c in checks:
        msg = _check_one(c, answer, code)
        if msg:
            failed.append(c.name)
            reports.append(msg)
    if not failed:
        return Verdict(True, (), "")
    return Verdict(False, tuple(failed),
                   f"{len(failed)}/{len(checks)} 项未过：" + "；".join(reports))
