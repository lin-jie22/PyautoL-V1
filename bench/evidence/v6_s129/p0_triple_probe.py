#!/usr/bin/env python3
"""V39 零成本归因：把 `"answer": \"\"\"<代码>\"\"\"` 修成合法 JSON 之后，候选数是多少？

## 为什么必须做这一步

`PREREG.md` §二 写着「模型从不给 ≥2 个候选」。那句话是拿 `candidates` 数组的
**解析结果**做的判断 —— 而 JSON 全都解析失败，所以"解析结果"恒为空。
真正该看的是**原文**：`n3_e1wi_c0.txt` 里 `"tool"` 出现了 **3 次**。

⇒ 于是有一个可能性必须先排除：**模型给了 3 个，只是写坏了。**
本探针就是排除它的：只做一件事 —— 把成对的三引号整段 `json.dumps` 转义，
再交给 `json.loads`。**不动别的任何字节**，也不改内核。

## 判读

- `candidates > 1` ⇒ 宽度**能兑现**，卡在格式 ⇒ 下一轮该修解析器，不是修 `n`。
- `candidates == 1` ⇒ 模型确实只给一个 ⇒ 那句话成立。

⛔ 只读 `/tmp/v39_raw/`，不写任何证据路径（纪律 34）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TQ = '"' * 3
RAW = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v39_raw")


def fix_triple(text: str) -> str:
    """把每一对 `\"\"\"…\"\"\"` 换成合法的 JSON 字符串字面量。

    ⚠️ 只认**成对**的三引号；没配对（截断）时把剩余全部当载荷。
    ⛔ 不做"猜测式"修复：修不了就原样返回，让上层继续判失败 ——
       一个"总能修好"的修复器会把所有失败都读成成功（红=无的第三种形态）。
    """
    out: list[str] = []
    i = 0
    while True:
        j = text.find(TQ, i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        k = text.find(TQ, j + 3)
        if k < 0:
            out.append(json.dumps(text[j + 3:], ensure_ascii=False))
            break
        out.append(json.dumps(text[j + 3:k], ensure_ascii=False))
        i = k + 3
    return "".join(out)


def main() -> int:
    files = sorted(RAW.glob("*.txt"))
    if not files:
        print(f"⛔ 没有原始样本：{RAW}")
        return 2
    print("=" * 92)
    print("V39 零成本归因：原文 → 修三引号 → 候选数")
    print("=" * 92)
    print(f"{'样本':22} {'原 tool 数':>10} {'修后 candidates':>16} {'tools':>28} {'answer 长度'}")
    print("-" * 92)
    for p in files:
        raw = p.read_text(encoding="utf-8", errors="replace")
        n_tool_raw = raw.count('"tool"')
        try:
            d = json.loads(fix_triple(raw), strict=False)
            items = d.get("candidates") or []
            tools = [str(it.get("tool")) for it in items]
            lens = [len(str((it.get("args") or {}).get("answer") or "")) for it in items]
            print(f"{p.name:22} {n_tool_raw:>10} {len(items):>16} {str(tools)[:28]:>28} {lens}")
        except Exception as e:                                  # noqa: BLE001
            print(f"{p.name:22} {n_tool_raw:>10} {'解析失败':>16} "
                  f"{type(e).__name__}: {str(e)[:32]}")
    print("-" * 92)
    print("读法：`修后 candidates > 1` ⇒ 模型**给了**多个，卡在格式；")
    print("      恒为 1 或解析失败 ⇒ 那句话要先分清「没给」与「没收到」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
