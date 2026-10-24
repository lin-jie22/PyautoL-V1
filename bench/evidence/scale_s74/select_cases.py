#!/usr/bin/env python3
"""一次性工具：按「新增种类数最多」贪心选用例，并把曲线打出来（第七十四轮）。

⚠️ 它**不是**运行时依赖：产物是**那几条用例的文本**，写进
`bench/gen_spec_tasks.py` 的 `CASES`，读数由契约 AZ81/AZ82 钉住。
跑它的目的只有一个 —— 让「这几条是怎么来的」可复核，
而不是「我觉得该加密」。

用法：
    python3 bench/select_cases.py             # 打印曲线与选中的用例
    python3 bench/select_cases.py --k 20      # 多选，看曲线还在不在涨
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V1 / "bench"))

import casepower as CP                                            # noqa: E402
import gen_spec_tasks as GS                                       # noqa: E402

#: 候选池里手写的那几条。不是为了让某道题好看：空列表 / 单字符 / 折行 / 长行
#: 这类边界任何一套题都该有。共用同一套用例（不按题定制）是第七十一轮定下的规矩 ——
#: 按题挑用例，A5 就从「题合格」退化成「用例挑得好」。
HAND: list[CP.Case] = [
    [], ["x"], ["aaaaaaaa"], ["a" * 9], ["a" * 5],
    ["Alpha Beta", "ALPHA BETA", "  alpha   beta  "],
    ["same", "same", "same"],
    ["zz", "yy", "xx"], ["a b", "ab", "A B"], ["tab\tsep", "tab sep"],
]


def main() -> int:
    ap = argparse.ArgumentParser(description="贪心选用例（按新增种类数）")
    ap.add_argument("--k", type=int, default=14, help="最多追加几条")
    ap.add_argument("--pool", type=int, default=400, help="随机候选条数")
    # ⚠️ 必须能指定"从哪几条出发"：采纳之后 `GS.CASES` 已经是 14 条（已饱和），
    #    再跑这个工具就只会得到"新增 0"。默认回到第七十一轮那 7 条，
    #    这样这份日志**任何时候都能重放出同一张曲线**。
    ap.add_argument("--base-n", type=int, default=7, help="起始用例数（默认 7＝原有那批）")
    a = ap.parse_args()

    pool = CP.rand_cases(a.pool, seed=CP.SPACE_SEED) + HAND
    base = GS.CASES[:a.base_n]
    # 宇宙 = 现有用例 + 候选池 + 定义空间（必须一次建好，之后只投影）
    pw = CP.Power(GS.SPECS, list(base) + pool + CP.SPACE)
    cap = pw.ceiling()
    n0 = pw.species(base)[0]
    print(f"宇宙：现有 {len(base)} 条 + 候选 {len(pool)} 条 + 定义空间 {CP.SPACE_N} 条")
    print(f"现有 {len(base)} 条能分辨 {n0} 种错误；天花板 {cap} 种 ⇒ 还差 {cap - n0} 种")
    print()
    trail = pw.greedy(base, pool, a.k)
    print(f"{'累计条数':>8s} {'新增种类':>8s} {'种类总数':>8s}   用例")
    for step in trail:
        print(f"{step['total_cases']:8d} {step['new_species']:8d} "
              f"{step['species']:8d}   {step['case']!r}")

    saturated = [s["total_cases"] for s in trail if s["species"] >= cap]
    print()
    if saturated:
        print(f"✅ 饱和点：{saturated[0]} 条（天花板 {cap}）")
        print(f"   ⇒ 再多加用例**不会**增加区分力；「21 条」这个数字没有依据。")
    else:
        last = trail[-1]["species"] if trail else n0
        print(f"⚠️ 追加 {a.k} 条后仍未到天花板（{last}/{cap}）"
              f" —— 只能说「这些候选不够」，不能说「再多就能到」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
