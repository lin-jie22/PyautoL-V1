#!/usr/bin/env python3
"""V25 读数：跨轮累积有没有通电？按**预注册**口径出判决（不在这里发明判据）。

预注册：`bench/evidence/v6_s116/PREREG.md`

判据（逐字照 PREREG §2，**按顺序取第一条命中**）：
    1  noise_floor ≠ 0                                    ⇒ J4 装置不稳
    2  c2 的 e2ni 或 e3ni 在轮 2/3/4 任一 > 0              ⇒ J1 累积通电（正）
    3  mean(c2, 轮 2..4) < mean(c2, 轮 1) − noise_floor    ⇒ J2 累积有害
    4  以上都不中                                          ⇒ J3 累积无效

形状判据（§2.1，不管落哪格都报）：c2 的轮 3 / 轮 4 是否与轮 2 逐格相同。
载荷贡献（§2.2）：Δ_payload = mean(c2, 轮2..3) − mean(c2z, 轮2..3)

用法：
    python3 bench/evidence/v6_s116/analyze_v25.py /tmp/v25
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

CELLS = 96
TASKS = ("e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni")
#: 臂 → (标签, static, echo, 轮数, 是否累积)
ARMS = (("c2",  "static=2 盲报 · echo=1  · 累积", 2, 1, 4, True),
        ("c2z", "static=2 盲报 · echo=0  · 累积", 2, 0, 4, True),
        ("f2",  "static=2 盲报 · echo=1  · 重置", 2, 1, 2, False),
        ("c1",  "static=1 报名字 · echo=1 · 累积", 1, 1, 2, True))
UNSAT = ("e2ni", "e3ni")          # V19~V24 从未被拉起过的两题（PREREG §5）


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
    w = Path(argv[1] if len(argv) > 1 else "/tmp/v25")
    print(f"工作目录 {w}\n")

    scores: dict[str, list[float]] = {}
    cells: dict[str, list[dict]] = {}
    print("一、逐轮逐题格分（每格 = score×16；满分 96，可达上限 64）")
    head = "        " + "".join(f"{t:>8}" for t in TASKS) + "   轮分"
    for arm, label, _st, _e, rounds, _c in ARMS:
        print(f"\n  {arm}  {label}  （{rounds} 轮）")
        print(head)
        vals, cs = [], []
        for r in range(1, rounds + 1):
            sc, tm, _sha = round_score(w, arm, r)
            vals.append(sc)
            cs.append(tm)
            line = "".join(f"{tm.get(t, 0.0) * 16:8.0f}" for t in TASKS)
            print(f"  r{r}    {line}{sc:8.1f}")
        scores[arm], cells[arm] = vals, cs

    # ── 二、装置闸 ─────────────────────────────────────────────────────────
    print("\n二、装置闸（全部来自**恒存在的产物**，不数「只在失败时才出现的文件」）")
    p0e = w / "p0e.txt"
    lines = [x for x in p0e.read_text(encoding="utf-8").splitlines() if x.strip()] if p0e.exists() else []
    mis = [x for x in lines if "MISMATCH" in x]
    print(f"  P0-e 判据名核对：{len(lines)} 行 / MISMATCH {len(mis)} 行"
          f"（应 12 行 0 失败 ⇒ 否则 env 没生效）")
    for x in mis:
        print(f"     ❌ {x}")

    acc = w / "acc.txt"
    alines = [x for x in acc.read_text(encoding="utf-8").splitlines() if x.strip()] if acc.exists() else []
    print(f"  P0-f 累积正控：{len(alines)} 行（应 12）")
    for x in alines:
        tag = x.split()[0]
        rew = x.split("rewards=")[1].split()[0] if "rewards=" in x else "?"
        cum = "累积" if "cumul=1" in x else "重置"
        ok = "✅" if (rew == "6" or (cum == "重置" and rew in ("6", "-1", "0"))) else "⚠️"
        print(f"     {ok} {tag:10s} reward 行 {rew:>3}  {cum}")

    missing, empty = [], []
    for arm, _l, _s, _e, rounds, _c in ARMS:
        for r in range(1, rounds + 1):
            log = w / f"run_{arm}_r{r}.log"
            if not log.exists():
                missing.append(f"{arm}_r{r}")
                continue
            txt = log.read_text(encoding="utf-8", errors="replace")
            if "skipped_no_answer=0" not in txt:
                empty.append(f"{arm}_r{r}")
    total = sum(a[4] for a in ARMS)
    print(f"  P0-a 轮次 {total - len(missing)}/{total} 存在  "
          f"空答轮：{empty or '无'}  miss：{missing or '无'}")

    # ── 三、噪声下限 ───────────────────────────────────────────────────────
    print("\n三、噪声下限（f2 两轮极差；f2 每轮从空库重置 ⇒ 差别只剩通道漂移）")
    f2 = [x for x in scores["f2"] if x == x]
    nf = (max(f2) - min(f2)) if len(f2) >= 2 else float("nan")
    print(f"  f2 = {[round(x, 1) for x in f2]}  极差 = {nf:.1f}")
    for arm, label, _s, _e, rounds, _c in ARMS:
        v = [x for x in scores[arm] if x == x]
        if len(v) >= 2:
            print(f"  {arm:4s} 自身极差 = {max(v) - min(v):.1f}  （{label}）")

    # ── 四、判决 ───────────────────────────────────────────────────────────
    def mean(xs: list[float]) -> float:
        v = [x for x in xs if x == x]
        return sum(v) / len(v) if v else float("nan")

    c2 = scores["c2"]
    c2_rise = mean(c2[1:])            # 轮 2..4
    c2_r1 = c2[0] if c2 else float("nan")
    unsat_any = [(r, t, cells["c2"][r - 1].get(t, 0.0) * 16)
                 for r in range(2, len(c2) + 1) for t in UNSAT
                 if cells["c2"][r - 1].get(t, 0.0) > 0]

    print("\n四、判决（PREREG §2，按顺序取第一条命中）")
    print(f"  c2 轮分 = {[round(x, 1) for x in c2]}")
    print(f"  mean(c2, 轮2..4) = {c2_rise:.1f}   mean(c2, 轮1) = {c2_r1:.1f}   "
          f"差 = {c2_rise - c2_r1:+.1f}   noise_floor = {nf:.1f}")

    if nf != nf:
        verdict, why = "J4 装置不全", "f2 两轮不齐 ⇒ 拿不到噪声下限，不出判据"
    elif nf != 0.0:
        verdict, why = "J4 装置不稳", ("f2 每轮从空库重置、同 prompt 同 T=0，却拿到不同分数 ⇒ "
                                     "「同一 prompt 同一答案」在本时段不成立，本轮不出判据")
    elif unsat_any:
        detail = "、".join(f"轮{r} {t}={v:.0f}" for r, t, v in unsat_any)
        verdict, why = "J1 累积通电（正）", f"未饱和题被拉起：{detail}"
    elif c2_rise < c2_r1 - nf:
        verdict, why = "J2 累积有害", f"轮 2..4 均值比轮 1 低 {c2_r1 - c2_rise:.1f} 格"
    else:
        verdict, why = "J3 累积无效", ("轮 2..4 与轮 1 无可辨差异，且未饱和题一格未动 ⇒ "
                                     "库在续跑，分数不动")
    print(f"\n  ⇒ {verdict}")
    print(f"     {why}")

    # ── 五、形状判据（§2.1）───────────────────────────────────────────────
    print("\n五、形状判据（§2.1）：c2 的轮 3 / 轮 4 是否与轮 2 逐格相同")
    if len(c2) >= 2:
        base = [cells["c2"][1].get(t, 0.0) * 16 for t in TASKS]
        print(f"  轮2 基线 {base}")
        same_all = True
        for r in range(3, len(c2) + 1):
            cur = [cells["c2"][r - 1].get(t, 0.0) * 16 for t in TASKS]
            same = (cur == base)
            same_all &= same
            diff = [f"{t}:{b:.0f}→{c:.0f}" for t, b, c in zip(TASKS, base, cur) if b != c]
            print(f"  轮{r} {'逐格相同' if same else '有变化 ' + str(diff)}")
        print(f"  ⇒ {'**一次跳变 + 平台**（累积只发生一步）' if same_all else '多步累积（这才叫会自己变强）'}")
    else:
        print("  c2 轮数不足 2 ⇒ 不判")

    # ── 六、载荷贡献（§2.2）───────────────────────────────────────────────
    print("\n六、载荷贡献（§2.2）：c2 vs c2z，只比共同轮（轮 2、轮 3）")
    c2c = mean(scores["c2"][1:3])
    c2zc = mean(scores["c2z"][1:3])
    d = c2c - c2zc
    print(f"  mean(c2, 轮2..3) = {c2c:.1f}   mean(c2z, 轮2..3) = {c2zc:.1f}   "
          f"Δ_payload = {d:+.1f}")
    if nf == nf:
        print(f"  ⇒ {'|Δ| ≤ noise_floor ⇒ **载荷内容值 0**（累积条件下复现 V23）' if abs(d) <= nf else '超噪声 ⇒ 载荷有作用，方向见上'}")
    print("  ⛔ c2z − f2 的差**不许**归因给 reward 载荷（那条路上库也在续跑，四条通道同时在工作）")

    # ── 七、机制取证 ───────────────────────────────────────────────────────
    print("\n七、机制取证：库是否真的在长大（累积是否真的发生）")
    for arm, _l, _s, _e, rounds, cum in ARMS:
        seq = []
        for r in range(1, rounds + 1):
            db = w / f"mem_{arm}" / "memory.db"
            n = -1
            if db.exists():
                import sqlite3
                try:
                    c = sqlite3.connect(str(db))
                    n = c.execute("select count(*) from episodes where kind='reward'").fetchone()[0]
                    c.close()
                except Exception:
                    n = -1
            seq.append(n)
        print(f"  {arm:4s} 末态 reward 行 = {seq[-1] if seq else '?'}  "
              f"（{'累积：应 = 6×轮数' if cum else '重置：应 = 6'}）")

    print("\n八、e1ni 交付件（V24 的唯一塌陷格，本轮看它在累积下是否重复塌）")
    for arm, _l, _s, _e, rounds, _c in ARMS:
        shas, keeps = [], []
        for r in range(1, rounds + 1):
            a = answer_of(w, arm, r, "e1ni")
            shas.append(round_score(w, arm, r)[2].get("e1ni", "-")[:8])
            keeps.append("有" if "import re" in a else "无")
        print(f"  {arm:4s} sha={' '.join(shas)}   import re = {'/'.join(keeps)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
