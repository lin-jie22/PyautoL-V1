"""V30 押注前的载荷到达率测量（纪律 31：押注前先量「载荷能不能到达押注的格子」）。

## 要回答的三件事

**Q0（现实形状）**：真实运行里 `[工作记忆]` 有多大？
`core.py:1404` 取 `wm[-6:]`、每条尾 200 字 ⇒ 理论上限 1200+ 字。
但实测（`/tmp/v29/states_*.jsonl` 58 个真实快照）只有 **0 / 50 / 83 / 124** 四个值。
⛔ 本探针第一版拿「6 条 × 200 字」当默认负载，把 `cap=2400` 的到达率压到 2/6 ——
   那是**现实里不存在的负载**，用它改实验设计就是拿假读数调参。
   ⇒ 现在分两档：`real`（3 条 × 45 字，等价实测上限 124）与 `stress`（6 条 × 200 字，物理上限）。
   判决闸**只认 `real`**；`stress` 只作为边界登记。

**Q1（到达率）**：`cap=2400` 时六题是不是**每题**都有 `[外部评审]`？
V29 在 `cap=1600` 下只有 2/6（`e1wi`/`e1ni`）；另外 4 题的 **题面本身**就 1478~1705 字，
`core.py:1451-1457` 的预算循环把第 4 优先级的块整块丢掉。
V30 的全部意义就是把这个 2/6 变成 6/6。

**Q2（信息密度）**：就算送到了，那三段字里**有多少是可行动的**？
`core.py:1443` 是 `crit[-3:]`、`critique.py:126` 按 `task_id` 字典序排
⇒ 恒取 `e2wi / e3ni / e3wi`。V29 落盘的三条里 `e2wi` 与 `e3wi` 的 `why` 就是
「通过 16/16」（满分 ⇒ 零可行动信息），只有 `e3ni` 那条带 `NameError: _split`。
⚠️ 这一条**不改变 V30 的装置**（不改内核 = 保住单变量），但必须写进预注册的押注依据：
若 V30 判零效应，正确的下一步不是再拧预算，而是治「送进去的东西」。

零 LLM、零网络。用法：`probe_v30_reach.py > p0_reach.txt`
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/Users/linshaojie/V1")

SUITE = "/Users/linshaojie/V1/bench/specgen/tasks_v8.jsonl"
CRIT = "/tmp/v29/mem_p2/critique.jsonl"
V29D = Path("/tmp/v29")
CAPS = (1600, 2400)
TASKS = ("e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni")
BLK = re.compile(r"\[外部评审\] (.*?)(?:\n\[|$)", re.S)
WMB = re.compile(r"\[工作记忆\] (.*?)(?:\n\[|$)", re.S)
SRC = re.compile(r"\[[\w]+/(\w+)\]")

#: 两档负载：`real` 按实测（3×45 ≈ 124 含分隔符），`stress` 按 `wm[-6:]` × 尾 200 的物理上限。
PROFILES = {"real": (3, 45), "stress": (6, 200)}
FILLER = "stdout: ok 输出行 {i} 已写入 /tmp/out.json exit=0 "


def make_wm(n: int, width: int) -> list[str]:
    out = []
    for i in range(n):
        s = "".join(FILLER.format(i=i) for _ in range(1 + width // len(FILLER)))
        out.append(s[:width])
    return out


def build(tmp: Path, cap: int, wm: list[str]):
    os.environ["V1_STATE_DIR"] = str(tmp / f"state_{cap}_{len(wm)}")
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
    core.memory.reset_working()
    #: ⛔ 必须用 `think()` 而不是 `capture()`：`capture()` 写 `blobs` 表，
    #:    **不进** `working` deque ⇒ `working_view()` 仍空 ⇒ 这一档等于没做。
    for w in wm:
        core.memory.think(w)
    assert len(core.memory.working_view()) == len(wm), len(core.memory.working_view())
    return cfg, core, n


def main() -> int:
    goal_of = {}
    for line in Path(SUITE).read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            goal_of[str(d["id"])] = str(d.get("prompt") or d.get("goal") or "")

    rows = [json.loads(x) for x in Path(CRIT).read_text(encoding="utf-8").splitlines() if x.strip()]
    scorer_sha, suite_sha = rows[0]["scorer_sha"], rows[0]["suite_sha"]

    out: list[str] = []
    p = out.append

    p("=" * 100)
    p("V30 押注前：载荷到达率 + 信息密度（离线，零 LLM）")
    p(f"账本 {CRIT}  载入 {len(rows)} 条  scorer_sha={scorer_sha[:12]}  suite_sha={suite_sha[:12]}")
    p("=" * 100)

    # ── 0 账本构成：谁进了池子、谁的信息可行动 ──────────────────────────
    p("\n## 0 账本构成（决定「送进去的是什么」）\n")
    p(f"{'题':6s} {'score':>5s} {'why_len':>7s} {'可行动':>6s}  why[:60]")
    info = {}
    for r in sorted(rows, key=lambda x: x["task_id"]):
        tid, sc, why = r["task_id"], float(r["score"]), str(r["why"])
        act = sc < 1.0 and "失败" in why
        info[tid] = act
        p(f"{tid:6s} {sc:5.2f} {len(why):7d} {int(act):6d}  {why[:60]!r}")

    pool = [r["task_id"] for r in sorted(rows, key=lambda x: x["task_id"])][-3:]
    p("\n`core.py:1443` 的 `crit[-3:]` 实际取到的三条"
      "（`critique.py:126` 按 task_id 字典序排，取尾三）：")
    p(f"  {pool}")
    p(f"  其中**可行动**（失败且有 why）的：{[t for t in pool if info.get(t)]}")
    p(f"  ⛔ 被系统性排除的：{[t for t in info if t not in pool]}"
      f"  ← 固定偏差，不是抽样（V29 §92.6 已量到来源恒为这 3 题）")

    # ── 0.5 现实形状：真实 state 里的工作记忆有多大 ─────────────────────
    p("\n## 0.5 现实形状（`real` 档的依据：V29 全部真实 state 快照）\n")
    lens: list[int] = []
    n_states = 0
    for f in sorted(V29D.glob("states_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            n_states += 1
            m = WMB.search(json.loads(line).get("state", ""))
            lens.append(len(m.group(1)) if m else 0)
    uniq = sorted(set(lens))
    p(f"  快照 {n_states} 个，`[工作记忆]` 块长取值：{uniq}")
    p(f"  非零占比 {sum(1 for x in lens if x)}/{len(lens)}；最大值 {max(lens)} 字")
    p(f"  ⇒ `real` 档取 3 条 × 45 字（含分隔符约 133 字 ≈ 实测上限）；"
      f"`stress` 档取 `wm[-6:]` × 尾 200 的物理上限 1200 字。")

    # ── 1 到达率 ──────────────────────────────────────────────────────
    p("\n## 1 到达率（`[外部评审]` 是否真的出现在 state 里）\n")
    p(f"{'prof':7s} {'cap':>5s} {'题':6s} {'goal':>5s} {'wm字':>5s} {'state':>6s} {'块?':>4s} "
      f"{'块字':>5s} {'trim':>4s} {'源题':22s} {'可行动字':>7s}")
    reach: dict[tuple[str, int], int] = {}
    act_chars: dict[tuple[str, int], int] = {}
    wm_seen: dict[tuple[str, int], int] = {}
    with tempfile.TemporaryDirectory() as td:
        for prof, (n, width) in PROFILES.items():
            wm = make_wm(n, width)
            for cap in CAPS:
                cfg, core, _n = build(Path(td), cap, wm)
                got = tot_act = seen_wm = 0
                for tid in TASKS:
                    g = goal_of[tid]
                    core.state_trimmed = 0
                    st = core._state(g, [], 3, task_id=tid,
                                     scorer_sha=scorer_sha, suite_sha=suite_sha)
                    m = BLK.search(st)
                    blk = m.group(1) if m else ""
                    srcs = SRC.findall(blk)
                    act = sum(len(seg) for seg, sid in
                              zip(blk.split(" ｜ "), srcs) if info.get(sid))
                    mb = WMB.search(st)
                    got += int(m is not None)
                    tot_act += act
                    seen_wm += int(mb is not None)
                    p(f"{prof:7s} {cap:5d} {tid:6s} {len(g):5d} {len(mb.group(1)) if mb else 0:5d} "
                      f"{len(st):6d} {int(m is not None):4d} {len(blk):5d} {core.state_trimmed:4d} "
                      f"{','.join(srcs):22s} {act:7d}")
                reach[(prof, cap)] = got
                act_chars[(prof, cap)] = tot_act
                wm_seen[(prof, cap)] = seen_wm
                core.memory.close()
                p("")

    p("## 2 汇总\n")
    p(f"{'prof':7s} {'cap':>5s} {'有块题数':>8s} {'全部可行动字数':>14s} {'含[工作记忆]题数':>16s}")
    for prof in PROFILES:
        for cap in CAPS:
            p(f"{prof:7s} {cap:5d} {reach[(prof, cap)]:8d}/6 {act_chars[(prof, cap)]:14d} "
              f"{wm_seen[(prof, cap)]:14d}/6")

    ok = reach.get(("real", 2400), 0) == 6
    #: ⛔ 「变异没生效」闸：负载档若 `[工作记忆]` 一次都没出现，说明它根本没加载。
    wm_live = wm_seen.get(("real", 2400), 0) > 0
    #: ⛔ 反向对照：`cap=1600` 必须**不是** 6/6，否则「抬预算」这个自变量没有变化空间。
    base_lt = reach.get(("real", 1600), 0) < 6

    p("")
    p("## 3 判决（本探针只做前提，不出效应）\n")
    p(f"- `real` 档操纵生效（`[工作记忆]` 出现过）：{'✅' if wm_live else '❌ 该档读数作废'}")
    p(f"- 自变量有变化空间（`cap=1600` 不是 6/6）：{reach.get(('real', 1600), 0)}/6 "
      f"{'✅' if base_lt else '❌ ⇒ 抬预算没有可观测的到达率差异，装置不成立'}")
    p(f"- `cap=2400` / `real` 到达率：{reach.get(('real', 2400), 0)}/6 "
      f"{'✅' if ok else '❌ ⇒ 预注册的「载荷到达闸」不成立，V30 装置作废'}")
    p(f"- 边界登记（不作判决）：`stress` 档 `cap=2400` → {reach.get(('stress', 2400), 0)}/6；"
      f"`cap=1600` → {reach.get(('stress', 1600), 0)}/6")
    p("  ⇒ 预算一旦被工作记忆吃满，评审块仍会被整块丢掉；本装置只能保证"
      "「工作记忆现实负载下 6/6」。")
    print("\n".join(out))
    ok = ok and wm_live and base_lt
    print("\nVERDICT", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
