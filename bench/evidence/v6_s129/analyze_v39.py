"""V39 分析器：`max_candidates` 这个旋钮到底值不值。

判据全部来自预注册（`bench/evidence/v6_s129/PREREG.md`，冻结于 2026-11-02 00:20）：

  D1  解析失败（**主判据**）：`kernel_planner.fail_total / calls`，两臂对比。
      ⛔ 分母必须一起给 —— 只报 `fail_total=7` 无法判断是 7/7 还是 7/60。
  D2  候选宽度：日志里 `候选=N` 的真实分布。**这是本轮的判决对象本身。**
      ⛔ 它来自 stdout 日志（`core.py` 的 `候选={len(cands)}`），不是 `state.n`
      —— `n` 只是**上限**，V37 已实测「n 有 44 次 ≥2，候选 0 次 ≥2」。
  D3  净效应：**逐格**置换表 + Σ（⛔ 只报 Σ 不合格，纪律 30）。
  D4  口径：`diff_except({"memory_seed","limits"})` 必须为空，且 `limits` 逐键差异
      **恰好只有** `max_candidates`（那是自变量，不是意外）。
  D5  请求身份：两臂 `req_sha` 的差异率。差异应当来自 `请给出 N 个` 那一句 ——
      若两臂 `req_sha` **全同**，说明旋钮根本没进 prompt ⇒ 整个实验作废。

⛔ 本脚本只打印，不写任何文件（纪律 34）。
⛔ 断段声明：`max_candidates` ∈ `Caliber.limits` ⇒ 两臂 sig 必然不同；本轮的 Σ
   **不许**与 V37/V38 的任何 Σ 相减。
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
sys.path.insert(0, str(ROOT))

from mw.bench import caliber_from_dict      # noqa: E402

W = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v39")
TAGS = ("a1_r1", "a3_r1", "a3_r2", "a1_r2")
TASKS = ["e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni"]
#: 配对：第 1 轮 a1→a3，第 2 轮 a3→a1（顺序交叉，对冲时段漂移）
PAIRS = [("a1_r1", "a3_r1"), ("a3_r2", "a1_r2")]
#: 轮次方向：同一个臂跨轮比（累积有没有让它变）—— 与两臂比（宽度有没有用）分开
ROUNDS = [("a1_r1", "a1_r2"), ("a3_r1", "a3_r2")]


def runs(tag: str) -> dict | None:
    p = W / f"bench_{tag}/runs.jsonl"
    if not p.exists():
        return None
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    return rows[-1] if rows else None


def scores(tag: str) -> dict[str, float] | None:
    r = runs(tag)
    if not r:
        return None
    return {str(t["id"]): float(t.get("score") or 0.0)
            for t in r["report"]["per_task"]}


def caliber(tag: str):
    r = runs(tag)
    return caliber_from_dict(r["caliber"]) if r else None


def kpl(tag: str) -> dict:
    """规划器读数（V39 新增落盘）。**分母与分项一起取**。"""
    r = runs(tag)
    return (r or {}).get("kernel_planner") or {}


def wm(tag: str) -> dict:
    r = runs(tag)
    return (r or {}).get("kernel_worldmodel") or {}


def log_text(tag: str) -> str:
    p = W / f"run_{tag}.log"
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def widths(tag: str) -> Counter:
    """真实候选数分布。来源是日志里的 `候选={len(cands)}`。

    ⛔ 不许用 `state.n` 代替：`n` 是**上限**（`n = min(budget.candidates, cand_left)`），
    V37 实测 `n` 分布 `3×24 / 2×20 / 1×32` 而真实候选 **76/76 恒为 1**。
    拿 `n` 当候选数会把"要求了几个"读成"给了几个"。
    """
    return Counter(int(x) for x in re.findall(r"候选=(\d+)", log_text(tag)))


def window(tag: str) -> tuple[float, float] | None:
    p = W / f"time_{tag}.txt"
    if not p.exists():
        return None
    m = re.findall(r"(\d{9,})", p.read_text())
    return (float(m[0]), float(m[1])) if len(m) >= 2 else None


def req_shas(tag: str) -> list[str]:
    p = W / "proxy.jsonl"
    if not p.exists():
        return []
    w = window(tag)
    if not w:
        return []
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    lo, hi = w
    return [str(r.get("req_sha") or "") for r in rows if lo <= float(r.get("t_wall") or 0) <= hi]


def main() -> int:
    print("=" * 96)
    print("V39 —— `max_candidates` 旋钮：A1(窄, =1) vs A3(宽, =3)")
    print("=" * 96)

    # ── D1 解析失败（主判据）────────────────────────────────────────────
    print("\n【D1】规划器解析失败（主判据；分母一起给）")
    print(f"{'臂':10} {'调用':>5} {'失败':>5} {'率':>6} {'json':>5} {'截断':>5} {'无工具':>6} {'空计划':>6} {'补救':>8}")
    d1_ok = True
    for t in TAGS:
        k = kpl(t)
        if not k:
            print(f"{t:10} ⛔ 没有 kernel_planner ⇒ D1 无从判定")
            d1_ok = False
            continue
        print(f"{t:10} {k.get('calls','-'):>5} {k.get('fail_total','-'):>5} "
              f"{str(k.get('fail_rate')):>6} {k.get('json_fail','-'):>5} "
              f"{k.get('truncated','-'):>5} {k.get('no_tool_fit','-'):>6} "
              f"{k.get('empty_plan','-'):>6} "
              f"{str(k.get('answer_retry_ok','-')) + '/' + str(k.get('answer_retry_fail','-')):>8}")

    # ── D2 候选宽度（判决对象本身）──────────────────────────────────────
    print("\n【D2】真实候选数分布（`候选={len(cands)}`，日志原值）")
    d2 = {}
    for t in TAGS:
        c = widths(t)
        d2[t] = c
        total = sum(c.values())
        ge2 = sum(v for k, v in c.items() if k >= 2)
        print(f"  {t:10} 分布={dict(sorted(c.items()))}  通道行={total}  "
              f"候选≥2 的次数={ge2}  {'✅' if ge2 else '· 恒为 1'}")

    # ── D3 净效应（逐格）────────────────────────────────────────────────
    print("\n【D3】逐格分数（⛔ 每对都要看置换，不能只看 Σ）")
    d3_ok = True
    for a, b in PAIRS + ROUNDS:
        sa, sb = scores(a), scores(b)
        if sa is None or sb is None:
            print(f"  {a} vs {b}: ⛔ 缺读数")
            d3_ok = False
            continue
        diff = [t for t in TASKS if abs(sa.get(t, 0) - sb.get(t, 0)) > 1e-9]
        up = [t for t in diff if sb.get(t, 0) > sa.get(t, 0)]
        down = [t for t in diff if sb.get(t, 0) < sa.get(t, 0)]
        kind = "两臂" if (a, b) in PAIRS else "跨轮"
        print(f"  [{kind}] {a} vs {b}: Σ {sum(sa.values()):.0f} → {sum(sb.values()):.0f}"
              f"  Δ={sum(sb.values()) - sum(sa.values()):+.0f}"
              f"  | 逐格差异 {len(diff)} 处 升={up} 降={down}")
        print(f"           {a}: " + " ".join(f"{t}={sa.get(t, 0):.2f}" for t in TASKS))
        print(f"           {b}: " + " ".join(f"{t}={sb.get(t, 0):.2f}" for t in TASKS))

    # ── D4 口径 ─────────────────────────────────────────────────────────
    print("\n【D4】口径：除了 `max_candidates`，两臂必须逐字段相同")
    d4_ok = True
    for a, b in PAIRS + ROUNDS:
        ca, cb = caliber(a), caliber(b)
        if ca is None or cb is None:
            print(f"  {a} vs {b}: ⛔ 缺口径")
            d4_ok = False
            continue
        d = ca.diff_except(cb, {"memory_seed", "limits"})
        # ⚠️ `diff_except` 返回**元组** ⇒ 必须取 `d[0]`。
        #    V38 自踩过：写 `if d:` 恒真（非空元组），口径干净却把每一对判成未通过。
        unexpected = d[0] if isinstance(d, tuple) else d
        la = dict(ca.limits.__dict__ if hasattr(ca.limits, "__dict__") else ca.limits)
        lb = dict(cb.limits.__dict__ if hasattr(cb.limits, "__dict__") else cb.limits)
        lim_diff = sorted(k for k in set(la) | set(lb) if la.get(k) != lb.get(k))
        print(f"  {a} vs {b}: 意外差异={unexpected or '无'}  limits 差异={lim_diff or '无'}")
        if unexpected:
            d4_ok = False
    print("  ⚠️ 两臂之间 `limits` 差异**恰好只有 `max_candidates`** 才是对的（那是自变量）；")
    print("     同一臂跨轮之间 `limits` 差异必须为**无**（跨轮只许 `memory_seed` 不同）。")

    # ── D5 请求身份 ─────────────────────────────────────────────────────
    print("\n【D5】两臂的请求身份：差异率必须 > 0（旋钮要真的进了 prompt）")
    d5_ok = False
    for a, b in PAIRS:
        ra, rb = req_shas(a), req_shas(b)
        if not ra or not rb:
            print(f"  {a} vs {b}: ⛔ 没有 proxy 台账或时间窗")
            continue
        n = min(len(ra), len(rb))
        same = sum(1 for i in range(n) if ra[i] == rb[i])
        print(f"  {a} vs {b}: 请求 {len(ra)}/{len(rb)} 条，逐条相同 {same}/{n}"
              f" ⇒ 差异率 {(n - same) / n:.1%}" if n else "  空")
        if n:
            d5_ok = True

    # ── 押注 ────────────────────────────────────────────────────────────
    print("\n" + "=" * 96)
    print("押注裁决")
    print("=" * 96)
    k1, k3 = kpl("a1_r1"), kpl("a3_r1")
    r1 = k1.get("fail_rate")
    r3 = k3.get("fail_rate")
    h1 = None if (r1 is None or r3 is None) else (r3 >= r1)
    print(f"H1 `J_WIDTH_HARMS`：宽臂的解析失败率 ≥ 窄臂")
    print(f"   窄臂 fail_rate={r1}  宽臂 fail_rate={r3}  ⇒ "
          f"{'✅ 押对' if h1 else '⛔ 押错' if h1 is not None else '未判定'}")

    ge2_a3 = [sum(v for k, v in d2[t].items() if k >= 2) for t in ("a3_r1", "a3_r2")]
    h2 = all(x == 0 for x in ge2_a3)
    print(f"H2 `J_WIDTH_NEVER_DELIVERS`：宽臂的候选数仍**恒为 1**（从未 ≥2）")
    print(f"   a3_r1/a3_r2 的 候选≥2 次数={ge2_a3} ⇒ "
          f"{'✅ 押对 —— 宽度没兑现' if h2 else '⛔ 押错 —— 宽度真的兑现了'}")

    print("\n⛔ 范围限制（预注册 §五）：")
    print("   · 本轮的判决对象是**旋钮**，不是累积循环。分数不动**不是**失败。")
    print("   · `max_candidates` ∈ `Caliber.limits` ⇒ 两臂 sig 必不同，Σ 不可与 V37/V38 相减。")
    print("   · 两臂各只跑 2 轮 ⇒ 跨轮差的噪声下限只有 1 格，⛔ 不许把 1 格当增益。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
