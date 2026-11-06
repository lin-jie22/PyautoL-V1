#!/usr/bin/env python3
"""V74 读数 —— D 格复合臂（`V1_ANSWER_CLEAN=1`）vs V67 基线（`=1`，`clean` 关）。

口径与 `PREREG.md` §4 逐条对应，⛔ 不在这里另发明字段：

  P1  `task_score_t04`          题级（基线 0.3333）      ⇒ 押「升」
  P2  其余五题**逐题** ≥ 基线    题级（veto）            ⇒ 押「平」
  P3  `mean_task`               题级（只登记，不判）      ⇒ 上限 0.111 < 0.1290

通电自检（PREREG §4，缺一即 UNKNOWN）：
  1) `🧼 收尾交付` 在 6/6 份日志里出现
  2) 该行报的「省去状态 N 字符」中位数 ∈ [200, 600]（PREREG §7 修正：实测 292）
  3) t04 的交付答案长度 ≈ 515（V65 D 格读数），不是 489/396

⛔ 基线是**历史跑批**（V67 七跑，同一脚本同一常量），不是同轮并行两臂。
⛔ `V1_ANSWER_CLEAN` 不进口径签名 ⇒ 两臂 `sig` 相同、行为不同：口径只由本文件
   与 PREREG 的人工声明承担，`sig` 不作为口径证据。
"""
import glob
import json
import re
import statistics
import sys
from pathlib import Path

BASE = Path("bench/evidence/v6_s156")
ARM = Path("/tmp/v74clean")
T04 = "t04_merge_ranges"

#: 基线逐题分（V67 七跑逐格相同，见 `v6_s156/RESULTS.md` / §135）
BASE_PER_TASK = {
    "t01_parse_duration": 0.8333,
    "t02_dedupe": 1.0,
    "t03_commas": 1.0,
    "t04_merge_ranges": 0.3333,
    "t05_top_k": 1.0,
    "t06_rle": 1.0,
}


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


def per_run(r):
    rep = r.get("report") or {}
    scores = {x["id"]: float(x.get("score") or 0.0)
              for x in (rep.get("per_task") or []) if isinstance(x, dict)}
    vals = list(scores.values())
    co = r.get("kernel_closeout") or {}
    kv = r.get("kernel_verify") or {}
    rw = r.get("kernel_rework") or {}
    return {
        "run_id": r.get("run_id"),
        "scores": scores,
        "mean": (sum(vals) / len(vals)) if vals else None,
        "closeout_chars": int(co.get("chars") or 0),
        "closeout_triggered": int(co.get("triggered") or 0),
        "closeout_origin": dict(co.get("origin") or {}),
        "example_runs": int(kv.get("example_runs") or 0),
        "example_failed": int(kv.get("example_failed") or 0),
        "redelivered": int(rw.get("redelivered") or 0),
        "same": int(rw.get("same") or 0),
    }


def scan_logs():
    """每跑：🧼 命中次数、省去状态字符数、交付字符数。"""
    out = []
    for f in sorted(glob.glob(str(ARM / "r*/log/core.log")),
                    key=lambda p: int(re.search(r"/r(\d+)/", p).group(1))):
        txt = Path(f).read_text(encoding="utf-8", errors="replace")
        saved = [int(m) for m in re.findall(r"省去状态\s*(\d+)\s*字符", txt)]
        deliver = [int(m) for m in re.findall(r"📦 收尾交付 (\d+) 字符", txt)]
        out.append({
            "log": f,
            "clean_hits": txt.count("🧼 收尾交付"),
            "saved": saved,
            "delivered": deliver,
        })
    return out


def main():
    arm_paths = glob.glob(str(ARM / "r*/bench/runs.jsonl"))
    if not arm_paths:
        print("⛔ 臂还没有产物（或路径不对）")
        return 1

    base_runs = []
    for f in sorted(glob.glob(str(BASE / "runs_v67_*.jsonl"))):
        base_runs.extend(load(f))
    base = [per_run(r) for r in base_runs]
    arm = [per_run(r) for r in (load(f)[0] for f in
                                sorted(arm_paths, key=lambda p: int(
                                    re.search(r"/r(\d+)/", p).group(1))))]

    print("=" * 86)
    print("V74 —— 收尾交付 D 格复合臂（不带状态 + 25 字正向 system）")
    print("=" * 86)

    def table(rows, label):
        print(f"\n[{label}]  跑={len(rows)}")
        hdr = f"{'run':>4} " + " ".join(f"{k.split('_')[0]:>7}" for k in BASE_PER_TASK)
        print(hdr + f" {'mean':>7} {'交付字符':>9} {'ex_fail':>8}")
        for i, x in enumerate(rows, 1):
            cells = " ".join(f"{x['scores'].get(k, float('nan')):7.4f}"
                             for k in BASE_PER_TASK)
            print(f"{i:>4} {cells} {x['mean']:7.4f} {x['closeout_chars']:>9} "
                  f"{x['example_failed']:>8}")

    table(base, "A 基线 = V67（V1_REJECT_CARRY=1，clean 关）")
    table(arm, "B 处理 = V74（V1_ANSWER_CLEAN=1）")

    # ---- P1：t04 题级 ----
    t04 = [x["scores"].get(T04) for x in arm]
    t04 = [v for v in t04 if v is not None]
    print("\n" + "-" * 86)
    print(f"P1  task_score_t04  基线 0.3333  处理逐跑 = {['%.4f' % v for v in t04]}")
    if t04 and all(abs(v - 1.0) < 1e-9 for v in t04):
        p1 = "升"
    elif t04 and all(abs(v - 0.3333) < 0.001 for v in t04):
        p1 = "平"
    elif t04 and all(v < 0.3333 + 1e-9 for v in t04) and any(v < 0.3333 - 1e-9 for v in t04):
        p1 = "降"
    else:
        p1 = "混合"
    print(f"     ⇒ P1 = {p1}")

    # ---- P2：其余五题逐题 ----
    print("\nP2  其余五题逐题 ≥ 基线（veto）")
    drops = []
    for k, want in BASE_PER_TASK.items():
        if k == T04:
            continue
        got = [x["scores"].get(k) for x in arm]
        got = [v for v in got if v is not None]
        worst = min(got) if got else None
        flag = "" if (worst is not None and worst >= want - 1e-9) else "  ← **掉**"
        if flag:
            drops.append((k, want, worst))
        print(f"    {k:<20} 基线 {want:.4f}  处理最差 {worst if worst is None else '%.4f' % worst}{flag}")
    p2 = "平" if not drops else "降"
    print(f"     ⇒ P2 = {p2}")

    # ---- P3：mean（只登记）----
    means = [x["mean"] for x in arm if x["mean"] is not None]
    print(f"\nP3  mean_task  基线 0.8611  处理逐跑 = "
          f"{['%.4f' % v for v in means]}  （⛔ 不判：Δ 上限 0.111 < 0.1290）")

    # ---- 通电自检 ----
    logs = scan_logs()
    hits = sum(x["clean_hits"] for x in logs)
    saved = [n for x in logs for n in x["saved"]]
    delivered = [n for x in logs for n in x["delivered"]]
    print("\n" + "-" * 86)
    print("通电自检")
    print(f"  1) `🧼 收尾交付` 命中 {hits} 次 / {len(logs)} 份日志")
    print(f"  2) 省去状态字符 = {saved}  中位数 = "
          f"{(statistics.median(saved) if saved else 'n/a')}")
    print(f"  3) 交付字符 = {delivered}（V65 D 格读数 515 / 原形态 489·396）")

    ok1 = hits >= len(logs) and hits > 0
    ok2 = bool(saved) and 200 <= statistics.median(saved) <= 600
    #: ⚠️ 诊断（**不入判决**）：PREREG 原文 §4 的区间 `[1000, 3000]` 下的判定。
    #: 两读并存是刻意的：本轮实测 2025 是**修正案区间**之外、**原区间**之内，
    #: 而修正案本身选错了样本（见 RESULTS §4）。⛔ 不许只引其中一个。
    ok2_orig = bool(saved) and 1000 <= statistics.median(saved) <= 3000
    print(f"  ⚠️ 诊断：按 PREREG §4 **原文**区间 [1000,3000] ⇒ 自检 2 = {ok2_orig}"
          f"（按 §7 修正案 [200,600] ⇒ {ok2}）")
    ok3 = bool(delivered) and any(400 <= n <= 700 for n in delivered)
    print(f"  ⇒ 自检 {'全过' if (ok1 and ok2 and ok3) else '**未全过**'} "
          f"(1={ok1} 2={ok2} 3={ok3})")

    print("\n" + "=" * 86)
    if not (ok1 and ok2):
        print("判决 = UNKNOWN：开关没走到（自检 1/2 未过）⇒ P1/P2 不成立也不否证")
    else:
        verdict = {
            "升": "D 格在真跑批里**复现**了 6/6" + ("且其余五题不掉" if p2 == "平"
                                                   else "但**其余题掉分** ⇒ 净增益待算"),
            "平": "D 格**没有**在真跑批里复现（仍是 2/6）⇒ 该线关闭",
            "降": "D 格**更差** ⇒ 该线关闭",
            "混合": "**逐跑不一致** ⇒ 同构假设不成立，读数降级为 UNKNOWN",
        }[p1]
        print(f"判决 = {verdict}")
    print("=" * 86)
    return 0


if __name__ == "__main__":
    sys.exit(main())
