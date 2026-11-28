#!/usr/bin/env python3
"""V259b analysis: k=8 temperature-0.8 samples on the 4 never-rejected cells.

Reads /tmp/v259_s{1..8}/bench/{runs.jsonl,answers.jsonl} and produces
readings_v259b.json with P1..P4 from the prereg.
"""
import json
import re
from pathlib import Path

EV = Path(__file__).resolve().parent
CELLS = ["g7t2_parse_pairs", "s02_limiter", "m02_vfs", "m03_tiered"]
K = 8


def read_scores(k):
    out = {}
    p = Path(f"/tmp/v259_s{k}/bench/runs.jsonl")
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        d = json.loads(line)
        for t in (d.get("report") or {}).get("per_task") or []:
            cid, sc = t.get("id"), t.get("score")
            if cid and isinstance(sc, (int, float)):
                cur = out.get(cid)
                if cur is None or float(sc) > cur["score"]:
                    out[cid] = {"score": float(sc), "why": str(t.get("why") or ""),
                                "vec": t.get("vec")}
    return out


def read_answers(k):
    out = {}
    p = Path(f"/tmp/v259_s{k}/bench/answers.jsonl")
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        r = json.loads(line)
        tid = r.get("task_id")
        if tid:
            out[tid] = r
    return out


def root_cause(why: str, cell: str) -> str:
    w = why or ""
    if "AttributeError" in w:
        m = re.search(r"AttributeError: ([^|]{0,60})", w)
        return "AttributeError:" + (m.group(1).strip() if m else "?")
    if "ModuleNotFoundError" in w:
        m = re.search(r"No module named '([^']+)'", w)
        return "ModuleNotFound:" + (m.group(1) if m else "?")
    if "TypeError" in w:
        m = re.search(r"TypeError: ([^|]{0,60})", w)
        return "TypeError:" + (m.group(1).strip() if m else "?")
    if "KeyError" in w:
        m = re.search(r"KeyError: ([^|]{0,60})", w)
        return "KeyError:" + (m.group(1).strip() if m else "?")
    if "AssertionError" in w:
        return "AssertionError"
    return "other"


def main():
    per_k = {}
    for k in range(1, K + 1):
        per_k[k] = {"scores": read_scores(k), "answers": read_answers(k)}

    report = {"cells": {}, "k": K, "per_run": {}}
    for k in range(1, K + 1):
        report["per_run"][k] = {
            c: (per_k[k]["scores"].get(c) or {}).get("score") for c in CELLS
        }

    print("=" * 82)
    print("scores per sample")
    print(f"{'cell':20s} " + " ".join(f"k{k:<5d}" for k in range(1, K + 1)) + "  max")
    for c in CELLS:
        vals = []
        for k in range(1, K + 1):
            s = (per_k[k]["scores"].get(c) or {}).get("score")
            vals.append(s)
        shown = " ".join((f"{v:<6.4f}" if isinstance(v, float) else "NA    ") for v in vals)
        got = [v for v in vals if isinstance(v, float)]
        print(f"{c:20s} {shown}  {max(got) if got else 'NA'}")

    print("=" * 82)
    print("P1  distinct sha256 per cell (>=3 in >=2 cells required)")
    p1_cells = 0
    for c in CELLS:
        shas = []
        for k in range(1, K + 1):
            a = per_k[k]["answers"].get(c)
            if a:
                shas.append(str(a.get("sha256"))[:12])
        uniq = sorted(set(shas))
        hit = len(uniq) >= 3
        p1_cells += 1 if hit else 0
        print(f"  {c:20s} n={len(shas)} distinct={len(uniq)} {'OK' if hit else 'few'} {uniq}")
        report["cells"].setdefault(c, {})["distinct_sha"] = uniq
    report["P1_cells_ge3"] = p1_cells
    report["P1_holds"] = p1_cells >= 2

    print("=" * 82)
    print("P2  max score > 0 per cell")
    s_count = 0
    p3_count = 0
    for c in CELLS:
        got = [(per_k[k]["scores"].get(c) or {}).get("score") for k in range(1, K + 1)]
        got = [v for v in got if isinstance(v, float)]
        mx = max(got) if got else None
        nz = mx is not None and mx > 0
        s_count += 1 if nz else 0
        p3_count += 1 if (mx is not None and mx >= 0.6) else 0
        print(f"  {c:20s} max={mx} {'>0' if nz else '==0'}")
        report["cells"][c]["max_score"] = mx
        report["cells"][c]["scores"] = got
        # P4 root causes
        causes = []
        for k in range(1, K + 1):
            sc = per_k[k]["scores"].get(c) or {}
            if sc:
                causes.append(root_cause(sc.get("why"), c))
        from collections import Counter
        cnt = Counter(causes)
        top, n = (cnt.most_common(1)[0] if cnt else ("none", 0))
        report["cells"][c]["causes"] = dict(cnt)
        report["cells"][c]["dominant_cause"] = top
        report["cells"][c]["dominant_n"] = n
        print(f"      causes={dict(cnt)}")
    report["P2_S"] = s_count
    report["P2_holds"] = s_count >= 1
    report["P3_cells_ge06"] = p3_count

    print("=" * 82)
    print(f"P1 holds={report['P1_holds']} (cells>=3 distinct: {p1_cells}/4)")
    print(f"P2 S={s_count}/4 holds={report['P2_holds']}")
    print(f"P3 cells with max>=0.6: {p3_count}")
    for c in CELLS:
        d = report["cells"][c]
        print(f"  P4 {c:20s} dominant={d['dominant_cause']!r} n={d['dominant_n']}/8 "
              f"{'STABLE' if d['dominant_n'] >= 5 else ''}")

    (EV / "readings_v259b.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", EV / "readings_v259b.json")


if __name__ == "__main__":
    main()
