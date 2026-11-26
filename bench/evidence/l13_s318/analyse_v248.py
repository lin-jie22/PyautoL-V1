"""V248 汇总：三臂对照 + E 臂定位/表达式分类。只读 readings_v248.jsonl。"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import statistics

D = pathlib.Path(__file__).resolve().parent
rows = [json.loads(x) for x in
        (D / "readings_v248.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
N = 6
by = collections.defaultdict(list)
for r in rows:
    by[r["arm"]].append(r)

START = (D.parents[2] / "bench/evidence/l9_s310/cand/90579ecebe633352.py").read_text(encoding="utf-8").splitlines()
GOOD = "minutes = int(parts[0])"
SEM = re.compile(r"int\(\s*parts\[0\]")
BAD = re.compile(r"int\(\s*parts\[1\]")


def arm(a: str) -> dict:
    rs = [r for r in by[a] if "err" not in r]
    sc = [r["score"] for r in rs]
    d = {"n": len(by[a]), "err": len(by[a]) - len(rs),
         "hit6": sum(1 for s in sc if s == N),
         "dist": dict(sorted(collections.Counter(sc).items())),
         "copy": sum(1 for r in rs if r.get("same_as_start")),
         "uniq": len({r.get("sha") for r in rs if r.get("sha")})}
    if rs and "sim" in rs[0]:
        d["sim_med"] = round(statistics.median(r["sim"] for r in rs), 3)
    return d


print("=" * 74)
print("三臂（同一批 48 次生成，i 外层 A→N→E）")
print("=" * 74)
for a in ("A", "N", "E"):
    print(f"  {a}: {arm(a)}")

print()
print("P4 净效应：E_6of6 − N_6of6 =", arm("E")["hit6"] - arm("N")["hit6"],
      "（N−A =", arm("N")["hit6"] - arm("A")["hit6"], "⇒ 带行号本身的效应）")

# ---------------- E 臂细节 ----------------
E = [r for r in by["E"] if "err" not in r]
lines = collections.Counter(r["line"] for r in E)
errs = collections.Counter(r["edit_err"] for r in E)
print()
print("=" * 74)
print("E 臂：定位与表达式")
print("=" * 74)
print("  P1 LINE 分布 :", dict(sorted(lines.items())),
      f"⇒ LINE==16 占 {lines.get(16,0)}/{len(E)}")
print("     解析/替换失败:", dict(errs) or "无")
ct = collections.Counter()
for r in E:
    e = r["expr"] or ""
    a, b = bool(SEM.search(e)), bool(BAD.search(e))
    # 用 parts[0] / 用 parts[1] 必须分开；不许用 `"* 60" not in e` 判「过修」——
    # `* 3600` 不含子串 `* 60`，会把改错单位的条目误分进「最小修复」（本轮实际踩到）。
    ct[("两者都用" if (a and b) else "只用 parts[0]" if a
        else "只用 parts[1]" if b else "都不用")] += 1
print("  P2 EXPR 形态 :", dict(ct))
print()
print("  LINE × 分数 交叉表：")
for ln in sorted(lines):
    sub = [r for r in E if r["line"] == ln]
    print(f"    LINE={ln:2d}  n={len(sub):2d}  6/6={sum(1 for r in sub if r['score']==N):2d}"
          f"  分布={dict(sorted(collections.Counter(r['score'] for r in sub).items()))}")
print()
print("  逐条：")
for r in sorted(E, key=lambda x: x["i"]):
    ind = r["cand"].splitlines()[r["line"] - 1].strip() if r.get("cand") else "?"
    print(f"    #{r['i']:02d} {r['score']}/6 LINE={r['line']:>2} "
          f"{'★' if r['score']==N else ' '} {str(r['expr'])[:58]!r}")
    if r["score"] < N:
        print(f"         → 落地：{ind!r}")

# ---------------- 复用的盲抽对照 ----------------
print()
print("=" * 74)
print("等预算盲抽对照（复用 v247 的 A 臂样本，不是新证据）")
print("=" * 74)
try:
    oa = []
    for fn in ("l12_s316/readings_v247.jsonl", "l12_s316/readings_v247_nocode.jsonl"):
        try:
            old = [json.loads(x) for x in
                   (D.parent / fn).read_text(encoding="utf-8").splitlines() if x.strip()]
        except FileNotFoundError:
            continue
        oa += [r for r in old if r["arm"] == "A" and "score" in r]
    print(f"  v247 A 臂两轮共 {len(oa)} 次独立采样：6/6 = "
          f"{sum(1 for r in oa if r['score']==N)}/{len(oa)}，最高分 "
          f"{max(r['score'] for r in oa)}，分布 {dict(sorted(collections.Counter(r['score'] for r in oa).items()))}")
    print("  ⇒ 该分布上盲抽 k 次在本样本内不可能得到 6/6；所以本轮不重跑盲抽臂。")
    print("  ⚠️ 这是 reuse，不是本轮新证据；分母就是这 %d 条。" % len(oa))
except FileNotFoundError:
    print("  （v247 读数缺失，跳过）")

print()
print("A 与 N 是否逐字节同提示只差代码段：preflight P3 已断言（见 preflight_v248.json）")
