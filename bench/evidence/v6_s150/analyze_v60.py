#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V60 读数：两臂（shell 关 / 开）的**验证动作** —— 只读两臂的产物与日志。

⛔ 本脚本**只读**：不写 `runs.jsonl`、不改仓库任何文件（纪律 8）。
⛔ 不另造探针：输入就是两臂各自落盘的 `runs.jsonl` 与 `core.log`。
   期望值来自 `PREREG.md` 的 P1~P10，其中 P1/P2 的对照值来自
   **V58 已冻结的账本**（`bench/evidence/v6_s148/runs.jsonl`）。

用法：python3 bench/evidence/v6_s150/analyze_v60.py [> readings.txt]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

RUN = Path("/tmp/v60")
V58 = ROOT / "bench" / "evidence" / "v6_s148" / "runs.jsonl"

RE_RUN = re.compile(r"▶ 任务开始: (.*)$")
RE_ROUND = re.compile(r"\[(\d+)\] 通道=(\S+) 候选=(\d+) 种类=(\d+)")
RE_DISPATCH = re.compile(r"↳ (\S+) ok=(True|False)\s*(.*)$")
RE_TAIL = re.compile(r"⏭ 交付成功 ⇒ 本轮剩余 (\d+) 个候选未轮到：(.*)$")
RE_DONE = re.compile(
    r"■ done .*?执行=(\d+)下发/(\d+)成/(\d+)败（交付=(\d+)/步骤=(\d+)/未轮到=(\d+)）\s*尾=(\d+)")
RE_SHELL = re.compile(r"↳ run_shell ok=(True|False)\s*(.*)$")


def load_arm(tag: str) -> dict:
    """读一臂的账本（只取**最后一行**：`--repeat` 只写一行）。"""
    p = RUN / f"bench_{tag}" / "runs.jsonl"
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    r = rows[-1]
    return {"rows": len(rows), "run": r}


def parse_log(tag: str) -> list[dict]:
    """把一臂的内核日志切成 task-run 段。

    ⚠️ 每臂有自己的 `V1_LOG_DIR` ⇒ 不用按时间戳切（V59 那是没得选的退路）。
    """
    p = RUN / f"log_{tag}" / "core.log"
    runs: list[dict] = []
    cur = None
    rnd = None
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = RE_RUN.search(line)
        if m:
            cur = {"goal": m.group(1).strip(), "rounds": []}
            runs.append(cur)
            rnd = None
            continue
        if cur is None:
            continue
        m = RE_ROUND.search(line)
        if m:
            rnd = {"no": int(m.group(1)), "n": int(m.group(3)),
                   "seq": [], "tail": [], "done": None}
            cur["rounds"].append(rnd)
            continue
        if rnd is None:
            continue
        m = RE_DISPATCH.search(line)
        if m:
            rnd["seq"].append(m.group(1))
            continue
        if "✋" in line:
            #: 拦截行不带工具名（§125.8 自踩 3）⇒ 原样记 `<blocked>`，⛔ 不猜。
            why = "tool_misuse" if "tool_misuse" in line else (
                "goal_hijack" if "goal_hijack" in line else "other")
            rnd["seq"].append(f"<blocked:{why}>")
            continue
        m = RE_TAIL.search(line)
        if m:
            rnd["tail"] = re.findall(r"([A-Za-z_][\w:]*)\s*×\s*(\d+)", m.group(2))
            continue
        m = RE_DONE.search(line)
        if m:
            cur["done"] = {"dispatched": int(m.group(1)), "ok": int(m.group(2)),
                           "failed": int(m.group(3)), "delivery": int(m.group(4)),
                           "steps": int(m.group(5)), "unreached": int(m.group(6)),
                           "tail": int(m.group(7))}
    return runs


def key(goal: str) -> str:
    """题身份：题面前 18 字（同题两 rep 用同一个 key）。"""
    return goal[:18]


def first_round(runs: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    seen: dict[str, int] = {}
    for r in runs:
        k = key(r["goal"])
        i = seen.get(k, 0)
        seen[k] = i + 1
        if r["rounds"] and i < 2:
            out[f"{k}#rep{i + 1}"] = list(r["rounds"][0]["seq"])
    return out


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
        print(f"    {p['id'][:22]:24s} rep{p['rep']}  {p['score']:.4f}  {p['why'][:52]}")
    print("  kernel_offer =", json.dumps(r["kernel_offer"], ensure_ascii=False))
    print("  kernel_order =", json.dumps(r["kernel_order"], ensure_ascii=False))
    print("  kernel_shell =", json.dumps(r["kernel_shell"], ensure_ascii=False))
    print("  kernel_exec  =", json.dumps(r["kernel_exec"], ensure_ascii=False))
    return a


def main() -> int:
    print("V60 读数 —— 两臂（A=shell 关 / B=--allow-shell）")
    print("⛔ 两臂 `caliber_sig` 相同是**预期**的：`allow_shell` 不在 `Limits`（§125.6）")

    v58 = json.loads(V58.read_text(encoding="utf-8").splitlines()[0])
    A = show("a")
    B = show("b")

    ka, kb = A["run"]["kernel_shell"], B["run"]["kernel_shell"]
    fa, fb = A["run"]["kernel_offer"], B["run"]["kernel_offer"]
    oa, ob = A["run"]["kernel_order"], B["run"]["kernel_order"]
    ma, mb = A["run"]["measurement"], B["run"]["measurement"]

    print(f"\n{'=' * 74}\n判据对账（P1~P10，见 PREREG §6）\n{'=' * 74}")

    def mark(ok: bool) -> str:
        return "✅" if ok else "❌"

    p1 = (fa["offered"], fa["dispatched"], fa["blocked"], fa["unreached"],
          fa["unaccounted"]) == (30, 18, 4, 8, 0)
    print(f"  {mark(p1)} P1 A 臂漏斗全等 V58：offered={fa['offered']} dispatched={fa['dispatched']}"
          f" blocked={fa['blocked']} unreached={fa['unreached']} unaccounted={fa['unaccounted']}"
          f"（V58 = 30/18/4/8/0）")

    p2 = (oa["rounds"], oa["with_delivery"], oa["pos_hist"], oa["tail_total"],
          oa["tail_survived"]) == (18, 12, {"0": 8, "1": 4}, 8, 0)
    print(f"  {mark(p2)} P2 A 臂顺序全等 V58：rounds={oa['rounds']} with_delivery={oa['with_delivery']}"
          f" pos_hist={oa['pos_hist']} tail_total={oa['tail_total']}"
          f" tail_survived={oa['tail_survived']}")

    p3 = (ka["dispatched"] == 0 and ka["blocked"] == 4 and ka["tasks_with_shell"] == 0
          and ka["rc_zero"] + ka["rc_nonzero"] + ka["rc_unknown"] == 0)
    print(f"  {mark(p3)} P3 A 臂不误记：dispatched={ka['dispatched']} blocked={ka['blocked']}"
          f" tasks_with_shell={ka['tasks_with_shell']}"
          f" rc_zero={ka['rc_zero']} rc_nonzero={ka['rc_nonzero']} rc_unknown={ka['rc_unknown']}"
          f" blocked_by_checkpoint={ka['blocked_by_checkpoint']}")

    p4 = kb["blocked"] < 4
    print(f"  {mark(p4)} P4 B 臂 blocked={kb['blocked']}（A={ka['blocked']}）"
          f" by_checkpoint={kb['blocked_by_checkpoint']}")

    p5 = kb["tasks_with_shell"] >= 2
    print(f"  {mark(p5)} P5 B 臂 tasks_with_shell={kb['tasks_with_shell']} / {kb['tasks']}")

    p6 = kb["dispatched"] >= 4
    print(f"  {mark(p6)} P6 B 臂 dispatched={kb['dispatched']}"
          f"（rc0={kb['rc_zero']} rcN={kb['rc_nonzero']} unknown={kb['rc_unknown']}"
          f" err={kb['err']} rc_gap={kb['rc_gap']}）")

    p7 = ob["rounds"] > 18
    print(f"  {mark(p7)} P7 B 臂 rounds={ob['rounds']}（A={oa['rounds']}）")

    def sc(arm, tid: str) -> list[float]:
        return [p["score"] for p in arm["run"]["report"]["per_task"] if p["id"].startswith(tid)]
    mr_a, mr_b = sc(A, "t04_merge"), sc(B, "t04_merge")
    p8 = mr_b == mr_a == [0.3333, 0.3333]
    print(f"  {mark(p8)} P8 merge_ranges：A={mr_a} B={mr_b}（V58 = [0.3333, 0.3333]）")

    p9 = mb["mean"] >= ma["mean"]
    print(f"  {mark(p9)} P9 mean：A={ma['mean']} B={mb['mean']}  Δ={round(mb['mean'] - ma['mean'], 4)}"
          f"（std A={ma['std']} B={mb['std']}；分辨率 1/12 = 0.0833）")

    fa_r, fb_r = first_round(parse_log("a")), first_round(parse_log("b"))
    same_b = [k for k in fb_r if k.endswith("#rep1") and fb_r.get(k.replace("rep1", "rep2")) == fb_r[k]]
    total_b = len([k for k in fb_r if k.endswith("#rep1")])
    print(f"  {mark(len(same_b) == total_b == 6)} P10 B 臂首轮序列 rep1==rep2：{len(same_b)}/{total_b}")
    for k in sorted(fb_r):
        print(f"      {k:26s} {fb_r[k]}")
    print("      —— A 臂同项（对照）：")
    for k in sorted(fa_r):
        print(f"      {k:26s} {fa_r[k]}")

    print(f"\n{'=' * 74}\n附：Funnel 差分（B − A）\n{'=' * 74}")
    for kk in ("offered", "dispatched", "blocked", "unreached"):
        print(f"  {kk:12s} A={fa[kk]:3d}  B={fb[kk]:3d}")
    print(f"  V58 冻结     offered={v58['kernel_offer']['offered']}"
          f" dispatched={v58['kernel_offer']['dispatched']}"
          f" blocked={v58['kernel_offer']['blocked']}"
          f" unreached={v58['kernel_offer']['unreached']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
