#!/usr/bin/env python3
"""读两次运行读数，做**逐题配对**比较 —— A/B 实验（干预实验）的分析工具。

⛔ 为什么必须逐题配对，而不是比平均分：
    「看总分」正是 V5 的死因之一。总分涨了，可能是 1 题暴涨掩盖了 5 题退步；
    总分没动，可能是 2 题变好、2 题变差互相抵消 —— 而这两种情况的**机制完全不同**。
    配对之后才能回答真正的实验问题：
        · 有没有题**变好**？（这个变量起作用了吗）
        · 有没有题**变差**？（它在污染输入吗 —— 记忆可能是负迁移）
        · **全都不变** ⇒ 这个变量根本没有影响。这是最容易被忽略、
          也最该原样写下来的结论：它说明"机制存在于链路上" ≠ "机制产生影响"。

⛔ 它**不下"更好/更差"的判定**（那是口径闸 + paired_compare 的活，要求同口径）。
    它回答的是另一个问题：**改变这个变量，输出变了没有、往哪变。**
    两者是不同的东西，不许混：一个是"判定"，一个是"归因"。

用法
    python bench/paired_runs.py RUNS_JSONL BASE_INDEX CAND_INDEX
    行号从 1 开始（即 runs.jsonl 的第 N 行）。两个读数必须来自**同一个 suite**，
    否则逐题配对无从谈起 —— 那时它显式报错，而不是硬凑。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__)
        return 2
    runs_path, b_i, c_i = Path(argv[1]), int(argv[2]), int(argv[3])
    rows = _load(runs_path)
    if not (1 <= b_i <= len(rows)) or not (1 <= c_i <= len(rows)):
        print(f"❌ 行号超出范围（共 {len(rows)} 行）")
        return 2
    base, cand = rows[b_i - 1], rows[c_i - 1]

    b_suite = (base.get("caliber") or {}).get("suite_sha")
    c_suite = (cand.get("caliber") or {}).get("suite_sha")
    if b_suite != c_suite:
        print(f"❌ 两次读数的 suite_sha 不同（{b_suite} vs {c_suite}）"
              " ⇒ 逐题配对无从谈起（比的是不同的题）。")
        return 2

    bmap = {t["id"]: t.get("score") for t in base["report"]["per_task"]}
    cmap = {t["id"]: t.get("score") for t in cand["report"]["per_task"]}
    ids = [t["id"] for t in base["report"]["per_task"]]
    if set(ids) != set(cmap):
        print(f"❌ 题号集合不一致：只在一侧出现的有 "
              f"{sorted(set(ids) ^ set(cmap))}")
        return 2

    print("=" * 74)
    print("逐题配对比较（**不是**比总分）")
    print("=" * 74)
    print(f"  基准 #{b_i}  {base['report'].get('candidate', '')[:40]}")
    print(f"       预算={base.get('budget_tag')} 记忆={base.get('caliber', {}).get('memory_scope')}"
          f" seed={base.get('caliber', {}).get('memory_seed')}")
    print(f"  候选 #{c_i}  {cand['report'].get('candidate', '')[:40]}")
    print(f"       预算={cand.get('budget_tag')} 记忆={cand.get('caliber', {}).get('memory_scope')}"
          f" seed={cand.get('caliber', {}).get('memory_seed')}")
    print()
    print(f"  {'task':<26}{'基准':>9}{'候选':>9}{'Δ':>10}  变化")
    print("  " + "-" * 68)

    better = worse = same = 0
    deltas: list[float] = []
    for tid in ids:
        a = bmap.get(tid)
        b = cmap.get(tid)
        if a is None or b is None:
            print(f"  {tid:<26}{str(a):>9}{str(b):>9}{'—':>10}  ⚠️ 有一侧没量到")
            continue
        d = float(b) - float(a)
        deltas.append(d)
        if d > 1e-9:
            better += 1
            mark = "↑ 变好"
        elif d < -1e-9:
            worse += 1
            mark = "↓ 变差"
        else:
            same += 1
            mark = "＝ 不变"
        print(f"  {tid:<26}{float(a):>9.4f}{float(b):>9.4f}{d:>+10.4f}  {mark}")

    n = len(deltas)
    mean_d = (sum(deltas) / n) if n else 0.0
    print("  " + "-" * 68)
    print(f"  题数 {n}   变好 {better}   变差 {worse}   不变 {same}"
          f"   mean Δ = {mean_d:+.4f}")
    print()
    if n and same == n:
        print("  ⚠️ **每一题都不变** ⇒ 这个变量没有影响。")
        print("     这是本工具最该被认真对待的结论：它说明"
              "\"机制存在于链路上\" ≠ \"机制产生了影响\"。")
    elif worse and not better:
        print("  ⚠️ 只有变差 ⇒ 这个变量在**负迁移**（很可能是在污染输入）。")
    elif better and not worse:
        print("  ℹ️ 只有变好 ⇒ 该变量产生了单向影响。")
        print("     ⚠️ 但单次实验**不足以**断定方向：需要 repeat 或多题集再看。")
    else:
        print("  ℹ️ 有增有减 ⇒ 影响存在但方向不一致（净效应可能接近 0）。")
    print("  ⛔ 本工具不下\"更好/更差\"的判定 —— 那是口径闸的活（要求同口径）。")
    print("     它只回答：改变这个变量，输出**变了没有、往哪变**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
