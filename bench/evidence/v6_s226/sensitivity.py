#!/usr/bin/env python3
"""V137 装置：量「判据在每个位置的约束强度」。

方法（比 V136 更直接）：
  对位置 P（模块, 行号, 原值），把该处的 `a[:N]` 改成库内每一个值 v，跑判据。
    v == 原值   ⇒ 基线，必须绿
    v 很小     ⇒ 若被断言覆盖则红（这是「这个位置被判据管着」的证据）
    绿的比例    ⇒ 判据在该位置的**约束强度**。绿得越多，约束越弱。

⛔ 候选值全部来自库内 `a[:N]` 的实测分布，不是我写的表。
⛔ 不改动仓库本体：所有实验在 /tmp/v1_sw* 的独立副本里做。
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
N_WORKERS = 6
WORKERS = [Path(f"/tmp/v1_sw{i}") for i in range(N_WORKERS)]
_pool: queue.Queue = queue.Queue()
_lock = threading.Lock()
_log: list[dict] = []


def prep_workers() -> None:
    for w in WORKERS:
        if w.exists():
            shutil.rmtree(w)
        shutil.copytree(ORIG, w, ignore=shutil.ignore_patterns(
            "__pycache__", ".git", "state", "logs", "bench_out", "build", "*.egg-info"))
        _pool.put(w)


def library_values() -> list[int]:
    seen = {}
    for f in (ORIG / "mw").glob("*.py"):
        for x in ast.walk(ast.parse(f.read_text())):
            if isinstance(x, ast.Subscript) and isinstance(x.slice, ast.Slice):
                u = x.slice.upper
                if isinstance(u, ast.Constant) and isinstance(u.value, int):
                    seen[u.value] = seen.get(u.value, 0) + 1
    return sorted(seen, key=lambda v: (-seen[v], v))


def restore(w: Path) -> None:
    for p in (ORIG / "mw").glob("*.py"):
        shutil.copy2(p, w / "mw" / p.name)


def set_value(w: Path, modfile: str, lineno: int, value: int) -> bool:
    p = w / "mw" / modfile
    lines = p.read_text().splitlines()
    if lineno - 1 >= len(lines):
        return False
    line = lines[lineno - 1]
    if len(re.findall(r"\[:\d+\]", line)) != 1:
        return False
    lines[lineno - 1] = re.sub(r"\[:\d+\]", f"[:{value}]", line)
    p.write_text("\n".join(lines) + "\n")
    return True


def run(w: Path) -> tuple[int, int]:
    for d in w.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)
    env = dict(os.environ)
    for k in ("V1_STATE_DIR", "V1_LOG_DIR", "V1_WORKSPACE", "V1_LLM_OFFLINE"):
        env.pop(k, None)
    p = subprocess.run([sys.executable, "tests/contract_test.py"], cwd=str(w),
                       capture_output=True, text=True, timeout=600, env=env)
    out = p.stdout + p.stderr
    for line in reversed(out.splitlines()):
        m = re.search(r"PASS=(\d+)\s+FAIL=(\d+)", line)
        if m:
            return int(m.group(1)), int(m.group(2))
    return -1, -1


def one(modfile: str, lineno: int, value: int, tag: str) -> dict:
    w = _pool.get()
    try:
        restore(w)
        ok = set_value(w, modfile, lineno, value)
        if not ok:
            return dict(tag=tag, modfile=modfile, lineno=lineno, value=value, skipped=True)
        np_, nf = run(w)
        green = nf == 0
        rec = dict(tag=tag, modfile=modfile, lineno=lineno, value=value,
                   npass=np_, nfail=nf, green=green)
        print(f"  {modfile}:{lineno} [:{value}] -> {'绿' if green else '红 ' + str(nf)}", flush=True)
        return rec
    finally:
        _pool.put(w)


def sites() -> list[tuple[str, int, int]]:
    out = []
    for f in sorted((ORIG / "mw").glob("*.py")):
        for x in ast.walk(ast.parse(f.read_text())):
            if isinstance(x, ast.Subscript) and isinstance(x.slice, ast.Slice):
                u = x.slice.upper
                if isinstance(u, ast.Constant) and isinstance(u.value, int):
                    lines = f.read_text().splitlines()
                    if x.lineno - 1 < len(lines) and len(re.findall(r"\[:\d+\]", lines[x.lineno - 1])) == 1:
                        out.append((f.name, x.lineno, u.value))
    return out


def main() -> None:
    prep_workers()
    all_sites = sites()
    vals = library_values()
    print(f"库内互异值 {len(vals)} 个：{vals}\n", flush=True)

    # 阶段 1：筛出「把值改小能被抓住」的站点（覆盖证明）
    # 用同一个模块选一个代表：按模块去重，每个模块取第一个
    bymod: dict[str, tuple[str, int, int]] = {}
    for s in all_sites:
        bymod.setdefault(s[0], s)
    cands = list(bymod.values())[:8]
    print(f"阶段1：{len(cands)} 个候选站点，测 v=2 是否被抓住", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        r1 = list(ex.map(lambda s: one(s[0], s[1], 2, "probe"), cands))
    live = [r for r in r1 if not r.get("skipped") and r["nfail"] > 0]
    print(f"⇒ {len(live)}/{len(cands)} 个被抓住（{time.time()-t0:.0f}s）\n", flush=True)
    _log.extend(r1)

    if not live:
        print("⛔ 没有任何位置的改动被判据抓住 —— 装置无效")
        return

    # 阶段 2：对活站点扫全部库内值
    live = live[:4]
    jobs = [(r["modfile"], r["lineno"], v) for r in live for v in vals]
    print(f"阶段2：{len(live)} 个站点 × {len(vals)} 个值 = {len(jobs)} 次判分 "
          f"（{N_WORKERS} 路并行）", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        r2 = list(ex.map(lambda j: one(j[0], j[1], j[2], "scan"), jobs))
    _log.extend(r2)
    print(f"⇒ 完成（{time.time()-t0:.0f}s = {(time.time()-t0)/60:.1f} 分）", flush=True)

    # 汇总
    print("\n" + "=" * 70)
    print("判据约束强度剖面")
    print("=" * 70)
    summary = []
    for r in live:
        mod, ln, orig = r["modfile"], r["lineno"], r["value"]
        # 原值从 all_sites 取
        orig_val = next((s[2] for s in all_sites if s[0] == mod and s[1] == ln), None)
        rows = [x for x in r2 if x["modfile"] == mod and x["lineno"] == ln and not x.get("skipped")]
        greens = sorted(x["value"] for x in rows if x["green"])
        reds = sorted(x["value"] for x in rows if not x["green"])
        n = len(rows)
        rate = len(greens) / n if n else 0
        print(f"\n{mod}:{ln}  原值={orig_val}  扫了 {n} 个值")
        print(f"  绿（约束不到）：{greens}")
        print(f"  红（判据管着）：{reds}")
        print(f"  ⇒ 可自由取值比例 = {len(greens)}/{n} = {rate:.0%}")
        if greens:
            print(f"  绿区范围: {min(greens)} ~ {max(greens)}；绿值中位数 {sorted(greens)[len(greens)//2]}")
        summary.append(dict(modfile=mod, lineno=ln, orig=orig_val, n=n,
                            greens=greens, reds=reds, green_rate=round(rate, 4),
                            green_median=sorted(greens)[len(greens)//2] if greens else None))

    out = Path(__file__).parent / "sensitivity.json"
    out.write_text(json.dumps(dict(values=vals, summary=summary, events=_log),
                              ensure_ascii=False, indent=1))
    print(f"\n落盘 {out}")


if __name__ == "__main__":
    main()
