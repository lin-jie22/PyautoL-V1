#!/usr/bin/env python3
"""V94 跑前量测：三臂注入块的位序与字数。

⭐ 重点看块字数：C1/C3 约 376 字，接近 V91 的 L2 臂（387 字，g7e1 曾掉到 0.4）。
   若本轮 C1 掉分，必须先看 C3 是否同样掉 —— 那就能把「长度」与「内容」分开。

用法：python3 probe_block.py [seed_root]
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

SEED = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v94seed")
SUITE = ROOT / "bench/heldout/g7j_n4_heldout.jsonl"
ARMS = ("C1", "C2", "C3")


def tag_of(text: str) -> str:
    return next((f"ZK{i}" for i in (1, 2, 3, 4) if f"（ZK{i}）" in text), "?")


def main() -> None:
    rows = [json.loads(x) for x in SUITE.read_text(encoding="utf-8").splitlines() if x.strip()]
    for arm in ARMS:
        src = SEED / arm / "memory.db"
        if not src.exists():
            print(f"!! {arm} 缺库")
            continue
        tmp = Path(tempfile.mkdtemp(prefix=f"v94p_{arm}_"))
        db = tmp / "memory.db"
        shutil.copy(src, db)
        m = MemoryStore(db)
        print(f"===== {arm}  facts={m.stats()['facts']}")
        for r in rows:
            got = m.recall(r["prompt"], k=6)
            order = [tag_of(f.text) for f in got]
            blk = " ｜ ".join(f.text.strip() for f in got)
            trunc = sum(1 for f in got if len(f.text) > 120)
            print(f"  {r['id'][:18]:20s} n={len(got)} 块={len(blk):3d} 字  顺序={order}"
                  + (f"  ⚠️{trunc} 条超 120 会截断" if trunc else ""))
        m.close()
        shutil.rmtree(tmp, ignore_errors=True)
        print()


if __name__ == "__main__":
    main()
