#!/usr/bin/env python3
"""路线①·外部能力对照的**唯一判读实现**（第二十三轮）。

规则在 DESIGN §16.22 ③ **跑之前**就写死了。本文件只做三件事：
读冻结证据 → 按那五条规则判 → 把判定冻成 `verdict.json`。

⛔ 为什么判读只有一份实现：第十八轮的教训 —— 脚本里内嵌一份、探针里再写一份，
   两份字符串其实不同（少两个 `**`），P5 当场转红。判据只许有一处。

用法
    python3 bench/probe_external_real.py --rules    # 只念事前规则（不读证据）
    python3 bench/probe_external_real.py            # 读证据并判读
    python3 bench/probe_external_real.py --write    # 判读并冻结 verdict.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

V1 = pathlib.Path(__file__).resolve().parent.parent
#: 证据目录。`--dir` 可以指向别的档位（2x）而不覆盖 1x 的证据。
EV = V1 / "bench" / "evidence" / "external_s23"
if str(V1) not in sys.path:
    sys.path.insert(0, str(V1))

#: 事前登记（与 DESIGN §16.22 ③ 逐字对应）。⛔ 看到分数之后不许改这里。
RULES: tuple[tuple[str, str], ...] = (
    ("R1", "ON − OFF ≤ 0 ⇒ 负结论：外部能力在这批题上没有增益。停止，不做归属拆分"),
    ("R2", "0 < Δ < 0.05 ⇒ UNKNOWN：不认领（低于分辨率与安慰剂量级）"),
    ("R3", "Δ ≥ 0.05 但只有 struct_convert 成立、heldout 不成立 ⇒ 窄题集效应，不得推广"),
    ("R4", "Δ ≥ 0.05 且两个题集同向 ⇒ 成立，但仍须做 k=1 归属臂才可说『执行筛选起作用』"),
    ("R5", "任何情况下：不许改判据、不许换题集、不许事后调阈值；T=0 只保证可复现，不保证可推广"),
)

#: 分辨率：一题值多少分（题数决定的，不在这里拍脑袋，用实际 n 算）。
THRESH = 0.05

#: 两臂**允许**不同的口径字段 —— 这就是"声明的自变量"。
#:   · `tool_catalog_sha`：外部 agent 在不在工具目录里（提示词因此不同）
#:   · `limits`：里面装着 `external_answer_tool` 这个开关
#: ⛔ 除这两项外任何一个字段不同 ⇒ 两臂不是同一个实验，判读强制 UNKNOWN。
DECLARED_DIFF: tuple[str, ...] = ("tool_catalog_sha", "limits")


def _caliber_of(name: str) -> dict | None:
    """读一个臂落盘的 `caliber`（**最后一条**记录——口径在整轮里恒定）。

    ⚠️ 与 `_kex` 的"必须求和"不同：`caliber` 是**条件**不是**读数**，
       每条记录都一样，取最后一条是对的。两者不可互相套用（这是两种东西）。
    """
    p = EV / f"{name}.jsonl"
    if not p.exists():
        return None
    cal: dict | None = None
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cur = json.loads(line).get("caliber")
            if isinstance(cur, dict):
                cal = cur
    return cal


def _kex(name: str) -> dict:
    """把一个臂**全部**记录的 `kernel_external` 加总。

    ⛔ 第三只脚踩进同一个坑：`--suite-dir` 每个领域写一条记录，而累加器
       是**在每条记录里各自从零开始的**（实测 ho_on: 第一条 calls=3、第二条
       calls=4，真值 7）。旧实现取 `rows[-1]` ⇒ 冻结的 verdict.json 里
       `external_on_heldout` 少报了一半（calls 4 vs 7、ok 1 vs 2）。
       这不是"小数点误差"，这是**把一次跑了 7 次的实验写成跑了 4 次**。
    ⚠️ 与 `run_external_control.sh` 里"摊平全部记录"是同一条纪律；
       契约 AF6 用两行 fixture 钉死本函数必须求和。
    """
    p = EV / f"{name}.jsonl"
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    add = ("calls", "ok", "err", "used_as_answer", "chars",
           "samples", "tests", "fallback", "discriminated",
           "skipped_no_budget", "exec_skipped")
    out: dict = {k: 0 for k in add}
    out["records"] = len(rows)
    reasons: list[str] = []
    for r in rows:
        ex = r.get("kernel_external") or {}
        for k in add:
            out[k] += int(ex.get(k) or 0)
        if ex.get("tool"):
            out["tool"] = ex["tool"]
        if ex.get("last_error") and not out.get("last_error"):
            out["last_error"] = ex["last_error"]
        for w in (ex.get("err_reasons") or []):
            if w not in reasons:
                reasons.append(w)
    if reasons:
        out["err_reasons"] = reasons
    #: 成功率：**不是**判据，只是"它到底有多少次真交付了"的一句话
    out["ok_rate"] = (round(out["ok"] / out["calls"], 3) if out["calls"] else None)
    return out


def _caliber_check(pairs: tuple[tuple[str, str, str], ...]) -> dict:
    """核验每一对臂"只差声明的变量"。

    返回值三态：`pass` / `fail` / `unverifiable`（旧证据缺字段）。
    ⛔ `unverifiable` **不许**被读成 pass（"没核验"与"核验通过"是两件事）。
    """
    from mw.bench import caliber_from_dict       # 唯一反序列化实现

    out: dict = {"status": "pass", "pairs": {}, "declared": list(DECLARED_DIFF)}
    fails: list[str] = []
    for setname, a, b in pairs:
        ca, cb = _caliber_of(a), _caliber_of(b)
        if ca is None or cb is None:
            out["status"] = "unverifiable"
            out["pairs"][setname] = {"why": "证据里没有 caliber 字段"}
            continue
        if "tool_catalog_sha" not in ca or "tool_catalog_sha" not in cb:
            out["status"] = "unverifiable"
            out["pairs"][setname] = {
                "why": "caliber 里缺 tool_catalog_sha（第二十三轮之前的证据）"}
            continue
        unexpected, declared = caliber_from_dict(ca).diff_except(
            caliber_from_dict(cb), DECLARED_DIFF)
        out["pairs"][setname] = {"unexpected": unexpected, "declared": declared}
        if unexpected:
            fails.append(f"{setname}: {unexpected}")
    if fails:
        out["status"] = "fail"
        out["unexpected"] = fails
    return out


def _load(name: str) -> dict:
    """读一个臂：返回 {题号: 分数} 与 mean。缺文件 ⇒ 空（由判读显式报错）。

    ⚠️ **缺题的题不会出现在 `per_task` 里**：`bench.py` 在
    `skipped_no_answer` 分支是 `continue`，在 append 之前就跳过了。
    ⇒ 本函数拿到的字典可能比题集短，调用方**不许**用 `.get(i, 0)` 补零。
    """
    p = EV / f"{name}.jsonl"
    if not p.exists():
        return {"per_task": {}, "mean": None, "n": 0, "missing": str(p), "reps": {}}
    scores: dict[str, float] = {}
    reps: dict[str, list[int]] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        for t in rec["report"]["per_task"]:
            tid = str(t["id"])
            scores[tid] = float(t["score"])
            reps.setdefault(tid, []).append(int(t.get("rep", 1)))
    mean = round(sum(scores.values()) / len(scores), 4) if scores else None
    return {"per_task": scores, "mean": mean, "n": len(scores),
            "missing": "", "reps": reps}


#: ⛔ **判读口径**（第二十三轮修）：只允许拿**两臂都量到的题**做配对。
#:    旧实现对"某臂缺的题"按 0 分补 ⇒ 把"没量到"读成"得 0 分"，
#:    违反契约③（缺失不得被读作否定）。修的方向会让 Δ **变大**，对我有利，
#:    所以必须双口径并列公布（见 `both`），且两口径判读不一致时强制 UNKNOWN。
def _arm_table(off: dict, on: dict) -> dict:
    all_ids = sorted(set(off["per_task"]) | set(on["per_task"]))
    #: 配对口径：两臂都有分数的题
    ids = [i for i in all_ids if i in off["per_task"] and i in on["per_task"]]
    #: 某臂缺的题（**不是 0 分**，是没量到）
    dropped = {i: {"off": off["per_task"].get(i), "on": on["per_task"].get(i)}
               for i in all_ids if i not in ids}
    moved = [i for i in ids if off["per_task"][i] != on["per_task"][i]]
    delta = (round(sum(on["per_task"][i] - off["per_task"][i] for i in ids) / len(ids), 4)
             if ids else None)
    #: 旧口径（缺题补 0）——**只为对照而保留**，不作为判读依据
    legacy = (round(sum(on["per_task"].get(i, 0) - off["per_task"].get(i, 0)
                        for i in all_ids) / len(all_ids), 4) if all_ids else None)
    return {"ids": ids, "all_ids": all_ids, "moved": moved, "delta": delta,
            "delta_legacy_fillzero": legacy, "dropped": dropped, "n_paired": len(ids),
            "moved_detail": {i: [off["per_task"][i], on["per_task"][i]] for i in moved}}



def _judge(ds: float | None, dh: float | None) -> tuple[str, str]:
    """**唯一**的判据实现。两处（配对 / 旧口径）都调它，不许各写一份。

    第十八轮的教训：判读写两份，字符串其实不同，P5 当场转红 ⇒ 判据只许有一处。
    """
    both_up = (ds or 0) >= THRESH and (dh or 0) >= THRESH
    any_up = (ds or 0) >= THRESH or (dh or 0) >= THRESH
    if (ds or 0) <= 0 and (dh or 0) <= 0:
        return "R1", "负结论：外部能力在这批题上没有增益"
    if not any_up:
        return "R2", "UNKNOWN：Δ 低于分辨率阈值，不认领"
    if not both_up:
        return "R3", "窄题集效应：只在单侧成立，不得推广"
    return "R4", "成立，但需 k=1 归属臂才能说『执行筛选起作用』"


def _verdict() -> dict:
    sc_off, sc_on = _load("sc_off"), _load("sc_on")
    ho_off, ho_on = _load("ho_off"), _load("ho_on")
    missing = [x for x in (sc_off, sc_on, ho_off, ho_on) if x["missing"]]
    if missing:
        return {"tag": "UNKNOWN", "why": "证据缺失", "missing": [m["missing"] for m in missing]}

    sc = _arm_table(sc_off, sc_on)
    ho = _arm_table(ho_off, ho_on)

    # ── 两臂口径核验：**只许差在声明的变量上**（第二十四轮）─────────────
    # 承诺"两臂只差外部能力"从前只是散文；现在它是一条可核验的断言。
    # 缺 `tool_catalog_sha` 的旧证据 ⇒ 无法核验（不假装通过，也不判红）。
    decl = _caliber_check((("struct_convert", "sc_off", "sc_on"),
                           ("heldout", "ho_off", "ho_on")))

    # ── 双口径并列（第二十三轮修）───────────────────────────────────────
    # 主口径：配对（只用两臂都量到的题）。旧口径：缺题补 0（**违反契约③**，
    # 留作对照，证明"改口径"本身没有把结论推向有利方向）。
    tag_p, why_p = _judge(sc["delta"], ho["delta"])
    tag_l, why_l = _judge(sc["delta_legacy_fillzero"], ho["delta_legacy_fillzero"])
    dropped_any = bool(sc["dropped"] or ho["dropped"])
    if dropped_any and tag_p != tag_l:
        tag, verdict = "R2", (f"两个合法口径判读不一致（配对={tag_p} / 补零={tag_l}）"
                              f" ⇒ UNKNOWN：分辨率不足以在两种口径下同号")
        conflict = True
    else:
        tag, verdict = tag_p, why_p
        conflict = False

    # 口径核验：**意外的**差异 ⇒ 两臂不是同一个实验，任何 Δ 都不成立。
    if decl["status"] == "fail":
        tag, verdict = "UNKNOWN", (f"两臂口径不止声明的变量不同（{decl['unexpected']}）"
                                   f" ⇒ 不是同一个实验，Δ 不成立")
        conflict = True

    # 外部能力读数（落盘，不是日志）：调用数 / 采用数 / 筛选是否真的区分
    # ── 两臂口径核验见上（`decl`）；此处只留读数 ────────────────────────
    kex_on_sc, kex_on_ho = _kex("sc_on"), _kex("ho_on")
    kex_off = _kex("sc_off")

    return {
        "tag": tag,
        "verdict": verdict,
        "caliber_conflict": conflict,
        "caliber_check": decl,
        "judge_paired": {"tag": tag_p, "why": why_p},
        "judge_fillzero": {"tag": tag_l, "why": why_l},
        "arms": {"sc": {"off": sc_off["mean"], "on": sc_on["mean"],
                        "n_off": sc_off["n"], "n_on": sc_on["n"],
                        "n_paired": sc["n_paired"]},
                 "ho": {"off": ho_off["mean"], "on": ho_on["mean"],
                        "n_off": ho_off["n"], "n_on": ho_on["n"],
                        "n_paired": ho["n_paired"]}},
        #: ⚠️ 两臂 n 不同 ⇒ 两个 mean **不是同一把 n 的尺子**，不可直接比。
        "means_comparable": {"sc": sc_off["n"] == sc_on["n"],
                             "ho": ho_off["n"] == ho_on["n"]},
        "delta": {"struct_convert": sc["delta"], "heldout": ho["delta"]},
        "delta_legacy_fillzero": {"struct_convert": sc["delta_legacy_fillzero"],
                                  "heldout": ho["delta_legacy_fillzero"]},
        #: 某臂**没量到**的题（不是 0 分）。这些题被排除在配对之外。
        "dropped": {"struct_convert": sc["dropped"], "heldout": ho["dropped"]},
        "moved": {"struct_convert": sc["moved"], "heldout": ho["moved"]},
        "moved_detail": {"struct_convert": sc["moved_detail"],
                         "heldout": ho["moved_detail"]},
        "external_on_struct": kex_on_sc,
        "external_on_heldout": kex_on_ho,
        "external_off_calls": (kex_off or {}).get("calls"),
        "threshold": THRESH,
        "note": "T=0 逐位可复现只保证『这批题上可复现』，不保证可推广；"
                "外部能力的 assert 是模型自写的，只能排序不能判分。",
    }


def main() -> int:
    global EV
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", action="store_true", help="只念事前规则")
    ap.add_argument("--write", action="store_true", help="冻结 verdict.json")
    ap.add_argument("--dir", default="",
                    help="证据目录（默认 bench/evidence/external_s23；2x 档用它指到别处）")
    args = ap.parse_args()

    if args.dir:
        EV = pathlib.Path(args.dir).expanduser().resolve()

    if args.rules:
        print("事前登记（DESIGN §16.22 ③ / §16.23 ②，跑之前写死）")
        for k, v in RULES:
            print(f"  {k}: {v}")
        return 0

    d = _verdict()
    print("=" * 74)
    print("路线①·外部能力对照 —— 判读（唯一实现）")
    print("=" * 74)
    if d.get("tag") == "UNKNOWN" and d.get("missing"):
        print("❌ 证据缺失：")
        for m in d["missing"]:
            print(f"   {m}")
        return 2
    for k, v in (d["arms"] or {}).items():
        same = "同 n" if d["means_comparable"][k] else "⚠️ n 不同 ⇒ 两个 mean 不可直接比"
        print(f"  {k}: OFF={v['off']} (n={v['n_off']})  ON={v['on']} (n={v['n_on']})"
              f"  配对 n={v['n_paired']}  [{same}]")
    print(f"  Δ(配对，主口径): {d['delta']}")
    print(f"  Δ(缺题补零，仅对照): {d['delta_legacy_fillzero']}")
    for setname, drops in (d["dropped"] or {}).items():
        if drops:
            print(f"  ⚠️ {setname} 有 {len(drops)} 题某臂没量到（**不是 0 分**，已排除在配对之外）:")
            for sid, pair in drops.items():
                print(f"      {sid}: OFF={pair['off']}  ON={pair['on']}")
    print(f"  位移: {d['moved']}")
    for setname, pair in (d["moved_detail"] or {}).items():
        for sid, m in pair.items():
            print(f"    {setname} {sid}: {m[0]} → {m[1]}")
    print(f"  外部读数(ON/struct): {json.dumps(d['external_on_struct'], ensure_ascii=False)}")
    print(f"  外部读数(ON/heldout): {json.dumps(d['external_on_heldout'], ensure_ascii=False)}")
    print(f"  外部读数(OFF/struct): calls={d['external_off_calls']}（必须是 0 或缺失）")
    print(f"  两口径判读: 配对={d['judge_paired']['tag']} / "
          f"补零={d['judge_fillzero']['tag']}"
          f"{'  ⚠️ 冲突 ⇒ 强制 UNKNOWN' if d.get('caliber_conflict') else ''}")
    _cc = d.get("caliber_check") or {}
    if _cc:
        _st = _cc.get("status")
        _mark = {"pass": "✅", "fail": "❌", "unverifiable": "⚠️"}.get(_st, "?")
        print(f"  {_mark} 口径核验: {_st}"
              f"（声明允许不同: {_cc.get('declared')}）")
        for _s, _v in (_cc.get("pairs") or {}).items():
            print(f"      {_s}: {json.dumps(_v, ensure_ascii=False)}")
    print()
    print(f"  【{d['tag']}】{d['verdict']}")
    if args.write:
        EV.mkdir(parents=True, exist_ok=True)
        (EV / "verdict.json").write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  已冻结 {EV / 'verdict.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
