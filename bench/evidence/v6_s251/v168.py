#!/usr/bin/env python3
"""V168：用一个免费数 `n_occ` 同时替代 `T5`/`T2` 两个方向相反的谓词？

臂（预注册 `PREREG_v168.md`）：
  `B0` = V166b `P2`（基线 366 / 25/25）
  `B2` = V167 `P2+Q1r`（对照 328 / 25/25）
  `NH(θ)` = `B0` 之上，`n_all(w) >= θ ⇒ 不生成`（同一 θ 同时管 `T5` 与 `T2`）
  `NL(θ)` = `B0` 之上，`n_all(w) <= θ ⇒ 不生成`
  `PERM`  = 与最优单臂**逐调用**同预算的随机丢弃

⛔ 一律在付费点之前（gen 返回前）执行（纪律 85）。
"""
import ast
import collections
import json
import pathlib
import random
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for d in ("v6_s198", "v6_s205", "v6_s208", "v6_s209", "v6_s236"):
    sys.path.insert(0, str(EV / d))
sys.path.insert(0, str(EV / "v6_s249"))
sys.path.insert(0, str(EV / "v6_s251"))
import idiom_transfer as I          # noqa: E402
import search as S                  # noqa: E402
import v119 as V                    # noqa: E402
import v120 as W                    # noqa: E402
import v161 as M                    # noqa: E402
import v164 as U                    # noqa: E402
import v165 as N                    # noqa: E402
import v166 as A                    # noqa: E402
import v166b as B                   # noqa: E402
import v167 as Q                    # noqa: E402
import v121 as X                    # noqa: E402

SEEDS = list(range(1, 21))
STATS = collections.Counter()
THETAS = [0, 1, 2, 3, 4, 5]


def const_multiset(src):
    c = collections.Counter()
    try:
        t = ast.parse(src)
    except SyntaxError:
        return c
    for n in ast.walk(t):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            c[n.value] += 1
    return c


def test_counter(tid):
    c = collections.Counter()
    for ts in (I.task_of(tid) or {}).get("tests") or []:
        try:
            t = ast.parse(ts)
        except SyntaxError:
            continue
        for n in ast.walk(t):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                c[n.value] += 1
    return c


def make_gen(mode, sib_consts, noc, tid, theta=0, rng=None, perm_dir="nh"):
    """`B0`(p2) 之上加**一个**单谓词；`mode` ∈ {u6,p2c,q1r,nh,nl,perm}。"""
    base = B.make_gen("p2", sib_consts)
    if mode == "q1r":
        base2 = Q.make_gen("q1r", sib_consts)

        def gen_q1r(t, code, Sx, ch, ex):
            return base2(t, code, Sx, ch, ex)
        return gen_q1r

    def gen(t, code, Sx, ch, ex):
        out = base(t, code, Sx, ch, ex)
        t5 = [(lab, src) for lab, src in out if lab.startswith("T5:")]
        t2 = [(lab, src) for lab, src in out if lab.startswith("T2:")]
        lits = A.lit_of(code)

        def n_of(lab, src_kind):
            if src_kind == "t5":
                w = lits.get(lab)
            else:
                w = Q.t2_word(lab)
            return noc.get((tid, w), 0) if w is not None else 0

        def pred(n):
            if mode == "nh":
                return n >= theta
            if mode == "nl":
                return n <= theta
            if mode == "perm":
                return n >= theta if perm_dir == "nh" else n <= theta
            return False

        # ⚠ 一律**按原位置过滤**，不许「拆开再拼」——重排会改贪心顺序，
        #   实测拆拼后 `p2c` 从 366 变 371（T5 判分 83→76），那是装置效应不是实验。
        if mode in ("u6", "p2c"):
            STATS["t5_kept"] += len(t5)
            STATS["t2_kept"] += len(t2)
            return out

        if mode == "perm":
            kt5 = sum(1 for lab, _s in t5 if pred(n_of(lab, "t5")))
            kt2 = sum(1 for lab, _s in t2 if pred(n_of(lab, "t2")))
            d5 = set(rng.sample(range(len(t5)), kt5)) if kt5 else set()
            d2 = set(rng.sample(range(len(t2)), kt2)) if kt2 else set()
            STATS["t5_perm_dropped"] += len(d5)
            STATS["t2_perm_dropped"] += len(d2)
            i5 = i2 = 0
            keepd = []
            for lab, src in out:
                if lab.startswith("T5:"):
                    i5 += 1
                    if (i5 - 1) in d5:
                        continue
                elif lab.startswith("T2:"):
                    i2 += 1
                    if (i2 - 1) in d2:
                        continue
                keepd.append((lab, src))
            STATS["t5_kept"] += len(t5) - len(d5)
            STATS["t2_kept"] += len(t2) - len(d2)
            return keepd

        keepd = []
        for lab, src in out:
            if lab.startswith("T5:") and pred(n_of(lab, "t5")):
                STATS[f"{mode}{theta}_t5_skipped"] += 1
                continue
            if lab.startswith("T2:") and pred(n_of(lab, "t2")):
                STATS[f"{mode}{theta}_t2_skipped"] += 1
                continue
            keepd.append((lab, src))
        STATS[f"{mode}{theta}_t5_kept"] += sum(1 for l, _ in keepd if l.startswith("T5:"))
        STATS[f"{mode}{theta}_t2_kept"] += sum(1 for l, _ in keepd if l.startswith("T2:"))
        return keepd
    return gen


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

    ctx, meta, sibs, noc = {}, {}, {}, {}
    for tid in V.TIDS:
        c = collections.Counter()
        for h in corpus[tid]:
            c.update(const_multiset(corpus[tid][h]))
        tc = test_counter(tid)
        for w in set(c) | set(tc):
            noc[(tid, w)] = c[w] + tc[w]
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ch, m = M.chains_meta(corpus, tid, h, s0)
        ex = W.harvest_exprs(corpus, tid, h)
        ctx[(tid, h)] = (Sx, ch, ex)
        meta[(tid, h)] = dict(m=m, n_sib=len(corpus[tid]) - 1)
        sc = set()
        for x in corpus[tid]:
            if x != h:
                sc |= B.const_values(corpus[tid][x])
        sibs[(tid, h)] = sc

    def run(mode, theta=0, rng=None, perm_dir="nh"):
        STATS.clear()
        out = {}
        for tid, h in impls:
            g = make_gen(mode, sibs[(tid, h)], noc, tid, theta, rng, perm_dir)
            out[(tid, h)] = A.replay(tid, corpus[tid][h], *ctx[(tid, h)],
                                     meta[(tid, h)], mines(tid, h), g)
        return out, dict(STATS)

    def summ(out):
        return dict(hit=sum(1 for r, _ in out.values() if r["hit"]),
                    n_eval=sum(r["n_eval"] for r, _ in out.values()),
                    mean_s1=sum(r["s1"] for r, _ in out.values()) / len(out),
                    t2_eval=sum(C["T2_eval"] for _r, C in out.values()),
                    t5_eval=sum(C["T5_eval"] for _r, C in out.values()))

    ref66 = json.loads((HERE / "readings_v166b.json").read_text())
    ref67 = json.loads((HERE / "readings_v167.json").read_text())

    B0, st0 = run("p2c")
    S0 = summ(B0)
    B2, st2 = run("q1r")
    S2 = summ(B2)

    print("## §1 基线（必须先复现）")
    print("  | 臂 | 命中 | 判分 | `T5` 判分 | `T2` 判分 | mean s1 |")
    for nm, sm in (("`B0`=P2", S0), ("`B2`=P2+Q1r", S2)):
        print(f"  | {nm} | {sm['hit']}/25 | {sm['n_eval']} | {sm['t5_eval']} | "
              f"{sm['t2_eval']} | {sm['mean_s1']:.4f} |")
    print(f"\n  ref V166b `P2`：{ref66['p2']['hit']}/25、{ref66['p2']['n_eval']}、"
          f"`T5` {ref66['p2'].get('t5_eval')}")
    print(f"  ref V167 `B2`：{ref67['b2']['hit']}/25、{ref67['b2']['n_eval']}、"
          f"`T2` {ref67['b2']['t2_eval']}")

    print("\n## §2 单谓词全扫（同一 θ 同时管 `T5` 与 `T2`）")
    print("  | 方向 | θ | 命中 | 判分 | `T5` 判分 | `T2` 判分 | `T5` 切 | `T2` 切 |")
    grid = {}
    for mode, lab in (("nh", "`n>=θ ⇒ 切`"), ("nl", "`n<=θ ⇒ 切`")):
        for th in THETAS:
            out, st = run(mode, th)
            sm = summ(out)
            grid[(mode, th)] = (sm, st)
            print(f"  | {lab} | {th} | {sm['hit']}/25 | {sm['n_eval']} | "
                  f"{sm['t5_eval']} | {sm['t2_eval']} | "
                  f"{st.get(f'{mode}{th}_t5_skipped', 0)} | "
                  f"{st.get(f'{mode}{th}_t2_skipped', 0)} |")

    ok = [k for k, (sm, _st) in grid.items() if sm["hit"] == 25 and sm["n_eval"] <= S2["n_eval"]]
    print(f"\n  ⭐ 同时满足「25/25 且判分 ≤ `B2`({S2['n_eval']})」的格：**{len(ok)}** {ok}")
    best = min(grid.items(), key=lambda kv: kv[1][0]["n_eval"])
    print(f"  判分最低的格：`{best[0][0]}({best[0][1]})` → {best[1][0]['hit']}/25、"
          f"{best[1][0]['n_eval']}")

    print("\n## §2b 逐实现：两个「合规格」掉了谁（只看未饱和实现）")
    diag = {}
    for m, th in (("nh", 2), ("nl", 2)):
        out, _st = run(m, th)
        diag[f"{m}@{th}"] = out
    print("  | impl | s0 | `B0` | `nh(2)` | `nl(2)` |")
    for k in impls:
        if s0[k] >= 1.0:
            continue
        a = B0[k][0]
        r1 = diag["nh@2"][k][0]
        r2 = diag["nl@2"][k][0]
        mark = lambda r: f"{'✅' if r['hit'] else '⛔'} {r['s1']:.2f}"
        print(f"  | {k[1][:8]} | {s0[k]:.2f} | {mark(a)} | {mark(r1)} | {mark(r2)} |")
    for nm in ("nh@2", "nl@2"):
        lost = sorted(k[1][:8] for k in diag[nm] if B0[k][0]["hit"] and not diag[nm][k][0]["hit"])
        print(f"  `{nm}` 掉的命中：{lost}")

    print("\n## §3 结构命题：两个集合的交叉（H1）")
    G = collections.defaultdict(set)
    for tid, h in impls:
        code = corpus[tid][h]
        sib = sibs[(tid, h)]
        for w in set(A.lit_of(code).values()):
            G["t5cut" if w in sib else "t5keep"].add((tid, w))
    for h in sorted(corpus[Q.TID]):
        code = corpus[Q.TID][h]
        sib = sibs[(Q.TID, h)]
        Sx, _ = S.wordset(Q.TID, code, True)
        ch, _m = M.chains_meta(corpus, Q.TID, h, s0)
        ex = W.harvest_exprs(corpus, Q.TID, h)
        out = B.make_gen("p2", sib)(Q.TID, code, Sx, ch, ex)
        for lab, _s in out:
            if not lab.startswith("T2:"):
                continue
            w = Q.t2_word(lab)
            G["t2cut" if w not in sib else "t2keep"].add((Q.TID, w))
    x1 = sorted({w for t, w in G["t5cut"]} & {w for t, w in G["t2keep"]})
    x2 = sorted({w for t, w in G["t5keep"]} & {w for t, w in G["t2cut"]})
    print(f"  `t5cut` ∩ `t2keep` = {x1}  → {len(x1)} 个值（T5 该切 / T2 不该切）")
    print(f"  `t5keep` ∩ `t2cut` = {x2}  → {len(x2)} 个值（T5 不该切 / T2 该切）")
    print("  ⇒ 同一个值、同一个 `n_occ`，方向相反 ⇒ 任何 `f(值)` 必错一个")

    print("\n## §4 单阈值合并判对率（H2）")
    print("  | θ | 方向`n≥θ` 判对 | 方向`n≤θ` 判对 | 最好 |")
    best_acc = 0.0
    for th in THETAS:
        accs = {}
        for tag, gold in (("t5cut", 1), ("t2keep", 0), ("t2cut", 1), ("t5keep", 0)):
            v = [noc.get((tid, w), 0) for tid, w in G[tag]]
            for mode in ("nh", "nl"):
                p = [(1 if x >= th else 0) if mode == "nh" else (1 if x <= th else 0) for x in v]
                accs.setdefault(mode, [0, 0])
                accs[mode][0] += sum(1 for q_ in p if q_ == gold)
                accs[mode][1] += len(p)
        a = {k: v[0] / v[1] for k, v in accs.items()}
        b = max(a.values())
        best_acc = max(best_acc, b)
        print(f"  | {th} | {accs['nh'][0]}/{accs['nh'][1]} = {a['nh']:.3f} | "
              f"{accs['nl'][0]}/{accs['nl'][1]} = {a['nl']:.3f} | **{b:.3f}** |")
    print(f"  ⭐ 12 格最优合并判对率 = **{best_acc:.3f}**（押 ≤0.65）")

    print("\n## §5 置换对照（逐调用同预算，纪律 49/84）")
    # ⚠ 先报哑火：判分最低的 `nh(0)` 是「全切」（θ=0 ⇒ 谓词恒真），
    #   随机无处可丢 ⇒ 20 seeds 必然逐位相同。那不是「先验准」，是**对照没有牙**（纪律 88）。
    dbg, dbg_st = run("perm", 0, random.Random(1), perm_dir="nh")
    dsm = summ(dbg)
    print(f"  [哑火记录] 对 `nh(0)`（全切）置换：命中 {dsm['hit']}/25、判分 {dsm['n_eval']}、"
          f"丢 T5 {dbg_st.get('t5_perm_dropped')}/T2 {dbg_st.get('t2_perm_dropped')}"
          f" ⇒ 与 `nh(0)` 相同 ⇒ θ=0 下 k==len(池)，随机退化。⛔ 不作对照。")
    # 有效对照：取「命中 ≥22」的格里判分最低者
    cand = [(k, v) for k, v in grid.items() if v[0]["hit"] >= 22]
    (bmode, bth), (bsm, _bst) = min(cand, key=lambda kv: kv[1][0]["n_eval"])
    print(f"  [有效对照臂] `{bmode}({bth})`：命中 {bsm['hit']}/25、判分 {bsm['n_eval']}"
          f"（命中 ≥22 的 {len(cand)} 格中判分最低）")
    rows = []
    for sd in SEEDS:
        out, st = run("perm", bth, random.Random(sd), perm_dir=bmode)
        sm = summ(out)
        rows.append((sd, sm["hit"], sm["n_eval"],
                     st.get("t5_perm_dropped", 0), st.get("t2_perm_dropped", 0)))
        print(f"  seed {sd:2d}：命中 {sm['hit']}/25｜判分 {sm['n_eval']}｜"
              f"随机丢 T5 {rows[-1][3]} / T2 {rows[-1][4]}")
    same = len({(r[1], r[2]) for r in rows}) == 1
    print(f"\n  `PERM` 保 25/25：**{sum(1 for r in rows if r[1] == 25)}/20**｜"
          f"命中中位 {sorted(r[1] for r in rows)[10]}｜判分中位 {sorted(r[2] for r in rows)[10]}"
          f"（对照臂 {bsm['hit']}/25、{bsm['n_eval']}）"
          f"{'｜⚠ 仍逐位相同 ⇒ 再查装置' if same else ''}")

    print("\n## §6 免费性（谓词期间不得偷偷判分）")
    delta = 0
    for mode in ("nh", "nl"):
        for th in (0, 2):
            for tid, h in impls:
                g = make_gen(mode, sibs[(tid, h)], noc, tid, th)
                before = len(V.CACHE)
                g(tid, corpus[tid][h], *ctx[(tid, h)])
                delta += len(V.CACHE) - before
    print(f"  谓词生成 100 个根节点，`V.CACHE` 增量 = **{delta}**（必须 0）")

    print("\n## §7 判据")
    rows_c = []
    MARKS = {"PASS": "✅", "FAIL": "⛔", "NA": "⬜"}

    def row(tag, st, msg):
        rows_c.append((tag, st, msg))
        print(f"  {tag} {MARKS[st]} [{st}]  {msg}")

    ok1 = (S0["hit"] == ref66["p2"]["hit"] and S0["n_eval"] == ref66["p2"]["n_eval"])
    row("T1", "PASS" if ok1 else "FAIL",
        f"`B0` 复现 V166b `P2`：{S0['hit']}/25、{S0['n_eval']}"
        f"（ref {ref66['p2']['hit']}/{ref66['p2']['n_eval']}）")
    ok2 = (S2["hit"] == ref67["b2"]["hit"] and S2["n_eval"] == ref67["b2"]["n_eval"]
           and S2["t2_eval"] == ref67["b2"]["t2_eval"])
    row("T2", "PASS" if ok2 else "FAIL",
        f"`B2` 复现 V167：{S2['hit']}/25、{S2['n_eval']}、`T2` {S2['t2_eval']}"
        f"（ref {ref67['b2']['hit']}/{ref67['b2']['n_eval']}/{ref67['b2']['t2_eval']}）")
    row("T3", "PASS" if (x1 and x2) else "FAIL",
        f"H1：交叉 {len(x1)}/{len(x2)} 个值（押两处都非空）")
    row("T4", "PASS" if best_acc <= 0.65 else "FAIL",
        f"H2：单阈值最优合并判对率 {best_acc:.3f}（押 ≤0.65）")
    row("T5", "PASS" if not ok else "FAIL",
        f"H3：满足「25/25 且 ≤{S2['n_eval']}」的格 {len(ok)}（押 0）")
    row("T6", "PASS" if delta == 0 else "FAIL", f"谓词判分增量 {delta}（必须 0）")

    json.dump(dict(
        b0=S0, b2=S2,
        grid={f"{m}@{th}": v[0] for (m, th), v in grid.items()},
        grid_skip={f"{m}@{th}": v[1] for (m, th), v in grid.items()},
        cross_x1=x1, cross_x2=x2, best_acc=best_acc,
        sets={k: sorted(f"{t}::{w}" for t, w in v) for k, v in G.items()},
        nocc={f"{t}::{w}": noc[(t, w)] for t, w in noc},
        perm=[dict(seed=r[0], hit=r[1], n_eval=r[2], d5=r[3], d2=r[4]) for r in rows],
        cache_delta=delta,
        criteria=[dict(id=r[0], status=r[1], msg=r[2]) for r in rows_c]),
        open(HERE / "readings_v168.json", "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()
