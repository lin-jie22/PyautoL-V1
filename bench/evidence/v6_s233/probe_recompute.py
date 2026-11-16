#!/usr/bin/env python3
"""V144 跑前取证：现有账本到底能不能被「从 per_task 重算」复核。

⛔ 这是**读数**，不是判据。判据在 PREREG.md 里，跑前落盘。
本脚本只回答三件事：
  1) 145 行里有多少行自带重算所需的全部输入；
  2) 用同样的求和口径重算，与写下的 mean 差多少（是不是 0.0）；
  3) 按候选规则分类，每一条规则各拒掉几行、放行几行。
"""
import json
import statistics
from collections import Counter

LEDGER = "bench_out/runs.jsonl"


def load(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def n_tasks_of(rec):
    """分母从哪来：attempted = n_tasks × repeat。"""
    rep = rec.get("repeat")
    att = (rec.get("report") or {}).get("attempted")
    if not isinstance(rep, int) or rep <= 0 or not isinstance(att, int):
        return None
    if att % rep:
        return None
    return att // rep


def recompute(rec):
    """按 run_suite 的原口径重算：每轮 sum(score)/n_tasks，再 fmean。"""
    n_tasks = n_tasks_of(rec)
    if n_tasks is None:
        return None
    by_rep = {}
    for row in rec["report"]["per_task"]:
        by_rep.setdefault(row.get("rep"), []).append(float(row["score"]))
    if not by_rep:
        return None
    rounds = [sum(v) / n_tasks for _, v in sorted(by_rep.items())]
    return statistics.fmean(rounds), n_tasks, len(by_rep)


def classify(rec):
    """返回 (规则名, 详情)。规则顺序即语义：先看能不能算，再看该不该信。"""
    rep = rec.get("report") or {}
    m = rec.get("measurement") or {}
    if "per_task" not in rep:
        return "R1_无明细", ""
    if "mean" not in m:
        return "R1_无均值", ""
    pt = rep["per_task"]
    if not pt:
        return "R1_明细空", f"attempted={rep.get('attempted')}"
    att, recd = rep.get("attempted"), rep.get("recorded")
    skip = rep.get("skipped_no_answer", 0)
    fail = rep.get("failed_scorer", 0)
    if None in (att, recd):
        return "R2_无计数", ""
    if att != recd + skip + fail:
        return "R3_账不平", f"{att} != {recd}+{skip}+{fail}"
    if len(pt) != recd:
        return "R4_明细条数≠recorded", f"{len(pt)} != {recd}"
    got = recompute(rec)
    if got is None:
        return "R5_分母不可导", f"attempted={att} repeat={rec.get('repeat')}"
    val, n_tasks, n_rounds = got
    if n_rounds != rec.get("repeat") or int(m.get("n", -1)) != n_rounds:
        return "R5_轮数不符", f"重算{n_rounds} 记录{rec.get('repeat')}/{m.get('n')}"
    return "R6_重算", f"{val!r} vs {m['mean']!r} Δ={abs(val - float(m['mean'])):.3e}"


def main():
    rows = load(LEDGER)
    print(f"账本 {LEDGER}  行数={len(rows)}")
    print(f"有 run_id={sum(1 for r in rows if r.get('run_id'))}  "
          f"有 verdict 键={sum(1 for r in rows if 'verdict' in r)}")
    cnt = Counter()
    deltas = []
    for r in rows:
        rule, detail = classify(r)
        cnt[rule] += 1
        if rule == "R6_重算":
            val = float(detail.split(" vs ")[0])
            deltas.append(abs(val - float(r["measurement"]["mean"])))
    print("\n分类（分母=%d）：" % len(rows))
    for k, v in sorted(cnt.items()):
        print(f"  {k:26s} {v:4d}  ({v / len(rows) * 100:.1f}%)")
    if deltas:
        print(f"\n重算差值：n={len(deltas)} max={max(deltas):.3e} "
              f"非零={sum(1 for d in deltas if d != 0.0)}")
    # 逐行细节（只打非 R6 的，便于归因）
    print("\n非 R6 的行：")
    for i, r in enumerate(rows, 1):
        rule, detail = classify(r)
        if rule != "R6_重算":
            print(f"  L{i:3d} {r.get('run_id') or '<无身份证>':18s} {rule}  {detail}")


if __name__ == "__main__":
    main()
