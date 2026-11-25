"""V247 preflight：只验装置，不调模型、不判分、不花钱。

⛔ 目的只有一个：把 PREREG_v247.md §9 的 P-a~P-f 变成可复跑的读数。
⛔ 若任何一项红，跑 v247 的读数不可解释 —— 先修装置。

用法：python3 preflight_v247.py
落盘：preflight_v247.json
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.reflect import (build_proposal_prompt, failed_actuals,  # noqa: E402
                        parse_proposal, trace_failure, verify)

D = pathlib.Path(__file__).resolve().parent
START = ROOT / "bench/evidence/l9_s310/cand/90579ecebe633352.py"
SUITE = ROOT / "bench/evidence/l5_s266/suite_one/t01.jsonl"

Q_TPL = (
    "下面这个函数没有通过全部测试。请**只回答两个问题**，不要写代码、不要改代码。\n"
    "【题目】\n{goal}\n"
    "【当前代码】\n{code}\n"
    "【问题】输入是 '10m'。按这份代码，s 会先按 'h' 切、再按 'm' 切。\n"
    "  1) 第二次切完之后，parts 的值是什么？（把列表原样写出来）\n"
    "  2) parts[0] 与 parts[1] 当中，哪一个是「分钟数」？\n"
    "格式：两行。第一行 `1) parts=…`，第二行 `2) 分钟数是 parts[…]。`"
)

ANCHOR = "请顺着轨迹找到**第一处**"
INS = ("【你上一轮的自述】你刚才被问到「第 16 行里哪一个 parts 元素是分钟数」，"
       "你的回答原文如下：\n<<<SELF_START>>>\n{answer}\n<<<SELF_END>>>\n"
       "请让上面的代码与你这段自述**一致**。\n\n")

SEM = re.compile(r"int\(\s*parts\[0\]\s*\)\s*\*\s*60")
BUG = re.compile(r"int\(\s*parts\[1\]")

POS = "def parse_duration(s):\n    return int(parts[0]) * 60\n"


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def main() -> int:
    task = json.loads([x for x in SUITE.read_text(encoding="utf-8").splitlines()
                       if x.strip()][0])
    goal, tests = task["prompt"], task["tests"]
    code = START.read_text(encoding="utf-8")

    base = verify(code, tests)
    actuals = failed_actuals(code, tests)
    trace = trace_failure(code, tests)
    why = (f"通过 {base}/{len(tests)}；失败: assert parse_duration('10m') == 600"
           f" ⇒ AssertionError")
    a_prompt = build_proposal_prompt(goal, code, why, actuals, trace=trace)

    q_prompt = Q_TPL.format(goal=goal, code=code)
    s_prompt = a_prompt.replace(
        ANCHOR, INS.replace("{answer}", "2) 分钟数是 parts[0]。") + ANCHOR, 1)
    s_back = s_prompt.replace(
        INS.replace("{answer}", "2) 分钟数是 parts[0]。"), "")

    checks: list[dict] = []

    def ck(tag: str, ok: bool, detail: str) -> None:
        checks.append({"tag": tag, "ok": bool(ok), "detail": detail})
        print(f"  {tag:5s} {'OK ' if ok else 'RED'} {detail}", flush=True)

    # P-a：A 臂与 v245 B 臂逐字节同
    ck("P-a", len(a_prompt.encode()) == 2307 and _sha(a_prompt) == "1bdea4fc92d67817",
       f"{len(a_prompt.encode())} bytes sha={_sha(a_prompt)} (want 2307 / 1bdea4fc92d67817)")

    # P-b：起点 5/6，失败项唯一是 10m
    ck("P-b", base == 5 and len(actuals) == 1 and "'10m'" in actuals[0],
       f"verify={base}/6 actuals={actuals}")

    # P-c：Q 不含独立词 600（裸子串会撞上 3600）
    naked = "600" in q_prompt
    strict = re.search(r"(?<!\d)600(?!\d)", q_prompt) is not None
    ck("P-c", not strict,
       f"strict600={strict} naked600={naked}(假阳性来自 3600) q_bytes={len(q_prompt.encode())}")

    # P-d：S 去掉插入段后与 A 逐字节同
    ck("P-d", s_back == a_prompt,
       f"s_bytes={len(s_prompt.encode())} back_equal={s_back == a_prompt}")

    # P-e：自述段在前时仍能取到代码块
    cand, fenced = parse_proposal(
        "【自述】分钟数是 parts[0]\n\n```python\ndef parse_duration(s):\n    return 1\n```\n")
    ck("P-e", fenced and cand.startswith("def parse_duration"),
       f"fenced={fenced} head={cand.splitlines()[0] if cand else '<empty>'}")

    # P-f：检测器先在原码上自检，再用正例验「能判对」
    ck("P-f1", (not SEM.search(code)) and bool(BUG.search(code)),
       f"原码 sem={bool(SEM.search(code))} bug={bool(BUG.search(code))} (want False/True)")
    ck("P-f2", bool(SEM.search(POS)) and not BUG.search(POS),
       f"正例 sem={bool(SEM.search(POS))} bug={bool(BUG.search(POS))} (want True/False)")

    # 额外：Q 与 S 的 sha 钉住，后续读数要引用
    print(f"\n  A sha={_sha(a_prompt)}  Q sha={_sha(q_prompt)}  S sha={_sha(s_prompt)}")
    print(f"  起点 sha={_sha(code)}")

    bad = [c["tag"] for c in checks if not c["ok"]]
    out = {"base": base, "actuals": actuals,
           "a_bytes": len(a_prompt.encode()), "a_sha": _sha(a_prompt),
           "q_bytes": len(q_prompt.encode()), "q_sha": _sha(q_prompt),
           "s_bytes": len(s_prompt.encode()), "s_sha": _sha(s_prompt),
           "start_sha": _sha(code), "checks": checks, "red": bad}
    (D / "preflight_v247.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n汇总：{len(checks) - len(bad)}/{len(checks)} 绿；红={bad or '无'}")
    print(f"落盘 → {D / 'preflight_v247.json'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
