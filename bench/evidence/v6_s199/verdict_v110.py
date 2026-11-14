#!/usr/bin/env python3
"""V110 判决：从 `delete.json` 重算，**不重跑判分**。

⛔ 本轮最重要的一件事：**符号方向**
    脚本算的是 `Δ = s(删后) − s(基线)`；
    惯用法的**因果效应** = `−Δ`。
    `delete_idiom.py` 的 §4 拿 `Δ` 直接与 V109 的 uplift 比「同号」，**口径错了**。
    本文件两个口径都报，以「效应 = −Δ」为准。
"""
from __future__ import annotations

import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent

STRUCT_METHODS = {"append", "add", "format"}


def kind(idiom: str) -> str:
    m = re.match(r"\.(\w+)\(", idiom)
    return "structural" if m and m.group(1) in STRUCT_METHODS else "modifier"


def main() -> None:
    d = json.loads((HERE / "delete.json").read_text(encoding="utf-8"))
    v109 = json.loads((EV / "v6_s198" / "idioms.json").read_text(encoding="utf-8"))
    rows = [r for r in d["rows"] if r["ok"] and r["delta"] is not None]
    unc = [r for r in d["rows"] if not r["ok"]]
    for r in rows:
        r["kind"] = kind(r["idiom"])
        r["effect"] = round(-r["delta"], 4)

    print("## §5 效应方向（本轮的核心自踩）")
    print("  脚本算的是 Δ = s(删后) − s(基线)；惯用法的**效应 = −Δ**。")
    print("  `delete_idiom.py` §4 拿 Δ 直接与 uplift 比同号 ⇒ **口径错**。两种口径都报：\n")
    print("  | 口径 | 同号 | 异号 | ZERO | 判 |")
    print("  |---|---|---|---|---|")
    for name, key in (("⛔ 错误口径（用 Δ）", "delta"), ("✅ 正确口径（用 −Δ）", "effect")):
        same = opp = zero = 0
        for r in rows:
            u = v109["uplift"].get(r["idiom"], {}).get(r["tid"])
            if u is None or abs(u) < 1e-9:
                continue
            v = r[key]
            if abs(v) < 1e-9:
                zero += 1
            elif (v > 0) == (u > 0):
                same += 1
            else:
                opp += 1
        print(f"  | {name} | {same} | {opp} | {zero} | "
              f"{'押中（异号为主）' if opp > same else '⛔ 押错（同号为主）'} |")

    # 按（题, 惯用法）聚合，正确口径
    print("\n  逐（题, 惯用法）聚合（效应 = −Δ 的均值，分母 = 格数）：")
    per = {}
    for r in rows:
        if r["idiom"] in v109["uplift"] and r["tid"] in v109["uplift"][r["idiom"]]:
            per.setdefault((r["tid"], r["idiom"]), []).append(r["effect"])
    same = opp = zero = 0
    for (tid, idiom), es in sorted(per.items()):
        u = v109["uplift"][idiom][tid]
        m = sum(es) / len(es)
        rel = "ZERO" if abs(m) < 1e-9 else ("同号" if (m > 0) == (u > 0) else "异号")
        same, opp, zero = (same + (rel == "同号"), opp + (rel == "异号"), zero + (rel == "ZERO"))
        print(f"    {tid.split('_')[0]:6s} {idiom:24s} V109={u:+.3f}　效应={m:+.4f}"
              f"（{len(es)} 格）　{rel}")
    print(f"    ⇒ 同号 {same} / 异号 {opp} / ZERO {zero}（分母 {len(per)} 个（题, 惯用法）组）")
    print(f"    ⇒ {'⛔ W2 押错：V109 的组间相关在同题内大体被因果证实' if same > opp else '✅ W2 押中'}")

    # ---------- 分层 ----------
    print("\n## §6 分层：结构性 vs 修饰性（V110 的分析核心）")
    for k in ("structural", "modifier"):
        sub = [r for r in rows if r["kind"] == k]
        down = [r for r in sub if r["effect"] > 0]
        print(f"  {k:11s} 分母 {len(sub):2d} 格　下降(效应>0) {len(down):2d}　"
              f"不变 {len(sub) - len(down):2d}")
    print("  ⛔ 结构性（`.append`/`.add`/`.format`）删掉后函数**根本不出结果** ⇒ 下降是必然，不算因果证据。")
    print("  ⭐ 只有**修饰性**（`.strip`/`.lower`/`.replace`）的下降才是「少一个惯用法就变差」的证据：")
    mod = [r for r in rows if r["kind"] == "modifier"]
    for r in sorted([r for r in mod if r["effect"] > 0], key=lambda x: (-x["effect"], x["tid"])):
        print(f"    {r['tid'].split('_')[0]:6s} {r['h']} {r['idiom']:24s} 效应={r['effect']:+.2f}")
    n_mod_down = sum(1 for r in mod if r["effect"] > 0)
    print(f"  ⇒ 修饰性里下降 {n_mod_down}/{len(mod)} 格")

    # ---------- V109 四条逐条归因 ----------
    print("\n## §7 ⭐ V109 的四条「符号」逐条归因（这是本轮唯一的结论翻转）")
    print("  **① g7d3 `.strip()` −0.450**")
    print("    唯一含它的实现 `36317a87` 是那 4 个 UNCOMPILABLE —— 因为**原代码本身就编译不过**：")
    print("    `f\" {(x:.2f):,}\"` 在这个 Python 上非法 ⇒ 它的 0.0 是**语法错**，与 `.strip()` 无关。")
    print("    其余 8 个 g7d3 实现都不含 `.strip()` ⇒ 该格**没有任何因果证据**。")
    print("    ⇒ ⛔ **−0.450 判死（可证明的混杂）**：删无可删。")
    print("\n  **② g7e2 `.strip()` / `.lower()` −0.800**")
    sub = [r for r in rows if r["tid"] == "g7e2_truncate_tail"]
    for r in sub:
        print(f"    {r['h']} {r['idiom']:12s} 基线 {r['s0']}　删后 {r['s1']}　效应 {r['effect']:+.2f}"
              f"　位图 {'不变' if r.get('bit_same') else '变了'}")
    print("    ⇒ ⛔ **−0.800 判死**：4 格效应全 0。那两个 0.2 来自 `s[-(n-3):]` 的下标错，与归一化无关。")
    print("\n  **③ g7d2 `.lower()` +0.700 / `.strip()` +0.600**")
    for r in rows:
        if r["tid"] == "g7d2_dedupe_keep" and r["idiom"] in (".lower()", ".strip()"):
            print(f"    {r['h']} {r['idiom']:10s} 基线 {r['s0']}　删后 {r['s1']}　效应 {r['effect']:+.2f}")
    print("    ⇒ ✅ **因果成立且同号**：删掉就掉分 ⇒ 这两条是真必要条件。")
    print("    ⚠️ 但注意幅度：`.lower()` 效应 +0.8/+0.8/+0.8/+0.4，`.strip()` +0.4/+0.4/+0.4 —— ")
    print("       **符号对，数值不对**（V109 的 +0.700/+0.600 是组间差，不是效应量）。")

    # ---------- g7e1 盲区 ----------
    print("\n## §8 ⭐ V109 的盲区：`两组之一为空` 的 28 个组合里藏着真因果")
    sub = [r for r in rows if r["tid"] == "g7e1_parse_flag"]
    by = {}
    for r in sub:
        by.setdefault(r["idiom"], []).append(r["effect"])
    for idiom, es in sorted(by.items()):
        print(f"    {idiom:10s} {len(es)} 格　效应 {es}　最大 {max(es):+.2f}")
    print("    ⇒ g7e1 的 8 个实现**每个都含 `.strip()` 与 `.lower()`** ⇒ V109 的「不含组」为空，")
    print("      这 16 格被 V109 **整组剔除**（那 28 个空组里最厚的一块）。")
    print("      V110 用实现内对照把它们捞回来：`.lower()` 在 3 个实现上效应 **+0.2**，`.strip()` 全 0。")
    print("    ⭐ ⇒ **「组间比较」在空组时不是丢掉一个数，而是丢掉一整片因果。**")

    # ---------- W3/W4 ----------
    print("\n## §9 W3 / W4（按原口径，如实报）")
    bs, n = d["w3"]["bit_same"], d["w3"]["n"]
    print(f"  W3 位图不变 {bs}/{n} = {bs / n:.1%}（押 ≥50%）⇒ "
          f"{'✅' if bs / n >= 0.5 else '⛔ 破'}")
    print("    ⚠️ 破的原因不是「混杂少」，而是**选格规则把 g7d2 的修饰性全捞进来了**（它们真起作用）。")
    print(f"  W4 注入 {d['w4']['n']} 格，下降 {d['w4']['drop']} 格 ⇒ ⛔ 押破")
    print("    但下降的两格是 `.replace` 注入到**返回 list 的 g7d2** ⇒ `AttributeError`（类型错误），")
    print("    另两格 `.strip()` 注入 g7d3（返回 str）⇒ **效应 0**。")
    print("    ⇒ ⛔ **W4 的判据形态不好**：把「类型不兼容导致的崩溃」与「语义效应」混在一个数字里。")
    print("      干净的读数是：**跨题注入一个语法/类型都兼容的修饰性惯用法，效应 = 0**（2/2 格）。")

    # ---------- 净收益 ----------
    print("\n## §10 净收益")
    n_down = sum(1 for r in rows if r["effect"] > 0)
    n_mod = sum(1 for r in mod if r["effect"] > 0)
    print(f"  1. **W1 成立**：{n_down}/{len(rows)} 格删除后下降、**0 格上升** ⇒ "
          "惯用法在实现内部**确实是原因**，不是纯相关。")
    print(f"  2. ⭐ **干净的那部分**：修饰性惯用法 {len(mod)} 格里 {n_mod} 格下降"
          f"（{n_mod / len(mod):.1%}）⇒ 「少一个惯用法就变差」有 **{n_mod} 个独立格子**作证。")
    print("  3. ⛔ **V109 的两条负迁移全部判死**：g7d3 `.strip()` −0.450（唯一证据格语法非法）、")
    print("     g7e2 `.strip()`/`.lower()` −0.800（效应全 0）。")
    print("  4. ⭐ **V109 的盲区**：空组剔除让 g7e1 的 16 格整个消失，那里有真因果（`.lower()` +0.2）。")
    print("  5. ⛔ 自踩：口径错（Δ vs −Δ）与恒真判据（N/C 对照）各一次，都登记在 PREREG/RESULTS。")

    out = dict(w2_correct=dict(same=same, opp=opp, zero=zero),
               n_rows=len(rows), n_unc=len(unc),
               n_down=n_down, n_modifier=len(mod), n_modifier_down=n_mod)
    (HERE / "verdict.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
