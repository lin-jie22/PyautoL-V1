#!/usr/bin/env python3
"""V87 读数 —— 留一法 5 臂 × 4 题。

读三份恒存在的产物：`bench/runs.jsonl`（分数/why）、`state_dump.jsonl`（送达原文）、
`memory_after.db` **副本**（finish 实现全文，纪律 31：先 copy 再开）。

用法：python3 bench/evidence/v6_s176/read_loo.py /tmp/v87
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ARMS = ["M0", "M1", "M2", "M3", "M4"]
DROP = {"M0": None, "M1": 0, "M2": 1, "M3": 2, "M4": 3}
TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]
ROW = {"M0": "ZK1", "M1": "—", "M2": "ZK2", "M3": "ZK3", "M4": "ZK4"}


def scores(d: Path) -> dict:
    p = d / "bench" / "runs.jsonl"
    if not p.exists():
        return {}
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    per = (rec.get("report") or {}).get("per_task") or []
    return {t["id"]: (t.get("score"), t.get("why")) for t in per}


def finishes(d: Path) -> list[str]:
    db = d / "memory_after.db"
    if not db.exists():
        return []
    tmp = Path(tempfile.mkdtemp()) / "s.db"
    shutil.copy(db, tmp)                        # ⛔ 先 copy 再开
    c = sqlite3.connect(str(tmp))
    c.row_factory = sqlite3.Row
    rows = [r["content"] for r in c.execute(
        "SELECT content FROM episodes WHERE kind='finish' ORDER BY id")]
    c.close()
    return rows


def memblocks(d: Path) -> dict:
    p = d / "state_dump.jsonl"
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        s = r.get("state") or ""
        m = re.search(r"\[语义记忆\](.*?)(?=\n\[|\Z)", s, re.S)
        blk = m.group(1) if m else ""
        zk = sorted(set(re.findall(r"ZK[1-4]", blk)))
        out.setdefault(r.get("task"), []).append({"chars": len(blk.strip()), "zk": zk})
    return out


def code_of(body: str) -> str:
    if "```" in body:
        return body.split("```python")[-1].split("```")[0]
    return body


def main(root: Path) -> None:
    S, F, B = {}, {}, {}
    for a in ARMS:
        d = root / a
        if not d.exists():
            print(f"⛔ 缺臂 {a}")
            continue
        S[a] = scores(d)
        F[a] = finishes(d)
        B[a] = memblocks(d)

    print("\n## §1 分数逐格（删掉的那条标在左列）\n")
    print("| 臂 | 删 | " + " | ".join(t[:8] for t in TASKS) + " |")
    print("|---" * (len(TASKS) + 2) + "|")
    for a in ARMS:
        if not S.get(a):
            continue
        cells = []
        for t in TASKS:
            sc = S[a].get(t, (None, None))[0]
            cells.append("—" if sc is None else f"{sc:g}")
        print(f"| {a} | {ROW[a]} | " + " | ".join(cells) + " |")

    print("\n## §2 P1 因果效力（删第 i 条 ⇒ 第 i 题掉不掉）\n")
    for i, a in enumerate(["M1", "M2", "M3", "M4"]):
        t = TASKS[i]
        base = S.get("M0", {}).get(t, (None,))[0]
        now = S.get(a, {}).get(t, (None,))[0]
        if base is None or now is None:
            print(f"- {a} / {t}: 读数缺失")
            continue
        d = round(now - base, 4)
        verdict = "降" if d < -1e-9 else ("平" if abs(d) <= 1e-9 else "升")
        print(f"- {a}（删 {ROW[a]}）/ {t}: {base:g} → {now:g}  Δ={d:+.4f}  ⇒ **{verdict}**")

    print("\n## §3 P4 干扰（删一条后其余题）\n")
    for i, a in enumerate(["M1", "M2", "M3", "M4"]):
        cells = []
        for j, t in enumerate(TASKS):
            if j == i:
                continue
            base = S.get("M0", {}).get(t, (None,))[0]
            now = S.get(a, {}).get(t, (None,))[0]
            cells.append(f"{t[:8]} {base:g}→{now:g}")
        print(f"- {a}: " + " ｜ ".join(cells))

    print("\n## §4 P2 实现逐字节（每题取 def 行之后的关键行）\n")
    for j, t in enumerate(TASKS):
        print(f"### {t}")
        for a in ARMS:
            fl = F.get(a) or []
            if j >= len(fl):
                print(f"  {a}: （缺）")
                continue
            code = code_of(fl[j])
            lines = [ln.strip() for ln in code.splitlines()
                     if ln.strip() and not ln.strip().startswith("def ")]
            print(f"  {a}: " + " / ".join(lines[:6]))
        print()

    print("## §5 送达（[语义记忆] 块里出现了哪些 ZK 标号）\n")
    for a in ARMS:
        if not B.get(a):
            continue
        for t in TASKS:
            rows = B[a].get(t) or []
            if not rows:
                continue
            zks = sorted({z for r in rows for z in r["zk"]})
            print(f"  {a} / {t}: 块行数={len(rows)} 标号={zks} 字数={[r['chars'] for r in rows]}")

    print("\n## §6 why（打分器失败原文，逐题）\n")
    for a in ARMS:
        if not S.get(a):
            continue
        print(f"### {a}")
        for t in TASKS:
            w = (S[a].get(t, (None, None))[1] or "")[:110].replace("\n", " ")
            print(f"  {t[:16]:18s} {w!r}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v87"))
