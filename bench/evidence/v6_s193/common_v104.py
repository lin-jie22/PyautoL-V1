#!/usr/bin/env python3
"""V104 补：**同分母**复核（修 §3 的口径隐患）。

为什么要有这个脚本
------------------
`judge_prop.py` 的微平均把 `None`（不适用）剔出分母 ⇒ **每个实现的分母不同**：
    075b4099 74/81   5755e033 79/87   8089eee6 81/81   309f1027 81/81
81 vs 87 的分母差 6 格，而两个分的差只有 0.0056 ⇒ **W3「次序相反」可能是分母造的**。
这不许拿「差得小」糊过去，必须换成**同一批格**再算一次。

做法：拿全部 4 个实现都能判的那批（性质 × 输入）格（**交集**），在同一分母上重算。
零模型、零网络：只跑 `judge_prop.py` 里现成的性质函数，输入仍是档案里的 4 个实现。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import judge_prop as J  # noqa: E402

TASK = "g7d3_fmt_amount"
HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
CELLS = [("P0", "A0", EV / "v6_s191"), ("P0", "A1", EV / "v6_s191"),
         ("P1", "A0", EV / "v6_s191"), ("P1", "A1", EV / "v6_s191"),
         ("P2", "A0", EV / "v6_s191"), ("P2", "A1", EV / "v6_s191"),
         ("P3", "A0", EV / "v6_s192"), ("P3", "A1", EV / "v6_s192"),
         ("P4", "A0", EV / "v6_s192"), ("P4", "A1", EV / "v6_s192")]


def answer_of(d: pathlib.Path, tag: str) -> str:
    p = d / "answers" / tag / "answers.jsonl"
    for ln in p.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r.get("kind") == "answer" and r.get("task_id") == TASK:
            return r.get("answer") or ""
    return ""


def score_of(d: pathlib.Path, tag: str) -> float | None:
    sp = d / "scores.json"
    if not sp.exists():
        return None
    return (json.loads(sp.read_text(encoding="utf-8")).get(tag) or {}).get(TASK)


def rows_of(code: str) -> list:
    """跑 harness 拿 (输入, 输出) 原始行 —— 与 `judge_prop.judge()` 同一段执行代码。"""
    code = J._code_of(code)
    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td) / "answer.py"
        src.write_text(code, encoding="utf-8")
        h = pathlib.Path(td) / "h.py"
        h.write_text(J.HARNESS.replace("__GRID__", repr(J.GRID))
                                 .replace("__SRC__", repr(str(src)))
                                 .replace("__FUNC__", repr(J.FUNC)), encoding="utf-8")
        p = subprocess.run([sys.executable, "-I", str(h)], capture_output=True,
                           text=True, timeout=J.TIMEOUT_S)
        if p.returncode != 0 or not p.stdout.strip():
            return []
        return json.loads(p.stdout.strip().splitlines()[-1])["rows"]


def matrix(code: str) -> dict:
    """{(性质名, 输入): True/False/None}"""
    rows = rows_of(code)
    out = {}
    for name, _desc, fn in J.PROPS:
        for x, o in rows:
            key = (name, repr(x))
            if isinstance(o, dict) and "__err__" in o:
                out[key] = False
            else:
                r = fn(x, o)
                out[key] = None if r is None else bool(r)
    return out


def micro(m: dict, common: set | None = None) -> tuple[int, int]:
    ks = [k for k in m if m[k] is not None and (common is None or k in common)]
    return sum(1 for k in ks if m[k]), len(ks)


def main() -> None:
    impls: dict[str, dict] = {}
    for p, arm, d in CELLS:
        tag = f"{arm}__{p}"
        code = answer_of(d, tag)
        h = hashlib.sha256(code.encode()).hexdigest()[:8]
        rec = impls.setdefault(h, {"code": code, "tests": set()})
        sc = score_of(d, tag)
        if sc is not None:
            rec["tests"].add(sc)

    mats = {h: matrix(r["code"]) for h, r in impls.items()}
    order = sorted(mats, key=lambda h: -max(impls[h]["tests"] or {0}))

    print("## §7 分母核对（微平均的分母是**逐实现**的，因为 None=不适用 被剔出）")
    print(f"  {'实现':10s} {'tests':>6s} {'通过/分母':>12s} {'分母':>5s}")
    for h in order:
        np_, nt = micro(mats[h])
        print(f"  {h:10s} {str(sorted(impls[h]['tests'])):>6s} {f'{np_}/{nt}':>12s} {nt:>5d}")
    dens = {micro(mats[h])[1] for h in mats}
    print(f"  ⇒ 分母集合 = {sorted(dens)} ⇒ "
          + ("⛔ **分母不齐**，跨实现比大小不成立" if len(dens) > 1 else "分母齐，可直接比"))

    # 交集：4 个实现都判得了的格
    common = set.intersection(*[{k for k, v in mats[h].items() if v is not None} for h in mats])
    allcells = set.intersection(*[set(mats[h]) for h in mats])
    print(f"\n## §8 同分母复核（取 {len(mats)} 个实现都能判的**交集格**）")
    print(f"  全网格格数 = {len(allcells)}　交集可判格数 = {len(common)}（同一分母）")
    print(f"  {'实现':10s} {'tests':>6s} {'微(本实现分母)':>16s} {'微(交集分母)':>14s}")
    same = {}
    for h in order:
        a, b = micro(mats[h])
        c, d = micro(mats[h], common)
        same[h] = round(c / d, 4) if d else None
        print(f"  {h:10s} {str(sorted(impls[h]['tests'])):>6s} "
              f"{f'{a}/{b}={a/b:.4f}':>16s} {f'{c}/{d}={c/d:.4f}':>14s}")

    print("\n## §9 判决（用同分母口径重判 W1 / W3）")
    lo = [h for h in same if impls[h]["tests"] == {0.4}]
    mid = [h for h in same if impls[h]["tests"] == {0.6}]
    hi = [h for h in same if impls[h]["tests"] == {1.0}]
    print(f"  tests=0.4 组：{[(h, same[h]) for h in lo]}")
    print(f"  tests=0.6 组：{[(h, same[h]) for h in mid]}")
    print(f"  tests=1.0 组：{[(h, same[h]) for h in hi]}")
    if lo and hi:
        print(f"  W1：0.4 组上限 {max(same[h] for h in lo)} ｜ 1.0 组下限 {min(same[h] for h in hi)} ⇒ "
              + ("**仍分开了**" if max(same[h] for h in lo) < min(same[h] for h in hi) else "⛔ 同分母下没分开"))
    if lo and mid:
        print(f"  W3：0.4 组上限 {max(same[h] for h in lo)} ｜ 0.6 组 {min(same[h] for h in mid)} ⇒ "
              + ("**次序相反（同分母下依然相反）**" if max(same[h] for h in lo) > min(same[h] for h in mid)
                 else "次序一致（改为分开了）"))

    print("\n## §10 逐格差（两个满分实现）")
    print("  ⚠️ 「交集内 0 格不同」只说明**判据看不见差别**，不等于「行为相同」——")
    print("     它俩都 81/81 全过，所以在交集上必然逐格相等（这是恒真，不是发现）。")
    for a in hi:
        for b in hi:
            if a >= b:
                continue
            diff = [k for k in common if mats[a][k] != mats[b][k]]
            print(f"  {a} vs {b}：交集内不同格 {len(diff)} 个 {diff[:6]}")

    print("\n## §11 留一性质法：判别力是不是全靠一条性质")
    print("  对每条性质，把它剔掉后用同分母重算，看 W1（0.4 组 < 1.0 组）还成不成立。")
    names = [n for n, _d, _f in J.PROPS]
    base_lo = max(same[h] for h in lo) if lo else None
    base_hi = min(same[h] for h in hi) if hi else None
    print(f"  {'剔除的性质':22s} {'0.4 组':>8s} {'1.0 组':>8s}  W1")
    load_bearing = []
    for drop in names:
        sub = {k: v for k, v in common.items() if k[0] != drop} if False else \
              {k for k in common if k[0] != drop}
        vals = {}
        for h in same:
            c, d = micro(mats[h], sub)
            vals[h] = round(c / d, 4) if d else None
        vlo = max(vals[h] for h in lo) if lo else None
        vhi = min(vals[h] for h in hi) if hi else None
        ok = (vlo is not None and vhi is not None and vlo < vhi)
        gap = (vhi - vlo) if (vlo is not None and vhi is not None) else None
        print(f"  {'−' + drop:22s} {str(vlo):>8s} {str(vhi):>8s}  "
              + ("成立" if ok else "⛔ **崩了**") + (f"　间隙 {gap:.4f}" if gap is not None else ""))
        if not ok:
            load_bearing.append(drop)
    print(f"  ⇒ 剔掉就崩的性质（**判别力全部所在**）：{load_bearing or '无（每条都不单独承载）'}")
    print("  ⭐ 读法：若只有 1 条，则整套判据的判别力 = 那一条性质，其余 8 条是装饰。")
    print(f"  基准间隙（全 9 条）= {base_hi - base_lo:.4f}" if base_lo is not None and base_hi is not None else "")


if __name__ == "__main__":
    main()
