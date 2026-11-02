"""V45 离线探针：候选宽度读数（真 `Core`、零模型、零网络）。

判据见 `PREREG.md` 的 J3 / J4。这里只回答三个问题：

  ① **形状对不对** —— 给定一份手造的 plan，`per_round` / `by_tool` /
     `by_tool_max_round` / `produces_by_round` 是不是逐字对得上；
  ② **读数能不能非零** —— 三个 width 键与 `by_tool_max_round` 都必须有
     一条景能让它 > 0（"恒为 0 的读数"与"没接线"同形，纪律 15）；
  ③ **它是不是真的恒存在** —— 每个景都断言 `rounds` 与 `len(per_round)` 相等。

⛔ 为什么用真 `Core.run()` 而不是直接调 `_note_plan_round`：
   后者的 `rounds` 是探针自己数出来的，等于**探针自己证明自己**
   （AS20 那次的教训：探针把洞挡住，断言全绿而洞还在）。
   这里让内核真的走规划 → 截断 → 分叉，读数从那条路里出来。

⛔ 状态目录必须隔离（踩过一次：漏了 `V1_STATE_DIR`，生产库多出 2 条 episode）。
⛔ 吞掉 stdout：`■` 行带墙钟 ⇒ 两次跑不可能逐字节相同，而**可复算**
   是这份证据唯一的价值（J3 要求同一份代码两次跑 sha 相同）。

⚠️ 第一版探针的期望值写错了三处，如实记在 RESULTS 里：
   ① S1/S2/S3 我以为只跑一轮 —— 实际 `values` 会否决"2 字的答案"
      （目标 98 字要求展开）⇒ 内核重规划到第 3 轮才停。**这不是 bug**，
      是我的期望错了：读数的分母是"整次运行"，不是"第 1 轮"。
      修法：给一个够长的答案让它**第 1 轮就交付**，这样"单轮"是装置给的，
      不是我在期望里假设的。
   ② S4（全未注册工具）我以为 `rounds=0` —— 实际规划降级走了 `_fallback`，
      产出了 `recall` + `finish` ⇒ `rounds=2`。这本身是读数：
      **"模型全灭"不等于"这一轮没有候选"**，兜底会补。判据随之改成
      "`offered` 全被记成 unknown"，不再假设 `rounds=0`。
   ③ 守恒式 `offered == cands_total + dropped_*` 是**错的**：
      `cands_total` 里含 `_fallback` 产出的候选（那不经 `_extract`，
      不在 `offered` 里）。改成强弱两条：弱式（丢的不能超过给的）恒断言，
      强式（`examined == offered_dropped`）只在"没降级"的景里断言。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="v45probe_")
os.environ["V1_STATE_DIR"] = str(Path(_TMP) / "state")
os.environ["V1_WORKSPACE"] = str(Path(_TMP) / "ws")
os.environ["V1_LLM_API_KEY"] = ""            # 强制 stub，绝不联网
import sys                                   # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mw import config as cfg                  # noqa: E402
from mw.core import Core                      # noqa: E402
from mw.llm import StubLLM                    # noqa: E402

#: 第一百三十三轮用的那条真实目标。98 字 ⇒ `looks_complex` 为真（3 个分隔符）
#: ⇒ 宽=3。⚠️ 它同时会让 `values` 要求"答案得展开"：
#: 答案短于某个比例会被否决 —— 所以下面的答案都写长。
GOAL_REAL = ("工作目录里有 sales.csv（列为 月份,区域,金额）。请计算每个区域的金额合计，"
             "按合计从高到低生成一张 markdown 表格，写入工作目录下的 report.md。"
             "表格两列为 区域|合计。")
GOAL_SHORT = "把 1+1 算出来。"

#: 够长、且与目标有公共词（`区域`/`合计`/`金额`）的交付文本 ——
#: 目的是让 `values` 与自检闸都放行，从而**第 1 轮就收尾**（单轮是可观测的前提）。
LONG_ANSWER = (
    "按区域汇总金额的结果如下，按合计从高到低排列：\n\n"
    "| 区域 | 合计 |\n|---|---|\n| 华北 | 2700 |\n| 华南 | 2000 |\n| 华东 | 700 |\n\n"
    "计算方式是对每个月、每个区域的金额逐条累加，再把同一区域的行相加；"
    "排序依据是合计值从大到小。若某个区域在不同月份出现多次，这些行的金额一样计入同一区域。"
    "上表已写入工作目录下的 report.md，两列分别是区域与合计，与目标要求的格式一致。"
)


def _core(llm) -> Core:
    """真 `Core`。`wire_external=True` 才能让 `agent:code_agent` 出现在工具表里。"""
    return Core(cfg.settings(), llm=llm, critique_ledger=None, score_ledger=None,
                caliber_sig="", memory_mode="raw", wire_external=True)


def _plan(*items) -> str:
    return json.dumps({"candidates": list(items)}, ensure_ascii=False)


def _cand(tool: str, args: dict, intent: str = "") -> dict:
    return {"intent": intent or f"用 {tool}", "tool": tool,
            "args": args, "rationale": "探针"}


def _run(name: str, goal: str, script: str, expect: dict,
         *, strong_conservation: bool = False) -> list[str]:
    """跑一景，返回不符合预期的行（空 = 全对）。"""
    llm = StubLLM(scripts=[("candidates", script)], default=script)
    core = _core(llm)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = core.run(goal, budget_factor=1.0)
    plan = out.stats["plan"]
    width = out.stats["planner"]["width"]
    #: 派生量（每一景都算，不是某一景的特例）：
    #   "模型给的东西**全部**被记成未知工具" —— 全灭景要断言的正是这个关系，
    #   而不是 `offered` 的具体数字（它按轮累计，与读数无关地随轮数变化）。
    got = {"plan": plan, "width": dict(width), "status": out.status}
    got["width"]["_all_unknown"] = bool(width["offered"]) and (
        width["dropped_unknown"] == width["offered"])

    bad: list[str] = []
    for k, want in expect.items():
        if k in ("plan", "width"):
            for kk, vv in want.items():
                if got[k].get(kk) != vv:
                    bad.append(f"{k}.{kk} 实测={got[k].get(kk)!r} 期望={vv!r}")
        elif got.get(k) != want:
            bad.append(f"{k} 实测={got.get(k)!r} 期望={want!r}")

    # ③ 恒存在：这三条**每一景**都要成立（PREREG J1 的内核版）
    if len(plan["per_round"]) != plan["rounds"]:
        bad.append(f"len(per_round)={len(plan['per_round'])} != rounds={plan['rounds']}")
    if plan["cands_total"] != sum(r["n"] for r in plan["per_round"]):
        bad.append(f"cands_total={plan['cands_total']} != Σper_round.n")
    if sum(plan["by_tool"].values()) != plan["cands_total"]:
        bad.append(f"Σby_tool={sum(plan['by_tool'].values())} != cands_total={plan['cands_total']}")
    # 截断损失必须是"交出"与"进执行器"之差 —— 否则它就是另一个数
    if plan["examined_total"] - plan["cands_total"] != plan["truncated_total"]:
        bad.append(f"examined({plan['examined_total']}) - cands({plan['cands_total']})"
                   f" != truncated({plan['truncated_total']})")
    # 弱守恒（恒真，除非有漏记）：丢掉的不能超过给的
    dropped = width["dropped_dup"] + width["dropped_unknown"] + width["dropped_shape"]
    if dropped > width["offered"]:
        bad.append(f"丢掉 {dropped} > 给了 {width['offered']}（漏记）")
    # 强守恒（只在"没降级走兜底"的景里成立）：
    #   `_extract` 的输入 = 交出的步骤 + 被丢的
    if strong_conservation and width["offered"] - dropped != plan["examined_total"]:
        bad.append(f"强守恒破：offered({width['offered']}) - dropped({dropped})"
                   f" != examined({plan['examined_total']})")

    print(f"{'✅' if not bad else '❌'} {name}")
    print(f"   plan  = {json.dumps(plan, ensure_ascii=False)}")
    print(f"   width = {json.dumps(width, ensure_ascii=False)}")
    for b in bad:
        print(f"   ⛔ {b}")
    return bad


def main() -> int:
    allbad: list[str] = []

    # ── S1 真实形态：5 条候选（3 可用 + 1 重复 + 1 未注册工具）──────────────
    # 这正是 V43 现场的形态：模型给得比 `n=3` 多，其中有重复、有它想要的
    # 不存在的工具。读数的价值在于把这三件事**分开**。
    allbad += _run(
        "S1 真实形态 5 条 ⇒ 3 可用 / 1 重复 / 1 未注册（第 1 轮就交付）",
        GOAL_REAL,
        _plan(_cand("finish", {"answer": LONG_ANSWER}, "交付答案"),
              _cand("run_shell", {"cmd": "echo hi"}, "跑一段"),
              _cand("agent:code_agent", {"task": "写个函数"}, "问外援"),
              _cand("finish", {"answer": LONG_ANSWER}, "重复交付"),   # ← sig 与第 1 条相同
              _cand("browser", {}, "想了没注册")),                     # ← 未注册
        {"width": {"offered": 5, "dropped_dup": 1, "dropped_unknown": 1,
                   "dropped_shape": 0},
         # 第 1 轮 3 个候选：finish / run_shell / agent:code_agent
         "plan": {"rounds": 1, "cands_total": 3, "by_tool_max_round": 1,
                  "examined_total": 3, "truncated_total": 0,
                  "produces_by_round": [2],          # finish + code_agent
                  "per_round": [{"n": 3, "tools": {"finish": 1, "run_shell": 1,
                                                   "agent:code_agent": 1}}]}},
        strong_conservation=True)

    # ── S2 同一 tool 占宽度：两个 **args 不同** 的 finish ⇒ 去重管不住 ──────
    # 这是 `by_tool_max_round` 存在的**唯一**理由：
    #   `_extract` 按 `tool|sorted(args)` 去重 ⇒ 两个 args 不同的 finish
    #   **都能活下来**，于是"宽度被同一个 tool 霸占"成了可能。
    #   ⛔ 若这里读到 1，说明这个字段永远不可能 >1 ⇒ 它是死字段，必须撤。
    allbad += _run(
        "S2 两个 args 不同的 finish ⇒ by_tool_max_round=2",
        GOAL_REAL,
        _plan(_cand("finish", {"answer": LONG_ANSWER}, "第一种交付"),
              _cand("finish", {"answer": LONG_ANSWER + "（第二版）"}, "第二种交付"),
              _cand("run_shell", {"cmd": "echo hi"}, "跑一段")),
        {"plan": {"rounds": 1, "cands_total": 3, "by_tool_max_round": 2,
                  "examined_total": 3, "truncated_total": 0,
                  "produces_by_round": [2],
                  "by_tool": {"finish": 2, "run_shell": 1}}},
        strong_conservation=True)

    # ── S3 非 dict 条目：格式问题必须与"未注册工具"分开 ────────────────────
    allbad += _run(
        "S3 夹一条字符串 ⇒ dropped_shape=1（不算进 unknown）",
        GOAL_REAL,
        json.dumps({"candidates": [
            _cand("finish", {"answer": LONG_ANSWER}),
            "这是一个字符串，不是对象",
            _cand("run_shell", {"cmd": "echo hi"}),
        ]}, ensure_ascii=False),
        {"width": {"offered": 3, "dropped_shape": 1, "dropped_unknown": 0,
                   "dropped_dup": 0},
         "plan": {"rounds": 1, "cands_total": 2, "by_tool_max_round": 1,
                  "examined_total": 2, "truncated_total": 0}},
        strong_conservation=True)

    # ── S4 全灭：3 条全是未注册工具 ⇒ 规划降级，兜底补两条候选 ──────────────
    # ⚠️ 这一景验的是"**模型全灭 ≠ 这一轮没有候选**"：
    #    实测 `rounds=2`，两轮各 1 条来自 `_fallback`（`recall` / `finish`）。
    #    ⛔ 所以这里**不**断言 `rounds=0` —— 那是我第一版写错的期望。
    allbad += _run(
        "S4 三条全是未注册工具 ⇒ unknown 全记，兜底仍交出候选",
        GOAL_REAL,
        _plan(_cand("browser", {}), _cand("cursor", {}), _cand("vim", {})),
        # ⚠️ 不断言 `offered` 的具体数字：它**按轮累计**，而这一景跑了 2 轮
        #    （实测 offered=6 = 3 条 × 2 轮）。写死 6 会让这条断言依赖
        #    "刚好两轮"这个与读数无关的事实（判据分辨率高于装置，纪律 24）。
        #    要断言的是**关系**：所有给出的条目都被记成 unknown。
        {"width": {"dropped_dup": 0, "dropped_shape": 0, "_all_unknown": True},
         "plan": {"by_tool_max_round": 1, "cands_total": 2,
                  "produces_by_round": [0, 1]}})

    # ── S5 短目标 ⇒ 走"快通道"，宽=1 ⇒ 模型给 3 条、执行器只拿 1 条 ─────────
    # ⭐ 这一景是预注册 §5.1"预期会被打脸"那条的**正面**形态：
    #    我原以为 `offered == cands_total`，实测**不等** —— 截断是真实的宽度损失，
    #    而旧读数（日志里的 `候选=1`）完全看不见它。
    allbad += _run(
        "S5 短目标走快通道 ⇒ offered=3 但 cands_total=1（截断看得见了）",
        GOAL_SHORT,
        _plan(_cand("finish", {"answer": "2"}),
              _cand("run_shell", {"cmd": "echo hi"}),
              _cand("recall", {"q": "1+1"})),
        {"width": {"offered": 3},
         "plan": {"rounds": 1, "cands_total": 1, "by_tool_max_round": 1,
                  "examined_total": 3, "truncated_total": 2,
                  "per_round": [{"n": 1, "tools": {"finish": 1}}]}},
        strong_conservation=True)

    print()
    if allbad:
        print(f"结论: {len(allbad)} 项不符")
        return 1
    print("结论: 五景全部符合预注册（形状、守恒式、恒存在关系、截断恒等式都成立）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
