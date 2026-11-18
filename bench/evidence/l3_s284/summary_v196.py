#!/usr/bin/env python3
"""V196 汇总：按预注册的 6 条押结算 + 逐格翻转表。⛔ 不改判据、不改阈值。"""
from __future__ import annotations

import json
import statistics as st
from pathlib import Path

OUT = Path(__file__).resolve().parent
CELLS = OUT / "cells_v196.jsonl"


def main() -> int:
    rows = [json.loads(l) for l in CELLS.read_text(encoding="utf-8").splitlines() if l.strip()]
    by: dict[tuple[str, str], dict[str, dict]] = {}
    for r in rows:
        if r["arm"] not in ("R", "P", "R2", "S", "C"):
            continue
        by.setdefault((r["suite"], r["id"]), {})[r["arm"]] = r

    cells = sorted(k for k, v in by.items() if "R" in v and "P" in v)
    print(f"格数 denom={len(cells)}（预注册 23）")

    n = [by[c]["R"]["baseline_score"] for c in cells]
    r_ = [by[c]["R"]["score"] for c in cells]
    p_ = [by[c]["P"]["score"] for c in cells]

    bad = [c for c, a, b in zip(cells, r_, p_) if a is None or b is None]
    print("无效格（有 None）:", bad)
    ok_cells = [c for c in cells if c not in bad]
    n = [by[c]["R"]["baseline_score"] for c in ok_cells]
    r_ = [by[c]["R"]["score"] for c in ok_cells]
    p_ = [by[c]["P"]["score"] for c in ok_cells]

    mN, mR, mP = (sum(n) / len(n), sum(r_) / len(r_), sum(p_) / len(p_)) if ok_cells else (0, 0, 0)
    print(f"mean(N)={mN:.4f}  mean(R)={mR:.4f}  mean(P)={mP:.4f}   denom={len(ok_cells)}")
    print(f"R-N={mR - mN:+.4f}   P-N={mP - mN:+.4f}   R-P={mR - mP:+.4f}")

    # P1：R 与 P 的答案逐字节不同
    diff = sum(1 for c in ok_cells
               if by[c]["R"]["answer_sha"] != by[c]["P"]["answer_sha"]
               and by[c]["R"]["answer_sha"] and by[c]["P"]["answer_sha"])
    print(f"P1 R/P 答案不同 {diff}/{len(ok_cells)} = {diff / max(1, len(ok_cells)):.3f}（押 ≥0.80）")

    # P2 / P3 / P4
    print(f"P2 mean(R)-mean(N) = {mR - mN:+.4f}（押 ≥ +0.10）")
    print(f"P3 |mean(P)-mean(N)| = {abs(mP - mN):.4f}（押 ≤ 0.10）")
    print(f"P4 mean(R) ≥ mean(P) ? {mR >= mP}（{mR:.4f} vs {mP:.4f}）")

    # P5 装置
    refuse = [c for c in ok_cells for arm in ("R", "P")
              if "不适用" in str(by[c][arm].get("err") or "")]
    empty = [c for c in ok_cells for arm in ("R", "P")
             if not by[c][arm].get("answer_chars")]
    models = {str(by[c][arm].get("model") or "") for c in ok_cells for arm in ("R", "P")}
    nb = len(ok_cells) * 2
    print(f"P5 拒答 {len(refuse)}/{nb}、空答案 {len(empty)}/{nb}、model={models}")

    # 逐格翻转
    def flip(a: list[float], b: list[float]) -> tuple[int, int, int]:
        up = sum(1 for x, y in zip(a, b) if y > x + 1e-9)
        dn = sum(1 for x, y in zip(a, b) if y < x - 1e-9)
        return up, sum(1 for x, y in zip(a, b) if abs(y - x) <= 1e-9), dn

    print("翻转 R vs N:", flip(n, r_), " P vs N:", flip(n, p_))
    echo = sum(1 for c in ok_cells for arm in ("R", "P") if by[c][arm].get("draft_echo"))
    print(f"照抄率 draft_echo（事后登记，非押）：{echo}/{nb} = {echo / max(1, nb):.3f}")

    # ── 照抄与收益的分解（事后登记）──────────────────────────────────────
    echo_r = [c for c in ok_cells if by[c]["R"].get("draft_echo")]
    non_echo_r = [c for c in ok_cells if not by[c]["R"].get("draft_echo")]
    same_when_echo = [c for c in echo_r
                      if abs(float(by[c]["R"]["score"]) - by[c]["R"]["baseline_score"]) <= 1e-9]
    print(f"R 照抄 {len(echo_r)} 格，其中分数与 N **完全相同** {len(same_when_echo)} 格")
    if non_echo_r:
        d = [float(by[c]["R"]["score"]) - float(by[c]["R"]["baseline_score"]) for c in non_echo_r]
        tot_from_echo = sum(float(by[c]["R"]["score"]) - float(by[c]["R"]["baseline_score"]) for c in echo_r)
        print(f"R 的非照抄 {len(non_echo_r)} 格：Δ={[round(x, 4) for x in d]} 合计 {sum(d):+.4f}；"
              f"照抄格合计 {tot_from_echo:+.4f}")

    # ── 事后对照 S（同一流程、不带草稿）──────────────────────────────────
    s_cells = [c for c in ok_cells if "S" in by[c] and by[c]["S"].get("score") is not None]
    if s_cells:
        sv = [float(by[c]["S"]["score"]) for c in s_cells]
        # ⚠️ 纪律 46/47：**分母不同不比**。R/P 必须在**同一批有 S 的格**上重算，
        #    否则 `R − S` 会比的是 23 格 vs 22 格。
        rv = [float(by[c]["R"]["score"]) for c in s_cells]
        pv = [float(by[c]["P"]["score"]) for c in s_cells]
        nv = [float(by[c]["R"]["baseline_score"]) for c in s_cells]
        mR_s, mP_s, mS, mN_s = (sum(rv) / len(rv), sum(pv) / len(pv),
                                sum(sv) / len(sv), sum(nv) / len(nv))
        print(f"\n事后对照 S（配对 denom={len(s_cells)}；R/P/N 在本块均按同一批格重算）")
        print(f"  mean(N')={mN_s:.4f} mean(R')={mR_s:.4f} mean(P')={mP_s:.4f} mean(S)={mS:.4f}")
        print(f"  R−S={mR_s - mS:+.4f}   P−S={mP_s - mS:+.4f}   S−N={mS - mN_s:+.4f}")
        per = [float(by[c]["R"]["score"]) - float(by[c]["S"]["score"]) for c in s_cells]
        print(f"  逐格 R−S 非零：{[(c[0][:8] + '/' + c[1], round(x, 4)) for c, x in zip(s_cells, per) if abs(x) > 1e-9]}")
        up_s = sum(1 for c in s_cells if float(by[c]["S"]["score"]) > float(by[c]["R"]["baseline_score"]) + 1e-9)
        dn_s = sum(1 for c in s_cells if float(by[c]["S"]["score"]) < float(by[c]["R"]["baseline_score"]) - 1e-9)
        print(f"  S vs N 翻转：涨 {up_s} / 平 {len(s_cells) - up_s - dn_s} / 跌 {dn_s}")

    # ── 装置自检 C（伤害性草稿）────────────────────────────────────────
    c_cells = [c for c in ok_cells if "C" in by[c]]
    for c in c_cells:
        cc, rr = by[c]["C"], by[c]["R"]
        print(f"\n装置自检 C {c[0]}/{c[1]}: N={cc['baseline_score']} C={cc['score']} R={rr['score']} "
              f"sha(C)={str(cc['answer_sha'])[:8]} sha(R)={str(rr['answer_sha'])[:8]} "
              f"同答案={cc['answer_sha'] == rr['answer_sha']}")


    # P6 噪声
    r2 = [(c, by[c]["R2"]) for c in ok_cells if "R2" in by[c]]
    if r2:
        deltas = [abs(float(x["score"]) - float(by[c]["R"]["score"])) for c, x in r2]
        print(f"P6 噪声 {len(r2)} 格：Δ={[round(d, 4) for d in deltas]} max={max(deltas):.4f}")
    else:
        print("P6 噪声：尚未跑")

    print("\n逐格明细：")
    for c in ok_cells:
        a, b = by[c]["R"], by[c]["P"]
        print(f"  {c[0]:24s} {c[1]:22s} N={a['baseline_score']:.4f} "
              f"R={a['score']} P={b['score']} echoR={a.get('draft_echo')} echoP={b.get('draft_echo')}")

    if ok_cells:
        print("\n分臂中位数:", round(st.median(n), 4), round(st.median(r_), 4), round(st.median(p_), 4))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
