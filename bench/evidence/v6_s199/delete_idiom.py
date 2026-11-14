#!/usr/bin/env python3
"""V110：删除式干预 —— 惯用法是不是「原因」。

零模型、零网络。每个变体一次 `bench/scorers/code_exec.py` 子进程。

§0  基线（26 个实现，与 V109 逐格对齐 = 校准）
§1  删除式干预：每个实现的每个惯用法调用点单独删除，重算分
§2  位图解释（对 V109 报过 ±0 的格子 + Δ≠0 的格子）
§3  注入式干预（W4）：在返回值上盲套 `.method(...)`
§4  判决 W1/W2/W3/W4
"""
from __future__ import annotations

import collections
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
ROOT = EV.parent.parent
SCORER = ROOT / "bench" / "scorers" / "code_exec.py"

sys.path.insert(0, str(EV / "v6_s198"))
import idiom_transfer as I  # noqa: E402  （复用 V109 的 harvest / task_of / code_of）

#: 与 code_exec 同一口径的调用链正则（不支持嵌套括号 —— 语料里没有）
_CALL_POS = re.compile(r"\.(\w+)\(([^()]*)\)")

TIMEOUT = 8.0


def score_of(tid: str, code: str) -> float | None:
    t = I.task_of(tid)
    if t is None:
        return None
    pt = {"id": t["id"], "prompt": t.get("prompt", ""), "tests": t["tests"]}
    payload = json.dumps({"task": pt, "answer": code}, ensure_ascii=False)
    p = subprocess.run([sys.executable, str(SCORER)], input=payload,
                       capture_output=True, text=True, timeout=120)
    if not p.stdout.strip():
        return None
    return json.loads(p.stdout.strip().splitlines()[-1]).get("score")


def compiles(code: str) -> bool:
    try:
        compile(code, "<cand>", "exec")
        return True
    except SyntaxError:
        return False


def deletions(code: str):
    """产出 (惯用法, start, end)。⛔ 格式说明符 `{:,.2f}` 不参与删除（删了代码就崩）。"""
    for m in _CALL_POS.finditer(code):
        yield f".{m.group(1)}({m.group(2).strip()})", m.start(), m.end()


def bitmap(tid: str, code: str) -> tuple:
    """逐条 test 的 rc 位图（与 code_exec 同构：-I + 独立临时 cwd + timeout）。"""
    t = I.task_of(tid)
    tests = [str(x) for x in t["tests"]]
    bits = []
    for test in tests:
        src = f"{code}\n\n{test}\n"
        wd = tempfile.mkdtemp(prefix="v110_")
        try:
            p = subprocess.run([sys.executable, "-I", "-c", src], capture_output=True,
                               text=True, timeout=TIMEOUT, cwd=wd)
            bits.append(1 if p.returncode == 0 else 0)
        except subprocess.TimeoutExpired:
            bits.append(-1)
        finally:
            shutil.rmtree(wd, ignore_errors=True)
    return tuple(bits)


def main() -> None:
    corpus = I.harvest()
    tasks = sorted(corpus)

    # ---------- §0 基线 ----------
    print("## §0 基线（与 V109 逐格对齐 = 校准）")
    v109 = json.loads((EV / "v6_s198" / "idioms.json").read_text(encoding="utf-8"))["scores"]
    base: dict[str, dict[str, float]] = {}
    drift = []
    for tid in tasks:
        d = {}
        for h, code in sorted(corpus[tid].items()):
            s = score_of(tid, code)
            if s is None:
                continue
            d[h] = s
            old = v109.get(tid, {}).get(h)
            if old is not None and abs(old - s) > 1e-9:
                drift.append((tid, h, old, s))
        base[tid] = d
        print(f"  {tid:22s} {len(d)} 个实现　{collections.Counter(d.values())}")
    print(f"  ⇒ 与 V109 不一致的格子 = {len(drift)} 个 {drift[:5]}"
          f"　{'✅ 校准通过' if not drift else '⛔ 基线漂移，后续所有 Δ 作废'}")

    # ---------- §1 删除式干预 ----------
    print("\n## §1 删除式干预（逐调用点单独删除）")
    rows = []          # 每格一行
    for tid in tasks:
        for h, code in sorted(corpus[tid].items()):
            s0 = base[tid].get(h)
            if s0 is None:
                continue
            for idiom, a, b in deletions(code):
                mut = code[:a] + code[b:]
                if not compiles(mut):
                    rows.append(dict(tid=tid, h=h, idiom=idiom, pos=a, ok=False,
                                     s0=s0, s1=None, delta=None, note="UNCOMPILABLE"))
                    continue
                s1 = score_of(tid, mut)
                rows.append(dict(tid=tid, h=h, idiom=idiom, pos=a, ok=True,
                                 s0=s0, s1=s1, delta=None if s1 is None else round(s1 - s0, 4),
                                 note=""))
    ok_rows = [r for r in rows if r["ok"] and r["delta"] is not None]
    unc = [r for r in rows if not r["ok"]]
    print(f"  删除格子总数 = {len(rows)}（可编译 {len(ok_rows)}，不可编译 {len(unc)}）")
    print(f"  ⛔ 格式说明符（`{{:...}}`）不参与删除：语料里共 "
          f"{sum(code.count('{:') for tid in tasks for code in corpus[tid].values())} 处，"
          f"删掉会让代码崩，单列不测")
    print("  逐格：")
    for r in rows:
        tag = "UNCOMP" if not r["ok"] else f"{r['s0']:.1f}→{r['s1']:.1f}"
        print(f"    {r['tid'].split('_')[0]:6s} {r['h']} {r['idiom']:26s} @{r['pos']:4d} "
              f"{tag:12s} Δ={r['delta'] if r['delta'] is not None else 'NA'}")

    dist = collections.Counter(r["delta"] for r in ok_rows)
    print(f"\n  Δ 分布（分母 = {len(ok_rows)} 格）：{dict(sorted(dist.items(), key=lambda kv: str(kv[0])))}")

    # ---------- §2 位图解释 ----------
    print("\n## §2 位图解释（逐条 test 的 rc）")
    # 只对 V109 报过 ±0 的（题, 惯用法）与 Δ≠0 的格子跑位图，控制次数
    v109u = json.loads((EV / "v6_s198" / "idioms.json").read_text(encoding="utf-8"))["uplift"]
    want_idioms = {i for i, d in v109u.items() if any(abs(v) > 1e-9 for v in d.values())}
    pick = [r for r in ok_rows if r["idiom"] in want_idioms or (r["delta"] or 0) != 0]
    print(f"  选格规则：V109 报过 ±0 的惯用法 {sorted(want_idioms)}，或 Δ≠0"
          f"　⇒ 共 {len(pick)} 格（分母写在这里）")
    bit_same = 0
    for r in pick:
        code = corpus[r["tid"]][r["h"]]
        b0 = bitmap(r["tid"], code)
        mut = code[:r["pos"]] + code[r["pos"] + len(r["idiom"]):]
        b1 = bitmap(r["tid"], mut) if compiles(mut) else None
        same = b1 == b0
        bit_same += 1 if same else 0
        r["bit_same"] = same
        r["b0"] = b0
        r["b1"] = b1
        print(f"    {r['tid'].split('_')[0]:6s} {r['h']} {r['idiom']:26s} "
              f"位图 {b0} → {b1}　{'不变' if same else '变了'}　Δ={r['delta']}")
    n_bit = len(pick)
    print(f"  ⇒ 位图不变的格子 {bit_same}/{n_bit}"
          + (f" = {bit_same / n_bit:.1%}" if n_bit else "（分母 0，不可判）"))
    print("  ⚠️ 位图不变 ⇒ `score = passed/n` 必然不变，**这是恒真**，"
          "所以这一行只报占比，不当判据强度用（PREREG §2 的修正）")

    # ---------- §3 注入式干预（W4） ----------
    print("\n## §3 注入式干预（W4）：在返回值上盲套惯用法")
    inj_rows = []
    for tid, targets, idiom in (
        ("g7d3_fmt_amount", ["0a6acc93", "17747356"], ".strip()"),
        ("g7d2_dedupe_keep", ["480ada8e", "74aa48d8"], ".replace(',', ' ')"),
    ):
        for h in targets:
            code = corpus[tid].get(h)
            if code is None:
                print(f"    ⛔ {tid}/{h} 不在语料里")
                continue
            s0 = base[tid].get(h)
            mut, n = inject_returns(code, idiom)
            s1 = score_of(tid, mut) if compiles(mut) else None
            inj_rows.append(dict(tid=tid, h=h, idiom=idiom, n=n, s0=s0, s1=s1,
                                 delta=None if s1 is None else round(s1 - s0, 4)))
            print(f"    {tid.split('_')[0]:6s} {h} 注入 {idiom:22s} ×{n}　"
                  f"{s0} → {s1}　Δ={inj_rows[-1]['delta']}")

    # ---------- §4 判决 ----------
    print("\n## §4 判决")
    downs = [r for r in ok_rows if (r["delta"] or 0) < 0]
    ups = [r for r in ok_rows if (r["delta"] or 0) > 0]
    print(f"\n  **W1 必要性存在**（分母 = 可编译删除格 {len(ok_rows)}）")
    print(f"    下降 {len(downs)} 格、上升 {len(ups)} 格、不变 {len(ok_rows) - len(downs) - len(ups)} 格")
    print(f"    ⇒ {'✅ 成立' if downs else '⛔ 破：没有任何一格因删除而下降'}")
    if downs:
        for r in downs[:12]:
            print(f"       {r['tid'].split('_')[0]} {r['h']} {r['idiom']} Δ={r['delta']}")

    print(f"\n  **W2 V109 符号可复现**（分母 = V109 报过 ±0 的（题, 惯用法）格子）")
    per = collections.defaultdict(list)
    for r in ok_rows:
        if r["idiom"] in want_idioms:
            per[(r["tid"], r["idiom"])].append(r["delta"])
    same_sign = zero = opp = 0
    for (tid, idiom), ds in sorted(per.items()):
        u = v109u[idiom].get(tid)
        if u is None or abs(u) < 1e-9:
            continue
        m = sum(ds) / len(ds)
        rel = "ZERO" if abs(m) < 1e-9 else ("同号" if (m > 0) == (u > 0) else "异号")
        if rel == "ZERO":
            zero += 1
        elif rel == "同号":
            same_sign += 1
        else:
            opp += 1
        print(f"    {tid.split('_')[0]:6s} {idiom:24s} V109={u:+.3f}　本轮 Δ均值={m:+.4f}（{len(ds)} 格）　{rel}")
    print(f"    ⇒ 同号 {same_sign} / 异号 {opp} / ZERO {zero}"
          f"　{'✅ 押中（不同号为主）' if opp > same_sign else ('⛔ 破' if same_sign > opp else '⛔ UNKNOWN')}")

    print(f"\n  **W3 可证明混杂占比**（分母 = 位图选格 {n_bit}）")
    print(f"    位图不变 {bit_same}/{n_bit} = {bit_same / n_bit:.1%}"
          if n_bit else "    分母 0，不可判")
    print(f"    ⇒ {'✅ 押中（≥50%）' if n_bit and bit_same / n_bit >= 0.5 else '⛔ 破或不可判'}")
    print("    ⚠️ 恒真提醒：位图不变 ⇒ 分数不变，所以「不变」不等于「惯用法无用」，"
          "只等于「在这 5 个离散分档上量不到」（PREREG §5.5）")

    print(f"\n  **W4 充分性**（分母 = {len(inj_rows)} 格）")
    drops = [r for r in inj_rows if (r["delta"] or 0) < 0]
    print(f"    注入后下降 {len(drops)} 格、不变 {sum(1 for r in inj_rows if r['delta'] == 0)} 格、"
          f"其他 {len(inj_rows) - len(drops) - sum(1 for r in inj_rows if r['delta'] == 0)} 格")
    print(f"    ⇒ {'⛔ 破：注入在新题上也有因果作用' if drops else '✅ 押中（不下降）'}")

    out = dict(base=base, rows=rows, inj=inj_rows,
               dist={str(k): v for k, v in dist.items()},
               w1=dict(down=len(downs), up=len(ups), n=len(ok_rows)),
               w2=dict(same=same_sign, opp=opp, zero=zero),
               w3=dict(bit_same=bit_same, n=n_bit),
               w4=dict(drop=len(drops), n=len(inj_rows)))
    (HERE / "delete.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def inject_returns(code: str, suffix: str) -> tuple[str, int]:
    """把每一条 `return EXPR` 的 EXPR 包起来再套 suffix。返回 (新代码, 注入条数)。"""
    out, n = [], 0
    for line in code.splitlines():
        m = re.search(r"(\breturn\s+)(.+)$", line)
        if m and not line.lstrip().startswith("#"):
            expr = m.group(2).strip()
            line = line[:m.start()] + m.group(1) + f"({expr}){suffix}"
            n += 1
        out.append(line)
    return "\n".join(out) + "\n", n


if __name__ == "__main__":
    main()
