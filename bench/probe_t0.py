#!/usr/bin/env python3
"""§17-7c 辅助探针：直连 LLM 在 T=0 下是否逐字节可复现（零内核、零记忆）。

它只回答一个问题：**同一条 messages，连发 N 次，输出是否完全相同。**
短 prompt 与长 prompt 各测一遍 —— 因为"prompt 一长就不确定"是一个
很容易被顺手编出来、但从没测过的解释。

⛔ 它不产生任何分数，也不进任何判据。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from mw.config import LLMConfig      # noqa: E402
from mw.llm import HTTPLLM, Message  # noqa: E402

SHORT_SYS = ('你是一个规划器。只输出 JSON，形如 '
             '{"candidates":[{"intent":"...","tool":"finish","args":{},"rationale":"..."}]}')
GOAL = ("目标：写一个函数 parse_headers(s)，解析 'k1: v1; k2: v2' 形式的头字符串。"
        "规则：条目用 ';' 分隔；键值用第一个 ':' 分隔；键和未加引号的值都要去掉首尾空白；"
        "值若用双引号包住，则引号内的 ';' 与 ':' 不算分隔符。只输出代码。")

_TOOLS = [("recall", "先在记忆中查找与目标相关的既有结论"),
          ("finish", "直接交付最终答案（args 里放 answer）"),
          ("run_shell", "在沙箱里执行一条 shell 命令并返回输出"),
          ("read_file", "读取工作区内的文件"),
          ("write_file", "写入工作区内的文件"),
          ("note", "把一条结论写入语义记忆"),
          ("expand", "按句柄取回某段被压缩的原文"),
          ("delegate", "把子任务派给外部 agent")]

LONG_SYS = ("你是一个任务执行内核的规划器。你只输出 JSON，不输出任何解释。\n"
            '输出格式：{"candidates":[{"intent":"...","tool":"<工具名>","args":{...},'
            '"rationale":"..."}]}\n'
            "候选数量 1-3 个，按把握从高到低排序。\n可用工具：\n"
            + "\n".join(f"- {n}: {d}" for n, d in _TOOLS * 6))


def run(llm: HTTPLLM, system: str, user: str, n: int) -> int:
    hs = []
    print(f"── system={len(system)} 字 user={len(user)} 字 × {n} 次 ──")
    for i in range(n):
        c = str(llm.complete([Message("system", system), Message("user", user)],
                             max_tokens=1024, temperature=0.0))
        h = hashlib.sha256(c.encode("utf-8")).hexdigest()[:12]
        hs.append(h)
        print(f"  #{i + 1} {len(c)} 字 sha={h}")
    uniq = len(set(hs))
    print(f"  ⇒ 唯一输出数 = {uniq} / {n}  {'✔ 逐字节可复现' if uniq == 1 else '❌ 不可复现'}")
    return uniq


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    args = ap.parse_args(argv)
    os.environ.setdefault("V1_LLM_TEMPERATURE", "0")
    cfg = LLMConfig()
    print(f"model={cfg.model} temperature={cfg.temperature} base={cfg.base_url}")
    if cfg.temperature != 0.0:
        print("❌ V1_LLM_TEMPERATURE 不是 0 ⇒ 这轮不是在测贪心，作废")
        return 1
    llm = HTTPLLM(cfg)
    a = run(llm, SHORT_SYS, GOAL, args.n)
    b = run(llm, LONG_SYS, GOAL + "\n[第 1/12 步]\n[工作记忆] （空）\n[语义记忆] （空）\n[轨迹] （空）",
            args.n)
    return 0 if (a == 1 and b == 1) else 2


if __name__ == "__main__":
    sys.exit(main())
