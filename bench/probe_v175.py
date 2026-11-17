#!/usr/bin/env python3
"""V175 探针：P1~P6（PREREG_v175 §2 的判据唯一来源）。零模型零网络。

被测 = **本文件顶部 import 的** `mw.scoreinfo` / `mw/goals`（工作区当前版）+
`bench/evidence/v6_s245/` 的 V157 事件流框架（只读重放，0 次新判分）。

⛔ P 判据来自 PREREG_v175 §2，跑后不许改装置迁就（V174 P3 教训）。
"""
from __future__ import annotations

import ast
import collections
import json
import math
import pathlib
import random
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mw.scoreinfo as SI      # noqa: E402
import mw.goals as G           # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE / "evidence"
OUT = EV / "l3_s263" / "readings_v175.json"

results: list[dict] = []


def check(tag: str, claim: str, ok: bool, detail: str = "") -> None:
    results.append({"tag": tag, "claim": claim, "ok": bool(ok), "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {tag} {claim}")
    if detail:
        print(f"        {detail}")


def obs_rows(cell: str, scores, shas=None) -> list[dict]:
    rows = []
    for i, s in enumerate(scores):
        r = {"task_id": cell, "score": float(s)}
        if shas is not None:
            r["impl_sha"] = shas[i]
        rows.append(r)
    return rows


# ===========================================================================
# P1 池抽取可归因（工具层）
# ===========================================================================
def p1() -> dict:
    print("\n## P1 池抽取：缺身份计数排除、坏行不崩、有身份进池")
    rows = [
        {"task_id": "x", "impl_sha": "a", "score": 0.5},
        {"task_id": "x", "impl_sha": "b", "score": 0.5},
        {"task_id": "x", "score": 0.9},                 # 缺身份
        {"task_id": "x", "impl_sha": "c"},              # 缺分数
        {"task_id": "y", "impl_sha": "z", "score": 1.0},  # 其他格
    ]
    pool, n_no = SI.pool_from_rows(rows, cell="x")
    ok1 = (sorted(pool) == [("a", 0.5), ("b", 0.5)] and n_no == 2)
    pool_all, n_all = SI.pool_from_rows(rows, cell=None)
    ok2 = (len(pool_all) == 3 and n_all == 2)
    # 非 dict 坏行：全局计数、逐格不计（无从归属）
    bad = rows + ["not-a-dict"]
    _, n_bad_g = SI.pool_from_rows(bad, cell=None)
    _, n_bad_c = SI.pool_from_rows(bad, cell="x")
    ok3 = (n_bad_g == 3 and n_bad_c == 2)
    # impl_sha_of：同文同值、异文异值、定长
    ok4 = (SI.impl_sha_of("hello") == SI.impl_sha_of("hello")
           and SI.impl_sha_of("hello") != SI.impl_sha_of("hell")
           and len(SI.impl_sha_of("hello")) == 16)
    check("P1a", "缺身份/缺分数行计数排除（逐格 n_no_impl 精确），有身份行进池",
          ok1, f"pool={sorted(pool)} n_no={n_no}")
    check("P1b", "全局口径（cell=None）池含跨格行、n_no 同口径", ok2,
          f"pool={len(pool_all)} n_no={n_all}")
    check("P1c", "非 dict 坏行不崩：全局计入、逐格不计入（无从归属）", ok3,
          f"全局 n_no={n_bad_g} 逐格 n_no={n_bad_c}")
    check("P1d", "impl_sha_of 纯函数：同文同值、异文异值、恒 16 位", ok4)
    return {"p1_pool": pool, "n_no": n_no}


# ===========================================================================
# P2 overlay fail-open + 零回归
# ===========================================================================
def p2() -> dict:
    print("\n## P2 overlay：无池/缺身份/弃权 ⇒ None；旧字段逐位不变")
    # ① 无身份行 ⇒ overlay None，旧字段（reason/n_below/…）逐位等于 V173 形态
    o_legacy = obs_rows("c1", [0.0, 0.5, 1.0])
    esc = G.escalations([], o_legacy)
    e = esc.get("c1") or {}
    ok1 = (e.get("reason") == "CRITERION_UNREACHABLE" and e.get("n_below") == 2
           and e.get("n_obs") == 3 and abs(e.get("mean") - 0.5) < 1e-12
           and e.get("movability") is None)
    # ② 弃权（尺子坏 1.2）⇒ unreachable 也弃权 ⇒ 无格可查；直接查 movability 输出
    m_broken = SI.movability(obs_rows("c2", [0.0, 1.2, 1.2], ["a", "b", "c"]), cell="c2")
    ok2 = (m_broken["label"] == "UNKNOWN" and m_broken["claim"] is None)
    # ③ 行为路（STUCK_MEANS）无 obs ⇒ overlay None（无池）
    log = [{"gid": "g", "cell": "c3", "kind": "RAISE", "verdict": "NOT_MET",
            "reading": {"mean_score": 0.0}} for _ in range(3)]
    esc3 = G.escalations(log, [])
    ok3 = (esc3.get("c3", {}).get("reason") == "STUCK_MEANS"
           and esc3["c3"].get("movability") is None)
    # ④ 有池 ⇒ overlay 在场且 claim 齐全（负对照：别把 fail-open 写成恒 None）
    o_pool = [{"task_id": "c4", "score": 0.4, "impl_sha": f"i{i}"} for i in range(3)]
    esc4 = G.escalations([], o_pool)
    m4 = esc4["c4"].get("movability")
    ok4 = (m4 is not None and m4.get("claim") == "SCORE_UNMOVABLE"
           and m4.get("label") == "CONSTANT" and m4.get("n_impls") == 3)
    check("P2a", "无身份行 ⇒ overlay=None；结构路旧字段逐位不变", ok1,
          f"reason={e.get('reason')} n_below={e.get('n_below')} mean={e.get('mean')} "
          f"overlay={e.get('movability')}")
    check("P2b", "尺子坏（1.2）⇒ 弃权且 claim=None（不许拿坏读数断言尺子盲）", ok2,
          f"label={m_broken['label']} reason={m_broken['reason']}")
    check("P2c", "行为路无 obs ⇒ overlay=None（无池 fail-open）", ok3)
    check("P2d", "负对照：有池时 overlay 必须在场（fail-open 不是恒 None）", ok4,
          f"movability={m4}")
    return {}


# ===========================================================================
# P3 修复轴映射
# ===========================================================================
def p3() -> dict:
    print("\n## P3 repair_axes：值域封闭 3 名字、映射逐条、纯函数")
    claim_m = {"claim": "SCORE_UNMOVABLE", "label": "CONSTANT"}
    no_m = {"claim": None, "label": "COARSE"}
    cases = [
        ("CRITERION_UNREACHABLE", None, ("CRITERION",)),
        ("CRITERION_UNREACHABLE", claim_m, ("CRITERION", "RULER")),
        ("CRITERION_UNREACHABLE", no_m, ("CRITERION",)),
        ("STUCK_MEANS", None, ("MODEL",)),
        ("STUCK_MEANS", claim_m, ("MODEL", "RULER")),
        ("STUCK_MEANS", no_m, ("MODEL",)),
        ("SOMETHING_ELSE", claim_m, ("RULER",)),
        ("SOMETHING_ELSE", None, ()),
    ]
    ok_map = all(G.repair_axes(r, m) == want for r, m, want in cases)
    all_axes = set()
    for r, m, _ in cases:
        all_axes |= set(G.repair_axes(r, m))
    ok_domain = all_axes == {"CRITERION", "MODEL", "RULER"}
    ok_pure = all(G.repair_axes(r, m) == G.repair_axes(r, m) for r, m, _ in cases)
    check("P3a", "映射表逐条对（CRITERION/MODEL 主轴 + claim 叠加 RULER + 弃权不加）",
          ok_map)
    check("P3b", "值域恰 3 名字（CRITERION/MODEL/RULER）", ok_domain,
          f"实际值域={sorted(all_axes)}")
    check("P3c", "纯函数：同入同出", ok_pure)
    return {"axes_domain": sorted(all_axes)}


# ===========================================================================
# P4 覆盖定理实证
# ===========================================================================
def p4() -> dict:
    print("\n## P4 覆盖定理：claim 触发的格 ⊆ 结构判据 esc；propose 输出 gate 无差别")
    # ① claim 触发组：3 实现同分 0.4 ⇒ claim + 结构判据都触发
    o_claim = [{"task_id": "w1", "score": 0.4, "impl_sha": f"i{i}"} for i in range(3)]
    esc = G.escalations([], o_claim)
    m = SI.movability(o_claim, cell="w1")
    claim_cells = [c for c in ("w1",) if m["claim"] == "SCORE_UNMOVABLE"]
    ok_subset = set(claim_cells) <= set(esc)
    # ② 唯一窗口组（n_impls=2, n_obs=2）：claim 不触发（MIN_IMPLS=3），propose 给 PROBE
    o_win = [{"task_id": "w2", "score": 0.5, "impl_sha": "a"},
             {"task_id": "w2", "score": 0.5, "impl_sha": "b"}]
    m_win = SI.movability(o_win, cell="w2")
    kinds_win = [g.kind for g in G.propose(o_win, ["w2"])]
    ok_win = (m_win["claim"] is None and kinds_win == ["PROBE"])
    # ③ 反例组（⛔ 必须不在 claim 里）：3 实现有分差 ⇒ COARSE；已达标 3×1.0 ⇒ 无 claim
    o_coarse = [{"task_id": "w3", "score": s, "impl_sha": f"i{i}"}
                for i, s in enumerate([0.4, 0.6, 0.6])]
    o_sat = [{"task_id": "w4", "score": 1.0, "impl_sha": f"i{i}"} for i in range(3)]
    m3 = SI.movability(o_coarse, cell="w3")
    m4 = SI.movability(o_sat, cell="w4")
    ok_counter = (m3["claim"] is None and m4["claim"] is None)
    # ④ A/B：propose(escalated=esc) == propose(escalated=esc∪claim格) 逐字相同
    o_all = o_claim + [{"task_id": "w5", "score": 0.0, "impl_sha": "z"}]
    esc_all = G.escalations([], o_all)
    claim_all = {c for c in esc_all if (esc_all[c].get("movability") or {}).get("claim")}
    a = [g.as_dict() for g in G.propose(o_all, ["w1", "w5"], escalated=list(esc_all))]
    b = [g.as_dict() for g in G.propose(o_all, ["w1", "w5"],
                                        escalated=list(set(esc_all) | claim_all))]
    ok_ab = (a == b and claim_all == {"w1"} and "w1" in esc_all)
    check("P4a", "claim 触发的格 ⊆ 结构判据 esc（覆盖定理的包含关系）", ok_subset,
          f"claim={claim_cells} esc={sorted(esc)}")
    check("P4b", "唯一窗口（2 实现 2 观测）：claim 不触发，propose 给 PROBE 不是 RAISE",
          ok_win, f"claim={m_win['claim']} kinds={kinds_win}")
    check("P4c", "反例组：有分差（COARSE）与已达标（3×1.0）都无 claim", ok_counter,
          f"COARSE claim={m3['claim']} SAT claim={m4['claim']}")
    check("P4d", "A/B propose 逐字相同：claim 格进出 escalated 不改变提议（gate 无差别）",
          ok_ab, f"claim格={sorted(claim_all)} goals={[g['kind'] for g in a]}")
    return {"claim_cells": sorted(claim_all)}


# ===========================================================================
# P5 产品账本现状体检（部署态）
# ===========================================================================
def p5() -> dict:
    print("\n## P5 部署态：产品账本 36 行的可动性现状（身份缺口读数）")
    led = ROOT / "state" / "score_ledger.jsonl"
    rows = [json.loads(l) for l in led.read_text(encoding="utf-8").splitlines() if l.strip()]
    cells = sorted({r.get("task_id") for r in rows if r.get("task_id")})
    per = {}
    for c in cells:
        m = SI.movability(rows, cell=c)
        per[c] = {"label": m["label"], "reason": m["reason"],
                  "n_impls": m["n_impls"], "n_no_impl": m["n_no_impl"],
                  "claim": m["claim"]}
    m_all = SI.movability(rows, cell=None)
    n_claim = sum(1 for v in per.values() if v["claim"])
    n_unknown = sum(1 for v in per.values() if v["label"] == "UNKNOWN")
    ok = (len(rows) == 36 and n_claim == 0 and n_unknown == len(cells)
          and m_all["n_no_impl"] == len(rows))
    check("P5", "36 行真账本全缺身份 ⇒ 24 格全 UNKNOWN、claim 全 None、"
                "n_no_impl=36（现状读数：身份缺口，⛔ 不是缺陷声明）",
          ok, f"rows={len(rows)} cells={len(cells)} UNKNOWN={n_unknown} "
              f"claim={n_claim} n_no_impl(全局)={m_all['n_no_impl']}")
    return {"per_cell": per, "n_rows": len(rows), "n_no_impl_all": m_all["n_no_impl"]}


# ===========================================================================
# P6 Q7 反证：A_DEPLOY 事件流上的 G6 信息量跳过（V157 框架只读重放）
# ===========================================================================
# ⚠️ search / run_arm / per_key / apply / search_early 逐字取自
#   `bench/evidence/v6_s245/v157.py` 与 `probe_remain.py`（不改旧证据），
#   自校验（§0.4 对拍靶）证明复制无损后，G6 才许出结论。
for _d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198", "v6_s210", "v6_s236"):
    sys.path.insert(0, str(EV / _d))

import idiom_transfer as I     # noqa: E402
import search as S             # noqa: E402
import v119 as V               # noqa: E402
import v120 as W               # noqa: E402
import v121 as X               # noqa: E402

BASELINE = ("?", "<baseline>")
MULTS = (0.5, 1.0, 1.66, 2.0)
SEEDS = 200


def search_full(tid, h, code, Sx, chains, exprs, mine, rounds, k) -> dict:
    s0, _ = V.ev(tid, code)
    frontier = [dict(code=code, s=s0)]
    visited = {code}
    n_eval = 0
    events: list[tuple] = []
    for rnd in range(1, rounds + 1):
        pool, seen_src = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, src)
                     for lab, src in X.gen(tid, node["code"], Sx, chains, exprs)]
            for key, _lab, src in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                if src in visited or src in seen_src:
                    continue
                s, _b = V.ev(tid, src)
                n_eval += 1
                events.append((key, s, src, rnd))
                if s > node["s"]:
                    pool.append(dict(code=src, s=s))
                    seen_src.add(src)
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:k]
        visited |= {d["code"] for d in pool}
    best = max([s0] + [e[1] for e in events]) if events else s0
    return dict(s0=s0, s1=best, n_eval=n_eval, events=events)


def search_early(tid, code, Sx, chains, exprs, mine, rounds=6, k=2):
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, n_eval=0, hit=True, events=[], stop=0)
    frontier = [dict(code=code, s=s0)]
    visited = {code}
    n = 0
    ev = []
    for rnd in range(1, rounds + 1):
        pool, seen = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, src)
                     for lab, src in X.gen(tid, node["code"], Sx, chains, exprs)]
            for key, _l, src in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                if src in visited or src in seen:
                    continue
                s, _ = V.ev(tid, src)
                n += 1
                ev.append((key, s, src, s > node["s"]))
                if s >= 1.0:
                    return dict(s0=s0, n_eval=n, hit=True, events=ev, stop=rnd)
                if s > node["s"]:
                    pool.append(dict(code=src, s=s))
                    seen.add(src)
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:k]
        visited |= {d["code"] for d in pool}
    return dict(s0=s0, n_eval=n, hit=False, events=ev, stop=-1)


def _g6_skippable(key_hist: list[float], s: float, m: int) -> bool:
    """在线因果：只看**已判过**的分数。已判 ≥ m 个、逐位相同、且 < 1.0 ⇒ 跳过。"""
    return (len(key_hist) >= m and len({repr(x) for x in key_hist}) == 1
            and key_hist[0] < 1.0)


def p6() -> dict:
    t0 = time.time()
    print("\n## P6 Q7 反证：G6（过去-pool CONSTANT@<1.0 ⇒ 跳过该键后续）")
    corpus = I.harvest()
    impls = []
    for tid in V.TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                continue
            impls.append((tid, h))
    mem = json.loads(X.MEM_FILE.read_text(encoding="utf-8"))
    V.CACHE.clear()

    # ---- 全搜索臂（V157 run_arm 逐字） ----
    A: dict = {}
    for tid, h in impls:
        mine = {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        ch = V.harvest_chains(corpus, tid, h)
        ex = W.harvest_exprs(corpus, tid, h)
        A[(tid, h)] = search_full(tid, h, corpus[tid][h], Sx, ch, ex, mine, 6, 2)
    tot = sum(r["n_eval"] for r in A.values())
    n_ok = sum(1 for r in A.values() if r["s1"] >= 1.0)

    cells = {}
    for (tid, h), r in A.items():
        seen: dict = {}
        for key, s, _src, _r in r["events"]:
            seen.setdefault(key, []).append(s)
        for key, seq in seen.items():
            cells[(tid, h, key)] = dict(
                seq=seq, p=sum(1 for s in seq if s >= 1.0) / len(seq), n=len(seq),
                pos=next((i for i, s in enumerate(seq, 1) if s >= 1.0), None))
    price = {}
    for (tid, h, key), c in cells.items():
        tr = [((t2, h2, k2), c2) for (t2, h2, k2), c2 in cells.items()
              if t2 == tid and h2 != h]
        if not tr:
            price[(tid, h, key)] = None
            continue
        s0 = A[(tid, h)]["s0"]
        nn = min(tr, key=lambda x: (abs(A[(tid, x[0][1])]["s0"] - s0), x[0][1]))
        p = nn[1]["p"]
        price[(tid, h, key)] = (1.0 / p) if p > 0 else None

    oracle_skip = {k for k, c in cells.items() if c["pos"] is None}
    uni_all = set()
    for tid, h in impls:
        for kk in mem[tid]:
            if kk["h"] != h:
                uni_all.add((tid, h, tuple(kk["act"])))
    real = {c for c in uni_all if c[2] != BASELINE}

    def apply(impl, policy, m=1.0, scope=None, g6_m=2):
        ev = A[impl]["events"]
        if scope is not None:
            ev = [e for e in ev if (impl[0], impl[1], e[0]) in scope]
        used, best = 0, A[impl]["s0"]
        if policy == "BASE":
            for _k, s, _src, _r in ev:
                used += 1
                best = max(best, s)
        elif policy == "G1":
            for _k, s, _src, _r in ev:
                used += 1
                best = max(best, s)
                if s >= 1.0:
                    break
        elif policy == "G2":
            seen_hit = set()
            for k, s, _src, _r in ev:
                if k in seen_hit:
                    continue
                used += 1
                best = max(best, s)
                if s >= 1.0:
                    seen_hit.add(k)
        elif policy == "G3":
            cnt = collections.Counter()
            for k, s, _src, _r in ev:
                p = price.get((impl[0], impl[1], k))
                budget = 10 ** 9 if p is None else max(1, math.ceil(m * p))
                if cnt[k] >= budget:
                    continue
                cnt[k] += 1
                used += 1
                best = max(best, s)
        elif policy == "G4":
            for k, s, _src, _r in ev:
                if (impl[0], impl[1], k) in oracle_skip:
                    continue
                used += 1
                best = max(best, s)
        elif policy == "G5":
            for k, s, _src, _r in ev:
                if (impl[0], impl[1], k) in policy_skip:
                    continue
                used += 1
                best = max(best, s)
        elif policy == "G6":
            hist: dict = {}
            for k, s, _src, _r in ev:
                h = hist.setdefault(k, [])
                if _g6_skippable(h, s, g6_m):
                    continue
                used += 1
                best = max(best, s)
                h.append(s)
        return used, best

    def run(policy, m=1.0, scope=None, g6_m=2):
        used = ok = 0
        for impl in A:
            u, b = apply(impl, policy, m, scope, g6_m)
            used += u
            ok += b >= 1.0
        return used, ok

    # ---- 自校验（§0.4 对拍靶；⛔ 对不上 ⇒ 全部作废） ----
    print("  自校验（对拍 v6_s245/readings_v157.json）：")
    targets = {"BASE": (2710, 21), "G1": (609, 21), "G2": (2506, 21), "G4": (904, 21)}
    tgt_g3 = {0.5: (786, 17), 1.0: (1384, 21), 1.66: (2135, 21), 2.0: (2331, 21)}
    tgt_real = {"BASE": (110, 20), "G1": (60, 20), "G2": (85, 20), "G4": (101, 20)}
    tgt_g3_real = {0.5: (44, 15), 1.0: (70, 20), 1.66: (99, 20), 2.0: (107, 20)}
    self_ok = (len(cells) == 526 and tot == 2710 and n_ok == 21)
    details = [f"cells={len(cells)}(记 526) tot={tot}(记 2710) ok={n_ok}(记 21)"]
    tab_all: dict = {}
    tab_real: dict = {}

    def _measure(tab: dict, tag: str, pol: str, m=1.0, scope=None, g6_m=2):
        u, ok = run(pol, m, scope, g6_m)
        tab[tag] = {"used": u, "saved": (tot if scope is None else 110) - u, "ok": ok}
        return u, ok

    for tag, pol in (("BASE", "BASE"), ("G1", "G1"), ("G2", "G2"), ("G4", "G4")):
        u, ok = _measure(tab_all, tag, pol)
        if (u, ok) != targets[tag]:
            self_ok = False
            details.append(f"{tag}={u}/{ok} ≠ 记 {targets[tag][0]}/{targets[tag][1]}")
        u, ok = _measure(tab_real, tag, pol, scope=real)
        if (u, ok) != tgt_real[tag]:
            self_ok = False
            details.append(f"real {tag}={u}/{ok} ≠ 记 {tgt_real[tag][0]}/{tgt_real[tag][1]}")
    for mlt in MULTS:
        u, ok = _measure(tab_all, f"G3({mlt})", "G3", mlt)
        if (u, ok) != tgt_g3[mlt]:
            self_ok = False
            details.append(f"G3({mlt})={u}/{ok} ≠ 记 {tgt_g3[mlt][0]}/{tgt_g3[mlt][1]}")
        u, ok = _measure(tab_real, f"G3({mlt})", "G3", mlt, scope=real)
        if (u, ok) != tgt_g3_real[mlt]:
            self_ok = False
            details.append(f"real G3({mlt})={u}/{ok} ≠ 记 {tgt_g3_real[mlt][0]}/{tgt_g3_real[mlt][1]}")
    check("P6-0", "装置自校验：全搜索 + 35 记忆格两口径逐位对上 V157 存档读数"
                  "（对不上 ⇒ P6 全部作废）",
          self_ok, "｜".join(details))

    # ---- G6 策略表（同跑内对照，两个口径 + 部署口径） ----
    g6_tab = {}
    for gm in (2, 3):
        u, ok = run("G6", g6_m=gm)
        g6_tab[f"G6(m={gm})"] = {"used": u, "saved": tot - u, "ok": ok}
        u, ok = run("G6", scope=real, g6_m=gm)
        g6_tab[f"G6(m={gm})@real"] = {"used": u, "saved": 110 - u, "ok": ok}
    # 丢命中拆两档（⛔ 不许把「已达标后的再命中」计成损失）：
    #   丢首中 = 跳过时判前 best<1.0 且 s≥1.0（真损失的候选）
    #   丢再中 = 跳过时判前 best≥1.0 且 s≥1.0（该实现早已达标，不是损失）
    loss = {}
    for gm in (2, 3):
        lost_first = lost_re = skipped = lost_imp = 0
        for impl in A:
            hist: dict = {}
            best = A[impl]["s0"]
            for k, s, _src, _r in A[impl]["events"]:
                h = hist.setdefault(k, [])
                if _g6_skippable(h, s, gm):
                    skipped += 1
                    if s >= 1.0:
                        if best < 1.0:
                            lost_first += 1
                        else:
                            lost_re += 1
                    if s > best:
                        lost_imp += 1
                    continue
                best = max(best, s)
                h.append(s)
        loss[f"G6(m={gm})"] = {"skipped": skipped, "lost_first_hits": lost_first,
                               "lost_rehits": lost_re, "lost_improvements": lost_imp}
    # G5 随机对照（与 V157 同式：跳过与预言家同数量的键，200 种子）
    keys_all = sorted({(im[0], im[1], k) for im in A for k in {e[0] for e in A[im]["events"]}})
    global policy_skip
    g5_saves, g5_oks = [], []
    for seed in range(SEEDS):
        rnd = random.Random(seed)
        policy_skip = set(rnd.sample(keys_all, len(oracle_skip)))
        u, ok = run("G5")
        g5_saves.append(tot - u)
        g5_oks.append(ok)
    g5 = {"saves_mean": sum(g5_saves) / SEEDS, "ok_mean": sum(g5_oks) / SEEDS,
          "drop_seeds": sum(1 for x in g5_oks if x < 21), "n_seed": SEEDS}

    # ---- 部署臂（search_early 逐字 = probe_remain）与条件率 ----
    E: dict = {}
    for tid, h in impls:
        mine = {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        E[(tid, h)] = search_early(tid, corpus[tid][h], Sx,
                                   V.harvest_chains(corpus, tid, h),
                                   W.harvest_exprs(corpus, tid, h), mine)
    dep_tot = sum(r["n_eval"] for r in E.values())
    dep_hit = sum(1 for r in E.values() if r["hit"])
    waste = noimp = 0
    for im, r in E.items():
        if r["hit"]:
            hitkeys = {k for k, s, _, _ in r["events"] if s >= 1.0}
            waste += sum(1 for k, s, _, _ in r["events"] if k not in hitkeys)
        noimp += sum(1 for e in r["events"] if not e[3] and e[1] < 1.0)
    dep_self_ok = (dep_tot == 588 and dep_hit == 21 and waste == 520 and noimp == 559)
    check("P6-0b", "部署臂自校验：588/21/520/559 逐位对上 readings_remain 存档",
          dep_self_ok, f"tot={dep_tot} hit={dep_hit} waste={waste} noimp={noimp}")

    # 条件率：部署流里，G6(m) 会跳过的事件 vs 不会跳过的事件，命中率/改进率
    cond = {}
    dep_g6 = {}
    for gm in (2, 3):
        sk_hit = sk_n = sk_imp = ns_hit = ns_n = ns_imp = 0
        dep_used = dep_lost_first = dep_lost_re = 0
        for im, r in E.items():
            hist: dict = {}
            best = r["s0"]
            for k, s, _src, improved in r["events"]:
                h = hist.setdefault(k, [])
                if _g6_skippable(h, s, gm):
                    sk_n += 1
                    sk_hit += (s >= 1.0)
                    sk_imp += bool(improved)
                    if s >= 1.0:
                        if best < 1.0:
                            dep_lost_first += 1
                        else:
                            dep_lost_re += 1
                else:
                    ns_n += 1
                    ns_hit += (s >= 1.0)
                    ns_imp += bool(improved)
                    dep_used += 1
                    best = max(best, s)
                    h.append(s)
        cond[f"G6(m={gm})"] = {
            "skippable_n": sk_n,
            "hit_rate_skippable": (sk_hit / sk_n) if sk_n else None,
            "improve_rate_skippable": (sk_imp / sk_n) if sk_n else None,
            "other_n": ns_n,
            "hit_rate_other": (ns_hit / ns_n) if ns_n else None,
            "improve_rate_other": (ns_imp / ns_n) if ns_n else None,
        }
        # 部署口径（588 事件）上的 G6 策略行 —— Q7 的原口径
        dep_ok = 0
        for im, r in E.items():
            hist2: dict = {}
            best2 = r["s0"]
            for k, s, _src, improved in r["events"]:
                h = hist2.setdefault(k, [])
                if _g6_skippable(h, s, gm):
                    continue
                best2 = max(best2, s)
                h.append(s)
            dep_ok += best2 >= 1.0
        dep_g6[f"G6(m={gm})@deploy"] = {"used": dep_used, "saved": dep_tot - dep_used,
                                        "ok": dep_ok, "lost_first_hits": dep_lost_first,
                                        "lost_rehits": dep_lost_re}

    # 注册方向：P(命中|CONSTANT@<1.0) < P(命中|非)
    ok_dir = []
    for gm in (2, 3):
        c = cond[f"G6(m={gm})"]
        if c["hit_rate_skippable"] is not None and c["hit_rate_other"] is not None:
            ok_dir.append(c["hit_rate_skippable"] < c["hit_rate_other"])
    dir_txt = "｜".join(
        f"m={gm}: skippable 命中率 "
        f"{cond[f'G6(m={gm})']['hit_rate_skippable']} vs 其他 "
        f"{cond[f'G6(m={gm})']['hit_rate_other']}" for gm in (2, 3))
    # ⛔ 方向不成立也如实入档（负结果），但装置自校验必须过
    check("P6a", "条件率（注册方向：CONSTANT@<1.0 的后续命中率更低）"
                 "——⚠️ 本条如实报：成立=正结果、不成立=负结果入档",
          all(ok_dir) if ok_dir else False, dir_txt)

    tab_txt = "｜".join(f"{k} 省{v['saved']}/到{v['ok']}" for k, v in g6_tab.items())
    loss_txt = "｜".join(f"{k} 跳{v['skipped']} 丢首中{v['lost_first_hits']} "
                         f"丢再中{v['lost_rehits']} 丢改进{v['lost_improvements']}"
                         for k, v in loss.items())
    dep_txt = "｜".join(f"{k} 省{v['saved']}/到{v['ok']} 丢首中{v['lost_first_hits']}"
                        for k, v in dep_g6.items())
    check("P6b", "策略表（同跑内对照）：G6 的省分/到 1.0/丢首中/丢改进，"
                 "与 BASE/G3/G4/G5 同口径（丢再中 = 已达标后的再命中，⛔ 不是损失）",
          self_ok and dep_self_ok,
          f"{tab_txt}｜{dep_txt}｜{loss_txt}｜G5 随机: 省均值 {g5['saves_mean']:.1f} "
          f"掉实现种子 {g5['drop_seeds']}/{SEEDS}")

    return {"selfcheck": {"tab_all": tab_all, "tab_real": tab_real,
                          "cells": len(cells), "tot": tot,
                          "n_ok": n_ok, "ok": self_ok},
            "deploy_selfcheck": {"tot": dep_tot, "hit": dep_hit, "waste": waste,
                                 "noimp": noimp, "ok": dep_self_ok},
            "g6_tab": g6_tab, "g6_loss": loss, "g6_deploy": dep_g6, "g5": g5,
            "cond": cond, "wall_s": time.time() - t0}


# ===========================================================================
def main() -> None:
    t0 = time.time()
    print("V175 探针（PREREG_v175 §2）—— 零模型零网络")
    out = {"prereg": "bench/evidence/l3_s263/PREREG_v175.md",
           "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    out.update(p1())
    out.update(p2())
    out.update(p3())
    out.update(p4())
    out.update(p5())
    out.update(p6())
    n_fail = sum(1 for r in results if not r["ok"])
    out["verdicts"] = results
    out["wall_s"] = time.time() - t0
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n汇总：{len(results) - n_fail}/{len(results)} PASS"
          f"（⚠️ P6a 是方向性命题，判负=负结果入档，不改装置）")
    print(f"已落盘 {OUT}（{out['wall_s']:.1f}s）")


if __name__ == "__main__":
    main()
