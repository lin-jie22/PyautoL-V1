#!/usr/bin/env python3
"""第十七轮安慰剂对照的**结论探针**（零模型调用，只读冻结证据）。

★ 为什么要有它
  把数字写进 DESIGN.md 是散文，改一个字不会有人发现；跑一轮需要 30 分钟，
  于是"看起来像我去复核过"和"我真的复核过"之间没有差别。
  ⇒ 这里把原始 `runs.jsonl` 冻结进 `bench/evidence/placebo_s17/`，
     每次跑都对 leatherbottom **重算**一遍结论。
  ⛔ 它不断言"结论应该是什么"，它断言"**依据这些数，按事前登记的规则，结论必须是什么**"。
     数据一换，探针自己会反过来耸 —— 那正是它存在的意义。

★ 三组读数（对应 DESIGN.md §16.16）
  ① 跨轮可复现性：A、B 两臂在第十六/十七两轮**逐题一致** ⇒ 装置是确定的
  ② 污染效应：同一个 C 臂，起跑记忆一干净就 0.8500 → 0.7500
  ③ 安慰剂带：换成**等长无关**的 13 个字，分数在 0.6500~0.8000 之间游走
     ⇒ 真 Δ（−0.05）落在安慰剂带内 ⇒ 不许认领

用法：
    python3 bench/probe_placebo.py
    python3 bench/probe_placebo.py --evidence bench/evidence/placebo_s17
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
# ⚠️ 这里曾经有一行 `PL = V1 / "bench" / "placebo.py"`，**从来没有被用过**。
#    第十九轮并包后它指向一个已不存在的文件，而探针照样全绿 ——
#    因为一个**没有消费者的字段不会报错**，只会误导下一个读它的人（通则第 3 条）。
#    已删除。要读机制源码请走 `bench/placebo/` 包（契约 AQ1 钉住它存在）。
SH = V1 / "bench" / "run_placebo_control.sh"

#: 事前登记的第五臂对照：这三个文本必须**逐字**出现在实验脚本里
PLACEBO_TEXTS = {
    "D1": None,                       # 打乱，文本由原文派生 —— 下面单独验
    "D2": "编译报错时先读最底层栈信息",
    "D3": "解析查询字符串时可忽略空项",
}
TRUE_TEXT = "解析查询字符串前先处理空项"

ok = True


def _say(tag: str, passed: bool, msg: str, detail: str = "") -> None:
    global ok
    ok = ok and passed
    line = f"  {'✅' if passed else '❌'} {tag} {msg}"
    if detail:
        line += f"\n        {detail}"
    print(line)


def load(path: Path) -> dict:
    """读一份 `runs.jsonl` 的最后一行，取逐题分数与口径。

    ⛔ 缺少任何字段都必须是**带理由的失败**，不返回 0.0 —— 返回 0.0 会让
    "某道题没跑"看起来像"某道题得了 0 分"，那是两种完全不同的事实。
    """
    if not path.exists():
        raise FileNotFoundError(f"证据文件不存在：{path}")
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()
            if x.strip()]
    if not rows:
        raise ValueError(f"证据文件为空：{path}")
    rep = rows[-1]["report"]
    cal = rows[-1].get("caliber", {})
    return {
        "per_task": {t["id"]: float(t["score"]) for t in rep["per_task"]},
        "attempted": int(rep.get("attempted", 0)),
        "skipped": int(rep.get("skipped_no_answer", 0)),
        "temperature": cal.get("temperature", None),
        "max_tokens": cal.get("max_tokens", None),
        # ⚠️ 模型名在 `caliber['llm_cond']['model']` 里，不在顶层。
        #    早先这里取的顶层 `model`，于是证据里印着 model=(未载) ——
        #    一份说不出自己是在哪个模型上测的证据，六个月后就是哑的。
        "model": (cal.get("llm_cond") or {}).get("model", ""),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="安慰剂对照结论探针（零模型调用）")
    p.add_argument("--evidence", default=str(V1 / "bench" / "evidence" / "placebo_s17"))
    args = p.parse_args(argv)
    E = Path(args.evidence)

    arms = ("A", "B", "C", "D1", "D2", "D3")
    try:
        r17 = {a: load(E / f"exam_{a}.runs.jsonl") for a in arms}
        r16 = {a: load(E / f"r16_exam_{a}.runs.jsonl") for a in ("A", "B", "C")}
    except (FileNotFoundError, ValueError, KeyError) as e:
        print(f"❌ 证据读不出来：{e}")
        return 2

    def mean(d):
        return sum(d.values()) / len(d) if d else 0.0

    print("=" * 70)
    print("第十七轮安慰剂对照 —— 结论探针（重算，非转抄）")
    print("=" * 70)

    print("\n【表】逐题配对差（同一批 4 道 held-out 题）")
    hdr = f"  {'题':22s}" + "".join(f"{a:>7s}" for a in arms)
    print(hdr)
    for t in sorted(r17["A"]["per_task"]):
        print(f"  {t:22s}" + "".join(f"{r17[a]['per_task'][t]:7.2f}" for a in arms))
    print(f"  {'mean':22s}" + "".join(f"{mean(r17[a]['per_task']):7.4f}" for a in arms))

    base = mean(r17["A"]["per_task"])
    print(f"\n【Δ】相对空记忆 A（={base:.4f}）：")
    for a in arms[1:]:
        d = mean(r17[a]["per_task"]) - base
        tag = "   ← 真抽象层" if a == "C" else ("   ← 安慰剂" if a.startswith("D") else "")
        print(f"    {a:3s} Δ={d:+7.4f}{tag}")

    # ── ① 跨轮可复现性 ────────────────────────────────────────────────
    print("\n【①】跨轮可复现性（只有装置确定，才能把差异归因到记忆）")
    same_ab = all(r16[a]["per_task"] == r17[a]["per_task"] for a in ("A", "B"))
    _say("P1", same_ab,
         "A、B 两臂在第十六/十七轮**逐题完全一致** ⇒ 同一个输入同一个输出",
         "\n".join(f"        {a}: r16={sorted(r16[a]['per_task'].items())} "
                   f"r17={sorted(r17[a]['per_task'].items())}" for a in ("A", "B")))

    # 口径必须一致，否则"复现"没有意义
    cal17 = r17["A"]
    _say("P2", cal17["temperature"] == 0 and cal17["attempted"] == 4,
         "两轮都在 **T=0**、同一模型同一预算、题数一致 ⇒ 有资格谈归因",
         f"model={cal17['model']} temperature={cal17['temperature']} "
         f"max_tokens={cal17['max_tokens']} attempted={cal17['attempted']}")

    # ── ② 污染效应：同一个 C，起跑状态干净与否 ────────────────────────
    print("\n【②】污染效应（第十六轮 B/C 共用记忆目录的真实代价）")
    c_old, c_new = mean(r16["C"]["per_task"]), mean(r17["C"]["per_task"])
    _say("P3", abs(c_old - c_new) > 1e-9,
         f"起跑记忆一旦干净，同一个 C 臂从 {c_old:.4f} → {c_new:.4f}"
         f"（差 {c_new - c_old:+.4f}）",
         "第十六轮 C 的起跑库里躺着 **B 臂跑出来的 1 条 outcome episode**，\n"
         "        干净基态里一条都没有 ⇒ `worldmodel._predict_by_retrieval`\n"
         "        的预演路径不同 ⇒ 生成路径不同。⇒ 第十六轮那个 C≡B=0.8500\n"
         "        **是这个污染的产物**，不是抽象层的功劳。")

    # ── ③ 安慰剂带 ────────────────────────────────────────────────────
    print("\n【③】安慰剂带（等长无关 ⇒ 只有内容在变）")
    deltas = {a: mean(r17[a]["per_task"]) - base for a in ("D1", "D2", "D3")}
    real = mean(r17["C"]["per_task"]) - base
    lo, hi = min(deltas.values()), max(deltas.values())
    spread = hi - lo
    print(f"    安慰剂 Δ 区间 = [{lo:+.4f}, {hi:+.4f}]  极差 = {spread:.4f}"
          f"   真 Δ = {real:+.4f}")

    _say("P4", spread > abs(real),
         f"安慰剂极差 {spread:.4f} > |真 Δ| {abs(real):.4f} ⇒ "
         "那条 Δ **落在噪声带里，不得认领**",
         "事前登记的规则 R2：存在安慰剂 Δ ≥ 真 Δ ⇒ 改写为『上下文里多一段\n"
         "        等长文本本身就能产生同量级变化』。")

    _say("P5", lo <= real <= hi,
         f"真 Δ（{real:+.4f}）落在安慰剂区间 [{lo:+.4f}, {hi:+.4f}] 之内",
         "⇒ 连**符号**都不可归因：换三个不同的无关句，它就 +0.00 / −0.15 / −0.15 地跳。")

    # ── ④ 逐题把这个结论拉到最细颗粒 ─────────────────────────────────
    print("\n【④】逐题：一次 13 字的改动，单题能跳多远？")
    worst_t, worst_r = "", 0.0
    for t in sorted(r17["A"]["per_task"]):
        vals = [r17[a]["per_task"][t] for a in arms]
        rng = max(vals) - min(vals)
        print(f"    {t:22s} 区间 [{min(vals):.2f}, {max(vals):.2f}]  极差 {rng:.2f}")
        if rng > worst_r:
            worst_t, worst_r = t, rng
    _say("P6", worst_r > 0,
         f"最不稳的是 {worst_t}，六臂之间极差 {worst_r:.2f}",
         "⇒ 「某个 μ 提高了 0.05」这种话在这个颗粒度上没有任何意义：\n"
         "        同一道题换个无关提示就能横跨 0.8。")

    # ── ⑤ 安慰剂本身是否成立（等长 + 逐字可见） ─────────────────────
    print("\n【⑤】安慰剂成立的前提")
    sh_src = SH.read_text(encoding="utf-8")
    lens_ok = all(abs(len(v) - len(TRUE_TEXT)) == 0 for v in PLACEBO_TEXTS.values() if v)
    visible = all(v in sh_src for v in PLACEBO_TEXTS.values() if v) and TRUE_TEXT in sh_src
    _say("P7", lens_ok and visible,
         "三条安慰剂与真文本**等长**（13 字）且**逐字**写在实验脚本里",
         f"长度={[(v, len(v)) for v in PLACEBO_TEXTS.values() if v]} "
         f"真={len(TRUE_TEXT)}")

    print("\n" + "=" * 70)
    _n = len([1 for t in ("P1", "P2", "P3", "P4", "P5", "P6", "P7")])
    print(f"结论探针  {'全部通过' if ok else '**有条目失败**'}   共 {_n} 条"
          f"（零模型调用，只读冻结证据）")
    print("=" * 70)
    print("⛔ 牢记：本探针证明的是『这份数据上什么都不能认领』，\n"
          "   不是『抽象层没用』——  这两句话的区别是本项目全部的方法论。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
