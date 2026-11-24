"""LLM 接口 —— 零依赖。

不用 openai SDK、不用 requests、不用 httpx。只用 stdlib 的 urllib + json。
理由不是清教徒式的洁癖，是**可验证性**：任何一个第三方依赖都是一块
"它坏了我不知道为什么"的黑区，而大量工程时间都会花在追黑区上。

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
    """模型通道错误。**必须区分"基础设施抖动"与"模型不会"** —— 这两者的修法相反：

      · 传输层失败（超时 / 连接断 / 5xx / 429）：**重试**是对的（一次幂等的只读调用，
        重试不改变语义）；不重试等于把抖动记成"模型答不出来"。
      · 模型侧失败（4xx 参数错、响应结构不符）：**不许重试**（重发同样的请求只会
        得到同样的错），只能改路径。

    `transient=True` 表示前者。这个判据不是修辞：2026-10-17 的 4x 预算实测里
    有 3/6 题死于 `TimeoutError`，被记成 `solver 未产出答案` —— 那一刻读数里
    混着**基础设施**的锅，而报错文本让人以为是模型的锅。这是一种常见的归因错误：
    把不可归因的失败一起算进"能力读数"。
    """

    def __init__(self, msg: str, *, transient: bool = False) -> None:
        self.transient = transient
        super().__init__(msg)


@dataclass
class Message:
    role: str  # system | user | assistant
    content: str

    def as_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


class LLM(Protocol):
    def complete(self, messages: Sequence[Message], *, max_tokens: int = 512,
                 temperature: float | None = None,
                 timeout_s: float | None = None) -> str: ...


# ---------------------------------------------------------------------------
# 真的端点
# ---------------------------------------------------------------------------
class HTTPLLM:
    def __init__(self, cfg: LLMConfig, ledger: Ledger | None = None) -> None:
        if not cfg.api_key:
            raise LLMError("缺少 V1_LLM_API_KEY；想离线跑请用 StubLLM")
        self.cfg = cfg
        self.ledger = ledger

    #: 单次尝试的**默认**上限（口径值）。调用方可以用 `timeout_s=` 把它**调小**，
    #: 但永远调不大 —— `_LedgeredLLM` 用它来把单次尝试裁进剩余预算。
    #: ⚠️ 口径签名读的仍然是 `cfg.timeout_s`（见 §16.24 ④）：这里只暴露值，
    #:    不改变"这把尺子量的是什么"。
    @property
    def timeout_s(self) -> float:
        return float(self.cfg.timeout_s)

    def complete(self, messages: Sequence[Message], *, max_tokens: int = 512,
                 temperature: float | None = None,
                 timeout_s: float | None = None) -> str:
        if self.ledger is not None:
            self.ledger.spend_llm()
        # ⚠️ `None` ⇒ 取配置里的温度，**不是**取一个写死的默认值。
        #    实测事故：内核三个调用点都没传 temperature，于是全部悄悄落到写死的 0.2，
        #    而口径签名写着 0.0 —— 那次"T=0 不可复现"就是这么来的。
        if temperature is None:
            temperature = self.cfg.temperature
        # ⚠️ 同理，`timeout_s=None` ⇒ 取配置值。**不许**在这里写死一个数：
        #    写死就等于绕过调用方对剩余预算的裁剪（第二十四轮 361s 的一半原因）。
        _to = self.cfg.timeout_s if timeout_s is None else float(timeout_s)
        #: ★ V238（§309）：**按 provider 选通道**，不是只换一个字段名。
        #:
        #: 为什么必须换整条通道（V238 实测，同一提示 / `max_tokens=200` / `T=0`）：
        #:   · ollama 的 `/v1/chat/completions` 对 `thinking:{"type":"disabled"}`
        #:     **和** `think:false` **都静默忽略** ⇒ `content` 0 字符、思考吃满；
        #:   · 原生 `/api/chat` 的 `think:false` ⇒ `content` 14 字符、`done=stop`。
        #: 也就是说：在兼容层上，思考开关**根本不是一个可用杠杆**（V236 三档全灭的根因）。
        #:
        #: ⛔ `provider=""` 分支必须与历史**逐字节相同**：请求体的键序、缩进、
        #:    `thinking` 的位置都不许动 —— 否则所有历史读数的口径签名集体失效。
        if self.cfg.provider == "ollama":
            url, body, headers = self._ollama_request(
                messages, max_tokens=max_tokens, temperature=temperature, t=_to)
        else:
            body_obj: dict[str, Any] = {
                "model": self.cfg.model,
                "messages": [m.as_dict() for m in messages],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            #: 「思考开关」只在这个字段**被显式设过**时才发（见 `config.LLMConfig.thinking`）。
            #: ⛔ 空串时**一个字节都不许多**：默认档必须与历史请求逐字节相同，
            #:    否则一台不认识该字段的端点会直接 400（而口径签名也已经被它带偏）。
            #: ⛔ 也不许在这里做"值不合法就不发"的宽容：合法性在 `LLMConfig.__post_init__`
            #:    已经 fail-closed 了；这里再宽容一次，就是给拼错留第二条活路。
            if self.cfg.thinking:
                body_obj["thinking"] = {"type": self.cfg.thinking}
            url = self.cfg.base_url.rstrip("/") + "/chat/completions"
            body = json.dumps(body_obj).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.cfg.api_key}",
            }
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=_to) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:  # 4xx/5xx 带 body，不能只看状态码
            detail = e.read().decode("utf-8", "replace")[:400]
            # 5xx / 429 = 服务端或限流 ⇒ 抖动，可重试；4xx = 请求本身有问题 ⇒ 不许重试
            raise LLMError(f"HTTP {e.code}: {detail}",
                           transient=e.code >= 500 or e.code == 429) from e
        except Exception as e:  # noqa: BLE001
            # 超时 / 连接断 / DNS —— 全是传输层抖动
            raise LLMError(f"请求失败: {type(e).__name__}: {e}",
                           transient=True) from e
        if self.cfg.provider == "ollama":
            return self._ollama_content(payload)
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            # 响应结构不符 = 通道/版本问题，重试无用
            raise LLMError(f"响应结构异常: {json.dumps(payload)[:300]}") from e

    # ------------------------------------------------------------------
    # ollama 原生通道（`provider="ollama"`）—— 与 OpenAI 兼容路径完全分开
    # ------------------------------------------------------------------
    def _ollama_url(self) -> str:
        """把 `base_url` 归一到原生根：`.../v1` ⇒ `...`，再拼 `/api/chat`。

        ⚠️ 为什么要在**这里**剥 `/v1` 而不是要求操作者改 `V1_LLM_BASE_URL`：
           同一台 ollama 的兼容端点与原生端点必须共用同一个 `base_url`，
           否则「换 provider」会顺带改掉端点主机 ⇒ 口径签名里出现两处变化
           （纪律 65：比两版装置要知道自己导入了谁）。剥 `<base>/v1` 是幂等的：
           `...:11434` 与 `...:11434/v1` 都归一到同一个原生地址。
        """
        b = self.cfg.base_url.rstrip("/")
        if b.endswith("/v1"):
            b = b[: -len("/v1")]
        return b + "/api/chat"

    def _ollama_request(self, messages: Sequence[Message], *, max_tokens: int,
                        temperature: float, t: float) -> tuple[str, bytes, dict]:
        """构造原生请求 `(url, body, headers)`。

        `t` 只用在错误信息里（超时由 `urlopen` 自己管），保留参数是为了
        让两个构造点签名一致、调用处不用分叉。
        """
        body_obj: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": [m.as_dict() for m in messages],
            # ⛔ 流式会让「一次 complete = 一次响应」这条不变量失效（账本按调用计数）。
            "stream": False,
            # 原生的生成预算叫 `num_predict`，**不是** `max_tokens`；温度也在 `options` 里。
            # 名字不同不是风格问题：把 `max_tokens` 直接塞进原生体会被静默忽略
            # ⇒ 预算没设上 ⇒ 截断 ⇒ 又变成「模型不会」（V238 预注册 §3.2）。
            "options": {"num_predict": int(max_tokens), "temperature": float(temperature)},
        }
        #: ⛔ 与兼容路径同一条规矩：`thinking` 为空 ⇒ **不发该键**。
        #:    这里更不能发默认值 —— ollama 的默认是「按模型默认」，
        #:    显式发 `false` 会改变 `qwen2.5` 这类不支持思考的模型的请求（虽然无害，
        #:    但会让「默认档逐字节相同」这句话不再成立）。
        if self.cfg.thinking:
            #: 原生字段是**布尔** `think`，不是 `{"type": "..."}`。
            #: 只有 `"enabled"` 才是 True —— 合法值已在 `LLMConfig` 里穷举过，
            #: 但这里仍按显式相等判断（不写 `!= "disabled"`）：将来多一个合法值时，
            #: 那种写法会把它悄悄当成 True。
            body_obj["think"] = (self.cfg.thinking == "enabled")
        body = json.dumps(body_obj).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        # ⚠️ ollama 不校验 Authorization，但也**不需要**它；不带是刻意的：
        #    少一个头就少一个「以为在鉴权」的错觉。
        return self._ollama_url(), body, headers

    def _ollama_content(self, payload: dict) -> str:
        """从原生响应里取正文 —— 并在**截断**时抛错，不许静默返回空串。

        ⛔ 这一条是本轮的关键判据，不是防御性编程：
           V236 的失败形态就是 `done_reason=length` + `content` 0 字符
           （思考吃满 13890 字符、正文一个字都没出）。若这里返回空串，
           下游只会看到「模型给的代码是空的」⇒ 归因成「模型不会」⇒
           而真相是**预算口径**。纪律 169 说的就是这件事。
        """
        msg = payload.get("message") or {}
        content = msg.get("content") or ""
        done = payload.get("done_reason")
        if done is None and payload.get("done") is True:
            # 旧版 ollama（<0.9）只给 `done: true`，没有 `done_reason` ⇒ 视为正常结束。
            done = "stop"
        if done == "length":
            raise LLMError(
                "响应被截断（done_reason=length）："
                f"thinking={len(msg.get('thinking') or '')}字符 "
                f"content={len(content)}字符 eval={payload.get('eval_count')} "
                "—— 这是预算/思考税，不是模型不会（V236 §307 / 纪律 169）")
        if done is None and not content:
            # 结构不符（既没结束原因也没正文）= 通道/版本问题，与兼容路径同判据
            raise LLMError(f"响应结构异常: {json.dumps(payload)[:300]}")
        return content


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
                 temperature: float | None = None,
                 timeout_s: float | None = None) -> str:
        # `timeout_s` 是**真有**端点的参数；桩不联网，接受它只为让
        # `_LedgeredLLM` 的裁剪逻辑在离线路径上也不用分叉（分叉就会漏测）。
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

    静默降级是经典病灶：看起来在跑，实际什么也没测。
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

#: 分隔符的**空白容忍**形态。
#:
#: ⚠️ 实测（2026-10-17 4x 预算跑内核，报文原文）：
#:     模型写的是 `---\n ANSWER---` —— 打成了 `---` 换行空格 `ANSWER---`，
#:     于是精确串匹配 `ANSWER_MARK not in text` 失败 ⇒ 整个响应（结构 + 代码）
#:     一起被丢进 JSON 解析 ⇒ 报"不是合法 JSON"，看起来像模型不会写 JSON。
#:
#: 判据仍然是**意图是否有歧义**（与上面第 152 行同一条判据，不是新开一套）：
#:     分隔符里多几个空白，意图**零歧义**（它就是想打这个分隔符）⇒ 允许。
#:     而载荷内容照旧**一个字符都不动** —— 容忍的边界只在分隔符本身。
_MARK_RE = re.compile(r"-{2,}[ \t\r\n]*ANSWER[ \t\r\n]*-{2,}")


def split_answer(text: str) -> tuple[str, str]:
    """把模型输出拆成 (结构部分, 载荷原文)。

    没有分隔符 ⇒ 载荷为空串（**不是**报错：老格式仍然按老办法处理）。
    载荷**一个字符都不动** —— 不转义、不解码、不剥代码块。这是整个修复的要点：
    代码原文从模型直接走到打分器，中途不经过任何有损变换。

    分隔符本身容忍空白（见 `_MARK_RE`）；`head` / `tail` 的切分位置由
    `re.split(..., maxsplit=1)` 决定，所以两侧都还是原文。
    """
    parts = _MARK_RE.split(text, maxsplit=1)
    if len(parts) == 1:
        return text, ""
    head, tail = parts
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
    绝不能悄悄吞掉。

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
