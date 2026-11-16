#!/usr/bin/env python3
"""V164 诊断：C6 被我押破，`0a6acc93` 的四步通路到底改了什么（逐跳 bitmap）。

预注册 §2 写死：C6 破 ⇒ 重查 bitmap，不许庆祝。本探针只做这一件事。
"""
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
import v164 as T                    # noqa: E402

TID = "g7d3_fmt_amount"


def main() -> None:
    corpus = I.harvest()
    impls = []
    for tid in V.TIDS:
        for h in sorted(corpus[tid]):
            try:
                __import__("ast").parse(corpus[tid][h])
            except SyntaxError:
                continue
            impls.append((tid, h))
    s0 = {(t, h): V.ev(t, corpus[t][h])[0] for t, h in impls}
    mem = __import__("json").loads(X.MEM_FILE.read_text())
    ctx, meta = {}, {}
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ch, m = M.chains_meta(corpus, tid, h, s0)
        ex = W.harvest_exprs(corpus, tid, h)
        ctx[(tid, h)] = (Sx, ch, ex)
        meta[(tid, h)] = dict(m=m, n_sib=len(corpus[tid]) - 1)

    def mines(tid, h):
        return {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}

    k = (TID, "0a6acc93")
    r = T.replay_gen(TID, corpus[k[0]][k[1]], *ctx[k], meta[k], mines(*k), T.gen5)
    print(f"## `{k[1]}`：s0={r['s0']:.2f} → s1={r['s1']:.2f}｜判分 {r['n_eval']}｜命中 {r['hit']}")
    best = max(r["trace"], key=lambda c: V.ev(TID, c)[0])
    path, cur = [], best
    while cur is not None:
        path.append(cur)
        cur = r["trace"].get(cur, (None, None))[0]
    path.reverse()
    labs = T.path_of(r["trace"], best)
    print(f"  路径 {len(path)-1} 跳：{' → '.join(labs)}")
    for i, c in enumerate(path):
        sc, bm = V.ev(TID, c)
        tag = labs[i - 1] if i else "（原码）"
        print(f"\n  [{i}] {tag}  s={sc:.2f}  bitmap={list(bm)}")
        for ln in c.strip().splitlines():
            print("      " + ln)
    print("\n## 逐跳：被删的是哪个字面量的第几个字符")
    for i, lab in enumerate(labs):
        if not lab.startswith("T5:"):
            continue
        site = lab.split(":")[1]
        ci = int(site[1:])
        kk = int(lab.split("del")[1])
        import ast
        ns = list(ast.walk(ast.parse(path[i])))
        old = ns[ci].value
        ns2 = list(ast.walk(ast.parse(path[i + 1])))
        new = ns2[ci].value
        print(f"  {lab}: {old!r} → {new!r}（删掉 {old[kk]!r}）")


if __name__ == "__main__":
    main()
