#!/usr/bin/env python3
# V230 离线核对：抄洞 / 补洞 / 重写 的字节级判定 + 离线判分复算。零模型、零网络。
import json, re, hashlib, difflib, sys
from pathlib import Path

D = Path(__file__).resolve().parent
ROOT = Path("/Users/linshaojie/V1")
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


def code_of(a):
    m = re.search(r"```(?:python)?\n(.*?)```", a, re.S)
    return m.group(1) if m else a


def score_of(tests, code):
    v = []
    for t in tests:
        ns = {}
        try:
            exec(code, ns); exec(t, ns); v.append(1)
        except Exception:
            v.append(0)
    return sum(v), v


suite = {json.loads(l)["id"]: json.loads(l) for l in open(D / "suite_sub2.jsonl", encoding="utf-8")}
own = {r["task_id"]: r["answer"] for r in rows("crit_v226.jsonl")}      # 挖洞前
fed = {r["task_id"]: r["answer"] for r in rows("crit_v230.jsonl")}      # 挖洞后（投喂）
v226b = probe("probe_d_h.jsonl")
pr = probe("probe_g_hole.jsonl")

runs = [json.loads(l) for l in open(ROOT / "bench_out/runs.jsonl", encoding="utf-8")]
run_id = runs[-1]["run_id"]
answers = {a["task_id"]: a["answer"] for a in
           (json.loads(l) for l in open(ROOT / "bench_out/answers.jsonl", encoding="utf-8"))
           if a.get("kind") == "answer" and a.get("run_id") == run_id}

print("run_id", run_id)
fails = 0
for tid in ("t01_parse_duration", "t04_merge_ranges"):
    t, d = pr[tid]
    i = t.find("[上版代码]")
    pre, seg = t[:i], t[i:]
    o = v226b[tid][0]
    pre_o = o[:o.find("[上版代码]")]
    f, ow, out = fed[tid], own[tid], answers[tid]
    tests = suite[tid]["tests"]

    print("\n===== %s  (state=%d, block@%d)" % (tid, d["task_chars"], i))
    print("  P1 前缀==V226b      : %s (%s)" % (pre == pre_o, sh(pre)))
    print("  P1 块==投喂(挖洞后)  : %s (seg %s / fed %s)" % (seg.startswith("[上版代码] " + f), sh(seg), sh(f)))
    print("  投喂 == 挖洞前?     : %s  (挖洞前 %s / 投喂 %s)" % (f == ow, sh(ow), sh(f)))
    print("  输出               : %s (%d 字节)" % (sh(out), len(out)))
    print("  抄洞 COPY-HOLE      : %s" % (out == f))
    print("  补洞 RESTORE        : %s  (输出 == 挖洞前那份码)" % (out == ow))
    sm = difflib.SequenceMatcher(None, out, f)
    m = sm.find_longest_match(0, len(out), 0, len(f))
    print("  与投喂最长公共子串   : %d 字节" % m.size)

    # 输出 vs 投喂 的差异行
    dl = list(difflib.unified_diff(f.split("\n"), out.split("\n"), "fed", "out", lineterm="", n=1))
    print("  diff(fed→out) 行数   : %d" % len([x for x in dl if x.startswith(("+", "-")) and not x.startswith(("+++", "---"))]))
    for x in dl:
        print("     ", x)

    n_out, v_out = score_of(tests, code_of(out))
    n_fed, v_fed = score_of(tests, code_of(f))
    n_own, v_own = score_of(tests, code_of(ow))
    print("  离线判分  投喂 %d/6 %s | 输出 %d/6 %s | 挖洞前 %d/6 %s"
          % (n_fed, "".join(map(str, v_fed)), n_out, "".join(map(str, v_out)), n_own, "".join(map(str, v_own))))
    print("  ⇒ 输出分数 == 挖洞前? %s ；输出分数 > 投喂? %s" % (n_out == n_own, n_out > n_fed))

    ok = (pre == pre_o) and seg.startswith("[上版代码] " + f)
    fails += 0 if ok else 1

print("\nP1 失败格数:", fails)
sys.exit(0 if fails == 0 else 1)
