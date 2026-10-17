#!/usr/bin/env python3
"""第十八轮假记忆对照的**结论探针**（零模型调用，只读冻结证据）。

★ 它要锁的那件事
  §16.14 测到一个跨任务增益 **+0.2000**（隔离 0.5600 → 累积 0.7600），
  排序对照已经证明它"属于位置，不属于能力"，但还证明不了它的另一半：
      「这段提升是因为前面那些题的答案**说了什么**，
        还是仅仅因为上下文里多了那么一堆字？」
  ⇒ 本轮把前面的答案换成**等长、等条数、等结构**的假东西。

  ⛔ 探针**不锁结论** —— 它锁的是：
     「依据这些冻结的数据，按事前登记的规则，结论**必须**是什么」。
     数据换一个字节，探针自己会转红。

★ 事前登记（已写死在 run_fakemem_control.sh 的开跑之前）
  R1  |E3 − T| > 0.05         ⇒ 装置改变了被测对象，整组作废
  R2  E3 − max(E1,E2) < 0.05  ⇒ +0.2000 降级为「存在性效应」，不得认领
  R3  E3 − max(E1,E2) ≥ 0.15  ⇒ 才允许说「记忆的内容真的带来了收益」
  R4  落在中间                ⇒ UNKNOWN，不许挑一个解释
  R5  E3 − E0 < 0.15          ⇒ +0.2000 本身没复现，无法对它提问

用法：
    python3 bench/probe_fakemem.py
    python3 bench/probe_fakemem.py --evidence bench/evidence/fakemem_s18
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
DEFAULT_EV = V1 / "bench" / "evidence" / "fakemem_s18"

ARMS = ("E0", "E3", "T", "E1", "E2")
#: 事前登记的阈值 —— **只能在这里改，且改了必须同时改脚本里的打印**（同名同值）
EPS = 1e-9
THRESH_DOWNGRADE = 0.05
THRESH_CONTENT = 0.15
THRESH_REPRODUCE = 0.15
THRESH_INSTRUMENT = 0.05

ok = True


def verdict_of(gain_repro: float, content_gap: float, instr_gap: float):
    """按事前登记的规则判读，返回 `(tag, text)`。

    ★★ 这是**全项目唯一一处**判读实现。
       `run_fakemem_control.sh` 收尾时**直接调本文件的 `--write`**，
       不再自己算一遍 —— 因为第十八轮的真实教训就是：
       两份实现各写一遍，其中一份多了两个 `**`，`verdict.json` 与
       探针重算立刻不等 ⇒ 冻结证据与判据当场打架。
       （这正是 V5 迁移规则第 2 条：同一事实只声明**一处**。）
    """
    ok_instr = instr_gap <= THRESH_INSTRUMENT + EPS
    ok_repro = gain_repro >= THRESH_REPRODUCE - EPS
    if not (ok_instr and ok_repro):
        return "VOID", "前置闸门不成立（装置改变了被测对象，或 +0.2000 没复现）⇒ 判读无意义"
    if content_gap < THRESH_DOWNGRADE - EPS:
        return "R2", "+0.2000 降级为「存在性效应」⇒ 不得认领（也不许反向读成有害）"
    if content_gap >= THRESH_CONTENT - EPS:
        return "R3", "允许说「记忆的内容真的带来了收益」"
    return "R4", f"UNKNOWN（{THRESH_DOWNGRADE}–{THRESH_CONTENT} 之间，不许挑一个解释）"


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
       "某道题没跑"和"某道题得 0 分"是两种完全不同的事实，
       前者填成后者的那一刻，结论就已经不可信了。
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
    p = argparse.ArgumentParser(description="第十八轮假记忆对照的结论探针")
    p.add_argument("--evidence", default=str(DEFAULT_EV), help="冻结证据目录")
    p.add_argument("--write", action="store_true",
                   help="把本次判读结果写回证据目录的 verdict.json"
                        "（脚本收尾用；实现只有这一处）")
    args = p.parse_args(argv)
    ev = Path(args.evidence)

    print("=" * 74)
    print("第十八轮 · 假记忆对照结论探针（只读冻结证据，不调模型）")
    print("=" * 74)
    print(f"  证据目录 {ev}")
    print()

    res = {a: load(ev / f"{a}.runs.jsonl") for a in ARMS}
    stats = {}
    for a in ("T", "E1", "E2"):
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
    placebo_max = max(m["E1"], m["E2"])
    content_gap = m["E3"] - placebo_max
    instr_gap = abs(m["T"] - m["E3"])
    tag, text = verdict_of(gain_repro, content_gap, instr_gap)
    verdict = f"{tag}：{text}"

    # ── P1 同口径：五臂必须在同一把尺子上 ──────────────────────────────
    base = res["E0"]
    same_cal = all(
        res[a]["suite_sha"] == base["suite_sha"]
        and res[a]["scorer_sha"] == base["scorer_sha"]
        and res[a]["temperature"] == base["temperature"]
        and res[a]["max_tokens"] == base["max_tokens"]
        and res[a]["attempted"] == base["attempted"]
        for a in ARMS)
    _say("P1", same_cal,
         "五臂同一套题、同一把尺子、同一温度同一预算 ⇒ 有资格谈归因",
         f"model={base['model']} temperature={base['temperature']} "
         f"max_tokens={base['max_tokens']} attempted={base['attempted']} "
         f"suite={base['suite_sha'][:12]}")

    # ── P2 patch 确实生效，且没有违反不变量 ────────────────────────────
    for arm in ("E1", "E2"):
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

    # ── P3 R1：装置没有改变被测对象 ────────────────────────────────────
    ok1 = instr_gap <= THRESH_INSTRUMENT + EPS
    _say("P3/R1", ok1,
         f"|E3 − T| = {instr_gap:.4f} ≤ {THRESH_INSTRUMENT} ⇒ 装测量没改变被测对象"
         if ok1 else
         f"|E3 − T| = {instr_gap:.4f} > {THRESH_INSTRUMENT} ⇒ **整组作废**",
         f"E3={m['E3']:.4f} T={m['T']:.4f}")

    # ── P4 R5：+0.2000 本身复现了吗 ────────────────────────────────────
    ok5 = gain_repro >= THRESH_REPRODUCE - EPS
    if ok5:
        msg5 = f"E3 − E0 = {gain_repro:+.4f} ≥ {THRESH_REPRODUCE} ⇒ §16.14 的 +0.2000 复现了，可以对它提问"
    else:
        msg5 = (f"E3 − E0 = {gain_repro:+.4f} < {THRESH_REPRODUCE} "
                f"⇒ 增益没复现，**无法对它提问**")
    _say("P4/R5", ok5, msg5, f"E0={m['E0']:.4f} E3={m['E3']:.4f}")

    # ── P5 R2/R3/R4：事前规则判读 ──────────────────────────────────────
    print()
    print("── 事前规则判读（R2 / R3 / R4）──")
    print(f"  E3 − max(E1, E2) = {content_gap:+.4f}")
    print(f"  ⇒ 判读：{verdict}")

    # ⛔ 刻意**不**断言"结论应当是哪一条" —— 那会把一次测量钉成一句教条。
    #    这里只验一件可算的事：脚本当时写进 verdict.json 的判定，
    #    必须等于现在按同样的数据重算出来的判定。两者一旦不等，
    #    说明有人改了数据或改了阈值而没有重跑。
    _vp = ev / "verdict.json"
    frozen = json.loads(_vp.read_text(encoding="utf-8")) if _vp.exists() else {}
    if frozen:
        same = (frozen.get("verdict") == verdict
                and abs(float(frozen.get("content_gap", -9)) - content_gap) < 1e-6)
        _say("P5/重算一致", same,
             "当时落盘的判定 = 现在重算的判定（没人改过数据也没改过阈值）",
             f"落盘={frozen.get('verdict')!r} 重算={verdict!r} "
             f"gap {frozen.get('content_gap')} vs {content_gap:+.4f}")
    else:
        _say("P5/重算一致", False, "缺少 verdict.json ⇒ 无法验证重算一致性",
             f"期望 {_vp}")

    if getattr(args, "write", False):
        out = {
            "arms": m, "per_task": {a: res[a]["per_task"] for a in ARMS},
            "gain_reproduced": round(gain_repro, 4),
            "content_gap": round(content_gap, 4),
            "instrument_gap": round(instr_gap, 4),
            "tag": tag, "verdict": verdict,
        }
        (_vp).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        print(f"\n  已固化到 {_vp}")

    print()
    print("=" * 74)
    print("结论探针  " + ("全部通过" if ok else "有条目失败"))
    print("=" * 74)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
