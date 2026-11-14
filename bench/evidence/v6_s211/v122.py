#!/usr/bin/env python3
"""V122：加一道硬预算闸 —— 把「省次数」变成「做得成」。

两臂（COLD / WARM）+ 一个剂量扫描（B ∈ {60,120,200,300,400,600}）。
唯一新增变量 = 硬预算 B：n_eval 到 B 立即停，返回「见过的最高分」。

零模型、零网络、零子进程。判据 = 现有 test 同一批。
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
sys.path.insert(0, str(EV / "v6_s205"))
sys.path.insert(0, str(EV / "v6_s208"))
sys.path.insert(0, str(EV / "v6_s209"))
sys.path.insert(0, str(EV / "v6_s210"))
sys.path.insert(0, str(EV / "v6_s198"))
import search as S            # noqa: E402
import idiom_transfer as I     # noqa: E402
import v119 as V              # noqa: E402
import v120 as W              # noqa: E402
import v121 as M              # noqa: E402

TIDS = V.TIDS
MAX_ROUNDS = 6
MEM = EV / "v6_s210" / "memory.json"
BUDGETS = [60, 120, 200, 300, 400, 600]
ev = V.ev


def search(tid, code, Sx, chains, exprs, order_key, budget) -> dict:
    s0, _ = ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, s1=1.0, n_eval=0, hit=True, exhausted=False)
    frontier = [dict(code=code, s=s0)]
    visited = {code}
    n_eval, best = 0, s0
    for _ in range(MAX_ROUNDS):
        pool, seen_src = [], set()
        for node in frontier:
            cands = sorted(M.gen(tid, node["code"], Sx, chains, exprs), key=order_key)
            for lab, src in cands:
                if src in visited or src in seen_src:
                    continue
                s, _ = ev(tid, src)
                n_eval += 1
                best = max(best, s)
                if s >= 1.0:
                    return dict(s0=s0, s1=1.0, n_eval=n_eval, hit=True, exhausted=False)
                if n_eval >= budget:
                    return dict(s0=s0, s1=best, n_eval=n_eval, hit=False, exhausted=True)
                if s > node["s"]:
                    pool.append(dict(code=src, s=s))
                    seen_src.add(src)
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        kept = pool[:2]
        visited |= {d["code"] for d in pool}
        frontier = kept
    return dict(s0=s0, s1=best, n_eval=n_eval, hit=best >= 1.0, exhausted=False)


def main() -> None:
    corpus = I.harvest()
    mem = json.loads(MEM.read_text(encoding="utf-8"))
    impls, skipped = [], []
    for tid in TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                skipped.append((tid, h))
                continue
            impls.append((tid, h))

    print(f"## §0 装置（分母 n_impl_parseable = {len(impls)}，记忆 = `{MEM}`，n_rec = "
          f"{sum(len(v) for v in mem.values())}）")
    rows = []
    for tid, h in impls:
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        chains = V.harvest_chains(corpus, tid, h)
        exprs = W.harvest_exprs(corpus, tid, h)
        mine = {(a, b) for k in mem[tid] if k["h"] != h for a, b in [tuple(k["act"])]}
        for B in BUDGETS:
            V.CACHE.clear()
            c = search(tid, corpus[tid][h], Sx, chains, exprs, lambda x: 0, B)
            V.CACHE.clear()
            w = search(tid, corpus[tid][h], Sx, chains, exprs,
                       lambda x, mine=mine: (0 if M.abstract(x[0]) in mine else 1), B)
            rows.append(dict(tid=tid, h=h, B=B, cold=c, warm=w, n_mem=len(mine)))

    print("\n## §1 主读数（B = 120）")
    R = [r for r in rows if r["B"] == 120]
    c1 = [r["h"] for r in R if r["cold"]["s1"] >= 1.0]
    w1 = [r["h"] for r in R if r["warm"]["s1"] >= 1.0]
    n = len(R)
    print(f"  COLD 到 1.0 = **{len(c1)}/{n}**｜WARM 到 1.0 = **{len(w1)}/{n}**")
    print(f"  n_impl_base_full = {sum(1 for r in R if r['cold']['s0'] >= 1.0)}"
          f"｜n_impl_needing_work = {sum(1 for r in R if r['cold']['s0'] < 1.0)}")
    print("\n  仍在预算内到不了 1.0 的：")
    for r in R:
        if r["warm"]["s1"] < 1.0:
            print(f"    {r['tid'][:6]} {r['h']} 冷 {r['cold']['s1']:.1f}（{r['cold']['n_eval']} 次）"
                  f"｜热 {r['warm']['s1']:.1f}（{r['warm']['n_eval']} 次）")

    print("\n## §2 判决")
    w_ok1 = len(c1) == 17
    print(f"  **W1 COLD 在 B=120 下 = 17/25** ⇒ 实得 {len(c1)}/25 ⇒ "
          f"{'✅ 押中' if w_ok1 else '⛔ 押破'}")
    w_ok2 = len(w1) == 20
    print(f"  **W2 WARM 在 B=120 下 = 20/25** ⇒ 实得 {len(w1)}/25 ⇒ "
          f"{'✅ 押中' if w_ok2 else '⛔ 押破'}")

    flip = sorted(set(w1) - set(c1))
    want = {"1aa5099d", "37a4f17f", "f2d1efa6"}
    w_ok3 = set(flip) == want
    print(f"  **W3 翻盘的恰好 = {sorted(want)}，a579b07a 不翻** ⇒ 实得 {flip} ⇒ "
          f"{'✅ 押中' if w_ok3 else '⛔ 押破'}")

    w_ok4 = set(c1) <= set(w1)
    print(f"  **W4 WARM ⊇ COLD（无实现因记忆掉出预算）** ⇒ "
          f"{'✅ 押中' if w_ok4 else '⛔ 押破'}（掉出 {sorted(set(c1) - set(w1))}）")

    print("\n## §3 剂量扫描（B 从 60 到 600）")
    print("  B   COLD  WARM  差")
    diffs = {}
    for B in BUDGETS:
        Rb = [r for r in rows if r["B"] == B]
        a = sum(1 for r in Rb if r["cold"]["s1"] >= 1.0)
        b = sum(1 for r in Rb if r["warm"]["s1"] >= 1.0)
        diffs[B] = b - a
        print(f"  {B:3d}  {a:3d}   {b:3d}   {b - a:+d}")
    w_ok5 = diffs[max(BUDGETS)] == 0
    print(f"  **W5 B≥400 时差值收敛到 0** ⇒ B={max(BUDGETS)} 差值 {diffs[max(BUDGETS)]:+d} ⇒ "
          f"{'✅ 押中' if w_ok5 else '⛔ 押破'}")

    (HERE / "search.json").write_text(json.dumps(
        dict(rows=rows, skipped=skipped, diffs=diffs), ensure_ascii=False, indent=1),
        encoding="utf-8")
    print("\n  原始读数已落盘 `search.json`")


if __name__ == "__main__":
    main()
