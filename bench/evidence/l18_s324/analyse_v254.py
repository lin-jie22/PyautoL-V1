#!/usr/bin/env python3
"""V254 分析：可修性上界 + P3 归因 + P5 定值复核。

只读 readings_v254.json；重跑仅限「可修格」的 B 臂一次（预注册 §2 P5）。
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path("/Users/linshaojie/V1")
sys.path.insert(0, str(ROOT))
EV = ROOT / "bench" / "evidence" / "l18_s324"


def main() -> int:
    d = json.loads((EV / "readings_v254.json").read_text(encoding="utf-8"))
    cells, rows = d["cells"], d["rows"]
    by = {(r["cell"], r["arm"]): r for r in rows}

    import json as _j
    an = _j.loads((ROOT / "bench/evidence/l17_s323/analyse_v253.json").read_text(encoding="utf-8"))
    print(f"{'cell':26}{'v253':>7}{'A':>7}{'B':>7}{'C':>7}  {'B-A':>7}  sha(A)==sha(B)")
    delta, same_sha, improved_any = [], 0, []
    for c in cells:
        a, b = by[(c, "A")], by[(c, "B")]
        cc = by.get((c, "C"))
        v253 = float((an["per_cell"][c]["A"] or {}).get("score") or 0.0)
        dlt = round(b["score"] - a["score"], 4)
        if b["score"] > a["score"]:
            delta.append(c)
        if a["sha256"] == b["sha256"]:
            same_sha += 1
        improved_any.append(dlt)
        print(f"{c:26}{v253:7.3f}{a['score']:7.3f}{b['score']:7.3f}"
              f"{(cc['score'] if cc else float('nan')):7.3f}  {dlt:+7.3f}  "
              f"{'YES' if a['sha256']==b['sha256'] else 'no'}")

    print()
    print(f"Δ (B>A) = {len(delta)}/{len(cells)}  {delta}")
    print(f"A 与 B **逐字节相同**的格 = {same_sha}/{len(cells)}")
    print(f"Σ(B−A) = {sum(improved_any):+.4f}   均值 = {sum(improved_any)/len(cells):+.4f}")
    # A 臂相对 V253 的漂移（「再写一版」本身的效应）
    import json as _j
    an = _j.loads((ROOT / "bench/evidence/l17_s323/analyse_v253.json").read_text(encoding="utf-8"))
    drift = []
    for c in cells:
        v = float((an["per_cell"][c]["A"] or {}).get("score") or 0.0)
        if abs(by[(c, "A")]["score"] - v) > 1e-9:
            drift.append((c, v, by[(c, "A")]["score"], by[(c, "B")]["score"]))
    print(f"「无信息重写」相对 V253 就变分的格 = {len(drift)}：")
    for c, v, a, b in drift:
        print(f"    {c:26} v253={v:.3f} → A={a:.3f} → B={b:.3f}")

    # P3：可修格必须「那条断言由 fail 转 pass」
    print()
    for c in delta:
        a, b = by[(c, "A")], by[(c, "B")]
        va, vb = a.get("vec") or [], b.get("vec") or []
        flips = [i for i, (x, y) in enumerate(zip(va, vb)) if x != y]
        print(f"P3 {c}: A vec={va} B vec={vb} 翻转位={flips}")
        print(f"    B 的 why: {b['why'][:160]}")
        cc = by.get((c, "C"))
        if cc:
            print(f"    C(安慰剂) vec={cc.get('vec')} score={cc['score']:.3f}")

    # P5：可修格 B 臂定值复核
    print()
    from mw.config import Settings
    from mw.llm import HTTPLLM, Message
    from run import DIRECT_SYSTEM
    from run_v254 import build_prompt
    tasks = {t["id"]: t for t in (json.loads(l) for l in
             pathlib.Path("/tmp/v253_suite/all39.jsonl").read_text(encoding="utf-8").splitlines() if l.strip())}
    answers = {}
    for line in (ROOT / "bench/evidence/l17_s323/answers_a.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("kind") == "answer":
                answers[r["task_id"]] = r["answer"]
    llm = HTTPLLM(Settings().llm)
    for c in delta:
        nxt = cells[(cells.index(c) + 1) % len(cells)]
        p = build_prompt("B", tasks[c], answers[c], d["fails"][c], d["fails"][nxt])
        import hashlib
        txt = llm.complete([Message("system", DIRECT_SYSTEM), Message("user", p)],
                           max_tokens=2048, temperature=0.0)
        sha = hashlib.sha256(txt.encode("utf-8")).hexdigest()[:12]
        print(f"P5 {c}: first={by[(c,'B')]['sha256']} rerun={sha} same={sha==by[(c,'B')]['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
