"""V44 离线探针：交付验收六景（真 `Core`、零模型、零网络）。

⛔ 为什么直接驱动 `_finish`：被改动的就是它（验收在这里算、`Outcome.verdict`
   在这里定）。走 `run()` 反而会把 PLAN 的不确定性引进读数 —— 那是另一件事。
   本题只问一句：**给定(status, 答案, 工作目录)，verdict 对不对。**

⛔ 状态目录必须隔离（踩过一次：漏了 `V1_STATE_DIR`，生产库多出 2 条 episode）。
"""
from __future__ import annotations

import contextlib
import dataclasses
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="v44probe_")
os.environ["V1_STATE_DIR"] = str(Path(_TMP) / "state")
os.environ["V1_WORKSPACE"] = str(Path(_TMP) / "ws")
os.environ["V1_LLM_API_KEY"] = ""            # 强制 stub，绝不联网
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mw import config as cfg                   # noqa: E402
from mw.core import Core                       # noqa: E402
from mw.llm import StubLLM                     # noqa: E402

GOAL_REAL = ("工作目录里有 sales.csv（列为 月份,区域,金额）。请计算每个区域的金额合计，"
             "按合计从高到低生成一张 markdown 表格，写入工作目录下的 report.md。"
             "表格两列为 区域|合计。")
GOAL_NOFILE = "请解释一下快速排序的时间复杂度，并说明最坏情况出现在什么输入上。"
GOAL_TWO = "请生成 out/a.md 与 out/b.json 两个文件。"

REPORT = "| 区域 | 合计 |\n|---|---|\n| 华北 | 2700 |\n| 华南 | 2000 |\n| 华东 | 700 |\n"


def _core(ws: Path) -> Core:
    s = cfg.settings()
    s = dataclasses.replace(s, paths=dataclasses.replace(
        s.paths, workspace=ws, state=Path(_TMP) / "state", logs=Path(_TMP) / "logs"))
    return Core(s, llm=StubLLM(), critique_ledger=None, score_ledger=None,
                caliber_sig="", memory_mode="raw", wire_external=False)


def _one(name: str, goal: str, answer: str, files: dict[str, str],
         status: str = "done") -> tuple[str, str, dict]:
    ws = Path(_TMP) / ("ws_" + name)
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    for rel, body in files.items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    core = _core(ws)
    core._goal_text = goal                    # 等价于 `run()` 里的那一行赋值
    # 内核会把 `■` 行同时印到 stdout（带墙钟 ⇒ 两次跑不可能逐字节相同）。
    # ⛔ 吞掉它，让 p1 成为**可复算**的证据：同一份代码两次跑必须 sha 相同。
    with contextlib.redirect_stdout(io.StringIO()):
        out = core._finish(status, answer, "", [])
    return out.verdict, out.status, out.stats["delivery"]


CASES = [
    # name, 题面, 答案, 工作目录里的文件, 预测
    ("S1_nofile_has_answer", GOAL_REAL, REPORT, {"sales.csv": "月份,区域,金额\n"}, "FAIL"),
    ("S2_file_exists", GOAL_REAL, "", {"sales.csv": "a\n", "report.md": REPORT}, "PASS"),
    ("S3_no_artifact_has_answer", GOAL_NOFILE, "O(n log n)，最坏 O(n^2)。", {}, "ANSWER"),
    ("S4_no_artifact_empty", GOAL_NOFILE, "", {}, "EMPTY"),
    ("S5_partial", GOAL_TWO, "写好了 a.md", {"out/a.md": "x"}, "PARTIAL"),
    ("S6_input_only", "工作目录里有 sales.csv，请把它里的金额加总后在回答里说出总数。",
     "2700", {"sales.csv": "a\n"}, "ANSWER"),
    # ← 下面这一景**不在预注册里**，是事后加的：复现 V43 的 C 臂（stalled + 产物正确）
    ("S7_stalled_but_artifact", GOAL_REAL, "", {"sales.csv": "a\n", "report.md": REPORT},
     "PASS"),
]


def main() -> int:
    print("=" * 74)
    print("V44 交付验收探针 —— 真 Core / 零模型 / 隔离状态目录")
    # ⛔ 不印临时目录名：它每次不同 ⇒ p1 的两份副本会 sha 不同，
    #    而"可复算"是这份证据唯一的价值（读数必须能靠重跑校验）。
    print("tmp=<每次新建的隔离目录>")
    print("=" * 74)
    bad = 0
    for name, goal, answer, files, want in CASES:
        got, status, dv = _one(name, goal, answer, files)
        ok = (got == want)
        bad += 0 if ok else 1
        print(f"{'✅' if ok else '❌'} {name}: verdict={got} 期望={want} "
              f"status={status} outputs={dv['outputs']} "
              f"found={dv['found']} missing={dv['missing']} "
              f"inputs_missing={dv['inputs_missing']}")
    # ── 反向读数：同一份代码在"没有题面"时必须不给 PASS ────────────────────
    got, _, dv = _one("S8_empty_goal", "", "", {})
    ok8 = (got == "EMPTY")
    bad += 0 if ok8 else 1
    print(f"{'✅' if ok8 else '❌'} S8_empty_goal: verdict={got} 期望=EMPTY "
          f"outputs={dv['outputs']}")
    print()
    print(f"结论: {'全部符合预测' if bad == 0 else f'{bad} 景不符'}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
