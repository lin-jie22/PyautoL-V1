#!/usr/bin/env python3
"""V88 前置量测（离线，不跑模型）：`V1_RECALL_K` 改小之后，每题实际收到哪几条？

为什么必须先量（纪律 3）：§156.6 写的押注 A1 假设「每题自己那条 ZK 排第 1，
所以 k=1 只留最相关的那条」。这个假设**从没被量过**。V87 的 §5 读数里
四题的标号顺序**完全相同**（`['ZK1','ZK2','ZK3','ZK4']`）—— 这已经暗示
排序与题目无关，而是 bm25 打平后按 rowid。

⛔ 纪律 31：`MemoryStore.__init__` 会 `executescript(SCHEMA)` ⇒ 打开归档库
   就是改证据。先 `shutil.copy` 到临时目录再开。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from mw.memory import MemoryStore  # noqa: E402

SRC = ROOT / "bench/evidence/v6_s176/arms/M0/memory_after.db"
SUITE = ROOT / "bench/heldout/g7j_n4_heldout.jsonl"


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="v88probe_"))
    db = tmp / "memory.db"
    shutil.copy(SRC, db)
    m = MemoryStore(db)
    rows = [json.loads(l) for l in SUITE.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"# 库：{SRC.relative_to(ROOT)}  facts={m.stats()['facts']}")
    print("# 每条 fact 的前 12 字（用来认人）")
    for f in m.conn.execute("SELECT id, substr(text,1,12) t FROM facts ORDER BY id"):
        print(f"   id={f['id']} {f['t']}")
    print()
    for r in rows:
        print(f"== {r['id']}  prompt={len(r['prompt'])} 字")
        for k in (1, 2, 3, 4, 6):
            got = m.recall(r["prompt"], k=k)
            # ⚠️ 这里要的是**位序**，不是集合 —— V87 的 `sorted(set(...))` 把顺序洗掉了。
            tags = []
            for f in got:
                hit = next((f"ZK{i}" for i in (1, 2, 3, 4) if f"（ZK{i}）" in f.text), "?")
                tags.append(hit)
            print(f"   k={k} n={len(got)} 顺序={tags} 字数={len(' ｜ '.join(x.text[:120] for x in got))}")
        print()
    m.close()
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
