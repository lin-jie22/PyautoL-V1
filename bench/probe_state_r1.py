#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R1 的**第二层**探针：审计真实跑臂时**拼给模型的那段 state**（§17-7h ⑭②）。

为什么不能只查 `recall()`
------------------------
7d–7g 的 R1 一直在查 `recall()` 的返回（"取回了几条、是不是本条"）。
7g 实测证明那只覆盖了一条通道：

    `recall` 工具成功 ⇒ 返回**全部** facts 的拼接（389 字）⇒ `think()` ⇒
    工作记忆（进程级 deque，不随任务边界清空）⇒ `_state()` 取每条输出**尾 200 字**

⇒ 第 4 题的 state 里出现**非本题**惯例 2/3 条，而 R1 全绿。

本探针读 `run.py --state-dump` 落下的 JSONL（每次 `_state()` 一行），逐题：
  · **第 1 步**（本题还没调用过任何工具）判红：`[语义记忆]` 之外的块里
    **不许**出现任何惯例文本 —— 这一步之前不可能有本题自己的工具输出，
    出现就是**跨题残留**；
  · **后续步骤**只登记：`recall` 工具返回全部 facts 是**通道自身行为**
    （不是跨题污染），但它是"1 条族的上下文里没有邻域"这句话的
    **真正反例** —— ⛔ 不许把它读成装置的锅，也不许把它算成送达。

用法：
    python3 bench/probe_state_r1.py --states /tmp/x/states_H_r1.jsonl \
        --heldout bench/heldout/g7e_hint_heldout.jsonl --facts 7f
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
for _p in (str(V1), str(V1 / "bench")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from state_audit import audit_state, format_audit, state_blocks   # noqa: E402
from build_g7d_seed import VARIANTS                        # noqa: E402

#: 语义块的两种合法名字（raw / abstract）—— 与 `state_audit` 同源。
_SEM = ("[语义记忆]", "[经验]")


def _others_only(state: str) -> str:
    """只保留**非语义块**的文本（登记层比对用）。"""
    blk = state_blocks(state)
    return "".join(v for k, v in blk.items() if k not in _SEM)


def load_rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def first_states(rows: list[dict]) -> list[dict]:
    """把"每一次出现"的第一条 state 挑出来（`it` 最小的那一条）。

    同一题若在文件里出现 N 次（N 次 repeat / N 次调用），返回 N 条。
    """
    picked: list[dict] = []
    i = 0
    while i < len(rows):
        tid = rows[i].get("task")
        j = i
        while j < len(rows) and rows[j].get("task") == tid:
            j += 1
        seg = rows[i:j]
        picked.append(min(seg, key=lambda r: int(r.get("it") or 0)))
        i = j
    return picked


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--states", required=True)
    p.add_argument("--heldout", required=True)
    p.add_argument("--facts", default="7d", choices=sorted(VARIANTS))
    args = p.parse_args(argv)

    facts = VARIANTS[args.facts]
    rows = load_rows(Path(args.states).expanduser().resolve())
    if not rows:
        print("❌ state dump 是空的 ⇒ 这一格没有第二层证据（⛔ 不许当「没泄漏」）")
        return 2

    order = [json.loads(l)["id"] for l in
             Path(args.heldout).read_text(encoding="utf-8").splitlines() if l.strip()]

    print(f"== 第 1 步（本题尚无工具输出）⇒ **判红层** ==")
    ok = True
    for row in first_states(rows):
        tid = str(row.get("task") or "")
        a = audit_state(row.get("state") or "", facts, tid)
        ok &= a["ok"]
        print("  " + format_audit(a))

    print("\n== 后续步骤（登记层，**不判红**）==")
    per_task: dict[str, list[dict]] = {}
    for r in rows:
        per_task.setdefault(str(r.get("task") or ""), []).append(r)
    for tid in order:
        seg = per_task.get(tid, [])
        if not seg:
            print(f"  ·  {tid:24s} 没有 state（这一格没跑到？）")
            continue
        # ⚠️ 只数**语义块之外**的块。第一版数的是"整段 state"，
        #    于是 4 条族里 `[语义记忆]` 正常带着的那 3 条被报成"出现过" ——
        #    与 R1 旧判据同一种病（整段比对分不清"哪条路送进来的"）。
        leaked = [k for k in facts
                  if k != tid and any(
                      facts[k][:20] in _others_only(r.get("state") or "")
                      for r in seg)]
        print(f"  ·  {tid:24s} {len(seg)} 步；其它惯例出现在**语义块之外**："
              f"{','.join(leaked) if leaked else '无'}"
              f"（有 ⇒ 来自本题自己 `recall` 的工具输出，是通道行为，不是跨题残留）")

    print("\n== 结论 ==\n  " + ("✅ 第 1 步全部干净：跨题残留 = 0"
                               if ok else
                               "❌ 第 1 步就有惯例出现在 `[语义记忆]` 之外 "
                               "⇒ 跨题残留仍在，本轮读数不得归因"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
