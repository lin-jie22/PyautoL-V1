"""V253 §1（纯离线，0 模型）：累积通道的**可达性**。

问题：`mw/worldmodel.py:633` 对同一 `task_id` 一律 `continue`
（`rewards_skipped_self`），所以累积只能在**跨题**间按结构特征检索，
门槛 `REWARD_MIN_SIM = 0.30`（Jaccard 相似度，非距离）。

那么：39 题 spec-inline 里，究竟有多少**有序对** (A,B) 满足
`jaccard(feature_set(A), feature_set(B)) >= 0.30`？

若为 0 ⇒ 累积通道在这份题集上**数学上不可能触发**，
任何「跑两轮看第 2 轮涨不涨」的实验都是白跑（纪律 3：先量 B 存在过）。

输出 stdout + `reach_v253.json`
"""
import json
import sys
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
sys.path.insert(0, str(ROOT))
from mw.features import feature_set, feature_version_hash  # noqa: E402
from mw.worldmodel import REWARD_MIN_SIM  # noqa: E402

OUT = ROOT / "bench/evidence/l17_s323" / "reach_v253.json"
SKIP = {"g7d_spec_incomplete.jsonl"}


def load_tasks():
    tasks = []
    for p in sorted((ROOT / "bench" / "tasks").glob("*.jsonl")):
        if p.name in SKIP:
            continue
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line:
                t = json.loads(line)
                t["_suite"] = p.stem
                tasks.append(t)
    return tasks


def jac(a, b):
    u = a | b
    return (len(a & b) / len(u)) if u else 0.0


def main():
    tasks = load_tasks()
    feats = {t["id"]: feature_set(t) for t in tasks}
    print(f"题数={len(tasks)} | REWARD_MIN_SIM={REWARD_MIN_SIM} | "
          f"feature_version={feature_version_hash()}")

    # 特征基数分布（若两个集合都很小，Jaccard 容易虚高）
    sizes = sorted({len(s) for s in feats.values()})
    print(f"特征集合基数分布: {sizes}")

    pairs = []
    for a in tasks:
        for b in tasks:
            if a["id"] == b["id"]:
                continue
            s = jac(feats[a["id"]], feats[b["id"]])
            if s >= REWARD_MIN_SIM:
                pairs.append((a["id"], b["id"], a["_suite"], b["_suite"], round(s, 4)))

    print(f"\n≥{REWARD_MIN_SIM} 的有序对 = {len(pairs)} / {len(tasks)*(len(tasks)-1)}")

    # 每格作为「检索方」能吃到几条别人的记录
    reach = {t["id"]: 0 for t in tasks}
    for a, b, *_ in pairs:
        reach[a] += 1
    zero = [c for c, n in reach.items() if n == 0]
    print(f"检索方一格都吃不到的格 = {len(zero)}：{zero[:12]}")
    print(f"能吃到的格 = {len(tasks)-len(zero)}，最多 {max(reach.values())} 条/格")

    # 打印最强的 12 对
    print(f"\n最强 12 对：")
    for a, b, sa, sb, s in sorted(pairs, key=lambda x: -x[-1])[:12]:
        print(f"  {s:.3f}  {a:<24}({sa}) ← {b:<24}({sb})")

    # 跨套件 vs 同套件
    same = sum(1 for a, b, sa, sb, s in pairs if sa == sb)
    print(f"\n同套件 {same} 对 / 跨套件 {len(pairs)-same} 对")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({
            "n_tasks": len(tasks),
            "reward_min_sim": REWARD_MIN_SIM,
            "feature_version": feature_version_hash(),
            "feature_sizes": {k: len(v) for k, v in feats.items()},
            "pairs": pairs,
            "reach": reach,
            "zero_reach": zero,
        }, fh, ensure_ascii=False, indent=2)
    print(f"\n落盘 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
