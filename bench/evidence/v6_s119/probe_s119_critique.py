"""P0 装置探针 —— 「外部评审」这条跨轮通道到底通不通（离线，零 LLM）。

## 为什么必须先跑这个

V19~V28 的 `[外部评审]` 在 **108 个 state 快照里出现 0 次**，而同一目录下的
`critique.jsonl` 有 6 行。两种解释读起来一模一样：
  ① 闸门（`_admissible_critiques`）拒了；
  ② 通道从来没接上（`_crit_for_kernel = None`，`run.py:995`）。
先分清是哪一个，否则「开闸」这个处置可能落在一条本来就不存在的路上（纪律 14）。

## 三个子检查

- **C-a 静态**：账本非空 + 传进 Core ⇒ `_state()` 里必须有 `[外部评审]`。
  ==证明"有内容就一定送达"，排除"渲染层丢块"==。
- **C-b 同对象**：本轮 `ledger.add()` 之后立刻 `_state()` ⇒ 可见。
  这就是 `--use-critique` 现状的语义（轮内也投喂）。
- **C-c 副本**：把加载时的条目快照进**另一个** `CritiqueLedger`，真账本再加新的
  ⇒ 副本视图里**看不到新条目**。这就是 `--critique-scope=prev` 要的语义
  （跨轮给，轮内不给）。

⛔ 本探针**不产生**任何分数读数；它只回答"这条路线存不存在"。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/Users/linshaojie/V1")

_HERE = Path(__file__).resolve().parent
SCORER = "d4b1cbbfd7f1ef50d1e1a2a4cd95371ec7a82c05f6cb6d277e079814ea021076"
SUITE = "519dad774a3664612e6611b220b33f4a"
WHY = "通过 0/16；失败: import pathlib as _pp ⇒ NameError: name '_split' is not defined"


def _mk_core(tmp: Path, ledger):
    os.environ["V1_STATE_DIR"] = str(tmp / "state")
    os.environ["V1_MEMORY_DIR"] = str(tmp / "mem")
    from mw import config as cfg
    from mw.core import Core
    from mw.llm import StubLLM
    s = cfg.settings()
    return Core(s, llm=StubLLM(), critique_ledger=ledger)


def _state(core, task: str) -> str:
    return core._state("请把函数 clean_lines 补全。", ["Observe"], 1,
                       task_id=task, scorer_sha=SCORER, suite_sha=SUITE)


def main() -> int:
    from mw.critique import Critique, CritiqueLedger

    out: list[str] = []
    ok = True

    def say(tag: str, cond: bool, detail: str) -> None:
        nonlocal ok
        ok = ok and cond
        out.append(f"{'✅' if cond else '❌'} {tag}  {detail}")

    # ── C-a：账本非空 ⇒ 送达 ────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        led = CritiqueLedger(None)
        led.add(Critique(scorer_sha=SCORER, suite_sha=SUITE, task_id="e3ni",
                         family="tasks_v8", score=0.0, why=WHY))
        core = _mk_core(tmp, led)
        st = _state(core, "e2ni")
        hit = "[外部评审]" in st
        say("C-a 账本非空 ⇒ [外部评审] 进 state", hit,
            f"含块={hit}  len(state)={len(st)}")
        say("C-a' 块内含别题 id 与 why 片段", ("e3ni" in st) and ("_split" in st),
            f"含e3ni={'e3ni' in st} 含_split={'_split' in st}")
        # 自评闸：当前题 e3ni 自己不该看到 e3ni
        st_self = _state(core, "e3ni")
        say("C-a'' 自评闸（当前题看不到自己）", "[外部评审]" not in st_self,
            f"e3ni 自看含块={'[外部评审]' in st_self}")
        core.memory.close()

    # ── C-b/C-c：同对象 vs 副本 ────────────────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        real = CritiqueLedger(None)
        snap = CritiqueLedger(None)
        real.add(Critique(scorer_sha=SCORER, suite_sha=SUITE, task_id="e3ni",
                          family="tasks_v8", score=0.0, why=WHY))
        # 模拟 run.py 的「加载时快照」
        for v in real.by_key.values():
            snap.add(v)

        core_real = _mk_core(tmp / "a", real)
        core_snap = _mk_core(tmp / "b", snap)

        # 本轮新增一条（`run_suite` 在跑完之后 add）
        real.add(Critique(scorer_sha=SCORER, suite_sha=SUITE, task_id="e1wi",
                          family="tasks_v8", score=0.0, why="通过 0/16；失败: set.append"))

        st_real = _state(core_real, "e2ni")
        st_snap = _state(core_snap, "e2ni")
        see_new_real = "e1wi" in st_real
        see_new_snap = "e1wi" in st_snap
        say("C-b 同对象 ⇒ 本轮新增可见（= all 语义）", see_new_real, f"含e1wi={see_new_real}")
        say("C-c 副本 ⇒ 本轮新增不可见（= prev 语义）", not see_new_snap,
            f"含e1wi={see_new_snap}")
        say("C-c' 副本仍看得到加载时的那条", "e3ni" in st_snap, f"含e3ni={'e3ni' in st_snap}")

        # 等长检验：两条 why 长度接近时，渲染块长度差必须很小
        say("C-d 快照与实时块长差（同内容应≈0）",
            abs(len(st_real) - len(st_snap)) < 200,
            f"|Δlen|={abs(len(st_real) - len(st_snap))}")
        core_real.memory.close()
        core_snap.memory.close()

    print("\n".join(out))
    print("VERDICT", "OK" if ok else "FAIL")
    (_HERE / "p0_critique.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
