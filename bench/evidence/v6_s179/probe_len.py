#!/usr/bin/env python3
"""V90 操纵量测：**只改写入的文本长度**，块内位序会不会翻？

零内核改动、零模型调用。库在 /tmp 重建（⛔ 不开归档库，纪律 31）。
题面与知识都用**现成**的（`bench/heldout/g7j_n4_heldout.jsonl` + `make_seed_v87.ZK`），不造新题。

动机：`probe_bm25.py` 显示 g7d2/g7d3 的查询里除四个标号外没有任何字面命中，
四条事实的 bm25 是**与题目无关的常数**（ZK1 −1.056 / ZK2 −0.934 / ZK4 −0.795 / ZK3 −0.692）。
若这个常数只由「事实自身的分词长度」决定，那么给某条加中性填充就应当把它推到后面。
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

#: 中性填充：不含任何题目的判别词（不写「大小写」「千分位」「截断」「真值」）
FILL = "这条惯例长期有效，遇到同类问题一律照此处理。"
ROWS = [json.loads(x) for x in
        (ROOT / "bench/heldout/g7j_n4_heldout.jsonl").read_text(encoding="utf-8").splitlines()
        if x.strip()]


def order(m: MemoryStore, prompt: str) -> list[str]:
    return [next((f"ZK{i}" for i in (1, 2, 3, 4) if f"（ZK{i}）" in f.text), "?")
            for f in m.recall(prompt, k=4)]


def run(pad_target: int, n_fill: int) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="v90len_"))
    m = MemoryStore(tmp / "memory.db")
    for i, x in enumerate(ZK, start=1):
        m.commit_fact(x + (FILL * n_fill if i == pad_target else ""),
                      source="abstract", confidence=0.6)
    label = "对照" if n_fill == 0 else f"给 ZK{pad_target} 追加 {n_fill} 句（+{len(FILL)*n_fill} 字）"
    print(f"\n## {label}")
    for r in ROWS:
        seq = order(m, r["prompt"])
        print(f"   {r['id']:22s} {'→'.join(seq)}   首位={seq[0]}")
    m.close()


def main() -> None:
    print("# 位序对「事实自身长度」的敏感性（只改数据，不改代码）")
    run(1, 0)
    for n in (1, 2, 3):
        run(1, n)
    print("\n# 反向验证：把 ZK2 加长，ZK2 应当掉出第 1（对 g7e1/g7e2 的位序不应受影响）")
    for n in (1, 2):
        run(2, n)


if __name__ == "__main__":
    main()
