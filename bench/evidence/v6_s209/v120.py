#!/usr/bin/env python3
"""V120：把「加」补成「加 + 减/改」。

三个臂，逐级加一类动作：
  A'（对照）= V119 臂 B（T1~T4 + 库链）
  C1        = A' + 表达式级移植（库内同题已满分实现的第 i 个 return 表达式，**无自由变量**）
  C2        = C1 + 语句删除（删一条 `Name = expr`）

零模型、零网络、零子进程。判据 = 现有 test 同一批。
"""
from __future__ import annotations

import ast
import builtins
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
sys.path.insert(0, str(EV / "v6_s205"))
sys.path.insert(0, str(EV / "v6_s208"))
sys.path.insert(0, str(EV / "v6_s198"))
import search as S            # noqa: E402
import idiom_transfer as I     # noqa: E402
import v119 as V              # noqa: E402

TIDS = V.TIDS
MAX_ROUNDS = 6
BUILTINS = set(dir(builtins))
ev = V.ev


# ---------------------------------------------------------------- 表达式移植
def _params(code: str) -> set[str]:
    for n in ast.walk(ast.parse(code)):
        if isinstance(n, ast.FunctionDef):
            a = n.args
            return {x.arg for x in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)}
    return set()


def _returns(code: str) -> list:
    t = ast.parse(code)
    rs = [n for n in ast.walk(t) if isinstance(n, ast.Return) and n.value is not None]
    return sorted(rs, key=lambda n: (n.lineno, n.col_offset))


def _free_names(expr, params: set[str]) -> set[str]:
    return {n.id for n in ast.walk(expr) if isinstance(n, ast.Name)} - params - BUILTINS


def harvest_exprs(corpus: dict, tid: str, exclude: str) -> list[str]:
    """同题、**已判满分**的兄弟实现里，第 i 个 return 的表达式（只收无自由变量的）。"""
    out: list[str] = []
    for h, code in corpus[tid].items():
        if h == exclude:
            continue
        try:
            if ev(tid, code)[0] < 1.0:
                continue
            params = _params(code)
            for r in _returns(code):
                if _free_names(r.value, params):
                    continue
                src = ast.unparse(r.value)
                if src not in out:
                    out.append(src)
        except SyntaxError:
            continue
    return out


def candidates_C1(code: str, exprs: list[str]) -> list[tuple[str, str]]:
    out = []
    try:
        my = _returns(code)
    except SyntaxError:
        return out
    for i in range(len(my)):
        for e in exprs:
            try:
                t = ast.parse(code)
                rs = sorted([n for n in ast.walk(t) if isinstance(n, ast.Return) and n.value is not None],
                            key=lambda n: (n.lineno, n.col_offset))
                rs[i].value = ast.parse(e, mode="eval").body
                out.append((f"C1:ret{i}:={e}", ast.unparse(ast.fix_missing_locations(t))))
            except Exception:                   # noqa: BLE001
                pass
    return out


# ---------------------------------------------------------------- 语句删除
def candidates_C2(code: str) -> list[tuple[str, str]]:
    out = []
    try:
        t = ast.parse(code)
    except SyntaxError:
        return out
    idx = []
    for fn in ast.walk(t):
        if isinstance(fn, ast.FunctionDef):
            for j, st in enumerate(fn.body):
                if isinstance(st, ast.Assign) and len(st.targets) == 1 \
                        and isinstance(st.targets[0], ast.Name):
                    idx.append((fn, j))
    for k, (fn, j) in enumerate(idx):
        try:
            t2 = ast.parse(code)
            fns = [n for n in ast.walk(t2) if isinstance(n, ast.FunctionDef)]
            # 按函数名 + 序号定位（语料都是单函数）
            del fns[0].body[j]
            if not fns[0].body:
                fns[0].body.append(ast.Pass())
            out.append((f"C2:del{k}", ast.unparse(ast.fix_missing_locations(t2))))
        except Exception:                       # noqa: BLE001
            pass
    return out


def beam(tid: str, code: str, Sx, chains, exprs, k: int, arm: str) -> dict:
    s0, _ = ev(tid, code)
    frontier = [dict(code=code, s=s0, lab="<baseline>")]
    visited = {code}
    n_eval = 0
    trail = []
    for _ in range(MAX_ROUNDS):
        pool, seen_src = [], set()
        for node in frontier:
            cands = list(S.candidates(node["code"], Sx))
            if arm in ("A", "C1", "C2"):
                cands += V.candidates_B(node["code"], chains)
            if arm in ("C1", "C2"):
                cands += candidates_C1(node["code"], exprs)
            if arm == "C2":
                cands += candidates_C2(node["code"])
            for lab, src in cands:
                if src in visited or src in seen_src:
                    continue
                s, _ = ev(tid, src)
                n_eval += 1
                if s > node["s"]:
                    pool.append(dict(code=src, s=s, lab=lab))
                    seen_src.add(src)
        if not pool:
            trail.append(dict(n_pool=0, kept=[]))
            break
        pool.sort(key=lambda d: -d["s"])
        kept = pool[:k]
        visited |= {d["code"] for d in pool}
        trail.append(dict(n_pool=len(pool), kept=[d["lab"] for d in kept], s=[d["s"] for d in kept]))
        frontier = kept
        if max(d["s"] for d in frontier) >= 1.0:
            break
    best = max(frontier, key=lambda d: d["s"])
    return dict(s0=s0, s1=best["s"], lab=best["lab"], n_eval=n_eval, trail=trail)


def main() -> None:
    corpus = I.harvest()
    res: dict[str, dict] = {}
    skipped = []
    for tid in TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                skipped.append((tid, h))
                continue
            Sx, _ = S.wordset(tid, corpus[tid][h], True)
            chains = V.harvest_chains(corpus, tid, h)
            exprs = harvest_exprs(corpus, tid, h)
            for arm in ("A", "C1", "C2"):
                r = beam(tid, corpus[tid][h], Sx, chains, exprs, 2, arm)
                r.update(tid=tid, h=h, n_exprs=len(exprs), exprs=exprs)
                res[f"{arm}:{tid}::{h}"] = r

    def fixed(arm, tid=None):
        return [d for key, d in res.items() if key.startswith(f"{arm}:")
                and d["s1"] >= 1.0 and d["s0"] < 1.0 and (tid is None or d["tid"] == tid)]

    # ---------------- §0
    print("## §0 移植源规模（同题已满分、无自由变量的 return 表达式）")
    for tid in TIDS:
        ns = sorted({res[f"A:{tid}::{h}"]["n_exprs"] for h in corpus[tid]
                     if f"A:{tid}::{h}" in res})
        ex = next((res[f"A:{tid}::{h}"]["exprs"] for h in sorted(corpus[tid])
                   if f"A:{tid}::{h}" in res), [])
        print(f"  {tid:22s} n_exprs = {ns}　样本：{ex[:3]}")
    print(f"  n_impl_skipped = {len(skipped)}：{skipped}")

    # ---------------- §1
    print("\n## §1 三臂的分数向量（k=2；分母写进标题）")
    for tid in TIDS:
        hs = sorted({d["h"] for d in res.values() if d["tid"] == tid})
        print(f"\n  **{tid}**（n_impl = {len(hs)}）")
        base = [res[f"A:{tid}::{h}"]["s0"] for h in hs]
        print(f"    基线 : {base}　到 1.0 的 {sum(1 for v in base if v >= 1.0)}/{len(hs)}")
        for arm in ("A", "C1", "C2"):
            vec = [res[f"{arm}:{tid}::{h}"]["s1"] for h in hs]
            print(f"    {arm:2s}   : {vec}　到 1.0 的 {sum(1 for v in vec if v >= 1.0)}/{len(hs)}")

    # ---------------- §2
    print("\n## §2 判决")
    n_par = sum(1 for t in TIDS for h in corpus[t] if (t, h) not in skipped)
    base_full = sum(1 for t in TIDS for h in corpus[t]
                    if (t, h) not in skipped and res[f"A:{t}::{h}"]["s0"] >= 1.0)
    print(f"  n_impl_total = {sum(len(v) for v in corpus.values())}｜n_impl_parseable = {n_par}"
          f"｜n_impl_base_full = {base_full}｜n_impl_needing_work = {n_par - base_full}")

    e2A = {d["h"] for d in fixed("A", "g7e2_truncate_tail")}
    e2C1 = {d["h"] for d in fixed("C1", "g7e2_truncate_tail")}
    new_e2 = e2C1 - e2A
    w1 = new_e2 == {"8184ec4d", "bc8c4b09"}
    print(f"\n  **W1 C1 修好 g7e2 恰好 2 个（{sorted(new_e2)}）** ⇒ {'✅ 押中' if w1 else '⛔ 押破'}")
    for hh in sorted(new_e2):
        print(f"      {hh} 生效动作 = {res[f'C1:g7e2_truncate_tail::{hh}']['lab']}")

    d3A = {d["h"] for d in fixed("A", "g7d3_fmt_amount")}
    d3C1 = {d["h"] for d in fixed("C1", "g7d3_fmt_amount")}
    w2 = (d3C1 - d3A) == set()
    print(f"  **W2 C1 在 g7d3 上新增 0** ⇒ {'✅ 押中' if w2 else '⛔ 押破'}"
          f"（新增 {sorted(d3C1 - d3A)}）")
    print(f"      g7d3 的移植源：{[res[f'A:g7d3_fmt_amount::{h}']['exprs'] for h in sorted(corpus['g7d3_fmt_amount']) if f'A:g7d3_fmt_amount::{h}' in res][:2]}")

    dmg = [(d["tid"], d["h"], d["s1"]) for key, d in res.items() if key.startswith("C1:")
           and d["s0"] >= 1.0 and d["s1"] < 1.0]
    a_set = {(d["tid"], d["h"]) for d in fixed("A")}
    c1_set = {(d["tid"], d["h"]) for d in fixed("C1")}
    new_oth = {(t, h) for t, h in c1_set - a_set if t in ("g7d2_dedupe_keep", "g7e1_parse_flag")}
    w3 = not new_oth and not dmg
    print(f"  **W3 C1 在其他题上新增 0 且不损害** ⇒ {'✅ 押中' if w3 else '⛔ 押破'}"
          f"（其他题新增 {sorted(new_oth)}、受损 {dmg}）")

    c2_new = {(d["tid"], d["h"]) for d in fixed("C2")} - c1_set
    w4 = not c2_new
    print(f"  **W4 C2 的语句删除单独修好 0 个** ⇒ {'✅ 押中' if w4 else '⛔ 押破'}"
          f"（新增 {sorted(c2_new)}）")
    print("      ⇒ 自我更正：V119 §187.4 的「删语句」诊断判错，「换表达式」才对。")

    tot = {arm: len(fixed(arm)) for arm in ("A", "C1", "C2")}
    tot["按对象"] = base_full + len(fixed("C1"))
    print(f"\n  **W5 区域 = 10 + {len(fixed('C1'))} = {base_full + len(fixed('C1'))}/{n_par}；g7d3 仍 4/8**")
    n_d3 = sum(1 for h in corpus["g7d3_fmt_amount"]
               if f"C1:g7d3_fmt_amount::{h}" in res and res[f"C1:g7d3_fmt_amount::{h}"]["s1"] >= 1.0)
    w5 = (base_full + len(fixed("C1")) == 21) and n_d3 == 4
    print(f"    修好数：A′={tot['A']}｜C1={tot['C1']}｜C2={tot['C2']}｜g7d3 到 1.0 的 = {n_d3}/8"
          f" ⇒ {'✅ 押中' if w5 else '⛔ 押破'}")

    dmg_all = [(a, d["tid"], d["h"]) for a in ("A", "C1", "C2") for key, d in res.items()
               if key.startswith(f"{a}:") and d["s0"] >= 1.0 and d["s1"] < 1.0]
    print(f"  **W6 所有臂在基线满分实现上不降** ⇒ {'✅ 押中' if not dmg_all else '⛔ 押破'}（{dmg_all}）")

    print("\n## §3 仍然不可达的实现（区域边界）")
    for tid in TIDS:
        for h in sorted({d["h"] for d in res.values() if d["tid"] == tid}):
            d = res[f"C2:{tid}::{h}"]
            if d["s1"] < 1.0 and d["s0"] < 1.0:
                print(f"  {tid:22s} {h} s0={d['s0']:.1f} → C2 最高 {d['s1']:.1f}")

    (HERE / "search.json").write_text(json.dumps(dict(res=res, skipped=skipped),
                                                 ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  原始读数已落盘 `search.json`")


if __name__ == "__main__":
    main()
