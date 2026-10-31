#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V28 判决：把「记账泄漏」那条边拆掉之后，零点校准成功了吗？

只读落盘证据，不起模型。
判据写死在 PREREG（`bench/evidence/v6_s118/PREREG.md`），本脚本**只执行不解释**。

⚠️ 判决与真相分开写（V25 的教训，§89.8-2）：字面判决只按预注册取第一条命中，
   逐格归因另开一节 —— 两者不一致时，**如实并列，不改判据**。

用法：analyze_v28.py [工作目录，默认 /tmp/v28]
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent.parent.parent
BLOCKS = ("[目标]", "[工作记忆]", "[语义记忆]", "[外部评审]", "[轨迹]", "[第")
ID = re.compile(r"b#(\d+)")


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def load_states(w: Path, tag: str) -> dict[tuple[str, int], str]:
    p = w / f"states_{tag}.jsonl"
    out: dict[tuple[str, int], str] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        out[(str(d["task"]), int(d["it"]))] = str(d["state"])
    return out


def load_scores(w: Path, tag: str) -> dict[str, float]:
    p = w / f"bench_{tag}" / "runs.jsonl"
    if not p.exists():
        return {}
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not rows:
        return {}
    per = rows[-1]["report"]["per_task"]
    return {str(t["id"]): float(t.get("score") or 0) * 16 for t in per}


def load_sha(w: Path, tag: str) -> dict[str, str]:
    p = w / f"bench_{tag}" / "answers.jsonl"
    out: dict[str, str] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        x = json.loads(line)
        a = str(x.get("answer") or "")
        out[str(x.get("task_id"))] = sha(a)
    return out


def block_of(lines: list[str], i: int) -> str:
    for j in range(i, -1, -1):
        if lines[j].startswith(BLOCKS):
            return lines[j].split(" ")[0].split("：")[0]
    return "(无块)"


def first_diff(a: str, b: str) -> tuple[int, str, str, str]:
    la, lb = a.splitlines(), b.splitlines()
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else "(无此行)"
        y = lb[i] if i < len(lb) else "(无此行)"
        if x != y:
            return i, block_of(la, i) if i < len(la) else block_of(lb, i), x, y
    return -1, "", "", ""


def min_ref(states: dict[tuple[str, int], str]) -> int | None:
    best = None
    for s in states.values():
        for m in ID.finditer(s):
            n = int(m.group(1))
            best = n if best is None else min(best, n)
    return best


def main(argv: list[str]) -> int:
    w = Path(argv[1] if len(argv) > 1 else "/tmp/v28")
    print(f"===== V28 治污（句柄 → 本会话内序）  {w} =====")

    tags = ["s12_r1", "s12_r2", "d2_r1", "d2_r2", "f2_r1", "f2_r2"]
    S = {t: load_states(w, t) for t in tags}
    SC = {t: load_scores(w, t) for t in tags}
    SH = {t: load_sha(w, t) for t in tags}

    tasks: list[str] = []
    for t in tags:
        for tid in SC[t]:
            if tid not in tasks:
                tasks.append(tid)
    tasks.sort()

    print("\n--- ① 读数总览（得分 = 逐题 ×16）---")
    print(f"{'task':6s}" + "".join(f"{t:>9s}" for t in tags))
    for tid in tasks:
        print(f"{tid:6s}" + "".join(f"{SC[t].get(tid, float('nan')):9.0f}" for t in tags))
    print(f"{'Σ':6s}" + "".join(f"{sum(SC[t].values()):9.0f}" for t in tags))
    print(f"{'state步':6s}" + "".join(f"{len(S[t]):9d}" for t in tags))

    print("\n--- ② 交付件指纹（同指纹 = 逐字节同一份答案）---")
    print(f"{'task':6s}" + "".join(f"{t:>9s}" for t in tags))
    for tid in tasks:
        print(f"{tid:6s}" + "".join(f"{SH[t].get(tid, '-'):>9s}" for t in tags))

    refmin = {t: min_ref(S[t]) for t in tags}
    print("\n--- ③ 最小句柄号（治污生效的**直接**观测）---")
    for t in tags:
        v = refmin[t]
        flag = "?" if v is None else ("✅ b#1" if v == 1 else f"❌ b#{v}")
        print(f"  {t:7s} 最小句柄 {flag}   （期望 b#1；`b#12` ⇒ 预置把行号推过去了、内核没扣基线）")

    # ---------------- 前提闸 ----------------
    print("\n--- ④ 前提闸（PREREG §2.0）---")
    gate_missing = [t for t in tags if not S[t]]
    print(f"  P0-d state-dump 缺失的臂：{gate_missing if gate_missing else '无'}")
    if gate_missing:
        print("\n  ⛔ 取证方式本身缺文件 ⇒ 不出判决（不是 J0/J4）。")
        return 2

    # ---------------- J4：装置没生效 ----------------
    bad_ref = [t for t in tags if refmin[t] != 1]
    j4 = bool(bad_ref)
    print(f"  H0-e 口径闸：最小句柄 != b#1 的臂 = {bad_ref if bad_ref else '无'}")

    # ---------------- J0：s12 可重复 ----------------
    keys = sorted(set(S["s12_r1"]) | set(S["s12_r2"]))
    s12_diff = [k for k in keys if S["s12_r1"].get(k) != S["s12_r2"].get(k)]
    print(f"  J0 s12 两次独立冷启动：共同键 {len(set(S['s12_r1']) & set(S['s12_r2']))}，"
          f"state 不同 {len(s12_diff)} 个")

    # ---------------- f2 正控 ----------------
    keys = sorted(set(S["f2_r1"]) | set(S["f2_r2"]))
    f2_diff = [k for k in keys if S["f2_r1"].get(k) != S["f2_r2"].get(k)]
    print(f"  f2 正控（每轮重置）state 不同 {len(f2_diff)} 个；"
          f"Σ {sum(SC['f2_r1'].values()):.0f} / {sum(SC['f2_r2'].values()):.0f}")

    # ---------------- d2 校准组 ----------------
    print("\n--- ⑤ 校准组 d2（累积 · echo=0）---")
    keys = sorted(set(S["d2_r1"]) | set(S["d2_r2"]))
    d2_diff = [k for k in keys if S["d2_r1"].get(k) != S["d2_r2"].get(k)]
    d2_score_changed = [tid for tid in tasks if SC["d2_r1"].get(tid) != SC["d2_r2"].get(tid)]
    print(f"  Σ {sum(SC['d2_r1'].values()):.0f} → {sum(SC['d2_r2'].values()):.0f}；"
          f"分数变化的题 {d2_score_changed if d2_score_changed else '无'}")
    print(f"  state 不同的 (task, it) 共 {len(d2_diff)} 个")
    for k in d2_diff[:12]:
        a, b = S["d2_r1"].get(k, ""), S["d2_r2"].get(k, "")
        i, blk, x, y = first_diff(a, b)
        print(f"    ✗ {k[0]} 步{k[1]}  len {len(a)}→{len(b)}  首差异行 {i} 块 {blk}")
        print(f"        r1: {x[:190]}")
        print(f"        r2: {y[:190]}")

    # ---------------- 逐格归因（与判决分开）----------------
    print("\n--- ⑥ 逐格归因（判决之外的事实，V25 教训）---")
    for tid in tasks:
        cells = []
        for t in ("s12_r1", "s12_r2", "d2_r1", "d2_r2", "f2_r1", "f2_r2"):
            a, b = SH[t].get(tid), SH["d2_r1"].get(tid)
            cells.append(f"{t}:{a}{'' if a == b else '*'}")
        print(f"  {tid:6s} " + "  ".join(cells))
    print("  （`*` = 与 d2_r1 的交付件**不同**）")

    s12_sum = [sum(SC[t].values()) for t in ("s12_r1", "s12_r2")]

    # ---------------- 判决 ----------------
    print("\n" + "=" * 66)
    print("判据（PREREG §2，按顺序取**第一条**命中；改判据 = 违规）")
    if j4:
        print(f"  ⇒ J4 装置没生效：这些臂的最小句柄不是 b#1 → {bad_ref}")
        print("     ⇒ 本轮作废。⛔ 不许读成 J1/J2（那是把「没做」当成「没效果」）。")
    elif s12_diff:
        print(f"  ⇒ J0 装置不可重复：s12 两次独立冷启动，{len(s12_diff)} 个 state 不同")
        print("     ⇒ 本轮不出归因。")
    elif s12_sum == [48.0, 48.0]:
        print(f"  ⇒ JB 治污成立：s12 两次都 {s12_sum[0]:.0f}（e1wi="
              f"{SC['s12_r1'].get('e1wi', 0):.0f}），最小句柄 b#1。")
        print("     ⇒ 「库里有 11 条历史」不再能改动 prompt ⇒ 那条与语义无关的因果边被拆掉了。")
        print("     ⇒ 那 §90 的「字符级因果」怎么解释？两条都成立：改之前 `b#12` **确实**"
              "能翻转这一格；改之后它不再随库变大而变。")
    elif s12_sum == [64.0, 64.0]:
        print("  ⇒ JC 治污生效但不够：s12 两次都 64，而最小句柄是 b#1")
        print("     ⇒ +16 不只来自编号 ⇒ §90 的 J1 要收窄（`b#12` 必要不充分）。")
        print("     ⚠️ 这时 c2z 那种累积臂与 s12 的差别必须重新查（不许沿用旧归因）。")
    else:
        print(f"  ⇒ 未预注册的分支：s12 两次 = {s12_sum}（既非 48 也非 64 且无 J4/J0）")
        print("     ⇒ 如实登记，不硬套 J 号。")

    print("\n判据（第二组：d2 校准组）")
    if d2_diff:
        print(f"  ⇒ JF 归因未完成：还有 {len(d2_diff)} 个 (task,it) 的 state 在变")
        print("     ⇒ 必须逐行指出**还有哪条边**在泄漏（`b#` 之外）。")
    elif d2_score_changed:
        print(f"  ⇒ JE 违反 T=0 确定性：state 逐字节相同，分数却变了 {d2_score_changed}")
        print("     ⇒ 确定性结论要重写（与 V26 的 J2 同形）。")
    else:
        print("  ⇒ JD 基线校准成功：d2 两轮 state **逐字节相同**、分数也相同")
        print("     ⇒ 累积循环在零信息下的净效应 = 0。")
        print("     ⇒ 这是第一次可以写「此后任何跨轮 Δ 都只能来自内容」的装置。")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
