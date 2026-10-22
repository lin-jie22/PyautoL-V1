"""外部评审（critique）—— 他者的**过程性反馈**，内核的只读输入。

## 它补的是哪一格

`Scored.score` 是一个标量：它告诉内核"几分"，不告诉"为什么"。
而内核现有的学习信号全部来自它自己的 `result.ok`，`finish` 永不失败
⇒ 学习面是平的（§15.3b 实测：ollama `qwen2.5:7b`，`检验=5 命中=5`）。

没有他者告诉它"你错在哪"，内核就永远只能从自己的成功里学。

## 三条硬纪律（每条都有断言钉住，撤掉就转红）

1. **只读。** 内核能读 critique，不能写、不能改、不能自己造 ——
   `Critique` 是 frozen，`CritiqueLedger.add` 只接受构造好的对象，
   **没有任何从内核内部状态造一条 critique 的入口**。
   理由：那正是自评分，而自评分会让判据失去独立性。
2. **不许用当前题。** `admissible()` 的第一道闸：正在答的这道题，
   不能拿到它自己的评审。用了就是自评 —— 分数还是那个分数，
   但内核此刻已经在"照着答案改答案"。
3. **必须与分数同源。** `scorer_sha` / `suite_sha` 不同 ⇒ 拒绝。
   两把尺子给的"理由"不能混着用：口径一换，教训就变成噪声
   （与 `Caliber` 拒绝跨口径比较是同一条纪律的两个侧面）。

## 为什么默认不在同一次 suite 里启用

在第 6 题上喂第 1 题的评审，第 6 题就不再是同一条件下的测量了 ——
`repeat` 的意义当场消失。所以 `run_suite` **只落盘、不投喂**；
投喂由显式开关 `--use-critique` 打开，那是接线验证，不是能力测量。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

#: 被拒原因的受控词表。新增一种拒绝方式必须先在**这里**登记，
#: 否则 `rejected` 字典里会冒出一个没有归属的键（同一事实多处声明的入口）。
REJECT_KINDS: tuple[str, ...] = ("self", "cross_scorer", "cross_suite")

_MAX_WHY = 400
_MAX_ANSWER = 200


@dataclass(frozen=True)
class Critique:
    """一条外部评审。**只读**：字段全部由外部（打分器）提供。

    `scorer_sha` / `suite_sha` 是**口径签名**，不是元数据 ——
    没有它们，不同尺子的理由会被混着用，教训就变成噪声。
    """

    scorer_sha: str
    suite_sha: str
    task_id: str
    family: str = ""
    score: float = 0.0
    why: str = ""
    answer: str = ""

    def as_line(self) -> str:
        head = f"[{self.family or '?'}/{self.task_id}] {self.score:.2f}"
        return f"{head} — {self.why[:120]}" if self.why else head


class CritiqueLedger:
    """评审账本。去重键 = `(scorer_sha, suite_sha, task_id)`。

    去重靠**键覆盖**而不是"插入后再清理" ——
    同一内容存 39 次就是后者的下场：写入时没人管，清理时两套逻辑对不上。
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.by_key: dict[tuple[str, str, str], Critique] = {}
        self.added = 0
        #: 同一键被覆盖的次数 —— 这个数不为 0 说明有题被重跑过，是有用信息
        self.superseded = 0
        self.rejected: dict[str, int] = {k: 0 for k in REJECT_KINDS}
        self.calls = 0
        self.served = 0

    # ------------------------------------------------------------------
    def add(self, c: Critique) -> None:
        if not c.scorer_sha or not c.suite_sha or not c.task_id:
            raise ValueError("critique 缺口径签名（scorer_sha/suite_sha/task_id 都必填）")
        key = (c.scorer_sha, c.suite_sha, c.task_id)
        if key in self.by_key:
            self.superseded += 1
        self.by_key[key] = Critique(
            scorer_sha=c.scorer_sha, suite_sha=c.suite_sha, task_id=c.task_id,
            family=c.family, score=c.score,
            why=c.why[:_MAX_WHY], answer=c.answer[:_MAX_ANSWER])
        self.added += 1

    def admissible(self, *, scorer_sha: str, suite_sha: str,
                   exclude_task_id: str | None) -> list[Critique]:
        """挑出**可以进当前上下文**的评审。三道闸，每道都计被拒原因。

        ⚠️ 被拒必须**分项计数**：一个总的"拒绝数"无法归因 ——
        你分不清是防自评在起作用，还是口径对不上。
        """
        self.calls += 1
        out: list[Critique] = []
        for (ss, su, tid), c in self.by_key.items():
            if exclude_task_id and tid == exclude_task_id:
                self.rejected["self"] += 1
                continue
            if ss != scorer_sha:
                self.rejected["cross_scorer"] += 1
                continue
            if su != suite_sha:
                self.rejected["cross_suite"] += 1
                continue
            out.append(c)
        out.sort(key=lambda c: c.task_id)
        self.served += len(out)
        return out

    # ------------------------------------------------------------------
    def stats(self) -> dict:
        return {
            "stored": len(self.by_key),
            "added": self.added,
            "superseded": self.superseded,
            "calls": self.calls,
            "served": self.served,
            # 分母同时给出：`rejected` 是**累计**的，离开 `calls` 无法解释
            "rejected": dict(self.rejected),
        }

    # ------------------------------------------------------------------
    def save(self) -> int:
        """落盘。返回写入行数。"""
        if self.path is None:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with open(self.path, "w", encoding="utf-8") as f:
            for c in self.by_key.values():
                f.write(json.dumps({
                    "scorer_sha": c.scorer_sha, "suite_sha": c.suite_sha,
                    "task_id": c.task_id, "family": c.family,
                    "score": c.score, "why": c.why, "answer": c.answer,
                }, ensure_ascii=False) + "\n")
                n += 1
        return n

    def load(self) -> int:
        """读回。坏行**跳过并计数**，不静默吞掉。"""
        if self.path is None or not self.path.exists():
            return 0
        n = 0
        bad = 0
        for ln in self.path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                bad += 1
                continue
            try:
                self.add(Critique(**{k: d[k] for k in
                                     ("scorer_sha", "suite_sha", "task_id")},
                                  family=d.get("family", ""),
                                  score=float(d.get("score", 0.0)),
                                  why=d.get("why", ""), answer=d.get("answer", "")))
                n += 1
            except (KeyError, TypeError, ValueError):
                bad += 1
        self.bad_lines = getattr(self, "bad_lines", 0) + bad
        return n


def from_scored(*, scorer_sha: str, suite_sha: str, task_id: str, family: str,
                score: float, why: str, answer: str = "") -> Critique | None:
    """把打分器的一次输出转成一条评审。

    ⚠️ `why` 为空时返回 `None`，**不许**造一条空理由凑数 ——
    一条没有理由的 critique 只是又一个标量，那正是它要补的缺口的形状。
    """
    why = (why or "").strip()
    if not why:
        return None
    return Critique(scorer_sha=scorer_sha, suite_sha=suite_sha, task_id=task_id,
                    family=family, score=score, why=why, answer=answer)
