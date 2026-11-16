#!/usr/bin/env python3
"""V160：候选表的**模板类过滤**（0 判分、0 学习）+ 预算上界的会计性。

臂：BASE / NO_T3 / NO_T1 / NO_T4 / NO_134 / NO_B / NO_C1 / MEM / MEM+NO_T3 /
NULL(n) 随机跳同样条数（200 种子）。零新增判分。
"""
import ast
import collections
import json
import pathlib
import random
import statistics
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198", "v6_s210", "v6_s236"):
    sys.path.insert(0, str(EV / d))
import idiom_transfer as I          # noqa: E402
import search as S                  # noqa: E402
import v119 as V                    # noqa: E402
import v120 as W                    # noqa: E402
import v121 as X                    # noqa: E402

ROUNDS, KEEP = 6, 2
SEEDS = 200


def search_cls(tid, code, Sx, chains, exprs, mine, skip_cls=frozenset(),
               use_mem=False, rand_skip=0.0, rnd=None):
    """v121.search 的同构重放：只加 skip(label) 钩子（按类名或随机）。"""
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, s1=s0, n_eval=0, hit=True, n_skip=0, cls=collections.Counter())
    frontier = [dict(code=code, s=s0)]
    visited, n, n_skip = {code}, 0, 0
    cls_imp = collections.Counter()
    for _ in range(ROUNDS):
        pool, seen = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, src) for lab, src in X.gen(tid, node["code"], Sx, chains, exprs)]
            for key, lab, src in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                if src in visited or src in seen:
                    continue
                kind = lab.split(":")[0]
                if kind in skip_cls:
                    n_skip += 1
                    continue
                if use_mem and key not in mine:
                    n_skip += 1
                    continue
                if rand_skip and rnd is not None and rnd.random() < rand_skip:
                    n_skip += 1
                    continue
                s, _ = V.ev(tid, src)
                n += 1
                if s >= 1.0:
                    return dict(s0=s0, s1=s, n_eval=n, hit=True, n_skip=n_skip, cls=cls_imp)
                if s > node["s"]:
                    pool.append(dict(code=src, s=s))
                    seen.add(src)
                    cls_imp[kind] += 1
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:KEEP]
        visited |= {d["code"] for d in pool}
    best = max(frontier, key=lambda d: d["s"])
    return dict(s0=s0, s1=best["s"], n_eval=n, hit=False, n_skip=n_skip, cls=cls_imp)


def ratio_model(train, test, feat):
    """单特征比率模型：pred = median(y/x) * x。0 拟合参数，抗过拟合。"""
    rs = [t["ub"] / t[feat] for t in train if t[feat]]
    k = statistics.median(rs) if rs else 0.0
    return k * test[feat]


def main() -> None:
    t0 = time.time()
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

    def mines(tid, h):
        return {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}

    ctx, ub, anatomy = {}, {}, collections.Counter()
    imp_anat, hit_anat = collections.Counter(), collections.Counter()
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ch, ex = V.harvest_chains(corpus, tid, h), W.harvest_exprs(corpus, tid, h)
        ctx[(tid, h)] = (Sx, ch, ex)
        s0, _ = V.ev(tid, code)
        if s0 >= 1.0:
            ub[(tid, h)] = 0
            continue
        cands = X.gen(tid, code, Sx, ch, ex)
        ub[(tid, h)] = len(cands)
        for lab, src in cands:
            kind = lab.split(":")[0]
            s, _ = V.ev(tid, src)
            anatomy[kind] += 1
            if s > s0:
                imp_anat[kind] += 1
            if s >= 1.0:
                hit_anat[kind] += 1

    def run(**kw):
        out = {}
        for tid, h in impls:
            Sx, ch, ex = ctx[(tid, h)]
            out[(tid, h)] = search_cls(tid, corpus[tid][h], Sx, ch, ex, mines(tid, h), **kw)
        return out

    def summ(out):
        return dict(n_eval=sum(r["n_eval"] for r in out.values()),
                    hit=sum(1 for r in out.values() if r["hit"]),
                    mean_s1=sum(r["s1"] for r in out.values()) / len(out),
                    n_skip=sum(r["n_skip"] for r in out.values()))

    base = run()
    B = summ(base)
    zero = [k for k, r in base.items() if r["n_eval"] == 0]
    print("## §0 自校验")
    print(f"  BASE 判分 {B['n_eval']}（记 588）｜到 1.0 {B['hit']}/25（记 21）"
          f"｜零判分 {len(zero)}（记 10）｜mean s1 {B['mean_s1']:.2f}")
    print(f"  上界合计 {sum(ub.values())}（记 671）｜解剖 {dict(sorted(anatomy.items()))}")
    cnt_ok = (anatomy["T1"] == 249 and anatomy["T2"] == 146 and anatomy["B"] == 106
              and anatomy["T3"] == 81 and anatomy["C1"] == 53 and anatomy["T4"] == 36)
    memr = run(use_mem=True)
    M = summ(memr)
    print(f"  MEM 判分 {M['n_eval']}（记 42）｜到 1.0 {M['hit']}（记 19）")
    ok0 = (B["n_eval"] == 588 and B["hit"] == 21 and len(zero) == 10
           and sum(ub.values()) == 671 and cnt_ok and M["n_eval"] == 42 and M["hit"] == 19)
    print(f"  自校验 {'✅ 通过' if ok0 else '⛔ 失败 —— 以下全部作废'}")

    print("\n## §1 类解剖（15 个搜索实现的基线候选表，671 条）")
    print("  | 类 | 条数 | 占比 | 改进 | 命中 |")
    print("  |---|---|---|---|---|")
    for k in sorted(anatomy, key=lambda k: -anatomy[k]):
        print(f"  | `{k}` | {anatomy[k]} | {anatomy[k]/sum(anatomy.values())*100:.1f}% |"
              f" {imp_anat[k]} | {hit_anat[k]} |")
    g = [k for k in ("T1", "T3", "T4")]
    print(f"  | **T1+T3+T4** | **{sum(anatomy[k] for k in g)}"
          f"（{sum(anatomy[k] for k in g)/sum(anatomy.values())*100:.1f}%）**"
          f" | | **{sum(imp_anat[k] for k in g)}** | **{sum(hit_anat[k] for k in g)}** |")

    print("\n## §2 按类跳过（同一回放骨架）")
    arms = {"BASE": dict(), "NO_T3": dict(skip_cls=frozenset({"T3"})),
            "NO_T1": dict(skip_cls=frozenset({"T1"})), "NO_T4": dict(skip_cls=frozenset({"T4"})),
            "NO_134": dict(skip_cls=frozenset({"T1", "T3", "T4"})),
            "NO_B": dict(skip_cls=frozenset({"B"})), "NO_C1": dict(skip_cls=frozenset({"C1"})),
            "MEM": dict(use_mem=True), "MEM+NO_T3": dict(use_mem=True, skip_cls=frozenset({"T3"}))}
    res = {}
    print("  | 臂 | 判分 | %BASE | 到 1.0 | mean s1 | 跳过 |")
    print("  |---|---|---|---|---|---|")
    for name, kw in arms.items():
        out = run(**kw)
        s_ = summ(out)
        res[name] = (s_, out)
        print(f"  | `{name}` | {s_['n_eval']} | {s_['n_eval']/B['n_eval']*100:.1f}% |"
              f" {s_['hit']}/25 | {s_['mean_s1']:.2f} | {s_['n_skip']} |")

    print("\n## §3 丢的命中是哪几个")
    for name in ("NO_T3", "NO_T1", "NO_T4", "NO_134", "NO_B", "NO_C1"):
        lost = [k for k in base if base[k]["hit"] and not res[name][1][k]["hit"]]
        if lost:
            print(f"  {name}: 丢 {len(lost)} 个 → " + ", ".join(f"{t[:4]}::{h[:8]}" for t, h in lost))
        else:
            print(f"  {name}: 丢 0 个")

    print("\n## §4 置换：随机跳掉同样条数")
    skip3 = res["NO_T3"][0]["n_skip"]
    tot_cand = sum(ub.values())
    p_rand = skip3 / tot_cand
    nh, ne = [], []
    for seed in range(SEEDS):
        rnd = random.Random(seed)
        out = run(rand_skip=p_rand, rnd=rnd)
        nh.append(sum(1 for r in out.values() if r["hit"]))
        ne.append(sum(r["n_eval"] for r in out.values()))
    nh.sort()
    ne.sort()
    print(f"  NULL(随机跳 {p_rand*100:.1f}%) 命中：min {nh[0]}｜p5 {nh[int(.05*SEEDS)]}"
          f"｜中位 {nh[SEEDS//2]}｜p95 {nh[int(.95*SEEDS)-1]}｜max {nh[-1]}")
    print(f"  NULL 判分：中位 {ne[SEEDS//2]}｜p5 {ne[int(.05*SEEDS)]}")
    print(f"  真实 `NO_T3`：命中 {res['NO_T3'][0]['hit']}｜判分 {res['NO_T3'][0]['n_eval']}")

    print("\n## §5 ② 预算：上界（会计）vs 特征模型（拟合）")
    rows = [dict(tid=t, h=h, ub=ub[(t, h)], n=base[(t, h)]["n_eval"],
                 hit=base[(t, h)]["hit"], n_S=len(ctx[(t, h)][0]))
            for t, h in impls if ub[(t, h)] > 0]
    err_ub = sorted(abs(r["ub"] - r["n"]) / r["n"] for r in rows)
    print(f"  上界 vs 实际判分：相对误差中位 **{err_ub[len(err_ub)//2]*100:.1f}%**"
          f"｜p75 {err_ub[3*len(err_ub)//4]*100:.1f}%｜max {err_ub[-1]*100:.1f}%")
    print(f"  上界/实际 比值：中位 {statistics.median([r['ub']/r['n'] for r in rows]):.2f}"
          f"｜≥7× 的实现 {sum(1 for r in rows if r['ub']/r['n'] >= 7)}/{len(rows)}")
    loio = []
    for i, r in enumerate(rows):
        pred = ratio_model([x for j, x in enumerate(rows) if j != i], r, "n_S")
        loio.append(abs(pred - r["ub"]) / r["ub"])
    loio.sort()
    print(f"  单特征 `|S|` 比率（LOIO）对上界：相对误差中位 **{loio[len(loio)//2]*100:.1f}%**"
          f"｜max {loio[-1]*100:.1f}%")
    # 跨题
    loto = []
    for r in rows:
        tr = [x for x in rows if x["tid"] != r["tid"]]
        if not tr:
            continue
        pred = ratio_model(tr, r, "n_S")
        loto.append(abs(pred - r["ub"]) / r["ub"])
    loto.sort()
    print(f"  同上但**跨题**（LOTO）：相对误差中位 **{loto[len(loto)//2]*100:.1f}%**"
          f"｜max {loto[-1]*100:.1f}%")

    print("\n## §6 判据（PREREG §3；`成立` 与 `押` 同时打印，纪律 76）")

    def row(tag, holds, bet, txt):
        ok = (holds == bet)
        print(f"  {tag} {'✅' if ok else '⛔'} {txt}｜命题成立={holds} 押={bet}")
        res.setdefault("_c", {})[tag] = dict(holds=holds, bet=bet, ok=ok, txt=txt)

    row("A1", res["NO_T3"][0]["hit"] == 21, True,
        f"NO_T3 到 1.0 = {res['NO_T3'][0]['hit']}/25")
    row("A2", res["NO_134"][0]["hit"] == 21, False,
        f"NO_134 到 1.0 = {res['NO_134'][0]['hit']}/25")
    row("A3", (B["hit"] - res["NO_B"][0]["hit"]) >= 5, True,
        f"NO_B 丢 {B['hit'] - res['NO_B'][0]['hit']} 个命中")
    row("A4", res["NO_T1"][0]["hit"] == 21, False,
        f"NO_T1 到 1.0 = {res['NO_T1'][0]['hit']}/25")
    drop = (B["n_eval"] - res["NO_T3"][0]["n_eval"]) / B["n_eval"]
    row("A5", drop >= 0.10, True, f"NO_T3 判分降幅 {drop*100:.1f}%")
    row("A6", nh[SEEDS // 2] <= 20 and nh[SEEDS // 2] < res["NO_T3"][0]["hit"], True,
        f"NULL 命中中位 {nh[SEEDS//2]} ≤ 20 且 < NO_T3 {res['NO_T3'][0]['hit']}")
    row("A7", res["MEM+NO_T3"][0]["n_eval"] < 42, True,
        f"MEM+NO_T3 判分 {res['MEM+NO_T3'][0]['n_eval']} < 42")
    ratio_err = (loio[len(loio)//2] / err_ub[len(err_ub)//2]) if err_ub[len(err_ub)//2] else float("inf")
    row("A8", ratio_err > 10, True,
        f"特征模型中位误差 {loio[len(loio)//2]*100:.1f}% vs 上界法 {err_ub[len(err_ub)//2]*100:.1f}%"
        f"（{ratio_err:.0f}×）")

    json.dump(dict(base=B, arms={n: v[0] for n, v in res.items() if n != "_c"},
                   criteria=res.get("_c", {}),
                   null=dict(hits=nh, evals=ne, p_rand=p_rand, skip3=skip3),
                   ub=dict(total=sum(ub.values()),
                           err_med=err_ub[len(err_ub)//2], err_max=err_ub[-1]),
                   loio=dict(med=loio[len(loio)//2], all=loio),
                   loto=dict(med=loto[len(loto)//2], all=loto),
                   anatomy=dict(cnt=anatomy, imp=imp_anat, hit=hit_anat),
                   per_impl={f"{t}::{h}": dict(ub=ub[(t, h)], base=base[(t, h)]["n_eval"],
                                               hit=base[(t, h)]["hit"],
                                               no_t3=res["NO_T3"][1][(t, h)]["n_eval"],
                                               mem=memr[(t, h)]["n_eval"])
                             for t, h in impls},
                   cache=len(V.CACHE), wall_s=time.time() - t0),
              open(HERE / "readings_v160.json", "w"), ensure_ascii=False, indent=1)
    print(f"\n  已落盘 readings_v160.json（{time.time()-t0:.1f}s）")


if __name__ == "__main__":
    main()
