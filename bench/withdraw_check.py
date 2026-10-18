#!/usr/bin/env python3
"""R13 撤回态驱动器 —— **逐条证明断言不是恒真**。

## 为什么它必须是一个入库的工具，而不是每轮手搓

第十九、二十轮各手搓过一次，两次都揪出了真问题：

  · 第十七轮 AO4/AO8：撤掉实现后**仍然是绿的**（AO4 因为代码里有两道等长闸；
    AO8 因为 FTS 失配时还有三条兜底会掩盖）；
  · 第十八轮 AP11：`.index()` 撤回后抛 `ValueError` ⇒ 不转红，还带走了汇总行；
  · 第十九轮 AQ4/AQ6/AQ7：一条让异常逃出去、一条按文件名匹配、一条锚在注释上。

⇒ **新写的断言必须逐个做撤回态。抽样做等于没做。**

## 三条硬要求（都是踩出来的）

1. **改文件 → 跑 → `finally` 还原**。第二十轮的第一版驱动器被前台超时 SIGKILL，
   `finally` 没跑，仓库里留下一个**变异过的 `memory.py`** —— 它和原文件几乎一样
   （`append` 换掉了 `replace`），下一次基线跑出 AR8 红、而全量套件是绿的，
   两个读数打架，根因是一个被 kill 的脚本。
   ⇒ 本版：变异前先落盘备份 + 每次启动先还原（幂等）+ **基线不绿就拒绝做撤回**。

2. **目标断言必须真的转红**，而且必须落在**同一组**里。撤 AS 组的实现却让 T 组转红，
   说明锚定跑偏了 —— 那不是"通过"，那是"测到了别的东西"。

3. **汇总行必须在**，且 `Traceback` 必须为 0。撤回态下实现被拿掉，
   很容易抛 `AttributeError` / `TypeError` 把整个测试打断 ——
   那时"转红"是假的（它是崩了，不是判红），而这正是 R13 那条硬坑。

用法：
    python3 bench/withdraw_check.py            # 全部跑一遍
    python3 bench/withdraw_check.py AS7 AS13   # 只跑指定几条
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
BAK = Path("/tmp/v1_withdraw_bak")
#: 变异登记表：{相对路径: {"mutated": 写进去那份内容的 sha}}。
#: ⛔ 没有它就回到了"有备份就无条件还原"的老行为 —— 那个行为本轮真的删掉过工作。
MANIFEST = BAK / "MANIFEST.json"

#: (断言号, 文件, 原文, 变异成)
#: `原文` 为空串 ⇒ **追加**到文件末尾。
#: ⚠️ 原文必须在文件里**恰好出现一次**：出现两次就会改到别处，
#:    那时"没转红"会被误读成"断言恒真"。
CASES: tuple[tuple[str, str, str, str], ...] = (
    # ── AS1：判据模块不许碰锚层 ────────────────────────────────────────
    ("AS1", "mw/verify.py", "",
     "\nfrom bench.scorers.code_exec import extract_code as _x  # 撤回态\n"),
    # ── AS2：抽取口径必须与打分器同构（去掉"裸围栏"分支 ⇒ 不同构）──────
    ("AS2", "mw/verify.py",
     'r"```(?:python|py)?[ \\t]*\\r?\\n(.*?)```"',
     'r"```python[ \\t]*\\r?\\n(.*?)```"'),
    # ── AS3：派生函数只许收 goal ──────────────────────────────────────
    ("AS3", "mw/verify.py", "def derive(goal: str) -> tuple[Check, ...]:",
     "def derive(goal: str, answer: str = \"\") -> tuple[Check, ...]:"),
    # ── AS4：散文题不许派生 compiles ──────────────────────────────────
    ("AS4", "mw/verify.py", 'if _CODE_ONLY.search(goal or ""):', "if True:"),
    # ── AS5：没有判据不许报通过 ───────────────────────────────────────
    ("AS5", "mw/verify.py",
     'return Verdict(False, ("(无判据)",), "本轮没有派生到任何判据")',
     'return Verdict(True, (), "")'),
    # ── AS6：未知判据类型不许静默放行 ─────────────────────────────────
    ("AS6", "mw/verify.py",
     'return f"未知判据类型 {c.kind!r}（未实现 ⇒ 不能算通过）"', 'return ""'),
    # ── AS7 / AS8：闸必须真的拦（`if v.ok` → `if True` ⇒ 永不拒）────────
    ("AS7", "mw/core.py",
     "        self.verify_stats[\"runs\"] += 1\n        if v.ok:",
     "        self.verify_stats[\"runs\"] += 1\n        if True:"),
    # ── AS9：打回必须有上限 ───────────────────────────────────────────
    ("AS9", "mw/core.py",
     'if self.verify_stats["failed"] > cap:',
     'if False:'),
    # ── AS10：答案必须被保住（把 salvage 置空 ⇒ 停滞出口交付空串）──────
    ("AS10", "mw/core.py", "self._salvage = result.output", 'self._salvage = ""'),
    # ── AS11：关掉必须真的关掉 ───────────────────────────────────────
    ("AS11", "mw/core.py",
     "vfy.derive(goal) if int(self.s.limits.verify_retries) > 0 else ())",
     "vfy.derive(goal))"),
    # ── AS12：开关必须进口径签名（从 ZERO_OK_KEYS 里拿掉）──────────────
    #: ⚠️ 锚点必须带上前面的整段元组：`ENV_KEYS` 与 `ZERO_OK_KEYS` 的**尾部逐字相同**
    #:    （都以 BACKOFF_S + VERIFY_RETRIES + `)` 收尾）。只锚尾部 ⇒ 命中 2 次，
    #:    改到的是 `ENV_KEYS`，而 AS12 恰好不会转红 —— 那时"没转红"会被误读成
    #:    "断言恒真"，实际是驱动器改错了地方。
    ("AS12", "mw/config.py",
     'ZERO_OK_KEYS: tuple[str, ...] = (\n'
     '    "V1_LLM_TEMPERATURE",\n'
     '    "V1_CLOSE_RESERVE_S",\n'
     '    "V1_CLOSE_RESERVE_STEPS",\n'
     '    "V1_LLM_RETRIES",\n'
     '    "V1_LLM_RETRY_BACKOFF_S",\n'
     '    "V1_VERIFY_RETRIES",\n)',
     'ZERO_OK_KEYS: tuple[str, ...] = (\n'
     '    "V1_LLM_TEMPERATURE",\n'
     '    "V1_CLOSE_RESERVE_S",\n'
     '    "V1_CLOSE_RESERVE_STEPS",\n'
     '    "V1_LLM_RETRIES",\n'
     '    "V1_LLM_RETRY_BACKOFF_S",\n)'),
    # ── AS13：闸只许挂在 finish 上（去掉 tool 守卫 ⇒ 每个动作都过闸）────
    #: ⚠️ 锚点第二十三轮改过一次：出口守卫从字面量 `cand.tool == "finish"`
    #:    升级成了 `_produces`（= finish 或 produces_answer 的外部能力），
    #:    否则外部能力的答案会绕过这道闸。锚点必须跟着实现走 ——
    #:    留着旧字面量的后果是"锚点出现 0 次"，驱动器当场拒绝执行（实测）。
    #:    AS13 的断言同时要求 `_produces` 的**定义里**必须含 finish，
    #:    所以"换成 _produces"不等于"闸被放宽"。
    ("AS13", "mw/core.py",
     "if _produces and result.ok and self._checks:",
     "if result.ok and self._checks:"),
    # ── AS14：salvage 只在答案为空时顶替（去掉 `not answer and` 前置条件）──
    #: ⚠️ AS8 没有自己的入口 —— 它与 AS7 锚在**同一行**（`if v.ok:`），
    #:   由 AS7 的撤回触发，靠 `COUPLED["AS7"]` 认账。抽样做撤回 = 没做，
    #:   所以这里逐条点名：AS8 走 AS7，其余每条都有自己的变异。
    ("AS14", "mw/core.py",
     "if not answer and self._salvage:", "if self._salvage:"),
    # ── AS15：汇总行必须念校验读数 ───────────────────────────────────
    #: ⚠️ 锚点**必须打在构造语句上**，不能只把 `{_vf_line}` 从 f-string 里摘掉。
    #:   第一版就是那么写的（把 log 的格式化串里 `{_vf_line}` 删掉），结果 AS15
    #:   **没转红** —— 因为 AS15 判的是 `_finish` **函数体里出现 `校验=` 与 `判据=`**，
    #:   而 `_vf_line = (f" | 校验= ...")` 这行构造语句还在函数体里，字符串仍在。
    #:   ⇒ 那条断言当时是恒真的，撤回态把它照出来了。现在整块换成一句 `_vf_line = ""`。
    ("AS15", "mw/core.py",
     '        _vf = stats["verify"]\n'
     '        _vf_line = (f" | 校验={_vf[\'runs\']}跑/{_vf[\'passed\']}过/{_vf[\'failed\']}打回"\n'
     '                    f"/{_vf[\'exhausted\']}放弃 判据={len(_vf[\'checks\'])}项")\n'
     '        if _vf["checks"]:\n'
     '            _vf_line += f"({\'/\'.join(_vf[\'checks\'])})"\n'
     '        if _vf["unverified_delivery"]:\n'
     '            _vf_line += " ⚠️本次交付未过自检"\n',
     '        _vf_line = ""\n'),
    # ── AS16：判读必须冻结且写着"不主张真模型分数" ────────────────────
    ("AS16", "bench/evidence/verify_s21/verdict.json",
     '"tag": "R5"', '"tag": "R4"'),
    # ── AS17：真模型读数的判定必须是负面的 Q3 ─────────────────────────
    ("AS17", "bench/evidence/verify_s21/real/verdict.json",
     '"tag": "Q3"', '"tag": "成立"'),
    # ── AS18：收尾必须留下结构化读数（触发数不涨 ⇒ 读数恒 0）────────────
    ("AS18", "mw/core.py",
     '        co["triggered"] += 1', '        co["triggered"] += 0'),
    # ── AS19：汇总行必须念收尾读数（把字面量改掉 ⇒ 日志里查不到）────────
    ("AS19", "mw/core.py",
     '_co_line = (f" | 收尾={_co[\'triggered\']}次(步{_co[\'by_reason\'][\'steps\']}"',
     '_co_line = (f" | 收尾X={_co[\'triggered\']}次(步{_co[\'by_reason\'][\'steps\']}"'),
    # ── AS20：`overrode_rejected` 必须只算"不同"（把判断写成恒假 ⇒ 全算成改）──
    ("AS20", "mw/core.py",
     'key = ("same_as_rejected" if best.strip() == self._salvage.strip()',
     'key = ("same_as_rejected" if False'),
    # ── AS21：初始化只许一处声明 + 两个调用点（删掉 `run()` 的那次调用）──
    #: 锚点必须带上**前一行注释**：两处调用逐字相同，只锚 `self._reset_closeout()` 会命中 2 次。
    ("AS21", "mw/core.py",
     '        #    "走到过几次、几次真的改了交付物"。而后者才是"它有没有用"的第一个问题。\n'
     '        self._reset_closeout()',
     '        #    "走到过几次、几次真的改了交付物"。而后者才是"它有没有用"的第一个问题。'),
    # ── AS22：驱动器不许无条件还原过期备份（把条件写成恒真）──────────────
    ("AS22", "bench/withdraw_check.py",
     '        if _sha(dst) == rec.get("mutated"):', '        if True:'),
    # ── AS23：基线汇总行必须锚在整句上（把前缀去掉，仍唯一但不再锚定）────
    #: ⚠️ 锚点**刻意不往回退成完全不锚定**的版本：那样驱动器自己就找不到唯一汇总行，
    #:    于是每一条撤回都会因为"汇总行不唯一"被记成失败 —— 那是驱动器自伤，
    #:    不是断言转红。用"少一个前缀"这个变异，行为不变、断言必红。
    # ══ AE 组（第二十三轮，外部能力）══════════════════════════════════════
    # ── AE1：内建工具不许带 produces_answer（给 `note` 单独打上标记）────────
    #: ⛔ 第一版把**默认值**翻成 True，结果 9 条断言同时红 —— 那不是锚定跑偏，
    #:   是变异太粗：所有工具的输出立刻都成了"候选答案"，任何依赖
    #:   "工具输出不是答案"的路径都会断。**改窄到单个工具**，信号才指向 AE1 本身。
    #: ⚠️ 第二版打在 `note` 上 —— **锚点在场、对象不在场**：`note` 只在
    #:    `memory is not None` 时注册，而 AE1 用的注册表没有 memory ⇒ 变异等于没改。
    #:    现在打在**必然注册**的 `http_get` 上（它也是真实的 `external=True` 工具）。
    ("AE1", "mw/tools.py",
     '        "http_get", "取一个 URL 的文本内容", {"url": "URL"}, _http_get, external=True,\n    ))',
     '        "http_get", "取一个 URL 的文本内容", {"url": "URL"}, _http_get, external=True,\n        produces_answer=True,\n    ))'),
    # ── AE2：开关必须登记 ENV_KEYS（删掉登记）────────────────────────────
    #: ⚠️ 会连带 A 组的"登记 == 实际读取"（那条本来就该红：代码读了没登记的 env
    #:    就是静默漂移），见 COUPLED。
    ("AE2", "mw/config.py",
     '    "V1_VERIFY_RETRIES",\n    "V1_EXTERNAL_ANSWER_TOOL",\n)',
     '    "V1_VERIFY_RETRIES",\n)'),
    # ── AE3：注入只许守在第 1 步（把 `it == 1` 放宽成 `it >= 1`）──────────
    ("AE3", "mw/core.py",
     "if _ext and it == 1 and self.tools.get(_ext) is not None:",
     "if _ext and it >= 1 and self.tools.get(_ext) is not None:"),
    # ── AE4：采用数必须独立计数（把 += 1 改成 += 0）──────────────────────
    #: 会连带 AE9（它断言 used_as_answer == 1），见 COUPLED。
    ("AE4", "mw/core.py",
     'self.external_stats["used_as_answer"] += 1',
     'self.external_stats["used_as_answer"] += 0'),
    # ── AE5：空答案必须 raise（改成把原文当答案返回）──────────────────────
    ("AE5", "mw/tools.py",
     '                    raise RuntimeError("外部 agent 未交付：answer 为空（不是 0 分，是没答案）")',
     '                    return raw'),
    # ── AE6：读数必须落盘（把键名改掉）──────────────────────────────────
    ("AE6", "run.py", '"kernel_external": _kex', '"kernel_external_x": _kex'),
    # ── AE7：外部 agent 不许 import 内核（加一行违约 import）──────────────
    ("AE7", "external/code_agent.py",
     "from __future__ import annotations\n",
     "from __future__ import annotations\nimport mw.config  # 撤回态：违约\n"),
    # ── AE8：--no-external 两个分支都要透传（把桩分支改成恒 True）─────────
    ("AE8", "run.py",
     "        core = Core(s, llm=StubLLM(), critique_ledger=critique_ledger,\n"
     "                    score_ledger=score_ledger, caliber_sig=caliber_sig,\n"
     "                    memory_mode=mem_mode,\n"
     '                    wire_external=not getattr(args, "no_external", False))',
     "        core = Core(s, llm=StubLLM(), critique_ledger=critique_ledger,\n"
     "                    score_ledger=score_ledger, caliber_sig=caliber_sig,\n"
     "                    memory_mode=mem_mode,\n"
     "                    wire_external=True)"),
    # ── AE9：端到端（把开关读成空 ⇒ 完全不注入）──────────────────────────
    ("AE9", "mw/core.py",
     "                _ext = self.s.limits.external_answer_tool",
     '                _ext = ""  # 撤回态：不注入'),
    # ── AS25：真模型收尾证据必须可重算（把触发次数改掉一个）────────────────
    ("AS25", "bench/evidence/closeout_s22/on.jsonl",
     '"triggered":2', '"triggered":3'),
    # ── AS24：收尾读数必须落盘（去掉"每题新建内核"那个分支的并入）──────────
    ("AS24", "run.py",
     '                        _kco_absorb(_kco, out.stats.get("closeout") or {})',
     '                        _ = 0  # 撤回：只接另一个分支'),
    ("AS23", "bench/withdraw_check.py",
     '_SUMMARY = re.compile(r"V1 契约测试汇总\\s+PASS=(\\d+)\\s+FAIL=(\\d+)")',
     '_SUMMARY = re.compile(r"汇总\\s+PASS=(\\d+)\\s+FAIL=(\\d+)")'),
)

#: 每条撤回**必然**连带转红的那些断言（同一行实现被两个断言锚定）。
#: ⛔ 写成白名单而不是"允许任意多条"：允许多条就等于不再检查锚定跑偏。
COUPLED: dict[str, frozenset[str]] = {
    # 闸永不拒（`if v.ok` → `if True`）时，连带转红的三条，理由各不相同：
    #   AS8  直接后果 —— 交付出去的就是第一版坏答案（分数停在 0）；
    #   AS9  从未打回 ⇒ `failed` 恒 0 ⇒ "超上限就放弃"这条分支**根本走不到**
    #        （它期望 failed==2）；
    #   AS10 同理 —— 没有打回就没有"重写失败"，`_salvage` 也就无从被触发。
    # ⇒ 这是**合理的连带**（上游断了，下游两条断言自然不成立），不是锚定跑偏。
    "AS7": frozenset({"AS8", "AS9", "AS10"}),
    # salvage 条件去掉 `not answer and` 之后，**正常交付**也会被上一版坏答案顶掉
    # ⇒ AS8（"第二版交对就交付它"）必然红。这正是 AS14 注释里预言的那个静默降级。
    "AS14": frozenset({"AS8"}),
    #: AE2 删掉 ENV_KEYS 登记 ⇒ A 组"登记 == 实际读取"必然红（代码读了没登记的 env
    #: 就是静默漂移，那条本来就该红）。这是**合理的连带**，不是锚定跑偏。
    "AE2": frozenset({"A1"}),
    #: AE4 把 used_as_answer 改成 += 0 ⇒ AE9 那条"答案被采用"必然红。
    "AE4": frozenset({"AE9"}),
    # 触发数不涨（`+= 0`）⇒ 汇总行也就没有"收尾=1次"可印 ⇒ AS19 必然红。
    # 上游的计数断了，下游"把计数念出来"这条断言自然不成立 —— 合理连带。
    "AS18": frozenset({"AS19"}),
    #: AS1 往 `mw/verify.py` 末尾**追加一行 import**（模拟"判据模块碰锚层"）。
    #: 那一行是**未被使用**的 ⇒ K5（"内核没有死导入"）必然红。
    #: 这是变异的直接副作用，不是锚定跑偏：要消掉多红只能让那行被用起来，
    #: 但那样变异就不再是"碰一下锚层"了（AS1 要抓的正是"碰"，不是"用"）。
    "AS1": frozenset({"K5"}),
    #: AS4 把 `if _CODE_ONLY.search(goal)` 改成 `if True` ⇒ **散文题也派生
    #: `compiles` 判据** ⇒ 那些题被自检错误打回 ⇒ 交付路径整个变了。
    #: 于是两条端到端读数自然不成立：
    #:   I1  —— `done` 不再达成（答案反复被打回，状态落到别的分支）；
    #:   AB7 —— "关掉工作记忆写入 ⇒ 回流率归零"的前提是有一条正常交付路径，
    #:           路径被打断后这条读数无意义。
    #: ⇒ 合理连带（上游断了，下游读数自然不成立）。
    "AS4": frozenset({"I1", "AB7"}),
}

#: ⚠️ 必须**锚在汇总行整句上**。第一版写的是 `PASS=(\d+)\s+FAIL=(\d+)`，
#:    它会在任何地方命中 —— 第二十二轮实测：契约测试中途崩了，
#:    驱动器抓到的是 **H8 那条断言自己打印的 `（PASS=7 FAIL=0）`**，
#:    于是"基线 7/0 全绿"这个假读数把它带过了基线闸（只因为 AS18/AS19 恰好也红）。
#:    ⇒ 判据必须要求"整句唯一"，而不是"某处出现过"。
_SUMMARY = re.compile(r"V1 契约测试汇总\s+PASS=(\d+)\s+FAIL=(\d+)")


def _cases_span(src: str) -> tuple[int, int]:
    """返回**本文件 CASES 表**的字符区间；不是本文件就返回 (-1, -1)。

    ⛔ 为什么需要它：本驱动器要撤回的锚点，有一部分就写在自己的 CASES 表里
    （`_SUMMARY = re.compile(...)`、`if _sha(dst) == rec.get("mutated")`）。
    直接 `src.count(old)` 会数到**表里那一份**，于是"锚点出现 2 次"地误报，
    或者更糟：替换到表里那一份，真代码根本没变、断言当然不转红。
    ⇒ 替换必须**落在 CASES 表之外**。
    """
    i = src.find("CASES: tuple[")
    if i < 0:
        return (-1, -1)
    j = src.find("\n)\n", i)
    return (i, (j + 3) if j >= 0 else len(src))


def _replace_outside_cases(src: str, old: str, new: str) -> tuple[str, int]:
    """只替换 CASES 表之外的**唯一**一处。返回 (新文本, 命中次数)。"""
    lo, hi = _cases_span(src)
    hits: list[int] = []
    k = src.find(old)
    while k >= 0:
        if not (lo <= k < hi):
            hits.append(k)
        k = src.find(old, k + 1)
    if len(hits) != 1:
        return src, len(hits)
    i = hits[0]
    return src[:i] + new + src[i + len(old):], 1


def _summary(out: str) -> tuple[bool, str]:
    """返回 (汇总行可信?, 展示用文字)。**必须恰好一条**才算可信。"""
    m = _SUMMARY.findall(out)
    if len(m) == 1:
        return True, f"{m[0]}"
    if not m:
        return False, "无汇总行"
    return False, f"汇总行出现 {len(m)} 次（不唯一）"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def _restore_all() -> tuple[list[str], list[str]]:
    """幂等还原上一次被 SIGKILL 打断留下的变异。返回 (已还原, 备份已过期而跳过)。

    ⛔ 第二十二轮的**实测事故**：「有备份就无条件还原」是破坏性的。

    上一轮留下的备份（第二十一轮的 `core.py`）在新一轮开工后被照着还原，
    把**本轮合法的修改**整个覆盖掉 —— `closeout_stats` 消失、契约从 444 条
    掉到 AS20 直接 AttributeError。而日志还写着"启动先还原 5 个残留变异文件"，
    读起来像在修，实际是在删。

    ⇒ 正确规则：**只还原"当前内容 == 我当初写进去的那份变异"的文件**。
    内容已经不是那个变异，说明现场已经前进了（人改了 / 另一个脚本改了），
    这时备份是**过期**的，绝不许覆盖 —— 而且必须**大声说出来**。
    """
    man = _load_manifest()
    done: list[str] = []
    skipped: list[str] = []
    for rel, rec in list(man.items()):
        dst = V1 / rel
        bak = BAK / rel
        if not (dst.exists() and bak.exists()):
            continue
        if _sha(dst) == rec.get("mutated"):
            shutil.copy2(bak, dst)
            done.append(rel)
        else:
            skipped.append(rel)
    if man:
        MANIFEST.unlink(missing_ok=True)
    return done, skipped


def _load_manifest() -> dict[str, dict]:
    if not MANIFEST.exists():
        return {}
    try:
        d = json.loads(MANIFEST.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _mark(rel: str, *, mutated: str | None) -> None:
    """登记 / 注销一个变异。`mutated=None` ⇒ 注销（已还原）。"""
    man = _load_manifest()
    if mutated is None:
        man.pop(rel, None)
    else:
        man[rel] = {"mutated": mutated}
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")


def _backup(rel: str) -> None:
    src = V1 / rel
    dst = BAK / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _run_suite() -> tuple[bool, set[str], str]:
    """跑契约测试。返回 (基线绿?, 判红的断言集合, 原始输出尾部)。"""
    p = subprocess.run([sys.executable, "tests/contract_test.py"], cwd=str(V1),
                       capture_output=True, text=True, timeout=900)
    out = (p.stdout or "") + (p.stderr or "")
    summary_ok, _shown = _summary(out)
    m = _SUMMARY.findall(out)
    passed, failed = (int(m[0][0]), int(m[0][1])) if summary_ok else (0, 0)
    #: ⚠️ 第二十三轮把标签范围从 `AS\d+` 放宽到 `[A-Z]{1,2}\d+`：
    #:    新加的 AE 组（外部能力）若不进来，"撤回 AE7 却没转红"就会被读成通过。
    #:    只认"❌ 紧跟标签"的形状 —— 被撤掉的子进程自己打印的 `❌ 长度不等…`
    #:    不带标签，不会被误算进红集。
    red = set(re.findall(r"❌\s*([A-Z]{1,2}\d+)\b", out))
    clean = summary_ok and "Traceback" not in out
    return (p.returncode == 0 and failed == 0), red, out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="R13 撤回态驱动器")
    ap.add_argument("tags", nargs="*", help="只跑这些断言号（缺省=全部）")
    args = ap.parse_args(argv)

    restored, stale = _restore_all()
    if restored:
        print(f"  ↩️  启动先还原 {len(restored)} 个残留变异文件：{restored}")
    if stale:
        print(f"  ⚠️  {len(stale)} 个备份**已过期**（当前内容 ≠ 当初写进去的变异）"
              f"⇒ 不还原：{stale}")
        print("      （第二十二轮事故：无条件还原把本轮合法修改整个覆盖掉，"
              "契约从 444 条掉到 AttributeError。）")

    print("一、基线必须先绿（不绿就拒绝做撤回）")
    base_ok, base_red, base_out = _run_suite()
    _bs_ok, _bs_show = _summary(base_out)
    print(f"  {'✅' if base_ok else '❌'} 基线 {_bs_show} "
          f"红={sorted(base_red) or '无'}")
    if not base_ok:
        print("  ⛔ 基线不是绿的 ⇒ 撤回态的读数没有意义，拒绝继续。")
        print(base_out[-1200:])
        return 2

    # ⛔ 边跑边改会让整轮读数作废：本轮实测踩过一次 —— 跑到 AS2 时我正在改
    #    `tests/contract_test.py`，那次子进程读到了半成品，报"无汇总行 + Traceback"，
    #    看起来像 AS2 的锚点坏了，实际是**我自己在动被测文件**。
    #    驱动器必须自己发现这件事，而不是等我去回忆时间线。
    _guard = _sha(V1 / "tests" / "contract_test.py")

    want = set(args.tags) if args.tags else None
    rows = []
    ok_all = True
    for tag, rel, old, new in CASES:
        if want and tag not in want:
            continue
        path = V1 / rel
        before = _sha(path)
        try:
            _backup(rel)
            src = path.read_text(encoding="utf-8")
            if old == "":
                path.write_text(src + new, encoding="utf-8")
            else:
                mutated, n = _replace_outside_cases(src, old, new)
                if n != 1:
                    rows.append((tag, f"❌ 变异锚点出现 {n} 次（必须恰好 1 次）", ""))
                    ok_all = False
                    continue
                path.write_text(mutated, encoding="utf-8")
            # 变异落盘之后**立刻**登记：登记的是"我写进去的那份内容"的 sha，
            # 下一次启动只有"当前内容 == 它"时才允许还原（否则备份已过期）。
            _mark(rel, mutated=_sha(path))
            _, red, out = _run_suite()
            hit = tag in red
            want_red = {tag} | set(COUPLED.get(tag, ()))
            extra = red - want_red
            _ok_s, _show_s = _summary(out)
            clean = "Traceback" not in out and _ok_s
            good = hit and not extra and clean
            ok_all = ok_all and good
            detail = (f"红={sorted(red) or '无'} 目标={'✔' if hit else '✘'}"
                      f" 多红={sorted(extra) or '无'} 汇总行={_show_s}"
                      f" Traceback={'有' if 'Traceback' in out else '无'}")
            rows.append((tag, "✅" if good else "❌", detail))
        finally:
            src_bak = BAK / rel
            if src_bak.exists():
                shutil.copy2(src_bak, path)
            _mark(rel, mutated=None)
            after = _sha(path)
            if after != before:
                rows.append((tag, "❌ 还原失败（sha 不一致）", f"{before} → {after}"))
                ok_all = False

    print("\n二、逐条撤回（目标必须转红，且**只**它转红）")
    for tag, flag, detail in rows:
        print(f"  {flag} {tag:5s} {detail}")

    # 还原之后必须仍然全绿 —— 否则"撤回"留下的痕迹会被当成下一轮的基线。
    print("\n三、还原后再跑一次（防止驱动器自己被写进仓库）")
    back_ok, back_red, back_out = _run_suite()
    _bk_ok, _bk_show = _summary(back_out)
    print(f"  {'✅' if back_ok else '❌'} 还原后基线 {_bk_show} "
          f"红={sorted(back_red) or '无'}")
    ok_all = ok_all and back_ok

    if _sha(V1 / "tests" / "contract_test.py") != _guard:
        print("\n  ⛔ 运行期间 `tests/contract_test.py` 被改动 ⇒ 本轮读数作废（边跑边改）")
        ok_all = False

    print("\n" + "=" * 74)
    print(f"  {'R13 通过：逐条撤回都精确转红' if ok_all else 'R13 失败：见上'}")
    print("=" * 74)
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
