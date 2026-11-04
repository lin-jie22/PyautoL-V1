#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V61 读数：两臂（`V1_VERIFY_EXEC=0` / `=1`）—— **交付闸里的强制验证**。

⛔ 本脚本**只读**：不写 `runs.jsonl`、不改仓库任何文件（纪律 8）。
⛔ 不另造探针：输入就是两臂各自落盘的 `runs.jsonl` 与 `core.log`。
   期望值来自 `PREREG.md` 的 P1~P10，其中 P3/P4/P5 的对照值来自
   **V60 已冻结的产物**（`bench/evidence/v6_s150/` 与 `/tmp/v60/`）。

用法：python3 bench/evidence/v6_s151/analyze_v61.py [> readings.txt]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

RUN = Path("/tmp/v61")
V60 = ROOT / "bench" / "evidence" / "v6_s150"

RE_RUN = re.compile(r"▶ 任务开始: (.*)$")
RE_ROUND = re.compile(r"\[(\d+)\] 通道=(\S+) 候选=(\d+)")
RE_DISPATCH = re.compile(r"↳ (\S+) ok=(True|False)\s*(.*)$")
RE_DONE = re.compile(
    r"■ \w+ .*?执行=(\d+)下发/(\d+)成/(\d+)败（交付=(\d+)/步骤=(\d+)")
RE_GATE = re.compile(r"🧪 自检未过（第 (\d+)/(\d+) 次打回）")


def load_arm(tag: str) -> dict:
    """读一臂的账本（`--repeat` 只写一行 ⇒ 取最后一行）。"""
    p = RUN / f"bench_{tag}" / "runs.jsonl"
    if not p.exists():
        raise SystemExit(f"❌ 没有 {p}（先跑 run_v61.sh {tag}）")
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {"rows": len(rows), "run": rows[-1]}


def parse_log(tag: str) -> list[dict]:
    """把一臂的内核日志切成 task-run 段，抽出**闸打回**的行。"""
    p = RUN / f"log_{tag}" / "core.log"
    runs: list[dict] = []
    cur = None
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = RE_RUN.search(line)
        if m:
            cur = {"goal": m.group(1).strip(), "rounds": 0, "disp": 0,
                   "shell": [], "gates": [], "done": None}
            runs.append(cur)
            continue
        if cur is None:
            continue
        if re.search(r"\[\d+\] 通道=", line):
            cur["rounds"] += 1
        m = RE_DISPATCH.search(line)
        if m:
            cur["disp"] += 1
            if m.group(1) == "run_shell":
                cur["shell"].append((m.group(2), (m.group(3) or "")[:60]))
        m = RE_GATE.search(line)
        if m:
            cur["gates"].append((int(m.group(1)), int(m.group(2))))
        m = RE_DONE.search(line)
        if m:
            cur["done"] = {"dispatched": int(m.group(1)), "ok": int(m.group(2)),
                           "failed": int(m.group(3)), "delivery": int(m.group(4))}
    return runs


def key(goal: str) -> str:
    return goal[:18]


def show(tag: str) -> dict:
    a = load_arm(tag)
    r = a["run"]
    m = r["measurement"]
    print(f"\n{'=' * 74}\n臂 {tag.upper()}  rows={a['rows']}  caliber_sig={r['caliber_sig']}"
          f"  repeat={r['repeat']}\n{'=' * 74}")
    print(f"  solver = {r['solver']}")
    print(f"  mean={m['mean']}  lo={m['lo']}  hi={m['hi']}  std={m['std']}  n={m['n']}")
    print("  逐题：")
    for p in r["report"]["per_task"]:
        print(f"    {p['id'][:22]:24s} rep{p['rep']}  {p['score']:.4f}"
              f"  {p['elapsed_s']:6.1f}s  {p['why'][:60]}")
    for k in ("kernel_verify", "kernel_shell", "kernel_offer", "kernel_order"):
        if k in r:
            print(f"  {k:14s} = " + json.dumps(r[k], ensure_ascii=False))
    return a


def main() -> int:
    print("V61 读数 —— 两臂（A=`V1_VERIFY_EXEC=0` / B=`=1`），其余逐项相同")
    print("⛔ 两臂 `caliber_sig` 应当**不同**：`verify_exec` 在 `Limits` 里（§127）")

    A = show("a")
    B = show("b")
    ra, rb = A["run"], B["run"]
    va, vb = ra.get("kernel_verify") or {}, rb.get("kernel_verify") or {}
    sa, sb = ra.get("kernel_shell") or {}, rb.get("kernel_shell") or {}
    ma, mb = ra["measurement"], rb["measurement"]

    print(f"\n{'=' * 74}\n判据对账（P1~P10，见 PREREG §5）\n{'=' * 74}")

    def mark(ok: bool) -> str:
        return "✅" if ok else "❌"

    p1 = (va.get("example_tasks") == 4 and vb.get("example_tasks") == 4)
    print(f"  {mark(p1)} P1 装置侧 `example_tasks`：A={va.get('example_tasks')} "
          f"B={vb.get('example_tasks')}（押 4/6；t01/t02 无示例）")

    p2 = (va.get("example_runs") == 0 and va.get("example_failed") == 0
          and va.get("exec_err") == 0)
    print(f"  {mark(p2)} P2 A 臂不跑示例判据：example_runs={va.get('example_runs')} "
          f"example_failed={va.get('example_failed')} exec_err={va.get('exec_err')}")

    p3 = (vb.get("example_runs", 0) >= 12 and vb.get("example_failed") == 2)
    print(f"  {mark(p3)} P3 B 臂：example_runs={vb.get('example_runs')} "
          f"example_failed={vb.get('example_failed')}（押 ≥12 与 2）")

    ga = parse_log("a")
    gb = parse_log("b")
    gates_b = [(key(r["goal"]), r["gates"]) for r in gb if r["gates"]]
    p4 = any(g for _, g in gates_b)
    print(f"  {mark(p4)} P4 B 臂被闸打回的题：{[(k, g) for k, g in gates_b] or '无'}"
          f"（押 t04 两 rep）")
    t04 = [p["score"] for p in rb["report"]["per_task"] if p["id"].startswith("t04")]
    t04a = [p["score"] for p in ra["report"]["per_task"] if p["id"].startswith("t04")]
    print(f"      t04 merge_ranges：A={t04a} B={t04}（V60 = [0.3333, 0.3333]）")

    d = round(mb["mean"] - ma["mean"], 4)
    p5 = mb["mean"] > ma["mean"]
    print(f"  {mark(p5)} P5 mean：A={ma['mean']} B={mb['mean']} Δ={d}"
          f"（押**升**，点估 0.9444；分辨率 1/12 = 0.0833）")

    wa = sum(p["elapsed_s"] for p in ra["report"]["per_task"])
    wb = sum(p["elapsed_s"] for p in rb["report"]["per_task"])
    pct = (wb - wa) / wa * 100 if wa else 0
    print(f"  {mark(30 <= pct <= 60)} P6 墙钟：A={wa:.0f}s B={wb:.0f}s（{pct:+.0f}%，押 +30~60%）"
          f" ｜ 轮数 A={sum(r['rounds'] for r in ga)} B={sum(r['rounds'] for r in gb)}")

    t01 = [p["score"] for p in rb["report"]["per_task"] if p["id"].startswith("t01")]
    p7 = t01 == [0.8333, 0.8333]
    print(f"  {mark(p7)} P7 t01（题面无 `==`）：B={t01}（押不动，0.8333×2）")

    p8 = (va.get("exec_err") == 0 and vb.get("exec_err") == 0)
    print(f"  {mark(p8)} P8 `exec_err`：A={va.get('exec_err')} B={vb.get('exec_err')}"
          f"（⛔ 非 0 ⇒ 整轮读数作废）")

    p9 = (ra["caliber_sig"] != "542f9053d19a7090"
          and ra["caliber_sig"] != rb["caliber_sig"])
    print(f"  {mark(p9)} P9 口径：A={ra['caliber_sig']} B={rb['caliber_sig']}"
          f"（押 ≠ `542f9053d19a7090` 且 A≠B）")

    p10 = (sb.get("dispatched") == sa.get("dispatched")
           and bool(sb.get("trace_cmd")) == bool(sa.get("trace_cmd")))
    print(f"  {mark(p10)} P10 shell 是常量：A.dispatched={sa.get('dispatched')} "
          f"B.dispatched={sb.get('dispatched')} ｜ trace_cmd 条数 "
          f"A={len(sa.get('trace_cmd') or [])} B={len(sb.get('trace_cmd') or [])}")
    for tag, s in (("A", sa), ("B", sb)):
        for ln in (s.get("trace_cmd") or [])[:6]:
            print(f"      {tag} {ln}")

    print(f"\n{'=' * 74}\n附：判据集与口径\n{'=' * 74}")
    print(f"  A verify_stats 键：{sorted(va)}")
    print(f"  B verify_stats 键：{sorted(vb)}")
    print(f"  verify_gap A={va.get('verify_gap')} B={vb.get('verify_gap')}（⛔ 必须 0）")
    print(f"  B：runs={vb.get('runs')} passed={vb.get('passed')} failed={vb.get('failed')} "
          f"exhausted={vb.get('exhausted')} example_runs={vb.get('example_runs')} "
          f"example_failed={vb.get('example_failed')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
