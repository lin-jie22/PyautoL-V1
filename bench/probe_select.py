#!/usr/bin/env python3
"""答案无关的选择器值多少格？（§48，第八十五轮，**零模型调用**）

===========================================================================
预注册（写在任何计算之前，2026-10-25）
===========================================================================

## 为什么是「选择」而不是继续找判据

§46 关掉了一条路（自检判对错：选择性 0），§47 又把另一条压到 20.1%（结构约束的检测覆盖）。
两轮合起来的教训是：**在这个尺子上，答案无关的判据判不了对错**。
但「判不了对错」≠「没有用」—— 它还可以当**选择器**：

> 给出 N 个候选，挑一个。不需要知道哪个对，只需要知道「哪个明显不像样」。

第七十二轮之前曾经否掉多采样，理由是「多出来也没有选择器」。那句话在当时是对的
（当时唯一的判据是模型自写的断言）。§47 之后情况变了：手上有两族**精度 100%**
（在真对答案上 0 误响）的闸。⇒ 本轮把「没有选择器」这句旧话重新量一次。

## 两个池子（一个是退化的，一个是真多样的）

⛔ **两个池子的读数不许并列**（`suite_sha` 不同：`d6b7ed14…` vs `cee95582…`）。
   摆在一起只为一件事：**机会有多大**（`oracle − 最好单链`）与**闸抓住多少**是两个独立的量。

| 池 | 链 | 说明 | 机会 |
|---|---|---|---|
| `v2`（10 题 140 格） | `C1` 内核默认 / `C2`,`C3` 内核 `off` ×2 | ⛔ `C2`/`C3` 同口径签名 = **同一个臂跑两次** | 小 |
| `v1`（6 题 84 格） | `D` 直连 / `K` 内核（`verify_static=2`） | 真两种策略（prompt 不同，§81） | 大 |

`v1` 的 `D` 臂读数来自 §81 的独立台账（`/tmp/v1_direct_s80`），本轮**冻结**进
`bench/evidence/select_s85/`（`PROVENANCE.md` 记来源与 sha256）。

## 四臂（只有一条是新的）

| 臂 | 规则 | 它回答什么 |
|---|---|---|
| `固定链` | 每条链单独全量 | 不用选择器能做到什么 |
| `随机` | 每题均匀随机挑一条，固定种子抽 200 次 | **安慰剂**：选择这个动作本身值不值钱 |
| `闸选` | 违反数最少者（见下） | 答案无关的选择器 |
| `oracle` | 每题取最高分那条 | 天花板（挑中需要 oracle，**是上界不是能力**） |

## 闸选的排序键（先写死，不许看了结果再调）

```
key = (是否崩溃, 结构违反条数, 链序)
```
先排除崩溃（崩溃的候选连 14 格都跑不完），再比结构违反条数，最后按**链序**定序。
⛔ 权重 1:1 是**任意**的：本文件不调参，只用这一个键。

## 预测

- **P1** `v2` 池：`闸选 ≥ 最好单链`，但增益极小（机会本来就小）。
- **P2** `v1` 池：`闸选 < 最好单链`。机制是**可推的**——§47 的闸对「干净但错」的候选
  （结构全合规、只是算错）**零信息**，而 `v1` 里 `D` 臂恰好大量「干净但错」
  （§81：`DIRECT_SYSTEM` 不写注释 ⇒ 输出短、结构合规、答案错）。
  排序键把「干净」排在「脏」前面 ⇒ 它会把**对的**换成**干净的错的**。
- **P3** 两个池子的 `oracle − 最好单链` 差一个量级 ⇒ 说明「机会」取决于池子多样性，
  而「抓取率」取决于闸的信息量。**这两件事过去被合成一句话，是本轮要拆开的**。

## 判据（写死）

- 若某池 `闸选 − 最好单链 > 0` 且该差值 ≥ 2 格 ⇒ 记为「可能有信息价值」（仍需噪声下限）。
- 若 `< 0` ⇒ **有害**，不许接。
- 若 `|差值| ≤ 1 格` ⇒ `UNKNOWN`（低于任何可测噪声下限）。
- ⛔ 三选一，不许把 1 格说成「有效」，也不许把负数藏进「总体仍略优」。

## 边界

1. ⛔ 选择器不判对错（§46）。它只能「避开不像样的」；本轮 P2 就是这条的性质第一次显形。
2. ⛔ 两个池子 `suite_sha`/`sig` 都不同 ⇒ 分数不许互比，只比「机会」与「抓取率」两个无量纲量。
3. ⛔ 不许把 `oracle` 写成能力。
4. ⛔ 闸读题面的解析器是我渲染的题面（§47 边界 1，尺子被做成镜子的第二处）。

用法：
    python3 bench/probe_select.py --self-check          # 零模型，六组反证
    python3 bench/probe_select.py --report              # 两个池子一起出
    python3 bench/probe_select.py --report --pool v1
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))       # 直接 `python3 bench/x.py` 时也能 import bench.*

from bench.probe_structcheck import (                                # noqa: E402
    cases_of, derive, eval_source, load_specs, rules_from_text,
)
from bench.scorers.code_exec import extract_code                      # noqa: E402

EV = ROOT / "bench/evidence/select_s85"

#: 池子定义。⛔ 链序是**预注册的定序规则**（判断并列时用），不许按分数排。
POOLS: dict[str, dict[str, Any]] = {
    "v2": {
        "suite": ROOT / "bench/specgen/tasks_v2.jsonl",
        "note": "10 题 140 格｜⛔ C2/C3 是同一个臂跑两次（口径签名相同）",
        "chains": [
            ("C1-内核默认", ROOT / "bench_out/runs.jsonl", ROOT / "bench_out/answers.jsonl",
             "69666@1792912166"),
            ("C2-off-r2", ROOT / "bench_out/runs.jsonl", ROOT / "bench_out/answers.jsonl",
             "70620@1792912544"),
            ("C3-off-r1", ROOT / "bench_out/runs.jsonl", ROOT / "bench_out/answers.jsonl",
             "5425@1792919669"),
        ],
    },
    "v1": {
        "suite": ROOT / "bench/specgen/tasks_v1.jsonl",
        "note": "6 题 84 格｜两种**真不同**的策略（prompt 不同，§81）",
        # ⛔ 两条链都从 evidence 读（**不依赖 `bench_out/`**）：`bench_out/` 是 gitignore 的
        #    本地台账，新克隆的仓库里没有它 ⇒ 若 v1 池依赖它，`--report --pool v1`
        #    就只能在"我这台机器上"复现。冻结进去的代价是 8KB。
        "chains": [
            ("D-直连", EV / "v1_direct_runs.jsonl", EV / "v1_direct_answers.jsonl",
             "59260@1792909152"),
            ("K-内核", EV / "v1_kernel_runs.jsonl", EV / "v1_kernel_answers.jsonl",
             "15808@1792896324"),
        ],
    },
}

#: 随机臂的种子与抽样次数（写死，可复现）。
SEED = 20261025
DRAWS = 200


# ===========================================================================
# 层 1：闸（只用题面文字，⛔ 不碰 meta.spec）
# ===========================================================================


def gate_of(prompt: str, code: str, fn: str, cases: list[list[str]]) -> dict:
    """候选 → `{"exec": bool, "struct": [...]}`。空代码按崩溃算（它跑不了）。"""
    if not (code or "").strip():
        return {"exec": True, "struct": []}
    return eval_source(derive(rules_from_text(prompt)), code, fn, cases)


def sort_key(g: dict, idx: int) -> tuple:
    """闸选的排序键。⛔ 先排除崩溃，再比结构违反条数，最后按链序。"""
    return (1 if g["exec"] else 0, len(g["struct"]), idx)


# ===========================================================================
# 层 2：候选池
# ===========================================================================


def _jsonl(p: Path) -> list[dict]:
    if not p.exists():
        raise SystemExit(f"❌ 缺文件 {p}（池子不完整，读数不可比）")
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _run_record(path: Path, run_id: str) -> dict:
    for r in _jsonl(path):
        if str(r.get("run_id")) == run_id:
            return r
    raise SystemExit(f"❌ {path.name} 里没有 run_id={run_id}")


def _answers(path: Path, run_id: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in _jsonl(path):
        if str(r.get("run_id")) == run_id and r.get("kind") == "answer" and r.get("task_id"):
            out[str(r["task_id"])] = str(r.get("answer") or "")
    return out


def arm_identity(rec: dict) -> str:
    """臂的身份 = `(caliber_sig, solver)`。

    ⛔ 只用 `caliber_sig` 会误判：§81 记过 `Caliber` **不含 solver** ⇒ 直连与内核同签名。
       两个都判成「同一臂」，池子的多样性就被抹掉了。
    """
    return f"{rec.get('caliber_sig') or '?'}｜{(rec.get('solver') or '?')[:12]}"


def build_pool(name: str) -> tuple[dict[str, dict[str, dict]], list[str], list[str],
                                   dict[str, str], str]:
    """→ (pool, 任务序, 标签序, {标签: 臂身份}, 备注)。"""
    spec = POOLS[name]
    specs = load_specs(Path(spec["suite"]))
    cases = {tid: cases_of(row) for tid, row in specs.items()}
    labels: list[str] = []
    ident: dict[str, str] = {}
    pool: dict[str, dict[str, dict]] = {}
    order: list[str] = []
    for label, runs, ans_p, rid in spec["chains"]:
        labels.append(label)
        rec = _run_record(Path(runs), rid)
        ident[label] = arm_identity(rec)
        per = {str(t["id"]): float(t["score"]) for t in rec["report"]["per_task"]}
        ans = _answers(Path(ans_p), rid)
        for tid, row in specs.items():
            if tid not in pool:
                pool[tid] = {}
                order.append(tid)
            n = len(cases[tid])
            code = extract_code(ans.get(tid, ""))
            pool[tid][label] = {
                "code": code, "fn": str(row["meta"]["spec"]["fn"]), "cells": n,
                "got": int(round(per.get(tid, 0.0) * n)),
                "gate": gate_of(str(row["prompt"]), code,
                                str(row["meta"]["spec"]["fn"]), cases[tid]),
            }
    for tid in order:
        missing = [x for x in labels if x not in pool[tid]]
        if missing:
            raise SystemExit(f"❌ {tid} 缺候选 {missing}")
    return pool, order, labels, ident, str(spec["note"])


# ===========================================================================
# 层 3：四臂
# ===========================================================================


def arm_fixed(pool: dict, order: list[str], label: str) -> dict[str, int]:
    return {tid: pool[tid][label]["got"] for tid in order}


def arm_gate(pool: dict, order: list[str], labels: list[str]) -> tuple[dict[str, int], dict[str, str]]:
    got: dict[str, int] = {}
    picks: dict[str, str] = {}
    for tid in order:
        key, lab = min((sort_key(pool[tid][lb]["gate"], i), lb) for i, lb in enumerate(labels))
        picks[tid] = lab
        got[tid] = pool[tid][lab]["got"]
    return got, picks


def arm_oracle(pool: dict, order: list[str], labels: list[str]) -> dict[str, int]:
    return {tid: max(pool[tid][lb]["got"] for lb in labels) for tid in order}


def arm_random(pool: dict, order: list[str], labels: list[str],
               seed: int = SEED, draws: int = DRAWS) -> dict[str, Any]:
    rng = random.Random(seed)
    tot = []
    for _ in range(draws):
        tot.append(sum(pool[tid][labels[rng.randrange(len(labels))]]["got"] for tid in order))
    tot.sort()
    return {"mean": sum(tot) / len(tot), "min": tot[0], "max": tot[-1],
            "median": tot[len(tot) // 2], "draws": draws, "seed": seed, "all": tot}


# ===========================================================================
# 层 4：读数
# ===========================================================================


def _sum(d: dict[str, int]) -> int:
    return sum(d.values())


def verdict_label(delta: int) -> str:
    if delta > 0 and delta >= 2:
        return "可能有效（缺噪声下限）"
    if delta < 0:
        return "有害"
    return "UNKNOWN（低于可测噪声下限）"


def report_pool(name: str) -> dict[str, Any]:
    pool, order, labels, ident, note = build_pool(name)
    denom = sum(pool[order[0]][labels[0]]["cells"] for _ in order)
    fixed = {lb: _sum(arm_fixed(pool, order, lb)) for lb in labels}
    gate_got, picks = arm_gate(pool, order, labels)
    gate = _sum(gate_got)
    oracle_got = arm_oracle(pool, order, labels)
    oracle = _sum(oracle_got)
    rnd = arm_random(pool, order, labels)
    best = max(fixed.values())
    best_lb = max(fixed, key=lambda x: fixed[x])

    print("=" * 96)
    print(f"池 `{name}`：{note}")
    print("=" * 96)
    print(f"  {len(order)} 题 × {len(labels)} 链｜分母 {denom} 格")
    groups: dict[str, list[str]] = {}
    for lb in labels:
        groups.setdefault(ident[lb], []).append(lb)
    for who, lbs in groups.items():
        if len(lbs) > 1:
            print(f"  ⛔ 池子退化：{' 与 '.join(lbs)} 的臂身份相同（{who}）"
                  f"⇒ 同一个臂的重复，不是两种策略。有效策略数 = {len(groups)}")
    if all(len(v) == 1 for v in groups.values()):
        print(f"  ✅ {len(groups)} 个链的臂身份两两不同（真多样）")

    print()
    print("  一、不选（固定链）与天花板")
    for lb in labels:
        print(f"    {lb:<12} {fixed[lb]:>4}/{denom} = {fixed[lb] / denom:.4f}"
              f"{'   ← 最好单链' if lb == best_lb else ''}")
    print(f"    {'oracle':<12} {oracle:>4}/{denom} = {oracle / denom:.4f}"
          f"   （上界：挑中需要参考答案）")
    print(f"    {'随机(安慰剂)':<12} {rnd['mean']:>7.1f}/{denom} = {rnd['mean'] / denom:.4f}"
          f"   中位 {rnd['median']}｜极差 {rnd['min']}~{rnd['max']}（{rnd['draws']} 次，种子 {rnd['seed']}）")
    print(f"    {'闸选':<12} {gate:>4}/{denom} = {gate / denom:.4f}")

    print()
    print("  二、机会 vs 抓取（本轮要拆开的两件事）")
    print(f"    机会 = oracle − 最好单链 = {oracle - best} 格 ({oracle - best:+d})")
    print(f"    抓取 = 闸选 − 最好单链 = {gate - best} 格 ({gate - best:+d})")
    rate = (gate - best) / (oracle - best) if oracle != best else float("nan")
    print(f"    抓取率 = {rate:.1%}" if oracle != best else "    抓取率 = n/a（机会为 0）")
    print(f"    闸选 − 随机中位 = {gate - rnd['median']:+.0f} 格")

    print()
    print("  三、逐题")
    head = "".join(f"{lb.split('-')[0]:>6}" for lb in labels)
    print(f"    {'题':<7}{head}{'max':>6}   " + "".join(f"{lb.split('-')[0]+'闸':<14}" for lb in labels)
          + f"{'选':>4}{'差':>5}")
    for tid in order:
        cells = [pool[tid][lb]["got"] for lb in labels]
        gates = [("崩" if pool[tid][lb]["gate"]["exec"] else "-")
                 + (",".join(pool[tid][lb]["gate"]["struct"]) or "ok") for lb in labels]
        d = oracle_got[tid] - gate_got[tid]
        print(f"    {tid:<7}" + "".join(f"{c:>6}" for c in cells) + f"{oracle_got[tid]:>6}   "
              + "".join(f"{g:<14}" for g in gates) + f"{picks[tid].split('-')[0]:>4}"
              + (f"{-d:>5}" if d else f"{'':>5}"))

    blind = 0
    for tid in order:
        c = pool[tid][picks[tid]]
        if not c["gate"]["exec"] and not c["gate"]["struct"] and c["got"] < c["cells"]:
            blind += c["cells"] - c["got"]
    # ── 归因拆分：换选的得失是「闸的信息」还是「并列规则」？────────────────
    # ⛔ 不拆这一条，负读数会被错记成「闸有害」：两种原因的可修性完全不同。
    # ⛔ 判「并列」必须**去掉链序那一维**：`(exec, n_struct)` 打平就是并列，
    #    把 index 一起比会让每一次并列都被误记成「闸的信息」（第一版就是这么错的，
    #    v1 的 −7 格被记成「闸的信息」，实际是链序）。
    # ⛔ 基准取**固定最好单链**（不是 oracle）：`换选` 的定义就是「相对不选时的那条链」。
    cost = lambda g: (1 if g["exec"] else 0, len(g["struct"]))          # noqa: E731
    gain_info = gain_tie = loss_info = loss_tie = short_tie = 0
    for tid in order:
        b, p = pool[tid][best_lb]["got"], pool[tid][picks[tid]]["got"]
        if p != b:
            d = p - b
            info = cost(pool[tid][picks[tid]]["gate"]) < cost(pool[tid][best_lb]["gate"])
            if d > 0:
                gain_info, gain_tie = (gain_info + d, gain_tie) if info else (gain_info, gain_tie + d)
            else:
                loss_info, loss_tie = (loss_info - d, loss_tie) if info else (loss_info, loss_tie - d)
        # 相对 oracle 的缺口里，有多少是「所有候选一样干净」⇒ 并列规则背锅
        if p < oracle_got[tid]:
            kb = min(cost(pool[tid][lb]["gate"]) for lb in labels
                     if pool[tid][lb]["got"] == oracle_got[tid])
            if cost(pool[tid][picks[tid]]["gate"]) == kb:
                short_tie += oracle_got[tid] - p
    print()
    print("  四、缺口分解")
    print(f"    被选中的候选**两族全绿但仍然错**的格：{blind}"
          f"（没有任何答案无关的信息能挑出来 ⇒ 原理不可达，同 §47 的 18 格）")
    print(f"    相对最好单链的换选：收益 +{gain_info + gain_tie} 格（闸的信息 +{gain_info}｜"
          f"并列规则 +{gain_tie}）｜损失 −{loss_info + loss_tie} 格（信息 −{loss_info}｜"
          f"并列 −{loss_tie}）")
    print(f"    闸选 − oracle 的缺口 {oracle - gate} 格，其中 {short_tie} 格是「所有候选一样干净」"
          f"⇒ 只能由并列规则决定")
    print(f"    ⛔ 并列规则是**任意选的**（链序）：本池里它独立贡献 {loss_tie + gain_tie} 格，"
          f"而机会只有 {oracle - best} 格 ⇒ 谁声称「选择器有效」，先得扛住这一项。")
    hist = {lb: 0 for lb in labels}
    for tid in order:
        hist[picks[tid]] += 1
    print(f"    闸选去向：" + "｜".join(f"{lb} {hist[lb]}/{len(order)}" for lb in labels))
    print(f"    裁决（预注册）：闸选 − 最好单链 = {gate - best:+d} 格 ⇒ **{verdict_label(gate - best)}**")
    return {"pool": name, "denom": denom, "fixed": fixed, "best": best, "best_label": best_lb,
            "oracle": oracle, "gate": gate, "random_median": rnd["median"],
            "delta": gate - best, "opportunity": oracle - best, "blind": blind,
            "picks": picks, "verdict": verdict_label(gate - best)}


def report(pools: list[str]) -> int:
    sums = [report_pool(p) for p in pools]
    if len(sums) > 1:
        print()
        print("=" * 96)
        print("汇总：机会 vs 抓取率（⛔ 分数不许互比，只比这两个无量纲量）")
        print("=" * 96)
        print(f"  {'池':<6}{'分母':>6}{'最好单链':>9}{'oracle':>8}{'闸选':>6}"
              f"{'机会':>6}{'抓取':>6}{'抓取率':>8}   裁决")
        for s in sums:
            rate = (f"{s['delta'] / s['opportunity']:.1%}" if s["opportunity"] else "n/a")
            print(f"  {s['pool']:<6}{s['denom']:>6}{s['best']:>9}{s['oracle']:>8}{s['gate']:>6}"
                  f"{s['opportunity']:>6}{s['delta']:>+6}{rate:>8}   {s['verdict']}")
    bad = [s for s in sums if s["verdict"].startswith("有害")]
    return 0 if not bad else 1


# ===========================================================================
# 层 5：反证（零模型）
# ===========================================================================


def self_check() -> int:
    bad = 0
    print("(a) 闸必须在 10 个 oracle 输出上一条都不响（判据健全性，v2 题集）")
    specs = load_specs(POOLS["v2"]["suite"])
    n = 0
    for tid, row in specs.items():
        g = gate_of(str(row["prompt"]), str(row["oracle"]),
                    str(row["meta"]["spec"]["fn"]), cases_of(row))
        if g["exec"] or g["struct"]:
            print(f"  ❌ {tid} 在正确答案上响了：{g}")
            bad += 1
        else:
            n += 1
    print(f"  {'✅' if n == len(specs) else '❌'} {n}/{len(specs)} 题在 oracle 上静默")

    print("\n(b) 闸选的**必然错误**：干净但错 vs 脏但对 ⇒ 它一定挑干净但错的那个")
    clean_wrong = {"exec": False, "struct": []}
    dirty_right = {"exec": True, "struct": ["max_len", "prefix"]}
    pick = min([(sort_key(clean_wrong, 0), "干净但错"), (sort_key(dirty_right, 1), "脏但对")])
    if pick[1] != "干净但错":
        print(f"  ❌ 排序键没按预期工作：挑了 {pick[1]}")
        bad += 1
    else:
        print("  ✅ 挑了「干净但错」——不是 bug，是**边界证明**：")
        print("     选择器只保证「避开不像样的」，不保证「挑对」。§46 的选择性 0 没被本轮推翻。")

    print("\n(c) 臂身份必须分开「同口径」与「同解法」：v1 两链同 `caliber_sig` 但 solver 不同")
    # ⛔ 这一项**不读盘**（合成两条记录）：自证要能在"没有 bench_out 的新克隆"上跑，
    #    否则它会在 CI 里假红，然后被人用 `|| true` 消掉 —— 那才是真正的损失。
    fake_d = {"caliber_sig": "50fc5523de300ca2", "solver": "真模型直连 qwen2.5:7b T=0"}
    fake_k = {"caliber_sig": "50fc5523de300ca2",
              "solver": "内核/http qwen2.5:7b（每题新建内核 + 记忆隔离）"}
    same_sig = arm_identity(fake_d).split("｜")[0] == arm_identity(fake_k).split("｜")[0]
    diff_arm = arm_identity(fake_d) != arm_identity(fake_k)
    if same_sig and diff_arm:
        print(f"  ✅ 同签名（{arm_identity(fake_d).split('｜')[0][:8]}…）但两个臂身份"
              f" ⇒ `(caliber_sig, solver)` 能分开它们")
        print("  ✅ 反证：只用 `caliber_sig` 会把它们判成同一个臂（§81 记过的坑）")
    else:
        print(f"  ❌ 期望「同签名、两身份」，实得 same_sig={same_sig} diff_arm={diff_arm}")
        bad += 1

    print("\n(d) 闸只读题面：把 `meta.spec` 删掉后题面仍可解析（⇒ 无泄漏）")
    ok_d = 0
    for tid, row in specs.items():
        try:
            rules_from_text(str(row["prompt"]))
            ok_d += 1
        except ValueError as e:
            print(f"  ❌ {tid} 删 spec 后解析不了：{e}")
            bad += 1
    print(f"  {'✅' if ok_d == len(specs) else '❌'} {ok_d}/{len(specs)} 题在没有 spec 时仍可解析")

    print("\n(e) 随机臂可复现：同一种子两次抽样逐位相同")
    fake = {"x": {lb: {"got": 1} for lb in ("a", "b")}}
    r1 = arm_random(fake, ["x"], ["a", "b"])
    r2 = arm_random(fake, ["x"], ["a", "b"])
    if (r1["mean"], r1["min"], r1["max"]) != (r2["mean"], r2["min"], r2["max"]):
        print("  ❌ 随机臂不可复现")
        bad += 1
    else:
        print(f"  ✅ 可复现 mean={r1['mean']} min={r1['min']} max={r1['max']}")

    print("\n(f) 裁决函数必须在三档上都给出**不同**标签（否则分档是死代码）")
    labels = {verdict_label(3): "可能有效（缺噪声下限）", verdict_label(0): "UNKNOWN（低于可测噪声下限）",
              verdict_label(-12): "有害"}
    if len(set(labels)) != 3:
        print(f"  ❌ 三档塌缩：{labels}")
        bad += 1
    else:
        print(f"  ✅ +3 ⇒ {verdict_label(3)}｜0 ⇒ {verdict_label(0)}｜−12 ⇒ {verdict_label(-12)}")

    print(f"\n{'✅ 六组反证全部通过' if not bad else f'❌ {bad} 处失败'}")
    return 0 if not bad else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="答案无关的选择器值多少格（§48，内核外预演）")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--pool", default="all", choices=["all", "v1", "v2"])
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    pools = ["v2", "v1"] if a.pool == "all" else [a.pool]
    return report(pools)


if __name__ == "__main__":
    raise SystemExit(main())
