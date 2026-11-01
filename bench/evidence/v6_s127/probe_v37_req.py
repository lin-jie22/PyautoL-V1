"""V37 证据：两臂的请求身份（零模型、零网络，只读 `/tmp/v37` 冻结产物）。

## 为什么「逐条 `req_sha`」比「分数相同」强

分数相同有三种来源：
  ① 行为真的没变（输入逐字节相同）；
  ② 变了但恰好互相抵消；
  ③ 尺子看不见那个变化。

`req_sha` 是**请求体的 sha**（`bench/llm_proxy.py` 落的台账），它是 prompt 的**身份**。
两臂 34/34 全同 ⇒ 排除 ①②，只可能是「这一轮两臂是同一个系统」。
（V34 纪律 40：「prompt 相同」只能由 prompt 证明，不能用 state 相同代证。）

## 判决与它的关系

预注册 D2 的「通电」判据是 `veto=True > 0`。
**若 `veto=0` 且 `req_sha` 34/34 相同 ⇒ 两臂的分数差必然是 0**，
这时报「Δ=0」不是「机制无效」，是「机制没被触发」——
两者在读数上必须分开，否则下一轮会拿这个 0 去证明"预演没用"。

⛔ 只读，不写任何文件（纪律 34）。
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

W = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v37")
PAIRS = [("a0_r1", "a1_r1"), ("a1_r2", "a0_r2")]


def window(tag: str) -> tuple[float, float] | None:
    p = W / f"time_{tag}.txt"
    if not p.exists():
        return None
    m = re.findall(r"(\d{9,})", p.read_text())
    return (float(m[0]), float(m[1])) if len(m) >= 2 else None


def main() -> int:
    p = W / "proxy.jsonl"
    if not p.exists():
        print("⛔ 没有 proxy.jsonl")
        return 0
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]

    def sub(tag: str) -> list[dict]:
        w = window(tag)
        if not w:
            return []
        lo, hi = w
        return [r for r in rows if lo <= r["t_wall"] <= hi]

    print("=" * 88)
    print("V37 / 两臂的请求身份（`req_sha` 是 prompt 的唯一身份）")
    print("=" * 88)
    for a, b in PAIRS:
        A, B = sub(a), sub(b)
        if not A or not B:
            print(f"\n  {a} vs {b}: 数据缺失（{len(A)} / {len(B)} 条）")
            continue
        n = min(len(A), len(B))
        same = sum(1 for x, y in zip(A[:n], B[:n]) if x["req_sha"] == y["req_sha"])
        print(f"\n  {a} ({len(A)} 条)  vs  {b} ({len(B)} 条)")
        print(f"    逐条 req_sha 相同 = **{same} / {n}**"
              f"   条数差 = {len(B) - len(A):+d}")
        mt = Counter((x["max_tokens"], y["max_tokens"]) for x, y in zip(A[:n], B[:n]))
        print(f"    max_tokens 对照 = {dict(mt)}   （200=预演，1024=规划/收尾）")
        diffs = [(i, x, y) for i, (x, y) in enumerate(zip(A[:n], B[:n]))
                 if x["req_sha"] != y["req_sha"]]
        if not diffs:
            print("    ⇒ **输入逐字节相同**：这一对里两臂是同一个系统，"
                  "分数差必然为 0（不是「机制无效」）")
        else:
            print(f"    ⇒ 有 {len(diffs)} 条不同，逐条点名：")
            for i, x, y in diffs:
                print(f"       #{i:<3d} Δbytes={y['req_bytes'] - x['req_bytes']:+6d} "
                      f"mt={x['max_tokens']}→{y['max_tokens']}")
        #: 同一条请求在两臂里"形态"是否也是同一个（防止条数相同但错位）
        shape_same = sum(1 for x, y in zip(A[:n], B[:n])
                         if x["max_tokens"] == y["max_tokens"])
        print(f"    形态对齐（max_tokens 相同）= {shape_same}/{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
