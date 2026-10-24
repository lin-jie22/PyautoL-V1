#!/usr/bin/env python3
"""噪声下限必须在**进程之间**量 —— 每条检查自带反证。

## 这个探针量什么

    P1 同一次运行内部 repeat 5 次 ≠ 5 次运行（必须拒判，而不是给出极差）
    P2 只有 4 次独立运行 ⇒ 拒判（不是"噪声是 0"）
    P3 混口径 ⇒ 拒判
    P4 旧记录没有 run_id ⇒ 抛错（不静默收下不可判的读数）
    P5 拿**最小**的逐题极差当聚合下限会低估（构造反例）
    P6 不变量：逐题最大极差 ≥ 聚合极差（定理，不是经验）
    P7 轮内 0 / 轮间 0.7143 ⇒ "拿轮内当噪声下限会低估"被点亮
    P8 **端到端**：同一个 Δ，旧口径判 KEEP，新口径判 NO_EVIDENCE
    P9 真实池子（冻结在 evidence/）：≥5 次独立运行、同一口径
    P10 池子的拒收统计可见（旧记录不计入，但不是"不存在"）
    P11 report() 的"最不稳"那句不许在**全 0** 或**并列**时点名

⛔ **不**量"系统变聪明了"。这里只有一件事：**噪声下限有没有量错地方**。

退出码：0 = 全部成立；1 = 有不成立项。
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V1 / "bench"))

import mutation_check as MC                                       # noqa: E402
import noise_floor as NF                                          # noqa: E402

_CHECKS: list[tuple[str, bool]] = []
_EV = V1 / "bench" / "evidence" / "noise_s72"


def ck(name: str, cond: bool) -> bool:
    _CHECKS.append((name, bool(cond)))
    return bool(cond)


def _synth_pool(per_run: list[dict[str, float]]) -> list[NF.Obs]:
    """构造一个"多次独立运行"的池子 —— 只用于验证**打印**，不参与裁决。"""
    out = []
    for i, pt in enumerate(per_run):
        m = statistics.fmean(pt.values())
        out.append(NF.Obs(arm="构造臂", sig="构造口径", run_id=f"r{i}", mean=m,
                          repeat=1, per_task=tuple(sorted(pt.items())),
                          rep_scores=(m,)))
    return out


def main() -> int:
    rp = NF.reverse_proofs()

    # ── P1–P4 四条纪律的坏样本（实现在 noise_floor.reverse_proofs，只此一份）──
    v, why = rp["同一次运行五次重复"]
    ck("P1 同一次运行 repeat 5 次 ⇒ 拒判（不是 5 次运行）", v is None)
    print(f"    {why}")
    v, why = rp["只有四次运行"]
    ck("P2 只有 4 次独立运行 ⇒ 拒判（不默认 0）", v is None)
    print(f"    {why}")
    v, why = rp["混口径"]
    ck("P3 混口径 ⇒ 拒判", v is None)
    print(f"    {why}")
    ck("P4 缺 run_id ⇒ 抛错（旧记录不收，不静默跳过）",
       str(rp["缺 run_id"]).startswith("ValueError"))

    # ── P5 逐题极差：min 会低估，max 不会 ─────────────────────────────────
    lo, agg = rp["逐题最小当聚合下限"]
    hi, _ = rp["逐题最大当聚合下限"]
    ck("P5 拿最小逐题极差当聚合下限会低估 ⇒ 只能取最大", lo < agg - 1e-9 <= hi)
    print(f"    逐题最小 {lo:.4f} / 最大 {hi:.4f} / 聚合 {agg:.4f}")

    # ── P6 不变量（定理）────────────────────────────────────────────────────
    ok, why = rp["不变量"]
    ck("P6 不变量：逐题最大极差 ≥ 聚合极差", ok)
    print(f"    {why}")

    # ── P7 轮内为 0、轮间不为 0 ⇒ 旧口径低估 ───────────────────────────────
    br = rp["轮内为 0"]
    ck("P7 轮内极差 0 而轮间极差 > 0 ⇒ '低估' 必须为真",
       br["intra_max"] == 0.0 and br["floor_agg"] > 0.7 and br["underestimates"])
    print(f"    轮内 {br['intra_max']:.4f} / 轮间 {br['floor_agg']:.4f}")

    # ── P8 端到端：换输入 ⇒ 结论翻转（裁决逻辑一个字没改）──────────────────
    # 一个"看起来是改进"的小 Δ。旧做法把轮内极差 0.0 喂进去 ⇒ 任何非零差都显著；
    # 新做法把轮间极差喂进去 ⇒ 同一 Δ 落在噪声里。
    delta = 0.02
    old_v, old_why = MC.mutation_verdict(knob="max_stall", diff=["max_stall"],
                                        base_mean=0.2857, cand_mean=0.2857 + delta,
                                        placebo_delta=0.0, noise_floor=0.0)
    new_v, new_why = MC.mutation_verdict(knob="max_stall", diff=["max_stall"],
                                        base_mean=0.2857, cand_mean=0.2857 + delta,
                                        placebo_delta=0.0, noise_floor=br["floor_agg"])
    ck(f"P8 同一个 Δ={delta:+.2f}：旧口径 {old_v}、新口径 {new_v} ⇒ 结论翻转",
       old_v == MC.KEEP and new_v == MC.NO_EVIDENCE)
    print(f"    旧：{old_why}")
    print(f"    新：{new_why}")

    # ── P7b 同口径不同臂 ⇒ 必须拒判（本轮的 sig 完全相同）──────────────────
    mix, mix_why = rp["同口径不同臂"]
    fake = rp["只按 sig 分组的假极差"]
    ck("P7b 同口径、不同臂 ⇒ 拒判；只按 sig 分组会造出假极差",
       mix is None and fake > 0.3)
    print(f"    假极差 {fake:.4f}（那是两个系统的差，不是抖动）｜ {mix_why}")

    # ── P9 两个真实池子 ─────────────────────────────────────────────────────
    d, drop_d = NF.pool_from_jsonl(_EV / "pool_direct_r1.jsonl")
    k, drop_k = NF.pool_from_jsonl(_EV / "pool_kernel_r1.jsonl")
    br_d, br_k = NF.bracket(d), NF.bracket(k)
    ck("P9a 裸模型池：5 次独立运行、同口径、轮间极差 0.0000",
       br_d["floor_agg"] == 0.0 and br_d["n_runs"] >= NF.MIN_RUNS)
    ck("P9b 内核池：5 次独立运行、同口径、轮间极差 0.0000",
       br_k["floor_agg"] == 0.0 and br_k["n_runs"] >= NF.MIN_RUNS)
    for line in NF.report(br_d, label="裸模型（5 个独立进程）"):
        print(line)
    for line in NF.report(br_k, label="内核（5 个独立进程）"):
        print(line)

    # ── P10 「0」是测出来的，不是默认值 ─────────────────────────────────────
    f5, _ = NF.floor(d)
    f4, why4 = NF.floor(d[:4])
    ck("P10 5 次 ⇒ 0.0000（确定）；4 次 ⇒ None（没测成）—— 两者不许混",
       f5 == 0.0 and f4 is None)
    print(f"    4 次：{why4}")

    # ── P11 拒收可见（构造一份带旧记录的池子，验证计数器）─────────────────
    # 旧记录（本轮之前落盘的 83 条）没有 run_id。它们不是"0 条读数"，
    # 是"被拒的 N 条"—— 计数器必须把 N 说出来。
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "mixed.jsonl"
        old = {"solver": "老臂", "caliber_sig": "x", "repeat": 2,
               "measurement": {"mean": 0.5}, "report": {"per_task": []}}
        f.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in [old]),
                     encoding="utf-8")
        with f.open("a", encoding="utf-8") as fh:
            fh.write((_EV / "pool_direct_r1.jsonl").read_text(encoding="utf-8"))
        kept, drop = NF.pool_from_jsonl(f)
    ck("P11 带旧记录的池子：拒收被**数出来**（1 条无 run_id），不是静默消失",
       len(kept) == 5 and drop.get("no_run_id") == 1)
    print(f"    收下 {len(kept)} 条 / 拒收 {drop}")

    # ── P12 打印不许造假：全 0 或并列时不许点名 ────────────────────────────
    # 坏的写法（本轮改掉的）：`sorted(...)[0]` 一取，全 0 时也会吐出 sg01，
    # 读起来像"sg01 在抖"。这是**读数被读大**，不是排版问题。
    z = "\n".join(NF.report(NF.bracket(d), label="全 0（真实裸模型池）"))
    ck("P12a 逐题极差全 0 ⇒ 不许点名某一题（旧写法会印「最不稳的是 sg01」）",
       "没有「最不稳」的一题" in z and "最不稳的是" not in z)

    tie = _synth_pool([{"t1": 0.0, "t2": 0.5}, {"t1": 0.5, "t2": 0.0},
                       {"t1": 0.5, "t2": 0.0}, {"t1": 0.0, "t2": 0.5},
                       {"t1": 0.5, "t2": 0.0}])
    t = "\n".join(NF.report(NF.bracket(tie), label="并列"))
    ck("P12b 两题极差并列 ⇒ 印「并列最不稳」并列出两题（不许只挑一个）",
       "并列最不稳" in t and "t1" in t and "t2" in t and "最不稳的是" not in t)

    one = _synth_pool([{"t1": 0.0, "t2": 0.0}, {"t1": 0.5, "t2": 0.0},
                       {"t1": 0.5, "t2": 0.0}, {"t1": 0.0, "t2": 0.0},
                       {"t1": 0.5, "t2": 0.0}])
    o = "\n".join(NF.report(NF.bracket(one), label="单题"))
    ck("P12c 只有一题在抖 ⇒ 正常点名它（新分支不许把三档都吞成「全 0」）",
       "最不稳的是 t1" in o and "并列" not in o and "没有「最不稳」" not in o)
    print(f"    全0：{z.splitlines()[-1].strip()}")
    print(f"    并列：{t.splitlines()[-1].strip()}")
    print(f"    单题：{o.splitlines()[-1].strip()}")

    print()
    for name, okk in _CHECKS:
        print(f"  {'✅' if okk else '❌'} {name}")
    bad = [n for n, okk in _CHECKS if not okk]
    print()
    print(f"  {'✅ 噪声下限量对了地方：' + str(len(_CHECKS)) + ' 项全部成立' if not bad else '❌ 不成立：' + str(bad)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
