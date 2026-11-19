"""V209 读数：把每臂的 per_task(id, rep, score, vec) 汇总到断言粒度。

⛔ 只用 `rep.per_task` 的原始行，不做二次判定：`vec` 是 scorer 给的逐条通过向量。
⛔ 目标格 T 与天花板格 C 的下标写死在 PREREG §2，本脚本不许改。
"""
import json
import sys
from collections import defaultdict
from math import comb


def fisher(a, b, c, d):
    """2×2 双侧 Fisher 精确检验。⛔ 只做显著性标注，不改判据阈值。"""
    n = a + b + c + d
    if n == 0:
        return 1.0

    def p(x):
        return comb(a + b, x) * comb(c + d, a + c - x) / comb(n, a + c)

    p0 = p(a)
    return sum(p(x) for x in range(0, min(a + b, a + c) + 1) if p(x) <= p0 + 1e-12)

# PREREG §2 钉死的格子（task -> vec 下标）
TARGET = {
    "t01_parse_duration": [0, 1, 2],   # 被点评名（ValueError）
    "t04_merge_ranges": [0],           # 被点评名（TypeError 元组不可变）
}
CEILING = {
    "t01_parse_duration": [4],         # 源尝试已通过
    "t04_merge_ranges": [1, 2, 5],     # 源尝试已通过
}


def load(path="bench_out/runs.jsonl"):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def cells(run):
    """(task_id, rep) -> vec"""
    out = {}
    for row in (run.get("report") or {}).get("per_task", []):
        out[(row["id"], row.get("rep"))] = row.get("vec")
    return out


def rate(vectors, idxs):
    """在给定 (task,idx) 集合上算通过率。分母 = 有效样本数（vec 为 None 的不进）。"""
    hit = tot = 0
    missing = 0
    for (tid, rep), vec in vectors.items():
        if tid not in idxs:
            continue
        if not vec:
            missing += 1
            continue
        for i in idxs[tid]:
            if i < len(vec):
                tot += 1
                hit += int(bool(vec[i]))
    return hit, tot, missing


def main():
    rows = load()
    print(f"runs.jsonl 共 {len(rows)} 条")
    last = rows[-3:]
    names = ["A(off)", "B(prev-self)", "C(prev)"]
    store = {}
    for nm, r in zip(names, last):
        m = r.get("measurement") or {}
        rep = r.get("report") or {}
        v = cells(r)
        store[nm] = v
        print(f"\n=== {nm}  run_id={r.get('run_id')}  repeat={r.get('repeat')}  "
              f"mean={m.get('mean'):.4f}  elapsed={(rep.get('elapsed_s') or 0):.0f}s")
        for tid in TARGET:
            sc = [ (row.get("score") or 0) for row in rep.get("per_task", []) if row["id"] == tid ]
            if sc:
                print(f"   {tid:22s} n={len(sc)} mean={sum(sc)/len(sc):.4f} "
                      f"min={min(sc):.3f} max={max(sc):.3f}")
        h, t, miss = rate(v, TARGET)
        print(f"   目标格 T:  {h}/{t} = {h/t if t else float('nan'):.4f}   (缺 vec {miss})")
        h, t, miss = rate(v, CEILING)
        print(f"   天花板格:  {h}/{t} = {h/t if t else float('nan'):.4f}   (缺 vec {miss})")

    print("\n=== 逐断言通过率（分母 = 该臂该题的次数；⛔ 不跨题合并）")
    hdr = "task/idx".ljust(26) + "".join(n.rjust(13) for n in names) + "   Fisher(B vs A)"
    print(hdr)
    for tid in TARGET:
        n_tests = max(len(v) for v in store["A(off)"].values() if v)
        for i in range(n_tests):
            line = f"{tid.split('_')[0]}[{i}]".ljust(26)
            got = {}
            for nm in names:
                vals = [v[i] for k, v in store[nm].items() if k[0] == tid and v and i < len(v)]
                got[nm] = (sum(vals), len(vals))
                line += (f"{sum(vals)}/{len(vals)}".rjust(13) if vals else "—".rjust(13))
            a, na = got["A(off)"]
            b, nb = got["B(prev-self)"]
            line += f"   p={fisher(a, na - a, b, nb - b):.4f}"
            print(line)

    print("\n=== 判据对账")
    def agg(nm, idxs):
        h, t, _ = rate(store[nm], idxs)
        return (h / t) if t else float("nan")
    tA, tB, tC = agg("A(off)", TARGET), agg("B(prev-self)", TARGET), agg("C(prev)", TARGET)
    cA, cB, cC = agg("A(off)", CEILING), agg("B(prev-self)", CEILING), agg("C(prev)", CEILING)
    print(f"P2 目标格: A={tA:.4f} B={tB:.4f} C={tC:.4f}  |  B-A={tB-tA:+.4f} (押 >=0.25)  |  C-A={tC-tA:+.4f} (押 <0.15)")
    print(f"P4 天花板: A={cA:.4f} B={cB:.4f}  |  B-A={cB-cA:+.4f} (押 >=-0.10)")
    ok = {
        "P2": (tB - tA) >= 0.25,
        "P3": abs(tC - tA) < 0.15 and (tB - tA) > (tC - tA),
        "P4": (cB - cA) >= -0.10,
    }
    for k, v in ok.items():
        print(f"  {k}: {'✅ 成立' if v else '❌ 不成立'}")

    json.dump({"A_off": {f"{k[0]}#{k[1]}": v for k, v in store['A(off)'].items()},
               "B_prev_self": {f"{k[0]}#{k[1]}": v for k, v in store['B(prev-self)'].items()},
               "C_prev": {f"{k[0]}#{k[1]}": v for k, v in store['C(prev)'].items()},
               "target_rate": {"A": tA, "B": tB, "C": tC},
               "ceiling_rate": {"A": cA, "B": cB, "C": cC}},
              open("bench/evidence/l3_s297/readings_v209.json", "w"), ensure_ascii=False, indent=1)
    print("\nwrote readings_v209.json")


if __name__ == "__main__":
    sys.exit(main())
