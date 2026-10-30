#!/usr/bin/env python3
"""V24 读数：4 臂 × 3 轮，按**预注册**口径出判决（不在这里发明判据）。

预注册：`bench/evidence/v6_s115/PREREG.md`

    Δ1 = mean(a1p1) − mean(a1p2)      # 有奖励块时，报名字值多少
    Δ0 = mean(a0p1) − mean(a0p2)      # 无奖励块时，同上

判据（逐字照 PREREG §4）：
    J1  Δ1 ≥ +16 且 a1p1 的 e1ni = 16        ⇒ 盲报就是那个洞
    J2  a1p1 的 e1ni 交付件 == a1p2 的（逐字节）⇒ 消息被忽略
    J3  Δ1 ≤ −16                              ⇒ 报名字有害
    J4  其它                                  ⇒ 只报读数
混合（0 < Δ1 < 16）⇒ 报 J4 并逐题列表，⛔ 不许说「净效应为正所以有用」。

用法：
    python3 bench/evidence/v6_s115/analyze_v24.py /tmp/v24 [轮数]
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

CELLS = 96
TASKS = ("e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni")
ARMS = (("a0p2", "a0 echo=0 · static=2 盲报（默认）", 0, 2),
        ("a0p1", "a0 echo=0 · static=1 报名字", 0, 1),
        ("a1p2", "a1 echo=1 · static=2 盲报（默认）", 1, 2),
        ("a1p1", "a1 echo=1 · static=1 报名字", 1, 1))


def rows(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()
            if x.strip()]


def round_score(w: Path, arm: str, r: int) -> tuple[float, dict, dict]:
    """一轮的格分 + 逐题分 + 逐题交付件 sha。分母固定 6 题，缺题记 0。"""
    rs = rows(w / f"bench_{arm}_r{r}" / "runs.jsonl")
    if not rs:
        return float("nan"), {}, {}
    pt = rs[-1]["report"]["per_task"]
    per: dict[str, list[float]] = {}
    for t in pt:
        per.setdefault(str(t["id"]), []).append(float(t.get("score") or 0.0))
    task_mean = {k: sum(v) / len(v) for k, v in per.items()}
    sc = sum(task_mean.get(t, 0.0) for t in TASKS) / len(TASKS) * CELLS
    sha: dict[str, str] = {}
    for x in rows(w / f"bench_{arm}_r{r}" / "answers.jsonl"):
        sha[str(x.get("task_id"))] = hashlib.sha256(
            str(x.get("answer") or "").encode()).hexdigest()[:12]
    return sc, task_mean, sha


def answer_of(w: Path, arm: str, r: int, tid: str) -> str:
    for x in rows(w / f"bench_{arm}_r{r}" / "answers.jsonl"):
        if str(x.get("task_id")) == tid:
            return str(x.get("answer") or "")
    return ""


def main(argv: list[str]) -> int:
    w = Path(argv[1] if len(argv) > 1 else "/tmp/v24")
    rounds = int(argv[2]) if len(argv) > 2 else 3
    print(f"工作目录 {w}  轮数/臂 {rounds}\n")

    scores: dict[str, list[float]] = {}
    print("一、逐轮逐题格分（每格 = score×16）")
    head = "        " + "".join(f"{t:>8}" for t in TASKS) + "   轮分"
    for arm, label, _e, _s in ARMS:
        print(f"\n  {label}")
        print(head)
        vals = []
        for r in range(1, rounds + 1):
            sc, tm, _sha = round_score(w, arm, r)
            vals.append(sc)
            cells = "".join(f"{tm.get(t, 0.0) * 16:8.0f}" for t in TASKS)
            print(f"  r{r}    {cells}{sc:8.1f}")
        scores[arm] = vals

    # ── 二、装置闸 ─────────────────────────────────────────────────────────
    print("\n二、装置闸")
    ok_n = mis_n = 0
    p0d = w / "p0d.txt"
    if p0d.exists():
        for line in p0d.read_text(encoding="utf-8").splitlines():
            if line.strip().endswith("OK"):
                ok_n += 1
            elif "MISMATCH" in line:
                mis_n += 1
    print(f"  P0-d 库重置 sha 核对：{ok_n} 通过 / {mis_n} 失败 "
          f"（应等于 {rounds * 4}）")

    fails: list[str] = []
    missing: list[str] = []
    for arm, _l, _e, _s in ARMS:
        for r in range(1, rounds + 1):
            log = w / f"run_{arm}_r{r}.log"
            if not log.exists():
                missing.append(f"{arm}_r{r}")
                continue
            txt = log.read_text(encoding="utf-8", errors="replace")
            if "skipped_no_answer=0" not in txt:
                fails.append(f"{arm}_r{r}")
    print(f"  P0-a 轮次 {rounds * 4 - len(missing)}/{rounds * 4} 存在  "
          f"空答轮：{fails or '无'}  miss：{missing or '无'}")

    # P0-e 正控：判据名必须与臂匹配
    bad_e = w / "p0e_fail.txt"
    n_bad = 0
    if bad_e.exists():
        n_bad = len([x for x in bad_e.read_text(encoding="utf-8").splitlines() if x.strip()])
    print(f"  P0-e 判据名与臂不匹配的轮：{n_bad}（应为 0 ⇒ 否则 env 没生效）")

    # ── 三、噪声下限 ───────────────────────────────────────────────────────
    print("\n三、噪声下限（每臂 3 轮极差，取最大）")
    nf = 0.0
    for arm, label, _e, _s in ARMS:
        v = [x for x in scores[arm] if x == x]
        spread = (max(v) - min(v)) if v else float("nan")
        nf = max(nf, spread if spread == spread else 0.0)
        print(f"  {label}: 极差={spread:.1f}  值={[round(x, 1) for x in v]}")
    print(f"  noise_floor = {nf:.1f}")

    # ── 四、判决 ───────────────────────────────────────────────────────────
    def mean(arm: str) -> float:
        v = [x for x in scores[arm] if x == x]
        return sum(v) / len(v) if v else float("nan")

    m = {a: mean(a) for a, *_ in ARMS}
    d1 = m["a1p1"] - m["a1p2"]
    d0 = m["a0p1"] - m["a0p2"]
    print("\n四、判决")
    for a, label, _e, _s in ARMS:
        print(f"  mean({a}) = {m[a]:.1f}   {label}")
    print(f"  Δ1 = a1p1−a1p2 = {d1:+.1f}（有奖励块时，报名字值多少）")
    print(f"  Δ0 = a0p1−a0p2 = {d0:+.1f}（无奖励块时，同上）")

    e1ni_a1p1 = round_score(w, "a1p1", 1)[1].get("e1ni", 0.0) * 16
    sha_p1 = {r: round_score(w, "a1p1", r)[2].get("e1ni", "-")
              for r in range(1, rounds + 1)}
    sha_p2 = {r: round_score(w, "a1p2", r)[2].get("e1ni", "-")
              for r in range(1, rounds + 1)}
    same_bytes = set(sha_p1.values()) == set(sha_p2.values())

    if d1 >= 16 and e1ni_a1p1 == 16:
        v, why = "J1 报名字有用", "盲报就是那个洞；自省循环的**载荷**是瓶颈"
    elif same_bytes:
        v, why = "J2 报名字无用", ("a1p1 的 e1ni 交付件与 a1p2 **逐字节相同** ⇒ "
                                  "消息被忽略 ⇒ 闸的修复路径整条要重做")
    elif d1 <= -16:
        v, why = "J3 报名字有害", "与 §74/§80 在 v1 尺子上的方向一致"
    elif 0 < d1 < 16:
        v, why = "J4 混合", "某题修回、另一题弄坏 ⇒ 逐题列表，⛔ 不许说净效应为正"
    else:
        v, why = "J4 其它", "读数形态不在 J1~J3 里 ⇒ 只报数"
    gate = (nf == 0.0) or (abs(d1) > nf)
    print(f"\n  ⇒ {v}")
    print(f"     {why}")
    print(f"     e1ni 在 a1p1 = {e1ni_a1p1:.0f} 分")
    print(f"     附加条件①噪声：noise_floor={nf:.1f} ⇒ "
          f"{'✅ 过' if gate else '❌ 不过（UNKNOWN）'}")

    # ── 五、机制取证 ───────────────────────────────────────────────────────
    print("\n五、机制取证（`e1ni`：本轮唯一预期会动的题）")
    for arm, label, _e, _s in ARMS:
        keeps, shas = [], []
        for r in range(1, rounds + 1):
            a = answer_of(w, arm, r, "e1ni")
            keeps.append("有" if "import re" in a else "**无**")
            shas.append(round_score(w, arm, r)[2].get("e1ni", "-")[:8])
        print(f"  {label}\n     import re = {' / '.join(keeps)}   sha={' '.join(shas)}")
    print("\n  打回报告里的标识符（s=1 必须出现名字、s=2 必须没有）：")
    for arm in ("a1p2", "a1p1"):
        log = w / f"run_{arm}_r1.log"
        if not log.exists():
            continue
        txt = log.read_text(encoding="utf-8", errors="replace")
        named = [s for s in txt.splitlines() if "未定义的名字" in s]
        blind = [s for s in txt.splitlines() if "不提供细节" in s]
        print(f"  {arm}: 带名字的报告 {len(named)} 行 ｜ 盲报 {len(blind)} 行"
              f" ｜ 首条带名字：{(named[0].strip()[:90] if named else '—')}")

    # ── 六、附带读数：cascade ──────────────────────────────────────────────
    print("\n六、附带读数：`■ cascade` 计数（闸打回计入 consecutive_failures）")
    for arm, label, _e, _s in ARMS:
        n = 0
        for r in range(1, rounds + 1):
            log = w / f"run_{arm}_r{r}.log"
            if log.exists():
                n += log.read_text(encoding="utf-8", errors="replace").count("■ cascade")
        print(f"  {label}: cascade {n} 次 / {rounds} 轮")

    # ── 七、边界复述 ───────────────────────────────────────────────────────
    print("\n七、边界（预注册 §6，逐条照抄）")
    for s in (
        "① 只能说：tasks_v8 / qwen2.5:7b / 2026-10-30 19:00 起的本时段。",
        "② ⛔ 不许说「系统自己会自省」：判据是人写的，两档也是人定的。",
        "③ ⛔ 不许说「报名字更鲁棒」：只在 6 题上分开，且只动 1~2 道题。",
        "④ 两档 sig 不同 ⇒ 只报候选间差，不并列成序列。",
        "⑤ e1wi 的 set+append 属性错不在 undefined 判据射程内，本轮不改。",
    ):
        print("  " + s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
