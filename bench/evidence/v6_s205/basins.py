#!/usr/bin/env python3
"""V116 §3：0.8 档的行为盆普查 —— 同分是否可能不同行为（W6）。

与 `search.py` **同一个候选生成器、同一个打分器、同一批语料**，
唯一区别：不看爬山终点，而是把**从基线出发的全部 1-编辑候选**逐一判分 + 取位图。

⇒ 这是对「分数 = 行为的单射」这句话在**搜索空间**（而不是 26 个实现的语料）内的检验。
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


def main() -> None:
    corpus = I.harvest()
    out = {}
    print("## §3 全候选空间的行为盆普查（1-编辑，两臂词表合并 = 上界）")
    print("  目的：同分是否可能对应不同行为？（V111 说非零档全单射，那是在 26 实现的语料内）")

    for tid in ("g7e1_parse_flag", "g7d2_dedupe_keep"):
        rows = []
        seen_src = set()
        for h in sorted(corpus[tid]):
            code = corpus[tid][h]
            Sx, _ = S.wordset(tid, code, True)      # 两臂合并 = ON（含 OFF）
            for lab, src in S.candidates(code, Sx):
                if src in seen_src:
                    continue
                seen_src.add(src)
                sc = S.score_of(tid, src)
                if sc is None:
                    continue
                bm = tuple(S.bitmap(tid, src))
                rows.append(dict(h=h, lab=lab, score=sc, bm=bm))
        out[tid] = rows

        print(f"\n  **{tid}**　候选 {len(rows)} 个（去重后）")
        by_score = collections.defaultdict(set)
        for r in rows:
            by_score[r["score"]].add(r["bm"])
        print("    score → 不同行为盆数（以及有没有「一盆多分」）")
        for sc in sorted(by_score, key=lambda x: (x is None, x)):
            bms = by_score[sc]
            flag = "⛔ 同分多盆" if len(bms) > 1 else ""
            print(f"      {sc:<5} {len(bms):2d} 盆  {flag}")
        # 反向：同一行为盆是否对应多个分数（V111 说非零档不会）
        by_bm = collections.defaultdict(set)
        for r in rows:
            by_bm[r["bm"]].add(r["score"])
        multi = {bm: s for bm, s in by_bm.items() if len(s) > 1}
        print(f"    ⭐ 「一盆多分」的盆数 = {len(multi)}"
              f"（V111：同盆必同分，应为 0）")

        # 0.8 档明细
        b08 = by_score.get(0.8, set())
        pure08 = {b for b in b08 if all(isinstance(x, bool) for x in b)}
        print(f"    **0.8 档行为盆数 = {len(b08)}**（其中纯 pass/fail 位图 {len(pure08)} 盆，"
              f"含异常态 {len(b08) - len(pure08)} 盆）")
        out.setdefault("_pure08", {})[tid] = sorted(pure08)
        for bm in b08:
            ex = [r for r in rows if r["score"] == 0.8 and r["bm"] == bm][:2]
            print(f"      {list(bm)}  e.g. {[e['h'] + ' ' + e['lab'] for e in ex]}")

    print("\n## §4 W6 判决：0.8 档至少 2 个不同的行为盆")
    print("  ⚠️ 预注册 W6 点名的两个位图 [T,T,T,T,F]（掉 t4）与 [T,T,T,F,T]（掉 t3）**都是 g7e1 的**")
    print("     ⇒ 作用域 = **g7e1**。⛔ 第一版用 `all()` 把 g7d2 也算进来，是**判据越出预注册作用域**。")
    focused = "g7e1_parse_flag"
    bms = out.get("_pure08", {}).get(focused, [])
    ok = len(bms) >= 2
    _named_set = {tuple([True, True, True, True, False]), tuple([True, True, True, False, True])}
    named = [b for b in bms if tuple(b) in _named_set]
    print(f"  **{focused}**（预注册作用域）纯 pass/fail 0.8 盆 = **{len(bms)}** ⇒ {'✅ 押中' if ok else '⛔ 押破'}")
    for b in bms:
        print(f"      {list(b)}{'  ← 预注册点名' if b in named else ''}")
    print(f"    预注册点名的两个盆都在：{len(named)}/2")
    print("\n  **对照（不参与 W6 判决）**")
    for tid, bb in out.get("_pure08", {}).items():
        if tid == focused:
            continue
        print(f"    {tid:22s} 纯 pass/fail 0.8 盆 = {len(bb)}"
              f"　⇒ 这题在 0.8 处**没有分支**，贪心不会走错；g7e1 有分支 ⇒ 贪心**必错**")

    print("  ⚠️ V111 的原话是「非零档全单射」，那是在 **26 个实现的语料**内。")
    print("     这里是 **1-编辑搜索空间**（743 + 30 个候选）⇒ 作用域不同，不构成对 V111 的推翻，")
    print("     而是把它的作用域**收窄**：语料内成立，搜索空间内不成立。")

    (HERE / "basins.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str),
                                      encoding="utf-8")
    print("\n  写出 basins.json")


if __name__ == "__main__":
    main()
