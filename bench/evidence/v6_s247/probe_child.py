#!/usr/bin/env python3
"""V159 机制探针（探索性）：129 次到底花在**哪个节点**上。

§226.5 的说法是「`kept=2` 把 `'on'` 挤出 frontier，所以走了 129 次」。
V159 主脚本量到 `K3` 与 `K2` **逐实现零差异** ⇒ 该说法必须当场核实：
① 逐次判分记下「父节点是谁、第几轮、第几次」；
② 强制只从 `'on'` 0.6 节点展开，看那条路到底通不通；
③ `MEM` 的 7 次是从哪个节点出来的。
零新增判分。
"""
import ast
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

ROUNDS = 6


def replay(tid, code, Sx, chains, exprs, mine, keep=2, use_mem=False, force=None):
    """逐次判分留痕：每行 (轮, 父节点分, 父节点键, 候选键, 分数, 是否命中)。"""
    s0, _ = V.ev(tid, code)
    lines = []
    if s0 >= 1.0:
        return dict(s0=s0, hit=True, n_eval=0, lines=[])
    frontier = [dict(code=code, s=s0, key=("<baseline>",))]
    visited, n = {code}, 0
    for rnd in range(1, ROUNDS + 1):
        pool, seen = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, src) for lab, src in X.gen(tid, node["code"], Sx, chains, exprs)]
            for key, _lab, src in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                if src in visited or src in seen:
                    continue
                if use_mem and key not in mine:
                    continue
                s, _ = V.ev(tid, src)
                n += 1
                lines.append((rnd, round(node["s"], 2), node["key"], key, round(s, 2), s >= 1.0))
                if s >= 1.0:
                    return dict(s0=s0, hit=True, n_eval=n, lines=lines)
                if s > node["s"]:
                    pool.append(dict(code=src, s=s, key=key))
                    seen.add(src)
        if not pool:
            return dict(s0=s0, hit=False, n_eval=n, lines=lines)
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:keep]
        visited |= {d["code"] for d in pool}
    return dict(s0=s0, hit=False, n_eval=n, lines=lines)


def main() -> None:
    corpus = I.harvest()
    mem = json.loads(X.MEM_FILE.read_text())

    def mines(tid, h):
        return {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}

    targets = [("g7e1_parse_flag", h) for h in sorted(corpus["g7e1_parse_flag"])]
    impls = []
    for tid in V.TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                continue
            impls.append((tid, h))
    tag = {k: i for i, k in enumerate(impls)}

    for tid, h in targets:
        if (tid, h) not in tag:
            continue
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        ch, ex = V.harvest_chains(corpus, tid, h), W.harvest_exprs(corpus, tid, h)
        mi = mines(tid, h)
        r2 = replay(tid, corpus[tid][h], Sx, ch, ex, mi, keep=2)
        if not r2["hit"] or r2["n_eval"] < 30:
            continue
        r3 = replay(tid, corpus[tid][h], Sx, ch, ex, mi, keep=3)
        rm = replay(tid, corpus[tid][h], Sx, ch, ex, mi, keep=2, use_mem=True)
        print(f"\n## {tid}::{h}")
        print(f"  s0={r2['s0']:.2f}｜K2 {r2['n_eval']} 次｜K3 {r3['n_eval']} 次｜MEM {rm['n_eval']} 次")
        print(f"  第 1 轮判分 {sum(1 for x in r2['lines'] if x[0]==1)} 次，"
              f"池里得到 {sorted({(x[3], x[4]) for x in r2['lines'] if x[0]==1 and x[4] > r2['s0']}, key=lambda y: -y[1])}")
        # 命中行
        hit = [x for x in r2["lines"] if x[5]][0]
        pos = r2["lines"].index(hit) + 1
        print(f"  ⭐ K2 命中在第 {pos} 次，来自**父节点 {hit[2]} s={hit[1]}**"
              f"（第 {hit[0]} 轮）｜命中候选键 {hit[3]}")
        print(f"     K2 第 2 轮逐节点：", end="")
        bypar = {}
        for x in r2["lines"]:
            if x[0] == 2:
                bypar.setdefault((x[2], x[1]), 0)
                bypar[(x[2], x[1])] += 1
        print("；".join(f"{k[0]} s={k[1]} → {v} 次" for k, v in bypar.items()))
        hitm = [x for x in rm["lines"] if x[5]]
        posm = rm["lines"].index(hitm[0]) + 1 if hitm else None
        if hitm:
            hitm = hitm[0]
            print(f"  ⭐ MEM 命中在第 {posm} 次，来自**父节点 {hitm[2]} s={hitm[1]}**"
                  f"（第 {hitm[0]} 轮）｜命中候选键 {hitm[3]}")
        else:
            print(f"  ⭐ MEM **没命中**（{rm['n_eval']} 次，skip 全部）")
        print(f"     MEM 第 1 轮判了 {sum(1 for x in rm['lines'] if x[0]==1)} 次："
              f"{[(x[3], x[4]) for x in rm['lines'] if x[0]==1][:8]}")
        print(f"     MEM 第 2 轮判了 {sum(1 for x in rm['lines'] if x[0]==2)} 次："
              f"{[(x[3], x[4]) for x in rm['lines'] if x[0]==2][:8]}")
        print(f"  MEM 的 mine = {sorted(mi)}")

        # ② 强制只从 'on' 0.6 节点展开
        s0 = r2["s0"]
        cands1 = []
        for lab, src in X.gen(tid, corpus[tid][h], Sx, ch, ex):
            s, _ = V.ev(tid, src)
            cands1.append((X.abstract(lab), round(s, 2)))
        onnode = [c for c in cands1 if c[0] == (("T2", "'on'"))]
        print(f"  ② 第 1 轮里 key=('T2',\"'on'\") 的候选：{onnode}")
        # 取那个 0.6 的源码，单独展开一轮
        for lab, src in X.gen(tid, corpus[tid][h], Sx, ch, ex):
            if X.abstract(lab) == (("T2", "'on'")) and abs(V.ev(tid, src)[0] - 0.6) < 1e-9:
                kids = [(X.abstract(l2), round(V.ev(tid, s2)[0], 2)) for l2, s2 in X.gen(tid, src, Sx, ch, ex)]
                print(f"     从 'on' 0.6 节点展开的候选（前 6）：{sorted(kids, key=lambda y: -y[1])[:6]}")
                print(f"     其中 ≥0.8 的：{[k for k in kids if k[1] >= 0.8]}")
                break


if __name__ == "__main__":
    main()
