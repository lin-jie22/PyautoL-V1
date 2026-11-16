#!/usr/bin/env python3
"""V167：把 V166 的「语料已有惯例 ⇒ 不值得试」先验搬到 `T2`（往 `in [...]` 插词）。

三臂 + 一对照（预注册 `PREREG_v167.md`）：
  `B0` = `P2`（V166b 的 T5 闸，基线，应复现 366 / 25/25）
  `B1` = `B0` + `Q1`：`w` ∈ 兄弟常量集 ⇒ 不生成（与 T5 同向）
  `B2` = `B0` + `Q1r`：`w` ∉ 兄弟常量集 ⇒ 不生成（**反向**，探索臂）
  `PERM` = `B0` + 与 `Q1` **逐调用同预算**的随机丢弃（零假设由重排造，纪律 49）

T2 闸一律在**付费点之前**（gen 返回前）执行（纪律 85）。
"""
import ast
import collections
import json
import pathlib
import random
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198", "v6_s210", "v6_s236"):
    sys.path.insert(0, str(EV / d))
import idiom_transfer as I          # noqa: E402
import search as S                  # noqa: E402
import v119 as V                    # noqa: E402
import v120 as W                    # noqa: E402
import v121 as X                    # noqa: E402
sys.path.insert(0, str(EV / "v6_s249"))
import v161 as M                    # noqa: E402
sys.path.insert(0, str(EV / "v6_s251"))
import v164 as U                    # noqa: E402
import v165 as N                    # noqa: E402
import v166 as A                    # noqa: E402
import v166b as B                   # noqa: E402

SEEDS = list(range(1, 21))
STATS = collections.Counter()
TID = "g7e1_parse_flag"
UNSAT = ("1aa5099d", "200ebf6e", "37a4f17f", "a579b07a", "f2d1efa6")


def t2_word(lab):
    """`T2:cont{i}.{j}:+{w!r}` → w。不是 T2 返回 `None`。"""
    if not lab.startswith("T2:"):
        return None
    try:
        return ast.literal_eval(lab.split(":+", 1)[1])
    except Exception:                                # noqa: BLE001
        return ""


def make_gen(mode, sib_consts, rng=None):
    """在 V166b 的 `P2` 之上加 T2 闸。"""
    base = B.make_gen("p2", sib_consts)

    def gen(tid, code, Sx, ch, ex):
        out = base(tid, code, Sx, ch, ex)
        t2 = [(lab, src) for lab, src in out if lab.startswith("T2:")]
        if not t2:
            return out
        rest = [(lab, src) for lab, src in out if not lab.startswith("T2:")]
        if mode == "u6":
            STATS["t2_kept"] += len(t2)
            return out
        if mode in ("perm", "perm_r"):
            # ⚠ 两个置换的**预算各自与它对应的臂逐调用相同**（纪律 49/84）：
            #   `perm`   ↔ `q1`  （丢 k 条，k = 会被 `Q1` 切的条数）
            #   `perm_r` ↔ `q1r`（丢 k 条，k = 会被 `Q1r` 切的条数）
            if mode == "perm":
                k = sum(1 for lab, _s in t2 if t2_word(lab) in sib_consts)
            else:
                k = sum(1 for lab, _s in t2 if t2_word(lab) not in sib_consts)
            if k:
                drop = set(rng.sample(range(len(t2)), k))
                t2 = [c for i, c in enumerate(t2) if i not in drop]
                STATS[f"t2_{mode}_dropped"] += k
            STATS["t2_kept"] += len(t2)
            return rest + t2
        kept = []
        for lab, src in t2:
            w = t2_word(lab)
            hit_pred = (w in sib_consts) if mode == "q1" else (w not in sib_consts)
            if hit_pred:
                STATS[f"t2_{mode}_skipped"] += 1
            else:
                kept.append((lab, src))
        STATS[f"t2_{mode}_kept"] += len(kept)
        return rest + kept
    return gen


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
    s0 = {(t, h): V.ev(t, corpus[t][h])[0] for t, h in impls}
    mem = json.loads(X.MEM_FILE.read_text())

    def mines(tid, h):
        return {(a, b) for kk in mem[tid] if kk["h"] != h for a, b in [tuple(kk["act"])]}

    ctx, meta, sibs = {}, {}, {}
    for tid, h in impls:
        code = corpus[tid][h]
        Sx, _ = S.wordset(tid, code, True)
        ch, m = M.chains_meta(corpus, tid, h, s0)
        ex = W.harvest_exprs(corpus, tid, h)
        ctx[(tid, h)] = (Sx, ch, ex)
        meta[(tid, h)] = dict(m=m, n_sib=len(corpus[tid]) - 1)
        sc = set()
        for x in corpus[tid]:
            if x != h:
                sc |= B.const_values(corpus[tid][x])
        sibs[(tid, h)] = sc

    def run(mode, rng=None):
        STATS.clear()
        out = {}
        for tid, h in impls:
            g = make_gen(mode, sibs[(tid, h)], rng)
            out[(tid, h)] = A.replay(tid, corpus[tid][h], *ctx[(tid, h)],
                                     meta[(tid, h)], mines(tid, h), g)
        return out, dict(STATS)

    def summ(out):
        return dict(hit=sum(1 for r, _ in out.values() if r["hit"]),
                    n_eval=sum(r["n_eval"] for r, _ in out.values()),
                    mean_s1=sum(r["s1"] for r, _ in out.values()) / len(out),
                    t2_eval=sum(C["T2_eval"] for _r, C in out.values()),
                    t2_gen=sum(C["T2_gen"] for _r, C in out.values()),
                    t2_path=sum(C["T2_path"] for _r, C in out.values()))

    ref = json.loads((HERE / "readings_v166b.json").read_text())
    B0, st0 = run("u6")
    S0 = summ(B0)
    B1, st1 = run("q1")
    S1 = summ(B1)
    B2, st2 = run("q1r")
    S2 = summ(B2)

    print("## §1 主读数")
    print("  | 臂 | 命中 | 判分 | `T2` 生成 | `T2` 判分 | `T2` 路径 | mean s1 |")
    for nm, sm, st in (("`B0`=P2", S0, st0), ("`B1`=P2+Q1", S1, st1),
                       ("`B2`=P2+Q1r", S2, st2)):
        print(f"  | {nm} | {sm['hit']}/25 | {sm['n_eval']} | {sm['t2_gen']} | "
              f"{sm['t2_eval']} | {sm['t2_path']} | {sm['mean_s1']:.4f} |")
    print(f"\n  `Q1` 切 `T2` 生成 {st1.get('t2_q1_skipped', 0)} 条、保留 {st1.get('t2_q1_kept', 0)} 条")
    print(f"  `Q1r` 切 `T2` 生成 {st2.get('t2_q1r_skipped', 0)} 条、保留 {st2.get('t2_q1r_kept', 0)} 条")

    print("\n## §2 逐实现：`B1`/`B2` vs `B0`")
    print("  | impl | s0 | `B0` | `B1` | `B2` | 掉命中臂 |")
    for k in impls:
        a, b, c = B0[k][0], B1[k][0], B2[k][0]
        if s0[k] >= 1.0:
            continue
        lost = []
        if a["hit"] and not b["hit"]:
            lost.append("`B1`")
        if a["hit"] and not c["hit"]:
            lost.append("`B2`")
        print(f"  | {k[1][:8]} | {s0[k]:.2f} | {a['hit']} {a['s1']:.2f} | "
              f"{b['hit']} {b['s1']:.2f} | {c['hit']} {c['s1']:.2f} | "
              f"{'、'.join(lost) or '—'} |")

    print("\n## §3 诊断：`B0` 的 `T2` 路径用了哪个词（只看未饱和实现）")
    for k in impls:
        if s0[k] >= 1.0:
            continue
        r, C = B0[k]
        if not C["T2_path"]:
            continue
        ends = [c for c in r["trace"] if abs((V.ev(k[0], c)[0] or 0) - r["s1"]) < 1e-9]
        if not ends:
            print(f"  {k[1][:8]}：找到 `T2` 路径计数 {C['T2_path']} 但无终点（如实报）")
            continue
        labs = A.path_of(r["trace"], ends[0])
        t2labs = [lb for lb in labs if lb.startswith("T2:")]
        ws = [t2_word(lb) for lb in t2labs]
        cut = [w for w in ws if w in sibs[k]]
        print(f"  {k[1][:8]}：`T2` 路径 {t2labs}")
        print(f"    插入词 {ws}｜`Q1` 会切 {len(cut)}/{len(ws)}：{cut}")

    print("\n## §4 `PERM`：同预算随机丢（20 seeds）")
    rows = []
    for sd in SEEDS:
        out, st = run("perm", random.Random(sd))
        sm = summ(out)
        lost = [k[1][:8] for k in B0 if B0[k][0]["hit"] and not out[k][0]["hit"]]
        rows.append((sd, sm["hit"], sm["n_eval"], st.get("t2_perm_dropped", 0), lost))
        print(f"  seed {sd:2d}：命中 {sm['hit']}/25｜判分 {sm['n_eval']}｜"
              f"随机丢 {st.get('t2_perm_dropped', 0)} 条｜丢命中 {lost}")
    keep = sum(1 for r in rows if r[1] == 25)
    print(f"\n  `PERM` 保 25/25 的 seed：**{keep}/20**｜"
          f"命中中位 {sorted(r[1] for r in rows)[10]}")

    print("\n## §4b `PERM_R`：与 `Q1r` 逐调用同预算的随机丢（20 seeds）")
    rows_r = []
    for sd in SEEDS:
        out, st = run("perm_r", random.Random(sd))
        sm = summ(out)
        lost = [k[1][:8] for k in B0 if B0[k][0]["hit"] and not out[k][0]["hit"]]
        rows_r.append((sd, sm["hit"], sm["n_eval"], st.get("t2_perm_r_dropped", 0), lost))
        print(f"  seed {sd:2d}：命中 {sm['hit']}/25｜判分 {sm['n_eval']}｜"
              f"随机丢 {st.get('t2_perm_r_dropped', 0)} 条｜丢命中 {lost}")
    keep_r = sum(1 for r in rows_r if r[1] == 25)
    print(f"\n  `PERM_R` 保 25/25 的 seed：**{keep_r}/20**｜"
          f"命中中位 {sorted(r[1] for r in rows_r)[10]}")

    print("\n## §5 免费性（谓词期间不得偷偷判分）")
    for mode in ("q1", "q1r"):
        delta = 0
        for tid, h in impls:
            g = make_gen(mode, sibs[(tid, h)])
            before = len(V.CACHE)
            g(tid, corpus[tid][h], *ctx[(tid, h)])
            delta += len(V.CACHE) - before
        print(f"  `{mode}` 生成 25 个根节点，`V.CACHE` 增量 = **{delta}**（必须 0）")

    print("\n## §6 结构命题（H1）：`Q1` 在未饱和实现上的保留集")
    h1_ok = True
    for h in UNSAT:
        Sx, _ = S.wordset(TID, corpus[TID][h], True)
        keepw = sorted(w for w in Sx if w not in sibs[(TID, h)])
        print(f"  {h}：保留 {keepw}")
        if any(w in sibs[(TID, h)] for w in keepw):
            h1_ok = False
    flagish = {"on", "true", "yes", "y", "off", "no", "n", "1", "0"}
    leak = [w for h in UNSAT for w in S.wordset(TID, corpus[TID][h], True)[0]
            if w not in sibs[(TID, h)] and w in flagish]
    print(f"  保留集里出现的 flag 词：{sorted(set(leak))}（押空集）")

    print("\n## §7 判据（PASS = 我的押成立）")
    rows_c = []
    MARKS = {"PASS": "✅", "FAIL": "⛔", "NA": "⬜"}

    def row(tag, st, msg):
        rows_c.append((tag, st, msg))
        print(f"  {tag} {MARKS[st]} [{st}]  {msg}")

    same_b0 = (S0["hit"] == ref["p2"]["hit"] and S0["n_eval"] == ref["p2"]["n_eval"]
               and abs(S0["mean_s1"] - ref["p2"]["mean_s1"]) < 1e-9)
    row("T1", "PASS" if same_b0 else "FAIL",
        f"`B0` 复现 V166b `P2`：{S0['hit']}/25、判分 {S0['n_eval']}"
        f"（ref {ref['p2']['hit']}/25、{ref['p2']['n_eval']}）")
    row("T2", "PASS" if h1_ok and not leak else "FAIL",
        f"H1 结构命题：`Q1` 保留集里 flag 词 {sorted(set(leak)) or '∅'}（押 ∅）")
    lost1 = sorted(k[1][:8] for k in B0 if B0[k][0]["hit"] and not B1[k][0]["hit"])
    sub = set(lost1) <= set(UNSAT)
    row("T3", "PASS" if (S1["hit"] < 25 and sub) else "FAIL",
        f"H2：`B1` 命中 {S1['hit']}/25（押 <25）｜掉的 {lost1} ⊆ 五个未饱和实现 {sub}")
    row("T4", "PASS" if keep <= 3 else ("NA" if keep < 10 else "FAIL"),
        f"H5：`PERM` 保 25/25 的 seed {keep}/20（≤3 ⇒ 先验有信息）")
    t2red = (1 - S2["t2_eval"] / S0["t2_eval"]) * 100 if S0["t2_eval"] else 0
    row("T5", "PASS" if (S2["hit"] == 25 and t2red >= 15) else "FAIL",
        f"H4：`B2` 命中 {S2['hit']}/25（押 25）、`T2` 判分 {S0['t2_eval']}→{S2['t2_eval']}"
        f"（−{t2red:.1f}%，押 ≥15%）")
    delta = 0
    for mode in ("q1", "q1r"):
        for tid, h in impls:
            g = make_gen(mode, sibs[(tid, h)])
            before = len(V.CACHE)
            g(tid, corpus[tid][h], *ctx[(tid, h)])
            delta += len(V.CACHE) - before
    row("T6", "PASS" if delta == 0 else "FAIL", f"谓词判分增量 {delta}（必须 0）")
    # T7 是**补充对照**（预注册 §1 H4 只押命中与降幅，没押「选得准」）：
    # `Q1r` 只切 84 条，若随机切 84 条也能保 25/25，则 H4 的解读必须降级。
    row("T7", "PASS" if keep_r <= 3 else ("NA" if keep_r < 10 else "FAIL"),
        f"[补] `PERM_R` 保 25/25 的 seed {keep_r}/20（≤3 ⇒ `Q1r` 选得准）")

    json.dump(dict(
        b0=S0, b1=S1, b2=S2, skip0=st0, skip1=st1, skip2=st2,
        per={f"{k[0]}::{k[1]}": dict(s0=s0[k], b0=[B0[k][0]["hit"], B0[k][0]["s1"]],
                                     b1=[B1[k][0]["hit"], B1[k][0]["s1"]],
                                     b2=[B2[k][0]["hit"], B2[k][0]["s1"]])
             for k in impls},
        lost_b1=lost1,
        perm=[dict(seed=r[0], hit=r[1], n_eval=r[2],
                   dropped=r[3], lost=r[4]) for r in rows],
        perm_r=[dict(seed=r[0], hit=r[1], n_eval=r[2],
                     dropped=r[3], lost=r[4]) for r in rows_r],
        keep_seeds=keep, keep_seeds_r=keep_r, cache_delta=delta,
        criteria=[dict(id=r[0], status=r[1], msg=r[2]) for r in rows_c]),
        open(HERE / "readings_v167.json", "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()
