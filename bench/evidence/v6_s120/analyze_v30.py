#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V30 判决：把状态预算抬到够用之后，「外部评审」送到每一题，分数会不会动？

只读落盘证据，不起模型。
判据写死在 `bench/evidence/v6_s120/PREREG.md`（起跑前冻结）。本脚本**只执行不解释**：
字面判决按预注册的矩阵取，逐格归因另开一节。

用法：analyze_v30.py [工作目录，默认 /tmp/v30] [--v29 /tmp/v29]
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

BLOCKS = ("[目标]", "[工作记忆]", "[语义记忆]", "[外部评审]", "[轨迹]", "[第")
CRIT = re.compile(r"\[外部评审\] (.*?)(?:\n\[|$)", re.S)
SRC = re.compile(r"\[[\w]+/(\w+)\]")

ARMS = ("o3", "p3", "o4", "p4")
CAPS = {"o3": 1600, "p3": 1600, "o4": 2400, "p4": 2400}
SCOPES = {"o3": "off", "p3": "prev", "o4": "off", "p4": "prev"}
TAGS = [f"{a}_r{r}" for r in (1, 2) for a in ARMS]
ALL = [f"{a}_r{r}" for r in (1, 2, 3) for a in ARMS]
TASKS = ("e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni")
CAP = 16
FLOOR_CELL = 16   # 当 noise_floor=0 时，判据按格子粒度执行


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def load_states(w: Path, tag: str) -> dict[tuple[str, int, int], str]:
    """按 **(题, 步, 同键第几次)** 建索引。

    ⚠️ 索引里必须有第三个分量：`--state-dump` 对同一个 `(task, it)` **可能写多行**
    （实测 `o3_r1` 的 `e3ni it=4` 两行、`p4_r2` 的 `e2ni/e3ni it=5` 各两行，
    后者两行内容还不同：1854 与 1638）。只用 `(task, it)` 作键会被后一行覆盖 ——
    `p4_r2` 里被覆盖的恰好是**带评审块**的那一行 ⇒ 到达率会被低估。
    那是「字典吞数据」，不是 dump 有问题。
    """
    p = w / f"states_{tag}.jsonl"
    out: dict[tuple[str, int, int], str] = {}
    seen: dict[tuple[str, int], int] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        k2 = (str(d["task"]), int(d["it"]))
        nth = seen.get(k2, 0)
        seen[k2] = nth + 1
        out[(k2[0], k2[1], nth)] = str(d["state"])
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


def load_acc(w: Path) -> dict[str, dict[str, str]]:
    p = w / "acc.txt"
    out: dict[str, dict[str, str]] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if not parts:
            continue
        out[parts[0]] = dict(x.split("=", 1) for x in parts[1:] if "=" in x)
    return out


def total(sc: dict[str, float]) -> int:
    return int(sum(sc.get(t, 0) for t in TASKS))


def diff(a: dict[tuple[str, int, int], str], b: dict[tuple[str, int, int], str]) -> tuple[int, int]:
    keys = set(a) | set(b)
    return sum(1 for k in keys if a.get(k) != b.get(k)), len(set(a) ^ set(b))


def crit_of(state: str) -> str:
    m = CRIT.search(state)
    return m.group(1) if m else ""


def crit_tasks(st: dict[tuple[str, int, int], str]) -> dict[str, int]:
    d = {t: 0 for t in TASKS}
    for k, s in st.items():
        if CRIT.search(s):
            d[k[0]] = d.get(k[0], 0) + 1
    return d


def main(argv: list[str]) -> int:
    w = Path(argv[1]) if len(argv) > 1 and not argv[1].startswith("-") else Path("/tmp/v30")
    v29 = Path("/tmp/v29")
    if "--v29" in argv:
        v29 = Path(argv[argv.index("--v29") + 1])

    sc = {t: load_scores(w, t) for t in ALL}
    st = {t: load_states(w, t) for t in ALL}
    sh = {t: load_sha(w, t) for t in ALL}
    acc = load_acc(w)

    L: list[str] = []
    p = L.append

    p("=" * 96)
    p("V30  状态预算 1600 → 2400 × 外部评审 off/prev（2×2，四臂两轮）")
    p(f"工作目录 {w}   对照 {v29}")
    p("=" * 96)

    # ── 0 读数总表 ────────────────────────────────────────────────────
    p("\n## 0 读数（逐臂逐轮）\n")
    p("| 臂 | 轮 | cap | scope | Σ | " + " | ".join(TASKS) + " | 交付件指纹 |")
    p("|---|---|---|---|---|" + "---|" * len(TASKS) + "---|")
    for r in (1, 2):
        for a in ARMS:
            t = f"{a}_r{r}"
            s = sc[t]
            fp = ",".join(sh[t].get(k, "?")[:4] for k in TASKS)
            p(f"| `{a}` | r{r} | {CAPS[a]} | {SCOPES[a]} | **{total(s)}** | "
              + " | ".join(str(int(s.get(k, 0))) for k in TASKS) + f" | `{fp}` |")

    # ── 1 前提闸 ──────────────────────────────────────────────────────
    p("\n## 1 前提闸（任一不过 ⇒ J0 装置作废）\n")
    gates: dict[str, bool] = {}

    # P0-c：o3 复现 V29 的 o2（逐字节）
    lines = []
    okc = True
    for r in (1, 2):
        n, only = diff(st[f"o3_r{r}"], load_states(v29, f"o2_r{r}"))
        same_fp = all(sh[f"o3_r{r}"].get(k) == load_sha(v29, f"o2_r{r}").get(k) for k in TASKS)
        same_s = total(sc[f"o3_r{r}"]) == total(load_scores(v29, f"o2_r{r}"))
        lines.append(f"r{r}: state 差异 {n} 个（键差 {only}）、Σ 相同={same_s}、指纹相同={same_fp}")
        okc = okc and n == 0 and only == 0 and same_fp and same_s
    gates["P0-c"] = okc
    p(f"- **P0-c** `o3` 逐字节复现 V29 `o2`：{'✅' if okc else '❌'}")
    for x in lines:
        p(f"    - {x}")

    # P0-d：自变量真的生效（p4_r2 6/6；p3_r2 不是 6/6）
    c4 = crit_tasks(st["p4_r2"])
    c3 = crit_tasks(st["p3_r2"])
    n4 = sum(1 for t in TASKS if c4[t] > 0)
    n3 = sum(1 for t in TASKS if c3[t] > 0)
    gates["P0-d"] = (n4 == 6) and (n3 < 6)
    p(f"- **P0-d** 载荷到达：`p4_r2` {n4}/6 题的 state 含 `[外部评审]`（要求 6/6）；"
      f"`p3_r2` {n3}/6（要求 <6）⇒ {'✅' if gates['P0-d'] else '❌'}")
    p(f"    - `p4_r2` 逐题 state 数：{c4}")
    p(f"    - `p3_r2` 逐题 state 数：{c3}")

    # P0-e：自评闸 0 泄漏
    leaks = []
    for k, s in st["p4_r2"].items():
        blk = crit_of(s)
        if k[0] in SRC.findall(blk):
            leaks.append((k[0], k[1]))
    gates["P0-e"] = not leaks
    p(f"- **P0-e** 自评闸：`p4_r2` 泄漏 {len(leaks)} 处 {'✅' if not leaks else '❌ ' + str(leaks[:4])}")

    # P0-f：判据名 / skipped
    bad = [t for t in TAGS if acc.get(t, {}).get("judge") != "OK"]
    skip = {t: acc.get(t, {}).get("skipped", "NA") for t in TAGS}
    gates["P0-f"] = not bad and all(v == "0" for v in skip.values())
    p(f"- **P0-f** 判据名 6/6 `5项(…结构检查)`、`skipped_no_answer=0`："
      f"{'✅' if gates['P0-f'] else '❌'}（异常臂 {bad or '无'}；skipped {skip}）")

    # P0-b / P0-g：由起跑前的探针与签名闸落盘，这里只核对存在性。
    # ⚠️ 首版这里只查工作目录 `/tmp/v30` ⇒ 把**证据目录里真实存在**的两份报告读成
    #    「❌ 缺失」，进而把整轮误判成 J0（纪律 28「缺席冒充零」的近亲：
    #    **找错地方 ≠ 不存在**）。改成按候选目录列表逐个找。
    ev = Path(__file__).resolve().parent

    def _verdict(name: str) -> tuple[bool, str]:
        for d in (w, ev, Path("/Users/linshaojie/V1/bench/evidence/v6_s120")):
            f = d / name
            if f.exists():
                ok = "VERDICT OK" in f.read_text(encoding="utf-8", errors="ignore")
                return ok, str(f)
        return False, f"（{name} 在 {w} 与 {ev} 都没有）"

    reach_ok, reach_src = _verdict("p0_reach.txt")
    sig_ok, sig_src = _verdict("p0_sig.txt")
    gates["P0-b"] = reach_ok
    gates["P0-g"] = sig_ok
    p(f"- **P0-b** 押注前到达率探针 VERDICT OK：{'✅' if reach_ok else '❌'}  `{reach_src}`")
    p(f"- **P0-g** 口径签名随 cap 变 VERDICT OK：{'✅' if sig_ok else '❌'}  `{sig_src}`")

    all_gates = all(gates.values())
    p(f"\n**装置判决：{'P0 全过' if all_gates else 'J0 —— 装置作废，不出判据'}**")
    if not all_gates:
        p(f"未过的闸：{[k for k, v in gates.items() if not v]}")

    # ── 2 噪声下限 ────────────────────────────────────────────────────
    p("\n## 2 噪声下限（同一臂两轮自身重复）\n")
    n_o3, only_o3 = diff(st["o3_r1"], st["o3_r2"])
    d_o3 = total(sc["o3_r2"]) - total(sc["o3_r1"])
    if n_o3 == 0 and only_o3 == 0:
        floor = 0
        p(f"- `o3` r1 与 r2 的 state **逐字节相同**（{len(st['o3_r1'])} 个快照，0 差异），Σ 差 {d_o3}")
        p(f"- ⇒ `noise_floor = 0`，判据按格子粒度执行：`|Δ| ≥ {FLOOR_CELL}` 才算效应")
    else:
        floor = abs(d_o3)
        p(f"- `o3` r1 与 r2 有 {n_o3} 个快照不同（键差 {only_o3}），Σ 差 {d_o3}")
        p(f"- ⇒ `noise_floor = {floor}`")
    eff = max(floor, FLOOR_CELL) if floor == 0 else floor

    # ── 3 Σ 判据 ──────────────────────────────────────────────────────
    p("\n## 3 Σ 判据\n")
    dp = total(sc["p4_r2"]) - total(sc["o4_r2"])
    db = total(sc["o4_r2"]) - total(sc["o3_r2"])
    S = "S+" if dp > eff else ("S−" if dp < -eff else "S0")
    B = "B+" if db > eff else ("B−" if db < -eff else "B0")
    p(f"- **载荷**（同预算 2400）：`Σ_p4_r2({total(sc['p4_r2'])}) − Σ_o4_r2({total(sc['o4_r2'])})` = **{dp:+d}** ⇒ `{S}`")
    p(f"- **预算**（同 scope=off）：`Σ_o4_r2({total(sc['o4_r2'])}) − Σ_o3_r2({total(sc['o3_r2'])})` = **{db:+d}** ⇒ `{B}`")
    p(f"- 参照（V29 复查）：`Σ_p3_r2 − Σ_o3_r2` = "
      f"{total(sc['p3_r2']) - total(sc['o3_r2']):+d}（通道通、材料只到 {n3}/6 题）")

    # ── 4 逐格置换判据（纪律 32）────────────────────────────────────────
    p("\n## 4 逐格（纪律 32：两格反向相消在 Σ 判据里完全不可见）\n")
    p("| 题 | o3 | p3 | o4 | p4 | Δ_payload | Δ_budget |")
    p("|---|---|---|---|---|---|---|")
    delta = {}
    for t in TASKS:
        d_t = int(sc["p4_r2"].get(t, 0) - sc["o4_r2"].get(t, 0))
        delta[t] = d_t
        p(f"| `{t}` | {int(sc['o3_r2'].get(t, 0))} | {int(sc['p3_r2'].get(t, 0))} | "
          f"{int(sc['o4_r2'].get(t, 0))} | {int(sc['p4_r2'].get(t, 0))} | {d_t:+d} | "
          f"{int(sc['o4_r2'].get(t, 0) - sc['o3_r2'].get(t, 0)):+d} |")
    pos = [t for t in TASKS if delta[t] >= FLOOR_CELL]
    neg = [t for t in TASKS if delta[t] <= -FLOOR_CELL]
    same_fp_all = all(sh["p4_r2"].get(t) == sh["o4_r2"].get(t) for t in TASKS)
    if not pos and not neg and same_fp_all:
        C = "C0"
    elif pos and not neg:
        C = "C1"
    elif pos and neg:
        C = "C2"
    elif neg and not pos:
        C = "C3"
    else:
        C = "C0"
    p(f"\n- 被拉起（≥+{FLOOR_CELL}）：**{pos or '无'}**；掉下（≤−{FLOOR_CELL}）：**{neg or '无'}**")
    p(f"- 六份交付件指纹逐个相同：{same_fp_all}")
    p(f"- ⇒ `{C}`")

    # ── 5 判决矩阵 ────────────────────────────────────────────────────
    p("\n## 5 判决（预注册 §6.4 矩阵，逐字执行）\n")
    table = {
        ("S+", "C0"): "CONTRADICT", ("S+", "C1"): "J1", ("S+", "C2"): "J1'", ("S+", "C3"): "J1''",
        ("S0", "C0"): "J2", ("S0", "C1"): "CONTRADICT", ("S0", "C2"): "J3", ("S0", "C3"): "CONTRADICT",
        ("S−", "C0"): "CONTRADICT", ("S−", "C1"): "CONTRADICT", ("S−", "C2"): "J4", ("S−", "C3"): "J5",
    }
    verdict = table.get((S, C), "CONTRADICT")
    if not all_gates:
        verdict = "J0"
    desc = {
        "J1": "载荷生效（第一次）",
        "J1'": "载荷生效，但伴随反向掉落",
        "J1''": "载荷生效，但同时有独立负格",
        "J2": "通电、完全零效应（Σ 与逐格都零）",
        "J3": "零净效应，但逐格发生置换",
        "J4": "有害且伴随置换",
        "J5": "有害（单向负）",
        "J0": "装置作废，不出判据",
        "CONTRADICT": "Σ 与逐格自相矛盾 ⇒ 按预注册重算一次；仍矛盾判 J0",
    }
    p(f"- Σ 判定 `{S}`（Δ_payload={dp:+d}，噪声/格限 {eff}）；逐格判定 `{C}`")
    p(f"- **判决 `{verdict}` — {desc.get(verdict, '')}**")
    p(f"- 旁读数 `{B}`：预算单独效应 Δ_budget={db:+d}")

    # ── 5.5 第三轮复现（预注册 §6.5：任何非零 Δ 都必须加跑第三轮）──────────
    p("\n## 5.5 第三轮复现（预注册 §6.5）\n")
    t3 = [f"{a}_r3" for a in ARMS]
    have3 = all((w / f"bench_{x}" / "runs.jsonl").exists() for x in t3)
    rep_ok = None
    if not have3:
        p("- 第三轮**未跑** ⇒ 按 §6.5，本轮任何非零 Δ 只能写「单次观测，未复现」。")
    else:
        d3 = int(sum(sc["p4_r3"].get(t, 0) - sc["o4_r3"].get(t, 0) for t in TASKS))
        pos3 = [t for t in TASKS if sc["p4_r3"].get(t, 0) - sc["o4_r3"].get(t, 0) >= FLOOR_CELL]
        neg3 = [t for t in TASKS if sc["p4_r3"].get(t, 0) - sc["o4_r3"].get(t, 0) <= -FLOOR_CELL]
        p("| 题 | o3_r3 | p3_r3 | o4_r3 | p4_r3 | Δ_payload(r3) |")
        p("|---|---|---|---|---|---|")
        for t in TASKS:
            p(f"| `{t}` | {int(sc['o3_r3'].get(t, 0))} | {int(sc['p3_r3'].get(t, 0))} | "
              f"{int(sc['o4_r3'].get(t, 0))} | {int(sc['p4_r3'].get(t, 0))} | "
              f"{int(sc['p4_r3'].get(t, 0) - sc['o4_r3'].get(t, 0)):+d} |")
        n_o3b, only_o3b = diff(st["o3_r2"], st["o3_r3"])
        p(f"\n- `Δ_payload(r3)` = **{d3:+d}**；被拉起 {pos3 or '无'}；掉下 {neg3 or '无'}")
        p(f"- 噪声正控 `o3_r2` vs `o3_r3`：state 差异 {n_o3b} 个（键差 {only_o3b}）"
          f" ⇒ {'再次为 0，`noise_floor=0` 复现' if n_o3b == 0 and only_o3b == 0 else '≠0'}")
        rep_ok = (d3 == dp) and (set(pos3) == set(pos)) and (set(neg3) == set(neg))
        p(f"- 与第二轮（Δ={dp:+d}，拉起 {pos or '无'}，掉下 {neg or '无'}）"
          f"逐格同构：{'✅ 复现' if rep_ok else '❌ 未复现'}")
    p(f"\n⇒ 判决 `{verdict}` 的强度："
      + ("**三轮同构，可作为结论**" if rep_ok else
         ("三轮不同构 ⇒ 只能写「单次观测，未复现」" if have3 else
          "**单次观测（第三轮未跑）** ⇒ 只能写「未复现」，不得写「生效」或「有害」")))

    # ── 6 押注对账 ────────────────────────────────────────────────────
    p("\n## 6 押注对账（预注册 §7）\n")
    p("- 主押：`S0 + (C0 或 C2)` ⇒ " + ("✅ 押中" if (S == "S0" and C in ("C0", "C2")) else "❌ 押错"))
    p(f"- 副押：若真有单格被拉起，最可能是 `e2ni` ⇒ "
      f"{'✅ 押中' if pos == ['e2ni'] else ('未发生（无正格）' if not pos else '❌ 押错，实际是 ' + str(pos))}")
    p("- 否证条件（预注册）：+16 落在 `e1wi`/`e1ni`/`e2wi`/`e3wi` 上 ⇒ 押错的不是位置而是推理")

    # ── 7 逐 state 明细（P0-d 的证据面）──────────────────────────────────
    p("\n## 7 `p4_r2` 逐 state（评审块到底在哪几步出现）\n")
    p("| 题 | it | 同键第几行 | state 长 | 块字 | 块内来源 | 含工作记忆 |")
    p("|---|---|---|---|---|---|---|")
    for k in sorted(st["p4_r2"]):
        t, it, nth = k
        s = st["p4_r2"][k]
        blk = crit_of(s)
        p(f"| `{t}` | {it} | {nth} | {len(s)} | {len(blk)} | {','.join(SRC.findall(blk)) or '—'} | "
          f"{'是' if '[工作记忆]' in s else '否'} |")

    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
