"""V219d 前置免费量（不跑模型、不计判分）：三臂各自会拿到什么附言。

V219c 把「累积」削到一个词：只有加回 `；异常: ValueError` 才 +1。
但那个词是**测试输出的类名**，所以有两种解释分不开：
    ① 牙在「这个词」本身（= 测试泄露残余）
    ② 牙在「有一个异常类名」这件事（= 「上次崩了」这种元信息）

本轮只换那个词，且**先解决长度**：
    C: ；异常: ValueError    （10 字符，149 字块）
    I: ；异常: IndexError    （10 字符 ⇒ 块长应也是 149，⭐与 C 逐字同长）
    K: ；异常: KeyError      （8 字符 ⇒ 147 字块，用来单独量「短 2 字」有没有牙）

判读设计：
    I = 5/6  ⇒ 类名不必对 ⇒ 牙是「有类名」这件事（元信息）
    I = 4/6  ⇒ 词特异 ⇒ 坐实「测试泄露残余」
    K 与 I 不等 ⇒ 2 个字符的长度差自己有效应（须申报，不能当噪声）

    env -u PYTHONPATH python3 bench/evidence/l3_s302/free_blocks_v219d.py
"""
import json
import sys

sys.path.insert(0, ".")
from mw.critique import Critique, CritiqueLedger  # noqa: E402
from run import _critique_snapshot  # noqa: E402

D = "bench/evidence/l3_s302"
rows = [json.loads(l) for l in open(f"{D}/prior_v214.jsonl", encoding="utf-8") if l.strip()]
SUITE = [json.loads(l) for l in open(f"{D}/suite_sub2.jsonl", encoding="utf-8") if l.strip()]
SCORER, SUITE_SHA = rows[0]["scorer_sha"], rows[0]["suite_sha"]
GOALS = {t["id"]: t.get("goal") or t.get("prompt") or "" for t in SUITE}
TASKS = [t["id"] for t in SUITE]
LEAK = ("5400", "== 45", "merge_ranges([(1,3),(2,6),(8,10)])", "does not support item assignment")
G_TASK = "t01_parse_duration"


def ledger_from(rs):
    led = CritiqueLedger(None)
    for r in rs:
        led.add(Critique(**r))
    return led


def block_for(snap, task_id):
    crit = snap.admissible(scorer_sha=SCORER, suite_sha=SUITE_SHA, exclude_task_id=task_id)
    self_c = [c for c in crit if c.task_id == task_id]
    others = [c for c in crit if c.task_id != task_id]
    pick = ([self_c[-1]] + others[-(3 - 1):]) if self_c else crit[-3:]
    return pick


def rewrite(exc):
    """只改那一行的异常类名，score/通过计数/位置全部不动。"""
    out = []
    for r in rows:
        r2 = dict(r)
        if r2["task_id"] == G_TASK:
            r2["score"] = 0.1667
            r2["why"] = f"通过 1/6；异常: {exc}"
        out.append(r2)
    return out


LEDGERS = {"C": rewrite("ValueError"), "I": rewrite("IndexError"), "K": rewrite("KeyError")}
for name, rs in LEDGERS.items():
    with open(f"{D}/prior_v219d_{name.lower()}.jsonl", "w", encoding="utf-8") as f:
        for r in rs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

print("== 三个账本的 t01 那条（只差类名一个词）==")
for name in ("C", "I", "K"):
    r = [x for x in LEDGERS[name] if x["task_id"] == G_TASK][0]
    exc = r["why"].split("异常: ")[1]
    print(f"  {name}  score={r['score']!r:8s} why={r['why']!r}  类名 {len(exc)} 字符")
print()

ARMS = [("C", "blocks", "prev-self", LEDGERS["C"]),
        ("I", "blocks", "prev-self", LEDGERS["I"]),
        ("K", "blocks", "prev-self", LEDGERS["K"])]
EXPECT = {"_meta": {"goals": {t: len(GOALS[t]) for t in TASKS}}, "arms": {}}
BLK = {}

for name, handoff, scope, src in ARMS:
    snap = _critique_snapshot(ledger_from(src), scope, None, "shape")
    print(f"== 臂 {name} scope={scope} 账本 t01 = "
          f"{[c.why for c in ledger_from(src).by_key.values() if c.task_id == G_TASK][0]!r}")
    for tid in TASKS:
        pick = block_for(snap, tid)
        txt = "[外部评审] " + " ｜ ".join(c.as_line()[:140] for c in pick)
        EXPECT["arms"].setdefault(name, {})[tid] = txt
        BLK.setdefault(name, {})[tid] = txt
        leaks = [m for m in LEAK if m in txt]
        print(f"   {tid:22s} 附言 {len(txt):3d} 字  条数 {len(pick)}  "
              f"含本题={any(c.task_id == tid for c in pick)}  "
              f"含失败={sum(1 for c in pick if c.score < 1.0)}  泄露={leaks or '无'}")
        for line in txt.split(" ｜ "):
            print(f"       {line}")
    print()

print("== 本轮最关键的两条对照 ==")
c1, i1, k1 = BLK["C"][G_TASK], BLK["I"][G_TASK], BLK["K"][G_TASK]
print(f"  C vs I（词变、长度不变）：{len(c1)} 字 vs {len(i1)} 字 ⇒ "
      f"{'**块长相同**（只差一个词，同 10 字符）' if len(c1) == len(i1) else '⛔ 长度也变了'}")
print(f"  C vs K（词变、短 2 字）  ：{len(c1)} → {len(k1)} 字（{len(k1)-len(c1):+d}）")
print()
print("== 结论口径 ==")
print("· 三臂的 t04 块应全部逐字节相同（本轮只改 t01 那条）⇒ t04 作未变块/零噪声旁证。")
print("· 任一臂出现 5400 / merge_ranges 字面 ⇒ 装置有问题，停跑。")
print("· ⛔ C→I 必须块长相等，否则这轮就退化成 V219b 的老情况（长度与内容同时变）。")

with open(f"{D}/expected_blocks_v219d.json", "w", encoding="utf-8") as f:
    json.dump(EXPECT, f, ensure_ascii=False, indent=1)
print(f"\n期望载荷已落盘 {D}/expected_blocks_v219d.json")
