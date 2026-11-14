#!/usr/bin/env python3
"""V103 读数：零语义扰动 —— P3（换括号码位）/ P4（加一个空格）vs V102 的 P0/P1/P2。

用法：python3 bench/evidence/v6_s192/read_v103.py [P3P4 根目录] [P0~P2 根目录]

§1 五份题面矩阵　§2 判据 V0~V4　§3 核心：g7d3 的盆在哪　§4 同盆必同分　§5 对账
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]
SHORT = {t: t.split("_")[0] for t in TASKS}
ARMS = ["A0", "A1"]
DESC = {"P0": "原样（V102）", "P1": "换说法 +5 字（V102）", "P2": "同字数换词（V102）",
        "P3": "**零换字**：全角括号→ASCII", "P4": "**零换字**：句内加一个空格"}
HERE = pathlib.Path(__file__).resolve().parent
OLD = pathlib.Path(__file__).resolve().parent.parent / "v6_s191"


def _read_answers(p: pathlib.Path) -> dict[str, str]:
    out = {}
    for ln in p.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r.get("kind") != "answer":
            continue
        out[r["task_id"]] = r.get("answer") or ""
    return out


def answers(tag: str, root: pathlib.Path, arch: pathlib.Path) -> dict[str, str]:
    for p in (root / tag / "bench" / "answers.jsonl", root / tag / "answers.jsonl",
              arch / "answers" / tag / "answers.jsonl"):
        if p.exists():
            return _read_answers(p)
    return {}


def scores(tag: str, root: pathlib.Path, arch: pathlib.Path) -> dict:
    p = root / tag / "bench" / "runs.jsonl"
    if p.exists():
        rec = json.loads(p.read_text().splitlines()[0])
        per = (rec.get("report") or {}).get("per_task") or []
        return {t["id"]: t.get("score") for t in per}
    sp = arch / "scores.json"
    if sp.exists():
        return json.loads(sp.read_text(encoding="utf-8")).get(tag, {})
    return {}


def fp(a: dict[str, str], t: str) -> str:
    return hashlib.sha256((a.get(t) or "").encode()).hexdigest()[:8]


def mean(sc: dict) -> float:
    if not sc:
        return float("nan")
    return sum(v for v in sc.values() if v is not None) / len(TASKS)


def main() -> None:
    r3 = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v103")
    r2 = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/v102")
    S, A = {}, {}
    for p, root, arch in (("P0", r2, OLD), ("P1", r2, OLD), ("P2", r2, OLD),
                          ("P3", r3, HERE), ("P4", r3, HERE)):
        for arm in ARMS:
            tag = f"{arm}__{p}"
            S[(arm, p)] = scores(tag, root, arch)
            A[(arm, p)] = answers(tag, root, arch)

    print("## §1 五份题面（mean）")
    print(f"  {'臂':4s} " + " ".join(f"{p:>12s}" for p in DESC))
    for arm in ARMS:
        print(f"  {arm:4s} " + " ".join(f"{mean(S[(arm,p)]):12.2f}" for p in DESC))
    for p in DESC:
        print(f"  -- {p}：{DESC[p]}")
        for arm in ARMS:
            sc = S[(arm, p)]
            print(f"     {arm}  " + ("（缺）" if not sc else
                                     "  ".join(f"{SHORT[t]}={sc.get(t)}" for t in TASKS)))

    print("\n## §2 判据（按 PREREG 原文）")
    print("  V0 前置：P0/P1/P2 取 V102 归档读数（不重跑），依赖已证确定性")

    print("  V1/V2 核心：A0 的 g7d3 盆在每一份题面上的位置")
    base = "075b4099"   # P0 上 A0 的 g7d3 盆（= 0.4）
    tgt = "5755e033"    # V102 里 P1/P2 上 A0 落到的盆（= 0.6）
    for p in DESC:
        h = fp(A[("A0", p)], "g7d3_fmt_amount")
        sc = S[("A0", p)].get("g7d3_fmt_amount")
        mark = "← 基线盆" if h == base else ("← 与 P1/P2 **同一个盆**" if h == tgt else "← 第三个盆")
        print(f"     A0 {p}: {h}({sc})  {mark}")
    v1 = fp(A[("A0", "P3")], "g7d3_fmt_amount")
    v2 = fp(A[("A0", "P4")], "g7d3_fmt_amount")
    verdict = lambda h: ("落同一盆 `5755e033` ⇒ **效应 = 扰动，与语义无关**" if h == tgt
                         else ("不动（仍在基线盆 `075b4099`）⇒ 零语义改动没进场" if h == base
                               else f"落到第三个盆 `{h}`"))
    print(f"  V1（P3，零换字）⇒ {verdict(v1)}")
    print(f"  V2（P4，加一空格）⇒ {verdict(v2)}")

    print("  V3 A1 在 P3/P4 上是否仍 1.00")
    for p in ("P3", "P4"):
        sc = S[("A1", p)].get("g7d3_fmt_amount")
        print(f"     A1 {p}: g7d3={sc}  整跑 mean={mean(S[('A1', p)]):.2f} ⇒ "
              + ("**仍 1.00**" if sc == 1.0 else "**掉了**"))

    print("  V4 同盆必同分（跨 5 份题面）")
    # ⚠️ PREREG 漏了这一条（已登记）：每一格必须先有「送达读数」，否则分数不可解释
    print("  §2b 送达读数（PREREG 漏项，事后补；法 = V101 §169.4 的分叉法）")
    for p in DESC:
        same = [t for t in TASKS
                if fp(A[("A0", p)], t) == fp(A[("A1", p)], t)]
        print(f"     {p}: A0 vs A1 逐字节相同 {len(same)}/4"
              f" ⇒ {'**零送达**（两臂无差别）' if len(same) == len(TASKS) else '送达仍在'}"
              + ("" if len(same) == len(TASKS) else f"（异：{[t.split('_')[0] for t in TASKS if t not in same]}）"))
    by = {}
    for p in DESC:
        for arm in ARMS:
            sc = S[(arm, p)]
            if not sc:
                continue
            for t in TASKS:
                by.setdefault((t, fp(A[(arm, p)], t)), set()).add(sc.get(t))
    bad = [(t.split("_")[0], h, ss) for (t, h), ss in by.items() if len(ss) > 1]
    print(f"     破的格：{bad if bad else '无'} ⇒ {'✓' if not bad else '⛔ 立刻停'}")

    print("\n## §3 g7d3 的五份盆（核心表）")
    for p in DESC:
        print(f"  {p}  A0:{fp(A[('A0',p)],'g7d3_fmt_amount')}"
              f"({S[('A0',p)].get('g7d3_fmt_amount')})  "
              f"A1:{fp(A[('A1',p)],'g7d3_fmt_amount')}({S[('A1',p)].get('g7d3_fmt_amount')})")

    print("\n## §4 每题在 10 格里的实现数 vs 分数数")
    for t in TASKS:
        imp = {}
        for p in DESC:
            for arm in ARMS:
                imp.setdefault(fp(A[(arm, p)], t), set()).add(S[(arm, p)].get(t))
        print(f"  {SHORT[t]:5s} 实现 {len(imp)} 种 / 分数 "
              f"{len({x for v in imp.values() for x in v})} 种　"
              + "  ".join(f"{h}:{sorted(v)}" for h, v in imp.items()))


if __name__ == "__main__":
    main()
