#!/usr/bin/env python3
"""V87 留一法种子库 —— M0 全给，M1~M4 各删一条。

⛔ 四条 ZK 与 `bench/evidence/v6_s174/make_seed_v85.py` **逐字相同**（换一个字就与 V85 不可比）。
⛔ 起跑库只差「少了哪一条」——这是本轮唯一变量。
用法：python3 bench/evidence/v6_s176/make_seed_v87.py /tmp/v87
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.memory import MemoryStore  # noqa: E402

# ⛔ 逐字取自 v6_s174/make_seed_v85.py 的 ZK
ZK = [
    "（ZK1）去重惯例：比较前先去掉首尾空白并转小写，遇到重复时保留第一次出现的那条原文。",
    "（ZK2）金额惯例：千分位用空格分隔，保留两位小数，负数用圆括号包裹、不写负号。",
    "（ZK3）开关惯例：只把 y/yes/1 当成真，'true'、'on' 这类历史脏值一律算假，其余无法识别的返回 None。",
    "（ZK4）截断惯例：超长时保留最后 n 个字符，前面加一个省略号字符 '…'（U+2026），不超长时原样返回。",
]

#: 臂 → 删掉第几条（None = 不删）
ARMS: dict[str, int | None] = {"M0": None, "M1": 0, "M2": 1, "M3": 2, "M4": 3}


def build(root: Path) -> None:
    for tag, drop in ARMS.items():
        d = root / tag
        d.mkdir(parents=True, exist_ok=True)
        db = d / "memory.db"
        if db.exists():
            db.unlink()
        m = MemoryStore(db)
        kept = []
        for i, t in enumerate(ZK):
            if drop is not None and i == drop:
                continue
            m.commit_fact(t, source="abstract", confidence=0.6)
            kept.append(f"ZK{i + 1}")
        st = m.stats()
        # 送达自检：每题必须命中 len(kept) 条（删的一条消失，其余全部送达）
        tasks = [l for l in (ROOT / "bench/heldout/g7j_n4_heldout.jsonl").read_text(
            encoding="utf-8").splitlines() if l.strip()]
        import json as _json
        hits = {}
        for line in tasks:
            t = _json.loads(line)
            hits[t["id"]] = len(m.recall(t["prompt"], k=4))
        m.close()
        want = len(kept)
        print(f"{tag}: drop={drop} facts={st['facts']} kept={kept} hits={hits}")
        assert st["facts"] == want, (tag, st, want)
        assert all(v == want for v in hits.values()), (tag, hits, want)


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v87"))
