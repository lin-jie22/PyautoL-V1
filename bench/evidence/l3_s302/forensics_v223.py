#!/usr/bin/env python3
"""V223 取证（**零 LLM 调用**）：把「附言有没有用」这件事拆成账本里已有的三个量。

动机：V219~V222 五轮都在比「总分 / 通过计数」，每臂只跑 1 次。
      本轮不问「哪个附言更好」，只问三个**离线可判**的问题：

  Q1 分数是不是**代码的函数**？  ⇒ 按 (task, code_sha) 分组看 vec 是否单值。
     若不单值，则「同分不同代码」存在，后面一切归因都无意义。
  Q2 代码支撑集有多大、重复率多高？ ⇒ 决定「1 次/臂」的检出能力。
  Q3 账本能不能区分那四个已知不同的配置？ ⇒ 看 caliber / caliber_sig。
     ⛔ 这是关键：若不能，则任何「按配置聚合」的分析（含系统自己读账本）
        都会把四个不同的自变量当成同一个格。

用法： python3 forensics_v223.py [--json out.json]
"""
from __future__ import annotations

import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
SUITE_SHA = "e322e014a9472f41d486163404ae11ef"   # suite_sub2，跨 sha 不可比（尺度纪律）
TASKS = ("t01_parse_duration", "t04_merge_ranges")


def load_runs() -> list[dict]:
    out = []
    with open(ROOT / "bench_out/runs.jsonl", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            d = json.loads(line)
            if "run_id" in d and "report" in d:
                d["_idx"] = i
                out.append(d)
    return out


def load_answers() -> dict[tuple[str, str, int], dict]:
    """(run_id, task_id, rep) -> answer 行。

    ⛔ 不能按 (run_id, task_id) 建字典：`--repeat 20` 的轮次同题有 20 条答案，
       覆盖写只会留下最后一条 ⇒ 拿错的 sha 去对 vec，会凭空造出「同代码不同分」。
       本脚本首版就是这么错的（702 观测里 430 条 score != pass/6），已改为三元键。
    """
    raw: dict[tuple[str, str, int], list[dict]] = collections.defaultdict(list)
    with open(ROOT / "bench_out/answers.jsonl", encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            if d.get("kind") != "answer" or "run_id" not in d:
                continue
            raw[(d["run_id"], d["task_id"], int(d.get("rep", 1)))].append(d)
    # ⛔ `--repeat N` 的轮次会把 N 条答案全写成 `rep=1` ⇒ 键本身不唯一。
    #    唯一可靠的做法：**只保留恰好 1 条答案的键**。多条的键里分不清哪条被判了分，
    #    硬按顺序配对会凭空造出「同代码不同分」（首版就是这么错的）。
    out, amb = {}, 0
    for k, v in raw.items():
        if len(v) == 1:
            out[k] = v[0]
        else:
            amb += 1
    print(f"# answers 键 {len(raw)} 个：唯一 {len(out)}，多答案（丢弃）{amb}")
    return out


def main() -> int:
    runs = load_runs()
    answers = load_answers()

    same_suite = [r for r in runs
                  if r["caliber"].get("suite_sha") == SUITE_SHA]
    print(f"# runs 总 {len(runs)} 行；同 suite_sha({SUITE_SHA[:8]}) 的 {len(same_suite)} 行")
    print(f"# answers run_id 桶 {len(answers)} 个")

    # 样本：同 suite，且两题都有 answer sha
    obs = []
    miss = 0
    for r in same_suite:
        for pt in (r.get("report") or {}).get("per_task", []):
            if "vec" not in pt or pt["id"] not in TASKS:
                continue
            a = answers.get((r["run_id"], pt["id"], int(pt.get("rep", 1))))
            if not a:
                miss += 1
                continue
            obs.append({
                "idx": r["_idx"], "run_id": r["run_id"], "task": pt["id"],
                "rep": int(pt.get("rep", 1)),
                "sha": a["sha256"], "chars": a["chars"],
                "vec": tuple(bool(x) for x in pt["vec"]),
                "pass": sum(1 for x in pt["vec"] if x),
                "score": pt["score"], "why": pt.get("why", ""),
            })
    print(f"# 可用观测 (run×task×rep) = {len(obs)}；per_task 无对应答案 {miss}")

    # ── Q1：分数是不是代码的单射函数 ──────────────────────────────
    print("\n## Q1 (task, code_sha) -> vec 是否单值")
    bad = 0
    for tid in TASKS:
        by_sha = collections.defaultdict(set)
        for o in obs:
            if o["task"] == tid:
                by_sha[o["sha"]].add(o["vec"])
        multi = {s: v for s, v in by_sha.items() if len(v) > 1}
        print(f"  {tid}: 观测 {sum(1 for o in obs if o['task']==tid)}"
              f"  distinct_sha {len(by_sha)}  多值 sha {len(multi)}")
        for s, v in multi.items():
            bad += 1
            print(f"    ⛔ {s[:12]} -> {len(v)} 种 vec: {sorted(v)}")
    print(f"  ⇒ 反例合计 {bad}")

    # score 与 pass 是否一致（口径自检）
    mis = [o for o in obs if abs(o["score"] - round(o["pass"] / 6.0, 4)) > 1e-9]
    print(f"  score == pass/6 的反例: {len(mis)}")

    # ── Q2：支撑集与重复率 ───────────────────────────────────────
    print("\n## Q2 支撑集（同 suite 内，代码变体 -> 出现次数 / 分数）")
    for tid in TASKS:
        cnt = collections.Counter(o["sha"] for o in obs if o["task"] == tid)
        pas = {}
        for o in obs:
            if o["task"] == tid:
                pas.setdefault(o["sha"], o["pass"])
        print(f"  {tid}: {len(cnt)} 种变体 / {sum(cnt.values())} 次观测")
        for sha, n in cnt.most_common():
            print(f"    {sha[:12]}  n={n:2d}  pass={pas[sha]}/6  chars={next(o['chars'] for o in obs if o['task']==tid and o['sha']==sha)}")
        dist = collections.Counter(pas[s] for s in cnt)
        print(f"    分数分布(按出现次数加权): "
              f"{ {k: sum(cnt[s] for s in cnt if pas[s]==k) for k in sorted(dist)} }")

    # ── Q3：账本能否区分四个已知配置 ──────────────────────────────
    print("\n## Q3 四个已知不同配置的账本签名")
    LABEL = {408: "p1 shape/无附言", 409: "p2 shape/有附言",
             410: "p3 raw/with-others", 411: "p4 raw/self-only"}
    sigs = {}
    for idx, lab in LABEL.items():
        r = next((x for x in runs if x["_idx"] == idx), None)
        if not r:
            print(f"  idx{idx} 缺失")
            continue
        cal = json.dumps(r["caliber"], sort_keys=True, ensure_ascii=False)
        cal_sha = hashlib.sha256(cal.encode()).hexdigest()[:12]
        sigs[idx] = cal_sha
        t01 = answers.get((r["run_id"], "t01_parse_duration", 1), {})
        print(f"  idx{idx} {lab:22s} caliber_sig={r['caliber_sig']} "
              f"caliber_sha={cal_sha} t01_sha={t01.get('sha256','?')[:12]} "
              f"t01_chars={t01.get('chars','?')}")
    uniq = set(sigs.values())
    print(f"  ⇒ 不同配置 {len(sigs)} 个，账本签名 {len(uniq)} 种"
          f"  {'⛔ 自变量不在账本' if len(uniq) == 1 else '✅ 可区分'}")

    # 四个配置的 t01 变体是否互不相同
    t01_shas = []
    for idx in LABEL:
        r = next((x for x in runs if x["_idx"] == idx), None)
        if r:
            a = answers.get((r["run_id"], "t01_parse_duration", 1))
            if a:
                t01_shas.append(a["sha256"])
    print(f"  四轮 t01 变体 distinct = {len(set(t01_shas))} / {len(t01_shas)}")

    # ── Q4：1 次/臂 的检出能力（用观测到的分数跨度算）──────────────
    print("\n## Q4 单次采样的检出能力")
    for tid in TASKS:
        ps = [o["pass"] for o in obs if o["task"] == tid]
        cnt = collections.Counter(o["sha"] for o in obs if o["task"] == tid)
        vals = collections.Counter(ps)
        print(f"  {tid}: pass 取值 {dict(sorted(vals.items()))}  极差 {max(ps)-min(ps)}")
    print("  说明：若每臂只抽 1 次，读数落在上面这个离散分布上；")
    print("        两臂差 1 格 = 两个相邻取值，概率由分布本身决定，与附言无关（除非分布整体位移）。")

    if "--json" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--json") + 1])
        out.write_text(json.dumps(obs, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n# 明细写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
