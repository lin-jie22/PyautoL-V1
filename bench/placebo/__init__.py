#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""等长安慰剂 —— **机制层**的单一入口（DESIGN §17-16）。

★ 这个包合并了什么、没合并什么
  合并：三种 mapper（`mappers.py`）、磁盘层整层替换（`store.py`）、
        进程内"写入即替换"安装器（`patch.py`）。
  不合并：**判据**。三个问题各有各的判读实现，各自躺着：
        `bench/probe_placebo.py`（§16.16 抽象层）、
        `bench/probe_fakemem.py`（§16.17 假记忆）、
        `bench/probe_shape.py`（§17-15 形状）。
        第十八轮的真实教训就是"判读实现有两份 ⇒ 立刻漂"，
        所以合并只能合并**机制**。

★ 为什么必须是一个包而不是 `bench/placebo.py`
  第十七轮写了一个 `placebo.py`，第十八轮又写了一个 `fake_mem.py` ——
  两次都在做同一件事：等长闸门 + 索引同步 + 撤回校验。
    同一个约束被声明两遍，就一定会有一遍先烂（通则第 2 条）。

★ 用法
    # 磁盘层（跑完之后整层替换）
    python3 -m bench.placebo --src /tmp/x/mem --out /tmp/x/mem_D1 \\
            --variant scramble --seed 17

    # 进程内（写入瞬间替换）—— 见 bench/fake_mem.py，它只是本包的一层薄壳
    from bench.placebo.patch import install, dump_stats

⚠️ 只许用 `bench.placebo.*` 这一种导入写法。若某处写 `import placebo`
   （把 `bench/` 自己放进 sys.path），同一个模块会被加载成两个对象 ——
   两个 `STATS`、两个 `install`，而**不会报任何错**。契约 AQ5 钉住这一点。
"""

from .mappers import (MAPPER_NAMES, cipher, fixed_text, md5, reshape,
                      scramble)
from .store import ABSTRACT_SOURCE, copy_dir, replace_texts

__all__ = [
    "ABSTRACT_SOURCE", "MAPPER_NAMES", "cipher", "copy_dir", "fixed_text",
    "md5", "replace_texts", "reshape", "scramble",
]
