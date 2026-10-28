#!/usr/bin/env python3
"""V2 量具（第二十件）：**失败分族 + 规范修复**。

要量的那句话（§72.5 / §74.2）：

    载荷的价值 = 题集里有多少失败能被一个名字说清

本文件把它变成一个可复现的分类：对每份**已冻结**的交付物，
① 用台账同一把尺子（`bench/scorers/code_exec.py` 子进程）复算分；
② 用**与内核载荷同源**的 `mw.verify.undefined_names` 问「缺哪个名字」；
③ **规范修复**：把每个缺的、且可 import 的名字 `import` 到文件顶部，再复算；
④ 按预注册 §2.3 分族，算每把尺子的 `share`。

⛔ 三个刻意的设计决定
    1. **零模型**：不发一次请求。分类必须能在任何机器上逐字复现。
    2. **打分器走子进程、用同一个文件**：不是"我重写一遍判分"，是"让台账那把尺子再量一次"。
       `code_exec.py` 的 `run_one` 每条断言一个隔离子进程 + 独立临时 cwd，
       所以复算是确定性的。
    3. **修复是规范化的、不是搜索**：只在文件顶部插入 `import <名字>`，
       不改任何其它字符。这样「说清」有一个可执行的定义，而不是我的判断。

⛔ 它测的是**上界**，不是收益：修复只补绑定、不顺手改坏（§74.2 的 `4beacfc9` 就改坏了）。
   所以 `share` = 「这个机会面至多多大」，不是「报名字能拿到多少格」。

用法
    python3 bench/evidence/v2_s105/probe_failfam.py                 # 正式读数
    python3 bench/evidence/v2_s105/probe_failfam.py --selftest      # N1 仪器反证
    python3 bench/evidence/v2_s105/probe_failfam.py --emit out.json # 落盘分类表（用于 N2）
    python3 bench/evidence/v2_s105/probe_failfam.py --table a.json --diff b.json  # N2 比对
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCORER = ROOT / "bench/scorers/code_exec.py"
SPECGEN = ROOT / "bench/specgen"
EVID = ROOT / "bench/evidence"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench/scorers"))

import mw.verify as V  # noqa: E402  与内核载荷同源（刻意）
import code_exec as CE  # noqa: E402  只借用它的 extract_code

#: 分族的判定顺序（互斥，先到先得）——与 PREREG §2.3 一一对应
FAMILIES = ("NO_CODE", "PASS", "UNDECIDABLE", "NO_NAME", "NAME_OTHER",
            "NAME_NULL", "NAME_PARTIAL", "NAME_FIXED")

#: 没有 `meta.suite` 的文件，只许在这里显式认领（PREREG §2.1）
SUITE_OVERRIDE = {
    "select_s85/v1_direct_answers.jsonl": ("tasks_v1", "PROVENANCE.md §一"),
    "select_s85/v1_kernel_answers.jsonl": ("tasks_v1", "PROVENANCE.md §一"),
}


# ===========================================================================
# 0. 尺子与题集（只读）
# ===========================================================================
def load_suites() -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for p in sorted(SPECGEN.glob("tasks_v*.jsonl")):
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        out[p.stem] = {r["id"]: r for r in rows}
    return out


def score(task: dict, code: str) -> float:
    """让**台账那把尺子**再量一次（子进程 + 同一个打分器文件）。"""
    payload = json.dumps({"task": task, "answer": code}, ensure_ascii=False)
    p = subprocess.run([sys.executable, str(SCORER)], input=payload,
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"打分器失败 rc={p.returncode}: {(p.stderr or '')[:200]}")
    lines = [l for l in (p.stdout or "").strip().splitlines() if l.strip()]
    return round(float(json.loads(lines[-1])["score"]), 4)


# ===========================================================================
# 1. 规范修复
# ===========================================================================
def importable(name: str) -> bool:
    if not name.isidentifier():
        return False
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def repair(code: str, names: tuple[tuple[str, str], ...]) -> str | None:
    """把缺的**模块名** import 到文件顶部。`None` = 补不出来。"""
    mods = [n for _scope, n in names]
    if not mods or not all(importable(n) for n in mods):
        return None
    head = "\n".join(f"import {n}" for n in mods)
    return head + "\n" + code


# ===========================================================================
# 2. 分族（一份交付物的全部事实）
# ===========================================================================
def classify(task: dict, answer: str) -> dict:
    code = CE.extract_code(answer)
    rec: dict = {"code_sha": hashlib.sha256(code.encode()).hexdigest()[:12]}
    if not code.strip():
        return {**rec, "family": "NO_CODE", "base": 0.0, "rep": 0.0, "names": []}
    base = score(task, code)
    rec["base"] = base
    if base >= 1.0:
        return {**rec, "family": "PASS", "rep": base, "names": []}
    names = V.undefined_names(code)
    if names is None:
        return {**rec, "family": "UNDECIDABLE", "rep": base, "names": []}
    if not names:
        return {**rec, "family": "NO_NAME", "rep": base, "names": []}
    rec["names"] = [n for _s, n in names]
    fixed = repair(code, names)
    if fixed is None:
        return {**rec, "family": "NAME_OTHER", "rep": base}
    rec["repaired_sha"] = hashlib.sha256(fixed.encode()).hexdigest()[:12]
    rep = score(task, fixed)
    rec["rep"] = rep
    fam = ("NAME_FIXED" if rep >= 1.0 else
           "NAME_PARTIAL" if rep > base else "NAME_NULL")
    return {**rec, "family": fam, "fixed_code": fixed}


# ===========================================================================
# 3. 收集答案台账
# ===========================================================================
def collect() -> list[dict]:
    """所有含 answer 行的台账 ⇒ [{file, suite, suite_src, task, sha, answer}]"""
    out: list[dict] = []
    for p in sorted(EVID.rglob("*.jsonl")):
        if p.name.endswith("runs.jsonl") or "proxy" in p.parts or "score_ledger" in p.name:
            continue
        rel = str(p.relative_to(EVID))
        try:
            rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        except (OSError, json.JSONDecodeError):
            continue
        ans = [r for r in rows if r.get("kind") == "answer"]
        if not ans:
            continue
        hdr = next((r for r in rows if r.get("kind") == "header"), {})
        suite = (hdr.get("meta") or {}).get("suite")
        src = "header.meta.suite"
        if not suite and rel in SUITE_OVERRIDE:
            suite, src = SUITE_OVERRIDE[rel][0], SUITE_OVERRIDE[rel][1]
        if not suite:
            continue
        for i, r in enumerate(ans):
            out.append({"file": rel, "suite": suite, "suite_src": src,
                        "task": r.get("task_id"), "sha": r.get("sha256"),
                        "answer": r.get("answer") or "", "order": i})
    return out


# ===========================================================================
# 4. N3 接线：末条答案复算分 == 台账 per_task 最大 rep 的分
# ===========================================================================
def wiring(rows: list[dict], suites: dict) -> dict:
    groups: dict[str, dict[str, list[dict]]] = {}
    for r in rows:
        groups.setdefault(r["file"], {}).setdefault(r["task"], []).append(r)
    checked = matched = 0
    misses: list[str] = []
    for rel, bytask in groups.items():
        rp = EVID / rel.replace("answers.jsonl", "runs.jsonl")
        if not rp.exists():
            continue
        try:
            run = [json.loads(l) for l in rp.read_text(encoding="utf-8").splitlines() if l.strip()][-1]
        except (OSError, json.JSONDecodeError):
            continue
        suite = next(iter(bytask.values()))[0]["suite"]
        per: dict[str, dict] = {}
        for t in (run.get("report") or {}).get("per_task", []):
            per.setdefault(t["id"], {})[t["rep"]] = round(float(t["score"]), 4)
        for tid, lst in bytask.items():
            if tid not in per or tid not in suites.get(suite, {}):
                continue
            last = max(lst, key=lambda r: r["order"])
            got = score(suites[suite][tid], CE.extract_code(last["answer"]))
            want = per[tid][max(per[tid])]
            checked += 1
            if got == want:
                matched += 1
            else:
                misses.append(f"{rel}:{tid} 复算={got} 台账={want}")
    return {"checked": checked, "matched": matched, "misses": misses}


# ===========================================================================
# 5. N1 仪器反证
# ===========================================================================
def mutate(code: str, old: str, new: str, tag: str) -> str:
    """做一处已知改动，**没改到就炸**。

    ⛔ 为什么必须炸：`str.replace` 找不到模式时静默返回原串，
    于是「构造没生效」与「构造生效但结论相反」在输出上**同形**
    （§70.4 ③ 的老病，§73.5.2 犯过第四次）。反证组里更不许出现这种情况。
    """
    if old not in code:
        raise AssertionError(f"{tag}: 构造未生效 —— 模式不存在: {old!r}")
    out = code.replace(old, new, 1)
    if out == code:
        raise AssertionError(f"{tag}: 替换后代码未变")
    return out


def selftest() -> bool:
    suites = load_suites()
    v4 = suites["tasks_v4"]
    task = v4["at04"]
    rows = [json.loads(l) for l in (EVID / "v1b_s104/a1_v4/answers.jsonl")
            .read_text(encoding="utf-8").splitlines() if l.strip()]
    good = next(CE.extract_code(r["answer"]) for r in rows
                if r.get("kind") == "answer" and r["task_id"] == "at04"
                and r["sha256"].startswith("26fe8d59"))
    print("=== N1 仪器反证（4 例，全部都按设计落位才算过）===")

    # S1：删掉 import os ⇒ 必须 NAME_FIXED，且修复后回到 1.0
    s1 = mutate(good, "\n    import os\n", "\n", "S1")
    if "import os" in s1:
        raise AssertionError("S1: import os 没删干净")
    r1 = classify(task, s1)
    ok1 = r1["family"] == "NAME_FIXED" and r1["rep"] >= 1.0
    print(f"  S1 删 import os        ⇒ {r1['family']:<13} base={r1['base']:.4f} rep={r1['rep']:.4f} "
          f"{'PASS' if ok1 else 'FAIL'}")

    # S2：语义改动（丢掉行尾换行）⇒ 必须不是名字类
    s2 = mutate(good, "file.write(clean_row + '\\n')", "file.write(clean_row)", "S2")
    r2 = classify(task, s2)
    ok2 = r2["family"] == "NO_NAME"
    print(f"  S2 去掉尾换行          ⇒ {r2['family']:<13} base={r2['base']:.4f} "
          f"{'PASS' if ok2 else 'FAIL'}")

    # S3：加一个**不可 import** 的未绑定名字 ⇒ 必须 NAME_OTHER
    s3 = good + "\n\n_TOTAL = undefined_helper(1)\n"
    r3 = classify(task, s3)
    ok3 = r3["family"] == "NAME_OTHER" and "undefined_helper" in r3["names"]
    print(f"  S3 未绑定的普通名字    ⇒ {r3['family']:<13} names={r3['names']} "
          f"{'PASS' if ok3 else 'FAIL'}")

    # S4：已知满分的**冻结交付物** ⇒ PASS（不进分母）
    # ⛔ 刻意**不读 `task["oracle"]`**：读了答案键的探针按本项目规矩只能挂 7i/7j。
    #    这里用「台账里已经 16/16 的那份交付物」当已知正例，够用且不碰答案键。
    r4 = classify(task, good)
    ok4 = r4["family"] == "PASS"
    print(f"  S4 已知满分交付物      ⇒ {r4['family']:<13} base={r4['base']:.4f} "
          f"{'PASS' if ok4 else 'FAIL'}")

    allok = ok1 and ok2 and ok3 and ok4
    print(f"  ⇒ N1 {'全绿' if allok else '⛔ 有红 ⇒ 整轮读数作废'}")
    return allok


# ===========================================================================
# 6. 主读数
# ===========================================================================
def build_table(rows: list[dict], suites: dict) -> tuple[list[dict], dict]:
    seen: dict[tuple, dict] = {}
    dup_rows = 0
    for r in rows:
        tid = r["task"]
        if tid not in suites.get(r["suite"], {}):
            continue
        key = (r["suite"], tid, r["sha"])
        if key in seen:
            seen[key]["n_rows"] += 1
            dup_rows += 1
            continue
        rec = classify(suites[r["suite"]][tid], r["answer"])
        seen[key] = {"suite": r["suite"], "task": tid, "sha": r["sha"],
                     "file": r["file"], "suite_src": r["suite_src"],
                     "n_rows": 1, **rec}
    return list(seen.values()), {"rows": len(rows), "dup_rows": dup_rows}


def share_of(recs: list[dict]) -> dict:
    fails = [r for r in recs if r["family"] != "PASS"]
    cnt = {f: sum(1 for r in fails if r["family"] == f) for f in FAMILIES}
    den = len(fails)
    return {"den": den, "cnt": {k: v for k, v in cnt.items() if v},
            "share": (cnt["NAME_FIXED"] / den) if den else 0.0,
            "share_any": ((cnt["NAME_FIXED"] + cnt["NAME_PARTIAL"]) / den) if den else 0.0,
            "tasks_hit": len({r["task"] for r in fails if r["family"] == "NAME_FIXED"}),
            "tasks_all": len({r["task"] for r in recs})}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 区间。⛔ 不是「显著性检验」—— 只用来判「这个差可分辨吗」。"""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (max(0.0, (c - m) / d), min(1.0, (c + m) / d))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--emit", default="")
    ap.add_argument("--table", default="")
    ap.add_argument("--diff", default="")
    args = ap.parse_args()

    if args.table and args.diff:
        a = json.loads(Path(args.table).read_text(encoding="utf-8"))
        b = json.loads(Path(args.diff).read_text(encoding="utf-8"))
        ha = hashlib.sha256(json.dumps(a, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        hb = hashlib.sha256(json.dumps(b, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        same = ha == hb
        print(f"N2 可复现：{ha[:16]} vs {hb[:16]} ⇒ {'✅ 逐字相同' if same else '⛔ 不同'}")
        if not same:
            for x in a:
                y = next((z for z in b if (z["suite"], z["task"], z["sha"]) ==
                          (x["suite"], x["task"], x["sha"])), None)
                if y != x:
                    print("  差异:", json.dumps({k: (x.get(k), (y or {}).get(k)) for k in x
                                                 if x.get(k) != (y or {}).get(k)},
                                                ensure_ascii=False)[:300])
        return 0 if same else 1

    if args.selftest:
        return 0 if selftest() else 1

    suites = load_suites()
    rows = collect()
    table, meta = build_table(rows, suites)

    print("=== V2 · 失败分族（单位 = 唯一交付物 (suite, task, sha)）===")
    print(f"台账行 {meta['rows']}，去重后 {len(table)} 份唯一交付物"
          f"（重复行 {meta['dup_rows']}）；题集 {sorted(suites)}")

    print("\n--- 每把尺子 ---")
    by_suite: dict[str, list[dict]] = {}
    for r in table:
        by_suite.setdefault(r["suite"], []).append(r)
    S = {s: share_of(v) for s, v in by_suite.items()}
    for s in sorted(by_suite):
        d = S[s]
        lo, hi = wilson(d["cnt"].get("NAME_FIXED", 0), d["den"])
        print(f"  {s:9s} 交付 {len(by_suite[s]):3d}  失败 {d['den']:3d}  "
              f"NAME_FIXED {d['cnt'].get('NAME_FIXED', 0):2d} ⇒ share={d['share']:.3f} "
              f"[{lo:.3f},{hi:.3f}]  share_any={d['share_any']:.3f}  "
              f"命中题 {d['tasks_hit']}/{d['tasks_all']}")
        print(f"            族分布 {d['cnt']}")

    print("\n--- 每个台账文件（同一份代码在不同臂只算一次）---")
    by_file: dict[str, list[dict]] = {}
    for r in table:
        by_file.setdefault(r["file"], []).append(r)
    for f in sorted(by_file):
        d = share_of(by_file[f])
        s = by_file[f][0]["suite"]
        print(f"  {f[:46]:46s} {s:9s} 失败 {d['den']:2d}  FIXED {d['cnt'].get('NAME_FIXED', 0):2d}  "
              f"share={d['share']:.3f}  族={d['cnt']}")

    print("\n--- 逐题（只列出现 NAME_FIXED 的题）---")
    for r in sorted(table, key=lambda x: (x["suite"], x["task"], x["sha"] or "")):
        if r["family"] != "NAME_FIXED":
            continue
        print(f"  {r['suite']:9s} {r['task']:5s} {(r['sha'] or '')[:8]} "
              f"{r['file'][:34]:34s} base={r['base']:.3f} → 1.000  名字={r['names']}")

    print("\n--- 逐题（v4 全表，用来看 at04 的形状）---")
    for r in sorted([x for x in table if x["suite"] == "tasks_v4"],
                    key=lambda x: (x["task"], x["sha"] or "")):
        print(f"  {r['task']:5s} {(r['sha'] or '')[:8]} {r['family']:<13} "
              f"base={r['base']:.3f} rep={r['rep']:.3f} names={r['names']}")

    print("\n=== 判负检查（PREREG 第四节）===")
    w = wiring(rows, suites)
    rate = (w["matched"] / w["checked"]) if w["checked"] else 0.0
    print(f"  N3 接线：{w['matched']}/{w['checked']} = {rate:.1%}")
    for m in w["misses"][:6]:
        print(f"     ⛔ {m}")

    tot_f = sum(d["den"] for d in S.values())
    tot_fixed = sum(d["cnt"].get("NAME_FIXED", 0) for d in S.values())
    tot_und = sum(d["cnt"].get("UNDECIDABLE", 0) for d in S.values())
    overall = (tot_fixed / tot_f) if tot_f else 0.0
    s1 = S.get("tasks_v1", share_of([]))
    s4 = S.get("tasks_v4", share_of([]))
    print(f"  N4 UNDECIDABLE 占比：{tot_und}/{tot_f} = {tot_und / tot_f:.1%}（阈值 ≤30%）")
    for s in sorted(S):
        flag = "✅" if S[s]["den"] >= 5 else "⛔ 样本不足"
        print(f"  N5 {s:9s} 失败交付 {S[s]['den']:3d} {flag}")

    print("\n=== 判读（PREREG 第三节）===")
    lo1, hi1 = wilson(s1["cnt"].get("NAME_FIXED", 0), s1["den"])
    lo4, hi4 = wilson(s4["cnt"].get("NAME_FIXED", 0), s4["den"])
    overlap = not (lo4 > hi1)
    print(f"  P1 share(v4)={s4['share']:.3f} [{lo4:.3f},{hi4:.3f}] vs "
          f"share(v1)={s1['share']:.3f} [{lo1:.3f},{hi1:.3f}] ⇒ "
          + ("✅ 方向未被否证" if s4["share"] > s1["share"]
             else "⛔ 被否证 ⇒ 撤回「价值 = 占比」形式化"))
    if s4["share"] > s1["share"] and overlap:
        print("     ⚠️ 但两个 Wilson 区间**重叠** ⇒ 这个方向在本分母下**不可分辨**，"
              "只能当点估计，⛔ 不许写成「v4 的占比更高」这一结论")
    p2 = [r for r in table if r["suite"] == "tasks_v4" and r["task"] == "at04"
          and (r["sha"] or "").startswith("1b5f6119")]
    if p2:
        ok = p2[0]["family"] == "NAME_FIXED"
        print(f"  P2 at04/1b5f6119 ⇒ {p2[0]['family']}（base {p2[0]['base']:.3f} → {p2[0]['rep']:.3f}）"
              f" {'✅' if ok else '⛔ §74.2 的改动半径解释被否证'}")
    else:
        print("  P2 ⛔ 表里没有 at04/1b5f6119 ⇒ 无法判，记 UNKNOWN")
    print(f"  P3 合计 share={overall:.3f}（{tot_fixed}/{tot_f}）⇒ "
          + ("✅ < 0.20" if overall < 0.20 else "⛔ ≥ 0.20 ⇒ 与 §67 的 7.8% 必须对齐解释"))
    for s in ("tasks_v2", "tasks_v3"):
        if s in S:
            v = S[s]["share"]
            print(f"  P4 {s} share={v:.3f} ⇒ {'✅ 在 0.05~0.40' if 0.05 <= v <= 0.40 else '⚠️ 区间外（登记为预期错）'}")

    # N2：分类表落盘（供两次跑比对）
    canon = sorted([{k: r[k] for k in ("suite", "task", "sha", "family", "base", "rep", "names")}
                    for r in table], key=lambda x: (x["suite"], x["task"], x["sha"] or ""))
    h = hashlib.sha256(json.dumps(canon, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    print(f"\n  分类表 sha256[:16] = {h[:16]}（N2 用它比对两次跑是否逐字相同）")
    if args.emit:
        Path(args.emit).write_text(json.dumps(canon, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  已落盘 {args.emit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
