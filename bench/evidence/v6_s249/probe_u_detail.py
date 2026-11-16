#!/usr/bin/env python3
"""V161 补量：`U` 为什么只省 40（结构上 50 条）＋ 能否与 `V` 叠加。
"""
import ast
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
sys.path.insert(0, str(HERE))
import v161 as M                    # noqa: E402

ROUNDS, KEEP = 6, 2


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
    s0 = {(t, h): V.ev(t, corpus[t][h])[0] for t, h in impls}
    mem = json.loads(X.MEM_FILE.read_text())

    def mines(tid, h):
        return {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}

    print("## §0 逐实现：ubq 候选的**结构条数** vs **实跳条数**")
    print("  | 实现 | n_sib | B 条数 | ubq 结构条数 | 实跳 | 命中轮 | 说明 |")
    print("  |---|---|---|---|---|---|---|")
    for tid, h in impls:
        if s0[(tid, h)] >= 1.0:
            continue
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ch, m = M.chains_meta(corpus, tid, h, s0)
        ex = W.harvest_exprs(corpus, tid, h)
        nsib = len(corpus[tid]) - 1
        gen_all = X.gen(tid, code, Sx, ch, ex)
        nb = sum(1 for l, _ in gen_all if l.split(":")[0] == "B")
        ubq_struct = sum(1 for l, _ in gen_all if l.split(":")[0] == "B"
                         and m.get(l.split(":", 2)[2], (0, 0))[0] == nsib)
        r = M.replay(tid, code, Sx, ch, ex, dict(m=m, n_sib=nsib), mines(tid, h),
                     skip_cls=frozenset({"T1", "T3", "T4"}), b_mode="ubiq")
        note = ""
        if r["n_skip_b"] < ubq_struct:
            note = f"提前命中/去重，少跳 {ubq_struct - r['n_skip_b']}"
        print(f"  | {tid[:10]}::{h[:6]} | {nsib} | {nb} | {ubq_struct} | {r['n_skip_b']} |"
              f" {'是' if r['hit'] else '否'} | {note} |")

    print("\n## §1 叠加臂：`U` + `V`（两者剪的集合是否相交）")
    print("  | 臂 | 判分 | 到 1.0 | mean s1 | 与 `NO134` 不同的实现 |")
    print("  |---|---|---|---|---|")
    base = {}
    for tid, h in impls:
        Sx, ch, ex = None, None, None
    # 复用 v161 的构造
    ctx, meta = {}, {}
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ch, m = M.chains_meta(corpus, tid, h, s0)
        ex = W.harvest_exprs(corpus, tid, h)
        ctx[(tid, h)] = (Sx, ch, ex)
        meta[(tid, h)] = dict(m=m, n_sib=len(corpus[tid]) - 1)

    def run_arm(skip_cls, b_mode):
        out = {}
        for tid, h in impls:
            Sx, ch, ex = ctx[(tid, h)]
            out[(tid, h)] = M.replay(tid, corpus[tid][h], Sx, ch, ex, meta[(tid, h)],
                                     mines(tid, h), skip_cls=skip_cls, b_mode=b_mode)
        return out

    arms = {
        "NO134": run_arm(frozenset({"T1", "T3", "T4"}), "none"),
        "U": run_arm(frozenset({"T1", "T3", "T4"}), "ubiq"),
        "V": run_arm(frozenset({"T1", "T3", "T4"}), "ver"),
        "UV": None,
    }
    # UV：ubiq 与 ver 都要跳 ⇒ 需要在 replay 里同时生效；用两次过滤近似：先 ubiq 再看 ver
    # 直接手写一个组合臂
    def replay_both(tid, code, Sx, ch, ex, mt, mine):
        s0v, _ = V.ev(tid, code)
        if s0v >= 1.0:
            return dict(s0=s0v, s1=s0v, n_eval=0, hit=True, n_skip_b=0)
        nsib = mt["n_sib"]
        frontier = [dict(code=code, s=s0v)]
        visited, n, ns = {code}, 0, 0
        for _ in range(ROUNDS):
            pool, seen = [], set()
            for node in frontier:
                cands = [(X.abstract(l), l, s) for l, s in X.gen(tid, node["code"], Sx, ch, ex)]
                for key, lab, src in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                    if src in visited or src in seen:
                        continue
                    kind = lab.split(":")[0]
                    if kind in ("T1", "T3", "T4"):
                        continue
                    if kind == "B":
                        npv, best = mt["m"].get(lab.split(":", 2)[2], (0, 0.0))
                        if npv == nsib or best < 1.0:
                            ns += 1
                            continue
                    sc, _ = V.ev(tid, src)
                    n += 1
                    if sc >= 1.0:
                        return dict(s0=s0v, s1=sc, n_eval=n, hit=True, n_skip_b=ns)
                    if sc > node["s"]:
                        pool.append(dict(code=src, s=sc))
                        seen.add(src)
            if not pool:
                break
            pool.sort(key=lambda d: -d["s"])
            frontier = pool[:KEEP]
            visited |= {d["code"] for d in pool}
        best = max(frontier, key=lambda d: d["s"])
        return dict(s0=s0v, s1=best["s"], n_eval=n, hit=False, n_skip_b=ns)

    arms["UV"] = {(t, h): replay_both(t, corpus[t][h], *ctx[(t, h)], meta[(t, h)], mines(t, h))
                  for t, h in impls}

    for a in ("NO134", "U", "V", "UV"):
        out = arms[a]
        diff = sum(1 for k in out
                   if (out[k]["hit"], round(out[k]["s1"], 6))
                   != (arms["NO134"][k]["hit"], round(arms["NO134"][k]["s1"], 6)))
        print(f"  | `{a}` | {sum(r['n_eval'] for r in out.values())} |"
              f" {sum(1 for r in out.values() if r['hit'])}/25 |"
              f" {sum(r['s1'] for r in out.values())/len(out):.2f} | {diff} |")

    print("\n## §2 被剪集合的交集（结构上）")
    tot_u = tot_v = tot_both = 0
    for tid, h in impls:
        if s0[(tid, h)] >= 1.0:
            continue
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ch, m = M.chains_meta(corpus, tid, h, s0)
        ex = W.harvest_exprs(corpus, tid, h)
        nsib = len(corpus[tid]) - 1
        for l, _ in X.gen(tid, code, Sx, ch, ex):
            if l.split(":")[0] != "B":
                continue
            npv, best = m.get(l.split(":", 2)[2], (0, 0.0))
            u = npv == nsib
            v = best < 1.0
            tot_u += u
            tot_v += v
            tot_both += (u and v)
    print(f"  `U` 剪 {tot_u}｜`V` 剪 {tot_v}｜交集 {tot_both}"
          f"⇒ {'可叠加（不相交）' if tot_both == 0 else '有重叠'}")


if __name__ == "__main__":
    main()
