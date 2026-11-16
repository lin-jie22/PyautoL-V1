#!/usr/bin/env python3
"""V162：`T2` 的「插入已存在元素」是不是结构性死路。

臂：UV（V161 基线）/ UVD / D / NULL(200 种子)。
零新增 ev 实现；B4 在重放里记录 dup 候选相对父节点的改进。
"""
import ast
import collections
import functools
import json
import pathlib
import random
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
sys.path.insert(0, str(EV / "v6_s249"))
import v161 as M                    # noqa: E402

ROUNDS, KEEP, SEEDS = 6, 2, 200
SKIP134 = frozenset({"T1", "T3", "T4"})


@functools.lru_cache(maxsize=None)
def t2_dup(node_code, lab):
    """`T2:cont{i}.{j}:+{w!r}`：w 是否**已在该容器的字面量元素里**。不可判 ⇒ False（保守）。"""
    if not lab.startswith("T2:"):
        return False
    try:
        site = lab.split(":")[1]                     # cont3.1
        w = ast.literal_eval(lab.split(":+", 1)[1])
        i, j = (int(x) for x in site[4:].split("."))
        nodes = list(ast.walk(ast.parse(node_code)))
        c = nodes[i].comparators[j]
        vals = [ast.literal_eval(e) for e in c.elts]
    except Exception:                                # noqa: BLE001
        return False
    return w in vals


def replay(tid, code, Sx, ch, ex, meta, mine, skip_dup=False,
           b_ubiq=False, b_ver=False, rand_p=0.0, rnd=None):
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, s1=s0, n_eval=0, hit=True, n_skip=0, n_dup=0, dup_imp=0, dup_hit=0)
    n_sib = meta["n_sib"]
    frontier = [dict(code=code, s=s0)]
    visited, n, n_skip, n_dup, dup_imp, dup_hit = {code}, 0, 0, 0, 0, 0
    for _ in range(ROUNDS):
        pool, seen = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, srcc) for lab, srcc in
                     X.gen(tid, node["code"], Sx, ch, ex)]
            for key, lab, srcc in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                if srcc in visited or srcc in seen:
                    continue
                kind = lab.split(":")[0]
                if kind in SKIP134:
                    n_skip += 1
                    continue
                if kind == "B":
                    npv, best = meta["m"].get(lab.split(":", 2)[2], (0, 0.0))
                    if b_ubiq and npv == n_sib:
                        n_skip += 1
                        continue
                    if b_ver and best < 1.0:
                        n_skip += 1
                        continue
                if rand_p and rnd is not None and rnd.random() < rand_p:
                    n_skip += 1
                    continue
                is_dup = kind == "T2" and t2_dup(node["code"], lab)
                if skip_dup and is_dup:          # ⭐ 结构判定在判分**之前**
                    n_skip += 1
                    n_dup += 1
                    continue
                sc, _ = V.ev(tid, srcc)
                n += 1
                if is_dup:                        # 只在不跳的臂里走到这里（B4 用）
                    n_dup += 1
                    if sc > node["s"]:
                        dup_imp += 1
                    if sc >= 1.0:
                        dup_hit += 1
                if sc >= 1.0:
                    return dict(s0=s0, s1=sc, n_eval=n, hit=True, n_skip=n_skip,
                                n_dup=n_dup, dup_imp=dup_imp, dup_hit=dup_hit)
                if sc > node["s"]:
                    pool.append(dict(code=srcc, s=sc))
                    seen.add(srcc)
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:KEEP]
        visited |= {d["code"] for d in pool}
    best = max(frontier, key=lambda d: d["s"])
    return dict(s0=s0, s1=best["s"], n_eval=n, hit=False, n_skip=n_skip,
                n_dup=n_dup, dup_imp=dup_imp, dup_hit=dup_hit)


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

    ctx, meta = {}, {}
    ev_meta = 0
    total_cand = 0
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        before = len(V.CACHE)
        ch, m = M.chains_meta(corpus, tid, h, s0)   # 纯 AST + 查表，不调 ev
        ev_meta += len(V.CACHE) - before
        ex = W.harvest_exprs(corpus, tid, h)
        ctx[(tid, h)] = (Sx, ch, ex)
        meta[(tid, h)] = dict(m=m, n_sib=len(corpus[tid]) - 1)
        if s0[(tid, h)] < 1.0:
            total_cand += len(X.gen(tid, code, Sx, ch, ex))

    def run(skip_dup=False, b_ubiq=False, b_ver=False, rand_p=0.0, rnd=None):
        return {(tid, h): replay(tid, corpus[tid][h], *ctx[(tid, h)], meta[(tid, h)],
                                 mines(tid, h), skip_dup=skip_dup, b_ubiq=b_ubiq,
                                 b_ver=b_ver, rand_p=rand_p, rnd=rnd)
                for tid, h in impls}

    arms = {
        "U":    run(skip_dup=False, b_ubiq=True),
        "UV":   run(skip_dup=False, b_ubiq=True, b_ver=True),
        "UVD":  run(skip_dup=True,  b_ubiq=True, b_ver=True),
        "UD":   run(skip_dup=True,  b_ubiq=True),
        "D":    run(skip_dup=True,  b_ubiq=False),
    }

    def summ(out):
        return dict(n_eval=sum(r["n_eval"] for r in out.values()),
                    hit=sum(1 for r in out.values() if r["hit"]),
                    mean_s1=sum(r["s1"] for r in out.values()) / len(out),
                    n_dup=sum(r["n_dup"] for r in out.values()),
                    dup_imp=sum(r["dup_imp"] for r in out.values()),
                    dup_hit=sum(r["dup_hit"] for r in out.values()),
                    n_skip=sum(r["n_skip"] for r in out.values()))

    print("## §1 主读数")
    print("  | 臂 | 判分 | %UV | 到 1.0 | mean s1 | 见过的 dup | dup 改进 | dup 命中 |")
    print("  |---|---|---|---|---|---|---|---|")
    S_ = {a: summ(arms[a]) for a in ("U", "UV", "UVD", "UD", "D")}
    for a in ("U", "UV", "UVD", "UD", "D"):
        s = S_[a]
        print(f"  | `{a}` | {s['n_eval']} | {s['n_eval']/S_['UV']['n_eval']*100:.1f}% |"
              f" {s['hit']}/25 | {s['mean_s1']:.2f} | {s['n_dup']} | {s['dup_imp']} | {s['dup_hit']} |")

    print("\n## §2 逐实现等价（纪律 82）")
    for a in ("UVD", "UD", "D"):
        ref = "UV" if a == "UVD" else "U"
        diff = [(k, arms[ref][k]["hit"], round(arms[ref][k]["s1"], 6),
                 arms[a][k]["hit"], round(arms[a][k]["s1"], 6))
                for k in arms[ref]
                if (arms[ref][k]["hit"], round(arms[ref][k]["s1"], 6))
                != (arms[a][k]["hit"], round(arms[a][k]["s1"], 6))]
        print(f"  `{a}` vs `{ref}`：不同 {len(diff)}/25")
        for d in diff:
            print(f"    {d[0][0][:12]}::{d[0][1][:8]}  hit {d[1]}→{d[3]}  s1 {d[2]}→{d[4]}")

    print("\n## §3 B4：dup 候选相对**父节点**的改进（在 `UV` 臂里）")
    uv = arms["UV"]
    print(f"  见过的 dup 候选 {S_['UV']['n_dup']} 次｜相对父节点改进 {S_['UV']['dup_imp']} 次"
          f"｜到 1.0 {S_['UV']['dup_hit']} 次")
    print(f"  ⇒ {'成立（0 改进 ⇒ 进不了 pool）' if S_['UV']['dup_imp'] == 0 else '不成立'}")

    print("\n## §4 B7：结构条数 vs 实跳")
    print("  | 实现 | dup 结构条数 | dup 实跳（`UVD`）|")
    print("  |---|---|---|")
    n_struct = 0
    for tid, h in impls:
        if s0[(tid, h)] >= 1.0:
            continue
        code = corpus[tid][h]
        Sx, ch, ex = ctx[(tid, h)]
        cnt = sum(1 for lab, _ in X.gen(tid, code, Sx, ch, ex)
                  if lab.startswith("T2:") and t2_dup(code, lab))
        if cnt:
            n_struct += 1
            print(f"  | {tid[:10]}::{h[:6]} | {cnt} | {arms['UVD'][(tid, h)]['n_dup']} |")
    print(f"  有 dup 候选的实现 = {n_struct} 个")

    print("\n## §5 NULL：均匀随机跳 41 条（不是同构对照）")
    p = 41 / total_cand
    print(f"  41/{total_cand} = {p*100:.2f}%")
    nh, ne = [], []
    for seed in range(SEEDS):
        rnd = random.Random(seed)
        out = run(skip_dup=False, b_ubiq=True, b_ver=True, rand_p=p, rnd=rnd)
        nh.append(sum(1 for r in out.values() if r["hit"]))
        ne.append(sum(r["n_eval"] for r in out.values()))
    nh.sort()
    ne.sort()
    print(f"  命中：min {nh[0]}｜中位 {nh[SEEDS//2]}｜max {nh[-1]}｜==21 的种子 {sum(1 for x in nh if x==21)}/{SEEDS}")
    print(f"  判分：min {ne[0]}｜中位 {ne[SEEDS//2]}｜max {ne[-1]}（`UVD` 实读 {S_['UVD']['n_eval']}）")

    print("\n## §6 判据")
    rows = []

    def row(tag, ok, expect, msg):
        rows.append((tag, ok, expect, msg))
        print(f"  {tag} {'✅' if ok == expect else '⛔'}  {msg}")

    row("B1", S_["UVD"]["n_eval"] < S_["UV"]["n_eval"], True,
        f"`UVD` 判分 {S_['UVD']['n_eval']} < `UV` {S_['UV']['n_eval']}")
    same = all((arms["UV"][k]["hit"], round(arms["UV"][k]["s1"], 6))
               == (arms["UVD"][k]["hit"], round(arms["UVD"][k]["s1"], 6)) for k in arms["UV"])
    row("B2", same, True, f"`UVD` 与 `UV` 逐实现 `hit`/`s1` 全同：{same}")
    row("B3", S_["D"]["n_eval"] < 260 and S_["D"]["hit"] == 21, True,
        f"`D` 判分 {S_['D']['n_eval']} < 260 且 hit {S_['D']['hit']} == 21")
    row("B4", S_["UV"]["dup_imp"] == 0, True,
        f"dup 相对父节点改进 {S_['UV']['dup_imp']} == 0（出现过 {S_['UV']['n_dup']} 次）")
    row("B5", nh[SEEDS // 2] < 21, True, f"`NULL` 命中中位 {nh[SEEDS//2]} < 21")
    row("B6", ne[SEEDS // 2] > S_["UVD"]["n_eval"], True,
        f"`NULL` 判分中位 {ne[SEEDS//2]} > `UVD` 的 {S_['UVD']['n_eval']}")
    row("B7", n_struct == 5, True, f"有 dup 结构条数的实现 {n_struct} == 5")
    row("B8", ev_meta == 0, True, f"标签生成期新增判分 {ev_meta} == 0")

    print(f"\n  判据 {sum(1 for r in rows if r[1] == r[2])}/{len(rows)} 中")
    json.dump(dict(summary=S_, null_hits=nh, null_evals=ne, total_cand=total_cand,
                   ev_meta=ev_meta, n_struct=n_struct,
                   arms={a: {f"{k[0]}::{k[1]}": v for k, v in arms[a].items()} for a in arms},
                   criteria=[{"id": r[0], "ok": r[1] == r[2], "msg": r[3]} for r in rows]),
              open(HERE / "readings_v162.json", "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()
