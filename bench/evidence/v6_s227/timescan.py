#!/usr/bin/env python3
"""V138 第三个装置缺陷：时间量（静态求集合，不靠挑）。

`mw.mutate.record` 的返回值里带 `{"ts": round(time.time(), 3)}`。
两次运行的 ts 落在不同的取整桶里 ⇒ 轨迹差一个函数 ⇒ 一次抽查里 **2/5 站从 NOOP 翻成 EFFECT**。

⛔ 不许「看到哪站翻转就把哪个函数拉黑」（那是拿结果定装置）。
所以改成**静态**规则：凡源码里引用时间源的函数，一律出局。
（传播的（只调 `elapsed()` 的）由空转差分自动求得，两者取并集。）
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

BASE = Path("/tmp/v1_base")
EVID = Path(__file__).resolve().parent
TIME_CALLS = {"time", "monotonic", "perf_counter", "process_time", "now", "utcnow"}


def main() -> int:
    raw: set[tuple[str, str]] = set()
    for f in sorted((BASE / "mw").glob("*.py")):
        mod = f.name[:-3]
        tree = ast.parse(f.read_text())
        funcs = [(n.name, n.lineno, n.end_lineno or n.lineno)
                 for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            fn = n.func
            nm = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else None)
            if nm not in TIME_CALLS:
                continue
            owner = None
            for nm2, a, b in funcs:
                if a <= n.lineno <= b and (owner is None or a > owner[1]):
                    owner = (nm2, a)
            if owner:
                raw.add((mod, owner[0]))

    keys = list(json.loads((EVID / "baseline.json").read_text())["base_per_fn"])
    hit = sorted({k for k in keys
                  for mod, fn in raw
                  if k.startswith(f"mw.{mod}.") and k.split(".")[-1] == fn})
    print(f"源码引用时间源的函数: {len(raw)} 个 → 轨迹中命中 {len(hit)} 个")
    for k in hit:
        print("   ", k)
    (EVID / "time_set.json").write_text(json.dumps(
        {"raw": sorted(f"mw.{m}.{x}" for m, x in raw), "hit": hit}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
