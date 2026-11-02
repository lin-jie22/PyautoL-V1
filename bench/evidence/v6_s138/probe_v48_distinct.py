"""V48 离线探针：宽度 ≥ 2 ≠ 手段 ≥ 2（真 `Core`、零模型、零网络）。

判据见 `PREREG.md` 的 J1~J4 + §4.4。这里只回答四个问题：

  ① **两种形态能不能分开** —— 两个 `run_shell`（args 不同）与
     `run_shell`+`finish`，在 `n` 与 `by_tool_max_round` 上**完全相同**，
     只有 `distinct_tools_max_round` / `rounds_same_tool_wide` 不同；
  ② **`n=1` 不许算"一路多枪"** —— 每轮单候选时 `distinct` 恒 1，
     但 `rounds_same_tool_wide` 必须是 **0**（否则读数方向反了）；
  ③ **三枪一路**也要认出来（`n=3` 同 tool ⇒ `distinct=1`）；
  ④ **守恒**：`distinct_by_round[i] == len(per_round[i].tools)` 且
     `<= per_round[i].n`，`max` 与 `count` 两条路径算出同一个数。

⛔ 为什么不直接调 `_note_plan_round`：那样期望值是探针自己喂进去的，
   等于探针自己证明自己（AS20 的教训）。这里让内核真走
   规划 → 去重 → 截断 → 准入 → 执行，读数从那条路里出来。

⛔ 状态目录必须隔离（踩过：漏了 `V1_STATE_DIR`，生产库多出 2 条 episode）。
⛔ 吞掉 stdout：`■` 行带墙钟 ⇒ 两次跑不可能逐字节相同，而**可复算**是这份证据
   唯一的价值（两跑 sha 必须相同）。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="v48probe_")
os.environ["V1_STATE_DIR"] = str(Path(_TMP) / "state")
os.environ["V1_WORKSPACE"] = str(Path(_TMP) / "ws")
os.environ["V1_LLM_API_KEY"] = ""            # 强制 stub，绝不联网
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mw import config as cfg                  # noqa: E402
from mw.core import Core                      # noqa: E402
from mw.guard import Guard                    # noqa: E402
from mw.llm import StubLLM                    # noqa: E402

#: 与 V45~V47 逐字同一条真实目标（98 字 ⇒ 深通道、宽=3）。
GOAL_REAL = ("工作目录里有 sales.csv（列为 月份,区域,金额）。请计算每个区域的金额合计，"
             "按合计从高到低生成一张 markdown 表格，写入工作目录下的 report.md。"
             "表格两列为 区域|合计。")

#: 够长、且与目标有公共词 —— 让 `values` 与自检闸放行，从而当轮就收尾。
LONG_ANSWER = (
    "按区域汇总金额的结果如下，按合计从高到低排列：\n\n"
    "| 区域 | 合计 |\n|---|---|\n| 华北 | 2700 |\n| 华南 | 2000 |\n| 华东 | 700 |\n\n"
    "计算方式是对每个月、每个区域的金额逐条累加，再把同一区域的行相加；"
    "排序依据是合计值从大到小。若某个区域在不同月份出现多次，这些行的金额一样计入同一区域。"
    "上表已写入工作目录下的 report.md，两列分别是区域与合计，与目标要求的格式一致。"
)


def _core(llm) -> Core:
    s = cfg.settings()
    # ⚠️ `run_shell` 的开关**不在 `Limits` 里**，在 `Guard` 上（V43 那个洞同源）。
    #    探针要演"两个 run_shell"，必须显式开闸 —— 否则候选会被 guard 拦掉，
    #    读数变成"每轮 0 个可执行"而不是"一路两枪"。
    return Core(s, llm=llm, critique_ledger=None, score_ledger=None,
                caliber_sig="", memory_mode="raw", wire_external=False,
                guard=Guard(allow_shell=True))


class _SeqLLM(StubLLM):
    """按**轮**回放剧本的桩：只对**规划**调用推进序列。

    ⛔ 不能用 `StubLLM(scripts=[("candidates", …), …])` 演多轮：
       它的 `scripts` 是"关键词命中即用"（`llm.py` 首屏写着"顺序敏感"，
       指列表内先后，**不是**按调用次数消费）。同一句关键词会在**每一轮**
       命中同一条 ⇒ 第 2 轮永远拿不到交付答案。
       （第一版探针就栽在这里：S1 期望 2 轮，实测 3 轮全是第一份 plan。）

    ⛔ 判别键用规划 prompt 独有的 `互不相同`（`planner.py:531`），
       不用 `max_tokens`：200=预演那条虽然是事实，但它是个**旋钮**，
       旋钮一调判据就失效（纪律 33 的同宗陷阱）。
    """

    _PLAN_KEY = "互不相同"

    def __init__(self, plans: list[str]) -> None:
        super().__init__(scripts=[], default="{}")
        self._plans = list(plans)
        self._i = 0

    def complete(self, messages, *, max_tokens: int = 512,
                 temperature: float | None = None,
                 timeout_s: float | None = None) -> str:
        self.calls.append(list(messages))
        blob = "\n".join(m.content for m in messages)
        if self._PLAN_KEY in blob:
            i = min(self._i, len(self._plans) - 1)
            self._i += 1
            return self._plans[i]
        return self.default


def _plan(*items) -> str:
    return json.dumps({"candidates": list(items)}, ensure_ascii=False)


def _cand(tool: str, args: dict, intent: str = "") -> dict:
    return {"intent": intent or f"用 {tool}", "tool": tool,
            "args": args, "rationale": "探针"}


def _run(name: str, goal: str, scripts: list[str], expect: dict) -> list[str]:
    """跑一景，返回不符合预期的行（空 = 全对）。

    `scripts` 按轮给：第 1 轮用 `scripts[0]`，之后恒用最后一个
    （`_SeqLLM` 只对规划调用推进序列）。
    """
    llm = _SeqLLM(scripts)
    core = _core(llm)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = core.run(goal, budget_factor=1.0)
    plan = out.stats["plan"]
    got = {"plan": plan, "status": out.status}

    bad: list[str] = []
    for k, want in expect.items():
        if k == "plan":
            for kk, vv in want.items():
                if isinstance(vv, str) and vv.startswith(">"):
                    if not (isinstance(plan.get(kk), int) and plan[kk] > int(vv[1:])):
                        bad.append(f"plan.{kk} 实测={plan.get(kk)!r} 期望 {vv}")
                    continue
                if plan.get(kk) != vv:
                    bad.append(f"plan.{kk} 实测={plan.get(kk)!r} 期望={vv!r}")
        elif got.get(k) != want:
            bad.append(f"{k} 实测={got.get(k)!r} 期望={want!r}")

    # ── §4.4 守恒：每一景都断言，与具体数字无关 ─────────────────────────
    dbr, pr = plan["distinct_by_round"], plan["per_round"]
    if len(dbr) != plan["rounds"]:
        bad.append(f"len(distinct_by_round)={len(dbr)} != rounds={plan['rounds']}")
    if len(pr) != plan["rounds"]:
        bad.append(f"len(per_round)={len(pr)} != rounds={plan['rounds']}")
    for i, (d, r) in enumerate(zip(dbr, pr)):
        if d != len(r["tools"]):
            bad.append(f"轮{i}: distinct_by_round={d} != len(per_round.tools)="
                       f"{len(r['tools'])}")
        if d > r["n"]:
            bad.append(f"轮{i}: distinct={d} > n={r['n']}")
        if r["n"] == 0 and d != 0:
            bad.append(f"轮{i}: n=0 但 distinct={d}（分母为 0 时种类也必须是 0）")
    if plan["distinct_tools_max_round"] != (max(dbr) if dbr else 0):
        bad.append(f"distinct_tools_max_round={plan['distinct_tools_max_round']}"
                   f" != max(distinct_by_round)={max(dbr) if dbr else 0}")
    # ⭐ 两个数从同一处产生、用**两种算法**对上 —— 单独任一个都能自洽地撒谎。
    _same = sum(1 for i, r in enumerate(pr) if r["n"] >= 2 and dbr[i] == 1)
    if plan["rounds_same_tool_wide"] != _same:
        bad.append(f"rounds_same_tool_wide={plan['rounds_same_tool_wide']}"
                   f" != 逐轮复算={_same}")
    if plan["cands_total"] != sum(r["n"] for r in pr):
        bad.append(f"cands_total={plan['cands_total']} != Σper_round.n")

    print(f"{'✅' if not bad else '❌'} {name}")
    print(f"   rounds={plan['rounds']} cands_total={plan['cands_total']}"
          f" by_tool_max_round={plan['by_tool_max_round']}"
          f" distinct_max={plan['distinct_tools_max_round']}"
          f" same_tool_wide={plan['rounds_same_tool_wide']}")
    print(f"   per_round = {json.dumps(pr, ensure_ascii=False)}")
    print(f"   distinct  = {json.dumps(dbr)}  produces = "
          f"{json.dumps(plan['produces_by_round'])}")
    for b in bad:
        print(f"   ⛔ {b}")
    return bad


def main() -> int:
    allbad: list[str] = []

    # ── S1 **同一路打两枪**：两个 `run_shell`，args 不同 ───────────────────
    # `_extract` 的 sig 是 `tool|sorted(args)` ⇒ args 不同就都放行。
    # 这正是 V47 on 臂第 2 轮的真实形状（`{"n":2,"tools":{"run_shell":2}}`）。
    # ⛔ 这一景的两个数必须**同时**成立：`by_tool_max_round=2`（宽）而
    #    `distinct_tools_max_round=1`（不宽）—— 后者是本轮新增的那一层。
    allbad += _run(
        "S1 两个 run_shell（args 不同）⇒ n=2 / distinct=1 ⇒ 认作一路两枪",
        GOAL_REAL,
        [_plan(_cand("run_shell", {"command": "echo a"}, "看目录"),
               _cand("run_shell", {"command": "echo b"}, "看文件")),
         _plan(_cand("finish", {"answer": LONG_ANSWER}, "交付答案"))],
        {"plan": {"distinct_tools_max_round": 1, "rounds_same_tool_wide": 1,
                  "by_tool_max_round": 2, "distinct_by_round": [1, 1],
                  "per_round": [{"n": 2, "tools": {"run_shell": 2}},
                                {"n": 1, "tools": {"finish": 1}}]}})

    # ── S2 **两路**：`finish` + `run_shell`（V47 on 臂第 4 轮的形状）──────
    # 与 S1 在 `n` 和 `by_tool_max_round` 上**完全相同**（都是 2 和 2 的上限），
    # 差别只在 `distinct` —— 这就是"宽度说不清手段"的现场。
    allbad += _run(
        "S2 finish + run_shell ⇒ n=2 但 distinct=2 ⇒ 不算一路多枪",
        GOAL_REAL,
        [_plan(_cand("finish", {"answer": LONG_ANSWER}, "交付答案"),
               _cand("run_shell", {"command": "echo done"}, "顺手看一眼"))],
        {"plan": {"distinct_tools_max_round": 2, "rounds_same_tool_wide": 0,
                  "distinct_by_round": [2],
                  "per_round": [{"n": 2, "tools": {"finish": 1, "run_shell": 1}}]}})

    # ── S3 **三枪一路**：三个 `recall`，query 不同（宽度上限 3）─────────────
    allbad += _run(
        "S3 三个 recall（query 不同）⇒ n=3 / distinct=1 ⇒ 仍然是一路",
        GOAL_REAL,
        [_plan(_cand("recall", {"query": "sales"}, "查1"),
               _cand("recall", {"query": "region"}, "查2"),
               _cand("recall", {"query": "合计"}, "查3")),
         _plan(_cand("finish", {"answer": LONG_ANSWER}, "交付答案"))],
        {"plan": {"distinct_tools_max_round": 1, "rounds_same_tool_wide": 1,
                  "by_tool_max_round": 3, "distinct_by_round": [1, 1]}})

    # ── S4 **每轮单候选**：`distinct` 全 1，但"一路多枪"必须是 0 ────────────
    # ⛔ 这一景是**反向校验**：没有 `n>=2` 那道闸，它会被读成
    #    "每轮都是同一路打枪"，而真相是"每轮只想到一个动作"。
    allbad += _run(
        "S4 每轮单候选 ⇒ distinct 全 1 而 same_tool_wide=0（n>=2 那道闸）",
        GOAL_REAL,
        [_plan(_cand("recall", {"query": "sales"}, "先查记忆")),
         _plan(_cand("finish", {"answer": LONG_ANSWER}, "交付答案"))],
        {"plan": {"distinct_tools_max_round": 1, "rounds_same_tool_wide": 0,
                  "distinct_by_round": [1, 1]}})

    print()
    if allbad:
        print(f"结论: {len(allbad)} 项不符")
        return 1
    print("结论: 全部符合（宽度与手段种类已分列，且 n=1 不冒充一路多枪）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
