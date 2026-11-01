"""V34 历史复核（零 LLM）：把 §99 里引用的每一张表重新生成一遍。

⛔ 本脚本**只打印**，不写文件：证据路径由调用者重定向
（纪律 34：证据脚本不该写回自己的证据路径 —— 复跑会静默改写已冻结的证据）。

用法：
    python3 probe_v34_history.py suite    > p0_suite.txt
    python3 probe_v34_history.py round    > p0_round.txt
    python3 probe_v34_history.py shamap   > p0_shamap.txt
    python3 probe_v34_history.py samestate> p0_samestate.txt
    python3 probe_v34_history.py v27v28   > p0_v27v28.txt
    python3 probe_v34_history.py variants > p0_variants.txt
"""
from __future__ import annotations

import collections
import glob
import hashlib
import json
import os
import sys

CUR = "519dad774a3664612e6611b220b33f4a"      # v8 的 suite_sha
SCORER = "d4b1cbbfd7f1ef50d1e1a2a4cd95371ec7a82c05f6cb6d277e079814ea021076"
TASKS = ("e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni")
ORDER = ["v19", "v21", "v22", "v23", "v24", "v25", "v26", "v27",
         "v28", "v29", "v30", "v31", "v32", "v33"]


def h8(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()[:8]


def load_answers(d: str) -> dict:
    out = {}
    p = os.path.join(d, "answers.jsonl")
    if not os.path.exists(p):
        return out
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        a = json.loads(line)
        if a.get("kind") == "header":
            continue
        out[a.get("task_id")] = a.get("answer", "")
    return out


def runs():
    """每个 (轮, 臂) 一条：分数、口径、答案原文。"""
    out = []
    for rj in sorted(glob.glob("/tmp/v*/**/runs.jsonl", recursive=True)):
        d = os.path.dirname(rj)
        rnd = os.path.basename(os.path.dirname(d))
        arm = os.path.basename(d)
        ans = load_answers(d)
        for line in open(rj, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            per = (r.get("report") or {}).get("per_task", [])
            cal = r.get("caliber") or {}
            if not per:
                continue
            sc = {}
            for pt in per:              # 同名任务重复出现（--repeat 3）时后者覆盖
                sc[pt["id"]] = float(pt.get("score", 0))
            out.append({"rnd": rnd, "arm": arm, "tag": f"{rnd}/{arm}", "sc": sc,
                        "cal": cal, "sig": r.get("caliber_sig"),
                        "suite": cal.get("suite_sha"),
                        "scorer": cal.get("scorer_sha"),
                        "ans": ans, "raw": r})
    return out


def full(rs):
    return [x for x in rs if x["suite"] == CUR and all(t in x["sc"] for t in TASKS)]


def cmd_suite(rs):
    r6 = full(rs)
    print(f"# 同一 suite（{CUR}）的六题齐全 run = {len(r6)}")
    scorers = collections.Counter(x["scorer"] for x in r6)
    print(f"# scorer_sha 取值 = {len(scorers)} 种  "
          f"{'（全部同一把尺子）' if len(scorers) == 1 else dict(scorers)}")
    print()
    tab = collections.Counter()
    for x in r6:
        tab[(int(x["sc"]["e1wi"] > 0), int(x["sc"]["e1ni"] > 0))] += 1
    print("## e1wi × e1ni（同一 run 内）")
    for a in (1, 0):
        for b in (1, 0):
            print(f"   e1wi={a} e1ni={b}  n={tab.get((a, b), 0):3d}  和={16 * (a + b)}")
    print(f"\n两边都过 = {tab.get((1, 1), 0)} / {len(r6)}")
    print()
    print("## Σ 分布")
    z = collections.Counter(round(sum(x["sc"].values()) * 16) for x in r6)
    for k in sorted(z):
        print(f"   Σ={k:3d}  n={z[k]}")
    print(f"\nΣ > 48 的 run = {sum(v for k, v in z.items() if k > 48)}")


def cmd_round(rs):
    r6 = full(rs)
    g = collections.defaultdict(collections.Counter)
    for x in r6:
        g[x["rnd"]][round(sum(x["sc"].values()) * 16)] += 1
    print(f"{'轮':10s} {'runs':>4s}  Σ 分布")
    for r in ORDER + sorted(set(g) - set(ORDER)):
        if r in g:
            print(f"{r:10s} {sum(g[r].values()):4d}  {dict(sorted(g[r].items()))}")


def cmd_shamap(rs):
    r6 = full(rs)
    m = collections.defaultdict(lambda: [collections.Counter(), collections.Counter()])
    for x in r6:
        for t in ("e1wi", "e1ni"):
            a = x["ans"].get(t)
            if a is None:
                continue
            e = m[(t, h8(a))]
            e[0][round(x["sc"][t] * 16)] += 1
            e[1][x["rnd"]] += 1
    print(f"{'题':6s} {'sha':10s} {'16分':>4s} {'0分':>4s}  出现轮次")
    for (t, hh), (sc, rnds) in sorted(m.items()):
        print(f"{t:6s} {hh:10s} {sc.get(16, 0):4d} {sc.get(0, 0):4d}  {dict(sorted(rnds.items()))}")
    print()
    print("=== e1 容器 × 分数（答案原文分类）===")
    import re
    TW = re.compile(r"^\s*kept\s*=\s*set\(\)", re.M)
    LS = re.compile(r"^\s*kept\s*=\s*\[\]", re.M)
    for t in ("e1wi", "e1ni"):
        c = collections.Counter()
        for x in r6:
            a = x["ans"].get(t)
            if a is None:
                continue
            k = "set()" if TW.search(a) else ("list" if LS.search(a) else "其它")
            c[(k, int(x["sc"][t] > 0))] += 1
        print(f"  {t}:")
        for k in sorted(c, key=str):
            print(f"     容器={k[0]:6s} 过={k[1]}  n={c[k]}")


def _states(path: str):
    out, seen = [], {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        t = d.get("task")
        it = int(d.get("it") or 0)
        k = (t, it, seen.get((t, it), 0))
        seen[(t, it)] = seen.get((t, it), 0) + 1
        out.append((k, d.get("state", "")))
    return out


def cmd_samestate(rs):
    groups = collections.defaultdict(list)
    for f in sorted(glob.glob("/tmp/v*/states_*.jsonl")):
        rnd = os.path.basename(os.path.dirname(f))
        arm = os.path.basename(f)[len("states_"):-len(".jsonl")]
        ans = load_answers(os.path.join(os.path.dirname(f), f"bench_{arm}"))
        for k, st in _states(f):
            if k[1] != 1 or k[2] != 0 or k[0] not in ("e1wi", "e1ni"):
                continue
            if k[0] not in ans:
                continue
            groups[hashlib.sha256(st.encode()).hexdigest()[:10]].append(
                (f"{rnd}/{arm}", k[0], h8(ans[k[0]]), len(st)))
    print("=== 同 state（逐字节）下答案是否唯一 ===")
    inc = 0
    for fp, lst in sorted(groups.items(), key=lambda x: -len(x[1])):
        if len(lst) < 2:
            continue
        s = {x[2] for x in lst}
        if len(s) > 1:
            inc += 1
        print(f"  state={fp} n={len(lst):3d} 答案种数={len(s)} "
              f"{'❗不一致' if len(s) > 1 else '一致'}")
        for x in lst:
            print(f"        {x[0]:22s} {x[1]} ans={x[2]} len={x[3]}")
    print(f"\n同 state 但答案不一致的组 = {inc}")


def cmd_v27v28(_rs):
    a = _states("/tmp/v27/states_s12_r1.jsonl")
    b = _states("/tmp/v28/states_s12_r1.jsonl")
    print(f"v27 {len(a)} 行 ｜ v28 {len(b)} 行")
    da, db = dict(a), dict(b)
    common = sorted(set(da) & set(db))
    print(f"逐字节不同的 state = {sum(1 for k in common if da[k] != db[k])} / {len(common)}")
    for k in common:
        print(f"  {k}  len {len(da[k])} vs {len(db[k])}  {'同' if da[k] == db[k] else '异'}")


def cmd_variants(rs):
    r6 = full(rs)
    print(f"{'轮/臂':26s} {'Σ':>3s} {'e1wi':10s} {'e1ni':10s} {'sc':>5s} {'vs':>3s} {'re':>3s} {'sig':16s}")
    for x in r6:
        lim = x["cal"].get("limits") or {}
        print(f"{x['tag']:26s} {round(sum(x['sc'].values()) * 16):3d} "
              f"{h8(x['ans'].get('e1wi')):10s} {h8(x['ans'].get('e1ni')):10s} "
              f"{str(lim.get('state_chars')):>5s} {str(lim.get('verify_static')):>3s} "
              f"{str(lim.get('reward_echo')):>3s} {str(x['sig'])}")


def cmd_stable(rs):
    """跨轮的答案稳定性：题 → 不同的答案 sha 有几个。用来排除「模型换了」。"""
    r6 = full(rs)
    for t in TASKS:
        c = collections.Counter()
        where = collections.defaultdict(set)
        for x in r6:
            a = x["ans"].get(t)
            if a is None:
                continue
            k = h8(a)
            c[k] += 1
            where[k].add(x["rnd"])
        print(f"\n{t}: 有答案 {sum(c.values())} 份，唯一 sha {len(c)} 个")
        for k, n in c.most_common(4):
            print(f"   {k}  n={n:3d}  出现在 {sorted(where[k])}")


CMDS = {"suite": cmd_suite, "round": cmd_round, "shamap": cmd_shamap,
        "samestate": cmd_samestate, "v27v28": cmd_v27v28, "variants": cmd_variants,
        "stable": cmd_stable}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "suite"
    if which not in CMDS:
        raise SystemExit(f"只认 {sorted(CMDS)}")
    CMDS[which](runs() if which != "v27v28" else [])
