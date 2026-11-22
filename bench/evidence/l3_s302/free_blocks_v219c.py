"""V219c 前置免费量（不跑模型、不计判分）：五臂各自会拿到什么附言。

V219b 证了「把本题那行从『全过』换成『失败 + ValueError』」= +1。
但那一行同时改了两件事：
    ① 通过计数（6/6 → 1/6）
    ② 异常类名（无 → ValueError）
本轮用 `H` 臂把 ① 留在、② 拿掉：
    G: [coding_small/t01_parse_duration] 1.00 — 通过 6/6      （134 字块）
    H: [coding_small/t01_parse_duration] 0.17 — 通过 1/6      （应也是 134 字块）
    C: [coding_small/t01_parse_duration] 0.17 — 通过 1/6；异常: ValueError（149 字块）
⭐ 若 H 与 G **块长逐字相同、只差那一行的数字**，则 G→H 是「只改内容」的干净对照（本轮最重要的一条）。

另外补一个 V219b 漏掉的同轮臂：
    B': scope=prev（排除本题）⇒ 只有 t05/t06 两条满分    （本轮唯一不带本题行的臂）
用来同轮确认「第三条满分记录是否零效应」（V219b 只有跨轮旁证 G=B=4/6）。

    env -u PYTHONPATH python3 bench/evidence/l3_s302/free_blocks_v219c.py
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


# ── 两个改造账本：G（全过）/ H（1/6，无异常名）────────────────────────
def rewrite(why, score):
    out = []
    for r in rows:
        r2 = dict(r)
        if r2["task_id"] == G_TASK:
            r2["score"] = score
            r2["why"] = why
        out.append(r2)
    return out


g_rows = rewrite("通过 6/6", 1.0)
h_rows = rewrite("通过 1/6", 0.1667)
for name, rs in (("g", g_rows), ("h", h_rows)):
    with open(f"{D}/prior_v219c_{name}.jsonl", "w", encoding="utf-8") as f:
        for r in rs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

print("== 三个账本的 t01 那条 ==")
for name, rs in (("base", rows), ("G", g_rows), ("H", h_rows)):
    r = [x for x in rs if x["task_id"] == G_TASK][0]
    print(f"  {name:4s} score={r['score']!r:10s} why={r['why']!r}")
print()

ARMS = [
    ("A", "off", "prev", rows),
    ("B'", "blocks", "prev", rows),
    ("C", "blocks", "prev-self", rows),
    ("G", "blocks", "prev-self", g_rows),
    ("H", "blocks", "prev-self", h_rows),
]
EXPECT = {"_meta": {"goals": {t: len(GOALS[t]) for t in TASKS}}, "arms": {}}
BLK = {}

for name, handoff, scope, src in ARMS:
    if handoff == "off":
        print(f"== 臂 {name} handoff=off ⇒ 外发附言 = 空串（入参逐字 = 题面）")
        print(f"   题面 {[len(GOALS[t]) for t in TASKS]} 字")
        print()
        EXPECT["arms"][name] = {t: "" for t in TASKS}
        BLK[name] = {}
        continue
    snap = _critique_snapshot(ledger_from(src), scope, None, "shape")
    print(f"== 臂 {name:2s} scope={scope:9s} 账本 t01 那条 = "
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
g1, h1, c1 = BLK["G"][G_TASK], BLK["H"][G_TASK], BLK["C"][G_TASK]
print(f"  G vs H（拆『异常类名』）：{len(g1)} 字 vs {len(h1)} 字 ⇒ "
      f"{'**块长相同**（只差那一行的数字）' if len(g1) == len(h1) else '⛔ 长度也变了'}")
print(f"  H vs C（同一个 H 加回 `；异常: ValueError`）：{len(h1)} → {len(c1)} 字（+{len(c1)-len(h1)}）")
print(f"  B' vs G（拆『第三条满分记录』）：{len(BLK[chr(66)+chr(39)][G_TASK])} 字 vs {len(g1)} 字")
print()
print("== 结论口径 ==")
print("· 五臂的 t04 块应全部逐字节相同（本轮只改 t01 那条）⇒ t04 作未变块。")
print("· 任一臂出现 5400 / merge_ranges 字面 ⇒ 装置有问题，停跑。")
print("· ⛔ G→H 若块长不等，就退化成 V219b 的老情况（长度与内容同时变），必须明说。")

with open(f"{D}/expected_blocks_v219c.json", "w", encoding="utf-8") as f:
    json.dump(EXPECT, f, ensure_ascii=False, indent=1)
print(f"\n期望载荷已落盘 {D}/expected_blocks_v219c.json")
