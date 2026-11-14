#!/usr/bin/env python3
"""V128 跑后诊断（⛔ 未押）：为什么 TOP4 只有 309，而随机 4 条能到 755。

假设：记忆的价值不在「它为自己省了多少」，而在**留一法下它能覆盖别的实现**。
检验：手工指定 5 个 4 条集合，看「跨题多样」是否比「单题最强」更值。
"""
from __future__ import annotations
import ast, json, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for d in ("v6_s205", "v6_s208", "v6_s209", "v6_s210", "v6_s211", "v6_s198"):
    sys.path.insert(0, str(EV / d))
import search as S            # noqa: E402
import idiom_transfer as I     # noqa: E402
import v119 as V              # noqa: E402
import v120 as W              # noqa: E402
import v121 as M              # noqa: E402
import v128 as R              # noqa: E402

corpus = I.harvest()
impls = []
for tid in V.TIDS:
    for h in sorted(corpus[tid]):
        try:
            ast.parse(corpus[tid][h])
        except SyntaxError:
            continue
        impls.append((tid, h))
pre = {}
for tid, h in impls:
    Sx, _ = S.wordset(tid, corpus[tid][h], True)
    pre[(tid, h)] = dict(Sx=Sx, chains=V.harvest_chains(corpus, tid, h),
                         exprs=W.harvest_exprs(corpus, tid, h))

cold, wins = {}, {t: [] for t in V.TIDS}
for tid, h in impls:
    c = pre[(tid, h)]
    V.CACHE.clear()
    r = M.search(tid, corpus[tid][h], c["Sx"], c["chains"], c["exprs"], lambda x: 0)
    cold[(tid, h)] = r
    if r["hit"]:
        wins[tid].append(dict(tid=tid, h=h, act=list(M.abstract(r["lab"])), label=r["lab"]))
base = sum(r["n_eval"] for r in cold.values())
allr = [w for t in V.TIDS for w in wins[t]]
print(f"## §0 基数 = {base}｜21 条的 tid 分布："
      f"{ {t: [w['h'][:6] for w in wins[t]] for t in V.TIDS} }")

# 去重后的抽象动作（这才是留一法下真正能匹配的东西）
print("\n## §1 抽象动作去重后的实际信息量")
for t in V.TIDS:
    uniq = sorted({tuple(w["act"]) for w in wins[t]})
    print(f"  {t:18s} 条目 {len(wins[t]):2d}｜去重后 {len(uniq)}：{uniq}")
uniq_all = sorted({tuple(w["act"]) for w in allr})
print(f"  全库 条目 {len(allr)}｜去重后 {len(uniq_all)}")

def run(records, tag):
    mem = {t: [] for t in V.TIDS}
    for r in records:
        mem[r["tid"]].append(dict(h=r["h"], act=r["act"], label=r["label"]))
    arm = R.sweep(impls, corpus, pre, mem)
    saved = sum(cold[k]["n_eval"] - arm[k]["n_eval"] for k in impls)
    to1 = sum(1 for k in arm if arm[k]["s1"] >= 1.0)
    print(f"  {tag:26s} 条目 {len(records):2d}｜saved {saved:4d}（{saved/base*100:5.1f}%）｜到1.0 {to1}/25")
    return saved

print("\n## §2 手工集合（先验指定，不是从读数里挑的）")
def pick(tid, h):
    return [w for w in wins[tid] if w["h"].startswith(h)][0]

sets = {
    "TOP4(n_cand 最大)": [w for w in allr if w["h"] in R.TOP4],
    "每题各 1 条(4 题)": [pick("g7d2_dedupe_keep", "74aa48d8"), pick("g7d3_fmt_amount", "075b4099"),
                        pick("g7e1_parse_flag", "a579b07a"), pick("g7e2_truncate_tail", "8184ec4d")],
    "每题各 1 条(便宜优先)": [pick("g7d2_dedupe_keep", "74aa48d8"), pick("g7d3_fmt_amount", "075b4099"),
                          pick("g7e1_parse_flag", "f2d1efa6"), pick("g7e2_truncate_tail", "bc8c4b09")],
    "g7e1 全部 4 条+200ebf": [w for w in allr if w["tid"].startswith("g7e1") and w["h"] != "c1f49180"
                              and w["h"] != "644f0618" and w["h"] != "cd7e0eb9"],
    "ALL 21 条": allr,
}
res = {k: run(v, k) for k, v in sets.items()}

print("\n## §3 逐条边际贡献（从 ALL 里删掉这一条，看 saved 跌多少）")
marg = []
for w in allr:
    keep = [x for x in allr if x is not w]
    s = run(keep, f"ALL − {w['h'][:8]}")
    marg.append((w["h"], w["tid"][:6], tuple(w["act"]), s, 793 - s))
print("\n## §4 边际贡献排序")
for h, t, act, s, d in sorted(marg, key=lambda x: -x[4]):
    print(f"  {t} {h} {str(act):28s} 删后 saved={s:4d}｜边际 = {d:+4d}")
