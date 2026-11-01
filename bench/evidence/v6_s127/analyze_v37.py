"""V37 分析器：把 `pred` 接进决策链之后，分数会往哪边走。

判据全部来自预注册（`bench/evidence/v6_s127/PREREG.md`，冻结于 2026-11-01 21:05）：

  D1  机制可达性：A0 臂（veto 关，纯观测）里 `ok=False 且 trust=True` 的条数 K
      · `K == 0` ⇒ J_NO_INPUT（机制没有输入，D3/D4 不判定）
      · 与 `kernel_worldmodel.confusion` 的 `fail_*` 交叉验证（两处必须一致）
  D2  通电：A1 臂 `vetoed > 0`
  D3  净效应：**逐格**置换表（⛔ 只报 Σ 不合格）
  D4  代价：`degraded` / 步数 / 墙钟 / `scored` 与 `vetoed` 的缺口
  D5  口径：`diff_except({"memory_seed","limits"})` 空，且 `limits` 逐键 diff
      必须恰好是 `{"foresight_veto"}` 一项

⛔ 本脚本只打印，不写任何文件（纪律 34）。
⛔ 断段声明：`Limits` 新增字段 ⇒ `Caliber.limits` 变 ⇒ 本轮的 Σ **不许**与
    V36 及之前的任何 Σ 相减。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
sys.path.insert(0, str(ROOT))

from mw.bench import caliber_from_dict      # noqa: E402

W = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v37")
TAGS = ("a0_r1", "a1_r1", "a1_r2", "a0_r2")
TASKS = ["e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni"]
#: 配对：第 1 轮 a0→a1，第 2 轮 a1→a0（顺序交叉，对冲时段漂移）
PAIRS = [("a0_r1", "a1_r1"), ("a1_r2", "a0_r2")]


def states(tag: str) -> list[dict]:
    p = W / f"states_{tag}.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


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


def wm(tag: str) -> dict:
    r = runs(tag)
    return (r or {}).get("kernel_worldmodel") or {}


def pred_rows(tag: str) -> list[dict]:
    out: list[dict] = []
    for d in states(tag):
        for p in (d.get("preds") or []):
            out.append({**p, "task": d.get("task"), "it": d.get("it")})
    return out


def log_count(tag: str, pattern: str) -> int:
    p = W / f"run_{tag}.log"
    if not p.exists():
        return -1
    return len(re.findall(pattern, p.read_text(encoding="utf-8", errors="replace")))


def main() -> int:
    print("=" * 88)
    print("V37 判据（预注册 bench/evidence/v6_s127/PREREG.md，冻结于 2026-11-01 21:05）")
    print("=" * 88)
    print(f"  工作目录 {W}")
    have = [t for t in TAGS if states(t) or runs(t)]
    print(f"  已跑完的臂：{have or '（无）'}")
    if not have:
        print("\n  ⛔ 没有读数。这不是 J0，是「还没跑」。")
        return 0

    verdict: dict[str, bool | None] = {}

    # ── D1 机制可达性 ────────────────────────────────────────────────
    print("\n" + "-" * 88)
    print("D1 / 机制可达性：预演到底说没说「会失败」（A0 臂纯观测，veto 关）")
    print("-" * 88)
    #: ⚠️ 判据口径：预注册写的是「**A0 臂**」⇒ 是**两轮合计**，不是只看 r1。
    #:    （实现时先只算了 r1，跑批中途发现 r1 是冷启动、r2 才带累积 ——
    #:      这正是差别所在，所以两轮必须**分列**再合计，缺一列就会把
    #:      「累积之后才学会说会失败」读成「从不说会失败」。）
    K_total = 0
    broke = None
    for tag in ("a0_r1", "a0_r2"):
        pr = pred_rows(tag)
        if not pr:
            print(f"  {tag}: 没有 preds（未跑或快通道）")
            continue
        K = sum(1 for p in pr if p.get("ok") is False and p.get("trust"))
        fail_any = sum(1 for p in pr if p.get("ok") is False)
        trust = sum(1 for p in pr if p.get("trust"))
        confs = [p.get("conf") for p in pr if p.get("ok") is False]
        c = wm(tag).get("confusion") or {}
        cm_fail = int(c.get("fail_ok", 0)) + int(c.get("fail_fail", 0))
        #: ⛔ `kernel_worldmodel` 的每个计数都被 `_kwm_absorb` **重复累加**过
        #:    （`WorldModel` 跨题共享，`stats()` 返回累计值，absorb 却当增量加）。
        #:    实测：a0_r1 的 `predictions=34`，而 proxy 的 `max_tokens=200` 只有 10 条、
        #:    `preds` 也只有 10 条 ⇒ 34 = 2+3+5+6+8+10（六题累计值之和）。
        #:    ⇒ 本节**只用 `preds`（逐条，不经 `_kwm`）**下判据；
        #:      `confusion` 只用于「方向是否一致」的旁证，⛔ 不比大小。
        print(f"  {tag}: 预演条目={len(pr):3d} trustworthy={trust:3d} "
              f"ok=False(全部)={fail_any:3d} **ok=False 且 trustworthy={K:3d}**")
        print(f"         confusion 里 fail_*={cm_fail:3d} "
              f"（{c}）  ⚠️ 重复计数，只作方向旁证")
        print(f"         kernel_worldmodel.predictions={int(wm(tag).get('predictions') or 0)} "
              f"⚠️ 同上，**真实预演数看 preds 条数 = {len(pr)}**")
        if confs:
            print(f"         ok=False 的置信度 = {sorted(confs)}")
        K_total += K
        if K and broke is None:
            broke = tag
    verdict["D1_K"] = K_total
    if K_total == 0:
        print("\n  ⇒ **J_NO_INPUT**：A0 臂两轮里 `ok=False 且 trust=True` 一次都没有。")
        print("     机制没有输入 —— 这不是「无效」，是「这套 6 题下模型从不说会失败」。")
        print("     ⛔ D3/D4 不判定。")
    else:
        print(f"\n  ⇒ K = {K_total}（首次出现于 {broke}）⇒ 载荷**能**到达押注的格子。")

    # ── D2 通电 ──────────────────────────────────────────────────────
    print("\n" + "-" * 88)
    print("D2 / 通电：A1 臂的预演否决真的发生了吗")
    print("-" * 88)
    veto_total = 0
    for tag in ("a1_r1", "a1_r2"):
        pr = pred_rows(tag)
        s = wm(tag)
        if not pr and not s:
            print(f"  {tag}: 没有读数（未跑）")
            continue
        #: ⛔ 判据只用两个**不经 `_kwm`** 的来源：
        #:    ① `preds` 里 `veto=True` 的条数（逐条 append）；
        #:    ② 日志里的否决行（runner 落的文本产物）。
        #:    `kernel_worldmodel.vetoed` 被重复累加过 ⇒ **不许**当判据。
        n_v = sum(1 for p in pr if p.get("veto"))
        n_f = sum(1 for p in pr if p.get("ok") is False)
        n_log = log_count(tag, "预演否决")
        print(f"  {tag}: preds 条目={len(pr):3d} ok=False={n_f:3d} **veto=True={n_v:3d}**  "
              f"日志否决行={n_log:2d}")
        print(f"         ⚠️ kernel_worldmodel.vetoed={s.get('vetoed')} "
              f"veto_refused_answer={s.get('veto_refused_answer')}（重复计数，不作判据）")
        veto_total += n_v
        if tag == "a1_r1":
            verdict["D2"] = bool(n_v > 0)
    print(f"\n  A1 两轮 veto 合计 = {veto_total}")

    # ── D3 逐格分数 ─────────────────────────────────────────────────
    print("\n" + "-" * 88)
    print("D3 / 净效应（⛔ 逐格，不许只报 Σ）")
    print("-" * 88)
    sc = {t: scores(t) for t in TAGS}
    hdr = "  ".join(f"{t:>7s}" for t in TAGS)
    print(f"    {'题':<8s} {hdr}")
    for tk in TASKS:
        vals = []
        for t in TAGS:
            v = sc[t].get(tk) if sc[t] else None
            vals.append("—" if v is None else f"{v * 16:.0f}")
        print(f"    {tk:<8s} " + "  ".join(f"{x:>7s}" for x in vals))
    tots = {}
    for t in TAGS:
        if sc[t]:
            tots[t] = sum(sc[t].values()) * 16
    print(f"    {'Σ/96':<8s} " + "  ".join(f"{tots.get(t, 0):>7.0f}" for t in TAGS))

    print("\n  逐格置换表（左 = 先跑的臂，右 = 后跑的臂；只列变了的格）")
    for a, b in PAIRS:
        if not (sc.get(a) and sc.get(b)):
            continue
        print(f"\n    {a} vs {b}")
        diff = []
        for tk in TASKS:
            x, y = sc[a][tk] * 16, sc[b][tk] * 16
            if x != y:
                diff.append(f"{tk} {x:.0f}→{y:.0f} (Δ{y - x:+.0f})")
        print("      变了：" + ("；".join(diff) if diff else "**一格没动**"))
        print(f"      Σ {tots[a]:.0f} → {tots[b]:.0f} (Δ{tots[b] - tots[a]:+.0f})")
    if len(PAIRS) == 2 and all(sc.get(x) and sc.get(y) for x, y in PAIRS):
        a0 = (tots.get("a0_r1", 0) + tots.get("a0_r2", 0)) / 2
        a1 = (tots.get("a1_r1", 0) + tots.get("a1_r2", 0)) / 2
        print(f"\n  两轮平均：A0={a0:.1f}/96  A1={a1:.1f}/96  Δ={a1 - a0:+.1f}")
        #: ⛔ 预注册 §五 写的是「A1 的 Σ **≥** A0 ⇒ 押错」⇒ Δ=0 也是**押错**。
        #:    （第一版这里写的是 `a1 <= a0`，那会把 Δ=0 判成"押对" ——
        #:      正是纪律 30 说的"聚合方式会骗人"：相等与变差被折成同一个真值。）
        verdict["D3_A1_lt_A0"] = (a1 < a0)
        verdict["D3_delta"] = a1 - a0

    # ── D4 代价 ──────────────────────────────────────────────────────
    print("\n" + "-" * 88)
    print("D4 / 代价：否决之后还剩什么东西（degraded / 步数 / 墙钟 / 未检验的预演）")
    print("-" * 88)
    for tag in TAGS:
        r = runs(tag)
        if not r:
            continue
        s = wm(tag)
        co = r.get("kernel_closeout") or {}
        deg = log_count(tag, r"合法 JSON 但没有可用候选")
        pr = pred_rows(tag)
        n_v = sum(1 for p in pr if p.get("veto"))
        el = [float(t.get("elapsed_s") or 0) for t in r["report"]["per_task"]]
        print(f"  {tag}: 降级提示={deg:2d}  收尾因由={co.get('by_reason')}  "
              f"总墙钟={sum(el):.0f}s  单题最长={max(el) if el else 0:.0f}s")
        #: 「未检验的预演」= 被否决的那些（没执行 ⇒ 永远拿不到 `result.ok`）。
        #: ⛔ 用 `preds` 逐条数；`predictions - scored` 两个数都被重复累加，差值不可信。
        print(f"         预演条目={len(pr)} 其中否决={n_v} ⇒ **未检验 = {n_v}**"
              f"（⚠️ kernel_worldmodel: pred={s.get('predictions')} scored={s.get('scored')}，重复计数不作判据）")
        bad = [(t["id"], t["why"]) for t in r["report"]["per_task"] if t.get("why")]
        for tid, w in bad[:6]:
            print(f"         · {tid}: {w[:80]}")

    # ── D5 口径 ──────────────────────────────────────────────────────
    print("\n" + "-" * 88)
    print("D5 / 口径：diff_except({\"memory_seed\",\"limits\"}) 必须空；limits 逐键只许差 foresight_veto")
    print("-" * 88)
    cal = {t: caliber(t) for t in TAGS}
    for a, b in [("a0_r1", "a1_r1"), ("a1_r2", "a0_r2"), ("a0_r1", "a0_r2"), ("a1_r1", "a1_r2")]:
        if not (cal.get(a) and cal.get(b)):
            continue
        unexpected, declared = cal[a].diff_except(cal[b], {"memory_seed", "limits"})
        la = dict(cal[a].limits or {})
        lb = dict(cal[b].limits or {})
        ldiff = sorted(k for k in set(la) | set(lb) if la.get(k) != lb.get(k))
        print(f"  {a} vs {b}: 意外差异={unexpected or '无'}  已声明={declared or '无'}")
        print(f"           limits 逐键差异={ldiff}")
        if (a, b) == ("a0_r1", "a1_r1"):
            verdict["D5"] = (not unexpected) and ldiff == ["foresight_veto"]

    # ── 结论 ─────────────────────────────────────────────────────────
    print("\n" + "=" * 88)
    for k in ("D2", "D3_A1_lt_A0", "D5"):
        if k in verdict:
            print(f"  {k:14s} = {verdict[k]}")
    print("=" * 88)
    print("\n  押注 J_VETO_COSTLY 判读（预注册 §五：A1 的 Σ **≥** A0 ⇒ 押错）：")
    d = verdict.get("D3_delta")
    fired = veto_total > 0
    if d is None:
        print("    ⚠️ 数据不足，不下判决。")
    elif d < 0:
        print(f"    ✅ 押对（Δ={d:+.1f} < 0）—— 但要先看 D4：那几格是不是「降级交付」造成的。")
    elif not fired:
        print(f"    ❌ **押错**（Δ={d:+.1f} ≥ 0），而且**押注的前提本身没成立**：")
        print(f"       veto 合计 = {veto_total} ⇒ 机制一次都没触发，两臂是同一个系统。")
        print("       ⇒ Δ=0 不是「机制不伤」，是「机制没发生」。")
        print("       ⇒ 这个押注在逻辑上**悬空**：它赌的是一件从未发生的事。")
        print("          按纪律「押错就认」，仍判**押错**，不判「无用」。")
    else:
        print(f"    ❌ **押错**（Δ={d:+.1f} ≥ 0，且 veto 确实触发过）—— "
              "按预注册 §五，方向该改成「候选数为什么恒为 1」。")
    print("\n" + "=" * 88)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
