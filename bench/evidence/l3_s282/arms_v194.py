#!/usr/bin/env python3
"""V194：量「免费闸接进 bench 之前，筛掉的有多少真该筛」。

⛔ 本脚本不改内核、不造题。它只做三件事：
   1. 从 bench 落盘产物里取出（答案, 真分数）；
   2. 对每个答案调**真入口** `run.py --verify-impl`（纪律 95：不调副本）；
   3. 数触发率 / 精密度 / 召回，并给两个方向的自证伪。

判据见 PREREG_v194.md §3~§6，读之前不许改。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]          # …/V1
OUT = Path(__file__).resolve().parent
BENCH = ROOT / "bench_out"
#: ⚠️ `bench_out/` 是被 gitignore 掉的运行时目录。证据目录里冻结了一份**本轮**的
#:    副本（`answers_v194.jsonl` / `runs_v194.jsonl`，43 + 10 行），
#:    这样 `bench_out` 被清掉之后本脚本仍可复跑（核对结论用）。
FROZEN = OUT if not (BENCH / "answers.jsonl").exists() else BENCH

#: 打分器那份抽取器 —— ⛔ 不另写一套（纪律 61：闸看的代码必须与判分跑的同一段）
_spec = importlib.util.spec_from_file_location(
    "_v194_scorer", ROOT / "bench" / "scorers" / "code_exec.py")
_sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sc)                        # type: ignore[union-attr]
extract_code = _sc.extract_code

#: 从题面 `tests` 里认出「被调用的函数名」时要跳过的内建
_STOP = {
    "assert", "print", "len", "str", "int", "float", "bool", "list", "dict", "set",
    "tuple", "range", "abs", "sum", "min", "max", "open", "isinstance", "type",
    "round", "enumerate", "zip", "map", "filter", "any", "all", "sorted", "repr",
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError", "True", "False",
    "None", "self", "super", "getattr", "setattr", "hasattr", "bytes", "divmod",
}


def suites() -> list[Path]:
    return sorted((ROOT / "bench" / "heldout").glob("*.jsonl")) + [
        ROOT / "bench" / "tasks" / "coding_small.jsonl"]


def load_tasks() -> dict[tuple[str, str], dict]:
    """(suite, id) → task。

    ⚠️ 键**必须**带 suite：同一题出现在多个套件里（`g7d2` 在 g7m 两个套件、
    `g7e1` 在 g7e 与 g7j_n4），只用 id 会互相覆盖 —— 本轮初版就这么错了，
    33 格被压成 19 格、分数张冠李戴。
    """
    out: dict[tuple[str, str], dict] = {}
    for p in suites():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            t = json.loads(ln)
            t["_suite"] = p.stem          # ⚠️ header 里记的是 stem，不是文件名
            out[(p.stem, t["id"])] = t
    return out


def run_suites() -> dict[str, str]:
    """run_id → suite（取自 `answers.jsonl` 的 header 行）。

    ⛔ 不从「第几个套件」推：那是靠顺序猜，一处乱序就全错。
    """
    out: dict[str, str] = {}
    p = FROZEN / ("answers_v194.jsonl" if FROZEN is OUT else "answers.jsonl")
    if not p.exists():
        return out
    for ln in p.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        d = json.loads(ln)
        if d.get("kind") != "header":
            continue
        meta = d.get("meta")
        suite = ""
        if isinstance(meta, dict):
            suite = str(meta.get("suite") or "")
        elif isinstance(meta, str):
            try:
                suite = str(ast.literal_eval(meta).get("suite") or "")
            except (ValueError, SyntaxError):
                suite = ""
        out[str(d.get("run_id"))] = Path(suite).stem
    return out


def def_names(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    return [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def called_names(code: str) -> list[str]:
    """题面里被调用的名字（按出现次数降序）。⛔ 不执行题面，只读 AST。"""
    cnt: dict[str, int] = {}
    for line in str(code).splitlines():
        try:
            tree = ast.parse(line.strip())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                nm = node.func.id
                if nm in _STOP:
                    continue
                cnt[nm] = cnt.get(nm, 0) + 1
    return [k for k, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))]


def pick_fn(task: dict) -> tuple[str, str]:
    """返回 (函数名, 来源)。来源 ∈ {from_tests, from_oracle, none}。"""
    tests = "\n".join(str(t) for t in (task.get("tests") or []))
    cands = called_names(tests)
    od = set(def_names(str(task.get("oracle") or "")))
    for c in cands:                      # 题面里调过、且参考解里也定义过 —— 最可靠
        if c in od:
            return c, "from_tests"
    if cands:
        return cands[0], "from_tests"
    if od:
        return sorted(od)[0], "from_oracle"
    return "", "none"


def gate(code: str, fn: str, *, timeout: float = 180.0) -> dict:
    """调**真入口**。返回 {rc, tally, rows, stderr_tail}。"""
    if not code.strip() or not fn:
        return {"rc": 2, "tally": None, "rows": None,
                "why": "无代码或无函数名（闸不适用）"}
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(code)
        tmp = Path(fh.name)
    try:
        r = subprocess.run([sys.executable, "-u", str(ROOT / "run.py"),
                            "--verify-impl", str(tmp), "--fn", fn, "--json"],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"rc": 124, "tally": None, "rows": None, "why": "入口超时"}
    finally:
        tmp.unlink(missing_ok=True)
    raw = (r.stdout or "").strip()
    data = None
    i = raw.find("{")                     # 与 V193 同一读法：允许前面有打印行
    if i >= 0:
        try:
            data = json.loads(raw[i:])
        except json.JSONDecodeError:
            data = None
    if r.returncode != 0 or data is None:
        tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        return {"rc": r.returncode, "tally": None, "rows": None,
                "why": (tail[-1][:160] if tail else "无输出")}
    return {"rc": 0, "tally": data.get("tally") or {}, "rows": data.get("rows") or [],
            "n_args": data.get("n_args")}


def verdict(g: dict) -> str:
    """PREREG §3 的判决。⛔ 阈值写死在这里，读数出来后不许调。"""
    if g["rc"] != 0 or g["tally"] is None:
        return "ABSTAIN"
    if int(g["tally"].get("VARIES", 0)) == 0:
        return "DROP"
    return "KEEP"


def main() -> int:
    t0 = time.time()
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    tasks = load_tasks()
    rid_suite = run_suites()

    # ── 读答案（只取本轮 run_id）────────────────────────────────────
    ans_rows: list[dict] = []
    for ln in FROZEN.joinpath("answers_v194.jsonl" if FROZEN is OUT else "answers.jsonl"
                           ).read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        d = json.loads(ln)
        if d.get("kind") != "answer":
            continue
        try:
            ts = int(str(d.get("run_id", "0")).split("@")[-1])
        except ValueError:
            ts = 0
        if ts >= start:
            d["_suite"] = rid_suite.get(str(d.get("run_id")), "?")
            ans_rows.append(d)

    # ── 读分数（同 run_id 范围；键 = (suite, id, rep)）──────────────
    scores: dict[tuple[str, str, int], float] = {}
    provs: dict[str, dict] = {}
    for ln in FROZEN.joinpath("runs_v194.jsonl" if FROZEN is OUT else "runs.jsonl"
                        ).read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        d = json.loads(ln)
        try:
            ts = int(str(d.get("run_id", "0")).split("@")[-1])
        except ValueError:
            ts = 0
        if ts < start:
            continue
        suite = rid_suite.get(str(d.get("run_id")), "?")
        rep = d.get("report") or {}
        for row in rep.get("per_task") or []:
            scores[(suite, row["id"], int(row.get("rep", 0)))] = float(row["score"])
        provs[str(d.get("run_id"))] = (rep.get("provenance") or {})

    print("=" * 78)
    print(f"V194 免费闸 × bench 候选筛选　答案 {len(ans_rows)} 条　分数 {len(scores)} 条")
    print("=" * 78)

    # ── I3 分母闭合 ────────────────────────────────────────────────
    missing = [d for d in ans_rows
               if (d["_suite"], d["task_id"], int(d.get("rep", 0))) not in scores]
    cells = [(d["_suite"], d["task_id"], int(d.get("rep", 0))) for d in ans_rows]
    dup = {k: v for k, v in __import__("collections").Counter(cells).items() if v > 1}
    I3 = (len(missing) == 0 and len(ans_rows) > 0 and not dup)

    # ── 逐答案过闸 ─────────────────────────────────────────────────
    rows: list[dict] = []
    for d in ans_rows:
        tid, rep, suite = d["task_id"], int(d.get("rep", 0)), d["_suite"]
        task = tasks.get((suite, tid))
        if task is None:
            print(f"⚠️ 题 {suite}/{tid} 不在任何 suite 里（跳过）")
            continue
        code = extract_code(d.get("answer") or "")
        fn, src = pick_fn(task)
        g = gate(code, fn)
        v = verdict(g)
        rows.append({
            "id": tid, "rep": rep, "suite": suite, "fn": fn, "fn_src": src,
            "score": scores.get((suite, tid, rep)),
            "verdict": v, "rc": g["rc"], "tally": g["tally"], "why": g.get("why", ""),
            "code_sha": __import__("hashlib").sha256(code.encode()).hexdigest()[:16],
            "code_chars": len(code),
        })

    # ── I1 抽取一致性 ──────────────────────────────────────────────
    # ⚠️ 如实记：这一条**按构造不可能失败** —— 闸喂给入口的代码就是
    #    `extract_code(answer)` 的返回值，再比一次是恒真式，不是读数。
    #    留它只为把「闸看的 == 判分跑的」写成可核对的**结构性事实**；
    #    ⛔ 不许把它当成证据计数（V159 恒真式当读数的坑）。
    I1 = callable(extract_code) and all(
        (r["code_chars"] > 0) or not (next(
            d for d in ans_rows if d["_suite"] == r["suite"] and d["task_id"] == r["id"]
            and int(d.get("rep", 0)) == r["rep"]).get("answer") or "").strip()
        for r in rows)

    # ── I2 血缘 ────────────────────────────────────────────────────
    kinds = {p.get("llm_kind") for p in provs.values()}
    models = {p.get("model") for p in provs.values()}
    I2 = (kinds == {"http"}) and all("qwen2.5:7b" in str(m) for m in models)

    # ── 混淆矩阵 ───────────────────────────────────────────────────
    drop = [r for r in rows if r["verdict"] == "DROP"]
    keep = [r for r in rows if r["verdict"] == "KEEP"]
    abst = [r for r in rows if r["verdict"] == "ABSTAIN"]
    zero = [r for r in rows if r["score"] == 0.0]
    imperfect = [r for r in rows if r["score"] is not None and r["score"] < 1.0]

    drop_zero = [r for r in drop if r["score"] == 0.0]
    zero_drop = [r for r in zero if r["verdict"] == "DROP"]

    n = len(rows)
    prec = (len(drop_zero) / len(drop)) if drop else None
    rec = (len(zero_drop) / len(zero)) if zero else None

    R = {
        "n_answers": n,
        "n_scored": sum(1 for r in rows if r["score"] is not None),
        "n_drop": len(drop), "n_keep": len(keep), "n_abstain": len(abst),
        "n_zero": len(zero), "n_imperfect": len(imperfect),
        "drop_and_zero": len(drop_zero),
        "precision": prec, "recall": rec,
        "imperfect_precision": (len([r for r in drop if r["score"] < 1.0]) / len(drop)) if drop else None,
        "imperfect_recall": (len([r for r in drop if r["score"] < 1.0]) / len(imperfect)) if imperfect else None,
        "I1": I1, "I2": I2, "I3": I3, "llm_kinds": sorted(map(str, kinds)),
        "llm_models": sorted(map(str, models)),
        "rows": rows,
    }

    print(f"\n[分母] 答案 {n} 条，join 到分数 {R['n_scored']} 条，缺 {len(missing)} 条 "
          f"⇒ I3 {'✅' if I3 else '❌'}")
    print(f"[血缘] llm_kind={sorted(map(str, kinds))} model={sorted(map(str, models))} "
          f"⇒ I2 {'✅' if I2 else '❌ 作废'}")
    print(f"[抽取] 闸与打分器同一段代码 ⇒ I1 {'✅' if I1 else '❌'}")

    print("\n逐题：")
    print(f"  {'题':<24}{'suite':<26}{'分':>5}  {'判决':<8}{'VARIES':>7}{'CONST':>7}"
          f"{'其他':>5}  fn")
    for r in rows:
        t = r["tally"] or {}
        other = sum(v for k, v in t.items() if k.startswith("STRUCT")) + t.get("FAULT", 0)
        print(f"  {r['id']:<24}{r['suite']:<26}{str(r['score']):>5}  {r['verdict']:<8}"
              f"{t.get('VARIES', 0):>7}{t.get('CONST', 0):>7}{other:>5}  {r['fn']}")

    print("\n混淆矩阵（真值：score==0 视为「真该筛」）")
    print(f"  筛掉 DROP {len(drop)}　放行 KEEP {len(keep)}　弃权 ABSTAIN {len(abst)}")
    print(f"  真该筛（score==0）{len(zero)} 条；其中被筛掉 {len(zero_drop)} 条")
    print(f"  精密度 = {len(drop_zero)}/{len(drop)} = {prec if prec is None else round(prec, 3)}")
    print(f"  召回　 = {len(zero_drop)}/{len(zero)} = {rec if rec is None else round(rec, 3)}")

    # ── 事后拆分（⛔ 不是预注册判据，只用来定位病因）─────────────────
    # `VARIES==0` 有两种完全不同的成因，混在一格里会把「探不进去」读成「没差异」：
    #   A. 一格合法域都没有（STRUCT:TYPES / FAULT 占满）⇒ 闸根本没跑起候选；
    #   B. 有合法格，但**每一格**输出都相同（CONST）⇒ 这才是「与桩不可分」。
    def bucket(r: dict) -> str:
        t = r["tally"] or {}
        legal = int(t.get("VARIES", 0)) + int(t.get("CONST", 0))
        if legal == 0:
            return "NO_DOMAIN"          # 全是 STRUCT / FAULT
        return "CONST_ONLY" if int(t.get("VARIES", 0)) == 0 else "VARIES"
    for r in rows:
        r["bucket"] = bucket(r)
    nd_ = [r for r in rows if r["bucket"] == "NO_DOMAIN"]
    co_ = [r for r in rows if r["bucket"] == "CONST_ONLY"]
    print("\n事后拆分（⛔ 预注册没写，只作病因定位）：")
    print(f"  NO_DOMAIN（一格都探不进去）{len(nd_)} 条："
          f"其中 score>0 = {len([r for r in nd_ if (r['score'] or 0) > 0])} 条")
    print(f"  CONST_ONLY（有合法格但全同）{len(co_)} 条："
          f"其中 score>0 = {len([r for r in co_ if (r['score'] or 0) > 0])} 条")
    R["n_no_domain"] = len(nd_)
    R["n_const_only"] = len(co_)
    R["no_domain_pos"] = len([r for r in nd_ if (r["score"] or 0) > 0])
    R["const_only_pos"] = len([r for r in co_ if (r["score"] or 0) > 0])
    R["posthoc_const_only_precision"] = (
        len([r for r in co_ if r["score"] == 0.0]) / len(co_)) if co_ else None
    R["posthoc_const_only_recall"] = (
        len([r for r in co_ if r["score"] == 0.0]) / len(zero)) if zero else None
    print(f"  只认 CONST_ONLY 当 DROP ⇒ 精密度={R['posthoc_const_only_precision']}，"
          f"召回={R['posthoc_const_only_recall']}（事后口径，⛔ 未预注册）")

    # ── P1~P7 ─────────────────────────────────────────────────────
    P = {
        "P1": R["n_drop"] <= 2,
        "P2": (prec == 1.0) if prec is not None else None,
        "P3": (rec <= 0.5) if rec is not None else None,
        "P4": R["n_abstain"] == 0,
        "P5": len(zero) >= 2,
        "P7": (R["n_drop"] / n) < 0.10 if n else None,
    }
    print("\n预注册：")
    for k, v in P.items():
        print(f"  {k}: {'✅ 成立' if v else ('UNKNOWN' if v is None else '❌ 不成立')}")

    # ── P6 装置自证：两个方向都要动 ────────────────────────────────
    def with_gate(kind: str) -> tuple[int, float | None]:
        if kind == "always_keep":
            ds = []
        elif kind == "always_drop":
            ds = [r for r in rows]
        prec2 = (len([r for r in ds if r["score"] == 0.0]) / len(ds)) if ds else None
        return len(ds), prec2
    nk, _ = with_gate("always_keep")
    nd, pd_ = with_gate("always_drop")
    if len(drop) == 0:
        # 闸一次没触发 ⇒ 第一向恒中、第二向无法检验。⛔ 记 UNKNOWN，不记 ❌ 也不记 ✅。
        P6 = None
        why6 = "闸一次没触发 ⇒ 精密度无分母，自证不可完成"
    else:
        P6 = (nk == 0) and (pd_ is not None and prec is not None and pd_ < prec)
        why6 = f"恒 KEEP ⇒ drop={nk}（须 0）；恒 DROP ⇒ 精密度={round(pd_, 3)}（须 < {round(prec, 3)}）"
    P["P6"] = P6
    print(f"  P6 自证：{why6} ⇒ {'✅' if P6 else ('UNKNOWN' if P6 is None else '❌')}")
    R["P"] = P

    # ── I4 幂等抽查 3 条（确定性取样：按题名排序取前 3，⛔ 不用 random）──
    sample = sorted(rows, key=lambda r: (r["id"], r["rep"]))[:3]
    idem = []
    for r in sample:
        task = tasks[(r["suite"], r["id"])]
        d = next(x for x in ans_rows if x["_suite"] == r["suite"] and x["task_id"] == r["id"]
                 and int(x.get("rep", 0)) == r["rep"])
        g2 = gate(extract_code(d.get("answer") or ""), r["fn"])
        idem.append({"id": r["id"], "same": (g2["tally"] == r["tally"])})
    R["I4"] = all(x["same"] for x in idem)
    print(f"\n[幂等] 抽查 {len(idem)} 条：{[x['same'] for x in idem]} ⇒ "
          f"I4 {'✅' if R['I4'] else '❌'}")

    R["elapsed_s"] = round(time.time() - t0, 1)
    OUT.joinpath("readings_v194.json").write_text(
        json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n读数 → {OUT/'readings_v194.json'}　用时 {R['elapsed_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
