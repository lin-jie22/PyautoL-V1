"""每格的历史读数分布（只读）。

动机：`best=1.0` 只证明「至少有一轮做到过」，不证明「稳定做到」。
若同一格的读数在 1.0 与 0.2 之间大幅摆动，那**可改进的维度不是上限，是下限**。
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
vals: dict[str, list[float]] = collections.defaultdict(list)
for pat in ("state/*.jsonl", "bench/evidence/*/*.jsonl"):
    for p in sorted(ROOT.glob(pat)):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                d = json.loads(line)
            except Exception:                                # noqa: BLE001
                continue
            if not isinstance(d, dict):
                continue
            tid, val = d.get("task_id"), d.get("score")
            if isinstance(tid, str) and isinstance(val, (int, float)):
                vals[tid].append(float(val))


def main() -> int:
    rows = []
    for t, xs in vals.items():
        n = len(xs)
        if n < 3:
            continue
        c = collections.Counter(round(x, 4) for x in xs)
        lo, hi = min(xs), max(xs)
        rows.append((hi - lo, t, n, lo, hi, sum(xs) / n, c))
    rows.sort(reverse=True)
    print(f"{'task':32} {'n':>4} {'min':>5} {'max':>5} {'mean':>6} {'span':>5}  top values")
    print("-" * 100)
    for span, t, n, lo, hi, mean, c in rows[:25]:
        top = " ".join(f"{v:.2f}x{k}" for v, k in c.most_common(4))
        print(f"{t:32} {n:4d} {lo:5.2f} {hi:5.2f} {mean:6.3f} {span:5.2f}  {top}")
    wide = [r for r in rows if r[0] >= 0.4]
    print(f"\n跨度 >= 0.4 的格：{len(wide)} / {len(rows)}（n>=3 的格共 {len(rows)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
