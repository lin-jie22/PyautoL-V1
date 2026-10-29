#!/usr/bin/env python3
"""V19 分析 —— 按预注册 `bench/evidence/v6_s111/PREREG.md` 出读数与判据。

⛔ 本脚本**不**决定判据内容；判据在 PREREG 里冻结，这里只做执行。
⛔ 每一个数都必须能从 `runs.jsonl` 或原始日志复算；算不出的印 `None`，不许填 0。

分母：D3 格分 = 96 格（6 题 × 16 格）。`M = mean(per_task.score) × 96`。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

W = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v19")
CELLS = 96
BIG_CELLS = 16          # 一道题的格数 —— PREREG §5 的最小证据单位


def load_round(arm: str, r: int) -> dict | None:
    p = W / f"bench_{arm}_r{r}" / "runs.jsonl"
    if not p.exists():
        return None
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not rows:
        return None
    return rows[-1]


def cell_score(run: dict) -> float:
    pt = run["report"]["per_task"]
    if not pt:
        raise ValueError("per_task 为空")
    return sum(float(t.get("score") or 0.0) for t in pt) / len(pt) * CELLS


def per_task_cells(run: dict) -> dict[str, float]:
    """按题号聚合（3 rep 取均值，再 × 16 格）。"""
    acc: dict[str, list[float]] = {}
    for t in run["report"]["per_task"]:
        acc.setdefault(str(t["id"]), []).append(float(t.get("score") or 0.0))
    return {k: sum(v) / len(v) * BIG_CELLS for k, v in acc.items()}


def reward_line(arm: str, r: int) -> str:
    """从原始日志取 `奖励[...]` 段，原样返回（不解析成数字，避免再犯 §80.7-1 口径错）。"""
    p = W / f"run_{arm}_r{r}.log"
    if not p.exists():
        return ""
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if "奖励[" in line:
            m = re.search(r"奖励\[[^\]]*\]", line)
            return m.group(0) if m else ""
    return ""


def grab(pattern: str, text: str) -> int | None:
    m = re.search(pattern + r"=(\d+)", text)
    return int(m.group(1)) if m else None


def main() -> int:
    print("=" * 66)
    print("V19 读数 —— PREREG §5 / §6")
    print("=" * 66)

    data: dict[tuple[str, int], dict] = {}
    for arm in ("a", "b"):
        for r in (1, 2, 3):
            run = load_round(arm, r)
            if run is None:
                print(f"⚠️ 缺 bench_{arm}_r{r}/runs.jsonl")
                continue
            data[(arm, r)] = run

    # ── 逐轮 D3 格分 ────────────────────────────────────────────────────
    print("\n【逐轮 D3 格分（分母 96）】")
    for arm, name in (("a", "A 冷启动(--fresh)"), ("b", "B 累积(--memory-dir)")):
        vals = []
        for r in (1, 2, 3):
            run = data.get((arm, r))
            v = cell_score(run) if run else None
            vals.append(v)
            print(f"  {name}  第{r}轮: " + (f"{v:6.1f}/96" if v is not None else "  缺失"))
        got = [v for v in vals if v is not None]
        if len(got) >= 2:
            print(f"    ⇒ 极差 = {max(got) - min(got):.1f} 格   Δ(末−首) = {got[-1] - got[0]:+.1f} 格")

    # ── 逐题对比（B 第1轮 vs 第3轮）──────────────────────────────────────
    print("\n【逐题格分（A 第1轮 / B 第1轮 / B 第3轮）】")
    if all(k in data for k in (("a", 1), ("b", 1), ("b", 3))):
        pa, pb1, pb3 = (per_task_cells(data[("a", 1)]), per_task_cells(data[("b", 1)]),
                        per_task_cells(data[("b", 3)]))
        for tid in sorted(pa):
            d = pb3[tid] - pb1[tid]
            mark = " ⬆" if d > 0.5 else (" ⬇" if d < -0.5 else "")
            print(f"  {tid:<7} A1={pa[tid]:5.1f}  B1={pb1[tid]:5.1f}  B3={pb3[tid]:5.1f}"
                  f"  Δ={d:+5.1f}{mark}")
    else:
        print("  ⚠️ 缺轮次，无法逐题对比")

    # ── P0 前提闸 ───────────────────────────────────────────────────────
    print("\n【P0 前提闸】")
    rb2 = reward_line("b", 2)
    ra2 = reward_line("a", 2)
    seen_b2 = grab(r"读", rb2) if rb2 else None
    seen_a2 = grab(r"读", ra2) if ra2 else None
    print(f"  B 第2轮 奖励行: {rb2 or '(未找到)'}")
    print(f"  A 第2轮 奖励行: {ra2 or '(未找到)'}")

    p0a = (seen_b2 is not None and seen_b2 > 0)
    p0b = (seen_a2 == 0)
    print(f"  P0-a（B 第2轮 读>0）: " + ("✅ 成立" if p0a else
                                      f"❌ 不成立（读={seen_b2}）"))
    print(f"  P0-b（A 第2轮 读=0）: " + ("✅ 成立" if p0b else
                                      f"❌ 不成立（读={seen_a2}）"))
    p0 = p0a and p0b
    print(f"  ⇒ P0 = {'✅ 通过' if p0 else '❌ 未通过 ⇒ P1/P2 一律 None'}")

    # ── P1 / P2 ────────────────────────────────────────────────────────
    print("\n【P1 主判 / P2 安慰剂闸】")
    if not p0:
        print("  P1 = None    P2 = None    （前提不成立，按 PREREG §5 不印 False）")
        return 0

    bv = [cell_score(data[("b", r)]) for r in (1, 2, 3) if ("b", r) in data]
    av = [cell_score(data[("a", r)]) for r in (1, 2, 3) if ("a", r) in data]
    if len(bv) < 3 or len(av) < 3:
        print("  ⚠️ 轮次不全 ⇒ P1 = None")
        return 0

    noise = max(max(av) - min(av), max(bv) - min(bv))
    delta = bv[-1] - bv[0]
    p1 = (delta > noise) and (delta >= BIG_CELLS)
    p2 = (max(av) - min(av)) <= BIG_CELLS / 2

    print(f"  noise_floor（A/B 三轮极差取大）= {noise:.1f} 格")
    print(f"  Δ_B = B3 − B1 = {delta:+.1f} 格")
    print(f"  P1（Δ > noise 且 Δ ≥ +16）: " + ("✅ 成立" if p1 else "❌ 不成立"))
    print(f"  P2（A 三轮极差 ≤ 8 格）:   " + ("✅ 通过" if p2 else "❌ 未通过"))

    # ── R1 / R2 辅助读数（不是判据）─────────────────────────────────────
    print("\n【R1 曲线形状（不是判据）】")
    print(f"  B: {bv[0]:.1f} → {bv[1]:.1f} → {bv[2]:.1f}"
          f"   单调={'是' if bv[0] <= bv[1] <= bv[2] else '否'}"
          f"   边际增益={bv[1] - bv[0]:+.1f} / {bv[2] - bv[1]:+.1f}")
    print(f"  A: {av[0]:.1f} → {av[1]:.1f} → {av[2]:.1f}")

    print("\n【R2 机制激活度（不是判据）】")
    for r in (1, 2, 3):
        line = reward_line("b", r)
        print(f"  B 第{r}轮: 写={grab('写', line)} 读={grab('读', line)} "
              f"挡自己={grab('挡自己', line)}")

    print("\n【判决】")
    if p1 and p2:
        print("  ✅ P1 成立 ⇒ 同族内记忆可复用（⛔ 不可以说『会跨族迁移』，PREREG §9-1）")
    else:
        print("  ❌ P1 不成立 ⇒ 按 PREREG §6：V1 没有累积能力，")
        print("     『AGI 路径』四个字不许再写 —— 除非先证明本轮的装置缺陷。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
