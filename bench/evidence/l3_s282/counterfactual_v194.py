#!/usr/bin/env python3
"""V194 补算：分母结构 + 反事实分数（若把闸真接进 bench）。⛔ 只读落盘产物。"""
from __future__ import annotations

import ast
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / "bench_out"
OUT = Path(__file__).resolve().parent
START = int(__import__("sys").argv[1]) if len(__import__("sys").argv) > 1 else 0


def rows(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def in_run(d: dict) -> bool:
    try:
        return int(str(d.get("run_id", "0")).split("@")[-1]) >= START
    except ValueError:
        return False


def _suite_of_run() -> dict[str, str]:
    """run_id → suite，取自 answers.jsonl 的 header（⛔ 不靠顺序猜）。"""
    out: dict[str, str] = {}
    for d in rows(BENCH / "answers.jsonl"):
        if d.get("kind") != "header":
            continue
        meta = d.get("meta")
        suite = ""
        if isinstance(meta, str):
            try:
                suite = str(ast.literal_eval(meta).get("suite") or "")
            except (ValueError, SyntaxError):
                suite = ""
        elif isinstance(meta, dict):
            suite = str(meta.get("suite") or "")
        out[str(d.get("run_id"))] = Path(suite).stem
    return out


SUITE = _suite_of_run()
answers = [r for r in rows(BENCH / "answers.jsonl") if r.get("kind") == "answer" and in_run(r)]
score_map: dict[tuple[str, str, int], float] = {}
for d in rows(BENCH / "runs.jsonl"):
    if not in_run(d):
        continue
    su = SUITE.get(str(d.get("run_id")), "?")
    for row in d["report"]["per_task"]:
        score_map[(su, row["id"], int(row.get("rep", 0)))] = float(row["score"])

cells = [(SUITE.get(str(r.get("run_id")), "?"), r["task_id"], int(r.get("rep", 0)))
         for r in answers]
uniq = set(cells)
missing = [c for c in cells if c not in score_map]
print(f"答案行 {len(answers)}　唯一 (id,rep) {len(uniq)}　分数格 {len(score_map)}")
print(f"join 失败 {len(missing)}：{sorted(set(missing))}")
print(f"有分数无答案：{sorted(set(score_map) - uniq)}")
dup = {k: v for k, v in collections.Counter(cells).items() if v > 1}
print(f"同一 (id,rep) 出现多次：{len(dup)} 个格 → {dup}")

# ── 反事实：接上闸（DROP ⇒ 该格分数记 0）──────────────────────────
readings = json.loads((OUT / "readings_v194.json").read_text(encoding="utf-8"))
verdict = {(r["suite"], r["id"], r["rep"]): r["verdict"] for r in readings["rows"]}

per_suite: dict[str, list[tuple[float, float]]] = collections.defaultdict(list)
for cell, s in sorted(score_map.items()):
    if cell not in verdict:
        continue
    g = 0.0 if verdict[cell] == "DROP" else s
    per_suite[cell[0]].append((s, g))

print("\n反事实：把 DROP 的格按 0 分算（⛔ 这是「接上之后尺子会变成什么」）")
print(f"  {'suite':<36}{'格数':>5}{'实测均分':>10}{'接闸后':>10}{'差':>9}")
tot_a = tot_g = 0.0
n_all = 0
for name, pairs in sorted(per_suite.items()):
    a = sum(p[0] for p in pairs) / len(pairs)
    g = sum(p[1] for p in pairs) / len(pairs)
    tot_a += sum(p[0] for p in pairs)
    tot_g += sum(p[1] for p in pairs)
    n_all += len(pairs)
    print(f"  {name:<36}{len(pairs):>5}{a:>10.4f}{g:>10.4f}{g - a:>+9.4f}")
print(f"  {'合计':<36}{n_all:>5}{tot_a / n_all:>10.4f}{tot_g / n_all:>10.4f}"
      f"{(tot_g - tot_a) / n_all:>+9.4f}")

print("\n被误杀的格（DROP 且 score>0）：")
for r in readings["rows"]:
    if r["verdict"] == "DROP" and (r["score"] or 0) > 0:
        print(f"  {r['id']:<22} rep={r['rep']} score={r['score']} tally={r['tally']}")
