#!/usr/bin/env python3
"""Preflight V251 -- production-chain A/B of --goals-swap-edit {off,line}.

Nothing here decides the main criterion; it only proves the apparatus can
produce a readable difference.  Writes preflight_v251.json next to itself.

P1  two arm state dirs are byte-identical copies of the probe state
P2  dry `--goals` proposes RAISE 6 / PROBE 0, base == per-cell max(obs)
P3  off vs line prompt differ ONLY by the two substitutions (identity check)
P4  LINE_ref (last `=>return`/`=>raise` line in trace) parses for all 7
P5  apparatus positive path: t01 line16 -> int(parts[0]) raises 5/6 to 6/6
P6  fail-closed: 4 bad inputs all return ("", err) and never a code string
P7  speed gate: real generation measured via ollama eval_count, tok/s >= 15
P8  ledger-best impl is fetchable from cache for all 7
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path("/Users/linshaojie/V1")
D = pathlib.Path(__file__).resolve().parent
PROBE = pathlib.Path("/tmp/v251_g7d")
SUITE = pathlib.Path("/tmp/v251_suite_d")
STATE_A = pathlib.Path("/tmp/v251_a")
STATE_B = pathlib.Path("/tmp/v251_b")

recs: list[dict] = []


def rec(tag: str, ok: object, detail: str = "") -> None:
    val = ok if isinstance(ok, bool) else bool(ok)
    recs.append({"tag": tag, "ok": val, "detail": detail})
    print(f"  {'PASS' if val else 'FAIL'}  {tag:16s} {detail}")


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def snapshot(d: pathlib.Path) -> dict:
    out = {}
    for name in ("goal_obs.jsonl", "goal_ledger.jsonl"):
        p = d / name
        out[name] = sha(p) if p.exists() else None
    cdir = d / "candidates"
    if cdir.is_dir():
        out["candidates"] = {p.name: sha(p) for p in sorted(cdir.glob("*.py"))}
    return out


def tok_s() -> tuple[float, str]:
    """One real generation; tokens from ollama's own eval_count."""
    body = json.dumps({"model": os.environ.get("V1_LLM_MODEL", "qwen2.5:7b"),
                       "prompt": "Count from 1 to 120, comma separated, nothing else.",
                       "stream": False,
                       "options": {"num_predict": 220, "temperature": 0}}).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as fh:
        d = json.load(fh)
    dt = time.time() - t0
    ec = int(d.get("eval_count") or 0)
    ed = float(d.get("eval_duration") or 0) / 1e9
    return (round(ec / ed, 2) if ed > 0 else 0.0,
            f"eval_count={ec} eval_s={ed:.1f} wall={dt:.1f}s")


def main() -> int:
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    from mw.reflect import (apply_edit, build_proposal_prompt, failed_actuals,
                            numbered_code, trace_failure, verify)

    tasks = {t["id"]: t for t in
             (json.loads(l) for l in SUITE.joinpath("g7d_spec_incomplete.jsonl")
              .read_text(encoding="utf-8").splitlines() if l.strip())}
    obs = [json.loads(l) for l in
           (PROBE / "goal_obs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    best: dict[str, float] = {}
    for r in obs:
        best[r["task_id"]] = max(best.get(r["task_id"], -1.0), float(r["score"]))
    impl = {r["task_id"]: r["impl_sha"] for r in obs
            if float(r["score"]) == best[r["task_id"]]}

    # ---- P1 ----
    for st in (STATE_A, STATE_B):
        if st.exists():
            shutil.rmtree(st)
        shutil.copytree(PROBE, st)
    sa, sb, sp = snapshot(STATE_A), snapshot(STATE_B), snapshot(PROBE)
    rec("P1_same_start", sa == sb == sp,
        f"obs={sa['goal_obs.jsonl']} ledger={sa['goal_ledger.jsonl']} "
        f"cand={len(sa['candidates'])}")

    # ---- P2 ----
    env = dict(os.environ, V1_STATE_DIR=str(STATE_A), V1_LOG_DIR=str(STATE_A / "log"),
               V1_WORKSPACE=str(STATE_A / "ws"), V1_BENCH_DIR=str(STATE_A / "bench"))
    out = subprocess.run([sys.executable, "-u", "run.py", "--goals",
                          "--goals-suite-dir", str(SUITE), "--no-external"],
                         capture_output=True, text=True, env=env, cwd=str(ROOT))
    txt = out.stdout
    kinds = dict((k, len(re.findall(rf"^\s+\d+\s+{k}\s+", txt, re.M)))
                 for k in ("COVER", "PROBE", "RAISE"))
    bases = sorted(float(x) for x in re.findall(r"best_score>([0-9.]+)", txt))
    exp = sorted(v for v in best.values() if v < 1.0)
    rec("P2_six_raise", kinds == {"COVER": 0, "PROBE": 0, "RAISE": 6} and bases == exp,
        f"kinds={kinds} bases={bases} expected={exp}")

    # ---- P3: identity -- two substitutions and nothing else ----
    # must be a cell whose ledger best is still < 1.0, else trace is empty
    tid = sorted(k for k, v in best.items() if v < 1.0)[0]
    task = tasks[tid]
    code = (PROBE / "candidates" / (impl[tid] + ".py")).read_text(encoding="utf-8")
    acts = failed_actuals(code, task["tests"])
    tr = trace_failure(code, task["tests"])
    why = "pass %d/%d" % (verify(code, task["tests"]), len(task["tests"]))
    p_off = build_proposal_prompt(task["prompt"], code, why, acts, trace=tr)
    p_line = build_proposal_prompt(task["prompt"], code, why, acts, trace=tr, edit=True)
    mark = "请顺着轨迹找到**第一处**让结果偏离题意的逻辑"
    h_off, t_off = p_off.split(mark, 1)
    h_line, t_line = p_line.split(mark, 1)
    same_head = h_line.replace(numbered_code(code), code) == h_off
    rec("P3_two_subs", same_head and t_line != t_off and "LINE:" in t_line
        and "LINE:" not in t_off,
        f"head_identity={same_head} off={len(p_off.encode())}B "
        f"line={len(p_line.encode())}B tail_differs={t_line != t_off}")

    # ---- P4 ----
    refs, bad = {}, []
    for tid2, t2 in tasks.items():
        if best[tid2] >= 1.0:          # best is already perfect -> no failing trace
            continue
        src = (PROBE / "candidates" / (impl[tid2] + ".py")).read_text(encoding="utf-8")
        tr2 = trace_failure(src, t2["tests"])
        lines = [int(m) for m in
                 re.findall(r"第 (\d+) 行 \|.*?⇒(?:返回|抛出)", "\n".join(tr2))]
        refs[tid2] = lines[-1] if lines else None
        if not lines:
            bad.append(tid2)
    rec("P4_line_ref", (not bad) and len(refs) == 6,
        f"n={len(refs)} refs={refs} unparsed={bad}")

    # ---- P5: t01 positive path (v248 anchor: line16 -> 6/6) ----
    t01 = json.loads(pathlib.Path(
        "bench/evidence/l5_s266/suite_one/t01.jsonl").read_text(encoding="utf-8").splitlines()[0])
    src01 = pathlib.Path("bench/evidence/l9_s310/cand/90579ecebe633352.py").read_text(encoding="utf-8")
    new01, err01 = apply_edit(src01, 16, "minutes = int(parts[0])")
    b01, a01 = verify(src01, t01["tests"]), (verify(new01, t01["tests"]) if new01 else -1)
    rec("P5_apply_positive", (not err01) and b01 == 5 and a01 == 6,
        f"t01 base={b01}/{len(t01['tests'])} -> {a01} err={err01}")

    # ---- P6 ----
    fcases = [(None, "x"), (0, "x"), (99, "x"), (6, "")]
    fout = [apply_edit(code, ln, ex) for ln, ex in fcases]
    rec("P6_fail_closed", all((not c) and e for c, e in fout),
        f"errs={[e for _, e in fout]} all_empty_code="
        f"{all(not c for c, _ in fout)}")

    # ---- P7 ----
    try:
        tps, det = tok_s()
    except Exception as exc:                        # noqa: BLE001
        tps, det = 0.0, f"{type(exc).__name__}: {exc}"[:120]
    rec("P7_speed", tps >= 15, f"tok/s={tps} {det}")

    # ---- P8 ----
    missing = [k for k in tasks if not (PROBE / "candidates" / (impl[k] + ".py")).exists()]
    rec("P8_start_impl", not missing, f"missing={missing}")

    D.joinpath("preflight_v251.json").write_text(json.dumps(
        {"checks": recs, "line_ref": refs, "best": best, "impl": impl},
        ensure_ascii=False, indent=2), encoding="utf-8")
    npass = sum(1 for r in recs if r["ok"])
    print(f"\nPREFLIGHT {npass}/{len(recs)}")
    return 0 if npass == len(recs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
