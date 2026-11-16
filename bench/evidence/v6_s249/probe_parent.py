#!/usr/bin/env python3
"""V161 前提验证（零新判分）：`n_prov == n_sib` 的 B 候选相对**父节点**是否也不改进。

若成立 ⇒ 它结构上进不了 pool ⇒ 进不了 frontier ⇒ 不可能是踏脚石（纪律 81 的整类移除前提）。
"""
import ast
import json
import pathlib
import sys
import collections

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198", "v6_s210", "v6_s236"):
    sys.path.insert(0, str(EV / d))
import idiom_transfer as I          # noqa: E402
import search as S                  # noqa: E402
import v119 as V                    # noqa: E402
import v120 as W                    # noqa: E402
import v121 as X                    # noqa: E402

ROUNDS, KEEP = 6, 2


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

    stat = collections.Counter()
    detail = []
    for tid, h in impls:
        if s0[(tid, h)] >= 1.0:
            continue
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        nsib = len(corpus[tid]) - 1
        # 重建带来源的 chains
        src = collections.defaultdict(set)
        for hh, cc in corpus[tid].items():
            if hh == h:
                continue
            try:
                tree = ast.parse(cc)
            except SyntaxError:
                continue
            for n in ast.walk(tree):
                if not isinstance(n, ast.Call):
                    continue
                seq, cur, ok = [], n, True
                while isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
                    if cur.func.attr in V.BAN:
                        ok = False
                        break
                    args = []
                    for a in cur.args:
                        if isinstance(a, ast.Constant):
                            args.append(a.value)
                        else:
                            ok = False
                            break
                    if not ok or cur.keywords:
                        ok = False
                        break
                    seq.append((cur.func.attr, tuple(args)))
                    cur = cur.func.value
                if ok and seq and len(seq) <= 3:
                    src[tuple(reversed(seq))].add(hh)
        ubq = {c for c, p in src.items() if len(p) == nsib}
        ch = sorted(src, key=lambda c: (len(c), str(c)))
        ex = W.harvest_exprs(corpus, tid, h)

        frontier = [dict(code=code, s=s0[(tid, h)])]
        visited = {code}
        for _ in range(ROUNDS):
            pool, seen = [], set()
            for node in frontier:
                for lab, srcc in X.gen(tid, node["code"], Sx, ch, ex):
                    if srcc in visited or srcc in seen:
                        continue
                    kind = lab.split(":")[0]
                    if kind == "B":
                        lab_c = lab.split(":", 2)[2]
                        is_ubq = any(V._label(c) == lab_c for c in ubq)
                    else:
                        is_ubq = False
                    s, _ = V.ev(tid, srcc)
                    if is_ubq:
                        stat["seen"] += 1
                        if s > node["s"]:
                            stat["imp_vs_parent"] += 1
                            detail.append((tid, h, lab, node["s"], s))
                        if s >= 1.0:
                            stat["hit"] += 1
                            detail.append((tid, h, lab, node["s"], s))
                        continue_write = False
                    if s >= 1.0:
                        break
                    if s > node["s"]:
                        pool.append(dict(code=srcc, s=s))
                        seen.add(srcc)
                else:
                    continue
                break
            else:
                pass
            if not pool:
                break
            pool.sort(key=lambda d: -d["s"])
            frontier = pool[:KEEP]
            visited |= {d["code"] for d in pool}

    print("## §0 `n_prov == n_sib` 的 B 候选在**真实搜索中**是否改进过")
    print(f"  出现过 {stat['seen']} 次（同一 chain 在多个父节点上重复出现）")
    print(f"  相对父节点改进 {stat['imp_vs_parent']} 次｜到 1.0 {stat['hit']} 次")
    print(f"  ⇒ 结构性结论：{'成立（0 次改进 ⇒ 进不了 pool ⇒ 不是踏脚石）' if stat['imp_vs_parent']==0 else '不成立'}")
    if detail:
        print("\n  反例（前 10）：")
        for d in detail[:10]:
            print(f"    {d[0][:12]}::{d[1][:8]}  {d[2]}  父 {d[3]} → {d[4]}")


if __name__ == "__main__":
    main()
