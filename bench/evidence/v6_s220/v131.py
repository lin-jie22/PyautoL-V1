#!/usr/bin/env python3
"""V131：让系统自己检查「我写下的键有没有可能被匹配到」（自省，不用分数不用人）。

做法：monkeypatch `v121.gen`，在冷启动搜索**生成候选的那一刻**顺手记下抽象动作，
得到「该实现可达的键集合」。写入前用这个集合自检：键不在集合里 ⇒ 判死 ⇒ 不写。

三臂唯一变量 = 自检用哪一层的候选：
  SELF  ：搜索过程中全部生成过的候选（深度 1~6）
  NAIVE ：只算从基线代码生成的那一批（深度 1）
  INV   ：判据反过来（判死可匹配的）

⛔ 不改搜索器一字（monkeypatch 只旁观）；⛔ 自检不读分数、不读 test。
零模型、零网络、零子进程。
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
for d in ("v6_s205", "v6_s208", "v6_s209", "v6_s210", "v6_s211", "v6_s198", "v6_s217"):
    sys.path.insert(0, str(EV / d))
import search as S            # noqa: E402
import idiom_transfer as I     # noqa: E402
import v119 as V              # noqa: E402
import v120 as W              # noqa: E402
import v121 as M              # noqa: E402
import v128 as R              # noqa: E402

TIDS = V.TIDS
N_SHUFF = 30
BASE_KEY = ("?", "<baseline>")

REACH: dict = {}          # (tid,h) -> {"all": set, "d1": set}
CUR: dict = {}            # tid -> (tid,h)  ← 冷启动是串行跑的，够用
BASE: dict = {}           # (tid,h) -> 基线代码

_ORIG_GEN = M.gen


def _gen_rec(tid, code, Sx, chains, exprs):
    """旁观者：生成照常，只是把抽象动作记进 REACH。"""
    out = _ORIG_GEN(tid, code, Sx, chains, exprs)
    k = CUR.get(tid)
    if k is not None:
        d = REACH.setdefault(k, {"all": set(), "d1": set()})
        absd = {M.abstract(lab) for lab, _ in out}
        d["all"] |= absd
        if code == BASE[k]:
            d["d1"] |= absd
    return out


def reach_of(tid, h, mode):
    d = REACH.get((tid, h))
    if d is None:
        return set()
    return d["all"] if mode == "self" else d["d1"]


def main() -> None:
    corpus = I.harvest()
    impls = []
    for tid in TIDS:
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

    # ---------------- 冷启动：生成记忆 + 同时记录可达集合
    M.gen = _gen_rec
    print("## §0 冷启动（同时旁观候选生成，不额外判分）")
    cold, wins = {}, {t: [] for t in TIDS}
    for tid, h in impls:
        CUR[tid] = (tid, h)
        BASE[(tid, h)] = corpus[tid][h]
        c = pre[(tid, h)]
        V.CACHE.clear()
        r = M.search(tid, corpus[tid][h], c["Sx"], c["chains"], c["exprs"], lambda x: 0)
        cold[(tid, h)] = r
        if r["hit"]:
            wins[tid].append(dict(tid=tid, h=h, act=list(M.abstract(r["lab"])), label=r["lab"]))
    M.gen = _ORIG_GEN                      # ⛔ 立刻还原：后面的 sweep 不许污染 REACH
    base = sum(r["n_eval"] for r in cold.values())
    allr = [w for t in TIDS for w in wins[t]]
    print(f"  n_impl = {len(impls)}｜判分基数 = {base}｜到 1.0 = "
          f"{sum(1 for r in cold.values() if r['s1'] >= 1.0)}/{len(impls)}｜写下的条目 = {len(allr)}")

    print("\n## §0b 可达集合的规模（自检的输入，不含任何分数）")
    n_empty = sum(1 for k in impls if not REACH.get(k, {}).get("all"))
    print(f"  reach(all) 为空的实现 = {n_empty}（预期 = 基线已满分的那些，它们不生成候选）")
    for tid, h in impls[:6]:
        d = REACH.get((tid, h), {"all": set(), "d1": set()})
        print(f"  {tid[:6]} {h} |all|={len(d['all']):3d} |d1|={len(d['d1']):3d}")

    # ---------------- 三臂：只用 REACH 决定写不写
    keep = {
        "SELF": [w for w in allr if tuple(w["act"]) in reach_of(w["tid"], w["h"], "self")],
        "NAIVE": [w for w in allr if tuple(w["act"]) in reach_of(w["tid"], w["h"], "d1")],
        "INV": [w for w in allr if tuple(w["act"]) not in reach_of(w["tid"], w["h"], "self")],
    }
    killed = {n: [w for w in allr if w not in keep[n]] for n in keep}
    print("\n## §0c 自检判死了什么（这是本轮的判决对象）")
    for n in ("SELF", "NAIVE", "INV"):
        ks = [tuple(w["act"]) for w in killed[n]]
        print(f"  {n:6s} 判死 {len(killed[n]):2d}｜判活 {len(keep[n]):2d}｜"
              f"判死的键 {sorted({str(k) for k in ks})[:3]}{' …' if len(set(ks)) > 3 else ''}")

    def mk(records):
        mem = {t: [] for t in TIDS}
        for r in records:
            mem[r["tid"]].append(dict(h=r["h"], act=r["act"], label=r["label"]))
        return mem

    def run(records, tag):
        arm = R.sweep(impls, corpus, pre, mk(records))
        saved = sum(cold[k]["n_eval"] - arm[k]["n_eval"] for k in impls)
        to1 = sum(1 for k in arm if arm[k]["s1"] >= 1.0)
        print(f"  {tag:8s} 条目 {len(records):2d}｜saved {saved:4d}（{saved/base*100:5.1f}%）"
              f"｜到 1.0 {to1}/{len(impls)}")
        return dict(saved=saved, n=len(records), to1=to1,
                    per={f"{k[0]}::{k[1]}": v["n_eval"] for k, v in arm.items()})

    print("\n## §1 三臂（接上写端）")
    a_all = run(allr, "ALL")
    a_self = run(keep["SELF"], "SELF")
    a_naive = run(keep["NAIVE"], "NAIVE")
    a_inv = run(keep["INV"], "INV")

    print(f"\n## §2 零假设（随机判死 10 条，{N_SHUFF} 次，seed=0；哨兵，不计入发现）")
    rng = random.Random(0)
    sh = []
    for _ in range(N_SHUFF):
        arm = R.sweep(impls, corpus, pre, mk(rng.sample(allr, len(keep["SELF"]))))
        sh.append(sum(cold[k]["n_eval"] - arm[k]["n_eval"] for k in impls))
    sh.sort()
    p95 = sh[int(0.95 * (len(sh) - 1))]

    # ---------------- 判决
    base_keys = {tuple(w["act"]) for w in allr if tuple(w["act"]) == BASE_KEY}
    killed_self = {tuple(w["act"]) for w in killed["SELF"]}
    print("\n## §3 判决")

    w1 = killed_self == base_keys and len(killed["SELF"]) == 10
    print(f"  **W1 SELF 判死集合 == 10 条 `('?','<baseline>')`** ⇒ "
          f"判死 {len(killed['SELF'])} 条，键种 {len(killed_self)} ⇒ {'✅ 押中' if w1 else '⛔ 押破'}")
    if not w1:
        print(f"      差集：自检多杀 {killed_self - base_keys}｜漏杀 {base_keys - killed_self}")

    nb_keys = {(w["tid"], tuple(w["act"])) for w in allr if tuple(w["act"]) != BASE_KEY}
    self_keys = {(w["tid"], tuple(w["act"])) for w in keep["SELF"]}
    w2 = self_keys == nb_keys and len(keep["SELF"]) == 11
    print(f"  **W2 SELF 判活的 11 条 == V130 人写的 ALL_NB** ⇒ "
          f"自检 {len(self_keys)} 种 / 人写 {len(nb_keys)} 种｜差集 {len(self_keys ^ nb_keys)}"
          f" ⇒ {'✅ 押中' if w2 else '⛔ 押破'}")

    diffs = [k for k in a_all["per"] if a_all["per"][k] != a_self["per"][k]]
    w3 = a_self["saved"] == a_all["saved"] and not diffs
    print(f"  **W3 SELF saved {a_self['saved']} == ALL {a_all['saved']} 且逐实现差异 0** ⇒ "
          f"差异 {len(diffs)} 个 ⇒ {'✅ 押中' if w3 else '⛔ 押破'}")

    w4 = len(killed["NAIVE"]) > 10 and a_naive["saved"] < a_all["saved"]
    print(f"  **W4 NAIVE 判死 {len(killed['NAIVE'])} > 10 且 saved {a_naive['saved']} < {a_all['saved']}** ⇒ "
          f"{'✅ 押中' if w4 else '⛔ 押破'}"
          f"（误杀的键：{sorted({str(tuple(w['act'])) for w in killed['NAIVE'] if tuple(w['act']) != BASE_KEY})}）")

    w5 = a_inv["saved"] < a_self["saved"] and len(keep["INV"]) < len(keep["SELF"])
    print(f"  **W5 INV saved {a_inv['saved']} < SELF {a_self['saved']} 且判活 {len(keep['INV'])} < {len(keep['SELF'])}**"
          f" ⇒ {'✅ 押中' if w5 else '⛔ 押破'}")

    w6 = a_self["saved"] >= p95
    print(f"  （哨兵）SELF {a_self['saved']} ≥ 随机判死 10 条 p95 {p95}"
          f"（均值 {statistics.mean(sh):.0f}｜max {sh[-1]}）⇒ {'✅' if w6 else '⛔'}")

    print("\n## §4 边界（跑前已写死，⛔ 不许扩口径）")
    live_on = [w for w in keep["SELF"] if tuple(w["act"]) == ("T2", "'on'")]
    print(f"  自检判活、但 V128 实测边际为 0 的条目：{len(live_on)} 条 `('T2',\"'on'\")` "
          f"⇒ ⛔ 自检**抓不到冗余**，它只回答「有没有可能被匹配到」")
    print(f"  自检也判活了 `('?','<baseline>')` 以外的全部键，其中 4 条 `'on'` 是纯冗余 "
          f"⇒ 「可匹配」≠「有用」（V128 已证边际 0 且可匹配的情形存在）")

    print("\n## §5 汇总")
    print(f"  W1 {'✅' if w1 else '⛔'}　W2 {'✅' if w2 else '⛔'}　W3 {'✅' if w3 else '⛔'}　"
          f"W4 {'✅' if w4 else '⛔'}　W5 {'✅' if w5 else '⛔'}")
    print(f"  人写规则 → 机器自检：条目 {len(allr)} → {len(keep['SELF'])}（−{(1-len(keep['SELF'])/len(allr))*100:.0f}%）"
          f"｜saved {a_all['saved']} → {a_self['saved']}｜额外判分 0 次")

    (HERE / "selfcheck.json").write_text(json.dumps(dict(
        base=base, ALL=a_all, SELF=a_self, NAIVE=a_naive, INV=a_inv,
        n_killed={n: len(killed[n]) for n in killed},
        killed_self=sorted(str(tuple(w["act"])) for w in killed["SELF"]),
        killed_naive=sorted(str(tuple(w["act"])) for w in killed["NAIVE"]),
        n_empty_reach=n_empty, n_total=len(impls),
        shuf=dict(mean=statistics.mean(sh), p50=sh[len(sh) // 2], p95=p95,
                  mn=sh[0], mx=sh[-1], n=N_SHUFF),
        all_records=allr, keep_self=keep["SELF"], keep_naive=keep["NAIVE"],
        reach={f"{k[0]}::{k[1]}": dict(n_all=len(REACH.get(k, {}).get("all", set())),
                                       n_d1=len(REACH.get(k, {}).get("d1", set()))) for k in impls},
    ), ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  原始读数已落盘 `selfcheck.json`")


if __name__ == "__main__":
    main()
