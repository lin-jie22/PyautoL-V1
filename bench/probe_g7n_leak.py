#!/usr/bin/env python3
"""§17-7g 的**第二条注入路径**探针（零模型调用）。

为什么要写它（读完再动手改）：
    7g 的事前登记把"邻域在不在上下文里"建立在 **R1** 上 —— 而 R1 只查
    `recall()` 返回几条，也就是**语义块**那一条路。本轮实测发现**还有一条**：

        `recall` 工具成功时，返回的是**全部** facts 的拼接（实测 389 字）；
        `core.py` 的 `self.memory.think(stored)` 把工具输出写进**工作记忆**；
        而 `MemoryStore.working` 是**进程级 deque**，`Core.run()` **不随任务边界清空**；
        `_state()` 又**无条件**读 `working_view()`（只要不是 abstract 模式）。

    ⇒ 同一份事实既在 `[语义记忆]` 块里，又在**后面几题**的 `[工作记忆]` 块里。
       "1 条族（`g7e1`/`g7e2`）的上下文里没有邻域"这句话**是错的** ——
       它只对语义块成立，对工作记忆不成立。

本探针只回答一件事：**把真实跑臂库里的 recall 输出按 `core.py` 的方式放进工作记忆，
后面那道题的 state 里到底有没有邻域？** 不调模型、不改判据、不进任何判定。

用法：
    python3 bench/probe_g7n_leak.py \
        --db bench/evidence/knowledge_s34/final_H_r1.db \
        --heldout bench/heldout/g7e_hint_heldout.jsonl --facts 7f
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
for _p in (str(V1), str(V1 / "bench")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if not os.environ.get("V1_STATE_DIR"):
    os.environ["V1_STATE_DIR"] = tempfile.mkdtemp(prefix="v1_g7n_leak_")

from mw.core import WORKING_CHAR_CAP, Core                  # noqa: E402
from mw.llm import StubLLM                                  # noqa: E402
from mw.memory import MemoryStore                           # noqa: E402
from build_g7d_seed import VARIANTS                         # noqa: E402


def _blocks(state: str) -> dict[str, str]:
    """把 state 按块切开（`[工作记忆]` / `[语义记忆]` / 其余）。"""
    out: dict[str, str] = {}
    cur = "头"
    for line in state.splitlines():
        for head in ("[工作记忆]", "[语义记忆]", "[经验]", "[外部评审]", "[轨迹]"):
            if line.startswith(head):
                cur = head
                break
        else:
            pass
        out[cur] = out.get(cur, "") + line + "\n"
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", required=True, help="跑完的库（含 blobs 表）")
    p.add_argument("--heldout", required=True)
    p.add_argument("--facts", default="7f", choices=sorted(VARIANTS))
    p.add_argument("--task", default=None, help="要 dump 的题 id（默认最后一题）")
    args = p.parse_args(argv)
    facts = VARIANTS[args.facts]

    tasks = [json.loads(l) for l in
             Path(args.heldout).read_text().splitlines() if l.strip()]
    tid = args.task or tasks[-1]["id"]
    prompt = next(t["prompt"] for t in tasks if t["id"] == tid)

    tmp = Path(tempfile.mkdtemp(prefix="v1_g7n_leakdb_")) / "memory.db"
    shutil.copy2(Path(args.db).expanduser(), tmp)
    mem = MemoryStore(tmp)
    core = Core(llm=StubLLM(), memory=mem, memory_mode="raw")

    rows = mem.conn.execute(
        "SELECT id, text FROM blobs ORDER BY id").fetchall()
    recalls = [r["text"] for r in rows if r["text"].startswith("recall ⇒ ✔")]
    print(f"== 库里成功的 recall 输出：{len(recalls)} 条 ==")
    for i, t in enumerate(recalls, 1):
        hit = [k for k in facts if facts[k][:1] and facts[k][:20] in t]
        print(f"  #{i} len={len(t):4d}  含惯例 {len(hit)}/4 条：{[k.split('_')[0] for k in hit]}")

    # 按 core.py 的写法放进工作记忆（`stored = (索引 + 原文前 300 字)[:380]`）。
    # ⛔ 不写索引（`[b#id …]`）—— 那与本探针要看的"字在不在"无关，加了反而更难比对。
    for t in recalls:
        mem.think(t[: WORKING_CHAR_CAP + 80])
    print(f"== 工作记忆里放进了 {len(recalls)} 条（deque 不随任务边界清空）==")

    st = core._state(prompt, [], 1, task_id=tid,
                     scorer_sha="probe", suite_sha="probe")
    blk = _blocks(st)
    print(f"\n== 第 4 题（`{tid}`）的 state = {len(st)} 字 ==")
    for name in ("[工作记忆]", "[语义记忆]"):
        print(f"  {name:8s} {'有' if name in blk else '无'}"
              f"  {len(blk.get(name, '')):4d} 字")

    print("\n== 每条惯例在 state 的哪个块里（前 20 字比对）==")
    leaked = 0
    for k, v in facts.items():
        probe = v[:20]
        where = [n for n in ("[工作记忆]", "[语义记忆]") if probe in blk.get(n, "")]
        own = "←本题那条" if k == tid else ""
        if where:
            print(f"  ✅ {k:24s} 出现在 {' / '.join(where)} {own}")
        else:
            print(f"  ·  {k:24s} 不在 state 里 {own}")
        if k != tid and "[工作记忆]" in where:
            leaked += 1

    print(f"\n== 结论 ==\n  非本题的惯例有 {leaked}/3 条经 `[工作记忆]` 进了 state"
          f" ⇒ " + ("**第二条注入路径存在**（R1 查不到它）"
                     if leaked else "本库里这条路径没被走到（recall 没成功过）"))
    mem.close()
    return 0 if leaked else 1


if __name__ == "__main__":
    raise SystemExit(main())
