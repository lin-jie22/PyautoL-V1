#!/usr/bin/env python3
"""V156：自省能不能定价 —— `1/p̂` 预测「判到第一条 1.0 要几次」的校准。

两分辨率（S2：动态范围差一个量级）：
  A 部署（k=2,R6）  位置 1~8，13 格分母只有 1
  C 满闭包（d=2）   位置 1~n_c（中位 12、最大 233）

预测器（实现级留一 LOIO）：
  P0 训练集实测位置中位数（0 判分）｜P1 s0 最近邻的 p（1 判分）｜P2 同 P1 但用 1/p（均值口径）
  P3 本格自己的 p（不可部署，只作「几何模型」自检）

⛔ 不新造分数；⛔ 不改 gen/abstract/记忆；⛔ 不碰 V79。
"""
from __future__ import annotations

import ast
import collections
import json
import math
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for _d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198", "v6_s210", "v6_s236"):
    sys.path.insert(0, str(EV / _d))

import idiom_transfer as I     # noqa: E402
import search as S             # noqa: E402
import v119 as V               # noqa: E402
import v120 as W               # noqa: E402
import v121 as X               # noqa: E402

BASELINE = ("?", "<baseline>")


def search(tid, h, code, Sx, chains, exprs, mine, rounds, k) -> dict:
    """与 v151.search 逐字同构（重放 A_DEPLOY）。"""
    s0, _ = V.ev(tid, code)
    frontier = [dict(code=code, s=s0)]
    visited = {code}
    n_eval = 0
    events: list[tuple] = []
    depth = 0
    for rnd in range(1, rounds + 1):
        pool, seen_src = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, src)
                     for lab, src in X.gen(tid, node["code"], Sx, chains, exprs)]
            for key, _lab, src in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                if src in visited or src in seen_src:
                    continue
                s, _ = V.ev(tid, src)
                n_eval += 1
                events.append((key, s, src, rnd))
                if s > node["s"]:
                    pool.append(dict(code=src, s=s))
                    seen_src.add(src)
        if not pool:
            break
        depth = rnd
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:k]
        visited |= {d["code"] for d in pool}
    best = max([s0] + [e[1] for e in events]) if events else s0
    return dict(s0=s0, s1=best, n_eval=n_eval, depth=depth, events=events)


def run_arm(impls, corpus, mem, rounds, k):
    out = {}
    for tid, h in impls:
        mine = {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        ch = V.harvest_chains(corpus, tid, h)
        ex = W.harvest_exprs(corpus, tid, h)
        out[(tid, h)] = search(tid, h, corpus[tid][h], Sx, ch, ex, mine, rounds, k)
    return out


def closure2(tid, code, Sx, chains, exprs):
    frontier = {code}
    seen = {code}
    per = collections.defaultdict(set)
    for _ in range(2):
        nxt = set()
        for c in sorted(frontier):
            for lab, src in X.gen(tid, c, Sx, chains, exprs):
                per[X.abstract(lab)].add(src)
                if src not in seen:
                    seen.add(src)
                    nxt.add(src)
        frontier = nxt
    return per, seen


def seq_a(r, key):
    """该键在 A_DEPLOY 事件里的实例，按首次出现去重（位置 = 这条键花掉的第几次判分）。"""
    scores, seen = [], set()
    for k, s, src, _rnd in r["events"]:
        if k != key or src in seen:
            continue
        seen.add(src)
        scores.append(s)
    return scores


def first_pos(scores):
    for i, s in enumerate(scores, 1):
        if s >= 1.0:
            return i
    return None


def geom_med(p):
    if p is None or p <= 0:
        return None
    if p >= 1:
        return 1.0
    return math.log(0.5) / math.log(1.0 - p)


def rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        m = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[order[k]] = m
        i = j + 1
    return r


def spearman(xs, ys):
    if len(xs) < 3:
        return float("nan")
    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return num / (dx * dy) if dx and dy else float("nan")


def med(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else float("nan")


def evaluate(rows, res: str, pred_key: str, truth_key: str, price_key: str) -> dict:
    """对某一分辨率、某一预测器算校准指标。"""
    used = [r for r in rows if r[pred_key] is not None and r[truth_key] is not None]
    ratios = [r[truth_key] / r[price_key] for r in used if r[price_key] and r[price_key] > 0]
    absd = [abs(r[truth_key] - r[price_key]) for r in used]
    lg = [abs(math.log2(r[truth_key] / r[price_key])) for r in used if r[price_key] > 0]
    sp = spearman([r[price_key] for r in used], [r[truth_key] for r in used])
    # 只含有动态范围的格（该分辨率下实例数 >=2）
    used2 = [r for r in used if r["n_" + res] >= 2]
    ratios2 = [r[truth_key] / r[price_key] for r in used2 if r[price_key] > 0]
    return dict(n=len(used), n_dyn=len(used2),
                med_ratio=med(ratios), med_ratio_dyn=med(ratios2),
                med_abs=med(absd), med_abslog2=med(lg), spearman=sp)


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
    mem = json.loads(X.MEM_FILE.read_text(encoding="utf-8"))

    uni_all = set()
    for tid, h in impls:
        for kk in mem[tid]:
            if kk["h"] != h:
                uni_all.add((tid, h, tuple(kk["act"])))
    uni_real = sorted(c for c in uni_all if c[2] != BASELINE)

    V.CACHE.clear()
    t = time.time()
    A = run_arm(impls, corpus, mem, rounds=6, k=2)
    tot_a = sum(r["n_eval"] for r in A.values())
    to1_a = sum(1 for r in A.values() if r["s1"] >= 1.0)
    base = {(t_, h) for (t_, h), r in A.items() if r["s0"] >= 1.0}
    t_a = time.time() - t

    print("## §0 自校验（⛔ 不成立则下面全部作废）")
    print(f"  A_DEPLOY n_eval = {tot_a}（记 2710）｜到 1.0 = {to1_a}/25（记 21）"
          f"｜基线 = {len(base)}/25（记 10）｜真实格 = {len(uni_real)}（记 35）｜重放 {t_a:.1f}s")
    w1 = (tot_a == 2710 and to1_a == 21 and len(base) == 10 and len(uni_real) == 35)

    t = time.time()
    clo = {}
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ch = V.harvest_chains(corpus, tid, h)
        ex = W.harvest_exprs(corpus, tid, h)
        clo[(tid, h)] = closure2(tid, code, Sx, ch, ex)[0]
    t_c = time.time() - t

    rows = []
    for tid, h, key in uni_real:
        sa = seq_a(A[(tid, h)], key)
        pa = first_pos(sa)
        sc = [V.ev(tid, s)[0] for s in sorted(clo[(tid, h)].get(key, set()))]
        pc = first_pos(sc)
        rows.append(dict(tid=tid, h=h, key=key, s0=A[(tid, h)]["s0"],
                         n_a=len(sa), n_c=len(sc), seq_a=sa, pos_a=pa, pos_c=pc,
                         p_a=(sum(1 for s in sa if s >= 1.0) / len(sa)) if sa else None,
                         p_c=(sum(1 for s in sc if s >= 1.0) / len(sc)) if sc else None,
                         base=(tid, h) in base))

    # ------------------------------------------------ §1 删失面
    closed = [r for r in rows if r["pos_a"] is None]
    open_ = [r for r in rows if r["pos_a"] is not None]
    print("\n## §1 删失面（价格只在有 1.0 的格上有定义）")
    print(f"  有 1.0（A 臂）= {len(open_)}｜无 1.0 = {len(closed)}｜真实格 = {len(rows)}"
          f"｜其中 A 臂 0 实例 = {sum(1 for r in closed if r['n_a'] == 0)}")
    print(f"  无 1.0 的格：" + "；".join(
        f"{r['tid'][:10]}::{r['h']}({r['key'][1]},n_a={r['n_a']})" for r in closed))
    print(f"  这些格花掉的判分 = {sum(r['n_a'] for r in closed)}/{sum(r['n_a'] for r in rows)}"
          f" = {sum(r['n_a'] for r in closed) / max(sum(r['n_a'] for r in rows), 1) * 100:.1f}%"
          f"（V157 的「完美预言家」上限就是它）")
    w2 = (len(open_) == 28 and len(closed) == 5)

    # ------------------------------------------------ §2 位置分布
    print("\n## §2 位置分布（这条键花掉第几次判分才见到 1.0）")
    for res, fld in (("a", "pos_a"), ("c", "pos_c")):
        ps = sorted(r[fld] for r in open_ if r[fld] is not None)
        print(f"  {res.upper()}：min {ps[0]}｜median {med(ps)}｜max {ps[-1]}｜"
              f"分布 {dict(sorted(collections.Counter(ps).items()))}")
    print(f"  A 的 n_i 取值 {dict(sorted(collections.Counter(r['n_a'] for r in rows).items()))}"
          f"｜n_a=1 且命中 = {sum(1 for r in open_ if r['n_a'] == 1)} 格")
    w3 = med([r["pos_a"] for r in open_])

    # ------------------------------------------------ §3 预测器
    def pred(rows, res, pfield, mode):
        """LOIO：留出实现 h，用同题其他实现估计 p。mode: 'nn' s0 最近邻 / 'median' 位置中位 / 'self' 本格。"""
        out = []
        for r in rows:
            if mode == "self":
                p = r[pfield]
            elif mode == "median":
                tr = [x for x in rows if x["tid"] == r["tid"] and x["h"] != r["h"]
                      and x["pos_" + res] is not None]
                out.append(dict(cell=(r["tid"], r["h"], r["key"]),
                                price=med([x["pos_" + res] for x in tr]) if tr else None))
                continue
            else:
                tr = [x for x in rows if x["tid"] == r["tid"] and x["h"] != r["h"]]
                if not tr:
                    out.append(dict(cell=(r["tid"], r["h"], r["key"]), price=None))
                    continue
                nn = min(tr, key=lambda x: (abs(x["s0"] - r["s0"]), x["h"]))
                p = nn[pfield]
            if p is None or p <= 0:
                out.append(dict(cell=(r["tid"], r["h"], r["key"]), price=None))
            else:
                g = geom_med(p)
                out.append(dict(cell=(r["tid"], r["h"], r["key"]), price=max(1.0, g)))
        return {c: pr for c, pr in ((d["cell"], d["price"]) for d in out)}

    print("\n## §3 校准（LOIO；倍数误差 = 实测/预测，中位）")
    print("  | 臂 | 预测器 | 判分成本 | 格 | 中位倍数 | 中位倍数(仅 n>=2) | 中位绝对误差 | Spearman |")
    print("  |---|---|---|---|---|---|---|---|")
    res_tab = {}
    for res in ("a", "c"):
        pk = "pos_" + res
        for tag, mode, pf in (("P0 位置中位(历史)", "median", None),
                              ("P1 s0 近邻 p", "nn", "p_" + res),
                              ("P3 本格 p（自检，不可部署）", "self", "p_" + res)):
            pr = pred(rows, res, pf, mode)
            for r in rows:
                r["tmp_price"] = pr.get((r["tid"], r["h"], r["key"]))
            m = evaluate(rows, res, "tmp_price", pk, "tmp_price")
            res_tab[(res, tag)] = m
            dyn = "%.2f×" % m["med_ratio_dyn"] if m["n_dyn"] else "—"
            cost = "0" if mode == "median" else ("n_c" if mode == "self" else "1")
            print(f"  | {res.upper()} | {tag} | {cost} | {m['n']} | {m['med_ratio']:.2f}× | "
                  f"{dyn} | {m['med_abs']:.2f} | {m['spearman']:+.2f} |")
        # P2：1/p（均值口径），只在 C 上算（A 的 n_a 太小，1/p 会常超上界）
        pr = {}
        for r in rows:
            tr = [x for x in rows if x["tid"] == r["tid"] and x["h"] != r["h"]]
            if not tr:
                continue
            nn = min(tr, key=lambda x: (abs(x["s0"] - r["s0"]), x["h"]))
            p = nn["p_" + res]
            if p and p > 0:
                pr[(r["tid"], r["h"], r["key"])] = max(1.0, 1.0 / p)
        for r in rows:
            r["tmp_price"] = pr.get((r["tid"], r["h"], r["key"]))
        m = evaluate(rows, res, "tmp_price", pk, "tmp_price")
        res_tab[(res, "P2 1/p(均值口径)")] = m
        dyn = "%.2f×" % m["med_ratio_dyn"] if m["n_dyn"] else "—"
        print(f"  | {res.upper()} | P2 1/p（均值口径） | 1 | {m['n']} | {m['med_ratio']:.2f}× | "
              f"{dyn} | {m['med_abs']:.2f} | {m['spearman']:+.2f} |")

    # ------------------------------------------------ §4 逐格（A 臂，仅 n>=2）
    print("\n## §4 有动态范围的格（A 臂 n_a>=2，逐格）")
    print("  | 实现 | 键 | s0 | n_a | 序列 | 位置 | P1 价格 |")
    print("  |---|---|---|---|---|---|---|")
    pr = pred(rows, "a", "p_a", "nn")
    for r in rows:
        if r["n_a"] < 2 or r["pos_a"] is None:
            continue
        p = pr.get((r["tid"], r["h"], r["key"]))
        print(f"  | {r['tid'][:10]}::{r['h']} | {r['key']} | {r['s0']:.1f} | {r['n_a']} | "
              f"{[round(x, 1) for x in r['seq_a']]} | {r['pos_a']} | "
              f"{'—' if p is None else f'{p:.1f}'} |")

    # ------------------------------------------------ §5 判据
    print("\n## §5 判据（预注册 §3）")
    p3c = res_tab[("c", "P3 本格 p（自检，不可部署）")]
    p1a = res_tab[("a", "P1 s0 近邻 p")]
    p1c = res_tab[("c", "P1 s0 近邻 p")]

    def band(x, lo, hi):
        return lo <= x <= hi

    def row(tag, ok, txt):
        print(f"  {tag} {'✅' if ok else '⛔'} {txt}")

    row("W1", w1, f"自校验 A {tot_a}/{to1_a}、基线 {len(base)}/25、真实格 {len(uni_real)}")
    row("W2", w2, f"删失面 {len(open_)} 可定价 / {len(closed)} 删失 / {len(rows)}")
    print(f"  W3 {'✅' if w3 == 1 else '⛔'} 部署层位置中位数 = {w3}（押 1）")
    print(f"  W4 {'✅' if band(p3c['med_ratio'], 0.5, 2.0) else '⛔'} "
          f"几何模型自检（P3，C）中位倍数 = {p3c['med_ratio']:.2f}×（押 ≤2×）"
          f"｜仅 n>=2：{p3c['med_ratio_dyn']:.2f}×｜Spearman {p3c['spearman']:+.2f}")
    print(f"  W5 {'✅' if p1a['med_ratio'] > 2.0 or p1a['med_ratio'] < 0.5 else '⛔'} "
          f"部署层可定价（P1，A）中位倍数 = {p1a['med_ratio']:.2f}×（押 >2×）")
    print(f"  W6 {'✅' if p1c['spearman'] >= 0.5 else '⛔'} "
          f"P1 在 C 上能排序：Spearman = {p1c['spearman']:+.2f}（押 ≥0.5）")
    print(f"  W7 {'✅' if p1a['med_abs'] <= 1.0 else '⛔'} "
          f"部署层绝对误差中位 = {p1a['med_abs']:.2f} 次（押 ≤1）")

    print("\n  ⭐ 反证 C1：把 A 的序列**反转**，位置应当变成 n_i+1−pos（恒等式，纪律 68 的对照）")
    rev_ok = 0
    for r in open_:
        rev = list(reversed(r["seq_a"]))
        if r["n_a"] >= 2 and first_pos(rev) == r["n_a"] + 1 - r["pos_a"]:
            rev_ok += 1
    print(f"    反转后位置符合恒等式的格 = {rev_ok}/{sum(1 for r in open_ if r['n_a'] >= 2)}"
          f"（只证明装置读数一致，⛔ 不是证据）")

    out = dict(w1=w1, w2=w2, n_open=len(open_), n_closed=len(closed),
               med_pos_a=w3, med_pos_c=med([r["pos_c"] for r in open_ if r["pos_c"]]),
               tab={f"{k[0]}:{k[1]}": v for k, v in res_tab.items()},
               cells=[dict(tid=r["tid"], h=r["h"], key=list(r["key"]), s0=r["s0"],
                           n_a=r["n_a"], n_c=r["n_c"], pos_a=r["pos_a"], pos_c=r["pos_c"],
                           p_a=r["p_a"], p_c=r["p_c"], seq_a=r["seq_a"]) for r in rows],
               wall_s=time.time() - t0)
    (HERE / "readings.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n  已落盘 readings.json（{out['wall_s']:.1f}s；A 重放 {t_a:.1f}s、C 闭包 {t_c:.1f}s）")


if __name__ == "__main__":
    main()
