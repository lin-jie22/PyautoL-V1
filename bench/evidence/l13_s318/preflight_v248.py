"""V248 preflight：构造三臂提示 + 自检单行替换装置。不调用模型，零成本。

三臂：
  A  = v247 的 A 臂逐字节（2307 bytes / 1bdea4fc92d67817），整函数重写
  N  = A 但【当前代码】段改成带行号（其余逐字节同）
  E  = N 的输入但把「输出完整函数」换成「只输出 LINE / EXPR 两行」，装置执行替换

A-vs-N 分离「代码带不带行号」；N-vs-E 分离「输出形态（整函数 vs 两行标签）」。

跑：python3 preflight_v248.py   （断言全过才写 preflight_v248.json）
"""
from __future__ import annotations

import ast
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.reflect import (build_proposal_prompt, failed_actuals,  # noqa: E402
                        trace_failure, verify)

D = pathlib.Path(__file__).resolve().parent
START = ROOT / "bench/evidence/l9_s310/cand/90579ecebe633352.py"
SUITE = ROOT / "bench/evidence/l5_s266/suite_one/t01.jsonl"

A_BYTES, A_SHA = 2307, "1bdea4fc92d67817"     # v247 实测，本轮必须逐字节复现


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def numbered(code: str) -> str:
    """带行号视图：真实坐标，格式 `{n:3d}| {原文}`。"""
    return "\n".join(f"{i:3d}| {ln}" for i, ln in enumerate(code.splitlines(), 1))


# ---------------------------------------------------------------- 装置：单行替换
def apply_edit(code: str, line: int | None, expr: str | None) -> tuple[str | None, str]:
    """把第 line 行整行替换成 expr。返回 (新代码, err)；err 非空即 fail-closed。

    缩进口径：新行 = 原行前导空白 + expr.lstrip()。原缩进由装置保留，
    不让模型负责缩进（否则「缩进写错」会被算成模型结论）。
    """
    if line is None:
        return None, "parse_fail"
    if expr is None or not expr.strip():
        return None, "empty_expr"
    lines = code.splitlines()
    if not (1 <= line <= len(lines)):
        return None, "oom"                       # out of range
    ind = lines[line - 1][: len(lines[line - 1]) - len(lines[line - 1].lstrip())]
    new = list(lines)
    new[line - 1] = ind + expr.strip().lstrip("|- ").strip()
    out = "\n".join(new) + "\n"
    try:
        ast.parse(out)
    except SyntaxError as e:
        return None, f"syntax_fail: {e.msg}"
    return out, ""


LINE_RE = re.compile(r"^\s*LINE\s*[:：]\s*(\d+)\s*$", re.M)
EXPR_RE = re.compile(r"^\s*EXPR\s*[:：]\s*(.+)$", re.M)


def parse_edit(text: str) -> tuple[int | None, str | None]:
    m1, m2 = LINE_RE.search(text or ""), EXPR_RE.search(text or "")
    return (int(m1.group(1)) if m1 else None,
            m2.group(1).strip() if m2 else None)


# ---------------------------------------------------------------- 提示构造
def build(goal: str, code: str, why: str, tests: list[str]) -> dict:
    acts = failed_actuals(code, tests)
    tr = trace_failure(code, tests)
    A = build_proposal_prompt(goal, code, why, acts, trace=tr)
    # N：只把代码段换成带行号版本（断言只差这一处）
    nb = numbered(code)
    N = A.replace(code, nb, 1)
    # E：把 A 的「输出要求」段整段换掉（断言只差这一段）
    TAIL_A = (
        "请顺着轨迹找到**第一处**让结果偏离题意的逻辑，只改那一处（以及必须连带改的地方），不要整篇重写。\n"
        "要求：\n"
        "1) 只输出一个 ```python 代码块，里面是**完整的**函数定义（不要省略任何行）；\n"
        "2) 保持函数名与签名不变；\n"
        "3) 不要解释，不要输出任何其他文字。"
    )
    TAIL_E = (
        "请顺着轨迹找到**第一处**让结果偏离题意的逻辑，只改那一处。\n"
        "【输出格式】不要输出代码块，不要输出完整函数。**只输出两行**，严格如下（Line 用上面给的行号）：\n"
        "LINE: <行号，纯数字>\n"
        "EXPR: <把那一行整行替换成的 Python 语句；不要写行号，不要写缩进>\n"
        "【硬约束】除这一行外**任何一行都不许改**——脚本只会按你给的 LINE 替换那一行，"
        "其余字节原样保留。你若想改别处，改了也不会生效。"
    )
    E = N.replace(TAIL_A, TAIL_E, 1)
    return {"A": A, "N": N, "E": E, "acts": acts, "trace": tr,
            "tail_a": TAIL_A, "tail_e": TAIL_E, "nb": nb}


def main() -> int:
    task = json.loads(SUITE.read_text(encoding="utf-8").strip().splitlines()[0])
    goal, tests = task["prompt"], task["tests"]
    code = START.read_text(encoding="utf-8")
    base = verify(code, tests)
    why = "通过 %d/%d；失败: assert parse_duration('10m') == 600 ⇒ AssertionError" % (
        base, len(tests))

    p = build(goal, code, why, tests)
    A, N, E = p["A"], p["N"], p["E"]
    chk = []

    def rec(tag, ok, detail):
        chk.append({"id": tag, "ok": bool(ok), "detail": detail})

    # P1 A 逐字节复现 v247
    rec("P1_A_bytes_sha", len(A.encode()) == A_BYTES and sha(A) == A_SHA,
        f"{len(A.encode())}B / {sha(A)}（期望 {A_BYTES} / {A_SHA}）")
    # P2 代码段在 A 中恰好 1 次
    rec("P2_code_once", A.count(code) == 1, f"count={A.count(code)}")
    # P3 N 与 A 只差代码段
    rec("P3_N_diff_only_code", N.replace(p["nb"], code, 1) == A,
        f"N={len(N.encode())}B / {sha(N)}")
    # P4 E 与 N 只差输出要求段
    rec("P4_E_diff_only_tail", p["tail_a"] in N and E.replace(p["tail_e"], p["tail_a"], 1) == N,
        f"E={len(E.encode())}B / {sha(E)}")
    # P5 E 里「第 16 行」附近不许出现 parts[0]（答案泄漏）
    leak = []
    for m in re.finditer("第 16 行", E):
        seg = E[m.end(): m.end() + 80]
        if "parts[0]" in seg:
            leak.append(seg[:40])
    rec("P5_no_leak_at_line16", not leak, f"leak={leak}")
    # P6 输出形态的改动不许新增答案信息：独立词 600 的出现次数 E 与 N 必须相同
    #    （600 本来就在 A 臂的 `assert ... == 600` 与 why 里，是题面要求，不是本轮泄漏）
    cnt = lambda s: len(re.findall(r"(?<!\d)600(?!\d)", s))  # noqa: E731
    rec("P6_no_new_answer_info", cnt(E) == cnt(N),
        f"E={cnt(E)} N={cnt(N)} 次独立 600（必须相等）")

    # ---- 装置正例 / 反例 ----
    good = "minutes = int(parts[0])"
    n16, e16 = apply_edit(code, 16, good)
    rec("P7_pos_L16_parts0", e16 == "" and verify(n16, tests) == 6,
        f"err={e16!r} verify={verify(n16, tests) if n16 else None}")
    n23, e23 = apply_edit(code, 23, good)
    rec("P8_neg_L23", e23 == "" and verify(n23, tests) == 4,
        f"verify={verify(n23, tests) if n23 else None}（把正确行改坏）")
    nX, eX = apply_edit(code, 16, "minutes = int(parts[0]) * 60")
    rec("P9_neg_double60", eX == "" and verify(nX, tests) == 5,
        f"verify={verify(nX, tests) if nX else None}（与第 17 行重复乘 60）")
    rec("P10_failclosed", apply_edit(code, 99, good)[1] == "oom"
        and apply_edit(code, None, good)[1] == "parse_fail"
        and apply_edit(code, 16, "")[1] == "empty_expr"
        and apply_edit(code, 16, "minutes = = 1")[1].startswith("syntax_fail"),
        "oom / parse_fail / empty_expr / syntax_fail 四路都拒")
    # P11 缩进由装置补：expr 不带缩进也能得到 6/6
    nI, eI = apply_edit(code, 16, good)
    rec("P11_indent_from_device", nI.splitlines()[15].startswith(" " * 16)
        and verify(nI, tests) == 6, repr(nI.splitlines()[15]))
    # P12 解析器：能取回 LINE/EXPR
    li, ex = parse_edit("LINE: 16\nEXPR: minutes = int(parts[0])")
    rec("P12_parse_edit", (li, ex) == (16, good), f"{li!r} {ex!r}")

    ok = all(c["ok"] for c in chk)
    for c in chk:
        print(f"  {c['id']:26s} {'PASS' if c['ok'] else 'FAIL'}  {c['detail']}")
    out = {
        "ok": ok,
        "prompts": {k: {"bytes": len(p[k].encode()), "sha": sha(p[k])} for k in ("A", "N", "E")},
        "checks": chk,
        "base_verify": base,
        "device": {"pos_L16": 6, "neg_L23": 4, "neg_double60": 5},
    }
    (D / "preflight_v248.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nA={out['prompts']['A']}\nN={out['prompts']['N']}\nE={out['prompts']['E']}")
    print("ALL_PASS" if ok else "HAS_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
