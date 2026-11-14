#!/usr/bin/env python3
"""V124 跑后诊断（未押）：mem_hit 帮了还是拖了。三个排序键，同一搜索器/记忆/预算。"""
from __future__ import annotations
import ast, json, pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for d in ("v6_s205","v6_s208","v6_s209","v6_s210","v6_s211","v6_s198"):
    sys.path.insert(0, str(EV / d))
import search as S
import idiom_transfer as I
import v119 as V
import v120 as W
import v121 as M
import v122 as B

TIDS = V.TIDS
MEM = EV / "v6_s210" / "memory.json"
GS = [150, 300, 450]

def main():
    corpus = I.harvest()
    mem = json.loads(MEM.read_text(encoding="utf-8"))
    ctx = {}
    for tid in TIDS:
        for h in sorted(corpus[tid]):
            try: ast.parse(corpus[tid][h])
            except SyntaxError: continue
            Sx, _ = S.wordset(tid, corpus[tid][h], True)
            chains = V.harvest_chains(corpus, tid, h)
            exprs = W.harvest_exprs(corpus, tid, h)
            s0, _ = V.ev(tid, corpus[tid][h])
            cands = M.gen(tid, corpus[tid][h], Sx, chains, exprs)
            mine = {(a, b) for k in mem[tid] if k["h"] != h for a, b in [tuple(k["act"])]}
            hit = any(M.abstract(lab) in mine for lab, _ in cands)
            ctx[(tid, h)] = dict(Sx=Sx, chains=chains, exprs=exprs, s0=s0,
                                 est_cost=len(cands), mem_hit=hit, mine=mine)
    impls = list(ctx)
    def run(order, G):
        left, reached = G, 0
        for k in order:
            if left <= 0: break
            c = ctx[k]
            V.CACHE.clear()
            r = B.search(k[0], corpus[k[0]][k[1]], c["Sx"], c["chains"], c["exprs"],
                         lambda x, mine=c["mine"]: (0 if M.abstract(x[0]) in mine else 1), left)
            left -= r["n_eval"]
            if r["s1"] >= 1.0: reached += 1
        return reached
    keys = {
        "SELF": lambda k: (0 if ctx[k]["mem_hit"] else 1, ctx[k]["est_cost"], ctx[k]["s0"], k[1]),
        "COST_ONLY": lambda k: (ctx[k]["est_cost"], ctx[k]["s0"], k[1]),
        "MEM_ONLY": lambda k: (0 if ctx[k]["mem_hit"] else 1, ctx[k]["s0"], k[1]),
    }
    print("## V124 跑后诊断（未押）：mem_hit 帮了还是拖了")
    print(f"  n_impl = {len(impls)}")
    print("  G    " + "".join(f"{n:>12s}" for n in keys))
    for G in GS:
        vals = [run(sorted(impls, key=f), G) for f in keys.values()]
        print(f"  {G:3d}  " + "".join(f"{v:>12d}" for v in vals))
    print("\n## mem_hit=True 且未满分的实现（就是它们被提到了最前面）")
    for k in impls:
        c = ctx[k]
        if c["mem_hit"] and c["s0"] < 1.0:
            print(f"  {k[0][:6]} {k[1]} s0={c['s0']:.1f} est_cost={c['est_cost']}")
    print("\n## COST_ONLY 的前 8（它先做谁）")
    for k in sorted(impls, key=keys["COST_ONLY"])[:8]:
        print(f"  {k[0][:6]} {k[1]} s0={ctx[k]['s0']:.1f} est_cost={ctx[k]['est_cost']} mem_hit={ctx[k]['mem_hit']}")

if __name__ == "__main__":
    main()
