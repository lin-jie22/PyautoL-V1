#!/usr/bin/env python3
"""V101 读数：换尺子 —— 四臂 × 四套只差 `prompt` 的题面（`tests_sha` 逐题相同）。

用法：python3 bench/evidence/v6_s190/read_v101.py /tmp/v101 [归档根目录]

§1 分数矩阵　§2 判据 T0~T4　§3 核心：两组「同分不同字节」能不能被分开
§4 跨套件的盆（指纹）表　§5 与 PREREG §4 对账
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]
SHORT = {t: t.split("_")[0] for t in TASKS}
SUITES = ["g7j_n4_heldout", "g7j_n0_heldout", "g7e_heldout", "g7e_hint_heldout"]
ARMS = ["A0", "A1", "A2", "A3"]
EDIT = {"A0": "无", "A1": "ZK2#34 、→,", "A2": "ZK2#39 。→.", "A3": "ZK2#18 ，→,"}
#: V100 r0 的四题指纹（T0 的装置指纹）
V100_R0 = {"g7d2_dedupe_keep": "1ac9e8da", "g7d3_fmt_amount": "075b4099",
           "g7e1_parse_flag": "c1f49180", "g7e2_truncate_tail": "c21ee3b8"}


def answers(d: pathlib.Path, tag: str = "") -> dict[str, str]:
    """⭐ 兼容两种取法：<run>/bench/answers.jsonl（原跑目录）、
    <本脚本目录>/answers/<tag>/answers.jsonl（归档副本，`/tmp` 清掉后仍可复核）。"""
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


def _scores_json() -> dict:
    """归档分数表：`<本脚本目录>/scores.json`，键 = `<臂>__<套件>`。"""
    p = pathlib.Path(__file__).resolve().parent / "scores.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def scores(d: pathlib.Path, tag: str = "") -> dict[str, float]:
    p = d / "bench" / "runs.jsonl"
    if p.exists():
        rec = json.loads(p.read_text().splitlines()[0])
        per = (rec.get("report") or {}).get("per_task") or []
        return {t["id"]: t.get("score") for t in per}
    return _scores_json().get(tag or d.name, {})


def fp(a: dict[str, str], t: str) -> str:
    return hashlib.sha256((a.get(t) or "").encode()).hexdigest()[:8]


def main() -> None:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v101")
    S, A = {}, {}
    for s in SUITES:
        for arm in ARMS:
            tag = f"{arm}__{s}"
            d = root / tag
            S[(arm, s)] = scores(d, tag)
            A[(arm, s)] = answers(d, tag)
    got = [k for k, v in S.items() if v]
    print(f"## §0 覆盖：{len(got)}/{len(SUITES) * len(ARMS)} 跑有分数")
    missing = [f"{a}__{s}" for s in SUITES for a in ARMS if not S[(a, s)]]
    if missing:
        print("  ⛔ 缺：" + " ".join(missing))

    print("\n## §1 分数矩阵（mean ｜ 四题）")
    print(f"  {'臂':4s} {'ZK2 改动':16s} " + " ".join(f"{s.replace('_heldout', ''):>13s}" for s in SUITES))
    for arm in ARMS:
        cells = []
        for s in SUITES:
            sc = S[(arm, s)]
            m = sum(v for v in sc.values() if v is not None) / len(TASKS) if sc else float("nan")
            cells.append(f"{m:13.2f}")
        print(f"  {arm:4s} {EDIT[arm]:16s} " + " ".join(cells))
    for s in SUITES:
        print(f"  -- {s} 逐题 --")
        for arm in ARMS:
            sc = S[(arm, s)]
            if not sc:
                print(f"     {arm}: （缺）")
                continue
            print(f"     {arm}  " + "  ".join(f"{SHORT[t]}={sc.get(t)}" for t in TASKS))

    print("\n## §2 判据（按 PREREG 原文）")
    # T0
    ok = [t for t in TASKS if fp(A[("A0", "g7j_n4_heldout")], t) == V100_R0[t]]
    sc0 = S[("A0", "g7j_n4_heldout")]
    m0 = sum(v for v in sc0.values() if v is not None) / len(TASKS) if sc0 else -1
    t0 = len(ok) == len(TASKS) and abs(m0 - 0.85) < 1e-9
    print(f"  T0 装置没漂：A0@g7j_n4 mean={m0:.2f}（期望 0.85）、"
          f"四题指纹与 V100 r0 相同 {len(ok)}/{len(TASKS)} ⇒ {'✓' if t0 else '⛔ 作废'}")

    # T1：两组「同分不同字节」
    print("  T1 核心：换题面后，两组『同分不同字节』能不能被分开")
    pairs = [("(A0,A3) 在 n4 上都 0.85", "A0", "A3", "g7j_n4_heldout"),
             ("(A1,A2) 在 n4 上都 1.00", "A1", "A2", "g7j_n4_heldout")]
    split_any = 0
    for desc, a, b, home in pairs:
        # 以 home 套件上的分数为「同分」基准，逐套件比较两臂的四题向量
        print(f"     {desc}")
        for s in SUITES:
            va = [S[(a, s)].get(t) for t in TASKS]
            vb = [S[(b, s)].get(t) for t in TASKS]
            if None in va or None in vb:
                print(f"       {s:18s} （缺）")
                continue
            diff = [f"{SHORT[t]}:{x}≠{y}" for t, x, y in zip(TASKS, va, vb) if x != y]
            ha = [fp(A[(a, s)], t) for t in TASKS]
            hb = [fp(A[(b, s)], t) for t in TASKS]
            bytesame = sum(1 for x, y in zip(ha, hb) if x == y)
            mark = "**分开**" if diff else "黏住"
            if diff:
                split_any += 1
            print(f"       {s:18s} {mark}　分数差异={diff or '无'}　答案逐字节同 {bytesame}/4")
    print(f"     ⇒ 分开的组数 = {split_any} ⇒ "
          + ("**存在第二个观测通道**" if split_any else "分数看不见实现是**普遍**的（本题集内）"))

    # T2：g7e 上四臂全同
    base = None
    same_all = True
    for arm in ARMS:
        sc = S[(arm, "g7e_heldout")]
        v = tuple(sc.get(t) for t in TASKS) if sc else None
        if base is None:
            base = v
        if v != base:
            same_all = False
            print(f"  ⛔ g7e 上 {arm} 与 A0 不同：{v} vs {base}")
    print(f"  T2 g7e（题面无 ZK 标记）四臂四题全同：{'✓' if same_all else '⛔ 破'}　基准={base}")
    hbase = [fp(A[("A0", "g7e_heldout")], t) for t in TASKS]
    hall = all([fp(A[(arm, "g7e_heldout")], t) for t in TASKS] == hbase for arm in ARMS)
    print(f"     g7e 四臂答案逐字节全同：{'✓' if hall else '⛔ 有分歧'}")

    # T3（按 PREREG §5 修订版：只押序，不押值、不押盆）
    print("  T3（修订版：只押序）g7j_n0 上的 g7d3")
    v = {arm: S[(arm, "g7j_n0_heldout")].get("g7d3_fmt_amount") for arm in ARMS}
    for arm in ARMS:
        h = fp(A[(arm, "g7j_n0_heldout")], "g7d3_fmt_amount")
        print(f"     {arm}: score={v[arm]}  盆={h}")
    ok_arms = [v[a] for a in ("A1", "A2") if v[a] is not None]
    bad_arms = [v[a] for a in ("A0", "A3") if v[a] is not None]
    if not ok_arms or not bad_arms:
        verdict = "（缺）"
    elif len(set(ok_arms + bad_arms)) == 1:
        verdict = "四臂相等"
    elif min(ok_arms) >= max(bad_arms):
        verdict = "✓ 序成立（满分在 ZK2 这条知识里）"
    else:
        verdict = "⛔ 反序（满分不是知识自己的性质）"
    print(f"     ⇒ {verdict}")

    # T4：同指纹必同分（跨四套件，因为 tests_sha 逐题相同）
    print("  T4 同答案必同分（四套 tests_sha 全等 ⇒ 构造性必然）")
    bad = []
    by = {}
    for (arm, s), sc in S.items():
        if not sc:
            continue
        for t in TASKS:
            by.setdefault((t, fp(A[(arm, s)], t)), set()).add(sc.get(t))
    for (t, h), ss in by.items():
        if len(ss) > 1:
            bad.append((t, h, ss))
    print(f"     破的格：{bad if bad else '无'} ⇒ {'✓' if not bad else '⛔ 立刻停'}")

    print("\n## §4 跨套件的盆（g7d3）")
    for s in SUITES:
        row = "  ".join(f"{arm}:{fp(A[(arm, s)], 'g7d3_fmt_amount')}({S[(arm, s)].get('g7d3_fmt_amount')})"
                        for arm in ARMS)
        print(f"  {s:18s} {row}")

    # §4b：送达的**因果**证明 —— 受控单字符改动导致输出分叉
    print("\n## §4b 送达的因果证明（受控单字符改动 → 输出是否分叉）")
    for s in SUITES:
        base = fp(A[("A0", s)], "g7d3_fmt_amount")
        div = {arm: fp(A[(arm, s)], "g7d3_fmt_amount") for arm in ARMS if arm != "A0"}
        hit = [a for a, h in div.items() if h != base]
        print(f"  {s:18s} A0={base} ⇒ 分叉：{hit if hit else '无（四臂逐字节同 ⇒ 改动没进场）'}")
    print("  ⇒ 分叉 ⇔ ZK2 那一个字符**确实进了生成**（同 prompt、同环境，唯一变量是库里的 ZK2）")

    # §5：16 格里每题有几个实现、几个分数 —— 量「分数看不见实现」有多粗
    print("\n## §5 每题在 16 格里的实现数 vs 分数数（桶化程度）")
    for t in TASKS:
        imp = {}
        for s in SUITES:
            for arm in ARMS:
                h = fp(A[(arm, s)], t)
                imp.setdefault(h, set()).add(S[(arm, s)].get(t))
        agg = "  ".join(f"{h}:{sorted(v)}" for h, v in imp.items())
        print(f"  {SHORT[t]:5s} 实现 {len(imp)} 种 / 分数 "
              f"{len({x for v in imp.values() for x in v})} 种　{agg}")
    print("  ⇒ 「实现数 > 分数数」的部分 = 分数分不开的实现（桶）；差得越多越看不见")

    print("\n## §6 与 PREREG §4/§5 对账")
    m = {s: {arm: (sum(v for v in S[(arm, s)].values() if v is not None) / len(TASKS)
                   if S[(arm, s)] else None) for arm in ARMS} for s in SUITES}
    for s in SUITES:
        vals = [m[s][a] for a in ARMS]
        uniq = len({None if v is None else round(v, 4) for v in vals})
        print(f"  {s:18s} mean " + " ".join(f"{a}={v:.2f}" if v is not None else f"{a}=—"
                                             for a, v in zip(ARMS, vals))
              + f"　⇒ {uniq} 个不同取值")


if __name__ == "__main__":
    main()
