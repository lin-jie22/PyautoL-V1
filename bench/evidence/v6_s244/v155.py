#!/usr/bin/env python3
"""V155：V154 的 `n` 增量是「特征」还是「g7d3 的 8 个格」—— 分辨率 / 集中度 / 置换零假设。

三个臂（同一 LOIO、同一规则族 `VALID iff s0>=τ1 and n>=τ2`）：
  M_c  n = n_c（满 d=2 闭包）
  M_a  n = n_a（部署搜索实际判过的实例数）
  M_0  n = 常数（只留 s0 阈值）= V154 的 M0
  M_perm  题内重排 n_c，200 次 ⇒ 观测准确率在零分布的位置

⛔ 不新造分数（复用 V152/V151 落盘的 n、n_a、s0、p）；⛔ 不改 gen/abstract/记忆；⛔ 不碰 V79。
"""
from __future__ import annotations

import collections
import json
import pathlib
import random

HERE = pathlib.Path(__file__).resolve().parent
C = HERE.parent / "v6_s241" / "readings.json"    # n, k, p, s0
A = HERE.parent / "v6_s240" / "readings.json"    # n_a
TAUS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
N_PERM = 200
SEED = 155


def key_of(cell):
    return (cell[0], cell[1], tuple(cell[2]))


def fit_and_score(cells, nfield: str):
    """逐留出实现拟合 (τ1,τ2)，返回 (准确率, 逐格预测, 逐格命中)。"""
    hit, pred, detail = 0, {}, {}
    for c in cells:
        o = [x for x in cells if x["tid"] == c["tid"] and x["h"] != c["h"]]
        cands = []
        for t1 in TAUS:
            for t2 in sorted({x[nfield] for x in o}):
                ok = sum(1 for x in o if ((x["s0"] >= t1) and (x[nfield] >= t2)) == x["valid"])
                cands.append((ok, t1, t2))
        best = max(x[0] for x in cands)
        t1, t2 = max([(a, b) for ok, a, b in cands if ok == best])   # 并列取更大的 τ（V153 预注册口径）
        g = (c["s0"] >= t1) and (c[nfield] >= t2)
        pred[c["cell"][1] + c["cell"][2][0]] = g
        detail[c["cell"][1] + c["cell"][2][0]] = (g == c["valid"])
        hit += g == c["valid"]
    return hit, pred, detail


def main() -> None:
    c = json.loads(C.read_text(encoding="utf-8"))
    a = json.loads(A.read_text(encoding="utf-8"))
    cells = [x for x in c["cells"] if x["n"] > 0]
    na = {key_of(x["cell"]): x["n_a"] for x in a["cells"]}
    for x in cells:
        x["tid"], x["h"] = x["cell"][0], x["cell"][1]
        x["valid"] = x["max_c"] >= 1.0
        x["na"] = na.get(key_of(x["cell"]), 0)
        x["nconst"] = 1
    n_base = sum(1 for x in cells if x["valid"])
    print(f"# V155：`n` 增量的三个可疑点")
    print(f"格 {len(cells)}｜一律 VALID {n_base}/33")

    r = {}
    for tag, fld in (("M0 只 s0", "nconst"), ("Mc 满枚举 n_c", "n"),
                     ("Ma 部署 n_a", "na")):
        hit, pred, detail = fit_and_score(cells, fld)
        r[tag] = (hit, pred, detail)
        print(f"\n## {tag}：{hit}/33 = {hit / 33 * 100:.1f}%")
        errs = [x for x in cells if not detail[x["cell"][1] + x["cell"][2][0]]]
        print("  误判：" + "；".join(
            f"{x['cell'][0][:10]}::{x['h']}(s0={x['s0']:.2f},n_c={x['n']},n_a={x['na']})"
            for x in errs))

    # ------------------------------------------------ W4 集中度
    print("\n## §2 W4 增益集中度：从 M0 到 Mc 修好了哪些格")
    gain = [x for x in cells
            if not r["M0 只 s0"][2][x["cell"][1] + x["cell"][2][0]]
            and r["Mc 满枚举 n_c"][2][x["cell"][1] + x["cell"][2][0]]]
    loss = [x for x in cells
            if r["M0 只 s0"][2][x["cell"][1] + x["cell"][2][0]]
            and not r["Mc 满枚举 n_c"][2][x["cell"][1] + x["cell"][2][0]]]
    print(f"  修好 {len(gain)} 格：{[x['cell'][1] + ':' + x['cell'][0][:4] for x in gain]}")
    print(f"  弄坏 {len(loss)} 格：{[x['cell'][1] + ':' + x['cell'][0][:4] for x in loss]}")
    tids_gain = collections.Counter(x["cell"][0] for x in gain)
    b4 = ("(a) 全在 g7d3" if set(tids_gain) == {"g7d3_fmt_amount"} else
          ("(c) 不在 g7d3" if "g7d3_fmt_amount" not in tids_gain else "(b) 部分"))

    # ------------------------------------------------ W3 置换零假设
    print(f"\n## §3 W3 置换零假设：题内重排 `n_c`，{N_PERM} 次")
    rng = random.Random(SEED)
    by_tid = collections.defaultdict(list)
    for x in cells:
        by_tid[x["tid"]].append(x)
    obs = r["Mc 满枚举 n_c"][0]
    null = []
    for _ in range(N_PERM):
        perm = {}
        for t, cs in by_tid.items():
            vals = [x["n"] for x in cs]
            rng.shuffle(vals)
            for x, v in zip(cs, vals):
                perm[id(x)] = v
        for x in cells:
            x["nperm"] = perm[id(x)]
        null.append(fit_and_score(cells, "nperm")[0])
    null.sort()
    ge = sum(1 for x in null if x >= obs) / len(null)
    print(f"  观测 Mc = {obs}/33｜零分布：p5 {null[int(0.05 * N_PERM)]}｜中位 "
          f"{null[N_PERM // 2]}｜p95 {null[int(0.95 * N_PERM) - 1]}｜max {null[-1]}")
    print(f"  置换 p 值（≥观测的比例）= {ge:.3f}")
    b3 = ("(a) p<0.01" if ge < 0.01 else ("(b) 0.01~0.10" if ge <= 0.10 else "(c) >0.10"))
    for x in cells:
        del x["nperm"]

    # ------------------------------------------------ 判据
    print("\n## §4 判据（PREREG §3）")
    h0, hc, ha = r["M0 只 s0"][0], r["Mc 满枚举 n_c"][0], r["Ma 部署 n_a"][0]
    w1 = (h0 == 29 and hc == 32 and n_base == 28)
    b2 = ("(a) ≤30" if ha <= 30 else ("(b) 31" if ha == 31 else "(c) ≥32"))
    print(f"  W1 {'✅' if w1 else '⛔'} 自校验：M0 {h0}/33、Mc {hc}/33、一律 VALID {n_base}/33")
    print(f"  W2 {'✅' if b2.startswith('(a)') else '⛔'} 部署分辨率 Ma = {ha}/33 ⇒ {b2}")
    print(f"  W3 {'✅' if b3.startswith('(b)') else '⛔'} 置换 p = {ge:.3f} ⇒ {b3}")
    print(f"  W4 {'✅' if b4.startswith('(a)') else '⛔'} 增益集中度 ⇒ {b4}")
    print(f"  W5 代价：满枚举 8.2s/25 impl ≈ 0.33s/impl、0 次判分；部署只看 1~8 个实例")

    out = dict(n_cells=len(cells), n_base=n_base, m0=h0, mc=hc, ma=ha,
               perm_p=ge, null_p5=null[int(0.05 * N_PERM)], null_med=null[N_PERM // 2],
               null_p95=null[int(0.95 * N_PERM) - 1], null_max=null[-1],
               gain=[x["cell"][1] for x in gain], loss=[x["cell"][1] for x in loss],
               b2=b2, b3=b3, b4=b4, w1=w1)
    (HERE / "readings.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print("\n  已落盘 `readings.json`")


if __name__ == "__main__":
    main()
