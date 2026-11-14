#!/usr/bin/env python3
"""V117 跑前取证：第 1 轮「同分集合」里，各行为盆在**生成顺序**上怎么排。

⛔ 这只是一次取证，不是判决。目的：把「k 要多大才够」从猜变成量。
装置：V116 `search.py` 的同一候选生成器 + 同一 bitmap，分数由位图**内联复算**
（前置：V116 的 773 个候选上 `score == 位图通过率` **零错位**，本次再交叉核对一遍）。
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
sys.path.insert(0, str(EV / "v6_s205"))
sys.path.insert(0, str(EV / "v6_s198"))
import search as S           # noqa: E402
import idiom_transfer as I   # noqa: E402

TID = "g7e1_parse_flag"
STUCK = ["1aa5099d", "37a4f17f", "a579b07a", "f2d1efa6"]
TRACE = ["200ebf6e", "644f0618", "c1f49180", "cd7e0eb9"]


def score_inline(tid: str, code: str) -> float | None:
    bm = S.bitmap(tid, code)
    return sum(1 for x in bm if x is True) / len(bm)


def main() -> None:
    corpus = I.harvest()
    # V116 用**子进程判分器**给这 743 个候选打过 1 分；这里拿那批分数当参考，
    # 比对内联复算结果 ⇒ 零子进程开销，且参考值确实来自真判分器。
    stored = {}
    for r in json.loads((EV / "v6_s205" / "basins.json").read_text(encoding="utf-8"))[TID]:
        stored[(r["h"], r["lab"])] = r["score"]

    print("## §0 内联复算 vs 子进程判分器（交叉核对，跑前取证）")
    mism = n = 0
    for h in STUCK + TRACE:
        code = corpus[TID][h]
        Sx, _ = S.wordset(TID, code, True)
        for lab, src in S.candidates(code, Sx):
            key = (h, lab)
            if key not in stored:
                continue
            ref = stored[key]
            got = score_inline(TID, src)
            n += 1
            if ref is None or abs(ref - got) > 1e-9:
                mism += 1
                if mism <= 5:
                    print(f"  ⛔ {h} {lab}: 子进程 {ref} vs 内联 {got} {S.bitmap(TID, src)}")
    print(f"  比对 {n} 个候选（V116 子进程分数 vs 本轮内联复算）⇒ **错位 {mism}**")

    for h in STUCK:
        code = corpus[TID][h]
        Sx, _ = S.wordset(TID, code, True)
        base = score_inline(TID, code)
        rows = []
        for lab, src in S.candidates(code, Sx):
            sc = score_inline(TID, src)
            rows.append((lab, sc, tuple(S.bitmap(TID, src))))
        best = max(r[1] for r in rows)
        tied = [r for r in rows if abs(r[1] - best) < 1e-9]
        print(f"\n## {h}（基线 {base}）第 1 轮：候选 {len(rows)}，最高分 {best}，同分 {len(tied)} 个")
        # 按生成顺序列出，标出第几个是「新盆」
        seen = {}
        for i, (lab, sc, bm) in enumerate(tied):
            first = bm not in seen
            seen.setdefault(bm, i)
            print(f"  #{i + 1:>2}  {lab:22s} {sc}  {list(bm)}"
                  f"{'   ← 新盆（第 1 个出现）' if first else ''}")
        print(f"  ⇒ 同分集合里的盆数 = {len(seen)}，各盆**首次出现的位次** = "
              f"{sorted(seen.values())}（0 起算）")
        print(f"  ⇒ 要保住「第一个之后的盆」，beam 宽度至少要 = "
              f"{sorted(seen.values())[1] + 1 if len(seen) > 1 else 'N/A'}")


if __name__ == "__main__":
    main()
