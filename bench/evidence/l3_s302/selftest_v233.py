"""V233（§304）离线自测：提议-验证-采纳的三段闸。

⛔ 为什么必须单测：这条路的全部安全性都压在「**只有更好才写**」上。
   闸一松，账本就会被模型自己写坏 —— 而账本正是下一轮唯一的输入。

    B1 `parse_proposal`：认得出代码块 / 没围栏但有 `def` / 什么都没有（⛔ 不许用原文兜底）
    B2 `verify` 与打分器同口径：同一份码数出 4 / 3（V230 的原码与挖洞码）
    B3 `promote` **无条件**覆盖，且 `promoted` 单独计数
    B4 反证：同一场景走 `add()` 会被 `freeze_pass` 拦下 ⇒ 两套判据不同源，必须分开
    B5 端到端：更好 ⇒ 写；持平 ⇒ 不写；更差 ⇒ 不写

零 LLM、零网络。跑法：
    unset PYTHONPATH && python3 bench/evidence/l3_s302/selftest_v233.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.critique import Critique, CritiqueLedger      # noqa: E402
from mw.reflect import parse_proposal, verify         # noqa: E402

TESTS = [
    "assert parse_duration('1h30m') == 5400",
    "assert parse_duration('45s') == 45",
    "assert parse_duration('2h') == 7200",
    "assert parse_duration('10m') == 600",
    "assert parse_duration('') is None",
    "assert parse_duration('abc') is None",
]

#: V230 落盘那版（4/6）。
BASE = '''def parse_duration(s):
    if not s:
        return None
    try:
        parts = s.lower().split('h')
        if len(parts) == 1:
            parts = s.lower().split('m')
            if len(parts) == 1:
                parts = s.lower().split('s')
                if len(parts) == 1:
                    return None
                else:
                    return int(parts[0])
            else:
                hours = int(parts[0])
                minutes = int(parts[1].split('m')[0])
                return hours * 3600 + minutes * 60
        else:
            hours = int(parts[0])
            return hours * 3600
    except ValueError:
        return None'''

#: 挖洞版（`hours * 3600` → `hours * 60`）⇒ 3/6。
HOLED = BASE.replace("            hours = int(parts[0])\n            return hours * 3600",
                     "            hours = int(parts[0])\n            return hours * 60")

#: 手写一份真的 6/6（逐段解析），用来验证「更好 ⇒ 采纳」的正例路径。
GOOD = '''def parse_duration(s):
    if not s:
        return None
    total = 0
    num = ''
    for ch in s.lower():
        if ch.isdigit():
            num += ch
        elif ch in 'hms' and num:
            unit = {'h': 3600, 'm': 60, 's': 1}[ch]
            total += int(num) * unit
            num = ''
        else:
            return None
    return total if total else None'''

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


print("== B1 parse_proposal 三种形态")
c1, f1 = parse_proposal("好的：\n```python\ndef f():\n    return 1\n```\n以上。")
chk("代码块被抽出且 fenced=True", c1.strip() == "def f():\n    return 1" and f1, repr(c1[:30]))
c2, f2 = parse_proposal("def f():\n    return 2\n")
chk("无围栏但有 def ⇒ fenced=False（要计数）", f2 is False and c2.startswith("def f()"))
c3, f3 = parse_proposal("抱歉，我做不到。")
chk("⛔ 什么都没有 ⇒ 空串（不许用原文兜底）", c3 == "" and f3 is False, repr(c3))

print("== B2 verify 与打分器同口径")
chk("原码 4/6", verify(BASE, TESTS) == 4, str(verify(BASE, TESTS)))
chk("挖洞码 3/6", verify(HOLED, TESTS) == 3, str(verify(HOLED, TESTS)))
chk("手写的好版本 6/6", verify(GOOD, TESTS) == 6, str(verify(GOOD, TESTS)))
chk("空串 0", verify("", TESTS) == 0)

print("== B3 promote 无条件覆盖 + 单独计数")
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "led.jsonl"
    led = CritiqueLedger(p)
    led.add(Critique(scorer_sha="s", suite_sha="u", task_id="t", score=0.6667,
                     why="通过 4/6", answer=BASE))
    led.save()
    #: 旧的是 4/6 + 开 freeze_pass ⇒ 新的满分会被 `add()` 拦（B4 反证组）
    #: ⚠️ 必须 `load()`：不加载的话 `by_key` 是空的，第一次 `add` 直接写入，
    #:    于是"被拦"这条断言会在**空的旧状态**上跑（首版本轮就栽在这，假红）。
    led2 = CritiqueLedger(p, freeze_pass=True)
    led2.load()
    led2.add(Critique(scorer_sha="s", suite_sha="u", task_id="t", score=1.0,
                      why="通过 6/6（add 路径）", answer=GOOD))
    chk("B4 反证：`add()` 面对满分旧行 + freeze_pass ⇒ 不覆盖",
        led2.by_key[("s", "u", "t")].answer == BASE and led2.frozen == 1,
        f"answer_changed={led2.by_key[('s','u','t')].answer != BASE} frozen={led2.frozen}")
    led2.promote(Critique(scorer_sha="s", suite_sha="u", task_id="t", score=1.0,
                          why="通过 6/6（promote 路径）", answer=GOOD))
    got = led2.by_key[("s", "u", "t")]
    chk("B3 `promote()` **无条件**覆盖（同一场景 add 被拦、promote 能写）",
        got.answer == GOOD and got.score == 1.0, f"score={got.score}")
    chk("B3 `promoted` 单独计数（与 frozen/best_kept 不混）",
        led2.promoted == 1 and led2.best_kept == 0, f"promoted={led2.promoted}")

    print("== B5 端到端：更好写 / 持平不写 / 更差不写")
    #: 另起一份只有 4/6 的账本，否则 `base` 已经是 6/6、没有改进空间（首版就是这么假红的）。
    p2 = Path(td) / "led2.jsonl"
    led3 = CritiqueLedger(p2)
    led3.add(Critique(scorer_sha="s", suite_sha="u", task_id="t", score=0.6667,
                      why="通过 4/6", answer=BASE))
    base = verify(led3.by_key[("s", "u", "t")].answer, TESTS)
    chk("起点是 4/6（有改进空间）", base == 4, str(base))
    for name, cand, expect in (("更好", GOOD, True), ("持平", BASE, False), ("更差", HOLED, False)):
        n0 = led3.promoted
        if verify(cand, TESTS) > base:
            led3.promote(Critique(scorer_sha="s", suite_sha="u", task_id="t",
                                  score=verify(cand, TESTS) / len(TESTS), answer=cand))
        chk(f"{name} ⇒ {'采纳' if expect else '不采纳'}", (led3.promoted > n0) == expect,
            f"promoted {n0}→{led3.promoted}")

print(f"\n{ok} ok / {bad} bad")
sys.exit(1 if bad else 0)
