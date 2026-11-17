#!/usr/bin/env python3
"""V180 判据 —— 让 COARSE 也给轴：接线 + 值域不动 + 部署不动 + 有牙。

对应 `PREREG_v180.md` §3（P1~P7）。零模型零网络。
真尺子只用于**重建池**（与 STAGE0 同一台）；判据本身只调纯函数。

用法：python3.13 bench/evidence/l3_s268/v180.py
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

from mw import goals as G          # noqa: E402
from mw import scoreinfo as SI     # noqa: E402
from mw.bench import Suite         # noqa: E402
import admit_sys                    # noqa: E402

SUITE = ROOT / "bench" / "tasks" / "sys_small.jsonl"
SCORER = ROOT / "bench" / "scorers" / "code_exec.py"
GOALS = ROOT / "mw" / "goals.py"
STAGE0 = HERE / "readings_stage0_v180.json"
OUT = HERE / "readings_v180.json"
WORKER = HERE / "_axes_worker.py"

CASES: list[dict] = []
OUTD: dict = {}


def verdict(pid: str, ok: bool, detail: str = "") -> bool:
    CASES.append({"id": pid, "ok": bool(ok), "detail": detail})
    print(f"  {'✅' if ok else '❌'} {pid} {detail}")
    return bool(ok)


def say(s: str = "") -> None:
    print(s)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ══════════════════════════════════════════════════════════════════════
# 池重建（与 STAGE0 同一台装置）
# ══════════════════════════════════════════════════════════════════════
def score_one(task: dict, answer: str) -> dict:
    p = subprocess.run([sys.executable, str(SCORER)],
                       input=json.dumps({"task": task, "answer": answer}),
                       capture_output=True, text=True, timeout=120)
    line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
    return json.loads(line) if line else {"error": f"rc={p.returncode}"}


def build_rows() -> list[dict]:
    rows: list[dict] = []
    for t in Suite.load(SUITE).tasks:
        d = t.as_dict()
        answers = [("oracle", t.oracle), ("partial", t.partial), ("null", t.null)]
        answers += list(admit_sys.MUTANTS.get(t.id, []))
        for name, src in answers:
            r = score_one(d, src)
            row = {"task_id": t.id, "impl_sha": f"{t.id}:{name}", "score": r.get("score")}
            if r.get("vec") is not None:
                row["vec"] = r["vec"]
            rows.append(row)
    return rows


def direct_tie(rows: list[dict], cell: str) -> bool:
    by: dict[float, set] = {}
    for r in rows:
        if r.get("task_id") == cell and "vec" in r and r.get("score") is not None:
            by.setdefault(float(r["score"]), set()).add(tuple(r["vec"]))
    return any(len(v) > 1 for v in by.values())


def forced_log(cells: list[str], mean: float = 0.0) -> list[dict]:
    """每格造 3 次「连判 NOT_MET 且均分逐位相同」—— 只为让 esc 里有这些格。"""
    log: list[dict] = []
    for c in cells:
        for _ in range(3):
            log.append({"gid": f"g:{c}", "cell": c, "kind": "RAISE",
                        "verdict": "NOT_MET", "reading": {"mean_score": mean}})
    return log


# ══════════════════════════════════════════════════════════════════════
# 变异体（参考实现）—— ⛔ 只用来**证明判据有牙**，不参与产品
# ══════════════════════════════════════════════════════════════════════
def axes_pre(reason: str, m: dict | None) -> tuple[str, ...]:
    """改动前的行为（V175 原样）：只有 claim 能加 RULER。"""
    ax: list[str] = []
    if reason == "CRITERION_UNREACHABLE":
        ax.append("CRITERION")
    elif reason == "STUCK_MEANS":
        ax.append("MODEL")
    if m and m.get("claim") in (SI.CLAIM_UNMOVABLE, SI.CLAIM_BEHAVIOR_UNRESOLVED):
        ax.append("RULER")
    return tuple(ax)


def axes_mut_never(reason: str, m: dict | None) -> tuple[str, ...]:
    """变异体 A：既然有 beh 也**恒不给** RULER（= 没接线）。"""
    return axes_pre(reason, m)


def axes_mut_non_inj(reason: str, m: dict | None) -> tuple[str, ...]:
    """变异体 B：**非 INJECTIVE** 就给 RULER（把 COARSE 的判据放宽到 CONSTANT）。"""
    ax = list(axes_pre(reason, m))
    beh = (m or {}).get("beh") or {}
    if beh.get("kind") not in (None, SI.KIND_INJECTIVE) and "RULER" not in ax:
        ax.append("RULER")
    return tuple(ax)


# ══════════════════════════════════════════════════════════════════════
# 两臂：换文件真跑（纪律 65 —— 装置自报导入了谁）
# ══════════════════════════════════════════════════════════════════════
def run_arm(obs: list[dict], log: list[dict], want_sha: str | None = None) -> dict:
    p = subprocess.run([sys.executable, str(WORKER)],
                       input=json.dumps({"obs": obs, "log": log}),
                       capture_output=True, text=True, cwd=str(ROOT), timeout=300)
    line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
    if not line:
        return {"error": f"rc={p.returncode}", "stderr": p.stderr[-400:]}
    d = json.loads(line)
    d["sha_ok"] = (want_sha is None or d.get("sha") == want_sha)
    return d


def main() -> int:
    t0 = time.time()
    say("V180 判据 —— 让 COARSE 也给轴")

    rows = build_rows()
    cells = sorted({r["task_id"] for r in rows})
    old = json.loads(STAGE0.read_text(encoding="utf-8"))["rows"] if STAGE0.exists() else []
    def norm(rs):
        return [(r["task_id"], r["impl_sha"], r.get("score"),
                 tuple(r["vec"]) if "vec" in r else None) for r in rs]
    say(f"\n  池：{len(cells)} 格 / {len(rows)} 行（{time.time()-t0:.1f}s）"
        f"｜与 STAGE0 逐行相同={norm(rows) == norm(old)}")

    log6 = forced_log(cells)
    esc6 = G.escalations(log6, rows)
    ov = {c: esc6[c].get("movability") for c in esc6}
    say(f"  esc 里 {len(esc6)} 格；overlay 带 beh 的 {sum(1 for m in ov.values() if m and m.get('beh'))} 格")

    # ── P1 真池：R_score 的假正例 vs R_beh 的集合 ──────────────────────
    fp_score = [c for c in cells
                if SI.movability(rows, cell=c).get("label") == SI.KIND_COARSE
                and not direct_tie(rows, c)]
    tie = [c for c in cells if direct_tie(rows, c)]
    r_beh_set = [c for c in sorted(esc6) if "RULER" in G.repair_axes(
        str(esc6[c].get("reason") or ""), ov[c])]
    say(f"  R_score 假正例 {len(fp_score)}：{fp_score}")
    say(f"  轴里拿到 RULER 的格 {len(r_beh_set)}：{r_beh_set}｜直接数出异行为同分 {len(tie)}：{tie}")
    verdict("P1", len(fp_score) >= 4 and set(r_beh_set) == set(tie)
            and not (set(r_beh_set) - set(tie)),
            f"分母={len(cells)} 格｜R_score 假正例 {len(fp_score)}｜"
            f"R_beh 集合={r_beh_set} == 直接数={tie}｜R_beh 假正例 {len(set(r_beh_set)-set(tie))}")
    OUTD["p1"] = {"fp_score": fp_score, "r_beh": r_beh_set, "tie": tie}

    # ── P2 门限与 claim 同源：n_behaviors=2 不给 ──────────────────────
    b2 = {"kind": SI.KIND_COARSE, "n_behaviors": 2}
    b3 = {"kind": SI.KIND_COARSE, "n_behaviors": 3}
    a2 = G.repair_axes("STUCK_MEANS", {"claim": None, "beh": b2})
    a3 = G.repair_axes("STUCK_MEANS", {"claim": None, "beh": b3})
    verdict("P2", "RULER" not in a2 and "RULER" in a3,
            f"n=2 ⇒ {a2}｜n=3 ⇒ {a3}（MIN_BEHAVIORS={SI.MIN_BEHAVIORS}）")

    # ── P3 旧输入逐个不变 ────────────────────────────────────────────
    old_cases = [
        ("CRITERION_UNREACHABLE", None, ("CRITERION",)),
        ("CRITERION_UNREACHABLE", {"claim": SI.CLAIM_UNMOVABLE}, ("CRITERION", "RULER")),
        ("STUCK_MEANS", None, ("MODEL",)),
        ("STUCK_MEANS", {"claim": SI.CLAIM_UNMOVABLE}, ("MODEL", "RULER")),
        ("STUCK_MEANS", {"claim": None}, ("MODEL",)),
        ("X", None, ()),
        ("X", {"claim": SI.CLAIM_BEHAVIOR_UNRESOLVED}, ("RULER",)),
    ]
    got = [(r, m, G.repair_axes(r, m)) for r, m, _ in old_cases]
    verdict("P3", all(g[2] == want for g, (_, _, want) in zip(got, old_cases)),
            "｜".join(f"{r}/{'-' if not m else ','.join(sorted(m))}={v}" for r, m, v in got))

    # ── P4 去重：claim 与 beh 同时要求 RULER ─────────────────────────
    a4 = G.repair_axes("STUCK_MEANS",
                       {"claim": SI.CLAIM_UNMOVABLE, "beh": b3})
    verdict("P4", a4.count("RULER") == 1 and a4 == ("MODEL", "RULER"),
            f"claim+beh ⇒ {a4}｜RULER 出现 {a4.count('RULER')} 次")

    # ── P5 overlay 真的带上了行为读数；无 vec 不许写成 INJECTIVE ───────
    with_beh = [c for c, m in ov.items() if m and (m.get("beh") or {}).get("kind")]
    no_vec_rows = [{"task_id": "nv", "impl_sha": f"i{i}", "score": 0.3} for i in range(3)]
    esc_nv = G.escalations(forced_log(["nv"]), no_vec_rows)
    m_nv = esc_nv.get("nv", {}).get("movability")
    ok5 = (len(with_beh) == len([c for c in cells if c in esc6])
           and m_nv is not None and "beh" not in (m_nv or {}))
    verdict("P5", ok5,
            f"带 vec 的格 {len(with_beh)}/{len(esc6)} 有 beh｜无 vec 格 beh 键={'有' if (m_nv or {}).get('beh') else '无'}（⛔ 不许写 INJECTIVE）")
    OUTD["p5"] = {"n_with_beh": len(with_beh), "no_vec_beh": (m_nv or {}).get("beh")}

    # ── P6 两臂：部署逐格相同 + 带 vec 池恰 m02 不同 ───────────────────
    new_sha = sha(GOALS)
    old_src = subprocess.run(["git", "show", "HEAD:mw/goals.py"], cwd=str(ROOT),
                             capture_output=True, text=True).stdout
    assert old_src.strip(), "取不到 HEAD 版 goals.py"

    from mw import config as cfg
    led = cfg.Paths().score_ledger
    dep = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines() if x.strip()] \
        if led.exists() else []
    dep_cells = sorted({r.get("task_id") for r in dep if isinstance(r, dict)})
    dep_log = forced_log(dep_cells[:3])

    arm_new_dep = run_arm(dep, dep_log, want_sha=new_sha)
    arm_new_pool = run_arm(rows, log6, want_sha=new_sha)

    tmp = Path(tempfile.mkdtemp(prefix="v180_arms_"))
    bak = tmp / "goals_new.py"
    bak.write_bytes(GOALS.read_bytes())
    try:
        GOALS.write_bytes(old_src.encode("utf-8"))
        old_sha = sha(GOALS)
        arm_old_dep = run_arm(dep, dep_log, want_sha=old_sha)
        arm_old_pool = run_arm(rows, log6, want_sha=old_sha)
    finally:
        GOALS.write_bytes(bak.read_bytes())
    restored = sha(GOALS) == new_sha

    say(f"\n  两臂自报：新 {arm_new_dep.get('sha','')[:12]} / 旧 {arm_old_dep.get('sha','')[:12]}"
        f"｜还原后 sha 一致={restored}")
    dep_diff = {c: (arm_old_dep.get("axes", {}).get(c), arm_new_dep.get("axes", {}).get(c))
                for c in set(arm_old_dep.get("axes", {})) | set(arm_new_dep.get("axes", {}))
                if arm_old_dep.get("axes", {}).get(c) != arm_new_dep.get("axes", {}).get(c)}
    pool_diff = {c: (arm_old_pool.get("axes", {}).get(c), arm_new_pool.get("axes", {}).get(c))
                 for c in set(arm_old_pool.get("axes", {})) | set(arm_new_pool.get("axes", {}))
                 if arm_old_pool.get("axes", {}).get(c) != arm_new_pool.get("axes", {}).get(c)}
    say(f"  部署账本：{len(dep)} 行 / {len(dep_cells)} 格 / esc "
        f"{len(arm_new_dep.get('axes', {}))} 格 ⇒ 轴不同 {len(dep_diff)} 格 {dep_diff}")
    say(f"  带 vec 池：esc {len(arm_new_pool.get('axes', {}))} 格 ⇒ 轴不同 {len(pool_diff)} 格")
    for c, (o, n) in sorted(pool_diff.items()):
        say(f"    {c:<16} 旧 {o} → 新 {n}")
    ok6 = (arm_new_dep.get("sha_ok") and arm_old_dep.get("sha_ok") and restored
           and not dep_diff and set(pool_diff) == set(tie))
    verdict("P6", ok6,
            f"部署轴不同 {len(dep_diff)} 格（C 成立）｜带 vec 池不同 {sorted(pool_diff)}（= 直接数出的 {tie}）"
            f"｜两臂自报 sha 对上={arm_new_dep.get('sha_ok') and arm_old_dep.get('sha_ok')}｜还原={restored}")
    OUTD["p6"] = {"dep_diff": dep_diff, "pool_diff": {k: list(map(list, v))
                                                     for k, v in pool_diff.items()},
                  "dep_cells": len(dep_cells), "dep_esc": len(arm_new_dep.get("axes", {}))}

    # ── P7 有牙：两个变异体 ─────────────────────────────────────────
    def r_beh_set_of(fn) -> set[str]:
        return {c for c in esc6 if "RULER" in fn(str(esc6[c].get("reason") or ""), ov[c])}

    never = r_beh_set_of(axes_mut_never)
    noninj = r_beh_set_of(axes_mut_non_inj)
    real = r_beh_set_of(G.repair_axes)
    p1_never = (never == set(tie))              # P1 的「集合相等」这一半
    p2_never = "RULER" in axes_mut_never("STUCK_MEANS", {"claim": None, "beh": b3})
    p1_noninj = (noninj == set(tie))
    say(f"\n  变异体 A（恒不给）：集合={sorted(never)} ⇒ P1 {p1_never}、P2 {p2_never}")
    say(f"  变异体 B（非 INJECTIVE 就给）：集合={sorted(noninj)} ⇒ P1 {p1_noninj}"
        f"（与真值 {sorted(real)} 相同={noninj == real}）")
    verdict("P7", (not p1_never) and (not p2_never) and (not p1_noninj),
            f"A：P1 {'仍成立(❌无牙)' if p1_never else '转红'}、P2 {'仍成立(❌无牙)' if p2_never else '转红'}｜"
            f"B：P1 {'仍成立(❌哑火)' if p1_noninj else '转红'}"
            f"（B 集合与真值相同={noninj == real}）")
    OUTD["p7"] = {"real": sorted(real), "never": sorted(never), "noninj": sorted(noninj),
                  "p1_never": p1_never, "p2_never": p2_never, "p1_noninj": p1_noninj}

    # ── P8（PREREG_v180_v2 追加）：用构造格把变异体 B 与真规则分开 ─────
    # 「3 行为类、全 1.0」= 所有行为都通过 ⇒ 分数没分辨力是**不需要**分辨，
    # 不是尺子坏（同 AZ137f：已达标不给 claim）。真池里没有这种格，
    # 所以 P7 的 B 半哑火 —— 这里补上它本该咬的那一口。
    sat_rows = [{"task_id": "sat", "impl_sha": f"s{i}", "score": 1.0,
                 "vec": [i == 0, i == 1, i == 2]} for i in range(3)]
    esc_sat = G.escalations(forced_log(["sat"], mean=1.0), sat_rows)
    m_sat = (esc_sat.get("sat") or {}).get("movability")
    a_sat_real = G.repair_axes("STUCK_MEANS", m_sat)
    a_sat_mut = axes_mut_non_inj("STUCK_MEANS", m_sat)
    say(f"\n  全 1.0 构造格：beh={((m_sat or {}).get('beh') or {})}｜"
        f"真={a_sat_real}｜B={a_sat_mut}")
    verdict("P8", "RULER" not in a_sat_real and "RULER" in a_sat_mut,
            f"真规则 {'不给' if 'RULER' not in a_sat_real else '给'} RULER｜"
            f"变异体 B {'给' if 'RULER' in a_sat_mut else '不给'}｜"
            f"⚠️ 该构造的 beh 为 {json.dumps((m_sat or {}).get('beh'))} —— "
            f"「3 行为类 + 全 1.0」过不了行内自洽闸（见 PREREG_v180_v3 §1）"
            f"⇒ 这一条测的是「构造不出来」，不是「两者不可分」")
    OUTD["p8"] = {"real": list(a_sat_real), "mut": list(a_sat_mut),
                  "beh": (m_sat or {}).get("beh")}

    # ── P9（PREREG_v180_v3）：B 与真规则在账本路径上**等价** ──────────
    # 理由：行过一致性闸 ⇒ score == mean(vec)；行为 CONSTANT 且 ≥2 类
    # ⇒ 分数恒 <1.0 ⇒ 分数级 claim 必触发 ⇒ 真规则本来也给 RULER。
    def overlay_of(rs: list[dict], cell: str) -> dict | None:
        esc_x = G.escalations(forced_log([cell], mean=0.0), rs)
        return (esc_x.get(cell) or {}).get("movability")

    pools: dict[str, tuple[list[dict], str]] = {}
    pools["real:" + cells[0]] = (rows, cells[0])
    pools["real:m02_vfs"] = (rows, "m02_vfs")
    pools["const3"] = ([{"task_id": "c3", "impl_sha": f"i{i}", "score": 1 / 3,
                         "vec": [j == i for j in range(3)]} for i in range(3)], "c3")
    pools["inject"] = ([{"task_id": "ij", "impl_sha": "a", "score": 1.0, "vec": [True] * 3},
                        {"task_id": "ij", "impl_sha": "b", "score": 0.0, "vec": [False] * 3},
                        {"task_id": "ij", "impl_sha": "c", "score": 2 / 3,
                         "vec": [True, True, False]}], "ij")
    pools["coarse"] = ([{"task_id": "co", "impl_sha": "a", "score": 1.0, "vec": [True] * 3},
                        {"task_id": "co", "impl_sha": "b", "score": 0.0, "vec": [False] * 3},
                        {"task_id": "co", "impl_sha": "c", "score": 0.0, "vec": [False, False, True]},
                        {"task_id": "co", "impl_sha": "d", "score": 0.0, "vec": [False, True, False]}],
                       "co")
    pools["sat1"] = ([{"task_id": "s1", "impl_sha": f"i{i}", "score": 1.0,
                       "vec": [True] * 3} for i in range(3)], "s1")

    diff: dict[str, tuple] = {}
    for name, (rs, cell) in pools.items():
        m = overlay_of(rs, cell)
        for reason in ("STUCK_MEANS", "CRITERION_UNREACHABLE"):
            a, b = G.repair_axes(reason, m), axes_mut_non_inj(reason, m)
            if a != b:
                diff[f"{name}/{reason}"] = (list(a), list(b))
        say(f"    {name:<14} beh={((m or {}).get('beh') or {}).get('kind')}"
            f" claim={(m or {}).get('claim')} ⇒ 真={G.repair_axes('STUCK_MEANS', m)}"
            f" B={axes_mut_non_inj('STUCK_MEANS', m)}")
    say(f"  差集：{len(diff)} 处 {diff}")
    verdict("P9", not diff,
            f"分母={len(pools)} 池 × 2 reason｜B 与真规则差集 {len(diff)} 处"
            f"（0 ⇒ B 不是错的变异体，是同一个规则的另一种写法）")
    OUTD["p9"] = {"pools": list(pools), "diff": diff}

    ok = sum(1 for c in CASES if c["ok"])
    say(f"\n## 汇总 {ok} PASS / {len(CASES)-ok} FAIL   （{time.time()-t0:.1f}s）")
    OUTD.update({"prereg": "PREREG_v180.md", "cases": CASES,
                 "summary": {"n": len(CASES), "ok": ok, "pass": ok == len(CASES)},
                 "rows": rows})
    OUT.write_text(json.dumps(OUTD, ensure_ascii=False, indent=1), encoding="utf-8")
    say(f"  读数 {OUT}")
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
