#!/usr/bin/env python3
"""§17-7c C1 判读（零模型调用）：D2 / D2b 三臂读数 → 预登记的三种结局之一。

⛔ 本探针**不产生 Δ**：D2/D2b 改了题面，不是同一把尺子。它只回答
"下一步该修哪一层"。对照组 = s30 三臂读数（g7s2=0.4 / g7s4=0.6，3 次逐位相同）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CONTROL = {"g7s2_parse_headers": 0.4, "g7s4_xml_escape_attrs": 0.6}
TARGET = "g7s2_parse_headers"
OTHER = "g7s4_xml_escape_attrs"
TOL = 0.05


def _scores(path: Path) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        for t in json.loads(line)["report"]["per_task"]:
            out.setdefault(t["id"], []).append(float(t["score"]))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", required=True)
    args = ap.parse_args(argv)
    ev = Path(args.evidence)

    arms: dict[str, dict[str, list[float]]] = {}
    for arm in ("D2", "D2b"):
        per: dict[str, list[float]] = {}
        for r in (1, 2, 3):
            p = ev / f"{arm}_r{r}_runs.jsonl"
            if not p.exists():
                print(f"❌ 缺 {p} ⇒ 不判读")
                return 2
            for k, v in _scores(p).items():
                per.setdefault(k, []).extend(v)
        arms[arm] = per

    print("=" * 66)
    ok = True
    for arm, per in arms.items():
        for tid in (TARGET, OTHER):
            xs = per[tid]
            rng = max(xs) - min(xs)
            mean = sum(xs) / len(xs)
            print(f"{arm:4s} {tid:24s} {xs}  mean={mean:.4f} range={rng:.4f}")
            ok &= rng <= TOL
    print("=" * 66)
    print(f"R-noise：两臂逐题 3 次极差 ≤ {TOL} ⇒ {'✔' if ok else '❌ 读数不稳，判读作废'}")

    d2 = sum(arms["D2"][TARGET]) / 3
    d2b = sum(arms["D2b"][TARGET]) / 3
    other = sum(arms["D2"][OTHER]) / 3
    ctrl = CONTROL[TARGET]
    print(f"\nD2  g7s2 = {d2:.4f}（对照 {ctrl:.2f}，位移 {d2 - ctrl:+.4f}）")
    print(f"D2b g7s2 = {d2b:.4f}（对照 {ctrl:.2f}，位移 {d2b - ctrl:+.4f}）")
    print(f"D2  g7s4 = {other:.4f}（对照 {CONTROL[OTHER]:.2f}，"
          f"位移 {other - CONTROL[OTHER]:+.4f}）")

    if not ok:
        return 2
    other_same = abs(other - CONTROL[OTHER]) <= TOL
    if d2 >= ctrl + 0.2 and d2b <= ctrl + TOL and other_same:
        print("\n⇒ 结局 (β)：**形式/过载**是瓶颈 ⇒ 走 C2（改蒸馏形式 + 注入限 1 条）")
        print("   ⛔ 这不构成任何 Δ，不许认领「知识可执行」；它只说明该修哪一层。")
        return 0
    if abs(d2 - ctrl) <= TOL:
        print("\n⇒ 结局 (α)：单条判别式也不动 ⇒ 记 qwen2.5:7b **实现能力上界**，"
              "挂「待换模型检验」。§17-7 结论改写为题面完备 ⇒ 结构性 Δ=0。")
        return 3
    if d2 <= ctrl - 0.2:
        print("\n⇒ 结局「负向」：任何注入都让读数变差 ⇒ 同 (α) 挂起，"
              "并登记「注入必须可关闭」。")
        return 4
    print("\n⇒ 读数落在预登记的三种结局之外 ⇒ 不作判读，如实登记并重跑"
          "（不许事后补第四种结局）。")
    return 5


if __name__ == "__main__":
    sys.exit(main())
