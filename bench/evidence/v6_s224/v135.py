#!/usr/bin/env python3
"""V135：用二分（分组测试）把「哪些记忆有贡献」的识别成本压到 O(log n)。

穷举基线 = 逐条删 21 次；二分 = 一次删一组，全废就整组丢。
同时量加性（删全部的损失 == 各条损失之和？）与顺序无关性（随机打乱 5 次）。
零模型、零网络、零子进程。
"""
from __future__ import annotations

import ast
import json
import pathlib
import random
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
BASE = 793          # 跑前写死：V121/V128/V131/V134 都是这个数
N_SHUFF = 5


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

    print("## §0 冷启动")
    cold, wins = {}, {t: [] for t in TIDS}
    for tid, h in impls:
        c = pre[(tid, h)]
        V.CACHE.clear()
        r = M.search(tid, corpus[tid][h], c["Sx"], c["chains"], c["exprs"], lambda x: 0)
        cold[(tid, h)] = r
        if r["hit"]:
            wins[tid].append(dict(tid=tid, h=h, act=list(M.abstract(r["lab"])), label=r["lab"]))
    base_n = sum(r["n_eval"] for r in cold.values())
    allr = [dict(idx=i, **w) for i, w in enumerate(w for t in TIDS for w in wins[t])]
    print(f"  n_impl = {len(impls)}｜判分基数 = {base_n}｜条目 = {len(allr)}")

    def mk(records):
        mem = {t: [] for t in TIDS}
        for r in records:
            mem[r["tid"]].append(dict(h=r["h"], act=r["act"], label=r["label"]))
        return mem

    n_sweep = [0]

    def saved_without(recs_idx):
        drop = set(recs_idx)
        keep = [r for r in allr if r["idx"] not in drop]
        mem = mk(keep)
        tot = 0
        n_sweep[0] += 1
        for tid, h in impls:
            c = pre[(tid, h)]
            mine = {(a, b) for k in mem.get(tid, []) if k["h"] != h for a, b in [tuple(k["act"])]}
            V.CACHE.clear()
            r = M.search(tid, corpus[tid][h], c["Sx"], c["chains"], c["exprs"],
                         lambda x, mine=mine: (0 if M.abstract(x[0]) in mine else 1))
            tot += cold[(tid, h)]["n_eval"] - r["n_eval"]
        return tot

    base_saved = saved_without([])
    print(f"  全量 saved = {base_saved}（跑前写死 {BASE}）")

    # ---------- §1 穷举：逐条删
    print("\n## §1 穷举基线（逐条删，21 次）")
    marg = {}
    for r in allr:
        marg[r["idx"]] = base_saved - saved_without([r["idx"]])
    useful = {i for i, d in marg.items() if d > 0}
    print(f"  实验次数 = {n_sweep[0] - 1}")
    for r in sorted(allr, key=lambda r: -marg[r["idx"]]):
        print(f"  {r['tid'][:6]} {r['h']} {str(tuple(r['act'])):28s} 边际 {marg[r['idx']]:+5d}")
    print(f"  有贡献的记录 = {len(useful)}/{len(allr)}")

    # ---------- §2 二分
    def bisect(items, exp):
        exp[0] += 1
        s = saved_without([r["idx"] for r in items])
        if s >= base_saved:                      # 整组全废
            return set()
        if len(items) == 1:
            return {items[0]["idx"]}             # 单条且有贡献
        mid = len(items) // 2
        return bisect(items[:mid], exp) | bisect(items[mid:], exp)

    print("\n## §2 二分（按记录顺序）")
    exp = [0]
    got = bisect(allr, exp)
    print(f"  实验次数 = {exp[0]}｜判为有贡献 = {len(got)}｜与穷举集合相等 = {got == useful}")
    if got != useful:
        print(f"    二分多判（穷举说废）：{sorted(got - useful)}")
        print(f"    二分漏判（穷举说有用）：{sorted(useful - got)}")

    # ---------- §3 加性
    all_loss = base_saved - saved_without([r["idx"] for r in allr])
    sum_loss = sum(marg[i] for i in marg)
    rel = abs(all_loss - sum_loss) / sum_loss if sum_loss else 0.0
    print("\n## §3 加性检验")
    print(f"  删全部的损失 = {all_loss}｜各条单独损失之和 = {sum_loss}｜相对误差 {rel*100:.1f}%")

    # ---------- §4 随机打乱 5 次
    print(f"\n## §4 顺序无关（随机打乱 {N_SHUFF} 次）")
    rng = random.Random(0)
    ok_all, cnt_all = True, []
    for t in range(N_SHUFF):
        perm = allr[:]
        rng.shuffle(perm)
        e = [0]
        g = bisect(perm, e)
        same = g == useful
        ok_all &= same
        cnt_all.append(e[0])
        print(f"  #{t+1} 实验 {e[0]:2d} 次｜集合相等 {same}")
    print(f"  5 次全相等 = {ok_all}｜次数 {min(cnt_all)}~{max(cnt_all)}")

    # ---------- 判决
    print("\n## §5 判决")
    w1 = got == useful
    print(f"  **W1 二分集合 == 穷举集合** ⇒ {len(got)} vs {len(useful)}｜相等 {w1} ⇒ "
          f"{'✅ 押中' if w1 else '⛔ 押破'}")
    w2 = exp[0] < len(allr)
    print(f"  **W2 二分实验 {exp[0]} 次 < 穷举 {len(allr)} 次** ⇒ {'✅ 押中' if w2 else '⛔ 押破'}")
    w3 = rel < 0.10
    print(f"  **W3 加性相对误差 {rel*100:.1f}% < 10%** ⇒ {'✅ 押中' if w3 else '⛔ 押破'}")
    w4 = ok_all and all(c < len(allr) for c in cnt_all)
    print(f"  **W4 打乱 5 次全部集合相等且都更少** ⇒ {'✅ 押中' if w4 else '⛔ 押破'}")

    print("\n## §6 汇总")
    print(f"  W1 {'✅' if w1 else '⛔'}　W2 {'✅' if w2 else '⛔'}　W3 {'✅' if w3 else '⛔'}　"
          f"W4 {'✅' if w4 else '⛔'}")
    print(f"  实验次数：穷举 {len(allr)} → 二分 {exp[0]}（省 {len(allr)-exp[0]} 次）")

    (HERE / "bisect.json").write_text(json.dumps(dict(
        base_saved=base_saved, base_n=base_n, n_rec=len(allr),
        marg={str(k): v for k, v in sorted(marg.items())},
        useful=sorted(useful), bisect=sorted(got), bisect_n=exp[0],
        exhaustive_n=len(allr),
        add=dict(all_loss=all_loss, sum_loss=sum_loss, rel=rel),
        shuffle=dict(counts=cnt_all, all_same=ok_all),
        records=[dict(idx=r["idx"], tid=r["tid"], h=r["h"], act=r["act"]) for r in allr],
    ), ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  原始读数已落盘 `bisect.json`")


if __name__ == "__main__":
    main()
