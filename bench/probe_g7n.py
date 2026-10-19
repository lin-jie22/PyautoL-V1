#!/usr/bin/env python3
"""§17-7g **邻域对照臂 N** 的曲线探针（零模型调用，只读冻结证据）。

要锁的那件事（DESIGN §17-7 ★7g 事前登记，跑臂之前写死；**只能在这里改，且必须同步 DESIGN**）：
    7f 那个 0.80 是**局部**的还是**全局**的？
    · 局部 ⇒ 只有"上下文里含邻域"的题（`g7d2`/`g7e2` 里走 fallback top-k 的那两题）会动；
    · 全局 ⇒ 连"上下文只有自己一条"的题也跟着动（那本装置的单题读数一律作废）。

两条**结构性事实**是本探针的判据基础（7d/7e/7f 种子库上可复核）：
    `g7d2`/`g7d3` → FTS 无命中 ⇒ fallback top-k ⇒ 上下文**恒 4 条**（邻域在里）；
    `g7e1`/`g7e2` → FTS 精确命中 ⇒ 上下文**恒 1 条**（邻域不在里）。
⇒ 探针逐档**实测**注入条数，并要求它恒为 4/4/1/1；条数一变，"长度"就不再是唯一变量。

判读顺序（写死）：
  R1       观察对象取回**自己那条**；H/P 两臂条数与字符数逐题相等；E 为空；
           注入条数与 7d **逐题相同**（4/4/1/1）；跑后 facts 数 == 种子
  R-noise  每格 2 次 repeat 极差 ≤ 0.05
  R7       P vs E：每档 |Δ_P| < 0.15；≥ 0.15 ⇒ 整组作废
  P3       P 臂曲线**平**（各档 |Δ_P| < 0.05）—— 若 P 也随档位动，
           动的是"长度"不是"内容" ⇒ 整组作废
  P1       O1（`g7d2`）曲线**不平**：按注入字符排序后，相邻档极差 ≥ 0.40
  P2       O2（`g7e2`）两个端点**都 = 1.00**
  结局     a) O1 斜 + O2 平   b) O1 平（⇒ 7f 的 0.80 不可复现）   c) O1 斜 + O2 也斜

用法：
    python3 bench/probe_g7n.py --root /tmp/v1_g7n
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
for _p in (str(V1), str(V1 / "bench")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

THRESH_CONTENT = 0.15
THRESH_PLACEBO = 0.05
THRESH_R7 = 0.15
THRESH_NOISE = 0.05
STEP_MIN = 0.40               # P1 的"台阶"判据（DESIGN 事前登记里写死）
HELDOUT = V1 / "bench" / "heldout" / "g7e_hint_heldout.jsonl"

#: 7d 的注入条数（前置②的比对基准，7e/7f 实测逐题相同）。
BASE_COUNTS = {"g7d2_dedupe_keep": 4, "g7d3_fmt_amount": 4,
               "g7e1_parse_flag": 1, "g7e2_truncate_tail": 1}
#: 两个历史锚点（7e/7f 实测；7e 的那条今天用 `repro_g7e_arm.sh` 复现过）。
#: ⛔ 只作为**参考点**打印，不参与任何计算 —— 轴不是同一条（历史锚点不是等长填充）。
ANCHORS = ((182, 1.00, "7e（今日复现）"), (291, 0.20, "7f"))


def _per_task(path: Path) -> dict[str, float]:
    merged: dict[str, float] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        for t in row["report"]["per_task"]:
            merged[t["id"]] = float(t["score"])
    if not merged:
        raise SystemExit(f"❌ {path} 无 per_task 读数")
    return merged


def _arm_cells(ev: Path, arm: str, reps: int) -> list[dict[str, float]]:
    out = []
    for r in range(1, reps + 1):
        p = ev / f"{arm}_r{r}_runs.jsonl"
        if not p.exists():
            raise SystemExit(f"❌ 缺证据文件 {p}")
        out.append(_per_task(p))
    return out


def _delivery(ev: Path, facts: dict[str, str], reps: int
              ) -> tuple[bool, list[str]]:
    """R1 逐档：own-fact + H/P 等量（条数与字符数）+ E 空 + 条数与 7d 相同 + 完整性。"""
    from mw.memory import MemoryStore
    tasks = [json.loads(l) for l in HELDOUT.read_text().splitlines() if l.strip()]
    seeds = {a: MemoryStore(ev / f"seed_{a}.db") for a in ("H", "P", "E")}
    lines: list[str] = []
    ok = True
    for a in ("H", "P", "E"):
        n_seed = seeds[a].conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"]
        for r in range(1, reps + 1):
            fin = MemoryStore(ev / f"final_{a}_r{r}.db")
            n_fin = fin.conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"]
            good = n_seed == n_fin
            ok &= good
            lines.append(f"  {'✅' if good else '❌'} {a} r{r} facts "
                         f"种子={n_seed} 跑后={n_fin}")
            fin.close()
    for t in tasks:
        want = facts[t["id"]]
        got = {a: [f.text for f in seeds[a].recall(t["prompt"], k=6)] for a in ("H", "P", "E")}
        own = want in got["H"]
        n_ok = len(got["H"]) == len(got["P"])
        c_ok = sum(len(x) for x in got["H"]) == sum(len(x) for x in got["P"])
        cnt_ok = len(got["H"]) == BASE_COUNTS[t["id"]]     # 前置②：检索路径没变
        good = own and n_ok and c_ok and cnt_ok and len(got["E"]) == 0
        ok &= good
        lines.append(
            f"  {'✅' if good else '❌'} {t['id']:24s} H={len(got['H'])}条"
            f"（本条{'✅' if own else '❌'} 基准 {BASE_COUNTS[t['id']]}条"
            f"{'✅' if cnt_ok else '❌'}）P={len(got['P'])}条 E={len(got['E'])}条"
            f"  注入字符 H={sum(len(x) for x in got['H'])}"
            f" P={sum(len(x) for x in got['P'])}")
    for a in ("H", "P", "E"):
        seeds[a].close()
    return ok, lines


def _injected(ev: Path, facts: dict[str, str], tid: str) -> int:
    """观察对象实际被注入的字符数（= 它那一档的"邻域长度"换算到上下文里的长度）。"""
    from mw.memory import MemoryStore
    tasks = {json.loads(l)["id"]: json.loads(l) for l in
             HELDOUT.read_text().splitlines() if l.strip()}
    m = MemoryStore(ev / "seed_H.db")
    n = sum(len(f.text) for f in m.recall(tasks[tid]["prompt"], k=6))
    m.close()
    return n


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True, help="含 ev_<observer>_<level>/ 与 ev_E/ 的目录")
    p.add_argument("--repeats", type=int, default=2)
    args = p.parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    reps = args.repeats

    from build_g7d_seed import VARIANTS, NEIGH_LEVELS

    dirs = sorted(d for d in root.glob("ev_*") if d.is_dir())
    if not dirs:
        raise SystemExit(f"❌ {root} 下没有 ev_* 目录")
    # 档位目录名：ev_<o1|o2>_<l40|l60|l84|l120>
    cells: dict[str, dict[str, list[dict[str, float]]]] = {}
    ok_all = True
    print("=" * 74)
    print("R1 送达（逐档：own-fact + H/P 等量 + 条数==7d + 完整性）")
    print("=" * 74)
    for d in dirs:
        name = d.name[3:]
        if name == "E":
            continue
        variant = f"7g_{name}"
        if variant not in VARIANTS:
            raise SystemExit(f"❌ 目录 {d.name} 找不到对应变体 {variant}")
        print(f"-- 档位 {name} --")
        ok, lines = _delivery(d, VARIANTS[variant], reps)
        for l in lines:
            print(l)
        ok_all &= ok
        cells[name] = {"H": _arm_cells(d, "H", reps), "P": _arm_cells(d, "P", reps)}
    if not ok_all:
        print("❌ R1 不成立 ⇒ 处理未送达/被混杂/检索路径变了，整组作废（不许判读）")
        return 2

    e_dir = root / "ev_E"
    if not e_dir.is_dir():
        raise SystemExit(f"❌ 缺 E 臂目录 {e_dir}")
    e_cells = _arm_cells(e_dir, "E", reps)
    ids = sorted(e_cells[0])

    # ── R-noise：每格每题 repeat 极差 ──────────────────────────────────
    max_rng = 0.0
    for name, arms in cells.items():
        for arm, rs in arms.items():
            for tid in ids:
                vals = [r[tid] for r in rs]
                max_rng = max(max_rng, max(vals) - min(vals))
    for tid in ids:
        vals = [r[tid] for r in e_cells]
        max_rng = max(max_rng, max(vals) - min(vals))
    print(f"\n== R-noise ==  max_range={max_rng:.4f}（阈值 {THRESH_NOISE}，"
          f"每格 {reps} 次 repeat）")

    def mean_of(rs: list[dict[str, float]]) -> float:
        return sum(sum(r[t] for t in ids) for r in rs) / (len(ids) * len(rs))

    e_mean = mean_of(e_cells)
    print(f"== E 臂 ==  mean={e_mean:.4f}（空表；与档位无关）")

    # ── 逐题 × 逐档读数（**先出数据，后出判决**）───────────────────────
    print("\n== 逐档逐题（H / P，每次 repeat 均值）==")
    print(f"  {'题':26s}" + "".join(f"{n:>12s}" for n in sorted(cells)))
    for tid in ids:
        row = f"  {tid:26s}"
        for name in sorted(cells):
            h = sum(r[tid] for r in cells[name]["H"]) / reps
            pv = sum(r[tid] for r in cells[name]["P"]) / reps
            row += f"{h:.2f}/{pv:.2f}".rjust(12)
        print(row)

    # ── 两个观察对象的曲线（按注入字符排序）──────────────────────────
    def curve(obs: str, tid: str) -> list[tuple[int, float, str]]:
        pts = []
        for name in cells:
            if not name.startswith(obs):
                continue
            ev = root / f"ev_{name}"
            inj = _injected(ev, VARIANTS[f"7g_{name}"], tid)
            h = sum(r[tid] for r in cells[name]["H"]) / reps
            pts.append((inj, h, name))
        return sorted(pts)

    print("\n== O1 曲线（观察对象 `g7d2`，冻结 38 字；x = 它**实际被注入**的字符数）==")
    c1 = curve("o1", "g7d2_dedupe_keep")
    for inj, h, name in c1:
        print(f"  {name:12s} 注入={inj:3d} 字  H={h:.2f}")
    for inj, h, tag in ANCHORS:
        print(f"  [参考] 历史锚点 {tag:14s} 注入={inj:3d} 字  H={h:.2f}（不是本轴上的档位）")

    print("\n== O2 曲线（观察对象 `g7e2`，冻结 7f 的 82 字形式）==")
    c2 = curve("o2", "g7e2_truncate_tail")
    for inj, h, name in c2:
        print(f"  {name:12s} 注入={inj:3d} 字  H={h:.2f}")

    # ── R-noise：逐格逐题（指出**哪一格**不稳，不是一个总数）──────────
    print("\n== R-noise：逐格逐题 repeat 极差 ==")
    worst: list[tuple[str, str, list[float]]] = []
    for name, arms in sorted(cells.items()):
        for arm in ("H", "P"):
            for tid in ids:
                vals = [r[tid] for r in arms[arm]]
                if max(vals) - min(vals) > 0:
                    worst.append((name, f"{arm}/{tid}", vals))
    for tid in ids:
        vals = [r[tid] for r in e_cells]
        if max(vals) - min(vals) > 0:
            worst.append(("E", f"E/{tid}", vals))
    if not worst:
        print(f"  （全部为 0；{len(cells) * 2 * len(ids)} 格 × {reps} 次 repeat 逐位相同）")
    for name, tag, vals in worst:
        print(f"  ⚠️ {name:12s} {tag:26s} {vals}  极差={max(vals) - min(vals):.4f}")

    # ── 判据：R7 先行 → R-noise → P3 → P1/P2 ─────────────────────────
    print("\n== R7：每档 P vs E（等长骨架安慰剂）==")
    d_ps = {}
    for name in sorted(cells):
        p_mean = mean_of(cells[name]["P"])
        d_p = p_mean - e_mean
        d_ps[name] = d_p
        flag = "✅" if abs(d_p) < THRESH_PLACEBO else ("⚠️" if abs(d_p) < THRESH_R7 else "❌")
        print(f"  {flag} {name:12s} P={p_mean:.4f}  Δ_P={d_p:+.4f}")
    if any(abs(v) >= THRESH_R7 for v in d_ps.values()):
        print(f"❌ R7 不成立（某档 |Δ_P| ≥ {THRESH_R7}）⇒ 整组作废")
        return 3
    p_flat = all(abs(v) < THRESH_PLACEBO for v in d_ps.values())
    print(f"   P3（P 曲线平）：{'✅ 成立' if p_flat else '❌ 不成立（动的是长度不是内容）'}")

    steps = [abs(c1[i + 1][1] - c1[i][1]) for i in range(len(c1) - 1)]
    p1 = bool(steps) and max(steps) >= STEP_MIN
    p2 = bool(c2) and all(h == 1.0 for _, h, _ in c2)
    print(f"\n== P1 ==  O1 相邻档最大极差={max(steps) if steps else 0.0:.4f}"
          f"（台阶判据 ≥{STEP_MIN}）⇒ {'✅ 不平' if p1 else '❌ 平'}")
    print(f"== P2 ==  O2 端点 {'/'.join(f'{n}={h:.2f}' for _, h, n in c2)}"
          f" ⇒ {'✅ 平在 1.00' if p2 else '❌ 不是全 1.00'}")

    print("\n== 结局 ==")
    if max_rng > THRESH_NOISE:
        print(f"❌ R-noise={max_rng:.4f} > {THRESH_NOISE} ⇒ 按事前登记**整组作废**"
              f"（三种结局一个都不许选）")
        print(f"   不稳定的是：{[f'{n}/{t} {v}' for n, t, v in worst]}")
        print("   ⛔ 不许挑稳的那几档单独报 —— 那等于在作废的组里挑读数。")
        return 4
    if not p_flat:
        print("⛔ (作废) P 曲线不平 ⇒ 动的是长度不是内容，本轮不判读")
        return 5
    if p1 and p2:
        print("（a）O1 斜 + O2 平 ⇒ 邻域效应**局部**成立；7f 的『记 0』成立，\n"
              "     但 7f 里 `g7e2` 的采用可以**重新归因给它的文本**（其上下文无邻域）。\n"
              "     ⛔ 只允许写『在 1-条-上下文族上例子形式被采用』，不许升格为通道增益。")
    elif not p1 and p2:
        print("（b）O1 平 ⇒ 7f 的 0.80 **不可复现**，登记为未定位干扰；\n"
              "     7f 的『记 0』仍成立，但理由要换成『位移不可复现』。")
    elif p1 and not p2:
        print("（c）O1 斜 + O2 也斜 ⇒ 存在**非局部**通道 ⇒ 本装置单题级读数一律作废。")
    else:
        print("（?）O1 平 + O2 不平 ⇒ 不在预登记的三条里，登记为**未预期**，不得判读。")
        return 6
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
