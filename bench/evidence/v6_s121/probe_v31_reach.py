"""V31 押注前的到达率测量（纪律 31：**先量载荷能不能到达押注的格子**，再押）。

## 它回答三个问题，全部离线（零 LLM、零网络）

**Q1（口径对不对）**：种子账本里的 `scorer_sha` / `suite_sha` 与本轮**当前**值是否相等？
不等 ⇒ `admissible()` 会按 `cross_scorer`/`cross_suite` 全拒 ⇒ 载荷一条都送不到，
而 run 会照常跑完、照常打印「载入 6 条」—— 那是 V29 形状的假零。

**Q2（送没送到）**：`cap=2400` 下，五个条件各自把载荷送进了哪几题的 `_state`？
判据是**逐字**：`MARKER in state`（P 的一句话）/ `PLACEBO_MARKER in state`（Q 的一句话）。
⛔ 不许用「账本载入 6 条」冒充「送到」—— 那是纪律 29（读数标签不是读数）。

**Q3（预算会不会再吃掉它）**：`core.py:1451-1457` 按优先级拼块，余量不足就**整块丢**。
V30 已把 cap 抬到 2400 并过了 6/6；本轮 P/Q 比原来的 `通过 16/16` 多 66 字，
必须重新量一遍（多 66 字足以在边缘题上把块挤掉）。

## 两档负载

`bare`（无工作记忆，最宽松）与 `real`（3×45 ≈ 124 字，V29/V30 实测的真实上限）。
判决闸**只认 `real`**；`bare` 只作对照（若连 bare 都不到，那是结构问题不是负载问题）。

用法：`python3 probe_v31_reach.py > p0_reach.txt`
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
sys.path.insert(0, str(ROOT))

HERE = Path(__file__).resolve().parent
SUITE = ROOT / "bench" / "specgen" / "tasks_v8.jsonl"
#: ⛔ 必须与 `run.py:915-916` 的**默认**打分器一致：那里是 `bench/scorers/code_exec.py`。
#:    第一版我写成了 `assertion.py` ⇒ 算出的 scorer_sha 是 `254bef5b…`，
#:    与种子里的 `d4b1cbbf…` 不符 ⇒ 探针直接判「种子不可用」。
#:    **那是探针自己的错，不是种子的错** —— 如果跳过这一步，就会得到一条
#:    「载荷送不到」的假结论，然后拿去改实验设计。这正是 §93.5 的同一种坑。
SCORER = ROOT / "bench" / "scorers" / "code_exec.py"
SEEDS = HERE / "seeds"
CAP = 2400
TASKS = ("e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni")
BLK = re.compile(r"\[外部评审\] (.*?)(?:\n\[|$)", re.S)
SRC = re.compile(r"\[[\w]+/(\w+)\]")

sys.path.insert(0, str(HERE))
from make_seed import MARKER, PLACEBO_MARKER, P, Q  # noqa: E402

FILLER = "stdout: ok 输出行 {i} 已写入 /tmp/out.json exit=0 "
PROFILES = {"bare": 0, "real": 3}
WM_WIDTH = 45


def make_wm(n: int) -> list[str]:
    out = []
    for i in range(n):
        s = "".join(FILLER.format(i=i) for _ in range(1 + WM_WIDTH // len(FILLER)))
        out.append(s[:WM_WIDTH])
    return out


def build(tmp: Path, seed: Path | None, wm: list[str]):
    os.environ["V1_STATE_DIR"] = str(tmp / "state")
    os.environ["V1_STATE_CHARS"] = str(CAP)
    from mw import config as cfg
    from mw.core import Core
    from mw.critique import CritiqueLedger
    from mw.llm import StubLLM

    s = cfg.settings()
    assert s.limits.state_chars == CAP, (s.limits.state_chars, CAP)
    led = None
    n = 0
    if seed is not None:
        led = CritiqueLedger(seed)
        n = led.load()
    core = Core(s, llm=StubLLM(), critique_ledger=led)
    core.memory.reset_working()
    for w in wm:
        core.memory.think(w)
    assert len(core.memory.working_view()) == len(wm)
    return core, n


def main() -> int:
    # ── Q1 口径 ────────────────────────────────────────────────────────
    from mw.bench import ExternalScorer, Suite
    suite = Suite.load(SUITE)
    scorer = ExternalScorer([sys.executable, str(SCORER)], source=SCORER)
    cur = (scorer.sha(), suite.sha())
    base = [json.loads(x) for x in (SEEDS / "s1_base.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    seed_sig = (base[0]["scorer_sha"], base[0]["suite_sha"])
    sig_ok = cur == seed_sig

    goal_of = {}
    for line in SUITE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            goal_of[str(d["id"])] = str(d.get("prompt") or d.get("goal") or "")

    out: list[str] = []
    p = out.append
    p("=" * 104)
    p("V31 押注前：可行动载荷的到达率（离线，零 LLM）")
    p(f"cap={CAP}  种子目录 {SEEDS}  题集 {SUITE.name}")
    p("=" * 104)

    p("\n## Q1 口径对账（种子签名 vs 本轮当前签名）\n")
    p(f"- 种子 scorer_sha = {seed_sig[0]}")
    p(f"- 当前 scorer_sha = {cur[0]}   {'✅ 一致' if cur[0] == seed_sig[0] else '❌ 不一致'}")
    p(f"- 种子 suite_sha  = {seed_sig[1]}")
    p(f"- 当前 suite_sha  = {cur[1]}   {'✅ 一致' if cur[1] == seed_sig[1] else '❌ 不一致'}")
    p(f"- 判决：{'✅ 种子可用（不会被三道闸拒收）' if sig_ok else '❌ 种子不可用 ⇒ 载荷一条都送不到，必须先重建种子'}")

    p("\n## Q1b 载荷原文与可视预算\n")
    p(f"- P（{len(P)} 字，可行动）：{P}")
    p(f"- Q（{len(Q)} 字，等长安慰剂）：{Q}")
    p(f"- 标记词 MARKER={MARKER!r}  PLACEBO_MARKER={PLACEBO_MARKER!r}")
    p(f"- `as_line()[:140]` 内 P 完整可见：{23 + len(P) <= 140}（head 23 + {len(P)} = {23 + len(P)}）")

    conds = ["s0_off", "s1_base", "s2_fix_nondeliv", "s3_placebo", "s4_fix"]
    reach: dict[tuple[str, str], dict[str, list[str]]] = {}

    p("\n## Q2 到达矩阵（`_state` 里逐字命中）\n")
    p(f"{'条件':17s} {'负载':5s} " + " ".join(f"{t:>6s}" for t in TASKS) + "   块被挤掉")
    with tempfile.TemporaryDirectory() as td:
        for name in conds:
            seed = None if name == "s0_off" else SEEDS / f"{name}.jsonl"
            for prof, n_wm in PROFILES.items():
                tmp = Path(td) / f"{name}_{prof}"
                tmp.mkdir(parents=True, exist_ok=True)
                core, n_led = build(tmp, seed, make_wm(n_wm))
                # 用**同一条**账本算每个题的 state；`_state` 内部走 `_admissible_critiques`
                led = core.critique_ledger
                rows = []
                for t in TASKS:
                    core.state_trimmed = 0
                    s = core._state(goal_of[t], [], 1, task_id=t,
                                    scorer_sha=seed_sig[0], suite_sha=seed_sig[1])
                    blk = BLK.search(s)
                    got_p = MARKER in s
                    got_q = PLACEBO_MARKER in s
                    rows.append({
                        "task": t, "len": len(s), "has_block": bool(blk),
                        "blk_len": len(blk.group(1)) if blk else 0,
                        "p": got_p, "q": got_q,
                        "trimmed": core.state_trimmed,
                        "srcs": SRC.findall(blk.group(1)) if blk else [],
                    })
                reach[(name, prof)] = {r["task"]: r for r in rows}
                marks = []
                for r in rows:
                    marks.append("P" if r["p"] else ("Q" if r["q"] else ("块" if r["has_block"] else "—")))
                n_trim = sum(r["trimmed"] for r in rows)
                p(f"{name:17s} {prof:5s} " + " ".join(f"{m:>6s}" for m in marks) + f"   {n_trim}")
                if name == "s0_off" and n_led:
                    p(f"  ⚠️ s0_off 竟载入 {n_led} 条（应该 0）—— 条件名与开关不一致")
                core.memory.close()

    p("\n图例：`P`=载荷送到 · `Q`=安慰剂送到 · `块`=有评审块但不含标记 · `—`=无块")
    p("（`s2` 的 P 放在 `e1ni`，而 `e1ni` **永不在任何送达窗口**，所以全程应为 `块`/`—`）")

    # ── Q3 逐条明细：送达那几题，块里到底是什么 ──────────────────────
    p("\n## Q3 `s4_fix` / `real` 档逐题明细（块里那三段原样）\n")
    r4 = reach[("s4_fix", "real")]
    for t in TASKS:
        r = r4[t]
        p(f"--- {t}  state={r['len']}  block={r['blk_len']}  来源={r['srcs']}  "
          f"P={r['p']}  Q={r['q']}  trimmed={r['trimmed']}")

    # ── 判决 ──────────────────────────────────────────────────────────
    tgt = ["e1wi", "e1ni", "e2wi", "e2ni", "e3ni"]   # e3wi 因自评闸拿不到自己那条
    ok_s4 = all(reach[("s4_fix", "real")][t]["p"] for t in tgt) and \
        not reach[("s4_fix", "real")]["e3wi"]["p"]
    ok_s3 = all(reach[("s3_placebo", "real")][t]["q"] for t in tgt) and \
        not any(reach[("s3_placebo", "real")][t]["p"] for t in TASKS)
    ok_s2 = not any(reach[("s2_fix_nondeliv", "real")][t]["p"] for t in TASKS)
    ok_s1 = not any(reach[("s1_base", "real")][t]["p"] or reach[("s1_base", "real")][t]["q"] for t in TASKS)
    ok_s0 = not any(reach[("s0_off", "real")][t]["has_block"] for t in TASKS)

    p("\n## Q4 判决（本探针只做前提，不出效应）\n")
    p(f"- `P0-a` 口径一致：{'✅' if sig_ok else '❌'}")
    p(f"- `P0-b` `s4` 把 P 送到 {tgt} 且不送到 e3wi：{'✅' if ok_s4 else '❌'}")
    p(f"- `P0-c` `s3` 把 Q 送到同一 5 题、且 P 一次都没出现：{'✅' if ok_s3 else '❌'}")
    p(f"- `P0-d` `s2` 的 P **零送达**（放在永不送达的条目里）：{'✅' if ok_s2 else '❌'}")
    p(f"- `P0-e` `s1` 不含任何标记（基线干净）：{'✅' if ok_s1 else '❌'}")
    p(f"- `P0-f` `s0_off` 一个块都没有（开关真的关）：{'✅' if ok_s0 else '❌'}")
    ok = all([sig_ok, ok_s4, ok_s3, ok_s2, ok_s1, ok_s0])
    p(f"\n⇒ {'✅ 前提全部成立，V31 装置可跑' if ok else '❌ 前提不成立 ⇒ 预注册的判据必须改写，装置不许起跑'}")
    print("\n".join(out))
    print("\nVERDICT", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
