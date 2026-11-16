#!/usr/bin/env python3
"""V146：知识的适用范围 —— 可匹配 ≠ 可用；系统能不能在搜索过程中自己发现它。

四个臂（同一搜索器、同一动作空间、同一 k=2、同一 25 实现）：
  WARM_ALL   ：记忆命中优先（V121 原样，基线 588）
  ONLINE     ：命中键被判分后「没有超过当前节点」满 2 次，就退回普通候选
  WARM_BAD   ：记忆 + 注入一条**完全无效**的键（g7d3 的 ('T3','strip().lower')）
  ONLINE_BAD ：同上，但开在线降权
  REV        ：反证 —— 失败越多越优先

⛔ 不删除条目、不读 test、不用人写规则。唯一的新信息是「系统自己判过的分」。
⛔ 不改 v121.py（那是证据）。
"""
from __future__ import annotations

import ast
import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for _d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198"):
    sys.path.insert(0, str(EV / _d))
sys.path.insert(0, str(EV / "v6_s210"))

import idiom_transfer as I     # noqa: E402
import search as S             # noqa: E402
import v119 as V               # noqa: E402
import v120 as W               # noqa: E402
import v121 as X               # noqa: E402

MAX_ROUNDS = X.MAX_ROUNDS
BAD_KEY = ("T3", "strip().lower")
BAD_TID = "g7d3_fmt_amount"


def search_arm(tid, h, code, Sx, chains, exprs, mem, mode="warm", thresh=2):
    """与 `v121.search` 逐行同构；唯一差别是 order_key 的计算与 `fails` 的更新。"""
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, s1=s0, n_eval=0, lab="<baseline>", hit=True, hit_eval=0)
    frontier = [dict(code=code, s=s0)]
    visited = {code}
    n_eval = hit_eval = 0
    fails: collections.Counter = collections.Counter()
    tries: collections.Counter = collections.Counter()
    bscore: dict = {}

    for _ in range(MAX_ROUNDS):
        pool, seen_src = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, src)
                     for lab, src in X.gen(tid, node["code"], Sx, chains, exprs)]

            def order(item):
                k, _lab, _src = item
                hit = 0 if k in mem else 1
                if mode == "warm":
                    return (hit,)
                if mode == "online":
                    return (0 if fails[k] < thresh else 1, hit)
                if mode == "rev":
                    return (-fails[k] if k in mem else 0, hit)
                raise ValueError(mode)

            for k, lab, src in sorted(cands, key=order):
                if src in visited or src in seen_src:
                    continue
                s, _ = V.ev(tid, src)
                n_eval += 1
                tries[k] += 1
                if s > bscore.get(k, -1.0):
                    bscore[k] = s
                if k in mem:
                    hit_eval += 1
                if s >= 1.0:
                    return dict(s0=s0, s1=s, n_eval=n_eval, lab=lab, hit=True,
                                hit_eval=hit_eval, fails=dict(fails),
                                tries=dict(tries), bscore=dict(bscore))
                if s > node["s"]:
                    pool.append(dict(code=src, s=s))
                    seen_src.add(src)
                    fails[k] = 0
                else:
                    fails[k] += 1
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:2]
        visited |= {d["code"] for d in pool}
    best = max(frontier, key=lambda d: d["s"])
    return dict(s0=s0, s1=best["s"], n_eval=n_eval, lab="<none>", hit=False,
                hit_eval=hit_eval, fails=dict(fails),
                tries=dict(tries), bscore=dict(bscore))


def run_arm(impls, corpus, mem, mode, thresh=2):
    out = {}
    V.CACHE.clear()
    for tid, h in impls:
        mine = {(a, b) for k in mem[tid] if k["h"] != h for a, b in [tuple(k["act"])]}
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        chains = V.harvest_chains(corpus, tid, h)
        exprs = W.harvest_exprs(corpus, tid, h)
        out[(tid, h)] = search_arm(tid, h, corpus[tid][h], Sx, chains, exprs,
                                   mine, mode=mode, thresh=thresh)
    return out


def summarize(name, arm):
    tot = sum(v["n_eval"] for v in arm.values())
    to1 = sum(1 for v in arm.values() if v["s1"] >= 1.0)
    he = sum(v["hit_eval"] for v in arm.values())
    print(f"  {name:12s} n_eval = {tot:5d}｜命中记忆的判分 = {he:5d}｜到 1.0 = {to1}/25")
    return tot, to1, he


def main() -> None:
    corpus = I.harvest()
    impls = []
    for tid in V.TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                continue
            impls.append((tid, h))
    mem = json.loads(X.MEM_FILE.read_text(encoding="utf-8"))
    arm_globals: dict = {}

    print("## §1 W1 现象：可匹配面 > 可用面（来自 trace.json，零判分）")
    d = json.loads((HERE / "trace.json").read_text(encoding="utf-8"))
    seen: dict = collections.defaultdict(set)
    best: dict = collections.defaultdict(dict)
    for r in d["trace"]:
        k = tuple(r["key"])
        seen[(r["tid"], r["h"])].add(k)
        b = best[(r["tid"], r["h"])]
        if k not in b or r["score"] > b[k]:
            b[k] = r["score"]
    by_tid: dict = collections.defaultdict(list)
    for tid, h in seen:
        by_tid[tid].append(h)
    real = [(tid, it) for tid, items in mem.items() for it in items
            if tuple(it["act"]) != ("?", "<baseline>")]
    part = []
    for tid, it in real:
        k = tuple(it["act"])
        hs = sorted(by_tid[tid])
        sp = [h for h in hs if k in seen[(tid, h)]]
        us = [h for h in sp if best[(tid, h)].get(k, 0.0) >= 1.0]
        if sp and len(us) < len(sp):
            part.append((tid, k, len(sp), len(us)))
    print(f"  可匹配条目 = {len(real)}｜其中 spread > useful = {len(part)}")
    for tid, k, sp, us in sorted(part, key=lambda x: x[3] / x[2]):
        print(f"    {tid:22s} {str(k):26s} spread={sp} useful={us} ratio={us/sp:.2f}")
    w1 = len(part) >= 6
    print(f"  ⇒ W1 {'✅ 成立' if w1 else '⛔ 不成立'}（线 ≥6，实得 {len(part)}）")

    print("\n## §2 三臂读数（无注入）")
    warm = run_arm(impls, corpus, mem, "warm")
    online = run_arm(impls, corpus, mem, "online")
    rev = run_arm(impls, corpus, mem, "rev")
    t_warm, n_warm, he_warm = summarize("WARM_ALL", warm)
    t_on, n_on, he_on = summarize("ONLINE", online)
    t_rev, n_rev, _ = summarize("REV（反证）", rev)

    print(f"\n  W2 在线有效：{t_on} ≤ {t_warm} × 0.98 = {t_warm * 0.98:.1f}")
    w2 = t_on <= t_warm * 0.98
    print(f"     ⇒ {'✅ 成立' if w2 else '⛔ 不成立'}（省 {t_warm - t_on}，"
          f"{1 - t_on / t_warm:.2%}）")
    dmg = [(k, warm[k]["s1"], online[k]["s1"]) for k in warm if online[k]["s1"] < warm[k]["s1"]]
    print(f"  W3 不伤正确性：到 1.0 {n_on}/25 vs {n_warm}/25；逐实现 s1 降 {dmg}")
    w3 = (n_on == n_warm) and not dmg
    print(f"     ⇒ {'✅ 成立' if w3 else '⛔ 不成立'}")
    print(f"  W5 反证：REV n_eval = {t_rev} vs WARM_ALL {t_warm}")
    w5 = t_rev > t_warm
    print(f"     ⇒ {'✅ 判据有牙' if w5 else '⛔ 判据无牙（恒真）'}")

    print("\n## §3 注入完全无效的键（W4）")
    mem_bad = {t: list(v) for t, v in mem.items()}
    mem_bad[BAD_TID].append(dict(h="INJECTED", act=list(BAD_KEY), label="INJECTED"))
    wbad = run_arm(impls, corpus, mem_bad, "warm")
    obad = run_arm(impls, corpus, mem_bad, "online")
    t_bad, n_bad, _ = summarize("WARM_BAD", wbad)
    t_obad, n_obad, _ = summarize("ONLINE_BAD", obad)
    print(f"\n  W4a 扰动可见：{t_bad} > {t_warm}？")
    w4a = t_bad > t_warm
    print(f"     ⇒ {'✅ 成立' if w4a else '⛔ 注入无效（W4b 作废）'}（+{t_bad - t_warm}）")
    print(f"  W4b 在线吸收：{t_obad} ≤ {t_warm} + 1 = {t_warm + 1}？")
    w4b = t_obad <= t_warm + 1
    print(f"     ⇒ {'✅ 成立' if w4b else '⛔ 不成立'}（相对 WARM_BAD 省 {t_bad - t_obad}）")

    print("\n## §4 反证组 R1~R3")
    print(f"  R1 去掉 fails（应等于 WARM_ALL 588）：{t_warm}"
          f" ⇒ {'✅' if t_warm == 588 else '⛔ 装置漂移'}")
    online0 = run_arm(impls, corpus, mem, "online", thresh=0)
    t_on0 = sum(v["n_eval"] for v in online0.values())
    print(f"  R2 阈值 0（第一次失败就降权）：n_eval = {t_on0}"
          f"（阈值 2 时 {t_on}）⇒ {'更低' if t_on0 < t_on else '不更低'}")
    print(f"  R3 REV 应上升：{t_rev} vs {t_warm} ⇒ {'✅' if t_rev > t_warm else '⛔'}")

    print("\n## §5 逐实现对比（WARM_ALL vs ONLINE，只看有差异的）")
    diff = [(k, warm[k]["n_eval"], online[k]["n_eval"], warm[k]["hit_eval"],
             online[k]["hit_eval"]) for k in warm
            if warm[k]["n_eval"] != online[k]["n_eval"]]
    for k, a, b, ha, hb in sorted(diff, key=lambda x: -(x[1] - x[2]))[:12]:
        print(f"  {k[0][:6]} {k[1]}  n_eval {a:4d} → {b:4d}（{b - a:+d}）"
              f"｜命中记忆的判分 {ha:3d} → {hb:3d}")
    if not diff:
        print("  （无差异 —— 在线降权一次也没生效）")

    (HERE / "readings.json").write_text(json.dumps(dict(
        w1=dict(n_real=len(real), n_part=len(part), ok=w1),
        arms=dict(warm=t_warm, online=t_on, rev=t_rev, warm_bad=t_bad, online_bad=t_obad,
                  online_thresh0=t_on0),
        to1=dict(warm=n_warm, online=n_on, bad=n_bad, online_bad=n_obad),
        hit_eval=dict(warm=he_warm, online=he_on),
        w2=w2, w3=w3, w4a=w4a, w4b=w4b, w5=w5,
        dmg=[list(x) for x in dmg],
        diff=[[k[0], k[1], a, b] for k, a, b, _, _ in diff],
    ), ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  已落盘 `readings.json`")


if __name__ == "__main__":
    main()
