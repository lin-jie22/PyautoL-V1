#!/usr/bin/env python3
"""代码执行打分器 —— 实证层 ground truth。

判分 = **真跑**，不是"像不像"。

这是 V1 与 V5 最大的分歧。V5 的适应度函数 54/54 条记录恒等于 0.0，
因为它自始至终没有接上任何**真实世界的结果**：它量的是自己写的分数，
而那个分数从来没有和"这件事到底做成了没有"发生过关系。

本打分器是契约②的第三层（实证 / 终裁）：
    ① 权威 —— 任务集里人工写的 assert           （收敛：与既有共识对齐）
    ② 前沿 —— 任务集里的参考解 oracle           （超越：让"质疑权威"有落点）
    ③ 实证 —— 本文件：候选代码在子进程里真跑     （终裁：语料只测像不像，实证才测行不行）

协议（一行进、一行出）
    stdin  : {"task": {"id","prompt","tests":[...]}, "answer": "<候选代码>"}
    stdout : {"score": 0.0..1.0, "why": "..."}
    退出码 : 0 = 判分完成（score 可为 0，那是**真实**的 0 分）
             2 = 输入不可判（缺 tests 等）—— 这是"没量到"，不是"0 分"
             1 = 打分器自身故障

⛔ 三个刻意的设计决定
    1. **每条 test 一个独立子进程**：候选代码崩溃 / 死循环 / 污染全局都不会
       影响下一条测试，也不会拖死打分器本身。
    2. **用 `python -I`（隔离模式）**：忽略 PYTHONPATH / 用户 site-packages，
       保证同一条任务在任何机器上判出同一个分 —— 尺子必须稳定。
    3. **执行候选代码是刻意的**：打分器的职责就是跑它。
       代价是它必须被当作不可信输入处理（超时 + 独立进程）。
       ⚠️ 生产部署应把这一步放进容器；本项目的任务集是自带的、零依赖的纯函数题。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

#: 从 Markdown 围栏里抽代码；抽不到就整段当代码（模型常常直接给裸代码）
_FENCE = re.compile(r"```(?:python|py)?[ \t]*\r?\n(.*?)```", re.S)
_TIMEOUT_S = 8.0


def extract_code(answer: str) -> str:
    blocks = _FENCE.findall(answer or "")
    if blocks:
        return "\n\n".join(b.strip() for b in blocks if b.strip())
    return (answer or "").strip()


def run_one(code: str, test: str) -> tuple[bool, str]:
    """把「候选代码 + 一条断言」拼起来丢进隔离子进程跑。rc==0 即通过。"""
    src = f"{code}\n\n{test}\n"
    try:
        p = subprocess.run(
            [sys.executable, "-I", "-c", src],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, f"超时 >{_TIMEOUT_S:g}s"
    except OSError as e:  # 解释器都起不来 —— 这是环境故障，不是候选的错
        raise RuntimeError(f"无法启动子进程: {e}") from e
    if p.returncode == 0:
        return True, ""
    err = (p.stderr or "").strip().splitlines()
    return False, (err[-1][:140] if err else f"rc={p.returncode}")


def main() -> int:
    raw = sys.stdin.read()
    try:
        req = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"输入不是合法 JSON: {e}", file=sys.stderr)
        return 1

    task = req.get("task") or {}
    answer = req.get("answer", "")
    tests = task.get("tests") or []
    if not tests:
        # 「没量到」而不是「0 分」：契约③ 缺失不得被读作否定
        print(f"任务 {task.get('id')!r} 没有任何 test，无法判分", file=sys.stderr)
        return 2

    code = extract_code(str(answer))
    if not code:
        print(json.dumps({"score": 0.0, "why": "答案里没有可执行的代码"},
                         ensure_ascii=False))
        return 0

    passed, fails = 0, []
    for t in tests:
        ok, why = run_one(code, str(t))
        if ok:
            passed += 1
        else:
            fails.append(f"{str(t)[:70]} ⇒ {why}")

    score = passed / len(tests)
    why = f"通过 {passed}/{len(tests)}"
    if fails:
        why += "；失败: " + " | ".join(fails[:3])
    print(json.dumps({"score": round(score, 4), "why": why[:280]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
