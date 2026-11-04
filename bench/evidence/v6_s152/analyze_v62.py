#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V62 读数：两臂（`V1_REJECT_CARRY=0` / `=1`）—— **打回 → 重写**这条边。

⛔ 本脚本**只读**：不写 `runs.jsonl`、不改仓库任何文件（纪律 8）。
⛔ 不另造探针：输入就是两臂各自落盘的 `runs.jsonl` 与 `core.log`。
   期望值来自 `PREREG.md` 的 P1~P9；对照值来自**V61 已冻结的产物**
   （`bench/evidence/v6_s151/`，其 B 臂与本轮 A 臂**同口径**：`VERIFY_EXEC=1`）。

用法：python3 bench/evidence/v6_s152/analyze_v62.py [> readings.txt]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

RUN = Path("/tmp/v62")
V61 = ROOT / "bench" / "evidence" / "v6_s151"

RE_RUN = re.compile(r"▶ 任务开始: (.*)$")
RE_DISPATCH = re.compile(r"↳ (\S+) ok=(True|False)\s*(.*)$")
RE_DONE = re.compile(
    r"■ \w+ .*?执行=(\d+)下发/(\d+)成/(\d+)败（交付=(\d+)/步骤=(\d+)")
RE_GATE = re.compile(r"🧪 自检未过（第 (\d+)/(\d+) 次打回）")
RE_CARRY = re.compile(r"↻ 打回报文进下一轮提问")
RE_DUP_REJ = re.compile(r"⏭ 打回之后重提的答案\*\*逐字相同\*\*")
RE_DIFF = re.compile(r"✎ 重写的答案与被打回那版\*\*不同\*\*")
RE_PASS_AFTER = re.compile(r"✅ 打回之后的重写\*\*过了闸\*\*")


def load_arm(tag: str) -> dict:
    p = RUN / f"bench_{tag}" / "runs.jsonl"
    if not p.exists():
        raise SystemExit(f"❌ 没有 {p}（先跑 run_v62.sh {tag}）")
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {"rows": len(rows), "run": rows[-1]}


def parse_log(tag: str) -> list[dict]:
    p = RUN / f"log_{tag}" / "core.log"
    if not p.exists():
        raise SystemExit(f"❌ 没有 {p}")
    runs: list[dict] = []
    cur = None
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = RE_RUN.search(line)
        if m:
            cur = {"goal": m.group(1).strip(), "rounds": 0, "disp": 0,
                   "gates": [], "carry": 0, "dup_rej": 0, "diff": 0,
                   "pass_after": 0, "done": None}
            runs.append(cur)
            continue
        if cur is None:
            continue
        if re.search(r"\[\d+\] 通道=", line):
            cur["rounds"] += 1
        if RE_DISPATCH.search(line):
            cur["disp"] += 1
        m = RE_GATE.search(line)
        if m:
            cur["gates"].append((int(m.group(1)), int(m.group(2))))
        if RE_CARRY.search(line):
            cur["carry"] += 1
        if RE_DUP_REJ.search(line):
            cur["dup_rej"] += 1
        if RE_DIFF.search(line):
            cur["diff"] += 1
        if RE_PASS_AFTER.search(line):
            cur["pass_after"] += 1
        m = RE_DONE.search(line)
        if m:
            cur["done"] = {"dispatched": int(m.group(1)), "ok": int(m.group(2)),
                           "failed": int(m.group(3)), "delivery": int(m.group(4))}
    return runs


def show(tag: str) -> dict:
    a = load_arm(tag)
    r = a["run"]
    m = r["measurement"]
    print(f"\n{'=' * 78}\n臂 {tag.upper()}  rows={a['rows']}  caliber_sig={r['caliber_sig']}"
          f"  repeat={r['repeat']}\n{'=' * 78}")
    print(f"  solver = {r['solver']}")
    print(f"  mean={m['mean']}  lo={m['lo']}  hi={m['hi']}  std={m['std']}  n={m['n']}")
    print("  逐题：")
    for p in r["report"]["per_task"]:
        print(f"    {p['id'][:22]:24s} rep{p['rep']}  {p['score']:.4f}"
              f"  {p['elapsed_s']:6.1f}s  {p['why'][:56]}")
    for k in ("kernel_verify", "kernel_rework", "kernel_shell", "kernel_offer"):
        if k in r:
            print(f"  {k:14s} = " + json.dumps(r[k], ensure_ascii=False))
    return a


def main() -> int:
    print("V62 读数 —— 两臂（A=`V1_REJECT_CARRY=0` / B=`=1`），其余逐项相同（含 VERIFY_EXEC=1）")
    print("⛔ 两臂 `caliber_sig` 应当**不同**：`reject_carry` 在 `Limits` 里（§128）")
    print("⛔ 本轮的因变量是『这条边存不存在』，不是分数 —— 分数只作三值方向的旁证")

    A = show("a")
    B = show("b")
    ra, rb = A["run"], B["run"]
    rwa, rwb = ra.get("kernel_rework") or {}, rb.get("kernel_rework") or {}
    vfa, vfb = ra.get("kernel_verify") or {}, rb.get("kernel_verify") or {}
    ofa, ofb = ra.get("kernel_offer") or {}, rb.get("kernel_offer") or {}
    ma, mb = ra["measurement"], rb["measurement"]
    ga, gb = parse_log("a"), parse_log("b")

    print(f"\n{'=' * 78}\n判据对账（P1~P9，见 PREREG §3）\n{'=' * 78}")

    def mark(ok: bool) -> str:
        return "✅" if ok else "❌"

    # P1 送达位置
    p1 = (rwa.get("carried", 0) == 0 and rwb.get("carried", 0) >= 2)
    print(f"  {mark(p1)} P1 `rework.carried`：A={rwa.get('carried')} B={rwb.get('carried')}"
          f"（押 A=0、B≥2）｜日志 `↻ 打回报文…`：A={sum(r['carry'] for r in ga)} "
          f"B={sum(r['carry'] for r in gb)}")

    # P2 照抄
    p2 = rwb.get("same", 0) >= 2
    print(f"  {mark(p2)} P2 `rework.same`（逐字重提）：A={rwa.get('same')} "
          f"B={rwb.get('same')}（押 B≥2）｜日志『逐字相同』："
          f"A={sum(r['dup_rej'] for r in ga)} B={sum(r['dup_rej'] for r in gb)}")

    # P3 改不改
    p3 = (rwb.get("diff", 0) == 0 and rwb.get("passed_after", 0) == 0)
    print(f"  {mark(p3)} P3 `rework.diff`/`passed_after`：A={rwa.get('diff')}/"
          f"{rwa.get('passed_after')} B={rwb.get('diff')}/{rwb.get('passed_after')}"
          f"（押都=0）｜日志『不同』：A={sum(r['diff'] for r in ga)} "
          f"B={sum(r['diff'] for r in gb)}")

    # P4 分数（三值）
    d = round(mb["mean"] - ma["mean"], 4)
    direction = "升" if d > 0 else ("平" if d == 0 else "降")
    p4 = (d == 0)
    print(f"  {mark(p4)} P4 mean：A={ma['mean']} B={mb['mean']} Δ={d} ⇒ **{direction}**"
          f"（押**平**；分辨率 1/12=0.0833）")
    t04a = [p["score"] for p in ra["report"]["per_task"] if p["id"].startswith("t04")]
    t04b = [p["score"] for p in rb["report"]["per_task"] if p["id"].startswith("t04")]
    print(f"      t04 merge_ranges：A={t04a} B={t04b}（V61 B 臂 = 0.3333×2）")

    # P5 轮数
    rounds_a = sum(r["rounds"] for r in ga)
    rounds_b = sum(r["rounds"] for r in gb)
    p5 = (rounds_a == rounds_b) or (rounds_b - rounds_a) <= 2
    print(f"  {mark(p5)} P5 轮数：A={rounds_a} B={rounds_b}"
          f"（押 A=26、B=26~28；V61 B 臂 26）")

    # P6 漏斗残差（不许被我改坏）
    ua, ub = ofa.get("unaccounted"), ofb.get("unaccounted")
    p6 = (ua == 4 and ub == 4)
    print(f"  {mark(p6)} P6 `unaccounted`：A={ua} B={ub}（押都=4，V61 旧读数）"
          f"｜`dup_skipped` 现在有名字了：A={rwa.get('dup_skipped')} "
          f"B={rwb.get('dup_skipped')}")

    # P7 口径
    p7 = (ra["caliber_sig"] != rb["caliber_sig"])
    print(f"  {mark(p7)} P7 口径：A={ra['caliber_sig']} B={rb['caliber_sig']}（押 A≠B）")

    # P8 检测器
    p8 = (rwa.get("rework_gap") == 0 and rwb.get("rework_gap") == 0)
    print(f"  {mark(p8)} P8 `rework_gap`：A={rwa.get('rework_gap')} "
          f"B={rwb.get('rework_gap')}（⛔ 必须 0）")

    # P9 分母
    p9 = (rwa.get("tasks_rejected") == 1 and rwb.get("tasks_rejected") >= 1)
    print(f"  {mark(p9)} P9 `tasks_rejected`：A={rwa.get('tasks_rejected')} "
          f"B={rwb.get('tasks_rejected')}（押 A=1；V61 的 4 次打回全在 t04）")

    print(f"\n{'=' * 78}\n附：打回逐条（按题切段）\n{'=' * 78}")
    for tag, g in (("A", ga), ("B", gb)):
        for r in g:
            if r["gates"] or r["carry"] or r["dup_rej"]:
                print(f"  {tag} {r['goal'][:16]:18s} 轮={r['rounds']:2d} "
                      f"打回={r['gates']} carried={r['carry']} "
                      f"逐字重提={r['dup_rej']} 改={r['diff']} 过闸={r['pass_after']}")

    wa = sum(p["elapsed_s"] for p in ra["report"]["per_task"])
    wb = sum(p["elapsed_s"] for p in rb["report"]["per_task"])
    print(f"\n  墙钟 A={wa:.0f}s B={wb:.0f}s（{((wb - wa) / wa * 100 if wa else 0):+.0f}%）")
    print(f"  校验 A=runs{vfa.get('runs')}/passed{vfa.get('passed')}/failed{vfa.get('failed')}"
          f"  B=runs{vfb.get('runs')}/passed{vfb.get('passed')}/failed{vfb.get('failed')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
