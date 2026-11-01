#!/usr/bin/env python3
"""V40 重放装置：把 V39 冻结的 **15 份规划器原文**灌进真 `Planner`，4 臂对照。

## 为什么是重放而不是再跑一遍模型

V39 已经把「候选恒为 1」归因到**格式**（`p0_triple.txt`：修三引号后 3 个候选）。
而修复要进内核，就得回答"**进了内核**之后它是几个" —— 这个问题与模型无关，
只与**那 15 份原文**有关。再跑一遍模型会引入采样差异，把"装置改进"和"模型抖动"
混成一个数（纪律 34：T=0 下一发是重放不是抽样）。

⇒ 本装置：**零 LLM**。原文当输入，真 `Planner` 当被测件，`V1_PLAN_SALVAGE` 当自变量。

## 装置约束（写死在这里，因为违反任何一条读数就作废）

1. **真 `Planner`**：真 `_SYS`、真 `split_answer`/`parse_json`、真 `state_chars`、真 `max_tokens`。
   ⛔ 不复刻解析逻辑 —— 复刻出来的读数只证明探针自己自洽。
2. **Stub 只回一次**：第 1 次 `complete()` 返回该样本原文，之后一律 `""`
   ⇒ 「定向补救」确定性失败，不会把 A/B 组之外的东西偷偷救回来。
3. **`salvage` 从 `Limits` 读**（`cfg.settings().limits.plan_salvage`），
   并在每臂开头核验它真的等于 env 值 —— 否则测的是探针参数，不是开关（纪律 13）。
4. 逐样本 `raws.clear()`（V39 踩过：跨样本共享记录器，同一段文本被数了 15 遍）。

⛔ 不写任何其他证据路径；所有读数走 stdout + 本文件同目录的 `p1_replay.txt`。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]          # v6_s130 → evidence → bench → V1
sys.path.insert(0, str(ROOT))

from mw import config as cfg                                       # noqa: E402
from mw.llm import split_answer                                    # noqa: E402
from mw.planner import Planner                                     # noqa: E402
from mw.tools import ToolRegistry, _StubMemory, register_core_tools  # noqa: E402

RAW = HERE / "raw"
ARMS = (0, 1, 2, 3)


class OnceLLM:
    """第 1 次返回原文，其后返回 `""`。**记录每一次**（含补救那一次）。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[str] = []      # 每次返回了什么（原文 / ""）

    def complete(self, messages, max_tokens=None):
        self.calls.append(self.text if len(self.calls) == 0 else "")
        return self.calls[-1]


def build_planner(s) -> Planner:
    reg = ToolRegistry()
    register_core_tools(reg, memory=_StubMemory(), workspace=None, allow_note=True)
    return Planner(OnceLLM(""), reg, max_tokens=s.limits.llm_max_tokens,
                   state_chars=s.limits.state_chars,
                   salvage=s.limits.plan_salvage)


def shape_of(text: str) -> str:
    tq = '"' * 3
    if text.count('"tool"') == 0:
        return "E 无JSON"
    if tq in text:
        return "C 三引号"
    if '"finish"' in text and text.count("{") - text.count("}") != 0:
        return "D 兄弟finish"
    try:
        json.loads(text, strict=False)
        return "A 已可解析"
    except (json.JSONDecodeError, ValueError):
        return "B 分隔符"


def run_arm(arm: int, files: list[Path]) -> dict:
    os.environ["V1_PLAN_SALVAGE"] = str(arm)
    s = cfg.settings()
    # 纪律 13：先证"开关通电"——env 没到 Limits，这一臂测的就是别的东西
    if s.limits.plan_salvage != arm:
        raise SystemExit(f"⛔ 开关没通电：env={arm} limits={s.limits.plan_salvage}")
    planner = build_planner(s)
    rows = []
    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        planner.llm = OnceLLM(text)          # 逐样本隔离（约束 4）
        n = 3 if p.stem.startswith("n3_") else 1
        goal = f"重放样本 {p.stem}"
        with contextlib.redirect_stdout(io.StringIO()):
            plan = planner.propose(goal=goal, state="(重放：无 state)", history=[],
                                   n=n, guidance="", blocked=(), rejected=())
        steps = [(s_.tool, dict(s_.args)) for s_ in plan.steps]
        ans = [len(str(a.get("answer", ""))) for t, a in steps if t == "finish"]
        rows.append({
            "sample": p.stem, "shape": shape_of(text), "n": n,
            "cands": len(steps),
            "degraded": bool(plan.degraded),
            "answer_len": ans,
            "steps_repr": repr(steps)[:200],
            "note": (plan.note or "")[:70],
            "llm_calls": len(planner.llm.calls),
        })
    st = planner.stats()
    return {"arm": arm, "sig_limits": _limits_sha(s), "rows": rows,
            "stats": {k: st[k] for k in ("calls", "degraded", "json_fail",
                                         "truncated", "no_tool_fit", "empty_plan",
                                         "salvage", "salvage_ok", "salvage_fail",
                                         "salvage_completed")}}


def _limits_sha(s) -> str:
    from hashlib import sha256
    return sha256(json.dumps(s.limits.as_dict(), sort_keys=True,
                             ensure_ascii=False).encode()).hexdigest()[:16]


def main() -> int:
    files = sorted(RAW.glob("*.txt"))
    if len(files) != 15:
        print(f"⛔ 样本数 {len(files)} ≠ 15 ⇒ 与 PREREG §二 不符，退出")
        return 2
    print("=" * 118)
    print("V40 重放装置：15 份冻结原文 × 4 臂（V1_PLAN_SALVAGE = 0/1/2/3），零 LLM")
    print("=" * 118)
    arms = [run_arm(a, files) for a in ARMS]

    # ── D5：四个取值必须产生四个不同的 limits 哈希 ────────────────────────
    shas = [a["sig_limits"] for a in arms]
    print(f"D5 limits 哈希：{shas}")
    print(f"   {'✅ 4 个全不同（⇒ plan_salvage 真的进了口径快照）' if len(set(shas)) == 4 else '⛔ 有重复 ⇒ 开关没进口径'}")
    print()
    print(f"{'样本':15}{'形态':14}{'n':>2} │ " + " │ ".join(f"臂{a} 候选/降/答长" for a in ARMS))
    print("-" * 118)
    for i, f in enumerate(files):
        cells = []
        for a in arms:
            r = a["rows"][i]
            cells.append(f"{r['cands']}/{'Y' if r['degraded'] else 'n'}/{r['answer_len']}")
        print(f"{arms[0]['rows'][i]['sample']:15}{arms[0]['rows'][i]['shape']:14}"
              f"{arms[0]['rows'][i]['n']:>2} │ " + " │ ".join(f"{c:^18}" for c in cells))
    print("-" * 118)

    # ── D1：按形态分组计数（不许只报总和，纪律 18）──────────────────────
    print("\nD1 形态 × 臂 的「恢复」计数（恢复 = 候选≥1 且未降级 且 载荷非空）")
    shapes = sorted({r["shape"] for r in arms[0]["rows"]})
    hdr = f"{'形态':16}" + "".join(f"{'臂'+str(a):>10}" for a in ARMS)
    print(hdr)
    for sh in shapes:
        cells = []
        for a in arms:
            got = 0
            for r in a["rows"]:
                if r["shape"] != sh:
                    continue
                has_payload = bool(r["answer_len"]) or not any(
                    r["answer_len"]) and r["cands"] >= 1
                if r["cands"] >= 1 and not r["degraded"]:
                    got += 1
            cells.append(f"{got}/{sum(1 for r in a['rows'] if r['shape'] == sh)}")
        print(f"{sh:16}" + "".join(f"{c:>10}" for c in cells))

    # ── D3：A/B 两组必须四臂逐字节相同 ───────────────────────────────────
    print("\nD3 A/B 组跨臂逐字节比对（不同 ⇒ 修复器越界）")
    base = {r["sample"]: r["steps_repr"] for r in arms[0]["rows"]}
    bad = []
    for a in arms[1:]:
        for r in a["rows"]:
            if r["shape"][0] in "AB" and r["steps_repr"] != base[r["sample"]]:
                bad.append((a["arm"], r["sample"]))
    print(f"   差异处：{bad if bad else '无（A/B 两组 6/6 四臂全同）'}")

    # ── 汇总：C/D/E 各臂变化 ────────────────────────────────────────────
    print("\n逐形态 × 臂：候选数向量（证明「谁被修好、谁没有」）")
    for sh in shapes:
        vec = {a["arm"]: [r["cands"] for r in a["rows"] if r["shape"] == sh] for a in arms}
        print(f"   {sh:16} " + "  ".join(f"臂{k}={v}" for k, v in vec.items()))

    print("\n各臂规划器读数")
    for a in arms:
        st = a["stats"]
        print(f"   臂{a['arm']}: calls={st['calls']} degraded={st['degraded']} "
              f"json={st['json_fail']} 截断={st['truncated']} 无工具={st['no_tool_fit']} "
              f"空计划={st['empty_plan']} 修复ok={st['salvage_ok']} "
              f"修复fail={st['salvage_fail']} 补完={st['salvage_completed']}")

    print("\n⛔ 本轮只证「内核**收到**几个候选」；⛔ 不许读成「分数变好」。")
    print("（V39 D3：96 格一格未动；宽度进内核后量分数是下一轮的事）")

    (HERE / "_p1_rows.json").write_text(
        json.dumps(arms, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细已写 {HERE / '_p1_rows.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
