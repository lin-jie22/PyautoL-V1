#!/usr/bin/env python3
"""§17-7d 的**状态级送达探针**（零模型调用）。

R1 只证明 `recall()` 能取回惯例；**取回 ≠ 进了拼给模型的状态**。
`core._state()` 的取舍顺序是 目标 > 工作记忆 > 步号 > 语义记忆 > 轨迹，
预算 `STATE_CHAR_CAP`，语义记忆排在第四位、塞不下会被静默挤掉（s30 已实测过一次
"48/48 格都在"）。所以本轮必须再证一遍：**每道题的惯例原文确实出现在 state 里**。

本探针直接调用真实 `Core._state()`（不跑循环、不调模型），逐题输出：
    state 长度 / `[语义记忆]` 块在不在 / 本题那条惯例是否**逐字留在语义块里** /
    ⚠️ **有没有 fact 文本出现在语义块之外**（第三十六轮 §17-7h ⑭② 新增的第二层）。

⛔ 旧写法是 `want[:120] in st`（**整段** state）—— 7g 实测证明那会把经
   `[工作记忆]` 漏进来的也算成"送达"，于是第二条注入路径在读数里完全不可见。
   现在改由 `bench/state_audit.py` 按块切开判定（`own_in_semantic and not outside_all`）。

⛔ 它不改判据、不进任何判定；它只回答"送到没送到、有没有第二条路"。

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
from build_g7d_seed import VARIANTS                          # noqa: E402
from state_audit import audit_state, format_audit            # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--seed", required=True)
    p.add_argument("--heldout", required=True)
    #: ★7f：惯例文本变体（只影响"逐字在不在"的比对；不改任何判据）
    p.add_argument("--facts", default="7d", choices=sorted(VARIANTS))
    args = p.parse_args(argv)
    facts = VARIANTS[args.facts]

    seed = Path(args.seed).expanduser().resolve()
    tasks = [json.loads(l) for l in
             Path(args.heldout).read_text().splitlines() if l.strip()]
    ok = True
    for t in tasks:
        mem = MemoryStore(seed)
        core = Core(llm=StubLLM(), memory=mem, memory_mode="raw")
        st = core._state(t["prompt"], [], 1, task_id=t["id"],
                         scorer_sha="probe", suite_sha="probe")
        # ★ §17-7h ⑭②：判据从"整段 state 里有这 120 字"改成**按块**审计。
        #   旧写法（`want[:120] in st`）会把经工作记忆漏进来的也算成"送达"，
        #   而 7g 实测那条路真的存在 ⇒ 旧判据看不见它。
        a = audit_state(st, facts, t["id"])
        good = a["ok"]
        ok &= good
        print("  " + format_audit(a)
              + f"  state_len={len(st):4d} 块字符数={a['blocks']}")
        i = st.find("[语义记忆]")
        if i >= 0:
            print(f"     {st[i:i + 150]}".replace("\n", " "))
        mem.close()
    print("状态级送达（两层）：" + ("通过（本题惯例在语义块里，且**没有** fact 漏在别的块）"
                                  if ok else "❌ 失败 ⇒ 不能把读数读成'模型不用'"))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
