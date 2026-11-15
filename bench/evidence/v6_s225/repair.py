#!/usr/bin/env python3
"""V136 主装置：定位 → 二级定位 → 库内值搜索 → 修好。

三级：
  A 模块级二分   : 23 个文件里定位到 1 个          (log2 23 ≈ 5 次)
  B 函数级二分   : 该模块的 top-level 定义里定位    (log2 k 次)
  C 库内值搜索   : 该函数内每个「切片上界」站点     (28 个候选/站点)
                   的候选值**全部来自库内 a[:N] 的实测分布**，不是我写的表

⛔ 全程不回滚到原件来「修复」：A/B 里的回滚只用来**定位**，
   最终修复必须由 C 的搜索给出，且修复值必须来自库内。
⛔ 每次跑前清 __pycache__（假绿第一来源）。
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ORIG = Path("/tmp/v1_orig")
MUT = Path("/tmp/v1_mut")
WORK = Path("/tmp/v1_work")
EV: list[dict] = []


# ---------- 环境 ----------
def prep() -> None:
    for d in (MUT, WORK):
        if d.exists():
            shutil.rmtree(d)
    shutil.copytree(ORIG, MUT, ignore=shutil.ignore_patterns(
        "__pycache__", ".git", "state", "logs", "bench_out", "build", "*.egg-info"))
    shutil.copytree(MUT, WORK)
    clear_pycache()


def clear_pycache() -> None:
    for d in WORK.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def reset_work(mutated: bool = True) -> None:
    """把 mw/*.py 恢复成变异版或原件版，只拷 23 个文件（快）。"""
    src = MUT if mutated else ORIG
    for p in (src / "mw").glob("*.py"):
        shutil.copy2(p, WORK / "mw" / p.name)


def run_contract() -> tuple[int, int, float]:
    clear_pycache()
    env = dict(os.environ)
    for k in ("V1_STATE_DIR", "V1_LOG_DIR", "V1_WORKSPACE", "V1_LLM_OFFLINE"):
        env.pop(k, None)
    t0 = time.time()
    p = subprocess.run([sys.executable, "tests/contract_test.py"], cwd=str(WORK),
                       capture_output=True, text=True, timeout=600, env=env)
    out = p.stdout + p.stderr
    npass = nfail = -1
    for line in reversed(out.splitlines()):
        m = re.search(r"PASS=(\d+)\s+FAIL=(\d+)", line)
        if m:
            npass, nfail = int(m.group(1)), int(m.group(2))
            break
    return npass, nfail, round(time.time() - t0, 1)


def ev(**kw) -> None:
    EV.append(kw)
    print("  " + json.dumps(kw, ensure_ascii=False), flush=True)


# ---------- A 模块级二分 ----------
def bisect_modules(mod: str) -> tuple[list[str], int]:
    cand = sorted(p.name for p in (ORIG / "mw").glob("*.py"))
    n = 0
    while len(cand) > 1:
        half = len(cand) // 2
        group = cand[:half]
        reset_work(True)
        for m in group:
            shutil.copy2(ORIG / "mw" / m, WORK / "mw" / m)
        npass, nfail, dt = run_contract()
        n += 1
        green = nfail == 0
        ev(stage="A", n=n, group_size=len(group), npass=npass, nfail=nfail, green=green, secs=dt)
        cand = group if green else cand[half:]
    return cand, n


# ---------- B 函数级二分 ----------
def top_defs(path: Path) -> list[tuple[str, int, int]]:
    t = ast.parse(path.read_text())
    return [(n.name, n.lineno, n.end_lineno) for n in t.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]


def restore_defs(modfile: str, names: set[str]) -> int:
    """把工作副本里 names 这些 top-level 定义的源码，从原件覆盖回去。"""
    op, wp = ORIG / "mw" / modfile, WORK / "mw" / modfile
    o_lines = op.read_text().splitlines()
    o_defs = {nm: (ln, en) for nm, ln, en in top_defs(op)}
    w_src = wp.read_text()
    w_lines = w_src.splitlines()
    edits = []
    for nm, ln, en in top_defs(wp):
        if nm in names and nm in o_defs:
            oln, oen = o_defs[nm]
            edits.append((ln, en, o_lines[oln - 1:oen]))
    for ln, en, new in sorted(edits, reverse=True):
        w_lines[ln - 1:en] = new
    wp.write_text("\n".join(w_lines) + "\n")
    return len(edits)


def bisect_defs(modfile: str) -> tuple[list[str], int]:
    cand = [nm for nm, _, _ in top_defs(MUT / "mw" / modfile)]
    n = 0
    if len(cand) <= 1:
        return cand, n
    while len(cand) > 1:
        half = len(cand) // 2
        group = cand[:half]
        reset_work(True)
        touched = restore_defs(modfile, set(group))
        npass, nfail, dt = run_contract()
        n += 1
        green = nfail == 0
        ev(stage="B", n=n, group=group, touched=touched, npass=npass, nfail=nfail,
           green=green, secs=dt)
        cand = group if green else cand[half:]
    return cand, n


# ---------- C 库内值搜索 ----------
def slice_sites(modfile: str, func: str) -> list[tuple[int, int]]:
    """返回该函数内 (行号, 当前上界值) 的列表。"""
    t = ast.parse((WORK / "mw" / modfile).read_text())
    out = []
    for n in ast.walk(t):
        if isinstance(n, ast.FunctionDef) and n.name == func:
            for x in ast.walk(n):
                if isinstance(x, ast.Subscript) and isinstance(x.slice, ast.Slice):
                    u = x.slice.upper
                    if isinstance(u, ast.Constant) and isinstance(u.value, int):
                        out.append((x.lineno, u.value))
    return out


def library_values() -> list[int]:
    """★ 候选值 100% 来自库内 a[:N] 的实测分布（含出现次数，按频次降序）。"""
    import collections
    c = collections.Counter()
    for f in (ORIG / "mw").glob("*.py"):
        for x in ast.walk(ast.parse(f.read_text())):
            if isinstance(x, ast.Subscript) and isinstance(x.slice, ast.Slice):
                u = x.slice.upper
                if isinstance(u, ast.Constant) and isinstance(u.value, int):
                    c[u.value] += 1
    return [v for v, _ in c.most_common()]


def set_slice_upper(modfile: str, lineno: int, newval: int) -> bool:
    """把第 lineno 行唯一的 [:N] 改成 [:newval]（行内必须恰好一个）。"""
    wp = WORK / "mw" / modfile
    lines = wp.read_text().splitlines()
    line = lines[lineno - 1]
    pat = re.compile(r"\[:(\d+)\]")
    hits = pat.findall(line)
    if len(hits) != 1:
        return False
    lines[lineno - 1] = pat.sub(f"[:{newval}]", line)
    wp.write_text("\n".join(lines) + "\n")
    return True


def main() -> None:
    mod, old, new = "memory.py", "toks[:24]", "toks[:2]"
    prep()
    # 注入
    p = MUT / "mw" / mod
    s = p.read_text()
    assert s.count(old) == 1
    p.write_text(s.replace(old, new))
    reset_work(True)
    npass, nfail, dt = run_contract()
    ev(stage="0", what="注入变异", npass=npass, nfail=nfail, secs=dt)
    if nfail <= 0:
        print("⛔ 变异无覆盖，装置无效")
        return

    # A
    cand_mod, na = bisect_modules(mod)
    ev(stage="A_done", located=cand_mod, evals=na)
    located = cand_mod[0]

    # B
    cand_def, nb = bisect_defs(located)
    ev(stage="B_done", located_def=cand_def, evals=nb)
    func = cand_def[0] if cand_def else None

    # C：在该函数内枚举切片站点 × 库内值
    reset_work(True)
    sites = slice_sites(located, func)
    vals = library_values()
    ev(stage="C_plan", func=func, sites=sites, n_vals=len(vals), vals=vals)

    nc = 0
    fixed_by = None
    for (ln, cur) in sites:
        for v in vals:
            if v == cur:
                continue
            reset_work(True)
            if not set_slice_upper(located, ln, v):
                ev(stage="C", skip="行内切片不唯一", lineno=ln)
                continue
            npass, nfail, dt = run_contract()
            nc += 1
            if nfail == 0:
                ev(stage="C", n=nc, lineno=ln, value=v, npass=npass, nfail=nfail,
                   secs=dt, RESULT="GREEN")
                fixed_by = (ln, v)
                break
            if nc % 5 == 0:
                ev(stage="C", n=nc, lineno=ln, value=v, npass=npass, nfail=nfail, secs=dt)
        if fixed_by:
            break

    total = 1 + na + nb + nc
    summary = dict(mod=mod, located=located, func=func, fixed_by=fixed_by,
                   evals=dict(inject=1, A=na, B=nb, C=nc, total=total),
                   brute_force_candidates=6034,
                   brute_force_hours=round(6034 * 46 / 3600, 1),
                   our_minutes=round(total * 46 / 60, 1))
    print("\n" + "=" * 60)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    (Path(__file__).parent / "repair.json").write_text(
        json.dumps(dict(summary=summary, events=EV), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
