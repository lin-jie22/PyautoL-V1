#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`python3 -m bench.placebo` —— 把一个记忆库的某一层整层替换成安慰剂。

原来是 `bench/placebo.py`（第十七轮）；本轮并进 `bench/placebo/` 包，
**命令行语义一字未改**，只多了一个 `--variant reshape`（§17-15 的形状臂）。

    # D1 打乱：原文字符的一个排列（同字多重集、零语义、必然等长）
    python3 -m bench.placebo --src /tmp/x/mem --out /tmp/x/mem_D1 \\
            --variant scramble --seed 17

    # D2/D3：调用方指定等长文本
    python3 -m bench.placebo --src /tmp/x/mem --out /tmp/x/mem_D2 \\
            --variant text --text '编译报错时先读最底层栈信息'

    # F：保字符序列、破行几何
    python3 -m bench.placebo --src /tmp/x/mem --out /tmp/x/mem_F --variant reshape

返回值：0 成功；非 0 一定是**把理由念出来之后**才退的（R41：无静默兜底）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):                       # 极端情况：被当脚本直接执行
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from bench.placebo.mappers import MAPPER_NAMES, md5, reshape, scramble  # noqa: E402
from bench.placebo.store import (ABSTRACT_SOURCE, cleanup_on_failure,   # noqa: E402
                                 copy_dir, replace_texts)

#: 磁盘层可用的变体：两种 mapper + 调用方手写的等长文本
VARIANTS = ("scramble", "text", "reshape")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="把记忆库的某一层换成安慰剂，产出一份新的记忆目录")
    p.add_argument("--src", required=True, help="源记忆目录（只读，不会被改）")
    p.add_argument("--out", required=True, help="新目录（必须不存在）")
    p.add_argument("--variant", required=True, choices=VARIANTS,
                   help="scramble=打乱原文字符；text=用 --text 指定的等长文本；"
                        "reshape=保字符序列、破行几何")
    p.add_argument("--text", default="", help="--variant text 时的替换文本")
    p.add_argument("--seed", type=int, default=17, help="打乱用的种子")
    p.add_argument("--source", default=ABSTRACT_SOURCE,
                   help=f"要替换的那一层（fact.source，默认 {ABSTRACT_SOURCE!r}）")
    p.add_argument("--allow-len-delta", type=int, default=0,
                   help="允许的字符数偏差（默认 0 = **严格等长**；"
                        "给非 0 只用于诊断，实验时必须为 0）")
    args = p.parse_args(argv)

    src = Path(args.src).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()

    if not src.is_dir():
        print(f"❌ 源目录不存在：{src}")
        return 2
    if not (src / "memory.db").exists():
        print(f"❌ 源目录里没有 memory.db：{src}")
        return 2
    if args.variant == "text" and not args.text.strip():
        print("❌ --variant text 必须给 --text")
        return 2
    if args.variant != "text" and args.text:
        print("❌ 给了 --text 却没选 --variant text（防止写了文本却没生效）")
        return 2

    copy_dir(src, out)

    def fn(old: str) -> str:
        if args.variant == "scramble":
            return scramble(old, args.seed)
        if args.variant == "reshape":
            return reshape(old, args.seed)
        return args.text.strip()

    try:
        pairs = replace_texts(out / "memory.db", fn, source=args.source,
                              allow_len_delta=args.allow_len_delta)
    except SystemExit as e:
        print(e)
        if args.variant == "reshape":
            # 诚实地说出这个变体的**盲区**，而不是让人对着
            # "替换后与原文相同"猜发生了什么。
            print("   ⚠️ reshape 的已知盲区：既**没有换行**又**没有空白**的行"
                  "\n      没有任何可挪的结构 ⇒ 对这个变体无定义。"
                  "\n      若这一层全是这种文本（如无空格的中文短句），"
                  "\n      请改用进程内 F 臂（bench/fake_mem.py F），"
                  "\n      它会把这类行**计数**后放行，并在统计里明示。")
        cleanup_on_failure(out)
        return 1

    print(f"源：{src}")
    print(f"产：{out}")
    print(f"变体：{args.variant}（目标层 source={args.source!r}）"
          + (f" seed={args.seed}" if args.variant != "text" else ""))
    print(f"替换 {len(pairs)} 条（等长约束 ±{args.allow_len_delta}）：")
    for old, new in pairs:
        print(f"   {len(old):>3d}字  {old}")
        print(f"   {len(new):>3d}字  {new}   md5 {md5(old)} → {md5(new)}")
    delta = sum(len(n) - len(o) for o, n in pairs)
    print(f"总字符差 {delta:+d}"
          f"（必然为 0 —— 它是上面那条**逐条**闸的推论，不是第二把锁）")
    # ⛔ 刻意**不**在这里再写一遍 `if delta != 0: return 1`。
    #   撤掉实现才知道原因：早先这里有两道闸（逐条 + 净差），
    #   撤回态验证时删掉逐条那一道，AO4 竟然**仍是绿的** —— 净差闸把测试挡住了。
    #   ⇒ 一把 assertion 永远测不到另一把 assertion 是不是还在。
    #   V5 迁移过来的第一条硬规则就是这个：**同一事实只声明一处**。
    #   这里只留一处闸门，净差降级为**读数**（读了不等于又判了一次）。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
