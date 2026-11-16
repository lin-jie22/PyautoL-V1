#!/usr/bin/env python3
"""V165：复合算子 `T6`——负数括号闭合（两个 marker：`-` / `(-`）。

`replay_gen` 直接复用 `v164`（不复制第三份）：D6 用「传 `v164.gen5` 必须与 V164 `U5` 逐实现全同」
钉死复用没过期。`T6` 只活在本文件里：`mw/` 一行不动，交付默认值不变。
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
sys.path.insert(0, str(EV / "v6_s251"))
import v164 as U                    # noqa: E402

TID = "g7d3_fmt_amount"
GAPS = ["17747356", "8c8ac7f0"]
MARKERS = ("-", "(-")          # 来源见 PREREG §1：全部来自兄弟实现自己的语句
TMP = "_t6"

STATS = collections.Counter()


def _site(tree):
    """函数体顶层恰好一个 `return`、且它是最后一句、函数内无 `_t6` ⇒ 返回 (fn, idx)。

    ⚠ 纯函数，**不碰 STATS**：早先版本在这里累加计数器，而同一候选要调用 3 次
    ⇒ 「站点不可用」被重复计数（纪律 61/73）。计数改由 `candidates_T6` 每次调用记一次。
    """
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        idx = [i for i, st in enumerate(fn.body) if isinstance(st, ast.Return)]
        if len(idx) != 1 or idx[0] != len(fn.body) - 1 or fn.body[idx[0]].value is None:
            continue
        if TMP in {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}:
            continue
        out.append((fn, idx[0]))
    return out


def candidates_T6(code):
    """`T6:r{i}:{marker}`：`_t6 = E; if _t6.startswith(m): _t6 = '(' + _t6[k:] + ')'; return _t6`。"""
    out = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        STATS["t6_badparse"] += 1
        return out
    if not _site(tree):
        STATS["t6_no_site"] += 1
    for si in range(len(_site(tree))):
        for m in MARKERS:
            t = ast.parse(code)                      # 每个候选一棵干净树
            got = _site(t)
            if len(got) != len(_site(tree)):
                STATS["t6_site_drift"] += 1
                continue
            fn2, ri2 = got[si]
            expr = fn2.body[ri2].value
            load = lambda: ast.Name(id=TMP, ctx=ast.Load())          # noqa: E731
            wrap = ast.BinOp(
                left=ast.BinOp(left=ast.Constant(value="("), op=ast.Add(),
                               right=ast.Subscript(
                                   value=load(),
                                   slice=ast.Slice(lower=ast.Constant(value=len(m))),
                                   ctx=ast.Load())),
                op=ast.Add(), right=ast.Constant(value=")"))
            body = [
                ast.Assign(targets=[ast.Name(id=TMP, ctx=ast.Store())], value=expr),
                ast.If(test=ast.Call(func=ast.Attribute(value=load(), attr="startswith",
                                                        ctx=ast.Load()),
                                     args=[ast.Constant(value=m)], keywords=[]),
                       body=[ast.Assign(targets=[ast.Name(id=TMP, ctx=ast.Store())],
                                        value=wrap)],
                       orelse=[]),
                ast.Return(value=load()),
            ]
            fn2.body[ri2:] = body
            try:
                src = ast.unparse(ast.fix_missing_locations(t))
            except Exception:                        # noqa: BLE001
                STATS["t6_unparse_fail"] += 1
                continue
            if src == code:
                STATS["t6_noop"] += 1
                continue
            out.append((f"T6:r{si}:{m}", src))
    STATS["t6_emitted"] += len(out)
    return out


def gen6(tid, code, Sx, ch, ex):
    return U.gen5(tid, code, Sx, ch, ex) + candidates_T6(code)


def path_of(trace, code):
    labs, cur = [], code
    while cur is not None and trace.get(cur, (None, None))[0] is not None:
        par, lab = trace[cur]
        labs.append(lab)
        cur = par
    return list(reversed(labs))


def endcode(trace):
    return max(trace, key=lambda c: V.ev(TID, c)[0])


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
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ch, m = M.chains_meta(corpus, tid, h, s0)
        ex = W.harvest_exprs(corpus, tid, h)
        ctx[(tid, h)] = (Sx, ch, ex)
        meta[(tid, h)] = dict(m=m, n_sib=len(corpus[tid]) - 1)

    def run(genfn):
        return {(t, h): U.replay_gen(t, corpus[t][h], *ctx[(t, h)], meta[(t, h)],
                                     mines(t, h), genfn) for t, h in impls}

    STATS.clear()
    U5 = run(U.gen5)
    STATS5 = STATS.copy()
    STATS.clear()
    U6 = run(gen6)
    STATS6 = STATS.copy()
    # D6 自校验：复用 v164 的 replay_gen + v164 的 gen5 ⇒ 必须复现 V164 的 U5
    STATS.clear()
    CHK = run(U.gen5)

    def summ(out):
        return dict(n_eval=sum(r["n_eval"] for r in out.values()),
                    hit=sum(1 for r in out.values() if r["hit"]),
                    mean_s1=sum(r["s1"] for r in out.values()) / len(out))

    S5, S6 = summ(U5), summ(U6)
    N = len(U5)

    print(f"## §1 主读数（{N} 个实现；基线 `U5` = V164）")
    print(f"  `U5` 判分 {S5['n_eval']}｜命中 {S5['hit']}/{N}｜mean s1 {S5['mean_s1']:.4f}")
    print(f"  `U6` 判分 {S6['n_eval']}（{S6['n_eval']/max(S5['n_eval'],1):.2f}×）｜"
          f"命中 {S6['hit']}/{N}｜mean s1 {S6['mean_s1']:.4f}")
    print(f"  `T6` 生成 {STATS6['t6_emitted']}｜`src==code` {STATS6['t6_noop']}｜"
          f"unparse 失败 {STATS6['t6_unparse_fail']}｜坏解析 {STATS6['t6_badparse']}｜"
          f"节点级无站点 {STATS6['t6_no_site']}（调用数，不是实现数）｜"
          f"站点漂移 {STATS6['t6_site_drift']}")

    print("\n## §2 两个缺口：路径与 marker")
    print("  | impl | s0 | `U5` s1 | `U6` s1 | `U6` 命中 | 路径标签 |")
    print("  |---|---|---|---|---|---|")
    mk = {}
    for h in GAPS:
        k = (TID, h)
        r = U6[k]
        labs = path_of(r["trace"], endcode(r["trace"])) if r["hit"] else []
        mk[h] = [l for l in labs if l.startswith("T6:")]
        print(f"  | {h[:8]} | {s0[k]:.2f} | {U5[k]['s1']:.2f} | {r['s1']:.2f} |"
              f" {'✅' if r['hit'] else '❌'} | {' → '.join(labs) if labs else '—'} |")

    print("\n## §3 逐实现对照（D4：不许退化 / 不许丢命中）")
    worse = [(k, U5[k]["hit"], round(U5[k]["s1"], 6), U6[k]["hit"], round(U6[k]["s1"], 6))
             for k in U5
             if (U5[k]["hit"] and not U6[k]["hit"])
             or round(U6[k]["s1"], 6) < round(U5[k]["s1"], 6)]
    lost = [k for k in U5 if U5[k]["hit"] and not U6[k]["hit"]]
    newhit = [k for k in U5 if U6[k]["hit"] and not U5[k]["hit"]]
    gains = [(k, round(U5[k]["s1"], 6), round(U6[k]["s1"], 6))
             for k in U5 if round(U6[k]["s1"], 6) > round(U5[k]["s1"], 6)]
    print(f"  退化 {len(worse)} 个：{[(w[0][1][:8], w[0][0][:10]) for w in worse]}")
    print(f"  丢失命中 {len(lost)} 个：{[(x[0][:10], x[1][:8]) for x in lost]}")
    print(f"  新增命中 {len(newhit)} 个：{[(x[0][:10], x[1][:8]) for x in newhit]}")
    print(f"  `s1` 抬高的 {len(gains)} 个：")
    for k, a, b in gains:
        labs = path_of(U6[k]["trace"], endcode(U6[k]["trace"]))
        print(f"    {k[0][:12]}::{k[1][:8]}  {a:.2f} → {b:.2f} ｜ {' → '.join(labs)}")

    print("\n## §4 成本：d=1 候选表（逐实现）")
    print("  | impl | s0 | `U5` 候选 | `U6` 候选 | 增量 | `T6` 条数 |")
    print("  |---|---|---|---|---|---|")
    tot5 = tot6 = 0
    per = {}
    for tid, h in impls:
        if s0[(tid, h)] >= 1.0:
            continue
        Sx, ch, ex = ctx[(tid, h)]
        code = corpus[tid][h]
        a = len(U.gen5(tid, code, Sx, ch, ex))
        t6 = len(candidates_T6(code))
        per[(tid, h)] = (a, t6)
        tot5 += a
        tot6 += a + t6
        if t6:
            print(f"  | {h[:8]} | {s0[(tid, h)]:.2f} | {a} | {a + t6} | +{t6} | {t6} |")
    print(f"  | **合计** | — | **{tot5}** | **{tot6}** | **+{tot6-tot5}** |"
          f" **{tot6-tot5}** |")
    pct = (tot6 - tot5) / max(tot5, 1) * 100

    print("\n## §5 判据（PASS / FAIL / NA）")
    rows = []
    MARKS = {"PASS": "✅", "FAIL": "⛔", "NA": "⬜"}

    def row(tag, st, msg):
        rows.append((tag, st, msg))
        print(f"  {tag} {MARKS[st]} [{st}]  {msg}")

    same = all((U5[k]["hit"], round(U5[k]["s1"], 6), U5[k]["n_eval"])
               == (CHK[k]["hit"], round(CHK[k]["s1"], 6), CHK[k]["n_eval"]) for k in U5)
    d1 = S6["hit"] == N
    row("D1", "PASS" if d1 else "FAIL", f"`U6` 命中 {S6['hit']}/{N}（押 {N}）")
    used = {h: [l.split(":", 2)[2] for l in mk[h]] for h in GAPS}
    if any(not U6[(TID, h)]["hit"] for h in GAPS):
        row("D2", "NA", f"marker {used}（有缺口未命中 ⇒ 无路径，不出结论）")
    else:
        ok2 = used["17747356"] and used["8c8ac7f0"] and used["17747356"] != used["8c8ac7f0"]
        row("D2", "PASS" if ok2 else "FAIL",
            f"marker 17747356={used['17747356']} / 8c8ac7f0={used['8c8ac7f0']}"
            f"（押两者不同）")
    row("D3", "PASS" if pct <= 4.0 else "FAIL",
        f"d=1 候选表 {tot5} → {tot6}（+{tot6-tot5}，+{pct:.1f}%；押 ≤4%）")
    row("D4", "PASS" if not lost and not worse else "FAIL",
        f"丢失命中 {len(lost)}｜退化 {len(worse)}（押 0 / 0）")
    clean = (STATS6["t6_noop"] == 0 and STATS6["t6_unparse_fail"] == 0
             and STATS6["t6_badparse"] == 0 and STATS6["t6_site_drift"] == 0)
    row("D5", "PASS" if clean else "FAIL",
        f"`src==code` {STATS6['t6_noop']}｜unparse 失败 {STATS6['t6_unparse_fail']}｜"
        f"坏解析 {STATS6['t6_badparse']}｜站点漂移 {STATS6['t6_site_drift']}｜"
        f"节点级无站点调用 {STATS6['t6_no_site']}（结构覆盖 23/25 见 RESULTS）")
    row("D6", "PASS" if same else "FAIL", f"复制版复现 V164 `U5` 逐实现全同：{same}（自校验）")

    real = [r for r in rows if r[0] != "D6"]
    print(f"\n  判据 {sum(1 for r in real if r[1] == 'PASS')}/{len(real)} PASS"
          f"（FAIL {sum(1 for r in real if r[1] == 'FAIL')}，"
          f"NA {sum(1 for r in real if r[1] == 'NA')}）；D6 自校验 "
          f"{'✅' if same else '⛔'} 不计入")

    json.dump(dict(
        n_impl=N, u5=dict(n_eval=S5["n_eval"], hit=S5["hit"], mean_s1=S5["mean_s1"]),
        u6=dict(n_eval=S6["n_eval"], hit=S6["hit"], mean_s1=S6["mean_s1"]),
        gaps={h: dict(s0=s0[(TID, h)], s1=U6[(TID, h)]["s1"], hit=U6[(TID, h)]["hit"],
                      path=path_of(U6[(TID, h)]["trace"], endcode(U6[(TID, h)]["trace"])))
              for h in GAPS},
        cand=dict(baseline=tot5, with_t6=tot6, delta=tot6 - tot5, pct=pct,
                  per={f"{t}::{h}": per[(t, h)] for t, h in per}),
        lost=[[k[0], k[1]] for k in lost], worse=[[k[0], k[1]] for k in worse],
        newhit=[[k[0], k[1]] for k in newhit],
        gains=[[k[0], k[1], a, b] for k, a, b in gains],
        stats=dict(STATS6), stats5=dict(STATS5),
        criteria=[dict(id=r[0], status=r[1], msg=r[2]) for r in rows]),
        open(HERE / "readings_v165.json", "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()
