#!/usr/bin/env python3
"""V91 前置量测：三臂的**块内容**与**位序**（离线，零模型调用）。

为什么三臂（V90 的教训）：`probe_len.py` 证明「加长 ZK1 ⇒ ZK2 升到第 1」，
但**加长本身也改了块的字节串**，而 §157.5 已经量到「无关字节串能翻转 ±0.8」。
⇒ L1 的效应有两个候选原因，必须用 L2 分开：

  L0  基线：ZK×4                      （位序 4 条都不动）
  L1  只给 ZK1 +2 句（+44 字）         （ZK1 第 1 → 第 2）
  L2  四条**各** +2 句（+176 字）      （相对差不变 ⇒ 位序应不动）

L2 分数不变 + L1 分数变 ⇒ 归因「位序」；L2 也变 ⇒ 归因「字节串总量/长度」。

⛔ 纪律 31：不打开归档库，库在 /tmp 重建。
⛔ 块拼装照抄 `core.py:2246`：`" ｜ ".join(f.text[:120] for f in recalled)`（每条只取前 120 字）。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench/evidence/v6_s176"))
from mw.memory import MemoryStore  # noqa: E402
from make_seed_v87 import ZK  # noqa: E402

#: 与 V90 `probe_len.py` **逐字相同**（否则 +44 这个数就不对应）
FILL = "这条惯例长期有效，遇到同类问题一律照此处理。"
ROWS = [json.loads(x) for x in
        (ROOT / "bench/heldout/g7j_n4_heldout.jsonl").read_text(encoding="utf-8").splitlines()
        if x.strip()]
K = 6                                     # `V1_RECALL_K` 默认值（config.py:291）


def texts(arm: str) -> list[str]:
    if arm == "L0":
        return list(ZK)
    if arm == "L1":
        return [ZK[0] + FILL * 2] + list(ZK[1:])
    if arm == "L2":
        return [x + FILL * 2 for x in ZK]
    raise SystemExit(f"未知臂 {arm}")


def tag(t: str) -> str:
    return next((f"ZK{i}" for i in (1, 2, 3, 4) if f"（ZK{i}）" in t), "?")


def main() -> None:
    print(f"# FILL = {FILL!r}  len={len(FILL)}  ×2 = {len(FILL) * 2} 字")
    print(f"# k = {K}（默认）；块拼装 = \" ｜ \".join(f.text[:120])  —— core.py:2246\n")
    for arm in ("L0", "L1", "L2"):
        tmp = Path(tempfile.mkdtemp(prefix=f"v91_{arm}_"))
        m = MemoryStore(tmp / "memory.db")
        ts = texts(arm)
        for t in ts:
            m.commit_fact(t, source="abstract", confidence=0.6)
        print(f"== {arm}")
        for i, t in enumerate(ts, 1):
            print(f"   ZK{i}: {len(t):3d} 字   {'（>120 ⇒ 块内被截断）' if len(t) > 120 else ''}")
        print(f"   {arm} 拼块总字数（k={K}，四题同库）")
        for r in ROWS:
            got = m.recall(r["prompt"], k=K)
            seq = [tag(f.text) for f in got]
            block = " ｜ ".join(f.text[:120] for f in got)
            print(f"     {r['id']:22s} n={len(got)} {'→'.join(seq)}  块={len(block)} 字"
                  f"  首位={seq[0] if seq else '（空）'}")
        m.close()
        print()


if __name__ == "__main__":
    main()
