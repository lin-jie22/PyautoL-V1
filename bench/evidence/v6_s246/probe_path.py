#!/usr/bin/env python3
"""V158 探针（探索性，非预注册判据）：
① 路径的宽度：逐条移除「非命中键上的改进候选」，看谁致命；
② 两个尾部命中各自需要的最小预算 B；
③ 4 个阶梯命中在 BASE / MEM / BUD(60) 下分别花了多少。
零新增判分（全部复用 V.ev 的确定性结果）。"""
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

ROUNDS, KEEP = 6, 2


def _search(tid, code, Sx, chains, exprs, mine, skip_src=frozenset(), bud=None):
    """同构搜索：skip_src 跳源码；bud 限制非记忆判分次数（None = 不限）。"""
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, s1=s0, n_eval=0, hit=True, events=[], n_non=0)
    frontier = [dict(code=code, s=s0)]
    visited, n, n_non, ev = {code}, 0, 0, []
    for _ in range(ROUNDS):
        pool, seen = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, src) for lab, src in X.gen(tid, node["code"], Sx, chains, exprs)]
            for key, _lab, src in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                if src in visited or src in seen or src in skip_src:
                    continue
                if bud is not None and key not in mine and n_non >= bud:
                    continue
                s, _ = V.ev(tid, src)
                n += 1
                if key not in mine:
                    n_non += 1
                ev.append((key, s, src, s > node["s"]))
                if s >= 1.0:
                    return dict(s0=s0, s1=s, n_eval=n, hit=True, events=ev, n_non=n_non)
                if s > node["s"]:
                    pool.append(dict(code=src, s=s))
                    seen.add(src)
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:KEEP]
        visited |= {d["code"] for d in pool}
    best = max(frontier, key=lambda d: d["s"])
    return dict(s0=s0, s1=best["s"], n_eval=n, hit=False, events=ev, n_non=n_non)


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
    mem = json.loads(X.MEM_FILE.read_text())
    ctx = {}
    for tid, h in impls:
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        ctx[(tid, h)] = (Sx, V.harvest_chains(corpus, tid, h), W.harvest_exprs(corpus, tid, h))

    def mines(tid, h):
        return {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}

    def run(skip_src=frozenset(), bud=None):
        out = {}
        for tid, h in impls:
            Sx, ch, ex = ctx[(tid, h)]
            out[(tid, h)] = _search(tid, corpus[tid][h], Sx, ch, ex, mines(tid, h), skip_src, bud)
        return out

    base = run()
    H0 = sum(1 for r in base.values() if r["hit"])
    print(f"## §0 自校验 BASE 命中 {H0}/25（记 21）")

    # ---------------- ① 路径宽度
    print("\n## §1 路径宽度：逐条移除「非命中键上的改进候选」")
    stones = [(k, tuple(e[0]), e[2]) for k, r in base.items()
              for e in r["events"] if e[3] and e[1] < 1.0]
    print(f"  非命中键上的改进事件 = {len(stones)} 条")
    by_impl = {}
    for k, key, src in stones:
        by_impl.setdefault(k, []).append(key)
    for k, v in sorted(by_impl.items(), key=lambda x: -len(x[1])):
        print(f"    {k[0][:16]}::{k[1]}  {len(v)} 条  {[str(x) for x in v]}")
    killers = []
    for k, key, src in stones:
        r = run(skip_src={src})
        h1 = sum(1 for x in r.values() if x["hit"])
        if h1 < H0:
            killers.append((k, key, H0 - h1))
            print(f"    {k[0][:16]}::{k[1]} 键{str(key):22s} 命中 {H0}→{h1}  ⭐ 致命")
    print(f"  ⇒ 单条移除就打掉命中的「承重踏脚石」= {len(killers)}/{len(stones)}")

    # ---------------- ② 尾部命中的预算阈值
    print("\n## §2 两个尾部命中各自需要的最小预算 B")
    for target in ("g7d2_dedupe_keep::480ada8e", "g7e1_parse_flag::200ebf6e"):
        tid, h = target.split("::")
        need = None
        for b in list(range(0, 71, 5)) + list(range(80, 141, 10)):
            if run(bud=b)[(tid, h)]["hit"]:
                need = b
                break
        print(f"  {tid[:16]}::{h} 需 B = {need}")

    # ---------------- ③ 阶梯命中
    print("\n## §3 4 个阶梯命中：BASE / MEM / BUD(60)")
    outm, out60 = run(bud=0), run(bud=60)
    print("  %-28s %6s %6s %6s   hit B/M/60" % ("impl", "BASE", "MEM", "BUD60"))
    for k in sorted(base, key=lambda k: -base[k]["n_eval"])[:8]:
        print("  %-28s %6d %6d %6d   %s/%s/%s"
              % (f"{k[0][:16]}::{k[1]}", base[k]["n_eval"], outm[k]["n_eval"],
                 out60[k]["n_eval"], base[k]["hit"], outm[k]["hit"], out60[k]["hit"]))


if __name__ == "__main__":
    main()
