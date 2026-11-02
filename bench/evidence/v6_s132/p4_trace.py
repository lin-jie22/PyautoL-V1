#!/usr/bin/env python3
"""V42 让位轨迹：把两臂日志里**同一段上下文**并排打出来。

## 为什么要这个脚本而不是直接贴日志

「让位 fire 了 1 次、救回 0 次」这三个数是台账里的，而**为什么救不回**在日志里。
本轮的原因只有一个，且很具体：

    候选 2/2 是一个 `run_shell`（不是答案）⇒ `--no-external` 下 shell 未开启
    ⇒ guard `tool_misuse` 拦掉 ⇒ 那个候选**结构上不可能被交付**

⇒ 结论必须写成「**候选 ≠ 答案**」，不能写成「让位没用」。
两者差一个 V43 的设计决定：让位该不该只挑交付动作。

⛔ 本脚本只读 `logs/`，不写任何证据路径。
"""
from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs"


def ctx(path: Path, pat: str, back: int = 5, fwd: int = 8) -> list[str]:
    if not path.exists():
        return [f"⛔ 缺日志 {path.name}"]
    lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
    out = []
    for i, l in enumerate(lines):
        if re.search(pat, l):
            out.append(f"  ── {path.name}:{i + 1} ──")
            out += ["    " + x[:150] for x in lines[max(0, i - back):i + fwd]]
    return out


def main() -> None:
    print("=" * 96)
    print("A. on 臂：让位真的发生了（`cand_failover=1`）")
    print("=" * 96)
    print("\n".join(ctx(LOGS / "run_s3f1.log", r"让位给候选")) or "  （没有让位行）")

    print()
    print("=" * 96)
    print("B. off 臂：同一段上下文（`cand_failover=0`）—— 候选=2 但第 2 个从未被碰")
    print("=" * 96)
    print("\n".join(ctx(LOGS / "run_s3f0.log", r"候选=2")) or "  （没有 候选=2 的轮次）")

    print()
    print("=" * 96)
    print("C. 两臂的状态分布（**相同**是重点：让位没有把 cascade 变多）")
    print("=" * 96)
    for name in ("run_s3f0.log", "run_s3f1.log"):
        p = LOGS / name
        if not p.exists():
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        status = {}
        for m in re.finditer(r"■ ([a-z_]+)", t):
            status[m.group(1)] = status.get(m.group(1), 0) + 1
        rounds = len(re.findall(r"候选=\d", t))
        cand = {}
        for m in re.finditer(r"候选=(\d)", t):
            cand[m.group(1)] = cand.get(m.group(1), 0) + 1
        print(f"  {name:16} 状态={status} 轮={rounds} 候选分布={dict(sorted(cand.items()))}"
              f" 打回={len(re.findall(r'自检未过（第', t))}"
              f" 全拦={len(re.findall(r'全部候选被拦', t))}")


if __name__ == "__main__":
    main()
