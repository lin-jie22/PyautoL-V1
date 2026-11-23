"""V231（§302）离线正例：`[自我修订]` 这一段**只可能是它自己**带来的。

⛔ 为什么必须有这一份：`t01` 的读数如果动了，可能来自四件不同的事 ——
   ① 新增的 `[自我修订]` 段；② `[上版代码]` 段漂了；③ 起手账本重建；
   ④ 说明绑到了**另一份**代码上（`freeze_pass` 会把旧输入冻在账本里）。
本条把 ②③④ 分别钉住，并给 ① 一条**正例**（不是只有反例）：

    P1 `parse_lesson` 认得出「它写了代码」——⛔ 不许把越界当格式小问题静默用掉
    P2 `pick` 三态可分：hit / stale / absent（合并三者 = 分不清「没东西」与「被校验挡了」）
    P3 on 臂：`[上版代码]` 与关闭时**逐字节相同**，新增段只有 `[自我修订]`
    P4 两处都要有：`state` 里有，`handoff_text()` 里也有（纪律 144）
    P5 反例：说明是**另一份代码**的 ⇒ 不注入，且计 `stale` 而不是 `absent`
    P6 反例：开 reflect 不开 carry ⇒ 不注入，计 `unpaired`（不是 absent）
    P7 默认 off：不传开关时 state 与 V230 的 off 行为一致（无该段、计数全 0）
    P8 落盘往返：`save_lessons` / `load_lessons` / `index_lessons`

零 LLM、零网络。跑法：
    unset PYTHONPATH && python3 bench/evidence/l3_s302/selftest_v231.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.core import Core, handoff_text                       # noqa: E402
from mw.critique import Critique, CritiqueLedger             # noqa: E402
from mw.reflect import (Lesson, build_prompt, index_lessons,  # noqa: E402
                        load_lessons, parse_lesson, pick, save_lessons, sha_of)

TASK = "t01_parse_duration"
FAMILY = "suite_sub2"
WHY = ("通过 4/6；失败: assert parse_duration('1h30m') == 5400 ⇒ AssertionError | "
       "assert parse_duration('10m') == 600 ⇒ AssertionError")
CODE = "```python\ndef parse_duration(s):\n    return 0\n```"
OTHER = "```python\ndef parse_duration(s):\n    return 12345\n```"
LESSON = "失败在单位换算：多段输入（如 1h30m）没有被拆成 h/m/s 分别累加，只取了第一段。"
GOAL = "写一个函数 parse_duration(s) 把 '1h30m' 解析成秒。只输出代码。"

ok = 0
bad = 0


def chk(name: str, cond: bool, detail: str = "") -> None:
    global ok, bad
    if cond:
        ok += 1
        print(f"  ✅ {name}")
    else:
        bad += 1
        print(f"  ❌ {name}  {detail}")


def build_core(tmp: Path, *, carry: bool, reflect: bool, answer: str = CODE,
               lesson_on: str = CODE) -> tuple[str, Core]:
    led_path = tmp / ("led.jsonl")
    led = CritiqueLedger(led_path)
    led.add(Critique(scorer_sha="s1", suite_sha="u1", task_id=TASK, family=FAMILY,
                     score=0.6667, why=WHY, answer=answer))
    led.save()
    #: ⚠️ `allow_self=True`：真跑时由 `--critique-scope prev-self` 打开（AZ99）。
    #:   没有它本题那条会被第 2 条纪律挡掉 ⇒ `crit` 为空 ⇒ 两臂都没有块 ⇒
    #:   「两臂相同」在空集上**恒真**（V226 首版自测就是这么假绿的）。
    led2 = CritiqueLedger(led_path, allow_self=True)
    led2.load()

    lsn: dict = {}
    if reflect:
        items = [Lesson(scorer_sha="s1", suite_sha="u1", task_id=TASK,
                        based_on_sha=sha_of(lesson_on), lesson=LESSON,
                        at_score=0.6667, at_vec="010011")]
        lsn = index_lessons(items)
    core = Core(critique_carry_code=carry, critique_reflect=reflect,
                lessons=lsn, critique_mix="self-only", external_handoff="blocks")
    core.critique_ledger = led2
    state = core._state(GOAL, [], 1, task_id=TASK, scorer_sha="s1", suite_sha="u1")
    return state, core


print("== P1 parse_lesson：越界要认得出")
t1, v1 = parse_lesson("好的，以下是修订说明：\n应把 h/m/s 分别累加，不要只取第一段。")
chk("开场白被压掉", not t1.startswith("好的"), repr(t1[:20]))
chk("纯文字不判越界", v1 is False)
t2, v2 = parse_lesson("把函数改成：\n```python\ndef parse_duration(s): ...\n```")
chk("写了代码 ⇒ violated=True", v2 is True, repr(v2))
chk("⛔ 越界内容仍原样返回给调用方去计数（不静默裁掉）", len(t2) > 0)

print("== P2 pick 三态")
idx = index_lessons([Lesson("s1", "u1", TASK, sha_of(CODE), LESSON)])
chk("hit", pick(idx, scorer_sha="s1", suite_sha="u1", task_id=TASK, answer=CODE)[1] == "hit")
chk("stale（代码换了）",
    pick(idx, scorer_sha="s1", suite_sha="u1", task_id=TASK, answer=OTHER)[1] == "stale")
chk("absent（没这道题）",
    pick(idx, scorer_sha="s1", suite_sha="u1", task_id="t99", answer=CODE)[1] == "absent")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)

    print("== P3/P4 on 臂：只多一段，且两处都有")
    s_off, c_off = build_core(tmp, carry=True, reflect=False)
    s_on, c_on = build_core(tmp, carry=True, reflect=True)
    i_off = s_off.find("[上版代码]")
    i_on = s_on.find("[上版代码]")
    chk("`[上版代码]` 之前逐字节相同", s_off[:i_off] == s_on[:i_on], f"{i_off} vs {i_on}")
    chk("off 臂无 `[自我修订]`", "[自我修订]" not in s_off)
    chk("on 臂有 `[自我修订]`", "[自我修订]" in s_on)
    chk("on 臂 `[上版代码]` 段与 off **逐字节相同**",
        s_on[i_on:s_on.find("[自我修订]")].rstrip("\n") == s_off[i_off:].rsplit("\n[第", 1)[0].rstrip("\n"),
        "段漂了")
    chk("state 里有（第一处）", "[自我修订]" in s_on)
    chk("handoff 里也有（第二处，纪律 144）", "[自我修订]" in c_on._handoff_txt)
    chk("注入计数 hit=1", c_on.reflect_hits == 1, str(c_on.reflect_hits))
    chk("off 臂计数全 0", (c_off.reflect_hits, c_off.reflect_absent,
                          c_off.reflect_stale, c_off.reflect_unpaired) == (0, 0, 0, 0))

    print("== P5 反例：说明绑的是另一份代码 ⇒ 不注入、计 stale")
    s_st, c_st = build_core(tmp / "a", carry=True, reflect=True, lesson_on=OTHER)
    chk("无 `[自我修订]`", "[自我修订]" not in s_st)
    chk("计 stale（不是 absent）", c_st.reflect_stale == 1 and c_st.reflect_absent == 0,
        f"stale={c_st.reflect_stale} absent={c_st.reflect_absent}")

    print("== P6 反例：开 reflect 不开 carry ⇒ 计 unpaired")
    s_up, c_up = build_core(tmp / "b", carry=False, reflect=True)
    chk("无 `[自我修订]`", "[自我修订]" not in s_up)
    chk("计 unpaired（不是 absent）", c_up.reflect_unpaired == 1 and c_up.reflect_absent == 0,
        f"unpaired={c_up.reflect_unpaired} absent={c_up.reflect_absent}")

    print("== P7 反例：真 off 时连计数器都不存在")
    core = Core()          # 全默认
    st = core._state(GOAL, [], 1, task_id=TASK, scorer_sha="s1", suite_sha="u1")
    chk("默认 state 无该段", "[自我修订]" not in st)
    chk("默认 reflect_hits=0 且 unpaired=0（开关没接线时不许伪装成 absent）",
        core.reflect_hits == 0 and core.reflect_unpaired == 0)

    print("== P8 落盘往返")
    p = tmp / "lsn.jsonl"
    n1 = save_lessons(p, [Lesson("s1", "u1", TASK, sha_of(CODE), LESSON, 0.6667, "010011")])
    back = load_lessons(p)
    chk("写入 1 条、读回 1 条", n1 == 1 and len(back) == 1, f"{n1} / {len(back)}")
    chk("字段逐字相同", back[0].lesson == LESSON and back[0].based_on_sha == sha_of(CODE))
    n2 = save_lessons(p, [Lesson("s1", "u1", TASK, sha_of(OTHER), "新说明")])
    again = load_lessons(p)
    chk("同键覆盖（不是插完再清理）", n2 == 1 and len(again) == 1 and again[0].lesson == "新说明",
        f"n2={n2} len={len(again)}")

print("== P9 prompt 里禁止写代码（那一条是可归因的前提，不是格式洁癖）")
pr = build_prompt(GOAL, CODE, WHY)
chk("含题目/代码/测试三段", GOAL in pr and CODE in pr and "AssertionError" in pr)
chk("明确要求不输出代码", "不要输出任何代码" in pr)

print(f"\n{ok} ok / {bad} bad")
sys.exit(1 if bad else 0)
