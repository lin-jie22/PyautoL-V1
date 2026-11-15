#!/usr/bin/env python3
"""V137 第二段：判据对 96 个切片站点的覆盖率。

问一个更基本的问题：631 条断言到底管着 mw/ 里多少个位置？

方法：对每个「整数上界切片」站点，把值改成 2，跑判据。
  红 ⇒ 这个位置被判据覆盖
  绿 ⇒ 判据对这里完全不说话

复用 /tmp/v1_sw* 已建好的 worker（不重新拷贝）。
"""
from __future__ import annotations

import ast
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ORIG = Path("/tmp/v1_orig")
WORKERS = [Path(f"/tmp/v1_sw{i}") for i in range(6)]
_pool: queue.Queue = queue.Queue()
for w in WORKERS:
    _pool.put(w)
_res: list[dict] = []
_rl = threading.Lock()


def sites() -> list[tuple[str, int, int]]:
    out = []
    for f in sorted((ORIG / "mw").glob("*.py")):
        src = f.read_text()
        lines = src.splitlines()
        tree = ast.parse(src)
        funcs = [(n.name, n.lineno, n.end_lineno or n.lineno)
                 for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for x in ast.walk(tree):
            if isinstance(x, ast.Subscript) and isinstance(x.slice, ast.Slice):
                u = x.slice.upper
                if isinstance(u, ast.Constant) and isinstance(u.value, int):
                    if x.lineno - 1 < len(lines) and len(re.findall(r"\[:\d+\]", lines[x.lineno - 1])) == 1:
                        fn = next((nm for nm, a, b in funcs if a <= x.lineno <= b), None)
                        out.append((f.name, x.lineno, u.value, fn))
    return out


def one(s) -> dict:
    mod, ln, orig, fn = s
    w = _pool.get()
    try:
        for p in (ORIG / "mw").glob("*.py"):
            shutil.copy2(p, w / "mw" / p.name)
        p = w / "mw" / mod
        lines = p.read_text().splitlines()
        lines[ln - 1] = re.sub(r"\[:\d+\]", "[:2]", lines[ln - 1])
        p.write_text("\n".join(lines) + "\n")
        for d in w.rglob("__pycache__"):
            shutil.rmtree(d, ignore_errors=True)
        env = dict(os.environ)
        for k in ("V1_STATE_DIR", "V1_LOG_DIR", "V1_WORKSPACE", "V1_LLM_OFFLINE"):
            env.pop(k, None)
        pr = subprocess.run([sys.executable, "tests/contract_test.py"], cwd=str(w),
                            capture_output=True, text=True, timeout=600, env=env)
        out = pr.stdout + pr.stderr
        np_ = nf = -1
        for line in reversed(out.splitlines()):
            m = re.search(r"PASS=(\d+)\s+FAIL=(\d+)", line)
            if m:
                np_, nf = int(m.group(1)), int(m.group(2))
                break
        rec = dict(mod=mod, lineno=ln, orig=orig, func=fn, npass=np_, nfail=nf,
                   covered=nf > 0)
        print(f"  {mod}:{ln} [:2] -> {'红 ' + str(nf) if nf > 0 else '绿'}", flush=True)
        return rec
    finally:
        _pool.put(w)


def main() -> None:
    ss = sites()
    print(f"站点总数 {len(ss)}，逐站测 v=2（6 路并行）", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(one, ss))
    dt = time.time() - t0
    cov = [r for r in res if r["covered"]]
    print("\n" + "=" * 66)
    print(f"覆盖 {len(cov)}/{len(res)} = {len(cov)/len(res):.1%}   （{dt/60:.1f} 分）")
    print("=" * 66)
    bymod: dict[str, list] = {}
    for r in res:
        bymod.setdefault(r["mod"], []).append(r)
    print(f"\n{'模块':20s} {'站点':>4s} {'被覆盖':>5s}")
    for m, rs in sorted(bymod.items(), key=lambda kv: -len(kv[1])):
        c = sum(1 for r in rs if r["covered"])
        print(f"{m:20s} {len(rs):4d} {c:5d}")
    print("\n被覆盖的站点：")
    for r in cov:
        print(f"  {r['mod']}:{r['lineno']}  原值={r['orig']}  {r['func']}")
    (Path(__file__).parent / "coverage.json").write_text(
        json.dumps(dict(total=len(res), covered=len(cov),
                        rate=round(len(cov)/len(res), 4), sites=res),
                   ensure_ascii=False, indent=1))
    print(f"\n落盘 coverage.json")


if __name__ == "__main__":
    main()
