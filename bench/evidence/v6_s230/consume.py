#!/usr/bin/env python3
"""V141 / §208 读数：`verdict_from` 的日常消费者到底通没通电。

跑法（仓库根目录）：
    python3 -u bench/evidence/v6_s230/consume.py > READINGS.txt 2>&1

⛔ 零模型、零网络：全部用 `--offline --solver oracle`（离线桩，纪律 30）。
⛔ 不动生产目录：每一步都显式设 `V1_BENCH_DIR` / `V1_STATE_DIR` 到 /tmp。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent          # bench/evidence/v6_s230 → 仓库根
sys.path.insert(0, str(ROOT))

from mw.measure import SENTINEL, verdict_from        # noqa: E402
from run import _rc_after_readback, judge_run_log    # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="v141_"))
SUITE = "bench/tasks/coding_small.jsonl"
RESULTS: dict[str, object] = {}


def say(*a) -> None:
    print(*a, flush=True)


def run_bench(bench_dir: Path, state_dir: Path, extra: list[str] | None = None,
              suite: str = SUITE) -> tuple[int, str]:
    """跑一次 bench，返回 (退出码, stdout)。⛔ 不接管道 —— 要拿真 rc（纪律 28）。"""
    env = dict(os.environ)
    env.update({"V1_BENCH_DIR": str(bench_dir), "V1_STATE_DIR": str(state_dir),
                "V1_LOG_DIR": str(state_dir / "logs")})
    cmd = [sys.executable, "-u", "run.py", "--bench", "--offline",
           "--solver", "oracle", "--fresh", "--suite", suite] + (extra or [])
    p = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def count_sentinel(text: str) -> int:
    return sum(1 for ln in text.splitlines() if SENTINEL in ln)


say("=" * 74)
say("V141 读数：让 `verdict_from` 有一个日常消费者（§208）")
say("=" * 74)
say(f"仓库 {ROOT}")
say(f"临时目录 {TMP}")
say()

# ── W1 装置自检：改动前后的日志里 sentinel 行数 ──────────────────────────
say("-" * 74)
say("W1 装置自检（**不构成命中**）：改动前 0 行 / 改动后 1 行")
say("-" * 74)
_pre = TMP / "pre"
_pre.mkdir(parents=True, exist_ok=True)
_tar = subprocess.run(["git", "archive", "HEAD"], cwd=str(ROOT),
                      capture_output=True)
subprocess.run(["tar", "-x", "-C", str(_pre)], input=_tar.stdout, check=True)
_env = dict(os.environ)
_env.update({"V1_BENCH_DIR": str(TMP / "pre_bench"),
             "V1_STATE_DIR": str(TMP / "pre_state"),
             "V1_LOG_DIR": str(TMP / "pre_state" / "logs")})
_p = subprocess.run([sys.executable, "-u", "run.py", "--bench", "--offline",
                     "--solver", "oracle", "--fresh", "--suite", SUITE],
                    cwd=str(_pre), env=_env, capture_output=True, text=True)
_pre_out = _p.stdout + _p.stderr
say(f"  改动前（git archive HEAD = {_tar.stdout[:0] or 'HEAD'}）：rc={_p.returncode}"
    f"  sentinel 行数={count_sentinel(_pre_out)}"
    f"  日志文件存在={ (TMP / 'pre_bench' / 'last_run.log').exists() }")
RESULTS["W1_pre_sentinel"] = count_sentinel(_pre_out)

# ── W2 两次独立运行的状态必须相同 ───────────────────────────────────────
say()
say("-" * 74)
say("W2 两次**独立**运行：判读结果必须相同（确定性，不含时间/pid/路径）")
say("-" * 74)
_runs = []
for i in (1, 2):
    b, s = TMP / f"bench{i}", TMP / f"state{i}"
    rc, out = run_bench(b, s)
    log = b / "last_run.log"
    state, why = judge_run_log(log, rc)
    _runs.append({"rc": rc, "state": state, "why": why,
                  "sentinel_in_stdout": count_sentinel(out),
                  "sentinel_in_log": count_sentinel(log.read_text(encoding="utf-8")),
                  "log": str(log)})
    say(f"  第{i}次  rc={rc}  读回={state}  why={why}")
    say(f"         日志 sentinel={_runs[-1]['sentinel_in_log']} 行"
        f"  终端 sentinel={_runs[-1]['sentinel_in_stdout']} 行（读回行不带前缀）")
_same = _runs[0]["state"] == _runs[1]["state"]
say(f"  ⇒ 两次相同={_same}  状态={_runs[0]['state']}/{_runs[1]['state']}")
RESULTS["W2"] = {"same": _same, "states": [r["state"] for r in _runs],
                 "rc": [r["rc"] for r in _runs]}

_log1 = Path(_runs[0]["log"])
_text1 = _log1.read_text(encoding="utf-8")

# ── W3 截断 ⇒ CRASH，且退出码被抬起 ────────────────────────────────────
say()
say("-" * 74)
say("W3 把同一份日志**截断**（判据缺席 / 判据行断在半截）⇒ 必须 CRASH")
say("-" * 74)
_lines = _text1.splitlines(keepends=True)
_no_last = "".join(ln for ln in _lines if SENTINEL not in ln)
_half = _text1.rstrip("\n")[:-14] + "\n"          # 截掉判据行末尾 14 字符
_cases = {"整行删掉（判据缺席）": _no_last, "判据行断在半截": _half}
RESULTS["W3"] = {}
for name, text in _cases.items():
    f = TMP / (str(abs(hash(name))) + ".log")
    f.write_text(text, encoding="utf-8")
    st, why = judge_run_log(f, 0)
    rc2 = _rc_after_readback(0, st)
    say(f"  {name:<16} ⇒ {st}  rc→{rc2}  — {why}")
    RESULTS["W3"][name] = {"state": st, "rc": rc2, "why": why}

# ── W4 判据行换成「上一次的绿」而 rc=1 ⇒ CRASH ──────────────────────────
say()
say("-" * 74)
say("W4 判据行被换成一行**绿**（上一次的结论），而进程退出码是 1 ⇒ 必须 CRASH")
say("-" * 74)
_fake = "".join(ln for ln in _text1.splitlines(keepends=True) if SENTINEL not in ln)
_fake += f"{SENTINEL} GREEN  PASS=1  FAIL=0\n"
_f4 = TMP / "stale_green.log"
_f4.write_text(_fake, encoding="utf-8")
_st4, _why4 = judge_run_log(_f4, 1)
say(f"  读回={_st4}  — {_why4}")
say(f"  （对照：同一个文本、rc=0 ⇒ {judge_run_log(_f4, 0)[0]}"
    f" —— 退出码是判据的一部分，不是装饰）")
RESULTS["W4"] = {"state_rc1": _st4, "state_rc0": judge_run_log(_f4, 0)[0]}

# ── W5 原地再跑一次**会崩**的运行 ⇒ 不许沿用上一次的绿 ───────────────────
say()
say("-" * 74)
say("W5 「不许沿用上一次的结论」：同一个 bench 目录里再跑一次中途崩的运行")
say("-" * 74)
_bad = TMP / "bad_suite.jsonl"
_bad.write_text('{"id":"t1","prompt":"x"}\n这不是 JSON\n', encoding="utf-8")
_bench5 = Path(_runs[0]["log"]).parent
_before = (_bench5 / "last_run.log").read_text(encoding="utf-8")
say(f"  崩溃前：该日志里 sentinel={count_sentinel(_before)} 行"
    f"，判读={judge_run_log(_bench5 / 'last_run.log', 0)[0]}")
rc5, out5 = run_bench(_bench5, TMP / "state5", suite=str(_bad))
_after = (_bench5 / "last_run.log").read_text(encoding="utf-8")
_st5, _why5 = judge_run_log(_bench5 / "last_run.log", rc5)
say(f"  崩溃运行 rc={rc5}  ⇒ 日志 sentinel={count_sentinel(_after)} 行"
    f"  读回={_st5}  — {_why5}")
say("  终端最后几行：")
for ln in [x for x in out5.strip().splitlines() if x.strip()][-4:]:
    say(f"    {ln}")
_kept = count_sentinel(_after)
say(f"  ⇒ 沿用上一次的绿？{'是（❌ 判负）' if _kept else '否（✅ 旧判据行物理上已不在）'}")
RESULTS["W5"] = {"crash_rc": rc5, "sentinel_after": _kept, "state": _st5,
                 "why": _why5}

# ── 汇总 ────────────────────────────────────────────────────────────────
say()
say("=" * 74)
say("汇总")
say("=" * 74)
say(f"  W1 改动前 sentinel 行数 = {RESULTS['W1_pre_sentinel']}（应为 0）")
say(f"     改动后 = {_runs[0]['sentinel_in_log']}（应为 1，且终端里也只有 1 行）")
say(f"  W2 两次独立运行状态相同 = {_same}（{_runs[0]['state']} / {_runs[1]['state']}）")
say(f"  W3 两种截断都判 CRASH = "
    f"{all(v['state'] == 'CRASH' for v in RESULTS['W3'].values())}"
    f"，且 rc 都被抬起 = "
    f"{all(v['rc'] != 0 for v in RESULTS['W3'].values())}")
say(f"  W4 假绿 + rc=1 判 {_st4}（应为 CRASH）")
say(f"  W5 崩后日志 sentinel={_kept} 行、读回 {_st5}（应为 0 / CRASH）")
say()
say("  ⚠️ 未取到的格：RED（不可信但未崩的 bench 运行）。")
say("     本轮不伪造它 —— 契约 AZ126c 已在单元层覆盖 RED，bench 层留白。")
say(f"\n  readings.json 写在 {HERE / 'readings.json'}")
(HERE / "readings.json").write_text(
    json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
shutil.rmtree(TMP, ignore_errors=True)
