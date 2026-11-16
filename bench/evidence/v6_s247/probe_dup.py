#!/usr/bin/env python3
"""V159 附量：同一份源码在一轮搜索里被判了几次（重复判分账）。

⚠️ 口径：本回放里 `V.ev` 有 CACHE ⇒ 重复调用**墙钟为 0**。所以这个数是**账本口径**
（逻辑上浪费了几次判分），不是可兑现的收益。⛔ 不接受「去重能省 7.3%」的说法。
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

    def mines(tid, h):
        return {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}

    rows = []
    for tid, h in impls:
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        ch, ex = V.harvest_chains(corpus, tid, h), W.harvest_exprs(corpus, tid, h)
        mi = mines(tid, h)
        s0, _ = V.ev(tid, corpus[tid][h])
        if s0 >= 1.0:
            continue
        frontier = [dict(code=corpus[tid][h], s=s0)]
        visited, n, scored, fresh = {corpus[tid][h]}, 0, set(), 0
        done = False
        for _ in range(6):
            pool, seen = [], set()
            for node in frontier:
                cands = [(X.abstract(lab), lab, src) for lab, src in X.gen(tid, node["code"], Sx, ch, ex)]
                for key, _lab, src in sorted(cands, key=lambda x: (0 if x[0] in mi else 1,)):
                    if src in visited or src in seen:
                        continue
                    n += 1
                    if src not in scored:
                        scored.add(src)
                        fresh += 1
                    s, _ = V.ev(tid, src)
                    if s >= 1.0:
                        done = True
                        break
                    if s > node["s"]:
                        pool.append(dict(code=src, s=s))
                        seen.add(src)
                if done:
                    break
            if done or not pool:
                break
            pool.sort(key=lambda d: -d["s"])
            frontier = pool[:2]
            visited |= {d["code"] for d in pool}
        if n:
            rows.append((tid, h, n, fresh))

    print("## 重复判分账（kept=2）")
    for tid, h, n, f in sorted(rows, key=lambda x: -x[2]):
        print(f"  {tid[:12]}::{h[:8]} 判分 {n:3d}｜首次判到的源码 {f:3d}｜重复 {n-f:3d}"
              f"（{(n-f)/n*100:4.1f}%）")
    T = sum(r[2] for r in rows)
    F = sum(r[3] for r in rows)
    print(f"  合计 {T}｜唯一 {F}｜重复 {T-F}（{(T-F)/T*100:.1f}%）")
    print("  ⚠️ `V.ev` 有 CACHE ⇒ 重复的墙钟成本为 0；这只是账本口径，不是收益。")
    json.dump(dict(per_impl={f"{t}::{h}": dict(n=n, fresh=f) for t, h, n, f in rows},
                   total=T, fresh=F, dup=T - F,
                   note="ev 有 CACHE ⇒ 墙钟 0，仅账本口径"),
              open(HERE / "readings_dup.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
