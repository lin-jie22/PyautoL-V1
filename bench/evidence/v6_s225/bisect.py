#!/usr/bin/env python3
"""V136 前置测量：黑盒二分能不能定位被变异的模块。

装置：
  /tmp/v1_orig    = git HEAD 的干净副本（原件）
  /tmp/v1_work    = 每次实验的工作副本（注入变异 / 回滚模块）

二分逻辑（不需要任何「断言→模块」映射表）：
  把候选模块集合 S 的一半从原件覆盖过去，跑契约。
    变绿  ⇒ 变异在 S 里（被我们修好了）
    不绿  ⇒ 变异在 S 的补集里（我们没碰到它）

⛔ 每次跑之前必须清 __pycache__，否则回滚被 .pyc 顶掉（这是本装置最大的假绿来源）。
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ORIG = Path("/tmp/v1_orig")
WORK = Path("/tmp/v1_work")
MODS = sorted(p.name for p in (ORIG / "mw").glob("*.py"))


def fresh_work() -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    shutil.copytree(ORIG, WORK, ignore=shutil.ignore_patterns(
        "__pycache__", ".git", "state", "logs", "bench_out", "build", "*.egg-info"))


def clear_pycache() -> None:
    for d in WORK.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def run_contract(timeout: int = 300) -> tuple[int, int, str]:
    """返回 (PASS, FAIL, 失败行摘要)。"""
    clear_pycache()
    env = dict(os.environ)
    for k in ("V1_STATE_DIR", "V1_LOG_DIR", "V1_WORKSPACE", "V1_LLM_OFFLINE"):
        env.pop(k, None)
    t0 = time.time()
    p = subprocess.run([sys.executable, "tests/contract_test.py"],
                       cwd=str(WORK), capture_output=True, text=True, timeout=timeout, env=env)
    out = p.stdout + p.stderr
    npass = nfail = -1
    for line in reversed(out.splitlines()):
        if "PASS=" in line and "FAIL=" in line:
            import re
            m = re.search(r"PASS=(\d+)\s+FAIL=(\d+)", line)
            if m:
                npass, nfail = int(m.group(1)), int(m.group(2))
                break
    fails = [l.strip() for l in out.splitlines() if l.strip().startswith("\u274c")]
    return npass, nfail, " | ".join(fails[:3]), round(time.time() - t0, 1)


INJECTIONS = {
    "memory.py": ('return " OR ".join(f\'"{t}"\' for t in toks[:24])',
                  'return " OR ".join(f\'"{t}"\' for t in toks[:2])'),
}


def inject(mod: str, old: str, new: str) -> None:
    p = WORK / "mw" / mod
    s = p.read_text()
    assert s.count(old) == 1, f"注入点不唯一：{s.count(old)}"
    p.write_text(s.replace(old, new))


def rollback(mods) -> None:
    for m in mods:
        shutil.copy2(ORIG / "mw" / m, WORK / "mw" / m)


def main() -> None:
    log = []
    for mod, (old, new) in INJECTIONS.items():
        print(f"\n=== 变异 {mod} ===", flush=True)
        fresh_work()
        inject(mod, old, new)
        np_, nf, fails, dt = run_contract()
        print(f"  变异后: PASS={np_} FAIL={nf} ({dt}s)", flush=True)
        log.append(dict(step="inject", mod=mod, npass=np_, nfail=nf, secs=dt, fails=fails))
        if nf <= 0:
            print("  ⛔ 变异没被任何断言抓到 ⇒ 这个变异点无效（不是判据的问题）", flush=True)
            log.append(dict(step="VACUOUS", mod=mod))
            continue

        # 二分：候选集合
        cand = list(MODS)
        n_eval = 0
        while len(cand) > 1:
            half = len(cand) // 2
            group = cand[:half]
            fresh_work()
            inject(mod, old, new)          # 重新注入（fresh_work 把它冲掉了）
            rollback(group)                # 把这一半回滚到原件
            n_eval += 1
            np_, nf, fails, dt = run_contract()
            green = (nf == 0)
            print(f"  [{n_eval}] 回滚 {len(group)} 个模块 -> PASS={np_} FAIL={nf} "
                  f"{'绿 ⇒ 变异在组内' if green else '红 ⇒ 变异在补集'}", flush=True)
            log.append(dict(step="probe", n=n_eval, group=group, npass=np_,
                            nfail=nf, green=green, secs=dt))
            if green:
                cand = group
            else:
                cand = cand[half:]
        n_eval += 1
        fresh_work()
        inject(mod, old, new)
        rollback(cand)                     # 只回滚定位到的那一个
        np_, nf, fails, dt = run_contract()
        ok = (nf == 0)
        print(f"  定位结果: {cand} -> PASS={np_} FAIL={nf} "
              f"{'✅ 修好' if ok else '⛔ 未修好'}", flush=True)
        log.append(dict(step="verify", located=cand, npass=np_, nfail=nf,
                        ok=ok, probes=n_eval, secs=dt))
        print(f"  判分次数: {n_eval}（穷举需 23）", flush=True)

    out = Path("/Users/linshaojie/V1/bench/evidence/v6_s225/bisect.json")
    out.write_text(json.dumps(log, ensure_ascii=False, indent=1))
    print(f"\n落盘 {out}", flush=True)


if __name__ == "__main__":
    main()
