#!/usr/bin/env python3
"""V234 小样：两臂提议（A=实测值 / T=实测值+轨迹），外加一条**同轮时间对照臂** A2。

⛔ 这是测量脚本，不是产品代码。它自己做 HTTP，不经过内核 ——
   因为要测的是「同一份起点 + 两种入参」，不是「内核跑一轮会怎样」。
⛔ 判据写在 `PREREG_v234.md`，跑之前就写好了。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT))

from mw.reflect import (build_proposal_prompt, failed_actuals,  # noqa: E402
                        parse_proposal, sha_of, trace_failure, verify)

BASE_URL = os.environ.get("V1_LLM_BASE_URL", "http://localhost:11434/v1")
MODEL = os.environ.get("V1_LLM_MODEL", "qwen2.5:7b")
TEMP = float(os.environ.get("V1_LLM_TEMPERATURE", "1.0"))
MAXTOK = 900
OUT = HERE / "probe_v234.jsonl"
RAW = HERE / "probe_v234_raw"


def complete(prompt: str) -> tuple[str, dict]:
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                       "temperature": TEMP, "max_tokens": MAXTOK}).encode()
    req = urllib.request.Request(BASE_URL + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer ollama"})
    t0 = time.time()
    r = json.loads(urllib.request.urlopen(req, timeout=600).read())
    wall = time.time() - t0
    txt = (r.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    usage = r.get("usage", {})
    usage["wall_s"] = round(wall, 1)
    usage["finish"] = (r.get("choices") or [{}])[0].get("finish_reason", "")
    return txt, usage


def load(tid: str) -> dict:
    for ln in open(HERE / "suite_sub2.jsonl", encoding="utf-8"):
        d = json.loads(ln)
        if d["id"] == tid:
            return d
    raise SystemExit("no task")


def ledger_answer(tid: str) -> tuple[str, str]:
    for ln in open(HERE / "crit_v231_r2_after.jsonl", encoding="utf-8"):
        d = json.loads(ln)
        if d["task_id"] == tid:
            return d["answer"], d["why"]
    raise SystemExit("no ledger row")


def diffstat(a: str, b: str) -> dict:
    al, bl = a.splitlines(), b.splitlines()
    sm = difflib.SequenceMatcher(None, al, bl)
    add = rem = same = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            rem += i2 - i1
        if tag in ("replace", "insert"):
            add += j2 - j1
        if tag == "equal":
            same += i2 - i1
    return {"lines_added": add, "lines_removed": rem,
            "lines_same": same, "ratio": round(sm.ratio(), 3)}


def main() -> int:
    task = load("t01_parse_duration")
    ans, why = ledger_answer("t01_parse_duration")
    tests, goal = task["tests"], task["prompt"]
    acts = failed_actuals(ans, tests)
    segs = trace_failure(ans, tests)
    base = verify(ans, tests)
    p_a = build_proposal_prompt(goal, ans, why, acts)
    p_t = build_proposal_prompt(goal, ans, why, acts, trace=segs)
    print(f"base={base}/{len(tests)}  actuals={acts}")
    print(f"prompt A={len(p_a)}B  T={len(p_t)}B  (Δ={len(p_t)-len(p_a)}B)")
    if not RAW.exists():                 # ⚠️ 沙箱 shim 对 exist_ok=True 会抛 EEXIST
        RAW.mkdir()
    (RAW / "prompt_A.txt").write_text(p_a, encoding="utf-8")
    (RAW / "prompt_T.txt").write_text(p_t, encoding="utf-8")
    rows = []
    #: A / T / A2 —— A2 是同轮时间对照臂（同样的入参再跑一次，看时间有没有换掉调用）
    #: ⚠️ V234 首跑就靠 A2 抓到一件更要紧的事：**同 prompt 两次给 3/6 与 5/6**
    #:    ⇒ 这个装置在 temperature=1.0 下**不确定**（纪律 136 的前置）。见 `--repeat`。
    plan = [("A", p_a), ("T", p_t), ("A2", p_a)]
    rep = int(os.environ.get("V234_REPEAT", "1"))
    if rep > 1:
        plan = []
        for k in range(rep):
            plan.append(("A", p_a))          # 交错：A/T 交替，抵消时间漂移
            plan.append(("T", p_t))
    for i, (tag, prompt) in enumerate(plan):
        txt, usage = complete(prompt)
        code, fenced = parse_proposal(txt)
        got = verify(code, tests)
        (RAW / f"out_{tag}_{i:02d}.txt").write_text(txt, encoding="utf-8")
        rec = {"arm": tag, "i": i, "model": MODEL, "temp": TEMP, "max_tokens": MAXTOK,
               "prompt_sha": hashlib.sha256(prompt.encode()).hexdigest()[:16],
               "prompt_chars": len(prompt), "out_chars": len(txt),
               "out_sha": sha_of(txt), "code_sha": sha_of(code),
               "code_chars": len(code), "fenced": fenced,
               "base": base, "got": got, "n_tests": len(tests),
               "gain_vs_fed": got - base, "gain_vs_oracle": got - len(tests),
               "diff_vs_base": diffstat(ans, code),
               "wall_s": usage.get("wall_s"), "tok": usage.get("completion_tokens"),
               "finish": usage.get("finish")}
        rows.append(rec)
        print(json.dumps(rec, ensure_ascii=False))
    with open(OUT, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nwritten {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
