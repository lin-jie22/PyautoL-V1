#!/usr/bin/env python3
"""V258 分析 —— 13 格 × 2 臂（`REJECT_CARRY=5` 对照 / `=6` 处理），同一个 harness。

只读产物，不重跑：
  /tmp/v258_a/bench/runs.jsonl + /tmp/v258_a/log/core.log     （臂 a，=5）
  /tmp/v258_b/bench/runs.jsonl + /tmp/v258_b/log/core.log     （臂 b，=6）
交叉核对（⛔ 不并列比较，只用来验「重构没改坏 5 档」）：
  /tmp/v257_b/bench/runs.jsonl（V257 臂 B，=5，旧 harness）
  /tmp/v253_a/bench/runs.jsonl（V253 Stage A，=1，同 suite）

判据见 PREREG_v258.md §2：P1 基线复现 / P2 g7t4 early≥1 / P3 g7t4 仍 0.0 /
P4 t04 仍 1.0 / P5 其余 11 格 Δ=0 / P6 均值持平
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOW = Path("/tmp/v253_suite/low.jsonl")

RE_BOUNDARY = re.compile(r"▶ 任务开始")
RE_REJECT = re.compile(r"🧪 自检未过（第 (\d+)/(\d+) 次打回）")
RE_EARLY = re.compile(r"⇢ 无候选可让位 ⇒ 早分支（REJECT_CARRY=6）")
RE_VIA = re.compile(r"打回重写：走\*\*交付协议\*\*（REJECT_CARRY=(\d+)")
RE_VIA_EMPTY = re.compile(r"交付协议\*\*交空\*\*（REJECT_CARRY=(\d+)")
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
    if not p.exists():
        return []
    txt = p.read_text(encoding="utf-8", errors="replace")
    return [b for b in RE_BOUNDARY.split(txt)[1:] if b.strip()]


def per_block_stats(blocks: list[str], order: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i, b in enumerate(blocks):
        tid = order[i] if i < len(order) else f"__extra_{i}"
        stalls = [int(m[0]) for m in RE_STALL.findall(b)]
        done = RE_DONE.search(b)
        ver = RE_VERIFY.search(b)
        out[tid] = {
            "rejects": len(RE_REJECT.findall(b)),
            "early": len(RE_EARLY.findall(b)),
            "via": len(RE_VIA.findall(b)),
            "via_empty": len(RE_VIA_EMPTY.findall(b)),
            "max_stall": max(stalls) if stalls else 0,
            "status": done.group(1) if done else "?",
            "verify": list(map(int, ver.groups())) if ver else None,
        }
    return out


def main() -> int:
    low = [json.loads(l) for l in LOW.read_text(encoding="utf-8").splitlines() if l.strip()]
    order = [str(t["id"]) for t in low]

    arms = {
        "a5": Path("/tmp/v258_a"),
        "b6": Path("/tmp/v258_b"),
        "v257b5": Path("/tmp/v257_b"),
        "v253a1": Path("/tmp/v253_a"),
    }
    sc = {k: scores_from_runs(v / "bench" / "runs.jsonl") for k, v in arms.items()}
    st = {k: per_block_stats(blocks_from_log(v / "log" / "core.log"), order)
          for k, v in arms.items() if k in ("a5", "b6")}

    print(f"{'cell':26s} {'A5':>6s} {'B6':>6s} {'D':>7s} | "
          f"{'A5 rej/via/stall':>18s} | {'B6 rej/early/via/stall':>24s}")
    print("-" * 100)
    rows = []
    for tid in order:
        a, b = sc["a5"].get(tid), sc["b6"].get(tid)
        d = (b - a) if (a is not None and b is not None) else None
        sa, sb = st["a5"].get(tid, {}), st["b6"].get(tid, {})
        print(f"{tid:26s} {a if a is not None else -1:6.4f} "
              f"{b if b is not None else -1:6.4f} "
              f"{('+' if (d or 0) > 0 else '') + format(d, '.4f') if d is not None else '  n/a':>7s} | "
              f"{sa.get('rejects', 0):>6d}/{sa.get('via', 0):d}/{sa.get('max_stall', 0):d}"
              f"{'':>7s} | "
              f"{sb.get('rejects', 0):>6d}/{sb.get('early', 0):d}/{sb.get('via', 0):d}/"
              f"{sb.get('max_stall', 0):d}{'':>8s}")
        rows.append({"cell": tid, "a5": a, "b6": b, "delta": d,
                     "a5_stats": sa, "b6_stats": sb})

    def mean(d: dict[str, float]) -> float:
        vals = [d.get(t) for t in order if d.get(t) is not None]
        return sum(vals) / len(vals) if vals else 0.0

    def n_pass(d: dict[str, float]) -> int:
        return sum(1 for t in order if (d.get(t) or 0) >= 1.0)

    ma, mb = mean(sc["a5"]), mean(sc["b6"])
    print("-" * 100)
    print(f"均值  A5={ma:.4f}  B6={mb:.4f}  Δ={mb - ma:+.4f}")
    print(f"全过闸 A5={n_pass(sc['a5'])}/13  B6={n_pass(sc['b6'])}/13")

    # ── P1：臂 a（=5，新 harness）必须复现 V257 臂 B（=5，旧 harness）──
    same_v257 = [t for t in order
                 if sc["a5"].get(t) is not None and sc["a5"].get(t) == sc["v257b5"].get(t)]
    print(f"\nP1 臂 a 与 V257 臂 B 逐格同分：{len(same_v257)}/13"
          f"{'' if len(same_v257) == 13 else '  ≠ ' + str([t for t in order if t not in same_v257])}")

    # ── P2~P6 ──
    g4 = st["b6"].get("g7t4_quote_join", {})
    t04 = "t04_merge_ranges"
    others = [t for t in order if t not in (t04, "g7t4_quote_join")]
    d_others = [t for t in others
                if sc["a5"].get(t) is not None and sc["b6"].get(t) is not None
                and abs(sc["b6"][t] - sc["a5"][t]) > 1e-12]
    checks = {
        "P1 臂a == V257臂B（13/13）": len(same_v257) == 13,
        "P2 g7t4 early>=1": g4.get("early", 0) >= 1,
        "P3 g7t4 仍 0.0（我押的；反了就登记）": sc["b6"].get("g7t4_quote_join") == 0.0,
        "P4 t04 仍 1.0": sc["b6"].get(t04) == 1.0,
        "P5 其余 11 格 Δ=0": not d_others,
        "P6 均值持平（Δ=0）": abs(mb - ma) < 1e-12,
    }
    print()
    for k, v in checks.items():
        print(f"  {'✅' if v else '❌'} {k}")
    if d_others:
        print(f"     P5 有变化的格：{[(t, sc['a5'][t], sc['b6'][t]) for t in d_others]}")

    # 逐格 early 触发清单（读数，不是判据）
    early_cells = [t for t in order if st["b6"].get(t, {}).get("early", 0) > 0]
    print(f"\n臂 b 早分支触发格（{len(early_cells)}）：{early_cells}")

    out = {
        "rows": rows,
        "mean": {"a5": ma, "b6": mb},
        "pass13": {"a5": n_pass(sc["a5"]), "b6": n_pass(sc["b6"])},
        "checks": checks,
        "early_cells": early_cells,
        "scores": {k: v for k, v in sc.items()},
    }
    (HERE / "readings_v258.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n读数已落盘 {HERE / 'readings_v258.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
