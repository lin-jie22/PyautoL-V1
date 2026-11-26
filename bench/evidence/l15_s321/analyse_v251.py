#!/usr/bin/env python3
"""Analyse V251 -- production-chain A/B of --goals-swap-edit {off,line}.

Reads:  run_a.log / run_b.log (device stdout, authoritative for base->after)
        /tmp/v251_{a,b}/goal_obs.jsonl (per-candidate scores + impl sha)
        preflight_v251.json (LINE_ref per cell)

Prints: per-cell table for both arms, main criterion (P1), pairing gate (P3),
        fail-closed counts (P4), LINE vs LINE_ref (P5).
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

D = pathlib.Path(__file__).resolve().parent
ARMS = {"A_off": pathlib.Path("/tmp/v251_a"), "B_line": pathlib.Path("/tmp/v251_b")}
N_TESTS = 5

RE_SWAP = re.compile(r"换路\s+(\S+)\s+(\d+) 次换路：best ([0-9.]+) → ([0-9.]+) ⇒ \*\*(\w+)\*\*")
RE_START = re.compile(r"▶ 起点 start_from=(\S+) src=(\S+) sha=(\S+) score=([0-9.]+) chars=(\d+)")
RE_ACT = re.compile(r"(RAISE|PROBE|COVER)\s+(\S+)\s+测到 ([0-9.]+) ⇒ \*\*(\w+)\*\*")
RE_FAIL = re.compile(r"⚠️ 换路 #(\d+) 失败：(\S+)(?: \| LINE=(\S+) EXPR=(.*))?")
RE_CELL = re.compile(r"^\s+\d+\s+(RAISE|PROBE|COVER)\s+(\S+)", re.M)


def load_log(name: str) -> str:
    p = D / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def obs_rows(d: pathlib.Path) -> list[dict]:
    p = d / "goal_obs.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def arm_report(tag: str) -> dict:
    log = load_log(f"run_{tag[0].lower()}.log")
    swaps = {m.group(1): (int(m.group(2)), float(m.group(3)), float(m.group(4)),
                          m.group(5)) for m in RE_SWAP.finditer(log)}
    acts = {}
    for m in RE_ACT.finditer(log):
        acts[m.group(2)] = (m.group(1), float(m.group(3)), m.group(4))
    fails = [{"i": int(m.group(1)), "err": m.group(2), "line": m.group(3),
              "expr": (m.group(4) or "").strip()} for m in RE_FAIL.finditer(log)]
    starts = [{"src": m.group(2), "sha": m.group(3), "score": float(m.group(4)),
               "chars": int(m.group(5))} for m in RE_START.finditer(log)]
    rows = obs_rows(ARMS[tag])
    swap_rows: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("source") == "goal_swap":
            swap_rows.setdefault(r["task_id"], []).append(r)
    return {"tag": tag, "swaps": swaps, "acts": acts, "fails": fails,
            "starts": starts, "swap_rows": swap_rows}


def main() -> int:
    pf = json.loads((D / "preflight_v251.json").read_text(encoding="utf-8"))
    refs = pf["line_ref"]
    base = pf["best"]
    rep = {t: arm_report(t) for t in ARMS}

    print("=" * 78)
    print("V251  生产链路 A/B：同一账本起点，只差动作空间")
    print("=" * 78)

    # ---- pairing gate P3 ----
    cells = sorted(rep["A_off"]["swaps"])
    pair_ok = 0
    print(f"\n[P3 配对] 两臂 goals-act 分数逐格比对（换路只在 act 判 NOT_MET 后触发）")
    for c in cells:
        a = rep["A_off"]["acts"].get(c)
        b = rep["B_line"]["acts"].get(c)
        same = bool(a and b and a[1] == b[1])
        pair_ok += same
        print(f"  {c:24s} A={a[1] if a else '-':<7} B={b[1] if b else '-':<7} "
              f"{'同' if same else '★不同'}")
    only_a = sorted(set(rep['A_off']['swaps']) - set(rep['B_line']['swaps']))
    only_b = sorted(set(rep['B_line']['swaps']) - set(rep['A_off']['swaps']))
    print(f"  只进 A 的格：{only_a or '无'}｜只进 B 的格：{only_b or '无'}")
    print(f"  P3 = {'成立' if pair_ok == len(cells) and not only_a and not only_b else '不成立'} "
          f"({pair_ok}/{len(cells)})")

    # ---- main table ----
    print(f"\n[主读数] base -> after（装置打印）")
    raised = {}
    for tag in ("A_off", "B_line"):
        r = rep[tag]
        n_raise = 0
        print(f"\n  {tag}")
        for c in sorted(r["swaps"]):
            k, b0, b1, v = r["swaps"][c]
            up = b1 > b0 + 1e-12
            n_raise += up
            print(f"    {c:24s} K={k} {b0:.4f} -> {b1:.4f} {v:8s} "
                  f"{'↑抬高' if up else '—'}")
        raised[tag] = n_raise
    print(f"\n  抬高格数：A_off={raised['A_off']}  B_line={raised['B_line']} "
          f"| 差 = {raised['B_line'] - raised['A_off']}")

    # ---- P1 ----
    diff = raised["B_line"] - raised["A_off"]
    # 预注册 §3 的三值映射（⛔ 负向不是 UNKNOWN：P3 已证明配对成立，所以可判）
    verdict = ("成立" if diff >= 2 else ("不成立（负向 ⇒ 立案）" if diff < 0 else "不成立"))
    print(f"  P1（B-A >= 2）：{verdict}（差 {diff}）")

    # ---- P4 fail-closed ----
    print(f"\n[P4 fail-closed]")
    for tag in ("A_off", "B_line"):
        fails = rep[tag]["fails"]
        kinds: dict[str, int] = {}
        for f in fails:
            k = f["err"]
            if k.startswith("edit:"):
                k = k[5:]
            k = k.split(":")[0].split()[0]
            kinds[k] = kinds.get(k, 0) + 1
        print(f"  {tag}: n_fail={len(fails)} kinds={kinds or '无'}")

    # ---- P5 LINE vs ref ----
    print(f"\n[P5 定位] B 臂 LINE 与 trace 指认的失效行")
    r = rep["B_line"]
    hit = 0
    tot = 0
    for c, rows in sorted(r["swap_rows"].items()):
        lines = [x.get("line") for x in rows]
        ref = refs.get(c)
        if ref is None or not lines:
            continue
        tot += 1
        got = [x for x in lines if x is not None]
        okk = bool(got) and all(x == ref for x in got)
        hit += okk
        print(f"  {c:24s} LINE={lines} ref={ref} {'命中' if okk else '未命中'}")
    print(f"  P5 = {hit}/{tot}")

    # ---- swap candidate scores ----
    print(f"\n[候选] 每格 swap 候选分数（来自 goal_obs）")
    for tag in ("A_off", "B_line"):
        print(f"  {tag}")
        for c, rows in sorted(rep[tag]["swap_rows"].items()):
            sc = [round(float(x["score"]), 4) for x in rows]
            best_c = max(sc) if sc else None
            print(f"    {c:24s} {sc} best={best_c} (base {base.get(c)})")

    out = {"pair_ok": pair_ok, "n_cells": len(cells), "raised": raised,
           "p1_diff": diff, "p1": verdict, "p5": f"{hit}/{tot}",
           "acts": {t: rep[t]["acts"] for t in rep},
           "swaps": {t: rep[t]["swaps"] for t in rep},
           "fails": {t: rep[t]["fails"] for t in rep}}
    (D / "readings_v251.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n落盘 readings_v251.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
