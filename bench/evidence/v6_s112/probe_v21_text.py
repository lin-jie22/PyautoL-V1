#!/usr/bin/env python3
"""V21 的 P0-c 前提闸（零模型）—— 提示文本到底长什么样。

问题：换内核之后，三道恒失败的 `ni` 题**到底能不能拿到** `NameError` 那条检查项？
      拿不到 ⇒ 后面三小时的跑批测的是别的东西（PREREG §4 的 P0-c）。

⛔ 这个探针**不碰模型**：它只调真正的 `WorldModel.reward_guidance`，
   所以它给出的文本就是内核会给模型的那段（可离线复算）。

用法：
    python3 bench/evidence/v6_s112/probe_v21_text.py <memory.db> [tasks_v8.jsonl]
退出码：0 = P0-c 成立；1 = 不成立（并逐题印出实际文本）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(V1))

from mw.features import feature_set          # noqa: E402
from mw.llm import StubLLM                   # noqa: E402
from mw.memory import MemoryStore            # noqa: E402
from mw.worldmodel import WorldModel         # noqa: E402

MUST_HAVE = ("e1ni", "e2ni", "e3ni")
FORBID = "import pathlib"     # 打分器原文里的噪音；出现即说明没归类


def main(argv: list[str]) -> int:
    db = Path(argv[1] if len(argv) > 1 else "/tmp/v21/mem_b/memory.db")
    suite = Path(argv[2] if len(argv) > 2 else V1 / "bench/specgen/tasks_v8.jsonl")
    if not db.exists():
        print(f"❌ 库不存在：{db} ⇒ P0-c 不可判")
        return 1

    tasks = [json.loads(x) for x in suite.read_text(encoding="utf-8").splitlines() if x.strip()]
    wm = WorldModel(MemoryStore(db), StubLLM())
    texts: dict[str, str] = {}
    for t in tasks:
        tid = str(t["id"])
        texts[tid] = wm.reward_guidance(features=feature_set(t), task_id=tid)

    ok = True
    for tid in [str(t["id"]) for t in tasks]:
        txt = texts[tid]
        flag = ""
        if tid in MUST_HAVE:
            good = ("NameError" in txt) and (FORBID not in txt)
            flag = " ✅" if good else " ❌"
            ok = ok and good
        elif FORBID in txt:
            ok = False
            flag = " ❌ 含原文噪音"
        print(f"\n--- {tid}{flag}  长度={len(txt)}")
        for line in (txt.splitlines() or ["(空)"]):
            print("    " + line)

    print(f"\n挡成功={wm.rewards_skipped_success} 挡自己={wm.rewards_skipped_self} "
          f"读={wm.rewards_seen} 写={wm.rewards_recorded}")
    print("P0-c =", "✅ 成立（三道 ni 都拿到 NameError、且不含打分器原文噪音）" if ok else "❌ 不成立")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
