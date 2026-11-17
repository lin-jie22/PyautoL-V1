"""V182 判据 —— 池要不要按口径分组。（PREREG_v182 §2）

⛔ 判据一律走真模块（`mw.goals` / `mw.scoreinfo`），不在这里重写规则。
⛔ 合成池（B 组）明确标注，不作能力读数。
"""
from __future__ import annotations

import hashlib
import itertools
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw import goals as G          # noqa: E402
from mw import scoreinfo as SI     # noqa: E402

LEDGER = ROOT / "state" / "score_ledger.jsonl"
EV_OUT = Path(__file__).resolve().parent / "readings_v182.json"
MIN_OBS = G.MIN_OBS

results: list[dict] = []
readings: dict = {}


def chk(cid: str, claim: str, ok: bool | None, detail: str) -> None:
    verdict = "UNKNOWN" if ok is None else ("PASS" if ok else "FAIL")
    results.append({"id": cid, "claim": claim, "verdict": verdict, "detail": detail})
    print(f"  [{verdict:7s}] {cid}  {claim}")
    print(f"            {detail}")


def rows_of(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def seq(rows: list[dict], cells: list[str]) -> list[str]:
    """真模块出的提议序列（cell+kind）。⛔ 不重写排序。"""
    obs, _ = G.coerce_all(rows)
    return [f"{g.kind}|{g.cell}" for g in G.propose(obs, cells, done=[], escalated=[])]


def pick_major(rows: list[dict], cell: str) -> str | None:
    """该格主口径（行数最多；并列 ⇒ None）。⛔ 不许用最新/字典序猜。"""
    rep = {r["cell"]: r for r in G.sig_report(rows, [cell])}
    return rep[cell]["major"]


def keep_major(rows: list[dict], cells: list[str]) -> list[dict]:
    out = []
    for c in cells:
        m = pick_major(rows, c)
        out += [r for r in rows if r["task_id"] == c and str(r.get("caliber_sig")) == m] \
            if m is not None else []
    return out


def mk(cell, tag, scores, sig):
    return [{"session": 900, "family": "synth", "task_id": cell, "score": s,
             "passed": s >= 1.0, "caliber_sig": sig, "impl_sha": f"{tag}{cell}{i}",
             "candidate": "synth"} for i, s in enumerate(scores)]


def main() -> None:
    rows = rows_of(LEDGER)
    cells24 = sorted({r["task_id"] for r in rows})
    cells6 = ["t01_parse_duration", "t02_dedupe", "t03_commas",
              "t04_merge_ranges", "t05_top_k", "t06_rle"]

    print("=" * 78)
    print("V182 判据（PREREG_v182 §2）")
    print("=" * 78)

    # ── A 组：真账本的结构事实 ──────────────────────────────────────
    sigs_with_impl = {str(r.get("caliber_sig")) for r in rows if r.get("impl_sha")}
    chk("A1", "带 impl_sha 的行覆盖的口径数 = 1",
        len(sigs_with_impl) == 1,
        f"口径 {len(sigs_with_impl)} 个：{sorted(s[:8] for s in sigs_with_impl)}；"
        f"带 impl_sha 的行 {sum(1 for r in rows if r.get('impl_sha'))} 行")

    # A2：同一格里，两两口径的 impl_sha 交集对数之和
    pairs_hit = 0
    pair_detail = []
    for c in cells24:
        per: dict[str, set] = {}
        for r in rows:
            if r["task_id"] == c and r.get("impl_sha"):
                per.setdefault(str(r.get("caliber_sig")), set()).add(r["impl_sha"])
        for (s1, s2) in itertools.combinations(sorted(per), 2):
            n = len(per[s1] & per[s2])
            if n:
                pairs_hit += n
                pair_detail.append(f"{c}:{s1[:6]}∩{s2[:6]}={n}")
    chk("A2", "跨 (格, 口径) 的 impl_sha 交集 = 0",
        pairs_hit == 0,
        f"交集 {pairs_hit} 个" + (f"：{pair_detail}" if pair_detail else
                                  "（因为带身份的 30 行全在同一个口径里）"))

    # A3：两口径各 ≥ MIN_OBS 行的格数
    multi = [r for r in G.sig_report(rows, cells24)
             if sum(1 for _s, n, _m in r["sigs"] if n >= MIN_OBS) >= 2]
    chk("A3", f"两口径各 ≥ {MIN_OBS} 行的格数 = 0（本轮不改判据的依据）",
        len(multi) == 0,
        f"{len(multi)} 格" + (f"：{[r['cell'] for r in multi]}" if multi else ""))
    readings["A"] = {"sigs_with_impl": sorted(sigs_with_impl),
                     "impl_pairs": pairs_hit, "multi_sig_cells": [r["cell"] for r in multi]}

    # ── B 组：危害存在性（合成，⛔ 不作能力读数）────────────────────
    XA = mk("x", "a", [0.4] * 5, "SIG_A")
    XB = mk("x", "b", [1.0] * 5, "SIG_B")
    YA = mk("y", "a", [0.65] * 5, "SIG_A")
    s_mixed = seq(XA + XB + YA, ["x", "y"])
    s_single = seq(XA + YA, ["x", "y"])
    chk("B1", "两格 → 混池与单口径的 propose 序列不同",
        s_mixed != s_single,
        f"混池={s_mixed}｜单口径={s_single}")

    Z1 = mk("z", "c", [0.4] * 5, "SIG_A")
    Z2 = mk("z", "d", [1.0] * 5, "SIG_B")
    k1 = {g.kind for g in G.propose([G.coerce(r) for r in Z1 + Z2], ["z"])}
    k2 = {g.kind for g in G.propose([G.coerce(r) for r in Z1], ["z"])}
    chk("B2", "同格同形态、仅 sig 不同 ⇒ kind 相同（混池不翻转 kind）",
        k1 == k2,
        f"混池 {sorted(k1)}｜单口径 {sorted(k2)}（均分 0.70 vs 0.40，都 <1.0）")
    readings["B"] = {"mixed": s_mixed, "single": s_single,
                     "z_kinds": [sorted(k1), sorted(k2)]}

    # ── C 组：真账本上的行为差异 ───────────────────────────────────
    kept = keep_major(rows, cells24)
    c6_mixed = seq([r for r in rows if r["task_id"] in cells6], cells6)
    c6_split = seq([r for r in kept if r["task_id"] in cells6], cells6)
    chk("C1", "coding_small 6 格：混池与主口径序列相同",
        c6_mixed == c6_split,
        f"混池={c6_mixed}｜主口径={c6_split}")

    c24_mixed = seq(rows, cells24)
    c24_split = seq(kept, cells24)
    became_cover = [x for x in c24_split if x.startswith("COVER|")
                    and x not in c24_mixed]
    chk("C2", "24 格：混池与主口径序列不同",
        c24_mixed != c24_split,
        f"集合相同={sorted(c24_mixed) == sorted(c24_split)}；"
        f"差异位 {sum(1 for a, b in zip(c24_mixed, c24_split) if a != b)}/{len(c24_mixed)}；"
        f"⚠️ 主口径臂里 {len(became_cover)} 格从 PROBE 掉成 COVER：{became_cover}")

    # C3：「最新口径」规则下，至少 1 格 mean 变化 > 0.4
    latest_rows = []
    for c in cells24:
        sub = [r for r in rows if r["task_id"] == c]
        pick = max(sub, key=lambda r: (r.get("session") if r.get("session") is not None else -1,
                                       str(r.get("caliber_sig"))))
        latest_rows += [r for r in sub if str(r.get("caliber_sig")) == str(pick.get("caliber_sig"))]
    deltas = []
    for c in cells24:
        a = G.stats([G.coerce(r) for r in rows], c)["mean_score"]
        b = G.stats([G.coerce(r) for r in latest_rows], c)["mean_score"]
        if abs(a - b) > 1e-9:
            deltas.append({"cell": c, "mixed": round(a, 4), "latest": round(b, 4),
                           "delta": round(abs(a - b), 4)})
    big = [d for d in deltas if d["delta"] > 0.4]
    chk("C3", "「最新口径」下至少 1 格 mean 变化 > 0.4",
        len(big) >= 1,
        f"变化格 {len(deltas)} 个，其中 >0.4 的 {len(big)} 个："
        f"{[(d['cell'], d['mixed'], d['latest']) for d in big]}")
    readings["C"] = {"c6": [c6_mixed, c6_split], "c24_mixed": c24_mixed,
                     "c24_split": c24_split, "deltas": deltas}

    # ── D 组：产品改动的自证 ───────────────────────────────────────
    rep = G.sig_report(rows, cells24)
    mixed_cells = [r["cell"] for r in rep if r["n_sigs"] >= 2]
    tie_cells = [r["cell"] for r in rep if r["tie"]]
    chk("D1", "sig_report 报：混口径格 = 6、平票 = 4（预注册押的数）",
        len(mixed_cells) == 6 and len(tie_cells) == 4,
        f"实测混口径 {len(mixed_cells)} 格 {mixed_cells}；平票 {len(tie_cells)} 格 {tie_cells}")

    # D2：propose 的源码与 HEAD 逐字相同（⛔ 本轮不动判据）
    def fn_src(text: str, name: str) -> str:
        i = text.index(f"\ndef {name}(")
        j = len(text)
        for k in (text.find("\ndef ", i + 1), text.find("\n# ===", i + 1)):
            if k != -1:
                j = min(j, k)
        return text[i:j]

    cur = (ROOT / "mw" / "goals.py").read_text(encoding="utf-8")
    head = subprocess.run(["git", "-C", str(ROOT), "show", "HEAD:mw/goals.py"],
                          capture_output=True, text=True).stdout
    same = {}
    for name in ("propose", "stats", "evaluate", "escalations", "repair_axes"):
        a = hashlib.sha256(fn_src(cur, name).encode()).hexdigest()[:12]
        b = hashlib.sha256(fn_src(head, name).encode()).hexdigest()[:12]
        same[name] = (a, b, a == b)
    chk("D2", "propose/stats/evaluate/escalations/repair_axes 源码与 HEAD 逐字相同",
        all(v[2] for v in same.values()),
        "｜".join(f"{k}={v[0]}{'=' if v[2] else '≠'}{v[1]}" for k, v in same.items()))

    bad = [r["cell"] for r in rep if r["tie"] and r["mean_major"] is not None]
    chk("D3", "平票格的 mean_major = None（⛔ 不猜一个口径）",
        not bad,
        f"平票 {len(tie_cells)} 格，mean_major 非 None 的 {len(bad)} 格"
        f"；样例 sg02: mean_all={[r for r in rep if r['cell']=='sg02'][0]['mean_all']:.4f} "
        f"mean_major={[r for r in rep if r['cell']=='sg02'][0]['mean_major']}")
    readings["D"] = {"mixed": mixed_cells, "ties": tie_cells,
                     "fn_sha": {k: list(v) for k, v in same.items()},
                     "rep": rep}

    n_pass = sum(1 for r in results if r["verdict"] == "PASS")
    n_fail = sum(1 for r in results if r["verdict"] == "FAIL")
    n_unk = sum(1 for r in results if r["verdict"] == "UNKNOWN")
    print("-" * 78)
    print(f"PASS={n_pass}  FAIL={n_fail}  UNKNOWN={n_unk}  共 {len(results)}")
    # ⛔ 纪律 55：判据条数必须与预注册一致（A3+B2+C3+D3 = 11），少一条就是漏跑
    assert len(results) == 11, f"判据条数 {len(results)} ≠ 预注册 11"
    EV_OUT.write_text(json.dumps({"results": results, "readings": readings},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"读数 → {EV_OUT.name}")
    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
