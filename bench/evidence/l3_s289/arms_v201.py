#!/usr/bin/env python3
"""V201 —— 补齐收尾签名后，评审块**加得进去吗**？代价是什么？

A 静态：AST 扫全库 `_state(` / `.run(` 调用点，看谁不传 `task_id`/签名。
B 动态：6 题 × `prev` 档，包一层 `Core._state` 记录入参 + 输出；
        取「最后一次带签名的 state」（迭代看到的那版）与「最后一次不带签名的 state」（收尾那版）。
C 反事实：用**同一个 `_state` 实现**、把收尾那次的入参换成补齐签名，重算一次；逐块比对。

⛔ 不改内核文件（patch 只在装置进程内）。⛔ 只读 `state/`（跑前跑后比 sha256+mtime）。
⚠️ `state_trimmed` 会被重算污染 ⇒ 只报**前后差值**，不当产品读数。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
SRC_STATE = ROOT / "state"
LEDGER = SRC_STATE / "critique.jsonl"
SUITE = ROOT / "bench" / "tasks" / "coding_small.jsonl"
SCORER = ROOT / "bench" / "scorers" / "code_exec.py"

sys.path.insert(0, str(ROOT))

STEP_RE = re.compile(r"^\[第 \d+/\d+ 步\]")


def fingerprint(p: Path) -> dict:
    if not p.exists():
        return {"exists": False}
    st = p.stat()
    return {"exists": True, "size": st.st_size, "mtime": round(st.st_mtime, 3),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest()[:16]}


# ------------------------------------------------------------------ A 静态
def part_a() -> dict:
    """谁在调 `_state` / `core.run`，实参里带不带签名。"""
    site_state, site_run = [], []
    for p in sorted(list((ROOT / "mw").glob("*.py")) + [ROOT / "run.py"]):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = getattr(f, "attr", None) or getattr(f, "id", None)
            if name == "_state":
                kws = {k.arg for k in node.keywords}
                site_state.append({"file": str(p.relative_to(ROOT)), "line": node.lineno,
                                   "task_id": "task_id" in kws,
                                   "scorer_sha": "scorer_sha" in kws,
                                   "suite_sha": "suite_sha" in kws})
            elif name == "run" and isinstance(f, ast.Attribute) and f.attr == "run":
                kws = {k.arg for k in node.keywords}
                site_run.append({"file": str(p.relative_to(ROOT)), "line": node.lineno,
                                 "task_id": "task_id" in kws,
                                 "sig": ("scorer_sha" in kws) and ("suite_sha" in kws)})
    return {"state_sites": site_state, "run_sites": site_run}


# ------------------------------------------------------------------ 块切分
def blocks(txt: str) -> dict[str, str]:
    """按行首 `[块名]` 切块；步号归一成 `步号`（内容带具体数字）。"""
    out: dict[str, str] = {}
    for ln in str(txt).splitlines():
        if STEP_RE.match(ln):
            out["步号"] = ln
            continue
        m = re.match(r"^\[([^\]/]+)\]", ln)
        key = m.group(1).strip() if m else "<裸行>"
        out[key] = out.get(key, "") + ("\n" if key in out else "") + ln
    return out


# ------------------------------------------------------------------ B/C
def main() -> int:
    from mw import config as cfg
    from mw.core import Core
    from mw.critique import CritiqueLedger
    from mw.bench import ExternalScorer, Suite
    from mw.features import feature_set
    from run import _critique_snapshot

    guard_before = {n: fingerprint(SRC_STATE / n) for n in ("critique.jsonl", "memory.db")}

    a = part_a()
    print("[A] `_state(` 调用点：")
    for s in a["state_sites"]:
        print(f"     {s['file']}:{s['line']:5d}  task_id={s['task_id']} "
              f"scorer_sha={s['scorer_sha']} suite_sha={s['suite_sha']}")
    print("     `.run(` 调用点（带/不带签名）：")
    for s in a["run_sites"]:
        print(f"     {s['file']}:{s['line']:5d}  task_id={s['task_id']} sig={s['sig']}")

    su = Suite.load(SUITE)
    sc = ExternalScorer([sys.executable, str(SCORER)], source=SCORER)
    ssha, susha = sc.sha(), su.sha()

    empty = Path(tempfile.mkdtemp(prefix="v201_state_"))
    os.environ["V1_STATE_DIR"] = str(empty)
    base = CritiqueLedger(LEDGER)
    base.load()
    snap = _critique_snapshot(base, "prev")

    s = cfg.settings()
    cap = int(s.limits.state_chars)
    print(f"\n[cap] state_chars = {cap}")

    # 包一层 _state（只在本进程）
    recs: list[dict] = []
    _orig = Core._state

    def spy(self, goal, history, it, *, task_id=None, scorer_sha="", suite_sha=""):  # noqa: ANN001
        out = _orig(self, goal, history, it, task_id=task_id,
                    scorer_sha=scorer_sha, suite_sha=suite_sha)
        recs.append({"it": it, "task_id": task_id or "", "ss": scorer_sha, "su": suite_sha,
                     "chars": len(out), "text": out,
                     "goal": goal, "history": tuple(history)})
        return out

    Core._state = spy
    from mw.guard import Guard
    from mw.llm import StubLLM

    per_task = []
    try:
        for t in su.tasks:
            recs.clear()
            g = Guard(allow_shell=False, workspace=s.paths.workspace,
                      max_consecutive_failures=s.limits.max_consecutive_failures)
            core = Core(s, llm=StubLLM(), guard=g, critique_ledger=snap)
            with open(os.devnull, "w") as dn:
                _old, sys.stdout = sys.stdout, dn
                try:
                    core.run(t.prompt, task_id=t.id, family=su.name,
                             scorer_sha=ssha, suite_sha=susha,
                             features=feature_set(t.as_dict()))
                finally:
                    sys.stdout = _old

            signed = [r for r in recs if r["task_id"]]
            unsigned = [r for r in recs if not r["task_id"]]
            it_last = signed[-1] if signed else None
            co = unsigned[-1] if unsigned else None
            if it_last is None or co is None:
                per_task.append({"id": t.id, "error": "缺样的 state（迭代或收尾）"})
                continue

            b_it, b_co = blocks(it_last["text"]), blocks(co["text"])
            miss = [k for k in b_it if k not in b_co]
            extra = [k for k in b_co if k not in b_it]

            # ---- 反事实：同一实现，补齐签名重算收尾那版
            trim0 = int(core.state_trimmed)
            cf = _orig(core, co["goal"], co["history"], co["it"],
                       task_id=t.id, scorer_sha=ssha, suite_sha=susha)
            trim1 = int(core.state_trimmed)
            b_cf = blocks(cf)

            added = [k for k in b_cf if k not in b_co]
            lost = [k for k in b_co if k not in b_cf]
            changed = [k for k in b_co
                       if k in b_cf and b_co[k] != b_cf[k]]
            per_task.append({
                "id": t.id, "cap": cap,
                "n_state_calls": len(recs),
                "iter_chars": it_last["chars"], "close_chars": co["chars"],
                "cf_chars": len(cf),
                "delta_chars": len(cf) - co["chars"],
                "iter_history": len(it_last["history"]), "close_history": len(co["history"]),
                "blocks_iter": sorted(b_it), "blocks_close": sorted(b_co),
                "missing_in_close": miss, "extra_in_close": extra,
                "cf_added_blocks": added, "cf_lost_blocks": lost,
                "cf_changed_blocks": changed,
                "cf_review_lines": len([ln for ln in cf.splitlines()
                                        if ln.startswith("[外部评审]")]),
                "cf_review_chars": len(b_cf.get("外部评审", "")),
                "trim_delta": trim1 - trim0,
                "cf_over_cap": len(cf) > cap,
                "cf_text": cf,
            })
            print(f"     · {t.id:20s} iter={it_last['chars']:4d} close={co['chars']:4d} "
                  f"cf={len(cf):4d}(+{len(cf)-co['chars']:3d}) "
                  f"缺块={miss} 增块={added} 丢块={lost} 改块={changed} "
                  f"trim+{trim1-trim0}")
    finally:
        Core._state = _orig
        shutil.rmtree(empty, ignore_errors=True)

    # ---- 汇总
    ok = [x for x in per_task if "error" not in x]
    n_review = sum(1 for x in ok if x["cf_review_lines"] > 0)
    n_lost = sum(1 for x in ok if x["cf_lost_blocks"])
    n_changed = sum(1 for x in ok if x["cf_changed_blocks"])
    n_trim = sum(1 for x in ok if x["trim_delta"] > 0)
    n_over = sum(1 for x in ok if x["cf_over_cap"])
    print(f"\n[B/C] {len(ok)}/{len(su)} 题有效："
          f"反事实里出现评审块 {n_review}；丢块 {n_lost}；改块 {n_changed}；"
          f"trim 增 {n_trim}；超 cap {n_over}")

    # 补进去的内容形状（承 V200 的载荷分类）
    pl = re.compile(r"^\[(?P<fam>[^/\]]+)/(?P<tid>[^\]]+)\]\s+(?P<score>[0-9.]+)\s*(?:—\s*(?P<why>.*))?$")
    lines = []
    for x in ok:
        for ln in x["cf_text"].splitlines():
            if ln.startswith("[外部评审]"):
                body = ln[len("[外部评审]"):].strip()
                lines += [y.strip() for y in body.split(" ｜ ") if y.strip()]
    pass_only = sum(1 for ln in lines if (m := pl.match(ln)) and
                    re.fullmatch(r"通过\s*\d+/\d+", (m.group("why") or "").strip()))
    print(f"     反事实投喂行 {len(lines)}（去重 {len(set(lines))}）："
          f"纯通过语 {pass_only}")

    guard_after = {n: fingerprint(SRC_STATE / n) for n in ("critique.jsonl", "memory.db")}
    clean = guard_before == guard_after
    print(f"\n[隔离] 原 state/ 跑前跑后一致：{clean}")

    reading = {"a_static": a, "cap": cap, "per_task": per_task,
               "summary": {"n_ok": len(ok), "n_tasks": len(su),
                           "cf_has_review": n_review, "cf_lost": n_lost,
                           "cf_changed": n_changed, "cf_trim": n_trim,
                           "cf_over_cap": n_over,
                           "review_lines": len(lines),
                           "review_distinct": len(set(lines)),
                           "pass_only": pass_only},
               "isolation": {"clean": clean, "before": guard_before, "after": guard_after}}
    (OUT / "readings_v201.json").write_text(
        json.dumps(reading, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {OUT/'readings_v201.json'}")
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
