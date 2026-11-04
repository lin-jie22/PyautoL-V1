#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V64 读数：两臂（`--temperature 0` / `0.7`）—— **采样是不是病根**。

⛔ 本脚本**只读**：不写 `runs.jsonl`、不改仓库任何文件（纪律 8）。
⛔ 不另造探针：输入就是两臂各自落盘的 `runs.jsonl` 与 `core.log`。
   期望值来自 `PREREG.md` 的 P1~P9；正控来自 **V62 已冻结的产物**
   （`bench/evidence/v6_s152/readings.txt` 的 **B 臂** —— 它与本轮 A 臂同口径：
   `REJECT_CARRY=1` + `T=0`）。

用法：python3 bench/evidence/v6_s153/analyze_v64.py [> readings.txt]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

RUN = Path("/tmp/v64")

RE_RUN = re.compile(r"▶ 任务开始: (.*)$")
RE_GATE = re.compile(r"🧪 自检未过（第 (\d+)/(\d+) 次打回）")
RE_CARRY = re.compile(r"↻ 打回报文进下一轮提问")
RE_DUP_REJ = re.compile(r"⏭ 打回之后重提的答案\*\*逐字相同\*\*")
RE_DIFF = re.compile(r"✎ 重写的答案与被打回那版\*\*不同\*\*")
RE_PASS_AFTER = re.compile(r"✅ 打回之后的重写\*\*过了闸\*\*")


def load_arm(tag: str) -> dict:
    p = RUN / f"bench_{tag}" / "runs.jsonl"
    if not p.exists():
        raise SystemExit(f"❌ 没有 {p}（先跑 run_v64.sh {tag}）")
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
            cur = {"goal": m.group(1).strip(), "rounds": 0, "gates": [],
                   "carry": 0, "dup_rej": 0, "diff": 0, "pass_after": 0}
            runs.append(cur)
            continue
        if cur is None:
            continue
        if re.search(r"\[\d+\] 通道=", line):
            cur["rounds"] += 1
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
    print("V64 读数 —— 两臂（A=`--temperature 0` / B=`0.7`），其余逐项相同（含 REJECT_CARRY=1）")
    print("⛔ 两臂 `caliber_sig` 应当**不同**：`temperature` 在 `Caliber` 里（run.py:1993）")
    print("⛔ 主因变量是 `passed_after`（改了之后能不能过闸），**不是** mean")

    A = show("a")
    B = show("b")
    ra, rb = A["run"], B["run"]
    rwa, rwb = ra.get("kernel_rework") or {}, rb.get("kernel_rework") or {}
    vfa, vfb = ra.get("kernel_verify") or {}, rb.get("kernel_verify") or {}
    ofa, ofb = ra.get("kernel_offer") or {}, rb.get("kernel_offer") or {}
    ma, mb = ra["measurement"], rb["measurement"]
    ga, gb = parse_log("a"), parse_log("b")

    print(f"\n{'=' * 78}\n判据对账（P1~P9，见 PREREG §5）\n{'=' * 78}")

    def mark(ok: bool) -> str:
        return "✅" if ok else "❌"

    # P1 逐字重提应当下降
    p1 = (rwa.get("same", 0) >= 2 and rwb.get("same", 0) == 0)
    print(f"  {mark(p1)} P1 `rework.same`：A={rwa.get('same')} B={rwb.get('same')}"
          f"（押 A≥2、B=0）｜日志『逐字相同』：A={sum(r['dup_rej'] for r in ga)} "
          f"B={sum(r['dup_rej'] for r in gb)}")

    # P2 改写应当上升
    p2 = (rwa.get("diff", 0) == 0 and rwb.get("diff", 0) >= 2)
    print(f"  {mark(p2)} P2 `rework.diff`：A={rwa.get('diff')} B={rwb.get('diff')}"
          f"（押 A=0、B≥2）｜日志『不同』：A={sum(r['diff'] for r in ga)} "
          f"B={sum(r['diff'] for r in gb)}")

    # P3 主判据：改了之后能不能过闸
    p3 = (rwa.get("passed_after", 0) == 0 and rwb.get("passed_after", 0) == 0)
    print(f"  {mark(p3)} P3 `rework.passed_after`（**主判据**）：A={rwa.get('passed_after')} "
          f"B={rwb.get('passed_after')}（押 都=0）｜日志『过了闸』："
          f"A={sum(r['pass_after'] for r in ga)} B={sum(r['pass_after'] for r in gb)}")

    # P4 分数（三值）
    d = round(mb["mean"] - ma["mean"], 4)
    direction = "升" if d > 0 else ("平" if d == 0 else "降")
    p4 = (d == 0)
    print(f"  {mark(p4)} P4 mean：A={ma['mean']} B={mb['mean']} Δ={d} ⇒ **{direction}**"
          f"（押**平**；分辨率 1/12=0.0833）")
    t04a = [p["score"] for p in ra["report"]["per_task"] if p["id"].startswith("t04")]
    t04b = [p["score"] for p in rb["report"]["per_task"] if p["id"].startswith("t04")]
    print(f"      t04 merge_ranges：A={t04a} B={t04b}（V62 B 臂 = 0.3333×2）")

    # P5 轮数
    rounds_a = sum(r["rounds"] for r in ga)
    rounds_b = sum(r["rounds"] for r in gb)
    p5 = rounds_b > rounds_a
    print(f"  {mark(p5)} P5 轮数：A={rounds_a} B={rounds_b}（押 B>A；V62 B 臂 26）")

    # P6 口径
    p6 = (ra["caliber_sig"] != rb["caliber_sig"])
    print(f"  {mark(p6)} P6 口径：A={ra['caliber_sig']} B={rb['caliber_sig']}（押 A≠B）")

    # P7 检测器
    p7 = (rwa.get("rework_gap") == 0 and rwb.get("rework_gap") == 0)
    print(f"  {mark(p7)} P7 `rework_gap`：A={rwa.get('rework_gap')} "
          f"B={rwb.get('rework_gap')}（⛔ 必须 0）")

    # P8 正控：A 臂复现 V62 的 **B 臂**
    p8 = (ma["mean"] == 0.8611 and rwa.get("carried") == 2 and rwa.get("same") == 4
          and rwa.get("diff") == 0 and rounds_a == 26)
    print(f"  {mark(p8)} P8 正控（= V62 B 臂）：mean={ma['mean']}（0.8611）"
          f" carried={rwa.get('carried')}（2） same={rwa.get('same')}（4）"
          f" diff={rwa.get('diff')}（0） rounds={rounds_a}（26）")

    # P9 方差：B 臂同题两 rep 至少一题不同
    by_id: dict[str, list[float]] = {}
    for p in rb["report"]["per_task"]:
        by_id.setdefault(p["id"], []).append(p["score"])
    varied = {k: v for k, v in by_id.items() if len(set(v)) > 1}
    p9 = bool(varied)
    print(f"  {mark(p9)} P9 B 臂逐题方差：{len(varied)} 题两 rep 分数不同"
          f"（押 是）{'｜' + str(varied) if varied else ''}")

    # 附：V63 改名在真跑批里是否还通（若 0 ⇒ 只改了一边，缺席冒充零）
    print(f"\n  改名体检：`example_task_runs` A={vfa.get('example_task_runs')} "
          f"B={vfb.get('example_task_runs')}（⛔ 押 8 = 4 题 × 2 rep；"
          f"若 0 ⇒ 内核侧键名没同步）")
    print(f"            `task_runs_rejected` A={rwa.get('task_runs_rejected')} "
          f"B={rwb.get('task_runs_rejected')}（V62 旧读数 2）")

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

    # 主判据的组合式判决（PREREG §6）—— **按题**，不按全局计数。
    print(f"\n{'=' * 78}\n§6 组合判决（预注册写死在前，但**按题**读）\n{'=' * 78}")
    da, db = rwa.get("diff", 0), rwb.get("diff", 0)
    pa, pb = rwa.get("passed_after", 0), rwb.get("passed_after", 0)
    #
    # ⚠️ 第一版只读全局 `diff` / `passed_after` ⇒ 实测当场给了错判决（已存
    #    `readings_v1_scriptsent.txt`）。错法：B 臂 `rle` 那道题被打回后改对
    #    ⇒ `passed_after=1`，脚本据此喊「T=0 是病根」；而**唯一真错**的
    #    `merge_ranges` 那一段是 `逐字重提 3 次 / 改 0 / 过闸 0`，与 T=0 完全一样。
    #    ⇒ 全局分母把"另一道题"的证据算进了主问题的账上（纪律 7 的第 N 次变体）。
    def _fn(tid: str) -> str:
        return tid.split("_", 1)[1] if "_" in tid else tid

    bad = sorted({p["id"] for p in ra["report"]["per_task"] if p["score"] < 1.0})
    per: dict[str, dict] = {}
    for tid in bad:
        fn = _fn(tid)
        seg = [r for r in gb if fn in r["goal"]]
        per[tid] = {"段数": len(seg),
                    "逐字重提": sum(r["dup_rej"] for r in seg),
                    "改": sum(r["diff"] for r in seg),
                    "过闸": sum(r["pass_after"] for r in seg)}
    print("  真错的题（A 臂 <1.0）在 **B 臂同一道题** 的段里：")
    for tid, v in per.items():
        print(f"    {tid:24s} {v}")

    stuck = [t for t, v in per.items() if v["段数"] and v["改"] == 0 and v["过闸"] == 0]
    solved_bad = [t for t, v in per.items() if v["过闸"] > 0]
    if pb > 0 and not solved_bad:
        print(f"  ⚠️ **全局 `passed_after={pb}` 被另一道题污染**：真错的题 "
              f"{bad} 上过闸=0 ⇒ 判据必须按题读，否则会把『会的题改对了』"
              f"记成『不会的题也会改了』")
    if rwb.get("redelivered", 0) == 0:
        print("  ⚠️ B 臂 `redelivered=0` ⇒ 分母为 0 ⇒ P1~P3 记 UNKNOWN，不记落空")
    elif solved_bad:
        print(f"  ⇒ **T=0 是病根**：真错的题 {solved_bad} 在 T>0 下过闸")
        print("     ⇒ V58~V62 的 Δ=0 都要重新解释；A 路成立")
    elif stuck:
        print(f"  ⇒ **采样不是病根**：真错的题 {stuck} 在 T=0.7 下仍然"
              f"『改了 0 次 / 过闸 0 次』⇒ 与 T=0 同一景象")
        print("     ⇒ A 路（换采样）**死**；剩 B（换模型）/ C（换题）")
    elif db == 0:
        print("  ⛔ **探针与跑批矛盾**：B 臂仍逐字相同 ⇒ 先查装置，不写结论")
        print("     要查的是 `--temperature` 有没有进内核路径的请求体")
    else:
        print(f"  ⚠️ 混合：diff={db} passed_after={pb} ⇒ 逐题看上面的『打回逐条』")
    _ = (ofa, ofb, da, pa)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
