#!/usr/bin/env python3
"""V163 稳健性检查（**不作判据**）：把 `Sx` 改成「每个节点重算」会不会抬高天花板？

部署口径（v162.replay）里 `Sx/ch/ex` 固定为**原实现**的上下文。`Sx` 是 T1/T2 的取值池，
所以「上下文陈旧」是一个可能的混淆：突变引入的新词永远进不了取值池。
本探针在同一 BFS 上只改这一处，比较 d≤3 的层最优。
"""
import ast
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198", "v6_s210", "v6_s236"):
    sys.path.insert(0, str(EV / d))
import idiom_transfer as I          # noqa: E402
import search as S                  # noqa: E402
import v119 as V                    # noqa: E402
import v120 as W                    # noqa: E402
import v121 as X                    # noqa: E402
sys.path.insert(0, str(EV / "v6_s249"))
import v161 as M                    # noqa: E402

TID = "g7d3_fmt_amount"
GAPS = ["0a6acc93", "17747356", "5755e033", "8c8ac7f0"]
D = 3


def bfs_ctx(code, ch, ex, Sx0, dynamic):
    s0 = V.ev(TID, code)[0]
    dist = {code: 0}
    level = {0: (code, s0)}
    frontier = [code]
    n_cand_extra = 0
    for d in range(1, D + 1):
        nxt, best, bs = [], None, -1.0
        for c in frontier:
            Sx = S.wordset(TID, c, True)[0] if dynamic else Sx0
            for lab, src in X.gen(TID, c, Sx, ch, ex):
                if src in dist:
                    continue
                n_cand_extra += 0
                sc = V.ev(TID, src)[0]
                dist[src] = d
                nxt.append(src)
                if sc > bs:
                    best, bs = src, sc
        level[d] = (best, bs)
        frontier = nxt
    return level, len(dist)


def main() -> None:
    corpus = I.harvest()
    impls = []
    for h in sorted(corpus[TID]):
        try:
            ast.parse(corpus[TID][h])
        except SyntaxError:
            continue
        impls.append(h)
    s0 = {(TID, h): V.ev(TID, corpus[TID][h])[0] for h in impls}
    print("  | impl | s0 | 固定 `Sx`（部署口径）d1/d2/d3 | **动态 `Sx`** d1/d2/d3 | 状态数 固定/动态 |")
    print("  |---|---|---|---|---|")
    for h in GAPS:
        code = corpus[TID][h]
        Sx, _ = S.wordset(TID, code, True)
        ch, _m = M.chains_meta(corpus, TID, h, s0)
        ex = W.harvest_exprs(corpus, TID, h)
        lf, nf = bfs_ctx(code, ch, ex, Sx, False)
        ld, nd = bfs_ctx(code, ch, ex, Sx, True)
        a = "/".join(f"{lf[d][1]:.2f}" for d in (1, 2, 3))
        b = "/".join(f"{ld[d][1]:.2f}" for d in (1, 2, 3))
        print(f"  | {h[:8]} | {s0[(TID,h)]:.2f} | {a} | **{b}** | {nf}/{nd} |")


if __name__ == "__main__":
    main()
