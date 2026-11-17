#!/usr/bin/env python3
"""V180 STAGE0 —— 「分辨力不足」该由哪一层的读数说话？

对应 `PREREG_v180.md`。**先于任何产品代码改动**。
零模型、零网络、真尺子（`bench/scorers/code_exec.py` 子进程）。

它只调既有读入口：
    scoreinfo.movability()                      （分数级）
    beh_pool_from_rows() + behavioral()         （行为级）
并把两条候选规则的触发集合与**独立算出来的**「存在异行为同分」对拍。

用法：python3.13 bench/evidence/l3_s268/probe_v180_stage0.py
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

from mw import scoreinfo as SI            # noqa: E402
from mw import goals as G                # noqa: E402
from mw.bench import Suite               # noqa: E402

import admit_sys                          # noqa: E402

SUITE = ROOT / "bench" / "tasks" / "sys_small.jsonl"
SCORER = ROOT / "bench" / "scorers" / "code_exec.py"
OUT = Path(__file__).with_name("readings_stage0_v180.json")
V179_ROWS = ROOT / "bench" / "evidence" / "l3_s267" / "readings_stage0_v179_run2.json"

CASES: list[dict] = []


def check(pid: str, desc: str, ok: bool, detail: str = "") -> bool:
    CASES.append({"id": pid, "desc": desc, "ok": bool(ok), "detail": detail})
    print(f"  {'✅' if ok else '❌'} {pid} {desc}" + (f" — {detail}" if detail else ""))
    return bool(ok)


# ════════════════════════════════════════════════════════════════
# 打分（与 V179 同一台装置）
# ════════════════════════════════════════════════════════════════
def score_one(task: dict, answer: str) -> dict:
    proc = subprocess.run([sys.executable, str(SCORER)],
                          input=json.dumps({"task": task, "answer": answer}),
                          capture_output=True, text=True, timeout=120)
    line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    if not line:
        return {"error": f"no stdout rc={proc.returncode}", "stderr": proc.stderr[-200:]}
    return json.loads(line)


def build_rows() -> list[dict]:
    suite = Suite.load(SUITE)
    rows: list[dict] = []
    for t in suite.tasks:
        d = t.as_dict()
        answers: list[tuple[str, str]] = [
            ("oracle", t.oracle), ("partial", t.partial), ("null", t.null)]
        answers += list(admit_sys.MUTANTS.get(t.id, []))
        for name, src in answers:
            r = score_one(d, src)
            row = {"task_id": t.id, "impl_sha": f"{t.id}:{name}", "score": r.get("score")}
            if r.get("vec") is not None:              # 只记**量到**的
                row["vec"] = r["vec"]
            rows.append(row)
    return rows


# ════════════════════════════════════════════════════════════════
# 两条候选规则（STAGE0 里只是**模拟**，实现后由 v180.py 用真函数复算）
# ════════════════════════════════════════════════════════════════
def r_score_fires(m: dict) -> bool:
    """R_score：分数级 COARSE ⇒ 尺子分辨力不足。"""
    return m.get("label") == SI.KIND_COARSE


def r_beh_fires(b: dict) -> bool:
    """R_beh：行为级 COARSE ∧ 行为类 ≥ MIN_BEHAVIORS。"""
    return (b.get("kind") == SI.KIND_COARSE
            and (b.get("n_behaviors") or 0) >= SI.MIN_BEHAVIORS)


def direct_tie(rows: list[dict], cell: str) -> tuple[bool, str]:
    """**独立路径**数「存在异行为同分」（⛔ 不调 behavioral()）：

    直接把本格 (vec, score) 按 score 分桶，看有没有一桶里装了两个不同的 vec。
    """
    by_score: dict[float, set[tuple]] = {}
    for r in rows:
        if r.get("task_id") != cell or "vec" not in r or r.get("score") is None:
            continue
        by_score.setdefault(float(r["score"]), set()).add(tuple(r["vec"]))
    ties = {s: vs for s, vs in by_score.items() if len(vs) > 1}
    show = "；".join(f"{s:g}←{len(vs)} 种行为" for s, vs in sorted(ties.items()))
    return bool(ties), show


# ════════════════════════════════════════════════════════════════
# 产品调用点静态扫描（死分支取证）
# ════════════════════════════════════════════════════════════════
def call_sites() -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for p in sorted((ROOT / "mw").glob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "repair_axes"):
                out.append((str(p.relative_to(ROOT)), node.lineno))
    return out


def main() -> int:
    t0 = time.time()
    print("V180 STAGE0 —— 「分辨力不足」由哪一层说话？")
    print(f"  证据目录 {OUT.parent}")

    rows = build_rows()
    cells = sorted({r["task_id"] for r in rows})
    print(f"\n## P1 池可重建（{len(cells)} 格 / {len(rows)} 行，{time.time()-t0:.1f}s）")
    old = json.loads(V179_ROWS.read_text(encoding="utf-8"))["rows"] if V179_ROWS.exists() else []
    def norm(rs):
        return [(r["task_id"], r["impl_sha"], r.get("score"),
                 tuple(r["vec"]) if "vec" in r else None) for r in rs]
    same_as_v179 = norm(rows) == norm(old)
    per_cell = {c: sum(1 for r in rows if r["task_id"] == c) for c in cells}
    check("P1", "6 格各 ≥4 行；且与 V179 那批**独立重打**逐行相同（装置确定性）",
          len(cells) == 6 and min(per_cell.values()) >= 4 and same_as_v179,
          f"每格 {per_cell}｜与 V179 行相同={same_as_v179}")

    print("\n## P2 死分支取证：产品里 `repair_axes` 的实参从哪来")
    sites = call_sites()
    for f, ln in sites:
        print(f"  调用点 {f}:{ln}")
    check("P2", "产品代码里 `repair_axes` 恰好 1 个调用点（`mw/goals.py`）",
          len(sites) == 1 and sites[0][0] == "mw/goals.py",
          f"{sites}")

    print("\n## P3 两条规则逐格对照（真池）")
    table: dict[str, dict] = {}
    for c in cells:
        m = SI.movability(rows, cell=c)
        pool, _ = SI.beh_pool_from_rows(rows, cell=c)
        b = SI.behavioral(pool) if pool else {"kind": SI.KIND_UNKNOWN,
                                              "reason": "NO_VEC", "claim": None}
        tie, tie_txt = direct_tie(rows, c)
        table[c] = {"score_label": m.get("label"), "beh_kind": b.get("kind"),
                    "beh_n": b.get("n_behaviors"), "tie": tie, "tie_txt": tie_txt,
                    "r_score": r_score_fires(m), "r_beh": r_beh_fires(b),
                    "score_claim": m.get("claim"), "beh_claim": b.get("claim")}
        print(f"  {c:<16} 分数级 {m.get('label'):<10} 行为级 {b.get('kind'):<10} "
              f"n_beh={b.get('n_behaviors')} 直接数出异行为同分={tie}"
              f"  R_score={r_score_fires(m)} R_beh={r_beh_fires(b)}")
        if tie_txt:
            print(f"  {'':<16} └ {tie_txt}")

    n_score = sum(1 for c in cells if table[c]["r_score"])
    n_beh = sum(1 for c in cells if table[c]["r_beh"])
    # 假正例：规则说要 RULER，但**直接数**没有异行为同分
    fp_score = [c for c in cells if table[c]["r_score"] and not table[c]["tie"]]
    fp_beh = [c for c in cells if table[c]["r_beh"] and not table[c]["tie"]]
    tie_cells = [c for c in cells if table[c]["tie"]]
    print(f"\n  R_score 触发 {n_score}/{len(cells)}，其中**没有**异行为同分的 {len(fp_score)}：{fp_score}")
    print(f"  R_beh   触发 {n_beh}/{len(cells)}，其中没有异行为同分的 {len(fp_beh)}：{fp_beh}")
    print(f"  直接数出「存在异行为同分」的格 {len(tie_cells)}：{tie_cells}")

    check("P4", "A：R_score 在有异行为同分的格之外也触发，假正例 ≥4", len(fp_score) >= 4,
          f"假正例 {len(fp_score)}/{n_score}")
    check("P5", "B：R_beh 的触发集合 == 直接数出的集合（不同计算路径），且 R_score 真包含它",
          set(c for c in cells if table[c]["r_beh"]) == set(tie_cells)
          and set(tie_cells) < set(c for c in cells if table[c]["r_score"]),
          f"R_beh={sorted(c for c in cells if table[c]['r_beh'])} 直接={tie_cells} "
          f"R_score={n_score} 格")

    print("\n## P6 C：部署上会不会触发（真账本有没有 vec）")
    from mw import config as cfg
    led = cfg.Paths().score_ledger
    dep_rows = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines() if x.strip()] \
        if led.exists() else []
    n_vec = sum(1 for r in dep_rows if isinstance(r, dict) and r.get("vec") is not None)
    dep_cells = sorted({r.get("task_id") for r in dep_rows if isinstance(r, dict)})
    print(f"  部署账本 {led}：{len(dep_rows)} 行 / {len(dep_cells)} 格 / 带 vec {n_vec} 行")
    dep_beh_fires = 0
    for c in dep_cells:
        pool, _ = SI.beh_pool_from_rows(dep_rows, cell=c)
        if pool and r_beh_fires(SI.behavioral(pool)):
            dep_beh_fires += 1
    print(f"  部署上 R_beh 会触发的格：{dep_beh_fires}")
    check("P6", "C：部署账本无 vec ⇒ R_beh 在部署上触发 0 格（有牙没猎物，照实报）",
          n_vec == 0 and dep_beh_fires == 0,
          f"vec 行 {n_vec}｜R_beh 触发 {dep_beh_fires}")

    print("\n## P7 overlay 的 claim 值域（环境断言，为 P2 的死分支补一条运行侧证据）")
    forced = [{"gid": "z", "cell": cells[0], "kind": "RAISE", "verdict": "NOT_MET",
               "reading": {"mean_score": 0.0}} for _ in range(3)]
    esc = G.escalations(forced, rows)
    claims = {c: (e.get("movability") or {}).get("claim") for c, e in esc.items()}
    print(f"  强制 1 格进 esc ⇒ overlay claim={claims}")
    check("P7", "强制进 esc 的格，overlay 的 claim 只能是 SCORE_UNMOVABLE 或 None —— "
                "**永远不是** BEHAVIOR_UNRESOLVED",
          all(v in (SI.CLAIM_UNMOVABLE, None) for v in claims.values()),
          f"{claims}")

    ok = sum(1 for c in CASES if c["ok"])
    print(f"\n## 汇总 {ok} PASS / {len(CASES)-ok} FAIL   （{time.time()-t0:.1f}s）")

    OUT.write_text(json.dumps({
        "prereg": "PREREG_v180.md", "stage": "STAGE0",
        "rows": rows,
        "per_cell": table,
        "n_cells": len(cells), "n_score_fires": n_score, "n_beh_fires": n_beh,
        "fp_score": fp_score, "fp_beh": fp_beh, "tie_cells": tie_cells,
        "deploy": {"ledger": str(led), "n_rows": len(dep_rows),
                   "n_cells": len(dep_cells), "n_vec": n_vec,
                   "r_beh_fires": dep_beh_fires},
        "call_sites": sites,
        "cases": CASES,
        "summary": {"n": len(CASES), "ok": ok, "pass": ok == len(CASES)},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  读数 {OUT}")
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
