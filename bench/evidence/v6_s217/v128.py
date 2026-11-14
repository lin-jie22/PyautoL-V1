#!/usr/bin/env python3
"""V128：把预测器的输出接到写端 —— 只写「值」的条目（记忆裁剪）。

三臂 + 零假设，唯一变量 = 写端条目集合：
  ALL   ：全部生效动作（= V121 原样）
  TOP4  ：只写 V127 预测器判定的 top4 实现（n_cand 最大的 4 个未满分实现）
  REV4  ：只写最差 4 个（n_cand 最小、saved 全 0）
  SHUF  ：从 ALL 的 21 条里随机抽 4 条（100 次），零假设分布
同搜索器 / 同动作空间 / 同留一法 / 同 k=2 / 同 25 个实现。零模型、零网络、零子进程。
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
for d in ("v6_s205", "v6_s208", "v6_s209", "v6_s210", "v6_s211", "v6_s198"):
    sys.path.insert(0, str(EV / d))
import search as S            # noqa: E402
import idiom_transfer as I     # noqa: E402
import v119 as V              # noqa: E402
import v120 as W              # noqa: E402
import v121 as M              # noqa: E402

TIDS = V.TIDS
TOP4 = ["a579b07a", "1aa5099d", "37a4f17f", "f2d1efa6"]
REV4 = ["17747356", "5755e033", "8c8ac7f0", "0a6acc93"]
N_SHUFF = 100
ev = V.ev


def sweep(impls, corpus, pre, mem) -> dict:
    """跑一遍 warm sweep（留一法），返回 per-impl 结果。"""
    out = {}
    for tid, h in impls:
        c = pre[(tid, h)]
        Sx, chains, exprs = c["Sx"], c["chains"], c["exprs"]
        mine = {(a, b) for k in mem.get(tid, []) if k["h"] != h for a, b in [tuple(k["act"])]}

        def key(x, mine=mine):
            return 0 if M.abstract(x[0]) in mine else 1

        V.CACHE.clear()
        out[(tid, h)] = M.search(tid, corpus[tid][h], Sx, chains, exprs, key)
    return out


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

    # ---------- 冷启动一次（三臂共用同一条基线，⛔ 不许复用 V121 的 warm 结果）
    print("## §0 冷启动（每臂都从这里算 saved）")
    cold, wins = {}, {t: [] for t in TIDS}
    for tid, h in impls:
        c = pre[(tid, h)]
        V.CACHE.clear()
        r = M.search(tid, corpus[tid][h], c["Sx"], c["chains"], c["exprs"], lambda x: 0)
        cold[(tid, h)] = r
        if r["hit"]:
            wins[tid].append(dict(h=h, act=list(M.abstract(r["lab"])), label=r["lab"]))
    base = sum(r["n_eval"] for r in cold.values())
    wins_all = [dict(tid=t, **w) for t in TIDS for w in wins[t]]
    print(f"  n_impl = {len(impls)}｜冷 saved 基数 = {base}｜ALL 条目 = {len(wins_all)}")
    print(f"  到 1.0 = {sum(1 for r in cold.values() if r['s1'] >= 1.0)}/{len(impls)}")

    def mk(records):
        mem = {t: [] for t in TIDS}
        for r in records:
            mem[r["tid"]].append(dict(h=r["h"], act=r["act"], label=r["label"]))
        return mem

    def run(records, tag):
        mem = mk(records)
        arm = sweep(impls, corpus, pre, mem)
        saved = sum(cold[k]["n_eval"] - arm[k]["n_eval"] for k in impls)
        to1 = sum(1 for k in arm if arm[k]["s1"] >= 1.0)
        n_rec = sum(len(v) for v in mem.values())
        print(f"  {tag:6s} 条目 {n_rec:2d}｜saved {saved:4d}/{base}（{saved/base*100:5.1f}%）"
              f"｜到 1.0 {to1}/{len(impls)}")
        return dict(saved=saved, n_rec=n_rec, to1=to1)

    print("\n## §1 三臂")
    a_all = run(wins_all, "ALL")
    a_top = run([w for w in wins_all if w["h"] in TOP4], "TOP4")
    a_rev = run([w for w in wins_all if w["h"] in REV4], "REV4")

    print(f"\n## §2 零假设（从 21 条随机抽 4 条，{N_SHUFF} 次，seed=0）")
    rng = random.Random(0)
    sh = []
    for _ in range(N_SHUFF):
        pick = rng.sample(wins_all, 4)
        mem = mk(pick)
        arm = sweep(impls, corpus, pre, mem)
        sh.append(sum(cold[k]["n_eval"] - arm[k]["n_eval"] for k in impls))
    sh.sort()
    p95 = sh[int(0.95 * (len(sh) - 1))]

    print("\n## §3 判决")
    w1 = a_top["saved"] >= 0.90 * base and a_top["n_rec"] <= 5
    print(f"  **W1 TOP4 的 saved {a_top['saved']} ≥ 90% × {base} = {0.90*base:.1f}"
          f" 且条目 {a_top['n_rec']} ≤ 5** ⇒ {'✅ 押中' if w1 else '⛔ 押破'}"
          f"（实得 {a_top['saved']/base*100:.1f}%）")

    w2 = a_top["saved"] > a_rev["saved"] and a_rev["saved"] <= 50
    print(f"  **W2 TOP4 {a_top['saved']} > REV4 {a_rev['saved']} 且 REV4 ≤ 50** ⇒ "
          f"{'✅ 押中' if w2 else '⛔ 押破'}")

    w3 = a_top["to1"] >= a_all["to1"]
    print(f"  **W3 TOP4 到 1.0 {a_top['to1']} ≥ ALL {a_all['to1']}** ⇒ "
          f"{'✅ 押中' if w3 else '⛔ 押破'}")

    w4 = a_top["saved"] >= p95
    print(f"  **W4 TOP4 {a_top['saved']} ≥ 重排 p95 {p95}**"
          f"（分布 均值 {statistics.mean(sh):.0f}｜min {sh[0]}｜p50 {sh[len(sh)//2]}｜max {sh[-1]}）"
          f" ⇒ {'✅ 押中' if w4 else '⛔ 押破'}")
    above = sum(1 for v in sh if v >= a_top["saved"])
    print(f"      超过 TOP4 的重排数 = {above}/{len(sh)}")

    print("\n## §4 汇总")
    print(f"  W1 {'✅' if w1 else '⛔'}　W2 {'✅' if w2 else '⛔'}　W3 {'✅' if w3 else '⛔'}　W4 {'✅' if w4 else '⛔'}")
    print(f"  条目 {a_all['n_rec']} → {a_top['n_rec']}（{a_top['n_rec']/a_all['n_rec']*100:.0f}%）"
          f"｜saved {a_all['saved']} → {a_top['saved']}（{a_top['saved']/a_all['saved']*100:.1f}%）")
    print(f"  对冷启动的节省：{a_all['saved']/base*100:.1f}% → {a_top['saved']/base*100:.1f}%")

    (HERE / "trim.json").write_text(json.dumps(
        dict(base=base, ALL=a_all, TOP4=a_top, REV4=a_rev,
             shuf=dict(mean=statistics.mean(sh), p50=sh[len(sh)//2], p95=p95, mn=sh[0], mx=sh[-1],
                       above_top4=above, n=N_SHUFF),
             records=wins_all), ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  原始读数已落盘 `trim.json`")


if __name__ == "__main__":
    main()
