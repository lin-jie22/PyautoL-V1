#!/usr/bin/env python3
"""§17-7j 的**验收探针**：新 suite 到底可不可控？

三段读数，零模型调用（第三段用 StubLLM）：

    ① 钥匙**可控性** —— 同一份 facts，题面提 N 把钥匙就命中 N 条？
       并且：**原题面（没有钥匙）必须 0 条**。这一格是正向对照 ——
       没有它，"命中来自钥匙"就只是一句推断（可能是正文里的中文恰好又撞上了）。
    ② 钥匙是否全在 `fts_query` 的 **24-token 窗口**内 ——
       "题面提了钥匙"与"FTS 收到钥匙"是两件事，窗口外等于没提。
    ③ `[语义记忆]` 块在 n4 档**真的出现**且条数对 ——
       `_state()` 的 cap 会把低优先级块整块丢掉；若语义块被丢掉，
       "没送达"会被误读成"模型不用"，那正是 7d 读错的那条路。

⛔ 它不改判据、不进任何判定；判定权在 `tests/contract_test.py` 的 AZ26–AZ30。
⛔ 它不写库、不动证据目录（种子建在临时目录）。

用法：
    python3 bench/probe_7j.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
for _p in (str(V1), str(V1 / "bench")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if not os.environ.get("V1_STATE_DIR"):
    os.environ["V1_STATE_DIR"] = tempfile.mkdtemp(prefix="v1_7j_probe_")

from mw.abstractor import ABSTRACT_SOURCE                    # noqa: E402
from mw.core import Core                                     # noqa: E402
from mw.llm import StubLLM                                   # noqa: E402
from mw.memory import MemoryStore                            # noqa: E402
from build_g7d_seed import VARIANTS, KEYS_7J                 # noqa: E402
from build_g7j_heldout import (                              # noqa: E402
    KEYS, query_tokens, _TOKEN,
)

HELD = V1 / "bench" / "heldout"
SOURCES = {
    "原题面（无钥匙）": HELD / "g7e_hint_heldout.jsonl",
    "n0（只提目标钥匙）": HELD / "g7j_n0_heldout.jsonl",
    "n4（提全部四把）": HELD / "g7j_n4_heldout.jsonl",
}
EXPECT = {"原题面（无钥匙）": None, "n0（只提目标钥匙）": 1, "n4（提全部四把）": 4}


def _load(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def _seed(facts: dict[str, str]) -> Path:
    p = Path(tempfile.mkdtemp(prefix="v1_7j_seed_")) / "memory.db"
    m = MemoryStore(p)
    for v in facts.values():
        m.commit_fact(v, source=ABSTRACT_SOURCE, confidence=0.5)
    m.close()
    return p


def _semantic_block(state: str) -> str:
    """从 state 里取出 `[语义记忆]` 那一行（没有则返回空串）。"""
    for line in state.splitlines():
        if line.startswith("[语义记忆]"):
            return line[len("[语义记忆]"):].strip()
    return ""


def part1_and_2() -> bool:
    print("① 钥匙可控性 + ② token 窗口（零模型调用）")
    db = _seed(VARIANTS["7j"])
    mem = MemoryStore(db)
    facts = VARIANTS["7j"]
    all_ok = True
    for label, path in SOURCES.items():
        rows = _load(path)
        print(f"\n  ── {label}（期望 {EXPECT[label] if EXPECT[label] is not None else '0/0/1/1（中文零交集）'} 条）──")
        for t in rows:
            tid = t["id"]
            got = [f.text for f in mem.recall(t["prompt"], k=6)]
            own = facts[tid]
            # 原题面的期望是"逐题看中文能不能撞上"——7i 已实测 0/0/1/1，
            # 所以这里只如实打印，不参与 ok 判定（判定在 AZ26 的正向对照里）。
            want = EXPECT[label]
            if want is None:
                ok = True
                note = "（对照：只登记）"
            else:
                ok = (len(got) == want) and (own in got)
                note = ""
            # ② 窗口
            toks = query_tokens(t["prompt"])
            n_all = len(_TOKEN.findall(t["prompt"]))
            keys_in = [k for k in KEYS_7J.values() if k in t["prompt"]]
            miss = [k for k in keys_in if k not in toks]
            win_ok = not miss
            all_ok &= ok and win_ok
            print(f"    {'✅' if ok and win_ok else '❌'} {tid:24s}"
                  f" 命中={len(got)}条 本条={'✅' if own in got else '❌'}"
                  f" token={n_all}→窗口{len(toks)}"
                  f" 钥匙={keys_in or '无'}"
                  f"{'  窗口外=' + ','.join(miss) if miss else ''}{note}")
    mem.close()
    return all_ok


def part3() -> bool:
    print("\n③ `[语义记忆]` 块在 n4 档真的出现（StubLLM，零模型调用）")
    rows = _load(HELD / "g7j_n4_heldout.jsonl")
    t = next(r for r in rows if r["id"] == "g7d2_dedupe_keep")
    db = _seed(VARIANTS["7j"])
    mem = MemoryStore(db)
    core = Core(llm=StubLLM(), memory=mem, memory_mode="raw")
    core.run(t["prompt"], task_id=t["id"])
    steps = core.state_log
    ok = True
    if not steps:
        print("    ❌ 一步都没跑（state_log 为空）")
        ok = False
    else:
        _it, st = steps[0]
        block = _semantic_block(st)
        n = 0 if not block else len(block.split(" ｜ "))
        # ⚠️ 每条形如 `（ZKx）……`：数钥匙出现次数比数分隔符更稳
        n_keys = len(re.findall(r"（ZK\d）", block))
        ok = block != "" and n_keys == 4 and n == 4
        print(f"    第 1 步：[语义记忆] {'存在' if block else '❌ 不存在'}"
              f"  条数={n}（钥匙 {n_keys} 个）  块长={len(block)} 字")
        print(f"    步数={len(steps)}  实际拼给模型的 state 长度={len(st)} 字")
        if not block:
            print("    ⚠️ 语义块被 cap 整块丢掉了 ⇒ 这不是'模型不用'，是'没送达'")
    mem.close()
    return ok


def main() -> int:
    print("=" * 74)
    a = part1_and_2()
    b = part3()
    print("\n" + "=" * 74)
    print(f"  ①+② {'✅' if a else '❌'}   ③ {'✅' if b else '❌'}")
    print("  ⚠️ 探针只报告读数；判据在 tests/contract_test.py 的 AZ26–AZ30。")
    print("=" * 74)
    return 0 if (a and b) else 1


if __name__ == "__main__":
    raise SystemExit(main())
