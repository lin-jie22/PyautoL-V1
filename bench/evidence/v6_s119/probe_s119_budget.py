"""为什么 4 道题的 state 里没有 `[外部评审]` —— 是闸门拒了，还是预算剪了？

## 这个判别为什么必须做

V29 的 `p2_r2` 里只有 `e1wi`/`e1ni` 的 state 含 `[外部评审]`，
而 `e2wi`/`e2ni`/`e3wi`/`e3ni` 一个都没有。两种解释对判决的后果**完全相反**：

- 若闸门拒了（`admissible` 返回空）⇒ 通道在多数题上没通 ⇒ 整轮读数作废；
- 若预算剪了（`STATE_CHAR_CAP=1600`，题面越长辅助块越没位置）
  ⇒ 通道通了，但**实验材料没到模型眼前** ⇒ 「零价值」这个判词不成立，
    只能判「未测到」。

⛔ 不许靠读代码猜。这里做**单变量离线复算**：同一份 goal、同一份账本，
   只改 `cfg.STATE_CHAR_CAP` 一个数，看块出不出来。

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


def build(tmp: Path, crit_path: Path):
    os.environ["V1_STATE_DIR"] = str(tmp / "state")
    from mw import config as cfg
    from mw.core import Core
    from mw.critique import CritiqueLedger
    from mw.llm import StubLLM
    led = CritiqueLedger(crit_path)
    n = led.load()
    core = Core(cfg.settings(), llm=StubLLM(), critique_ledger=led)
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
        cfg, core, n_loaded = build(Path(td), Path(CRIT))
        out.append(f"账本载入 {n_loaded} 条  scorer_sha={scorer_sha[:12]}  suite_sha={suite_sha[:12]}")
        out.append(f"STATE_CHAR_CAP 默认 = {cfg.STATE_CHAR_CAP}")
        out.append("")
        out.append(f"{'题':6s} {'cap':>5s} {'goal_len':>8s} {'state_len':>9s} {'评审块':>5s}  {'trimmmed':>8s}  块序")
        for tid in ("e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni"):
            g = goal_of[tid]
            core.state_trimmed = 0
            s = core._state(g, [], 1, task_id=tid, scorer_sha=scorer_sha, suite_sha=suite_sha)
            blocks = [t for t in ("[目标]", "[工作记忆]", "[语义记忆]", "[外部评审]", "[轨迹]") if t in s]
            out.append(f"{tid:6s} {cfg.STATE_CHAR_CAP:5d} {len(g):8d} {len(s):9d} "
                       f"{int('[外部评审]' in s):5d}  {core.state_trimmed:8d}  {','.join(blocks)}")

        # ── 只改一个数：把 cap 放到足够大 ────────────────────────────────
        out.append("")
        out.append(f"—— 只把 cap 改成 {BIG}（别的都不动）——")
        out.append(f"{'题':6s} {'cap':>5s} {'state_len':>9s} {'评审块':>5s}  {'trimmmed':>8s}")
        cfg.STATE_CHAR_CAP = BIG
        recovered = 0
        for tid in ("e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni"):
            g = goal_of[tid]
            core.state_trimmed = 0
            s = core._state(g, [], 1, task_id=tid, scorer_sha=scorer_sha, suite_sha=suite_sha)
            has = "[外部评审]" in s
            recovered += int(has)
            out.append(f"{tid:6s} {cfg.STATE_CHAR_CAP:5d} {len(s):9d} {int(has):5d}  {core.state_trimmed:8d}")
        out.append("")
        out.append(f"cap={BIG} 时 6 题里有评审块的：{recovered}/6")

        # ── 反向：账本清空 ⇒ 块必须消失（证明块来自账本而不是常驻） ──────
        cfg.STATE_CHAR_CAP = 1600
        core.critique_ledger.by_key.clear()
        core.state_trimmed = 0
        s = core._state(goal_of["e1wi"], [], 1, task_id="e1wi",
                        scorer_sha=scorer_sha, suite_sha=suite_sha)
        out.append(f"清空账本后 e1wi 含评审块={'[外部评审]' in s}（应为 False）")
        core.memory.close()

    txt = "\n".join(out)
    print(txt)
    Path("/Users/linshaojie/V1/bench/evidence/v6_s119/p0_budget_probe.txt").write_text(
        txt + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
