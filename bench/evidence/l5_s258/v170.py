#!/usr/bin/env python3
"""V170（§238）：L5 —— 系统能不能从自己的账本里「找活干」。

判据按 `PREREG_v170.md`，五条，一条一行。⛔ 跑完不许改判据。

用法：
    python3 v170.py            # 全跑（P5 会调一次 run.py --goals-act 1）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]                     # bench/evidence/l5_s258 → 仓库根
sys.path.insert(0, str(ROOT))

import mw.goals as G                       # noqa: E402

STATE = ROOT / "state"
SUITE_DIR = ROOT / "bench" / "tasks"
OUT: dict = {}
VERDICTS: list[tuple[str, str, str]] = []


def say(title: str) -> None:
    print(f"\n## {title}")


def verdict(tag: str, ok: bool, detail: str) -> None:
    VERDICTS.append((tag, "PASS" if ok else "FAIL", detail))
    print(f"  ⇒ {tag} **{'PASS' if ok else 'FAIL'}**｜{detail}")


def load_obs() -> list[dict]:
    rows, bad = G.coerce_all(G.load(STATE / "score_ledger.jsonl"))
    print(f"  账本  {len(rows)} 行（坏行 {bad}）")
    return rows


def prompts() -> list[str]:
    out = []
    for p in sorted(SUITE_DIR.glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(str(json.loads(line).get("prompt") or ""))
    return out


# ---------------------------------------------------------------- P1
def p1(obs, suite_ids) -> None:
    say("P1 无证据时不许编（fail-closed）")
    a = G.propose([], [])
    b = G.propose([], suite_ids)
    c = G.propose(obs, suite_ids)
    print(f"  空账本 + 空套件 ⇒ {len(a)} 条")
    print(f"  空账本 + {len(suite_ids)} 题 ⇒ {len(b)} 条，类型 "
          f"{sorted({g.kind for g in b})}")
    print(f"  真账本 + {len(suite_ids)} 题 ⇒ {len(c)} 条")
    ok = (len(a) == 0) and all(g.kind == "COVER" for g in b)
    verdict("P1", ok,
            f"空套件 0 条；空账本只出 COVER（0 次观测是**真读数**，不是编的分数目标）")
    OUT["p1"] = {"empty_both": len(a), "empty_ledger": len(b),
                 "real": len(c), "kinds_empty_ledger": sorted({g.kind for g in b})}


# ---------------------------------------------------------------- P2
def p2(goals, obs) -> None:
    say("P2 可追溯到自己的读数，且不是人给的")
    bad_prov = [(g.cell, G.verify_provenance(g, obs)) for g in goals]
    bad_prov = [(c, b) for c, b in bad_prov if b]

    ps = prompts()
    # ⚠️ 口径修正（首版被 task id 跨界命中，如实登记）：
    #   首版直接拿「题面里任意 8 字窗口 ∈ 目标陈述」判「抄了题面」，
    #   实测 33 次命中**全部**来自题 id 里的函数名（`parse_date_flex` 既是 id 的一部分，
    #   也是题面的一部分）⇒ 那个口径测的是**身份**，不是抄袭（纪律 89 的形态）。
    #   修正：先去掉 cell id 再判；同时补一条**结构对照**（把全部题面换掉，目标必须一字不变）。
    hits_raw, hits = [], []
    for g in goals:
        for pr in ps:
            for i in range(0, len(pr) - 8 + 1):
                w = pr[i:i + 8]
                if w in g.statement:
                    hits_raw.append((g.cell, w))
                    break
    for g in goals:
        body = g.statement.replace(g.cell, "")
        for pr in ps:
            for i in range(0, len(pr) - 8 + 1):
                w = pr[i:i + 8]
                if w in body:
                    hits.append((g.cell, w))
                    break
    # 结构对照：题面整段换掉（id 不变）⇒ 目标集必须逐条相同
    import tempfile, hashlib
    tmpd = Path(tempfile.mkdtemp(prefix="v170_noprompt_"))
    for f in sorted(SUITE_DIR.glob("*.jsonl")):
        out = []
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            d["prompt"] = "X" * 40          # 题面换成垃圾，id 留着
            out.append(json.dumps(d, ensure_ascii=False))
        (tmpd / f.name).write_text("\n".join(out) + "\n", encoding="utf-8")
    ids2 = [t for v in G.scan_suites(tmpd).values() for t in v]
    ids1 = [t for v in G.scan_suites(SUITE_DIR).values() for t in v]
    g2 = G.propose(obs, ids2)
    same_goals = [g.as_dict() for g in g2] == [g.as_dict() for g in goals]
    print(f"  目标 {len(goals)} 条｜溯源不自洽 {len(bad_prov)} 条")
    print(f"  题面 8 字窗口命中：原样口径 {len(hits_raw)} 次（**全部来自 cell id**）"
          f"｜去掉 id 后 {len(hits)} 次")
    print(f"  结构对照：把 {len(ids1)} 条题面全部换成占位符 ⇒ id 不变"
          f"（{ids1 == ids2}）⇒ 目标集逐条相同（{same_goals}）")
    ok = (not bad_prov) and (not hits) and same_goals
    verdict("P2", ok, f"溯源 {len(goals) - len(bad_prov)}/{len(goals)} 自洽；"
                      f"去 id 后题面交集 {len(hits)} 次；换掉全部题面目标不变={same_goals}")
    OUT["p2"] = {"n_goals": len(goals), "n_bad_prov": len(bad_prov),
                 "hits_raw": len(hits_raw), "hits_after_strip": len(hits),
                 "n_prompts": len(ps), "ids_unchanged": ids1 == ids2,
                 "goals_unchanged": same_goals}


# ---------------------------------------------------------------- P3
def p3(goals, obs, suite_ids) -> None:
    say("P3 自己判得了（MET/NOT_MET，无 UNKNOWN）+ 每类一个翻转见证")
    vs = [G.evaluate(g, obs)[0] for g in goals]
    print(f"  {len(goals)} 条判定：MET {vs.count('MET')}｜NOT_MET {vs.count('NOT_MET')}"
          f"｜UNKNOWN {vs.count('UNKNOWN')}")

    flips = {}
    # ① COVER：补 1 次观测
    cover = next(g for g in goals if g.kind == "COVER")
    a1 = G.evaluate(cover, obs)[0]
    a2 = G.evaluate(cover, obs + [{"task_id": cover.cell, "score": 0.0}])[0]
    flips["COVER"] = (a1, a2)
    # ② PROBE：补到 3 次
    probe = next(g for g in goals if g.kind == "PROBE")
    n_now = G.stats(obs, probe.cell)["n_obs"]
    add = [{"task_id": probe.cell, "score": 1.0} for _ in range(max(0, 3 - n_now))]
    b1 = G.evaluate(probe, obs)[0]
    b2 = G.evaluate(probe, obs + add)[0]
    flips["PROBE"] = (b1, b2)
    # ③ RAISE：构造一个 3 次观测、均分 0.5 的格子
    fake = [{"task_id": "zz_synth", "score": 0.5} for _ in range(3)]
    raise_g = G.propose(fake, ["zz_synth"])[0]
    c1 = G.evaluate(raise_g, fake)[0]
    c2 = G.evaluate(raise_g, [{"task_id": "zz_synth", "score": 1.0} for _ in range(3)])[0]
    flips["RAISE"] = (c1, c2)

    for k, (x, y) in flips.items():
        print(f"  {k:<5} 前 {x} → 后 {y}")
    ok = (vs.count("UNKNOWN") == 0) and all(x != y for x, y in flips.values())
    verdict("P3", ok, f"判定 {len(vs)} 条无 UNKNOWN；3 类翻转 "
                      f"{sum(1 for x, y in flips.values() if x != y)}/3")
    OUT["p3"] = {"n": len(vs), "met": vs.count("MET"),
                 "not_met": vs.count("NOT_MET"),
                 "flips": {k: list(v) for k, v in flips.items()}}


# ---------------------------------------------------------------- P4
def p4(obs, suite_ids, goals) -> None:
    say("P4 目标随读数变（4 个扰动，预注册原式：top-1 gid）")
    base = G.propose(obs, suite_ids)
    base_top = base[0].gid if base else ""
    cells = sorted({o["task_id"] for o in obs} & set(suite_ids))
    sess = sorted({o.get("session") for o in obs if o.get("session") is not None})

    def top(rows):
        gs = G.propose(rows, suite_ids)
        return (gs[0].gid if gs else ""), gs

    variants = {}
    # ① 丢掉最后一个会话
    variants["drop_last_session"] = [o for o in obs if o.get("session") != sess[-1]]
    # ② 把一格分数抬到 1.0
    v = [dict(o) for o in obs]
    for o in v:
        if o["task_id"] == cells[0]:
            o["score"] = 1.0
    variants["boost_one"] = v
    # ③ 删掉一格的全部行（该格变成 COVER）
    variants["drop_cell"] = [o for o in obs if o["task_id"] != cells[0]]
    # ④ 追加一轮（同分再测一次）
    variants["append_round"] = obs + [dict(o, session=99) for o in obs if o["task_id"] in cells]

    changed_top = 0
    changes_set = 0
    base_set = {(g.kind, g.cell) for g in base}
    for name, rows in variants.items():
        gid, gs = top(rows)
        s = {(g.kind, g.cell) for g in gs}
        dt = "变" if gid != base_top else "同"
        ds = "变" if s != base_set else "同"
        # 活性：这个扰动到底改了几行（0 = 装置哑火，纪律 88）
        n0 = sorted((o["task_id"], round(o["score"], 6)) for o in obs)
        n1 = sorted((o["task_id"], round(o["score"], 6)) for o in rows)
        live = abs(len(n1) - len(n0)) + sum(1 for a, b in zip(n0, n1) if a != b)
        if gid != base_top:
            changed_top += 1
        if s != base_set:
            changes_set += 1
        print(f"  {name:<18} 改了 {live} 行｜top-1 {dt}（{gid[:8] or '—'}）｜"
              f"目标集 {ds}（{len(gs)} 条，Δ={len(s ^ base_set)}）")
        variants[name] = {"gid": gid, "n": len(gs), "set_delta": len(s ^ base_set),
                          "liveness": live}

    # 附：第 5 个扰动 = 把第一名那一格补够观测（执行之后的状态）
    top_cell = base[0].cell if base else ""
    extra = [{"task_id": top_cell, "score": 0.0} for _ in range(3)]
    gid5, gs5 = top(obs + extra)
    print(f"  {'fill_top_cell':<18} top-1 {'变' if gid5 != base_top else '同'}"
          f"（{gid5[:8] or '—'}）｜目标集 "
          f"{'变' if {(g.kind, g.cell) for g in gs5} != base_set else '同'}")

    # 附：P4'（补充读数，**不替代**预注册原式）—— 逐目标：把这条目标引用的读数改掉，
    # 它必须消失或换类。这是「目标不是常量」的直接证据，分母 = 目标条数。
    responsive = 0
    for g in goals:
        st = G.stats(obs, g.cell)
        if g.kind == "COVER":
            rows2 = obs + [{"task_id": g.cell, "score": 0.0}]
        elif g.kind == "PROBE":
            rows2 = obs + [{"task_id": g.cell, "score": 0.0}
                           for _ in range(3 - st["n_obs"])]
        else:
            rows2 = [dict(o, score=1.0) if o["task_id"] == g.cell else o for o in obs]
        gs2 = G.propose(rows2, suite_ids)
        if all(x.gid != g.gid for x in gs2):
            responsive += 1
    print(f"  逐目标：改掉它引用的读数 ⇒ 该目标消失/换类 **{responsive}/{len(goals)}**")

    dead = [k for k, v in variants.items() if v["liveness"] == 0]
    live_n = 4 - len(dead)
    ok = changed_top >= 2
    verdict("P4", ok, f"预注册 4 个扰动里 top-1 改变 **{changed_top}/4**（阈值 ≥2）"
                      f"｜活的扰动 {live_n}/4 {('（哑火：' + ','.join(dead) + '）') if dead else ''}"
                      f"｜目标集变化 {changes_set}/4｜逐目标读数依赖 {responsive}/{len(goals)}")
    OUT["p4"] = {"base_top": base_top, "changed_top": changed_top,
                 "changed_set": changes_set, "variants": variants,
                 "fill_top_cell_top": gid5, "n_base": len(base),
                 "per_goal_responsive": responsive, "n_goals": len(goals),
                 "dead_perturbations": dead, "n_live": live_n}


# ---------------------------------------------------------------- P5
def p5() -> None:
    say("P5 判完就换下一个（真执行一条，走生产入口）")
    tmp = Path(tempfile.mkdtemp(prefix="v170_"))
    shutil.copy(STATE / "score_ledger.jsonl", tmp / "score_ledger.jsonl")
    env = dict(os.environ, V1_STATE_DIR=str(tmp))
    cmd = [sys.executable, str(ROOT / "run.py"), "--goals", "--goals-act", "1",
           "--offline", "--goals-json", str(tmp / "goals.json")]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(ROOT))
    tail = [ln for ln in r.stdout.splitlines() if "⇒" in ln or "判据行" in ln
            or "PROBE" in ln or "COVER" in ln]
    for ln in tail[-8:]:
        print("  " + ln.strip())
    d = json.loads((tmp / "goals.json").read_text()) if (tmp / "goals.json").exists() else {}
    acted = d.get("acted") or []
    again = d.get("reproposed") or []
    met = [a for a in acted if a["verdict"] == "MET"]
    gid = met[0]["gid"] if met else ""
    cell = met[0]["cell"] if met else ""
    no_repeat = all(g["gid"] != gid for g in again)
    moved = [g for g in again if g["cell"] == cell]
    kinds = sorted({g["kind"] for g in moved})
    print(f"  执行 {len(acted)} 条｜MET {len(met)}｜重新提议 {len(again)} 条"
          f"｜同 gid 重复 {sum(1 for g in again if g['gid'] == gid)} 次")
    print(f"  同一格的新目标：{[(g['kind'], g['cell'][:20]) for g in moved]}")
    ok = bool(met) and no_repeat and bool(moved) and "COVER" not in kinds
    verdict("P5", ok, f"CODE→MET 后同 gid 不再出现={no_repeat}；"
                      f"同格换类={kinds or '无'}")
    OUT["p5"] = {"acted": acted, "n_again": len(again),
                 "repeat_gid": sum(1 for g in again if g["gid"] == gid),
                 "successor_kinds": kinds}


def p6() -> None:
    say("P6（补充，不参与 5 条判据）目标链：同一格上连续执行 4 次")
    # ⚠️ 为什么用**单题套件**：全量 40 题下同类优先 ⇒ 前 34 次全是 COVER，
    #    看不到「同类变化」。单题套件把「同一格的相邻目标」隔离出来。
    #    ⛔ 这是**环境边界**的缩小，不是人给的目标（题还是那一道，没人指定要做它）。
    tmp = Path(tempfile.mkdtemp(prefix="v170_chain_"))
    sd = tmp / "tasks"
    sd.mkdir()
    src = SUITE_DIR / "g7d_spec_incomplete.jsonl"
    first = src.read_text(encoding="utf-8").splitlines()[0]
    (sd / "one.jsonl").write_text(first + "\n", encoding="utf-8")
    cell = json.loads(first)["id"]
    shutil.copy(STATE / "score_ledger.jsonl", tmp / "score_ledger.jsonl")
    env = dict(os.environ, V1_STATE_DIR=str(tmp))
    chain = []
    for i in range(4):
        cmd = [sys.executable, str(ROOT / "run.py"), "--goals", "--goals-act", "1",
               "--offline", "--goals-suite-dir", str(sd),
               "--goals-json", str(tmp / f"g{i}.json")]
        subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(ROOT))
        d = json.loads((tmp / f"g{i}.json").read_text())
        a = (d.get("acted") or [{}])[0]
        nxt = (d.get("reproposed") or [{}])
        chain.append({"step": i + 1, "kind": a.get("kind"), "verdict": a.get("verdict"),
                      "n_obs": (a.get("reading") or {}).get("n_obs"),
                      "mean": round((a.get("reading") or {}).get("mean_score", 0.0), 4),
                      "next": nxt[0].get("kind") if nxt else None})
        print(f"  第 {i+1} 步：{a.get('kind'):<6} ⇒ {a.get('verdict'):<8}"
              f" n={chain[-1]['n_obs']} mean={chain[-1]['mean']}"
              f"｜下一目标 {chain[-1]['next']}")
    kinds = [c["kind"] for c in chain]
    OUT["p6"] = {"cell": cell, "chain": chain, "kinds": kinds}


def p7() -> None:
    say("P7（补充）覆盖跑：一次把 34 条 COVER 全执行掉（没人要求这件事）")
    tmp = Path(tempfile.mkdtemp(prefix="v170_cover_"))
    shutil.copy(STATE / "score_ledger.jsonl", tmp / "score_ledger.jsonl")
    env = dict(os.environ, V1_STATE_DIR=str(tmp))
    cmd = [sys.executable, str(ROOT / "run.py"), "--goals", "--goals-act", "34",
           "--offline", "--goals-json", str(tmp / "cover.json")]
    import time
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(ROOT))
    dt = time.time() - t0
    d = json.loads((tmp / "cover.json").read_text())
    before = d.get("goals") or []
    acted = d.get("acted") or []
    after = d.get("reproposed") or []
    kb = {k: sum(1 for g in before if g["kind"] == k) for k in G.KINDS}
    ka = {k: sum(1 for g in after if g["kind"] == k) for k in G.KINDS}
    print(f"  执行前 {len(before)} 条 {kb}")
    print(f"  执行  {len(acted)} 条｜MET {sum(1 for a in acted if a['verdict']=='MET')}"
          f"｜NOT_MET {sum(1 for a in acted if a['verdict']=='NOT_MET')}｜墙钟 {dt:.1f}s")
    print(f"  执行后 {len(after)} 条 {ka}")
    print(f"  人给的指令：0 条（`--goal` 没传；题是环境边界，不是指令）")
    OUT["p7"] = {"before_kinds": kb, "after_kinds": ka,
                 "n_acted": len(acted), "wall_s": round(dt, 1),
                 "met": sum(1 for a in acted if a["verdict"] == "MET"),
                 "n_after": len(after)}


def main() -> int:
    print("=" * 74)
    print("V170 / L5 —— 目标来自自己的账本：提议 → 判定 → 记账 → 换下一个")
    print("=" * 74)
    obs = load_obs()
    suites = G.scan_suites(SUITE_DIR)
    suite_ids = [t for ids in suites.values() for t in ids]
    covered = sorted({o["task_id"] for o in obs} & set(suite_ids))
    print(f"  套件  {len(suites)} 个 {len(suite_ids)} 题；覆盖 {len(covered)}；"
          f"零覆盖 **{len(suite_ids) - len(covered)}**")
    goals = G.propose(obs, suite_ids)
    kinds = {k: sum(1 for g in goals if g.kind == k) for k in G.KINDS}
    print(f"  提议  {len(goals)} 条：{kinds}")

    p1(obs, suite_ids)
    p2(goals, obs)
    p3(goals, obs, suite_ids)
    p4(obs, suite_ids, goals)
    p5()
    p6()
    p7()

    print("\n" + "=" * 74)
    print("判据汇总")
    print("=" * 74)
    for tag, v, detail in VERDICTS:
        print(f"  {tag:<3} {v:<5} {detail}")
    n_pass = sum(1 for _, v, _ in VERDICTS if v == "PASS")
    print(f"\n  {n_pass}/{len(VERDICTS)} PASS")
    OUT["verdicts"] = [{"tag": t, "verdict": v, "detail": d} for t, v, d in VERDICTS]
    OUT["n_pass"] = n_pass
    (HERE / "readings_v170.json").write_text(
        json.dumps(OUT, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  读数  {HERE / 'readings_v170.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
