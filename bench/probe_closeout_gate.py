#!/usr/bin/env python3
"""交付物「过没过内核自己的自检」—— 一条没有闸的补救通道，量它。

## 它量的是什么

第九十轮那对两臂实验里，`qwen2.5:7b` 三次跑同一道 `sg10` 得到 **14 / 1 / 14** 格
（同一把尺子、T=0、每题新建内核）。查下来不是"模型抖动"：

    三个 rep 的第一版**都被自检打回**（"1/5 项未过：自检未通过（不提供细节）"）；
    第二次 `finish` 被判「⏭ 重复动作已跳过（不产生新信息）」；
    墙钟转收尾 ⇒ `_close_out` 又发了一次**专门索要答案**的调用；
    那一次交出的 419 字符版本**少了 `import re`** —— 而它被直接交付，**没有再过自检**。

    14 → 1 的 13 格全部来自 `NameError: name 're' is not defined`（14 条断言里 13 条）；
    唯一通过的那条是 `norm_sg10([]) == []`，它在碰到 `re` 之前就返回了。

## 为什么这是一个**缺陷**而不是设计

`mw/core.py` 里有两处声明"未通过自检的交付必须说出来"：

  · 第 1049 行：「两个来源（内核自己的 `finish` / 外部能力的答案）走**同一条**交付路径：
    **同一道自检闸**、同一个 `done_unverified` 语义」
  · `_verify` 的文档第 ③ 条：「**未通过自检的交付不许报 `done`**。状态是读数的一部分」

而 `_close_out`（第 1446 行起）拿到 `best = self.planner.answer(...)` 之后，
**直接** `return done("closed", best, ...)` —— 从不调用 `vfy.run(best, self._checks)`，
也不写 `self._unverified`。⇒ 收尾交付是一条**绕过闸且不留痕**的路：
`verify.runs` 不增加、`unverified_delivery` 仍是 False。

## 读数（第九十轮，两臂 60 份交付物）

    臂 A（qwen2.5:7b）：30 份里 **1 份**通不过自检（`sg10`，`结构检查` 红）
    臂 B（Qwen3.5-4B）：30 份里 **0 份**
    收尾交付合计 11 次（A 6 / B 5）⇒ 这条无闸路上的不合格率 **1/11**
    正对照：10 题 oracle 全部通过（检查器有牙但不乱咬）

⛔ 这份读数**不主张**"收尾有害"：同一批里 `overrode_rejected=5` 说明收尾常常换掉
   被打回的那一版（§54 已证"看起来更对题"不等于更值钱，这里同样不作价值判断）。
   它只说一件事：**这条路上的交付物没有被测过，所以它好不好是 UNKNOWN。**

## 用法

    python3 bench/probe_closeout_gate.py --answers <answers.jsonl> \\
        --suite bench/specgen/tasks_v2.jsonl
    python3 bench/probe_closeout_gate.py --self-check      # 六条反证，零模型零网络
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from mw.verify import derive, run      # noqa: E402

#: 与运行时同一个值：`V1_VERIFY_STATIC` 的默认档（第八十轮 `KEEP`）。
STATIC = 2


def check_one(prompt: str, answer: str) -> tuple[bool, tuple[str, ...], str]:
    """用**运行时同一组判据**判一份交付物。⛔ 不另写一套。"""
    return_code = run(answer, derive(prompt, static=STATIC))
    return return_code.ok, return_code.failed, return_code.report


def measure(answers_path: Path, suite_path: Path) -> int:
    suite = [json.loads(l) for l in suite_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    prompt = {t["id"]: t["prompt"] for t in suite}
    oracle = {t["id"]: t["oracle"] for t in suite}
    rows = [json.loads(l) for l in answers_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    ans = [r for r in rows if r.get("kind") == "answer"]
    if not ans:
        print(f"❌ {answers_path} 里没有 kind=answer 行（空文件 ≠ 全部通过）", file=sys.stderr)
        return 2

    bad = []
    missing = []
    #: ⚠️ `answers.jsonl` 的 `rep` 字段**恒为 1**（`run.py:1199` 调 `_ans_log.add(t.id, a)`
    #:    时没传 `rep`，`AnswerStore.add` 的默认值是 1）⇒ 它与 `per_task[].rep`
    #:    对不上，**不能用来定位是第几次 repeat**。
    #:    唯一可用的替代是**行序**（每题每轮出现一次，按题序循环）——
    #:    但"行序 == 执行顺序"是一句**没有任何地方声明过**的约定，所以这里
    #:    同时印出两个号：`rep`（落盘声明，恒 1）与 `ord`（按行序推断）。
    seen: dict[str, int] = {}
    for r in ans:
        tid = r.get("task_id")
        seen[tid] = seen.get(tid, 0) + 1
        if tid not in prompt:
            missing.append(tid)
            continue
        ok, failed, rep = check_one(prompt[tid], r["answer"])
        if not ok:
            bad.append((tid, r.get("rep"), seen[tid], failed, rep))

    #: ★ 正对照：oracle 必须**全部通过**。它红了不是"oracle 差"，是**检查器坏了**，
    #:   那时上表里任何一个"不合格"都不能当读数用。
    oracle_bad = [tid for tid in prompt if not check_one(prompt[tid], oracle[tid])[0]]

    print("=" * 76)
    print("  交付物是否通过内核自检（static=%d，同一组判据）" % STATIC)
    print("=" * 76)
    print(f"  答案 {len(ans)} 份｜不合格 **{len(bad)}** 份｜题集里找不到的 {len(missing)} 份")
    for tid, rep, ordn, failed, why in bad:
        print(f"     ❌ {tid}（rep={rep} 落盘值 / ord={ordn} 行序推断）failed={failed}  {why[:70]}")
    print(f"  正对照：{len(prompt)} 题 oracle 不合格的 = {oracle_bad}"
          f"  {'✅ 检查器有牙但不乱咬' if not oracle_bad else '❌ 检查器坏了 ⇒ 上面全部作废'}")
    if missing:
        print(f"  ⚠️ 有答案的题号不在题集里：{missing[:5]}")
    print()
    print("  ⛔ 本读数只回答「交付物过没过闸」，不回答「收尾该不该留」。")
    print("  ⛔ 不合格 ≠ 低分：分数由外部打分器给，两者**不可互相代替**。")
    return 0 if not oracle_bad else 1


# ---------------------------------------------------------------------------
def _self_check() -> int:
    print("── 交付物自检闸 · 量具自检（六条反证）──")
    bad = 0

    def chk(name: str, ok: bool, note: str = "") -> None:
        nonlocal bad
        print(f"  {'✅' if ok else '❌'} {name}" + (f"  [{note}]" if note else ""))
        if not ok:
            bad += 1

    #: ⚠️ 题面必须带上**那两个措辞**（"只输出代码" / "函数名必须是 X"）——
    #:    `derive()` 的判据是从题面派生的，两句缺任一句就只剩"非空"一条。
    #:    第一版自检用的题面没写这两句 ⇒ 只派生出 1 条判据 ⇒ C2/C3/C5 全红。
    #:    那不是量具坏了，是**自检的题面不像真题面**（真 10 题都同时含这两句，
    #:    见下方 `--suite` 的 10 题逐题核对）。
    P = "写一个函数 f(x)，返回处理后的列表。只输出代码，函数名必须是 f。"
    GOOD = "```python\ndef f(x):\n    return sorted(x)\n```"
    BAD_UNDEF = "```python\ndef f(x):\n    return re.sub('a', 'b', x)\n```"
    BAD_NAME = "```python\ndef g(x):\n    return x\n```"
    EMPTY = ""

    chk("C1 合格答案 ⇒ 通过（检查器不咬好人）", check_one(P, GOOD)[0], "")
    #: C2 是**本量具存在理由**的正例：这正是 sg10 那一份的形状。
    chk("C2 缺 import 的答案 ⇒ 不合格，且点名 `未定义的名字`/结构检查",
        not check_one(P, BAD_UNDEF)[0],
        f"failed={check_one(P, BAD_UNDEF)[1]}")
    chk("C3 函数名不对 ⇒ 不合格（不是只有未定义名这一条判据）",
        not check_one(P, BAD_NAME)[0], f"failed={check_one(P, BAD_NAME)[1]}")
    chk("C4 空答案 ⇒ 不合格（空交付不许算通过）", not check_one(P, EMPTY)[0], "")

    #: C5 反证：`static=0` 时第 5 条判据**必须不存在** ——
    #:    否则 C2 可能只是"另一条判据碰巧抓到了"，而不是 static 开关在起作用。
    n0 = len(derive(P, static=0))
    n2 = len(derive(P, static=2))
    chk("C5 反证：static=0 比 static=2 少一条判据（C2 归 static 开关管）",
        n2 == n0 + 1, f"{n0} → {n2}")
    #: C6 反证：`static=0` 下同一份缺 import 的答案**必须放行** ——
    #:    这一条挡的是"判据恒响"（恒响的话 C2 毫无信息量）。
    chk("C6 反证：static=0 下同一份缺 import 的答案放行（C2 不是恒真）",
        run(BAD_UNDEF, derive(P, static=0)).ok,
        f"failed={run(BAD_UNDEF, derive(P, static=0)).failed}")

    print(f"\n结论：{'六条全部成立' if bad == 0 else f'{bad} 条不成立'}")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="量交付物过没过内核自检（零模型零网络）")
    ap.add_argument("--answers", default="", help="answers.jsonl")
    ap.add_argument("--suite", default=str(_HERE / "specgen" / "tasks_v2.jsonl"))
    ap.add_argument("--self-check", action="store_true", help="六条反证")
    a = ap.parse_args()
    if a.self_check:
        return _self_check()
    if not a.answers:
        ap.error("要么 --self-check，要么给 --answers")
    return measure(Path(a.answers), Path(a.suite))


if __name__ == "__main__":
    raise SystemExit(main())
