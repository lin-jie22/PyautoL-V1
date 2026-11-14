#!/usr/bin/env python3
"""V93 跑前量测：R1/R2/R3 的注入块**位序与字数**。

为什么必须先量（V88 的教训）：`bm25` 位序由**词元数**定（V90），R2/R3 的文本长度
与 R1 不同 ⇒ 位序会变。V91 已证位序不是因果量，但**块字数**必须逐格记下来，
否则跑完分不清「示例有效」和「块变长了」。

⛔ 只读 /tmp 下刚建的库（不是归档库），无纪律 31 风险。
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

SEED = Path("/tmp/v93seed")
SUITE = ROOT / "bench/heldout/g7j_n4_heldout.jsonl"
ARMS = ("R0", "R1", "R2", "R3")


def tags_of(texts) -> list[str]:
    out = []
    for t in texts:
        hit = next((f"ZK{i}" for i in (1, 2, 3, 4) if f"（ZK{i}）" in t), "?")
        out.append(hit)
    return out


def main() -> None:
    rows = [json.loads(x) for x in SUITE.read_text(encoding="utf-8").splitlines() if x.strip()]
    for arm in ARMS:
        tmp = Path(tempfile.mkdtemp(prefix=f"v93p_{arm}_"))
        db = tmp / "memory.db"
        shutil.copy(SEED / arm / "memory.db", db)
        m = MemoryStore(db)
        print(f"===== {arm}  facts={m.stats()['facts']}")
        for r in rows:
            got = m.recall(r["prompt"], k=6)
            order = tags_of([f.text for f in got])
            blk = " ｜ ".join(f.text.strip() for f in got)
            print(f"  {r['id'][:18]:20s} n={len(got)} 块={len(blk):3d} 字  顺序={order}")
        m.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
