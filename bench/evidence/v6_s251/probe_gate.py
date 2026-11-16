#!/usr/bin/env python3
"""V166 结构探针：两条**免费**触发谓词会不会误杀已知的 5 个路径命中。

只看结构（字面量 vs 兄弟实现源码），不判分、不设判据。
若某谓词会切掉 V165 已知路径上的 `T5` 候选，它当场作废 —— 跑正式臂之前先量。
"""
import ast
import collections
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198", "v6_s210", "v6_s236"):
    sys.path.insert(0, str(EV / d))
import idiom_transfer as I          # noqa: E402
import v119 as V                    # noqa: E402
sys.path.insert(0, str(EV / "v6_s251"))
import v164 as U                    # noqa: E402

# V165 `RESULTS_v165.md` 记录的路径（`T5` 那些跳），用于「会不会误杀」
KNOWN = {
    "17747356": {"T5:c16:del0"},
    "0a6acc93": {"T5:c22:del0", "T5:c25:del0", "T5:c27:del1"},
    "5755e033": {"T5:c12:del1"},
}


def _doc_ids(tree):
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = getattr(n, "body", None)
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                out.add(id(b[0].value))
    return out


def t5_literals(code):
    """[(label, 字面量值)]，与 `v164.candidates_T5` 同序同筛选。"""
    out = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return out
    doc = _doc_ids(tree)
    for i, n in enumerate(ast.walk(tree)):
        if not (isinstance(n, ast.Constant) and isinstance(n.value, str)):
            continue
        if id(n) in doc:
            continue
        s = n.value
        if not s:
            continue
        for k in range(len(s)):
            out.append((f"T5:c{i}:del{k}", s))
    return out


def main() -> None:
    corpus = I.harvest()
    tid = "g7d3_fmt_amount"
    impls = [h for h in sorted(corpus[tid]) if _parse_ok(corpus[tid][h])]
    print(f"## 语料：{tid} 可解析 {len(impls)} 个")

    for h in impls:
        sib = [corpus[tid][x] for x in impls if x != h]
        sib_txt = "\n".join(sib)
        # 兄弟字面量多重集（用于 P5）
        sib_lits = collections.Counter()
        for x in sib:
            try:
                t = ast.parse(x)
            except SyntaxError:
                continue
            for n in ast.walk(t):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value:
                    sib_lits[n.value] += 1
        lits = t5_literals(corpus[tid][h])
        print(f"\n{'='*74}\n## {h[:8]}：T5 候选 {len(lits)}｜兄弟字面量 {len(sib_lits)} 种")
        seen = collections.Counter()
        for lab, s in lits:
            # P1 字面量在兄弟源码里**作为子串**出现 ⇒ 视为「共享惯例」，跳过生成
            p1_skip = s in sib_txt
            # P5 字面量与某「≥2 个兄弟都有」的字面量**逐字相等** ⇒ 跳过
            p5_skip = sib_lits.get(s, 0) >= 2
            seen[(p1_skip, p5_skip)] += 1
            mark = "★" if lab in KNOWN.get(h, set()) else " "
            if lab in KNOWN.get(h, set()) or (not p1_skip and not p5_skip) is False:
                print(f"  {mark} {lab:16} 字面量 {s!r:14} P1跳过={p1_skip} P5跳过={p5_skip}")
        print(f"  （P1,P5）分布：{dict(seen)}")
        for lab in sorted(KNOWN.get(h, set())):
            s = next((v for k, v in lits if k == lab), None)
            if s is None:
                print(f"  ⚠ 已知路径标签 {lab} 不在当前候选里（口径变了？）")
                continue
            print(f"  ★ 已知路径 {lab} 字面量 {s!r}：P1 跳过={s in sib_txt}｜"
                  f"P5 跳过={sib_lits.get(s, 0) >= 2}")


def _parse_ok(code):
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


if __name__ == "__main__":
    main()
