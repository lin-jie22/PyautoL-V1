#!/usr/bin/env python3
"""V96 跑前量测：六臂注入块的位序与字数。

⭐ 为什么必须先量：V90 §159 说位序由**该条事实的词元数**决定（`bm25` 常数），
   而六个变体的字元数/词元数差别很大（W1 33 字 → W5 117 字）。
   ⛔ 但 V91 §160 已双向否证「位序是因果量」，所以位序变化**不是**替代解释；
   这里落出来只为了事后能说「哪一格换了位序、结果动没动」。

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

SEED = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v96/seeds")
SUITE = ROOT / "bench/heldout/g7j_n4_heldout.jsonl"
ARMS = ("W1", "W2", "W3", "W4", "W5", "W6")


def tag_of(text: str) -> str:
    return next((f"ZK{i}" for i in (1, 2, 3, 4) if f"（ZK{i}）" in text), "?")


def main() -> None:
    rows = [json.loads(x) for x in SUITE.read_text(encoding="utf-8").splitlines() if x.strip()]
    for arm in ARMS:
        src = SEED / arm / "memory.db"
        if not src.exists():
            print(f"!! {arm} 缺库")
            continue
        tmp = Path(tempfile.mkdtemp(prefix=f"v96p_{arm}_"))
        db = tmp / "memory.db"
        shutil.copy(src, db)          # ⛔ 纪律 31：绝不直接打开播种库
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
