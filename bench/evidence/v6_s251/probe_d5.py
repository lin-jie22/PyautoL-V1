#!/usr/bin/env python3
"""V163 探索臂（**超出预注册，不作判据**）：把深度推到 5，看天花板会不会动。

理由：预注册只到 D=4。若「不动」只在 d≤4 成立，读者无法排除「再深一点就到了」。
本探针只回答一件事：**d=5 的最优分数是否仍然等于 `U` 臂的 s1**。
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
sys.path.insert(0, str(EV / "v6_s251"))
import v163 as E                    # noqa: E402

TID = "g7d3_fmt_amount"
GAPS = ["0a6acc93", "17747356", "5755e033", "8c8ac7f0"]


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
    print("  | impl | s0 | d≤4 最好 | **d≤5 最好** | d5 新状态 | d5 生成 | 命中 |")
    print("  |---|---|---|---|---|---|---|")
    tot = 0
    for h in GAPS:
        code = corpus[TID][h]
        Sx, _ = S.wordset(TID, code, True)
        ch, m = M.chains_meta(corpus, TID, h, s0)
        ex = W.harvest_exprs(corpus, TID, h)
        e5 = E.bfs(code, Sx, ch, ex, 5)
        b4 = E.bfs(code, Sx, ch, ex, 4)
        v4 = max(v for (_c, v) in b4["level"].values())
        v5 = max(v for (_c, v) in e5["level"].values())
        tot += e5["n_gen"]
        print(f"  | {h[:8]} | {s0[(TID,h)]:.2f} | {v4:.2f} | **{v5:.2f}** | {len(e5['dist'])} |"
              f" {e5['n_gen']} | {'✅' if e5['hit_path'] else '❌'} |")
    print(f"\n  d≤5 全枚举生成量合计 {tot}")


if __name__ == "__main__":
    main()
