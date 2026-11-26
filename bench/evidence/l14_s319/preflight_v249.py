"""V249 preflight：接档的**结构性**取证。不调真模型（用桩），零成本。

判据见 `PREREG_v249.md` §4：
  E1 默认档逐字节不变（t01 的 2307 / 1bdea4fc92d67817）
  E2 无 trace 的默认档仍是 AZ163a 的 2818c5673cc0884d
  E3 `--goals-swap-edit` 已注册且默认 off
  D1~D4 装置（apply_edit）正例 + 四路 fail-closed
  S1 line 档端到端：桩给 `LINE: 16 / EXPR: int(parts[0])` ⇒ 候选分数 1.0
  S2 off 档端到端：桩给代码块 ⇒ 走 parse_proposal，行为与旧路径同
  S3 非法 edit 值 ⇒ ValueError（⛔ 不静默回落 off）
  S4 line 档失败（LINE 越界）⇒ 记 `edit:oom` 且**不产出候选**

跑：python3 preflight_v249.py
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw import llm as _llm_mod  # noqa: E402
from mw.reflect import (apply_edit, build_proposal_prompt,  # noqa: E402
                        failed_actuals, parse_edit, trace_failure, verify)

D = pathlib.Path(__file__).resolve().parent
START = ROOT / "bench/evidence/l9_s310/cand/90579ecebe633352.py"
SUITE = ROOT / "bench/evidence/l5_s266/suite_one/t01.jsonl"

CHECKS: list[dict] = []


def rec(tag: str, ok: bool, detail: str) -> None:
    CHECKS.append({"id": tag, "ok": bool(ok), "detail": detail})


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def main() -> int:
    import run as _run
    task_d = json.loads(SUITE.read_text(encoding="utf-8").strip().splitlines()[0])
    goal, tests = task_d["prompt"], task_d["tests"]
    code = START.read_text(encoding="utf-8")
    base = verify(code, tests)
    why = ("通过 %d/%d；失败: assert parse_duration('10m') == 600 ⇒ AssertionError"
           % (base, len(tests)))
    acts, tr = failed_actuals(code, tests), trace_failure(code, tests)

    # ---- E1 / E2：默认档逐字节不变 ----
    A = build_proposal_prompt(goal, code, why, acts, trace=tr)
    rec("E1_default_t01", (len(A.encode()), sha(A)) == (2307, "1bdea4fc92d67817"),
        f"{len(A.encode())}B/{sha(A)}")
    B = build_proposal_prompt("G", "def f(): pass", "W", ("a1", "a2"))
    rec("E2_default_notrace", sha(B) == "2818c5673cc0884d", sha(B))

    # ---- E3：参数已注册且默认 off ----
    h = subprocess.run([sys.executable, str(ROOT / "run.py"), "--help"],
                       capture_output=True, text=True, cwd=ROOT)
    rec("E3_arg_registered", "--goals-swap-edit" in (h.stdout or ""),
        "help 里有没有这个名字")
    # ⚠️ 第一版这里写成「拿不到 parser 就跳过」——那就是**假通过**（跳过与通过同形，
    #    纪律 127 的同型）。改成三条**行为**断言：
    import inspect
    _dflt = inspect.signature(_run._swap_candidates).parameters["edit"].default
    rec("E3b_signature_default_off", _dflt == "off", f"_swap_candidates(edit=) 默认 {_dflt!r}")
    #: argparse 是否真的接受 `line` / 拒 `both`：用 `--help` 让它在校验完参数后退出，
    #: 这样既走完 choices 校验，又不跑主逻辑。
    ok_line = subprocess.run([sys.executable, str(ROOT / "run.py"),
                              "--goals-swap-edit", "line", "--help"],
                             capture_output=True, text=True, cwd=ROOT)
    bad_line = subprocess.run([sys.executable, str(ROOT / "run.py"),
                               "--goals-swap-edit", "both", "--help"],
                              capture_output=True, text=True, cwd=ROOT)
    rec("E3c_choices_behavior", ok_line.returncode == 0 and bad_line.returncode != 0,
        f"line→rc={ok_line.returncode} both→rc={bad_line.returncode}（⛔ 不静默回落）")
    _src = (ROOT / "run.py").read_text(encoding="utf-8")
    _decl = re.search(r'"--goals-swap-edit",\s*default="(\w+)"', _src)
    rec("E3d_source_default_off", bool(_decl) and _decl.group(1) == "off",
        f"源码声明 default={_decl.group(1) if _decl else '?'}")

    # ---- D：装置 ----
    n1, e1 = apply_edit(code, 16, "minutes = int(parts[0])")
    rec("D1_pos_6of6", not e1 and verify(n1, tests) == len(tests), f"verify={verify(n1, tests)}")
    errs = {apply_edit(code, 99, "x")[1], apply_edit(code, None, "x")[1],
            apply_edit(code, 16, "")[1],
            apply_edit(code, 16, "minutes = = 1")[1].split(":")[0]}
    rec("D2_failclosed", errs == {"oom", "parse_fail", "empty_expr", "syntax_fail"}, str(sorted(errs)))
    rec("D3_indent_by_device", n1.splitlines()[15].startswith(" " * 16),
        repr(n1.splitlines()[15]))
    rec("D4_parse_edit", parse_edit("LINE: 16\nEXPR: minutes = int(parts[0])")
        == (16, "minutes = int(parts[0])")
        and parse_edit("我觉得第 16 行有问题") == (None, None),
        "独占行才算，正文提到的「第 16 行」不许抓")

    # ---- S：桩式端到端 ----
    class _Scored:
        def __init__(self, v, vec):
            self.score, self.vec = v, vec

    class _Scorer:
        def score(self, task_dict, cand):
            v = verify(cand, tests)
            return _Scored(v / len(tests), [bool(x) for x in [1] * v + [0] * (len(tests) - v)])

    # ⚠️ 用 SimpleNamespace 而不是 class：class 体里写 `tests = tests` 会
    #   因作用域规则（类体不闭包外层函数变量）抛 NameError —— 本轮实际踩到。
    _Task = types.SimpleNamespace(
        prompt=goal, tests=tests,
        as_dict=lambda: {"prompt": goal, "tests": list(tests)})

    class _Stub:
        def __init__(self, reply):
            self.reply, self.seen = reply, None

        def complete(self, messages, max_tokens=None):
            self.seen = messages[0].content
            return self.reply

    _orig = _llm_mod.build_llm
    try:
        # S1: line 档，桩给正确的 LINE/EXPR
        stub = _Stub("LINE: 16\nEXPR: minutes = int(parts[0])")
        _llm_mod.build_llm = lambda spec: stub
        core = types.SimpleNamespace(s=types.SimpleNamespace(llm=object()))
        bc, bs, rows = _run._swap_candidates(
            core, _Scorer(), _Task, code, base / len(tests), 1, edit="line")
        rec("S1_line_end2end", abs(bs - 1.0) < 1e-9 and rows[0].get("line") == 16
            and rows[0].get("expr") == "minutes = int(parts[0])",
            f"best={bs} line={rows[0].get('line')} sha={sha(bc)}")
        rec("S1b_line_prompt_is_edit_form",
            "LINE: <行号" in (stub.seen or "") and "```python" not in (stub.seen or ""),
            "发出去的提示确实是 edit 形态")

        # S2: off 档，桩给代码块 —— 走 parse_proposal，提示里不许出现 LINE:
        good = "```python\n" + code.replace(
            "minutes = int(parts[1].split('m')[0])\n                return minutes * 60",
            "minutes = int(parts[0])\n                return minutes * 60") + "```"
        stub2 = _Stub(good)
        _llm_mod.build_llm = lambda spec: stub2
        bc2, bs2, rows2 = _run._swap_candidates(
            core, _Scorer(), _Task, code, base / len(tests), 1, edit="off")
        rec("S2_off_end2end", abs(bs2 - 1.0) < 1e-9 and "LINE:" not in (stub2.seen or "")
            and "```python" in (stub2.seen or ""),
            f"best={bs2} 提示含 LINE:={'LINE:' in (stub2.seen or '')}")

        # S3: 非法档位 ⇒ 抛
        try:
            _run._swap_candidates(core, _Scorer(), _Task, code, 0.5, 1, edit="both")
            rec("S3_bad_value_raises", False, "没抛")
        except ValueError as e:
            rec("S3_bad_value_raises", True, str(e)[:44])

        # S4: line 档失败 ⇒ 记 edit:oom、不产出候选
        stub3 = _Stub("LINE: 99\nEXPR: pass")
        _llm_mod.build_llm = lambda spec: stub3
        _, bs4, rows4 = _run._swap_candidates(
            core, _Scorer(), _Task, code, base / len(tests), 1, edit="line")
        rec("S4_line_fail_no_cand", abs(bs4 - base / len(tests)) < 1e-9
            and rows4[0].get("err") == "edit:oom" and not rows4[0].get("code"),
            f"err={rows4[0].get('err')} code={'有' if rows4[0].get('code') else '无'}")
    finally:
        _llm_mod.build_llm = _orig

    ok = all(c["ok"] for c in CHECKS)
    for c in CHECKS:
        print(f"  {c['id']:26s} {'PASS' if c['ok'] else 'FAIL'}  {c['detail']}")
    (D / "preflight_v249.json").write_text(
        json.dumps({"ok": ok, "checks": CHECKS}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("ALL_PASS" if ok else "HAS_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
