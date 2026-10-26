#!/usr/bin/env python3
"""第九十六轮 §63 的确定性复现：**收尾调用被墙钟裁掉**那一秒的竞态。

## 为什么需要这个文件

§62 的结论是「一份已经写好的答案，输给了一个常数（45.0 秒）」——
而它的证据来自**一次采样运气**：真模型那发收尾调用要 45.6s，被裁到 44.5s，
差 1.1 秒。这种东西不可复跑：换一晚、换一台机器、通道快一点，它就消失了
（§62 实测通道从 46s/次漂到 29s/次）。

⇒ 「改前会失败、改后不失败」这件事必须能在**零模型、零网络、十秒内**重演，
否则它只能靠 n=3 的真模型读数去碰运气，而那正是 §62 被自己的对照臂否证的场景。

## 场景（数字全部是构造的，但形状与真机一致）

    base（inner.timeout_s）= 60.0      —— 真机 120s，这里只要「比 left 大」即可
    wall_s                = 8.0        —— 真机 120s
    pre_s（已用）          = 2.5        —— 真机 75.5s
    收尾那一发需要的秒数     = 6.0        —— 真机 45.6s
    close_overrun_s       = 2.5        —— 新增字段

    ｜———— 已用 2.5 ————｜
    0                  2.5                                    8.0
                        收尾起点：reserve_release()
                        旧：left = 8.0 − 2.5          = 5.5  ⇒ 裁到 5.5 ⇒ 6.0 超时 ⇒ 交轨迹
                        新：left = 8.0+2.5 − 2.5      = 8.0  ⇒ 给 8.0 ⇒ 6.0 跑完 ⇒ 交答案
    10.5 是新臂收尾调用结束的时刻（超出墙钟 2.5s），仍在宽限内。

⚠️ 真机上 `left` 是 44.5s、`base` 是 120s ⇒ **`left` 先撞上**，所以改 `left` 有效。
   若在桩上把 `base` 设得比 `left` 小（例如 4.5），改 `left` 根本看不见 ——
   这是本探针第一版写错的地方，已固化成 **C0**。

## 七条反证（缺一条，下面的绿就不算绿）

| # | 反证 | 防的是什么 |
|---|---|---|
| C0 | 桩的 `timeout_s` **真的**被读到（`_attempt_timeout()` 不是 inf） | 桩没有 `timeout_s` ⇒ 裁剪整条路径不存在 ⇒ 全绿是假绿 |
| C1 | `closing=False` 时新旧公式在网格上**逐位相同** | 「不影响探索阶段」这句话没有机制保证 |
| C2 | `closing=True` 时新 ≥ 旧，且差值恰为宽限给的量 | 宽限随便加（不看剩余） |
| C3 | 端到端：旧臂交 `history`、新臂交 `收尾调用` | 只测公式不测链路 = 测了个算术 |
| C4 | 宽限有界：`overrun` 极大时仍 `_to ≤ base` | 「宽限」退化成「取消预算」 |
| C5 | `closing=False` 且 `overrun>0` ⇒ 与 `overrun=0` 逐位相同 | 忘了判 `closing`，探索阶段也超支 |
| C6 | 旧臂 `wall_overrun_grants==0`、新臂 ≥1 | 计数恒 0（没有消费者）或恒 1（没分辨率） |

用法：
    python3 bench/probe_closeoverrun.py --self-check     # 零模型、零网络、约 13 秒
    python3 bench/probe_closeoverrun.py --grid          # 只打印网格，不睡（约 0.1 秒）
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mw import config as cfg                                   # noqa: E402
from mw.budget import Budget, BudgetExhausted, Ledger          # noqa: E402
from mw.core import Core, _LedgeredLLM                          # noqa: E402
from mw.llm import LLMError                                     # noqa: E402
from mw.memory import MemoryStore                               # noqa: E402

# ── 场景常数（改这里就是改场景，读 `RESULTS.md` 时必须一起看）─────────────
BASE_S = 60.0        # inner.timeout_s
WALL_S = 8.0         # budget.wall_s
PRE_S = 2.5          # 进入收尾时已经用掉的秒数
COST_S = 6.0         # 收尾那一发真正需要的秒数
OVERRUN_S = 2.5      # 新增的宽限
GOAL = "写一个函数 norm_s63(rows)，返回处理后的字符串列表。"


class SlowLLM:
    """按 `timeout_s` **真的**睡觉、睡不够就报超时的桩。

    ⚠️ 它必须有 `timeout_s` 属性：`_attempt_timeout()` 用
    `getattr(inner, "timeout_s", None)` 判断"要不要裁剪"，
    `StubLLM` **没有**这个属性 ⇒ 走的是 `return inf`（不裁剪）那条路
    ⇒ 拿它做本探针等于什么也没测。C0 就是钉这一条的。
    """

    timeout_s = BASE_S

    def __init__(self, cost_s: float = COST_S) -> None:
        self.cost_s = float(cost_s)
        self.calls: list[float] = []      # 每次实际拿到的 timeout_s
        self.slept: list[float] = []

    def complete(self, messages, *, max_tokens: int = 512,       # noqa: ANN001
                 temperature: float | None = None,
                 timeout_s: float | None = None) -> str:
        cap = self.timeout_s if timeout_s is None else float(timeout_s)
        self.calls.append(cap)
        # 模拟一次"只要不被裁、本来能跑完"的调用
        use = min(self.cost_s, cap)
        time.sleep(use)
        self.slept.append(use)
        if cap < self.cost_s:
            # 真机上是 `HTTPLLM` 把 `socket.timeout` 翻成 transient 的 `LLMError`
            raise LLMError(f"超时：给了 {cap:g}s，需要 {self.cost_s:g}s", transient=True)
        return "```python\ndef norm_s63(rows):\n    return [r.strip() for r in rows]\n```"


def _mk_counted(overrun: float, *, base: float = BASE_S, wall: float = WALL_S,
                reserve: float = 0.0, pre: float = PRE_S,
                freeze: bool = True) -> tuple[_LedgeredLLM, SlowLLM]:
    """只有账本与包装器，**不建 Core**。

    ⚠️ 网格类反证（C0/C1/C2/C4/C5）只需要 `_attempt_timeout()` ——
    给它们造 `Core`（每个都要建一个 SQLite 记忆库）纯属浪费：
    实测每个约 0.15s，几十个就是十秒级，而 7i 是个每次提交都要跑的步骤。
    ⛔ 省掉的只能是**装置开销**，不能省掉被测物：`_LedgeredLLM` 一字不差。
    """
    inner = SlowLLM()
    inner.timeout_s = base
    led = Ledger(budget=Budget(steps=1, llm_calls=4, wall_s=wall, candidates=1),
                 t0=time.monotonic() - pre)
    if freeze:
        led.elapsed = lambda: pre        # type: ignore[method-assign]  见 _mk 的注释
    counted = _LedgeredLLM(inner, led, retries=1, backoff_s=0.0,
                           wall_reserve_s=reserve, close_overrun_s=overrun)
    return counted, inner


def _mk(overrun: float, *, base: float = BASE_S, wall: float = WALL_S,
        reserve: float = 0.0, pre: float = PRE_S,
        freeze: bool = True) -> tuple[Core, _LedgeredLLM, SlowLLM]:
    """造一个只差 `close_overrun_s` 的最小内核。

    ⚠️ `freeze=True` 时把 `ledger.elapsed()` 换成常量 `pre`。
    为什么必须这么做：用 `t0 = now - pre` 模拟"已用 pre 秒"，两次构造之间
    会差几十微秒 —— 在 `MIN_ATTEMPT_S` 那个边界上，几十微秒就足以让
    `_to` 从 `5.5` 变成 `0.0`，于是「逐位相同」变成「4 处不同」。
    ⛔ 这不是容差问题，是**测的东西不是纯函数**：要逐位比较就必须先把时钟钉住。
    （第一版没钉，C0/C1/C2 全红 —— 三条红里没有一条是真的缺陷。）
    """
    lim = cfg.Limits(max_steps=1, max_wall_s=120.0,          # 不变式：reserve < max_wall_s
                     close_reserve_steps=0, verify_retries=0,
                     llm_retries=1, llm_retry_backoff_s=0.0)
    inner = SlowLLM()
    inner.timeout_s = base
    core = Core(settings=cfg.Settings(limits=lim), llm=inner,
                memory=MemoryStore(Path(_TMPDIR) / f"probe63_{id(inner)}.db"))
    core.log = lambda m: None                                # noqa: ARG005
    led = Ledger(budget=Budget(steps=1, llm_calls=4, wall_s=wall, candidates=1),
                 t0=time.monotonic() - pre)
    if freeze:
        led.elapsed = lambda: pre        # type: ignore[method-assign]  见上面那段
    counted = _LedgeredLLM(inner, led, retries=1, backoff_s=0.0,
                           wall_reserve_s=reserve, close_overrun_s=overrun)
    core.ledger = led
    core._counted = counted
    core.planner.llm = counted
    core._checks = ()                 # 收尾闸与本探针无关，关掉以免噪声
    core._reset_verify(GOAL)
    return core, counted, inner


_TMPDIR = "."
try:                                                             # pragma: no cover
    import tempfile
    _TMPDIR = tempfile.mkdtemp(prefix="probe63_")
except Exception:                                                # pragma: no cover
    _TMPDIR = "."


def _run_close(overrun: float, *, reserve: float = 0.0):
    """真跑一次 `_close_out`，返回 (origin, counted, seen)。"""
    core, counted, _inner = _mk(overrun, reserve=reserve, freeze=False)
    seen: dict = {}

    def _done(status: str, answer: str, reason: str) -> str:      # noqa: ANN001
        seen.update(status=status, answer=answer, reason=reason)
        return "ok"

    core._close_out(GOAL, ["[step 1] finish({'answer': 'x'})"], _done)
    return core.closeout_stats["origin"].get("收尾调用", 0), counted, seen


def _grid_rows(overrun: float, reserve: float, base: float):
    """同一组 elapsed 上，`close_overrun_s=0` 与 `close_overrun_s=overrun` 的 `_to`。"""
    out = []
    for elapsed in (0.5, 1.0, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 7.0, 7.5, 8.0, 9.0):
        row = []
        for ov in (0.0, overrun):
            counted, _ = _mk_counted(ov, base=base, reserve=reserve, pre=elapsed)
            row.append(counted._attempt_timeout())
        out.append((elapsed, row[0], row[1]))
    return out


# ---------------------------------------------------------------------------
# 反证组
# ---------------------------------------------------------------------------
def _self_check() -> int:
    ok_all = True
    print("=" * 74)
    print("  §63 探针自检（零模型、零网络）—— 七条反证组 C0~C6")
    print("=" * 74)
    t0 = time.monotonic()

    def _say(tag: str, ok: bool, msg: str) -> None:
        nonlocal ok_all
        ok_all = ok_all and ok
        print(f"  {'✅' if ok else '❌'} {tag} {msg}")

    # ── C0 桩真的被裁剪 ─────────────────────────────────────────────
    c0, _i = _mk_counted(0.0)
    _to0 = c0._attempt_timeout()
    _say("C0", _to0 != float("inf") and abs(_to0 - (WALL_S - PRE_S)) < 1e-9,
         f"桩的 timeout_s 被读到且裁剪生效：_to={_to0:g}s"
         f"（期望 {WALL_S - PRE_S:g}s = wall−已用）；inf ⇒ 这条路径不存在，后面全是假绿")

    # ── C1 探索阶段逐位不变 ────────────────────────────────────────
    bad1 = []
    for reserve in (0.0, 4.0):
        for base in (3.0, 60.0):
            for el, old, new in _grid_rows(0.0, reserve, base):
                # overrun=0 时"新"就是旧公式本身；这里比的是**同为 0**的两条路径
                if not (old == new):
                    bad1.append((reserve, base, el, old, new))
    _say("C1", not bad1, f"`close_overrun_s=0` 时新旧路径逐位相同（{len(bad1)} 处不同）"
                         " —— 这是 A/B 的对照组：不给宽限就等于没改")

    # ── C5 收尾前不给宽限（closing=False 时不生效）─────────────────
    bad5 = []
    for reserve in (4.0,):
        for base in (3.0, 60.0):
            for el, old, _ in _grid_rows(OVERRUN_S, reserve, base):
                # 未 release 时，ov=OVERRUN 与 ov=0 必须逐位相同
                ca, _ = _mk_counted(OVERRUN_S, base=base, reserve=reserve, pre=el)
                cb, _ = _mk_counted(0.0, base=base, reserve=reserve, pre=el)
                if ca._attempt_timeout() != cb._attempt_timeout():
                    bad5.append((base, el, ca._attempt_timeout(), cb._attempt_timeout()))
    _say("C5", not bad5, f"`closing=False` 且 overrun={OVERRUN_S:g}s ⇒ 与 overrun=0 逐位相同"
                         f"（{len(bad5)} 处不同）—— 探索阶段一秒都没多给")

    # ── C2/C4 收尾阶段：单调、差值恰好、有界 ───────────────────────
    bad2 = []
    for el in (1.0, 2.0, 3.0, 4.0):
        co, _ = _mk_counted(0.0, pre=el)
        cn, _ = _mk_counted(OVERRUN_S, pre=el)
        co.reserve_release()
        cn.reserve_release()
        o, n = co._attempt_timeout(), cn._attempt_timeout()
        left_old = WALL_S - co.ledger.elapsed()
        want = min(BASE_S, max(0.0, left_old) + OVERRUN_S)
        if not (n >= o and abs(n - want) < 1e-6):
            bad2.append((el, o, n, want))
    _say("C2", not bad2,
         f"收尾阶段 新 ≥ 旧 且 新 == min(base, 旧剩余+{OVERRUN_S:g})（{len(bad2)} 处不符）")

    ch, _ = _mk_counted(1e6, pre=2.5)
    ch.reserve_release()
    _h = ch._attempt_timeout()
    _say("C4", _h <= BASE_S + 1e-9,
         f"宽限=1e6s 时 `_to` 仍被 base 夹住：{_h:g}s ≤ {BASE_S:g}s"
         " —— 「宽限」没有退化成「取消预算」")

    # ── C3 端到端（唯一会真的睡 ~11.5 秒的两段）────────────────────
    n_old, c_old_k, seen_old = _run_close(0.0)
    n_new, c_new_k, seen_new = _run_close(OVERRUN_S)
    old_ans = seen_old.get("answer", "")
    new_ans = seen_new.get("answer", "")
    _say("C3", n_old == 0 and n_new == 1 and "finish(" in old_ans and "def norm_s63" in new_ans,
         f"旧臂 收尾调用={n_old} 交={old_ans[:26]!r}… ｜ "
         f"新臂 收尾调用={n_new} 交={new_ans[:26]!r}…")

    # ── C6 记账 ───────────────────────────────────────────────────
    _say("C6", c_old_k.wall_overrun_grants == 0 and c_new_k.wall_overrun_grants >= 1,
         f"宽限计数：旧臂 {c_old_k.wall_overrun_grants}（必须 0）"
         f" / 新臂 {c_new_k.wall_overrun_grants}（必须 ≥1）；"
         f"实际超出={c_new_k.overrun_used_s:.2f}s ≤ {OVERRUN_S:g}s")

    print("-" * 74)
    print(f"  C3 的两段真睡用时 {time.monotonic() - t0:.1f}s；其余全部零睡眠")
    print(f"  自检结果：{'全绿（可以起跑）' if ok_all else '红项 ⇒ 拒绝起跑'}")
    return 0 if ok_all else 1


def _grid() -> int:
    print(f"  base={BASE_S:g}s wall={WALL_S:g}s 已用={PRE_S:g}s overrun={OVERRUN_S:g}s")
    print("  已用   探索(未release)  收尾(ov=0)   收尾(ov=2.5)")
    for el in (0.5, 1.0, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 7.0):
        cx, _ = _mk_counted(0.0, pre=el)
        explore = cx._attempt_timeout()
        cy, _ = _mk_counted(0.0, pre=el)
        cy.reserve_release()
        old = cy._attempt_timeout()
        cz, _ = _mk_counted(OVERRUN_S, pre=el)
        cz.reserve_release()
        new = cz._attempt_timeout()
        print(f"  {el:5.1f}   {explore:13.2f}   {old:10.2f}   {new:10.2f}")
    return 0


def main(argv: list[str]) -> int:
    if "--self-check" in argv:
        return _self_check()
    if "--grid" in argv:
        return _grid()
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
