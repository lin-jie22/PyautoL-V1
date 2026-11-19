"""V210 机制读数（**非判据**）：答案的**形状**有没有跟着评审走。

V209 §4 用的就是这一格：t04 的「对 tuple 下标赋值」从 18/20 掉到 13/20，
通过数恰好 +5。本轮问同一件事 —— 去掉元数之后，t01 的「按 h 切两段」还会不会上升。

⛔ 分类器是**字面正则**，不是语义判定。它的读数只能当「形状对照」，不能当「答案对不对」。
⛔ 分母 = 该臂该题的答案条数（去重前），与 PREREG 的题次口径不同 ⇒ 不参与 P1~P5。
"""
import json
import re
import sys

UNITS = re.compile(r"""['"]s['"]\s*:\s*1|units\s*=\s*\{|3600""")
SPLIT_H = re.compile(r"""\.split\(\s*['"]h['"]\s*\)""")
TUP_ASSIGN = re.compile(r"\[\s*-1\s*\]\s*\[\s*1\s*\]\s*=")
TUP_REBUILD = re.compile(r"\[\s*-1\s*\]\s*=\s*\(")


def main():
    runs = [json.loads(l) for l in open("bench_out/runs.jsonl", encoding="utf-8") if l.strip()]
    rows = [json.loads(l) for l in open("bench_out/answers.jsonl", encoding="utf-8") if l.strip()]
    #: ⛔ 摘要有自己的行（`cap`/`n_answers`/`ts`，没有 `task_id`）——
    #:   不过滤的话 `x["task_id"]` 直接 KeyError（V210 自踩：首版就在这炸了）。
    rows = [x for x in rows if "answer" in x and "task_id" in x]
    names = [("A(off)", runs[-3]), ("B(prev-self,raw)", runs[-2]),
             ("D(prev-self,transferable)", runs[-1])]
    for nm, r in names:
        rid = r.get("run_id")
        mine = [x for x in rows if x.get("run_id") == rid]
        print(f"\n=== {nm}  run_id={rid}  答案 {len(mine)} 条")

        t01 = [x["answer"] for x in mine if x["task_id"].startswith("t01")]
        t04 = [x["answer"] for x in mine if x["task_id"].startswith("t04")]
        if t01:
            u = sum(1 for a in t01 if UNITS.search(a))
            h = sum(1 for a in t01 if SPLIT_H.search(a))
            print(f"  t01 n={len(t01)}  单位表/3600={u}（{u/len(t01):.0%}）"
                  f"  按 h 切={h}（{h/len(t01):.0%}）")
        if t04:
            a_ = sum(1 for a in t04 if TUP_ASSIGN.search(a))
            b_ = sum(1 for a in t04 if TUP_REBUILD.search(a))
            print(f"  t04 n={len(t04)}  对 tuple 下标赋值={a_}（{a_/len(t04):.0%}）"
                  f"  重建元组={b_}（{b_/len(t04):.0%}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
