"""闭环通量 —— 感知 → 记忆 → 推理 → 行动 这条环上，每一步**真的过去了多少**。

## 为什么需要这个模块

「模块齐了」不等于「闭环通了」。2026-10-17 的接线审计抓到两个同构的例子：
`working` 有 `think()` 但全项目 **0 个生产调用点**；`episodes` 写了 16 条，
而 `recall()` 只查 facts 表。**两张表、两套 API、各自有测试、各自能跑通，
中间没有管子。** 只看模块清单，这两处都是"已实现"。

所以本模块只做一件事：给环上的每条边装一个流量计。
    · 产出（`emit`）：这个相位这回合交出了多少条、多少字符
    · 消费（`take`）：下游这回合真的读进了多少条
    · 回流（`reuse_rate`）：上一回合行动的结果，这一回合的上下文里还剩多少

第三条是「流畅」的判据。人脑的流畅不是每回合重新读一遍全部历史，
而是**上一步的结果仍在激活状态里**。若回流率恒为 0，四个相位再强也只是
四个独立进程 —— 那正是"单个模块强 ≠ 整体智能强"的可测量形态。

## 两条纪律

1. **产出 ≠ 消费**。`emit` 和 `take` 必须分开记，覆盖率才有意义。
   把两者写成一个数，等于把"我写了"当成"它读了"。
2. **回流率用 token 覆盖率算，不用字符数算**。字符数会被模板文字稀释：
   `[目标] xxx` 这段每回合都在，它不属于"回流"，属于"常驻脚手架"。
   按 token 覆盖率并在下方扣除脚手架，才答得了"上一步的结果还在不在"。

## 为什么边只有三条，回流单列

第一版写了四条边，其中 `perceive>memory` 与 `act>perceive` 的覆盖率**恒为 0**：
前者我只 `emit` 没 `take`，后者根本不该按"条数"计（它问的是"内容还剩下多少"，
不是"几条过去了"）。当时自检打印出 `perceive>memory=0.0 act>perceive=0.0`，
而**没有任何地方会因此报错** —— 一个永远读 0 的读数，和没有这个读数一样危险，
因为它看起来像"闭环断了"，实际是"我忘了装表"。

现在的三条边各自只问一件事，且都有真实的消费端：

    perceive>memory   写入时被截断多少（原始字符 → 实际存下的字符）
    memory>reason     记忆里有几条真的进了这一回合的上下文
    reason>act        推理交出的 N 个想法里，有几个真的被执行

回流（`reuse`）单列，因为它是**内容保留率**，与"条数"不是同一个量纲。
把两种量纲塞进同一个 `coverage` 表，就会得到一串没法解释的数字。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 环上的三条边。顺序即信号的流向。
#: 回边（`act>perceive`）不在其中 —— 它的量纲是**内容保留率**，见 `reuse`。
#:
#: ⚠️ 每条边该看**哪个量纲**必须在这里声明一次（单一口径）：
#:    `perceive>memory` 问"写进去的字符还剩多少" ⇒ 看 chars；
#:    另两条问"几条过去了" ⇒ 看 items。
#:    第一版没这个表，`perceive>memory` 的覆盖率被算成 1/1 = 1.0 —— 一个
#:    **恒为满分的读数**，比没有更糟：它让人以为"这一环很健康"。
EDGES: tuple[str, ...] = (
    "perceive>memory",   # 观测写进记忆（覆盖率 = 写入时保留了多少字符）
    "memory>reason",     # 记忆进入推理的上下文
    "reason>act",        # 候选变成真实动作
)

#: 每条边的量纲。唯一事实源 —— `coverage()` 只读这张表。
EDGE_UNIT: dict[str, str] = {
    "perceive>memory": "chars",
    "memory>reason": "items",
    "reason>act": "items",
}

#: ⚠️ `memory>reason` 的口径是「**进入上下文的条数 / 记忆池里的可用条数**」。
#:    这个数**不是越高越好** —— 库越大，取 top-k 的比例自然越小。
#:    它只回答一个问题：这条线**通没通**（0 = 断，非 0 = 通）。
#:    把它读成"健康度"就会得出"记忆越少越好"的荒谬结论，
#:    而这正是"指标自己变成目标"的典型（V5 的全部读数之死）。

_TOK = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff]{2,}")


def _tokens(text: str) -> set[str]:
    return set(_TOK.findall((text or "").lower()))


@dataclass(frozen=True)
class Hop:
    """一次相位间传递。`items` 与 `chars` **分列**，因为它们的失真方向不同：
    一条很长的观测和六条短结论，字符数可以相同，信息量完全不同。"""
    step: int
    edge: str
    items: int
    chars: int


class FlowMeter:
    """被动计量器。**不做决策**，只回答"管道里有没有东西"。

    ⚠️ 刻意没有任何"打分"或"阈值"：它是尺子，不是裁判。把判定塞进尺子，
    就会出现 V5 那种"尺子跟着结果一起动"的病。
    """

    def __init__(self) -> None:
        self.hops: list[Hop] = []
        #: 每条边累计 (items, chars) —— 产出与消费**分行**记账，绝不合并。
        self.produced: dict[str, list[int]] = {e: [0, 0] for e in EDGES}
        self.consumed: dict[str, list[int]] = {e: [0, 0] for e in EDGES}
        self.reuse: list[float] = []
        self._prev_result = ""

    # ------------------------------------------------------------------
    def emit(self, step: int, edge: str, items: int, chars: int) -> None:
        """上游交出。非法边名直接报错 —— 静默忽略会让"少了一条边"永远不被发现。"""
        if edge not in self.produced:
            raise ValueError(f"未知闭环边 {edge!r}；合法值：{EDGES}")
        self.hops.append(Hop(step, edge, max(0, int(items)), max(0, int(chars))))
        self.produced[edge][0] += max(0, int(items))
        self.produced[edge][1] += max(0, int(chars))

    def take(self, edge: str, items: int, chars: int = 0) -> None:
        """下游真的读进。调用方必须**在读取点**调它，不能在上游顺手估一个数。"""
        if edge not in self.consumed:
            raise ValueError(f"未知闭环边 {edge!r}；合法值：{EDGES}")
        self.consumed[edge][0] += max(0, int(items))
        self.consumed[edge][1] += max(0, int(chars))

    # ------------------------------------------------------------------
    def note_result(self, text: str) -> None:
        """记下本回合行动产出的原文，供下一回合算回流率。"""
        self._prev_result = text or ""

    def reuse_rate(self, current_state: str) -> float:
        """上一步的结果，在本回合上下文里还占多少（token 覆盖率）。

        ⚠️ 返回 `None` 是不可能的 —— 没有上一步就没有回流可言，返回 0.0 会
        把"还没开始"读成"回流断了"。所以**首次调用不入样本**（由调用方判空）。
        """
        prev = _tokens(self._prev_result)
        if not prev:
            return 0.0
        return len(prev & _tokens(current_state)) / len(prev)

    def sample_reuse(self, current_state: str) -> float | None:
        """带空样本保护的版本：没有上一步结果时返回 None（没量到 ≠ 量到 0）。"""
        if not self._prev_result:
            return None
        r = self.reuse_rate(current_state)
        self.reuse.append(r)
        return r

    # ------------------------------------------------------------------
    def coverage(self, edge: str) -> float | None:
        """消费 / 产出，按 `EDGE_UNIT` 指定的量纲。分母为 0 返回 None ——
        没东西可传不等于传丢了。"""
        idx = 1 if EDGE_UNIT.get(edge, "items") == "chars" else 0
        out = self.produced[edge][idx]
        if out == 0:
            return None
        return round(self.consumed[edge][idx] / out, 3)

    def report(self) -> dict:
        """读数。`coverage=None` 表示该边从未产出（结构性缺席），
        与 `coverage=0.0`（产出了但没人读）是**两件不同的事**，必须分开看。
        """
        cov = {e: self.coverage(e) for e in EDGES}
        return {
            "hops": len(self.hops),
            "produced": {e: self.produced[e][0] for e in EDGES},
            "consumed": {e: self.consumed[e][0] for e in EDGES},
            "coverage": cov,
            "reuse_samples": len(self.reuse),
            "reuse_mean": round(sum(self.reuse) / len(self.reuse), 3) if self.reuse else None,
            "reuse_min": round(min(self.reuse), 3) if self.reuse else None,
            # 结构性缺席的边 —— 一条就能说明"环"其实是开链
            "dead_edges": [e for e in EDGES if cov[e] is None],
            "starved_edges": [e for e in EDGES if cov[e] == 0.0],
        }

    def as_line(self) -> str:
        r = self.report()
        if not r["hops"]:
            return "通量=未计量"
        cov = " ".join(
            f"{e.split('>')[0]}>{e.split('>')[1]}={('n/a' if v is None else v)}"
            for e, v in r["coverage"].items())
        reuse = "n/a" if r["reuse_mean"] is None else r["reuse_mean"]
        return f"通量[{cov}] 回流={reuse}"
