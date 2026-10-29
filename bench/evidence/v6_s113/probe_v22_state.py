#!/usr/bin/env python3
"""V22 的 P0-d（零模型）—— `e1wi` 在空库与 S0 库下，进 prompt 的**每一段**是否相同。

为什么要这个探针：
  A 臂（空库）的 `e1wi` 交 `kept = set()` + `kept.append(...)`（AttributeError，0 分）；
  V22 `S0` 臂（有 facts + 其他 episode、**没有** reward 段）交的是 `kept=[] / seen=set()`（16 分）。
  但 `probe_v22_seed.py` 已证 `guidance()` 在两库下都是**空串**。
  ⇒ 如果连 `_state()` 的两段（`recall` / `working_view`）也相同，那 prompt 就是相同的，
     「S0 让 e1wi 修对」这个说法不成立，差异只能来自**跨进程的模型非确定性**。
  ⇒ 这条判断决定 V22 的读数能不能归因，必须先量。

⛔ 不碰模型：只调 `MemoryStore` 的两个读入口（`recall` / `working_view`）。

用法：
    python3 bench/evidence/v6_s113/probe_v22_state.py <seed_0.db> <seed_s0.db> <seed_s1.db> [tasks_v8.jsonl]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(V1))

from mw.features import feature_set          # noqa: E402
from mw.llm import StubLLM                   # noqa: E402
from mw.memory import MemoryStore            # noqa: E402
from mw.worldmodel import WorldModel         # noqa: E402


def sections(db: Path, prompt: str, k: int = 6) -> dict:
    m = MemoryStore(db)
    recalled = m.recall(prompt, k=k)
    return {
        "recall_n": len(recalled),
        "recall_text": "\n".join(f"- {f.text}" for f in recalled),
        "working": m.working_view(),
        "pool": m.pool_size(),
        "guidance": WorldModel(m, StubLLM()).guidance(action=prompt, k=3),
    }


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    suite = Path(argv[4] if len(argv) > 4 else V1 / "bench/specgen/tasks_v8.jsonl")
    tasks = {str(t["id"]): t for t in
             (json.loads(x) for x in suite.read_text(encoding="utf-8").splitlines() if x.strip())}
    tid = "e1wi"
    prompt = str(tasks[tid]["prompt"])

    labels = [("EMPTY", "空库(不存在)"), ("S0", "S0(无reward)"), ("S1", "S1(有reward)")]
    out = {}
    for (key, lbl), p in zip(labels, argv[1:4]):
        pp = Path(p)
        out[key] = (lbl, sections(pp, prompt) if pp.exists() else None)

    for key, (lbl, s) in out.items():
        print(f"\n=== {lbl}")
        if s is None:
            print("    (库不存在 = A 臂的 `--fresh` 每题空库)")
            continue
        print(f"    recall_n={s['recall_n']}  pool={s['pool']}  len(guidance)={len(s['guidance'])}")
        print(f"    working_view={s['working']}")
        if s["recall_text"]:
            print("    recall_text:")
            for line in s["recall_text"].splitlines():
                print("      " + line[:160])
        else:
            print("    recall_text=(空)")

    # ── 判决：进 prompt 的段落是否相同 ────────────────────────────────
    print("\n" + "=" * 68)
    keys = ("recall_text", "working", "guidance")
    s0, s1 = out["S0"][1], out["S1"][1]
    same_s0_s1 = all((s0[k] == s1[k]) for k in keys)
    print(f"S0 与 S1 的 (recall/working/guidance) 三段是否全同：{'是' if same_s0_s1 else '否'}")
    print("空库那侧没有 MemoryStore（A 臂每题临时库）⇒ 按「两段都为空」推断：")
    empty_like = (s0["recall_n"] == 0 and not s0["working"] and not s0["guidance"])
    if empty_like:
        print("⇒ P0-d：**S0 的库在 `_state()` 与 `guidance()` 两条路上一个字节都不吐**，")
        print("   与空库进 prompt 的段落完全相同。⇒ 库内容不是 V22 里 e1wi 拿满分的原因。")
        print("   ⛔ 也不许反过来写「A 臂 e1wi=0 所以库有害」：")
        print("     A 臂跑在 V21 的 21:12~23:59，S0 跑在 V22 的 00:30~02:00 —— **跨时段**，")
        print("     纪律 2 明令不算数。两边只能各自在**本时段内**和兄弟臂比。")
    else:
        print("⇒ P0-d：S0 与空库**有**段落不同 ⇒ 差异可归因到 recall/working。")
    print(f"   （S0 库 pool={s0['pool']} 条 facts，但 recall 命中 {s0['recall_n']} 条 —— "
          f"**facts 存在 ≠ facts 被检索到**；这是「有机制」与「机制通电」的又一例）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
