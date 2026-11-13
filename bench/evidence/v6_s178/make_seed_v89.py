#!/usr/bin/env python3
"""V89 种子库 —— 空库 / 4 条 ZK。

⛔ 四条 ZK 直接 import V87 的常量（逐字相同）。
用法：python3 bench/evidence/v6_s178/make_seed_v89.py /tmp/v89
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench/evidence/v6_s176"))

from mw.memory import MemoryStore  # noqa: E402
from make_seed_v87 import ZK  # noqa: E402  ⛔ 逐字复用


def build(root: Path) -> None:
    for tag, facts in (("E", []), ("Z", ZK)):
        d = root / "seed" / tag
        d.mkdir(parents=True, exist_ok=True)
        db = d / "memory.db"
        if db.exists():
            db.unlink()
        m = MemoryStore(db)
        for t in facts:
            m.commit_fact(t, source="abstract", confidence=0.6)
        st = m.stats()
        m.close()
        print(f"seed/{tag}: facts={st['facts']}（期望 {len(facts)}）")
        assert st["facts"] == len(facts), (tag, st, len(facts))


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v89"))
