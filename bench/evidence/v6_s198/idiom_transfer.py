#!/usr/bin/env python3
"""V109：惯用法能不能跨题 —— 「累积」攒的是什么。

零模型、零网络。只有一步需要判分：26 个实现各用**本题 tests** 重算一次（约 12 秒）。

§0 语料与打分
§1 惯用法抽取（方法调用链 + 格式说明符，参数保留）
§2 S2 单题抬分
§3 S1 跨题抬分（核心）
§4 S3 负迁移
§5 混杂检查（同一实现是否共享多条惯用法）
"""
from __future__ import annotations

import collections
import hashlib
import json
import pathlib
import re
import statistics
import subprocess
import sys

EV = pathlib.Path(__file__).resolve().parent.parent
ROOT = EV.parent.parent
SCORER = ROOT / "bench" / "scorers" / "code_exec.py"

_FENCE = re.compile(r"```(?:python|py)?[ \t]*\r?\n(.*?)```", re.S)
_CALL = re.compile(r"\.(\w+)\(([^()]*)\)")
_FMT = re.compile(r"\{:[^}]*\}")


def code_of(answer: str) -> str:
    m = _FENCE.search(answer or "")
    return (m.group(1) if m else (answer or "")).strip("\n")


def harvest() -> dict[str, dict[str, str]]:
    """task_id -> {impl_hash: 裸代码}"""
    out: dict[str, dict[str, str]] = collections.defaultdict(dict)
    for rd in sorted(EV.iterdir()):
        for ans in sorted(rd.glob("answers/**/answers.jsonl")):
            for ln in ans.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                r = json.loads(ln)
                if r.get("kind") != "answer":
                    continue
                tid, a = r.get("task_id"), r.get("answer") or ""
                if not tid or not a:
                    continue
                out[tid][hashlib.sha256(a.encode()).hexdigest()[:8]] = code_of(a)
    return out


def task_of(tid: str) -> dict | None:
    for d in (ROOT / "bench" / "heldout", ROOT / "bench" / "tasks"):
        if not d.exists():
            continue
        for f in sorted(d.glob("*.jsonl")):
            for ln in f.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                r = json.loads(ln)
                if r.get("id") == tid:
                    return r
    return None


def idioms_of(code: str) -> set[str]:
    out = set()
    for m in _CALL.finditer(code):
        out.add(f".{m.group(1)}({m.group(2).strip()})")
    for m in _FMT.finditer(code):
        out.add(m.group(0))
    return out


def main() -> None:
    corpus = harvest()
    print("## §0 语料与打分（每题用自己的 tests）")
    scores: dict[str, dict[str, float]] = {}
    for tid, impls in sorted(corpus.items()):
        t = task_of(tid)
        if t is None:
            print(f"  ⛔ {tid}: 找不到题目定义，跳过")
            continue
        pt = {"id": t["id"], "prompt": t.get("prompt", ""), "tests": t["tests"]}
        sc = {}
        for h, code in impls.items():
            payload = json.dumps({"task": pt, "answer": code}, ensure_ascii=False)
            p = subprocess.run([sys.executable, str(SCORER)], input=payload,
                               capture_output=True, text=True, timeout=120)
            if p.stdout.strip():
                sc[h] = json.loads(p.stdout.strip().splitlines()[-1]).get("score")
        scores[tid] = sc
        vals = sorted(v for v in sc.values() if v is not None)
        print(f"  {tid:22s} {len(sc)} 个实现　分数 {vals}")
    tasks = sorted(scores)

    print("\n## §1 惯用法抽取")
    per_task_idioms: dict[str, dict[str, set]] = {}
    all_idioms: set[str] = set()
    for tid in tasks:
        d = {}
        for h, code in corpus[tid].items():
            s = idioms_of(code)
            d[h] = s
            all_idioms |= s
        per_task_idioms[tid] = d
    print(f"  惯用法总数 = {len(all_idioms)}")
    for i in sorted(all_idioms):
        where = [t for t in tasks if any(i in v for v in per_task_idioms[t].values())]
        print(f"    {i:34s} 出现在 {len(where)} 道题：{where}")

    # ---------- 逐 (题, 惯用法) 算抬分 ----------
    uplift: dict[str, dict[str, float]] = {}   # idiom -> task -> uplift
    empty_group = 0
    for tid in tasks:
        sc = scores[tid]
        for i in all_idioms:
            have = [sc[h] for h in sc if i in per_task_idioms[tid].get(h, set()) and sc[h] is not None]
            lack = [sc[h] for h in sc if i not in per_task_idioms[tid].get(h, set()) and sc[h] is not None]
            if not have or not lack:
                if have or lack:
                    empty_group += 1
                continue
            u = statistics.mean(have) - statistics.mean(lack)
            uplift.setdefault(i, {})[tid] = round(u, 4)
    print(f"\n  两组之一为空的（题, 惯用法）组合 = {empty_group}（不参与均值比较，单列）")

    up = {i: {t: u for t, u in d.items() if u > 0} for i, d in uplift.items()}
    down = {i: {t: u for t, u in d.items() if u < 0} for i, d in uplift.items()}
    single = {i: d for i, d in up.items() if len(d) == 1}
    multi = {i: d for i, d in up.items() if len(d) >= 2}

    print("\n## §2 S2 至少一条在单题上抬分")
    print(f"  有正抬分的惯用法 {len([i for i in up if up[i]])} 条；其中只在 1 道题上抬分的 {len(single)} 条")
    print("  ⇒ " + ("✅ 成立" if single or multi else "⛔ 方法失败（惯用法没有信息量）"))
    print("  逐条（抬分 > 0 的）：")
    for i in sorted(up):
        if not up[i]:
            continue
        print(f"    {i:34s} " + "、".join(f"{t.split('_')[0]}+{u:+.3f}" for t, u in up[i].items()))

    print("\n## §3 S1 跨题抬分（核心）")
    if not multi:
        print("  命中 **0** 条 ⇒ **S1 成立（押中）：没有一条惯用法能在 ≥2 道题上抬分。**")
        print("  ⇒ ⛔ **「累积惯用法 → 迁移新题」正式关闭**：累积只能加速同一道题。")
    else:
        print(f"  命中 **{len(multi)}** 条：")
        for i, d in sorted(multi.items()):
            print(f"    ⭐ {i} ⇒ " + "、".join(f"{t}+{u:+.3f}" for t, u in d.items()))
            for t in d:
                sc = scores[t]
                have = [sc[h] for h in sc if i in per_task_idioms[t].get(h, set()) and sc[h] is not None]
                lack = [sc[h] for h in sc if i not in per_task_idioms[t].get(h, set()) and sc[h] is not None]
                print(f"         {t}: 含 {len(have)} 个 {have} ｜ 不含 {len(lack)} 个 {lack}")

    print("\n## §4 S3 负迁移（一题抬、另一题掉）")
    neg = {i: (up.get(i, {}), down.get(i, {})) for i in all_idioms
           if up.get(i) and down.get(i)}
    if neg:
        print(f"  {len(neg)} 条惯用法同时存在「抬」与「掉」：")
        for i, (u, d) in sorted(neg.items()):
            print(f"    {i:34s} " + "、".join(f"{t.split('_')[0]}{v:+.3f}" for t, v in
                                              list(u.items()) + list(d.items())))
        print("  ⇒ **S3 成立（押中）：负迁移存在** ⇒ V110 的干预实验必须带否决机制。")
    else:
        print("  ⇒ 未发现负迁移（S3 破）")

    print("\n## §5 混杂检查（同一实现是否共享多条惯用法）")
    for tid in tasks:
        d = per_task_idioms[tid]
        cnt = collections.Counter()
        for h, s in d.items():
            cnt[len(s)] += 1
        print(f"  {tid:22s} 每个实现的惯用法条数分布 {dict(sorted(cnt.items()))}")
    print("  ⚠️ 若某题实现普遍共享 3~5 条惯用法，则「抬分」是**实现整体好坏**的代理，不是单条惯用法的功劳。")

    out = {"tasks": tasks, "scores": scores,
           "uplift": uplift, "multi": {k: v for k, v in multi.items()},
           "neg": {k: [v[0], v[1]] for k, v in neg.items()},
           "n_idioms": len(all_idioms), "empty_group": empty_group}
    (EV / "v6_s198" / "idioms.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
