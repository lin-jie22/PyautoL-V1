#!/usr/bin/env python3
"""§17-7i 的**通道普查**：同一份记忆，两条注入路径各自给几条？

背景（实测，不是推断）：7h 判读登记了"`recall` 工具成功时返回**全部** facts"，
并把它归因于 `MemoryStore.recall()` 的**第三级全表兜底**。本探针零模型调用，
只回答三个问题：

    ① 四个题面各自**真正** FTS 命中几条？（7d–7g 记的 4/4/1/1 是怎么来的）
    ② 模型会写的那种**短 query** 在两条口径下各返回几条？
       （旧口径 = 带兜底；新口径 = 只有 FTS+LIKE）
    ③ 跑一遍 StubLLM，逐题逐**步**看 state：语义块外还有没有别的惯例？

⛔ 它不改判据、不进任何判定；判据在 `tests/contract_test.py` 的 AZ22–AZ24。

用法：
    python3 bench/probe_7i.py --seed bench/evidence/knowledge_s34/seed_H.db \
        --heldout bench/heldout/g7e_hint_heldout.jsonl --facts 7f
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V1))
sys.path.insert(0, str(V1 / "bench"))

import mw.config as cfg                                     # noqa: E402
from mw.core import Core                                    # noqa: E402
from mw.llm import StubLLM                                  # noqa: E402
from mw.memory import MemoryStore, fts_query, normalize      # noqa: E402
from build_g7d_seed import VARIANTS                          # noqa: E402
from state_audit import audit_state, format_audit            # noqa: E402


def _old_recall(db: Path, query: str, k: int = 6) -> int:
    """**旧口径**（7d–7g 那一版）：FTS → LIKE → 全表 top-k。只用于对照。"""
    c = sqlite3.connect(str(db))
    try:
        q = fts_query(query)
        if q:
            try:
                n = c.execute(
                    "SELECT COUNT(*) FROM (SELECT f.id FROM facts_fts "
                    "JOIN facts f ON f.id = facts_fts.rowid "
                    "WHERE facts_fts MATCH ? LIMIT ?)", (q, k)).fetchone()[0]
                if n:
                    return n
            except sqlite3.OperationalError:
                pass
        like = f"%{normalize(query)[:64]}%"
        n = c.execute("SELECT COUNT(*) FROM (SELECT id FROM facts "
                      "WHERE text LIKE ? LIMIT ?)", (like, k)).fetchone()[0]
        if n:
            return n
        return c.execute("SELECT COUNT(*) FROM (SELECT id FROM facts "
                         "ORDER BY hits DESC, updated_ts DESC LIMIT ?)",
                         (k,)).fetchone()[0]
    finally:
        c.close()


def _install_old_fallback() -> None:
    """把 `recall()` 换回**旧口径**（含第三级全表兜底），只为做 ③ 的 A/B。

    ⛔ 这是探针内的运行时替换，**不改仓库里的实现**、不参与任何判定；
       它的唯一用途是回答"旧口径下 1 条族的 state 里到底有没有邻域"。
    """
    def recall(self, query: str, k: int = 6):      # type: ignore[no-untyped-def]
        k = max(1, int(k))
        q = fts_query(query)
        if q:
            try:
                rows = self.conn.execute(
                    """SELECT f.*, bm25(facts_fts) AS score
                       FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid
                       WHERE facts_fts MATCH ?
                       ORDER BY score LIMIT ?""", (q, k)).fetchall()
                if rows:
                    return [self._fact(r, r["score"]) for r in rows]
            except sqlite3.OperationalError:
                self.fts_fallbacks += 1
        like = f"%{normalize(query)[:64]}%"
        rows = self.conn.execute(
            "SELECT *, 0.0 AS score FROM facts WHERE text LIKE ? "
            "ORDER BY hits DESC LIMIT ?", (like, k)).fetchall()
        if not rows:
            rows = self.conn.execute(
                "SELECT *, 0.0 AS score FROM facts "
                "ORDER BY hits DESC, updated_ts DESC LIMIT ?", (k,)).fetchall()
        return [self._fact(r, r["score"]) for r in rows]

    MemoryStore.recall = recall                    # type: ignore[method-assign]


#: 模型真会写的那种短 query（s34 日志里 `recall(...)` 的实际形态）
SHORT = {"g7d2_dedupe_keep": "去重", "g7d3_fmt_amount": "金额",
         "g7e1_parse_flag": "开关", "g7e2_truncate_tail": "截断"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--seed", required=True)
    p.add_argument("--heldout", required=True)
    p.add_argument("--facts", default="7d", choices=sorted(VARIANTS))
    p.add_argument("--with-fallback", action="store_true",
                   help="③ 用**旧口径**（含全表兜底）跑一遍做 A/B")
    args = p.parse_args(argv)
    if args.with_fallback:
        _install_old_fallback()

    seed = Path(args.seed).expanduser().resolve()
    facts = VARIANTS[args.facts]
    tasks = [json.loads(l) for l in
             Path(args.heldout).read_text(encoding="utf-8").splitlines()
             if l.strip()]
    k = cfg.Limits().recall_k

    mem = MemoryStore(seed)
    print("== ① 题面：真 FTS 命中 vs 旧口径（带全表兜底）==")
    print(f"  {'题':24s} {'新(唯 FTS+LIKE)':>16s} {'旧(含兜底)':>12s}")
    for t in tasks:
        new_n = len(mem.recall(t["prompt"], k=k))
        old_n = _old_recall(seed, t["prompt"], k)
        flag = "" if new_n == old_n else "  ← 差额来自兜底"
        print(f"  {t['id']:24s} {new_n:>16d} {old_n:>12d}{flag}")

    print("\n== ② 短 query（模型自己写的那种）==")
    print(f"  {'题':24s} {'新':>6s} {'旧':>6s}  说明")
    for t in tasks:
        q = SHORT.get(t["id"], "")
        new_n = len(mem.recall(q, k=k))
        old_n = _old_recall(seed, q, k)
        print(f"  {t['id']:24s} {new_n:>6d} {old_n:>6d}  query={q!r}")
    mem.close()

    print(f"\n== ③ 逐题逐步的 state 审计（StubLLM，零模型调用；"
          f"口径={'旧(含兜底)' if args.with_fallback else '新(唯 FTS+LIKE)'}）==")
    for t in tasks:
        db = Path(tempfile.mkdtemp()) / "m.db"
        shutil.copy2(seed, db)
        m = MemoryStore(db)
        core = Core(llm=StubLLM(), memory=m, memory_mode="raw",
                    log=lambda *_: None)
        core.run(t["prompt"], task_id=t["id"])
        print(f"  ── {t['id']}（{len(core.state_log)} 步）")
        for it, st in core.state_log:
            a = audit_state(st, facts, t["id"])
            # ⚠️ 必须把「非本题漏入」与「本题自己重复出现」分开：
            #   后者是同一事实在 `[语义记忆]` 与 `[工作记忆]` 各出现一次
            #   （工具通道把本题那条又送了一遍），**不是邻域**。
            #   混在一起数，就会把"1 条族"误读成"也有邻域"。
            _outs = list(a["outside_all"])
            _other = [k for k in _outs if k != t["id"]]
            print(f"     第{it}步 语义块本条="
                  f"{'✅' if a['own_in_semantic'] else '❌'}"
                  f"  非本题漏入={','.join(_other) if _other else '无'}"
                  f"  本题重复={'是' if t['id'] in _outs else '否'}")
        m.close()

    print("\n== 读法 ==")
    print("  · ① 的『旧』列 = 7d–7g 装置里那条路；两个数字不等就说明那一族的"
          "注入条数**不是检索来的**。")
    print("  · ② 的『旧』列应等于 facts 总数（全表兜底），『新』列应为 0。")
    print("  · ③ 每个「第 k 步」的 `outside_all` 必须是空的。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
