#!/usr/bin/env python3
"""§17-7l —— n4 抹除效应的装置侧机制探针（**零模型调用**）。

7k 读数：n0 档 Δ_H=+0.2500（R3a 认领）、n4 档 Δ_H=+0.0000（12 格全 0.2）。
n4 是 n0 的"邻域加长版"，行为却从 +0.25 **抹掉**到 0 —— 为什么？

本探针不调模型、不下行为结论，只把两档之间的**装置侧客观差异**全部量清，
给候选机制逐个判定"实锤 / 排除 / 未测"：

  ① 题面轴：n0 提 1 把钥匙 vs n4 提 4 把 —— token 数与惯例的交集。
  ② 语义块轴：n0 送 1 条 vs n4 送 4 条 —— 条数/总长/本条位置/逐字节完整性
     （f.text[:120] 有没有真的截到谁）。
  ③ 工作记忆通道（7i 装置缺陷的余波）：`recall` 工具输出过 `full[:300]`
     与"每条输出尾 200 字"两道闸之后，各条惯例的存活情况。
     —— 这是**第 2 步起**的通道；第 1 步语义块完整，行为可以发生在第 1 步，
        所以通道差异与 7k 行为读数的因果方向**未测量**，本探针只报差异本身。

判据：无（读数探针，同 probe_7i 先例，不进 run_tests.sh —— 进了就是"跑通即绿"）。
"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V1))
sys.path.insert(0, str(V1 / "bench"))

from bench.build_g7d_seed import VARIANTS           # noqa: E402
from bench.build_g7j_heldout import KEYS, rewrite   # noqa: E402
from mw.memory import MemoryStore, fts_query        # noqa: E402
from state_audit import state_blocks                # noqa: E402

FACTS = VARIANTS["7j"]
ORDER = list(FACTS)                                  # ZK1→ZK4 固定排列


def section(title: str) -> None:
    print(f"\n== {title} ==")


def main() -> int:
    heldout = {}
    for name in ("g7j_n0", "g7j_n4"):
        heldout[name] = {
            json.loads(l)["id"]: json.loads(l)["prompt"]
            for l in (V1 / "bench" / "heldout" / f"{name}_heldout.jsonl").read_text().splitlines()
            if l.strip()
        }

    # ── ① 题面轴 ────────────────────────────────────────────────
    section("① 题面对比（钥匙数 / token 数）")
    for tid in ORDER:
        p0, p4 = heldout["g7j_n0"][tid], heldout["g7j_n4"][tid]
        k0 = sum(1 for k in KEYS.values() if f"（{k}）" in p0)
        k4 = sum(1 for k in KEYS.values() if f"（{k}）" in p4)
        print(f"  {tid:22s} n0: {len(p0):3d}字/{k0}把钥匙   n4: {len(p4):3d}字/{k4}把钥匙")

    # ── ② 语义块轴（用 7k 冻结的 states：H 臂 r1 各题 it=1）─────
    section("② 语义块对比（H 臂 r1，it=1；state 来自 7k 冻结证据）")
    for name in ("ev_n0", "ev_n4"):
        p = V1 / "bench" / "evidence" / "knowledge_s39" / name / "states_H_r1.jsonl"
        for l in p.read_text().splitlines():
            if not l.strip():
                continue
            d = json.loads(l)
            if d.get("it") != 1:
                continue
            blk = state_blocks(d["state"] or "")
            sem = blk.get("[语义记忆]", "")
            # 语义块是空格 join（四条同在一行）—— 按钥匙标记 （ZKx） 切分
            marks = [f"（{k}）" for k in KEYS.values()]
            rows = [seg for seg in sem.split(" （")
                    if seg.strip() and not seg.strip().startswith("[")]
            # 本条位置 = 本条原文之前出现了几把其它钥匙 + 1
            idx = sem.find(FACTS[d["task"]][:20])
            pos = sum(1 for m2 in marks if m2 in sem[:idx]) + 1
            intact = FACTS[d["task"]] in sem
            print(f"  {name} {d['task']:22s} {len(sem):3d}字/{len(rows)}条 "
                  f"本条在第{pos}位 逐字节{'✅' if intact else '❌被截'}")

    # ── ③ 工作记忆通道（recall 输出过两道闸）────────────────────
    section("③ recall 输出过 full[:300] 与 尾200 两道闸（零模型模拟）")
    tmp = Path(tempfile.mkdtemp())
    m = MemoryStore(tmp / "m.db")
    for t in FACTS.values():
        m.commit_fact(t, source="abstract", confidence=0.5)
    for name in ("g7j_n0", "g7j_n4"):
        for tid in ORDER:
            prompt = heldout[name][tid]
            got = m.recall(prompt, k=6)
            joined = "\n".join(f.text for f in got)
            tail = joined[-200:]
            alive = [ORDER.index(k) + 1 for k in ORDER if FACTS[k] in tail]
            dead = [k for k in ORDER if FACTS[k] not in tail]
            own_dead = FACTS[tid] not in tail
            gate300 = "过" if len(joined) <= 300 else "被截"
            print(f"  {name} {tid:22s} recall={len(got)}条 输出={len(joined):3d}字 "
                  f"[:300]{gate300}  尾200存活位={alive} 丢失={dead}"
                  f"{'  ⚠️本条被截' if own_dead else ''}")
    m.close()

    # ── 汇总 ────────────────────────────────────────────────────
    section("汇总：候选机制的装置侧判定")
    print("""  候选A 语义块变长/变多（第1步上下文）      ：实锤存在差异（50字/1条 vs 219字/4条），
          但与行为的因果未测 —— 第1步语义块里本条逐字节完整，模型"看得到"。
  候选B f.text[:120] 截断                   ：排除 —— 四条最长 70 字，R5 100% 保留。
  候选C full[:300] 截断                     ：排除 —— n4 recall 输出 205 字 < 300。
  候选D 每条输出尾 200 字（第2步起的通道）  ：实锤 —— n4 输出 205 字 > 200，
          每题恰好截掉【排头那条】惯例；g7d2/g7e1/g7e2 的排头恰是本条，
          g7d3 丢的是 ZK1（本条 ZK2 完整）。⚠️ 与 7k 行为读数的因果方向未测：
          n0 有效应的两题（g7d2/g7d3）里，g7d2 本条被截、g7d3 本条完好 ——
          "本条被截"解释不了 g7d3 归零，候选机制彼此不互斥。""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
