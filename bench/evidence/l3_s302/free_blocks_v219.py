"""V219 前置免费量（不跑模型、不计判分）：四档臂各自会拿到什么附言。

为什么要先量（纪律 120 / 124）：
    V217 的教训是「块内容与路由不可分」。V218 把路由固定住（calls 恒 2、steps=1），
    代价是题内历史被删。于是本轮能问的问题只剩一个：
    **在固定通道下，附言本身有没有能力动分数？**
    但「附言」有四种档位，差别各是一件事：
        A off              → 无附言（基线）
        B prev + shape     → 跨题、无泄露（本轮主问题）
        C prev-self + shape→ 加上**本题自己的**形状行（复现 V216 的 confound 来源）
        D prev-self + raw  → 加上**测试原文**（正例：证明通道能传；纪律 62）
    ⛔ 先看这四档的**字节内容**，再决定预注册怎么写。

⚠️ 这里仍只是「算」不是「读」：用的是生产函数（`_critique_snapshot` / `admissible`
    / `as_line()` 截 140 / pick 一条 self + 后两条 others），但真跑时判据必须依据
    探针读到的入参原文（纪律 124）。本文件只用来**预注册时写清预期**。

    env -u PYTHONPATH python3 bench/evidence/l3_s302/free_blocks_v219.py
"""
import json
import sys

sys.path.insert(0, ".")
from mw.critique import Critique, CritiqueLedger  # noqa: E402
from run import _critique_snapshot  # noqa: E402

D = "bench/evidence/l3_s302"
rows = [json.loads(l) for l in open(f"{D}/prior_v214.jsonl", encoding="utf-8") if l.strip()]
SUITE = [json.loads(l) for l in open(f"{D}/suite_sub2.jsonl", encoding="utf-8") if l.strip()]
SCORER = rows[0]["scorer_sha"]
SUITE_SHA = rows[0]["suite_sha"]
GOALS = {t["id"]: t.get("goal") or t.get("prompt") or "" for t in SUITE}

#: 泄露标记：只查**测试字面**。⛔ 不用 `'1h30m' in block` 这种恒真判据（纪律 123）。
LEAK = ("5400", "== 45", "merge_ranges([(1,3),(2,6),(8,10)])", "does not support item assignment")


def build(scope, why_mode):
    real = CritiqueLedger(None)
    for r in rows:
        real.add(Critique(**r))
    return _critique_snapshot(real, scope, None, why_mode)


def block_for(snap, task_id):
    """复刻 mw/core.py:2305~2320：有 self ⇒ self 最后一条 + others 后两条；无 ⇒ 后三条。"""
    crit = snap.admissible(scorer_sha=SCORER, suite_sha=SUITE_SHA, exclude_task_id=task_id)
    self_c = [c for c in crit if c.task_id == task_id]
    others = [c for c in crit if c.task_id != task_id]
    pick = ([self_c[-1]] + others[-(3 - 1):]) if self_c else crit[-3:]
    return pick


ARMS = [
    ("A",
     "off", None, None),
    ("B", "blocks", "prev", "shape"),
    ("C", "blocks", "prev-self", "shape"),
    ("D", "blocks", "prev-self", "raw"),
]

TASKS = [t["id"] for t in SUITE]
print("题目", TASKS)
print("题面长度", {t: len(GOALS[t]) for t in TASKS})
print()

#: 给 `read_v219.py` 用的**期望载荷**：真跑时拿探针读到的追加段与它逐字节比。
EXPECT = {"_meta": {"prior": "prior_v214.jsonl", "goals": {t: len(GOALS[t]) for t in TASKS}},
          "arms": {}}

for name, handoff, scope, why in ARMS:
    if handoff == "off":
        print(f"== 臂 {name}  handoff=off  ⇒ 附言 = 空串（四题逐字等于题面）")
        print(f"   题面 {[len(GOALS[t]) for t in TASKS]} 字")
        print()
        EXPECT["arms"][name] = {t: "" for t in TASKS}
        continue
    snap = build(scope, why)
    st = snap.stats()
    print(f"== 臂 {name}  handoff=blocks  scope={scope:9s} why={why:5s}  "
          f"账本 {st['stored']} 条 allow_self={st['allow_self']} "
          f"改写 {st['why_rewritten']} 丢弃 {st['why_dropped']}")
    for tid in TASKS:
        pick = block_for(snap, tid)
        txt = "[外部评审] " + " ｜ ".join(c.as_line()[:140] for c in pick)
        own = any(c.task_id == tid for c in pick)
        n_info = sum(1 for c in pick if c.score < 1.0)
        leak = [m for m in LEAK if m in txt]
        EXPECT["arms"].setdefault(name, {})[tid] = txt
        print(f"   {tid:22s} 附言 {len(txt):3d} 字  条数 {len(pick)}  "
              f"含失败信息 {n_info}  含本题={own}  泄露标记={leak or '无'}")
        for line in txt.split(" ｜ "):
            print(f"       {line}")
        print(f"       ⤷ 拼进外部入参后总长 = {len(GOALS[tid]) + 2 + len(txt)}")
    print()

print("== 结论口径 ==")
print("· A 的附言必须为空 ⇒ 入参逐字 = 题面（V214 已证，本轮应复现）。")
print("· B 是唯一「跨题 + 无泄露」的档；C 只多一条**本题**形状行；D 只多测试字面。")
print("· 若 B 的附言里 n_info=1 而 C/D 的 t01 n_info=1（自评），说明这三档的差别就在「谁的历史」。")

with open(f"{D}/expected_blocks_v219.json", "w", encoding="utf-8") as f:
    json.dump(EXPECT, f, ensure_ascii=False, indent=1)
print(f"\n期望载荷已落盘 {D}/expected_blocks_v219.json（探针读数逐字节比对用）")
