"""V211 读数：时间对照臂 A_late（off）—— 与 V210 的 A/B/D 并排。

⛔ 判据与门槛照抄 `PREREG_v211.md §2`。
⛔ 只读 `runs.jsonl`（末四条 = A / B / D / A_late）与 `state_alate.jsonl`。
"""
import json
import re
import sys
from math import comb

REBUILD = re.compile(r"\[\s*-1\s*\]\s*=\s*\(")
ASSIGN = re.compile(r"\[\s*-1\s*\]\s*\[\s*1\s*\]\s*=")


def fisher(a, b, c, d):
    n = a + b + c + d
    if n == 0:
        return 1.0

    def p(x):
        return comb(a + b, x) * comb(c + d, a + c - x) / comb(n, a + c)

    p0 = p(a)
    return sum(p(x) for x in range(0, min(a + b, a + c) + 1) if p(x) <= p0 + 1e-12)


def cell(run, tid, idx):
    vs = [x["vec"] for x in run["report"]["per_task"] if x["id"] == tid]
    return sum(1 for v in vs if idx < len(v) and v[idx]), len(vs)


def combo(run, tid, idxs):
    h = t = 0
    for i in idxs:
        a, b = cell(run, tid, i)
        h += a
        t += b
    return h, t


def main():
    rows = [json.loads(l) for l in open("bench_out/runs.jsonl", encoding="utf-8") if l.strip()]
    A, B, D, L = rows[-4], rows[-3], rows[-2], rows[-1]
    arms = [("A(early,off)", A), ("B(raw)", B), ("D(transferable)", D), ("A_late(off)", L)]
    print("臂                总分     t01      t04     t01[1..3]   t04[0]   run_id")
    for nm, r in arms:
        m = r["measurement"]
        print(f"{nm:<18}{m['mean']:.4f}  "
              f"{sum(x['score'] for x in r['report']['per_task'] if x['id'].startswith('t01'))/20:.4f}  "
              f"{sum(x['score'] for x in r['report']['per_task'] if x['id'].startswith('t04'))/20:.4f}  "
              f"{combo(r, 't01_parse_duration', [1, 2, 3])[0]:>3}/60   "
              f"{cell(r, 't04_merge_ranges', 0)[0]:>3}/20   {r.get('run_id')}")

    l01, l01n = combo(L, "t01_parse_duration", [1, 2, 3])
    l04, l04n = cell(L, "t04_merge_ranges", 0)
    a04, _ = cell(A, "t04_merge_ranges", 0)
    a01, a01n = combo(A, "t01_parse_duration", [1, 2, 3])

    print("\n" + "=" * 66)
    print("判据（PREREG_v211 §2）")
    print("=" * 66)
    p1 = l04 >= 6
    print(f"\nP1 t04[0]  A_early={a04}/20  B=8  D=9  A_late={l04}/20（门槛 ≥6）")
    print(f"   ⇒ {'成立（对照组追平 ⇒ t04 的增益不含评审信息）' if p1 else '不成立'}")

    d01 = abs(l01 / l01n - a01 / a01n)
    p2 = d01 >= 0.15
    print(f"\nP2 |A_late − A_early| = |{l01}/{l01n} − {a01}/60| = {d01:.4f}（门槛 ≥0.15）")
    print(f"   ⇒ {'成立（t01 也在漂）' if p2 else '不成立（A 的 t01 可复现）'}")

    blk = []
    try:
        with open("bench/evidence/l3_s299/state_alate.jsonl", encoding="utf-8") as fh:
            for ln in fh:
                d = json.loads(ln)
                blk += [l for l in d["state"].splitlines() if l.startswith("[外部评审]")]
    except FileNotFoundError:
        pass
    print(f"\nP3 正例路径：A_late 的 `[外部评审]` 块数 = {len(blk)}（门槛 =0）")
    print(f"   ⇒ {'成立' if not blk else '不成立（贴错臂）'}")

    # ── 形状（非判据）───────────────────────────────────────────────
    arows = [json.loads(l) for l in open("bench_out/answers.jsonl", encoding="utf-8") if l.strip()]
    arows = [x for x in arows if "answer" in x and "task_id" in x]
    print("\n形状（非判据，正则分类）")
    for nm, r in arms:
        mine = [x["answer"] for x in arows if x.get("run_id") == r.get("run_id")
                and x["task_id"].startswith("t04")]
        if mine:
            print(f"  {nm:<18} n={len(mine)}  对 tuple 下标赋值={sum(1 for a in mine if ASSIGN.search(a))}"
                  f"  重建元组={sum(1 for a in mine if REBUILD.search(a))}")

    # ── 与 B 的对照（这才是 V210 想回答的问题）──────────────────────
    print("\n附：若 A_late 追平 B，则 V210 的 B−A 也是漂移")
    for nm, r in arms:
        h, n = combo(r, "t01_parse_duration", [1, 2, 3])
        print(f"  {nm:<18} t01[1..3] {h:>3}/{n} = {h/n:.4f}   "
              f"t04[0] {cell(r, 't04_merge_ranges', 0)[0]:>2}/20   "
              f"p(t04 vs A_early)={fisher(cell(r,'t04_merge_ranges',0)[0], 20-cell(r,'t04_merge_ranges',0)[0], a04, 20-a04):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
