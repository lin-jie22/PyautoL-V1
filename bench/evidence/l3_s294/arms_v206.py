#!/usr/bin/env python3
"""V206：放宽「空」的长度闸，收益与代价同时量。

两档 MIN_ABSTRACT_CHARS ∈ {8, 4}，两个池子：
  收益池  /tmp/v205/same4/memory.db   19 条（含 2 条失败 reward）
  代价池  state/memory.db 的门控 108 条（V204 口径）

⛔ 不改源码：monkeypatch `mw.abstractor.MIN_ABSTRACT_CHARS`。
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

GAIN_DB = Path("/tmp/v205/same4/memory.db")
SRC_DB = ROOT / "state" / "memory.db"
TMP = Path("/tmp/v206")

# 预注册冻结的结构词表（宽松口径 ⇒ 空话率是下界）
STRUCT_WORDS = ("空", "None", "空串", "重叠", "端点", "区间", "顺序", "首次", "逗号",
                "千分位", "次数", "升序", "游程", "编码", "元组", "tuple", "不可变",
                "括号", "分隔符", "递归", "基线")

_PLACEHOLDER = re.compile(r"^\(语义记忆中无相关条目\)$|^stalled: \{\}$")
_MISS_DEF = re.compile(r"^收尾交付未过自检 — \d+/\d+ 项未过：缺少 def \w+\(\.\.\.\)$")
_PURE_ANSWER = re.compile(r"^(closed|done):\s*```")
_HAS_EVID = re.compile(r"⇒|\bassert\b|编译失败|Traceback|\bError\b|Exception")


def gate(content: str):
    t = (content or "").strip()
    if _PLACEHOLDER.match(t) or _MISS_DEF.match(t) or _PURE_ANSWER.match(t):
        return None
    if len(t) < 40 and not _HAS_EVID.search(t):
        return None
    return t


def gated_episodes(db: Path) -> list[dict]:
    import sqlite3
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    eps = [dict(r) for r in c.execute(
        "SELECT id, kind, content FROM episodes ORDER BY id DESC LIMIT 500")]
    c.close()
    eps.sort(key=lambda e: (0 if e["kind"] == "failure" else 1, -e["id"]))
    kept, seen = [], set()
    for e in eps:
        t = gate(e["content"])
        if not t or t[:100] in seen:
            continue
        seen.add(t[:100])
        kept.append(e)
    return kept


def run(pool_name: str, eps: list[dict], min_chars: int) -> dict:
    from mw import config as cfg
    from mw.llm import build_llm
    from mw.memory import MemoryStore
    import mw.abstractor as ab

    ab.MIN_ABSTRACT_CHARS = min_chars          # monkeypatch
    assert ab.MIN_ABSTRACT_CHARS == min_chars, "monkeypatch 没生效"

    d = TMP / f"{pool_name}_{min_chars}"
    d.mkdir(parents=True, exist_ok=True)
    db = d / "memory.db"
    if db.exists():
        db.unlink()
    # 只把需要的 episode 建成新库（不复制整库，避免把 reward 之外的杂质带进来）
    with MemoryStore(db) as mem:
        for e in eps:
            mem.record(e["kind"], e["content"])
        st = ab.distill(mem, build_llm(cfg.LLMConfig()), max_tokens=160)
        # distill 后再读一次：此时 MIN_ABSTRACT_CHARS 仍是 monkeypatch 值
        rows = [r[0] for r in mem.conn.execute(
            "SELECT text FROM facts WHERE source=? ORDER BY id", (ab.ABSTRACT_SOURCE,))]
    return {"pool": pool_name, "min_chars": min_chars, "n_eps": len(eps),
            "stats": st.as_dict(), "texts": rows}


def main() -> int:
    TMP.mkdir(parents=True, exist_ok=True)
    if not GAIN_DB.exists():
        print(f"⛔ 收益池不存在：{GAIN_DB}")
        return 2

    gain_eps = [{"id": r["id"], "kind": r["kind"], "content": r["content"]}
                for r in _read(GAIN_DB)]
    cost_eps = gated_episodes(SRC_DB)
    print(f"[自检] 收益池 {len(gain_eps)} 条 / 代价池 {len(cost_eps)} 条（预注册 108）")

    res = {}
    for name, eps in (("gain", gain_eps), ("cost", cost_eps)):
        for mc in (8, 4):
            r = run(name, eps, mc)
            res[f"{name}_{mc}"] = r
            print(f"  [{name} MIN={mc}] eps={r['n_eps']} kept={r['stats']['kept']} "
                  f"empty={r['stats']['dropped_empty']} 新增行={len(r['texts'])}")

    print("\n=== 收益侧：闸=8 vs 闸=4 ===")
    t8 = set(res["gain_8"]["texts"])
    t4 = set(res["gain_4"]["texts"])
    print(f"  闸8 有 {len(t8)} 条：{sorted(t8)}")
    print(f"  闸4 有 {len(t4)} 条：{sorted(t4)}")
    print(f"  ⭐ 闸4 新增：{sorted(t4 - t8)}")

    print("\n=== 代价侧：闸=8 vs 闸=4 ===")
    c8 = set(res["cost_8"]["texts"])
    c4 = set(res["cost_4"]["texts"])
    new = sorted(c4 - c8)
    hollow = [t for t in new if not any(w in t for w in STRUCT_WORDS)]
    print(f"  闸8 {len(c8)} 条 / 闸4 {len(c4)} 条 ⇒ 新增 {len(new)} 条")
    print(f"  新增里空话 {len(hollow)} 条 = {len(hollow)/max(1,len(new)):.0%}")
    for t in new:
        print(f"    {'空话' if t in hollow else '有料'} | {t}")

    res["summary"] = {
        "gain_new": sorted(t4 - t8), "cost_new": new,
        "cost_new_hollow": hollow,
        "cost_new_hollow_rate": round(len(hollow) / max(1, len(new)), 4),
        "struct_words": list(STRUCT_WORDS),
    }
    (OUT / "readings_v206.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ {OUT/'readings_v206.json'}")
    return 0


def _read(db: Path):
    import sqlite3
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute("SELECT id, kind, content FROM episodes ORDER BY id")]
    c.close()
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
