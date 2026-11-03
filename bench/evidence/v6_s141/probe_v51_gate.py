#!/usr/bin/env python3
"""V51 离线探针 —— 交付闸的**第二问**（产物在不在）。

零模型：真 `Core` + `StubLLM`，只调 `_gate_finish`（AS20 已证明它是可单独调的单元）。

六景（全部与预注册 §4 的 J2/J3/J5 对应）：
  S1 题面要 report.md、文件不存在          ⇒ 打回（ok=False）+ `artifact_failed=1`
  S2 同一答案、文件已存在                  ⇒ 通过 + `passed=1`、`artifact_failed` 不再涨
  S3 题面**没有**产物（单函数题）          ⇒ 通过、`artifact_failed=0`（⛔ 不许乱开火）
  S4 两个产物只写了一个                    ⇒ 打回，报错里点名**缺的那一个**
  S5 连打三次（`verify_retries=2`）        ⇒ 第 3 次超限交付，`exhausted=1` + `_unverified` 非空
  S6 判据自身炸掉（workspace 是个不存在的类型）⇒ `artifact_error=1` 且**不拦**

⛔ 本探针不写回自己的证据路径（纪律 8）：输出由调用方重定向。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("V1_STATE_DIR", tempfile.mkdtemp(prefix="v51probe_state_"))

from mw import config as cfg                     # noqa: E402
from mw.core import Core                          # noqa: E402
from mw.llm import StubLLM                        # noqa: E402
from mw.memory import MemoryStore                 # noqa: E402
from mw.tools import ToolResult                   # noqa: E402

GOAL_ART = ("工作目录里有 sales.csv（列为 月份,区域,金额）。请计算每个区域的金额合计，"
            "按合计从高到低生成一张 markdown 表格，写入工作目录下的 report.md。"
            "表格两列为 区域|合计。")
GOAL_ART2 = ("读取工作目录里的 sales.csv，生成 out/a.md 与 out/b.json 两个文件。")
GOAL_CODE = ("写一个函数 parse_duration(s)，把 '1h30m' 解析为总秒数。"
             "只输出代码，函数名必须是 parse_duration。")

ANSWER = "| 区域 | 合计 |\n|---|---|\n| 华东 | 300 |\n| 华北 | 120 |"
RESULTS: list[tuple[str, bool, str]] = []


def _core(goal: str, ws: Path) -> Core:
    # `Paths` 是 frozen dataclass ⇒ 只能在构造前用 env 注入工作目录。
    os.environ["V1_WORKSPACE"] = str(ws)
    lim = cfg.Limits(max_steps=2, verify_retries=2, verify_static=2)
    c = Core(settings=cfg.Settings(limits=lim), llm=StubLLM(""),
             memory=MemoryStore(Path(tempfile.mkdtemp(prefix="v51db_")) / "p.db"))
    c.log = lambda m: None                        # noqa: ARG005
    c._reset_verify(goal)
    c._goal_text = goal
    return c


def _gate(c: Core, ans: str = ANSWER) -> ToolResult:
    return c._gate_finish(ToolResult(True, output=ans))


def _check(tag: str, cond: bool, detail: str) -> None:
    RESULTS.append((tag, bool(cond), detail))


def main() -> int:
    ws = Path(tempfile.mkdtemp(prefix="v51ws_"))
    (ws / "sales.csv").write_text("月份,区域,金额\n1月,华东,100\n1月,华北,60\n"
                                  "2月,华东,200\n2月,华北,60\n", encoding="utf-8")

    # ── S1 产物缺失 ⇒ 打回 ────────────────────────────────────────────
    c = _core(GOAL_ART, ws)
    r = _gate(c)
    _check("S1 打回", (r.ok is False
                       and "report.md" in (r.error or "")
                       and c.verify_stats["artifact_failed"] == 1
                       and c.verify_stats["failed"] == 1),
           f"ok={r.ok} err={(r.error or '')[:70]!r} "
           f"art_failed={c.verify_stats['artifact_failed']} "
           f"failed={c.verify_stats['failed']}")

    # ── S2 文件有了 ⇒ 通过 ───────────────────────────────────────────
    (ws / "report.md").write_text(ANSWER, encoding="utf-8")
    r2 = _gate(c)
    _check("S2 通过", (r2.ok is True
                       and c.verify_stats["passed"] == 1
                       and c.verify_stats["artifact_failed"] == 1
                       and c._salvage == ANSWER),
           f"ok={r2.ok} passed={c.verify_stats['passed']} "
           f"art_failed={c.verify_stats['artifact_failed']} "
           f"salvage={'有' if c._salvage else '无'}")

    # ── S3 无产物的题 ⇒ 不拦 ─────────────────────────────────────────
    c3 = _core(GOAL_CODE, ws)
    r3 = _gate(c3, "def parse_duration(s):\n    return 0")
    _check("S3 不乱开火", (r3.ok is True
                           and c3.verify_stats["artifact_failed"] == 0
                           and c3.verify_stats["artifact_checks"] == 1),
           f"ok={r3.ok} art_failed={c3.verify_stats['artifact_failed']} "
           f"checks={c3.verify_stats['artifact_checks']}")

    # ── S4 两个产物写了一个 ⇒ 点名缺的那个 ───────────────────────────
    ws4 = Path(tempfile.mkdtemp(prefix="v51ws4_"))
    (ws4 / "out").mkdir()
    (ws4 / "out" / "a.md").write_text("x", encoding="utf-8")
    c4 = _core(GOAL_ART2, ws4)
    r4 = _gate(c4, "写好了 a.md")
    _check("S4 点名缺失", (r4.ok is False and "out/b.json" in (r4.error or "")
                           and "out/a.md" not in (r4.error or "")),
           f"ok={r4.ok} err={(r4.error or '')[:80]!r}")

    # ── S5 连打三次 ⇒ 超限交付 ───────────────────────────────────────
    ws5 = Path(tempfile.mkdtemp(prefix="v51ws5_"))
    (ws5 / "sales.csv").write_text("x", encoding="utf-8")
    c5 = _core(GOAL_ART, ws5)
    outs = [_gate(c5) for _ in range(3)]
    _check("S5 超限如实交付", (outs[0].ok is False and outs[1].ok is False
                               and outs[2].ok is True
                               and c5.verify_stats["exhausted"] == 1
                               and "report.md" in (c5._unverified or "")),
           f"三次 ok={[o.ok for o in outs]} exhausted={c5.verify_stats['exhausted']} "
           f"unverified={(c5._unverified or '')[:40]!r}")

    # ── S6 判据自己炸掉 ⇒ 计数且不拦 ─────────────────────────────────
    # ⚠️ 用 monkeypatch 而不是喂一个坏 workspace：`Paths` 是 frozen 且 `_env_path`
    #    会把任何字符串变成合法 Path ⇒ 走不到 except 分支（那是"没测到"，不是"没坏"）。
    from mw import verify as _vfy
    _orig = _vfy.check_delivery
    c6 = _core(GOAL_ART, ws)
    _vfy.check_delivery = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        r6 = _gate(c6)
    finally:
        _vfy.check_delivery = _orig
    _check("S6 判据异常不拦", (r6.ok is True
                               and c6.verify_stats["artifact_error"] == 1
                               and c6.verify_stats["artifact_failed"] == 0),
           f"ok={r6.ok} err_cnt={c6.verify_stats['artifact_error']}")

    bad = [t for t, ok, _ in RESULTS if not ok]
    for tag, ok, detail in RESULTS:
        print(f"{'✅' if ok else '⛔'} {tag}: {detail}")
    print(f"\n六景：{len(RESULTS) - len(bad)}/{len(RESULTS)} 绿"
          f"{'' if not bad else ' ｜ 红=' + ','.join(bad)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
