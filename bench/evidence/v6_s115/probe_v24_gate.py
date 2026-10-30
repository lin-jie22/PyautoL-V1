"""V24 前提取证（零模型）：自检闸在 V23 的交付件上到底判了什么？

回答三个问题，全部从**已落盘的交付件**读：

  ① 闸在**拿 16 分**的交付上也会打回吗（假阳性）？
  ② 打回的理由在 `verify_static=1`（报名字）下具体是什么？
  ③ `verify_static=2`（盲报）与 `1` 的**触发集**是否相同（契约 AO 锁的性质）？

⛔ 不读打分器、不读参考答案。判据来自题面（`vfy.derive(goal, ...)`）。
⚠️ 分数只用来**分组**（"判据对不对"不能拿分数当输入，但"判据在满分解上响不响"必须看分数）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import mw.verify as vfy  # noqa: E402

TASKS = ("e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni")


def rows(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def main(argv: list[str]) -> int:
    work = Path(argv[1] if len(argv) > 1 else "/tmp/v23")
    goals = {str(t["id"]): str(t["prompt"])
             for t in rows(Path(__file__).resolve().parents[3]
                           / "bench" / "specgen" / "tasks_v8.jsonl")}

    print("=" * 78)
    print("一、题面里有没有 `>>> TODO` 段（`keep` 这类判据的前提）")
    for tid in TASKS:
        g = goals[tid]
        print(f"  {tid:<6} prompt={len(g):5d}字  BEGIN_TODO={'>>> BEGIN TODO' in g}"
              f"  END_TODO={'>>> END TODO' in g}  fenced={'```' in g}")

    print()
    print("=" * 78)
    print("二、逐交付件：分数 vs 三档判据")
    print(f"  {'arm':<5}{'task':<6}{'分':>4}  {'s=0':<6}{'s=1':<6}{'s=2':<6}  s=1 的首条说明")
    stat: dict[str, dict[str, int]] = {}
    for arm in ("a0", "a1", "a2"):
        for tid in TASKS:
            ans = ""
            score = None
            for r in (1, 2, 3):
                for x in rows(work / f"bench_{arm}_r{r}" / "answers.jsonl"):
                    if str(x.get("task_id")) == tid:
                        ans = str(x.get("answer") or "")
                for t in (rows(work / f"bench_{arm}_r{r}" / "runs.jsonl") or [{}])[-1].get(
                        "report", {}).get("per_task", []):
                    if str(t.get("id")) == tid:
                        score = float(t.get("score") or 0.0) * 16
            if not ans:
                continue
            verdicts = {}
            for s in (0, 1, 2):
                v = vfy.run(ans, vfy.derive(goals[tid], s))
                verdicts[s] = v
            first = next((x for x in verdicts[1].report.split("；") if "未定义" in x), "")
            print(f"  {arm:<5}{tid:<6}{score:>4.0f}  "
                  f"{('过' if verdicts[0].ok else '打回'):<6}"
                  f"{('过' if verdicts[1].ok else '打回'):<6}"
                  f"{('过' if verdicts[2].ok else '打回'):<6}  {first[:64]}")
            k = f"score{int(score)}"
            stat.setdefault(k, {"n": 0, "red1": 0, "red2": 0})
            stat[k]["n"] += 1
            stat[k]["red1"] += 0 if verdicts[1].ok else 1
            stat[k]["red2"] += 0 if verdicts[2].ok else 1

    print()
    print("=" * 78)
    print("三、按分数分组（假阳性 = 满分解被闸打回）")
    for k in sorted(stat):
        s = stat[k]
        print(f"  {k}: n={s['n']:2d}  s=1 打回 {s['red1']:2d}  s=2 打回 {s['red2']:2d}")

    print()
    print("=" * 78)
    print("四、两档的**触发集**是否相同（`ok` 相同；判据**名字**刻意见不同）")
    print("   ⚠️ 名字必须不同：`未过：未定义名` 这五个字本身就是 mode 2 要去掉的载荷。")
    diff = 0
    for arm in ("a0", "a1", "a2"):
        for r in (1, 2, 3):
            for tid in TASKS:
                ans = ""
                for x in rows(work / f"bench_{arm}_r{r}" / "answers.jsonl"):
                    if str(x.get("task_id")) == tid:
                        ans = str(x.get("answer") or "")
                if not ans:
                    continue
                a = vfy.run(ans, vfy.derive(goals[tid], 1))
                b = vfy.run(ans, vfy.derive(goals[tid], 2))
                if a.ok != b.ok:
                    diff += 1
                    print(f"  ⚠️ {arm}_r{r}/{tid}: s=1 ok={a.ok} ≠ s=2 ok={b.ok}")
    print(f"  ⇒ 触发集不同 {diff} 处（应为 0）")

    print()
    print("=" * 78)
    print("五、打回报告原文（机制取证：mode 2 里**一个标识符都没有**）")
    shown = set()
    for arm in ("a0", "a1", "a2"):
        for tid in TASKS:
            ans = ""
            for x in rows(work / f"bench_{arm}_r1" / "answers.jsonl"):
                if str(x.get("task_id")) == tid:
                    ans = str(x.get("answer") or "")
            if not ans or tid in shown:
                continue
            a = vfy.run(ans, vfy.derive(goals[tid], 1))
            b = vfy.run(ans, vfy.derive(goals[tid], 2))
            if a.ok:
                continue
            shown.add(tid)
            print(f"  -- {tid}（{arm}_r1）")
            print(f"     s=1: {a.report}")
            print(f"     s=2: {b.report}")
            idents = [w for w in ("_strip_tags", "_split", "_dump", "re") if w in b.report]
            print(f"     s=2 报告里的标识符：{idents or '无'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
