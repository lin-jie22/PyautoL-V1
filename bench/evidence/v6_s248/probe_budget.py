#!/usr/bin/env python3
"""V160 跑前结构（零判分）：判分预算的哪一部分**免费可知**。

三个量：
① 上界 `len(gen(baseline))` —— 生成候选表是纯 AST 变换，不看答案、不花判分；
② 实际花费（第 1 轮判分 / 总判分）；
③ 命中的**相对位置**（命中位置 / 上界）—— 决定「排序还能省多少」。
"""
import ast
import json
import pathlib
import statistics
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


def replay(tid, code, Sx, chains, exprs, mine):
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0:
        return None
    frontier = [dict(code=code, s=s0)]
    visited, n, by_round, hit_pos = {code}, 0, {}, None
    for rnd in range(1, ROUNDS + 1):
        pool, seen = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, src) for lab, src in X.gen(tid, node["code"], Sx, chains, exprs)]
            for key, _lab, src in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                if src in visited or src in seen:
                    continue
                s, _ = V.ev(tid, src)
                n += 1
                by_round[rnd] = by_round.get(rnd, 0) + 1
                if s >= 1.0:
                    return dict(s0=s0, hit=True, n=n, by_round=by_round, hit_pos=n,
                                ub=len(X.gen(tid, code, Sx, chains, exprs)), rounds=rnd)
                if s > node["s"]:
                    pool.append(dict(code=src, s=s))
                    seen.add(src)
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:2]
        visited |= {d["code"] for d in pool}
    return dict(s0=s0, hit=False, n=n, by_round=by_round, hit_pos=None,
                ub=len(X.gen(tid, code, Sx, chains, exprs)), rounds=max(by_round, default=0))


def static(code):
    tree = ast.parse(code)
    nodes = list(ast.walk(tree))
    st = S.sites(code)
    return dict(n_lit=sum(1 for n in nodes if isinstance(n, ast.Constant)),
                n_cmp=sum(1 for n in nodes if isinstance(n, ast.Compare)),
                n_call=sum(1 for n in nodes if isinstance(n, ast.Call)),
                n_if=sum(1 for n in nodes if isinstance(n, ast.If)),
                n_ret=sum(1 for n in nodes if isinstance(n, ast.Return)),
                ret_c=len(st["ret_c"]), in_cont=len(st["in_cont"]), loc=len(code.splitlines()))


def main() -> None:
    corpus = I.harvest()
    mem = json.loads(X.MEM_FILE.read_text())
    impls = []
    for tid in V.TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                continue
            impls.append((tid, h))
    rows = []
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ch, ex = V.harvest_chains(corpus, tid, h), W.harvest_exprs(corpus, tid, h)
        mine = {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}
        r = replay(tid, code, Sx, ch, ex, mine)
        if r is None:
            continue
        st = static(code)
        r1 = r["by_round"].get(1, 0)
        rows.append(dict(tid=tid, h=h, s0=r["s0"], n_S=len(Sx), ub=r["ub"], n=r["n"], r1=r1,
                         hit=r["hit"], hit_pos=r["hit_pos"],
                         rel=(r["hit_pos"] / r["ub"]) if r["hit_pos"] else None,
                         rounds=r["rounds"], **st))
    print("## §0 上界（免费）vs 实际花费")
    print("  | 实现 | |S| | 上界 | 第1轮 | 总 | 上界−第1轮 | 命中位置 | 相对位置 | 命中 |")
    print("  |---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda x: -x["ub"]):
        rel = "-" if r["rel"] is None else f"{r['rel']:.2f}"
        hp = "-" if r["hit_pos"] is None else str(r["hit_pos"])
        print(f"  | {r['tid'][:12]}::{r['h'][:8]} | {r['n_S']} | **{r['ub']}** | {r['r1']} |"
              f" {r['n']} | {r['r1']-r['ub']:+d} | {hp} | {rel} | {int(r['hit'])} |")
    print(f"\n  上界合计 = {sum(r['ub'] for r in rows)}｜总判分 = {sum(r['n'] for r in rows)}"
          f"｜第 1 轮 = {sum(r['r1'] for r in rows)}")
    print(f"  `第1轮 − 上界` 的分布 = {sorted({r['r1']-r['ub'] for r in rows})}")
    print(f"  第1轮 ≤ 上界（结构上必然）: {all(r['r1'] <= r['ub'] for r in rows)}")

    rels = sorted(r["rel"] for r in rows if r["rel"] is not None)
    print(f"\n## §1 命中的相对位置（命中位置 / 上界），n={len(rels)}")
    print(f"  min {rels[0]:.2f}｜p25 {rels[len(rels)//4]:.2f}｜中位 {rels[len(rels)//2]:.2f}"
          f"｜p75 {rels[3*len(rels)//4]:.2f}｜max {rels[-1]:.2f}")
    print(f"  命中时「已判掉的候选」中位占比 = {rels[len(rels)//2]*100:.0f}%"
          f"（相对位置 >1 表示用到了第 2 轮）")
    print(f"  相对位置 ≥0.5 的实现 = {sum(1 for x in rels if x >= 0.5)}/{len(rels)}"
          f"｜≥0.9 的 = {sum(1 for x in rels if x >= 0.9)}/{len(rels)}")

    print("\n## §2 免费特征（可用于「猜」上界的那些）")
    for key in ("n_S", "s0", "n_lit", "n_cmp", "n_call", "n_if", "ret_c", "in_cont", "loc"):
        xs = [r[key] for r in rows]
        ys = [r["ub"] for r in rows]
        print(f"  {key}: 范围 {min(xs)}~{max(xs)}｜中位 {statistics.median(xs)}"
              f"｜与上界的比值中位 {statistics.median([y/x for x, y in zip(xs, ys) if x]):.2f}")
    print("\n  逐题（题内 |S| 与上界）：")
    by = {}
    for r in rows:
        by.setdefault(r["tid"], []).append((r["n_S"], r["ub"]))
    for tid, v in by.items():
        print(f"    {tid}: |S|={sorted(x[0] for x in v)}｜上界={sorted(x[1] for x in v)}")
    json.dump(rows, open(HERE / "readings_budget_probe.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
