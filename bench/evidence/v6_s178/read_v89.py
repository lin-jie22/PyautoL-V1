#!/usr/bin/env python3
"""V89 读数 —— struct_convert_heldout 4 题 × 2 臂（E 空库 / Z 4 条无关 ZK）× 各 2 跑。

用法：python3 bench/evidence/v6_s178/read_v89.py /tmp/v89
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ARMS: list[tuple[str, str]] = [("E1", "空"), ("E2", "空"), ("Z1", "ZK×4"), ("Z2", "ZK×4")]
TASKS = ["k01_diff_tree", "k02_encode_cookie", "k03_unflatten", "k04_to_ini"]
#: 预注册 §4 P4：Z 臂期望的块（k01~k03 收 ZK3 一条 63 字；k04 无块）
WANT_Z = {"k01_diff_tree": ["ZK3"], "k02_encode_cookie": ["ZK3"],
          "k03_unflatten": ["ZK3"], "k04_to_ini": []}
WANT_Z_CHARS = 63


def scores(d: Path) -> dict:
    p = d / "bench" / "runs.jsonl"
    if not p.exists():
        return {}
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    per = (rec.get("report") or {}).get("per_task") or []
    return {t["id"]: (t.get("score"), t.get("why")) for t in per}


def memblocks(d: Path) -> dict:
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
    shutil.copy(db, tmp)                      # ⛔ 纪律 31
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
    for a, _sd in ARMS:
        d = root / a
        if not d.exists():
            print(f"⛔ 缺臂 {a}")
            continue
        S[a] = scores(d)
        B[a] = memblocks(d)
        F[a] = finishes(d)

    def cell(a: str, t: str):
        return S.get(a, {}).get(t, (None, None))[0]

    def mean(a: str):
        v = [cell(a, t) for t in TASKS if cell(a, t) is not None]
        return sum(v) / len(v) if v else None

    print("\n## §1 分数逐格（分母：每格 = 该题 score）\n")
    print("| 臂 | 起跑库 | " + " | ".join(t[:10] for t in TASKS) + " | mean |")
    print("|---" * (len(TASKS) + 3) + "|")
    for a, sd in ARMS:
        if a not in S:
            continue
        cells = [fmt(cell(a, t)) for t in TASKS]
        mv = mean(a)
        print(f"| {a} | {sd} | " + " | ".join(cells) + f" | {fmt(mv) if mv is None else f'{mv:.4f}'} |")

    print("\n## §2 P1 基线复现：E 两跑 —— 预注册押 mean=0.7500 且逐格全同\n")
    for a in ("E1", "E2"):
        if a in S:
            print(f"- {a}: mean={mean(a) if mean(a) is None else f'{mean(a):.4f}'} 形态="
                  f"{[fmt(cell(a, t)) for t in TASKS]}")
    if "E1" in S and "E2" in S:
        same = sum(1 for t in TASKS if cell("E1", t) is not None and cell("E2", t) is not None
                   and abs(cell("E1", t) - cell("E2", t)) <= 1e-9)
        print(f"  ⇒ 逐格同 {same}/{len(TASKS)}")
        m = mean("E1")
        print(f"  ⇒ P1 {'命中' if m is not None and abs(m - 0.75) <= 1e-9 and same == len(TASKS) else '未命中'}")

    print("\n## §3 P2（主）Z vs E —— 预注册押逐格全同（4/4）\n")
    if "Z1" in S and "E1" in S:
        same = 0
        for t in TASKS:
            e, z = cell("E1", t), cell("Z1", t)
            eq = e is not None and z is not None and abs(e - z) <= 1e-9
            same += eq
            d = "同" if eq else ("⛔降" if (e is not None and z is not None and z < e) else "⛔升")
            print(f"- {t[:20]:22s} E={fmt(e)}  Z={fmt(z)}  {d}")
        print(f"  ⇒ 同 {same}/{len(TASKS)}")
    else:
        print("- 缺臂")

    print("\n## §4 P3 题内对照：Z 臂里 k04（无注入）会不会跟着掉\n")
    if "Z1" in S:
        for t in TASKS:
            e, z = cell("E1", t), cell("Z1", t)
            if e is None or z is None:
                continue
            print(f"- {t[:20]:22s} 注入={'ZK3' if WANT_Z[t] else '（空）'}  "
                  f"Δ(J-E)={z - e:+.4f}")

    print("\n## §5 P4（装置自检）`[语义记忆]` 块\n")
    for a, sd in ARMS:
        if a not in B:
            continue
        for t in TASKS:
            rows = B[a].get(t) or []
            if not rows:
                print(f"  {a}({sd}) / {t[:20]:22s} **无块**（state 里没有 `[语义记忆]`）")
                continue
            zks = [sorted(set(r["order"])) for r in rows]
            chars = [r["chars"] for r in rows]
            if sd == "空":
                ok = all(not z for z in zks) and all(c == 0 for c in chars)
            else:
                # ⚠️ k04 的期望是**空块**（字数 0），不是 63 —— 第一版这里写错，把 k04 判成 ⛔。
                want_c = WANT_Z_CHARS if WANT_Z[t] else 0
                ok = all(z == WANT_Z[t] for z in zks) and all(abs(c - want_c) <= 2 for c in chars)
            print(f"  {a}({sd}) / {t[:20]:22s} 行数={len(rows)} 标号={zks} 字数={chars} "
                  f"{'✓' if ok else '⛔'}")

    print("\n## §6 why（打分器失败原文）\n")
    for a, sd in ARMS:
        if a not in S:
            continue
        print(f"### {a}({sd})")
        for t in TASKS:
            w = (S[a].get(t, (None, None))[1] or "")[:120].replace("\n", " ")
            print(f"  {t[:18]:20s} {w!r}")

    print("\n## §7 实现逐字节（每题前 5 行）\n")
    for j, t in enumerate(TASKS):
        print(f"### {t}")
        for a, sd in ARMS:
            fl = F.get(a) or []
            if j >= len(fl):
                print(f"  {a}({sd}): （缺）")
                continue
            lines = [ln.strip() for ln in code_of(fl[j]).splitlines()
                     if ln.strip() and not ln.strip().startswith("def ")]
            print(f"  {a}({sd}): " + " / ".join(lines[:5]))
        print()


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v89"))
