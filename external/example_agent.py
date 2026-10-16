#!/usr/bin/env python3
"""一个最小外部 agent 的范例。

它是配合 agents.example.json 用的：内核把一个任务文本从 stdin 交给它，
它把结果写 stdout。**这就是 V1 的扩展方式** —— 内核不认识具体能力，
只认识"stdin 进、stdout 出"这个契约。

真实项目里，这里可以是任何东西：另一个 LLM 调用、一个 WorkBuddy skill、
一个 MCP server 的包装、一段脚本。内核不需要知道，也不该知道。
"""

import json
import sys


def main() -> int:
    task = sys.stdin.read().strip()
    if not task:
        print(json.dumps({"error": "空任务"}, ensure_ascii=False))
        return 1
    # 这里换成真实能力。示例只回显，便于确认接线通了。
    print(json.dumps({
        "agent": "example",
        "received": task[:200],
        "note": "这是范例 agent。把本文件的实现换成真实能力即可。",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
