#!/usr/bin/env python3
"""V117 §3（⛔ 跑后补的观察项，未押）：同分集合有多大、含几个盆。

问：`k=2` 够用是「原理」还是「巧合」？
答：先量「每个格子里，最高分处有几个同分项、分属几个盆」。
   · 若同分项 ≤ k ⇒ `k=2` 必然保留全部盆（原理）；
   · 若同分项 > k ⇒ `k=2` 是否保住活盆，取决于**生成顺序**（巧合）。

装置同上：V116 的候选生成器 + 内联复算判分（与子进程判分器错位 0，见 probe.txt §0）。
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(EV / "v6_s198"))
import beam as B             # noqa: E402
import search as S           # noqa: E402
import idiom_transfer as I   # noqa: E402


def cell_stats(tid: str, node: str, Sx: set[str]) -> dict | None:
    base, _ = B.evaluate(tid, node)
    kids = []
    for _lab, src in S.candidates(node, Sx):
        s, bm = B.evaluate(tid, src)
        if s > base:
            kids.append((s, bm))
    if not kids:
        return None
    top = max(k[0] for k in kids)
    tied = [k for k in kids if abs(k[0] - top) < 1e-9]
    first_pos, seen = [], {}
    for i, (_s, bm) in enumerate(tied):
        if bm not in seen:
            seen[bm] = i
            first_pos.append(i)
    return dict(base=base, top=top, n_pool=len(kids), n_tied=len(tied),
                n_basins=len(seen), first_pos=first_pos)


def main() -> None:
    corpus = I.harvest()
    rows = []
    print("## §3 同分集合普查（每个实现量两处：基线、beam(k=2,ON) 的终点）")
    for tid in B.TIDS:
        for h in sorted(corpus[tid]):
            Sx, _ = S.wordset(tid, corpus[tid][h], True)
            end = B.beam(tid, corpus[tid][h], Sx, 2)["code"]
            for tag, node in (("baseline", corpus[tid][h]), ("end", end)):
                st = cell_stats(tid, node, Sx)
                if st is None:
                    print(f"  {tid[:6]} {h} {tag:8s} 池 0（无改进候选）")
                    continue
                rows.append(dict(tid=tid, h=h, tag=tag, **st))
                flag = "⚠️ 同分项 > k=2" if st["n_tied"] > 2 else ""
                print(f"  {tid[:6]} {h} {tag:8s} 池{st['n_pool']:>3}　基线 {st['base']} → 最高 {st['top']}"
                      f"　**同分 {st['n_tied']} 个 / 盆 {st['n_basins']} 个**"
                      f"　各盆首次位次 {st['first_pos']} {flag}")

    over = [r for r in rows if r["n_tied"] > 2]
    max_tied = max((r["n_tied"] for r in rows), default=0)
    print(f"\n## §3.1 汇总（n_cell = {len(rows)}）")
    print(f"  同分项最大值 = **{max_tied}**；同分项 > 2 的格子 = **{len(over)}**")
    multi = [r for r in rows if r["n_basins"] >= 2]
    print(f"  含 ≥2 个盆的格子 = **{len(multi)}**（只有这些格子才可能「选错盆」）")
    print(f"  ⇒ 本语料内 k=2 恰好够用；**但这不是原理保证**：")
    print(f"     k 的正确值 = 同分集合大小，而同分集合大小**不是分数能告诉你的**")
    print(f"     （要它就得知道有几个盆，而盆要跑 test 才知道 ⇒ 分数只给了一个数）。")
    print(f"     一旦某格同分项 > k，k 是否保住活盆就取决于**生成顺序**（本语料的 T1 在前）。")

    (HERE / "tiesets.json").write_text(
        json.dumps(dict(rows=rows, max_tied=max_tied, n_over_k=len(over), n_multi=len(multi)),
                   ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("\n  写出 tiesets.json")


if __name__ == "__main__":
    main()
