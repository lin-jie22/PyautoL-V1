"""单一配置源。

一条教训：同一事实若声明在 4~5 处，每加一个东西就要同步改 N 处，漏一处就是静默漂移。
所以 V1 里**所有**目录、上限、开关都只在这个文件里出现一次，别处一律 `from .config import`。

所有状态目录都可用环境变量覆盖 —— 这是一条用事故换来的规则：
测试绝不能写生产目录。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from urllib.parse import urlsplit


def _env_path(key: str, default: Path) -> Path:
    raw = os.environ.get(key)
    return Path(raw).expanduser().resolve() if raw else default


def _env_int(key: str, default: int, *, allow_zero: bool = False) -> int:
    """读整数 env。

    ⚠️ `allow_zero` 不是可选的锦上添花，它是一个**实测事故的修复**（2026-10-17）：
    原来两个读取器都写着 `return v if v > 0 else default` —— 于是**配置里的 0
    被静默改写成默认值**。踩中的是三个最要命的取值：
      · `V1_LLM_TEMPERATURE=0`（贪心 ⇒ 可复现的基线）→ 被静默改成 0.2；
      · `V1_CLOSE_RESERVE_S=0`（关闭收尾余量）→ 被静默改回 45.0；
      · `V1_LLM_RETRIES=0` / `V1_CLOSE_RESERVE_STEPS=0`（关闭）→ 被改回默认。
    症状极具欺骗性：口径签名里写着 `temperature=0.0`，实际线上是 `0.2`；
    "同口径两轮"因此给出 `0.5000` / `0.6667` —— 又一次**标签撒谎**。
    ⇒ 哪些键允许 0 必须**显式声明**（见 `ZERO_OK_KEYS`），不能靠一条全局策略猜。
    """
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    return v if (v >= 0 if allow_zero else v > 0) else default


def _env_float(key: str, default: float, *, allow_zero: bool = False) -> float:
    """读浮点 env。`allow_zero` 的理由见 `_env_int`（同一场事故）。"""
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        v = float(raw)
    except ValueError:
        return default
    return v if (v >= 0.0 if allow_zero else v > 0.0) else default


def _env_bool(key: str, default: bool) -> bool:
    """读布尔 env：`0`/`false`（不分大小写）⇒ False，其余非空 ⇒ True。

    ⛔ 只有"明确写 0/false"才关。空串 / 写错值都回默认 ——
    跟 `_env_int` 的纪律一致：解析失败不是"关"，是"没配"。
    （"false" 被 int() 拒收的教训在这里同样适用：不能用 _env_int 读布尔的理由
     是 `V1_...=false` 会静默回默认，而布尔的默认恰恰是用户想改的值。）
    """
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in ("0", "false")


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Paths:
    root: Path = ROOT
    state: Path = field(default_factory=lambda: _env_path("V1_STATE_DIR", ROOT / "state"))
    logs: Path = field(default_factory=lambda: _env_path("V1_LOG_DIR", ROOT / "logs"))
    workspace: Path = field(default_factory=lambda: _env_path("V1_WORKSPACE", ROOT / "workspace"))
    # 外部锚层（基线 / 读数落盘）的目录。
    # ⚠️ 刻意**不进** ensure()：不写基线就不该创建这个目录 ——
    #    否则任何构造 Core 的代码（包括测试）都会顺手创建它，违反"测试不碰生产目录"。
    #    真正的创建点在 bench._atomic_write / bench.append_jsonl。
    bench: Path = field(default_factory=lambda: _env_path("V1_BENCH_DIR", ROOT / "bench_out"))

    @property
    def db(self) -> Path:
        """三层记忆全部落在这个 SQLite 文件里（stdlib，零依赖）。"""
        return self.state / "memory.db"

    @property
    def score_ledger(self) -> Path:
        """跨会话分数账本（自我模型的唯一证据来源）。

        ⚠️ 默认落在 `state/` 下，但**不进 `ensure()`** —— 与 `bench` 同理：
        不写分数就不该创建这个文件（测试绝不碰生产目录）。
        真正的创建点在 `ScoreLedger.append`。

        ⚠️ 刻意**不新增**一个 `V1_SCORE_LEDGER` env：它派生自 `state`，
        于是设了 `V1_STATE_DIR` 的测试自动就隔离了。多一个 env 就多一处
        "忘了设 ⇒ 写到生产目录"的入口。
        """
        return self.state / "score_ledger.jsonl"

    @property
    def log_file(self) -> Path:
        return self.logs / "core.log"

    def ensure(self) -> "Paths":
        for p in (self.state, self.logs, self.workspace):
            p.mkdir(parents=True, exist_ok=True)
        return self


@dataclass(frozen=True)
class LLMConfig:
    """OpenAI 兼容端点。零 SDK：走 stdlib 的 urllib。

    `offline=True` 时内核不碰网络，改用 llm.StubLLM —— 这让整个循环
    **可以在无 API key、无网络的情况下被完整跑通和验证**。

    ⚠️ 这五个字段必须用 `default_factory`，**不能**写成类级默认值。
    类级默认值（`api_key: str = os.environ.get(...)`）在**模块被 import 时**
    求值一次就定型了 —— 于是 `settings()` 文档里那句"每次调用都重新读 env"
    对 LLM 字段是假的：import 之后再设 `V1_LLM_API_KEY` 一律无效。
    `Paths` 一直用的是 default_factory，两边口径必须一致（否则又是一个
    "同一事实两种读法"的漂移源）。
    """

    base_url: str = field(
        default_factory=lambda: os.environ.get("V1_LLM_BASE_URL", "https://api.openai.com/v1"))
    api_key: str = field(
        default_factory=lambda: os.environ.get("V1_LLM_API_KEY", ""))
    model: str = field(
        default_factory=lambda: os.environ.get("V1_LLM_MODEL", "gpt-4o-mini"))
    timeout_s: float = field(
        default_factory=lambda: _env_float("V1_LLM_TIMEOUT_S", 120.0))
    #: 采样温度。**必须在这里**，不能靠调用方每次显式传。
    #:
    #: ⚠️ 实测事故（2026-10-17）：内核的三个调用点（planner ×2、worldmodel ×1）
    #:    没有一个传 temperature，于是全部落到 `HTTPLLM.complete` 的默认 `0.2`。
    #:    后果有两层，都很严重：
    #:      ① `--temperature 0` 对内核路径**完全无效** —— 你以为在跑贪心，
    #:         实际一直在 0.2 采样；
    #:      ② 口径签名里写着 `temperature=0.0`，而实际是 `0.2` ⇒ **标签撒谎**，
    #:         口径闸会拿一个"假 T=0 的内核读数"去和"真 T=0 的直连读数"比较。
    #:    实测症状：记忆完全隔离、口径签名逐位相同，同一题同一档两轮仍给出
    #:    `0.5000` / `0.6667`。现在温度进 `LLMConfig`（测量前就固定的条件），
    #:    由 HTTPLLM 统一取用 —— 调用方漏传也不会再悄悄换口径。
    temperature: float = field(
        default_factory=lambda: _env_float("V1_LLM_TEMPERATURE", 0.2,
                                         allow_zero=True))
    offline: bool = field(
        default_factory=lambda: os.environ.get("V1_LLM_OFFLINE", "") not in ("", "0", "false"))

    def usable(self) -> bool:
        return bool(self.api_key) and not self.offline

    def cond(self) -> dict:
        """模型条件快照 —— 口径签名的输入之一（见 `mw.bench.CALIBER_KEYS`）。

        ⛔ **绝不含 `api_key`**：它敏感，且改了它不该改变"这把尺子量的是什么"。
        `temperature` 也不在这里 —— 它是 `Caliber` 的独立字段（实测事故最多的一项），
        同一事实只声明一处，不放第二遍。
        """
        try:
            host = urlsplit(self.base_url).netloc or self.base_url
        except ValueError:
            host = self.base_url
        return {"model": self.model, "endpoint": host,
                "timeout_s": self.timeout_s, "offline": self.offline}


@dataclass(frozen=True)
class Limits:
    """硬上限。ACT 原则：内核不许无界思考。

    默认值刻意小 —— 小到**跑一次只要几秒**，这样它才会被真的跑。
    一次跑十分钟的内核，最后一定没人跑。

    ⚠️ 全部字段用 `field(default_factory=...)` 而**不是**类级默认值。
    类级默认值在 **import 时**求值一次，于是：
      · `settings()` 文档里承诺的"每次调用都重新读 env"对 `Limits` 就是假的；
      · `V1_LLM_RETRIES=0` / `V1_CLOSE_RESERVE_S=0` 这类"运行前才设"的配置**永远读不到**。
    契约 T5 就是实测这一点的（它第一次跑时 5 个键里有 4 个不生效）。
    """

    max_steps: int = field(
        default_factory=lambda: _env_int("V1_MAX_STEPS", 12))
    max_llm_calls: int = field(
        default_factory=lambda: _env_int("V1_MAX_LLM_CALLS", 24))
    max_candidates: int = field(
        default_factory=lambda: _env_int("V1_MAX_CANDIDATES", 3))
    max_wall_s: float = field(
        default_factory=lambda: _env_float("V1_MAX_WALL_S", 120.0))
    max_consecutive_failures: int = field(
        default_factory=lambda: _env_int("V1_MAX_CONSECUTIVE_FAILURES", 3))
    #: 连续多少轮"没有任何新动作"就判定停滞并收尾。
    #: 为什么需要它：`max_consecutive_failures` 只数**失败**，而实测里
    #: 一次任务把同一个 `recall` 重派了 5 次 —— 那 5 次**全都成功**，
    #: 于是没有任何机制拦它，最后烧光 120s 预算才停。
    #: **重复的"成功"不是进展**，必须单独表达。
    max_stall: int = field(
        default_factory=lambda: _env_int("V1_MAX_STALL", 2))
    #: 收尾余量：预算剩这么少时**必须**转向交付，不许继续探索。
    #: 实测背景：有一题把 120s 全花在 recall 上，最后 `budget_exhausted`
    #: —— 交付了**零**东西。探索是手段，交付才是目的；
    #: 一次模型调用约 20s，所以留的余量至少够一次"专门索要答案"的调用。
    close_reserve_s: float = field(
        default_factory=lambda: _env_float("V1_CLOSE_RESERVE_S", 45.0, allow_zero=True))
    close_reserve_steps: int = field(
        default_factory=lambda: _env_int("V1_CLOSE_RESERVE_STEPS", 1, allow_zero=True))
    working_memory_items: int = field(
        default_factory=lambda: _env_int("V1_WORKING_MEMORY_ITEMS", 24))
    recall_k: int = field(
        default_factory=lambda: _env_int("V1_RECALL_K", 6))
    #: 单次 LLM 调用的 token 上限。**必须可配**，不能散落在各调用点写死。
    #: 实测教训（2026-10-17）：规划器内部写死 700，而它要求模型把**整段代码**
    #: 放进 JSON 字符串 —— 响应被截断在半截 token 上，于是看起来像"模型不会写 JSON"，
    #: 实际是**预算给少了**。这两种病修法完全不同（一个调 token，一个改提示词），
    #: 而当时的日志把它们合并成同一个"不可解析"。
    llm_max_tokens: int = field(
        default_factory=lambda: _env_int("V1_LLM_MAX_TOKENS", 2048))
    #: 传输层失败的**有界重试**次数（超时 / 连接断 / 5xx / 429）。
    #: 实测背景（2026-10-17 4x 预算跑内核）：6 题里 3 题死于 `TimeoutError`，
    #: 于是那一次的读数里混着**基础设施**的锅，报错文本却写着
    #: `solver 未产出答案` —— 读起来像模型的锅。判据必须分开：
    #:   · 传输层失败 ⇒ 重试（同一请求，幂等的只读调用，重试不改变语义）
    #:   · 模型侧失败（4xx / 响应结构不符 / JSON 解析不出）⇒ **不许重试**，换路径
    #: 取 0 = 关闭重试。
    llm_retries: int = field(
        default_factory=lambda: _env_int("V1_LLM_RETRIES", 2, allow_zero=True))
    #: 传输层重试之间的等待秒数（线性增长：0.5s、1.0s、1.5s…）。
    llm_retry_backoff_s: float = field(
        default_factory=lambda: _env_float("V1_LLM_RETRY_BACKOFF_S", 0.5, allow_zero=True))
    #: 自检最多打回重写几次（第二十一轮，`mw/verify.py`）。取 0 = 关闭自检。
    #:
    #: ⚠️ 为什么它是一个**上限**而不是"无限重写"：自检只挡结构性错误，
    #:    一个答不对的题可以永远写得"结构正确"。没有上限，"重写"就会变成
    #:    第三种烧预算的方式 —— 而它烧掉的是**交付**的配额（`_closing_reserve` 的教训）。
    #: ⚠️ 为什么它必须进 `Limits` 而不是写死在 `core.py`：`Limits.as_dict()` 是
    #:    口径签名的输入之一。开/关自检跑出来的分数**不是同一个候选** ——
    #:    不进制口径，两轮就能被 `paired_compare` 判成"可比"，那是又一次尺子下移。
    verify_retries: int = field(
        default_factory=lambda: _env_int("V1_VERIFY_RETRIES", 2, allow_zero=True))
    #: 指定一个**外部能力**作为答案的第一来源（第二十三轮，路线①）。
    #: 空字符串 = 关（默认）：内核的行为与之前**逐位相同**。
    #:
    #: 为什么它是配置而不是"让规划器自己选"：
    #:   第十三轮已经证实过 —— 交给模型选，机制会恒 0 次触发，而读数只说
    #:   "没被选到"，看起来像"外部能力没用"。位置必须写死（与 `_gate_finish`
    #:   写死在 `finish` 出口同宗理由）。
    #: ⚠️ 它必须进口径签名（`as_dict` 自动包含）：开/关外部能力跑出来的分数
    #:    不是同一个候选 —— 不进制就会让 `paired_compare` 判成"可比"。
    external_answer_tool: str = field(
        default_factory=lambda: os.environ.get("V1_EXTERNAL_ANSWER_TOOL", "").strip())
    #: 是否允许模型在运行期调用 `note` **写**语义记忆（第二十八轮，§17-6 6c）。
    #:
    #: 为什么必须可关（实测逼出来的）：三臂记忆实验里，空库臂在跑的过程中
    #: 被模型自己调 `note` 写回了 facts —— "种子是唯一处理"的前提被运行期
    #: 写入混杂，三臂不再可比。关掉它 ⇒ 播种内容是唯一的 facts 来源，
    #: 送达（recall 兜底 top-k）成为确定性事件。只读的 `recall` 不受影响。
    #:
    #: ⚠️ 必须进 `Limits`（⇒ 进口径签名）：开/关注入语义记忆跑出来的分数
    #:    不是同一个候选，不进制就会让 `paired_compare` 判成"可比"。
    allow_note: bool = field(
        default_factory=lambda: _env_bool("V1_ALLOW_NOTE", True))
    #: 是否允许世界模型把可教学的失配**沉淀成语义事实**（第二十八轮，§17-6 6c）。
    #:
    #: 这是运行期写语义层的**第二条**通道（第一条 `note`，见 `allow_note`）：
    #: 预测失配 → `worldmodel.learn()` → `commit_fact(source="worldmodel")`。
    #: 三臂记忆实验里空库臂被它写回了 facts ⇒ 处理被混杂。关掉它 ⇒ 失配仍记
    #: 情景（不进检索），只是不进语义层 —— "种子是唯一 facts 来源"才成立。
    #: ⚠️ 与 `allow_note` 同理必须进 `Limits`（口径签名）。
    allow_learn: bool = field(
        default_factory=lambda: _env_bool("V1_ALLOW_LEARN", True))

    def as_dict(self) -> dict:
        """硬上限的**完整**快照 —— 口径签名的输入之一。

        为什么要有它、而不是让调用方自己拼：`budget_tag` 只是**倍数**，
        乘在**绝对值**上。两次运行可以都写 `4x`，却因为 `V1_MAX_STEPS`
        不同而跑出完全不同的预算 —— 那种"同口径"是假的。
        所以全部绝对值一起进闸，而"有哪些字段"只该在这一处声明。
        """
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True)
class Settings:
    paths: Paths = field(default_factory=Paths)
    llm: LLMConfig = field(default_factory=LLMConfig)
    limits: Limits = field(default_factory=Limits)


def settings() -> Settings:
    """每次调用都重新读 env —— 便于测试就地构造隔离环境。"""
    return Settings()


# ---- 上下文预算（第二十轮）--------------------------------------------------
#: 拼给规划器的**状态字符串**最多多少字符。
#:
#: ⚠️ 这个常量原本不存在 —— 它硬编码在 `planner.propose()` 里写成 `state[:1200]`。
#:    那是一个**没人声明的第二个漏斗**：内核把状态拼好了，规划器在最后一米
#:    又静默剪一刀，而被剪掉的正好是拼在后面的块。
#:    第二十轮实测就是这样翻车的：按需回看把正文放进状态尾部，规划器一剪，
#:    正文一个字都没进模型 —— 读数上"取回成功"，实际"没看见"。
#:    ⇒ 提成常量并让两侧共读，`core._state()` 负责在这个预算内**按优先级**取舍。
STATE_CHAR_CAP = 1600


# ---- 环境变量清单（唯一登记处）------------------------------------------------
# 任何新增 env 都必须在这里登记，测试 H 组会核验「登记 == 代码实际读取」。
ENV_KEYS = (
    "V1_STATE_DIR",
    "V1_LOG_DIR",
    "V1_WORKSPACE",
    "V1_BENCH_DIR",
    "V1_LLM_BASE_URL",
    "V1_LLM_API_KEY",
    "V1_LLM_MODEL",
    "V1_LLM_TIMEOUT_S",
    "V1_LLM_TEMPERATURE",
    "V1_LLM_OFFLINE",
    "V1_MAX_STEPS",
    "V1_MAX_LLM_CALLS",
    "V1_MAX_CANDIDATES",
    "V1_MAX_WALL_S",
    "V1_MAX_CONSECUTIVE_FAILURES",
    "V1_WORKING_MEMORY_ITEMS",
    "V1_RECALL_K",
    "V1_LLM_MAX_TOKENS",
    "V1_MAX_STALL",
    "V1_CLOSE_RESERVE_S",
    "V1_CLOSE_RESERVE_STEPS",
    "V1_LLM_RETRIES",
    "V1_LLM_RETRY_BACKOFF_S",
    "V1_VERIFY_RETRIES",
    "V1_EXTERNAL_ANSWER_TOOL",
    "V1_ALLOW_NOTE",
    "V1_ALLOW_LEARN",
)

#: **允许取 0** 的 env 键（唯一事实源）。
#:
#: 其余键沿用"0 视为未设置 ⇒ 回默认"的老策略；这些键不行，因为 0 在这里
#: 是一个**有意义的值**，不是"没填"：
#:   · `V1_LLM_TEMPERATURE=0`       贪心解码 —— 可复现基线的唯一入口
#:   · `V1_CLOSE_RESERVE_S=0`       关闭收尾余量（纯预算耗尽路径）
#:   · `V1_CLOSE_RESERVE_STEPS=0`   同上（步数口径）
#:   · `V1_LLM_RETRIES=0`           关闭传输层重试
#:   · `V1_LLM_RETRY_BACKOFF_S=0`   不退避（测试与本地端点）
#:   · `V1_VERIFY_RETRIES=0`        关闭自检（对照组：开/关自检必须能被分开跑）
#: 实测：这五个里前四个都曾被静默改写成默认值，而口径签名照样宣称理想取值。
ZERO_OK_KEYS: tuple[str, ...] = (
    "V1_LLM_TEMPERATURE",
    "V1_CLOSE_RESERVE_S",
    "V1_CLOSE_RESERVE_STEPS",
    "V1_LLM_RETRIES",
    "V1_LLM_RETRY_BACKOFF_S",
    "V1_VERIFY_RETRIES",
)
