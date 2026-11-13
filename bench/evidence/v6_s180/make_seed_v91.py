#!/usr/bin/env python3
"""V91 三份种子库（唯一变量 = 起跑库里的事实文本）。

  seeds/L0/memory.db  ZK×4 原样
  seeds/L1/memory.db  只 ZK1 +2 句（+44 字）
  seeds/L2/memory.db  四条各 +2 句（+176 字，安慰剂：位序不动）

⛔ 四条 ZK **直接 import** V87 的常量（逐字相同，手抄一次就是一次漂移机会）。
⛔ 填充句与 V90 `probe_len.py` 逐字相同 —— 否则「+44 字」这个数就不对应上一轮的读数。
用法：python3 bench/evidence/v6_s180/make_seed_v91.py /tmp/v91
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench/evidence/v6_s176"))

from mw.memory import MemoryStore  # noqa: E402
from make_seed_v87 import ZK  # noqa: E402  ⛔ 逐字复用，不重抄

#: 与 `v6_s179/probe_len.py` 的 FILL 逐字相同
FILL = "这条惯例长期有效，遇到同类问题一律照此处理。"


def texts(arm: str) -> list[str]:
    if arm == "L0":
        return list(ZK)
    if arm == "L1":
        return [ZK[0] + FILL * 2] + list(ZK[1:])
    if arm == "L2":
        return [x + FILL * 2 for x in ZK]
    raise SystemExit(f"未知臂 {arm}")


def build(root: Path) -> None:
    for arm in ("L0", "L1", "L2"):
        d = root / "seeds" / arm
        d.mkdir(parents=True, exist_ok=True)
        db = d / "memory.db"
        if db.exists():
            db.unlink()
        m = MemoryStore(db)
        ts = texts(arm)
        for t in ts:
            m.commit_fact(t, source="abstract", confidence=0.6)
        st = m.stats()
        m.close()
        total = sum(len(t) for t in ts)
        print(f"seed {arm}: facts={st['facts']}（应为 4）  事实总字数={total}")
        assert st["facts"] == len(ts), (st, len(ts))
    #: 三份库必须真的不同 —— ⛔ 不能靠「文件 sha 不同」证明：
    #:   元数据里有时间戳 ⇒ 三份库 sha 天然不同，那是假绿（纪律 5）。
    #:   只比**库里的事实原文**。
    seen = {}
    for arm in ("L0", "L1", "L2"):
        m = MemoryStore(root / "seeds" / arm / "memory.db")
        seen[arm] = tuple(r["text"] for r in
                          m.conn.execute("SELECT text FROM facts ORDER BY id"))
        m.close()
    print("事实原文指纹:",
          {a: __import__("hashlib").sha256("".join(v).encode()).hexdigest()[:12]
           for a, v in seen.items()})
    assert seen["L0"] != seen["L1"] and seen["L0"] != seen["L2"] and seen["L1"] != seen["L2"], \
        "三份库事实原文有重复 ⇒ 臂没造出来"
    assert len(seen["L0"][0]) == 43 and len(seen["L1"][0]) == 87 and len(seen["L2"][0]) == 87, \
        f"ZK1 长度不对：{[len(seen[a][0]) for a in ('L0','L1','L2')]}"


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v91"))
