#!/usr/bin/env python3
"""V121：跨跑累积 —— 写记忆 → 读回 → 用上；以及「记忆错了不许变差」。

三个臂，同一搜索器、同一动作空间（V120 的 C1）、同一 k=2、同一预算：
  COLD  ：候选按自然顺序
  WARM  ：候选按「记忆命中优先」排序（记忆 = 同题兄弟在 Pass 1 里真正生效的动作）
  WRONG ：候选按「别题的记忆」排序（故意给错）

⭐ 唯一让「顺序」能省下判分次数的机制：**任一轮里一旦有候选到 1.0 就立即停**。
   三个臂用同一条规则，所以差异只来自顺序。

零模型、零网络、零子进程。判据 = 现有 test 同一批。
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
sys.path.insert(0, str(EV / "v6_s205"))
sys.path.insert(0, str(EV / "v6_s208"))
sys.path.insert(0, str(EV / "v6_s209"))
sys.path.insert(0, str(EV / "v6_s198"))
import search as S            # noqa: E402
import idiom_transfer as I     # noqa: E402
import v119 as V              # noqa: E402
import v120 as W              # noqa: E402

TIDS = V.TIDS
MAX_ROUNDS = 6
MEM_FILE = HERE / "memory.json"
ev = V.ev


def abstract(label: str) -> tuple:
    """把动作标签压成跨实现可比的形式：变量名/站点号全部去掉。"""
    m = re.match(r"^B:(key|ret|cmp)\(?([^)]*)\)?:(.*)$", label)
    if m:
        kind, _, chain = m.groups()
        return (kind, chain)
    m = re.match(r"^B:(ret|cmp)\d+:(.*)$", label)
    if m:
        return (m.group(1), m.group(2))
    m = re.match(r"^T1:ret\d+:=(.*)$", label)
    if m:
        return ("T1", m.group(1))
    m = re.match(r"^T2:cont[\d.]+:\+(.*)$", label)
    if m:
        return ("T2", m.group(1))
    m = re.match(r"^T3:ret\d+:(.*)$", label)
    if m:
        return ("T3", m.group(1))
    m = re.match(r"^T4:cmp\d+:(.*)$", label)
    if m:
        return ("T4", m.group(1))
    m = re.match(r"^C1:ret\d+:=(.*)$", label)
    if m:
        return ("C1", m.group(1))
    if label.startswith("C2:del"):
        return ("C2", "-")
    return ("?", label)


def gen(tid, code, Sx, chains, exprs) -> list[tuple[str, str]]:
    out = list(S.candidates(code, Sx))
    out += V.candidates_B(code, chains)
    out += W.candidates_C1(code, exprs)
    return out


def search(tid, code, Sx, chains, exprs, order_key) -> dict:
    s0, _ = ev(tid, code)
    if s0 >= 1.0:
        return dict(s0=s0, s1=s0, n_eval=0, lab="<baseline>", hit=True)
    frontier = [dict(code=code, s=s0)]
    visited = {code}
    n_eval = 0
    for _ in range(MAX_ROUNDS):
        pool, seen_src = [], set()
        for node in frontier:
            cands = gen(tid, node["code"], Sx, chains, exprs)
            cands = sorted(cands, key=order_key)          # ⭐ 只有这一处随臂变化
            for lab, src in cands:
                if src in visited or src in seen_src:
                    continue
                s, _ = ev(tid, src)
                n_eval += 1
                if s >= 1.0:                              # ⭐ 三臂共用：一到 1.0 就停
                    return dict(s0=s0, s1=s, n_eval=n_eval, lab=lab, hit=True)
                if s > node["s"]:
                    pool.append(dict(code=src, s=s))
                    seen_src.add(src)
        if not pool:
            break
        pool.sort(key=lambda d: -d["s"])
        kept = pool[:2]
        visited |= {d["code"] for d in pool}
        frontier = kept
    best = max(frontier, key=lambda d: d["s"])
    return dict(s0=s0, s1=best["s"], n_eval=n_eval, lab="<none>", hit=False)


def main() -> None:
    corpus = I.harvest()
    impls, skipped = [], []
    for tid in TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                skipped.append((tid, h))
                continue
            impls.append((tid, h))

    # ---------------- Pass 1：冷启动，并把「生效动作」写进记忆
    print("## §0 Pass 1（冷启动）：把真正生效的动作写下来")
    cold, wins = {}, {t: [] for t in TIDS}
    for tid, h in impls:
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        chains = V.harvest_chains(corpus, tid, h)
        exprs = W.harvest_exprs(corpus, tid, h)
        r = search(tid, corpus[tid][h], Sx, chains, exprs, lambda x: 0)
        cold[(tid, h)] = r
        if r["hit"]:
            wins[tid].append(dict(h=h, act=list(abstract(r["lab"])), label=r["lab"]))
        print(f"  {tid[:6]} {h} s0={r['s0']:.1f} s1={r['s1']:.1f} n_eval={r['n_eval']:5d}"
              f"　生效={r['lab'] if r['hit'] else '（无）'}")
    MEM_FILE.write_text(json.dumps(wins, ensure_ascii=False, indent=1), encoding="utf-8")
    payload = MEM_FILE.read_bytes()
    import hashlib
    sha = hashlib.sha256(payload).hexdigest()[:12]
    n_rec = sum(len(v) for v in wins.values())
    print(f"\n  记忆已落盘 `{MEM_FILE.name}`：n_rec = {n_rec}｜bytes = {len(payload)}｜sha = {sha}")

    # ---------------- Pass 2：从磁盘读回（留一法）
    mem = json.loads(MEM_FILE.read_text(encoding="utf-8"))
    print("\n## §1 Pass 2（热启动，留一法：不含自己那条）")
    warm = {}
    for tid, h in impls:
        mine = {(a, b) for k in mem[tid] if k["h"] != h for a, b in [tuple(k["act"])]}
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        chains = V.harvest_chains(corpus, tid, h)
        exprs = W.harvest_exprs(corpus, tid, h)

        def key(x, mine=mine):
            return (0 if abstract(x[0]) in mine else 1)

        V.CACHE.clear()                       # ⛔ 必须清：否则「省下的次数」是缓存假象
        warm[(tid, h)] = search(tid, corpus[tid][h], Sx, chains, exprs, key)
        r, c = warm[(tid, h)], cold[(tid, h)]
        print(f"  {tid[:6]} {h} s1={r['s1']:.1f}（冷 {c['s1']:.1f}）"
              f"　n_eval {c['n_eval']:5d} → {r['n_eval']:5d}　|记忆|={len(mine)}"
              f"{'  ⬇' if r['n_eval'] < c['n_eval'] else ''}")

    # ---------------- Pass 3：故意给错记忆（别题的）
    print("\n## §2 Pass 3（错记忆：用别题的记忆排序）")
    wrong = {}
    for tid, h in impls:
        other = [t for t in TIDS if t != tid]
        bad = set()
        for t2 in other:
            for k in mem[t2]:
                bad.add(tuple(k["act"]))
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        chains = V.harvest_chains(corpus, tid, h)
        exprs = W.harvest_exprs(corpus, tid, h)

        def key(x, bad=bad):
            return (0 if abstract(x[0]) in bad else 1)

        V.CACHE.clear()
        wrong[(tid, h)] = search(tid, corpus[tid][h], Sx, chains, exprs, key)

    # ---------------- §3 判决
    print("\n## §3 判决")
    # 「1 跳可修」= 基线处存在一个候选（1 编辑）就能到 1.0。
    # ⛔ 这是 W2/W3 的**作用域**：记忆只可能在「一轮之内能找到 1.0」时省下次数。
    onehop = set()
    for tid, h in impls:
        Sx, _ = S.wordset(tid, corpus[tid][h], True)
        chains = V.harvest_chains(corpus, tid, h)
        exprs = W.harvest_exprs(corpus, tid, h)
        s0, _ = ev(tid, corpus[tid][h])
        if s0 >= 1.0:
            continue
        if any(ev(tid, src)[0] >= 1.0 for _, src in gen(tid, corpus[tid][h], Sx, chains, exprs)):
            onehop.add((tid, h))
    print(f"  n_impl_onehop = {len(onehop)}｜n_impl_twohop = "
          f"{sum(1 for k in cold if cold[k]['hit']) - len(onehop)}")
    # ⛔ 第一版把「修好数（s0<1）」和「到 1.0 数」混在一句话里比 ⇒ 分母不同（纪律 46）。
    #    这里两块都按**同一个分母**（n_impl_parseable）分列。
    reps = {"冷": cold, "热": warm, "错记忆": wrong}
    print(f"  分母统一 n_impl_parseable = {len(impls)}")
    for nm, arm in reps.items():
        to1 = sum(1 for k in arm if arm[k]["s1"] >= 1.0)
        fx = sum(1 for k in arm if arm[k]["s0"] < 1.0 and arm[k]["s1"] >= 1.0)
        print(f"    {nm:6s} 到 1.0 = {to1}/{len(impls)}　其中「修好」= {fx}/{len(impls)}")

    dmg_w = [(k, cold[k]["s1"], warm[k]["s1"]) for k in cold if warm[k]["s1"] < cold[k]["s1"]]
    w1 = not dmg_w
    print(f"\n  **W1 热启动的 s1 一个都不降** ⇒ {'✅ 押中' if w1 else '⛔ 押破'}（{dmg_w}）")

    oh = sorted(onehop)
    better = [k for k in oh if warm[k]["n_eval"] < cold[k]["n_eval"]]
    w2 = bool(oh) and len(better) == len(oh) and all(warm[k]["n_eval"] <= 3 for k in oh)
    print(f"  **W2 热启动在 1 跳可修的实现上判分次数严格下降，且 ≤3**")
    for k in oh:
        print(f"      {k[0][:6]} {k[1]}: {cold[k]['n_eval']:3d} → {warm[k]['n_eval']:3d}")
    print(f"    严格下降 {len(better)}/{len(oh)}，全部 ≤3：{all(warm[k]['n_eval'] <= 3 for k in oh)}"
          f" ⇒ {'✅ 押中' if w2 else '⛔ 押破'}")

    s_cold = sum(cold[k]["n_eval"] for k in cold)
    s_warm = sum(warm[k]["n_eval"] for k in warm)
    s_wrong = sum(wrong[k]["n_eval"] for k in wrong)
    oh_cold = sum(cold[k]["n_eval"] for k in oh)
    oh_warm = sum(warm[k]["n_eval"] for k in oh)
    tw = [k for k in cold if cold[k]["hit"] and k not in onehop]
    tw_cold = sum(cold[k]["n_eval"] for k in tw)
    tw_warm = sum(warm[k]["n_eval"] for k in tw)
    w3 = oh_warm < oh_cold * 0.5
    print(f"\n  **W3a 在 1 跳可修集合上，判分次数降一半以上**："
          f"冷 {oh_cold} → 热 {oh_warm}（{1 - oh_warm / oh_cold:.1%}）"
          f" ⇒ {'✅ 押中' if w3 else '⛔ 押破'}")
    print(f"  **W3b 在 2 跳集合上，热启动基本无变化（记忆在一轮内帮不上）**："
          f"冷 {tw_cold} → 热 {tw_warm}（n_impl={len(tw)}）")
    print(f"  全体：冷 {s_cold} → 热 {s_warm}（省 {s_cold - s_warm}，{1 - s_warm / s_cold:.1%}）"
          f"｜错记忆 {s_wrong}")

    dmg_x = [(k, cold[k]["s1"], wrong[k]["s1"]) for k in cold if wrong[k]["s1"] < cold[k]["s1"]]
    w4 = not dmg_x and s_wrong >= s_cold
    print(f"\n  **W4 记忆错了也不许变差（s1 不降），但要有代价（n_eval 不降）**")
    print(f"    受损 {dmg_x}｜n_eval 冷 {s_cold} vs 错 {s_wrong}"
          f" ⇒ {'✅ 押中' if w4 else '⛔ 押破'}")

    w5 = MEM_FILE.exists() and len(payload) > 0 and n_rec > 0 and sha is not None
    print(f"\n  **W5 记忆是写出去再读回来的（磁盘文件 + sha）** ⇒ {'✅ 押中' if w5 else '⛔ 押破'}")
    print(f"    n_rec = {n_rec}｜bytes = {len(payload)}｜sha = {sha}")

    (HERE / "search.json").write_text(json.dumps(dict(
        cold={f"{k[0]}::{k[1]}": v for k, v in cold.items()},
        warm={f"{k[0]}::{k[1]}": v for k, v in warm.items()},
        wrong={f"{k[0]}::{k[1]}": v for k, v in wrong.items()},
        skipped=skipped, sha=sha, n_rec=n_rec), ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  原始读数已落盘 `search.json`")


if __name__ == "__main__":
    main()
