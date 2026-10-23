#!/usr/bin/env python3
"""「变异有界」的四类自检：界 / 拒绝 / 判定 / 生效。

## 这个探针量的是什么，不量什么

它量**机械事实**：

1. 界是**声明**出来的（`KNOBS` + `FROZEN` 覆盖 `Limits` 的每一个字段，没有暗门）；
2. 越界与类型错**被拒绝**，而且**不被钳制**（拒绝后配置逐字不变）；
3. 判定有四个前提，缺一个就 `UNKNOWN`（尤其"缺安慰剂臂"）；
4. 旋钮**真的生效** —— 同一个离线桩、同一道题，只改 `max_stall`
   ⇒ 停滞轮数等于阈值（1 / 2 / 3），状态都是 `stalled`，交付都非空。

⛔ 它**不**量"变异让内核变强了"。那需要一套能裁决的题集（现有 4 题天花板 0.70，
连续四轮负结果），本轮没有做 —— 边界写在 DESIGN §33.8，不写成"已实现"。

## 为什么"生效"要单独量

`apply_mutation` 返回 `ok=True` 只证明**配置对象换了**，不证明**内核读了它**。
本项目的老账里有一整类"配了没生效"（`V1_LLM_RETRIES=0` 读不到、`--max-tokens`
没进 Planner、`external_answer_tool` 从未开启）。所以这里的第 4 类检查
是让真 `Core` 跑三臂，看读数是否按阈值单调变化。

退出码：0 = 全部成立；1 = 有不成立项。
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V1))

#: 动作通道被堵死的形状（与 `probe_stall.py` 同一个形态：同一个 `recall` 反复提）。
SAME = ('{"candidates": [{"intent": "再查一次", "tool": "recall", '
        '"args": {"query": "goal"}, "rationale": "查记忆"}]}')
ANSWER_MARK = "请直接给出最终答案"
ANSWER = "停滞之后仍然交付的答案（来自答案通道）"
GOAL = "读一下这个文件"

_CHECKS: list[tuple[str, bool]] = []


def ck(name: str, cond: bool) -> bool:
    _CHECKS.append((name, bool(cond)))
    return bool(cond)


# ---------------------------------------------------------------------------
# 0. 环境
# ---------------------------------------------------------------------------
os.environ.setdefault("V1_LLM_OFFLINE", "1")
_TMP = Path(tempfile.mkdtemp(prefix="v1_mutate_"))
os.environ["V1_STATE_DIR"] = str(_TMP / "state")

from bench import mutation_check as mc                         # noqa: E402
from mw import bench, mutate as mu                            # noqa: E402
from mw.config import Limits, Settings                         # noqa: E402
from mw.loop import Curve, CurvePoint, harness_sha             # noqa: E402

S = Settings()
BASE_STALL = S.limits.max_stall


# ---------------------------------------------------------------------------
# 1. 界：声明 + 全覆盖 + 空间闭合
# ---------------------------------------------------------------------------
missing, both = mu.coverage_gaps()
ck("① `Limits` 每个字段都被归类（KNOBS ∪ FROZEN 全覆盖）", missing == [] and both == [],
   )
ck("② 每个旋钮都写了依据，且依据是两种之一",
   all(k.basis in mu.BASES and k.why for k in mu.KNOBS))
ck("③ 每个冻结项都写了理由（否则下次顺手就能挪进 KNOBS）",
   all(f.why for f in mu.FROZEN))
ck("④ int 旋钮的界是整数、lo<hi、step>0",
   all((isinstance(k.lo, int) and isinstance(k.hi, int)) if k.kind == "int"
       else k.lo < k.hi for k in mu.KNOBS)
   and all(k.lo < k.hi and k.step > 0 for k in mu.KNOBS))

space = mu.space(S)
ck("⑤ 合法邻域每一步都能被接受（空间闭合）",
   len(space) > 0 and all(mu.apply_mutation(S, n, v).ok for n, v in space))
ck("⑥ 邻域是确定性的：同样的配置给同样的清单", mu.space(S) == space)


# ---------------------------------------------------------------------------
# 2. 拒绝，不是钳制
# ---------------------------------------------------------------------------
def rej(name: str, to: object) -> mu.Mutation:
    return mu.apply_mutation(S, name, to)


out_lo = rej("max_stall", 0)
out_hi = rej("max_stall", 7)
ck("⑦ 越界两个方向都拒绝，且理由点名区间（不是只说「非法」）",
   (not out_lo.ok) and (not out_hi.ok)
   and "[1, 6]" in out_lo.why and "[1, 6]" in out_hi.why)
ck("⑧ 拒绝**不钳制**：`ok=False` 时没有新 Settings，原配置逐字未变",
   out_lo.settings is None and out_hi.settings is None
   and S.limits == Settings().limits)
ck("⑨ 类型不隐式转换：int 旋钮收到 3.0 / True 都拒绝"
   "（`isinstance(True, int)` 是 True —— 不挡就会把 True 记成 1）",
   (not rej("max_stall", 3.0).ok) and (not rej("max_stall", True).ok)
   and "bool" in rej("max_stall", True).why)
ck("⑩ float 旋钮拒绝 NaN/inf，且拒绝空变异（没东西可测）",
   (not rej("close_reserve_s", math.nan).ok)
   and (not rej("max_stall", BASE_STALL).ok)
   and "空变异" in rej("max_stall", BASE_STALL).why)
ck("⑪ 冻结旋钮拒绝，理由写「冻结」并给出理由原文",
   (not rej("max_wall_s", 240.0).ok) and "冻结" in rej("max_wall_s", 240.0).why)
ck("⑫ 未知旋钮拒绝（含 `Limits` 的方法名 —— 取到方法对象会让台账写不出去）",
   (not rej("nope", 1).ok) and (not rej("as_dict", 1).ok))


# ---------------------------------------------------------------------------
# 3. 不变式：只拒绝「引入的」违规
# ---------------------------------------------------------------------------
from dataclasses import replace                                  # noqa: E402

# 已有病：余量 40 ≥ 墙钟 30 ⇒ INV1 本来就破了。
_sick = replace(S, limits=replace(S.limits, max_wall_s=30.0, close_reserve_s=40.0))
_before_v = mu.violations(_sick.limits)
_fix = mu.apply_mutation(_sick, "close_reserve_s", 29.0)
# 同一份「病」配置里改一个**无关**旋钮 ⇒ 必须允许（不能因为既有病就把所有变异封死）。
# 取邻域的最后一步（`working_memory_items`）：与不变式无关，且与当前值不同。
_unrelated = mu.apply_mutation(_sick, *space[-1])
# 健康配置里把余量推到 ≥ 墙钟 ⇒ 必须拒绝。
_health = replace(S, limits=replace(S.limits, max_wall_s=30.0, close_reserve_s=29.0))
_break = mu.apply_mutation(_health, "close_reserve_s", 30.0)
ck("⑬ 不变式：`close_reserve_s >= max_wall_s` 被拒绝（core.py 那条 `wall_left <= …` "
   "从第 0 秒就成立 ⇒ 探索一次都不会发生）",
   _before_v == ["close_reserve_s < max_wall_s"] and (not _break.ok)
   and "close_reserve_s < max_wall_s" in _break.why)
ck("⑭ 既有违规**不惩罚**：一份本来就越界的配置里，改无关旋钮仍然允许"
   "（否则拒绝理由全写在这条变异头上 = 假归因），而把它修好也允许",
   _unrelated.ok and _fix.ok)
ck("⑮ 不变式：`max_stall > max_steps` 被拒绝（每次停滞都吃掉一个 step）",
   not mu.apply_mutation(replace(S, limits=replace(S.limits, max_steps=4)),
                         "max_stall", 6).ok)


# ---------------------------------------------------------------------------
# 4. 判定：四个前提（含反证）
# ---------------------------------------------------------------------------
def verdict_of(**kw):
    base = dict(knob="max_stall", diff=["max_stall"], base_mean=0.60,
                cand_mean=0.85, placebo_delta=0.05, noise_floor=0.10)
    base.update(kw)
    return mc.mutation_verdict(**base)


ck("⑯ 前提齐 ⇒ KEEP（净增益 +0.20 > 噪声 0.10）", verdict_of()[0] == mc.KEEP)
ck("⑰ 缺噪声下限 ⇒ UNKNOWN（不默认 0）",
   verdict_of(noise_floor=None)[0] == mc.UNKNOWN)
ck("⑱ 缺安慰剂臂 ⇒ UNKNOWN —— **即使 Δ 很大**（反证：这是最容易被放过的一格）",
   verdict_of(placebo_delta=None, cand_mean=0.99)[0] == mc.UNKNOWN)
ck("⑲ 多变量 ⇒ UNKNOWN（两臂 Limits 差两项，Δ 不可归因）",
   verdict_of(diff=["max_stall", "recall_k"])[0] == mc.UNKNOWN)
ck("⑳ 安慰剂吃掉全部收益 ⇒ NO_EVIDENCE（+0.25 的 Δ 是这一跑的漂移）",
   verdict_of(placebo_delta=0.25)[0] == mc.NO_EVIDENCE)
ck("㉑ 明确变差 ⇒ REVERT", verdict_of(cand_mean=0.30, placebo_delta=0.0)[0] == mc.REVERT)
ck("㉒ 只有 KEEP 才保留（其余三种一律回退）",
   mc.should_keep(mc.KEEP) and not any(mc.should_keep(v) for v in
                                       (mc.REVERT, mc.NO_EVIDENCE, mc.UNKNOWN)))


def _detector_works(fn) -> bool:
    """反证自检：喂一个「恒 KEEP」的坏判定，检测必须判它坏。

    ★ 没有这一格，上面那 7 条可以全是死代码 —— 检测器自己从不失败，
      就没人知道它还在不在。
    """
    bad = lambda **_kw: (mc.KEEP, "假判定")                   # noqa: E731
    return fn(bad) is False


def _verdict_gate(vf) -> bool:
    """把「缺安慰剂不许给 KEEP」写成可喂坏样本的形状。"""
    return vf(knob="max_stall", diff=["max_stall"], base_mean=0.60, cand_mean=0.99,
              placebo_delta=None, noise_floor=0.10)[0] != mc.KEEP


ck("㉓ 反证：检测器喂坏样本必须判红（恒 KEEP 的假判定 ⇒ False）",
   _detector_works(_verdict_gate) and _verdict_gate(mc.mutation_verdict))


# ---------------------------------------------------------------------------
# 5. 台账：拒绝也记
# ---------------------------------------------------------------------------
_led = _TMP / "mutations.jsonl"
mu.apply_mutation(S, "max_stall", 0, ledger=_led)             # 拒绝
_to_acc = BASE_STALL + 1 if BASE_STALL < 6 else BASE_STALL - 1
_acc = mu.apply_mutation(S, "max_stall", _to_acc, ledger=_led)
_rows = [json.loads(x) for x in _led.read_text(encoding="utf-8").splitlines()]
ck("㉔ 拒绝也落台账（一行一条 JSON，字段齐全）",
   len(_rows) == 2 and [r["ok"] for r in _rows] == [False, True]
   and set(_rows[0]) >= {"ts", "ok", "knob", "frm", "to", "why",
                         "calib_before", "calib_after", "harness"})
ck("㉕ 接受的那条带新配置，拒绝的那条不带（`None` 不许冒充空配置）",
   _acc.ok and _acc.settings is not None)


# ---------------------------------------------------------------------------
# 6. 口径：变异必然换口径，但内核不变 ⇒ 曲线必须断段
# ---------------------------------------------------------------------------
# ⚠️ 这两个口径对象**只填 limits**：本题要证明的只是「limits 一个字段的差异
#    如何传进口径签名」，不是复现一次真实实验的口径（其余 11 键是同一个哨兵）。
_c_before = bench.Caliber(limits=S.limits.as_dict())
_c_after = bench.Caliber(limits=_acc.settings.limits.as_dict())
_h_before = harness_sha(V1)
ck("㉖ 单变量：两臂 Limits 只差被声明的那个旋钮",
   mc.limits_diff(S.limits.as_dict(), _acc.settings.limits.as_dict()) == ["max_stall"])
ck("㉗ 但它**换口径**（`limits` 在 CALIBER_KEYS 里）⇒ 签名不同、diff 点名 limits",
   _c_before.signature() != _c_after.signature()
   and _c_before.diff(_c_after) == ["limits"])
# 用真实 `Curve` 证明后果：三个点一段，换个旋钮再三个点 ⇒ 两段，8 个点都不接。
_pts = ([CurvePoint(round=i, ts=1.0 * i, calib=_c_before.signature(), mean=0.6,
                    n=4, spread=0.0, trustworthy=True, harness=_h_before)
         for i in range(1, 4)]
        + [CurvePoint(round=i, ts=1.0 * i, calib=_c_after.signature(), mean=0.7,
                      n=4, spread=0.0, trustworthy=True, harness=_h_before)
           for i in range(4, 7)])
_cv = Curve(points=list(_pts))
_segs = _cv.segments()
ck("㉘ 曲线按口径断段：3 + 3 ⇒ 两段（所以「变异有没有变好」不能用曲线连着看）",
   [len(s) for s in _segs] == [3, 3])
ck("㉙ 变异不改源码 ⇒ `harness_sha` 逐字不变（同一个内核、不同口径）",
   harness_sha(V1) == _h_before)


# ---------------------------------------------------------------------------
# 7. 生效：真 `Core` 三臂，只改 `max_stall`
# ---------------------------------------------------------------------------
def arm(stall: int):
    """同一个桩、同一道题，只换 `max_stall`。"""
    from mw.core import Core
    from mw.llm import StubLLM
    from mw.memory import MemoryStore

    # 基线臂**不走变异**：`max_stall` 已经是那个值，空变异按设计会被拒绝
    # （§3 的 ⑩）。"不改"本来就不是一次变异，不该在台账里占一行。
    if stall == BASE_STALL:
        settings = S
    else:
        m = mu.apply_mutation(S, "max_stall", stall)
        assert m.ok, m.why
        settings = m.settings
    llm = StubLLM(scripts=[(ANSWER_MARK, ANSWER)], default=SAME)
    out = Core(settings=settings, llm=llm,
               memory=MemoryStore(_TMP / f"mem_{stall}.db"),
               wire_external=False, log=lambda *_a: None).run(GOAL)
    return out, settings


_lo_s = 1
_hi_s = min(BASE_STALL + 1, 6)
_arms = [(s,) + arm(s) for s in (_lo_s, BASE_STALL, _hi_s)]
print("=" * 74)
print("生效读数：只改 `max_stall`（离线桩，同一道题）")
print("=" * 74)
for s_, out, st in _arms:
    lp = out.stats.get("loop") or {}
    tag = "基线（未改）" if s_ == BASE_STALL else "已变异"
    print(f"  max_stall={s_:2d}  status={out.status:8s} 停滞={lp.get('stalled')} "
          f"重复跳过={lp.get('repeats_skipped')} 不同动作={lp.get('attempted_actions')} "
          f"交付={len(out.answer):3d}字  {tag}")

ck("㉚ 三臂都走到停滞出口，且**都交了东西**（§32 的出口在变异后仍然有效）",
   all(o.status == "stalled" and o.answer for _, o, _ in _arms))
ck("㉛ 停滞轮数**恰好等于**阈值 ⇒ 旋钮真的被内核读了（不是只换了配置对象）",
   [(o.stats.get("loop") or {}).get("stalled") for _, o, _ in _arms]
   == [_lo_s, BASE_STALL, _hi_s])
ck("㉜ 反证：三臂读数不许全一样（全一样 = 这个探针什么也没测到）",
   len({(o.stats.get("loop") or {}).get("stalled") for _, o, _ in _arms}) == 3)

print()
bad = 0
for name, cond in _CHECKS:
    print(f"  {'✅' if cond else '❌'} {name}")
    bad += 0 if cond else 1
print()
print(f"结论：{'✅ 界 / 拒绝 / 判定 / 生效 四类都成立' if not bad else f'❌ {bad} 项不成立'}")
raise SystemExit(0 if not bad else 1)
