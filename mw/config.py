"""单一配置源。

V5 教训：同一事实声明在 4~5 处，每加一个东西就要同步改 N 处，漏一处就是静默漂移。
所以 V1 里**所有**目录、上限、开关都只在这个文件里出现一次，别处一律 `from .config import`。

所有状态目录都可用环境变量覆盖 —— 这是 V5 用血换来的规则（R4/R8）：
测试绝不能写生产目录。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_path(key: str, default: Path) -> Path:
    raw = os.environ.get(key)
    return Path(raw).expanduser().resolve() if raw else default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    return v if v > 0 else default


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        v = float(raw)
    except ValueError:
        return default
    return v if v > 0 else default


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Paths:
    root: Path = ROOT
    state: Path = field(default_factory=lambda: _env_path("V1_STATE_DIR", ROOT / "state"))
    logs: Path = field(default_factory=lambda: _env_path("V1_LOG_DIR", ROOT / "logs"))
    workspace: Path = field(default_factory=lambda: _env_path("V1_WORKSPACE", ROOT / "workspace"))

    @property
    def db(self) -> Path:
        """三层记忆全部落在这个 SQLite 文件里（stdlib，零依赖）。"""
        return self.state / "memory.db"

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
    """

    base_url: str = os.environ.get("V1_LLM_BASE_URL", "https://api.openai.com/v1")
    api_key: str = os.environ.get("V1_LLM_API_KEY", "")
    model: str = os.environ.get("V1_LLM_MODEL", "gpt-4o-mini")
    timeout_s: float = _env_float("V1_LLM_TIMEOUT_S", 120.0)
    offline: bool = os.environ.get("V1_LLM_OFFLINE", "") not in ("", "0", "false")

    def usable(self) -> bool:
        return bool(self.api_key) and not self.offline


@dataclass(frozen=True)
class Limits:
    """硬上限。ACT 原则：内核不许无界思考。

    默认值刻意小 —— 小到**跑一次只要几秒**，这样它才会被真的跑。
    一次跑十分钟的内核，最后一定没人跑。
    """

    max_steps: int = _env_int("V1_MAX_STEPS", 12)
    max_llm_calls: int = _env_int("V1_MAX_LLM_CALLS", 24)
    max_candidates: int = _env_int("V1_MAX_CANDIDATES", 3)
    max_wall_s: float = _env_float("V1_MAX_WALL_S", 120.0)
    max_consecutive_failures: int = _env_int("V1_MAX_CONSECUTIVE_FAILURES", 3)
    working_memory_items: int = _env_int("V1_WORKING_MEMORY_ITEMS", 24)
    recall_k: int = _env_int("V1_RECALL_K", 6)


@dataclass(frozen=True)
class Settings:
    paths: Paths = field(default_factory=Paths)
    llm: LLMConfig = field(default_factory=LLMConfig)
    limits: Limits = field(default_factory=Limits)


def settings() -> Settings:
    """每次调用都重新读 env —— 便于测试就地构造隔离环境。"""
    return Settings()


# ---- 环境变量清单（唯一登记处）------------------------------------------------
# 任何新增 env 都必须在这里登记，测试 H 组会核验「登记 == 代码实际读取」。
ENV_KEYS = (
    "V1_STATE_DIR",
    "V1_LOG_DIR",
    "V1_WORKSPACE",
    "V1_LLM_BASE_URL",
    "V1_LLM_API_KEY",
    "V1_LLM_MODEL",
    "V1_LLM_TIMEOUT_S",
    "V1_LLM_OFFLINE",
    "V1_MAX_STEPS",
    "V1_MAX_LLM_CALLS",
    "V1_MAX_CANDIDATES",
    "V1_MAX_WALL_S",
    "V1_MAX_CONSECUTIVE_FAILURES",
    "V1_WORKING_MEMORY_ITEMS",
    "V1_RECALL_K",
)
