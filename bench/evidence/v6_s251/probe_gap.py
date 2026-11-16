#!/usr/bin/env python3
"""V163 诊断：4 个缺口到底缺什么算子（**纯测量，无判据**）。

对每个缺口：原码 / 5 条测试的 bitmap / 深度 ≤4 全枚举里的最优状态 / 它相对原码的差异 /
深度 1 的候选标签菜单（即「现有动作空间」的全部算子）。
"""
import ast
import difflib
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
import v163 as E                    # noqa: E402

TID = "g7d3_fmt_amount"
GAPS = ["0a6acc93", "17747356", "5755e033", "8c8ac7f0"]


def main() -> None:
    corpus = I.harvest()
    impls = []
    for h in sorted(corpus[TID]):
        try:
            ast.parse(corpus[TID][h])
        except SyntaxError:
            continue
        impls.append(h)
    s0 = {(TID, h): V.ev(TID, corpus[TID][h]) for h in impls}

    task = I.task_of(TID)
    print(f"## 题面 {TID}（tests = {len(task['tests'])}）")
    for i, ts in enumerate(task["tests"]):
        print(f"  T{i}: {ts.strip()[:150]}")
    print(f"  spec: {str(task.get('spec') or task.get('prompt') or '')[:400]}")

    for h in GAPS:
        code = corpus[TID][h]
        sc, bm = s0[(TID, h)]
        print(f"\n{'='*78}\n## {h}：s0={sc:.2f}  bitmap={list(bm)}")
        print("  原码：")
        for ln in code.strip().splitlines():
            print("    " + ln)
        Sx, _ = S.wordset(TID, code, True)
        ch, m = M.chains_meta(corpus, TID, h, s0)
        ex = W.harvest_exprs(corpus, TID, h)
        labs = sorted({lab for lab, _s in X.gen(TID, code, Sx, ch, ex)})
        print(f"  d=1 候选标签 {len(labs)} 条：")
        for lab in labs:
            print("    · " + lab)
        e4 = E.bfs(code, Sx, ch, ex, 4)
        best = None
        for d in sorted(e4["level"]):
            if d == 0:
                continue
            cand, bs = e4["level"][d]
            if cand is not None and (best is None or bs > best[1]):
                best = (cand, bs, d)
        if best is None:
            print("  ⚠ 无可达状态")
            continue
        bcode, bs, bd = best
        print(f"  最优可达（深度 {bd}）：s={bs:.2f}  bitmap={list(V.ev(TID, bcode)[1])}")
        print("  差异（原码 → 最优）：")
        diff = list(difflib.unified_diff(code.strip().splitlines(),
                                         bcode.strip().splitlines(),
                                         "orig", "best", lineterm="", n=1))
        for ln in diff[2:]:
            print("    " + ln)
        # 沿路径取每一跳的标签
        p, cur = [], bcode
        while cur is not None:
            p.append(cur)
            cur = e4["parent"][cur]
        p = list(reversed(p))
        print(f"  路径（{len(p)-1} 跳）：")
        for i in range(1, len(p)):
            print(f"    {i}. {e4['lab_of'][p[i]]}  ⇒ s={V.ev(TID, p[i])[0]:.2f}")


if __name__ == "__main__":
    main()
