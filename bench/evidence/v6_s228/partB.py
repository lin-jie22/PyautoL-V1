#!/usr/bin/env python3
"""V139 Part B：**免费信号**能不能预测「这次改动会不会有人跟着变」。

免费 = 在**改之前**就有、不需要跑判据也不需要跑轨迹：
  ① owner_in_base —— 被改函数在基线轨迹里出现过（说明它至少被调用过一次）
  ② base_n        —— 它的基线调用数
  ③ indeg         —— 静态图里有多少个函数调它

分类：SPILL（27，含 2 个崩溃站——崩本身也是「有人变了」）vs NOOP（54）。
AUC = P(随机一个 SPILL 的分数 > 随机一个 NOOP 的分数)。零分布由标签置换造。
"""
from __future__ import annotations

import json
import pathlib
import random
import statistics
import sys

EVID = pathlib.Path(__file__).resolve().parent
V138 = EVID.parent / "v6_s227"
sys.path.insert(0, str(EVID))


def auc(pos, neg) -> float:
    if not pos or not neg:
        return float("nan")
    tot = 0.0
    for a in pos:
        for b in neg:
            tot += 1.0 if a > b else (0.5 if a == b else 0.0)
    return tot / (len(pos) * len(neg))


def main() -> int:
    tiers = json.loads((EVID / "tiers.json").read_text())
    tm, own = tiers["tiers"], tiers["owner"]
    rows = json.loads((V138 / "analysis.json").read_text())["rows"]
    base = json.loads((V138 / "baseline.json").read_text())["base_per_fn"]

    import callgraph as CG
    g = CG.build(False)
    indeg: dict[str, int] = {}
    for a, bs in g.calls.items():
        for b in bs:
            indeg[b] = indeg.get(b, 0) + 1

    spill = [r["key"] for r in rows if tm[r["key"]] == "SPILL"]
    noop = [r["key"] for r in rows if tm[r["key"]] == "NOOP"]
    print(f"分母：SPILL {len(spill)} 站 ｜ NOOP {len(noop)} 站 ｜ 排除 SELF_ONLY "
          f"{sum(1 for r in rows if tm[r['key']] == 'SELF_ONLY')} 站")

    def feat(key):
        f = own[key]
        n = base.get(f, {}).get("n", 0)
        return {"in_base": 1 if f in base else 0, "base_n": n, "indeg": indeg.get(f, 0),
                "f": f}

    print("\n=== W5：owner 从没被调用过的站，是不是全 NOOP ===")
    never = [k for k in spill + noop if own[k] not in base]
    print(f"owner ∉ base_per_fn 的站: {len(never)}  → "
          f"其中 SPILL {sum(1 for k in never if k in spill)} / NOOP {sum(1 for k in never if k in noop)}")
    for k in never[:10]:
        print(f"   {k:18s} {tm[k]:10s} owner={own[k]}")

    print("\n=== W6：三个免费特征的 AUC ===")
    for name in ("in_base", "base_n", "indeg"):
        pos = [feat(k)[name] for k in spill]
        neg = [feat(k)[name] for k in noop]
        a = auc(pos, neg)
        rng = random.Random(20261115)
        lab = pos + neg
        null = []
        for _ in range(2000):
            rng.shuffle(lab)
            null.append(auc(lab[:len(pos)], lab[len(pos):]))
        p = sum(1 for x in null if x >= a) / len(null)
        print(f"  {name:8s} AUC={a:.3f} 置换 p={p:.4f} "
              f"(零分布均值 {statistics.mean(null):.3f})  "
              f"阳性中位 {statistics.median(pos):.1f} / 阴性中位 {statistics.median(neg):.1f}")

    # 组合：先判 in_base，再判 indeg
    print("\n=== 组合规则（先 in_base，再 indeg>0）===")
    tp = sum(1 for k in spill if feat(k)["in_base"] and feat(k)["indeg"] > 0)
    fp = sum(1 for k in noop if feat(k)["in_base"] and feat(k)["indeg"] > 0)
    print(f"  规则命中 SPILL {tp}/{len(spill)}，误报 NOOP {fp}/{len(noop)} ⇒ "
          f"精度 {tp/(tp+fp) if tp+fp else 0:.3f} 召回 {tp/len(spill):.3f}")

    (EVID / "partB.json").write_text(json.dumps(
        {"never_called": never,
         "auc": {n: auc([feat(k)[n] for k in spill], [feat(k)[n] for k in noop])
                 for n in ("in_base", "base_n", "indeg")},
         "rule": {"tp": tp, "fp": fp}}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
