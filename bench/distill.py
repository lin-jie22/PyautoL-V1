#!/usr/bin/env python3
"""把一个记忆库里的 episodic 原文蒸馏成**抽象经验**（`mw.abstractor.distill`）。

为什么是独立脚本而不是塞进 `run.py`：蒸馏是**训练期的收尾动作**，
不是被测对象的一部分。它在打分**之后**跑，既不写账本、也不改分数，
唯一产物是语义记忆里的若干条 `source=abstract` 的事实。

用法：
    python3 bench/distill.py --memory-dir /tmp/xxx/mem
    python3 bench/distill.py --memory-dir /tmp/xxx/mem --show     # 顺带把条目打出来

⛔ 它必须把 DistillStats **念出来**（`--show` 之外也念）——
   一次静默的"蒸馏完成"等于承认我不知道它到底提炼出了什么。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mw import config as cfg                                   # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="把 episodic 原文蒸馏成抽象经验")
    p.add_argument("--memory-dir", required=True,
                   help="记忆目录（V1_STATE_DIR 会指向它的父目录）")
    p.add_argument("--show", action="store_true", help="把提炼出的条目逐条打印")
    p.add_argument("--max-tokens", type=int, default=160)
    args = p.parse_args(argv)

    import os
    d = Path(args.memory_dir).expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    os.environ["V1_STATE_DIR"] = str(d)

    from mw.abstractor import ABSTRACT_SOURCE, distill
    from mw.llm import build_llm
    from mw.memory import MemoryStore

    s = cfg.settings()
    mem = MemoryStore(s.paths.db)
    llm = build_llm(s.llm)

    before = mem.stats()
    print(f"记忆库 {d}")
    print(f"  蒸馏前 facts={before.get('facts', '?')} "
          f"episodes={before.get('episodes', '?')}")

    st = distill(mem, llm, max_tokens=args.max_tokens)
    after = mem.stats()
    print(f"  {st.as_line()}")
    print(f"  蒸馏后 facts={after.get('facts', '?')}")

    if st.examples_dropped_code:
        print("  ⚠️ 被当成代码拒收的样本（前几条）：")
        for ex in st.examples_dropped_code:
            print(f"     · {ex}")

    if args.show:
        rows = [f for f in _all_facts(mem) if f.source == ABSTRACT_SOURCE]
        print(f"  抽象层 {len(rows)} 条：")
        for f in rows:
            print(f"     · {f.text}")

    # ⛔ 一条都没提炼出来 ⇒ 这不是成功。抽象臂若读到空层，
    #    实验就退化为"和空记忆比"，测得的是噪声而不是抽象能力。
    return 0 if st.kept else 1


def _all_facts(mem):
    """取全部 facts（不限 k）。"""
    out = []
    for r in mem.conn.execute(
            "SELECT id, text, source, confidence, hits, ts FROM facts "
            "ORDER BY id"):
        out.append(mem._fact(r, 0.0))
    return out


if __name__ == "__main__":
    raise SystemExit(main())
