#!/usr/bin/env python3
"""V158：候选级免费信号（key∈mine）当过滤器 —— 代价曲线 + 置换零假设。

零新增判分：全部复用 V.ev（确定性、带 CACHE）。
臂：BASE / MEM / BUD(B) / ORACLE / NULL(200 置换)。
"""
import ast
import collections
import json
import pathlib
import random
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
BUDS = [5, 10, 20, 40, 60, 80, 120, 200, 400]


def search_pol(tid, code, Sx, chains, exprs, mine, policy):
    """v121.search 的同构重放，只加一个 policy.skip(key, n_mem, n_non) 钩子。

    ⛔ 与 v121 的差异显式登记：① 早停保留（`s>=1.0: return`）；
    ② 跳过发生在 `ev` 之前，被跳过的候选既不进 frontier 也不进 visited
    （与部署「不生成」等价，但顺序与 `order_key` 一致：记忆键先）。
    """
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, s1=s0, n_eval=0, hit=True, events=[], n_mem=0, n_non=0)
    frontier = [dict(code=code, s=s0)]
    visited = {code}
    n = n_mem = n_non = 0
    ev = []
    for rnd in range(1, ROUNDS + 1):
        pool, seen = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, src) for lab, src in X.gen(tid, node["code"], Sx, chains, exprs)]
            cands = sorted(cands, key=lambda x: (0 if x[0] in mine else 1,))
            for key, _lab, src in cands:
                if src in visited or src in seen:
                    continue
                if policy(key, n_mem, n_non):
                    continue
                s, _ = V.ev(tid, src)
                n += 1
                if key in mine:
                    n_mem += 1
                else:
                    n_non += 1
                ev.append((key, s, src, s > node["s"], rnd))
                if s >= 1.0:
                    return dict(s0=s0, s1=s, n_eval=n, hit=True, events=ev,
                                n_mem=n_mem, n_non=n_non)
                if s > node["s"]:
                    pool.append(dict(code=src, s=s))
                    seen.add(src)
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:KEEP]
        visited |= {d["code"] for d in pool}
    best = max(frontier, key=lambda d: d["s"])
    return dict(s0=s0, s1=best["s"], n_eval=n, hit=False, events=ev,
                n_mem=n_mem, n_non=n_non)


class Pol:
    def __init__(self, kind, mine=None, bud=None):
        self.kind, self.mine, self.bud = kind, mine or set(), bud

    def skip(self, key, n_mem, n_non):
        if self.kind == "BASE":
            return False
        if self.kind == "MEM":
            return key not in self.mine
        if self.kind == "BUD":
            if key in self.mine:
                return False
            return n_non >= self.bud
        raise SystemExit(f"bad kind {self.kind}")


def main() -> None:
    t0 = time.time()
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

    def run_all(pol_factory):
        out = {}
        for tid, h in impls:
            Sx, ch, ex = ctx[(tid, h)]
            out[(tid, h)] = search_pol(tid, corpus[tid][h], Sx, ch, ex,
                                       mines(tid, h), pol_factory(tid, h).skip)
        return out

    def summ(out):
        n_ev = sum(r["n_eval"] for r in out.values())
        n_hit = sum(1 for r in out.values() if r["hit"])
        n_imp = sum(1 for r in out.values() for e in r["events"] if e[3])
        mean_s1 = sum(r["s1"] for r in out.values()) / len(out)
        return dict(n_eval=n_ev, hit=n_hit, imp=n_imp, mean_s1=mean_s1)

    # ------------------------------------------------ §0 自校验
    base = run_all(lambda t, h: Pol("BASE"))
    B = summ(base)
    print("## §0 自校验（⛔ 不成立则全部作废）")
    print(f"  BASE 判分 = {B['n_eval']}（记 588）｜到 1.0 = {B['hit']}/25（记 21）"
          f"｜改进(s>node.s) = {B['imp']}（记 29 = 18 纯改进 + 11 命中）｜mean s1 = {B['mean_s1']:.2f}")
    zero = [k for k, r in base.items() if r["n_eval"] == 0]
    print(f"  零判分实现 = {len(zero)}（记 10，全部因基线已 1.0）"
          f"｜全对 = {all(base[k]['hit'] and base[k]['s0'] >= 1.0 for k in zero)}")
    ok0 = (B["n_eval"] == 588 and B["hit"] == 21 and B["imp"] == 29 and len(zero) == 10)
    print(f"  自校验 {'✅ 通过' if ok0 else '⛔ 失败 —— 以下全部作废'}")

    # ------------------------------------------------ §1 MEM / ORACLE
    memr = run_all(lambda t, h: Pol("MEM", mines(t, h)))
    M = summ(memr)
    print("\n## §1 两个参照臂")
    print(f"  MEM    ：判分 {M['n_eval']}（{M['n_eval']/B['n_eval']*100:.1f}%）"
          f"｜到 1.0 {M['hit']}/25｜改进 {M['imp']}｜mean s1 {M['mean_s1']:.2f}")
    lost = [k for k in base if base[k]["hit"] and not memr[k]["hit"]]
    print(f"  丢掉的命中（{len(lost)} 个）：")
    for k in lost:
        r = base[k]
        hk = [tuple(e[0]) for e in r["events"] if e[1] >= 1.0][0]
        pos = [i for i, e in enumerate(r["events"], 1) if e[1] >= 1.0][0]
        print(f"    {k[0][:16]}::{k[1]} 键{hk} 位置 {pos}/{len(r['events'])}"
              f"｜该键记忆记录 = {sum(1 for x in mem[k[0]] if tuple(x['act']) == hk)} 条")

    # ORACLE：跳过「本实现从未命中」的键（需知未来）
    hitkeys = {k: {tuple(e[0]) for e in r["events"] if e[1] >= 1.0} for k, r in base.items()}

    def oracle_factory(tid, h):
        hk = hitkeys[(tid, h)]
        p = Pol("MEM", set())
        p.mine = hk                     # 「记忆」= 只有命中键
        return p

    orc = run_all(oracle_factory)
    O = summ(orc)
    print(f"  ORACLE ：判分 {O['n_eval']}（{O['n_eval']/B['n_eval']*100:.1f}%）"
          f"｜到 1.0 {O['hit']}/25｜改进 {O['imp']}｜mean s1 {O['mean_s1']:.2f}")
    omiss = [k for k in base if base[k]["hit"] and not orc[k]["hit"]]
    print(f"  ORACLE 回放丢的命中 = {len(omiss)}/{B['hit']}"
          f"（若 >0 ⇒ 「非命中事件」不全是浪费，其中一部分是**路径**）")
    for k in omiss:
        r = base[k]
        hk = [tuple(e[0]) for e in r["events"] if e[1] >= 1.0][0]
        pos = [i for i, e in enumerate(r["events"], 1) if e[1] >= 1.0][0]
        maxr = max(e[4] for e in r["events"])
        print(f"    {k[0][:16]}::{k[1]} 键{hk} 位置 {pos}/{len(r['events'])} 轮次 {maxr}")
    print(f"  ⭐ ORACLE 判分 {O['n_eval']} 却只拿 {O['hit']}/25 ⇒ 省下的 {B['n_eval']-O['n_eval']}"
          f" 次里含**踏脚石**，不是纯浪费")

    # ------------------------------------------------ §2 BUD 曲线
    print("\n## §2 预算曲线：记忆键必判 + 非记忆键最多 B 次")
    print("  | B | 判分 | %BASE | 到 1.0 | 改进 | mean s1 | 非记忆实判 |")
    print("  |---|---|---|---|---|---|---|")
    bud_rows = []
    for b in BUDS:
        r_ = run_all(lambda t, h, b=b: Pol("BUD", mines(t, h), b))
        s_ = summ(r_)
        nn = sum(x["n_non"] for x in r_.values())
        bud_rows.append((b, s_, nn, r_))
        print(f"  | {b} | {s_['n_eval']} | {s_['n_eval']/B['n_eval']*100:.1f}% |"
              f" {s_['hit']}/25 | {s_['imp']} | {s_['mean_s1']:.2f} | {nn} |")
    full = [b for b, s_, _nn, _r in bud_rows if s_["hit"] == B["hit"]]
    min_b = min(full) if full else None
    print(f"  ⭐ 回到 {B['hit']}/25 的最小 B = {min_b if min_b is not None else '（达不到）'}")

    # ------------------------------------------------ §3 置换零假设
    print("\n## §3 NULL：按实现内重排 `mine` 标签（保留每实现记忆键数量，200 种子）")
    prod = {k: sorted({tuple(e[0]) for e in r["events"]}) for k, r in base.items()}
    real_cnt = {k: len([x for x in prod[k] if x in mines(*k)]) for k in base}
    print(f"  每实现「记忆键」数分布 = {dict(sorted(collections.Counter(real_cnt.values()).items()))}"
          f"｜合计 {sum(real_cnt.values())}")
    nh, ne = [], []
    for seed in range(SEEDS):
        rnd = random.Random(seed)
        perm = {}
        for k in base:
            pool = prod[k]
            c = real_cnt[k]
            pick = set(rnd.sample(pool, c)) if c else set()
            perm[k] = pick
        out = {}
        for tid, h in impls:
            Sx, ch, ex = ctx[(tid, h)]
            out[(tid, h)] = search_pol(tid, corpus[tid][h], Sx, ch, ex, perm[(tid, h)],
                                       Pol("MEM", perm[(tid, h)]).skip)
        nh.append(sum(1 for r in out.values() if r["hit"]))
        ne.append(sum(r["n_eval"] for r in out.values()))
    nh.sort()
    ne.sort()
    p_emp = sum(1 for x in nh if x < M["hit"]) / SEEDS
    print(f"  NULL 命中数：min {nh[0]}｜p5 {nh[int(0.05*SEEDS)]}｜中位 {nh[SEEDS//2]}"
          f"｜p95 {nh[int(0.95*SEEDS)-1]}｜max {nh[-1]}")
    print(f"  NULL 判分：中位 {ne[SEEDS//2]}｜p95 {ne[int(0.95*SEEDS)-1]}")
    print(f"  ⭐ 真实 MEM 命中 {M['hit']} vs NULL：经验 p = {p_emp:.3f}"
          f"（{SEEDS} 次置换中 {sum(1 for x in nh if x < M['hit'])} 次更差）")

    # ------------------------------------------------ §4 判据
    print("\n## §4 判据（PREREG §3）")

    def row(tag, ok, txt):
        print(f"  {tag} {'✅' if ok else '⛔'} {txt}")

    # ⚠️ 极性说明：命题按字面成立与否记为 holds，与预注册「押」比对；一致 ⇒ ✅
    w1 = M["n_eval"] <= 60
    w2 = M["hit"] == 19
    w3 = nh[SEEDS // 2] <= 19 and M["hit"] > nh[int(0.95 * SEEDS) - 1]
    row("W1", w1 == True, f"MEM 判分 {M['n_eval']} ≤ 60（结构上界 43）｜押 ✅")
    row("W2", w2 == True, f"MEM 到 1.0 = {M['hit']}/25（押 19）")
    row("W3", w3 == True,
        f"NULL 中位 {nh[SEEDS//2]} ≤ 19 且 MEM {M['hit']} > NULL p95 {nh[int(0.95*SEEDS)-1]}｜押 ✅")
    bkt = "(a) ≤60" if (min_b is not None and min_b <= 60) else (
        "(b) 61~300" if (min_b is not None and min_b <= 300) else "(c) >300 或回不来")
    row("W4", bkt == "(c) >300 或回不来", f"回到 21/25 的最小 B = {min_b} ⇒ 实际 {bkt}（押 c）")
    w5 = O["hit"] == 21
    row("W5", w5 == False, f"ORACLE 回放 = {O['hit']}/25｜命题「仍到 21」= {w5}（押 ⛔）")
    w6 = M["mean_s1"] >= 0.9 * B["mean_s1"]
    row("W6", w6 == False,
        f"MEM mean s1 {M['mean_s1']:.2f} vs 0.9×BASE {0.9*B['mean_s1']:.2f}"
        f"｜命题成立 = {w6}（押 ⛔）")

    json.dump(dict(
        base=B, mem=M, oracle=O, min_b=min_b,
        buds=[dict(b=b, **s_, n_non=nn) for b, s_, nn, _r in bud_rows],
        null=dict(hits=nh, evals=ne, seeds=SEEDS, p=p_emp),
        lost=[f"{k[0]}::{k[1]}" for k in lost],
        oracle_miss=[f"{k[0]}::{k[1]}" for k in omiss],
        per_impl={f"{t}::{h}": dict(base=dict(n=base[(t, h)]["n_eval"], hit=base[(t, h)]["hit"],
                                              s1=base[(t, h)]["s1"]),
                                    mem=dict(n=memr[(t, h)]["n_eval"], hit=memr[(t, h)]["hit"],
                                             s1=memr[(t, h)]["s1"]),
                                    oracle=dict(n=orc[(t, h)]["n_eval"], hit=orc[(t, h)]["hit"]))
                  for (t, h) in impls},
        zero_eval=len(zero), wall_s=time.time() - t0,
    ), open(HERE / "readings_v158.json", "w"), ensure_ascii=False, indent=1)
    print(f"\n  已落盘 readings_v158.json（{time.time()-t0:.1f}s）")


if __name__ == "__main__":
    main()
