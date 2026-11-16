#!/usr/bin/env python3
"""V159 跑前结构探针（零新增判分，先于判据；纪律 51/52）。

只量三件与「选择规则」有关、且不需要新分数的事：
① BASE（kept=2）的逐轮池宽与**被挤掉**的节点数 —— 稀释到底发生在多少个实现里；
② 判分在 25 个实现上的集中度（一个实现占 588 的多少）；
③ MEM 下的池宽 —— 若池宽恒 ≤2，则 `kept` 在 MEM 下根本不生效（= 不可叠加）。
"""
import ast
import collections
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


def trace(tid, code, Sx, chains, exprs, mine, keep, use_mem=False):
    """返回逐轮池宽、被挤掉的节点数、判分、命中。⛔ 不改排序（记忆键优先照旧）。"""
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, s1=s0, n_eval=0, hit=True, pools=[], evicted=[], keys=[])
    frontier = [dict(code=code, s=s0)]
    visited, n, pools, evicted = {code}, 0, [], []
    for _ in range(ROUNDS):
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
                if s >= 1.0:
                    pools.append(len(pool))
                    evicted.append(max(0, len(pool) - keep))
                    return dict(s0=s0, s1=s, n_eval=n, hit=True, pools=pools, evicted=evicted, keys=[])
                if s > node["s"]:
                    pool.append(dict(code=src, s=s, key=key))
                    seen.add(src)
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        pools.append(len(pool))
        evicted.append(max(0, len(pool) - keep))
        frontier = pool[:keep]
        visited |= {d["code"] for d in pool}
    best = max(frontier, key=lambda d: d["s"])
    return dict(s0=s0, s1=best["s"], n_eval=n, hit=False, pools=pools, evicted=evicted, keys=[])


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
    mem = json.loads(X.MEM_FILE.read_text())

    def mines(tid, h):
        return {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}

    ctx = {}
    for tid, h in impls:
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        ctx[(tid, h)] = (Sx, V.harvest_chains(corpus, tid, h), W.harvest_exprs(corpus, tid, h))

    rows = {}
    for tid, h in impls:
        Sx, ch, ex = ctx[(tid, h)]
        rows[(tid, h)] = trace(tid, corpus[tid][h], Sx, ch, ex, mines(tid, h), 2)
    mem_rows = {}
    for tid, h in impls:
        Sx, ch, ex = ctx[(tid, h)]
        mem_rows[(tid, h)] = trace(tid, corpus[tid][h], Sx, ch, ex, mines(tid, h), 2, use_mem=True)

    tot = sum(r["n_eval"] for r in rows.values())
    print("## §0 集中度（BASE, kept=2）")
    print(f"  总判分 = {tot}（记 588）")
    order = sorted(rows.items(), key=lambda kv: -kv[1]["n_eval"])
    top = order[0]
    print(f"  最大实现 = {top[0][0]}::{top[0][1]} → {top[1]['n_eval']} 次"
          f"（{top[1]['n_eval']/tot*100:.1f}%）")
    for (tid, h), r in order[:6]:
        print(f"    {tid}::{h} n={r['n_eval']:3d} hit={r['hit']} s1={r['s1']:.2f}"
              f" pools={r['pools']} evicted={sum(r['evicted'])}")

    print("\n## §1 池宽分布（BASE）")
    widths = [w for r in rows.values() for w in r["pools"]]
    print(f"  逐轮池宽（{len(widths)} 轮）：{dict(sorted(collections.Counter(widths).items()))}")
    n_ev = sum(1 for r in rows.values() if sum(r["evicted"]) > 0)
    print(f"  有过挤掉的实现 = {n_ev}/25（其中真的搜索的 = "
          f"{sum(1 for r in rows.values() if r['n_eval'] > 0)}）")
    print(f"  被挤掉的节点总数 = {sum(sum(r['evicted']) for r in rows.values())}"
          f"（kept=3 时要多展开的节点数下界）")
    for (tid, h), r in rows.items():
        if sum(r["evicted"]) > 0 and (tid, h) != top[0]:
            print(f"    {tid}::{h} n={r['n_eval']} hit={r['hit']} pools={r['pools']}"
                  f" evicted={sum(r['evicted'])}")

    print("\n## §2 MEM 下的池宽（kept 是否生效）")
    mw = [w for r in mem_rows.values() for w in r["pools"]]
    print(f"  逐轮池宽（{len(mw)} 轮）：{dict(sorted(collections.Counter(mw).items()))}")
    print(f"  池宽 > 2 的轮数 = {sum(1 for w in mw if w > 2)}"
          f"｜被挤掉节点 = {sum(sum(r['evicted']) for r in mem_rows.values())}")
    print(f"  MEM 总判分 = {sum(r['n_eval'] for r in mem_rows.values())}（记 42）"
          f"｜命中 {sum(1 for r in mem_rows.values() if r['hit'])}（记 19）")

    print("\n## §3 阶梯实现 a579b07a 的逐轮细节（BASE vs MEM）")
    for (tid, h), r in rows.items():
        if h.startswith("a579b07a"):
            print(f"  BASE: n={r['n_eval']} hit={r['hit']} s1={r['s1']:.2f}"
                  f" pools={r['pools']} evicted={r['evicted']}")
            m = mem_rows[(tid, h)]
            print(f"  MEM : n={m['n_eval']} hit={m['hit']} s1={m['s1']:.2f}"
                  f" pools={m['pools']} evicted={m['evicted']}")

    json.dump(dict(
        total=tot,
        per_impl={f"{t}::{h}": dict(n=r["n_eval"], hit=r["hit"], s1=r["s1"],
                                    pools=r["pools"], evicted=r["evicted"],
                                    mem_n=mem_rows[(t, h)]["n_eval"],
                                    mem_hit=mem_rows[(t, h)]["hit"],
                                    mem_pools=mem_rows[(t, h)]["pools"])
                  for (t, h), r in rows.items()},
        width_dist=dict(sorted(collections.Counter(widths).items())),
        mem_width_dist=dict(sorted(collections.Counter(mw).items())),
        cache_len=len(V.CACHE),
    ), open(HERE / "readings_keep_probe.json", "w"), ensure_ascii=False, indent=1)
    print(f"\n  CACHE 条目 = {len(V.CACHE)}（纪律 73：构造过的 ≠ 看过的）")


if __name__ == "__main__":
    main()
