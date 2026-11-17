"""V182 STAGE0 —— 池要不要按 `caliber_sig` 分组？

⛔ 只量结构，不下判断（判断留给 PREREG 之后的判据）。
本轮要回答的三个结构问题：

  S1  真账本上有几个口径、每格落几个。混池 vs 按口径分组，**逐格读数差在哪**。
  S2  `goals.load()` 的读路径**有没有** `caliber_sig` 闸（对照 `ScoreLedger.admissible`）。
  S3  混池的危害**是不是结构上存在**：造一份「同格两个口径、实现集不同」的账本，
      量 `propose()` 的目标序列会不会变 —— 如果只变桶值不变序列，那危害是「虚的比率」
      而不是「错的动作」。
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw import goals as G          # noqa: E402
from mw import scoreinfo as SI     # noqa: E402

LEDGER = ROOT / "state" / "score_ledger.jsonl"


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def per_cell(rows: list[dict]) -> dict:
    cells = sorted({r["task_id"] for r in rows})
    out = {}
    for c in cells:
        rs = [r for r in rows if r["task_id"] == c]
        mov = SI.movability(rows, cell=c)
        pool_b, cnt_b = SI.beh_pool_from_rows(rows, cell=c)
        beh = SI.behavioral(pool_b) if pool_b else {"kind": "UNKNOWN", "reason": "NO_POOL"}
        out[c] = {
            "n_rows": len(rs),
            "n_sigs": len({str(r.get("caliber_sig")) for r in rs}),
            "n_impls": mov["n_impls"], "n_scores": mov["n_scores"],
            "label": mov["label"], "reason": mov["reason"], "claim": mov["claim"],
            "beh_kind": beh.get("kind"), "beh_claim": beh.get("claim"),
            "n_beh": beh.get("n_behaviors"),
            "beh_counts": cnt_b,
            "mean": round(sum(r["score"] for r in rs) / len(rs), 4),
        }
    return out


def split_by_sig(rows: list[dict]) -> dict:
    """按 (格, sig) 分组后，每组自己算一次（组内行数 ≥1 才算）。"""
    out = {}
    for c in sorted({r["task_id"] for r in rows}):
        for s in sorted({str(r.get("caliber_sig")) for r in rows if r["task_id"] == c}):
            rs = [r for r in rows if r["task_id"] == c and str(r.get("caliber_sig")) == s]
            mov = SI.movability(rs, cell=c)
            pool_b, cnt_b = SI.beh_pool_from_rows(rs, cell=c)
            beh = SI.behavioral(pool_b) if pool_b else {"kind": "UNKNOWN", "reason": "NO_POOL"}
            out[(c, s)] = {"n_rows": len(rs), "n_impls": mov["n_impls"],
                           "n_scores": mov["n_scores"], "label": mov["label"],
                           "reason": mov["reason"], "claim": mov["claim"],
                           "beh_kind": beh.get("kind"), "beh_claim": beh.get("claim"),
                           "beh_counts": cnt_b,
                           "mean": round(sum(r["score"] for r in rs) / len(rs), 4)}
    return out


def main() -> None:
    rows = load_rows(LEDGER)
    print(f"账本 {LEDGER.relative_to(ROOT)}  行={len(rows)}")
    sigs = collections.Counter(str(r.get("caliber_sig")) for r in rows)
    print(f"口径 {len(sigs)} 个：")
    for s, n in sigs.most_common():
        sess = sorted({r.get("session") for r in rows if str(r.get("caliber_sig")) == s})
        withimpl = sum(1 for r in rows
                       if str(r.get("caliber_sig")) == s and r.get("impl_sha"))
        withvec = sum(1 for r in rows
                      if str(r.get("caliber_sig")) == s and isinstance(r.get("vec"), list))
        print(f"  {s}  {n:3d} 行  会话{sess}  带impl_sha={withimpl}  带vec={withvec}")

    print("\n── S1a 混池（现状 = goals.load 全部行）逐格 ──")
    mixed = per_cell(rows)
    for c, v in mixed.items():
        print(f"  {c:22s} 行{v['n_rows']:2d} sig{v['n_sigs']} "
              f"impls={str(v['n_impls']):4s} scores={str(v['n_scores']):4s} "
              f"{v['label']:8s} claim={str(v['claim']):16s} "
              f"beh={str(v['beh_kind']):8s} n_beh={str(v['n_beh']):4s} mean={v['mean']}")

    print("\n── S1b 按 (格, 口径) 分组 逐组 ──")
    split = split_by_sig(rows)
    for (c, s), v in split.items():
        print(f"  {c:22s} {s[:8]} 行{v['n_rows']:2d} "
              f"impls={str(v['n_impls']):4s} scores={str(v['n_scores']):4s} "
              f"{v['label']:8s} claim={str(v['claim']):16s} "
              f"beh={str(v['beh_kind']):8s} mean={v['mean']}")

    # 分组后「还能判的组」有几个 vs 混池「还能判的格」有几个
    n_mixed_judge = sum(1 for v in mixed.values() if v["label"] != SI.KIND_UNKNOWN)
    n_split_cells = {c for (c, s), v in split.items() if v["label"] != SI.KIND_UNKNOWN}
    print(f"\n  可判格数：混池 {n_mixed_judge} / 分组后 {len(n_split_cells)} "
          f"{sorted(n_split_cells)}")
    print(f"  分组后组数 {len(split)}，其中可判 "
          f"{sum(1 for v in split.values() if v['label'] != SI.KIND_UNKNOWN)}")

    print("\n── S2 读路径的 sig 闸 ──")
    from mw.ledger import ScoreLedger
    top = sigs.most_common(1)[0][0]
    led = ScoreLedger(LEDGER, caliber_sig=top)
    snap = led.snapshot(top)
    print(f"  ScoreLedger(sig={top[:8]}).snapshot = {len(snap)} 行")
    print(f"  goals.load()                               = {len(rows)} 行")
    print(f"  ledger.report: {led.as_line()}")
    print(f"  ⚠️ goals 的 obs 走 G.load + coerce_all ⇒ 绕过上闸："
          f"{len(rows)} 行 vs 应 {len(snap)} 行（多 {len(rows) - len(snap)} 行外来口径）")

    print("\n── S3 混池危害的结构检验（合成，⛔ 不作能力读数）──")
    # 造：t01 这一格，口径 A 的实现集 {a1..a5} 分 0.2/0.4/0.6/0.8/1.0（COARSE），
    # 口径 B 的实现集 {b1..b3} 全 1.0（CONSTANT 但桶值 1.0 ⇒ 无 claim）。
    # 混池 vs 分组，比 label / 桶值 / propose 的 gid 序列。
    def mk(cell, tag, impl_scores, sig):
        return [{"session": 900, "family": "synth", "task_id": cell,
                 "score": sc, "passed": sc >= 1.0, "caliber_sig": sig,
                 "impl_sha": f"{tag}{i}", "candidate": "synth"}
                for i, sc in enumerate(impl_scores)]

    A = mk("t01_parse_duration", "a", [0.2, 0.4, 0.6, 0.8, 1.0], "SIG_A")
    B = mk("t01_parse_duration", "b", [1.0, 1.0, 1.0], "SIG_B")
    both = A + B
    for name, rs in (("口径A", A), ("口径B", B), ("混池", both)):
        mov = SI.movability(rs, cell="t01_parse_duration")
        st = G.stats([G.coerce(r) for r in rs], "t01_parse_duration")
        print(f"  {name:6s} label={mov['label']:8s} impls={mov['n_impls']} "
              f"scores={mov['n_scores']} mean={st.get('mean')} "
              f"obs={st.get('n')} claim={mov['claim']}")

    print("\n  propose 序列（真模块，入参只有 obs + 套件 id）：")
    for name, rs in (("口径A", A), ("口径B", B), ("混池", both)):
        obs = [G.coerce(r) for r in rs]
        gs = G.propose(obs, ["t01_parse_duration"], done=[], escalated=[])
        print(f"    {name:6s} → {[g.gid for g in gs]}")


if __name__ == "__main__":
    main()
