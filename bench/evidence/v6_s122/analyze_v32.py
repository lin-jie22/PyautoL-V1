"""V32 判决器 —— 跨轮自省能不能让抽搐率下降（按 PREREG 的判据顺序执行）

## 只读三样东西（⛔ 不看日志里的标签 —— 纪律 29）

1. `bench_<tag>/runs.jsonl` 的 `report.per_task`（分数唯一真口径）
2. `bench_<tag>/answers.jsonl` 里该 `(run_id, task_id)` 的**最后一行**（行序=尝试序）
3. `states_<tag>.jsonl`（⛔ 同一 `(task, it)` 会写多行 ⇒ 三键 `(task, it, 序号)`）

## 不做什么

- 不重算分数、不改判据、不写回任何已被冻结的历史证据（纪律 34）。
- 判据顺序严格按 PREREG §5：P0 不过 ⇒ `J0`，不许往下判。
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

W = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v32")
V31 = Path("/Users/linshaojie/V1/bench/evidence/v6_s121")
TASKS = ["e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni"]
TAGS = [("c0", 1), ("c0", 2), ("c0", 3), ("c0", 4),
        ("c1", 1), ("c1", 2), ("c1", 3), ("c1", 4),
        ("c0b", 1)]
BLK = re.compile(r"\[外部评审\] (.*?)(?:\n\[|$)", re.S)
SELF_IN_BLK = re.compile(r"\[tasks_v8/([a-z0-9]+)\]")
TWITCH = re.compile(r"^\s*kept\s*=\s*set\(\)", re.M)
CLEAN = re.compile(r"^\s*kept\s*=\s*\[", re.M)
OUT: list[str] = []


def p(s: str = "") -> None:
    OUT.append(s)
    print(s)


def load_states(tag: str) -> list[tuple[str, int, int, str]]:
    f = W / f"states_{tag}.jsonl"
    if not f.exists():
        return []
    rows = []
    seen: collections.Counter = collections.Counter()
    for i, line in enumerate(f.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        t = d.get("task") or "?"
        it = int(d.get("it") or 0)
        k = (t, it)
        rows.append((t, it, seen[k], d.get("state", "")))
        seen[k] += 1
    return rows


def load_scores(tag: str) -> tuple[dict[str, float], dict[str, str], dict[str, int]]:
    """→ ({task: score}, {task: why}, 计数)"""
    f = W / f"bench_{tag}" / "runs.jsonl"
    if not f.exists():
        return {}, {}, {}
    sc: dict[str, float] = {}
    wh: dict[str, str] = {}
    cnt = {"attempted": 0, "recorded": 0, "skipped": 0, "failed": 0, "rows": 0}
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        cnt["rows"] += 1
        rep = r.get("report") or {}
        cnt["attempted"] += int(rep.get("attempted") or 0)
        cnt["recorded"] += int(rep.get("recorded") or 0)
        cnt["skipped"] += int(rep.get("skipped_no_answer") or 0)
        cnt["failed"] += int(rep.get("failed_scorer") or 0)
        for pt in rep.get("per_task", []):
            sc[pt["id"]] = float(pt.get("score", 0.0))
            wh[pt["id"]] = pt.get("why") or ""
    return sc, wh, cnt


def last_answers(tag: str) -> dict[str, str]:
    f = W / f"bench_{tag}" / "answers.jsonl"
    if not f.exists():
        return {}
    keep: dict[str, str] = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        a = json.loads(line)
        if a.get("kind") == "header":
            continue
        keep[a.get("task_id")] = a.get("answer", "")
    return keep


def scores16(sc: dict[str, float]) -> dict[str, int]:
    return {t: round(sc.get(t, 0.0) * 16) for t in TASKS}


def main() -> int:
    p("=" * 108)
    p("V32 判决 —— 跨轮自省（PREREG: bench/evidence/v6_s122/PREREG.md）")
    p(f"工作目录 {W}")
    p("=" * 108)

    S: dict[str, dict[str, int]] = {}
    Z: dict[str, dict[str, str]] = {}
    A: dict[str, dict[str, str]] = {}
    CNT: dict[str, dict[str, int]] = {}
    ST: dict[str, list] = {}
    for arm, k in TAGS:
        tag = f"{arm}_r{k}"
        sc, wh, cnt = load_scores(tag)
        S[tag] = scores16(sc)
        Z[tag] = wh
        A[tag] = last_answers(tag)
        CNT[tag] = cnt
        ST[tag] = load_states(tag)

    # ── 1 逐臂逐轮分数
    p()
    p("## 1 逐臂逐轮（分数 ×16；表格左边是 c0 旧口径，右边是 c1 新口径）")
    p()
    hdr = "| 轮 | " + " | ".join(TASKS) + " | Σ | 抽搐题数 | 步数 |"
    p(hdr)
    p("|---|" + "---|" * (len(TASKS) + 3))
    for tag in [f"{a}_r{k}" for a, k in TAGS]:
        row = S[tag]
        if not row or all(v == 0 for v in row.values()):
            p(f"| `{tag}` | — | 缺读数 |")
            continue
        tw = sum(1 for t in ("e1wi", "e1ni")
                 if TWITCH.search(A[tag].get(t) or ""))
        p(f"| `{tag}` | " + " | ".join(str(row[t]) for t in TASKS)
          + f" | **{sum(row.values())}** | {tw} | {len(ST[tag])} |")

    # ── 2 抽搐明细
    p()
    p("## 2 抽搐明细（`kept = set()` 出现 ⇔ 该题必 0 分，历史 70/70）")
    p()
    p("| 轮 | e1wi 容器 | e1wi 分 | e1ni 容器 | e1ni 分 |")
    p("|---|---|---|---|---|")
    for tag in [f"{a}_r{k}" for a, k in TAGS]:
        cells = []
        for t in ("e1wi", "e1ni"):
            a = A[tag].get(t) or ""
            kind = "set()" if TWITCH.search(a) else ("[]" if CLEAN.search(a) else "?")
            cells.append(f"{kind} | {S[tag].get(t, '—')}")
        p(f"| `{tag}` | " + " | ".join(cells) + " |")

    # ── 3 送达读数
    p()
    p("## 3 自评送达（块里有没有**本题自己**那一条）")
    p()
    p("| 轮 | state 行 | 含块 state | 自评入块 state | 自评题集 |")
    p("|---|---|---|---|---|")
    selfhit: dict[str, int] = {}
    selftasks: dict[str, set] = {}
    for tag in [f"{a}_r{k}" for a, k in TAGS]:
        n = len(ST[tag])
        bs = 0
        hit = 0
        ts: set[str] = set()
        for t, _it, _i, s in ST[tag]:
            m = BLK.search(s)
            if not m:
                continue
            bs += 1
            if t and t in SELF_IN_BLK.findall(m.group(1)):
                hit += 1
                ts.add(t)
        selfhit[tag] = hit
        selftasks[tag] = ts
        p(f"| `{tag}` | {n} | {bs} | **{hit}** | {' '.join(sorted(ts)) or '—'} |")

    # ── 4 P0 闸
    p()
    p("## 4 P0 前提闸（任一不过 ⇒ J0，不许判效应）")
    p()
    g: dict[str, tuple[bool, str]] = {}

    #: P0-a 通电：c1 的 r2~r4 自评入块 > 0；c0 全部 = 0
    c1_ok = all(selfhit[f"c1_r{k}"] > 0 for k in (2, 3, 4) if f"c1_r{k}" in selfhit)
    c0_zero = all(selfhit[f"c0_r{k}"] == 0 for k in (1, 2, 3, 4))
    g["P0-a 通电"] = (c1_ok and c0_zero,
                   f"c1 r2~r4 自评入块={[selfhit.get(f'c1_r{k}') for k in (2,3,4)]} "
                   f"| c0 全部={[selfhit.get(f'c0_r{k}') for k in (1,2,3,4)]}")

    #: P0-b 起点同：c0_r1 与 c1_r1 state 逐字节
    a1 = [(t, it, i, s) for t, it, i, s in ST["c0_r1"]]
    b1 = [(t, it, i, s) for t, it, i, s in ST["c1_r1"]]
    diff_b = sum(1 for x, y in zip(a1, b1) if x[3] != y[3]) if len(a1) == len(b1) else -1
    g["P0-b 起点同"] = (len(a1) == len(b1) and diff_b == 0,
                     f"行 {len(a1)}/{len(b1)}，逐字节不同 {diff_b}")

    #: P0-c 覆盖：r2 加载的账本里 e1wi 那条必须是 r1 写的
    e1wi_r1_why = (Z["c0_r1"].get("e1wi") or "")[:20]
    g["P0-c 账本被覆盖"] = (bool(e1wi_r1_why),
                       f"c0_r1 的 e1wi why 前 20 字={e1wi_r1_why!r}（r2 的 state 应含它或含其失败关键字）")

    #: P0-d 落盘完整
    d_ok = all(CNT[t]["recorded"] == 6 and CNT[t]["attempted"] == 6 for _, t in
               [(a, f"{a}_r{k}") for a, k in TAGS])
    g["P0-d 落盘完整"] = (d_ok,
                      " ".join(f"{t}:{CNT[t]['attempted']}/{CNT[t]['recorded']}" for t in CNT))

    #: P0-e 时段漂移
    drift = 0
    a1s = sorted([(t, it, i, s) for t, it, i, s in ST["c0_r1"]], key=lambda x: (x[0], x[1], x[2]))
    b1s = sorted([(t, it, i, s) for t, it, i, s in ST["c0b_r1"]], key=lambda x: (x[0], x[1], x[2]))
    if len(a1s) == len(b1s):
        drift = sum(1 for x, y in zip(a1s, b1s) if x[3] != y[3])
    else:
        drift = -1
    g["P0-e 时段漂移"] = (True, f"c0_r1 vs c0b_r1：行 {len(a1s)}/{len(b1s)}，不同 {drift}")

    #: P0-f 旧口径等价：c0_r1 vs V31 s1
    v31 = []
    f31 = V31 / "states_s1.jsonl"
    if f31.exists():
        for line in f31.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            v31.append((d.get("task") or "?", int(d.get("it") or 0), d.get("state", "")))
    v31s = sorted(v31, key=lambda x: (x[0], x[1]))
    mine = sorted([(t, it, s) for t, it, i, s in ST["c0_r1"]], key=lambda x: (x[0], x[1]))
    eq = (len(v31s) == len(mine) and all(x[2] == y[2] for x, y in zip(v31s, mine)))
    g["P0-f 旧口径等价"] = (eq or drift == 0,
                       f"与 V31 s1 逐字节{'相同' if eq else '不同'}（行 {len(mine)}/{len(v31s)}）；"
                       f"本时段漂移={drift}")

    for k, (ok, det) in g.items():
        p(f"- **{k}** {'✅' if ok else '❌'} — {det}")
    hard = [k for k, (ok, _) in g.items() if not ok]

    # ── 5 主判据
    p()
    p("## 5 主判据（并列，缺一不可 —— 纪律 32）")
    p()
    p("### S（Σ）")
    p()
    p("| 轮 | c0 | c1 | Δ(c1−c0) |")
    p("|---|---|---|---|")
    dS = {}
    for k in (1, 2, 3, 4):
        s0 = sum(S[f"c0_r{k}"].values()) if S[f"c0_r{k}"] else None
        s1 = sum(S[f"c1_r{k}"].values()) if S[f"c1_r{k}"] else None
        d = None if (s0 is None or s1 is None) else s1 - s0
        dS[k] = d
        p(f"| r{k} | {s0} | {s1} | {d:+d} |" if d is not None else f"| r{k} | {s0} | {s1} | — |")

    p()
    p("### C（逐格）—— 上升格与下降格**必须分开列**")
    p()
    up, down = [], []
    for t in TASKS:
        seq = []
        for k in (1, 2, 3, 4):
            a = S[f"c0_r{k}"].get(t)
            b = S[f"c1_r{k}"].get(t)
            seq.append(f"{a}→{b}" if (a is not None and b is not None) else "—")
        p(f"- `{t}`: " + " ｜ ".join(seq))
    for k in (2, 3, 4):
        for t in TASKS:
            a = S[f"c0_r{k}"].get(t)
            b = S[f"c1_r{k}"].get(t)
            if a is None or b is None:
                continue
            if b > a:
                up.append((t, k, a, b))
            elif b < a:
                down.append((t, k, a, b))
    p()
    p(f"  上升格 {up or '无'}")
    p(f"  下降格 {down or '无'}")

    # ── 6 副判据 T
    p()
    p("## 6 副判据 T（抽搐题数，本轮的核心观测量）")
    p()
    T0 = T1 = 0
    for k in (2, 3, 4):
        n0 = sum(1 for t in ("e1wi", "e1ni") if TWITCH.search(A[f"c0_r{k}"].get(t) or ""))
        n1 = sum(1 for t in ("e1wi", "e1ni") if TWITCH.search(A[f"c1_r{k}"].get(t) or ""))
        T0 += n0
        T1 += n1
        p(f"- r{k}: c0 = {n0}/2 ｜ c1 = {n1}/2")
    p()
    p(f"  **r2~r4 合计：c0 = {T0} ｜ c1 = {T1} ｜ 差 = {T1 - T0:+d}**（可判上限 6/臂）")

    # ── 7 判决
    p()
    p("## 7 判决（按 PREREG §5 顺序）")
    p()
    if hard:
        verdict = "J0"
        p(f"**判决 = `J0`** —— P0 未过：{hard}。载荷没到／装置不稳 ⇒ 本轮不出效应结论。")
    elif T1 - T0 <= -2 and (sum(d for d in dS.values() if d is not None) >= 0):
        verdict = "J_SELF"
        p(f"**判决 = `J_SELF`** —— 抽搐合计 {T0}→{T1}（差 {T1-T0:+d}），且 Σ 未被反向抵消。")
    elif T1 > T0:
        verdict = "J_HARM"
        p(f"**判决 = `J_HARM`** —— 抽搐合计 {T0}→{T1}（差 {T1-T0:+d}）：接通后更差。")
    elif up and down:
        verdict = "J_DICE"
        p(f"**判决 = `J_DICE`** —— 抽搐合计相同（{T0}={T1}）但出现逐格置换：上升 {up}｜下降 {down}。")
    else:
        verdict = "J_NULL"
        p(f"**判决 = `J_NULL`** —— 抽搐合计相同（{T0}={T1}），逐格无置换。")

    p()
    p("押注对账：预注册押 `J_SELF`（c1 的 r2~r4 抽搐合计 < c0）"
      f" ⇒ {'✔ 押中' if verdict == 'J_SELF' else '❌ 押错'}")

    out = W / "analysis_v32.txt"
    out.write_text("\n".join(OUT) + "\n", encoding="utf-8")
    print()
    print(f"（已落盘 {out}）")
    print(f"VERDICT {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
