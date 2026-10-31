#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V27 预置：只把 `blobs` 的自增行号往前推 N 行，不写任何别的东西。

⛔ 不写 facts / episodes / reward —— 本轮的操纵变量**只有** `blobs.lastrowid`。
用法：seed_blobs.py <N> <memory.db 路径>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from mw.memory import MemoryStore      # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("用法：seed_blobs.py <N> <memory.db>")
        return 2
    n = int(argv[1])
    db = Path(argv[2])
    db.parent.mkdir(parents=True, exist_ok=True)
    st = MemoryStore(str(db))
    for i in range(n):
        h = st.capture(f"dummy-{i}", kind="preflight")
        assert h.ref.startswith("b#"), h.ref
    row = st.conn.execute("SELECT MAX(id) m, COUNT(*) c FROM blobs").fetchone()
    facts = st.conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"]
    eps = st.conn.execute("SELECT COUNT(*) c FROM episodes").fetchone()["c"]
    st.close()
    print(f"预置完成：blobs max_id={row['m']} count={row['c']} | facts={facts} episodes={eps}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
