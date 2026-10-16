"""LLM 接口 —— 零依赖。

不用 openai SDK、不用 requests、不用 httpx。只用 stdlib 的 urllib + json。
理由不是清教徒式的洁癖，是**可验证性**：任何一个第三方依赖都是一块
"它坏了我不知道为什么"的黑区，而 V5 的 136 轮里大量时间花在追黑区。

两种实现：
  · HTTPLLM  —— OpenAI 兼容端点，走 urllib。
  · StubLLM  —— 确定性脚本化响应。**没有它整个内核就不可验证**：
                离线也能把 loop 从头跑到尾，契约测试才不依赖网络与钱包。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from .budget import Ledger
from .config import LLMConfig


class LLMError(RuntimeError):
    pass


@dataclass
class Message:
    role: str  # system | user | assistant
    content: str

    def as_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


class LLM(Protocol):
    def complete(self, messages: Sequence[Message], *, max_tokens: int = 512,
                 temperature: float = 0.2) -> str: ...


# ---------------------------------------------------------------------------
# 真的端点
# ---------------------------------------------------------------------------
class HTTPLLM:
    def __init__(self, cfg: LLMConfig, ledger: Ledger | None = None) -> None:
        if not cfg.api_key:
            raise LLMError("缺少 V1_LLM_API_KEY；想离线跑请用 StubLLM")
        self.cfg = cfg
        self.ledger = ledger

    def complete(self, messages: Sequence[Message], *, max_tokens: int = 512,
                 temperature: float = 0.2) -> str:
        if self.ledger is not None:
            self.ledger.spend_llm()
        body = json.dumps({
            "model": self.cfg.model,
            "messages": [m.as_dict() for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }).encode("utf-8")
        req = urllib.request.Request(
            self.cfg.base_url.rstrip("/") + "/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.cfg.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:  # 4xx/5xx 带 body，不能只看状态码
            detail = e.read().decode("utf-8", "replace")[:400]
            raise LLMError(f"HTTP {e.code}: {detail}") from e
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"请求失败: {type(e).__name__}: {e}") from e
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"响应结构异常: {json.dumps(payload)[:300]}") from e


# ---------------------------------------------------------------------------
# 确定性桩 —— 内核可测的地基
# ---------------------------------------------------------------------------
class StubLLM:
    """脚本化响应器。

    设计要点：
      · **确定性**：同一输入永远同一输出（用内容哈希选择响应），否则契约测试会 flaky。
      · **可编排**：可以按提示词里的关键词命中不同剧本。
      · **会计数**：便于断言"probe 零调用"这类结构性质。
    """

    def __init__(self, scripts: Sequence[tuple[str, str]] | None = None,
                 default: str = "{}") -> None:
        # scripts 是 (关键词, 响应) 列表，顺序敏感：先命中先用。
        self.scripts = list(scripts or [])
        self.default = default
        self.calls: list[list[Message]] = []

    def complete(self, messages: Sequence[Message], *, max_tokens: int = 512,
                 temperature: float = 0.2) -> str:
        self.calls.append(list(messages))
        blob = "\n".join(m.content for m in messages)
        for key, resp in self.scripts:
            if key in blob:
                return resp
        return self.default

    @property
    def call_count(self) -> int:
        return len(self.calls)


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------
def build_llm(cfg: LLMConfig, ledger: Ledger | None = None,
              stub: StubLLM | None = None) -> LLM:
    """唯一构造入口。offline 或缺 key ⇒ StubLLM（**显式**，不静默降级）。

    静默降级是 V5 的经典病灶：看起来在跑，实际什么也没测。
    所以这里降级一定会被 core 记进日志。
    """
    if stub is not None:
        return stub
    if cfg.usable():
        return HTTPLLM(cfg, ledger)
    return StubLLM()


# ---------------------------------------------------------------------------
# 输出解析 —— 只认 JSON，但给一次容错
# ---------------------------------------------------------------------------
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

#: 「结构」与「载荷」的分隔符 —— 双通道协议的锚点。
#:
#: 为什么要分成两个通道（2026-10-17 一个下午实测撞出来的三种坏法）：
#:   ① 代码里的**裸换行**落进 JSON 字符串 ⇒ 非法 JSON（RFC 8259 不许字符串内有控制字符）
#:   ② 响应被 `max_tokens` **截断** ⇒ 半截 JSON，括号不闭合
#:   ③ 模型顺手把**单引号也转义**成 `\'` ⇒ 非法转义序列
#: 三者不是三个 bug，是**同一个设计错误**的症状：内容不该走 JSON。
#:
#: ⛔ 而且 ③ **不能**靠"放宽解析器/修转义"来兜：模型写 `"\d+"`（正则漏了一次
#:    转义）时，把它"修"成 `"d+"` 就是**静默改坏候选** —— 比解析失败恶劣得多。
#:    判据是**意图是否有歧义**：
#:      · 裸换行：意图无歧义（模型就是想换行）⇒ 宽松放行（strict=False）可以
#:      · 漏转义的反斜杠：意图有歧义（想写 `\d` 还是 `d`？）⇒ 一律不许猜
#:    ⇒ 结论：只能**改协议**，不能把解析器越改越宽。
ANSWER_MARK = "---ANSWER---"


def split_answer(text: str) -> tuple[str, str]:
    """把模型输出拆成 (结构部分, 载荷原文)。

    没有分隔符 ⇒ 载荷为空串（**不是**报错：老格式仍然按老办法处理）。
    载荷**一个字符都不动** —— 不转义、不解码、不剥代码块。这是整个修复的要点：
    代码原文从模型直接走到打分器，中途不经过任何有损变换。
    """
    if ANSWER_MARK not in text:
        return text, ""
    head, _, tail = text.partition(ANSWER_MARK)
    return head, tail.strip("\n").rstrip()

#: 解析模式计数 —— **宽松放行必须留痕**（不许静默兜底）。
#: `lenient` 增长说明模型/提示词的输出格式在退化：即便最终解析成功，
#: 那也是一个"正在变坏"的先行信号，不该被当成没事。
PARSE_MODES: dict[str, int] = {"exact": 0, "lenient": 0, "failed": 0}


def _loads(text: str, *, strict: bool) -> tuple[bool, Any]:
    try:
        return True, json.loads(text, strict=strict)
    except (json.JSONDecodeError, ValueError):
        return False, None


def parse_json(text: str, fallback: Any = None) -> Any:
    """从模型输出里取 JSON。

    解析失败**返回 fallback 而不是抛异常**，但调用方必须能把这次失败计入读数。
    理由：模型输出脏是常态，不该让内核崩；但"解析失败率"是高价值信号，
    绝不能悄悄吞掉（V5 的 R41）。

    实现上刻意**不写 `except: pass`** —— 静默兜底在本项目里等同缺陷
    （契约测试 J10 会结构性地钉死这一点）。这里用 `continue` 表达
    "试下一个形态"，用显式 `return fallback` 表达"确实解析不出来"。

    ⚠️ 为什么要试 `strict=False`（2026-10-17 实测逼出来的真实修复）：
        模型被要求把**代码**放进 `args.answer`，于是代码里的**裸换行**直接
        落进了 JSON 字符串 —— 按 RFC 8259 那是非法 JSON，`json.loads` 直接拒。
        实测抓到的原文（就是这么坏的）：

            {"candidates": [{"..." : "tool": "finish",
             "args": {"answer": "def parse_duration(s):
                 if not s:
                     return None"}}]}

        这不是模型"不听话"，是**用 JSON 传代码**这件事本身的毛病。
        `strict=False` 允许字符串内出现控制字符（含裸换行），正好对上。
        注意尝试顺序：**先 strict 后宽松** —— 干净 JSON 必须优先，
        否则我们会悄悄接受所有脏输出，那就变成另一种静默兜底了。
    """
    if not text:
        return fallback
    candidates = [text, *(_FENCE.findall(text) or [])]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for cand in candidates:
        for strict in (True, False):
            ok, val = _loads(cand.strip(), strict=strict)
            if ok:
                PARSE_MODES["exact" if strict else "lenient"] += 1
                return val
    PARSE_MODES["failed"] += 1
    return fallback
