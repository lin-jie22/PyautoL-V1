#!/usr/bin/env python3
"""V89 前置量测：`recall` 的**召回窗口**只有题面前 24 个 token。

`fts_query()`（`mw/memory.py:103`）是 `" OR ".join(f'"{t}"' for t in toks[:24])`
—— 第 25 个 token 起**根本不进查询**。`LIKE` 回退也只看 `normalize(query)[:64]`（前 64 字）。
这两级都只看题面**开头**。

为什么要量：V83 结论是「送达 = 字面 token 相等」，V88 结论是「位序由 bm25 定」。
两条都没问过 **「有没有资格进入查询」**。这一层在上游。

用法：python3 bench/evidence/v6_s178/probe_window.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from mw.memory import MemoryStore, _TOKEN, fts_query  # noqa: E402

SRC = ROOT / "bench/evidence/v6_s176/arms/M0/memory_after.db"
SUITES = ["bench/heldout/g7j_n4_heldout.jsonl", "bench/heldout/struct_convert_heldout.jsonl"]
#: 在 ZK 文本里真实出现的「钥匙」token（用来判断这题的 query 有没有资格碰到某条事实）
KEYS = {"None", "true", "True", "false", "False", "ZK1", "ZK2", "ZK3", "ZK4",
        "y", "yes", "n", "on", "off", "1", "0"}


def tag(fact_text: str) -> str:
    return next((f"ZK{i}" for i in (1, 2, 3, 4) if f"（ZK{i}）" in fact_text), "?")


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="v89probe_"))
    db = tmp / "memory.db"
    shutil.copy(SRC, db)                       # ⛔ 纪律 31
    m = MemoryStore(db)

    print("## §1 窗口位置（窗口 = 前 24 个 `_TOKEN`；`fts_query` 只发这么多个）\n")
    print("| 题 | token 总数 | 钥匙 token 及位置 | 窗口内 | 实际召回（k=6） |")
    print("|---|---|---|---|---|")
    for rel in SUITES:
        for line in (ROOT / rel).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            toks = _TOKEN.findall(r["prompt"])
            pos: dict[str, list[int]] = {}
            for i, t in enumerate(toks, 1):
                if t in KEYS:
                    pos.setdefault(t, []).append(i)
            inside = [t for t, ps in pos.items() if any(p <= 24 for p in ps)]
            outside = [t for t, ps in pos.items() if all(p > 24 for p in ps)]
            got = [tag(f.text) for f in m.recall(r["prompt"], k=6)]
            pos_s = " ".join(f"{t}@{ps}" for t, ps in pos.items()) or "（无）"
            print(f"| {r['id']} | {len(toks)} | {pos_s} | {len(inside)}/{len(pos)} | "
                  f"{'→'.join(got) if got else '（空）'} |")
            if outside:
                print(f"|  ↳ 窗口外 | | {outside} | | |")

    print("\n## §2 边界验证（纯函数级：不动题面，只构造 query）\n")
    print("填充 token 用 `zz1..zz24`（不在任何事实里）；钥匙用 `None`（只出现在 ZK3）。\n")
    for n_fill, want in ((23, "命中"), (24, "不命中")):
        q = " ".join(f"zz{i}" for i in range(1, n_fill + 1)) + " None"
        toks = _TOKEN.findall(q)
        fs = fts_query(q)
        got = [tag(f.text) for f in m.recall(q, k=6)]
        has_none = "None" in fs
        print(f"- 填充 {n_fill} 个 ⇒ 总 {len(toks)} token；`fts_query` 里还有 `None` = {has_none} "
              f"⇒ 召回 {'→'.join(got) if got else '（空）'} ｜ 期望：{want} "
              f"{'✓' if (got and want == '命中') or (not got and want == '不命中') else '⛔'}")

    print("\n## §3 结论\n")
    print("- 窗口边界 = **24**（`fts_query` 的 `toks[:24]`）；第 25 个 token 起不进查询。")
    print("- `LIKE` 回退另有一条更窄的口子：`normalize(query)[:64]`（前 64 字）。")
    print("- 题面越长越聋：k02 34 token、k04 40 token，后半段的规格词对检索**不存在**。")
    m.close()
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
