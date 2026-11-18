#!/usr/bin/env python3
"""V204：原料门控 vs 指令改造 —— 哪个能救蒸馏。

四臂（2×2）：
  A  全部 500 × 原指令   —— **复用 V202 的 67 条产出**，不重跑
  B  全部 500 × 新指令
  C  门控 108 × 原指令
  D  门控 108 × 新指令

⛔ 不改内核：清洗逻辑直接 import `mw.abstractor` 的同一批函数，保证口径一致。
⛔ 不写原库：每臂 shutil.copy 到 /tmp（纪律 31）。
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

SRC_DB = ROOT / "state" / "memory.db"
V202_DB = Path("/tmp/v202_full/memory.db")
TMP = Path("/tmp/v204")

# ---------------------------------------------------------------- 门控（预注册冻结）
_PLACEHOLDER = re.compile(r"^\(语义记忆中无相关条目\)$|^stalled: \{\}$")
_MISS_DEF = re.compile(r"^收尾交付未过自检 — \d+/\d+ 项未过：缺少 def \w+\(\.\.\.\)$")
_PURE_ANSWER = re.compile(r"^(closed|done):\s*```")
_HAS_EVID = re.compile(r"⇒|\bassert\b|编译失败|Traceback|\bError\b|Exception")


def gate(content: str) -> str | None:
    t = (content or "").strip()
    if _PLACEHOLDER.match(t) or _MISS_DEF.match(t) or _PURE_ANSWER.match(t):
        return None
    if len(t) < 40 and not _HAS_EVID.search(t):
        return None
    return t


# ---------------------------------------------------------------- 新指令
_DISTILL_SYSTEM_V2 = (
    "你是「题目结构提炼器」。给你一段 AI 智能体过去的执行记录"
    "（可能含题面、失败信息、预演差异）。\n"
    "任务：写出**这道题/这类题的结构性陷阱** —— 下次遇到同类题，具体该防什么。\n"
    "要求：\n"
    "1. 一句话，但必须点名**具体的结构**（例：『空串要返回 None 而不是空字符串』"
    "『端点相接 (1,3) 与 (3,6) 不算重叠』『千分位从右往左每三位插逗号』）。\n"
    "2. 绝对不要写代码、不要出现函数名、变量名、代码块。\n"
    "3. **不要写**『先检查』『注意细节』『确保完整』『统一格式』这类没有可执行内容的空话"
    "—— 那不算经验，算复述脚手架。\n"
    "4. 如果这段记录确实不含结构性信息，就只输出一个词：NONE\n"
)


def fp(p: Path) -> dict:
    st = p.stat()
    return {"size": st.st_size, "mtime": round(st.st_mtime, 3),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest()[:16]}


def load_episodes(db: Path, gated: bool) -> list[dict]:
    """按 `_source_episodes` 的口径取 + 排序（failure 优先，id 倒序）。"""
    import sqlite3
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    eps = [dict(r) for r in c.execute(
        "SELECT id, kind, content FROM episodes ORDER BY id DESC LIMIT 500")]
    c.close()
    eps.sort(key=lambda e: (0 if e["kind"] == "failure" else 1, -e["id"]))
    if not gated:
        return eps
    kept, seen = [], set()
    for e in eps:
        t = gate(e["content"])
        if not t:
            continue
        key = t[:100]
        if key in seen:
            continue
        seen.add(key)
        kept.append(e)
    return kept


def run_arm(name: str, eps: list[dict], system: str, tag: str) -> dict:
    """跑一个臂：逐条调 LLM → 复用内核清洗逻辑 → 写进该臂的库副本。"""
    from mw import config as cfg
    from mw.abstractor import (MIN_ABSTRACT_CHARS, _IDENTIFIER, _to_one_line,
                               looks_like_code, ABSTRACT_SOURCE)
    from mw.llm import build_llm, Message
    from mw.memory import MemoryStore

    d = TMP / name
    d.mkdir(parents=True, exist_ok=True)
    db = d / "memory.db"
    if db.exists():
        db.unlink()
    shutil.copy(SRC_DB, db)

    llm = build_llm(cfg.LLMConfig())
    rows, t0 = [], time.monotonic()
    with MemoryStore(db) as mem:
        for i, ep in enumerate(eps, 1):
            raw = str(ep["content"] or "").strip()
            rec = {"arm": name, "src_id": ep["id"], "kind": ep["kind"],
                   "raw_len": len(raw), "raw": raw[:300]}
            try:
                out = llm.complete(
                    [Message("system", system),
                     Message("user", f"执行记录（kind={ep['kind']}）：\n{raw[:1200]}")],
                    max_tokens=160, temperature=0.0)
                text = str(getattr(out, "content", out)).strip()
            except Exception as exc:                      # noqa: BLE001
                rec.update(kept=False, reason="failed_llm", out="", err=str(exc)[:120])
                rows.append(rec)
                continue
            rec["out"] = text[:400]
            if not text or text.upper().startswith("NONE"):
                rec.update(kept=False, reason="NONE")
            elif looks_like_code(text):
                rec.update(kept=False, reason="code")
            elif _IDENTIFIER.search(text):
                rec.update(kept=False, reason="identifier")
            else:
                line = _to_one_line(text)
                if len(line) < MIN_ABSTRACT_CHARS:
                    rec.update(kept=False, reason="empty", cleaned=line)
                else:
                    mem.commit_fact(line, source=ABSTRACT_SOURCE, confidence=0.5)
                    rec.update(kept=True, reason="kept", cleaned=line)
            rows.append(rec)
            if i % 50 == 0:
                print(f"    [{name}] {i}/{len(eps)}  {time.monotonic()-t0:.0f}s", flush=True)

    st = collections.Counter(r["reason"] for r in rows)
    n_kept = st["kept"]
    # 该臂库里的 abstract 条数（去重后）
    with MemoryStore(db) as mem:
        n_abs = mem.conn.execute(
            "SELECT COUNT(*) FROM facts WHERE source=?", (ABSTRACT_SOURCE,)).fetchone()[0]
        texts = [r[0] for r in mem.conn.execute(
            "SELECT text FROM facts WHERE source=? ORDER BY id", (ABSTRACT_SOURCE,))]
        # 本臂新增（跑前库里 abstract=0，因为原库没有 abstract）
        pass
    print(f"  [{name}] 原料={len(eps)} kept={n_kept} 库内 abstract={n_abs} "
          f"耗时={time.monotonic()-t0:.0f}s  分布={dict(st)}")
    return {"arm": name, "tag": tag, "system": system, "n_eps": len(eps),
            "n_kept": n_kept, "n_abs_rows": n_abs, "reasons": dict(st),
            "rate_per_ep": round(n_kept / max(1, len(eps)), 4),
            "texts": texts, "rows": rows,
            "elapsed_s": round(time.monotonic() - t0, 1)}


def arm_a_from_v202() -> dict:
    """A 臂：复用 V202 的 67 条产出（原指令 × 全部 500）。"""
    import sqlite3
    if not V202_DB.exists():
        raise SystemExit(f"⛔ V202 库不存在：{V202_DB}")
    c = sqlite3.connect(f"file:{V202_DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT id, text, hits FROM facts WHERE source='abstract' ORDER BY id")]
    c.close()
    return {"arm": "A_full_old", "tag": "复用 V202（原指令 × 全部 500）",
            "n_eps": 500, "n_kept": 295, "n_abs_rows": len(rows),
            "texts": [r["text"] for r in rows],
            "hits_sum": sum(r["hits"] for r in rows), "rows": []}


def main() -> int:
    # 隔离自证
    before = {n: fp(SRC_DB) for n in ("x",)}
    TMP.mkdir(parents=True, exist_ok=True)

    from mw.abstractor import _DISTILL_SYSTEM as SYS_OLD

    cap = int(os.environ.get("V204_MAX", "0") or 0)   # 冒烟用：只跑前 N 条

    full = load_episodes(SRC_DB, gated=False)
    gated = load_episodes(SRC_DB, gated=True)
    print(f"[自检] 全部={len(full)}  门控后={len(gated)}")
    if len(gated) != 108:
        print(f"⚠️ 门控后不是预注册的 108（{len(gated)}）⇒ 如实登记，不改门控规则。")

    result = {"prereg_gated": 108, "n_full": len(full), "n_gated": len(gated)}

    print("\n[A] 复用 V202")
    a = arm_a_from_v202()
    print(f"  abstract={a['n_abs_rows']} kept={a['n_kept']} hits和={a['hits_sum']}")
    result["A"] = a

    print("\n[B] 全部 500 × 新指令")
    result["B"] = run_arm("B_full_new", full[:cap] if cap else full,
                          _DISTILL_SYSTEM_V2, "全部 × 新指令")

    print("\n[C] 门控 108 × 原指令")
    result["C"] = run_arm("C_gate_old", gated[:cap] if cap else gated,
                          SYS_OLD, "门控 × 原指令")

    print("\n[D] 门控 108 × 新指令")
    result["D"] = run_arm("D_gate_new", gated[:cap] if cap else gated,
                          _DISTILL_SYSTEM_V2, "门控 × 新指令")

    # 隔离
    after = {n: fp(SRC_DB) for n in ("x",)}
    clean = before == after
    print(f"\n[隔离] 原库跑前跑后一致：{clean}")
    result["isolation"] = {"clean": clean, "before": before, "after": after}

    (OUT / "readings_v204.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {OUT/'readings_v204.json'}")
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
