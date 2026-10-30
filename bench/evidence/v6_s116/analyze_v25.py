#!/usr/bin/env python3
"""V25 读数：跨轮累积有没有通电？按**预注册**口径出判决（不在这里发明判据）。

预注册：`bench/evidence/v6_s116/PREREG.md`

判据（逐字照 PREREG §2，**按顺序取第一条命中**）：
    1  noise_floor ≠ 0                                    ⇒ J4 装置不稳
    2  c2 的 e2ni 或 e3ni 在轮 2/3/4 任一 > 0              ⇒ J1 累积通电（正）
    3  mean(c2, 轮 2..4) < mean(c2, 轮 1) − noise_floor    ⇒ J2 累积有害
    4  以上都不中                                          ⇒ J3 累积无效

⚠️ 早退轮（日志里出现「不得**用于判定」/ `可信=❌` / `skipped_no_answer≠0`）**一律不计入**，
   并在报告里单列。纪律 24：客户端超时（服务端其实算完）≠ 模型交空。

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
ARMS = (("c2",  "static=2 盲报 · echo=1 · 累积", 2, 1, 4, True),
        ("c2z", "static=2 盲报 · echo=0 · 累积", 2, 0, 4, True),
        ("f2",  "static=2 盲报 · echo=1 · 重置", 2, 1, 2, False),
        ("c1",  "static=1 报名字 · echo=1 · 累积", 1, 1, 2, True))
UNSAT = ("e2ni", "e3ni")          # V19~V24 从未被拉起过的两题（PREREG §5）


def rows(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()
            if x.strip()]


def round_load(w: Path, arm: str, r: int) -> tuple[bool, str, float, dict, dict]:
    """一轮读数。返回 (有效?, 作废理由, 格分, 逐题分, 逐题 sha)。"""
    log = w / f"run_{arm}_r{r}.log"
    if not log.exists():
        return False, "无日志", float("nan"), {}, {}
    txt = log.read_text(encoding="utf-8", errors="replace")
    if "不得**用于判定" in txt or "可信=❌" in txt or "skipped_no_answer=0" not in txt:
        miss = [x.strip() for x in txt.splitlines() if "solver 未产出答案" in x]
        return False, (miss[0][:60] if miss else "早退（无 skipped_no_answer=0）"), float("nan"), {}, {}

    rs = rows(w / f"bench_{arm}_r{r}" / "runs.jsonl")
    if not rs:
        return False, "无 runs.jsonl", float("nan"), {}, {}
    per: dict[str, list[float]] = {}
    for t in rs[-1]["report"]["per_task"]:
        per.setdefault(str(t["id"]), []).append(float(t.get("score") or 0.0))
    task_mean = {k: sum(v) / len(v) for k, v in per.items()}
    sc = sum(task_mean.get(t, 0.0) for t in TASKS) / len(TASKS) * CELLS
    sha: dict[str, str] = {}
    for x in rows(w / f"bench_{arm}_r{r}" / "answers.jsonl"):
        if x.get("task_id"):
            sha[str(x["task_id"])] = hashlib.sha256(
                str(x.get("answer") or "").encode()).hexdigest()[:12]
    return True, "", sc, task_mean, sha


def answer_of(w: Path, arm: str, r: int, tid: str) -> str:
    for x in rows(w / f"bench_{arm}_r{r}" / "answers.jsonl"):
        if str(x.get("task_id")) == tid:
            return str(x.get("answer") or "")
    return ""


def main(argv: list[str]) -> int:
    w = Path(argv[1] if len(argv) > 1 else "/tmp/v25")
    print(f"工作目录 {w}\n")

    data: dict[str, dict[int, tuple]] = {}
    print("一、逐轮逐题格分（每格 = score×16；满分 96，V24 记录的可达上限 64）")
    head = "        " + "".join(f"{t:>8}" for t in TASKS) + "   轮分"
    for arm, label, _st, _e, rounds, _c in ARMS:
        print(f"\n  {arm}  {label}  （计划 {rounds} 轮）")
        print(head)
        data[arm] = {}
        for r in range(1, rounds + 1):
            ok, why, sc, tm, sha = round_load(w, arm, r)
            data[arm][r] = (ok, why, sc, tm, sha)
            if not ok:
                print(f"  r{r}    ⛔ 作废：{why}")
                continue
            line = "".join(f"{tm.get(t, 0.0) * 16:8.0f}" for t in TASKS)
            print(f"  r{r}    {line}{sc:8.1f}")

    # ── 二、装置闸 ─────────────────────────────────────────────────────────
    print("\n二、装置闸（全部来自**恒存在的产物**，不数「只在失败时才出现的文件」）")
    p0e = w / "p0e.txt"
    lines = [x for x in p0e.read_text(encoding="utf-8").splitlines() if x.strip()] if p0e.exists() else []
    mis = [x for x in lines if "MISMATCH" in x]
    print(f"  P0-e 判据名核对：{len(lines)} 行 / MISMATCH {len(mis)} 行（应 12 行 0 失败）")
    for x in mis:
        print(f"     ❌ {x}")

    acc = w / "acc.txt"
    alines = [x for x in acc.read_text(encoding="utf-8").splitlines() if x.strip()] if acc.exists() else []
    print(f"\n  P0-f 累积正控：{len(alines)} 行（应 12）")
    print("     ⚠️ 判据是「累积臂的库要比上一轮大 / 重置臂 pre 必须 0」，⛔ 不是「rewards=6」")
    print("        —— 早退轮只写 5 条（attempted=6 recorded=5），那是通道故障不是装置故障。")
    bad_acc = 0
    for x in alines:
        f = dict(p.split("=", 1) for p in x.split() if "=" in p)
        tag = x.split()[0]
        pre, post = int(f.get("pre", -1)), int(f.get("post", -1))
        rew, cum = f.get("rewards", "?"), f.get("cumul", "?")
        if cum == "0":
            ok = (pre == 0 and post > 0)
            why = "重置臂 pre=0 ✅" if ok else f"重置臂 pre={pre} ❌"
        else:
            ok = (post > pre)
            why = f"累积 {pre}B→{post}B reward {rew}" if ok else f"累积没长大 {pre}B→{post}B ❌"
        bad_acc += 0 if ok else 1
        print(f"     {'✅' if ok else '❌'} {tag:10s} {why}")
    print(f"     ⇒ 不通过 {bad_acc} 行")

    print("\n  P0-a 有效轮清点")
    for arm, _l, _s, _e, rounds, _c in ARMS:
        vs = [r for r in data[arm] if data[arm][r][0]]
        dead = [r for r in data[arm] if not data[arm][r][0]]
        print(f"     {arm:4s} 有效 {vs}  作废 {dead}")

    # ── 三、噪声下限 ───────────────────────────────────────────────────────
    print("\n三、噪声下限（f2 两轮极差；f2 每轮从空库重置 ⇒ 差别只剩通道漂移）")
    f2 = [v[2] for r, v in data["f2"].items() if v[0]]
    nf = (max(f2) - min(f2)) if len(f2) >= 2 else float("nan")
    print(f"  f2 = {[round(x, 1) for x in f2]}  极差 = {nf:.1f}")
    f2_shas = {r: v[4] for r, v in data["f2"].items() if v[0]}
    if len(f2_shas) >= 2:
        ks = sorted(f2_shas)
        same = all(f2_shas[k] == f2_shas[ks[0]] for k in ks)
        print(f"  f2 六题交付件逐字节：{'**全同**' if same else '**有差异**'}")
    for arm, label, _s, _e, rounds, _c in ARMS:
        v = [x[2] for x in data[arm].values() if x[0]]
        if len(v) >= 2:
            print(f"  {arm:4s} 自身极差 = {max(v) - min(v):.1f}  （{label}）")

    # ── 四、判决 ───────────────────────────────────────────────────────────
    def mean(xs) -> float:
        v = [x for x in xs if x == x]
        return sum(v) / len(v) if v else float("nan")

    c2_valid = sorted([r for r in data["c2"] if data["c2"][r][0]])
    c2_scores = {r: data["c2"][r][2] for r in c2_valid}
    c2_r1 = c2_scores.get(1, float("nan"))
    c2_rise = mean([c2_scores[r] for r in c2_valid if r >= 2])

    unsat_any = []
    for r in c2_valid:
        if r < 2:
            continue
        tm = data["c2"][r][3]
        for t in UNSAT:
            v = tm.get(t, 0.0) * 16
            if v > 0:
                unsat_any.append((r, t, v))

    print("\n四、判决（PREREG §2，按顺序取第一条命中）")
    print(f"  c2 有效轮 = {c2_valid}  轮分 = {[round(c2_scores[r], 1) for r in c2_valid]}")
    print(f"  mean(c2, 轮≥2 有效) = {c2_rise:.1f}   mean(c2, 轮1) = {c2_r1:.1f}   "
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
        verdict, why = "J2 累积有害", f"轮≥2 均值比轮 1 低 {c2_r1 - c2_rise:.1f} 格"
    else:
        verdict, why = "J3 累积无效", "轮≥2 与轮 1 无可辨差异，且未饱和题一格未动"
    print(f"\n  ⇒ {verdict}")
    print(f"     {why}")

    # ── 五、逐格归因（判据只给判决，这一节给**哪一格因为什么变了**）──────────
    print("\n五、逐格归因（这是判决之外最有信息量的一节）")
    for arm, _l, _s, _e, rounds, _c in ARMS:
        if 1 not in data[arm] or not data[arm][1][0]:
            continue
        base_ok, _w, base_sc, base_tm, base_sha = data[arm][1]
        for r in sorted(data[arm]):
            if r == 1 or not data[arm][r][0]:
                continue
            _ok, _w2, sc, tm, sha = data[arm][r]
            diffs = []
            for t in TASKS:
                if base_sha.get(t) != sha.get(t):
                    diffs.append(f"{t} {base_tm.get(t, 0) * 16:.0f}→{tm.get(t, 0) * 16:.0f}")
            print(f"  {arm}_r{r}  {base_sc:.0f}→{sc:.0f} ({sc - base_sc:+.0f})   变的格："
                  f"{'、'.join(diffs) if diffs else '无（六题逐字节全同）'}")

    # ── 六、载荷贡献（§2.2）───────────────────────────────────────────────
    print("\n六、载荷贡献（§2.2）：c2 vs c2z，只比共同有效轮")
    common = [r for r in c2_valid if r >= 2 and data["c2z"].get(r) and data["c2z"][r][0]]
    if common:
        a = mean([data["c2"][r][2] for r in common])
        b = mean([data["c2z"][r][2] for r in common])
        d = a - b
        print(f"  共同轮 {common}:  mean(c2)={a:.1f}  mean(c2z)={b:.1f}  Δ_payload = {d:+.1f}")
        if nf == nf and nf == 0.0:
            print(f"  ⇒ {'|Δ| ≤ 0 ⇒ **载荷内容值 0**（累积条件下复现 V23）' if abs(d) <= 0 else '超噪声（noise_floor=0）⇒ 载荷有作用，方向见上'}")
        print("  ⚠️ c2 与 c2z 的库**路径依赖**：两臂各自演化，episodes 数不同 ⇒ prompt 不可能逐字节相同。")
        print("     ⛔ 所以 Δ_payload 是「同起点 + 同 env 差 + 不同路径」的差，不是严格单变量。")
    else:
        print("  无共同有效轮 ⇒ 不判")

    # ── 七、形状（§2.1）───────────────────────────────────────────────────
    print("\n七、形状判据（§2.1）：轮 3 / 轮 4 是否与轮 2 逐格相同")
    if 2 in data["c2"] and data["c2"][2][0]:
        base = [data["c2"][2][3].get(t, 0.0) * 16 for t in TASKS]
        print(f"  轮2 基线 {base}")
        got = False
        for r in (3, 4):
            if r in data["c2"] and data["c2"][r][0]:
                cur = [data["c2"][r][3].get(t, 0.0) * 16 for t in TASKS]
                diff = [f"{t}:{b:.0f}→{c:.0f}" for t, b, c in zip(TASKS, base, cur) if b != c]
                print(f"  轮{r} {'逐格相同' if not diff else '有变化 ' + str(diff)}")
                got = True
            else:
                print(f"  轮{r} ⛔ 作废（{data['c2'].get(r, (0, '没跑'))[1]}）⇒ 这一点拿不到")
        print(f"  ⇒ {'可判' if got else '**形状判据本轮无法判**：轮 3/4 都是装置失败'}")
    else:
        print("  轮 2 无效 ⇒ 不判")

    # ── 八、机制取证 ───────────────────────────────────────────────────────
    print("\n八、机制取证")
    print("  ⓐ e1ni 是否保住了 `import re`（V22/V23 那个「重写删 import」形态）")
    for arm, _l, _s, _e, rounds, _c in ARMS:
        for r in range(1, rounds + 1):
            if not data[arm][r][0]:
                continue
            a = answer_of(w, arm, r, "e1ni")
            print(f"    {arm}_r{r}  {'有' if 'import re' in a else '**无**'}  "
                  f"分 {data[arm][r][3].get('e1ni', 0) * 16:.0f}  sha {data[arm][r][4].get('e1ni', '-')}")
    print("\n  ⓑ 打回报告里的标识符（static=1 必须有名字、static=2 必须没有）")
    for arm, _l, _s, _e, rounds, _c in ARMS:
        named = blind = 0
        for r in range(1, rounds + 1):
            log = w / f"run_{arm}_r{r}.log"
            if log.exists():
                txt = log.read_text(encoding="utf-8", errors="replace")
                named += txt.count("未定义的名字")
                blind += txt.count("不提供细节")
        print(f"    {arm:4s} 带名字 {named} 行 ｜ 盲报 {blind} 行")
    print("\n  ⓒ 库的语义记忆 / 事实条数（累积到底往 prompt 里放了什么）")
    import sqlite3
    for arm in ("c2", "c2z", "c1", "f2"):
        db = w / f"mem_{arm}" / "memory.db"
        if not db.exists():
            continue
        try:
            c = sqlite3.connect(str(db))
            nf_ = c.execute("select count(*) from facts").fetchone()[0]
            nr = c.execute("select count(*) from episodes where kind='reward'").fetchone()[0]
            print(f"    {arm:4s} facts={nf_}  reward={nr}")
            for row in c.execute("select text from facts order by id"):
                print(f"         · {str(row[0])[:78]}")
            c.close()
        except Exception as e:
            print(f"    {arm:4s} 读库失败 {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
