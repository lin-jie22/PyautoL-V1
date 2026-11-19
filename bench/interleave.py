#!/usr/bin/env python3
"""交错调度：把「臂」与「时刻」解耦（V212 §280）。

⛔ 为什么非要有这个东西：

V211 实测**同一配置**的 `off` 臂跨两小时差 **0.2833**（`t01[1..3]` 25/60 → 42/60，
Fisher **p=0.0031**），比它想测的任何效应都大。而 V209/V210 的三臂都是
「A 跑完 27 分钟 → B 跑 27 分钟 → D 跑 24 分钟」⇒ **臂与时刻完全混杂**，
读数分不清「评审的作用」与「这一小时模型的脾气」。那一轮的两个方向的结论
（V209「咬两边」、V210「损害不复现」）都是这个混杂的产物。

修法不是「再跑一次对照」（那只是把混杂变成两个混杂），而是**让同一块的
两个臂在时间上相邻**：块内相邻的两次运行共享几乎同一个时刻，
而块与块之间不管怎么漂，漂移是两个臂一起漂的。

⛔ 块内顺序**必须轮转**，不能恒为 `arms` 的原序。恒序时「位置」是第三个
   混淆变量：先跑的臂总在同一块条件下起跑。轮转后，每 `n` 块里每个臂在
   每个位置各出现一次 ⇒ 位置效应在块集合上被平掉（锚 AZ158c）。

⛔ 这不是「新的尺子」：调度器只决定**谁先跑**，不碰任何读数、判据、聚合。
   它唯一的产物是一张 `块/位置/臂` 的表，而这张表可以被离线核对
   （`--check`）——「实际跑的顺序与表逐行相同」是装置正面路径的前提。
"""

from __future__ import annotations

import argparse
import sys

#: ⛔ 受控词表：块的顺序只有一种造法（左移 k 位）。想加第二种必须先在这里登记，
#: 否则「同一份 schedule 能被两个实现造出来」就成了两条事实。
ROTATE = "rotate"


def schedule(arms: "list[str] | tuple[str, ...]", blocks: int) -> list[list[str]]:
    """返回 `blocks` 个块，块 k 的顺序 = `arms` 左移 k 位。

    ⭐ `pos(块 k, 臂 i) == (i - k) % n` —— 这条等式就是「轮转」的全部内容，
    也是锚 AZ158c 直接断言的形状。⛔ 不要用「相邻两块不同」来替代它：
    那对 n≥3 只是必要条件，`[a,b,c] / [b,c,a] / [a,b,c]` 也满足，但位置没平掉。
    """
    arms = list(arms)
    n = len(arms)
    if n == 0:
        raise ValueError("arms 为空：没有臂就没有块")
    if len(set(arms)) != n:
        raise ValueError(f"arms 里有重复名 {arms!r} ⇒ 块内会出现同一个臂两次")
    if int(blocks) < 1:
        raise ValueError(f"blocks={blocks!r}：至少要一块")
    return [[arms[(i + k) % n] for i in range(n)] for k in range(int(blocks))]


def flat(arms: "list[str] | tuple[str, ...]", blocks: int) -> list[tuple[int, int, str]]:
    """摊平成 `(块号, 块内位置, 臂名)`，顺序就是**该被执行的顺序**。"""
    return [(k, pos, a)
            for k, block in enumerate(schedule(arms, blocks))
            for pos, a in enumerate(block)]


def check(arms: "list[str] | tuple[str, ...]", blocks: int,
          rows: "list[tuple[int, int, str]]") -> list[str]:
    """核对一张已经落盘的表。返回**问题清单**（空 = 通过）。

    ⚠️ 为什么核对的是「表」而不是重新调用 `schedule()`：跑前核对的对象是
    **即将执行的那张表**（手抄、截断、少一行都会在这里暴露），
    重新算一遍只能证明函数是纯的。
    """
    arms = list(arms)
    n = len(arms)
    bad: list[str] = []
    want = flat(arms, blocks)
    if len(rows) != len(want):
        bad.append(f"行数 {len(rows)} ≠ 应为 {len(want)}（{blocks} 块 × {n} 臂）")
    for i, (got, exp) in enumerate(zip(rows, want)):
        if tuple(got) != tuple(exp):
            bad.append(f"第 {i} 行 {tuple(got)!r} ≠ 应为 {tuple(exp)!r}")
            if len(bad) >= 5:
                bad.append("…（只报前 5 处）")
                break
    return bad


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(
        description="交错调度表（V212）：块内轮转，把臂与时刻解耦")
    ap.add_argument("--arms", required=True, help="逗号分隔的臂名，顺序=块 0 的顺序")
    ap.add_argument("--blocks", type=int, required=True, help="块数（每块每臂一次）")
    ap.add_argument("--check", default="", help="核对这张已存在的 TSV，而不是打印新表")
    a = ap.parse_args(argv)
    arms = [x.strip() for x in a.arms.split(",") if x.strip()]
    if a.check:
        rows = []
        with open(a.check, encoding="utf-8") as f:
            for ln in f:
                if not ln.strip() or ln.startswith("#"):
                    continue
                c = ln.rstrip("\n").split("\t")
                rows.append((int(c[0]), int(c[1]), c[2]))
        bad = check(arms, a.blocks, rows)
        if bad:
            print("调度表核对  FAIL")
            for b in bad:
                print("   ", b)
            return 1
        print(f"调度表核对  OK  {len(rows)} 行 = {a.blocks} 块 × {len(arms)} 臂")
        return 0
    for k, pos, arm in flat(arms, a.blocks):
        print(f"{k}\t{pos}\t{arm}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
