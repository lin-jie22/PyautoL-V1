#!/usr/bin/env python3
"""§17-7n 直发探针（零内核改动、零 agent loop）—— 事前登记见 DESIGN ★7n。

问题：7m-γ 定性「1.0 只在逐字节 n0 题面下出现」后，1.0 依赖的是
prompt 字符串本身（M1）还是 agent loop 的多轮轨迹（M2/M3）？

实测前提（7n 取证，H_out_r1.log + states dump）：g7d2 在真实 loop 里
**第 1 步 propose 直接收尾**（无 recall、无 history/blocked/guidance——
种子里 facts source=abstract ⇒ worldmodel guidance 必空），
交付 state = it=1 初始态。⇒ 单发复刻 = 忠实重放。

装置：**真** Planner（真 _SYS、真工具目录、真 HTTPLLM、T=0）+ 冻结 state
⇒ 单发 propose(history=[], blocked=(), n=1)，读 finish 候选 answer 写法。
⛔ 采纳判据沿 7m-γ 口径：strip().lower() = ADOPT；seen_add = BASE；
非 finish 候选 = DIVERGE（不许溜进判读闸）。全部判读只做格子间比较。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("NO_PROXY", "127.0.0.1")
os.environ.setdefault("no_proxy", "127.0.0.1")
os.environ.setdefault("V1_LLM_BASE_URL", "http://localhost:11434/v1")
os.environ.setdefault("V1_LLM_API_KEY", "ollama")
os.environ.setdefault("V1_LLM_MODEL", "qwen2.5:7b")
os.environ.setdefault("V1_LLM_TEMPERATURE", "0")
os.environ.setdefault("V1_ALLOW_NOTE", "0")
os.environ.setdefault("V1_ALLOW_LEARN", "0")

from hashlib import sha256                                  # noqa: E402
from mw.config import LLMConfig, STATE_CHAR_CAP             # noqa: E402
from mw.llm import HTTPLLM                                  # noqa: E402
from mw.planner import Planner                              # noqa: E402
from mw.tools import ToolRegistry, _StubMemory, register_core_tools  # noqa: E402

EV = ROOT / "bench" / "evidence"
TASK = "g7d2_dedupe_keep"
#: 7k 真实跑臂口径签名里的 tool_catalog_sha（ev_n0/H_out_r1.log）
EXPECT_CATALOG_SHA = "8decb60895d4"


def build_planner() -> Planner:
    reg = ToolRegistry()
    # 实测定标（7n）：真实跑臂口径签名的 tool_catalog_sha=8decb60895d4
    # == allow_note=True 的 5 工具目录（note 在册）⇒ 以它为准复刻 prompt
    register_core_tools(reg, memory=_StubMemory(), allow_note=True)
    got = sha256(reg.catalog().encode("utf-8")).hexdigest()[:16]
    mark = "✅" if got.startswith(EXPECT_CATALOG_SHA) else "⛔ 与真实跑臂不一致 ⇒ 读数作废"
    print(f"工具目录 sha={got}（真实跑臂={EXPECT_CATALOG_SHA}）{mark}")
    if not got.startswith(EXPECT_CATALOG_SHA):
        raise SystemExit(2)
    llm = HTTPLLM(LLMConfig(temperature=0.0))
    return Planner(llm, reg, max_tokens=1024)


def delivery_state(path: Path) -> str:
    last = None
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("task") == TASK:
            last = d["state"] or ""
    if last is None:
        raise SystemExit(f"❌ {path} 里没有 {TASK} 的 state")
    return last


def drop_semantic(state: str) -> str:
    out, n = re.subn(r"\n?\[语义记忆\][^\n]*", "", state, count=1)
    if n != 1:
        raise SystemExit("❌ [语义记忆] 块没找到，S0 构造失败")
    return out


def goal_of(heldout: Path) -> str:
    for line in heldout.read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d["id"] == TASK:
            return d["prompt"]
    raise SystemExit(f"❌ {heldout} 里没有 {TASK}")


def classify(plan) -> str:
    if not plan:
        return "NO_PLAN"
    fin = next((s for s in plan.steps if s.tool == "finish"), None)
    if fin is None:
        return "DIVERGE:" + ",".join(s.tool for s in plan.steps)
    ans = str((fin.args or {}).get("answer", ""))
    has_norm = "strip()" in ans and ".lower()" in ans
    has_base = "seen_add" in ans
    if has_norm and not has_base:
        return "ADOPT"
    if has_base and not has_norm:
        return "BASE"
    return "OTHER"


def main() -> None:
    planner = build_planner()
    llm = planner.llm
    print(f"== ★7n 直发探针 == model={llm.cfg.model} base={llm.cfg.base_url} "
          f"T={llm.cfg.temperature} max_tokens=1024 STATE_CHAR_CAP={STATE_CHAR_CAP}")

    cells = [
        ("S1_n0_1key_1fact", EV / "knowledge_s39/ev_n0", "g7j_n0_heldout.jsonl", False),
        ("S2_n4_4key_4fact", EV / "knowledge_s39/ev_n4", "g7j_n4_heldout.jsonl", False),
        ("S3_7mb_4key_1fact", EV / "knowledge_s42", "g7m_g7d2_heldout.jsonl", False),
        ("S4_pad15_1key_1fact", EV / "knowledge_s43", "g7m_g7d2_pad15_heldout.jsonl", False),
        ("S0_no_mem", EV / "knowledge_s39/ev_n0", "g7j_n0_heldout.jsonl", True),
    ]

    only = set(sys.argv[1:]) or None
    summary: dict[str, list[str]] = {}
    for name, ev_dir, heldout_name, is_s0 in cells:
        if only and name not in only:
            continue
        goal = goal_of(ROOT / "bench/heldout" / heldout_name)
        verdicts = []
        print(f"\n-- {name} --")
        for r in (1, 2, 3):
            state = delivery_state(ev_dir / f"states_H_r{r}.jsonl")
            if is_s0:
                state = drop_semantic(state)
            trunc = max(0, len(state) - STATE_CHAR_CAP)
            plan = planner.propose(goal=goal, state=state, history=[], n=1)
            v = classify(plan)
            verdicts.append(v)
            extra = f" ⚠️截断{trunc}字" if trunc else ""
            print(f"  r{r}: {v}{extra}")
            if v in ("ADOPT", "BASE", "OTHER") and plan:
                fin = next(s for s in plan.steps if s.tool == "finish")
                ans = str((fin.args or {}).get("answer", ""))
                print(f"      answer 前 90 字: {ans[:90]!r}")
        summary[name] = verdicts
        print(f"  ⇒ 格判定: {' '.join(verdicts)}")

    print("\n== 汇总（登记闸）==")
    for name, vs in summary.items():
        adopt = sum(1 for v in vs if v == "ADOPT")
        print(f"  {name}: ADOPT {adopt}/3  {' '.join(vs)}")

    print("\n== 判读 ==")
    # 量具病闸：NO_PLAN 不许溜进判读（第一版教训）
    no_plan = {k: v.count("NO_PLAN") for k, v in summary.items() if v.count("NO_PLAN")}
    if no_plan:
        print(f"  ⛔ 量具病（NO_PLAN 不许进判读闸，须先修装置）: {no_plan}")
        raise SystemExit(2)
    diverge = {k: [v for v in vs if v.startswith("DIVERGE")]
               for k, vs in summary.items()}
    diverge = {k: v for k, v in diverge.items() if v}
    if diverge:
        print(f"  ⚠️ DIVERGE（单发未选 finish——与真实 loop 行为分歧，如实登记）: {diverge}")
    bistable = [k for k, v in summary.items()
                if len({x.split(':')[0] for x in v}) > 1]
    if bistable:
        print(f"  ⚠️ 格内非全同（bistable 候选，7f 前科）: {bistable}")
    s1 = summary["S1_n0_1key_1fact"].count("ADOPT")
    others = [summary[k].count("ADOPT") for k in
              ("S2_n4_4key_4fact", "S3_7mb_4key_1fact", "S4_pad15_1key_1fact")]
    if s1 >= 2 and all(o <= 1 for o in others):
        print("  ⇒ M1：形状效应单发可复现（S1 采纳、其余基线）—— 效应由 (goal+state) "
              "prompt 字符串承载，loop 动力学无贡献")
    elif s1 <= 1 and all(o <= 1 for o in others):
        print("  ⇒ M2：单发全基线 —— 效应需要 loop 轨迹（⛔ 仅当无 DIVERGE/量具病才有效）")
    elif s1 >= 2 and all(o >= 2 for o in others):
        print("  ⇒ M3：单发全采纳 —— 抹除只在 loop 内发生")
    else:
        print("  ⇒ 混合形态：逐格如实登记，不套闸")


if __name__ == "__main__":
    main()
