#!/usr/bin/env python3
"""V257 分析 —— 两臂（REJECT_CARRY=1 vs =5）在 V253 的 13 格上的读数。

只读产物，不重跑：
  /tmp/v257_a/bench/runs.jsonl  +  /tmp/v257_a/log/core.log
  /tmp/v257_b/bench/runs.jsonl  +  /tmp/v257_b/log/core.log
基线：/tmp/v253_a/bench/runs.jsonl（V253 Stage A，REJECT_CARRY=1）

判据见 PREREG_v257.md §2：P1 基线复现 / P2 t04 升 / P3 z02,g7t4 不升 /
P4 其余 10 格 Δ=0 / P5 via_answer ≥ 打回次数
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOW = Path("/tmp/v253_suite/low.jsonl")
V253_RUNS = Path("/tmp/v253_a/bench/runs.jsonl")

RE_BOUNDARY = re.compile(r"▶ 任务开始")
RE_REJECT = re.compile(r"🧪 自检未过（第 (\d+)/(\d+) 次打回）")
RE_VIA_ANSWER = re.compile(r"打回重写：走\*\*交付协议\*\*")
RE_STALL = re.compile(r"🧊 本轮 .* ⇒ 停滞 (\d+)/(\d+)")
RE_DONE = re.compile(r"■ (done|stalled|budget_exhausted|cascade|blocked|error)")
RE_VERIFY = re.compile(r"校验=(\d+)跑/(\d+)过/(\d+)打回/(\d+)放弃")


def scores_from_runs(p: Path) -> dict[str, float]:
    best: dict[str, float] = {}
    if not p.exists():
        return best
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        for t in (d.get("report") or {}).get("per_task") or []:
            cid, sc = t.get("id"), t.get("score")
            if cid and isinstance(sc, (int, float)):
                best[cid] = max(best.get(cid, -1.0), float(sc))
    return best


def blocks_from_log(p: Path) -> list[str]:
    """按 `▶ 任务开始` 切块（run.py 顺序跑 ⇒ 块序 = suite 序）。"""
    if not p.exists():
        return []
    txt = p.read_text(encoding="utf-8", errors="replace")
    parts = RE_BOUNDARY.split(txt)
    return [b for b in parts[1:] if b.strip()]


def per_block_stats(blocks: list[str], order: list[str]) -> dict[str, dict]:
    out = {}
    for i, b in enumerate(blocks):
        tid = order[i] if i < len(order) else f"__extra_{i}"
        n_reject = len(RE_REJECT.findall(b))
        n_via = len(RE_VIA_ANSWER.findall(b))
        stalls = [int(m[0]) for m in RE_STALL.findall(b)]
        done = RE_DONE.search(b)
        ver = RE_VERIFY.search(b)
        out[tid] = {
            "rejects": n_reject,
            "via_answer": n_via,
            "max_stall": max(stalls) if stalls else 0,
            "status": done.group(1) if done else "?",
            "verify": list(map(int, ver.groups())) if ver else None,
        }
    return out


def main() -> int:
    low = [json.loads(l) for l in LOW.read_text(encoding="utf-8").splitlines() if l.strip()]
    order = [str(t["id"]) for t in low]
    assert len(order) == 13, f"low.jsonl 不是 13 题：{len(order)}"

    s253 = scores_from_runs(V253_RUNS)
    sa = scores_from_runs(Path("/tmp/v257_a/bench/runs.jsonl"))
    sb = scores_from_runs(Path("/tmp/v257_b/bench/runs.jsonl"))
    if len(sa) != 13 or len(sb) != 13:
        print(f"⛔ 缺读数：arm_a={len(sa)}/13 arm_b={len(sb)}/13 ⇒ UNKNOWN")
        return 2

    ba = per_block_stats(blocks_from_log(Path("/tmp/v257_a/log/core.log")), order)
    bb = per_block_stats(blocks_from_log(Path("/tmp/v257_b/log/core.log")), order)

    print("=" * 108)
    print(f"{'cell':26s} {'V253(=1)':>9s} {'A(=1)':>7s} {'B(=5)':>7s} {'Δ(B-A)':>8s} "
          f"{'A打回':>6s} {'B打回':>6s} {'B via':>6s} {'B停滞':>6s} {'B状态':>10s}")
    print("-" * 108)
    for tid in order:
        v253 = s253.get(tid, float("nan"))
        a, b = sa[tid], sb[tid]
        da = ba.get(tid, {})
        db = bb.get(tid, {})
        print(f"{tid:26s} {v253:9.4f} {a:7.4f} {b:7.4f} {b-a:+8.4f} "
              f"{da.get('rejects',0):6d} {db.get('rejects',0):6d} "
              f"{db.get('via_answer',0):6d} {db.get('max_stall',0):6d} {db.get('status','?'):>10s}")

    # ---------------- P1 基线复现 ----------------
    same = sum(1 for t in order if abs(sa[t] - s253.get(t, -9)) < 1e-6)
    print(f"\n[P1] 臂 A 与 V253 同分格数 = {same}/13  (押 ≥10 ⇒ "
          f"{'成立' if same >= 10 else '不成立'}）")
    if same < 13:
        diff_cells = [(t, s253.get(t), sa[t]) for t in order
                      if abs(sa[t] - s253.get(t, -9)) >= 1e-6]
        print(f"     不同分的格：{diff_cells}")

    # ---------------- P2 t04 ----------------
    t04 = "t04_merge_ranges"
    p2 = sb[t04] > sa[t04]
    print(f"[P2] t04: A={sa[t04]:.4f} B={sb[t04]:.4f}  ⇒ B>A = {p2} "
          f"（押成立 ⇒ {'成立' if p2 else '不成立'}）")

    # ---------------- P3 z02/g7t4 ----------------
    syn = ["z02_to_csv", "g7t4_quote_join"]
    p3_up = [(t, sa[t], sb[t]) for t in syn if sb[t] > sa[t]]
    print(f"[P3] SyntaxError 两格：{[ (t, sa[t], sb[t]) for t in syn ]}")
    print(f"     B>A 的格数 = {len(p3_up)}  ⇒ 押『不成立』"
          f" ⇒ {'成立（我押对了：都没升）' if not p3_up else '不成立（它们升了，要追 diff）'}")

    # ---------------- P4 其余 10 格 ----------------
    rest = [t for t in order if t not in [t04] + syn]
    moved = [(t, sa[t], sb[t]) for t in rest if abs(sb[t] - sa[t]) > 1e-9]
    print(f"[P4] 其余 {len(rest)} 格：变动 {len(moved)} 个 {moved}  "
          f"⇒ 押『Δ 全 0』⇒ {'成立' if not moved else '不成立'}")

    # ---------------- P4 前提复核：臂 A 每格打回次数 ----------------
    n_zero = sum(1 for t in order if ba.get(t, {}).get("rejects", 0) == 0)
    n_hit = [t for t in order if ba.get(t, {}).get("rejects", 0) > 0]
    print(f"[P4前提] 臂 A 自检 0 打回 = {n_zero}/13；打回过的格 = {n_hit}")
    if n_zero != 10:
        print("         ⚠️ 与 V254 审计的『10 格 0 打回』不符 ⇒ P4 判 UNKNOWN（纪律：不许拿旧审计当前提）")

    # ---------------- P5 via_answer ----------------
    bad = [(t, bb.get(t, {}).get("rejects", 0), bb.get(t, {}).get("via_answer", 0))
           for t in order
           if bb.get(t, {}).get("rejects", 0) > bb.get(t, {}).get("via_answer", 0)]
    tot_rej = sum(bb.get(t, {}).get("rejects", 0) for t in order)
    tot_via = sum(bb.get(t, {}).get("via_answer", 0) for t in order)
    print(f"[P5] 臂 B 合计 打回={tot_rej} via_answer={tot_via}  "
          f"不足的格={bad} ⇒ {'成立' if not bad else '不成立'}")

    # ---------------- 汇总 ----------------
    ma = sum(sa[t] for t in order) / 13
    mb = sum(sb[t] for t in order) / 13
    m253 = sum(s253.get(t, 0) for t in order) / 13
    print(f"\n[均值] V253={m253:.4f}  A(=1)={ma:.4f}  B(=5)={mb:.4f}  Δ(B-A)={mb-ma:+.4f}")
    print(f"[全过闸格数] V253={sum(1 for t in order if s253.get(t,0)>=1.0)}/13  "
          f"A={sum(1 for t in order if sa[t]>=1.0)}/13  B={sum(1 for t in order if sb[t]>=1.0)}/13")

    out = {
        "prereg": "bench/evidence/l21_s327/PREREG_v257.md",
        "order": order,
        "v253": {t: s253.get(t) for t in order},
        "arm_a": sa, "arm_b": sb,
        "blocks_a": ba, "blocks_b": bb,
        "verdict": {
            "P1_same_ge_10": same >= 10, "P1_same_n": same,
            "P2_t04_up": p2,
            "P3_syntax_up": len(p3_up) > 0, "P3_detail": p3_up,
            "P4_rest_all_zero": not moved, "P4_moved": moved,
            "P4_prem_zero_reject": n_zero, "P5_via_ok": not bad,
        },
        "means": {"v253": m253, "a": ma, "b": mb},
    }
    (HERE / "readings_v257.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[DONE] {HERE/'readings_v257.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
