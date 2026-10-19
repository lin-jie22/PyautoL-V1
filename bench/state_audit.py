#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R1 的**第二层**：state 级审计（第三十六轮 §17-7h 装置修正⑭②）。

为什么需要它（读完再改）
------------------------
7d–7g 的 R1 一直这么写：

    want in [f.text for f in seeds["H"].recall(t["prompt"], k=6)]     # ① 语义层
    want[:120] in state                                              # ② 送达

① 只回答"`recall()` 能不能取回"，② 用的是**整段** state —— 于是**任何**一条把
fact 文本送进上下文的路径都会被判成"送达"。7g 实测证明这条路不止一条：

    `recall` 工具成功 ⇒ 返回**全部** facts 的拼接（389 字）⇒ `think()` ⇒
    工作记忆（进程级 deque，不随任务边界清空）⇒ `_state()` 取每条输出**尾 200 字**

⇒ 第 4 题的 state 里出现**非本题**惯例 2/3 条，而 ① 与 ② 都报"✅"。

本模块把 ② 拆成两问，并让它们**互不掩盖**：

    own_in_semantic : 本题那条惯例是否逐字出现在 `[语义记忆]` 块里（这才是"送达"）
    outside         : 有没有**任何** fact 文本出现在**别的**块里（工作记忆/轨迹/…）

判据（写死）：`own_in_semantic and not outside`，缺一不可。

⛔ 本模块**不调模型、不读库、不改判据**：它只吃一段 state 文本。
⛔ 不许把它们合并成一个布尔（"反正都是送达"）：一旦合并，
   "经工作记忆漏进来的"就又变成不可见的 —— 那正是本轮要修的那个缺陷。
"""

from __future__ import annotations

#: state 里的块头（`core._state()` 拼块时用的就是这些字面量）。
#: ⚠️ 顺序无关：按**行首**匹配，命中谁就是谁。
BLOCK_HEADS: tuple[str, ...] = (
    "[目标]", "[工作记忆]", "[语义记忆]", "[经验]",
    "[外部评审]", "[轨迹]", "[第",
)

#: 比对用的前缀长度。为什么不是整条：语义块逐条截到 120 字，
#: 而工作记忆只留**尾 200 字** ⇒ 只有"短前缀"在两条路径上都还活着。
PROBE_CHARS = 20

_SEMANTIC = "[语义记忆]"
_EXPERIENCE = "[经验]"
#: 语义块的两个合法名字（raw / abstract 两种模式，见 `core._state`）。
_SEMANTIC_HEADS = (_SEMANTIC, _EXPERIENCE)


def state_blocks(state: str) -> dict[str, str]:
    """把 state 按块头切开，返回 `{块头: 该块全文}`（含块头本身）。

    ⚠️ 必须**逐行**扫，不能 `state.split("[工作记忆]")`：
       工作记忆里存的是工具输出原文，**本身就可能含换行**；
       按块头切字符串会把工具输出里的任意一行误当成新块的开始。
    """
    out: dict[str, str] = {}
    cur = "头"
    for line in str(state or "").splitlines(keepends=True):
        for head in BLOCK_HEADS:
            if line.startswith(head):
                cur = head
                break
        out[cur] = out.get(cur, "") + line
    return out


def _probe(text: str) -> str:
    return (text or "")[:PROBE_CHARS]


def audit_state(state: str, facts: dict[str, str], task_id: str) -> dict:
    """审计一段 state：本题惯例在不在语义块、有没有别的 fact 漏在别处。

    返回字典（**每个字段都有消费者**：探针打印 + 契约断言）：
      · `own_in_semantic`  本题惯例是否逐字出现在 `[语义记忆]`/`[经验]` 块里
      · `own_elsewhere`    本题惯例是否**同时**出现在别的块里（同一事实声明两处 = 缺陷）
      · `outside`          非本题的 fact 文本出现在别的块里的 id 列表（泄漏）
      · `outside_all`      任何一个 fact（含本题）出现在别的块里的 id 列表
      · `ok`               `own_in_semantic and not outside_all`
      · `blocks`           各块字符数（只登记，不判定）
    """
    blk = state_blocks(state)
    sem = "".join(blk.get(h, "") for h in _SEMANTIC_HEADS)
    others = "".join(v for k, v in blk.items() if k not in _SEMANTIC_HEADS)

    own = facts.get(task_id, "")
    own_p = _probe(own)
    own_in_semantic = bool(own_p) and own_p in sem
    own_elsewhere = bool(own_p) and own_p in others

    outside: list[str] = []
    outside_all: list[str] = []
    for k, v in facts.items():
        p = _probe(v)
        if not p:
            continue
        if p in others:
            outside_all.append(k)
            if k != task_id:
                outside.append(k)

    return {
        "task_id": task_id,
        "own_in_semantic": own_in_semantic,
        "own_elsewhere": own_elsewhere,
        "outside": sorted(outside),
        "outside_all": sorted(outside_all),
        "ok": bool(own_in_semantic and not outside_all),
        "blocks": {k: len(v) for k, v in blk.items()},
    }


def format_audit(a: dict) -> str:
    """一行摘要（探针输出用；⛔ 不参与判定）。"""
    flag = "✅" if a["ok"] else "❌"
    extra = ""
    if a["outside_all"]:
        extra = f" 其它块里有 fact：{','.join(a['outside_all'])}"
    return (f"{flag} {a['task_id']:24s} 语义块本条="
            f"{'✅' if a['own_in_semantic'] else '❌'}"
            f"{extra}")


def main(argv: list[str] | None = None) -> int:
    """极小自检：三行 state 各判一次（也是契约 AZ19 的夹具原型）。"""
    import argparse

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--state", help="要审计的 state 文本文件；不给则跑内置自检")
    p.add_argument("--facts", default="{}", help="JSON：{task_id: 惯例原文}")
    p.add_argument("--task", default="t1")
    args = p.parse_args(argv)

    if args.state:
        import json
        from pathlib import Path
        st = Path(args.state).read_text(encoding="utf-8")
        a = audit_state(st, json.loads(args.facts), args.task)
        print(format_audit(a))
        print(f"  块字符数={a['blocks']}")
        return 0 if a["ok"] else 1

    facts = {"t1": "惯例甲：去重时保留首次出现的写法，归一化只用 strip().lower()。",
             "t2": "惯例乙：截断超长串时保留尾部，前置一个省略号。"}
    ok_state = "[目标] 题目\n[语义记忆] " + facts["t1"] + "\n[轨迹] [step 1] recall ⇒ 成功"
    leak_state = ("[目标] 题目\n[工作记忆] recall ⇒ ✔ "
                  + facts["t2"] + "\n[语义记忆] " + facts["t1"] + "\n")
    a1 = audit_state(ok_state, facts, "t1")
    a2 = audit_state(leak_state, facts, "t1")
    print(format_audit(a1), "← 只有语义块，应 ✅")
    print(format_audit(a2), "← 工作记忆里漏了 t2，应 ❌")
    return 0 if (a1["ok"] and not a2["ok"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
