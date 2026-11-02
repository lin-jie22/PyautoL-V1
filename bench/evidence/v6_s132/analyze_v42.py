#!/usr/bin/env python3
"""V42 分析器：四臂（salvage × failover）的读数与三条对照。

⛔ **只读**：它不改任何证据文件，只从 `--work` 目录（默认 `/tmp/v42`）读
`bench_*/runs.jsonl` 与 `run_*.log`，把结果打到 stdout（由调用方重定向冻结）。

## 为什么每条判据都要有"对照臂"

| 判据 | 它在防什么 |
|---|---|
| D1 `limits` 逐键 diff | 四臂"只有两个旋钮不同"。⛔ 若还有第三个字段变了，后面的分数差**不可归因** |
| D2 `salvage=0` 两臂逐格相同 | **H0-j**：恒 `候选=1` ⇒ failover 不该有弹药。不同 ⇒ 开关泄漏 |
| D3 `salvage=3` 两臂逐格 | 本轮唯一的"有效对照" |
| D4 `kernel_failover` | 机制到底 fire 了几次（**不是**"分数涨了几格"） |
| D5 日志 `候选=N` 分布 | 弹药量。⛔ `候选=N` 是**机制打印的名字**，不是读数标签（§107.6 第 4 条） |

⚠️ 分数只有 96 格、有效样本按**题**算 ⇒ 任何 Δ 都必须连同"哪几题动了"一起报，
不许只报均值（纪律 18：Σ 会把两格反向相消折成 0）。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ARMS = [("s0f0", 0, 0), ("s0f1", 0, 1), ("s3f0", 3, 0), ("s3f1", 3, 1)]


def load_run(work: Path, arm: str) -> dict | None:
    p = work / f"bench_{arm}" / "runs.jsonl"
    if not p.exists():
        return None
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").split("\n") if x.strip()]
    return rows[-1] if rows else None


def per_task(row: dict) -> dict:
    out = {}
    for t in (row.get("report") or {}).get("per_task") or []:
        why = str(t.get("why") or "")
        m = re.search(r"(\d+)\s*/\s*(\d+)", why)
        out[str(t.get("id"))] = {
            "score": t.get("score"),
            "cells": int(m.group(1)) if m else None,
            "total": int(m.group(2)) if m else None,
            "status": t.get("status"),
        }
    return out


def log_stats(work: Path, arm: str) -> dict:
    p = work / f"run_{arm}.log"
    if not p.exists():
        return {}
    t = p.read_text(encoding="utf-8", errors="replace")
    cand: dict[str, int] = {}
    for m in re.finditer(r"候选=(\d+)", t):
        cand[m.group(1)] = cand.get(m.group(1), 0) + 1
    return {
        "候选分布": dict(sorted(cand.items())),
        "让位行": len(re.findall(r"让位给候选", t)),
        "自检打回": len(re.findall(r"自检未过（第", t)),
        "自检超限": len(re.findall(r"自检连续", t)),
        "降级行": len(re.findall(r"规划降级", t)),
        "收尾未过": len(re.findall(r"收尾（未通过自检）", t)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="/tmp/v42")
    a = ap.parse_args()
    work = Path(a.work)

    print("=" * 100)
    print(f"V42 四臂读数（work={work}）")
    print("=" * 100)
    rows = {}
    for arm, sag, fo in ARMS:
        row = load_run(work, arm)
        rows[arm] = row
        pt = per_task(row) if row else {}
        kfo = (row or {}).get("kernel_failover") or {}
        kpl = (row or {}).get("kernel_planner") or {}
        m = (row or {}).get("measurement") or {}
        print()
        print(f"■ {arm}（salvage={sag} failover={fo}）")
        if row is None:
            print("  ⛔ 没有 runs.jsonl ⇒ 这一臂没跑完（不是 0 分）")
            continue
        print(f"  口径 sig={(row.get('caliber_sig') or 'NA')[:16]} "
              f"mean={m.get('mean')} n={m.get('n')} std={m.get('std')}")
        print(f"  让位 用={kfo.get('used')} 救回={kfo.get('rescued')} "
              f"额外失败={kfo.get('cf_extra')} 无弹药={kfo.get('no_more')} "
              f"率={kfo.get('rescue_rate')} 开关={kfo.get('switch')}")
        print(f"  规划 calls={kpl.get('calls')} 失败={kpl.get('fail_total')} "
              f"修复={kpl.get('salvage_ok')}/{kpl.get('salvage_fail')}")
        print(f"  逐题 " + " ".join(f"{k}={v['cells']}/{v['total']}({v['score']})"
                                    for k, v in pt.items()))
        ls = log_stats(work, arm)
        if ls:
            print(f"  日志 候选={ls['候选分布']} 让位行={ls['让位行']} "
                  f"打回={ls['自检打回']} 超限={ls['自检超限']} 降级={ls['降级行']}")

    print()
    print("=" * 100)
    print("D1 口径：四臂 limits 逐键 diff（应只有 cand_failover / plan_salvage 不同）")
    print("=" * 100)
    base = None
    for arm, _, _ in ARMS:
        row = rows.get(arm)
        if row is None:
            print(f"  {arm}: 缺")
            continue
        lim = ((row.get("caliber") or {}).get("limits") or {})
        if base is None:
            base = (arm, lim)
            print(f"  {arm}: 基准（{len(lim)} 个键）")
            continue
        diff = {k: (base[1].get(k), lim.get(k))
                for k in sorted(set(base[1]) | set(lim)) if base[1].get(k) != lim.get(k)}
        print(f"  {arm} vs {base[0]}: {diff if diff else '无差异'}")

    print()
    print("=" * 100)
    print("D2 H0-j：salvage=0 两臂逐格（failover 无弹药 ⇒ 必须相同）")
    print("=" * 100)
    p0, p1 = per_task(rows["s0f0"]) if rows.get("s0f0") else {}, \
        per_task(rows["s0f1"]) if rows.get("s0f1") else {}
    if not p0 or not p1:
        print("  ⛔ 有臂缺读数 ⇒ 判不了（不是'相同'）")
    else:
        bad = {k: (p0[k]["cells"], p1[k]["cells"]) for k in p0
               if p0[k]["cells"] != p1.get(k, {}).get("cells")}
        print(f"  s0f0={[v['cells'] for v in p0.values()]}")
        print(f"  s0f1={[v['cells'] for v in p1.values()]}")
        print(f"  ⇒ {'✅ 逐格相同' if not bad else '⛔ 不同：' + str(bad)}")

    print()
    print("=" * 100)
    print("D3 有效对照：salvage=3 两臂逐格（本轮唯一能回答'让位值不值'的一对）")
    print("=" * 100)
    q0, q1 = per_task(rows["s3f0"]) if rows.get("s3f0") else {}, \
        per_task(rows["s3f1"]) if rows.get("s3f1") else {}
    if not q0 or not q1:
        print("  ⛔ 有臂缺读数 ⇒ 判不了")
    else:
        print(f"  s3f0={[v['cells'] for v in q0.values()]}")
        print(f"  s3f1={[v['cells'] for v in q1.values()]}")
        moved = {k: (q0[k]["cells"], q1[k]["cells"]) for k in q0
                 if q0[k]["cells"] != q1.get(k, {}).get("cells")}
        print(f"  动了 {len(moved)} 题：{moved if moved else '无'}")
        print(f"  ⛔ 动了的题必须与 D4 的 `used`/`rescued` 对得上；对不上 ⇒ 别处也在动")


if __name__ == "__main__":
    main()
