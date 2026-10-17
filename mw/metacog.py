"""自我模型 —— 我知道自己**在哪些事上**做对过、在哪些事上错过。

## 为什么它不能是"自评分"

V5 的死因用一句话说就是：**系统自己给自己打分，然后把这个分当成"我变好了"。**
它的适应度函数恒等于 0（`bench_gain.jsonl` 54/54 `gain=0.0`），
而每一轮都交付"全绿"——因为那个"全绿"是它自己说的。

所以本模块的**接口形状**就是为了杜绝这件事：

    self_model.advise(family="numeric_logic")     ✅ 只收任务族标签
    self_model.advise(goal="把 [3,1,2] 排序")      ❌ 物理上不存在这个参数

它拿不到当前任务的任何内容，也就无法"先看一眼题再吹自己会做"。
它唯一能读的是**已经答完的题**的外部读数（`bench` 产出的记录）——
那些分数来自独立子进程里的打分器，内核改不动它。

## 它到底回答什么问题

不是"我聪明吗"（无法证伪），而是四个**可操作**的问题：

    1. 这个族我历史上通过率多少？        → 决定要不要多花候选预算
    2. 这个族的失败主要发生在哪一步？    → 决定要不要先做小验证
    3. 我的样本够不够支撑上面两条？      → 不够就说"不知道"，不装懂
    4. 「分族」这个划分本身有没有预测力？ → 没有 ⇒ 前三条一律不许用

第 3 条曾经是这套东西能不能信的全部，但它**漏了一层**：样本够，不等于
样本里的规律是真的。第 4 条是被一次实测逼出来的 ——

    真模型账本（qwen2.5:7b / T=0.8 / 4 会话 × 2 族 × 40 行）：
        自我模型预测 MAE 0.410   常数基线 MAE 0.395   Δ = −0.015
        text_ops 跨会话通过率 1.00 → 1.00 → 0.80 → 0.40

    样本是够的（历史 20 / 未来 20），结论却是**负**的：族内漂移大于族间
    差异。此时"族 numeric_logic 历史通过率 0.85"这句话字字属实，
    而基于它去加宽候选预算，是在**把噪声写成规律**再照着噪声行动。

⇒ 所以 `advise()` 多了一道闸：先测「分族有没有预测力」，测不出来
（UNKNOWN）或测出来没有（ABSENT），就**不许 confident**。
⚠️ 注意两个层面的分工，不许混读：
    · 结论层面：UNKNOWN **不并入** ABSENT（没量到 ≠ 没有，§16.7 第二层判据）
    · 行动层面：**两者都 fail-closed**（没量到就不许据此行动）
"""

from __future__ import annotations

import hashlib
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

#: 少于这个样本数，"这个族的通过率"不构成证据。
#: 3 的依据：2 次里 1 次成功，通过率 0.5 与 0.0、1.0 都只差一个样本，
#: 任何基于它的资源调整都是在放大噪声（V5 的教训：极差 0.0862 盖过一切"改进"）。
MIN_SAMPLES = 3

#: ── 「分族有没有预测力」这道闸的四个常数 ──────────────────────────────
#: ⚠️ 它们**不是**可调旋钮，是"什么时候算测到了"的声明。理由逐条写在下面，
#:    因为调这四个数就能把结论调好看 —— 那正是 V5 的病（尺子随退化下移）。
#:    契约 AJ11 用 AST 钉死：本模块不许有读取环境变量 / 形参默认值以外的入口。

#: 时序切分至少要这么多会话。2 ⇒ 只能切出 1 历史 + 1 未来，
#: 而"用 1 个会话预测下一个会话"的方差大到没有分辨力。
MIN_POWER_SESSIONS = 4

#: 未来集至少要这么多行。少于 4 条，MAE 的每一个样本都带着 ±0.25 的抖动，
#: Δ 的正负基本由运气决定（真实账本 4 会话 × 5 题的情形刚好卡在这条线上）。
MIN_FUTURE_ROWS = 4

#: 判定阈值的**下限**。当反证组的噪声恰好接近 0 时，不许拿 0 当阈值 ——
#: 那会让任何一丁点正数都算"有预测力"。0.02 的量级来自：一次二值预测
#: 最多能改善的 MAE 是 0.5，0.02 是它的 4%，远低于任何真信号的量级，
#: 但足以挡住"四舍五入级别的优势"。
POWER_MARGIN_FLOOR = 0.02

#: 历史占会话数的比例。0.6 的理由：要保证未来集不为空（见 `measure_family_power`
#: 里的兜底），同时历史要占多数 —— 用少数预测多数是外推，反过来是插值。
POWER_HISTORY_RATIO = 0.6

#: 置换检验的次数。**512，由实测定，不是拍的。**
#:
#: ⚠️ 上一版写的是 8，注释还写着"超过 8 次收益递减"—— 那句话被同一份
#: 真实账本**直接推翻**（本轮实测，见 DESIGN §16.12）：
#:
#:     4 会话   n=8 σ=0.0217 │ n=512 σ=0.0257 │ n=2048 σ=0.0264
#:     6 会话   n=8 σ=0.0279 │ n=512 σ=0.0223 │ n=2048 σ=0.0215
#:
#: 8 次时两行的大小关系是**反的**（4 会话的 σ 被估得比 6 会话还小），
#: 512 次才回到正确的方向（样本越多噪声越小）。⇒ 8 个样本估出来的 σ，
#: 它自己的抽样误差与被它拿来当阈值的信号同量级 —— 判据在掷骰子。
#:
#: 512 的代价是 0.2~0.5 秒（历史行数几十条的量级），对一个跨会话读数来说
#: 完全无所谓。取 512 而不是 2048：512 与 2048 的差已 <0.001，
#: 再往上只会让每次判定多花几秒，没有信息增量。
N_CONTROL_PERMS = 512


@dataclass(frozen=True)
class Advice:
    """资源分配建议。**每条都必须带 `basis`**，否则无从复核它凭什么这么说。

    `confident` 是消费方的开关：**只有它为 True 时，建议才允许覆盖内核的默认判断**。
    这个字段的存在理由是一次结构性的风险 —— 一个"没有证据但语气笃定"的自我模型
    会把 `looks_complex()` 的判定整体抹掉，而且日志上看不出来
    （`basis` 里写着"样本不足"，但决策已经变了）。所以把"能不能覆盖"
    做成一个显式布尔，而不是让消费方去比较字符串。
    """
    candidates: int
    deep: bool
    verify_first: bool
    basis: str
    confident: bool = False

    def as_line(self) -> str:
        return (f"[self] 候选={self.candidates} 深通道={'是' if self.deep else '否'}"
                f" 先验证={'是' if self.verify_first else '否'} — {self.basis}")


DEFAULT = Advice(1, False, False, "无证据 ⇒ 用默认值（不装懂）")


@dataclass(frozen=True)
class FamilyPower:
    """「分族」这个划分在**这份账本**上有没有预测力的读数。

    三态与 §16.7 第二层判据一致，且 `UNKNOWN` **不许并入** `ABSENT`：
    没量到（会话不够 / 未来集太小）与量到了但没有，是两件事。

    ⚠️ 但 `usable` 对两者都返回 False —— 那是**行动层面**的 fail-closed：
    不知道就不许动。结论层面与行动层面故意分开，是因为把它们合成一个
    布尔会让"没量到"在系统里消失（退化成"没有"），而"没有"又会反过来
    被当成"所以不用量了"。
    """

    state: str                      # PRESENT / ABSENT / UNKNOWN
    delta: float | None             # 基线 MAE − 自我模型 MAE（正数 = 有优势）
    mae_model: float | None
    mae_base: float | None
    noise_floor: float              # 反证组（置换检验）Δ 的**标准差**
    noise_mean: float               # 反证组 |Δ| 的**均值**（防置换退化成确定性操作）
    margin: float                   # 判定阈值 = max(下限, 2×std, mean|Δ|)
    n_history: int
    n_future: int
    n_sessions: int
    #: 反证组的个数（= 族数 − 1）。它决定这个阈值有多脆：
    #: 1 个反证组 ⇒ 阈值是一次抽样的结果，换一次数据就变。
    n_controls: int = 0
    reason: str = ""

    @property
    def usable(self) -> bool:
        """**只有 PRESENT 才允许据此行动** —— 这是 `advise()` 的闸。"""
        return self.state == "PRESENT"

    @property
    def snr(self) -> float | None:
        """信噪比 = Δ / 阈值。**> 1 才判 PRESENT**，但 1.0 与 3.0 不是一回事。

        为什么必须进读数：只念"闸开了"会丢掉最关键的信息 —— **开得多稳**。
        实测里这个数是 1.058（8 会话真模型）：刚过线，换一批数据就可能翻回去。
        一个把 1.05 和 3.0 都显示成"闸=开"的读数，等于把临界结论说成稳固结论。
        """
        if self.delta is None or not self.margin:
            return None
        return round(self.delta / self.margin, 3)

    def as_line(self) -> str:
        if self.delta is None:
            return f"[power] {self.state} — {self.reason or '无法测量'}"
        return (f"[power] Δ={self.delta:+.4f}（模型 {self.mae_model} vs 基线 "
                f"{self.mae_base}）阈值={self.margin} 噪声(std={self.noise_floor}"
                f"/mean={self.noise_mean}，{self.n_controls} 个反证组）"
                f" snr={self.snr} ⇒ {self.state}")

    def as_dict(self) -> dict:
        return {"state": self.state, "delta": self.delta,
                "mae_model": self.mae_model, "mae_base": self.mae_base,
                "noise_floor": self.noise_floor, "noise_mean": self.noise_mean,
                "margin": self.margin,
                "n_history": self.n_history, "n_future": self.n_future,
                "n_sessions": self.n_sessions, "n_controls": self.n_controls,
                "usable": self.usable, "snr": self.snr, "reason": self.reason}


class SelfModel:
    """从**已完成**任务的读数里，估自己的能力边界。

    `evidence` 每条形如：
        {"family": "numeric_logic", "passed": True, "score": 1.0,
         "failed_step": "plan"  # 可选：失败发生在哪个相位
        }

    ⚠️ 只有**外部**打分器给的分才算证据。内核自己在 `Outcome.status` 里
    写的 "done" 不算 —— 那是"我跑完了"，不是"我做对了"。这两个词
    在 V5 里被混成同一个，代价是 16 天。
    """

    def __init__(self, evidence: list[dict] | None = None) -> None:
        self.evidence: list[dict] = list(evidence or [])
        #: 被引用过的建议次数 —— 让"这套东西到底有没有影响决策"可读。
        #: 一个从未被采纳的自我模型，和不存在是一样的（装饰模块）。
        self.consulted = 0
        self.influenced = 0
        #: 族预测力的惰性缓存。计算它要重切会话、重建模型，不该每次顾问都做。
        self._power: FamilyPower | None = None

    # ------------------------------------------------------------------
    def add(self, *, family: str, passed: bool, score: float | None = None,
            failed_step: str = "", session: int | None = None) -> None:
        e: dict = {"family": family, "passed": bool(passed),
                   "score": score, "failed_step": failed_step}
        # `session` 是时序切分的唯一依据，缺了它这一行就进不了预测力的测量。
        # 刻意**不**用 0 填补：填 0 会让它假装成一个会话号（V5 的"把缺失当零"）。
        if session is not None:
            e["session"] = int(session)
        self.evidence.append(e)

    def profile(self) -> dict[str, dict]:
        """按族聚合。**每个族都带 n**，因为通过率离开样本量就没有意义。"""
        out: dict[str, dict] = {}
        for e in self.evidence:
            fam = str(e.get("family") or "unknown")
            d = out.setdefault(fam, {"n": 0, "passed": 0, "steps": {}})
            d["n"] += 1
            if e.get("passed"):
                d["passed"] += 1
            st = e.get("failed_step")
            if st:
                d["steps"][st] = d["steps"].get(st, 0) + 1
        for d in out.values():
            d["pass_rate"] = round(d["passed"] / d["n"], 3) if d["n"] else None
        return out

    # ------------------------------------------------------------------
    def global_rate(self) -> float | None:
        """**不区分能力族**的通过率。

        它有两个用处，缺一不可：
          ① 样本不足时 `predict()` 退回这里（"不知道就说不知道"的具体形态：
             说"平均而言"而不是说"这个族是 xx"）；
          ② 它是判据里的**对照基线预测器** —— 一个不区分族的常数预测器。
             自我模型的预测误差必须显著低于它，否则"分族"这件事
             只是在给噪声起名字（`bench/probe_selfmodel.py` 在测这个）。
        """
        if not self.evidence:
            return None
        hits = sum(1 for e in self.evidence if e.get("passed"))
        return round(hits / len(self.evidence), 3)

    def predict(self, family: str | None) -> float | None:
        """预测 `family` **下一次**的通过率。这是自我模型唯一的可证伪断言。

        `advise()` 说的是"该怎么分配资源"（一个决策，事后可以自圆其说），
        `predict()` 说的是"我下次会拿多少分"（一个数字，可以被外部分数打脸）。
        判据要的是后者：**只有能被打脸的断言才是断言。**

        ⚠️ 分族无预测力时退回**全局率**（`base`）而不是族率 ——
        这才是"我知道自己不知道"的具体形态：不预测具体族，
        只说"平均而言"。见模块头的第 4 条。
        """
        base = self.global_rate()
        if not family:
            return base
        p = self.profile().get(family)
        if not p or p["n"] < MIN_SAMPLES or p["pass_rate"] is None:
            return base          # 样本不足 ⇒ 退回全局率，不装懂
        if not self.family_power().usable:
            return base          # 分族没有预测力 ⇒ 族的历史率对未来没有信息
        return p["pass_rate"]

    # ------------------------------------------------------------------
    def family_power(self) -> FamilyPower:
        """「分族」在本账本上有没有预测力。惰性算一次，之后封板。

        与 `measure_family_power(self.evidence)` 等价，只是加了缓存。
        """
        if self._power is None:
            # 递归守卫：测量内部构造的临时模型也会问到自己，
            # 而它们的 `evidence` 只是切片 —— 用同一份实现，不特殊化。
            self._power = measure_family_power(self.evidence)
        return self._power

    # ------------------------------------------------------------------
    def advise(self, *, family: str | None, max_candidates: int = 3) -> Advice:
        """给出资源建议。**认不出族就不装懂**（`family=None` 返回默认值）。

        ⚠️ 参数表里没有 `goal` —— 这不是遗漏，是**接口级防线**：
        只要它读得到当前题目，就一定有人会去读，然后就变成自评分。

        ── 族预测力闸（本轮新增）─────────────────────────────────────
        样本够 ≠ 样本里的规律是真的。`confident=True` 意味着"这条建议
        允许覆盖内核的默认判断（`looks_complex`）"，所以它的门槛必须是
        **测到过预测力**，而不只是"看过几道题"。

        真实账本上这道闸会把自我模型整体关掉（Δ=−0.015 ⇒ ABSENT）。
        那不是退化，是**纠正**：在此之前它一直在用噪声给建议。
        """
        self.consulted += 1
        if not family:
            return DEFAULT
        p = self.profile().get(family)
        if not p or p["n"] < MIN_SAMPLES or p["pass_rate"] is None:
            n = p["n"] if p else 0
            return Advice(1, False, False,
                          f"族 {family} 样本 {n} < {MIN_SAMPLES} ⇒ 不足以判断")
        pw = self.family_power()
        if not pw.usable:
            # ⚠️ 这里**不**自增 `influenced`：闸关着的时候，自我模型没有
            #    影响任何决策。把它算进 influenced 就是在给一条从未生效的
            #    建议记功 —— 那正是旧判据（consulted × influenced）的病。
            return Advice(
                1, False, False,
                f"族 {family} 有 {p['n']} 个样本，但「分族」在本账本上"
                f"{'未测出' if pw.state == 'UNKNOWN' else '无'}预测力"
                f"（Δ={pw.delta if pw.delta is not None else 'n/a'} ≤ 阈值 "
                f"{pw.margin}，{pw.state}）⇒ 一律用默认值，不按族调整")
        rate = p["pass_rate"]
        # 弱族：加宽候选 + 先验证。强族：快通道单候选，省预算。
        # 这两条**都是可被后续读数推翻的**，不是"设置成看起来合理"。
        if rate < 0.5:
            steps = p["steps"]
            worst = max(steps, key=steps.get) if steps else ""
            advice = Advice(
                min(max_candidates, 3), True, True,
                f"族 {family} 历史通过率 {rate:.2f}（n={p['n']}）"
                + (f"，最常败在 {worst}" if worst else ""),
                confident=True)
            self.influenced += 1
            return advice
        if rate >= 0.8:
            self.influenced += 1
            return Advice(1, False, False,
                          f"族 {family} 历史通过率 {rate:.2f}（n={p['n']}）⇒ 走快通道",
                          confident=True)
        return Advice(1, False, False, f"族 {family} 历史通过率 {rate:.2f}（n={p['n']}）",
                      confident=True)

    # ------------------------------------------------------------------
    def report(self) -> dict:
        prof = self.profile()
        pw = self.family_power()
        eligible = [f for f, d in prof.items()
                    if d["n"] >= MIN_SAMPLES and d["pass_rate"] is not None]
        return {
            "families": len(prof),
            "samples": len(self.evidence),
            "global_rate": self.global_rate(),
            # 样本够格的族（**不考虑**预测力闸）—— 与下面那条分开，
            # 是因为"样本够但闸关着"和"样本不够"的修法完全不同：
            # 前者要换族的定义 / 多跑会话，后者只要继续跑。
            "eligible_families": eligible,
            # 真正**会给建议**的族。闸关着时它必然为空 —— 这个空不是缺陷，
            # 是自我模型在说"我现在分不出族"。
            "confident_families": eligible if pw.usable else [],
            # 闸本身的读数：Δ / 阈值 / 噪声下限 / 三态。**必须有消费者**
            # （core 汇总行 + 契约 AJ 组），否则它又是一个装饰字段。
            "family_power": pw.as_dict(),
            "consulted": self.consulted,
            "influenced": self.influenced,
        }


def load_evidence(rows: list[dict]) -> list[dict]:
    """从 `runs.jsonl` 这类落盘读数里挑出**可当自我模型证据**的行。

    只认同时具备 `family` 与 `passed` 的记录：缺 family 的行无法归族，
    缺 passed 的行是"我没记"，不是"我失败" —— 后者会被算成通过率里的 0，
    于是自我模型会凭空觉得"我很差"，进而做出错误的资源调整。
    **把缺失当零是这套系统里最隐蔽的一类谎。**
    """
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        fam = r.get("family")
        passed = r.get("passed")
        if not fam or passed is None:
            continue
        e: dict = {"family": str(fam), "passed": bool(passed),
                   "score": r.get("score"),
                   "failed_step": str(r.get("failed_step") or "")}
        # ⚠️ `session` **必须**带过去：它是时序切分的唯一依据，
        #    丢掉它 ⇒ 预测力永远测不出来 ⇒ 闸永远关着，而日志上看不出原因
        #    （会显示"样本够、Δ=n/a"）。这是本轮最容易被漏掉的一处。
        s = r.get("session")
        if s is not None:
            try:
                e["session"] = int(s)
            except (TypeError, ValueError):
                # 不是数字 ⇒ 当作"没有会话标记"。⛔ **不许**填 0：
                # 填 0 会让这一行假装属于会话 0，于是它被算进切分，
                # 而它其实没有时序信息（V5 的"把缺失当零"）。
                # ⚠️ 也不许静默丢：`session_bad` 是正向信号，契约 AJ8 盯着它。
                e["session_bad"] = str(s)
        out.append(e)
    return out


# ══════════════════════════════════════════════════════════════════════
# 「分族有没有预测力」的测量
#
# ⚠️ 这里是**唯一实现**。`bench/probe_selfmodel.py`（判据台）与
#    `SelfModel.advise()`（运行闸）共用它 —— 同一事实只声明一处，
#    否则闸用 A 算法、报告用 B 算法，两边漂一次就变成"闸关着但报告说成立"。
#    （V5 的经典病灶：同一件事两套实现，各自都对，合起来是谎。）
# ══════════════════════════════════════════════════════════════════════

def _rate_of(sm: SelfModel, family: str, base: float | None) -> float | None:
    """**未经闸**的族率查询 —— 测量专用。

    为什么测量里不能用 `sm.predict()`：predict 内部有闸，闸又会去测
    power，power 又要建临时模型 ⇒ 无限递归。更深层的理由是语义：
    我们要测的是「按族预测的原始能力」有多好，闸是**外加**在上面的，
    不能让被测对象自己决定自己被测成什么样。
    """
    p = sm.profile().get(family)
    if not p or p["n"] < MIN_SAMPLES or p["pass_rate"] is None:
        return base
    return p["pass_rate"]


def _predict_errors(history: Sequence[dict], future: Sequence[dict]) -> dict:
    """拿历史建模型、预测未来，返回两个预测器的 MAE。**不用随机切分**。

    时序切分是这条判据的命脉：随机切会把"预测未来"降级成"插值"，
    那是在给判据放水 —— 而放过水的判据，绿了也没有意义。
    """
    sm = SelfModel(list(history))
    base = sm.global_rate()
    if base is None:
        return {"n": 0, "mae_model": None, "mae_base": None, "delta": None}
    err_m: list[float] = []
    err_b: list[float] = []
    for r in future:
        y = 1.0 if r.get("passed") else 0.0
        pm = _rate_of(sm, str(r.get("family") or ""), base)
        if pm is None:
            continue
        err_m.append(abs(pm - y))
        err_b.append(abs(base - y))
    if not err_m:
        return {"n": 0, "mae_model": None, "mae_base": None, "delta": None}
    mae_m = sum(err_m) / len(err_m)
    mae_b = sum(err_b) / len(err_b)
    return {"n": len(err_m), "mae_model": round(mae_m, 4),
            "mae_base": round(mae_b, 4), "delta": round(mae_b - mae_m, 4)}


def deterministic_u01(seed: str) -> float:
    """确定性伪随机 [0,1)。**不用 `random`**：噪声下限必须逐次可复现，
    否则"这次判过了"和"这次阈值松了"会混成同一件事（V5 的老病）。

    `bench/probe_selfmodel.py` 造合成数据也用它 —— 同一个生成器，
    所以"校准材料"和"判据的零假设"不会各自漂移。
    """
    h = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") / float(1 << 64)


def _permuted_labels(history: Sequence[dict], seed: int) -> list[dict]:
    """**置换检验**：在**行层面**打乱族标签（各族样本数保持不变）。

    ⚠️ 打乱的是"哪一族"这一列，**不是**给族改名。这两件事差很远：
      · 给族改名（A→B, B→A）：每个族的通过率原封不动，只是名字换了 ⇒
        未来行若也跟着改名，Δ 一点不变；只改历史则恒等于"完全错配"。
      · 行级打乱：每个族都混进别族的样本 ⇒ 族率向全局率收缩 ⇒
        这才是"族标签与通过率无关"这个零假设的正确表达。

    第一版的教训（如实留档）：我用的是**轮转族名**，于是
      · 2 族时只有"恒等 / 交换"两种结果，噪声恒等于正组 Δ，
        ⇒ margin = 2Δ > Δ ⇒ **判据在数学上不可能成立**；
      · 5 族时噪声取决于族列表的排列顺序（字母序恰好把 0.90 与 0.10
        排成相邻 ⇒ 噪声 0.1233，是声明顺序 0.0592 的两倍），
        ⇒ 同一个真信号 Δ=+0.1724 被判成 ABSENT。
    """
    fams = [str(r.get("family") or "") for r in history]
    for i in range(len(fams) - 1, 0, -1):
        j = int(deterministic_u01(f"perm|{seed}|{i}") * (i + 1)) % (i + 1)
        fams[i], fams[j] = fams[j], fams[i]
    return [dict(r, family=fams[i]) for i, r in enumerate(history)]


def _noise_floor(history: Sequence[dict], future: Sequence[dict],
                 n_perm: int) -> tuple[float, float, int]:
    """噪声下限 = `n_perm` 次置换检验下 Δ 的 **标准差** 与 **|Δ| 均值**。

    ⚠️ 为什么两个都要，且取 `max`（不是相加、不是任选一个）：
      · `2 × std` 防的是**随机波动** —— 这是显著性判据 Δ > 2σ 的本体。
      · `mean(|Δ|)` 防的是**系统性残留**：若置换退化成确定性操作
        （例如旧实现的"族名轮转"，与随机数无关），8 次结果完全相同 ⇒
        std = 0 ⇒ 噪声下限凭空消失，而 mean(|Δ|) 仍然抓得住那一大坨偏差。
      · `FLOOR` 防的是两者**同时**≈0 时，把舍入级优势当成预测力。

    返回 `(std, mean_abs, n_controls)`。三个数都进读数：
    一个只报"阈值=0.03"的账本，你看不出这个阈值是稳的还是脆的。
    """
    deltas: list[float] = []
    for k in range(n_perm):
        c = _predict_errors(_permuted_labels(history, k), future)
        if c["delta"] is not None:
            deltas.append(c["delta"])
    if not deltas:
        return 0.0, 0.0, 0
    sd = statistics.pstdev(deltas) if len(deltas) >= 2 else 0.0
    mean_abs = sum(abs(d) for d in deltas) / len(deltas)
    return sd, mean_abs, len(deltas)


def _split_sessions(rows: Sequence[dict], *,
                    history_ratio: float = POWER_HISTORY_RATIO
                    ) -> tuple[list[dict], list[dict], list[int]]:
    """时序切分。**唯一实现**（闸与判据台共用，契约 AJ25 用 AST 钉死只有一处）。

    ⚠️ 为什么要抽出来：切分是"哪段是历史、哪段是未来"的唯一事实源。
    闸用一种切法、判据台用另一种切法 ⇒ 两边各测各的，报告说 PRESENT
    而闸关着 —— 又一处"同一事实两处声明"（V5 静默漂移最经典的入口）。

    返回 `(history, future, sessions)`。缺 `session` 或不是数字的行**整批**
    不参与：把它填成 0 会让所有无会话的行挤进"最老的一批"。
    """
    with_sess: list[dict] = []
    for r in rows:
        s = r.get("session")
        if s is None:
            continue
        try:
            int(s)
        except (TypeError, ValueError):
            continue
        with_sess.append(r)

    sessions = sorted({int(r["session"]) for r in with_sess})
    if not sessions:
        return [], [], []
    n_hist = max(1, int(len(sessions) * history_ratio))
    if n_hist >= len(sessions):              # 保证未来集不为空
        n_hist = len(sessions) - 1
    hist_s = set(sessions[:n_hist])
    hist = [r for r in with_sess if int(r["session"]) in hist_s]
    fut = [r for r in with_sess if int(r["session"]) not in hist_s]
    return hist, fut, sessions


def measure_family_power(rows: Sequence[dict], *,
                         history_ratio: float = POWER_HISTORY_RATIO) -> FamilyPower:
    """测「分族」在 `rows` 上有没有预测力。三态，且 UNKNOWN 不并进 ABSENT。

    判据：时序切分 ⇒ 历史建模型 ⇒ 预测未来 ⇒ 与**不区分族的常数基线**比
    误差。优势必须超过 `max(下限, 2 × 噪声)`，噪声由**反证组**实测给出
    （不是拍一个绝对阈值 —— 与 `measure.noise_floor` 同一条纪律）。

    输入行需要 `session`（切分）、`family`（分组）、`passed`（目标）。
    缺 `session` 的行**整批**不可用 ⇒ 返回 UNKNOWN 并写明原因。
    """
    hist, fut, sessions = _split_sessions(rows, history_ratio=history_ratio)
    n_sessions = len(sessions)
    if n_sessions < MIN_POWER_SESSIONS:
        return FamilyPower("UNKNOWN", None, None, None, 0.0, 0.0, POWER_MARGIN_FLOOR,
                           len(hist) + len(fut), 0, n_sessions,
                           f"会话数 {n_sessions} < {MIN_POWER_SESSIONS}（且需带 session 字段）"
                           f" ⇒ 做不了时序切分")

    if len(hist) < MIN_SAMPLES:
        return FamilyPower("UNKNOWN", None, None, None, 0.0, 0.0, POWER_MARGIN_FLOOR,
                           len(hist), len(fut), n_sessions,
                           f"历史 {len(hist)} 条 < {MIN_SAMPLES}")
    if len(fut) < MIN_FUTURE_ROWS:
        return FamilyPower("UNKNOWN", None, None, None, 0.0, 0.0, POWER_MARGIN_FLOOR,
                           len(hist), len(fut), n_sessions,
                           f"未来 {len(fut)} 条 < {MIN_FUTURE_ROWS}"
                           f"（会话数 {n_sessions} 偏少 ⇒ 外推不可靠）")

    # 只有一个族 ⇒ 「分族」这个划分在物理上不存在（族率恒等于全局率，
    # Δ 恒为 0）。这时不能报 ABSENT（那是在说"我测过了，没有"），
    # 要报 UNKNOWN（"这个账本上根本没有可测的划分"）。
    if len({str(r.get("family") or "") for r in hist}) < 2:
        return FamilyPower("UNKNOWN", None, None, None, 0.0, 0.0, POWER_MARGIN_FLOOR,
                           len(hist), len(fut), n_sessions,
                           f"只有 1 个能力族 ⇒ 无从谈『族间差异』")

    main = _predict_errors(hist, fut)
    sd, mean_abs, n_ctrl = _noise_floor(hist, fut, N_CONTROL_PERMS)
    # 三道取大者，各自防的东西不同（见 `_noise_floor` 的注释）
    margin = max(POWER_MARGIN_FLOOR, 2.0 * sd, mean_abs)
    d = main["delta"]
    if d is None:
        state = "UNKNOWN"
        reason = "无法计算误差（历史无可用族率）"
    elif d > margin:
        state = "PRESENT"
        reason = ""
    else:
        # 样本够、算得出来、但没有优势 ⇒ 这是**负结论**，不是没量到。
        state = "ABSENT"
        reason = (f"族内漂移 ≥ 族间差异（Δ={d} ≤ {margin}）"
                  f" ⇒ 分族只是把噪声写成规律")
    return FamilyPower(state, d, main["mae_model"], main["mae_base"],
                       round(sd, 4), round(mean_abs, 4), round(margin, 4),
                       len(hist), len(fut), n_sessions, n_ctrl, reason)
