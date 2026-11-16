#!/usr/bin/env python3
"""V161：候选表「类内」的免费结构信号。

臂：NO134 / U / Uonly / V / NV / NULL(200 种子)。
⛔ 不改交付路径；零新增 ev 实现；A8 用计数器证明 `n_prov` 标签不花判分。
"""
import ast
import collections
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

ROUNDS, KEEP, SEEDS = 6, 2, 200
EV_CALLS = collections.Counter()


def counted_ev(tid, code):
    EV_CALLS["n"] += 1
    return V.ev(tid, code)


def chains_meta(corpus, tid, exclude, s0):
    """chain -> (n_prov, best_s0)。纯 AST + 查表：**不调 ev**。"""
    src = collections.defaultdict(set)
    for h, code in corpus[tid].items():
        if h == exclude:
            continue
        try:
            tree = ast.parse(code)
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
                src[tuple(reversed(seq))].add(h)
    chains = sorted(src, key=lambda c: (len(c), str(c)))
    return chains, {V._label(c): (len(p), max(s0[(tid, x)] for x in p))
                    for c, p in src.items()}


def replay(tid, code, Sx, ch, ex, meta, mine, skip_cls=frozenset(),
           b_mode="none", rand_p=0.0, rnd=None):
    """v121.search 的同构重放。b_mode: none | ubiq | ver | unv。"""
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, s1=s0, n_eval=0, hit=True, n_skip=0, n_skip_b=0, ubq=0)
    n_sib = meta["n_sib"]
    frontier = [dict(code=code, s=s0)]
    visited, n, n_skip, n_skip_b, ubq_hit = {code}, 0, 0, 0, 0
    for _ in range(ROUNDS):
        pool, seen = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, srcc) for lab, srcc in
                     X.gen(tid, node["code"], Sx, ch, ex)]
            for key, lab, srcc in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                if srcc in visited or srcc in seen:
                    continue
                kind = lab.split(":")[0]
                if kind in skip_cls:
                    n_skip += 1
                    continue
                if kind == "B":
                    npv, best = meta["m"].get(lab.split(":", 2)[2], (0, 0.0))
                    if b_mode == "ubiq" and npv == n_sib:
                        n_skip += 1
                        n_skip_b += 1
                        ubq_hit += 1
                        continue
                    if b_mode == "ver" and best < 1.0:
                        n_skip += 1
                        n_skip_b += 1
                        continue
                    if b_mode == "unv" and best >= 1.0:
                        n_skip += 1
                        n_skip_b += 1
                        continue
                if rand_p and rnd is not None and rnd.random() < rand_p:
                    n_skip += 1
                    continue
                s, _ = counted_ev(tid, srcc)
                n += 1
                if s >= 1.0:
                    return dict(s0=s0, s1=s, n_eval=n, hit=True, n_skip=n_skip,
                                n_skip_b=n_skip_b, ubq=ubq_hit)
                if s > node["s"]:
                    pool.append(dict(code=srcc, s=s))
                    seen.add(srcc)
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:KEEP]
        visited |= {d["code"] for d in pool}
    best = max(frontier, key=lambda d: d["s"])
    return dict(s0=s0, s1=best["s"], n_eval=n, hit=False, n_skip=n_skip,
                n_skip_b=n_skip_b, ubq=ubq_hit)


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

    ctx, meta = {}, {}
    total_cand = 0
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        before_cache = len(V.CACHE)
        ch, m = chains_meta(corpus, tid, h, s0)
        EV_CALLS["meta"] += len(V.CACHE) - before_cache   # 直接看判分缓存有没有新增
        ex = W.harvest_exprs(corpus, tid, h)
        ctx[(tid, h)] = (Sx, ch, ex)
        meta[(tid, h)] = dict(m=m, n_sib=len(corpus[tid]) - 1)
        if s0[(tid, h)] < 1.0:
            total_cand += len(X.gen(tid, code, Sx, ch, ex))

    mem = json.loads(X.MEM_FILE.read_text())

    def mines(tid, h):
        return {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}

    def run(arm, rand_p=0.0, rnd=None):
        out = {}
        for tid, h in impls:
            Sx, ch, ex = ctx[(tid, h)]
            kw = dict(b_mode="none", rand_p=rand_p, rnd=rnd)
            if arm == "NO134":
                kw["skip_cls"] = frozenset({"T1", "T3", "T4"})
            elif arm == "U":
                kw["skip_cls"] = frozenset({"T1", "T3", "T4"})
                kw["b_mode"] = "ubiq"
            elif arm == "Uonly":
                kw["b_mode"] = "ubiq"
            elif arm == "V":
                kw["skip_cls"] = frozenset({"T1", "T3", "T4"})
                kw["b_mode"] = "ver"
            elif arm == "NV":
                kw["skip_cls"] = frozenset({"T1", "T3", "T4"})
                kw["b_mode"] = "unv"
            out[(tid, h)] = replay(tid, corpus[tid][h], Sx, ch, ex, meta[(tid, h)],
                                   mines(tid, h), **kw)
        return out

    arms = {a: run(a) for a in ("NO134", "U", "Uonly", "V", "NV")}

    def summ(out):
        return dict(n_eval=sum(r["n_eval"] for r in out.values()),
                    hit=sum(1 for r in out.values() if r["hit"]),
                    mean_s1=sum(r["s1"] for r in out.values()) / len(out),
                    n_skip_b=sum(r["n_skip_b"] for r in out.values()),
                    ubq=sum(r["ubq"] for r in out.values()))

    print("## §1 主读数（15 个搜索实现；另 10 个 s0=1.0 原样返回）")
    print("  | 臂 | 判分 | %NO134 | 到 1.0 | mean s1 | 跳过 B | 其中全量chain |")
    print("  |---|---|---|---|---|---|---|")
    S_ = {}
    for a in ("NO134", "U", "Uonly", "V", "NV"):
        S_[a] = summ(arms[a])
        s = S_[a]
        print(f"  | `{a}` | {s['n_eval']} | {s['n_eval']/S_['NO134']['n_eval']*100:.1f}% |"
              f" {s['hit']}/25 | {s['mean_s1']:.2f} | {s['n_skip_b']} | {s['ubq']} |")

    print("\n## §2 逐实现等价（纪律 82）")
    for a in ("U", "V", "NV"):
        diff = [(k, arms["NO134"][k]["hit"], round(arms["NO134"][k]["s1"], 6),
                 arms[a][k]["hit"], round(arms[a][k]["s1"], 6))
                for k in arms["NO134"]
                if (arms["NO134"][k]["hit"], round(arms["NO134"][k]["s1"], 6))
                != (arms[a][k]["hit"], round(arms[a][k]["s1"], 6))]
        print(f"  `{a}` vs `NO134`：不同 {len(diff)}/25")
        for d in diff:
            print(f"    {d[0][0][:12]}::{d[0][1][:8]}  hit {d[1]}→{d[3]}  s1 {d[2]}→{d[4]}")

    print("\n## §3 NULL：均匀随机跳同样比例（不是同构对照）")
    p = S_["U"]["n_eval"] and 50 / total_cand
    print(f"  `U` 跳掉的条数 / 候选总数 = 50/{total_cand} = {p*100:.2f}%")
    nh, ne = [], []
    for seed in range(SEEDS):
        rnd = random.Random(seed)
        out = {}
        for tid, h in impls:
            Sx, ch, ex = ctx[(tid, h)]
            out[(tid, h)] = replay(tid, corpus[tid][h], Sx, ch, ex, meta[(tid, h)],
                                   set(), skip_cls=frozenset({"T1", "T3", "T4"}),
                                   rand_p=p, rnd=rnd)
        nh.append(sum(1 for r in out.values() if r["hit"]))
        ne.append(sum(r["n_eval"] for r in out.values()))
    nh.sort()
    ne.sort()
    print(f"  命中：min {nh[0]}｜p5 {nh[int(.05*SEEDS)]}｜中位 {nh[SEEDS//2]}"
          f"｜p95 {nh[int(.95*SEEDS)-1]}｜max {nh[-1]}｜==21 的种子 {sum(1 for x in nh if x==21)}/{SEEDS}")
    print(f"  判分：中位 {ne[SEEDS//2]}｜min {ne[0]}｜max {ne[-1]}"
          f"（`U` 实读 {S_['U']['n_eval']}）")

    print("\n## §4 判据")
    rows = []

    def row(tag, ok, expect, msg):
        rows.append((tag, ok, expect, msg))
        print(f"  {tag} {'✅' if ok == expect else '⛔'}  {msg}")

    row("A1", S_["U"]["n_eval"] < S_["NO134"]["n_eval"], True,
        f"`U` 判分 {S_['U']['n_eval']} < `NO134` {S_['NO134']['n_eval']}")
    same_u = all((arms["NO134"][k]["hit"], round(arms["NO134"][k]["s1"], 6))
                 == (arms["U"][k]["hit"], round(arms["U"][k]["s1"], 6)) for k in arms["NO134"])
    row("A2", same_u, True, f"`U` 与 `NO134` 逐实现 `hit`/`s1` 全同：{same_u}")
    row("A3", S_["Uonly"]["n_eval"] < 588 and S_["Uonly"]["hit"] == 21, True,
        f"`Uonly` 判分 {S_['Uonly']['n_eval']} < 588 且 hit {S_['Uonly']['hit']} == 21")
    row("A4", S_["V"]["hit"] < 21, True, f"`V`（只留已验证）hit {S_['V']['hit']} < 21")
    row("A5", S_["NV"]["hit"] < 21, True, f"`NV`（只留未验证）hit {S_['NV']['hit']} < 21")
    row("A6", sum(1 for x in nh if x == 21) / SEEDS < 0.5, True,
        f"`NULL` 达 21 的种子 {sum(1 for x in nh if x==21)}/{SEEDS}")
    n_impl_aff = sum(1 for k in arms["U"] if arms["U"][k]["n_skip_b"] > 0)
    row("A7", n_impl_aff == 5, True, f"`U` 影响的实现数 {n_impl_aff} == 5")
    row("A8", EV_CALLS["meta"] == 0, True,
        f"`n_prov` 标签生成期间新增判分 {EV_CALLS['meta']} == 0（看 `V.CACHE` 增量）")

    print(f"\n  判据 {sum(1 for r in rows if r[1] == r[2])}/{len(rows)} 中")
    json.dump(dict(arms={a: {f"{k[0]}::{k[1]}": v for k, v in arms[a].items()} for a in arms},
                   summary=S_, null_hits=nh, null_evals=ne, total_cand=total_cand,
                   ev_meta=EV_CALLS["meta"], criteria=[{"id": r[0], "ok": r[1] == r[2], "msg": r[3]} for r in rows]),
              open(HERE / "readings_v161.json", "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()
