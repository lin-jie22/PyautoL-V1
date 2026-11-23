"""V226（§297）离线正例：证明「投喂上一版代码」这一臂与旧臂**只有新增那一段不同**。

⛔ 为什么必须有这一份：`t01` 的读数如果变了，可能来自三件不同的事 ——
   ① 新增的 `[上版代码]`；② 起手账本的 `answer` 重建；③ 块本身漂了。
   本条把 ③ 与 ② 分别钉住：
     P1 `[外部评审]` 段两臂**逐字节相同**（块没漂）；
     P2 on 臂新增段 == 账本 `answer` **逐字节相同**（喂的就是账本里那份）；
     P3 `answer` 超过 200 字仍能完整往返（`_MAX_ANSWER` 抬升生效，不是截断）。
零 LLM、零网络。跑法：
    unset PYTHONPATH && python3 bench/evidence/l3_s302/selftest_v226.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.core import Core, handoff_text        # noqa: E402
from mw.critique import Critique, CritiqueLedger  # noqa: E402

TASK = "t01_parse_duration"
FAMILY = "suite_sub2"
WHY = ("通过 4/6；失败: assert parse_duration('1h30m') == 5400 ⇒ AssertionError | "
       "assert parse_duration('10m') == 600 ⇒ AssertionError")
#: 与 f1 落盘的那份同长（677 字）的**合成**代码：换行 + 三段结构，
#: 只为验证"整段往返不被切"，不用真代码（真代码在账本重建时逐字取）。
CODE = "```python\ndef parse_duration(s):\n" + "".join(
    f"    # 行 {i:03d}: 填充，用来把长度顶到真实量级\n" for i in range(14)
) + "    pass\n```"


def build(tmp: Path, *, carry: bool) -> tuple[str, dict]:
    led_path = tmp / ("carry.jsonl" if carry else "plain.jsonl")
    led = CritiqueLedger(led_path)
    led.load()
    led.add(Critique(scorer_sha="s1", suite_sha="u1", task_id=TASK, family=FAMILY,
                     score=0.6667, why=WHY, answer=CODE))
    led.save()

    # ⚠️ `allow_self=True`：真跑时由 `--critique-scope prev-self` 打开（AZ99）。
    #   没有它，本题那条会被第 2 条纪律挡掉，`crit` 为空 ⇒ 两臂都**没有块**，
    #   于是「两臂块相同」会**恒真**（假绿：断言在空集上成立）。
    led2 = CritiqueLedger(led_path, allow_self=True)   # 从盘上读回：证明上限抬升后能往返
    n = led2.load()
    assert n == 1, f"账本读回 {n} 条"
    got = led2.by_key[("s1", "u1", TASK)]
    assert got.answer == CODE, f"answer 往返不一致：{len(got.answer)} vs {len(CODE)}"

    core = Core(critique_carry_code=carry, critique_mix="self-only",
                external_handoff="blocks")
    core.critique_ledger = led2
    state = core._state("写一个函数 parse_duration(s)。只输出代码。", [], 1,
                        task_id=TASK, scorer_sha="s1", suite_sha="u1")
    # ⚠️ 必须取 `Core` 自己算出的那份，不能把 state 按 "\n" 切开再喂给 `handoff_text`：
    #   块是用 "\n" 连接的，而**代码正文自己就带换行**，切完只剩首行 ⇒
    #   会得到"移交里没有代码"的假负（首版自测就是这么错的）。
    return state, {"hits": core.carry_code_hits, "chars": core.carry_code_chars,
                   "ok": got.answer == CODE, "len": len(got.answer),
                   "handoff": core._handoff_txt}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="v1_v226_") as td:
        tmp = Path(td)
        off, off_st = build(tmp, carry=False)
        on, on_st = build(tmp, carry=True)

    # ── P3：往返不被切 ───────────────────────────────────────────────
    print(f"P3 answer 往返：len={on_st['len']}（>200）ok={on_st['ok']}")
    assert on_st["ok"], "P3 失败：answer 被截断"

    # ── P1：`[外部评审]` 段逐字节相同 ────────────────────────────────
    def seg(s: str) -> str:
        """取 `[外部评审]` 那**一行**（块是 `\\n` 连接的，后面还跟着 `[上版代码]`/步骤行）。"""
        i = s.find("[外部评审]")
        assert i >= 0, "两臂都应含 [外部评审]"
        j = s.find("\n", i)
        return s[i:] if j < 0 else s[i:j]

    same_block = seg(off) == seg(on)
    print(f"P1 [外部评审] 段两臂逐字节相同：{same_block}")
    print(f"   off 段：{seg(off)!r}")
    assert same_block, "P1 失败：块本身在两臂之间不同 ⇒ 不是单变量"

    # ── P2：新增段 == 账本 answer 原文 ──────────────────────────────
    # ⚠️ 不能用 `on.find(...)` 到结尾：代码正文里带换行，块后面还跟着步骤行。
    #   改成「子串出现」+「总长差额只等于这一段」，两件事都要成立。
    expect = "[上版代码] " + CODE
    has = expect in on
    print(f"P2 新增段 == 账本 answer：{has}（hits={on_st['hits']} "
          f"chars={on_st['chars']}，段长 {len(expect)}）")
    assert has, "P2 失败：喂出去的与账本里存的不是同一份"
    assert off_st["hits"] == 0, "⛔ off 臂不该有 [上版代码]"

    # ── 差额只有一段 ────────────────────────────────────────────────
    delta = len(on) - len(off)
    print(f"差额 = {delta} 字（应为 {len(expect) + 1} = 新增段 + 一个分隔符）")
    assert delta == len(expect) + 1, f"差额 {delta} ≠ 只有新增段"

    # ── P4：**移交**那条路上也得有（V226 首跑就是栽在这：state 有、移交没有）──
    ho_on = on_st["handoff"]
    ho_off = off_st["handoff"]
    assert ho_on, "⛔ `_handoff_txt` 为空：`external_handoff` 默认 off ⇒ 本自测必须显式开 blocks"
    print(f"P4 移交白名单：on 含上版代码={expect in ho_on}｜off 不含={'上版代码' not in ho_off}")
    assert expect in ho_on, "P4 失败：state 里有、移交里没有（HANDOFF_BLOCKS 漏了这一项）"
    assert "[上版代码]" not in ho_off, "P4 失败：off 臂不该把它交出去"

    print(f"\nOFF state ({len(off)} 字)：{off}")
    print(f"\nON  state ({len(on)} 字)：{on[:160]} …（截断显示）")
    print("\nSELFTEST OK（两臂可区分，且只差 `[上版代码]` 一段）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
