#!/usr/bin/env python3
"""V161 跑前结构（零判据）：候选的**来源**能不能免费拿到，拿到后有没有分辨力。

⛔ 口径修正：类与来源**一律从 label 解析**，不用 `srccode` 做键
（不同类会生成同一源码，用源码做键会互相覆盖 —— 首版把 T1 249 读成 219、B 106 读成 141）。
"""
import ast
import collections
import json
import pathlib
import statistics
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


def chains_with_src(corpus, tid, exclude):
    src = collections.defaultdict(set)
    for h, code in corpus[tid].items():
        if h == exclude:
            continue
        try:
            tree = ast.parse(code)
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            seq, cur, ok = [], n, True
            while isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
                if cur.func.attr in V.BAN:
                    ok = False
                    break
                args = []
                for a in cur.args:
                    if isinstance(a, ast.Constant):
                        args.append(a.value)
                    else:
                        ok = False
                        break
                if not ok or cur.keywords:
                    ok = False
                    break
                seq.append((cur.func.attr, tuple(args)))
                cur = cur.func.value
            if ok and seq and len(seq) <= 3:
                src[tuple(reversed(seq))].add(h)
    return src


def exprs_with_src(corpus, tid, exclude):
    src = collections.defaultdict(set)
    for h, code in corpus[tid].items():
        if h == exclude:
            continue
        try:
            if V.ev(tid, code)[0] < 1.0:
                continue
            params = W._params(code)
            for r in W._returns(code):
                if W._free_names(r.value, params):
                    continue
                src[ast.unparse(r.value)].add(h)
        except SyntaxError:
            continue
    return src


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
    print(f"## §0 语料：{len(impls)} 个实现 / {len(V.TIDS)} 题；s0==1.0 的 {sum(1 for v in s0.values() if v >= 1.0)} 个")

    rows, cost = [], []
    for tid, h in impls:
        if s0[(tid, h)] >= 1.0:
            continue
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ch_src = chains_with_src(corpus, tid, h)
        ex_src = exprs_with_src(corpus, tid, h)
        ch = sorted(ch_src, key=lambda c: (len(c), str(c)))
        ex = list(ex_src)
        cost.append(dict(tid=tid, h=h, n_sib=len(corpus[tid]) - 1,
                         n_chain=len(ch), n_expr=len(ex)))
        base = s0[(tid, h)]
        for lab, srccode in X.gen(tid, code, Sx, ch, ex):
            kind = lab.split(":")[0]
            best, nprov = None, 0
            if kind == "B":
                lab_c = lab.split(":", 2)[2]
                for cc in ch:
                    if V._label(cc) == lab_c:
                        provs = ch_src.get(cc, set())
                        best = max((s0[(tid, p)] for p in provs), default=None)
                        nprov = len(provs)
                        break
            elif kind == "C1":
                e = lab.split(":=", 1)[1] if ":=" in lab else ""
                provs = ex_src.get(e, set())
                best = max((s0[(tid, p)] for p in provs), default=None)
                nprov = len(provs)
            s, _ = V.ev(tid, srccode)
            rows.append(dict(tid=tid, h=h, kind=kind, lab=lab, src_best=best, n_prov=nprov,
                             s=round(s, 2), s0=round(base, 2), imp=s > base, hit=s >= 1.0))
    json.dump(rows, open(HERE / "readings_src_probe.json", "w"), ensure_ascii=False)

    tot = len(rows)
    print(f"\n## §1 来源分解（{tot} 条）—— 与 V160 的 671 对账")
    print("  | 类 | 条数 | 来源 | 已验证(s0=1.0) | 未验证 | 无外部来源 | 改进 | 命中 |")
    print("  |---|---|---|---|---|---|---|---|")
    desc = {"T1": "自身常量表", "T2": "自身容器字面量", "T3": "自身 return 包装",
            "T4": "自身比较主语", "B": "同题 sibling 的方法链", "C1": "同题 sibling 的 return 表达式"}
    for k in ("T1", "T2", "T3", "T4", "B", "C1"):
        sub = [r for r in rows if r["kind"] == k]
        if not sub:
            continue
        ver = sum(1 for r in sub if r["src_best"] is not None and r["src_best"] >= 1.0)
        unv = sum(1 for r in sub if r["src_best"] is not None and r["src_best"] < 1.0)
        none = sum(1 for r in sub if r["src_best"] is None)
        print(f"  | `{k}` | {len(sub)} | {desc[k]} | {ver} | {unv} | {none} |"
              f" {sum(1 for r in sub if r['imp'])} | {sum(1 for r in sub if r['hit'])} |")

    print("\n## §2 ⭐ 来源已验证 vs 未验证")
    print("  | 切片 | 条数 | 改进 | 改进率 | 命中 | 命中率 |")
    print("  |---|---|---|---|---|---|")
    for name, sub in (
        ("`B` 已验证", [r for r in rows if r["kind"] == "B" and r["src_best"] is not None and r["src_best"] >= 1.0]),
        ("`B` 未验证", [r for r in rows if r["kind"] == "B" and r["src_best"] is not None and r["src_best"] < 1.0]),
        ("`C1`（结构上全已验证）", [r for r in rows if r["kind"] == "C1"]),
        ("`T2`（无外部来源）", [r for r in rows if r["kind"] == "T2"]),
    ):
        if not sub:
            print(f"  | {name} | 0 | - | - | - | - |")
            continue
        imp = sum(1 for r in sub if r["imp"])
        hit = sum(1 for r in sub if r["hit"])
        print(f"  | {name} | {len(sub)} | {imp} | {imp/len(sub)*100:.1f}% | {hit} | {hit/len(sub)*100:.1f}% |")

    print("\n## §3 成本账")
    print(f"  来源验证（判同题 sibling）：每实现中位 {statistics.median(c['n_sib'] for c in cost)}"
          f"｜合计 {sum(c['n_sib'] for c in cost)} 次")
    print(f"  V160 `NO_134` 后搜索：260 次 ⇒ 验证/搜索 = {sum(c['n_sib'] for c in cost)/260:.2f}×")
    print(f"  `C1` 的来源验证**已经付过**（`harvest_exprs` 内部就判 sibling）"
          f"⇒ 它的「已验证」标签结构性免费")


if __name__ == "__main__":
    main()
