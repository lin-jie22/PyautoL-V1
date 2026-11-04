#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V65 读数：交付提示词去否定化 —— `_ANSWER_SYS` 四连否定 → 一句正向。

⛔ 只读：不写 `runs.jsonl`、不改仓库。
对照臂 = **V64 的 A 臂**（`/tmp/v64/bench_a`，同模型/同 env/同各项，只差 `_ANSWER_SYS` 文本）。
⚠️ `Caliber` 无内核代码哈希 ⇒ 两臂 `caliber_sig` **可能相同**，那是洞的证据而不是"没改"。

用法：python3 bench/evidence/v6_s154/analyze_v65.py [> readings.txt]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

A_DIR = Path("/tmp/v64")          # 旧代码（V64 A 臂）
B_DIR = Path("/tmp/v65run")       # 新代码


def load(base: Path) -> dict:
    p = base / "bench_a" / "runs.jsonl"
    if not p.exists():
        raise SystemExit(f"❌ 没有 {p}")
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return rows[-1]


def log_rounds(base: Path) -> tuple[int, int]:
    p = base / "log_a" / "core.log"
    if not p.exists():
        return 0, 0
    n = r = 0
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if re.search(r"▶ 任务开始", line):
            n += 1
        if re.search(r"\[\d+\] 通道=", line):
            r += 1
    return n, r


def show(tag: str, r: dict) -> None:
    m = r["measurement"]
    print(f"\n{'=' * 78}\n臂 {tag}  sig={r['caliber_sig']}  repeat={r['repeat']}\n{'=' * 78}")
    print(f"  solver = {r['solver']}")
    print(f"  mean={m['mean']}  lo={m['lo']}  hi={m['hi']}  std={m['std']}  n={m['n']}")
    for p in r["report"]["per_task"]:
        print(f"    {p['id'][:22]:24s} rep{p['rep']}  {p['score']:.4f}"
              f"  {p['elapsed_s']:6.1f}s  {p['why'][:50]}")
    for k in ("kernel_verify", "kernel_rework", "kernel_offer"):
        if k in r:
            print(f"  {k:14s} = " + json.dumps(r[k], ensure_ascii=False))


def main() -> int:
    print("V65 读数 —— `_ANSWER_SYS`：四连否定 → 一句正向")
    print("⛔ 对照 = V64 A 臂（同模型/env/各项，只差这句提示词）")
    print("⚠️ 断段只能人工声明：`Caliber` 无内核代码哈希")

    ra, rb = load(A_DIR), load(B_DIR)
    show("A 旧（V64）", ra)
    show("B 新（V65）", rb)

    ma, mb = ra["measurement"], rb["measurement"]
    rwa, rwb = ra.get("kernel_rework") or {}, rb.get("kernel_rework") or {}
    ofa, ofb = ra.get("kernel_offer") or {}, rb.get("kernel_offer") or {}
    _, rounds_a = log_rounds(A_DIR)
    _, rounds_b = log_rounds(B_DIR)

    def sc(r: dict, tid: str) -> list[float]:
        return [p["score"] for p in r["report"]["per_task"] if p["id"] == tid]

    print(f"\n{'=' * 78}\n判据对账（P1~P6，见 PREREG §4）\n{'=' * 78}")

    def mark(ok: bool) -> str:
        return "✅" if ok else "❌"

    t04a, t04b = sc(ra, "t04_merge_ranges"), sc(rb, "t04_merge_ranges")
    p1 = bool(t04b) and min(t04b) >= 0.6667 and max(t04b) > max(t04a)
    print(f"  {mark(p1)} P1 t04：A={t04a} → B={t04b}（押 ≥0.6667 且升）")

    d = round(mb["mean"] - ma["mean"], 4)
    p2 = mb["mean"] >= 0.9167
    print(f"  {mark(p2)} P2 mean：A={ma['mean']} → B={mb['mean']}  Δ={d:+}"
          f"（押 ≥0.9167）")

    c = "升" if d > 0 else ("平" if d == 0 else "降")
    t01a, t01b = sc(ra, "t01_parse_duration"), sc(rb, "t01_parse_duration")
    p3 = min(t01b) >= 0.8333 and max(t01b) >= max(t01a)
    print(f"  {mark(p3)} P3 t01：A={t01a} → B={t01b}（押 平或升，≥0.8333）")

    others = ("t02_dedupe", "t03_commas", "t05_top_k", "t06_rle")
    drop = {t: (sc(ra, t), sc(rb, t)) for t in others if min(sc(rb, t)) < min(sc(ra, t))}
    p4 = not drop
    print(f"  {mark(p4)} P4 其余四题不许掉：{'掉分 ' + str(drop) if drop else '无掉分'}")

    p5 = ra["caliber_sig"] == rb["caliber_sig"]
    print(f"  {mark(p5)} P5 两臂 sig：A={ra['caliber_sig']} B={rb['caliber_sig']}"
          f"（押**相同** ⇒ sig 不能当断段凭据）")

    pa, pb = rwa.get("passed_after", 0), rwb.get("passed_after", 0)
    p6 = pb > pa
    print(f"  {mark(p6)} P6 `passed_after`：A={pa} → B={pb}（押 升）")

    print(f"\n{'=' * 78}\n附\n{'=' * 78}")
    print(f"  rework：A carried={rwa.get('carried')} same={rwa.get('same')} "
          f"diff={rwa.get('diff')} passed_after={rwa.get('passed_after')} "
          f"dup={rwa.get('dup_skipped')}")
    print(f"          B carried={rwb.get('carried')} same={rwb.get('same')} "
          f"diff={rwb.get('diff')} passed_after={rwb.get('passed_after')} "
          f"dup={rwb.get('dup_skipped')}")
    print(f"  轮数（日志）：A={rounds_a} B={rounds_b}"
          f"｜offer.rounds：A={ofa.get('rounds')} B={ofb.get('rounds')}")
    wa = sum(p["elapsed_s"] for p in ra["report"]["per_task"])
    wb = sum(p["elapsed_s"] for p in rb["report"]["per_task"])
    print(f"  墙钟：A={wa:.0f}s B={wb:.0f}s（{(wb - wa) / wa * 100:+.0f}%）")

    print(f"\n{'=' * 78}\n§5 组合判决（PREREG 写死，不事后编）\n{'=' * 78}")
    if not t04b or min(t04b) == 0.3333:
        print("  ⛔ **探针与跑批矛盾**：t04 没变 ⇒ 先查收尾是否真用 `_ANSWER_SYS`（日志第 22 行"
              " 已证明用），不写结论")
    elif min(t04b) >= 1.0 and d >= 0.1111:
        print(f"  ⇒ 探针与跑批**一致**：t04 {t04a}→{t04b}、mean Δ={d:+}"
              f" ⇒ 改法成立，**V58~V65 第一次正 Δ**")
    elif min(t04b) >= 0.6667:
        print(f"  ⇒ 部分兑现（t04 {t04a}→{t04b}，Δ={d:+}）⇒ 收尾路径还有**第二层**约束，继续分层")
    if drop:
        print(f"  ⚠️ 有掉分：{drop} ⇒ 净增益要重算（V64 的教训：改善一题坏另一题）")
    _ = (c,)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
