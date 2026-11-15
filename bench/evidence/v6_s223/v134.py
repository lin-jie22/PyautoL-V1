#!/usr/bin/env python3
"""V134：自省第二格 —— 系统自己发现「这条键可以匹配、但从没赢过」。

记录版搜索器 `search_rec` 在判分循环里记 n_tried / n_win（只在命中别人记忆时记）。
⛔ 记录版必须与 v121.search 逐实现 (s1, n_eval) 相同，否则整轮作废（PREREG §1）。
零模型、零网络、零子进程。
"""
from __future__ import annotations

import ast
import json
import pathlib
import statistics
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for d in ("v6_s205", "v6_s208", "v6_s209", "v6_s210", "v6_s211", "v6_s198", "v6_s217"):
    sys.path.insert(0, str(EV / d))
import search as S            # noqa: E402
import idiom_transfer as I     # noqa: E402
import v119 as V              # noqa: E402
import v120 as W              # noqa: E402
import v121 as M              # noqa: E402

TIDS = V.TIDS
ON_KEY = ("T2", "'on'")
TRUE_KEY = ("T2", "'true'")


def search_rec(tid, code, Sx, chains, exprs, order_key, mine, TRY, WIN):
    """v121.search 的逐行复制 + 两行记录。⛔ 不许改任何控制流。"""
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, s1=s0, n_eval=0, lab="<baseline>", hit=True)
    frontier = [dict(code=code, s=s0)]
    visited = {code}
    n_eval = 0
    for _ in range(M.MAX_ROUNDS):
        pool, seen_src = [], set()
        for node in frontier:
            cands = M.gen(tid, node["code"], Sx, chains, exprs)
            cands = sorted(cands, key=order_key)
            for lab, src in cands:
                if src in visited or src in seen_src:
                    continue
                s, _ = V.ev(tid, src)
                n_eval += 1
                k = M.abstract(lab)
                hit = k in mine
                if hit:
                    TRY[k] = TRY.get(k, 0) + 1
                if s >= 1.0:
                    if hit:
                        WIN[k] = WIN.get(k, 0) + 1
                    return dict(s0=s0, s1=s, n_eval=n_eval, lab=lab, hit=True)
                if s > node["s"]:
                    pool.append(dict(code=src, s=s))
                    seen_src.add(src)
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        kept = pool[:2]
        visited |= {d["code"] for d in pool}
        frontier = kept
    best = max(frontier, key=lambda d: d["s"])
    return dict(s0=s0, s1=best["s"], n_eval=n_eval, lab="<none>", hit=False)


def main() -> None:
    corpus = I.harvest()
    impls = []
    for tid in TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                continue
            impls.append((tid, h))
    pre = {}
    for tid, h in impls:
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        pre[(tid, h)] = dict(Sx=Sx, chains=V.harvest_chains(corpus, tid, h),
                             exprs=W.harvest_exprs(corpus, tid, h))

    print("## §0 冷启动（算基数与记忆，用原版搜索器）")
    cold, wins = {}, {t: [] for t in TIDS}
    for tid, h in impls:
        c = pre[(tid, h)]
        V.CACHE.clear()
        r = M.search(tid, corpus[tid][h], c["Sx"], c["chains"], c["exprs"], lambda x: 0)
        cold[(tid, h)] = r
        if r["hit"]:
            wins[tid].append(dict(tid=tid, h=h, act=list(M.abstract(r["lab"])), label=r["lab"]))
    base = sum(r["n_eval"] for r in cold.values())
    allr = [w for t in TIDS for w in wins[t]]
    print(f"  n_impl = {len(impls)}｜基数 = {base}｜到 1.0 = "
          f"{sum(1 for r in cold.values() if r['s1'] >= 1.0)}/{len(impls)}｜条目 = {len(allr)}")

    def mk(records):
        mem = {t: [] for t in TIDS}
        for r in records:
            mem[r["tid"]].append(dict(h=r["h"], act=r["act"], label=r["label"]))
        return mem

    mem = mk(allr)

    def mine_of(tid, h):
        return {(a, b) for k in mem.get(tid, []) if k["h"] != h for a, b in [tuple(k["act"])]}

    # ---------- §1 记录版 vs 原版：自校验
    print("\n## §1 自校验（记录版必须行为不变）")
    TRY, WIN, recs, origin = {}, {}, {}, {}
    for tid, h in impls:
        c = pre[(tid, h)]
        mine = mine_of(tid, h)
        key = lambda x, mine=mine: (0 if M.abstract(x[0]) in mine else 1)
        V.CACHE.clear()
        recs[(tid, h)] = search_rec(tid, corpus[tid][h], c["Sx"], c["chains"], c["exprs"],
                                    key, mine, TRY, WIN)
        V.CACHE.clear()
        origin[(tid, h)] = M.search(tid, corpus[tid][h], c["Sx"], c["chains"], c["exprs"], key)
    same = [k for k in impls
            if recs[k]["s1"] == origin[k]["s1"] and recs[k]["n_eval"] == origin[k]["n_eval"]]
    w3 = len(same) == len(impls)
    print(f"  (s1, n_eval) 逐实现相同：{len(same)}/{len(impls)} ⇒ {'✅' if w3 else '⛔ 记录器改变了行为'}")
    if not w3:
        for k in impls:
            if k not in same:
                print(f"    差异 {k}: rec {recs[k]['s1']},{recs[k]['n_eval']} "
                      f"vs orig {origin[k]['s1']},{origin[k]['n_eval']}")
    saved0 = sum(cold[k]["n_eval"] - origin[k]["n_eval"] for k in impls)
    print(f"  热启动 saved = {saved0}（基数 {base}）")

    # ---------- §2 判死
    print("\n## §2 记录读数（命中别人记忆的键）")
    seen = sorted(((k, TRY.get(k, 0), WIN.get(k, 0)) for k in TRY), key=lambda t: -t[1])
    print(f"  {'键':34s} {'n_tried':>8s} {'n_win':>6s}")
    for k, t, w in seen:
        print(f"  {str(k):34s} {t:8d} {w:6d}")
    dead = {k for k, t, w in seen if t > 0 and w == 0}
    alive = {k for k, t, w in seen if t > 0 and w > 0}
    print(f"  判死（tried>0 且 win==0）：{sorted(str(k) for k in dead)}")
    print(f"  判活（win>0）：{sorted(str(k) for k in alive)}")

    # ---------- §3 删掉判死键后重跑
    keep = [w for w in allr if tuple(w["act"]) not in dead]
    mem2 = mk(keep)

    def mine2(tid, h):
        return {(a, b) for k in mem2.get(tid, []) if k["h"] != h for a, b in [tuple(k["act"])]}

    arm = {}
    for tid, h in impls:
        c = pre[(tid, h)]
        mine = mine2(tid, h)
        V.CACHE.clear()
        arm[(tid, h)] = M.search(tid, corpus[tid][h], c["Sx"], c["chains"], c["exprs"],
                                 lambda x, mine=mine: (0 if M.abstract(x[0]) in mine else 1))
    saved1 = sum(cold[k]["n_eval"] - arm[k]["n_eval"] for k in impls)
    diffs = [k for k in impls if origin[k]["n_eval"] != arm[k]["n_eval"]]
    print(f"\n## §3 删掉判死键：条目 {len(allr)} → {len(keep)}｜saved {saved0} → {saved1}"
          f"｜逐实现差异 {len(diffs)} 个")

    # ---------- §4 判决
    print("\n## §4 判决")
    w1 = ON_KEY in dead
    print(f"  **W1 判死集合包含 ('T2',\"'on'\")** ⇒ {'✅ 押中' if w1 else '⛔ 押破'}")
    w2 = TRUE_KEY not in dead
    print(f"  **W2 不误杀 ('T2',\"'true'\")**（V128 边际 +446）⇒ "
          f"{'✅ 押中' if w2 else '⛔ 押破'}｜它的 tried={TRY.get(TRUE_KEY, 0)} win={WIN.get(TRUE_KEY, 0)}")
    print(f"  **W3 记录器无副作用 {len(same)}/{len(impls)}** ⇒ {'✅ 押中' if w3 else '⛔ 押破'}")
    w4 = saved1 == saved0 and not diffs
    print(f"  **W4 判死是免费的：saved {saved1} == {saved0} 且差异 0** ⇒ "
          f"{'✅ 押中' if w4 else '⛔ 押破'}")
    w5 = bool(alive)
    print(f"  **W5 正向对照：判活集合非空（{len(alive)} 条）** ⇒ {'✅ 押中' if w5 else '⛔ 押破'}")

    print("\n## §5 汇总")
    print(f"  W1 {'✅' if w1 else '⛔'}　W2 {'✅' if w2 else '⛔'}　W3 {'✅' if w3 else '⛔'}　"
          f"W4 {'✅' if w4 else '⛔'}　W5 {'✅' if w5 else '⛔'}")
    print(f"  自省第二格：判死 {len(dead)} 种键（tried {sum(TRY.get(k,0) for k in dead)} 次、"
          f"win 0 次），判活 {len(alive)} 种（win {sum(WIN.get(k,0) for k in alive)} 次）")

    (HERE / "tried.json").write_text(json.dumps(dict(
        base=base, saved0=saved0, saved1=saved1, n_rec0=len(allr), n_rec1=len(keep),
        n_same=len(same), n_impls=len(impls),
        records={str(k): dict(tried=t, win=w) for k, t, w in seen},
        dead=sorted(str(k) for k in dead), alive=sorted(str(k) for k in alive),
    ), ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  原始读数已落盘 `tried.json`")


if __name__ == "__main__":
    main()
