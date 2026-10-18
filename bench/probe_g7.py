#!/usr/bin/env python3
"""§17-7 三臂 × 3 repeats 实验的**结论探针**（零模型调用，只读冻结证据）。

★ 它要锁的那件事（DESIGN §17-7 ★7a，跑臂之前写死；**只能在这里改，且改了必须同步 DESIGN**）
  真实失败史蒸馏出的启发式，能否让**不同的题**（heldout）少犯错。
  三臂（H 纯 abstract facts / P 逐条等长安慰剂 / E 空），heldout 2 题逐题配对，
  每臂 3 次 repeat，T=0。

ⓘ 同一探针服务两轮：★7a（训练/考试**跨族**）与 ★7b（训练/考试**同族**阳性对照）。
  判据逐条相同（同一把尺子），但**判读语义不同** —— 7b 的 R3a 只记「同族可迁移」，
  ⛔ 不构成 7a 翻案（7a 问的是跨族）。二者是两个独立命题，不许合并。

  R1  冻结种子库上重放 heldout goals 的 recall()：H≥1 / P≥1 / E=0；
      且每臂每 repeat 跑后 facts 数 == 种子     ⇒ 不满足 ⇒ 整组作废
  R-noise  每臂每题 3 次 repeat 的极差；记 max_range（纯记录，进 R3 判定）
  R3a  Δ_H ≥ 0.15 且 Δ_P < 0.05 且 max_range ≤ 0.05 ⇒ 认领「知识可执行」
  R3b  Δ_H ≥ 0.15 且（Δ_P ≥ 0.05 或 max_range > 0.05）⇒ 存在性/噪声，降级不得认领
  R3c  Δ_H < 0.15 ⇒ 不认领，如实登记
  R4   逐题三臂 × 3 次并列全表；任何翻转逐题列出
  R5   注入保真（**只登记，不判定**）：`core.py` 拼上下文时 `f.text[:120]`，
       H 臂 facts 里超过 120 字的条数与保留字符比例 ⇒ 只用来解释 R3c
  Δ_H = mean(H) − mean(E)、Δ_P = mean(P) − mean(E)，各在 3 次 repeat 均值上算。

用法：
    python3 bench/probe_g7.py --evidence bench/evidence/knowledge_s29
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V1))

THRESH_CONTENT = 0.15
THRESH_PLACEBO = 0.05
THRESH_NOISE = 0.05

ARMS = ("H", "P", "E")
REPEATS = (1, 2, 3)
HELDOUT = V1 / "bench" / "heldout" / "g7_heldout.jsonl"


def _load_scores(ev: Path) -> dict[str, list[dict[str, float]]]:
    """{arm: [ {task_id: score} × 3 repeats ]}。"""
    out: dict[str, list[dict[str, float]]] = {}
    for arm in ARMS:
        reps: list[dict[str, float]] = []
        for r in REPEATS:
            path = ev / f"{arm}_r{r}_runs.jsonl"
            if not path.exists():
                raise SystemExit(f"❌ 缺证据文件 {path.name}")
            merged: dict[str, float] = {}
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                for t in row["report"]["per_task"]:
                    merged[t["id"]] = float(t["score"])
            if not merged:
                raise SystemExit(f"❌ {path.name} 无 per_task 读数")
            reps.append(merged)
        n = len(reps[0])
        if any(set(x) != set(reps[0]) for x in reps):
            raise SystemExit(f"❌ 臂 {arm} 三次 repeat 题集不一致")
        out[arm] = reps
    return out


def _check_delivery(ev: Path) -> tuple[bool, list[str]]:
    """R1：冻结种子库上离线重放 heldout goals 的 recall() + 完整性。"""
    from mw.memory import MemoryStore
    goals = [json.loads(l)["prompt"]
             for l in HELDOUT.read_text().splitlines() if l.strip()]
    lines: list[str] = []
    ok = True
    seeds = {a: MemoryStore(ev / f"seed_{a}.db") for a in ARMS}
    for a in ARMS:
        n_seed = seeds[a].conn.execute(
            "SELECT COUNT(*) c FROM facts").fetchone()["c"]
        for r in REPEATS:
            fin = MemoryStore(ev / f"final_{a}_r{r}.db")
            n_fin = fin.conn.execute(
                "SELECT COUNT(*) c FROM facts").fetchone()["c"]
            good = n_seed == n_fin
            ok &= good
            lines.append(f"  {'✅' if good else '❌'} {a} r{r} facts "
                         f"种子={n_seed} 跑后={n_fin}（不等 ⇒ 运行期写入混杂）")
    for g in goals:
        n = {a: len(seeds[a].recall(g, k=6)) for a in ARMS}
        good = n["H"] >= 1 and n["P"] >= 1 and n["E"] == 0
        ok &= good
        lines.append(f"  {'✅' if good else '❌'} H={n['H']} P={n['P']} E={n['E']}"
                     f"  goal={g[:32]}…")
    return ok, lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--evidence", required=True)
    args = p.parse_args(argv)
    ev = Path(args.evidence).expanduser().resolve()

    reps = _load_scores(ev)
    ids = sorted(reps["H"][0])

    # ── R1 送达 + 完整性 ────────────────────────────────────────────────
    print("== R1 送达（冻结种子库 recall 重放 + facts 完整性）==")
    ok_delivery, d_lines = _check_delivery(ev)
    for l in d_lines:
        print(l)
    if not ok_delivery:
        print("❌ R1 不成立 ⇒ 处理未送达/被混杂，整组作废（按登记不许判读）")
        return 2

    # ── R5 注入保真（只登记，不判定）─────────────────────────────────────
    # `core.py` 的 `f.text[:120]` 是拼上下文时的硬截断。这里**只报数**：
    # 若启发式长于 120 字，判别条件可能根本没进上下文 —— 那是 R3c 的一种解释，
    # ⛔ 不是改判据的理由（改判据 = 又一根可调旋钮）。
    from mw.memory import MemoryStore
    CAP = 120
    seed_h = MemoryStore(ev / "seed_H.db")
    fh = [r["text"] for r in
          seed_h.conn.execute("SELECT text FROM facts").fetchall()]
    over = [t for t in fh if len(t) > CAP]
    kept = (sum(min(len(t), CAP) for t in fh) / sum(len(t) for t in fh)) if fh else 1.0
    print(f"== R5 注入保真 ==  facts={len(fh)} 条，> {CAP} 字 {len(over)} 条，"
          f"注入后保留字符 {kept:.1%}（单条上限 {CAP}，只记录不判定）")
    for t in over:
        print(f"   ⚠️ len={len(t)} 被截到 {CAP}：{t[:40]}…{t[-20:]}")
    seed_h.close()

    # ── R4 逐题全表 + R-noise ───────────────────────────────────────────
    print("== R4 逐题（三臂 × 3 repeats）==")
    max_range = 0.0
    for tid in ids:
        cells = {a: [rep[tid] for rep in reps[a]] for a in ARMS}
        rng = max(max(v) - min(v) for v in cells.values())
        max_range = max(max_range, rng)
        vals = "  ".join(f"{a}={cells[a]}" for a in ARMS)
        flag = "" if rng == 0 else f"  ← repeat 极差 {rng:.4f}"
        print(f"  {tid:26s} {vals}{flag}")
    print(f"== R-noise ==  max_range={max_range:.4f} "
          f"（阈值 {THRESH_NOISE}）")

    # ── R2 / R3 判读 ────────────────────────────────────────────────────
    mean = {a: (sum(sum(rep[t] for t in ids) for rep in reps[a])
                / (len(ids) * len(REPEATS))) for a in ARMS}
    d_h = mean["H"] - mean["E"]
    d_p = mean["P"] - mean["E"]
    print(f"== R2 ==  mean: H={mean['H']:.4f} P={mean['P']:.4f} E={mean['E']:.4f}"
          f"  Δ_H={d_h:+.4f}  Δ_P={d_p:+.4f}")
    if d_h >= THRESH_CONTENT and d_p < THRESH_PLACEBO and max_range <= THRESH_NOISE:
        print(f"✅ R3a：Δ_H={d_h:+.4f} ≥ {THRESH_CONTENT} 且 Δ_P={d_p:+.4f} < "
              f"{THRESH_PLACEBO} 且 max_range={max_range:.4f} ≤ {THRESH_NOISE} "
              f"⇒ 认领「知识可执行」，§17-7 记正读数")
    elif d_h >= THRESH_CONTENT:
        why = ("Δ_P ≥ 0.05（存在性效应）" if d_p >= THRESH_PLACEBO
               else f"max_range={max_range:.4f} > {THRESH_NOISE}（噪声淹没）")
        print(f"⚠️ R3b：Δ_H={d_h:+.4f} ≥ {THRESH_CONTENT} 但 {why} "
              f"⇒ 降级，不得认领")
    else:
        print(f"⛔ R3c：Δ_H={d_h:+.4f} < {THRESH_CONTENT} ⇒ 不认领，如实登记")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
