#!/usr/bin/env python3
"""V99 读数：九臂 × 4 题，逐格 + 盆表 + 六条判据（按 PREREG 逐字）。"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

ARMS = ["O0", "O1", "O2", "O3", "O4", "O5", "O6", "O7", "O8"]
TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]
SHORT = {t: t.split("_")[0] for t in TASKS}
BASE = {"g7d2_dedupe_keep": 1.0, "g7d3_fmt_amount": 0.4,
        "g7e1_parse_flag": 1.0, "g7e2_truncate_tail": 1.0}
EDIT = {"O0": "无（基线）", "O1": "。→.", "O2": "两→2", "O3": "（→(",
        "O4": "）→)", "O5": "：→:", "O6": "，→,", "O7": "，→,", "O8": "、→,"}
#: 已登记的 g7d3 盆（V94~V98 逐字节核对过）
KNOWN = {"075b4099": 0.4, "8089eee6": 1.0, "3e033a96": 0.6,
         "8f2e966c": 0.0, "5755e033": 0.6}


def scores(d: pathlib.Path) -> dict[str, float]:
    p = d / "bench" / "runs.jsonl"
    if not p.exists():
        return {}
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    per = (rec.get("report") or {}).get("per_task") or []
    return {t["id"]: t.get("score") for t in per if t.get("score") is not None}


def answers(d: pathlib.Path) -> dict[str, str]:
    for p in (d / "bench" / "answers.jsonl", d / "answers.jsonl"):
        if p.exists():
            out = {}
            for ln in p.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                r = json.loads(ln)
                if r.get("kind") != "answer":
                    continue
                out[r["task_id"]] = r.get("answer") or ""
            return out
    return {}


def main() -> None:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v99")
    got = {a: scores(root / a) for a in ARMS}
    ans = {a: answers(root / a) for a in ARMS}
    got = {k: v for k, v in got.items() if v}

    print("## §1 逐臂")
    print(f"{'臂':4s}{'改动':12s}" + "".join(f"{SHORT[t]:>9s}" for t in TASKS) + f"{'mean':>8s}  g7d3 指纹")
    fp3 = {}
    for a in ARMS:
        if a not in got:
            print(f"{a:4s}{EDIT[a]:12s}  (缺)")
            continue
        row = [got[a].get(t) for t in TASKS]
        m = round(sum(x for x in row if x is not None) / len(TASKS), 4)
        h = hashlib.sha256((ans.get(a, {}).get("g7d3_fmt_amount") or "").encode()).hexdigest()[:8]
        fp3[a] = (h, got[a].get("g7d3_fmt_amount"))
        print(f"{a:4s}{EDIT[a]:12s}" + "".join(f"{x:9.1f}" if x is not None else f"{'-':>9s}" for x in row)
              + f"{m:8.4f}  {h}" + ("（已知）" if h in KNOWN else "（新）" if ans.get(a) else ""))

    print("\n## §2 盆表（g7d3）")
    by = {}
    for a, (h, s) in fp3.items():
        by.setdefault(h, []).append((a, s))
    for h, lst in sorted(by.items(), key=lambda kv: -len(kv[1])):
        tag = f"已知 {KNOWN[h]}" if h in KNOWN else "**新**"
        print(f"  {h}  分={lst[0][1]}  {tag}  臂={[a for a, _ in lst]}")
    print(f"  ⇒ 不同盆数 = {len(by)}（已知 5 个：{list(KNOWN)}）")

    print("\n## §3 判据（按 PREREG 原文）")
    # Q0
    if "O0" in got:
        same = all(abs(got["O0"][t] - BASE[t]) < 1e-9 for t in TASKS)
        h0 = fp3.get("O0", ("", None))[0]
        print(f"  Q0 O0 逐格 = 1.0/0.4/1.0/1.0 且指纹 075b4099："
              f"{'✓ 装置没漂' if same and h0 == '075b4099' else '⛔ ⇒ 本轮 UNKNOWN'}"
              f"（指纹 {h0}）")
    else:
        print("  Q0 缺 O0 ⇒ UNKNOWN")

    # Q1
    h1 = fp3.get("O1", ("", None))[0]
    print(f"  Q1 O1 复现 8089eee6：{'✓' if h1 == '8089eee6' else '⛔'}（实得 {h1}）")

    # Q2
    new = [h for h in by if h not in KNOWN]
    print(f"  Q2 未登记的新盆 = {len(new)} 个（押 ≥2）⇒ "
          f"{'✓' if len(new) >= 2 else '⛔ 少于 2'}")

    # Q3
    zero = by.get("8f2e966c", [])
    print(f"  Q3 0.0 盆 8f2e966c 出现 {len(zero)} 次（押 0）⇒ {'✓' if not zero else '⛔ 出现了'}")

    # Q4 结构断言
    bad = []
    for h, lst in by.items():
        ss = {s for _, s in lst}
        if len(ss) > 1:
            bad.append((h, ss))
    print(f"  Q4 同指纹同分（结构断言）：{'✓ 成立' if not bad else f'⛔ 破 {bad}'}")

    # Q5
    best = max((round(sum(got[a][t] for t in TASKS) / 4, 4) for a in got), default=None)
    print(f"  Q5 九臂最好 mean = {best:.4f}（押 ≥0.85）⇒ {'✓' if best and best >= 0.85 else '⛔'}")

    print("\n## §4 撞到 1.0 的经验概率（粗估，n=9）")
    ones = [a for a in fp3 if fp3[a][1] == 1.0]
    print(f"  g7d3 = 1.0 的臂：{len(ones)}/9 {ones} ⇒ p̂ = {len(ones)/9:.3f}")
    print("  ⚠️ 这些扰动**不是随机抽样**（全是全角标点→ASCII），⛔ 不能当盆空间的估计。")


if __name__ == "__main__":
    main()
