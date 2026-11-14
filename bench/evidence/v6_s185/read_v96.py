#!/usr/bin/env python3
"""V96 读数：六臂 × 4 题，逐格 + 五条判据（按 PREREG 逐字）。"""
from __future__ import annotations

import json
import pathlib
import sys

ARMS = ["W1", "W2", "W3", "W4", "W5", "W6"]
RUNS = [f"{a}{s}" for a in ARMS for s in "ab"]
TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]
SHORT = {t: t.split("_")[0] for t in TASKS}
#: V93 R1 逐格（W2 与它**逐字节相同** ⇒ 同批次校准锚）
R1 = {"g7d2_dedupe_keep": 1.0, "g7d3_fmt_amount": 0.4,
      "g7e1_parse_flag": 1.0, "g7e2_truncate_tail": 1.0}
BLOCK = {"W1": 204, "W2": 211, "W3": 231, "W4": 232, "W5": 288, "W6": 234}
POS = {"W1": 2, "W2": 2, "W3": 4, "W4": 2, "W5": 4, "W6": 2}


def scores(d: pathlib.Path) -> dict[str, float]:
    p = d / "bench" / "runs.jsonl"
    if not p.exists():
        return {}
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    per = (rec.get("report") or {}).get("per_task") or []
    return {t["id"]: t.get("score") for t in per if t.get("score") is not None}


def main() -> None:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v96")
    got = {r: scores(root / r) for r in RUNS}
    got = {k: v for k, v in got.items() if v}

    print("## §1 逐跑")
    print(f"{'跑':6s}" + "".join(f"{SHORT[t]:>10s}" for t in TASKS))
    for r in RUNS:
        if r not in got:
            print(f"{r:6s}  (缺)")
            continue
        print(f"{r:6s}" + "".join(
            f"{got[r][t]:10.1f}" if got[r].get(t) is not None else f"{'-':>10s}" for t in TASKS))

    print("\n## §2 臂均值（2 跑）")
    agg: dict[str, dict[str, float]] = {}
    for a in ARMS:
        runs = [got[r] for r in RUNS if r.startswith(a) and r in got]
        if not runs:
            continue
        cell = {t: round(sum(x.get(t, 0) for x in runs) / len(runs), 4) for t in TASKS}
        agg[a] = cell | {"mean": round(sum(cell.values()) / len(TASKS), 4)}
        print(f"{a} 块={BLOCK[a]:3d} ZK2位置=第{POS[a]}  "
              + "  ".join(f"{SHORT[t]}={cell[t]:.2f}" for t in TASKS)
              + f"  mean={agg[a]['mean']:.4f}")

    print("\n## §3 g7d3 响应分布")
    dist = sorted({agg[a]["g7d3_fmt_amount"] for a in agg})
    print(f"  取值集合 = {dist}   基数 = {len(dist)}")
    for a in agg:
        print(f"  {a}: {agg[a]['g7d3_fmt_amount']:.1f}")
    ones = [a for a in agg if agg[a]["g7d3_fmt_amount"] == 1.0]
    print(f"  ⇒ g7d3 = 1.0 的臂：{len(ones)}/6 {ones}")

    print("\n## §4 判据（按 PREREG 原文）")
    # U1
    if "W2" in agg:
        same = all(abs(agg["W2"][t] - R1[t]) < 1e-9 for t in TASKS)
        print(f"  U1 W2 逐格 = V93 R1：{same and '✓ 条件可复现' or '⛔ 不复现 ⇒ 全场 UNKNOWN'}")
        if not same:
            print("     实得 " + "  ".join(f"{SHORT[t]}={agg['W2'][t]:.2f}" for t in TASKS)
                  + " / 期望 " + "  ".join(f"{SHORT[t]}={R1[t]:.2f}" for t in TASKS))
    else:
        print("  U1 缺 W2 ⇒ UNKNOWN")
    # U2
    n1 = len(ones)
    if n1 <= 2:
        v2 = "✓ 命中（≤2 ⇒ 碰运气，设计路线死，只能搜）"
    elif n1 == 3:
        v2 = "⚠️ 边界（=3，按预注册不下结论）"
    else:
        v2 = "⛔ 未命中（≥4 ⇒ 措辞多数可达）"
    print(f"  U2 g7d3=1.0 的臂数 = {n1}/6（押 ≤2）⇒ {v2}")
    # U3
    print(f"  U3 g7d3 取值基数 = {len(dist)}（押 ≥3）⇒ {'✓' if len(dist) >= 3 else '⛔ 二值开关'}")
    # U4
    ok4 = True
    for a in ARMS:
        pair = [r for r in RUNS if r.startswith(a) and r in got]
        if len(pair) == 2:
            if not all(got[pair[0]].get(t) == got[pair[1]].get(t) for t in TASKS):
                ok4 = False
                print(f"  U4 ⛔ {a} 两跑有翻动：{ {SHORT[t]: (got[pair[0]].get(t), got[pair[1]].get(t)) for t in TASKS if got[pair[0]].get(t) != got[pair[1]].get(t)} }")
    print(f"  U4 六臂两跑逐格相同：{'✓（第 8 次确认噪声地板 0）' if ok4 else '⛔ 见上'}")
    # U5
    others = [t for t in TASKS if t != "g7d3_fmt_amount"]
    flipped = []
    for t in others:
        vals = {agg[a][t] for a in agg}
        if len(vals) > 1:
            flipped.append((SHORT[t], sorted(vals)))
    print(f"  U5 非 g7d3 格子在六臂间翻转：{len(flipped)} 格 {flipped}（押 ≥1）"
          f" ⇒ {'✓ ZK2 措辞串味' if flipped else '⛔ 不串味'}")

    print("\n## §5 若 U2 命中（碰运气），撞到 1.0 要试几次")
    if n1:
        p = n1 / len(agg)
        print(f"  命中率 p̂ = {n1}/6 = {p:.3f}")
        for want in (0.5, 0.9, 0.99):
            if 0 < p < 1:
                import math
                need = math.ceil(math.log(1 - want) / math.log(1 - p))
                print(f"  至少撞到一次 1.0 的概率 ≥ {want:.0%} ⇒ 需写 **{need}** 个变体")
            else:
                print(f"  p̂={p:.0f} ⇒ 不适用")
    else:
        print("  六臂全 0 ⇒ 无从估 p̂；且 W2=0.4 说明 ZK2 位置/措辞集体不够，需开第七臂对照。")


if __name__ == "__main__":
    main()
