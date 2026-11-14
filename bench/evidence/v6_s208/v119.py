#!/usr/bin/env python3
"""V119：动作参数从人手改成库里取。

两个臂，唯一变量 = 链的来源：
  A（对照）= `v6_s205/search.py` 的 T1~T4，一字不改
  B（实验）= A 全部 + （库链 × 3 类落刀位置：ret / cmp / key）

零模型、零网络、零子进程。判据 = 现有 test 同一批。
分数 = 位图通过率（V117 `probe.txt` §0 已在 773 个候选上验证与子进程判分器零错位）。
"""
from __future__ import annotations

import ast
import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
sys.path.insert(0, str(EV / "v6_s205"))
sys.path.insert(0, str(EV / "v6_s198"))
import search as S           # noqa: E402
import idiom_transfer as I    # noqa: E402

TIDS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]
KS = [1, 2, 3]
MAX_ROUNDS = 6
BAN = {"format", "join", "split", "append", "add", "startswith"}
CACHE: dict[tuple[str, str], tuple[float, tuple]] = {}


def ev(tid: str, code: str) -> tuple[float, tuple]:
    key = (tid, code)
    if key in CACHE:
        return CACHE[key]
    bm = tuple(S.bitmap(tid, code))
    sc = sum(1 for x in bm if x is True) / len(bm)
    CACHE[key] = (sc, bm)
    return sc, bm


# ---------------------------------------------------------------- 库链抽取
def harvest_chains(corpus: dict, tid: str, exclude: str) -> list[tuple]:
    """从**同题其他实现**抽方法链。⛔ 排除自己（否则是抄答案）。"""
    chains = set()
    for h, code in corpus[tid].items():
        if h == exclude:
            continue
        try:
            tree = ast.parse(code)
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            seq, cur, ok = [], n, True
            while isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
                if cur.func.attr in BAN:
                    ok = False
                    break
                args = []
                for a in cur.args:
                    if isinstance(a, ast.Constant):
                        args.append(a.value)
                    else:
                        ok = False
                        break
                if not ok or cur.keywords:
                    ok = False
                    break
                seq.append((cur.func.attr, tuple(args)))
                cur = cur.func.value
            if ok and seq and len(seq) <= 3:
                chains.add(tuple(reversed(seq)))
    return sorted(chains, key=lambda c: (len(c), str(c)))


def _apply(expr, chain):
    e = expr
    for name, args in chain:
        e = ast.Call(func=ast.Attribute(value=e, attr=name, ctx=ast.Load()),
                     args=[ast.Constant(value=a) for a in args], keywords=[])
    return e


def _label(chain) -> str:
    return "".join(f".{n}({','.join(repr(a) for a in args)})" for n, args in chain)


# ---------------------------------------------------------------- 站点
def _rebuild(code, mut):
    t = ast.parse(code)
    mut(list(ast.walk(t)))
    return ast.unparse(ast.fix_missing_locations(t))


def candidates_B(code: str, chains: list[tuple]) -> list[tuple[str, str]]:
    """库链 × 3 类落刀位置。站点集来自 AST / 数据流，不逐题指定。"""
    out: list[tuple[str, str]] = []
    try:
        nodes = list(ast.walk(ast.parse(code)))
    except SyntaxError:
        return out

    ret_idx = [i for i, n in enumerate(nodes)
               if isinstance(n, ast.Return) and n.value is not None]
    cmp_idx = [i for i, n in enumerate(nodes)
               if isinstance(n, ast.Compare) and isinstance(n.left, ast.Name)]

    for c in chains:
        lab = _label(c)
        for i in ret_idx:
            def mut(ns, i=i, c=c):
                ns[i].value = _apply(ns[i].value, c)
            try:
                out.append((f"B:ret{i}:{lab}", _rebuild(code, mut)))
            except Exception:                   # noqa: BLE001
                pass
        for i in cmp_idx:
            def mut(ns, i=i, c=c):
                ns[i].left = _apply(ns[i].left, c)
            try:
                out.append((f"B:cmp{i}:{lab}", _rebuild(code, mut)))
            except Exception:                   # noqa: BLE001
                pass

    # key 类：同一变量的「比较主语」+「同容器 .add 的实参」一起改
    pairs = set()
    for n in nodes:
        if isinstance(n, ast.Compare) and isinstance(n.left, ast.Name) \
                and any(isinstance(o, (ast.In, ast.NotIn)) for o in n.ops):
            for cc in n.comparators:
                if isinstance(cc, ast.Name):
                    pairs.add((n.left.id, cc.id))
    for var, cont in sorted(pairs):
        il = [i for i, n in enumerate(nodes)
              if isinstance(n, ast.Compare) and isinstance(n.left, ast.Name)
              and n.left.id == var and any(isinstance(o, (ast.In, ast.NotIn)) for o in n.ops)]
        ia = [i for i, n in enumerate(nodes)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "add" and isinstance(n.func.value, ast.Name)
              and n.func.value.id == cont and len(n.args) == 1
              and isinstance(n.args[0], ast.Name) and n.args[0].id == var]
        if not (il and ia):
            continue
        for c in chains:
            def mut(ns, il=tuple(il), ia=tuple(ia), c=c):
                for i in il:
                    ns[i].left = _apply(ns[i].left, c)
                for i in ia:
                    ns[i].args[0] = _apply(ns[i].args[0], c)
            try:
                out.append((f"B:key({var},{cont}):{_label(c)}", _rebuild(code, mut)))
            except Exception:                   # noqa: BLE001
                pass
    return out


# ---------------------------------------------------------------- beam
def beam(tid: str, code: str, Sx: set[str], chains: list[tuple], k: int, arm: str) -> dict:
    s0, _ = ev(tid, code)
    frontier = [dict(code=code, s=s0, lab="<baseline>")]
    visited = {code}
    trail, n_eval = [], 0
    steps_to_full = None
    for rnd in range(MAX_ROUNDS):
        pool, seen_src = [], set()
        for node in frontier:
            cands = S.candidates(node["code"], Sx)
            if arm == "B":
                cands = cands + candidates_B(node["code"], chains)
            for lab, src in cands:
                if src in visited or src in seen_src:
                    continue
                s, _ = ev(tid, src)
                n_eval += 1
                if s > node["s"]:
                    pool.append(dict(code=src, s=s, lab=lab, parent=node["lab"]))
                    seen_src.add(src)
        if not pool:
            trail.append(dict(n_pool=0, kept=[]))
            break
        pool.sort(key=lambda d: -d["s"])
        kept = pool[:k]
        visited |= {d["code"] for d in pool}
        trail.append(dict(n_pool=len(pool), kept=[d["lab"] for d in kept],
                          s=[d["s"] for d in kept]))
        frontier = kept
        if max(d["s"] for d in frontier) >= 1.0:
            if steps_to_full is None:
                steps_to_full = rnd + 1
            break
    best = max(frontier, key=lambda d: d["s"])
    return dict(s0=s0, s1=best["s"], lab=best["lab"], n_eval=n_eval,
                steps_to_full=steps_to_full, trail=trail)


def main() -> None:
    corpus = I.harvest()
    res: dict[str, list] = {}
    chain_info: dict[str, dict] = {}
    skipped = []

    for tid in TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                skipped.append((tid, h))
                continue
            Sx, _ = S.wordset(tid, corpus[tid][h], True)
            chains = harvest_chains(corpus, tid, h)
            chain_info[f"{tid}::{h}"] = dict(n_chains=len(chains),
                                             chains=[_label(c) for c in chains])
            for arm in ("A", "B"):
                for k in KS:
                    if arm == "A" and k != 2:      # 臂 A 只跑 k=2（V117 已量过 k=1/3）
                        continue
                    r = beam(tid, corpus[tid][h], Sx, chains, k, arm)
                    r.update(h=h, tid=tid)
                    res[f"{arm}:k{k}:{tid}::{h}"] = r

    # ---------------- §0 库链规模
    print("## §0 库链规模（从同题其他实现抽，排除自己）")
    for tid in TIDS:
        ns = [v["n_chains"] for kk, v in chain_info.items() if kk.startswith(tid + "::")]
        print(f"  {tid:22s} n_chains = {ns}")
    print("  样本（g7d3 某个实现看到的链）：")
    sample = next((v for kk, v in chain_info.items() if kk.startswith("g7d3")), None)
    if sample:
        for c in sample["chains"][:12]:
            print(f"    {c}")
        print(f"    …共 {sample['n_chains']} 条")
    print(f"  n_impl_skipped = {len(skipped)}（不可解析）：{skipped}")

    # ---------------- §1 主读数
    print("\n## §1 主读数（每题的分数向量；分母写进标题）")
    for tid in TIDS:
        hs = sorted({d["h"] for d in res.values() if d["tid"] == tid})
        print(f"\n  **{tid}**（n_impl = {len(hs)}）")
        for arm, k in [("A", 2), ("B", 1), ("B", 2), ("B", 3)]:
            vec = [res[f"{arm}:k{k}:{tid}::{h}"]["s1"] for h in hs]
            nf = sum(1 for v in vec if v >= 1.0)
            print(f"    {arm} k={k}: {vec}　到 1.0 的 {nf}/{len(hs)}")
        base = [res[f"A:k2:{tid}::{h}"]["s0"] for h in hs]
        print(f"    基线    : {base}　到 1.0 的 {sum(1 for v in base if v >= 1.0)}/{len(hs)}")

    # ---------------- §2 判决
    print("\n## §2 判决")
    n_par = sum(len([h for h in corpus[t] if (t, h) not in skipped]) for t in TIDS)
    base_full = sum(1 for t in TIDS for h in corpus[t] if (t, h) not in skipped
                    and res[f"A:k2:{t}::{h}"]["s0"] >= 1.0)
    need = n_par - base_full

    def fixed(arm, k, tid=None):
        """⛔ 第一版漏了 arm/k 过滤 ⇒ 把三条 B 臂的同一行数了三遍
        （g7d3 读出 6 = 2 x 3），把 W2/W3/W4 全判成押破。键是 `arm:k:tid::h`，必须前缀过滤。
        """
        pre = f"{arm}:k{k}:"
        return [d for key, d in res.items()
                if key.startswith(pre) and d["s1"] >= 1.0 and d["s0"] < 1.0
                and (tid is None or d["tid"] == tid)]

    fA = fixed("A", 2)
    fB2 = fixed("B", 2)
    print(f"\n  n_impl_total = {sum(len(v) for v in corpus.values())}"
          f"｜n_impl_parseable = {n_par}｜n_impl_base_full = {base_full}"
          f"｜n_impl_needing_work = {need}")

    # W1
    e1 = "g7e1_parse_flag"
    w1 = (sum(1 for h in corpus[e1] if res[f"A:k2:{e1}::{h}"]["s1"] >= 1.0) == 8
          and sum(1 for h in corpus["g7d2_dedupe_keep"]
                  if res[f"A:k2:g7d2_dedupe_keep::{h}"]["s1"] >= 1.0) == 3)
    print(f"\n  **W1 臂 A 复现 V117：g7e1 8/8、g7d2 3/5** ⇒ {'✅ 押中' if w1 else '⛔ 押破'}")

    # W2
    w2 = len(fixed("A", 2, "g7d3_fmt_amount")) == 0 and len(fixed("A", 2, "g7e2_truncate_tail")) == 0
    print(f"  **W2 臂 A 在 g7d3/g7e2 上修好 0 个** ⇒ {'✅ 押中' if w2 else '⛔ 押破'}"
          f"（实得 g7d3={len(fixed('A', 2, 'g7d3_fmt_amount'))}"
          f"、g7e2={len(fixed('A', 2, 'g7e2_truncate_tail'))}）")

    # W3
    d2b = sorted(d["h"] for d in fixed("B", 2, "g7d2_dedupe_keep"))
    w3 = d2b == ["480ada8e", "74aa48d8"]
    print(f"  **W3 臂 B 在 g7d2 上修好恰好 {{480ada8e, 74aa48d8}}** ⇒ {'✅ 押中' if w3 else '⛔ 押破'}")
    print(f"    实得：{d2b}")
    for h in d2b:
        print(f"      {h} 生效动作 = {res[f'B:k2:g7d2_dedupe_keep::{h}']['lab']}")

    # W4
    d3b = sorted(d["h"] for d in fixed("B", 2, "g7d3_fmt_amount"))
    w4 = d3b == ["075b4099", "419ec792"]
    print(f"  **W4 臂 B 在 g7d3 上修好恰好 {{075b4099, 419ec792}}** ⇒ {'✅ 押中' if w4 else '⛔ 押破'}")
    print(f"    实得：{d3b}")
    for h in d3b:
        print(f"      {h} 生效动作 = {res[f'B:k2:g7d3_fmt_amount::{h}']['lab']}")

    # W5
    w5 = len(fixed("B", 2, "g7e2_truncate_tail")) == 0
    print(f"  **W5 臂 B 在 g7e2 上修好 0 个** ⇒ {'✅ 押中' if w5 else '⛔ 押破'}"
          f"（实得 {[d['h'] for d in fixed('B', 2, 'g7e2_truncate_tail')]}）")

    # W6
    dmg = [(d["tid"], d["h"], d["s1"]) for d in res.values()
           if d["s0"] >= 1.0 and d["s1"] < 1.0]
    w6 = not dmg
    print(f"  **W6 基线满分的实现不被损害** ⇒ {'✅ 押中' if w6 else '⛔ 押破'}（受损 {dmg}）")

    # W7
    print(f"  **W7 臂 B 总修好 > 臂 A，且新增全部落在「库里有对应链」的题上**")
    print(f"    臂 A 修好 = {len(fA)}（{sorted((d['tid'][:6], d['h']) for d in fA)}）")
    print(f"    臂 B 修好 = {len(fB2)}（{sorted((d['tid'][:6], d['h']) for d in fB2)}）")
    new = {(d["tid"], d["h"]) for d in fB2} - {(d["tid"], d["h"]) for d in fA}
    print(f"    新增 = {sorted(new)}")
    w7 = len(fB2) > len(fA) and all(t != "g7e2_truncate_tail" for t, _ in new)
    print(f"    ⇒ {'✅ 押中' if w7 else '⛔ 押破'}")

    # ---------------- §3 判分次数（V120 的基线）
    print("\n## §3 到 1.0 所需的判分次数（⛔ V120 要比的是这个，不是「有没有用过记忆」）")
    for tid in TIDS:
        for h in sorted({d["h"] for d in res.values() if d["tid"] == tid}):
            a = res[f"A:k2:{tid}::{h}"]
            b = res[f"B:k2:{tid}::{h}"]
            print(f"  {tid[:6]} {h} s0={a['s0']:.1f}｜A n_eval={a['n_eval']:5d} s1={a['s1']:.1f}"
                  f"｜B n_eval={b['n_eval']:5d} s1={b['s1']:.1f}")

    (HERE / "search.json").write_text(
        json.dumps(dict(res=res, chains=chain_info, skipped=skipped),
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  原始读数已落盘 `search.json`")


if __name__ == "__main__":
    main()
