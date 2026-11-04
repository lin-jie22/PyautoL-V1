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
| `undefined` | 函数体里有一个**哪儿都没绑定**的名字 | `symtable` 是确定性的；名字没绑，跑到那行必炸 |

⛔ 它们**都不判断"答案对不对"**。`def f(): pass` 能过全部五项检查。
所以本模块**不产生任何分数**，也**不允许**被读成分数 ——
"自检通过"这句话的全部含义是"结构上过得去"，不是"做对了"。
把它读成后者，就是 V1 版本的"自己给自己发分"。

## ⭐ 第一百四十七轮（V61，§127）：**重开**上面那条"不做执行"

那段判词的前提在 V60 之后**塌了一半**，所以本轮把它拆开重判 —— 不是推翻，是**限定**：

| 第八十轮反对的理由 | 现在还成立吗 |
|---|---|
| 代价：**探针输入**（一处新的判据来源） | ⛔ **不成立了**。V61 的输入是**题面自带的示例**（`commas(0) == '0'`）。题面本来就是做题人手里的东西，不是判分器事后才有的信息 ⇒ 与"判据只从题面派生"这条立论**一致**，不是新来源。 |
| 边际只有 1 格（sg05 0/14→1/14） | ⚠️ 只对**那一套题**成立。那套题的错是"处理顺序写反"，与本套题无关。 |
| 挂死风险 / 沙箱 | ✅ 仍然成立，所以：`subprocess.run(timeout=5)` + `python3 -I -B` + 独立 tempdir，失败一律折成说明，⛔ 绝不抛。 |

V60 的实测把"再做一层"这条线**走完了**：放开 `--allow-shell` 之后模型 4/4 都跑了，
但**验证只落在已经做对的题上**（`top_k`/`rle` 全 1.0，各跑 2 次），
唯一真错的 `merge_ranges`（0.3333）22 轮里**一次都没提**。
⇒ 结论不是"验证无效"，是**验证由模型自己选，就永远选在安全的地方**。
所以它必须进**交付闸**（位置写死的第四处，与 `defines`/`compiles` 同一处代码）。

⚠️ 判据的期望值**只从题面抽**：`example_asserts(goal)` 找不到 `==` 就**不派这条判据**
（本套题 6 题里 4 题有，t01/t02 没有）——"没有示例"必须是**判据缺席**，
不许退化成"跑一下看有没有抛异常"，那又回到 V60 那个 `rc=0` 的坑里。

## `undefined` 是怎么来的，为什么**不是**"跑一下看看"

第八十轮实测（证据 `bench/evidence/answers_s79/`）：

| 做法 | sg01（忘 `import re`） | sg05（`odict_values.sort()`） | 误报 |
|---|---|---|---|
| 现在这 4 条 | 判"通过" | 判"通过" | — |
| 静态未定义名 | **命中** | 漏（是类型错，不是名字错） | 18 份权威实现 **0** |
| 执行一次 | 命中 | 命中 | 4 题调通无炸 |

把错误原文回灌给同一个模型重写：`sg01` 1/14 → **14/14**（只补一行 import）；
`sg05` 0/14 → **1/14** —— 因为它剩下的错误是**对错**（处理顺序写反），
而自检按设计不判对错。⇒ 执行相对静态的**边际只有 1 格**，代价却是沙箱、
探针输入（一处新的判据来源）、挂死风险。**不做**，这是被读数否掉的。

⇒ 本套题里自检可及的天花板 = +13 格，全部来自"名字没绑"这一类。

## 为什么必须是闸，不是"建议"

第二十轮的 `recall_trigger` 已经写过一遍同样的理由：**交给模型决定，
它会在 100% 的情况下说不用**。这里同理 —— 如果"要不要自检"由规划器选，
那它永远不会选，机制恒为 0 次，而读数上只显示"没触发"。

所以位置是**写死**的：`finish` 被 dispatch 之后、交付之前。
理由不是"更严格"，是"可证伪"：一个恒不触发的机制和一个没装的机制长得一模一样。
"""

from __future__ import annotations

import builtins
import os
import re
import shutil
import subprocess
import symtable
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

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

#: builtins 名字集合。`symtable` 只给符号表，**不告诉你"哪些是语言自带的"** ——
#: 少了这一步，`len` / `sorted` / `str` 会全被当成"未定义的名字"。
_BUILTINS = frozenset(dir(builtins))

#: 一旦出现这些，**静态上不可判定**"哪些名字被绑定过" ⇒ 这条判据本轮不适用。
#: 星号导入把未知数量的名字塞进命名空间；`exec` / `eval` / `globals` / `locals` / `vars` 同理。
#: ⚠️ 不可判定必须返回 `None`，**不许**返回"通过" —— 那两句在读数上必须分得开。
_DYNAMIC_BIND = re.compile(r"\b(?:exec|eval|globals|locals|vars)\s*\(|\bimport\s*\*")


# ══════════════════════════════════════════════════════════════════════════
# 题面示例（V61，§127）—— 判据的**期望值**从哪来
#
# ⛔ 只从 `goal`（题面）抽。打分器的 `tests` 字段一个字都不许读 —— 那是尺子。
#    题面里的"例如 `commas(0) == '0'`"是**出题人交给做题人的要求**，
#    与"函数名必须是 commas"是同一类信息。
# ══════════════════════════════════════════════════════════════════════════

#: 分句边界。⚠️ **只按全角标点与换行分**：示例里常带 ASCII 逗号
#: （`[('a', 2)]`、`'1,234,567'`），按 ASCII 分会把一条断言切成两半。
_EX_SPLIT = re.compile(r"[，。；\n]")

#: 中文里引出示例的词。剥掉它们，剩下的才可能是一条断言。
_EX_LEAD = re.compile(r"^\s*(?:例如|比如|如|示例[:：]?)\s*")

#: 一个函数调用的开头。⚠️ 前面**允许有别的字**：`…带千分位逗号的字符串：commas(1234567) == '1,234,567'`。
_EX_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")

#: 一题最多抽几条。设上限不是洁癖：题面可以很长，`assert` 越多，
#: 超时与"一条挂死全盘不算"的面越大。
_EX_MAX = 5

#: 子进程上限。⛔ 必须有界：答案里一个 `while True:` 就能把交付闸拖死。
_EX_TIMEOUT_S = 5.0

#: 报告里留给 stderr 末行的字数。只要"错在哪一行"，不要整个 traceback。
_EX_OUT_CAP = 400

#: ⭐ **装置自曝**的标记。内核靠这个前缀把"判据说答案错了"与"装置自己崩了"
#: 分开计（`kernel_verify.exec_err`）。⛔ 合成一个数就是把装置的锅记到被测对象头上。
EXEC_FAULT = "示例执行装置故障："


def _match_close(s: str, i: int) -> int:
    """`s[i]` 是 `(`；返回与它配对的 `)` 的下标，配不上 ⇒ `-1`。"""
    depth, quote = 0, ""
    while i < len(s):
        ch = s[i]
        if quote:
            if ch == "\\":       # 转义：跳过下一个字符（`'it\\'s'`）
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _cut_call(s: str) -> str | None:
    """从一段文字里切出**一条** `f(...) == …`。切不出 ⇒ `None`。

    为什么要括号配对而不是正则：`merge_ranges([(1,3),(2,6),(8,10)])` 里有嵌套括号，
    非贪婪正则会在第一个 `)` 处停下，切出来的是 `merge_ranges([(1,3)` ——
    那条 assert 语法错，会把**每题**都判成"示例不通过"。这种错读起来像"模型全错"。

    ⚠️ 为什么要**遍历每一个**调用起点（V61 自踩）：同一个分句里可能先出现**函数签名**，
    `写一个函数 commas(n)：commas(0) == '0'` 的第一个 `(` 属于 `commas(n)`，
    只看它 ⇒ 切不出断言 ⇒ **判据静默缺席**，而读数上看起来像"这题没有示例"。
    """
    for m in _EX_CALL.finditer(s):
        j = _match_close(s, m.end() - 1)
        if j < 0:
            continue
        if not s[j + 1:].lstrip().startswith("=="):
            continue
        return s[m.start():].strip()
    return None


def example_asserts(goal: str) -> tuple[str, ...]:
    """从**题面**抽示例断言（`assert f(...) == …`）。抽不到 ⇒ `()`。

    ⚠️ `()` 的语义是**"这题没有可比对的期望值"**，不是"示例通过了"。
    两者在读数上必须分得开（同 `undefined_names` 的 `None` / `()` 之分）。
    """
    out: list[str] = []
    for chunk in _EX_SPLIT.split(goal or ""):
        s = _EX_LEAD.sub("", chunk.strip()).strip()
        if not s or "==" not in s:
            continue
        one = _cut_call(s)
        if not one:
            continue
        line = f"assert {one}"
        if line in out:
            continue
        out.append(line)
        if len(out) >= _EX_MAX:
            break
    return tuple(out)


def _split_eq(s: str) -> tuple[str, str] | None:
    """在**顶层**（括号外、引号外）找第一个 `==`，切成 `(左, 右)`。

    ⛔ 不能用 `s.split("==")`：`commas(0) == '='` 里的 `'='` 会把右值切碎；
    `f(x) == g(y == z)` 里的第二个 `==` 是**内层**的，切在那儿就成了语法错。
    """
    depth, quote, i = 0, "", 0
    while i < len(s) - 1:
        ch = s[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "=" and s[i + 1] == "=" and depth == 0:
            return s[:i].strip(), s[i + 2:].strip()
        i += 1
    return None


#: 跑示例的**外壳**。为什么不用裸 `assert`：裸 assert 的报错只有 `AssertionError`，
#: 模型据此只能猜"哪个错了、差多少"。V60 的教训是"`rc=0` 不带信息"，
#: 同一条理由在这儿成立：**验证必须产出可比对的结果**，否则跑一万次也没人改得动。
#: ⚠️ 只暴露 `实得 / 期望` 两个值 —— 它们由**模型自己的代码**算出来，不是新信息。
_EX_HARNESS = '''
def _v1_chk(n, src, got, want):
    if got != want:
        print("示例%d未通过：%s" % (n, src))
        print("  实得 %r" % (got,))
        print("  期望 %r" % (want,))
        raise SystemExit(3)
'''


def run_example(code: str, asserts: str) -> str:
    """把**答案代码 + 题面示例**放一个独立目录里跑一次。通过 ⇒ `""`。

    ⛔ 四条不许省：
      ① `timeout` —— 答案里一个死循环就能把交付闸拖死，而那是**装置**挂掉；
      ② 独立 tempdir + `-I` —— 别人在 `ws/` 里留的残次品不许参与，`sys.path` 也不许含 cwd；
      ③ 装置故障有一个**自己的前缀**（`EXEC_FAULT`）—— 与"示例不通过"分开计；
      ④ 失败必须带**实得/期望**（`_EX_HARNESS`）—— 只回一句 `AssertionError`
         等于让做题人重新猜一遍，而"跑一遍"的全部价值就在这四个字之外。
    """
    if not (code or "").strip():
        return "抽不出可执行代码"
    calls: list[str] = []
    for n, line in enumerate([x for x in asserts.splitlines() if x.strip()], 1):
        body = line.strip()
        if body.startswith("assert "):
            body = body[len("assert "):].strip()
        eq = _split_eq(body)
        if eq is None:
            calls.append(f"assert {body}")
        else:
            left, right = eq
            calls.append(f"_v1_chk({n}, {body!r}, {left}, {right})")
    src = (code.rstrip("\n") + "\n" + _EX_HARNESS + "\n" + "\n".join(calls) + "\n")
    tmp = tempfile.mkdtemp(prefix="v1ex-")
    try:
        path = Path(tmp) / "answer.py"
        path.write_text(src, encoding="utf-8")
        try:
            r = subprocess.run([sys.executable, "-I", "-B", str(path)],
                               cwd=tmp, capture_output=True, text=True,
                               timeout=_EX_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return f"{EXEC_FAULT}超过 {_EX_TIMEOUT_S:.0f}s（死循环或等待输入）"
        except (OSError, ValueError) as e:
            return f"{EXEC_FAULT}{type(e).__name__}: {e}"
        if r.returncode == 0:
            return ""
        lines = [ln for ln in (r.stdout or "").strip().splitlines() if ln.strip()]
        if lines:
            # 外壳自己打的（示例 N 未通过 + 实得/期望）—— 三行，别截。
            return "；".join(ln.strip() for ln in lines[-3:])
        err = [ln for ln in (r.stderr or "").strip().splitlines() if ln.strip()]
        tail = err[-1][:_EX_OUT_CAP] if err else f"退出码 {r.returncode}"
        return f"题面示例未通过：{tail}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@dataclass(frozen=True)
class Check:
    """一条判据。`name` 是给人读的，进日志与读数；`kind` 是给代码分支用的。"""

    name: str
    kind: str
    arg: str = ""

    def as_line(self) -> str:
        # ⚠️ `arg` 可能是**多行**（示例断言一行一条）⇒ 换行压成空格再截断。
        #    不截断的话，一条 `■` 汇总行会被 5 条 assert 撑到 400 字，
        #    而那一行是逐题读数的主视图。
        arg = (self.arg or "").replace("\n", " ")
        if len(arg) > 48:
            arg = arg[:48] + "…"
        return f"{self.name}<{self.kind}{(':' + arg) if arg else ''}>"


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


def _walk_symbols(tab, bound_module: frozenset[str] | set[str],
                  outer: list[set[str]], out: list[tuple[str, str]]) -> None:
    """递归找"被引用但没有任何绑定"的名字。"""
    for sym in tab.get_symbols():
        # 参数 / 赋值 / 导入 ⇒ 这个作用域里"有定义"（`x += 1` 也是 assigned）
        if sym.is_parameter() or sym.is_assigned() or sym.is_imported():
            continue
        name = sym.get_name()
        if name in _BUILTINS or name in bound_module or any(name in s for s in outer):
            continue
        out.append((tab.get_name() or "<module>", name))
    local = {s.get_name() for s in tab.get_symbols()}
    for child in tab.get_children():
        _walk_symbols(child, bound_module, outer + [local], out)


def undefined_names(code: str) -> tuple[tuple[str, str], ...] | None:
    """代码里"被引用、却哪儿都没有绑定"的名字。返回 `(作用域名, 名字)` 元组。

    `()` = 没发现；`None` = **本轮不可判定**（语法错 / 动态绑定）。

    ## 为什么是静态符号表，不是"跑一下看看"

    第八十轮实测：把错误原文回灌给同一个模型，`sg01` 从 1/14 变 14/14（只补一行
    `import re`），`sg05` 从 0/14 只变 1/14 —— 因为 `sg05` 剩下的错误是**对错**
    （处理顺序写反），而自检按设计不判对错。⇒ 执行相对静态的边际只有 1 格，
    却要付沙箱、探针输入（一处新的判据来源）、挂死风险三样代价。**不做**。

    ## 为什么只收"绑定的"符号当已定义

    ⛔ 第一版写成 `{s.get_name() for s in top.get_symbols()}`（**全部**符号）⇒
    模块级的"引用名"把自己豁免掉 ⇒ **模块级未定义名永远抓不到**。
    反证集当场抓出来（`@lru_cached` 漏报），读代码看不出来。
    现在只收 `is_assigned() / is_imported() / is_namespace()`。
    """
    if _DYNAMIC_BIND.search(code or ""):
        return None
    try:
        top = symtable.symtable(code, "<answer>", "exec")
    except SyntaxError:
        # 语法错交给 `compiles` 判据，这里不重复报（两处报同一件事 = 噪音）
        return None
    bound = frozenset(
        s.get_name() for s in top.get_symbols()
        if s.is_assigned() or s.is_imported() or s.is_namespace())
    out: list[tuple[str, str]] = []
    _walk_symbols(top, bound, [], out)
    return tuple(out)


def derive(goal: str, static: int = 0, exec_: int = 0) -> tuple[Check, ...]:
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
        # ★ 第八十轮：静态未定义名。排在最后 —— 前面几条都是"改一下就好"的，
        #   这条是"你忘了一个 import"，两者同时不满足时应先修前者。
        # ⚠️ `arg` 带着模式号：1 = 报名字，2 = **同一触发集、盲报**（等长安慰剂臂）。
        #    模式 2 连判据名字都换成"结构检查" —— 否则 `未过：未定义名` 本身就是载荷。
        _st = int(static)
        if _st > 0:
            checks.append(Check("未定义名" if _st == 1 else "结构检查",
                                "undefined", str(_st)))
        # ★ V61（§127）：**题面示例**。排在最后，理由与 `undefined` 相同 ——
        #   前面几条都是"改一下就好"的，这条要跑一遍、代价最大。
        # ⚠️ 抽不到示例就**不派**（t01/t02 就是这样）：判据缺席必须显形，
        #   不许退化成"跑一下没抛异常就算过"（那是 V60 的 `rc=0` 坑）。
        # ⚠️ `arg` 里放的是**断言原文**，`name` 里放条数：名字进读数，
        #   原文只在日志里给人看（`as_line` 会截断）。
        if int(exec_) > 0:
            _ex = example_asserts(goal)
            if _ex:
                checks.append(Check(f"示例可跑×{len(_ex)}", "example", "\n".join(_ex)))
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

    if c.kind == "undefined":
        bad = undefined_names(code)
        if bad is None or not bad:
            # ⚠️ `None`（不可判定）与 `()`（没发现）在这里**走同一条出口**，
            #    但那是刻意的：不可判定时打回 = 自造假失败（装置吃掉了效应）。
            #    区别记在下面的说明里 —— 读数要能分开，动作不许分开。
            return ""
        if c.arg == "2":
            # 等长安慰剂臂的载荷：**一个标识符都不许漏**。
            return "自检未通过（不提供细节）"
        fn, name = bad[0]
        more = f"；另有 {len(bad) - 1} 个未定义名" if len(bad) > 1 else ""
        return (f"未定义的名字 {name!r}（在 {fn} 内被引用，"
                f"模块里既没有 import 也没有赋值）{more}")

    if c.kind == "defines":
        pat = re.compile(rf"\bdef\s+{re.escape(c.arg)}\s*\(")
        if pat.search(code):
            return ""
        return f"缺少 def {c.arg}(...)"

    if c.kind == "example":
        # ⚠️ 与其它判据**同一条规矩**：判据自己崩了不许抛出去。
        #   `run_example` 的三条异常路径都已折成说明，这里再兜一层 ——
        #   兜底的错**也带 `EXEC_FAULT` 前缀**，否则装置故障会被读成模型写错了。
        try:
            return run_example(code, c.arg)
        except Exception as e:                       # noqa: BLE001
            return f"{EXEC_FAULT}{type(e).__name__}: {e}"

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
        c.kind in ("compiles", "defines", "undefined", "example") for c in checks) else ""
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


# ══════════════════════════════════════════════════════════════════════════
# 交付验收（第一百三十四轮 §110）—— 答案之外，**产物**也得有人核
#
# 第一百三十三轮的现场（三臂同一道真实任务，同一个模型）：
#
#   | 臂 | status   | 产物 report.md | 产物内容           |
#   |----|----------|----------------|--------------------|
#   | A  | stalled  | 不存在         | —                  |
#   | B  | **done** | **不存在**     | —                  |
#   | C  | stalled  | 存在 44 字节   | 三个数字全对       |
#
# ⇒ 状态词两个方向都在撒谎。`done` 的语义一直是"模型**说**它做完了"
#   （`finish` 工具被成功调用过），从来不是"用户要的东西**在**"。
#   这两个命题之间的差，恰好是"能不能当产品"的差。
#
# ⛔ 为什么不在 `status` 里再加一个值：`status` 是**主循环怎么结束的**
#   （done/stalled/cascade/budget/error），它是过程量；"东西在不在"是结果量。
#   把结果塞进过程量的枚举里，下一次加结果维度又要改一次枚举 ——
#   而且历史 `answers.jsonl` 里的 status 会立刻失去可比性。
#   ⇒ 分列：`status` 不动，新增 `verdict`，两者都由 `■` 行与 stats 同时给出。
# ══════════════════════════════════════════════════════════════════════════

#: 题面里"像文件"的 token。**只在题面里找**，不在答案里找 ——
#: 从答案里找等于让模型自己给自己出验收题（§16.9：判据来源比判据松紧更重要）。
_ARTIFACT_RE = re.compile(
    r"(?<![A-Za-z0-9_./\-])"
    r"((?:[A-Za-z0-9_.\-]+/)*[A-Za-z0-9_.\-]+\.(?:md|csv|tsv|json|jsonl|txt|py"
    r"|html?|ya?ml|log|xlsx|sh|toml|ini))"
    r"(?![A-Za-z0-9_])", re.I)

#: "这个 token 是被**写出来**的"证据 —— 只在它前面 12 个字符里找。
#: ⚠️ 中文没有空格，窗口按**字符**算，不按词算。
_WRITE_WORDS = ("写入", "写到", "写进", "写在", "保存", "生成", "输出", "导出",
                "创建", "落盘", "存到", "放到", "放进", "写")
_WRITE_WORDS_EN = ("write", "save", "create", "output", "generate", "produce")
_WRITE_WINDOW = 12

#: 并列列举的连接词 —— 用来把"被写出来的"这个身份**传递**给后一个 token。
#: ⚠️ 第十二个字符的窗口在**并列列举**上不成立，这是 V44 的 S5 抓到的：
#:    `请生成 out/a.md 与 out/b.json 两个文件。` ⇒ `out/b.json` 前面只有
#:    「 与 」（3 个字符，没有写动作）⇒ 被判成**输入** ⇒ 分母只剩 1 个产物
#:    ⇒ 少写一个文件也会读成 `PASS`。**预注册押 PARTIAL、实测 PASS**，
#:    按纪律改分类器（不是改预测）。
_SEP_ONLY = re.compile(r"^[\s\u3000与和、,，/及并且以及+＆&]+$")
_SEP_WORDS = re.compile(r"^\s*(?:and|or|plus)\s*$", re.I)


def expected_artifacts(goal: str) -> tuple[list[str], list[str]]:
    """从题面抽 `(产物, 输入)`，各自去重保序。

    为什么要分两类（第一百三十三轮 B 臂的教训）：
        题面里同时有 `sales.csv`（**输入**，本来就在）与 `report.md`（**产物**）。
        不分的话，"找到了输入"会被记成"部分完成" —— 而用户要的东西一个字都没有。
        B 臂现场：`status=done`、`report.md` 不存在、交出去的是 pandas 代码片段。
        若把输入算进分母，这一格会读成 `PARTIAL`（半成功），
        真相是 **`FAIL`（零产物）**。

    分类规则只有一条：token **前面 12 个字符**里有没有写动作。
    `写入工作目录下的 report.md` ⇒ 产物；`工作目录里有 sales.csv` ⇒ 输入。
    同一 token 若两次出现、其中一次带写动作 ⇒ **按产物算**（升级，不降级）。
    """
    g = goal or ""
    #: [start, end, token, 是不是产物]
    marks: list[list] = []
    for m in _ARTIFACT_RE.finditer(g):
        head = g[max(0, m.start() - _WRITE_WINDOW):m.start()].lower()
        is_out = (any(w in head for w in _WRITE_WORDS)
                  or any(w in head for w in _WRITE_WORDS_EN))
        marks.append([m.start(), m.end(), m.group(1), is_out])

    # 第二遍：**并列列举传递** —— 上一个产物与这个 token 之间只有连接词
    # （`与`/`和`/`、`/`,`/`and`…）⇒ 这个也是产物。
    # ⛔ 不把窗口从 12 放大到 40 来"顺手解决"：窗口放大到能覆盖任意列举，
    #    就一定会盖到"读取 a.csv，生成 b.md"里的 `a.csv` ——
    #    那是**输入**，而把输入算进产物分母会让"零产物"读成 `PARTIAL`。
    last_out_end: int | None = None
    for mk in marks:
        if mk[3]:
            last_out_end = mk[1]
            continue
        if last_out_end is None:
            continue
        gap = g[last_out_end:mk[0]]
        if _SEP_ONLY.match(gap) or _SEP_WORDS.match(gap):
            mk[3] = True
            last_out_end = mk[1]

    outs: list[str] = []
    ins: list[str] = []
    for _s, _e, tok, is_out in marks:
        if is_out:
            if tok not in outs:
                outs.append(tok)
            if tok in ins:                   # 升级：产物优先于输入
                ins.remove(tok)
        elif tok not in outs and tok not in ins:
            ins.append(tok)
    return outs, ins


def check_delivery(goal: str, workspace: str | os.PathLike | None,
                   answer: str = "") -> dict:
    """核**约定产物**在不在工作目录里，给出五值之一的 `verdict`。

    | 条件 | verdict | 含义 |
    |---|---|---|
    | 有产物 ∧ 全部存在 | `PASS` | 用户要的东西在 |
    | 有产物 ∧ 部分存在 | `PARTIAL` | 只做了一半，**并列出缺哪些** |
    | 有产物 ∧ 一个都没有 | `FAIL` | 零产物 |
    | 无产物 ∧ 答案非空 | `ANSWER` | 这题交付的是**文本**，只验了它非空 |
    | 无产物 ∧ 答案为空 | `EMPTY` | 什么都没交 |

    ⛔ 故意**没有** `NONE`/`UNKNOWN`：任何"说不清"的值到了汇总行都会被读成"成功"。
    `ANSWER` 明说"没约定产物"，让"验不了"与"验过了"在读数上分得开
    （`verify.run()` 在无判据时返回"没检查"而不是"通过"，同一条理由）。
    """
    outs, ins = expected_artifacts(goal)
    root = Path(workspace) if workspace else None

    def _exists(tok: str) -> bool:
        p = Path(tok)
        if p.is_absolute():
            return p.exists()
        return bool(root is not None and (root / p).exists())

    found = [t for t in outs if _exists(t)]
    missing = [t for t in outs if t not in found]
    if outs:
        v = "PASS" if not missing else ("PARTIAL" if found else "FAIL")
    else:
        v = "ANSWER" if (answer or "").strip() else "EMPTY"
    return {
        "verdict": v,
        "outputs": outs,
        "found": found,
        "missing": missing,
        "inputs": ins,
        # 输入缺失是**另一件事**（题面前提不成立 ≠ 模型没做完），分开报。
        "inputs_missing": [t for t in ins if not _exists(t)],
        "dir": (str(root) if root is not None else ""),
    }
