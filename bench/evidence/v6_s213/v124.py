#!/usr/bin/env python3
"""V124：L5 最小形态 —— 在正确的工作点上重做，并加「内容对照」臂。

四个排产器（唯一变量 = 顺序的来源）：
  HUMAN  ：我给的固定顺序
  RANDOM ：20 种子
  SELF   ：(mem_hit, est_cost, s0) —— 只用系统免费可观测的量
  SHUF   ：同一启发式，但把 est_cost 在实现之间随机置换（保留分布、毁掉信息）

同一搜索器、同一动作空间（V120 C1）、同一 k=2、同一记忆（留一法）。
零模型、零网络、零子进程。判据 = 现有 test 同一批。
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
import v122 as B              # noqa: E402

TIDS = V.TIDS
MEM = EV / "v6_s210" / "memory.json"
GS = [150, 300, 450]
G_SAT = 600
SEEDS = list(range(20))
ev = V.ev


def main() -> None:
    corpus = I.harvest()
    mem = json.loads(MEM.read_text(encoding="utf-8"))
    ctx = {}
    for tid in TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                continue
            Sx, _ = S.wordset(tid, corpus[tid][h], True)
            chains = V.harvest_chains(corpus, tid, h)
            exprs = W.harvest_exprs(corpus, tid, h)
            base = corpus[tid][h]
            s0, _ = ev(tid, base)
            cands = M.gen(tid, base, Sx, chains, exprs)
            mine = {(a, b) for k in mem[tid] if k["h"] != h for a, b in [tuple(k["act"])]}
            hit = any(M.abstract(lab) in mine for lab, _ in cands)
            ctx[(tid, h)] = dict(Sx=Sx, chains=chains, exprs=exprs, s0=s0,
                                 est_cost=len(cands), mem_hit=hit, mine=mine)
    impls = list(ctx)

    def run(order, G):
        left, reached = G, 0
        for k in order:
            if left <= 0:
                break
            c = ctx[k]
            V.CACHE.clear()
            r = B.search(k[0], corpus[k[0]][k[1]], c["Sx"], c["chains"], c["exprs"],
                         lambda x, mine=c["mine"]: (0 if M.abstract(x[0]) in mine else 1),
                         left)
            left -= r["n_eval"]
            if r["s1"] >= 1.0:
                reached += 1
        return reached

    hum = sorted(impls, key=lambda k: (TIDS.index(k[0]), k[1]))
    slf = sorted(impls, key=lambda k: (0 if ctx[k]["mem_hit"] else 1,
                                       ctx[k]["est_cost"], ctx[k]["s0"], k[1]))

    print("## §0 固定臂")
    print(f"  |impls| = {len(impls)}｜记忆 n_rec = {sum(len(v) for v in mem.values())}")
    print(f"  SELF 前 6：{[(k[0][:6], ctx[k]['est_cost'], ctx[k]['mem_hit']) for k in slf[:6]]}")
    print(f"  与 HUMAN 位置不同 = {sum(1 for i, k in enumerate(slf) if hum[i] != k)}/{len(slf)}")

    print("\n## §1 主读数（到 1.0 的实现数 / 25）")
    res = {}
    for G in GS + [G_SAT]:
        h = run(hum, G)
        s = run(slf, G)
        rr = [run(random.Random(seed).sample(impls, len(impls)), G) for seed in SEEDS]
        sh = []
        for seed in SEEDS:
            rng = random.Random(1000 + seed)
            perm = list(range(len(impls)))
            rng.shuffle(perm)
            fake = {k: dict(ctx[k]) for k in impls}
            costs = [ctx[k]["est_cost"] for k in impls]
            for k, j in zip(impls, perm):
                fake[k]["est_cost"] = costs[j]
            order = sorted(impls, key=lambda k: (0 if fake[k]["mem_hit"] else 1,
                                                 fake[k]["est_cost"], fake[k]["s0"], k[1]))
            sh.append(run(order, G))
        res[G] = dict(h=h, s=s, r=statistics.mean(rr), rmin=min(rr), rmax=max(rr),
                      sh=statistics.mean(sh), shmin=min(sh), shmax=max(sh))
        print(f"  G={G:3d}　HUMAN {h:2d}｜SELF {s:2d}｜RANDOM {statistics.mean(rr):5.2f}"
              f"（{min(rr)}~{max(rr)}）｜SHUF {statistics.mean(sh):5.2f}（{min(sh)}~{max(sh)}）")

    print("\n## §2 判决")
    w1 = all(res[g]["s"] > res[g]["h"] for g in GS)
    print(f"  **W1 三个 G 上 SELF > HUMAN** ⇒ "
          f"{ {g: (res[g]['s'], res[g]['h']) for g in GS} } ⇒ {'✅ 押中' if w1 else '⛔ 押破'}")

    g150 = res[150]
    w2 = g150["s"] > g150["r"] + 2
    print(f"  **W2 G=150：SELF {g150['s']} > RANDOM {g150['r']:.2f} + 2** ⇒ "
          f"{'✅ 押中' if w2 else '⛔ 押破'}")

    w3 = g150["s"] > g150["sh"]
    print(f"  **W3（内容对照）G=150：SELF {g150['s']} > SHUF {g150['sh']:.2f}** ⇒ "
          f"{'✅ 押中' if w3 else '⛔ 押破'}")
    print(f"      ⇒ 收益{'来自启发式的内容' if w3 else '**不来自**内容，只是顺序不同'}")

    w4 = abs(g150["sh"] - g150["r"]) <= 1
    print(f"  **W4（负对照）G=150：|SHUF − RANDOM| = {abs(g150['sh'] - g150['r']):.2f} ≤ 1** ⇒ "
          f"{'✅ 押中' if w4 else '⛔ 押破'}")

    seq = [res[g]["s"] for g in GS]
    w5 = all(a <= b for a, b in zip(seq, seq[1:]))
    print(f"  **W5 SELF 随 G 单调不减** ⇒ {dict(zip(GS, seq))} ⇒ "
          f"{'✅ 押中' if w5 else '⛔ 押破'}")

    print(f"\n## §3 收敛对照（不参与判决）：G={G_SAT} 时 "
          f"HUMAN {res[G_SAT]['h']}｜SELF {res[G_SAT]['s']}｜"
          f"RANDOM {res[G_SAT]['r']:.2f}｜SHUF {res[G_SAT]['sh']:.2f}")

    (HERE / "search.json").write_text(json.dumps(dict(res={str(k): v for k, v in res.items()}),
                                                 ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  原始读数已落盘 `search.json`")


if __name__ == "__main__":
    main()
