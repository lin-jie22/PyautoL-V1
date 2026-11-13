#!/usr/bin/env python3
"""V91 读数 —— 三臂（L0 基线 / L1 只加长 ZK1 / L2 四条各加长）× 4 题。

判据编号与 `PREREG.md` §3 逐条对应（Q1~Q6）。
⛔ 臂 → 起跑库的映射与 `run_v91.sh` 的 `seed_of` 逐字对应。
⛔ 纪律 31：归档库一律 `shutil.copy` 到临时目录再开。

用法：python3 bench/evidence/v6_s180/read_v91.py /tmp/v91
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

#: 臂 → 起跑库（⛔ 与 run_v91.sh 的 `seed_of` 逐字对应）
ARMS: list[tuple[str, str]] = [
    ("L0", "L0"), ("L1a", "L1"), ("L1b", "L1"), ("L2a", "L2"), ("L2b", "L2"),
]
TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]
#: 预注册 Q5：块字数期望（`probe_v91.txt` 的读数，逐臂写死）
WANT_CHARS = {"L0": 211, "L1": 255, "L2": 387}
#: 预注册 §3 基线
BASE = [1.0, 0.4, 1.0, 1.0]


def scores(d: Path) -> dict:
    p = d / "bench" / "runs.jsonl"
    if not p.exists():
        return {}
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    per = (rec.get("report") or {}).get("per_task") or []
    return {t["id"]: (t.get("score"), t.get("why")) for t in per}


def memblocks(d: Path) -> dict:
    """→ {task: [{'chars': int, 'order': [..]}, ...]}，**位序保序**。"""
    p = d / "state_dump.jsonl"
    if not p.exists():
        return {}
    out: dict[str, list] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        s = r.get("state") or ""
        m = re.search(r"\[语义记忆\](.*?)(?=\n\[|\Z)", s, re.S)
        blk = m.group(1) if m else ""
        out.setdefault(r.get("task"), []).append(
            {"chars": len(blk.strip()), "order": re.findall(r"（(ZK[1-4])）", blk)})
    return out


def facts(d: Path) -> tuple[int, int, list[str]]:
    db = d / "memory_after.db"
    if not db.exists():
        return (-1, -1, [])
    tmp = Path(tempfile.mkdtemp()) / "s.db"
    shutil.copy(db, tmp)
    c = sqlite3.connect(str(tmp))
    c.row_factory = sqlite3.Row
    n_facts = c.execute("SELECT COUNT(*) n FROM facts").fetchone()["n"]
    n_rew = c.execute("SELECT COUNT(*) n FROM episodes WHERE kind='reward'").fetchone()["n"]
    rows = [r["content"] for r in c.execute(
        "SELECT content FROM episodes WHERE kind='finish' ORDER BY id")]
    c.close()
    shutil.rmtree(tmp.parent, ignore_errors=True)
    return (n_facts, n_rew, rows)


def code_of(body: str) -> str:
    if "```" in body:
        return body.split("```python")[-1].split("```")[0]
    return body


def fmt(sc) -> str:
    return "—" if sc is None else f"{sc:g}"


def main(root: Path) -> None:
    S, B, F = {}, {}, {}
    for a, _sd in ARMS:
        d = root / a
        if not d.exists():
            print(f"⛔ 缺臂 {a}")
            continue
        S[a] = scores(d)
        B[a] = memblocks(d)
        F[a] = facts(d)

    def cell(a: str, t: str):
        return S.get(a, {}).get(t, (None, None))[0]

    print("\n## §1 分数逐格（分母：每格 = 该题 score；行 = 臂）\n")
    print("| 臂 | 起跑库 | " + " | ".join(t[:8] for t in TASKS) + " | mean |")
    print("|---" * (len(TASKS) + 3) + "|")
    for a, sd in ARMS:
        if a not in S:
            continue
        vals = [cell(a, t) for t in TASKS]
        ok = [v for v in vals if v is not None]
        mean = sum(ok) / len(ok) if ok else None
        print(f"| {a} | {sd} | " + " | ".join(fmt(v) for v in vals) +
              f" | {'—' if mean is None else f'{mean:.4f}'} |")

    print("\n## §2 Q1 L1·g7d3 = 1.0（复现 V87/M1；零假设 0.4）\n")
    for a in ("L1a", "L1b"):
        v = cell(a, "g7d3_fmt_amount")
        print(f"- {a}: {fmt(v)}  {'✓' if v is not None and abs(v - 1.0) <= 1e-9 else '⛔'}")

    print("\n## §3 Q2 L1·g7d2 ≤ 0.4（**全轮最新信息**；零假设 1.0）\n")
    for a in ("L1a", "L1b"):
        v = cell(a, "g7d2_dedupe_keep")
        print(f"- {a}: {fmt(v)}  {'✓' if v is not None and v <= 0.4 + 1e-9 else '⛔'}")

    print("\n## §4 Q3 L1·g7e1/g7e2 都 = 1.0（字节串变了，零假设 1.0）\n")
    for a in ("L1a", "L1b"):
        v1, v2 = cell(a, "g7e1_parse_flag"), cell(a, "g7e2_truncate_tail")
        hit = v1 is not None and v2 is not None and abs(v1 - 1.0) <= 1e-9 and abs(v2 - 1.0) <= 1e-9
        print(f"- {a}: g7e1={fmt(v1)} g7e2={fmt(v2)}  {'✓' if hit else '⛔'}")

    print("\n## §5 Q4（核心）L2 四题逐格 = L0 —— 安慰剂：位序不动、总长 +176\n")
    if "L0" in S:
        base = [cell("L0", t) for t in TASKS]
        print(f"- L0 实测基线：{['—' if v is None else f'{v:g}' for v in base]}")
        print(f"- 预注册基线：{['%g' % v for v in BASE]}  "
              f"{'✓与历史一致' if all(b is not None and abs(b - p) <= 1e-9 for b, p in zip(base, BASE)) else '⚠️与历史基线不同'}")
    for a in ("L2a", "L2b"):
        if a not in S or "L0" not in S:
            print(f"- {a}: 缺")
            continue
        got = [cell(a, t) for t in TASKS]
        if any(g is None for g in got):
            print(f"- {a}: 读数缺失 {got}")
            continue
        diffs = [f"{TASKS[i][:8]} L0={fmt(base[i])}→L2={fmt(g)}"
                 for i, g in enumerate(got)
                 if base[i] is None or abs(g - base[i]) > 1e-9]
        print(f"- {a}: {[f'{g:g}' for g in got]}  与 L0 同 {len(TASKS) - len(diffs)}/{len(TASKS)}"
              + ("  ✓" if not diffs else "  ⛔ " + " ｜ ".join(diffs)))

    print("\n## §6 Q5（装置）块字数：L1 = 255±2、L2 = 387±2、L0 = 211±2\n")
    for a, sd in ARMS:
        if a not in B:
            continue
        for t in TASKS:
            rows = B[a].get(t) or []
            if not rows:
                continue
            chars = [r["chars"] for r in rows]
            want = WANT_CHARS[sd]
            ok = all(abs(c - want) <= 2 for c in chars)
            tag = "✓" if ok else "⛔"
            print(f"  {a}({sd}) / {t[:18]:20s} 字数={chars} 期望≈{want} {tag}")

    print("\n## §7 Q6（噪声地板）L1a vs L1b、L2a vs L2b —— 押逐格全同\n")
    for x, y in (("L1a", "L1b"), ("L2a", "L2b")):
        if x not in S or y not in S:
            print(f"- {x} vs {y}: 缺臂")
            continue
        same = sum(1 for t in TASKS
                   if cell(x, t) is not None and cell(y, t) is not None
                   and abs(cell(x, t) - cell(y, t)) <= 1e-9)
        print(f"- {x} vs {y}: 同 {same}/{len(TASKS)}  " +
              " ｜ ".join(f"{t[:8]} {fmt(cell(x, t))}/{fmt(cell(y, t))}" for t in TASKS))

    print("\n## §8 位序（保序读，`（ZK{i}）` 在块内的出现顺序）\n")
    for a, sd in ARMS:
        if a not in B:
            continue
        for t in TASKS:
            rows = B[a].get(t) or []
            if not rows:
                continue
            seqs = ["→".join(r["order"]) or "（空）" for r in rows]
            print(f"  {a}({sd}) / {t[:18]:20s} {len(rows)} 次： " + " ｜ ".join(seqs))

    print("\n## §9 库内计数（facts / reward episodes）与 finish 条数\n")
    for a, sd in ARMS:
        if a not in F:
            continue
        nf, nr, fins = F[a]
        print(f"  {a}({sd}): facts={nf}  reward_eps={nr}  finish={len(fins)}")

    print("\n## §10 实现逐字节（每题的关键行，前 6 行）\n")
    for j, t in enumerate(TASKS):
        print(f"### {t}")
        for a, sd in ARMS:
            fl = (F.get(a) or (0, 0, []))[2] if a in F else []
            if j >= len(fl):
                print(f"  {a}({sd}): （缺）")
                continue
            lines = [ln.strip() for ln in code_of(fl[j]).splitlines()
                     if ln.strip() and not ln.strip().startswith("def ")]
            print(f"  {a}({sd}): " + " / ".join(lines[:6]))
        print()

    print("## §11 why（打分器失败原文，逐题）\n")
    for a, sd in ARMS:
        if a not in S:
            continue
        print(f"### {a}({sd})")
        for t in TASKS:
            w = (S[a].get(t, (None, None))[1] or "")[:110].replace("\n", " ")
            print(f"  {t[:16]:18s} {w!r}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v91"))
