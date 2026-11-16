#!/usr/bin/env python3
"""V166b：`P2` 闸 —— 用**字面量等式**（不是子串）判定「语料已有惯例」。

与 `v166.py` 的唯一差别在谓词粒度；`replay` / `lit_of` / `path_of` 直接复用 `v166`，
并用 R1（`U6` 复现 V165）钉死复用没过期。
"""
import ast
import collections
import io
import json
import pathlib
import random
import sys
import tokenize

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

SEEDS = list(range(1, 21))
STATS = collections.Counter()


def const_values(src):
    """一个实现的字符串常量值集合（含 f-string 内部的格式说明符常量）。"""
    try:
        t = ast.parse(src)
    except SyntaxError:
        vals = set()
        try:
            for tok in tokenize.generate_tokens(io.StringIO(src).readline):
                if tok.type != tokenize.STRING:
                    continue
                body = tok.string.lstrip("rbuRBU")
                if body[:1] in ("f", "F"):
                    STATS["sib_fstring_skipped"] += 1
                    continue
                try:
                    vals.add(ast.literal_eval(tok.string))
                except Exception:                    # noqa: BLE001
                    STATS["sib_eval_fail"] += 1
            return vals
        except Exception:                            # noqa: BLE001
            STATS["sib_tokenize_fail"] += 1
            return vals
    return {n.value for n in ast.walk(t)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def make_gen(mode, sib_consts, rng=None):
    def gen(tid, code, Sx, ch, ex):
        out = list(X.gen(tid, code, Sx, ch, ex))
        t5 = U.candidates_T5(code)
        lits = A.lit_of(code)
        if mode == "perm2":
            k = sum(1 for lab, _s in t5 if lits.get(lab) in sib_consts)
            if k:
                drop = set(rng.sample(range(len(t5)), k))
                t5 = [c for i, c in enumerate(t5) if i not in drop]
                STATS["t5_perm2_dropped"] += k
        elif mode == "p2":
            kept = []
            for lab, src in t5:
                if lits.get(lab) in sib_consts:
                    STATS["t5_p2_skipped"] += 1
                    STATS["t5_p2_skipped_chars"] += 0
                else:
                    kept.append((lab, src))
                    STATS["t5_p2_kept"] += 1
            t5 = kept
        out += t5 + N.candidates_T6(code)
        return out
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
            if x == h:
                continue
            sc |= const_values(corpus[tid][x])
        sibs[(tid, h)] = sc

    def run(mode, rng=None):
        STATS.clear()
        out = {}
        for tid, h in impls:
            g = make_gen(mode, sibs[(tid, h)], rng)
            Sx, ch, ex = ctx[(tid, h)]
            out[(tid, h)] = A.replay(tid, corpus[tid][h], Sx, ch, ex, meta[(tid, h)],
                                     mines(tid, h), g)
        return out, dict(STATS)

    def summ(out):
        return dict(hit=sum(1 for r, _ in out.values() if r["hit"]),
                    n_eval=sum(r["n_eval"] for r, _ in out.values()),
                    mean_s1=sum(r["s1"] for r, _ in out.values()) / len(out),
                    t5_eval=sum(C["T5_eval"] for _r, C in out.values()))

    U6, _ = run("u6")
    SU = summ(U6)
    P2, st2 = run("p2")
    S2 = summ(P2)
    ref = json.loads((HERE / "readings_v165.json").read_text())
    same_ref = (SU["hit"] == ref["u6"]["hit"] and SU["n_eval"] == ref["u6"]["n_eval"]
                and abs(SU["mean_s1"] - ref["u6"]["mean_s1"]) < 1e-9)

    print("## §1 主读数")
    print(f"  `U6` 命中 {SU['hit']}/25｜判分 {SU['n_eval']}｜`T5` 判分 {SU['t5_eval']}")
    print(f"  `P2` 命中 {S2['hit']}/25｜判分 {S2['n_eval']}｜`T5` 判分 {S2['t5_eval']}"
          f"｜跳过 `T5` 候选 {st2.get('t5_p2_skipped', 0)} 条")

    print("\n## §2 逐实现：`P2` vs `U6`")
    diff = [(k, U6[k][0]["hit"], round(U6[k][0]["s1"], 6), P2[k][0]["hit"],
             round(P2[k][0]["s1"], 6))
            for k in U6
            if (U6[k][0]["hit"], round(U6[k][0]["s1"], 6))
            != (P2[k][0]["hit"], round(P2[k][0]["s1"], 6))]
    print(f"  不同的实现 {len(diff)} 个：{[(d[0][1][:8], d[1:]) for d in diff]}")

    print("\n## §3 S2：`'( '` 在两边分别被怎么判（`P1` 子串 vs `P2` 相等）")
    tid, hh = "g7d3_fmt_amount", "5755e033"
    code = corpus[tid][hh]
    raw = "\n".join(corpus[tid][x] for x in corpus[tid] if x != hh)
    lits = A.lit_of(code)
    for lab, src in U.candidates_T5(code):
        if lits.get(lab) == "( ":
            print(f"  {lab}: 字面量 {lits[lab]!r}｜P1(子串)跳过={lits[lab] in raw}｜"
                  f"P2(相等)跳过={lits[lab] in sibs[(tid, hh)]}")

    print("\n## §4 `PERM2` 同预算随机置换（20 seeds）")
    rows = []
    for sd in SEEDS:
        out, st = run("perm2", random.Random(sd))
        SM = summ(out)
        lost = [k for k in U6 if U6[k][0]["hit"] and not out[k][0]["hit"]]
        rows.append((sd, SM["hit"], SM["n_eval"], st.get("t5_perm2_dropped", 0), lost))
        print(f"  seed {sd:2d}：命中 {SM['hit']}/25｜判分 {SM['n_eval']}｜"
              f"随机丢 {st.get('t5_perm2_dropped', 0)} 条｜丢命中 "
              f"{[x[1][:8] for x in lost]}")
    keep = sum(1 for r in rows if r[1] == 25)
    print(f"\n  `PERM2` 保住 25/25 的 seed：**{keep}/20**｜"
          f"命中中位 {sorted(r[1] for r in rows)[10]}｜判分中位 {sorted(r[2] for r in rows)[10]}")

    print("\n## §5 免费性")
    delta = 0
    for tid, h in impls:
        g = make_gen("p2", sibs[(tid, h)])
        before = len(V.CACHE)
        g(tid, corpus[tid][h], *ctx[(tid, h)])
        delta += len(V.CACHE) - before
    print(f"  生成 25 个实现的根节点候选，`V.CACHE` 增量 = **{delta}**（必须 0）")

    print("\n## §6 判据（PASS / FAIL / NA）")
    rows_c = []
    MARKS = {"PASS": "✅", "FAIL": "⛔", "NA": "⬜"}

    def row(tag, st, msg):
        rows_c.append((tag, st, msg))
        print(f"  {tag} {MARKS[st]} [{st}]  {msg}")

    row("R1", "PASS" if same_ref else "FAIL",
        f"`U6` 复现 V165：{SU['hit']}（{ref['u6']['hit']}）/ {SU['n_eval']}"
        f"（{ref['u6']['n_eval']}）")
    row("R2", "PASS" if (S2["hit"] == 25 and not diff) else "FAIL",
        f"`P2` 命中 {S2['hit']}/25、与 `U6` 不同的 {len(diff)} 个（押 0）")
    row("R3", "PASS" if S2["n_eval"] <= 366 else "FAIL",
        f"`P2` 判分 {S2['n_eval']}（−{(1-S2['n_eval']/SU['n_eval'])*100:.1f}%；押 ≤366）")
    if keep <= 3:
        st4 = "PASS"
    elif keep >= 10:
        st4 = "FAIL"
    else:
        st4 = "NA"
    row("R4", st4, f"`PERM2` 保 25/25 的 seed {keep}/20（≤3 有信息；≥10 反转）")
    row("R5", "PASS" if (delta == 0 and st2.get("t5_p2_skipped", 0) > 0) else "FAIL",
        f"谓词判分增量 {delta}｜跳过 {st2.get('t5_p2_skipped', 0)}｜"
        f"保留 {st2.get('t5_p2_kept', 0)}")
    v166 = json.loads((HERE / "readings_v166.json").read_text())
    row("R6", "PASS", f"两端跳过数：`P1` {v166['skip'].get('t5_p1_skipped', 0)} 条 ／ "
                      f"`P2` {st2.get('t5_p2_skipped', 0)} 条；"
                      f"`P1` 判分 {v166['p1']['n_eval']} ／ `P2` {S2['n_eval']}")

    json.dump(dict(u6=SU, p2=S2, skip=st2,
                   perm=[dict(seed=r[0], hit=r[1], n_eval=r[2], dropped=r[3],
                              lost=[[k[0], k[1]] for k in r[4]]) for r in rows],
                   keep_seeds=keep, diff=[[k[0], k[1]] for k, *_ in diff],
                   cache_delta=delta,
                   criteria=[dict(id=r[0], status=r[1], msg=r[2]) for r in rows_c]),
              open(HERE / "readings_v166b.json", "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()
