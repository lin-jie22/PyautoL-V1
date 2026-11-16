#!/usr/bin/env python3
"""V161 结构探针 2：`C1` 的表达式在**当前实现**里名字能不能解析；`B` 未验证来源的 s0 分布。

`harvest_exprs` 只按**来源实现的参数集**过滤自由变量 ⇒ 表达式里的名字可能在本实现不存在。
这是纯 AST 可判的（当前函数的参数 + 局部赋值 + 内建），零判分。
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


def scope_names(code):
    """当前实现里「能被解析」的名字：参数 + 局部赋值目标 + 内建。"""
    t = ast.parse(code)
    names = set(W.BUILTINS)
    for n in ast.walk(t):
        if isinstance(n, ast.FunctionDef):
            a = n.args
            names |= {x.arg for x in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)}
        if isinstance(n, ast.Assign):
            for tg in n.targets:
                for x in ast.walk(tg):
                    if isinstance(x, ast.Name):
                        names.add(x.id)
        if isinstance(n, (ast.For, ast.comprehension)):
            tgt = n.target
            for x in ast.walk(tgt):
                if isinstance(x, ast.Name):
                    names.add(x.id)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            names.add(n.id)
    return names


def expr_names(e):
    try:
        return {n.id for n in ast.walk(ast.parse(e, mode="eval")) if isinstance(n, ast.Name)}
    except SyntaxError:
        return set()


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
        ch = V.harvest_chains(corpus, tid, h)
        ex = W.harvest_exprs(corpus, tid, h)
        sc = scope_names(code)
        cands = X.gen(tid, code, Sx, ch, ex)
        for lab, srccode in cands:
            kind = lab.split(":")[0]
            if kind != "C1":
                continue
            e = lab.split(":=", 1)[1] if ":=" in lab else ""
            miss = expr_names(e) - sc
            s, _ = V.ev(tid, srccode)
            rows.append(dict(tid=tid, h=h, expr=e, n_miss=len(miss),
                             miss=sorted(miss), s=round(s, 2),
                             imp=s > s0[(tid, h)], hit=s >= 1.0))
    json.dump(rows, open(HERE / "readings_name_probe.json", "w"), ensure_ascii=False)

    print(f"## §0 `C1` 候选 {len(rows)} 条；名字在当前实现可解析的判定")
    ok = [r for r in rows if r["n_miss"] == 0]
    bad = [r for r in rows if r["n_miss"] > 0]
    print(f"  可解析 {len(ok)}｜不可解析 {len(bad)}（{len(bad)/len(rows)*100:.1f}%）")
    print("  | 切片 | 条数 | 改进 | 改进率 | 命中 | 命中率 |")
    print("  |---|---|---|---|---|---|")
    for name, sub in (("可解析", ok), ("不可解析", bad)):
        if not sub:
            continue
        imp = sum(1 for r in sub if r["imp"])
        hit = sum(1 for r in sub if r["hit"])
        print(f"  | {name} | {len(sub)} | {imp} | {imp/len(sub)*100:.1f}% | {hit} | {hit/len(sub)*100:.1f}% |")
    print("\n  不可解析的名字分布（Top 10）：")
    c = collections.Counter(x for r in bad for x in r["miss"])
    for k, v in c.most_common(10):
        print(f"    `{k}` × {v}")
    print("\n  逐实现（C1 条数 / 不可解析 / 改进 / 命中）：")
    by = collections.defaultdict(lambda: [0, 0, 0, 0])
    for r in rows:
        b = by[(r["tid"], r["h"])]
        b[0] += 1
        b[1] += 1 if r["n_miss"] else 0
        b[2] += 1 if r["imp"] else 0
        b[3] += 1 if r["hit"] else 0
    for (t, h), b in by.items():
        print(f"    {t[:12]}::{h[:8]}  {b[0]:2d} / 不可解析 {b[1]:2d} / 改进 {b[2]} / 命中 {b[3]}")

    print("\n## §1 `B` 未验证来源：来源 sibling 的 s0 分布")
    cnt = collections.Counter()
    for tid, h in impls:
        if s0[(tid, h)] >= 1.0:
            continue
        for c in V.harvest_chains(corpus, tid, h):
            pass
    for tid in V.TIDS:
        vals = sorted(round(v, 2) for (t, _), v in s0.items() if t == tid)
        print(f"  {tid}: 该题全部实现的 s0 = {vals}")


if __name__ == "__main__":
    main()
