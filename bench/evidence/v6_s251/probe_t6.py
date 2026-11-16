#!/usr/bin/env python3
"""V165 结构探针：剩下 2 个缺口到底需要什么样的「负数括号闭合」编辑（纯测量，无判据）。

对每个缺口的两个状态（原码 / `U5` 贪心终点 0.60）应用一组**模板**，看哪些能到 1.0。
模板全部来自本语料里**兄弟实现自己的语句**，不引用题面测试。
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
sys.path.insert(0, str(EV / "v6_s249"))
import v161 as M                    # noqa: E402
sys.path.insert(0, str(EV / "v6_s251"))
import v164 as U                    # noqa: E402

TID = "g7d3_fmt_amount"
GAPS = ["17747356", "8c8ac7f0"]
TMP = "_t6"


def _ret_site(tree):
    """(func_idx, ret_idx) —— 只在「函数体顶层恰好一个 return 且是最后一句」时返回。

    ⚠ 必须传**已经解析好的树**：早先版本在这里又 `ast.parse` 了一次，
    于是改动落在另一棵树上、`unparse` 回来仍是原码（四个模板输出逐字相同）。
    """
    out = []
    for fi, fn in enumerate(ast.walk(tree)):
        if not isinstance(fn, ast.FunctionDef):
            continue
        rets = [i for i, st in enumerate(fn.body) if isinstance(st, ast.Return)]
        if len(rets) != 1 or rets[0] != len(fn.body) - 1:
            continue
        if fn.body[rets[0]].value is None:
            continue
        out.append((fi, rets[0], fn))
    return out


def tmpl(code, which):
    """在唯一 return 站点上套模板。which ∈ {A,B,C,BC}。"""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    sites = _ret_site(tree)
    if len(sites) != 1:
        return None
    fi, ri, fn = sites[0]
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    if TMP in names:
        return None
    expr = fn.body[ri].value
    assign = ast.Assign(targets=[ast.Name(id=TMP, ctx=ast.Store())], value=expr)

    def call(attr, arg):
        return ast.Call(func=ast.Attribute(value=ast.Name(id=TMP, ctx=ast.Load()),
                                           attr=attr, ctx=ast.Load()),
                        args=[ast.Constant(value=arg)], keywords=[])

    def wrap(k):
        """'(' + TMP[k:] + ')'"""
        return ast.BinOp(
            left=ast.BinOp(left=ast.Constant(value="("),
                           op=ast.Add(),
                           right=ast.Subscript(value=ast.Name(id=TMP, ctx=ast.Load()),
                                               slice=ast.Slice(lower=ast.Constant(value=k)),
                                               ctx=ast.Load())),
            op=ast.Add(), right=ast.Constant(value=")"))

    ret = lambda e: ast.Return(value=e)                       # noqa: E731
    load = lambda: ast.Name(id=TMP, ctx=ast.Load())           # noqa: E731

    if which == "A":
        # 5755e033：return s + ')' if s.startswith('(') else s
        body = [assign, ret(ast.IfExp(test=call("startswith", "("),
                                      body=ast.BinOp(left=load(), op=ast.Add(),
                                                     right=ast.Constant(value=")")),
                                      orelse=load()))]
    elif which in ("B", "C"):
        pre = "-" if which == "B" else "(-"
        k = len(pre)
        body = [assign,
                ast.If(test=call("startswith", pre),
                       body=[ast.Assign(targets=[ast.Name(id=TMP, ctx=ast.Store())],
                                        value=wrap(k))],
                       orelse=[]),
                ret(load())]
    elif which == "BC":                        # 先 B 再 C：两个前缀都归一
        body = [assign,
                ast.If(test=call("startswith", "(-"),
                       body=[ast.Assign(targets=[ast.Name(id=TMP, ctx=ast.Store())],
                                        value=wrap(2))],
                       orelse=[ast.If(test=call("startswith", "-"),
                                      body=[ast.Assign(
                                          targets=[ast.Name(id=TMP, ctx=ast.Store())],
                                          value=wrap(1))],
                                      orelse=[])]),
                ret(load())]
    else:
        return None
    fn.body[ri:] = body
    src = ast.unparse(ast.fix_missing_locations(tree))
    assert src != code, "模板没有生效（装置自校验）"
    return src


def u5_paths(corpus):
    """用 v164 的 replay_gen 跑出 `U5` 的终点代码（与主实验同一口径：含记忆排序 `mine`）。"""
    impls = []
    for h in sorted(corpus[TID]):
        try:
            ast.parse(corpus[TID][h])
        except SyntaxError:
            continue
        impls.append(h)
    s0 = {(TID, h): V.ev(TID, corpus[TID][h])[0] for h in impls}
    mem = json.loads(X.MEM_FILE.read_text())

    def mines(h):
        return {(a, b) for kk in mem[TID] if kk["h"] != h for a, b in [tuple(kk["act"])]}

    out = {}
    for h in GAPS:
        Sx, _ = S.wordset(TID, corpus[TID][h], True)
        ch, m = M.chains_meta(corpus, TID, h, s0)
        ex = W.harvest_exprs(corpus, TID, h)
        meta = dict(m=m, n_sib=len(corpus[TID]) - 1)
        r = U.replay_gen(TID, corpus[TID][h], Sx, ch, ex, meta, mines(h), U.gen5)
        best = max(r["trace"].keys() if r["trace"] else [corpus[TID][h]],
                   key=lambda c: V.ev(TID, c)[0])
        out[h] = (r, best)
    return out


def main():
    corpus = I.harvest()
    up = u5_paths(corpus)
    for h in GAPS:
        r, best = up[h]
        for tag, code in (("orig", corpus[TID][h]), ("U5终点", best)):
            sc, bm = V.ev(TID, code)
            print(f"\n{'='*78}\n## {h} [{tag}] s={sc:.2f} bm={list(bm)}")
            for ln in code.strip().splitlines():
                print("    " + ln)
            for which in ("A", "B", "C", "BC"):
                src = tmpl(code, which)
                if src is None:
                    print(f"  T6-{which}: 站点不可用（多 return / 有同名临时变量）")
                    continue
                s2, bm2 = V.ev(TID, src)
                mark = "★" if s2 > sc else " "
                print(f"  {mark} T6-{which}: s={s2:.2f} bm={list(bm2)}")
                print("      " + src.replace("\n", "\n      "))


if __name__ == "__main__":
    main()
