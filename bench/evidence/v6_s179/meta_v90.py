#!/usr/bin/env python3
"""V90 元分析 —— 把 V85/V87/V88/V89 的 88 格按「块里到底有什么」重切。

零模型调用、零 `.db` 打开（纪律 31）。只读 `state_dump.jsonl` 与 `bench/runs.jsonl`。

⛔ 分组按**原文判别子串**，不按标号 —— V85 的 C 臂是同标号安慰剂，
   按标号分组会把「常识默认」当成本题知识（这正是 §154 的安慰剂设计要防的错）。

判据见同目录 `PREREG.md`（先落盘，后跑）。
用法：python3 bench/evidence/v6_s179/meta_v90.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "bench" / "evidence"

#: 题 → 该题所需的 ZK（⛔ 与 make_seed_v85.py / make_seed_v87.py 的构造一致）
OWN_ZK = {
    "g7d2_dedupe_keep": "ZK1", "g7d3_fmt_amount": "ZK2",
    "g7e1_parse_flag": "ZK3", "g7e2_truncate_tail": "ZK4",
}
#: ZK → 原文判别子串（真知识）与安慰剂写法（用于核对「安慰剂确实判 0」）
REAL = {"ZK1": "转小写", "ZK2": "千分位用空格分隔", "ZK3": "这类历史脏值", "ZK4": "U+2026"}
PLACEBO = {"ZK1": "不改变大小写", "ZK2": "千分位用逗号分隔",
           "ZK3": "都算真", "ZK4": "三个英文句点"}

#: (轮名, 目录, 臂名, 题族)
CELLS: list[tuple[str, str, str, str]] = []
for a in ("Ar1", "Ar2", "Br1", "Br2", "Cr1", "Cr2"):
    CELLS.append(("V85", "v6_s174", a, "g7j"))
for a in ("M0", "M1", "M2", "M3", "M4"):
    CELLS.append(("V87", "v6_s176", a, "g7j"))
for a in ("K1a", "K1b", "K2", "K3", "K4", "K6a", "K6b"):
    CELLS.append(("V88", "v6_s177", a, "g7j"))
for a in ("E1", "E2", "Z1", "Z2"):
    CELLS.append(("V89", "v6_s178", a, "struct"))


def scores(d: Path) -> dict[str, float]:
    """V85 的臂把 `runs.jsonl` 直接放在臂目录，V87~V89 放在 `bench/` 下（两种都认）。"""
    p = next((x for x in (d / "bench" / "runs.jsonl", d / "runs.jsonl") if x.exists()), None)
    if p is None:
        return {}
    per: dict[str, float] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        for t in (rec.get("report") or {}).get("per_task") or []:
            if t.get("score") is not None:
                per[t["id"]] = t["score"]
    return per


def blocks(d: Path) -> dict[str, list[str]]:
    p = d / "state_dump.jsonl"
    if not p.exists():
        return {}
    out: dict[str, list[str]] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        m = re.search(r"\[语义记忆\](.*?)(?=\n\[|\Z)", r.get("state") or "", re.S)
        out.setdefault(r.get("task"), []).append((m.group(1) if m else "").strip())
    return out


def feature(blk: str, task: str) -> dict:
    own_zk = OWN_ZK.get(task)
    present = [z for z in REAL if REAL[z] in blk]
    placebo = [z for z in PLACEBO if PLACEBO[z] in blk]
    labels = re.findall(r"（(ZK[1-4])）", blk)
    own = 1 if (own_zk and REAL[own_zk] in blk) else 0
    label = 1 if (own_zk and own_zk in labels) else 0
    pos = labels.index(own_zk) + 1 if (own_zk in labels) else 0
    return {"own": own, "label": label, "n": len(labels), "full": int(len(labels) == 4),
            "pos": pos, "real": present, "placebo": placebo, "chars": len(blk)}


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    z = 1.959963984540054
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, c - h), min(1.0, c + h))


def main() -> None:
    rows: list[dict] = []
    seen: list[str] = []
    for round_, dirn, arm, family in CELLS:
        d = EV / dirn / "arms" / arm
        S, B = scores(d), blocks(d)
        if not S or not B:
            seen.append(f"⛔ 缺产物 {round_}/{arm}")
            continue
        for task in sorted(B):
            blks = B[task]
            sc = S.get(task)
            if sc is None:
                seen.append(f"⛔ {round_}/{arm}/{task} 无分数")
                continue
            # 同题多次尝试：块必须逐字节相同（否则「一格一个块」不成立，须单独登记）
            uniq = sorted(set(blks))
            if len(uniq) > 1:
                seen.append(f"⚠️ {round_}/{arm}/{task} 块在 {len(blks)} 次尝试间不一致（取首次）")
            f = feature(uniq[0], task)
            rows.append({"round": round_, "arm": arm, "task": task, "family": family,
                         "score": sc, "attempts": len(blks), **f})

    print("# V90 元分析：88 格按块内容重切（`mw/` 零改动，零模型调用）\n")
    for s in seen:
        print(s)
    print(f"\n总格数 = {len(rows)}（g7j {sum(r['family']=='g7j' for r in rows)} / "
          f"struct {sum(r['family']=='struct' for r in rows)}）；零模型调用。\n")

    print("## §1 逐轮 × 逐臂：块内容与分数（g7j）\n")
    print("| 轮 | 臂 | 题 | own | label | n | pos | 块字 | score |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        if r["family"] != "g7j":
            continue
        print(f"| {r['round']} | {r['arm']} | {r['task'][:14]} | {r['own']} | {r['label']} | "
              f"{r['n']} | {r['pos']} | {r['chars']} | {r['score']:g} |")

    g = [r for r in rows if r["family"] == "g7j"]
    st = [r for r in rows if r["family"] == "struct"]

    print("\n## §2 P1 地板：g7j 里 `own=0` 的全部格\n")
    no = [r for r in g if r["own"] == 0]
    for r in no:
        print(f"  {r['round']}/{r['arm']}/{r['task'][:14]:16s} label={r['label']} n={r['n']} "
              f"块字={r['chars']:3d} score={r['score']:g}")
    mx = max((r["score"] for r in no), default=None)
    print(f"\n  ⇒ n={len(no)}  max={mx:g}  ⇒ P1 押 max=0.2 ⇒ {'✓ 命中' if mx == 0.2 else '⛔ 未命中'}")

    print("\n## §3 P2 天花板兑现率：g7j 里 `own=1 & full=1`\n")
    full = [r for r in g if r["own"] == 1 and r["full"] == 1]
    k = sum(1 for r in full if abs(r["score"] - 1.0) < 1e-9)
    print(f"  n={len(full)}  满分={k}  比例={k/len(full):.4f}" if full else "  （无格）")
    lo, hi = wilson(k, len(full)) if full else (0, 0)
    print(f"  Wilson 95% = [{lo:.3f}, {hi:.3f}]")
    for r in full:
        if abs(r["score"] - 1.0) > 1e-9:
            print(f"    未满分：{r['round']}/{r['arm']}/{r['task'][:14]} = {r['score']:g}  pos={r['pos']}")
    ok = bool(full) and abs(k / len(full) - 0.75) * len(full) <= 1
    print(f"  ⇒ P2 押 0.75 ±1 格 ⇒ {'✓ 命中' if ok else '⛔ 未命中'}")

    print("\n## §4 P3 总体兑现率：g7j 里 `own=1`\n")
    ow = [r for r in g if r["own"] == 1]
    k3 = sum(1 for r in ow if abs(r["score"] - 1.0) < 1e-9)
    print(f"  n={len(ow)}  满分={k3}  比例={k3/len(ow):.4f}  区间押 [0.45,0.75]")
    lo3, hi3 = wilson(k3, len(ow))
    print(f"  Wilson 95% = [{lo3:.3f}, {hi3:.3f}]")
    print(f"  ⇒ {'✓ 命中' if 0.45 <= k3 / len(ow) <= 0.75 else '⛔ 未命中'}")

    print("\n## §5 P4 低于地板：g7j 里 `own=1 且 score<0.2`\n")
    bad = [r for r in ow if r["score"] < 0.2 - 1e-9]
    for r in bad:
        print(f"  {r['round']}/{r['arm']}/{r['task'][:14]:16s} score={r['score']:g} "
              f"块内真知识={r['real']} 块字={r['chars']}")
    print(f"  ⇒ n={len(bad)}  ⇒ P4 押 ≥1 ⇒ {'✓ 命中' if len(bad) >= 1 else '⛔ 未命中'}")

    print("\n## §6 P5 安慰剂：V85 C 臂（8 格）\n")
    c = [r for r in g if r["round"] == "V85" and r["arm"].startswith("C")]
    for r in c:
        print(f"  {r['arm']}/{r['task'][:14]:16s} own={r['own']} label={r['label']} "
              f"块内安慰剂={r['placebo']} score={r['score']:g}")
    kc = sum(1 for r in c if abs(r["score"] - 1.0) < 1e-9)
    mc = max((r["score"] for r in c), default=None)
    print(f"\n  ⇒ 满分 {kc} 格、max={mc:g} ⇒ P5 押 0 / ≤0.4 ⇒ "
          f"{'✓ 命中' if kc == 0 and mc is not None and mc <= 0.4 else '⛔ 未命中'}")
    print(f"  ⇒ 安慰剂陷阱规模：`label=1 & own=0` 的格 = "
          f"{sum(1 for r in g if r['label'] == 1 and r['own'] == 0)}/{len(g)}")

    print("\n## §7 P6 两族不重叠：struct 16 格（全部 own=0）\n")
    for r in st:
        print(f"  {r['arm']}/{r['task'][:18]:20s} n={r['n']} 块字={r['chars']:3d} score={r['score']:g}")
    if st:
        mn, me = min(r["score"] for r in st), mean(r["score"] for r in st)
        print(f"\n  ⇒ min={mn:g}  mean={me:.4f} ⇒ P6 押 min≥0.2 且 mean≥0.5 ⇒ "
              f"{'✓ 命中' if mn >= 0.2 - 1e-9 and me >= 0.5 else '⛔ 未命中'}")
        by = {}
        for r in st:
            by.setdefault(r["task"], []).append(r)
        print("  逐题（臂间）:")
        for t, rs in sorted(by.items()):
            print(f"    {t[:20]:22s} " + " ｜ ".join(f"{x['arm']}={x['score']:g}" for x in rs))

    print("\n## §8 补充：位序与分数（g7j, own=1）\n")
    for p in (1, 2, 3, 4):
        sub = [r for r in ow if r["pos"] == p]
        if sub:
            sc = " ".join(f"{r['score']:g}" for r in sub)
            print(f"  pos={p}: n={len(sub):2d}  mean={mean(r['score'] for r in sub):.4f}  "
                  f"满分={sum(1 for r in sub if abs(r['score'] - 1.0) < 1e-9)}  分数=[{sc}]")
    print("\n## §9 补充：块字数与分数（g7j）\n")
    for r in sorted(g, key=lambda x: x["chars"]):
        print(f"  {r['chars']:3d} 字  {r['score']:g}  {r['round']}/{r['arm']}/{r['task'][:14]}")


if __name__ == "__main__":
    main()
