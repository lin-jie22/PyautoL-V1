"""把评审账本的 `why` 置换为**等长中性文本** —— V29 的安慰剂臂 `q2` 用。

## 为什么必须是等长

`p2` 与 `q2` 的差别只能在「`why` 里有没有信息」，不能在「prompt 长了多少字」。
V24 已经踩过一次：「投喂 × 盲报」的交互只有 16 格，而单看两臂读数会把它读成主效应。
所以这里做两件事：
  1. 长度对齐 —— 逐条补齐到**原长度**，超出的部分用「·」压掉（只在原长度不足时发生，
     且必须 ≤ 2 字，由 `--strict` 断言）。
  2. 字段保真 —— `task_id` / `score` / `family` / `scorer_sha` / `suite_sha`
     **一个字节都不动**，否则 admissible 的三道闸会被意外触发，`q2` 就不再是安慰剂。

⛔ 本脚本**只改 `why`**。它不动账本的其它任何字段，也不碰记忆库。

用法：
    python3 placebo_why.py <critique.jsonl> [--strict]

输出一行 JSON 摘要：条数、逐条 |Δlen|、最大相对偏差。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

#: 填充文本。刻意**不含**任何任务词（补全/函数/文件/缩进/import…），
#: 也不含数字与符号组合 —— 否则它会在 FTS/LIKE 层面与别的通道产生交互。
_FILL = "本条为等长占位说明不含任务信息"


def _pad_to(base: str, target: int) -> str:
    """把 `base` 重复/截断到恰好 `target` 个字符。"""
    if target <= 0:
        return ""
    s = ""
    while len(s) < target:
        s += _FILL
    return s[:target]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法: placebo_why.py <critique.jsonl> [--strict]", file=sys.stderr)
        return 2
    path = Path(argv[1])
    strict = "--strict" in argv[2:]
    if not path.is_file():
        print(f"❌ 不存在：{path}", file=sys.stderr)
        return 2

    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not rows:
        print("❌ 账本为空 —— 安慰剂臂没有东西可置（这本身就是装置故障，不是读数）",
              file=sys.stderr)
        return 2

    worst_rel = 0.0
    detail = []
    out = []
    for r in rows:
        old = str(r.get("why", ""))
        new = _pad_to(_FILL, len(old))
        rel = abs(len(new) - len(old)) / max(1, len(old))
        worst_rel = max(worst_rel, rel)
        detail.append({"task_id": r.get("task_id"), "old_len": len(old), "new_len": len(new)})
        r2 = dict(r)
        r2["why"] = new
        out.append(r2)

    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out),
                    encoding="utf-8")
    summary = {"rows": len(out), "worst_rel": round(worst_rel, 4), "detail": detail}
    print(json.dumps(summary, ensure_ascii=False))

    #: `--strict` 用在跑批里：长度对不齐就当场失败，绝不带病起跑。
    if strict and worst_rel > 0.02:
        print(f"❌ 等长失败：最大相对偏差 {worst_rel:.4f} > 0.02", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
