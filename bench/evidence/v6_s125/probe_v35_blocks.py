#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V35 块级归因：十五轮里「累积」到底动过 state 的哪一块？

零 LLM、零内核改动、零新题集。只读 `/tmp/v1x` 下**早已冻结**的 `states_*.jsonl`。

为什么是块级（PREREG §2 的理由）：
  V25~V33 十五轮的读数全部落在 `Σ` 上，而 `Σ` 对「文本层面发生了什么」无能。
  本轮换一个**恒存在的产物**（state dump）去问一个更前置的问题：
  **累积臂比重置臂多出来的那份记忆，究竟进了 state 的哪一块、进了几个字？**
  如果某块从未动过，那么「靠它起效」的假设在本装置里就是结构性空的。

分类只有三种（判据写死在 PREREG，本脚本只执行不解释）：
  same        —— 该块逐字节相同
  handle-only —— 把 `b#<digits>` 归一化成 `b#N` 后相同 ⇒ 差异**只是**句柄漂移
  content     —— 归一化后仍不同 ⇒ 差异是内容

用法: probe_v35_blocks.py [root] [--verbose]
       root 默认 /tmp；`--verbose` 逐 (task,it) 印出，否则只印摘要
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BLOCKS = ("[目标]", "[工作记忆]", "[语义记忆]", "[外部评审]", "[轨迹]", "[第")
ORDER = ["[目标]", "[工作记忆]", "[语义记忆]", "[外部评审]", "[轨迹]", "[步号]"]
HANDLE = re.compile(r"b#\d+")
RUNNAME = re.compile(r"v\d+")
TAG = re.compile(r"states_(?P<arm>.+)_r(?P<rnd>\d+)")


def split_blocks(st: str) -> dict[str, str]:
    """按行首块标记切块；同名多段按出现顺序以换行拼接。"""
    lines = st.splitlines()
    marks: list[tuple[int, str]] = []
    for i, ln in enumerate(lines):
        if ln.startswith(BLOCKS):
            name = ln.split(" ")[0].split("：")[0]
            marks.append((i, "[步号]" if name == "[第" else name))
    out: dict[str, list[str]] = {}
    for j, (i, name) in enumerate(marks):
        end = marks[j + 1][0] if j + 1 < len(marks) else len(lines)
        out.setdefault(name, []).append("\n".join(lines[i:end]))
    return {k: "\n".join(v) for k, v in out.items()}


def diff_blocks(a: str, b: str) -> dict[str, tuple[str, int, int]]:
    A, B = split_blocks(a), split_blocks(b)
    res: dict[str, tuple[str, int, int]] = {}
    for nm in ORDER:
        ta, tb = A.get(nm, ""), B.get(nm, "")
        if ta == tb:
            res[nm] = ("same", len(ta), len(tb))
        elif HANDLE.sub("b#N", ta) == HANDLE.sub("b#N", tb):
            res[nm] = ("handle-only", len(ta), len(tb))
        else:
            res[nm] = ("content", len(ta), len(tb))
    return res


def load(path: Path) -> dict[tuple[str, int, int], str]:
    """键必须含**行序**：同一 (task, it) 会写多行（纪律 35）。"""
    out: dict[tuple[str, int, int], str] = {}
    if not path.exists():
        return out
    seen: dict[tuple[str, int], int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        base = (str(d["task"]), int(d["it"]))
        idx = seen.get(base, 0)
        seen[base] = idx + 1
        out[(base[0], base[1], idx)] = str(d["state"])
    return out


def main(argv: list[str]) -> int:
    root = Path("/tmp")
    verbose = "--verbose" in argv
    for a in argv[1:]:
        if not a.startswith("--"):
            root = Path(a)

    dirs = sorted((d for d in root.iterdir()
                   if d.is_dir() and RUNNAME.fullmatch(d.name)),
                  key=lambda p: int(p.name[1:]))

    # 全局计数：块名 -> {class: 次数}
    total: dict[str, dict[str, int]] = {nm: {} for nm in ORDER}
    # 「从无到有」：r1 该块为空、rk 非空
    born: dict[str, int] = {nm: 0 for nm in ORDER}
    # handle-only 差异里，句柄取值变化的具体形态
    handle_forms: dict[tuple[str, str], int] = {}
    pairs_seen = 0
    per_run_rows: list[str] = []

    for d in dirs:
        files = sorted(d.glob("states_*.jsonl"))
        if not files:
            continue
        arms: dict[str, dict[int, Path]] = {}
        for f in files:
            m = TAG.fullmatch(f.stem)
            if not m:
                continue
            arms.setdefault(m["arm"], {})[int(m["rnd"])] = f
        for arm, rounds in sorted(arms.items()):
            if len(rounds) < 2:
                continue
            base_r = min(rounds)
            base = load(rounds[base_r])
            for r in sorted(rounds):
                if r == base_r:
                    continue
                cur = load(rounds[r])
                keys = sorted(set(base) & set(cur))
                pairs_seen += 1
                cnt: dict[str, dict[str, int]] = {nm: {"same": 0, "handle-only": 0, "content": 0}
                                                  for nm in ORDER}
                detail: list[str] = []
                for k in keys:
                    res = diff_blocks(base[k], cur[k])
                    for nm, (kind, la, lb) in res.items():
                        cnt[nm][kind] += 1
                        total[nm][kind] = total[nm].get(kind, 0) + 1
                        if kind != "same":
                            A, B = split_blocks(base[k]), split_blocks(cur[k])
                            if not A.get(nm) and B.get(nm):
                                born[nm] += 1
                            if kind == "handle-only":
                                ha = tuple(HANDLE.findall(A.get(nm, "")))
                                hb = tuple(HANDLE.findall(B.get(nm, "")))
                                for x, y in zip(ha, hb):
                                    if x != y:
                                        handle_forms[(x, y)] = handle_forms.get((x, y), 0) + 1
                            detail.append(f"      {k[0]:5s} it{k[1]}.{k[2]} {nm}: {kind} {la}->{lb}")
                miss = len(set(cur) - set(base))
                tagline = (f"  {d.name}/{arm}: {Path(rounds[base_r]).stem} → {Path(rounds[r]).stem}"
                           f"  共同键 {len(keys)}  r{r} 多 {miss} 步")
                brief = "  ".join(
                    f"{nm}={cnt[nm]['same']}/{cnt[nm]['handle-only']}/{cnt[nm]['content']}"
                    for nm in ORDER if any(cnt[nm].values()))
                per_run_rows.append(tagline)
                per_run_rows.append(f"      [same/handle/content] {brief}")
                if verbose and detail:
                    per_run_rows.extend(detail)

    print("=" * 78)
    print(f"V35 块级归因  root={root}  比较对 {pairs_seen}")
    print("=" * 78)
    print("\n--- ① 逐 run 逐臂（same / handle-only / content）---")
    for row in per_run_rows:
        print(row)

    print("\n--- ② 全局块级计数（所有比较对的 (task,it,行序) 累加）---")
    print(f"  {'块':10s} {'same':>7s} {'handle':>7s} {'content':>8s} {'从无到有':>8s}")
    for nm in ORDER:
        c = total[nm]
        print(f"  {nm:10s} {c.get('same',0):7d} {c.get('handle-only',0):7d} "
              f"{c.get('content',0):8d} {born[nm]:8d}")

    print("\n--- ③ handle-only 差异的形态（句柄从什么变成什么）---")
    if not handle_forms:
        print("  （无）")
    for (x, y), n in sorted(handle_forms.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {x:>8s} → {y:<8s} {n:4d} 次")

    print("\n" + "=" * 78)
    print("⛔ 本脚本只输出事实。判决见 PREREG.md §3；本文件不含任何结论。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
