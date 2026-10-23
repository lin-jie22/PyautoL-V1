"""探针：本轮全灭之后，下一次提问到底变了没有（§30.8 第 1 条）。

## 这个探针回答什么

`propose(blocked=…)` 一直是**请求**模型别重复，而"别重复"的硬闸在内核里
（`attempted_sigs`）。第六十六轮的 A 臂证明请求不管用：第 2、3 步规划器
连续两次只给同一个 `agent:code_agent`，两次都被硬闸丢掉 ⇒ `🧊 停滞 1/2`、`2/2`
⇒ 交出零分。**重复的候选不是错误，浪费掉的那个回合才是。**

修法不是把提示词写得更重，而是：本轮全灭 ⇒ 下一次提问**换形态**
（摆出被丢掉的动作原文 + 列出一次都没试过的工具）。这个探针量三件事：

1. 第 2 次提问里**真的**有「被拒绝」那一段？（提示词层，可 grep）
2. 模型对**换过形态的提问**是不是给了不同的动作？（行为层）
3. 有界：一共问了几次、`reseek` 记了几次？

## 为什么要真模型 + 假外部能力

真模型是唯一能回答第 2 问的对象；外部能力必须**确定性失败**，否则"重复"这件事
取决于网络与生成质量，量不准。所以这里注册一个 `agent:always_refuse`：
它的失败方式与 `external/code_agent.py` 拒绝作答**逐字同形**（缺『函数名必须是 X』）。

## 用法

    V1_LLM_BASE_URL=http://127.0.0.1:11434/v1 V1_LLM_API_KEY=ollama \
    V1_LLM_MODEL=qwen2.5:7b python3 bench/probe_reseek.py [--goal 文本]

⛔ 这不是能力读数。它量的是**装置有没有把话说清楚**，不是内核会不会做题。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mw import config as cfg                                    # noqa: E402
from mw.core import Core                                       # noqa: E402
from mw.llm import build_llm, split_answer                     # noqa: E402
from mw.memory import MemoryStore                              # noqa: E402
from mw.tools import (ToolRegistry, ToolSpec,                  # noqa: E402
                      register_core_tools)

#: 与 `external/code_agent.py` 的拒绝话术**逐字同形**（见该文件 `_FUNC_NAME` 那一支）。
#: ⚠️ 刻意不 import 它：探针不许依赖被观测对象的内部实现（那是自证）。
REFUSAL = "题面里没有『函数名必须是 X』⇒ 本 agent 不适用（不猜）"

#: ⛔ 题面**故意不含**『函数名必须是 X』：这样 `agent:code_agent` 会真的拒绝作答
#: （不是假装的），于是"规划器下一轮还给同一个动作"这件事可以在**真实装置**上复现，
#: 不需要伪造任何失败。第六十六轮 A 臂的形状就是这样：工具看着能干，门外契约不认。
DEFAULT_GOAL = (
    "写一个函数，把 '1h30m'、'45s'、'2h'、'10m' 这类字符串解析为总秒数（int）。"
    "空串或无法解析时返回 None。只输出代码。"
)

#: 工具描述**必须与被替换的真实能力一致**。
#: ⚠️ 第一版写的是"确定性拒绝作答的外部能力（探针专用）" —— 结果是模型读到描述就
#:    绕开了它（候选全是 `recall` / `finish`），一次都没触发拒绝 ⇒ 探针什么都没量到。
#:    这不是"模型不听话"，是**我把答案写在目录里了**。
REAL_DESC = ("会执行的编码 agent：同题多角度生成 3 份实现，自写 assert 用例，"
             "真跑后选通过最多的一份，返回可直接交付的代码。")


class Tap:
    """把真模型包一层，记下**每一次**调用看到的提示词与原文回答。

    ⚠️ 不记录就无从判断"提问变了没有"：日志里只有候选个数，没有候选内容。
    """

    def __init__(self, inner) -> None:                          # noqa: ANN001
        self.inner = inner
        self.calls: list[tuple[str, str]] = []

    def complete(self, messages, **kw):                         # noqa: ANN001, ANN003
        prompt = "\n".join(getattr(m, "content", "") for m in messages)
        out = self.inner.complete(messages, **kw)
        self.calls.append((prompt, out))
        return out


def _plan_of(raw: str) -> list[str] | None:
    """从一次回答里取出候选动作（`tool({args})`）。不是规划回答 ⇒ None。"""
    head, _ = split_answer(raw)
    for body in (head, raw):
        try:
            data = json.loads(body)
        except Exception:                                       # noqa: BLE001
            continue
        cands = data.get("candidates") if isinstance(data, dict) else None
        if isinstance(cands, list):
            return [f"{c.get('tool')}({json.dumps(c.get('args', {}), ensure_ascii=False)})"
                    for c in cands if isinstance(c, dict)]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal", default=DEFAULT_GOAL)
    ap.add_argument("--wall", type=float, default=120.0,
                    help="墙钟预算；默认与 bench 口径的 1x 档相同")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="v1_probe_reseek_"))
    import os
    os.environ["V1_STATE_DIR"] = str(tmp / "state")
    os.environ["V1_MAX_WALL_S"] = str(a.wall)
    st = cfg.settings()

    real = build_llm(st.llm)
    if type(real).__name__ == "StubLLM":
        print("❌ 这是离线桩，量不到「换形态」的效果。请配置 V1_LLM_* 再跑。")
        return 2
    tap = Tap(real)

    reg = ToolRegistry()
    mem = MemoryStore(st.paths.db, working_items=st.limits.working_memory_items)
    register_core_tools(reg, memory=mem, workspace=tmp / "ws")

    def _refuse(args: dict) -> str:
        # 拒绝条件与被替换的真实能力**同一个**：题面里没有『函数名必须是 X』。
        if "函数名必须是" not in str(args.get("task", "")):
            raise RuntimeError(f"外部 agent 未交付：{REFUSAL}")
        return "def parse_duration(s):\n    return None\n"

    reg.register(ToolSpec(
        name="agent:code_agent", params={"task": "题面原文", "deadline_s": "秒"},
        description=REAL_DESC,
        handler=_refuse, external=True, produces_answer=True))

    core = Core(settings=st, llm=tap, memory=mem, tools=reg, wire_external=False)
    out = core.run(a.goal, task_id="probe_reseek", scorer_sha="probe", suite_sha="probe")

    plans: list[list[str]] = []
    for i, (prompt, raw) in enumerate(tap.calls, 1):
        got = _plan_of(raw)
        if got is None:
            print(f"  调用 {i}: 非规划回答（{len(raw)} 字）")
            continue
        plans.append(got)
        has_reject = "被**拒绝**" in prompt
        print(f"  调用 {i}: 提示词含「被拒绝」段={has_reject} 候选={got}")

    print()
    print(f"■ status={out.status} reason={out.reason[:80]}")
    loop = out.stats.get("loop", {})
    print(f"   repeats_skipped={loop.get('repeats_skipped')} stalled={loop.get('stalled')} "
          f"attempted={loop.get('attempted_actions')}")
    print(f"   planner={out.stats.get('planner', {})}")
    print(f"   reseek={out.stats.get('planner', {}).get('reseek')}")
    distinct = len({tuple(p) for p in plans})
    print(f"   规划回答 {len(plans)} 次 / 互异 {distinct} 种")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
