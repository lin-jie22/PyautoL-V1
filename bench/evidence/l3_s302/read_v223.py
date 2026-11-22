#!/usr/bin/env python3
"""V223 读数：三次同配置实跑，判 P0/P1/P2/P3（判据见 PREREG_v223.md，跑前写死）。

⛔ 只读：`probe_d_*.jsonl`（外部能力收到的 stdin 原文）、`bench_out/runs.jsonl` 新增尾部、
   `bench_out/answers.jsonl` 新增尾部。不改任何文件、不调 LLM。
"""
from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
D = ROOT / "bench/evidence/l3_s302"
RUNS = 3
T01 = "t01_parse_duration"
T04 = "t04_merge_ranges"


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def baseline() -> tuple[int, int]:
    # ⛔ 不能用 split() 后按 "=" 取第二段：`wc -l` 的输出前面带空格，
    #    `runs=    412` 会被切成 `runs=` 与 `412` 两个 token（本轮首跑就栽在这）。
    txt = (D / "start_v223.txt").read_text(encoding="utf-8")
    import re
    m = re.search(r"runs=\s*(\d+)", txt)
    n = re.search(r"answers=\s*(\d+)", txt)
    if not m or not n:
        raise SystemExit(f"start_v223.txt 解析失败: {txt!r}")
    return int(m.group(1)), int(n.group(1))


def read_probes() -> dict[int, dict[str, dict]]:
    """run -> {'t01'|'t04': {'sha':..., 'chars':..., 'text':...}}"""
    out: dict[int, dict[str, dict]] = {}
    for i in range(1, RUNS + 1):
        p = D / f"probe_d_{i}.jsonl"
        rec: dict[str, dict] = {}
        if not p.exists():
            out[i] = rec
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            t = d.get("task") or ""
            key = "t01" if "parse_duration" in t else ("t04" if "merge_ranges" in t else "")
            if not key:
                continue
            # 同一 run 内同题多次调用时保留全部（用列表下标区分）
            n = 0
            while f"{key}#{n}" in rec:
                n += 1
            rec[f"{key}#{n}"] = {"sha": sha(t), "chars": len(t), "text": t,
                                 "has_review": d.get("has_review")}
        out[i] = rec
    return out


def read_runs() -> tuple[list[dict], list[dict]]:
    r0, a0 = baseline()
    runs = [json.loads(l) for l in (ROOT / "bench_out/runs.jsonl").read_text(encoding="utf-8").splitlines()[r0:] if l.strip()]
    ans = [json.loads(l) for l in (ROOT / "bench_out/answers.jsonl").read_text(encoding="utf-8").splitlines()[a0:] if l.strip()]
    return runs, ans


def main() -> int:
    probes = read_probes()
    runs, ans = read_runs()
    print(f"# 新增 runs {len(runs)} 行；新增 answers {len(ans)} 行")
    print(f"# probe 记录数 {[len(probes[i]) for i in range(1, RUNS+1)]}")

    # ── P0：输入是否逐字节相同 ────────────────────────────────────
    print("\n## P0 外部能力收到的 stdin（t01）")
    shas = []
    for i in range(1, RUNS + 1):
        rec = probes.get(i, {})
        e = rec.get("t01#0")
        if not e:
            print(f"  run{i}: 无 t01 调用")
            shas.append(None)
            continue
        shas.append(e["sha"])
        print(f"  run{i}: chars={e['chars']:4d} sha={e['sha']} review={e['has_review']}")
    p0 = len(set(x for x in shas if x)) == 1 and all(shas)
    print(f"  ⇒ P0 {'✅ 全同' if p0 else '❌ 不全同/有缺失'}  {shas}")

    print("\n## P0b 同一段原文（run1 收到的 t01 附言尾部 300 字）")
    e1 = probes.get(1, {}).get("t01#0")
    if e1:
        print("  " + e1["text"][-300:].replace("\n", "\n  "))

    # ── 代码 sha 与 vec ─────────────────────────────────────────
    by_run: dict[str, dict[str, dict]] = collections.defaultdict(dict)
    for a in ans:
        if a.get("kind") != "answer":
            continue
        by_run[a["run_id"]][a["task_id"]] = a
    print("\n## P1/P2/P3 逐轮代码与分数")
    t01s, t04s, t01vec = [], [], []
    for r in runs:
        rid = r["run_id"]
        ppt = {pt["id"]: pt for pt in (r.get("report") or {}).get("per_task", []) if "vec" in pt}
        line = [f"  run {rid}"]
        for tid, bucket in ((T01, t01s), (T04, t04s)):
            a = by_run.get(rid, {}).get(tid)
            pt = ppt.get(tid)
            s = a["sha256"][:12] if a else "?"
            bucket.append(s)
            v = "".join("1" if x else "0" for x in (pt["vec"] if pt else []))
            line.append(f"{tid[:3]} sha={s} chars={a['chars'] if a else '?':>4} vec={v}")
            if tid == T01 and pt:
                t01vec.append(tuple(pt["vec"]))
        print("  ".join(line))

    def verdict(name: str, xs: list[str]) -> tuple[str, str]:
        uniq = len(set(xs))
        if "?" in xs:
            return name, "UNKNOWN（有缺失）"
        tag = f"{RUNS - uniq + 1}/{RUNS} 同" if uniq > 1 else f"{RUNS}/{RUNS} 同"
        if uniq == 1:
            return name, f"✅ {tag}"
        return name, f"❌ {tag}（{uniq} 种）"

    print()
    for name, xs in (("P1 t01 代码", t01s), ("P2 t04 代码", t04s)):
        n, v = verdict(name, xs)
        print(f"  {n}: {v}  {xs}")
    print(f"  P3 t01 vec: {'✅ 全同' if len(set(t01vec)) == 1 else '❌'}  {len(set(t01vec))} 种")

    # ── 判决 ───────────────────────────────────────────────────
    print("\n## 判决")
    if not p0:
        print("  P0 ❌ ⇒ **UNKNOWN**：装置没固定住输入，本轮不算证据。")
    else:
        u = len(set(t01s))
        if u == 1 and len(set(t04s)) == 1:
            print("  P0 ✅ 且 3/3 ⇒ **确定**：1 格差异是真效应，L3 净 0 是真净 0。")
        elif u == 2:
            print("  P0 ✅ 且 2/3 ⇒ **部分确定**：1 格差异 = 效应 + 抽样，不可分。")
        else:
            print("  P0 ✅ 且 ≤1/3 ⇒ **抽样主导**：前五轮 ±1 结论不可识别。")
    print("\n  押注存档：本轮押「确定」（3/3），把握自报 0.5。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
