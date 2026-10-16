"""测量基座 —— V1 存在的**第一理由**。

V5 的对抗性审查给出的判决是：系统"不具备声称我变好了的分辨力"。
根因不是某个 bug，是**测量从未被当成一等公民**：
    · 同一配置重复跑 3 次，分数极差 0.0862 —— 而所有"改进"都小于它
    · 冻结基线恰好落在噪声带中央
    · 唯一的数字 0.7184 五天没动，而每轮都在交付"全绿"

更要命的是 2026 的推理期理论给这件事加了一条 V5 当时不知道的约束：
    UK AISI 2026-07 / arXiv:2606.17930：
    能力是 test-time compute 上的**曲线**，固定预算评测给的是**下界不是上界**。
⇒ 所以"改进"这个词必须同时绑定 (候选, 预算档)。拿 4x 预算的新方案
  去比 1x 预算的旧方案，得到的差值里混着预算，是假信号。

因此本模块只提供这几样东西，多一样都不加：
    1. repeat()        —— 同一候选同一预算跑 N 次，拿到分布而不是一个数
    2. noise_floor()   —— 噪声下限（这里是极差 max-min，最保守的口径）
    3. verdict()       —— 判定"是否真的变好"：必须**显著高于噪声下限**才算

▶ 2026-10-17 加了第 4 样，这是**唯一一次**扩这个模块，理由写在函数里：
    4. abstraction_collapse() —— 记忆层"有没有抽象"的可判定读数。
       它不属于上面三样：那三样都在问**同一个问题**（"这个候选更好吗"），
       而它问的是另一个问题 —— "系统有没有把具体实例归到同一个概念上"。
       为什么它必须和 1–3 放在一起：它们都是"**能力在不在长**"的读数，
       而这一类读数正是 V5 从来没有过的（它只有一个恒为 0 的适应度数字）。
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Callable, Sequence


@dataclass(frozen=True)
class Measurement:
    """一个候选在**一个预算档**下的完整读数。字段不可省 —— 少一个就不可对账。"""

    candidate: str
    budget_tag: str
    n: int
    mean: float
    std: float
    lo: float
    hi: float

    @property
    def spread(self) -> float:
        """极差。这是噪声下限的最保守估计（不假设正态）。"""
        return self.hi - self.lo

    def as_line(self) -> str:
        return (f"{self.candidate}@{self.budget_tag} n={self.n} "
                f"mean={self.mean:.4f} std={self.std:.4f} "
                f"[{self.lo:.4f},{self.hi:.4f}] 极差={self.spread:.4f}")


@dataclass
class Report:
    rows: list[Measurement] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    pass_n: int = 0
    fail_n: int = 0

    def summary_line(self) -> str:
        """⚠️ 格式**必须**被外部 runner 的正则 `PASS\\s*=\\s*(\\d+)\\s+FAIL\\s*=\\s*(\\d+)` 命中。

        V5 的 §134/§135 连续两轮踩了这个坑：写成 `SUM_PASS=1 SUM_FAIL=0`
        时 `FAIL` 前面是下划线不是空白 ⇒ 套件判绿但断言数记 0（违反 R79）。
        这里用空格分隔，并把这个坑写进注释。
        """
        return f"PASS={self.pass_n}  FAIL={self.fail_n}"


def repeat(fn: Callable[[], float], n: int = 5) -> list[float]:
    """跑 N 次取分数。N 默认 5 —— V5 的教训是 3 次不够（极差 0.086）。
    费用是 N 倍，这是**必要成本**：没有它，一切"改进"都无法与噪声区分。
    """
    if n < 2:
        raise ValueError("n 必须 ≥2，单次测量无法给出噪声估计")
    return [float(fn()) for _ in range(n)]


def summarize(scores: Sequence[float], *, candidate: str, budget_tag: str) -> Measurement:
    if not scores:
        raise ValueError("空读数")
    xs = list(scores)
    return Measurement(
        candidate=candidate,
        budget_tag=budget_tag,
        n=len(xs),
        mean=statistics.fmean(xs),
        std=statistics.pstdev(xs) if len(xs) > 1 else 0.0,
        lo=min(xs),
        hi=max(xs),
    )


def noise_floor(*measurements: Measurement) -> float:
    """噪声下限 = 所有已知重复测量里最大的极差。

    口径保守（max 而不是 mean）：宁可要求更大的增益，也不要放过噪声。
    参数可以只传基线那一组 —— 基线自身的抖动就是下限。
    """
    spreads = [m.spread for m in measurements if m.n >= 2]
    return max(spreads) if spreads else 0.0


def paired_compare(
    base: Measurement, cand: Measurement, *, min_gain: float = 0.0
) -> tuple[bool, str]:
    """配对判定：候选是不是**真的**比基线好。

    三条同时满足才算数（缺一不可）：
      1. 同预算档 —— 否则比的不是同一把尺子（2026 推理期理论的硬约束）
      2. 候选下界 > 基线均值 + 噪声下限 —— 要求"最差的一次"也赢过基线的抖动带
      3. 增益 ≥ min_gain（默认 0，即不额外加主观门槛）
    """
    if base.budget_tag != cand.budget_tag:
        return False, (f"预算档不同（{base.budget_tag} vs {cand.budget_tag}）"
                       f" ⇒ 不可比，需在同一档重测")
    noise = noise_floor(base, cand)
    required = base.mean + noise + min_gain
    if cand.lo <= required:
        return False, (f"不成立：候选最差 {cand.lo:.4f} ≤ 基线 {base.mean:.4f} "
                       f"+ 噪声 {noise:.4f} = {required:.4f} ⇒ 落在噪声带内")
    return True, (f"成立：候选最差 {cand.lo:.4f} > 门槛 {required:.4f}"
                  f"（增益 {cand.mean - base.mean:+.4f}，噪声下限 {noise:.4f}）")


def abstraction_collapse(pairs: Sequence[tuple[str, str]], *,
                         key_of: Callable[[str], str]) -> float:
    """「抽象」的可判定读数 —— 语义等价、**字面不同**的事实被归到同一 key 的比例。

    为什么这是抽象的正确判据：抽象的实质就是**丢掉字面、保留结构**。
    若两条表达同一个意思的话被算成两条记忆，那系统里**没有概念，只有字面**。

    返回 0.0~1.0：
        0.0 = 完全按字面走（没有抽象层）
        1.0 = 所有等价表述都收敛到同一个 key

    ⛔ 它需要调用方传入 `key_of`：因为"什么算等价"的定义属于**被测量的系统**，
       不该由测量层替它拍板 —— 否则测量层会偷偷改写被测量的对象（V5 的老病：
       尺子自己变，读数跟着变，而没人察觉）。
       本函数只保证**算法正确**，不保证**口径合理**；口径由调用方负责并被读出来。

    ⛔ 它也**不判断多少算好**：0.0 不是"错"，是"当前没有这一层"。见 DESIGN §14.4。
    """
    ps = list(pairs)
    if not ps:
        return 1.0      # 没有反例 ⇒ 不诬告（"没测到" ≠ "做不到"）
    return sum(1 for a, b in ps if key_of(a) == key_of(b)) / len(ps)


def budget_curve(measurements: Sequence[Measurement]) -> str:
    """把一个候选在多个预算档上的读数排成曲线。

    这是 2026 理论的直接产物：**不要用一个数描述一个候选**，
    要给出「能力 vs 预算」的形状。曲线平 ⇒ 加预算没用，该换方法而不是加钱。
    """
    by_cand: dict[str, list[Measurement]] = {}
    for m in measurements:
        by_cand.setdefault(m.candidate, []).append(m)
    lines = []
    for cand, ms in sorted(by_cand.items()):
        ms = sorted(ms, key=lambda m: m.mean)
        pts = " → ".join(f"{m.budget_tag}:{m.mean:.4f}" for m in ms)
        mono = all(ms[i].mean <= ms[i + 1].mean for i in range(len(ms) - 1))
        slope = (ms[-1].mean - ms[0].mean) if len(ms) > 1 else 0.0
        lines.append(f"{cand}: {pts}   单调={mono} 跨度={slope:+.4f}")
    return "\n".join(lines) if lines else "(无读数)"


def check(rs: Report, name: str, cond: bool, detail: str = "") -> bool:
    """断言记账。所有断言数都要能被 Report 对账（V5 的 R79）。"""
    if cond:
        rs.pass_n += 1
        print(f"  ✅ {name}" + (f"  ({detail})" if detail else ""))
    else:
        rs.fail_n += 1
        print(f"  ❌ {name}" + (f"  ({detail})" if detail else ""))
    return cond


def has_real_gain(base: Measurement, cand: Measurement) -> bool:
    """给外部调用的一句话版判定。"""
    ok, _ = paired_compare(base, cand)
    return ok


def ci95(scores: Sequence[float]) -> tuple[float, float]:
    """均值 ± 1.96·SEM。样本量小时会严重低估不确定性，所以 verdict 用的是
    **极差**而不是这个 —— 这里只作为参考读数输出，不参与判定。
    """
    if len(scores) < 2:
        return (float(scores[0]), float(scores[0])) if scores else (0.0, 0.0)
    m = statistics.fmean(scores)
    sem = statistics.stdev(scores) / math.sqrt(len(scores))
    return (m - 1.96 * sem, m + 1.96 * sem)
