#!/usr/bin/env python3
"""V72 分析器 —— 自省循环的**传感器覆盖率**：内核能不能判「答案错了」。

三部分，全部离线、零模型调用：

  A. 判据来源：`mw.verify.derive()` 在每个题集/每道题上能派生出哪几种判据。
     ⇒ 只有 `example`（题面示例）能判"对不对"，其余全是"是不是代码"。
  B. 跑级 2×2：有题分数 <1.0 而 `kernel_verify.failed == 0` ⇒ **错而不知**（假阴性）。
     ⛔ 缺 `kernel_verify` 块的旧跑单列「缺席」，⛔ 不许并进 0（纪律 8）。
  C. 载荷真值：记忆库里 `reward` 记录的 failure 带不带 `输入 → 期望`；
     载荷原文里出现 `assert` / `==` 的段数。

口径全在字段名里：
  task_rows / task_ids / tasks_with_exec / runs_with_verify / fn_runs / payload_blocks / records_with_truth

用法：
  python3 bench/evidence/v6_s161/analyze_v72.py
"""
from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from mw.verify import derive, example_asserts  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
MEM_DB = os.path.join(ROOT, "bench/evidence/v6_s159/final_memory.db")
STATE_DUMP = os.path.join(ROOT, "bench/evidence/v6_s159/state_dump_A5.jsonl")


# ---------------------------------------------------------------- A. 判据来源
def part_a() -> dict:
    files = sorted(glob.glob(os.path.join(ROOT, "bench/heldout/*.jsonl"))
                   + glob.glob(os.path.join(ROOT, "bench/tasks/*.jsonl")))
    rows = []
    for p in files:
        for line in open(p, encoding="utf-8"):
            if line.strip():
                rows.append((os.path.basename(p), json.loads(line)))
    seen: dict[str, dict] = {}
    for fname, t in rows:
        tid = t["id"]
        ex = example_asserts(t["prompt"])
        kinds = [c.kind for c in derive(t["prompt"], static=2, exec_=1)]
        rec = seen.setdefault(tid, {"id": tid, "files": [], "ex": len(ex), "kinds": kinds})
        rec["files"].append(fname)
    with_ex = [r for r in seen.values() if r["ex"] > 0]
    print("=" * 96)
    print("A. 判据来源（`derive()` 实际派生出什么）")
    print("=" * 96)
    print(f"{'题 id':26s} {'示例断言':>6s} {'有语义判据':>8s}  判据种类")
    for r in sorted(seen.values(), key=lambda x: (-x["ex"], x["id"])):
        sem = "YES" if r["ex"] > 0 else "no"
        print(f"{r['id']:26s} {r['ex']:>6d} {sem:>8s}  {r['kinds']}")
    print()
    print(f"task_rows（题集文件里的行，含重复 id） = {len(rows)}")
    print(f"task_ids（去重后的题）              = {len(seen)}")
    print(f"tasks_with_exec（有可执行判据的题）   = {len(with_ex)}  -> "
          f"{[r['id'] for r in with_ex]}")
    print(f"覆盖率：按行 {len(with_ex)}/{len(rows)}，按题 {len(with_ex)}/{len(seen)}")
    dup = {k: v["files"] for k, v in seen.items() if len(v["files"]) > 1}
    print(f"重复出现在多个题集文件里的 id：{len(dup)} 个")
    return {"task_rows": len(rows), "task_ids": len(seen),
            "tasks_with_exec": len(with_ex)}


# ------------------------------------------------------------ B. 跑级 2×2
def part_b() -> dict:
    paths = (sorted(glob.glob(os.path.join(ROOT, "bench/evidence/**/runs*.jsonl"), recursive=True))
             + sorted(glob.glob(os.path.join(ROOT, "bench/evidence/**/bench/runs.jsonl"), recursive=True)))
    have, absent = [], 0
    for p in paths:
        for line in open(p, encoding="utf-8", errors="replace"):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            pt = ((r.get("report") or {}).get("per_task") or [])
            if not pt:
                continue
            vf = r.get("kernel_verify")
            if not isinstance(vf, dict):
                absent += 1           # ⛔ 缺席 ≠ 0
                continue
            below = sum(1 for x in pt if x.get("score") is not None and float(x["score"]) < 1.0)
            have.append(dict(
                dir=os.path.basename(os.path.dirname(p)), f=os.path.basename(p),
                n=len(pt), below=below,
                failed=vf.get("failed") or 0, exec_runs=vf.get("runs") or 0,
                ex_runs=vf.get("example_runs") or 0, ex_failed=vf.get("example_failed") or 0,
                ids=[x["id"] for x in pt]))
    fn = [r for r in have if r["below"] > 0 and r["failed"] == 0]
    hit = [r for r in have if r["below"] > 0 and r["failed"] > 0]
    clean = [r for r in have if r["below"] == 0]
    print()
    print("=" * 96)
    print("B. 跑级 2×2 —— 有题 <1.0 时，内核自检报不报错")
    print("=" * 96)
    print(f"runs_total（有 per_task 的行）        = {len(have) + absent}")
    print(f"runs_with_verify（有 kernel_verify）  = {len(have)}")
    print(f"runs_without_verify（旧格式，缺席）    = {absent}   ⛔ 不并入 0")
    print()
    print(f"  fn_runs    （错而不知：below>0 且 failed==0） = {len(fn):>3d}")
    print(f"  caught_runs（自检报过错：below>0 且 failed>0） = {len(hit):>3d}")
    print(f"  clean_runs （全部满分：below==0）              = {len(clean):>3d}")
    print()
    print(f"{'目录':18s} {'文件':24s} {'题数':>4s} {'<1.0':>4s} {'failed':>6s} {'示例跑':>6s}")
    for r in sorted(hit, key=lambda x: x["dir"]):
        print(f"{r['dir']:18s} {r['f']:24s} {r['n']:>4d} {r['below']:>4d} "
              f"{r['failed']:>6d} {r['ex_runs']:>6d}   ← 自检报过错")
    print()
    print("V68~V71 各跑的 example_runs（>0 才算真跑过一条可执行判据）：")
    for r in sorted(have, key=lambda x: (x["dir"], x["f"])):
        if r["dir"].startswith("v6_s15"):
            print(f"  {r['dir']:10s} {r['f']:22s} example_runs={r['ex_runs']} "
                  f"example_failed={r['ex_failed']} failed={r['failed']} below={r['below']}/{r['n']}")
    return {"runs_with_verify": len(have), "runs_without_verify": absent,
            "fn_runs": len(fn), "caught_runs": len(hit)}


# ------------------------------------------------------- C. 载荷里的真值
def part_c() -> dict:
    print()
    print("=" * 96)
    print("C. 真值在库里，但进不了载荷")
    print("=" * 96)
    c = sqlite3.connect(f"file:{MEM_DB}?mode=ro", uri=True)
    tot = truth = 0
    sample = None
    for (m,) in c.execute("select meta from episodes where kind='reward'"):
        d = json.loads(m)
        f = d.get("failure") or ""
        tot += 1
        if re.search(r"assert\s.*?==", f):
            truth += 1
            sample = sample or f
    print(f"records_reward（库内 reward 条）              = {tot}")
    print(f"records_with_truth（failure 含 `assert … ==`）= {truth}")
    print("样例：", (sample or "")[:120])

    blocks = hits = 0
    if os.path.exists(STATE_DUMP):
        seen = set()
        for line in open(STATE_DUMP, encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            g = r.get("guidance") or ""
            if not g or r.get("task") in seen:
                continue
            seen.add(r.get("task"))
            blocks += 1
            if "assert" in g or "==" in g:
                hits += 1
            print(f"  [{r['task']:20s}] len={len(g):3d} 含assert={'assert' in g} "
                  f"含'==={'==' in g}")
    print(f"payload_blocks（载荷段）        = {blocks}")
    print(f"payload_assert_hits（含真值段）  = {hits}")
    return {"records_reward": tot, "records_with_truth": truth,
            "payload_blocks": blocks, "payload_assert_hits": hits}


# ------------------------------------- D. 反证组：故意写错，看闸拦不拦
def part_d() -> dict:
    """⛔ 这一部分是**反证组**（纪律：证明断言不是死代码）。

    只看 `verify.failed=0` 不足以说明"闸放过了错答案" —— 也可能那一跑恰好没写错。
    所以直接喂一份**确定错的**实现，看闸说通过还是不通过。
    """
    from mw.verify import run as vrun
    cases = [
        ("k03_unflatten", "def unflatten(flat):\n    return flat", "把输入原样返回"),
        ("k02_encode_cookie", "def encode_cookie(d):\n    return 'x'", "永远返回 'x'"),
        ("t04_merge_ranges", "def merge_ranges(pairs):\n    return []", "永远返回空表"),
        ("t03_commas", "def commas(n):\n    return str(n)", "不加千分位"),
    ]
    pool = {}
    for p in glob.glob(os.path.join(ROOT, "bench/heldout/*.jsonl")) + \
            glob.glob(os.path.join(ROOT, "bench/tasks/*.jsonl")):
        for line in open(p, encoding="utf-8"):
            if line.strip():
                t = json.loads(line)
                pool.setdefault(t["id"], t)

    def score(code: str, tests: list[str]) -> float:
        ns: dict = {}
        try:
            exec(code, ns)
        except Exception:
            return 0.0
        ok = 0
        for a in tests:
            try:
                exec(a, ns)
                ok += 1
            except Exception:
                pass
        return ok / len(tests)

    print()
    print("=" * 96)
    print("D. 反证组 —— 喂一份**确定错的**实现，内核自检拦不拦")
    print("=" * 96)
    print(f"{'题':22s} {'外部打分':>7s} {'有示例判据':>7s} {'自检结论':>8s}  说明")
    caught = 0
    for tid, code, why in cases:
        t = pool[tid]
        checks = derive(t["prompt"], static=2, exec_=1)
        has_ex = any(c.kind == "example" for c in checks)
        v = vrun(code, checks)
        sc = score(code, t["tests"])
        if not v.ok:
            caught += 1
        print(f"{tid:22s} {sc:>7.2f} {('YES' if has_ex else 'no'):>9s} "
              f"{('拦住' if not v.ok else '放过'):>8s}  {why}")
        if not v.ok:
            print(f"{'':22s}   报错原文：{v.report[:90]}")
    print()
    print(f"caught/cases（故意错且被闸拦住）= {caught}/{len(cases)}")
    return {"cases": len(cases), "caught": caught}


def main() -> None:
    a = part_a()
    b = part_b()
    cc = part_c()
    d = part_d()
    print()
    print("=" * 96)
    print("结论行（字段名带分母）")
    print("=" * 96)
    print(f"P1 tasks_with_exec/task_ids      = {a['tasks_with_exec']}/{a['task_ids']}"
          f"（按题集行 {a['tasks_with_exec']}/{a['task_rows']}）")
    print(f"P2 fn_runs/runs_with_verify      = {b['fn_runs']}/{b['runs_with_verify']}"
          f"（另有 {b['runs_without_verify']} 跑因旧格式缺席）")
    print(f"P3 payload_assert_hits/blocks    = {cc['payload_assert_hits']}/{cc['payload_blocks']}"
          f"（库里 records_with_truth/records_reward = "
          f"{cc['records_with_truth']}/{cc['records_reward']}）")


if __name__ == "__main__":
    main()
