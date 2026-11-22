"""V219d（§292）判据读数：三臂 C(ValueError) / I(IndexError) / K(KeyError)。

    env -u PYTHONPATH python3 bench/evidence/l3_s302/read_v219d.py

⛔ 口径：
   · 三臂的 state 侧配置完全相同（blocks/prev-self/shape），只差账本里 t01 那一个类名词
   · 附言只判**题面之后追加的那一段**；`answer_task()` 的 "\\n\\n" 是连接符，不属于块
   · 判据以 PREREG_v219d.md 为准，本文件不改判据、不改分母
"""
import json
from collections import Counter

D = "bench/evidence/l3_s302"
BASE = int(open(f"{D}/start_runs_j.txt").read().strip().split("=")[-1])
N = 9

rows = [json.loads(l) for l in open("bench_out/runs.jsonl", encoding="utf-8") if l.strip()][BASE:BASE + N]
order = [l.rstrip("\n").split("\t") for l in open(f"{D}/order_j.txt", encoding="utf-8") if l.strip()]
print(f"runs 新行 {len(rows)}  调度 {len(order)}  基线 {BASE}")

suite = [json.loads(l) for l in open(f"{D}/suite_sub2.jsonl", encoding="utf-8") if l.strip()]
PROMPTS = {t["id"]: t["prompt"] for t in suite}
TASKS = list(PROMPTS)
EXP = json.load(open(f"{D}/expected_blocks_v219d.json", encoding="utf-8"))
ARM2CFG = {"val": "C", "idx": "I", "key": "K"}
CFGS = ["C", "I", "K"]
EXC = {"C": "ValueError", "I": "IndexError", "K": "KeyError"}

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
p1a = (not bad) and max(durs) < 300 and len(order) == N
print(f"rc=0 {len(order)-len(bad)}/{len(order)}  单次 {min(durs)}~{max(durs)}s  超 300s {sum(d >= 300 for d in durs)}")
print(f"P1a {'PASS' if p1a else 'FAIL'}")

print("\n== P1b 通道固定（calls==2 / tasks==2 / steps==1）==")
p1b = True
for o, r in zip(order, rows):
    ext = r.get("kernel_external") or {}
    log = open(f"{D}/log_j_{o[0]}_{o[2]}.log", encoding="utf-8", errors="replace").read()
    n1, n0 = log.count("steps=1 执行="), log.count("steps=0 执行=")
    ok = ext.get("calls") == 2 and ext.get("tasks") == 2 and n1 >= 2 and n0 == 0
    p1b &= ok
    print(f"  块{o[0]} {o[2]:5s}({ARM2CFG[o[2]]}) calls={ext.get('calls')} tasks={ext.get('tasks')} "
          f"ok={ext.get('ok')} used={ext.get('used_as_answer')} steps1={n1} steps0={n0} "
          f"{'OK' if ok else '⛔'}")
print(f"P1b {'PASS' if p1b else 'FAIL'}")

print("\n== P1c 探针：追加段逐字节 ==")
p1c = True
recv = {}
for o in order:
    arm, blk = o[2], o[0]
    key = ARM2CFG[arm]
    path = f"{D}/probe_j_{blk}_{arm}.jsonl"
    calls = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    recv[(blk, key)] = {}
    if len(calls) != 2:
        print(f"  ⛔ 块{blk} {arm}({key}) 探针只有 {len(calls)} 次调用")
        p1c = False
        continue
    for c in calls:
        tid, rest = split_prompt(c["task"])
        want = EXP["arms"][key].get(tid, "<缺>")
        exact = (rest == "\n\n" + want)
        recv[(blk, key)][tid] = want
        p1c &= exact
        if not exact:
            print(f"  ⛔ 块{blk} {arm}({key}) {tid} 追加 {len(rest)} 字 vs 期望 {len(want)+2} 字")
    print(f"  块{blk} {arm:5s}({key}) {len(calls)} 次  块长 "
          f"t01={len(EXP['arms'][key][TASKS[0]])} t04={len(EXP['arms'][key][TASKS[1]])}")
print(f"P1c {'PASS' if p1c else 'FAIL'}")

print("\n== P1e 泄露标记（三臂 18 次必须 0 次）==")
LEAK = ("5400", "== 45", "merge_ranges([(1,3),(2,6),(8,10)])", "does not support item assignment")
p_leak = True
for key in CFGS:
    hits = set()
    for blk in ("0", "1", "2"):
        for _, txt in (recv.get((blk, key)) or {}).items():
            hits |= {m for m in LEAK if m in txt}
    p_leak &= not hits
    print(f"  臂 {key}: {'无' if not hits else sorted(hits)}")
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
        line += f"  {key}({EXC[key]}) {v}/6"
    print(line)
print()
for key in CFGS:
    print(f"  n_{key} = {n[key]:2d}  （t01 三块合计，满 18）   逐块 {vecs[key]}")

print("\n== 两条轴 ==")
print(f"  C->I（ValueError -> IndexError）：149 -> 149 字 ⭐同长，只换词   {n['C']} -> {n['I']}")
print(f"  C->K（ValueError -> KeyError）  ：149 -> 147 字（-2，顺带量长度）{n['C']} -> {n['K']}")

print("\n  ── P2 正例闸 / 同轮时间对照（PREREG §5）──")
gate = (n["C"] == 15) and vecs["C"] == [5, 5, 5]
print(f"  C 应复现 5/6 ×3：n_C={n['C']} 逐块 {vecs['C']} ⇒ {'通过' if gate else '⛔ 不通过'}")

print("\n  ── P3 主判据（PREREG §6）──")
print(f"  n_I={n['I']} 逐块 {vecs['I']}   n_K={n['K']} 逐块 {vecs['K']}")
if not gate:
    verdict = "UNKNOWN（C 未复现 ⇒ 装置非平稳，I/K 不可归因）"
elif n["I"] == 12 and n["K"] == 12:
    verdict = "成立：牙在具体的那个词（ValueError）⇒ 换名即回落 ⇒ 泄露残余"
elif n["I"] == 15 or n["K"] == 15:
    verdict = "不成立：任何类名都够 ⇒ 牙 = 元信息「上次是崩不是错」"
else:
    verdict = "UNKNOWN（逐块复现性不足 / 落在中间）"
print(f"  ⇒ **{verdict}**")
print(f"  我押 n_I=12 且 n_K=12（成立）⇒ {'押中' if (n['I'] == 12 and n['K'] == 12) else '押错'}")

print("\n== P4 未变块 t04：块逐字节相同，读数动不动 ==")
for key in CFGS:
    vals = [tot(per[(b, key)][TASKS[1]]["vec"]) for b in BLKS]
    print(f"  {key}: {vals}")
v04 = [tot(per[(b, k)][TASKS[1]]["vec"]) for b in BLKS for k in CFGS]
print(f"  全体取值 {sorted(set(v04))} ⇒ {'零噪声（可作稳定参照）' if len(set(v04)) == 1 else '⚠️ 有块在动'}")

print("\n== 逐格：翻转的是哪一格（t01 六项）==")
for blk in BLKS:
    for key in CFGS:
        t = per[(blk, key)][TASKS[0]]
        fails = [i for i, x in enumerate(t["vec"]) if not x]
        print(f"  块{blk} {key} 过 {tot(t['vec'])}/6  失败格 {fails}")

print("\n== n_eff：每臂的 (题×vec) 状态数 ==")
for key in CFGS:
    c = Counter(tuple(per[(b, key)][t]["vec"]) for b in BLKS for t in TASKS)
    print(f"  {key}: 状态 {len(c)} 种 / 6 格    t01 逐块 {vecs[key]}")
print("\n⛔ 纪律 122：块不是独立样本 ⇒ 本文件不给 p 值。")

print("\n== 前提汇总 ==")
alls = {"P1a": p1a, "P1b": p1b, "P1c": p1c, "P1e": p_leak, "P2": gate}
print("  " + "  ".join(f"{k}={'✅' if v else '❌'}" for k, v in alls.items()))
print(f"  判决：{verdict if all(alls.values()) else 'UNKNOWN（前提缺失：' + '/'.join(k for k, v in alls.items() if not v) + '）'}")
