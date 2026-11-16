"""V171 STAGE0 —— 零判分结构探针（只读账本，不跑 scorer、不调模型）。

要量的三件事（跑主实验前必须先知道，否则判据会是恒真式）：

  S1 现有账本里 RAISE 格有多少、它们的 n_obs/mean 分布
  S2 **同一格重复测，分数会动吗** —— 如果构造上必然不动，
     那「测了 k 次读数没动」就是恒真，ESCALATE 判据没有牙（纪律 53/72）
  S3 现有 `propose()` 对 RAISE 的 gid 是否**逐次相同** ——
     相同 ⇒ 同一格会被无限重复提；这是 V171 要堵的那个洞
"""
from __future__ import annotations

import collections
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT))

import mw.goals as G  # noqa: E402

LED = ROOT / "state" / "score_ledger.jsonl"
TASKS = ROOT / "bench" / "tasks"


def main() -> int:
    obs, bad = G.coerce_all(G.load(LED))
    suites = G.scan_suites(TASKS)
    ids = [t for v in suites.values() for t in v]
    print("=" * 74)
    print("V171 STAGE0（零判分）—— RAISE 的重复性与「读数会不会动」")
    print("=" * 74)
    print(f"账本 {LED.name}: {len(obs)} 行（坏行 {bad}）；套件 {len(suites)} 个 {len(ids)} 题")
    print(f"账本覆盖 {len({o['task_id'] for o in obs} & set(ids))} 题")

    # ── S1 每格分布 ──────────────────────────────────────────────────────
    per = collections.defaultdict(list)
    for o in obs:
        per[o["task_id"]].append(o["score"])
    print("\n## S1 每格观测（只列套件内的）")
    print(f"  {'cell':<30} {'n':>3} {'mean':>8} {'scores'}")
    kinds = collections.Counter()
    for c in sorted(per):
        if c not in ids:
            continue
        xs = per[c]
        m = G._mean(xs)
        k = "COVER" if not xs else ("PROBE" if len(xs) < G.MIN_OBS else
                                    ("RAISE" if m < G.TARGET else "DONE"))
        kinds[k] += 1
        print(f"  {c[:30]:<30} {len(xs):>3} {m:>8.4f} {[round(x, 4) for x in xs][:6]}")
    for c in ids:
        if c not in per:
            kinds["COVER"] += 1
    print(f"\n  按现状分类：{dict(kinds)}（COVER 含套件里从没测过的）")

    # ── S2 同格重复测，分数会不会动 ──────────────────────────────────────
    print("\n## S2 同一格多次观测：分数相同率")
    multi = {c: xs for c, xs in per.items() if len(xs) >= 2}
    same = sum(1 for xs in multi.values() if len(set(round(x, 6) for x in xs)) == 1)
    print(f"  有 ≥2 次观测的格 {len(multi)} 个；**逐次逐位相同**的 {same} 个")
    for c, xs in sorted(multi.items())[:10]:
        uniq = sorted(set(round(x, 6) for x in xs))
        print(f"  {c[:30]:<30} n={len(xs)} 不同取值 {len(uniq)} 个 {uniq[:4]}")
    if multi:
        print(f"  ⇒ 「重复测同一格读数不动」在现有账本上占 {same}/{len(multi)}"
              f"（{'恒真 ⇒ 判据没牙' if same == len(multi) else '有变化 ⇒ 判据有牙'}）")

    # ── S3 RAISE 的 gid 逐次相同吗 ───────────────────────────────────────
    print("\n## S3 同一格连续提议：gid 会不会变")
    raise_cells = [c for c in sorted(per) if c in ids
                   and len(per[c]) >= G.MIN_OBS and G._mean(per[c]) < G.TARGET]
    print(f"  当前 RAISE 格 {len(raise_cells)} 个")
    for c in raise_cells[:5]:
        # 模拟「测了 3 次仍不达标」：往观测里追加同分 3 行，看提议出的 gid
        base = G.propose(obs, ids)
        base_g = [g for g in base if g.cell == c and g.kind == "RAISE"]
        extra = obs + [{"task_id": c, "score": per[c][0]} for _ in range(3)]
        after = G.propose(extra, ids)
        after_g = [g for g in after if g.cell == c and g.kind == "RAISE"]
        print(f"  {c[:30]:<30} 现在 gid={base_g[0].gid if base_g else '—'}"
              f"｜追加 3 次同分后 gid={after_g[0].gid if after_g else '—'}"
              f"｜**相同={bool(base_g and after_g and base_g[0].gid == after_g[0].gid)}**")

    # ── S4 真账本里 RAISE 格有几次 NOT_MET 机会？ ────────────────────────
    print("\n## S4 触发阈值的可达性（真账本上）")
    print(f"  现有 RAISE 格 {len(raise_cells)} 个；k=3 次 NOT_MET 需要真跑 3 轮")
    print(f"  ⚠️ 「连测 k 次」这个条件在真账本上**从未被触到过**"
          f"（目标账本 goal_ledger.jsonl 不存在）⇒ 主实验必须自己造出 k 次记录，"
          f"并**在真实装置上跑一次**（不是只喂构造行）")

    out = {
        "n_obs": len(obs), "n_bad": bad,
        "kinds": dict(kinds),
        "multi_cells": len(multi), "multi_same": same,
        "raise_cells": raise_cells,
        "gid_stable": {},
    }
    for c in raise_cells:
        base = G.propose(obs, ids)
        b = [g for g in base if g.cell == c and g.kind == "RAISE"]
        extra = obs + [{"task_id": c, "score": per[c][0]} for _ in range(3)]
        a = [g for g in G.propose(extra, ids) if g.cell == c and g.kind == "RAISE"]
        out["gid_stable"][c] = bool(b and a and b[0].gid == a[0].gid)
    (HERE / "readings_stage0.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n落盘 {HERE.name}/readings_stage0.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
