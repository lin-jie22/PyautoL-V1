#!/usr/bin/env python3
"""把一趟「独立进程跑批」冻结成证据池。

## 为什么要单独一个脚本

`runs.jsonl` 是**活文件**，每跑一次就长一行。裁决拿活文件当输入，等于
「换一个时间点跑同一个检查会得到不同结论」—— 那不叫证据。
所以：跑批 → 冻结 → 冻结件入库（`bench/evidence/noise_s72/pool.jsonl`），
契约与探针只读冻结件。**要更新就重跑脚本并重新登记**，不许让测试顺手刷新。

## 它拒收什么

- 没有 `run_id` 的记录（本轮之前的 83 条全部属于此列）；
- 臂或口径不匹配的记录；
- 冻结后独立运行次数 < `noise_floor.MIN_RUNS` ⇒ **报错退出**，
  不生成一个"看起来有 4 次"的池子。

用法：
    python3 bench/freeze_noise_pool.py --arm 真模型直连… --sig 39e8… --out …
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V1 / "bench"))

import noise_floor as NF                                          # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="冻结独立运行池")
    ap.add_argument("--runs", default=str(V1 / "bench_out" / "runs.jsonl"))
    ap.add_argument("--arm", required=True, help="臂标签（runs.jsonl 的 solver 原文）")
    ap.add_argument("--sig", required=True, help="口径签名")
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-runs", type=int, default=NF.MIN_RUNS)
    a = ap.parse_args()

    src = Path(a.runs)
    keep: list[dict] = []
    drop = {"no_run_id": 0, "other": 0}
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if not rec.get("run_id"):
            drop["no_run_id"] += 1
            continue
        if str(rec.get("solver")) != a.arm or str(rec.get("caliber_sig")) != a.sig:
            drop["other"] += 1
            continue
        keep.append(rec)

    ids = {r["run_id"] for r in keep}
    if len(ids) < a.min_runs:
        print(f"⛔ 只有 {len(ids)} 次独立运行（要求 ≥{a.min_runs}）"
              f" ⇒ 不冻结（凑数的池子比没有池子更危险）")
        return 1

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep),
                   encoding="utf-8")
    print(f"  ✅ 冻结 {len(keep)} 条 / {len(ids)} 次独立运行 → {out}")
    print(f"     拒收：{drop}（旧记录没有 run_id，被**数出来**而不是静默消失）")
    obs = [NF.from_record(r) for r in keep]
    for line in NF.report(NF.bracket(obs), label=a.arm):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
