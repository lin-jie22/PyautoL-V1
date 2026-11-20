#!/usr/bin/env python3
"""V214 的**测量用**外部能力壳子：把内核交过来的 stdin 原样存证，再转交给真的 `code_agent`。

⛔ 它不是产品代码，也不进 `agents.json` 的产品版本 —— 只在测量时**临时**替换
   `agents.json` 里 `code_agent.command` 那一行，跑完按 sha 恢复（`restore.sh`）。

为什么需要它（V214 要回答的那一个问题）
    默认配置下 `code_agent` 吃掉 84.4% 的题次（§277 免费普查）。问题是：
    **内核写进 state 的评审块 / 记忆块，到底有没有进到那次真正的求解调用里？**
    `mw/tools.py:341` 的 `input=str(args.get("task",""))` 只交 planner 填的
    那一个参数 —— 所以答案取决于 planner 有没有把块抄进去。这件事只能**看**，
    不能从代码推断。

记录什么
    每次调用追加一行 JSON 到 `$V1_PROBE_LOG`：
      {"i": 序号, "goal_chars": 题面长度, "task_chars": 收到的长度,
       "has_review": 是否含 `[外部评审]`, "has_mem": 是否含 `[语义记忆]`/`[工作记忆]`,
       "has_correction": 是否含评审里的失败特征串, "task": 全文}
    `task` 全文一并存下：**判据要能复核**，只存布尔值等于让人信我。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
REAL = os.path.join(ROOT, "external", "code_agent.py")
LOG = os.environ.get("V1_PROBE_LOG", os.path.join(HERE, "probe_stdin.jsonl"))

#: 评审块首行标记（`mw/critique.py` 的 `as_line()` 之前，`mw/core.py` 拼进 state）
#: ⚠️ 自踩（V214）：第一版把 `"[外部评审"`（无右括号的前缀）也塞进同一个元组，
#:    而 `has_mem` 又是用 `_MARKERS[1:]` 判的 ⇒ 只要有评审块，`has_mem` 就跟着 True。
#:    读数上一共只错一处（blocks 臂的 `has_mem`），但那是**系统性**的：
#:    前缀与全名混在一个列表里，切片就没有语义了。⇒ 拆成两个常量。
_REVIEW = ("[外部评审]",)
_MEM = ("[语义记忆]", "[工作记忆]", "[经验]")
#: 评审原文里一定会出现的特征串（`prior_v214.jsonl` 的 t01/t04 首行）
_CORR = ("to unpack", "does not support item assignment", "parse_duration", "merge_ranges")


def main() -> int:
    raw = sys.stdin.read()
    idx = 0
    try:
        if os.path.exists(LOG):
            with open(LOG, encoding="utf-8") as f:
                idx = sum(1 for _ in f)
    except OSError:
        idx = -1
    rec = {
        "i": idx,
        "ts": time.strftime("%H:%M:%S"),
        "task_chars": len(raw),
        "has_review": any(m in raw for m in _REVIEW),
        "has_mem": any(m in raw for m in _MEM),
        "has_corr": [c for c in _CORR if c in raw],
        "task": raw,
    }
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # 原样转交（stdin → 真 code_agent 的 stdin），stdout/stderr 直通。
    p = subprocess.run([sys.executable, REAL], input=raw, text=True,
                       capture_output=True)
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    return 0 if p.stdout.strip() else 1


if __name__ == "__main__":
    sys.exit(main())
