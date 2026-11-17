#!/usr/bin/env python3
"""V178 探针：P1~P7（PREREG_v178 §2 的判据唯一来源）。

⛔ 判据来自 `bench/evidence/l3_s266/PREREG_v178.md` §2，跑后不许改装置迁就
   （V174 P3 教训）。读数落同目录 `readings_v178.json`。
P4 的罐头 HTTP 是**本地回环**（127.0.0.1），不联网；真 CLI、真写侧、真打分器。
"""

from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCORER_PATH = ROOT / "bench" / "scorers" / "code_exec.py"
SUITE_PATH = ROOT / "bench" / "tasks" / "sys_small.jsonl"
EVID = ROOT / "bench" / "evidence" / "l3_s266"
sys.path.insert(0, str(ROOT))

from mw.bench import ExternalScorer, Scored, ScoreError, Suite, Task, run_suite  # noqa: E402
from mw import scoreinfo as SI  # noqa: E402

RESULTS: list[dict] = []


def check(pid: str, desc: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"id": pid, "desc": desc, "ok": bool(ok), "detail": detail})
    print(f"  {'✅' if ok else '❌'} {pid} {desc}" + (f" — {detail}" if detail else ""))


def _fence(code: str) -> str:
    return f"```python\n{code}\n```"


# ════════════════════════════════════════════════════════════════════
# P1 发射面（子进程真跑 code_exec）
# ════════════════════════════════════════════════════════════════════
def _score_raw(task: dict, answer: str) -> dict:
    proc = subprocess.run([sys.executable, str(SCORER_PATH)],
                          input=json.dumps({"task": task, "answer": answer}),
                          capture_output=True, text=True, timeout=60)
    return json.loads(proc.stdout.strip().splitlines()[-1])


def case_p1() -> None:
    print("\n## P1 发射面：有代码发 vec（T1 自洽）；无代码不发；逐测试独立")
    task = {"id": "t1", "prompt": "p",
            "tests": ["assert 1 == 1", "assert False"]}
    out_ok = _score_raw(task, _fence("pass"))
    v = out_ok.get("vec")
    ok_a = (isinstance(v, list) and len(v) == 2 and all(isinstance(b, bool) for b in v)
            and out_ok["score"] == round(sum(v) / 2, 4))
    check("P1a", "2-test+代码 ⇒ vec 恰 2 元素全 bool 且 score==round(mean(vec),4)（T1）",
          ok_a, f"vec={v} score={out_ok['score']}")

    out_nc = _score_raw(task, "   ")
    ok_b = ("vec" not in out_nc and out_nc["score"] == 0.0)
    check("P1b", "无可执行代码（extract_code 空）⇒ 无 vec 键（「没量到」不许全零冒充）",
          ok_b, f"keys={sorted(out_nc)}")

    task2 = {"id": "t2", "prompt": "p",
             "tests": ["assert True", "assert False"]}
    out_half = _score_raw(task2, _fence("pass"))
    ok_c = (out_half.get("vec") == [True, False] and out_half["score"] == 0.5)
    check("P1c", "逐测试独立：1/2 ⇒ vec==[true,false]、score==0.5",
          ok_c, f"vec={out_half.get('vec')} score={out_half['score']}")


# ════════════════════════════════════════════════════════════════════
# P2 传输面（ExternalScorer × python -c 罐头打分器）
# ════════════════════════════════════════════════════════════════════
def _canned_scorer(payload: dict) -> ExternalScorer:
    return ExternalScorer(
        [sys.executable, "-c", f"import json; print(json.dumps({payload!r}))"],
        name="canned")


def case_p2() -> None:
    print("\n## P2 传输面：合法/缺席/坏形态/长度错（fail-closed）+ 旧协议回归")
    s = _canned_scorer({"score": 0.5, "why": "w", "vec": [True, False]}).score(
        {"id": "x", "tests": ["a", "b"]}, "ans")
    check("P2a", "合法 vec ⇒ Scored.vec 逐元素还原（tuple[bool]）",
          s.vec == (True, False) and isinstance(s.vec, tuple),
          f"vec={s.vec}")

    s2 = _canned_scorer({"score": 1.0, "why": "w"}).score(
        {"id": "x", "tests": ["a"]}, "ans")
    check("P2b", "缺 vec ⇒ Scored.vec=None（老打分器合法）",
          s2.vec is None, f"vec={s2.vec}")

    arms = [({"score": 0.5, "vec": "xy"}, "非列表"),
            ({"score": 0.5, "vec": [1, 0]}, "元素非 bool"),
            ({"score": 0.5, "vec": [True]}, "长度≠tests"),
            ({"score": 0.5, "vec": None}, "显式 null")]
    arms_ok, details = True, []
    for payload, tag in arms:
        try:
            _canned_scorer(payload).score({"id": "x", "tests": ["a", "b"]}, "ans")
            arms_ok = False
            details.append(f"{tag}=没报错")
        except ScoreError:
            details.append(f"{tag}=拒")
    check("P2c", "坏形态四臂全部 ScoreError（fail-closed，不许静默丢）",
          arms_ok, " ".join(details))

    # 旧协议回归：detail 的三种行为一字不变
    d1 = _canned_scorer({"score": 0.5, "why": "w",
                         "detail": [{"id": "a1", "ok": False, "why": "x"}]}).score(
        {"id": "x", "tests": ["a"]}, "ans")
    try:
        _canned_scorer({"score": 0.5, "detail": "bad"}).score(
            {"id": "x", "tests": ["a"]}, "ans")
        d_bad = False
    except ScoreError:
        d_bad = True
    d3 = _canned_scorer({"score": 0.0, "why": ""}).score(
        {"id": "x", "tests": ["a"]}, "ans")
    ok_d = (d1.detail == ({"id": "a1", "ok": False, "why": "x"},) and d_bad
            and d3.detail == () and d3.vec is None and d3.score == 0.0)
    check("P2d", "旧协议回归：detail 合法收/坏拒/缺席空；score/why 行为不变",
          ok_d, f"detail={d1.detail} bad拒={d_bad} 空={d3.detail}")


# ════════════════════════════════════════════════════════════════════
# P3 跑题链（run_suite × stub scorer）
# ════════════════════════════════════════════════════════════════════
class _StubScorer:
    def __init__(self, vec):
        self._v = vec

    def sha(self) -> str:
        return "stub"

    def score(self, task, ans):
        return Scored(0.5, "w", (), self._v)


def case_p3() -> None:
    print("\n## P3 跑题链：vec 在 ⇒ 行带值；None ⇒ 键不在")
    suite = Suite(name="s", tasks=(Task(id="t1", prompt="p", tests=("assert True",)),))
    _, rep1 = run_suite(lambda t: "ans", _StubScorer((True, False)), suite)
    _, rep2 = run_suite(lambda t: "ans", _StubScorer(None), suite)
    r1, r2 = rep1.per_task[0], rep2.per_task[0]
    check("P3a", "Scored.vec 在 ⇒ per_task 行带 vec 原值",
          r1.get("vec") == (True, False) and "vec" in r1, f"row vec={r1.get('vec')}")
    check("P3b", "Scored.vec=None ⇒ 行**无 vec 键**（⛔ 不写 None 值冒充）",
          "vec" not in r2, f"keys={sorted(r2)}")


# ════════════════════════════════════════════════════════════════════
# P4 真 e2e（run.py --bench --solver llm-direct × 罐头 HTTP × sys_small）
# ════════════════════════════════════════════════════════════════════
class _CannedHandler(http.server.BaseHTTPRequestHandler):
    answers: dict[str, str] = {}
    default: str = _fence("pass")

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            body = {}
        msgs = body.get("messages") or []
        prompt = msgs[-1].get("content", "") if msgs and isinstance(msgs[-1], dict) else ""
        content = self.answers.get(prompt, self.default)
        resp = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *args):  # noqa: D102
        pass


def _run_cli(tmp: Path, port: int) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update({
        "V1_LLM_BASE_URL": f"http://127.0.0.1:{port}/v1",
        "V1_LLM_API_KEY": "probe",
        "V1_LLM_MODEL": "probe-canned",
        "V1_STATE_DIR": str(tmp / "state"),
        "V1_BENCH_DIR": str(tmp / "bench"),
    })
    cmd = [sys.executable, str(ROOT / "run.py"), "--bench",
           "--solver", "llm-direct",
           "--suite", str(SUITE_PATH),
           "--ledger", str(tmp / "score_ledger.jsonl"),
           "--temperature", "0", "--no-answers"]
    return subprocess.run(cmd, capture_output=True, text=True, env=env,
                          cwd=str(ROOT), timeout=240)


def case_p4(tasks: list[dict], port: int, tmp: Path):
    print("\n## P4 真 e2e：真 CLI × 罐头端点 × sys_small × 隔离 tmp（三档答案 × 连跑）")
    variants = ["oracle", "partial", "junk", "oracle"]  # 第 4 轮复跑 oracle（P7）
    ledger_runs: list[list[dict]] = []
    served: list[dict[str, str]] = []          # 每轮 prompt→content（对拍用）
    ok_cli, cli_detail = True, []
    n_seen = 0
    for vi, variant in enumerate(variants, 1):
        ans_map: dict[str, str] = {}
        for t in tasks:
            code = (t.get("oracle") if variant == "oracle"
                    else t.get("partial") if variant == "partial" else "pass")
            ans_map[t["prompt"]] = _fence(code or "pass")
        _CannedHandler.answers = ans_map
        served.append(ans_map)
        p = _run_cli(tmp, port)
        rows = [json.loads(ln) for ln in
                (tmp / "score_ledger.jsonl").read_text(encoding="utf-8").splitlines()
                if ln.strip()] if (tmp / "score_ledger.jsonl").exists() else []
        rows_vi = rows[n_seen:]          # ⛔ 不按 session 号过滤 —— 行增量才稳
        n_seen = len(rows)
        ledger_runs.append(rows_vi)
        cli_detail.append(f"run{vi}({variant}): rc={p.returncode} rows={len(rows_vi)}")
        if p.returncode != 0 or len(rows_vi) != len(tasks):
            ok_cli = False
    check("P4a-0", "四轮 CLI 全部完成且各轮账本行数 == 题数（会话号递增）",
          ok_cli, " ".join(cli_detail))

    # P4a：行含 vec 且逐行过 §1.5 闸（自洽）
    all_rows = [r for run in ledger_runs for r in run]
    pool_all, counts_all = SI.beh_pool_from_rows(all_rows, cell=None)
    has_vec = all("vec" in r for r in all_rows)
    ok_a = has_vec and counts_all["n_inconsistent"] == 0 and counts_all["n_kept"] == len(all_rows)
    check("P4a", "账本行全含 vec 且逐行自洽（beh_pool 全收、n_inconsistent==0）",
          ok_a, f"counts={counts_all}")

    # P4b：T1 落账不改值 —— 与罐头答案直接判分对拍
    scorer = ExternalScorer([sys.executable, str(SCORER_PATH)], source=SCORER_PATH)
    diffs = []
    for vi, (run, amap) in enumerate(zip(ledger_runs, served), 1):
        for r in run:
            t = next((tt for tt in tasks if tt["id"] == r["task_id"]), None)
            if t is None:
                diffs.append(f"run{vi}:{r['task_id']} 未知题")
                continue
            direct = float(scorer.score(
                {"id": t["id"], "prompt": t["prompt"], "tests": t["tests"]},
                amap[t["prompt"]]).score)
            if abs(direct - float(r["score"])) > 1e-12:
                diffs.append(f"run{vi}:{r['task_id']} 账本{r['score']}≠直接{direct}")
    check("P4b", "T1 落账不改值：账本 score 与罐头答案直接判分逐行对拍（1e-12）",
          not diffs, "; ".join(diffs[:4]) or f"{len(all_rows)} 行全对上")

    # P4c：三档答案连跑 ⇒ 同格多实现/多行为（每格 3 行为）
    beh_n = {}
    for t in tasks:
        pool, _ = SI.beh_pool_from_rows(all_rows, cell=t["id"])
        beh_n[t["id"]] = len({v for _, v, _ in pool})
    deep = [k for k, n in beh_n.items() if n >= 2]
    check("P4c", "三档答案连跑 ⇒ 每格 ≥2 行为进池（oracle/partial/junk 三档向量）",
          len(deep) == len(tasks), f"behaviors/格={beh_n}")

    # P7 原料：run1 与 run4（同 oracle 复跑）行对拍
    r1 = [{k: v for k, v in r.items() if k != "session"} for r in ledger_runs[0]]
    r4 = [{k: v for k, v in r.items() if k != "session"} for r in ledger_runs[3]]
    same = r1 == r4
    check("P7a", "同罐头答案双跑 ⇒ 账本行除 session 号外逐字相同",
          same, f"run1={len(r1)}行 run4={len(r4)}行")
    return all_rows, ledger_runs, served


# ════════════════════════════════════════════════════════════════════
# P5 读闸（beh_pool_from_rows 变异臂）
# ════════════════════════════════════════════════════════════════════
def case_p5() -> None:
    print("\n## P5 读闸：四桶计数精确、fail-open 不抛、容差边界")
    rows = [
        {"task_id": "x", "impl_sha": "a", "score": 0.5, "vec": [True, False]},   # keep
        {"task_id": "x", "impl_sha": "b", "score": 0.5},                          # no_vec
        {"task_id": "x", "impl_sha": "c", "score": 0.5, "vec": [True, True]},    # inconsistent
        {"task_id": "x", "impl_sha": "d", "score": 0.5, "vec": [1, 0]},          # bad_vec
        {"task_id": "x", "impl_sha": "e", "score": 0.5, "vec": []},              # bad_vec 空
        {"task_id": "x", "impl_sha": "f", "score": float("nan"),
         "vec": [True, False]},                                                  # inconsistent NaN
        {"task_id": "x", "impl_sha": "g", "score": True, "vec": [True, False]},  # inconsistent bool 分
        {"task_id": "x", "score": 0.5, "vec": [True, False]},                    # no_impl
        {"task_id": "y", "impl_sha": "h", "score": 1.0, "vec": [True]},          # off-cell
        "bad",                                                                   # bad_row（全局才计）
    ]
    pool, c = SI.beh_pool_from_rows(rows, cell="x")
    ok1 = (pool == [("a", (True, False), 0.5)]
           and c == {"n_bad_row": 0, "n_no_impl": 1, "n_no_vec": 1,
                     "n_bad_vec": 2, "n_inconsistent": 3, "n_kept": 1})
    check("P5a", "逐格四桶计数精确（坏行只进全局；一行只进一桶）",
          ok1, f"pool={pool} counts={c}")

    pool2, c2 = SI.beh_pool_from_rows(rows, cell=None)
    ok2 = (c2["n_bad_row"] == 1 and c2["n_kept"] == 2 and c2["n_no_impl"] == 1)
    check("P5b", "全局口径：非 dict 坏行计入 n_bad_row；跨格行入池",
          ok2, f"counts={c2}")

    r_in = [{"task_id": "z", "impl_sha": "h", "score": 0.25005,
             "vec": [True, False, False, False]}]
    r_out = [{"task_id": "z", "impl_sha": "h", "score": 0.2504,
              "vec": [True, False, False, False]}]
    ok3 = (SI.beh_pool_from_rows(r_in)[1]["n_kept"] == 1
           and SI.beh_pool_from_rows(r_out)[1]["n_inconsistent"] == 1)
    check("P5c", "容差边界：差恰 5e-5 收（发射量子半宽）、4e-4 拒",
          ok3, f"in={SI.beh_pool_from_rows(r_in)[1]} out={SI.beh_pool_from_rows(r_out)[1]}")

    try:
        _, c4 = SI.beh_pool_from_rows(rows + [None, 42, {"task_id": "x"}], cell=None)
        ok4 = c4["n_bad_row"] >= 2
    except Exception as e:  # noqa: BLE001
        ok4, c4 = False, {"raised": str(e)}
    check("P5d", "fail-open：任意坏行（None/int/空 dict）只计数排除、绝不抛",
          ok4, f"counts={c4}")


# ════════════════════════════════════════════════════════════════════
# P6 真池回环 + T3
# ════════════════════════════════════════════════════════════════════
def case_p6(tasks: list[dict], all_rows: list[dict], served: list[dict]) -> None:
    print("\n## P6 回环：账本→beh_pool→behavioral ≡ 直接收；T3 两侧")
    scorer = ExternalScorer([sys.executable, str(SCORER_PATH)], source=SCORER_PATH)
    sys.path.insert(0, str(SCORER_PATH.parent))
    import code_exec  # noqa: PLC0415 —— 探针侧直接收向量（probe_v177 同款）

    # 直接收：三轮（oracle/partial/junk）× 每题 (impl, vec, score)
    mism = []
    for t in tasks:
        direct_rows = []
        for amap in served[:3]:
            ans = amap[t["prompt"]]
            code = code_exec.extract_code(ans)
            vec = tuple(bool(code_exec.run_one(code, str(x))[0])
                        for x in t["tests"])
            s = float(scorer.score(
                {"id": t["id"], "prompt": t["prompt"], "tests": t["tests"]}, ans).score)
            if abs(sum(vec) / len(vec) - s) > 1e-9:
                mism.append(f"{t['id']} 直接收对拍不过")
            direct_rows.append((SI.impl_sha_of(ans), vec, s))
        # 账本路
        pool_l, _ = SI.beh_pool_from_rows(all_rows, cell=t["id"])
        b_led = SI.behavioral(pool_l)
        b_dir = SI.behavioral(direct_rows)
        for k in ("kind", "n_impls", "n_behaviors", "n_scores", "claim"):
            if b_led.get(k) != b_dir.get(k):
                mism.append(f"{t['id']} {k}: 账本{b_led.get(k)}≠直接{b_dir.get(k)}")
        if b_led.get("reason") == SI.REASON_BEHAVIOR_SCORE_CONFLICT:
            mism.append(f"{t['id']} 自洽池弹了冲突闸（T3 破）")
    check("P6a", "账本路 ≡ 直接收（kind/n/claim 逐字段）且自洽池冲突闸零触发（T3）",
          not mism, "; ".join(mism[:4]) or f"{len(tasks)} 题全一致")

    # T3 负对照：跨源池（同 vec 异分）⇒ 冲突闸照弹（闸没被删）
    cross = [("a", (True, False), 0.5), ("b", (True, False), 0.7)]
    bc = SI.behavioral(cross)
    check("P6b", "T3 负对照：跨源池（同行为异分）⇒ BEHAVIOR_SCORE_CONFLICT 照弹",
          bc.get("reason") == SI.REASON_BEHAVIOR_SCORE_CONFLICT,
          f"reason={bc.get('reason')}")

    # 双读纯度（P7 的一半）
    p1, c1 = SI.beh_pool_from_rows(all_rows, cell=None)
    p2, c2 = SI.beh_pool_from_rows(all_rows, cell=None)
    check("P7b", "beh_pool_from_rows 双读逐字相同（纯函数）",
          p1 == p2 and c1 == c2)


# ════════════════════════════════════════════════════════════════════
def main() -> int:
    t0 = time.time()
    prereg = EVID / "PREREG_v178.md"
    print("V178 探针（行为向量落账）——判据 PREREG_v178 §2")
    print(f"PREREG mtime: {prereg.stat().st_mtime_ns}")
    case_p1()
    case_p2()
    case_p3()
    case_p5()

    tasks = [json.loads(ln) for ln in SUITE_PATH.read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    # 罐头 HTTP（本地回环）
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CannedHandler)
    port = server.server_address[1]
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    try:
        with tempfile.TemporaryDirectory(prefix="v1_v178_") as td:
            all_rows, _runs, served = case_p4(tasks, port, Path(td))
            case_p6(tasks, all_rows, served)
    finally:
        server.shutdown()

    ok_all = all(r["ok"] for r in RESULTS)
    n_ok = sum(1 for r in RESULTS if r["ok"])
    readings = {
        "prereg_mtime_ns": prereg.stat().st_mtime_ns,
        "timeline": {"main_start": t0, "main_end": time.time()},
        "results": RESULTS,
        "summary": {"n": len(RESULTS), "ok": n_ok, "pass": ok_all},
    }
    EVID.mkdir(parents=True, exist_ok=True)
    (EVID / "readings_v178.json").write_text(
        json.dumps(readings, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    print(f"\n{'✅' if ok_all else '❌'} 判据 {n_ok}/{len(RESULTS)}"
          f" — 读数已落 {EVID / 'readings_v178.json'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
