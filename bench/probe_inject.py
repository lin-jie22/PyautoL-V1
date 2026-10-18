#!/usr/bin/env python3
"""§17-7b 的**注入诊断探针**（零模型调用，只读冻结种子 + 真实拼装路径）。

背景（实测，不是推断）：★7b 三臂逐位相同（Δ=+0.0000），而 R5 已排除"fact 太长
被 `f.text[:120]` 截断"（9 条全 16–31 字，保留 100%）。于是下一个待查的是
**语义记忆块有没有真的进到拼给模型的状态里**。

`core._state()` 的取舍顺序（源码注释写死）：
    目标 > 工作记忆（含回看升级） > 步号 > **语义记忆** > 轨迹
预算 `cfg.STATE_CHAR_CAP = 1600`。语义记忆排在第四位，且 `parts` 是
"能塞就塞、塞不下就数 `state_trimmed`" —— 也就是说：**它可以被静默挤掉**。

本探针直接调用真实的 `Core._state()`（不跑循环、不调模型），
在**真实规模**的工作记忆/轨迹长度上扫一遍，回答：
    「在这些长度下，`[语义记忆]` 还在不在状态里？一条 fact 都没进去吗？」
⛔ 它**不改判据**：★7b 的判读已经按 R3c 落定；这里只做机制归因。

用法：
    python3 bench/probe_inject.py --seed bench/evidence/knowledge_s30/seed_H.db \
        --goal-file bench/heldout/g7_heldout.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V1))

import mw.config as cfg                                     # noqa: E402
from mw.core import Core                                    # noqa: E402
from mw.llm import StubLLM                                  # noqa: E402
from mw.memory import MemoryStore                           # noqa: E402


def _core(seed: Path) -> Core:
    """构造一个**与判定臂同参数**的 Core（离线桩，零调用）。"""
    return Core(llm=StubLLM(), memory=MemoryStore(seed), memory_mode="raw")


def _scan(core: Core, goal: str) -> list[dict]:
    """在真实规模的长度组合上扫 `_state()`，逐格记录语义记忆块的下场。"""
    rows: list[dict] = []
    # 真实规模参考（s30 日志：steps=3~5、回看 1~2 次、上限被裁短 4~13）
    for n_wm in (0, 1, 2, 4):
        for wm_chars in (200, 600, 1200):
            for n_hist in (0, 2, 4, 6):
                core.memory.working.clear()
                for i in range(n_wm):
                    core.memory.think(f"python_exec(...) ⇒ 成功：" + "x" * wm_chars)
                hist = [f"第{i}步 finish(...) ⇒ 成功" for i in range(n_hist)]
                st = core._state(goal, hist, it=1)
                has_head = "[语义记忆]" in st
                # 一条 fact 都没进去 ≠ 块头被剪：两者要分开记
                n_facts = 0
                if has_head:
                    tail = st.split("[语义记忆]", 1)[1]
                    n_facts = sum(1 for f in _facts(core)
                                  if f[:24] in tail)
                rows.append({
                    "n_wm": n_wm, "wm_chars": wm_chars, "n_hist": n_hist,
                    "len": len(st), "has_head": has_head, "n_facts": n_facts,
                })
    return rows


def _facts(core: Core) -> list[str]:
    return [r["text"] for r in
            core.memory.conn.execute("SELECT text FROM facts").fetchall()]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seed", required=True, help="H 臂冻结种子库")
    p.add_argument("--goal-file", required=True, help="heldout jsonl（取第 1 题）")
    args = p.parse_args(argv)

    seed = Path(args.seed).expanduser().resolve()
    goal = json.loads(Path(args.goal_file).read_text().splitlines()[0])["prompt"]
    print(f"== 注入诊断 ==  种子={seed.name}  cap={cfg.STATE_CHAR_CAP}  "
          f"goal={len(goal)} 字")

    with tempfile.TemporaryDirectory() as td:
        # 状态目录指到临时目录：禁止往仓库里写日志/账本（诊断不能污染被测装置）。
        import os
        os.environ["V1_STATE_DIR"] = td
        core = _core(seed)
        facts = _facts(core)
        print(f"  种子 facts = {len(facts)} 条，总长 {sum(map(len, facts))} 字")
        rows = _scan(core, goal)
        core.memory.close()

    print(f"\n  {'工作记忆':>12s} {'轨迹':>4s} {'state':>6s}  语义记忆块")
    for r in rows:
        wm = f"{r['n_wm']}×{r['wm_chars']}字"
        mark = ("✅ " + f"{r['n_facts']}/{len(facts)} 条") if r["has_head"] else "❌ 被挤掉"
        print(f"  {wm:>12s} {r['n_hist']:>4d} {r['len']:>6d}  {mark}")

    kept = sum(1 for r in rows if r["has_head"])
    print(f"\n  R6 注入存活率 = {kept}/{len(rows)} 格（零工作记忆时"
          f" {sum(1 for r in rows if r['n_wm'] == 0 and r['has_head'])}/"
          f"{sum(1 for r in rows if r['n_wm'] == 0)}）")
    print("  ⛔ 只做归因，不改 ★7b 的判读（R3c 已落定）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
