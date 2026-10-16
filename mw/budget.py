"""推理期预算（Test-Time Compute）—— 2026 理论在内核里的第一次落地。

依据（有出处，不是我编的）：
  · Noam Brown 2026-06《Implications of Large-Scale Test-Time Compute》：
    模型能力 = 投入预算的函数；10 美元 / 1 万美元 / 100 万美元得到不同量级的智能。
  · UK AISI 2026-07：能力不是单一分数，而是 test-time compute 上的曲线；
    **固定预算给出的是下界，不是上界**。
  · WAIC 2026「复杂度第一约束」：能力再强也要算得起，必须有自适应计算量（ACT）。

这条理论对内核有两条**强制的**后果：
  1. 内核不许有「无界循环」。任何一步都要花预算，花完就停并显式报告。
  2. **测量必须带预算维度**。因为同一候选在 1x 预算和 4x 预算下是两个不同的东西，
     拿「1x 的我」比「4x 的基线」得到的"提升"是假的 —— 这是 V5 尺子问题的
     推理期版本，measure.py 会把它钉死。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace


class BudgetExhausted(RuntimeError):
    """预算耗尽。**这是一等公民**，不是异常路径 —— 它必须向上传播并留下读数，
    绝不能被 `except Exception: pass` 吞掉（V5 的 R41：守卫拦下必须留正向信号）。
    """

    def __init__(self, kind: str, spent: float, limit: float) -> None:
        self.kind = kind
        self.spent = spent
        self.limit = limit
        super().__init__(f"预算耗尽 [{kind}]：已用 {spent:g} / 上限 {limit:g}")


@dataclass(frozen=True)
class Budget:
    """一次任务的资源信封。不可变 —— 降档/升档都返回新对象，避免"谁改了我"。"""

    steps: int
    llm_calls: int
    wall_s: float
    candidates: int
    tag: str = "1x"

    def scaled(self, factor: float, tag: str | None = None) -> "Budget":
        """按倍数升/降档。factor<1 降档（省钱），>1 升档（深想）。"""
        if factor <= 0:
            raise ValueError("factor 必须 > 0")
        return replace(
            self,
            steps=max(1, int(self.steps * factor)),
            llm_calls=max(1, int(self.llm_calls * factor)),
            wall_s=max(1.0, self.wall_s * factor),
            candidates=max(1, int(self.candidates * factor)),
            tag=tag or f"{factor:g}x",
        )


@dataclass
class Ledger:
    """预算账本：**唯一的**消费记录处，用于对账。

    V5 的教训（R79）是"分母必须能对账"。所以这里既提供 `left()` 给控制流，
    也提供 `snapshot()` 给日志与测试 —— 二者读同一个源，不许各算各的。
    """

    budget: Budget
    t0: float = field(default_factory=time.monotonic)
    steps_used: int = 0
    llm_calls_used: int = 0
    candidates_used: int = 0

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def check(self) -> None:
        """在**每次动作前**调用。按固定顺序检查，报错信息才能稳定复现。"""
        if self.steps_used >= self.budget.steps:
            raise BudgetExhausted("steps", self.steps_used, self.budget.steps)
        if self.llm_calls_used >= self.budget.llm_calls:
            raise BudgetExhausted("llm_calls", self.llm_calls_used, self.budget.llm_calls)
        if self.elapsed() >= self.budget.wall_s:
            raise BudgetExhausted("wall_s", self.elapsed(), self.budget.wall_s)

    def spend_step(self) -> None:
        self.steps_used += 1

    def spend_llm(self, n: int = 1) -> None:
        self.llm_calls_used += n

    def spend_candidate(self, n: int = 1) -> None:
        self.candidates_used += n

    def snapshot(self) -> dict:
        """给日志/测试的单一读数。**不要**在别处自己算 remaining。

        ⚠️ 语义澄清（第一版这里写错过）：`cand_width` 是**单次规划的探索宽度**
        （per-plan width），不是累计配额；所以这里只报累计**花费** `cand_spent`，
        不给分母。把它写成 `4/3` 会让人以为越界了 —— 含糊的字段迟早骗人。
        真正的累计硬约束是 steps / llm_calls / wall_s，那三个有分母。
        """
        return {
            "tag": self.budget.tag,
            "steps": f"{self.steps_used}/{self.budget.steps}",
            "llm_calls": f"{self.llm_calls_used}/{self.budget.llm_calls}",
            "cand_spent": self.candidates_used,
            "cand_width": self.budget.candidates,
            "wall_s": round(self.elapsed(), 2),
            "wall_limit_s": self.budget.wall_s,
        }

    def summary(self) -> str:
        s = self.snapshot()
        return (
            f"budget[{s['tag']}] steps={s['steps']} llm={s['llm_calls']} "
            f"cand={s['cand_spent']}(宽{s['cand_width']}) "
            f"wall={s['wall_s']}s/{s['wall_limit_s']:g}s"
        )
