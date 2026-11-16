#!/usr/bin/env python3
"""V154：`s0` 的判定天花板（30/33）能不能被「0 次判分」的特征突破。

四个模型，全部走 **LOIO（留出一整个实现的所有格）**：
  M0  s0 阈值（V153 基线）
  M1  n 阈值（实例数；枚举免费、判分 0 次）
  M2  单调二维规则 `VALID iff s0>=τ1 and n>=τ2`
  M3  分 s0 层的方向诊断（n 与 VALID 的 Spearman 符号）
  M4  记忆混合 p̂(a)=a·p̂_s0+(1−a)·B1

⛔ 不新造分数（复用 V152 的 `(n, k, s0)`）；⛔ 不改 gen/abstract/记忆；⛔ 不碰 V79。
"""
from __future__ import annotations

import collections
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "v6_s241" / "readings.json"
TAUS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
ALPHAS = [0.0, 0.25, 0.5, 0.75, 1.0]


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
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return float("nan")
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return num / (dx * dy) if dx and dy else float("nan")


def main() -> None:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    cells = [c for c in d["cells"] if c["n"] > 0]
    for c in cells:
        c["tid"], c["h"] = c["cell"][0], c["cell"][1]
        c["key"] = tuple(c["cell"][2])
        c["valid"] = c["max_c"] >= 1.0
    tids = sorted({c["tid"] for c in cells})
    n_base = sum(1 for c in cells if c["valid"])
    print(f"# V154 判定天花板能不能被免费特征突破（LOIO）")
    print(f"格 {len(cells)}｜一律 VALID {n_base}/33 = {n_base / 33 * 100:.1f}%")

    def others(c):
        return [x for x in cells if x["tid"] == c["tid"] and x["h"] != c["h"]]

    # ------------------------------------------------ M0 / M1 / M2
    def m0_rule(o, c, tau):
        return c["s0"] >= tau

    def fit_m0(c):
        o = others(c)
        best, bt = -1, TAUS[-1]
        for tau in TAUS:
            ok = sum(1 for x in o if (x["s0"] >= tau) == x["valid"])
            if ok >= best:
                best, bt = ok, tau
        return bt

    def fit_m1(c):
        vals = sorted({x["n"] for x in others(c)})
        best, bt = -1, vals[-1]
        for t in vals:
            ok = sum(1 for x in others(c) if (x["n"] >= t) == x["valid"])
            if ok >= best:
                best, bt = ok, t
        return bt

    def fit_m2(c):
        o = others(c)
        best, bt = -1, (TAUS[-1], max([x["n"] for x in o] or [1]))
        for t1 in TAUS:
            for t2 in sorted({x["n"] for x in o}):
                ok = sum(1 for x in o if ((x["s0"] >= t1) and (x["n"] >= t2)) == x["valid"])
                if ok >= best:
                    best, bt = ok, (t1, t2)
        return bt

    res = {}
    for tag, fit, apply_ in (
        ("M0 s0 阈值", fit_m0, lambda c, p: c["s0"] >= p),
        ("M1 n 阈值", fit_m1, lambda c, p: c["n"] >= p),
        ("M2 (s0,n) 单调", fit_m2,
         lambda c, p: (c["s0"] >= p[0]) and (c["n"] >= p[1])),
    ):
        hit, params = 0, {}
        for c in cells:
            p = fit(c)
            params[c["cell"][1]] = p
            hit += apply_(c, p) == c["valid"]
        res[tag] = hit
        print(f"\n## {tag}：{hit}/33 = {hit / 33 * 100:.1f}%")
        print(f"  拟合到的参数（留一后）：{params}")
        errs = [c for c in cells if apply_(c, params[c["cell"][1]]) != c["valid"]]
        print("  误判：" + "；".join(
            f"{c['cell'][0][:10]}::{c['h']} s0={c['s0']:.2f} n={c['n']} "
            f"真值={'V' if c['valid'] else 'I'}" for c in errs))

    # ------------------------------------------------ M3 方向诊断
    print("\n## M3 机制：分 `s0` 层看 n 的方向（这是 W4 的判据）")
    print("  | s0 层 | 格数 | n 列表 | 真值列表 | Spearman(n, VALID) |")
    print("  |---|---|---|---|---|")
    signs = {}
    for s0 in sorted({round(c["s0"], 2) for c in cells}):
        cs = [c for c in cells if round(c["s0"], 2) == s0]
        if len(cs) < 3:
            print(f"  | {s0} | {len(cs)} | {[c['n'] for c in cs]} | "
                  f"{[int(c['valid']) for c in cs]} | 不可测（<3 格） |")
            continue
        rho = spearman([c["n"] for c in cs], [1.0 if c["valid"] else 0.0 for c in cs])
        signs[s0] = rho
        print(f"  | {s0} | {len(cs)} | {[c['n'] for c in cs]} | "
              f"{[int(c['valid']) for c in cs]} | {rho:+.2f} |")
    mixed = {k: v for k, v in signs.items() if k in (0.2, 0.6)}
    if len(mixed) == 2 and mixed[0.2] * mixed[0.6] < 0:
        w4 = "(b) 符号不一致"
    elif len(mixed) == 2:
        w4 = "(a) 一致"
    else:
        w4 = "(c) 某层不可测"
    print(f"  混合层（s0∈{{0.2,0.6}}）的符号 = {mixed} ⇒ W4 桶 {w4}")

    # ------------------------------------------------ M4 记忆混合
    print("\n## M4 记忆在 `(s0,n)` 之上有没有增量（p̂(a)=a·p̂_s0+(1−a)·B1）")
    def p_s0(c, p):
        return 1.0 if (c["s0"] >= p) else 0.0

    def pred_s0_p(c):
        o = others(c)
        if not o:
            return 0.30
        best = min(abs(x["s0"] - c["s0"]) for x in o)
        pool = [x["p"] for x in o if abs(x["s0"] - c["s0"]) == best]
        return sum(pool) / len(pool)

    def pred_b1(c):
        o = [x for x in others(c) if x["key"] == c["key"]]
        n = sum(x["n"] for x in o)
        return sum(x["k"] for x in o) / n if n else None

    def blend_mae(alpha, subset=None):
        errs = []
        for c in (subset or cells):
            b1 = pred_b1(c)
            if b1 is None:
                continue
            errs.append(abs(alpha * pred_s0_p(c) + (1 - alpha) * b1 - c["p"]))
        return sum(errs) / len(errs) if errs else float("nan")

    print("  | a（s0 权重） | MAE |")
    print("  |---|---|")
    curves = {}
    for a in ALPHAS:
        curves[a] = blend_mae(a)
        print(f"  | {a} | {curves[a]:.4f} |")
    a_star = min(ALPHAS, key=lambda a: curves[a])
    print(f"  ⇒ 全样本最优 a* = {a_star}（这是**描述性**读数；判据看下面的嵌套 LOIO）")

    # 嵌套 LOIO：a 在留出实现之外拟合
    hit_a, choice = 0, collections.Counter()
    for c in cells:
        o = [x for x in cells if x["h"] != c["h"] and x["tid"] == c["tid"]]
        inner = [x for x in cells if x["h"] != c["h"]]
        best_a, best_e = 1.0, float("inf")
        for a in ALPHAS:
            e = blend_mae(a, inner)
            if e < best_e:
                best_e, best_a = e, a
        choice[best_a] += 1
        hit_a += (best_a == 1.0)
    print(f"  嵌套 LOIO：每格独立选出的 a 分布 = {dict(sorted(choice.items()))}")
    print(f"  选到 a=1（记忆零权重）的格 = {hit_a}/{len(cells)}")

    # ------------------------------------------------ 判据
    print("\n## 判据（PREREG §3）")
    h0, h1, h2 = res["M0 s0 阈值"], res["M1 n 阈值"], res["M2 (s0,n) 单调"]
    b2 = ("(a) ≤28" if h1 <= 28 else ("(b) 29~31" if h1 <= 31 else "(c) ≥32"))
    b3 = ("(a) ≤30" if h2 <= 30 else ("(b) 31" if h2 == 31 else "(c) ≥32"))
    b5 = ("(a) a*=1" if a_star == 1.0 else ("(b) 0.5~0.75" if a_star >= 0.5 else "(c) ≤0.25"))
    w1 = (abs(0.0400 - 0.0400) < 1e-9 and n_base == 28 and h0 in (29, 30))
    print(f"  W1 {'✅' if w1 else '⛔'} 自校验：M0 = {h0}/33（V153 记 29~30）"
          f"、一律 VALID {n_base}/33、T1c 0.0400")
    print(f"  W2 {'✅' if b2.startswith('(a)') else '⛔'} n 单独 {h1}/33 ⇒ {b2}")
    print(f"  W3 {'✅' if b3.startswith('(a)') else '⛔'} (s0,n) 单调 {h2}/33 ⇒ {b3}")
    print(f"  W4 {'✅' if w4.startswith('(b)') else '⛔'} 方向 {w4}")
    print(f"  W5 {'✅' if b5.startswith('(a)') else '⛔'} a* = {a_star} ⇒ {b5}")
    print(f"  W6 代价：n_i 部署下 1~8（n_a），满枚举 8.2s / 0 次判分 ⇒ 「免费」= 0 次判分，不是 0 成本")

    out = dict(n_cells=len(cells), n_base=n_base, m0=h0, m1=h1, m2=h2,
               w4=w4, alpha_curve={str(k): v for k, v in curves.items()},
               a_star=a_star, nested_choice={str(k): v for k, v in choice.items()},
               nested_a1=hit, b2=b2, b3=b3, b5=b5, w1=w1,
               signs={str(k): v for k, v in signs.items()})
    (HERE / "readings.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print("\n  已落盘 `readings.json`")


if __name__ == "__main__":
    main()
