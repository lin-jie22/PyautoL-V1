"""V210 读数：三臂 A(off) / B(prev-self, raw) / D(prev-self, transferable)。

⛔ 判据与阈值**照抄 PREREG_v210.md §3**，本脚本不许改阈值、不许补跑。
⛔ 只用 `rep.per_task` 的 `vec`（scorer 给的逐条通过向量）与 `state_*.jsonl` 的**原文**。
"""
import json
import sys
from math import comb


def fisher(a, b, c, d):
    n = a + b + c + d
    if n == 0:
        return 1.0

    def p(x):
        return comb(a + b, x) * comb(c + d, a + c - x) / comb(n, a + c)

    p0 = p(a)
    return sum(p(x) for x in range(0, min(a + b, a + c) + 1) if p(x) <= p0 + 1e-12)


TASKS = ("t01_parse_duration", "t04_merge_ranges")


def cells(run):
    out = {}
    for row in (run.get("report") or {}).get("per_task", []):
        v = row.get("vec")
        if v is None:
            continue
        out[(row["id"], row.get("rep"))] = v
    return out


def rate(vecs, tid, idxs):
    hit = tot = 0
    for (t, _rep), v in vecs.items():
        if t != tid:
            continue
        for i in idxs:
            if i < len(v):
                tot += 1
                hit += int(bool(v[i]))
    return hit, tot


def pct(h, t):
    return (h / t) if t else float("nan")


def blocks(path):
    """该臂所有 `[外部评审]` 块的原文（去重：同一题同一块会重复多步）。"""
    out = []
    try:
        fh = open(path, encoding="utf-8")
    except FileNotFoundError:
        return out
    with fh:
        for ln in fh:
            d = json.loads(ln)
            for line in (d.get("state") or "").splitlines():
                if line.startswith("[外部评审]"):
                    out.append((d.get("task"), line))
    return out


def main():
    rows = [json.loads(l) for l in open("bench_out/runs.jsonl", encoding="utf-8") if l.strip()]
    last3 = rows[-3:]
    names = ["A(off)", "B(prev-self,raw)", "D(prev-self,transferable)"]
    keys = ["a", "b", "d"]
    store, means = {}, {}
    print(f"runs.jsonl 共 {len(rows)} 条；取末 3 条（按运行顺序 = A,B,D）\n")
    for nm, k, r in zip(names, keys, last3):
        m = r.get("measurement") or {}
        rep = r.get("report") or {}
        v = cells(r)
        store[k] = v
        means[k] = m.get("mean")
        print(f"=== {nm}   run_id={r.get('run_id')}  repeat={r.get('repeat')}  "
              f"mean={m.get('mean'):.4f}  elapsed={(rep.get('elapsed_s') or 0):.0f}s  "
              f"记录 {rep.get('recorded')} 题次")
        for tid in TASKS:
            sc = [row.get("score") or 0 for row in rep.get("per_task", []) if row["id"] == tid]
            if sc:
                print(f"   {tid:22s} n={len(sc)}  mean={sum(sc)/len(sc):.4f}")
    print()

    # ── 逐格表 ────────────────────────────────────────────────────────
    print("逐断言（分母 = 每臂该题 repeat 次数）")
    print(f"{'格':<44}{'A':>8}{'B':>8}{'D':>8}{'p(B,A)':>9}{'p(D,A)':>9}")
    nrep = {}
    for k, v in store.items():
        for (t, _r), vec in v.items():
            nrep.setdefault(t, set()).add(len(vec))
    allcells = []
    for tid in TASKS:
        for i in range(max(nrep.get(tid, {0}))):
            row = []
            for k in keys:
                h, t = rate(store[k], tid, [i])
                row.append((h, t))
            if not row[0][1]:
                continue
            allcells.append((tid, i, row))
            pb = fisher(row[1][0], row[1][1] - row[1][0], row[0][0], row[0][1] - row[0][0])
            pd = fisher(row[2][0], row[2][1] - row[2][0], row[0][0], row[0][1] - row[0][0])
            lab = f"{tid}[{i}]"
            print(f"{lab:<44}{row[0][0]:>4}/{row[0][1]:<3}{row[1][0]:>4}/{row[1][1]:<3}"
                  f"{row[2][0]:>4}/{row[2][1]:<3}{pb:>9.4f}{pd:>9.4f}")

    # ── §3 判据 ───────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("判据（PREREG_v210 §3，阈值照抄）")
    print("=" * 70)

    # P1 装置正面路径：扫 state 原文
    st = {k: blocks(f"bench/evidence/l3_s298/state_{k}.jsonl") for k in keys}
    def cnt(k, needle):
        return sum(1 for _t, line in st[k] if needle in line)
    d_exp = cnt("d", "expected")
    d_unp = cnt("d", "unpack")
    b_exp = cnt("b", "expected")
    d_typ = cnt("d", "TypeError: 'tuple' object")
    lens = {k: sorted({len(line) for _t, line in st[k]}) for k in keys}
    print(f"\nP1 装置正面路径（块数 A={len(st['a'])} B={len(st['b'])} D={len(st['d'])}）")
    print(f"   D 含 expected={d_exp}  含 unpack={d_unp}    B 含 expected={b_exp}")
    print(f"   D 含 `TypeError: 'tuple' object` = {d_typ}（正例对照：不许被一起吃掉）")
    print(f"   块长分布（去重）A={lens['a']} B={lens['b']} D={lens['d']}")
    p1 = (d_exp == 0 and d_unp == 0 and b_exp > 0 and d_typ >= 1 and len(st["a"]) == 0)
    print(f"   ⇒ {'成立' if p1 else '不成立'}")

    def r(k, tid, idxs):
        h, t = rate(store[k], tid, idxs)
        return pct(h, t), h, t

    ta, ha, na = r("a", "t01_parse_duration", [1, 2, 3])
    tb, hb, nb = r("b", "t01_parse_duration", [1, 2, 3])
    td, hd, nd = r("d", "t01_parse_duration", [1, 2, 3])
    print(f"\nP2 主判据 t01[1..3]   A={ha}/{na}={ta:.4f}  B={hb}/{nb}={tb:.4f}  D={hd}/{nd}={td:.4f}")
    print(f"   D−A = {td - ta:+.4f}（门槛 ≥ −0.10）")
    print(f"   ⇒ {'成立' if td >= ta - 0.10 else '不成立'}")

    t4a, h4a, n4a = r("a", "t04_merge_ranges", [0])
    t4d, h4d, n4d = r("d", "t04_merge_ranges", [0])
    t4b, h4b, n4b = r("b", "t04_merge_ranges", [0])
    print(f"\nP3 保留增益 t04[0]   A={h4a}/{n4a}  B={h4b}/{n4b}  D={h4d}/{n4d}（门槛 ≥ 6/20）")
    print(f"   ⇒ {'成立' if h4d >= 6 else '不成立'}")

    print(f"\nP4 装置处在 V209 状态：B−A(t01[1..3]) = {tb - ta:+.4f}（门槛 ≤ −0.30）")
    print(f"   ⇒ {'成立' if tb - ta <= -0.30 else '不成立'}")

    t0a, h0a, n0a = r("a", "t01_parse_duration", [0])
    t0d, h0d, n0d = r("d", "t01_parse_duration", [0])
    print(f"\nP5（非判据）t01[0]  A={h0a}/{n0a}  D={h0d}/{n0d}  Δ={t0d - t0a:+.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
