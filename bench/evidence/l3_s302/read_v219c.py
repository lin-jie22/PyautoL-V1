"""V219c（§291）判据读数：五臂 A / B' / C / G / H。

    env -u PYTHONPATH python3 bench/evidence/l3_s302/read_v219c.py

⛔ 口径：
   · A=off、B'=blocks/prev、C=blocks/prev-self、G=prev-self+\"通过 6/6\"、H=prev-self+\"通过 1/6\"
   · 附言只判**题面之后追加的那一段**；`answer_task()` 的 "\\n\\n" 是连接符，不属于块
   · 判据以 PREREG_v219c.md 为准，本文件不改判据、不改分母
"""
import json
from collections import Counter

D = "bench/evidence/l3_s302"
BASE = int(open(f"{D}/start_runs_i.txt").read().strip().split("=")[-1])
N = 15

rows = [json.loads(l) for l in open("bench_out/runs.jsonl", encoding="utf-8") if l.strip()][BASE:BASE + N]
order = [l.rstrip("\n").split("\t") for l in open(f"{D}/order_i.txt", encoding="utf-8") if l.strip()]
print(f"runs 新行 {len(rows)}  调度 {len(order)}  基线 {BASE}")

suite = [json.loads(l) for l in open(f"{D}/suite_sub2.jsonl", encoding="utf-8") if l.strip()]
PROMPTS = {t["id"]: t["prompt"] for t in suite}
TASKS = list(PROMPTS)
EXP = json.load(open(f"{D}/expected_blocks_v219c.json", encoding="utf-8"))
ARM2CFG = {"off": "A", "prev": "B'", "self": "C", "gone": "G", "count": "H"}
CFGS = ["A", "B'", "C", "G", "H"]

if len(rows) != len(order):
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

print("\n== P1b 通道固定（calls==2 / tasks==2 / steps==1）==")
p1b = True
for o, r in zip(order, rows):
    ext = r.get("kernel_external") or {}
    log = open(f"{D}/log_i_{o[0]}_{o[2]}.log", encoding="utf-8", errors="replace").read()
    n1, n0 = log.count("steps=1 执行="), log.count("steps=0 执行=")
    ok = ext.get("calls") == 2 and ext.get("tasks") == 2 and n1 >= 2 and n0 == 0
    p1b &= ok
    print(f"  块{o[0]} {o[2]:6s}({ARM2CFG[o[2]]:2s}) calls={ext.get('calls')} tasks={ext.get('tasks')} "
          f"ok={ext.get('ok')} used={ext.get('used_as_answer')} steps1={n1} steps0={n0} "
          f"{'OK' if ok else '⛔'}")
print(f"P1b {'PASS' if p1b else 'FAIL'}")

print("\n== P1c 探针：追加段逐字节 ==")
p1c = True
recv = {}
for o in order:
    arm, blk = o[2], o[0]
    key = ARM2CFG[arm]
    calls = [json.loads(l) for l in open(f"{D}/probe_i_{blk}_{arm}.jsonl", encoding="utf-8") if l.strip()]
    recv[(blk, key)] = {}
    if len(calls) != 2:
        print(f"  ⛔ 块{blk} {arm}({key}) 探针只有 {len(calls)} 次调用")
        p1c = False
        continue
    for c in calls:
        tid, rest = split_prompt(c["task"])
        want = EXP["arms"][key].get(tid, "<缺>")
        exact = (rest == "" if want == "" else rest == "\n\n" + want)
        recv[(blk, key)][tid] = want if want else ""
        p1c &= exact
        if not exact:
            print(f"  ⛔ 块{blk} {arm}({key}) {tid} 追加 {len(rest)} 字 vs 期望 {len(want)} 字")
    print(f"  块{blk} {arm:6s}({key:2s}) {len(calls)} 次  "
          f"{'块长 ' + str(len(EXP['arms'][key][TASKS[0]])) + '/' + str(len(EXP['arms'][key][TASKS[1]])) if arm != 'off' else '追加段 0 字'}")
print(f"P1c {'PASS' if p1c else 'FAIL'}")

print("\n== P1d 泄露标记（五臂 30 次必须 0 次）==")
LEAK = ("5400", "== 45", "merge_ranges([(1,3),(2,6),(8,10)])", "does not support item assignment")
p_leak = True
for key in CFGS:
    hits = set()
    for blk in ("0", "1", "2"):
        for _, txt in (recv.get((blk, key)) or {}).items():
            hits |= {m for m in LEAK if m in txt}
    p_leak &= not hits
    print(f"  臂 {key:2s}: {'无' if not hits else sorted(hits)}")
print(f"泄露闸 {'PASS' if p_leak else 'FAIL'}")

print("\n== 主读数：t01 逐块（六项里唯一有变化空间的格）==")


def tot(v):
    return sum(1 for x in v if x)


per = {(o[0], ARM2CFG[o[2]]): {t["id"]: t for t in r["report"]["per_task"]}
       for o, r in zip(order, rows)}
BLKS = sorted({b for b, _ in per})
n = {k: 0 for k in CFGS}
vecs = {k: [] for k in CFGS}
for blk in BLKS:
    line = f"  块 {blk}:"
    for key in CFGS:
        t = per[(blk, key)][TASKS[0]]
        v = tot(t["vec"])
        vecs[key].append(v)
        n[key] += v
        line += f"  {key} {v}/6"
    print(line)
print()
for key in CFGS:
    print(f"  n_{key:2s} = {n[key]:2d}  （t01 三块合计，满 18）   逐块 {vecs[key]}")

print("\n== 三条轴 ==")
bq = n["B'"]
print(f"  B'->G（别题失败 -> 本题满分）：{bq} -> {n['G']}  块长 162->134")
print(f"  G->H（满分 -> 失败无类名）  ：{n['G']} -> {n['H']}  ⭐块长 134=134（只改内容）")
print(f"  H->C（加回 `；异常: ValueError`）：{n['H']} -> {n['C']}  块长 134->149")

print("\n  ── 判据（PREREG_v219c §4）──")
gate = (n["C"] >= 14) and (11 <= n["G"] <= 13)
print(f"  正例闸 n_C≥14 且 n_G∈[11,13] : n_C={n['C']} n_G={n['G']} ⇒ {'通过' if gate else '⛔ 不通过'}")
if not gate:
    verdict = "UNKNOWN（正例闸不通过 ⇒ 本轮与上轮不同向，装置不可比）"
elif n["H"] >= 14:
    verdict = "成立 A：失败**计数**足够，异常类名零贡献"
elif n["H"] >= 11:
    verdict = "成立 B：计数零贡献，牙在**异常类名**"
else:
    verdict = "不成立：「计数 + 类名」须同时在场（非加性）"
print(f"  n_H={n['H']} ⇒ **{verdict}**")
print(f"  我押 n_H=12（成立 B）⇒ {'押中' if n['H'] == 12 else '押错'}")

print("\n  ── P3 次判据：条数/位置轴 ──")
if bq == n["G"]:
    p3 = "相等 ⇒ 「别题失败行」≡「本题满分行」（失败**位置**决定一切）"
elif bq <= n["G"] - 2:
    p3 = f"低 {n['G'] - bq} ⇒ 别题失败行也有正贡献"
else:
    p3 = f"介于其间（{bq} vs {n['G']}）"
print(f"  n_B'={bq} n_G={n['G']} ⇒ {p3}")
print(f"  我押 n_B'=12 ⇒ {'押中' if bq == 12 else '押错'}")

print("\n== P3b 未变块 t04：块逐字节相同，读数动不动 ==")
for key in CFGS:
    vals = [tot(per[(b, key)][TASKS[1]]["vec"]) for b in BLKS]
    print(f"  {key:2s}: {vals}")
v04 = [tot(per[(b, k)][TASKS[1]]["vec"]) for b in BLKS for k in CFGS]
print(f"  全体取值 {sorted(set(v04))} ⇒ {'零噪声（可作稳定参照）' if len(set(v04)) == 1 else '⚠️ 有块在动'}")

print("\n== 逐格：翻转的是哪一格（t01 六项）==")
for blk in BLKS:
    for key in CFGS:
        t = per[(blk, key)][TASKS[0]]
        fails = [i for i, x in enumerate(t["vec"]) if not x]
        print(f"  块{blk} {key:2s} 过 {tot(t['vec'])}/6  失败格 {fails}")

print("\n== 块长 vs 效应（非判据，只用来否掉「纯长度」解释）==")
for key in CFGS:
    ln = {tid: len(EXP["arms"][key][tid]) for tid in TASKS}
    print(f"  臂 {key:2s}: 块长 t01={ln[TASKS[0]]:3d} t04={ln[TASKS[1]]:3d}")

print("\n== n_eff：每臂的 (题×vec) 状态数 ==")
for key in CFGS:
    c = Counter(tuple(per[(b, key)][t]["vec"]) for b in BLKS for t in TASKS)
    print(f"  {key:2s}: 状态 {len(c)} 种 / 10 格   t01 逐块 {vecs[key]}")
print("\n⛔ 纪律 122：块不是独立样本 ⇒ 本文件不给 p 值。")

print("\n== 前提汇总 ==")
alls = {"P1a": p1a, "P1b": p1b, "P1c": p1c, "泄露": p_leak, "正例闸": gate}
print("  " + "  ".join(f"{k}={'✅' if v else '❌'}" for k, v in alls.items()))
print(f"  判决：{verdict if all(alls.values()) else 'UNKNOWN（前提缺失）'}")
