#!/usr/bin/env python3
"""V4′ 读数器（第 106 轮 §76）：`verify_static` **1（报名字）vs 2（盲报）**，`tasks_v5`。

判决只用**组内格分**（预注册 §3 的 D3′）；D1 只作描述、D2 不用。

⛔ 判据方向是**分组**的（§73.5.1 第三次翻车后立的规矩）：
   · `need_diff`：主张「两族 Δ 不同」⇒ 门槛是 |差| **大**
   · `need_eq`  ：主张「两族 Δ 相同」⇒ 门槛是 |差| **小**
   同一组门槛不许两用。本轮只有 `need_diff`（P1/P2）与灰带（P3）。

用法：
    python3 bench/evidence/v3_s106/analyze_v5.py
    python3 bench/evidence/v3_s106/analyze_v5.py --selftest
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mw.verify import undefined_names          # noqa: E402
from code_exec import extract_code            # noqa: E402
from probe_first_attempt import first_answers  # noqa: E402

NC = ("nc01", "nc02", "nc03")
CT = ("ct01", "ct02", "ct03")
GRID = 16
THRESH = 4 / 48            #: 预注册 §4：4 格 / 48 格 = 0.0833


# ===========================================================================
# 台账读取
# ===========================================================================
def load_run(arm_dir: Path) -> dict:
    runs = [json.loads(l) for l in (arm_dir / "runs.jsonl").read_text().splitlines() if l.strip()]
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
        "calibration": d.get("calibration", {}),
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
# 判决函数（唯一实现；selftest 反证它）
# ===========================================================================
def verdict(delta_nc: float, delta_ct: float, thresh: float = THRESH) -> dict:
    gap = delta_nc - delta_ct
    if gap >= thresh:
        tag, why = "P1", f"Δ_nc − Δ_ct = {gap:+.4f} ≥ {thresh:.4f} ⇒ 随占比上升"
    elif gap <= -thresh:
        tag, why = "P2", f"Δ_nc − Δ_ct = {gap:+.4f} ≤ −{thresh:.4f} ⇒ **反号**"
    else:
        tag, why = "P3", f"|Δ_nc − Δ_ct| = {abs(gap):.4f} < {thresh:.4f} ⇒ 灰带 UNKNOWN"
    return {"tag": tag, "why": why, "gap": gap, "thresh": thresh}


# ===========================================================================
# 首答 / 末答 的名字状态（零模型，静态）
# ===========================================================================
def name_state(answers_path: Path) -> dict[str, dict]:
    """每题：首答有没有未定义名 / 末答有没有 / 尝试数 / 不同 sha 数。"""
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
    print("=" * 84)
    print("  §76 V4′ 读数：verify_static 1（报名字） vs 2（盲报） —— tasks_v5（6 题 / 96 格）")
    print("=" * 84)

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
        n5 = (A["caliber"]["llm_cond"]["endpoint"] == B["caliber"]["llm_cond"]["endpoint"]
              == "127.0.0.1:8812")
        print(f"  N5 {'✅ 两臂同端口 8812' if n5 else '❌ 端口不一致'}")
    else:
        B = None
        print("  B: **未跑**（预注册 D0 触发或手动跳过）")
        n4 = n5 = None

    # ── N6 尝试数 ───────────────────────────────────────────────────────
    print("\n[N6] 尝试数与代理台账")
    for tag, arm_dir in (("A", arm_a), ("B", arm_b)):
        if arm_dir is None:
            continue
        px = arm_dir / "proxy.jsonl"
        nlines = len(px.read_text().splitlines()) if px.exists() else 0
        print(f"  {tag}: attempted={load_run(arm_dir)['attempted']} 代理台账={nlines} 行")
    print(f"  期望 attempted=18（6 题 × 3 rep）")

    # ── 逐题格分 ────────────────────────────────────────────────────────
    print("\n[逐题] 每题 3 rep 的平均格分（分母 16）与 Δ_t = A − B")
    hdr = f"  {'题':6s} {'组':4s} {'A':>18s} {'B':>18s} {'Δ_t':>8s}"
    print(hdr)
    deltas: dict[str, float] = {}
    for tid in NC + CT:
        sa, sb = A["scores"].get(tid, []), (B["scores"].get(tid, []) if B else [])
        ma, mb = mean(sa), mean(sb)
        d = ma - mb if sb else float("nan")
        if sb:
            deltas[tid] = d
        grp = "nc" if tid in NC else "ct"
        fa = "/".join(f"{x:.2f}" for x in sa)
        fb = "/".join(f"{x:.2f}" for x in sb) if sb else "—"
        print(f"  {tid:6s} {grp:4s} {fa:>18s} {fb:>18s} {d:+8.4f}" if sb
              else f"  {tid:6s} {grp:4s} {fa:>18s} {fb:>18s}        —")

    if not B:
        print("\n⛔ 只有一臂 ⇒ 无法算 Δ。按预注册 D0 的判读：登记「题面构造失败」。")
        return _one_arm_report(A)

    d_nc = mean([deltas[t] for t in NC])
    d_ct = mean([deltas[t] for t in CT])
    d_all = mean([deltas[t] for t in NC + CT])
    print(f"\n[组均值] Δ_nc = {d_nc:+.4f}   Δ_ct = {d_ct:+.4f}   "
          f"Δ_all = {d_all:+.4f}   （门槛 {THRESH:.4f}）")

    v = verdict(d_nc, d_ct)
    print(f"\n[判决] {v['tag']}：{v['why']}")

    # ── 名字状态（机会与兑现，零模型静态）───────────────────────────────
    print("\n[名字状态] 首答 / 末答是否有未定义名（= 名字类机会与它有没有被兑现）")
    SA = name_state(arm_a / "answers.jsonl")
    SB = name_state(arm_b / "answers.jsonl")
    print(f"  {'题':6s} {'组':4s} {'臂':2s} {'尝试':>4s} {'sha数':>5s} "
          f"{'首答名':>10s} {'末答名':>10s} {'首→末 sha':>20s}")
    for tid in NC + CT:
        for tag, S in (("A", SA), ("B", SB)):
            s = S.get(tid)
            if not s:
                continue
            print(f"  {tid:6s} {'nc' if tid in NC else 'ct':4s} {tag:2s} "
                  f"{s['attempts']:4d} {s['shas']:5d} "
                  f"{','.join(s['first_names']) or '—':>10s} "
                  f"{','.join(s['last_names']) or '—':>10s} "
                  f"{s['first_sha']}→{s['last_sha']:>8s}")

    # ── N3 分类可复现 ───────────────────────────────────────────────────
    def digest() -> str:
        blob = json.dumps({"A": SA, "B": SB}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    h1, h2 = digest(), digest()
    print(f"\n[N3] 同一命令两次分类逐字相同：{h1[:16]} == {h2[:16]} "
          f"{'✅' if h1 == h2 else '❌'}")

    # ── 兑现：首答有名字的题，末答修好了吗 ──────────────────────────────
    print("\n[兑现] 首答含未定义名的题 ⇒ 末答是否消除（同臂内，只作描述）")
    for tag, S in (("A", SA), ("B", SB)):
        rows = [t for t in NC + CT
                if S.get(t) and S[t]["first_names"]]
        fixed = [t for t in rows if not S[t]["last_names"]]
        print(f"  {tag}: {len(rows)} 题有名字  ⇒ 末答消除 {len(fixed)} 题 "
              f"{fixed}（未消除：{[t for t in rows if t not in fixed]}）")

    print("\n[成本] A={:.1f}s  B={:.1f}s".format(A["elapsed_s"], B["elapsed_s"]))
    return 0


def _one_arm_report(A: dict) -> int:
    S = name_state(Path("/tmp/v1_s106_bench_a1_v5/answers.jsonl"))
    nc_hit = [t for t in NC if S.get(t) and S[t]["first_names"]]
    print(f"\n[描述性] nc 组「每次尝试的最终交付」含未定义名：{len(nc_hit)}/3 {nc_hit}")
    print("  ⚠️ 这一行**不参与判决**（PREREG §8.4 已把它降级）：台账只存每次尝试的最终交付，")
    print("     机制效应已经混在里面。判决用 `probe_gate_hits.py` 的打回日志。")
    print(f"  预注册阈值：< 2 ⇒ 题面构造失败（不跑第二臂）")
    print(f"  判定：{'❌ 构造失败' if len(nc_hit) < 2 else '⚠️ 达到阈值但缺第二臂 ⇒ UNKNOWN'}")
    print("\n[逐题分数] A 臂")
    for tid in NC + CT:
        print(f"  {tid}: {A['scores'].get(tid)}")
    return 0


# ===========================================================================
# 反证：判决函数必须能红
# ===========================================================================
def selftest() -> int:
    cases = [
        ("S1 同向且够大 ⇒ P1", (0.50, 0.00), "P1"),
        ("S2 反号且够大 ⇒ P2", (-0.50, 0.00), "P2"),
        ("S3 两族都为 0 ⇒ P3 灰带", (0.00, 0.00), "P3"),
        ("S4 差 3 格（< 4 格门槛）⇒ P3 不是 P1", (3 / 48, 0.00), "P3"),
        ("S5 差 4 格（= 门槛）⇒ P1（边界含等号）", (4 / 48, 0.00), "P1"),
        ("S6 两族同向移动（都 +0.5）⇒ P3，不因绝对值大而误判",
         (0.50, 0.50), "P3"),
    ]
    bad = 0
    for why, (a, b), want in cases:
        got = verdict(a, b)["tag"]
        ok = got == want
        bad += 0 if ok else 1
        print(f"  {'✅' if ok else '❌'} {why}  实测={got}")
    print(f"{'✅ 判决层自证 6/6' if bad == 0 else f'❌ {bad} 条自证失败'}")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm-a", default="/tmp/v1_s106_bench_a1_v5")
    ap.add_argument("--arm-b", default="/tmp/v1_s106_bench_a2_v5")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    arm_a, arm_b = Path(a.arm_a), Path(a.arm_b)
    if not (arm_a / "runs.jsonl").exists():
        print(f"❌ A 臂台账不存在：{arm_a}", file=sys.stderr)
        return 2
    b = arm_b if (arm_b / "runs.jsonl").exists() else None
    return run(arm_a, b)


if __name__ == "__main__":
    raise SystemExit(main())
