"""工具契约 + 外部能力分派。

设计立场（用户明确要求）：**内核不实现功能**。
内核只做四件事：预算、记忆、规划、校验。所有具体能力
—— 写代码、查资料、跑命令、调别的 agent —— 一律作为**外部能力**通过契约接入。

所以这个文件的价值不在"提供了多少工具"，而在**接口的形状**：
    · 能力必须显式注册（ToolSpec），不许隐式全局函数
    · 调用结果永远返回 ToolResult，**永不抛异常**（异常是内核崩的来源）
    · 缺能力 = 显式报错并留痕，不是静默返回空串（V5 的经典病灶）

外部能力的登记处是 `agents.json`（见 agents.example.json）：
    {"agents": {"<name>": {"type":"shell"|"http", "description":"...", ...}}}
"""

from __future__ import annotations

import json
import shlex
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    params: dict
    handler: Callable[[dict], str]
    external: bool = False
    mutating: bool = False   # True = 会改外部世界，guard 会额外审

    def catalog_line(self) -> str:
        args = ", ".join(self.params.keys()) or "无"
        mark = " [外部]" if self.external else ""
        mut = " [会改外部状态]" if self.mutating else ""
        return f"- {self.name}({args}): {self.description}{mark}{mut}"


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    error: str = ""

    def as_observation(self) -> str:
        return self.output if self.ok else f"[工具失败] {self.error}"


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"工具重名（唯一事实源原则）: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return sorted(self._specs)

    def catalog(self) -> str:
        return "\n".join(self._specs[n].catalog_line() for n in self.names())

    def dispatch(self, name: str, args: dict) -> ToolResult:
        """唯一调用入口。**永不抛异常**，所有失败都变成可记账的 ToolResult。"""
        spec = self._specs.get(name)
        if spec is None:
            return ToolResult(False, error=f"未知工具 {name!r}（已注册: {self.names()}）")
        missing = [k for k in spec.params if k not in args]
        if missing:
            return ToolResult(False, error=f"{name} 缺参数 {missing}")
        try:
            out = spec.handler(args)
        except Exception as e:  # noqa: BLE001
            return ToolResult(False, error=f"{name} 执行异常 {type(e).__name__}: {e}")
        if out is None:
            return ToolResult(True, output="(无输出)")
        return ToolResult(True, output=str(out))


# ---------------------------------------------------------------------------
# 内建：它们不是"功能"，是内核跑起来所需的最小骨骼
# ---------------------------------------------------------------------------
def register_core_tools(reg: ToolRegistry, *, memory: Any = None,
                        workspace: Path | None = None) -> None:
    """注册内核自带的极简工具。刻意极少 —— 其余全部走外部。

    语义是「**保证**核心工具在册」，不是「新增」：
        · 已在册且定义一致 ⇒ 跳过
        · 已在册但定义不同 ⇒ 交给 register 抛错（那是真冲突，必须炸）
    为什么：`Core(tools=<调用方预置的注册表>)` 是公开的注入点，而 Core 一定会
    再调一次本函数。若这里无脑 register，注入就必然因 `finish` 重名而崩 ——
    实测就是这么撞的（契约 L6）。但"唯一事实源"的重名检查**不能**因此放松：
    它正是用来抓"同一个工具被两处定义"的。
    """

    def _put(spec: ToolSpec) -> None:
        cur = reg.get(spec.name)
        if cur is None:
            reg.register(spec)
            return
        same = (cur.description == spec.description and cur.params == spec.params
                and cur.external == spec.external and cur.mutating == spec.mutating)
        if not same:
            reg.register(spec)          # 定义不一致 ⇒ 触发重名检查并抛错

    def _finish(args: dict) -> str:
        return str(args.get("answer", ""))

    _put(ToolSpec(
        "finish", "**把完整答案原文放进 answer** 并结束本次任务"
                  "（目标只是要一段代码/一个结论时，就用它，不要为用工具而用工具）",
        {"answer": "最终答复文本"}, _finish,
    ))

    if memory is not None:
        def _recall(args: dict) -> str:
            facts = memory.recall(str(args.get("query", "")), k=int(args.get("k", 6)))
            if not facts:
                return "(语义记忆中无相关条目)"
            return "\n".join(f.as_line() for f in facts)

        def _note(args: dict) -> str:
            key, is_new = memory.commit_fact(
                str(args.get("text", "")),
                source=str(args.get("source", "agent")),
                confidence=float(args.get("confidence", 0.6)),
            )
            return f"{'新增' if is_new else '已存在(仅累加引用)'} key={key}"

        _put(ToolSpec(
            "recall", "检索语义记忆", {"query": "查询词"}, _recall,
        ))
        _put(ToolSpec(
            "note", "写入一条语义记忆（自动去重）",
            {"text": "事实文本", "source": "来源", "confidence": "0~1 可信度"},
            _note, mutating=True,
        ))

    def _run_shell(args: dict) -> str:
        cmd = str(args.get("cmd", "")).strip()
        if not cmd:
            raise ValueError("cmd 为空")
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=float(args.get("timeout_s", 30)),
            cwd=str(workspace) if workspace else None,
        )
        tail = (proc.stdout or "") + (("\n[stderr] " + proc.stderr) if proc.stderr else "")
        return f"rc={proc.returncode}\n{tail[:4000]}"

    _put(ToolSpec(
        "run_shell", "在工作目录执行 shell 命令（受 guard 白名单约束）",
        {"cmd": "命令", "timeout_s": "超时秒数(可选)"}, _run_shell,
        external=True, mutating=True,
    ))

    def _http_get(args: dict) -> str:
        url = str(args.get("url", ""))
        if not url.startswith(("http://", "https://")):
            raise ValueError("只允许 http/https")
        req = urllib.request.Request(url, headers={"User-Agent": "V1/1.0"})
        with urllib.request.urlopen(req, timeout=float(args.get("timeout_s", 20))) as r:
            return r.read().decode("utf-8", "replace")[:8000]

    _put(ToolSpec(
        "http_get", "取一个 URL 的文本内容", {"url": "URL"}, _http_get, external=True,
    ))


# ---------------------------------------------------------------------------
# 外部 agent / skill 接入（用户要求的核心形态）
# ---------------------------------------------------------------------------
def register_external_agents(reg: ToolRegistry, path: Path | str) -> list[str]:
    """从 agents.json 注册外部 agent。返回注册成功的名字列表。

    文件不存在 ⇒ 返回空列表（**不是错误**：V1 必须能在零外部能力下跑通，
    这保证内核的可验证性不依赖任何外部系统）。
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    agents = data.get("agents", {}) if isinstance(data, dict) else {}
    # 相对路径的锚点 = agents.json 所在目录（通常就是项目根）。
    # ⚠️ 不这样做的话，`command: ["python3", "external/x.py"]` 会**继承调用方的 cwd**：
    #    从别的目录调用 run.py 时外部能力就静默失效了 —— 静默失效比报错更糟。
    anchor = Path(path).parent
    added: list[str] = []
    for name, spec in agents.items():
        if not isinstance(spec, dict):
            continue
        tool_name = f"agent:{name}"
        kind = spec.get("type", "shell")
        desc = str(spec.get("description", "外部 agent"))
        cwd = str(spec.get("cwd") or anchor)

        if kind == "shell":
            argv = spec.get("command") or []
            if not isinstance(argv, list) or not argv:
                continue

            def _handler(args: dict, _argv=list(argv), _cwd=cwd,
                         _to=float(spec.get("timeout_s", 120))) -> str:
                # 任务文本通过 stdin 传，避免 shell 注入与引号地狱。
                # ⚠️ argv/cwd/timeout 一律用默认参数**绑定到本轮的 spec**：
                #    直接读循环变量 `spec` 是 late binding —— 所有 agent 会共用
                #    最后一个 spec 的超时值。这类闭包踩坑在实测里很难看出来。
                proc = subprocess.run(
                    _argv, input=str(args.get("task", "")), capture_output=True,
                    text=True, timeout=_to, cwd=_cwd,
                )
                return (proc.stdout or "")[:8000] or f"(rc={proc.returncode})"

            reg.register(ToolSpec(tool_name, desc, {"task": "交给该 agent 的任务"},
                                  _handler, external=True, mutating=True))

        elif kind == "http":
            url = str(spec.get("url", ""))

            def _handler(args: dict, _url=url,
                         _to=float(spec.get("timeout_s", 120))) -> str:
                body = json.dumps({"task": args.get("task", "")}).encode()
                req = urllib.request.Request(
                    _url, data=body, headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=_to) as r:
                    return r.read().decode("utf-8", "replace")[:8000]

            reg.register(ToolSpec(tool_name, desc, {"task": "交给该 agent 的任务"},
                                  _handler, external=True, mutating=True))
        else:
            continue
        added.append(tool_name)
    return added


def external_agent_names(reg: ToolRegistry) -> list[str]:
    """外部能力的单一查询口径 —— 别处不要再自己 filter。"""
    return [n for n in reg.names() if n.startswith("agent:")]


def shlex_split(cmd: str) -> list[str]:
    """给 guard 用的安全切分；失败时回退到整串。"""
    try:
        return shlex.split(cmd)
    except ValueError:
        return [cmd]
