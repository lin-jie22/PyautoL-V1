#!/usr/bin/env python3
"""V157：元信息能不能变成「改」—— 五个策略在同一事件流上的回放。

G1 见到任一 1.0 就停整个实现（免费）｜G2 每键见 1.0 就停该键（免费）
G3(m) 每键预算 ceil(m*price)（price = V156 的 P2，1 次判分）｜G4 预言家（跳过永不中的格）
G5 随机跳过同数量键（200 种子）

⛔ 0 次新判分（重放 A_DEPLOY）；⛔ 不改 gen/abstract/记忆；⛔ 不碰 V79。
"""
from __future__ import annotations

import ast
import collections
import json
import math
import pathlib
import random
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for _d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198", "v6_s210", "v6_s236"):
    sys.path.insert(0, str(EV / _d))

import idiom_transfer as I     # noqa: E402
import search as S             # noqa: E402
import v119 as V               # noqa: E402
import v120 as W               # noqa: E402
import v121 as X               # noqa: E402

BASELINE = ("?", "<baseline>")
MULTS = (0.5, 1.0, 1.66, 2.0)
SEEDS = 200


def search(tid, h, code, Sx, chains, exprs, mine, rounds, k) -> dict:
    s0, _ = V.ev(tid, code)
    frontier = [dict(code=code, s=s0)]
    visited = {code}
    n_eval = 0
    events: list[tuple] = []
    depth = 0
    for rnd in range(1, rounds + 1):
        pool, seen_src = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, src)
                     for lab, src in X.gen(tid, node["code"], Sx, chains, exprs)]
            for key, _lab, src in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                if src in visited or src in seen_src:
                    continue
                s, _ = V.ev(tid, src)
                n_eval += 1
                events.append((key, s, src, rnd))
                if s > node["s"]:
                    pool.append(dict(code=src, s=s))
                    seen_src.add(src)
        if not pool:
            break
        depth = rnd
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:k]
        visited |= {d["code"] for d in pool}
    best = max([s0] + [e[1] for e in events]) if events else s0
    return dict(s0=s0, s1=best, n_eval=n_eval, depth=depth, events=events)


def run_arm(impls, corpus, mem, rounds, k):
    out = {}
    for tid, h in impls:
        mine = {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        ch = V.harvest_chains(corpus, tid, h)
        ex = W.harvest_exprs(corpus, tid, h)
        out[(tid, h)] = search(tid, h, corpus[tid][h], Sx, ch, ex, mine, rounds, k)
    return out


def per_key(events):
    d = collections.OrderedDict()
    for key, s, _src, _r in events:
        d.setdefault(key, []).append(s)
    return d


def reached(scores):
    return max(scores) >= 1.0 if scores else False


def main() -> None:
    t0 = time.time()
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

    V.CACHE.clear()
    A = run_arm(impls, corpus, mem, rounds=6, k=2)
    tot = sum(r["n_eval"] for r in A.values())
    n_ok = sum(1 for r in A.values() if r["s1"] >= 1.0)

    # 每格：p_a 与 LOIO 价格（邻居的 1/p）
    cells = {}
    for (tid, h), r in A.items():
        for key, seq in per_key(r["events"]).items():
            cells[(tid, h, key)] = dict(seq=seq, p=sum(1 for s in seq if s >= 1.0) / len(seq),
                                        n=len(seq), pos=next((i for i, s in enumerate(seq, 1)
                                                              if s >= 1.0), None))
    price = {}
    for (tid, h, key), c in cells.items():
        tr = [((t2, h2, k2), c2) for (t2, h2, k2), c2 in cells.items() if t2 == tid and h2 != h]
        if not tr:
            price[(tid, h, key)] = None
            continue
        s0 = A[(tid, h)]["s0"]
        nn = min(tr, key=lambda x: (abs(A[(tid, x[0][1])]["s0"] - s0), x[0][1]))
        p = nn[1]["p"]
        price[(tid, h, key)] = (1.0 / p) if p > 0 else None

    hit_cells = sum(1 for c in cells.values() if c["pos"])
    oracle_skip = [k for k, c in cells.items() if c["pos"] is None]
    save_oracle = sum(cells[k]["n"] for k in oracle_skip)
    # 35 个真实记忆格（V156 的分母）—— 与全搜索分开
    uni_all = set()
    for tid, h in impls:
        for kk in mem[tid]:
            if kk["h"] != h:
                uni_all.add((tid, h, tuple(kk["act"])))
    real = {c for c in uni_all if c[2] != BASELINE}
    tot_real = sum(1 for im in A for e in A[im]["events"] if (im[0], im[1], e[0]) in real)
    real_hit = sum(1 for k, c in cells.items() if k in real and c["pos"] is not None)
    real_oracle = [k for k, c in cells.items() if k in real and c["pos"] is None]
    real_save = sum(cells[k]["n"] for k in real_oracle)

    print("## §0 自校验（两个口径分开报，⛔ 不成立则全部作废）")
    print(f"  全搜索：总判分 {tot}｜到 1.0 {n_ok}/25｜(实现,键) 对 {len(cells)}"
          f"｜永不中的对 {len(oracle_skip)} 省 {save_oracle}")
    print(f"  记忆格：总判分 {tot_real}（记 110）｜命中格 {real_hit}/35（记 26）"
          f"｜永不中的 {len(real_oracle)} 格省 {real_save}（记 9）")
    w1 = (tot_real == 110 and n_ok == 21 and real_hit == 26 and real_save == 9)

    # ------------------------------------------------ 策略
    def apply(impl, policy, m=1.0, scope=None):
        """返回该实现实际花掉的判分与最好分（回放）。scope=None ⇒ 全搜索。"""
        ev = A[impl]["events"]
        if scope is not None:
            ev = [e for e in ev if (impl[0], impl[1], e[0]) in scope]
        used, best = 0, A[impl]["s0"]
        if policy == "BASE":
            for _k, s, _src, _r in ev:
                used += 1
                best = max(best, s)
        elif policy == "G1":
            for _k, s, _src, _r in ev:
                used += 1
                best = max(best, s)
                if s >= 1.0:
                    break
        elif policy == "G2":
            seen_hit = set()
            for k, s, _src, _r in ev:
                if k in seen_hit:
                    continue
                used += 1
                best = max(best, s)
                if s >= 1.0:
                    seen_hit.add(k)
        elif policy == "G3":
            cnt = collections.Counter()
            for k, s, _src, _r in ev:
                p = price.get((impl[0], impl[1], k))
                budget = 10**9 if p is None else max(1, math.ceil(m * p))
                if cnt[k] >= budget:
                    continue
                cnt[k] += 1
                used += 1
                best = max(best, s)
        elif policy == "G4":
            for k, s, _src, _r in ev:
                if (impl[0], impl[1], k) in oracle_skip:
                    continue
                used += 1
                best = max(best, s)
        elif policy == "G5":
            skip = policy_skip
            for k, s, _src, _r in ev:
                if (impl[0], impl[1], k) in skip:
                    continue
                used += 1
                best = max(best, s)
        return used, best

    def run(policy, m=1.0, scope=None):
        used = ok = 0
        for impl in A:
            u, b = apply(impl, policy, m, scope)
            used += u
            ok += b >= 1.0
        return used, ok

    # 35 个真实记忆格（V156 的分母）—— 与全搜索分开报
    uni_all = set()
    for tid, h in impls:
        for kk in mem[tid]:
            if kk["h"] != h:
                uni_all.add((tid, h, tuple(kk["act"])))
    real = {c for c in uni_all if c[2] != BASELINE}
    tot_real = sum(len([1 for e in A[im]["events"] if (im[0], im[1], e[0]) in real])
                   for im in A)

    print("\n## §0b 77.5% 的构成：赢了之后还在判，还是在必输的实现上判完")
    win_impl, lose_impl = [], []
    for im in A:
        ev = A[im]["events"]
        pos = next((i for i, e in enumerate(ev, 1) if e[1] >= 1.0), None)
        (win_impl if pos else lose_impl).append((im, len(ev), pos))
    post = sum(n - p for _im, n, p in win_impl)
    allw = sum(n for _im, n, _p in win_impl)
    alll = sum(n for _im, n, _p in lose_impl)
    print(f"  赢家 {len(win_impl)} 个：判分 {allw}｜其中**首个 1.0 之后** = {post}"
          f"（{post / tot * 100:.1f}% of 全搜索）")
    print(f"  输家 {len(lose_impl)} 个：判分 {alll}（{alll / tot * 100:.1f}%）—— 完美预言家也只能省这部分")
    print(f"  ⭐ 两个来源分得很开：停手省 {post}｜认输省 {alll}｜合计 {post + alll} = {(post + alll) / tot * 100:.1f}%")
    for im, n, p in sorted(win_impl, key=lambda x: -(x[1] - x[2]))[:6]:
        print(f"    {im[0][:10]}::{im[1]} 判 {n} 次、第 {p} 次到 1.0 ⇒ 之后白判 {n - p}")

    print("\n## §1 五个策略 × 两个口径（⛔ 分母不同不许比大小，纪律 46）")
    for scope_name, scope, tot_s in (("全搜索（决策相关）", None, tot),
                                     ("35 个记忆格（V156 口径）", real, tot_real)):
        print(f"\n  ### {scope_name}：基线判分 {tot_s}")
        print("  | 策略 | 判分 | 省 | 省% | 到 1.0 | 掉 |")
        print("  |---|---|---|---|---|---|")
        tab = {}
        for tag, pol in (("BASE 现状", "BASE"), ("G1 见 1.0 停整个实现", "G1"),
                         ("G2 每键见 1.0 停该键", "G2"), ("G4 预言家（跳过永不中的格）", "G4")):
            u, ok = run(pol, scope=scope)
            tab[tag] = (u, ok)
            print(f"  | {tag} | {u} | {tot_s - u} | {(tot_s - u) / tot_s * 100:.1f}% | {ok}/25 | {21 - ok} |")
        for m in MULTS:
            u, ok = run("G3", m, scope)
            tab[f"G3({m})"] = (u, ok)
            print(f"  | G3(m={m}) 定价预算 | {u} | {tot_s - u} | {(tot_s - u) / tot_s * 100:.1f}% | {ok}/25 | {21 - ok} |")
        if scope is None:
            tab_all, tot_all_g = tab, tot_s
        else:
            tab_real, tot_real_g = tab, tot_s

    # G5 随机对照：随机跳过与预言家同数量的**键**（全搜索口径）
    print("\n## §2 G5 随机对照（全搜索；随机跳过与预言家同数量的键，200 种子）")
    keys_all = sorted({(im[0], im[1], k) for im in A for k in per_key(A[im]["events"])})
    n_oracle_keys = len({k for k in keys_all if cells.get(k, {}).get("pos") is None})
    saves, oks = [], []
    for seed in range(SEEDS):
        rnd = random.Random(seed)
        policy_skip = set(rnd.sample(keys_all, n_oracle_keys))
        u, ok = run("G5")
        saves.append(tot - u)
        oks.append(ok)
    saves.sort()
    print(f"  跳过键数 = {n_oracle_keys}｜省：mean {sum(saves) / len(saves):.1f}"
          f"｜median {saves[len(saves) // 2]}｜p5 {saves[int(0.05 * SEEDS)]}"
          f"｜p95 {saves[int(0.95 * SEEDS) - 1]}｜max {saves[-1]}")
    print(f"  到 1.0：mean {sum(oks) / len(oks):.2f}｜min {min(oks)}｜max {max(oks)}"
          f"｜掉 ≥1 个的种子 = {sum(1 for x in oks if x < 21)}/{SEEDS}")

    # ------------------------------------------------ 判据
    print("\n## §3 判据（预注册 §2；⚠️ 预注册的结构数来自 35 记忆格口径）")
    def get(tag, m=None):
        k = tag if m is None else f"{tag}({m})"
        return tab_all[k]
    g1_u, g1_ok = get("G1 见 1.0 停整个实现")
    g2_u, g2_ok = get("G2 每键见 1.0 停该键")
    g4_u, g4_ok = get("G4 预言家（跳过永不中的格）")
    s1, s2, s4 = tot_all_g - g1_u, tot_all_g - g2_u, tot_all_g - g4_u
    r1 = tot_real_g - tab_real["G1 见 1.0 停整个实现"][0]
    r2 = tot_real_g - tab_real["G2 每键见 1.0 停该键"][0]
    r4 = tot_real_g - tab_real["G4 预言家（跳过永不中的格）"][0]
    r3_1 = tot_real_g - tab_real["G3(1.0)"][0]

    def row(tag, ok, txt):
        print(f"  {tag} {'✅' if ok else '⛔'} {txt}")

    row("W1", w1, f"自校验（记忆格口径）总 {tot_real_g}/命中 {real_hit}/预言家 {real_save}"
        f"｜全搜索 {tot_all_g}/到 1.0 {n_ok}｜(实现,键) 对 {len(cells)}")
    row("W2", s1 > s2, f"全搜索：G1 省 {s1} > G2 省 {s2}｜记忆格：{r1} vs {r2}")
    print(f"  W3 {'✅' if 0.3 * tot_all_g <= s1 <= 0.6 * tot_all_g else '⛔'} "
          f"G1 省 {s1}/{tot_all_g} = {s1 / tot_all_g * 100:.1f}%（押 30~60% 桶）")
    row("W4", not (tot_all_g - get("G3", 1.0)[0] > s2),
        f"G3(1.0) 省 {tot_all_g - get('G3', 1.0)[0]} vs G2 省 {s2}（押 G3 不更省）")
    row("W5", get("G3", 0.5)[1] < 21, f"G3(0.5) 到 1.0 {get('G3', 0.5)[1]}/25（押掉 ≥1）")
    row("W6", sum(1 for x in oks if x < 21) > 0,
        f"G5 掉实现的种子 {sum(1 for x in oks if x < 21)}/{SEEDS}（押 >0）")
    print(f"  W7 {'✅' if r2 > r4 else '⛔'} 记忆格口径：G2 省 {r2} > 预言家 {r4}（押 ✅）"
          f"｜全搜索口径：G2 {s2} vs 预言家 {s4} ⇒ {'✅' if s2 > s4 else '⛔ 反向'}")
    print(f"\n  ⭐ G3(1.0) 在记忆格口径省 {r3_1}/{tot_real_g}"
          f"（{(r3_1) / tot_real_g * 100 if tot_real_g else 0:.1f}%），全搜索口径见上表")
    print("  ⭐ 口径提醒：G1/G2 的节省是**精确值**（停在 1.0 之后的事件确实不会发生）；")
    print("     G3 的节省是**上界**（回放假设事件与顺序无关，真实 frontier 依赖顺序）。")

    out = dict(w1=w1, tot=tot_all_g, tot_real=tot_real_g, n_ok=n_ok, hit_cells=hit_cells,
               save_oracle=save_oracle, oracle_keys=n_oracle_keys,
               tab_all={k: dict(used=v[0], saved=tot_all_g - v[0], ok=v[1]) for k, v in tab_all.items()},
               tab_real={k: dict(used=v[0], saved=tot_real_g - v[0], ok=v[1]) for k, v in tab_real.items()},
               g5=dict(saves=saves, oks=oks, n_seed=SEEDS),
               cells=[dict(cell=list(k), n=v["n"], pos=v["pos"], p=v["p"], price=price[k])
                      for k, v in cells.items()],
               wall_s=time.time() - t0)
    (HERE / "readings_v157.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                             encoding="utf-8")
    print(f"\n  已落盘 readings_v157.json（{out['wall_s']:.1f}s）")


if __name__ == "__main__":
    main()
