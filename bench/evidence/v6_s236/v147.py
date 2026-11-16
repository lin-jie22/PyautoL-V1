#!/usr/bin/env python3
"""V147：判分预算 → 自省覆盖 的曲线。

两个臂，同一搜索器、同一动作空间、同一记忆（`v121.MEM_FILE`）、同一 25 实现：

  STOP     部署态：`v121.search` 逐行同构，「一到 1.0 就停」（= V146 的 WARM_ALL）
  NOSTOP   反事实：同样逐行，**唯一差别是不 return**，把剩下的轮次跑完

`NOSTOP` 是 `STOP` 的行为延伸（不是另一个装置）：逐实现断言「前 `n_eval(STOP)` 个
事件与 `STOP` 的全部事件逐事件相同」（纪律 50）。

每次判分归一类（互斥，跑前写死 + 跑后修订见 PREREG §3.1）：
  NONMEM   该候选的抽象键**不在**本实现的记忆格里 ⇒ 这次判分不验证任何知识
  NEW      记忆格在**本臂里第一次**被判分
  RESOLVE  这次判分把该格的累计最高分从 <1.0 抬到 ≥1.0
  REFINE   其余（已知格的再判分，只提高「不成立」的置信度）

⛔ 不新造分数：判分仍是 `v119.ev`，候选仍是 `v121.gen`，记忆仍是 `v121.MEM_FILE`。
⛔ 不改 `v121.py`（那是证据）。
"""
from __future__ import annotations

import ast
import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for _d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198"):
    sys.path.insert(0, str(EV / _d))
sys.path.insert(0, str(EV / "v6_s210"))

import idiom_transfer as I     # noqa: E402
import search as S             # noqa: E402
import v119 as V               # noqa: E402
import v120 as W               # noqa: E402
import v121 as X               # noqa: E402

MAX_ROUNDS = X.MAX_ROUNDS
#: 记忆文件里的哨兵条目。`abstract()` 永远不可能产出它（见 `v121.abstract` 的六条正则），
#: ⇒ 由它构成的格**没有任何候选能命中**，是结构上不可达的格。
BASELINE = ("?", "<baseline>")


def search_trace(tid, h, code, Sx, chains, exprs, mine, stop_on_hit=True) -> dict:
    """与 `v121.search` 逐行同构；差别只有 (1) 记录事件 (2) `stop_on_hit` 可关。"""
    s0, _ = V.ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, s1=s0, n_eval=0, hit=True, hit_at=0, events=[])
    frontier = [dict(code=code, s=s0)]
    visited = {code}
    n_eval = 0
    events: list[dict] = []
    best_s = s0
    hit_at = -1
    for rnd in range(1, MAX_ROUNDS + 1):
        pool, seen_src = [], set()
        for node in frontier:
            cands = [(X.abstract(lab), lab, src)
                     for lab, src in X.gen(tid, node["code"], Sx, chains, exprs)]
            for k, _lab, src in sorted(cands, key=lambda x: (0 if x[0] in mine else 1,)):
                if src in visited or src in seen_src:
                    continue
                s, _ = V.ev(tid, src)
                n_eval += 1
                events.append(dict(tid=tid, h=h, key=list(k), score=s, rnd=rnd))
                if s > best_s:
                    best_s = s
                if s >= 1.0 and hit_at < 0:
                    hit_at = n_eval
                    if stop_on_hit:
                        return dict(s0=s0, s1=s, n_eval=n_eval, hit=True,
                                    hit_at=hit_at, events=events)
                if s > node["s"]:
                    pool.append(dict(code=src, s=s))
                    seen_src.add(src)
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        frontier = pool[:2]
        visited |= {d["code"] for d in pool}
    return dict(s0=s0, s1=best_s, n_eval=n_eval, hit=hit_at > 0, hit_at=hit_at,
                events=events)


def run_impls(impls, corpus, mem, stop_on_hit):
    out = {}
    V.CACHE.clear()
    for tid, h in impls:
        mine = {(a, b) for k in mem[tid] if k["h"] != h for a, b in [tuple(k["act"])]}
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        chains = V.harvest_chains(corpus, tid, h)
        exprs = W.harvest_exprs(corpus, tid, h)
        out[(tid, h)] = search_trace(tid, h, corpus[tid][h], Sx, chains, exprs,
                                     mine, stop_on_hit=stop_on_hit)
    return out


def analyze(events, universe, checkpoints):
    """一次扫描算：四分类计数 + 覆盖曲线（只在 `universe` 内计格）。"""
    seen: set = set()
    bmax: dict = {}
    cls = collections.Counter()
    rows: list[dict] = []
    cps = sorted(set(c for c in checkpoints if c > 0))
    ci = 0
    if 0 in checkpoints:
        rows.append(dict(n=0, covered=0, valid=0, NONMEM=0, NEW=0, RESOLVE=0, REFINE=0))
    for i, e in enumerate(events, 1):
        cell = (e["tid"], e["h"], tuple(e["key"]))
        if cell not in universe:
            cls["NONMEM"] += 1
        else:
            new = cell not in seen
            prev = bmax.get(cell, -1.0)
            seen.add(cell)
            if e["score"] > prev:
                bmax[cell] = e["score"]
            if new:
                cls["NEW"] += 1
            elif e["score"] >= 1.0 and prev < 1.0:
                cls["RESOLVE"] += 1
            else:
                cls["REFINE"] += 1
        if ci < len(cps) and i == cps[ci]:
            rows.append(dict(n=i, covered=len(seen),
                             valid=sum(1 for v in bmax.values() if v >= 1.0),
                             **{k: cls[k] for k in ("NONMEM", "NEW", "RESOLVE", "REFINE")}))
            ci += 1
    return rows, dict(cls), seen


def first_n(events, universe, target):
    """达到 target 个覆盖格所需的最小判分次数（1-based）。"""
    if target <= 0:
        return 0
    seen: set = set()
    for i, e in enumerate(events, 1):
        cell = (e["tid"], e["h"], tuple(e["key"]))
        if cell in universe:
            seen.add(cell)
        if len(seen) >= target:
            return i
    return None


def channel_forensics() -> None:
    """§-1（零判分）：记忆条目在装置里被用来做什么 —— 只有「排序」，没有「施加」。"""
    print("## §-1 通道取证：记忆条目的消费点（零判分）")
    files = [EV / "v6_s210" / "v121.py", EV / "v6_s235" / "v146.py", HERE / "v147.py"]
    for f in files:
        hits = [(i, ln.rstrip()) for i, ln in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
                if '["act"]' in ln or "['act']" in ln]
        for i, ln in hits:
            print(f"  {f.name}:{i}  {ln.strip()}")
        if not hits:
            print(f"  {f.name}: 无")
    print("  ⇒ 每一处都只是把 `act` 变成**集合/元组**用于 `k in mine` 排序；")
    print("     没有任何一处用它构造候选。候选唯一来源 = `X.gen(...)`，")
    print("     `X.abstract(lab)` 只是事后**读**出来的标签。\n")


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

    # 格的两个分母（纪律 46：口径写进字段名）
    uni_all = set()
    for tid, h in impls:
        for k in mem[tid]:
            if k["h"] != h:
                uni_all.add((tid, h, tuple(k["act"])))
    uni_real = {c for c in uni_all if c[2] != BASELINE}

    feas = "--feas" in sys.argv
    if not feas:
        channel_forensics()
    stop = run_impls(impls, corpus, mem, stop_on_hit=True)
    nost = run_impls(impls, corpus, mem, stop_on_hit=False)

    ev_stop = [e for tid, h in impls for e in stop[(tid, h)]["events"]]
    ev_nost = [e for tid, h in impls for e in nost[(tid, h)]["events"]]
    n_stop = sum(v["n_eval"] for v in stop.values())
    n_nost = sum(v["n_eval"] for v in nost.values())
    to1_stop = sum(1 for v in stop.values() if v["s1"] >= 1.0)
    to1_nost = sum(1 for v in nost.values() if v["s1"] >= 1.0)

    print("## §0 装置自校验（⛔ 不成立则下面全部作废）")
    print(f"  STOP   n_eval = {n_stop}（V146 记 588）｜到 1.0 = {to1_stop}/25（记 21）")
    print(f"  NOSTOP n_eval = {n_nost}｜到 1.0 = {to1_nost}/25")
    # ⚠️ 前缀性必须**逐实现**断言：两条臂都是"一个实现接一个实现"串行，
    #    某实现早停会让全局事件位置整体错位（首读就踩了：@14 报不同）。
    bad = []
    for tid, h in impls:
        a = stop[(tid, h)]["events"]
        b = nost[(tid, h)]["events"][:len(a)]
        if a != b:
            bad.append((tid, h))
    prefix_ok = not bad
    print(f"  前缀性（逐实现：NOSTOP 的前 n_eval 个事件 == STOP 全部事件）："
          f"{'✅' if prefix_ok else '⛔ 不成立'}（{len(impls)} 个实现，坏 {len(bad)}）")
    if bad:
        tid, h = bad[0]
        a, b = stop[(tid, h)]["events"], nost[(tid, h)]["events"]
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                print(f"    首个差异 {tid}::{h} @ {i}: STOP={x} NOSTOP={y}")
                break
    triv = [k for k in stop if stop[k]["s1"] != nost[k]["s1"]]
    print(f"  答案不变：逐实现 s1 有差异的实现数 = {len(triv)}"
          f"{'（' + str(triv[:3]) + '）' if triv else ''}")

    if feas:
        print("\n  --feas：只报装置可行性，不报判据读数。")
        return

    print(f"\n## §0b 格的两个分母（纪律 46）")
    print(f"  universe_all  = {len(uni_all)}（V146 口径：本 tid 的、来自别的实现的记忆条目）")
    print(f"  其中哨兵格（键 == {BASELINE}，没有任何候选能命中）= {len(uni_all - uni_real)}")
    print(f"  universe_real = {len(uni_real)}  ← 本轮判据用这个分母")

    cps = [0, 25, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 550, 588,
           700, 800, 900, 1000, 1200, 1500, 1800, 2100, n_nost]
    rows_s, cls_s, seen_s = analyze(ev_stop, uni_real, cps)
    rows_n, cls_n, seen_n = analyze(ev_nost, uni_real, cps)

    print(f"\n## §1 曲线：前 N 次判分买到多少格（分母 universe_real = {len(uni_real)}）")
    print("  | N | STOP 覆盖 | STOP 成立 | NOSTOP 覆盖 | NOSTOP 成立 |")
    print("  |---|---|---|---|---|")
    ns = {r["n"]: r for r in rows_n}
    for r in rows_s:
        m = ns.get(r["n"])
        print(f"  | {r['n']} | {r['covered']} | {r['valid']} | "
              f"{m['covered'] if m else '-'} | {m['valid'] if m else '-'} |")

    print("\n## §2 四分类（每次判分归一类，互斥；分母 = 该臂的全部判分）")
    for name, cls in (("STOP", cls_s), ("NOSTOP", cls_n)):
        print(f"  {name:7s} NONMEM={cls.get('NONMEM', 0):4d}  NEW={cls.get('NEW', 0):3d}"
              f"  RESOLVE={cls.get('RESOLVE', 0):3d}  REFINE={cls.get('REFINE', 0):4d}")
    mem_stop = cls_s.get("NEW", 0) + cls_s.get("RESOLVE", 0) + cls_s.get("REFINE", 0)
    print(f"  ⇒ 部署预算的 {1 - mem_stop / n_stop:.1%} 花在**与记忆无关**的候选上"
          f"（{n_stop - mem_stop}/{n_stop}）")
    extra_new = cls_n.get("NEW", 0) - cls_s.get("NEW", 0)
    extra_res = cls_n.get("RESOLVE", 0) - cls_s.get("RESOLVE", 0)
    print(f"  追加段（第 {n_stop + 1}~{n_nost} 次）：NEW={extra_new} RESOLVE={extra_res}"
          f"｜每 100 次新判分买到新格 = {extra_new / (n_nost - n_stop) * 100:.2f}")

    print("\n## §3 覆盖上界诊断：NOSTOP 之后仍然没试到的格（口径 universe_real）")
    cold = json.loads((EV / "v6_s235" / "trace.json").read_text(encoding="utf-8"))
    ctries: collections.Counter = collections.Counter()
    for r in cold["trace"]:
        ctries[(r["tid"], r["h"], tuple(r["key"]))] += 1
    miss = sorted(uni_real - seen_n)
    zero_cold = [c for c in miss if ctries[c] == 0]
    some_cold = [c for c in miss if ctries[c] > 0]
    print(f"  没试到的格 = {len(miss)}/{len(uni_real)}")
    print(f"    两条路径（冷启动 {len(cold['trace'])} 次 + 本轮 NOSTOP {n_nost} 次）"
          f"都没生成过该键 = {len(zero_cold)}")
    print(f"    冷启动生成过、本臂没排到 = {len(some_cold)}")
    for c in miss:
        print(f"      {c[0]:22s} {c[1]}  {str(c[2]):30s} cold_tries={ctries[c]}")
    print(f"  对照：V146 口径（universe_all={len(uni_all)}）下没试到 = "
          f"{len(uni_all - seen_n)}/{len(uni_all)}"
          f"（其中 {len(uni_all - uni_real)} 个是哨兵格，结构上不可能被命中）")

    print("\n## §3b 跑后诊断（⛔ 未预注册）：NEW 事件发生在第几轮")
    for name, ev in (("STOP", ev_stop), ("NOSTOP", ev_nost)):
        seen: set = set()
        hist: collections.Counter = collections.Counter()
        for e in ev:
            cell = (e["tid"], e["h"], tuple(e["key"]))
            if cell in uni_real and cell not in seen:
                seen.add(cell)
                hist[e["rnd"]] += 1
        print(f"  {name:7s} NEW 的轮次分布 = {dict(sorted(hist.items()))}"
              f"｜第 1 轮占比 = {hist[1] / max(1, sum(hist.values())):.1%}")

    print("\n## §4 反证（预注册 §4 的 W5）：反转事件序列")
    rows_r, cls_r, _ = analyze(list(reversed(ev_stop)), uni_real, [n_stop])
    rev_cov = rows_r[-1]["covered"]
    print(f"  正序 coverage({n_stop}) = {rows_s[-1]['covered']}"
          f"｜反序 coverage({n_stop}) = {rev_cov}")
    print(f"  ⇒ {'顺序有牙' if rev_cov != rows_s[-1]['covered'] else '⛔ 顺序无关'}")
    print("  ⚠️ 这个反证**结构上不可能变**：反转一个排列不改变它在全长上的集合。")
    print("     ⇒ W5/R3 的押破是**装置自证不可能**（纪律 47），不是对世界的读数。")

    print("\n## §4b 修正后的灵敏度对照（跑后补，替代无效的 W5）")
    cov_stop = rows_s[-1]["covered"]
    cov_nost = len(seen_n)
    print(f"  C1 计数器随预算动：coverage 序列 = "
          f"{[r['covered'] for r in rows_s][:8]} … → {rows_s[-1]['covered']}")
    print(f"  C2 匹配器两侧都非空：认出 {cov_stop} 格｜判「从未出现」{len(uni_real - seen_s)} 格")
    n_all_covered = len(uni_all & seen_n)
    print(f"  C3 换分母只改分母、不改分子：{cov_stop}/{len(uni_real)} = "
          f"{cov_stop / len(uni_real):.1%}｜{n_all_covered}/{len(uni_all)} = "
          f"{n_all_covered / len(uni_all):.1%}（V146 口径，哨兵格永远进不了分子）")


    cov_stop = rows_s[-1]["covered"]
    cov_nost = len(seen_n)
    cov_stop = rows_s[-1]["covered"]
    cov_nost = len(seen_n)
    print("\n## §5 拐点（达到终值某比例所需的最小预算；描述性，不押）")
    for frac in (0.25, 0.5, 0.75, 0.9, 1.0):
        t = max(1, -(-int(cov_nost * frac * 1000) // 1000))
        print(f"  {int(frac * 100):3d}% of NOSTOP({cov_nost}) = {t:3d} 格 → N="
              f"{first_n(ev_nost, uni_real, t)}")
    print(f"  STOP 在 N={n_stop} 处覆盖 {cov_stop} = NOSTOP 终值的 "
          f"{cov_stop / cov_nost:.1%}")

    w1 = (n_stop == 588 and to1_stop == 21 and prefix_ok and to1_nost == 21)
    w2 = cov_nost > cov_stop
    w3 = cov_nost < len(uni_real)
    w4 = (to1_nost == to1_stop) and not triv
    w5 = rev_cov != cov_stop
    print("\n## §6 判据（预注册 §4）")
    def row(tag, ok, txt):
        print(f"  {tag} {'✅' if ok else '⛔'} {txt}")
    row("W1", w1, f"自校验 588/21/前缀{'✅' if prefix_ok else '⛔'}/NOSTOP 21 —— "
                  f"{n_stop}/{to1_stop}/{to1_nost}")
    row("W2", w2, f"追加预算买到新格：coverage {cov_stop} → {cov_nost}"
                  f"（+{cov_nost - cov_stop}，预算 ×{n_nost / n_stop:.2f}）")
    row("W3", w3, f"结构性上界：coverage(NOSTOP)={cov_nost} < |universe_real|={len(uni_real)}")
    row("W4", w4, f"到 1.0 {to1_stop} → {to1_nost}；s1 有差异的实现 {len(triv)} 个")
    row("W5", w5, f"反序 coverage {rev_cov} ≠ 正序 {cov_stop}")

    print("\n## §7 反证组（预注册 §5）")
    print(f"  R1 {'✅' if n_stop == 588 and to1_stop == 21 else '⛔'} STOP == {n_stop}/{to1_stop}")
    print(f"  R2 {'✅' if prefix_ok else '⛔'} 前缀性 {len(impls) - len(bad)}/{len(impls)}")
    print(f"  R3 {'✅' if w5 else '⛔'} 反转改变覆盖")

    out = dict(n_stop=n_stop, n_nost=n_nost, to1_stop=to1_stop, to1_nost=to1_nost,
               n_universe_all=len(uni_all), n_universe_real=len(uni_real),
               n_baseline_cells=len(uni_all - uni_real),
               prefix_ok=prefix_ok, cov_stop=cov_stop, cov_nost=cov_nost,
               w1=w1, w2=w2, w3=w3, w4=w4, w5=w5,
               s1_diff=[list(k) for k in triv],
               rows_stop=rows_s, rows_nost=rows_n, cls_stop=cls_s, cls_nost=cls_n,
               miss=[list(c) for c in miss],
               miss_zero_cold=[list(c) for c in zero_cold],
               miss_some_cold=[list(c) for c in some_cold],
               miss_all_denom=len(uni_all - seen_n), rev_covered=rev_cov)
    (HERE / "readings.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print("\n  已落盘 `readings.json`")


if __name__ == "__main__":
    main()
