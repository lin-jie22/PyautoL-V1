#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V59 读数：候选顺序从哪来 —— 只读 `logs/core.log` 的最后 12 个任务段。

⛔ 本脚本**只读**：不写回 `logs/`、不写回 `runs.jsonl`、不改仓库任何文件
   （纪律 8：证据脚本不许写回自己的证据路径）。输出走 stdout。

⛔ 不另造探针/尺子：输入就是 V58 已经冻结的两个产物
   ① `bench/evidence/v6_s148/runs.jsonl`（账本）
   ② `logs/core.log`（内核日志，追加式；本轮取**最后 12 个** `▶ 任务开始` 段）

口径（见 PREREG §3）：主分母 = **轮**；题身份用 **task-run**。
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))   # 直接跑脚本时 cwd 不在 sys.path（只有脚本目录在）
LOG = ROOT / "logs" / "core.log"
RUNS = ROOT / "bench" / "evidence" / "v6_s148" / "runs.jsonl"

RE_RUN = re.compile(r"▶ 任务开始: (.*)$")
RE_ROUND = re.compile(r"\[(\d+)\] 通道=(\S+) 候选=(\d+) 种类=(\d+)")
RE_DISPATCH = re.compile(r"↳ (\S+) ok=(True|False)\s*(.*)$")
RE_TAIL = re.compile(r"⏭ 交付成功 ⇒ 本轮剩余 (\d+) 个候选未轮到：(.*)$")
RE_DONE = re.compile(r"■ done .*?执行=(\d+)下发/(\d+)成/(\d+)败（交付=(\d+)/步骤=(\d+)/未轮到=(\d+)）"
                     r"\s*尾=(\d+)")
RE_TS = re.compile(r"^(\d\d:\d\d:\d\d)\s")
#: `⏭` 行的尾：`note×1、run_shell×1`；**必须按顺序展开**（集合会丢掉"谁在前"）。
RE_TAIL_ITEM = re.compile(r"([A-Za-z_][\w:]*)\s*×\s*(\d+)")


def parse_log() -> list[dict]:
    """把日志切成 task-run 段，返回最后 12 段（= V58 那次跑批）。"""
    runs: list[dict] = []
    cur = None
    rnd = None
    for raw in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.rstrip("\n")
        m = RE_RUN.search(line)
        if m:
            cur = {"goal": m.group(1).strip(), "rounds": [], "done": None,
                   "ts0": (RE_TS.match(line).group(1) if RE_TS.match(line) else "")}
            runs.append(cur)
            rnd = None
            continue
        if cur is None:
            continue
        m = RE_ROUND.search(line)
        if m:
            rnd = {"no": int(m.group(1)), "n": int(m.group(3)),
                   "channel": m.group(2), "events": [], "tail": [], "done": None}
            cur["rounds"].append(rnd)
            continue
        m = RE_DISPATCH.search(line)
        if m and rnd is not None:
            rnd["events"].append({"kind": "dispatch", "tool": m.group(1),
                                  "ok": m.group(2) == "True", "intent": m.group(3).strip()})
            continue
        if "✋" in line and rnd is not None:
            # ⚠️ 拦截行**不带工具名**（只写 guard 原因）⇒ 只知道"这里占了一个候选位"。
            rnd["events"].append({"kind": "blocked", "why": line.strip()})
            continue
        m = RE_TAIL.search(line)
        if m and rnd is not None:
            items = []
            for name, k in RE_TAIL_ITEM.findall(m.group(2)):
                items.extend([name] * int(k))
            rnd["tail"] = items
            continue
        m = RE_DONE.search(line)
        if m and cur is not None:
            cur["done"] = {"dispatched": int(m.group(1)), "ok": int(m.group(2)),
                           "failed": int(m.group(3)), "delivery": int(m.group(4)),
                           "steps": int(m.group(5)), "unreached": int(m.group(6)),
                           "tail": int(m.group(7))}
            rnd = None
            continue
    return runs[-12:]


DELIVERY = ("finish",)   # 本轮 `--no-external` ⇒ 交付候选只有 finish（spec.produces_answer 无）


def delivery_pos(rnd: dict) -> int:
    """第一个**交付候选**在候选列表里的下标；-1 = 本轮没有交付候选。

    ⚠️ 下标是**执行器视角**（`cands` 已过准入与 `n` 截断）——拦截的候选仍占位。
    """
    for i, ev in enumerate(rnd["events"]):
        if ev["kind"] == "dispatch" and ev["tool"] in DELIVERY:
            return i
    return -1


def plan_seq(rnd: dict) -> list[str]:
    """整轮计划序列 = 已执行的（按顺序）+ 交付后的尾（按顺序）。

    ⛔ 被拦截的候选记作 `<blocked>`：日志里拿不到工具名，**不许猜**。
    """
    seq = [(ev["tool"] if ev["kind"] == "dispatch" else "<blocked>") for ev in rnd["events"]]
    return seq + list(rnd["tail"])


def main() -> int:
    assert LOG.exists(), f"缺 {LOG}"
    ledger_row = json.loads(RUNS.read_text(encoding="utf-8").strip().splitlines()[-1])
    KO = ledger_row["kernel_order"]
    KF = ledger_row["kernel_offer"]

    runs = parse_log()
    print("=" * 78)
    print("V59 读数 —— 候选顺序从哪来（只读 V58 冻结产物）")
    print("=" * 78)
    print(f"日志段：最后 {len(runs)} 个 task-run（{runs[0]['ts0']} 起）")

    # ── 逐格明细 ────────────────────────────────────────────────────────
    print("\n--- 逐格明细（12 个 task-run）---")
    per: list[dict] = []
    for i, r in enumerate(runs):
        ident = r["goal"][:14].replace("写一个函数 ", "")
        rounds = []
        for rnd in r["rounds"]:
            p = delivery_pos(rnd)
            seq = plan_seq(rnd)
            assert len(seq) == rnd["n"], f"{ident} r{rnd['no']}: 序列 {len(seq)} ≠ 候选 {rnd['n']}"
            rounds.append({"no": rnd["no"], "n": rnd["n"], "pos": p,
                           "seq": seq, "tail": rnd["tail"],
                           "n_dispatch": sum(1 for e in rnd["events"]
                                             if e["kind"] == "dispatch"),
                           "blocked": sum(1 for e in rnd["events"] if e["kind"] == "blocked"),
                           "intents": [e.get("intent", "") for e in rnd["events"]]})
        per.append({"i": i, "task": ident, "goal": r["goal"],
                    "rep": 1 if i < 6 else 2,
                    "rounds": rounds, "done": r["done"]})
        print(f"\n[{i + 1:02d}] {ident}  (rep{per[-1]['rep']})  轮数={len(rounds)}")
        for rr in rounds:
            tail = ("  尾=" + "、".join(rr["tail"])) if rr["tail"] else ""
            blk = f"  拦={rr['blocked']}" if rr["blocked"] else ""
            print(f"    r{rr['no']} n={rr['n']} pos={rr['pos']:>2}  {rr['seq']}{tail}{blk}")
            if rr["intents"]:
                print(f"         intent: {rr['intents']}")

    # ── P1 自校验：与冻结账本四项全等 ────────────────────────────────────
    all_rounds = [rr for p in per for rr in p["rounds"]]
    hist = Counter(str(rr["pos"]) for rr in all_rounds if rr["pos"] >= 0)
    tail_all = [t for rr in all_rounds for t in rr["tail"]]
    tail_tool = Counter(tail_all)
    offered = sum(rr["n"] for rr in all_rounds)
    #: 下发数按 `↳` 行数（= 真执行过）算；`■` 行的 `执行=N下发` 作为第二源交叉核对。
    dispatched = sum(rr["n_dispatch"] for rr in all_rounds)
    dispatched_done = sum(p["done"]["dispatched"] for p in per if p["done"])
    blocked = sum(rr["blocked"] for rr in all_rounds)
    unreached = sum(len(rr["tail"]) for rr in all_rounds)
    delivery_rounds = sum(1 for rr in all_rounds if rr["pos"] >= 0)

    print("\n--- P1 自校验：我的解析 vs 冻结账本 ---")
    checks = [
        ("pos_hist", dict(hist), KO["pos_hist"]),
        ("tail_total", unreached, KO["tail_total"]),
        ("tail_by_tool", dict(tail_tool), KO["tail_by_tool"]),
        ("offered", offered, KF["offered"]),
        ("dispatched", dispatched, KF["dispatched"]),
        ("（第二源）■行下发合计", dispatched_done, KF["dispatched"]),
        ("blocked", blocked, KF["blocked"]),
        ("rounds", len(all_rounds), KO["rounds"]),
        ("tasks", len(per), KO["tasks"]),
    ]
    ok_all = True
    for name, mine, led in checks:
        ok = mine == led
        ok_all &= ok
        print(f"  {name:14} 解析={mine!s:28} 账本={led!s:28} {'✅' if ok else '⛔'}")

    # ── M1 目录序 ───────────────────────────────────────────────────────
    from mw.tools import ToolRegistry, register_core_tools, catalog_sha  # noqa: E402

    class _Mem:  # 只为让 note/recall 注册，形状与 bench 一致（memory 非 None）
        pass

    reg = ToolRegistry()
    register_core_tools(reg, memory=_Mem())
    names = reg.names()
    print("\n--- M1 目录序（静态）---")
    print(f"  catalog_sha(no external) = {catalog_sha(wire_external=False)}")
    print(f"  names() = {names}  ⇒ finish 下标 = {names.index('finish')}")

    # ── M2 H1 判据：pos>0 的轮 ─────────────────────────────────────────
    pos_positive = [rr for rr in all_rounds if rr["pos"] > 0]
    print("\n--- M2 H1（照抄目录序）判据 ---")
    print(f"  交付轮 = {delivery_rounds}；pos>0 的轮 = {len(pos_positive)}")
    print(f"  pos>0 轮的「首位工具」 = {[rr['seq'][0] for rr in pos_positive]}")
    print(f"  ⇒ H1 {'不成立（顺序不是抄目录）' if pos_positive else '成立（全部排第一）'}")

    # ── M4 兜底识别 ─────────────────────────────────────────────────────
    FB = ("先在记忆中查找与目标相关的既有结论", "信息不足，直接给出当前最优答复")
    fb_rounds = [rr for rr in all_rounds if any(i.startswith(FB[0]) or i.startswith(FB[1])
                                                for i in rr["intents"])]
    print("\n--- M4 兜底/降级（_fallback 的两条字面量 intent）---")
    print(f"  命中兜底的轮 = {len(fb_rounds)}  {[(r['no'], r['seq']) for r in fb_rounds]}")
    print(f"  其中 pos>0 的 = {sum(1 for r in fb_rounds if r['pos'] > 0)}"
          f"（结构性预期 0：_fallback 只产单步）")

    # ── M5 pos=0 那 8 格的形状 ──────────────────────────────────────────
    p0 = [rr for rr in all_rounds if rr["pos"] == 0]
    print("\n--- M5 pos=0 的 8 格：n 分布与题覆盖 ---")
    print(f"  n 分布 = {dict(Counter(rr['n'] for rr in p0))}；n=1 的轮数 = "
          f"{sum(1 for rr in p0 if rr['n'] == 1)}")
    print(f"  n≥2 的轮数 = {sum(1 for rr in p0 if rr['n'] >= 2)}（想到了别的但排在 finish 后面）")
    ids_p0 = [p["task"] for p in per for rr in p["rounds"] if rr["pos"] == 0]
    print(f"  题覆盖 = {sorted(set(ids_p0))}（{len(set(ids_p0))} 个）")

    # ── M6 同题两 rep ───────────────────────────────────────────────────
    print("\n--- M6 同题两 rep 的计划序列是否相同（T=0）---")
    same = 0
    for a, b in zip(per[:6], per[6:]):
        sa = [(rr["n"], rr["pos"], tuple(rr["seq"])) for rr in a["rounds"]]
        sb = [(rr["n"], rr["pos"], tuple(rr["seq"])) for rr in b["rounds"]]
        eq = sa == sb
        same += eq
        print(f"  {a['task']:14} {'相同' if eq else '不同'}  {sa} vs {sb}")
    print(f"  ⇒ 相同 {same}/6")

    # ── M7 n 分布 ──────────────────────────────────────────────────────
    print("\n--- M7 18 轮的 n 分布 ---")
    print(f"  {dict(sorted(Counter(rr['n'] for rr in all_rounds).items()))}；"
          f"n=1 的轮数 = {sum(1 for rr in all_rounds if rr['n'] == 1)}")

    # ── M8 结论所需的分母 ──────────────────────────────────────────────
    print("\n--- M8 交付轮 / 非交付轮 ---")
    print(f"  交付轮 = {delivery_rounds}；非交付轮 = {len(all_rounds) - delivery_rounds}"
          f"（pos=-1 的 = {sum(1 for rr in all_rounds if rr['pos'] < 0)}）")

    # ── M9 尾里的动作能不能改变答案正确性（分类是判断，数出来的是事实）────
    print("\n--- M9 尾的 8 个候选：能否改变答案正确性 ---")
    print(f"  逐项 = {tail_all}")
    print(f"  验证类（run_shell，真跑一遍能发现错） = {sum(1 for t in tail_all if t == 'run_shell')}")
    print(f"  非验证类（recall/note，检索与记录，不会让答案变对） = "
          f"{sum(1 for t in tail_all if t in ('recall', 'note'))}")

    # ── M3 / P8 提示词许可（静态逐字）────────────────────────────────────
    from mw import planner as _pl  # noqa: E402

    LIC = "目标只是要一段代码或一段纯文本（不需读外部材料）时不必绕路，直接 finish"
    print("\n--- M3 P8 提示词许可（静态、逐字）---")
    print(f"  _SYS 含许可句（逐字） = {LIC in _pl._SYS}")
    print(f"  许可句 = 「{LIC}」")
    #: 许可句的**适用面**：本题集几句题面写着"只输出代码"（= 许可说"不必绕路"的那种）。
    #: ⚠️ 必须读 `bench/tasks/coding_small.jsonl`，⛔ 不许从日志里数：
    #:   日志的 `▶ 任务开始` 行是**截断**的（前 ~100 字），按它数会把"被截掉"
    #:   当成"没有"（缺席冒充零的又一形态）。
    suite = [json.loads(l) for l in
             (ROOT / "bench" / "tasks" / "coding_small.jsonl").read_text(
                 encoding="utf-8").splitlines() if l.strip()]
    print(f"  6 题里题面含「只输出代码」的 = "
          f"{sum(1 for r in suite if '只输出代码' in (r.get('prompt') or ''))}/{len(suite)}"
          f"（源：bench/tasks/coding_small.jsonl）")
    #: 违背许可的题：首位候选被拦（= 模型在"只要一段代码"的题上仍然先要跑一遍）。
    dis = sorted({p["task"] for p in per for rr in p["rounds"] if rr["pos"] > 0})
    print(f"  在这句许可下仍然先提 run_shell 的题 = {dis}（{len(dis)}/6）")
    print(f"  ⇒ 许可**不是**决定性的：同一句许可，{len(dis)}/6 题的模型照样分叉。")
    print("  ⚠️ 它是**许可**（何时可以直接交），不是**条件**（何时不可以）—— "
          "V53 已量过：许可有效、条件几乎无效。")

    # ── 分数对照（只读 per_task，用来说明"被拦的那两题"发生了什么）──────
    print("\n--- 附：逐题得分（runs.jsonl report.per_task，两次 rep 并列）---")
    sc: dict[str, list] = {}
    for t in ledger_row["report"]["per_task"]:
        sc.setdefault(t["id"], []).append((t["rep"], t["score"]))
    for k, v in sc.items():
        print(f"  {k:20} {sorted(v)}")

    print("\nP1 全等 =", ok_all)
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
