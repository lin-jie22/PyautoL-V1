#!/usr/bin/env python3
"""V172 主实验：把「无事可做」与「做完了」分成两个量。

判据 P1~P8 见 `PREREG_v172.md`（写于本文件首次执行之前）。

三件事分开报（纪律 64：已看 / 未看两个分母）：
  A. **纯函数**臂：5 个构造账本喂 `state()`，看五个名字是否各就其位
  B. **CLI** 臂：真 `run.py --goals` 跑在临时 state 上，看输出是否换了话
  C. **反例**臂：把 `state()` 换成恒返回 WORK 的变异体，看 8 条判据掉几条

⛔ 生产 `state/` 只读：所有构造账本写在 `tempfile.mkdtemp()` 下，
   CLI 臂全部用 `--ledger/--goals-log/--goals-obs` 显式指过去。
"""
from __future__ import annotations

import ast
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT))

import mw.goals as G  # noqa: E402

OUT: dict = {}
VERDICTS: list[tuple[str, bool | None, str]] = []
MIN_OBS, TARGET = G.MIN_OBS, G.TARGET


def say(t: str) -> None:
    print(f"\n{'=' * 74}\n{t}\n{'=' * 74}")


def verdict(tag: str, ok: bool | None, note: str) -> None:
    mark = "PASS" if ok is True else ("NA  " if ok is None else "FAIL")
    print(f"\n  [{mark}] {tag}  {note}")
    VERDICTS.append((tag, ok, note))


def rrow(gid, cell, kind, v, mean, n_obs=4):
    """与 `G.record()` 写出的形状逐字段相同。"""
    return {"gid": gid, "kind": kind, "cell": cell, "verdict": v,
            "reading": {"cell": cell, "n_obs": n_obs, "mean_score": mean},
            "criterion": [["mean_score", ">=", 1.0]], "source": "test"}


def suite_ids() -> list[str]:
    """真套件里有多少题（⛔ 只读 id，不读题面）。"""
    return [t for v in G.scan_suites(ROOT / "bench" / "tasks").values() for t in v]


# ===========================================================================
# A. 纯函数臂
# ===========================================================================
def build_arms(ids: list[str]) -> dict:
    """造 5 个臂。⛔ 每个臂都是纯数据，不跑模型、不读题面。"""
    arms: dict = {}

    # ① 全饱和：40 格 × 3 次 0.0，且每格连判 3 次 NOT_MET
    obs_sat = [{"task_id": c, "score": 0.0, "session": i, "source": "test"}
               for c in ids for i in range(3)]
    g_sat = {(g.cell): g for g in G.propose(obs_sat, ids)}
    log_sat = [rrow(g_sat[c].gid, c, "RAISE", "NOT_MET", 0.0, 3) for c in ids for _ in range(3)]
    esc_sat = G.escalations(log_sat, obs_sat)
    arms["A_sat"] = {"obs": obs_sat, "ids": ids, "log": log_sat, "esc": esc_sat,
                     "goals": G.propose(obs_sat, ids, escalated=list(esc_sat))}

    # ② 全达标：40 格 × 3 次 1.0，无日志
    obs_done = [{"task_id": c, "score": 1.0, "session": i, "source": "test"}
                for c in ids for i in range(3)]
    arms["A_done"] = {"obs": obs_done, "ids": ids, "log": [], "esc": {},
                      "goals": G.propose(obs_done, ids)}

    # ③ 被 done 挡住：一格 0.5 × 3 次，且它那条 RAISE 被判过 MET
    obs_st = [{"task_id": ids[0], "score": 0.5, "session": i, "source": "test"}
              for i in range(3)]
    g_st = G.propose(obs_st, [ids[0]])[0]
    log_st = [rrow(g_st.gid, ids[0], "RAISE", "MET", 1.0, 3)]
    arms["A_stale"] = {"obs": obs_st, "ids": [ids[0]], "log": log_st, "esc": {},
                       "goals": G.propose(obs_st, [ids[0]], done=[g_st.gid])}

    # ④ 套件 0 题，但历史账本里有转运维格
    obs_ep = [{"task_id": "z", "score": 0.0, "session": i, "source": "test"}
              for i in range(3)]
    g_ep = G.propose(obs_ep, ["z"])[0]
    log_ep = [rrow(g_ep.gid, "z", "RAISE", "NOT_MET", 0.0, 3) for _ in range(3)]
    arms["A_empty"] = {"obs": obs_ep, "ids": [], "log": log_ep,
                       "esc": G.escalations(log_ep, obs_ep), "goals": []}

    # ⑤ 有活：一格 0 次观测
    arms["A_work"] = {"obs": [], "ids": [ids[0]], "log": [], "esc": {},
                      "goals": G.propose([], [ids[0]])}

    for name, a in arms.items():
        a["state"] = G.state(a["goals"], a["esc"], a["ids"], a["obs"])
    return arms


def part_a(arms: dict) -> None:
    say("A. 纯函数臂 —— 五个构造输入，五个名字")
    print(f"  套件规模：{len(arms['A_sat']['ids'])} 题（只取 id，不读题面）")
    print(f"\n  {'臂':<9} {'提议':>4} {'转运维':>6} {'达标格':>6} {'名字':<7} 渲染")
    for n, a in arms.items():
        nm, det = a["state"]
        print(f"  {n:<9} {det['n_goals']:>4} {det['n_esc']:>6} "
              f"{det['n_sat']}/{det['n_cells']:<4} {nm:<7} {G.render_state(nm, det)}")

    expected = {"A_sat": "IDLE", "A_done": "DONE", "A_stale": "STALE",
                "A_empty": "EMPTY", "A_work": "WORK"}
    got = {n: a["state"][0] for n, a in arms.items()}
    names = list(got.values())
    OUT["arms"] = {n: {"name": got[n], "detail": a["state"][1]} for n, a in arms.items()}

    # ── P1 ───────────────────────────────────────────────────────────────
    distinct = len(set(names))
    verdict("P1", names.count("UNKNOWN_STATE") == 0 and distinct == 5
            and got == expected,
            f"分母=5 个构造输入｜不同名字 {distinct}/5｜逐个对得上={got == expected}"
            f"｜实测 {got}")

    # ── P2 ───────────────────────────────────────────────────────────────
    verdict("P2", got["A_sat"] == "IDLE" and got["A_done"] == "DONE"
            and got["A_sat"] != got["A_done"],
            f"分母=2 臂×{len(arms['A_sat']['ids'])} 格｜全饱和={got['A_sat']}"
            f"（转运维 {arms['A_sat']['state'][1]['n_esc']} 格）"
            f"｜全达标={got['A_done']}（达标 {arms['A_done']['state'][1]['n_sat']} 格）"
            f"｜互不相同={got['A_sat'] != got['A_done']}")

    # ── P3 ───────────────────────────────────────────────────────────────
    nm3, det3 = arms["A_stale"]["state"]
    blk = det3["blocked"]
    verdict("P3", nm3 == "STALE" and len(blk) == 1 and blk[0]["cell"] == arms["A_stale"]["ids"][0],
            f"分母=1 个构造格｜名字={nm3}｜blocked 长度={len(blk)}"
            f"｜点名={[b['cell'] for b in blk]}")

    # ── P4 ───────────────────────────────────────────────────────────────
    nm4, det4 = arms["A_empty"]["state"]
    verdict("P4", nm4 == "EMPTY" and det4["n_esc"] > 0 and det4["n_cells"] == 0,
            f"分母=1 个构造｜名字={nm4}｜套件格数={det4['n_cells']}"
            f"｜账本里的转运维格={det4['n_esc']}（⇒ 若按『转运维>0』就判 IDLE，那是错的）")

    # ── P5 可撤销 ─────────────────────────────────────────────────────────
    sat = arms["A_sat"]
    c0 = sat["ids"][0]
    cut = [r for r in sat["log"] if r["cell"] == c0][-1]
    log2 = [r for r in sat["log"] if r is not cut]
    esc2 = G.escalations(log2, sat["obs"])
    goals2 = G.propose(sat["obs"], sat["ids"], escalated=list(esc2))
    nm5, det5 = G.state(goals2, esc2, sat["ids"], sat["obs"])
    others = [c for c in sat["ids"] if c != c0]
    kept = all(c in esc2 for c in others)
    verdict("P5", nm5 == "WORK" and det5["n_esc"] == len(sat["ids"]) - 1 and kept,
            f"分母={len(sat['ids'])} 格｜删 1 行后：名字={nm5}｜转运维 "
            f"{det5['n_esc']}‹{sat['state'][1]['n_esc']}｜其余格仍转运维={kept}"
            f"｜提议 {det5['n_goals']} 条（该格回到 RAISE）")

    # ── P7 无新常量 ────────────────────────────────────────────────────────
    tree = ast.parse((ROOT / "mw" / "goals.py").read_text(encoding="utf-8"))
    consts = sorted(t.id for n in tree.body if isinstance(n, ast.Assign)
                    for t in n.targets if isinstance(t, ast.Name) and t.id.isupper())
    new_defaults: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in n.args.defaults + [d for d in n.args.kw_defaults if d is not None]:
                if isinstance(d, ast.Constant) and isinstance(d.value, (int, float)) \
                        and not isinstance(d.value, bool) and n.name == "state":
                    new_defaults.append(f"{n.name}:{d.value}")
    verdict("P7", consts == ["KINDS", "MIN_OBS", "TARGET"] and not new_defaults,
            f"模块级常量={consts}｜`state()` 里的数值默认值={new_defaults or '无'}")
    OUT["consts"] = consts

    # ── P8 反例臂 ─────────────────────────────────────────────────────────
    def mut_state(*_a, **_k):
        return "WORK", {"n_goals": 999, "n_esc": 0, "n_cells": 0, "n_sat": 0,
                        "n_obs": 0, "escalated": [], "blocked": []}

    m = {n: mut_state() for n in arms}
    mnames = [v[0] for v in m.values()]
    p1m = len(set(mnames)) == 5 and {n: v[0] for n, v in m.items()} == expected
    p2m = m["A_sat"][0] == "IDLE" and m["A_done"][0] != m["A_sat"][0]
    p3m = m["A_stale"][0] == "STALE" and len(m["A_stale"][1]["blocked"]) == 1
    p4m = m["A_empty"][0] == "EMPTY" and m["A_empty"][1]["n_esc"] > 0
    dead = [k for k, v in (("P1", p1m), ("P2", p2m), ("P3", p3m), ("P4", p4m)) if not v]
    verdict("P8", dead == ["P1", "P2", "P3", "P4"],
            f"分母=4 条（P1~P4）｜恒返回 WORK 的变异体上 FAIL 的条数={len(dead)}/4"
            f"｜{dead}（⇒ 这 4 条判的是判别力，不是常量）")

    OUT["p8_dead"] = dead


# ===========================================================================
# B. CLI 臂
# ===========================================================================
def _sha_dir(p: pathlib.Path) -> dict:
    out = {}
    for f in sorted(p.glob("*")):
        if f.is_file():
            out[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
    return out


def _write_ledger(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")


def run_cli(tmp: pathlib.Path, tag: str, ledger, log, obs_rows, suite_dir,
            ledger_rows=None, log_rows=None) -> str:
    d = tmp / tag
    d.mkdir(parents=True, exist_ok=True)
    led = d / "score_ledger.jsonl"
    if ledger_rows is not None:
        _write_ledger(led, ledger_rows)
    elif ledger is not None:
        shutil.copy(ledger, led)
    else:
        led.write_text("", encoding="utf-8")
    lg = d / "goal_ledger.jsonl"
    _write_ledger(lg, log_rows or [])
    ob = d / "goal_obs.jsonl"
    ob.write_text("", encoding="utf-8")
    cmd = [sys.executable, str(ROOT / "run.py"), "--goals",
           "--ledger", str(led), "--goals-log", str(lg), "--goals-obs", str(ob),
           "--goals-suite-dir", str(suite_dir), "--goals-json", str(d / "out.json")]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    txt = r.stdout + r.stderr
    print(f"\n  ── {tag}（EXIT={r.returncode}）──")
    for line in r.stdout.splitlines():
        if line.startswith(("  账本", "  观测", "  套件", "  提议", "  状态", "  判据行",
                            "（0 条", "--- 已转运维")):
            print(f"    {line}")
    if r.returncode != 0:
        print(f"    ⛔ stderr: {r.stderr.strip()[:400]}")
    OUT.setdefault("cli", {})[tag] = {
        "exit": r.returncode,
        "state_line": next((ln.strip() for ln in r.stdout.splitlines()
                            if ln.startswith("  状态")), ""),
        "has_old": "（没有可提的目标" in txt,
        "json": json.loads((d / "out.json").read_text(encoding="utf-8"))
        if (d / "out.json").exists() else {},
    }
    return txt


def part_b(tmp: pathlib.Path, arms: dict) -> None:
    say("B. CLI 臂 —— `run.py --goals` 在临时 state 上（⛔ 不碰生产 state/）")
    prod = ROOT / "state"
    before = _sha_dir(prod)

    real_led = prod / "score_ledger.jsonl"
    real_suite = ROOT / "bench" / "tasks"

    # B_real：真账本 + 真套件
    t_real = run_cli(tmp, "B_real", real_led, None, None, real_suite)
    # B_idle：全饱和账本 + 40 格各 3 条 NOT_MET 日志
    sat = arms["A_sat"]
    t_idle = run_cli(tmp, "B_idle", None, None, None, real_suite,
                     ledger_rows=sat["obs"], log_rows=sat["log"])
    # B_done：全达标账本，无日志
    t_done = run_cli(tmp, "B_done", None, None, None, real_suite,
                     ledger_rows=arms["A_done"]["obs"], log_rows=[])
    # B_empty：空套件目录 + 有转运维格的历史日志
    empty_suite = tmp / "empty_suite"
    empty_suite.mkdir(parents=True, exist_ok=True)
    t_empty = run_cli(tmp, "B_empty", None, None, None, empty_suite,
                      ledger_rows=arms["A_empty"]["obs"], log_rows=arms["A_empty"]["log"])

    after = _sha_dir(prod)
    print(f"\n  生产 state/ 指纹：执行前 {len(before)} 个文件，"
          f"逐字节未变={before == after}")
    OUT["prod_untouched"] = before == after

    st = {k: v["state_line"] for k, v in OUT["cli"].items()}
    old = {k: v["has_old"] for k, v in OUT["cli"].items()}

    # ── P6 ───────────────────────────────────────────────────────────────
    ok6 = (all(("状态" in s and ("IDLE" in s or "WORK" in s or "DONE" in s
                                 or "EMPTY" in s or "STALE" in s)) for s in st.values())
           and not any(old.values())
           and "IDLE" in st["B_idle"] and "IDLE" not in st["B_done"]
           and "DONE" in st["B_done"] and "EMPTY" in st["B_empty"])
    verdict("P6", ok6, f"分母=4 份输出｜状态行={st}｜旧文案仍在={old}")
    OUT["old_text_gone"] = not any(old.values())

    print(f"\n  ⛔ 旧文案「（没有可提的目标：…或该做的都做完了）」在 4 份输出里"
          f"出现 {sum(old.values())} 次")
    if "（0 条 —— 原因见「状态」行" in t_idle:
        print("     新文案「（0 条 —— 原因见「状态」行；⛔ 不在这里猜）」已就位")


# ===========================================================================
# 汇总
# ===========================================================================
# ===========================================================================
# C. 补充读数（⛔ 不入判据：预注册里没有这一条，事后补的读数只能当读数）
# ===========================================================================
def part_c(arms: dict) -> None:
    say("C. 补充读数 —— 「旧语义」变异体：提议 0 条 ⇒ 一律 DONE")
    print("  这是**这轮要防的那个 bug 本身**：把「没有目标」当成「做完了」。")
    print("  ⛔ 不入判据（预注册的 P8 只写了恒返回 WORK 那个变异体）。")

    def old_state(goals, _esc, _ids, _obs, **_k):
        det = {"n_goals": len(goals), "n_esc": len(_esc), "n_cells": len(_ids),
               "n_sat": 0, "n_obs": len(_obs), "escalated": sorted(_esc), "blocked": []}
        return ("DONE" if not goals else "WORK"), det

    names = {n: old_state(a["goals"], a["esc"], a["ids"], a["obs"])[0]
             for n, a in arms.items()}
    expected = {"A_sat": "IDLE", "A_done": "DONE", "A_stale": "STALE",
                "A_empty": "EMPTY", "A_work": "WORK"}
    wrong = {n: (names[n], expected[n]) for n in names if names[n] != expected[n]}
    print(f"\n  旧语义下五个臂的名字：{names}")
    print(f"  ⇒ 说错的臂 {len(wrong)}/5：{ {k: f'{v[0]}≠{v[1]}' for k, v in wrong.items()} }")
    print(f"  ⇒ 尤其是 `A_sat`（40 格全饱和）会被报成 **{names['A_sat']}**，"
          f"而它真实的成因是「测量已用尽」—— 这正是本轮要消掉的那句话。")
    OUT["old_semantics_wrong"] = {k: list(v) for k, v in wrong.items()}


def main() -> int:
    say("V172 —— 把「无事可做」与「做完了」分成两个量")
    ids = suite_ids()
    print(f"  真套件 id 数：{len(ids)}（{', '.join(ids[:4])} …）")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="v1_v172_"))
    print(f"  临时目录：{tmp}")

    arms = build_arms(ids)
    part_a(arms)
    part_b(tmp, arms)
    part_c(arms)

    OUT["verdicts"] = [{"tag": t, "ok": o, "note": n} for t, o, n in VERDICTS]
    OUT["n_pass"] = sum(1 for _t, o, _n in VERDICTS if o is True)
    OUT["n_fail"] = sum(1 for _t, o, _n in VERDICTS if o is False)
    OUT["n_na"] = sum(1 for _t, o, _n in VERDICTS if o is None)

    say("判据汇总")
    for t, o, n in VERDICTS:
        print(f"  {'PASS' if o is True else ('NA  ' if o is None else 'FAIL')}  {t}  {n[:96]}")
    print(f"\n  判据 {OUT['n_pass']}/{len(VERDICTS)}（FAIL={OUT['n_fail']} NA={OUT['n_na']}）")

    (HERE / "readings_v172.json").write_text(
        json.dumps(OUT, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"  读数落盘：{HERE / 'readings_v172.json'}")
    print(f"  临时目录留着供复查：{tmp}")
    return 1 if OUT["n_fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
