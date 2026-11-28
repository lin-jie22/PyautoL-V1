#!/usr/bin/env python3
"""V259e: persist the real T=0.8 samples and produce readings_v259e.json.

Sources: /tmp/v259d_s1 (verification run, k=1) and /tmp/v259e_s{1..4} (k=4).
Copies the raw answers.jsonl + runs.jsonl into answers_t08/ so the evidence
survives /tmp cleanup (discipline 227: artifacts must be persisted).
"""
import json
import shutil
from pathlib import Path

EV = Path(__file__).resolve().parent
CELLS = ["g7t2_parse_pairs", "s02_limiter", "m02_vfs", "m03_tiered"]
SRC = [("k0", "/tmp/v259d_s1")] + [(f"k{k}", f"/tmp/v259e_s{k}") for k in range(1, 5)]


def main():
    dst = EV / "answers_t08"
    dst.mkdir(exist_ok=True)
    report = {"temperature": 0.8, "cells": {}, "samples": {}}

    for tag, base in SRC:
        for name in ("answers.jsonl", "runs.jsonl"):
            p = Path(base) / "bench" / name
            if p.exists():
                shutil.copy2(p, dst / f"{tag}_{name}")
        scores = {}
        rp = Path(base) / "bench" / "runs.jsonl"
        if rp.exists():
            for line in rp.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                for t in (json.loads(line).get("report") or {}).get("per_task") or []:
                    cid, sc = t.get("id"), t.get("score")
                    if cid and isinstance(sc, (int, float)):
                        scores[cid] = float(sc)
        shas = {}
        ap = Path(base) / "bench" / "answers.jsonl"
        if ap.exists():
            for line in ap.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                d = json.loads(line)
                if d.get("task_id"):
                    shas[d["task_id"]] = str(d.get("sha256"))[:12]
        report["samples"][tag] = {"scores": scores, "sha": shas}

    print(f"{'cell':22s} " + " ".join(f"{t:>5s}" for t, _ in SRC) + "   max   mean  distinct")
    for c in CELLS:
        vals = [report["samples"][t]["scores"].get(c) for t, _ in SRC]
        got = [v for v in vals if v is not None]
        mx = max(got) if got else None
        mean = round(sum(got) / len(got), 4) if got else None
        shas = {report["samples"][t]["sha"].get(c) for t, _ in SRC}
        shas.discard(None)
        report["cells"][c] = {"scores": vals, "max": mx, "mean": mean,
                              "distinct_sha": len(shas),
                              "sha": [report["samples"][t]["sha"].get(c) for t, _ in SRC]}
        shown = " ".join((f"{v:5.1f}" if v is not None else "   NA") for v in vals)
        print(f"{c:22s} {shown}  {mx:5.1f} {mean:5.2f}  {len(shas)}")

    report["P1_holds"] = sum(1 for c in CELLS if report["cells"][c]["distinct_sha"] >= 3) >= 2
    report["P2_S"] = sum(1 for c in CELLS if (report["cells"][c]["max"] or 0) > 0)
    report["P3_cells_ge06"] = sum(1 for c in CELLS if (report["cells"][c]["max"] or 0) >= 0.6)
    print()
    print(f"P1 holds={report['P1_holds']}  P2 S={report['P2_S']}/4  "
          f"P3 cells>=0.6 = {report['P3_cells_ge06']}")

    (EV / "readings_v259e.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", EV / "readings_v259e.json", "and", dst)


if __name__ == "__main__":
    main()
