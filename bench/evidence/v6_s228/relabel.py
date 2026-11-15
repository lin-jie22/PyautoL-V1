#!/usr/bin/env python3
"""V139 前置更正：V138 的三档分档用了错的名字，重判一遍。

⛔ 自踩：`v6_s227/taxonomy.py::owner_map()` 返回 `mw.<模块>.<n.name>`（**不带类名**），
而轨迹（`trace_run.py`）记的是 `__qualname__` 风格的 `mw.<模块>.<类>.<方法>`。
两边对不上 ⇒ 凡是被改函数是**方法**的站点，`diff == [owner]` 永远不成立
⇒ 那些「只有自己变了」的站被错误地归进 SPILL。

修法：owner 用 qualname（最近包容 def 的完整限定名）。只改命名，不改任何读数。
"""
from __future__ import annotations

import ast
import json
import pathlib
import re

EVID = pathlib.Path(__file__).resolve().parent
V138 = EVID.parent / "v6_s227"
BASE = pathlib.Path("/tmp/v1_base")
if not (BASE / "mw").exists():
    BASE = pathlib.Path("/Users/linshaojie/V1")


def qual_owner() -> dict[str, str]:
    out: dict[str, str] = {}
    for f in sorted((BASE / "mw").glob("*.py")):
        src = f.read_text()
        t = ast.parse(src)
        qn: dict[int, str] = {}

        def walk(node, prefix: str) -> None:
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    q = f"{prefix}.{ch.name}"
                    qn[id(ch)] = q
                    walk(ch, q)
                elif isinstance(ch, ast.ClassDef):
                    walk(ch, f"{prefix}.{ch.name}")
                else:
                    walk(ch, prefix)

        walk(t, f"mw.{f.stem}")
        defs = [m for m in ast.walk(t)
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for n in ast.walk(t):
            if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice):
                u = n.slice.upper
                if not (isinstance(u, ast.Constant) and isinstance(u.value, int)):
                    continue
                if len(re.findall(r"\[:\d+\]", src.splitlines()[n.lineno - 1])) != 1:
                    continue
                best = None
                for m in defs:
                    if m.lineno <= n.lineno <= (m.end_lineno or m.lineno):
                        if best is None or m.lineno > best.lineno:
                            best = m
                out[f"{f.name}:{n.lineno}"] = qn.get(id(best), "?")
    return out


def tier(r: dict, own: dict[str, str]) -> str:
    if not r["eff"]:
        return "NOOP"
    return "SELF_ONLY" if r["diff"] == [own.get(r["key"], "?")] else "SPILL"


def main() -> int:
    import sys
    sys.path.insert(0, str(V138))
    from taxonomy import owner_map as old_map

    new = qual_owner()
    old = old_map()
    rows = json.loads((V138 / "analysis.json").read_text())["rows"]

    diff_name = [k for k in old if k in new and old[k] != new[k]]
    print(f"命名不一致的站点: {len(diff_name)} / {len(old)}")
    for k in diff_name[:6]:
        print(f"  {k:18s} 旧={old[k]:40s} 新={new[k]}")

    from collections import Counter
    c_old, c_new = Counter(), Counter()
    flips = []
    for r in rows:
        a, b = tier(r, old), tier(r, new)
        c_old[a] += 1
        c_new[b] += 1
        if a != b:
            flips.append((r["key"], a, b, r["diff"]))

    print(f"\n=== 89 站三档：旧 vs 新（qualname）===")
    print(f"{'档':10s}{'旧':>6s}{'新':>6s}")
    for k in ("SPILL", "SELF_ONLY", "NOOP"):
        print(f"{k:10s}{c_old[k]:6d}{c_new[k]:6d}")
    print(f"\n翻转 {len(flips)} 站（全部 SPILL -> SELF_ONLY）:")
    for k, a, b, d in flips:
        print(f"  {k:18s} {a:>9s} -> {b:<9s} diff={d[:2]}")

    print("\n=== 按 V137 标签拆（新口径）===")
    for tag, want in (("COV", True), ("BLIND", False)):
        sub = [r for r in rows if r["cov"] is want]
        c = Counter(tier(r, new) for r in sub)
        print(f"  {tag:6s} n={len(sub):3d}  SPILL={c['SPILL']:3d} "
              f"SELF_ONLY={c['SELF_ONLY']:3d} NOOP={c['NOOP']:3d}")

    (EVID / "tiers.json").write_text(json.dumps(
        {"owner": new,
         "tiers": {r["key"]: tier(r, new) for r in rows},
         "old_counts": dict(c_old), "new_counts": dict(c_new),
         "flips": [{"key": k, "old": a, "new": b} for k, a, b, _ in flips]},
        ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
