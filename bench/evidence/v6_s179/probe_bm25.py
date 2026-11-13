#!/usr/bin/env python3
"""V90 补充量测：位序是「语义排序」还是「写入顺序兜底」？

只看**现成**的四题与四条 ZK，不造题、不造知识。库在 /tmp 重建（⛔ 不开归档库，纪律 31）。

动机：元分析 §8 显示 g7d3 的 ZK2 在 10 个块里恒排第 2（唯一排第 1 的那次是 ZK1 被删掉的臂，
分数从 0.4 跳到 1.0）。若位序来自 `facts_fts` 的 bm25，则中文 token 的贡献为 0，
排序会退化成 rowid（= 写入顺序）⇒「最早写入的那条永远排第一」。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench/evidence/v6_s176"))
from mw.memory import MemoryStore  # noqa: E402
from make_seed_v87 import ZK  # noqa: E402

TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="v90bm25_"))
    m = MemoryStore(tmp / "memory.db")
    for t in ZK:
        m.commit_fact(t, source="abstract", confidence=0.6)
    rows = [json.loads(x) for x in
            (ROOT / "bench/heldout/g7j_n4_heldout.jsonl").read_text(encoding="utf-8").splitlines()
            if x.strip()]
    print("# 库：/tmp 重建（4 条 ZK，写入顺序 = ZK1..ZK4）")
    print("# bm25 逐条（SQLite 约定：**越小越相关**；相同值 = 平局，由 rowid 兜底）\n")
    from mw.memory import fts_query
    for r in rows:
        if r["id"] not in TASKS:
            continue
        q = fts_query(r["prompt"])
        got = m.conn.execute(
            "SELECT f.id, bm25(facts_fts) s, substr(f.text,1,6) t "
            "FROM facts_fts JOIN facts f ON f.id=facts_fts.rowid "
            "WHERE facts_fts MATCH ? ORDER BY s, f.id", (q,)).fetchall()
        print(f"== {r['id']}  query={q[:80]!r}")
        for g in got:
            print(f"   id={g['id']} bm25={g['s']:+.6f}  {g['t']}")
        order = [next((f"ZK{i}" for i in (1, 2, 3, 4) if f"（ZK{i}）" in f.text), "?")
                 for f in m.recall(r["prompt"], k=4)]
        print(f"   ⇒ recall 实际顺序 = {order}\n")
    m.close()


if __name__ == "__main__":
    main()
