"""V173 STAGE0 —— ⛔ 不改仓库代码，只量。

三件事，全部零模型零网络：

A. **`done` 单调到底挡住了谁**（§240.7 要求的「先量」）
   在真账本 + 真套件上，比三种 `done` 语义下 `propose()` 的差集：
     hist  = 曾经 MET（出厂实现）
     naive = 最后一次判定是 MET（§240.7 押的形式）
     read  = 最后一次 MET 且该行记下的 criterion 在当前读数上仍成立（本轮想做的）
B. **RAISE 这一类目标能不能判成 MET**（读代码读出来的怀疑，先穷举取证）
   均分取 1.0 ⟺ 每个分数都是 1.0（因为 score ≤ 1.0）；
   而 RAISE 只在均分 < 1.0 时提出 ⇒ 「可提出」与「可判 MET」可能是空交集。
C. **部署读数**：逐格 n_obs / 均分 / 会不会被提出 RAISE / 那条 RAISE 有没有可能达成。

⚠️ 装置自报（纪律 65）：本文件 `import mw.goals` 取的是**改动前**的实现；
`done_naive` / `done_read` 是文件内的**本地复刻**，因为改动前仓库里只有 hist。
主实验（`v173.py`）会逐条核对出厂函数与复刻是否一致。
"""
from __future__ import annotations

import itertools
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT))

from mw import goals as G  # noqa: E402

IDS = None
OUT: dict = {}


def _last_rows(rows):
    last = {}
    for r in rows:
        if isinstance(r, dict) and r.get("gid") and r.get("verdict") in ("MET", "NOT_MET"):
            last[str(r["gid"])] = r
    return last


def done_hist(rows):
    return G.done_gids(rows)


def done_naive(rows):
    return [g for g, r in _last_rows(rows).items() if r["verdict"] == "MET"]


def _meets(criterion, st) -> bool:
    for metric, op, want in criterion:
        got = st.get(metric)
        if got is None:
            return False
        if op == ">=" and not (got >= want):
            return False
        if op == "<=" and not (got <= want):
            return False
    return True


def done_read(rows, obs):
    out = []
    for gid, r in _last_rows(rows).items():
        if r["verdict"] != "MET":
            continue
        crit = r.get("criterion") or ()
        if not crit:                      # 没有判据 ⇒ 不算 done（宁可见，不可藏）
            continue
        st = G.stats(obs, str(r.get("cell") or ""))
        if _meets(crit, st):
            out.append(gid)
    return out


def load_env():
    from mw import config as cfg
    p = cfg.Paths()
    return p


def section(t):
    print()
    print("=" * 74)
    print(t)
    print("=" * 74)


# ---------------------------------------------------------------------------
# B. RAISE 能不能判成 MET —— 穷举
# ---------------------------------------------------------------------------
def part_b():
    section("B. RAISE 可提出 ⟹ 可判 MET？穷举所有分数组合（score ∈ {0, .5, 1}，n ≤ 5）")
    cell = "probe_cell"
    combos = 0
    n_raise = 0
    n_raise_and_sat = 0
    n_sat = 0
    n_sat_without_raise = 0
    for n in range(0, 6):
        for scores in itertools.product((0.0, 0.5, 1.0), repeat=n):
            combos += 1
            obs = [{"task_id": cell, "score": s} for s in scores]
            gs = G.propose(obs, [cell])
            raise_emitted = any(g.kind == "RAISE" for g in gs)
            st = G.stats(obs, cell)
            sat = st["n_obs"] >= G.MIN_OBS and st["mean_score"] >= G.TARGET
            n_raise += raise_emitted
            n_sat += sat
            n_sat_without_raise += (sat and not raise_emitted)
            if raise_emitted and sat:
                n_raise_and_sat += 1
    print(f"  组合总数            {combos}")
    print(f"  会提出 RAISE 的      {n_raise}")
    print(f"  当前读数已达标       {n_sat}（其中不提出 RAISE 的 {n_sat_without_raise}）")
    print(f"  ⭐ 两者交集          {n_raise_and_sat}"
          f"  ⇒ {'空交集：RAISE 永不可达' if n_raise_and_sat == 0 else '有交集，我的怀疑是错的'}")
    OUT["B"] = {"combos": combos, "n_raise": n_raise, "n_sat": n_sat,
                "n_raise_and_sat": n_raise_and_sat}

    # 换一个更强的说法：均分 1.0 是否 ⟺ 全部 1.0
    counter = 0
    for n in range(1, 6):
        for scores in itertools.product((0.0, 0.5, 1.0), repeat=n):
            m = G.stats([{"task_id": cell, "score": s} for s in scores], cell)["mean_score"]
            if (m >= G.TARGET) != all(s >= G.TARGET for s in scores):
                counter += 1
    print(f"  「均分 ≥ {G.TARGET:g}」与「每个分数都 ≥ {G.TARGET:g}」不同值的组合数：{counter}")
    OUT["B"]["mean_eq_all"] = counter


# ---------------------------------------------------------------------------
# C. 部署读数
# ---------------------------------------------------------------------------
def part_c(p):
    section("C. 部署读数：真账本逐格")
    obs, bad = G.coerce_all(G.load(p.score_ledger))
    suites = G.scan_suites(ROOT / "bench" / "tasks")
    ids = sorted({t for v in suites.values() for t in v})
    print(f"  账本 {p.score_ledger.name}: {len(obs)} 行（坏行 {bad}），套件 {len(ids)} 题")
    calibers = {}
    for r in G.load(p.score_ledger):
        calibers[str(r.get("caliber_sig"))[:12]] = calibers.get(str(r.get("caliber_sig"))[:12], 0) + 1
    print(f"  caliber_sig 取值：{calibers}")
    OUT["C"] = {"n_obs": len(obs), "n_ids": len(ids), "calibers": calibers}

    rows = []
    for c in ids:
        st = G.stats(obs, c)
        if st["n_obs"] == 0:
            continue
        gs = G.propose(obs, [c])
        kinds = [g.kind for g in gs]
        stuck = ("RAISE" in kinds)      # 均分 < 1.0 且 n ≥ 3 ⇒ 按 §B 永不可达
        rows.append({"cell": c, "n": st["n_obs"], "mean": round(st["mean_score"], 6),
                     "scores": st["scores"], "propose": kinds, "stuck": stuck})
    rows.sort(key=lambda r: (-r["n"], r["mean"]))
    print(f"  {'格':<22}{'n':>2}  {'均分':<10}{'会提的目标':<22}历史分数")
    for r in rows:
        print(f"  {r['cell']:<22}{r['n']:>2}  {r['mean']:<10.4f}{','.join(r['propose']) or '（无）':<22}"
              f"{r['scores']}")
    n_stuck = sum(1 for r in rows if r["stuck"])
    n_sat = sum(1 for r in rows if G.satisfied(obs, r["cell"]))
    print(f"\n  ⭐ 今天就会提 RAISE 且**永不可达**的格：{n_stuck} / {len(rows)}")
    print(f"  ⭐ 已达标（DONE 候选）的格：{n_sat} / {len(rows)}")
    print(f"  ⭐ 有 <1.0 历史但 n<{G.MIN_OBS}（还没到提 RAISE 的门槛）："
          f"{sum(1 for r in rows if r['n'] < G.MIN_OBS and any(s < G.TARGET for s in r['scores']))}")
    OUT["C"].update({"n_stuck": n_stuck, "n_sat": n_sat, "rows": rows})


# ---------------------------------------------------------------------------
# A. done 三种语义的差集（含真 loop 账本）
# ---------------------------------------------------------------------------
def build_loop_ledger(tmp: pathlib.Path):
    """用**真 API** 跑一个记账循环（⛔ 不手写 verdict 行）。"""
    log = tmp / "goal_ledger.jsonl"
    obs: list[dict] = []
    cell = "t06_rle"
    for round_i in range(8):
        goals = G.propose(obs, [cell])
        if not goals:
            break
        g = goals[0]
        # 「执行」= 记一次观测。离线桩上这道题恒 0.0（V171 实测），这里照抄那个读数。
        obs.append({"task_id": cell, "score": 0.0, "source": "stage0"})
        v, rd = G.evaluate(g, obs)
        G.record(log, g, v, rd, source="stage0")
    return log, obs


def part_a(p):
    section("A. 三种 done 语义差在哪")
    obs, _ = G.coerce_all(G.load(p.score_ledger))
    suites = G.scan_suites(ROOT / "bench" / "tasks")
    ids = sorted({t for v in suites.values() for t in v})
    log_rows = G.load(p.state / "goal_ledger.jsonl")
    print(f"  部署目标账本 {p.state / 'goal_ledger.jsonl'} 存在={bool(log_rows)} 行={len(log_rows)}")

    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="v173_stage0_"))
    log2, obs2 = build_loop_ledger(tmp)
    rows2 = G.load(log2)
    print(f"  loop 账本（真 API，8 轮上限）：{len(rows2)} 行，观测 {len(obs2)} 次")
    for r in rows2:
        print(f"    {r['kind']:<6} {r['verdict']:<8} gid={r['gid']} cell={r['cell']}")

    for tag, rows, ob in (("部署", log_rows, obs), ("loop", rows2, list(obs) + obs2)):
        h, na, rd = done_hist(rows), done_naive(rows), done_read(rows, ob)
        # 「MET 之后还有行」的 gid
        after = 0
        seen_met = set()
        for r in rows:
            if r.get("gid") in seen_met and r.get("verdict") == "NOT_MET":
                after += 1
            if r.get("verdict") == "MET":
                seen_met.add(r.get("gid"))
        base = len(G.propose(ob, ids))
        ph = len(G.propose(ob, ids, done=h))
        pn = len(G.propose(ob, ids, done=na))
        pr = len(G.propose(ob, ids, done=rd))
        print(f"\n  [{tag}] 行={len(rows)} hist={len(h)} naive={len(na)} read={len(rd)}"
              f"｜MET 之后出现 NOT_MET 的行数={after}")
        print(f"        提议条数：无 done={base} hist={ph} naive={pn} read={pr}")
    OUT["A"] = {"loop_rows": rows2, "n_loop": len(rows2)}


def main() -> int:
    p = load_env()
    part_b()
    part_c(p)
    part_a(p)
    outp = HERE / "readings_stage0.json"
    outp.write_text(json.dumps(OUT, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\n读数落盘：{outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
