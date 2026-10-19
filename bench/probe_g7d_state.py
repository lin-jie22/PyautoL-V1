#!/usr/bin/env python3
"""§17-7d 的**状态级送达探针**（零模型调用）。

R1 只证明 `recall()` 能取回惯例；**取回 ≠ 进了拼给模型的状态**。
`core._state()` 的取舍顺序是 目标 > 工作记忆 > 步号 > 语义记忆 > 轨迹，
预算 `STATE_CHAR_CAP`，语义记忆排在第四位、塞不下会被静默挤掉（s30 已实测过一次
"48/48 格都在"）。所以本轮必须再证一遍：**每道题的惯例原文确实出现在 state 里**。

本探针直接调用真实 `Core._state()`（不跑循环、不调模型），逐题输出：
    state 长度 / `[语义记忆]` 块在不在 / 本题那条惯例是否逐字出现在块里。

⛔ 它不改判据、不进任何判定；它只回答"送到没送到"。

用法：
    python3 bench/probe_g7d_state.py --seed bench/evidence/knowledge_s32/seed_H.db \
        --heldout bench/heldout/g7e_heldout.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
for _p in (str(V1), str(V1 / "bench")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import os                                                    # noqa: E402

if not os.environ.get("V1_STATE_DIR"):
    os.environ["V1_STATE_DIR"] = tempfile.mkdtemp(prefix="v1_g7d_state_")

from mw.core import Core                                     # noqa: E402
from mw.llm import StubLLM                                   # noqa: E402
from mw.memory import MemoryStore                            # noqa: E402
from build_g7d_seed import FACTS                             # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--seed", required=True)
    p.add_argument("--heldout", required=True)
    args = p.parse_args(argv)

    seed = Path(args.seed).expanduser().resolve()
    tasks = [json.loads(l) for l in
             Path(args.heldout).read_text().splitlines() if l.strip()]
    ok = True
    for t in tasks:
        mem = MemoryStore(seed)
        core = Core(llm=StubLLM(), memory=mem, memory_mode="raw")
        st = core._state(t["prompt"], [], 1, task_id=t["id"],
                         scorer_sha="probe", suite_sha="probe")
        head = "[语义记忆]"
        i = st.find(head)
        want = FACTS.get(t["id"], "")
        has_block = i >= 0
        has_fact = bool(want) and want[: min(len(want), 120)] in st
        good = has_block and has_fact
        ok &= good
        print(f"{'✅' if good else '❌'} {t['id']:24s} state_len={len(st):4d}"
              f" 块={'有' if has_block else '无'} 本条惯例逐字在={has_fact}")
        if has_block:
            print(f"     {st[i:i + 150]}".replace("\n", " "))
        mem.close()
    print("状态级送达：" + ("通过（惯例确实进了拼给模型的状态）"
                          if ok else "❌ 失败 ⇒ 不能把读数读成'模型不用'"))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
