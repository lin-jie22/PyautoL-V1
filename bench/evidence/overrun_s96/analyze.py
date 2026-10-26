#!/usr/bin/env python3
"""第九十六轮 §63 两臂读数汇总（零模型，只读落盘）。

用法：python3 /tmp/v1_s96/analyze.py
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ARMS = {
    "overrun=0（对照）": ("v2_7b_s96_old", "/tmp/v1_s96_run_v2_7b_s96_old.log"),
    "overrun=30（新）": ("v2_7b_s96_new", "/tmp/v1_s96_run_v2_7b_s96_new.log"),
}

SUMMARY = re.compile(r"n=3\s+mean=([0-9.]+)\s+std=([0-9.]+)\s+\[([0-9.]+),([0-9.]+)\]\s+极差=([0-9.]+)")


def counts_mech(log: Path) -> dict:
    txt = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    c: Counter = Counter()
    c["交付"] = len(re.findall(r"📦 收尾交付", txt))
    for m in re.finditer(r"来源=([^\s|]+)", txt):
        c["来源:" + m.group(1)] += 1
    for m in re.finditer(r"收尾调用失败 (\w+)", txt):
        c["收尾失败:" + m.group(1)] += 1
    for m in re.finditer(r"宽限生效=(\d+)次\(超出([0-9.]+)s\)", txt):
        c["宽限生效次数"] += int(m.group(1))
        c["宽限最大超出s"] = max(c["宽限最大超出s"], float(m.group(2)))
    for m in re.finditer(r"上限被裁短=(\d+)", txt):
        c["上限被裁短"] += int(m.group(1))
    for m in re.finditer(r"墙钟放弃首发=(\d+)/重试=(\d+)", txt):
        c["放弃首发"] += int(m.group(1))
        c["放弃重试"] += int(m.group(2))
    for m in re.finditer(r"收尾闸=(\d+)跑/(\d+)过/(\d+)未过", txt):
        c["收尾闸跑"] += int(m.group(1))
        c["收尾闸未过"] += int(m.group(3))
    m = SUMMARY.search(txt)
    if m:
        c["mean"] = float(m.group(1))
        c["std"] = float(m.group(2))
        c["min"] = float(m.group(3))
        c["max"] = float(m.group(4))
        c["极差"] = float(m.group(5))
    return dict(c)


def per_task(log: Path) -> dict[str, list[float]]:
    """从 `run.log` 的逐题表抓 (题, 三次分)。"""
    out: dict[str, list[float]] = {}
    txt = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    for line in txt.splitlines():
        m = re.match(r"^\s{2}(sg\d\d)\s+([\d./\s]+?)\s{2,}", line)
        if not m:
            continue
        nums = [float(x) for x in re.findall(r"(\d+\.\d+)", m.group(2))]
        if len(nums) >= 3:
            out.setdefault(m.group(1), nums[:3])
    return out


def main() -> int:
    res = {}
    for name, (tag, log) in ARMS.items():
        log = Path(log)
        res[name] = counts_mech(log)
        print("=" * 78)
        print(f"  {name}   [{tag}]")
        print("=" * 78)
        for k in ("mean", "std", "极差", "min", "max"):
            if k in res[name]:
                print(f"    {k:8s} {res[name][k]}")
        print("    机制：", {k: v for k, v in sorted(res[name].items())
                             if k not in ("mean", "std", "极差", "min", "max")})
        pt = per_task(log)
        if pt:
            print("    逐题：", {k: [round(x, 4) for x in v] for k, v in sorted(pt.items())})

    keys = sorted({k for v in res.values() for k in v})
    print()
    print("=" * 78)
    print("  两臂对照")
    print("=" * 78)
    print(f"  {'量':22s} {'overrun=0':>14s} {'overrun=30':>14s} {'Δ':>10s}")
    names = list(ARMS)
    for k in keys:
        a = res[names[0]].get(k, 0)
        b = res[names[1]].get(k, 0)
        d = ""
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            d = f"{b - a:+.4f}" if isinstance(a, float) or isinstance(b, float) else f"{b - a:+d}"
        print(f"  {k:22s} {str(a):>14s} {str(b):>14s} {d:>10s}")

    if "mean" in res[names[0]] and "mean" in res[names[1]]:
        a, b = res[names[0]], res[names[1]]
        d = b["mean"] - a["mean"]
        noise = max(a.get("极差", 0), b.get("极差", 0))
        print()
        print(f"  Δmean = {d:+.4f} ｜ 单臂极差 = {a.get('极差'):.4f} / {b.get('极差'):.4f}"
              f" ｜ max = {noise:.4f}")
        print(f"  裁决：{'可分辨' if abs(d) > noise else '落在噪声内 ⇒ UNKNOWN'}（方向 {'+' if d >= 0 else '−'}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
