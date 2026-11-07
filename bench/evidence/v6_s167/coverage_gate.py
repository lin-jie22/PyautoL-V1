"""量「闸有没有期望值可判」的覆盖面：逐题跑 `verify.derive()`，看派出了哪些判据。

⛔ 不调 LLM、不跑批。只是把 `mw/verify.py` 的既有派生规则在现有语料上跑一遍。
⭐ 关键区分：`example`（会**真的跑**题面示例）vs 其余四条（只能判"跑得起来/名字对"）。
   `example` 缺席 ⇒ 闸没有可比对的期望值 ⇒ 打回（`carried`）在结构上不可能发生。
"""
import glob
import json
import sys

sys.path.insert(0, ".")
from mw.bench import Suite  # noqa: E402
from mw.verify import derive  # noqa: E402

rows = []
for path in sorted(glob.glob("bench/tasks/*.jsonl") + glob.glob("bench/heldout/*.jsonl")):
    suite = Suite.load(path)
    for task in sorted(suite.tasks, key=lambda t: t.id):
        goal = task.goal if hasattr(task, "goal") else getattr(task, "prompt", "")
        checks = derive(goal, static=2, exec_=1)
        kinds = [c.kind for c in checks]
        rows.append((path, suite.sha()[:12], task.id, kinds,
                     len(getattr(task, "tests", []) or [])))

total = len(rows)
with_ex = sum(1 for r in rows if "example" in r[3])
print(f"语料：{total} 题 / "
      f"{len({r[0] for r in rows})} 个题集")
print(f"闸能判对错的题（派出了 `example` 检查）：{with_ex} / {total} = {with_ex / total:.4f}")
print()
head = f"{'题集':46s} {'suite_sha':12s} {'题':22s} {'示例':4s} {'评分器tests':6s} 闸派出的判据"
print(head)
print("-" * len(head))
for path, sha, tid, kinds, ntests in rows:
    flag = "有" if "example" in kinds else "—"
    print(f"{path:46s} {sha:12s} {tid:22s} {flag:4s} {ntests:6d} {'/'.join(kinds)}")

print()
by_suite = {}
for path, sha, tid, kinds, nt in rows:
    d = by_suite.setdefault((path, sha), [0, 0, 0])
    d[0] += 1
    d[1] += 1 if "example" in kinds else 0
    d[2] += 1 if nt else 0
print("按题集汇总：")
for (path, sha), (n, ex, nt) in sorted(by_suite.items()):
    print(f"  {sha}  {path:46s} 题={n:2d} 带示例={ex:2d} 评分器tests={nt:2d}")
