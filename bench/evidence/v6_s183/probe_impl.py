#!/usr/bin/env python3
"""V94 机制：逐字节看 C1/C2/C3 在 g7e2（truncate_tail）上的实现。

⛔ 纪律 31：`MemoryStore.__init__` 会 `executescript(SCHEMA)` ⇒ 打开归档库就是改证据。
   一律 `shutil.copy` 到临时目录再开。
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

RUN = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v94")
ARMS = ("C1a", "C2a", "C3a")
WANT = ("truncate_tail",)


def dump(arm: str) -> None:
    src = RUN / arm / "memory_after.db"
    if not src.exists():
        print(f"!! {arm} 无 memory_after.db")
        return
    tmp = Path(tempfile.mkdtemp(prefix="v94m_")) / "m.db"
    shutil.copy(src, tmp)
    c = sqlite3.connect(str(tmp))
    c.row_factory = sqlite3.Row
    print("=" * 78)
    print(f"### {arm}")
    for tb in [x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]:
        cols = [x[1] for x in c.execute(f"PRAGMA table_info({tb})")]
        if "text" not in cols:
            continue
        for row in c.execute(f"SELECT rowid AS rid, text AS v FROM {tb}"):
            v = row["v"]
            if not isinstance(v, str):
                continue
            for w in WANT:
                if f"def {w}" in v:
                    print(f"\n--- {tb} rowid={row['rid']} ---")
                    print(v[:600])
    c.close()


if __name__ == "__main__":
    for a in ARMS:
        dump(a)
