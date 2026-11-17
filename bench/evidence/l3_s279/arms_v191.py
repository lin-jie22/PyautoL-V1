"""V191：17 个盲区格 → 下一步动作（路由 + 2×2 + collapse 账）。⛔ 不跑模型。

## 要回答的四件事

1. `route()` 把 17 格分成了什么（病名分布、目标签名种类）。
2. 2×2：{原签, 重选} × {原池, 扩池} 各转可判几格。
3. **collapse 账**：17 格重选后落在几个不同配置上？这些配置里几个**本来就有判定**？
   ⇒ 这一条决定「吃掉 12/17」是真收益还是重复记账。
4. `false_reach` 在 91 个可达格里点出几格。

## 自检（不过就不许出读数）

- `I1`：`probe.sig_space()` 与已冻结的 `space_v189.type_combos()` **逐条相同**。
- `I2`：`route()` / `varies()` 的参数表里没有正确性信号（`pass_oracle`/`correct`/`expected`/`want`）。
- `I3`：`sig_space()` 两次调用逐字相同（F5：枚举顺序不许随探测结果变）。
- `I4`：重选后的判据与目标格原判据**只差 `INPUTS` 一行**（V188 I2 同一条边界）。
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "l3_s271"))
sys.path.insert(0, str(HERE.parent / "l3_s272"))
sys.path.insert(0, str(HERE.parent / "l3_s273"))
sys.path.insert(0, str(HERE.parent / "l3_s277"))
sys.path.insert(0, str(HERE.parent / "l3_s278"))
sys.path.insert(0, str(ROOT))

import substrate as S  # noqa: E402
import arms_v184 as A4  # noqa: E402
import space_v189 as SP  # noqa: E402
from mw import critgen as CG  # noqa: E402
from mw import probe as PB  # noqa: E402

SPACE = json.loads((HERE.parent / "l3_s277" / "space_v189.json").read_text(encoding="utf-8"))
V190 = json.loads((HERE.parent / "l3_s278" / "readings_v190.json").read_text(encoding="utf-8"))

FACTS = {
    "g7d2_dedupe_keep": "去重惯例：比较前先去掉首尾空白并转小写，遇到重复时保留第一次出现的那条原文。",
    "g7d3_fmt_amount": "金额惯例：千分位用空格分隔，保留两位小数，负数用圆括号包裹、不写负号。",
    "g7e1_parse_flag": "开关惯例：只把 y/yes/1 当成真，'true'、'on' 这类历史脏值一律算假，"
                       "其余无法识别的返回 None。",
    "g7e2_truncate_tail": "截断惯例：超长时保留最后 n 个字符，前面加一个省略号字符 '…'（U+2026），"
                          "不超长时原样返回。",
}

_INPUTS = re.compile(r"^INPUTS = .*$", re.M)
_WRONG = {"pass_oracle", "correct", "expected", "want", "right", "truth"}


def key(r: dict) -> tuple:
    return (r["id"], r["kind"], tuple(r["types"]))


def verdict_of(sig: str, pass_oracle) -> str:
    """把一格折成三值。`sig` 是老装置的口径，`pass_oracle` 是判分结果。"""
    if sig in ("CONST", "PROBE_FAULT") or pass_oracle is None:
        return "UNDECIDED"
    return "PASS" if pass_oracle else "FAIL"


def measure_at(t: dict, kind: str, types: list, slots: dict, *, pool=None,
               texts: tuple = ()) -> dict:
    """在**指定签名**上量一格：合法域 → 是否恒定 → 判分。"""
    fn = S.fn_name(t)
    oracle = t["oracle"]
    ins = CG.inputs_for(kind, list(types), pool)
    legal = CG.legal_indexes(oracle, fn, ins)
    out = {"types": list(types), "n_legal": 0, "const": None, "src_sha": "",
           "pass_oracle": None, "bite_stub": None, "bite_partial": None, "verdict": "UNDECIDED"}
    if legal is None or not legal:
        return out
    use = [ins[i] for i in legal]
    out["n_legal"] = len(use)
    const = CG.constant_on(oracle, fn, use)
    out["const"] = const
    src = CG.build(kind, list(types), slots, fn, use)
    if not src:
        out["verdict"] = "CRASH"
        return out
    out["src_sha"] = hashlib.sha256(_INPUTS.sub("INPUTS = <POOL>", src).encode()).hexdigest()[:16]
    j = A4.judge4(t, src)
    out.update({"pass_oracle": bool(j["pass_oracle"]), "bite_stub": bool(j["bite_stub"]),
                "bite_partial": bool(j["bite_partial"]), "why": str(j.get("why_oracle"))[:100]})
    out["verdict"] = "UNDECIDED" if const in (True, None) else (
        "PASS" if j["pass_oracle"] else "FAIL")
    return out


def main() -> int:
    T = S.tasks()
    cells = [r for r in SPACE["rows"] if r.get("reach") is True and r.get("const") is not None]
    weak = [r for r in cells if r["sig"] == "CONST"]
    print(f"可测格 {len(cells)}（弱点 {len(weak)}）")

    # ── I1：签名枚举与冻结的证据脚本逐条相同 ──────────────────────────
    bad1 = []
    for tid, t in T.items():
        n = CG.arity(t["oracle"], S.fn_name(t))
        for kind in CG.ops_for(n):
            if PB.sig_space(kind, n) != SP.type_combos(kind, n):
                bad1.append((tid, kind, n))
    print(f"I1 sig_space ≡ space_v189.type_combos：{'✅' if not bad1 else '❌'} 不符 {len(bad1)}")
    for b in bad1[:5]:
        print("   ", b)

    # ── I2：参数表里没有正确性信号 ────────────────────────────────────
    p_route = set(inspect.signature(PB.route).parameters)
    p_var = set(inspect.signature(PB.varies).parameters)
    p_dom = set(inspect.signature(PB.domain_of).parameters)
    bad2 = sorted((p_route | p_var | p_dom) & _WRONG)
    print(f"I2 路由层参数表无正确性信号：{'✅' if not bad2 else '❌'} 命中 {bad2}")

    # ── I3：枚举确定 ─────────────────────────────────────────────────
    det = all(PB.sig_space(k, n) == PB.sig_space(k, n)
              for tid, t in T.items()
              for n in [CG.arity(t["oracle"], S.fn_name(t))]
              for k in CG.ops_for(n))
    print(f"I3 sig_space 两次调用逐字相同：{'✅' if det else '❌'}")

    # ── 主读数 1：路由 ───────────────────────────────────────────────
    routes: list[dict] = []
    for c in weak:
        t = T[c["id"]]
        n = c["n_args"]
        r = PB.route(c["kind"], c["types"], n, t["oracle"], S.fn_name(t),
                     texts=(FACTS.get(c["id"], ""),))
        r.update({"id": c["id"], "kind": c["kind"], "types": c["types"],
                  "slots": c["slots"], "n_args": n})
        routes.append(r)
        print(f"  {c['id']:22s} {c['kind']:16s} {str(c['types']):18s} "
              f"→ {r['disease']:13s} target={r['target']} probes={r['probes']}")

    dis = {}
    for r in routes:
        dis[r["disease"]] = dis.get(r["disease"], 0) + 1
    print(f"\n病名分布：{dis}")

    # ── 主读数 2：2×2 ────────────────────────────────────────────────
    # `A`（原签·原池）与 `E1`（原签·扩池）直接取自 V190 的读数，⛔ 不重跑
    #   —— 重跑会引入「这次跑得更准」的解释空间，且 V190 I1 已验过可复现。
    v190 = {(r["arm"], r["id"], r["kind"], tuple(r["types"])): r for r in V190["rows"]}
    arms: list[dict] = []
    for c in weak:
        t = T[c["id"]]
        rt = next(r for r in routes if r["id"] == c["id"] and r["kind"] == c["kind"]
                  and tuple(r["types"]) == tuple(c["types"]))
        row = {"id": c["id"], "kind": c["kind"], "types": c["types"],
               "disease": rt["disease"], "target": rt["target"]}
        for arm, src in (("A", "A"), ("E1", "E1")):
            o = v190.get((src, c["id"], c["kind"], tuple(c["types"])))
            row[arm] = o["verdict"] if o else "MISSING"
        # 重选臂：原池
        if rt["target"]:
            row["RS"] = measure_at(t, c["kind"], rt["target"], c["slots"])["verdict"]
            row["RS_detail"] = measure_at(t, c["kind"], rt["target"], c["slots"])
            exp = PB.expand(c["kind"], rt["target"], sources=("mutate", "text", "noise"),
                            texts=(FACTS.get(c["id"], ""),))["pools"]
            row["RSX"] = measure_at(t, c["kind"], rt["target"], c["slots"], pool=exp)["verdict"]
        else:
            row["RS"] = "NO_TARGET"
            row["RSX"] = "NO_TARGET"
        arms.append(row)

    print("\n== 2×2（分母 17）==")
    for arm in ("A", "E1", "RS", "RSX"):
        opened = [r for r in arms if r.get(arm) not in ("UNDECIDED", "MISSING", "NO_TARGET", "CRASH")]
        vd = {}
        for r in opened:
            vd[r[arm]] = vd.get(r[arm], 0) + 1
        print(f"  {arm:4s} 转可判 {len(opened):2d}/17  裁定 {vd}")

    # ── 主读数 3：collapse 账 ────────────────────────────────────────
    targets: dict[tuple, list] = {}
    for r in arms:
        if r["target"]:
            targets.setdefault((r["kind"], tuple(r["target"])), []).append(r["id"])
    print(f"\n== collapse：17 格 → {len(targets)} 个配置 ==")
    for k, ids in targets.items():
        print(f"  {k[0]:16s} {k[1]}  ← {len(ids)} 格")

    #: 目标配置在 216 格空间里本来是什么
    space_idx = {(r["kind"], tuple(r["types"])): r for r in SPACE["rows"]
                 if r.get("reach") is True and r.get("const") is not None}
    collapse = []
    for (kind, tps), ids in targets.items():
        o = space_idx.get((kind, tps))
        collapse.append({"kind": kind, "target": list(tps), "cells": len(ids),
                         "pre_sig": o["sig"] if o else None,
                         "pre_verdict": verdict_of(o["sig"], o.get("pass_oracle")) if o else None})
        print(f"  {kind:16s} {list(tps)}  原判定={collapse[-1]['pre_verdict']}  覆盖 {len(ids)} 格")
    new_cfg = [c for c in collapse if c["pre_verdict"] in (None, "UNDECIDED")]
    print(f"  新增可判配置 {len(new_cfg)}/{len(collapse)}")

    # ── P3：逐格核对「重选后的判定 ≡ 目标格原判定」 ──────────────────
    mism = []
    for r in arms:
        if not r["target"]:
            continue
        o = space_idx.get((r["kind"], tuple(r["target"])))
        pre = verdict_of(o["sig"], o.get("pass_oracle")) if o else None
        if pre != r["RS"]:
            mism.append((r["id"], r["kind"], r["target"], pre, r["RS"]))
    print(f"\nP3 重选后判定 ≡ 目标格原判定：{len(arms) - len([r for r in arms if not r['target']]) - len(mism)}"
          f"/{len([r for r in arms if r['target']])} 一致；不符 {len(mism)}")
    for m in mism[:6]:
        print("   ", m)

    # ── I4：重选后的判据只差 INPUTS ──────────────────────────────────
    bad4 = []
    for r in arms:
        if not r["target"] or not r.get("RS_detail", {}).get("src_sha"):
            continue
        c = next(x for x in weak if x["id"] == r["id"] and x["kind"] == r["kind"]
                 and tuple(x["types"]) == tuple(r["types"]))
        base = measure_at(T[r["id"]], r["kind"], r["types"], c["slots"])
        if base["src_sha"] and base["src_sha"] != r["RS_detail"]["src_sha"]:
            bad4.append((r["id"], r["kind"], base["src_sha"], r["RS_detail"]["src_sha"]))
    print(f"I4 重选前后判据指纹一致（只差 INPUTS）：{'✅' if not bad4 else '❌'} 不符 {len(bad4)}")
    for b in bad4[:5]:
        print("   ", b)

    # ── P4：false_reach 全 91 格扫描 ────────────────────────────────
    fr = []
    for c in cells:
        t = T[c["id"]]
        if PB.false_reach(c["kind"], c["types"], t["oracle"], S.fn_name(t)):
            fr.append((c["id"], c["kind"], c["types"], c["sig"]))
    print(f"\nP4 false_reach：{len(fr)}/{len(cells)} 格")
    for f in fr:
        mark = "★弱点" if f[3] == "CONST" else ""
        print(f"   {f[0]:22s} {f[1]:16s} {str(f[2]):18s} {f[3]:10s} {mark}")

    out = {"frozen": {"space": "l3_s277/space_v189.json", "v190": "l3_s278/readings_v190.json"},
           "checks": {"I1_bad": bad1, "I2_bad": bad2, "I3_det": det, "I4_bad": bad4},
           "disease": dis, "cells": len(cells), "weak": len(weak),
           "routes": routes, "arms": arms,
           "collapse": collapse, "new_cfg": len(new_cfg), "p3_mismatch": mism,
           "false_reach": fr}
    (HERE / "readings_v191.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n落盘 {HERE / 'readings_v191.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
