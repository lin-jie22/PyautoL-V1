#!/usr/bin/env python3
"""V236 装置自测（离线、零模型）。钉住三件事，缺一件结论就不能归因：

T1 三臂入参**逐字节相同**（只有模型名不同）——「模型」是唯一自变量。
T2 `models=()` ⇒ 用 `core.s.llm.model`（V235 行为不变）。
T3 `models=(m1,m2)` ⇒ 按 i % 2 轮换；且 `vec` 落进 rows。
T4 `--goals-swap-models` 默认空串。
T5 换路仍用**真打分器**（`scorer.score`），不走 `reflect.verify`。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT))

import run as R                                              # noqa: E402
import mw.llm as L                                           # noqa: E402

npass = nfail = 0


def check(tag: str, desc: str, ok: bool, detail: str = "") -> None:
    global npass, nfail
    if ok:
        npass += 1
        print(f"PASS {tag}  {desc}")
    else:
        nfail += 1
        print(f"FAIL {tag}  {desc}\n      {detail}")


class FakeLLM:
    def __init__(self, model: str, calls: list) -> None:
        self.model = model
        self.calls = calls

    def complete(self, messages, *, max_tokens=512, temperature=None, timeout_s=None):
        self.calls.append(self.model)
        return "```python\ndef parse_duration(s):\n    return 0\n```"


class FakeScorer:
    """返回带 vec 的 Scored —— 钉住「vec 不许被丢掉」。"""
    def __init__(self) -> None:
        self.calls = 0

    def score(self, task, answer):
        from mw.bench import Scored
        self.calls += 1
        return Scored(score=0.5, why="fake",
                      vec=(True, False, True, False, True, False))


class FakeCfg:
    """⚠️ 直接用**真** `LLMConfig`：`_swap_candidates` 用 `dataclasses.replace`
    换模型名，任何包装层都会抛 `TypeError`（首版就这么红的 —— 装置自测抓到的第一条）。
    要用就 `FakeCfg(model)` 返回 `LLMConfig` 实例本身。"""

    def __new__(cls, model: str):
        from mw.config import LLMConfig
        return LLMConfig(model=model, offline=False, api_key="x")


class FakeS:
    def __init__(self, model: str) -> None:
        self.llm = FakeCfg(model)


class FakeCore:
    def __init__(self, model: str) -> None:
        self.s = FakeS(model)


class FakeTask:
    prompt = "把 '1h30m' 转成分钟"
    tests = ["assert parse_duration('1h30m') == 90"] * 6

    def as_dict(self):
        return {"id": "t01", "prompt": self.prompt, "tests": self.tests}


def main() -> int:
    calls: list[str] = []
    made: list[str] = []
    orig = L.build_llm

    def fake_build(cfg, ledger=None, stub=None):
        made.append(cfg.model)
        return FakeLLM(cfg.model, calls)

    L.build_llm = fake_build                      # `from ... import` 在函数内 ⇒ 生效
    try:
        sc = FakeScorer()
        # ── T2：models=() ⇒ 用 core 的模型 ─────────────────────────────
        made.clear(); calls.clear(); sc.calls = 0
        _bc, _bs, rows = R._swap_candidates(
            FakeCore("qwen2.5:7b"), sc, FakeTask(), "x = 1", 0.5, 3)
        check("T2", "models=() ⇒ 用 core.s.llm.model（V235 行为）",
              made == ["qwen2.5:7b"] and calls == ["qwen2.5:7b"] * 3,
              f"made={made} calls={calls}")
        check("T2b", "models=() ⇒ rows 里 model 字段为 None（不假装换了模型）",
              all(r.get("model") is None for r in rows), f"{[r.get('model') for r in rows]}")
        # ── T3：轮换 ─────────────────────────────────────────────────
        made.clear(); calls.clear(); sc.calls = 0
        _bc, _bs, rows = R._swap_candidates(
            FakeCore("qwen2.5:7b"), sc, FakeTask(), "x = 1", 0.5, 4,
            models=("m1", "m2"))
        check("T3", "models=(m1,m2) ⇒ 按 i%2 轮换", calls == ["m1", "m2", "m1", "m2"],
              f"calls={calls}")
        check("T3b", "rows 里每次记下实际用的模型名",
              [r.get("model") for r in rows] == ["m1", "m2", "m1", "m2"],
              f"{[r.get('model') for r in rows]}")
        # ── T4：vec 不许丢 ───────────────────────────────────────────
        check("T4", "换路 rows 带 vec（V235 的缺陷：只取 score ⇒ 行为池看不见候选）",
              all(r.get("vec") == [True, False, True, False, True, False] for r in rows),
              f"{[r.get('vec') for r in rows]}")
        check("T4b", "换路真的调了打分器（不是本地 verify）",
              sc.calls == 4, f"scorer.calls={sc.calls}")
        # ── T1：三臂入参逐字节相同 ───────────────────────────────────
        from mw.reflect import build_proposal_prompt, failed_actuals, sha_of
        t = FakeTask()
        start = (Path(__file__).resolve().parent / "start_answer.py").read_text(encoding="utf-8")
        why = "当前最好的一次是 0.8333（共 6 条断言）。请给出**把剩下的失败断言一起修好**的完整实现。"
        ps = {sha_of(build_proposal_prompt(t.prompt, start, why,
                                           failed_actuals(start, t.tests)))
              for _ in range(3)}
        check("T1", "同一起点构造三次 ⇒ prompt 逐字节相同（模型是唯一自变量）",
              len(ps) == 1, f"shas={ps}")

        # ── T5：CLI 默认值 ───────────────────────────────────────────
        import argparse
        p = argparse.ArgumentParser()
        src = (ROOT / "run.py").read_text(encoding="utf-8")
        check("T5", "--goals-swap-models 默认空 ⇒ 部署行为不变",
              'p.add_argument("--goals-swap-models", default=""' in src,
              "找不到该参数定义")
    finally:
        L.build_llm = orig

    print(f"\n{npass}/{npass + nfail} PASS")
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
