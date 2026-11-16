#!/usr/bin/env python3
"""V152：一条键的 1.0 占比，有多少由键本身决定 —— 键级统计量能不能替代实例级判分。

上游：§218 把 `MAX`/`MIN` 都逼到饱和（26→28、9→0），说明极值这一层只剩「存不存在」。
剩下唯一可能有用处的量是**分布** `p = #{ev>=1.0} / n`。

本轮零新增判分：复用 V151 的 C_FULL 覆盖（满 d=2 闭包），只补记每格 `k`（1.0 实例数）。

判据见 PREREG.md（跑前落盘）：W1 自校验 / W2 p 形状 / W3 键内是否变 /
W4 ⭐⭐ 键的边际信息量（B0 题级 vs B1 键级，重排零假设 200 次）/ W5 决策层 /
W6 误差量级 / W7 四条对照。

⛔ 不新造分数（只用 `v119.ev >= 1.0`）；⛔ 不改 gen/abstract/记忆；⛔ 不碰 V79。
"""
from __future__ import annotations

import ast
import collections
import json
import pathlib
import random
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for _d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198", "v6_s210", "v6_s236", "v6_s240"):
    sys.path.insert(0, str(EV / _d))

import idiom_transfer as I     # noqa: E402
import search as S             # noqa: E402
import v119 as V               # noqa: E402
import v120 as W               # noqa: E402
import v121 as X               # noqa: E402
import v151 as P               # noqa: E402

BASELINE = ("?", "<baseline>")
N_PERM = 200
SEED = 152


def pooled(pairs) -> float:
    """Σk/Σn；分母 0 时返回 None。"""
    k = sum(p[0] for p in pairs)
    n = sum(p[1] for p in pairs)
    return (k / n) if n else None


def mae(pred: dict, obs: dict) -> float:
    """格等权平均绝对误差；pred[i] 为 None 的格跳过（两个分母都要报）。"""
    errs = [abs(pred[i] - obs[i]) for i in obs if pred.get(i) is not None]
    return (sum(errs) / len(errs)) if errs else float("nan"), len(errs)


def spearman(xs, ys) -> float:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for t in range(i, j + 1):
                r[order[t]] = avg
            i = j + 1
        return r
    if len(xs) < 3:
        return float("nan")
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return num / (dx * dy) if dx and dy else float("nan")


def main() -> None:
    t_start = time.time()
    corpus = I.harvest()
    impls = []
    for tid in V.TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                continue
            impls.append((tid, h))
    mem = json.loads(X.MEM_FILE.read_text(encoding="utf-8"))

    uni_all = set()
    for tid, h in impls:
        for kk in mem[tid]:
            if kk["h"] != h:
                uni_all.add((tid, h, tuple(kk["act"])))
    uni_real = sorted(c for c in uni_all if c[2] != BASELINE)

    # ------------------------------------------------ 补记 k：复用 V151 的 C_FULL 覆盖
    V.CACHE.clear()
    t = time.time()
    clo = {}
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ch = V.harvest_chains(corpus, tid, h)
        ex = W.harvest_exprs(corpus, tid, h)
        clo[(tid, h)] = P.closure2(tid, code, Sx, ch, ex)[0]
    t_clo = time.time() - t

    rows = []
    for c in uni_real:
        tid, h, key = c
        srcs = sorted(clo[(tid, h)].get(key, set()))
        sc = [V.ev(tid, s)[0] for s in srcs]
        n = len(srcs)
        k = sum(1 for x in sc if x >= 1.0)
        s0 = V.ev(tid, corpus[tid][h])[0]
        rows.append(dict(cell=c, tid=tid, h=h, key=key, n=n, k=k, p=(k / n if n else None),
                         s0=s0, base_correct=s0 >= 1.0,
                         max_c=(max(sc) if sc else None)))

    cells33 = [r for r in rows if r["n"] > 0]
    n_inst = sum(r["n"] for r in rows)

    print("## §0 自校验（W1；⛔ 不成立则下面全部作废）")
    tab = collections.Counter(
        "UNTRIED" if r["n"] == 0 else ("VALID" if r["max_c"] >= 1.0 else "INVALID") for r in rows)
    keys = sorted({r["key"] for r in rows})
    print(f"  实例合计 = {n_inst}（V151 记 3142）｜有实例格 = {len(cells33)}/35（33）")
    print(f"  三值 = VALID {tab['VALID']} / INVALID {tab['INVALID']} / UNTRIED {tab['UNTRIED']}"
          f"（记 28/5/2）｜不同键 = {len(keys)}（记 6）｜建闭包 + 判分 {t_clo:.1f}s")
    all_below1 = all(r["p"] < 1.0 for r in cells33)
    print(f"  33/33 格 p < 1.0 = {all_below1}"
          f"｜p=0 的格 = {sum(1 for r in cells33 if r['p'] == 0)}（应 = INVALID 数）")
    w1 = (n_inst == 3142 and len(cells33) == 33 and tab["VALID"] == 28
          and tab["INVALID"] == 5 and tab["UNTRIED"] == 2 and len(keys) == 6 and all_below1)

    # ------------------------------------------------ 每格全表
    print("\n## §1 每格读数（35 格 = 25 实现 × 1~2 键）")
    print("  | 实现 | 键 | n | k | p | s0 | 基线已对 |")
    print("  |---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (r["tid"], str(r["key"]), -r["n"])):
        p = "—" if r["p"] is None else f"{r['p']:.3f}"
        print(f"  | {r['tid'][:12]}::{r['h']} | `{r['key']}` | {r['n']} | {r['k']} | {p} | "
              f"{r['s0']:.2f} | {'是' if r['base_correct'] else '否'} |")

    # ------------------------------------------------ W2 p 的形状
    ps = sorted(r["p"] for r in cells33)
    med_p = ps[len(ps) // 2] if len(ps) % 2 else (ps[len(ps) // 2 - 1] + ps[len(ps) // 2]) / 2
    print("\n## §2 W2：`p`（1.0 占比）的形状")
    print(f"  n = {len(ps)}｜min {ps[0]:.3f}｜median {med_p:.3f}｜mean {sum(ps) / len(ps):.3f}"
          f"｜max {ps[-1]:.3f}")
    print(f"  分位：p10 {ps[len(ps) // 10]:.3f}｜p90 {ps[9 * len(ps) // 10]:.3f}")
    print(f"  p=0 的格 = {sum(1 for x in ps if x == 0)}｜p>0 的格 = {sum(1 for x in ps if x > 0)}")
    b2 = "(a) <0.20" if med_p < 0.20 else ("(b) 0.20~0.50" if med_p <= 0.50 else "(c) >0.50")
    print(f"  预注册桶 W2：押 (a) <0.20 ⇒ 实际 {b2}")
    print("  按题：")
    for t in sorted({r["tid"] for r in cells33}):
        sub = sorted(r["p"] for r in cells33 if r["tid"] == t)
        print(f"    {t:22s} 格 {len(sub):2d}｜p 中位 {sub[len(sub) // 2]:.3f}"
              f"｜范围 {sub[0]:.3f}~{sub[-1]:.3f}｜n 中位 "
              f"{sorted(r['n'] for r in cells33 if r['tid'] == t)[len(sub) // 2]}")

    # ------------------------------------------------ W3 键内是否变
    print("\n## §3 W3：同一条键在它覆盖的实现里，1.0 占比变不变")
    print("  | 键 | 覆盖实现 | p 明细 | max/min |")
    print("  |---|---|---|---|")
    by_key = collections.defaultdict(list)
    for r in cells33:
        by_key[r["key"]].append(r)
    n_2x = 0
    for k_, v in sorted(by_key.items(), key=lambda kv: -len(kv[1])):
        lo = min(x["p"] for x in v)
        hi = max(x["p"] for x in v)
        ratio = float("inf") if lo == 0 else hi / lo
        if ratio >= 2:
            n_2x += 1
        print(f"  | `{k_}` | {len(v)} | {[round(x['p'], 3) for x in v]} | "
              f"{'∞' if ratio == float('inf') else f'{ratio:.1f}×'} |")
    b3 = "6" if n_2x == 6 else ("4~5" if n_2x >= 4 else "≤3")
    print(f"  预注册桶 W3：押 max/min ≥2× 的键 = 6/6 ⇒ 实际 **{n_2x}/6**（桶 {b3}）")

    # ------------------------------------------------ W4 主判据：键的边际信息量
    print("\n## §4 W4（主判据）：键级统计量比题级统计量少多少误差")
    dual = {}
    for t in sorted({r["tid"] for r in cells33}):
        ks = sorted({r["key"] for r in cells33 if r["tid"] == t})
        if len(ks) >= 2:
            dual[t] = {r["cell"]: r for r in cells33 if r["tid"] == t}
    print(f"  双键题 = {list(dual)}｜参与格 = {sum(len(v) for v in dual.values())}"
          f"（PREREG P3 记 21）")

    def delta(assign):
        """assign: cell -> 键标签（重排后）。返回 (Δ, 各题 MAE 明细)。"""
        per_task, tot = {}, []
        for t, cs in dual.items():
            obs = {c: r["p"] for c, r in cs.items()}
            b0, b1 = {}, {}
            for c, r in cs.items():
                others_all = [(x["k"], x["n"]) for cc, x in cs.items() if cc != c]
                b0[c] = pooled(others_all)
                same = [(x["k"], x["n"]) for cc, x in cs.items()
                        if cc != c and assign[cc] == assign[c]]
                b1[c] = pooled(same)
            m0, n0 = mae(b0, obs)
            m1, n1 = mae(b1, obs)
            per_task[t] = (m0, m1, n0, n1)
            if n0 and n1:
                tot.append(m0 - m1)
        return (sum(tot) / len(tot) if tot else float("nan")), per_task

    ident = {r["cell"]: tuple(r["key"]) for r in cells33}
    d_obs, detail = delta(ident)
    print("  | 题 | MAE(B0 题级) | MAE(B1 键级) | Δ = B0−B1 | 格数 |")
    print("  |---|---|---|---|---|")
    for t, (m0, m1, n0, n1) in detail.items():
        print(f"  | {t} | {m0:.4f} | {m1:.4f} | {m0 - m1:+.4f} | {n0} |")
    print(f"  ⭐ Δ_obs（macro 跨题）= {d_obs:+.4f}")

    rng = random.Random(SEED)
    null = []
    for _ in range(N_PERM):
        assign = dict(ident)
        for t, cs in dual.items():
            labels = [assign[c] for c in cs]
            cnt = collections.Counter(labels)
            toks = [l for l, n_ in cnt.items() for _ in range(n_)]
            shuffled = toks[:]
            rng.shuffle(shuffled)
            for c, lab in zip(sorted(cs), shuffled):
                assign[c] = lab
        null.append(delta(assign)[0])
    null.sort()
    lo_i, hi_i = int(0.05 * len(null)), int(0.95 * len(null)) - 1
    q05, q95 = null[lo_i], null[hi_i]
    inside = q05 <= d_obs <= q95
    frac_ge = sum(1 for x in null if x >= d_obs) / len(null)
    print(f"  零假设（题内随机重排键标签，{N_PERM} 次）：Δ 的分位 "
          f"p5 {q05:+.4f}｜中位 {null[len(null) // 2]:+.4f}｜p95 {q95:+.4f}")
    print(f"  实际 Δ = {d_obs:+.4f}｜落在区间内 = {inside}｜零分布中 ≥Δ_obs 的比例 = {frac_ge:.3f}")
    if inside or d_obs <= 0:
        b4 = "(a) 区间内（键零信息）"
    elif d_obs < 0.05:
        b4 = "(b) 显著但 Δ<0.05"
    else:
        b4 = "(c) 显著且 Δ≥0.05"
    print(f"  预注册桶 W4：押 (a) ⇒ 实际 {b4}")

    # 对照 C2：池化 → 格等权均值
    def delta_eqw(assign):
        per_task, tot = {}, []
        for t, cs in dual.items():
            obs = {c: r["p"] for c, r in cs.items()}
            b0, b1 = {}, {}
            for c, r in cs.items():
                oth = [x["p"] for cc, x in cs.items() if cc != c]
                b0[c] = sum(oth) / len(oth) if oth else None
                same = [x["p"] for cc, x in cs.items() if cc != c and assign[cc] == assign[c]]
                b1[c] = sum(same) / len(same) if same else None
            m0, _ = mae(b0, obs)
            m1, _ = mae(b1, obs)
            per_task[t] = (m0, m1)
            if m0 == m0 and m1 == m1:
                tot.append(m0 - m1)
        return (sum(tot) / len(tot) if tot else float("nan")), per_task
    d_eqw, _ = delta_eqw(ident)
    print(f"  C2（池化 → 格等权均值）：Δ = {d_eqw:+.4f}（池化口径 {d_obs:+.4f}，符号一致 = "
          f"{(d_eqw > 0) == (d_obs > 0)}）")

    # 对照 C3：剔除 n_c=5 的格（g7d2 的 6 格）
    keep = {c: l for c, l in ident.items() if all(x["n"] != 5 for x in cells33 if x["cell"] == c)}
    dual_backup = dual
    dual = {t: {c: r for c, r in cs.items() if c in keep} for t, cs in dual_backup.items()}
    dual = {t: cs for t, cs in dual.items() if len({r["key"] for r in cs.values()}) >= 2}
    d_nosmall, _ = delta({c: ident[c] for c in keep})
    print(f"  C3（剔除 n_c=5 的 6 格，剩 {sum(len(v) for v in dual.values())} 格）：Δ = "
          f"{d_nosmall:+.4f}｜与全量符号一致 = {(d_nosmall > 0) == (d_obs > 0)}")
    dual = dual_backup

    # 对照 C1：单键题上 B1 ≡ B0（结构恒等，纪律 4 ⇒ 不是证据）
    single = [r for r in cells33 if len({x["key"] for x in cells33 if x["tid"] == r["tid"]}) == 1]
    print(f"  C1 单键题格 = {len(single)}：这些格上 B1 ≡ B0 恒等，对「键有没有用」零信息，"
          f"不参与 Δ（PREREG W7-C1）")

    # ------------------------------------------------ W5 决策层
    print("\n## §5 W5：用键级统计量判 VALID/INVALID，能不能比「一律 VALID」强")
    pred_all = {}
    for r in cells33:
        cs = [x for x in cells33 if x["tid"] == r["tid"] and x["key"] == r["key"]
              and x["cell"] != r["cell"]]
        pred_all[r["cell"]] = pooled([(x["k"], x["n"]) for x in cs])
    truth = {r["cell"]: (r["max_c"] >= 1.0) for r in cells33}
    base_acc = sum(1 for v in truth.values() if v) / len(truth)
    best = (0.0, -1.0, None)
    for i in range(101):
        tau = i / 100
        ok = 0
        for c, p_ in pred_all.items():
            guess = (p_ is not None and p_ > tau)
            ok += (guess == truth[c])
        if ok > best[1]:
            best = (tau, ok, i)
    n_hit = best[1]
    print(f"  「一律 VALID」= {sum(1 for v in truth.values() if v)}/{len(truth)}"
          f" = {base_acc * 100:.1f}%")
    print(f"  键级预测器最好 τ = {best[0]:.2f} ⇒ 正确 {int(n_hit)}/{len(truth)}"
          f" = {n_hit / len(truth) * 100:.1f}%")
    print(f"  τ=0（只要历史上出现过 1.0 就判 VALID）⇒ 正确 "
          f"{sum(1 for c, p_ in pred_all.items() if (p_ is not None and p_ > 0) == truth[c])}"
          f"/{len(truth)}")
    b5 = ("(a) ≤28/33" if n_hit <= 28 else ("(b) 29~31" if n_hit <= 31 else "(c) ≥32"))
    print(f"  预注册桶 W5：押 (a) ⇒ 实际 {b5}（{int(n_hit)}/33）")

    # ------------------------------------------------ W6 误差量级
    print("\n## §6 W6：替代的误差有多大")
    def err_stats(scope: str) -> tuple[float, float, int]:
        errs = []
        for r in cells33:
            if scope == "b2":
                cs = [x for x in cells33 if x["cell"] != r["cell"]]
            elif scope == "b1":
                cs = [x for x in cells33 if x["tid"] == r["tid"] and x["key"] == r["key"]
                      and x["cell"] != r["cell"]]
            else:
                cs = [x for x in cells33 if x["tid"] == r["tid"] and x["cell"] != r["cell"]]
            ph = pooled([(x["k"], x["n"]) for x in cs])
            if ph is not None:
                errs.append(abs(r["p"] - ph))
        errs.sort()
        m = sum(errs) / len(errs)
        return m, errs[int(0.9 * len(errs)) - 1], len(errs)

    stats = {}
    for tag, sc in (("B0 题级", "b0"), ("B1 键级", "b1"), ("B2 跨题池化", "b2")):
        m, q90, used = err_stats(sc)
        stats[sc] = m
        rel = m / med_p if med_p else float("inf")
        print(f"  {tag:10s} MAE {m:.4f}｜p90 绝对误差 {q90:.4f}"
              f"｜MAE/median(p) = {rel * 100:.0f}%｜格 {used}")
    mae_b1 = stats["b1"]
    rel_b1 = mae_b1 / med_p if med_p else float("inf")
    b6 = ("(a) >100%" if rel_b1 > 1.0 else ("(b) 50~100%" if rel_b1 >= 0.5 else "(c) <50%"))
    print(f"  ⭐ MAE(B1) / median(p) = {rel_b1 * 100:.0f}%｜预注册桶 W6：押 (a) >100% ⇒ 实际 {b6}")

    # ------------------------------------------------ 描述：p 与 s0
    print("\n## §7 描述（不押）：p 是不是「实现本身好不好」的代理")
    for t in sorted({r["tid"] for r in cells33}):
        sub = [r for r in cells33 if r["tid"] == t]
        rho = spearman([r["s0"] for r in sub], [r["p"] for r in sub])
        print(f"  {t:22s} Spearman(s0, p) = {rho:+.3f}（n={len(sub)}）")
    hi = [r["p"] for r in cells33 if r["base_correct"]]
    lo = [r["p"] for r in cells33 if not r["base_correct"]]
    print(f"  基线已 1.0 的实现：格 {len(hi)}｜p 中位 {sorted(hi)[len(hi) // 2]:.4f}")
    print(f"  基线未到 1.0 的实现：格 {len(lo)}｜p 中位 {sorted(lo)[len(lo) // 2]:.4f}")

    # ------------------------------------------------ 判据汇总
    print("\n## §8 判据（PREREG §2）")
    print(f"  W1 {'✅' if w1 else '⛔'} 自校验：实例 {n_inst}、有实例格 {len(cells33)}、"
          f"三值 {tab['VALID']}/{tab['INVALID']}/{tab['UNTRIED']}、键 {len(keys)}、"
          f"33/33 p<1.0 = {all_below1}")
    print(f"  W2 {'✅' if b2.startswith('(a)') else '⛔'} p 中位 {med_p:.3f} ⇒ {b2}")
    print(f"  W3 {'✅' if n_2x == 6 else '⛔'} max/min ≥2× 的键 {n_2x}/6 ⇒ {b3}")
    print(f"  W4 {'✅' if b4.startswith('(a)') else '⛔'} Δ = {d_obs:+.4f}"
          f"（零分布 p5 {q05:+.4f} / p95 {q95:+.4f}）⇒ {b4}")
    print(f"  W5 {'✅' if n_hit <= 28 else '⛔'} 最好准确率 {int(n_hit)}/33 vs 一律 "
          f"VALID {sum(1 for v in truth.values() if v)}/33 ⇒ {b5}")
    print(f"  W6 {'✅' if rel_b1 > 1.0 else '⛔'} 相对误差 {rel_b1 * 100:.0f}% ⇒ {b6}")

    out = dict(
        n_inst=n_inst, n_cells=len(cells33), tab=dict(tab), n_keys=len(keys),
        all_p_below1=all_below1, med_p=med_p, mean_p=sum(ps) / len(ps),
        p10=ps[len(ps) // 10], p90=ps[9 * len(ps) // 10],
        n_p0=sum(1 for x in ps if x == 0),
        key_ratio_2x=n_2x, bucket_w2=b2, bucket_w3=b3, bucket_w4=b4, bucket_w5=b5, bucket_w6=b6,
        delta_obs=d_obs, null_p5=q05, null_p50=null[len(null) // 2], null_p95=q95,
        delta_inside=inside, delta_eqw=d_eqw, delta_nosmall=d_nosmall,
        mae_b1=mae_b1, rel_b1=rel_b1, acc_key=int(n_hit), acc_base=base_acc,
        best_tau=best[0],
        detail={t: dict(m0=v[0], m1=v[1], n0=v[2], n1=v[3]) for t, v in detail.items()},
        cells=[dict(cell=list(r["cell"]), n=r["n"], k=r["k"], p=r["p"], s0=r["s0"],
                    base=r["base_correct"], max_c=r["max_c"]) for r in rows],
        w1=w1, wall_s=time.time() - t_start)
    (HERE / "readings.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print(f"\n  已落盘 `readings.json`（{out['wall_s']:.1f}s）")


if __name__ == "__main__":
    main()
