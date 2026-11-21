"""V218-C：免费普查 —— `kernel_external.calls` 是谁决定的、它能不能单独解释分数。

⛔ 不跑模型、不改产品。只读 `bench_out/runs.jsonl` 与 `bench/evidence/l3_s302/order_*.txt`。

要回答三件事：
  C1  calls 的分布是什么形状（常量？双峰？长尾？）
  C2  calls 与分数是什么关系（按题交叉）
  C3  calls 是装置属性还是 planner 的抽样输出（读代码 + 读同配置重复）
"""
import json
import sys
from collections import Counter, defaultdict

RUNS = "bench_out/runs.jsonl"
FREEZE = "bench/evidence/l3_s302/suite_sub2.jsonl"


def load():
    with open(RUNS, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def ext(r):
    return r.get("kernel_external") or {}


def vecsum(t):
    return sum(1 for x in t["vec"] if x)


def section(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def main():
    rows = load()
    print(f"账本 `{RUNS}`：{len(rows)} 行")
    with open(FREEZE, encoding="utf-8") as f:
        tids = [json.loads(l)["id"] for l in f if l.strip()]
    print(f"冻结题集：{tids}")

    # ── C1 分布 ────────────────────────────────────────────────────────
    section("C1  calls 的分布（全账本 / 2 题子集 / 最近 12 次）")
    c_all = Counter(ext(r).get("calls") for r in rows)
    print("全账本：")
    for k, v in sorted(c_all.items(), key=lambda x: (x[0] is None, x[0])):
        print(f"    calls={str(k):>4s}  n={v:3d}  {v/len(rows):6.1%}")

    sub_idx = [i for i, r in enumerate(rows)
               if [t["id"] for t in r["report"]["per_task"]] == tids]
    sub = [rows[i] for i in sub_idx]
    c_sub = Counter(ext(r).get("calls") for r in sub)
    print(f"\n2 题子集（冻结题集逐字相同）：n={len(sub)}")
    for k, v in sorted(c_sub.items(), key=lambda x: (x[0] is None, x[0])):
        print(f"    calls={str(k):>4s}  n={v:3d}  {v/len(sub):6.1%}")

    print(f"\n最近 12 次（V216/V217 争议批）逐行：")
    print("    idx  calls  ok  tasks  samples  每题通过数")
    for i in range(max(0, len(rows) - 12), len(rows)):
        r = rows[i]
        e = ext(r)
        per = "  ".join(f"{t['id'][:3]}={vecsum(t)}/{len(t['vec'])}"
                        for t in r["report"]["per_task"])
        print(f"    {i:3d}  {str(e.get('calls')):>5s}  {e.get('ok')}   "
              f"{e.get('tasks')}     {e.get('samples')}      {per}")

    # ── C2 calls × 分数 ────────────────────────────────────────────────
    section("C2  calls × 每题通过数（2 题子集）")
    agg = defaultdict(lambda: defaultdict(list))
    for r in sub:
        c = ext(r).get("calls")
        for t in r["report"]["per_task"]:
            agg[t["id"]][c].append(vecsum(t))
    for tid in tids:
        print(f"\n== {tid}")
        print("    calls   n   通过数分布                均值")
        for c in sorted(agg[tid], key=lambda x: (x is None, x)):
            v = agg[tid][c]
            hist = dict(sorted(Counter(v).items()))
            print(f"    {str(c):>5s}  {len(v):3d}   {str(hist):<24s}  {sum(v)/len(v):.2f}")

    # ── C2b 关键交叉：t04 拿满分 是否等价于「走了外部」 ────────────────
    section("C2b  关键交叉：t04 拿 6/6 与 calls 的关系")
    t04 = "t04_merge_ranges"
    by_c = defaultdict(lambda: [0, 0])   # calls -> [满分数, 总数]
    for r in sub:
        c = ext(r).get("calls")
        for t in r["report"]["per_task"]:
            if t["id"] != t04:
                continue
            by_c[c][1] += 1
            if vecsum(t) == len(t["vec"]):
                by_c[c][0] += 1
    print("    calls   t04 满分率")
    for c in sorted(by_c, key=lambda x: (x is None, x)):
        hit, n = by_c[c]
        print(f"    {str(c):>5s}   {hit}/{n} = {hit/n:6.1%}")
    print("\n    读法：calls=0 仍有满分 ⇒ 内核自己能做对（不是唯一通路）；")
    print("          calls=2 全满分 ⇒ 「外部覆盖到 t04」几乎等价于满分。")

    # ── C3 calls 是不是装置常量 ────────────────────────────────────────
    section("C3  calls 是不是装置常量（同配置逐次重复）")
    print("    读代码（mw/core.py:1482）：`for ci, cand in enumerate(cands)`")
    print("    ⇒ 内核按 planner 给出的**顺序**逐个执行，不重排、不比较（§107 静态扫描：比较/选择 0 处）。")
    print("    ⇒ calls 由「planner 把 produces_answer 候选排在第几位」决定。")
    print()
    # 同题集、同 calls 的裸重复：state 长度与 calls 的关系（免费）
    print("    2 题子集内，calls 与 run_id 的先后（看是否成段）：")
    seg = [(i, ext(r).get("calls")) for i, r in zip(sub_idx, sub)]
    runs_of = []
    cur = None
    for i, c in seg:
        if cur and cur[0] == c:
            cur[1] += 1
            cur[3] = i          # ⚠️ 只更新尾，首必须保持第一次出现的那个
        else:
            if cur:
                runs_of.append(cur)
            cur = [c, 1, i, i]
    if cur:
        runs_of.append(cur)
    long_seg = [s for s in runs_of if s[1] >= 3]
    print(f"    连续同值的段：{len(runs_of)} 段，其中 ≥3 连的 {len(long_seg)} 段"
          f"（最长 {max((s[1] for s in runs_of), default=0)}）")
    for s in long_seg[:8]:
        print(f"      calls={s[0]} 连续 {s[1]:2d} 次  idx {s[2]}..{s[3]}")

    print()
    print("    ⇒ 若 calls 是装置常量，应当整段同值；实测在 0/1/2 之间跳。")
    print("      但「长段」说明它也**不是纯白噪声**：planner 的选择带有惯性（state 里)")
    print("      「上次做了什么」会把它推向同一侧）。这正好是块内容可能的入口。")


if __name__ == "__main__":
    sys.exit(main())
