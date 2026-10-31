"""为什么 4 道题的 state 里没有 `[外部评审]` —— 是闸门拒了，还是预算剪了？

## 这个判别为什么必须做

V29 的 `p2_r2` 里只有 `e1wi`/`e1ni` 的 state 含 `[外部评审]`，
而 `e2wi`/`e2ni`/`e3wi`/`e3ni` 一个都没有。两种解释对判决的后果**完全相反**：

- 若闸门拒了（`admissible` 返回空）⇒ 通道在多数题上没通 ⇒ 整轮读数作废；
- 若预算剪了（`STATE_CHAR_CAP=1600`，题面越长辅助块越没位置）
  ⇒ 通道通了，但**实验材料没到模型眼前** ⇒ 「零价值」这个判词不成立，
    只能判「未测到」。

⛔ 不许靠读代码猜。这里做**单变量离线复算**：同一份 goal、同一份账本，
   只改状态预算一个数，看块出不出来。

⚠️ 第 120 轮（§93）之后，预算的**运行时事实源**从模块常量 `cfg.STATE_CHAR_CAP`
   变成了 `Limits.state_chars`（它进口径签名）。本脚本随之改为
   **设 `V1_STATE_CHARS` + 重建 Core** 来做同一个单变量 ——
   ⛔ 不要再用 `cfg.STATE_CHAR_CAP = BIG` 那种 monkeypatch：它现在**改不动**
   `_state()` 的读数，脚本会静默输出「cap=4000 时 0/6」这个假读数
   （纪律 6 的第二种形态：变异没起作用）。

零 LLM、零网络。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/Users/linshaojie/V1")

SUITE = "/Users/linshaojie/V1/bench/specgen/tasks_v8.jsonl"
CRIT = "/tmp/v29/mem_p2/critique.jsonl"
BIG = 4000


def build(tmp: Path, cap: int):
    """按 `cap` 重新构造一个 Core（**唯一**变量就是 `V1_STATE_CHARS`）。"""
    os.environ["V1_STATE_DIR"] = str(tmp / "state")
    os.environ["V1_STATE_CHARS"] = str(cap)
    from mw import config as cfg
    from mw.core import Core
    from mw.critique import CritiqueLedger
    from mw.llm import StubLLM
    s = cfg.settings()
    assert s.limits.state_chars == cap, (s.limits.state_chars, cap)
    led = CritiqueLedger(Path(CRIT))
    n = led.load()
    core = Core(s, llm=StubLLM(), critique_ledger=led)
    assert core.planner.state_chars == cap, core.planner.state_chars
    return cfg, core, n


def main() -> int:
    goal_of = {}
    for line in Path(SUITE).read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            goal_of[str(d["id"])] = str(d.get("prompt") or d.get("goal") or "")

    rows = [json.loads(x) for x in Path(CRIT).read_text(encoding="utf-8").splitlines() if x.strip()]
    scorer_sha = rows[0]["scorer_sha"]
    suite_sha = rows[0]["suite_sha"]

    out = []
    with tempfile.TemporaryDirectory() as td:
        cfg, core, n_loaded = build(Path(td), cap=1600)
        out.append(f"账本载入 {n_loaded} 条  scorer_sha={scorer_sha[:12]}  suite_sha={suite_sha[:12]}")
        out.append(f"state_chars 默认 = {cfg.settings().limits.state_chars}"
                   f"（常量 STATE_CHAR_CAP={cfg.STATE_CHAR_CAP}）")
        out.append("")
        out.append(f"{'题':6s} {'cap':>5s} {'goal_len':>8s} {'state_len':>9s} {'评审块':>5s}  {'trimmmed':>8s}  块序")
        for tid in ("e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni"):
            g = goal_of[tid]
            core.state_trimmed = 0
            s = core._state(g, [], 1, task_id=tid, scorer_sha=scorer_sha, suite_sha=suite_sha)
            blocks = [t for t in ("[目标]", "[工作记忆]", "[语义记忆]", "[外部评审]", "[轨迹]") if t in s]
            out.append(f"{tid:6s} {core.s.limits.state_chars:5d} {len(g):8d} {len(s):9d} "
                       f"{int('[外部评审]' in s):5d}  {core.state_trimmed:8d}  {','.join(blocks)}")
        core.memory.close()

        # ── 只改一个数：把 cap 放到足够大（重建 Core，唯一变量是 V1_STATE_CHARS）──
        out.append("")
        out.append(f"—— 只把 cap 改成 {BIG}（别的都不动）——")
        out.append(f"{'题':6s} {'cap':>5s} {'state_len':>9s} {'评审块':>5s}  {'trimmmed':>8s}")
        cfg2, core2, _ = build(Path(td), cap=BIG)
        recovered = 0
        for tid in ("e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni"):
            g = goal_of[tid]
            core2.state_trimmed = 0
            s = core2._state(g, [], 1, task_id=tid, scorer_sha=scorer_sha, suite_sha=suite_sha)
            has = "[外部评审]" in s
            recovered += int(has)
            out.append(f"{tid:6s} {core2.s.limits.state_chars:5d} {len(s):9d} {int(has):5d}  {core2.state_trimmed:8d}")
        out.append("")
        out.append(f"cap={BIG} 时 6 题里有评审块的：{recovered}/6")

        # ── 反向：账本清空 ⇒ 块必须消失（证明块来自账本而不是常驻） ──────
        core2.critique_ledger.by_key.clear()
        core2.state_trimmed = 0
        s = core2._state(goal_of["e1wi"], [], 1, task_id="e1wi",
                         scorer_sha=scorer_sha, suite_sha=suite_sha)
        out.append(f"清空账本后 e1wi 含评审块={'[外部评审]' in s}（应为 False）")
        core2.memory.close()

    txt = "\n".join(out)
    print(txt)
    Path("/Users/linshaojie/V1/bench/evidence/v6_s119/p0_budget_probe.txt").write_text(
        txt + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
