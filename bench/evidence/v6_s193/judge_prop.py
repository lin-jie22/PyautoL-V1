#!/usr/bin/env python3
"""V104 性质判据（property judge）：判断 `fmt_amount` 的**行为性质**，不跑题目自带的 tests。

为什么它能当「第二通道」
-----------------------
- 它的输入只有**答案代码**，看不到题面 ⇒ **结论对题面字节天然不变**（V101~V103 里
  所有分数都会随题面一个码位跳，这个不会）。
- 它给的是**理由**（哪条性质破了），不是只有一个数。V100~V103 的困境正是「只有一个数」。

⛔ 它**不替代** tests 判据，也不声称更对。它是一个**独立**的观测通道，
   两者的分歧本身就是要读的东西。

协议（与 `mw/bench.py` 的 `ExternalScorer` 一致）
    stdin  : {"task": {"id":...}, "answer": "<候选代码>"}
    stdout : {"score": 0.0..1.0, "why": "...", "detail": [{"prop":..., "ok":...}, ...]}
    退出码 : 0 = 判分完成；2 = 输入不可判；1 = 判据自身故障

⚠️ 执行候选代码是刻意的（同 `bench/scorers/code_exec.py`）：
   `python3 -I` 隔离子进程 + 超时 + `RLIMIT_CPU`。macOS 上 `RLIMIT_AS` 不可用，如实登记。
"""
from __future__ import annotations

import json
import re
import resource
import subprocess
import sys
import tempfile
from pathlib import Path

TIMEOUT_S = 12.0
FUNC = "fmt_amount"
#: 从 Markdown 围栏里抽代码（与 bench/scorers/code_exec.py 同口径：抽不到就整段当代码）
_FENCE = re.compile(r"```(?:python|py)?[ \t]*\r?\n(.*?)```", re.S)


def _code_of(answer: str) -> str:
    m = _FENCE.search(answer or "")
    return (m.group(1) if m else (answer or "")).strip("\n")

#: **输入网格**（刻意**不用**题目 tests 里的任何输入）
GRID = [0, 1, 12, 123, 1234, 12345.678, 1234567.891, 1000000,
        -1, -12, -1234.5, -1234567.891, 999.995, 0.5]

HARNESS = r'''
import json, sys
grid = __GRID__
src = open(__SRC__, encoding="utf-8").read()
func = __FUNC__
out = {"ok": False, "err": "", "rows": []}
ns = {}
try:
    exec(compile(src, "<answer>", "exec"), ns)
except BaseException as e:
    out["err"] = "exec: %s: %s" % (type(e).__name__, e)
    print(json.dumps(out, ensure_ascii=False)); sys.exit(0)
fn = ns.get(func)
if not callable(fn):
    out["err"] = "no callable %s" % func
    print(json.dumps(out, ensure_ascii=False)); sys.exit(0)
for x in grid:
    try:
        out["rows"].append([x, fn(x)])
    except BaseException as e:
        out["rows"].append([x, {"__err__": "%s: %s" % (type(e).__name__, e)}])
out["ok"] = True
print(json.dumps(out, ensure_ascii=False))
'''

#: 每条性质：(名字, 说明, 判定函数)。判定函数接 (x, out) → True/False/None（None = 不适用）
def _digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def _int_part(s: str) -> str:
    """整数部分的字符：去掉符号与括号（**不碰空白与分隔符**，因为它们本身是判据输入）。

    ⚠️ 这里踩过一次：第一版把空格也当「噪声」删了，于是 `1 234` 变成 `1234`，
    所有实现都「只有 0 种分隔符」→ `single_sep` 全红。判据的清洗口径本身就是判据的一部分。
    """
    t = s
    for ch in "()（）":
        t = t.replace(ch, "")
    t = t.lstrip("−-")
    for sep in (".", "．"):
        if sep in t:
            return t.split(sep)[0]
    return t


def p_ok(x, o):
    return isinstance(o, str)


def p_two_decimals(x, o):
    """小数位必须恒为 2。"""
    if not isinstance(o, str):
        return False
    for sep in (".", "．"):
        if sep in o:
            frac = o.split(sep)[-1].rstrip(") ）")
            return len(frac) == 2 and frac.isdigit()
    return False


def p_sep_is_space(x, o):
    """千分位分隔符必须是**空格**（这是本项目金额惯例的第 1 条）。"""
    if not isinstance(o, str):
        return False
    ip = _int_part(o)
    seps = {c for c in ip if not c.isdigit()}
    if not seps:            # 不足千位时没有分隔符，不适用
        return None
    return seps == {" "}


def p_single_sep(x, o):
    """整数部分里**只允许一种**非数字字符充当千分位（不许混用）。"""
    if not isinstance(o, str):
        return False
    ip = _int_part(o)
    seps = {c for c in ip if not c.isdigit()}
    if not seps:
        return None
    return len(seps) == 1


def p_group_of_three(x, o):
    """按千分位切开后，除最高一组外每组必须恰好 3 位。"""
    if not isinstance(o, str):
        return False
    ip = _int_part(o)
    seps = {c for c in ip if not c.isdigit()}
    if len(seps) != 1:
        return None
    sep = seps.pop()
    groups = ip.split(sep)
    if len(groups) == 1:
        return len(groups[0]) <= 3
    return all(len(g) == 3 for g in groups[1:]) and 1 <= len(groups[0]) <= 3


def p_no_minus_for_negative(x, o):
    """负数**不许**用 '-'，必须被一对括号包裹。"""
    if not isinstance(o, str) or x >= 0:
        return None
    if "-" in o or "−" in o:
        return False
    return ("(" in o and ")" in o) or ("（" in o and "）" in o)


def p_no_space_after_paren(x, o):
    """负数的左括号必须**紧贴第一位数字**（`( 1 234.50)` 这种多一个空格算破）。"""
    if not isinstance(o, str) or x >= 0:
        return None
    for op, cl in (("(", ")"), ("（", "）")):
        if op in o:
            i = o.index(op)
            return i + 1 < len(o) and o[i + 1].isdigit()
    return None


def p_positive_no_wrap(x, o):
    """非负数**不许**被括号包裹。"""
    if not isinstance(o, str) or x < 0:
        return None
    return not any(c in o for c in "()（）")


def p_numeric_value(x, o):
    """把非数字全部去掉后，数字串必须等于 round(|x|, 2) 的定点表示。"""
    if not isinstance(o, str):
        return False
    d = _digits(o)
    want = f"{round(abs(x) + 1e-9, 2):.2f}".replace(".", "")
    return d == want


PROPS = [
    ("is_str", "返回字符串", p_ok),
    ("two_decimals", "小数位恒为 2", p_two_decimals),
    ("sep_is_space", "千分位用空格", p_sep_is_space),
    ("single_sep", "千分位只用一种字符", p_single_sep),
    ("group_of_three", "千分位按 3 位分组", p_group_of_three),
    ("neg_no_minus", "负数不用负号、用括号", p_no_minus_for_negative),
    ("no_space_after_paren", "负号括号紧贴数字", p_no_space_after_paren),
    ("pos_no_wrap", "非负数不加括号", p_positive_no_wrap),
    ("numeric_value", "去掉分隔符后数值正确", p_numeric_value),
]


def judge(answer: str, coarse: bool = False) -> dict:
    answer = _code_of(answer)
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "answer.py"
        src.write_text(answer, encoding="utf-8")
        harness = Path(td) / "h.py"
        harness.write_text(
            HARNESS.replace("__GRID__", repr(GRID))
                   .replace("__SRC__", repr(str(src)))
                   .replace("__FUNC__", repr(FUNC)),
            encoding="utf-8")

        def _limit():
            resource.setrlimit(resource.RLIMIT_CPU, (8, 8))

        try:
            proc = subprocess.run([sys.executable, "-I", str(harness)],
                                  capture_output=True, text=True,
                                  timeout=TIMEOUT_S, preexec_fn=_limit)
        except subprocess.TimeoutExpired:
            return {"score": 0.0, "why": "超时", "detail": [{"prop": "timeout", "ok": False}]}
        if proc.returncode != 0:
            return {"score": 0.0, "why": f"子进程退出码 {proc.returncode}",
                    "detail": [{"prop": "crash", "ok": False}]}
        lines = [l for l in (proc.stdout or "").strip().splitlines() if l.strip()]
        if not lines:
            return {"score": 0.0, "why": "子进程无输出", "detail": [{"prop": "no_output", "ok": False}]}
        data = json.loads(lines[-1])
    if not data.get("ok"):
        return {"score": 0.0, "why": data.get("err", "执行失败"),
                "detail": [{"prop": "exec", "ok": False}]}

    rows = data["rows"]
    detail, passed, total = [], 0, 0
    for name, desc, fn in PROPS:
        hits = []
        for x, o in rows:
            if isinstance(o, dict) and "__err__" in o:
                hits.append(False)
                continue
            r = fn(x, o)
            if r is not None:
                hits.append(bool(r))
        if not hits:
            continue
        npass = sum(1 for h in hits if h)
        # ⭐ **微平均**（默认）：(性质 × 输入) 每一对算一格。
        #    粗平均（每条性质 1 票）会把「14 个输入里 1 个破」和「14 个全破」判成一样，
        #    而 tests 判据恰恰是逐用例算的 —— 两者口径必须可比。
        # ⚠️ `--coarse` 走粗平均，**只用来做敏感性检查**：两种口径都报，防止「调口径调出结论」。
        if coarse:
            passed += 1 if npass == len(hits) else 0
            total += 1
        else:
            passed += npass
            total += len(hits)
        detail.append({"prop": name, "desc": desc, "ok": npass == len(hits),
                       "n": len(hits), "n_fail": len(hits) - npass})
    score = (passed / total) if total else 0.0
    unit = "条性质" if coarse else "个（性质×输入）"
    return {"score": round(score, 4), "mode": "coarse" if coarse else "micro",
            "why": f"{passed}/{total} {unit}成立",
            "detail": detail}


def main() -> None:
    raw = sys.stdin.read()
    try:
        req = json.loads(raw)
        answer = req.get("answer") or ""
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"输入不是合法 JSON: {e}"}, ensure_ascii=False))
        sys.exit(2)
    if not answer.strip():
        print(json.dumps({"error": "answer 为空"}, ensure_ascii=False))
        sys.exit(2)
    print(json.dumps(judge(answer, coarse="--coarse" in sys.argv[1:]), ensure_ascii=False))


if __name__ == "__main__":
    main()
