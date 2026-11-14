#!/usr/bin/env python3
"""V130：删掉「永不匹配」的 baseline 记录 —— 免费减半，saved 一次不掉。

两臂唯一变量 = 是否保留 ('?','<baseline>') 记录。零模型、零网络。
"""
from __future__ import annotations

import ast
import json
import pathlib
import random
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
import v128 as R              # noqa: E402

N_SHUFF = 30
BASE_KEY = ("?", "<baseline>")


def main() -> None:
    corpus = I.harvest()
    impls = []
    for tid in V.TIDS:
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
    cold, wins = {}, {t: [] for t in V.TIDS}
    for tid, h in impls:
        c = pre[(tid, h)]
        V.CACHE.clear()
        r = M.search(tid, corpus[tid][h], c["Sx"], c["chains"], c["exprs"], lambda x: 0)
        cold[(tid, h)] = r
        if r["hit"]:
            wins[tid].append(dict(tid=tid, h=h, act=list(M.abstract(r["lab"])), label=r["lab"]))
    base = sum(r["n_eval"] for r in cold.values())
    allr = [w for t in V.TIDS for w in wins[t]]
    nb = [w for w in allr if tuple(w["act"]) != BASE_KEY]
    print(f"  n_impl = {len(impls)}｜基数 = {base}｜ALL {len(allr)} 条｜ALL_NB {len(nb)} 条")
    print(f"  被删的 {len(allr)-len(nb)} 条来自："
          f"{[(w['tid'][:6], w['h'][:8]) for w in allr if tuple(w['act']) == BASE_KEY]}")

    def mk(records):
        mem = {t: [] for t in V.TIDS}
        for r in records:
            mem[r["tid"]].append(dict(h=r["h"], act=r["act"], label=r["label"]))
        return mem

    def run(records, tag, verbose=False):
        arm = R.sweep(impls, corpus, pre, mk(records))
        saved = sum(cold[k]["n_eval"] - arm[k]["n_eval"] for k in impls)
        to1 = sum(1 for k in arm if arm[k]["s1"] >= 1.0)
        print(f"  {tag:10s} 条目 {len(records):2d}｜saved {saved:4d}（{saved/base*100:5.1f}%）"
              f"｜到 1.0 {to1}/{len(impls)}")
        return dict(saved=saved, n=len(records), to1=to1, per={f"{k[0]}::{k[1]}": v["n_eval"]
                                                                for k, v in arm.items()})

    print("\n## §1 两臂")
    a_all = run(allr, "ALL")
    a_nb = run(nb, "ALL_NB")

    print(f"\n## §2 零假设（随机删 10 条，{N_SHUFF} 次，seed=0）")
    rng = random.Random(0)
    sh = []
    for _ in range(N_SHUFF):
        keep = rng.sample(allr, len(nb))
        arm = R.sweep(impls, corpus, pre, mk(keep))
        sh.append(sum(cold[k]["n_eval"] - arm[k]["n_eval"] for k in impls))
    sh.sort()
    p95 = sh[int(0.95 * (len(sh) - 1))]

    print("\n## §3 判决")
    w1 = a_nb["saved"] == a_all["saved"]
    print(f"  **W1 ALL_NB {a_nb['saved']} 严格等于 ALL {a_all['saved']}** ⇒ "
          f"{'✅ 押中' if w1 else '⛔ 押破'}")

    w2 = a_nb["to1"] == 21
    print(f"  **W2 到 1.0 仍 21/25** ⇒ {a_nb['to1']}/25 ⇒ {'✅ 押中' if w2 else '⛔ 押破'}")

    w3 = a_nb["n"] == 11 and a_nb["saved"] == base * 0  # 占位，真判据在下一行
    red = (1 - a_nb["n"] / a_all["n"]) * 100
    print(f"  **W3 条目 {a_all['n']} → {a_nb['n']}（−{red:.0f}%）而 saved 不变** ⇒ "
          f"{'✅ 押中' if (a_nb['n'] == 11 and w1) else '⛔ 押破'}")

    w4 = a_nb["saved"] >= p95
    print(f"  **W4 ALL_NB {a_nb['saved']} ≥ 随机删 10 条 p95 {p95}**"
          f"（均值 {statistics.mean(sh):.0f}｜min {sh[0]}｜p50 {sh[len(sh)//2]}｜max {sh[-1]}）⇒ "
          f"{'✅ 押中' if w4 else '⛔ 押破'}")
    above = sum(1 for v in sh if v >= a_nb["saved"])
    print(f"      随机删 10 条能达到 {a_nb['saved']} 的次数 = {above}/{len(sh)}")

    print("\n## §4 逐实现差异（ALL vs ALL_NB，只打非零的）")
    diffs = [(k, a_all["per"][k], a_nb["per"][k]) for k in a_all["per"]
             if a_all["per"][k] != a_nb["per"][k]]
    print(f"  有差异的实现数 = {len(diffs)}")
    for k, x, y in diffs[:20]:
        print(f"    {k}  ALL {x} vs NB {y}")

    print("\n## §5 汇总")
    print(f"  W1 {'✅' if w1 else '⛔'}　W2 {'✅' if w2 else '⛔'}　W3 {'✅' if (a_nb['n']==11 and w1) else '⛔'}　W4 {'✅' if w4 else '⛔'}")
    print(f"  条目 −{red:.0f}%｜saved {a_all['saved']} → {a_nb['saved']}")

    (HERE / "nb.json").write_text(json.dumps(
        dict(base=base, ALL=a_all, ALL_NB=a_nb,
             shuf=dict(mean=statistics.mean(sh), p50=sh[len(sh)//2], p95=p95,
                       mn=sh[0], mx=sh[-1], above=above, n=N_SHUFF),
             all_records=allr, nb_records=nb),
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  原始读数已落盘 `nb.json`")


if __name__ == "__main__":
    main()
