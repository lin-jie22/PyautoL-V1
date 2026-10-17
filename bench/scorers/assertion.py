#!/usr/bin/env python3
"""断言打分器 —— 判「答案里说出来的话，能不能被核对」。

## 它补的是哪一格

`code_exec.py` 判的是**代码能不能跑通**。它给"语言与沟通"这一格留下的判据是
一个**代理量**：答案字数。字数答不了真正的问题 ——

    一段 300 字的空话，和一段 80 字把事情说清楚的答案，谁在沟通？

字数分不出来。能分出来的是**可复核性**：这道题要求说清的事实，答案说了几条。

## 判据来源的位移（这是本文件存在的理由）

| | 判据从哪来 | 性质 |
|---|---|---|
| 旧 | 打分器自己发明的规则（"答案短于目标 1/5 = 敷衍"） | **尺子的想象** |
| 新 | 任务**自带**的 `assertions`（这道题该说清什么） | **任务自带的 ground truth** |

这个位移才是从代理量升级为真判据的实质。判据不由测量者发明，
就没人能通过调松判据让分数变好看 —— 那是 V5 的死法。

## 受控词表（**唯一事实源**，就在这个文件里）

    contains_any   答案须包含其中**至少**一个词
    contains_all   答案须包含**全部**给定词
    contains_none  答案**不得**包含任何一个（抓套话 / 幻觉）
    min_chars      答案长度须 >= N（这是**这道题**声明的要求，不是全局规则）

⛔ `mw/` 侧**不得**另立一份同样的词表（契约 AH1 静态锚钉住）。
理由：判定的合法性由裁判决定。任务层若也校验一遍 kind，
两处迟早漂移成"任务层认为合法、打分器不认"的第二套真相。

## 为什么要逐条输出明细

    {"score": 0.6, "why": "通过 3/5；未满足 a4(...)、a5(...)"}

`code_exec` 的 why 是"通过 5/5" —— 一个**标量**。
对代码题那够了（错一条就是错一条，看代码就懂）。
对语言题不够：你必须说出**哪一条没说到**，否则内核拿到的还是"我不对"三个字。

逐条明细是本轮与 critique 通道的协同点：
`Scored.why` 里现在带的是**逐条可归因的理由**，
上一轮接上的那条只读通道立刻就能吃到比标量信息量大得多的过程性反馈。

协议（一行进、一行出，与 `code_exec.py` 同）
    stdin  : {"task": {...}, "answer": "..."}
    stdout : {"score": 0..1, "why": "...", "detail": [{"id","ok","why"}, ...]}
    退出码 : 0 = 判分完成（score 可为 0，那是**真实**的 0 分）
             2 = 输入不可判（缺 assertions / kind 非法）—— 没量到，不是 0 分
             1 = 打分器自身故障
"""

from __future__ import annotations

import json
import sys

#: 受控词表。新增一种断言必须先在这里登记 —— 否则 `mw` 侧的任务集
#: 会带着一个没人认识的 kind 进来，判分时才发现，而那时已经跑完一轮了。
KINDS = ("contains_any", "contains_all", "contains_none", "min_chars")

_UNSET = object()


def _as_list(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return []


def judge_one(a: dict, answer: str) -> tuple[bool, str]:
    """判一条断言。返回 (通过, 说明)。

    ⛔ 说明里**必须**带上这条断言自己在说什么（它的 `why`），
    否则"未满足 a4"这种输出对内核没有任何信息量 ——
    它不知道 a4 是什么，也就无从改起。
    """
    aid = str(a.get("id", ""))
    kind = str(a.get("kind", ""))
    why = str(a.get("why", "")) or "(此条未写 why)"
    if kind not in KINDS:
        raise ValueError(f"{aid}: 未知 kind={kind!r}（受控词表={list(KINDS)}）")
    low = answer.lower()

    if kind == "contains_any":
        cands = _as_list(a.get("value"))
        if not cands:
            raise ValueError(f"{aid}: contains_any 缺 value")
        ok = any(str(c).lower() in low for c in cands)
        return ok, (why if ok else f"{why}（未提到 {cands}）")

    if kind == "contains_all":
        cands = _as_list(a.get("value"))
        if not cands:
            raise ValueError(f"{aid}: contains_all 缺 value")
        miss = [str(c) for c in cands if str(c).lower() not in low]
        return (not miss), (why if not miss else f"{why}（缺 {miss}）")

    if kind == "contains_none":
        cands = _as_list(a.get("value"))
        hit = [str(c) for c in cands if str(c).lower() in low]
        return (not hit), (why if not hit else f"{why}（出现了 {hit}）")

    # min_chars —— 值必须是非负整数，不接受 bool（True 会被当成 1）
    n = a.get("value", _UNSET)
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValueError(f"{aid}: min_chars 的 value 必须是整数，收到 {n!r}")
    ok = len(answer) >= int(n)
    return ok, (why if ok else f"{why}（只有 {len(answer)} 字，要求 >= {n}）")


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as e:
        print(f"输入不是合法 JSON: {e}", file=sys.stderr)
        return 1
    task = payload.get("task") or {}
    answer = str(payload.get("answer") or "")

    asserts = task.get("assertions")
    if not isinstance(asserts, list) or not asserts:
        # ⛔ 缺断言 = **不可判**，不是 0 分。
        #    把"这道题没说清该说什么"记成"答案 0 分"，
        #    就是把尺子的缺陷记到被测系统头上 —— 契约③的老规矩。
        print("任务缺 assertions ⇒ 不可判（退出码 2，这不是 0 分）", file=sys.stderr)
        return 2

    detail: list[dict] = []
    for i, a in enumerate(asserts):
        if not isinstance(a, dict):
            print(f"第 {i} 条断言不是对象: {a!r}", file=sys.stderr)
            return 2
        if not str(a.get("id", "")).strip():
            print(f"第 {i} 条断言缺 id", file=sys.stderr)
            return 2
        try:
            ok, note = judge_one(a, answer)
        except ValueError as e:
            print(f"{e}", file=sys.stderr)
            return 2
        detail.append({"id": str(a.get("id")), "ok": bool(ok), "why": note})

    passed = [d for d in detail if d["ok"]]
    failed = [d for d in detail if not d["ok"]]
    score = len(passed) / len(detail) if detail else 0.0
    why = (f"通过 {len(passed)}/{len(detail)}"
           + ("；未满足：" + "、".join(f"{d['id']}({d['why']})" for d in failed)
              if failed else ""))
    json.dump({"score": score, "why": why, "detail": detail},
              sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
