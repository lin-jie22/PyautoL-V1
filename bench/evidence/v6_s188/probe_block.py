#!/usr/bin/env python3
"""V97 跑前量测：四臂注入块的位序与字数（应与 V96 的 W2 完全相同）。"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from mw.memory import MemoryStore  # noqa: E402

SEED = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v99/seeds")
SUITE = ROOT / "bench/heldout/g7j_n4_heldout.jsonl"
ARMS = ("O0","O1","O2","O3","O4","O5","O6","O7","O8")


def tag_of(text: str) -> str:
    return next((f"ZK{i}" for i in (1, 2, 3, 4) if f"ZK{i}" in text[:6]), "?")


def main() -> None:
    rows = [json.loads(x) for x in SUITE.read_text(encoding="utf-8").splitlines() if x.strip()]
    for arm in ARMS:
        src = SEED / arm / "memory.db"
        if not src.exists():
            print(f"!! {arm} 缺库")
            continue
        tmp = Path(tempfile.mkdtemp(prefix=f"v97p_{arm}_"))
        db = tmp / "memory.db"
        shutil.copy(src, db)          # ⛔ 纪律 31
        m = MemoryStore(db)
        print(f"===== {arm}  facts={m.stats()['facts']}")
        for r in rows:
            got = m.recall(r["prompt"], k=6)
            order = [tag_of(f.text) for f in got]
            blk = " ｜ ".join(f.text.strip() for f in got)
            print(f"  {r['id'][:18]:20s} n={len(got)} 块={len(blk):3d} 字  顺序={order}")
        m.close()
        shutil.rmtree(tmp, ignore_errors=True)
        print()


if __name__ == "__main__":
    main()
