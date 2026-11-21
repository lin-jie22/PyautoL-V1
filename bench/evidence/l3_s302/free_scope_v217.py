"""V217 前置免费量（不跑模型、不计判据）：换 `--critique-scope prev` 之后，
块里还有没有**本题自己**的条目？以及块里到底剩下什么信息。

为什么必须先量（记忆 §二 的先决条件 / 纪律 120）：
    上一轮（V216）的块第一行是**本题自己的历史** ⇒ 效应与「知识转移」不可识别。
    换 scope 的目的是把同题条目挡住；但如果挡完之后块里只剩 `1.00 — 通过 5/5` 这种
    空话，那这一刀就不是「跨题知识」，而是「换了一句废话」。
    ⛔ 先看内容，再决定要不要预注册。
    ⚠️ 这里用的是**生产函数**（`_critique_snapshot` / `admissible` / pick 逻辑），
    不是我自己抄一遍；但它仍然是「算」不是「读」——真跑时必须用探针读入参原文（纪律 124）。

    env -u PYTHONPATH python3 bench/evidence/l3_s302/free_scope_v217.py
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


def build(scope, why_mode):
    """真账本（raw）→ 快照副本（scope + why_mode），与 run.py 同一实现。"""
    real = CritiqueLedger(None)
    for r in rows:
        real.add(Critique(**r))
    return _critique_snapshot(real, scope, None, why_mode)


def block_for(snap, task_id, added=()):
    """复刻 mw/core.py:2305~2320 的取块逻辑（self_c 一条 + others 后两条；无 self 取后三条）。"""
    crit = snap.admissible(scorer_sha=SCORER, suite_sha=SUITE_SHA, exclude_task_id=task_id)
    self_c = [c for c in crit if c.task_id == task_id]
    others = [c for c in crit if c.task_id != task_id]
    pick = ([self_c[-1]] + others[-(3 - 1):]) if self_c else crit[-3:]
    return pick


TASKS = [t["id"] for t in SUITE]
print(f"题目 {TASKS}\n")

for scope in ("prev-self", "prev"):
    for why in ("raw", "shape"):
        snap = build(scope, why)
        st = snap.stats()
        print(f"== scope={scope:9s} why={why:5s}  账本 {st['stored']} 条  allow_self={st['allow_self']}  "
              f"改写 {st['why_rewritten']}  丢弃 {st['why_dropped']}")
        for tid in TASKS:
            pick = block_for(snap, tid)
            own = [c.task_id for c in pick if c.task_id == tid]
            txt = " ｜ ".join(c.as_line()[:140] for c in pick)
            n_info = sum(1 for c in pick if c.score < 1.0)
            print(f"   {tid:22s} 块 {len(pick)} 条（含失败信息 {n_info}）  含本题={bool(own)}  长度 {len(txt)}")
            print(f"       {txt}")
        print()

print("== 结论口径 ==")
print("· 「含本题」必须为 False，否则与 V216 同病（不可识别）。")
print("· 「含失败信息条数」= 块里 score<1.0 的条数；为 0 ⇒ 块只剩「全过」的空话。")
