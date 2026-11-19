#!/usr/bin/env python3
"""V213（§281）读数：单臂时间序列 —— 潜伏状态到底存不存在。

⛔ 判据全文见 PREREG_v213.md；本脚本只做**预注册写死的那三件事** + 一条非判据的免费读数。
⛔ 不许在这里加「跑完才想到」的判据。
"""
from __future__ import annotations

import json
import math
import random
import statistics as st
import sys
from math import comb, lgamma
from pathlib import Path

D = Path(__file__).resolve().parent
BASE = int((D / "start_runs.txt").read_text().strip())
N = 30


# ---------- 统计工具（只依赖 stdlib，口径写在函数名里） ----------

def _betacf(a: float, b: float, x: float) -> float:
    """Lentz 连分式（Numerical Recipes 6.4），用于不完全 Beta。"""
    MAXIT, EPS, FPMIN = 300, 3e-16, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < EPS:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """正则化不完全 Beta I_x(a,b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = lgamma(a + b) - lgamma(a) - lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_p_two_sided(r: float, n: int) -> float:
    """Pearson r 的双侧 t 检验 p 值（H0: rho=0）。"""
    df = n - 2
    if abs(r) >= 1.0:
        return 0.0
    t2 = r * r * df / (1.0 - r * r)
    return _betai(df / 2.0, 0.5, df / (df + t2))


def pearson(a: list[float], b: list[float]) -> float:
    return st.correlation(a, b)


def spearman(a: list[float], b: list[float]) -> float:
    def rank(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        rk = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                rk[order[k]] = avg
            i = j + 1
        return rk

    return st.correlation(rank(a), rank(b))


def perm_p_pearson(a: list[float], b: list[float], trials: int = 100_000,
                   seed: int = 20261120) -> float:
    """Pearson r 的置换双侧 p（打乱 b，保持两组值的边缘分布）。"""
    obs = abs(pearson(a, b))
    rng = random.Random(seed)
    bb = list(b)
    hit = 0
    for _ in range(trials):
        rng.shuffle(bb)
        if abs(st.correlation(a, bb)) >= obs:
            hit += 1
    return (hit + 1) / (trials + 1)


# ---------- 取数 ----------

order = []
for ln in (D / "order.txt").read_text(encoding="utf-8").splitlines():
    if not ln.strip():
        continue
    i, rc, t0, t1 = ln.split("\t")
    order.append({"i": int(i), "rc": rc, "t0": t0, "t1": t1})

runs = [json.loads(l) for l in open("bench_out/runs.jsonl", encoding="utf-8") if l.strip()]
new = runs[BASE:]
print(f"基线 {BASE} 行 → 现在 {len(runs)} 行 → 新增 {len(new)} 行；order.txt {len(order)} 行")
if len(new) != N or len(order) != N:
    print(f"⛔ 行数不是 {N}：新增 {len(new)} / order {len(order)} —— 判据 P1.1 直接判红")
    sys.exit(3)

rows = []
for k, r in enumerate(new):
    per = {t["id"]: t for t in r["report"]["per_task"]}
    x1 = sum(1 for b in per["t01_parse_duration"]["vec"] if b)
    x4 = sum(1 for b in per["t04_merge_ranges"]["vec"] if b)
    rows.append({
        "k": k + 1,
        "run_id": r["run_id"],
        "at": r["verdict_at"],
        "repeat": r["repeat"],
        "solver": r["solver"],
        "x1": x1,
        "x4": x4,
        "x": x1 + x4,
        "v1": "".join("1" if b else "0" for b in per["t01_parse_duration"]["vec"]),
        "v4": "".join("1" if b else "0" for b in per["t04_merge_ranges"]["vec"]),
        "tau": r["report"]["elapsed_s"],
        "tau1": per["t01_parse_duration"]["elapsed_s"],
        "tau4": per["t04_merge_ranges"]["elapsed_s"],
        "caliber_sha": json.dumps(r["caliber"], sort_keys=True),
    })

print("\n=== 每次运行（按真实时间顺序） ===")
print(" #   x1 x4  x  v1      v4      τ(s)  τ1    τ4    verdict_at")
for r in rows:
    print(f"{r['k']:2d}   {r['x1']}  {r['x4']}  {r['x']:2d}  {r['v1']}  {r['v4']}  "
          f"{r['tau']:5.0f} {r['tau1']:5.0f} {r['tau4']:5.0f}  {r['at']}")

# ---------- P1 装置 ----------

p1 = []
p1.append(("新增 30 行、全 repeat=1、verdict_at 严格递增",
           len(new) == N
           and all(r["repeat"] == 1 for r in new)
           and all(rows[i]["at"] < rows[i + 1]["at"] for i in range(N - 1))))
p1.append(("30 次运行 rc 全 0（非零退出 %d 次）" % sum(1 for o in order if o["rc"] != "rc=0"),
           all(o["rc"] == "rc=0" for o in order)))
blk = 0
state_lens: list[int] = []
sp = D / "state_off.jsonl"
if sp.exists():
    for ln in sp.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        d = json.loads(ln)
        s = d.get("state", "")
        blk += sum(1 for line in s.splitlines() if line.startswith("[外部评审]"))
        state_lens.append(len(s))
p1.append((f"state 里 [外部评审] 块 = {blk}（应为 0）", blk == 0))
p1.append(("30 行 caliber 逐字节相同", len({r["caliber_sha"] for r in rows}) == 1))

print("\n=== P1 装置 ===")
for name, ok in p1:
    print(("  ✅ " if ok else "  ❌ ") + name)
print(f"  附：state 长度 {len(state_lens)} 条，取值范围 "
      f"{min(state_lens) if state_lens else '-'}~{max(state_lens) if state_lens else '-'}"
      f"，去重 {len(set(state_lens))} 种（自踩预案 3：写回若改了 state 长度，这里会露）")

# ---------- P2 跨题共因 ----------

x1 = [r["x1"] for r in rows]
x4 = [r["x4"] for r in rows]
r_p = pearson(x1, x4)
p_t = t_p_two_sided(r_p, N)
r_s = spearman(x1, x4)
p_perm = perm_p_pearson(x1, x4)

print("\n=== P2 跨题共因：同一次运行内 x1 ~ x4 ===")
print(f"  Pearson r = {r_p:+.4f}  (t 双侧 p = {p_t:.4f})")
print(f"  Spearman  = {r_s:+.4f}")
print(f"  置换双侧 p = {p_perm:.4f}  (100k 次，seed 20261120)")
ok2 = (r_p >= 0.45 and p_t < 0.05)
print(f"  押「r ≥ 0.45 且 p<0.05」 ⇒ {'✅ 成立' if ok2 else '❌ 不成立'}")
print(f"  可检测下限：n=30 双侧 p=0.05 对应 r={0.361:.3f} ⇒ r=0.30 本轮测不出（预注册已认）")

# ---------- P3 时间聚簇 ----------

xs = [r["x"] for r in rows]


def adj_mean(v: list[int]) -> float:
    return sum(abs(v[i] - v[i + 1]) for i in range(len(v) - 1)) / (len(v) - 1)


S = adj_mean(xs)
rng = random.Random(20261120)
TRIALS = 100_000
ge = 0
for _ in range(TRIALS):
    v = xs[:]
    rng.shuffle(v)
    if adj_mean(v) <= S + 1e-12:
        ge += 1
p3 = (ge + 1) / (TRIALS + 1)

h1, h2 = xs[:15], xs[15:]
d_half = st.mean(h2) - st.mean(h1)

print("\n=== P3 时间聚簇：S = mean|x_i − x_{i+1}| ===")
print(f"  S_obs = {S:.4f}  （x 的总体 sd = {st.pstdev(xs):.4f}）")
print(f"  置换下尾 p = {p3:.4f}  (100k 次，seed 20261120)  ⇒ "
      f"{'✅ 相邻比随机更像' if p3 < 0.05 else '❌ 未过 0.05'}")
print(f"  自踩预案 4：前半均值 {st.mean(h1):.3f} / 后半 {st.mean(h2):.3f} / 差 {d_half:+.3f}")
if abs(d_half) > 0.30:
    print("  ⛔ 前后半差 > 0.30 ⇒ 按预注册，P3 降级为「趋势，不是聚簇」，不许当聚簇报")

# ---------- P4 非判据：免费读数 ----------

tau = [r["tau"] for r in rows]
tau1 = [r["tau1"] for r in rows]
tau4 = [r["tau4"] for r in rows]
print("\n=== P4（非判据行，只报数，不设押） ===")
print(f"  r(τ 总耗时, x)   = {pearson(tau, xs):+.4f}  (p={t_p_two_sided(pearson(tau, xs), N):.4f})")
print(f"  r(τ1, τ4)        = {pearson(tau1, tau4):+.4f}  (p={t_p_two_sided(pearson(tau1, tau4), N):.4f})")
print(f"  r(τ1, x1)        = {pearson(tau1, x1):+.4f}")
print(f"  r(τ4, x4)        = {pearson(tau4, x4):+.4f}")

# ---------- 汇总 ----------
share = {v: xs.count(v) for v in sorted(set(xs))}
print("\n=== x 的分布 ===")
print("  " + "  ".join(f"{k}:{v}" for k, v in share.items()))
print(f"  mean={st.mean(xs):.3f} sd={st.pstdev(xs):.3f}  零方差？{st.pstdev(xs) == 0}")

json.dump({"rows": rows, "P2": {"r": r_p, "p_t": p_t, "p_perm": p_perm, "spearman": r_s},
           "P3": {"S": S, "p": p3, "half_delta": d_half},
           "P1": [[n, ok] for n, ok in p1]},
          open(D / "readings_v213.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print(f"\n写盘 {D/'readings_v213.json'}")
