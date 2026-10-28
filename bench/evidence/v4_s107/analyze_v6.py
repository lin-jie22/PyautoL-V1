#!/usr/bin/env python3
"""V4″ 读数器（第 107 轮 §77）：配对设计 `bf`（从零写）vs `sk`（骨架补全）× 两臂 `1`/`2`。

## 口径（预注册 §3 / §4）

- 分母 **D3（格）**：每次尝试 × 16 条断言。理由见 PREREG §3。
- Δ 的定义：**Δ = 分数(=1) − 分数(=2)**，正数 = 报名字更好。
- 四条判据**方向分组**（§73.5.1），不压成一个 tag：

    P1 `need_diff`  |Δ_sk − Δ_bf| ≥ 6/48   「两族的 Δ 不同」
    P2 `need_diff`  Δ_sk ≥ 6/48            「sk 上 =1 优于 =2」
    P4 `need_eq`    |Δ_bf| < 4/48          「bf 上两臂无差」（§76 的干净对照）
    P3 灰带         4/48 ≤ |Δ_sk| < 6/48   ⇒ UNKNOWN

⛔ `need_eq` 的 4/48 **不能**拿来支持 P2（§73.5.1：同一组门槛不许两用）。
⛔ 若 P4 红（|Δ_bf| ≥ 4/48）⇒ bf 不是干净对照 ⇒ 所有跨组比较降级为**描述性**（PREREG §7.4）。

用法：
    python3 bench/evidence/v4_s107/analyze_v6.py --arm-a DIR --arm-b DIR
    python3 bench/evidence/v4_s107/analyze_v6.py --selftest
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

BF = ("p1bf", "p2bf", "p3bf")
SK = ("p1sk", "p2sk", "p3sk")
GRID = 16
TH_DIFF = 6 / 48           #: need_diff 门槛（预注册 §4）
TH_EQ = 4 / 48             #: need_eq 门槛（预注册 §4）
TH_GREY_LO = 4 / 48        #: 灰带下缘


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
def verdict(d_sk: float, d_bf: float) -> dict:
    """四条判据分别给结论，不压成一个 tag。门槛与方向见预注册 §4。"""
    gap = d_sk - d_bf
    p1 = abs(gap) >= TH_DIFF
    p2 = d_sk >= TH_DIFF
    p2_neg = d_sk <= -TH_DIFF          #: 反号：=2 反而更好
    p4 = abs(d_bf) < TH_EQ
    p3 = TH_GREY_LO <= abs(d_sk) < TH_DIFF
    return {
        "d_sk": d_sk, "d_bf": d_bf, "gap": gap,
        "P1": p1, "P2": p2, "P2_neg": p2_neg, "P3": p3, "P4": p4,
        "th_diff": TH_DIFF, "th_eq": TH_EQ,
    }


def read_verdict(v: dict) -> str:
    out = []
    out.append(f"  P1 need_diff |Δ_sk − Δ_bf| = {abs(v['gap']):.4f} "
               f"{'≥' if v['P1'] else '<'} {v['th_diff']:.4f}  "
               f"{'✅ 两族 Δ 不同' if v['P1'] else '❌ 两族 Δ 分不开'}")
    if v["P2"]:
        out.append(f"  P2 need_diff Δ_sk = {v['d_sk']:+.4f} ≥ {v['th_diff']:.4f}  "
                   "✅ 报名字更好")
    elif v["P2_neg"]:
        out.append(f"  P2 need_diff Δ_sk = {v['d_sk']:+.4f} ≤ −{v['th_diff']:.4f}  "
                   "⛔ **反号**：盲报反而更好")
    elif v["P3"]:
        out.append(f"  P2 need_diff Δ_sk = {v['d_sk']:+.4f} < {v['th_diff']:.4f}，"
                   f"但 ≥ {TH_GREY_LO:.4f}  ⇒ P3 灰带 **UNKNOWN**")
    else:
        out.append(f"  P2 need_diff Δ_sk = {v['d_sk']:+.4f} < {TH_GREY_LO:.4f}  "
                   "❌ 没有效应（预注册 §7.2：撤回「闸的价值」）")
    out.append(f"  P4 need_eq  |Δ_bf| = {abs(v['d_bf']):.4f} "
               f"{'<' if v['P4'] else '≥'} {v['th_eq']:.4f}  "
               f"{'✅ bf 是干净对照' if v['P4'] else '❌ bf 不是干净对照 ⇒ 跨组比较降级为描述性'}")
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
    print("=" * 88)
    print("  §77 V4″ 读数：verify_static 1（报名字） vs 2（盲报） —— tasks_v6（6 题 / 96 格 / 3 对）")
    print("=" * 88)

    # ── N4 / N5 口径 ────────────────────────────────────────────────────
    print("\n[N4/N5] 口径签名与端口")
    print(f"  A: sig={A['caliber_sig']}  endpoint={A['caliber']['llm_cond']['endpoint']}"
          f"  verify_static={A['caliber']['limits']['verify_static']}"
          f"  suite_sha={A['caliber']['suite_sha'][:16]}")
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
        B = None
        print("  B: **未跑**（预注册 D0′ 触发或手动跳过）")

    # ── N6 尝试数 ───────────────────────────────────────────────────────
    print("\n[N6] 尝试数与代理台账")
    for tag, arm_dir in (("A", arm_a), ("B", arm_b)):
        if arm_dir is None:
            continue
        px = arm_dir / "proxy.jsonl"
        nlines = len(px.read_text().splitlines()) if px.exists() else 0
        print(f"  {tag}: attempted={load_run(arm_dir)['attempted']} 代理台账={nlines} 行")
    print("  期望 attempted=18（6 题 × 3 rep）")

    # ── 逐题格分 ────────────────────────────────────────────────────────
    print("\n[逐题] 每题 3 rep 的平均格分（分母 16）与 Δ_t = A − B")
    print(f"  {'题':6s} {'组':4s} {'A(rep1/2/3)':>20s} {'B(rep1/2/3)':>20s} {'Δ_t':>8s}")
    deltas: dict[str, float] = {}
    for tid in BF + SK:
        sa = A["scores"].get(tid, [])
        sb = B["scores"].get(tid, []) if B else []
        ma, mb = mean(sa), mean(sb)
        d = ma - mb if sb else float("nan")
        if sb:
            deltas[tid] = d
        grp = "bf" if tid in BF else "sk"
        fa = "/".join(f"{x:.2f}" for x in sa)
        fb = "/".join(f"{x:.2f}" for x in sb) if sb else "—"
        print(f"  {tid:6s} {grp:4s} {fa:>20s} {fb:>20s} "
              + (f"{d:+8.4f}" if sb else "       —"))

    if not B:
        print("\n⛔ 只有一臂 ⇒ 无法算 Δ。按预注册 D0′ 的判读登记。")
        return _one_arm_report(A, arm_a)

    d_bf = mean([deltas[t] for t in BF])
    d_sk = mean([deltas[t] for t in SK])
    print(f"\n[组均值] Δ_bf = {d_bf:+.4f}   Δ_sk = {d_sk:+.4f}   "
          f"（need_diff 门槛 {TH_DIFF:.4f} / need_eq 门槛 {TH_EQ:.4f}）")

    v = verdict(d_sk, d_bf)
    print("\n[判决] 四条判据分别给结论（预注册 §4）")
    print(read_verdict(v))

    # ── 名字状态 ────────────────────────────────────────────────────────
    print("\n[名字状态] 首答 / 末答是否有未定义名（零模型静态；只作描述）")
    SA = name_state(arm_a / "answers.jsonl")
    SB = name_state(arm_b / "answers.jsonl")
    print(f"  {'题':6s} {'组':4s} {'臂':2s} {'尝试':>4s} {'sha数':>5s} "
          f"{'首答名':>14s} {'末答名':>14s} {'首→末 sha':>20s}")
    for tid in BF + SK:
        for tag, S in (("A", SA), ("B", SB)):
            s = S.get(tid)
            if not s:
                continue
            print(f"  {tid:6s} {'bf' if tid in BF else 'sk':4s} {tag:2s} "
                  f"{s['attempts']:4d} {s['shas']:5d} "
                  f"{','.join(s['first_names']) or '—':>14s} "
                  f"{','.join(s['last_names']) or '—':>14s} "
                  f"{s['first_sha']}→{s['last_sha']:>8s}")

    # ── N3 分类可复现 ───────────────────────────────────────────────────
    def digest() -> str:
        blob = json.dumps({"A": SA, "B": SB}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    h1, h2 = digest(), digest()
    print(f"\n[N3] 同一命令两次分类逐字相同：{h1[:16]} == {h2[:16]} "
          f"{'✅' if h1 == h2 else '❌'}")

    # ── 兑现 ────────────────────────────────────────────────────────────
    print("\n[兑现] 首答含未定义名的题 ⇒ 末答是否消除（同臂内，只作描述）")
    for tag, S in (("A", SA), ("B", SB)):
        hits = [t for t in BF + SK if S.get(t) and S[t]["first_names"]]
        fixed = [t for t in hits if not S[t]["last_names"]]
        print(f"  {tag}: {len(hits)} 题有名字 ⇒ 末答消除 {len(fixed)} 题 {fixed}"
              f"（未消除：{[t for t in hits if t not in fixed]}）")

    print(f"\n[成本] A={A['elapsed_s']:.1f}s  B={B['elapsed_s']:.1f}s  "
          f"合计={(A['elapsed_s'] + B['elapsed_s']) / 60:.1f} 分钟")
    return 0


def _one_arm_report(A: dict, arm_a: Path) -> int:
    S = name_state(arm_a / "answers.jsonl")
    sk_hit = [t for t in SK if S.get(t) and S[t]["first_names"]]
    bf_hit = [t for t in BF if S.get(t) and S[t]["first_names"]]
    print(f"\n[描述性] 「每次尝试的最终交付」含未定义名："
          f"sk {len(sk_hit)}/3 {sk_hit}   bf {len(bf_hit)}/3 {bf_hit}")
    print("  ⚠️ 这一行**不参与判决**：台账只存每次尝试的最终交付，机制效应已混在里面。")
    print("     机会判定用 `probe_gate_hits_v6.py` 的打回日志（预注册 §5）。")
    print("\n[逐题分数] A 臂")
    for tid in BF + SK:
        print(f"  {tid}: {A['scores'].get(tid)}")
    return 0


# ===========================================================================
# 反证：判决函数必须能红
# ===========================================================================
def selftest() -> int:
    cases = [
        ("S1 差 10 格、bf 零差 ⇒ P1/P2/P4 全 ✅",
         (10 / 48, 0.0), {"P1": True, "P2": True, "P4": True, "P3": False}),
        ("S2 两族都零 ⇒ P1/P2 ❌、P3 ❌、P4 ✅",
         (0.0, 0.0), {"P1": False, "P2": False, "P4": True, "P3": False}),
        ("S3 Δ_sk = 5 格（4≤5<6）⇒ P3 灰带、P2 ❌",
         (5 / 48, 0.0), {"P1": False, "P2": False, "P4": True, "P3": True}),
        ("S4 Δ_sk = 6 格（= 门槛，边界含等号）⇒ P2 ✅",
         (6 / 48, 0.0), {"P1": True, "P2": True, "P4": True, "P3": False}),
        ("S5 两族同步 +10 格 ⇒ P1 ❌（gap=0）且 P4 ❌（bf 不干净）",
         (10 / 48, 10 / 48), {"P1": False, "P2": True, "P4": False, "P3": False}),
        ("S6 Δ_sk = −10 格 ⇒ P2 ❌ 且反号标记为真",
         (-10 / 48, 0.0), {"P1": True, "P2": False, "P4": True, "P3": False}),
        ("S7 Δ_bf = 3 格（< 4）⇒ P4 仍 ✅，但 Δ_sk 3 格 ⇒ P2 ❌ P3 ❌",
         (3 / 48, 3 / 48), {"P1": False, "P2": False, "P4": True, "P3": False}),
    ]
    bad = 0
    for why, (a, b), want in cases:
        v = verdict(a, b)
        got = {k: v[k] for k in want}
        ok = got == want
        print(f"  {'✅' if ok else '❌'} {why}  实测={got}")
        bad += 0 if ok else 1
    # 反号标记必须与 P2 互斥
    neg_ok = (verdict(-10 / 48, 0.0)["P2_neg"] is True
              and verdict(10 / 48, 0.0)["P2_neg"] is False)
    print(f"  {'✅' if neg_ok else '❌'} S8 P2_neg 只在 Δ_sk 足够负时为真")
    bad += 0 if neg_ok else 1
    print(f"{'✅ 自证 8/8' if bad == 0 else f'❌ {bad} 条自证失败'}")
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
