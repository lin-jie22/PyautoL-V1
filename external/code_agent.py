#!/usr/bin/env python3
"""真实外部能力 —— 一个会**执行**的编码 agent（零第三方依赖）。

为什么它存在（第二十三轮，路线①）
----------------------------------
内核的定位是"不含实际能力，能力一律外部接入"（`mw/tools.py` 首屏）。
但在此之前，`agents.json` 里唯一的 `example` 只会把任务原文回显回来：

    {"agent": "example", "received": "...", "note": "这是范例 agent。"}

⇒ 外部能力通道**只有契约、没有能力**。本文件把它填上。

它比"再调一次模型"多出来的两件事
--------------------------------
1. **多角度生成**：同一道题用 3 个不同的**提问角度**各写一份实现。
   多样性来自**提示词角度**，不来自采样温度 —— 因为本项目的不变量是
   "T=0 逐位可复现"（`bench/probe_verify.py` R1 钉住）；用 T>0 换多样性
   就等于把 0.1111 的采样噪声重新引进来，读数立刻不可复核。
2. **执行筛选**：让模型从题面自己写一组 `assert`，然后把全部实现**真的跑**
   一遍，选通过最多的一份。模型写的测试可能是错的 —— 所以这**不是判据**，
   只是**排序**；最终的分数仍只由外部尺子给（`bench/scorers/code_exec.py`）。

⛔ 它**不是**尺子，也**读不到**尺子
   任务集里的 `tests` / `oracle` 一律不进来：本文件的输入只有 stdin 的任务
   **题面原文**。内核给什么它就用什么，多一个字都没有。

协议（与 `mw/tools.py` 的外部 agent 契约一致）
    stdin  : 任务题面原文（纯文本）
    stdout : 一行 JSON  {"answer": "<可直接交付的答案>", "meta": {...}}
             失败时 {"answer": "", "error": "<原因>"}（rc 恒为 0，
             由内核侧把它变成一次**可记账的失败**，而不是一个空答案）
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

BASE = os.environ.get("V1_LLM_BASE_URL", "http://localhost:11434/v1")
KEY = os.environ.get("V1_LLM_API_KEY", "")
MODEL = os.environ.get("V1_LLM_MODEL", "qwen2.5:7b")

#: 生成几份实现。
#:
#: ⚠️ 第二十三轮实测后从 **3 降到 2**（这不是为分数调参，理由必须看全）：
#:    · 单次生成的实测成本 ≈ **25 字符/秒**（329 字 13.2s / 613 字 24.0s，
#:      三个独立进程 13.1/13.2/13.3s，稳定可复现）；
#:    · 这类题的输出 400~800 字 ⇒ 单次 16~32s；k=3 光是生成就要 48~96s，
#:      再加测试合成（≈8s）与执行（≈2s）⇒ 常态需要 60~105s；
#:    · 而内核能给的 deadline 实测是 **60s**（`min(60, 120−45)`）。
#:    ⇒ k=3 在多数题上会让 `gen_stopped_early=True` + `tests=0`，
#:      **执行筛选恒不工作**，这个 agent 就退化成了"取第一份生成"。
#:    改成 k=2 之后，2×32+8+2 ≈ 74s，仍会触到上沿但**筛选有机会真跑**。
#:    ⛔ 调整时点声明：改这一行时，ON 臂的可用读数是 **0/5 与 0/4**
#:      （全部 `rc=1` 崩溃）⇒ 不存在"看到好分数所以往有利方向调"。
K = int(os.environ.get("AGENT_K", "2"))
#: 温度**固定 0**：见模块 docstring 的复现性理由。留这个变量只是为了
#: 让"温度确实为 0"这件事可被外部核对，而不是让它被随手调高。
TEMP = float(os.environ.get("AGENT_TEMP", "0"))
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "1024"))
TEST_TIMEOUT_S = float(os.environ.get("AGENT_TEST_TIMEOUT_S", "6"))
#: 本 agent 的**总墙钟上限**（第二十三轮实测逼出来的）。
#:
#: ⛔ 为什么必须有：内核的墙钟预算是 120s、收尾余量 45s，而外部调用的耗时
#:    **完全没有进预算层**。实测（`k04_to_ini`）：三次生成 + 五次执行跑了 120s+，
#:    内核走到 `budget_exhausted`，那道题**交付为空**（`skipped_no_answer`）
#:    ⇒ 一个本来 1.0 分的题被外部能力变成了"没答案"。
#:    ⇒ 外部能力必须**自己看着表**，并且**到点就交最好的**，而不是交一个异常。
#: 60 这个数不是拍的：120（内核墙钟）− 45（收尾余量）= 75，留 15s 给自检与交付。
DEADLINE_S = float(os.environ.get("AGENT_DEADLINE_S", "60"))
#: 单次模型调用的**最后兜底**（urlopen 的超时）。实际取值 = min(它, 剩余时间)：
#: ⛔ 实测（第二次）：只把"要不要再开一个角度"卡在 60s 是不够的 ——
#:    **单次调用本身可以跑 60s+**，于是 k04 的 agent 调用仍然跑了 128s，
#:    把内核 120s 预算吃光，那道题交付为空。时间闸必须下沉到**每一次 IO**。
IO_TIMEOUT_CAP_S = float(os.environ.get("AGENT_IO_TIMEOUT_S", "120"))
#: 用掉这个比例之后**不再开新的角度**（继续加只会把交付挤掉）。
GEN_STOP_FRAC = 0.6

#: 三个**提问角度**（不是三种温度）。每份实现都是确定性的。
ANGLES: tuple[tuple[str, str], ...] = (
    ("直接",
     "先给最直接的实现。不要解释。"),
    ("边界",
     "先在心里枚举至少 4 个边界情况（空输入、嵌套、异常类型、重复键、"
     "缺省值），再给出能同时处理它们的实现。不要解释。"),
    ("朴素",
     "用最朴素、最容易被人逐行核对的写法（宁可慢，不要技巧）。不要解释。"),
)

_FUNC_NAME = re.compile(r"函数名(?:必须|应)(?:是|为)\s*`?\s*([A-Za-z_][A-Za-z0-9_]*)")
_FENCE = re.compile(r"```(?:python|py)?[ \t]*\r?\n(.*?)```", re.S)
_CODE_ONLY = re.compile(r"只输出代码|只要代码|只给代码|仅输出代码")


# ---------------------------------------------------------------------------
# 模型调用（只用 stdlib，理由同 mw/llm.py：第三方依赖是"坏了我不知道为什么"的黑区）
# ---------------------------------------------------------------------------
_T0 = time.time()


def _elapsed_from_start() -> float:
    """从进程启动算起用了多少秒（时间闸的唯一时钟）。"""
    return time.time() - _T0


def _chat(system: str, user: str, *, max_tokens: int = MAX_TOKENS) -> str:
    if not KEY:
        raise RuntimeError("未配置 V1_LLM_API_KEY ⇒ 外部 agent 没有模型可用")
    body = json.dumps({
        "model": MODEL, "temperature": TEMP, "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        BASE.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {KEY}"},
    )
    # ⛔ `timeout` 必须是**剩余时间**，不是固定 180：固定值等于没有时间闸。
    _remain = DEADLINE_S - _elapsed_from_start()
    if _remain < 3.0:
        # ⛔ 剩余不足 3s 就**不要再发请求**：`max(5.0, ...)` 的下限会让每次
        #    调用至少吃 5s，于是"最后一次调用"必然把总时长推过 deadline。
        #    实测：60s 预算下 agent 跑了 60.1s，正好卡在闸上。
        #    宁可降级交付，也不要"准时地失败"。
        raise RuntimeError(f"时间预算已用尽（剩余 {_remain:.1f}s < 3s）")
    _left = max(3.0, min(IO_TIMEOUT_CAP_S, _remain))
    try:
        with urllib.request.urlopen(req, timeout=_left) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # ⛔ 第二十三轮实测踩到的坑：`URLError` **只包住连接阶段**。
        #    读响应体时超时抛的是**裸 `TimeoutError`**（`OSError` 子类），
        #    它**不是** `URLError` 的子类 —— 只 catch URLError 会让它一路穿透。
        #    后果不是"这次调用失败"，而是**整个 agent 以 rc=1 死掉**：
        #    已经生成好的 3 份实现全部作废，内核只看到 `(rc=1)`。
        #    实测证据：本轮 ho_on 4 次调用 4 次 rc=1，ON 臂 0 个可用答案。
        raise RuntimeError(
            f"模型调用失败 {BASE}: {type(e).__name__}: {e}") from e
    try:
        return str(data["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"模型响应结构异常: {data!r}"[:300]) from e


def _extract_code(text: str) -> str:
    """与打分器的抽取规则**同构**（相同正则、相同回退）。"""
    blocks = _FENCE.findall(text or "")
    if blocks:
        return "\n\n".join(b.strip() for b in blocks if b.strip())
    return (text or "").strip()


# ---------------------------------------------------------------------------
# 生成 / 测试合成 / 执行
# ---------------------------------------------------------------------------
def gen_impl(goal: str, angle: str, hint: str, fn: str) -> str:
    sys_p = ("你是一名严格的 Python 工程师。只输出一个 Markdown 代码块，"
             "不要任何解释、不要示例调用。")
    user = (f"{goal}\n\n【本次要求】{hint}\n"
            f"函数名必须是 {fn}。只输出代码。")
    return _extract_code(_chat(sys_p, user))


def synthesize_tests(goal: str, fn: str) -> list[str]:
    """让模型**只从题面**写 assert 用例。返回可用的语句列表（解析失败的丢掉）。

    ⚠️ 这不是判据，只是排序信号：测试本身可能是错的。
    """
    sys_p = "你只输出 JSON，不要解释。"
    user = (f"{goal}\n\n请据此写 5 条 Python `assert` 语句，用来检验 `{fn}` 的实现。\n"
            "要求：每条是一个可独立执行的 assert 表达式（直接调用 "
            f"`{fn}`），只用手写字面量，不要 import、不要 def、不要 print。\n"
            '输出格式（严格）：{"tests": ["assert ...", "assert ..."]}')
    raw = _chat(sys_p, user, max_tokens=600)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out: list[str] = []
    for t in (data.get("tests") or []):
        s = str(t).strip()
        if not s.startswith("assert ") or "\n" in s:
            continue
        out.append(s)
    return out[:8]


def run_tests(code: str, tests: list[str], *,
              per_test_s: float = TEST_TIMEOUT_S) -> tuple[int, list[str]]:
    """跑一遍：返回 (通过条数, 失败说明)。每条一个隔离子进程（同打分器纪律）。

    ⛔ `per_test_s` 必须由**调用方统一算好再传进来**，不许每份实现各算各的：
       如果 A 份实现跑 5 条、B 份实现因为时间不够只跑 2 条，那么
       `pass_counts` **不可比** —— 排序会按"谁跑得早"而不是"谁更好"。
       这不叫时间闸，叫给先跑的加分。
    """
    passed = 0
    fails: list[str] = []
    for t in tests:
        src = f"{code}\n\n{t}\n"
        try:
            p = subprocess.run([sys.executable, "-I", "-c", src],
                               capture_output=True, text=True,
                               timeout=per_test_s)
        except subprocess.TimeoutExpired:
            fails.append(f"{t[:40]} ⇒ 超时")
            continue
        if p.returncode == 0:
            passed += 1
        else:
            tail = (p.stderr or "").strip().splitlines()
            fails.append(f"{t[:40]} ⇒ {(tail[-1] if tail else 'rc!=0')[:120]}")
    return passed, fails


def _compiles(code: str) -> bool:
    try:
        compile(code, "<agent>", "exec")
        return True
    except SyntaxError:
        return False


def main() -> int:
    goal = sys.stdin.read().strip()
    global _T0
    _T0 = time.time()
    t0 = _T0
    meta: dict = {"model": MODEL, "k": K, "temp": TEMP, "angles": [a for a, _ in ANGLES],
                  "fn": "", "tests": 0, "pass_counts": [], "chosen": -1,
                  "fallback": False, "elapsed_s": 0.0}

    def emit(answer: str, error: str = "") -> int:
        meta["elapsed_s"] = round(time.time() - t0, 2)
        out = {"answer": answer, "meta": meta}
        if error:
            out["error"] = error
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
        sys.stdout.flush()
        return 0

    if not goal:
        return emit("", "stdin 为空：外部 agent 需要任务题面原文")

    m = _FUNC_NAME.search(goal)
    if not m:
        return emit("", "题面里没有『函数名必须是 X』⇒ 本 agent 不适用（不猜）")
    fn = m.group(1)
    meta["fn"] = fn

    # ① 测试合成**前置**（第二十三轮实测后调顺序）
    #    ⛔ 为什么先做：它只依赖**题面**（不依赖任何实现），而且只要 ~8s。
    #       放在生成之后时，实测结果是"生成吃掉 50s ⇒ 测试超时 ⇒ tests=0"
    #       ⇒ **执行筛选一次都没跑过**，那这个 agent 就只剩"多份生成"。
    #       先做它，等于把最便宜、最能区分的那一步优先保住。
    tests: list[str] = []
    try:
        tests = synthesize_tests(goal, fn)
    except RuntimeError as e:
        meta["tests_error"] = str(e)[:160]

    # ② 多角度生成（确定性：只是提示词不同）
    #    ⚠️ 每个角度之前**看一次表**：已经用掉 60% 就不再开新的角度。
    #       少一份样本是**降级**，交付为空是**失败** —— 前者可接受，后者不可接受。
    #    ⚠️ 生成失败也**不再 return**：只要手上已经有一份，就继续往下走。
    #       原来这里是 `except RuntimeError: return emit("", ...)`，
    #       于是"第 3 次生成超时"会把前 2 份好代码一起扔掉。
    impls: list[str] = []
    for _name, hint in ANGLES[:max(1, K)]:
        if impls and (time.time() - t0) > DEADLINE_S * GEN_STOP_FRAC:
            meta["gen_stopped_early"] = True
            break
        try:
            code = gen_impl(goal, _name, hint, fn)
        except RuntimeError as e:
            meta.setdefault("gen_errors", []).append(f"{_name}: {str(e)[:120]}")
            break
        if code:
            impls.append(code)
    if not impls:
        # ⛔ 只说"没有产出"是**不可归因**的：时间到 / 模型返回空 / 解析失败
        #    三种病的修法完全不同（调预算 / 换模型 / 改正则）。
        #    实测代价：ho_on 7 次调用 5 次失败，日志里只有这一句，
        #    我必须手工复现才能猜原因。
        _why = "; ".join(meta.get("gen_errors") or []) or "(无异常：模型返回了空内容)"
        return emit("", f"生成阶段没有产出任何代码 ⇒ {_why}")

    # ③0 执行预算：**先算总账再决定要不要筛**
    #    预算 = 剩余时间 − 1s（留给 emit）。
    #    每份实现 × 每条用例 都要能拿到**同一个** per_test_s，否则 pass_counts 不可比。
    exec_ok = bool(tests)
    per_test_s = TEST_TIMEOUT_S
    if exec_ok:
        _budget = DEADLINE_S - (time.time() - t0) - 1.0
        _n = len(impls) * max(1, len(tests))
        if _budget < _n * 1.0:                 # 连每条 1s 都保证不了
            exec_ok = False
            meta["exec_skipped"] = "budget"
        else:
            per_test_s = max(1.0, min(TEST_TIMEOUT_S, _budget / _n))
    else:
        meta["exec_skipped"] = "no_tests"
    meta["tests"] = len(tests) if exec_ok else 0
    meta["per_test_s"] = round(per_test_s, 2)

    # ③ 执行筛选（对所有实现：同样的 tests、同样的 per_test_s）
    scored: list[tuple[int, int, bool, str]] = []
    for i, code in enumerate(impls):
        ok_c = _compiles(code)
        n = 0
        if exec_ok and ok_c:
            n, _fails = run_tests(code, tests, per_test_s=per_test_s)
        scored.append((n, i, ok_c, code))
    meta["pass_counts"] = [s[0] for s in scored]

    # 排序：通过数 > 能编译 > 生成顺序（确定性，不引入任何随机）
    best = max(scored, key=lambda s: (s[0], s[2], -s[1]))
    meta["chosen"] = best[1]
    if best[0] == 0:
        meta["fallback"] = True
    if not best[2]:
        return emit("", "全部实现都编译不过 ⇒ 不交付坏代码（交给内核自己处理）")

    meta["chars"] = len(best[3])
    return emit("```python\n" + best[3].strip() + "\n```")


def _crash_report(exc: BaseException, t0: float) -> int:
    """**最后的兜底**：任何未预料的异常都必须变成一行 JSON，rc 恒为 0。

    ⛔ 为什么这条不能省：协议的甲方（`mw/tools.py`）只能从 stdout 读到
       `(rc=1)` 这个字符串 —— 那是一次**无信息的失败**：
       它既说不出"哪一步死的"，也分不出"模型超时"与"代码写错"。
       实测代价见 `_chat` 的注释（本轮 ON 臂 4/4 全这样死的）。
    """
    import traceback

    tb = traceback.format_exc().strip().splitlines()
    tail = " | ".join(tb[-3:])[:600]
    out = {
        "answer": "",
        "error": f"agent 未捕获异常 {type(exc).__name__}: {exc} ⇒ {tail}",
        "meta": {"elapsed_s": round(time.time() - t0, 2), "crash": True,
                 "traceback_tail": tb[-8:]},
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    _BOOT = time.time()
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as e:                       # noqa: BLE001
        sys.exit(_crash_report(e, _BOOT))
