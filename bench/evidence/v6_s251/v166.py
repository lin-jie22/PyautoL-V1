#!/usr/bin/env python3
"""V166：`T5`/`T6` 的触发条件 —— 命中已饱和（25/25），只剩成本这一个维度。

三件事：
1. `P1` 闸：字面量在**同题兄弟源码**里出现过 ⇒ 视为语料已有惯例 ⇒ 不生成该字面量的 del 候选。
   在**付费点之前**过滤（纪律 85）。
2. `PERM` 对照：每次生成调用先算 `P1` 会跳几个（k/n），再随机挑 k 个丢弃
   ⇒ 预算与 `P1` 逐调用相同，只有「丢谁」随机（纪律 49：零假设由重排造）。
3. 记账：`replay` 是 `v164.replay_gen` 的第三次复制，唯一的差别是按 kind 记
   「生成/判分/入池/路径」；F1 用 V165 的 `readings_v165.json` 钉死它没过期。
"""
import ast
import collections
import json
import pathlib
import random
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
import v165 as N                    # noqa: E402

KEEP, ROUNDS = 2, 6
SKIP134 = frozenset({"T1", "T3", "T4"})
SEEDS = list(range(1, 21))

STATS = collections.Counter()


# ---------------------------------------------------------------- 谓词（免费）
def _doc_ids(tree):
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = getattr(n, "body", None)
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                out.add(id(b[0].value))
    return out


def lit_of(code):
    """标签 → 该候选所属字符串常量的值（与 `v164.candidates_T5` 同序同筛选）。"""
    out = {}
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return out
    doc = _doc_ids(tree)
    for i, n in enumerate(ast.walk(tree)):
        if not (isinstance(n, ast.Constant) and isinstance(n.value, str)):
            continue
        if id(n) in doc or not n.value:
            continue
        for k in range(len(n.value)):
            out[f"T5:c{i}:del{k}"] = n.value
    return out


def g6_ok(code):
    """`T6` 闸：返回值里既无 `')'` 字面量、也无 `startswith(` 调用（结构，零判分）。"""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    rets = [n.value for n in ast.walk(tree) if isinstance(n, ast.Return) and n.value is not None]
    if not rets:
        return False
    for r in rets:
        if any(isinstance(n, ast.Constant) and n.value == ")" for n in ast.walk(r)):
            return False
        if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "startswith" for n in ast.walk(r)):
            return False
    return True


def make_gen(mode, sib_src, rng=None):
    """返回 gen(tid, code, Sx, ch, ex)。mode ∈ {u6, p1, p1g6, perm}。"""
    def gen(tid, code, Sx, ch, ex):
        out = list(X.gen(tid, code, Sx, ch, ex))
        t5 = U.candidates_T5(code)
        if mode in ("p1", "p1g6", "perm"):
            lits = lit_of(code)
            if mode == "perm":
                k = sum(1 for lab, _s in t5 if lits.get(lab) in sib_src)
                if k:
                    drop = set(rng.sample(range(len(t5)), k))
                    t5 = [c for i, c in enumerate(t5) if i not in drop]
                    STATS["t5_perm_dropped"] += k
            else:
                kept = []
                for lab, src in t5:
                    if lits.get(lab) in sib_src:
                        STATS["t5_p1_skipped"] += 1
                    else:
                        kept.append((lab, src))
                t5 = kept
        t6 = N.candidates_T6(code)
        if mode == "p1g6" and not g6_ok(code):
            STATS["t6_g6_skipped"] += len(t6)
            t6 = []
        out += t5 + t6
        STATS["gen_all"] += len(out)
        return out
    return gen


# ---------------------------------------------------------------- 记账版 replay
def replay(tid, code, Sx, ch, ex, meta, mine, genfn):
    """`v164.replay_gen` 的逐行复制 + 按 kind 记账（生成/判分/入池/路径）。"""
    C = collections.Counter()
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, s1=s0, n_eval=0, hit=True, trace={}), C
    n_sib = meta["n_sib"]
    frontier = [dict(code=code, s=s0)]
    visited, n = {code}, 0
    trace = {code: (None, None)}
    for _ in range(ROUNDS):
        pool, seen = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, srcc) for lab, srcc in
                     genfn(tid, node["code"], Sx, ch, ex)]
            for k in {lab.split(":")[0] for _a, lab, _c in cands}:
                C[f"{k}_gen"] += sum(1 for _a, lab, _c in cands if lab.split(":")[0] == k)
            for key, lab, srcc in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                kind = lab.split(":")[0]
                if srcc in visited or srcc in seen:
                    continue
                if kind in SKIP134:
                    continue
                if kind == "B":
                    npv, _best = meta["m"].get(lab.split(":", 2)[2], (0, 0.0))
                    if npv == n_sib:
                        continue
                sc, _ = V.ev(tid, srcc)
                n += 1
                C[f"{kind}_eval"] += 1
                if sc > node["s"]:
                    pool.append(dict(code=srcc, s=sc))
                    seen.add(srcc)
                    trace.setdefault(srcc, (node["code"], lab))
                    C[f"{kind}_pool"] += 1
                if sc >= 1.0:
                    cur, labs = srcc, []
                    while cur is not None and trace.get(cur, (None, None))[0] is not None:
                        par, lb = trace[cur]
                        labs.append(lb)
                        cur = par
                    for lb in labs:
                        C[f"{lb.split(':')[0]}_path"] += 1
                    return dict(s0=s0, s1=sc, n_eval=n, hit=True, trace=trace), C
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:KEEP]
        visited |= {d["code"] for d in pool}
    best = max(frontier, key=lambda d: d["s"])
    return dict(s0=s0, s1=best["s"], n_eval=n, hit=False, trace=trace), C


def path_of(trace, code):
    labs, cur = [], code
    while cur is not None and trace.get(cur, (None, None))[0] is not None:
        par, lab = trace[cur]
        labs.append(lab)
        cur = par
    return list(reversed(labs))


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

    def run(mode, rng=None):
        """跑一个臂。返回 {impl: (result, C)}；谓词期间 `V.CACHE` 增量单独核（F5）。"""
        STATS.clear()
        out = {}
        for tid, h in impls:
            sib_src = "\n".join(corpus[tid][x] for x in corpus[tid] if x != h)
            g = make_gen(mode, sib_src, rng)
            Sx, ch, ex = ctx[(tid, h)]
            r, C = replay(tid, corpus[tid][h], Sx, ch, ex, meta[(tid, h)], mines(tid, h), g)
            out[(tid, h)] = (r, C)
        return out, dict(STATS)

    def summ(out):
        return dict(hit=sum(1 for r, _ in out.values() if r["hit"]),
                    n_eval=sum(r["n_eval"] for r, _ in out.values()),
                    mean_s1=sum(r["s1"] for r, _ in out.values()) / len(out),
                    t5_eval=sum(C["T5_eval"] for _r, C in out.values()),
                    t6_eval=sum(C["T6_eval"] for _r, C in out.values()))

    U6, st6 = run("u6")
    S_U6 = summ(U6)
    P1, stp = run("p1")
    S_P1 = summ(P1)
    PG, stpg = run("p1g6")
    S_PG = summ(PG)

    ref = json.loads((HERE / "readings_v165.json").read_text())
    same_ref = (S_U6["hit"] == ref["u6"]["hit"]
                and S_U6["n_eval"] == ref["u6"]["n_eval"]
                and abs(S_U6["mean_s1"] - ref["u6"]["mean_s1"]) < 1e-9)

    print("## §1 主读数")
    print("  | 臂 | 命中 | 判分 | `T5` 判分 | `T6` 判分 | mean s1 |")
    print("  |---|---|---|---|---|---|")
    for name, SM in (("`U6`", S_U6), ("`P1`", S_P1), ("`P1G6`", S_PG)):
        print(f"  | {name} | {SM['hit']}/25 | {SM['n_eval']} | {SM['t5_eval']} |"
              f" {SM['t6_eval']} | {SM['mean_s1']:.4f} |")

    print(f"\n  `P1` 跳过 `T5` 候选 {stp.get('t5_p1_skipped', 0)} 条"
          f"（相对 `U6` 生成 {sum(C['T5_gen'] for _r, C in U6.values())} 条）")
    print(f"  `P1G6` 额外跳过 `T6` 候选 {stpg.get('t6_g6_skipped', 0)} 条")

    print("\n## §2 逐实现：`P1` 是否与 `U6` 全同（纪律 82）")
    diff = [(k, U6[k][0]["hit"], round(U6[k][0]["s1"], 6), P1[k][0]["hit"],
             round(P1[k][0]["s1"], 6))
            for k in U6
            if (U6[k][0]["hit"], round(U6[k][0]["s1"], 6))
            != (P1[k][0]["hit"], round(P1[k][0]["s1"], 6))]
    print(f"  与 `U6` 不同的实现：{len(diff)} 个 {[(d[0][1][:8], d[1:]) for d in diff]}")
    diffg = [(k, U6[k][0]["hit"], round(U6[k][0]["s1"], 6), PG[k][0]["hit"],
              round(PG[k][0]["s1"], 6))
             for k in U6
             if (U6[k][0]["hit"], round(U6[k][0]["s1"], 6))
             != (PG[k][0]["hit"], round(PG[k][0]["s1"], 6))]
    print(f"  `P1G6` 与 `U6` 不同的实现：{len(diffg)} 个 {[(d[0][1][:8], d[1:]) for d in diffg]}")

    print("\n## §3 `PERM` 同预算随机置换（20 seeds）")
    rows = []
    for sd in SEEDS:
        out, st = run("perm", random.Random(sd))
        SM = summ(out)
        lost = [k for k in U6 if U6[k][0]["hit"] and not out[k][0]["hit"]]
        rows.append((sd, SM["hit"], SM["n_eval"], st.get("t5_perm_dropped", 0), lost))
        print(f"  seed {sd:2d}：命中 {SM['hit']}/25｜判分 {SM['n_eval']}｜"
              f"随机丢 {st.get('t5_perm_dropped', 0)} 条｜丢命中的实现 "
              f"{[x[1][:8] for x in lost]}")

    keep_all = sum(1 for r in rows if r[1] == 25)
    print(f"\n  `PERM` 保住 25/25 的 seed：**{keep_all}/20**")
    print(f"  `PERM` 命中中位 {sorted(r[1] for r in rows)[len(rows)//2]}｜"
          f"判分中位 {sorted(r[2] for r in rows)[len(rows)//2]}")

    print("\n## §4 F5 谓词免费性（生成期不得判分）")
    # 谓词单独抽出来量：对每个实现的根节点调一次 `make_gen`，看 `V.CACHE` 增量
    delta = 0
    for tid, h in impls:
        sib_src = "\n".join(corpus[tid][x] for x in corpus[tid] if x != h)
        g = make_gen("p1", sib_src)
        before = len(V.CACHE)
        g(tid, corpus[tid][h], *ctx[(tid, h)])
        delta += len(V.CACHE) - before
    print(f"  生成 25 个实现的根节点候选，`V.CACHE` 增量 = **{delta}**（必须 0）")

    print("\n## §5 判据（PASS / FAIL / NA）")
    rows_c = []
    MARKS = {"PASS": "✅", "FAIL": "⛔", "NA": "⬜"}

    def row(tag, st, msg):
        rows_c.append((tag, st, msg))
        print(f"  {tag} {MARKS[st]} [{st}]  {msg}")

    row("F1", "PASS" if same_ref else "FAIL",
        f"`U6` 复现 V165：命中 {S_U6['hit']}（{ref['u6']['hit']}）、"
        f"判分 {S_U6['n_eval']}（{ref['u6']['n_eval']}）")
    row("F2", "PASS" if (S_P1["hit"] == 25 and not diff) else "FAIL",
        f"`P1` 命中 {S_P1['hit']}/25、逐实现与 `U6` 不同的 {len(diff)} 个（押 0）")
    cut = 1 - S_P1["n_eval"] / S_U6["n_eval"]
    row("F3", "PASS" if S_P1["n_eval"] <= 366 else "FAIL",
        f"`P1` 判分 {S_P1['n_eval']}（−{cut*100:.1f}%；押 ≤366）")
    if keep_all <= 3:
        st4 = "PASS"
    elif keep_all >= 10:
        st4 = "FAIL"
    else:
        st4 = "NA"
    row("F4", st4, f"`PERM` 保住 25/25 的 seed {keep_all}/20"
                   f"（≤3 ⇒ 结构有信息；≥10 ⇒ 结论反转）")
    n_impl_kept = sum(1 for _r, C in P1.values() if C["T5_gen"] or C["T5_eval"])
    row("F5", "PASS" if (delta == 0 and stp.get("t5_p1_skipped", 0) > 0) else "FAIL",
        f"谓词判分增量 {delta}｜跳过数 {stp.get('t5_p1_skipped', 0)}｜"
        f"`P1` 保留过 `T5` 的实现 {n_impl_kept} 个")
    row("F6", "PASS", f"两个口径分开：跳过候选 {stp.get('t5_p1_skipped', 0)} 条 ／ "
                      f"判分省 {S_U6['n_eval'] - S_P1['n_eval']} 次")

    json.dump(dict(
        u6=dict(S_U6), p1=dict(S_P1), p1g6=dict(S_PG),
        skip=dict(stp), skip_g6=dict(stpg),
        perm=[dict(seed=r[0], hit=r[1], n_eval=r[2], dropped=r[3],
                   lost=[[k[0], k[1]] for k in r[4]]) for r in rows],
        diff=[[k[0], k[1]] for k, *_ in diff],
        cache_delta=delta,
        criteria=[dict(id=r[0], status=r[1], msg=r[2]) for r in rows_c]),
        open(HERE / "readings_v166.json", "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()
