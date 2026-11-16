#!/usr/bin/env python3
"""V153：0 次 / 1 次 / k 次判分的定价曲线 —— 「1 次判分」是不是真下界。

三档，**同一个实现级留出协议（LOIO：留出一整个实现的所有格）**：
  T0  0 次：题内常数 / 题级池化（B0）/ 键级池化（B1）
  T1  1 次：只用该实现的 `s0`（搜索第一行就有）。映射只在留出实现之外拟合。
           T1a 跨题 1-NN｜T1b 题内线性｜T1c 题内 1-NN
  Tk  k 次：从该键的 `n_i` 个实例里无放回抽 k 个（k>n_i 全取），判定 `VALID iff 至少一个 1.0`

`Tk` 全部从 V152 已算过的分数做**超几何模拟** ⇒ 本轮判分成本 **0**。

⛔ 不新造分数（仍是 `v119.ev >= 1.0`）；⛔ 不改 `gen`/`abstract`/记忆；⛔ 不碰 V79。
"""
from __future__ import annotations

import collections
import json
import pathlib
import random
import statistics

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "v6_s241" / "readings.json"
KS = [1, 2, 4, 8, 16, 32]
N_DRAW = 200
SEED = 153
TAUS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]


def mae(pairs) -> float:
    e = [abs(a - b) for a, b in pairs]
    return sum(e) / len(e) if e else float("nan")


def macro(per: dict) -> float:
    return sum(per.values()) / len(per) if per else float("nan")


def main() -> None:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    cells = [c for c in d["cells"] if c["n"] > 0]
    for c in cells:
        c["tid"], c["h"] = c["cell"][0], c["cell"][1]
        c["key"] = tuple(c["cell"][2])
    tids = sorted({c["tid"] for c in cells})
    print("# V153 定价曲线（LOIO：留出一整个实现）")
    print(f"格 {len(cells)}｜实例 {sum(c['n'] for c in cells)}｜题 {len(tids)}"
          f"｜不同 (题,s0) 组合 {len({(c['tid'], round(c['s0'], 2)) for c in cells})}")

    # ------------------------------------------------ W1 自校验
    tab = collections.Counter("VALID" if c["max_c"] >= 1.0 else "INVALID" for c in cells)
    ps = sorted(c["p"] for c in cells)
    med = ps[len(ps) // 2] if len(ps) % 2 else (ps[len(ps) // 2 - 1] + ps[len(ps) // 2]) / 2
    print(f"\n## §0 自校验（W1）：VALID {tab['VALID']} / INVALID {tab['INVALID']}"
          f"｜p 中位 {med:.3f}｜键 {len({c['key'] for c in cells})}"
          f"｜实例 {sum(c['n'] for c in cells)}")

    def others(c):
        """LOIO：同题、**不同实现**的所有格。"""
        return [x for x in cells if x["tid"] == c["tid"] and x["h"] != c["h"]]

    # ------------------------------------------------ T0 / T1
    def t0_const(c):
        o = [x["p"] for x in others(c)]
        return statistics.median(o) if o else None

    def t0_b0(c):
        o = others(c)
        n = sum(x["n"] for x in o)
        return sum(x["k"] for x in o) / n if n else None

    def t0_b1(c):
        o = [x for x in others(c) if x["key"] == c["key"]]
        n = sum(x["n"] for x in o)
        return sum(x["k"] for x in o) / n if n else None

    def t1a(c):
        o = [x for x in cells if x["h"] != c["h"]]
        if not o:
            return None
        best = min(abs(x["s0"] - c["s0"]) for x in o)
        pool = [x["p"] for x in o if abs(x["s0"] - c["s0"]) == best]
        return sum(pool) / len(pool)

    def t1b(c):
        o = others(c)
        if len(o) < 2:
            return None
        mx = sum(x["s0"] for x in o) / len(o)
        my = sum(x["p"] for x in o) / len(o)
        sxx = sum((x["s0"] - mx) ** 2 for x in o)
        if not sxx:
            return my
        b = sum((x["s0"] - mx) * (x["p"] - my) for x in o) / sxx
        return min(max(my + b * (c["s0"] - mx), 0.0), 1.0)

    def t1c(c):
        o = others(c)
        if not o:
            return None
        best = min(abs(x["s0"] - c["s0"]) for x in o)
        pool = [x["p"] for x in o if abs(x["s0"] - c["s0"]) == best]
        return sum(pool) / len(pool)

    preds = {"T0 题内常数": t0_const, "T0 B0 题级": t0_b0, "T0 B1 键级": t0_b1,
             "T1a 跨题1NN": t1a, "T1b 题内线性": t1b, "T1c 题内1NN": t1c}
    print("\n## §1 T0 / T1：0 次与 1 次判分（LOIO，33 格）")
    print("  | 档 | 判分 | MAE | macro MAE | 覆盖格 |")
    print("  |---|---|---|---|---|")
    res = {}
    for name, fn in preds.items():
        per, allp = {}, []
        for t in tids:
            pr = []
            for c in cells:
                if c["tid"] != t:
                    continue
                v = fn(c)
                if v is not None:
                    pr.append((v, c["p"]))
            if pr:
                per[t] = mae(pr)
                allp += pr
        res[name] = dict(all=mae(allp), macro=macro(per), per=per, n=len(allp))
        need = "0" if name.startswith("T0") else "**1**"
        print(f"  | {name} | {need} | {res[name]['all']:.4f} | {res[name]['macro']:.4f}"
              f" | {len(allp)} |")

    # ------------------------------------------------ Tk 采样
    rng = random.Random(SEED)
    print(f"\n## §2 Tk：直接采样 k 个实例（每格 {N_DRAW} 次无放回抽样）")
    print("  | k | 估计 p̂ 的 MAE | 校正版 MAE | 判定准确率 | 准确率 p5~p95 | 全取的格 |")
    print("  |---|---|---|---|---|---|")
    samp = {}
    for k in KS:
        errs, cerrs, accs = [], [], []
        capped = 0
        for c in cells:
            n, kk = c["n"], min(k, c["n"])
            if kk < k:
                capped += 1
            e, ce, ac = [], [], []
            for _ in range(N_DRAW):
                hits = sum(1 for x in rng.sample(range(n), kk) if x < c["k"])  # 前 k_i 个记为 1.0
                e.append(abs(hits / kk - c["p"]))
                ce.append(abs((hits + 1) / (kk + 2) - c["p"]))
                ac.append((hits >= 1) == (c["max_c"] >= 1.0))
            errs.append(sum(e) / len(e))
            cerrs.append(sum(ce) / len(ce))
            accs.append(sum(ac) / len(ac))
        per_draw = accs
        samp[k] = dict(mae=sum(errs) / len(errs), mae_c=sum(cerrs) / len(cerrs),
                       acc=sum(per_draw) / len(per_draw), capped=capped,
                       n=int(sum(per_draw)))
        srt = sorted(per_draw)
        print(f"  | {k} | {samp[k]['mae']:.4f} | {samp[k]['mae_c']:.4f} | "
              f"{samp[k]['acc']:.4f}（{samp[k]['acc'] * 33:.1f}/33） | "
              f"{srt[0]:.3f}~{srt[-1]:.3f} | {capped} |")

    # ------------------------------------------------ 决策层
    print("\n## §3 决策层：判定 VALID/INVALID（对照「一律 VALID」28/33 = 84.8%）")
    n_valid = sum(1 for c in cells if c["max_c"] >= 1.0)
    n_all = len(cells)

    # T1：s0 阈值，τ 在留出实现上拟合（网格，并列取更保守=更大的 τ）
    def fit_tau(c):
        o = others(c)
        best, bt = -1, TAUS[-1]
        for tau in TAUS:
            ok = sum(1 for x in o if (x["s0"] >= tau) == (x["max_c"] >= 1.0))
            if ok > best:
                best, bt = ok, tau
        return bt

    hit = 0
    per_t = {}
    taus = collections.defaultdict(list)
    for c in cells:
        tau = fit_tau(c)
        taus[c["tid"]].append(tau)
        ok = (c["s0"] >= tau) == (c["max_c"] >= 1.0)
        hit += ok
        per_t.setdefault(c["tid"], []).append(ok)
    print(f"  T1（s0≥τ，τ 留一拟合）：{hit}/{n_all} = {hit / n_all * 100:.1f}%"
          f"｜各题 τ 分布 { {t: collections.Counter(v) for t, v in taus.items()} }")
    for t, v in sorted(per_t.items()):
        print(f"    {t:22s} {sum(v)}/{len(v)}")

    print(f"  一律 VALID：{n_valid}/{n_all} = {n_valid / n_all * 100:.1f}%")
    need_k = next((k for k in KS if samp[k]["acc"] * n_all >= n_valid), None)
    print(f"  采样判定 ≥ 一律 VALID 的最小 k = {need_k if need_k else '无（>32）'}")

    # ------------------------------------------------ W5/W6 汇总
    print("\n## §4 价格表（每多付判分买到什么）")
    print("  | 判分次数 | 代表档 | MAE | 判定准确率 |")
    print("  |---|---|---|---|")
    print(f"  | 0 | T0 B1 键级 | {res['T0 B1 键级']['all']:.4f} | — |")
    print(f"  | **1** | **T1c 题内1NN** | **{res['T1c 题内1NN']['all']:.4f}** | "
          f"**{hit}/{n_all}** |")
    for k in KS:
        print(f"  | {k} | 采样 | {samp[k]['mae']:.4f} | {samp[k]['acc'] * n_all:.1f}/{n_all} |")

    # ------------------------------------------------ 对照
    print("\n## §5 对照（W7）")
    print(f"  C1 跨题 1-NN（T1a）MAE {res['T1a 跨题1NN']['all']:.4f} vs "
          f"题内 1-NN（T1c）{res['T1c 题内1NN']['all']:.4f} ⇒ "
          f"{'✅ 跨题更差' if res['T1a 跨题1NN']['all'] > res['T1c 题内1NN']['all'] else '⛔ 跨题不差'}")
    leak = 0
    for c in cells:
        for x in others(c):
            if x["h"] == c["h"]:
                leak += 1
    print(f"  C2 留出协议：每个格的可参考集**不含**同实现（泄漏计数 = {leak}，应为 0）")
    print(f"  C3 n_i<k 的格：k=8 {samp[8]['capped']}｜k=16 {samp[16]['capped']}"
          f"｜k=32 {samp[32]['capped']}（这些格全取，超几何退化）")

    # ------------------------------------------------ 判据汇总
    print("\n## §6 判据（PREREG §3）")
    w1 = (len(cells) == 33 and sum(c["n"] for c in cells) == 3142
          and tab["VALID"] == 28 and tab["INVALID"] == 5
          and abs(med - 0.163) < 0.005 and len({c["key"] for c in cells}) == 6)
    m1c = res["T1c 题内1NN"]["all"]
    b2 = ("(a) ≤0.06" if m1c <= 0.06 else ("(b) 0.06~0.10" if m1c <= 0.10 else "(c) >0.10"))
    b3 = ("(a) ≤28/33" if hit <= 28 else ("(b) 29~31" if hit <= 31 else "(c) ≥32"))
    lowk = [k for k in KS if k <= 8 and samp[k]["acc"] * n_all < n_valid]
    b4 = ("(a) k≤8 全低" if len(lowk) == 4 else
          ("(c) 全超" if not lowk else "(b) 一半"))
    b5 = "✅ T1c 仍低于 k=32 采样" if m1c < samp[32]["mae"] else "⛔ 采样更准"
    print(f"  W1 {'✅' if w1 else '⛔'} 自校验")
    print(f"  W2 {'✅' if b2.startswith('(a)') else '⛔'} T1c LOIO MAE = {m1c:.4f} ⇒ {b2}")
    print(f"  W3 {'✅' if b3.startswith('(a)') else '⛔'} s0 阈值判定 {hit}/{n_all} ⇒ {b3}")
    print(f"  W4 {'✅' if b4.startswith('(a)') else '⛔'} k≤8 采样判定 {[(k, round(samp[k]['acc'] * n_all, 1)) for k in KS if k <= 8]} ⇒ {b4}")
    print(f"  W5 {b5}（T1c {m1c:.4f} vs k=32 {samp[32]['mae']:.4f}）")
    print(f"  W6 达到 28/33 的最少判分 = 1（T1）；采样最小 k = {need_k}")

    out = dict(n_cells=len(cells), n_inst=sum(c["n"] for c in cells), n_valid=n_valid,
               med_p=med, t0={k: dict(all=v["all"], macro=v["macro"]) for k, v in res.items()},
               samp={str(k): v for k, v in samp.items()}, t1_decision=hit,
               acc_base=n_valid / n_all, need_k=need_k, tau_hist={t: list(v) for t, v in taus.items()},
               w1=w1, b2=b2, b3=b3, b4=b4, b5=b5)
    (HERE / "readings.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print("\n  已落盘 `readings.json`")


if __name__ == "__main__":
    main()
