#!/usr/bin/env python3
"""V198 —— 交付闸在**真实作答**上有没有判别力。

⛔ 零模型、零内核改动、零新答案：
   · G1 = V194 的 33 条真实答案 × 真闸（vfy.derive(goal, 2, 0) + vfy.run）
   · G2 = 10 条满分答案人为破坏（必须语法错，用 compile() 自校验）
   · G3 = V197 的空实现形态 × 23 题（函数名取自题面）

跑完把逐格读数写 readings_v198.json；汇总打印到 stdout。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
SUITE_DIR = ROOT / "bench" / "heldout"
EXTRA_SUITE = ROOT / "bench" / "tasks" / "coding_small.jsonl"
FROZEN = ROOT / "bench" / "evidence" / "l3_s282" / "answers_v194.jsonl"
READINGS = ROOT / "bench" / "evidence" / "l3_s282" / "readings_v194.json"

sys.path.insert(0, str(ROOT))
import mw.verify as vfy  # noqa: E402

VERIFY_STATIC = 2   # 部署默认
VERIFY_EXEC = 0     # 部署默认


# ---------------------------------------------------------------- 装载
def load_suites() -> dict[str, dict[str, dict]]:
    """suite 名（stem，⛔ 不是文件名）→ {task_id: 题记录}。"""
    out: dict[str, dict[str, dict]] = {}
    for p in sorted(SUITE_DIR.glob("*.jsonl")) + [EXTRA_SUITE]:
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        out[p.stem] = {str(r["id"]): r for r in rows}
    return out


def load_answers() -> dict[tuple[str, str], str]:
    """(suite, task_id) → 答案原文。套件靠 header 的 run_id 反查。"""
    rows = [json.loads(l) for l in FROZEN.read_text(encoding="utf-8").splitlines() if l.strip()]
    run2suite = {r["run_id"]: str((r.get("meta") or {}).get("suite") or "")
                 for r in rows if r.get("kind") == "header"}
    out: dict[tuple[str, str], str] = {}
    for r in rows:
        if r.get("kind") != "answer":
            continue
        suite = run2suite.get(r["run_id"], "")
        out[(suite, str(r["task_id"]))] = str(r.get("answer") or "")
    return out


def load_scores() -> dict[tuple[str, str], float]:
    d = json.loads(READINGS.read_text(encoding="utf-8"))
    return {(str(r["suite"]), str(r["id"])): float(r["score"]) for r in d["rows"]}


# ---------------------------------------------------------------- 工具
FN_PATTERNS = (
    re.compile(r"函数名(?:必须|应)(?:是|为)\s*`?\s*([A-Za-z_]\w*)"),
    re.compile(r"def\s+([A-Za-z_]\w*)\s*\("),
)


def fn_name(prompt: str) -> str:
    for rx in FN_PATTERNS:
        m = rx.search(prompt or "")
        if m:
            return m.group(1)
    return ""


def code_body(ans: str) -> str:
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", ans, re.S)
    return m.group(1) if m else ans


def break_into_syntax_error(ans: str) -> tuple[str, bool]:
    """把答案改成**确定**语法错。返回 (新答案, 是否真的错)。"""
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", ans, re.S)
    if m:
        lines = m.group(1).split("\n")
        # 插一个未匹配的右括号在**第二行**（非缩进）—— 层内层外都必然 SyntaxError
        pos = 1 if len(lines) > 1 else 0
        broken_lines = lines[:pos] + [")"] + lines[pos:]
        broken = "\n".join(broken_lines)
        new_ans = ans[:m.start(1)] + broken + ans[m.end(1):]
    else:
        broken = "\n)\n" + ans
        new_ans = broken
    try:
        compile(broken, "<brk>", "exec")
        return new_ans, False          # 没破成
    except SyntaxError:
        return new_ans, True


def run_gate(goal: str, ans: str, static: int = VERIFY_STATIC, exec_: int = VERIFY_EXEC):
    checks = vfy.derive(goal, static, exec_)
    v = vfy.run(ans, checks)
    return {
        "ok": bool(v.ok),
        "failed": list(v.failed),
        "report": str(v.report or ""),
        "n_checks": len(checks),
        "check_names": [c.name for c in checks],
        "check_kinds": [c.kind for c in checks],
    }


# ---------------------------------------------------------------- 主
def main() -> int:
    suites = load_suites()
    answers = load_answers()
    scores = load_scores()

    keys = sorted(k for k in answers if k in scores and k[0] in suites and k[1] in suites[k[0]])
    missing = [k for k in answers if k not in keys]
    print(f"[池] answers={len(answers)}  scores={len(scores)}  可 join={len(keys)}  丢掉={len(missing)}")
    for k in missing[:5]:
        print(f"     ⚠️ 丢 {k}")

    cells = []
    # ---- G1
    for suite, tid in keys:
        task = suites[suite][tid]
        goal = str(task.get("prompt") or "")
        ans = answers[(suite, tid)]
        g = run_gate(goal, ans)
        cells.append({"arm": "G1", "suite": suite, "id": tid, "score": scores[(suite, tid)],
                      "n_checks": g["n_checks"], "check_names": g["check_names"],
                      "check_kinds": g["check_kinds"], "gate_ok": g["ok"],
                      "failed": g["failed"], "report": g["report"][:200],
                      "has_fn": bool(fn_name(goal)), "answer_chars": len(ans)})

    # ---- G2：满分答案破坏
    perfect = [k for k in keys if scores[k] >= 0.999]
    for suite, tid in perfect:
        task = suites[suite][tid]
        goal = str(task.get("prompt") or "")
        broken_ans, really_broken = break_into_syntax_error(answers[(suite, tid)])
        g = run_gate(goal, broken_ans)
        cells.append({"arm": "G2", "suite": suite, "id": tid, "score": scores[(suite, tid)],
                      "n_checks": g["n_checks"], "check_names": g["check_names"],
                      "check_kinds": g["check_kinds"], "gate_ok": g["ok"],
                      "failed": g["failed"], "report": g["report"][:200],
                      "really_broken": really_broken, "answer_chars": len(broken_ans)})

    # ---- G3：空实现（函数名取自题面）
    no_fn = 0
    for suite, tid in keys:
        task = suites[suite][tid]
        goal = str(task.get("prompt") or "")
        fn = fn_name(goal)
        if not fn:
            no_fn += 1
            continue
        ans = "```python\ndef %s(*a, **k):\n    return None\n```" % fn
        g = run_gate(goal, ans)
        cells.append({"arm": "G3", "suite": suite, "id": tid, "score": scores[(suite, tid)],
                      "n_checks": g["n_checks"], "check_names": g["check_names"],
                      "check_kinds": g["check_kinds"], "gate_ok": g["ok"],
                      "failed": g["failed"], "report": g["report"][:200],
                      "fn": fn, "answer_chars": len(ans)})

    (OUT / "readings_v198.json").write_text(
        json.dumps({"static": VERIFY_STATIC, "exec": VERIFY_EXEC,
                    "cells": cells, "no_fn_g3": no_fn}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    # ================================================================ 汇总
    def sel(arm):
        return [c for c in cells if c["arm"] == arm]

    g1 = sel("G1")
    print()
    print("=" * 78)
    print(f"G1 全池 n={len(g1)}（闸 static={VERIFY_STATIC} exec={VERIFY_EXEC}）")
    print("=" * 78)

    # 判据清单（S1）
    names = g1[0]["check_names"] if g1 else []
    kinds = g1[0]["check_kinds"] if g1 else []
    print(f"S1 判据 {len(names)} 条：{names}")
    print(f"   kind：{kinds}   example 条数={sum(1 for k in kinds if k == 'example')}")

    # S2
    print(f"S2 has_fn = {sum(1 for c in g1 if c['has_fn'])}/{len(g1)}")

    tp = [c for c in g1 if c["gate_ok"]]
    tf = [c for c in g1 if not c["gate_ok"]]
    print(f"\nG1 pass={len(tp)}  fail={len(tf)}   pass率={len(tp)/max(1,len(g1)):.3f}")

    # 混淆矩阵
    def m(rows, cond):
        return [c for c in rows if cond(c["score"])]
    cm = {
        "pass×1.0": len(m(tp, lambda s: s >= 0.999)),
        "pass×<1": len(m(tp, lambda s: s < 0.999)),
        "fail×1.0": len(m(tf, lambda s: s >= 0.999)),
        "fail×<1": len(m(tf, lambda s: s < 0.999)),
    }
    print(f"\n混淆矩阵：{cm}")

    def mean(xs):
        return sum(xs) / len(xs) if xs else float("nan")

    print(f"\npass 组：n={len(tp)} mean={mean([c['score'] for c in tp]):.4f} "
          f"score<1 占比={len(m(tp, lambda s: s < 0.999))/max(1,len(tp)):.3f}")
    print(f"fail 组：n={len(tf)} mean={mean([c['score'] for c in tf]):.4f}")
    if tp and tf:
        print(f"  差值 pass−fail = {mean([c['score'] for c in tp]) - mean([c['score'] for c in tf]):+.4f}")
    else:
        print("  ⚠️ fail 组为空 ⇒ P4 判 UNKNOWN（判别力按 0 记，不是\"闸准\"）")

    # 判据触发次数
    print("\n各判据触发次数（G1）：")
    trig: dict[str, int] = {}
    for c in tf:
        for f in c["failed"]:
            key = str(f).split("×")[0]
            trig[key] = trig.get(key, 0) + 1
    for k, v in sorted(trig.items(), key=lambda kv: -kv[1]):
        print(f"   {k:16s} {v}")

    # 满分格（P2）
    pf = [c for c in g1 if c["score"] >= 0.999]
    print(f"\nP2 满分 {len(pf)} 格 pass={sum(1 for c in pf if c['gate_ok'])}")

    # G2
    g2 = sel("G2")
    print(f"\nG2 破坏满分答案 n={len(g2)}  fail={sum(1 for c in g2 if not c['gate_ok'])}  "
          f"真的破了={sum(1 for c in g2 if c.get('really_broken'))}")
    for c in g2:
        if c["gate_ok"]:
            print(f"   ❌ 没拦住：{c['suite']}/{c['id']}  failed={c['failed']} really={c.get('really_broken')}")

    # G3
    g3 = sel("G3")
    print(f"\nG3 空实现 n={len(g3)}（函数名取自题面的题；NO_FN 落空 {no_fn} 题）")
    print(f"   pass={sum(1 for c in g3 if c['gate_ok'])}  fail={sum(1 for c in g3 if not c['gate_ok'])}")
    for c in g3:
        if not c["gate_ok"]:
            print(f"   fail 格：{c['suite']}/{c['id']} fn={c['fn']} failed={c['failed']}")

    print("\nreadings →", OUT / "readings_v198.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
