#!/usr/bin/env python3
"""V139 Part A'：用**实际调用边**（edges_run.py 跑出来的）判断后果沿哪走。

三个方向，同一个真值（v6_s227/analysis.json 的 diff，VI39 重判三档后的 SPILL 站）：
  up   = f 的传递**调用者**（含 f）——「调用方拿到不同的返回值」
  down = f 的传递**被调用者**（含 f）——「被调方拿到不同的实参」
  both = up ∪ down
零分布：把真值标签随机置换（纪律 49）。
"""
from __future__ import annotations

import json
import pathlib
import random
import statistics
from collections import deque

EVID = pathlib.Path(__file__).resolve().parent
V138 = EVID.parent / "v6_s227"


def reach(fwd: dict[str, set[str]], f: str) -> set[str]:
    seen = {f}
    dq = deque([f])
    while dq:
        cur = dq.popleft()
        for n in fwd.get(cur, ()):
            if n not in seen:
                seen.add(n)
                dq.append(n)
    return seen


def main() -> int:
    d = json.loads(pathlib.Path("/tmp/v139_edges.json").read_text())
    edges = [(a, b) for a, b in d["edges"]]
    fwd: dict[str, set[str]] = {}
    rev: dict[str, set[str]] = {}
    for a, b in edges:
        fwd.setdefault(a, set()).add(b)
        rev.setdefault(b, set()).add(a)

    tiers = json.loads((EVID / "tiers.json").read_text())
    own = tiers["owner"]
    tm = tiers["tiers"]
    rows = json.loads((V138 / "analysis.json").read_text())["rows"]
    universe = set(json.loads((V138 / "baseline.json").read_text())["base_per_fn"])

    # 方向图只用 mw 内节点（丢掉 <root>）
    spill = [r for r in rows if tm[r["key"]] == "SPILL"]
    print(f"实际调用图：边 {len(edges)}，节点(含 root) {len(set(fwd) | set(rev))}")
    print(f"真值：SPILL {len(spill)} 站；universe（基线里被调用过的函数）{len(universe)}")

    rng = random.Random(20261115)
    agg = {k: [0, 0, 0] for k in ("up", "down", "both")}
    rich = {k: [] for k in agg}
    rec_lt1 = {k: 0 for k in agg}
    out = []

    for r in spill:
        key, truth = r["key"], set(r["diff"])
        f = own.get(key, "?")
        up = reach(rev, f)
        down = reach(fwd, f)
        both = up | down
        line = {"key": key, "f": f, "ntruth": len(truth)}
        for tag, pred in (("up", up), ("down", down), ("both", both)):
            hit = len(pred & truth)
            agg[tag][0] += hit
            agg[tag][1] += len(pred)
            agg[tag][2] += len(truth)
            if len(pred & truth) < len(truth):
                rec_lt1[tag] += 1
            p = hit / len(pred) if pred else 0.0
            zero = []
            for _ in range(200):
                fake = set(rng.sample(sorted(universe), min(len(truth), len(universe))))
                zero.append(len(pred & fake) / len(pred) if pred else 0.0)
            z = statistics.mean(zero)
            rich[tag].append(p / z if z else float("inf"))
            line[tag] = (round(p, 3), round(hit / len(truth) if truth else 0, 3), len(pred), z)
        out.append(line)

    print(f"\n{'站点':20s}{'|T|':>4s} | {'up: 精度/召回/|P|':>24s} | {'down':>22s} | {'both':>22s}")
    for L in out:
        def fmt(t):
            p, rr, n, z = L[t]
            return f"{p:.2f}/{rr:.2f}/{n:3d}"
        print(f"{L['key']:20s}{L['ntruth']:4d} | {fmt('up'):>24s} | {fmt('down'):>22s} | {fmt('both'):>22s}")

    print("\n=== 合并（Σ命中/Σ预测）===")
    tot_t = agg["up"][2]
    for tag in ("up", "down", "both"):
        h, p, t = agg[tag]
        print(f"  {tag:5s} 命中 {h:4d}/{p:5d} 精度 {h/p:6.3f} | 召回 {h/t:6.3f} "
              f"| 富集中位 {statistics.median(rich[tag]):6.2f}× | 召回<1 的站 {rec_lt1[tag]}/{len(spill)}")

    # 被调用次数少的函数：真值集中在少数几个
    from collections import Counter
    cnt = Counter()
    for L in out:
        for k in L["ntruth"] and [L["key"]]:
            pass
    top = Counter()
    for r in spill:
        for g in r["diff"]:
            top[g] += 1
    print("\n=== 真值里出现最多的函数（27 站合计）===")
    for g, c in top.most_common(10):
        print(f"  {g:44s} {c:3d} 站")

    (EVID / "partA_dyn.json").write_text(json.dumps(
        {"rows": out, "agg": agg,
         "rich_median": {k: statistics.median(v) for k, v in rich.items()},
         "edges": len(edges),
         "top_truth": top.most_common(20)}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
