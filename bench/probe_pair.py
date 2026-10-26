#!/usr/bin/env python3
"""两臂单变量对的判定量具（零模型、零网络）—— 「这个比较成不成立」先于「差多少」。

## 为什么先写它，而不是先看分数

§55 审计出的事实是：147 行历史记录里 `SUBJECT_ONLY`（只差被测对象）**0 对**。
第九十轮第一次真的造这一对，于是立刻面对一个更基础的问题：

    **怎么证明我造的这对是干净的？**

肉眼核对 `caliber` 的 12 个字段不算证明 —— §17-7q / §45.9 / §55 三次混口径
全都是"标签说同口径、实际差了别的"。所以把判据写成代码，并且**能拒答**：

    R1 除 `llm_cond` 外还有任何口径字段不同  ⇒ 拒
    R2 `llm_cond` 的差异不是「只换被测对象」 ⇒ 拒（复用 `probe_caliber_split.classify`）
    R3 有样本没量到（`attempted != recorded` 或 `skipped_no_answer > 0`）⇒ 拒
    R4 两臂样本数不同（`repeat` 不同已在 R1 里，这里是逐题复查）⇒ 拒

⛔ **拒答时不打印差值**。理由很实际：只要差值一旦印出来，它就会被引用，
而"这条读数是在什么前提下得出的"总在最需要它的时候被忘掉（本项目第 85 轮
把「两池恰好相同」写成结论，就是没先问"这两池能不能比"）。

## 判据：净增益必须超过臂内噪声

同一 `sig`（口径逐位相同）重复跑，分数仍会摆动 —— 这不是缺陷，是读数的一部分。
第九十轮实测到一对**同口径**历史记录：`5.7141` 与 `5.4284`，差 **4 格**（140 格制），
且差异只落在 `sg02`（−3）与 `sg05`（−1）两题上。

⇒ 判定规则（唯一一条）：

    |Δ| > 两臂极差的较大者  ⇒ 可分辨
    否则                    ⇒ 噪声内（UNKNOWN）

⛔ 不许"差一点点也算趋势"，也不许把两臂极差取最小值（那会偏向出结论）。

## 用法

    python3 bench/probe_pair.py --a armA/runs.jsonl --b armB/runs.jsonl \\
        --suite bench/specgen/tasks_v2.jsonl
    python3 bench/probe_pair.py --self-check     # 七条反证，零模型零网络
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))                 # probe_caliber_split
sys.path.insert(0, str(_HERE.parent))          # mw.*

from probe_caliber_split import classify        # noqa: E402
from mw.bench import CALIBER_KEYS, Caliber      # noqa: E402

#: 唯一允许不同的字段。写死成字符串而不是读参数：放宽它的入口就是混口径的入口。
DECLARED = ("llm_cond",)


class Refuse(Exception):
    """这一对不能比。**带原因**：只说"不可比"等于什么都没说。"""

    def __init__(self, code: str, why: str) -> None:
        self.code = code
        self.why = why
        super().__init__(f"{code}: {why}")


# ---------------------------------------------------------------------------
# 读入与清洗
# ---------------------------------------------------------------------------
def load_run(path: Path) -> dict:
    """取该文件**最后一行**（一次调用一条；重跑过就取最新）。

    ⛔ 不合并多行、不取平均：`runs.jsonl` 里两行是两个不同的 `run_id`，
       合成一条会把两次运行的差异抹掉 —— 而那正是要量的东西。
    """
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        raise Refuse("R0", f"{path} 是空的（空文件 ≠ 没差，先查是不是没跑起来）")
    r = json.loads(lines[-1])
    if not isinstance(r.get("caliber"), dict):
        raise Refuse("R0", f"{path} 最后一行没有 caliber")
    return r


def per_rep_totals(rec: dict, n_tests: dict[str, int]) -> list[tuple[float, int, list[tuple[str, int]]]]:
    """按 repeat 归并：返回 [(均值, 总格数, [(题号, 格数)])]。

    格数 = `round(score × len(tests))`。**断言数从题集读**，不写死 14 ——
    写死就等于"换题集后静默按旧尺子算格"。
    """
    by_rep: dict[int, list[dict]] = {}
    for t in rec["report"].get("per_task") or []:
        by_rep.setdefault(int(t.get("rep") or 1), []).append(t)
    out = []
    for rep in sorted(by_rep):
        cells: list[tuple[str, int]] = []
        tot = 0.0
        for t in by_rep[rep]:
            n = n_tests.get(t["id"])
            if n is None:
                raise Refuse("R0", f"题集里没有 {t['id']} ⇒ 无法把分数换算成格数")
            c = int(round(float(t["score"]) * n))
            cells.append((t["id"], c))
            tot += c
        out.append((tot / sum(n_tests.values()), sum(c for _, c in cells), cells))
    return out


def _cal(rec: dict) -> Caliber:
    return Caliber(**{k: (rec["caliber"].get(k)) for k in CALIBER_KEYS})


def assert_comparable(a: dict, b: dict, n_tests: dict[str, int] | None = None) -> dict:
    """四条拒答规则。**全部通过**才返回读数用的上下文。"""
    ca, cb = _cal(a), _cal(b)
    unexpected, declared = ca.diff_except(cb, DECLARED)
    if unexpected:
        raise Refuse("R1", f"除 {DECLARED} 外还有口径字段不同：{unexpected}"
                           f"（声明放行的只有 {declared}）")
    if not declared:
        raise Refuse("R2", "两臂口径逐位相同 ⇒ 这不是'换被测对象'，是同口径重复"
                           "（那种对只能用来量噪声）")
    lc_a, lc_b = a["caliber"]["llm_cond"], b["caliber"]["llm_cond"]
    cls = classify(lc_a, lc_b)
    if cls != "SUBJECT_ONLY":
        diff = [k for k in sorted(set(lc_a) | set(lc_b)) if lc_a.get(k) != lc_b.get(k)]
        raise Refuse("R2", f"llm_cond 的差异被判成 {cls}，差异键 = {diff}"
                           f"（只允许 model / thinking 变）")
    for tag, rec in (("A", a), ("B", b)):
        pt = rec["report"].get("per_task") or []
        got = len(pt)
        #: ⚠️ 分母必须是**题集里的题数**，不是"per_task 里出现过的题数" ——
        #:    后者在"某题整轮没产出"时会跟着变小，于是缺题反而让 R3 通过。
        n_task = len(n_tests) if n_tests else len({t["id"] for t in pt})
        want = int(rec["caliber"].get("repeat") or 1) * n_task
        if got != want:
            raise Refuse("R3", f"臂{tag} 的 per_task 行数 {got} ≠ repeat×题数 {want}"
                               f" ⇒ 有整题没产出")
        if int(rec["report"].get("recorded") or 0) != int(rec["report"].get("attempted") or 0):
            raise Refuse("R3", f"臂{tag} attempted={rec['report'].get('attempted')} "
                               f"recorded={rec['report'].get('recorded')} ⇒ 有样本没量到")
        if int(rec["report"].get("skipped_no_answer") or 0) > 0:
            raise Refuse("R3", f"臂{tag} skipped_no_answer={rec['report'].get('skipped_no_answer')}"
                               f" ⇒ 模型交了空，分数会把'没交'读成'不会'")
    if int(a["caliber"].get("repeat") or 1) != int(b["caliber"].get("repeat") or 1):
        raise Refuse("R4", "两臂 repeat 不同")
    return {"cls": cls, "cond_a": lc_a, "cond_b": lc_b,
            "diff_keys": [k for k in sorted(set(lc_a) | set(lc_b))
                          if lc_a.get(k) != lc_b.get(k)]}


def judge(mean_a: float, mean_b: float, range_a: float, range_b: float) -> tuple[str, float, float]:
    """唯一判定：净增益是否超过两臂极差的**较大者**。返回 (结论, Δ, 噪声)。"""
    d = mean_b - mean_a
    noise = max(range_a, range_b)
    return ("可分辨" if abs(d) > noise else "噪声内（UNKNOWN）"), d, noise


def report_pair(a: dict, b: dict, n_tests: dict[str, int]) -> int:
    ctx = assert_comparable(a, b, n_tests)
    ra, rb = per_rep_totals(a, n_tests), per_rep_totals(b, n_tests)
    ma = sum(x[0] for x in ra) / len(ra)
    mb = sum(x[0] for x in rb) / len(rb)
    range_a = max(x[0] for x in ra) - min(x[0] for x in ra)
    range_b = max(x[0] for x in rb) - min(x[0] for x in rb)
    total_cells = sum(n_tests.values())

    print("=" * 76)
    print("  两臂单变量对  —— 判定先于读数")
    print("=" * 76)
    print(f"  口径差异：{ctx['cls']}  只差 {ctx['diff_keys']}"
          f"  （声明放行 {list(DECLARED)}）")
    for tag, c in (("A", ctx["cond_a"]), ("B", ctx["cond_b"])):
        print(f"    臂{tag}  " + "  ".join(f"{k}={c.get(k)!r}" for k in sorted(c)))
    print(f"  题集：{total_cells} 格 / {len(n_tests)} 题   repeat="
          f"{int(a['caliber']['repeat'])}   solver={a.get('solver')}")

    for tag, rec, reps in (("A", a, ra), ("B", b, rb)):
        cells = "  ".join(f"r{i+1}={x[1]}" for i, x in enumerate(reps))
        print(f"\n  ── 臂{tag} ── {cells}   均值={sum(x[0] for x in reps)/len(reps):.4f}"
              f"  极差={max(x[0] for x in reps)-min(x[0] for x in reps):.4f}")

    verdict, d, noise = judge(ma, mb, range_a, range_b)
    print(f"\n  Δ = B − A = {mb - ma:+.4f}（{(mb - ma) * total_cells:+.1f} 格）")
    print(f"  臂内噪声（两臂极差取大） = {noise:.4f}（{noise * total_cells:.1f} 格）")
    print(f"  判定：**{verdict}**")

    # 逐题：只在两臂都有样本的题上比；用逐题中位数，避开单次抖动
    print(f"\n  ── 逐题（格数，取各 rep 中位数）──")
    print(f"  {'题':8s}{'A':>6s}{'B':>6s}{'Δ':>7s}")
    for tid in sorted(n_tests):
        def med(reps):
            v = sorted(dict(x[2]).get(tid, 0) for x in reps)
            return v[len(v) // 2]
        ga, gb = med(ra), med(rb)
        flag = "  ←" if ga != gb else ""
        print(f"  {tid:8s}{ga:>6d}{gb:>6d}{gb - ga:>+7d}{flag}")
    print()
    print("  ⛔ 逐题差值**不可相加**：§54 实测过单项收益之和 ≠ 合计（有耦合）。")
    print("  ⛔ 本条只回答「同一把尺子、只换被测对象，差多少」；"
          "不回答「谁更强」这种跨题集/跨端点的普适问题。")
    return 0 if verdict == "可分辨" else 1


# ---------------------------------------------------------------------------
# 自检：七条反证
# ---------------------------------------------------------------------------
def _fake(model: str, per_rep: list[list[int]], *, side: str = "q",
          repeat: int | None = None, **cal_over) -> dict:
    """造一条最小可判的运行记录。`per_rep[r]` 是第 r+1 次 repeat 的逐题格数。"""
    rep = repeat or len(per_rep)
    cal = {
        "scorer_sha": "S", "suite_sha": "T", "repeat": rep, "budget_tag": "1x",
        "temperature": 0.0, "fresh": True, "memory_scope": "per_task_fresh",
        "memory_seed": [0, 0],
        "limits": {"llm_max_tokens": 2048, "max_llm_calls": 24, "max_wall_s": 120.0},
        "llm_cond": {"model": model, "endpoint": "127.0.0.1:8790",
                     "timeout_s": 120.0, "offline": False},
        "max_tokens": 2048, "tool_catalog_sha": "C",
    }
    cal.update(cal_over)
    per_task = []
    for i, cells in enumerate(per_rep, 1):
        for tid, c in zip(("sg01", "sg02"), cells):
            per_task.append({"rep": i, "id": tid, "score": c / 14, "elapsed_s": 1.0})
    n = len(per_rep) * 2
    return {"caliber": cal, "solver": f"内核/{side}", "report": {
        "per_task": per_task, "attempted": n, "recorded": n,
        "skipped_no_answer": 0, "failed_scorer": 0}}


_NT = {"sg01": 14, "sg02": 14}


def _self_check() -> int:
    print("── 两臂单变量对 · 判定量具自检（七条反证）──")
    bad = 0

    def chk(name: str, ok: bool, note: str = "") -> None:
        nonlocal bad
        print(f"  {'✅' if ok else '❌'} {name}" + (f"  [{note}]" if note else ""))
        if not ok:
            bad += 1

    def refuses(fn, code: str) -> tuple[bool, str]:
        try:
            fn()
        except Refuse as e:
            return e.code == code, e.why
        return False, "没有拒答"

    a = _fake("qwen2.5:7b", [[10, 8], [10, 7], [9, 8]])
    b = _fake("Qwen3.5-4B:latest", [[6, 5], [7, 5], [6, 4]], side="q4")

    #: C1 是**唯一不该拒**的一条。⛔ 它不能走 `refuses()` ——
    #:   那个助手在"没抛"时返回 False，于是"该放行且真的放行了"会被记成失败。
    #:   （这一版就这么错过一次：反证组全绿、正例被自己的助手判红。）
    try:
        ctx = assert_comparable(a, b, _NT)
        c1_ok, c1_note = ctx["diff_keys"] == ["model"], f"diff_keys={ctx['diff_keys']}"
    except Refuse as e:
        c1_ok, c1_note = False, f"被拒了：{e.code} {e.why}"
    chk("C1 只换 model（其余口径逐位相同）⇒ 放行且被判 SUBJECT_ONLY", c1_ok, c1_note)

    # C2 反证：多一个字段不同就必须拒，且**点名**是哪个字段
    b2 = _fake("Qwen3.5-4B:latest", [[6, 5], [7, 5], [6, 4]],
               limits={"llm_max_tokens": 4096, "max_llm_calls": 24, "max_wall_s": 120.0})
    ok, why = refuses(lambda: assert_comparable(a, b2, _NT), "R1")
    chk("C2 反证：limits 也变了 ⇒ R1 拒，并点名 'limits'",
        ok and "limits" in why, why)

    # C3 反证：只换通道不是"换被测对象"
    b3 = _fake("qwen2.5:7b", [[10, 8], [10, 7], [9, 8]],
               llm_cond={"model": "qwen2.5:7b", "endpoint": "localhost:11434",
                         "timeout_s": 120.0, "offline": False})
    ok, why = refuses(lambda: assert_comparable(a, b3, _NT), "R2")
    chk("C3 反证：只换 endpoint ⇒ R2 拒（通道差异不算换被测对象）", ok, why)

    # C4 反证：同口径重复不是"换被测对象"
    a2 = _fake("qwen2.5:7b", [[10, 8], [10, 7], [9, 8]])
    ok, why = refuses(lambda: assert_comparable(a, a2, _NT), "R2")
    chk("C4 反证：两臂口径逐位相同 ⇒ R2 拒（那种对只能量噪声）", ok, why)

    # C5 反证：有样本没量到 ⇒ 拒（否则"没交"被读成"不会"）
    b5 = _fake("Qwen3.5-4B:latest", [[6, 5], [7, 5], [6, 4]])
    b5["report"]["skipped_no_answer"] = 1
    b5["report"]["recorded"] = 5
    ok, why = refuses(lambda: assert_comparable(a, b5, _NT), "R3")
    chk("C5 反证：skipped_no_answer>0 ⇒ R3 拒", ok, why)

    # C6/C7 判定阈值**两侧**都要试：只试一侧，一个恒返回"可分辨"的函数也能过
    v_in, d_in, n_in = judge(0.80, 0.826, 0.03, 0.02)      # Δ=0.026 < 噪声 0.03
    chk("C6 判定：Δ 落在噪声内 ⇒ UNKNOWN（不是'趋势'）",
        v_in.startswith("噪声内"), f"Δ={d_in:.3f} 噪声={n_in:.3f}")
    v_out, d_out, n_out = judge(0.80, 0.90, 0.03, 0.02)     # Δ=0.10 > 噪声 0.03
    chk("C7 判定：Δ 超过噪声 ⇒ 可分辨",
        v_out == "可分辨", f"Δ={d_out:.3f} 噪声={n_out:.3f}")

    print(f"\n结论：{'七条全部成立' if bad == 0 else f'{bad} 条不成立'}")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="两臂单变量对的判定（零模型零网络）")
    ap.add_argument("--a", default="", help="臂 A 的 runs.jsonl")
    ap.add_argument("--b", default="", help="臂 B 的 runs.jsonl")
    ap.add_argument("--suite", default="", help="题集 jsonl（用来把分数换算成格数）")
    ap.add_argument("--self-check", action="store_true", help="七条反证")
    x = ap.parse_args()

    if x.self_check:
        return _self_check()
    if not (x.a and x.b and x.suite):
        ap.error("要么 --self-check，要么 --a + --b + --suite")
    suite = [json.loads(l) for l in Path(x.suite).read_text(encoding="utf-8").splitlines() if l.strip()]
    n_tests = {t["id"]: len(t["tests"]) for t in suite}
    try:
        return report_pair(load_run(Path(x.a)), load_run(Path(x.b)), n_tests)
    except Refuse as e:
        print(f"❌ 拒答  {e.code}: {e.why}", file=sys.stderr)
        print("⛔ 拒答时不打印差值：一句没有前提的差值一定会被单独引用。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
