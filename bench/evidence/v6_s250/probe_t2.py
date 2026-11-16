#!/usr/bin/env python3
"""V162 跑前结构（零判据）：`T2` 里「插入的元素本来就在容器里」有多少条。

`T2` = 往 `x in [...]` / `x in (...)` 的容器追加一个词 w（w ∈ S，题面派生的词集）。
若 w 已在容器的**常量元素**里 ⇒ 源码变、语义不变 ⇒ 必然 `s == s0`（V111：分数是行为的单射函数）。
不可判的部分（容器含非字面量）必须单独报分母（纪律 64）。
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


def cont_values(node):
    """容器里能 literal_eval 的元素值；有任何一个不可判 ⇒ 返回 None（不得当 0 处理）。"""
    vals = []
    for e in node.elts:
        try:
            vals.append(ast.literal_eval(e))
        except (ValueError, SyntaxError):
            return None
    return vals


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

    rows = []
    for tid, h in impls:
        if s0[(tid, h)] >= 1.0:
            continue
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        st = S.sites(code)
        nodes = list(ast.walk(ast.parse(code)))
        base = s0[(tid, h)]
        for i, j in st["in_cont"]:
            c = nodes[i].comparators[j]
            vals = cont_values(c)
            for w in sorted(Sx):
                if vals is None:
                    dup, what = "U", "-"
                elif w in vals:
                    dup, what = "Y", repr(w)
                else:
                    dup, what = "N", repr(w)
                # 实际候选（与 S.candidates 同构）
                def mut(ns, i=i, j=j, w=w):
                    ns[i].comparators[j].elts.append(ast.Constant(value=w))
                t = ast.parse(code)
                mut(list(ast.walk(t)))
                src = ast.unparse(ast.fix_missing_locations(t))
                s, _ = V.ev(tid, src)
                rows.append(dict(tid=tid, h=h, site=f"cont{i}.{j}", w=w, dup=dup,
                                 cont=vals, s=round(s, 2), s0=round(base, 2),
                                 eq=(abs(s - base) < 1e-9), imp=s > base, hit=s >= 1.0,
                                 src=src))
    json.dump(rows, open(HERE / "readings_t2_probe.json", "w"), ensure_ascii=False)

    print(f"## §0 `T2` 候选 {len(rows)} 条（15 个搜索实现）")
    c = collections.Counter(r["dup"] for r in rows)
    print(f"  可判 Y（w 已在容器里）{c['Y']}｜N（不在）{c['N']}｜U（容器含非字面量，不可判）{c['U']}")
    print("\n## §1 三分片的改进/命中/等价")
    print("  | 切片 | 条数 | 改进 | 命中 | 与 s0 同分 |")
    print("  |---|---|---|---|---|")
    for k, name in (("Y", "Y 已在容器里"), ("N", "N 不在容器里"), ("U", "U 不可判")):
        sub = [r for r in rows if r["dup"] == k]
        if not sub:
            print(f"  | {name} | 0 | - | - | - |")
            continue
        print(f"  | {name} | {len(sub)} | {sum(1 for r in sub if r['imp'])} |"
              f" {sum(1 for r in sub if r['hit'])} | {sum(1 for r in sub if r['eq'])} |")

    print("\n## §2 Y 切片逐条（若全等价 ⇒ 结构性死路）")
    y = [r for r in rows if r["dup"] == "Y"]
    print(f"  Y 里与 s0 **同分** 的 {sum(1 for r in y if r['eq'])}/{len(y)}")
    bad = [r for r in y if not r["eq"]]
    for r in bad[:10]:
        print(f"    ⚠️ {r['tid'][:12]}::{r['h'][:8]} {r['site']} +{r['w']!r} "
              f"容器={r['cont']} s {r['s0']}→{r['s']}")

    print("\n## §3 容器形态（Y 从哪来）")
    cnt = collections.Counter((len(r["cont"]) if r["cont"] else 0, r["dup"]) for r in rows)
    by_len = collections.defaultdict(lambda: [0, 0, 0])
    for r in rows:
        if r["cont"] is None:
            continue
        b = by_len[len(r["cont"])]
        b[0] += 1
        b[1] += 1 if r["dup"] == "Y" else 0
        b[2] += 1 if r["dup"] == "N" else 0
    print("  | 容器元素数 | 候选数 | 其中 Y | 其中 N |")
    print("  |---|---|---|---|")
    for L in sorted(by_len):
        print(f"  | {L} | {by_len[L][0]} | {by_len[L][1]} | {by_len[L][2]} |")

    print("\n## §4 |S| 里被容器覆盖的比例（题级）")
    for tid in V.TIDS:
        sub = [r for r in rows if r["tid"] == tid]
        if not sub:
            continue
        print(f"  {tid}: T2 候选 {len(sub)}｜Y {sum(1 for r in sub if r['dup']=='Y')}"
              f"｜U {sum(1 for r in sub if r['dup']=='U')}")


if __name__ == "__main__":
    main()
