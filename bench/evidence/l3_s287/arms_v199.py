#!/usr/bin/env python3
"""V199 —— 累积的**可用量**：3055 条 episode 里有几条能到达下一步的输入。

三层：
  L1 静态：每种 kind 的「活读点」（调用点所在模块在部署闭包内）
  L2 截断：读点的 limit 字面量 vs 写入量
  L3 动态：构造真 Core 跑一题（StubLLM），读 `state_log` 原文，
          数其中有几条 episode / fact 是**字面出现**的

⛔ 不改内核；⛔ 只在 `V1_STATE_DIR` 的副本上跑（`--memory-dir` 语义）。
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
SRC_STATE = ROOT / "state"
WORK = Path("/tmp/v199_state")
DB = WORK / "memory.db"
SUITE_DIR = ROOT / "bench" / "heldout"
EXTRA_SUITE = ROOT / "bench" / "tasks" / "coding_small.jsonl"

#: 三题采样：满分题（t03）、半对题（t02 是 1.0，换 t04）、heldout 一题。
SAMPLES = (("coding_small", "t03_commas"),
           ("coding_small", "t04_merge_ranges"),
           ("g7_heldout", "g7s2_parse_headers"))

sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------- L1 静态
def scan_episodes_calls() -> list[dict]:
    """AST 扫全库的 `X.episodes(...)` 调用，取 kind / limit 字面量。"""
    out = []
    for p in sorted(list((ROOT / "mw").glob("*.py")) + [ROOT / "run.py"]):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not (isinstance(f, ast.Attribute) and f.attr == "episodes"):
                continue
            kind = limit = None
            for kw in node.keywords:
                if kw.arg == "kind" and isinstance(kw.value, ast.Constant):
                    kind = kw.value.value
                if kw.arg == "limit" and isinstance(kw.value, ast.Constant):
                    limit = kw.value.value
            out.append({"file": str(p.relative_to(ROOT)), "line": node.lineno,
                        "kind": kind, "limit": limit})
    return out


def enclosing_funcs() -> list[tuple[str, int, str]]:
    """(file, lineno, 外层函数名) —— 用来把读点从「模块级」收紧到「函数级」。"""
    out = []
    for p in sorted(list((ROOT / "mw").glob("*.py")) + [ROOT / "run.py"]):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        def walk(node, cur=None):                       # noqa: ANN001
            for ch in ast.iter_child_nodes(node):
                name = cur
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = ch.name
                if isinstance(ch, ast.Call):
                    f = ch.func
                    if isinstance(f, ast.Attribute) and f.attr == "episodes":
                        out.append((str(p.relative_to(ROOT)), ch.lineno, name or "<module>"))
                walk(ch, name)

        walk(tree)
    return out


def scan_record_calls() -> list[dict]:
    """AST 扫 `record(kind, ...)` 的写入点，用来对照「谁写了哪个 kind」。"""
    out = []
    for p in sorted(list((ROOT / "mw").glob("*.py")) + [ROOT / "run.py"]):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Attribute) and f.attr in ("record", "record_reward"):
                    k = None
                    if f.attr == "record" and node.args and isinstance(node.args[0], ast.Constant):
                        k = node.args[0].value
                    out.append({"file": str(p.relative_to(ROOT)), "line": node.lineno,
                                "fn": f.attr, "kind": k})
    return out


# ---------------------------------------------------------------- 库读数
def db_counts() -> dict:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        eps = dict(con.execute("select kind, count(*) from episodes group by kind").fetchall())
        facts = con.execute("select count(*) from facts").fetchone()[0]
        n_eps = con.execute("select count(*) from episodes").fetchone()[0]
        return {"episodes_by_kind": eps, "episodes_total": n_eps, "facts": facts}
    finally:
        con.close()


def db_rows(kind: str, limit: int) -> list[str]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return [r[0] for r in con.execute(
            "select content from episodes where kind=? order by id desc limit ?", (kind, limit))]
    finally:
        con.close()


def load_suites() -> dict[str, dict[str, dict]]:
    """suite 名（stem）→ {task_id: 题记录}。"""
    out: dict[str, dict[str, dict]] = {}
    for p in sorted(SUITE_DIR.glob("*.jsonl")) + [EXTRA_SUITE]:
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        out[p.stem] = {str(r["id"]): r for r in rows}
    return out


# ---------------------------------------------------------------- L3 动态
def run_one(goal: str, features=None) -> tuple[list[str], list[str], set[str], set[str], str]:
    """构造真 Core 跑一题（StubLLM）。

    返回 (state 原文, guidance 原文, 实际 import 的 mw 模块, 执行到的行, 状态行)。
    ⚠️ `features` **必须传**（自踩 5）：`reward_guidance()` 的第一行就是
       `if not features: return ""`；不传就等于把那条通道关掉再去量它——
       与纪律 75「测量臂关掉的功能，先查部署里有没有」同型。
       bench 侧的计算点是 `run.py:2377 features=_feat_of.get(t.id)`。
    """
    os.environ["V1_STATE_DIR"] = str(WORK)
    from mw import config as cfg
    from mw.core import Core
    from mw.guard import Guard
    from mw.llm import StubLLM

    called: set[str] = set()

    def tracer(frame, event, arg):                      # noqa: ANN001
        """记 **行级** 事件：`file:lineno`。

        ⚠️ 为什么不用函数级（自踩 4）：`_close_out()` 被调用 ≠ 它里面那行 `episodes(...)`
           被执行到 —— 那行在 `if not best:` 兜底分支里，本次 `best` 非空 ⇒ 跳过。
           函数级会把这种「分支没走到」误判成活读点。
        """
        fn = frame.f_code.co_filename
        if "/mw/" not in fn:
            return None
        if event == "line":
            called.add(f"{fn.rsplit('/', 1)[-1]}:{frame.f_lineno}")
        return tracer

    s = cfg.settings()
    guard = Guard(allow_shell=False, workspace=s.paths.workspace,
                  max_consecutive_failures=s.limits.max_consecutive_failures)
    core = Core(s, llm=StubLLM(), guard=guard)
    sys.settrace(tracer)
    try:
        core.run(goal, task_id="v199_probe",
                 features=frozenset(features) if features is not None else None)
    finally:
        sys.settrace(None)
    states = [txt for _, txt in (core.state_log or [])]
    guides = [str((m or {}).get("guidance") or "") for m in (core.state_meta or [])]
    mods = {m for m in sys.modules if m.startswith("mw")}
    status = str(getattr(core, "last_status", "") or "")
    return states, guides, mods, called, status


def count_hits(blob: str, kind: str, rows: list[str]) -> dict:
    """把 `blob` 里**字面出现**的 episode 条数数出来。

    ⚠️ 口径必须按内核的**实际渲染**，不是按原文（自踩 3）：
       · `[工作记忆]` 走 `_render` ⇒ `w[-200:]`（尾 200 字）
       · `[语义记忆]` 走 `f.text[:120]`（前 120 字）
    用整条去比会得到**假 0**（第一版就是这么错的）。
    """
    hit, checked = 0, 0
    for c in rows:
        c = str(c or "")
        if len(c) < 24:
            continue
        checked += 1
        probe = c[-200:] if len(c) > 200 else c     # episode 的实际渲染形态
        if probe and probe in blob:
            hit += 1
    return {"kind": kind, "checked": checked, "hit": hit}


def main() -> int:
    # ---- 副本
    if WORK.exists():
        shutil.rmtree(WORK)
    shutil.copytree(SRC_STATE, WORK)
    print(f"[副本] {SRC_STATE} → {WORK}（原库不动）")

    counts = db_counts()
    print(f"\n[库] episodes={counts['episodes_total']}  facts={counts['facts']}")
    for k, v in sorted(counts["episodes_by_kind"].items(), key=lambda kv: -kv[1]):
        print(f"     {k:10s} {v}")

    # ---- L1
    calls = scan_episodes_calls()
    writes = scan_record_calls()
    print(f"\n[L1] episodes() 读点 {len(calls)} 处；record() 写点 {len(writes)} 处")
    for c in calls:
        print(f"     {c['file']}:{c['line']}  kind={c['kind']}  limit={c['limit']}")

    # ---- L3（先跑，才知道哪些行真的被执行）
    rows_by_kind = {k: db_rows(k, 400) for k in counts["episodes_by_kind"]}
    suites = load_suites()
    from mw.features import feature_set

    print(f"\n[L3] 采样 {len(SAMPLES)} 题（StubLLM，不调模型，settrace 记**行级**执行）…")
    states, guides, called, mods = [], [], set(), set()
    per_task = []
    for suite, tid in SAMPLES:
        task = suites.get(suite, {}).get(tid)
        if not task:
            print(f"     ⚠️ 缺题 {suite}/{tid}")
            continue
        feats = feature_set(task)
        st, gd, md, cl, status = run_one(str(task.get("prompt") or ""), feats)
        print(f"     · {suite}/{tid:22s} features={len(feats):2d} "
              f"state={len(st)} 轮 记到 {len(cl)} 行  status={status or '-'}")
        states += st
        guides += gd
        called |= cl
        mods |= md
        per_task.append({"suite": suite, "id": tid, "n_features": len(feats),
                         "n_states": len(st), "lines": len(cl)})

    # ---- 活读点判定（**行级**）：那一行真的被执行到了
    fn_of = {(f, ln): fn for f, ln, fn in enclosing_funcs()}

    def live(c: dict) -> bool:
        return f"{Path(c['file']).name}:{c['line']}" in called

    alive = [c for c in calls if live(c)]
    dead = [c for c in calls if not live(c)]
    print(f"\n[L1] mw 模块 {len([m for m in mods if m.startswith('mw.')])} 个；"
          f"真跑中**被执行到**的 mw 代码行 {len(called)} 行")
    print(f"     活读点 {len(alive)} 处，死读点 {len(dead)} 处（判据=那一行真被执行到）")
    for c in alive:
        print(f"     ✅ 活 {c['file']}:{c['line']} {fn_of.get((c['file'], c['line']))}()"
              f" kind={c['kind']} limit={c['limit']}")
    for c in dead:
        print(f"     ⛔ 死 {c['file']}:{c['line']} {fn_of.get((c['file'], c['line']))}()"
              f" kind={c['kind']} limit={c['limit']}")
    # 正例对照：先证明 tracer 看得见（按文件统计被记录的行数）
    from collections import Counter
    per_file = Counter(x.split(":")[0] for x in called)
    print("     正例对照（tracer 自检：每文件被记录的行数）")
    for f, n in sorted(per_file.items(), key=lambda kv: -kv[1])[:8]:
        print(f"       {f:22s} {n} 行")
    if not per_file:
        print("       ⛔⛔ tracer 一行都没记到 ⇒ **装置哑火**，本轮 L1 读数作废")

    # ---- 覆盖表
    print("\n[L2] 每 kind 的 写入量 / 活读点 / limit 之和 / 最大覆盖")
    cover = {}
    for k, n in sorted(counts["episodes_by_kind"].items(), key=lambda kv: -kv[1]):
        lims = [int(c["limit"]) for c in alive if c["kind"] == k and isinstance(c["limit"], int)]
        cap = max(lims) if lims else 0
        cover[k] = {"written": n, "live_points": len(lims), "limit_max": cap,
                    "cover_ratio": round(min(cap, n) / n, 4) if n else 0.0}
        print(f"     {k:10s} 写={n:5d} 活读点={len(lims)} limit={lims} "
              f"覆盖≤{cap}/{n} = {cover[k]['cover_ratio']:.1%}")

    # 无 kind 过滤的读点（能读全部）
    all_kind = [c for c in alive if c["kind"] is None]
    print(f"     无 kind 过滤的活读点：{len(all_kind)} 处 {[(c['file'], c['limit']) for c in all_kind]}")

    # ---- L3 命中（blob = state 正文 + guidance，两条通道都要算）
    blob = "\n".join(states) + "\n" + "\n".join(guides)
    print(f"\n[L3] state 轮数={len(states)} 正文={sum(len(x) for x in states)} 字符"
          f"  guidance={sum(len(x) for x in guides)} 字符")
    hits = []
    for k in counts["episodes_by_kind"]:
        h = count_hits(blob, k, rows_by_kind.get(k, []))
        hits.append(h)
        print(f"     {k:10s} 参与比对={h['checked']:3d} 字面命中={h['hit']}")
    total_hit = sum(h["hit"] for h in hits)

    # facts（口径：`f.text[:120]`，见 `[语义记忆]` 的渲染）
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    fact_rows = [r[0] for r in con.execute("select text from facts")]
    con.close()
    f_hit = sum(1 for t in fact_rows if str(t)[:120] in blob)
    print(f"     facts      共 {len(fact_rows)} 条，字面命中 {f_hit}")

    # facts 通道：直接问一次 recall 返回几条（「语义记忆=4」是 facts 表行数，不是召回条数）
    os.environ["V1_STATE_DIR"] = str(WORK)
    from mw import config as _cfg
    from mw.memory import MemoryStore
    _s = _cfg.settings()
    _probe_goal = str(suites[SAMPLES[0][0]][SAMPLES[0][1]].get("prompt") or "")
    _ms = MemoryStore(_s.paths.db)
    try:
        rc = _ms.recall(_probe_goal, k=int(_s.limits.recall_k))
        rc_text = [f.text for f in rc]
    finally:
        _ms.close()
    print(f"\n     语义记忆通道：facts 表 {len(fact_rows)} 条；"
          f"用本题面 recall(k={int(_s.limits.recall_k)}) 返回 **{len(rc)}** 条")
    for t in rc_text:
        print(f"       · {t[:90]}")

    # guidance 里有几条 episode（`wm.guidance()` 是另一条通道）
    gblob = "\n".join(guides)
    g_hit = sum(h["hit"] for k in counts["episodes_by_kind"]
                for h in [count_hits(gblob, k, rows_by_kind.get(k, []))])
    print(f"     其中只出现在 guidance 里的 episode 条数：{g_hit}"
          f"（state 正文里 {total_hit} 条）")

    reading = {
        "db": counts,
        "calls": calls,
        "writes": writes,
        "alive": alive,
        "dead": dead,
        "cover": cover,
        "l3": {"n_states": len(states), "chars": sum(len(x) for x in states),
               "guide_chars": sum(len(x) for x in guides),
               "hits": hits, "episode_hit_total": total_hit, "guide_only_hits": g_hit,
               "facts_total": len(fact_rows), "facts_hit": f_hit,
               "mods": sorted(m for m in mods if m.startswith("mw")),
               "called_mw_fns": sorted(called), "per_task": per_task},
    }
    # 落 state 原文（证据要能复核：哪一条进了 prompt 必须可逐字对）
    (OUT / "states_v199.json").write_text(
        json.dumps({"states": states, "guides": guides}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    # 六条通道各自在 state 原文里出现了几次
    print("\n[通道] state 原文里的通道标记：")
    for mark in ("[工作记忆]", "[语义记忆]", "[经验]", "[外部评审]", "[轨迹]", "已知易错点"):
        print(f"     {mark:12s} {blob.count(mark)} 次")

    (OUT / "readings_v199.json").write_text(json.dumps(reading, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    print(f"\n→ {OUT/'readings_v199.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
