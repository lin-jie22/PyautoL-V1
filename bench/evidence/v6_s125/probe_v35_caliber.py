#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V35 口径合法性：同一实验臂的 r1 与 rk，除了 `memory_seed` 还有没有别的差异？

为什么必须单独量这一件事（PREREG §1 的 Q4）：
  `Caliber.same_as()` = `not diff()`，而 `diff()` 按 `CALIBER_KEYS` 全字段比，
  `memory_seed` 在 `CALIBER_KEYS` 里（`mw/bench.py:132`），且有契约钉着
  「只有记忆基线不同 ⇒ 口径不同」（`tests/bench_test.py:130` A8）。
  而**累积实验的设计本身**就是让记忆基线增长 ⇒ 同一臂的 r1 与 r2
  **按定义**就不是 `same_as`。

  ⇒ 若拿 `same_as()` 当可比性判据，**累积实验在制度上永远不可比**，
    十五轮的跨轮比较全部踩在这个雷上。
  `Caliber.diff_except(other, declared)` 是同一个文件里给出的第三条路：
  **声明哪些字段允许不同，剩下的任何一个不同都算失败**。
  本探针就是去量「声明 `memory_seed` 之后，意外差异是不是真的为空」。

零 LLM、零内核改动。只读 /tmp 下已冻结的 `bench_*/runs.jsonl`。

用法: probe_v35_caliber.py [root]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(V1))
from mw.bench import CALIBER_KEYS                        # noqa: E402

RUNNAME = re.compile(r"v\d+")
TAG = re.compile(r"bench_(?P<arm>.+)_r(?P<rnd>\d+)")


def canon(v) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)


def cal_of(p: Path) -> dict | None:
    f = p / "runs.jsonl"
    if not f.exists():
        return None
    rows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not rows:
        return None
    return rows[-1].get("caliber") or {}


def diffkeys(a: dict, b: dict) -> list[str]:
    return [k for k in CALIBER_KEYS if canon(a.get(k)) != canon(b.get(k))]


def main(argv: list[str]) -> int:
    root = Path("/tmp")
    for a in argv[1:]:
        if not a.startswith("--"):
            root = Path(a)

    dirs = sorted((d for d in root.iterdir()
                   if d.is_dir() and RUNNAME.fullmatch(d.name)),
                  key=lambda p: int(p.name[1:]))

    n_pairs = 0
    n_seed_only = 0
    n_other = 0
    others: list[str] = []
    seed_seq: dict[str, list] = {}
    rows_out: list[str] = []

    for d in dirs:
        arms: dict[str, dict[int, dict]] = {}
        for sub in sorted(d.glob("bench_*")):
            m = TAG.fullmatch(sub.name)
            if not m:
                continue
            c = cal_of(sub)
            if c is None:
                continue
            arms.setdefault(m["arm"], {})[int(m["rnd"])] = c
        for arm, rounds in sorted(arms.items()):
            if len(rounds) < 2:
                continue
            base_r = min(rounds)
            base = rounds[base_r]
            seed_seq[f"{d.name}/{arm}"] = [rounds[r].get("memory_seed") for r in sorted(rounds)]
            for r in sorted(rounds):
                if r == base_r:
                    continue
                cur = rounds[r]
                dk = diffkeys(base, cur)
                declared = [k for k in dk if k == "memory_seed"]
                unexpected = [k for k in dk if k != "memory_seed"]
                n_pairs += 1
                if not unexpected:
                    n_seed_only += 1
                else:
                    n_other += 1
                    others.append(f"{d.name}/{arm} r{base_r}→r{r}: {unexpected}")
                rows_out.append(
                    f"  {d.name:6s}/{arm:5s} r{base_r}→r{r}  "
                    f"diff={dk if dk else '[]'}  意外={unexpected if unexpected else '[]'}  "
                    f"seed {base.get('memory_seed')}→{cur.get('memory_seed')}")

    print("=" * 78)
    print(f"V35 口径合法性  root={root}  比较对 {n_pairs}")
    print("=" * 78)
    print("\n--- ① 逐臂逐轮：全字段 diff 与「豁免 memory_seed 后的意外差异」---")
    for row in rows_out:
        print(row)

    print("\n--- ② 判决 ---")
    print(f"  意外差异为空（只差 memory_seed） : {n_seed_only}/{n_pairs}")
    print(f"  存在意外差异                     : {n_other}/{n_pairs}")
    if others:
        print("  明细：")
        for o in others:
            print("    ✗ " + o)

    print("\n--- ③ memory_seed 序列（每个臂各轮起跑前的 (facts, episodes)）---")
    for k, seq in sorted(seed_seq.items()):
        print(f"  {k:14s} {seq}")

    print("\n" + "=" * 78)
    print("⛔ 本脚本只输出事实。判决见 PREREG.md §3。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
