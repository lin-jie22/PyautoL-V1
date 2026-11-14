#!/usr/bin/env python3
"""V125：`mem_hit` 在排序键里的位置与符号 —— 记忆分量到底有没有信息。

六个键（见 PREREG）唯一变量 = 记忆分量出现在四元键的哪个位置、取什么符号。
同搜索器 / 同动作空间 / 同 k=2 / 同记忆（留一法）/ 同 G / 同 25 个可解析实现。
零模型、零网络、零子进程。判据 = 现有 test 同一批。
"""
from __future__ import annotations

import ast
import json
import pathlib
import random
import statistics
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for d in ("v6_s205", "v6_s208", "v6_s209", "v6_s210", "v6_s211", "v6_s198"):
    sys.path.insert(0, str(EV / d))
import search as S            # noqa: E402
import idiom_transfer as I     # noqa: E402
import v119 as V              # noqa: E402
import v120 as W              # noqa: E402
import v121 as M              # noqa: E402
import v122 as B              # noqa: E402

TIDS = V.TIDS
MEM = EV / "v6_s210" / "memory.json"
GS = [150, 300, 450]
SEEDS = list(range(20))
ev = V.ev

# 键定义：true = 命中优先（0 在前），false = 反向（命中排后面）
def neg(hit, prefer_hit):
    return (0 if hit else 1) if prefer_hit else (1 if hit else 0)


# ⛔ 键函数收 (ctx, tail) 两个参数：第一版把实现哈希塞进 kwargs
# （`dict(ctx[k], **{1: k[1]})`）⇒ `TypeError: keywords must be strings`。
KEYS = {
    "K0_COST":     lambda c, t: (c["est_cost"], c["s0"], t),
    "K1_MEM_FIRST": lambda c, t: (neg(c["mem_hit"], True), c["est_cost"], c["s0"], t),
    "K2_MEM_TIE":  lambda c, t: (c["est_cost"], neg(c["mem_hit"], True), c["s0"], t),
    "K3_MEM_LAST": lambda c, t: (c["est_cost"], c["s0"], neg(c["mem_hit"], True), t),
    "K4_MEM_REV":  lambda c, t: (c["est_cost"], neg(c["mem_hit"], False), c["s0"], t),
    "K5_MEM_ONLY": lambda c, t: (neg(c["mem_hit"], True), c["s0"], t),
}
ORDER = list(KEYS)


def main() -> None:
    corpus = I.harvest()
    mem = json.loads(MEM.read_text(encoding="utf-8"))
    ctx = {}
    for tid in TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                continue
            Sx, _ = S.wordset(tid, corpus[tid][h], True)
            chains = V.harvest_chains(corpus, tid, h)
            exprs = W.harvest_exprs(corpus, tid, h)
            base = corpus[tid][h]
            s0, _ = ev(tid, base)
            cands = M.gen(tid, base, Sx, chains, exprs)
            mine = {(a, b) for k in mem[tid] if k["h"] != h for a, b in [tuple(k["act"])]}
            hit = any(M.abstract(lab) in mine for lab, _ in cands)
            ctx[(tid, h)] = dict(Sx=Sx, chains=chains, exprs=exprs, s0=s0,
                                 est_cost=len(cands), mem_hit=hit, mine=mine)
    impls = list(ctx)

    print("## §0 前置读数（不参与判决）：mem_hit 的分布")
    ht = [k for k in impls if ctx[k]["mem_hit"]]
    nt = [k for k in impls if not ctx[k]["mem_hit"]]
    print(f"  n_impl = {len(impls)}｜mem_hit=True {len(ht)}｜False {len(nt)}"
          f"｜比例 {len(ht)}/{len(impls)}")
    for name, grp in (("True", ht), ("False", nt)):
        if grp:
            print(f"  mem_hit={name:5s} s0 均值 {statistics.mean(ctx[k]['s0'] for k in grp):.2f}"
                  f"｜est_cost 均值 {statistics.mean(ctx[k]['est_cost'] for k in grp):.1f}"
                  f"｜tid 分布 {sorted({k[0][:4] for k in grp})}")
    cnt: dict[int, int] = {}
    for k in impls:
        cnt[ctx[k]["est_cost"]] = cnt.get(ctx[k]["est_cost"], 0) + 1
    print(f"  est_cost 并列组（size>1）：{ {c: n for c, n in sorted(cnt.items()) if n > 1} }")

    def run(order, G):
        left, reached = G, 0
        for k in order:
            if left <= 0:
                break
            c = ctx[k]
            V.CACHE.clear()
            r = B.search(k[0], corpus[k[0]][k[1]], c["Sx"], c["chains"], c["exprs"],
                         lambda x, mine=c["mine"]: (0 if M.abstract(x[0]) in mine else 1),
                         left)
            left -= r["n_eval"]
            if r["s1"] >= 1.0:
                reached += 1
        return reached

    def order_of(name):
        return sorted(impls, key=lambda k: KEYS[name](ctx[k], k[1]))

    orders = {n: order_of(n) for n in ORDER}
    print("\n## §0b 各键前 5（看它先做谁）")
    for n in ORDER:
        print(f"  {n:12s} {[(k[0][:4], k[1][:6]) for k in orders[n][:5]]}")

    print("\n## §1 主读数（到 1.0 的实现数 / 25）")
    res = {}
    for G in GS:
        row = {n: run(orders[n], G) for n in ORDER}
        rr = [run(random.Random(seed).sample(impls, len(impls)), G) for seed in SEEDS]
        row["RANDOM"] = statistics.mean(rr)
        row["rmin"], row["rmax"] = min(rr), max(rr)
        res[G] = row
        print(f"  G={G:3d}　" + "｜".join(f"{n.replace('_', '')} {row[n]:2d}" for n in ORDER)
              + f"｜RANDOM {row['RANDOM']:5.2f}（{row['rmin']}~{row['rmax']}）")

    print("\n## §2 判决")
    base = {G: res[G]["K0_COST"] for G in GS}

    beats = {G: [n for n in ("K1_MEM_FIRST", "K2_MEM_TIE", "K3_MEM_LAST")
                 if res[G][n] > base[G]] for G in GS}
    w1 = all(not v for v in beats.values())
    print(f"  **W1 三个 G 上 K1/K2/K3 都没有严格超过 K0** ⇒ 超过的 = {beats} ⇒ "
          f"{'✅ 押中' if w1 else '⛔ 押破'}")

    w2 = all(res[G]["K4_MEM_REV"] >= res[G]["K1_MEM_FIRST"] for G in GS)
    print(f"  **W2 反向 K4 ≥ 正向 K1 在三个 G 上都成立** ⇒ "
          f"{ {G: (res[G]['K4_MEM_REV'], res[G]['K1_MEM_FIRST']) for G in GS} } ⇒ "
          f"{'✅ 押中' if w2 else '⛔ 押破'}")

    d2 = {G: res[G]["K2_MEM_TIE"] - res[G]["K0_COST"] for G in GS}
    w3 = all(abs(v) <= 1 for v in d2.values())
    print(f"  **W3 K2 与 K0 的差 ≤1** ⇒ {d2} ⇒ {'✅ 押中' if w3 else '⛔ 押破'}")

    w4 = all(res[G]["K5_MEM_ONLY"] <= min(res[G][n] for n in ORDER if n != "K5_MEM_ONLY")
             for G in GS)
    print(f"  **W4 K5（不用 cost）三个 G 都是最差** ⇒ "
          f"{ {G: res[G]['K5_MEM_ONLY'] for G in GS} } ⇒ {'✅ 押中' if w4 else '⛔ 押破'}")

    w5 = all(all(a <= b for a, b in zip([res[g][n] for g in GS],
                                        [res[g][n] for g in GS][1:])) for n in ORDER)
    print(f"  **W5 六键都随 G 单调不减** ⇒ {'✅ 押中' if w5 else '⛔ 押破'}")

    print(f"\n## §3 汇总")
    print(f"  W1 {'✅' if w1 else '⛔'}　W2 {'✅' if w2 else '⛔'}　W3 {'✅' if w3 else '⛔'}"
          f"　W4 {'✅' if w4 else '⛔'}　W5 {'✅' if w5 else '⛔'}")
    print(f"  最优键（G=150）：{max(ORDER, key=lambda n: res[150][n])}"
          f" = {max(res[150][n] for n in ORDER)}")

    (HERE / "keys.json").write_text(json.dumps(
        dict(res={str(k): v for k, v in res.items()},
             mem_hit=dict(n_true=len(ht), n_false=len(nt),
                          true=[f"{k[0]}/{k[1]}" for k in ht],
                          false=[f"{k[0]}/{k[1]}" for k in nt])),
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  原始读数已落盘 `keys.json`")


if __name__ == "__main__":
    main()
