#!/usr/bin/env python3
"""V53 零模型探针：收尾出口那句「收尾交付过闸」到底问没问**产物**。

⛔ 零模型：`_FixedLLM` 每次 `complete` 都返回同一段文本。
   本轮的被测物是**代码路径属性**（谁在哪一步被问过），不是模型行为 ——
   换成真模型只会把"读不到"和"不会做"混在一起（V52 已经吃过这个亏）。

同一份脚本在**改前 / 改后**各跑一次：
  p0_before.txt = HEAD（`_close_out` 的闸只跑 `vfy.run`）
  p1_after.txt  = 闸前置产物问之后

四个场景（与 PREREG §3 的 J1~J4 一一对应）：
  S1 产物缺失（FAIL）         ⇒ 不许出现「收尾交付过闸」，`artifact_fail=1`
  S2 产物存在（PASS）         ⇒ 必须出现「收尾交付过闸」（反向闸：证明不是把闸改成恒不打印）
  S3 只写了一半（PARTIAL）    ⇒ `artifact_partial=1`，与 FAIL **分列**
  S4 关掉文本判据（`_checks=()`）⇒ 文本闸零跑，但产物问**仍然发生**且可计数
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw import config as cfg                        # noqa: E402
from mw import verify as vfy                        # noqa: E402
from mw.core import Core                            # noqa: E402
from mw.memory import MemoryStore                   # noqa: E402

GOAL_ONE = ("工作目录里有 sales.csv（列为 月份,区域,金额）。请计算每个区域的金额合计，"
            "按合计从高到低生成一张 markdown 表格，写入工作目录下的 report.md。")
GOAL_TWO = "请生成 out/a.md 与 out/b.md 两个文件，分别写一句话。"
ANSWER = "华北 2700 ｜ 华南 2000 ｜ 华东 700"

FAILS: list[str] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


class _FixedLLM:
    """收尾答案的桩：任何请求都回同一段文本。"""

    def __init__(self, reply: str = ANSWER) -> None:
        self.reply = reply
        self.calls = 0

    def complete(self, messages, max_tokens=None):      # noqa: ANN001, ARG002
        self.calls += 1
        return self.reply


def _run_once(goal: str, files: dict[str, str], *, retries: int) -> dict:
    """在**新建空目录**里跑一次 `_close_out`，返回读数。"""
    tmp = Path(tempfile.mkdtemp(prefix="v53_"))
    ws = tmp / "ws"
    ws.mkdir()
    for name, body in files.items():
        p = ws / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    # ⚠️ `Paths` 是 frozen dataclass：**必须在构造前**设 env（构造后赋值会
    #    `FrozenInstanceError`，V51 现场踩过一次）。
    os.environ["V1_WORKSPACE"] = str(ws)
    os.environ["V1_STATE_DIR"] = str(tmp / "state")
    os.environ["V1_LOG_DIR"] = str(tmp / "logs")
    lim = cfg.Limits(max_steps=3, close_reserve_steps=1,
                     verify_retries=retries, verify_static=2)
    mem = MemoryStore(tmp / "state" / "memory.db")
    lines: list[str] = []
    c = Core(settings=cfg.Settings(limits=lim), llm=_FixedLLM(), memory=mem)
    c.log = lambda m: lines.append(str(m))          # noqa: ARG005
    c._reset_verify(goal)
    # ⚠️ 判据**显式给**（AZ92 的老坑）：同进程里前序断言会改 `os.environ`
    #    ⇒ 从接线里取到的可能是空的，那样"闸零跑"会被读成"闸放行"。
    c._checks = (tuple(vfy.derive(goal, 2)) if retries > 0 else ())
    c.verify_stats["checks"] = [x.name for x in c._checks]

    seen: dict = {}

    def _done(status: str, answer: str, reason: str) -> str:   # noqa: ANN001
        seen.update(status=status, answer=answer, reason=reason)
        return "ok"

    c._close_out(goal, ["h1"], _done)
    co = dict(c.closeout_stats)
    out = {
        "goal": goal.split("。")[0][:20],
        "files": sorted(files),
        "retries": retries,
        "checks": len(c._checks),
        "status": seen.get("status"),
        "answer_sha": __import__("hashlib").sha256(
            str(seen.get("answer", "")).encode()).hexdigest()[:8],
        "answer_chars": len(str(seen.get("answer", ""))),
        "reason": str(seen.get("reason", ""))[:160],
        "gate_lines": [ln for ln in lines if "🧪" in ln],
        "said_passed": any("收尾交付过闸" in ln for ln in lines),
        "artifact_fail": int(co.get("artifact_fail", 0)),
        "artifact_partial": int(co.get("artifact_partial", 0)),
        "closeout_runs": int(c.verify_stats.get("closeout_runs", 0)),
        "gate_failed": int(co.get("gate_failed", 0)),
    }
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def main() -> int:
    print("=" * 78)
    print("V53 收尾出口：产物问有没有在「过闸」之前发生")
    print("=" * 78)

    s1 = _run_once(GOAL_ONE, {}, retries=2)
    s2 = _run_once(GOAL_ONE, {"report.md": "| 区域 | 合计 |\n|---|---|\n| 华北 | 2700 |\n"},
                   retries=2)
    s3 = _run_once(GOAL_TWO, {"out/a.md": "第一句\n"}, retries=2)
    s4 = _run_once(GOAL_ONE, {}, retries=0)

    for name, r in (("S1 产物缺失(FAIL)", s1), ("S2 产物存在(PASS)", s2),
                    ("S3 半产物(PARTIAL)", s3), ("S4 无文本判据", s4)):
        print(f"\n[{name}]")
        print("  " + json.dumps(r, ensure_ascii=False, sort_keys=True))

    print("\n" + "-" * 78)
    print("判定（PREREG §3）")
    # J1：产物缺失 ⇒ 不许说「过闸」，且 FAIL 要被计数看见
    _check("J1 产物缺失 ⇒ 日志**不含**「收尾交付过闸」",
           s1["said_passed"] is False,
           f"过闸={s1['said_passed']} 闸行={s1['gate_lines']}")
    _check("J1b 产物缺失 ⇒ `artifact_fail=1`（可计数）",
           s1["artifact_fail"] == 1, f"artifact_fail={s1['artifact_fail']}")
    # J2 反向闸：产物在时必须还能说「过闸」（否则只是把闸关掉）
    _check("J2 产物存在 ⇒ 日志**含**「收尾交付过闸」",
           s2["said_passed"] is True, f"闸行={s2['gate_lines']}")
    _check("J2b 产物存在 ⇒ FAIL/PARTIAL 都是 0",
           s2["artifact_fail"] == 0 and s2["artifact_partial"] == 0,
           f"F={s2['artifact_fail']} P={s2['artifact_partial']}")
    # J3 形态分列
    _check("J3 半产物 ⇒ `artifact_partial=1` 且 `artifact_fail=0`（与 FAIL 分列）",
           s3["artifact_partial"] == 1 and s3["artifact_fail"] == 0,
           f"F={s3['artifact_fail']} P={s3['artifact_partial']}")
    _check("J3b 半产物 ⇒ 也不许说「过闸」", s3["said_passed"] is False,
           f"过闸={s3['said_passed']}")
    # J4 不依赖开关
    _check("J4 关掉文本判据 ⇒ 文本闸零跑但产物问仍在",
           s4["checks"] == 0 and s4["closeout_runs"] == 0
           and s4["artifact_fail"] == 1,
           f"checks={s4['checks']} closeout_runs={s4['closeout_runs']} "
           f"artifact_fail={s4['artifact_fail']}")
    _check("J4b 关掉文本判据 ⇒ 同样不许说「过闸」", s4["said_passed"] is False,
           f"过闸={s4['said_passed']}")
    # 混口径防护：四格里交付物必须逐字不变（同一段桩文本）
    _shas = {r["answer_sha"] for r in (s1, s2, s3, s4)}
    _check("P1 交付物逐字不变（四格同 sha）", len(_shas) == 1, f"shas={sorted(_shas)}")
    _check("P2 状态词不变（都走 `closed`）",
           {r["status"] for r in (s1, s2, s3, s4)} == {"closed"},
           f"status={sorted({r['status'] for r in (s1, s2, s3, s4)})}")

    print("\n" + "=" * 78)
    print(f"结果：{'PASS' if not FAILS else 'FAIL'}  "
          f"失败项={len(FAILS)} {FAILS if FAILS else ''}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
