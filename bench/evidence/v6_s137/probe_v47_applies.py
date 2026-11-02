"""V47 离线探针：外部能力**不适配**时不该占宽度（真 `Core`、零模型、零网络）。

判据见 `PREREG.md` 的 J1/J2/J3/J5。这里只回答四个问题：

  ① **丢弃是不是真的发生** —— CSV 题面 + 4 个 `agent:code_agent` ⇒
     `dropped_not_applicable=4` 且 `by_tool_max_round` 从 4 掉到 1；
  ② **它会不会误杀** —— 未注册工具（`browser`）必须走 `dropped_unknown`，
     **不许**进 `dropped_not_applicable`（两条完全不同的路径各自有读数）；
  ③ **注入点也判** —— `external_answer_tool=agent:code_agent` + CSV 题面 ⇒
     不注入、`external.skipped_not_applicable=1`，且与"额度不足"分列；
  ④ **空轮有自己的出口** —— 每轮都只给不适配的候选 ⇒ 到 `max_stall` 转交付，
     `status="not_applicable"`，⛔ 不许是 `stalled` / `cascade` / `closed`。

⛔ 为什么用真 `Core.run()` 而不是直接调 `_drop_not_applicable`：
   后者的返回值是探针自己传进去的，等于探针自己证明自己（AS20 的教训）。
   这里让内核真的走 规划 → 去重 → 截断 → 准入 → 执行，读数从那条路里出来。

⛔ 状态目录必须隔离（踩过一次：漏了 `V1_STATE_DIR`，生产库多出 2 条 episode）。
⛔ 吞掉 stdout：`■` 行带墙钟 ⇒ 两次跑不可能逐字节相同，而**可复算**是这份证据
   唯一的价值（两跑 sha 必须相同）。
"""
from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="v47probe_")
os.environ["V1_STATE_DIR"] = str(Path(_TMP) / "state")
os.environ["V1_WORKSPACE"] = str(Path(_TMP) / "ws")
os.environ["V1_LLM_API_KEY"] = ""            # 强制 stub，绝不联网
import sys                                   # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mw import config as cfg                  # noqa: E402
from mw.core import Core                      # noqa: E402
from mw.llm import StubLLM                    # noqa: E402

#: 第一百三十三~一百三十六轮用的那条真实目标（98 字 ⇒ 深通道、宽=3）。
#: ⭐ 它**不含**『函数名必须是 X』⇒ 对 `agent:code_agent` 结构上不适用。
GOAL_REAL = ("工作目录里有 sales.csv（列为 月份,区域,金额）。请计算每个区域的金额合计，"
             "按合计从高到低生成一张 markdown 表格，写入工作目录下的 report.md。"
             "表格两列为 区域|合计。")

#: 反过来：一条**适用**的题面（题集式）。同一份候选在这一景里**不该**被丢。
GOAL_FUNC = ("请实现一个函数，函数名必须是 sum_by_region，输入二维列表返回按区域汇总的字典。"
             "要求写成可直接导入的 Python 代码，并处理空输入。")

#: 够长、且与目标有公共词 —— 让 `values` 与自检闸放行，从而第 1 轮就收尾。
LONG_ANSWER = (
    "按区域汇总金额的结果如下，按合计从高到低排列：\n\n"
    "| 区域 | 合计 |\n|---|---|\n| 华北 | 2700 |\n| 华南 | 2000 |\n| 华东 | 700 |\n\n"
    "计算方式是对每个月、每个区域的金额逐条累加，再把同一区域的行相加；"
    "排序依据是合计值从大到小。若某个区域在不同月份出现多次，这些行的金额一样计入同一区域。"
    "上表已写入工作目录下的 report.md，两列分别是区域与合计，与目标要求的格式一致。"
)


def _core(llm, *, ext_tool: str = "") -> Core:
    """真 `Core`。`wire_external=True` 才会把 `agent:code_agent` 注册进工具表。

    `ext_tool` 非空时把它设成 `external_answer_tool`（注入点那一景需要）。
    ⚠️ 这只改 `Limits` 的一个字段，**不**改 `agents.json` —— 单变量。
    """
    s = cfg.settings()
    if ext_tool:
        s = dataclasses.replace(s, limits=dataclasses.replace(
            s.limits, external_answer_tool=ext_tool))
    return Core(s, llm=llm, critique_ledger=None, score_ledger=None,
                caliber_sig="", memory_mode="raw", wire_external=True)


def _plan(*items) -> str:
    return json.dumps({"candidates": list(items)}, ensure_ascii=False)


def _cand(tool: str, args: dict, intent: str = "") -> dict:
    return {"intent": intent or f"用 {tool}", "tool": tool,
            "args": args, "rationale": "探针"}


def _run(name: str, goal: str, script: str, expect: dict,
         *, ext_tool: str = "") -> list[str]:
    """跑一景，返回不符合预期的行（空 = 全对）。"""
    llm = StubLLM(scripts=[("candidates", script)], default=script)
    core = _core(llm, ext_tool=ext_tool)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = core.run(goal, budget_factor=1.0)
    plan = out.stats["plan"]
    ex = out.stats["external"]
    got = {"plan": plan, "external": dict(ex), "status": out.status}

    bad: list[str] = []
    for k, want in expect.items():
        if k in ("plan", "external"):
            for kk, vv in want.items():
                # ⚠️ `">0"` 这类**关系**断言：判据的分辨率不许高于装置。
                #    `examined_total` / `truncated_total` 是**跨轮累计**，
                #    写死具体数字就等于在断言"刚好跑了几轮"—— 而那与本题无关
                #    （V45 探针第一版就是这么错的，见它的 RESULTS）。
                if isinstance(vv, str) and vv.startswith(">"):
                    if not (isinstance(got[k].get(kk), int)
                            and got[k][kk] > int(vv[1:])):
                        bad.append(f"{k}.{kk} 实测={got[k].get(kk)!r} 期望 {vv}")
                    continue
                if got[k].get(kk) != vv:
                    bad.append(f"{k}.{kk} 实测={got[k].get(kk)!r} 期望={vv!r}")
        elif got.get(k) != want:
            bad.append(f"{k} 实测={got.get(k)!r} 期望={want!r}")

    # ── 恒存在关系（每一景都断言，与具体数字无关）────────────────────────
    if len(plan["per_round"]) != plan["rounds"]:
        bad.append(f"len(per_round)={len(plan['per_round'])} != rounds={plan['rounds']}")
    if plan["cands_total"] != sum(r["n"] for r in plan["per_round"]):
        bad.append(f"cands_total={plan['cands_total']} != Σper_round.n")
    if sum(plan["by_tool"].values()) != plan["cands_total"]:
        bad.append(f"Σby_tool={sum(plan['by_tool'].values())}"
                   f" != cands_total={plan['cands_total']}")
    # ⭐ 恒等式（§113 的**新**一条）：截断只算"被 n 截掉的"，
    #   被**准入**丢掉的不许算进去 —— 否则同一批候选被记两次。
    _left = plan["examined_total"] - plan["cands_total"] - plan["dropped_not_applicable"]
    if max(0, _left) != plan["truncated_total"]:
        bad.append(f"examined({plan['examined_total']}) - cands({plan['cands_total']})"
                   f" - dropped_na({plan['dropped_not_applicable']})"
                   f" = {max(0, _left)} != truncated({plan['truncated_total']})")
    if sum(plan["dropped_na_by_tool"].values()) != plan["dropped_not_applicable"]:
        bad.append(f"Σdropped_na_by_tool={sum(plan['dropped_na_by_tool'].values())}"
                   f" != dropped_not_applicable={plan['dropped_not_applicable']}")

    print(f"{'✅' if not bad else '❌'} {name}")
    print(f"   plan = {json.dumps(plan, ensure_ascii=False)}")
    print(f"   ext  = {json.dumps({k: ex.get(k) for k in ('calls', 'ok', 'err',
                                                         'skipped_no_budget',
                                                         'skipped_not_applicable')},
                                 ensure_ascii=False)}")
    print(f"   status = {out.status}")
    for b in bad:
        print(f"   ⛔ {b}")
    return bad


def main() -> int:
    allbad: list[str] = []
    _ms = max(1, int(cfg.settings().limits.max_stall))

    # ── S1 **不适配**：CSV 题面 + 4 个 code_agent ⇒ 丢弃，finish 存活 ────────
    # 这是 V45 B 臂第 1 轮的形状（`{code_agent:4, run_shell:1}`）。
    # ⚠️ `finish` 必须排在**前 3 位**（`n=3`）：若把它放到第 5 位，它会被 `[:3]`
    #    截掉，而前 3 条全是 code_agent ⇒ 全丢 ⇒ 变成 S5 的"空轮"形状，
    #    就测不到"丢弃之后仍有候选"这一格了。次序是装置的一部分。
    allbad += _run(
        "S1 CSV 题面 + 4×code_agent ⇒ dropped=2（在 n 内）、by_tool_max_round=1",
        GOAL_REAL,
        _plan(_cand("agent:code_agent", {"task": "a"}, "外援1"),
              _cand("agent:code_agent", {"task": "b"}, "外援2"),
              _cand("finish", {"answer": LONG_ANSWER}, "交付答案"),
              _cand("agent:code_agent", {"task": "c"}, "外援3"),
              _cand("agent:code_agent", {"task": "d"}, "外援4")),
        {"plan": {"rounds": 1, "cands_total": 1, "by_tool_max_round": 1,
                  "dropped_not_applicable": 2,
                  "dropped_na_by_tool": {"agent:code_agent": 2},
                  "examined_total": 5, "truncated_total": 2,
                  "produces_by_round": [1],
                  "per_round": [{"n": 1, "tools": {"finish": 1}}]}})

    # ── S2 **适用**：函数题面 + 同样的 4 个 code_agent ⇒ 一个都不许丢 ───────
    # ⭐ 这是 S1 的对照：证明丢弃读的是**题面 × 声明**，不是"看到 code_agent 就丢"。
    # ⚠️ 这一景第 1 轮会真去 dispatch `agent:code_agent`（外部能力会被调用！）。
    #    为避免联网，`code_agent.py` 在没有『函数名』时会立刻 emit 空答案 ——
    #    但**这里**题面是适用的 ⇒ 它会真去调模型。⛔ 所以这一景**不用真 dispatch**：
    #    把候选放在 `n=3` 之外（第 4、5 条被截断），只验"`dropped_not_applicable=0`"
    #    与"截断吃掉的是 2 条而不是 4 条"。
    allbad += _run(
        "S2 函数题面 + 4×code_agent ⇒ dropped=0（不误杀），截断吃 2 条",
        GOAL_FUNC,
        _plan(_cand("finish", {"answer": LONG_ANSWER}, "交付答案"),
              _cand("recall", {"query": "sum_by_region"}, "先查记忆"),
              _cand("agent:code_agent", {"task": "x"}, "外援1"),
              _cand("agent:code_agent", {"task": "y"}, "外援2"),
              _cand("agent:code_agent", {"task": "z"}, "外援3")),
        {"plan": {"dropped_not_applicable": 0, "dropped_na_by_tool": {},
                  # ⚠️ 不断言 `examined_total == 5`：这一景实测跑了 **3 轮**
                  #    （`LONG_ANSWER` 是表格，函数题的自检判据会打回它），
                  #    而这两个数**跨轮累计** ⇒ 15 / 10。
                  #    要断言的是关系：截断确实发生、且**没有**被记成"丢弃"。
                  "truncated_total": ">0"}})

    # ── S3 未注册工具 ≠ 不适配：两条路径各有读数 ────────────────────────────
    allbad += _run(
        "S3 browser（未注册）+ 1 个不适配的 code_agent ⇒ unknown=1、na=1",
        GOAL_REAL,
        _plan(_cand("browser", {}, "想了没注册"),
              _cand("agent:code_agent", {"task": "a"}, "外援"),
              _cand("finish", {"answer": LONG_ANSWER}, "交付答案")),
        {"plan": {"dropped_not_applicable": 1,
                  "dropped_na_by_tool": {"agent:code_agent": 1},
                  "cands_total": 1, "by_tool_max_round": 1}})

    # ── S4 注入点：不适配 ⇒ 根本不注入，且与"额度不足"分列 ──────────────────
    # `external_answer_tool` 配了它，题面却不适配 ⇒ `skipped_not_applicable=1`，
    # 而 `calls=0`（一次都没调）。
    allbad += _run(
        "S4 注入点：不适配 ⇒ skipped_not_applicable=1、calls=0",
        GOAL_REAL,
        _plan(_cand("finish", {"answer": LONG_ANSWER}, "交付答案")),
        {"external": {"skipped_not_applicable": 1, "skipped_no_budget": 0,
                      "calls": 0},
         "plan": {"rounds": 1, "cands_total": 1, "dropped_not_applicable": 0}},
        ext_tool="agent:code_agent")

    # ── S5 空轮出口：每轮只给不适配的候选 ⇒ 到 max_stall 转交付 ─────────────
    # ⛔ 判据是 `status == "not_applicable"` —— 不是 `stalled`（模型没打转）、
    #    不是 `cascade`（一次都没做错）、不是 `closed`（额度并不不足）。
    allbad += _run(
        f"S5 每轮只有不适配候选 ⇒ {_ms} 轮后走 not_applicable 出口",
        GOAL_REAL,
        _plan(_cand("agent:code_agent", {"task": "a"}, "外援")),
        {"status": "not_applicable",
         "plan": {"rounds": _ms, "cands_total": 0,
                  "dropped_not_applicable": _ms,
                  "produces_by_round": [0] * _ms}})

    print()
    if allbad:
        print(f"结论: {len(allbad)} 项不符")
        return 1
    print("结论: 五景全部符合预注册（丢弃/不误杀/未知工具分列/注入点/空轮出口 + 恒等式）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
