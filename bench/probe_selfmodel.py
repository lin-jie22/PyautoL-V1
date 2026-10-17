#!/usr/bin/env python3
"""自我模型的判据台 —— **跨会话预测校准**。

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

## 为什么判据是「预测」而不是「描述」

`advise()` 说"这个族弱，多给候选" —— 那是决策，事后永远可以自圆其说。
`predict()` 说"这个族下次大概拿 0.2" —— 那是一个数字，会被下一次外部分数打脸。
**只有能被打脸的断言才是断言。**

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

from mw.ledger import ScoreLedger, signature_of          # noqa: E402
from mw.metacog import MIN_SAMPLES, SelfModel, load_evidence  # noqa: E402

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
HISTORY_SESSIONS = 12    # 前 12 个作历史，后 8 个作未来


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


def split(rows: list[dict], history_sessions: int = HISTORY_SESSIONS) -> tuple[list[dict], list[dict]]:
    """按**会话序号**切分（不是随机切）—— 跨会话预测必须是时序外推。

    随机切分会让"预测未来"退化成"插值"，那是在给判据放水。
    """
    hist = [r for r in rows if int(r.get("session", 0)) < history_sessions]
    fut = [r for r in rows if int(r.get("session", 0)) >= history_sessions]
    return hist, fut


def _shuffle_families(rows: list[dict]) -> list[dict]:
    """**反证组**：把历史里的族标签整体轮转一位。

    轮转（而不是随机置换）的理由：它保持每个族的样本数不变，
    只改变"标签 ↔ 通过率"的对应关系。于是如果优势消失，
    只可能是**对应关系**被破坏了 —— 归因干净，没有第二个变量。
    """
    out = []
    n = len(FAMILIES)
    for r in rows:
        fam = r.get("family")
        i = FAMILIES.index(fam) if fam in FAMILIES else -1
        new = FAMILIES[(i + 1) % n] if i >= 0 else fam
        d = dict(r)
        d["family"] = new
        out.append(d)
    return out


def evaluate(history: list[dict], future: list[dict]) -> dict:
    """拿历史建自我模型，预测未来，与**对照基线**比误差。"""
    sm = SelfModel(load_evidence(history))
    base = sm.global_rate()
    if not future:
        return {"n_future": 0, "mae_model": None, "mae_base": None,
                "delta": None, "global_rate": base, "families": len(sm.profile())}
    err_m, err_b = [], []
    for r in future:
        y = 1.0 if r.get("passed") else 0.0
        pm = sm.predict(str(r.get("family") or ""))
        if pm is None or base is None:
            continue
        err_m.append(abs(pm - y))
        err_b.append(abs(base - y))
    if not err_m:
        return {"n_future": len(future), "mae_model": None, "mae_base": None,
                "delta": None, "global_rate": base, "families": len(sm.profile())}
    mae_m = sum(err_m) / len(err_m)
    mae_b = sum(err_b) / len(err_b)
    return {"n_future": len(err_m), "mae_model": round(mae_m, 4),
            "mae_base": round(mae_b, 4), "delta": round(mae_b - mae_m, 4),
            "global_rate": base, "families": len(sm.profile()),
            "samples": len(sm.evidence)}


def probe_calibration(rows: list[dict],
                      history_sessions: int = HISTORY_SESSIONS) -> dict:
    """一次完整的判据读数：正组 + 反证组（打乱族标签）。

    `history_sessions` 可给，是为了**真实账本**：合成账本固定 20 个会话，
    真实账本可能有 4 个 —— 若沿用 12 这个切点，所有行都落进历史、
    未来为空 ⇒ 读不到任何东西却报告"判据成立"，那就是假绿。
    """
    hist, fut = split(rows, history_sessions)
    main = evaluate(hist, fut)
    ctrl = evaluate(_shuffle_families(hist), fut)
    # 对照组的 |delta| 就是**这个指标在这份数据上的噪声下限**：
    # 与 measure.noise_floor 同一条纪律 —— 不许拿一个绝对阈值去判"显著"。
    noise = abs(ctrl["delta"] or 0.0)
    margin = max(0.02, 2.0 * noise)
    n_fam = len({str(r.get("family")) for r in rows})
    d = main["delta"]
    if d is None or (main["n_future"] or 0) < 4:
        state = "UNKNOWN"
    elif d > margin:
        state = "PRESENT"
    else:
        # ⚠️ 不是一律 ABSENT：样本不够时"没测到" ≠ "没有"。
        #    （§16.7 第二层判据：UNKNOWN 不许并入 ABSENT）
        state = "UNKNOWN" if (main["samples"] or 0) < MIN_SAMPLES * max(n_fam, 1) \
            else "ABSENT"
    return {"main": main, "control": ctrl, "noise_floor": round(noise, 4),
            "margin": round(margin, 4), "state": state, "families_in_data": n_fam}


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

    hist_a, fut_a = split(rows_a)
    hist_b, _ = split(rows_b)

    gated = evaluate(hist_a, fut_a)
    mixed = evaluate(hist_a + hist_b, fut_a)
    return {"gated": gated, "mixed": mixed,
            "loss": round((mixed["mae_model"] or 0) - (gated["mae_model"] or 0), 4),
            "state": "PRESENT" if (mixed["mae_model"] or 0) > (gated["mae_model"] or 0)
                     else "ABSENT"}


def probe_real(path: Path) -> dict:
    """真实账本的读数。**样本不够就明说 UNKNOWN**，不许用合成数据凑。"""
    led = ScoreLedger(path)
    rows = led.load()
    sessions = {int(r.get("session", -1)) for r in rows}
    families = {str(r.get("family")) for r in rows}
    if len(rows) < 8 or len(sessions) < 4 or len(families) < 2:
        return {"state": "UNKNOWN", "rows": len(rows),
                "sessions": len(sessions), "families": len(families),
                "why": (f"账本不足（行={len(rows)} 会话={len(sessions)} "
                        f"族={len(families)}）；需要 行≥8 会话≥4 族≥2")}
    sigs: dict[str, int] = {}
    for r in rows:
        sigs[str(r.get("caliber_sig"))] = sigs.get(str(r.get("caliber_sig")), 0) + 1
    top = max(sigs, key=sigs.get) if sigs else ""
    sub = [r for r in rows if str(r.get("caliber_sig")) == top]
    # ⚠️ 切点必须**从数据来**：合成账本的 12 是拍出来的，真实账本会话数不定。
    #    沿用固定切点会让"未来集为空"静默发生 ⇒ 读数为 None 却报 PRESENT。
    sesses = sorted({int(r.get("session", 0)) for r in sub})
    cut = sesses[max(0, len(sesses) * 3 // 5 - 1)] if sesses else 0
    res = probe_calibration(sub, cut + 1)
    res["rows"] = len(rows)
    res["sessions"] = len(sessions)
    res["families"] = len(families)
    res["calibers"] = sigs
    res["used_sig"] = top
    return res


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

    if args.real or Path(ROOT / "state" / "score_ledger.jsonl").exists():
        p = Path(args.real) if args.real else ROOT / "state" / "score_ledger.jsonl"
        r_real = probe_real(p)
        out["real"] = r_real
        # ⚠️ 真实账本判 UNKNOWN **不算失败** —— 那是诚实结论，不是缺陷。
        #    把"没量到"记成"失败"，会逼着下一轮去凑数据（V5 的病）。

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    m, c = r_syn["main"], r_syn["control"]
    print("=" * 74)
    print("自我模型判据 —— 跨会话预测校准（合成账本：证明判据有分辨力）")
    print("=" * 74)
    print(f"  历史 {m['samples']} 条 / {m['families']} 族   未来 {m['n_future']} 条")
    print(f"  自我模型预测  MAE = {m['mae_model']}")
    print(f"  对照基线预测  MAE = {m['mae_base']}   （常数 {m['global_rate']}，不区分族）")
    print(f"  优势 Δ = {m['delta']}   噪声下限(打乱族标签) = {r_syn['noise_floor']}"
          f"   判定阈值 = {r_syn['margin']}")
    print(f"  ⇒ 判据{'成立' if r_syn['state'] == 'PRESENT' else '不成立'}（{r_syn['state']}）")
    print()
    print("  反证组（把族标签轮转一位，只破坏『标签↔通过率』的对应关系）：")
    print(f"    自我模型 MAE = {c['mae_model']}   Δ = {c['delta']}"
          f"   ⇒ 优势{'消失' if abs(c['delta'] or 0) < r_syn['margin'] else '仍在（可疑）'}")
    print()
    g, mx = r_gate["gated"], r_gate["mixed"]
    print("口径闸是不是承重的（两个口径下族通过率**相反**）：")
    print(f"  只收同口径  MAE = {g['mae_model']}")
    print(f"  混入异口径  MAE = {mx['mae_model']}   劣化 = {r_gate['loss']}")
    print(f"  ⇒ {'闸是承重的' if r_gate['state'] == 'PRESENT' else '撤了闸也没差（闸是装饰）'}")
    print()
    if "real" in out:
        rr = out["real"]
        print("真实账本：")
        if rr.get("state") == "UNKNOWN" and "why" in rr:
            print(f"  ⚠️ {rr['why']} —— 这是**诚实结论**，不是失败")
        else:
            rm = rr["main"]
            print(f"  行={rr['rows']} 会话={rr['sessions']} 族={rr['families']}"
                  f" 口径数={len(rr['calibers'])}（取最大口径 {rr['used_sig']}）")
            print(f"  自我模型 MAE={rm['mae_model']} 基线 MAE={rm['mae_base']}"
                  f" Δ={rm['delta']} ⇒ {rr['state']}")
    else:
        print("真实账本：不存在（`state/score_ledger.jsonl` 还没被写入过）")
    print()
    print("=" * 74)
    print("结论：", "✅ 判据成立（自我模型的预测优于不区分族的基线，"
                    "且撤掉族/撤掉口径闸都会转红）" if ok else "❌ 判据不成立")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
