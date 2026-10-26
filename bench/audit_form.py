#!/usr/bin/env python3
"""答案**形态**审计器：落盘的那份东西到底是「代码」还是「过程记录」。

## 为什么需要它

第九十四轮（§61）在 `tasks_v2` 的 30 份落盘答案里发现 **6 份**长这样：

    [step 1] finish({'answer': "```python\\ndef norm_sg01(rows):\\n …\\n```"}) — 直接实现…⇒ 失败

那是**内核把 step 渲染进 history 之后又原样交付**的结果（`_close_out` 的
`history[-1]` 兜底）。收尾闸当场喊了「缺少 def / 编译失败」，但按 §57 的设计只置标不拦。

`warn` 不是读数。这个工具把这件事变成可复跑的三个数：

1. **形态分布** —— `CODE` / `TRACE`（`[step N] tool(...)` 外壳）/ `EMPTY` / `PROSE`；
2. **闸通过率** —— 用内核**同一条** `verify.derive` + `verify.run` 判（不另写一套判据）；
3. **剥壳价值** —— 把 `finish({...})` 的 `answer` 字段取出来再打一次分。
   这一条是本轮的关键：它回答「壳本身吃掉多少格」，而**不是**「模型会不会」。

## ⛔ 它不是什么

- 它**不判**代码对不对（那是打分器的事），只判「交付物的形状」。
- 它**不修**任何一个字节：剥壳只用于**读数**，写入路径一个字都没改。

## 用法

    python3 bench/audit_form.py --suite bench/specgen/tasks_v2.jsonl \\
        --answers bench/evidence/repair_s94/answers_v2_7b.jsonl

    python3 bench/audit_form.py --self-check      # 四条反证，零网络

退出码：0 = 跑完；1 = 反证组红（拒绝出数）；2 = 用法错误。
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mw import bench as _bench          # noqa: E402
from mw import verify as _vfy           # noqa: E402

#: `[step 12] finish(` —— **外壳的开头**。用 `match` 不用 `search`：
#: 出现在正文中间的 `[step` 是模型在解释步骤，不是外壳。
_STEP_HEAD = re.compile(r"\s*\[step \d+\]\s*finish\(", re.S)

#: 只允许 `finish` 被剥：其它工具的渲染（`run_shell(...)`）**不是答案**，
#: 把它剥出来当答案是伪造 —— 那种情况必须留在 `TRACE` 里如实计数。
FORM_CODE, FORM_TRACE, FORM_EMPTY, FORM_PROSE = "CODE", "TRACE", "EMPTY", "PROSE"


def form_of(answer: str) -> str:
    """三分类。⛔ 只认形状，不认对错。"""
    s = (answer or "").strip()
    if not s:
        return FORM_EMPTY
    if _STEP_HEAD.match(s):
        return FORM_TRACE
    if "def " in s or "```" in s:
        return FORM_CODE
    return FORM_PROSE


def unwrap(answer: str) -> str | None:
    """把 `[step N] finish(<字面量>) — <理由>` 剥成它自己的 `answer` 字段。

    返回 `None` = **剥不动**（不是 `finish` 壳 / 字面量解析失败 / 没有 answer 键）。
    ⚠️ 括号用**扫描**而不是正则：`answer` 里全是 `)` 和 `}`，
       非贪婪正则会在第一个 `)` 就收手（第一版就是这么错的）。
    """
    m = _STEP_HEAD.match(answer or "")
    if not m:
        return None
    rest = answer[m.end():]
    depth = 1
    i = 0
    while i < len(rest) and depth:
        if rest[i] == "(":
            depth += 1
        elif rest[i] == ")":
            depth -= 1
        i += 1
    if depth:
        return None
    try:
        obj = ast.literal_eval(rest[:i - 1])
    except (ValueError, SyntaxError):
        return None
    if not isinstance(obj, dict):
        return None
    val = obj.get("answer")
    return val if isinstance(val, str) else None


def _load_tasks(path: Path) -> dict[str, dict]:
    return {d["id"]: d for d in
            (json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())}


def _load_answers(path: Path) -> list[dict]:
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [r for r in rows if r.get("task_id")]


def audit(suite: Path, answers: Path, *, scorer: "_bench.ExternalScorer | None" = None,
          cells_each: int | None = None) -> dict:
    """一个答案文件的形态读数。**零写入**。"""
    tasks = _load_tasks(suite)
    rows = _load_answers(answers)
    out = {"file": str(answers), "n": len(rows), "form": {}, "gate": {"pass": 0, "fail": 0},
           "unwrap_n": 0, "unwrap_gain_cells": 0, "cells_each": cells_each or 0}
    for r in rows:
        f = form_of(r.get("answer") or "")
        out["form"][f] = out["form"].get(f, 0) + 1
        t = tasks.get(r["task_id"])
        if t is None:
            continue
        v = _vfy.run(r.get("answer") or "", _vfy.derive(t["prompt"], 2))
        out["gate"]["pass" if v.ok else "fail"] += 1
        if f != FORM_TRACE or scorer is None:
            continue
        payload = unwrap(r.get("answer") or "")
        if payload is None:
            continue
        n_cases = len(t["tests"])
        base = scorer.score(t, r.get("answer") or "").score
        after = scorer.score(t, payload).score
        out["unwrap_n"] += 1
        out["unwrap_gain_cells"] += round((after - base) * n_cases)
        out["cells_each"] = n_cases
    return out


def _fmt(res: dict) -> str:
    forms = " ".join(f"{k}={v}" for k, v in sorted(res["form"].items()))
    g = res["gate"]
    line = (f"  {Path(res['file']).name}: n={res['n']}  [{forms}]  "
            f"闸 pass={g['pass']}/fail={g['fail']}")
    if res["unwrap_n"]:
        line += (f" ｜ 可剥={res['unwrap_n']} 剥壳净增={res['unwrap_gain_cells']} 格"
                 f"（每格 {res['cells_each']} 条断言）")
    return line


# ── 自检（四条反证）—— 零模型、零网络 ──────────────────────────────────────
def self_check() -> int:
    """⛔ 每条都在证明「这个读数是活的」，而不是「这个工具跑起来了」。"""
    suite = ROOT / "bench" / "specgen" / "tasks_v2.jsonl"
    tasks = _load_tasks(suite)
    tid = "sg01"
    task = tasks[tid]
    oracle = task["oracle"]
    scorer = _bench.ExternalScorer(
        [sys.executable, str(ROOT / "bench" / "scorers" / "code_exec.py")],
        source=ROOT / "bench" / "scorers" / "code_exec.py")

    ok = True
    print("  §62 形态审计自检（零模型、零网络）—— 四条反证")

    # R1 形态分类：oracle 是 CODE；空串是 EMPTY；一段散文是 PROSE。
    r1 = (form_of(oracle) == FORM_CODE and form_of("") == FORM_EMPTY
          and form_of("这个函数的作用是把每行去重。") == FORM_PROSE)
    print(f"  {'✅' if r1 else '❌'} R1 分类器认得出 CODE / EMPTY / PROSE"
          f"（oracle={form_of(oracle)} 空={form_of('')} 散文={form_of('这个函数…')}）")
    ok &= r1

    # R2 剥壳：手工套一个 `finish({...})` 外壳 ⇒ 必须 TRACE，且**剥离后逐字还原**。
    wrapped = "[step 1] finish({'answer': %r}) — 直接实现规范要求的函数 ⇒ 失败" % oracle
    got = unwrap(wrapped)
    r2 = (form_of(wrapped) == FORM_TRACE and got == oracle)
    print(f"  {'✅' if r2 else '❌'} R2 外壳被认出，且剥出来的载荷**逐字等于**原文"
          f"（form={form_of(wrapped)} 还原={got == oracle}）")
    ok &= r2

    # R3 剥壳的价值：外壳那一份必须打得比载荷低 —— 否则「壳吃掉格子」这句话是假的。
    s_wrap = scorer.score(task, wrapped).score
    s_pay = scorer.score(task, oracle).score
    r3 = s_pay > s_wrap
    print(f"  {'✅' if r3 else '❌'} R3 外壳那一份的分**确实低于**载荷"
          f"（壳={s_wrap:.4f} 载荷={s_pay:.4f}）—— 反过来就说明打分器自己能剥壳，"
          f"那本工具剥出来的数就没有意义")
    ok &= r3

    # R4 剥壳是**只读**的：非 TRACE 的答案一律返回 None（不许"顺手剥一下"）。
    r4 = (unwrap(oracle) is None and unwrap("") is None
          and unwrap("[step 1] run_shell({'cmd': 'ls'})") is None)
    print(f"  {'✅' if r4 else '❌'} R4 只剥 `finish`：代码本身/空串/别的工具一律返回 None"
          f"（否则会把 `run_shell` 的输出当答案，那是伪造）")
    ok &= r4

    print("-" * 74)
    print(f"  自检结果：{'全绿（可以出数）' if ok else '红项 ⇒ 拒绝出数'}")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if "--self-check" in argv:
        return self_check()
    suite = answers = None
    for i, a in enumerate(argv):
        if a == "--suite" and i + 1 < len(argv):
            suite = Path(argv[i + 1])
        elif a == "--answers" and i + 1 < len(argv):
            answers = Path(argv[i + 1])
    if not suite or not answers:
        print(__doc__)
        return 2
    scorer = _bench.ExternalScorer(
        [sys.executable, str(ROOT / "bench" / "scorers" / "code_exec.py")],
        source=ROOT / "bench" / "scorers" / "code_exec.py")
    print(f"  形态审计：suite={suite.name}  answers={answers.name}")
    print(_fmt(audit(suite, answers, scorer=scorer)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
