#!/usr/bin/env python3
"""V163 §0 结构探针：`g7d3` 的候选表与可达集大小（**0 判分**，只数生成量）。

口径：
  · 状态 = 源码字符串；候选生成 = `X.gen(tid, code, Sx, ch, ex)`，其中
    `Sx/ch/ex` 固定为**原实现**的上下文（与 v162.replay 完全一致）；
  · 深度 d 的可达集 = 从原码出发、做 ≤d 步、**不判分/不剪枝/不早停**、
    只去掉重复源码后得到的状态集合；
  · 深度 d 的「新增」= 深度 d 可达集 − 深度 d-1 可达集（按源码去重）。
只打印计数，不打印判分。**这一步不许出判据**（仅测量）。
"""
import ast
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198", "v6_s210", "v6_s236"):
    sys.path.insert(0, str(EV / d))
import idiom_transfer as I          # noqa: E402
import search as S                  # noqa: E402
import v119 as V                    # noqa: E402
import v120 as W                    # noqa: E402
import v121 as X                    # noqa: E402
sys.path.insert(0, str(EV / "v6_s249"))
import v161 as M                    # noqa: E402

TID = "g7d3_fmt_amount"
MAXD = 3


def main() -> None:
    corpus = I.harvest()
    impls = []
    for h in sorted(corpus[TID]):
        try:
            ast.parse(corpus[TID][h])
        except SyntaxError:
            continue
        impls.append(h)
    s0 = {(TID, h): V.ev(TID, corpus[TID][h])[0] for h in impls}
    print(f"## {TID}：实现 {len(impls)} 个（可解析）")
    print(f"  测试条数 = {len(I.task_of(TID)['tests'])}")
    for h in impls:
        print(f"    {h[:8]}  s0={s0[(TID, h)]:.3f}")

    print("\n## 每实现的深度可达集（不判分）")
    print("  | impl | s0 | d1 生成 | d1 新状态 | d2 展开量 | d2 新状态 | d3 展开量 | d3 新状态 |")
    print("  |---|---|---|---|---|---|---|---|")
    rows = {}
    for h in impls:
        code = corpus[TID][h]
        Sx, _ = S.wordset(TID, code, True)
        ch, _m = M.chains_meta(corpus, TID, h, s0)
        ex = W.harvest_exprs(corpus, TID, h)
        seen = {code}
        frontier = [code]
        stat = []
        raw_total = 0
        for d in range(1, MAXD + 1):
            nxt, raw = [], 0
            for c in frontier:
                try:
                    cands = X.gen(TID, c, Sx, ch, ex)
                except Exception as e:                       # noqa: BLE001
                    print(f"    ⚠ {h[:8]} d{d} gen 抛 {type(e).__name__}")
                    cands = []
                raw += len(cands)
                for _lab, src in cands:
                    if src in seen:
                        continue
                    seen.add(src)
                    nxt.append(src)
            raw_total += raw
            stat.append((raw, len(nxt)))
            frontier = nxt
            if not frontier:
                break
        rows[h] = stat
        cells = " | ".join(f"{r[0]} | **{r[1]}**" for r in stat)
        print(f"  | {h[:8]} | {s0[(TID, h)]:.2f} | {cells} |")

    print("\n## 合计（不判分展开总量 / 可达状态数）")
    tots = [sum(rows[h][i][0] if i < len(rows[h]) else 0 for h in impls) for i in range(MAXD)]
    news = [sum(rows[h][i][1] if i < len(rows[h]) else 0 for h in impls) for i in range(MAXD)]
    for i in range(MAXD):
        print(f"  d{i+1}：展开 {tots[i]}｜新状态 {news[i]}")
    print(f"  合计展开 {sum(tots)}｜可达状态 {sum(news) + len(impls)}（含原码）")
    print(f"  每实现平均：{sum(tots)/len(impls):.0f} 次生成")


if __name__ == "__main__":
    main()
