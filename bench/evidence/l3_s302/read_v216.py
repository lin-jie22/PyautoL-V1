"""V216（§286）判据读数：shape 档下 off vs blocks 的 3 配对块。

只读已落盘证据，不重跑、不改判据。
    env -u PYTHONPATH python3 bench/evidence/l3_s302/read_v216.py

⛔ 自踩登记（本文件第一版）：P1d 原来是用 `handoff_text(parts_of(state))` **现算**附言，
   于是 `off` 臂也"算出"了 313 字附言 ⇒ 断言当场炸。那只能证明「内核有这个函数」，
   不能证明「这一次调用收到了什么」（纪律 114）。改为读 `probe_stdin_v216_*.jsonl` ——
   壳子存下的**那次调用的入参原文**。
⛔ 第二个口径坑（纪律 89）：t01 的**题面自带** `'1h30m'` / `'2h'` / `'10m'` 字面
   （题目要求就是解析这些），拿它们当泄露标记会恒真 ⇒ 泄露只在**题面之后追加的那段**里判。
"""
import json
import sys
from collections import Counter

D = "bench/evidence/l3_s302"
BASE = int(open(f"{D}/start_runs_c.txt").read().strip())

rows = [json.loads(l) for l in open("bench_out/runs.jsonl", encoding="utf-8") if l.strip()][BASE:BASE + 6]
order = [l.rstrip("\n").split("\t") for l in open(f"{D}/order_c.txt", encoding="utf-8") if l.strip()]
print(f"runs 新行 {len(rows)}  调度 {len(order)}  基线 {BASE}")
assert len(rows) == len(order) == 6, "行数对不上"

prompts = {json.loads(l)["id"]: json.loads(l)["prompt"]
           for l in open(f"{D}/suite_sub2.jsonl", encoding="utf-8") if l.strip()}
PROMPTS = list(prompts.values())

#: 泄露标记：只在**追加段**里判。`assert ` / 期望值 / 异常消息正文都算。
LEAK = ("assert", "5400", "7200", "600", "to unpack", "does not support",
        "expected", "got ")


def split_prompt(raw):
    """返回 (命中的题面, 追加段)。按最长前缀匹配，找不到返回 (None, raw)。"""
    for p in sorted(PROMPTS, key=len, reverse=True):
        if raw.startswith(p):
            return p, raw[len(p):]
    return None, raw


print("\n== P1a 装置 ==")
bad = [o for o in order if o[3] != "rc=0"]
durs = [int(o[4].rstrip("s")) for o in order]
print(f"rc=0 {len(order)-len(bad)}/{len(order)}  单次 {min(durs)}~{max(durs)}s  超 300s {sum(d > 300 for d in durs)}/{len(durs)}")
p1a = not bad and max(durs) <= 300
print(f"P1a {'PASS' if p1a else 'FAIL'}")

print("\n== P1d 无泄露：壳子存下的**真实入参**（3 块 × 2 题 = 6 次/臂）==")
p1d = True
for arm in ("off", "blocks"):
    calls = [json.loads(l) for l in open(f"{D}/probe_stdin_v216_{arm}.jsonl", encoding="utf-8") if l.strip()]
    print(f"  臂 {arm}: {len(calls)} 次调用")
    for r in calls:
        p, extra = split_prompt(r["task"])
        assert p is not None, f"题面没对上：{r['task'][:60]!r}"
        hits = [m for m in LEAK if m in extra]
        ok = (len(extra) == 0) if arm == "off" else (len(extra) > 40 and not hits)
        p1d &= ok
        print(f"    入参 {r['task_chars']:3d} = 题面 {len(p):3d} + 追加 {len(extra):3d}"
              f"  追加段泄露标记 {hits if hits else '无'}  {'OK' if ok else '⛔'}")
        if arm == "blocks" and extra:
            print(f"      追加段原文：{extra.strip()}")
print(f"P1d {'PASS' if p1d else 'FAIL'}（off 6/6 无追加；blocks 6/6 有追加且追加段无泄露）")

print("\n== P1b/P1c 转发与外包 ==")
state_ok = {}
for arm in ("off", "blocks"):
    ls = [json.loads(l) for l in open(f"{D}/state_c_{arm}.jsonl", encoding="utf-8") if l.strip()]
    state_ok[arm] = sum(1 for d in ls if "[外部评审]" in d["state"])
calls = {}
for o, r in zip(order, rows):
    calls.setdefault(o[2], []).append((r.get("kernel_external") or {}).get("calls", 0))
for arm in ("off", "blocks"):
    print(f"  {arm:6s} state 带评审块 {state_ok[arm]}/6 行  外包 calls={calls[arm]}  ≥1 的 {sum(c >= 1 for c in calls[arm])}/3")
p1b = all(state_ok[a] >= 3 for a in ("off", "blocks"))
p1c = all(sum(c >= 1 for c in calls[a]) >= 3 for a in ("off", "blocks"))
print(f"P1b {'PASS' if p1b else 'FAIL'}（各臂 ≥3 行）")
print(f"P1c {'PASS' if p1c else 'FAIL'}（各臂 3/3 次外包）")

print("\n== P2 主判据：块级总分（分母 12）==")


def tot(v):
    return sum(1 for x in v if x)


per = {(o[0], o[2]): {t["id"]: t for t in r["report"]["per_task"]} for o, r in zip(order, rows)}
deltas = []
for blk in ("0", "1", "2"):
    o = per[(blk, "off")]
    b = per[(blk, "blocks")]
    to = tot(o["t01_parse_duration"]["vec"]) + tot(o["t04_merge_ranges"]["vec"])
    tb = tot(b["t01_parse_duration"]["vec"]) + tot(b["t04_merge_ranges"]["vec"])
    d = tb - to
    deltas.append(d)
    print(f"  块 {blk}: off {to}/12  blocks {tb}/12  Δ={d:+d}")
    print(f"          t01 off {o['t01_parse_duration']['vec']}  blocks {b['t01_parse_duration']['vec']}")
    print(f"          t04 off {o['t04_merge_ranges']['vec']}  blocks {b['t04_merge_ranges']['vec']}")
up = sum(1 for d in deltas if d >= 0)
pos = sum(1 for d in deltas if d >= 1)
neg = sum(1 for d in deltas if d <= -1)
if up == 3 and pos >= 1:
    verdict = "成立（3/3 块 Δ≥0 且 ≥1 块 Δ≥+1）"
elif neg >= 2:
    verdict = "不成立（≥2/3 块 Δ≤−1）"
else:
    verdict = "不可判（Δ 全为 0 或方向混杂）"
print(f"  Δ 序列 {deltas}  ⇒ P2 {verdict}")

print("\n== n_eff：不同 vec 状态数（纪律 122）==")
for arm in ("off", "blocks"):
    c = Counter((tuple(per[(b, arm)]["t01_parse_duration"]["vec"]),
                 tuple(per[(b, arm)]["t04_merge_ranges"]["vec"])) for b in ("0", "1", "2"))
    print(f"  {arm:6s} {len(c)} 种状态 / 3 块")
    for k, v in c.items():
        print(f"     n={v}  t01 {tot(k[0])}/6 {k[0]}  t04 {tot(k[1])}/6 {k[1]}")
print("\n⛔ 纪律 122：块不是样本 ⇒ 本文件不给 p 值。")
