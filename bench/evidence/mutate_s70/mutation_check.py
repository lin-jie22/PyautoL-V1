#!/usr/bin/env python3
"""变异的**裁决**：四个前提 + 四种结果。

## 为什么这个文件在 `bench/` 而不在 `mw/`

第一版把它写进了 `mw/mutate.py`，契约 **AO11** 当场判红：

> 内核不许知道「安慰剂」的存在（`mw/*.py` 里连这个词都不许出现）——
> 安慰剂是**测量仪器**，不是能力。一旦内核里有了「我被告知这是一条安慰剂」
> 的分支，测到的就不再是这个系统，而是这个系统的应试技巧。

判红是对的，而且比它写下的那句更狠：**裁决权本来就不该在内核里**。
本项目的第 0 条约束一直是「判定标准必须可读不可写、出自内核之外」——
让内核拿着"证据"决定自己该不该被保留，等于把裁判席搬进更衣室。
所以：`mw/mutate.py` 只管**机制**（界、拒绝、台账），
`bench/mutation_check.py` 管**裁决**（要不要保留）。两边都不越界。

## 四个前提

- **单变量**：两臂的 `Limits` 必须**只差**被声明的那个旋钮（`limits_diff` 恰好一项）。
- **噪声下限**：`None` ⇒ 不判定。默认 0 等于"任何抖动都算改进"。
- **等长对照臂**：缺它就不判 —— 没有对照，Δ 分不清是旋钮的功劳还是这一跑的运气
  （7u 实测：一条自认为"中性"的指令被模型读成处理臂，Δ+0.15）。
- **只有 `KEEP` 才保留**；其余一律回退 ——「没证据说更好 = 不改」。

## 它不做什么

不做探索、不跑题、不写台账。它是**纯函数**：给数字，给结论，给理由。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

KEEP = "KEEP"                 # 有证据说更好 ⇒ 保留
REVERT = "REVERT"             # 有证据说更差 ⇒ 回退
NO_EVIDENCE = "NO_EVIDENCE"   # 量准了，但扣掉对照臂后落回噪声内 ⇒ 按「不改」处理
UNKNOWN = "UNKNOWN"           # 前提不满足 ⇒ 这一跑没资格说话

VERDICTS = (KEEP, REVERT, NO_EVIDENCE, UNKNOWN)
#: 与 `Curve.trend` 同一个浮点容差理由：`0.85 - 0.60 - 0.05` 会留下 1e-17 的残差，
#: 而没有容差时"净增益 > 噪声"对**恰好等于**的情形会随机为真。
_TOL = 1e-9


def limits_diff(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[str]:
    """两个 `Limits.as_dict()` 之间**有哪些字段不一样**（排序后返回名字）。

    ★ 为什么需要它而不是直接比 `limits` 这一个口径键：`Caliber.limits` 是**整个字典**，
       它的 `diff()` 只会说"limits 变了"，说不出"变的是哪一个旋钮"。
       而"单变量"要的正是后者 —— 少了它，一次同时改两个旋钮的变异会被读成
       "只改了一个"（口径只报一个键）。
    """
    sentinel = object()
    keys = sorted(set(a) | set(b))
    return [k for k in keys if a.get(k, sentinel) != b.get(k, sentinel)]


def mutation_verdict(*, knob: str, diff: Sequence[str],
                     base_mean: float, cand_mean: float,
                     placebo_delta: float | None,
                     noise_floor: float | None) -> tuple[str, str]:
    """这次变异该不该保留。返回 (verdict, why)。

    `placebo_delta` = 等长安慰剂臂的 Δ（同样两臂、同样的题，只是把旋钮
    改成一个**语义惰性**的值）。用它扣掉"这一跑本来就会有的漂移"。
    """
    if list(diff) != [knob]:
        return UNKNOWN, (f"两臂的 Limits 差 {sorted(diff)}，不是只有 {knob} "
                         f"⇒ 不是单变量，Δ 不可归因")
    if noise_floor is None:
        return UNKNOWN, "没有噪声下限 ⇒ 任何差值都不可判定（不默认 0）"
    if placebo_delta is None:
        return UNKNOWN, ("缺等长安慰剂臂 ⇒ 分不清是旋钮还是这一跑的运气"
                         "（7u：自认为中性的指令被读成处理臂，Δ+0.15）")
    delta = float(cand_mean) - float(base_mean)
    net = delta - float(placebo_delta)
    if net > float(noise_floor) + _TOL:
        return KEEP, (f"扣掉安慰剂 {placebo_delta:+.4f} 后净增益 {net:+.4f} "
                      f"> 噪声 {noise_floor:.4f} ⇒ 够格保留")
    if net < -float(noise_floor) - _TOL:
        return REVERT, (f"扣掉安慰剂 {placebo_delta:+.4f} 后净变化 {net:+.4f} "
                        f"< -噪声 {-float(noise_floor):.4f} ⇒ 明确变差")
    return NO_EVIDENCE, (f"净变化 {net:+.4f} 落在噪声 {noise_floor:.4f} 内 "
                         f"⇒ 没证据说更好（那就别改）")


def should_keep(verdict: str) -> bool:
    """唯一保留判据。⛔ 别在别处再写一遍 `== KEEP`：那是两个未来。"""
    return verdict == KEEP
