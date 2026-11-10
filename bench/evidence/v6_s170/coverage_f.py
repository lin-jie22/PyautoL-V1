"""V81 事后普查（⛔ 不是预注册判据）：F 的规则表能把闸的入口从 4/67 补到多少。

沿用 `v6_s167/coverage_gate.py` 的语料枚举（`bench/tasks/*.jsonl` + `bench/heldout/*.jsonl`），
对每题跑一遍 `extract_constraints.specs_from_prompt()`，看**有没有**至少一条约束可抽。

⚠️ 口径：这里只回答「抽得出几条」，**不**回答「抽得出的约束咬不咬得住」——
   后者只在 8 题上有 44 份现成错答案可测（见 `extract_constraints.out`）。
"""
import glob
import json
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "bench/evidence/v6_s170")
from mw.bench import Suite                      # noqa: E402
from mw.verify import derive                    # noqa: E402
from extract_constraints import specs_from_prompt, fn_of, DEF   # noqa: E402

rows = []
for path in sorted(glob.glob("bench/tasks/*.jsonl") + glob.glob("bench/heldout/*.jsonl")):
    suite = Suite.load(path)
    for task in sorted(suite.tasks, key=lambda t: t.id):
        goal = getattr(task, "goal", "") or getattr(task, "prompt", "")
        oracle = getattr(task, "oracle", "") or ""
        fn = fn_of(oracle) or (DEF.search(goal).group(1) if DEF.search(goal) else "")
        tests = list(getattr(task, "tests", []) or [])
        kinds = [c.kind for c in derive(goal, static=2, exec_=1)]
        specs = specs_from_prompt(task.id, fn, goal, tests)
        rows.append((path, suite.sha()[:12], task.id, kinds, [s["rule"] for s in specs]))

total = len(rows)
with_ex = sum(1 for r in rows if "example" in r[3])
with_f = sum(1 for r in rows if r[4])
both = sum(1 for r in rows if r[4] and "example" not in r[3])

print(f"语料：{total} 题 / {len({r[0] for r in rows})} 个题集")
print(f"闸现有入口（派出 `example`）：{with_ex} / {total} = {with_ex / total:.4f}")
print(f"F 能挂上约束的题：{with_f} / {total} = {with_f / total:.4f}")
print(f"其中闸原来没有入口的：{both} 题（F 的净增量）")
print(f"合并后入口：{(with_ex + both)} / {total} = {(with_ex + both) / total:.4f}")
print()
head = f"{'题集':46s} {'题':24s} {'闸':4s} {'F 派出的规则'}"
print(head)
print("-" * len(head))
for path, sha, tid, kinds, rules in rows:
    flag = "有" if "example" in kinds else "—"
    if rules or "example" in kinds:
        print(f"{path:46s} {tid:24s} {flag:4s} {','.join(rules) or '—'}")

print()
print("按题集：")
acc = {}
for path, sha, tid, kinds, rules in rows:
    d = acc.setdefault((path, sha), [0, 0, 0])
    d[0] += 1
    d[1] += 1 if "example" in kinds else 0
    d[2] += 1 if rules else 0
for (path, sha), (n, ex, f) in sorted(acc.items()):
    print(f"  {sha}  {path:46s} 题={n:2d} 闸有期望值={ex:2d} F可挂={f:2d}")

json.dump({"total": total, "with_example": with_ex, "with_f": with_f,
           "net_gain": both,
           "rows": [{"path": r[0], "sha": r[1], "id": r[2], "kinds": r[3], "rules": r[4]}
                    for r in rows]},
          open("bench/evidence/v6_s170/coverage_f.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
