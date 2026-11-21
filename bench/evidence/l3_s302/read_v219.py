"""V219（§289）判据读数：固定通道（门 B）下四臂阶梯 A/B/C/D。

    env -u PYTHONPATH python3 bench/evidence/l3_s302/read_v219.py

⛔ 口径（写进代码，防手滑）：
   · A=off/prev/shape、B=blocks/prev/shape、C=blocks/prev-self/shape、D=blocks/prev-self/raw
   · 附言只判**题面之后追加的那一段**（纪律 123）；读的是探针存下的原文（纪律 124）
   · 判据以 PREREG_v219.md 为准，本文件不改判据
"""
import json
from collections import Counter

D = "bench/evidence/l3_s302"
BASE = int(open(f"{D}/start_runs_g.txt").read().strip().split("=")[-1])
N = 12

rows = [json.loads(l) for l in open("bench_out/runs.jsonl", encoding="utf-8") if l.strip()][BASE:BASE + N]
order = [l.rstrip("\n").split("\t") for l in open(f"{D}/order_g.txt", encoding="utf-8") if l.strip()]
print(f"runs 新行 {len(rows)}  调度 {len(order)}  基线 {BASE}")

suite = [json.loads(l) for l in open(f"{D}/suite_sub2.jsonl", encoding="utf-8") if l.strip()]
PROMPTS = {t["id"]: t["prompt"] for t in suite}
TASKS = list(PROMPTS)
EXP = json.load(open(f"{D}/expected_blocks_v219.json", encoding="utf-8"))
ARM2CFG = {"off": "A", "prev": "B", "self": "C", "raw": "D"}

if len(rows) != len(order):
    # 账本行数对不上时不硬算：只报现状，避免「少一行还照旧聚合」
    print(f"⛔ 行数不符（runs {len(rows)} / order {len(order)}）⇒ 只打印已有部分")
    order = order[:len(rows)]


def split_prompt(raw):
    for tid, p in PROMPTS.items():
        if raw.startswith(p):
            return tid, raw[len(p):]
    return None, raw


print("\n== P1a 装置 ==")
bad = [o for o in order if o[3] != "rc=0"]
durs = [int(o[4].rstrip("s")) for o in order]
p1a = (not bad) and max(durs) <= 420 and len(order) == N
print(f"rc=0 {len(order)-len(bad)}/{len(order)}  单次 {min(durs)}~{max(durs)}s  超 420s {sum(d > 420 for d in durs)}")
print(f"P1a {'PASS' if p1a else 'FAIL'}")

print("\n== P1b 通道固定（calls==2 / tasks==2，逐次运行）==")
p1b = True
for o, r in zip(order, rows):
    ext = r.get("kernel_external") or {}
    ok = ext.get("calls") == 2 and ext.get("tasks") == 2
    p1b &= ok
    print(f"  块{o[0]} {o[2]:5s}({ARM2CFG[o[2]]}) calls={ext.get('calls')} tasks={ext.get('tasks')} "
          f"na={ext.get('skipped_not_applicable')} nob={ext.get('skipped_no_budget')} "
          f"ok={ext.get('ok')} used={ext.get('used_as_answer')} {'OK' if ok else '⛔'}")
print(f"P1b {'PASS' if p1b else 'FAIL'}")

print("\n== P1d steps==1 且 state 含 [外部评审] ==")
p1d = True
for o in order:
    log = open(f"{D}/log_g_{o[0]}_{o[2]}.log", encoding="utf-8", errors="replace").read()
    n1 = log.count("steps=1 执行=")
    n0 = log.count("steps=0 执行=")
    st = [json.loads(l) for l in open(f"{D}/state_g_{o[0]}_{o[2]}.jsonl", encoding="utf-8") if l.strip()]
    has_crit = sum(1 for d in st if "[外部评审]" in d["state"])
    ok = (n1 >= 2 and n0 == 0 and has_crit >= 2)
    p1d &= ok
    print(f"  块{o[0]} {o[2]:5s}({ARM2CFG[o[2]]}) done 行 steps=1 计 {n1}  steps=0 计 {n0}  "
          f"state 行 {len(st)} 带评审块 {has_crit}  {'OK' if ok else '⛔'}")
print(f"P1d {'PASS' if p1d else 'FAIL'}")

print("\n== P1c 探针：追加段是否逐字节等于冻结账本的块 ==")
# ⚠️ 自踩登记（本轮第 1 条，口径不是判据）：第一版把 `raw[len(prompt):]` 整段当「追加段」，
#    于是每次都比期望长 **恰好 2 个字符**。查实：`answer_task()` 用 `"\n\n"` 连接题面与附言，
#    而那 2 个字符是**连接符**，不属于块本身（`handoff_text()` 的产物）。
#    ⇒ 修的是「追加段」的口径（`rest == ("\n\n"+块) 当块非空`），预注册的「逐字节相同」一字未动。
#    ⛔ 这是口径错误的标准形态（纪律 89）：比两个字符串前没写清边界算到哪。
p1c = True
recv = {}
for o in order:
    arm, blk = o[2], o[0]
    key = ARM2CFG[arm]
    path = f"{D}/probe_g_{blk}_{arm}.jsonl"
    calls = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    recv[(blk, key)] = {}
    if len(calls) != 2:
        print(f"  ⛔ 块{blk} {arm}({key}) 探针只有 {len(calls)} 次调用")
        p1c = False
        continue
    for c in calls:
        tid, rest = split_prompt(c["task"])
        want = EXP["arms"][key].get(tid, "<缺>")
        sep = "" if want == "" else "\n\n"
        exact = (rest == sep + want)
        recv[(blk, key)][tid] = want if want else rest
        p1c &= exact
        print(f"  块{blk} {arm}({key}) {tid:22s} 入参 {c['task_chars']:3d}  "
              f"块 {len(want):3d} 字（连接符 {'2' if sep else '0'}）  "
              f"逐字节={'相同' if exact else '⛔不同'}")
print(f"P1c {'PASS' if p1c else 'FAIL'}")

print("\n== 泄露标记：只许 D 出现 ==")
LEAK = ("5400", "== 45", "merge_ranges([(1,3),(2,6),(8,10)])", "does not support item assignment")
for key in ("A", "B", "C", "D"):
    hits = set()
    for blk in ("0", "1", "2"):
        for tid, txt in (recv.get((blk, key)) or {}).items():
            hits |= {m for m in LEAK if m in txt}
    print(f"  臂 {key}: {'无' if not hits else sorted(hits)}")

print("\n== P2/P3/P4 主读数：t01（唯一有变化空间的格）==")


def tot(v):
    return sum(1 for x in v if x)


per = {(o[0], ARM2CFG[o[2]]): {t["id"]: t for t in r["report"]["per_task"]}
       for o, r in zip(order, rows)}
BLKS = sorted({b for b, _ in per})
dd = {"B": [], "C": [], "D": []}
for blk in BLKS:
    a = per[(blk, "A")][TASKS[0]]
    print(f"  块 {blk}  A {tot(a['vec'])}/{len(a['vec'])} {a['vec']}")
    for key in ("B", "C", "D"):
        x = per[(blk, key)][TASKS[0]]
        d = tot(x["vec"]) - tot(a["vec"])
        dd[key].append(d)
        print(f"        {key} {tot(x['vec'])}/{len(x['vec'])} {x['vec']}   Δ={d:+d}")
print()
for key, name in (("B", "跨题（B−A）"), ("C", "同题形状（C−A）"), ("D", "泄露（D−A）")):
    d = dd[key]
    up2 = sum(1 for x in d if x >= 2)
    print(f"  {name}: Δ={d}  ≥+2 的块 {up2}/3  ⇒ 门槛（≥+2 且 ≥2/3 块）"
          f"{'达标' if up2 >= 2 else '不达标'}")

print("\n== P5 天花板对照 t04 ==")
for key in ("A", "B", "C", "D"):
    vals = [tot(per[(b, key)][TASKS[1]]["vec"]) for b in BLKS]
    print(f"  {key}: {vals}  {'全 6/6' if all(v == 6 for v in vals) else '⛔ 有掉'}")
print("  ⛔ 6/6 是天花板 ⇒ 只查「有没有掉」，不作「有效」的判据（纪律 119）")

print("\n== 逐检查：翻转的是哪一格（t01 六项）==")
for blk in BLKS:
    for key in ("A", "B", "C", "D"):
        t = per[(blk, key)][TASKS[0]]
        fails = [i for i, x in enumerate(t["vec"]) if not x]
        print(f"  块{blk} {key} 过 {tot(t['vec'])}/6  失败格 {fails}  why0={t['why'][:60]}")

print("\n== 长度 vs 效应（跨臂，非判据，只用来否掉「纯长度」解释）==")
for key in ("B", "C", "D"):
    ln = {tid: len(EXP["arms"][key][tid]) for tid in TASKS}
    print(f"  臂 {key}: 块长 t01={ln[TASKS[0]]} t04={ln[TASKS[1]]}  "
          f"t01 Δ={dd[key]}  平均 Δ={sum(dd[key])/len(dd[key]):+.2f}")

print("\n== n_eff：每臂的 (题×vec) 状态数 ==")
for key in ("A", "B", "C", "D"):
    c = Counter(tuple(per[(b, key)][t]["vec"]) for b in BLKS for t in TASKS)
    t01 = [tot(per[(b, key)][TASKS[0]]["vec"]) for b in BLKS]
    print(f"  {key}: 状态 {len(c)} 种 / 6 格   t01 逐块 {t01}")
print("\n⛔ 纪律 122：块不是独立样本 ⇒ 本文件不给 p 值。")
