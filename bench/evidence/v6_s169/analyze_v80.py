"""V80 读数：把「分歧」按 label 分组，看它到底是不是错的信号。"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "bench/evidence/v6_s169")
from probe_diffself import (CASES, EV, battery, fn_of, load_tasks,  # noqa: E402
                            pair_diverges, run_sample)


def rate(n: int, d: int) -> str:
    return f"{d}/{n} = {(d / n if n else float('nan')):.4f}"


def detector_check() -> bool:
    """检测器有效性：oracle vs partial / oracle vs 空实现 **必须**分歧。

    ⛔ 这条不做，本轮全部读数都可能只是「我的比较器坏了」。
    """
    print("── 检测器自检（不调 LLM）：oracle vs partial / 空实现 必须全部分歧")
    ok_all = True
    for tag, path in CASES:
        for t in load_tasks(path):
            fn = fn_of(t["oracle"])
            calls = battery(t["tests"], fn)
            o = run_sample(t["oracle"], fn, calls)
            p = run_sample(t.get("partial") or "", fn, calls)
            s = run_sample(f"def {fn}(*a, **k):\n    return None\n", fn, calls)
            line = f"  {t['id']:22s}"
            for name, other in (("partial", p), ("stub", s)):
                if isinstance(o, str) or isinstance(other, str):
                    line += f" {name}=装置故障"
                    ok_all = False
                    continue
                div, bad = pair_diverges(o, other)
                line += f" {name}={'分歧✅' if div else '同结果❌'}({len(bad)}点)"
                ok_all &= div
            print(line)
    print(f"检测器自检 {'通过 ✅' if ok_all else '失败 ❌ ⇒ 本轮读数作废'}")
    return ok_all


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "probe_diffself.json"
    print("── 检测器自检（不调 LLM）：oracle vs partial / 空实现 必须全部分歧")
    det_ok = detector_check()
    d = json.loads((EV / name).read_text(encoding="utf-8"))
    tasks = d["tasks"]
    print(f"模型={d['meta']['model']} T={d['meta']['temp']} "
          f"每题采样={d['meta']['samples']} 用时={d['meta']['elapsed_s']}s\n")

    for t in tasks:
        g = {}
        for p in t["pairs"]:
            g.setdefault(p["group"], []).append(p)
        s = " ".join(f"{k}={sum(1 for p in v if p['diverge'])}/{len(v)}"
                     for k, v in sorted(g.items()))
        print(f"{t['suite']:8s} {t['id']:22s} labels={''.join(x[0] for x in t['labels'])} "
              f"文本去重={t['text_unique']} {s}")

    print("\n── 分组汇总（主分母 = 实现对）")
    tot = {}
    per_suite = {}
    pts = {}
    for t in tasks:
        for p in t["pairs"]:
            k = p["group"]
            tot.setdefault(k, [0, 0])
            tot[k][0] += 1
            tot[k][1] += int(p["diverge"])
            key = (t["suite"], k)
            per_suite.setdefault(key, [0, 0])
            per_suite[key][0] += 1
            per_suite[key][1] += int(p["diverge"])
            pts.setdefault(k, [0, 0])
            pts[k][0] += t["n_inputs"]
            pts[k][1] += len(p["bad_idx"])
    for k in ("PP", "FF", "MIX"):
        n, dd = tot.get(k, [0, 0])
        print(f"  {k:3s} 对={n:3d} 分歧={rate(n, dd)}   "
              f"按输入点={rate(pts[k][0], pts[k][1]) if k in pts else '-'}")

    print("\n── 按套件")
    for (suite, k), (n, dd) in sorted(per_suite.items()):
        print(f"  {suite:8s} {k:3s} {rate(n, dd)}")

    pp = tot.get("PP", [0, 0])
    ff = tot.get("FF", [0, 0])
    mx = tot.get("MIX", [0, 0])
    r_pp = pp[1] / pp[0] if pp[0] else None
    r_ff = ff[1] / ff[0] if ff[0] else None
    r_mx = mx[1] / mx[0] if mx[0] else None

    print("\n── 采样池状态")
    uniq = [t["text_unique"] for t in tasks]
    invalid = sum(1 for t in tasks for x in t["labels"] if x == "invalid")
    empt = sum(1 for t in tasks for x in t["labels"] if x == "empty")
    n_s = sum(len(t["labels"]) for t in tasks)
    print(f"  文本去重：中位数={statistics.median(uniq)} 最小={min(uniq)} "
          f"=1 的题={sum(1 for u in uniq if u == 1)}/{len(uniq)}")
    print(f"  样例 {n_s} 份：pass={sum(1 for t in tasks for x in t['labels'] if x == 'pass')} "
          f"fail={sum(1 for t in tasks for x in t['labels'] if x == 'fail')} "
          f"invalid={invalid} empty={empt}")
    #: ⚠️ 两种"跑不了"必须分开：**模型交了不能 exec 的东西**（C 代码 / 语法错）
    #: 与**我的装置挂了**（超时 / 子进程无输出）。前者是被测者的失败模式，
    #: 后者才让比较器不可信（PREREG §7-3 只对后者设闸）。
    noexec = [f for t in tasks for f in t["faults"] if f.startswith("FATAL(")]
    harness = [f for t in tasks for f in t["faults"]
               if f and not f.startswith("FATAL(")]
    print(f"  跑不了：模型侧不可执行={len(noexec)}  装置侧故障={len(harness)}"
          f"（阈值 10% = {0.1 * n_s:.1f} 份）")
    if noexec:
        why = {}
        for f in noexec:
            k = f[6:].split(":")[0]
            why[k] = why.get(k, 0) + 1
        print(f"    模型侧原因：{why}")

    print("\n── 判决（PREREG §5/§6）")
    if r_ff is not None and r_pp is not None:
        print(f"  Δ = R_FF − R_PP = {r_ff - r_pp:+.4f}   （押 ≥ +0.30）")
    gate = [
        ("检测器自检通过（oracle vs partial/空实现 全分歧）", det_ok),
        ("MIX == 1.0（构造自检）", r_mx is not None and abs(r_mx - 1.0) < 1e-9),
        ("文本去重中位数 ≥ 2", statistics.median(uniq) >= 2),
        ("装置侧故障 < 10%", len(harness) < 0.1 * n_s),
        ("R_FF ≥ 0.40", r_ff is not None and r_ff >= 0.40),
        ("R_PP ≤ 0.20", r_pp is not None and r_pp <= 0.20),
        ("≥2 套件里 FF 分歧非空",
         len({s for (s, k), (n, dd) in per_suite.items() if k == "FF" and dd > 0}) >= 2),
    ]
    for label, ok in gate:
        print(f"  [{'✅' if ok else '❌'}] {label}")
    print(f"\n⇒ C 方案{'**可以开**' if all(x[1] for x in gate) else '**不开**'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
