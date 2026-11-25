"""V247 汇总：把 readings_v247.jsonl 变成 PREREG §4/§5 的读数。

⛔ 只读读数，不改读数。
⛔ P2 先机器给候选，再由人逐条核（本文件打印 16 条原文供核）。
用法：python3 analyse_v247.py
"""
from __future__ import annotations

import collections
import difflib
import json
import pathlib
import re
import statistics

D = pathlib.Path(__file__).resolve().parent
START = pathlib.Path("/Users/linshaojie/V1/bench/evidence/l9_s310/cand/90579ecebe633352.py")
start_code = START.read_text(encoding="utf-8") if START.exists() else ""
rows = [json.loads(x) for x in
        (D / "readings_v247.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]

by = collections.defaultdict(list)
for r in rows:
    by[r["arm"]].append(r)

N = 6


def arm_stats(arm: str) -> dict:
    rs = [r for r in by[arm] if "err" not in r]
    scored = [r for r in rs if "score" in r]
    sc = [r["score"] for r in scored]
    shas = {r.get("sha") for r in scored if r.get("sha")}
    has_sem = bool(scored) and "sem_in_code" in scored[0]
    return {
        "n": len(rs), "n_scored": len(scored), "err": len(by[arm]) - len(rs),
        "hit6": sum(1 for s in sc if s == N),
        "dist": dict(sorted(collections.Counter(sc).items())),
        "copy": sum(1 for r in scored if r.get("same_as_start")),
        "uniq_sha": len(shas),
        "sim_med": round(statistics.median([r["sim"] for r in scored]), 3)
        if scored else None,
        "sem": sum(1 for r in scored if r.get("sem_in_code")) if has_sem else None,
        "bug": sum(1 for r in scored if r.get("kept_bug")) if has_sem else None,
        "s_bytes": (min(r["s_bytes"] for r in scored), max(r["s_bytes"] for r in scored))
        if scored and "s_bytes" in scored[0] else None,
    }


print("== 臂汇总 ==")
for arm in ("A", "Q", "S"):
    print(arm, json.dumps(arm_stats(arm), ensure_ascii=False))

q = [r for r in by["Q"] if "err" not in r]
print(f"\n== Q 有效 {sum(1 for r in q if r.get('valid'))}/{len(q)} ==")
pat_a = re.compile(r"parts\s*=\s*\[\s*'10'\s*\]")
pat_b = re.compile(r"parts\s*=\s*\[\s*'10'\s*,\s*''\s*\]")
pat_min0 = re.compile(r"分钟数(?:是)?\s*parts\[0\]")
pat_min1 = re.compile(r"分钟数(?:是)?\s*parts\[1\]")
print(f"答 parts=['10']      : {sum(1 for r in q if pat_a.search(r['q_text']))}")
print(f"答 parts=['10','']   : {sum(1 for r in q if pat_b.search(r['q_text']))}")
print(f"指认 parts[0] 为分钟 : {sum(1 for r in q if pat_min0.search(r['q_text']))}")
print(f"指认 parts[1] 为分钟 : {sum(1 for r in q if pat_min1.search(r['q_text']))}")

print("\n== Q 原文 16 条（人工核 P2）==")
for r in q:
    one = r["q_text"].strip().replace("\n", " / ")
    print(f"Q#{r['i']:02d} {one}")

# P2 判对：明确「分钟数是 parts[0]」
p2 = {r["i"]: bool(pat_min0.search(r["q_text"])) for r in q}
s_by_i = {r["i"]: r for r in by["S"] if "err" not in r}
pair = [i for i in p2 if p2[i] and i in s_by_i]
bad = [i for i in pair if s_by_i[i]["score"] < N]
print(f"\n== P3 ==")
print(f"P2 对的条数 = {len(pair)}；其中 S 仍 <6/6 = {len(bad)}"
       f" ⇒ {len(bad)}/{len(pair)}"
       f" = {len(bad) / len(pair) if pair else float('nan'):.2f}"
       f"（门槛 0.75）")

print("\n== 逐条对齐（i, Q 指认, S 分, S 照抄, sem, bug）==")
for i in sorted(p2):
    s = s_by_i.get(i, {})
    print(f"  #{i:02d} P2={'对' if p2[i] else '错'} S={s.get('score')}/{N}"
          f" copy={s.get('same_as_start')} sem={s.get('sem_in_code')}"
          f" bug={s.get('kept_bug')}")

# 自述-行为一致
cons = [i for i in p2 if i in s_by_i and bool(s_by_i[i].get("sem_in_code")) == p2[i]]
print(f"\nself_consistent（sem_in_code == P2判对）= {len(cons)}/{len(p2)}")
print("  ⚠️ 本读数里 P2 全对、sem 全 False ⇒ self_consistent 全 False，"
      "含义是「说对了、写的时候没用」")


# ---- 非照抄产出的行级改动（纪律 227 的补丁：cand 已落盘）----
def _lines_diff(a: str, b: str, limit: int = 12) -> list[str]:
    d = difflib.unified_diff((a or "").splitlines(), (b or "").splitlines(),
                             lineterm="", n=0)
    return [x for x in d if (x.startswith("+") or x.startswith("-"))
            and not x.startswith(("+++", "---"))][:limit]


print("\n== 非照抄产出的改动（行级，相对起点）==")
no_cand = 0
n_diff = 0
for arm in ("A", "S"):
    for r in by[arm]:
        if "err" in r or r.get("same_as_start") is not False:
            continue
        c = r.get("cand")
        if not c:
            no_cand += 1
            print(f"  {arm}#{r['i']:02d} ⚠️ cand 缺失（这次跑又没落代码）")
            continue
        n_diff += 1
        print(f"  {arm}#{r['i']:02d} score={r['score']}/6 sim={r['sim']:.2f}")
        for line in _lines_diff(start_code, c):
            print(f"      {line}")
print(f"\n  非照抄条数 = {n_diff}；cand 缺失 = {no_cand}")
