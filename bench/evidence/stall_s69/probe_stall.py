#!/usr/bin/env python3
"""停滞出口的**同场景 A/B**：`stalled` 时到底交不交东西。

## 为什么需要这个探针

第六十六轮的 A 臂实测：`■ stalled | steps=3`、`mean=0.0000`，而那一刻墙钟
**还剩 85s** —— 那 85s 里一次答案调用都没发生过。`stalled` 的语义是
"探索到此为止"，它和"交付"是两件事；旧代码把后者也一起停了。

## 两条臂怎么做到只差一个变量

A 臂把 `mw.core.stall_verdict` **替换成恒 `give_up`** —— 那正是第六十八轮之前
的行为（到阈值就 `return _done("stalled", "", …)`）。
B 臂原样跑。两臂共用同一个假模型、同一道题、同一个预算档。

⚠️ A 臂是**对旧路径的模拟**，不是历史重放：它证明的是"这条判据一改，
   读数就从 0 字变成 N 字"，不是"历史上那次真跑会怎样"。

## 它不是"内核变强了"

两臂的分数都还是 0（答案通道在离线桩上给出的是固定串）。
这个探针量的是**交付量**，不是**能力** —— 把 0 字变成一段话，
和让那段话变对，是两件不同的事。

退出码：0 = 两条臂都符合预期；1 = 不符合。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V1))

DEFAULT_GOAL = "读一下这个文件"
#: 动作通道被堵死的形状：同一个 `recall` 反复提 —— 这是 O 组的实测形态。
SAME = ('{"candidates": [{"intent": "再查一次", "tool": "recall", '
        '"args": {"query": "goal"}, "rationale": "查记忆"}]}')
#: 答案通道的判据不是"有输出"，而是**它真的被单独问过**：
#: `Planner.answer()` 的提示词结尾是这句，`propose()` 的提示词里没有它。
ANSWER_MARK = "请直接给出最终答案"
ANSWER = "停滞之后仍然交付的答案（来自答案通道）"


def _run(*, force_give_up: bool, tag: str):
    """跑一次内核。`force_give_up=True` ⇒ 模拟第六十八轮之前的停滞出口。"""
    os.environ["V1_LLM_OFFLINE"] = "1"
    tmp = Path(tempfile.mkdtemp(prefix=f"v1_stall_{tag}_"))
    os.environ["V1_STATE_DIR"] = str(tmp / "state")

    import mw.core as core_mod
    from mw.core import Core
    from mw.llm import StubLLM
    from mw.memory import MemoryStore

    orig = core_mod.stall_verdict
    if force_give_up:
        # ⛔ 只换这一个纯函数：其它一切（预算、提示词、工具、记忆）逐字相同。
        core_mod.stall_verdict = lambda **_kw: "give_up"       # noqa: ARG005
    try:
        llm = StubLLM(scripts=[(ANSWER_MARK, ANSWER)], default=SAME)
        out = Core(llm=llm, memory=MemoryStore(tmp / "mem.db"),
                   wire_external=False, log=lambda *_a: None).run(DEFAULT_GOAL)
    finally:
        core_mod.stall_verdict = orig
    return out


def main() -> int:
    old = _run(force_give_up=True, tag="old")
    new = _run(force_give_up=False, tag="new")
    print("=" * 74)
    print("停滞出口同场景 A/B（离线桩；只看交付量，不看分数）")
    print("=" * 74)
    for name, out in (("A 旧行为（stall ⇒ 交零）", old), ("B 现行（stall ⇒ 走答案通道）", new)):
        co = out.stats.get("closeout") or {}
        print(f"  {name}")
        print(f"    status={out.status:8s} 交付={len(out.answer):3d}字 "
              f"by_reason={co.get('by_reason')} origin={co.get('origin')}")
        print(f"    answer={out.answer[:40]!r}")
        print(f"    reason={out.reason[:70]!r}")

    ok = [
        # ① 两臂都必须走到停滞出口（否则这不是"停滞"场景的对照）
        ("两条臂都到达 stalled", old.status == "stalled" and new.status == "stalled"),
        # ② 旧行为：交零（这正是本轮修的那个损失）
        ("A 臂交零", old.answer == ""),
        # ③ 现行：交付的是**答案通道**的产出，不是 history/episodic 兜底
        ("B 臂交付答案通道的产出", new.answer == ANSWER),
        # ④ 归因正确：不许把"停滞"说成"预算不足"
        ("B 臂原因写「不是预算不足」", "不是预算不足" in new.reason),
        # ⑤ 读数留痕：`by_reason.stalled`
        ("B 臂 by_reason.stalled == 1",
         ((new.stats.get("closeout") or {}).get("by_reason") or {}).get("stalled") == 1),
        # ⑥ 反证：两臂读数必须不同（相同 ⇒ 这个探针什么也没测到）
        ("两臂读数不同（反证）", old.answer != new.answer),
    ]
    print()
    bad = 0
    for name, cond in ok:
        print(f"  {'✅' if cond else '❌'} {name}")
        bad += 0 if cond else 1
    print()
    print(f"结论：{'✅ 停滞不再等于交零' if not bad else f'❌ {bad} 项不成立'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
