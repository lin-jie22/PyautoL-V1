#!/usr/bin/env python3
"""V154 跑前量测：只量结构（零分数）。两个信息源在**判分前**拿得到吗？

§219 之后剩下的问题是：V153 的 30/33 天花板能不能被**不花钱**的信息突破。
候选两个：
  A. 记忆（键级/题级占比）—— 已付过的判分；
  B. **实例数 `n_i`**（该键在该实现上枚举出几个不同源码）—— 枚举 `gen` 免费，判分 0 次。
本脚本只量：
  P1 `n_i` 与真值的对照表（这是 B 的全部信息）
  P2 `n_i` 在**部署搜索**（A_DEPLOY，V151 `readings.json` 的 `n_a`）里是不是也拿得到
     —— 若部署下只有 1~2 个，那 `n_i` 只在「满枚举」下可用，必须写清代价
  P3 7 个「同 s0 真值相反」的混合格，`n_i` 能不能分开（V153 §5 的三个错误）

⛔ 不调用 `v119.ev`。
"""
from __future__ import annotations

import collections
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
C = HERE.parent / "v6_s241" / "readings.json"      # C_FULL：n, k, p, s0
A = HERE.parent / "v6_s240" / "readings.json"      # A_DEPLOY：n_a


def main() -> None:
    c = json.loads(C.read_text(encoding="utf-8"))
    a = json.loads(A.read_text(encoding="utf-8"))
    cells = [x for x in c["cells"] if x["n"] > 0]
    na = {(x["cell"][0], x["cell"][1], tuple(x["cell"][2])): x["n_a"] for x in a["cells"]}

    print("# V154 跑前量测（零分数）")
    print(f"格 {len(cells)}｜实例 {sum(x['n'] for x in cells)}")

    print("\n## P1 `n_i` 与真值（按 n 分档，全部格）")
    print("  | n | 格数 | VALID | INVALID | 题 |")
    print("  |---|---|---|---|---|")
    g = collections.defaultdict(list)
    for x in cells:
        g[x["n"]].append(x)
    for n, xs in sorted(g.items()):
        v = sum(1 for x in xs if x["max_c"] >= 1.0)
        tids = sorted({x["cell"][0][:4] for x in xs})
        print(f"  | {n} | {len(xs)} | {v} | {len(xs) - v} | {','.join(tids)} |")

    print("\n## P2 部署搜索（A_DEPLOY）下每条键的实例数 `n_a` vs 满枚举 `n_c`")
    pairs = [(na.get((x["cell"][0], x["cell"][1], tuple(x["cell"][2])), 0), x["n"]) for x in cells]
    print(f"  逐格对数（n_a, n_c）前 12 组：{pairs[:12]}")
    print(f"  n_a 的分布 = {dict(sorted(collections.Counter(p[0] for p in pairs).items()))}")
    print(f"  n_c 的分布（分档）= n≤12: {sum(1 for p in pairs if p[1] <= 12)}"
          f"｜n>12: {sum(1 for p in pairs if p[1] > 12)}")
    print(f"  ⇒ 部署下每条键只判 1~{max(p[0] for p in pairs)} 个实例 ⇒ "
          f"`n_i` 只有**满枚举**才拿得到（枚举 8.2s、判分 0 次，见 V151 P3）")

    print("\n## P3 7 个混合格（同 s0、真值相反）：`n_i` 能不能分开")
    mixed = [x for x in cells if round(x["s0"], 2) in (0.2, 0.6)]
    print("  | 题 | 实现 | s0 | n | 真值 |")
    print("  |---|---|---|---|---|")
    for x in sorted(mixed, key=lambda x: (x["s0"], -x["max_c"])):
        print(f"  | {x['cell'][0][:12]} | {x['cell'][1]} | {x['s0']:.2f} | {x['n']} | "
              f"{'VALID' if x['max_c'] >= 1.0 else 'INVALID'} |")
    print("  ⇒ 若 VALID 的 n 普遍更大，则「抽签次数」可分开它们；否则不能。")


if __name__ == "__main__":
    main()
