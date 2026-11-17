"""V182 STAGE0 补量 —— 口径闸的**代价**，以及混池能不能翻转提议顺序。

S3 上一版只造了一格 ⇒ `propose` 只能提那一个，看不到排序。本版造两格。
S4 量：如果按「每格选一个口径」算读数，真账本上 24 格的读数与提议序列怎么变。
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


def rows_of(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def show(tag: str, rs: list[dict], cells: list[str]) -> list[str]:
    obs = [G.coerce(r) for r in rs]
    gs = G.propose(obs, cells, done=[], escalated=[])
    seq = [(g.kind, g.cell) for g in gs]
    means = {c: (G.stats(obs, c)["mean_score"], G.stats(obs, c)["n_obs"])
             for c in cells if G.stats(obs, c)["n_obs"]}
    print(f"  {tag:14s} {seq}")
    print(f"  {'':14s} (mean,n) = " +
          ", ".join(f"{c}:{means[c][0]:.3f}/{means[c][1]}" for c in sorted(means)))
    return [g.cell + "|" + g.kind for g in gs]


def mk(cell, tag, scores, sig):
    return [{"session": 900, "family": "synth", "task_id": cell, "score": s,
             "passed": s >= 1.0, "caliber_sig": sig, "impl_sha": f"{tag}{cell}{i}",
             "candidate": "synth"} for i, s in enumerate(scores)]


def main() -> None:
    print("── S3 两格：混池能不能翻转 RAISE 的先后 ──")
    # X：口径 A 五行为 0.4（均分 0.40），口径 B 五行全 1.0 ⇒ 混池 0.70
    # Y：只有口径 A，五行为 0.65 ⇒ 混池也是 0.65
    XA = mk("x", "a", [0.4] * 5, "SIG_A")
    XB = mk("x", "b", [1.0] * 5, "SIG_B")
    YA = mk("y", "a", [0.65] * 5, "SIG_A")
    cells = ["x", "y"]
    s_mixed = show("混池", XA + XB + YA, cells)
    s_A = show("只看 SIG_A", XA + YA, cells)
    print(f"  ⇒ 顺序是否不同：{s_mixed != s_A}")
    if s_mixed != s_A:
        print(f"     混池 {s_mixed}")
        print(f"     单口径 {s_A}")

    print("\n── S3b 反向：两口径都不该被混（同格同实现集，只是 sig 不同）──")
    Z1 = mk("z", "c", [0.4] * 5, "SIG_A")
    Z2 = mk("z", "d", [1.0] * 5, "SIG_B")
    show("混池", Z1 + Z2, ["z"])
    show("只看 SIG_A", Z1, ["z"])

    print("\n── S4 真账本：按「每格一个口径」算，读数与提议怎么变 ──")
    rows = rows_of(LEDGER)
    cells24 = sorted({r["task_id"] for r in rows})
    cells6 = ["t01_parse_duration", "t02_dedupe", "t03_commas",
              "t04_merge_ranges", "t05_top_k", "t06_rle"]

    # 选口径的三种规则
    def pick_majority(rs, cell):
        cnt = collections.Counter(str(r.get("caliber_sig")) for r in rs if r["task_id"] == cell)
        return cnt.most_common(1)[0][0]

    def pick_latest(rs, cell):
        sub = [r for r in rs if r["task_id"] == cell]
        if not sub:
            return None
        best = max(sub, key=lambda r: (r.get("session") or -1))
        return str(best.get("caliber_sig"))

    for tag, picker in (("多数口径", pick_majority), ("最新口径", pick_latest)):
        kept = []
        for c in cells24:
            s = picker(rows, c)
            kept += [r for r in rows if r["task_id"] == c and str(r.get("caliber_sig")) == s]
        print(f"\n  【{tag}】留 {len(kept)} 行（原 66）")
        a = show("24 格", rows, cells24)
        b = show("24 格·加闸", kept, cells24)
        print(f"    序列不同 = {a != b}")
        a6 = show("6 格", [r for r in rows if r["task_id"] in cells6], cells6)
        b6 = show("6 格·加闸", [r for r in kept if r["task_id"] in cells6], cells6)
        print(f"    6 格序列不同 = {a6 != b6}")

    print("\n── S5 加闸后消失的格（观测归零 ⇒ 从 PROBE/RAISE 掉成 COVER）──")
    kept_maj = []
    for c in cells24:
        s = pick_majority(rows, c)
        kept_maj += [r for r in rows if r["task_id"] == c and str(r.get("caliber_sig")) == s]
    for c in cells24:
        n0 = sum(1 for r in rows if r["task_id"] == c)
        n1 = sum(1 for r in kept_maj if r["task_id"] == c)
        if n0 != n1:
            print(f"  {c:22s} {n0} → {n1}  {'⚠️ 归零' if n1 == 0 else ''}")


if __name__ == "__main__":
    main()
