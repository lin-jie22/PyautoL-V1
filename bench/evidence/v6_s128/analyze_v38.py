"""V38 分析器：修掉「把跨题累计值当增量」之后，除了计数器，别的东西会不会跟着变。

判据全部来自预注册（`bench/evidence/v6_s128/PREREG.md`，冻结于 2026-11-01 22:21）：

  D1  分数不许动：与 V37 `a0_r1` 逐格比（空库 / T=0 / 同 suite / 同 scorer / 同 limits）
  D2  计数器必须变成真值，且**三个来源互相对上**：
      `preds` 条数（逐条）｜proxy `max_tokens=200`（出网侧）｜`kernel_worldmodel.predictions`
  D3  层读数：⛔ 缺席不等于零（0 ⇒ 只许说「这条路径未触发」）
  D4  口径：`diff_except({"memory_seed"})` 意外差异 = 无；`limits` 逐键差异 = 空
  D5  两轮一致：两次独立空库运行的逐题分数与 `predictions` 相同

押注 `J_READONLY_FIX`：只改读数，不改行为。
推翻条件：① 任一格分数变 ② 三源不相等 ③ predictions 仍 34 ④ 两轮不一致。

⛔ 本脚本只打印，不写任何文件（纪律 32）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
sys.path.insert(0, str(ROOT))

from mw.bench import caliber_from_dict      # noqa: E402

W = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v38")
#: 对照：V37 的**空库**冷启动轮（同 suite / scorer / limits / T=0）
REF = Path("/tmp/v37")
REF_TAG = "a0_r1"
TAGS = ("r1", "r2")
TASKS = ["e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni"]
#: 真值：V37 从上表 6 题逐题累计差分算出来的预演条数（2,1,2,1,2,2）
TRUE_PRED = 10
OLD_VALUE = 34


def rows_of(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def run_of(base: Path, tag: str) -> dict | None:
    r = rows_of(base / f"bench_{tag}" / "runs.jsonl")
    return r[-1] if r else None


def scores(base: Path, tag: str) -> dict[str, float] | None:
    r = run_of(base, tag)
    if not r:
        return None
    return {str(t["id"]): float(t.get("score") or 0.0)
            for t in r["report"]["per_task"]}


def wm(base: Path, tag: str) -> dict:
    return (run_of(base, tag) or {}).get("kernel_worldmodel") or {}


def caliber(base: Path, tag: str):
    r = run_of(base, tag)
    return caliber_from_dict(r["caliber"]) if r else None


def pred_count(base: Path, tag: str) -> int:
    n = 0
    for d in rows_of(base / f"states_{tag}.jsonl"):
        n += len(d.get("preds") or [])
    return n


def window(base: Path, tag: str) -> tuple[float, float] | None:
    p = base / f"time_{tag}.txt"
    if not p.exists():
        return None
    parts = p.read_text(encoding="utf-8").replace("⏱", "").split()
    try:
        return float(parts[0]), float(parts[1])
    except (IndexError, ValueError):
        return None


def proxy_pred_calls(base: Path, tag: str) -> int | None:
    """出网侧的预演调用数（`max_tokens=200`，`worldmodel.py:398` 写死）。"""
    win = window(base, tag)
    if win is None:
        return None
    t0, t1 = win
    n = 0
    for r in rows_of(base / "proxy.jsonl"):
        tw = r.get("t_wall")
        if tw is None:
            continue
        if t0 - 1 <= float(tw) <= t1 + 1 and int(r.get("max_tokens") or 0) == 200:
            n += 1
    return n


def fmt(d: dict[str, float] | None) -> str:
    if d is None:
        return "（未跑）"
    return "  ".join(f"{t}={d.get(t, 0):.0f}" for t in TASKS)


def main() -> int:
    print("=" * 90)
    print("V38 判据（预注册 bench/evidence/v6_s128/PREREG.md，冻结于 2026-11-01 22:21）")
    print("=" * 90)
    print(f"  本轮工作目录 {W}")
    print(f"  对照（V37 空库冷启动）{REF} / {REF_TAG}")
    have = [t for t in TAGS if run_of(W, t)]
    print(f"  已跑完的轮：{have or '（无）'}")
    if not have:
        print("\n  ⛔ 没有读数。这不是任何一个判决，是「还没跑」。")
        return 0

    ref_s = scores(REF, REF_TAG)
    ref_wm = wm(REF, REF_TAG)
    ref_preds = pred_count(REF, REF_TAG)
    ref_proxy = proxy_pred_calls(REF, REF_TAG)
    print(f"\n  对照基线（V37 {REF_TAG}）：分数 {fmt(ref_s)}  Σ={sum(ref_s.values()) if ref_s else 'NA'}")
    print(f"                          predictions={ref_wm.get('predictions')} "
          f"preds 条数={ref_preds} proxy200={ref_proxy}")

    verdict: dict[str, bool] = {}

    # ── D1 分数不许动 ───────────────────────────────────────────────
    print("\n" + "-" * 90)
    print("D1 / 分数不许动：与 V37 空库冷启动轮**逐格**比（⛔ 只报 Σ 不合格）")
    print("-" * 90)
    d1_ok = True
    for tag in have:
        s = scores(W, tag)
        if s is None or ref_s is None:
            print(f"  {tag}: 缺分数")
            d1_ok = False
            continue
        diff = {t: (s.get(t, 0) - ref_s.get(t, 0)) for t in TASKS}
        moved = {t: v for t, v in diff.items() if v != 0}
        print(f"  {tag}: {fmt(s)}   Σ={sum(s.values()):.0f}")
        print(f"       逐格差 = {moved or '无（全 0）'}")
        if moved:
            d1_ok = False
    verdict["D1"] = d1_ok
    print(f"\n  ⇒ D1 {'✅ 一格未动' if d1_ok else '❌ 有格子动了 ⇒ 预注册 §一 的形态 (b)，必须停下查'}")

    # ── D2 三源对账 ─────────────────────────────────────────────────
    print("\n" + "-" * 90)
    print("D2 / 计数器必须变成真值：`preds` 条数（逐条）｜proxy 200 型（出网侧）｜累加器")
    print("-" * 90)
    d2_ok = True
    for tag in have:
        kw = wm(W, tag)
        got = int(kw.get("predictions") or 0)
        p = pred_count(W, tag)
        q = proxy_pred_calls(W, tag)
        same = (got == p == q)
        print(f"  {tag}: predictions={got:3d}  preds={p:3d}  proxy200={q}  "
              f"{'✅ 三源相同' if same else '❌ 不一致'}")
        print(f"       scored={kw.get('scored')} hits={kw.get('hits')} "
              f"confusion={kw.get('confusion')} vetoed={kw.get('vetoed')} "
              f"refused={kw.get('veto_refused_answer')}")
        if not same or got == OLD_VALUE:
            d2_ok = False
    verdict["D2"] = d2_ok
    print(f"\n  ⇒ D2 {'✅ 累加器 = 真值 ' + str(TRUE_PRED) if d2_ok else '❌ 还没对上'}"
          f"（修复前是 {OLD_VALUE} = 6 题累计值之和 2+3+5+6+8+10）")

    # ── D3 层读数 ───────────────────────────────────────────────────
    print("\n" + "-" * 90)
    print("D3 / 层读数：⛔ 缺席 ≠ 零（0 只许说「这条路径在本装置上未触发」）")
    print("-" * 90)
    for tag in have:
        kw = wm(W, tag)
        print(f"  {tag}: layer_recalled={kw.get('layer_recalled')} "
              f"layer_abstract={kw.get('layer_abstract')}")
    lay = [int(wm(W, t).get("layer_recalled") or 0) for t in have]
    verdict["D3"] = any(v > 0 for v in lay)
    print(f"\n  ⇒ D3 {'✅ 有读数（修好）' if verdict['D3'] else '⚠️ 全 0 ⇒ **未触发**，不许写成「修好了」'}")

    # ── D4 口径 ─────────────────────────────────────────────────────
    print("\n" + "-" * 90)
    print("D4 / 口径：内核零改动 ⇒ 除 `memory_seed` 外应完全相同，`limits` 逐键零差异")
    print("-" * 90)
    d4_ok = True
    pairs = [(t, REF_TAG) for t in have]
    if len(have) >= 2:
        pairs.append((have[0], have[1]))
    for a, b in pairs:
        ca, cb = caliber(W, a), (caliber(REF, b) if a != b and b == REF_TAG else caliber(W, b))
        if ca is None or cb is None:
            print(f"  {a} vs {b}: 缺口径")
            d4_ok = False
            continue
        d = ca.diff_except(cb, {"memory_seed"})
        #: ⛔ `diff_except` 返回的是**元组** `(差异列表, 别的东西)` ——
        #:    `if d:` 恒为真（非空元组），会把每一对都判成"有意外差异"。
        #:    第一版就这么写的：口径明明 `([], [])`，D4 却报未通过。
        #:    ⇒ 判据必须取 `d[0]`（差异列表本身），这是"聚合方式会骗人"的又一例。
        d_list = list(d[0]) if isinstance(d, tuple) else list(d or [])
        la = dict(ca.limits.__dict__ if hasattr(ca.limits, "__dict__") else {})
        lb = dict(cb.limits.__dict__ if hasattr(cb.limits, "__dict__") else {})
        lim_diff = sorted(k for k in set(la) | set(lb) if la.get(k) != lb.get(k))
        print(f"  {a} vs {b}: 意外差异={d_list or '无'}  limits 差异={lim_diff or '无'}")
        if d_list or lim_diff:
            d4_ok = False
    verdict["D4"] = d4_ok

    # ── D5 两轮一致 ─────────────────────────────────────────────────
    print("\n" + "-" * 90)
    print("D5 / 两轮一致：两次**独立空库**运行的分数与计数器必须相同（T=0 确定性重放）")
    print("-" * 90)
    d5_ok = None
    if len(have) >= 2:
        s1, s2 = scores(W, have[0]), scores(W, have[1])
        p1, p2 = int(wm(W, have[0]).get("predictions") or 0), int(wm(W, have[1]).get("predictions") or 0)
        d5_ok = (s1 == s2) and (p1 == p2)
        print(f"  {have[0]} vs {have[1]}: 分数同={s1 == s2}  predictions {p1} vs {p2}")
    else:
        print("  只有一轮 ⇒ 不判定（⛔ 不许用一轮冒充确定性）")
    verdict["D5"] = bool(d5_ok)

    # ── 押注 ────────────────────────────────────────────────────────
    print("\n" + "=" * 90)
    print("押注 `J_READONLY_FIX`：这次修复只改读数，不改行为")
    print("=" * 90)
    c1 = verdict["D1"]
    c2 = verdict["D2"]
    c3 = all(int(wm(W, t).get("predictions") or 0) != OLD_VALUE for t in have)
    c4 = verdict["D5"]
    print(f"  ① 分数一格未动            : {'✅' if c1 else '❌'}  ⇒ 推翻（形态 b）")
    print(f"  ② 三源相等（preds/proxy/acc）: {'✅' if c2 else '❌'}  ⇒ 推翻")
    print(f"  ③ predictions ≠ {OLD_VALUE}（真的改到了）: {'✅' if c3 else '❌'}  ⇒ 推翻")
    print(f"  ④ 两轮一致                : {'✅' if c4 else '❌'}  ⇒ 推翻")
    #: ⛔ D4 不在押注的推翻条件里（预注册 §四只有 4 条），但它是**对照口径的前提**：
    #:    不通过时必须显式写在这里，不许让它躲在"押对"的后面。
    print(f"  口径 D4（前提，不在推翻条件里）: {'✅ 无意外差异' if verdict['D4'] else '❌ 未通过 —— 见上表，如实登记'}")
    won = c1 and c2 and c3 and c4
    print(f"\n  裁决：{'⭐ 押对 —— 修复只改读数，一格分数没动' if won else '⛔ 押错（按上表指出是哪一条推翻的）'}")
    print("\n⛔ 范围限制（预注册 §五）：34 → 10 是**同一个数的两种记法**，不是性能提升；")
    print("   本轮修的是尺子的读数，不是累积循环本身。")

    # ── 补充量（预注册之外；⛔ 不是判据，只许记录，不许据此改裁决）──────────
    print("\n" + "-" * 90)
    print("[补充 / 非判据] `hit_rate` 会不会跟着变（预注册没写，跑完才发现该记）")
    print("-" * 90)
    ref_sc = int(ref_wm.get("scored") or 0)
    ref_hr = (int(ref_wm.get("hits") or 0) / ref_sc) if ref_sc else None
    for tag in have:
        kw = wm(W, tag)
        sc = int(kw.get("scored") or 0)
        hr = (int(kw.get("hits") or 0) / sc) if sc else None
        print(f"  {tag}: hits/scored = {kw.get('hits')}/{sc} ⇒ {hr}")
    print(f"  V37 {REF_TAG}（修复前）: hits/scored = {ref_wm.get('hits')}/{ref_sc} ⇒ "
          f"{round(ref_hr, 3) if ref_hr is not None else 'None'}")
    print("  ⇒ ⛔ V37 §103.4② 那句「`hit_rate`（比值）恰好约掉」是**错的**：")
    print("     比值也变了。原因：逐段累计的 `hitsᵢ/scoredᵢ` 不是分题值，")
    print("     Σhits/Σscored ≠ hits总/scored总 —— 比值的加权方式同样被污染。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
