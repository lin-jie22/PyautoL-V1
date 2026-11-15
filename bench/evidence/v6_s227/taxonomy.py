#!/usr/bin/env python3
"""V138 分类：EFFECT 再分三档，因为「改了」有三种很不一样的后果。

NOOP      —— 轨迹一格没变（变更所在代码没被执行）
SELF_ONLY —— 只有被改函数自己的返回值变了，下游一格没动（值变了，后果没变）
SPILL     —— 下游也跟着变了（真的影响到了别的东西）
"""
from __future__ import annotations

import ast
import json
import re
from collections import Counter
from pathlib import Path

EVID = Path(__file__).resolve().parent
BASE = Path("/tmp/v1_base")


def owner_map() -> dict[str, str]:
    out = {}
    for f in sorted((BASE / "mw").glob("*.py")):
        src = f.read_text()
        tree = ast.parse(src)
        funcs = [(n.name, n.lineno, n.end_lineno or n.lineno)
                 for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for n in ast.walk(tree):
            if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice):
                u = n.slice.upper
                if isinstance(u, ast.Constant) and isinstance(u.value, int):
                    if len(re.findall(r"\[:\d+\]", src.splitlines()[n.lineno - 1])) == 1:
                        fn = next((nm for nm, a, b in funcs if a <= n.lineno <= b), "?")
                        out[f"{f.name}:{n.lineno}"] = f"mw.{f.name[:-3]}.{fn}"
    return out


def main() -> int:
    own = owner_map()
    rows = json.loads((EVID / "analysis.json").read_text())["rows"]
    cnt = Counter()
    buckets = {"NOOP": [], "SELF_ONLY": [], "SPILL": []}
    for r in rows:
        if not r["eff"]:
            buckets["NOOP"].append(r)
            cnt["NOOP"] += 1
            continue
        o = own.get(r["key"], "?")
        if r["diff"] == [o]:
            buckets["SELF_ONLY"].append(r)
            cnt["SELF_ONLY"] += 1
        else:
            buckets["SPILL"].append(r)
            cnt["SPILL"] += 1
    print("=== 89 站（v=2）三档 ===")
    for k in ("SPILL", "SELF_ONLY", "NOOP"):
        print(f"  {k:10s} {cnt[k]:3d}  ({cnt[k]/len(rows)*100:.1f}%)")

    lab = {r["key"]: r["cov"] for r in rows}
    print("\n=== 按 V137 标签拆 ===")
    for tag, want in (("COV", True), ("BLIND", False)):
        sub = [r for r in rows if r["cov"] is want]
        c = Counter()
        for r in sub:
            if not r["eff"]:
                c["NOOP"] += 1
            elif r["diff"] == [own.get(r["key"], "?")]:
                c["SELF_ONLY"] += 1
            else:
                c["SPILL"] += 1
        print(f"  {tag:6s} n={len(sub):3d}  SPILL={c['SPILL']:3d} SELF_ONLY={c['SELF_ONLY']:3d} NOOP={c['NOOP']:3d}")

    print("\n=== SELF_ONLY 明细（值变了、后果没变）===")
    for r in buckets["SELF_ONLY"]:
        print(f"  {r['key']:20s} 判据={r['verdict']:6s} 仅 {own.get(r['key'])}")

    print("\n=== SPILL 明细 ===")
    for r in buckets["SPILL"]:
        print(f"  {r['key']:20s} 判据={r['verdict']:6s} {len(r['diff'])} 个函数 {r['diff'][:2]}")

    # Part B
    pb = []
    for line in (EVID / "readings.jsonl").read_text().splitlines():
        d = json.loads(line)
        if "value" in d:
            pb.append(d)
    print("\n=== Part B（单点剖面）===")
    print(f"{'站点':18s}{'值':>6s}{'判据':>8s}{'档':>10s}  差异函数")
    for d in sorted(pb, key=lambda x: (x["site"][0], x["value"])):
        mod, ln, orig = d["site"]
        key = f"{mod}:{ln}"
        o = own.get(key, "?")
        if not d["effect"]:
            g = "NOOP"
        elif d["diff_fns"] == [o]:
            g = "SELF_ONLY"
        else:
            g = "SPILL"
        print(f"{key:18s}{d['value']:6d}{d['verdict']:>8s}{g:>10s}  {d['diff_fns'][:3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
