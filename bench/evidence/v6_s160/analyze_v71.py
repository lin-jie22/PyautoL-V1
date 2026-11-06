#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V71 取证脚本（§139）：全仓「打回 → 重写」这条边的兑现率。

⛔ 不调模型、不改内核、不押方向。只从**已冻结的** runs/*.jsonl 与 core/stdout 日志里
   把三个计数搬出来，并给出可逐行核对的原文位置。

用法：
    python3 bench/evidence/v6_s160/analyze_v71.py            # 全仓汇总
    python3 bench/evidence/v6_s160/analyze_v71.py detail     # 附「有打回」的逐跑明细
"""
import glob
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

# 三个账本键的**定义行**（解释成立前先回读定义，纪律 10）
DEFS = {
    "redelivered": "mw/core.py:2005 / mw/core.py:1407 —— 打回之后**又交了一次**",
    "same":        "mw/core.py:1407 —— 重提的交付**逐字相同**，在重复闸被丢",
    "diff":        "mw/core.py:2012 —— 重提的交付与被打回那版**不同**",
    "passed_after":"mw/core.py:2022 —— 重交的那一份**过闸**（是 diff 的子集）",
    "task_runs_rejected": "run.py:1197 —— 被打回过至少一次的**题-run** 数（上面四项的分母）",
    "rework_gap":  "run.py:1222 —— redelivered − (same + diff)，恒等于 0 才对",
}


def load_rows():
    out = []
    for p in sorted(glob.glob(os.path.join(ROOT, "bench/evidence/**/runs*.jsonl"),
                              recursive=True)):
        try:
            rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
        except Exception:
            continue
        for r in rows:
            if isinstance(r, dict):
                out.append((p, r))
    return out


def summarize(rows):
    agg = {k: {} for k in ("kernel_rework", "kernel_verify", "kernel_shell")}
    for _, r in rows:
        for k in agg:
            d = r.get(k)
            if isinstance(d, dict):
                for kk, v in d.items():
                    if isinstance(v, int):
                        agg[k][kk] = agg[k].get(kk, 0) + v
    return agg


def rejected_rows(rows):
    out = []
    for p, r in rows:
        rw = r.get("kernel_rework") or {}
        if (rw.get("task_runs_rejected") or 0) <= 0:
            continue
        lim = ((r.get("caliber") or {}).get("limits") or {})
        vf = r.get("kernel_verify") or {}
        out.append({
            "file": os.path.basename(os.path.dirname(p)) + "/" + os.path.basename(p),
            "rej": rw.get("task_runs_rejected"), "same": rw.get("same"),
            "diff": rw.get("diff"), "passed_after": rw.get("passed_after"),
            "dup": rw.get("dup_skipped"),
            "verify_exec": lim.get("verify_exec"), "verify_retries": lim.get("verify_retries"),
            "example_runs": vf.get("example_runs"), "example_failed": vf.get("example_failed"),
        })
    return out


def main():
    rows = load_rows()
    print(f"# V71 取证：全仓「打回 → 重写」兑现率\n")
    print(f"runs 文件 {len({p for p, _ in rows})} 个、行 {len(rows)} 行\n")

    agg = summarize(rows)
    print("## 一、三个账本的累计（全仓，跨 93 个 runs 文件）\n")
    for k in ("kernel_rework", "kernel_verify", "kernel_shell"):
        print(f"### {k}")
        for kk, v in sorted(agg[k].items()):
            print(f"  {kk:22s} {v}")
        print()

    rw = agg["kernel_rework"]
    print("## 二、兑现率（分母口径写进句子）\n")
    rej = rw.get("task_runs_rejected", 0)
    red = rw.get("redelivered", 0)
    same = rw.get("same", 0)
    diff = rw.get("diff", 0)
    pa = rw.get("passed_after", 0)
    print(f"  被打回的**题-run**            : {rej}")
    print(f"  打回之后**又交了一次**（次）  : {red}")
    print(f"    ├ 与被打回那版**逐字相同**  : {same}")
    print(f"    └ 与被打回那版**不同**      : {diff}")
    print(f"  重交那一次**过闸**（次）      : {pa}")
    print(f"  `rework_gap`（必须为 0）      : {rw.get('rework_gap')}")
    print(f"  `verify.exhausted`（配额用满）: {agg['kernel_verify'].get('exhausted')}")
    if red:
        print(f"\n  ⇒ 改版率 = {diff}/{red} = {diff / red:.4f}")
        print(f"  ⇒ 改版后过闸率 = {pa}/{diff} = {(pa / diff) if diff else float('nan'):.4f}")

    print("\n## 三、字段定义行（解释之前先回读）\n")
    for k, v in DEFS.items():
        print(f"  {k:20s} {v}")

    if "detail" in sys.argv:
        print("\n## 四、逐跑明细（只列有打回的跑）\n")
        det = rejected_rows(rows)
        hdr = (f"{'file':46s} {'打回':>4s} {'同':>3s} {'异':>3s} {'过':>3s} "
               f"{'dup':>4s} {'ve':>3s} {'vr':>3s} {'ex_r':>5s} {'ex_f':>5s}")
        print(hdr)
        print("-" * len(hdr))
        for x in det:
            print(f"{x['file']:46s} {x['rej']:>4} {x['same']:>3} {x['diff']:>3} "
                  f"{x['passed_after']:>3} {x['dup']:>4} {str(x['verify_exec']):>3} "
                  f"{str(x['verify_retries']):>3} {str(x['example_runs']):>5} "
                  f"{str(x['example_failed']):>5}")
        print(f"\n合计 {len(det)} 跑")


if __name__ == "__main__":
    main()
