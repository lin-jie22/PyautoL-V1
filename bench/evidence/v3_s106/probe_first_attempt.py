#!/usr/bin/env python3
"""D0 探针（第 106 轮 §76）：**反馈前**的首答里，有多少题「差一个名字」。

预注册 `bench/evidence/v3_s106/PREREG.md` §6 把决策规则写死在这里：

    `nc` 组首答里含未定义名的题 **< 2 道** ⇒ 判「题面构造没把占比抬起来」，不跑 B 臂。

⛔ 为什么必须用**首答**而不是最终交付：最终交付已经吃过了反馈，
   拿它算「机会」会把机制的效应算进机会里（选择偏差）。
   首答在任何反馈之前产生，所以它**与臂无关** —— 这也是 N7 的内容。

⛔ 为什么用 `mw.verify.undefined_names` 而不是自己写个 import 检查：
   要与内核那个载荷**同源**（§75.2 第 3 步），否则量的是另一个东西。

用法：
    python3 bench/evidence/v3_s106/probe_first_attempt.py \
        --answers /tmp/v1_s106_bench_a1_v5/answers.jsonl
    python3 bench/evidence/v3_s106/probe_first_attempt.py --selftest
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "bench" / "scorers") not in sys.path:
    sys.path.insert(0, str(ROOT / "bench" / "scorers"))

from mw.verify import undefined_names          # noqa: E402
from code_exec import extract_code            # noqa: E402


def first_answers(path: str) -> dict[str, dict]:
    """台账**行序 = 尝试序**（§75.7 的既有事实）⇒ 每个 task_id 的第一次出现就是首答。"""
    out: dict[str, dict] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("kind") != "answer":
                continue
            tid = d.get("task_id")
            if tid and tid not in out:
                out[tid] = d
    return out


def classify(answers: dict[str, dict]) -> list[dict]:
    rows = []
    for tid in sorted(answers):
        d = answers[tid]
        code = extract_code(d.get("answer") or "")
        names = undefined_names(code)
        rows.append({
            "task": tid,
            "rep": d.get("rep"),
            "sha": (d.get("sha256") or "")[:8],
            "undecidable": names is None,
            "names": [nm for _fn, nm in (names or [])],
        })
    return rows


def report(rows: list[dict]) -> dict:
    nc = [r for r in rows if r["task"].startswith("nc")]
    ct = [r for r in rows if r["task"].startswith("ct")]
    return {
        "nc_hit": [r["task"] for r in nc if r["names"]],
        "nc_n": len([r for r in nc if r["names"]]),
        "ct_hit": [r["task"] for r in ct if r["names"]],
        "ct_n": len([r for r in ct if r["names"]]),
        "rows": rows,
    }


def selftest() -> int:
    """反证：构两份假台账，探针必须分别判「有名字」和「没名字」。

    ⛔ 学 §75.7.2：构造**没生效就抛**，不许静默 —— 反证失效等于没有反证。
    """
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "answers.jsonl"
        rows = [
            {"kind": "header"},
            {"kind": "answer", "task_id": "nc01", "rep": 1, "sha256": "a" * 64,
             "answer": "```python\ndef scrub_tags(rows, path):\n"
                       "    return re.sub('x', '', rows[0])\n```"},
            {"kind": "answer", "task_id": "nc02", "rep": 1, "sha256": "b" * 64,
             "answer": "```python\nimport json\nimport os\n"
                       "def save_counts(rows, path):\n"
                       "    os.makedirs('o', exist_ok=True)\n"
                       "    return json.dumps({})\n```"},
            {"kind": "answer", "task_id": "ct01", "rep": 1, "sha256": "c" * 64,
             "answer": "```python\ndef write_ranked(rows, path):\n"
                       "    open(path, 'w').write('')\n```"},
            # 同一题的第二份（必须被忽略：首答才算）
            {"kind": "answer", "task_id": "nc01", "rep": 1, "sha256": "d" * 64,
             "answer": "```python\nimport re\n"
                       "def scrub_tags(rows, path):\n    return re.sub('x','',rows[0])\n```"},
        ]
        p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                     encoding="utf-8")
        rep = report(classify(first_answers(str(p))))

    cases = [
        ("S1 首答缺 re ⇒ nc01 必须被计入",
         rep["nc_hit"] == ["nc01"], rep["nc_hit"]),
        ("S2 首答带了 import ⇒ nc02 不许被计入",
         "nc02" not in rep["nc_hit"], rep["nc_hit"]),
        ("S3 同一题的第二份不许覆盖首答",
         [r["sha"] for r in rep["rows"] if r["task"] == "nc01"] == ["aaaaaaaa"],
         [r["sha"] for r in rep["rows"] if r["task"] == "nc01"]),
        ("S4 控制组照实统计（本题没名字 ⇒ 空）",
         rep["ct_hit"] == [], rep["ct_hit"]),
    ]
    for why, ok, got in cases:
        print(f"  {'✅' if ok else '❌'} {why}  实测={got}")
        bad += 0 if ok else 1
    print(f"{'✅ 自证 4/4' if bad == 0 else f'❌ {bad} 条自证失败'}")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.answers:
        print("缺 --answers（或 --selftest）", file=sys.stderr)
        return 2
    rep = report(classify(first_answers(a.answers)))
    print(f"{'题':6s} {'sha':9s} {'判定':10s} 未定义名")
    for r in rep["rows"]:
        verdict = "不可判定" if r["undecidable"] else ("有名字" if r["names"] else "—")
        print(f"  {r['task']:6s} {r['sha']:9s} {verdict:10s} {r['names']}")
    print(f"nc 组含未定义名的题：{len(rep['nc_hit'])}/3 {rep['nc_hit']}")
    print(f"ct 组含未定义名的题：{len(rep['ct_hit'])}/3 {rep['ct_hit']}")
    print(f"D0_N={rep['nc_n']}")
    if a.json:
        Path(a.json).write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
