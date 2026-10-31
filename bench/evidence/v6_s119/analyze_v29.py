#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V29 判决：把「外部评审」这条跨轮通道接通之后，分数动了吗？

只读落盘证据，不起模型。
判据写死在 `bench/evidence/v6_s119/PREREG.md`（2026-10-31 16:10 冻结）。
本脚本**只执行不解释** —— 字面判决按预注册取第一条命中，逐格归因另开一节。

用法：analyze_v29.py [工作目录，默认 /tmp/v29] [--v28 /tmp/v28]
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent.parent.parent
BLOCKS = ("[目标]", "[工作记忆]", "[语义记忆]", "[外部评审]", "[轨迹]", "[第")
CRIT = re.compile(r"\[外部评审\] (.*?)(?:\n\[|$)", re.S)

ARMS = ("o2", "p2", "q2")
TAGS = [f"{a}_r{r}" for r in (1, 2) for a in ARMS]
TASKS = ("e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni")
CAP = 16   # 每题满分（16 格 × 1 分）


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def load_states(w: Path, tag: str) -> dict[tuple[str, int], str]:
    p = w / f"states_{tag}.jsonl"
    out: dict[tuple[str, int], str] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        out[(str(d["task"]), int(d["it"]))] = str(d["state"])
    return out


def load_scores(w: Path, tag: str) -> dict[str, float]:
    p = w / f"bench_{tag}" / "runs.jsonl"
    if not p.exists():
        return {}
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not rows:
        return {}
    per = rows[-1]["report"]["per_task"]
    return {str(t["id"]): float(t.get("score") or 0) * CAP for t in per}


def load_sha(w: Path, tag: str) -> dict[str, str]:
    p = w / f"bench_{tag}" / "answers.jsonl"
    out: dict[str, str] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        x = json.loads(line)
        out[str(x.get("task_id"))] = sha(str(x.get("answer") or ""))
    return out


def total(sc: dict[str, float]) -> int:
    return int(sum(sc.get(t, 0) for t in TASKS))


def diff(a: dict[tuple[str, int], str], b: dict[tuple[str, int], str]) -> tuple[int, int]:
    """返回 (不同快照数, 只在一侧出现的键数)。键集合不同也算差异。"""
    keys = set(a) | set(b)
    n = sum(1 for k in keys if a.get(k) != b.get(k))
    only = len(set(a) ^ set(b))
    return n, only


def crit_blocks(states: dict[tuple[str, int], str]) -> list[tuple[str, str]]:
    out = []
    for (t, _it), s in states.items():
        for m in CRIT.finditer(s):
            out.append((t, m.group(1)))
    return out


def main(argv: list[str]) -> int:
    w = Path(argv[1]) if len(argv) > 1 and not argv[1].startswith("-") else Path("/tmp/v29")
    v28 = Path("/tmp/v28")
    if "--v28" in argv:
        v28 = Path(argv[argv.index("--v28") + 1])

    sc = {t: load_scores(w, t) for t in TAGS}
    st = {t: load_states(w, t) for t in TAGS}
    sh = {t: load_sha(w, t) for t in TAGS}

    L: list[str] = []
    p = L.append

    p("=" * 78)
    p("V29  外部评审：跨轮给、轮内不给")
    p(f"工作目录 {w}")
    p("=" * 78)

    # ── 0 读数总表 ────────────────────────────────────────────────────
    p("\n## 0 读数（逐臂逐轮）\n")
    p("| 臂 | 轮 | scope | Σ | " + " | ".join(TASKS) + " | 交付件指纹 |")
    p("|---|---|---|---|" + "---|" * len(TASKS) + "---|")
    for r in (1, 2):
        for a in ARMS:
            t = f"{a}_r{r}"
            s = sc[t]
            fp = ",".join(sh[t].get(k, "?")[:4] for k in TASKS)
            p(f"| `{a}` | r{r} | {'off' if a == 'o2' else 'prev'} | **{total(s)}** | "
              + " | ".join(str(int(s.get(k, 0))) for k in TASKS) + f" | `{fp}` |")

    # ── 1 前提闸 ──────────────────────────────────────────────────────
    p("\n## 1 前提闸\n")
    gates: list[tuple[str, bool, str]] = []

    # P0-a 门禁（由 run_tests.sh 单独跑，这里只记录要不要人工补）
    gates.append(("P0-a 门禁 596/0 · 97/0 · EXIT=0", True, "见 RESULTS 手工登记（本脚本不跑门禁）"))

    # P0-c o2 复现 V28 的 d2
    okc, detc = True, []
    for r in (1, 2):
        st_o = st[f"o2_r{r}"]
        st_d = load_states(v28, f"d2_r{r}")
        if not st_o or not st_d:
            okc = False
            detc.append(f"r{r}: 缺 dump（o2={len(st_o)} d2={len(st_d)}）")
            continue
        n, only = diff(st_o, st_d)
        same_score = total(sc[f"o2_r{r}"]) == total(load_scores(v28, f"d2_r{r}"))
        shp = sh[f"o2_r{r}"] == load_sha(v28, f"d2_r{r}")
        okc = okc and n == 0 and only == 0 and same_score and shp
        detc.append(f"r{r}: state 差异={n} 键差={only} Σ同={same_score} 指纹同={shp}")
    gates.append(("P0-c `o2` 逐字节复现 V28 `d2`", okc, " ; ".join(detc)))

    # P0-d p2 r1 无评审块
    cb1 = crit_blocks(st["p2_r1"])
    gates.append(("P0-d `p2` r1 无 [外部评审]", len(cb1) == 0,
                  f"r1 出现 {len(cb1)} 次"))

    # P0-e p2 r2 每题都有评审块
    cb2 = crit_blocks(st["p2_r2"])
    per_task = {t: sum(1 for (tt, _) in cb2 if tt == t) for t in TASKS}
    all_tasks = all(v > 0 for v in per_task.values())
    gates.append(("P0-e `p2` r2 每题都有 [外部评审]", all_tasks and len(cb2) > 0,
                  f"共 {len(cb2)} 次，逐题 {per_task}"))

    # P0-f 自评闸：每个块内不含自己的 task_id
    leak = [(t, b[:60]) for (t, b) in cb2 if f"/{t}]" in b or f"[tasks_v8/{t}]" in b]
    gates.append(("P0-f 自评闸（块内不含当前题）", len(leak) == 0,
                  f"泄漏 {len(leak)} 处" + (f"：{leak[:2]}" if leak else "")))

    # P0-g 等长
    cq = crit_blocks(st["q2_r2"])
    lp = sum(len(b) for _, b in cb2)
    lq = sum(len(b) for _, b in cq)
    rel = abs(lp - lq) / max(1, lp)
    gates.append(("P0-g 安慰剂等长（相对差 ≤ 10%）", rel <= 0.10,
                  f"p2={lp} 字 q2={lq} 字 相对差={rel:.4f}"))

    # P0-h 判据名（由 run 脚本的 p0e.txt 提供）
    p0e = w / "p0e.txt"
    mis = 0
    if p0e.exists():
        mis = sum(1 for x in p0e.read_text(encoding="utf-8").splitlines() if "MISMATCH" in x)
    gates.append(("P0-h 判据名 6/6 = 5项(…结构检查)", p0e.exists() and mis == 0,
                  f"p0e.txt {'缺' if not p0e.exists() else f'MISMATCH={mis}'}"))

    p("| 闸 | 结果 | 读数 |")
    p("|---|---|---|")
    for name, ok, d in gates:
        p(f"| {name} | {'✅' if ok else '❌'} | {d} |")

    p0_ok = all(ok for _, ok, _ in gates)

    # ── 2 噪声下限 ────────────────────────────────────────────────────
    n_o, only_o = diff(st["o2_r1"], st["o2_r2"])
    if n_o == 0 and only_o == 0:
        noise = 0
        noise_src = "o2 两轮 state 逐字节相同 ⇒ noise_floor=0"
    else:
        noise = abs(total(sc["o2_r2"]) - total(sc["o2_r1"]))
        noise_src = f"o2 两轮 state 差异 {n_o} ⇒ noise_floor=|ΔΣ_o2|={noise}"
    p(f"\n**噪声下限** `noise_floor = {noise}` —— {noise_src}")

    # ── 3 判决 ────────────────────────────────────────────────────────
    so1, so2 = total(sc["o2_r1"]), total(sc["o2_r2"])
    sp1, sp2 = total(sc["p2_r1"]), total(sc["p2_r2"])
    sq2 = total(sc["q2_r2"])
    d_p = sp2 - so2          # p2 vs 基线
    d_q = sq2 - so2          # q2 vs 基线
    d_pq = sp2 - sq2         # p2 vs 安慰剂

    verdict = "J0"
    why = ""
    if not p0_ok:
        verdict = "J0"
        why = "前提闸未全过 ⇒ 装置作废，不出判据"
    elif d_p > noise and abs(d_pq) > noise:
        verdict = "J1"
        why = f"Δ(p2−o2)={d_p:+d} > {noise}，且 Δ(p2−q2)={d_pq:+d} 超噪声 ⇒ 语义贡献成立"
    elif d_p > noise and abs(d_pq) <= noise:
        verdict = "J4"
        why = (f"Δ(p2−o2)={d_p:+d} 超噪声，但 Δ(p2−q2)={d_pq:+d} 不超 ⇒ "
               f"效应来自「多了一段字」，不是语义")
    elif abs(d_q) > noise:
        verdict = "J4"
        why = f"安慰剂自己就动了：Δ(q2−o2)={d_q:+d} > {noise} ⇒ 长度/存在性效应"
    elif d_p < -noise:
        verdict = "J3"
        why = f"Δ(p2−o2)={d_p:+d} < −{noise} ⇒ 通电且有害"
    else:
        verdict = "J2"
        why = (f"Δ(p2−o2)={d_p:+d} 不超噪声 {noise}，且 P0-e 成立 ⇒ "
               f"通道已通电、内容对本尺子零价值")

    p("\n## 2 判决\n")
    p(f"- Σ `o2` r1={so1} / r2={so2}（基线，复现 V28 的 48）")
    p(f"- Σ `p2` r1={sp1} / r2={sp2}（处理组）")
    p(f"- Σ `q2` r2={sq2}（安慰剂）")
    p(f"- Δ(p2−o2) r2 = **{d_p:+d}** · Δ(q2−o2) r2 = **{d_q:+d}** · Δ(p2−q2) = **{d_pq:+d}**")
    p(f"\n### >>> 判决：**{verdict}** —— {why}")

    # ── 4 逐格归因（不许改判据，只并列） ──────────────────────────────
    p("\n## 3 逐格归因（p2 r2 vs o2 r2）\n")
    p("| 题 | o2 | p2 | Δ | o2 指纹 | p2 指纹 | 变了 |")
    p("|---|---|---|---|---|---|---|")
    for k in TASKS:
        a, b = sc["o2_r2"].get(k, 0), sc["p2_r2"].get(k, 0)
        fa, fb = sh["o2_r2"].get(k, "?"), sh["p2_r2"].get(k, "?")
        p(f"| `{k}` | {int(a)} | {int(b)} | {int(b - a):+d} | `{fa}` | `{fb}` | {'是' if fa != fb else '否'} |")

    p("\n## 4 押注对账\n")
    lift = [k for k in ("e2ni", "e3ni") if sc["p2_r2"].get(k, 0) > sc["o2_r2"].get(k, 0)]
    p(f"- 预注册押注：`e2ni`/`e3ni` 被拉起 ≥1 格。实测被拉起：**{lift or '无'}**")
    p(f"- 押注结果：{'✅ 押中' if lift else '❌ 押错（按 PREREG §6 判 J2）'}")

    # ── 5 附加读数（不改判据）─────────────────────────────────────────
    p("\n## 5 附加读数（只登记，不参与判决）\n")
    p("### 5.1 r1 三臂一致性 —— 独立于 `noise_floor` 的时段漂移读数\n")
    p("r1 时三个臂的账本**都为空** ⇒ `scope=prev` 与 `scope=off` 的快照都是空的 ⇒ "
      "理论上三臂的 prompt 应逐字节相同。不同就是时段漂移（纪律 2），"
      "这一条与 §2 的 `noise_floor` 是两条独立的证据。")
    p("\n| 对比 | state 差异 | 键差 | Σ 差 | 指纹差 |")
    p("|---|---|---|---|---|")
    for a, b in (("o2_r1", "p2_r1"), ("o2_r1", "q2_r1"), ("p2_r1", "q2_r1")):
        n, only = diff(st[a], st[b])
        ds = total(sc[b]) - total(sc[a])
        fdiff = sorted(k for k in TASKS if sh[a].get(k) != sh[b].get(k))
        p(f"| `{a}` vs `{b}` | {n} | {only} | {ds:+d} | {fdiff or '无'} |")

    p("\n### 5.2 `[外部评审]` 块里出现的是哪些题\n")
    seen: dict[str, int] = {}
    for (_t, b) in cb2:
        for k in TASKS:
            if f"/{k}]" in b:
                seen[k] = seen.get(k, 0) + 1
    p(f"- `p2` r2 的块里被引用的来源题：`{seen}`")
    p(f"- 应有的来源题（每块 5 条、不含自己）：{TASKS}")

    p("\n### 5.3 逐题墙钟（是否触到预算）\n")
    p("| 轮 | " + " | ".join(TASKS) + " |")
    p("|---|" + "---|" * len(TASKS))
    for t in TAGS:
        pth = w / f"bench_{t}" / "runs.jsonl"
        if not pth.exists():
            continue
        rows = [json.loads(x) for x in pth.read_text(encoding="utf-8").splitlines() if x.strip()]
        if not rows:
            continue
        el = {str(x["id"]): x.get("elapsed_s") for x in rows[-1]["report"]["per_task"]}
        p(f"| `{t}` | " + " | ".join(str(el.get(k, "?")) for k in TASKS) + " |")

    txt = "\n".join(L)
    print(txt)
    (w / "verdict.txt").write_text(txt + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
