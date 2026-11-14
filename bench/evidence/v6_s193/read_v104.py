#!/usr/bin/env python3
"""V104 读数：**性质判据**（对题面不变的第二通道）—— 拿现成档案里的 4 个实现去量。

零模型、零网络：全部输入来自 `v6_s191`（P0~P2）与 `v6_s192`（P3~P4）的 `answers/`。

§1 实现清单与 tests 分　§2 逐条性质　§3 与 tests 的一致性
§4 核心：性质判据能否分开 0.4 与 1.0　§5 不变性冒烟　§6 它对「理由」的贡献
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

TASK = "g7d3_fmt_amount"
HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
JUDGE = HERE / "judge_prop.py"
CELLS = [("P0", "A0", EV / "v6_s191"), ("P0", "A1", EV / "v6_s191"),
         ("P1", "A0", EV / "v6_s191"), ("P1", "A1", EV / "v6_s191"),
         ("P2", "A0", EV / "v6_s191"), ("P2", "A1", EV / "v6_s191"),
         ("P3", "A0", EV / "v6_s192"), ("P3", "A1", EV / "v6_s192"),
         ("P4", "A0", EV / "v6_s192"), ("P4", "A1", EV / "v6_s192")]


def answer_of(d: pathlib.Path, tag: str) -> str:
    p = d / "answers" / tag / "answers.jsonl"
    for ln in p.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r.get("kind") == "answer" and r.get("task_id") == TASK:
            return r.get("answer") or ""
    return ""


def score_of(d: pathlib.Path, tag: str) -> float | None:
    sp = d / "scores.json"
    if not sp.exists():
        return None
    return (json.loads(sp.read_text(encoding="utf-8")).get(tag) or {}).get(TASK)


def run_judge(code: str, coarse: bool = False) -> dict:
    payload = json.dumps({"task": {"id": TASK}, "answer": code}, ensure_ascii=False)
    cmd = [sys.executable, str(JUDGE)] + (["--coarse"] if coarse else [])
    p = subprocess.run(cmd, input=payload, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        return {"score": None, "why": f"judge 退出码 {p.returncode}: {p.stderr[:200]}", "detail": []}
    return json.loads(p.stdout.strip().splitlines()[-1])


def main() -> None:
    impls: dict[str, dict] = {}
    for p, arm, d in CELLS:
        tag = f"{arm}__{p}"
        code = answer_of(d, tag)
        h = hashlib.sha256(code.encode()).hexdigest()[:8]
        rec = impls.setdefault(h, {"code": code, "cells": [], "tests": set()})
        rec["cells"].append(f"{arm}@{p}")
        sc = score_of(d, tag)
        if sc is not None:
            rec["tests"].add(sc)

    print("## §1 四个实现（来自 10 格）与其 tests 分")
    for h, rec in sorted(impls.items(), key=lambda kv: -max(kv[1]["tests"] or {0})):
        print(f"  {h}  tests={sorted(rec['tests'])}  出现 {len(rec['cells'])} 格：{' '.join(rec['cells'])}")
        print("       " + rec["code"].strip().replace("\n", "\n       ")[:300])

    print("\n## §2 性质判据（对题面不变的通道）")
    res = {}
    for h, rec in impls.items():
        r = run_judge(rec["code"])
        res[h] = r
        print(f"  {h}  tests={sorted(rec['tests'])} ⇒ **性质分 {r['score']}**（{r['why']}）")
        for dd in r.get("detail", []):
            if "prop" in dd:
                print(f"       {'✅' if dd.get('ok') else '❌'} {dd['prop']:16s} {dd.get('desc','')}"
                      + ("" if dd.get("ok", True) else f"　失败 {dd.get('n_fail')}/{dd.get('n')}"))

    print("\n## §3 与 tests 的一致性（逐实现）")
    print("  ⭐ 同时报**两种口径**（微平均 / 粗平均），防止「调口径调出结论」")
    print(f"  {'实现':10s} {'tests':>8s} {'性质(微)':>10s} {'性质(粗)':>10s}")
    coarse = {}
    for h in res:
        c = run_judge(impls[h]["code"], coarse=True)
        coarse[h] = c
        print(f"  {h:10s} {str(sorted(impls[h]['tests'])):>8s} "
              f"{res[h]['score']:>10} {c['score']:>10}")

    print("\n## §4 核心：性质判据能不能分开 0.4 与 1.0")
    lo = [h for h in res if impls[h]["tests"] == {0.4}]
    hi = [h for h in res if impls[h]["tests"] == {1.0}]
    for h in lo:
        print(f"  tests=0.4 的 {h}：性质分 {res[h]['score']}　"
              f"破 {'、'.join(d['prop'] for d in res[h].get('detail', []) if d.get('ok') is False) or '（无）'}")
    for h in hi:
        print(f"  tests=1.0 的 {h}：性质分 {res[h]['score']}　"
              f"破 {'、'.join(d['prop'] for d in res[h].get('detail', []) if d.get('ok') is False) or '（无）'}")
    if lo and hi:
        slo = min(res[h]["score"] for h in lo)
        shi = max(res[h]["score"] for h in hi)
        print(f"  ⇒ 0.4 组的性质分上限 {slo} ｜ 1.0 组的下限 {shi} ⇒ "
              + ("**分开了**（性质判据是有效判据）" if slo < shi else "⛔ 没分开（性质判据在两盆上同值）"))
    print("  ⭐ 次序检查（tests 说 0.4 < 0.6，性质判据怎么说）：")
    a04 = [h for h in res if impls[h]["tests"] == {0.4}]
    a06 = [h for h in res if impls[h]["tests"] == {0.6}]
    for h in a04 + a06:
        print(f"     {h}  tests={sorted(impls[h]['tests'])}  性质(微)={res[h]['score']}  性质(粗)={coarse[h]['score']}")
    if a04 and a06:
        m04 = max(res[h]["score"] for h in a04)
        m06 = min(res[h]["score"] for h in a06)
        print("     ⇒ " + ("**次序一致**（0.4 组低于 0.6 组）" if m04 < m06
                          else "⛔ **次序相反**：性质判据认为 0.4 那组不比 0.6 差"))

    print("\n## §5 不变性冒烟")
    print("  ⚠️ 性质判据的输入只有答案代码 ⇒ **对题面字节天然不变是构造性的**，不是发现。")
    print("  这里只冒烟一点：同一实现在不同题面下被判出的性质分必须**相同**（否则判据有环境随机性）。")
    bad = [(h, r["score"]) for h, r in res.items() if h in impls]
    print(f"  实际：{len(bad)} 个实现各判一次，每个实现只有一个性质分 ⇒ 同一实现不可能得到两个分（构造性）。")

    print("\n## §6 它对「理由」的贡献（这是 tests 没有的东西）")
    for h, r in res.items():
        fails = [d for d in r.get("detail", []) if d.get("ok") is False]
        print(f"  {h}  tests={sorted(impls[h]['tests'])}  ⇒ 性质判据的理由："
              + ("、".join(f"{d['prop']}({d.get('n_fail')}/{d.get('n')})" for d in fails) if fails else "全部成立"))


if __name__ == "__main__":
    main()
