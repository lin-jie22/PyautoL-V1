#!/usr/bin/env python3
"""V83 复算：§2.2（真实跑次库上的送达 0/4）与 §2.4（换读法的噪音量）。

⛔⛔ 这一支必须**先复制再打开**：`MemoryStore.__init__` 会 `executescript(SCHEMA)`，
   而 S2 候选还要往库里建 `facts_fts_char` —— 直接打开归档证据库 = **改了证据**。
   V83 第一版就是这么踩的（`final_memory.db` 多出 `facts_fts_char*` 六张表、
   还顺手用 `sqlite3.connect` 造了个 0 字节的 `seed_memory.db`），靠 `git checkout` 复原。
   ⇒ 归档只读，一律 `shutil.copy` 到临时目录再开。

用法：python3 bench/evidence/v6_s172/repro_readings.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from bench.probe_mem_channel import (                        # noqa: E402
    build_char_fts, s1_bigram, s2_char_phrase)
from mw.memory import MemoryStore                            # noqa: E402

ARCHIVE = ROOT / "bench/evidence/v6_s159/final_memory.db"
SUITE = ROOT / "bench/heldout/struct_convert_heldout.jsonl"


def _copy() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="v83repro_")) / "memory.db"
    shutil.copy(ARCHIVE, tmp)
    return tmp


def main() -> int:
    tasks = [json.loads(l) for l in SUITE.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    print(f"归档 {ARCHIVE.relative_to(ROOT)}（复制后打开）")

    print("\n=== §2.2 现状读端在真实跑次库上的送达 ===")
    m = MemoryStore(_copy())
    print(f"  facts = {m.stats()['facts']}（全部来源 = {m.stats()['by_source']}）")
    hit = 0
    for t in tasks:
        n = len(m.recall(t["prompt"], k=6))
        print(f"  {t['id']:20s} recall={n}")
        hit += bool(n)
    print(f"  ⇒ 送达 {hit}/4")
    m.close()

    print("\n=== §2.4 换成 S1/S2 后的噪音注入（同一库）===")
    m2 = MemoryStore(_copy())
    build_char_fts(m2.conn)
    for t in tasks:
        a = len(s1_bigram(m2, t["prompt"]))
        b = len(s2_char_phrase(m2, t["prompt"]))
        print(f"  {t['id']:20s} S0={len(m2.recall(t['prompt'], k=6))}  "
              f"S1={a}  S2={b}  (库里共 {m2.stats()['facts']} 条)")
    m2.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
