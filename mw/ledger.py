"""跨会话分数账本 —— 自我模型**唯一**的证据来源。

## 为什么需要它

§16.7 第三条给「自我模型」排的判据是：**它需要跨会话、带口径签名的分数账本。**

在此之前 `SelfModel` 是空着起跑的：`Core()` 里写的是 `SelfModel()`，
没有任何历史。于是它的判据只能退而求其次，用**代理量**
（`consulted` / `influenced` 计数）—— 那只能证明"它被咨询过"，
证明不了"它说的对"。这正是 §16.7 判它为"判据不成立"的原因。

代理量与行为锚的差别，用一句话说清：

    代理量：自我模型有没有说话？        → 恒真（它总会说话）
    行为锚：自我模型说的话能不能预测未来？ → 可证伪

## 三条硬纪律（每一条都对应一个已经发生过的失败）

**① 跨会话，不是轮内。**
轮内使用会当场毁掉 `repeat` 的意义：第 6 题拿到第 1 题的分数，
于是"同一条件下重复 N 次"变成"顺次影响 N 次"，量到的顺序效应而不是能力。
所以本模块**只提供起跑快照**：`Core` 在构造时冻结一次历史，
之后无论账本被谁追加，本轮读到的都不变。

**② 带口径签名，缺签名的行一律拒绝。**
一条分数如果不知道是哪把尺子量的，它就不是证据。
把它并进账本 = 把两种东西当成一种 —— V5 死因的同一类
（拿跨口径的数字做比较，然后称之为"改进"）。
⚠️ **缺签名 ≠ 同口径**。缺了就拒，不默认、不猜测、不静默放行。

**③ 内核在轮内没有写通道。**
写入只发生在外部（`run.py --bench` 在拿到**外部打分器**的分数之后）。
这不是"暂时没接"，是接口上没有——`ScoreLedger` 的写入方法只有在
外部锚层一侧被调起，内核持有的账本实例是只读快照。
理由和 critique 的"只落盘不投喂"完全一致：
**自己给自己记分的那一天，就是这套测量体系失效的那一天。**

## 它回答什么问题

不是"我聪明吗"，而是：

    用历史会话的读数去预测未来会话，误差有多大？
    这个误差必须**显著低于**一个不区分能力族的对照基线。
    低不了 ⇒ 自我模型是装饰（它记了一堆数，但那些数对未来没有信息）。

`bench/probe_selfmodel.py` 就是这条判据的实测台。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

#: 一条分数要**落盘**最少必须具备的字段。
#: 缺 `family` ⇒ 归不了族；缺 `passed` ⇒ 是"没记"不是"失败"
#: （把缺失当零会让自我模型凭空觉得自己很差 —— 见 `metacog.load_evidence`）；
#: 缺 `caliber_sig` ⇒ 不知道是哪把尺子量的。
REQUIRED_FIELDS: tuple[str, ...] = ("family", "passed", "caliber_sig")

#: 读侧**先判**的两个字段。与 `REQUIRED_FIELDS` 分开声明，是为了让拒绝原因
#: **可归因**：若读侧直接用 `REQUIRED_FIELDS` 一次性判，缺签名的行会被记成
#: `missing_fields` —— 于是"没签名"和"字段没记全"混成一个数，
#: 又一个"总拒绝数无法归因"的入口（与 critique 的 `REJECT_KINDS` 同构）。
EVIDENCE_FIELDS: tuple[str, ...] = ("family", "passed")

#: 拒绝原因的受控词表。与 `critique.REJECT_KINDS` 同构：
#: 一个总拒绝数无法归因（分不清是"没签名"还是"口径不对"），必须分项。
REJECT_KINDS: tuple[str, ...] = (
    "missing_fields",   # 缺必需字段（没记全 ⇒ 不是证据）
    "no_caliber_sig",   # 完全没有口径签名 ⇒ 拒绝，不默认同口径
    "diff_caliber_sig",  # 有签名但不是当前口径
    "duplicate",        # 同一会话里的同一题写了两次（详见 `append` 的注释）
)


#: 「通过」的判定线。**刻意取满分**，且刻意**不提供**调节入口。
#:
#: 为什么不能再低一点（例如 0.5 = 部分正确）：`passed` 是自我模型预测的目标
#: 变量，而预测目标一旦是可调的阈值，就出现了一个**能把结论调好看的旋钮** ——
#: 那正是 V5 的病（尺子随退化下移）。满分是唯一不需要额外论证的阈值：
#: "全对"与"没全对"是外部打分器说了算的事件，不是我们定义的。
PASS_MARK = 1.0


def is_passed(score: Any) -> bool:
    """分数 → 通过与否。**没量到（`None`）不是失败**，调用方必须先分口径。"""
    try:
        return float(score) >= PASS_MARK
    except (TypeError, ValueError):
        return False


def next_session(rows: Sequence[dict]) -> int:
    """下一个会话号 = 已有最大会话号 + 1。

    会话号是**时序切分的唯一依据**（`probe_selfmodel.split` 按它切历史/未来）。
    用"已有最大 +1"而不是"行数"，是因为一轮里会有多行属于同一会话 ——
    若按行数编号，同一会话的不同题目会被切成"一题在未来、一题在历史"。
    """
    top = -1
    for r in rows:
        try:
            top = max(top, int(r.get("session", -1)))
        except (TypeError, ValueError):
            continue
    return top + 1


def canonical(obj: Any) -> str:
    """规范化 JSON。与 `mw.bench._canon` **逐字节相同** —— 口径签名必须
    在两处算出同一个值，否则"同口径"这个判断就是假的。

    ⚠️ 为什么把它搬到这里而不是各写一份：同一个哈希函数两处实现
    = 同一事实两处声明，是 V5 静默漂移最经典的入口。
    现在 `mw.bench.Caliber.signature()` 委托本函数，实现只有一份。
    """
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


#: 账本签名**故意排除**的口径字段（唯一事实源）。
#:
#: ⚠️ 这不是"把口径闸放松"，是**两个问题需要两把签名**：
#:
#:   · 基线比较问的是"同一个候选在同一批题上有没有变好"
#:     ⇒ 题目集必须钉死（`suite_sha` 在），换一批题就是换一个问题。
#:   · 自我模型问的是"我在**哪些族**上弱"
#:     ⇒ 若把题目集钉死，同口径就必然同族（现在的 `family = suite.name`），
#:       于是"跨族预测"在物理上**永远不可测** —— 判据会退化成又一条恒真断言。
#:       题目集在这里不是"尺子"，是**被泛化的对象**。
#:
#: 排除 `repeat` 的理由不同：账本存的是**每题**分数，不是整轮均值，
#: `repeat` 对它没有意义（把一个不相关的字段塞进签名，只会让行白白分裂）。
#:
#: ⛔ 剩下的**一个都不许再放**：`scorer_sha`（换尺子）、`temperature`（换采样）、
#: `limits`（换预算绝对值）、`llm_cond`（换模型）、`memory_*`（换起跑记忆）
#: —— 任一项变了，两条分数就不是同一个东西，混进来就是跨口径比较。
LEDGER_SIG_EXCLUDE: tuple[str, ...] = ("suite_sha", "repeat")


def ledger_sig_of(caliber: dict) -> str:
    """账本签名 = caliber 去掉 `LEDGER_SIG_EXCLUDE` 后的哈希。

    与 `signature_of`（完整口径签名）共用同一个哈希实现 ——
    **两把签名，两个问题**，但实现只有一份。
    """
    return signature_of({k: v for k, v in dict(caliber).items()
                         if k not in LEDGER_SIG_EXCLUDE})


def signature_of(caliber: dict) -> str:
    """口径签名 = 规范化 JSON 的 sha256 前 16 位。

    刻意**只取 16 位**：它是日志里给人看的短标签，
    不是密码学用途；取全长的唯一后果是每行日志长 64 个字符。
    """
    return hashlib.sha256(canonical(caliber).encode("utf-8")).hexdigest()[:16]


@dataclass
class LedgerStats:
    """账本的**读数**。每个计数都必须有消费者（`Core._finish` 的 stats 出口）。

    `admitted` 之外的四个计数不是装饰：一个只报"采纳了多少"的账本，
    你永远看不出它有没有在挡东西 —— 而"从没挡过东西的闸"等于没闸。
    """

    total: int = 0
    admitted: int = 0
    rejected_missing_fields: int = 0
    rejected_no_sig: int = 0
    rejected_diff_sig: int = 0
    rejected_duplicate: int = 0
    bad_lines: int = 0

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "admitted": self.admitted,
            "rejected": {
                "missing_fields": self.rejected_missing_fields,
                "no_caliber_sig": self.rejected_no_sig,
                "diff_caliber_sig": self.rejected_diff_sig,
                "duplicate": self.rejected_duplicate,
            },
            "bad_lines": self.bad_lines,
        }

    def as_line(self) -> str:
        r = self.as_dict()["rejected"]
        return (f"账本 总={self.total} 采纳={self.admitted} "
                f"拒(缺字段={r['missing_fields']}/无签名={r['no_caliber_sig']}"
                f"/异口径={r['diff_caliber_sig']}/重复={r['duplicate']})"
                f" 坏行={self.bad_lines}")


class ScoreLedger:
    """跨会话分数账本。**内核侧只读，写入在外部锚层。**

    用法（两侧各一步）：

        外部（run.py --bench，拿到外部打分器的分数之后）：
            ledger.append([{"family": ..., "passed": ..., "score": ...,
                            "caliber_sig": caliber.signature(), ...}])

        内核（Core 构造时，一次性）：
            core = Core(score_ledger=ledger, caliber_sig=caliber.signature())
            # → selfmodel 的证据 = 起跑那一刻的同口径历史

    ⚠️ `Core` **不会**在 `run()` 里写账本。这是设计，不是遗漏：
    写完再读会让同一轮内的第 N 题受益于第 N-1 题的分数（纪律①）。
    """

    def __init__(self, path: Path | str, *, caliber_sig: str = "") -> None:
        self.path = Path(path)
        self.caliber_sig = str(caliber_sig)
        self.stats = LedgerStats()
        self._rows: list[dict] | None = None   # 惰性读：构造不该有副作用

    # ------------------------------------------------------------------
    # 写（**外部**调用；内核没有调用点，契约 AI4 用 AST 钉死）
    # ------------------------------------------------------------------
    def append(self, rows: Iterable[dict]) -> dict:
        """追加落盘。**缺必需字段的行在写入前就被拒**，不进文件。

        为什么不"先全记下来、读的时候再筛"：那样文件里会堆积无从归属的
        行，而"账本里有一行不知道哪来的分数"正是下一次跨口径比较的入口。
        挡在写入侧，文件里的每一行都自带可归属信息。

        ── 幂等：同一会话里的同一题**只许写一次** ──────────────────────
        起因（本轮自踩，实测）：后台跑 8 个会话的实验被我中途 kill，
        重启后 `next_session()` 算出**同一个会话号** ⇒ 那一轮的成绩被写了
        两次（同一 `task_id` 在该会话下出现 2 次，14 行变成 27 行）。
        账本当时**静默接受**了，于是通过率被两份数据加权 —— 而日志上
        看不出来（只显示"追加 N 行"）。

        ⇒ 这不是理论风险：长跑服务被 kill 后重启是常态，而"同一题被计两次"
        会让自我模型对自己能力的估计**静默失真**。所以挡在写侧，
        并把它记成一个**可归因**的拒绝原因（不是混进总拒绝数）。

        ⚠️ 只按 `(session, task_id)` 判重：同一个 task_id 在**不同**会话里
        出现是正常的（同一套题每个会话都要跑一遍）。
        """
        added, rejected, dup = 0, 0, 0
        seen: set[tuple] = set()
        for r in self.load():
            k = (r.get("session"), r.get("task_id"))
            if k[0] is not None and k[1] is not None:
                seen.add((str(k[0]), str(k[1])))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            for r in rows:
                if not isinstance(r, dict) or any(k not in r for k in REQUIRED_FIELDS):
                    rejected += 1
                    continue
                key = (str(r.get("session")) if r.get("session") is not None else None,
                       str(r.get("task_id")) if r.get("task_id") is not None else None)
                if key[0] is not None and key[1] is not None:
                    if key in seen:
                        dup += 1
                        continue
                    seen.add(key)
                f.write(canonical(r) + "\n")
                added += 1
        self._rows = None   # 缓存失效
        return {"added": added, "rejected": rejected, "duplicate": dup}

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    def load(self) -> list[dict]:
        """读出全部行。**坏行计数并跳过**，不让一行坏数据带走整轮。

        为什么不抛异常：账本是**跨进程累积**的，一次写入中断就可能留下
        半行。让整个内核因为一行历史起不来，是把历史的重要性放在当下之上。
        ⚠️ 但跳过必须留痕 —— `stats.bad_lines` 是唯一允许"跳过"的理由，
        且它出现在读数出口里（不静默）。
        """
        if self._rows is not None:
            return list(self._rows)
        rows: list[dict] = []
        bad = 0
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        bad += 1
                        continue
                    if not isinstance(obj, dict):
                        bad += 1
                        continue
                    rows.append(obj)
        self._rows = rows
        self.stats.bad_lines = bad
        return list(rows)

    def admissible(self, caliber_sig: str | None = None) -> list[dict]:
        """只返回**当前口径**下行得通的行，并**分项计数**被拒的原因。

        `caliber_sig=None` ⇒ 一条都不收。这不是苛刻：
        没有口径就没有"可比"这件事，返回任何东西都是猜。
        """
        sig = self.caliber_sig if caliber_sig is None else caliber_sig
        st = LedgerStats()
        out: list[dict] = []
        for r in self.load():
            st.total += 1
            if any(k not in r for k in EVIDENCE_FIELDS):
                st.rejected_missing_fields += 1
                continue
            got = r.get("caliber_sig")
            if not got:                       # 完全没有签名 ⇒ 拒，不默认同口径
                st.rejected_no_sig += 1
                continue
            if str(got) != str(sig):
                st.rejected_diff_sig += 1
                continue
            out.append(r)
        st.admitted = len(out)
        st.bad_lines = self.stats.bad_lines
        self.stats = st
        return out

    def snapshot(self, caliber_sig: str | None = None) -> tuple[dict, ...]:
        """**起跑冻结**：返回一个元组（不可变），本轮不会再变。

        返回元组而不是 list 是刻意的：它是"这一轮的证据已经封板"的
        物理表达 —— 后面谁想往里加东西，拿到的是不可变对象。
        """
        return tuple(self.admissible(caliber_sig))

    # ------------------------------------------------------------------
    def report(self) -> dict:
        d = self.stats.as_dict()
        d["path"] = str(self.path)
        d["caliber_sig"] = self.caliber_sig
        return d

    def as_line(self) -> str:
        return self.stats.as_line()
