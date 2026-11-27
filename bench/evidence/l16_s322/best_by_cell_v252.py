"""汇总所有历史读数里每一格的最高分（只读，不调模型）。

用途：判断「spec-inline 的 39 题里还有没有没做满的格」。
若有 ⇒ V251 选 `g7d_spec_incomplete` 就是**选错套件**，不必造新题。
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
PATTERNS = ["state/*.jsonl"]
for sub in ("bench/evidence",):
    PATTERNS += [f"{sub}/*/*.jsonl", f"{sub}/*/*.json"]

best: dict[str, dict] = collections.defaultdict(
    lambda: {"best": -1.0, "rows": 0, "families": set()})


def eat(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    n = 0
    if path.suffix == ".json":
        try:
            blob = json.loads(text)
        except Exception:                                    # noqa: BLE001
            return 0
        items = blob if isinstance(blob, list) else [blob]
        for d in items:
            if isinstance(d, dict):
                n += one(d)
        return n
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except Exception:                                    # noqa: BLE001
            continue
        if isinstance(d, dict):
            n += one(d)
    return n


def one(d: dict) -> int:
    tid = d.get("task_id") or d.get("id")
    val = d.get("score")
    if not isinstance(tid, str) or not isinstance(val, (int, float)):
        return 0
    # 只认真实评测读数：排除 goal_obs 里 kind=COVER 之类的非打分行也不会错，
    # 因为 COVER 行同样带 score，保留更全。
    e = best[tid]
    e["rows"] += 1
    if float(val) > e["best"]:
        e["best"] = float(val)
    fam = d.get("family")
    if isinstance(fam, str) and fam:
        e["families"].add(fam)
    return 1


def main() -> int:
    total = 0
    seen_files = 0
    for pat in PATTERNS:
        for p in sorted(ROOT.glob(pat)):
            if p.is_file():
                k = eat(p)
                if k:
                    seen_files += 1
                    total += k
    print(f"文件 {seen_files} 份｜读数行 {total} 行｜格 {len(best)}")
    print(f"{'task':34} {'best':>6} {'rows':>5}  families")
    print("-" * 78)
    for t, e in sorted(best.items(), key=lambda kv: (kv[1]["best"], kv[0])):
        fam = ",".join(sorted(e["families"]))[:30]
        print(f"{t:34} {e['best']:6.4f} {e['rows']:5d}  {fam}")
    out = Path(__file__).resolve().parent / "best_by_cell_v252.json"
    out.write_text(json.dumps({k: {"best": v["best"], "rows": v["rows"],
                                   "families": sorted(v["families"])}
                               for k, v in best.items()},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
