#!/usr/bin/env python3
"""V237 免费测温（零模型、零网络）：两个 5/6 之间到底差什么。

⛔ 这是**可达性**测量，不是实验。它回答三件事，全部本地可算：
  1. 两个 5/6 的失败格是不是**互补**的（若同格，组合没意义）。
  2. 各自离 6/6 多远（字符级最小编辑 + 行级差异）⇒「一次修一处」是不是字面属实。
  3. 手工合并能不能 6/6 ⇒ **正例闸**：判据看得见满分（纪律 62/129）。

用法：python3 free_v237.py
"""
from __future__ import annotations

import difflib
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT))

from mw.bench import ExternalScorer          # noqa: E402
from mw.reflect import as_code, sha_of       # noqa: E402
from mw.verify import extract_code           # noqa: E402


def task() -> dict:
    p = ROOT / "bench" / "evidence" / "l5_s266" / "suite_one" / "t01.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()][0]


def make_scorer():
    sp = ROOT / "bench" / "scorers" / "code_exec.py"
    return ExternalScorer([sys.executable, str(sp)], source=sp)


def vec_str(v) -> str:
    return "".join("1" if x else "0" for x in (v or ()))


def edits(a: str, b: str) -> dict:
    """字符级与行级距离。⛔ 口径先写：`chars` = 最短编辑脚本长度，`lines` = 差异行数。"""
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    chars = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes()
                if tag != "equal")
    dl = [ln for ln in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0)
          if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    return {"chars": chars, "lines": len(dl)}


def bodies() -> tuple[str, str, str, str]:
    """四份体：A、B、A'（A 的最小修）、B'（B 的最小修）。

    ⛔ 唯一构造点 —— `probe_v237.py` 的自检也 import 这里，
       否则两处各写一遍「最小修」，改了一边就开始测两个实验（§308 同类坑）。
    """
    A = (HERE.parent / "l6_s307" / "start_answer.py").read_text(encoding="utf-8")
    B_raw = (HERE.parent / "l6_s307" / "raw_v236" / "out_B_think0_900.txt").read_text(encoding="utf-8")
    B = extract_code(B_raw)
    #: A 的洞：m 分支读了 `parts[1]`（`'10m'.split('m') == ['10','']` ⇒ `int('')` 抛 ⇒ None）
    A_fix = A.replace("minutes = int(parts[1].split('m')[0])\n                return minutes * 60",
                      "minutes = int(parts[0])\n                return minutes * 60")
    assert A_fix != A, "A 的最小修没命中 —— 先重新定位洞，别硬跑"
    #: B 的洞：什么都没匹配上时返回 0（应 None）
    B_fix = B.replace("        return total_seconds\n",
                      "        if total_seconds == 0 and not any(u in s for u in 'hms'):\n"
                      "            return None\n"
                      "        return total_seconds\n")
    assert B_fix != B, "B 的最小修没命中"
    return A, B, A_fix, B_fix


def main() -> int:
    t = task()
    sc = make_scorer()
    tests = t["tests"]
    A, B, A_fix, B_fix = bodies()

    print("=" * 78)
    print("0. 两份体")
    for tag, src in (("A", A), ("B", B)):
        print(f"  {tag}: sha={sha_of(src)}  {len(src)} 字符  {len(src.splitlines())} 行")

    print("=" * 78)
    print("1. 判分（真打分器子进程，与账本同一把尺子）")
    for tag, src in (("A", A), ("B", B)):
        r = sc.score(t, src)
        print(f"  {tag}: score={r.score}  vec={vec_str(r.vec)}  why={(r.why or '').splitlines()[0][:90] if r.why else ''}")
        for i, te in enumerate(tests):
            if not r.vec[i]:
                print(f"       ✗ [{i}] {te}")

    print("=" * 78)
    print("2. 互补性（能不能被「各取一半」拼成 6/6）")
    ra, rb = sc.score(t, A), sc.score(t, B)
    va, vb = ra.vec, rb.vec
    both_bad = [i for i in range(len(va)) if not va[i] and not vb[i]]
    only_a = [i for i in range(len(va)) if not va[i] and vb[i]]
    only_b = [i for i in range(len(va)) if va[i] and not vb[i]]
    print(f"  两臂都挂的格: {both_bad}  只 A 挂: {only_a}  只 B 挂: {only_b}")
    print(f"  ⇒ 互补={'是' if not both_bad and only_a and only_b else '否'}")

    print("=" * 78)
    print("3. 各自离 6/6 多远（手工最小修）")
    for tag, src in (("A'", A_fix), ("B'", B_fix)):
        r = sc.score(t, src)
        print(f"  {tag}: score={r.score}  vec={vec_str(r.vec)}  sha={sha_of(src)}")
    print(f"  A → A'  {edits(A, A_fix)}   ← 修 A 的代价")
    print(f"  B → B'  {edits(B, B_fix)}   ← 修 B 的代价")
    print(f"  A → B   {edits(A, B)}   ← 两体之间的距离（组合要跨过它）")

    print("=" * 78)
    print("4. 正例闸：判据看得见满分？（若这里不是 1.0，后面所有 0 都不可解释）")
    ok = True
    for tag, src in (("A'", A_fix), ("B'", B_fix)):
        r = sc.score(t, src)
        good = abs(r.score - 1.0) < 1e-9
        ok = ok and good
        print(f"  {tag}: {r.score}  {'✅' if good else '❌'}")
    print("FREE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
