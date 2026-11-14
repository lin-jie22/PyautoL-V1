#!/usr/bin/env python3
"""V118：区域闭合的天花板是「几跳」。

零模型、零网络、零子进程。判据 = 现有 test（同一批）。
动作空间 = `v6_s205/search.py` 的 T1~T4，一字不改。

§A 深度 1 天花板（全 13 个实现）
§B 深度 2 可达性（未满分实现，穷举 BFS，不剪枝）
§C 缺口形状诊断（g7d2 两个卡点：单站点天花板 + 多站点存在性）
§D 判决 W1~W5
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
sys.path.insert(0, str(EV / "v6_s205"))
sys.path.insert(0, str(EV / "v6_s198"))
import search as S           # noqa: E402
import idiom_transfer as I    # noqa: E402

TIDS = ["g7e1_parse_flag", "g7d2_dedupe_keep"]
CACHE: dict[tuple[str, str], tuple[float, tuple]] = {}


def ev(tid: str, code: str) -> tuple[float, tuple]:
    key = (tid, code)
    if key in CACHE:
        return CACHE[key]
    bm = tuple(S.bitmap(tid, code))
    sc = sum(1 for x in bm if x is True) / len(bm)
    CACHE[key] = (sc, bm)
    return sc, bm


def _wrapped(expr, w):
    """`w` 形如 "strip().lower" ⇒ 逐级包成 `x.strip().lower()`。

    ⛔ 第一版用 `w.split(".")` 会切出 `["strip()", "lower"]` ⇒ 生成 `x.strip()().lower()`
    ⇒ 全部 EXC:TypeError（读数 0.2）。这里先剥掉 `()` 再切。
    """
    e = expr
    for part in w.replace("()", "").split("."):
        e = ast.Call(func=ast.Attribute(value=e, attr=part, ctx=ast.Load()),
                     args=[], keywords=[])
    return e


# ---------------------------------------------------------------- §C 多站点动作
def key_consistency(code: str, wrap: str) -> list[tuple[str, str]]:
    """把「同一变量的键用途」全部归一化：比较主语 + 同容器 add 的实参。

    站点集由**数据流**决定（变量名 + 容器名相等），不由人逐格指定。
    """
    out = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return out
    nodes = list(ast.walk(tree))
    pairs = set()
    for n in nodes:
        if isinstance(n, ast.Compare) and isinstance(n.left, ast.Name) \
                and any(isinstance(o, (ast.In, ast.NotIn)) for o in n.ops):
            for c in n.comparators:
                if isinstance(c, ast.Name):
                    pairs.add((n.left.id, c.id))
    for var, cont in sorted(pairs):
        idx_left, idx_arg = [], []
        for i, n in enumerate(nodes):
            if isinstance(n, ast.Compare) and isinstance(n.left, ast.Name) \
                    and n.left.id == var \
                    and any(isinstance(o, (ast.In, ast.NotIn)) for o in n.ops):
                idx_left.append(i)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "add" and isinstance(n.func.value, ast.Name) \
                    and n.func.value.id == cont and len(n.args) == 1 \
                    and isinstance(n.args[0], ast.Name) and n.args[0].id == var:
                idx_arg.append(i)
        if not (idx_left and idx_arg):
            continue
        try:
            t2 = ast.parse(code)
            n2 = list(ast.walk(t2))
            for i in idx_left:
                n2[i].left = _wrapped(n2[i].left, wrap)
            for i in idx_arg:
                n2[i].args[0] = _wrapped(n2[i].args[0], wrap)
            src = ast.unparse(ast.fix_missing_locations(t2))
            out.append((f"T5:keycons({var},{cont}):{wrap}", src))
        except Exception:                       # noqa: BLE001
            pass
    return out


def main() -> None:
    corpus = I.harvest()
    res: dict = {}

    # ---------------------------------------------------------------- §A
    print("## §A 深度 1 天花板（词表 = ON，模板集 T1~T4 原样）")
    for tid in TIDS:
        n = len(corpus[tid])
        print(f"\n  **{tid}**（n_impl = {n}）")
        rows = []
        for h in sorted(corpus[tid]):
            base = corpus[tid][h]
            s0, bm0 = ev(tid, base)
            Sx, _ = S.wordset(tid, base, True)
            cands = S.candidates(base, Sx)
            best, best_lab, best_bm = s0, "<baseline>", bm0
            for lab, src in cands:
                s, bm = ev(tid, src)
                if s > best:
                    best, best_lab, best_bm = s, lab, bm
            rows.append(dict(h=h, s0=s0, d1=best, lab=best_lab,
                             bm0=list(bm0), bm1=list(best_bm), n_cand=len(cands)))
            print(f"    {h} s0={s0:.1f} → max_d1=**{best:.1f}**　|候选|={len(cands):3d}　"
                  f"最佳动作={best_lab}")
        res[tid] = rows

    # ---------------------------------------------------------------- §B
    print("\n## §B 深度 2 可达性（穷举，不剪枝；问 1.0 是否在 2 跳内）")
    STUCK = [(t, r["h"]) for t in TIDS for r in res[t] if r["s0"] < 1.0]
    bfs: list[dict] = []
    for tid, h in STUCK:
        base = corpus[tid][h]
        s0, _ = ev(tid, base)
        Sx, _ = S.wordset(tid, base, True)
        L1 = [src for _, src in S.candidates(base, Sx)]
        seen = set(L1) | {base}
        max1 = max([s0] + [ev(tid, s)[0] for s in L1])
        max2, n2, path2 = max1, 0, None
        for src in L1:
            for lab, s2 in S.candidates(src, Sx):
                if s2 in seen:
                    continue
                seen.add(s2)
                n2 += 1
                sc, bm = ev(tid, s2)
                if sc > max2:
                    max2, path2 = sc, (lab, bm)
        bfs.append(dict(tid=tid, h=h, s0=s0, max_d1=max1, max_d2=max2,
                        n_l1=len(L1), n_l2=n2, reach1=(max2 >= 1.0)))
        print(f"    {tid[:6]} {h} s0={s0:.1f}｜max_d1=**{max1:.1f}**（|L1|={len(L1)}）"
              f"｜max_d2=**{max2:.1f}**（新展开 {n2}）"
              f"｜{'✅ 2 跳内到 1.0' if max2 >= 1.0 else '⛔ 2 跳内到不了 1.0'}")

    # ---------------------------------------------------------------- §C
    print("\n## §C 缺口形状诊断（g7d2 的两个卡点）")
    diag = {}
    for h in sorted(corpus["g7d2_dedupe_keep"]):
        base = corpus["g7d2_dedupe_keep"][h]
        s0, bm0 = ev("g7d2_dedupe_keep", base)
        if s0 >= 1.0:
            continue
        Sx, _ = S.wordset("g7d2_dedupe_keep", base, True)
        print(f"\n  **{h}**（s0={s0:.1f}，位图 {[1 if x is True else 0 for x in bm0]}）")
        print("    单站点候选（全部 T1~T4）：")
        rows = []
        for lab, src in S.candidates(base, Sx):
            sc, bm = ev("g7d2_dedupe_keep", src)
            rows.append((sc, lab, bm))
            print(f"      {lab:28s} → {sc:.1f}　位图 {[1 if x is True else 0 for x in bm]}")
        single_max = max([s0] + [r[0] for r in rows])
        print(f"    单站点天花板 = **{single_max:.1f}**")
        print("    多站点动作（同变量键用途一起归一化，站点集由数据流决定）：")
        multi = []
        for lab, src in key_consistency(base, "strip().lower"):
            sc, bm = ev("g7d2_dedupe_keep", src)
            multi.append((sc, lab, bm))
            print(f"      {lab:34s} → {sc:.1f}　位图 {[1 if x is True else 0 for x in bm]}")
        multi_max = max([s0] + [m[0] for m in multi]) if multi else s0
        diag[h] = dict(s0=s0, single_max=single_max, multi_max=multi_max,
                       n_single=len(rows), n_multi=len(multi))
        print(f"    多站点天花板 = **{multi_max:.1f}**")

    # ---------------------------------------------------------------- §D
    print("\n## §D 判决")
    e1 = res["g7e1_parse_flag"]
    d2 = res["g7d2_dedupe_keep"]

    # ⛔ 作用域按预注册逐字：只算 s0 = 0.4 的那 4 个。第一版用 `s0 < 1.0` 把 0.6 的
    #    200ebf6e（1 跳就能到 1.0）也算进来 ⇒ 把押注判成破，是判据越界（纪律 32）。
    e1_04 = [r for r in e1 if abs(r["s0"] - 0.4) < 1e-9]
    w1 = len(e1_04) == 4 and all(abs(r["d1"] - 0.8) < 1e-9 for r in e1_04)
    print(f"\n  **W1 g7e1 的 4 个 0.4 卡点，max_depth1 = 0.8（无一跳达 1.0）**")
    print(f"    作用域 = s0=0.4 的 {len(e1_04)} 个：{[(r['h'], r['d1']) for r in e1_04]}"
          f" ⇒ {'✅ 押中' if w1 else '⛔ 押破'}")
    print(f"    （对照：s0=0.6 的 {[(r['h'], r['d1']) for r in e1 if abs(r['s0'] - 0.6) < 1e-9]}"
          f" —— 不在 W1 作用域内）")

    e1b = [b for b in bfs if b["tid"] == "g7e1_parse_flag"]
    w2 = bool(e1b) and all(b["reach1"] for b in e1b)
    print(f"\n  **W2 g7e1 的卡点 2 跳内可达 1.0**")
    print(f"    {[(b['h'], b['max_d2']) for b in e1b]} ⇒ {'✅ 押中' if w2 else '⛔ 押破'}")

    d2b = [b for b in bfs if b["tid"] == "g7d2_dedupe_keep"]
    w3 = bool(d2b) and all(not b["reach1"] for b in d2b)
    print(f"\n  **W3 g7d2 的两个卡点 2 跳内不可达 1.0**（本轮最要紧）")
    print(f"    {[(b['h'], b['max_d2']) for b in d2b]} ⇒ {'✅ 押中' if w3 else '⛔ 押破'}")
    print(f"    n_impl_g7d2_stuck = {len(d2b)}｜n_l2_total = {sum(b['n_l2'] for b in d2b)}")

    w4 = all(v["single_max"] < 1.0 and v["multi_max"] >= 1.0 for v in diag.values())
    print(f"\n  **W4 g7d2 必须同时改 2 个站点：任何单站点都 < 1.0，多站点到 1.0**")
    for h, v in sorted(diag.items()):
        print(f"    {h}: 单站点最高 {v['single_max']:.1f}｜多站点最高 {v['multi_max']:.1f}")
    print(f"    ⇒ {'✅ 押中' if w4 else '⛔ 押破'}")

    full2 = [r for r in d2 if r["s0"] >= 1.0]
    w5 = all(abs(r["d1"] - 1.0) < 1e-9 for r in full2)
    print(f"\n  **W5 g7d2 的 3 个基线满分实现在 1 跳内不被破坏（分数恒 1.0）**")
    print(f"    {[(r['h'], r['d1']) for r in full2]} ⇒ {'✅ 押中' if w5 else '⛔ 押破'}")

    n_all = sum(len(corpus[t]) for t in TIDS)
    n_base_full = sum(1 for t in TIDS for r in res[t] if r["s0"] >= 1.0)
    print(f"\n## §E 分母（⛔ 不许混）")
    print(f"  n_impl_g7e1 = {len(e1)}｜n_impl_g7d2 = {len(d2)}｜n_impl_total = {n_all}")
    print(f"  n_impl_base_full = {n_base_full}｜n_impl_needing_work = {n_all - n_base_full}")
    print(f"  n_impl_d2_reach = {sum(1 for b in bfs if b['reach1'])}"
          f"｜n_impl_d2_stuck = {sum(1 for b in bfs if not b['reach1'])}")

    (HERE / "reach_raw.json").write_text(
        json.dumps(dict(arms=res, bfs=bfs, diag=diag), ensure_ascii=False, indent=1),
        encoding="utf-8")
    print("\n  （原始读数已落盘 `reach_raw.json`）")


if __name__ == "__main__":
    main()
