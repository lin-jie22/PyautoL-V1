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

#: ASCII 词（含数字/下划线），长度 ≥ 2。
_WORD = re.compile(r"[A-Za-z0-9_]{2,}")
#: 连续汉字段。**必须单独抽出来**，见 `tokens_of` 的历史。
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")


def tokens_of(text: str) -> set[str]:
    """把文本切成可比较的 token 集合。**唯一**的分词实现。

    ## 为什么不是一个大正则（第六十五轮修的真 bug）

    旧实现只有一行：

        set(re.findall(r"[A-Za-z0-9_\\u4e00-\\u9fff]{2,}", text.lower()))

    这个字符类把 ASCII 与汉字混在一起，而 `{2,}` 是**贪婪**的 ——
    于是**一整句中文变成一个 token**：实测 `"去掉重复元素并保持首次出现的顺序"`
    （14 字）产出 **1 个** token，就是它自己。

    ⇒ 中文之间要匹配，必须**整句逐字相等**。而自然语言的子句几乎不会逐字重复，
    所以 `check_goal` 在中文任务上不是"偶尔误杀"，是**结构性 100% 误杀**。

    实测证据（`bench/evidence/loop_s64/run_qwen25_7b_T0.log`）：`t02_dedupe` 三轮
    全部被拦，被拦的动作是 `使用集合去重并保持顺序` —— 它**恰好就在做题**。
    目标侧的 token 是 `{dedupe, seq, 写一个函数, 返回一个新列表, ...}` 8 个整句，
    交集恒空。

    ## 修法：汉字用二元组（bigram）

    句子里"函数""去重""顺序"这类**二字词**占绝对多数，二元组不需要词典就能
    逼近词粒度（jieba 之类会违反本项目的零第三方依赖约束）。
    单字段（只有 1 个汉字）保留自身，否则它会被静默丢掉。

    ## 代价（必须写出来）

    一个二字词在两边都出现就算命中（阈值仍是 1）⇒ 比旧实现**宽得多**，
    常见词（"函数""使用"）会造成偶然命中。这是刻意的方向选择：
    误杀的代价是内核什么都不敢做，而漏检的代价由 `check_shell`（不可逆命令）
    与 `values.check_irreversible` 兜底。但它意味着**这道检查从此更接近
    "只拦完全无关"**，不再是偏题探测器 —— 别拿它当语义检查用。
    """
    t = text.lower()
    out = set(_WORD.findall(t))
    for run in _CJK_RUN.findall(t):
        if len(run) == 1:
            out.add(run)
            continue
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    return out


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

        ## 能力边界（写死，不许含糊）

        字面重合判不了语义相关 —— 这是本项目已经登记过的结论（`mw/values.py`
        模块 docstring：用字面重合判相关性"结构上必然错"）。所以这里的判据只有
        **两档**：能拦住"完全无关"，拦不住"相关但偏题"。它**不是**语义检查，
        谁把它当语义检查用，谁就会在第一次误杀时把它关掉。

        所以它只有两种坏法，必须都测：**误杀**（把合法的活拦死）与**空转**
        （永远放行，等于没有这道检查）。第六十五轮之前它是第一种，
        而且是在中文上 **100% 误杀**，根因见 `tokens_of`。
        """
        a, g = tokens_of(action), tokens_of(goal)
        if not a or not g:
            # 一侧为空 ⇒ 没有可比的证据，放行。⛔ 不要在这里 deny：
            # "比不了"与"比出来无关"是两件事，混淆它们正是本项目的头号老病
            # （缺失被读成否定）。
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
