#!/usr/bin/env python3
"""V148：可寻址性有多硬 —— 16 个格是「没走到」还是「产不出」。

零判分（除 §4 复用 V147 的一次 NOSTOP 取「判过的键」）：

  keys_at(impl, d)  从**原始代码**出发，对每个新代码再跑一次 `X.gen`，收集 `X.abstract(lab)`
  sites(code)       `S.sites` 四类站点 + `B:key` 需要的 `(In, .add)` 同变量对

判定：
  19 个**已覆盖**格 → 键必须落在 d=1 键集里（装置自校验）
  16 个 **missed** 格 → 键在 d=1 键集里 =「可寻址但没排到」（软）；不在 =「产不出」（硬）
  每个硬墙 → 归到一条具体缺失（站点 / 链 / 表达式）

⛔ 不改 `gen` / `abstract` / 语料；⛔ 不判分任何新候选。
"""
from __future__ import annotations

import ast
import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for _d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198", "v6_s210", "v6_s236"):
    sys.path.insert(0, str(EV / _d))

import idiom_transfer as I     # noqa: E402
import search as S             # noqa: E402
import v119 as V               # noqa: E402
import v120 as W               # noqa: E402
import v121 as X               # noqa: E402
import v147 as M               # noqa: E402  复用同一台搜索器（NOSTOP 臂）

BASELINE = ("?", "<baseline>")
NODE_CAP = 8000          # 单个实现、单个深度允许展开的代码节点数（纪律 64：截断要报）


def site_counts(code: str) -> dict:
    """四类模板站点 + `B:key` 需要的 `(In, .add)` 同变量对。"""
    try:
        st = S.sites(code)
        nodes = list(ast.walk(ast.parse(code)))
    except SyntaxError:
        return dict(ret_c=0, ret_any=0, in_cont=0, cmp_name=0, pair=0)
    pairs = set()
    for n in nodes:
        if isinstance(n, ast.Compare) and isinstance(n.left, ast.Name) \
                and any(isinstance(o, (ast.In, ast.NotIn)) for o in n.ops):
            for cc in n.comparators:
                if isinstance(cc, ast.Name):
                    pairs.add((n.left.id, cc.id))
    ok_pairs = 0
    for var, cont in pairs:
        has_in = any(isinstance(n, ast.Compare) and isinstance(n.left, ast.Name)
                     and n.left.id == var and any(isinstance(o, (ast.In, ast.NotIn)) for o in n.ops)
                     for n in nodes)
        has_add = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                      and n.func.attr == "add" and isinstance(n.func.value, ast.Name)
                      and n.func.value.id == cont and len(n.args) == 1
                      and isinstance(n.args[0], ast.Name) and n.args[0].id == var
                      for n in nodes)
        if has_in and has_add:
            ok_pairs += 1
    return dict(ret_c=len(st["ret_c"]), ret_any=len(st["ret_any"]),
                in_cont=len(st["in_cont"]), cmp_name=len(st["cmp_name"]), pair=ok_pairs)


def keys_at(tid, code, Sx, chains, exprs, depth, cap=NODE_CAP) -> dict:
    """BFS 到 `depth` 层，返回键集 + 节点数 + 「新造站点」计数 + 是否被截断。"""
    keys: set = set()
    seen = {code}
    frontier = [code]
    nodes = 0
    new_sites = 0
    truncated = False
    for _ in range(depth):
        nxt = []
        for parent in frontier:
            pc = site_counts(parent)
            for lab, src in X.gen(tid, parent, Sx, chains, exprs):
                keys.add(X.abstract(lab))
                if src in seen:
                    continue
                seen.add(src)
                nodes += 1
                cc = site_counts(src)
                if any(cc[k] > pc[k] for k in ("ret_c", "ret_any", "in_cont", "cmp_name", "pair")):
                    new_sites += 1
                nxt.append(src)
                if nodes >= cap:
                    truncated = True
                    break
            if truncated:
                break
        frontier = nxt
        if truncated or not frontier:
            break
    return dict(keys=keys, nodes=nodes, new_sites=new_sites, truncated=truncated)


def diagnose(key, Sx, chains, exprs, sites) -> str:
    """一条硬墙缺什么（唯一措辞）。"""
    kind, arg = key
    if kind == "T1":
        return "S 里没有该常量" if arg not in {repr(v) for v in Sx} else "站点在但未产出（异常）"
    if kind == "T2":
        if sites["in_cont"] == 0:
            return "无 `in [列表/元组]` 站点"
        if arg not in {repr(w) for w in Sx}:
            return "S 里没有该词"
        return "站点在但未产出（异常）"
    if kind == "T3":
        return "无 return 站点" if sites["ret_any"] == 0 else "站点在但未产出（异常）"
    if kind == "T4":
        return "无 `Compare.left is Name` 站点" if sites["cmp_name"] == 0 else "站点在但未产出（异常）"
    if kind == "key":
        if sites["pair"] == 0:
            return "无 `In` + 同容器 `.add(var)` 对"
        if arg not in chains:
            return "兄弟集里没有这条链"
        return "站点在但未产出（异常）"
    if kind in ("ret", "cmp"):
        if arg not in chains:
            return "兄弟集里没有这条链"
        if kind == "ret" and sites["ret_any"] == 0:
            return "无 return 站点"
        if kind == "cmp" and sites["cmp_name"] == 0:
            return "无 `Compare.left is Name` 站点"
        return "站点/链都在但未产出（异常）"
    if kind == "C1":
        if arg not in exprs:
            return "满分兄弟里没有该表达式"
        if sites["ret_any"] == 0:
            return "无 return 站点"
        return "站点/表达式都在但未产出（异常）"
    return "未分类"


def main() -> None:
    corpus = I.harvest()
    impls = []
    for tid in V.TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                continue
            impls.append((tid, h))
    mem = json.loads(X.MEM_FILE.read_text(encoding="utf-8"))

    uni_all = set()
    for tid, h in impls:
        for k in mem[tid]:
            if k["h"] != h:
                uni_all.add((tid, h, tuple(k["act"])))
    uni_real = {c for c in uni_all if c[2] != BASELINE}

    # 上下文（每实现一次）
    ctx = {}
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ctx[(tid, h)] = dict(code=code, Sx=Sx,
                             chains=set(V.harvest_chains(corpus, tid, h)),
                             exprs=set(W.harvest_exprs(corpus, tid, h)),
                             sites=site_counts(code))

    print("## §1 键宇宙：深度 1 vs 深度 2（零判分）")
    U1, U2 = {}, {}
    trunc, newsite = [], 0
    for (tid, h), c in ctx.items():
        r1 = keys_at(tid, c["code"], c["Sx"], c["chains"], c["exprs"], 1)
        r2 = keys_at(tid, c["code"], c["Sx"], c["chains"], c["exprs"], 2)
        U1[(tid, h)], U2[(tid, h)] = r1["keys"], r2["keys"]
        newsite += r2["new_sites"]
        if r2["truncated"]:
            trunc.append((tid, h, r2["nodes"]))
        print(f"  {tid[:6]} {h}  d1 keys={len(r1['keys']):3d} nodes={r1['nodes']:4d}"
              f"｜d2 keys={len(r2['keys']):3d} nodes={r2['nodes']:5d}"
              f"｜d2⊆d1 {'✅' if r2['keys'] <= r1['keys'] else '⛔'}"
              f"｜d2−d1={len(r2['keys'] - r1['keys'])}"
              f"{'｜截断' if r2['truncated'] else ''}")
    print(f"\n  汇总：d2⊆d1 的实现数 = {sum(1 for k in U1 if U2[k] <= U1[k])}/{len(U1)}"
          f"｜d2 新增键总数 = {sum(len(U2[k] - U1[k]) for k in U1)}"
          f"｜「新造站点」计数 = {newsite}｜被截断 = {len(trunc)}")

    print("\n## §2 覆盖对照：NOSTOP 判过的键（2301 次判分，同 V147 装置）")
    V.CACHE.clear()
    nost = M.run_impls(impls, corpus, mem, stop_on_hit=False)
    scored = collections.defaultdict(set)
    for (tid, h), r in nost.items():
        for e in r["events"]:
            scored[(tid, h)].add(tuple(e["key"]))
    base = {(t, h) for (t, h), r in nost.items() if r["s0"] >= 1.0}
    cov = {c for c in uni_real if c[2] in scored[(c[0], c[1])]}
    missed = sorted(uni_real - cov)
    print(f"  覆盖 = {len(cov)}/{len(uni_real)}｜missed = {len(missed)}（V147 记 19/35、16）")
    unscored_u1 = []
    for (tid, h), ks in U1.items():
        rest = ks - scored[(tid, h)]
        if rest:
            unscored_u1.append(((tid, h), len(rest), len(ks)))
    act_un = [x for x in unscored_u1 if x[0] not in base]
    print(f"  「深度 1 可产出但 NOSTOP 一次都没判过」的 (实现, 键数) = {len(unscored_u1)} 处"
          f"｜合计键 = {sum(n for _, n, _ in unscored_u1)}"
          f"｜其中属于**活跃**实现 = {len(act_un)} 处 / {sum(n for _, n, _ in act_un)} 个键")
    for k, n, tot in unscored_u1[:8]:
        tag = "基线" if k in base else "活跃"
        print(f"    {tag} {k[0][:6]} {k[1]}  未判过的键 = {n}/{tot}")

    print("\n## §3 19 个已覆盖格：键在深度 1 键集里吗（W1 自校验）")
    bad_cov = [c for c in sorted(cov) if c[2] not in U1[(c[0], c[1])]]
    print(f"  在 = {len(cov) - len(bad_cov)}/{len(cov)}"
          f"{'  ✅' if not bad_cov else '  ⛔ ' + str(bad_cov[:4])}")

    print("\n## §4 16 个 missed 格：可寻址（软）还是产不出（硬）")
    soft, hard = [], []
    rows = []
    for c in missed:
        tid, h, key = c
        cx = ctx[(tid, h)]
        inu1 = key in U1[(tid, h)]
        why = "" if inu1 else diagnose(key, cx["Sx"], cx["chains"], cx["exprs"], cx["sites"])
        (soft if inu1 else hard).append(c)
        rows.append(dict(tid=tid, h=h, key=list(key), addressable=inu1, why=why,
                         sites=cx["sites"]))
        print(f"  {tid[:22]:22s} {h}  {str(key):30s} "
              f"{'软（可寻址）' if inu1 else '硬（产不出）：' + why}")
    print(f"\n  软 = {len(soft)}｜硬 = {len(hard)}")

    print("\n## §4b 跑后诊断（⛔ 未预注册）：第三层分母 —— 「已经答对的实现」没有自省问题")
    act_univ = {c for c in uni_real if (c[0], c[1]) not in base}
    base_miss = [c for c in missed if (c[0], c[1]) in base]
    act_miss = [c for c in missed if (c[0], c[1]) not in base]
    print(f"  基线已到 1.0 的实现 = {len(base)}/{len(impls)}"
          f"（`v121.search:77-78` 在 `s0>=1.0` 时**直接返回，一次也不搜**）")
    print(f"  ⇒ 这些实现的格在 60 格口径下占 "
          f"{sum(1 for c in uni_all if (c[0], c[1]) in base)} 个")
    print(f"  missed 分组：属于基线实现（问题为空） = {len(base_miss)}"
          f"｜属于活跃实现（真有问题） = {len(act_miss)}")
    print(f"  活跃格 = {len(act_univ)}｜其中被覆盖 = {len(cov & act_univ)}"
          f"｜没触到 = {len(act_miss)}")
    for c in sorted(act_miss):
        print(f"    活跃但没触到：{c[0]} {c[1]} {c[2]}")

    print("\n## §5 硬墙的成因分布")
    cnt = collections.Counter(r["why"] for r in rows if not r["addressable"])
    for why, n in cnt.most_common():
        print(f"  {n:2d} × {why}")
    print("  缺失站点汇总（只看硬墙所在实现）：")
    for tid, h, key in hard:
        s = ctx[(tid, h)]["sites"]
        print(f"    {tid[:22]:22s} {h}  sites={s}｜chains={len(ctx[(tid,h)]['chains'])}"
              f"｜exprs={len(ctx[(tid,h)]['exprs'])}")

    print("\n## §6 反证组（W5）")
    fake = ("Z9", "nonsense")
    fake_hit = [k for k in U1 if fake in U1[k]]
    print(f"  C1 伪键 {fake} 出现在任何实现的键集里？{len(fake_hit)} 处"
          f" ⇒ {'⛔ 装置分不出真假' if fake_hit else '✅ 能说「产不出」'}")
    print(f"  C2 已覆盖 19 格全部可产出？{'✅' if not bad_cov else '⛔'}（{len(cov) - len(bad_cov)}/{len(cov)}）")
    print(f"  C3 「新造站点」计数 = {newsite}"
          f" ⇒ {'✅ 站点单调（没有模板新增站点）' if newsite == 0 else '⛔ 有模板新增了站点'}")

    w1 = not bad_cov
    w2 = all(U2[k] == U1[k] for k in U1)
    w3 = len(soft) == 0
    w4 = all(r["why"] and r["why"] != "未分类" for r in rows if not r["addressable"])
    w5 = (not fake_hit) and (not bad_cov) and (newsite == 0)
    print("\n## §7 判据（预注册 §4）")
    def row(tag, ok, txt):
        print(f"  {tag} {'✅' if ok else '⛔'} {txt}")
    row("W1", w1, f"已覆盖格键在 d1 键集里 {len(cov) - len(bad_cov)}/{len(cov)}")
    row("W2", w2, f"d2 键集 == d1 键集：{sum(1 for k in U1 if U2[k] == U1[k])}/{len(U1)}"
                  f"（d2 多出 {sum(len(U2[k] - U1[k]) for k in U1)} 个键）")
    row("W3", w3, f"missed 里可寻址（软）= {len(soft)}（押 0）")
    row("W4", w4, f"硬墙归因覆盖 = {sum(1 for r in rows if not r['addressable'] and r['why'])}/{len(hard)}")
    row("W5", w5, f"反证三条：伪键 {len(fake_hit)} 处｜已覆盖 {len(cov) - len(bad_cov)}/{len(cov)}"
                  f"｜新造站点 {newsite}")

    out = dict(n_impl=len(impls), n_universe_real=len(uni_real),
               n_covered=len(cov), n_missed=len(missed),
               n_baseline_impl=len(base), n_base_cells=len(uni_real) - len(act_univ),
               n_active_cells=len(act_univ), n_active_covered=len(cov & act_univ),
               n_active_missed=len(act_miss),
               soft=[list(c) for c in soft], hard=[list(c) for c in hard],
               rows=rows, new_sites=newsite, truncated=[list(t) for t in trunc],
               unscored_u1=[[list(k), n, tot] for k, n, tot in unscored_u1],
               d2_added={f"{k[0]}::{k[1]}": sorted(map(list, U2[k] - U1[k])) for k in U1
                         if U2[k] - U1[k]},
               w1=w1, w2=w2, w3=w3, w4=w4, w5=w5)
    (HERE / "readings.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print("\n  已落盘 `readings.json`")


if __name__ == "__main__":
    main()
