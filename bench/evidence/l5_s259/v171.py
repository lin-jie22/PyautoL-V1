"""V171 主实验：把自己做不到写进账本（`RAISE` → `ESCALATE`）。

判据 Q1~Q6 见 `PREREG_v171.md`。分母一律写进字段名。

三件事分开报（纪律 64：已看 / 未看两个分母）：
  A. **真实装置**读数：8 轮两臂对照（`readings_loop_A_noesc.json` / `_B_esc.json`）
  B. **纯函数**判别力：构造 4 类账本喂 `escalations()`，看触发/不触发
  C. **CLI 表面**：`--goals` 的输出里「已转运维」一节在不在、条数对不对
"""
from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT))

import mw.goals as G  # noqa: E402

OUT: dict = {}
VERDICTS: list[tuple[str, bool | None, str]] = []


def say(t: str) -> None:
    print(f"\n{'=' * 74}\n{t}\n{'=' * 74}")


def verdict(tag: str, ok: bool | None, note: str) -> None:
    mark = "PASS" if ok is True else ("NA  " if ok is None else "FAIL")
    print(f"\n  [{mark}] {tag}  {note}")
    VERDICTS.append((tag, ok, note))


def row(gid, cell, kind, verdict_, mean, n_obs=4):
    """造一条目标账本行（与 `G.record()` 写出的形状逐字段相同）。"""
    return {"gid": gid, "kind": kind, "cell": cell, "verdict": verdict_,
            "reading": {"cell": cell, "n_obs": n_obs, "mean_score": mean},
            "criterion": [["mean_score", ">=", 1.0]], "source": "test"}


# ===========================================================================
# A. 真实装置：两臂对照
# ===========================================================================
def part_a() -> None:
    say("A. 真实装置 —— 8 轮两臂对照（同装置、同题、同桩，唯一差异是 escalate）")
    fa = HERE / "readings_loop_A_noesc.json"
    fb = HERE / "readings_loop_B_esc.json"
    if not (fa.exists() and fb.exists()):
        print("  ⛔ 缺对照读数，跳过（不是 PASS）")
        verdict("Q2", None, "缺 A/B 臂读数")
        return
    A = json.loads(fa.read_text())
    B = json.loads(fb.read_text())
    print(f"  A 臂（`20066a3` 原样，不启用）：{A['rounds']} 轮，RAISE 被提 "
          f"**{A['n_raise_total']}** 次｜mean 取值 {A['distinct_means']}")
    print(f"  B 臂（本轮代码，启用）：{B['rounds']} 轮，RAISE 被提 "
          f"**{B['n_raise_total']}** 次｜mean 取值 {B['distinct_means']}")

    # 逐轮对照表
    print(f"\n  {'轮':>2} {'A: 执行后提议':<22} {'A: RAISE':>8} {'B: 执行后提议':<22} {'B: RAISE':>8}")
    for ra, rb in zip(A["rows"], B["rows"]):
        print(f"  {ra['round']:>2} {str(ra['kinds_after']):<22} {ra['n_raise_after']:>8} "
              f"{str(rb['kinds_after']):<22} {rb['n_raise_after']:>8}")

    # 分界：B 臂从第几轮起归零
    zero_from = next((r["round"] for r in B["rows"] if r["n_raise_after"] == 0
                      and r["round"] > 3), None)
    print(f"\n  B 臂从第 **{zero_from}** 轮起 RAISE 归零"
          f"（该轮执行的是 fail#{[r['round'] for r in B['rows'] if r['round'] == zero_from]}"
          f"，累计 NOT_MET={sum(1 for r in B['rows'] if r['round'] <= zero_from and r['verdict'] == 'NOT_MET')} 次）")

    # 触发后（归零起）的提议数
    after = [r["n_raise_after"] for r in B["rows"] if zero_from and r["round"] >= zero_from]
    ctrl = [r["n_raise_after"] for r in A["rows"] if zero_from and r["round"] >= zero_from]
    print(f"  触发后的轮次：B 臂依次 {after}（合计 {sum(after)}）"
          f"｜同轮 A 臂依次 {ctrl}（合计 {sum(ctrl)}）")

    if not ctrl or sum(ctrl) == 0:
        verdict("Q2", None, "对照臂 A 在同样轮次也不重复 ⇒ 分母为 0，判 NA（纪律 69）")
    else:
        verdict("Q2", sum(after) == 0,
                f"触发后 RAISE 提议 B={sum(after)} 次 vs A={sum(ctrl)} 次"
                f"（分母 = 触发后 {len(after)} 轮）")
    OUT["a"] = {"A": {k: A[k] for k in ("rounds", "n_raise_total", "distinct_means")},
                "B": {k: B[k] for k in ("rounds", "n_raise_total", "distinct_means")},
                "zero_from_round": zero_from,
                "after_trigger_B": after, "after_trigger_A": ctrl}


# ===========================================================================
# B. 纯函数判别力：构造 4 类账本
# ===========================================================================
def part_b() -> None:
    say("B. 纯函数判别力 —— 同一函数喂 4 类构造账本（分母 = 每类 1 格）")
    obs_low = [{"task_id": "c1", "score": 0.0}] * 3
    cases = {
        "① 3 次 NOT_MET + mean 逐位相同（该转）": [
            row("g1", "c1", "RAISE", "NOT_MET", 0.0),
            row("g1", "c1", "RAISE", "NOT_MET", 0.0),
            row("g1", "c1", "RAISE", "NOT_MET", 0.0),
        ],
        "② 3 次 NOT_MET 但 mean 在涨（不该转）": [
            row("g2", "c2", "RAISE", "NOT_MET", 0.2),
            row("g2", "c2", "RAISE", "NOT_MET", 0.4),
            row("g2", "c2", "RAISE", "NOT_MET", 0.6),
        ],
        "③ 只 2 次 NOT_MET（试不够，不该转）": [
            row("g3", "c3", "RAISE", "NOT_MET", 0.0),
            row("g3", "c3", "RAISE", "NOT_MET", 0.0),
        ],
        "④ NOT_MET 总数 3 次但末尾只连 2 次（该按连续算，不转）": [
            row("g4", "c4", "RAISE", "NOT_MET", 0.0),
            row("g4", "c4", "RAISE", "MET", 1.0),
            row("g4", "c4", "RAISE", "NOT_MET", 0.0),
            row("g4", "c4", "RAISE", "NOT_MET", 0.0),
        ],
    }
    res = {}
    for name, rows in cases.items():
        esc = G.escalations(rows)
        res[name] = sorted(esc)
        print(f"  {name:<46} ⇒ 触发 {sorted(esc) or '无'}")

    q1 = res["① 3 次 NOT_MET + mean 逐位相同（该转）"] == ["c1"]
    q3 = res["② 3 次 NOT_MET 但 mean 在涨（不该转）"] == []
    extra = (res["③ 只 2 次 NOT_MET（试不够，不该转）"] == []
             and res["④ NOT_MET 总数 3 次但末尾只连 2 次（该按连续算，不转）"] == [])
    verdict("Q1", q1, f"① 触发 = {res['① 3 次 NOT_MET + mean 逐位相同（该转）']}"
                      f"（分母 1 格，期望 ['c1']）")
    verdict("Q3", q3, f"② 读数在动（0.2→0.4→0.6）触发 = "
                      f"{res['② 3 次 NOT_MET 但 mean 在涨（不该转）']}（期望 []）")
    print(f"\n  附（不入 6 条判据）：③ 试不够 触发={res['③ 只 2 次 NOT_MET（试不够，不该转）']}"
          f"｜④ 末尾只连 2 次 触发={res['④ NOT_MET 总数 3 次但末尾只连 2 次（该按连续算，不转）']}"
          f" ⇒ {'两条都对' if extra else '**有错：必须在 RESULTS 里如实登记**'}")

    # ── Q4 可撤销：删掉第 3 行，同一个函数必须不再触发 ──────────────────
    rows = cases["① 3 次 NOT_MET + mean 逐位相同（该转）"]
    before = sorted(G.escalations(rows))
    after = sorted(G.escalations(rows[:-1]))
    print(f"\n  Q4 可撤销：3 行 ⇒ {before}｜删掉 1 行 ⇒ {after}")
    verdict("Q4", before == ["c1"] and after == [],
            f"删 1 行后 {after}（期望 []）")

    # ── fail-open：现在已达标 ⇒ 自动消失 ────────────────────────────────
    obs_ok = [{"task_id": "c1", "score": 1.0}] * 3
    fo = sorted(G.escalations(rows, obs_ok))
    print(f"  fail-open：同一份账本 + 「现在已达标」的观测 ⇒ {fo or '无'}（期望无）")

    OUT["b"] = {"cases": res, "q1": q1, "q3": q3, "extra": extra,
                "revoke_before": before, "revoke_after": after, "fail_open": fo}


# ===========================================================================
# C. CLI 表面 + Q5 留痕
# ===========================================================================
def part_c() -> None:
    say("C. CLI 表面 —— `--goals` 的输出里「已转运维」一节在不在、条数对不对（Q5）")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="v171_cli_"))
    sd = tmp / "tasks"
    sd.mkdir()
    src = ROOT / "bench" / "tasks" / "coding_small.jsonl"
    line = next(l for l in src.read_text(encoding="utf-8").splitlines()
                if json.loads(l)["id"] == "t06_rle")
    (sd / "one.jsonl").write_text(line + "\n", encoding="utf-8")
    # 预置账本：3 次 NOT_MET、mean 全 0.0 ⇒ 该格已转运维
    log = tmp / "goal_ledger.jsonl"
    with open(log, "w", encoding="utf-8") as f:
        for _ in range(3):
            f.write(json.dumps(row("gX", "t06_rle", "RAISE", "NOT_MET", 0.0),
                               ensure_ascii=False) + "\n")
    with open(tmp / "score_ledger.jsonl", "w", encoding="utf-8") as f:
        for _ in range(4):
            f.write(json.dumps({"task_id": "t06_rle", "score": 0.0,
                                "family": "one", "session": 1}) + "\n")

    cmd = [sys.executable, str(ROOT / "run.py"), "--goals", "--offline",
           "--goals-suite-dir", str(sd), "--goals-log", str(log),
           "--goals-obs", str(tmp / "goal_obs.jsonl"),
           "--goals-json", str(tmp / "g.json")]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT),
                       env={**__import__("os").environ, "V1_STATE_DIR": str(tmp)})
    out = r.stdout
    has_section = "已转运维" in out
    # 从输出里抽那一节的行
    sec = []
    if has_section:
        lines = out.splitlines()
        i = next(k for k, l in enumerate(lines) if "已转运维" in l)
        for l in lines[i + 1:]:
            if not l.strip() or l.startswith("---"):
                break
            sec.append(l.strip())
    d = json.loads((tmp / "g.json").read_text(encoding="utf-8")) if (tmp / "g.json").exists() else {}
    esc_cells = d.get("escalated_cells", [])
    n_goals = len(d.get("goals", []))
    print(f"  输出含「已转运维」一节：{has_section}")
    print(f"  该节内容（{len(sec)} 行）：")
    for l in sec:
        print(f"    {l}")
    print(f"  json：escalated_cells={esc_cells}｜提议 {n_goals} 条（该格不该出现在提议里）")

    ok = (has_section and esc_cells == ["t06_rle"] and n_goals == 0
          and len(sec) == len(esc_cells))
    verdict("Q5", ok, f"输出有该节={has_section}｜json 里 {len(esc_cells)} 格｜"
                      f"该节 {len(sec)} 行｜提议 {n_goals} 条（期望 0）")
    OUT["c"] = {"has_section": has_section, "section_lines": sec,
                "escalated_cells": esc_cells, "n_goals": n_goals, "rc": r.returncode}


# ===========================================================================
# D. Q6 不新增可调参数
# ===========================================================================
def part_d() -> None:
    say("D. Q6 —— `mw/goals.py` 的模块级常量集合不许变（AST）")
    tree = ast.parse((ROOT / "mw" / "goals.py").read_text(encoding="utf-8"))
    consts = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    consts.append(t.id)
    print(f"  模块级常量：{sorted(consts)}")
    print(f"  本轮之前应有的：['KINDS', 'MIN_OBS', 'TARGET']")
    extra = sorted(set(consts) - {"KINDS", "MIN_OBS", "TARGET"})
    # 默认参数也不许出现新的阈值字面量
    defaults = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.args.defaults + [x for x in node.args.kw_defaults if x]:
                if isinstance(d, ast.Constant) and isinstance(d.value, (int, float)):
                    defaults.append((node.name, d.value))
    # ⚠️ 自踩第 1 条：首版把「函数默认参数里的数值字面量」也当成判据的一部分，
    #    于是把 **V170 就有的** `render(limit=12)` 判成本轮新增 ⇒ Q6 当场红。
    #    但预注册 Q6 只写「**模块级常量集合**不变」——那条检查是我实现时自己加的，
    #    **超出了预注册作用域**（纪律 2：判据不许越出预注册）。所以从判据里删掉，
    #    只留作补充读数（⛔ 不入 PASS/FAIL）。
    print(f"  [补充读数，不属于 Q6] 函数默认参数里的数值字面量：{defaults or '无'}"
          f"（`render(limit=12)` 是 V170 就有的显示宽度，git 可查）")
    ok = not extra
    verdict("Q6", ok, f"模块级常量 {sorted(consts)}｜新增 {extra or '无'}（分母 = 常量集合）")
    OUT["d"] = {"consts": sorted(consts), "extra": extra, "defaults_note": defaults}


# ===========================================================================
# E. 部署形态（补充读数，不入 6 条判据）：全部饱和时它会说什么
# ===========================================================================
def part_e() -> None:
    say("E. 补充：40 题全部饱和（各测 4 次、均分全 0.0、各连判 3 次 NOT_MET）")
    import os
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="v171_sat_"))
    sd = tmp / "tasks"
    sd.mkdir()
    for f in sorted((ROOT / "bench" / "tasks").glob("*.jsonl")):
        (sd / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    ids = [t for v in G.scan_suites(sd).values() for t in v]
    log = tmp / "goal_ledger.jsonl"
    with open(log, "w", encoding="utf-8") as f:
        for c in ids:
            for _ in range(3):
                f.write(json.dumps(row("g_" + c, c, "RAISE", "NOT_MET", 0.0),
                                   ensure_ascii=False) + "\n")
    with open(tmp / "score_ledger.jsonl", "w", encoding="utf-8") as f:
        for c in ids:
            for _ in range(4):
                f.write(json.dumps({"task_id": c, "score": 0.0, "family": "x",
                                    "session": 1}) + "\n")
    cmd = [sys.executable, str(ROOT / "run.py"), "--goals", "--offline",
           "--goals-suite-dir", str(sd), "--goals-log", str(log),
           "--goals-obs", str(tmp / "goal_obs.jsonl"),
           "--goals-json", str(tmp / "g.json")]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT),
                       env={**os.environ, "V1_STATE_DIR": str(tmp)})
    d = json.loads((tmp / "g.json").read_text(encoding="utf-8"))
    n_goals = len(d.get("goals", []))
    esc = d.get("escalated_cells", [])
    line = next((l for l in r.stdout.splitlines() if "判据行" in l), "")
    print(f"  套件 {len(ids)} 题｜账本 {len(ids)*4} 行｜目标账本 {len(ids)*3} 行")
    print(f"  提议 **{n_goals}** 条｜转运维 **{len(esc)}** 格")
    print(f"  {line.strip()}")
    print(f"  ⇒ 它说得出来的话：**测量路径已用尽，{len(esc)} 格需换能力** —— 不是「做完了」")
    OUT["e"] = {"n_ids": len(ids), "n_goals": n_goals, "n_escalated": len(esc)}


def main() -> int:
    part_a()
    part_b()
    part_c()
    part_d()
    part_e()

    print("\n" + "=" * 74)
    n_ok = sum(1 for _t, o, _n in VERDICTS if o is True)
    n_na = sum(1 for _t, o, _n in VERDICTS if o is None)
    n_bad = sum(1 for _t, o, _n in VERDICTS if o is False)
    print(f"V171 判据：PASS {n_ok} / FAIL {n_bad} / NA {n_na}")
    for t, o, n in VERDICTS:
        print(f"  [{'PASS' if o is True else ('NA' if o is None else 'FAIL')}] {t}  {n}")
    print("=" * 74)
    OUT["verdicts"] = [{"tag": t, "ok": o, "note": n} for t, o, n in VERDICTS]
    OUT["summary"] = {"pass": n_ok, "fail": n_bad, "na": n_na}
    (HERE / "readings_v171.json").write_text(
        json.dumps(OUT, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"落盘 {HERE.name}/readings_v171.json")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
