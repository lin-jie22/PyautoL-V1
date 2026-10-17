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

不是"我聪明吗"（无法证伪），而是三个**可操作**的问题：

    1. 这个族我历史上通过率多少？        → 决定要不要多花候选预算
    2. 这个族的失败主要发生在哪一步？    → 决定要不要先做小验证
    3. 我的样本够不够支撑上面两条？      → 不够就说"不知道"，不装懂

第 3 条是这套东西能不能信的**全部**。一个看过 2 道题就开始给建议的
自我模型，和一个恒返回默认值的桩没有区别；区别只在于它更有说服力——
那更糟。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 少于这个样本数，"这个族的通过率"不构成证据。
#: 3 的依据：2 次里 1 次成功，通过率 0.5 与 0.0、1.0 都只差一个样本，
#: 任何基于它的资源调整都是在放大噪声（V5 的教训：极差 0.0862 盖过一切"改进"）。
MIN_SAMPLES = 3


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

    # ------------------------------------------------------------------
    def add(self, *, family: str, passed: bool, score: float | None = None,
            failed_step: str = "") -> None:
        self.evidence.append({"family": family, "passed": bool(passed),
                              "score": score, "failed_step": failed_step})

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
    def advise(self, *, family: str | None, max_candidates: int = 3) -> Advice:
        """给出资源建议。**认不出族就不装懂**（`family=None` 返回默认值）。

        ⚠️ 参数表里没有 `goal` —— 这不是遗漏，是**接口级防线**：
        只要它读得到当前题目，就一定有人会去读，然后就变成自评分。
        """
        self.consulted += 1
        if not family:
            return DEFAULT
        p = self.profile().get(family)
        if not p or p["n"] < MIN_SAMPLES or p["pass_rate"] is None:
            n = p["n"] if p else 0
            return Advice(1, False, False,
                          f"族 {family} 样本 {n} < {MIN_SAMPLES} ⇒ 不足以判断")
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
        return {
            "families": len(prof),
            "samples": len(self.evidence),
            # 有多少族**够格**给出建议 —— 这是"我到底知不知道自己"的分母
            "confident_families": [f for f, d in prof.items()
                                   if d["n"] >= MIN_SAMPLES and d["pass_rate"] is not None],
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
        out.append({"family": str(fam), "passed": bool(passed),
                    "score": r.get("score"),
                    "failed_step": str(r.get("failed_step") or "")})
    return out
