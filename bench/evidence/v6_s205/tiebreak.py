#!/usr/bin/env python3
"""V116 §5：盆的可达性 —— 同一信息、同一模板集，只因为第一轮落在不同的 0.8 盆，终点差多少。

装置：`search.py` 的同一候选生成器 + 同一打分器 + **ON 臂词表**（信息最全的那一臂）。
做法：对 g7e1 每个贪心停在 0.8 的实现，取它**每一个不同的 0.8 行为盆**各一个代表，
      从该代表继续爬一轮（只一轮），记录能否到 1.0。

⇒ 这是「同分多盆」与「贪心失败」之间的因果链，而不是又一次相关。
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(EV / "v6_s198"))
import search as S          # noqa: E402
import idiom_transfer as I  # noqa: E402

TID = "g7e1_parse_flag"


def main() -> None:
    corpus = I.harvest()
    greedy = json.loads((HERE / "search.json").read_text(encoding="utf-8"))["arms"]
    stuck = [r for r in greedy[f"{TID}::ON"] if r["s1"] == 0.8]
    print("## §5 盆的可达性（词表 = ON 臂，信息最全）")
    print(f"  贪心停在 0.8 的实现 {len(stuck)}/{len(greedy[TID + '::ON'])}"
          f"：{[r['h'] for r in stuck]}")

    rows = []
    for r in stuck:
        h = r["h"]
        code = corpus[TID][h]
        Sx, _ = S.wordset(TID, code, True)
        basins = collections.OrderedDict()
        for lab, src in S.candidates(code, Sx):
            sc = S.score_of(TID, src)
            if sc != 0.8:
                continue
            bm = tuple(S.bitmap(TID, src))
            if bm in basins:                      # 每个盆只取一个代表
                continue
            basins[bm] = (lab, src)
        print(f"\n  **{h}**（基线 {r['s0']}）0.8 盆 {len(basins)} 个")
        for bm, (lab, src) in basins.items():
            reach, best_lab, best_s = None, None, 0.8
            for lab2, src2 in S.candidates(src, Sx):
                s2 = S.score_of(TID, src2)
                if s2 is not None and s2 > best_s:
                    best_lab, best_s = lab2, s2
            reach = best_s
            rows.append(dict(h=h, basin=list(bm), entry=lab, best=reach, move=best_lab))
            print(f"    {list(bm)}  入口 {lab}　再爬一轮最好 → **{reach}**"
                  f"{'（' + str(best_lab) + '）' if best_lab else ''}"
                  f"　{'✅ 可达 1.0' if reach == 1.0 else '⛔ 死盆'}")

    print("\n## §5.1 汇总：盆 → 可达性")
    agg = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        k = tuple(r["basin"])
        agg[k][0] += 1
        agg[k][1] += 1 if r["best"] == 1.0 else 0
    for k, (n, ok) in sorted(agg.items(), key=lambda kv: -kv[1][1]):
        print(f"  {list(k)}　盆 {n} 个　可达 1.0 的 {ok} 个　"
              f"{'✅ 活盆' if ok else '⛔ 死盆'}")

    n_dead = sum(1 for r in rows if r["best"] != 1.0)
    print(f"\n  **同一信息、同一模板集，{len(rows)} 个 0.8 入口里 {n_dead} 个是死盆**"
          f"（{n_dead}/{len(rows)} = {n_dead / len(rows):.1%}）")
    print("  ⇒ 贪心若不选这些盆，就能到 1.0；它选了，因为**它只看分数**。")

    (HERE / "tiebreak.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str),
                                        encoding="utf-8")
    print("\n  写出 tiebreak.json")


if __name__ == "__main__":
    main()
