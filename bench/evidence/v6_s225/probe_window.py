#!/usr/bin/env python3
"""V136 关键验证：判据对 `toks[:N]` 这个参数的敏感区间。

背景：repair.py 的 C 阶段第 1 次搜索就绿了，但修出来的值是 200，
而原值是 24。要么「200 在语义上也对」，要么「631 条断言覆盖不到这个参数」。
本脚本逐个试值，画出判据的判别力剖面。
"""
from __future__ import annotations
import json, os, re, shutil, subprocess, sys, time
from pathlib import Path

ORIG = Path("/tmp/v1_orig")
WORK = Path("/tmp/v1_work2")
LINE = 103
PROBE = [2, 3, 8, 12, 24, 60, 120, 200, 1000, 8000]


def prep():
    if WORK.exists():
        shutil.rmtree(WORK)
    shutil.copytree(ORIG, WORK, ignore=shutil.ignore_patterns(
        "__pycache__", ".git", "state", "logs", "bench_out", "build", "*.egg-info"))


def run():
    for d in WORK.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)
    env = dict(os.environ)
    for k in ("V1_STATE_DIR", "V1_LOG_DIR", "V1_WORKSPACE", "V1_LLM_OFFLINE"):
        env.pop(k, None)
    t0 = time.time()
    p = subprocess.run([sys.executable, "tests/contract_test.py"], cwd=str(WORK),
                       capture_output=True, text=True, timeout=600, env=env)
    out = p.stdout + p.stderr
    np_ = nf = -1
    for ln in reversed(out.splitlines()):
        m = re.search(r"PASS=(\d+)\s+FAIL=(\d+)", ln)
        if m:
            np_, nf = int(m.group(1)), int(m.group(2))
            break
    fails = [l.strip() for l in out.splitlines() if "\u274c" in l]
    return np_, nf, fails, round(time.time() - t0, 1)


def setv(v: int):
    p = WORK / "mw" / "memory.py"
    lines = p.read_text().splitlines()
    old = lines[LINE - 1]
    assert len(re.findall(r"\[:(\d+)\]", old)) == 1, old
    lines[LINE - 1] = re.sub(r"\[:\d+\]", f"[:{v}]", old)
    p.write_text("\n".join(lines) + "\n")


def main():
    prep()
    rows = []
    # 先跑原件基线
    np_, nf, fails, dt = run()
    rows.append(dict(value=None, note="原始 24", npass=np_, nfail=nf, secs=dt, fails=fails[:6]))
    print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    for v in PROBE:
        setv(v)
        np_, nf, fails, dt = run()
        rows.append(dict(value=v, npass=np_, nfail=nf, secs=dt, fails=fails[:6]))
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    out = Path(__file__).parent / "window_probe.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print("\n判据敏感区间:")
    for r in rows:
        mark = "绿" if r["nfail"] == 0 else "红"
        print(f"  [:{r['value']}]  {mark}  PASS={r['npass']} FAIL={r['nfail']}")
    print(f"\n落盘 {out}")


if __name__ == "__main__":
    main()
