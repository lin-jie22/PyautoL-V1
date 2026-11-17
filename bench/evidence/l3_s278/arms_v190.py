"""V190 六臂：把 17 个「池退化」格拿六种词表来源各翻一次。⛔ 不跑模型。

## 臂

| 臂 | 源 | 可部署 |
|---|---|---|
| `A` | 原池 | 基线 |
| `B` | `structural()` 变异 | ✅ |
| `C` | 同尺寸随机噪声 | ✅（对照：「只是加了更多输入」）|
| `E1` | 记忆条目正文取词元 | ⚠️ 假设条目已取到 |
| `E2` | 题面取词元 | ✅ |
| `D` | 实现字面量 | ❌ 泄漏上限 |

## 自检（不过就不许出读数）

- `I1`：`A` 臂必须逐格复现 `space_v189.json` 的 `sig`（老装置重放）。
- `I2`：同一格跨臂的判据**只差 `INPUTS` 一行**（归一化后 sha256 全等）。
- `I3`：`D` 臂的 `leak` 旗标必须为真（证明泄漏臂自己知道自己是泄漏）。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "l3_s271"))
sys.path.insert(0, str(HERE.parent / "l3_s272"))
sys.path.insert(0, str(HERE.parent / "l3_s273"))
sys.path.insert(0, str(ROOT))

import substrate as S  # noqa: E402
import arms_v184 as A4  # noqa: E402
from mw import critgen as CG  # noqa: E402
from mw import probe as PB  # noqa: E402

SPACE = json.loads((HERE.parent / "l3_s277" / "space_v189.json").read_text(encoding="utf-8"))

#: 种子惯例正文 —— 与 `bench/build_g7d_seed.py` 的 `FACTS` 逐字相同（7d 变体）。
FACTS = {
    "g7d2_dedupe_keep": "去重惯例：比较前先去掉首尾空白并转小写，遇到重复时保留第一次出现的那条原文。",
    "g7d3_fmt_amount": "金额惯例：千分位用空格分隔，保留两位小数，负数用圆括号包裹、不写负号。",
    "g7e1_parse_flag": "开关惯例：只把 y/yes/1 当成真，'true'、'on' 这类历史脏值一律算假，"
                       "其余无法识别的返回 None。",
    "g7e2_truncate_tail": "截断惯例：超长时保留最后 n 个字符，前面加一个省略号字符 '…'（U+2026），"
                          "不超长时原样返回。",
}

ARMS = ("A", "B", "C", "E1", "E2", "D")

_INPUTS = re.compile(r"^INPUTS = .*$", re.M)


def pool_for(arm: str, kind: str, types: list, t: dict) -> dict:
    """该臂该格的池。⛔ 这里**不碰实现**（`D` 例外，且自己标 LEAK）。"""
    if arm == "A":
        return PB.expand(kind, types, sources=())
    if arm == "B":
        return PB.expand(kind, types, sources=("mutate",))
    if arm == "C":
        #: 预算 = 结构变异给该签名带来的**最大增量**（同尺寸对照，只多不少）。
        add = max(len(PB.structural([ty])[ty]) - len(CG.POOL[ty]) for ty in set(types))
        return PB.expand(kind, types, sources=("noise",), noise_budget=add)
    if arm == "E1":
        return PB.expand(kind, types, sources=("text",), texts=[FACTS.get(t["id"], "")])
    if arm == "E2":
        return PB.expand(kind, types, sources=("text",), texts=[t["prompt"]])
    if arm == "D":
        return PB.expand(kind, types, sources=("literal",), oracle_src=t["oracle"])
    raise ValueError(arm)


def norm_sha(src: str) -> str:
    """判据指纹：把 `INPUTS` 那行归一掉，其余逐字算 sha。⛔ 不等 ⇒ 换了判据，不是换池。"""
    return hashlib.sha256(_INPUTS.sub("INPUTS = <POOL>", src).encode()).hexdigest()[:16]


def measure_arm(t: dict, kind: str, types: list, slots: dict, arm: str) -> dict:
    fn = S.fn_name(t)
    oracle = t["oracle"]
    e = pool_for(arm, kind, types, t)
    ins = CG.inputs_for(kind, types, e["pools"])
    legal = CG.legal_indexes(oracle, fn, ins)
    row = {"arm": arm, "id": t["id"], "kind": kind, "types": types, "slots": slots,
           "n_pool": len(ins), "sizes": e["sizes"], "leak": e["leak"],
           "prov_n": len(e["prov"])}
    if legal is None:
        row.update({"state": "PROBE_FAULT", "const": None, "verdict": "CRASH",
                    "n_legal": 0, "src_sha": ""})
        return row
    use = [ins[i] for i in legal]
    row["n_legal"] = len(use)
    const = CG.constant_on(oracle, fn, use)
    row["const"] = const
    src = CG.build(kind, types, slots, fn, use)
    if not src:
        row.update({"state": "SHAPE_BUILD", "verdict": "CRASH", "src_sha": ""})
        return row
    row["src_sha"] = norm_sha(src)
    j = A4.judge4(t, src)
    row.update({"state": "OK", "pass_oracle": bool(j["pass_oracle"]),
                "bite_stub": bool(j["bite_stub"]),
                "bite_partial": bool(j["bite_partial"]),
                "why": str(j.get("why_oracle"))[:120]})
    if const is True:
        row["verdict"] = "UNDECIDED"
    elif const is None:
        row["verdict"] = "UNDECIDED"
    else:
        row["verdict"] = "PASS" if row["pass_oracle"] else "FAIL"
    return row


def main() -> int:
    T = S.tasks()
    cells = [r for r in SPACE["rows"] if r.get("reach") is True and r.get("const") is not None]
    weak = [r for r in cells if r["sig"] == "CONST"]
    ok_cells = [r for r in cells if r["sig"] != "CONST"]
    print(f"格: reachable+const 可测 {len(cells)}（弱点 {len(weak)} / 本来可判 {len(ok_cells)}）")
    for r in weak:
        print(f"  弱点 {r['id']:22s} {r['kind']:16s} {str(r['types']):18s}")

    rows: list[dict] = []
    for c in cells:
        t = T[c["id"]]
        for arm in ARMS:
            rows.append(measure_arm(t, c["kind"], c["types"], c["slots"], arm))

    # ── I1：A 臂复现 space_v189 的 sig ─────────────────────────────────
    bad1 = []
    for c in cells:
        a = next(r for r in rows if r["id"] == c["id"] and r["kind"] == c["kind"]
                 and r["types"] == c["types"] and r["arm"] == "A")
        if c["sig"] == "CONST" and a["verdict"] != "UNDECIDED":
            bad1.append((c["id"], c["kind"], c["sig"], a["verdict"]))
        if c["sig"] in ("OK", "SEMANTIC") and a["verdict"] == "UNDECIDED":
            bad1.append((c["id"], c["kind"], c["sig"], a["verdict"]))
    print(f"\nI1 A 臂复现 space_v189 的 CONST/非 CONST 二分：{'✅' if not bad1 else '❌'} 不符 {len(bad1)}")
    for b in bad1[:6]:
        print("   ", b)

    # ── I2：同一格跨臂判据只差 INPUTS ────────────────────────────────
    bad2 = []
    for c in cells:
        shas = {r["src_sha"] for r in rows
                if r["id"] == c["id"] and r["kind"] == c["kind"] and r["types"] == c["types"]
                and r["src_sha"]}
        if len(shas) > 1:
            bad2.append((c["id"], c["kind"], sorted(shas)))
    n_cross = sum(1 for c in cells
                  if len({r["src_sha"] for r in rows
                          if r["id"] == c["id"] and r["kind"] == c["kind"]
                          and r["types"] == c["types"] and r["src_sha"]}) == 1)
    print(f"I2 跨臂判据指纹一致：{n_cross}/{len(cells)} 格；不一致 {len(bad2)}")
    for b in bad2[:6]:
        print("   ", b)

    # ── I3：D 臂自标 LEAK ────────────────────────────────────────────
    dleak = sum(1 for r in rows if r["arm"] == "D" and r["leak"])
    print(f"I3 D 臂 leak 旗标：{dleak}/{len(cells)} 格为真")

    out = {"frozen": {"space": str(HERE.parent / "l3_s277" / "space_v189.json"),
                      "critgen": "mw/critgen.py@V190"},
           "arms": list(ARMS), "cells": cells.__len__(),
           "weak": [{"id": c["id"], "kind": c["kind"], "types": c["types"],
                     "slots": c["slots"]} for c in weak],
           "checks": {"I1_bad": bad1, "I2_bad": bad2, "I3_leak": dleak},
           "rows": rows}
    (HERE / "readings_v190.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 汇总 ─────────────────────────────────────────────────────────
    def get(arm, c):
        return next(r for r in rows if r["arm"] == arm and r["id"] == c["id"]
                    and r["kind"] == c["kind"] and r["types"] == c["types"])

    print("\n== 弱点 17 格：转可判 ==")
    for arm in ARMS:
        opened = [c for c in weak if get(arm, c)["verdict"] != "UNDECIDED"]
        by_task = {}
        for c in opened:
            by_task[c["id"]] = by_task.get(c["id"], 0) + 1
        vd = {}
        for c in opened:
            v = get(arm, c)["verdict"]
            vd[v] = vd.get(v, 0) + 1
        print(f"  {arm:3s} 转可判 {len(opened):2d}/{len(weak)}  裁定 {vd}  按题 {by_task}")
        if opened and arm != "A":
            print(f"      {[c['kind'] for c in opened][:20]}")

    print(f"\n== {len(ok_cells)} 个本来可判的格：判定翻转 ==")  # ⚠️ 初版写死 199（= 216-17），把结构不可达的 125 格也算进分母了
    for arm in ("B", "C", "E1", "E2", "D"):
        flips = []
        for c in ok_cells:
            a, x = get("A", c), get(arm, c)
            if a["verdict"] != x["verdict"]:
                flips.append((c["id"], c["kind"], a["verdict"], x["verdict"]))
        print(f"  {arm:3s} 翻转 {len(flips):3d}/{len(ok_cells)}  "
              f"PASS→FAIL {sum(1 for f in flips if f[2]=='PASS')}  "
              f"FAIL→PASS {sum(1 for f in flips if f[2]=='FAIL')}")
        for f in flips[:5]:
            print("      ", f)

    print("\n== 池尺寸（同格跨臂）==")
    for c in weak[:4]:
        print("  ", c["id"], c["kind"], c["types"],
              {arm: (get(arm, c)["n_pool"], get(arm, c)["n_legal"]) for arm in ARMS})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
