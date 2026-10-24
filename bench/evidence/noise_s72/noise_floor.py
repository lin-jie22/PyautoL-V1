#!/usr/bin/env python3
"""噪声下限：必须在**独立进程之间**量，不是在一次运行内部量。

## 这条约束是被两轮实测夹出来的

§35（第七十二轮）报告过 sg04 的四次观测 `0.2857 / 1.0 / 0.2857 / 0.2857`，
并据此说「噪声下限被系统性低估」。**那句话证据不足**：那四次跨了三个口径
（逐字段对撞见 DESIGN §36.1），而 `repeat` 本身就是 `Caliber` 的一个字段
⇒ 轮内极差与轮间极差**永远不同口径**，"拿轮内当轮间下限"在本项目的口径闸下
是违规，不只是"统计上可疑"。

第七十三轮按 `(臂, sig)` 分池、每池 5 个独立进程重测：两条臂的轮间极差都是
**0.0000**（逐位相同）。⇒ 真正的教训不是"轮间抖动大"，而是**「0」必须是被
测出来的**：5 次 ⇒ `0.0000`（确定）；4 次 ⇒ `None`（没测成）。
旧实现两者都给 0，而后者会让 `mutation_verdict` 把噪声读成改进。

## 四条纪律

1. **独立进程**：一次运行 = 一个进程 = 一条 `run_id`。同一个 pid 的多条记录
   是 `--repeat`，**不是**多次运行 —— 必须拒收，而不是算作 5 次。
2. `MIN_RUNS = 5`：不够就返回 `None`（上层 `mutation_verdict` 会判 `UNKNOWN`）。
   ⛔ 不默认 0。默认 0 等于"任何抖动都算改进"，而那正是本条纪律要消灭的东西。
3. **不默认 0 也不假装够**：n=5 的极差**仍然**是真实范围的下偏估计
   （要踩到两端才有 0.7143）。本模块只保证"不再系统性低估到 0"，
   不保证"这就是真噪声"——后者需要更多运行，本模块不给这个承诺。
4. **逐题**也量：一题涨、一题跌，聚合极差会相互抵消。
   `floor_task >= floor_agg` 是**定理**（聚合是逐题的凸组合，见 `invariant`），
   所以两个都输出，谁也不能替代谁。

## 它不做什么

不跑题、不落盘、不判"变好"。它只回答一个问题：
**在同一个口径下，同一份代码跑两次，分数能差多少。**
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

#: 独立运行次数下限。
#:
#: 为什么是 5 而不是 3：3 个样本里"至少有一个落在另一端"的概率仍偏低，
#: 实测里 4 次观测（0.2857/1.0/0.2857/0.2857）range 才踩到 0.7143，
#: 而任意 2 次观测的 range 是 0 或 0.7143 —— 一半概率给出 0。
#: ⛔ 这是**下限不是充分条件**（见模块 docstring 第 3 条）。
MIN_RUNS = 5

#: 浮点容差。与 `Curve.trend` / `mutation_verdict` 同一个理由：
#: `1.0 - 0.2857 - 0.7143` 会留下 1e-17 的残差。
_TOL = 1e-9


@dataclass(frozen=True)
class Obs:
    """**一次独立运行**（一个进程）的读数。

    `run_id` 是"这是不是另一次运行"的唯一判据 —— 用 pid + 启动时刻，
    因为 pid 会在重启后重用，单靠 pid 可能把两次真运行读成一次。
    """

    arm: str                      # 臂名（solver 的自由文本标签）
    sig: str                      # 口径签名
    run_id: str
    mean: float                   # 该次运行的聚合分（内部 repeat 的均值）
    repeat: int                   # 该次运行内部重复了几次（只用于诊断）
    per_task: tuple[tuple[str, float], ...]   # 逐题分（内部 repeat 的均值）
    rep_scores: tuple[float, ...]  # 逐次总分 —— 轮内极差从这里来

    @property
    def intra(self) -> float:
        """该次运行**内部**的极差。⛔ 它**不是**噪声下限，只是轮内一致性。"""
        return (max(self.rep_scores) - min(self.rep_scores)
                if len(self.rep_scores) >= 2 else 0.0)

    def task_ids(self) -> tuple[str, ...]:
        return tuple(k for k, _ in self.per_task)


def from_record(rec: Mapping[str, Any]) -> Obs:
    """把 `runs.jsonl` 的一行读成 `Obs`。

    ⛔ 没有 `run_id` 的记录**一律拒收**（抛错，不静默丢弃）：
       本轮之前落盘的 83 条读数都没有这个字段，
       它们无法回答"这是同一次运行的多次重复，还是多次运行"——
       把它们混进来，等于用一个已知不可判的量去量噪声。
    """
    rid = rec.get("run_id")
    if not rid:
        raise ValueError("这条读数没有 run_id ⇒ 无法判断是不是独立运行（旧记录不收）")
    m = rec.get("measurement") or {}
    if "mean" not in m:
        raise ValueError(f"读数 {rid} 缺 measurement.mean")
    per_task: dict[str, list[float]] = {}
    rep_task: dict[int, list[float]] = {}
    for row in (rec.get("report") or {}).get("per_task", []):
        tid = str(row.get("id"))
        per_task.setdefault(tid, []).append(float(row["score"]))
        rep_task.setdefault(int(row.get("rep", 1)), []).append(float(row["score"]))
    return Obs(
        arm=str(rec.get("solver", "")),
        sig=str(rec.get("caliber_sig", "")),
        run_id=str(rid),
        mean=float(m["mean"]),
        repeat=int(rec.get("repeat") or 1),
        per_task=tuple((k, statistics.fmean(v)) for k, v in sorted(per_task.items())),
        # 与 `mw.bench.run_suite` 同口径：每轮 total / n_tasks
        rep_scores=tuple(statistics.fmean(v) for _, v in sorted(rep_task.items())),
    )


def pool_from_jsonl(path: Path | str, *, arm: str | None = None,
                    sig: str | None = None) -> tuple[list[Obs], dict[str, int]]:
    """从 `runs.jsonl` 收集池子。返回 (读数, 拒收统计)。

    拒收统计**必须**被打印出来：静默丢行会让"只有 2 次独立运行"
    看起来像"有 5 次"。这一条是从"全量撤回没复跑"那次学来的同一类错误 ——
    没被数出来的东西，就是不存在的东西。
    """
    p = Path(path)
    kept: list[Obs] = []
    drop = {"no_run_id": 0, "arm_mismatch": 0, "sig_mismatch": 0, "malformed": 0}
    if not p.exists():
        return kept, drop
    for line in p.read_text(encoding="utf-8").splitlines():
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
        if arm is not None and str(rec.get("solver", "")) != arm:
            drop["arm_mismatch"] += 1
            continue
        if sig is not None and str(rec.get("caliber_sig", "")) != sig:
            drop["sig_mismatch"] += 1
            continue
        try:
            kept.append(from_record(rec))
        except (ValueError, KeyError):
            drop["malformed"] += 1
    return kept, drop


def _same(obs: Iterable[Obs]) -> tuple[bool, str]:
    """这一池读数是不是**同一个臂、同一个口径**。混了就不可比。"""
    xs = list(obs)
    arms = {o.arm for o in xs}
    sigs = {o.sig for o in xs}
    if len(arms) > 1:
        return False, f"混了 {len(arms)} 个臂：{sorted(arms)[:3]}"
    if len(sigs) > 1:
        return False, f"混了 {len(sigs)} 个口径：{sorted(sigs)[:3]}"
    return True, ""


def independence(obs: Iterable[Obs], *, min_runs: int = MIN_RUNS
                 ) -> tuple[int, str]:
    """独立运行次数。⛔ `--repeat` 不是独立运行：同一个 `run_id` 只算一次。"""
    ids = {o.run_id for o in obs}
    if len(ids) < min_runs:
        return len(ids), (f"只有 {len(ids)} 次独立运行（run_id 去重后），"
                          f"要求 ≥{min_runs} —— 同一次运行内部的 repeat "
                          f"**不是**多次运行，它量的是轮内，不是轮间")
    return len(ids), ""


def floor(obs: Iterable[Obs], *, min_runs: int = MIN_RUNS
          ) -> tuple[float | None, str]:
    """聚合分在**独立运行之间**的极差。`None` = 没有资格当噪声下限。

    `None` 不是"噪声是 0"，是"这件事没测成" —— 上层必须把它读成 `UNKNOWN`。
    """
    xs = [o for o in obs]
    if not xs:
        return None, "没有读数"
    ok, why = _same(xs)
    if not ok:
        return None, why
    n, why_n = independence(xs, min_runs=min_runs)
    if n < min_runs:
        return None, why_n
    means = [o.mean for o in xs]
    return max(means) - min(means), (f"{n} 次独立运行，聚合 {min(means):.4f}"
                                     f"..{max(means):.4f} ⇒ 极差 {max(means) - min(means):.4f}")


def floor_task(obs: Iterable[Obs]) -> dict[str, float]:
    """逐题在独立运行之间的极差。

    ⚠️ 任务集必须一致：某次运行少了一道题，那道题的"极差"就是拿缺考算出来的。
       不一致时抛错（**不是**跳过那道题）—— 跳过等于让它静默消失。
    """
    xs = [o for o in obs]
    if not xs:
        return {}
    ok, why = _same(xs)
    if not ok:
        raise ValueError(why)
    ids0 = xs[0].task_ids()
    for o in xs[1:]:
        if o.task_ids() != ids0:
            raise ValueError(f"任务集不一致：{o.run_id} 有 {len(o.task_ids())} 道，"
                             f"{xs[0].run_id} 有 {len(ids0)} 道")
    out: dict[str, float] = {}
    for tid in ids0:
        vals = [dict(o.per_task)[tid] for o in xs]
        out[tid] = max(vals) - min(vals)
    return out


def invariant(obs: Iterable[Obs]) -> tuple[bool, str]:
    """`max_task_floor >= floor_agg` —— 这是**定理**，不是经验。

    聚合分是逐题的凸组合（权重 1/n_tasks），所以
        |mean_a − mean_b| = |Σ_i (x_ai − x_bi)| / N ≤ max_i |x_ai − x_bi| ≤ max_i range_i
    ⇒ 聚合极差永远不会超过**最大的那一题**的极差。

    ★ 为什么值得钉成断言：这两个数的关系一旦被写反（拿逐题极差当聚合下限去比），
      结论会**系统性偏保守**，看起来"更严谨"，实际是把可判定的改进判成不可判定。
    """
    xs = [o for o in obs]
    f, _ = floor(xs)
    if f is None:
        return False, "先要有一个合法的聚合下限"
    tf = floor_task(xs)
    worst = max(tf.values()) if tf else 0.0
    return worst >= f - _TOL, (f"逐题最大 {worst:.4f} vs 聚合 {f:.4f}"
                               f"（差 {worst - f:+.4f}，一题涨一题跌会被抵消）")


def bracket(obs: Iterable[Obs], *, min_runs: int = MIN_RUNS) -> dict[str, Any]:
    """一次运行池的完整读数：轮内 / 轮间 / 逐题 / 结论。

    `underestimates` 是本模块存在的理由：只有当轮间极差 > 轮内极差时，
    才说明"用轮内当噪声下限会低估"。轮内大的时候它反而不重要 ——
    但那时噪声下限也不会是 0，所以旧做法不会造成假显著。
    """
    xs = [o for o in obs]
    agg, why = floor(xs, min_runs=min_runs)
    n, _ = independence(xs, min_runs=min_runs)
    intra = max((o.intra for o in xs), default=0.0)
    tf = floor_task(xs) if agg is not None else {}
    return {
        "n_runs": n,
        "floor_agg": agg,
        "why": why,
        "intra_max": intra,
        "floor_task": tf,
        "floor_task_max": max(tf.values()) if tf else None,
        "underestimates": (agg is not None and agg > intra + _TOL),
        "means": [o.mean for o in xs],
        "repeat": sorted({o.repeat for o in xs}),
    }


def report(br: Mapping[str, Any], *, label: str = "") -> list[str]:
    """人读的一屏。数字全部来自 `bracket`，这里不再算一遍。"""
    out = [f"  臂：{label}  独立运行 {br['n_runs']} 次  内部 repeat={br['repeat']}"]
    out.append("  各次聚合：" + "  ".join(f"{m:.4f}" for m in br["means"]))
    if br["floor_agg"] is None:
        out.append(f"  ⛔ 没有噪声下限：{br['why']}")
        out.append("     ⇒ 任何 Δ 都不可判定（上层应判 UNKNOWN，不默认 0）")
        return out
    out.append(f"  轮间极差（**噪声下限**）= {br['floor_agg']:.4f}")
    out.append(f"  轮内极差（仅一致性）      = {br['intra_max']:.4f}")
    if br["underestimates"]:
        out.append(f"  ⚠️ 轮内 < 轮间 ⇒ 拿轮内当噪声下限会**低估** "
                   f"{br['floor_agg'] - br['intra_max']:.4f}，"
                   f"进而把噪声读成改进")
    else:
        out.append("  ⇒ 轮内 ≥ 轮间：这一池里旧口径没有低估（不代表下次不会）")
    if br["floor_task"]:
        hot = sorted(br["floor_task"].items(), key=lambda kv: -kv[1])
        out.append("  逐题极差：" + "  ".join(f"{k}={v:.4f}" for k, v in hot))
        top = hot[0][1]
        tied = [k for k, v in hot if v == top]
        # 全 0 时不许点名：`sorted` 只是按名字兜底，它不是"sg01 最不稳"的证据。
        # 并列时同理 —— 这句话一写错，读者会把 0 读成"某一题在抖"。
        if top == 0.0 and len(tied) == len(hot):
            out.append(f"  ⇒ {len(hot)} 题逐题极差全为 0：没有「最不稳」的一题；"
                       f"聚合 {br['floor_agg']:.4f} 是真 0，不是抵消出来的")
        elif len(tied) > 1:
            out.append(f"  ⇒ 并列最不稳：{','.join(tied)}（各 {top:.4f}）；"
                       f"聚合 {br['floor_agg']:.4f} 比它小是抵消，不是稳定")
        else:
            out.append(f"  ⇒ 最不稳的是 {hot[0][0]}（{top:.4f}）；"
                       f"聚合 {br['floor_agg']:.4f} 比它小是抵消，不是稳定")
    return out


def noise_for_verdict(obs: Iterable[Obs], *, min_runs: int = MIN_RUNS) -> float | None:
    """**唯一**允许传给 `mutation_verdict(noise_floor=...)` 的东西。

    写成函数而不是让调用方自己挑数字，是为了让"喂错输入"变成一个
    可以被断言抓住的事件：旧做法（`max(intra spread)`）在这里拿不到值。
    """
    return floor(obs, min_runs=min_runs)[0]


# ===========================================================================
# 检测器自带反证（纪律 ⑩）
#
# 一个只会输出数字的估计器，和一个恒等于 0 的估计器，在**下游**看起来一样：
# 都给出一个数，都让 `mutation_verdict` 说话。区别只在拿坏样本喂它时。
# 所以四条纪律各配一个坏样本，**就地**造、跑完即弃。
#
# 放在本文件而不是探针里：契约测试必须能在**没有探针**时独立成立
# （两处各写一份坏样本，迟早会长成两份判据）。
# ===========================================================================
def _synth(means: Sequence[float], *, run_ids: Sequence[str] | None = None,
           sig: str = "sig1", arm: str = "臂", tasks: Sequence[str] = ("t1", "t2"),
           per_task: Sequence[Sequence[float]] | None = None,
           reps: Sequence[Sequence[float]] | None = None) -> list[Obs]:
    """造一池假读数。默认每道题都等于聚合分（此时逐题极差 == 聚合极差）。"""
    ids = list(run_ids) if run_ids is not None else [f"pid{i}" for i in range(len(means))]
    out: list[Obs] = []
    for i, m in enumerate(means):
        row = (tuple(per_task[i]) if per_task is not None
               else tuple(m for _ in tasks))
        out.append(Obs(arm=arm, sig=sig, run_id=ids[i], mean=float(m),
                       repeat=len(reps[i]) if reps is not None else 1,
                       per_task=tuple(zip(tasks, row)),
                       rep_scores=tuple(reps[i]) if reps is not None else (float(m),)))
    return out


def reverse_proofs() -> dict[str, Any]:
    """四条纪律 + 两个方向的坏样本。返回值全部可被断言（不打印）。"""
    out: dict[str, Any] = {}

    # ① 五次"运行"共用同一个 run_id = 一次运行内部 repeat 了 5 次
    #    ⛔ 这是本轮要消灭的那个错误本身：它在**数据里**看起来像 5 次运行。
    same = _synth([0.0, 0.2, 0.4, 0.6, 0.8], run_ids=["pid1"] * 5)
    out["同一次运行五次重复"] = floor(same)

    # ② 4 次独立运行 ⇒ 不到下限 ⇒ 拒判（不是"噪声是 0"）
    few = _synth([0.0, 0.4, 0.4, 0.8], run_ids=[f"p{i}" for i in range(4)])
    out["只有四次运行"] = floor(few)

    # ③ 混口径：两条记录 sig 不同 ⇒ 拒判（跨口径的极差没有意义）
    mixed = _synth([0.0, 0.5, 0.5, 0.5, 1.0], run_ids=[f"p{i}" for i in range(5)])
    mixed = [replace(o, sig="sig2") for o in mixed]
    mixed[0] = replace(mixed[0], sig="sig1")
    out["混口径"] = floor(mixed)

    # ④ 旧记录（没有 run_id）⇒ 抛错，而不是被静默跳过
    try:
        from_record({"solver": "x", "caliber_sig": "s", "measurement": {"mean": 1.0}})
        out["缺 run_id"] = "❌ 没抛错（静默收下了不可判的记录）"
    except ValueError as e:
        out["缺 run_id"] = f"ValueError: {e}"

    # ⑤ 逐题 vs 聚合：拿**最小**的逐题极差当噪声下限会低估。
    #    构造：两题各 3 次运行，t1 稳定、t2 大幅摆动 ⇒ 聚合被 t1 拖住一半。
    matrix = [(1.0, 1.0), (1.0, 0.0), (1.0, 1.0), (1.0, 0.0), (1.0, 0.0)]
    canc = _synth([sum(r) / 2 for r in matrix], per_task=matrix,
                  run_ids=[f"q{i}" for i in range(5)])
    tf = floor_task(canc)
    agg, _ = floor(canc)
    out["逐题最小当聚合下限"] = (min(tf.values()), agg)          # min < agg ⇒ 会低估
    out["逐题最大当聚合下限"] = (max(tf.values()), agg)          # max ≥ agg ⇒ 保守但不错
    out["不变量"] = invariant(canc)

    # ⑥ 轮内一致但轮间摆动：这就是 §35 实测的形态
    #    （repeat=2 两次分数相同 ⇒ 轮内极差 0，而下一次运行分数完全不同）
    reps = [(0.2857, 0.2857)] * 5
    out["轮内为 0"] = bracket(_synth([0.2857, 1.0, 0.2857, 0.2857, 0.2857],
                                     reps=reps, run_ids=[f"r{i}" for i in range(5)]))

    # ⑦ 同口径、不同臂 ⇒ 必须拒判。**这不是假想的坑**：
    #    本轮的裸模型与内核 `sig` **完全相同**（`Caliber` 里没有 solver），
    #    而两者均值差 0.3095。只按 sig 分组，会把"两个系统的差"读成"一个系统的抖动"。
    two_arms = (_synth([0.2857] * 5, run_ids=[f"d{i}" for i in range(5)], arm="直连")
                + _synth([0.5952] * 5, run_ids=[f"k{i}" for i in range(5)], arm="内核"))
    out["同口径不同臂"] = floor(two_arms)
    out["只按 sig 分组的假极差"] = max(o.mean for o in two_arms) - min(o.mean for o in two_arms)
    return out

