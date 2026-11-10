#!/usr/bin/env python3
"""V82（§151）：累积循环的**写端 / 读端**普查 —— 零模型、零内核改动。

## 这一轮要回答的问题

「累积了」这句话在过去五轮里一直是靠 `memory_seed` 的第二分量说的。本探针只问三件事，
每件都要求一个**能落在产物里的数**：

  Q1 写端：库里到底写了什么？（按 kind 计数，并把 `finish` 的代码**离线重打分**）
  Q2 读端：哪些 kind 有生产路径的读点？（静态：`episodes(kind=...)` / `facts` / `recall`）
  Q3 通路：把 episodes 变成 facts（唯一会被 `recall` 读到的那张表）的那个函数，
         在 `run.py` 里有没有调用点？

## 为什么必须离线重打分

`finish` 的 content 是 `done: ```python ... ``` `。**"写过一条交付" ≠ "写过一条对交付"**。
只有把代码代进题集自带的 `tests` 才算数。打分一律走**现成的**外部打分器
（`bench/scorers/code_exec.py`，与跑批同一把尺子），不另写判分逻辑。

用法:
    python3 bench/probe_accum_wires.py            # 默认扫 state/ 与归档
    python3 bench/probe_accum_wires.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mw.bench import ExternalScorer  # noqa: E402

#: 扫描的记忆库。存在才读 —— 缺库是"没归档"，不是"零条"。
DEFAULT_DBS = [
    ROOT / "state" / "memory.db",
    ROOT / "bench/evidence/v6_s158/seed_memory.db",
    ROOT / "bench/evidence/v6_s159/final_memory.db",
    ROOT / "bench/evidence/knowledge_s30/train_memory.db",
]

#: 任务 id → 取它 `tests` 的题集。**全部用现成题集**（`bench/tasks/*` + `bench/heldout/*`），
#: 一行不新造。覆盖不到的交付记 `unscored_no_task` —— 那是探针的射程，不是「0 分」。
TASK_SUITES = sorted((ROOT / "bench" / "tasks").glob("*.jsonl")) + sorted(
    (ROOT / "bench" / "heldout").glob("*.jsonl"))

_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)
_DEF = re.compile(r"^\s*def\s+([A-Za-z_]\w*)\s*\(", re.M)


def load_tasks() -> dict[str, dict]:
    """函数名 → 任务字典（`tests` 取自现成题集；同名取第一个）。"""
    out: dict[str, dict] = {}
    for p in TASK_SUITES:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            t = json.loads(line)
            for name in _DEF.findall("".join(t.get("oracle") or "")) or []:
                out.setdefault(name, t)
            # oracle 可能不含 def（空实现），退回用 tests 里的函数名
            for m in re.finditer(r"\b([A-Za-z_]\w*)\(", " ".join(t.get("tests") or [])):
                if m.group(1) not in {"assert", "len", "str", "int", "float"}:
                    out.setdefault(m.group(1), t)
    return out


#: 交付原文的**非代码**前缀（实测：库里主力形态是不带围栏的裸代码 `done: def f...`，
#: 第一版只认 ``` 围栏 ⇒ 把 277 条裸代码判成「无代码块」，是探针自己失效，不是库的事实）。
_PROSE = re.compile(r"^(done|stalled|error|done_unverified|done_unverif)\w*\s*[:：]\s*", re.I)


def extract_code(content: str) -> str:
    """取出可判分的代码正文。

    两级：① ``` 围栏内的原文；② 没有围栏时去掉 `done:`/`stalled:` 前缀，
    只要剩下部分出现 `def ` 就整段当代码（判分器自己会判 SyntaxError/失败）。
    ⛔ 不含 `def ` 的散文（"根因是…"、"规划降级…"）一律不判 —— 它们不是交付代码。
    """
    txt = content or ""
    m = _FENCE.search(txt)
    if m:
        return m.group(1).strip()
    body = _PROSE.sub("", txt).strip()
    return body if re.search(r"^\s*def\s+\w+\s*\(", body, re.M) else ""


def scan_db(path: Path, tasks: dict[str, dict], scorer: ExternalScorer) -> dict:
    con = sqlite3.connect(path)
    cur = con.cursor()
    kinds = {k: n for k, n in cur.execute(
        "SELECT kind, COUNT(*) FROM episodes GROUP BY kind ORDER BY 2 DESC")}
    srcs = {s: n for s, n in cur.execute(
        "SELECT source, COUNT(*) FROM facts GROUP BY source ORDER BY 2 DESC")}

    #: 2x2：桩跑（wall_s≤0.05，`--solver oracle/partial/null` 走离线桩）vs 真跑。
    #: ⛔ 不分开统计就会把「oracle 桩跑写进去的满分」当成「系统自己攒的对答案」。
    by_wall: dict[str, dict] = {
        "stub(wall≤0.05)": {"n": 0, "scored": 0, "correct": 0},
        "real(wall>0.05)": {"n": 0, "scored": 0, "correct": 0},
        "unknown": {"n": 0, "scored": 0, "correct": 0},
    }

    # --- finish 离线重打分 ---
    per_fn: dict[str, dict] = {}
    for content, meta in cur.execute(
            "SELECT content, meta FROM episodes WHERE kind='finish'"):
        try:
            wall = float(((json.loads(meta or "{}").get("stats") or {})
                          .get("budget") or {}).get("wall_s"))
        except Exception:                                  # noqa: BLE001
            wall = None
        bucket = ("unknown" if wall is None
                  else ("stub(wall≤0.05)" if wall <= 0.05 else "real(wall>0.05)"))
        by_wall[bucket]["n"] += 1
        code = extract_code(content)
        if not code:
            per_fn.setdefault("<无代码块>", {"n": 0, "scored": 0, "correct": 0})["n"] += 1
            continue
        names = _DEF.findall(code)
        fn = names[0] if names else "<无def>"
        rec = per_fn.setdefault(fn, {"n": 0, "scored": 0, "correct": 0, "best": 0.0})
        rec["n"] += 1
        task = tasks.get(fn)
        if not task:
            continue
        try:
            sc = scorer.score(task, code).score
        except Exception:                                  # noqa: BLE001
            continue
        rec["scored"] += 1
        by_wall[bucket]["scored"] += 1
        rec["best"] = max(rec["best"], float(sc))
        if float(sc) >= 0.999:
            rec["correct"] += 1
            by_wall[bucket]["correct"] += 1

    facts = [{"source": s, "text": t[:160]} for s, t in cur.execute(
        "SELECT source, text FROM facts ORDER BY id")]
    con.close()
    return {"db": str(path.relative_to(ROOT)), "episodes_kind": kinds,
            "facts_by_source": srcs, "n_facts": sum(srcs.values()),
            "n_episodes": sum(kinds.values()),
            "finish_by_fn": per_fn, "finish_by_wall": by_wall,
            "facts_sample": facts}


def reader_census() -> dict:
    """读点普查：谁读 episodes、谁读 facts、谁把 episodes 变成 facts。

    ⛔ 只做**存在性**判断（出现/不出现），不做"应该在哪"的推断 ——
       读点的**语义**（在不在生产路径上）由 `run.py` 的调用点单独证明。
    """
    py = sorted(p for p in (ROOT / "mw").glob("*.py"))
    ep_readers: dict[str, list[str]] = {}
    fact_readers: list[str] = []
    for p in py:
        txt = p.read_text(encoding="utf-8")
        for m in re.finditer(r'episodes\(\s*kind\s*=\s*["\'](\w+)["\']', txt):
            ep_readers.setdefault(m.group(1), []).append(p.name)
        if re.search(r"\.episodes\(\s*(limit|\))", txt):
            ep_readers.setdefault("<全部/无kind>", []).append(p.name)
        if re.search(r"def recall\(", txt):
            fact_readers.append(f"{p.name}:def recall")
    # 蒸馏器的生产调用点：run.py 里有没有 distill(
    run_txt = (ROOT / "run.py").read_text(encoding="utf-8")
    distill_in_run = len(re.findall(r"\bdistill\s*\(", run_txt))
    distill_callers = []
    for p in sorted(ROOT.glob("**/*.py")):
        if "build/lib" in str(p) or ".venv" in str(p):
            continue
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"\bdistill\s*\(", txt) and p.name != "abstractor.py":
            distill_callers.append(str(p.relative_to(ROOT)))
    return {"episodes_readers": ep_readers, "facts_readers": fact_readers,
            "distill_callers": distill_callers, "distill_calls_in_run_py": distill_in_run}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    tasks = load_tasks()
    scorer = ExternalScorer(
        [sys.executable, str(ROOT / "bench" / "scorers" / "code_exec.py")],
        source=ROOT / "bench" / "scorers" / "code_exec.py")

    out = {"tasks_known": sorted(tasks)[:40], "n_tasks_known": len(tasks),
           "scorer_sha": scorer.sha(), "dbs": [], "census": reader_census()}

    print(f"# 打分器 {scorer.name} sha={scorer.sha()[:16]}  题库函数名={len(tasks)}")
    for p in DEFAULT_DBS:
        if not p.exists():
            print(f"\n## {p}  —— 缺库（未归档），跳过")
            continue
        r = scan_db(p, tasks, scorer)
        out["dbs"].append(r)
        print(f"\n## {r['db']}  episodes={r['n_episodes']} facts={r['n_facts']}")
        print(f"   episodes by kind: {r['episodes_kind']}")
        print(f"   facts  by source: {r['facts_by_source']}")
        for b, rec in r["finish_by_wall"].items():
            print(f"   交付 {b:18s} 条={rec['n']:3d} 可判={rec['scored']:3d} "
                  f"满分={rec['correct']:3d}")
        for fn, rec in sorted(r["finish_by_fn"].items()):
            print(f"   finish {fn:18s} 条={rec['n']:3d} 可判={rec.get('scored',0):3d} "
                  f"满分={rec.get('correct',0):3d} 最高分={rec.get('best',0.0):.4f}")

    c = out["census"]
    print("\n## 读点普查")
    for k, v in sorted(c["episodes_readers"].items()):
        print(f"   episodes(kind={k})  <- {sorted(set(v))}")
    print(f"   facts 读点: {c['facts_readers']}")
    print(f"   把 episodes 变成 facts 的函数 distill() 在 run.py 的调用点 = "
          f"{c['distill_calls_in_run_py']}")
    print(f"   distill() 的全部调用方: {c['distill_callers']}")

    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        print(f"\n已写 {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
