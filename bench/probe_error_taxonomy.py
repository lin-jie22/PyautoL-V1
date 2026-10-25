#!/usr/bin/env python3
"""失分**分类器**：把一次运行丢掉的那些格子按「自检能不能报」分成两类（第八十二轮，§45）。

## 为什么需要它

§43 的结论是「这套题自检可及的天花板 = +13 格」，依据是**一题一题读答案**读出来的。
那个过程不可复用：换一套题、换一个模型，就得再读一遍，而"读一遍"不是量具。

本文件把那次判读做成**机械判据**，四类，按"当前自检能不能发现"排序：

| 类 | 判据（机械） | 当前自检能报吗 |
|---|---|---|
| `UNDEFINED` | `mw.verify.undefined_names(code)` 非空 | **能**（`verify_static=1/2`） |
| `CRASH` | 执行时抛非 `AssertionError` 的异常 | 不能（要执行才能看见） |
| `SEMANTIC` | 执行时抛 `AssertionError`（算得出，但不对） | 不能（按设计不判对错） |
| `SYNTAX` | 编译不过 | 能（`可编译` 那条判据） |

⛔ 这四类是**枚举全部**的：一格要么过、要么不进这四类之一，没有"其他"。所以
   「可动作 / 不可动作」的划分是一个**覆盖**，不是抽样。这正是不想再用"人工读答案"
   的理由 —— 人工读会漏，而漏掉的那几格恰好会支持结论。

## 边界（明写）

1. 它**不判**「改进」也不判「变好」。它只回答"这一次运行丢的格子长什么样"。
2. `CRASH` 一类里包含了"补上就会好"与"补上还会露出下一个错"两种情况，
   本文件**不区分** —— 区分它要靠另一次重写实验（§43.2 做过一次，结论是 +1 格）。
3. 进程内 `exec`。这些答案**已经**由打分器在隔离子进程里完整跑过一遍
   （能跑完才出分），这里只是把同一份代码再跑一次拿异常类型；
   仍然不是沙箱，所以**只用于分析，不许接进打分链路**。

用法：

    python3 bench/probe_error_taxonomy.py                 # 最近一次运行
    python3 bench/probe_error_taxonomy.py --run-id 69666@1792912166
    python3 bench/probe_error_taxonomy.py --self-check    # 反证：五类样本必须各自命中
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.scorers.code_exec import extract_code          # noqa: E402
from mw import verify as VFY                              # noqa: E402

PASS, UNDEF, CRASH, SEMANTIC, SYNTAX, NOANS = (
    "PASS", "UNDEFINED", "CRASH", "SEMANTIC", "SYNTAX", "NOANSWER")

#: 自检**看得见**的类（其余一律看不见）。
ACTIONABLE = {UNDEF, SYNTAX}


def classify(code: str, tests: list[str], fn: str) -> tuple[str, str]:
    """返回 (类别, 细节)。⛔ 顺序有感：先编译 → 再静态 → 再执行。"""
    if not code.strip():
        return NOANS, "没有代码"
    try:
        compile(code, "<answer>", "exec")
    except SyntaxError as e:
        return SYNTAX, f"SyntaxError: {e.msg}"

    und = VFY.undefined_names(code)
    if und:
        return UNDEF, "未定义名 " + ", ".join(f"{n}@{s or '<module>'}" for s, n in list(und)[:3])

    ns: dict = {}
    try:
        exec(compile(code, "<answer>", "exec"), ns)        # noqa: S102
    except Exception as e:                                 # noqa: BLE001
        return CRASH, f"{type(e).__name__}（在导入/定义阶段）"
    if fn not in ns:
        return CRASH, f"{fn} 没有定义出来"

    for t in tests:
        try:
            exec(compile(t, "<test>", "exec"), ns)         # noqa: S102
        except AssertionError:
            return SEMANTIC, "断言不符（算得出，但不对）"
        except Exception as e:                             # noqa: BLE001
            return CRASH, f"{type(e).__name__}: {str(e)[:60]}"
    return PASS, ""


def _answers(path: Path, run_id: str | None) -> tuple[str, list[dict]]:
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not rows:
        raise SystemExit(f"❌ 没有答案记录 {path}")
    rid = run_id or str(rows[-1].get("run_id") or "")
    # ⚠️ 同一个 run_id 下还有**非答案**记录（`kind != answer`，没有 task_id）——
    #    不过滤就会在排序时 KeyError（第一版实测就这么炸的）。
    keep = [r for r in rows if str(r.get("run_id")) == rid
            and r.get("kind") == "answer" and r.get("task_id")]
    return rid, keep


def _run_rec(path: Path, run_id: str) -> dict:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if str(rec.get("run_id")) == run_id:
            return rec
    raise SystemExit(f"❌ runs.jsonl 里没有 run_id={run_id}")


# ===========================================================================
# 检测器自带反证（纪律 ⑩）：五类各一个样本，必须各自命中
# ===========================================================================
_SELF = [
    (UNDEF, "```python\ndef f(rows):\n    return [re.sub(r'\\s+', ' ', r) for r in rows]\n```",
     ["assert f(['a  b']) == ['a b']"], "f"),
    (CRASH, "```python\ndef f(rows):\n    return {}.values().sort()\n```",
     ["assert f(['a']) == []"], "f"),
    (SEMANTIC, "```python\ndef f(rows):\n    return [r.lower() for r in rows]\n```",
     ["assert f(['A']) == ['A']"], "f"),
    (SYNTAX, "```python\ndef f(rows)\n    return rows\n```", ["assert f([]) == []"], "f"),
    (NOANS, "", ["assert f([]) == []"], "f"),
    (PASS, "```python\ndef f(rows):\n    return [r.strip() for r in rows]\n```",
     ["assert f([' a ']) == ['a']"], "f"),
]


def self_check() -> int:
    bad = 0
    print("反证：五类 + 通过，各一个样本")
    for want, ans, tests, fn in _SELF:
        got, why = classify(extract_code(ans), tests, fn)
        ok = got == want
        bad += int(not ok)
        print(f"  {'✅' if ok else '❌'} 期望 {want:9s} 实得 {got:9s} {why}")
    print("✅ 分类器六个分支全部被样本打中" if not bad else f"❌ {bad} 个分支没被命中")
    return 0 if not bad else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="失分分类器（§45）")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--answers", default="bench_out/answers.jsonl")
    ap.add_argument("--runs", default="bench_out/runs.jsonl")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()

    rid, rows = _answers(Path(a.answers), a.run_id or None)
    rec = _run_rec(Path(a.runs), rid)
    per = {str(t["id"]): float(t["score"]) for t in rec["report"]["per_task"]}

    print("=" * 78)
    print(f"失分分类  run_id={rid}  suite_sha={rec['caliber']['suite_sha'][:12]}  "
          f"mean={rec['measurement']['mean']:.4f}")
    print("=" * 78)
    print(f"  {'题':6s} {'得分':>6s} {'丢格':>4s}  类别（机械判据）")
    tally: dict[str, int] = {}
    total_lost = 0
    for r in sorted(rows, key=lambda x: str(x["task_id"])):
        tid = str(r["task_id"])
        code = extract_code(str(r.get("answer") or ""))
        # 测试断言不在答案记录里 ⇒ 从题集现读（⛔ 不猜：题集是唯一来源）
        tests = _tests_of(rec, tid)
        cat, why = classify(code, tests, _fn_of(rec, tid))
        loss = round((1.0 - per.get(tid, 0.0)) * len(tests))
        total_lost += loss
        if loss:
            tally[cat] = tally.get(cat, 0) + loss
        print(f"  {tid:6s} {per.get(tid, 0.0):6.4f} {loss:4d}  "
              f"{'-' if not loss else cat + '：' + why}")
    print()
    print(f"  丢格合计 {total_lost}")
    for k, v in sorted(tally.items(), key=lambda kv: -kv[1]):
        mark = "✅ 自检可报" if k in ACTIONABLE else "⛔ 自检看不见"
        print(f"    {k:9s} {v:3d} 格   {mark}")
    act = sum(v for k, v in tally.items() if k in ACTIONABLE)
    print(f"  ⇒ 可动作 {act}/{total_lost} 格 "
          f"（{act / total_lost:.1%}）" if total_lost else "  ⇒ 没有失分")
    return 0


_SUITE_CACHE: dict[str, list[dict]] = {}


def _suite_of(rec: dict) -> list[dict]:
    """这次运行跑的是哪份题集：按 `caliber.suite_sha` 在 `bench/specgen` 里找。

    ⛔ 不拿 `n` 或题名去猜：`suite_sha` 是**唯一**能对上"这次量的是哪把尺子"的字段
       （§37 的规矩）。找不到就报错 —— 那时候任何分类都是在给别的题对号。
    """
    want = str(rec["caliber"]["suite_sha"])
    if want in _SUITE_CACHE:
        return _SUITE_CACHE[want]
    from mw.bench import Suite                                    # noqa: PLC0415
    for p in sorted((ROOT / "bench" / "specgen").glob("*.jsonl")):
        if Suite.load(p).sha() == want:
            rows = [json.loads(x) for x in
                    p.read_text(encoding="utf-8").splitlines() if x.strip()]
            _SUITE_CACHE[want] = rows
            return rows
    raise SystemExit(f"❌ 找不到 suite_sha={want[:12]} 对应的题集文件")


def _tests_of(rec: dict, tid: str) -> list[str]:
    for row in _suite_of(rec):
        if str(row.get("id")) == tid:
            return list(row.get("tests") or [])
    raise SystemExit(f"❌ 题集里没有 {tid}")


def _fn_of(rec: dict, tid: str) -> str:
    for row in _suite_of(rec):
        if str(row.get("id")) == tid:
            return str((row.get("meta") or {}).get("spec", {}).get("fn") or f"norm_{tid}")
    return f"norm_{tid}"


if __name__ == "__main__":
    raise SystemExit(main())
