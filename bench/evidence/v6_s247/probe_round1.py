#!/usr/bin/env python3
"""V159 成本归因（探索性）：判分到底花在**哪里** —— 逐轮留痕。

V159 主脚本量到 `kept=3/4/6` 与 `kept=2` **逐实现零差异** ⇒ §226.5 的「稀释」归因被推翻。
本探针把每个实现的判分按「轮」拆开，回答：成本是第 1 轮（基线节点的候选广度）
还是后续轮（frontier 宽度）？另给候选数、池宽、命中位置。零新增判分。
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


def replay(tid, code, Sx, chains, exprs, mine, keep=2, use_mem=False):
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, s1=s0, hit=True, n_eval=0, by_round={}, pools=[], hit_parent=None,
                    hit_pos=None, hit_key=None, skip=0)
    frontier = [dict(code=code, s=s0, key="<baseline>")]
    visited, n, skip, pools, by_round = {code}, 0, 0, [], {}
    for rnd in range(1, ROUNDS + 1):
        pool, seen = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, src) for lab, src in X.gen(tid, node["code"], Sx, chains, exprs)]
            for key, _lab, src in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                if src in visited or src in seen:
                    continue
                if use_mem and key not in mine:
                    skip += 1
                    continue
                s, _ = V.ev(tid, src)
                n += 1
                by_round[rnd] = by_round.get(rnd, 0) + 1
                if s >= 1.0:
                    pools.append(len(pool))
                    return dict(s0=s0, s1=s, hit=True, n_eval=n, by_round=by_round,
                                pools=pools, hit_parent=(node["key"], round(node["s"], 2)),
                                hit_pos=n, hit_key=key, skip=skip)
                if s > node["s"]:
                    pool.append(dict(code=src, s=s, key=key))
                    seen.add(src)
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        pools.append(len(pool))
        frontier = pool[:keep]
        visited |= {d["code"] for d in pool}
    best = max(frontier, key=lambda d: d["s"])
    return dict(s0=s0, s1=best["s"], hit=False, n_eval=n, by_round=by_round, pools=pools,
                hit_parent=None, hit_pos=None, hit_key=None, skip=skip)


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

    rows = []
    for tid, h in impls:
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        ch, ex = V.harvest_chains(corpus, tid, h), W.harvest_exprs(corpus, tid, h)
        mi = mines(tid, h)
        r = replay(tid, corpus[tid][h], Sx, ch, ex, mi, keep=2)
        m = replay(tid, corpus[tid][h], Sx, ch, ex, mi, keep=2, use_mem=True)
        base_cands = len({src for _l, src in X.gen(tid, corpus[tid][h], Sx, ch, ex)})
        rows.append((tid, h, r, m, base_cands))

    print("## §0 逐实现的判分归因（kept=2，= 部署）")
    print("  | 实现 | 判分 | 第1轮 | 占比 | 基线候选 | 池宽 | 命中位置/父节点 | MEM 判分 |")
    print("  |---|---|---|---|---|---|---|---|")
    tot = r1 = 0
    for tid, h, r, m, bc in sorted(rows, key=lambda x: -x[2]["n_eval"]):
        if r["n_eval"] == 0:
            continue
        tot += r["n_eval"]
        first = r["by_round"].get(1, 0)
        r1 += first
        hp = "-" if not r["hit"] else f"{r['hit_pos']}/{r['hit_parent'][0]} s={r['hit_parent'][1]}"
        print(f"  | {tid[:12]}::{h[:8]} | {r['n_eval']} | {first} | {first/r['n_eval']*100:.0f}% |"
              f" {bc} | {r['pools']} | {hp} | {m['n_eval']} |")
    print(f"  **合计 {tot}｜第 1 轮 {r1}（{r1/tot*100:.1f}%）**")

    print("\n## §1 按轮拆开（前 6 个最贵的）")
    for tid, h, r, m, bc in sorted(rows, key=lambda x: -x[2]["n_eval"])[:6]:
        if r["n_eval"] == 0:
            continue
        print(f"  {tid}::{h} 总 {r['n_eval']}｜逐轮 {r['by_round']}｜池宽 {r['pools']}"
              f"｜MEM 逐轮 {m['by_round']}｜MEM 跳过 {m['skip']}｜MEM 命中 {m['hit']}")

    print("\n## §2 MEM 的省法：是「少判候选」还是「少判轮次」")
    d = [(tid, h, r, m) for tid, h, r, m, bc in rows if r["n_eval"] > 0]
    print(f"  15 个搜索实现的判分：BASE {sum(x[2]['n_eval'] for x in d)}"
          f"｜MEM {sum(x[3]['n_eval'] for x in d)}")
    print(f"  其中第 1 轮：BASE {sum(x[2]['by_round'].get(1,0) for x in d)}"
          f"｜MEM {sum(x[3]['by_round'].get(1,0) for x in d)}")
    print(f"  BASE 各实现「第 1 轮占比」中位 = "
          f"{sorted(x[2]['by_round'].get(1,0)/x[2]['n_eval'] for x in d)[len(d)//2]:.2f}")

    json.dump(dict(per_impl={f"{t}::{h}": dict(
        n=r["n_eval"], by_round={str(k): v for k, v in r["by_round"].items()},
        pools=r["pools"], hit=r["hit"], hit_pos=r["hit_pos"],
        hit_parent=r["hit_parent"], base_cands=bc,
        mem_n=m["n_eval"], mem_by_round={str(k): v for k, v in m["by_round"].items()},
        mem_skip=m["skip"], mem_hit=m["hit"])
        for t, h, r, m, bc in rows},
        total=tot, round1=r1), open(HERE / "readings_round1.json", "w"),
        ensure_ascii=False, indent=1)
    print("\n  已落盘 readings_round1.json")


if __name__ == "__main__":
    main()
