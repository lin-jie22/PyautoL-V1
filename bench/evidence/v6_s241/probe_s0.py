#!/usr/bin/env python3
"""V152 探索性补测（⭐ **跑后**，不是预注册判据）：`p` 是不是由**免费量** `s0` 决定的？

动机：主读数 §7 描述里 `Spearman(s0, p)` 在四个题上 = +0.775 / +0.667 / +0.893 / +1.000。
若 `p` 几乎被 `s0` 单调决定，那「键级历史占比」这条廉价代理不仅**没有边际信息**（W4），
还**打不过一个 0 次判分就能拿到的数** —— 因为 `s0` 就是搜索第一行 `s0, _ = V.ev(tid, code)`。

⚠️ 分母纪律（46）：所有预测器**必须在同一批格上比**。这里分两组报：
  `all33`   = 全部有实例的 33 格
  `dual21`  = 两个双键题的 21 格（B0/B1 能定义的那批，W4 用的同一批）
⛔ 本文件是**探索性**读数，不作为本轮判据，只给下一轮写预注册（冲突只认预注册，纪律 1）。
⛔ 不新造分数：`p` 与 `s0` 都是既有 `v119.ev` 的输出。
"""
from __future__ import annotations

import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent


def mae(pairs):
    e = [abs(a - b) for a, b in pairs]
    return sum(e) / len(e) if e else float("nan")


def predict(cs, i, name):
    """留一法：用同题其他格预测第 i 格。name ∈ {const, step, 1nn, linear, b0, b1}。"""
    c = cs[i]
    oth = [x for j, x in enumerate(cs) if j != i]
    if name == "const":
        return c["_global_med"]
    if name == "step":
        pool = [x["p"] for x in oth if x["base"]] if c["base"] else \
               [x["p"] for x in oth if not x["base"]]
        pool = pool or [x["p"] for x in oth]
        return sum(pool) / len(pool)
    if name == "1nn":
        best = min(abs(x["s0"] - c["s0"]) for x in oth)
        pool = [x["p"] for x in oth if abs(x["s0"] - c["s0"]) == best]
        return sum(pool) / len(pool)
    if name == "linear":
        n = len(oth)
        mx = sum(x["s0"] for x in oth) / n
        my = sum(x["p"] for x in oth) / n
        sxx = sum((x["s0"] - mx) ** 2 for x in oth)
        sxy = sum((x["s0"] - mx) * (x["p"] - my) for x in oth)
        b = sxy / sxx if sxx else 0.0
        return min(max(my + b * (c["s0"] - mx), 0.0), 1.0)
    sel = ([x for x in oth] if name == "b0"
           else [x for x in oth if x["key"] == c["key"]])
    k = sum(x["k"] for x in sel)
    n = sum(x["n"] for x in sel)
    return (k / n) if n else None


def evaluate(cells, gmed, label):
    tasks = sorted({c["cell"][0] for c in cells})
    for c in cells:
        c["_global_med"] = gmed
    out = {}
    print(f"\n## 组 `{label}`（{len(cells)} 格，{len(tasks)} 题）")
    print("  | 预测器 | 需要几次判分 | 格等权 MAE | 跨题 macro MAE |")
    print("  |---|---|---|---|")
    for name in ("const", "step", "1nn", "linear", "b0", "b1"):
        per, allp = {}, []
        for t in tasks:
            cs = [c for c in cells if c["cell"][0] == t]
            pairs = []
            for i in range(len(cs)):
                pr = predict(cs, i, name)
                if pr is None:
                    continue
                pairs.append((pr, cs[i]["p"]))
            if pairs:
                per[t] = mae(pairs)
                allp += pairs
        if not allp:
            continue
        out[name] = dict(all=mae(allp), macro=sum(per.values()) / len(per), per=per)
        need = {"const": "0", "step": "1", "1nn": "1", "linear": "1",
                "b0": "已付过的判分", "b1": "已付过的判分"}[name]
        print(f"  | {name} | {need} | {mae(allp):.4f}（{len(allp)} 格） | "
              f"{sum(per.values()) / len(per):.4f} |")
    return out


def main() -> None:
    d = json.loads((HERE / "readings.json").read_text(encoding="utf-8"))
    cells = [c for c in d["cells"] if c["n"] > 0]
    for c in cells:
        c["key"] = tuple(c["cell"][2])
    ps = sorted(c["p"] for c in cells)
    gmed = ps[len(ps) // 2]
    duel = [c for c in cells if c["cell"][0] in ("g7d2_dedupe_keep", "g7e1_parse_flag")]

    print("# V152 探索性：用免费量 `s0` 预测 `p`（跑后，非预注册）")
    print(f"总格 {len(cells)}｜p 中位 {gmed:.4f}｜双键题格 {len(duel)}")
    a = evaluate(cells, gmed, "all33")
    b = evaluate(duel, gmed, "dual21（与 W4 同一批）")

    print("\n## §X 为什么在 `g7d3` 上 `s0` 也不好使：s0 与 p 的逐格对照")
    print("  | 题 | 实现 | s0 | p |")
    print("  |---|---|---|---|")
    for c in sorted([x for x in cells if x["cell"][0] == "g7d3_fmt_amount"],
                    key=lambda x: (-x["s0"], x["cell"][1])):
        print(f"  | g7d3 | {c['cell'][1]} | {c['s0']:.2f} | {c['p']:.3f} |")

    out = dict(gmed=gmed, all33=a, dual21=b)
    (HERE / "readings_s0.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                           encoding="utf-8")


if __name__ == "__main__":
    main()
