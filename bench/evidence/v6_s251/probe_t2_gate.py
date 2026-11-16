#!/usr/bin/env python3
"""V167 STAGE0：`T2` 的**结构**读数 —— 0 次判分。

问三件事（全部只看 AST/字面量集合，不调 `V.ev`）：
  1. `T2` 候选的规模分布：每实现几个 `in` 容器 × |S|。
  2. `w` 落在「同题兄弟实现的字符串常量集」里的比例 —— 若接近 100%，
     先验就退化成「砍掉整类」，那不是「选得准」，是本轮不能用的退化形态（纪律 51）。
  3. 两档谓词的切法差异：
       `Q1` = `w` ∈ 兄弟实现的**全部**字符串常量值（与 V166 `P2` 完全同构）
       `Q2` = `w` ∈ 兄弟实现的 **`in` 容器内**常量值（更窄，只算同类用法）

⛔ 本探针不判分、不读判据，只报结构量。
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
import search as S                  # noqa: E402
import v119 as V                    # noqa: E402
sys.path.insert(0, str(EV / "v6_s251"))
import v166b as B                   # noqa: E402


def in_container_values(src):
    """一个实现里所有 `x in [...]` / `x in (...)` 容器内的常量值集合。"""
    vals = set()
    try:
        t = ast.parse(src)
    except SyntaxError:
        return vals
    for n in ast.walk(t):
        if not isinstance(n, ast.Compare):
            continue
        for op, comp in zip(n.ops, n.comparators):
            if not isinstance(op, (ast.In, ast.NotIn)):
                continue
            if not isinstance(comp, (ast.List, ast.Tuple)):
                continue
            for e in comp.elts:
                if isinstance(e, ast.Constant) and isinstance(e.value, str):
                    vals.add(e.value)
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

    per_tid = collections.defaultdict(lambda: dict(n_in=0, n_cand=0, n_w=0,
                                                  w_in_q1=0, w_in_q2=0,
                                                  n_sib_const=0, n_sib_in=0))
    detail = []
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, meta = S.wordset(tid, code, True)
        st = S.sites(code)
        cands = [c for c in S.candidates(code, Sx) if c[0].startswith("T2:")]
        consts, ins = set(), set()
        for x in corpus[tid]:
            if x == h:
                continue
            consts |= B.const_values(corpus[tid][x])
            ins |= in_container_values(corpus[tid][x])
        d = per_tid[tid]
        d["n_in"] += len(st["in_cont"])
        d["n_cand"] += len(cands)
        d["n_w"] += len(Sx)
        d["n_sib_const"] += len(consts)
        d["n_sib_in"] += len(ins)
        # 每个 (容器, 词) 组合的判定；用 label 解析出词
        for lab, _src in cands:
            w = lab.split(":+", 1)[1]
            w = ast.literal_eval(w)
            if w in consts:
                d["w_in_q1"] += 1
            if w in ins:
                d["w_in_q2"] += 1
        if cands:
            detail.append((tid, h, len(st["in_cont"]), len(Sx), len(cands)))

    print("## §1 逐题结构量")
    print("  | 题 | 实现 | `in` 容器 | `T2` 候选 | `w∈Q1` | `w∈Q2` | 兄弟常量 | 兄弟容器值 |")
    for tid in sorted(per_tid):
        d = per_tid[tid]
        n_impl = sum(1 for t, _h in impls if t == tid)
        p1 = d["w_in_q1"] / d["n_cand"] * 100 if d["n_cand"] else 0
        p2 = d["w_in_q2"] / d["n_cand"] * 100 if d["n_cand"] else 0
        print(f"  | {tid} | {n_impl} | {d['n_in']} | {d['n_cand']} | "
              f"{d['w_in_q1']} ({p1:.0f}%) | {d['w_in_q2']} ({p2:.0f}%) | "
              f"{d['n_sib_const']} | {d['n_sib_in']} |")
    T = collections.Counter()
    for d in per_tid.values():
        T.update(d)
    print(f"  | **合计** | {len(impls)} | {T['n_in']} | {T['n_cand']} | "
          f"{T['w_in_q1']} ({T['w_in_q1']/T['n_cand']*100:.1f}%) | "
          f"{T['w_in_q2']} ({T['w_in_q2']/T['n_cand']*100:.1f}%) | — | — |")

    print("\n## §2 每实现明细（有 `T2` 候选的）")
    print("  | impl | 题 | `in` 站点 | \\|S\\| | `T2` 候选 |")
    for tid, h, ni, ns, nc in detail:
        print(f"  | {h[:8]} | {tid[:6]} | {ni} | {ns} | {nc} |")
    print(f"\n  有 `T2` 候选的实现 **{len(detail)}/{len(impls)}**；"
          f"无站点/无候选的 **{len(impls)-len(detail)}** 个")

    print("\n## §3 `|S|` 的来源分解（前 5 个实现）")
    for tid, h in impls[:5]:
        _Sx, m = S.wordset(tid, corpus[tid][h], True)
        print(f"  {h[:8]}：code {len(m['code'])} / prompt {len(m['prompt'])} / "
              f"tests {len(m['tests'])} ⇒ |S| {m['n']}")

    print("\n## §4 退化告警（纪律 51：先量覆盖面，再定判据）")
    r1 = T["w_in_q1"] / T["n_cand"]
    r2 = T["w_in_q2"] / T["n_cand"]
    for name, r in (("`Q1`", r1), ("`Q2`", r2)):
        if r >= 0.95:
            print(f"  ⛔ {name} 切掉 {r*100:.1f}% —— 接近整类，"
                  f"「命中不变」将无法与「整类不起作用」区分")
        elif r <= 0.10:
            print(f"  ⚠️ {name} 只切 {r*100:.1f}% —— 信号几乎不响，省不下成本")
        else:
            print(f"  ✅ {name} 切 {r*100:.1f}% —— 落在可测的中间区")
    print(f"\n  总候选 {T['n_cand']}，`Q1` 保 {T['n_cand']-T['w_in_q1']} 条，"
          f"`Q2` 保 {T['n_cand']-T['w_in_q2']} 条")


if __name__ == "__main__":
    main()
