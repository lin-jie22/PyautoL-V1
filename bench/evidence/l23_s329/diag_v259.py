#!/usr/bin/env python3
"""V259 diagnostic (0 model calls, 0 production changes).

For each of the 4 cells whose gate never rejected (g7t2 / s02 / m02 / m03):
  1. dump the delivered answer verbatim
  2. check delivery format (FILES dict present? keys as asked?)
  3. run every suite test in a fresh subprocess, record pass/fail + failing line
No model is called; the delivered answers come from V258 arm b answers.jsonl.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

V258 = Path("/tmp/v258_b/bench/answers.jsonl")
SUITE = Path("/tmp/v253_suite/low.jsonl")
OUT = Path(__file__).resolve().parent
CELLS = ["g7t2_parse_pairs", "s02_limiter", "m02_vfs", "m03_tiered"]

FENCE = re.compile(r"^\s*```[a-zA-Z0-9_+-]*\s*$", re.M)


def strip_fence(s: str) -> str:
    lines = s.splitlines()
    out, i = [], 0
    while i < len(lines):
        if FENCE.match(lines[i]):
            i += 1
            while i < len(lines) and not FENCE.match(lines[i]):
                out.append(lines[i])
                i += 1
            i += 1
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def load_answers():
    by = {}
    for line in V258.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        r = json.loads(line)
        if r.get("task_id"):
            by[r["task_id"]] = r
    return by


def load_suite():
    by = {}
    for line in SUITE.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        by[r["id"]] = r
    return by


def has_files_dict(code: str):
    return bool(re.search(r"^\s*FILES\s*=", code, re.M))


RUNNER = r"""
import sys, os, json, traceback
cell = sys.argv[1]
codefile = sys.argv[2]
testfile = sys.argv[3]
code = open(codefile, encoding='utf-8').read()
test = open(testfile, encoding='utf-8').read()
td = sys.argv[4]
os.chdir(td)
sys.path.insert(0, td)
g = {'__name__': '__diag__'}
try:
    exec(compile(code, '<code>', 'exec'), g)
    exec(compile(test, '<test>', 'exec'), g)
    print(json.dumps({'ok': True}))
except BaseException as e:
    tb = traceback.extract_tb(sys.exc_info()[2])
    last = tb[-1] if tb else None
    info = {
        'ok': False,
        'etype': type(e).__name__,
        'emsg': str(e)[:300],
        'line': (last.lineno if last else None),
        'src': (last.line if last else None),
    }
    print(json.dumps(info))
"""


def run_one(code: str, test: str, cell: str):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "code.py").write_text(code, encoding="utf-8")
        (td / "test.py").write_text(test, encoding="utf-8")
        (td / "runner.py").write_text(RUNNER, encoding="utf-8")
        p = subprocess.run(
            [sys.executable, str(td / "runner.py"), cell,
             str(td / "code.py"), str(td / "test.py"), str(td)],
            capture_output=True, text=True, timeout=120,
        )
        txt = (p.stdout or "").strip().splitlines()
        if not txt:
            return {"ok": False, "etype": "NoOutput", "emsg": (p.stderr or "")[-300:],
                    "line": None, "src": None}
        try:
            return json.loads(txt[-1])
        except Exception:
            return {"ok": False, "etype": "BadOutput", "emsg": txt[-1][:300],
                    "line": None, "src": None}


def main():
    answers = load_answers()
    suite = load_suite()
    (OUT / "answers_b").mkdir(exist_ok=True)

    report = {}
    for cell in CELLS:
        raw = answers[cell]["answer"]
        code = strip_fence(raw)
        (OUT / "answers_b" / f"{cell}.py").write_text(code, encoding="utf-8")
        tests = suite[cell]["tests"]
        rows = []
        for i, t in enumerate(tests):
            r = run_one(code, t, cell)
            r["test_idx"] = i
            r["test_head"] = t.strip().splitlines()[0][:120]
            rows.append(r)
        npass = sum(1 for r in rows if r["ok"])
        report[cell] = {
            "chars_raw": len(raw),
            "chars_code": len(code),
            "has_files": has_files_dict(code),
            "n_tests": len(tests),
            "n_pass": npass,
            "tests": rows,
        }
        print(f"== {cell}: pass {npass}/{len(tests)}  has_FILES={has_files_dict(code)} "
              f"chars={len(code)}")
        for r in rows:
            if not r["ok"]:
                print(f"   FAIL#{r['test_idx']} line={r['line']} {r['etype']}: {r['emsg'][:120]}")
                if r["src"]:
                    print(f"      src: {r['src'][:120]}")

    (OUT / "diag_v259.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT / "diag_v259.json")


if __name__ == "__main__":
    main()
