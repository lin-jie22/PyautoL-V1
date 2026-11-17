"""V183 基底：8 题（V79 那批）× 三种实现（oracle / partial / 空）。

⛔ 为什么错答案用 `partial` 而不是 V79 的模型错答：
   `/tmp/v78/*/mem/memory.db` 已随 tmp 清空（本脚本 STAGE0 实测：目录在、库文件 0 字节），
   V79 的错答案**原物已失**，只剩 probe_selfasserts.json 里每条断言的一条实得串。
   `partial` 是题集自带的「半对实现」——**不是本轮写的**，且比模型错答更接近正确
   （判据必须更利），拿它当错答案只会让读数更难而不是更容易。

⛔ 题集 `tests` 字段一律不进本文件：它是隐藏判分例，拿它当关系探针 = 泄漏。
   关系探针的输入全部另写（见 relations.py 的 `INPUTS`）。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

SUITES = ("bench/heldout/struct_convert_heldout.jsonl",
          "bench/heldout/g7j_n4_heldout.jsonl")

#: 「题面自足」的判据不是人眼看的，是**题面自己写的**：spec-incomplete 的题
#: 明说「惯例不在题面里」⇒ 判据生成者若只拿 prompt，信息量下界是 0。
MEMORY_DEPENDENT = ("g7d2_dedupe_keep", "g7d3_fmt_amount",
                    "g7e1_parse_flag", "g7e2_truncate_tail")


def tasks() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for p in SUITES:
        for line in (ROOT / p).read_text(encoding="utf-8").splitlines():
            if line.strip():
                t = json.loads(line)
                t["suite"] = Path(p).stem
                out[t["id"]] = t
    return out


def fn_name(task: dict) -> str:
    """从 oracle 里取函数名（题面里写了「函数名必须是 X」，oracle 必然同名）。"""
    import re
    m = re.search(r"def\s+([A-Za-z_]\w*)\s*\(", task["oracle"])
    return m.group(1) if m else ""


def stub(task: dict) -> str:
    """空实现：`def f(*a, **k): return None` —— 只用来抓「恒真判据」。"""
    return f"def {fn_name(task)}(*a, **k):\n    return None\n"


def v79_asserts() -> dict[str, list[str]]:
    """V79 那 22 条断言原物（`bench/evidence/v6_s168/probe_selfasserts.json`）。"""
    p = ROOT / "bench/evidence/v6_s168/probe_selfasserts.json"
    rows = json.loads(p.read_text(encoding="utf-8"))
    return {r["id"]: list(r["asserts"]) for r in rows}


def judge(code: str, asserts: list[str]) -> str:
    """与内核同一把尺（`mw.verify.run_example`）。通过 ⇒ `""`。"""
    import sys
    sys.path.insert(0, str(ROOT))
    from mw.verify import run_example
    return run_example(code, "\n".join(asserts))
