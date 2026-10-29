#!/usr/bin/env python3
"""P0 取证探针 —— **零模型**，只读库 + 直接调用检索函数。

为什么需要它：预注册 §5 原写「从日志读 `奖励[… 读=N]`」，但
`run.py:1289-1305` 只在 `_kw["predictions"]` 非空时才打印那一行 ——
`predictions=0` 时整段不打印 ⇒ 日志里**根本没有**这个字段。
⇒ 按纪律（预注册写的取证方式，先去装置里确认它存在），改成这个探针：
它直接问「**这道题现在能不能检索到别的题的历史得分**」，
答案只取决于库内容 + 特征，与模型/日志格式无关，**可离线复算**。

用法：
    python probe_reward_wire.py /tmp/v19/mem_b/memory.db bench/specgen/tasks_v8.jsonl
输出（每行一个机器可读字段）：
    REWARD_ROWS=<n>
    RETRIEVABLE=<可检索题数>/<总题数>
    SELF_SKIPPED=<n>
    DETAIL: 题号 命中数 首条摘要
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(V1))

from mw.features import feature_set            # noqa: E402
from mw.llm import StubLLM                     # noqa: E402
from mw.memory import MemoryStore              # noqa: E402
from mw.worldmodel import WorldModel           # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    db, suite = Path(argv[1]), Path(argv[2])
    if not db.exists():
        print(f"❌ 库不存在：{db}")
        return 2

    print(f"DB={db}  size={db.stat().st_size}")

    # ① 库里有几条 reward（直接 SQL，绕开任何封装）
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        n_reward = con.execute(
            "select count(*) from episodes where kind='reward'").fetchone()[0]
        kinds = con.execute(
            "select kind, count(*) from episodes group by kind order by 2 desc").fetchall()
    finally:
        con.close()
    print(f"REWARD_ROWS={n_reward}")
    print("KINDS=" + " ".join(f"{k}:{c}" for k, c in kinds))

    # ② 逐题问「现在能不能检索到别人的历史得分」
    wm = WorldModel(MemoryStore(db), StubLLM())
    tasks = [json.loads(x) for x in suite.read_text(encoding="utf-8").splitlines() if x.strip()]
    ok = 0
    for t in tasks:
        tid = str(t["id"])
        f = feature_set(t)
        g = wm.reward_guidance(features=f, task_id=tid, k=3)
        n = g.count("\n- ") + (1 if g.startswith("历史同类题") and "\n- " not in g else 0)
        if g:
            ok += 1
            first = g.split("\n")[1][:110] if "\n" in g else g[:110]
            print(f"DETAIL: {tid:<7} 命中={n}  {first}")
        else:
            print(f"DETAIL: {tid:<7} 命中=0  (空)")
    print(f"RETRIEVABLE={ok}/{len(tasks)}")
    print(f"SELF_SKIPPED={wm.rewards_skipped_self}")
    print(f"REWARDS_SEEN_COUNTER={wm.rewards_seen}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
