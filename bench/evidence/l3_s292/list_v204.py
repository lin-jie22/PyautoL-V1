#!/usr/bin/env python3
"""V204 β 判读辅助：把四臂产出**打乱臂序**后全列，供人工逐条判「可迁移」。

⚠️ 打乱是为了让判读不受臂标签污染（预注册 §2）。
⚠️ 本脚本只做展示与统计，不做判定 —— 判定由人给出，写在 `judge_v204.py`。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent


def main() -> int:
    d = json.loads((OUT / "readings_v204.json").read_text(encoding="utf-8"))
    rows = []
    for arm in ("A", "B", "C", "D"):
        a = d.get(arm) or {}
        for i, t in enumerate(a.get("texts") or []):
            rows.append({"arm": arm, "text": t})
    # 去重（同文本只留一条，记下它出现在哪些臂）
    by = {}
    for r in rows:
        by.setdefault(r["text"], set()).add(r["arm"])
    uniq = sorted(by.items(), key=lambda kv: hashlib.md5(kv[0].encode()).hexdigest())

    print(f"四臂产出合计 {len(rows)} 条，去重后 {len(uniq)} 条")
    print(f"各臂：", {a: d[a]["n_abs_rows"] for a in ("A", "B", "C", "D") if d.get(a)})
    print("\n" + "=" * 84)
    for n, (t, arms) in enumerate(uniq, 1):
        print(f"{n:3d}. [{','.join(sorted(arms))}] {t}")
    print("=" * 84)

    (OUT / "pool_v204.json").write_text(
        json.dumps([{"text": t, "arms": sorted(a)} for t, a in uniq],
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ {OUT/'pool_v204.json'}（{len(uniq)} 条，供 judge 用）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
