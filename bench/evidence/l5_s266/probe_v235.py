#!/usr/bin/env python3
"""V235 判据验证（离线，零模型、零网络）。

预注册：`bench/evidence/l5_s266/PREREG_v235.md`（§3 是判据的唯一来源）。

## 这份脚本量什么

L5 的 `RAISE` 判据从 `mean_score >= 1.0`（V173 已证不可满足）换成
`best_score > base`（可满足）。判据改对没有，只在**真读数**上说得清 ——
所以 P1~P5 全部跑在 V234 的真实采样产物上：

- 起点：`t01` 的 3 次 `4/6`（V234 R1 的起点，账本实读）
- 候选：`probe_v234.jsonl` 的 25 次真实采样（6 个去重实现，分数 3/4/5）

## ⛔ 这不是新尺子

分数用 `mw.reflect.verify`（与 V234 的 `got` 同一口径、同一题自带断言）。
本脚本不判任何答案的对错，只判 **L5 的目标语言接不接得上**。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw import goals as G                     # noqa: E402
from mw.reflect import verify                 # noqa: E402

E302 = ROOT / "bench" / "evidence" / "l3_s302"
HERE = ROOT / "bench" / "evidence" / "l5_s266"

T01 = "t01_parse_duration"
#: 4/6 与 5/6 的精确浮点（与 V234 的 `got/len(tests)` 同源）。
S4 = 4 / 6
S5 = 5 / 6

ROWS: list[str] = []
FAIL = 0


def check(tag: str, claim: str, ok: bool, detail: str) -> None:
    global FAIL
    if not ok:
        FAIL += 1
    ROWS.append(f"[{'PASS' if ok else 'FAIL'}] {tag} {claim}\n        {detail}")
    print(f"[{'PASS' if ok else 'FAIL'}] {tag} {claim}")
    print(f"        {detail}")


def suite_task(tid: str) -> dict:
    for ln in (E302 / "suite_sub2.jsonl").read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        d = json.loads(ln)
        if d["id"] == tid:
            return d
    raise SystemExit(f"套件里没有 {tid}")


def pool() -> list[dict]:
    """V234 的 25 次真实采样，**按到达顺序**（时间去重不排 —— 顺序是轨迹的一部分）。"""
    out = []
    for ln in (E302 / "probe_v234.jsonl").read_text(encoding="utf-8").splitlines():
        if ln.strip():
            out.append(json.loads(ln))
    return out


def obs_of(scores: list[float], tid: str = T01) -> list[dict]:
    return [{"task_id": tid, "score": float(s)} for s in scores]


def main() -> int:
    task = suite_task(T01)
    tests = task["tests"]
    cand = pool()

    # ── 起点：V234 真账本里的 3 次 4/6 ─────────────────────────────────
    base_scores = [S4] * 3
    obs = obs_of(base_scores)
    st0 = G.stats(obs, T01)
    goals0 = G.propose(obs, [T01])
    g0 = goals0[0] if goals0 else None

    check("P0", "起点读数（账本实读）：3 次 4/6 ⇒ n=3, mean=best=0.6667；提 1 条 RAISE",
          st0["n_obs"] == 3 and abs(st0["mean_score"] - S4) < 1e-12
          and abs(st0["best_score"] - S4) < 1e-12
          and g0 is not None and g0.kind == "RAISE",
          f"n={st0['n_obs']} mean={st0['mean_score']:.6f} best={st0['best_score']:.6f} "
          f"goals={[g.kind for g in goals0]}")

    # ── P1：新判据在真历史上判出 MET ──────────────────────────────────
    # 顺序重放前 3 个真实候选：3/6, 4/6, 5/6
    best = S4
    trace = []
    met_at = None
    for i, r in enumerate(cand[:3]):
        obs.append({"task_id": T01, "score": float(r["got"]) / len(tests)})
        v, rd = G.evaluate(g0, obs) if g0 else ("NA", {})
        trace.append((i, r["got"], v, rd.get("best_score")))
        if v == "MET" and met_at is None:
            met_at = i
    check("P1", "新判据真判出 MET：按真实到达顺序喂 3 个候选（3/6, 4/6, 5/6），"
               "第 3 个把 best 从 4/6 抬到 5/6 ⇒ MET",
          met_at == 2 and abs(G.stats(obs, T01)["best_score"] - S5) < 1e-12,
          f"轨迹={[(i, f'{g}/6', v, (f'{b:.4f}' if isinstance(b, float) else b)) for i, g, v, b in trace]}")

    # ── P2：旧判据在同一序列上恒 NOT_MET（两判据分道）──────────────────
    old = G.Goal(kind="RAISE", cell=T01,
                 statement="均分提到 1.0",
                 criterion=(("mean_score", ">=", 1.0),), provenance=())
    v_old, rd_old = G.evaluate(old, obs)
    check("P2", "旧判据在同一份 obs 上判 NOT_MET（mean=0.6667 < 1.0，best 已到 0.8333）⇒ "
               "「改的是判据本身」，不是把数据换了",
          v_old == "NOT_MET" and rd_old["mean_score"] < 1.0,
          f"旧判据={v_old} mean={rd_old['mean_score']:.6f} best={rd_old['best_score']:.6f}")

    # ── P3：MET 之后能再提一条（链式，不是无限重复同一 gid）────────────
    done = G.done_gids([{"gid": g0.gid, "verdict": "MET"}])
    goals1 = G.propose(obs, [T01], done=done)
    g1 = goals1[0] if goals1 else None
    check("P3", "MET 之后提**新一代**目标：gid 换新、base 更新到 5/6 ⇒ 逐轮逼近，"
               "不是同一条 gid 无限重复",
          g1 is not None and g1.gid != g0.gid
          and g1.criterion[0][0] == "best_score"
          and abs(g1.criterion[0][2] - S5) < 1e-12,
          f"gid {g0.gid[:8]} → {g1.gid[:8] if g1 else 'None'}；"
          f"criterion={g1.criterion if g1 else None}")

    # ── P4：换路用尽 ⇒ STUCK_BEST（且 STUCK_MEANS 漏掉这段）─────────────
    log = [{"gid": g0.gid, "kind": "RAISE", "cell": T01, "verdict": "MET",
            "reading": dict(G.evaluate(g0, obs)[1]), "source": "probe"}]
    best = G.stats(obs, T01)["best_score"]
    seq = []
    for i, r in enumerate(cand[3:6], start=3):
        s = float(r["got"]) / len(tests)
        obs.append({"task_id": T01, "score": s})
        v, rd = G.evaluate(g1, obs)
        log.append({"gid": g1.gid, "kind": "RAISE", "cell": T01, "verdict": v,
                    "reading": dict(rd), "source": "probe"})
        seq.append((i, r["got"], v, rd["mean_score"], rd["best_score"]))
    esc_best = G.escalations(log, obs, target_metric="best_score")
    esc_mean = G.escalations(log, obs, target_metric="mean_score")
    check("P4", "换路用尽 ⇒ esc(STUCK_BEST)：后 3 个候选（3/6, 4/6, 3/6）都没超过 5/6 ⇒ "
               "连判 3 次 NOT_MET、best 逐位相同",
          esc_best.get(T01, {}).get("reason") == "STUCK_BEST",
          f"后 3 次={[(i, f'{g}/6', v, f'{m:.4f}', f'{b:.4f}') for i, g, v, m, b in seq]}；"
          f"esc={esc_best.get(T01, {}).get('reason')}")

    means = [m for _i, _g, _v, m, _b in seq]
    check("P4b", "两个口径**不是同一条路**：mean 口径下这一格被 **CRITERION_UNREACHABLE** "
                "判死（结构判据、第一轮就够，不需要任何尝试），best 口径下要真试 3 次才 "
                "STUCK_BEST ⇒ 新终止条件不是装饰，旧口径会在这格上永不换路",
          esc_mean.get(T01, {}).get("reason") == "CRITERION_UNREACHABLE"
          and esc_best.get(T01, {}).get("reason") == "STUCK_BEST"
          and len({repr(m) for m in means}) == 3,
          f"esc(mean)={esc_mean.get(T01, {}).get('reason')}；"
          f"esc(best)={esc_best.get(T01, {}).get('reason')}；"
          f"三次均值={[f'{m:.6f}' for m in means]}（逐位不同 ⇒ 不是 STUCK_MEANS）")

    # ── P5：天花板是读数，不是定理 ─────────────────────────────────────
    got_all = [int(r["got"]) for r in cand]
    n_impl = len({r["code_sha"] for r in cand})
    check("P5", "天花板要报分母：25 次采样 / 6 个去重实现里，最好 = 5/6，6/6 命中 0 次 "
               "⛔ 不许写成「6/6 不可能」",
          max(got_all) == 5 and got_all.count(6) == 0 and len(got_all) == 25,
          f"n_sample={len(got_all)} n_impl={n_impl} max={max(got_all)} hit6={got_all.count(6)} "
          f"dist={ {g: got_all.count(g) for g in sorted(set(got_all))} }")

    # ── P6：不新增模块级常量、不改 KINDS ───────────────────────────────
    consts = sorted(n for n in vars(G)
                    if n.isupper() and not n.startswith("_")
                    and isinstance(getattr(G, n), (tuple, int, float, str)))
    check("P6", "常量集合仍是 KINDS/MIN_OBS/TARGET；KINDS 仍是三元组（⛔ 不加第四类）",
          consts == ["KINDS", "MIN_OBS", "TARGET"] and len(G.KINDS) == 3,
          f"consts={consts} KINDS={G.KINDS}")

    # ── P7：变异体有牙 ─────────────────────────────────────────────────
    # 变异 1：base 恒 0 ⇒ 任何非零分都算「进步」⇒ 做成的反复做。
    #         正确构造：obs 里已有 0.5，base 必须是 0.5 ⇒ NOT_MET。
    o_half = obs_of([0.0, 0.0, 0.5])
    v_mut1 = G.evaluate(G.Goal(kind="RAISE", cell=T01, statement="",
                               criterion=(("best_score", ">", 0.0),), provenance=()),
                        o_half)[0]
    v_true1 = G.evaluate(G.Goal(kind="RAISE", cell=T01, statement="",
                                criterion=(("best_score", ">", 0.5),), provenance=()),
                         o_half)[0]
    # 变异 2：`>` 写成 `>=` ⇒ **没进步也叫进步**（同分不断重开目标）。
    o_flat = obs_of([0.5, 0.5, 0.5])
    v_mut2 = G.evaluate(G.Goal(kind="RAISE", cell=T01, statement="",
                               criterion=(("best_score", ">=", 0.5),), provenance=()),
                        o_flat)[0]
    v_true2 = G.evaluate(G.Goal(kind="RAISE", cell=T01, statement="",
                                criterion=(("best_score", ">", 0.5),), provenance=()),
                         o_flat)[0]
    check("P7", "变异体有牙：base→0 ⇒ 已有 0.5 也判 MET（应为 NOT_MET）；"
               "`>`→`>=` ⇒ 同分也判 MET（应为 NOT_MET）⇒ 两条都会让「进步」变成恒真",
          v_mut1 == "MET" and v_true1 == "NOT_MET"
          and v_mut2 == "MET" and v_true2 == "NOT_MET",
          f"base=0:{v_mut1} vs base=0.5:{v_true1}；>=:{v_mut2} vs >:{v_true2}")

    # ── P8：历史账本读数不被改 ─────────────────────────────────────────
    led = [json.loads(x) for x in
           (ROOT / "state" / "score_ledger.jsonl").read_text(encoding="utf-8").splitlines()
           if x.strip()]
    ok8 = True
    detail8 = []
    for cell in sorted({r["task_id"] for r in led})[:5]:
        sc = [float(r["score"]) for r in led if r["task_id"] == cell]
        stt = G.stats(obs_of(sc, cell), cell)
        t = 0.0
        for x in sc:
            t += x
        want = t / len(sc)
        if not (stt["n_obs"] == len(sc) and abs(stt["mean_score"] - want) < 1e-15):
            ok8 = False
        detail8.append(f"{cell}:n={stt['n_obs']},mean={stt['mean_score']:.6f}")
    check("P8", "历史账本的 mean/n_obs 读数逐位不变（新增 best_score 只是加字段）",
          ok8, "｜".join(detail8[:3]) + " …")

    (HERE / "readings_v235.json").write_text(
        json.dumps({"p0": st0, "trace": [[i, g, v, (b if isinstance(b, float) else None)]
                                         for i, g, v, b in trace],
                    "seq_after_met": [[i, g, v, m, b] for i, g, v, m, b in seq],
                    "esc_best": esc_best.get(T01), "esc_mean": sorted(esc_mean),
                    "pool": {"n_sample": len(got_all), "n_impl": n_impl,
                             "max": max(got_all), "hit6": got_all.count(6)}},
                   ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    (HERE / "readings_v235.txt").write_text("\n".join(ROWS) + "\n", encoding="utf-8")
    print()
    print(f"::VERDICT:: {'PASS' if FAIL == 0 else 'FAIL'}  FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
