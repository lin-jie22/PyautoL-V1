#!/usr/bin/env python3
"""第二十一轮**真模型**读数探针（零模型调用，只重算冻结证据）。

## 它回答的问题

`mw/verify.py` + `core._gate_finish` 接上之后，同一批题、同一把尺子、同一个模型、
T=0 的分数会不会动？如果动了，是**判据的诊断信息**在起作用，还是**打回重写**
这个动作本身在起作用？

三条臂（唯一变量 = `_gate_finish` 的行为）：

| 臂 | 闸 | 打回时说的那句话 |
|---|---|---|
| OFF | 不存在（`verify_retries=0`） | —— |
| ON | 真的判据 | 「编译失败 line 7: unicodeescape …」（**有信息**） |
| PL  | **同一份判据、同一时机、同样次数** | 「请再检查一遍，逐条核对要求」（**无信息**） |

⇒ ON 与 PL 的差别只有一处：那句话里有没有关于这个答案的信息。

## 判读（事前登记，写在跑之前）

  Q1 对照必须成立：ON 与 OFF 必须**至少一题**不同。全同 ⇒ 闸没触发，
     那时 Δ=0 说明的是"没靶子"，不是"没效果"，整组按 UNKNOWN 记。
  Q2 位移必须能**定位**：只报均值不算读数，必须点名是哪道题。
  Q3 **安慰剂**：把 `elapsed_s` 去掉后，ON 与 PL 的报告若**逐字相同**
     ⇒ 判「收益来自打回重写，不来自诊断信息」⇒ **§17-6 仍是 0，不得认领**。
     PL 明显低于 ON ⇒ 判「诊断信息有用」。
     PL 高于 ON ⇒ 判「装置有问题」，不许挑好的那个读。
  Q4 跨题集复核：`bench/heldout`（7 题）若 Δ=0，必须如实写"没有复现"，
     ⛔ 不许只报 `struct_convert` 那一个 Δ —— 那是拿最好看的一格当结论。
  Q5 落盘判定与重算判定必须一致（判定不许躺在散文里）。
  Q6 **这一对数字不是新证据**：OFF/ON 的逐题分数与第十八轮假记忆臂 E0/E3
     **逐位相同** ⇒ 5 题里只有 1 道题有位移空间，任何"让它重试一次"的机制
     都会拿到一模一样的 +0.2000。⛔ 不许把"不同机制、同一对数字"读成互相印证。

## 已知边界（都要写）

- **n=1 题**发生了位移。1 题在 5 题套件里就是 0.20，在 12 题里是 0.083。
  这不是"提升 20%"，这是一次**单点事件**。
- 每臂只跑一次。T=0 保证可复现（第 1 条已实测），但**不保证因果**
  （第十七轮的教训）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V1))

EV = V1 / "bench" / "evidence" / "verify_s21" / "real"

RULES: tuple[tuple[str, str], ...] = (
    ("Q1", "ON 与 OFF 至少一题不同 —— 全同 ⇒ 闸没触发，Δ=0 记 UNKNOWN，不认领"),
    ("Q2", "位移必须点名定位，不许只报均值"),
    ("Q3", "ON 与 PL 去掉 elapsed_s 后逐字相同 ⇒ 收益属于『打回重写』，诊断信息贡献 0"),
    ("Q4", "跨题集（heldout 7 题）Δ=0 必须如实写『没有复现』，不许只报好看的那一格"),
    ("Q6", "OFF/ON 逐题必须与第十八轮 E0/E3 **逐位相同** ⇒ 同一道题的第二次 0→1，"
           "⛔ 不许读成『两种机制互相印证』（那是题集太窄的指纹）"),
)


def print_rules() -> None:
    print("===================== 事前登记（不可事后修改）=====================")
    for tag, line in RULES:
        print(f"  {tag:3s} {line}")
    print("==================================================================")


def _load(name: str) -> list[dict]:
    p = EV / name
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _load_at(p: Path) -> list[dict]:
    """从**任意路径**读冻结证据（Q6 要读第十八轮的文件，它不在 `EV` 下）。"""
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _per_task(records: list[dict]) -> dict[str, float]:
    """把一个臂的**全部**记录摊平成 {题号: 分数}。

    ⚠️ 必须取全部记录：`--suite-dir` 每个领域写**一条**记录，
    只读最后一条 = 只读了最后一个领域（第一版就是这么错的，heldout 少读了 3 题）。
    """
    out: dict[str, float] = {}
    for r in records:
        for t in (r.get("report") or {}).get("per_task") or []:
            out[str(t["id"])] = float(t["score"])
    return out


def _canon(records: list[dict]) -> str:
    """去掉**只影响耗时**的字段后的规范化文本 —— 用来判"两次读数是不是同一件事"。"""
    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k != "elapsed_s"}
        if isinstance(o, list):
            return [strip(x) for x in o]
        return o
    return json.dumps([strip(r) for r in records], ensure_ascii=False, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="第二十一轮真模型读数探针")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--rules", action="store_true")
    args = ap.parse_args(argv)
    if args.rules:
        print_rules()
        return 0

    ok = True

    def say(name: str, good: bool, why: str, detail: str = "") -> bool:
        nonlocal ok
        ok = ok and good
        print(f"  {'✅' if good else '❌'} {name}  {why}")
        if detail:
            print(f"       {detail}")
        return good

    sc_off = _per_task(_load("sc_off.jsonl"))
    sc_on = _per_task(_load("sc_on.jsonl"))
    sc_pl = _per_task(_load("sc_placebo.jsonl"))
    ho_off = _per_task(_load("ho_off.jsonl"))
    ho_on = _per_task(_load("ho_on.jsonl"))

    def mean(d):
        return round(sum(d.values()) / len(d), 4) if d else None

    print("一、struct_convert（5 题，第十九轮六臂的同一批题）")
    ids = sorted(set(sc_off) | set(sc_on) | set(sc_pl))
    for tid in ids:
        a, b, c = sc_off.get(tid), sc_on.get(tid), sc_pl.get(tid)
        mark = "" if a == b == c else "   <<<"
        print(f"       {tid:20s} OFF={a:4.2f}  ON={b:4.2f}  PL={c:4.2f}{mark}")
    print(f"       mean: OFF={mean(sc_off)}  ON={mean(sc_on)}  PL={mean(sc_pl)}")

    moved = [t for t in ids if sc_off.get(t) != sc_on.get(t)]
    q1 = bool(moved)
    say("Q1/对照成立", q1,
        "闸必须真的改变过交付物（否则 Δ=0 说明的不是『没效果』而是『没靶子』）",
        f"变了 {len(moved)} 题：{moved or '无'}")
    say("Q2/位移可定位", len(moved) == 1,
        "只报均值不算读数；这一次位移落在**一道题**上",
        f"Δ(ON−OFF) = {[round(sc_on[t] - sc_off[t], 2) for t in ids]}"
        f" 均值 {round(mean(sc_on) - mean(sc_off), 4):+}")

    # ── Q3：三臂里最要紧的一条 ──────────────────────────────────────────
    #: ⚠️ 这一条**不是** pass/fail 断言，而是**判读分支** —— 三种结果都可能是真的，
    #:    所以它不能写成 `say(...)`。第一版写成了 `say(..., pl_delta < on_delta or True, ...)`，
    #:    那是**恒真断言**：它永远绿、永远不携带信息，正是本项目反复防的那类东西。
    same = _canon(_load("sc_on.jsonl")) == _canon(_load("sc_placebo.jsonl"))
    pl_delta = round(mean(sc_pl) - mean(sc_off), 4)
    on_delta = round(mean(sc_on) - mean(sc_off), 4)
    print("\n二、安慰剂（内容无关的自检）")
    print(f"       ON − OFF = {on_delta:+.4f}   PL − OFF = {pl_delta:+.4f}")
    print(f"       ON 与 PL 去掉 `elapsed_s` 后逐字相同 = {same}")
    print("       ⇒ 判读见下（Q3 是分支，不是断言）")

    print("\n三、跨题集复核（bench/heldout，7 题）")
    hids = sorted(set(ho_off) | set(ho_on))
    for tid in hids:
        a, b = ho_off.get(tid), ho_on.get(tid)
        print(f"       {tid:20s} OFF={a:4.2f}  ON={b:4.2f}"
              + ("" if a == b else "   <<<"))
    print(f"       mean: OFF={mean(ho_off)}  ON={mean(ho_on)}")
    hmoved = [t for t in hids if ho_off.get(t) != ho_on.get(t)]
    say("Q4/没有复现就写没有复现", not hmoved,
        "heldout 上 7 题**逐题相同** ⇒ 效应没有跨题集复现，必须原样写",
        f"变了 {len(hmoved)} 题：{hmoved or '无'}")

    # ── Q6：这一对数字**不是新证据** ────────────────────────────────────
    #: 第十八轮（假记忆五臂）的 E0 = 空记忆、E3 = 真累积记忆就落在这里。
    #: 本轮 OFF/ON 与它们**逐题逐位相同**（字典相等，不是"接近"）。
    #: ⇒ 这不是巧合，是**题集的指纹**：5 题里只有 z03 一道有位移空间
    #:   （z01 恒 0.8、z02 恒 0.0、z04/z05 恒 1.0），所以任何"让它重试一次"
    #:   的机制都会拿到一模一样的 +0.2000。
    #: ⛔ 所以"两种完全不同的机制拿到同一对数字"必须读成**题集太窄**，
    #:    不许读成"互相印证"—— 后者是拿一个无法证伪的巧合当证据。
    #:    真正的交叉检验在 heldout（Q4，7 题，Δ=0）。
    fm = V1 / "bench" / "evidence" / "fakemem_s18"
    e0 = _per_task(_load_at(fm / "E0.runs.jsonl"))
    e3 = _per_task(_load_at(fm / "E3.runs.jsonl"))
    same_e0 = (e0 == sc_off) and bool(e0)
    same_e3 = (e3 == sc_on) and bool(e3)
    print("\n四、与第十八轮（假记忆五臂）的逐题对账")
    print(f"       OFF == E0（空记忆）= {same_e0}   ON == E3（真记忆）= {same_e3}")
    for tid in ids:
        print(f"       {tid:20s} 本轮 OFF={sc_off.get(tid):4.2f} E0={e0.get(tid)}"
              f"  |  本轮 ON={sc_on.get(tid):4.2f} E3={e3.get(tid)}")
    movable = [t for t in ids if len({sc_off.get(t), sc_on.get(t)}) > 1]
    say("Q6/同一道题的第二次 0→1", same_e0 and same_e3,
        "两条完全不同的机制给出**逐位相同**的一对数字 ⇒ 这是题集太窄的指纹，"
        "⛔ 不是互相印证",
        f"可动题={movable or '无'}（该题集里其余 4 题的分数在五个臂上完全不变）")

    # ── 判读（唯一实现） ────────────────────────────────────────────────
    if not q1:
        verdict, tag = "Q1：闸一次都没改变交付物 ⇒ UNKNOWN，不认领", "Q1"
    elif same:
        verdict, tag = ("Q3：**内容无关的安慰剂给出逐字相同的读数** ⇒ 收益来自"
                        "『打回重写』这个动作，不是判据的诊断信息 ⇒ **§17-6 仍是 0**"), "Q3"
    elif pl_delta < on_delta:
        verdict, tag = "Q3b：诊断信息确实有用（PL 明显低于 ON）", "Q3b"
    else:
        verdict, tag = "Q3c：PL 高于 ON ⇒ 装置有问题，不许挑好的那个读", "Q3c"

    out = {
        "rules": [list(r) for r in RULES],
        "sc": {"off": sc_off, "on": sc_on, "placebo": sc_pl,
               "mean": {"off": mean(sc_off), "on": mean(sc_on), "pl": mean(sc_pl)},
               "moved": moved},
        "ho": {"off": ho_off, "on": ho_on,
               "mean": {"off": mean(ho_off), "on": mean(ho_on)}, "moved": hmoved},
        "on_equals_placebo_ignore_elapsed": same,
        "per_task_equals_fakemem_round": {"off_eq_e0": same_e0, "on_eq_e3": same_e3,
                                          "movable": movable},
        "verdict": verdict, "tag": tag,
        "claim": ("结构：`finish` 第一次可以失败，失败之后收尾兜底那条路才第一次被走到；"
                  "⛔ 诊断信息本身没有可测贡献（安慰剂同分且逐字相同）"),
        "unknowns": [
            "位移只落在 1 道题（z03）：1/5 = 0.20，跨 12 题摊薄后 = 0.083 —— 不是『提升 20%』",
            "每臂单次；T=0 保证逐位可复现（第 1 条已实测），但不保证因果（第十七轮教训）",
            "heldout 7 题 Δ=0 ⇒ 效应没有跨题集复现，不许外推",
            "真正承重的机制是『允许失败 ⇒ 收尾兜底被走到』，而不是『校验』这个名字；"
            "把它读成『校验让模型变聪明』是越界",
        ],
    }

    print("\n" + "=" * 74)
    print(f"  {tag}  ⇒ 判读：{verdict}")
    print("=" * 74)

    if args.write:
        EV.mkdir(parents=True, exist_ok=True)
        vp = EV / "verdict.json"
        vp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  已固化到 {vp}")
        back = json.loads(vp.read_text(encoding="utf-8"))
        say("Q5/固化自检", back.get("tag") == tag,
            "写完读回来核对", f"回读 tag={back.get('tag')!r}")
    elif (EV / "verdict.json").exists():
        fz = json.loads((EV / "verdict.json").read_text(encoding="utf-8"))
        say("Q5/重算一致", fz.get("tag") == tag,
            "落盘判定 = 现在重算的判定", f"落盘={fz.get('tag')!r} 重算={tag!r}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
