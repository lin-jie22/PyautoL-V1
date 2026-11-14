#!/usr/bin/env python3
"""V92 评估用：从历史证据里提取各套件的分数分布。

不跑模型、不改内核、不打开归档库（只读 runs.jsonl 文本）。
"""
from __future__ import annotations

import json
import pathlib
import collections

ROOT = pathlib.Path(__file__).resolve().parents[3]
EV = ROOT / "bench/evidence"


def main() -> None:
    best: dict[str, float] = {}
    worst: dict[str, float] = {}
    ntasks: dict[str, int] = {}
    runs: dict[str, int] = {}

    for p in EV.rglob("runs.jsonl"):
        for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not ln.strip():
                continue
            try:
                d = json.loads(ln)
            except Exception:
                continue
            rep = d.get("report") or {}
            per = rep.get("per_task") or []
            sc = [t.get("score") for t in per if t.get("score") is not None]
            if not sc:
                continue
            name = str(d.get("suite") or rep.get("suite") or "?")[:44]
            m = round(sum(sc) / len(sc), 4)
            best[name] = max(best.get(name, -1), m)
            worst[name] = min(worst.get(name, 2), m)
            ntasks[name] = max(ntasks.get(name, 0), len(sc))
            runs[name] = runs.get(name, 0) + 1

    print(f"{'套件':46s}{'题':>3s}{'跑数':>6s}{'最差':>8s}{'最好':>8s}")
    for k in sorted(best, key=lambda x: -best[x]):
        print(f"{k:46s}{ntasks[k]:3d}{runs[k]:6d}{worst[k]:8.4f}{best[k]:8.4f}")


if __name__ == "__main__":
    main()
