#!/usr/bin/env python3
"""V163：`g7d3` 的 4 个缺口在「全枚举」下可达吗。

主臂 `E3` = 不剪枝/不早停/不按分数筛 frontier 的 BFS，深度 ≤3。
扩展臂 `E4` = 同口径，深度 ≤4（仅当 `E3` 后仍有缺口）。
归因 = 在 `U` 臂（V162 口径）的**逐行判定顺序**上重放最短路径，找第一处断裂。

零新增判分的说法不成立（全枚举要 5067 次 ev）—— 但那是**可达性**的钱，按 V146 单独记。
"""
import ast
import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198", "v6_s210", "v6_s236"):
    sys.path.insert(0, str(EV / d))
import idiom_transfer as I          # noqa: E402
import search as S                  # noqa: E402
import v119 as V                    # noqa: E402
import v120 as W                    # noqa: E402
import v121 as X                    # noqa: E402
sys.path.insert(0, str(EV / "v6_s249"))
import v161 as M                    # noqa: E402
sys.path.insert(0, str(EV / "v6_s250"))
import v162 as R                    # noqa: E402

TID = "g7d3_fmt_amount"
KEEP, ROUNDS = 2, 6
SKIP134 = frozenset({"T1", "T3", "T4"})


def bfs(code, Sx, ch, ex, D):
    """不剪枝/不早停/不筛 frontier 的 BFS。返回每层最优点、最短命中路径、生成量。"""
    s0 = V.ev(TID, code)[0]
    dist = {code: 0}
    parent = {code: None}
    lab_of = {code: None}
    level = {0: (code, s0)}
    frontier = [code]
    n_gen = 0
    hit_path = None
    for d in range(1, D + 1):
        nxt, best, best_s = [], None, -1.0
        for c in frontier:
            try:
                cands = X.gen(TID, c, Sx, ch, ex)
            except Exception:                            # noqa: BLE001
                continue
            n_gen += len(cands)
            for lab, src in cands:
                if src in dist:
                    continue
                sc = V.ev(TID, src)[0]
                dist[src] = d
                parent[src] = c
                lab_of[src] = lab
                nxt.append(src)
                if sc > best_s:
                    best, best_s = src, sc
                if sc >= 1.0 and hit_path is None:
                    p, cur = [], src
                    while cur is not None:
                        p.append(cur)
                        cur = parent[cur]
                    hit_path = (list(reversed(p)), [lab_of[x] for x in reversed(p)][1:])
        level[d] = (best, best_s)
        frontier = nxt
        if not frontier:
            break
    return dict(s0=s0, dist=dist, parent=parent, lab_of=lab_of, level=level,
                n_gen=n_gen, hit_path=hit_path)


def greedy_trace(code, Sx, ch, ex, meta, mine):
    """逐行复刻 V162 `U` 臂（`b_ubiq=True`, `skip_dup=False`），记下每个判定的首因。"""
    s0 = V.ev(TID, code)[0]
    n_sib = meta["n_sib"]
    frontier = [dict(code=code, s=s0)]
    visited, verdict = {code}, {}
    expanded, pool_ever = {code}, set()
    rounds = []
    for _ in range(ROUNDS):
        pool, seen = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, srcc) for lab, srcc in
                     X.gen(TID, node["code"], Sx, ch, ex)]
            for _k, lab, srcc in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                key = (node["code"], srcc)
                kind = lab.split(":")[0]
                if srcc in visited or srcc in seen:
                    verdict.setdefault(key, "dup")
                    continue
                if kind in SKIP134:
                    verdict.setdefault(key, "skip_cls")
                    continue
                if kind == "B":
                    npv, _best = meta["m"].get(lab.split(":", 2)[2], (0, 0.0))
                    if npv == n_sib:
                        verdict.setdefault(key, "skip_ubiq")
                        continue
                sc = V.ev(TID, srcc)[0]
                if sc > node["s"]:
                    verdict.setdefault(key, "acc")
                    pool.append(dict(code=srcc, s=sc))
                    seen.add(srcc)
                else:
                    verdict.setdefault(key, "notimp")
        if not pool:
            rounds.append(dict(frontier=[n["code"] for n in frontier], pool=[],
                               kept=[]))
            break
        pool.sort(key=lambda d: -d["s"])
        kept = pool[:KEEP]
        rounds.append(dict(frontier=[n["code"] for n in frontier],
                           pool=[(d["code"], d["s"]) for d in pool],
                           kept=[d["code"] for d in kept]))
        pool_ever |= {d["code"] for d in pool}
        frontier = kept
        visited |= {d["code"] for d in pool}
        expanded |= {d["code"] for d in kept}
    return dict(s0=s0, verdict=verdict, expanded=expanded, pool_ever=pool_ever,
                rounds=rounds, s1=max((n["s"] for n in frontier), default=s0))


def attribute(path, trace, pool_ever):
    """沿最短路径找第一处断裂。返回 (断裂步, 原因, 细节) 或 (None, 'reachable_by_greedy', '')。"""
    verdict = trace["verdict"]
    expanded = trace["expanded"]
    for t in range(len(path) - 1):
        p, c = path[t], path[t + 1]
        if c in expanded:
            continue
        if c in pool_ever:
            return t, "trunc", f"`{p[:0]}` 的子代进 pool 但排名 >{KEEP}"
        v = verdict.get((p, c))
        if v is None:
            return t, "other", "该步在 U 臂里从未被生成"
        if v == "acc":
            return t, "other", "标 acc 却未进 expanded（口径不自洽）"
        return t, {"notimp": "downhill", "skip_cls": "pruned_cls",
                   "skip_ubiq": "pruned_ubiq"}.get(v, "other"), v
    return None, "reachable_by_greedy", ""


def main() -> None:
    corpus = I.harvest()
    impls = []
    for h in sorted(corpus[TID]):
        try:
            ast.parse(corpus[TID][h])
        except SyntaxError:
            continue
        impls.append(h)
    s0 = {(TID, h): V.ev(TID, corpus[TID][h])[0] for h in impls}
    todo = [h for h in impls if s0[(TID, h)] < 1.0]

    ctx, meta = {}, {}
    ev_meta = 0
    for h in impls:
        code = corpus[TID][h]
        Sx, _ = S.wordset(TID, code, True)
        before = len(V.CACHE)
        ch, m = M.chains_meta(corpus, TID, h, s0)
        ev_meta += len(V.CACHE) - before
        ex = W.harvest_exprs(corpus, TID, h)
        ctx[h] = (Sx, ch, ex)
        meta[h] = dict(m=m, n_sib=len(corpus[TID]) - 1)

    def mines(h):
        return {(a, b) for kk in json.loads(X.MEM_FILE.read_text())[TID]
                if kk["h"] != h for a, b in [tuple(kk["act"])]}

    # ---------------- §1 对照臂：V162 `U`
    U = {h: R.replay(TID, corpus[TID][h], *ctx[h], meta[h], mines(h),
                     skip_dup=False, b_ubiq=True) for h in impls}

    # ---------------- §2 全枚举
    E = {}
    for h in impls:
        if s0[(TID, h)] >= 1.0:
            E[h] = None
            continue
        E[h] = bfs(corpus[TID][h], *ctx[h], 3)

    print("## §1 `U` 臂（V162 口径）vs 全枚举 `E3`（深度 ≤3，不剪枝/不早停/不筛）")
    print("  | impl | s0 | `U` 判分 | `U` s1 | `U` 命中 | `E3` 状态 | `E3` 生成 | d1 最好 | d2 最好 | d3 最好 | `E3` 命中 |")
    print("  |---|---|---|---|---|---|---|---|---|---|---|")
    for h in impls:
        u = U[h]
        if E[h] is None:
            print(f"  | {h[:8]} | {s0[(TID,h)]:.2f} | 0 | 1.00 | ✅(s0) | — | — | — | — | — | — |")
            continue
        e = E[h]
        lv = e["level"]
        cells = " | ".join(f"{lv[d][1]:.2f}" if d in lv and lv[d][1] >= 0 else "—"
                           for d in (1, 2, 3))
        best_all = max([v for (_c, v) in lv.values() if v >= 0] or [0.0])
        print(f"  | {h[:8]} | {s0[(TID,h)]:.2f} | {u['n_eval']} | {u['s1']:.2f} |"
              f" {'✅' if u['hit'] else '❌'} | {len(e['dist'])} | {e['n_gen']} | {cells} |"
              f" {'✅' if e['hit_path'] else '❌'}（best {best_all:.2f}）|")

    # ---------------- §3 缺口归因
    gaps = [h for h in todo if not U[h]["hit"]]
    reached = [h for h in gaps if E[h] and E[h]["hit_path"]]
    print(f"\n## §2 缺口 {len(gaps)} 个｜`E3` 下可达 {len(reached)} 个")
    print("  | impl | s0 | `U` s1 | `E3` 最好 | 最短命中深度 | 第一处断裂 | 原因 |")
    print("  |---|---|---|---|---|---|---|")
    att = {}
    for h in gaps:
        e = E[h]
        tr = greedy_trace(corpus[TID][h], *ctx[h], meta[h], mines(h))
        best_all = max([v for (_c, v) in e["level"].values() if v >= 0] or [0.0])
        if e["hit_path"]:
            path, labs = e["hit_path"]
            t, why, det = attribute(path, tr, tr["pool_ever"])
            att[h] = dict(depth=len(path) - 1, why=why, det=det, labs=labs)
            print(f"  | {h[:8]} | {s0[(TID,h)]:.2f} | {U[h]['s1']:.2f} | {best_all:.2f} |"
                  f" {len(path)-1} | 第 {t} 步 | {why} |")
        else:
            att[h] = dict(depth=None, why="unreachable", det="", labs=[])
            print(f"  | {h[:8]} | {s0[(TID,h)]:.2f} | {U[h]['s1']:.2f} | {best_all:.2f} |"
                  f" — | — | unreachable |")

    for h in reached:
        print(f"\n  路径 {h[:8]}（{att[h]['depth']} 步）：")
        for lab in att[h]["labs"]:
            print(f"    + {lab}")

    # ---------------- §4 扩展臂 E4
    print("\n## §3 扩展臂 `E4`（深度 ≤4，仅对 `E3` 不可达者）")
    E4 = {}
    for h in gaps:
        if E[h]["hit_path"]:
            E4[h] = None
            continue
        e4 = bfs(corpus[TID][h], *ctx[h], 4)
        E4[h] = e4
        best_all = max([v for (_c, v) in e4["level"].values() if v >= 0] or [0.0])
        print(f"  {h[:8]}：状态 {len(e4['dist'])}｜生成 {e4['n_gen']}｜d4 最好 {e4['level'].get(4,(None,-1))[1]:.2f}"
              f"｜全层最好 {best_all:.2f}｜命中 {'✅ 深度 %d' % (len(e4['hit_path'][0])-1) if e4['hit_path'] else '❌'}")
        if e4["hit_path"]:
            att[h]["why"] = "reachable_at_d4"
            att[h]["depth"] = len(e4["hit_path"][0]) - 1
            att[h]["labs"] = e4["hit_path"][1]

    # ---------------- §5 会计
    n_e3 = sum(len(E[h]["dist"]) for h in todo)
    n_e4 = sum(len(E4[h]["dist"]) for h in todo if E4.get(h))
    n_u = sum(u["n_eval"] for u in U.values())
    print("\n## §4 会计（可达性 vs 有效性，V146）")
    print(f"  `U` 臂判分 {n_u}｜`E3` 唯一状态 {n_e3}（{n_e3/max(n_u,1):.1f}×）｜"
          f"`E4` 增量唯一状态 {n_e4}｜标签期新增判分 {ev_meta}")

    # ---------------- §6 判据
    print("\n## §5 判据（三值：PASS / FAIL / NA=无实例，纪律 69）")
    rows = []

    def row(tag, status, msg):
        rows.append((tag, status, msg))
        mark = {"PASS": "✅", "FAIL": "⛔", "NA": "⬜"}[status]
        print(f"  {tag} {mark} [{status}]  {msg}")

    causes = collections.Counter(a["why"] for a in att.values())
    row("A1", "PASS" if len(reached) >= 1 else "FAIL",
        f"`E3` 下可达缺口 {len(reached)} 个（押 ≥1）")
    if reached:
        dmin = min(att[h]["depth"] for h in reached)
        row("A2", "PASS" if dmin >= 2 else "FAIL",
            f"可达缺口最短命中深度 min {dmin}（押 ≥2 ⇒ 无 d=1 被漏）")
    else:
        row("A2", "NA", "无可达缺口 ⇒ 无「最短命中深度」可判")
    if reached:
        row("A3", "PASS" if causes["downhill"] >= 1 else "FAIL",
            f"第一处断裂=①下坡 的缺口 {causes['downhill']} 个（押 ≥1）")
        row("A4", "PASS" if (causes["pruned_cls"] + causes["pruned_ubiq"]
                             + causes["trunc"]) >= 1 else "FAIL",
            f"第一处断裂=②被剪/③宽度截断 的缺口 "
            f"{causes['pruned_cls'] + causes['pruned_ubiq'] + causes['trunc']} 个（押 ≥1）")
        row("A5", "PASS" if causes["other"] == 0 else "FAIL",
            f"归因④其他/不自洽 {causes['other']} 个（押 0）")
    else:
        row("A3", "NA", "无通路可归因 ⇒ 无第一处断裂")
        row("A4", "NA", "同上")
        row("A5", "NA", "同上（0 个 other 是**空**，不是证据，纪律 36）")
    sup = all((not U[h]["hit"]) or (E[h] is None or E[h]["hit_path"] is not None)
              for h in impls)
    row("A6", "PASS" if sup else "FAIL", f"`E3` 命中集 ⊇ `U` 命中集：{sup}（自校验，不计入结论）")

    n_pass = sum(1 for r in rows if r[1] == "PASS")
    n_fail = sum(1 for r in rows if r[1] == "FAIL")
    n_na = sum(1 for r in rows if r[1] == "NA")
    print(f"\n  PASS {n_pass}｜FAIL {n_fail}｜NA {n_na}（实质判据 = A1~A5："
          f"{sum(1 for r in rows if r[1]=='PASS' and r[0]!='A6')} 过、"
          f"{sum(1 for r in rows if r[1]=='FAIL' and r[0]!='A6')} 破、"
          f"{sum(1 for r in rows if r[1]=='NA')} 无实例）")
    json.dump(dict(
        s0={h: s0[(TID, h)] for h in impls},
        U={h: dict(n_eval=U[h]["n_eval"], s1=U[h]["s1"], hit=U[h]["hit"]) for h in impls},
        E3={h: dict(n_state=len(E[h]["dist"]), n_gen=E[h]["n_gen"],
                    level={d: E[h]["level"][d][1] for d in E[h]["level"]},
                    hit=bool(E[h]["hit_path"]),
                    depth=(len(E[h]["hit_path"][0]) - 1) if E[h]["hit_path"] else None)
            for h in todo},
        E4={h: dict(n_state=len(E4[h]["dist"]), n_gen=E4[h]["n_gen"],
                    level={d: E4[h]["level"][d][1] for d in E4[h]["level"]},
                    hit=bool(E4[h]["hit_path"]),
                    depth=(len(E4[h]["hit_path"][0]) - 1) if E4[h]["hit_path"] else None)
            for h in todo if E4.get(h)},
        att=att, cost=dict(n_u=n_u, n_e3=n_e3, n_e4=n_e4, ev_meta=ev_meta),
        criteria=[dict(id=r[0], status=r[1], msg=r[2]) for r in rows]),
        open(HERE / "readings_v163.json", "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()
