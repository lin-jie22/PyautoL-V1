#!/usr/bin/env python3
"""V255 Stage A：闸的「眼睛」普查（0 次模型调用）。

问题：V254 量到 13 格里 10 格「自检 0 打回」⇒ 装置不知道自己做错了。
本脚本量的是**为什么**：生产默认 `V1_VERIFY_EXEC=0` 时 derive() 只派形式判据，
而打开 `=1` 后能派出的 `example` 判据覆盖面有多大。

⛔ 只读：不调模型、不改生产代码、不写任何 state。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.verify import derive, example_asserts          # noqa: E402

EV = Path(__file__).resolve().parent
SUITE = ROOT / "bench/evidence/l17_s323/suite_all39.jsonl"
ANALYSE = ROOT / "bench/evidence/l17_s323/analyse_v253.json"

#: 「题面里看起来像期望值」的三种写法 —— 用来找**解析器漏掉**的，不是用来当判据。
EX_LIKE = (
    re.compile(r"\bassert\b"),
    re.compile(r"^\s*>>>", re.M),
    re.compile(r"==\s*\S"),
)


def main() -> int:
    rows = [json.loads(l) for l in SUITE.read_text(encoding="utf-8").splitlines() if l.strip()]
    low = json.loads(ANALYSE.read_text(encoding="utf-8"))["low"]
    low_set = set(low)

    out = {"n": len(rows), "cells": {}, "low": sorted(low_set)}

    print("=" * 92)
    print("A. 每题：默认档 vs 开 exec 档，判据条数与能否判对错")
    print("=" * 92)
    print(f"{'cell':26}{'low':>4}{'默认判据':>9}{'exec判据':>9}{'示例数':>7}"
          f"{'题面像有例':>11}  判据名（exec 档）")
    n_def_judge = n_exec_judge = 0
    n_exlike_but_zero = 0
    for r in rows:
        tid, prompt = r["id"], r["prompt"]
        d0 = derive(prompt, static=0, exec_=0)
        d1 = derive(prompt, static=0, exec_=1)
        ex = example_asserts(prompt)
        exlike = any(p.search(prompt) for p in EX_LIKE)
        can_judge0 = any(c.kind in ("example",) for c in d0)
        can_judge1 = any(c.kind == "example" for c in d1)
        n_def_judge += int(can_judge0)
        n_exec_judge += int(can_judge1)
        if exlike and not ex:
            n_exlike_but_zero += 1
        out["cells"][tid] = {
            "low": tid in low_set,
            "kinds_default": [c.kind for c in d0],
            "kinds_exec": [c.kind for c in d1],
            "n_examples": len(ex),
            "examples": list(ex),
            "prompt_has_example_like": bool(exlike),
            "prompt_chars": len(prompt),
        }
        print(f"{tid:26}{'Y' if tid in low_set else '.':>4}{len(d0):>9}{len(d1):>9}"
              f"{len(ex):>7}{'Y' if exlike else '.':>11}  "
              f"{','.join(c.kind for c in d1)}")

    print()
    print("=" * 92)
    print("B. 汇总")
    print("=" * 92)
    print(f"题数                              {len(rows)}")
    print(f"默认档能判对错的题                {n_def_judge}")
    print(f"verify_exec=1 能判对错的题        {n_exec_judge}")
    print(f"题面『像有期望值』但抽不出的题    {n_exlike_but_zero}")
    n_low_blind = sum(1 for t in low_set
                      if not any(c == "example" for c in out["cells"][t]["kinds_exec"]))
    print(f"13 格里 exec 档仍判不了对错的      {n_low_blind}/{len(low_set)}")
    print()
    print("13 格逐条（exec 档）:")
    print(f"{'cell':26}{'示例数':>7}{'题面像有例':>11}  判据名")
    for t in sorted(low_set):
        c = out["cells"][t]
        print(f"{t:26}{c['n_examples']:>7}{'Y' if c['prompt_has_example_like'] else '.':>11}  "
              f"{','.join(c['kinds_exec'])}")

    out["summary"] = {
        "n_tasks": len(rows),
        "n_judge_default": n_def_judge,
        "n_judge_exec": n_exec_judge,
        "n_exlike_but_zero": n_exlike_but_zero,
        "n_low_blind_under_exec": n_low_blind,
    }
    p = EV / "gate_v255.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n⇒ 写 {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
