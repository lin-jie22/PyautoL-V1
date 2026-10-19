#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运输层路径进读数（第三十六轮 §17-7h 装置修正⑭③）。

要解决的问题（7g 实测）
-----------------------
`s35` 里 **96 个读数 95 个是 0.2**，唯一翻掉的一格是
`o1_l120 / H / g7e2`，两次 repeat = `[0.2, 1.0]`。两次的**运输层路径**不同：

    r1: 墙钟放弃首发=3 / 抖动重试=0        r2: 墙钟放弃首发=0 / 抖动重试=1

⛔ 但"路径不同"**不是**充分条件：同表另 2 格（`o1_l40` H、`o2_l120` H）路径
也不同，却没有翻。⇒ 正确的用法只有一种：**把路径记进读数，并规定
"同一格的两次 repeat 路径不同 ⇒ 该格作废"**（作废 = 不作归因，不是失败）。

为什么要作废而不是"排除那一次"
-------------------------------
因为路径不同意味着**这一次读数经过了不同的装置**。留哪一次都等于
事后挑选（选中的那一次恰好支持某个结论）。作废是唯一不引入选择偏倚的做法。

⚠️ `wall_shortened` **不进签名**：它是"上限被裁短但发出去了"，
在正常端点上 ≡ 调用次数（s35 里 10–19），随工作量变而不随路径变 ⇒
把它算进签名会把几乎**每一格**都标成路径不同（等于把闸废掉）。
⇒ 签名只取"**放弃/重试**"这四个事件计数。

⛔ 本模块不改判据、不改任何已有的判决；`s35` 的 7g 判决已冻结。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

#: 进入路径签名的字段。四个都是"事件计数"，不是"工作量计数"。
SIG_KEYS: tuple[str, ...] = (
    "wall_refused_first",   # 预算尽：首次调用根本没发出去
    "wall_refused_retry",   # 预算尽：重试没发出去
    "retries",              # 抖动重试（同请求、幂等只读）
    "exhausted",            # 重试耗完仍失败
)

#: 明确**排除**的字段及理由（写在这里，防止下一个人"顺手加进去"）。
EXCLUDED = {"wall_shortened": "≡ 调用次数（工作量），不随路径变"}


def path_of(row: dict) -> tuple[int, ...]:
    """一行 runs.jsonl ⇒ 该次的运输层路径签名。缺失的键按 0 计（不静默）。"""
    tx = row.get("kernel_transport") or {}
    return tuple(int(tx.get(k) or 0) for k in SIG_KEYS)


def path_line(sig: tuple[int, ...]) -> str:
    return " ".join(f"{k}={v}" for k, v in zip(SIG_KEYS, sig))


def read_repeats(ev_dir: Path, arm: str) -> dict[int, tuple[int, ...]]:
    """读某臂所有 repeat 的路径签名：`{rep: sig}`。

    ⚠️ repeat 号从**文件名**取（`{arm}_r{N}_runs.jsonl`），不从行里取：
       行里的 `repeat` 字段由驱动器写，早先有过缺省 0 的情形；
       文件名是 `run_g7d.sh` / `run_g7n.sh` 的产物布局，probe 依赖它更稳。
    """
    out: dict[int, tuple[int, ...]] = {}
    for p in sorted(ev_dir.glob(f"{arm}_r*_runs.jsonl")):
        m = p.name[len(arm) + 2:].split("_", 1)[0]      # "r3_runs.jsonl" ⇒ "3"
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()
                if l.strip()]
        if not rows:
            continue
        try:
            rep = int(m)
        except ValueError:
            continue
        out[rep] = path_of(rows[-1])
    return out


def audit_cell(ev_dir: Path, arm: str) -> dict:
    """一个格（= 一个臂 × 一个档位目录）是否作废。

    返回：`{"arm", "reps", "void", "why"}`。`void=True` ⇒ **该格的读数不作归因**。
    """
    reps = read_repeats(ev_dir, arm)
    sigs = set(reps.values())
    #: ⚠️ n=1 的格**无法**判断路径是否一致 ⇒ 既不算"同路径"，也不算"作废"。
    #: 把它算成"同路径"就是静默放行（`rerun_check/` 就是这种格）。
    checked = len(reps) >= 2
    void = checked and len(sigs) > 1
    why = ""
    if void:
        why = "两次 repeat 的运输层路径不同（" + " / ".join(
            f"r{r}:{path_line(s)}" for r, s in sorted(reps.items())) + "）"
    elif not checked:
        why = f"只有 {len(reps)} 次 repeat ⇒ 路径一致性不可判"
    return {"arm": arm, "reps": reps, "void": void,
            "checked": checked, "why": why}


def audit_dir(ev_dir: Path, arms: tuple[str, ...] = ("H", "P", "E")) -> list[dict]:
    return [audit_cell(ev_dir, a) for a in arms
            if any(ev_dir.glob(f"{a}_r*_runs.jsonl"))]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", required=True,
                   help="含多个 ev_* 子目录的证据根目录")
    p.add_argument("--arms", default="H,P,E")
    args = p.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    arms = tuple(a.strip() for a in args.arms.split(",") if a.strip())
    n_void = n_all = n_unchecked = 0
    print(f"== 运输层路径签名（{len(SIG_KEYS)} 个事件计数；"
          f"排除 {','.join(EXCLUDED)}）==")
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        for c in audit_dir(d, arms):
            n_all += 1
            if not c["checked"]:
                n_unchecked += 1
                mark = "·  不可判"
            else:
                n_void += int(c["void"])
                mark = "⛔ 作废" if c["void"] else "✅ 同路径"
            sigs = " / ".join(f"r{r}:({path_line(s)})"
                              for r, s in sorted(c["reps"].items()))
            print(f"  {mark:8s} {d.name:14s} {c['arm']}  {sigs}")
    print(f"\n== 合计 ==  {n_all} 格（其中 {n_unchecked} 格 repeat 数 <2 ⇒ 不可判），"
          f"路径不同 **{n_void}** 格 ⇒ 这些格不作归因")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
