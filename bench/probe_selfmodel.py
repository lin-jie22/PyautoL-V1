#!/usr/bin/env python3
"""自我模型的判据台 —— **跨会话预测校准** + **族预测力闸**。

## 它在替换什么

旧判据（代理量）：`selfmodel.consulted × influenced`。
它只能证明"自我模型被咨询过" —— 而它**总会**被咨询，所以这条永远绿。
§16.7 判「自我模型」为**判据不成立**，就是这个原因。

新判据（行为锚）：

    用历史会话的分数，预测未来会话的通过率。
    预测误差必须**显著低于**一个不区分能力族的对照基线。
    低不了 ⇒ 「分族」这件事只是在给噪声起名字。

这条是可证伪的，并且**有两个方向都能红**：
  · 族标签与通过率无关（打乱标签）⇒ 优势必须消失；
  · 口径闸被撤掉（混入异口径行）⇒ 预测必须变差。

## 本轮新增：闸这一格

判据测的是"尺子能不能分辨"，闸管的是"测不出来时**不许行动**"。
上一轮的真实读数 Δ=−0.015（ABSENT）暴露了 `advise()` 的老门槛有洞：
它只看样本数够不够，于是**在噪声上给建议**。所以这里多打一行读数 ——
同一个账本上，`advise()` 到底给不给建议。

⚠️ 算法**唯一实现在 `mw/metacog.py`**（`measure_family_power`），本文件
只是"选数据 + 构造反证 + 念读数"。理由写在 metacog 那边的分隔注释里：
闸用 A 算法、报告用 B 算法，两边漂一次就变成"闸关着但报告说成立"。

## 用法

    python3 bench/probe_selfmodel.py                  # 合成账本（证明判据有分辨力）
    python3 bench/probe_selfmodel.py --real PATH      # 真实账本（给出真实结论）

⚠️ 两者的地位**不同**，不许混着读：合成账本证明的是**尺子**能分辨，
真实账本才是在问 **V1 有没有族信号**。混淆二者就是又一次"用校准材料冒充结论"。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mw.ledger import ScoreLedger, signature_of                    # noqa: E402
from mw.metacog import (MIN_POWER_SESSIONS, SelfModel,             # noqa: E402
                        load_evidence, measure_family_power)

#: 合成账本的能力族（与 bench/tasks 下的真实领域同名，避免"随便编的族"）
FAMILIES = ("numeric_logic", "text_ops", "struct_convert", "coding_small", "explain")

#: 各族**真实**通过率的生成参数。刻意在 0.1~0.9 之间拉开 ——
#: 若族之间没有真实差异，本探针就该判"不成立"，而不是想办法让它成立。
TRUE_RATES = {
    "numeric_logic": 0.90,
    "text_ops": 0.70,
    "struct_convert": 0.50,
    "coding_small": 0.30,
    "explain": 0.10,
}

N_SESSIONS = 20          # 每个族 20 个历史会话


def _u01(seed: str) -> float:
    """确定性伪随机 [0,1)。**不用 `random`**：探针的结论必须逐次可复现，
    否则"这次通过了"和"上次数值不同"会混成同一件事（V5 的老病）。"""
    h = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") / float(1 << 64)


def build_synthetic_rows(sig: str = "synth", *, rates: dict | None = None,
                         n_sessions: int = N_SESSIONS) -> list[dict]:
    """造一份**族信号真实存在**的账本。

    ⚠️ 它是**校准材料**，不是结论：它证明这把尺子能分辨"有族信号"和"没有"，
    不能证明 V1 自己有族信号（那是 `--real` 的事）。
    """
    rates = rates or TRUE_RATES
    rows = []
    for fam in FAMILIES:
        p = rates.get(fam, 0.5)
        for s in range(n_sessions):
            passed = _u01(f"{fam}|{sig}|{s}") < p
            rows.append({"session": s, "family": fam, "passed": passed,
                         "score": 1.0 if passed else 0.0, "caliber_sig": sig,
                         "task_id": f"{fam}-{s}"})
    return rows


def build_null_rows(sig: str = "null", *, n_sessions: int = N_SESSIONS,
                    rate: float = 0.5) -> list[dict]:
    """**反证组**：一份"通过率与族**无关**"的账本（各族真实率完全相同）。

    ⚠️ 它替换的是旧的"轮转族标签"做法 —— 后者只给族改名，若未来行也跟着
    改名，信号**一点没动**；只改历史则恒等于"完全错配"。两种都不是
    "族标签与通过率无关"的正确表达。真正的零假设是：**族之间本无差异**。

    `rate=0.5` 是刻意的：二值变量在 0.5 处方差最大，最容易把噪声读成规律。
    若判据在这份数据上还能绿，它就是在给随机起伏起名字。
    """
    rows = []
    for fam in FAMILIES:
        for s in range(n_sessions):
            passed = _u01(f"{fam}|{sig}|{s}") < rate
            rows.append({"session": s, "family": fam, "passed": passed,
                         "score": 1.0 if passed else 0.0, "caliber_sig": sig,
                         "task_id": f"{fam}-{s}"})
    return rows


def probe_calibration(rows: list[dict], *,
                      history_ratio: float | None = None) -> dict:
    """一次完整的判据读数。**委托** `measure_family_power`（唯一实现）。

    返回值带上 `families_in_data` 与 `sessions_in_data`：
    判据的三态离开这两个数无法解释（ABSENT 与 UNKNOWN 的分界就在它们身上）。
    """
    kw = {} if history_ratio is None else {"history_ratio": history_ratio}
    pw = measure_family_power(rows, **kw)
    d = pw.as_dict()
    d["families_in_data"] = len({str(r.get("family")) for r in rows})
    d["sessions_in_data"] = len({r.get("session") for r in rows
                                 if r.get("session") is not None})
    return d


def probe_caliber_gate() -> dict:
    """**口径闸是不是承重的**：混入异口径 ⇒ 预测必须变差。

    构造：两个口径下同一批族的通过率**正好相反**。
      · 闸在（只收同口径历史）⇒ 预测准；
      · 闸撤（两个口径一起收）⇒ 每个族的率都被拉回 0.5 ⇒ 预测塌回基线。
    若撤了闸误差不变，说明那道闸是装饰 —— 而装饰的闸就是 V5 的死法。
    """
    sig_a = signature_of({"scorer_sha": "A", "suite_sha": "A"})
    sig_b = signature_of({"scorer_sha": "B", "suite_sha": "B"})
    rows_a = build_synthetic_rows(sig_a)
    rows_b = build_synthetic_rows(sig_b, rates={f: 1.0 - TRUE_RATES[f] for f in FAMILIES})

    gated = measure_family_power(rows_a)
    mixed = measure_family_power(rows_a + rows_b)
    loss = round((mixed.mae_model or 0.0) - (gated.mae_model or 0.0), 4)
    return {"gated": gated.as_dict(), "mixed": mixed.as_dict(), "loss": loss,
            "state": "PRESENT" if loss > 0 else "ABSENT"}


def probe_gate(rows: list[dict], *, label: str) -> dict:
    """**闸这一格**：同一份账本上，`advise()` 到底给不给建议。

    念两个数：`family_power`（尺子读数）与 `advice.confident`（行动开关）。
    两者不是一回事，必须**同时**念 —— 只看前者会以为"测到了就会行动"，
    只看后者看不出它是因为没测到还是因为测到了但没优势。
    """
    sm = SelfModel(load_evidence(rows))
    pw = sm.family_power()
    fams = sorted(sm.profile())
    advs = {f: sm.advise(family=f) for f in fams} if fams else {}
    return {
        "label": label,
        "power": pw.as_dict(),
        "advice": {f: {"confident": a.confident, "basis": a.basis}
                   for f, a in advs.items()},
        "any_confident": any(a.confident for a in advs.values()),
        "influenced": sm.influenced,
        "consulted": sm.consulted,
        "global_rate": sm.global_rate(),
    }


def probe_real(path: Path) -> dict:
    """真实账本的读数。**样本不够就明说 UNKNOWN**，不许用合成数据凑。"""
    led = ScoreLedger(path)
    rows = led.load()
    sessions = {int(r["session"]) for r in rows if r.get("session") is not None}
    families = {str(r.get("family")) for r in rows}
    if len(rows) < 8 or len(sessions) < MIN_POWER_SESSIONS or len(families) < 2:
        return {"state": "UNKNOWN", "rows": len(rows),
                "sessions": len(sessions), "families": len(families),
                "why": (f"账本不足（行={len(rows)} 会话={len(sessions)} "
                        f"族={len(families)}）；需要 行≥8 "
                        f"会话≥{MIN_POWER_SESSIONS} 族≥2")}
    sigs: dict[str, int] = {}
    for r in rows:
        k = str(r.get("caliber_sig"))
        sigs[k] = sigs.get(k, 0) + 1
    top = max(sigs, key=sigs.get) if sigs else ""
    sub = [r for r in rows if str(r.get("caliber_sig")) == top]
    res = probe_calibration(sub)
    res["rows"] = len(rows)
    res["sessions"] = len(sessions)
    res["families"] = len(families)
    res["calibers"] = sigs
    res["used_sig"] = top
    res["gate"] = probe_gate(sub, label=str(path.name))
    return res


def _gate_line(gd: dict, indent: str = "  ") -> None:
    pw = gd["power"]
    print(f"{indent}{gd['label']}：Δ={pw['delta']} 阈值={pw['margin']} ⇒ {pw['state']}")
    print(f"{indent}  闸={'开' if gd['any_confident'] else '关'}"
          f"  咨询 {gd['consulted']} 次 / 实际影响 {gd['influenced']} 次")
    for f, a in sorted(gd["advice"].items()):
        if a["confident"]:
            print(f"{indent}    ✅ {f}：{a['basis']}")
    if not gd["any_confident"]:
        some = next(iter(gd["advice"].values()), None)
        if some:
            print(f"{indent}    ⛔ {some['basis']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="自我模型跨会话判据探针")
    ap.add_argument("--real", metavar="PATH", default="",
                    help="用真实账本（默认 state/score_ledger.jsonl）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    ok = True
    out: dict = {}

    syn = build_synthetic_rows()
    r_syn = probe_calibration(syn)
    out["synthetic"] = r_syn
    ok &= r_syn["state"] == "PRESENT"

    r_gate = probe_caliber_gate()
    out["caliber_gate"] = r_gate
    ok &= r_gate["state"] == "PRESENT"

    # ── 反证组：族间本无差异 ⇒ 判据不许绿 ────────────────────────────
    r_null = probe_calibration(build_null_rows())
    out["null"] = r_null
    ok &= r_null["state"] != "PRESENT"

    # ── 闸这一格 ──────────────────────────────────────────────────────
    # 合成账本上闸必须**开**（有族信号却不行动 = 自我模型是装饰）；
    # 真实账本上闸开不开由数据决定，**都算通过**（真实读数为负不是缺陷）。
    out["gate_synthetic"] = probe_gate(syn, label="合成（族信号真实存在）")
    ok &= out["gate_synthetic"]["any_confident"] is True

    if args.real or Path(ROOT / "state" / "score_ledger.jsonl").exists():
        p = Path(args.real) if args.real else ROOT / "state" / "score_ledger.jsonl"
        r_real = probe_real(p)
        out["real"] = r_real
        # ⚠️ 真实账本判 UNKNOWN / ABSENT **都不算失败** —— 那是诚实结论。
        #    把"没量到"或"测出来没有"记成"失败"，会逼着下一轮去凑数据（V5 的病）。

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    m = r_syn
    print("=" * 74)
    print("自我模型判据 —— 跨会话预测校准（合成账本：证明判据有分辨力）")
    print("=" * 74)
    print(f"  历史 {m['n_history']} 条 / {m['families_in_data']} 族   "
          f"未来 {m['n_future']} 条 / 会话 {m['sessions_in_data']}")
    print(f"  自我模型预测  MAE = {m['mae_model']}")
    print(f"  对照基线预测  MAE = {m['mae_base']}   （常数，不区分族）")
    print(f"  优势 Δ = {m['delta']}   噪声下限(std={m['noise_floor']}"
          f" / mean|Δ|={m['noise_mean']}，{m['n_controls']} 次置换检验)")
    print(f"  判定阈值 = max(下限, 2×std, mean|Δ|) = {m['margin']}")
    print(f"  ⇒ 判据{'成立' if m['state'] == 'PRESENT' else '不成立'}（{m['state']}）")
    print()
    g, mx = r_gate["gated"], r_gate["mixed"]
    print("口径闸是不是承重的（两个口径下族通过率**相反**）：")
    print(f"  只收同口径  MAE = {g['mae_model']}")
    print(f"  混入异口径  MAE = {mx['mae_model']}   劣化 = {r_gate['loss']}")
    print(f"  ⇒ {'闸是承重的' if r_gate['state'] == 'PRESENT' else '撤了闸也没差（闸是装饰）'}")
    print()
    print("反证组（各族真实通过率**完全相同**，0.5 处方差最大）：")
    print(f"  自我模型 MAE = {r_null['mae_model']}   Δ = {r_null['delta']}"
          f"   阈值 = {r_null['margin']}   ⇒ {r_null['state']}")
    print(f"  ⇒ {'✅ 无差异时不给假阳性' if r_null['state'] != 'PRESENT' else '❌ 把随机起伏读成了规律'}")
    print()
    print("族预测力闸（判据测的是尺子，闸管的是「测不出来不许行动」）：")
    _gate_line(out["gate_synthetic"])
    print()
    if "real" in out:
        rr = out["real"]
        print("真实账本：")
        if rr.get("state") == "UNKNOWN" and "why" in rr:
            print(f"  ⚠️ {rr['why']} —— 这是**诚实结论**，不是失败")
        else:
            print(f"  行={rr['rows']} 会话={rr['sessions']} 族={rr['families']}"
                  f" 口径数={len(rr['calibers'])}（取最大口径 {rr['used_sig']}）")
            print(f"  自我模型 MAE={rr['mae_model']} 基线 MAE={rr['mae_base']}"
                  f" Δ={rr['delta']} ⇒ {rr['state']}")
            if rr.get("reason"):
                print(f"  {rr['reason']}")
            if "gate" in rr:
                _gate_line(rr["gate"])
    else:
        print("真实账本：不存在（`state/score_ledger.jsonl` 还没被写入过）")
    print()
    print("=" * 74)
    print("结论：", "✅ 判据成立（自我模型的预测优于不区分族的基线，" \
                    "且撤掉族/撤掉口径闸都会转红）" if ok else "❌ 判据不成立")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
