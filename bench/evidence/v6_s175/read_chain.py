#!/usr/bin/env python3
"""读 V86 两条**累积链**的逐跑读数（真读数，不采信日志标签）。

每格取四个来源，全部是恒存在的产物：
  · `bench/runs.jsonl`  → 逐题分数、`kernel_worldmodel.rewards_*` 三个计数器
  · `state_dump.jsonl`  → `[语义记忆]` 块字数（知识边送达）
                        + 顶层 `guidance` 字段字数与原文（奖励边送达，纪律 14）
  · `memory_after.db`   → facts 条数 / 按 source / reward episodes 条数（**副本**，纪律 31）
  · `out.txt`           → 退出码与耗时（只作对账，不进判决）

用法：python3 bench/evidence/v6_s175/read_chain.py [/tmp/v86]
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v86")
TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]
CHAINS = ["B", "A"]


def db_reads(db: Path) -> dict:
    """⛔ 先 copy 再开：`MemoryStore.__init__` 会 executescript(SCHEMA)（V83 踩过）。"""
    if not db.exists():
        return {}
    tmp = Path(tempfile.mkdtemp()) / "snap.db"
    shutil.copy(db, tmp)
    c = sqlite3.connect(str(tmp))
    c.row_factory = sqlite3.Row
    facts = c.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    by_src = {r["source"]: r["c"] for r in c.execute(
        "SELECT source, COUNT(*) c FROM facts GROUP BY source")}
    rew = c.execute("SELECT COUNT(*) FROM episodes WHERE kind='reward'").fetchone()[0]
    eps = c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    c.close()
    shutil.rmtree(tmp.parent, ignore_errors=True)
    return {"facts": facts, "by_source": by_src, "reward_eps": rew, "episodes": eps}


def main() -> int:
    rows = []
    for chain in CHAINS:
        dirs = [p for p in sorted((ROOT / chain).glob("*"))
                if (p / "bench/runs.jsonl").exists()]
        for d in dirs:
            rj = d / "bench/runs.jsonl"
            if not rj.exists():
                continue
            rec = json.loads(rj.read_text(encoding="utf-8").splitlines()[0])
            per = {}
            for t in (rec.get("report") or {}).get("per_task") or []:
                per[t["id"]] = t.get("score")
            wm = (rec.get("kernel_worldmodel") or {})
            blocks = []
            guids: list[int] = []
            gtext = ""
            sd = d / "state_dump.jsonl"
            if sd.exists():
                for line in sd.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    s = row.get("state", "")
                    m = re.search(r"\[语义记忆\](.*?)(?=\n\[|\Z)", s, re.S)
                    blocks.append(len(m.group(1).strip()) if m else 0)
                    # 顶层 `guidance`：`core.py:1174` 每轮算一次并落进 `state_meta`
                    # ⇒ 这是「本轮实际收到的奖励提示」的**原文**，不是计数器推导。
                    g = (row.get("guidance") or "").strip()
                    guids.append(len(g))
                    if g and not gtext:
                        gtext = g
            rows.append({
                "chain": chain, "run": d.name,
                "per_task": per,
                "full": sum(1 for t in TASKS if (per.get(t) or 0) >= 0.999),
                "state_mem_chars": blocks,
                "guidance_chars": guids,
                "guidance_sample": gtext[:200],
                "rewards_seen": wm.get("rewards_seen"),
                "skipped_self": wm.get("rewards_skipped_self"),
                "skipped_success": wm.get("rewards_skipped_success"),
                "rewards_recorded": wm.get("rewards_recorded"),
                **db_reads(next((p for p in (d / "memory_after.db", d / "mem/memory.db")
                                 if p.exists()), d / "memory_after.db")),
            })

    print("| 链/跑 | " + " | ".join(t[:10] for t in TASKS)
          + " | 满分 | 知识块字数 | 奖励提示字数 | rewards_seen/self/succ | 库 facts / reward_eps |")
    print("|---" * (len(TASKS) + 6) + "|")
    for r in rows:
        sc = [r["per_task"].get(t) for t in TASKS]
        print(f"| {r['chain']}/{r['run']} | "
              + " | ".join("—" if s is None else f"{s:g}" for s in sc)
              + f" | **{r['full']}/4** | {r['state_mem_chars']} "
              + f"| {r['guidance_chars']} "
              + f"| {r['rewards_seen']}/{r['skipped_self']}/{r['skipped_success']} "
              + f"| {r.get('facts')} / {r.get('reward_eps')} |")
    print()
    for chain in CHAINS:
        sel = [r for r in rows if r["chain"] == chain]
        if not sel:
            continue
        print(f"链 {chain}：满分 {' → '.join(str(r['full']) for r in sel)}"
              f"  ｜ facts {' → '.join(str(r.get('facts')) for r in sel)}"
              f"  ｜ rewards_seen {' → '.join(str(r['rewards_seen']) for r in sel)}")
        print(f"        by_source 逐跑 {[r.get('by_source') for r in sel]}")
        print(f"        奖励提示字数逐跑 {[sum(r['guidance_chars']) for r in sel]}"
              f"（全为 0 ⇒ 奖励边没通电，P3b 判「没通」而非「没效果」）")
        for r in sel:
            if r.get("guidance_sample"):
                print(f"        {r['run']} 首次非空原文：{r['guidance_sample'][:110]}")
    (ROOT / "chain_readings.json").write_text(
        json.dumps({"tasks": TASKS, "rows": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"\n落盘：{ROOT / 'chain_readings.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
