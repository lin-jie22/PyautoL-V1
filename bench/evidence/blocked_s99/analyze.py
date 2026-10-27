#!/usr/bin/env python3
"""第九十九轮 §66 两臂读数汇总（零模型，只读落盘）。

用法：python3 bench/evidence/blocked_s99/analyze.py [--tag-a …] [--tag-b …]

⚠️ 两臂**只差** `V1_STALL_ON_BLOCKED`（0 / 1），且必须**同晚**（§62）。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SUMMARY = re.compile(
    r"n=3\s+mean=([0-9.]+)\s+std=([0-9.]+)\s+\[([0-9.]+),([0-9.]+)\]\s+极差=([0-9.]+)")
#: ⚠️ 一律用 `-E` 语义的**单一**模式（BSD grep 不认 `\|`；这里是 python，无此问题，
#:    但上一轮真的因为那条 grep 把"6 次重复"读成"0 次"）。


def counts_mech(log: Path) -> dict:
    txt = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    c: dict[str, float] = {}
    # ── 三个"这一步没执行"的出口，分开数（它们会被开关改变归属）──────────
    c["停滞行"] = len(re.findall(r"个候选全部是已试过的动作", txt))
    c["全拦行"] = len(re.findall(r"⛔ 全部候选被拦", txt))
    c["重复跳过"] = len(re.findall(r"⏭ 重复动作已跳过", txt))
    c["被拦"] = len(re.findall(r"⊘ \[values", txt)) + len(re.findall(r"      ✋ ", txt))
    # ── 本轮无关但要一起看，防止"顺手改了别的" ──────────────────────────
    c["交付"] = len(re.findall(r"📦 收尾交付", txt))
    for m in re.finditer(r"来源=([^\s|]+)", txt):
        c["来源:" + m.group(1)] = c.get("来源:" + m.group(1), 0) + 1
    for m in re.finditer(r"宽限生效=(\d+)次\(超出([0-9.]+)s\)", txt):
        c["宽限生效次数"] = c.get("宽限生效次数", 0) + int(m.group(1))
        c["宽限超出s"] = max(c.get("宽限超出s", 0.0), float(m.group(2)))
    m = SUMMARY.search(txt)
    if m:
        c["mean"] = float(m.group(1))
        c["std"] = float(m.group(2))
        c["min"] = float(m.group(3))
        c["max"] = float(m.group(4))
        c["极差"] = float(m.group(5))
    return c


def per_task(log: Path) -> dict[str, list[float]]:
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


def run_meta(tag: str) -> dict:
    """从 runs.jsonl 取口径与耗时（**不**从日志猜）。"""
    p = Path(f"/tmp/v1_s99_bench_{tag}/runs.jsonl")
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    rep = d.get("report") or {}
    lim = (d.get("caliber") or {}).get("limits") or {}
    return {"caliber_sig": d.get("caliber_sig"),
            "stall_on_blocked": lim.get("stall_on_blocked"),
            "elapsed_s": rep.get("elapsed_s"),
            "attempted": rep.get("attempted"), "recorded": rep.get("recorded")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag-a", default="v2_7b_s99a")
    ap.add_argument("--tag-b", default="v2_7b_s99b")
    args = ap.parse_args()
    arms = {f"{args.tag_a}(SB=0)": args.tag_a, f"{args.tag_b}(SB=1)": args.tag_b}
    res, tasks = {}, {}
    for name, tag in arms.items():
        log = Path(f"/tmp/v1_s99_run_{tag}.log")
        res[name] = counts_mech(log)
        tasks[name] = per_task(log)
        print("=" * 78)
        print(f"  {name}   [{tag}]   {run_meta(tag)}")
        print("=" * 78)
        for k in ("mean", "std", "极差", "min", "max"):
            if k in res[name]:
                print(f"    {k:8s} {res[name][k]}")
        print("    机制：", {k: v for k, v in sorted(res[name].items())
                             if k not in ("mean", "std", "极差", "min", "max")})
        if tasks[name]:
            print("    逐题：", {k: [round(x, 4) for x in v]
                                for k, v in sorted(tasks[name].items())})

    a, b = list(arms)[0], list(arms)[1]
    keys = sorted({k for v in res.values() for k in v})
    print()
    print("=" * 78)
    print("  两臂对照（唯一变量 = V1_STALL_ON_BLOCKED）")
    print("=" * 78)
    print(f"  {'量':22s} {a.split('(')[1]:>14s} {b.split('(')[1]:>14s} {'Δ':>10s}")
    for k in keys:
        x, y = res[a].get(k, 0), res[b].get(k, 0)
        d = ""
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            d = f"{y - x:+.4f}" if isinstance(x, float) or isinstance(y, float) \
                else f"{y - x:+d}"
        print(f"  {k:22s} {str(x):>14s} {str(y):>14s} {d:>10s}")

    # ── 逐题逐轮翻转（§85：机会的正确度量是**逐题翻转数**，不是均值）──────
    flips = []
    for t in sorted(set(tasks[a]) & set(tasks[b])):
        for i, (u, v) in enumerate(zip(tasks[a][t], tasks[b][t])):
            if abs(u - v) > 1e-9:
                flips.append((t, i + 1, u, v, v - u))
    print()
    print(f"  翻转格 {len(flips)} / {sum(len(v) for v in tasks[a].values())}："
          + (str([f"{t}#{i} {u:.4f}→{v:.4f}" for t, i, u, v, _ in flips]) if flips else "无"))

    if "mean" in res[a] and "mean" in res[b]:
        d = res[b]["mean"] - res[a]["mean"]
        noise = max(res[a].get("极差", 0), res[b].get("极差", 0))
        print()
        print(f"  Δmean = {d:+.4f} ｜ 单臂极差 = {res[a].get('极差'):.4f} / "
              f"{res[b].get('极差'):.4f} ｜ max = {noise:.4f}")
        # ⛔ §63.6：极差是**本时段**的属性，T=0 不保证逐字确定。
        print(f"  裁决：{'可分辨' if abs(d) > noise else '落在噪声内 ⇒ UNKNOWN'}"
              f"（方向 {'+' if d >= 0 else '−'}；⚠️ 极差只是本时段的下界）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
