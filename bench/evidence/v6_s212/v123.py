#!/usr/bin/env python3
"""V123：L5 最小形态 —— 系统自己决定「先改哪个」。

三个排产器（唯一变量 = 顺序）：HUMAN（我给的）/ RANDOM（20 种子）/ SELF（系统自己的优先级）。
同一搜索器、同一动作空间（V120 C1）、同一 k=2、同一记忆（v6_s210/memory.json，留一法）。

零模型、零网络、零子进程。判据 = 现有 test 同一批。
"""
from __future__ import annotations

import ast
import json
import pathlib
import random
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
GS = [150, 300, 450, 600, 1200]
SEEDS = list(range(10))
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
                                 est_cost=len(cands), mem_hit=hit, mine=mine,
                                 abst={M.abstract(lab) for lab, _ in cands})

    impls = list(ctx)

    def human_key(k):
        return (TIDS.index(k[0]), k[1])

    def self_key(k):
        c = ctx[k]
        return (0 if c["mem_hit"] else 1, c["est_cost"], c["s0"], k[1])

    def run(order, G):
        left, reached, spent = G, 0, 0
        log = []
        for k in order:
            if left <= 0:
                break
            c = ctx[k]
            V.CACHE.clear()
            r = B.search(k[0], corpus[k[0]][k[1]], c["Sx"], c["chains"], c["exprs"],
                         lambda x, mine=c["mine"]: (0 if M.abstract(x[0]) in mine else 1),
                         left)
            left -= r["n_eval"]
            spent += r["n_eval"]
            if r["s1"] >= 1.0:
                reached += 1
            log.append((k, r["s1"], r["n_eval"]))
        return dict(reached=reached, spent=spent, log=log)

    print("## §0 三个排序的样子")
    hum = sorted(impls, key=human_key)
    slf = sorted(impls, key=self_key)
    print(f"  HUMAN 前 8：{[(k[0][:6], k[1]) for k in hum[:8]]}")
    print(f"  SELF  前 8：{[(k[0][:6], k[1], ctx[k]['mem_hit'], ctx[k]['est_cost']) for k in slf[:8]]}")
    pos = {k: i for i, k in enumerate(hum)}
    moved = sum(1 for i, k in enumerate(slf) if pos[k] != i)
    print(f"  位置不同的实现数 = {moved}/{len(slf)}")

    print("\n## §1 主读数（到 1.0 的实现数 / 25）")
    res = {}
    for G in GS:
        hres = run(hum, G)
        sres = run(slf, G)
        rr = [run(random.Random(s).sample(impls, len(impls)), G)["reached"] for s in SEEDS]
        mean = sum(rr) / len(rr)
        res[G] = dict(h=hres["reached"], s=sres["reached"], r=mean,
                      r_min=min(rr), r_max=max(rr), spent_s=sres["spent"])
        print(f"  G={G:4d}　HUMAN {hres['reached']:2d}｜SELF {sres['reached']:2d}"
              f"｜RANDOM {mean:5.2f}（{min(rr)}~{max(rr)}）｜SELF 花 {sres['spent']} 次")

    print("\n## §2 判决")
    r600 = res[600]
    print(f"  **W1 G=600：SELF {r600['s']} ≥ HUMAN {r600['h']}** ⇒ "
          f"{'✅ 押中' if r600['s'] >= r600['h'] else '⛔ 押破'}")
    print(f"  **W2 G=600：SELF {r600['s']} > RANDOM 均值 {r600['r']:.2f}** ⇒ "
          f"{'✅ 押中' if r600['s'] > r600['r'] else '⛔ 押破'}")
    print(f"  **W3 排产独立性：位置不同 ≥ 8** ⇒ 实得 {moved} ⇒ "
          f"{'✅ 押中' if moved >= 8 else '⛔ 押破'}")
    ok4 = all(res[1200][x] == 21 for x in ("h", "s")) and abs(res[1200]["r"] - 21) < 1e-9
    print(f"  **W4 G=1200：三者都到 21/25** ⇒ H {res[1200]['h']}｜S {res[1200]['s']}"
          f"｜R {res[1200]['r']:.2f} ⇒ {'✅ 押中' if ok4 else '⛔ 押破'}")
    seq = [res[g]["s"] for g in GS]
    mono = all(a <= b for a, b in zip(seq, seq[1:]))
    print(f"  **W5 SELF 随预算单调不减** ⇒ {dict(zip(GS, seq))}"
          f" ⇒ {'✅ 押中' if mono else '⛔ 押破'}")
    print("\n  **附加（⛔ 跑后发现，未押）：预注册的 G=600 落在饱和区**")
    best = max(GS[:-1], key=lambda g: res[g]["s"] - res[g]["h"])
    print(f"    判别力最大的预算点 = G={best}：SELF {res[best]['s']} vs HUMAN {res[best]['h']}"
          f" vs RANDOM {res[best]['r']:.2f}（差 {res[best]['s'] - res[best]['h']:+d} / "
          f"{res[best]['s'] - res[best]['r']:+.2f}）")

    print("\n## §3 G=600 下 SELF 实际做了什么（前 12 个）")
    for k, s1, n in run(slf, 600)["log"][:12]:
        print(f"  {k[0][:6]} {k[1]} s0={ctx[k]['s0']:.1f} → {s1:.1f}"
              f"　花 {n:3d} 次　mem_hit={ctx[k]['mem_hit']}")

    (HERE / "search.json").write_text(json.dumps(dict(res=res, moved=moved),
                                                 ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  原始读数已落盘 `search.json`")


if __name__ == "__main__":
    main()
