#!/usr/bin/env python3
"""V22 读数器 —— 按 `PREREG.md` §3/§4 的口径出判据，一条不多一条不少。

⚠️ 判据在预注册里，算式在这里。**两者不一致时只认预注册**（§83.6 自踩 1 的教训）：
   `noise_floor` 的口径写死在 PREREG §4 P1-a —— 同条件 5 轮的轮间极差，
   不是 `analyze_v19.py:177` 那个 `max(A 极差, B 三轮极差)`。

用法：
    python3 bench/evidence/v6_s113/analyze_v22.py [工作目录=/tmp/v22] [轮数=5]
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ARMS = ("s0", "s1")
NAME = {"s0": "S0 无 reward 块", "s1": "S1 有 reward 块"}
TASKS = ("e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni")
CELLS = 96           # 6 题 × 16


def rows(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _round_dir(w: Path, arm: str, r: int) -> Path:
    """优先用补跑目录 —— 但**只在该轮原本 EXIT≠0 时**才允许替换。

    ⚠️ 这条判断必须显式：否则「补跑目录里有就用」等于**事后挑好看的轮**
    （补跑可能只覆盖了一部分，剩下的是原轮，混起来就分不清哪个是哪个）。
    """
    tag = f"{arm}_r{r}"
    t = w / f"time_{tag}.txt"
    orig_ok = t.exists() and t.read_text().strip().endswith("EXIT=0")
    retry = w / "retry" / f"bench_{tag}"
    if not orig_ok and retry.exists():
        rt = w / "retry" / f"time_{tag}.txt"
        if rt.exists() and rt.read_text().strip().endswith("EXIT=0"):
            return retry
    return w / f"bench_{tag}"


def round_score(w: Path, arm: str, r: int) -> tuple[float, dict, dict]:
    """一轮的格分 + 逐题分 + 逐题交付件 sha。"""
    d = _round_dir(w, arm, r)
    runs = rows(d / "runs.jsonl")
    if not runs:
        return float("nan"), {}, {}
    pt = runs[-1]["report"]["per_task"]
    per: dict[str, list[float]] = {}
    for t in pt:
        per.setdefault(str(t["id"]), []).append(float(t.get("score") or 0.0))
    task_mean = {k: sum(v) / len(v) for k, v in per.items()}
    # 分母 = 6 题全部，量不到的记 0（PREREG §3）
    sc = sum(task_mean.get(t, 0.0) for t in TASKS) / len(TASKS) * CELLS
    sha: dict[str, str] = {}
    for x in rows(d / "answers.jsonl"):
        tid = str(x.get("task_id"))
        a = str(x.get("answer") or "")
        sha[tid] = hashlib.sha256(a.encode()).hexdigest()[:12]
    return sc, task_mean, sha


def answers(w: Path, arm: str, r: int, tid: str) -> str:
    for x in rows(_round_dir(w, arm, r) / "answers.jsonl"):
        if str(x.get("task_id")) == tid:
            return str(x.get("answer") or "")
    return ""


def main(argv: list[str]) -> int:
    w = Path(argv[1] if len(argv) > 1 else "/tmp/v22")
    rounds = int(argv[2] if len(argv) > 2 else 5)
    print(f"工作目录 {w}   轮数 {rounds}\n")

    # ── P0 ────────────────────────────────────────────────────────────────
    print("=" * 72)
    print("P0 装置自检")
    print("=" * 72)
    time_rows = []
    for arm in ARMS:
        for r in range(1, rounds + 1):
            p = w / f"time_{arm}_r{r}.txt"
            if p.exists():
                ln = p.read_text().strip().split()
                time_rows.append((f"{arm}_r{r}", ln[-1]))
    bad = [t for t, e in time_rows if e != "EXIT=0"]
    print(f"  P0-a 轮次 {len(time_rows)}/{rounds*2}  原始 EXIT≠0 的轮：{bad or '无'}")
    # ⚠️ 原始 EXIT≠0 的轮若被补跑顶替，必须显式说出来 —— 否则「10/10」是假象。
    replaced, still = {}, []
    for t in bad:
        rt = w / "retry" / f"time_{t}.txt"
        if rt.exists() and rt.read_text().strip().endswith("EXIT=0"):
            replaced[t] = "补跑成功"
        else:
            still.append(t)
    if replaced:
        print(f"       ⇒ 补跑顶替 {len(replaced)} 轮：{sorted(replaced)}（补跑自身 EXIT=0）")
    print(f"  P0-a 有效轮次 = {len(time_rows) - len(still)}/{rounds*2}"
          + ("" if not still else f"  ❌ 仍不合格：{still}"))
    p0c = (w / "p0c.txt").read_text().strip().splitlines() if (w / "p0c.txt").exists() else []
    nok = sum(1 for x in p0c if x.endswith("OK"))
    nmis = sum(1 for x in p0c if x.endswith("MISMATCH"))
    print(f"  P0-c 重置校验 OK={nok} MISMATCH={nmis}")
    for x in p0c:
        if x.endswith("MISMATCH"):
            print("      ❌", x)

    # ── 逐轮读数 ──────────────────────────────────────────────────────────
    sc: dict[str, list[float]] = {a: [] for a in ARMS}
    tsc: dict[str, list[dict]] = {a: [] for a in ARMS}
    sha: dict[str, list[dict]] = {a: [] for a in ARMS}
    print("\n" + "=" * 72)
    print(f"逐轮格分（{CELLS} 格制）")
    print("=" * 72)
    for arm in ARMS:
        line = []
        for r in range(1, rounds + 1):
            s, tm, sh = round_score(w, arm, r)
            sc[arm].append(s)
            tsc[arm].append(tm)
            sha[arm].append(sh)
            line.append(f"{s:.1f}" if s == s else "—")
        print(f"  {NAME[arm]:<16} " + "  ".join(f"r{i+1}={x}" for i, x in enumerate(line)))
    allsc = [x for a in ARMS for x in sc[a] if x == x]
    if allsc and all(x == allsc[0] for x in allsc):
        print("\n  ⚠️ 所有轮分完全相同 ⇒ 装置确定性极强（T=0 下同一库给出同一分）")
    avgs = {a: sum(x for x in sc[a] if x == x) / max(1, sum(1 for x in sc[a] if x == x)) for a in ARMS}
    for a in ARMS:
        vals = [x for x in sc[a] if x == x]
        if vals:
            print(f"  {NAME[a]:<16} mean={avgs[a]:.1f}  极差={max(vals)-min(vals):.1f}")

    # ── P1-a 确定性 ───────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("P1-a 确定性：同条件 5 轮内，同一题的交付件是否逐字节相同")
    print("=" * 72)
    noise = 0.0
    for arm in ARMS:
        for tid in TASKS:
            shs = [sh.get(tid, "—") for sh in sha[arm]]
            uniq = len(set(shs))
            flag = "✅ 全同" if uniq == 1 else f"⚠️ {uniq} 种：{shs}"
            print(f"  {arm} {tid:<5} {flag}")
        vals = [x for x in sc[arm] if x == x]
        if vals:
            noise = max(noise, max(vals) - min(vals))
    print(f"\n  noise_floor = max(两条件轮间极差) = {noise:.1f} 格")

    # ── P1-b 效应 ─────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("P1-b 效应")
    print("=" * 72)
    d = avgs["s1"] - avgs["s0"]
    thr = max(noise, 16.0)
    print(f"  Δ = mean(S1) − mean(S0) = {avgs['s1']:.1f} − {avgs['s0']:.1f} = {d:+.1f} 格")
    print(f"  门槛 = max(noise_floor {noise:.1f}, 一格 16) = {thr:.1f}")
    if d <= -thr:
        v = "提示块有害"
    elif d >= thr:
        v = "提示块有益"
    else:
        v = "UNDECIDED（⛔ 不许写成「无差异」）"
    print(f"  判定 = {v}")

    # ── 逐题矩阵 ──────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("逐题均值（×16 = 格）")
    print("=" * 72)
    print(f"  {'':6}" + "".join(f"{t:>9}" for t in TASKS))
    for arm in ARMS:
        cells = []
        for t in TASKS:
            vals = [x.get(t, 0.0) * 16 for x in tsc[arm] if x]
            cells.append(sum(vals) / len(vals) if vals else 0.0)
        print(f"  {arm.upper():6}" + "".join(f"{c:>9.1f}" for c in cells))

    # ── P2 定性 ───────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("P2 定性：e1ni 交付件是否含 import re（不进主判据）")
    print("=" * 72)
    for arm in ARMS:
        fl = []
        for r in range(1, rounds + 1):
            a = answers(w, arm, r, "e1ni")
            fl.append("含" if "import re" in a else "缺")
        print(f"  {NAME[arm]:<16} " + " ".join(fl) + f"   （{fl.count('含')}/{len(fl)} 含）")

    print("\n" + "=" * 72)
    print("⛔ 边界：本轮只有一次投喂 ⇒ 不许说「累积循环闭合」；")
    print("   检查项→类别映射是人写的模板 ⇒ 不许说「系统自己学会了」。")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
