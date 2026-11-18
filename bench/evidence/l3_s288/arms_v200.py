#!/usr/bin/env python3
"""V200 —— critique 通道的**可用量**：288 条里几条能进 prompt、进去的内容有几个字符是知识。

A 静态：9 suite × 2 scorer = 18 种口径与 288 条求交（纯计算，不跑）
B 动态：真 `Core` + `StubLLM`，`_critique_snapshot`（**复用 run.py 的实现**，不抄）
        四档 off / prev / prev-self / all，逐题取 `state_log` 里的 `[外部评审]` 原文
C 结构：`--goal` 路径（不传签名）⇒ `_admissible_critiques` 首个分支直接 return []
D 载荷：投喂行的 `why` 分类 + `answer` 是否出现

⛔ 不改内核。⚠️ 只读 `state/`：跑前跑后比 sha256+mtime（V199 纪律 99）。
⚠️ 不走 `--bench` CLI：无 API key 时它只跑参照点（oracle），内核一次都不执行；
   所以这里直构 `Core`，与 bench 里 `_build()`+`core.run()` 同一入口。
"""
from __future__ import annotations

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


def fingerprint(p: Path) -> dict:
    if not p.exists():
        return {"exists": False}
    st = p.stat()
    return {"exists": True, "size": st.st_size, "mtime": round(st.st_mtime, 3),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest()[:16]}


# ------------------------------------------------------------------ A 静态
def part_a() -> dict:
    from mw.bench import ExternalScorer, Suite

    keys: dict[tuple[str, str], int] = {}
    for ln in LEDGER.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        d = json.loads(ln)
        k = (d["scorer_sha"], d["suite_sha"])
        keys[k] = keys.get(k, 0) + 1

    files = (sorted((ROOT / "bench" / "tasks").glob("*.jsonl"))
             + sorted((ROOT / "bench" / "heldout").glob("*.jsonl")))
    scorers = [ROOT / "bench" / "scorers" / "code_exec.py",
               ROOT / "bench" / "scorers" / "assertion.py"]
    combos, total = [], 0
    for f in files:
        su = Suite.load(f)
        for sp in scorers:
            sc = ExternalScorer([sys.executable, str(sp)], source=sp)
            n = keys.get((sc.sha(), su.sha()), 0)
            total += n
            combos.append({"suite": f.stem, "scorer": sp.stem,
                           "suite_sha": su.sha()[:12], "scorer_sha": sc.sha()[:12],
                           "reachable": n})
    return {"n_ledger": sum(keys.values()), "n_keys": len(keys), "combos": combos,
            "reachable_sum_over_combos": total}


# ------------------------------------------------------------------ B 动态
def build_core(ledger):
    from mw.core import Core
    from mw.guard import Guard
    from mw.llm import StubLLM
    from mw import config as cfg

    s = cfg.settings()
    guard = Guard(allow_shell=False, workspace=s.paths.workspace,
                  max_consecutive_failures=s.limits.max_consecutive_failures)
    return Core(s, llm=StubLLM(), guard=guard, critique_ledger=ledger)


def crit_per_step(state_log) -> list[list[str]]:
    """逐 step 切 `[外部评审]`（渲染口径：`as_line()[:140]`，` ｜ ` 分隔）。

    ⚠️ 必须**逐 step** 而不是全量累加：`_state()` 每轮都拼一次这段，
       全量数会把「3 行 × 4 步」读成 12 行，看起来像窗口变宽了。
    """
    per = []
    for _, txt in (state_log or []):
        step = []
        for ln in str(txt).splitlines():
            if ln.startswith("[外部评审]"):
                body = ln[len("[外部评审]"):].strip()
                step += [x.strip() for x in body.split(" ｜ ") if x.strip()]
        per.append(step)
    return per


def crit_lines(state_log) -> list[str]:
    return [x for step in crit_per_step(state_log) for x in step]


LINE_RE = re.compile(r"^\[(?P<fam>[^/\]]+)/(?P<tid>[^\]]+)\]\s+(?P<score>[0-9.]+)\s*(?:—\s*(?P<why>.*))?$")


def classify(line: str) -> dict:
    m = LINE_RE.match(line)
    if not m:
        return {"parsed": False, "pass_only": False, "has_assert": False}
    why = (m.group("why") or "").strip()
    return {"parsed": True,
            "fam": m.group("fam"), "tid": m.group("tid"),
            "score": float(m.group("score")), "why": why, "why_len": len(why),
            # 「通过 5/5」这类纯通过语 = 零知识载荷
            "pass_only": bool(re.fullmatch(r"通过\s*\d+/\d+", why)),
            # 含断言/期望值/异常名 ⇒ 才有错误定位（V197：诊断是答案）
            "has_assert": bool(re.search(r"assert|⇒|Error|Exception|期望|expected", why))}


def part_b(decode: dict) -> dict:
    from run import _critique_snapshot          # 复用实现，不抄（纪律 95）
    from mw.critique import CritiqueLedger
    from mw.bench import Suite
    from mw.features import feature_set

    suite = Suite.load(SUITE)
    scorer_sha, suite_sha = decode["scorer_sha_full"], decode["suite_sha_full"]

    # 空 state 目录：把「记忆」这条通道清零，剩下的跨题内容只有 critique
    empty = Path(tempfile.mkdtemp(prefix="v200_state_"))
    os.environ["V1_STATE_DIR"] = str(empty)

    base = CritiqueLedger(LEDGER)
    base.load()

    arms = {}
    answers = [json.loads(l).get("answer") or ""
               for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]

    for scope in ("off", "prev", "prev-self", "all"):
        snap = _critique_snapshot(base, scope)
        per_task, all_lines = [], []
        for t in suite.tasks:
            core = build_core(snap)
            with open(os.devnull, "w") as dn:      # 内核日志会淹掉读数
                _old, sys.stdout = sys.stdout, dn
                try:
                    _out = core.run(t.prompt, task_id=t.id, family=suite.name,
                                    scorer_sha=scorer_sha, suite_sha=suite_sha,
                                    features=feature_set(t.as_dict()))
                finally:
                    sys.stdout = _old
            steps = crit_per_step(core.state_log)
            # ⚠️ 末步不是「下发的那一版」：收尾还会再建一次 state 而那次不带 task_id
            #    ⇒ 末步 0 行。取**最后一个非空步**才是模型真正看到的那一版。
            nonempty = [s_ for s_ in steps if s_]
            last = nonempty[-1] if nonempty else []
            all_lines += last
            blob = "\n".join(str(x) for _, x in (core.state_log or []))
            per_task.append({"id": t.id,
                             "n_steps": len(steps),
                             "lines_per_step": [len(s) for s in steps],
                             "last_step_lines": len(last),
                             "first_tid": (classify(last[0]).get("tid") if last else ""),
                             # answer 存了却进没进 prompt：这是 P3 的直接判据
                             "answer_leak": sum(1 for a in answers
                                                if len(a) >= 40 and a[:60] in blob),
                             "trimmed": int(getattr(core, "state_trimmed", 0)),
                             "status": str(getattr(core, "last_status", "") or ""),
                             # 收尾调用是否真的交付了答案（决定「最后一版 prompt」有没有评审）
                             "closeout": dict((_out.stats or {}).get("closeout") or {}),
                             "state_chars_per_step": [len(str(x)) for _, x in (core.state_log or [])]})
        st = snap.stats() if snap is not None else {}
        arms[scope] = {"per_task": per_task,
                       "final_lines": all_lines,
                       "n_lines_final": len(all_lines),
                       "served": st.get("served", 0),
                       "calls": st.get("calls", 0),
                       "rejected": st.get("rejected", {}),
                       "allow_self": st.get("allow_self"),
                       "ledger_keys": (len(snap.by_key) if snap is not None else 0)}
    shutil.rmtree(empty, ignore_errors=True)
    return {"scorer_sha": scorer_sha[:12], "suite_sha": suite_sha[:12],
            "n_tasks": len(suite), "arms": arms}


# ------------------------------------------------------------------ C goal 路径
def part_c() -> dict:
    from run import _critique_snapshot
    from mw.critique import CritiqueLedger

    empty = Path(tempfile.mkdtemp(prefix="v200_goal_"))
    os.environ["V1_STATE_DIR"] = str(empty)
    base = CritiqueLedger(LEDGER)
    base.load()
    snap = _critique_snapshot(base, "prev")
    core = build_core(snap)
    # ⛔ 不传 scorer_sha/suite_sha —— 这正是 `--goal` 路径的形态（`_build()` 不传）
    with open(os.devnull, "w") as dn:
        _old, sys.stdout = sys.stdout, dn
        try:
            core.run("写一个函数 f(x)，返回 x+1。只输出代码。", task_id="v200_goal")
        finally:
            sys.stdout = _old
    lines = crit_lines(core.state_log)
    st = snap.stats()
    shutil.rmtree(empty, ignore_errors=True)
    return {"n_lines": len(lines), "served": st.get("served", 0),
            "calls": st.get("calls", 0), "rejected": st.get("rejected", {})}


def part_e(rows: list[tuple[str, str, str]], scorer_sha: str,
           suite_sha: str, tids: list[str]) -> dict:
    """静态分解：对每个 task_id，288 个键里谁被哪道闸挡掉。

    ⚠️ 为什么要静态算一遍：`rejected` 是**累计计数器**，而闸的顺序是
       `self → cross_scorer → cross_suite` ⇒ 别的套件里**同 id** 的行会先被记成
       `self`，看起来像"防自评在起作用"。逐键归类才能把「自评」与「口径不符」分开。
    """
    out = {}
    for tid in tids:
        cls = {"self": 0, "cross_scorer": 0, "cross_suite": 0, "admitted": 0,
               "total": len(rows)}
        for t, ss, su_ in rows:
            if t == tid:
                cls["self"] += 1
            elif ss != scorer_sha:
                cls["cross_scorer"] += 1
            elif su_ != suite_sha:
                cls["cross_suite"] += 1
            else:
                cls["admitted"] += 1
        out[tid] = cls
    return out


def main() -> int:
    guard_before = {n: fingerprint(SRC_STATE / n) for n in ("critique.jsonl", "memory.db")}

    from mw.bench import ExternalScorer, Suite
    su = Suite.load(SUITE)
    sc = ExternalScorer([sys.executable, str(SCORER)], source=SCORER)
    decode = {"scorer_sha_full": sc.sha(), "suite_sha_full": su.sha()}

    a = part_a()
    print(f"\n[A] 存量 {a['n_ledger']} 条 / {a['n_keys']} 个口径键；"
          f"18 种当前口径合计可达 **{a['reachable_sum_over_combos']}** 条")
    for c in a["combos"]:
        if c["reachable"]:
            print(f"     HIT {c['suite']:30s} {c['scorer']:12s} n={c['reachable']}")

    b = part_b(decode)
    print(f"\n[B] coding_small（{b['n_tasks']} 题）scorer_sha={b['scorer_sha']} "
          f"suite_sha={b['suite_sha']}")
    for scope, arm in b["arms"].items():
        ns = [x["last_step_lines"] for x in arm["per_task"]]
        steps = [x["lines_per_step"] for x in arm["per_task"]]
        leaks = sum(x["answer_leak"] for x in arm["per_task"])
        trim = sum(x["trimmed"] for x in arm["per_task"])
        print(f"     {scope:10s} served={arm['served']:3d} 末步行数/题={ns} "
              f"answer泄漏={leaks} 被裁块={trim} 逐题步数={steps}")

    # 静态分解（逐键归类，替代累计计数器）
    rows = []
    for ln in LEDGER.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            d = json.loads(ln)
            rows.append((d["task_id"], d["scorer_sha"], d["suite_sha"]))
    e = part_e(rows, decode["scorer_sha_full"], decode["suite_sha_full"],
               [t.id for t in su.tasks])
    print("\n[E] 逐题闸分解（288 键的去向，静态）：")
    for tid, cls in e.items():
        tot = cls["self"] + cls["cross_scorer"] + cls["cross_suite"] + cls["admitted"]
        print(f"     {tid:22s} self={cls['self']:3d} cross_scorer={cls['cross_scorer']:4d} "
              f"cross_suite={cls['cross_suite']:3d} admitted={cls['admitted']}  合计={tot}")

    c = part_c()
    print(f"\n[C] goal 路径（不传签名）：served={c['served']} 进 prompt={c['n_lines']} "
          f"拒绝={c['rejected']} calls={c['calls']}")

    # 载荷分类：以 prev 档**末步**（=真正下发的那一版）为样本
    lines = b["arms"]["prev"]["final_lines"]
    cls = [classify(x) for x in lines]
    pass_only = sum(1 for x in cls if x.get("pass_only"))
    with_assert = sum(1 for x in cls if x.get("has_assert"))
    unparsed = sum(1 for x in cls if not x.get("parsed"))
    distinct = sorted(set(lines))
    print(f"\n[D] 末步投喂 {len(lines)} 行（去重 {len(distinct)}）："
          f"纯通过语 {pass_only}、含断言/异常 {with_assert}、未解析 {unparsed}")
    for x in distinct:
        q = classify(x)
        print(f"     · {q.get('tid','?'):22s} s={q.get('score')} "
              f"why={q.get('why','')[:58]!r} pass_only={q.get('pass_only')} "
              f"assert={q.get('has_assert')}")

    guard_after = {n: fingerprint(SRC_STATE / n) for n in ("critique.jsonl", "memory.db")}
    clean = guard_before == guard_after
    print(f"\n[隔离] 原 state/ 跑前跑后一致：{clean}")
    for n in guard_before:
        if guard_before[n] != guard_after[n]:
            print(f"     ⛔ {n} 变了：{guard_before[n]} → {guard_after[n]}")

    reading = {"a_static": a, "b_dynamic": b, "c_goal": c, "e_gates": e,
               "d_payload": {"n_lines": len(lines), "n_distinct": len(distinct),
                             "pass_only": pass_only, "has_assert": with_assert,
                             "unparsed": unparsed, "lines": lines,
                             "distinct": distinct},
               "isolation": {"clean": clean, "before": guard_before, "after": guard_after},
               "decode": decode}
    (OUT / "readings_v200.json").write_text(
        json.dumps(reading, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ {OUT/'readings_v200.json'}")
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
