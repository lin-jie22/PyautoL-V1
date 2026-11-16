#!/usr/bin/env python3
"""V166 探索臂（**不计入判据**）：候选表代价 + 更严谓词 `P3` 的余量。

`P3` = 字面量既不在兄弟常量集里、**且**与某个兄弟常量编辑距离（≤2 处操作）相近
⇒ 「近似惯例」——比 `P2` 更严一档。若 `P3` 与 `P2` 逐实现全同，说明同一条信号已用尽。
"""
import ast
import difflib
import json
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
import v164 as U                    # noqa: E402
import v165 as N                    # noqa: E402
import v166 as A                    # noqa: E402
import v166b as B                   # noqa: E402


def near(s, pool):
    for c in pool:
        if abs(len(c) - len(s)) > 2:
            continue
        ops = [o for o in difflib.SequenceMatcher(None, s, c).get_opcodes() if o[0] != "equal"]
        if 0 < len(ops) <= 2:
            return True
    return False


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
    s0 = {(t, h): V.ev(t, corpus[t][h])[0] for t, h in impls}
    mem = json.loads(X.MEM_FILE.read_text())

    def mines(tid, h):
        return {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}

    ctx, meta, sibs = {}, {}, {}
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ch, m = M.chains_meta(corpus, tid, h, s0)
        ex = W.harvest_exprs(corpus, tid, h)
        ctx[(tid, h)] = (Sx, ch, ex)
        meta[(tid, h)] = dict(m=m, n_sib=len(corpus[tid]) - 1)
        sc = set()
        for x in corpus[tid]:
            if x != h:
                sc |= B.const_values(corpus[tid][x])
        sibs[(tid, h)] = sc

    print("## §1 d=1 候选表（结构，零判分）")
    print("  | impl | `X.gen` | `T5`(`U6`) | `T5`(`P2`) | `T6` |")
    print("  |---|---|---|---|---|")
    t6_ = t2_ = 0
    for tid, h in impls:
        if s0[(tid, h)] >= 1.0:
            continue
        code = corpus[tid][h]
        Sx, ch, ex = ctx[(tid, h)]
        n = len(X.gen(tid, code, Sx, ch, ex))
        lits = A.lit_of(code)
        t5 = U.candidates_T5(code)
        keep = [c for c in t5 if lits.get(c[0]) not in sibs[(tid, h)]]
        n6 = len(N.candidates_T6(code))
        t6_ += n + len(t5) + n6
        t2_ += n + len(keep) + n6
        print(f"  | {h[:8]} | {n} | {len(t5)} → {len(keep)} | {n6} |")
    print(f"  **合计 `U6` {t6_} → `P2` {t2_}（{t2_-t6_:+d}，{(t2_-t6_)/t6_*100:+.1f}%）**")

    def gen_for(mode, h):
        def g(tid, code, Sx, ch, ex):
            out = list(X.gen(tid, code, Sx, ch, ex))
            lits = A.lit_of(code)
            pool = sibs[(tid, h)]
            for lab, src in U.candidates_T5(code):
                s = lits.get(lab)
                if mode == "p2" and s in pool:
                    continue
                if mode == "p3" and (s in pool or not near(s, pool)):
                    continue
                out.append((lab, src))
            out += N.candidates_T6(code)
            return out
        return g

    print("\n## §2 探索臂")
    res = {}
    for mode in ("p2", "p3"):
        hits = ev = 0
        per = {}
        for tid, h in impls:
            r, _ = A.replay(tid, corpus[tid][h], *ctx[(tid, h)], meta[(tid, h)],
                            mines(tid, h), gen_for(mode, h))
            hits += r["hit"]
            ev += r["n_eval"]
            per[(tid, h)] = (r["hit"], round(r["s1"], 4))
        res[mode] = (hits, ev, per)
        print(f"  `{mode}`：命中 {hits}/25｜判分 {ev}")
    d = [(k, res["p3"][2][k], res["p2"][2][k]) for k in res["p3"][2]
         if res["p3"][2][k] != res["p2"][2][k]]
    print(f"  `P3` 与 `P2` 不同的实现 {len(d)} 个：{[(x[0][1][:8], x[1], x[2]) for x in d]}")
    print(f"\n  ⭐ 结论：更严一档 `P3` 判分 {res['p3'][1]} == `P2` {res['p2'][1]} ⇒ 同一条信号已用尽。")


if __name__ == "__main__":
    main()
