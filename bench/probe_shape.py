#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第十九轮「形状对照」的**结论探针**（零模型调用，只读冻结证据）。

★ 它要锁的那件事
  第十八轮的 `E1 = cipher` 给出了一条很硬的证据：把前面几道题的答案
  换成**等长、等行、等条目**的乱码，5 份答卷**逐字相同** ⇒
  「答案说了什么」的贡献是 0。

  但 `cipher` 同时保留了**形状**：字符数、逐行长度、缩进、换行、标点分布、
  字符类别（数字仍是数字、小写仍是大写……）。也就是说，模型的上下文里
  仍然躺着"一个看起来完全像代码的方块"，只是每个字都是暗号。

  ⇒ 剩下两种解释，直到本轮之前**分不开**：
     (a) 起作用的是"上下文里有那么一堆**形状对**的东西"；
     (b) 起作用的是"上下文里**多了一堆东西**"（连形状都不重要）。

  本轮的 F 臂（`reshape`）就是用来拆它们的：**字符序列一个字都没动**，
  只把换行位置重切 ⇒ 行宽、缩进、`def/return` 出现的列位置全部错位。

★ 事前登记（已写死在 `run_shape_control.sh` 开跑之前）
  S1  |E3 − T| > 0.05        ⇒ 装置改变了被测对象，整组作废
  S2  E3 − E0 < 0.15         ⇒ +0.2000 没复现，无法对它提问
  S3  E3 − F ≥ 0.15          ⇒ 形状**有**贡献
  S4  E3 − F < 0.05          ⇒ 形状**无**贡献 ⇒ 退到「纯存在性」
  S5  0.05 ≤ E3 − F < 0.15   ⇒ UNKNOWN（不许挑一个解释）
  S6  前五臂**逐题**读数必须与 s18 冻结值逐位相同
      ⇒ 否则「重构后的仪器」和「当年那台」不是同一台，两组数不许同表。

  ⛔ S6 是**本轮特有**的一条：这一轮同时把两个安慰剂脚本并成了一个包
     （§17-16）。合并机制时最容易发生的事故，就是"读数悄悄变了而没人发现"
     —— 所以这里不靠"我检查过了"，靠**逐题比对冻结值**。

用法：
    python3 bench/probe_shape.py
    python3 bench/probe_shape.py --evidence bench/evidence/shape_s19 --write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
DEFAULT_EV = V1 / "bench" / "evidence" / "shape_s19"
#: 第十八轮冻结的读数 —— S6 的参照。**只读数据，不引用它的判据**。
S18_EV = V1 / "bench" / "evidence" / "fakemem_s18"

ARMS = ("E0", "E3", "T", "E1", "E2", "F")
#: 事前登记的阈值 —— **只能在这里改，且改了必须同时改脚本里的打印**（同名同值）
EPS = 1e-9
THRESH_INSTRUMENT = 0.05
THRESH_REPRODUCE = 0.15
THRESH_SHAPE = 0.15
THRESH_PURE = 0.05

#: ★ 事前登记的规则 —— **唯一一处声明**。
#: 实验脚本不再自带一份（第十九轮中途发现的复发：`run_shape_control.sh` 里
#: 用 `cat <<RULES` 又抄了一遍，与这里的阈值构成"同一事实两处声明"。
#: 第十八轮已经栽过一次 —— 两份判读实现里一份多了两个 `**`，
#: 落盘判定与探针重算当场不等。⇒ 脚本改为调 `--rules` 打印这里这一份。）
RULES: tuple[tuple[str, str], ...] = (
    ("S1", f"|E3 - T| > {THRESH_INSTRUMENT}        ==> 装置改变了被测对象，整组作废"),
    ("S2", f"E3 - E0 < {THRESH_REPRODUCE}         ==> +0.2000 没复现，无法对它提问"),
    ("S3", f"E3 - F >= {THRESH_SHAPE}         ==> 形状有贡献（语义已由 E1 排除）"),
    ("S4", f"E3 - F < {THRESH_PURE}          ==> 形状无贡献 ==> 退到「纯存在性」"),
    ("S5", f"{THRESH_PURE} <= E3 - F < {THRESH_SHAPE}  ==> UNKNOWN（不许挑一个解释）"),
    ("S6", "前五臂逐题读数 != s18 冻结值 ==> 仪器不是同一台，整组作废"),
)


def print_rules() -> None:
    """把事前登记的规则原样打印出来（脚本在**开跑之前**调用它）。"""
    print("===================== 事前登记（不可事后修改）=====================")
    for tag, line in RULES:
        print(f"  {tag:3s} {line}")
    print("==================================================================")


ok = True


def verdict_of(instr_gap: float, gain_repro: float, shape_gap: float):
    """按事前登记的规则判读，返回 `(tag, text)`。

    ★★ 这是 S1–S5 的**全项目唯一一处**实现。
       `run_shape_control.sh` 收尾时直接调本文件的 `--write`，不自己再算一遍
       —— 第十八轮的真实教训：判读写两份，其中一份多了两个 `**`，
       落盘判定与探针重算立刻不等。
    """
    if instr_gap > THRESH_INSTRUMENT + EPS:
        return "VOID", "S1 命中（装置改变了被测对象）⇒ 判读无意义"
    if gain_repro < THRESH_REPRODUCE - EPS:
        return "VOID", "S2 命中（+0.2000 没复现）⇒ 无法对它提问"
    if shape_gap >= THRESH_SHAPE - EPS:
        return "S3", "形状有贡献 ⇒ 收益可归因到「上下文里有形状对的文本」"
    if shape_gap < THRESH_PURE - EPS:
        return "S4", "形状无贡献 ⇒ 收益退到「纯存在性」（有一堆字在场就够）"
    return "S5", f"UNKNOWN（{THRESH_PURE}–{THRESH_SHAPE} 之间，不许挑一个解释）"


def _say(tag: str, passed: bool, msg: str, detail: str = "") -> None:
    global ok
    ok = ok and passed
    line = f"  {'✅' if passed else '❌'} {tag} {msg}"
    if detail:
        line += f"\n        {detail}"
    print(line)


def load(path: Path) -> dict:
    """读一份 runs.jsonl 的最后一行。

    ⛔ 缺文件/缺字段一律**带理由地失败**，不返回 0.0 ——
       "某道题没跑"和"某道题得 0 分"是两种完全不同的事实。
    """
    if not path.exists():
        raise SystemExit(f"❌ 证据读不出来：证据文件不存在：{path}")
    lines = [x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not lines:
        raise SystemExit(f"❌ 证据读不出来：{path} 是空的")
    row = json.loads(lines[-1])
    rep = row.get("report") or {}
    per = rep.get("per_task")
    if not per:
        raise SystemExit(f"❌ 证据读不出来：{path} 没有 report.per_task")
    cal = row.get("caliber") or {}
    return {
        "per_task": {t["id"]: float(t["score"]) for t in per},
        "attempted": int(rep.get("attempted", len(per))),
        "temperature": cal.get("temperature", None),
        "max_tokens": cal.get("max_tokens", None),
        "model": cal.get("model", ""),
        "suite_sha": cal.get("suite_sha", ""),
        "scorer_sha": cal.get("scorer_sha", ""),
    }


def mean(d: dict) -> float:
    return sum(d.values()) / len(d) if d else 0.0


def main(argv: list[str] | None = None) -> int:
    global ok
    p = argparse.ArgumentParser(description="第十九轮形状对照的结论探针")
    p.add_argument("--evidence", default=str(DEFAULT_EV), help="冻结证据目录")
    p.add_argument("--write", action="store_true",
                   help="把本次判读结果写回证据目录的 verdict.json（实现只有这一处）")
    p.add_argument("--rules", action="store_true",
                   help="只打印事前登记的规则（实验脚本在**开跑之前**调它；"
                        "阈值只在本文件里声明一处）")
    args = p.parse_args(argv)
    if args.rules:
        print_rules()
        return 0
    ev = Path(args.evidence)

    print("=" * 78)
    print("第十九轮 · 形状对照结论探针（只读冻结证据，不调模型）")
    print("=" * 78)
    print(f"  证据目录 {ev}")
    print()

    res = {a: load(ev / f"{a}.runs.jsonl") for a in ARMS}
    stats = {}
    for a in ("T", "E1", "E2", "F"):
        sp = ev / f"{a}.stats.json"
        stats[a] = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}

    m = {a: mean(res[a]["per_task"]) for a in ARMS}

    # ── 逐题 ────────────────────────────────────────────────────────────
    print("── 逐题分数 ──")
    ids = sorted(res["E0"]["per_task"])
    print(f"  {'task':18s} " + " ".join(f"{a:>7s}" for a in ARMS))
    for t in ids:
        print(f"  {t:18s} "
              + " ".join(f"{res[a]['per_task'].get(t, float('nan')):7.2f}" for a in ARMS))
    print(f"  {'mean':18s} " + " ".join(f"{m[a]:7.4f}" for a in ARMS))
    print()

    gain_repro = m["E3"] - m["E0"]
    shape_gap = m["E3"] - m["F"]
    instr_gap = abs(m["T"] - m["E3"])
    tag, text = verdict_of(instr_gap, gain_repro, shape_gap)
    verdict = f"{tag}：{text}"

    # ── P1 同口径 ──────────────────────────────────────────────────────
    base = res["E0"]
    same_cal = all(
        res[a]["suite_sha"] == base["suite_sha"]
        and res[a]["scorer_sha"] == base["scorer_sha"]
        and res[a]["temperature"] == base["temperature"]
        and res[a]["max_tokens"] == base["max_tokens"]
        and res[a]["attempted"] == base["attempted"]
        for a in ARMS)
    _say("P1", same_cal,
         "六臂同一套题、同一把尺子、同一温度同一预算 ⇒ 有资格谈归因",
         f"model={base['model']} temperature={base['temperature']} "
         f"max_tokens={base['max_tokens']} attempted={base['attempted']} "
         f"suite={base['suite_sha'][:12]}")

    # ── P2 patch 生效且不变量没破 ──────────────────────────────────────
    for arm in ("E1", "E2", "F"):
        s = stats.get(arm) or {}
        _say(f"P2-{arm}", int(s.get("records", 0) or 0) > 0
             and not s.get("violations"),
             f"{arm} 臂确实把记忆换掉了，且没有一条违反等长/相异",
             f"episodes={s.get('records')} facts={s.get('facts')} "
             f"字符={s.get('chars')} 违反={len(s.get('violations') or [])} "
             f"工作记忆={s.get('fake_working')} 残留处理={s.get('working_prev_mapped')}条")
    ts = stats.get("T") or {}
    _say("P2-T", int(ts.get("identity_ok", 0) or 0) > 0 and not ts.get("violations"),
         "T 只读探针逐条验过「一个字都没改」",
         f"identity_ok={ts.get('identity_ok')} 违反={len(ts.get('violations') or [])}")

    # ── P2b F 臂：它到底动了多少（盲区必须一起念）──────────────────────
    fs = stats.get("F") or {}
    moved = int(fs.get("shape_lines_moved", 0) or 0) + \
        int(fs.get("shape_spaces_moved", 0) or 0)
    blind = int(fs.get("shape_identity", 0) or 0)
    _say("P2b-F", moved > 0,
         f"F 臂真的重排了结构：{moved} 条被重切/挪空白",
         f"重切行宽={fs.get('shape_lines_moved')} 挪空白={fs.get('shape_spaces_moved')} "
         f"退化切(无词边界)={fs.get('shape_unsafe_cuts')} "
         f"⚠️无可挪结构原样放行={blind} 条"
         f"（这 {fs.get('shape_identity_ok')} 条**没有**被操纵 —— 读 Δ 时必须一起念）")

    # ── P3 S1 装置 ─────────────────────────────────────────────────────
    ok1 = instr_gap <= THRESH_INSTRUMENT + EPS
    _say("P3/S1", ok1,
         f"|E3 − T| = {instr_gap:.4f} ≤ {THRESH_INSTRUMENT} ⇒ 装测量没改变被测对象"
         if ok1 else
         f"|E3 − T| = {instr_gap:.4f} > {THRESH_INSTRUMENT} ⇒ **整组作废**",
         f"E3={m['E3']:.4f} T={m['T']:.4f}")

    # ── P4 S2 复现 ─────────────────────────────────────────────────────
    ok2 = gain_repro >= THRESH_REPRODUCE - EPS
    _say("P4/S2", ok2,
         f"E3 − E0 = {gain_repro:+.4f} ≥ {THRESH_REPRODUCE} ⇒ +0.2000 复现，可以对它提问"
         if ok2 else
         f"E3 − E0 = {gain_repro:+.4f} < {THRESH_REPRODUCE} ⇒ 增益没复现，**无法对它提问**",
         f"E0={m['E0']:.4f} E3={m['E3']:.4f}")

    # ── P5 S6 仪器回归：前五臂逐题必须与 s18 逐位相同 ──────────────────
    old = {}
    old_ok = S18_EV.is_dir()
    if old_ok:
        try:
            old = {a: load(S18_EV / f"{a}.runs.jsonl")["per_task"]
                   for a in ("E0", "E3", "T", "E1", "E2")}
        except SystemExit as e:
            old_ok = False
            print(f"        {e}")
    diffs = []
    for a in ("E0", "E3", "T", "E1", "E2"):
        if a not in old:
            diffs.append(f"{a}: 无 s18 参照")
            continue
        for t in sorted(set(old[a]) | set(res[a]["per_task"])):
            v0 = old[a].get(t)
            v1 = res[a]["per_task"].get(t)
            if v0 is None or v1 is None or abs(v0 - v1) > 1e-9:
                diffs.append(f"{a}/{t}: s18={v0} 本轮={v1}")
    _say("P5/S6", old_ok and not diffs,
         "前五臂**逐题**与 s18 冻结值逐位相同 ⇒ 并包重构没动过读数"
         if (old_ok and not diffs) else
         "前五臂与 s18 不一致 ⇒ **仪器不是同一台**，两组数不许同表（整组作废）",
         f"比对 {5 * len(ids)} 个数，差异 {len(diffs)} 处"
         + (f"：{diffs[:4]}" if diffs else ""))

    # ── P6 S3/S4/S5 判读 ───────────────────────────────────────────────
    print()
    print("── 事前规则判读（S3 / S4 / S5）──")
    print(f"  E3 − F = {shape_gap:+.4f}    （E3 真记忆 {m['E3']:.4f} / F 破形状 {m['F']:.4f}）")
    print(f"  E3 − E1 = {m['E3'] - m['E1']:+.4f}  （语义：上一轮已测得 0.0000 且答卷逐字相同）")
    print(f"  E3 − E2 = {m['E3'] - m['E2']:+.4f}  （语义+形状一起毁 —— 无法单独归因）")
    print(f"  ⇒ 判读：{verdict}")

    _vp = ev / "verdict.json"
    frozen = json.loads(_vp.read_text(encoding="utf-8")) if _vp.exists() else {}
    if frozen:
        same = (frozen.get("verdict") == verdict
                and abs(float(frozen.get("shape_gap", -9)) - shape_gap) < 1e-6)
        _say("P7/重算一致", same,
             "当时落盘的判定 = 现在重算的判定（没人改过数据也没改过阈值）",
             f"落盘={frozen.get('verdict')!r} 重算={verdict!r} "
             f"gap {frozen.get('shape_gap')} vs {shape_gap:+.4f}")
    elif getattr(args, "write", False):
        # ★ 第十九轮实测踩到的自伤：脚本收尾调 `--write`，而 P7 在**写入之前**
        #   就要求"落盘的判定与重算一致" ⇒ 第一次跑必然判红，卡片上写着
        #   "❌ 结论探针判红 ⇒ 本组不认领"，而数据其实是好的。
        #   ⇒ 首次固化不是失败，是"还没有可对账的东西"；但**写完必须自检**。
        _say("P7/首次固化", True,
             "此前没有判定可对账 —— 本次 --write 固化第一份",
             f"目标 {_vp}")
    else:
        _say("P7/重算一致", False, "缺少 verdict.json ⇒ 无法验证重算一致性", f"期望 {_vp}")

    if getattr(args, "write", False):
        out = {
            "arms": m, "per_task": {a: res[a]["per_task"] for a in ARMS},
            "gain_reproduced": round(gain_repro, 4),
            "shape_gap": round(shape_gap, 4),
            "semantic_gap": round(m["E3"] - m["E1"], 4),
            "both_gap": round(m["E3"] - m["E2"], 4),
            "instrument_gap": round(instr_gap, 4),
            "s18_bitwise_same": bool(old_ok and not diffs),
            "s18_diffs": diffs[:20],
            "tag": tag, "verdict": verdict,
        }
        _vp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  已固化到 {_vp}")
        # ★ 写完**读回来**自检：只相信"我再读一遍拿到的"，不相信刚才那句 write_text。
        _back = json.loads(_vp.read_text(encoding="utf-8"))
        _say("P7b/固化自检", _back.get("verdict") == verdict
             and abs(float(_back.get("shape_gap", -9)) - shape_gap) < 1e-6,
             "固化后回读 = 本次判读（写盘没有把它写坏）",
             f"回读={_back.get('verdict')!r}")

    print()
    print("=" * 78)
    print("结论探针  " + ("全部通过" if ok else "有条目失败"))
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
