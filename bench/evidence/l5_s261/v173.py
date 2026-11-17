"""V173 主实验 —— 「提分」这一类目标按定义不可能完成。

预注册：`PREREG_v173_v2.md`（v1 已被 STAGE0 否证，原文留在 `PREREG_v173_v1_SUPERSEDED.md`）。

七条判据：
  P1 `RAISE` 不可达成（穷举）        P2 `unreachable()` 与解析式逐组一致
  P3 行为变化（两臂真跑）            P4 不误伤（三条 fail-open）
  P5 留痕（两种理由都印）            P6 部署态（提议逐条相同）
  P7 变异体有牙（两个都跑）

⚠️ 装置自报（纪律 65）：
  - 纯函数判据用**本文件顶部 import 的** `mw.goals`（= 改动后）。
  - 「旧臂」用 `git show HEAD:mw/goals.py` 还原到临时文件后**子进程真跑**，
    每一步都核对 `sha256`，⛔ 不靠 import 顺序假设「现在跑的是哪一版」。
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT))

from mw import goals as G  # noqa: E402

CELL = "t06_rle"
ROUNDS = 8
PY = sys.executable
GOALS = ROOT / "mw" / "goals.py"
#: 与 goals.py **成对**换臂（V173 装置自踩：只换一个会让旧臂崩在 AttributeError）。
RUNPY = ROOT / "run.py"

VERDICTS: list[dict] = []
READ: dict = {}


def say(s=""):
    print(s, flush=True)


def check(tag: str, claim: str, ok: bool, detail: str = ""):
    VERDICTS.append({"tag": tag, "claim": claim, "ok": bool(ok), "detail": detail})
    mark = "PASS" if ok else "FAIL"
    say(f"  [{mark}] {tag} {claim}")
    if detail:
        say(f"         {detail}")


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


# ===========================================================================
# P1 / P2 —— 纯函数
# ===========================================================================
def part_p1():
    say()
    say("=" * 74)
    say("P1 `RAISE` 不可达成 —— 穷举 score ∈ {0,.5,1}，n ≤ 5")
    say("=" * 74)
    cell, combos, n_raise, n_sat, both = "p1cell", 0, 0, 0, 0
    for n in range(0, 6):
        for scores in itertools.product((0.0, 0.5, 1.0), repeat=n):
            combos += 1
            obs = [{"task_id": cell, "score": s} for s in scores]
            raise_emitted = any(g.kind == "RAISE" for g in G.propose(obs, [cell]))
            st = G.stats(obs, cell)
            sat = st["n_obs"] >= G.MIN_OBS and st["mean_score"] >= G.TARGET
            n_raise += raise_emitted
            n_sat += sat
            both += (raise_emitted and sat)
    # 「均分 ≥ target」⟺「每个分数 ≥ target」
    counter = 0
    for n in range(1, 6):
        for scores in itertools.product((0.0, 0.5, 1.0), repeat=n):
            obs = [{"task_id": cell, "score": s} for s in scores]
            m = G.stats(obs, cell)["mean_score"]
            if (m >= G.TARGET) != all(s >= G.TARGET for s in scores):
                counter += 1
    say(f"  组合 {combos}｜会提 RAISE {n_raise}｜当前达标 {n_sat}｜**交集 {both}**")
    say(f"  「均分≥{G.TARGET:g}」与「每格≥{G.TARGET:g}」不同值的组合：{counter}")
    check("P1", "RAISE 被提出 ⟹ 判据已不可能满足（交集 0）；且均分 ≥1 ⟺ 每个分数 ≥1",
          both == 0 and counter == 0,
          f"交集={both} 反例={counter}（提出 {n_raise} 组 / 达标 {n_sat} 组 —— 两臂都不为 0，说明穷举不是空转）")
    READ["p1"] = {"combos": combos, "n_raise": n_raise, "n_sat": n_sat,
                  "both": both, "mean_counterexample": counter}


def part_p2():
    say()
    say("=" * 74)
    say("P2 `unreachable()` 与解析式逐组一致（n ∈ 3..6）")
    say("=" * 74)
    cell = "p2cell"
    total = flagged = mismatch = 0
    for n in range(3, 7):
        for scores in itertools.product((0.0, 0.5, 1.0), repeat=n):
            obs = [{"task_id": cell, "score": s} for s in scores]
            u = G.unreachable(cell, obs)
            want = min(scores) < G.TARGET
            total += 1
            flagged += bool(u)
            mismatch += (bool(u) != want)
    say(f"  组合 {total}｜判为不可达 {flagged}｜与解析式不一致 {mismatch}")

    # 追加 k 个满分：均分严格 < 1 且单调上升
    base = [{"task_id": cell, "score": 0.0} for _ in range(3)]
    means = []
    obs = list(base)
    for _ in range(8):
        obs = obs + [{"task_id": cell, "score": 1.0}]
        means.append(G.stats(obs, cell)["mean_score"])
    strictly_under = all(m < G.TARGET for m in means)
    increasing = all(b > a for a, b in zip([0.0] + means, means))
    say(f"  追加 1..8 个满分后的均分：{[round(m, 6) for m in means]}")
    say(f"  全部严格 < 1：{strictly_under}｜严格单调上升：{increasing}")
    check("P2", "判「不可达」⟺ 历史里有 <1 的分数；追加满分后均分严格 <1 且单调上升",
          flagged > 0 and mismatch == 0 and strictly_under and increasing,
          f"判为不可达 {flagged}/{total} 不一致 {mismatch}｜末值 {means[-1]:.6f}")
    READ["p2"] = {"total": total, "flagged": flagged, "mismatch": mismatch,
                  "means_after_k": means}


# ===========================================================================
# P4 / P5 / P7 —— 纯函数（P7 会把这几个函数在变异体下再跑一遍）
# ===========================================================================
def case_p4() -> tuple[bool, str]:
    cell = "p4cell"
    ok_all = [{"task_id": cell, "score": 1.0} for _ in range(3)]
    ok_below_few = [{"task_id": cell, "score": 0.0}, {"task_id": cell, "score": 1.0}]
    over = [{"task_id": cell, "score": 0.0}, {"task_id": cell, "score": 1.2},
            {"task_id": cell, "score": 1.2}]
    a = G.unreachable(cell, ok_all)
    b = G.unreachable(cell, ok_below_few)
    c = G.unreachable(cell, over)
    kinds_b = [g.kind for g in G.propose(ok_below_few, [cell])]
    esc_c = G.escalations([], over)
    kinds_c = [g.kind for g in G.propose(over, [cell], escalated=list(esc_c))]
    good = (a is None and b is None and c is None
            and kinds_b == ["PROBE"] and kinds_c == ["RAISE"] and not esc_c)
    return good, (f"全1.0 n=3 ⇒ {a}｜<1.0 但 n=2 ⇒ {b}（提议 {kinds_b}）"
                  f"｜有 1.2 的分数 ⇒ {c}（提议 {kinds_c}，转运维 {sorted(esc_c)}）")


def case_p5() -> tuple[bool, str]:
    loop_log = [{"gid": "g-beh", "kind": "RAISE", "cell": "cbeh", "verdict": "NOT_MET",
                 "reading": {"cell": "cbeh", "n_obs": 4, "mean_score": 0.0},
                 "criterion": [["mean_score", ">=", 1.0]]} for _ in range(3)]
    obs = [{"task_id": "cstr", "score": 0.0}, {"task_id": "cstr", "score": 0.5},
           {"task_id": "cstr", "score": 1.0}]
    esc = G.escalations(loop_log, obs)
    text = G.render_escalations(esc)
    empty = G.render_escalations({})
    good = (set(esc) == {"cbeh", "cstr"}
            and esc["cstr"]["reason"] == "CRITERION_UNREACHABLE"
            and esc["cbeh"]["reason"] == "STUCK_MEANS"
            and "判据按定义不可满足" in text and "n_below" not in text
            and "2 次 <1" in text and "0.000000" in text
            and "均分逐位相同" in text
            and "没有" in empty)
    return good, (f"格={sorted(esc)}｜结构行={'判据按定义不可满足' in text}"
                  f"｜行为行={'均分逐位相同' in text}｜0 条时={empty[:12]}…")


def case_p7_mutant_none() -> tuple[bool, str]:
    """恒 None ⇒ 结构判据哑火：一轮读数后仍会提 RAISE。
    返回 True = **变异体确实让判据失效了**（这是 P7 要的证据，不是坏消息）。"""
    obs = [{"task_id": "m1", "score": 0.0} for _ in range(3)]
    esc = G.escalations([], obs)
    kinds = [g.kind for g in G.propose(obs, ["m1"], escalated=list(esc))]
    return (esc == {} and kinds == ["RAISE"]), f"转运维={sorted(esc)} 提议={kinds}"


def case_p7_mutant_always() -> tuple[bool, str]:
    """恒非空 ⇒ 误伤：全 1.0 已达标的格也被判「做不到」。
    返回 True = **变异体确实让 P4 失效了**。"""
    cell = "m2"
    obs = [{"task_id": cell, "score": 1.0} for _ in range(3)]
    u = G.unreachable(cell, obs)
    esc = G.escalations([], obs)
    return (u is not None and cell in esc), f"unreachable 非空={u is not None} 转运维={sorted(esc)}"


def part_p4_p5():
    say()
    say("=" * 74)
    say("P4 不误伤 / P5 留痕")
    say("=" * 74)
    ok4, d4 = case_p4()
    check("P4", "已达标 / 未到门槛 / 尺子坏了（>1.0）三种都不许判不可达", ok4, d4)
    ok5, d5 = case_p5()
    check("P5", "两种理由都印出读数；0 条时仍打印（⛔ 不许静默丢弃）", ok5, d5)
    READ["p4"] = d4


def part_p7():
    say()
    say("=" * 74)
    say("P7 变异体（两个都跑）")
    say("=" * 74)
    real = G.unreachable
    try:
        G.unreachable = lambda *a, **k: None
        ok_none, d_none = case_p7_mutant_none()
        G.unreachable = lambda *a, **k: {"reason": "CRITERION_UNREACHABLE", "cell": "x",
                                        "kind": "RAISE", "n_fail": 0, "means": [],
                                        "gids": [], "n_obs": 0, "mean": 0.0,
                                        "n_below": 0, "min": 0.0, "ceiling": ""}
        ok_always, d_always = case_p7_mutant_always()
    finally:
        G.unreachable = real
    say(f"  变异体 A（恒 None）：让结构判据哑火 = {ok_none} —— {d_none}")
    say(f"  变异体 B（恒非空）：让 P4 误伤      = {ok_always} —— {d_always}")
    check("P7", "两个变异体都让对应的判据失效（恒 None ⇒ 结构判据哑火；恒非空 ⇒ 误伤）",
          ok_none and ok_always,
          f"A 哑火={ok_none} B 误伤={ok_always}")


# ===========================================================================
# P3 / P6 —— 两臂真跑（子进程）
# ===========================================================================
def one_suite(tmp: pathlib.Path) -> pathlib.Path:
    sd = tmp / "tasks"
    sd.mkdir(parents=True, exist_ok=True)
    src = ROOT / "bench" / "tasks" / "coding_small.jsonl"
    line = next(l for l in src.read_text(encoding="utf-8").splitlines()
                if json.loads(l)["id"] == CELL)
    (sd / "one.jsonl").write_text(line + "\n", encoding="utf-8")
    return sd


def run_loop(tag: str, tmp: pathlib.Path, *, stop_on_empty: bool = True) -> dict:
    """单题、离线桩、8 轮上限 —— 与 V171 `probe_loop.py` 同一台装置。

    `stop_on_empty=False` 复刻 V171 的跑法（提议 0 条也把 8 轮跑满），
    用来核对预注册里照抄 V171 的那组数字（5 / 8 / 6）。
    """
    sd = one_suite(tmp)
    (tmp / "score_ledger.jsonl").write_text("", encoding="utf-8")
    env = dict(os.environ, V1_STATE_DIR=str(tmp))
    rows = []
    esc_round = None
    for i in range(ROUNDS):
        cmd = [PY, str(ROOT / "run.py"), "--goals", "--goals-act", "1", "--offline",
               "--goals-suite-dir", str(sd),
               "--goals-json", str(tmp / f"g{i}.json"),
               "--goals-log", str(tmp / "goal_ledger.jsonl"),
               "--goals-obs", str(tmp / "goal_obs.jsonl"),
               "--ledger", str(tmp / "score_ledger.jsonl")]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(ROOT))
        if r.returncode != 0:
            return {"tag": tag, "error": f"rc={r.returncode}", "stderr": r.stderr[-400:]}
        d = json.loads((tmp / f"g{i}.json").read_text(encoding="utf-8"))
        before = d.get("goals") or []
        acted = d.get("acted") or []
        esc = d.get("escalated_cells_in_suite")
        if esc is None:                       # 旧臂没有这个字段（V173 才加）⇒ 用全格口径
            esc = d.get("escalated_cells") or []
        if esc and esc_round is None:
            esc_round = i + 1
        rows.append({"round": i + 1,
                     "kinds_before": [g["kind"] for g in before],
                     "acted": (acted[0].get("kind") if acted else None),
                     "verdict": (acted[0].get("verdict") if acted else None),
                     "n_esc": len(esc)})
        if not before and stop_on_empty:
            break
    log_rows = G.load(tmp / "goal_ledger.jsonl")
    return {"tag": tag, "rows": rows,
            "n_acted": sum(1 for r in rows if r["acted"]),
            "n_raise_proposed": sum(r["kinds_before"].count("RAISE") for r in rows),
            "esc_round": esc_round,
            "n_log_rows": len(log_rows),
            "ledger_kinds": [r.get("kind") for r in log_rows]}


def run_deploy(tag: str, tmp: pathlib.Path) -> dict:
    """真账本上的提议（⛔ 不动一笔，只读）。"""
    outp = tmp / f"dep_{tag}.json"
    cmd = [PY, str(ROOT / "run.py"), "--goals", "--goals-json", str(outp)]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    if r.returncode != 0:
        return {"tag": tag, "error": f"rc={r.returncode}", "stderr": r.stderr[-400:]}
    d = json.loads(outp.read_text(encoding="utf-8"))
    return {"tag": tag,
            "n_goals": len(d.get("goals") or []),
            "gids": sorted(g["gid"] for g in (d.get("goals") or [])),
            "n_esc": len(d.get("escalated_cells") or []),
            "esc_cells": sorted(d.get("escalated_cells") or []),
            "esc_in_suite": sorted(d.get("escalated_cells_in_suite") or []),
            "esc_outside": sorted(d.get("escalated_cells_outside") or [])}


def part_p3_p6():
    say()
    say("=" * 74)
    say("P3 行为（两臂真跑）/ P6 部署态")
    say("=" * 74)
    new_src = GOALS.read_bytes()
    h_new = sha(GOALS)
    old_src = subprocess.run(["git", "show", "HEAD:mw/goals.py"], cwd=str(ROOT),
                             capture_output=True).stdout
    if not old_src:
        raise SystemExit("取不到 HEAD 版的 mw/goals.py")
    tmp_old = pathlib.Path(tempfile.mkdtemp(prefix="v173_old_"))
    (tmp_old / "goals.py").write_bytes(old_src)
    h_old = sha(tmp_old / "goals.py")
    # ⚠️ 装置自踩（2026-11-17 复核时发现）：只换 goals.py 不换 run.py 会让旧臂**当场炸**——
    #    新 run.py 调 `G.split_escalations`，旧 goals.py 里没有这个函数。
    #    两臂是一**对**，必须同进同出；sha 两个都报（纪律 65）。
    runpy_src = RUNPY.read_bytes()
    h_runpy_new = sha(RUNPY)
    old_runpy_src = subprocess.run(["git", "show", "HEAD:run.py"], cwd=str(ROOT),
                                   capture_output=True).stdout
    if not old_runpy_src:
        raise SystemExit("取不到 HEAD 版的 run.py")
    (tmp_old / "run.py").write_bytes(old_runpy_src)
    h_runpy_old = sha(tmp_old / "run.py")
    say(f"  新臂 sha={h_new} + run.py {h_runpy_new[:8]}｜"
        f"旧臂（HEAD）sha={h_old} + run.py {h_runpy_old[:8]}｜相同={h_new == h_old}")

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="v173_arms_"))
    try:
        GOALS.write_bytes(old_src)
        RUNPY.write_bytes(old_runpy_src)
        assert sha(GOALS) == h_old, "换臂失败"
        old_arm = run_loop("old", _fresh(tmp, "old"))
    finally:
        GOALS.write_bytes(new_src)
        RUNPY.write_bytes(runpy_src)
    assert sha(GOALS) == h_new and sha(RUNPY) == h_runpy_new, "还原失败"
    new_arm = run_loop("new", _fresh(tmp, "new"))
    # 部署态：两臂各读一次（旧臂要再换一次文件对）
    tmp2 = pathlib.Path(tempfile.mkdtemp(prefix="v173_dep_"))
    (tmp2 / "old").mkdir()
    (tmp2 / "new").mkdir()
    try:
        GOALS.write_bytes(old_src)
        RUNPY.write_bytes(old_runpy_src)
        dep_old = run_deploy("old", tmp2 / "old")
    finally:
        GOALS.write_bytes(new_src)
        RUNPY.write_bytes(runpy_src)
    assert sha(GOALS) == h_new and sha(RUNPY) == h_runpy_new, "还原失败"
    dep_new = run_deploy("new", tmp2 / "new")

    for a in (old_arm, new_arm):
        say(f"\n  [{a['tag']}] 执行 {a['n_acted']} 轮｜提议 RAISE {a['n_raise_proposed']} 次"
            f"｜转运维首次出现在 round {a['esc_round']}｜账本 {a['n_log_rows']} 行"
            f"（{a['ledger_kinds']}）")
        for r in a["rows"]:
            say(f"      r{r['round']}: 提议 {r['kinds_before']} ⇒ 执行 {r['acted']} "
                f"({r['verdict']})｜转运维 {r['n_esc']}")
    ok3 = (new_arm["n_raise_proposed"] == 0 and old_arm["n_raise_proposed"] > 0
           and new_arm["n_acted"] < old_arm["n_acted"]
           and new_arm["esc_round"] is not None
           and old_arm["esc_round"] is not None
           and new_arm["esc_round"] < old_arm["esc_round"])
    check("P3", "结构判据让「永远闭不上的 RAISE」不再被提出：RAISE 提议归零、执行轮数下降、转运维提前",
          ok3,
          f"RAISE {old_arm['n_raise_proposed']}→{new_arm['n_raise_proposed']}｜"
          f"执行 {old_arm['n_acted']}→{new_arm['n_acted']}｜"
          f"转运维 round {old_arm['esc_round']}→{new_arm['esc_round']}")

    say(f"\n  部署态：旧臂 {dep_old['n_goals']} 条 / 转运维 {dep_old['n_esc']} 格"
        f"｜新臂 {dep_new['n_goals']} 条 / 转运维 {dep_new['n_esc']} 格 {dep_new['esc_cells']}")
    same = dep_old["gids"] == dep_new["gids"]
    say(f"  提议 gid 集合逐条相同：{same}")
    check("P6", "部署态：提议 40 条逐条相同（gid 集合相等）、escalations 0 格",
          dep_old["n_goals"] == dep_new["n_goals"] == 40 and same and dep_new["n_esc"] == 0,
          f"条数 {dep_old['n_goals']}/{dep_new['n_goals']}｜gid 相同={same}｜"
          f"转运维 {dep_old['n_esc']}/{dep_new['n_esc']} 格={dep_new['esc_cells']}"
          f"（其中套件内 {len(dep_new.get('esc_in_suite') or [])} 格）")
    READ["p3"] = {"old": old_arm, "new": new_arm}
    READ["p6"] = {"old": dep_old, "new": dep_new, "same_gids": same}

    # ---- 补充读数 1：复刻 V171 的跑法（提议 0 条也不 break），核对预注册里那组数字 ----
    say()
    say("  [补充，不入判据] 同装置但跑满 8 轮（不 break）—— 核对预注册照抄 V171 的 5/8/6/4")
    try:
        GOALS.write_bytes(old_src)
        RUNPY.write_bytes(old_runpy_src)
        assert sha(GOALS) == h_old
        old_nb = run_loop("old_nb", _fresh(tmp, "old_nb"), stop_on_empty=False)
    finally:
        GOALS.write_bytes(new_src)
        RUNPY.write_bytes(runpy_src)
    assert sha(GOALS) == h_new and sha(RUNPY) == h_runpy_new, "还原失败"
    new_nb = run_loop("new_nb", _fresh(tmp, "new_nb"), stop_on_empty=False)
    for a in (old_nb, new_nb):
        say(f"      [{a['tag']}] 执行 {a['n_acted']} 轮｜RAISE 提议 {a['n_raise_proposed']} 次"
            f"｜转运维 round {a['esc_round']}｜账本 {a['n_log_rows']} 行")
    READ["no_break"] = {"old": old_nb, "new": new_nb}

    # ---- 补充读数 2：P6 FAIL 暴露的真 bug —— 套件外的格会把 DONE 报成 IDLE ----
    sat = [{"task_id": "s1", "score": 1.0} for _ in range(3)]
    esc_outside = G.escalations([], [{"task_id": "k02", "score": 0.8}] * 3)
    st_mixed = G.state([], esc_outside, ["s1"], sat)[0]
    st_clean = G.state([], {}, ["s1"], sat)[0]
    say(f"      [补充] 套件 1 格全达标 + 账本外 1 格转运维：过滤前状态={st_mixed}"
        f"｜只留套件内={st_clean}")
    READ["p6b"] = {"mixed": st_mixed, "clean": st_clean}


def _fresh(base: pathlib.Path, tag: str) -> pathlib.Path:
    d = base / tag
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def main() -> int:
    say("V173 —— 「提分」这一类目标按定义不可能完成")
    say(f"  解释器 {PY}｜仓库 {ROOT}")
    part_p1()
    part_p2()
    part_p4_p5()
    part_p7()
    part_p3_p6()
    n_ok = sum(1 for v in VERDICTS if v["ok"])
    n_bad = len(VERDICTS) - n_ok
    say()
    say("=" * 74)
    say(f"汇总  PASS={n_ok}  FAIL={n_bad}")
    for v in VERDICTS:
        say(f"  {'✔' if v['ok'] else '✘'} {v['tag']}: {v['claim'][:52]}")
    say("=" * 74)
    here = pathlib.Path(__file__).resolve().parent
    (here / "readings_v173.json").write_text(
        json.dumps({"verdicts": VERDICTS, "readings": READ}, ensure_ascii=False,
                   indent=1, default=str), encoding="utf-8")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
