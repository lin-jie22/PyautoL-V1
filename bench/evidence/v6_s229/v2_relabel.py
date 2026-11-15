#!/usr/bin/env python3
"""W2 / W4：把 V139 的 89 站读数用**三值口径**重判一遍。

⛔ 这不产生新读数 —— 输入是 `v6_s227/readings.jsonl` 里已经落盘的
   `verdict`（旧口径 grep 的结果）与 `exit`。它只回答一个问题：

       换成 `mw.measure.verdict_from` 之后，**哪些站的判决会变**？

W2：差异集合必须 == V139 已登记的 `SPILL_CRASH = 2`（复算一致性，不是命中）。
W4：原本判绿的站一个都不许变红（新判据不许顺手把别的东西判死）。

用法：
    python3 bench/evidence/v6_s229/v2_relabel.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.measure import SENTINEL, verdict_from        # noqa: E402

EVID = Path(__file__).resolve().parent
V138 = ROOT / "bench" / "evidence" / "v6_s227"


def sites() -> list[dict]:
    """89 个站点。

    ⚠️ 分母口径（纪律 46）：`readings.jsonl` 里有三类记录，
       只有第一类是这 89 站 ——
         · `模块.py:行`      ← 站点扫描（89）
         · `模块.py:行@值`   ← Part B 剖面（10），带 `@`
         · `re_模块_行`      ← 复现抽查（5），不含冒号
       不排除后两类会把分母写成 99/104。V140 第一次复算就是这么错的。
    """
    out = []
    for ln in (V138 / "readings.jsonl").read_text().splitlines():
        r = json.loads(ln)
        t = r.get("tag") or ""
        if "site" in r and ":" in t and "@" not in t:
            out.append(r)
    return out


def old_state(v: object) -> str:
    """旧口径：`grep -oE 'PASS=\\d+  FAIL=\\d+' | tail -1` 读出来的东西。

    `FAIL==0` 就判绿 —— 它**看不见退出码**，这正是缺口所在。
    """
    return "GREEN" if str(v).endswith("/0") else "RED"


def main() -> int:
    recs = sites()
    diff, bad = [], []
    for r in recs:
        v, e = r["verdict"], r.get("exit")
        old = old_state(v)
        p, f = (str(v).split("/") + ["?"])[:2]
        # 把旧口径「看到的那一行」当作输入喂给新口径：差异只来自退出码与格式。
        new, why = verdict_from(f"{SENTINEL} {old}  PASS={p}  FAIL={f}", int(e or 0))
        if new != old:
            diff.append({"site": r["tag"], "old": old, "exit": e,
                         "new": new, "why": why, "verdict": v})
        if old == "GREEN" and new == "RED":
            bad.append(r["tag"])

    old_dist = Counter(old_state(r["verdict"]) for r in recs)
    new_dist = Counter(("GREEN" if old_state(r["verdict"]) == "GREEN" else "RED")
                       for r in recs)
    for d in diff:
        new_dist[d["old"]] -= 1
        new_dist[d["new"]] += 1

    print(f"站点数        : {len(recs)}")
    print(f"旧口径分布    : {dict(old_dist)}")
    print(f"exit 分布     : {dict(Counter(r.get('exit') for r in recs))}")
    print(f"新口径分布    : {dict(new_dist)}")
    print(f"\n判决改变的站  : {len(diff)}")
    for d in diff:
        print(f"  {d['site']:20s} {d['old']:5s}({d['verdict']}) exit={d['exit']}"
              f" → {d['new']:5s} — {d['why']}")

    print("\n== W4 无副作用检查 ==")
    print(f"  原本判绿 → 变红 : {len(bad)} 站"
          f"  {'✅ 无' if not bad else '⛔ ' + str(bad)}")
    print(f"  原本判绿 → 变崩 : "
          f"{len([d for d in diff if d['old'] == 'GREEN' and d['new'] == 'CRASH'])} 站")
    print(f"  原本判红 → 任何 : "
          f"{len([d for d in diff if d['old'] == 'RED'])} 站")

    (EVID / "relabel.json").write_text(json.dumps(
        {"n_sites": len(recs), "old": dict(old_dist), "new": dict(new_dist),
         "changed": diff, "green_to_red": bad}, ensure_ascii=False, indent=2))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
