#!/usr/bin/env python3
"""probe_v177 —— 行为级分数信息量与可动性主实验。判据 = PREREG_v177 §2。

P1 常数臂（含 claim 三合取的正/负对照）/ P2 单射臂 + 两层对照 /
P3 粗臂（bits 对手算）/ P4 弃权三条 + 陷阱（含 §0.3 反例）/
P5 变异体有牙 / P6 真池（sys_small 6 题，向量对拍 + 双层读数 + 双跑）/
P7 值域自洽 + T1/T2。

⛔ 判据写在 PREREG 里，本文件只负责执行与读数落盘。
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

EV = ROOT / "bench" / "evidence" / "l3_s265"
SUITE_PATH = ROOT / "bench" / "tasks" / "sys_small.jsonl"
SCORER_PATH = ROOT / "bench" / "scorers" / "code_exec.py"
PREREG = EV / "PREREG_v177.md"

RESULTS: list[dict] = []
READ: dict = {}


def say(msg: str = "") -> None:
    print(msg)


def check(tag: str, claim: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"tag": tag, "claim": claim, "ok": bool(ok), "detail": detail})
    say(f"  [{'PASS' if ok else 'FAIL'}] {tag}  {claim}")
    if detail:
        say(f"         {detail}")
    return bool(ok)


# ===========================================================================
# 构造臂（P5 变异体要能对它们做撤回 ⇒ 判据写成「函数 f 进、(ok, detail) 出」）
# ===========================================================================

def pool_const3():
    """3 行为同分 0.5（常数臂；行为是 3 维向量的三个单位位）。"""
    return [("a", (True, False, False), 0.5),
            ("b", (False, True, False), 0.5),
            ("c", (False, False, True), 0.5)]


def pool_const2():
    """2 行为同分 —— 反巧合合取的负对照（claim 不许给）。"""
    return [("a", (True, False), 0.5),
            ("b", (False, True), 0.5)]


def pool_const_full():
    """3 行为同分 1.0 —— 「做完了 ≠ 做不到」的负对照（claim 不许给）。"""
    return [("a", (True, False), 1.0),
            ("b", (False, True), 1.0),
            ("c", (True, True), 1.0)]


def pool_inj_two_layers():
    """单射臂 + 两层对照：A,B 同行为同分；C,D 异行为异分。"""
    return [("a", (True, True, False, False), 0.5),
            ("b", (True, True, False, False), 0.5),
            ("c", (True, True, True, False), 0.75),
            ("d", (True, True, True, True), 1.0)]


def pool_coarse():
    """粗臂：4 行为 3 分 —— 1000 与 0100 同权重撞 0.25（异行为同分）。"""
    return [("a", (True, False, False, False), 0.25),
            ("b", (False, True, False, False), 0.25),
            ("c", (True, True, True, False), 0.75),
            ("d", (True, True, True, True), 1.0)]


def pool_few():
    return [("a", (True, False), 0.5)]


def pool_ruler_out():
    return [("a", (True, False), 1.2), ("b", (False, True), 0.5)]


def pool_ruler_bool():
    return [("a", (True, False), True), ("b", (False, True), 0.5)]


def pool_ruler_nan():
    return [("a", (True, False), float("nan")), ("b", (False, True), 0.5)]


def pool_ruler_neg():
    return [("a", (True, False), -0.1), ("b", (False, True), 0.5)]


def pool_ruler_vecelem():
    return [("a", (1, 0), 0.5), ("b", (False, True), 1.0)]


def pool_ruler_emptyvec():
    return [("a", (), 0.5)]


def pool_ruler_lenmix():
    return [("a", (True, False), 0.5), ("b", (False, True), 0.5), ("c", (True,), 0.5)]


def pool_conf_beh():
    """同行为多分（违反同行为同分）。"""
    return [("a", (True, False), 0.5), ("b", (True, False), 0.7)]


def pool_conf_beh3():
    """同行为多分 + 第三种行为 —— P5「拆冲突闸」后必须能被分类（不再弃权）。"""
    return [("a", (True, False), 0.5), ("b", (True, False), 0.7),
            ("c", (False, True), 1.0)]


def pool_conf_impl():
    """同实现多行为（确定性判分下口径混了）。"""
    return [("a", (True, False), 0.5), ("a", (False, True), 0.5)]


def pool_t1_const():
    """T1 用：分数级也 CONSTANT 的干净池。"""
    return [("a", (True, False), 0.5), ("b", (False, True), 0.5), ("c", (True, True), 0.5)]


def pool_t2_inj():
    """T2 用：分数级 INJECTIVE（3 实现 3 分）。"""
    return [("a", (True, False), 0.5), ("b", (False, True), 0.75), ("c", (True, True), 1.0)]


def case_p1(f):
    r3, r2, rf = f(pool_const3()), f(pool_const2()), f(pool_const_full())
    ok = (r3["kind"] == "CONSTANT" and r3["claim"] == "BEHAVIOR_UNRESOLVED"
          and r3["bits"] == 0.0 and r3["n_behaviors"] == 3
          and r2["kind"] == "CONSTANT" and r2["claim"] is None
          and rf["kind"] == "CONSTANT" and rf["claim"] is None)
    return ok, (f"3行为 claim={r3['claim']}｜2行为 claim={r2['claim']}（期望 None）"
                f"｜1.0 池 claim={rf['claim']}（期望 None）")


def case_p2(f):
    rows = pool_inj_two_layers()
    r = f(rows)
    c = __import__("mw.scoreinfo", fromlist=["classify"]).classify(
        [(i, s) for i, _v, s in rows])
    ok = (r["kind"] == "INJECTIVE" and r["n_behaviors"] == 3 and r["n_impls"] == 4
          and r["resolution"] == 1.0 and c["kind"] == "COARSE")
    return ok, (f"行为级={r['kind']}(beh={r['n_behaviors']}/impl={r['n_impls']}) "
                f"分数级={c['kind']}(k={c['n_scores']}) —— 两层不同层")


def case_p3(f):
    r = f(pool_coarse())
    b025 = r["buckets"].get("0.25") or []
    ok = (r["kind"] == "COARSE" and r["n_behaviors"] == 4 and r["n_scores"] == 3
          and sorted(b025) == ["0100", "1000"]
          and abs(r["resolution"] - 0.75) < 1e-12
          and abs(r["bits"] - 1.5) < 1e-12)
    return ok, (f"kind={r['kind']} k/n={r['n_scores']}/{r['n_behaviors']} "
                f"0.25桶={b025} bits={r['bits']}（手算 1.5）")


def case_p4(f):
    arms = {
        "few": (pool_few(), "TOO_FEW"),
        "out": (pool_ruler_out(), "RULER_BROKEN"),
        "bool": (pool_ruler_bool(), "RULER_BROKEN"),
        "nan": (pool_ruler_nan(), "RULER_BROKEN"),
        "neg": (pool_ruler_neg(), "RULER_BROKEN"),
        "vecelem": (pool_ruler_vecelem(), "RULER_BROKEN"),
        "emptyvec": (pool_ruler_emptyvec(), "RULER_BROKEN"),
        "lenmix": (pool_ruler_lenmix(), "RULER_BROKEN"),
        "conf_beh": (pool_conf_beh(), "BEHAVIOR_SCORE_CONFLICT"),
        "conf_impl": (pool_conf_impl(), "BEHAVIOR_SCORE_CONFLICT"),
    }
    bad, detail = [], []
    for name, (pool, want) in arms.items():
        r = f(pool)
        good = (r["kind"] == "UNKNOWN" and r["reason"] == want
                and r["bits"] is None and r["buckets"] == {} and r["claim"] is None)
        if not good:
            bad.append(name)
        detail.append(f"{name}={r['kind']}/{r['reason']}")
    # §0.3 反例：同行为异分 ⇒ 行为级必须弃权，而分数级是 INJECTIVE
    SI = __import__("mw.scoreinfo", fromlist=["classify"])
    rc = SI.classify([(i, s) for i, _v, s in pool_conf_beh()])
    counter_ok = rc["kind"] == "INJECTIVE"
    return (not bad) and counter_ok, "｜".join(detail) + f"｜反例分数级={rc['kind']}"


def case_p7(f):
    pools = [pool_const3(), pool_const2(), pool_const_full(),
             pool_inj_two_layers(), pool_coarse(), pool_t1_const(), pool_t2_inj()]
    names = {"CONSTANT", "INJECTIVE", "COARSE", "UNKNOWN"}
    reasons = {"TOO_FEW", "RULER_BROKEN", "BEHAVIOR_SCORE_CONFLICT"}
    claims = {None, "BEHAVIOR_UNRESOLVED"}
    ok, why = True, []
    for p in pools:
        r = f(p)
        if r["kind"] not in names:
            ok = False
            why.append(f"值域外 kind={r['kind']}")
        if r["reason"] is not None and r["reason"] not in reasons:
            ok = False
            why.append(f"值域外 reason={r['reason']}")
        if r["claim"] not in claims:
            ok = False
            why.append(f"值域外 claim={r['claim']}")
        if r["kind"] == "CONSTANT" and r["bits"] != 0.0:
            ok = False
            why.append("CONSTANT bits≠0")
        if r["resolution"] is not None and not (0.0 < r["resolution"] <= 1.0):
            ok = False
            why.append(f"resolution 越界 {r['resolution']}")
        if r["bits"] is not None and r["bits_max"] is not None \
                and r["bits"] > r["bits_max"] + 1e-12:
            ok = False
            why.append("bits > bits_max")
    # T1/T2（前提=两边都不弃权）
    SI = __import__("mw.scoreinfo", fromlist=["classify", "behavioral"])
    for p, side in ((pool_t1_const(), "CONST"), (pool_t2_inj(), "INJ"),
                    (pool_inj_two_layers(), "TWO")):
        b, c = SI.behavioral(p), SI.classify([(i, s) for i, _v, s in p])
        if b["kind"] == "UNKNOWN" or c["kind"] == "UNKNOWN":
            ok = False
            why.append(f"T 池弃权 {side}")
            continue
        if (b["kind"] == "CONSTANT") != (c["kind"] == "CONSTANT"):
            ok = False
            why.append(f"T1 破 {side}: beh={b['kind']} score={c['kind']}")
        if c["kind"] == "INJECTIVE" and b["kind"] != "INJECTIVE":
            ok = False
            why.append(f"T2 破 {side}: beh={b['kind']} score={c['kind']}")
    return ok, "｜".join(why) if why else "值域封闭 + T1/T2 成立（3 池）"


# ===========================================================================
# P6 真池：sys_small × {oracle, partial, null} ∪ mutants
# ===========================================================================

def collect_pool():
    """逐题收 (impl, 通过向量, 分数)。向量走 code_exec.run_one（打分器同一机制），
    与 ExternalScorer 实得分对拍 sum(vec)/len == score。⛔ 对拍不过 ⇒ 返回 err。"""
    from mw.bench import ExternalScorer, Suite
    sys.path.insert(0, str(SCORER_PATH.parent))
    import code_exec
    sys.path.insert(0, str(ROOT / "bench"))
    import admit_sys as AS

    scorer = ExternalScorer([sys.executable, str(SCORER_PATH)], source=SCORER_PATH)
    suite = Suite.load(SUITE_PATH)
    out, err = {}, []
    for t in suite.tasks:
        d = t.as_dict()
        answers = {"oracle": t.oracle, "partial": t.partial, "null": t.null}
        answers.update(dict(AS.MUTANTS.get(t.id, [])))
        rows = []
        for name in sorted(answers):
            ans = answers[name]
            code = code_exec.extract_code(str(ans))
            vec = tuple(bool(code_exec.run_one(code, str(x))[0]) for x in t.tests)
            score = float(scorer.score(d, ans).score)
            expect = sum(vec) / len(vec)
            if abs(expect - score) > 1e-9:
                err.append(f"{t.id}:{name} 对拍不过 vec均值={expect} 实得分={score}")
            rows.append((name, vec, score))
        out[t.id] = rows
    return out, err


def main() -> int:
    t0 = time.time()
    say("=" * 74)
    say("probe_v177  行为级分数信息量与可动性（PREREG_v177 §2）")
    say("=" * 74)
    prereg_mtime = PREREG.stat().st_mtime
    READ["timeline"] = {"prereg_mtime": prereg_mtime, "probe_start": t0,
                        "probe_start_iso": time.strftime("%F %T", time.localtime(t0))}

    from mw import goals as G
    from mw import scoreinfo as SI

    # ---------- P1 常数臂（claim 三合取正/负对照） ----------
    say("\nP1 常数臂")
    ok, d = case_p1(SI.behavioral)
    check("P1", "3 行为同分 ⇒ CONSTANT + claim 触发；2 行为同分 ⇒ 无 claim（反巧合）；"
                "CONSTANT@1.0 ⇒ 无 claim（做完了≠做不到）", ok, d)

    # ---------- P2 单射臂 + 两层对照 ----------
    say("\nP2 单射臂 + 两层对照")
    ok, d = case_p2(SI.behavioral)
    check("P2", "A,B 同行为同分 + C,D 异行为异分 ⇒ 行为级 INJECTIVE，"
                "同池分数级 classify()=COARSE（两层不同层）", ok, d)

    # ---------- P3 粗臂 ----------
    say("\nP3 粗臂")
    ok, d = case_p3(SI.behavioral)
    check("P3", "4 行为 3 分（1000/0100 同权重撞 0.25）⇒ COARSE，逐行为点名，"
                "bits==1.5 对上手算", ok, d)

    # ---------- P4 弃权三条 + 陷阱 + §0.3 反例 ----------
    say("\nP4 弃权三条 + 陷阱")
    ok, d = case_p4(SI.behavioral)
    check("P4", "TOO_FEW/RULER_BROKEN(越界·bool·NaN·负·非bool向量·空向量·长度不齐)/"
                "BEHAVIOR_SCORE_CONFLICT(同行为多分·同实现多行为) 全弃权不给半个读数；"
                "§0.3 反例：同行为异分池分数级=INJECTIVE", ok, d)

    # ---------- P5 变异体有牙 ----------
    say("\nP5 变异体有牙（撤回态）")
    real = SI.behavioral

    def _mut_const(rows):
        r = real(rows)
        r["kind"] = "CONSTANT"
        r["bits"] = 0.0
        return r

    def _mut_inj(rows):
        r = real(rows)
        r["kind"] = "INJECTIVE"
        return r

    def _mut_no_conf_gate(rows):
        # 拆冲突闸的可观测形态：同行为静默取首个分（冲突不再弹出）
        seen = {}
        for r in rows:
            seen.setdefault(r[1], r)
        return real(list(seen.values()))

    try:
        SI.behavioral = _mut_const
        p2m, d2m = case_p2(SI.behavioral)
        p3m, d3m = case_p3(SI.behavioral)
        SI.behavioral = _mut_inj
        p1m, d1m = case_p1(SI.behavioral)
        SI.behavioral = _mut_no_conf_gate
        r_gate = SI.behavioral(pool_conf_beh3())
    finally:
        SI.behavioral = real
    gate_broken = r_gate["kind"] != "UNKNOWN" or \
        r_gate["reason"] != "BEHAVIOR_SCORE_CONFLICT"
    ok5 = (not p2m) and (not p3m) and (not p1m) and gate_broken
    check("P5", "恒 CONSTANT ⇒ P2/P3 转红；恒 INJECTIVE ⇒ P1 转红；\n"
                "              拆冲突闸（去重旁路）⇒ 同行为多分臂不再按冲突弃权",
          ok5,
          f"恒CONSTANT下 P2={p2m} P3={p3m}（期望 False）｜恒INJECTIVE下 P1={p1m}"
          f"（期望 False）｜拆闸后冲突臂={r_gate['kind']}/{r_gate['reason']}"
          f"（期望≠冲突弃权）｜已还原={SI.behavioral is real}")
    READ["p5"] = {"const_mut_p2": p2m, "const_mut_p3": p3m, "inj_mut_p1": p1m,
                  "no_gate_conflict": r_gate}

    # ---------- P6 真池（sys_small） ----------
    say("\nP6 真池（sys_small 6 题 × oracle/partial/null/mutants）")
    pool, err = collect_pool()
    check("P6a", "装置对拍：逐 (题,实现) sum(vec)/len == ExternalScorer 实得分（1e-9）",
          not err, "；".join(err) if err else f"{sum(len(v) for v in pool.values())} 行全对上")
    per_task = {}
    all_det = True
    for tid in sorted(pool):
        rows = pool[tid]
        b = SI.behavioral(rows)
        b2 = SI.behavioral(rows)
        c = SI.classify([(i, s) for i, _v, s in rows])
        det = (b == b2)
        all_det = all_det and det
        per_task[tid] = {
            "rows": [{"impl": i, "vec": "".join("1" if x else "0" for x in v),
                      "score": s} for i, v, s in rows],
            "behavioral": b, "score_level": {"kind": c["kind"],
                                             "n_impls": c["n_impls"],
                                             "n_scores": c["n_scores"]},
            "determinism": det,
        }
    READ["p6"] = per_task
    check("P6b", "逐题 behavioral() + classify() 双层读数落盘，双跑逐字相同",
          all_det and len(per_task) == 6,
          "｜".join(f"{t}:{v['behavioral']['kind']}"
                    f"(beh={v['behavioral']['n_behaviors']},"
                    f"k={v['behavioral']['n_scores']},"
                    f"claim={v['behavioral']['claim']})/"
                    f"{v['score_level']['kind']}"
                    for t, v in sorted(per_task.items())))

    # ---------- P7 值域自洽 + T1/T2 ----------
    say("\nP7 值域自洽 + T1/T2")
    ok, d = case_p7(SI.behavioral)
    check("P7", "kind 恰 4 名、reason 恰 3 名、claim 恰 2 值、resolution/bits 区间恒成立；"
                "T1（CONSTANT⟺CONSTANT）与 T2（分数单射⇒行为单射）在构造池上成立", ok, d)

    # ---------- 落盘 ----------
    READ["verdicts"] = RESULTS
    READ["repair_axes_smoke"] = {
        "BEHAVIOR_UNRESOLVED": list(G.repair_axes("STUCK_MEANS",
                                                  {"claim": SI.CLAIM_BEHAVIOR_UNRESOLVED})),
        "SCORE_UNMOVABLE": list(G.repair_axes("STUCK_MEANS",
                                              {"claim": SI.CLAIM_UNMOVABLE})),
        "none": list(G.repair_axes("STUCK_MEANS", {"claim": None})),
    }
    READ["timeline"]["probe_end"] = time.time()
    OUT = EV / "readings_v177.json"
    OUT.write_text(json.dumps(READ, ensure_ascii=False, indent=1, default=str),
                   encoding="utf-8")
    n_ok = sum(1 for v in RESULTS if v["ok"])
    say()
    say(f"判据 {n_ok}/{len(RESULTS)}")
    say(f"读数 → {OUT}")
    return 0 if n_ok == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
