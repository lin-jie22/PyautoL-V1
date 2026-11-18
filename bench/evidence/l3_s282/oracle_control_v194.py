#!/usr/bin/env python3
"""V194 事后对照：把**参考解（oracle）**喂给同一道闸。

⛔ 这不是预注册判据，是 P6 想要而当时拿不到的「正例对照」：
   一道以「筛掉没用的候选」为目的的闸，**必须先证明它不筛掉参考解**。
   若参考解也被 DROP，那这道闸不是尺子，是噪声。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arms_v194 as A  # noqa: E402  —— 复用同一套 fn 提取与入口调用


def main() -> int:
    tasks = A.load_tasks()
    rows = []
    for (suite, tid), t in sorted(tasks.items()):
        code = str(t.get("oracle") or "")
        fn, src = A.pick_fn(t)
        g = A.gate(code, fn)
        v = A.verdict(g)
        rows.append({"id": tid, "suite": t["_suite"], "fn": fn, "fn_src": src,
                     "verdict": v, "tally": g["tally"], "rc": g["rc"]})
    drop = [r for r in rows if r["verdict"] == "DROP"]
    abst = [r for r in rows if r["verdict"] == "ABSTAIN"]
    print("=" * 78)
    print(f"参考解过同一道闸：{len(rows)} 题　DROP {len(drop)}　KEEP "
          f"{len([r for r in rows if r['verdict'] == 'KEEP'])}　ABSTAIN {len(abst)}")
    print("=" * 78)
    for r in rows:
        t = r["tally"] or {}
        print(f"  {r['id']:<24}{r['suite']:<32}{r['verdict']:<8}"
              f"VARIES={t.get('VARIES', 0):<3}CONST={t.get('CONST', 0):<3}"
              f"STRUCT={sum(v for k, v in t.items() if k.startswith('STRUCT'))}")
    print(f"\n⭐ 参考解被筛掉 {len(drop)}/{len(rows)}"
          + (f" ⇒ 闸在**满分实现**上也会误杀：{[r['id'] for r in drop]}" if drop else " ⇒ 闸对参考解无误杀"))
    out = {"n": len(rows), "n_drop": len(drop), "n_abstain": len(abst),
           "dropped": [r["id"] for r in drop], "rows": rows}
    Path(__file__).resolve().parent.joinpath("oracle_control_v194.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
