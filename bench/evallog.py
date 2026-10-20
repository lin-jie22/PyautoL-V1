#!/usr/bin/env python3
"""合并 eval log —— 一个 evidence 目录 → 单文件 `evallog.jsonl`（★7p 补强③）。

对应 Inspect AI 的 `.eval` 结构化日志：判读不再散在 runs/states/final/probe
五类文件里，每格一行、schema 固定、字段可直接 grep：

    {"arm":"H","rep":1,"model":"qwen2.5:7b","llm_of_record":"http",
     "candidate":"…","suite":"g7m_g7d2_heldout.jsonl",
     "per_task":[{"id":…,"rep":1,"score":0.2,"why":…,"elapsed_s":…}],
     "mean":0.2,"elapsed_s":61.9,
     "transport":{"wall_refused_first":0,…}}

字段来源（全部取证自 runs.jsonl，不做推断）：
    · model      —— `report.candidate` 里的 `model[:N]` 记号 + `llm_of_record`；
    · suite      —— 调用方显式传入（evidence 目录本身不记 suite 名）；
    · transport  —— `kernel_transport`（str(dict) 单引号形态，见 7m-β 自踩）。

用法：
    python3 bench/evallog.py bench/evidence/knowledge_s42 \
        --suite g7m_g7d2_heldout.jsonl --out bench/evidence/knowledge_s42/evallog.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_MODEL_RE = re.compile(r"[A-Za-z0-9._:\-/]+(?:/[A-Za-z0-9._:\-]+)?")


def _parse_transport(raw) -> dict:
    """runs.jsonl 的 kernel_transport 是 str(dict)（单引号）—— 7m-β 的坑。"""
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(str(raw).replace("'", '"'))
    except (json.JSONDecodeError, ValueError):
        return {"raw": str(raw)[:200], "parse_error": True}


def _extract_model(candidate: str) -> str:
    """从自由文本 candidate（如 `内核/http qwen2.5:7b（复用同一内核…）`）
    里取模型记号。取不到就返回空串 —— ⛔ 不猜。"""
    m = re.search(r"(?:http|stub)\s+([A-Za-z0-9._:\-/]+)", candidate or "")
    return m.group(1) if m else ""


def consolidate(ev: Path, suite: str = "") -> list[dict]:
    rows: list[dict] = []
    for runs_path in sorted(ev.glob("*_runs.jsonl")):
        stem = runs_path.stem.removesuffix("_runs")   # H_r1_runs → H_r1
        arm, _, rep = stem.partition("_r")
        if not rep.isdigit():
            continue
        for line in runs_path.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            report = d.get("report") or {}
            meas = d.get("measurement") or {}
            rows.append({
                "arm": arm,
                "rep": int(rep),
                "model": _extract_model(str(report.get("candidate", ""))),
                "llm_of_record": report.get("llm_of_record", ""),
                "candidate": report.get("candidate", ""),
                "suite": suite,
                "per_task": report.get("per_task", []),
                "mean": meas.get("mean"),
                "n": meas.get("n"),
                "std": meas.get("std"),
                "elapsed_s": report.get("elapsed_s"),
                "errors": report.get("errors", []),
                "failed_scorer": report.get("failed_scorer"),
                "transport": _parse_transport(d.get("kernel_transport")),
            })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("evidence_dir", type=Path)
    ap.add_argument("--suite", default="")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    ev = a.evidence_dir
    if not ev.is_dir():
        print(f"❌ {ev} 不是目录")
        return 2
    rows = consolidate(ev, suite=a.suite)
    if not rows:
        print(f"❌ {ev} 里没有 *_runs.jsonl")
        return 2
    out = a.out or (ev / "evallog.jsonl")
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                           for r in rows))
    print(f"✅ {out}：{len(rows)} 格")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
