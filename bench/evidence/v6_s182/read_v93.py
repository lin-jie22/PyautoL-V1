#!/usr/bin/env python3
"""V93 读数：四臂 × 4 题，逐格 + 五条判据。

⛔ 判据按 `PREREG.md` **逐字**写，不在这里放宽作用域（纪律 32）。
⛔ 只读 `runs.jsonl`（文本），不打开归档 `.db`（纪律 31）。
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
ARMS = ["R0a", "R0b", "R1a", "R1b", "R2a", "R2b", "R3a", "R3b"]
TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]
SHORT = {t: t.split("_")[0] for t in TASKS}

#: 预注册 §3 的位序读数（probe_block.txt）
ORDER = {
    "R1": "ZK1→ZK2→ZK4→ZK3 / ZK1→ZK2→ZK4→ZK3 / ZK3→… / ZK4→…",
    "R2": "ZK3→ZK4→ZK2→ZK1 / ZK1→ZK3→ZK4→ZK2 / ZK3→… / ZK3→…",
    "R3": "ZK3→ZK4→ZK2→ZK1 / ZK1→ZK3→ZK4→ZK2 / ZK3→… / ZK3→…",
}
BLOCK_CHARS = {"R0": 0, "R1": 211, "R2": 290, "R3": 307}


def scores(d: pathlib.Path) -> dict[str, float]:
    p = d / "bench" / "runs.jsonl"
    if not p.exists():
        return {}
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    per = (rec.get("report") or {}).get("per_task") or []
    return {t["id"]: t.get("score") for t in per if t.get("score") is not None}


def main() -> None:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v93")
    got: dict[str, dict[str, float]] = {}
    for a in ARMS:
        s = scores(root / a)
        if s:
            got[a] = s

    print("## §1 逐格")
    print(f"{'臂':6s}" + "".join(f"{SHORT[t]:>10s}" for t in TASKS) + f"{'mean':>8s}")
    for a in ARMS:
        if a not in got:
            print(f"{a:6s}  (缺)")
            continue
        s = got[a]
        row = [s.get(t) for t in TASKS]
        m = round(sum(x for x in row if x is not None) / len(TASKS), 4)
        print(f"{a:6s}" + "".join(f"{x:10.1f}" if x is not None else f"{'-':>10s}" for x in row) + f"{m:8.4f}")

    print("\n## §2 臂均值（两跑合并）")
    agg: dict[str, dict[str, float]] = {}
    for base in ("R0", "R1", "R2", "R3"):
        runs = [got[a] for a in ARMS if a.startswith(base) and a in got]
        if not runs:
            continue
        per_cell = {t: round(sum(r.get(t, 0) for r in runs) / len(runs), 4) for t in TASKS}
        mean = round(sum(per_cell.values()) / len(TASKS), 4)
        agg[base] = per_cell | {"mean": mean}
        print(f"{base}: " + "  ".join(f"{SHORT[t]}={per_cell[t]:.2f}" for t in TASKS) + f"  mean={mean:.4f}")

    print("\n## §3 装置检查")
    for base in ("R0", "R1", "R2", "R3"):
        pair = [a for a in ARMS if a.startswith(base) and a in got]
        if len(pair) == 2:
            same = all(got[pair[0]].get(t) == got[pair[1]].get(t) for t in TASKS)
            print(f"  {base} 两跑逐格相同: {'✓' if same else '⛔ 有翻动'}")

    print("\n## §4 判据（按 PREREG 原文）")
    r0, r1, r2, r3 = (agg.get(k, {}) for k in ("R0", "R1", "R2", "R3"))

    def cell(a: dict, t: str) -> float:
        return a.get(t, float("nan"))

    # Q1：R2.mean ≥ 0.85
    v = r2.get("mean")
    print(f"  Q1 R2.mean_4task={v:.4f}  押 ≥0.85 ⇒ {'✓ 命中' if v is not None and v >= 0.85 else '⛔ 未命中'}")
    # Q2：R2.g7d3 ≥ 0.8（拆三档）
    v = cell(r2, "g7d3_fmt_amount")
    if v >= 0.8:
        tag = "✓ 命中（且位序劣势被克服，强结论）"
    elif v >= 0.4:
        tag = "⚠️ UNKNOWN（示例与位序分不开）"
    else:
        tag = "⛔ 方向相反（最差位序下更差）"
    print(f"  Q2 R2.g7d3={v:.4f}（R1={cell(r1,'g7d3_fmt_amount'):.4f}）⇒ {tag}")
    # Q3：R2 两跑逐格相同（在 §3 已印）
    # Q4：R2 > R3（mean 与 g7d3）
    ok = r2.get("mean", -1) > r3.get("mean", 9) and cell(r2, "g7d3_fmt_amount") > cell(r3, "g7d3_fmt_amount")
    print(f"  Q4 R2>R3：mean {r2.get('mean'):.4f} vs {r3.get('mean'):.4f}；"
          f"g7d3 {cell(r2,'g7d3_fmt_amount'):.4f} vs {cell(r3,'g7d3_fmt_amount'):.4f} ⇒ {'✓' if ok else '⛔'}")
    # Q5：R0 与历史 0.20 逐格一致（V85 A 臂 `bench/evidence/v6_s174/arms/Ar1/runs.jsonl`：
    #     0.2/0.2/0.2/0.2，本脚本第一版把 g7d2 误写成 1.0，产生一个假 ⛔）
    hist = {"g7d2_dedupe_keep": 0.2, "g7d3_fmt_amount": 0.2,
            "g7e1_parse_flag": 0.2, "g7e2_truncate_tail": 0.2}
    same = all(abs(cell(r0, t) - hist[t]) < 1e-9 for t in TASKS)
    print(f"  Q5 R0 逐格={[cell(r0,t) for t in TASKS]} 历史={[hist[t] for t in TASKS]} ⇒ {'✓ 同口径' if same else '⛔ 装置变了'}")

    print("\n## §5 参考（本轮回测的对照值）")
    print(f"  R0={r0.get('mean')}  R1={r1.get('mean')}  R2={r2.get('mean')}  R3={r3.get('mean')}")
    print(f"  块字数: {BLOCK_CHARS}")


if __name__ == "__main__":
    main()
