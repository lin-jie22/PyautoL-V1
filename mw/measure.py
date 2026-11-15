"""测量基座 —— V1 存在的**第一理由**。

对既有自演化系统的对抗性审查给出的判决是：系统"不具备声称我变好了的分辨力"。
根因不是某个 bug，是**测量从未被当成一等公民**：
    · 同一配置重复跑 3 次，分数极差 0.0862 —— 而所有"改进"都小于它
    · 冻结基线恰好落在噪声带中央
    · 唯一的数字 0.7184 五天没动，而每轮都在交付"全绿"

更要命的是 2026 的推理期理论给这件事加了一条当时未知的约束：
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
       而这一类读数正是过去从未有过的（过去只有一个恒为 0 的适应度数字）。
"""

from __future__ import annotations

import math
import re
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


#: 汇总行的**唯一可解析前缀**。只由 `Report.verdict_line()` 产生。
#:
#: ⛔ 为什么必须有它 —— 这是第 140 轮从一次真实的假绿里挖出来的债：
#:
#:     ✅ H8 汇总行可被外部 runner 正则解析（§134/§135 的坑）  (PASS=7  FAIL=0)
#:
#: 这条断言的**名字**是「汇总行能被正则解析」，而它的 **detail 文本**
#: 恰好就是让那个正则解析出错的字符串。于是 `grep -oE 'PASS=\d+  FAIL=\d+' | tail -1`
#: 在**崩溃**的日志上返回 `PASS=7  FAIL=0`（= 绿），而那次运行 `EXIT=1`、
#: 主汇总行从未打印。
#:
#: 自指陷阱：用来证明「能被解析」的那句话，本身是不可解析的污染源。
#: 根因不是 grep，是本模块产出的读数**与普通文本同形**。
#: 修法是给判据一个普通文本不可能冒充的前缀。
SENTINEL = "::VERDICT::"

#: 三值。⛔ 第四档 `UNKNOWN` 刻意不存在：判据态要么有汇总行、要么没有，
#: 没有「可能跑完了」这种中间状态 —— 加第四档只会给含糊留后门。
VERDICT_STATES = ("GREEN", "RED", "CRASH")


@dataclass
class Report:
    rows: list[Measurement] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    pass_n: int = 0
    fail_n: int = 0
    #: 「我跑完了」——**默认 False，即默认没跑完**。
    #:
    #: ⛔ 方向是刻意反过来的。旧代码里「跑完」是默认状态（没人声明它也是跑完），
    #: 于是任何一次中途崩溃都不改变任何字段 —— 崩与跑完在数据上不可区分。
    #: 翻过来之后，忘记调 `finish()` 的代价是「被判成崩」，而不是「被判成通过」。
    #: 这是 fail-closed 在**一个布尔字段**上的最小实现。
    finished: bool = False

    def finish(self) -> None:
        """显式声明「我跑完了」。没有这一步，`verdict()` 永远是 CRASH。"""
        self.finished = True

    def verdict(self) -> str:
        """判据态：GREEN / RED / CRASH。**崩优先于红**。

        为什么崩优先：崩的时候判据**没说话**，是解释器在说话。
        把「跑到一半抛异常」记成「有失败断言」，等于说「我知道错了」，
        而实际上系统只是死了 —— 它连自己错在哪都不知道。
        """
        if not self.finished:
            return "CRASH"
        return "RED" if self.fail_n else "GREEN"

    def verdict_line(self) -> str:
        """**唯一**可被程序解析的判据行。三值 + 两个计数，一行给全。

        ⛔ 只有这一处产生 `SENTINEL`。断言 detail、日志正文、错误信息里
        一律**不许**出现它 —— 一旦出现，它就不再是唯一可解析的行。
        （`verdict_from` 遇到多行会判 CRASH，就是为了让这种污染立刻暴露。）
        """
        return (f"{SENTINEL} {self.verdict()}  "
                f"PASS={self.pass_n}  FAIL={self.fail_n}")

    def summary_line(self) -> str:
        """人读的汇总行。**⛔ 不是判据** —— 它没有 `SENTINEL`，不可作为判读依据。

        ⚠️ 保留原格式（`PASS=n  FAIL=n`）是因为它与外部 runner 的正则、
        契约 H8、以及多处文档互锁。改格式的收益抵不上改动面，
        所以正确做法是**另给一行**（`verdict_line`），而不是改这一行。

        连续两轮踩过这个坑：写成 `SUM_PASS=1 SUM_FAIL=0`
        时 `FAIL` 前面是下划线不是空白 ⇒ 套件判绿但断言数记 0（违反 R79）。
        这里用空格分隔，并把这个坑写进注释。
        """
        return f"PASS={self.pass_n}  FAIL={self.fail_n}"


def repeat(fn: Callable[[], float], n: int = 5) -> list[float]:
    """跑 N 次取分数。N 默认 5 —— 实测教训是 3 次不够。
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
       不该由测量层替它拍板 —— 否则测量层会偷偷改写被测量的对象（老病：
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
    """断言记账。所有断言数都要能被 Report 对账。"""
    if cond:
        rs.pass_n += 1
        print(f"  ✅ {name}" + (f"  ({detail})" if detail else ""))
    else:
        rs.fail_n += 1
        print(f"  ❌ {name}" + (f"  ({detail})" if detail else ""))
    return cond


def verdict_from(text: str, exit_code: int = 0) -> tuple[str, str]:
    """从**一段运行日志 + 退出码**判读判据态。返回 `(状态, 理由)`。

    这是本模块唯一一个「读别人的输出」的函数 —— 也是「自省」这件事
    在内核里的最小可执行形态：**系统读自己的账本，判断这次判据有没有效。**

    ⛔ 规则顺序即语义，不许重排：

      1. 没有 `SENTINEL` 行        ⇒ CRASH（汇总行缺席）
      2. 有多于 1 行 `SENTINEL`    ⇒ CRASH（不知道哪行是真的）
      3. 有 1 行但解析不出计数      ⇒ CRASH（格式退化）
      4. FAIL > 0                  ⇒ RED
      5. FAIL == 0 而 exit_code≠0  ⇒ CRASH（打印汇总后进程仍崩了）
      6. 其余                       ⇒ GREEN

    规则 1 是纪律 8「崩溃冒充跑完」的**可执行版本**：
    **缺席不许冒充零。** 一段没有任何判据行的日志，既不是「零失败」，
    也不是「通过了」—— 它是「判据没说话」。

    ⛔ 反过来说：文本里出现 `PASS=631  FAIL=0` **不构成任何证据**，
    因为断言 detail 里可以复制这几个字（真实发生过，见 `SENTINEL` 的注释）。
    本函数**只看带前缀的那一行**。

    ⚠️ 规则 5 防的是另一种崩：汇总行打完了、进程还在收尾时抛异常。
    只靠规则 1 抓不到它，只靠退出码又会把「有失败断言」和「崩」混成一件事。
    """
    lines = [ln for ln in str(text or "").splitlines() if SENTINEL in ln]
    if not lines:
        return "CRASH", ("日志里没有汇总行（判据缺席）"
                         " ⇒ 不判通过，也不判失败 —— 判据没说话")
    if len(lines) > 1:
        return "CRASH", (f"汇总行不唯一（{len(lines)} 行）"
                         f" ⇒ 有东西在冒充判据，判据已不可解析")
    m = re.search(r"PASS\s*=\s*(\d+)\s+FAIL\s*=\s*(\d+)", lines[0])
    if not m:
        return "CRASH", "汇总行存在但解析不出 PASS/FAIL ⇒ 格式退化"
    n_pass, n_fail = int(m.group(1)), int(m.group(2))
    if n_fail:
        return "RED", f"{n_fail} 条断言失败（通过 {n_pass} 条）"
    if exit_code:
        return "CRASH", (f"汇总行写 PASS={n_pass} FAIL=0，但进程退出码 {exit_code}"
                         f" ⇒ 打印之后仍崩了（判据失效，不许判通过）")
    return "GREEN", f"全部 {n_pass} 条断言通过，退出码 0"


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
