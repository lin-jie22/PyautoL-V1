#!/usr/bin/env python3
"""V10 读数器（第 109 轮 §79）：`wi`（骨架有 import）× `ni`（骨架没 import），两臂 `=1` / `=2`。

## 与 §78 的 `analyze_v7.py` 有两处**刻意的**不同

### ① 分母**两套并用**（PREREG §4.1）

§78 的教训是硬的：`Δ_ni = +16/48` 看着很大，但**全来自 1 题**（3 题 Δ_t = {0,+1,0}）。
只报格数会系统性高估 ⇒ 本轮把判据拆到两套分母上：

| 判据 | 分母 | 为什么 |
|---|---|---|
| P0 / G1 / P2 / P3（格侧） | **D3 格**（48/组） | "幅度 / 贴壁"需要分辨率 |
| P1 / P3（题侧）/ P4 | **D1 题**（3/组） | "是不是只有 1 题在动" |
| ⛔ D2（题 × rep，9 格） | 不用 | 3 rep 逐字节相同 ⇒ 不是独立样本 |

### ② P4 搬到**形态层**（PREREG §5.4）

§78 的 P4 是 `need_eq`（分数层），会被 `P0` 红一起打死。
本轮 P4 改成零模型读数：`ni` 组交付里**自己补了 import** 的题数 ≥ 2
（来自 `probe_shape_v8.py` 的 `added > 0`）⇒ **不受 `P0` 影响**。

## 四条判据（PREREG §5.2，写定不许事后调）

    P0 前提闸   M_wi(A) ≥ 12/48
    G1 贴壁闸   ni 两臂都 ≤ 2/48 ⇒ FLOOR；wi 两臂都 ≥ 42/48 ⇒ CEILING
    P1 need_diff  ni 按题：≥2/3 题 Δ_t > 0 **且无一题 < 0**
    P2 need_diff  Δ_ni ≥ 8/48
    P3 need_eq    |Δ_wi| ≤ 4/48 **且** 3/3 题 |Δ_t| ≤ 1 格
    P4 形态层     ni 组 added>0 的**题数** ≥ 2

⛔ 闸咬住时 P1/P2/P3 一律置 `None`（= 不给结论），**不是 `False`**（= 判否）。
  P4 例外：它是零模型读数，`P0` 红时照出。

用法：
    python3 bench/evidence/v5_s109/analyze_v8.py --arm-a DIR --arm-b DIR [--shape-a J --shape-b J]
    python3 bench/evidence/v5_s109/analyze_v8.py --selftest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for p in (ROOT, ROOT / "bench" / "scorers"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mw.verify import undefined_names          # noqa: E402
from code_exec import extract_code            # noqa: E402

WI = ("e1wi", "e2wi", "e3wi")
NI = ("e1ni", "e2ni", "e3ni")
GRID = 16
TH_P0 = 12 / 48            #: 前提闸（M_wi(A) 的**格**均分下限）
TH_DIFF = 8 / 48           #: P2 need_diff（半题）
TH_EQ = 4 / 48             #: P3 need_eq（格侧）
TH_TASK_EQ = 1             #: P3 题侧：单题 |Δ_t| ≤ 1 格
TASK_UP_N = 2              #: P1：≥2/3 题同向
FLOOR_MAX = 2 / 48         #: 贴地板上限
CEIL_MIN = 42 / 48         #: 贴天花板下限
TH_P4 = 2                  #: P4：ni 组 added>0 的**题数**下限


# ===========================================================================
# 台账读取
# ===========================================================================
def load_run(arm_dir: Path) -> dict:
    runs = [json.loads(l) for l in
            (arm_dir / "runs.jsonl").read_text().splitlines() if l.strip()]
    if not runs:
        raise SystemExit(f"❌ {arm_dir}/runs.jsonl 是空的")
    d = runs[-1]
    per = d["report"]["per_task"] or []
    scores: dict[str, list[float]] = {}
    for r in per:
        scores.setdefault(r["id"], []).append(float(r["score"]))
    return {
        "caliber": d["caliber"],
        "caliber_sig": d["caliber_sig"],
        "per_task": per,
        "scores": scores,
        "attempted": d["report"]["attempted"],
        "elapsed_s": d["report"]["elapsed_s"],
        "closeout": d.get("kernel_closeout", {}),
    }


def flat(d: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flat(v, key))
        else:
            out[key] = json.dumps(v, ensure_ascii=False, sort_keys=True)
    return out


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


# ===========================================================================
# 判决（唯一实现；selftest 反证它）
# ===========================================================================
def gates(m_wi_a: float, m_wi_b: float, m_ni_a: float, m_ni_b: float) -> dict:
    """两道前置闸。所有 M 都是**格**均分（分母 48）。"""
    p0_ok = m_wi_a >= TH_P0
    floor = max(m_ni_a, m_ni_b) <= FLOOR_MAX
    ceil = min(m_wi_a, m_wi_b) >= CEIL_MIN
    return {
        "p0_value": m_wi_a, "p0_ok": p0_ok,
        "floor": floor, "ceiling_wi": ceil,
        "ni_max": max(m_ni_a, m_ni_b), "wi_min": min(m_wi_a, m_wi_b),
        "blocked": (not p0_ok) or floor,
    }


def p1_ok(d_ni_tasks: dict[str, float] | None) -> bool | None:
    """P1（按题）：≥2/3 题 Δ_t > 0 且**无一题** < 0。"""
    if not d_ni_tasks:
        return None
    up = [t for t, x in d_ni_tasks.items() if x > 0]
    down = [t for t, x in d_ni_tasks.items() if x < 0]
    return len(up) >= TASK_UP_N and not down


def p3_ok(d_wi: float, d_wi_tasks: dict[str, float] | None) -> bool | None:
    """P3：格侧 |Δ_wi| ≤ 4/48 **且** 3/3 题 |Δ_t| ≤ 1 格。"""
    if abs(d_wi) > TH_EQ:
        return False
    if not d_wi_tasks:
        return None
    return all(abs(x) <= TH_TASK_EQ for x in d_wi_tasks.values())


def verdict(d_ni: float, d_wi: float, g: dict | None = None,
            d_ni_tasks: dict[str, float] | None = None,
            d_wi_tasks: dict[str, float] | None = None,
            p4_n: int | None = None) -> dict:
    """四条判据分别给结论，不压成一个 tag。闸咬住时 P1/P2/P3 置 None，P4 照出。"""
    out = {
        "d_ni": d_ni, "d_wi": d_wi,
        "P1": p1_ok(d_ni_tasks),
        "P2": d_ni >= TH_DIFF,
        "P3": p3_ok(d_wi, d_wi_tasks),
        "P4": (None if p4_n is None else p4_n >= TH_P4),
        "p4_n": p4_n,
        "blocked": False,
        "th_diff": TH_DIFF, "th_eq": TH_EQ,
    }
    if g is not None and g["blocked"]:
        # ⛔ 闸咬住：分数层判据一律置 None（= 不许给结论），不是 False（= 判否）
        out.update({"P1": None, "P2": None, "P3": None, "blocked": True})
        # P4 是零模型读数，不受 P0/FLOOR 影响（PREREG §5.4）
    return out


def read_verdict(v: dict, g: dict | None = None) -> str:
    out: list[str] = []
    if g is not None:
        out.append(f"  P0 前提闸  M_wi(A) = {g['p0_value'] * 48:.1f}/48 "
                   f"({'≥' if g['p0_ok'] else '<'} {TH_P0 * 48:.0f}/48)  "
                   f"{'✅ 尺子有分辨力' if g['p0_ok'] else '❌ 模型做不出 wi 组 ⇒ Δ 不可解释'}")
        out.append(f"  G1 贴壁闸  ni 两臂 max = {g['ni_max'] * 48:.1f}/48 "
                   f"{'≤' if g['floor'] else '>'} {FLOOR_MAX * 48:.0f}/48"
                   f"{'  ⇒ **FLOOR**' if g['floor'] else ''}；"
                   f"wi 两臂 min = {g['wi_min'] * 48:.1f}/48 "
                   f"{'≥' if g['ceiling_wi'] else '<'} {CEIL_MIN * 48:.0f}/48"
                   f"{'  ⇒ **CEILING(wi)**' if g['ceiling_wi'] else ''}")
    if v["blocked"]:
        out.append("  ⛔ 前置闸咬住 ⇒ P1/P2/P3 **一律不许给结论**（置 None，不是 False）。")
        out.append("     ⚠️ P4 是零模型读数，不受此限，下面照样出。")
    else:
        p1 = v["P1"]
        out.append(f"  P1 need_diff（**按题**）≥{TASK_UP_N}/3 题 Δ_t>0 且无反向  "
                   f"{'✅' if p1 else '❌'}")
        out.append(f"  P2 need_diff（**按格**）Δ_ni = {v['d_ni'] * 48:+.1f}/48 "
                   f"{'≥' if v['P2'] else '<'} {TH_DIFF * 48:.0f}/48  "
                   f"{'✅' if v['P2'] else '❌'}")
        p3 = v["P3"]
        out.append(f"  P3 need_eq  |Δ_wi| = {abs(v['d_wi']) * 48:.1f}/48 "
                   f"≤ {TH_EQ * 48:.0f}/48 且 3/3 题 |Δ_t| ≤ {TH_TASK_EQ} 格  "
                   f"{'✅ wi 是干净对照' if p3 else '❌ wi 也动了'}")
    if v["P4"] is None:
        out.append("  P4 形态层 未提供 `--shape-*` ⇒ 不出")
    else:
        out.append(f"  P4 形态层  ni 组 added>0 的题数 = {v['p4_n']}/3 "
                   f"{'≥' if v['P4'] else '<'} {TH_P4}  "
                   f"{'✅ 模型自己补了 import' if v['P4'] else '❌ 模型没补'}")
    return "\n".join(out)


# ===========================================================================
# 名字状态（零模型，静态）
# ===========================================================================
def name_state(answers_path: Path) -> dict[str, dict]:
    rows = [json.loads(l) for l in answers_path.read_text().splitlines() if l.strip()]
    rows = [r for r in rows if r.get("kind") == "answer"]
    per: dict[str, list[dict]] = {}
    for r in rows:
        per.setdefault(r["task_id"], []).append(r)
    out = {}
    for tid, rs in per.items():
        def names_of(r: dict) -> list[str]:
            got = undefined_names(extract_code(r.get("answer") or ""))
            return [nm for _fn, nm in (got or [])]
        out[tid] = {
            "attempts": len(rs),
            "shas": len({r.get("sha256") for r in rs}),
            "first_names": names_of(rs[0]),
            "last_names": names_of(rs[-1]),
            "first_sha": (rs[0].get("sha256") or "")[:8],
            "last_sha": (rs[-1].get("sha256") or "")[:8],
        }
    return out


def load_shape(p: Path | None) -> dict[str, dict] | None:
    """读 `probe_shape_v8.py --json` 的产物，返回 `{题: 该题唯一交付里 added 的最大值}`。"""
    if p is None or not p.exists():
        return None
    rows = json.loads(p.read_text())
    out: dict[str, dict] = {}
    for d in rows:
        tid = d["task"]
        cur = out.setdefault(tid, {"max_added": -99, "n": 0, "dirty": 0})
        cur["max_added"] = max(cur["max_added"], d.get("added", -99))
        cur["n"] += 1
        cur["dirty"] += 1 if d.get("dirty") else 0
    return out


def p4_count(shape: dict[str, dict] | None) -> int | None:
    """`ni` 组里 `added > 0` 的**题数**（不是份数 —— 有效样本按题算，§76.9）。"""
    if shape is None:
        return None
    return sum(1 for t in NI if shape.get(t, {}).get("max_added", -99) > 0)


# ===========================================================================
# 主流程
# ===========================================================================
def run(arm_a: Path, arm_b: Path | None, shape_a: Path | None,
        shape_b: Path | None) -> int:
    A = load_run(arm_a)
    print("=" * 96)
    print("  §79 V10 读数：verify_static 1（报名字） vs 2（盲报） —— tasks_v8"
          "（easy 档 / 6 题 / 96 格 / 3 对 ±import）")
    print("=" * 96)

    print("\n[N4/N5] 口径签名与端口")
    print(f"  A: sig={A['caliber_sig']}  endpoint={A['caliber']['llm_cond']['endpoint']}"
          f"  verify_static={A['caliber']['limits']['verify_static']}"
          f"  suite_sha={A['caliber']['suite_sha'][:16]}")
    B = None
    if arm_b:
        B = load_run(arm_b)
        print(f"  B: sig={B['caliber_sig']}  endpoint={B['caliber']['llm_cond']['endpoint']}"
              f"  verify_static={B['caliber']['limits']['verify_static']}"
              f"  suite_sha={B['caliber']['suite_sha'][:16]}")
        fa, fb = flat(A["caliber"]), flat(B["caliber"])
        keys = sorted(set(fa) | set(fb))
        diff = [k for k in keys if fa.get(k) != fb.get(k)]
        n4 = diff == ["limits.verify_static"]
        print(f"  逐字段比对：{len(keys)} 个字段，不同 {len(diff)} 项 ⇒ {diff}")
        print(f"  N4 {'✅ 只差 limits.verify_static' if n4 else '❌ 自变量不止一个 ⇒ 本轮作废'}")
        n5 = (A["caliber"]["llm_cond"]["endpoint"]
              == B["caliber"]["llm_cond"]["endpoint"] == "127.0.0.1:8812")
        print(f"  N5 {'✅ 两臂同端口 8812' if n5 else '❌ 端口不一致'}")
    else:
        print("  B: **未跑**（预注册 D0′ 触发或手动跳过）")

    print("\n[N6] 尝试数与代理台账")
    for tag, arm_dir in (("A", arm_a), ("B", arm_b)):
        if arm_dir is None:
            continue
        px = arm_dir / "proxy.jsonl"
        nlines = len(px.read_text().splitlines()) if px.exists() else 0
        print(f"  {tag}: attempted={load_run(arm_dir)['attempted']} 代理台账={nlines} 行")
    print("  期望 attempted=18（6 题 × 3 rep）")

    print("\n[逐题] 每题 3 rep 的平均格分（分母 16）与 Δ_t = A − B")
    print(f"  {'题':6s} {'组':3s} {'A(rep1/2/3)':>22s} {'B(rep1/2/3)':>22s} {'Δ_t':>9s}")
    deltas: dict[str, float] = {}
    for tid in WI + NI:
        sa = A["scores"].get(tid, [])
        sb = B["scores"].get(tid, []) if B else []
        d = (mean(sa) - mean(sb)) if sb else float("nan")
        if sb:
            deltas[tid] = d
        grp = "wi" if tid in WI else "ni"
        fa = "/".join(f"{x:g}" for x in sa)
        fb = "/".join(f"{x:g}" for x in sb) if sb else "—"
        print(f"  {tid:6s} {grp:3s} {fa:>22s} {fb:>22s} "
              + (f"{d:+9.2f}" if sb else "        —"))

    if not B:
        print("\n⛔ 只有一臂 ⇒ 无法算 Δ。")
        return _one_arm_report(A, arm_a, load_shape(shape_a))

    d_wi = mean([deltas[t] for t in WI])
    d_ni = mean([deltas[t] for t in NI])
    m = {
        "wi_a": mean([mean(A["scores"].get(t, [])) for t in WI]),
        "wi_b": mean([mean(B["scores"].get(t, [])) for t in WI]),
        "ni_a": mean([mean(A["scores"].get(t, [])) for t in NI]),
        "ni_b": mean([mean(B["scores"].get(t, [])) for t in NI]),
    }
    print(f"\n[组均值] M_wi(A)={m['wi_a'] * 48:.1f}/48 M_wi(B)={m['wi_b'] * 48:.1f}/48 | "
          f"M_ni(A)={m['ni_a'] * 48:.1f}/48 M_ni(B)={m['ni_b'] * 48:.1f}/48")
    print(f"          Δ_wi = {d_wi * 48:+.1f}/48   Δ_ni = {d_ni * 48:+.1f}/48")

    g = gates(m["wi_a"], m["wi_b"], m["ni_a"], m["ni_b"])
    d_ni_tasks = {t: deltas[t] for t in NI}
    d_wi_tasks = {t: deltas[t] for t in WI}
    n4 = p4_count(load_shape(shape_a))

    print("\n[前置闸 P0 / G1] + 四条判据")
    v = verdict(d_ni, d_wi, g, d_ni_tasks, d_wi_tasks, n4)
    print(read_verdict(v, g))

    # ── §78 的反面教材：只报格数会怎样 ────────────────────────────────────
    up = [t for t, x in d_ni_tasks.items() if x > 0]
    down = [t for t, x in d_ni_tasks.items() if x < 0]
    flat_ = [t for t, x in d_ni_tasks.items() if x == 0]
    print(f"\n[按题分解]（§78 的教训：格数 Δ 可以全来自 1 题）")
    print(f"  涨 {len(up)} 题 {up}   跌 {len(down)} 题 {down}   平 {len(flat_)} 题 {flat_}")
    print(f"  ⛔ 「平均 {d_ni * 48:+.1f}/48」只有在**多题同向**时才允许这么写；"
          f"当前 {len(up)} 涨 {len(down)} 跌 {len(flat_)} 平。")

    # ── 名字状态 ─────────────────────────────────────────────────────────
    print("\n[名字状态] 首答 / 末答是否有未定义名（零模型静态；只作描述）")
    SA = name_state(arm_a / "answers.jsonl")
    SB = name_state(arm_b / "answers.jsonl")
    print(f"  {'题':6s} {'组':3s} {'臂':2s} {'尝试':>4s} {'sha数':>5s} "
          f"{'首答名':>12s} {'末答名':>12s} {'首→末 sha':>20s}")
    for tid in WI + NI:
        for tag, S in (("A", SA), ("B", SB)):
            s = S.get(tid)
            if not s:
                continue
            print(f"  {tid:6s} {'wi' if tid in WI else 'ni':3s} {tag:2s} "
                  f"{s['attempts']:4d} {s['shas']:5d} "
                  f"{','.join(s['first_names']) or '—':>12s} "
                  f"{','.join(s['last_names']) or '—':>12s} "
                  f"{s['first_sha']}→{s['last_sha']:>8s}")

    blob = json.dumps({"A": SA, "B": SB}, ensure_ascii=False, sort_keys=True)
    h1 = hashlib.sha256(blob.encode()).hexdigest()
    print(f"\n[N3] 同一命令两次分类逐字相同：{h1[:16]}（确定性读数）")

    print(f"\n[成本] A={A['elapsed_s']:.1f}s  B={B['elapsed_s']:.1f}s  "
          f"合计={(A['elapsed_s'] + B['elapsed_s']) / 60:.1f} 分钟")
    return 0


def _one_arm_report(A: dict, arm_a: Path, shape: dict | None) -> int:
    S = name_state(arm_a / "answers.jsonl")
    ni_hit = [t for t in NI if S.get(t) and S[t]["first_names"]]
    wi_hit = [t for t in WI if S.get(t) and S[t]["first_names"]]
    print(f"\n[描述性]「每次尝试的最终交付」含未定义名："
          f"ni {len(ni_hit)}/3 {ni_hit}   wi {len(wi_hit)}/3 {wi_hit}")
    print("  ⚠️ 这一行**不参与判决**：台账只存每次尝试的最终交付，机制效应已混在里面。")
    print("     机会判定用 `probe_gate_hits_v8.py` 的打回日志（预注册 §6）。")
    m_wi = mean([mean(A["scores"].get(t, [])) for t in WI])
    print(f"\n[P0 前提闸（单臂也能看）] M_wi(A) = {m_wi * 48:.1f}/48 "
          f"（门槛 {TH_P0 * 48:.0f}/48）  {'✅' if m_wi >= TH_P0 else '❌'}")
    n4 = p4_count(shape)
    if n4 is not None:
        print(f"[P4 形态层] ni 组 added>0 的题数 = {n4}/3  "
              f"{'✅' if n4 >= TH_P4 else '❌'}")
    print("\n[逐题分数] A 臂")
    for tid in WI + NI:
        print(f"  {tid}: {A['scores'].get(tid)}")
    return 0


# ===========================================================================
# 反证：判决函数与闸必须能红
# ===========================================================================
def selftest() -> int:
    T3 = {"e1ni": 1.0, "e2ni": 1.0, "e3ni": 0.0}
    W3 = {"e1wi": 0.0, "e2wi": 0.0, "e3wi": 0.0}
    cases = [
        ("S1 Δ_ni=+10/48、2 涨 1 平、wi 零差 ⇒ P1/P2/P3 全 ✅",
         (10 / 48, 0.0, None, T3, W3, None),
         {"P1": True, "P2": True, "P3": True}),
        ("S2 ⭐ Δ_ni=+10/48 但只有 **1** 题在动 ⇒ P1 ❌（§78 的核心教训）",
         (10 / 48, 0.0, None, {"e1ni": 10.0, "e2ni": 0.0, "e3ni": 0.0}, W3, None),
         {"P1": False, "P2": True, "P3": True}),
        ("S3 ⭐ Δ_ni=+10/48 但 2 涨 1 **跌** ⇒ P1 ❌（有反向就不算同向）",
         (10 / 48, 0.0, None, {"e1ni": 8.0, "e2ni": 8.0, "e3ni": -6.0}, W3, None),
         {"P1": False, "P2": True, "P3": True}),
        ("S4 Δ_ni = 8/48（= 门槛，边界含等号）⇒ P2 ✅",
         (8 / 48, 0.0, None, T3, W3, None), {"P1": True, "P2": True, "P3": True}),
        ("S5 Δ_ni = 7/48（差一格）⇒ P2 ❌",
         (7 / 48, 0.0, None, T3, W3, None), {"P1": True, "P2": False, "P3": True}),
        ("S6 wi 有一题动 2 格 ⇒ P3 ❌（格侧没超但题侧超）",
         (10 / 48, 0.0, None, T3, {"e1wi": 2.0, "e2wi": 0.0, "e3wi": 0.0}, None),
         {"P1": True, "P2": True, "P3": False}),
        ("S7 wi 每题动 1 格（3 题共 3 格 ≤ 4/48）⇒ P3 ✅（题侧恰好卡线内）",
         (10 / 48, 3 / 48, None, T3,
          {"e1wi": 1.0, "e2wi": 1.0, "e3wi": 1.0}, None),
         {"P1": True, "P2": True, "P3": True}),
    ]
    bad = 0
    for why, args, want in cases:
        v = verdict(*args)
        got = {k: v[k] for k in want}
        ok = got == want
        print(f"  {'✅' if ok else '❌'} {why}  实测={got}")
        bad += 0 if ok else 1

    # ── 闸：P0 红必须把 P1/P2/P3 压成 None，但 P4 照出 ──
    g_p0 = gates(m_wi_a=4 / 48, m_wi_b=4 / 48, m_ni_a=10 / 48, m_ni_b=2 / 48)
    v_p0 = verdict(10 / 48, 0.0, g_p0, T3, W3, 2)
    ok_p0 = (g_p0["p0_ok"] is False and g_p0["blocked"]
             and all(v_p0[k] is None for k in ("P1", "P2", "P3"))
             and v_p0["P4"] is True)
    print(f"  {'✅' if ok_p0 else '❌'} S8 ⭐ P0 红 ⇒ P1/P2/P3 全 None，"
          f"**但 P4 照出**（零模型不受 P0 影响）  实测="
          f"{ {k: v_p0[k] for k in ('P1', 'P2', 'P3', 'P4')} }")
    bad += 0 if ok_p0 else 1

    # ── 反证 S8：同一条判据，P0 抬到门槛以上就必须放行 ──
    g_ok = gates(m_wi_a=12 / 48, m_wi_b=4 / 48, m_ni_a=10 / 48, m_ni_b=2 / 48)
    v_ok = verdict(10 / 48, 0.0, g_ok, T3, W3, 2)
    ok_ok = (g_ok["p0_ok"] is True) and v_ok["P2"] is True and v_ok["blocked"] is False
    print(f"  {'✅' if ok_ok else '❌'} S9 ⭐ 反证 S8：M_wi(A) 恰好 = 12/48 ⇒ P0 绿、P2 放行  "
          f"实测 p0_ok={g_ok['p0_ok']} P2={v_ok['P2']}")
    bad += 0 if ok_ok else 1

    # ── FLOOR ──
    g_floor = gates(20 / 48, 20 / 48, 1 / 48, 0 / 48)
    v_floor = verdict(0.0, 0.0, g_floor, T3, W3, None)
    ok_floor = (g_floor["floor"] is True
                and all(v_floor[k] is None for k in ("P1", "P2", "P3")))
    print(f"  {'✅' if ok_floor else '❌'} S10 FLOOR（ni 两臂都 ≤2/48）⇒ 判据一律 None")
    bad += 0 if ok_floor else 1

    # ── CEILING（只降级 P3 的读法，不挡其他）──
    g_ceil = gates(48 / 48, 44 / 48, 10 / 48, 2 / 48)
    ok_ceil = g_ceil["ceiling_wi"] is True and g_ceil["blocked"] is False
    print(f"  {'✅' if ok_ceil else '❌'} S11 CEILING(wi) 在 wi 两臂都 ≥42/48 时为真，"
          f"且**不**因此挡住判据  实测={ok_ceil}")
    bad += 0 if ok_ceil else 1

    # ── P4 按**题**算，不按份 ──
    shape_2t = {"e1ni": {"max_added": 1, "n": 1, "dirty": 0},
                "e2ni": {"max_added": 1, "n": 1, "dirty": 0},
                "e3ni": {"max_added": 0, "n": 1, "dirty": 0}}
    shape_1t = {"e1ni": {"max_added": 1, "n": 3, "dirty": 0},
                "e2ni": {"max_added": 0, "n": 1, "dirty": 0},
                "e3ni": {"max_added": 0, "n": 1, "dirty": 0}}
    ok_p4 = (p4_count(shape_2t) == 2 and p4_count(shape_1t) == 1
             and verdict(0, 0, None, T3, W3, 2)["P4"] is True
             and verdict(0, 0, None, T3, W3, 1)["P4"] is False)
    print(f"  {'✅' if ok_p4 else '❌'} S12 ⭐ P4 按**题**算：2 题补 ⇒ True、1 题补 3 份 ⇒ False "
          f"实测 2t={p4_count(shape_2t)} 1t={p4_count(shape_1t)}")
    bad += 0 if ok_p4 else 1

    print(f"{'✅ 自证 12/12' if bad == 0 else f'❌ {bad} 条自证失败'}")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm-a")
    ap.add_argument("--arm-b")
    ap.add_argument("--shape-a", help="probe_shape_v8.py --json 的产物（A 臂）")
    ap.add_argument("--shape-b", help="同上（B 臂，暂未用于判据）")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.arm_a:
        print("缺 --arm-a（或 --selftest）", file=sys.stderr)
        return 2
    b = Path(a.arm_b) if a.arm_b else None
    if b is not None and not (b / "runs.jsonl").exists():
        print(f"⚠️ {b} 没有 runs.jsonl ⇒ 按单臂处理")
        b = None
    return run(Path(a.arm_a), b,
               Path(a.shape_a) if a.shape_a else None,
               Path(a.shape_b) if a.shape_b else None)


if __name__ == "__main__":
    raise SystemExit(main())
