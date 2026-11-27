#!/usr/bin/env python3
"""V254 剂量探针（纯离线，0 次模型调用）。

要回答的问题只有一个：在**当前装置**上，把 V253 那 13 格的失败当作「历史同类题」
回灌，目标格实际会收到**什么字**？

三个必须分开量的事实：
  A. rkey 的 Jaccard 能不能过 `REWARD_MIN_SIM=0.30`（通道能否检索到）；
  B. 失败文本被 `_error_class` 归到哪一类（类别是机器归一的）；
  C. 该类在 `_ERROR_CLASS_CHECK` 里的检查项是什么（这行才是模型真正读到的东西）。

⛔ 本脚本不写记忆、不写账本、不跑模型；只读题面 + 只读 v253 读数。
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path("/Users/linshaojie/V1")
sys.path.insert(0, str(ROOT))

from mw.features import feature_set                       # noqa: E402
from mw.worldmodel import (REWARD_MIN_SIM, _ERROR_CLASS_CHECK,  # noqa: E402
                           _ERROR_CLASS_RE, _error_class)

EV = ROOT / "bench" / "evidence" / "l18_s324"
LOW = pathlib.Path("/tmp/v253_suite/low.jsonl")
ALL39 = pathlib.Path("/tmp/v253_suite/all39.jsonl")
V253_ANALYSE = ROOT / "bench" / "evidence" / "l17_s323" / "analyse_v253.json"


def load_tasks() -> list[dict]:
    if LOW.exists():
        return [json.loads(l) for l in LOW.read_text(encoding="utf-8").splitlines() if l.strip()]
    ids = json.loads(pathlib.Path("/tmp/v253_suite/low_ids.json").read_text(encoding="utf-8"))
    tasks = [json.loads(l) for l in ALL39.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [t for t in tasks if t["id"] in set(ids)]


def main() -> int:
    tasks = load_tasks()
    an = json.loads(V253_ANALYSE.read_text(encoding="utf-8"))
    per_cell = an["per_cell"]

    feats: dict[str, frozenset[str]] = {}
    why: dict[str, str] = {}
    score: dict[str, float] = {}
    cls: dict[str, str] = {}
    raw: dict[str, str] = {}
    for t in tasks:
        tid = t["id"]
        feats[tid] = feature_set(t)
        cell = per_cell.get(tid) or {}
        why[tid] = str((cell.get("A") or {}).get("why") or "")
        sc = (cell.get("A") or {}).get("score")
        score[tid] = float(sc) if isinstance(sc, (int, float)) else -1.0
        cls[tid] = _error_class(why[tid])
        m = _ERROR_CLASS_RE.search(why[tid])
        raw[tid] = m.group(1) if m else "其他"

    print("=" * 78)
    print("A. 13 格的特征与失败类别（类别由装置归一，不是人挑的）")
    print("=" * 78)
    print(f"{'cell':26} {'score':>6}  {'|rkey|':>6}  {'真实异常':<20} {'装置归一':<14} 模板覆盖")
    for tid in sorted(feats, key=lambda x: score[x]):
        print(f"{tid:26} {score[tid]:6.3f}  {len(feats[tid]):6d}  "
              f"{raw[tid]:<20} {cls[tid]:<14} {cls[tid] in _ERROR_CLASS_CHECK}")

    # ── B. 有序对（src → dst），只看过门槛的 ────────────────────────────────
    print()
    print("=" * 78)
    print(f"B. 有序对 jaccard(rkey) >= {REWARD_MIN_SIM} —— 目标格实际会收到的行")
    print("=" * 78)
    rows = []
    for src in feats:
        for dst in feats:
            if src == dst:
                continue
            inter = feats[src] & feats[dst]
            union = feats[src] | feats[dst]
            j = len(inter) / len(union) if union else 0.0
            if j < REWARD_MIN_SIM:
                continue
            c = cls[src]
            line = (f"- 同类题里 `{c}` 出现过 1 次（历史得分均值 {score[src]:.2f}）"
                    f" ⇒ 先检查：{_ERROR_CLASS_CHECK.get(c, _ERROR_CLASS_CHECK['其他'])}")
            rows.append({"src": src, "dst": dst, "jaccard": round(j, 4),
                         "src_score": score[src], "src_class": c,
                         "src_has_template": c in _ERROR_CLASS_CHECK,
                         "line": line})
    rows.sort(key=lambda r: (-r["jaccard"], r["dst"], r["src"]))
    for r in rows:
        tag = "" if r["src_has_template"] else "  ⚠️类别无模板→落到『其他』"
        print(f"{r['src']:24} -> {r['dst']:24} J={r['jaccard']:.3f} "
              f"class={r['src_class']}{tag}")

    # ── C. 每个目标格的候选回灌源（按 J 排序取前 3）────────────────────────
    print()
    print("=" * 78)
    print("C. 逐目标格：能当回灌源的有几个、内容是否含该格真实病因")
    print("=" * 78)
    by_dst: dict[str, list[dict]] = {}
    for r in rows:
        by_dst.setdefault(r["dst"], []).append(r)
    for dst in sorted(feats):
        srcs = by_dst.get(dst) or []
        uniq_cls = sorted({s["src_class"] for s in srcs})
        print(f"\n### {dst}  score={score[dst]:.3f}  class={cls[dst]}")
        print(f"    可达源 {len(srcs)} 个；源类别 {uniq_cls}")
        for r in srcs[:3]:
            print(f"    ← {r['src']} J={r['jaccard']:.3f}")
            print(f"      {r['line']}")

    out = {"min_sim": REWARD_MIN_SIM,
           "cells": {k: {"score": score[k], "class": cls[k], "raw_error": raw[k],
                         "has_template": cls[k] in _ERROR_CLASS_CHECK,
                         "rkey": sorted(feats[k])} for k in feats},
           "pairs": rows}
    EV.mkdir(parents=True, exist_ok=True)
    (EV / "dose_v254.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── D. 一句话汇总 ────────────────────────────────────────────────────
    print()
    print("=" * 78)
    n_pairs = len(rows)
    n_dst = len(by_dst)
    n_dst_with_correct_class = sum(
        1 for d, ss in by_dst.items() if any(s["src_class"] == cls[d] for s in ss))
    n_other = sorted(k for k in cls if cls[k] == "其他")
    print(f"D. 过门槛有序对 {n_pairs}；有源的目标格 {n_dst}/{len(feats)}；"
          f"源类别与本格类别相同的目标格 {n_dst_with_correct_class}")
    print(f"   归一后落到『其他』的格 {len(n_other)} 个：{n_other}")
    print(f"   ⇒ 这 {len(n_other)} 格的回灌行与真实病因**无关**（都是『手算一遍』）。")
    print(f"   ⇒ 写 {EV / 'dose_v254.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
