#!/usr/bin/env python3
"""V159：把杠杆从预测器挪到搜索自己的选择规则（`kept`）。

臂：K1/K2/K3/K4/K6（宽度）、DIV2/DIV3（每个分数值只留最好）、MEM2/MEM3（记忆过滤 × 宽度）、
以及等成本的 BUD 预算曲线。零新增判分（复用 V.ev）。
"""
import ast
import collections
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198", "v6_s210", "v6_s236"):
    sys.path.insert(0, str(EV / d))
import idiom_transfer as I          # noqa: E402
import search as S                  # noqa: E402
import v119 as V                    # noqa: E402
import v120 as W                    # noqa: E402
import v121 as X                    # noqa: E402

ROUNDS = 6
BUDS = [20, 40, 60, 100, 200]


def pick(pool, keep, mode):
    """选择规则：top = 取前 keep；div = 每个不同分数只留第一个，再截到 keep。"""
    if mode == "top":
        return pool[:keep]
    out, seen_s = [], set()
    for d in pool:
        if d["s"] in seen_s:
            continue
        seen_s.add(d["s"])
        out.append(d)
        if len(out) >= keep:
            break
    return out


def search_keep(tid, code, Sx, chains, exprs, mine, keep=2, mode="top",
                use_mem=False, budget=None, want_keys=False):
    """v121.search 的同构重放。⛔ 排序不动（记忆键优先）；只有截断处随 mode/keep 变。"""
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, s1=s0, n_eval=0, hit=True, rounds=0, pools=[], skip=0, keys=[])
    frontier = [dict(code=code, s=s0)]
    visited, n, skip, pools, keys = {code}, 0, 0, [], []
    best_s = s0
    for rnd in range(1, ROUNDS + 1):
        pool, seen = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, src) for lab, src in X.gen(tid, node["code"], Sx, chains, exprs)]
            for key, _lab, src in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                if src in visited or src in seen:
                    continue
                if use_mem and key not in mine:
                    skip += 1
                    continue
                if budget is not None and n >= budget:
                    return dict(s0=s0, s1=best_s, n_eval=n, hit=False, rounds=rnd,
                                pools=pools, skip=skip, keys=keys)
                s, _ = V.ev(tid, src)
                n += 1
                if s >= 1.0:
                    pools.append(len(pool))
                    return dict(s0=s0, s1=s, n_eval=n, hit=True, rounds=rnd,
                                pools=pools, skip=skip, keys=keys)
                if s > node["s"]:
                    pool.append(dict(code=src, s=s, key=key))
                    seen.add(src)
                    best_s = max(best_s, s)
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        pools.append(len(pool))
        if want_keys:
            keys.append([(d["key"], round(d["s"], 2)) for d in pool])
        frontier = pick(pool, keep, mode)
        visited |= {d["code"] for d in pool}
    best = max(frontier, key=lambda d: d["s"])
    return dict(s0=s0, s1=best["s"], n_eval=n, hit=False, rounds=rnd, pools=pools,
                skip=skip, keys=keys)


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
    mem = json.loads(X.MEM_FILE.read_text())

    def mines(tid, h):
        return {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}

    ctx = {}
    for tid, h in impls:
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        ctx[(tid, h)] = (Sx, V.harvest_chains(corpus, tid, h), W.harvest_exprs(corpus, tid, h))

    def run(**kw):
        out = {}
        for tid, h in impls:
            Sx, ch, ex = ctx[(tid, h)]
            out[(tid, h)] = search_keep(tid, corpus[tid][h], Sx, ch, ex, mines(tid, h), **kw)
        return out

    def summ(out):
        return dict(n_eval=sum(r["n_eval"] for r in out.values()),
                    hit=sum(1 for r in out.values() if r["hit"]),
                    mean_s1=sum(r["s1"] for r in out.values()) / len(out),
                    skip=sum(r["skip"] for r in out.values()))

    arms = {}
    for k in (1, 2, 3, 4, 6):
        arms[f"K{k}"] = (dict(keep=k, mode="top"), run(keep=k, mode="top"))
    for k in (2, 3):
        arms[f"DIV{k}"] = (dict(keep=k, mode="div"), run(keep=k, mode="div"))
    for k in (2, 3):
        arms[f"MEM{k}"] = (dict(keep=k, use_mem=True), run(keep=k, use_mem=True))

    base = arms["K2"][1]
    B = summ(base)
    print("## §0 自校验（⛔ 不成立则全部作废）")
    zero = [k for k, r in base.items() if r["n_eval"] == 0]
    ok0 = (B["n_eval"] == 588 and B["hit"] == 21 and len(zero) == 10)
    print(f"  K2（=部署 kept=2）判分 {B['n_eval']}（记 588）｜到 1.0 {B['hit']}/25（记 21）"
          f"｜零判分 {len(zero)}（记 10）｜mean s1 {B['mean_s1']:.2f}")
    M2 = arms["MEM2"][1]
    Sm2 = summ(M2)
    ok1 = (Sm2["n_eval"] == 42 and Sm2["hit"] == 19)
    print(f"  MEM2 判分 {Sm2['n_eval']}（记 42）｜到 1.0 {Sm2['hit']}（记 19）"
          f"｜跳过候选 {Sm2['skip']} 次（纪律 77：先确认开关接通）")
    ev2 = {k for k, r in base.items() if any(w > 2 for w in r["pools"])}
    print(f"  探针记的 5 个「有挤掉」实现 = {sorted(f'{t[:4]}::{h}' for t, h in ev2)}")
    print(f"  自校验 {'✅ 通过' if (ok0 and ok1 and len(ev2) == 5) else '⛔ 失败 —— 以下全部作废'}")

    print("\n## §1 宽度与选择规则（25 实现，含 10 个不搜索的）")
    print("  | 臂 | 判分 | %K2 | 到 1.0 | mean s1 | 跳过 |")
    print("  |---|---|---|---|---|---|")
    for name, (_kw, out) in arms.items():
        s_ = summ(out)
        print(f"  | {name} | {s_['n_eval']} | {s_['n_eval']/B['n_eval']*100:.1f}% |"
              f" {s_['hit']}/25 | {s_['mean_s1']:.2f} | {s_['skip']} |")

    print("\n## §2 逐实现差异（对照 K2）")
    for name in ("K1", "K3", "K4", "K6", "DIV2", "DIV3", "MEM2", "MEM3"):
        out = arms[name][1]
        diff = [(t, h) for t, h in impls
                if (out[(t, h)]["n_eval"], out[(t, h)]["hit"], round(out[(t, h)]["s1"], 6))
                != (base[(t, h)]["n_eval"], base[(t, h)]["hit"], round(base[(t, h)]["s1"], 6))]
        print(f"  {name}: 与 K2 不同 {len(diff)} 个"
              + ("" if not diff else "：" + ", ".join(f"{t[:4]}::{h[:8]}" for t, h in diff)))

    print("\n## §3 5 个「被挤掉」实现的细节（K2 / K3 / DIV2 / MEM2）")
    A = {"K2": arms["K2"][1], "K3": arms["K3"][1], "DIV2": arms["DIV2"][1], "MEM2": M2}
    tr = {n: run(keep=(2 if n in ("K2", "MEM2") else 3),
                 mode=("div" if n == "DIV2" else "top"),
                 use_mem=(n == "MEM2"), want_keys=True) for n in ("K2", "K3", "DIV2")}
    for key in sorted(ev2, key=lambda k: -base[k]["n_eval"]):
        print(f"  {key[0]}::{key[1]}")
        for n in ("K2", "K3", "DIV2"):
            r = tr[n][key]
            print(f"    {n:5s} n={r['n_eval']:3d} hit={int(r['hit'])} s1={r['s1']:.2f}"
                  f" pools={r['pools']} frontier_keys={r['keys']}")
        print(f"    MEM2  n={M2[key]['n_eval']:3d} hit={int(M2[key]['hit'])}"
              f" s1={M2[key]['s1']:.2f} pools={M2[key]['pools']}")

    print("\n## §4 等成本比较：固定判分预算 B 下到 1.0 的实现数")
    print("  | 臂 | " + " | ".join(f"B={b}" for b in BUDS) + " |")
    print("  |" + "---|" * (len(BUDS) + 1))
    budget_rows = {}
    for name, kw in (("K2", dict(keep=2)), ("K3", dict(keep=3)), ("K4", dict(keep=4)),
                     ("DIV2", dict(keep=2, mode="div")), ("MEM2", dict(keep=2, use_mem=True))):
        hits = []
        for b in BUDS:
            out = run(budget=b, **kw)
            h = sum(1 for r in out.values() if r["hit"])
            hits.append(h)
            budget_rows.setdefault(name, []).append(h)
        print(f"  | {name} | " + " | ".join(str(h) for h in hits) + " |")

    print("\n## §5 判据（PREREG §3；`成立` 与 `押` 同时打印，纪律 76）")
    res = {}

    def row(tag, holds, bet, txt):
        ok = (holds == bet)
        res[tag] = dict(holds=holds, bet=bet, ok=ok, txt=txt)
        print(f"  {tag} {'✅' if ok else '⛔'} {txt}｜命题成立={holds} 押={bet}")

    K3 = summ(arms["K3"][1])
    K4 = summ(arms["K4"][1])
    D2 = summ(arms["DIV2"][1])
    M3 = summ(arms["MEM3"][1])
    ladder = [k for k in ev2 if k[1] != "480ada8e"]

    row("C1", K3["hit"] == 21, True, f"K3 到 1.0 = {K3['hit']}/25")

    d3 = [(t, h) for t, h in impls
          if (arms['K3'][1][(t, h)]["n_eval"], arms['K3'][1][(t, h)]["hit"])
          != (base[(t, h)]["n_eval"], base[(t, h)]["hit"])]
    row("C2", len(d3) == 5, True, f"K3 与 K2 逐实现不同 = {len(d3)} 个（押 5）")

    lad_ok = all(arms["K3"][1][k]["hit"] and arms["K3"][1][k]["n_eval"] < 30 for k in ladder)
    row("C3", lad_ok, True,
        "4 个阶梯实现 K3 n_eval = "
        + ", ".join(f"{arms['K3'][1][k]['n_eval']}" for k in ladder) + "（押各 <30）")

    row("C4", (D2["hit"] == 21 and D2["n_eval"] <= B["n_eval"]), True,
        f"DIV2 到 1.0 = {D2['hit']}/25｜判分 {D2['n_eval']} vs K2 {B['n_eval']}")

    row("C5", D2["n_eval"] < K3["n_eval"], True,
        f"DIV2 判分 {D2['n_eval']} < K3 {K3['n_eval']}")

    same = all((M2[(t, h)]["n_eval"], M2[(t, h)]["hit"], round(M2[(t, h)]["s1"], 6))
               == (arms["MEM3"][1][(t, h)]["n_eval"], arms["MEM3"][1][(t, h)]["hit"],
                   round(arms["MEM3"][1][(t, h)]["s1"], 6)) for t, h in impls)
    row("C6", same, True, f"MEM3 与 MEM2 逐实现零差异 = {same}｜MEM3 判分 {M3['n_eval']}")

    best_other = max(budget_rows[n][BUDS.index(100)]
                     for n in ("K2", "K3", "K4", "DIV2"))
    row("C7", budget_rows["MEM2"][BUDS.index(100)] >= best_other, True,
        f"B=100 时 MEM2 命中 {budget_rows['MEM2'][BUDS.index(100)]}"
        f" ≥ 其余臂最好 {best_other}")

    save_by_impl = {k: base[k]["n_eval"] - arms["K3"][1][k]["n_eval"] for k in base}
    tot_save = sum(save_by_impl.values())
    lad_save = sum(save_by_impl[k] for k in ladder)
    # ⚠️ 分母为 0 ⇒ 比率**未定义**，不许打印成 0.0%（纪律 46/67）
    frac = lad_save / tot_save if tot_save else None
    row("C8", (frac is not None and frac >= 0.8), True,
        f"K3 省的 {tot_save} 次里 4 个阶梯贡献 {lad_save}"
        f"（{'分母=0 ⇒ 未定义' if frac is None else f'{frac*100:.1f}%'}）")

    print("\n## §6 分母单列（纪律 46/67）")
    searching = [k for k in base if base[k]["n_eval"] > 0]
    med = sorted(save_by_impl[k] for k in searching)
    print(f"  真正搜索的实现 = {len(searching)}｜不搜索（基线已 1.0）= {25-len(searching)}"
          f"（任何选择规则对它们是 0 收益）")
    print(f"  K3 每个实现的省判分：中位 {med[len(med)//2]}｜最小 {med[0]}｜最大 {med[-1]}"
          f"｜有省（>0）的实现 {sum(1 for x in med if x > 0)}/{len(searching)}")
    print(f"  MEM2 每个实现的省判分：中位 "
          f"{sorted(base[k]['n_eval']-M2[k]['n_eval'] for k in searching)[len(searching)//2]}"
          f"｜有省（>0）的实现 {sum(1 for k in searching if base[k]['n_eval'] > M2[k]['n_eval'])}"
          f"/{len(searching)}")

    json.dump(dict(
        arms={n: summ(o) for n, (_kw, o) in arms.items()},
        per_impl={f"{t}::{h}": {n: dict(n=arms[n][1][(t, h)]["n_eval"], hit=arms[n][1][(t, h)]["hit"],
                                       s1=arms[n][1][(t, h)]["s1"], pools=arms[n][1][(t, h)]["pools"])
                               for n in arms}
                  for t, h in impls},
        budget=budget_rows, buds=BUDS,
        save_k3=dict(total=tot_save, ladder=lad_save, frac=frac,
                     per_impl={f"{t}::{h}": save_by_impl[(t, h)] for t, h in impls}),
        criteria=res, cache=len(V.CACHE), wall_s=time.time() - t0,
    ), open(HERE / "readings_v159.json", "w"), ensure_ascii=False, indent=1)
    print(f"\n  已落盘 readings_v159.json（{time.time()-t0:.1f}s，CACHE {len(V.CACHE)}）")


if __name__ == "__main__":
    main()
