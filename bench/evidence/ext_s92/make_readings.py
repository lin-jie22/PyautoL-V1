"""从一次 bench 跑的 `runs.jsonl` 里摊出逐题逐轮的格数表。

⛔ 只读。产出的表与 `bench/evidence/model_s90/readings.log` 同格式，
   换一次跑就换一次表 —— 但**不许**把不同 `suite_sha` 的表并列比较。

    python3 bench/evidence/ext_s92/make_readings.py --bench /tmp/... --suite bench/specgen/tasks_v3.jsonl
"""

from __future__ import annotations

import argparse
import json


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", required=True)
    ap.add_argument("--suite", required=True)
    ap.add_argument("--title", default="")
    a = ap.parse_args()

    with open(f"{a.bench}/runs.jsonl", encoding="utf-8") as fh:
        row = json.loads(fh.readline())

    suite = [json.loads(x) for x in open(a.suite, encoding="utf-8") if x.strip()]
    cells = {t["id"]: len(t["tests"]) for t in suite}
    ids = [t["id"] for t in suite]

    rep = row["repeat"]
    pt = row["report"]["per_task"]
    me = row["measurement"]
    cal = row.get("caliber", {})
    llm = cal.get("llm_cond", {}) if isinstance(cal, dict) else {}

    grid: dict[str, dict[int, int]] = {i: {} for i in ids}
    for r in pt:
        grid[r["id"]][int(r["rep"])] = round(float(r["score"]) * cells[r["id"]])

    print("=" * 72)
    print(f"  {a.title or '读数'}")
    print("=" * 72)
    print(f"  suite = {a.suite}")
    print(f"  sig = {row.get('caliber_sig')} | repeat = {rep} | "
          f"budget_tag = {row.get('budget_tag')}")
    print(f"  llm_cond = model={llm.get('model')} endpoint={llm.get('endpoint')} "
          f"timeout_s={llm.get('timeout_s')} offline={llm.get('offline')}")
    rep_ok = row["report"]
    print(f"  attempted/recorded/skipped/failed = "
          f"{rep_ok.get('attempted')} {rep_ok.get('recorded')} "
          f"{rep_ok.get('skipped_no_answer')} {len(rep_ok.get('errors') or [])}")
    print()
    print("  题      " + "".join(f"  r{k + 1} " for k in range(rep)) + "   格数")
    totals = [0] * rep
    for i in ids:
        line = f"  {i:6s} "
        for k in range(rep):
            v = grid[i].get(k + 1)
            line += f"{v:4d} " if v is not None else "   - "
            totals[k] += v or 0
        print(line + f"   /{cells[i]}")
    print("  合计    " + "".join(f"{t:5d} " for t in totals))

    n_cells = sum(cells.values())
    print()
    print(f"  均分={me['mean']:.4f}  hi={me['hi']:.4f}  lo={me['lo']:.4f}  "
          f"std={me['std']:.4f}")
    hi = max(totals) / n_cells
    lo = min(totals) / n_cells
    print(f"  逐轮均分 = {[round(t / n_cells, 4) for t in totals]}   极差 = "
          f"{(max(totals) - min(totals))} 格（{(hi - lo):.4f}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
