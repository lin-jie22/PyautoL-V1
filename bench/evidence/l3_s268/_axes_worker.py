#!/usr/bin/env python3
"""V180 的两臂工作进程：在一份**指定的** `mw/goals.py` 上算每格的修复轴。

为什么要子进程：`repair_axes` 是模块级函数，换实现只能换文件。
⚠️ 纪律 65：装置必须自报「我导入了谁」—— 所以它把 `goals.__file__` 与它的
sha256 一起打出来，父进程比对，⛔ 不许靠 `sys.path[0]` 猜。

输入（stdin JSON）：{"obs": [...], "log": [...]}
输出（last stdout line JSON）：{"sha": ..., "file": ..., "axes": {cell: [..]},
"reason": {cell: ...}, "beh": {cell: kind or null}}
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw import goals as G          # noqa: E402


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    d = json.load(sys.stdin)
    obs = d.get("obs") or []
    log = d.get("log") or []
    esc = G.escalations(log, obs)
    axes, reasons, beh = {}, {}, {}
    for c in sorted(esc):
        e = esc[c]
        m = e.get("movability")
        axes[c] = list(G.repair_axes(str(e.get("reason") or ""), m))
        reasons[c] = e.get("reason")
        beh[c] = ((m or {}).get("beh") or {}).get("kind")
    src = Path(G.__file__).resolve()
    print(json.dumps({"sha": sha(src), "file": str(src), "axes": axes,
                      "reason": reasons, "beh": beh}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
