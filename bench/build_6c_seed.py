#!/usr/bin/env python3
"""§17-6 6c 三臂实验的**种子构建器**（DESIGN §17-6 ★6c 事前登记）。

构建三个记忆目录（臂间唯一差异 = 起跑库里的 facts）：
    memH/  启发式 —— 离线桩失败会话（两种打回模式 + 超限）经真模型蒸馏的 abstract facts
    memP/  安慰剂 —— 与 H 逐条**等长**、同条数、同 source=abstract 的异域句
    memE/  空表   —— 只建 schema

⛔ 纪律：
  - H 的 facts 数量以蒸馏实产为准，**不许手补**（蒸馏一条没出 ⇒ 本脚本 rc=1，整组作废）；
  - P 的等长由本脚本逐条断言，差一个字符都抛异常（登记写明"差一个字都不算等长"）；
  - 构建完当场做 **R1 送达预检**：用第一道 heldout 题的 goal 走同一条
    `MemoryStore.recall()` 路径 —— H 必须取回启发式、P 必须取回安慰剂、E 必须为空。
    取不回 ⇒ rc=2（处理未送达，跑臂没有意义）。

用法：
    python3 bench/build_6c_seed.py --out-dir /tmp/v1_6c \
        --suite bench/heldout/struct_convert_heldout.jsonl
（蒸馏需要模型环境变量：V1_LLM_BASE_URL / V1_LLM_API_KEY / V1_LLM_MODEL）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mw import config as cfg                                   # noqa: E402
from mw.core import Core                                       # noqa: E402
from mw.memory import MemoryStore                              # noqa: E402
from mw.abstractor import ABSTRACT_SOURCE, distill             # noqa: E402

#: 安慰剂原料池 —— 与代码/结构转换**完全无关**的领域（书架 / 日程 / 天气）。
#: 足够长，保证任何启发式都能切出等长的句子。
_PLACEBO_POOL = (
    "书架第三层的旅行手册按照出版年份重新排列了一次，"
    "傍晚的光线落在目录标签上显得格外整齐，"
    "下周三上午要把阳台的花盆转到朝南的方向，"
    "天气预报说周末两天都有小到中雨，出门记得带伞，"
    "厨房抽屉里的螺丝刀上次用完放回了原来的格子。"
)


def _bad_answer_c(task_prompt: str) -> str:
    """失败模式 ①：代码块语言标签是 c（题面要 Python）。"""
    return "```c\nint solve(int x) { return x + 1; }\n```"


def _bad_answer_name(fn: str) -> str:
    """失败模式 ②：函数名不对（verify `defines` 判据打回）。"""
    return f"```python\ndef totally_wrong_name(a):\n    return a\n```  # 应为 {fn}"


def _fn_name(prompt: str) -> str:
    """从题面抠函数名（`写一个函数 xxx(`）。"""
    import re
    m = re.search(r"函数\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", prompt)
    return m.group(1) if m else "flatten"


def _stub_session(mem: MemoryStore, tasks: list[dict], log) -> None:
    """离线桩失败会话：每题两次坏答案都被 verify 打回，最后超限交付。

    第二轮（第二十七轮）之后，failure 行的 content 自带 verify 报告原文 ——
    那正是蒸馏的原料。桩只负责让失败**发生**，不伪造任何读数。
    """
    for t in tasks:
        fn = _fn_name(t["prompt"])
        answers = [_bad_answer_c(t["prompt"]), _bad_answer_name(fn)]
        calls = {"n": 0}

        class L:
            def complete(self, messages, **kw):
                i = min(calls["n"], len(answers) - 1)
                calls["n"] += 1
                return json.dumps(
                    {"candidates": [{"intent": f"v{i}", "tool": "finish",
                                     "args": {"answer": answers[i]},
                                     "rationale": "交付"}]},
                    ensure_ascii=False)

        lim = cfg.Limits(max_steps=8, max_llm_calls=24, max_candidates=2,
                         max_consecutive_failures=6, max_stall=6,
                         verify_retries=1)
        c = Core(settings=cfg.Settings(limits=lim), llm=L(), memory=mem,
                 wire_external=False)
        c.log = lambda *_: None
        out = c.run(t["prompt"])
        log(f"  桩会话 {t['id']}: status={out.status} llm_calls={calls['n']}")


def _build_placebo(facts: list) -> list[str]:
    """与 H 逐条等长的异域句。确定性：只依赖 (序号, 长度)。"""
    out = []
    for i, f in enumerate(facts):
        n = len(f.text)
        start = (i * 53) % len(_PLACEBO_POOL)
        s = (_PLACEBO_POOL[start:] + _PLACEBO_POOL)[:n]
        assert len(s) == n, f"安慰剂等长断言失败：{len(s)} != {n}"
        out.append(s)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out-dir", required=True)
    p.add_argument("--suite", required=True)
    p.add_argument("--n-tasks", type=int, default=2,
                   help="用前 N 道题构造失败史（默认 2）")
    args = p.parse_args(argv)

    out = Path(args.out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    tasks = [json.loads(l) for l in
             Path(args.suite).read_text().splitlines() if l.strip()][:args.n_tasks]

    # ── H：桩失败史 → 真模型蒸馏 ──────────────────────────────────────────
    memH_dir = out / "memH"
    memH = MemoryStore(memH_dir / "memory.db")
    print("== H 臂：桩失败会话 ==")
    _stub_session(memH, tasks, print)
    stats = memH.stats()
    print(f"  桩会话后 episodes={stats['episodes']} facts={stats['facts']}")

    print("== H 臂：真模型蒸馏 ==")
    s = cfg.settings()
    from mw.llm import build_llm
    st = distill(memH, build_llm(s.llm), max_tokens=160)
    print(f"  {st.as_line()}")
    h_facts = [r for r in _all_facts(memH) if r.source == ABSTRACT_SOURCE]
    for f in h_facts:
        print(f"  · (len={len(f.text)}) {f.text}")
    if not h_facts:
        print("❌ 蒸馏一条都没产出 ⇒ H 臂为空，整组作废（不许手补）")
        return 1

    # ── P：逐条等长安慰剂 ────────────────────────────────────────────────
    memP = MemoryStore(out / "memP" / "memory.db")
    print("== P 臂：等长安慰剂 ==")
    for txt in _build_placebo(h_facts):
        memP.commit_fact(txt, source=ABSTRACT_SOURCE, confidence=0.6)
        print(f"  · (len={len(txt)}) {txt}")

    # ── E：空表 ──────────────────────────────────────────────────────────
    MemoryStore(out / "memE" / "memory.db").close()
    print("== E 臂：空表已建 ==")

    # ── R1 送达预检：走同一条 recall() 路径 ───────────────────────────────
    goal = tasks[0]["prompt"]
    print(f"== R1 送达预检（goal={goal[:40]}…）==")
    rH = memH.recall(goal, k=6)
    rP = memP.recall(goal, k=6)
    rE = MemoryStore(out / "memE" / "memory.db").recall(goal, k=6)
    print(f"  H 召回 {len(rH)} 条：{[f.text[:30] for f in rH]}")
    print(f"  P 召回 {len(rP)} 条：{[f.text[:30] for f in rP]}")
    print(f"  E 召回 {len(rE)} 条")
    ok = len(rH) >= 1 and len(rP) >= 1 and len(rE) == 0
    print("R1 预检：" + ("通过" if ok else "❌ 失败（处理未送达，跑臂没有意义）"))
    return 0 if ok else 2


def _all_facts(mem: MemoryStore):
    for r in mem.conn.execute(
            "SELECT id, text, source, confidence, hits, ts FROM facts ORDER BY id"):
        yield mem._fact(r, 0.0)


if __name__ == "__main__":
    raise SystemExit(main())
