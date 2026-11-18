#!/usr/bin/env python3
"""V197 反事实 —— L（题面 + 真 why 全文，含隐藏测试原文）/ M（题面 + 只有计数行）。

⚠️ 装置自报（纪律 65）：直接复用 `bench/evidence/l3_s284/arms_v196.py` 的
   `call_agent` / `score_answer` / `load_suites` / `load_frozen`，见 `DEPS` 打印。
⛔ 不改内核、不改 agents.json、不改打分器。断点续跑：`cells_v197.jsonl` 追加。
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
V196 = ROOT / "bench" / "evidence" / "l3_s284"
CELLS = OUT / "cells_v197.jsonl"
BASE = ROOT / "bench" / "evidence" / "l3_s282" / "readings_v194.json"

DEADLINE_S = 90
_COUNT_RE = re.compile(r"通过\s*\d+\s*/\s*\d+")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


A196 = _load("arms_v196_for_v197", V196 / "arms_v196.py")
DEPS = {
    "module": "arms_v196",
    "path": str(V196 / "arms_v196.py"),
    "agent": str(A196.AGENT),
    "scorer": str(A196.SCORER),
    "frozen": str(A196.FROZEN),
}


def load_why() -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for l in (V196 / "cells_v196.jsonl").read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        r = json.loads(l)
        if r.get("arm") == "R":
            out[(r["suite"], r["id"])] = str(r.get("why") or "")
    return out


def load_base() -> dict[tuple[str, str], float]:
    d = json.loads(BASE.read_text(encoding="utf-8"))
    return {(r["suite"], r["id"]): float(r["score"]) for r in d["rows"]}


def build(arm: str, prompt: str, why: str) -> str:
    if arm == "L":
        return f"{prompt}\n\n【上次尝试的失败反馈】\n{why}"
    m = _COUNT_RE.search(why)
    tail = m.group(0) if m else "无反馈"
    return f"{prompt}\n\n【上次尝试的结果】\n{tail}"


def done() -> set[tuple[str, str, str]]:
    if not CELLS.exists():
        return set()
    out = set()
    for l in CELLS.read_text(encoding="utf-8").splitlines():
        if l.strip():
            r = json.loads(l)
            out.add((r["suite"], r["id"], r["arm"]))
    return out


def main() -> int:
    suites = A196.load_suites()
    why = load_why()
    base = load_base()
    pool = sorted(k for k, v in base.items() if v < 1.0)
    have = done()
    print(f"DEPS={json.dumps(DEPS, ensure_ascii=False)}", flush=True)
    print(f"池 {len(pool)} 格；已完成 {len(have)} 格", flush=True)

    for i, (suite, tid) in enumerate(pool, 1):
        task = suites[suite][tid]
        prompt = str(task.get("prompt") or "")
        w = why.get((suite, tid), "")
        for arm in ("L", "M"):
            if (suite, tid, arm) in have:
                continue
            payload = build(arm, prompt, w)
            t0 = __import__("time").time()
            res = A196.call_agent(payload, {"AGENT_DEADLINE_S": str(DEADLINE_S)})
            sc = (A196.score_answer(task, str(res.get("answer") or "")))
            row = {
                "suite": suite, "id": tid, "arm": arm,
                "baseline_score": float(base[(suite, tid)]),
                "score": sc.get("score"), "why": str(sc.get("why") or "")[:200],
                "ok": bool(res.get("ok")), "err": str(res.get("error") or "")[:160],
                "answer_chars": len(str(res.get("answer") or "")),
                "answer_sha": __import__("hashlib").sha256(
                    str(res.get("answer") or "").encode()).hexdigest()[:16],
                # 事后登记：交付的答案里是否原样出现该格的隐藏测试串
                "echo_assert": sum(1 for m in re.findall(
                    r"assert\s+[A-Za-z_][A-Za-z0-9_]*\s*\(", w)
                    if m in str(res.get("answer") or "")),
                "payload_chars": len(payload), "why_chars": len(w),
                "elapsed_s": round(__import__("time").time() - t0, 2),
            }
            with CELLS.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"[{i}/{len(pool)}] {arm} {suite}/{tid} score={row['score']} "
                  f"echo={row['echo_assert']} {row['elapsed_s']}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
