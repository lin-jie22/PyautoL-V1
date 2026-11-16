#!/usr/bin/env python3
"""V164：只加一个算子 `T5`（字面量里删一个字符），看能不能补上一个缺口。

`replay_gen` 是从 `v162.replay` **逐行复制**的，只把 `X.gen(...)` 换成一个参数；
C7 自校验（`genfn=X.gen` 时与 `v162.replay` 逐实现全同）钉死这份复制没过期。
`T5` 只活在本文件里：`mw/` 一行不动，交付默认值不变。
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
sys.path.insert(0, str(EV / "v6_s249"))
import v161 as M                    # noqa: E402
sys.path.insert(0, str(EV / "v6_s250"))
import v162 as R                    # noqa: E402

TID = "g7d3_fmt_amount"
GAPS = ["0a6acc93", "17747356", "5755e033", "8c8ac7f0"]
KEEP, ROUNDS = 2, 6
SKIP134 = frozenset({"T1", "T3", "T4"})

STATS = collections.Counter()


def _doc_ids(tree):
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = getattr(n, "body", None)
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                out.add(id(b[0].value))
    return out


def candidates_T5(code):
    """`T5:c{i}:del{k}`：第 i 个字符串常量的第 k 个字符删掉。0 外部输入。"""
    out = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        STATS["t5_badparse"] += 1
        return out
    doc = _doc_ids(tree)
    nodes = list(ast.walk(tree))
    for i, n in enumerate(nodes):
        if not (isinstance(n, ast.Constant) and isinstance(n.value, str)):
            continue
        if id(n) in doc:
            STATS["t5_docstring_skipped"] += 1
            continue
        s = n.value
        if not s:
            STATS["t5_empty_skipped"] += 1
            continue
        for k in range(len(s)):
            t = ast.parse(code)
            ns = list(ast.walk(t))
            ns[i].value = s[:k] + s[k + 1:]
            try:
                src = ast.unparse(ast.fix_missing_locations(t))
            except Exception:                        # noqa: BLE001
                STATS["t5_unparse_fail"] += 1
                continue
            if src == code:
                STATS["t5_noop"] += 1
                continue
            out.append((f"T5:c{i}:del{k}", src))
    STATS["t5_emitted"] += len(out)
    return out


def gen5(tid, code, Sx, ch, ex):
    return X.gen(tid, code, Sx, ch, ex) + candidates_T5(code)


def replay_gen(tid, code, Sx, ch, ex, meta, mine, genfn):
    """逐行复刻 `v162.replay(..., skip_dup=False, b_ubiq=True)`，只换生成器。"""
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, s1=s0, n_eval=0, hit=True, n_skip=0, trace={})
    n_sib = meta["n_sib"]
    frontier = [dict(code=code, s=s0)]
    visited, n, n_skip = {code}, 0, 0
    trace = {code: (None, None)}
    for _ in range(ROUNDS):
        pool, seen = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, srcc) for lab, srcc in
                     genfn(tid, node["code"], Sx, ch, ex)]
            for key, lab, srcc in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                if srcc in visited or srcc in seen:
                    continue
                kind = lab.split(":")[0]
                if kind in SKIP134:
                    n_skip += 1
                    continue
                if kind == "B":
                    npv, _best = meta["m"].get(lab.split(":", 2)[2], (0, 0.0))
                    if npv == n_sib:
                        n_skip += 1
                        continue
                sc, _ = V.ev(tid, srcc)
                n += 1
                if sc > node["s"]:
                    pool.append(dict(code=srcc, s=sc))
                    seen.add(srcc)
                    trace.setdefault(srcc, (node["code"], lab))
                if sc >= 1.0:
                    return dict(s0=s0, s1=sc, n_eval=n, hit=True, n_skip=n_skip, trace=trace)
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:KEEP]
        visited |= {d["code"] for d in pool}
    best = max(frontier, key=lambda d: d["s"])
    return dict(s0=s0, s1=best["s"], n_eval=n, hit=False, n_skip=n_skip, trace=trace)


def path_of(trace, code):
    labs, cur = [], code
    while cur is not None and trace.get(cur, (None, None))[0] is not None:
        par, lab = trace[cur]
        labs.append(lab)
        cur = par
    return list(reversed(labs))


def bfs5(code, Sx, ch, ex, D):
    """不剪枝/不早停/不筛 frontier 的 BFS，生成器 = `gen5`。"""
    s0 = V.ev(TID, code)[0]
    dist, parents, labs = {code: 0}, {code: None}, {code: None}
    frontier, level, hit_path = [code], {0: (code, s0)}, None
    for d in range(1, D + 1):
        nxt, best, bs = [], None, -1.0
        for c in frontier:
            for lab, src in gen5(TID, c, Sx, ch, ex):
                if src in dist:
                    continue
                sc = V.ev(TID, src)[0]
                dist[src], parents[src], labs[src] = d, c, lab
                nxt.append(src)
                if sc > bs:
                    best, bs = src, sc
                if sc >= 1.0 and hit_path is None:
                    p, cur = [], src
                    while cur is not None:
                        p.append(cur)
                        cur = parents[cur]
                    hit_path = (list(reversed(p)), [labs[x] for x in reversed(p)][1:])
        level[d] = (best, bs)
        frontier = nxt
    return dict(s0=s0, level=level, hit_path=hit_path, n_state=len(dist))


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
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        before = len(V.CACHE)
        ch, m = M.chains_meta(corpus, tid, h, s0)
        ev_meta += len(V.CACHE) - before
        ex = W.harvest_exprs(corpus, tid, h)
        ctx[(tid, h)] = (Sx, ch, ex)
        meta[(tid, h)] = dict(m=m, n_sib=len(corpus[tid]) - 1)

    def run(genfn):
        return {(t, h): replay_gen(t, corpus[t][h], *ctx[(t, h)], meta[(t, h)],
                                   mines(t, h), genfn) for t, h in impls}

    STATS.clear()
    U = run(X.gen)
    Ugen = STATS.copy()
    STATS.clear()
    U5 = run(gen5)
    STATS5 = STATS.copy()

    def summ(out):
        return dict(n_eval=sum(r["n_eval"] for r in out.values()),
                    hit=sum(1 for r in out.values() if r["hit"]),
                    mean_s1=sum(r["s1"] for r in out.values()) / len(out))

    SU, S5 = summ(U), summ(U5)

    print("## §1 主读数（25 个可解析实现）")
    print(f"  `U`  判分 {SU['n_eval']}｜命中 {SU['hit']}/25｜mean s1 {SU['mean_s1']:.4f}｜"
          f"标签期判分 {ev_meta}")
    print(f"  `U5` 判分 {S5['n_eval']}（{S5['n_eval']/max(SU['n_eval'],1):.2f}×）｜"
          f"命中 {S5['hit']}/25｜mean s1 {S5['mean_s1']:.4f}")
    print(f"  `T5` 生成 {STATS5['t5_emitted']}｜`src==code` {STATS5['t5_noop']}｜"
          f"unparse 失败 {STATS5['t5_unparse_fail']}｜坏解析 {STATS5['t5_badparse']}｜"
          f"跳过 docstring {STATS5['t5_docstring_skipped']}／空串 {STATS5['t5_empty_skipped']}")

    print("\n## §2 `g7d3` 逐实现（缺口在哪个实现上）")
    print("  | impl | s0 | `U` s1 | `U5` s1 | `U5` 命中 | 路径标签 |")
    print("  |---|---|---|---|---|---|")
    for h in GAPS:
        k = (TID, h)
        r = U5[k]
        labs = path_of(r["trace"], max(r["trace"], key=lambda c: V.ev(TID, c)[0])) if r["hit"] else []
        print(f"  | {h[:8]} | {s0[k]:.2f} | {U[k]['s1']:.2f} | {r['s1']:.2f} |"
              f" {'✅' if r['hit'] else '❌'} | {' → '.join(labs) if labs else '—'} |")

    print("\n## §3 逐实现对照（C3：不许退化）")
    worse = [(k, U[k]["hit"], round(U[k]["s1"], 6), U5[k]["hit"], round(U5[k]["s1"], 6))
             for k in U
             if (U[k]["hit"] and not U5[k]["hit"])
             or round(U5[k]["s1"], 6) < round(U[k]["s1"], 6)]
    gains = [(k, round(U[k]["s1"], 6), round(U5[k]["s1"], 6))
             for k in U if round(U5[k]["s1"], 6) > round(U[k]["s1"], 6)]
    newhit = [k for k in U if U5[k]["hit"] and not U[k]["hit"]]
    lost = [k for k in U if U[k]["hit"] and not U5[k]["hit"]]
    print(f"  退化 {len(worse)} 个：{[(w[0][0][:10], w[0][1][:6]) for w in worse]}")
    print(f"  丢失命中 {len(lost)} 个：{[(x[0][:10], x[1][:6]) for x in lost]}")
    print(f"  新增命中 {len(newhit)} 个：{[(x[0][:10], x[1][:6]) for x in newhit]}")
    print(f"  `s1` 抬高的实现 {len(gains)} 个：")
    for k, a, b in gains:
        labs = path_of(U5[k]["trace"], max(U5[k]["trace"], key=lambda c: V.ev(k[0], c)[0]))
        print(f"    {k[0][:12]}::{k[1][:6]}  {a:.2f} → {b:.2f} ｜ {' → '.join(labs)}")

    print("\n## §4 成本：d=1 候选表（逐实现，不只报总量）")
    print("  | impl | s0 | `U` 候选 | `U5` 候选 | 增量 | `T5` 占比 |")
    print("  |---|---|---|---|---|---|")
    tot_u = tot_5 = 0
    for tid, h in impls:
        if s0[(tid, h)] >= 1.0:
            continue
        Sx, ch, ex = ctx[(tid, h)]
        code = corpus[tid][h]
        a = len(X.gen(tid, code, Sx, ch, ex))
        t5 = len(candidates_T5(code))
        tot_u += a
        tot_5 += a + t5
        print(f"  | {h[:8]} | {s0[(tid, h)]:.2f} | {a} | {a + t5} | +{t5} |"
              f" {t5/(a+t5)*100:.0f}% |")
    print(f"  | **合计** | — | **{tot_u}** | **{tot_5}** | **+{tot_5-tot_u}** |"
          f" {(tot_5-tot_u)/tot_5*100:.0f}% |")

    print("\n## §5 C2：`8c8ac7f0` 在 `U5` 的 d≤3 全枚举下也不可达吗")
    k = (TID, "8c8ac7f0")
    b5 = bfs5(corpus[k[0]][k[1]], *ctx[k], 3)
    vb = max(v for _c, v in b5["level"].values())
    print(f"  状态 {b5['n_state']}｜层最优 {[round(b5['level'][d][1],2) for d in sorted(b5['level'])]}"
          f"｜最好 {vb:.2f}｜命中 {'✅' if b5['hit_path'] else '❌'}")

    print("\n## §6 判据（PASS/FAIL/NA）")
    rows = []

    MARKS = {"PASS": "✅", "FAIL": "⛔", "NA": "⬜"}

    def row(tag, st, msg):
        rows.append((tag, st, msg))
        print(f"  {tag} {MARKS[st]} [{st}]  {msg}")

    # C7 自校验：用 X.gen 的复制版必须与 v162 全同
    def ref(k):
        return R.replay(k[0], corpus[k[0]][k[1]], *ctx[k], meta[k], mines(*k),
                        skip_dup=False, b_ubiq=True)

    same = all((U[k]["hit"], round(U[k]["s1"], 6), U[k]["n_eval"])
               == (ref(k)["hit"], round(ref(k)["s1"], 6), ref(k)["n_eval"]) for k in U)

    g = {h: (TID, h) for h in GAPS}
    row("C1", "PASS" if any(U5[g[h]]["hit"] and not U[g[h]]["hit"] for h in GAPS) else "FAIL",
        f"新增命中 {len(newhit)} 个（押 ≥1）")
    row("C2", "PASS" if not U5[g["8c8ac7f0"]]["hit"] and not b5["hit_path"] else "FAIL",
        f"`8c8ac7f0`：贪心 {'✅' if U5[g['8c8ac7f0']]['hit'] else '❌'}、"
        f"d≤3 全枚举 {'✅' if b5['hit_path'] else '❌'}（押两个都 ❌）")
    row("C3", "PASS" if not lost else "FAIL", f"丢失命中 {len(lost)} 个（押 0）")
    onpath = [k for k in newhit if any(l.startswith("T5:") for l in path_of(U5[k]["trace"],
              max(U5[k]["trace"], key=lambda c: V.ev(k[0], c)[0])))]
    row("C4", "NA" if not newhit else ("PASS" if onpath else "FAIL"),
        f"新增命中里路径上真用了 `T5` 的 {len(onpath)} 个（押 ≥1）")
    row("C5", "PASS" if STATS5["t5_noop"] == 0 else "FAIL",
        f"`src==code` {STATS5['t5_noop']}（押 0）｜unparse 失败 {STATS5['t5_unparse_fail']}、"
        f"坏解析 {STATS5['t5_badparse']}（报实数，不写 0 除非数过）")
    row("C6", "PASS" if not U5[g["0a6acc93"]]["hit"] else "FAIL",
        f"`0a6acc93` `U5` 下 {'✅ 命中' if U5[g['0a6acc93']]['hit'] else '❌ 未命中'}（押 ❌）")
    row("C7", "PASS" if same else "FAIL", f"复制版 replay 与 `v162.replay` 逐实现全同：{same}（自校验）")

    np_ = sum(1 for r in rows if r[1] == "PASS")
    nf_ = sum(1 for r in rows if r[1] == "FAIL")
    print(f"\n  PASS {np_}｜FAIL {nf_}（C7 自校验不计结论：实质 "
          f"{sum(1 for r in rows if r[1]=='PASS' and r[0]!='C7')} 过 / "
          f"{sum(1 for r in rows if r[1]=='FAIL' and r[0]!='C7')} 破）")
    json.dump(dict(summary=dict(U=SU, U5=S5), t5=dict(STATS5), stats_gen=dict(Ugen),
                   per_impl={f"{k[0]}::{k[1]}": dict(s0=s0[k], u_s1=U[k]["s1"],
                             u_hit=U[k]["hit"], u5_s1=U5[k]["s1"], u5_hit=U5[k]["hit"],
                             u5_eval=U5[k]["n_eval"])
                             for k in U},
                   bfs8c8=dict(n_state=b5["n_state"], level={d: b5["level"][d][1]
                              for d in b5["level"]}, hit=bool(b5["hit_path"])),
                   criteria=[dict(id=r[0], status=r[1], msg=r[2]) for r in rows]),
              open(HERE / "readings_v164.json", "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()
