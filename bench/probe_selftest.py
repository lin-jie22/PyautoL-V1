#!/usr/bin/env python3
"""自检能不能判对错 —— **只量「检测」，不量「修复」**（第八十三轮，§46）。

## 起点：上一轮把一个方向关掉了，但关得不完整

§45 的读数是：内核默认档（`verify_static=2`）在 140 格上丢 **40 格**，
其中 `SEMANTIC` 26 格 + `CRASH` 14 格，**可动作 0 格**。
「可动作」的定义是「当前自检能报出来」—— 于是结论写成「这套尺子上进化停了」。

但那个结论里混了两件事，必须拆开：

| 问题 | 谁回答 | 成本 |
|---|---|---|
| ① 自检**能不能**看见这类错（检测） | 一次生成 + 一次执行 | 低（不动内核） |
| ② 看见了**能不能**改对（修复） | 一次重写 + 一次判分 | 高 |

§43.2 只测过 ②，而且是拿**错误原文**当载荷测的（`sg05` 0/14 → 1/14）。
它**没有**测过 ①，也**没有**测过「载荷是自检结果」这一种。混在一起的后果是：
「执行一次」被一句话否掉了（「边际只有 1 格」），而那句话测的是**修复**，不是**检测**。

⚠️ 更关键的是：那是**旧 6 题尺子**上的读数。现在尺子是 10 题 140 格，
`CRASH` 一类从 0 格变成 14 格（`sg05` 在 `on` 臂上稳定崩）。
**尺子换了，「边际 1 格」这句话不能直接搬过来。**

⇒ 本轮只做 ①，且**在内核外**做。内核零 diff。

## 要量的东西：一个 2×2

真值用题集自带的 14 条断言机械判（与打分器同一份代码，`run_one`）：

|  | 自检报警 | 自检沉默 |
|---|---|---|
| **程序真错** | 检测（有用） | **漏检**（自检在给错代码发合格证） |
| **程序真对** | **虚警**（白花一次重写，还可能把对的改坏） | 正确沉默 |

⛔ 四个格子都要，缺一个就不能判「这个机制值不值得接进内核」：
  只报检测率 = 不知道代价；只报虚警率 = 不知道收益。

## 两个臂：题面推导 vs 题面 + 自己的代码

- **A 臂（题面）**：只给题面，让模型自己出输入、**手推**期望值。
- **B 臂（题面 + 自己的代码）**：多给它自己写的那份实现，但明写「不许照抄实际输出」。
  B 才是内核里的真实处境（它手里就是有自己的答案的）。

⚠️ 为什么必须两臂：**照着代码的实际输出写断言**是一种毫不费力、且 100% 不报警的写法
   （`assert f(x) == f(x)` 的变体）。只跑 B 臂而不加约束，量到的会是这个假象。
   A 臂是这个假象的**上界对照**：它想作弊也没有可抄的东西。

## 四条预测（⛔ 写于任何模型调用之前）

- **H1｜`CRASH`/`SYNTAX`/`UNDEFINED` 类检测率 ≥ 0.7**。理由：这类错一跑就炸，
  模型写不出一条能通过的断言 —— 除非它压根没跑。这是**机制推理**，不是乐观。
- **H2｜`SEMANTIC` 类检测率 ≤ 0.3**。理由是**自洽**：错信念会同时写进代码和期望值。
  `sg05` 把「去重后排序」写反了，它手推期望值时会把同一个反序推一遍 ⇒ 断言照样通过。
  ⇒ **自检最容易漏的，正是它最需要抓的那一类。**
- **H3｜B 臂检测率 < A 臂**。看见自己的代码会去对齐现状（§43.9 的反面：
  给了细节就局部修补）。⚠️ 若 H3 反向成立（B > A），说明「可抄」比「会跑偏」更危险，
  那 A 臂才是该接进内核的形态 —— 这个分支要**单独记**，不许含糊过去。
- **H4｜虚警率 > 0**。手推期望值本身会错，而错在测试侧就表现成「真对的程序被报错」。

**H2 被证伪**（`SEMANTIC` 检测率 > 0.5）⇒ 自检值得走第二步（Stage R：修复）。
**H2 成立** ⇒ 「让自检能判对错」这条路在**当前模型**上不成立，且原因是结构性的
（不是判据不够严，是判据和错误同源），应当在 DESIGN 里关掉这一支。

## 三条边界（明写，不许事后补）

1. **这是探针，不是变异臂**。它不动 `mw/`、不产生 `KEEP`/`REVERT`、
   不进 `runs.jsonl`。它回答的是「值不值得做」，不是「做了有没有用」。
2. **样本 n=30**（3 次真实运行 × 10 题的程序，答案取自 `bench_out/answers.jsonl` 的**真读数**，
   不是造的）。丢了 2/14 的题会写出一份覆盖不到那个边界的自检 —— **那是合法的漏检**，
   所以主读数按**格**加权（`可报格数上界`），不按题数。⛔ 不按题数报，
   否则「sg03 永远满分」这类无信息样本会稀释分母。
3. **单模型**（本地 ollama `qwen2.5:7b`，T=0）。⛔ 不许外推到「自检没用」，
   只能外推到「这个模型 + 这把尺子上，自检的检测面有多大」。

## 量具自带反证（纪律 ⑩）

`--self-check` 用四组人工构造的（程序，断言）把 2×2 的**四个格子各打中一次**。
⛔ 它证明的是「这台仪器能报出四个格子」，**不是**「模型会报警」——
   后者只有真跑才知道，两者不许混着说。

---

# 事后补的判据（如实登记：这是在 n=2 的烟囱样本上补的）

预注册写完、烟囱跑完（1 对 × 2 臂）就发现一个**把读数做反**的缺陷：

模型写的断言会**自己写错期望值**。`sg01` 那一对该程序本身 12/14（只丢 2 格），
而两个臂各 4 条断言**全挂** —— 逐条手算核对，是模型的期望值错了（它把去重后的
条数都算错了：3 条算成 2 条）。若照原设计记，这一对计入「检测成功」，
而它实际上什么也没检测到。

⇒ 补一条机械判据：**同一条断言，在 oracle 上跑一遍**。

| 现象 | 判定 |
|---|---|
| 在 **oracle 上过** ∧ 在**候选上挂** | **真判别力**（这条断言确实指出了候选与参考的行为差） |
| 在 **oracle 上也挂** | **测试自身写错**，不计入检测 |

⛔ 边界（这条判据只能用来量，绝不能进内核）：
   它读了 oracle，而 `mw/verify.py` 的第一条立论就是「绝不允许读参考答案」。
   所以本文件是**测量装置**，不是判据候选。谁想把它接进 `finish` 闸，先读那一节。

⚠️ 补判据之后，「虚警」拆成两个**不同**的数，不许混着报：

- **工程口径虚警率** = P(报警 | 候选真对)。内核拿到的是这个信号，
  它**不知道**断言本身是错的 ⇒ 会白花一次重写，还可能把对的改坏。
- **测量口径虚警率** = P(∃真判别力的断言 | 候选真对)。**由构造恒为 0**
  （候选真对 ⇒ 行为与 oracle 一致 ⇒ 在 oracle 上过的断言不可能在候选上挂）。
  ⇒ 这个数没有信息量，报告里**不印**，只在下面说明为什么它恒为 0。

用法：

    python3 bench/probe_selftest.py --self-check       # 零模型调用
    python3 bench/probe_selftest.py                    # 30 对 × 2 臂
    python3 bench/probe_selftest.py --arms a           # 只跑 A 臂
    python3 bench/probe_selftest.py --redet 3          # 抽 3 对重跑，查 T=0 复现性
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.probe_error_taxonomy import (                    # noqa: E402
    ACTIONABLE, CRASH, NOANS, PASS, SEMANTIC, SYNTAX, UNDEF, classify)
from bench.scorers.code_exec import extract_code, run_one    # noqa: E402

#: 本轮要拿的三次真实运行（都是 `suite_sha=d6b7ed14…` 的 140 格尺子）。
RUN_IDS = ("69666@1792912166", "70620@1792912544", "5425@1792919669")

#: 题集一题多少条断言。用于把 0..1 的分数换算成「格」。
#: ⚠️ 从题集**现读**（`len(tests)`），这里只当兜底常量，不许拿它当唯一事实源。
CELLS_FALLBACK = 14

#: 自检「有牙」的最低门槛。⛔ 低于这个数就不计入检测率 ——
#: 否则 `assert True` 会被算成一次可靠的沉默，把漏检率做低。
MIN_ASSERTS = 4

# ===========================================================================
# 提示词（⛔ 定死并落盘：§81 实测 prompt 是确定性开关，±13 格）
# ===========================================================================
SYSTEM = "你是严谨的程序员。严格按题目要求作答，只输出 Python 代码块，不要解释。"

_HOWTO = (
    "请只根据上面的题目写一段测试代码：用 assert 语句检查函数是否符合题目要求"
    f"（至少 {MIN_ASSERTS} 条）。\n"
    "输入可以自己选（要覆盖题目里列出的每一条规则），"
    "但**期望值必须按题目规则手工推出来**。\n"
    "函数已经存在，不要重新实现它。\n"
    "只输出一个 python 代码块。"
)

_TMPL_A = "{prompt}\n\n" + _HOWTO

_TMPL_B = (
    "{prompt}\n\n"
    "下面还附有你之前写的实现。**不要**拿它的实际输出去凑期望值 ——"
    "只把它当作确认函数签名的参考：\n\n```python\n{code}\n```\n\n" + _HOWTO
)

#: C 臂：**性质断言**（不写具体期望值）。
#:
#: ⛔ 加这一臂的原因必须明写：A/B 臂跑到第 6 对时已经看到「模型的例子型断言
#:    **一条都没算对**」（4 条断言在候选与 oracle 上**同时**挂）。若不补 C 臂，
#:    结论就只能写成「这个模型不会算」——那是**模型能力**的结论，
#:    而本轮要问的是**机制**：自检这种东西在这个内核里能不能长出牙。
#:    性质断言是唯一不需要先知道答案就能写出来的检查，所以它是对
#:    「自检 ⇒ 必须先有对 ⇒ 循环论证」这一指控的**唯一反例形态**。
#:    ⇒ 它在**看到 A 臂前 6 对之后**追加，这是事后加的，不许装成预注册。
_TMPL_C = (
    "{prompt}\n\n"
    "请只根据上面的题目，写一组**性质断言**（property）。\n"
    "⛔ 不要算出任何具体的期望输出值，也不许写 `assert f(x) == <你猜的值>` 这种例子型断言。\n"
    "每条断言都必须是「对**任意**输入都成立」的检查 —— 例如"
    "「输出里每一行的长度不超过 N」「输出已经按题目要求排好序」"
    "「输出里没有按题目要求本该被去重掉的重复项」「每行都带上了题目要求的固定前缀」。\n\n"
    "要求：\n"
    f"- 至少 {MIN_ASSERTS} 条 assert，可以写在 for 循环里，也可以写 `all(...)`；\n"
    "- 输入列表自己选（可以写多个不同的输入，覆盖题目里列出的每一条规则）；\n"
    "- 只调用题目里那个函数，**不许重新定义它**；需要中间变量就用 `out = f(输入)`；\n"
    "- 只输出一个 python 代码块。\n"
    "如果某条规则做不到「不猜具体值就能检查」，跳过它，别退化成例子型断言。"
)

ARMS = ("a", "b", "c")


def build_prompt(arm: str, prompt: str, code: str) -> str:
    if arm == "a":
        return _TMPL_A.format(prompt=prompt)
    if arm == "b":
        return _TMPL_B.format(prompt=prompt, code=code)
    if arm == "c":
        return _TMPL_C.format(prompt=prompt)
    raise ValueError(arm)


# ===========================================================================
# 断言抽取 —— 与打分器同一口径（`extract_code`），但只留 assert 行
# ===========================================================================
def _strip_scaffold(body: str, fn: str) -> tuple[str, bool]:
    """删掉模型自己写的 `def <fn>(...)` 脚手架（连同它的缩进块），返回 (代码, 是否删过)。

    ⛔ 为什么是"删掉"而不是"判没牙"：实测 C 臂 4 个样本里 3 个都以
       `def norm_sgNN(rows: List[str]) -> List[str]: pass` 开头 —— 那是**脚手架**，
       不是作弊（它下面才是真正的性质断言）。判"没牙"等于因为一行脚手架
       丢掉整份样本，而丢样本的**方向恰好是对假说有利的**（沉默变没牙 ⇒ 检测率不变、
       但 n 变小 ⇒ 结论显得更极端）。所以必须删掉照跑，与 A/B 臂同一口径。
    """
    out: list[str] = []
    removed = False
    skip = False
    for line in body.splitlines():
        if re.match(rf"^def\s+{re.escape(fn)}\s*\(", line):
            removed = True
            skip = True
            continue
        if skip:
            if not line.strip() or line[:1] in (" ", "\t"):
                continue            # 脚手架的函数体
            skip = False
        out.append(line)
    return "\n".join(out), removed


def _split_flat(body: str) -> tuple[list[str], int] | None:
    """把「顶格的、彼此相邻的」assert 拆成「公共前缀 + 单条 assert」。拆不了返回 None。

    拆不了的两种形态（都会退回整块判）：
      · 有 assert 带缩进 ⇒ 它在 `for`/`def` 里，单独拿出来语义就不一样了；
      · 两条 assert 之间夹了别的代码 ⇒ 中间那行可能是被下一条依赖的赋值。
    """
    lines = body.splitlines()
    idx = [i for i, ln in enumerate(lines) if ln.strip().startswith("assert ")]
    if not idx:
        return None
    if any(lines[i][:1] in (" ", "\t") for i in idx):
        return None
    lo, hi = idx[0], idx[-1]
    # 首尾之间只允许出现 assert 行与空行
    if any(lines[i].strip() and not lines[i].strip().startswith("assert ")
           for i in range(lo, hi + 1)):
        return None
    prefix = "\n".join(lines[:lo]).strip()
    units = [((prefix + "\n") if prefix else "") + lines[i].strip() for i in idx]
    return units, len(idx)


def asserts_of(text: str, fn: str = "", mode: str = "lines") -> tuple[list[str], str, int]:
    """返回 (可执行单元, 没牙的理由, assert 条数)。理由为空串 ⇒ 有牙。

    `mode="lines"`（A/B 臂）：只留 `assert` 开头的行。⛔ 刻意丢掉模型自己写的
        `def norm_sgNN(rows): pass` —— 实测它在 A 臂上每次都写，整段执行会让
        所有断言都挂在那个空壳上，量到的就不是候选程序了。
    `mode="block"`（C 臂）：整块执行（性质断言天然需要 `for`/中间变量），
        只删掉被检函数的**脚手架定义**（见 `_strip_scaffold`）。
    `mode="auto"`（C 臂的实际口径，第八十三轮**事后**加的）：能拆就拆。
        规则：删脚手架后，若所有 assert 语句都**顶格**且两条 assert 之间没有别的
        代码 ⇒ 拆成「公共前缀 + 一条 assert」逐条判；只要有一条 assert 带缩进
        （在 `for`/`def` 里）或两条 assert 之间夹了代码 ⇒ 退回整块。
        ⛔ 为什么加：整块判时，块里**任何一条**性质写错都会让整块在参考上也挂 ⇒
        被记成「测试自身写错」，把同块里真有判别力的那条**盖掉**。那个方向
        对本轮的否定性结论**有利**，所以必须修 —— 修完读数只会更有利于"自检有用"。
    """
    body = extract_code(text or "")
    if not body.strip():
        return [], "没有代码", 0
    if mode in ("block", "auto"):
        if fn:
            body, _ = _strip_scaffold(body, fn)
        if mode == "auto":
            flat = _split_flat(body)
            if flat is not None:
                units, n = flat
                if n == 0:
                    return [], "一条 assert 都没有", 0
                if n < MIN_ASSERTS:
                    return [], f"只有 {n} 条 < {MIN_ASSERTS}", n
                return units, "", n
        n = body.count("assert ")
        if n == 0:
            return [], "一条 assert 都没有", 0
        if n < MIN_ASSERTS:
            return [], f"只有 {n} 条 < {MIN_ASSERTS}", n
        return [body.strip()], "", n
    keep: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("assert "):
            keep.append(s)
    if not keep:
        return [], "一条 assert 都没有", 0
    if len(keep) < MIN_ASSERTS:
        return keep, f"只有 {len(keep)} 条 < {MIN_ASSERTS}", len(keep)
    return keep, "", len(keep)


def _fn_in(text: str, fn: str) -> bool:
    return fn in (text or "")


# ===========================================================================
# 量具反证：四组样本，2×2 四个格子各一次
# ===========================================================================
_GOOD = "def f(rows):\n    return [r.strip() for r in rows]\n"
_BAD = "def f(rows):\n    return [r for r in rows]\n"
_A_OK = ["assert f([' a ']) == ['a']"]
_A_BAD_EXPECT = ["assert f([' a ']) == [' a ']"]      # 期望值本身写错 ⇒ 虚警
_A_TOOTHLESS = ["assert f([]) == []"] * MIN_ASSERTS   # 条数够、但什么也判别不出来
_SELF = [
    # (名字, 程序, 断言, 期望 flag, 期望 n_valid>0, 期望 n_badtest>0)
    ("真错+真判别（有用）", _BAD, _A_OK, True, True, False),
    ("真对+沉默（正确）", _GOOD, _A_OK, False, False, False),
    ("真对+报警（测试写错）", _GOOD, _A_BAD_EXPECT, True, False, True),
    ("真错+沉默（漏检）", _BAD, _A_TOOTHLESS, False, False, False),
]


def judge(code: str, oracle: str, asserts: list[str]) -> dict:
    """同一条断言在**候选**与 **oracle** 上各跑一遍 —— 拆开「真判别力」与「测试写错」。

    ⛔ 读 oracle 是测量装置的特权，不许进内核（见模块 docstring 的边界一节）。
    """
    n_valid = n_badtest = n_pass = 0
    first_valid = ""
    for a in asserts:
        ok_c, why_c = run_one(code, a)
        if ok_c:
            n_pass += 1
            continue
        ok_o, _ = run_one(oracle, a)
        if ok_o:
            # 候选挂、oracle 过 ⇒ 这条断言确实指出了行为差
            n_valid += 1
            if not first_valid:
                first_valid = why_c
        else:
            # 两边都挂 ⇒ 断言自己写错了
            n_badtest += 1
    return {
        "flag": (n_valid + n_badtest) > 0,
        "n_pass": n_pass, "n_valid": n_valid, "n_badtest": n_badtest,
        "first_error": first_valid or ("断言自身不符" if n_badtest else ""),
    }


def self_check() -> int:
    bad = 0
    print("反证：四组样本打中「真判别 / 测试写错 / 沉默 / 漏检」四条分支（零模型调用）")
    for name, code, asserts, wflag, wval, wbad in _SELF:
        j = judge(code, _GOOD, asserts)
        ok = (j["flag"] == wflag and (j["n_valid"] > 0) == wval
              and (j["n_badtest"] > 0) == wbad)
        bad += int(not ok)
        print(f"  {'✅' if ok else '❌'} {name:20s} flag={j['flag']!s:5s} "
              f"真判别{j['n_valid']} 测试错{j['n_badtest']} {j['first_error']}")
    # 抽取器也必须被打中：没有 assert 的回答不许算成「沉默」
    a1, why1, n1 = asserts_of("```python\nprint(1)\n```")
    ok1 = not a1 and bool(why1) and n1 == 0
    bad += int(not ok1)
    print(f"  {'✅' if ok1 else '❌'} 抽取器：无断言 ⇒ 判「没牙」({why1})")
    a2, why2, n2 = asserts_of("```python\nassert f([]) == []\n```")
    ok2 = bool(a2) and "只有" in why2 and n2 == 1
    bad += int(not ok2)
    print(f"  {'✅' if ok2 else '❌'} 抽取器：1 条 < {MIN_ASSERTS} ⇒ 判「没牙」({why2})")
    # C 臂（block 模式）两支：脚手架必须被删掉后照跑；没 assert 仍判没牙
    a3, why3, n3 = asserts_of(
        "```python\nfrom typing import List\n\ndef f(rows: List[str]) -> List[str]:\n"
        "    pass\n\ntest_cases = [[], [' a ']]\n"
        "assert all(isinstance(f(c), list) for c in test_cases)\n"
        "assert all(len(f(c)) == len(f(c)) for c in test_cases)\n"
        "assert all(all(x == x for x in f(c)) for c in test_cases)\n"
        "assert all(isinstance(f(c), list) for c in test_cases)\n```", "f", "block")
    ok3 = bool(a3) and not why3 and n3 == 4
    bad += int(not ok3)
    print(f"  {'✅' if ok3 else '❌'} 抽取器(block)：脚手架被删后照跑（{n3} 条 assert）")
    a4, why4, n4 = asserts_of(
        "```python\ndef f(rows):\n    pass\n\nprint(1)\n```", "f", "block")
    ok4 = not a4 and "一条 assert 都没有" in why4 and n4 == 0
    bad += int(not ok4)
    print(f"  {'✅' if ok4 else '❌'} 抽取器(block)：删完脚手架没断言 ⇒ 判「没牙」")
    # ⚠️ 明写这台仪器**测不出来**的一种坏法：条数够、但断言恒真。
    #    它会被算成「沉默」而不是「没牙」—— 这是已知盲区，不许当已解决。
    print("  ⚠️ 已知盲区：条数够但恒真的断言（`assert f([]) == []`）算「沉默」，"
          "不算「没牙」")
    print("✅ 四条分支 + 抽取器四支全部被样本打中" if not bad
          else f"❌ {bad} 个分支没被命中")
    return 0 if not bad else 1


# ===========================================================================
# 读真实读数：三次运行的答案 + 逐题得分
# ===========================================================================
def _load_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def load_pairs(answers: Path, runs: Path) -> list[dict]:
    """(run_id, task_id, 程序, 丢格, 真态类别) 的清单。"""
    recs = {str(r.get("run_id")): r for r in _load_jsonl(runs)}
    rows = _load_jsonl(answers)
    out: list[dict] = []
    for rid in RUN_IDS:
        rec = recs.get(rid)
        if rec is None:
            raise SystemExit(f"❌ runs.jsonl 里没有 run_id={rid}")
        suite = _suite_rows(str(rec["caliber"]["suite_sha"]))
        per = {str(t["id"]): float(t["score"]) for t in rec["report"]["per_task"]}
        ans = {str(r["task_id"]): str(r.get("answer") or "") for r in rows
               if str(r.get("run_id")) == rid and r.get("kind") == "answer" and r.get("task_id")}
        for row in suite:
            tid = str(row["id"])
            code = extract_code(ans.get(tid, ""))
            tests = list(row.get("tests") or [])
            fn = str((row.get("meta") or {}).get("spec", {}).get("fn") or f"norm_{tid}")
            cat, why = classify(code, tests, fn)
            n = len(tests) or CELLS_FALLBACK
            lost = int(round((1.0 - per.get(tid, 0.0)) * n))
            out.append({
                "run_id": rid, "task_id": tid, "fn": fn,
                "prompt": str(row.get("prompt") or ""),
                "code": code, "oracle": str(row.get("oracle") or ""),
                "lost": lost, "cells": n,
                "true_state": "FAIL" if lost else "PASS",
                "class": cat, "class_why": why,
            })
    return out


_SUITE_CACHE: dict[str, list[dict]] = {}


def _suite_rows(sha: str) -> list[dict]:
    """按 `suite_sha` 找题集。⛔ 不按题数/题名猜（§37）。"""
    if sha in _SUITE_CACHE:
        return _SUITE_CACHE[sha]
    from mw.bench import Suite                                  # noqa: PLC0415
    for p in sorted((ROOT / "bench" / "specgen").glob("*.jsonl")):
        if Suite.load(p).sha() == sha:
            _SUITE_CACHE[sha] = _load_jsonl(p)
            return _SUITE_CACHE[sha]
    raise SystemExit(f"❌ 找不到 suite_sha={sha[:12]} 对应的题集文件")


# ===========================================================================
# 主流程
# ===========================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="自检的检测面（§46，内核外预演）")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--arms", default="ab")
    ap.add_argument("--answers", default="bench_out/answers.jsonl")
    ap.add_argument("--runs", default="bench_out/runs.jsonl")
    ap.add_argument("--out", default="bench/evidence/selftest_s83")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 对（调试用）")
    ap.add_argument("--only", default="",
                    help="只跑指定对，格式 run_id/task_id,逗号分隔（补跑截断样本用）")
    ap.add_argument("--max-tokens", type=int, default=900,
                    help="单次生成上限。⚠️ 改了它就是**换口径**，结果不许与默认档并列")
    ap.add_argument("--redet", type=int, default=0, help="抽 N 对重跑，比 sha256")
    ap.add_argument("--report", action="store_true", help="只从已有 raw_*.jsonl 重出汇总")
    ap.add_argument("--cross", action="store_true",
                    help="断言级四格（零模型调用，只重跑已有回答）")
    ap.add_argument("--recompute", action="store_true",
                    help="用当前抽取口径重判已有 raw（零模型调用）")
    ap.add_argument("--model", default="qwen2.5:7b")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if a.report:
        return report(ROOT / a.out)
    if a.cross:
        return cross(ROOT / a.out, Path(a.answers), Path(a.runs))
    if a.recompute:
        return recompute(ROOT / a.out, Path(a.answers), Path(a.runs), a.max_tokens)

    pairs = load_pairs(Path(a.answers), Path(a.runs))
    if a.only:
        want = {x.strip() for x in a.only.split(",") if x.strip()}
        pairs = [p for p in pairs if f"{p['run_id']}/{p['task_id']}" in want]
        missing = want - {f"{p['run_id']}/{p['task_id']}" for p in pairs}
        if missing:
            raise SystemExit(f"❌ --only 里有对不上的键: {sorted(missing)}")
    if a.limit:
        pairs = pairs[:a.limit]
    arms = [x for x in a.arms if x in ARMS]
    if not arms:
        raise SystemExit("❌ --arms 里没有 a/b")

    from mw.config import LLMConfig                             # noqa: PLC0415
    from mw.llm import HTTPLLM, Message                         # noqa: PLC0415

    # ⚠️ `LLMConfig` 是 frozen dataclass —— 只能构造时给值，不能事后赋值
    #    （第一版这么写过，当场 `FrozenInstanceError`）。
    cfg = LLMConfig(
        base_url=os.environ.get("V1_LLM_BASE_URL", "http://127.0.0.1:11434/v1"),
        api_key=os.environ.get("V1_LLM_API_KEY", "ollama"),
        model=os.environ.get("V1_LLM_MODEL", a.model),
        temperature=0.0,
        timeout_s=180.0,
    )
    llm = HTTPLLM(cfg)

    outdir = ROOT / a.out
    outdir.mkdir(parents=True, exist_ok=True)
    tags = "".join(arms)
    raw = (outdir / f"raw_{tags}.jsonl").open("w", encoding="utf-8")

    def one(arm: str, pr: dict) -> dict:
        prompt = build_prompt(arm, pr["prompt"], pr["code"])
        text = llm.complete(
            [Message("system", SYSTEM), Message("user", prompt)],
            max_tokens=a.max_tokens, temperature=0.0, timeout_s=240.0)
        mode = "auto" if arm == "c" else "lines"
        units, toothless, n_asserts = asserts_of(text, pr["fn"], mode)
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        rec = {
            "arm": arm, "run_id": pr["run_id"], "task_id": pr["task_id"],
            "true_state": pr["true_state"], "class": pr["class"], "lost": pr["lost"],
            "n_asserts": n_asserts, "n_units": len(units),
            "toothless": toothless, "text_sha": sha, "text": text,
            "max_tokens": a.max_tokens,
        }
        if toothless:
            rec.update(flag=None, n_valid=0, n_badtest=0, n_pass=0,
                       first_error=toothless)
        else:
            j = judge(pr["code"], pr["oracle"], units)
            rec.update(flag=j["flag"], n_valid=j["n_valid"],
                       n_badtest=j["n_badtest"], n_pass=j["n_pass"],
                       first_error=j["first_error"])
        raw.write(json.dumps(rec, ensure_ascii=False) + "\n")
        raw.flush()
        return rec

    res: list[dict] = []
    total = len(pairs) * len(arms)
    k = 0
    for arm in arms:
        for pr in pairs:
            k += 1
            rec = one(arm, pr)
            res.append(rec)
            mark = ("没牙" if rec["flag"] is None else
                    (f"报警(真判别{rec['n_valid']}/测试错{rec['n_badtest']})"
                     if rec["flag"] else "沉默"))
            print(f"  [{k:3d}/{total}] {arm} 臂 {pr['run_id'][:5]} {pr['task_id']} "
                  f"真{pr['true_state']:4s} {pr['class']:9s} 丢{pr['lost']:2d}格 "
                  f"断言{rec['n_asserts']:2d} → {mark}", flush=True)

    if a.redet:
        print("\n复现性：抽样重跑，比对 text_sha256")
        same = diff = 0
        for rec in res[:a.redet * len(arms)]:
            pr = next(p for p in pairs
                      if p["run_id"] == rec["run_id"] and p["task_id"] == rec["task_id"])
            again = one(rec["arm"], pr)
            eq = again["text_sha"] == rec["text_sha"]
            same += int(eq)
            diff += int(not eq)
            print(f"  {'✅' if eq else '❌'} {rec['arm']} {rec['task_id']} "
                  f"{rec['text_sha'][:8]} vs {again['text_sha'][:8]}")
        print(f"  ⇒ 逐字相同 {same} / 不同 {diff}")

    raw.close()
    return summarize(res, outdir)


def summarize(res: list[dict], outdir: Path) -> int:
    arms = sorted({r["arm"] for r in res})
    cells_total = sum(r["lost"] for r in res if r["arm"] == arms[0])
    print()
    print("=" * 96)
    print("真值（题集 14 条断言） × 自检（模型自己写的断言，且已在 oracle 上交叉验过）")
    print("=" * 96)
    print(f"  {'臂':3s} {'真错':>4s} {'真检测':>5s} {'真检测率':>8s} {'工程报警':>6s} "
          f"{'可报格':>6s}/{'总丢格':>5s} | {'真对':>4s} {'工程报警':>6s} "
          f"{'工程虚警率':>9s} {'没牙':>4s}")
    summ: dict = {}
    for arm in arms:
        rs = [r for r in res if r["arm"] == arm]
        usable = [r for r in rs if r["flag"] is not None]
        fail = [r for r in usable if r["true_state"] == "FAIL"]
        ok = [r for r in usable if r["true_state"] == "PASS"]
        tip = [r for r in fail if r["n_valid"] > 0]        # 有真判别力的断言
        loud = [r for r in fail if r["flag"]]              # 工程口径：内核会看到的报警
        fp = [r for r in ok if r["flag"]]                  # 工程口径虚警
        d = (len(tip) / len(fail)) if fail else float("nan")
        f = (len(fp) / len(ok)) if ok else float("nan")
        cover = sum(r["lost"] for r in tip)
        lost_all = sum(r["lost"] for r in rs)
        summ[arm] = dict(n_fail=len(fail), tip=len(tip), det=d, loud=len(loud),
                         n_ok=len(ok), fp=len(fp), far=f,
                         toothless=len(rs) - len(usable),
                         cover=cover, lost=lost_all,
                         badtest=sum(r["n_badtest"] for r in usable),
                         valid=sum(r["n_valid"] for r in usable),
                         n_asserts=sum(r["n_asserts"] for r in usable))
        print(f"  {arm:3s} {len(fail):4d} {len(tip):5d} {d:8.1%} {len(loud):6d} "
              f"{cover:6d}/{lost_all:5d} | {len(ok):4d} {len(fp):6d} {f:9.1%} "
              f"{len(rs) - len(usable):4d}")

    print()
    print("  断言级明细（分母 = 该臂全部有牙断言数）：")
    for arm in arms:
        s = summ[arm]
        n = max(s["n_asserts"], 1)
        print(f"    {arm} 臂  共 {s['n_asserts']:3d} 条 ｜ 真判别 {s['valid']:3d} 条 "
              f"({s['valid'] / n:5.1%}) ｜ 测试自身写错 {s['badtest']:3d} 条 "
              f"({s['badtest'] / n:5.1%})")

    print()
    print("  按真态的类别拆（只算 FAIL 行；分子 = 有真判别力断言的题数）：")
    classes = sorted({r["class"] for r in res})
    print(f"  {'类别':10s} " + " ".join(f"{arm + ' 臂':>12s}" for arm in arms))
    by_class: dict = {}
    for c in classes:
        cells = []
        for arm in arms:
            rs = [r for r in res if r["arm"] == arm and r["class"] == c
                  and r["true_state"] == "FAIL" and r["flag"] is not None]
            cells.append(f"{sum(1 for r in rs if r['n_valid'] > 0)}/{len(rs)}")
            by_class.setdefault(c, {})[arm] = cells[-1]
        if any(x.split("/")[1] != "0" for x in cells):
            print(f"  {c:10s} " + " ".join(f"{x:>12s}" for x in cells))

    print()
    print("  ⇒ 可报格数上界 = 有真判别力断言的那些对子里，一共丢了多少格")
    print("     （**上界**：指出行为差 ≠ 改得对；§43.2 的 0/14→1/14 说的就是这一段损耗）")
    for arm in arms:
        s = summ[arm]
        print(f"     {arm} 臂 {s['cover']}/{cells_total} 格 = "
              f"{s['cover'] / max(cells_total, 1):.1%}")

    payload = {"arms": arms, "cells_total": cells_total, "summary": summ,
               "by_class": by_class,
               # ⚠️ 生成上限是**口径的一部分**（截断会吃掉 assert）⇒ 必须落盘
               "max_tokens": sorted({r.get("max_tokens", 0) for r in res}),
               "truncated_records": sum(
                   1 for r in res
                   if not str(r.get("text", "")).rstrip().endswith("```")),
               "truncated": sorted({(r["arm"], r["task_id"]) for r in res
                                    if not str(r.get("text", "")).rstrip().endswith("```")}),
               "prompts": {"system": SYSTEM, "a": _TMPL_A,
                           "b": _TMPL_B, "c": _TMPL_C,
                           "min_asserts": MIN_ASSERTS}}
    (outdir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  落盘 {outdir}/summary.json（原始回答在同目录 raw_*.jsonl）")
    return 0


def recompute(outdir: Path, answers: Path, runs: Path,
              max_tokens: int = 900) -> int:
    """用**当前**抽取口径把已有 raw 重判一遍（零模型调用）。

    为什么需要它：抽取口径改过两次（删脚手架、能拆就拆），而模型回答是**已有事实**、
    不该为口径改动重跑一遍模型（那会引入新的随机性，也让"改口径"变成"改数据"）。
    本函数只读 `raw_*.jsonl` 里的原文，重算 `flag/n_valid/n_badtest/n_pass`。
    ⚠️ 它**不**改 `raw_*.jsonl`（回答原文是证据，只读）。
    """
    pairs = {(p["run_id"], p["task_id"]): p for p in load_pairs(answers, runs)}
    out: list[dict] = []
    for p in sorted(outdir.glob("raw_*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            # 老记录里没有这个字段（当时是代码里写死的 900）；补一个**声明的**值，
            # 并在 PROVENANCE 里写明"这是声明的，不是记录自带的"。
            r.setdefault("max_tokens", max_tokens)
            pr = pairs[(r["run_id"], r["task_id"])]
            mode = "auto" if r["arm"] == "c" else "lines"
            units, why, na = asserts_of(r["text"], pr["fn"], mode)
            r["n_asserts"], r["n_units"], r["toothless"] = na, len(units), why
            if why:
                r.update(flag=None, n_valid=0, n_badtest=0, n_pass=0, first_error=why)
            else:
                j = judge(pr["code"], pr["oracle"], units)
                r.update(flag=j["flag"], n_valid=j["n_valid"],
                         n_badtest=j["n_badtest"], n_pass=j["n_pass"],
                         first_error=j["first_error"])
            out.append(r)
    print(f"重判 {len(out)} 条记录（零模型调用）")
    return summarize(out, outdir)


def cross(outdir: Path, answers: Path, runs: Path) -> int:
    """断言级四格交叉（**零模型调用**，只重跑已有回答）。

    为什么要它：题级读数只能说明「没检测到」。断言级四格能回答**为什么**：

    |  | 在参考上过 | 在参考上挂 |
    |---|---|---|
    | **在候选上过** | 无判别力（两边都对） | ⭐ **断言与错误行为一致** = 错信念被写进了测试 |
    | **在候选上挂** | ✅ 真判别力 | 断言自身写错 |

    ⭐ 那一格是本轮的核心机制证据：它数的是「模型的检查**恰好满意了它自己的错**」。
    若它非零，就不是"模型手算不行"，而是**同一个错信念同时长在代码和测试里** ——
    这跟"模型不够聪明"是两个完全不同的结论，前者是结构性的。
    """
    pairs = {(p["run_id"], p["task_id"]): p
             for p in load_pairs(answers, runs)}
    tally: dict[tuple[str, str], dict[tuple[bool, bool], int]] = {}
    n_unit: dict[tuple[str, str], int] = {}
    n_assert: dict[tuple[str, str], int] = {}
    for p in sorted(outdir.glob("raw_*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("flag") is None:
                continue
            pr = pairs[(r["run_id"], r["task_id"])]
            mode = "auto" if r["arm"] == "c" else "lines"
            units, why, na = asserts_of(r["text"], pr["fn"], mode)
            if why:
                continue
            for key in ((r["arm"], "全部"), (r["arm"], r["true_state"])):
                t = tally.setdefault(key, {})
                n_unit[key] = n_unit.get(key, 0) + len(units)
                n_assert[key] = n_assert.get(key, 0) + na
                for u in units:
                    ok_c, _ = run_one(pr["code"], u)
                    ok_o, _ = run_one(pr["oracle"], u)
                    t[(ok_c, ok_o)] = t.get((ok_c, ok_o), 0) + 1

    print("=" * 88)
    print("断言级四格（零点：候选 = 内核真实交的代码，参考 = 题集 oracle）")
    print("=" * 88)
    print("  ⚠️ A/B 臂的一行 = 一条 assert；C 臂的一行 = 一个代码块（内含 ≥4 条 assert，")
    print("     所以「单元」与「assert 条数」两列要分开看，不许混着比。")
    for key in sorted(tally):
        arm, st = key
        t = tally[key]
        n = max(n_unit[key], 1)
        g = lambda a, b: t.get((a, b), 0)          # noqa: E731
        print(f"\n  {arm} 臂 · 真态={st}   单元 {n_unit[key]} 个 / assert {n_assert[key]} 条")
        print(f"    候选过/参考过  {g(True, True):3d}  ({g(True, True) / n:5.1%})  无判别力")
        print(f"    候选过/参考挂  {g(True, False):3d}  ({g(True, False) / n:5.1%})  "
              f"断言只在候选上成立")
        print(f"    候选挂/参考过  {g(False, True):3d}  ({g(False, True) / n:5.1%})  "
              f"✅ 真判别力")
        print(f"    候选挂/参考挂  {g(False, False):3d}  ({g(False, False) / n:5.1%})  "
              f"两边都不是 ⇒ 断言自身写错")
        print(f"    ⇒ 与参考一致 {(g(True, True) + g(False, True)) / n:5.1%}"
              f" ｜ 与候选一致 {(g(True, True) + g(True, False)) / n:5.1%}")
    print()
    print("  ⛔ 两种「与候选一致」不许混为一谈：")
    print("     · 在**真错**的候选上「候选过/参考挂」⇒ 这条检查满意了错误行为")
    print("       （看起来像「错信念被写进测试」）；")
    print("     · 但候选**真对**时也会出现这一格 —— 那时它是「候选与参考在尺子")
    print("       没覆盖的输入上本来就不同」，不是模型的功劳也不是它的错。")
    print("     ⇒ 所以本表只报事实，不给动机归因；归因要靠逐题读答案（那是另一件事）。")
    return 0


def report(outdir: Path) -> int:
    """把该目录下所有 `raw_*.jsonl` 拼起来重出汇总（不重新问模型）。"""
    rows: list[dict] = []
    for p in sorted(outdir.glob("raw_*.jsonl")):
        rows += [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not rows:
        raise SystemExit(f"❌ {outdir} 里没有 raw_*.jsonl")
    return summarize(rows, outdir)


if __name__ == "__main__":
    raise SystemExit(main())
