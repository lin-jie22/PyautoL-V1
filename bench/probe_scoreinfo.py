#!/usr/bin/env python3
"""V174 主实验 —— 分数信息量：CONSTANT / INJECTIVE / COARSE / UNKNOWN。

预注册：`bench/evidence/l3_s262/PREREG_v174.md`（判据 P1~P7，⛔ 不引用本文件之外的定义）。
装置：`mw/scoreinfo.py`（被测，纯函数）+ 本文件（判据）。零模型、零网络、零子进程写库。

⚠️ 装置自报（纪律 65）：
  - 被测 = **本文件顶部 import 的** `mw.scoreinfo`（工作区当前版）。
  - P5 的变异体在同一进程内替换 `classify` 后真跑判据，跑完还原（sha 不适用，
    改为记录变异体的名字与恢复断言）。
  - P6 真池 = `bench/evidence/v6_s208/search.json`（只读；`s0` 逐实现一致是 STAGE0 已核）。
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import mw.scoreinfo as SI  # noqa: E402

EV = HERE / "evidence"
POOL208 = EV / "v6_s208" / "search.json"
OUT = EV / "l3_s262" / "readings_scoreinfo.json"

VERDICTS: list[dict] = []
READ: dict = {}


def say(s=""):
    print(s, flush=True)


def check(tag: str, claim: str, ok: bool, detail: str = ""):
    VERDICTS.append({"tag": tag, "claim": claim, "ok": bool(ok), "detail": detail})
    mark = "PASS" if ok else "FAIL"
    say(f"  [{mark}] {tag} {claim}")
    if detail:
        say(f"         {detail}")


# ===========================================================================
# 判据用的池构造（形态抄 PREREG §0.1/§0.3，⛔ 不从实现反推）
# ===========================================================================
def pool_constant():
    return [("i1", 1.0), ("i2", 1.0)]


def pool_injective():
    return [("i1", 1.0), ("i2", 0.6), ("i3", 0.2)]


def pool_coarse():
    # §169.5 g7d3 形态：7 实现 / 4 分数，0.0 挤 3 个
    # ⚠️ 装置自踩（首跑 P3 判负当场抓住）：第一版把 §0.3 真池的 8 实现/5 分布混了进来
    #    （0.0/0.2/0.4/0.6/1.0 ⇒ k=5），与注册形态「7 实现 **4** 分数」不符。
    #    模块报 k=5 是对的 —— 错的是池。⛔ 判据原文一字未改，只把池改回注册形态。
    return [("a", 0.0), ("b", 0.0), ("c", 0.0), ("d", 0.2),
            ("e", 0.6), ("f", 0.6), ("g", 1.0)]


def pool_few():
    return [("only", 1.0)]


def pool_ruler():
    return [("i1", 1.0), ("i2", 1.2)]


def pool_conflict():
    return [("i1", 1.0), ("i1", 0.6), ("i2", 0.2)]


def pool_nan():
    return [("i1", float("nan")), ("i2", 0.2)]


def pool_bool():
    # bool 是 int 的子类 —— 不挡的话 True 会被记成 1.0（V173 变异有界自检 ⑨ 的同款坑）
    return [("i1", True), ("i2", 0.2)]


# ===========================================================================
# P1~P3 / P7 的可复用判据（P5 变异体要能对它们做撤回）
# ===========================================================================
def case_p1(cls):
    r = cls(pool_constant())
    ok = (r["kind"] == SI.KIND_CONSTANT and r["bits"] == 0.0
          and r["n_scores"] == 1 and r["reason"] is None)
    return ok, f"kind={r['kind']} bits={r['bits']} k={r['n_scores']}"


def case_p2(cls):
    r = cls(pool_injective())
    ok = (r["kind"] == SI.KIND_INJECTIVE and r["resolution"] == 1.0
          and abs(r["bits"] - math.log2(3)) < 1e-12 and r["n_impls"] == 3)
    return ok, (f"kind={r['kind']} resolution={r['resolution']} "
                f"bits={r['bits']:.6f} (log2(3)={math.log2(3):.6f})")


def case_p3(cls):
    r = cls(pool_coarse())
    bucket0 = r["buckets"].get("0") or []
    ok = (r["kind"] == SI.KIND_COARSE and abs(r["resolution"] - 4 / 7) < 1e-12
          and sorted(bucket0) == ["a", "b", "c"] and r["n_impls"] == 7
          and r["n_scores"] == 4)
    return ok, (f"kind={r['kind']} resolution={r['resolution']:.6f}(4/7={4/7:.6f}) "
                f"0.0桶={bucket0} k={r['n_scores']}")


def case_p7(cls):
    """值域与自洽：所有臂的输出一起查。"""
    pools = [pool_constant(), pool_injective(), pool_coarse()]
    outs = [cls(p) for p in pools]
    names = {SI.KIND_CONSTANT, SI.KIND_INJECTIVE, SI.KIND_COARSE, SI.KIND_UNKNOWN}
    ok = True
    why = []
    for r in outs:
        if r["kind"] not in names:
            ok = False
            why.append(f"值域外 {r['kind']}")
        if r["kind"] == SI.KIND_CONSTANT and r["bits"] != 0.0:
            ok = False
            why.append("CONSTANT bits≠0")
        if r["kind"] == SI.KIND_INJECTIVE and abs(r["bits"] - math.log2(r["n_impls"])) > 1e-12:
            ok = False
            why.append("INJECTIVE bits≠log2(n)")
        if r["kind"] == SI.KIND_COARSE:
            if not (0.0 < r["bits"] <= r["bits_max"] + 1e-12):
                ok = False
                why.append("COARSE bits 越界")
            if not (0.0 < r["resolution"] < 1.0):
                ok = False
                why.append("COARSE resolution 越界")
        if r["bits"] is not None and r["bits_max"] is not None and r["bits"] > r["bits_max"] + 1e-12:
            ok = False
            why.append("bits > bits_max")
    return ok, "｜".join(why) if why else "3 臂全自洽（值域/bits/resolution）"


def main() -> int:
    say("=" * 74)
    say("V174 分数信息量 —— CONSTANT / INJECTIVE / COARSE / UNKNOWN")
    say("=" * 74)

    # ---------- P1 / P2 / P3 ----------
    say("\nP1 常数臂 / P2 单射臂 / P3 粗臂")
    ok, d = case_p1(SI.classify)
    check("P1", "全等池 ⇒ CONSTANT，bits == 0.0", ok, d)
    ok, d = case_p2(SI.classify)
    check("P2", "3 实现 3 分数 ⇒ INJECTIVE，resolution == 1.0，bits == log2(3)", ok, d)
    ok, d = case_p3(SI.classify)
    check("P3", "7 实现 4 分数、0.0 挤 3 个 ⇒ COARSE，resolution == 4/7，逐桶点名", ok, d)

    # ---------- P4 弃权三条 ----------
    say("\nP4 弃权三条（fail-open）")
    r_few = SI.classify(pool_few())
    r_rul = SI.classify(pool_ruler())
    r_con = SI.classify(pool_conflict())
    r_nan = SI.classify(pool_nan())
    r_bool = SI.classify(pool_bool())
    reasons = [r_few["reason"], r_rul["reason"], r_con["reason"]]
    ok4 = (r_few["kind"] == r_rul["kind"] == r_con["kind"] == SI.KIND_UNKNOWN
           and r_few["reason"] == SI.REASON_TOO_FEW
           and r_rul["reason"] == SI.REASON_RULER_BROKEN
           and r_con["reason"] == SI.REASON_IMPL_SCORE_CONFLICT
           and r_nan["kind"] == SI.KIND_UNKNOWN and r_nan["reason"] == SI.REASON_RULER_BROKEN
           and r_bool["kind"] == SI.KIND_UNKNOWN and r_bool["reason"] == SI.REASON_RULER_BROKEN
           and all(r["bits"] is None and r["buckets"] == {} for r in
                   (r_few, r_rul, r_con, r_nan, r_bool)))
    check("P4", "n=1 ⇒ TOO_FEW｜1.2 ⇒ RULER_BROKEN｜同 impl 两分 ⇒ IMPL_SCORE_CONFLICT；\n"
                "              NaN/bool 也拦（bool 是 int 子类，不挡会被记成 1.0）；弃权时不给半个读数",
          ok4,
          f"few={r_few['reason']} ruler={r_rul['reason']} conflict={r_con['reason']} "
          f"nan={r_nan['reason']} bool={r_bool['reason']}｜弃权 bits 全 None="
          f"{all(r['bits'] is None for r in (r_few, r_rul, r_con, r_nan, r_bool))}")
    READ["p4"] = {"few": r_few, "ruler": r_rul, "conflict": r_con,
                  "nan": r_nan, "bool": r_bool}

    # ---------- P5 变异体（撤回态） ----------
    say("\nP5 变异体有牙（撤回态）")
    real = SI.classify

    def _mut_const(pool, **kw):
        r = real(pool, **kw)
        r["kind"] = SI.KIND_CONSTANT
        r["bits"] = 0.0
        return r

    def _mut_inj(pool, **kw):
        r = real(pool, **kw)
        r["kind"] = SI.KIND_INJECTIVE
        return r

    def _mut_no_ruler_gate(pool, **kw):
        rows = [(i, 1.0 if (isinstance(s, float) and s > 1.0) else s) for i, s in pool]
        return real(rows, **kw)

    try:
        SI.classify = _mut_const
        p2m, d2m = case_p2(SI.classify)
        p3m, d3m = case_p3(SI.classify)
        SI.classify = _mut_inj
        p1m, d1m = case_p1(SI.classify)
        SI.classify = _mut_no_ruler_gate
        r_gate = SI.classify(pool_ruler())
    finally:
        SI.classify = real
    ok5 = (not p2m) and (not p3m) and (not p1m) \
        and r_gate["kind"] != SI.KIND_UNKNOWN
    check("P5", "恒 CONSTANT ⇒ P2/P3 转红；恒 INJECTIVE ⇒ P1 转红；\n"
                "              拆 RULER_BROKEN 闸（越界分钳到 1.0）⇒ 1.2 臂不再弃权",
          ok5,
          f"恒CONSTANT下 P2={p2m}(期望 False) P3={p3m}(期望 False)｜"
          f"恒INJECTIVE下 P1={p1m}(期望 False)｜拆闸后 1.2 臂 kind={r_gate['kind']}"
          f"（期望≠UNKNOWN）｜已还原={SI.classify is real}")
    READ["p5"] = {"const_mut_p2": p2m, "const_mut_p3": p3m, "inj_mut_p1": p1m,
                  "no_gate_ruler": r_gate}

    # ---------- P6 部署态（真池） ----------
    say("\nP6 部署态（v6_s208 真池 25 实现，STAGE0 手数对拍）")
    d208 = json.loads(POOL208.read_text(encoding="utf-8"))
    per: dict[str, dict[str, float]] = {}
    for key, v in d208["res"].items():
        per.setdefault(v["tid"], {})[v["h"]] = float(v["s0"])
    truth = {
        "g7d2_dedupe_keep": (5, 3, {0.2, 0.6, 1.0}),
        "g7d3_fmt_amount": (8, 5, {0.0, 0.2, 0.4, 0.6, 1.0}),
        "g7e1_parse_flag": (8, 3, {0.4, 0.6, 1.0}),
        "g7e2_truncate_tail": (4, 2, {0.2, 1.0}),
    }
    ok6 = True
    lines = []
    outs6 = {}
    for tid in sorted(truth):
        pool = sorted(per[tid].items())
        r = SI.classify(pool)
        r2 = SI.classify(pool)
        n, k, dist = truth[tid]
        got_dist = {float(s) for s in r["buckets"]}
        same_twice = json.dumps(r, sort_keys=True) == json.dumps(r2, sort_keys=True)
        good = (r["kind"] == SI.KIND_COARSE and r["n_impls"] == n and r["n_scores"] == k
                and got_dist == dist and same_twice)
        ok6 = ok6 and good
        outs6[tid] = r
        lines.append(f"{tid} {r['kind']} {r['n_impls']}/{r['n_scores']} "
                     f"dist={sorted(got_dist)} 双跑相同={same_twice}")
    check("P6", "真池 4 格分类 == STAGE0 手数（4×COARSE，桶分布逐一对上）；\n"
                "              同池两次调用输出逐字相同（纯函数确定性）",
          ok6, "｜".join(lines))
    READ["p6"] = outs6

    # ---------- P7 值域与自洽 ----------
    say("\nP7 值域与自洽")
    ok, d = case_p7(SI.classify)
    check("P7", "kind 值域恰 4 名字；CONSTANT⇒bits=0｜INJECTIVE⇒bits=log2(n)｜\n"
                "              COARSE⇒0<bits≤bits_max；resolution∈(0,1]；bits≤bits_max 恒成立",
          ok, d)

    OUT.write_text(json.dumps({"verdicts": VERDICTS, "readings": READ},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    say(f"\n读数已落盘 {OUT.relative_to(ROOT)}")
    n_ok = sum(1 for v in VERDICTS if v["ok"])
    say(f"判据 {n_ok}/{len(VERDICTS)}")
    return 0 if n_ok == len(VERDICTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
