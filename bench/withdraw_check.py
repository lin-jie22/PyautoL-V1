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
     '    "V1_VERIFY_RETRIES",\n    "V1_EXTERNAL_ANSWER_TOOL",\n',
     '    "V1_VERIFY_RETRIES",\n'),
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
    # ══ AT1–AT8（第二十四轮）：口径签名补齐 / 失败可归因 / 探针求和 ═════════
    # ── AT1：指纹与真实 Core 脱节（占位记忆换成 None ⇒ 少 recall/note 两个工具）──
    ("AT1", "mw/tools.py",
     "    register_core_tools(reg, memory=_StubMemory() if with_memory else None,\n"
     "                        workspace=workspace, allow_note=allow_note)",
     "    register_core_tools(reg, memory=None,\n"
     "                        workspace=workspace, allow_note=allow_note)"),
    # ── AT2：字段填了但填的是空串（"只加字段不填值"的撤回态）────────────────
    ("AT2", "run.py",
     "tool_catalog_sha=tools_catalog_sha(",
     'tool_catalog_sha=(lambda *_a, **_k: "")('),
    # ── AT3：目录行里去掉描述（改 agents.json 描述不再改变提示词指纹）────────
    ("AT3", "mw/tools.py",
     '        return f"- {self.name}({args}): {self.description}{mark}{mut}"',
     '        return f"- {self.name}({args}): ^{mark}{mut}"  # 撤回态：描述不进口录'),
    # ── AT4：豁免表形同虚设（放行一切，只报 declared）────────────────────────
    ("AT4", "mw/bench.py",
     "        want = {str(x) for x in (declared or ())}\n"
     "        diff = self.diff(other)\n"
     "        return ([k for k in diff if k not in want],\n"
     "                [k for k in diff if k in want])",
     "        diff = self.diff(other)\n"
     "        return ([], diff)  # 撤回态：声明形同虚设"),
    # ── AT5：把目录指纹塞回账本签名（历史 8 个会话的行会全部失效）────────────
    ("AT5", "mw/ledger.py",
     'LEDGER_SIG_EXCLUDE: tuple[str, ...] = ("suite_sha", "repeat", "tool_catalog_sha")',
     'LEDGER_SIG_EXCLUDE: tuple[str, ...] = ("suite_sha", "repeat")'),
    # ── AT6：探针只读最后一条记录（第三次踩的那个坑）────────────────────────
    ("AT6", "bench/probe_external_real.py",
     '    for r in rows:\n        ex = r.get("kernel_external") or {}',
     '    for r in rows[-1:]:\n        ex = r.get("kernel_external") or {}'),
    # ── AT7："没核验到"被读成"核验通过"（三态塌成两态）──────────────────────
    ("AT7", "bench/probe_external_real.py",
     '    if fails:\n        out["status"] = "fail"',
     '    if False:  # 撤回态：核验永不判失败\n        out["status"] = "fail"'),
    # ── AT8：失败原因不落盘（只留 last_error 的老写法）──────────────────────
    ("AT8", "mw/core.py",
     "                            if _why not in _rs and len(_rs) < _ERR_REASON_CAP:\n"
     "                                _rs.append(_why)",
     "                            _ = _why  # 撤回态：原因不进证据"),
    # ══ AU 组（第二十五轮，重试链守墙钟 + 读数分辨率）══════════════════════
    # ── AU1：`check_wall()` 不再查墙钟（加一行 return 让它永远放行）────────
    ("AU1", "mw/budget.py",
     "    def check_wall(self) -> None:",
     "    def check_wall(self) -> None:\n        return  # 撤回态：墙钟闸失效"),
    # ── AU2：不再裁剪（上限恒等于端点默认值 ⇒ 单次又能吃满整题预算）────────
    #: 会连带 AU4：AU4 的夹具正是"第一发被裁短"，不裁剪就没有 shortened 可计。
    ("AU2", "mw/core.py",
     "        return min(float(base), float(left))",
     "        return float(base)  # 撤回态：不裁剪"),
    # ── AU3：拒绝发起失效（不再返回 0 ⇒ 发出一个注定超时的请求）──────────
    #: 连带 AU5/AU8：两者的前提都是"这一发被拒绝"，拒绝失效后它们走不到那一格。
    ("AU3", "mw/core.py",
     "        if left <= self.MIN_ATTEMPT_S:\n            return 0.0",
     "        if False:  # 撤回态：拒绝发起失效\n            return 0.0"),
    # ── AU4："裁短但仍发出"不再计数（证据看不见它要证明的事）──────────────
    ("AU4", "mw/core.py",
     "                if _base is not None and float(_to) < float(_base):\n"
     "                    self.wall_shortened += 1",
     "                if False:  # 撤回态：裁短不计数\n"
     "                    self.wall_shortened += 1"),
    # ── AU5：两个"放弃"计数混成一个（首发/重试分不开）──────────────────────
    #: 连带 AU4：它断言 `wall_refused_first == 0`（首发确实发出去了），
    #: 计数被对调后这个数变成 1 ⇒ AU4 必红。
    ("AU5", "mw/core.py",
     "                if attempt == 0:\n                    self.wall_refused_first += 1",
     "                if attempt != 0:  # 撤回态：两种放弃混为一谈\n"
     "                    self.wall_refused_first += 1"),
    # ── AU6：离线桩也被裁剪（桩分支返回一个具体上限，而非"不裁剪"的 inf）────
    #: ⛔ 第一版把整个分支删掉（`if False:`）—— 后果是**所有离线路径**都
    #:    `float(None)` 崩掉，31 条断言同时红。那不是锚定跑偏，是变异太粗：
    #:    桩分支是整条离线套件的地基。**改窄到只改返回值**，信号才指向 AU6 本身
    #:    （1e9 秒的 timeout 在离线路径上完全等价于 inf）。
    ("AU6", "mw/core.py",
     '        if base is None:                     # 桩/无网络：不裁剪\n'
     '            return float("inf")',
     '        if base is None:                     # 桩/无网络：不裁剪\n'
     '            return 1e9  # 撤回态：桩上限不再是 inf'),
    # ── AU7：收尾不释放余量（"留了 45s 却不用"）────────────────────────────
    ("AU7", "mw/core.py",
     "        if self._counted is not None:\n            self._counted.reserve_release()",
     "        if False:  # 撤回态：收尾不释放余量\n"
     "            self._counted.reserve_release()"),
    # ── AU8：墙钟被裁到底时**不再**转向交付（退回"交付零东西"）────────────
    ("AU8", "mw/core.py",
     "                if _left > _LedgeredLLM.MIN_ATTEMPT_S:",
     "                if False:  # 撤回态：墙钟裁到底也不转向收尾"),
    # ── AU9：累加器只覆盖不加总（读数字段退化成"最后一题的值"）────────────
    ("AU9", "run.py",
     '    for k in ("retries", "exhausted", "wall_shortened",\n'
     '              "wall_refused_first", "wall_refused_retry"):',
     '    for k in ("retries", "exhausted", "wall_shortened",\n'
     '              "wall_refused_first", "wall_refused_retry"):\n'
     '        acc[k] = int(stats.get(k, 0) or 0)  # 撤回态：覆盖而非加总\n'
     '        continue'),
    # ── AU10：汇总行只念固定的两个原因（新原因在日志上消失）────────────────
    ("AU10", "mw/core.py",
     '                        + "".join(f"/{k}{v}" for k, v in _co["by_reason"].items()\n'
     '                                  if k not in ("steps", "wall") and v)\n',
     '                        + ""  # 撤回态：只念固定的两个原因\n'),
    # ── AV1：打回落盘的 failure 行把错误原文从 content 里抽走（meta 保留）────
    ("AV1", "mw/core.py",
     '                        self.memory.record(\n'
     '                            "failure",\n'
     '                            f"{cand.as_line()} — {result.error[:200]}",\n'
     '                            error=result.error[:300])',
     '                        self.memory.record(\n'
     '                            "failure", cand.as_line(),  # 撤回态：content 无诊断原文\n'
     '                            error=result.error[:300])'),
    # ── AV2：超限交付不落 failure（"最终为什么没过闸"零痕迹）────────────────
    ("AV2", "mw/core.py",
     '            self.memory.record(\n'
     '                "failure", f"自检超限未过（{self.verify_stats[\'failed\']}次）— {v.report[:200]}",\n'
     '                error=v.report[:300])',
     '            _ = v.report  # 撤回态：超限交付不落情景记忆'),
    # ── AV3：蒸馏原料不排序（failure 混回堆里，注意力最低处）────────────────
    ("AV3", "mw/abstractor.py",
     '    eps.sort(key=lambda e: (0 if getattr(e, "kind", "") in _FAIL_KINDS else 1,\n'
     '                            -int(getattr(e, "id", 0) or 0)))\n'
     '    return eps',
     '    return eps  # 撤回态：不排序，failure 混回堆里'),
    # ── AW1：note 注册门槛失效（无条件注册）⇒ 默认行为被偷换 ────────────────
    #: ⚠️ 变异必须打在"默认路径也拿得到 note"上（`if False:`）——
    #:    打成 `if True:` 时默认路径照样注册 ⇒ AW1 自己不红（锚定跑偏，首跑实测）。
    ("AW1", "mw/tools.py",
     '        if allow_note:\n'
     '            _put(ToolSpec(\n'
     '                "note", "写入一条语义记忆（自动去重）",',
     '        if False:\n'
     '            _put(ToolSpec(\n'
     '                "note", "写入一条语义记忆（自动去重）",'),
    # ── AW2：开关被无视（allow_note=False 也注册）⇒ OFF 臂照样能写 ─────────
    ("AW2", "mw/tools.py",
     '        if allow_note:\n'
     '            _put(ToolSpec(\n'
     '                "note", "写入一条语义记忆（自动去重）",',
     '        if memory is not None:\n'
     '            _put(ToolSpec(\n'
     '                "note", "写入一条语义记忆（自动去重）",'),
    # ── AW3：关写入连读也关了（整块 memory 工具被 allow_note 门控）──────────
    ("AW3", "mw/tools.py",
     '    if memory is not None:\n'
     '        def _recall(args: dict) -> str:',
     '    if memory is not None and allow_note:\n'
     '        def _recall(args: dict) -> str:'),
    # ── AW4：env 不生效（default_factory 忽略 V1_ALLOW_NOTE）────────────────
    ("AW4", "mw/config.py",
     '    allow_note: bool = field(\n'
     '        default_factory=lambda: _env_bool("V1_ALLOW_NOTE", True))',
     '    allow_note: bool = field(\n'
     '        default_factory=lambda: True)  # 撤回态：env 读了个寂寞'),
    # ── AW5：Core 透传线断（硬编码 True）⇒ Limits 的开关到不了注册表 ────────
    ("AW5", "mw/core.py",
     '                            allow_note=self.s.limits.allow_note)',
     '                            allow_note=True)'),
    # ── AX1：沉淀本身被撤（commit_fact 不再被调用）⇒ 默认行为被偷换 ──────────
    #: ⚠️ 变异必须打在 **commit** 上：AX1 断言的是默认（True）路径的行为，
    #:    打在 allow_learn 检查上时默认路径照常沉淀 ⇒ AX1 自己不红（首跑实测）。
    ("AX1", "mw/worldmodel.py",
     '        self.memory.commit_fact(mm.as_heuristic(), source="worldmodel", confidence=0.7)',
     '        _ = mm.as_heuristic()  # 撤回态：失配不再沉淀为语义事实'),
    # ── AX2：开关被无视（allow_learn=False 也沉淀）──────────────────────────
    ("AX2", "mw/worldmodel.py",
     '        if not mm.teachable:\n'
     '            return\n'
     '        if not self.allow_learn:\n'
     '            return',
     '        if not mm.teachable:\n'
     '            return\n'
     '        if not (self.allow_learn or True):\n'
     '            return'),
    # ── AX3：env 不生效（default_factory 忽略 V1_ALLOW_LEARN）───────────────
    ("AX3", "mw/config.py",
     '    allow_learn: bool = field(\n'
     '        default_factory=lambda: _env_bool("V1_ALLOW_LEARN", True))',
     '    allow_learn: bool = field(\n'
     '        default_factory=lambda: True)  # 撤回态：env 读了个寂寞'),
    # ── AX4：Core 透传线断（硬编码 True）⇒ Limits 的开关到不了世界模型 ───────
    ("AX4", "mw/core.py",
     '                             allow_learn=self.s.limits.allow_learn)',
     '                             allow_learn=True)'),
    # ── AY1/AY2：训练史的闸从内容退回"看谁的通过/未通过都算" ─────────────────
    #: ⚠️ 这**一条变异同时打红 AY1 和 AY2**，且是**合理的连带**：
    #:    两条断言是同一个命题的正面用例与反面用例（有失败 / 全通过），
    #:    实现只有一处 ⇒ 一起翻。写进 COUPLED 白名单，不当作锚定跑偏。
    #:    `'%通过%'` 同时命中「未通过」与「通过」⇒ 反向用例（全通过应为 0）必红。
    ("AY1", "bench/build_g7_seed.py",
     "        \"AND content LIKE '%未通过%'\").fetchone()[\"c\"]",
     "        \"AND content LIKE '%通过%'\").fetchone()[\"c\"]"),
    # ── AY3：注入诊断少扫几格 ⇒ 「48/48 存活」这句话就不再可复算 ─────────────
    ("AY3", "bench/probe_inject.py",
     "    for n_wm in (0, 1, 2, 4):",
     "    for n_wm in (0, 1, 2):"),
    # ── AZ 组（§17-7d 修正⑨）：安慰剂与处理的**等价性**必须逐条可撤 ─────────
    # AZ1：每个汉字替换成**两个**字 ⇒ 长度不再相等（条数仍相等）。
    ("AZ1", "bench/build_g7d_seed.py",
     "                buf.append(_SCRAMBLE_POOL[(ord(ch) * 31 + i) % len(_SCRAMBLE_POOL)])",
     "                buf.extend([_SCRAMBLE_POOL[(ord(ch) * 31 + i) % len(_SCRAMBLE_POOL)]] * 2)"),
    # AZ2：条件反过来 ⇒ 留下的是汉字、被替换的是 ASCII ⇒ 检索行为不再等价。
    ("AZ2", "bench/build_g7d_seed.py",
     '            if ord(ch) < 128 or not ("\\u4e00" <= ch <= "\\u9fff"):',
     '            if "\\u4e00" <= ch <= "\\u9fff":'),
    # AZ3：不替换汉字 ⇒ 安慰剂与原句逐字相同（P ≡ H）。
    ("AZ3", "bench/build_g7d_seed.py",
     "                buf.append(_SCRAMBLE_POOL[(ord(ch) * 31 + i) % len(_SCRAMBLE_POOL)])",
     "                buf.append(ch)"),
    # AZ4：拆掉"已有种子库就拒绝重建"的闸 ⇒ 旧 facts 可以静默混入。
    ("AZ4", "bench/build_g7d_seed.py",
     "    if stale:",
     "    if False and stale:"),
    # AZ5：把一条惯例写超 120 字 ⇒ 注入时会被 core.py 静默截断。
    ("AZ5", "bench/build_g7d_seed.py",
     '        "金额惯例：千分位用空格分隔，保留两位小数，负数用圆括号包裹、不写负号。",',
     '        "金额惯例：千分位用空格分隔，保留两位小数，负数用圆括号包裹、不写负号；'
     '这条被故意写长到超过一百二十个字，用来证明注入截断的闸真的会转红，'
     '而不是一句装饰性的注释在测试里恒绿；再补一句让它足够长，长到必须被截断才能通过；'
     '还要再长一点，否则它自己就只是一条没越过阈值的普通注释而已。",'),
    # AZ6：派生时把 tests 丢掉 ⇒ 除题面外不再是逐字段不动（7e 的自变量不干净）。
    ("AZ6", "bench/build_g7e_hint.py",
     '            "tests": row["tests"],',
     '            "tests": [],'),
    # AZ7：派生器退化成恒等函数 ⇒ 题面根本没指向记忆（唯一变更没发生）。
    ("AZ7", "bench/build_g7e_hint.py",
     "    return prompt.replace(BASE_CLAUSE, HINT_CLAUSE)",
     "    return prompt"),
    # AZ8：把**已采用**的那条也改掉 ⇒ 7f 不再是"只改未采用的三条"，
    #      读数分不清"例子起作用"与"把已经会的那条改坏了"。
    ("AZ8", "bench/build_g7d_seed.py",
     "FACTS_G7F: dict[str, str] = dict(FACTS)",
     'FACTS_G7F: dict[str, str] = {**FACTS, '
     '"g7d2_dedupe_keep": "改动过的去重惯例"}'),
    # AZ9：往 7f 变体里塞一个 7d 没有的键 ⇒ 键集不再相同（多出一条无题可配的惯例）。
    ("AZ9", "bench/build_g7d_seed.py",
     "FACTS_G7F.update({",
     'FACTS_G7F.update({"g7f_extra": "多余的惯例",'),
    # AZ10：给例子塞一个**题面里也有**的中文 token ⇒ 该条被 FTS 精确命中，
    #        H 臂注入从 4 条降到 1 条（P 臂不会同步）⇒「形式」与「注入量」同时变。
    ("AZ10", "bench/build_g7d_seed.py",
     "\"不超长时原样返回。例：'0123456789' 取 n=4 得 '…6789'。\",",
     "\"不超长时原样返回。例：'0123456789' 取 n=4 得 '…6789'，超长时保留哪一头？\","),
    # AZ11：例子带连续空白 ⇒ `commit_fact` 的 normalize 会折叠/ strip，
    #        存进库的文本 ≠ FACTS 原文 ⇒ R1 的 own-fact 永远判不过。
    ("AZ11", "bench/build_g7d_seed.py",
     "\"例：'YES' 为真、'on' 为假、'maybe' 为 None。\",",
     "\"例：'YES' 为真、'on' 为假、'  1 ' 为真、'maybe' 为 None。\","),
    # AZ12：把观察对象也一起填充 ⇒ "邻域长度 → 它的分数"这个单变量没了，
    #       曲线变成"它自己的长度 → 它的分数"（7f 那种混淆的更高配版）。
    #       ⚠️ 用"追加"而不是 `_pad_to`：后者在 l40 会**截断**观察对象 ⇒
    #          连带改变它的 token 交集 ⇒ 顺带把 AZ14 也打红（那不是同一个事实）。
    ("AZ12", "bench/build_g7d_seed.py",
     "    return {k: (pool[k] if k == obs_key else _pad_to(FACTS[k], target))",
     "    return {k: (pool[k] + PAD if k == obs_key else _pad_to(FACTS[k], target))"),
    # AZ13：填充只补一句就返回 ⇒ 档位 60/84/120 在库里全是 35+PAD 长度，
    #       "档位"不再等于长度，四档实际只有一档。
    ("AZ13", "bench/build_g7d_seed.py",
     "    return text + (PAD * (need // len(PAD) + 1))[:need]",
     "    return text + PAD"),
    # AZ14：填充句里塞 ASCII ⇒ FTS 命中面可能变（本条测 ①）。
    #       ⚠️ 刻意**不带空格**：带空格会让"补到第 ℓ 字"的切片可能以空格结尾，
    #          `normalize` 一 strip 长度就不再等于 ℓ ⇒ 顺带把 AZ13 也打红。
    ("AZ14", "bench/build_g7d_seed.py",
     'PAD = "此处为长度档位所用的填充文字与本题无关。"',
     'PAD = "此处为长度档位n所用的填充文字与本题无关。"'),
    # AZ15：名字说 l120、值给 84 ⇒ 轴重复（两个 84），"120 档"根本不存在。
    ("AZ15", "bench/build_g7d_seed.py",
     '"l120": 120}',
     '"l120": 84}'),
    # AZ16：工作记忆只保留**头** 200 字而不是尾 200 字 ⇒ 泄漏到后续题的那一段
    #       换成了输出的开头（"哪一段会漏"变了，长度轴上的非单调性也就没了依据）。
    ("AZ16", "mw/core.py",
     "w if w.startswith(EXPAND_MARK) else w[-200:] for w in ws)",
     "w if w.startswith(EXPAND_MARK) else w[:200] for w in ws)"),
    # ══════════════════════════════════════════════════════════════════════
    # AZ17–AZ21（§17-7h）：**修装置**那四条各自的撤回态。
    # ══════════════════════════════════════════════════════════════════════
    # AZ17：把"清掉了几条"换成常数 0 ⇒ 清空**照旧发生**，但读数消失
    #       ⇒ "清干净了"与"没接线"再也分不开（字段必须有消费者）。
    #       ⚠️ 刻意**不动**清空那一行：动了它 AZ18 也会红（见 COUPLED）。
    ("AZ17", "mw/core.py",
     '            "working_cross_task": self.working_cross_task,',
     '            "working_cross_task": 0,'),
    # AZ18：题边界不再清空 ⇒ 第 1 题 `recall` 出来的全部 facts 留在工作记忆里，
    #       第 2 题第 1 步的 state 里就会出现非本题惯例（正是 7g 实测的形状）。
    ("AZ18", "mw/core.py",
     "        self.working_cross_task = self.memory.reset_working()",
     "        self.working_cross_task = 0"),
    # AZ19：审计退回"整段里有就算"（`others` 恒空）⇒ 经 `[工作记忆]` 漏进来的
    #       再也看不见 —— 那正是旧判据 `want[:120] in st` 的病。
    ("AZ19", "bench/state_audit.py",
     '    others = "".join(v for k, v in blk.items() if k not in _SEMANTIC_HEADS)',
     '    others = ""'),
    # AZ20：作废规则恒假 ⇒ 路径不同的格不再被标出来（"路径"这条读数等于没进)。
    ("AZ20", "bench/transport.py",
     "    void = checked and len(sigs) > 1",
     "    void = False"),
    # AZ21：逐题墙钟写死 0 ⇒ 字段在、但完全没有信息（"最坏值"仍然不可知）。
    ("AZ21", "mw/bench.py",
     '"elapsed_s": round(time.time() - _t_task, 2)})',
     '"elapsed_s": 0})'),
    # ══════════════════════════════════════════════════════════════════════
    # AZ22–AZ23（§17-7i）：删掉静默兜底那两级，各配一条撤回态。
    # ══════════════════════════════════════════════════════════════════════
    # AZ22a：把第三级"全表 top-k"原样加回 ⇒ 零命中又变成"给全表"。
    #        ⚠️ 会连带打红 AZ23（前两题的 0 条变回 4 条）—— 见 COUPLED：
    #           两件事锚在同一段 SQL 上，本来就是同一条事实的两个面。
    ("AZ22", "mw/memory.py",
     '            (like, k),\n        ).fetchall()\n        return [self._fact(r, r["score"]) for r in rows]',
     '            (like, k),\n        ).fetchall()\n        if not rows:\n'
     '            rows = self.conn.execute(\n'
     '                "SELECT *, 0.0 AS score FROM facts ORDER BY hits DESC,'
     ' updated_ts DESC LIMIT ?",\n'
     '                (k,),\n'
     '            ).fetchall()\n'
     '        return [self._fact(r, r["score"]) for r in rows]'),
    # AZ22b：只堵"零命中"这一个入口、留着"空 query"那个 ⇒ `LIKE '%%'` 照样给全表。
    #        这正是"只堵一个入口 = 假装修好"的撤回态。
    ("AZ24", "mw/memory.py",
     '        if not normalize(query).strip():\n            return []',
     '        if False:\n            return []'),
    # AZ23：把 FTS 每次最多回 1 条 ⇒ "四原文拼接能召回 4 条"立刻不成立。
    #        ⛔ 只动上限，不动分词也不动 SQL 结构：要证明的是**条数读数**有牙。
    ("AZ23", "mw/memory.py",
     '                       ORDER BY score LIMIT ?"""',
     '                       ORDER BY score LIMIT MIN(?, 1)"""'),
    # AZ25：把降级兜底的 query 换回**字面量** `"goal"` ⇒ 与任何 fact 零交集
    #       ⇒ 那条通道永远不会命中（兜底已删，"永远空"而日志仍写 ok=True）。
    ("AZ25", "mw/planner.py",
     '            _q = (goal or "goal").strip()[:64] or "goal"',
     '            _q = "goal"'),
    # ══════════════════════════════════════════════════════════════════════
    # AZ26–AZ30（§17-7j）：**钥匙可控性**这一组，每条各配一条撤回态。
    # ══════════════════════════════════════════════════════════════════════
    # AZ26：让**所有**惯例都用同一把钥匙（ZK1）⇒ n0 档从 1 条变 4 条。
    #   ⛔ 为什么**不**选"改 KEYS_7J 的某个值"：钥匙是**派生**的，改了会同时
    #      改 facts 与题面，两边一起变 ⇒ 仍然一致 ⇒ 不红。那正是"同一事实只
    #      声明一处"的副作用，也是为什么这条撤回态必须打在**构造式**上。
    #   ⛔ 也**不**选"n4 少提一把"：那会把 AZ29 的 4 条变成 3 条 ⇒ 连带。
    ("AZ26", "bench/build_g7d_seed.py",
     'FACTS_7J: dict[str, str] = {\n    k: f"（{KEYS_7J[k]}）{t}" for k, t in FACTS.items()\n}',
     'FACTS_7J: dict[str, str] = {\n    k: f"（ZK1）{t}" for k, t in FACTS.items()\n}'),
    # AZ27：`fts_query` 只取前 **8** 个 token ⇒ 钥匙被挤出窗口。
    #   "题面里写了钥匙"与"FTS 收到钥匙"是两件事 —— 这条撤回态把两者拆开。
    #   ⚠️ 连带 AZ26/AZ29：query 里没有钥匙 ⇒ 命中变 0 条。同一条链上的因果。
    ("AZ27", "mw/memory.py",
     '''return " OR ".join(f'"{t}"' for t in toks[:24])''',
     '''return " OR ".join(f'"{t}"' for t in toks[:8])'''),
    # AZ28：去掉骨架安慰剂的"ASCII 原位保留" ⇒ P 臂的钥匙被打散 ⇒ 命中 0 条。
    #   ⚠️ 必须写成 `if False:` 而**不是**"删掉 `ord(ch) < 128 or`"：
    #      删掉之后对 ASCII 而言 `not (CJK 区间)` 仍为 True ⇒ 仍然原位保留
    #      ⇒ **逻辑等价、mutation 无效、红=无**（本轮第一版就是这么写错的，
    #      R13 当场抓到：`AZ28 红=无 目标=✘`）。这又是一次"改了个看起来
    #      一样的东西"。
    #   ⚠️ 连带 AZ2（"安慰剂保留全部 ASCII token"）—— 同一个缺陷的正面断言。
    ("AZ28", "bench/build_g7d_seed.py",
     '            if ord(ch) < 128 or not ("\\u4e00" <= ch <= "\\u9fff"):',
     '            if False:'),
    # AZ29：语义块只留前 3 条 ⇒ n4 档从 4 条变 3 条（块还在，只是少一条）。
    #   ⛔ 不选"把 cap 调到 120"：那会把语义块**整块**丢掉，连带一整片端到端断言，
    #      看起来像"锚定跑偏"，实际是"改了另一个变量"。
    ("AZ29", "mw/core.py",
     'block = " ｜ ".join(f.text[:120] for f in recalled)',
     'block = " ｜ ".join(f.text[:120] for f in recalled[:3])'),
    # AZ30：R1 预检恒真（`return 0 if ok else 2` → `return 0`）⇒ 7d 也"通过"了。
    #   这条撤回态证明的是：**"7j 通过"这个读数有分辨力**，不是一个恒真的绿。
    ("AZ30", "bench/build_g7d_seed.py",
     "    memH.close(); memP.close(); memE.close()\n    return 0 if ok else 2",
     "    memH.close(); memP.close(); memE.close()\n    return 0"),
    # ── AZ31–AZ33（第四十三轮，§17-7p）：code_exec 沙箱补强 ────────────────
    # AZ31：TimeoutExpired 分支改 raise ⇒ CPU 炸弹把打分器整个炸掉（rc!=0）
    #   ⇒ AZ31 红。⚠️ 不能选"删掉 timeout 参数"：那会让撤回态**挂死** ——
    #   R13 第三条硬坑（崩了 ≠ 判红）的挂死变体。
    ("AZ31", "bench/scorers/code_exec.py",
     '''        except subprocess.TimeoutExpired:
            return False, f"超时 >{_TIMEOUT_S:g}s"''',
     '''        except subprocess.TimeoutExpired:
            raise  # 撤回态'''),
    # AZ32：FSIZE 上限 no-op ⇒ 2MB 落盘成功 ⇒ 该条转绿 ⇒ AZ32 红。
    #   ⚠️ 只撤 FSIZE 一道（CPU/AS 保持），保证单变量。
    ("AZ32", "bench/scorers/code_exec.py",
     "    resource.setrlimit(resource.RLIMIT_FSIZE, (_FSIZE_B, _FSIZE_B))",
     "    pass  # 撤回态"),
    # AZ33：cwd 隔离撤掉 ⇒ 候选的标记文件落进调用方 cwd ⇒ AZ33 红。
    ("AZ33", "bench/scorers/code_exec.py",
     "cwd=workdir, preexec_fn=_sandbox,",
     "preexec_fn=_sandbox,"),
    # AZ34：verify() 丢掉明细 ⇒ 覆盖集变空 ⇒ "每份考题都被覆盖"必红。
    #   ⚠️ 不能 mutate 成 `return True, lines`：不篡改考题时行数照旧 ⇒ 恒绿。
    ("AZ34", "bench/suite_manifest.py",
     "    return ok, lines",
     "    return ok, []  # 撤回态"),
    # AZ35：transport 字段改名 ⇒ schema 缺键 ⇒ AZ35 红。
    ("AZ35", "bench/evallog.py",
     '"transport": _parse_transport(d.get("kernel_transport")),',
     '"transport_x": _parse_transport(d.get("kernel_transport")),'),
    # ══ AZ36–AZ40（第四十五轮，§17-7q）：改内核轮 ══════════════════════════
    # AZ36：补救丢 state ⇒ 补救请求里没有记忆 ⇒ AZ36 红。
    #   （P1–P5 不受影响：补救本身照常返回答案，丢的只是 state。）
    ("AZ36", "mw/planner.py",
     "                    got = self.answer(goal, state=state)",
     "                    got = self.answer(goal)  # 撤回态"),
    # AZ37：收尾丢 state ⇒ 收尾请求里没有语义记忆原文 ⇒ AZ37 红。
    ("AZ37", "mw/core.py",
     "            _co_state = self._state(goal, history, _co_it)",
     "            _co_state = ''  # 撤回态"),
    # AZ38：catalog_sha 转发失效（恒 True）⇒ allow_note 不再改变指纹 ⇒ AZ38 红。
    #   ⚠️ AZ39 的手动注册表用默认 True ⇒ 指纹一致 ⇒ AZ39 不红（单变量）。
    ("AZ38", "mw/tools.py",
     "                        workspace=workspace, allow_note=allow_note)",
     "                        workspace=workspace, allow_note=True)  # 撤回态"),
    # AZ39：forwarding 反向失效（恒 False）⇒ 默认指纹与手工 True 注册表脱节
    #   ⇒ AZ39 红；连带 AZ38（on==off ⇒ 相等判据崩）。见 COUPLED。
    ("AZ39", "mw/tools.py",
     "                        workspace=workspace, allow_note=allow_note)",
     "                        workspace=workspace, allow_note=False)  # 撤回态"),
    # AZ40：env 覆盖行删掉 ⇒ --max-tokens 退回"caliber 一套、内核一套"
    #   的两真相 ⇒ AZ40 红。caliber 读生效值的那行不动（单变量）。
    ("AZ40", "run.py",
     'os.environ["V1_LLM_MAX_TOKENS"] = str(int(args.max_tokens))',
     "pass  # 撤回态"),
    # ── AZ41/AZ42（§19 R-b，第四十五轮）：安装形态 ─────────────────────
    # AZ41：dependencies 塞进第三方名 ⇒ "零第三方运行依赖"边界破 ⇒ AZ41 红。
    #   其余字段不动（单变量）。
    ("AZ41", "pyproject.toml",
     "dependencies = []",
     'dependencies = ["requests"]  # 撤回态'),
    # AZ42：__main__ 的转发调用注释掉 ⇒ `python -m mw` 变成空操作 ⇒
    #   子进程 rc 虽为 0 但无"通过"标记 ⇒ AZ42 判据（rc==0 且含通过）红；
    #   连带 K5（死导入检查）：sys/main 不再被使用 ⇒ 结构性死导入。
    #   同一行删除的两个观测面，合理连带。见 COUPLED。
    ("AZ42", "mw/__main__.py",
     "    sys.exit(main())",
     "    pass  # 撤回态"),
    # ── AZ43（§19 R-c，第四十五轮）：演示资产 ─────────────────────────
    # AZ43：删掉 oracle 臂 ⇒ 三臂缺一 + 写死读数锚不全 ⇒ 结构锚红。
    #   只动 oracle 臂一行（单变量）；null/partial 行不动。
    ("AZ43", "demo/run_demo.sh",
     'arm 1.0000 "oracle" --solver oracle',
     'pass  # 撤回态'),
    # ── AZ44（§19 R-d，第四十五轮）：产品摘要块 ───────────────────────
    # AZ44：撤回态首版删差异表"测量有效性"行**未转红** —— 引言句里还有
    #   一处"测量有效性"，判据 AND 仍全真（如实登记：判据粒度粗于单行）。
    #   改撤摘要块标题本身 ⇒ "产品摘要" 判据必假 ⇒ AZ44 红。
    ("AZ44", "README.md",
     "## 产品摘要（30 秒读完）",
     "## （撤回态）"),
    # ⛔ AZ45（§17-7u 消歧指令）已随内核改动一起撤回 —— 三臂实测未过门槛且引入
    #   输出双稳（证据 s52）⇒ 断言与实现同撤，不留"锚一个不存在的实现"的假绿。
    # ── AZ46（§17-7v-b/c/d，第五十轮）：补救通道加的那一句中文 ───────────
    # AZ46：撤掉 `answer()` 里那一句的**拼接** ⇒ ①体内拼接数归 0 ⇒ 判据假；
    #   ②补救退回"无指令"（s54/s56 的 B 臂，g7e2=0.2）⇒ 必须红。
    #   ⛔ 措辞已三次修正（7v-b "指令性" → 7v-c "含逐条" → 7v-d "未定位"）：
    #     契约锚的是**机制存在**（拼了 ≥20 字一句、且只有一处拼接），不是措辞。
    #   ⛔ 只撤拼接一行，**不动**常量定义 —— 常量还在却没人用 = 唯一事实源被架空，
    #     正是 AZ46 判的对象。
    ("AZ46", "mw/planner.py",
     '            prompt += _ANSWER_FIDELITY + "\\n"',
     '            pass  # 撤回态'),
    # ── AZ48（§19 D-a 回归锚，第五十九轮）：默认数据路径的优先序 ───────────
    # AZ48 一条断言里束了四件事（两种形态的返回值 + 判别性的那条 + 显式值不进兜底），
    # 所以它需要**两条**撤回态：一条证明行为分支有牙，一条证明静态条件有牙。
    # ① 把优先序倒过来（包目录优先）⇒ "两处都有"那格读到 pkg ⇒ AZ48 红。
    #    ⛔ 这条是判别性所在：只测"只有一处有"的两种形态，两个实现都一样绿。
    ("AZ48", "run.py",
     "    cand = Path.cwd() / rel\n"
     "    return cand if cand.exists() else (base / rel)",
     "    cand = Path.cwd() / rel\n"
     "    return (base / rel) if (base / rel).exists() else cand"),
    # ② 让显式 `--suite` 也走兜底 ⇒ 静态判据（显式站点不许出现 _data_path）假 ⇒ 红。
    #    这锚的是 D-a 的**边界**：悄悄换掉用户给的路径是比报错更坏的假成功。
    ("AZ48", "run.py",
     '    suite_path = (Path(args.suite).expanduser() if args.suite\n'
     '                  else _data_path(Path("bench") / "tasks" / "coding_small.jsonl"))',
     '    suite_path = (_data_path(Path(args.suite).expanduser()) if args.suite\n'
     '                  else _data_path(Path("bench") / "tasks" / "coding_small.jsonl"))'),
    # ── AZ49（落地页零外部请求，第六十一轮）：页面上的承诺必须能被撤掉 ─────────
    # 一条断言里束了三件事（页面存在 + 边界句在 + 没有任何会发请求的标签），
    # 所以两条撤回态：一条证明"外链检测"有牙，一条证明"边界句"是被锚的。
    # ① 往页面里塞一个外部图片 ⇒ 只有 AZ49 红。
    ("AZ49", "site/index.html",
     "\n  <footer>",
     "\n  <img src=\"https://cdn.example/x.png\" alt=\"\">\n  <footer>"),
    # ② 把「不是 AGI」改成一句吹的 ⇒ 边界句判据假 ⇒ 只有 AZ49 红。
    #    ⚠️ 这条锚的不是"页面好看"，是这个仓库最不许丢的一句话。
    ("AZ49", "site/index.html",
     "<strong>先读这一条：V1 不是 AGI。</strong>",
     "<strong>先读这一条：V1 很强。</strong>"),
    # ── AZ50–AZ52（能力曲线装置）：每条判据各配一条变异 ─────────────────
    # 为什么这三条必须存在：曲线装置的全部价值就是"不让人用噪声骗自己"，
    # 而它自己最容易被改松的地方恰好是这三处 —— 断线、噪声、换尺子。
    # ① 断线条件放宽成"只要已有段就接上去" ⇒ 异口径点连成一条线 ⇒ 只有 AZ50 红。
    ("AZ50", "mw/loop.py",
     "            if out and p.trustworthy and out[-1][-1].calib == p.calib and out[-1][-1].trustworthy:",
     "            if out:"),
    # ② "每一轮都要超过基线+噪声"放宽成"最低一轮不低于基线" ⇒ 噪声与浮点容差
    #    双双被忽略 ⇒ 只有 AZ51 红。
    ("AZ51", "mw/loop.py",
     "        if worst > base + noise_floor + _tol:",
     "        if worst >= base:"),
    # ③ 撤掉每轮开头的尺子校验 ⇒ 运行中换尺子不再被拦 ⇒ 只有 AZ52 红。
    ("AZ52", "mw/loop.py",
     "        if scorer.sha() != s0:",
     "        if False:"),
    # ── AZ53–AZ54（循环模式的接线）：接线层同样要能被撤回 ─────────────────
    # ④ 撤掉"真模型必须 --fresh"这一格 ⇒ 记忆跨轮累积也能出曲线 ⇒ 只有 AZ53 红。
    ("AZ53", "run.py",
     "    if not getattr(args, \"fresh\", False):",
     "    if False:"),
    # ⑤ 让循环模式在本文件里**再构造一个** Caliber（改成按轮重建）⇒
    #    `run.py` 里的 Caliber 调用节点变 2 个 ⇒ 只有 AZ54 红。
    #    ⚠️ 这条变异是"看起来更严谨"的方向（每轮现算口径），正是它危险的地方：
    #    多一处构造 = 多一套可能漂移的口径，而两边都自洽。
    ("AZ54", "run.py",
     "                   caliber_factory=lambda: caliber, rounds=rounds,",
     "                   caliber_factory=lambda: Caliber(**caliber.as_dict()), rounds=rounds,"),
    # ⑥ 把早退计数写死成 0 ⇒ "不可信"又变成没有主语的形容词 ⇒ 只有 AZ55 红。
    ("AZ55", "mw/loop.py",
     "                       skipped=int(rep.skipped_no_answer),",
     "                       skipped=0,"),
    # ── AZ56–AZ57（第六十五轮）：从"量参照臂"走到"量真模型"才暴露的两处 ────
    # ⑦ 中文分词退回"整句一个 token"（= 第一版实现）⇒ 中文任务上 goal_hijack
    #    结构性 100% 误杀 ⇒ 只有 AZ56 红。
    #    ⚠️ 这条变异**就是本轮修掉的那个 bug 本身**，不是人造的边界。
    ("AZ56", "mw/guard.py",
     "        for i in range(len(run) - 1):\n            out.add(run[i:i + 2])",
     "        out.add(run)"),
    # ⑧ 切段时不再看被测对象版本 ⇒ 改一次内核就把新旧版本接成一条线 ⇒ 只有 AZ57 红。
    ("AZ57", "mw/loop.py",
     "                    and out[-1][-1].harness == p.harness):",
     "                    and True):"),
    # ── AZ58–AZ59（第六十六轮）：失败归因 ────────────────────────────────
    # ⑨ 工具没给理由时不再写 `(无错误信息)` ⇒ 日志里那一行变成
    #    `ok=False 原因=`（看起来像"没有原因"而不是"工具不给理由"）⇒ 只有 AZ58 红。
    ("AZ58", "mw/core.py",
     "原因={(why[:cap] or '(无错误信息)')}{tail}",
     "原因={why[:cap]}{tail}"),
    # ⑩ 外部读数段退回"只看配置项"（= 第六十四/六十五轮那 21 次失败隐形的根因）
    #    ⇒ 未配置但被规划器调用时整段消失 ⇒ 只有 AZ59 红。
    ("AZ59", "mw/core.py",
     "    if not cfg and not seen:\n        return \"\"",
     "    if not cfg:\n        return \"\""),
    # ── AZ60–AZ61（第六十七轮）：答案型外部能力只允许一扇门 ────────────────
    # ⑪ 执行点**保留规划器改写的内容**（只把 deadline 归一化）⇒ 门 A 交给外部
    #    能力的还是那句改写 ⇒ 与门 B 不同源 ⇒ 只有 AZ60 红
    #    （`answer_task(` 的计数没变，AZ61 不受影响 —— 已实测单红）。
    ("AZ60", "mw/core.py",
     "                        cand.args = answer_task(goal, _dl2)",
     "                        cand.args = answer_task("
     "cand.args.get('task') or goal, _dl2)"),
    # ⑫ 把归一化**内联成一个新的字典字面量** ⇒ `"task"` 的构造从 1 处变 2 处
    #    ⇒ 只有 AZ61 红（task 仍是原文、deadline 仍在，AZ60 的判据全部成立）。
    #    ⚠️ 这条撤的正是"一扇门"本身：两个构造点 = 两个未来。
    ("AZ61", "mw/core.py",
     "                        cand.args = answer_task(goal, _dl2)",
     "                        cand.args = {\"task\": str(goal), "
     "\"deadline_s\": round(float(_dl2), 1)}"),
    # ── AZ62–AZ65（第六十八轮）：让「别重复」真的起作用 ──────────────────
    # ⑬ 不再把"刚被丢掉的动作"喂给下一次提问（只留静态 `blocked` 清单）
    #    ⇒ 模型在同一个问题上重复同一个答案（实测：连续两轮）⇒ 只有 AZ62 红。
    #    ⚠️ 锚点打在**载荷**上（`reseek_lines = [...]`），不打在 `rejected=` 上 ——
    #       打在 `rejected=` 上会让 AZ63 的"喂入点 == 2"一起红（实测多红）。
    #       而同一条载荷同时喂两个调用点，所以清空它就等于把整个装置关掉。
    ("AZ62", "mw/core.py",
     "                        reseek_lines = [c.as_line() for c in cands]",
     "                        reseek_lines = []"),
    # ⑭ 把「换形态」的渲染**内联成第二处字面量**（语义等价，两个字面量）
    #    ⇒ `reseek_block(` 只剩定义那一处 ⇒ 只有 AZ63 红（AZ62 照样绿：
    #      标记语与"还没试过的工具"都还在，装置行为没变 —— 变的是"有几个未来"）。
    ("AZ63", "mw/planner.py",
     "        reseek = reseek_block(rejected, self._untried_tools(blocked))",
     "        reseek = (\"\\n⛔ 你上一次的回答被**拒绝**了：这些动作已经执行过，\"\n"
     "                  f\"再提一次只会被内核丢弃：\\n  · {str(list(rejected)[-1])[:160]}\\n\"\n"
     "                  \"**到目前为止一次都没试过的工具**：\"\n"
     "                  + \" / \".join(self._untried_tools(blocked))\n"
     "                  + \"\\n请给出与上面那些动作**不同**的候选；\"\n"
     "                    \"若确实无法再推进，就直接调用 finish。\") if rejected else \"\""),
    # ⑮ 签名退回"所有参数都算身份"（`deadline_s` 一起进签名）
    #    ⇒ 同一件事每轮都是"新动作" ⇒ 外部能力被重复执行两次 ⇒ 只有 AZ64 红。
    #    ⚠️ 这条变异**就是第六十八轮真跑里量到的那个 bug 本身**。
    ("AZ64", "mw/planner.py",
     "    pairs = [(k, v) for k, v in pairs if k not in _NON_IDENTITY_ARGS]",
     "    pairs = list(pairs)"),
    # ⑯ "全部候选因额度不足未执行"退回"记成连续失败"⇒ 三轮撞级联熔断
    #    ⇒ `cascade`（语义是"连续做错"，而一次都没做）⇒ 只有 AZ65 红。
    ("AZ65", "mw/core.py",
     "                    if skipped_now and skipped_now == len(cands):",
     "                    if False:"),
)

#: 每条撤回**必然**连带转红的那些断言（同一行实现被两个断言锚定）。
#: ⛔ 写成白名单而不是"允许任意多条"：允许多条就等于不再检查锚定跑偏。
COUPLED: dict[str, frozenset[str]] = {
    #: AZ22a（把全表兜底加回）⇒ 前两题的"零命中"又变成 4 条 ⇒ AZ23 的
    #: `[0,0,1,1]` 立刻不成立。**同一段 SQL 的两个面**，合理连带。
    #: ★第三十八轮补登 AZ26/AZ30：兜底回来了 ⇒ n0 档命中 4 条（AZ26 红）；
    #:   7d 的 R1 预检反而"通过"了 ⇒ AZ30 的分辨力断言（7d 必须 rc=2）红。
    "AZ22": frozenset({"AZ23", "AZ26", "AZ30"}),
    #: ★第四十五轮（§17-7q）：AZ39 撤回态把 forwarding 打成恒 False ⇒
    #:   ① AZ38 的 on/off 相等判据同时崩（同一段 forwarding 的两个面）；
    #:   ② **AT1 连带**：AT1 锚的就是"catalog_sha() 默认形状与 Core 逐字一致"
    #:     （AF1 同源），恒 False 让默认指纹丢了 note ⇒ 真实脱节，不是锚定跑偏
    #:     —— 实现被三处锚定（AZ38/AZ39/AT1），合理连带。
    #: AZ38 撤回态（恒 True）已核对**不**连带 AZ39/AT1（默认形状仍一致，
    #: 首跑实测 红=['AZ38'] 单红）—— 单变量。
    "AZ39": frozenset({"AZ38", "AT1"}),
    #: ★第四十五轮（§17-7q）全量复跑补登记（AW1/AW2/AT1 三条，合并进下方
    #: 原条目 + 新增 AT1）：
    #:  · AW1/AW2 撤的是"note 按 allow_note 注入"本身 ⇒ note 无论开关都不注册
    #:    ⇒ catalog_sha 的 on/off 指纹必然相等 ⇒ AZ38 连带（并入原条目）。
    #:  · AT1 撤的是"catalog_sha 注册表带 memory"⇒ recall/note 双双消失
    #:    ⇒ AZ38（on/off 相等）+ AZ39（默认指纹与手工注册表脱节）连带。
    #: 反向均已核对：AZ38 撤回态实测 红=['AZ38'] 单红；AZ39 已登记
    #: {AZ38, AT1}。单向成立。
    #: ★第三十八轮补登：AZ23（FTS LIMIT 1）⇒ n4 档只回 1 条 ⇒ AZ26（命中数）
    #: 与 AZ29（语义块含恰好 4 条）同时红。同一条 SQL 的三个观测点。
    "AZ23": frozenset({"AZ26", "AZ29"}),
    #: ★第三十八轮补登（7j 落地后，**老撤回态的连带面扩大**；以下每条都
    #:   单独复现过，看的是 ❌ 详情不是只看红集）：
    #: AZ1/AZ2/AZ3 都 mutate `build_placebo` 的同一段 ⇒ 骨架安慰剂的"ASCII
    #: 原位保留"性质被破 ⇒ AZ28（P 臂等条数）红；7j 预检的"两臂等价"检查
    #: 也跟着失败 ⇒ AZ30（预检分辨力）红。
    #: ⚠️ AZ28 ⇄ AZ2 本来就是"同一行 ASCII 保留"的两个面 —— 连带是**对称**的，
    #:    第三十七轮只登记了 AZ28 → AZ2 一个方向，漏了反向。教训：登记连带时
    #:    要双向检查（撤 A 连带 B 的，撤 B 通常也连带 A）。
    "AZ1": frozenset({"AZ28", "AZ30"}),
    "AZ2": frozenset({"AZ28", "AZ30"}),
    "AZ3": frozenset({"AZ28", "AZ30"}),
    #: AZ5（惯例写到 138 字）⇒ 注入被 core.py 截断 ⇒ 7j 预检 rc=2 ⇒ AZ30 红。
    "AZ5": frozenset({"AZ30"}),
    #: ⚠️ flaky 观察（第三十八轮，实测 1 + 3）：第三轮 R13 里 AT1 撤回多红
    #: AU7/AU8/AU9/AU10；单独复现 4 次（1 + 3 连跑）**全部只红 AT1 自己**。
    #: ⇒ 那次多红是负载下的假红（时序敏感的 AU 组在机器忙时翻红），与 7g
    #:   的"运输层路径"教训同宗。⛔ 不登记进 COUPLED —— 登记一条 flaky
    #:   连带等于给假红发永久通行证。判据：R13 的多红若疑似 flaky，单独
    #:   复现 ≥3 次全净才算实锤，复现记录写进当轮 DESIGN。
    #: AZ26（所有惯例都用同一把钥匙 ZK1）⇒ n0 档不再"每题只命中自己那条" ⇒
    #: 7j 的 R1 预检在 n0 上失败（rc=2）⇒ AZ30 的 `7j rc=0` 不成立。
    #: **同一个装置缺陷的两个观测点**：一个看命中条数，一个看预检判决。
    "AZ26": frozenset({"AZ30"}),
    #: AZ27（`fts_query` 只取前 8 个 token）⇒ 钥匙被挤出窗口 ⇒ 所有**依赖钥匙**
    #: 的读数同时失效：AZ26（命中条数）、AZ29（语义块条数）、AZ30（预检）、
    #: AZ23（7i 的两批拼接，第二批 19 个 token 掉到 8 个 ⇒ 召回变少）。
    #: ⚠️ 这条连带**宽**，但它就是同一条因果链，而且它顺带证明了一件事：
    #:    24-token 窗口**不是装饰上限** —— 它真的在切断东西。
    "AZ27": frozenset({"AZ23", "AZ26", "AZ29", "AZ30"}),
    #: AZ28（骨架安慰剂不再保留 ASCII）⇒ ① AZ2 直接不成立（同一件事的正面断言）；
    #: ② 7j 预检的"两臂等条数"必然失败（P 臂钥匙被打散 ⇒ 0 条 vs H 臂 1 条）
    #: ⇒ AZ30 的 `7j rc=0` 不成立。三条锚的是**同一条**"ASCII 必须原位保留"。
    "AZ28": frozenset({"AZ2", "AZ30"}),
    # 闸永不拒（`if v.ok` → `if True`）时，连带转红的三条，理由各不相同：
    #   AS8  直接后果 —— 交付出去的就是第一版坏答案（分数停在 0）；
    #   AS9  从未打回 ⇒ `failed` 恒 0 ⇒ "超上限就放弃"这条分支**根本走不到**
    #        （它期望 failed==2）；
    #   AS10 同理 —— 没有打回就没有"重写失败"，`_salvage` 也就无从被触发。
    # ⇒ 这是**合理的连带**（上游断了，下游两条断言自然不成立），不是锚定跑偏。
    #   ★第三十三轮补登 AV1/AV2：它们读的就是 `_AS_C2.memory.episodes(kind="failure")`
    #     那几行 —— 闸永不拒 ⇒ 没有 failure 行 ⇒ 两条断言的前提整体消失。
    #     实测（第三十三轮）：AS7 撤回后 AV1/AV2 同时红，`failure行数=0`。
    "AS7": frozenset({"AS8", "AS9", "AS10", "AV1", "AV2"}),
    #: AS9 撤掉 `failed > cap` ⇒ 超限分支永不可达 ⇒ AV2（超限交付要落 failure 行）
    #: 的前提消失。实测：AS9 撤回后 AV2 红，其余只 AS9 自己红。
    "AS9": frozenset({"AV2"}),
    # AY1/AY2 是同一命题的正反用例，实现只有一处 ⇒ 必然一起翻（见上）。
    "AY1": frozenset({"AY2"}),
    # salvage 条件去掉 `not answer and` 之后，**正常交付**也会被上一版坏答案顶掉
    # ⇒ AS8（"第二版交对就交付它"）必然红。这正是 AS14 注释里预言的那个静默降级。
    "AS14": frozenset({"AS8"}),
    #: AW 组：env → Limits → Core 是一条**串联链**，上游断下游必红 —— 合理连带：
    #:   AW2（开关被无视）⇒ Core 注册表里 note 在册 ⇒ AW5（端到端传播）红；
    #:   AW3（整块门控）⇒ OFF 时 recall 消失 ⇒ AW5 红；
    #:   AW4（env 读不到）⇒ Limits 恒 True ⇒ Core 照常注册 note ⇒ AW5 红。
    #: AW1（默认路径也不注册 note）⇒ 默认工具目录变了 ⇒ I6/I7（目录断言）红。
    #: ★第四十五轮（§17-7q）补登 AZ38：note 无论开关都不注册 ⇒ on/off 指纹相等。
    "AW1": frozenset({"I6", "I7", "AZ38"}),
    #: AX1（沉淀被撤）⇒ worldmodel 的学习行为断言全红：I7（覆盖读数）、
    #: V1/V3/V4（失配沉淀与检索）、V6（下一次预演带"已知易错点"）——
    #: 这些就是"学习"本身，撤沉淀它们必红。合理连带。
    "AX1": frozenset({"I7", "V1", "V3", "V4", "V6"}),
    #: AX3（env 读不到）⇒ Limits 恒 True ⇒ Core 的世界模型照常可学 ⇒ AX4 红（串联链）。
    "AX3": frozenset({"AX4"}),
    #: AS18（`triggered += 0`）⇒ AU8 要求 `triggered ≥ 1` ⇒ 计数恒 0 必红。
    #: AS19（打印键改成 `收尾X=`）⇒ AU10 找 `收尾=` 那行，找不到 ⇒ 必红。
    #: AU3（拒绝发起失效）⇒ 收尾触发路径改变 ⇒ AU10 的 `by_reason` 分项与打印行对不上。
    #: 三条都是"同一个 `_au_co` 对象被上下游两条断言同时观测"，实测逐条复现。
    "AS19": frozenset({"AU10"}),
    #: ★第四十五轮（§17-7q）补登 AZ38：AW2 让开关被无视 ⇒ note 恒在册
    #:   ⇒ catalog_sha 的 off 形状与 on 相同 ⇒ AZ38（on≠off 判据）连带。
    "AW2": frozenset({"AW5", "AZ38"}),
    #: ★第四十五轮 R-b 补登：AZ42（转发调用变 pass）⇒ __main__.py 的
    #:   sys/main 两个 import 变死导入 ⇒ K5（结构检查，扫 mw/*.py）红。
    "AZ42": frozenset({"K5"}),
    #: ★第四十五轮（§17-7q）新增：AT1（catalog_sha 注册表 memory=None）
    #:   ⇒ recall/note 双双从默认指纹消失 ⇒ AZ38（on/off 相等）与
    #:   AZ39（默认指纹与手工 _StubMemory 注册表脱节）同时崩。
    #:   实测 红=['AT1','AZ38','AZ39']，目标 ✔，其余全净。
    "AT1": frozenset({"AZ38", "AZ39"}),
    #: AZ18：撤掉题边界清空 ⇒ AZ17 的**顺序锚**（`reset_working()` 在
    #: `record("goal")` 之前）也找不到调用点（`find` 返回 −1）。
    #: 这是同一行实现被两条断言锚定（AZ17 看"在不在/顺序与消费者"，
    #: AZ18 看"有没有用"），属于**合理连带**，不是锚定跑偏。
    "AZ18": frozenset({"AZ17"}),
    "AW3": frozenset({"AW5"}),
    "AW4": frozenset({"AW5"}),
    #: AE2 删掉 ENV_KEYS 登记 ⇒ A 组"登记 == 实际读取"必然红（代码读了没登记的 env
    #: 就是静默漂移，那条本来就该红）。这是**合理的连带**，不是锚定跑偏。
    "AE2": frozenset({"A1"}),
    #: AE4 把 used_as_answer 改成 += 0 ⇒ AE9 那条"答案被采用"必然红。
    "AE4": frozenset({"AE9"}),
    # 触发数不涨（`+= 0`）⇒ 汇总行也就没有"收尾=1次"可印 ⇒ AS19 必然红。
    # 上游的计数断了，下游"把计数念出来"这条断言自然不成立 —— 合理连带。
    # ★第三十三轮补登 AU8/AU10：AU8 直接要求 `closeout.triggered ≥ 1`（计数恒 0 必红）；
    #   AU10 读的是同一份 `by_reason` 打印行（分项与总数一起被打断）。实测逐条复现。
    "AS18": frozenset({"AS19", "AU8", "AU10"}),
    #: AS1 往 `mw/verify.py` 末尾**追加一行 import**（模拟"判据模块碰锚层"）。
    #: 那一行是**未被使用**的 ⇒ K5（"内核没有死导入"）必然红。
    #: 这是变异的直接副作用，不是锚定跑偏：要消掉多红只能让那行被用起来，
    #: 但那样变异就不再是"碰一下锚层"了（AS1 要抓的正是"碰"，不是"用"）。
    "AS1": frozenset({"K5"}),
    #: AT4 把豁免表改成"放行一切" ⇒ 探针的 `_caliber_check` 再也判不出意外差异
    #: （本该 `fail` 的变体变成 `pass`）⇒ AT7 的三态断言必然红。
    #: 这是**合理的连带**：AT7 测的是探针，而探针用的正是 `diff_except`。
    "AT4": frozenset({"AT7"}),
    #: AE9 把注入开关读成空串 ⇒ **所有**外部能力的题都不再注入，而 AT8 的夹具
    #: 正是"让一个必然失败的 agent 被调用一次"⇒ 一次都不会被调用 ⇒ AT8 必然红。
    #: 这是**合理的连带**：AT8 测的是"失败原因落盘"，而它需要失败真的发生。
    "AE9": frozenset({"AT8"}),
    #: AS4 把 `if _CODE_ONLY.search(goal)` 改成 `if True` ⇒ **散文题也派生
    #: `compiles` 判据** ⇒ 那些题被自检错误打回 ⇒ 交付路径整个变了。
    #: 于是两条端到端读数自然不成立：
    #:   I1  —— `done` 不再达成（答案反复被打回，状态落到别的分支）；
    #:   AB7 —— "关掉工作记忆写入 ⇒ 回流率归零"的前提是有一条正常交付路径，
    #:           路径被打断后这条读数无意义。
    #: ⇒ 合理连带（上游断了，下游读数自然不成立）。
    #: ★第四十五轮 R-b 全量复跑补登：AS4（散文题强制走代码提取）⇒ selftest
    #:   端到端行为改变 ⇒ AZ42（`python -m mw --selftest` 动态判据）连带。
    #:   AZ42 是"selftest 真跑通"的观测面 —— 任何破坏 selftest 的撤回都会
    #:   连带它，合理。反查：AZ41（静态）不受行为影响，实测未连。
    "AS4": frozenset({"I1", "AB7", "AZ42"}),
    #: AU2 不裁剪 ⇒ AU4 的夹具（"第一发被裁到 75s"）不复存在 ⇒ `wall_shortened`
    #: 恒为 0 ⇒ "裁短必须被计数"这条断言自然不成立。**合理连带**：
    #: AU4 测的是"裁短有没有被观测到"，而它需要裁短真的发生。
    "AU2": frozenset({"AU4"}),
    #: AU3 的"拒绝发起"失效 ⇒ 两个下游必然不成立：
    #:   AU5 —— 它要的是"墙钟不够再试 ⇒ 抛 BudgetExhausted"，拒绝失效后
    #:          内核会把负剩余时间当 timeout 发出去，错误变成 LLMError；
    #:   AU8 —— 它要的是"第一发被拒 ⇒ 转向收尾"，拒绝失效后内核照常调端点，
    #:          交付路径整个变了；
    #:   AU7 —— 它读的是"收尾后预留已释放"，而收尾根本没发生 ⇒ 没有读数可判。
    #: ⇒ 合理连带（上游断了，下游三条断言自然不成立）。
    #: ★第三十三轮补登 AU10：拒绝发起失效后收尾被**另一条路径**触发，
    #:   `by_reason` 的分项与"收尾="打印行对不上 ⇒ AU10 红。实测复现。
    "AU3": frozenset({"AU5", "AU7", "AU8", "AU10"}),
    #: AU5 把"首发放弃"与"重试放弃"对调 ⇒ AU4 断言"首发放弃恒为 0"必然红。
    "AU5": frozenset({"AU4"}),
    #: AU8 不转向收尾 ⇒ 那次运行根本走不到 `_close_out` ⇒ 两条下游必然不成立：
    #:   AU7  —— 它读的是"收尾后预留已释放"，没有收尾就没有读数；
    #:   AU10 —— 没有收尾就没有「收尾=」那一行可念。
    #: ⇒ 合理连带（上游断了，下游两条断言自然不成立）。
    "AU8": frozenset({"AU7", "AU10"}),
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

    _guard_end = _sha(V1 / "tests" / "contract_test.py")
    if _guard_end != _guard:
        print(f"\n  ⛔ 运行期间 `tests/contract_test.py` 被改动 ⇒ 本轮读数作废（边跑边改）")
        # ★第三十四轮：把两个值印出来。只报警不给值 = 只能靠回忆时间线，
        #   而"回忆时间线"正是这条闸当初要取代的东西。
        print(f"     基线后 sha={_guard}  结束时 sha={_guard_end}")
        ok_all = False

    print("\n" + "=" * 74)
    print(f"  {'R13 通过：逐条撤回都精确转红' if ok_all else 'R13 失败：见上'}")
    print("=" * 74)
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
