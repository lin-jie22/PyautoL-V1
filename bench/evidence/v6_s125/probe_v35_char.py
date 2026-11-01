#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V35 逐字符证据：`+16` 的载体到底是不是一个数字。

把 PREREG §0 里那条「此前已知」固化成**可复现**的读数，而不是留在会话里的一句话。
只读 `/tmp/v26`（2026-10-31 的冻结产物），零 LLM、零内核改动。

三件事：
  ① `e1wi` 每一步 state 的两轮逐字节比较 —— 到底哪一步不同、差几个字；
  ② 那一步的字符级 diff —— 差异的具体形态；
  ③ proxy 台账里 `e1wi` 每次请求的 `req_sha` —— 请求在 socket 层是不是真的不同。

用法: probe_v35_char.py [rundir=/tmp/v26]
"""
from __future__ import annotations

import difflib
import hashlib
import json
import sys
from pathlib import Path


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:10]


def load_states(p: Path) -> dict[tuple[str, int, int], str]:
    out: dict[tuple[str, int, int], str] = {}
    if not p.exists():
        return out
    seen: dict[tuple[str, int], int] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        base = (str(d["task"]), int(d["it"]))
        idx = seen.get(base, 0)
        seen[base] = idx + 1
        out[(base[0], base[1], idx)] = str(d["state"])
    return out


def scores(p: Path) -> dict[str, float]:
    f = p / "runs.jsonl"
    if not f.exists():
        return {}
    rows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not rows:
        return {}
    return {str(t["id"]): float(t.get("score") or 0) * 16 for t in rows[-1]["report"]["per_task"]}


def window(w: Path, tag: str) -> tuple[float, float] | None:
    f = w / f"time_{tag}.txt"
    if not f.exists():
        return None
    parts = f.read_text(encoding="utf-8").split()
    nums = [p for p in parts if p.isdigit()]
    if len(nums) < 2:
        return None
    return float(nums[0]), float(nums[1])


def main(argv: list[str]) -> int:
    w = Path(argv[1] if len(argv) > 1 else "/tmp/v26")
    print("=" * 78)
    print(f"V35 逐字符证据  {w}")
    print("=" * 78)

    A = load_states(w / "states_d2_r1.jsonl")
    B = load_states(w / "states_d2_r2.jsonl")
    SA, SB = scores(w / "bench_d2_r1"), scores(w / "bench_d2_r2")

    print("\n--- ① 分数 ---")
    for tid in sorted(set(SA) | set(SB)):
        mark = "  <<< 变了" if SA.get(tid) != SB.get(tid) else ""
        print(f"  {tid:6s} r1={SA.get(tid, float('nan')):5.0f}  r2={SB.get(tid, float('nan')):5.0f}{mark}")

    print("\n--- ② e1wi 每一步 state 的两轮比较 ---")
    keys = sorted(k for k in A if k[0] == "e1wi")
    for k in keys:
        a, b = A[k], B.get(k, "")
        same = a == b
        print(f"  it={k[1]} idx={k[2]}  len {len(a)} / {len(b)}  "
              f"sha {sha(a)} / {sha(b)}  {'逐字节相同' if same else '**不同**'}")

    print("\n--- ③ 不同那几步的字符级 diff ---")
    for k in keys:
        a, b = A[k], B.get(k, "")
        if a == b:
            continue
        sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            print(f"  it={k[1]} idx={k[2]}  {tag}: a[{i1}:{i2}]={a[i1:i2]!r} "
                  f"b[{j1}:{j2}]={b[j1:j2]!r}")
            lo = max(0, i1 - 70)
            print(f"     上下文: ...{a[lo:i2 + 70]!r}...")

    print("\n--- ④ proxy 台账：e1wi 的每次请求（socket 层指纹）---")
    pf = w / "proxy.jsonl"
    if not pf.exists():
        print("  ⚠️ proxy.jsonl 不存在")
    else:
        rows = [json.loads(x) for x in pf.read_text(encoding="utf-8").splitlines() if x.strip()]
        wins = {t: window(w, t) for t in ("d2_r1", "d2_r2")}
        for tag, ww in wins.items():
            if not ww:
                print(f"  ⚠️ {tag} 无时间窗")
                continue
            sel = [r for r in rows if ww[0] <= r["t_wall"] <= ww[1]]
            print(f"  [{tag}] 窗 {ww[0]:.0f}~{ww[1]:.0f}  共 {len(sel)} 次请求；前 3 次：")
            for i, r in enumerate(sel[:3]):
                print(f"    #{i} req_sha={r['req_sha'][:16]} up_sha={r['up_sha'][:16]} "
                      f"req_bytes={r['req_bytes']} prompt_tokens={r['usage']['prompt_tokens']} "
                      f"resp_bytes={r['resp_bytes']} completion={r['usage']['completion_tokens']} "
                      f"finish={r['finish_reason']}")
        # 逐条对照：两次运行的第 i 次请求是否同指纹
        a = [r for r in rows if wins.get("d2_r1") and wins["d2_r1"][0] <= r["t_wall"] <= wins["d2_r1"][1]]
        b = [r for r in rows if wins.get("d2_r2") and wins["d2_r2"][0] <= r["t_wall"] <= wins["d2_r2"][1]]
        print(f"  逐条对照（共 {min(len(a), len(b))} 对）：")
        for i in range(min(len(a), len(b))):
            eq = "同" if a[i]["req_sha"] == b[i]["req_sha"] else "**异**"
            print(f"    #{i:2d} req_sha {eq}  {a[i]['req_sha'][:12]} / {b[i]['req_sha'][:12]}  "
                  f"bytes {a[i]['req_bytes']} / {b[i]['req_bytes']}")

    print("\n" + "=" * 78)
    print("⛔ 本脚本只输出事实。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
