#!/usr/bin/env python3
"""§17-6 6c 三臂实验的**结论探针**（零模型调用，只读冻结证据）。

★ 它要锁的那件事
  蒸馏出的启发式进了语义层之后，能不能让**后面**的题少犯错。
  三臂（H 启发式 / P 逐条等长安慰剂 / E 空），heldout 7 题逐题配对，T=0。

  ⛔ 探针不锁结论 —— 它锁的是：「依据这些冻结的数据，按事前登记的规则
     （DESIGN §17-6 ★6c，跑臂之前写死），结论**必须**是什么」。
     数据换一个字节，探针自己会转红。

★ 事前登记的规则（与 DESIGN 同名同值；**只能在这里改，且改了必须同步 DESIGN**）
  R0  mean(E) ≥ 0.95                ⇒ 天花板，实验无法提问（不许读成"启发式无用"）
  R1  H/P 每题送达 ≥1 条、E 恒 0 条   ⇒ 处理已送达；不满足 ⇒ 整组作废
      （证据 = 冻结种子库上离线重放 7 个 goal 的 recall()；
        依据：recall 只依赖 (库, goal)，且运行期 core 不写 facts）
  R2  Δ_H = mean(H) − mean(E)；Δ_P = mean(P) − mean(E)
  R3a Δ_H ≥ 0.15 且 Δ_P < 0.05      ⇒ 认领「知识可执行」
  R3b Δ_H ≥ 0.15 且 Δ_P ≥ 0.05      ⇒ 存在性效应，降级，不得认领
  R3c Δ_H < 0.15                    ⇒ 不认领，§17-6 保持 0
  R4  逐题表三臂并列；任何翻转逐题列出

用法：
    python3 bench/probe_knowledge_exec.py
    python3 bench/probe_knowledge_exec.py --evidence bench/evidence/knowledge_s28
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V1))

DEFAULT_EV = V1 / "bench" / "evidence" / "knowledge_s28"

#: 事前登记的阈值（与 DESIGN §17-6 ★6c 同名同值）
THRESH_CEILING = 0.95
THRESH_CONTENT = 0.15
THRESH_PLACEBO = 0.05

ARMS = ("H", "P", "E")
SUITES = ("struct_convert_heldout", "numeric_logic_heldout")


def _load_scores(ev: Path) -> dict[str, dict[str, float]]:
    """每臂合并两次 suite 调用的 per_task 分数：{task_id: score}。"""
    out: dict[str, dict[str, float]] = {}
    for arm in ARMS:
        rows = [json.loads(l) for l in
                (ev / f"{arm}_runs.jsonl").read_text().splitlines() if l.strip()]
        if not rows:
            raise SystemExit(f"❌ {arm}_runs.jsonl 为空（证据不完整）")
        merged: dict[str, float] = {}
        for r in rows:
            for t in r["report"]["per_task"]:
                merged[t["id"]] = float(t["score"])
        out[arm] = merged
    return out


def _check_delivery(ev: Path) -> tuple[bool, list[str]]:
    """R1：**跑前冻结的种子库**上离线重放全部 goal 的 recall()。

    ⛔ 首跑的教训：错用了跑完后的库 —— 那里面混进了运行期写入（模型调 note），
    测的是污染后的库。修正依据：recall 只依赖 (库, goal)，运行期 core 不写
    facts（note 已被 allow_note=0 关闭，且由完整性核对自证）。
    """
    from mw.memory import MemoryStore
    goals: list[str] = []
    for s in SUITES:
        for l in (V1 / "bench" / "heldout" / f"{s}.jsonl").read_text().splitlines():
            if l.strip():
                goals.append(json.loads(l)["prompt"])
    lines: list[str] = []
    ok = True
    stores = {a: MemoryStore(ev / f"seed_{a}.db") for a in ARMS}
    # 完整性：note 关闭 ⇒ 跑完后 facts 数必须与种子一致（否则处理被混杂）。
    for a in ARMS:
        fin = MemoryStore(ev / f"final_{a}.db")
        n_seed = stores[a].conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"]
        n_fin = fin.conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"]
        good = n_seed == n_fin
        ok &= good
        lines.append(f"  {'✅' if good else '❌'} {a} facts 种子={n_seed} 跑后={n_fin}"
                     f"（不等 ⇒ 运行期写入混杂，整组作废）")
    for g in goals:
        n = {a: len(stores[a].recall(g, k=6)) for a in ARMS}
        good = n["H"] >= 1 and n["P"] >= 1 and n["E"] == 0
        ok &= good
        lines.append(f"  {'✅' if good else '❌'} H={n['H']} P={n['P']} E={n['E']}"
                     f"  goal={g[:36]}…")
    return ok, lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--evidence", default=str(DEFAULT_EV))
    args = p.parse_args(argv)
    ev = Path(args.evidence).expanduser().resolve()

    scores = _load_scores(ev)
    ids = sorted(set(scores["H"]) | set(scores["P"]) | set(scores["E"]))
    if not (set(scores["H"]) == set(scores["P"]) == set(scores["E"])):
        raise SystemExit(f"❌ 三臂题集不一致："
                         f"{ {a: sorted(scores[a]) for a in ARMS} }")
    mean = {a: sum(scores[a].values()) / len(ids) for a in ARMS}

    print(f"题数={len(ids)}  mean: H={mean['H']:.4f}  P={mean['P']:.4f}  E={mean['E']:.4f}")

    # ── R1 送达 ──────────────────────────────────────────────────────────
    print("== R1 送达（冻结种子库 × 7 goal 离线重放 recall()）==")
    ok_delivery, d_lines = _check_delivery(ev)
    for l in d_lines:
        print(l)

    # ── R4 逐题表 ────────────────────────────────────────────────────────
    print("== R4 逐题（T=0，三臂并列）==")
    for tid in ids:
        h, pp, e = scores["H"][tid], scores["P"][tid], scores["E"][tid]
        flag = "" if h == pp == e else "  ← 有翻转"
        print(f"  {tid:28s} H={h:.4f} P={pp:.4f} E={e:.4f}{flag}")

    # ── R0 / R2 / R3 判读 ────────────────────────────────────────────────
    d_h = mean["H"] - mean["E"]
    d_p = mean["P"] - mean["E"]
    print(f"== R2 ==  Δ_H={d_h:+.4f}  Δ_P={d_p:+.4f}")
    if not ok_delivery:
        print("❌ R1 不成立 ⇒ 处理未送达，整组作废（按登记不许判读）")
        return 2
    if mean["E"] >= THRESH_CEILING:
        print(f"⛔ R0：E 臂 mean={mean['E']:.4f} ≥ {THRESH_CEILING} ⇒ "
              f"本套题在天花板，实验无法提问；§17-6 记『需更难题集』，"
              f"不许读成『启发式无用』")
        return 0
    if d_h >= THRESH_CONTENT and d_p < THRESH_PLACEBO:
        print(f"✅ R3a：Δ_H={d_h:+.4f} ≥ {THRESH_CONTENT} 且 "
              f"Δ_P={d_p:+.4f} < {THRESH_PLACEBO} ⇒ 认领「知识可执行」")
    elif d_h >= THRESH_CONTENT:
        print(f"⚠️ R3b：Δ_H={d_h:+.4f} ≥ {THRESH_CONTENT} 但 "
              f"Δ_P={d_p:+.4f} ≥ {THRESH_PLACEBO} ⇒ 存在性效应，降级，不得认领")
    else:
        print(f"⛔ R3c：Δ_H={d_h:+.4f} < {THRESH_CONTENT} ⇒ "
              f"不认领，§17-6 保持 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
