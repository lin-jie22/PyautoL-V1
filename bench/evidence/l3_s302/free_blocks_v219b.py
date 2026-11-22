"""V219b 前置免费量（不跑模型、不计判分）：G 臂到底会拿到什么附言。

V219 的未决问题（§289.5）：B 的块对 `1h30m` 一字未提，却三块都恰好翻第 0 格
⇒「附言给了知识」与「附言把生成推到另一个采样点」分不开。

V219b 只用一条改动去分开它们：把**本题那一行**的失败信息换成零信息
    C: [coding_small/t01_parse_duration] 0.17 — 通过 1/6；异常: ValueError
    G: [coding_small/t01_parse_duration] 1.00 — 通过 6/6
位置、格式、条数、其余两行**逐字节不变**（`admissible()` 按 task_id 排序，
与分数无关 ⇒ 改分数不会挪位置，这一点必须在这里量到，不能假设）。

⚠️ 两个必须自报的口径：
  1. G 臂的块**必然更短**（零失败信息字少）。块长差要写进读数，不许事后当解释。
  2. `t04` 的块在两臂之间**逐字节相同**（本轮只动 t01 那条）⇒ 它不是「对照臂」，
     是**未变块**：同一装置下它自己会不会动，是本轮免费的噪声量。

    env -u PYTHONPATH python3 bench/evidence/l3_s302/free_blocks_v219b.py
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
TASKS = [t["id"] for t in SUITE]

#: 泄露标记：只查测试字面（纪律 123：不用题面自带的值当标记）
LEAK = ("5400", "== 45", "merge_ranges([(1,3),(2,6),(8,10)])", "does not support item assignment")


def ledger_from(rows_):
    led = CritiqueLedger(None)
    for r in rows_:
        led.add(Critique(**r))
    return led


def block_for(snap, task_id):
    """复刻 mw/core.py 的挑选：有 self ⇒ 本题最后一条 + others 后两条；无 ⇒ 后三条。"""
    crit = snap.admissible(scorer_sha=SCORER, suite_sha=SUITE_SHA, exclude_task_id=task_id)
    self_c = [c for c in crit if c.task_id == task_id]
    others = [c for c in crit if c.task_id != task_id]
    pick = ([self_c[-1]] + others[-(3 - 1):]) if self_c else crit[-3:]
    return pick


# ── 构造 G 臂账本：只把 t01 那条换成零失败信息 ──────────────────────────
G_TASK = "t01_parse_duration"
g_rows = []
for r in rows:
    r2 = dict(r)
    if r2["task_id"] == G_TASK:
        r2["score"] = 1.0
        r2["why"] = "通过 6/6"
    g_rows.append(r2)
with open(f"{D}/prior_v219b_g.jsonl", "w", encoding="utf-8") as f:
    for r in g_rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

print("== 账本差异（G 只动一条）==")
for a, b in zip(rows, g_rows):
    tag = "改" if a != b else "同"
    print(f"  {tag} {a['task_id']:22s} {a['score']} -> {b['score']}  "
          f"{a['why'][:34]!r} -> {b['why'][:34]!r}")
print(f"  G 账本已落盘 {D}/prior_v219b_g.jsonl（{len(g_rows)} 条）")
print()

# ── 三臂：A 无附言 / C 现状 / G 零失败信息 ──────────────────────────────
ARMS = [
    ("A", "off", None),
    ("C", "blocks", "shape"),
    ("G", "blocks", "gone"),
]
EXPECT = {"_meta": {"goals": {t: len(GOALS[t]) for t in TASKS},
                    "ledger_C": "prior_v214.jsonl", "ledger_G": "prior_v219b_g.jsonl"},
          "arms": {}}
BLOCKS = {}

for name, handoff, why in ARMS:
    if handoff == "off":
        print(f"== 臂 {name} handoff=off ⇒ 附言 = 空串（入参逐字 = 题面）")
        print(f"   题面 {[len(GOALS[t]) for t in TASKS]} 字")
        print()
        EXPECT["arms"][name] = {t: "" for t in TASKS}
        BLOCKS[name] = {}
        continue
    src = rows if why == "shape" else g_rows
    snap = _critique_snapshot(ledger_from(src), "prev-self", None, "shape")
    st = snap.stats()
    print(f"== 臂 {name} handoff=blocks scope=prev-self why=shape  "
          f"账本 {st['stored']} 条 allow_self={st['allow_self']} "
          f"丢弃 {st['why_dropped']}")
    for tid in TASKS:
        pick = block_for(snap, tid)
        txt = "[外部评审] " + " ｜ ".join(c.as_line()[:140] for c in pick)
        own = any(c.task_id == tid for c in pick)
        n_fail = sum(1 for c in pick if c.score < 1.0)
        leak = [m for m in LEAK if m in txt]
        EXPECT["arms"].setdefault(name, {})[tid] = txt
        BLOCKS.setdefault(name, {})[tid] = txt
        print(f"   {tid:22s} 附言 {len(txt):3d} 字  条数 {len(pick)}  "
              f"含失败信息 {n_fail}  含本题={own}  泄露标记={leak or '无'}")
        for line in txt.split(" ｜ "):
            print(f"       {line}")
    print()

print("== C 与 G 的差异（逐题）==")
for tid in TASKS:
    c, g = BLOCKS["C"][tid], BLOCKS["G"][tid]
    same = "逐字节相同" if c == g else f"不同（C {len(c)} 字 / G {len(g)} 字）"
    print(f"  {tid:22s} {same}")
print("  ⭐ 同一装置下 G 与 C 只差 t01 那一行 ⇒ t04 的块是**未变块**，")
print("     它若也动，说明本轮噪声 ≥ 该块的可动幅度（不许把它算成效应）。")
print()

print("== 结论口径 ==")
print("· G 的 t01 行必须是 `1.00 — 通过 6/6`，且**位置不动**（第 1 条）。")
print("· 三档任一档出现 5400 / merge_ranges 字面 ⇒ 装置有问题，停跑。")
print("· 块长：C 的 t01 块最长，G 更短 ⇒ ⛔ 不许用长度解释 G 的读数（V219 已证长度不解释）。")

with open(f"{D}/expected_blocks_v219b.json", "w", encoding="utf-8") as f:
    json.dump(EXPECT, f, ensure_ascii=False, indent=1)
print(f"\n期望载荷已落盘 {D}/expected_blocks_v219b.json")
