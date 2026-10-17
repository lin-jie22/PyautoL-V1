#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""进程内层：给 `MemoryStore` 装一个"写入即替换"的假记忆。

★ 为什么必须是"写入瞬间替换"而不是"跑完之后改库"
  累积臂是**同一个进程、同一个 Core** 逐题跑的：第 i 题答完立刻把答案
  写进 episodes / facts，第 i+1 题起跑时就读得到。
  ⇒ 事后再改库对第 i 题毫无影响 —— 那等于重新做一遍完全不同的实验；
  而且第二轮的右键缺陷就是这么来的（改完了历史还在）。

  所以在 patch 里替换：**真答案一次都没有真正进过记忆**。
  各臂走的是**同一段进程、同一套调用、同一批 id**，
  唯一变量就是"写进去的那串字符"。

★ ⛔ 三条硬不变量（违反就抛异常终止，绝不静默）
  1. **等长**：`len(new) == len(old)`。
  2. **不等于原文**：`new != old`（唯一例外见 `_check`，且必须计数）。
  3. **不修改内核**：本文件**只在进程内 monkeypatch**，`mw/` 下一行都不动 ——
     patch 必须能被 grep 证明不在内核里（契约 AP1 盯着）。

★ 本文件是**机制**，不是判据
  哪一臂该判"存在性效应"、阈值多少、怎么归档 —— 一律在
  `bench/probe_placebo.py`（§16.16）、`bench/probe_fakemem.py`（§16.17）、
  `bench/probe_shape.py`（§17-15）里，各自一份，互不引用。
  合并机制的目的正是让判据可以各活各的。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mw.memory import MemoryStore

from .mappers import cipher, reshape, scramble

_ROOT = Path(__file__).resolve().parent.parent.parent

#: 允许在本 runner 里使用的臂。
#: E0（空记忆）/ E3（真原文）**刻意不在其中** —— 它们必须跑**没被 patch 过**
#: 的进程，否则"这个数有没有被改过"就没法从外部证明。
#:
#:   E1  cipher    保结构、灭语义          （第十八轮）
#:   E2  scramble  保字符多重集、毁语序     （第十八轮）
#:   F   reshape   **保字符序列、破行几何** （第十九轮，§17-15）
#:   T   （恒等，只读探针）                （第十八轮）
ARMS = ("E1", "E2", "F", "T")

#: 臂 → mapper。
#: ⛔ 统一签名 `(s, *, stats=None)`：调用方只有一条路径，不许"哪个臂多一个参数"
#:    —— 那正是上一版 `_wrap` 里会写出 `TypeError` 的地方。
def _m_e1(s: str, *, stats: dict | None = None) -> str:
    return cipher(s)


def _m_e2(s: str, *, stats: dict | None = None) -> str:
    return scramble(s, 17)


def _m_f(s: str, *, stats: dict | None = None) -> str:
    return reshape(s, 17, stats=stats)


MAPPERS = {"E1": _m_e1, "E2": _m_e2, "F": _m_f}

#: T 臂：mapper 恒等，且**必须**逐条证明它没改动任何东西。
#: 这不是装饰 —— 只有靠它，才能把"装上测量装置不改变被测对象"
#: 从一句保证变成一次测量（T 的分数必须贴近未被 patch 的 E3）。
TRACE_ONLY = "T"

#: 工作记忆这条通道的三种处理档次（见 `install` 的 docstring）。
WORKING_MODES = ("prev", "all", "none")

# ═══════════════════════════════════════════════════════════════════════════
# 统计与追踪
# ═══════════════════════════════════════════════════════════════════════════
STATS: dict = {
    "records": 0,          # 替换过的 episode 条数
    "facts": 0,            # 替换过的 fact 条数
    "chars": 0,            # 替换涉及的总字符数
    "by_kind": {},         # 按 episode kind 计数
    "examples": [],        # 前若干条原文→新文（含长度），事后可核对
    "violations": [],      # 违反不变量的记录（非空 ⇒ 数据作废）
    "identity_ok": 0,      # T 臂：逐条验过"确实没变换"的次数
    "working_prev_mapped": 0,  # 被替换掉的"上一题残留工作记忆"条数（泄漏通道）
    "blank_identity": 0,   # 放行"空/纯空白允许恒等"的次数
    "task_boundary": [],   # ★题边界追踪：每题起跑时 working deque 里残留几条
    # ── 以下是 F 臂（reshape）专属：它动了多少、又有什么动不了 ──────────
    "shape_lines_moved": 0,    # 真正被重新切过行宽的多行记录
    "shape_spaces_moved": 0,   # 单行记录：只挪了空白位置
    "shape_unsafe_cuts": 0,    # 没有足够"词边界" ⇒ 退化为任意位置切
    "shape_identity": 0,       # mapper 自述"无可挪结构"（唯一的恒等来源）
    "shape_identity_ok": 0,    # 经 _check 放行的恒等（必须只来自上一条）
}


def reset_stats() -> None:
    for k, v in list(STATS.items()):
        if isinstance(v, int):
            STATS[k] = 0
        elif isinstance(v, list):
            STATS[k] = []
        elif isinstance(v, dict):
            STATS[k] = {}


def _check(old: str, new: str, where: str, *, allow_identity: bool = False) -> str:
    """逐条校验等长与相异。返回 new（便于链式调用）。

    ⛔ 两个例外，都必须**写窄 + 计数**：

      ① 当 `old` 除空白外没有任何字符时允许 `new == old`。
         理由：这种字符串本身没有内容可替换 —— 它的存在性效应与"写的是什么"无关，
         拿它去卡"必须相异"只会让整个臂因为一条空记录崩掉。
         ⇒ 每次放行都在 `blank_identity` 里计一次数，契约要求它只能出现在
           `strip()` 为空的情形。

      ② `allow_identity=True`：**mapper 自己**声明"这条没有可挪的结构"
         （`reshape` 对既无换行又无空白的字符串）。
         ⇒ 由调用方从 `shape_identity` 的增量算出来，不是一张可以随手打开的通行证；
           每次放行同样计数（`shape_identity_ok`）。
         诚实地说：这是 F 臂的**盲区**，读数里必须如实报出它有多少条。
    """
    if len(new) != len(old):
        msg = (f"{where}: 长度不等 {len(old)}→{len(new)} "
               f"（差 {len(new)-len(old):+d}）")
        STATS["violations"].append(msg)
        raise AssertionError(
            f"❌ {msg}\n"
            f"   原文：{old[:200]!r}\n   新文：{new[:200]!r}\n"
            f"   ⛔ 不补空格、不截断。这条数据作废，不是下调某个阈值继续用。")
    if new == old:
        if old.strip() == "":
            STATS["blank_identity"] += 1      # 唯一允许的例外之一，留痕可核
            return new
        if allow_identity:
            STATS["shape_identity_ok"] += 1   # 第二个例外：只来自 mapper 的自述
            return new
        msg = f"{where}: 替换后与原文完全相同"
        STATS["violations"].append(msg)
        raise AssertionError(f"❌ {msg}（{old[:200]!r}）—— 那是 E3 本人，不是安慰剂。")
    return new


def install(arm: str, *, fake_working: str = "prev") -> None:
    """把假记忆装进 `MemoryStore`。**不触碰 mw/ 的任何源码文本**。

    `fake_working` 决定**工作记忆**这条泄漏通道怎么处理：
        "prev"（默认）只替换**上一题留下来的**那几条 ——
                它们是泄漏；而本题的草稿纸必须原样，否则就不是安慰剂，
                是"把这个系统弄瞎"，Δ 里会混进"看不见自己刚写的东西"。
        "all"   连本题的草稿纸也替换 —— 只作诊断用，不得据此认领 Δ。
        "none"  完全不动工作记忆 —— 用来给"这条通道到底有多大"出一个数。
    """
    if arm not in ARMS:
        raise SystemExit(
            f"❌ 本 runner 不接受 {arm!r}。\n"
            f"   E0（空记忆）与 E3（真原文）必须跑**没有 patch 过**的进程，\n"
            f"   否则'这个数有没有被改过'就没有办法从外部证明。\n"
            f"   允许：{ARMS}")
    if fake_working not in WORKING_MODES:
        raise SystemExit(f"❌ --fake-working 只接受 {WORKING_MODES}，收到 {fake_working!r}")
    STATS["fake_working"] = fake_working

    mapper = None if arm == TRACE_ONLY else MAPPERS[arm]

    #: 输入 → 输出。用途只有一个：**确定性**。
    #: mapper 里一旦掺了时间、随机数或字典遍历顺序，同一段原文在
    #: 两道题里就会变成两个不同的"假记忆"，而那正是我们要排除的变量。
    _memo: dict[str, str] = {}

    def _wrap(s: str, where: str) -> str:
        """唯一的替换出口。**每一次写入都要过这里** —— 漏掉一处就等于
        有一部分真原文绕过了安慰剂，实验当场失去归因能力。"""
        if mapper is None:
            new = s
            # 只读探针：不做变换，改验"确实没变换"。这一条若破，
            # T 就不再是 E3 的替身，整套对照的设置失去意义。
            if new != s:
                raise AssertionError(f"❌ T 臂竟产生了变换：{s[:80]!r}")
            STATS["identity_ok"] += 1
        else:
            before = int(STATS.get("shape_identity", 0) or 0)
            out = mapper(s, stats=STATS)
            no_freedom = int(STATS.get("shape_identity", 0) or 0) > before
            new = _check(s, out, where, allow_identity=no_freedom)
        prev = _memo.get(s)
        if prev is not None and prev != new:
            raise AssertionError(
                f"❌ mapper 不确定：同一段原文两次映射出不同结果\n"
                f"   前：{prev[:120]!r}\n   后：{new[:120]!r}\n"
                f"   ⇒ 两道题看的不是同一个'假记忆'，本臂作废。")
        _memo[s] = new
        STATS["chars"] += len(s)
        if len(STATS["examples"]) < 3:
            STATS["examples"].append({"where": where, "old": s[:300],
                                      "new": new[:300], "len": len(s),
                                      "identity": new == s})
        return new

    _orig_think = MemoryStore.think
    _orig_view = MemoryStore.working_view

    #: ★ **本题自己** push 进 deque 的那些条目。用对象 id 而不是内容匹配：
    #:   内容会重复（同一句报错两次），id 不会。
    #: 用它切出"上一题留下的"vs"本题写的"，精确到条。
    _own: set[int] = set()

    def think(self, text: str) -> None:
        _orig_think(self, text)
        if self.working:
            _own.add(id(self.working[-1]))

    def working_view(self) -> list[str]:
        out = []
        for item in self.working:
            _ts, txt = item
            mine = id(item) in _own
            if fake_working == "all" or (fake_working == "prev" and not mine):
                out.append(_wrap(txt, "working-mine" if mine else "working-prev"))
                if not mine:
                    STATS["working_prev_mapped"] += 1
            else:
                out.append(txt)
        return out

    MemoryStore.think = think
    MemoryStore.working_view = working_view

    _orig_record = MemoryStore.record
    _orig_fact = MemoryStore.commit_fact

    def record(self, kind: str, content: str, **meta):
        # ★题边界：`record("goal", ...)` 是每题的**第一行**写入。
        #   此刻 deque 里剩下的，就是上一题真正残留到这一题的工作记忆 ——
        #   实测（2026-10-17，stub 两题）：残留 2 条，内容是 tool ⇒ ✔ 行。
        #   而 `finish ⇒ ✔ <答案>` 装的就是上一题的答案原文。
        #   ⇒ 不处理它，安慰剂就有一条敞开的泄漏通道。
        if kind == "goal":
            STATS["task_boundary"].append({
                "goal": content[:60],
                "working_residue": len(self.working),
                "sample": [t[1][:40] for t in list(self.working)[-2:]],
            })
            _own.clear()      # 从这一刻起 push 的才算"本题的"
        new = _wrap(content, f"episode/{kind}")
        STATS["records"] += 1
        STATS["by_kind"][kind] = STATS["by_kind"].get(kind, 0) + 1
        return _orig_record(self, kind, new, **meta)

    def commit_fact(self, text: str, *, source: str, confidence: float):
        new = _wrap(text, f"fact/{source}")
        STATS["facts"] += 1
        return _orig_fact(self, new, source=source, confidence=confidence)

    MemoryStore.record = record
    MemoryStore.commit_fact = commit_fact


def dump_stats(arm: str, rc: int, *, filename: str = "fakemem_stats.json") -> Path:
    """把统计落到 `$V1_BENCH_DIR/<filename>`——事后可核对，不接受口头保证。"""
    STATS["arm"] = arm
    STATS["rc"] = rc
    STATS["mappers"] = sorted(MAPPERS) + [TRACE_ONLY]
    d = os.environ.get("V1_BENCH_DIR", "")
    dest = (Path(d) / filename) if d else (_ROOT / filename)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(STATS, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("=" * 70)
    print(f"【安慰剂 {arm}】替换统计（{dest}）")
    print(f"  episodes {STATS['records']} 条 / facts {STATS['facts']} 条 "
          f"/ 共 {STATS['chars']} 字符")
    print(f"  按 kind：{STATS['by_kind']}")
    _mode = STATS.get("fake_working", "prev")
    if _mode == "prev":
        print(f"  工作记忆档次 = prev（替换掉上一题残留 "
              f"{STATS['working_prev_mapped']} 条，本题草稿纸原样）")
    else:
        print(f"  工作记忆档次 = {_mode}")
    if STATS["identity_ok"]:
        print(f"  T 只读探针：{STATS['identity_ok']} 条写入**逐条验过未改一个字**")
    if arm == "F":
        print(f"  F 形状臂：重切行宽 {STATS['shape_lines_moved']} 条 / "
              f"挪空白 {STATS['shape_spaces_moved']} 条 / "
              f"退化切（无词边界）{STATS['shape_unsafe_cuts']} 条")
        print(f"  ⚠️ F 的盲区：无可挪结构 {STATS['shape_identity']} 条原样放行"
              f"（这 {STATS['shape_identity_ok']} 条**没有**被操纵，读数必须一起念）")
    print(f"  空/纯空白放行恒等 {STATS['blank_identity']} 次（有内容者一律必须相异）")
    print("  题边界（每题起跑时 deque 里残留几条上一题的东西）:")
    for b in STATS["task_boundary"]:
        print(f"    - {b['goal'][:44]:44s} 残留={b['working_residue']:>3d} "
              f"尾两条={b['sample']}")
    print(f"  ⛔ 不变量违反 {len(STATS['violations'])} 条"
          f"{'（本臂数据有效）' if not STATS['violations'] else '（**本臂数据作废**）'}")
    print("=" * 70)
    return dest
