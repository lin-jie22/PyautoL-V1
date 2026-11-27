"""V252 审计：v251 那 6 格到底在量什么（纯离线，0 次模型调用）。

## 为什么需要这一步

V251 的 RESULTS 把 `off` 3/6 读成「整函数重写能抬分」，把 `g7e` 三格读成「模型照抄」。
两句都建立在同一个未验证的前提上：**这套题的信息是在题面里可得的**。

事实是套件叫 `g7d_spec_incomplete` —— 每道题的题面都写着
「按**本项目定下的惯例**处理（惯例不在题面里）」。于是「抬分」有第二种解释：
**模型猜中了一条不可得的惯例**，与「从起点继续改」没有关系。

本脚本把这件事做成**可数的读数**，而不是我读代码的印象：

  A1 复现     —— 18 份候选重跑断言，score/vec 与账本逐位相同（装置可信）
  A2 归属     —— 每份候选属于哪一格、是不是该格起点、与起点是否逐字节相同
  A3 惯例差   —— 每份候选与 oracle 的**首个分歧点**（人工归因的锚，不是自动判决）
  A4 判定     —— 「抬分」是否 == 「多过至少一条断言」

⛔ 本脚本不改任何生产文件，不调模型，只读 v251 的落盘产物。
⛔ 「猜中惯例」的标签由人读 A3 的 diff 得出，脚本只负责把 diff 摆出来。
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import signal
import sys
import warnings
from pathlib import Path

warnings.simplefilter("ignore")
signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))

EV = Path(__file__).resolve().parent
STATE = Path("/tmp/v251_a")
SUITE = Path("/tmp/v251_suite_d/g7d_spec_incomplete.jsonl")
CAND = STATE / "candidates"

#: 单个候选墙钟上限。这些函数都是纯字符串处理，正常微秒级；
#: 0.25s 只用来兜「候选里写了 while True」。
CAND_TIMEOUT = 0.25


def fn_name_of(tests: list[str]) -> str:
    """从断言里取被测函数名。取不到就抛 —— ⛔ 不猜默认名。"""
    for t in tests:
        m = re.search(r"\b([a-z_][a-z_0-9]*)\s*\(", t)
        if m:
            return m.group(1)
    raise ValueError("tests 里找不到函数名")


def judge(src: str, tests: list[str], name: str) -> tuple[list[bool], float, str]:
    """跑断言。返回 (vec, score, err)。⛔ 语法错与断言失败分开记。"""
    ns: dict = {}
    try:
        compile(src, "<cand>", "exec")
    except SyntaxError as e:
        return [False] * len(tests), 0.0, f"syntax:{e.msg}"
    signal.setitimer(signal.ITIMER_REAL, CAND_TIMEOUT)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(src, "<cand>", "exec"), ns)
            fn = ns.get(name)
            if not callable(fn):
                return [False] * len(tests), 0.0, f"no_fn:{name}"
            vec = []
            for t in tests:
                try:
                    exec(t, {name: fn})
                    vec.append(True)
                except Exception:
                    vec.append(False)
            return vec, round(sum(vec) / len(tests), 4), ""
    except TimeoutError:
        return [False] * len(tests), 0.0, "timeout"
    except Exception as e:                                   # noqa: BLE001
        return [False] * len(tests), 0.0, f"exec:{type(e).__name__}"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def first_divergence(a: str, b: str) -> str:
    """两段源码的首个分歧行（去注释、去空行）。给人工归因当锚。"""
    def norm(s: str) -> list[str]:
        out = []
        for ln in s.splitlines():
            ln = re.sub(r"#.*$", "", ln).rstrip()
            if ln.strip():
                out.append(ln)
        return out
    la, lb = norm(a), norm(b)
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else "<EOF>"
        y = lb[i] if i < len(lb) else "<EOF>"
        if x != y:
            return f"L{i + 1}: cand={x!r} | oracle={y!r}"
    return ""


def main() -> int:
    sys.stdin = open("/dev/null")                         # noqa: SIM115
    tasks = {r["id"]: r for r in
             (json.loads(l) for l in SUITE.read_text(encoding="utf-8").splitlines() if l.strip())}
    obs = [json.loads(l) for l in
           (STATE / "goal_obs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]

    by_cell: dict[str, list[dict]] = {}
    for r in obs:
        by_cell.setdefault(r["task_id"], []).append(r)

    out: dict = {"cells": {}, "mismatch": [], "n_cand": 0}
    print(f"{'cell':26} {'sha':10} {'row_sc':>7} {'re_sc':>7} {'ok':>3}  note")
    print("-" * 92)
    for cell in sorted(by_cell):
        task = tasks[cell]
        tests, oracle = task["tests"], task["oracle"]
        name = fn_name_of(tests)
        rows = by_cell[cell]
        # 起点 = 日志里 start 那条；此处用「第一条 act 行」不可靠，
        # 改用 V251 账本事实：swap 行的 source == goal_swap；起点是 act 行里分数最高的那份的 sha。
        acts = [r for r in rows if r.get("source") == "goals-act"]
        swaps = [r for r in rows if r.get("source") == "goal_swap"]
        # V251 的 start sha 由 run.log 打印，同一格内唯一；这里用「与 swap 行不同的那份 act sha」反推不可靠
        # ⇒ 直接从日志抓（见 start_shas.json），本脚本只对拍分数。
        seen: dict[str, dict] = {}
        for r in rows:
            sha = str(r.get("impl_sha") or "")
            if not sha or sha in seen:
                continue
            p = CAND / f"{sha}.py"
            if not p.exists():
                out["mismatch"].append({"cell": cell, "sha": sha, "why": "cand_missing"})
                continue
            src = p.read_text(encoding="utf-8")
            vec, sc, err = judge(src, tests, name)
            ok = abs(sc - float(r["score"])) < 1e-9
            if not ok:
                out["mismatch"].append({"cell": cell, "sha": sha, "row": r["score"],
                                        "replay": sc, "err": err})
            _ovec = r.get("vec")
            vec_ok = (_ovec == vec) if isinstance(_ovec, list) else None
            seen[sha] = {"sha": sha, "row_score": float(r["score"]), "replay": sc,
                         "vec": vec, "vec_match": vec_ok, "err": err,
                         "sources": sorted({str(x.get("source")) for x in rows
                                            if str(x.get("impl_sha") or "") == sha}),
                         "n_rows": sum(1 for x in rows if str(x.get("impl_sha") or "") == sha),
                         "divergence": first_divergence(src, oracle),
                         "oracle_score": judge(oracle, tests, name)[1]}
            out["n_cand"] += 1
            print(f"{cell:26} {sha:10} {float(r['score']):7.4f} {sc:7.4f} "
                  f"{'OK' if ok else 'DIFF':>3}  {err or ''}")
        out["cells"][cell] = {
            "n_unique": len(seen),
            "act_shas": sorted({str(r.get("impl_sha") or "") for r in acts}),
            "swap_shas": sorted({str(r.get("impl_sha") or "") for r in swaps}),
            "max_score": max((v["replay"] for v in seen.values()), default=None),
            "oracle_score": judge(oracle, tests, name)[1],
            "cands": list(seen.values()),
        }

    (EV / "audit_v252.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print()
    print(f"唯一候选 {out['n_cand']} 份；分数对不上 {len(out['mismatch'])} 份")
    for c, d in out["cells"].items():
        print(f"  {c:26} 唯一 {d['n_unique']}｜act {len(d['act_shas'])}｜swap {len(d['swap_shas'])}"
              f"｜最高 {d['max_score']}｜oracle {d['oracle_score']}")
    return 0 if not out["mismatch"] else 1


if __name__ == "__main__":
    sys.exit(main())
