#!/usr/bin/env python3
"""V168 STAGE0：把「风格域 vs 语义域」量成一个免费数 `n_occ`（0 判分）。

问（§235.8）：`T5` 该切的候选值（∈ 兄弟常量集，风格域）与 `T2` 该切的值
（∉ 兄弟常量集，语义域）能否由**同一个数 + 同一个阈值**分开？

⛔ 恒等式陷阱（跑前已排除，纪律 68）：
  若把 `n_occ` 定义成「在**兄弟**代码里的出现次数」，则两边的金标是它的
  补集/正集**定义**⇒ 重叠恒 0%，这个押注不含信息。
  ⇒ 本探针只报**与兄弟定义无关**的量：
     `n_code_all`（本题全部实现，含自己）／`n_test`（本题测试脚本里的字面量）／
     `n_all = n_code_all + n_test`。
  `n_code_sib` 只作对照，**不进判据**。
"""
import ast
import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for d in ("v6_s198", "v6_s205", "v6_s208", "v6_s209", "v6_s236"):
    sys.path.insert(0, str(EV / d))
sys.path.insert(0, str(EV / "v6_s249"))
sys.path.insert(0, str(EV / "v6_s251"))
import idiom_transfer as I          # noqa: E402
import search as S                  # noqa: E402
import v119 as V                    # noqa: E402
import v120 as W                    # noqa: E402
import v161 as M                    # noqa: E402
import v164 as U                    # noqa: E402
import v166 as A                    # noqa: E402
import v166b as B                   # noqa: E402

T2_TID = "g7e1_parse_flag"


def const_multiset(src):
    """本题源码里的字符串常量值（**多重集**）：`v166b.const_values` 的计数版。"""
    c = collections.Counter()
    try:
        t = ast.parse(src)
    except SyntaxError:
        return c
    for n in ast.walk(t):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            c[n.value] += 1
    return c


def test_counter(tid):
    """本题测试脚本里的字符串字面量（多重集）。0 判分：只 parse，不 exec。"""
    task = I.task_of(tid) or {}
    c = collections.Counter()
    for ts in task.get("tests") or []:
        for chunk in (ts,):
            try:
                t = ast.parse(chunk)
            except SyntaxError:
                continue
            for n in ast.walk(t):
                if isinstance(n, ast.Constant) and isinstance(n.value, str):
                    c[n.value] += 1
    return c


def q(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
    return s[i]


def stat(vals):
    if not vals:
        return dict(n=0)
    return dict(n=len(vals), median=q(vals, 0.5), q05=q(vals, 0.05), q95=q(vals, 0.95),
                mn=min(vals), mx=max(vals),
                zero=sum(1 for v in vals if v == 0) / len(vals))


def overlap_ratio(a, b):
    """q05~q95 区间重叠 / 并集（口径写死，纪律 89）。"""
    if not a or not b:
        return None
    a0, a1, b0, b1 = q(a, 0.05), q(a, 0.95), q(b, 0.05), q(b, 0.95)
    lo, hi = max(a0, b0), min(a1, b1)
    inter = max(0, hi - lo + 1)
    uni = max(a1, b1) - min(a0, b0) + 1
    return inter / uni if uni else None


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

    # 每题：全部实现的常量多重集 + 测试字面量多重集
    code_ms, test_ms = {}, {}
    for tid in V.TIDS:
        c = collections.Counter()
        for h in corpus[tid]:
            c.update(const_multiset(corpus[tid][h]))
        code_ms[tid] = c
        test_ms[tid] = test_counter(tid)

    print("## §0 题目语料口径")
    print("  | 题 | 实现数 | 代码常量种类 | 测试字面量种类 | 测试条数 |")
    for tid in V.TIDS:
        print(f"  | {tid} | {len(corpus[tid])} | {len(code_ms[tid])} | "
              f"{len(test_ms[tid])} | {len(I.task_of(tid).get('tests') or [])} |")

    # 四个集合：以**去重值**为单位（值层面，不是候选层面）
    G = collections.defaultdict(set)       # group -> {w}
    for tid, h in impls:
        code = corpus[tid][h]
        sib = set()
        for x in corpus[tid]:
            if x != h:
                sib |= B.const_values(corpus[tid][x])
        t5v = set(A.lit_of(code).values())
        for w in t5v:
            G["t5cut" if w in sib else "t5keep"].add((tid, w))
        for lab, _s in U.candidates_T5(code):
            w = A.lit_of(code).get(lab)
            if w is not None:
                G["t5cut" if w in sib else "t5keep"].add((tid, w))

    # T2：只在 g7e1，用与 v167 完全相同的候选源
    import v167 as Q                    # noqa: E402
    s0 = {(t, h): V.ev(t, corpus[t][h])[0] for t, h in impls}
    for h in sorted(corpus[T2_TID]):
        code = corpus[T2_TID][h]
        sib = set()
        for x in corpus[T2_TID]:
            if x != h:
                sib |= B.const_values(corpus[T2_TID][x])
        Sx, _ = S.wordset(T2_TID, code, True)
        ch, _m = M.chains_meta(corpus, T2_TID, h, s0)
        ex = W.harvest_exprs(corpus, T2_TID, h)
        out = B.make_gen("p2", sib)(T2_TID, code, Sx, ch, ex)
        t2 = [(lab, s) for lab, s in out if lab.startswith("T2:")]
        seen = set()
        for lab, _s in t2:
            w = Q.t2_word(lab)
            if w in seen:
                continue
            seen.add(w)
            G["t2cut" if w not in sib else "t2keep"].add((T2_TID, w))

    def vals(tag, fn):
        return [fn(tid, w) for tid, w in G[tag]]

    for key in ("n_code_all", "n_test", "n_all", "n_code_sib"):
        if key == "n_code_all":
            fn = lambda tid, w: code_ms[tid][w]
        elif key == "n_test":
            fn = lambda tid, w: test_ms[tid][w]
        elif key == "n_all":
            fn = lambda tid, w: code_ms[tid][w] + test_ms[tid][w]
        else:
            def fn(tid, w):
                c = 0
                for x in corpus[tid]:
                    c += const_multiset(corpus[tid][x])[w]
                return c
        print(f"\n## §1 `{key}` 分布（值层面去重，括号内为 (题,值) 对数）")
        print("  | 集合 | n | 中位 | q05 | q95 | min | max | =0 占比 |")
        for tag in ("t5cut", "t5keep", "t2cut", "t2keep"):
            v = vals(tag, fn)
            s = stat(v)
            if not s["n"]:
                print(f"  | `{tag}` | 0 | — | — | — | — | — | — |")
                continue
            print(f"  | `{tag}` | {s['n']} | {s['median']} | {s['q05']} | {s['q95']} | "
                  f"{s['mn']} | {s['mx']} | {s['zero']:.2f} |")
        o = overlap_ratio(vals("t5cut", fn), vals("t2cut", fn))
        print(f"  ⇒ `t5cut` vs `t2cut` q05~q95 区间重叠 **{o if o is None else round(o, 3)}**")

    # 单谓词可分性：`n >= θ ⇒ 该切`
    print("\n## §2 单谓词 `n_all >= θ ⇒ 切` 的混淆矩阵（金标 = t5cut ∪ t2cut）")
    print("  | θ | t5cut 判对 | t5keep 误切 | t2cut 判对 | t2keep 误切 | 合计准确 |")
    pos = [("t5cut", 1), ("t2cut", 1), ("t5keep", 0), ("t2keep", 0)]
    for th in range(0, 6):
        cells = {}
        for tag, gold in pos:
            v = vals(tag, lambda tid, w: code_ms[tid][w] + test_ms[tid][w])
            pred = [1 if x >= th else 0 for x in v]
            cells[tag] = (sum(1 for p in pred if p == gold), len(pred))
        tot_ok = sum(c[0] for c in cells.values())
        tot = sum(c[1] for c in cells.values())
        print(f"  | {th} | {cells['t5cut'][0]}/{cells['t5cut'][1]} | "
              f"{cells['t5keep'][1] - cells['t5keep'][0]}/{cells['t5keep'][1]} | "
              f"{cells['t2cut'][0]}/{cells['t2cut'][1]} | "
              f"{cells['t2keep'][1] - cells['t2keep'][0]}/{cells['t2keep'][1]} | "
              f"{tot_ok}/{tot} = {tot_ok / tot:.3f} |")

    print("\n## §3 同题限定（只 `g7e1`，排除题目混杂）")
    for key in ("n_test", "n_all"):
        if key == "n_test":
            fn = lambda tid, w: test_ms[tid][w]
        else:
            fn = lambda tid, w: code_ms[tid][w] + test_ms[tid][w]
        print(f"  `{key}`：")
        for tag in ("t5cut", "t2cut"):
            v = [fn(tid, w) for tid, w in G[tag] if tid == T2_TID]
            s = stat(v)
            print(f"    `{tag}@{T2_TID}`：n={s['n']} 中位 {s.get('median')} "
                  f"区间 [{s.get('mn')},{s.get('mx')}]")

    out = {k: dict(n=len(G[k]),
                   vals=sorted(f"{tid}::{w}" for tid, w in G[k]))
           for k in G}
    (HERE / "readings_v168_stage0.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
