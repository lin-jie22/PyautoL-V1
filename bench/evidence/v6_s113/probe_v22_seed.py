#!/usr/bin/env python3
"""V22 的 P0 前提闸（零模型）—— 两套 seed 库渲染出的提示差在哪一段。

V22 的唯一变量是「库里有 18 条 `reward` 记录 vs 没有」。这个探针证明：

  ① `S1`（有 reward）与 `S0`（无 reward）渲染出的 `wm.guidance()` **只在 reward 段不同**；
  ② `S0` 的那一段**整块消失**（不是留一个空标题）；
  ③ 其余段（`facts` 的「已知易错点」）逐字节相同 —— 否则就不是单变量。

⛔ 不碰模型：只调真正的 `WorldModel.guidance`，所以它印出的就是内核会给模型的那段。

用法：
    python3 bench/evidence/v6_s113/probe_v22_seed.py <seed_s1.db> <seed_s0.db> [tasks_v8.jsonl]
退出码：0 = 单变量成立；1 = 不成立（并印出差异）
"""
from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(V1))

from mw.features import feature_set          # noqa: E402
from mw.llm import StubLLM                   # noqa: E402
from mw.memory import MemoryStore            # noqa: E402
from mw.worldmodel import WorldModel         # noqa: E402


def render(db: Path, tasks: list[dict]) -> dict[str, str]:
    wm = WorldModel(MemoryStore(db), StubLLM())
    out = {}
    for t in tasks:
        tid = str(t["id"])
        # ⚠️ `action` 必须传**题面原文**：`guidance` 用它去 `memory.recall(action)`，
        #    传错就等于换了一套检索条件，两库的 facts 段就不再可比。
        out[tid] = wm.guidance(action=str(t["prompt"]), k=3,
                               features=feature_set(t), task_id=tid)
    out["__stats__"] = (f"挡自己={wm.rewards_skipped_self} 挡成功={wm.rewards_skipped_success} "
                        f"读={wm.rewards_seen}")
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    s1_db, s0_db = Path(argv[1]), Path(argv[2])
    suite = Path(argv[3] if len(argv) > 3 else V1 / "bench/specgen/tasks_v8.jsonl")
    for p in (s1_db, s0_db):
        if not p.exists():
            print(f"❌ seed 不存在：{p} ⇒ P0 不可判")
            return 1
    tasks = [json.loads(x) for x in suite.read_text(encoding="utf-8").splitlines() if x.strip()]

    a = render(s1_db, tasks)
    b = render(s0_db, tasks)
    print(f"S1(有 reward)  {s1_db}   {a.pop('__stats__')}")
    print(f"S0(无 reward)  {s0_db}   {b.pop('__stats__')}")

    ok = True
    for tid in [str(t["id"]) for t in tasks]:
        ta, tb = a[tid], b[tid]
        same = ta == tb
        # S0 里不该出现 reward 段的标志词
        leak = ("同类题里 `" in tb) or ("先检查" in tb)
        flag = " ✅ 两库相同（该题不受 reward 影响）" if same else ""
        if leak:
            ok = False
            flag += " ❌ S0 仍含 reward 段"
        print(f"\n--- {tid}  len(S1)={len(ta)} len(S0)={len(tb)}{flag}")
        if same:
            continue
        for line in difflib.unified_diff(ta.splitlines(), tb.splitlines(),
                                         "S1", "S0", lineterm="", n=1):
            print("    " + line)

    print("\nP0 =", "✅ 单变量成立（差别只在 reward 段，且 S0 整块消失）" if ok else "❌ 不成立")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
