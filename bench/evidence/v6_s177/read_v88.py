#!/usr/bin/env python3
"""V88 读数 —— `V1_RECALL_K` 剂量—反应 7 臂 × 4 题。

与 V87 的 `read_loo.py` 的关键区别：**位序保序读**（`re.findall` 不排序）。
V87 用 `sorted(set(...))` 把顺序洗掉了，于是「ZK2 排第 2」只能靠删一条的臂反面推。
本轮 `k` 直接按位置截断 ⇒ 位序必须保序读，否则读数无法解释行为。

用法：python3 bench/evidence/v6_s177/read_v88.py /tmp/v88
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

#: 臂 → k（⛔ 与 run_v88.sh 的 `k_of` 逐字对应）
ARMS: list[tuple[str, int]] = [
    ("K1a", 1), ("K1b", 1), ("K2", 2), ("K3", 3), ("K4", 4), ("K6a", 6), ("K6b", 6),
]
TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]
#: 预注册 §4 P4 的字数期望。⛔ 逐 (题, k) 写死 —— 这是 `probe_order.txt` 的读数，
#:   不是「k>=2 就满」这种想当然（第一版就是这么写错的：把 k=2 的 86 字判成 ⛔）。
WANT: dict[str, dict[int, int]] = {
    "g7d2_dedupe_keep":   {1: 43, 2: 86, 3: 145, 4: 211, 6: 211},
    "g7d3_fmt_amount":    {1: 43, 2: 86, 3: 145, 4: 211, 6: 211},
    "g7e1_parse_flag":    {1: 63, 2: 109, 3: 152, 4: 211, 6: 211},
    "g7e2_truncate_tail": {1: 56, 2: 102, 3: 145, 4: 211, 6: 211},
}


def scores(d: Path) -> dict:
    p = d / "bench" / "runs.jsonl"
    if not p.exists():
        return {}
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    per = (rec.get("report") or {}).get("per_task") or []
    return {t["id"]: (t.get("score"), t.get("why")) for t in per}


def memblocks(d: Path) -> dict:
    """→ {task: [{'chars': int, 'order': ['ZK1', ...]}, ...]}，**位序保序**。"""
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


def finishes(d: Path) -> list[str]:
    db = d / "memory_after.db"
    if not db.exists():
        return []
    tmp = Path(tempfile.mkdtemp()) / "s.db"
    shutil.copy(db, tmp)                        # ⛔ 纪律 31
    c = sqlite3.connect(str(tmp))
    c.row_factory = sqlite3.Row
    rows = [r["content"] for r in c.execute(
        "SELECT content FROM episodes WHERE kind='finish' ORDER BY id")]
    c.close()
    return rows


def code_of(body: str) -> str:
    if "```" in body:
        return body.split("```python")[-1].split("```")[0]
    return body


def fmt(sc) -> str:
    return "—" if sc is None else f"{sc:g}"


def main(root: Path) -> None:
    S, B, F = {}, {}, {}
    for a, _k in ARMS:
        d = root / a
        if not d.exists():
            print(f"⛔ 缺臂 {a}")
            continue
        S[a] = scores(d)
        B[a] = memblocks(d)
        F[a] = finishes(d)

    def cell(a: str, t: str):
        return S.get(a, {}).get(t, (None, None))[0]

    print("\n## §1 分数逐格（分母：每格 = 该题 score；行 = k）\n")
    print("| 臂 | k | " + " | ".join(t[:8] for t in TASKS) + " | mean |")
    print("|---" * (len(TASKS) + 3) + "|")
    for a, k in ARMS:
        if a not in S:
            continue
        cells = [fmt(cell(a, t)) for t in TASKS]
        vals = [cell(a, t) for t in TASKS]
        mean = sum(v for v in vals if v is not None) / len([v for v in vals if v is not None]) \
            if any(v is not None for v in vals) else None
        print(f"| {a} | {k} | " + " | ".join(cells) + f" | {fmt(mean) if mean is None else f'{mean:.4f}'} |")

    print("\n## §2 P1（主）K1 的四题形态 —— 预注册押 `1.0 / 0.2 / 1.0 / 1.0`\n")
    pred = [1.0, 0.2, 1.0, 1.0]
    for a in ("K1a", "K1b"):
        if a not in S:
            print(f"- {a}: 缺")
            continue
        got = [cell(a, t) for t in TASKS]
        if any(g is None for g in got):
            print(f"- {a}: 读数缺失 {got}")
            continue
        hit = all(abs(g - p) <= 1e-9 for g, p in zip(got, pred))
        n_drop = sum(1 for g in got if g < 1.0 - 1e-9)
        n_flat = sum(1 for g in got if abs(g - 1.0) <= 1e-9)
        print(f"- {a}: {[f'{g:g}' for g in got]}  命中={hit}  掉={n_drop}  满分={n_flat}")
        if not hit:
            diffs = [f"{TASKS[i][:8]} 押{p:g}实{got[i]:g}" for i, p in enumerate(pred)
                     if abs(got[i] - p) > 1e-9]
            print(f"    偏离： " + " ｜ ".join(diffs))

    print("\n## §3 P2 K4 vs K6 逐格 —— 预注册押「全同」\n")
    if "K4" in S and "K6a" in S:
        same = 0
        for t in TASKS:
            a4, a6 = cell("K4", t), cell("K6a", t)
            eq = a4 is not None and a6 is not None and abs(a4 - a6) <= 1e-9
            same += eq
            print(f"- {t[:18]:20s} K4={fmt(a4)}  K6a={fmt(a6)}  {'同' if eq else '⛔不同'}")
        print(f"  ⇒ 同 {same}/{len(TASKS)}")
    else:
        print("- 缺臂")

    print("\n## §4 P3 K2/K3 的 g7d3 —— 预注册押 0.4\n")
    for a in ("K2", "K3", "K4", "K6a"):
        if a in S:
            print(f"- {a}: g7d3 = {fmt(cell(a, 'g7d3_fmt_amount'))}")

    print("\n## §5 P4（装置自检）注入块字数\n")
    for a, k in ARMS:
        if a not in B:
            continue
        for t in TASKS:
            rows = B[a].get(t) or []
            if not rows:
                continue
            chars = [r["chars"] for r in rows]
            want = WANT[t][k]
            ok = all(abs(c - want) <= 2 for c in chars)   # ±2：块首空格/换行不计入 `blk.strip()`
            print(f"  {a}(k={k}) / {t[:18]:20s} 字数={chars} 期望≈{want} {'✓' if ok else '⛔'}")

    print("\n## §6 P5（噪声地板）K1a vs K1b、K6a vs K6b —— 预注册押逐格全同\n")
    for x, y in (("K1a", "K1b"), ("K6a", "K6b")):
        if x not in S or y not in S:
            print(f"- {x} vs {y}: 缺臂")
            continue
        same = sum(1 for t in TASKS
                   if cell(x, t) is not None and cell(y, t) is not None
                   and abs(cell(x, t) - cell(y, t)) <= 1e-9)
        print(f"- {x} vs {y}: 同 {same}/{len(TASKS)}  " +
              " ｜ ".join(f"{t[:8]} {fmt(cell(x, t))}/{fmt(cell(y, t))}" for t in TASKS))

    print("\n## §7 位序（保序读，`（ZK{i}）` 在块内的出现顺序）\n")
    for a, k in ARMS:
        if a not in B:
            continue
        for t in TASKS:
            rows = B[a].get(t) or []
            if not rows:
                continue
            seqs = ["→".join(r["order"]) or "（空）" for r in rows]
            print(f"  {a}(k={k}) / {t[:18]:20s} {len(rows)} 次： " + " ｜ ".join(seqs))

    print("\n## §8 实现逐字节（每题取 def 行之后的关键行，前 6 行）\n")
    for j, t in enumerate(TASKS):
        print(f"### {t}")
        for a, k in ARMS:
            fl = F.get(a) or []
            if j >= len(fl):
                print(f"  {a}(k={k}): （缺）")
                continue
            lines = [ln.strip() for ln in code_of(fl[j]).splitlines()
                     if ln.strip() and not ln.strip().startswith("def ")]
            print(f"  {a}(k={k}): " + " / ".join(lines[:6]))
        print()

    print("## §9 why（打分器失败原文，逐题）\n")
    for a, k in ARMS:
        if a not in S:
            continue
        print(f"### {a}(k={k})")
        for t in TASKS:
            w = (S[a].get(t, (None, None))[1] or "")[:110].replace("\n", " ")
            print(f"  {t[:16]:18s} {w!r}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v88"))
