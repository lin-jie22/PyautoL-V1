"""V34 第一步：量「守恒」本身（零 LLM）。

问的是：`e1wi + e1ni ≡ 16`（十二轮零例外）到底是
  (a) 题面决定的（同一份骨架的两个变体，模型必然只写对一边），还是
  (b) 「一次生成只能二选一」的偶然，存在两边都对的样本。

做法：把历史上**同一个 run 内**同时存在 e1wi/e1ni 的分数取出来做 2×2 交叉表。
不同 run 之间条件不同，不能比；同一 run 内条件相同，这才是单变量。

同时给出：两题各自的失败机制分类（用答案原文，不用台账里的 why）。
"""
import glob
import json
import os
import re
import collections

TASKS = ("e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni")
TW = re.compile(r"^\s*(?:\w+\s*=\s*)?set\(\)", re.M)
TWK = re.compile(r"^\s*kept\s*=\s*set\(\)", re.M)
IMP = re.compile(r"^\s*import\s+re\b", re.M)
ADDCP = re.compile(r"kept\s*\.\s*append", re.M)
KEPT_LIST = re.compile(r"^\s*kept\s*=\s*\[\]", re.M)


def collect():
    out = []
    for rj in sorted(glob.glob("/tmp/v*/**/runs.jsonl", recursive=True)):
        d = os.path.dirname(rj)
        arm = os.path.basename(d)
        rnd = os.path.basename(os.path.dirname(d))
        ans = {}
        aj = os.path.join(d, "answers.jsonl")
        if os.path.exists(aj):
            for line in open(aj, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                a = json.loads(line)
                if a.get("kind") == "header":
                    continue
                ans[a.get("task_id")] = a.get("answer", "")
        for line in open(rj, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            rep = r.get("report") or {}
            per = rep.get("per_task", [])
            if not per:
                continue
            sc = {pt["id"]: float(pt.get("score", 0)) for pt in per}
            if not all(t in sc for t in TASKS):
                continue
            out.append({"tag": f"{rnd}/{arm}", "sc": sc, "ans": ans})
    return out


def mech(txt):
    """失败机制分类（只对 e1 两题有意义）。"""
    if not txt:
        return "NA"
    bits = []
    bits.append("set()" if TWK.search(txt) else "list")
    bits.append("import_re" if IMP.search(txt) else "no_re")
    bits.append("append" if ADDCP.search(txt) else "no_append")
    return "+".join(bits)


def main():
    runs = collect()
    print(f"# 全量 run（六题齐全）= {len(runs)}")
    print()

    print("## 1  e1wi × e1ni 2×2（同一 run 内）")
    print()
    tab = collections.Counter()
    for r in runs:
        a = 1 if r["sc"]["e1wi"] > 0 else 0
        b = 1 if r["sc"]["e1ni"] > 0 else 0
        tab[(a, b)] += 1
    print(f"{'e1wi':>6s} {'e1ni':>6s}  run 数   和")
    for a in (1, 0):
        for b in (1, 0):
            n = tab.get((a, b), 0)
            print(f"{a:>6d} {b:>6d}  {n:>6d}   {16 * (a + b)}")
    print()
    both = tab.get((1, 1), 0)
    print(f"两边都过 = {both} / {len(runs)}")
    print(f"恰好一边过 = {tab.get((1,0),0) + tab.get((0,1),0)} / {len(runs)}")
    print(f"两边都不过 = {tab.get((0,0),0)} / {len(runs)}")
    print()
    if both:
        print("### 两边都过的 run（存在性证明 ⇒ 守恒不是结构律）")
        for r in runs:
            if r["sc"]["e1wi"] > 0 and r["sc"]["e1ni"] > 0:
                print(f"  {r['tag']:22s} e1wi={r['sc']['e1wi']:.2f} e1ni={r['sc']['e1ni']:.2f}")
    else:
        print("### 两边都过：**零**（全历史没有一次）")
    print()

    print("## 2  逐对（pair）守恒核对")
    for pair in ("e1", "e2", "e3"):
        w, n = f"{pair}wi", f"{pair}ni"
        s = collections.Counter(int(r["sc"][w] > 0) + int(r["sc"][n] > 0) for r in runs)
        print(f"  pair={pair}: 过题数分布 {dict(sorted(s.items()))}  (Σ 恒 {16*2} 的 run = {s.get(1,0)}/{len(runs)})")
    print()

    print("## 3  e1 两题的失败机制（答案原文分类，不看台账 why）")
    for t in ("e1wi", "e1ni"):
        print(f"\n### {t}")
        c = collections.Counter()
        for r in runs:
            c[(mech(r["ans"].get(t, "")), int(r["sc"][t] > 0))] += 1
        for k in sorted(c, key=str):
            print(f"  {k[0]:38s} 过={k[1]}  n={c[k]}")
    print()

    print("## 4  e1ni 过 / 不过 × 是否写了 import re（只看有答案原文的 run）")
    c = collections.Counter()
    for r in runs:
        a = r["ans"].get("e1ni")
        if a is None:
            continue
        c[(bool(IMP.search(a)), int(r["sc"]["e1ni"] > 0))] += 1
    for k in sorted(c, key=str):
        print(f"  import_re={k[0]!s:5s} 过={k[1]}  n={c[k]}")


if __name__ == "__main__":
    main()
