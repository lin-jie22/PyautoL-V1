#!/usr/bin/env python3
"""V241 分析：预算 → 截断率 / 落分率 / 6-6 次数 / 照抄率。

口径（与 PREREG_v241 §1 一致，写进字段名）：
  n_att   = 换路**调用次数**（落分的 + 失败的，失败从日志行反推模型）
  n_trunc = done_reason=length 的次数
  n_land  = 落分候选数
  rate_X  = n_X / n_att（⛔ 分母是调用次数，不是落分数）

模型归因：日志 `换路 #N` 里的 N 是 0 基循环下标，模型 = models[N % 2]
  ⇒ 偶数位 = qwen2.5:7b，奇数位 = 4B。截断行不带模型名，只能这样反推。
  **反向检验**：若偶数位也出现截断 ⇒ 归因错，报 UNKNOWN（不许硬解释）。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

D = pathlib.Path(__file__).resolve().parent
MODELS = ["qwen2.5:7b", "Qwen3.5-4B@ollama:disabled"]
START_SHA = "90579ecebe633352"


def arm(tag: str) -> dict:
    obs = [json.loads(x) for x in (D / f"obs_{tag}.jsonl").read_text().splitlines() if x.strip()]
    swap = [o for o in obs if o.get("source") == "goal_swap"]
    trunc_idx: list[int] = []
    other_fail: list[tuple[int, str]] = []
    for lf in sorted(D.glob(f"round_{tag}_*.log")):
        for ln in lf.read_text(errors="replace").splitlines():
            m = re.search(r"换路 #(\d+) 失败：(.*)", ln)
            if not m:
                continue
            i = int(m.group(1))
            msg = m.group(2)
            if "响应被截断" in msg:
                trunc_idx.append(i)
            else:
                other_fail.append((i, msg[:60]))

    per = {}
    for mi, name in enumerate(MODELS):
        land = [o for o in swap if (o.get("model") or "") == name]
        tr = [i for i in trunc_idx if i % len(MODELS) == mi]
        ot = [(i, w) for i, w in other_fail if i % len(MODELS) == mi]
        n_att = len(land) + len(tr) + len(ot)
        per[name] = {
            "n_att": n_att, "n_land": len(land), "n_trunc": len(tr),
            "n_other_fail": len(ot),
            "rate_trunc": round(len(tr) / n_att, 4) if n_att else None,
            "rate_land": round(len(land) / n_att, 4) if n_att else None,
            "n_6of6": sum(1 for o in land if float(o.get("score") or 0) >= 1.0),
            "n_exact_start": sum(1 for o in land
                                 if (o.get("impl_sha") or "")[:16] == START_SHA),
            "vecs": ["".join("1" if x else "0" for x in (o.get("vec") or []))
                     for o in land],
            "scores": [o.get("score") for o in land],
            "chars": [o.get("chars") for o in land],
        }
    return {"tag": tag, "n_swap_rows": len(swap), "trunc_idx": sorted(trunc_idx),
            "other_fail": other_fail, "per_model": per}


def main() -> int:
    arms = [arm(t) for t in ("b900", "b1600", "b2400")]
    m4 = "Qwen3.5-4B@ollama:disabled"
    m7 = "qwen2.5:7b"

    print(f"{'臂':7s} {'模型':26s} {'att':>4s} {'land':>5s} {'trunc':>6s} "
          f"{'r_trunc':>8s} {'r_land':>7s} {'6/6':>4s} {'照抄':>5s}")
    for a in arms:
        for name in MODELS:
            p = a["per_model"][name]
            print(f"{a['tag']:7s} {name:26s} {p['n_att']:4d} {p['n_land']:5d} "
                  f"{p['n_trunc']:6d} {str(p['rate_trunc']):>8s} "
                  f"{str(p['rate_land']):>7s} {p['n_6of6']:4d} {p['n_exact_start']:5d}")

    print("\n--- 逐条 ---")
    for a in arms:
        for name in MODELS:
            p = a["per_model"][name]
            if p["n_att"]:
                print(f"{a['tag']:7s} {name:26s} vec={p['vecs']} score={p['scores']} "
                      f"chars={p['chars']} trunc_idx={a['trunc_idx']} "
                      f"other={a['other_fail']}")

    # ---- 押注判定 ----
    def rate(a, m):
        return a["per_model"][m]["rate_trunc"]

    r900, r1600, r2400 = (rate(arms[0], m4), rate(arms[1], m4), rate(arms[2], m4))
    print("\n=== 押注 ===")
    unknown = []
    for a in arms:
        if (a["per_model"][m4]["n_att"] or 0) < 4:
            unknown.append(f"{a['tag']} 4B n_att={a['per_model'][m4]['n_att']} <4（早停）")
    if any(i % 2 == 0 for a in arms for i in a["trunc_idx"]):
        unknown.append("偶数位（7b）也出现截断 ⇒ 模型归因错")

    if unknown:
        print("UNKNOWN:", unknown)
        p1 = p2 = p3 = p4 = "UNKNOWN"
    else:
        mono = (r900 >= r1600 >= r2400) and (r900 > r2400 or r900 > r1600)
        p1 = "PASS" if mono else "FAIL"
        l900 = arms[0]["per_model"][m4]["rate_land"] or 0
        l2400 = arms[2]["per_model"][m4]["rate_land"] or 0
        p2 = "PASS" if l2400 > l900 else "FAIL"
        n7 = sum(a["per_model"][m7]["n_trunc"] for a in arms)
        att7 = sum(a["per_model"][m7]["n_att"] for a in arms)
        p3 = "PASS" if n7 == 0 and att7 > 0 else "FAIL"
        s6 = [a["per_model"][m4]["n_6of6"] for a in arms]
        p4 = "PASS" if s6[2] >= s6[0] else "FAIL"

    print(f"P1 截断率单调不增（{r900} >= {r1600} >= {r2400}，至少一处严格降）: {p1}")
    print(f"P2 rate_land(4B) 2400 > 900: {p2}")
    print(f"P3 7b 截断恒 0: {p3}")
    print(f"P4 6/6 次数 2400 >= 900: {p4}")
    print(f"\n判据行  V241 P1={p1} P2={p2} P3={p3} P4={p4}")

    (D / "analyse_v241_out.json").write_text(
        json.dumps({"arms": arms, "verdicts": {"P1": p1, "P2": p2, "P3": p3, "P4": p4},
                    "unknown": unknown}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
