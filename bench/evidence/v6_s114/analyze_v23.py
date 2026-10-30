#!/usr/bin/env python3
"""V23 读数：三臂 × 3 轮，按**预注册**口径出判决（不在这里发明判据）。

预注册：`bench/evidence/v6_s114/PREREG.md`
判据四种结局（J1~J4）逐字照抄预注册 §4，**本脚本不许临时改判**：

    Δ_total    = mean(A1) − mean(A0)      # 整块的效应（V22 量到 −16）
    Δ_content  = mean(A1) − mean(A2)      # 其中属于「内容」的那一半
    Δ_presence = mean(A2) − mean(A0)      # 其中属于「有这么一段等长字」的那一半

用法：
    python3 bench/evidence/v6_s114/analyze_v23.py /tmp/v23 [轮数]
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

CELLS = 96
TASKS = ("e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni")
ARMS = (("a0", "A0 不回显", 0), ("a1", "A1 真回显", 1), ("a2", "A2 等长无关", 2))


def rows(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()
            if x.strip()]


def round_score(w: Path, arm: str, r: int) -> tuple[float, dict, dict]:
    """一轮的格分 + 逐题分 + 逐题交付件 sha。分母固定 6 题，缺题记 0。"""
    rs = rows(w / f"bench_{arm}_r{r}" / "runs.jsonl")
    if not rs:
        return float("nan"), {}, {}
    pt = rs[-1]["report"]["per_task"]
    per: dict[str, list[float]] = {}
    for t in pt:
        per.setdefault(str(t["id"]), []).append(float(t.get("score") or 0.0))
    task_mean = {k: sum(v) / len(v) for k, v in per.items()}
    sc = sum(task_mean.get(t, 0.0) for t in TASKS) / len(TASKS) * CELLS
    sha: dict[str, str] = {}
    for x in rows(w / f"bench_{arm}_r{r}" / "answers.jsonl"):
        tid = str(x.get("task_id"))
        sha[tid] = hashlib.sha256(str(x.get("answer") or "").encode()).hexdigest()[:12]
    return sc, task_mean, sha


def answer_of(w: Path, arm: str, r: int, tid: str) -> str:
    for x in rows(w / f"bench_{arm}_r{r}" / "answers.jsonl"):
        if str(x.get("task_id")) == tid:
            return str(x.get("answer") or "")
    return ""


def main(argv: list[str]) -> int:
    w = Path(argv[1] if len(argv) > 1 else "/tmp/v23")
    rounds = int(argv[2]) if len(argv) > 2 else 3
    print(f"工作目录 {w}  轮数/臂 {rounds}\n")

    # ── 一、逐轮逐题格分矩阵 ────────────────────────────────────────────────
    scores: dict[str, list[float]] = {}
    print("一、逐轮逐题格分（每格 = score×16）")
    head = "        " + "".join(f"{t:>8}" for t in TASKS) + "   轮分"
    for arm, label, _e in ARMS:
        print(f"\n  {label}")
        print(head)
        vals = []
        for r in range(1, rounds + 1):
            sc, tm, _sha = round_score(w, arm, r)
            vals.append(sc)
            cells = "".join(f"{tm.get(t, 0.0) * 16:8.0f}" for t in TASKS)
            print(f"  r{r}    {cells}{sc:8.1f}")
        scores[arm] = vals

    # ── 二、P0：装置闸 ─────────────────────────────────────────────────────
    print("\n二、装置闸")
    p0c = w / "p0c.txt"
    ok_n = mis_n = 0
    if p0c.exists():
        for line in p0c.read_text(encoding="utf-8").splitlines():
            if line.strip().endswith("OK"):
                ok_n += 1
            elif "MISMATCH" in line:
                mis_n += 1
    print(f"  P0-c 每轮重置 sha 核对：{ok_n} 通过 / {mis_n} 失败 "
          f"（应等于 {rounds * 3}）")

    exits = []
    for arm, _l, _e in ARMS:
        for r in range(1, rounds + 1):
            tf = w / f"time_{arm}_r{r}.txt"
            if tf.exists():
                txt = tf.read_text(encoding="utf-8")
                exits.append((f"{arm}_r{r}", "EXIT=0" in txt, txt.strip()))
    bad = [(t, s) for t, ok, s in exits if not ok]
    print(f"  P0-d 轮次 {len(exits)}/{rounds * 3} 记录的 EXIT≠0 的轮："
          f"{[t for t, _ in bad] or '无'}")
    for t, s in bad:
        print(f"        {t}: {s}")

    # ── 三、噪声下限 ───────────────────────────────────────────────────────
    print("\n三、噪声下限（每臂 3 轮极差，取最大）")
    nf = 0.0
    for arm, label, _e in ARMS:
        v = [x for x in scores[arm] if x == x]
        spread = (max(v) - min(v)) if v else float("nan")
        nf = max(nf, spread if spread == spread else 0.0)
        print(f"  {label}: 极差={spread:.1f}  值={[round(x, 1) for x in v]}")
    print(f"  noise_floor = {nf:.1f}")

    # ── 四、判决（J1~J4，逐字照 PREREG §4）────────────────────────────────
    def mean(arm: str) -> float:
        v = [x for x in scores[arm] if x == x]
        return sum(v) / len(v) if v else float("nan")

    m0, m1, m2 = mean("a0"), mean("a1"), mean("a2")
    d_total, d_content, d_presence = m1 - m0, m1 - m2, m2 - m0
    print("\n四、判决")
    print(f"  mean(A0)={m0:.1f}  mean(A1)={m1:.1f}  mean(A2)={m2:.1f}")
    print(f"  Δ_total={d_total:+.1f}  Δ_content={d_content:+.1f}  "
          f"Δ_presence={d_presence:+.1f}   (恒等式差 = "
          f"{d_total - (d_content + d_presence):+.1f})")

    verdict, why = "", ""
    if d_total >= 0:
        verdict = "UNKNOWN（PREREG §4 附条件 4）"
        why = ("Δ_total ≥ 0 ⇒ 连 V22 的 −16 方向都没复现 ⇒ 本轮只说『装置变了』，"
               "不许拿 Δ_content/Δ_presence 下任何结论")
    elif m2 == m0:
        verdict = "J1  H_content 成立：载荷在**内容**"
        why = "A2 == A0 ⇒ 等长无关文本与『没有这块』同效 ⇒ 起作用的是那段字的意思"
    elif m2 == m1:
        verdict = "J2  H_perturb 成立：载荷在**有这么一块字**"
        why = ("A2 == A1 ⇒ 任何等长文本都能复现该效应 ⇒ §86.4『段话让模型想重写』"
               "作废，应改写成『prompt 一变就跳到另一个采样点』")
    elif m0 < m2 < m1:
        verdict = "J3  混合"
        tot = d_content + d_presence
        why = (f"内容占 {d_content / tot:.0%}、扰动占 {d_presence / tot:.0%}"
               if tot else "分母为 0")
    elif m2 < m0:
        verdict = "J4  安慰剂比没有更差"
        why = ("等长填充本身有害 ⇒ 本轮**不能**回答 PREREG §0 的问题；"
               "需要一个第三种填充（下次）")
    else:
        verdict = "UNKNOWN（读数形态不在 J1~J4 里）"
        why = "M2 落在 A1 之上 ⇒ 装置异常，先查 P0"
    gate = (nf == 0.0) or (abs(d_total) > nf)
    print(f"\n  ⇒ {verdict}")
    print(f"     {why}")
    print(f"     附加条件①噪声：noise_floor={nf:.1f} ⇒ "
          f"{'✅ 过' if gate else '❌ 不过（UNKNOWN）'}")

    # ── 五、机制取证：e1ni 的交付件到底还不还 import re ────────────────────
    print("\n五、机制取证（`e1ni`：唯一对这块敏感的题）")
    for arm, label, _e in ARMS:
        keeps = []
        for r in range(1, rounds + 1):
            a = answer_of(w, arm, r, "e1ni")
            keeps.append("有" if "import re" in a else "**无**")
        shas = [round_score(w, arm, r)[2].get("e1ni", "-")[:8]
                for r in range(1, rounds + 1)]
        print(f"  {label}: import re = {' / '.join(keeps)}   sha="
              f"{' '.join(shas)}")
    print("\n  ⚠️ 逐字节相同 ⇒ 同条件是**确定映射**（V22 已证），"
          "不是『平均效应』。")

    # ── 六、边界复述（照抄进 RESULTS，不许改弱）────────────────────────────
    print("\n六、边界（预注册 §6，逐条照抄）")
    for s in (
        "① 只能说：在 tasks_v8 / qwen2.5:7b / 本时段。6 题里只有 e1ni 对这块敏感。",
        "② 不许说『系统自己发现了这个规律』：0/1/2 三档与填充文本都是人定的。",
        "③ 本轮不动投喂策略（不删那段、不改措辞）—— 先读数再决定。",
        "④ A2 的『等长』是**字符数**口径，不是 token 数（字节长度有小幅差异）。",
        "⑤ 内核本轮有 diff（harness_sha 必变）⇒ 与 V22 并列时必须声明。",
    ):
        print("  " + s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
