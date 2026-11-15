#!/usr/bin/env python3
"""V138 扫描：89 个切片站点 × 真实负载轨迹。

阶段：
  base   —— 4 次空转（2 次求噪声名单，2 次带噪声名单验确定性）
  sites  —— 89 站 × v=2，与 V137 同一批站点、同一个变异值（分母同源）
  partb  —— 两个站点 × 多个值，问「判据绿的值是不是行为上也等于原值」

判定：
  EFFECT  ⇔ digest_clean ≠ baseline_clean
  NOOP    ⇔ digest_clean == baseline_clean
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = Path("/tmp/v1_base")
EVID = Path(__file__).resolve().parent
TRACE = EVID / "trace_run.py"
MK = "/tmp/mkworker.sh"
WORKERS = [Path(f"/tmp/v1_sw{i}") for i in range(6)]
STATE = EVID / "readings.jsonl"

_pool: queue.Queue = queue.Queue()
for w in WORKERS:
    _pool.put(w)
_lock = threading.Lock()


def sites() -> list[tuple[str, int, int, str]]:
    out = []
    for f in sorted((BASE / "mw").glob("*.py")):
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
                        fn = next((nm for nm, a, b in funcs if a <= x.lineno <= b), "?")
                        out.append((f.name, x.lineno, u.value, fn))
    return out


def reset(worker: Path) -> None:
    for p in (BASE / "mw").glob("*.py"):
        shutil.copy2(p, worker / "mw" / p.name)
    for junk in (worker / "mw").glob("__pycache__"):
        shutil.rmtree(junk, ignore_errors=True)


def mutate(worker: Path, mod: str, lineno: int, orig: int, new: int) -> None:
    p = worker / "mw" / mod
    src = p.read_text()
    lines = src.splitlines(keepends=True)
    i = lineno - 1
    if lines[i].count(f"[:{orig}]") != 1:
        raise RuntimeError(f"站点不唯一 {mod}:{lineno} 命中数={lines[i].count(f'[:{orig}]')}")
    lines[i] = lines[i].replace(f"[:{orig}]", f"[:{new}]")
    p.write_text("".join(lines))


def run_one(tag: str, mut: tuple | None, noise: str) -> dict:
    w = _pool.get()
    try:
        reset(w)
        if mut:
            mutate(w, *mut)
        out = Path(f"/tmp/tr_{tag.replace(':','_').replace('/','_')}.json")
        if out.exists():
            out.unlink()
        env = dict(os.environ)
        env["TRACE_NOISE"] = noise
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        t0 = __import__("time").time()
        pr = subprocess.run([sys.executable, str(TRACE), str(w), str(out)],
                            cwd=str(w), env=env, capture_output=True, text=True, timeout=900)
        dt = __import__("time").time() - t0
        ms = re.findall(r"PASS=(\d+)\s+FAIL=(\d+)", pr.stdout)
        verdict = f"{ms[-1][0]}/{ms[-1][1]}" if ms else f"?rc{pr.returncode}"
        d = json.loads(out.read_text()) if out.exists() else {}
        rec = {"tag": tag, "mut": mut, "verdict": verdict, "exit": pr.returncode, "sec": round(dt, 1),
               "calls": d.get("n_calls"), "digest_clean": d.get("digest_clean"),
               "per_fn": d.get("per_fn")}
        return rec
    finally:
        _pool.put(w)


def diff_fns(rec: dict, base_per_fn: dict, drop: set[str]) -> list[str]:
    """⛔ V138 自踩：原来传的是 `bl["base_per_fn"]`（已是 per_fn 本身），
    函数内部又 `.get("per_fn")` ⇒ 拿到 None ⇒ 所有函数都被算成「变了」（diff=325）。"""
    a = rec.get("per_fn") or {}
    return sorted(k for k in a if k not in drop and a[k] != base_per_fn.get(k))


def main() -> int:
    stage = sys.argv[1] if len(sys.argv) > 1 else "base"
    for w in WORKERS:
        if not (w / "mw").exists():
            subprocess.run([MK, str(w)], check=True)

    if stage == "base":
        r1 = run_one("base1", None, "")
        r2 = run_one("base2", None, "")
        noise_auto = sorted(k for k in r1["per_fn"] if r1["per_fn"][k] != r2["per_fn"].get(k))
        # 预注册第 2 节点名的 6 个墙钟函数，与自动求得取并集
        PRE = ["mw.budget.Ledger.elapsed", "mw.budget.Ledger.snapshot", "mw.budget.Ledger.summary",
               "mw.core._LedgeredLLM._attempt_timeout", "mw.core._LedgeredLLM._left_s",
               "mw.core.answer_deadline"]
        noise = sorted(set(noise_auto) | set(PRE))
        print("空转间不同的函数（自动求得）:", noise_auto, flush=True)
        print("噪声名单（并集预注册 6 个墙钟）:", noise, flush=True)
        ns = ",".join(noise)
        r3 = run_one("base3", None, ns)
        r4 = run_one("base4", None, ns)
        ok = r3["digest_clean"] == r4["digest_clean"]
        # 用 r1/r2 的逐函数数据在剔除名单后重算，验证与 r3/r4 一致
        def clean_digest(rec):
            per = rec["per_fn"]
            import hashlib as H
            return H.sha1("\n".join(f"{k}\t{per[k]['n']}\t{per[k]['h']}"
                                    for k in sorted(per) if k not in noise).encode()).hexdigest()
        print(f"base3/base4 clean 相同: {ok}", flush=True)
        print(f"base1 clean == base3 clean: {clean_digest(r1)==r3['digest_clean']}", flush=True)
        out = {"noise": noise, "verdict": r3["verdict"], "calls": r3["calls"],
               "clean": r3["digest_clean"], "sec": r3["sec"],
               "deterministic": ok and clean_digest(r1) == r3["digest_clean"],
               "base_per_fn": r3["per_fn"]}
        (EVID / "baseline.json").write_text(json.dumps(out, ensure_ascii=False))
        print("落盘 baseline.json", flush=True)
        return 0

    bl = json.loads((EVID / "baseline.json").read_text())
    srcf = EVID / "src_set.json"
    src = set(json.loads(srcf.read_text())["src"]) if srcf.exists() else set()
    timf = EVID / "time_set.json"
    tim = set(json.loads(timf.read_text())["hit"]) if timf.exists() else set()
    drop = set(bl["noise"]) | src | tim
    base_per = bl["base_per_fn"]

    import hashlib as _H

    def _dg(per):
        return _H.sha1("\n".join(f"{k}\t{per[k]['n']}\t{per[k]['h']}"
                                 for k in sorted(per) if k not in drop).encode()).hexdigest()

    base_clean = _dg(base_per)
    print(f"剔除集合: 噪声{len(bl['noise'])} + 源码派生{len(src)} = {len(drop)}", flush=True)

    if stage == "sites":
        ss = sites()
        print(f"站点 {len(ss)}，v=2，6 路并行", flush=True)
        def job(s):
            mod, ln, orig, fn = s
            rec = run_one(f"{mod}:{ln}", (mod, ln, orig, 2), ",".join(bl["noise"]))
            rec["site"] = [mod, ln, orig, fn]
            rec["effect"] = _dg(rec["per_fn"] or {}) != base_clean
            rec["diff_fns"] = diff_fns(rec, base_per, drop)
            with _lock:
                with STATE.open("a") as fh:
                    fh.write(json.dumps({k: v for k, v in rec.items() if k != "per_fn"},
                                        ensure_ascii=False) + "\n")
            print(f"  {mod}:{ln} 原{orig} -> {rec['verdict']} "
                  f"{'EFFECT' if rec['effect'] else 'NOOP'} "
                  f"diff={len(rec['diff_fns'])} {rec['sec']}s", flush=True)
            return rec
        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(job, ss))
        return 0

    if stage == "recheck":
        ana = {r["key"]: r for r in json.loads((EVID / "analysis.json").read_text())["rows"]}
        origv = {f"{m}:{l}": o for m, l, o, _f in sites()}
        rows = [{"key": k, "effect": a["eff"], "mod": a["mod"], "ln": a["ln"]}
                for k, a in sorted(ana.items())]
        sample = rows[::18]         # 确定性抽样，不挑（1/18 = 5 站）
        agree = 0
        for r in sample:
            mod, ln = r["mod"], r["ln"]
            orig = origv[f"{mod}:{ln}"]
            rec = run_one(f"re_{mod}_{ln}", (mod, ln, orig, 2), ",".join(bl["noise"]))
            eff2 = _dg(rec["per_fn"] or {}) != base_clean
            same = (eff2 == r["effect"])
            agree += same
            print(f"  {mod}:{ln} 原{orig} 首判={'EFFECT' if r['effect'] else 'NOOP'} "
                  f"复判={'EFFECT' if eff2 else 'NOOP'} {'一致' if same else '⛔翻转'}",
                  flush=True)
        print(f"\n抽查 {len(sample)} 站，一致 {agree}/{len(sample)}", flush=True)
        (EVID / "recheck.json").write_text(json.dumps(
            {"n": len(sample), "agree": agree,
             "keys": [r["key"] for r in sample]}, ensure_ascii=False))
        return 0

    if stage == "partb":
        plan = [("memory.py", 103, 24, [2, 8, 60, 200, 8000]),
                ("core.py", 423, 60, [2, 8, 24, 200, 8000])]
        jobs = [(mod, ln, orig, v) for mod, ln, orig, vals in plan for v in vals]

        def _one(j):
            mod, ln, orig, v = j
            rec = run_one(f"{mod}:{ln}@{v}", (mod, ln, orig, v), ",".join(bl["noise"]))
            rec["site"] = [mod, ln, orig]
            rec["value"] = v
            rec["effect"] = _dg(rec["per_fn"] or {}) != base_clean
            rec["diff_fns"] = diff_fns(rec, base_per, drop)
            with _lock:
                with STATE.open("a") as fh:
                    fh.write(json.dumps({k: vv for k, vv in rec.items() if k != "per_fn"},
                                        ensure_ascii=False) + "\n")
            print(f"  {mod}:{ln} 原{orig} -> [:{v}] {rec['verdict']} "
                  f"{'EFFECT' if rec['effect'] else 'NOOP'} diff={rec['diff_fns'][:4]}", flush=True)
            return rec
        with ThreadPoolExecutor(max_workers=5) as ex:
            list(ex.map(_one, jobs))
        return 0
        for mod, ln, orig, vals in plan:
            for v in vals:
                rec = run_one(f"{mod}:{ln}@{v}", (mod, ln, orig, v), ",".join(bl["noise"]))
                rec["site"] = [mod, ln, orig]
                rec["value"] = v
                rec["effect"] = _dg(rec["per_fn"] or {}) != base_clean
                rec["diff_fns"] = diff_fns(rec, base_per, drop)
                with _lock:
                    with STATE.open("a") as fh:
                        fh.write(json.dumps({k: vv for k, vv in rec.items() if k != "per_fn"},
                                            ensure_ascii=False) + "\n")
                print(f"  {mod}:{ln} 原{orig} -> [:{v}] {rec['verdict']} "
                      f"{'EFFECT' if rec['effect'] else 'NOOP'} diff={rec['diff_fns'][:4]}", flush=True)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
