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
import re
from dataclasses import dataclass
from pathlib import Path

#: 被拒原因的受控词表。新增一种拒绝方式必须先在**这里**登记，
#: 否则 `rejected` 字典里会冒出一个没有归属的键（同一事实多处声明的入口）。
REJECT_KINDS: tuple[str, ...] = ("self", "cross_scorer", "cross_suite")

_MAX_WHY = 400
_MAX_ANSWER = 200

# ===========================================================================
# 投喂口径：`why` 里哪一半算「教训」
#
# V209（§277）实测：同一条 `[外部评审]` 通道，两种用法方向相反 ——
#   · t04：`TypeError: 'tuple' object does not support item assignment`
#          ⇒ 异常名点的就是根因（元组不可变），照做 2/20 → 7/20（+25pp）；
#   · t01：`ValueError: not enough values to unpack (expected 2, got 1)`
#          ⇒ 模型把「expected 2」当成**设计线索**，把输入按 `h` 切成两段，
#            单单位 14/15/14 → 2/6/4（p≤0.01）。
# ⇒ 异常**名**与失败断言（输入/期望）可迁移；异常文里的**元数**是有害的具体化。
#
# ⛔ 这个函数**只管投喂**，不碰落盘：真账本永远存原文（证据不能被自己的过滤器改）。
# ⛔ 只认这三类形态 —— 多认一条就是多一条没有读数支撑的改写规则。
# ===========================================================================
RAW_WHY: str = "raw"
TRANSFERABLE_WHY: str = "transferable"
WHY_MODES: tuple[str, ...] = (RAW_WHY, TRANSFERABLE_WHY)

#: `(expected 2, got 1)` / `(expected at least 1, got 0)` —— 元数陈述。
_METASHAPE = (
    re.compile(r"\s*\(\s*expected\s+(?:at\s+least\s+|at\s+most\s+)?\d+\s*,\s*got\s+\d+\s*\)",
               re.I),
    #: 同一句话的中文/英文措辞形态：解包失败是**症状**，不是"输入有两段"的证据。
    re.compile(r"\s*\b(?:not\s+enough|too\s+many)\s+values\s+to\s+unpack\b", re.I),
)


def transferable_why(why: str) -> str:
    """把一段打分器理由裁成「可迁移的那一半」。

    只做三件事（顺序固定）：
        ① 删括号元数 `(expected N, got M)`；
        ② 删解包短语 `not enough/too many values to unpack`；
        ③ 删删完剩下的悬空冒号（`⇒ ValueError:` → `⇒ ValueError`）。

    ⚠️ 第 ③ 步是**装饰**不是判据：不删也不改变语义，只是让块读起来不像被截断。
    ⚠️ 输入原样返回是**正常结果**（例如 t04 的 `TypeError` 里一个模式都不匹配）。
       ⇒ 调用方不许把「没改写」读成「过滤器哑了」，那是它该有的行为。
    """
    out = why or ""
    for pat in _METASHAPE:
        out = pat.sub("", out)
    out = re.sub(r"[：:]\s*(?=\||$)", "", out)
    return re.sub(r"\s+", " ", out).strip()


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

    def __init__(self, path: Path | None = None, *, allow_self: bool = False,
                 why_mode: str = RAW_WHY) -> None:
        self.path = Path(path) if path else None
        #: ⭐ 第一百二十三轮（§96）：同题评审是否放行。**默认 False = 旧行为一字不改。**
        #:
        #: 为什么需要一个开关而不是直接改闸：第 2 条纪律（「不许用当前题」）挡的是
        #: **本轮正在答的这道题**——那确实是照着自己的答案改答案。但 `--critique-scope=prev`
        #: 给内核的是**加载时的快照**：里面同题那条讲的是**上一轮**的答案与分数，
        #: 内核此刻还没答。把这两件事一起挡掉，等于把「自省」这一环物理切断：
        #: V32 探针实测 6/6 题的同题评审不可达（`p0_selfreach.txt`）。
        #: 所以：**轮内照旧挡（默认），跨轮由调用方显式打开**。
        self.allow_self = bool(allow_self)
        #: ⭐ V210（§278）：`why` 的投喂口径。**默认 raw = 旧行为一字不改。**
        #: 过滤只发生在 `add()` ⇒ 它改的是**这一份账本里的文本**。
        #: 真账本（落盘那条）必须保持 `raw`；只有内核拿到的快照副本才该是
        #: `transferable` —— 证据完整、投喂裁剪，两件事不能在同一份对象上做。
        if why_mode not in WHY_MODES:
            raise ValueError(f"why_mode={why_mode!r} 不在 {WHY_MODES} 里")
        self.why_mode = why_mode
        #: 被改写/被清空的条数。⛔ 不报这两个数，「过滤器没生效」和「过滤器哑火」
        #: 在读数上一模一样（V158 假绿 253 / 真值 4 的同型）。
        self.why_rewritten = 0
        self.why_emptied = 0
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
        why = c.why[:_MAX_WHY]
        if self.why_mode == TRANSFERABLE_WHY:
            _w = transferable_why(why)
            if _w != why:
                self.why_rewritten += 1
                why = _w
            if not why:
                #: 过滤把理由整个吃掉了 ⇒ **退回原文并计数**。
                #: ⛔ 不许静默换成空串：一条"只有分数没有理由"的评审
                #: 正是这个模块存在的缺口，比不投喂更糟。
                self.why_emptied += 1
                why = c.why[:_MAX_WHY]
        self.by_key[key] = Critique(
            scorer_sha=c.scorer_sha, suite_sha=c.suite_sha, task_id=c.task_id,
            family=c.family, score=c.score, why=why,
            answer=c.answer[:_MAX_ANSWER])
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
            if exclude_task_id and tid == exclude_task_id and not self.allow_self:
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
            #: ⚠️ 口径必须自报：`allow_self=True` 的账本和 `False` 的账本
            #: 读出来的 `served` 不可比（前者最多 6 条，后者最多 5 条）。
            "allow_self": self.allow_self,
            #: ⭐ V210：`why` 投喂口径 + 它实际改了几条。
            #: 只报 `why_mode` 不报 `why_rewritten` ⇒ 分不清「没改写」与「没生效」。
            "why_mode": self.why_mode,
            "why_rewritten": self.why_rewritten,
            "why_emptied": self.why_emptied,
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
