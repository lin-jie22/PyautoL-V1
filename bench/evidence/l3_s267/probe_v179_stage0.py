#!/usr/bin/env python3
"""V179 STAGE0 —— 修复轴「消费」能不能落地：先量两层诊断会不会给出不同的轴。

对应 `PREREG_v179.md`。零模型、零网络、真尺子（`bench/scorers/code_exec.py` 子进程）。

它**不改任何产品代码**：只调既有读入口
    scoreinfo.movability()        （分数级）
    beh_pool_from_rows()+behavioral()（行为级）
    goals.repair_axes()           （轴）
把同一批账本行喂进两条通道，看轴会不会因此不同。

用法：python3.13 bench/evidence/l3_s267/probe_v179_stage0.py
"""
from __future__ import annotations

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
OUT = Path(__file__).with_name("readings_stage0_v179.json")

CASES: list[dict] = []


def check(pid: str, desc: str, ok: bool, detail: str = "") -> bool:
    CASES.append({"id": pid, "desc": desc, "ok": bool(ok), "detail": detail})
    print(f"  {'✅' if ok else '❌'} {pid} {desc}" + (f" — {detail}" if detail else ""))
    return bool(ok)


# ════════════════════════════════════════════════════════════════
# 打分：一次子进程拿 (score, vec)
# ════════════════════════════════════════════════════════════════
def score_one(task: dict, answer: str) -> dict:
    proc = subprocess.run([sys.executable, str(SCORER)],
                          input=json.dumps({"task": task, "answer": answer}),
                          capture_output=True, text=True, timeout=120)
    line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    if not line:
        return {"error": f"no stdout rc={proc.returncode}", "stderr": proc.stderr[-200:]}
    d = json.loads(line)
    return {"score": d.get("score"), "vec": d.get("vec"), "passed": d.get("passed")}


def build_rows() -> list[dict]:
    """6 题 × (oracle/null/partial + 变异体) ⇒ 账本行（带 vec）。"""
    suite = Suite.load(SUITE)
    rows: list[dict] = []
    for t in suite.tasks:
        d = t.as_dict()
        answers: list[tuple[str, str]] = [
            ("oracle", t.oracle), ("partial", t.partial), ("null", t.null)]
        answers += list(admit_sys.MUTANTS.get(t.id, []))
        for name, src in answers:
            r = score_one(d, src)
            vec = r.get("vec")
            row = {"task_id": t.id, "impl_sha": f"{t.id}:{name}",
                   "score": r.get("score")}
            if vec is not None:                     # 只记**量到**的（缺席不许当零）
                row["vec"] = vec
            rows.append(row)
    return rows


# ════════════════════════════════════════════════════════════════
# 两条通道
# ════════════════════════════════════════════════════════════════
def two_layers(rows: list[dict], cell: str) -> dict:
    m = SI.movability(rows, cell=cell)
    pool, counts = SI.beh_pool_from_rows(rows, cell=cell)
    b = SI.behavioral(pool) if pool else {"kind": SI.KIND_UNKNOWN,
                                          "reason": "NO_VEC", "claim": None}
    return {"score": m, "beh": b, "counts": counts, "n_pool": len(pool)}


def axes_of(claim: str | None) -> tuple[str, ...]:
    """把 claim 灌进现成的 repair_axes —— ⛔ 不改它的值域，只喂输入。"""
    return G.repair_axes("STUCK_MEANS", {"claim": claim} if claim else None)


def main() -> int:
    t0 = time.time()
    print("V179 STAGE0 —— 行为级读数进来以后，轴会不会变？")
    print(f"  证据目录 {OUT.parent}")

    rows = build_rows()
    cells = sorted({r["task_id"] for r in rows})
    print(f"\n## P1 池可重建（{len(cells)} 格 / {len(rows)} 行，{time.time()-t0:.1f}s）")

    n_no_vec = sum(1 for r in rows if "vec" not in r)
    n_bad = 0
    for r in rows:
        if "vec" in r:
            v = r["vec"]
            if not v or abs(float(r["score"]) - round(sum(v) / len(v), 4)) > 5e-5:
                n_bad += 1
    per_cell = {c: sum(1 for r in rows if r["task_id"] == c) for c in cells}
    ok_p1 = (len(cells) == 6 and min(per_cell.values()) >= 4 and n_bad == 0)
    check("P1", f"6 格各 ≥4 行、行内自洽（无 vec {n_no_vec} 行 —— 缺席单独计数，不当零）",
          ok_p1, f"每格行数 {per_cell}｜自洽失败 {n_bad}｜无 vec {n_no_vec}")

    print("\n## P2 两层 label 逐格对照")
    table = {}
    n_diff = 0
    for c in cells:
        r = two_layers(rows, c)
        sk = r["score"]["label"]
        bk = r["beh"]["kind"]
        same = (sk == bk)
        n_diff += (0 if same else 1)
        table[c] = r
        print(f"  {c:<16} 分数级 {sk:<10} 行为级 {bk:<10}"
              f" 分数claim={r['score'].get('claim')} behclaim={r['beh'].get('claim')}"
              f" 池 n={r['n_pool']} counts={ {k: v for k, v in r['counts'].items() if v} }")
    check("P2", "两层 label 至少 1 格不同（A）", n_diff >= 1, f"不同 {n_diff}/{len(cells)} 格")

    print("\n## P3 claim 触发矩阵（B = 只有行为层触发）")
    only_beh = [c for c in cells
                if table[c]["beh"].get("claim") and not table[c]["score"].get("claim")]
    only_score = [c for c in cells
                  if table[c]["score"].get("claim") and not table[c]["beh"].get("claim")]
    both = [c for c in cells
            if table[c]["score"].get("claim") and table[c]["beh"].get("claim")]
    print(f"  只有行为层触发 {len(only_beh)}：{only_beh}")
    print(f"  只有分数层触发 {len(only_score)}：{only_score}")
    print(f"  两层都触发     {len(both)}：{both}")
    check("P3", "≥1 格「行为级 claim 触发而分数级不触发」（B）", len(only_beh) >= 1,
          f"只有行为层 {len(only_beh)} 格 ⇒ B={'成立' if only_beh else '不成立'}")

    print("\n## P4 轴对照")
    n_axis_diff = 0
    for c in cells:
        a_s = axes_of(table[c]["score"].get("claim"))
        a_b = axes_of(table[c]["beh"].get("claim"))
        if a_s != a_b:
            n_axis_diff += 1
            print(f"  {c:<16} 分数轴 {a_s} ≠ 行为轴 {a_b}")
    print(f"  轴不同的格：{n_axis_diff}/{len(cells)}")
    check("P4", "轴对照报出（是否不同即读数本身）", True,
          f"{n_axis_diff} 格不同；0 格 = 行为层没改变任何一格的动作指向")

    print("\n## P5 判据有牙：构造对照（3 实现 / 同分异行为）")
    beh_pool = [("a", (True, False, False, False), 0.25),
                ("b", (False, True, False, False), 0.25),
                ("c", (False, False, True, False), 0.25)]
    b1 = SI.behavioral(beh_pool)
    rows_ctrl = [{"task_id": "ctrl", "impl_sha": i, "score": s, "vec": list(v)}
                 for i, v, s in beh_pool]
    s1 = SI.movability(rows_ctrl, cell="ctrl")
    ax_b = axes_of(b1.get("claim"))
    ax_s = axes_of(s1.get("claim"))
    print(f"  行为级 kind={b1.get('kind')} claim={b1.get('claim')} bits={b1.get('bits')} 轴={ax_b}")
    print(f"  分数级 kind={s1['label']} claim={s1.get('claim')} 轴={ax_s}")
    ok_p5 = (b1.get("claim") == SI.CLAIM_BEHAVIOR_UNRESOLVED and "RULER" in ax_b
             and s1.get("claim") is None and ax_s == ())
    check("P5", "同分异行为 ⇒ 行为 claim 触发 + 轴含 RULER；同池分数级不触发 + 轴空",
          ok_p5, f"beh={b1.get('claim')}/{ax_b} score={s1.get('claim')}/{ax_s}")

    print("\n## P6 缺席不许当零")
    rows_gap = [{"task_id": "gap", "impl_sha": f"i{i}", "score": 0.3} for i in range(3)]
    b2 = SI.behavioral(SI.beh_pool_from_rows(rows_gap, cell="gap")[0])
    m2 = SI.movability(rows_gap, cell="gap")
    ax2 = axes_of(b2.get("claim"))
    print(f"  无 vec 格：行为级 kind={b2.get('kind')} reason={b2.get('reason')}"
          f" 轴={ax2}｜分数级 kind={m2['label']} claim={m2.get('claim')}")
    check("P6", "无 vec ⇒ 行为级 UNKNOWN、轴空；⛔ 不报成「两层相同」",
          b2.get("kind") == SI.KIND_UNKNOWN and ax2 == (),
          f"{b2.get('kind')}/{b2.get('reason')}")

    # ── P7/P8：PREREG_v179_v2 追加（⛔ 不是改写 P5/P6 —— 那两条判负留档，
    #    这里问的是**另外**两个命题：轴为什么不因层而异、缺席有没有被计数）──
    print("\n## P7 机制：两个 claim 都落 RULER ⇒ 轴丢掉了「哪一层坏」")
    ok_p7 = (b1.get("claim") == SI.CLAIM_BEHAVIOR_UNRESOLVED
             and s1.get("claim") == SI.CLAIM_UNMOVABLE
             and ax_b == ax_s and "RULER" in ax_b)
    check("P7", "同分异行为池：两级 claim **不同名**，但两级轴 **tuple 相同**（都含 RULER）",
          ok_p7, f"claim {b1.get('claim')} vs {s1.get('claim')}｜轴 {ax_b} vs {ax_s}")

    print("\n## P8 缺席被计数（不是被当零）")
    _, cnt = SI.beh_pool_from_rows(rows_gap, cell="gap")
    ok_p8 = (b2.get("kind") == SI.KIND_UNKNOWN and cnt.get("n_no_vec", 0) == 3)
    check("P8", "3 行无 vec ⇒ 行为级 UNKNOWN ∧ n_no_vec==3（缺席可归因）",
          ok_p8, f"kind={b2.get('kind')} reason={b2.get('reason')} counts={cnt}")

    ok = sum(1 for c in CASES if c["ok"])
    print(f"\n## 汇总 {ok} PASS / {len(CASES)-ok} FAIL")
    print(f"  耗时 {time.time()-t0:.1f}s")

    OUT.write_text(json.dumps({
        "prereg": "PREREG_v179.md",
        "stage": "STAGE0",
        "rows": rows,
        "per_cell": {c: {"score_label": table[c]["score"]["label"],
                         "beh_kind": table[c]["beh"]["kind"],
                         "score_claim": table[c]["score"].get("claim"),
                         "beh_claim": table[c]["beh"].get("claim"),
                         "n_pool": table[c]["n_pool"],
                         "counts": table[c]["counts"]} for c in cells},
        "n_label_diff": n_diff, "n_axis_diff": n_axis_diff,
        "only_beh": only_beh, "only_score": only_score, "both": both,
        "cases": CASES,
        "summary": {"n": len(CASES), "ok": ok, "pass": ok == len(CASES)},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  读数 {OUT}")
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
