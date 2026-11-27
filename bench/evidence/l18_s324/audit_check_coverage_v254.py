#!/usr/bin/env python3
"""V254 自检覆盖审计（纯离线，0 次模型调用）。

从 V253 Stage A 的日志里还原**每格的自检判据项**与**打回/交付状态**，
再与分数交叉。要回答的是：

  13 格 <1.0，究竟是「自检没覆盖到」还是「自检看到了但模型改不动」？

判据项里有没有 `示例可跑` 是一个**机械可判**的二分变量：
有 ⇒ 装置把题面里的示例真跑了；没有 ⇒ 装置只查了非空/函数名/语言标签/可编译/结构。

⛔ 只读日志与读数，不跑模型、不改生产代码。
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path("/Users/linshaojie/V1")
EV = ROOT / "bench" / "evidence" / "l18_s324"
LOG = ROOT / "bench" / "evidence" / "l17_s323" / "run_a.log"
SUITE = pathlib.Path("/tmp/v253_suite/all39.jsonl")
ANALYSE = ROOT / "bench" / "evidence" / "l17_s323" / "analyse_v253.json"

RE_START = re.compile(r"▶ 任务开始: (.*)$")
#: ⚠️ 收尾行的第一个词**不止 done / done_unverified** —— t04 是 `■ stalled`。
#:    第一版只写死了两个值 ⇒ 39 格里静默漏掉 3 格，而交叉表照样打印「0/11」。
#:    （纪律 69：缺失不是失败；但这里的缺失会**伪造一个干净结论**。）
RE_DONE = re.compile(r"■ ([a-z_]+) .*?校验=(\d+)跑/(\d+)过/(\d+)打回/(\d+)放弃 判据=(\d+)项\((.*?)\)")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def main() -> int:
    tasks = [json.loads(l) for l in SUITE.read_text(encoding="utf-8").splitlines() if l.strip()]
    # 日志里题面被截断到 ~80 字，所以用前缀匹配
    by_prefix = {norm(t["prompt"])[:40]: t["id"] for t in tasks}
    an = json.loads(ANALYSE.read_text(encoding="utf-8"))
    scores = {k: float((v.get("A") or {}).get("score") or 0.0) for k, v in an["per_cell"].items()}
    whys = {k: str((v.get("A") or {}).get("why") or "") for k, v in an["per_cell"].items()}

    cur = None
    rows = []
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    for ln in lines:
        m = RE_START.search(ln)
        if m:
            cur = norm(m.group(1))
            continue
        m = RE_DONE.search(ln)
        if m and cur is not None:
            verdict, runs, ok, bounced, gaveup, nitems, items = m.groups()
            tid = by_prefix.get(cur[:40]) or ""
            rows.append({"id": tid, "verdict": verdict, "checks": int(runs),
                         "ok": int(ok), "bounced": int(bounced), "gaveup": int(gaveup),
                         "n_items": int(nitems),
                         "items": [x.strip() for x in items.split("/")],
                         "has_example": "示例可跑" in items})
            cur = None

    # 去重：同题可能因重跑出现多次，取最后一次
    latest: dict[str, dict] = {}
    for r in rows:
        if r["id"]:
            latest[r["id"]] = r

    print("=" * 96)
    print("A. 39 格（有日志的）逐格：分数 / 打回 / 判据项 / 是否有示例可跑")
    print("=" * 96)
    print(f"{'cell':26}{'score':>7}{'valid':>4}{'bounce':>7}{'gaveup':>7}  ex  items")
    for tid in sorted(latest, key=lambda x: scores.get(x, 9)):
        r = latest[tid]
        print(f"{tid:26}{scores.get(tid, -1):7.3f}{r['ok']:4d}{r['bounced']:7d}{r['gaveup']:7d}"
              f"  {'Y' if r['has_example'] else '.'}   {'/'.join(r['items'])}")

    # ── B. 交叉表 ────────────────────────────────────────────────────────
    low = [t for t in latest if scores.get(t, 0) < 1.0]
    high = [t for t in latest if scores.get(t, 0) >= 1.0]
    def rate(ids, key):
        return f"{sum(1 for i in ids if latest[i][key])}/{len(ids)}"
    print()
    print("=" * 96)
    print("B. 交叉表")
    print("=" * 96)
    print(f"  <1.0 的格 {len(low)}：有示例可跑 {rate(low,'has_example')}，"
          f"有打回 {sum(1 for i in low if latest[i]['bounced']>0)}/{len(low)}，"
          f"未过自检交付 {sum(1 for i in low if latest[i]['verdict']=='done_unverified')}/{len(low)}")
    print(f"  =1.0 的格 {len(high)}：有示例可跑 {rate(high,'has_example')}，"
          f"有打回 {sum(1 for i in high if latest[i]['bounced']>0)}/{len(high)}，"
          f"未过自检交付 {sum(1 for i in high if latest[i]['verdict']=='done_unverified')}/{len(high)}")
    print()
    print("  ⚠️ 「自检通过但外部 0 分/低分」的格 = 自检看不见的缺陷：")
    blind = [i for i in latest if latest[i]["ok"] > 0 and latest[i]["bounced"] == 0
             and scores.get(i, 1) < 1.0]
    for i in sorted(blind, key=lambda x: scores.get(x, 9)):
        print(f"    {i:26} score={scores.get(i, -1):.3f} why={(whys.get(i) or '')[:88]}")
    print(f"    共 {len(blind)} 格")

    print()
    print("  ⚠️ 「自检三次打回后仍交付」的格（模型拿到了错误原文却没改对）：")
    hard = [i for i in latest if latest[i]["gaveup"] > 0 or latest[i]["verdict"] == "done_unverified"]
    for i in sorted(hard, key=lambda x: scores.get(x, 9)):
        r = latest[i]
        print(f"    {i:26} score={scores.get(i, -1):.3f} bounce={r['bounced']} gaveup={r['gaveup']}")

    out = {"cells": latest, "scores": scores}
    (EV / "audit_check_coverage_v254.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n⇒ 写 {EV / 'audit_check_coverage_v254.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
