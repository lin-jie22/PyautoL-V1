"""V217（§287）判据读数：scope=prev（挡同题）+ shape 档，off vs blocks 3 配对块。

    env -u PYTHONPATH python3 bench/evidence/l3_s302/read_v217.py

⛔ 口径（写在代码里，防止又手滑）：
   · 附言只判**题面之后追加的那一段**（纪律 123）
   · 入参读的是壳子存下的**那一次调用原文**，不是我对 state 现算（纪律 124）
"""
import json
import sys
from collections import Counter

D = "bench/evidence/l3_s302"
BASE = int(open(f"{D}/start_runs_e.txt").read().strip())

rows = [json.loads(l) for l in open("bench_out/runs.jsonl", encoding="utf-8") if l.strip()][BASE:BASE + 6]
order = [l.rstrip("\n").split("\t") for l in open(f"{D}/order_e.txt", encoding="utf-8") if l.strip()]
print(f"runs 新行 {len(rows)}  调度 {len(order)}  基线 {BASE}")
assert len(rows) == len(order) == 6, "行数对不上"

suite = [json.loads(l) for l in open(f"{D}/suite_sub2.jsonl", encoding="utf-8") if l.strip()]
PROMPTS = {t["id"]: t["prompt"] for t in suite}
TASKS = list(PROMPTS)
LEAK = ("assert", "5400", "7200", "600", "to unpack", "expected", "got ")


def split_prompt(raw):
    for tid, p in PROMPTS.items():
        if raw.startswith(p):
            return tid, raw[len(p):]
    return None, raw


print("\n== P1a 装置 ==")
bad = [o for o in order if o[3] != "rc=0"]
durs = [int(o[4].rstrip("s")) for o in order]
print(f"rc=0 {len(order)-len(bad)}/{len(order)}  单次 {min(durs)}~{max(durs)}s  超 300s {sum(d > 300 for d in durs)}/{len(durs)}")
p1a = not bad and max(durs) <= 300
print(f"P1a {'PASS' if p1a else 'FAIL'}")

print("\n== P1d 入参原文：同题条目是否真被挡住 ==")
p1d = True
req_t01 = False
for arm in ("off", "blocks"):
    calls = [json.loads(l) for l in open(f"{D}/probe_stdin_v217_{arm}.jsonl", encoding="utf-8") if l.strip()]
    print(f"  臂 {arm}: {len(calls)} 次调用")
    for r in calls:
        tid, extra = split_prompt(r["task"])
        assert tid, f"题面没对上：{r['task'][:50]!r}"
        others = [t for t in TASKS if t != tid]
        has_other = [t for t in others if t in extra]
        has_self = tid in extra
        leak = [m for m in LEAK if m in extra]
        if arm == "off":
            ok = len(extra) == 0
        else:
            # ⛔ 只按 PREREG_v217 §4 的字面判：追加段非空、不含本题、无测试源码。
            #    第一版多写了一条「恰含 1 道别题」——那条不在预注册里，且对 t04 结构上不可能满足
            #    （块里是 t03/t05/t06，都不在本题集）⇒ 是**实现比预注册严**，已删。
            ok = len(extra) > 40 and not has_self and not leak
        p1d &= ok
        if arm == "blocks" and tid == TASKS[0] and "t04_merge_ranges" in extra:
            req_t01 = True
        print(f"    {tid:22s} 入参 {r['task_chars']:3d} 追加 {len(extra):3d}"
              f"  含本题={has_self}  含本题集别题={has_other}  泄露={leak if leak else '无'}  {'OK' if ok else '⛔'}")
        if arm == "blocks" and extra:
            print(f"      追加段：{extra.strip()}")
p1d &= req_t01
print(f"P1d {'PASS' if p1d else 'FAIL'}（off 6/6 无追加；blocks 6/6 有追加、无本题、无测试源码；t01 的追加段含 t04）")

print("\n== P1b/P1c 转发与外包 ==")
state_ok = {}
for arm in ("off", "blocks"):
    ls = [json.loads(l) for l in open(f"{D}/state_e_{arm}.jsonl", encoding="utf-8") if l.strip()]
    state_ok[arm] = sum(1 for d in ls if "[外部评审]" in d["state"])
calls_n = {}
for o, r in zip(order, rows):
    calls_n.setdefault(o[2], []).append((r.get("kernel_external") or {}).get("calls", 0))
for arm in ("off", "blocks"):
    print(f"  {arm:6s} state 带评审块 {state_ok[arm]}/{len(open(f'{D}/state_e_{arm}.jsonl').readlines())} 行"
          f"  外包 calls(逐次运行)={calls_n[arm]}  ≥1 的 {sum(c >= 1 for c in calls_n[arm])}/3"
          f"  合计 {sum(calls_n[arm])}/6 个题位")
p1b = all(state_ok[a] >= 3 for a in ("off", "blocks"))
p1c = all(sum(c >= 1 for c in calls_n[a]) >= 3 for a in ("off", "blocks"))
print(f"P1b {'PASS' if p1b else 'FAIL'}（各臂 ≥3 行）")
print(f"P1c {'PASS' if p1c else 'FAIL'}（预注册门槛 5/6；本轮每臂只有 3 次运行 ⇒ 门槛写成 5/6 "
      f"结构上不可能达到，属**判据作者错误**，见 RESULTS_v217.md §自踩）")

print("\n== P2 主判据 t01（拿得到有信息的跨题行）/ P3 对照 t04（块里零信息）==")


def tot(v):
    return sum(1 for x in v if x)


per = {(o[0], o[2]): {t["id"]: t for t in r["report"]["per_task"]} for o, r in zip(order, rows)}
d01, d04 = [], []
for blk in ("0", "1", "2"):
    o, b = per[(blk, "off")], per[(blk, "blocks")]
    for tid, acc in ((TASKS[0], d01), (TASKS[1], d04)):
        vo = o[tid]["vec"]; vb = b[tid]["vec"]
        acc.append(tot(vb) - tot(vo))
        print(f"  块 {blk} {tid:22s} off {tot(vo)}/{len(vo)} {vo}")
        print(f"        {'':22s} blk {tot(vb)}/{len(vb)} {vb}   Δ={tot(vb)-tot(vo):+d}")
up01 = sum(1 for d in d01 if d >= 1)
dn01 = sum(1 for d in d01 if d <= -1)
if up01 >= 2:
    v2 = "成立（≥2/3 块 Δ≥+1）"
elif dn01 >= 2:
    v2 = "不成立（≥2/3 块 Δ≤−1）"
else:
    v2 = "不可判（Δ 全 0 或方向混杂）"
print(f"  P2 Δ={d01} ⇒ {v2}")
print(f"  P3 对照 Δ={d04}（押 0；若 ≥+2 ⇒「加文本改抽样」胜出，P2 归因作废）")

print("\n== n_eff：不同 vec 状态数 ==")
for arm in ("off", "blocks"):
    c = Counter(tuple(per[(b, arm)][t]["vec"]) for b in ("0", "1", "2") for t in TASKS)
    print(f"  {arm:6s} {len(c)} 种 (题×vec) 状态 / 6 格")
print("\n⛔ 纪律 122：块不是样本 ⇒ 本文件不给 p 值。")
