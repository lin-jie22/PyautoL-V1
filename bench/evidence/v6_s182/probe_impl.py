#!/usr/bin/env python3
"""V93 机制：逐字节看 R1/R2/R3 在 g7d2 与 g7d3 上的实现。

⛔ 纪律 31：`MemoryStore.__init__` 会 `executescript(SCHEMA)` ⇒ 打开归档库就是改证据。
   一律 `shutil.copy` 到临时目录再开。
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUN = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v93")
ARMS = ("R1a", "R2a", "R3a")
WANT = ("dedupe_keep", "fmt_amount")


def dump(arm: str) -> None:
    src = RUN / arm / "memory_after.db"
    if not src.exists():
        print(f"!! {arm} 无 memory_after.db")
        return
    tmp = Path(tempfile.mkdtemp(prefix="v93m_")) / "m.db"
    shutil.copy(src, tmp)
    c = sqlite3.connect(str(tmp))
    c.row_factory = sqlite3.Row
    print("=" * 78)
    print(f"### {arm}  （facts={c.execute('SELECT COUNT(*) n FROM facts').fetchone()['n']}）")
    for r in c.execute("SELECT id, source, length(text) L, substr(text,1,40) t FROM facts ORDER BY id"):
        print(f"   fact id={r['id']} src={r['source']:10s} L={r['L']:3d} {r['t']!r}")

    # blobs / episodes 里找实现
    tables = [x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    hit = False
    for tb in tables:
        cols = [x[1] for x in c.execute(f"PRAGMA table_info({tb})")]
        text_cols = [x for x in cols if x in ("text", "body", "content", "payload", "value", "data")]
        if not text_cols:
            continue
        for col in text_cols:
            try:
                rows = c.execute(f"SELECT rowid AS rid, {col} v FROM {tb}").fetchall()
            except Exception:
                continue
            for row in rows:
                v = row["v"]
                if not isinstance(v, str):
                    continue
                for w in WANT:
                    if f"def {w}" in v:
                        hit = True
                        print(f"\n--- {tb}.{col} rowid={row['rid']}  def {w} ---")
                        print(v[:700])
    if not hit:
        print("\n   （没在库里找到 `def <fn>`；实现可能只存在于 state_dump 之外的日志里）")
    c.close()


def main() -> None:
    for a in ARMS:
        dump(a)


if __name__ == "__main__":
    main()
