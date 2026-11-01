#!/usr/bin/env python3
"""V39 前置量：探索宽度到底卡在哪一段？—— 零内核改动，真模型单发。

## 为什么要量这一个数

V37 收尾的零成本读数（`p0_width.txt`）：四轮 76 次 `通道=深`，`候选=1` 出现 **76 次**，
`候选≥2` 出现 **0 次**。而 `state.n`（`n = min(budget.candidates, cand_left)`）的实测分布是
`3×24 / 2×20 / 1×32` —— **44 次给过 ≥2 的宽度，一次都没兑现**。

`max_candidates` 声明为 3，`metacog` 弱族建议也给 3，`budget.scaled()` 还会放大它 ——
但真实候选恒为 1 ⇒ 「在候选之间选择 / 排序 / 否决」这一整族机制（含 V37 的预演否决、
价值层否决、`_extract` 的去重、`plan.steps[:n]` 的截断）**全部是零次机制**。

## 三段必须分开数（纪律 45：一个分类太粗就会把两件事算成一件）

    A 模型原始给了几条      ← raw 里的 `candidates` 数组长度（去重前）
    B 去重后剩几条          ← `Planner._extract` 的返回值（同 tool|args 判重）
    C 截断后剩几条          ← `plan.steps[:n]`，就是日志里的 `候选=N`

只量 C 会得到「模型只给 1 条」这个结论，但**它可能是错的**：
若 A=3 而 B=1，真凶是去重；若 A=1，真凶才是模型。

## 装置

真 `Core`（工具目录与 `run.py --bench` 完全同源，catalog sha 现场校验）+
真 `Planner`（真 `_SYS`、真 `state_chars`、真 `max_tokens`）+ 真 HTTPLLM（T=0）。
`state` 取 V37 冻结的 `states_a0_r1.jsonl` **原文**（不是重新渲染 —— 重新渲染会引入
`[工作记忆]` 的句柄差异，那就不是在测同一件事了）。
题面取 `bench/specgen/tasks_v8.jsonl`（suite_sha 现场校验）。

⛔ 不写任何证据路径；只打印（纪律 34）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
sys.path.insert(0, str(ROOT))

#: ⚠️ 必须在任何 `import mw.config` 之前：LLM 字段的 default_factory 在 import 时求值。
os.environ.setdefault("NO_PROXY", "127.0.0.1")
os.environ.setdefault("no_proxy", "127.0.0.1")
os.environ.setdefault("V1_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
os.environ.setdefault("V1_LLM_API_KEY", "ollama-local")
os.environ.setdefault("V1_LLM_MODEL", "qwen2.5:7b")
os.environ.setdefault("V1_LLM_TEMPERATURE", "0")
os.environ.setdefault("V1_LLM_MAX_TOKENS", "1024")
os.environ.setdefault("V1_STATE_CHARS", "2400")

from hashlib import sha256                                    # noqa: E402

from mw import config as cfg                                  # noqa: E402
from mw.bench import Suite                                    # noqa: E402
from mw.core import Core                                      # noqa: E402
from mw.llm import HTTPLLM, parse_json, split_answer           # noqa: E402
from mw.memory import MemoryStore                             # noqa: E402

V37 = Path("/tmp/v37")
SUITE = ROOT / "bench" / "specgen" / "tasks_v8.jsonl"
EXPECT_SUITE = "519dad774a3664612e6611b220b33f4a"
EXPECT_CATALOG = "8decb60895d4a972"


class RecLLM:
    """记录 `complete()` 的**每一次**原始返回。只听不拦（不修改任何输入输出）。

    ⚠️ 必须记**列表**，不能只记最后一次：`Planner.propose` 在「选了 finish 但没给答案」时
    会再调一次 `self.answer(...)` 做**定向补救**（`planner.py:381-405`），那也是一次
    `complete()`。只留最后一次 ⇒ 三段归因读的是补救响应，不是规划响应 ⇒
    `A`（模型原始候选数）被算成 0，而归因结论会整个反过来。
    这正是纪律 33 的形状：同一 `(task, it)` 会有多行，分析器的键必须带上"第几次"。
    """

    def __init__(self, inner):
        self.inner = inner
        self.raws: list[str] = []
        self.prompts: list[str] = []

    @property
    def raw(self) -> str:
        """**规划调用**的原始输出（第一次），不是最后一次。"""
        return self.raws[0] if self.raws else ""

    @property
    def n_calls(self) -> int:
        return len(self.raws)

    def complete(self, messages, max_tokens=None):
        self.prompts.append("\n".join(f"[{m.role}] {m.content}" for m in messages))
        out = self.inner.complete(messages, max_tokens=max_tokens)
        self.raws.append(out or "")
        return out

    def __getattr__(self, key):
        return getattr(self.inner, key)


def split_raw(raw: str) -> tuple[int, str]:
    """(模型原始 candidate 条数, 诊断标签)。**不经过** `_extract` 的去重。"""
    head, payload = split_answer(raw)
    data = parse_json(head, fallback=None)
    if data is None and payload:
        data = parse_json(raw, fallback=None)
    if not isinstance(data, dict):
        return 0, "no_json"
    items = data.get("candidates") or data.get("steps") or []
    if not isinstance(items, list):
        return 0, "not_list"
    return len(items), "ok"


def main() -> int:
    n_req = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    s = cfg.settings()

    # ── H0：口径现场校验（不通过就不出读数）─────────────────────────────
    suite = Suite.load(SUITE)
    got_suite = suite.sha()
    print("=" * 92)
    print(f"V39 前置量：探索宽度三段分解（请求 n={n_req}）")
    print("=" * 92)
    print(f"suite_sha = {got_suite}  {'✅' if got_suite == EXPECT_SUITE else '⛔ 与 V37 不同'}")
    print(f"temperature = {s.llm.temperature}   state_chars = {s.limits.state_chars}   "
          f"planner.max_tokens = {s.limits.llm_max_tokens}")
    print(f"limits.max_candidates = {s.limits.max_candidates}")
    if got_suite != EXPECT_SUITE:
        print("⛔ suite 与 V37 不一致 ⇒ 读数不可比，退出")
        return 2

    # state 原文从 V37 冻结产物取：必须是**同一个** state，不许重新渲染
    dump = V37 / "states_a0_r1.jsonl"
    if not dump.exists():
        print(f"⛔ 缺少冻结 state：{dump}")
        return 3
    states: dict[str, str] = {}
    for line in dump.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if int(d.get("it") or 0) == 1:
            states.setdefault(str(d.get("task")), str(d.get("state") or ""))
    goals = {t.id: t.prompt for t in suite.tasks}

    mem = MemoryStore(Path("/tmp/v39_width_state/memory.db"))
    llm = RecLLM(HTTPLLM(s.llm))
    core = Core(s, llm=llm, memory=mem, wire_external=False)
    cat = sha256(core.tools.catalog().encode("utf-8")).hexdigest()[:16]
    print(f"tool_catalog_sha = {cat}  {'✅' if cat == EXPECT_CATALOG else '⛔ 与 V37 不同'}")
    if cat != EXPECT_CATALOG:
        print("⛔ 工具目录不同 ⇒ prompt 不同 ⇒ 读数不可比，退出")
        return 2

    rows = []
    print()
    print("-" * 92)
    print(f"{'task':8} {'A 原始':>7} {'B 去重后':>8} {'C 截断后':>8} {'标签':>10}  raw 长度")
    print("-" * 92)
    for tid in [t.id for t in suite.tasks]:
        goal, state = goals.get(tid), states.get(tid)
        if not goal or not state:
            print(f"{tid:8} ⛔ 缺题面或 state（goal={bool(goal)} state={bool(state)}）")
            continue
        # ⚠️ 每题必须清空记录器：`RecLLM` 实例是跨题共享的（省一次 HTTP 会话）。
        #    不清空 ⇒ `raws[0]` 永远是**第一题**的输出，`calls` 会一路累加到 7，
        #    于是六题的行看起来完全一样、而"`tool` 出现 18 次"这种汇总数是拿
        #    同一段文本数了六遍。⛔ 这是纪律 33 的跨题版本：多行同名产物必须按题切窗。
        llm.raws.clear()
        llm.prompts.clear()
        plan = core.planner.propose(goal=goal, state=state, history=[],
                                    n=n_req, guidance="", blocked=(), rejected=())
        a, tag = split_raw(llm.raw)
        b = len(plan.steps)
        c = len(plan.steps[:n_req])
        rows.append({"task": tid, "A": a, "B": b, "C": c, "tag": tag,
                     "raw_len": len(llm.raw), "degraded": bool(plan.degraded),
                     "calls": llm.n_calls,
                     # `"tool"` 的出现次数 ≈ 模型**想**给几个候选。
                     # 若 JSON 坏了但 `tool` 出现 3 次，说明"它给了 3 个、只是写坏了"；
                     # 若只出现 1 次，说明"它压根只想给 1 个" —— 两种病，修法不同。
                     "n_tool_tokens": llm.raw.count('"tool"'),
                     "note": plan.note[:60]})
        flag = "✅" if c >= 2 else "·"
        print(f"{tid:8} {a:>7} {b:>8} {c:>8} {tag:>10}  {len(llm.raw):>5}  {flag}"
              f"  degraded={plan.degraded}  calls={llm.n_calls}"
              f"  note={(plan.note or '')[:32]!r}")
        # 原始输出**必须留档**：只报 `no_json` 等于说"解析失败"，而失败有三种
        # 完全不同的原因（不写 JSON / 写了但被截断 / 写了但被 split 切坏），
        # 只有原文能分开（纪律 6 的第三种形态：检测器自己失效）。
        d = Path("/tmp/v39_raw")
        d.mkdir(parents=True, exist_ok=True)
        for i, r in enumerate(llm.raws):
            (d / f"n{n_req}_{tid}_c{i}.txt").write_text(r, encoding="utf-8")
        print(f"         raw[0][:220] = {llm.raws[0][:220]!r}" if llm.raws else "         (无输出)")
        if len(llm.raws) > 1:
            # 第 2 次起是 `answer()` 定向补救 —— 它**不是**规划响应，必须分开看
            print(f"         raw[1][:120] = {llm.raws[1][:120]!r}  ← 定向补救响应")

    print("-" * 92)
    tot = {"A": sum(r["A"] for r in rows), "B": sum(r["B"] for r in rows),
           "C": sum(r["C"] for r in rows)}
    print(f"合计：A 原始 {tot['A']} 条 → B 去重 {tot['B']} 条 → C 截断 {tot['C']} 条"
          f"（{len(rows)} 题 × 请求 {n_req} 条）")
    n_tool = sum(r["n_tool_tokens"] for r in rows)
    print(f"`\"tool\"` 出现总次数 = {n_tool}（{len(rows)} 题）"
          f" ｜ 触发定向补救的题数 = {sum(1 for r in rows if r['calls'] > 1)}")
    print()
    # ── 归因：三段里哪一段掉得最多（相邻两段之差）──────────────────────
    lost_dedup = tot["A"] - tot["B"]
    lost_cut = tot["B"] - tot["C"]
    print(f"去重损失 = {lost_dedup} 条 ｜ 截断损失 = {lost_cut} 条")
    if tot["A"] >= 2 * len(rows):
        print("⛔ 归因：**内核吃掉了宽度**（A ≥ 2×题数）—— 继续看是去重还是截断在吃。")
    elif n_tool > len(rows):
        print(f"⛔ 归因：**模型给了多个、但 JSON 写坏了**（`\"tool\"` 出现 {n_tool} 次 > 题数 {len(rows)}，"
              f"可 A 只有 {tot['A']}）⇒ 病在**格式/解析**，不在服从性。")
    else:
        print(f"⛔ 归因：**模型原始就只给 1 个候选**（`\"tool\"` 只出现 {n_tool} 次，题数 {len(rows)}）"
              "⇒ 病在**规划器服从性**，内核的 n 根本没起作用。")
    print()
    print("⚠️ 样本量：每题只发一次（T=0 下一发是重放不是抽样，纪律 34/39）")
    print("   ⇒ 本读数只回答「这一刻模型给几条」，不回答「模型稳不稳定」。")

    out = Path(f"/tmp/v39_width_n{n_req}.json")
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"明细已写 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
