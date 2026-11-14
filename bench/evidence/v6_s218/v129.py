#!/usr/bin/env python3
"""V129：裁剪的正确单位是「抽象动作」—— 21 条去重成 7 条，节省不该掉。

三臂唯一变量 = 条目集合：ALL(21) / DEDUP(7) / DEDUP_NB(6)。
同搜索器 / 同动作空间 / 同留一法 / 同 k=2 / 同 25 个实现。零模型、零网络。
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

    print("## §0 冷启动（每轮重算，⛔ 不借旧数）")
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
    print(f"  n_impl = {len(impls)}｜基数 = {base}｜到 1.0 = "
          f"{sum(1 for r in cold.values() if r['s1'] >= 1.0)}/{len(impls)}｜ALL 条目 = {len(allr)}")

    # 去重：每个 (tid, 抽象动作) 留第一条
    seen, dedup = set(), []
    for w in allr:
        k = (w["tid"], tuple(w["act"]))
        if k not in seen:
            seen.add(k)
            dedup.append(w)
    nb = [w for w in dedup if tuple(w["act"]) != ("?", "<baseline>")]
    print(f"  去重后 {len(dedup)} 条｜再去 baseline {len(nb)} 条")

    def mk(records):
        mem = {t: [] for t in V.TIDS}
        for r in records:
            mem[r["tid"]].append(dict(h=r["h"], act=r["act"], label=r["label"]))
        return mem

    def run(records, tag):
        arm = R.sweep(impls, corpus, pre, mk(records))
        saved = sum(cold[k]["n_eval"] - arm[k]["n_eval"] for k in impls)
        to1 = sum(1 for k in arm if arm[k]["s1"] >= 1.0)
        print(f"  {tag:10s} 条目 {len(records):2d}｜saved {saved:4d}（{saved/base*100:5.1f}%）"
              f"｜到 1.0 {to1}/{len(impls)}")
        return dict(saved=saved, n=len(records), to1=to1)

    print("\n## §1 三臂")
    a_all = run(allr, "ALL")
    a_dd = run(dedup, "DEDUP")
    a_nb = run(nb, "DEDUP_NB")

    print(f"\n## §2 零假设（从 21 条随机抽 7 条，{N_SHUFF} 次，seed=0）")
    rng = random.Random(0)
    sh = []
    for _ in range(N_SHUFF):
        arm = R.sweep(impls, corpus, pre, mk(rng.sample(allr, 7)))
        sh.append(sum(cold[k]["n_eval"] - arm[k]["n_eval"] for k in impls))
    sh.sort()
    p95 = sh[int(0.95 * (len(sh) - 1))]

    print("\n## §3 判决")
    w1 = a_dd["saved"] >= 0.95 * base and a_dd["n"] == 7
    print(f"  **W1 DEDUP saved {a_dd['saved']} ≥ 95%×{base} = {0.95*base:.1f} 且条目 == 7** ⇒ "
          f"{'✅ 押中' if w1 else '⛔ 押破'}（条目 {a_dd['n']}，占比 {a_dd['saved']/base*100:.1f}%）")

    w2 = a_nb["saved"] == a_dd["saved"]
    print(f"  **W2 DEDUP_NB {a_nb['saved']} 严格等于 DEDUP {a_dd['saved']}** ⇒ "
          f"{'✅ 押中' if w2 else '⛔ 押破'}")

    w3 = a_dd["to1"] == len(impls) - 4 and a_nb["to1"] == len(impls) - 4
    print(f"  **W3 两臂到 1.0 都是 21/25** ⇒ {a_dd['to1']} / {a_nb['to1']} ⇒ "
          f"{'✅ 押中' if w3 else '⛔ 押破'}")

    w4 = a_dd["saved"] >= p95
    print(f"  **W4 DEDUP {a_dd['saved']} ≥ 随机 7 条 p95 {p95}**"
          f"（均值 {statistics.mean(sh):.0f}｜min {sh[0]}｜p50 {sh[len(sh)//2]}｜max {sh[-1]}）⇒ "
          f"{'✅ 押中' if w4 else '⛔ 押破'}")
    above = sum(1 for v in sh if v >= a_dd["saved"])
    print(f"      超过 DEDUP 的重排数 = {above}/{len(sh)}")

    print("\n## §4 汇总")
    print(f"  W1 {'✅' if w1 else '⛔'}　W2 {'✅' if w2 else '⛔'}　W3 {'✅' if w3 else '⛔'}　W4 {'✅' if w4 else '⛔'}")
    print(f"  条目 21 → 7（{7/21*100:.0f}%）｜saved 793 → {a_dd['saved']}"
          f"（{a_dd['saved']/a_all['saved']*100:.1f}% of ALL）")

    (HERE / "trim2.json").write_text(json.dumps(
        dict(base=base, ALL=a_all, DEDUP=a_dd, DEDUP_NB=a_nb,
             shuf=dict(mean=statistics.mean(sh), p50=sh[len(sh)//2], p95=p95,
                       mn=sh[0], mx=sh[-1], above=a_dd["saved"] and above, n=N_SHUFF),
             dedup_records=dedup, nb_records=nb, all_records=allr),
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  原始读数已落盘 `trim2.json`")


if __name__ == "__main__":
    main()
