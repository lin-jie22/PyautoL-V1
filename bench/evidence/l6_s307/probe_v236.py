#!/usr/bin/env python3
"""V236 探针：**MODEL 轴** —— 换模型能不能扩候选空间（行为类数）与提高命中率。

⛔ 这是测量脚本，不是产品代码。它自己做 HTTP，但**判分走产品同一个打分器子进程**
   （`ExternalScorer`），因为判据是「分数/行为类」—— 必须与账本同一把尺子。
⛔ 判据写在 `PREREG_v236.md`，跑之前写好的。

用法：
    python3 probe_v236.py selftest     # 装置自检：起点 5/6 + vec 非空
    V236_N=1 python3 probe_v236.py run # 小样（验证装置）
    python3 probe_v236.py run          # 全量（8/8/4）
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT))

from mw.bench import ExternalScorer                      # noqa: E402
from mw.reflect import (build_proposal_prompt, failed_actuals,  # noqa: E402
                        parse_proposal, sha_of)

BASE_URL = os.environ.get("V1_LLM_BASE_URL", "http://localhost:11434/v1")
TEMP = float(os.environ.get("V1_LLM_TEMPERATURE", "1.0"))
MAXTOK = 900
OUT = Path(os.environ.get("V236_OUT") or (HERE / "probe_v236.jsonl"))
RAW = HERE / "raw_v236"

#: 预注册写死的预算（PREREG §3）。⛔ 不许中途加。
#: ★ 预注册后修订（§8）：`Qwen3.5-4B` 实测是 **thinking** 模型，
#:   900 token 全被思考吃掉 ⇒ 同一预算测的是「截断」，不是「能力」。
#:   ⇒ 追加 `B_prime`（同模型、2400 token），**单列、不进合并池**。
ARMS = [("A_7b", "qwen2.5:7b", 8),
        ("B_4b", "Qwen3.5-4B:latest", 8),
        ("C_14b", "qwen2.5:14b", 4)]
ARMS_ALL = ARMS + [("B_prime", "Qwen3.5-4B:latest", 4)]

#: 每臂的 token 预算。⛔ 只有 thinking 模型那一臂不同，且单列。
BUDGET = {"B_prime": 2400}


def task_and_start() -> tuple[dict, str, str]:
    task = None
    for ln in open(HERE.parent / "l5_s266" / "suite_one" / "t01.jsonl", encoding="utf-8"):
        task = json.loads(ln)
    if task is None:
        raise SystemExit("no task")
    start = (HERE / "start_answer.py").read_text(encoding="utf-8")
    why_tpl = (f"当前最好的一次是 {{s:.4f}}（共 {len(task['tests'])} 条断言）。"
               f"请给出**把剩下的失败断言一起修好**的完整实现。")
    return task, start, why_tpl


def make_scorer():
    sp = ROOT / "bench" / "scorers" / "code_exec.py"
    return ExternalScorer([sys.executable, str(sp)], source=sp)


def complete(model: str, prompt: str, max_tokens: int = MAXTOK) -> tuple[str, dict]:
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": prompt}],
                       "temperature": TEMP, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(
        BASE_URL + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer ollama"})
    t0 = time.time()
    r = json.loads(urllib.request.urlopen(req, timeout=900).read())
    wall = time.time() - t0
    ch = (r.get("choices") or [{}])[0]
    txt = (ch.get("message", {}) or {}).get("content", "") or ""
    u = dict(r.get("usage") or {})
    u["wall_s"] = round(wall, 1)
    u["finish"] = ch.get("finish_reason", "")
    return txt, u


def selftest() -> int:
    task, start, why_tpl = task_and_start()
    sc = make_scorer()
    r = sc.score(task, start)
    vec = getattr(r, "vec", None)
    print(f"起点: score={r.score}  vec={''.join('1' if x else '0' for x in (vec or ()))}"
          f"  len(vec)={len(vec or ())}  n_tests={len(task['tests'])}")
    print(f"why 模板: {why_tpl.format(s=r.score)}")
    p = build_proposal_prompt(task["prompt"], start, why_tpl.format(s=r.score),
                              failed_actuals(start, task["tests"]))
    print(f"prompt chars={len(p)}  sha={sha_of(p)}")
    # ⚠️ 同精度比（纪律 163）：打分器给的是 `round(k/n, 4)`，拿 `5/6` 比会差 3.3e-5
    #    ⇒ 首版写成 `< 1e-6` 当场 FAIL。这不是装置坏，是**判据自己的量纲错**。
    want = round(5 / 6, 4)
    ok = (abs(r.score - want) < 1e-9 and vec is not None
          and len(vec) == len(task["tests"]))
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def run() -> int:
    task, start, why_tpl = task_and_start()
    sc = make_scorer()
    base = sc.score(task, start)
    base_vec = getattr(base, "vec", None)
    if base_vec is None:
        raise SystemExit("⛔ 起点没有 vec ⇒ 行为级判据结构上不可用")
    n = int(os.environ.get("V236_N", "0") or 0)
    only = [a.strip() for a in (os.environ.get("V236_ARMS") or "").split(",") if a.strip()]
    why = why_tpl.format(s=base.score)
    prompt = build_proposal_prompt(task["prompt"], start, why,
                                   failed_actuals(start, task["tests"]))
    if not RAW.exists():
        RAW.mkdir()
    (RAW / "prompt.txt").write_text(prompt, encoding="utf-8")
    print(f"base={base.score:.4f} vec={''.join('1' if x else '0' for x in base_vec)} "
          f"prompt={len(prompt)}B")
    for tag, model, budget in ARMS_ALL:
        if only and tag not in only:
            continue
        k = min(budget, n) if n > 0 else budget
        mt = int(BUDGET.get(tag, MAXTOK))
        #: 预注册 §5：连续 2 次 `finish != stop` ⇒ 停该臂（如实报截断，不补跑）
        trunc_run = 0
        for i in range(k):
            try:
                txt, u = complete(model, prompt, mt)
            except Exception as e:                       # noqa: BLE001
                print(json.dumps({"arm": tag, "model": model, "i": i,
                                  "err": str(e)[:160]}, ensure_ascii=False))
                continue
            code, fenced = parse_proposal(txt)
            (RAW / f"out_{tag}_{i:02d}.txt").write_text(txt, encoding="utf-8")
            rec = {"arm": tag, "model": model, "i": i, "temp": TEMP,
                   "max_tokens": mt,
                   "prompt_sha": sha_of(prompt), "out_chars": len(txt),
                   "code_chars": len(code), "fenced": bool(fenced),
                   "code_sha": sha_of(code),
                   "tok": u.get("completion_tokens"), "wall_s": u.get("wall_s"),
                   "finish": u.get("finish")}
            trunc_run = trunc_run + 1 if u.get("finish") != "stop" else 0
            if not code.strip():
                rec["err"] = "empty"
                print(json.dumps(rec, ensure_ascii=False))
                if trunc_run >= 2:
                    print(f"  ⛔ {tag} 连续 {trunc_run} 次未正常结束 ⇒ 按 PREREG §5 停该臂")
                    break
                continue
            sr = sc.score(task, code)
            vec = getattr(sr, "vec", None)
            rec["score"] = round(float(sr.score), 4)
            rec["hit6"] = bool(abs(sr.score - 1.0) < 1e-9)
            rec["vec"] = list(vec) if vec is not None else None
            rec["why"] = (sr.why or "")[:120]
            with open(OUT, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(json.dumps(rec, ensure_ascii=False))
            if trunc_run >= 2:
                print(f"  ⛔ {tag} 连续 {trunc_run} 次未正常结束 ⇒ 按 PREREG §5 停该臂")
                break
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    raise SystemExit(selftest() if mode == "selftest" else run())
