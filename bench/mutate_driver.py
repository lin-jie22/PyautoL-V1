#!/usr/bin/env python3
"""进化闭环的**第一段驱动器**：变异 → 跑 → 量 → 判。

## 这个文件为什么现在才存在

第七十五轮体检的结论是「**不能进化**」，三条断线：

1. `mw/mutate.py` 在 `mw/` 内**零引用**（`core.py` 的 15 个 import 里没有 `.mutate`）
2. `bench/mutation_check.py` 的 `mutation_verdict` **没吃过一条真实读数**
3. 变异台账**从未被真实写过**

三块拼图都在，缺的是把它们连起来的那一段。本文件就是那一段。

## 分层：谁管什么

| 层 | 文件 | 管什么 |
|---|---|---|
| 机制 | `mw/mutate.py` | 这个变异**允许**吗、记账 |
| 执行 | 本文件 | 把变异**真的注入**一次运行，并验证它生效了 |
| 测量 | `bench/noise_floor.py` | 这一臂自己抖多少 |
| 裁决 | `bench/mutation_check.py` | 要不要保留 |

⛔ 裁决仍然不在内核里（AO11）。本文件也**不是**内核的一部分：
它只调 `run.py` 这个**外部**入口，不 import 进 `mw/` 的任何执行路径。

## 最重要的一格：配置对象换了 ≠ 内核读了它

`run_tests.sh:470` 写着这条：

> `apply_mutation` 返回 ok=True 只证明配置对象换了，不证明内核读了它

所以本文件在**每次**跑完之后，从 `runs.jsonl` 里把实际生效的
`caliber.limits` 读出来，做**两个**断言：

- **A 生效**：目标旋钮的值 == 我们想要的值（`V1_MAX_STALL=3` 真的成了 3）
- **B 单变量**：除目标旋钮外，其余所有 limits 字段与 base 臂**逐个相同**

B 是 `mutation_verdict` 那条「不是单变量 ⇒ Δ 不可归因」的**前置校验**。
如果只在裁决函数里验，验的是"我以为我改了什么"，不是"跑的时候是什么"。

## 安慰剂臂为什么要挑 `working_memory_items`

`mutation_verdict` 要求 `placebo_delta`：缺它就不判（7u 实测：一条自认为
"中性"的指令被模型读成处理臂，Δ+0.15）。安慰剂必须是**等长但语义惰性**的改动。

- **等长** = 各取自己的一个 `step`：`max_stall` 的 step 是 1（2→3），
  `working_memory_items` 的 step 是 8（24→32）。都正好一步。
- **语义惰性是可证的**（不是猜的）：`mw/memory.py:160` 是
  `deque(maxlen=max(1, working_items))`。`maxlen` 只决定**从哪里开始丢**。
  实测 435 次 finish 的步数最多 7（`max_steps=12` 从没用完），
  一题内的动作数远小于 24 ⇒ `maxlen` 取 24 还是 32，`deque` 里装的东西
  **逐元素相同** ⇒ 行为相同。这是结构性事实，不是"我们觉得它不影响"。

⚠️ 但驱动器仍然**运行后复查**：安慰剂臂的逐题分若与 base 臂不同，
就说明"惰性"这个前提在这一次没成立，此时 `placebo_delta` 不能当噪音扣 ——
本文件会把这件事**打印出来并拒绝判定**，而不是悄悄用掉。

## 用法

    # 冒烟（三臂各 1 次，先验证驱动器本身）
    python3 bench/mutate_driver.py --n 1

    # 正式（MIN_RUNS=5 才给得出噪声下限）
    python3 bench/mutate_driver.py --n 5

需要先配好真模型（本地 ollama 即可，零成本）：

    export V1_LLM_BASE_URL=http://localhost:11434/v1
    export V1_LLM_API_KEY=ollama
    export V1_LLM_MODEL=qwen2.5:7b
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, fields as dc_fields, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mw import mutate as MU                                    # noqa: E402
from mw.config import Limits, Settings                          # noqa: E402
from bench import mutation_check as MC                         # noqa: E402
from bench import noise_floor as NF                            # noqa: E402


def fields_of_limits() -> set[str]:
    """`Limits` 的全部字段名。**唯一**实现 —— 别处不许再拼一遍。

    ⚠️ 它是"哪些环境变量必须先清掉"的唯一依据：
    漏一个字段，调用者 shell 里恰好设着的那个值就会溜进"基线臂"。
    """
    return {f.name for f in dc_fields(Limits)}

#: 题集。⛔ 与第七十二/七十三轮同源（`specgen/tasks_v1.jsonl`），
#: 但第七十四轮把用例 7 → 14 ⇒ `suite_sha` 变了 ⇒ **口径是新的**，
#: 旧读数（0.2857 / 0.5952）不能与新尺子的分数并列比较。
SUITE = "bench/specgen/tasks_v1.jsonl"

#: 独立进程跑法：与 `bench/run_noise_floor.sh` 逐字一致。
#: ⛔ 固定 `--repeat 1`：`repeat` 是同一进程内的重复，拿它当轮间噪声会系统性低估。
BASE_CMD = [
    sys.executable, "run.py", "--bench", "--suite", SUITE,
    "--solver", "llm", "--fresh", "--no-external",
    "--temperature", "0", "--repeat", "1", "--no-ledger",
]


def total_cells(suite: str = SUITE) -> int:
    """题集的**格数** = Σ 每题断言条数。最小效应量的分母。

    ★ 为什么从题集现算而不是写常数：第七十六轮踩过一个 4 位小数字面量的坑
      ——`0.0357` 比 `3/84 = 0.035714` 小 1.4e-5，而裁决器的容差是 1e-9，
      于是「恰好等于噪声」会被判成「大于」。用 `格数/总格数` 算就没有这个缝。
    """
    n = 0
    for line in Path(suite).read_text(encoding="utf-8").splitlines():
        if line.strip():
            n += len(json.loads(line).get("tests") or [])
    return n


def env_key(knob: str) -> str:
    """`Limits` 字段名 → 环境变量名。唯一实现，别处不许再拼一遍。"""
    return "V1_" + knob.upper()


@dataclass(frozen=True)
class Arm:
    """一个臂：跑什么、跑完必须看到什么。"""

    name: str
    env: Mapping[str, str]
    #: 该臂跑完后 `caliber.limits` 里**必须**出现的值（空 = 全默认）。
    expect: Mapping[str, Any]


def runs_path() -> Path:
    return Settings().paths.bench / "runs.jsonl"


def count_lines(p: Path) -> int:
    return len(p.read_text(encoding="utf-8").splitlines()) if p.exists() else 0


def read_new(path: Path, before: int) -> list[dict[str, Any]]:
    """只取本次运行**新增**的行。

    ⛔ 不按 `run_id` 猜：`runs.jsonl` 是 append-only，行号是比 pid 更直接的事实。
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, Any]] = []
    for line in lines[before:]:
        if not line.strip():
            continue
        out.append(json.loads(line))
    return out


def clean_env(extra: Mapping[str, str]) -> dict[str, str]:
    """父环境 + 目标变异，但**先清掉所有 `Limits` 字段的同名 env**。

    为什么必须清：如果调用者的 shell 里正好设着 `V1_MAX_STALL=4`，
    而我们要的 base 臂是默认 2，那次"base"跑的其实是 4 —— 三个臂一起错，
    而读数看上去完全正常。**没被清掉的东西，就是没被控制的东西。**
    """
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("V1_") and k[3:].lower() in fields_of_limits())}
    env.update(extra)
    return env


def run_once(arm: Arm, idx: int, log_path: Path) -> dict[str, Any]:
    """跑一次（一个独立进程），返回它落盘的那一条记录。"""
    rp = runs_path()
    rp.parent.mkdir(parents=True, exist_ok=True)
    before = count_lines(rp)
    env = clean_env(arm.env)
    t0 = time.time()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as fh:
        proc = subprocess.run(BASE_CMD, cwd=str(ROOT), env=env,
                              stdout=fh, stderr=subprocess.STDOUT)
    dt = time.time() - t0
    if proc.returncode != 0:
        tail = "\n".join(log_path.read_text(encoding="utf-8").splitlines()[-8:])
        raise RuntimeError(
            f"⛔ {arm.name} 第 {idx} 次没有正常出分（exit={proc.returncode}，{dt:.0f}s）\n"
            f"   日志 {log_path}\n{tail}")
    recs = read_new(rp, before)
    if len(recs) != 1:
        raise RuntimeError(
            f"⛔ {arm.name} 第 {idx} 次落盘了 {len(recs)} 条（应为 1）"
            f" ⇒ 取不到「这一次」是哪一条")
    rec = recs[0]
    rec["_elapsed_s"] = round(dt, 1)
    return rec


def check_record(rec: Mapping[str, Any], arm: Arm, base_limits: Mapping[str, Any]) -> None:
    """两个断言：**生效** 与 **单变量**。不通过就抛错，不静默继续。

    ⚠️ 期望"该差哪个旋钮"**只能从 `arm.expect` 推**，不许当参数传进来。
       第一版把它写成 `check_record(..., knob)`，主流程传的是处理臂的 `max_stall`，
       于是安慰剂臂（改的是 `working_memory_items`）被自己的守门员判红 ——
       而 base 臂因为 `expect` 为空，这个参数**恒不被读**，
       正是那个"恒不被读的参数"让 bug 藏到了安慰剂臂才露头。
    """
    lim = ((rec.get("caliber") or {}).get("limits")) or {}
    if not lim:
        raise RuntimeError(f"⛔ {arm.name} 的记录里没有 caliber.limits ⇒ 无法验证变异生效")
    want = sorted(arm.expect)
    if len(want) > 1:
        raise RuntimeError(f"⛔ {arm.name} 声明了多个旋钮 {want} ⇒ 不是单变量，Δ 不可归因")
    # A 生效
    for k, v in arm.expect.items():
        got = lim.get(k)
        if got != v:
            raise RuntimeError(
                f"⛔ {arm.name}: {k} 想设成 {v!r}，实际跑的是 {got!r}"
                f" ⇒ 环境变量没进内核（`{env_key(k)}` 没用）")
    # B 单变量
    diff = sorted(MC.limits_diff(base_limits, lim))
    if diff != want:
        raise RuntimeError(
            f"⛔ {arm.name}: 与 base 的 limits 差异是 {diff}，"
            f"期望 {want} ⇒ 不止一个变量，Δ 不可归因")
    if not rec.get("run_id"):
        raise RuntimeError(
            f"⛔ {arm.name}: 这条记录没有 run_id ⇒ 无法证明它是独立的一次运行")


def pool_from_ledger(runs: Path, *, arm: Arm, base_limits: Mapping[str, Any],
                     sig: str | None, solver: str | None = None
                     ) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """从**整个** `runs.jsonl` 里挑出属于这一臂的记录（不限本次跑的）。

    `sig=None` ⇒ 不按口径过滤（只用于"这台账上这个臂有哪些口径"这种问法）。

    为什么必须从台账挑而不是只认本次新增：
      「5 次独立运行」本来就是**可以跨会话累积**的（`noise_floor` 的设计如此）。
      只认本次新增，会让"昨天跑了 3 次、今天跑 2 次"永远凑不满，
      然后每一次都拿"没测够"当结论。

    ⛔ 四条过滤缺一不可：
      · `caliber_sig` 必须**完全相同**（含 `suite_sha` / `limits` / `repeat` / `fresh`）；
      · **`solver` 必须相同** —— `Caliber` 里**没有** solver 字段，所以
        裸模型直连与内核可以是同一个 sig（第七十三轮实测：只按 sig 分组会造出
        0.3095 的假噪声）。只按 limits+sig 挑，会把两个系统混进一个池子；
      · 没有 `run_id` 的旧记录**拒收并计数**（不是静默丢）；
      · `limits` 指纹必须恰好等于该臂声明的变异。
    """
    want = sorted(arm.expect)
    kept: list[dict[str, Any]] = []
    drop = {"no_run_id": 0, "sig_mismatch": 0, "solver_mismatch": 0,
            "limits_mismatch": 0, "malformed": 0}
    if not runs.exists():
        return kept, drop
    for line in runs.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            drop["malformed"] += 1
            continue
        if not rec.get("run_id"):
            drop["no_run_id"] += 1
            continue
        if sig is not None and str(rec.get("caliber_sig") or "") != sig:
            drop["sig_mismatch"] += 1
            continue
        if solver is not None and str(rec.get("solver") or "") != solver:
            drop["solver_mismatch"] += 1
            continue
        lim = ((rec.get("caliber") or {}).get("limits")) or {}
        if sorted(MC.limits_diff(base_limits, lim)) != want:
            drop["limits_mismatch"] += 1
            continue
        if any(lim.get(k) != v for k, v in arm.expect.items()):
            drop["limits_mismatch"] += 1
            continue
        kept.append(rec)
    return kept, drop


def _ledger_arm(runs: Path, arm: Arm,
                base_limits: Mapping[str, Any]) -> list[dict[str, Any]]:
    """台账上这个臂的全部记录（**不按口径/solver 过滤**，只用于"它有哪些"）。"""
    return pool_from_ledger(runs, arm=arm, base_limits=base_limits, sig=None)[0]


def pool(records: Sequence[Mapping[str, Any]], arm_name: str) -> list[NF.Obs]:
    """把记录读成 `Obs`，并把臂名**覆盖成本驱动器给的名字**。

    为什么要覆盖：三个臂的 `solver` 标签完全相同（都是内核 + 同一个模型），
    区别只在 `limits`。用 solver 当臂名会把三池混成一池 ——
    `noise_floor` 的池子必须按 `(臂, sig)` 分，而"臂"在这里是**变异配置**。
    """
    out: list[NF.Obs] = []
    for r in records:
        out.append(replace(NF.from_record(r), arm=arm_name))
    return out


def judge(*, knob: str, means: Mapping[str, float],
          floors: Mapping[str, float | None],
          runs: Mapping[str, Sequence[float]],
          min_effect_cells: int = 0,
          suite: str = SUITE) -> tuple[str, str, dict[str, Any]]:
    """三臂读数 → 裁决。返回 `(verdict, why, 明细)`。

    **纯函数**：不跑模型、不读台账（只读题集算格数）。

    ★ 抽出来只有一个理由：让契约能用**合成读数**直接测这两道闸，
      不用真跑 12 次模型。⛔ 若某道闸只在 `main()` 里才生效，
      契约就只剩"去读源码"这一条路 —— 那不是测。
    """
    known = [v for v in floors.values() if v is not None]
    nf = max(known) if known and len(known) == len(floors) else None
    placebo_delta = float(means["placebo"]) - float(means["base"])
    cells = total_cells(suite) if min_effect_cells else 0
    min_effect = (min_effect_cells / cells) if cells else None
    control = list(runs.get("base") or []) + list(runs.get("placebo") or [])
    verdict, why = MC.mutation_verdict(
        knob=knob, diff=[knob],
        base_mean=means["base"], cand_mean=means["cand"],
        placebo_delta=placebo_delta, noise_floor=nf,
        cand_runs=list(runs.get("cand") or []) or None,
        control_runs=control or None,
        min_effect=min_effect)
    return verdict, why, {"noise_used": nf, "placebo_delta": placebo_delta,
                          "min_effect": min_effect,
                          "min_effect_cells": min_effect_cells,
                          "cells": cells}


def main() -> int:
    ap = argparse.ArgumentParser(description="变异驱动器：跑三臂 → 量噪声 → 出裁决")
    ap.add_argument("--knob", default="max_stall", help="处理臂要动的旋钮")
    ap.add_argument("--to", type=int, default=3, help="处理臂的目标值")
    ap.add_argument("--placebo-knob", default="working_memory_items",
                    help="安慰剂臂要动的旋钮（必须语义惰性）")
    ap.add_argument("--placebo-to", type=int, default=32, help="安慰剂臂的目标值")
    ap.add_argument("--n", type=int, default=1, help="每臂独立运行次数（正式跑 5）")
    ap.add_argument("--min-effect-cells", type=int, default=3,
                    help="最小效应量（格）：低于它就够不上「变强」。0 = 关掉这道闸")
    ap.add_argument("--out", default="bench/evidence/evolve_s76",
                    help="证据目录")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ledger = Settings().paths.root / "state" / "mutation_ledger.jsonl"

    # ── 1. 先在**干净环境**下拿基线，再让内核机制批准两个变异并记账 ──────
    # 顺序不能反：如果先设 env 再 `Settings()`，取到的就已经是变异后的值，
    # `apply_mutation` 会判「已经是 3 ⇒ 空变异」而拒绝 —— 而那个拒绝是对的。
    saved = {k: os.environ.pop(k, None) for k in
             [env_key(k) for k in fields_of_limits()]}
    try:
        base_settings = Settings()
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

    base_limits = base_settings.limits.as_dict()
    m_cand = MU.apply_mutation(base_settings, a.knob, a.to, ledger=ledger)
    m_plac = MU.apply_mutation(base_settings, a.placebo_knob, a.placebo_to, ledger=ledger)
    for m in (m_cand, m_plac):
        print(("  ✅ " if m.ok else "  ⛔ ") + m.as_line())
        if not m.ok:
            print("⛔ 变异被内核的界拒绝 ⇒ 这一发不该跑（拒绝本身就是结论）")
            return 2
    assert m_cand.settings is not None and m_plac.settings is not None

    arms = [
        Arm("base", {}, {}),
        Arm("cand", {env_key(a.knob): str(a.to)},
            {a.knob: m_cand.settings.limits.as_dict()[a.knob]}),
        Arm("placebo", {env_key(a.placebo_knob): str(a.placebo_to)},
            {a.placebo_knob: m_plac.settings.limits.as_dict()[a.placebo_knob]}),
    ]

    print()
    print("=" * 74)
    print(f"变异驱动器：{a.knob} {base_limits[a.knob]!r} → {a.to!r}"
          f"（安慰剂：{a.placebo_knob} {base_limits[a.placebo_knob]!r} → {a.placebo_to!r}）")
    print(f"每臂 {a.n} 次独立进程 · 题集 {SUITE} · T=0 · --fresh --no-external")
    print("=" * 74)

    # ── 2. 逐臂跑（本次新增 n 次） ───────────────────────────────────────
    fresh: dict[str, list[dict[str, Any]]] = {ar.name: [] for ar in arms}
    for arm in arms:
        for i in range(1, a.n + 1):
            log = out / f"{arm.name}_r{i}.log"
            print(f"  {arm.name} 第 {i}/{a.n} 次 …", end="", flush=True)
            rec = run_once(arm, i, log)
            check_record(rec, arm, base_limits)
            fresh[arm.name].append(rec)
            mean = (rec.get("measurement") or {}).get("mean")
            print(f" {rec['_elapsed_s']}s  sig={rec.get('caliber_sig')}  "
                  f"mean={mean:.4f}")

    # ── 3. 定口径 → 从台账挑池（含**以前**跑过的同口径记录） ─────────────
    rp = runs_path()
    pools: dict[str, list[dict[str, Any]]] = {}
    sigs: dict[str, str] = {}
    solvers: dict[str, str] = {}
    for arm in arms:
        new = fresh[arm.name]
        if new:
            got = {str(r.get("caliber_sig")) for r in new}
            if len(got) > 1:
                raise RuntimeError(f"⛔ {arm.name} 本次跑出的口径不一致：{sorted(got)}"
                                   f" ⇒ 中途换了尺子，这一池不可比")
            sigs[arm.name] = got.pop()
        else:
            # `--n 0`：没有新增，只能从台账里认。要求该臂在台账里只有一种配置。
            got = {str(r.get("caliber_sig")) for r in _ledger_arm(rp, arm, base_limits)}
            if len(got) != 1:
                raise RuntimeError(f"⛔ {arm.name} 在台账里有 {len(got)} 种口径，"
                                   f"无法确定该用哪一个（{sorted(got)}）")
            sigs[arm.name] = got.pop()
        # solver 必须同源：`Caliber` 不含 solver ⇒ 同一个 sig 下可以躺着两个系统。
        if new:
            gs = {str(r.get("solver")) for r in new}
            if len(gs) > 1:
                raise RuntimeError(f"⛔ {arm.name} 本次跑出的 solver 标签不一致：{sorted(gs)}")
            solvers[arm.name] = gs.pop()
        else:
            cnt = collections.Counter(str(r.get("solver"))
                                      for r in _ledger_arm(rp, arm, base_limits))
            solvers[arm.name] = cnt.most_common(1)[0][0]
        recs, drop = pool_from_ledger(rp, arm=arm, base_limits=base_limits,
                                      sig=sigs[arm.name], solver=solvers[arm.name])
        if any(r.get("_elapsed_s") for r in new):
            # 让本次跑的耗时留在池子文件里（复核墙钟用）
            by_id = {r.get("run_id"): r.get("_elapsed_s") for r in new}
            recs = [dict(r, _elapsed_s=by_id[r["run_id"]])
                    if r.get("run_id") in by_id else r for r in recs]
        pools[arm.name] = recs
        print(f"  {arm.name}: 本次新增 {len(new)} · 池内同口径同 solver 共 {len(recs)} 次")
        print(f"     拒收：{drop['no_run_id']} 条无 run_id / {drop['sig_mismatch']} 条异口径"
              f" / {drop['solver_mismatch']} 条异 solver / {drop['limits_mismatch']} 条异配置")

    # ── 4. 落盘池子 ──────────────────────────────────────────────────────
    for name, recs in pools.items():
        p = out / f"pool_{name}.jsonl"
        p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs),
                     encoding="utf-8")

    # ── 5. 三臂各自的噪声下限（按 (臂,sig) 分池 —— 不许混） ─────────────
    print()
    print("── 噪声（每臂自成一池）" + "─" * 50)
    floors: dict[str, float | None] = {}
    means: dict[str, float] = {}
    runs: dict[str, list[float]] = {}
    for arm in arms:
        obs = pool(pools[arm.name], arm.name)
        floors[arm.name] = NF.noise_for_verdict(obs)
        means[arm.name] = sum(o.mean for o in obs) / len(obs)
        runs[arm.name] = [o.mean for o in obs]
        for line in NF.report(NF.bracket(obs), label=f"{arm.name}（{len(obs)} 次）"):
            print(line)
        print()

    # ── 6. 安慰剂的惰性复查（运行后） ───────────────────────────────────
    #
    # ⛔ 判据**不是**「逐题分逐个相同」。第一版这么写，被本轮读数当场否掉：
    #    实测三个臂在 sg02 上就有一个共同的 +0.2143 —— 那是**这一跑的抖动**，
    #    T=0 在这套题上也不是逐位确定的。要求"逐题全同"等于要求噪声为 0，
    #    而噪声为 0 恰恰是**没测够**时才"看起来成立"的东西。
    #
    # 正确的判据与 `mutation_verdict` 同口径：安慰剂的 Δ 若落在噪声内 ⇒
    # 没有证据说它有效应 ⇒ 惰性前提成立（它扣掉的就是噪声本身）。
    # 若超出噪声 ⇒ 说明"改一个不该有用的旋钮也让分动了"，这个装置有问题，必须报出来。
    def per_task(recs: Sequence[Mapping[str, Any]]) -> dict[str, float]:
        acc: dict[str, list[float]] = {}
        for r in recs:
            for row in (r.get("report") or {}).get("per_task", []):
                acc.setdefault(str(row.get("id")), []).append(float(row["score"]))
        return {k: sum(v) / len(v) for k, v in acc.items()}

    task_base, task_plac = per_task(pools["base"]), per_task(pools["placebo"])
    moved = sorted(k for k in set(task_base) | set(task_plac)
                   if abs(task_base.get(k, -1) - task_plac.get(k, -1)) > 1e-12)

    # 裁决先在**纯函数**里算出来（打印留到后面：惰性复查在前，裁决在后）。
    verdict, why, jx = judge(knob=a.knob, means=means, floors=floors, runs=runs,
                             min_effect_cells=a.min_effect_cells)
    nf = jx["noise_used"]
    placebo_delta = jx["placebo_delta"]
    inert: bool | None = (None if nf is None
                          else abs(placebo_delta) <= nf + MC._TOL)

    print("── 安慰剂惰性复查" + "─" * 52)
    print(f"  逐题有差异的题：{moved if moved else '（无）'}")
    if inert is None:
        print("  ⚠️ 噪声没测够（每臂需 5 次）⇒ **无法判断**安慰剂是否惰性。")
        print("     这不是「惰性成立」，是「还没有资格说话」。")
    elif inert:
        print(f"  ✅ |安慰剂 Δ| = {abs(placebo_delta):.4f} ≤ 噪声 {nf:.4f}"
              f" ⇒ 它动的量与抖动同量级，惰性前提成立")
        print(f"     （逐题差异 {len(moved)} 处是同一量级的抖动，不是「它真的有用」）")
    else:
        print(f"  ❌ |安慰剂 Δ| = {abs(placebo_delta):.4f} > 噪声 {nf:.4f}"
              f" ⇒ 改一个**用不到**的旋钮也让分动了。")
        print("     这说明这套测量装置里还混着别的东西；本次裁决不能作为保留依据。")
    print()

    # ── 7. 裁决 ──────────────────────────────────────────────────────────
    # 噪声取三臂最大：Δ 的不确定性来自每一条臂，任一臂越抖、门槛就该越高。
    # ⛔ 任一臂不够 5 次 ⇒ None ⇒ `mutation_verdict` 会判 UNKNOWN（不默认 0）。
    # 裁决本身已在上面由 `judge()` 算出（纯函数，可被契约用合成读数直测）。

    print("── 裁决" + "─" * 64)
    print(f"  base   {means['base']:.4f}")
    print(f"  cand   {means['cand']:.4f}   Δ = {means['cand'] - means['base']:+.4f}")
    print(f"  安慰剂 {means['placebo']:.4f}   Δ = {placebo_delta:+.4f}")
    print(f"  噪声   {('未测够（每臂需 5 次）' if nf is None else f'{nf:.4f}')}")
    print(f"  噪声明细：" + "  ".join(
        f"{k}={'None' if v is None else f'{v:.4f}'}" for k, v in floors.items()))
    # 交叠闸：把三臂的**每次读数**摊开印出来，让人能自己看「分不分得开」。
    if runs.get("cand") and (runs.get("base") or runs.get("placebo")):
        ctl = list(runs.get("base") or []) + list(runs.get("placebo") or [])
        way = MC.separation(runs["cand"], ctl)[0]
        print(f"  交叠   cand {min(runs['cand']):.4f}~{max(runs['cand']):.4f}  ｜  "
              f"对照(base∪安慰剂) {min(ctl):.4f}~{max(ctl):.4f}"
              f"  ⇒ {'分开↑' if way == 'up' else '分开↓' if way == 'dn' else '交叠'}")
    _me = jx["min_effect"]
    _me_s = "关" if _me is None else f"{_me:.4f}"
    print(f"  量级   最小效应量 {_me_s}"
          f"（{jx['min_effect_cells']} 格 / {jx['cells']} 格）")
    print()
    print(f"  ⇒ **{verdict}**  {why}")
    print()

    # ── 7. 证据 ──────────────────────────────────────────────────────────
    summary = {
        "knob": a.knob, "to": a.to,
        "placebo_knob": a.placebo_knob, "placebo_to": a.placebo_to,
        "n_per_arm": a.n, "suite": SUITE,
        "means": means, "floors": floors, "noise_used": nf,
        "placebo_delta": placebo_delta, "placebo_inert": inert,
        "min_effect": jx["min_effect"], "min_effect_cells": jx["min_effect_cells"],
        "cells": jx["cells"], "runs": runs,
        "verdict": verdict, "why": why,
        "sigs": {k: sorted({r.get("caliber_sig") for r in v}) for k, v in pools.items()},
        "arms": [{"name": ar.name, "env": dict(ar.env), "expect": dict(ar.expect)}
                 for ar in arms],
        "ts": time.time(),
    }
    (out / "verdict.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  证据已落盘 {out}/（三份池子 + verdict.json + 每次的日志）")
    print()

    # `should_keep` 是**唯一**保留判据（别处不许再写一遍 `== KEEP`）。
    print(f"  保留？ {'是' if MC.should_keep(verdict) else '否'}"
          f"（{verdict}）"
          f"{'' if inert else '  ⚠️ 但安慰剂不惰性，这一发的裁决不成立'}")
    return 0 if (inert and verdict != MC.UNKNOWN) else 1


if __name__ == "__main__":
    raise SystemExit(main())
