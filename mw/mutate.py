#!/usr/bin/env python3
"""有界变异 —— 内核只许改「怎么花钱」，不许改「有多少钱」（第七十轮，§32.8）。

## 这一层要解决的是什么

§32.8 的原话是「改内核**过程**（预算分配、收尾时机、守卫阈值），不换模型」。
这句话可以一行写完，但**界**在哪里必须被声明出来，否则"有界"只是一个形容词。
界分三层，缺一层就漏一种坏法：

1. **旋钮表**（`KNOBS`）—— 哪些字段可动、各自的 `[lo, hi]` 是多少、
   依据属于哪一种（`mechanical` = 代码里一条可以指到行的事实；
   `policy` = 策略选择）。⛔ 依据的**种类**必须写，因为两者被推翻的方式不同：
   前者被代码改掉就失效，后者本来就只是"围个圈"，不许冒充"量出来的最优点"。
2. **冻结表**（`FROZEN`）—— 哪些字段**不许**动，各写理由。
   ⛔ 只列可动的、不列禁动的 ⇒ 界是筛出来的、不是声明出来的，
   而且"下次顺手把它加进可动表"没有任何阻力。两张表合起来必须
   **覆盖 `Limits` 的每一个字段**（AZ69 钉这一点）。
3. **跨字段不变式**（`INVARIANTS`）—— 每个字段都在界内，组合起来仍可能无意义。
   最硬的一例：`close_reserve_s >= max_wall_s` 时，`core.py` 那条
   `wall_left <= close_reserve_s` 从第 0 秒起就成立 ⇒ 探索一次都不会发生。

## 拒绝，不是钳制

越界一律 `ok=False` 并说清范围；**不许** clamp。
钳制是最坏的一种"成功"：账本里写着 `close_reserve_s=500`，实际跑的是 90 ——
从此"我改过什么"和"我跑的是什么"是两个事实，而读数只会反映后者。
（同宗：`tool_result_line` 那次的 `deadline_s=0.0` 假值事故，§30.3 决定③。）

## 这个文件**不管**裁决

第一版把 `mutation_verdict()` 也写在这里，契约 **AO11** 当场判红
（内核模块里连那个词都不许出现 —— 那条断言连**注释和 docstring** 一起扫，
所以这里也不能把它写出来）。判红是对的，而且比它写下的那句更狠：
**裁决权本来就不该在内核里** —— 让内核拿着"证据"决定自己该不该被保留，
等于把裁判席搬进更衣室。所以裁决在 `bench/mutation_check.py`：

| 层 | 管什么 | 理由 |
|---|---|---|
| `mw/mutate.py`（内核） | 界、拒绝、台账 | 这些是**机制**：它们说什么被允许，不说"好不好" |
| `bench/mutation_check.py`（尺子） | 要不要保留 | 这是**裁决**：它必须在内核之外，且可以被独立复核 |

## 它**不**声称的东西

本轮**没有**把自主变异接进 `Core.run`：谁提议下一个变异、什么时候试，是调用方的事，
而现有 4 题集（天花板 0.70、连续四轮负结果）没有裁决能力 —— 接上去只会得到一串
`UNKNOWN`。这条边界登记在 DESIGN §33.8，不是待办清单里的"忘了做"。
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Mapping

from .config import Limits, Settings

# ---------------------------------------------------------------------------
# 1. 界的第一层：哪些旋钮可动
# ---------------------------------------------------------------------------
#: 依据的种类。**不是一个装饰字段**：`mechanical` 的条目一旦代码变了就要跟着改，
#: `policy` 的条目本来就不承诺最优点在区间里 —— 两者的"错"长得不一样。
MECHANICAL = "mechanical"
POLICY = "policy"
BASES = (MECHANICAL, POLICY)


@dataclass(frozen=True)
class Knob:
    """一个可动旋钮。`lo/hi/step` 都是**闭区间**语义（lo 与 hi 本身合法）。"""

    name: str            # 必须是 `Limits` 的字段名（AZ69 对撞）
    kind: str            # "int" | "float"
    lo: float
    hi: float
    step: float
    basis: str
    why: str


KNOBS: tuple[Knob, ...] = (
    Knob("max_stall", "int", 1, 6, 1, MECHANICAL,
         "下界 1：0 在 `stall_verdict` 里已被按 1 处理（§32 决定③）⇒ 0 不是"
         "「更小的阈值」，是同一个值的另一种写法。"
         "上界 6：一次 `propose` 的日志耗时在 15~25s 量级，120s 档装不下 6 次以上 —— "
         "再高的阈值在那档预算里**到不了**（INV2 只是更宽的硬边界）。"),
    Knob("close_reserve_s", "float", 20.0, 90.0, 10.0, MECHANICAL,
         "下界 20：与一次答案调用同阶（~500 字 / 25 字每秒）。低于它，余量就是"
         "「给一次调用不够的时间」—— 那是准时失败。"
         "上界 90：120s 档留下 30s 探索；再高就必须同时改 `max_wall_s`，而那是冻结项"
         "（INV1 是硬边界，这条是软理由：它是策略，不是定理）。"),
    Knob("max_candidates", "int", 1, 5, 1, POLICY,
         "下界 1 = 没有选择，退化成单候选 —— 那是合法配置（也是变异前的基线）。"
         "上界 5 是**策略**：候选越多、单次响应越长，而截断判据在 `planner.truncated`；"
         "本项目没有量过「几个候选最好」，所以只围边界，不声称最优点在区间里。"),
    Knob("recall_k", "int", 1, 12, 2, POLICY,
         "下界 1：7x 的最硬读数是「只给唯一正确的那条，模型仍按常识作答」—— "
         "所以 1 不是「太少」，它是一个合法的对照点（数量不是瓶颈，但下界仍要立）。"
         "上界 12 是策略：再多会把状态正文挤出 `STATE_CHAR_CAP`。"),
    Knob("verify_retries", "int", 0, 4, 1, POLICY,
         "下界 0 = 关闭自检（合法配置，`Limits` 的注释里写着）。"
         "上界 4 是策略：每次重写都是一次完整生成，4 次已经吃掉半份预算；"
         "而「重写」本来就只挡结构性错误（`mw/verify.py`），不挡答错。"),
    Knob("verify_static", "int", 0, 2, 1, POLICY,
         "★ 第八十轮。**不是「更严一点」，是三档语义**："
         "0=不派生这条判据（默认，与之前逐位相同）/1=报名字/2=同一触发集但盲报。"
         "2 是**等长安慰剂臂** —— 触发时机与多花的 LLM 调用与 1 完全相同，只有载荷不同"
         "⇒ base↔2 量「被退回重写一次（盲发）值多少」，2↔1 量「看见名字值多少」。"
         "上界 2 = 这三档，不多不少；这不是一个「越大越好」的数。"),
    Knob("max_consecutive_failures", "int", 1, 6, 1, POLICY,
         "下界 1 = 一次失败就熔断，会在正常波动里误杀（实测里 2 次连续失败很常见）。"
         "上界 6 是策略：与 `max_stall` 同量级，超过它熔断就永远轮不到触发。"),
    Knob("working_memory_items", "int", 8, 48, 8, POLICY,
         "下界 8 = 少到能看见「记忆不够」的效果。"
         "上界 48 是策略：受状态窗口容量约束。"),
)


@dataclass(frozen=True)
class Frozen:
    """一个**不许**动的字段。理由必须写 —— 否则下次有人顺手把它挪进 `KNOBS`。"""

    name: str
    why: str


FROZEN: tuple[Frozen, ...] = (
    Frozen("max_wall_s",
           "预算**总量**。改它不是改过程，是改考试时长；档位由 `--budget` 决定"
           "（1x/2x/4x），内核自己不许改自己的分母。"),
    Frozen("max_llm_calls",
           "调用次数的绝对值 —— 同上，是预算总量的一种表达。"),
    Frozen("max_steps",
           "步数的绝对值 —— 同上。它与 `max_stall` 的比值才是过程，而过程由"
           "`max_stall` 那一侧表达（INV2 保住两者的序关系）。"),
    Frozen("llm_max_tokens",
           "生成上限。它是 `Caliber.max_tokens` 的同一个事实（`bench.CALIBER_KEYS` "
           "的注释写着为什么），改它等于换尺子。"),
    Frozen("llm_retries",
           "传输层重试。它掩盖的是**基础设施**，不是认知过程；把它放进变异空间，"
           "等于允许系统用「多试几次」冒充「变强」。"),
    Frozen("llm_retry_backoff_s",
           "同上（重试的间隔）。"),
    Frozen("close_reserve_steps",
           "与 `close_reserve_s` 是**同一件事的两种表达**（`_closing_reserve_hit` "
           "里是 OR）。两个都能动 = 两个真相，改一个另一个继续按旧值行事。"),
    Frozen("close_overrun_s",
           "收尾那一发的墙钟宽限。它买的是「不要让一份已写好的答案输给 1 秒」——"
           "**交付可靠性**，不是解题过程。放进变异空间就等于允许系统用"
           "「多赖几秒」冒充「变强」（与 `llm_retries` 同一条理由）。"
           "⛔ 必须被 `coverage_gaps()` 覆盖：漏了它就是一个默认可动的字段。"),
    Frozen("stall_on_blocked",
           "**判据**，不是过程参数：它决定「什么算一次进展」。放进变异空间等于允许"
           "系统自己改「我到底有没有在原地打转」的定义 —— 那是裁判席，不是更衣室"
           "（与 `mutation_verdict` 不许住在内核里同一条理由）。"
           "⛔ 必须被 `coverage_gaps()` 覆盖。"),
    Frozen("external_answer_tool",
           "能力开关（谁来做这道题），不是过程。§30 刚把它的入口收成一处，"
           "不许在这里开第二个。"),
    Frozen("allow_note",
           "记忆通道开关。它决定「被测系统包含什么」—— §17-6 6c 的三臂实验里，"
           "空库臂就是被它在运行期写回了 facts 而失去可比性。"),
    Frozen("allow_learn",
           "同上（世界模型沉淀进语义层的第二条写通道）。"),
    Frozen("reward_echo",
           "★第一百一十四轮（§87）。它是**实验的分臂开关**，不是可优化参数："
           "0/1/2 分别对应「没有那块字」「真回显」「等长但内容无关」三个观测点，"
           "三个点一起才构成一次可识别的测量（§85.9）。"
           "⛔ 一旦允许变异动它，系统就能「关掉自己的反馈通道」来提分——"
           "而 V22 已经实测那条通道值是 **−16 格**（关掉它分数反而更高）。"
           "把它放进变异空间，等于把「自欺」写成一个可被自然选择保留的性状。"
           "⛔ 必须被 `coverage_gaps()` 覆盖。"),
)


#: 跨字段不变式。名字**同时是判据的字符串**（读数里点名它），所以不许重复。
INVARIANTS: tuple[tuple[str, str], ...] = (
    ("close_reserve_s < max_wall_s",
     "`core.py` 的余量判据是 `wall_left <= lim.close_reserve_s`；"
     "相等即从第 0 秒起就在余量里 ⇒ 探索一次都不会发生。"),
    ("close_overrun_s <= max_wall_s",
     "宽限若不比整个预算小，它就不是宽限而是「取消预算」；"
     "而 `_wall_deadline()` 的整个可信度建立在「多花的秒数有上界」上。"),
    ("max_stall <= max_steps",
     "每一次停滞都消耗一个 step；阈值大于总步数 ⇒ 它永远到不了，是装饰。"),
)


# ---------------------------------------------------------------------------
# 2. 界的三层检查
# ---------------------------------------------------------------------------
def knob_of(name: str) -> Knob | None:
    """按名字取可动旋钮；没有就返回 `None`（**不**抛异常 —— 调用方要能记账）。"""
    for k in KNOBS:
        if k.name == name:
            return k
    return None


def frozen_of(name: str) -> Frozen | None:
    for f in FROZEN:
        if f.name == name:
            return f
    return None


def coverage_gaps() -> tuple[list[str], list[str]]:
    """返回 (`没被归类` 的字段, `两张表都出现` 的字段)。

    ★ 为什么"全覆盖"是界的一部分而不是形式主义：只要有一个字段既不在 `KNOBS`
      也不在 `FROZEN`，它就是一个**默认可动**的字段 —— 界上开着一个没人检查的门。
    """
    declared = [k.name for k in KNOBS] + [f.name for f in FROZEN]
    real = [f.name for f in fields(Limits)]
    missing = [n for n in real if n not in declared]
    both = sorted({n for n in declared if declared.count(n) > 1})
    unknown = [n for n in declared if n not in real]
    return missing + unknown, both


def violations(limits: Limits) -> list[str]:
    """列出当前 `Limits` 违反了哪几条不变式（名字原文，可直接印进日志）。"""
    out: list[str] = []
    if not (limits.close_reserve_s < limits.max_wall_s):
        out.append("close_reserve_s < max_wall_s")
    if not (limits.close_overrun_s <= limits.max_wall_s):
        out.append("close_overrun_s <= max_wall_s")
    if not (limits.max_stall <= limits.max_steps):
        out.append("max_stall <= max_steps")
    return out


def introduced_violations(before: Limits, after: Limits) -> list[str]:
    """只返回**这次变异引入的**违规。

    ⛔ 必须区分"引入的"与"本来就有的"：环境里可能一开始就有越界的组合
       （`V1_MAX_WALL_S=30` 配默认 `close_reserve_s=45` 就是一例）。
       若一律拒绝，那么一个**本来就有病**的配置里，任何旋钮都改不动 ——
       而拒绝理由会写在"这条变异"头上，那是假归因。
    """
    old = set(violations(before))
    return [v for v in violations(after) if v not in old]


# ---------------------------------------------------------------------------
# 3. 施加一次变异：拒绝 / 不钳制 / 唯一构造点
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Mutation:
    """一次变异尝试的结果。**拒绝也是一条结果**，不许用异常表达。

    为什么不用异常：拒绝是**常态**（`space()` 之外的一切都是拒绝），
    而异常会被上层顺手 `except: pass` —— 那样"我试过但被拒"就从账本里消失了。
    """

    ok: bool
    knob: str
    frm: Any
    to: Any
    why: str
    settings: Settings | None = None     # ok=True 时是新 Settings；拒绝时**必须**是 None

    def as_line(self) -> str:
        head = "✅ 接受" if self.ok else "⛔ 拒绝"
        return f"{head} {self.knob}: {self.frm!r} → {self.to!r}  （{self.why}）"


def _reject(knob: str, frm: Any, to: Any, why: str) -> Mutation:
    return Mutation(False, knob, frm, to, why, None)


def apply_mutation(settings: Settings, name: str, to: Any, *,
                   ledger: Path | None = None) -> Mutation:
    """尝试把 `name` 改成 `to`。合法 ⇒ 返回**新** `Settings`；否则 `ok=False`。

    `ledger` 给了就**每次**都写一行（含拒绝）—— 记账与判定在同一个函数里，
    这样"接受了却没记"在结构上不可能发生（AZ73 钉这一点）。
    """
    # ⚠️ 只在**是字段**时才 getattr：`Limits` 还有 `as_dict` 这样的方法，
    #    对 `nope` 之类的名字取到方法对象会被写进台账 ⇒ `json.dumps` 当场炸。
    _names = {f.name for f in fields(Limits)}
    cur = getattr(settings.limits, name) if name in _names else None

    def done(m: Mutation) -> Mutation:
        if ledger is not None:
            append_record(ledger, record(m))
        return m

    if name not in _names:
        return done(_reject(name, cur, to, f"未知旋钮 {name!r}：不在 `Limits` 里"))
    fr = frozen_of(name)
    if fr is not None:
        return done(_reject(name, cur, to, f"旋钮 {name} 已冻结：{fr.why}"))
    k = knob_of(name)
    if k is None:                                   # 覆盖不全 ⇒ 这里就是那道暗门
        return done(_reject(name, cur, to,
                            f"旋钮 {name} 既不在 KNOBS 也不在 FROZEN ⇒ 界不完整"))

    # ── 类型：不许隐式转换 ────────────────────────────────────────────────
    # ⛔ `isinstance(True, int)` 是 True —— 布尔必须**先**单独挡掉，
    #    否则 `max_stall=True` 会被读成 1，账本里写着 True、跑的是 1。
    if isinstance(to, bool):
        return done(_reject(name, cur, to, f"{name} 是 {k.kind}，收到 bool"))
    if k.kind == "int":
        if not isinstance(to, int):
            return done(_reject(name, cur, to,
                                f"{name} 是 int，收到 {type(to).__name__}"
                                f"（不截断：3.7 改成 3 是替调用方做决定）"))
    else:
        if not isinstance(to, (int, float)):
            return done(_reject(name, cur, to,
                                f"{name} 是 float，收到 {type(to).__name__}"))
        if isinstance(to, float) and (math.isnan(to) or math.isinf(to)):
            return done(_reject(name, cur, to, f"{name} 不接受 NaN/inf"))

    # ── 范围：越界**拒绝**，不钳制 ────────────────────────────────────────
    if not (k.lo <= to <= k.hi):
        return done(_reject(name, cur, to,
                            f"{name} 越界：允许 [{k.lo:g}, {k.hi:g}]，收到 {to!r}"
                            f"（拒绝而不是钳制：钳制会让账本与实跑变成两个事实）"))

    if cur == to:
        return done(_reject(name, cur, to,
                            f"{name} 已经是 {to!r} ⇒ 空变异（没有东西可测）"))

    # ── 唯一构造点：`replace` 保住其余全部字段 ────────────────────────────
    new_limits = replace(settings.limits, **{name: to})
    bad = introduced_violations(settings.limits, new_limits)
    if bad:
        return done(_reject(name, cur, to,
                            f"{name} 会让不变式失效：{'、'.join(bad)}"))

    return done(Mutation(True, name, cur, to,
                         f"界内合法，依据={k.basis}",
                         replace(settings, limits=new_limits)))


def space(settings: Settings) -> list[tuple[str, Any]]:
    """当前配置下的**合法邻域**：每个旋钮各走一步，越界的步直接不产生。

    确定性（无随机）：同样的配置永远给同样的清单 ⇒ 探针可以钉住它的基数，
    "搜索空间有多大"从一句形容变成可核验的数。
    """
    out: list[tuple[str, Any]] = []
    for k in KNOBS:
        cur = getattr(settings.limits, k.name)
        for sign in (+1, -1):
            raw = cur + sign * k.step
            val = int(round(raw)) if k.kind == "int" else float(round(raw, 3))
            if val == cur or not (k.lo <= val <= k.hi):
                continue
            out.append((k.name, val))
    return out


# ---------------------------------------------------------------------------
# 5. 台账
# ---------------------------------------------------------------------------
def record(m: Mutation, *, calib_before: str = "", calib_after: str = "",
           harness: str = "", ts: float | None = None) -> dict[str, Any]:
    """一行台账。**拒绝也记** —— 「我试过但被拒」本身是证据。"""
    return {
        "ts": round(time.time() if ts is None else float(ts), 3),
        "ok": bool(m.ok),
        "knob": m.knob,
        "frm": m.frm,
        "to": m.to,
        "why": m.why,
        # 两个口径签名并排：变异**必然**换口径（`limits` 在 `CALIBER_KEYS` 里），
        # 所以台账自己就要留着"变异前/后"两把尺子的名字。
        "calib_before": calib_before,
        "calib_after": calib_after,
        # 内核指纹：变异不改源码 ⇒ 这一列在同一进程里应当**逐字不变**。
        # 它和上面那两列一起说明"同一个内核、不同口径"（AZ74）。
        "harness": harness,
    }


def append_record(path: Path, rec: Mapping[str, Any]) -> None:
    """追加一行 JSON。一行一条，永不重写文件 —— 证据只能追加。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(rec), ensure_ascii=False, sort_keys=True) + "\n")
