"""运行时守卫 —— OWASP Agentic Top-10 的检查点落地。

依据：OWASP Agentic Top-10 / AGT(2026) 的十道风险检查点。V1 只做其中
**在内核层面真正可控**的四道，其余（网络隔离、基础设施零信任）不在内核职责内，
写在 README 的边界声明里，不假装自己做了。

    1. Goal Hijacking      —— 动作必须与原始目标相关，偏离要显式说明
    2. Tool Misuse         —— shell 白名单 + 破坏性命令拦截
    3. Memory Poisoning    —— 记忆写入必须有 source 且可信度达阈值
    4. Cascading Failures  —— 连续失败达阈值即熔断本轮

设计原则：**拦截必须留正向信号**。
被拦下的动作要形成一条可读的 Verdict 并进入日志与读数，
静默兜底（`except: pass`）在本项目里等同缺陷。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# 检查点清单 —— 唯一事实源，测试与日志都从这里派生
CHECKPOINTS: dict[str, str] = {
    "goal_hijack": "动作与原始目标的相关性检查",
    "tool_misuse": "工具与参数白名单检查",
    "memory_poison": "记忆写入的来源与可信度门控",
    "cascade": "连续失败熔断",
}

# 破坏性命令：命中即拒。宁可误拒，不可误放 —— 因为误放是不可逆的。
_DESTRUCTIVE = (
    re.compile(r"\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*[rR])\b"),
    re.compile(r"\bmkfs(\.\w+)?\b"),
    re.compile(r"\bdd\s+.*\bof=/dev/"),
    re.compile(r":\(\)\s*\{.*\}\s*;\s*:"),          # fork bomb
    re.compile(r"\b(shutdown|reboot|halt)\b"),
    re.compile(r"\bdiskutil\s+(eraseDisk|eraseVolume|reformat)\b"),
    re.compile(r"\b(mkswap|fdisk|parted)\b"),
)

# 即使不是 rm -rf，也不允许被 shell 触碰的路径（personal files 安全带）
_PROTECTED = (
    "/", "~", "$HOME", "/Users", "/System", "/Applications", "/Library",
    "Desktop", "Downloads", "Documents",
)

_ADMIN = (re.compile(r"\bsudo\b"), re.compile(r"\bdoas\b"))
_PIPE_EXEC = (re.compile(r"\|\s*(ba|z|k)?sh\b"), re.compile(r"\|\s*python[0-9.]*\b"))


@dataclass(frozen=True)
class Verdict:
    allow: bool
    checkpoint: str
    reason: str = ""

    def as_line(self) -> str:
        tag = "放行" if self.allow else "拦截"
        return f"[guard:{self.checkpoint}] {tag}" + (f" —— {self.reason}" if self.reason else "")


ALLOW = Verdict(True, "-", "")


class Guard:
    def __init__(
        self,
        *,
        allow_shell: bool = False,
        workspace: Path | None = None,
        min_memory_confidence: float = 0.3,
        max_consecutive_failures: int = 3,
    ) -> None:
        self.allow_shell = allow_shell
        self.workspace = Path(workspace).resolve() if workspace else None
        self.min_memory_confidence = min_memory_confidence
        self.max_consecutive_failures = max_consecutive_failures
        # 拦截记账：每一次拒绝都要能被数出来
        self.blocks: dict[str, int] = {k: 0 for k in CHECKPOINTS}

    # ------------------------------------------------------------------
    def check_shell(self, cmd: str) -> Verdict:
        if not self.allow_shell:
            return self._deny("tool_misuse", "shell 未开启（需显式 --allow-shell）")
        if not cmd.strip():
            return self._deny("tool_misuse", "空命令")
        for pat in _ADMIN:
            if pat.search(cmd):
                return self._deny("tool_misuse", "禁止提权命令")
        for pat in _DESTRUCTIVE:
            if pat.search(cmd):
                return self._deny("tool_misuse", "破坏性命令（不可逆，一律拒绝）")
        for pat in _PIPE_EXEC:
            if pat.search(cmd):
                return self._deny("tool_misuse", "禁止把远程内容直接管道进解释器")
        # 触碰受保护路径
        for token in _PROTECTED:
            if token in ("Desktop", "Downloads", "Documents", "/Users", "/System"):
                if re.search(rf"(rm|mv|chmod|chown)\s[^|;&]*{re.escape(token)}", cmd):
                    return self._deny("tool_misuse", f"禁止对 {token} 做写操作")
        return ALLOW

    def check_memory_write(self, *, source: str, confidence: float) -> Verdict:
        if not source:
            return self._deny("memory_poison", "记忆写入缺 source")
        if confidence < self.min_memory_confidence:
            return self._deny(
                "memory_poison",
                f"可信度 {confidence:.2f} < 阈值 {self.min_memory_confidence:.2f}",
            )
        return ALLOW

    def check_cascade(self, consecutive_failures: int) -> Verdict:
        if consecutive_failures >= self.max_consecutive_failures:
            return self._deny(
                "cascade", f"连续失败 {consecutive_failures} 次 ≥ {self.max_consecutive_failures}"
            )
        return ALLOW

    def check_goal(self, *, action: str, goal: str, min_overlap: int = 1) -> Verdict:
        """粗粒度 goal-hijack 检查：动作与目标要有至少一个公共 token。

        刻意做**宽**（阈值 1）。做窄会大量误杀正常动作，误杀的代价是内核
        什么都不敢做，最后被人关掉 —— 那比漏检更糟。这里只拦"完全无关"。
        """
        a = set(re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]{2,}", action.lower()))
        g = set(re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]{2,}", goal.lower()))
        if not a or not g:
            return ALLOW
        if len(a & g) >= min_overlap:
            return ALLOW
        return self._deny("goal_hijack", f"动作与目标无公共词：{sorted(a)[:4]}")

    # ------------------------------------------------------------------
    def _deny(self, checkpoint: str, reason: str) -> Verdict:
        self.blocks[checkpoint] = self.blocks.get(checkpoint, 0) + 1
        return Verdict(False, checkpoint, reason)

    def report(self) -> dict:
        return {"blocks": dict(self.blocks), "total": sum(self.blocks.values())}
