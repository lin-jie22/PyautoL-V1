#!/usr/bin/env python3
"""V76 读数 —— 打回后的重写**真的只给目标 + 报错**（`REJECT_CARRY=4`）。

口径与 `PREREG.md` §3 逐条对应，⛔ 不在这里另发明字段：

  P1a  `rework_same`         8 跑绝对数（基线 16）      ⇒ 押「降」
  P1b  `rework_redelivered`  8 跑绝对数（基线 16）      ⇒ 只登记；**降到 0 ⇒ P1a 无分母 ⇒ UNKNOWN**
  P2   `task_score_t04`      题级（基线 0.3333）        ⇒ 押「升」
  P3   其余五题**逐题** ≥ 基线  题级（veto）             ⇒ 押「平」
  P4   `rework_diff`（基线 0）                          ⇒ 只登记（硬不变式 same+diff==redelivered）

通电自检（PREREG §3，缺一即 UNKNOWN）：
  1) `↻ 打回重写：只给目标+报错` 在 8/8 份日志里出现
  2) `rework_carried` = 8
  3) 该跑日志里 `[N] 候选=` 的 N 至少出现过 ≥3

⛔ 基线是**上一轮**（V75，`v6_s164`，8 跑，同一脚本只差这一个 env 取值）。
⛔ `reject_carry` 在 `Limits` 上 ⇒ 它**进** `Caliber.signature()`；但 `memory_seed`
   在 `--memory-dir` 下每跑不同 ⇒ 「几跑逐格相同」不是零假设的替代品，只作为记账。
"""
import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

BASE = Path("bench/evidence/v6_s164")
ARM = Path("/tmp/v76rc4")
T04 = "t04_merge_ranges"

#: 基线逐题分（V67 七跑 / V73 八跑逐格相同）
BASE_PER_TASK = {
    "t01_parse_duration": 0.8333,
    "t02_dedupe": 1.0,
    "t03_commas": 1.0,
    "t04_merge_ranges": 0.3333,
    "t05_top_k": 1.0,
    "t06_rle": 1.0,
}
BASE_REWORK = {"carried": 8, "redelivered": 16, "same": 16, "diff": 0,
               "dup_skipped": 16, "passed_after": 0}


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
    return {
        "scores": scores,
        "mean": (sum(vals) / len(vals)) if vals else None,
        "carried": int(rw.get("carried") or 0),
        "redelivered": int(rw.get("redelivered") or 0),
        "same": int(rw.get("same") or 0),
        "diff": int(rw.get("diff") or 0),
        "dup_skipped": int(rw.get("dup_skipped") or 0),
        "passed_after": int(rw.get("passed_after") or 0),
        "closeout": int(co.get("triggered") or 0),
    }


def scan_logs():
    out = []
    for f in sorted(glob.glob(str(ARM / "r*/log/core.log")),
                    key=lambda p: int(re.search(r"/r(\d+)/", p).group(1))):
        txt = Path(f).read_text(encoding="utf-8", errors="replace")
        idx = [int(m) for m in re.findall(r"\[(\d+)\] 通道=", txt)]
        out.append({
            "log": f,
            "hits": txt.count("↻ 打回重写：只给目标+报错"),
            "max_n": max(idx) if idx else 0,
        })
    return out


def main():
    paths = glob.glob(str(ARM / "r*/bench/runs.jsonl"))
    if not paths:
        print("⛔ 臂还没有产物（或路径不对）")
        return 1
    paths.sort(key=lambda p: int(re.search(r"/r(\d+)/", p).group(1)))
    arm = [per_run(load(p)[0]) for p in paths]

    base_runs = []
    for f in sorted(glob.glob(str(BASE / "runs_v75_*.jsonl"))):
        base_runs.extend(load(f))
    base = [per_run(r) for r in base_runs]

    print("=" * 88)
    print("V76 —— 重写真的只给目标+报错（REJECT_CARRY=4）")
    print("=" * 88)

    def table(rows, label):
        print(f"\n[{label}]  跑={len(rows)}")
        print(f"{'run':>4} " + " ".join(f"{k.split('_')[0]:>7}" for k in BASE_PER_TASK)
              + f" {'mean':>7} {'same':>5} {'diff':>5} {'redel':>6} {'close':>6}")
        for i, x in enumerate(rows, 1):
            cells = " ".join(f"{x['scores'].get(k, float('nan')):7.4f}" for k in BASE_PER_TASK)
            print(f"{i:>4} {cells} {x['mean']:7.4f} {x['same']:5d} {x['diff']:5d} "
                  f"{x['redelivered']:6d} {x['closeout']:6d}")

    table(base, "A 基线 = V75（REJECT_CARRY=3，不带 state/history）")
    table(arm, "B 处理 = V76（REJECT_CARRY=4，真的只给目标+报错）")

    total = Counter()
    for x in arm:
        for k in ("carried", "redelivered", "same", "diff", "dup_skipped", "passed_after"):
            total[k] += x[k]
    print(f"\n处理合计 = {dict(total)}")
    print(f"基线合计 = {BASE_REWORK}")

    # ---- P1a / P1b ----
    print("\n" + "-" * 88)
    same_n = total["same"]
    redel_n = total["redelivered"]
    print(f"P1a  rework_same  基线 {BASE_REWORK['same']}  处理 = {same_n}"
          f"  （逐跑 {[x['same'] for x in arm]}）")
    if redel_n == 0:
        p1a = "无分母"
    elif same_n < BASE_REWORK["same"]:
        p1a = "降"
    elif same_n == BASE_REWORK["same"]:
        p1a = "平"
    else:
        p1a = "升"
    print(f"     ⇒ P1a = {p1a}")

    print(f"P1b  rework_redelivered  基线 {BASE_REWORK['redelivered']}  处理 = {redel_n}"
          f"  （逐跑 {[x['redelivered'] for x in arm]}）")

    # ---- P2：t04 ----
    t04 = [x["scores"].get(T04) for x in arm]
    t04 = [v for v in t04 if v is not None]
    print(f"\nP2   task_score_t04  基线 0.3333  处理逐跑 = {['%.4f' % v for v in t04]}")
    if t04 and all(v > 0.3333 + 1e-9 for v in t04):
        p2 = "升"
    elif t04 and all(abs(v - 0.3333) < 0.001 for v in t04):
        p2 = "平"
    elif t04 and all(v < 0.3333 - 1e-9 for v in t04):
        p2 = "降"
    else:
        p2 = "混合"
    print(f"     ⇒ P2 = {p2}")

    # ---- P3：其余五题 ----
    print("\nP3  其余五题逐题 ≥ 基线（veto）")
    drops = []
    for k, want in BASE_PER_TASK.items():
        if k == T04:
            continue
        got = [x["scores"].get(k) for x in arm]
        got = [v for v in got if v is not None]
        worst = min(got) if got else None
        flag = "" if (worst is not None and worst >= want - 1e-9) else "  ← **掉**"
        if flag:
            drops.append(k)
        print(f"    {k:<20} 基线 {want:.4f}  处理最差 "
              f"{worst if worst is None else '%.4f' % worst}{flag}")
    p3 = "平" if not drops else "降"
    print(f"     ⇒ P3 = {p3}")

    # ---- P4：不变式 ----
    print(f"\nP4  rework_diff 基线 {BASE_REWORK['diff']}  处理 = {total['diff']}"
          f"  ｜不变式 same+diff==redelivered ⇒ "
          f"{'成立' if same_n + total['diff'] == redel_n else '**不成立（记账错）**'}")

    # ---- 通电自检 ----
    logs = scan_logs()
    hits = sum(x["hits"] for x in logs)
    print("\n" + "-" * 88)
    print("通电自检")
    print(f"  1) `↻ 打回重写：只给目标+报错（REJECT_CARRY=4` 命中 {hits} 次 / {len(logs)} 份日志 "
          f"（逐份 {[x['hits'] for x in logs]}）")
    print(f"  2) rework_carried = {total['carried']}（基线 {BASE_REWORK['carried']}）")
    print(f"  3) 每份日志 max(N) = {[x['max_n'] for x in logs]}（要求至少一次 ≥3）")

    ok1 = len(logs) > 0 and hits >= len(logs)
    ok2 = total["carried"] == BASE_REWORK["carried"]
    ok3 = any(x["max_n"] >= 3 for x in logs)
    print(f"  ⇒ 自检 {'全过' if (ok1 and ok2 and ok3) else '**未全过**'} "
          f"(1={ok1} 2={ok2} 3={ok3})")

    print("\n" + "=" * 88)
    if not (ok1 and ok2 and ok3) or p1a == "无分母":
        print("判决 = UNKNOWN（通电自检未过 或 分母消失）⇒ P1a/P2/P3 不成立也不否证")
    else:
        print(f"判决 = P1a {p1a} ｜ P2 {p2} ｜ P3 {p3}")
        if p1a == "降" and p2 == "平":
            print("     ⇒ 重写换路了但没变对 ⇒ 下一个问题是**质量**，不是多样性")
        elif p1a == "平":
            print("     ⇒ 「输入构造」在重写通道上彻底关闭（state 和 history 都不是钉子）")
        elif p1a == "降" and p2 == "升":
            print("     ⇒ 摘掉旧代码**同时**让重写换路并过题 ⇒ 这条边第一次通了")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    sys.exit(main())
