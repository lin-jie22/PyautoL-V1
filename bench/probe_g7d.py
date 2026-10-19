#!/usr/bin/env python3
"""§17-7d 三臂实验的**结论探针**（零模型调用，只读冻结证据）。

要锁的那件事（DESIGN §17-7 ★7d 事前登记，跑臂之前写死；**只能在这里改，且必须同步 DESIGN**）：
    题面**不完备**（判别条件只在记忆里）时，记忆通道能不能把那条判别条件送进决策、
    并让分数动起来。
    ⛔ 本探针测的是**通道容量**，不是学习（H 臂 facts 是手写的）。

判读顺序（写死，R7 先行）：
  R1       每道 heldout 题都必须取回**它自己那条**惯例（只数条数不够：FTS 命中非空时
           不兜底 top-k，条数够但取回别人的惯例等于没送达）；且每臂每 repeat
           跑后 facts 数 == 种子
  **R7**   等长中性扰动效应量 |Δ_P| < 0.15；≥ 0.15 ⇒ **整组作废**（一个结局都不许选）
  R-noise  每臂每题 3 次 repeat 极差 ≤ 0.05；否则整组作废
  R3a      Δ_H ≥ 0.15 且 |Δ_P| < 0.05 ⇒ 认领**容量**
  R3b      Δ_H ≥ 0.15 且 0.05 ≤ |Δ_P| < 0.15 ⇒ 存在性效应，降级
  R3c      Δ_H < 0.15 ⇒ 不认领（R1 全绿时才能读成"送到了但没改变行为"）
  R5       注入保真（只登记）：facts 超过 120 字的条数

用法：
    python3 bench/probe_g7d.py --evidence bench/evidence/knowledge_s32
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
for _p in (str(V1), str(V1 / "bench")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

THRESH_CONTENT = 0.15
THRESH_PLACEBO = 0.05
THRESH_R7 = 0.15
THRESH_NOISE = 0.05
CAP = 120

ARMS = ("H", "P", "E")
REPEATS = (1, 2, 3)
HELDOUT = V1 / "bench" / "heldout" / "g7e_heldout.jsonl"


def _load_scores(ev: Path) -> dict[str, list[dict[str, float]]]:
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
        if any(set(x) != set(reps[0]) for x in reps):
            raise SystemExit(f"❌ 臂 {arm} 三次 repeat 题集不一致")
        out[arm] = reps
    return out


def _check_delivery(ev: Path) -> tuple[bool, list[str]]:
    from build_g7d_seed import FACTS
    from mw.memory import MemoryStore

    tasks = [json.loads(l) for l in HELDOUT.read_text().splitlines() if l.strip()]
    lines: list[str] = []
    ok = True
    seeds = {a: MemoryStore(ev / f"seed_{a}.db") for a in ARMS}
    n_facts = seeds["H"].conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"]

    # 完整性：跑后 facts 必须与种子一致（运行期写入 = 处理被混杂）
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
                         f"种子={n_seed} 跑后={n_fin}")
            fin.close()

    # 送达：own-fact + **两臂等量**（装置修正⑨d：等长只有在条数/字符数都相等时
    # 才等价于"注入量相等"；"H 送得越多越好"是错的判据）
    for t in tasks:
        want = FACTS[t["id"]]
        got = {a: [f.text for f in seeds[a].recall(t["prompt"], k=6)] for a in ARMS}
        own = want in got["H"]
        n_ok = len(got["H"]) == len(got["P"])
        c_ok = sum(len(x) for x in got["H"]) == sum(len(x) for x in got["P"])
        good = own and n_ok and c_ok and len(got["E"]) == 0
        ok &= good
        lines.append(
            f"  {'✅' if good else '❌'} {t['id']:24s} H={len(got['H'])}条"
            f"（本条{'✅' if own else '❌'}）P={len(got['P'])}条 E={len(got['E'])}条"
            f"  注入字符 H={sum(len(x) for x in got['H'])}"
            f" P={sum(len(x) for x in got['P'])}")
    for a in ARMS:
        seeds[a].close()
    return ok, lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--evidence", required=True)
    args = p.parse_args(argv)
    ev = Path(args.evidence).expanduser().resolve()

    reps = _load_scores(ev)
    ids = sorted(reps["H"][0])

    print("== R1 送达（冻结种子库 recall 重放 + own-fact + 完整性）==")
    ok_delivery, d_lines = _check_delivery(ev)
    for l in d_lines:
        print(l)
    if not ok_delivery:
        print("❌ R1 不成立 ⇒ 处理未送达/被混杂，整组作废（登记：不许判读）")
        return 2

    # R5 注入保真（只登记，不判定）
    from mw.memory import MemoryStore
    seed_h = MemoryStore(ev / "seed_H.db")
    fh = [r["text"] for r in
          seed_h.conn.execute("SELECT text FROM facts").fetchall()]
    over = [t for t in fh if len(t) > CAP]
    kept = (sum(min(len(t), CAP) for t in fh) / sum(len(t) for t in fh)) if fh else 1.0
    print(f"== R5 注入保真 ==  facts={len(fh)} 条，> {CAP} 字 {len(over)} 条，"
          f"注入后保留字符 {kept:.1%}（只记录不判定）")
    seed_h.close()

    # R4 逐题全表 + R-noise
    print("== R4 逐题（三臂 × 3 repeats）==")
    max_range = 0.0
    for tid in ids:
        cells = {a: [rep[tid] for rep in reps[a]] for a in ARMS}
        rng = max(max(v) - min(v) for v in cells.values())
        max_range = max(max_range, rng)
        vals = "  ".join(f"{a}={cells[a]}" for a in ARMS)
        flag = "" if rng == 0 else f"  ← repeat 极差 {rng:.4f}"
        print(f"  {tid:24s} {vals}{flag}")
    print(f"== R-noise ==  max_range={max_range:.4f}（阈值 {THRESH_NOISE}）")

    mean = {a: (sum(sum(rep[t] for t in ids) for rep in reps[a])
                / (len(ids) * len(REPEATS))) for a in ARMS}
    d_h = mean["H"] - mean["E"]
    d_p = mean["P"] - mean["E"]
    print(f"== R2 ==  mean: H={mean['H']:.4f} P={mean['P']:.4f} E={mean['E']:.4f}"
          f"  Δ_H={d_h:+.4f}  Δ_P={d_p:+.4f}  |Δ_P|={abs(d_p):.4f}")

    # ── R7 先行闸 ────────────────────────────────────────────────────────
    print(f"== R7 ==  等长中性扰动效应量 |Δ_P|={abs(d_p):.4f}"
          f"（闸 {THRESH_R7}）")
    if abs(d_p) >= THRESH_R7:
        print(f"❌ R7 不成立 ⇒ 本尺子对该效应没有分辨力 ⇒ **整组作废**，"
              f"三种结局一个都不许选（⛔ 不许换阈值重判）")
        return 3
    if max_range > THRESH_NOISE:
        print(f"❌ R-noise={max_range:.4f} > {THRESH_NOISE} ⇒ 整组作废")
        return 4

    if d_h >= THRESH_CONTENT and abs(d_p) < THRESH_PLACEBO:
        print(f"✅ R3a：Δ_H={d_h:+.4f} ≥ {THRESH_CONTENT} 且 |Δ_P|={abs(d_p):.4f} < "
              f"{THRESH_PLACEBO} ⇒ 认领「记忆通道能携带题面缺失的判别条件并改变行为」"
              f"（**容量**主张，不是学习）")
        same = all(reps["H"][0] == reps["H"][i] for i in (1, 2))
        print(f"   附报：H 臂 3 次 repeat {'逐位相同' if same else '不逐位相同'}")
        for tid in ids:
            hm = sum(reps["H"][i][tid] for i in range(3)) / 3
            pm = sum(reps["P"][i][tid] for i in range(3)) / 3
            em = sum(reps["E"][i][tid] for i in range(3)) / 3
            print(f"   逐题：{tid:24s} H={hm:.2f} P={pm:.2f} E={em:.2f}"
                  f"  H−P={hm - pm:+.2f}")
    elif d_h >= THRESH_CONTENT:
        print(f"⚠️ R3b：Δ_H={d_h:+.4f} ≥ {THRESH_CONTENT} 但 |Δ_P|={abs(d_p):.4f} "
              f"∈[{THRESH_PLACEBO},{THRESH_R7}) ⇒ 存在性效应，降级不得认领")
    else:
        print(f"⛔ R3c：Δ_H={d_h:+.4f} < {THRESH_CONTENT} ⇒ 不认领。"
              f"R1 全绿 ⇒ 只能说「送到了但没改变行为」，⛔ 不能说「没送到」")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
