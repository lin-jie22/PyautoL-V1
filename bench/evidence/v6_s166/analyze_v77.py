#!/usr/bin/env python3
"""V77 读数 —— 打回后的重写改走**交付协议**（`REJECT_CARRY=5`）。

口径与 `PREREG.md` 逐条对应，⛔ 不在这里另发明字段：

  C1  `rework_same`         押「降」：`same_B < same_A` 且比例也降
  C2  `passed_after`        押「升」：`passed_after_B > passed_after_A` 且 ≥1
  C3  `task_score_t04`      押「升」：`mean_B > mean_A` 且 ≥0.6667
  C4  其余五题**逐题**平     前提：两臂逐题 mean 相等（差 > 0.1290 ⇒ 混入别的变量，作废）

自检（PREREG §5）：
  S1 两臂 `rework_gap == 0`
  S2 `via_answer_B > 0` 且 `via_answer_A == 0`
  S3 `carried_A > 0` 且 `carried_B > 0`
  S4 B 臂 `same == dup_skipped`（若 same>0）
  S5 每跑退出码 0 且状态不含 closed/error

⛔ 两臂**同轮**产出（/tmp/v77a、/tmp/v77b），不跨代引旧读数。
⛔ `memory_seed` 在 `--memory-dir` 下每跑不同 ⇒ 「逐格相同」只当加强项。
"""
import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

ARM_A = Path("/tmp/v77a")
ARM_B = Path("/tmp/v77b")
EV = Path("bench/evidence/v6_s166")
T04 = "t04_merge_ranges"
NOISE = 0.1290

TASKS = ["t01_parse_duration", "t02_dedupe", "t03_commas",
         "t04_merge_ranges", "t05_top_k", "t06_rle"]
#: 本轮的**先验**（V67/V73/V74 六~八跑均一致；只用于"抬升/塌陷"的旁证）
PRIOR = {"t01_parse_duration": 0.8333, "t02_dedupe": 1.0, "t03_commas": 1.0,
         "t04_merge_ranges": 0.3333, "t05_top_k": 1.0, "t06_rle": 1.0}


def load(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def per_run(rec):
    rep = rec.get("report") or {}
    scores = {x["id"]: float(x.get("score") or 0.0)
              for x in (rep.get("per_task") or []) if isinstance(x, dict)}
    vals = list(scores.values())
    rw = rec.get("kernel_rework") or {}
    co = rec.get("kernel_closeout") or {}
    vf = rec.get("kernel_verify") or {}
    return {
        "scores": scores,
        "mean": (sum(vals) / len(vals)) if vals else None,
        "carried": int(rw.get("carried") or 0),
        "redelivered": int(rw.get("redelivered") or 0),
        "same": int(rw.get("same") or 0),
        "diff": int(rw.get("diff") or 0),
        "dup_skipped": int(rw.get("dup_skipped") or 0),
        "passed_after": int(rw.get("passed_after") or 0),
        "via_answer": int(rw.get("via_answer") or 0),
        "rework_gap": int(rw.get("rework_gap") or 0),
        "switch": int(rw.get("switch") or 0),
        "closeout": int(co.get("triggered") or 0),
        "verify_runs": int(vf.get("runs") or 0),
        "verify_failed": int(vf.get("failed") or 0),
        #: ⚠️ `runs.jsonl` **没有** `status` 键（第一版按它写，全是 None）。
        #:    跑批层的"这跑跑完了没有"由 `report` 的三个字段回答：
        #:    `attempted`（题数）/ `errors`（跑批异常）/ `skipped_no_answer`。
        "attempted": int(rep.get("attempted") or 0),
        "errors": list(rep.get("errors") or []),
        "failed_scorer": int(rep.get("failed_scorer") or 0),
        "skipped_no_answer": int(rep.get("skipped_no_answer") or 0),
        "elapsed_s": float(rep.get("elapsed_s") or 0.0),
        "why": {x["id"]: str(x.get("why") or "")
                for x in (rep.get("per_task") or []) if isinstance(x, dict)},
    }


def arm_runs(arm: Path, tag: str):
    paths = glob.glob(str(arm / "r*/bench/runs.jsonl"))
    paths.sort(key=lambda p: int(re.search(r"/r(\d+)/", p).group(1)))
    if not paths:                      # /tmp 被清理时退到证据目录的归档
        paths = sorted(glob.glob(str(EV / f"runs_v77_{tag}_r*.jsonl")),
                       key=lambda p: int(re.search(r"_r(\d+)\.jsonl$", p).group(1)))
    return [per_run(load(p)[0]) for p in paths if load(p)]


def scan_logs(tag: str, mark: str):
    files = sorted(glob.glob(str((ARM_A if tag == "A" else ARM_B) / "r*/log/core.log")))
    if not files:
        files = sorted(glob.glob(str(EV / f"core_v77_{tag}_r*.log")))
    out = []
    for f in files:
        txt = Path(f).read_text(encoding="utf-8", errors="replace")
        idx = [int(m) for m in re.findall(r"\[(\d+)\] 通道=", txt)]
        out.append({"log": f, "hits": txt.count(mark),
                    "max_n": max(idx) if idx else 0,
                    "n_cand": len(idx)})
    return out


def table(rows, label):
    print(f"\n[{label}]  跑={len(rows)}")
    print(f"{'run':>4} " + " ".join(f"{k.split('_')[0]:>7}" for k in TASKS)
          + f" {'mean':>7} {'same':>5} {'diff':>5} {'redel':>6} {'pa':>3}"
          f" {'via':>4} {'gap':>4} {'close':>6}")
    for i, x in enumerate(rows, 1):
        cells = " ".join(f"{x['scores'].get(k, float('nan')):7.4f}" for k in TASKS)
        print(f"{i:>4} {cells} {x['mean']:7.4f} {x['same']:5d} {x['diff']:5d} "
              f"{x['redelivered']:6d} {x['passed_after']:3d} {x['via_answer']:4d} "
              f"{x['rework_gap']:4d} {x['closeout']:6d}")


def total(rows, key):
    return sum(x[key] for x in rows)


def main():
    a = arm_runs(ARM_A, "A")
    b = arm_runs(ARM_B, "B")
    if not a or not b:
        print(f"⛔ 产物不全：A={len(a)} B={len(b)}")
        return 1

    print("=" * 96)
    print("V77 —— 打回后的重写改走交付协议（REJECT_CARRY=4 vs 5）")
    print("=" * 96)
    table(a, "A 基线 = REJECT_CARRY=4（V76 原样：replan + 载荷脱敏）")
    table(b, "B 处理 = REJECT_CARRY=5（answer(goal, clean=True) 包成单个 finish）")

    A = Counter({k: total(a, k) for k in
                 ("carried", "redelivered", "same", "diff", "dup_skipped",
                  "passed_after", "via_answer", "rework_gap", "closeout",
                  "verify_runs", "verify_failed")})
    B = Counter({k: total(b, k) for k in
                 ("carried", "redelivered", "same", "diff", "dup_skipped",
                  "passed_after", "via_answer", "rework_gap", "closeout",
                  "verify_runs", "verify_failed")})
    print(f"\nA 合计 = {dict(A)}")
    print(f"B 合计 = {dict(B)}")

    # ---- C1：same ----
    print("\n" + "-" * 96)
    ra = (A["same"] / A["redelivered"]) if A["redelivered"] else None
    rb = (B["same"] / B["redelivered"]) if B["redelivered"] else None
    print(f"C1  rework_same   A={A['same']}/{A['redelivered']}"
          f"{'' if ra is None else ' = %.4f' % ra}"
          f"   B={B['same']}/{B['redelivered']}"
          f"{'' if rb is None else ' = %.4f' % rb}")
    print(f"     A 逐跑 {[x['same'] for x in a]} ｜ B 逐跑 {[x['same'] for x in b]}")
    if rb is None or A["redelivered"] == 0:
        c1 = "无分母"
    elif B["same"] < A["same"] and rb < ra:
        c1 = "降"
    elif B["same"] == A["same"] and abs(rb - ra) < 1e-9:
        c1 = "平"
    else:
        c1 = "混合/升"
    print(f"     ⇒ C1 = {c1}")

    # ---- C2：passed_after ----
    print(f"\nC2  passed_after  A={A['passed_after']}  B={B['passed_after']}"
          f"  （B 逐跑 {[x['passed_after'] for x in b]}）")
    c2 = "升" if (B["passed_after"] > A["passed_after"]
                  and B["passed_after"] >= 1) else (
        "平" if B["passed_after"] == A["passed_after"] else "降")
    print(f"     ⇒ C2 = {c2}")

    # ---- C3：t04 ----
    ta = [x["scores"].get(T04) for x in a if x["scores"].get(T04) is not None]
    tb = [x["scores"].get(T04) for x in b if x["scores"].get(T04) is not None]
    ma = sum(ta) / len(ta) if ta else None
    mb = sum(tb) / len(tb) if tb else None
    print(f"\nC3  task_score_t04  A 逐跑 {['%.4f' % v for v in ta]} mean={ma:.4f}")
    print(f"                    B 逐跑 {['%.4f' % v for v in tb]} mean={mb:.4f}")
    if ma is None or mb is None:
        c3 = "无读数"
    elif mb > ma and mb >= 0.6667:
        c3 = "升"
    elif abs(mb - ma) < NOISE:
        c3 = "平（Δ < 噪声 0.1290）"
    elif mb < ma:
        c3 = "降"
    else:
        c3 = "升但未过 0.6667 门槛"
    print(f"     ⇒ C3 = {c3}")

    # ---- C4：其余五题 ----
    print("\nC4  其余五题逐题平（前提）：两臂 mean 之差")
    dirty = []
    for k in TASKS:
        if k == T04:
            continue
        va = [x["scores"][k] for x in a if k in x["scores"]]
        vb = [x["scores"][k] for x in b if k in x["scores"]]
        m1 = sum(va) / len(va) if va else None
        m2 = sum(vb) / len(vb) if vb else None
        d = None if (m1 is None or m2 is None) else (m2 - m1)
        bad = d is not None and abs(d) > NOISE
        if bad:
            dirty.append((k, d))
        print(f"    {k:<20} A={m1:.4f}  B={m2:.4f}  Δ={d:+.4f}"
              f"{'  ← **超噪声，混入别的变量**' if bad else ''}")
    c4 = "平" if not dirty else "**不成立**"
    print(f"     ⇒ C4 = {c4}")

    # ---- 自检 ----
    print("\n" + "-" * 96)
    print("通电自检")
    la = scan_logs("A", "↻ 打回重写：只给目标+报错（REJECT_CARRY=4")
    lb = scan_logs("B", "↻ 打回重写：走**交付协议**")
    print(f"  S1 rework_gap：A={A['rework_gap']} B={B['rework_gap']} ⇒ "
          f"{'成立' if A['rework_gap'] == 0 and B['rework_gap'] == 0 else '**不成立**'}")
    print(f"  S2 via_answer：A={A['via_answer']} B={B['via_answer']} ⇒ "
          f"{'成立' if A['via_answer'] == 0 and B['via_answer'] > 0 else '**不成立**'}")
    print(f"  S3 carried：A={A['carried']} B={B['carried']} ⇒ "
          f"{'成立' if A['carried'] > 0 and B['carried'] > 0 else '**不成立**'}")
    print(f"  S4 same==dup_skipped：A({A['same']},{A['dup_skipped']}) "
          f"B({B['same']},{B['dup_skipped']})")
    print(f"  S5 跑批完整性：A 题数={[x['attempted'] for x in a]} "
          f"错误={[len(x['errors']) for x in a]} 无答案={[x['skipped_no_answer'] for x in a]}")
    print(f"                    B 题数={[x['attempted'] for x in b]} "
          f"错误={[len(x['errors']) for x in b]} 无答案={[x['skipped_no_answer'] for x in b]}")
    print(f"    耗时：A={[round(x['elapsed_s']) for x in a]} ｜ B={[round(x['elapsed_s']) for x in b]}")
    ok_s5 = (all(x["attempted"] == len(TASKS) and not x["errors"]
                 and x["skipped_no_answer"] == 0 for x in a + b))
    print(f"  日志命中：A {[x['hits'] for x in la]} ｜ B {[x['hits'] for x in lb]}"
          f" ｜ B 每份候选轮数 {[x['n_cand'] for x in lb]}")
    ok = (A["rework_gap"] == 0 and B["rework_gap"] == 0
          and A["via_answer"] == 0 and B["via_answer"] > 0
          and A["carried"] > 0 and B["carried"] > 0
          and all(x["hits"] > 0 for x in lb) and len(lb) == len(b)
          and ok_s5)
    print(f"  ⇒ 自检 {'全过' if ok else '**未全过**'}")

    print("\n" + "=" * 96)
    if not ok:
        print("判决 = UNKNOWN（自检未过 ⇒ 机制没通电，C1~C3 不成立也不否证）")
    elif c4 == "**不成立**":
        print(f"判决 = UNKNOWN（C4 崩：{dirty} ⇒ 改动漏到了『没打回』的路径上）")
    else:
        print(f"判决 = C1 {c1} ｜ C2 {c2} ｜ C3 {c3}")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    sys.exit(main())
