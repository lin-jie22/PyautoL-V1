#!/usr/bin/env python3
"""V117：唯一变量 = 同分候选保不保留（beam 宽度 k）。

六条臂 = k ∈ {1,2,3} × 信息 ∈ {OFF, ON}。
候选生成器 / 模板集 / 判据全部沿用 V116（`v6_s205/search.py`），一字不改。
分数由 `bitmap()` 内联复算（前置：V116 的 743 个候选上，与子进程判分器错位 0，见 `probe.txt` §0）。

§0 六条臂的分数向量
§1 判决 W1~W6
§2 覆盖面（三分母分列）
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
sys.path.insert(0, str(EV / "v6_s205"))
sys.path.insert(0, str(EV / "v6_s198"))
import search as S           # noqa: E402
import idiom_transfer as I   # noqa: E402

TIDS = ["g7e1_parse_flag", "g7d2_dedupe_keep"]
KS = [1, 2, 3]
MAX_ROUNDS = 6
SCACHE: dict[tuple[str, str], tuple[float, tuple]] = {}


def evaluate(tid: str, code: str) -> tuple[float, tuple]:
    """⇒ (score, bitmap)。bitmap 一次执行两用：算分 + 认盆。"""
    key = (tid, code)
    if key in SCACHE:
        return SCACHE[key]
    bm = tuple(S.bitmap(tid, code))
    sc = sum(1 for x in bm if x is True) / len(bm)
    SCACHE[key] = (sc, bm)
    return sc, bm


def beam(tid: str, code: str, Sx: set[str], k: int) -> dict:
    """PREREG §1.1 的规则，逐字实现。"""
    s0, _ = evaluate(tid, code)
    frontier = [dict(code=code, s=s0, lab="<baseline>")]
    visited = {code}
    trail = []
    exhausted = False
    for _ in range(MAX_ROUNDS):
        pool, seen_src = [], set()
        for node in frontier:                                  # 按 frontier 顺序
            for lab, src in S.candidates(node["code"], Sx):    # 按生成顺序（与 V116 同一顺序）
                if src in visited or src in seen_src:
                    continue
                s, _ = evaluate(tid, src)
                if s > node["s"]:                              # ★ 只收严格改进
                    pool.append(dict(code=src, s=s, lab=lab, parent=node["lab"]))
                    seen_src.add(src)
        if not pool:
            exhausted = True
            trail.append(dict(n_pool=0, kept=[], s=[n["s"] for n in frontier]))
            break
        pool.sort(key=lambda d: -d["s"])                       # 稳定排序 ⇒ 同分保持入池顺序
        kept = pool[:k]
        visited |= {d["code"] for d in pool}
        trail.append(dict(n_pool=len(pool), kept=[d["lab"] for d in kept],
                          s=[d["s"] for d in kept]))
        frontier = kept
        if max(d["s"] for d in frontier) >= 1.0:
            break
    best = max(frontier, key=lambda d: d["s"])
    return dict(s0=s0, s1=best["s"], code=best["code"], lab=best["lab"],
                n_frontier=len(frontier), trail=trail, exhausted=exhausted,
                bitmap=list(evaluate(tid, best["code"])[1]))


def arm_name(tid: str, k: int, see: bool) -> str:
    return f"{tid}::{'ON' if see else 'OFF'}::k{k}"


def main() -> None:
    corpus = I.harvest()
    v116 = json.loads((EV / "v6_s205" / "search.json").read_text(encoding="utf-8"))["arms"]
    res: dict[str, list] = {}

    for tid in TIDS:
        for see in (False, True):
            for k in KS:
                rows = []
                for h in sorted(corpus[tid]):
                    Sx, _ = S.wordset(tid, corpus[tid][h], see)
                    r = beam(tid, corpus[tid][h], Sx, k)
                    r["h"] = h
                    rows.append(r)
                res[arm_name(tid, k, see)] = rows

    # ---------------------------------------------------------------- §0
    print("## §0 六条臂的分数向量（分母写进字段名）")
    for tid in TIDS:
        n_impl = len(corpus[tid])
        print(f"\n  **{tid}**（n_impl = {n_impl}）")
        for see in (False, True):
            for k in KS:
                vec = [r["s1"] for r in res[arm_name(tid, k, see)]]
                n_full = sum(1 for v in vec if v >= 1.0)
                star = "　← 与 k=2 逐字节相同" if k == 3 and vec == [r["s1"] for r in res[arm_name(tid, 2, see)]] else ""
                print(f"    {'ON ' if see else 'OFF'} k={k}: {vec}　到 1.0 的 {n_full}/{n_impl}{star}")

    # ---------------------------------------------------------------- §1
    print("\n## §1 判决")

    # W1：k=1 逐项复现 V116
    ok1, bad1, n_cmp = True, [], 0
    for tid in TIDS:
        for see in (False, True):
            arm = f"{tid}::{'ON' if see else 'OFF'}"
            a = {r["h"]: r for r in res[arm_name(tid, 1, see)]}
            for r in v116[arm]:
                n_cmp += 1
                m = a[r["h"]]
                if not (abs(m["s0"] - r["s0"]) < 1e-9 and abs(m["s1"] - r["s1"]) < 1e-9
                        and m["exhausted"] == r["exhausted"]):
                    ok1 = False
                    bad1.append((arm, r["h"], r["s0"], m["s0"], r["s1"], m["s1"],
                                 r["exhausted"], m["exhausted"]))
    print(f"\n  **W1 `k=1` 逐项复现 V116（n_impl_total = {n_cmp}）**"
          f"⇒ {'✅ 押中' if ok1 else '⛔ 押破'}")
    if bad1:
        for b in bad1:
            print(f"    ⛔ {b}")
    else:
        print(f"    {n_cmp}/{n_cmp} 个实现的 (s0, s1, exhausted) 全部相同")

    # W2/W3
    T1 = "g7e1_parse_flag"
    n_e1 = len(corpus[T1])
    f = lambda k, see: sum(1 for r in res[arm_name(T1, k, see)] if r["s1"] >= 1.0)  # noqa: E731
    print(f"\n  **W2 `ON × k=2` 在 g7e1 上 {n_e1}/{n_e1}；W3 `OFF × k=2` 仍是 {f(1, False)}/{n_e1}**")
    print(f"    OFF k=1 = {f(1, False)}/{n_e1}　OFF k=2 = {f(2, False)}/{n_e1}　"
          f"ON k=1 = {f(1, True)}/{n_e1}　ON k=2 = {f(2, True)}/{n_e1}　ON k=3 = {f(3, True)}/{n_e1}")
    print(f"    W2 ⇒ {'✅ 押中' if f(2, True) == n_e1 else '⛔ 押破'}（预言 {n_e1}/{n_e1}）")
    print(f"    W3 ⇒ {'✅ 押中' if f(2, False) == f(1, False) else '⛔ 押破'}"
          f"（预言与 OFF k=1 相同 = {f(1, False)}）")
    print(f"    ⭐ 开关效应（ON − OFF 的到 1.0 数）：k=1 时 **{f(1, True) - f(1, False)}**"
          f"　k=2 时 **{f(2, True) - f(2, False)}**　k=3 时 **{f(3, True) - f(3, False)}**")

    # W4：g7d2 六臂全同
    T2 = "g7d2_dedupe_keep"
    vecs = {f"k{k}{'ON' if s else 'OFF'}": [r["s1"] for r in res[arm_name(T2, k, s)]]
            for k in KS for s in (False, True)}
    base_vec = vecs["k1OFF"]
    w4 = all(v == base_vec for v in vecs.values())
    print(f"\n  **W4 g7d2 六条臂逐字节相同 = {base_vec}** ⇒ {'✅ 押中' if w4 else '⛔ 押破'}")
    if not w4:
        for name, v in vecs.items():
            print(f"    {name}: {v}")

    # W5：ON k=3 与 ON k=2 逐字节相同
    v2 = [r["s1"] for r in res[arm_name(T1, 2, True)]]
    v3 = [r["s1"] for r in res[arm_name(T1, 3, True)]]
    print(f"\n  **W5 `ON × k=3` 与 `ON × k=2` 在 g7e1 上逐字节相同**"
          f"⇒ {'✅ 押中' if v2 == v3 else '⛔ 押破'}")
    print(f"    k=2: {v2}")
    print(f"    k=3: {v3}")
    print("    ⇒ k 的作用是**覆盖同分集合**，不是「越大越好」")

    # W6：新修好的恰好是 V116 卡在 0.8 的那 4 个，且只在 ON 臂
    stuck_116 = {r["h"] for r in v116[f"{T1}::OFF"] if abs(r["s1"] - 0.8) < 1e-9}
    off2 = {r["h"] for r in res[arm_name(T1, 2, False)] if r["s1"] >= 1.0}
    on2 = {r["h"] for r in res[arm_name(T1, 2, True)] if r["s1"] >= 1.0}
    off1 = {r["h"] for r in res[arm_name(T1, 1, False)] if r["s1"] >= 1.0}
    newly = on2 - off1
    w6 = newly == stuck_116 and (off2 - off1) == set()
    print(f"\n  **W6 新修好的实现恰好 = V116 卡在 0.8 的那 4 个，且只在 ON 臂**"
          f"⇒ {'✅ 押中' if w6 else '⛔ 押破'}")
    print(f"    V116 卡在 0.8 的（n_stuck = {len(stuck_116)}）：{sorted(stuck_116)}")
    print(f"    ON × k=2 新到 1.0 的（n_newly = {len(newly)}）：{sorted(newly)}")
    print(f"    OFF × k=2 新到 1.0 的（n_newly_off = {len(off2 - off1)}）：{sorted(off2 - off1)}")
    print(f"    集合相等：{newly == stuck_116}　ON 独有：{(off2 - off1) == set()}")

    # ---------------------------------------------------------------- §2
    n_all = sum(len(corpus[t]) for t in TIDS)
    n_base_full = sum(1 for t in TIDS for r in res[arm_name(t, 1, True)] if r["s0"] >= 1.0)
    n_work = n_all - n_base_full
    n_fix_on = sum(1 for t in TIDS for r in res[arm_name(t, 2, True)]
                   if r["s0"] < 1.0 and r["s1"] >= 1.0)
    n_fix_off = sum(1 for t in TIDS for r in res[arm_name(t, 2, False)]
                    if r["s0"] < 1.0 and r["s1"] >= 1.0)
    print(f"\n## §2 覆盖面（⛔ 三块分开，不许混成一个比率）")
    print(f"  n_impl_total = {n_all}｜n_impl_base_full = {n_base_full}（基线已 1.0，搜索无事可做）"
          f"｜n_impl_needing_work = {n_work}")
    print(f"  被搜索修到 1.0（s0<1.0 且 s1=1.0）：")
    print(f"    ON  × k=1（= V116）：{sum(1 for t in TIDS for r in res[arm_name(t, 1, True)] if r['s0'] < 1.0 and r['s1'] >= 1.0)}/{n_work}")
    print(f"    ON  × k=2（本轮）  ：**{n_fix_on}/{n_work}**")
    print(f"    OFF × k=2（对照）  ：**{n_fix_off}/{n_work}**")
    print(f"  仍不到 1.0 的："
          f"{sorted((t, r['h'], r['s1']) for t in TIDS for r in res[arm_name(t, 2, True)] if r['s1'] < 1.0)}")
    print(f"  ⛔ 旧版这里写「合计 11/13」是**分母混用**（把基线已满分的 {n_base_full} 个算成「修好的」），已改。")
    print(f"  ⛔ 与 V115 的 7/26 = 26.9% 不可比（那是「人工施加」的覆盖面，这是「搜索」的）。")

    # ---------------------------------------------------------------- §1b
    print("\n## §1b 机制：4 个新修好的实现到底走了哪几步（ON × k=2）")
    for r in res[arm_name(T1, 2, True)]:
        if r["s0"] < 1.0 and r["s1"] >= 1.0:
            print(f"\n  **{r['h']}**（{r['s0']} → {r['s1']}）")
            for i, step in enumerate(r["trail"], 1):
                print(f"    第 {i} 轮：池 {step['n_pool']} 个，保留 {step['kept']}")
            print(f"    终点位图 {r['bitmap']}")
            print(f"    终点代码：{r['code'].strip().splitlines()[-1]!r} …")

    out = dict(arms=res, verdicts=dict(
        W1=ok1, W2=(f(2, True) == n_e1), W3=(f(2, False) == f(1, False)),
        W4=w4, W5=(v2 == v3), W6=w6),
        counts=dict(n_impl_e1=n_e1, n_impl_d2=len(corpus[T2]), n_impl_total=n_all,
                    on_k1=f(1, True), on_k2=f(2, True), on_k3=f(3, True),
                    off_k1=f(1, False), off_k2=f(2, False),
                    newly_on=sorted(newly), newly_off=sorted(off2 - off1),
                    stuck_116=sorted(stuck_116)))
    (HERE / "beam.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str),
                                    encoding="utf-8")
    print("\n  写出 beam.json")


if __name__ == "__main__":
    main()
