#!/usr/bin/env python3
"""V102 读数：拆混淆 —— 3 臂 × 3 份只差一句话题面（`tests` 逐字节相同）。

用法：python3 bench/evidence/v6_s191/read_v102.py /tmp/v102

§1 矩阵　§2 判据 U0~U5　§3 核心（U1/U2）　§4 送达自检（分叉法）　§5 盆表　§6 对账
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]
SHORT = {t: t.split("_")[0] for t in TASKS}
PROMPTS = ["P0", "P1", "P2"]
ARMS = ["A0", "A1", "A3"]
EDIT = {"A0": "无（基线）", "A1": "ZK2#34 、→,", "A3": "ZK2#18 ，→,"}
PDESC = {"P0": "原样", "P1": "（惯例不在题面里）→（这几条惯例没有写在题面上）",
         "P2": "惯例处理→惯例来做（同字数）"}
#: V100 r0 的四题指纹
V100_R0 = {"g7d2_dedupe_keep": "1ac9e8da", "g7d3_fmt_amount": "075b4099",
           "g7e1_parse_flag": "c1f49180", "g7e2_truncate_tail": "c21ee3b8"}


def answers(d: pathlib.Path, tag: str = "") -> dict[str, str]:
    cands = [d / "bench" / "answers.jsonl", d / "answers.jsonl",
             pathlib.Path(__file__).resolve().parent / "answers" / (tag or d.name) / "answers.jsonl"]
    p = next((c for c in cands if c.exists()), None)
    if p is None:
        return {}
    out = {}
    for ln in p.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r.get("kind") != "answer":
            continue
        out[r["task_id"]] = r.get("answer") or ""
    return out


def scores(d: pathlib.Path, tag: str = "") -> dict[str, float]:
    p = d / "bench" / "runs.jsonl"
    if p.exists():
        rec = json.loads(p.read_text().splitlines()[0])
        per = (rec.get("report") or {}).get("per_task") or []
        return {t["id"]: t.get("score") for t in per}
    sp = pathlib.Path(__file__).resolve().parent / "scores.json"
    if sp.exists():
        return json.loads(sp.read_text(encoding="utf-8")).get(tag or d.name, {})
    return {}


def fp(a: dict[str, str], t: str) -> str:
    return hashlib.sha256((a.get(t) or "").encode()).hexdigest()[:8]


def mean(sc: dict) -> float:
    if not sc:
        return float("nan")
    return sum(v for v in sc.values() if v is not None) / len(TASKS)


def main() -> None:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v102")
    S, A = {}, {}
    for p in PROMPTS:
        for arm in ARMS:
            tag = f"{arm}__{p}"
            d = root / tag
            S[(arm, p)] = scores(d, tag)
            A[(arm, p)] = answers(d, tag)
    print(f"## §0 覆盖：{sum(1 for v in S.values() if v)}/{len(PROMPTS) * len(ARMS)} 跑有分数")
    miss = [f"{a}__{p}" for p in PROMPTS for a in ARMS if not S[(a, p)]]
    if miss:
        print("  ⛔ 缺：" + " ".join(miss))

    print("\n## §1 矩阵（mean ｜ 四题）")
    print(f"  {'臂':4s} {'ZK2 改动':16s} " + " ".join(f"{p:>26s}" for p in PROMPTS))
    for arm in ARMS:
        cells = [f"{mean(S[(arm, p)]):26.2f}" for p in PROMPTS]
        print(f"  {arm:4s} {EDIT[arm]:16s} " + " ".join(cells))
    for p in PROMPTS:
        print(f"  -- {p}：{PDESC[p]} --")
        for arm in ARMS:
            sc = S[(arm, p)]
            if not sc:
                print(f"     {arm}: （缺）")
                continue
            print(f"     {arm}  " + "  ".join(f"{SHORT[t]}={sc.get(t)}" for t in TASKS))

    print("\n## §2 判据（按 PREREG 原文）")
    # U0
    ok = [t for t in TASKS if fp(A[("A0", "P0")], t) == V100_R0[t]]
    m0 = mean(S[("A0", "P0")])
    u0 = len(ok) == len(TASKS) and abs(m0 - 0.85) < 1e-9
    print(f"  U0 A0@P0 mean={m0:.2f}（期望 0.85）、指纹与 V100 r0 相同 {len(ok)}/{len(TASKS)}"
          f" ⇒ {'✓ 装置没漂 + 派生副本没走样' if u0 else '⛔ 作废'}")

    # U4 先算（它是 U1/U2 的前提）
    print("  U4 送达自检：A1/A3 的 g7d3 与 A0 是否分叉（§169.4 分叉法）")
    live = {}
    for p in PROMPTS:
        base = fp(A[("A0", p)], "g7d3_fmt_amount")
        div = [a for a in ("A1", "A3") if fp(A[(a, p)], "g7d3_fmt_amount") != base]
        live[p] = bool(div)
        print(f"     {p}: A0={base} ⇒ 分叉 {div if div else '无'} ⇒ "
              f"{'送达仍在' if div else '**零送达，U1/U2 这两格作废**'}")

    # U1
    print("  U1 核心：P1/P2 上 A0 的四题分数与 P0 比")
    u1 = {}
    for p in ("P1", "P2"):
        if not live[p]:
            u1[p] = "作废（U4 未过）"
            print(f"     {p}: {u1[p]}")
            continue
        diff = [f"{SHORT[t]}:{S[('A0', 'P0')].get(t)}→{S[('A0', p)].get(t)}"
                for t in TASKS if S[("A0", p)].get(t) != S[("A0", "P0")].get(t)]
        if not diff:
            u1[p] = "全同"
        elif len(diff) == len(TASKS):
            u1[p] = "全不同"
        else:
            u1[p] = f"部分不同（{len(diff)}/{len(TASKS)}）"
        print(f"     {p}: {u1[p]}　{diff or ''}")

    # U2
    print("  U2 A1 是否仍严格高于 A0")
    u2 = {}
    for p in PROMPTS:
        d = mean(S[("A1", p)]) - mean(S[("A0", p)])
        if not live[p]:
            u2[p] = "作废（U4 未过）"
        elif d > 1e-9:
            u2[p] = f"保持（+{d:.2f}）"
        elif abs(d) <= 1e-9:
            u2[p] = "塌平（±0.00）"
        else:
            u2[p] = f"**反转**（{d:.2f}）"
        print(f"     {p}: {u2[p]}")

    # U3
    print("  U3 同盆必同分（跨三份题面）")
    by = {}
    for p in PROMPTS:
        for arm in ARMS:
            sc = S[(arm, p)]
            if not sc:
                continue
            for t in TASKS:
                by.setdefault((t, fp(A[(arm, p)], t)), set()).add(sc.get(t))
    bad = [(t.split("_")[0], h, ss) for (t, h), ss in by.items() if len(ss) > 1]
    print(f"     破的格：{bad if bad else '无'} ⇒ {'✓' if not bad else '⛔ 立刻停'}")

    print("\n## §3 每题在 9 格里的实现数 vs 分数数")
    for t in TASKS:
        imp = {}
        for p in PROMPTS:
            for arm in ARMS:
                imp.setdefault(fp(A[(arm, p)], t), set()).add(S[(arm, p)].get(t))
        print(f"  {SHORT[t]:5s} 实现 {len(imp)} 种 / 分数 "
              f"{len({x for v in imp.values() for x in v})} 种　"
              + "  ".join(f"{h}:{sorted(v)}" for h, v in imp.items()))

    print("\n## §4 盆表（g7d3）")
    for p in PROMPTS:
        print(f"  {p}  " + "  ".join(f"{a}:{fp(A[(a, p)], 'g7d3_fmt_amount')}"
                                     f"({S[(a, p)].get('g7d3_fmt_amount')})" for a in ARMS))

    print("\n## §5 与 PREREG §4 对账")
    print("  U1 押「部分不同」⇒ 实得 " + " / ".join(f"{p}={u1[p]}" for p in ("P1", "P2")))
    print("  U2 押「保持」　⇒ 实得 " + " / ".join(f"{p}={u2[p]}" for p in PROMPTS))
    print("  U4 押「仍分叉」⇒ 实得 " + " / ".join(f"{p}={'是' if live[p] else '否'}" for p in PROMPTS))


if __name__ == "__main__":
    main()
