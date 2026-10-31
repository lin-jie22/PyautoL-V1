"""V31 判决分析 —— 只读落盘证据，判据只执行不解释（预注册 §5 的机器版）。

⛔ 三条口径纪律（都由前面的轮次踩出来）：
1. `--state-dump` 对同一 `(task, it)` 会写**多行**（V30 §94.8-2：`e2ni it=5` 两行 1854/1638，
   带块的是**前一行**）⇒ 键必须是 `(task, it, 同键第几行)`。
2. 到达必须逐字搜 `state`，⛔ 不用「账本载入 N 条」（纪律 29）。
3. 判据**并列**两条：「Σ 判据」与「逐格置换判据」（纪律 32；V25/V29 两次只看 Σ 看不见相消）。

用法：`analyze_v31.py /tmp/v31`
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ARMS = ("s0", "s1", "s4", "s3", "s2", "s1b")
TASKS = ("e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni")
CAP = 16
BLK = re.compile(r"\[外部评审\] (.*?)(?:\n\[|$)", re.S)
SRC = re.compile(r"\[[\w]+/(\w+)\]")
P_MARK, Q_MARK = "整份原样输出", "并无实质差异"
ROOT = Path("/Users/linshaojie/V1")
SUITE = ROOT / "bench" / "specgen" / "tasks_v8.jsonl"


def load_scores(w: Path, tag: str) -> dict[str, int]:
    f = w / f"bench_{tag}" / "runs.jsonl"
    if not f.exists():
        return {}
    rec = json.loads(f.read_text(encoding="utf-8").splitlines()[0])
    rep = rec.get("report") or rec
    out = {}
    for p in rep.get("per_task", []):
        out[str(p["id"])] = int(round(float(p["score"]) * CAP))
    return out


def load_meta(w: Path, tag: str) -> dict:
    f = w / f"bench_{tag}" / "runs.jsonl"
    if not f.exists():
        return {}
    rec = json.loads(f.read_text(encoding="utf-8").splitlines()[0])
    rep = rec.get("report") or rec
    return {"elapsed": rep.get("elapsed_s"), "skipped": rep.get("skipped_no_answer"),
            "failed": rep.get("failed_scorer")}


def load_states(w: Path, tag: str) -> dict[tuple[str, int, int], str]:
    p = w / f"states_{tag}.jsonl"
    out: dict[tuple[str, int, int], str] = {}
    if not p.exists():
        return out
    seen: dict[tuple[str, int], int] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        k2 = (str(d["task"]), int(d["it"]))
        n = seen.get(k2, 0)
        seen[k2] = n + 1
        out[(k2[0], k2[1], n)] = str(d["state"])
    return out


def load_answers(w: Path, tag: str) -> dict[str, str]:
    f = w / f"bench_{tag}" / "answers.jsonl"
    out: dict[str, str] = {}
    if not f.exists():
        return out
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            out.setdefault(str(d.get("task_id")), str(d.get("answer") or ""))
    return out


def skeleton_needs() -> dict[str, tuple[list[str], list[str]]]:
    """从题面里取：该题**必须**有的 import 名、**必须**在答案里出现的骨架函数名。

    `meta.expect_names` 是「本题需要的外部模块」的唯一事实源（尺子生成时写进去的）；
    骨架辅助函数从 prompt 的 ```python 块里 grep `def`，剔除被要求补全的那个函数。
    """
    out = {}
    for line in SUITE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        tid = str(d["id"])
        prompt = str(d.get("prompt") or "")
        m = re.search(r"```python\n(.*?)```", prompt, re.S)
        if not m:
            out[tid] = ([], [])
            continue
        skel = m.group(1)
        defs = re.findall(r"^def (\w+)\(", skel, re.M)
        target = re.search(r"请你把函数 `(\w+)` 补全", prompt)
        tgt = target.group(1) if target else ""
        helpers = [x for x in defs if x != tgt]
        out[tid] = (list(d.get("meta", {}).get("expect_names") or []), helpers)
    return out


def crit_of(state: str) -> str:
    m = BLK.search(state)
    return m.group(1) if m else ""


def main(argv: list[str]) -> int:
    w = Path(argv[1] if len(argv) > 1 else "/tmp/v31")
    sc = {a: load_scores(w, a) for a in ARMS}
    st = {a: load_states(w, a) for a in ARMS}
    an = {a: load_answers(w, a) for a in ARMS}
    mt = {a: load_meta(w, a) for a in ARMS}
    need = skeleton_needs()

    out: list[str] = []
    p = out.append
    p("=" * 108)
    p("V31 判决 —— 装置分辨率标定（预注册 bench/evidence/v6_s121/PREREG.md）")
    p(f"工作目录 {w}")
    p("=" * 108)

    # ── 1 逐臂读数 ────────────────────────────────────────────────────
    p("\n## 1 逐臂读数（分数 ×16；来自 bench_<tag>/runs.jsonl）\n")
    hdr = "| 臂 | 账本 | scope | " + " | ".join(f"`{t}`" for t in TASKS) + " | Σ |"
    p(hdr)
    p("|" + "---|" * (len(TASKS) + 4))
    for a in ARMS:
        s = sc.get(a, {})
        row = " | ".join(str(s.get(t, "—")) for t in TASKS)
        p(f"| `{a}` | {a} | — | {row} | **{sum(s.values())}** |")

    if any(not sc.get(a) for a in ARMS):
        p("\n⛔ 有臂缺读数 ⇒ 按 `J0` 处理（先修装置，不出判据）")

    # ── 2 到达（纪律 29：逐字搜 state）────────────────────────────────
    p("\n## 2 到达与块（只从 state dump 取，⛔ 不看日志）\n")
    p("| 臂 | state 行 | 含块 state/题 | 块字数和 | P 命中行 | Q 命中行 | 最长 state |")
    p("|---|---|---|---|---|---|---|")
    for a in ARMS:
        d = st.get(a, {})
        blocks = [s for s in d.values() if BLK.search(s)]
        tasks = {k[0] for k, s in d.items() if BLK.search(s)}
        pchars = sum(len(crit_of(s)) for s in blocks)
        hp = sum(1 for s in d.values() if P_MARK in s)
        hq = sum(1 for s in d.values() if Q_MARK in s)
        mx = max((len(s) for s in d.values()), default=0)
        p(f"| `{a}` | {len(d)} | {len(blocks)}/{len(tasks)} | {pchars} | {hp} | {hq} | {mx} |")

    # ── 3 前提闸 ─────────────────────────────────────────────────────
    p("\n## 3 前提闸（PREREG §4）\n")
    gates: dict[str, bool] = {}

    def per_task_hits(tag: str, mark: str) -> set[str]:
        return {k[0] for k, s in st.get(tag, {}).items() if mark in s}

    g_p = per_task_hits("s4", P_MARK)
    gates["P0-g1"] = g_p == {"e1wi", "e1ni", "e2wi", "e2ni", "e3ni"}
    p(f"- **P0-g1** `s4` 的 P 命中题集 = {sorted(g_p)}（应恰为 e1wi,e1ni,e2wi,e2ni,e3ni）"
      f" {'✅' if gates['P0-g1'] else '❌'}")

    g_q = per_task_hits("s3", Q_MARK)
    g_q_p = per_task_hits("s3", P_MARK)
    gates["P0-g2"] = g_q == {"e1wi", "e1ni", "e2wi", "e2ni", "e3ni"} and not g_q_p
    p(f"- **P0-g2** `s3` 的 Q 命中题集 = {sorted(g_q)}，P 泄漏 = {sorted(g_q_p) or '无'}"
      f" {'✅' if gates['P0-g2'] else '❌'}")

    g_s2 = per_task_hits("s2", P_MARK)
    gates["P0-g3"] = not g_s2
    p(f"- **P0-g3** `s2` 的 P 零送达（放在永不送达的 `e1ni` 条目）："
      f"命中 {sorted(g_s2) or '无'} {'✅' if gates['P0-g3'] else '❌'}")

    g_s1 = per_task_hits("s1", P_MARK) | per_task_hits("s1", Q_MARK)
    gates["P0-g4"] = not g_s1
    p(f"- **P0-g4** `s1` 基线不含任何标记：{'✅' if gates['P0-g4'] else '❌ ' + str(sorted(g_s1))}")

    g_s0 = any(BLK.search(s) for s in st.get("s0", {}).values())
    gates["P0-g5"] = not g_s0
    p(f"- **P0-g5** `s0`（scope=off）一个块都没有：{'✅' if gates['P0-g5'] else '❌'}")

    #: ⛔ 不要写成 `A and B or C`（优先级 = `(A and B) or C`，会把缺字段的臂也算进"坏"）
    bad: dict[str, tuple] = {}
    for a in ARMS:
        m = mt.get(a) or {}
        if (m.get("skipped") or 0) or (m.get("failed") or 0):
            bad[a] = (m.get("skipped"), m.get("failed"))
    gates["P0-h"] = not bad and all(sc.get(a) for a in ARMS)
    p(f"- **P0-h** 六臂 `skipped_no_answer=0` / `failed_scorer=0`："
      f"{'✅' if gates['P0-h'] else '❌ ' + str(bad)}")
    p(f"  逐臂：{ {a: (mt.get(a, {}).get('skipped'), mt[a].get('failed') if mt.get(a) else None) for a in ARMS} }")

    # ── 4 主判据（Σ + 逐格，并列）────────────────────────────────────
    p("\n## 4 主判据（并列：Σ 与 逐格）\n")
    s1, s4, s3, s2 = sc.get("s1", {}), sc.get("s4", {}), sc.get("s3", {}), sc.get("s2", {})
    d_payload = sum(s4.values()) - sum(s1.values())
    d_placebo = sum(s3.values()) - sum(s1.values())
    d_nondeliv = sum(s2.values()) - sum(s1.values())
    cells = {t: (s1.get(t, 0), s4.get(t, 0)) for t in TASKS}
    moved = {t: v for t, v in cells.items() if v[0] != v[1]}
    up = {t: v for t, v in moved.items() if v[1] > v[0]}
    down = {t: v for t, v in moved.items() if v[1] < v[0]}
    pcells = {t: (s1.get(t, 0), s3.get(t, 0)) for t in TASKS}
    pmoved = {t: v for t, v in pcells.items() if v[0] != v[1]}

    p(f"- **S0（Σ 判据）** `ΔΣ(s4 − s1) = {d_payload:+d}`；"
      f"`ΔΣ(s3 − s1) = {d_placebo:+d}`；`ΔΣ(s2 − s1) = {d_nondeliv:+d}`")
    p(f"- **C0（逐格判据）** `s4` vs `s1` 移动的格：{moved or '无'}")
    p(f"  - 上升 {up or '无'}；下降 {down or '无'}")
    p(f"- **C0'（安慰剂逐格）** `s3` vs `s1` 移动的格：{pmoved or '无'}")

    # ── 5 结构与因果（J_RESOLVED 的第 3 条）──────────────────────────
    p("\n## 5 结构检查（分数涨了不算，答案里得有 P 要求的东西）\n")
    p("| 题 | import 需要 | 骨架 helper | s1 涨分 | s1 有 import | s1 有 helper | s4 涨分 | s4 有 import | s4 有 helper |")
    p("|---|---|---|---|---|---|---|---|---|")
    struct_ok = {}
    for t in TASKS:
        if not need.get(t):
            continue
        imps, helpers = need[t]
        row = [t, "/".join(imps) or "—", "/".join(helpers) or "—"]
        res = {}
        for a in ("s1", "s4"):
            ans = an.get(a, {}).get(t, "")
            has_imp = any(re.search(rf"^\s*import\s+{re.escape(n)}\b", ans, re.M) for n in imps) if imps else True
            has_help = all(re.search(rf"^\s*def {re.escape(h)}\(", ans, re.M) for h in helpers) if helpers else True
            res[a] = (has_imp, has_help)
            row += [str(sc.get(a, {}).get(t, "—")), "是" if has_imp else "否", "是" if has_help else "否"]
        struct_ok[t] = res
        p("| " + " | ".join(row) + " |")

    # ── 6 判决（按 PREREG §5 的顺序执行，先命中先判）─────────────────
    p("\n## 6 判决（按预注册 §5 顺序执行）\n")
    j = None
    why = ""
    if not all(gates.values()):
        j, why = "J0", f"前提闸未过：{[k for k, v in gates.items() if not v]}"
    elif not moved and d_payload == 0:
        j, why = "J_NOROOM", "s4 与 s1 逐格完全相同 ⇒ 装置分辨不出这条指令"
    elif up == {} and down:
        j, why = "J_INVERT", f"只降不升：{down}"
    elif d_payload == 0:
        j, why = "J_DICE", f"Σ 零净但出现置换：{moved}"
    elif pmoved and any(pcells[t][1] - pcells[t][0] >= CAP for t in pmoved):
        j, why = "J_DICE", f"安慰剂 `s3` 也把格抬起 ⇒ 74 字只是扰动：{pmoved}"
    else:
        lifted = [t for t, (a, b) in up.items() if a == 0 and b == CAP]
        good = [t for t in lifted
                if struct_ok.get(t, {}).get("s4", (False, False))[0]
                and struct_ok.get(t, {}).get("s4", (False, False))[1]]
        if d_payload >= CAP and good:
            j, why = "J_RESOLVED", f"Σ {d_payload:+d}，被拉起且结构对上：{good}"
        else:
            j, why = "J_DICE", (f"Σ {d_payload:+d} 但结构对不上："
                                f"拉起 {lifted}，其中结构齐的 {good or '无'}")

    p(f"**判决 = `{j}`** —— {why}\n")
    p(f"押注对账：预注册押 `J_RESOLVED` 且 `e2ni`/`e3ni` 至少一格被拉起。"
      f"实测被拉起 {list(up) or '无'} ⇒ {'✅ 押中' if j == 'J_RESOLVED' else '❌ 押错'}")

    # ── 7 漂移对照 ───────────────────────────────────────────────────
    p("\n## 7 时段漂移（`s1b` vs `s1`，同账本同样命令，隔约 50 分钟）\n")
    d1, d1b = sc.get("s1", {}), sc.get("s1b", {})
    drift_cells = {t: (d1.get(t), d1b.get(t)) for t in TASKS if d1.get(t) != d1b.get(t)}
    st1, st1b = st.get("s1", {}), st.get("s1b", {})
    keys = set(st1) | set(st1b)
    st_diff = sum(1 for k in keys if st1.get(k) != st1b.get(k))
    p(f"- Σ：`s1`={sum(d1.values())}  `s1b`={sum(d1b.values())}")
    p(f"- 逐格不同的格：{drift_cells or '无'}")
    p(f"- state 快照 {len(st1)}/{len(st1b)}，逐字节不同的 {st_diff} 个")
    p(f"⇒ 漂移 = **{'0（本轮时段无漂移）' if not drift_cells and st_diff == 0 else '非 0，见上'}**")

    # ── 8 s4 的块原文（人工可核）────────────────────────────────────
    p("\n## 8 `s4` 首条含块 state 的评审块原文（人眼可核 P 是否真在里面）\n")
    shown = 0
    for k in sorted(st.get("s4", {})):
        s = st["s4"][k]
        if BLK.search(s):
            p(f"--- {k[0]} it={k[1]}#{k[2]}  state={len(s)}")
            p("```")
            p(crit_of(s))
            p("```")
            shown += 1
            if shown >= 3:
                break

    print("\n".join(out))
    print("\nVERDICT", j)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
