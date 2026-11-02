"""V46 离线探针：`rescued` 拆两档（真 `Core`、零模型、零网络）。

判据见 `PREREG.md` 的 J1 / J2 / J5。这里只回答四个问题：

  ① **形状对不对** —— 交付救回记 `rescued_answer`、步骤救回记 `rescued_step`，
     两档**互不冒充**（V45 的病：B 臂的 PASS 全靠让位而记 `rescued=0`）；
  ② **能不能非零** —— 两档都必须有**一条景**能让它 > 0
     （恒为 0 的计数器与"没接线"同形，纪律 15）；
  ③ **守恒** —— `rescued == rescued_answer + rescued_step`（三景全成立）；
     ⚠️ 守恒是**必要**条件不是通电证明：两档同时归零时它当然成立
     （撤回态 AZ109 实测：撤掉步骤那一档，守恒仍绿）；
  ④ **判决入口不被过程量混入** —— `rescue_rate` 的分子仍是 `rescued_answer`。

⛔ 为什么用真 `Core.run()` 而不是直接调计数器：后者等于**探针自己证明自己**
   （AS20 的教训）。这里让内核真的走「交付被打回 → 让位 → 下一个候选」。
⛔ 状态目录必须隔离（踩过一次：漏了 `V1_STATE_DIR`，生产库多出 2 条 episode）。
⛔ 吞掉 stdout：`■` 行带墙钟 ⇒ 两次跑不可能逐字节相同，而**可复算**
   是这份证据唯一的价值。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="v46probe_")
os.environ["V1_STATE_DIR"] = str(Path(_TMP) / "state")
os.environ["V1_WORKSPACE"] = str(Path(_TMP) / "ws")
os.environ["V1_LLM_API_KEY"] = ""            # 强制 stub，绝不联网
#: ⚠️ 必须在 `import cfg` **之前**设：`cfg.settings()` 在 import 时求值
#:    （MEMORY 硬约束：命令行参数只表达意图，真正生效的是 env）。
os.environ["V1_CAND_FAILOVER"] = "1"
import sys                                   # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import run as _run_mod                        # noqa: E402
from mw import config as cfg                  # noqa: E402
from mw.core import Core                      # noqa: E402
from mw.llm import StubLLM                    # noqa: E402

#: 与 AZ105 同一条目标：它会让 `values` 要求"答案得展开"，所以答案都写长。
GOAL = ("写一个函数，函数名必须是 flatten，把嵌套列表压平，只输出代码。"
        "不允许使用递归，也不能引入任何第三方库，并且要处理空列表的情况，"
        "还要保证结果里保留原始顺序，最后不要写任何解释文字。")
#: 过全部判据的答案（函数名对、可编译、结构检查过）。
OK = ("```python\ndef flatten(xs):\n    out = []\n    for x in xs:\n"
      "        if isinstance(x, list):\n            out.extend(flatten(x))\n"
      "        else:\n            out.append(x)\n    return out\n```")
#: ⚠️ "坏答案"必须**过得了规划器、过不了自检**（可编译 + 函数名不是 flatten）。
#    空答案会在 `propose()` 的 `blank` 过滤里被吃掉，根本到不了执行器。
BAD = "```python\ndef other(xs):\n    return xs\n```"


def _cand(tool: str, args: dict, intent: str) -> dict:
    return {"intent": intent, "tool": tool, "args": args, "rationale": "探针"}


def _plan(*items: dict) -> str:
    return json.dumps({"candidates": list(items)}, ensure_ascii=False)


SCENES = {
    #: 交付救回：第 1 个 `finish` 被自检打回 ⇒ 让位 ⇒ 第 2 个 `finish` 交付。
    "D 交付救回": _plan(_cand("finish", {"answer": BAD}, "先交一版"),
                       _cand("finish", {"answer": OK}, "交合法实现")),
    #: 步骤救回：第 1 个 `finish` 被打回 ⇒ 让位 ⇒ `recall` 成功（非交付动作）。
    #: ⭐ 这一景就是 V45 B 臂的离线同构：救回的是**一步**，不是一次交付。
    "S 步骤救回": _plan(_cand("finish", {"answer": BAD}, "先交一版"),
                       _cand("recall", {"query": "压平"}, "先查一下")),
    #: 无弹药：单候选 ⇒ 与 `0` 臂同路径，两档都必须为 0（开关不许泄漏）。
    "ONE 单候选": _plan(_cand("finish", {"answer": BAD}, "只有一条")),
}


def _run_scene(name: str, script: str) -> dict:
    """跑一景，返回读数 + **第 1 轮真的 dispatch 了哪些工具**。"""
    llm = StubLLM(scripts=[("candidates", script)], default=script)
    core = Core(cfg.settings(), llm=llm, memory_mode="raw")
    hits: list[str] = []
    inner = core.tools

    class _Reg:
        """只读观测点。⛔ 只记工具名**不够**：三候选可能都是 `finish`。"""

        def __getattr__(self, k):
            return getattr(inner, k)

        def dispatch(self, tool, args):
            hits.append(str(tool))
            return inner.dispatch(tool, args)

    core.tools = _Reg()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = core.run(GOAL, budget_factor=1.0)
    fo = dict(out.stats["failover"])
    return {"name": name, "fo": fo, "hits": hits, "status": out.status,
            "verdict": out.verdict,
            "answer_is_ok": out.answer.strip().startswith("```python\ndef flatten")}


def main() -> int:
    got = [_run_scene(n, s) for n, s in SCENES.items()]
    by = {g["name"]: g for g in got}
    bad: list[str] = []

    for g in got:
        fo = g["fo"]
        print(f"[{g['name']}] status={g['status']} verdict={g['verdict']}")
        print(f"    used={fo['used']} 救回: 答案={fo['rescued_answer']} "
              f"步骤={fo['rescued_step']} 合计={fo['rescued']} "
              f"无弹药={fo['no_more']}")
        print(f"    第 1 轮 dispatch={g['hits']}")
        # ③ 守恒：三景**每一景**都断言（不是某一景的特例）。
        if fo["rescued"] != fo["rescued_answer"] + fo["rescued_step"]:
            bad.append(f"{g['name']}: 守恒破了 "
                       f"{fo['rescued']} != {fo['rescued_answer']}+{fo['rescued_step']}")

    d, s_, one = by["D 交付救回"]["fo"], by["S 步骤救回"]["fo"], by["ONE 单候选"]["fo"]
    # ① 交付救回：答案那一档必须为 1，步骤那一档必须为 0（互不冒充）。
    if not (d["used"] == 1 and d["rescued_answer"] == 1 and d["rescued_step"] == 0):
        bad.append(f"D 期望 used=1/answer=1/step=0，实测 {d}")
    # ② 步骤救回：反过来，而且必须**真的 dispatch 了** `recall`。
    if not (s_["used"] == 1 and s_["rescued_step"] == 1 and s_["rescued_answer"] == 0):
        bad.append(f"S 期望 used=1/step=1/answer=0，实测 {s_}")
    if "recall" not in by["S 步骤救回"]["hits"]:
        bad.append(f"S 的 recall 根本没被执行：hits={by['S 步骤救回']['hits']}")
    # ④ 无弹药：两档都 0。
    if not (one["rescued_answer"] == 0 and one["rescued_step"] == 0):
        bad.append(f"ONE 期望两档都 0，实测 {one}")
    # ⑤ 判决入口：分子只能是 answer 那一档。
    line_a = _run_mod._kfo_line({"used": 2, "rescued": 2, "rescued_answer": 1,
                                 "rescued_step": 1, "cf_extra": 0, "finish": 1,
                                 "external": 0, "no_more": 0, "switch": 1})
    line_b = _run_mod._kfo_line({"used": 2, "rescued": 2, "rescued_answer": 0,
                                 "rescued_step": 2, "cf_extra": 0, "finish": 0,
                                 "external": 0, "no_more": 0, "switch": 1})
    print(f"[R 判决入口] rescue_rate 分子=答案档 ⇒ A(1/2)={line_a['rescue_rate']} "
          f"B(0/2)={line_b['rescue_rate']}")
    if line_a["rescue_rate"] != 0.5 or line_b["rescue_rate"] != 0.0:
        bad.append(f"rescue_rate 混入了步骤档：{line_a['rescue_rate']}/"
                   f"{line_b['rescue_rate']}")

    print()
    if bad:
        for x in bad:
            print(f"✘ {x}")
        print("判据 J1/J2/J7：FAIL")
        return 1
    print("判据 J1/J2/J7：PASS（两档互不冒充 · 都能非零 · 守恒 · 率只认结果量）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
