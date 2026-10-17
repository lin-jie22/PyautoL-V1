#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【假记忆】臂 —— 给 §16.14 那个 "+0.2000 跨任务增益" 补的安慰剂（§17-14）。

★ 它要回答的唯一问题
  第十五轮测到：同一套 5 道题，让它们**共用一份累积记忆**，总分从 0.5600
  涨到 0.7600（**+0.2000**）。当时的两个解释是：

    (a) 它真的从前面几题里学到了东西；
    (b) 只是"上下文里多了一堆前面题的文字"，改变了贪心解码的路径。

  那一轮用「把受益题换到第 1 题」的排序对照证明了 **位置** 那一半属于 (b)。
  但排序对照**证不了存在性**那一半 —— 因为它同时删掉了"前面的题"，
  而不是把"前面的题"换成**等量的假东西**。

  ⇒ 本文件造的就是那个"假东西"：**条数相同、长度相同、一切结构相同，
    只有内容变成假的**。若 (b) 成立，E1/E2 应当复现 E3 的绝大部分涨幅。

★ ★ 为什么是"在写入瞬间替换"而不是"跑完之后改库"
  累积臂是**同一个进程、同一个 Core** 逐题跑的：第 i 题答完立刻把答案
  写进 episodes / facts，第 i+1 题起跑时就读得到。
  ⇒ 事后再改库对第 i 题毫无影响 —— 那等于重新做一遍完全不同的实验；
  而且第二轮的右键缺陷就是这么来的（改完了历史还在）。

  所以在 patch 里替换：**真答案一次都没有真正进过记忆**。
  E1/E2 与 E3 走的是**同一段进程、同一套调用、同一批 dyst 数**，
  唯一变量就是"写进去的那串字符"。

★ ⛔ 三条硬不变量（违反就抛异常终止，绝不静默）
  1. **等长**：`len(new) == len(old)`。长度一旦不等，
     "多出来的那几个字"就变成第二个自变量，实验当场失去归因能力。
  2. **不等于原文**：`new != old`。等于原文就不是安慰剂，那是 E3 本人。
  3. **不修改内核**：本文件**只在进程内 monkeypatch**，
     `mw/` 下任何一行都不动 —— patch 必须能被 grep 证明不在内核里
     （契约 AP1 盯着）。

★ 用法
    # E1 假原文：逐字符双射替换（等长、保留全部格式结构）
    python3 bench/fake_mem.py E1 --bench --solver llm \
        --suite bench/tasks/struct_convert.jsonl --memory-dir /tmp/x/mem

    # E2 打乱：同字符多重集，只摧毁语序
    python3 bench/fake_mem.py E2 --bench ... 同上

    # E3 真原文：直接 `run.py`（本 runner 不该参与，见下方 E3 拒绝）
    python3 run.py --bench ...

  ⛔ 本 runner **拒绝** ARMS ∈ {E0, E3}：
     E0 是无记忆（`--fresh`）、E3 是正对照，两者都必须跑**没有被 patch 过**
     的进程。让它们也经过这里，就等于承认"我分不清这个进程有没有被改过"。

返回值：透传 run.main() 的返回码；违反不变量时直接抛异常（Traceback 是**特性**，
      它意味着"这一臂的数据作废"，而不是"假装无事发生"）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "bench"))     # 复用 placebo.scramble

from placebo import scramble                  # noqa: E402

from mw.memory import MemoryStore             # noqa: E402

#: 允许在本 runner 里使用的臂。
#: E0（空）/ E3（真原文）**刻意不在其中** —— 它们必须跑**没被 patch 过**的
#: 进程，否则"这个数有没有被改过"就没法从外部证明。
#: `T` 是**只读探针**：装一样的包装、一样追踪，但 mapper 恒等。它同时回答
#: 两件原本要靠嘴说的事 —— ①装测量本身会不会改变读数（T 必须贴近 E3）；
#: ②E3 的题边界残留究竟多少（E3 不能被 patch，写不出这段账）。
ARMS = ("E1", "E2", "T")

# ═══════════════════════════════════════════════════════════════════════
# 统计与追踪
# ═══════════════════════════════════════════════════════════════════════
_STATS: dict = {
    "records": 0,          # 替换过的 episode 条数
    "facts": 0,            # 替换过的 fact 条数
    "chars": 0,            # 替换涉及的总字符数
    "by_kind": {},         # 按 episode kind 计数
    "examples": [],        # 前若干条原文→新文（含长度），事后可核对
    "violations": [],      # 违反不变量的记录（非空 ⇒ 数据作废）
    "identity_ok": 0,      # T 臂：逐条验过"确实没变换"的次数
    "working_prev_mapped": 0,  # 被替换掉的"上一题残留工作记忆"条数（泄漏通道）
    "blank_identity": 0,   # 放行"空/纯空白允许恒等"的次数（见 _check 的注释）
    "task_boundary": [],   # ★题边界追踪：每题起跑时 working deque 里残留几条
}


def _check(old: str, new: str, where: str) -> str:
    """逐条校验等长与相异。返回 new（便于链式调用）。

    ⛔ 唯一的例外，必须写窄：当 `old` **除空白外没有任何字符**时允许 `new == old`。
      理由：这种字符串本身没有内容可替换 —— 它的存在性效应与"写的是什么"无关，
      拿它去卡"必须相异"只会让整个臂因为一条空记录崩掉。
      ⇒ 但这个口子必须**可核对**：每次放行都在 `blank_identity` 里计一次数，
        契约 AP2 要求它只能出现在 `strip()` 为空的情形。
    """
    if len(new) != len(old):
        msg = (f"{where}: 长度不等 {len(old)}→{len(new)} "
               f"（差 {len(new)-len(old):+d}）")
        _STATS["violations"].append(msg)
        raise AssertionError(
            f"❌ {msg}\n"
            f"   原文：{old[:200]!r}\n   新文：{new[:200]!r}\n"
            f"   ⛔ 不补空格、不截断。这条数据作废，不是下调某个阈值继续用。")
    if new == old:
        if old.strip() == "":
            _STATS["blank_identity"] += 1     # 唯一允许的例外，留痕可核
            return new
        msg = f"{where}: 替换后与原文完全相同"
        _STATS["violations"].append(msg)
        raise AssertionError(f"❌ {msg}（{old[:200]!r}）—— 那是 E3 本人，不是安慰剂。")
    return new


# ═══════════════════════════════════════════════════════════════════════
# 两条 mapper
# ═══════════════════════════════════════════════════════════════════════
_DIGIT = str.maketrans("0123456789", "5274019683")
_LOWER = str.maketrans("abcdefghijklmnopqrstuvwxyz", "qwertyuiopasdfghjklzxcvbnm")
_UPPER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "QWERTYUIOPASDFGHJKLZXCVBNM")
_CJK_LO, _CJK_HI = 0x4E00, 0x9FFF
_CJK_SHIFT = 3019          # 与区间长度互素即可保证是双射


def cipher(text: str) -> str:
    """逐字符**双射**替换：长度与格式结构逐位不变，语义归零。

    为什么要逐字符而不是"另写一句等长的话"：
      那样会同时改变**换行位置、缩进、标点分布**。而此刻被替换的是前面
      几道题的答案 —— 那是**代码**，缩进和换行本身就是它的结构。
      逐字符替换保留了一切几何信息（第 7 行第 5 列仍是一个空格），
      只把每一个字换成另一个字 ⇒ 剩下唯一的区别就是"字本身"。

    CJK 在区间内循环移位（双射、确定性）；其余字符（含所有空白标点）
    **原样保留** —— 一旦连它们也换，"结构相同"这个承诺就破了。
    """
    out = []
    for ch in text:
        if ch.isdigit():
            out.append(ch.translate(_DIGIT))
        elif ch.isascii() and ch.isalpha():
            out.append(ch.translate(_LOWER if ch.islower() else _UPPER))
        elif _CJK_LO <= ord(ch) <= _CJK_HI:
            span = _CJK_HI - _CJK_LO + 1
            out.append(chr(_CJK_LO + (ord(ch) - _CJK_LO + _CJK_SHIFT) % span))
        else:
            out.append(ch)
    return "".join(out)


_MAPPERS = {"E1": cipher, "E2": (lambda s: scramble(s, 17))}

#: T 臂：mapper 恒等，且**必须**逐条证明它没改动任何东西。
#: 这不是装饰 —— 只有靠它，才能把"装上测量装置不改变被测对象"
#: 从一句保证变成一次测量（T 的分数必须贴近未被 patch 的 E3）。
TRACE_ONLY = "T"

#: 工作记忆这条通道的三种处理档次（见 `install` 的 docstring）。
WORKING_MODES = ("prev", "all", "none")


# ═══════════════════════════════════════════════════════════════════════
# 安装 patch
# ═══════════════════════════════════════════════════════════════════════
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
    if arm == TRACE_ONLY:
        mapper = None
    else:
        mapper = _MAPPERS[arm]

    #: 输入 → 输出。用途只有一个：**确定性**。
    #: mapper 里一旦掺了时间、随机数或字典遍历顺序，同一段原文在
    #: 两道题里就会变成两个不同的"假记忆"，而那正是我们要排除的变量。
    _memo: dict[str, str] = {}

    def _wrap(s: str, where: str) -> str:
        """唯一的替换出口。**每一次写入都要过这里** —— 漏掉一处就等于
        有一部分真原文绕过了安慰剂，实验当场失去归因能力。"""
        new = s if mapper is None else _check(s, mapper(s), where)
        if mapper is None:
            # 只读探针：不做变换，改验"确实没变换"。这一条若破，
            # T 就不再是 E3 的替身，整套对照的设置失去意义。
            if new != s:
                raise AssertionError(f"❌ T 臂竟产生了变换：{s[:80]!r}")
            _STATS["identity_ok"] += 1
        prev = _memo.get(s)
        if prev is not None and prev != new:
            raise AssertionError(
                f"❌ mapper 不确定：同一段原文两次映射出不同结果\n"
                f"   前：{prev[:120]!r}\n   后：{new[:120]!r}\n"
                f"   ⇒ 两道题看的不是同一个'假记忆'，本臂作废。")
        _memo[s] = new
        _STATS["chars"] += len(s)
        if len(_STATS["examples"]) < 3:
            _STATS["examples"].append({"where": where, "old": s[:300],
                                       "new": new[:300], "len": len(s),
                                       "identity": new == s})
        return new

    if fake_working not in WORKING_MODES:
        raise SystemExit(f"❌ --fake-working 只接受 {WORKING_MODES}，收到 {fake_working!r}")
    _STATS["fake_working"] = fake_working

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
                    _STATS["working_prev_mapped"] += 1
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
            _STATS["task_boundary"].append({
                "goal": content[:60],
                "working_residue": len(self.working),
                "sample": [t[1][:40] for t in list(self.working)[-2:]],
            })
            _own.clear()      # 从这一刻起 push 的才算"本题的"
        new = _wrap(content, f"episode/{kind}")
        _STATS["records"] += 1
        _STATS["by_kind"][kind] = _STATS["by_kind"].get(kind, 0) + 1
        return _orig_record(self, kind, new, **meta)

    def commit_fact(self, text: str, *, source: str, confidence: float):
        new = _wrap(text, f"fact/{source}")
        _STATS["facts"] += 1
        return _orig_fact(self, new, source=source, confidence=confidence)

    MemoryStore.record = record
    MemoryStore.commit_fact = commit_fact


def dump(arm: str, rc: int) -> None:
    """把统计落到 `$V1_BENCH_DIR/fakemem_stats.json`——事后可核对，不接受口头保证。"""
    _STATS["arm"] = arm
    _STATS["rc"] = rc
    _STATS["mappers"] = sorted(_MAPPERS)
    d = os.environ.get("V1_BENCH_DIR", "")
    dest = (Path(d) / "fakemem_stats.json") if d else (_ROOT / "fakemem_stats.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(_STATS, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("=" * 70)
    print(f"【假记忆 {arm}】替换统计（{dest}）")
    print(f"  episodes {_STATS['records']} 条 / facts {_STATS['facts']} 条 "
          f"/ 共 {_STATS['chars']} 字符")
    print(f"  按 kind：{_STATS['by_kind']}")
    _mode = _STATS["fake_working"]
    if _mode == "prev":
        print(f"  工作记忆档次 = prev（替换掉上一题残留 "
              f"{_STATS['working_prev_mapped']} 条，本题草稿纸原样）")
    else:
        print(f"  工作记忆档次 = {_mode}")
    if _STATS["identity_ok"]:
        print(f"  T 只读探针：{_STATS['identity_ok']} 条写入**逐条验过未改一个字**")
    print(f"  空/纯空白放行恒等 {_STATS['blank_identity']} 次（有内容者一律必须相异）")
    print("  题边界（每题起跑时 deque 里残留几条上一题的东西）:")
    for b in _STATS["task_boundary"]:
        print(f"    - {b['goal'][:44]:44s} 残留={b['working_residue']:>3d} "
              f"尾两条={b['sample']}")
    print(f"  ⛔ 不变量违反 {len(_STATS['violations'])} 条"
          f"{'（本臂数据有效）' if not _STATS['violations'] else '（**本臂数据作废**）'}")
    print("=" * 70)


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2
    arm = sys.argv[1]
    rest = sys.argv[2:]

    fake_working = "prev"
    rest = sys.argv[2:]
    for i, a in enumerate(rest):
        # ⛔ 必须从传给 run.py 的参数里摘掉 —— 否则 argparse 会把它当未知参数。
        #   早先这里写的是 `if "--fake-working" in rest`（布尔开关），
        #   而 install 现在要三个档 ⇒ 开关与值必须一次摘干净，不能只摘前者。
        if a == "--fake-working":
            if i + 1 >= len(rest):
                print(f"❌ --fake-working 需要一个值：{WORKING_MODES}")
                return 2
            fake_working = rest[i + 1]
            if fake_working not in WORKING_MODES:
                print(f"❌ --fake-working 只接受 {WORKING_MODES}，收到 {fake_working!r}")
                return 2
            rest = rest[:i] + rest[i + 2:]
            break
        if a.startswith("--fake-working="):
            fake_working = a.split("=", 1)[1]
            if fake_working not in WORKING_MODES:
                print(f"❌ --fake-working 只接受 {WORKING_MODES}，收到 {fake_working!r}")
                return 2
            rest = rest[:i] + rest[i + 1:]
            break

    install(arm, fake_working=fake_working)
    print(f"[fake_mem] 臂={arm} mapper={arm} working={fake_working}")

    # sys.argv[1:] 会被 run.main() 的 argparse 读到 ⇒ 先卸掉已被我吃掉的前两项
    sys.argv = [sys.argv[0]] + rest
    import run                                                       # noqa: E402
    rc = 0
    try:
        rc = run.main()
    finally:
        # 即使中途炸了也要落盘 —— 那时 violations 可能非空，
        # 而"这一臂到底有没有被污染过"正是事后最需要核对的东西。
        dump(arm, rc)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
