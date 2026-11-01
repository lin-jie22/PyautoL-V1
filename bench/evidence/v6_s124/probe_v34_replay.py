"""V34 决定性探针：同一个 prompt 重放 N 次，答案唯一吗？（§99）

为什么必须先量这一条
--------------------
V33 判 `J_ANY` 时给了一条通则（纪律 41）：「`T=0` 装置的预注册必须先声明
**有效样本 = 每臂的独立 prompt 数，不是轮数**」，理由是 `P` 臂 r2~r4 的答案
sha 全同 ⇒「3/3 实为 1 次观察」。

这个推理**依赖一个从未被直接测过的前提**：`T=0` + 同一个 prompt ⇒ 同一个答案。

离线数据里已经有反例的嫌疑（`p0_samestate.txt`）：三组 state **逐字节相同**、
答案却各有 2~3 种。但 `--state-dump` 落的只是 `_state()` 的产物，
不是完整 prompt（`guidance` / `history` 没进 dump）⇒ 那条反例**不闭合**。

本探针绕开这个缺口：不问「两次运行为什么不同」，直接把**同一份 messages**
连发 N 次，数答案的种类。这一步不需要 dump 与 prompt 等价。

它答的不是「系统变强了没有」，而是「**这台装置的一发，是抽样还是重放**」——
后者是前者所有比较的前提。

⛔ 本探针不判「内核那一发的分布」：messages 由 `Planner.propose` 真拼（用的是
   `Core` 里的同一个 `ToolRegistry`），但 `guidance` 显式给空串、`history` 给空表。
   所以它量的是「模型层：同输入是否同输出」，不是「装置层：某一臂的答案分布」。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
sys.path.insert(0, str(ROOT))

from mw import config as cfg          # noqa: E402
from mw.core import Core              # noqa: E402
from mw.llm import HTTPLLM, Message   # noqa: E402
from mw.planner import Planner        # noqa: E402
from mw.llm import StubLLM            # noqa: E402

TASK = "e1wi"
STATE_FILE = Path("/tmp/v33/states_c0_r1.jsonl")
SUITE = ROOT / "bench/specgen/tasks_v8.jsonl"
N_REPS = int(os.environ.get("V34_N", "16"))

TW = re.compile(r"^\s*kept\s*=\s*set\(\)", re.M)
LIST = re.compile(r"^\s*kept\s*=\s*\[\]", re.M)
IMP = re.compile(r"^\s*import\s+re\b", re.M)


def h8(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def task_prompt(task_id: str) -> str:
    for line in SUITE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        if d.get("id") == task_id:
            return d["prompt"]
    raise SystemExit(f"suite 里没有 {task_id}")


def dumped_state(task_id: str, it: int = 1) -> str:
    """取 V33 `c0_r1` 里该题第 `it` 步的 state（同 (task, it) 可能多行 ⇒ 取第一行）。"""
    for line in STATE_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        if d.get("task") == task_id and int(d.get("it") or 0) == it:
            return d["state"]
    raise SystemExit(f"{STATE_FILE} 里没有 {task_id} it={it}")


class Recorder:
    """记下 planner 真正会发出去的 messages，不联网。"""

    def __init__(self) -> None:
        self.calls: list[list[Message]] = []

    def complete(self, messages, *, max_tokens=1024, temperature=None, timeout_s=None) -> str:
        self.calls.append(list(messages))
        return "{}"

    @property
    def timeout_s(self) -> float:
        return 120.0


def build_messages(goal: str, state: str, n: int) -> list[Message]:
    """用 `Core` 里同一份 `ToolRegistry` 与同一套 `Planner` 拼提示词。"""
    s = cfg.settings()
    core = Core(s, llm=StubLLM(), wire_external=False)
    rec = Recorder()
    pl = Planner(rec, core.tools, max_tokens=int(s.limits.llm_max_tokens),
                 state_chars=int(s.limits.state_chars))
    pl.propose(goal=goal, state=state, history=[], n=n, guidance="",
               blocked=(), rejected=())
    core.memory.close()
    assert len(rec.calls) == 1, f"planner 发起了 {len(rec.calls)} 次调用"
    return rec.calls[0]


def main() -> None:
    goal = task_prompt(TASK)
    state = dumped_state(TASK)
    print(f"# V34 重放探针  task={TASK}  state_len={len(state)}  N={N_REPS}")
    print(f"# base_url={cfg.settings().llm.base_url}  model={cfg.settings().llm.model}"
          f"  temperature={cfg.settings().llm.temperature}"
          f"  max_tokens={cfg.settings().limits.llm_max_tokens}")
    print()

    llm = HTTPLLM(cfg.settings().llm)
    groups = {}
    for n_cand in (1, 3):
        msgs = build_messages(goal, state, n_cand)
        fp = h8("\n".join(f"{m.role}\x00{m.content}" for m in msgs))
        groups[n_cand] = (msgs, fp)
        print(f"## 组 {'AB'[n_cand == 3]}  n={n_cand}  messages_sha={fp}  "
              f"chars={sum(len(m.content) for m in msgs)}")
        print()

    print(f"{'组':4s} {'次':>3s} {'答案 sha':10s} {'len':>5s} 容器  import_re")
    out = {"groups": {}, "raw": []}
    for n_cand, (msgs, fp) in groups.items():
        shas, cont, imp = [], [], []
        for i in range(1, N_REPS + 1):
            try:
                raw = llm.complete(msgs, max_tokens=int(cfg.settings().limits.llm_max_tokens))
            except Exception as e:  # noqa: BLE001
                print(f"{'AB'[n_cand == 3]:4s} {i:3d}  ❗ {type(e).__name__}: {e}")
                out["raw"].append({"group": n_cand, "rep": i, "error": f"{type(e).__name__}: {e}"})
                continue
            s8 = h8(raw)
            c = "set()" if TW.search(raw) else ("list" if LIST.search(raw) else "?")
            r = "有" if IMP.search(raw) else "无"
            shas.append(s8)
            cont.append(c)
            imp.append(r)
            print(f"{'AB'[n_cand == 3]:4s} {i:3d} {s8:10s} {len(raw):5d} {c:5s} {r}")
            out["raw"].append({"group": n_cand, "rep": i, "sha": s8, "len": len(raw),
                               "container": c, "import_re": r, "text": raw})
        out["groups"][n_cand] = {"messages_sha": fp, "shas": shas,
                                 "distinct": sorted(set(shas)),
                                 "k": len(set(shas)),
                                 "containers": {x: cont.count(x) for x in set(cont)},
                                 "import_re": {x: imp.count(x) for x in set(imp)}}
        print()
        print(f"  ⇒ 组 {'AB'[n_cand == 3]}：k={len(set(shas))} 种答案 {sorted(set(shas))}  "
              f"容器 {out['groups'][n_cand]['containers']}")

    print()
    print("### 判决输入")
    ka = out["groups"].get(1, {}).get("k")
    kb = out["groups"].get(3, {}).get("k")
    print(f"  D1 组 A（n=1）k = {ka}")
    print(f"  D3 组 B（n=3）k = {kb}")
    if ka is None:
        print("  ⇒ P0-c 失败：组 A 没有有效读数 ⇒ UNKNOWN")
    elif ka >= 2:
        print("  ⇒ J_REPLAY_NOISE：同一 prompt 重放就有 ≥2 种答案 ⇒「重放」是「抽样」")
    else:
        print(f"  ⇒ 组 A {N_REPS}/{N_REPS} 全同（{ka} 种）⇒ 本探针量不到噪声")

    Path("/tmp/v34_replay.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n原始读数写到 /tmp/v34_replay.json")


if __name__ == "__main__":
    main()
