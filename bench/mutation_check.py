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

## 两道上限闸（第七十七轮补）

前四个前提管「这一跑有没有资格说话」，这两道管「就算说了，算不算数」。

**交叠闸**（`cand_runs` + `control_runs`）：处理臂的**每一次**运行都要严格好过
对照组的**每一次**。为什么要它 —— 第七十六轮实测：驱动器自己那 12 次运行的
轮间极差**恰好 0.0000**，于是「噪声下限」= 0，`net > 0` 就够判 `KEEP`。
纯函数实测：`base 47/84, cand 48/84, 噪声 0` ⇒ **KEEP**。
**1 格运气被读成「变强」**，而 `KEEP` 一旦出现，就等于"系统自己认定自己变强了"。
交叠闸不需要任何常数：它问的是「两个分布分不分得开」。

**量级闸**（`min_effect`）：低于这个量级的差不算「变强」。
⛔ 它是**政策**不是读数，所以必须由调用方显式传入并写清依据；
并且要用**分数**（`格数 / 总格数`）算，**不许**用 `0.0357` 这种 4 位小数字面量
—— 那个值比 `3/84 = 0.035714` 小 1.4e-5，而容差若小到 `1e-9` 就够不着它，
于是「恰好等于」会被判成「大于」。容差现在按**读数量化精度**定成 `1e-3`
（依据见 `_TOL` 上方那段实测），这个洞因此关掉。

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
#: 容差不能只够挡浮点残差（1e-17），还必须盖住**读数的量化误差**。
#:
#: 第七十八轮实测：`report.per_task[].score` 存的是 **4 位小数**（`1/14` 存成 `0.0714`），
#: 逐题平均之后台账里的 `mean` 是 `0.55951666…`，而真值 `47/84 = 0.55952380…`
#: —— 相差 **7.1e-6**。噪声下限由这些 mean 相减得到，于是 `3/84` 被算成 `0.03571667`。
#:
#: ⇒ 拿 `1e-9` 去判「净增益是否**恰好等于**噪声」，边界就由最后几位小数决定：
#:    实测 `cand = 50/84`（+3 格）时 `net - 噪声 = +4.8e-6` ⇒ 被判 `KEEP`；
#:    而按政策它必须是 `NO_EVIDENCE`（净增益要**严格大于**噪声，见 §39.1）。
#:
#: 取 `1e-3`：量化误差算到「差」这一层 ≤ `1e-4`（每格 4 位小数 ÷ 题数），
#: `1e-3` 是它的 **10 倍**；一格是 `1/84 = 1.19e-2`，`1e-3` 是它的 **1/12**
#: —— 既盖得住舍入，又不吃掉任何真实的格差。
_TOL = 1e-3


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


def separation(cand_runs: Sequence[float],
               control_runs: Sequence[float]) -> tuple[str, float, float, float, float]:
    """两个分布分不分得开。返回 `(方向, cand最差, cand最好, 对照最差, 对照最好)`。

    方向：`"up"` / `"dn"` / `"overlap"`（含相等 —— **相等是交叠，不是分开**）。
    """
    lo_c, hi_c = min(cand_runs), max(cand_runs)
    lo_k, hi_k = min(control_runs), max(control_runs)
    up = lo_c > hi_k + _TOL
    dn = hi_c < lo_k - _TOL
    return ("up" if up else "dn" if dn else "overlap"), lo_c, hi_c, lo_k, hi_k


def mutation_verdict(*, knob: str, diff: Sequence[str],
                     base_mean: float, cand_mean: float,
                     placebo_delta: float | None,
                     noise_floor: float | None,
                     cand_runs: Sequence[float] | None = None,
                     control_runs: Sequence[float] | None = None,
                     min_effect: float | None = None) -> tuple[str, str]:
    """这次变异该不该保留。返回 (verdict, why)。

    `placebo_delta` = 等长安慰剂臂的 Δ（同样两臂、同样的题，只是把旋钮
    改成一个**语义惰性**的值）。用它扣掉"这一跑本来就会有的漂移"。

    `cand_runs` / `control_runs` = 处理臂与对照组（base ∪ 安慰剂）**每次运行**
    各自的均值。给了就开交叠闸；`control_runs` 把安慰剂也算进对照组，
    这是有意的：安慰剂跑到多高，处理臂就必须**每一次**都超过它。
    不给则退回旧的单点口径（只为兼容历史调用，⛔ 正式跑必须给）。

    `min_effect` = 最小效应量（分数单位，`格数 / 总格数`）。给了就开量级闸。
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

    if cand_runs and control_runs:
        way, lo_c, hi_c, lo_k, hi_k = separation(cand_runs, control_runs)
        if way == "overlap":
            return NO_EVIDENCE, (
                f"交叠：cand 最差 {lo_c:.4f} 没有严格好过对照组最好 {hi_k:.4f}"
                f"（cand {lo_c:.4f}~{hi_c:.4f} ｜ 对照 {lo_k:.4f}~{hi_k:.4f}）"
                f" ⇒ 两个分布分不开，涨的那点落在抖动里")
        if min_effect is not None:
            mag = net if way == "up" else -net
            if mag < float(min_effect) - _TOL:
                return NO_EVIDENCE, (
                    f"净{'增益' if way == 'up' else '变化'} {net:+.4f} 未达最小效应量 "
                    f"{float(min_effect):.4f}"
                    f" ⇒ 分得开，但够不上「{('变强' if way == 'up' else '变差')}」")

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
