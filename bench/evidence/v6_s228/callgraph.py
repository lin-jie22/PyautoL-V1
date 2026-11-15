#!/usr/bin/env python3
"""V139 Part A：静态调用图能不能预测「谁跟着变了」。

不跑判据、不跑轨迹。真值来自 v6_s227/analysis.json 的 `diff`，
预测来自源码 AST 的调用关系。随机零分布由标签置换造（纪律 49）。

节点命名与轨迹一致：`mw.<模块>.<函数>` 或 `mw.<模块>.<类>.<方法>`。
"""
from __future__ import annotations

import ast
import json
import pathlib
import random
import statistics
import sys
from collections import deque

EVID = pathlib.Path(__file__).resolve().parent
V138 = EVID.parent / "v6_s227"
BASE = pathlib.Path("/tmp/v1_base")
if not (BASE / "mw").exists():
    BASE = pathlib.Path("/Users/linshaojie/V1")


# ---------------------------------------------------------------- 建图
class Graph:
    def __init__(self) -> None:
        self.nodes: set[str] = set()
        self.calls: dict[str, set[str]] = {}      # caller -> callees (strict)
        self.wild: dict[str, set[str]] = {}       # caller -> unresolved attr names
        self.by_leaf: dict[str, set[str]] = {}    # 叶名 -> 节点（loose 用）
        self.n_unres = 0

    def add(self, a: str, b: str) -> None:
        self.calls.setdefault(a, set()).add(b)
        self.calls.setdefault(b, set())

    def wildcard(self, a: str, name: str) -> None:
        self.wild.setdefault(a, set()).add(name)
        self.n_unres += 1


def qualnames(tree: ast.Module, mod: str) -> tuple[dict[int, str], dict[int, str]]:
    """-> ({id(func): 'mw.mod.Class.func'}, {id(func): 'mw.mod.Class' 或 ''})

    ⛔ 自踩（V139 装置）：原来用 `v.count(".") >= 3` 判断「这是不是类」，
    而 `mw.bench.Suite` 只有 **2** 个点 ⇒ 所有方法都被当成模块级函数，
    `self.method()` 一律解析不出 ⇒ strict 边只有 136 条（真实远多于此）。
    """
    quals: dict[int, str] = {}
    cls_of: dict[int, str] = {}

    def walk(node, prefix: str, cls: str) -> None:
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = f"{prefix}.{ch.name}"
                quals[id(ch)] = q
                cls_of[id(ch)] = cls
                walk(ch, q, cls)
            elif isinstance(ch, ast.ClassDef):
                cq = f"{prefix}.{ch.name}"
                walk(ch, cq, cq)
            else:
                walk(ch, prefix, cls)

    walk(tree, mod, "")
    return quals, cls_of


def build(loose: bool) -> Graph:
    g = Graph()
    files = sorted((BASE / "mw").glob("*.py"))
    trees: dict[str, tuple] = {}
    for f in files:
        mod = f"mw.{f.stem}"
        src = f.read_text()
        tree = ast.parse(src)
        qn, cls_of = qualnames(tree, mod)
        # 本模块可解析符号：函数/类名 -> 节点，导入名 -> 目标节点或模块
        syms: dict[str, str] = {}
        mods: dict[str, str] = {}
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                syms[n.name] = qn[id(n)]
            elif isinstance(n, ast.ClassDef):
                syms[n.name] = f"{mod}.{n.name}"
            elif isinstance(n, ast.ImportFrom):
                if n.level:                       # from .x import a, b
                    base = f"mw.{(n.module or '').strip('.')}" if n.module else mod
                    for a in n.names:
                        if n.module:
                            syms[a.asname or a.name] = f"{base}.{a.name}"
                        else:
                            mods[a.asname or a.name] = f"mw.{a.name}"
                elif n.module and n.module.split(".")[0] == "mw":
                    for a in n.names:
                        syms[a.asname or a.name] = f"{n.module}.{a.name}"
        trees[mod] = (tree, qn, syms, mods, cls_of)
        g.nodes |= set(qn.values())

    # by_leaf：loose 档用
    for node in g.nodes:
        g.by_leaf.setdefault(node.rsplit(".", 1)[-1], set()).add(node)

    for mod, (tree, qn, syms, mods, cls_of) in trees.items():
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            me = qn[id(n)]
            cls = cls_of.get(id(n)) or None
            for c in ast.walk(n):
                if not isinstance(c, ast.Call):
                    continue
                fn = c.func
                tgt = None
                if isinstance(fn, ast.Name):
                    tgt = syms.get(fn.id)
                    if tgt is None and fn.id in mods:
                        continue                              # 调模块本身，不算边
                elif isinstance(fn, ast.Attribute):
                    attr = fn.attr
                    base = fn.value
                    if isinstance(base, ast.Name):
                        if base.id in ("self", "cls") and cls:
                            cand = f"{cls}.{attr}"
                            tgt = cand if cand in g.nodes else None
                        elif base.id in mods:
                            cand = f"{mods[base.id]}.{attr}"
                            tgt = cand if cand in g.nodes else None
                        elif base.id in syms:                 # Class.method()
                            cand = f"{syms[base.id]}.{attr}"
                            tgt = cand if cand in g.nodes else None
                    if tgt is None:
                        g.wildcard(me, attr)                  # 解析不出接收者
                        if loose:
                            for t in g.by_leaf.get(attr, ()):
                                if t != me:
                                    g.add(me, t)
                        continue
                if tgt and tgt != me and tgt in g.nodes:
                    g.add(me, tgt)
    return g


def callers(g: Graph, f: str) -> set[str]:
    rev: dict[str, set[str]] = {k: set() for k in g.nodes}
    for a, bs in g.calls.items():
        for b in bs:
            rev.setdefault(b, set()).add(a)
    seen = {f}
    dq = deque([f])
    while dq:
        cur = dq.popleft()
        for p in rev.get(cur, ()):
            if p not in seen:
                seen.add(p)
                dq.append(p)
    return seen


def direct(g: Graph, f: str) -> set[str]:
    rev: dict[str, set[str]] = {k: set() for k in g.nodes}
    for a, bs in g.calls.items():
        for b in bs:
            rev.setdefault(b, set()).add(a)
    return {f} | set(rev.get(f, ()))


# ---------------------------------------------------------------- 主程序
def owner_map() -> dict[str, str]:
    """⛔ 不用 v6_s227/taxonomy.py 的版本：它返回不带类名的 `n.name`，与轨迹的
    `qualname` 对不上（V139 更正，见 relabel.py）。这里读重判后的 tiers.json。"""
    return json.loads((EVID / "tiers.json").read_text())["owner"]


def main() -> int:
    rows = json.loads((V138 / "analysis.json").read_text())["rows"]
    bl = json.loads((V138 / "baseline.json").read_text())
    universe = set(bl["base_per_fn"])
    own = owner_map()

    tiers = json.loads((EVID / "tiers.json").read_text())["tiers"]
    spill = [r for r in rows if tiers[r["key"]] == "SPILL"]
    print(f"真值: 89 站；SPILL = {len(spill)} 站；universe（被调用函数）= {len(universe)}")

    gs, gl = build(False), build(True)
    print(f"静态图: strict 边 {sum(len(v) for v in gs.calls.values())} / "
          f"loose 边 {sum(len(v) for v in gl.calls.values())} / "
          f"未解析调用点 {gs.n_unres}（占全部调用点的一部分）"
          f" / 节点 {len(gs.nodes)}")

    rng = random.Random(20261115)
    rows_out = []
    agg = {"loose": [0, 0, 0], "strict": [0, 0, 0]}   # 命中 / 预测 / 真值
    frich: dict[str, list[float]] = {"loose": [], "strict": []}
    better = 0
    n_cmp = 0
    miss_recall = []
    over = []

    for r in spill:
        key, truth = r["key"], set(r["diff"])
        f = own.get(key)
        if f is None or f not in gs.nodes:
            print(f"  ⚠️ {key}: 被改函数 {f} 不在静态图节点里，跳过")
            continue
        rec = {}
        for tag, g in (("loose", gl), ("strict", gs)):
            pt = callers(g, f)
            pd = direct(g, f)
            rec[tag] = (pt, pd)
            hit = len(pt & truth)
            agg[tag][0] += hit
            agg[tag][1] += len(pt)
            agg[tag][2] += len(truth)
            # 零分布：把 truth 的标签随机置换，同样大小
            zero = []
            uni = sorted(universe)
            for _ in range(200):
                fake = set(rng.sample(uni, min(len(truth), len(uni))))
                zero.append(len(pt & fake) / len(pt) if pt else 0.0)
            p = hit / len(pt) if pt else 0.0
            z = statistics.mean(zero)
            frich[tag].append(p / z if z else float("inf"))
            rec[tag] = (pt, pd, p, len(pt & truth) / len(truth) if truth else 0.0, z)
        # 便于阅读的诊断量
        pt_l, pd_l, p_l, r_l, z_l = rec["loose"]
        pt_s, pd_s, p_s, r_s, z_s = rec["strict"]
        if r_l < 1.0:
            miss_recall.append((key, sorted(truth - pt_l)[:3]))
        if truth - pt_l:
            pass
        elif truth != pt_l:
            over.append(key)
        pd_l_prec = len(pd_l & truth) / len(pd_l) if pd_l else 0.0
        if pd_l_prec > p_l:
            better += 1
        n_cmp += 1
        rows_out.append(dict(key=key, f=f, ntruth=len(truth), npred=len(pt_l),
                             hit=len(pt_l & truth), prec=round(p_l, 3), rec=round(r_l, 3),
                             zero=round(z_l, 4), npred_d=len(pd_l),
                             prec_d=round(pd_l_prec, 3)))

    print(f"\n=== Part A（{n_cmp} 站）===")
    print(f"{'站点':22s}{'|T|':>5s}{'|P|':>5s}{'命中':>5s}{'精度':>7s}{'召回':>7s}{'随机':>7s}{'直接精度':>9s}")
    for d in rows_out:
        print(f"{d['key']:22s}{d['ntruth']:5d}{d['npred']:5d}{d['hit']:5d}"
              f"{d['prec']:7.3f}{d['rec']:7.3f}{d['zero']:7.4f}{d['prec_d']:9.3f}")

    print("\n=== 合并口径（Σ命中/Σ预测）===")
    for tag in ("loose", "strict"):
        h, p, t = agg[tag]
        zmean = statistics.mean(frich[tag])
        print(f"  {tag:6s} 命中 {h:4d} / 预测 {p:5d} = 精度 {h/p:.3f} | "
              f"真值总数 {t} ⇒ 召回 {h/t:.3f} | 富集中位 {statistics.median(frich[tag]):.2f}×")

    print(f"\nW1 召回<1 的站: {len(miss_recall)} / {n_cmp}")
    for k, miss in miss_recall[:6]:
        print(f"   {k:20s} 漏: {miss}")
    print(f"W2 真值 ⊊ 预测（有假阳性）的站: {len(over)} / {n_cmp}")
    print(f"W3 直接调用者精度 > 传递可达精度: {better} / {n_cmp}")

    (EVID / "partA.json").write_text(json.dumps(
        {"rows": rows_out, "agg": agg,
         "rich_median": {k: statistics.median(v) for k, v in frich.items()},
         "n": n_cmp}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
