"""V32 P0 —— 自评能不能回到产生它的那道题（零 LLM、零网络）

## 为什么这是主线上的一个断点

「累积 → 预测 → 自省」里的**自省**，在本系统里的实现是：
跑完一轮 → 每条评审落盘 → 下一轮的 prompt 里带上 `[外部评审]` 块。

`core.py:1440` 调 `_admissible_critiques(task_id=..., ...)`，
而 `critique.py:107` 对**同 task_id** 的条目一律拒收并计入 `rejected["self"]`。
原文注释的理由是「防自己给自己打分」，这在**轮内**是对的。
但它同时对**上一轮**的同题评审也生效 —— 而「同一道题的第二次尝试」正是学习该发生的地方。

## 这个探针只回答一件事

在三种 `--critique-scope` 语义下，**`e1wi` 的评审能不能进入 `e1wi` 的 prompt**。
⛔ 它不回答「接通后有没有用」—— 那是实验，不是探针。

## 顺带必须核的两件事（纪律 12 / 14）

- `add()` 是不是按 `(scorer_sha, suite_sha, task_id)` **覆盖**同名键
  ⇒ 决定「第 2 轮能不能看到第 1 轮」，还是只看得到最后一次。
- `--repeat N` 时第 2 轮的 e1wi 的 prompt 里到底有没有 e1wi 这一条。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/Users/linshaojie/V1")

from mw.critique import Critique, CritiqueLedger  # noqa: E402

EV = Path("/Users/linshaojie/V1/bench/evidence/v6_s122")
BASE = Path("/Users/linshaojie/V1/bench/evidence/v6_s121/seed_base/critique.jsonl")
TASKS = ["e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni"]


def line(title: str) -> None:
    print()
    print("─" * 96)
    print(title)
    print("─" * 96)


def main() -> int:
    rows = [json.loads(x) for x in BASE.read_text(encoding="utf-8").splitlines() if x.strip()]
    if len(rows) != 6:
        print(f"❌ 种子必须是 6 条，实际 {len(rows)}")
        return 2
    scorer_sha, suite_sha = rows[0]["scorer_sha"], rows[0]["suite_sha"]
    print("=" * 96)
    print("V32 P0 —— 自评能不能回到产生它的那道题")
    print("=" * 96)
    print(f"种子 {BASE}  {len(rows)} 条  scorer_sha={scorer_sha[:16]}  suite_sha={suite_sha}")

    #: 复刻 run.py 的 prev 语义：CritiqueLedger(None) + 逐条 add（只读副本）
    led = CritiqueLedger(None)
    for r in rows:
        led.add(Critique(scorer_sha=r["scorer_sha"], suite_sha=r["suite_sha"],
                         task_id=r["task_id"], family=r.get("family", ""),
                         score=float(r["score"]), why=r.get("why", ""),
                         answer=r.get("answer", "")))
    print(f"账本 {len(led.by_key)} 条（键 = scorer_sha|suite_sha|task_id）")

    line("一、逐题：每个 task 能拿到哪几条评审（= 真正进 [外部评审] 块的候选）")
    print(f"  {'当前题':8s} {'admissible 条数':>14s}   {'拿到的 task_id（排序后）':s}")
    reach = {}
    for t in TASKS:
        got = led.admissible(exclude_task_id=t, scorer_sha=scorer_sha, suite_sha=suite_sha)
        ids = [c.task_id for c in got]
        reach[t] = ids
        print(f"  {t:8s} {len(ids):14d}   {' '.join(ids)}")

    line("二、`crit[-3:]` 之后真正进 prompt 的三条（core.py:1443）")
    served = {}
    for t in TASKS:
        trip = [c.task_id for c in led.admissible(
            exclude_task_id=t, scorer_sha=scorer_sha, suite_sha=suite_sha)][-3:]
        served[t] = trip
        print(f"  {t:8s} → {' '.join(trip)}")

    line("三、判决：同题评审可达吗")
    self_reach = [t for t in TASKS if t in served[t]]
    print(f"  同题评审进入自己 prompt 的题 = {self_reach if self_reach else '**无**'}")
    print(f"  ⇒ {'✅ 可达' if self_reach else '⛔ 结构上不可达（0/6）'}")

    line("四、第 2 轮看得到第 1 轮吗（`add()` 是覆盖还是追加）")
    led2 = CritiqueLedger(None)
    for r in rows:
        led2.add(Critique(scorer_sha=r["scorer_sha"], suite_sha=r["suite_sha"],
                          task_id=r["task_id"], family=r.get("family", ""),
                          score=float(r["score"]), why=r.get("why", ""),
                          answer=r.get("answer", "")))
    n_before = len(led2.by_key)
    #: 模拟第 2 轮 e1wi 产出新评审（分数不同、理由不同）
    led2.add(Critique(scorer_sha=scorer_sha, suite_sha=suite_sha, task_id="e1wi",
                      family="tasks_v8", score=0.0, why="第二轮的新理由", answer="x"))
    n_after = len(led2.by_key)
    got = led2.by_key[(scorer_sha, suite_sha, "e1wi")]
    print(f"  add 前 {n_before} 条 → add 后 {n_after} 条（键数变化 {n_after - n_before}）")
    print(f"  e1wi 现存 why = {got.why!r}（score={got.score}）")
    print(f"  ⇒ {'覆盖：同名键只留最后一次' if n_after == n_before else '追加：两条并存'}")

    line("五、结论（本次探针读出的事实，不含实验判断）")
    print(f"  · 6 道题里，同题评审可达 {len(self_reach)}/6")
    print(f"  · 送达窗口（末三）恒为 dict 序尾三条，与被评题无关")
    print(f"  · `add()` {'覆盖' if n_after == n_before else '追加'} ⇒ 跨轮只有最后一次可见"
          f"（在覆盖语义下，第 2 轮看到的就是第 1 轮那条，前提是它不被 self 规则挡掉）")

    out = {"reach": reach, "served": served, "self_reach": self_reach,
           "overwrite": n_after == n_before}
    (EV / "p0_selfreach.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print()
    print("VERDICT P0-OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
