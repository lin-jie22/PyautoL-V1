"""把一次 bench 跑的失分，按「自检过没过 × 一跑就炸/就是错」分成三个池子。

这是 `bench/evidence/loss_s58/audit_loss_pool.py` 的**通用版**：那一份写死了
第九十轮的 60 份答案（用 `FROZEN` 字典当外部真值），只能用一次。这一份从
`runs.jsonl` 的 `report.per_task` 直接读落盘分数，所以对任何 `--suite` 都能跑。

用法：

    python3 bench/audit_loss_pool.py \
        --suite  bench/specgen/tasks_v3.jsonl \
        --bench  /tmp/v1_s92_bench_v3_7b \
        --log    /tmp/v1_s92_run_v3_7b.log

自带三条反证（任一条不成立 ⇒ 整张表作废）：

  V1 逐份离线复算的分数，必须与 `report.per_task` 里落盘的 `score` **逐格相同**。
     不一致 ⇒ 提取代码块 / 执行环境与当时不同 ⇒ 表作废。
  V2 反例冻结：题集里第一题的 `oracle` 必须满分，被削过的桩必须不满分。
  V3 行序假设：`answers.jsonl` 必须是「每轮按题集顺序走一遍」的 rep-major 排列。
     不成立就没法把答案和分数对上 —— 宁可不给读数，也不许瞎配。

⛔ 只读，不写任何文件。
"""

from __future__ import annotations

import argparse
import json
import re
import sys

RT = frozenset({
    "NameError", "AttributeError", "TypeError", "IndexError", "KeyError",
    "ValueError", "ZeroDivisionError", "RecursionError", "UnboundLocalError",
    "SyntaxError", "ImportError", "ModuleNotFoundError", "StopIteration",
    "FloatingPointError", "OverflowError",
})


def load_suite(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


def code_of(answer: str) -> str:
    m = re.search(r"```(?:python)?\n(.*?)```", answer or "", re.S)
    return m.group(1) if m else (answer or "")


def score_of(tests: list[str], code: str) -> int:
    if not code.strip():
        return 0
    ns: dict = {}
    try:
        exec(compile(code, "<audit>", "exec"), ns)  # noqa: S102
    except Exception:  # noqa: BLE001
        return 0
    hit = 0
    for t in tests:
        try:
            exec(compile(t, "<audit-t>", "exec"), ns)  # noqa: S102
            hit += 1
        except Exception:  # noqa: BLE001
            pass
    return hit


def first_failure(tests: list[str], code: str) -> tuple[str, str]:
    if not code.strip():
        return "EMPTY", ""
    for t in tests:
        ns: dict = {}
        try:
            exec(compile(code, "<audit>", "exec"), ns)  # noqa: S102
            exec(compile(t, "<audit-t>", "exec"), ns)  # noqa: S102
        except AssertionError:
            return "ASSERT", ""
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            return ("RT" if name in RT else "OTHER"), f"{name}: {exc}"
    return "-", ""


def per_task(bench_dir: str) -> list[dict]:
    with open(f"{bench_dir}/runs.jsonl", encoding="utf-8") as fh:
        row = json.loads(fh.readline())
    return row["report"]["per_task"]


def answers(bench_dir: str) -> list[dict]:
    with open(f"{bench_dir}/answers.jsonl", encoding="utf-8") as fh:
        rows = [json.loads(x) for x in fh if x.strip()]
    return [r for r in rows if r.get("task_id")]


def fn_of(task: dict) -> str:
    m = re.search(r"写一个函数 (\w+)\(", task.get("prompt", ""))
    return m.group(1) if m else ""


def gates(log_path: str) -> list[tuple[str, int]]:
    """从汇总行取 (函数名, 自检过了几次)。顺序 = 运行顺序。

    ⚠️ 返回的是**函数名**不是题号：v2 的函数叫 `norm_sg01`、题号是 `sg01`，
       v3 的函数叫 `f_split_ws`、题号是 `ex01` —— 两套都不能假设相等。
       对不上就在调用处按题集里的函数名映射。
    """
    out: list[tuple[str, int]] = []
    cur = None
    for line in open(log_path, encoding="utf-8"):
        m = re.search(r"任务开始: 写一个函数 (\w+)\(", line)
        if m:
            cur = m.group(1)
        if "■ " in line and cur:
            v = re.search(r"校验=(\d+)跑/(\d+)过", line)
            out.append((cur, int(v.group(2)) if v else 0))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", required=True)
    ap.add_argument("--bench", required=True)
    ap.add_argument("--log", required=True)
    a = ap.parse_args()

    suite = load_suite(a.suite)
    by_id = {t["id"]: t for t in suite}
    ids = [t["id"] for t in suite]
    n_tasks = len(ids)
    pt = per_task(a.bench)
    ans = answers(a.bench)
    gate = gates(a.log)
    rec = {(r["id"], int(r["rep"])): r for r in pt}

    print("=" * 76)
    print("  失分三池：自检过没过 × 一跑就炸 / 就是错")
    print(f"  题集 {a.suite}    臂输出 {a.bench}")
    print("=" * 76)

    # ── V2 反例冻结 ───────────────────────────────────────────────────
    t0 = suite[0]
    f0 = fn_of(t0)
    good = score_of(t0["tests"], code_of(t0["oracle"]))
    mangled = score_of(t0["tests"], f"def {f0}(*a, **k):\n    return None\n")
    print("\n[反证 V2]")
    print(f"  {t0['id']} oracle 满分？ {good}/{len(t0['tests'])}  "
          f"{'✅' if good == len(t0['tests']) else '❌ 打分器坏了'}")
    print(f"  空壳实现（同签名返回 None）必须不满分？ {mangled}/{len(t0['tests'])}  "
          f"{'✅' if mangled < len(t0['tests']) else '❌ 打分器恒真'}")

    # ── V3 行序假设 ───────────────────────────────────────────────────
    reps = max((int(r["rep"]) for r in pt), default=0)
    expect_order = ids * reps
    got_order = [r["task_id"] for r in ans]
    v3ok = got_order == expect_order
    print("\n[反证 V3] 答案行序 == 题集顺序 × rep 数？ "
          f"{'✅' if v3ok else '❌ 不能把答案和分数对上'}")
    if not v3ok:
        print(f"  期望前 12 项 {expect_order[:12]}")
        print(f"  实际前 12 项 {got_order[:12]}")
        return 1

    # ── 逐份复算 + 分池 ───────────────────────────────────────────────
    pools = {"无闸": 0, "假绿-崩": 0, "逻辑错": 0}
    mismatch = 0
    lines: list[str] = []
    total_cells = 0
    total_loss = 0
    for i, row in enumerate(ans):
        tid = row["task_id"]
        rep = i // n_tasks + 1
        tests = by_id[tid]["tests"]
        n = len(tests)
        code = code_of(row.get("answer", ""))
        got = score_of(tests, code)
        r = rec.get((tid, rep))
        if r is None:
            mismatch += 1
            lines.append(f"  ⚠️ {tid} rep{rep} 落盘缺这一格")
            continue
        exp = round(float(r["score"]) * n)
        if got != exp:
            mismatch += 1
            lines.append(f"  ⚠️ {tid} rep{rep} 复算 {got} ≠ 落盘 {exp}")
            continue
        total_cells += n
        loss = n - got
        total_loss += loss
        if loss <= 0:
            continue
        g = gate[i][1] if i < len(gate) else 0
        # 对齐闸：日志里的函数名必须与本格的题对得上，否则宁可不给读数
        if i >= len(gate) or gate[i][0] != fn_of(by_id[tid]):
            mismatch += 1
            lines.append(f"  ⚠️ {tid} rep{rep} 日志对不上（第 {i} 行 "
                         f"{gate[i][0] if i < len(gate) else '缺失'}）")
            continue
        passed = g == 1
        kind, msg = first_failure(tests, code)
        pool = ("逻辑错" if kind == "ASSERT" else "假绿-崩") if passed else "无闸"
        pools[pool] += loss
        lines.append(f"  {tid:6s} rep{rep} 闸={'过 ' if passed else '未过'} "
                     f"失{loss:2d}/{n} 池={pool:8s} {msg[:46]}")

    print("\n[反证 V1] 逐份复算 vs 落盘分数")
    print(f"  {'✅ 全部逐格相同' if mismatch == 0 else f'❌ {mismatch} 处不一致'}"
          f"（复算覆盖 {total_cells} 格）")

    print("\n[逐份明细]")
    for x in lines:
        print(x)

    print("\n[三池]")
    tot = sum(pools.values())
    if tot:
        for k, v in pools.items():
            print(f"  {k:8s} {v:4d} 格  {v / tot * 100:5.1f}%")
        print(f"  {'合计':8s} {tot:4d} 格（总格数 {total_cells}，均分 "
              f"{(total_cells - total_loss) / total_cells:.4f}）")

    if mismatch:
        print("\n❌ 复算与落盘不一致 ⇒ 表作废")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
