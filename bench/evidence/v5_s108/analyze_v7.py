#!/usr/bin/env python3
"""V4‴ 读数器（第 108 轮 §78）：`wi`（骨架有 import）× `ni`（骨架没 import），两臂 `=1` / `=2`。

## 口径（预注册 §4）

- 分母 **D3（格）**：每次尝试 × 16 条断言；理由见 `PREREG.md` §4。
- Δ = 分数(`=1`) − 分数(`=2`)，**正数 = 报名字更好**。
- 四条判据**方向分组**（§73.5.1），不压成一个 tag：

    P1 `need_diff`  |Δ_ni − Δ_wi| ≥ 6/48   「两族的 Δ 不同」
    P2 `need_diff`  Δ_ni ≥ 6/48            「ni 上 =1 优于 =2」
    P4 `need_eq`    |Δ_wi| < 4/48          「wi 是干净对照」
    P3 灰带         4/48 ≤ |Δ_ni| < 6/48   ⇒ UNKNOWN

## 两道前置闸（本轮新增，§77.8 的 V9）

    P0  前提闸   M_wi(A) ≥ 12/16   —— 不满足 ⇒ 题目本身做不出来 ⇒ Δ 不可解释
    G1  贴壁闸   ni 两臂都 ≤ 2/16 ⇒ FLOOR；wi 两臂都 ≥ 14/16 ⇒ CEILING(wi)
                 ⛔ FLOOR 时**不许**出 P1/P2/P4（地板/天花板上的判据没有分辨力，§77.5.2）

用法：
    python3 bench/evidence/v5_s108/analyze_v7.py --arm-a DIR --arm-b DIR
    python3 bench/evidence/v5_s108/analyze_v7.py --selftest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench" / "scorers"))

from mw.verify import undefined_names          # noqa: E402
from code_exec import extract_code            # noqa: E402

WI = ("p1wi", "p2wi", "p3wi")
NI = ("p1ni", "p2ni", "p3ni")
GRID = 16
TH_DIFF = 6 / 48           #: need_diff 门槛（预注册 §5.1）
TH_EQ = 4 / 48             #: need_eq 门槛
TH_GREY_LO = 4 / 48        #: 灰带下缘
TH_P0 = 12 / 16            #: 前提闸：wi 组 A 臂均分下限
FLOOR_MAX = 2 / 16         #: 贴地板上限
CEIL_MIN = 14 / 16         #: 贴天花板下限


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
    """两道前置闸。返回 {p0, p0_ok, floor, ceiling_wi, blocked}。"""
    p0_ok = m_wi_a >= TH_P0
    floor = max(m_ni_a, m_ni_b) <= FLOOR_MAX
    ceil = min(m_wi_a, m_wi_b) >= CEIL_MIN
    return {
        "p0_value": m_wi_a, "p0_ok": p0_ok,
        "floor": floor, "ceiling_wi": ceil,
        "blocked": (not p0_ok) or floor,
    }


def verdict(d_ni: float, d_wi: float, g: dict | None = None) -> dict:
    """四条判据分别给结论，不压成一个 tag。被闸挡住时一律不许出 P1/P2/P4。"""
    gap = d_ni - d_wi
    p1 = abs(gap) >= TH_DIFF
    p2 = d_ni >= TH_DIFF
    p2_neg = d_ni <= -TH_DIFF
    p4 = abs(d_wi) < TH_EQ
    p3 = TH_GREY_LO <= abs(d_ni) < TH_DIFF
    out = {"d_ni": d_ni, "d_wi": d_wi, "gap": gap,
           "P1": p1, "P2": p2, "P2_neg": p2_neg, "P3": p3, "P4": p4,
           "th_diff": TH_DIFF, "th_eq": TH_EQ, "blocked": False}
    if g is not None and g["blocked"]:
        # ⛔ 闸咬住：判据一律置 None（= 不许给结论），不是 False（= 判否）
        out.update({"P1": None, "P2": None, "P2_neg": None, "P3": None, "P4": None,
                    "blocked": True})
    return out


def read_verdict(v: dict, g: dict | None = None) -> str:
    out: list[str] = []
    if g is not None:
        out.append(f"  P0 前提闸  M_wi(A) = {g['p0_value']:.4f} "
                   f"{'≥' if g['p0_ok'] else '<'} {TH_P0:.4f}  "
                   f"{'✅ 题目做得出来' if g['p0_ok'] else '❌ 装置失败（模型做不出这道题）'}")
        out.append(f"  G1 贴壁闸  ni 两臂 max = {g.get('ni_max', 0):.4f} "
                   f"{'≤' if g['floor'] else '>'} {FLOOR_MAX:.4f}"
                   f"{'  ⇒ **FLOOR**' if g['floor'] else ''}；"
                   f"wi 两臂 min = {g.get('wi_min', 0):.4f} "
                   f"{'≥' if g['ceiling_wi'] else '<'} {CEIL_MIN:.4f}"
                   f"{'  ⇒ CEILING(wi)' if g['ceiling_wi'] else ''}")
    if v["blocked"]:
        out.append("  ⛔ 前置闸咬住 ⇒ P1/P2/P3/P4 **一律不许给结论**（置 None，不是 False）。")
        out.append("     地板/天花板上的 `need_diff` 与 `need_eq` 都失去分辨力（§77.5.2）。")
        return "\n".join(out)
    out.append(f"  P1 need_diff |Δ_ni − Δ_wi| = {abs(v['gap']):.4f} "
               f"{'≥' if v['P1'] else '<'} {v['th_diff']:.4f}  "
               f"{'✅ 两族 Δ 不同' if v['P1'] else '❌ 两族 Δ 分不开'}")
    if v["P2"]:
        out.append(f"  P2 need_diff Δ_ni = {v['d_ni']:+.4f} ≥ {v['th_diff']:.4f}  ✅ 报名字更好")
    elif v["P2_neg"]:
        out.append(f"  P2 need_diff Δ_ni = {v['d_ni']:+.4f} ≤ −{v['th_diff']:.4f}  "
                   "⛔ **反号**：盲报反而更好")
    elif v["P3"]:
        out.append(f"  P2 need_diff Δ_ni = {v['d_ni']:+.4f} < {v['th_diff']:.4f}，"
                   f"但 ≥ {TH_GREY_LO:.4f}  ⇒ P3 灰带 **UNKNOWN**")
    else:
        out.append(f"  P2 need_diff Δ_ni = {v['d_ni']:+.4f} < {TH_GREY_LO:.4f}  "
                   "❌ 没有效应")
    out.append(f"  P4 need_eq  |Δ_wi| = {abs(v['d_wi']):.4f} "
               f"{'<' if v['P4'] else '≥'} {v['th_eq']:.4f}  "
               f"{'✅ wi 是干净对照' if v['P4'] else '❌ wi 不是干净对照 ⇒ 跨组比较降级为描述性'}")
    if g is not None and g["ceiling_wi"]:
        out.append("  ⚠️ wi 组贴天花板 ⇒ P4 降级为描述（`need_eq` 在天花板上没有分辨力）")
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


# ===========================================================================
# 主流程
# ===========================================================================
def run(arm_a: Path, arm_b: Path | None) -> int:
    A = load_run(arm_a)
    print("=" * 92)
    print("  §78 V4‴ 读数：verify_static 1（报名字） vs 2（盲报） —— tasks_v7（6 题 / 96 格 / 3 对 ±import）")
    print("=" * 92)

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
        print(f"  N4 {'✅ 只差 limits.verify_static' if n4 else '❌ 自变量不止一个'}")
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
        ma, mb = mean(sa), mean(sb)
        d = ma - mb if sb else float("nan")
        if sb:
            deltas[tid] = d
        grp = "wi" if tid in WI else "ni"
        fa = "/".join(f"{x:.2f}" for x in sa)
        fb = "/".join(f"{x:.2f}" for x in sb) if sb else "—"
        print(f"  {tid:6s} {grp:3s} {fa:>22s} {fb:>22s} "
              + (f"{d:+9.4f}" if sb else "        —"))

    if not B:
        print("\n⛔ 只有一臂 ⇒ 无法算 Δ。按预注册 §6.1 的 D0′ 判读登记。")
        return _one_arm_report(A, arm_a)

    d_wi = mean([deltas[t] for t in WI])
    d_ni = mean([deltas[t] for t in NI])
    m = {
        "wi_a": mean([mean(A["scores"].get(t, [])) for t in WI]),
        "wi_b": mean([mean(B["scores"].get(t, [])) for t in WI]),
        "ni_a": mean([mean(A["scores"].get(t, [])) for t in NI]),
        "ni_b": mean([mean(B["scores"].get(t, [])) for t in NI]),
    }
    print(f"\n[组均值] M_wi(A)={m['wi_a']:.4f} M_wi(B)={m['wi_b']:.4f} | "
          f"M_ni(A)={m['ni_a']:.4f} M_ni(B)={m['ni_b']:.4f}")
    print(f"          Δ_wi = {d_wi:+.4f}   Δ_ni = {d_ni:+.4f}   "
          f"（门槛 {TH_DIFF:.4f} / 灰带下缘 {TH_GREY_LO:.4f}）")

    g = gates(m["wi_a"], m["wi_b"], m["ni_a"], m["ni_b"])
    g["wi_min"] = min(m["wi_a"], m["wi_b"])
    g["ni_max"] = max(m["ni_a"], m["ni_b"])

    print("\n[前置闸 P0 / G1]（§77.8 的 V9）")
    v = verdict(d_ni, d_wi, g)
    print(read_verdict(v, g))

    # ── 名字状态（兑现的第一层：末答有没有把名字补上）────────────────────
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

    def digest() -> str:
        blob = json.dumps({"A": SA, "B": SB}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    h1, h2 = digest(), digest()
    print(f"\n[N3] 同一命令两次分类逐字相同：{h1[:16]} == {h2[:16]} "
          f"{'✅' if h1 == h2 else '❌'}")

    print("\n[兑现] 首答含未定义名的题 ⇒ 末答是否消除（同臂内，只作描述）")
    for tag, S in (("A", SA), ("B", SB)):
        hits = [t for t in WI + NI if S.get(t) and S[t]["first_names"]]
        fixed = [t for t in hits if not S[t]["last_names"]]
        print(f"  {tag}: {len(hits)} 题有名字 ⇒ 末答消除 {len(fixed)} 题 {fixed}"
              f"（未消除：{[t for t in hits if t not in fixed]}）")

    print(f"\n[成本] A={A['elapsed_s']:.1f}s  B={B['elapsed_s']:.1f}s  "
          f"合计={(A['elapsed_s'] + B['elapsed_s']) / 60:.1f} 分钟")
    return 0


def _one_arm_report(A: dict, arm_a: Path) -> int:
    S = name_state(arm_a / "answers.jsonl")
    ni_hit = [t for t in NI if S.get(t) and S[t]["first_names"]]
    wi_hit = [t for t in WI if S.get(t) and S[t]["first_names"]]
    print(f"\n[描述性]「每次尝试的最终交付」含未定义名："
          f"ni {len(ni_hit)}/3 {ni_hit}   wi {len(wi_hit)}/3 {wi_hit}")
    print("  ⚠️ 这一行**不参与判决**：台账只存每次尝试的最终交付，机制效应已混在里面。")
    print("     机会判定用 `probe_gate_hits_v7.py` 的打回日志（预注册 §6）。")
    print(f"\n[文前提闸 P0（单臂也能看）] M_wi(A) = "
          f"{mean([mean(A['scores'].get(t, [])) for t in WI]):.4f} "
          f"（门槛 {TH_P0:.4f}）")
    print("\n[逐题分数] A 臂")
    for tid in WI + NI:
        print(f"  {tid}: {A['scores'].get(tid)}")
    return 0


# ===========================================================================
# 反证：判决函数与闸必须能红
# ===========================================================================
def selftest() -> int:
    cases = [
        ("S1 差 10 格、wi 零差 ⇒ P1/P2/P4 全 ✅",
         (10 / 48, 0.0, None), {"P1": True, "P2": True, "P4": True, "P3": False}),
        ("S2 两族都零 ⇒ P1/P2 ❌、P3 ❌、P4 ✅",
         (0.0, 0.0, None), {"P1": False, "P2": False, "P4": True, "P3": False}),
        ("S3 Δ_ni = 5 格（4≤5<6）⇒ P3 灰带、P2 ❌",
         (5 / 48, 0.0, None), {"P1": False, "P2": False, "P4": True, "P3": True}),
        ("S4 Δ_ni = 6 格（= 门槛，边界含等号）⇒ P2 ✅",
         (6 / 48, 0.0, None), {"P1": True, "P2": True, "P4": True, "P3": False}),
        ("S5 两族同步 +10 格 ⇒ P1 ❌（gap=0）且 P4 ❌（wi 不干净）",
         (10 / 48, 10 / 48, None), {"P1": False, "P2": True, "P4": False, "P3": False}),
        ("S6 Δ_ni = −10 格 ⇒ P2 ❌ 且反号标记为真",
         (-10 / 48, 0.0, None), {"P1": True, "P2": False, "P4": True, "P3": False}),
        ("S7 Δ 都是 3 格 ⇒ P2 ❌ P3 ❌、P4 ✅（3 < 4）",
         (3 / 48, 3 / 48, None), {"P1": False, "P2": False, "P4": True, "P3": False}),
    ]
    bad = 0
    for why, (a, b, g), want in cases:
        v = verdict(a, b, g)
        got = {k: v[k] for k in want}
        ok = got == want
        print(f"  {'✅' if ok else '❌'} {why}  实测={got}")
        bad += 0 if ok else 1

    # ── 闸的反证：FLOOR 必须把 P1/P2/P4 压成 None（不是 False）──
    g_floor = gates(m_wi_a=1.0, m_wi_b=1.0, m_ni_a=0.0, m_ni_b=0.0)
    v_floor = verdict(0.0, 0.0, g_floor)
    ok_floor = (g_floor["floor"] is True) and all(
        v_floor[k] is None for k in ("P1", "P2", "P3", "P4", "P2_neg"))
    print(f"  {'✅' if ok_floor else '❌'} S8 ⭐ FLOOR（ni 两臂都 0）⇒ 四条判据一律 None "
          f"实测={ {k: v_floor[k] for k in ('P1', 'P2', 'P4')} }")
    bad += 0 if ok_floor else 1

    # ── 反证 S8：同一条 FLOOR 判据，抬高 ni 一臂就必须放行 ──
    g_notfloor = gates(m_wi_a=1.0, m_wi_b=1.0, m_ni_a=0.5, m_ni_b=0.0)
    v_open = verdict(10 / 48, 0.0, g_notfloor)
    ok_open = (g_notfloor["floor"] is False) and v_open["P2"] is True
    print(f"  {'✅' if ok_open else '❌'} S9 ⭐ 反证 S8：ni 一臂抬到 0.5 ⇒ 不判 FLOOR、P2 放行 "
          f"实测 floor={g_notfloor['floor']} P2={v_open['P2']}")
    bad += 0 if ok_open else 1

    # ── P0 前提闸：wi 做不出来时必须挡住一切 ──
    g_p0 = gates(m_wi_a=0.3, m_wi_b=0.3, m_ni_a=0.0, m_ni_b=0.1)
    v_p0 = verdict(10 / 48, 0.0, g_p0)
    ok_p0 = (g_p0["p0_ok"] is False) and v_p0["blocked"] and v_p0["P2"] is None
    print(f"  {'✅' if ok_p0 else '❌'} S10 ⭐ P0（wi 只 0.3）⇒ 装置失败、判据一律 None "
          f"实测 p0_ok={g_p0['p0_ok']} P2={v_p0['P2']}")
    bad += 0 if ok_p0 else 1

    # ── 反号标记必须与 P2 互斥 ──
    neg_ok = (verdict(-10 / 48, 0.0, None)["P2_neg"] is True
              and verdict(10 / 48, 0.0, None)["P2_neg"] is False)
    print(f"  {'✅' if neg_ok else '❌'} S11 P2_neg 只在 Δ_ni 足够负时为真")
    bad += 0 if neg_ok else 1

    # ── CEILING 必须只降级 P4 的读法，不挡 P2 ──
    g_ceil = gates(m_wi_a=1.0, m_wi_b=1.0, m_ni_a=0.0, m_ni_b=0.0)
    ok_ceil = g_ceil["ceiling_wi"] is True
    print(f"  {'✅' if ok_ceil else '❌'} S12 CEILING(wi) 在 wi 两臂都满分时为真 "
          f"实测={g_ceil['ceiling_wi']}")
    bad += 0 if ok_ceil else 1

    print(f"{'✅ 自证 12/12' if bad == 0 else f'❌ {bad} 条自证失败'}")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm-a")
    ap.add_argument("--arm-b")
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
    return run(Path(a.arm_a), b)


if __name__ == "__main__":
    raise SystemExit(main())
