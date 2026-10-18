"""外部锚层 —— 让"这个候选是不是真的更好"有一个**内核改不了**的依据。

为什么必须有这个文件
--------------------
V5 跑了 136 轮、215K 行生产代码，对抗性审查的判决是：
**系统不具备声称"我变好了"的分辨力。**

    适应度函数 54/54 条记录 gain ≡ 0.0
    主测量通道 pairs=95 evaluated=0 —— 从未测到过任何东西
    同一配置重复 3 次极差 0.0862 > 任何可能的"改进"
    唯一的数字 0.7184 五天没动过一格，而每轮都在交付"全绿"

结论不是"某个 bug 没修"，是**反馈不可信**。经验数据也支持这个判断：
这类系统 99 轮故障全部集中在"反馈能不能被相信"，没有一条出自引擎本身。

所以 V1 在引擎之外单独建这一层，并立下唯一不变量：

    **尺子可以被内核读取，但不可以被内核改写。**

内核只能拿到一个 float。它拿不到尺子、也改不了尺子 —— 打分器在**独立子进程**里，
任务集是**只读文件**，两者内容哈希一起构成"口径签名"。换尺子 = 换口径 = 拒绝比较。

本模块实现 eval-standard-anchoring 的六条契约
---------------------------------------------
① 口径闸        caliber 签名 + 原子写 + 口径不同则拒绝覆盖 + 旧值另存 superseded
② 来源三层分级  权威（人工写的断言）/ 前沿（参考解 oracle）/ 实证（真跑通过率）
③ fail 边界     判定从严（口径不符 ⇒ 拒写）；缺失不得被读作否定（打分器失败 ≠ 0 分）
④ 消费计数      打在「跑完了」上；两个早退分支分开计；计数形态可对账
⑤ 证据可追溯    evidence_id + 观察/推断/未知 三分 + 失败也留下已确立的部分
⑥ 反证组        先证明尺子有分辨力（参考解必须赢过空答案），否则 fail-closed 拒用

⛔ 故意**不**做的事
------------------
· 不把供给侧运行时状态（如"本轮 LLM 有没有熔断"）写进口径 —— 那会让"供给侧崩了"
  的那一轮自动获得一把新尺子，正是要防的事。
· 不提供任何"内核可以写任务集 / 写打分器"的通道。写入权由**通道**定，不由声明定。
· 不在内核侧做任何近似判分。判分只在外部进程里发生。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

# 方向是单向的：**bench 可以读内核，内核不许读 bench**（契约钉死）。
# bench 是环外的裁判 —— 它 import 内核模块没问题，反过来就是"尺子被内核碰到"。
from .critique import CritiqueLedger, from_scored
from .ledger import canonical as _canon, signature_of
from .measure import Measurement, summarize


# ===========================================================================
# 0. 哨兵与规范化
# ===========================================================================
class _Missing:
    """缺失哨兵。

    契约① 纪律 2：**缺失键用哨兵，不要用默认值**。
    用默认值会让「没记」和「记了假值」在比较时不可区分 ——
    那正是尺子被悄悄换掉的入口。
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 仅调试可见
        return "<MISSING>"

    def __bool__(self) -> bool:
        return False


_MISSING = _Missing()


def _val(x: Any) -> Any:
    """把哨兵转成可哈希/可序列化的稳定表示。"""
    return "<MISSING>" if isinstance(x, _Missing) else x


# ⚠️ `_canon` **不在本文件里实现** —— 它现在是 `mw.ledger.canonical` 的别名。
#
# 为什么必须搬走（2026-10-17）：跨会话分数账本（`mw/ledger.py`）要用**同一个**
# 规范化函数算口径签名。若两边各写一份 `json.dumps(...)`，
# 那就是"同一事实两处声明" —— 哪天一边的 `separators` 变了，
# 同一个 caliber 会算出两个签名，"同口径"这个判断当场变假，
# 而**没有任何测试会红**（两边各自内部自洽）。V5 的静默漂移全是这个形状。
# 现在实现只有一份，且 `Caliber.signature()` 也走它（见下）。
def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha_file(p: Path) -> str:
    try:
        return _sha_text(p.read_text(encoding="utf-8"))
    except OSError:
        return "<UNREADABLE>"


# ===========================================================================
# 1. 口径签名（契约①）—— 尺子的身份
# ===========================================================================
#: 决定"是不是同一把尺子"的字段。改任何一个 ⇒ 两个数字不可比。
#: ⚠️ 刻意**不含**任何供给侧运行时状态（熔断 / 重试 / 上游健康），见模块头注释。
#:
#: `temperature` 为什么必须在里面（2026-10-17 实测加进来的）：
#:     同一把尺子、同一个模型、同一份任务集，仅改采样温度：
#:         T=0.0  → 各次总分极差 0.0000（逐题逐次完全一致）
#:         T=0.8  → 各次总分极差 0.1111（6 题中 2 题剧烈抖动）
#:     0.1111 这个数比 V5 的死亡数字 0.0862 还大。若温度不进闸，
#:     拿 T=0 的读数去和 T=0.8 的基线比较，就会把**采样抖动**读成"改进"
#:     —— 那正是 V5 "尺子随退化下移"的同一类错误。
#:     注意它**不是**运行时状态：温度是测量前就固定下来的条件，
#:     而熔断/重试是测量过程中自发产生的事件，两者性质不同。
#:
#: `limits` / `llm_cond` / `max_tokens` 为什么必须在里面（2026-10-17 补）：
#:     这是一类**同类漏洞的三个入口**。`budget_tag` 只是"倍数"（`4x`），
#:     而倍数乘在**绝对值**上 —— 两次运行可以都写 `4x` 却因为
#:     `V1_MAX_STEPS` 不同而跑出完全不同的预算。
#:     同理 `--max-tokens`（截断阈值）、`close_reserve_*`（在哪一步转向交付）、
#:     `candidates`（规划宽度）、`recall_k`（提示词里的记忆条数）、
#:     以及 `model` / `base_url` —— 每一个都能单独改变读数，
#:     而**只要有一个不在签名里，"同口径"这三个字就是假的**。
#:     这次的教训与 temperature 那次同源：**不是尺子坏了，是尺子量错了对象。**
CALIBER_KEYS: tuple[str, ...] = ("scorer_sha", "suite_sha", "repeat",
                                 "budget_tag", "temperature", "fresh",
                                 "memory_scope", "memory_seed",
                                 "limits", "llm_cond", "max_tokens",
                                 "tool_catalog_sha")


@dataclass(frozen=True)
class Caliber:
    """口径签名。缺失键一律是哨兵，不是默认值。"""

    scorer_sha: Any = _MISSING
    suite_sha: Any = _MISSING
    repeat: Any = _MISSING
    budget_tag: Any = _MISSING
    temperature: Any = _MISSING
    fresh: Any = _MISSING
    #: 记忆作用域：`per_task_fresh` = 每题独立记忆目录（种子恒空）；
    #: `shared_db` = 所有任务共用同一个 SQLite（记忆跨题、**跨运行**累积）。
    #:
    #: ⚠️ 这两个字段是 2026-10-17 实测逼出来的：两轮"同口径"（1x / T=0 / --fresh）
    #:    读数一次 `0.8611`、一次 `0.7222`。查下来 `--fresh` 只换了 Core，
    #:    DB 路径还是共享的 `state/memory.db` ⇒ 第二轮起跑时已带着 27 条语义记忆，
    #:    **召回结果不同 ⇒ 提示词不同 ⇒ 即使 T=0 输出也不同**。
    #:    而当时的标签写着"记忆不跨题"，口径闸也判"可比"。
    #:    这同时踩了 V5 的两条老病：**标签撒谎** + **口径没固定就宣称可比**。
    memory_scope: Any = _MISSING
    #: 起跑时的记忆基线 (facts, episodes)。两个 `shared_db` 运行只有在
    #: **基线也相同**时才是同口径 —— 否则它们比的是"记忆多少"而不是"候选好坏"。
    memory_seed: Any = _MISSING
    #: 内核硬上限的完整快照（`Limits` 的全部字段）。`budget_tag` 只是倍数，
    #: 乘在绝对值上；不含绝对值 ⇒ "4x" 可以是两个不同的实验。
    limits: Any = _MISSING
    #: 模型条件：model / 端点主机 / 超时 / 重试 / 退避。**不含 api_key**
    #: （敏感且不该落盘）。换个模型换个端点，就是换了一把尺子。
    llm_cond: Any = _MISSING
    #: 生成上限（`--max-tokens`）。它决定截断阈，而截断会直接改变分数。
    max_tokens: Any = _MISSING
    #: 工具目录指纹（`mw.tools.catalog_sha`）—— **提示词**的一部分。
    #:
    #: ⛔ 第二十三轮实测逼出来的漏洞：`planner.py:182` 把 `tools.catalog()` 拼进
    #:    规划器提示词，于是改 `agents.json` 就等于改提示词，而签名里**一个字都没有**。
    #:    观测后果：OFF 臂 `z01=1.0, z03=0.8`、上一版 `z01=0.8, z03=1.0`
    #:    —— 两个 mean 都是 0.7600，**分布却换了**，而标签照样写着"同口径"。
    #:    这与 `temperature` / `memory_scope` 两次是同一个病：**不是尺子坏了，
    #:    是尺子量错了对象**。
    tool_catalog_sha: Any = _MISSING

    def as_dict(self) -> dict:
        return {k: _val(getattr(self, k)) for k in CALIBER_KEYS}

    def signature(self) -> str:
        # 走 `mw.ledger.signature_of`：**唯一**的签名实现。
        # 账本侧用同一函数算出的签名，才可能与这里的"当前口径"对上。
        return signature_of(self.as_dict())

    def diff(self, other: "Caliber") -> list[str]:
        """点名差异字段 —— 拒绝时必须说清**哪一项**变了（不许只说"不一致"）。"""
        return [k for k in CALIBER_KEYS if _val(getattr(self, k)) != _val(getattr(other, k))]

    def diff_except(self, other: "Caliber", declared: Any) -> tuple[list[str], list[str]]:
        """**声明的变量豁免**：把差异分成 (意外的, 已声明的)。

        ## 为什么需要"豁免"而不是"放行"

        有些实验的**自变量本身就是口径字段**（路线①里 ON/OFF 的唯一差别就是
        工具目录与 `limits.external_answer_tool`）。若一律拒绝，实验做不成；
        若一律放行，就等于"换个来源就换把尺子"。

        第三条路：**声明哪些字段允许不同**，剩下的任何一个不同都算失败。
        这样"两臂只差一个变量"从**散文承诺**变成了可核验的事实 ——
        契约 AF4 与 `bench/probe_external_real.py` 的 `caliber_check` 都用它。
        """
        want = {str(x) for x in (declared or ())}
        diff = self.diff(other)
        return ([k for k in diff if k not in want],
                [k for k in diff if k in want])

    def same_as(self, other: "Caliber") -> bool:
        return not self.diff(other)

    def as_line(self) -> str:
        """一行摘要。字段**从 CALIBER_KEYS 派生**，不硬编码键名 ——
        硬编码的话，"闸里少了某个字段"这件事在日志里也看不出来
        （那正是"同一事实多份声明"的又一个入口）。"""
        d = self.as_dict()
        abbr = {"scorer_sha": "scorer", "suite_sha": "suite", "budget_tag": "budget"}
        cells = []
        for k in CALIBER_KEYS:
            raw = d[k]
            if isinstance(raw, (dict, list)):
                # 结构化值（limits / llm_cond）打短哈希：一行日志塞不下一个 dict，
                # 而**全量**在落盘的 caliber 里 —— 摘要与全量各归其位，都不缺。
                val = _sha_text(_canon(raw))[:10]
            elif k.endswith("_sha"):
                val = str(raw)[:12]
            else:
                val = str(raw)
            cells.append(f"{abbr.get(k, k)}={val}")
        return f"sig={self.signature()} " + " ".join(cells)


def caliber_from_dict(raw: dict) -> Caliber:
    """从落盘的 `caliber` 字典还原口径对象 —— **唯一**的反序列化实现。

    ⛔ 为什么要有它：探针要核验"两臂只差声明的变量"，就得把两臂的 caliber
       都还原成 `Caliber` 再走 `diff_except`；若探针自己拼一份
       `Caliber(**{...})`，那就是同一事实两处声明（V5 的老病）。
    ⚠️ 缺失键补 `_MISSING` 哨兵，**不补默认值** —— 补默认值会让
       "旧证据没记这个字段"和"这个字段等于默认值"变成同一件事。
    """
    return Caliber(**{k: dict(raw).get(k, _MISSING) for k in CALIBER_KEYS})


# ===========================================================================
# 2. 外部打分器（契约③）—— 物理隔离，内核只能拿到一个 float
# ===========================================================================
class ScoreError(RuntimeError):
    """打分器**自身**失败（超时 / 崩溃 / 输出不可解析）。

    ⛔ 绝不把它降级成 score=0.0。契约③：**缺失不得被读作否定** ——
    "这题得 0 分"和"这题没量到"必须在上层可区分（因为修法完全不同：
    前者改候选，后者改尺子或环境）。
    """


@dataclass
class Scored:
    score: float
    why: str = ""
    #: 逐条明细（打分器可选给出）。**有它，「为什么错」才是可归因的**：
    #: `why` 是一句话，`detail` 是"哪一条没满足、那条在说什么"。
    #: 内核只读，不改 —— critique 通道消费的就是它。
    detail: tuple[dict, ...] = ()


class ExternalScorer:
    """一个跑在**独立子进程**里的打分器适配器。

    协议（一行进、一行出，零依赖）：
        stdin  : {"task": {...}, "answer": "..."}
        stdout : {"score": 0.0..1.0, "why": "..."}
        退出码 : 0 = 正常判分；非 0 = 打分器自身失败（⇒ ScoreError）

    为什么必须走子进程而不是 import 进来：
        1. **物理隔离** —— 内核拿不到除 score 外的任何东西，改不了尺子；
        2. answer 是不可信输入，子进程天然是安全边界；
        3. 打分器可以是任何语言写的，内核不需要理解它。
    """

    def __init__(self, cmd: Sequence[str], *, source: Path | None = None,
                 timeout_s: float = 60.0, name: str | None = None) -> None:
        if not cmd:
            raise ValueError("打分器命令为空")
        self.cmd = [str(c) for c in cmd]
        self.source = Path(source) if source else None
        self.timeout_s = float(timeout_s)
        # 尺子的名字取**打分器脚本**名，不是解释器名。
        # 取 cmd[0] 会让所有打分器都叫 "python3" —— 日志里两把不同的尺子无法区分，
        # 而这正是"同一事实多份声明"的入口（拿名字对账时对不上）。
        self.name = name or (self.source.stem if self.source
                             else (Path(self.cmd[0]).name if self.cmd else "scorer"))
        self.calls = 0

    # ---- 尺子的身份 = 打分器**文件内容**的哈希，不是路径 --------------------
    # 路径可以指向同一个文件的不同版本 —— 那是两把不同的尺子。
    def sha(self) -> str:
        if self.source is None:
            return "<NO_SOURCE>"
        return _sha_file(self.source)

    def score(self, task: dict, answer: str) -> Scored:
        self.calls += 1
        payload = json.dumps({"task": task, "answer": answer}, ensure_ascii=False)
        try:
            proc = subprocess.run(
                self.cmd, input=payload, capture_output=True, text=True,
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired as e:
            raise ScoreError(f"{self.name} 超时 {self.timeout_s:g}s") from e
        except OSError as e:
            raise ScoreError(f"{self.name} 无法启动: {e}") from e

        if proc.returncode != 0:
            raise ScoreError(
                f"{self.name} 退出码 {proc.returncode}: "
                f"{(proc.stderr or '').strip()[:200] or '(无 stderr)'}"
            )

        lines = [ln for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
        if not lines:
            raise ScoreError(f"{self.name} 无输出")
        try:
            data = json.loads(lines[-1])
        except json.JSONDecodeError as e:
            raise ScoreError(f"{self.name} 输出不是合法 JSON: {lines[-1][:200]}") from e
        if not isinstance(data, dict):
            raise ScoreError(f"{self.name} 输出不是对象: {lines[-1][:200]}")

        raw = data.get("score", _MISSING)
        if isinstance(raw, _Missing):
            raise ScoreError(f"{self.name} 输出缺 score 字段")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ScoreError(f"{self.name} score 非数值: {raw!r}")
        s = float(raw)
        if not (0.0 <= s <= 1.0):
            raise ScoreError(f"{self.name} score 越界 [0,1]: {s}")
        # 明细是**可选**的：老打分器（code_exec）不给，那是合法的。
        # ⛔ 给了但格式不对 ⇒ **报错**，不许静默丢掉 ——
        #    静默丢掉 = 一句"我不对"又变回没有理由，这一格就白补了。
        raw_detail = data.get("detail", ())
        if raw_detail is None:
            raw_detail = ()
        if not isinstance(raw_detail, (list, tuple)):
            raise ScoreError(f"{self.name} detail 不是列表: {type(raw_detail).__name__}")
        detail: list[dict] = []
        for i, d in enumerate(raw_detail):
            if not isinstance(d, dict):
                raise ScoreError(f"{self.name} detail[{i}] 不是对象")
            detail.append(dict(d))
        return Scored(s, str(data.get("why", "")), tuple(detail))


# ===========================================================================
# 3. 任务集（契约②）—— 权威层：人工写的断言 + 前沿层：参考解
# ===========================================================================
@dataclass(frozen=True)
class Assertion:
    """一条**关于答案**的可核对声明。

    ⚠️ 受控词表（`KINDS`：**这个任务该说清什么**有哪几种说法）**不在这一层**。
    它在 `bench/scorers/assertion.py` 里，因为**判定的合法性由裁判决定**：
    `mw` 侧若也校验一遍 kind，两处迟早漂移成
    「任务层认为合法、打分器不认」的第二套真相（契约 AH1 静态锚钉住）。

    本层只保证结构：有 `id`、有 `why`。
    `why` 必填不是形式主义 —— 逐条明细的价值全靠它：
    「未满足 a4」对内核没有信息量，「未满足 a4（必须说清减法规则的生效条件）」才有。
    """

    id: str
    kind: str
    why: str
    value: Any = ()

    def as_dict(self) -> dict:
        v = self.value
        if isinstance(v, tuple):
            v = list(v)
        return {"id": self.id, "kind": self.kind, "why": self.why, "value": v}


@dataclass(frozen=True)
class Task:
    """一条可判分的任务。

    三层来源在这里同时被钉住（契约②）：
        权威 —— `tests`：人工写的断言，是这条任务的 ground truth
        前沿 —— `oracle`：参考解，用来在 calibrate() 里自证尺子有分辨力
        实证 —— 真跑出来的通过率（由外部打分器给出，不在本结构里）

    `partial`（可选）是一份**部分正确**的参考解，用来证明尺子不是二值的：
    只证明"满分 vs 零分可分"太弱 —— 那与 V5 那把"五天不动"的尺子没有本质区别，
    真正的分辨力必须证明**中段也可分**（0 < score < 1 可达且有序）。
    """

    id: str
    prompt: str
    tests: tuple[str, ...] = ()
    oracle: str = ""
    partial: str = ""
    null: str = ""
    meta: tuple[tuple[str, Any], ...] = ()
    #: 「语言与沟通」那一格的 ground truth：这道题**该说清什么**。
    #: 判据来源从「尺子的想象」位移到「任务自带的声明」—— 见 §16.9。
    assertions: tuple[Assertion, ...] = ()

    def as_dict(self) -> dict:
        d: dict = {
            "id": self.id,
            "prompt": self.prompt,
            "tests": list(self.tests),
            "meta": {k: _val(v) for k, v in self.meta},
        }
        if self.assertions:
            d["assertions"] = [a.as_dict() for a in self.assertions]
        return d

    def calibration_material(self) -> tuple[str, str, str]:
        """(好, 中, 坏) 三个已知质量的答案 —— 校准材料本身不进 task 载荷。"""
        return (self.oracle, self.partial, self.null)

    def fingerprint(self) -> dict:
        """进尺子身份哈希的内容。**必须**含校准材料：
        改掉一条任务的部分解，校准结论就变了 ⇒ 那是换了尺子，不是同一把。"""
        return {
            "id": self.id, "prompt": self.prompt, "tests": list(self.tests),
            "oracle": self.oracle, "partial": self.partial, "null": self.null,
            "meta": {k: _val(v) for k, v in self.meta},
            # 断言进尺子身份：改掉一条「该说清什么」，校准结论就变了 ⇒ 换了尺子。
            "assertions": [a.as_dict() for a in self.assertions],
        }


@dataclass(frozen=True)
class Suite:
    """任务集。**只读** —— 内核没有任何写入通道（契约②写入权由通道定）。"""

    name: str
    tasks: tuple[Task, ...]

    def sha(self) -> str:
        """内容哈希。按 id 稳定排序 ⇒ 重排版不算换尺子，改一个字就算。"""
        blob = _canon([t.fingerprint() for t in sorted(self.tasks, key=lambda t: t.id)])
        return _sha_text(blob)[:32]

    def __len__(self) -> int:
        return len(self.tasks)

    def ids(self) -> list[str]:
        return [t.id for t in self.tasks]

    @staticmethod
    def load(path: Path | str) -> "Suite":
        """从 JSONL 装载。每行一条任务。坏行**显式报错**，不静默跳过。"""
        p = Path(path)
        tasks: list[Task] = []
        seen: set[str] = set()
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{p.name}:{i} 不是合法 JSON: {e}") from e
            tid = str(d.get("id", "")).strip()
            if not tid:
                raise ValueError(f"{p.name}:{i} 缺 id")
            if tid in seen:
                raise ValueError(f"{p.name}:{i} 任务 id 重复: {tid}（唯一事实源原则）")
            seen.add(tid)
            raw_a = d.get("assertions") or []
            if not isinstance(raw_a, (list, tuple)):
                raise ValueError(f"{p.name}:{i} assertions 必须是列表")
            asserts: list[Assertion] = []
            for j, a in enumerate(raw_a):
                if not isinstance(a, dict):
                    raise ValueError(f"{p.name}:{i} assertions[{j}] 不是对象")
                aid = str(a.get("id", "")).strip()
                if not aid:
                    raise ValueError(f"{p.name}:{i} assertions[{j}] 缺 id")
                if not str(a.get("why", "")).strip():
                    # 没有 why 的断言等于没有断言：判分时它只能说"你错了"，
                    # 说不出"错在哪" —— 那正是这一格要补的东西。
                    raise ValueError(f"{p.name}:{i} assertions[{j}] 缺 why")
                av = a.get("value", ())
                if isinstance(av, list):
                    av = tuple(av)
                asserts.append(Assertion(
                    id=aid, kind=str(a.get("kind", "")), why=str(a.get("why")),
                    value=av))
            tasks.append(Task(
                id=tid,
                prompt=str(d.get("prompt", "")),
                tests=tuple(str(x) for x in d.get("tests", [])),
                oracle=str(d.get("oracle", "")),
                partial=str(d.get("partial", "")),
                null=str(d.get("null", "")),
                meta=tuple(sorted((d.get("meta") or {}).items())),
                assertions=tuple(asserts),
            ))
        if not tasks:
            raise ValueError(f"{p.name} 没有任何任务")
        return Suite(name=p.stem, tasks=tuple(tasks))


# ===========================================================================
# 4. 证据（契约⑤）—— 从哪来 / 有多确定 / 什么没查到
# ===========================================================================
@dataclass
class Evidence:
    """每条结论都要能回答三件事，三者混在一句里 = 无法证伪。"""

    observations: list[str] = field(default_factory=list)   # 实测看到的事实
    inferences: list[str] = field(default_factory=list)     # 由事实推出的判断
    unknowns: list[str] = field(default_factory=list)       # 明确没查到的
    limitations: list[str] = field(default_factory=list)    # 这套证据的适用范围

    def evidence_id(self) -> str:
        """证据 ID = **内容**哈希 ⇒ 证据不可被悄悄改写（改了 ID 就变）。"""
        return _sha_text(_canon({
            "o": self.observations, "i": self.inferences,
            "u": self.unknowns, "l": self.limitations,
        }))[:16]

    def as_text(self) -> str:
        out = [f"evidence_id={self.evidence_id()}"]
        for label, xs in (("观察", self.observations), ("推断", self.inferences),
                          ("未知", self.unknowns), ("局限", self.limitations)):
            if xs:
                out.append(f"  [{label}]")
                out.extend(f"    - {x}" for x in xs)
        return "\n".join(out)


# ===========================================================================
# 5. 判别力自证（契约⑥）—— 先证明尺子能分辨，再拿它量东西
# ===========================================================================
class CalibrationError(RuntimeError):
    """尺子**没有分辨力**。fail-closed：拒绝用它做任何判定。"""


@dataclass
class Calibration:
    ok: bool
    margin: float
    per_task: list[dict] = field(default_factory=list)
    reason: str = ""
    evidence: Evidence | None = None
    middle_n: int = 0   # 做了三档检查的条数（0 ⇒ 中段分辨力是空判据）

    def as_lines(self) -> str:
        head = (f"校准{'通过' if self.ok else '失败'} margin={self.margin:+.4f} "
                f"中段检查={self.middle_n}/{len(self.per_task)}")
        rows = []
        for r in self.per_task:
            mid = "—" if r["partial"] is None else f"partial={r['partial']:.4f}"
            rows.append(f"  {r['id']}: oracle={r['oracle']:.4f} {mid} "
                        f"null={r['null']:.4f} {'✅' if r['ok'] else '❌ 无分辨力'}")
        return "\n".join([head, *rows, f"  理由: {self.reason}"])


def calibrate(scorer: ExternalScorer, suite: Suite, *, min_margin: float = 0.0,
              min_middle: int = 1) -> Calibration:
    """**先**证明这把尺子能分辨好坏，**再**允许它参与任何判定。

    对每条任务，用已知质量的答案去打同一把尺子：
        参考解 oracle（应当高分） ／ 部分解 partial（应当居中） ／ 空答案 null（应当低分）
    要求**逐条**：
        · `score(oracle) > score(null)`                 —— 两极可分
        · 若有 partial：`null < partial < oracle` 严格  —— **中段也有序**（不是二值尺子）
    并且整体 margin > min_margin、中段检查条数 ≥ min_middle。

    为什么不满足于两极：
        V5 的适应度函数 54/54 恒等于 0 —— 那是一把**没有分辨力**的尺子。
        但只证明"满分可分于零分"同样弱：一把"要么 0 要么 1"的二值尺子，
        照样会让所有"改进"看起来纹丝不动。**中段有序**才是真正的分辨力。

    `min_middle` 默认 1：一条中段检查都没做 ⇒ 该判据从未被触发
    ⇒ 拿它当"证明"是零判别力的（"触发 0 次就不要拿分数说话"）。

    失败 ⇒ fail-closed（契约③"判定从严"）：不返回一个可以糊弄过去的 0，抛 CalibrationError。
    """
    per_task: list[dict] = []
    obs: list[str] = []
    inf: list[str] = []
    unk: list[str] = []
    o_scores: list[float] = []
    n_scores: list[float] = []
    bad: list[str] = []
    bad_mid: list[str] = []
    middle_n = 0

    for t in suite.tasks:
        so = scorer.score(t.as_dict(), t.oracle)
        sn = scorer.score(t.as_dict(), t.null)
        sp = scorer.score(t.as_dict(), t.partial) if t.partial.strip() else None

        good = so.score > sn.score
        if sp is not None:
            middle_n += 1
            mono = sn.score < sp.score < so.score
            if not mono:
                bad_mid.append(t.id)
        else:
            mono = True

        per_task.append({"id": t.id, "oracle": so.score,
                         "partial": (sp.score if sp is not None else None),
                         "null": sn.score, "ok": bool(good and mono)})
        o_scores.append(so.score)
        n_scores.append(sn.score)
        if not good:
            bad.append(t.id)
        obs.append(f"{t.id}: oracle={so.score:.4f} "
                   f"partial={'—' if sp is None else f'{sp.score:.4f}'} "
                   f"null={sn.score:.4f}")

    margin = (sum(o_scores) / len(o_scores)) - (sum(n_scores) / len(n_scores))

    if bad:
        inf.append(f"尺子 {scorer.name} 在 {len(bad)} 条任务上分不出好坏: {bad}")
    else:
        inf.append(f"尺子 {scorer.name} 在 {len(suite)} 条任务上两极可分")
    if middle_n:
        inf.append(f"中段有序检查 {middle_n} 条，全部通过" if not bad_mid
                   else f"中段有序检查 {middle_n} 条，其中无序: {bad_mid}")

    inf.append(f"分辨力 margin={margin:+.4f}（门槛 {min_margin:+.4f}）")
    unk.append("未测：两个**都接近满分**的答案（如 0.9 与 0.95）是否可分 —— "
               "校准用的是间距很大的档位")
    unk.append("未测：真实候选的得分分布（校准用人工构造的三档，不代表模型实际分布）")

    ev = Evidence(
        observations=obs,
        inferences=inf,
        unknowns=unk,
        limitations=[
            "校准覆盖 好/中/坏 三档 ⇒ 保证'能分出对错'与'中段有序'，"
            "但不保证任意两个相近分数可分",
            f"尺子身份 scorer_sha={scorer.sha()[:12]}、suite_sha={suite.sha()[:12]}；"
            "换任一者本校准即失效，必须重做",
        ],
    )

    if bad:
        reason = f"{len(bad)}/{len(suite)} 条任务上 参考解 未胜过 空答案 ⇒ 尺子无分辨力"
    elif bad_mid:
        reason = f"{len(bad_mid)} 条任务上 中段无序（partial 不在 null 与 oracle 之间）: {bad_mid}"
    elif middle_n < min_middle:
        reason = f"中段检查条数 {middle_n} < 要求 {min_middle} ⇒ 中段分辨力未被证明"
    elif margin <= min_margin:
        reason = f"整体 margin {margin:+.4f} 未超过门槛 {min_margin:+.4f}"
    else:
        reason = (f"全部 {len(suite)} 条两极可分，其中 {middle_n} 条中段有序；"
                  f"margin {margin:+.4f} > {min_margin:+.4f}")

    ok = (not bad) and (not bad_mid) and (middle_n >= min_middle) and (margin > min_margin)
    return Calibration(ok=ok, margin=margin, per_task=per_task, reason=reason,
                       evidence=ev, middle_n=middle_n)


def calibrate_or_raise(scorer: ExternalScorer, suite: Suite, **kw: Any) -> Calibration:
    """校准 + fail-closed。**不做校准就不许用它出任何结论。**"""
    cal = calibrate(scorer, suite, **kw)
    if not cal.ok:
        raise CalibrationError(f"尺子不可用：{cal.reason}\n{cal.as_lines()}")
    return cal


# ===========================================================================
# 6. 跑任务集（契约④）—— 计数打在「跑完了」上
# ===========================================================================
@dataclass
class BenchReport:
    """消费/尝试计数。契约④：**两本账分开记、分开报，绝不合并成单一布尔。**

    最小可对账形态：
        attempted == recorded + skipped_no_answer + failed_scorer

    ⛔ 两个早退分支必须分开计：`skipped_no_answer`（上游没产出答案，改 solver）
       与 `failed_scorer`（尺子/环境出问题，改 scorer）**修法完全不同**，
       合并成一个 `skipped` 就再也分不清。
    """

    candidate: str
    budget_tag: str
    attempted: int = 0
    recorded: int = 0
    skipped_no_answer: int = 0
    failed_scorer: int = 0
    errors: list[str] = field(default_factory=list)
    per_task: list[dict] = field(default_factory=list)
    elapsed_s: float = 0.0
    #: solver **自称之外**的事实：它实际用了什么通道（llm_kind / 外部能力数 / 模型名…）。
    #:
    #: 为什么必须有这个字段：`candidate` 是一串**自由文本**，它可以声称"真模型 qwen2.5:7b"
    #: 而实际跑的是离线桩 —— 2026-10-17 的真实事故就是这样：标签写"真模型"，
    #: 内核日志写 `llm=stub(离线)`，两者矛盾而**没有任何机制发现它**，读数还标着"可信=✅"。
    #: 所以判定不能只看"有没有早退"，必须能读到**血缘**。
    provenance: dict = field(default_factory=dict)

    def llm_of_record(self) -> str:
        """实际使用的模型通道；取不到就是 'unknown'（**不是**默认成 http）。"""
        v = self.provenance.get("llm_kind")
        return str(v) if v else "unknown"

    def balanced(self) -> bool:
        """账本是否平。不平 ⇒ 有分数没被计入 ⇒ 所有读数作废。"""
        return self.attempted == self.recorded + self.skipped_no_answer + self.failed_scorer

    def trustworthy(self) -> bool:
        """**可信**的读数要求账平 **且** 没有任何一种早退。

        用"早退 = 0"而不是"≥ 某阈值"：任何一次没量到，都可能正好落在
        判定边界上，而判定边界正是我们唯一关心的地方。
        """
        return self.balanced() and self.skipped_no_answer == 0 and self.failed_scorer == 0

    def summary_line(self) -> str:
        return (f"attempted={self.attempted} recorded={self.recorded} "
                f"skipped_no_answer={self.skipped_no_answer} "
                f"failed_scorer={self.failed_scorer} "
                f"对账={'✅' if self.balanced() else '❌'} "
                f"可信={'✅' if self.trustworthy() else '❌'}")

    def as_dict(self) -> dict:
        return {
            "candidate": self.candidate, "budget_tag": self.budget_tag,
            "attempted": self.attempted, "recorded": self.recorded,
            "skipped_no_answer": self.skipped_no_answer,
            "failed_scorer": self.failed_scorer,
            "errors": self.errors[:20], "elapsed_s": round(self.elapsed_s, 3),
            "llm_of_record": self.llm_of_record(),
            "provenance": self.provenance,
            # 逐题逐次明细 —— **不截断**（不像 errors 那样只留前 20 条）。
            # 理由：它是噪声归因的唯一依据。若只落一个总分极差，你就无法回答
            # "抖动出在哪些题上"，也无法区分"整卷漂移"与"两题涨两题跌相互抵消"。
            # V5 的 0.0862 正是这样变成一个孤立数字的：没人知道它从哪来。
            "per_task": self.per_task,
        }


def run_suite(
    solver: Callable[[Task], str],
    scorer: ExternalScorer,
    suite: Suite,
    *,
    repeat_n: int = 1,
    budget_tag: str = "?",
    candidate: str = "candidate",
    provenance: Callable[[], dict] | None = None,
    critique_ledger: CritiqueLedger | None = None,
) -> tuple[Measurement, BenchReport]:
    """把一个 solver 在同一预算档上跑 repeat_n 次，每轮对每条任务判分。

    分数口径**故意保守**：每条任务等权，量到的算实分，没量到的记 0
    （而不是把没量到的从分母里剔除）。理由：剔除会系统性高估 ——
    恰好是"缺失被读成否定"的反面错误，"缺失被读成不存在"。
    代价是读数会被压低，所以 `BenchReport.trustworthy()` 必须同时为真，
    否则这个 Measurement **不得**用于任何判定。

    `provenance` 是一个**无参回调**，在跑完之后调用一次，用来把"实际用了什么"
    写进读数（而不是让 candidate 那串自由文本自说自话）。取不到就留空字典，
    由调用方决定空血缘是否可接受 —— 库不做假设。
    """
    rep = BenchReport(candidate=candidate, budget_tag=budget_tag)
    t0 = time.time()
    scores: list[float] = []
    n_tasks = len(suite.tasks)
    if n_tasks == 0:
        raise ValueError("空任务集")

    for r in range(repeat_n):
        total = 0.0
        for t in suite.tasks:
            rep.attempted += 1
            try:
                ans = solver(t)
            except Exception as e:  # noqa: BLE001 —— solver 崩了不该带走整轮
                rep.skipped_no_answer += 1
                rep.errors.append(f"[r{r+1}] {t.id}: solver 异常 {type(e).__name__}: {e}")
                continue
            if not str(ans or "").strip():
                rep.skipped_no_answer += 1
                rep.errors.append(f"[r{r+1}] {t.id}: solver 未产出答案")
                continue
            try:
                sc = scorer.score(t.as_dict(), str(ans))
            except ScoreError as e:
                rep.failed_scorer += 1
                rep.errors.append(f"[r{r+1}] {t.id}: {e}")
                continue
            rep.recorded += 1
            total += sc.score
            rep.per_task.append({"rep": r + 1, "id": t.id, "score": sc.score,
                                 "why": sc.why[:200]})
            # ── 外部评审落盘 ──────────────────────────────────────────
            # 打分器给的**理由**（`why`）此前只有一个去处：写进日志给人看。
            # 现在它进账本，成为下一轮可以被内核读取的只读输入。
            #
            # ⚠️ 这里**只落盘、不投喂**：在同一轮 suite 里把第 1 题的理由喂给
            #    第 6 题，第 6 题就不再是同一条件下的测量了 —— `repeat` 的意义
            #    当场消失。投喂由调用方显式决定（`run.py --use-critique`），
            #    那是接线验证，不是能力测量。
            if critique_ledger is not None:
                c = from_scored(scorer_sha=scorer.sha(), suite_sha=suite.sha(),
                                task_id=t.id, family=suite.name,
                                score=sc.score, why=sc.why, answer=str(ans))
                if c is not None:
                    critique_ledger.add(c)
        scores.append(total / n_tasks)

    rep.elapsed_s = time.time() - t0
    if provenance is not None:
        try:
            rep.provenance = dict(provenance() or {})
        except Exception as e:  # noqa: BLE001
            # 血缘取不到**不能**让整轮崩，但必须留痕：空血缘 = 无法证伪标签
            rep.errors.append(f"provenance 取用失败: {type(e).__name__}: {e}")
    m = summarize(scores, candidate=candidate, budget_tag=budget_tag)
    return m, rep


# ===========================================================================
# 7. 口径闸 + 基线读写（契约①）
# ===========================================================================
def measurement_to_dict(m: Measurement) -> dict:
    return {"candidate": m.candidate, "budget_tag": m.budget_tag, "n": m.n,
            "mean": m.mean, "std": m.std, "lo": m.lo, "hi": m.hi}


def measurement_from_dict(d: dict) -> Measurement:
    return Measurement(candidate=str(d["candidate"]), budget_tag=str(d["budget_tag"]),
                       n=int(d["n"]), mean=float(d["mean"]), std=float(d["std"]),
                       lo=float(d["lo"]), hi=float(d["hi"]))


def _atomic_write(path: Path, text: str) -> None:
    """原子写（契约① 纪律 4）：半截文件会变成一把谁也说不清的尺子。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class BaselineStore:
    """基线的**唯一**读写通道。内核侧没有任何其他写入口。

    存的是 {"caliber": {...}, "measurement": {...}, "saved_at": ...}。
    """

    path: Path

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def load_measurement(self) -> Measurement | None:
        d = self.load()
        if not d or "measurement" not in d:
            return None
        try:
            return measurement_from_dict(d["measurement"])
        except (KeyError, TypeError, ValueError):
            return None

    def load_caliber(self) -> Caliber | None:
        d = self.load()
        if not d or "caliber" not in d:
            return None
        return caliber_from_dict(d["caliber"])

    def save(self, m: Measurement, caliber: Caliber, *,
             allow_supersede: bool = False) -> tuple[bool, str]:
        """写入基线。**口径不一致时拒绝并保持盘面不动**（契约①）。

        返回 (是否写入, 说明)。被拒绝时文件**一个字节都不变** ——
        这是"标准不可被被测者改写"的物理实现。
        """
        old = self.load()
        if old is not None:
            old_cal = self.load_caliber()
            if old_cal is not None and not old_cal.same_as(caliber):
                fields = old_cal.diff(caliber)
                if not allow_supersede:
                    return False, (
                        f"口径不一致，拒绝覆盖（差异字段 {fields}）。"
                        f"旧 {old_cal.as_line()} / 新 {caliber.as_line()}。"
                        f"跨口径的两个数字不可比；确需替换请显式传 allow_supersede。"
                    )
                # 显式允许 ⇒ 旧值**另存**，不销毁
                stamp = time.strftime("%Y%m%d%H%M%S")
                sup = self.path.with_name(f"{self.path.stem}_superseded_{stamp}.json")
                payload = dict(old)
                payload["superseded"] = True
                payload["superseded_reason"] = f"口径变更: {fields}"
                _atomic_write(sup, _canon(payload))

        body = _canon({
            "caliber": caliber.as_dict(),
            "caliber_sig": caliber.signature(),
            "measurement": measurement_to_dict(m),
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        _atomic_write(self.path, body)
        return True, f"已写入 {self.path.name}（{caliber.signature()}）"


def verdict_against_baseline(base: Measurement, cand: Measurement, *,
                             min_gain: float = 0.0) -> tuple[bool, str]:
    """拿新读数对基线做判定。同源工具在 measure.paired_compare —— 不另写一份判据。

    ⛔ 判据必须与被测对象同源：另写一份判分逻辑，实测能把结论带反。
    """
    from .measure import paired_compare
    return paired_compare(base, cand, min_gain=min_gain)


# ===========================================================================
# 8. 落盘出口（契约④ 最后一步）—— 内存态计数必须能被别的进程读到
# ===========================================================================
def append_jsonl(path: Path, record: dict) -> None:
    """把读数追加落盘。没有落盘出口的计数只是**进程内内存态**：
    重启清零、跨进程不可见，拿它做诊断只能看个热闹。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(_canon(record) + "\n")
