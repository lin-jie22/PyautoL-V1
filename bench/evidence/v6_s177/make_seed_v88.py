#!/usr/bin/env python3
"""V88 种子库 —— 只有库，没有臂差异（唯一变量是 env，不是库）。

⛔ 四条 ZK **直接 import** V87 的常量（逐字相同，手抄一次就是一次漂移机会）。
用法：python3 bench/evidence/v6_s177/make_seed_v88.py /tmp/v88
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench/evidence/v6_s176"))

from mw.memory import MemoryStore  # noqa: E402
from make_seed_v87 import ZK  # noqa: E402  ⛔ 逐字复用，不重抄


def build(root: Path) -> None:
    d = root / "seed"
    d.mkdir(parents=True, exist_ok=True)
    db = d / "memory.db"
    if db.exists():
        db.unlink()
    m = MemoryStore(db)
    for t in ZK:
        m.commit_fact(t, source="abstract", confidence=0.6)
    st = m.stats()
    m.close()
    print(f"seed: facts={st['facts']}（应为 4）")
    assert st["facts"] == len(ZK), (st, len(ZK))


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v88"))
