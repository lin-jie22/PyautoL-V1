"""V219b（§290）判据读数：A(off) / C(现行) / G(本题行改成零失败信息)。

    env -u PYTHONPATH python3 bench/evidence/l3_s302/read_v219b.py

⛔ 口径（写进代码，防手滑）：
   · A=off/prev/shape、C=blocks/prev-self/shape、G=blocks/prev-self/shape + prior_v219b_g.jsonl
   · 附言只判**题面之后追加的那一段**；`answer_task()` 的 "\\n\\n" 是连接符，不属于块
   · 判据以 PREREG_v219b.md 为准，本文件不改判据、不改分母
"""
import json
from collections import Counter

D = "bench/evidence/l3_s302"
BASE = int(open(f"{D}/start_runs_h.txt").read().strip().split("=")[-1])
N = 9

rows = [json.loads(l) for l in open("bench_out/runs.jsonl", encoding="utf-8") if l.strip()][BASE:BASE + N]
order = [l.rstrip("\n").split("\t") for l in open(f"{D}/order_h.txt", encoding="utf-8") if l.strip()]
print(f"runs 新行 {len(rows)}  调度 {len(order)}  基线 {BASE}")

suite = [json.loads(l) for l in open(f"{D}/suite_sub2.jsonl", encoding="utf-8") if l.strip()]
PROMPTS = {t["id"]: t["prompt"] for t in suite}
TASKS = list(PROMPTS)
EXP = json.load(open(f"{D}/expected_blocks_v219b.json", encoding="utf-8"))
ARM2CFG = {"off": "A", "self": "C", "gone": "G"}

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

print("\n== P1b 通道固定（calls==2 / tasks==2）==")
p1b = True
for o, r in zip(order, rows):
    ext = r.get("kernel_external") or {}
    ok = ext.get("calls") == 2 and ext.get("tasks") == 2
    p1b &= ok
    print(f"  块{o[0]} {o[2]:5s}({ARM2CFG[o[2]]}) calls={ext.get('calls')} tasks={ext.get('tasks')} "
          f"na={ext.get('skipped_not_applicable')} nob={ext.get('skipped_no_budget')} "
          f"ok={ext.get('ok')} used={ext.get('used_as_answer')} {'OK' if ok else '⛔'}")
print(f"P1b {'PASS' if p1b else 'FAIL'}")

print("\n== P1b2 steps==1 且 state 含 [外部评审] ==")
# ⚠️ 自踩登记（本轮第 2 条）：首版这里写 `want_crit = 0 if off`，于是三条 `off` 全部报 ⛔。
#    查实是**我的期望值错**，不是装置坏：`-external-handoff off` 只关**外发**，
#    `--critique-scope` 三臂都是 `prev`/`prev-self`（照抄 V219）⇒ state **本来就该**有评审块。
#    V219 的同一检查写的是 `has_crit >= 2`（对 A 臂也要求 ≥2），与此一致。
#    ⛔ 而且这个 `==want_crit` 条件是**预注册里没有的**，是我跑后自加的更严检查 ——
#    它的期望值错了，退回预注册口径（+ 装置事实）不是放宽判据：
#    预注册的四个前提是 P1a(rc) / P1b(calls,steps) / P1c(探针逐字节) / P1d(泄露 0 次)，
#    这四项都不依赖本条。本条只报「state 注入与 scope 是否一致」。
p1d = True
for o in order:
    log = open(f"{D}/log_h_{o[0]}_{o[2]}.log", encoding="utf-8", errors="replace").read()
    n1 = log.count("steps=1 执行=")
    n0 = log.count("steps=0 执行=")
    st = [json.loads(l) for l in open(f"{D}/state_h_{o[0]}_{o[2]}.jsonl", encoding="utf-8") if l.strip()]
    has_crit = sum(1 for d in st if "[外部评审]" in d["state"])
    ok = (n1 >= 2 and n0 == 0 and has_crit >= 2)
    p1d &= ok
    print(f"  块{o[0]} {o[2]:5s}({ARM2CFG[o[2]]}) steps=1 计 {n1}  steps=0 计 {n0}  "
          f"state 行 {len(st)} 带评审块 {has_crit}（scope 非 off ⇒ 应 ≥2）  {'OK' if ok else '⛔'}")
print(f"state 注入闸 {'PASS' if p1d else 'FAIL'}")
print("  ⭐ A 臂的 off 只关**外发**：state 有评审块、但外发追加段 0 字（P1c 已证）")
print("     ⇒ 对 `code_agent` 而言 A 仍是「只看题面」（它的入参 = prompt + 附言）")

print("\n== P1c 探针：追加段是否逐字节等于冻结账本的块 ==")
p1c = True
recv = {}
for o in order:
    arm, blk = o[2], o[0]
    key = ARM2CFG[arm]
    path = f"{D}/probe_h_{blk}_{arm}.jsonl"
    calls = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    recv[(blk, key)] = {}
    if o[2] == "off":
        # off 档：calls 也是 2，但附言必须是空串（入参逐字 = 题面）
        for c in calls:
            tid, rest = split_prompt(c["task"])
            recv[(blk, key)][tid] = rest
            if rest != "":
                print(f"  ⛔ 块{blk} {arm} {tid} 追加段非空 {len(rest)} 字")
                p1c = False
        print(f"  块{blk} {arm}(A) {len(calls)} 次调用，追加段全为 0 字 ⇒ 入参逐字 = 题面")
        continue
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
        print(f"  块{blk} {arm}({key}) {tid:22s} 入参 {c['task_chars']:3d}  "
              f"块 {len(want):3d} 字  逐字节={'相同' if exact else '⛔不同'}")
print(f"P1c {'PASS' if p1c else 'FAIL'}")

print("\n== P1d2 泄露标记：三臂 18 次必须 0 次 ==")
LEAK = ("5400", "== 45", "merge_ranges([(1,3),(2,6),(8,10)])", "does not support item assignment")
p_leak = True
for key in ("A", "C", "G"):
    hits = set()
    for blk in ("0", "1", "2"):
        for _, txt in (recv.get((blk, key)) or {}).items():
            hits |= {m for m in LEAK if m in txt}
    p_leak &= not hits
    print(f"  臂 {key}: {'无' if not hits else sorted(hits)}")
print(f"泄露闸 {'PASS' if p_leak else 'FAIL'}")

print("\n== P2 主读数：t01 逐块（唯一有变化空间的格）==")


def tot(v):
    return sum(1 for x in v if x)


per = {(o[0], ARM2CFG[o[2]]): {t["id"]: t for t in r["report"]["per_task"]}
       for o, r in zip(order, rows)}
BLKS = sorted({b for b, _ in per})
t01 = {k: [] for k in ("A", "C", "G")}
for blk in BLKS:
    a = per[(blk, "A")][TASKS[0]]
    print(f"  块 {blk}  A {tot(a['vec'])}/{len(a['vec'])} {a['vec']}")
    for key in ("C", "G"):
        x = per[(blk, key)][TASKS[0]]
        t01[key].append(tot(x["vec"]))
        print(f"        {key} {tot(x['vec'])}/{len(x['vec'])} {x['vec']}   "
              f"Δ(vs A)={tot(x['vec'])-tot(a['vec']):+d}")
    t01["A"].append(tot(a["vec"]))

nA, nC, nG = sum(t01["A"]), sum(t01["C"]), sum(t01["G"])
print(f"\n  n_A={nA}  n_C={nC}  n_G={nG}   （满 18；每块 0~6）")
print(f"  C−A = {(nC-nA)/3:+.2f}/块 等效  |  G−A = {(nG-nA)/3:+.2f}/块 等效  "
      f"|  C−G = {(nC-nG)/3:+.2f}/块 等效")

print("\n  ── 判据（PREREG_v219b §4）──")
gate = nC >= 14
print(f"  正例闸 n_C≥14 : {nC} ⇒ {'通过' if gate else '⛔ 不通过（本轮装置不可比）'}")
if not gate:
    verdict = "UNKNOWN（正例闸不通过）"
elif nG <= 11:
    verdict = "成立（强）：失败信息净贡献 ≥ +2/块"
elif nG <= 13:
    verdict = "成立（弱）：失败信息净贡献 +1 ~ +1.33/块"
else:
    verdict = "不成立：效应由条数/文本量解释，失败信息无独立贡献"
print(f"  n_G={nG} ⇒ **{verdict}**")
print(f"  我押的是 n_G=12 ⇒ {'押中' if nG == 12 else '押错'}")

print("\n== P3 未变块 t04：块逐字节相同，读数动不动 ==")
for key in ("A", "C", "G"):
    vals = [tot(per[(b, key)][TASKS[1]]["vec"]) for b in BLKS]
    print(f"  {key}: {vals}")
c04 = [tot(per[(b, "C")][TASKS[1]]["vec"]) for b in BLKS]
g04 = [tot(per[(b, "G")][TASKS[1]]["vec"]) for b in BLKS]
print(f"  C 与 G 的 t04 逐块差 = {[g-c for c, g in zip(c04, g04)]}")
print("  ⇒ 块相同却读出差值 ⇒ 那是噪声下限；差值全 0 ⇒ 本轮该块可作稳定参照")

print("\n== 逐格：翻转的是哪一格（t01 六项）==")
for blk in BLKS:
    for key in ("A", "C", "G"):
        t = per[(blk, key)][TASKS[0]]
        fails = [i for i, x in enumerate(t["vec"]) if not x]
        print(f"  块{blk} {key} 过 {tot(t['vec'])}/6  失败格 {fails}  why0={t['why'][:70]}")

print("\n== 块长 vs 效应（跨臂，非判据）==")
for key in ("A", "C", "G"):
    ln = {tid: len(EXP["arms"][key][tid]) for tid in TASKS}
    print(f"  臂 {key}: 块长 t01={ln[TASKS[0]]} t04={ln[TASKS[1]]}")

print("\n== n_eff：每臂的 (题×vec) 状态数 ==")
for key in ("A", "C", "G"):
    c = Counter(tuple(per[(b, key)][t]["vec"]) for b in BLKS for t in TASKS)
    print(f"  {key}: 状态 {len(c)} 种 / 6 格   t01 逐块 {t01[key]}")
print("\n⛔ 纪律 122：块不是独立样本 ⇒ 本文件不给 p 值。")

print("\n== 四前提汇总 ==")
alls = {"P1a": p1a, "P1b": p1b, "P1c": p1c, "P1d": p1d, "泄露": p_leak, "正例闸": gate}
print("  " + "  ".join(f"{k}={'✅' if v else '❌'}" for k, v in alls.items()))
print(f"  判决：{verdict if all(alls.values()) else 'UNKNOWN（前提缺失）'}")
