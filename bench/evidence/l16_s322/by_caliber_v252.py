"""按口径分组看分布 —— 判「宽分布」是真实波动还是混口径假象。

⛔ 纪律 64/182：同一格混了不同模型 / 不同 scorer_sha 的读数，
   合起来算 min/max 会把「换了一台装置」读成「同一装置波动 1.0」。
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
TARGETS = ("z01_flatten", "z02_to_csv", "z03_parse_query", "z04_deep_get",
           "z05_group_by", "g7n1_split_amount", "g7n2_next_permutation",
           "g7n3_spiral_order", "g7n4_min_meeting_rooms")

rows: dict[str, list[dict]] = collections.defaultdict(list)
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
            if isinstance(d, dict) and d.get("task_id") in TARGETS \
                    and isinstance(d.get("score"), (int, float)):
                rows[str(d["task_id"])].append(d)


def key_of(d: dict) -> str:
    cal = d.get("caliber") or {}
    sig = d.get("caliber_sig")
    model = ((cal.get("llm_cond") or {}).get("model")
             or (cal.get("llm") or {}).get("model") or "?")
    ssha = str(cal.get("scorer_sha") or "?")[:8]
    temp = cal.get("temperature")
    return f"{str(sig)[:8]}|{model}|ssha={ssha}|T={temp}"


for t in TARGETS:
    print(f"### {t}  总行 {len(rows[t])}")
    groups: dict[str, list[float]] = collections.defaultdict(list)
    for d in rows[t]:
        groups[key_of(d)].append(float(d["score"]))
    print(f"  {'口径':56} {'n':>4} {'min':>5} {'max':>5} {'mean':>6}")
    for k, xs in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:8]:
        print(f"  {k[:56]:56} {len(xs):4d} {min(xs):5.2f} {max(xs):5.2f} "
              f"{sum(xs) / len(xs):6.3f}")
    if len(groups) == 1:
        print(f"  → 只有 1 个口径组；上表的跨度就是**同一装置**的波动")
    else:
        print(f"  → {len(groups)} 个口径组混在一起；合算的跨度不可直接当波动")
    print()
