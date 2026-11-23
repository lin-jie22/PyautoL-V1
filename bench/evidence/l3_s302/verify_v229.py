#!/usr/bin/env python3
# V229 离线核对：四格判 EXACT / NAME-FIX，以及 P1 前缀。
# 只读证据文件，不调用 LLM、不改任何东西。
import json, hashlib, sys
from pathlib import Path

D = Path(__file__).resolve().parent
sh = lambda s: hashlib.sha256(s.encode()).hexdigest()[:12]


def rows(f):
    return [json.loads(l) for l in open(D / f, encoding="utf-8")]


def probe(f):
    out = {}
    for ln in open(D / f, encoding="utf-8"):
        d = json.loads(ln)
        t = d["task"]
        tid = "t01_parse_duration" if "parse_duration" in t[:80] else "t04_merge_ranges"
        out[tid] = (t, d)
    return out


own = {r["task_id"]: r["answer"] for r in rows("crit_v226.jsonl")}
fed = {"B": {r["task_id"]: r["answer"] for r in rows("crit_v229.jsonl")},
       "C": {r["task_id"]: r["answer"] for r in rows("crit_v229c.jsonl")}}
v226b = probe("probe_d_h.jsonl")

# 本轮产出：按 run_id 取
runs = [json.loads(l) for l in open(Path("/Users/linshaojie/V1/bench_out/runs.jsonl"), encoding="utf-8")]
last2 = [r["run_id"] for r in runs[-2:]]
ans = [a for a in (json.loads(l) for l in open("/Users/linshaojie/V1/bench_out/answers.jsonl", encoding="utf-8"))
       if a.get("kind") == "answer" and a.get("run_id") in last2]
got = {}
for a in ans:
    got[(a["run_id"], a["task_id"])] = a["answer"]

print("run_id 顺序:", last2)
fails = 0
for arm, run_id in (("B", last2[0]), ("C", last2[1])):
    pr = probe("probe_g_name.jsonl" if arm == "B" else "probe_g_body.jsonl")
    print("\n===== 臂 %s（run %s）" % (arm, run_id))
    for tid in ("t01_parse_duration", "t04_merge_ranges"):
        t, d = pr[tid]
        i = t.find("[上版代码]")
        pre = t[:i]
        o = v226b[tid][0]
        pre_o = o[:o.find("[上版代码]")]
        f = fed[arm][tid]
        out = got[(run_id, tid)]
        exact = out == f
        namefix = out == own[tid]
        seg = t[i:]
        print("  %-18s state=%d block@%d seg=%d" % (tid, d["task_chars"], i, len(seg)))
        print("     P1 前缀==V226b : %s (%s)" % (pre == pre_o, sh(pre)))
        print("     P1 块==投喂     : %s (seg %s / fed %s)" % (seg == "[上版代码] " + f + "\n" or seg == "[上版代码] " + f, sh(seg), sh(f)))
        print("     输出 %s(%d)  投喂 %s(%d)" % (sh(out), len(out), sh(f), len(f)))
        print("     EXACT   : %s" % exact)
        print("     NAME-FIX: %s（== 该题原本的码 %s）" % (namefix, sh(own[tid])))
        ok = (pre == pre_o) and (seg.startswith("[上版代码] " + f))
        fails += 0 if ok else 1
print("\nP1 失败格数:", fails)
sys.exit(0 if fails == 0 else 1)
