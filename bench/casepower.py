#!/usr/bin/env python3
"""刻度：一套用例能分开多少种**错误的实现**（第七十四轮）。

## 这一层要回答的问题

「一条用例 = 1/N 分」是**条数**的说法：N 变大，分数看起来更细。
但真正决定刻度的是另一件事：**两个不同的错误实现会不会拿到同一个分数**。
若会，那么「某个变异让分数动了」这件事，在这两者之间根本不可观测 ——
它不是「信号小」，是「尺子上没有这道刻度」。

§36.4 曾据此写下处方：「下一步该**加密用例**（7 → 21 条/题）」。
本模块就是去量那句话：**21 是拍板数，不是需求数**。

## 尺子怎么造（不许手写「错误答案」）

变异族由 spec **派生**，spec 变了族跟着变：

    · 单点参数改动（`_OPS[op]['alts']`）—— 「题面写了这个参数，代码没读它」
    · 丢一步、相邻换序                   —— 「漏做一步 / 顺序搞乱」
    · 常识实现（`naive=True`）           —— 「按最常见的做法写」

每个实现 `e` 在用例集 `C` 上的**通过向量** = 逐条 (与 oracle 相同? )。
向量相同 ⇒ C 给它们同一个分 ⇒ **C 分不开它们**，与这条用例写得多聪明无关。

## 天花板

把通过向量算在一个**确定性随机的输入空间**上（400 条）：
那时可分种类的总数，就是这套用例**最多**能表达多少种错误。
`species(C) == ceiling()` ⇒ 饱和：再加用例**不再增加区分力**，
只把 1/N 的分母做大（对阶跃分布的分数反而是钝化）。

## 实现要点（先算一次，之后只做投影）

`Power` 在构造时把每个 (题, 变异体) 在**整个用例宇宙**上的通过向量算一遍；
之后任何用例子集只需要按位置投影 —— 所以贪心能对几百条候选反复比较。
⛔ 不这么做就只能每换一条用例重跑一遍全宇宙，慢到不可用（实测被 SIGTERM 掉）。

## 边界（别读大）

· 天花板由「宇宙」与变异族共同决定，不是「无穷多」。**它是上界，也是有限的界。**
· 它只说「能分开多少种**这种形态**的错误」。真实模型的错误形态不在这个族里 ——
  这一层量不到，⛔ 不许把 100% 覆盖读成「这题不会被答错」。
"""

from __future__ import annotations

import random
from typing import Any, Mapping, Sequence

import taskspec as T

Case = list[str]
Mutants = dict[str, str]

#: 天花板的定义空间：确定性随机输入（同 seed ⇒ 逐字相同）。
SPACE_N = 400
SPACE_SEED = 99
_WORDS = ("alpha", "Beta", "GAMMA", "delta", "x", "y", "ze ta",
          "much longer token here", "a b  c", "TAB\there", "  pad  ",
          "dup", "DUP", "same line", "l" * 20)


def rand_cases(n: int, seed: int = SPACE_SEED) -> list[Case]:
    rng = random.Random(seed)
    out: list[Case] = []
    for _ in range(n):
        case: Case = []
        for _ in range(rng.randint(0, 6)):
            w = rng.choice(_WORDS)
            if rng.random() < 0.3:
                w = w + " " + rng.choice(_WORDS)
            if rng.random() < 0.2:
                w = w.upper()
            if rng.random() < 0.2:
                w = " " * rng.randint(1, 2) + w + " " * rng.randint(1, 2)
            case.append(w)
        out.append(case)
    return out


SPACE: list[Case] = rand_cases(SPACE_N)


def mutants(spec: Mapping[str, Any]) -> Mutants:
    """spec 派生的变异实现族。⛔ 不手写错误答案：手写的那一份会是第二套判据。"""
    out: Mutants = {"naive": T.compile_oracle(spec, naive=True)}
    rules = spec["rules"]
    for i, rule in enumerate(rules):
        for j, alt in enumerate(T._OPS[rule["op"]](rule)["alts"]):
            rs = list(rules)
            rs[i] = alt
            out[f"alt{i}.{j}:{rule['op']}"] = T.compile_oracle(dict(spec, rules=rs))
        rs = [r for k, r in enumerate(rules) if k != i]
        if rs:
            out[f"drop{i}:{rule['op']}"] = T.compile_oracle(dict(spec, rules=rs))
        if i + 1 < len(rules):
            rs = list(rules)
            rs[i], rs[i + 1] = rs[i + 1], rs[i]
            out[f"swap{i}:{rule['op']}+{rules[i + 1]['op']}"] = \
                T.compile_oracle(dict(spec, rules=rs))
    return out


class Power:
    """用例宇宙 + 变异体通过向量。构造一次，之后投影查表。

    ⛔ `space` 必须与用例集**独立**：天花板是「这套尺子最多能表达多少种错误」，
       如果把待测用例也算进定义空间，加一条用例天花板就涨一点 —— 永远饱和不了，
       那个"天花板"就变成了自证。这是本轮刻意分开的两个参数。
    """

    def __init__(self, specs: Sequence[Mapping[str, Any]], universe: Sequence[Case],
                 space: Sequence[Case] | None = None):
        self.specs = list(specs)
        self.universe: list[tuple[str, ...]] = [tuple(c) for c in universe]
        self.space: list[tuple[str, ...]] = [tuple(c) for c in (space if space is not None
                                                               else universe)]
        self.pos: dict[tuple[str, ...], int] = {}
        for i, c in enumerate(self.universe):
            self.pos.setdefault(c, i)
        # 逐题：活变异体的通过向量（长度 = 宇宙长度）
        self.live: dict[str, dict[str, tuple[bool, ...]]] = {}
        for spec in self.specs:
            sid = str(spec["id"])
            ov = _vec(spec, T.compile_oracle(spec), universe)
            live = {}
            for name, src in mutants(spec).items():
                v = _vec(spec, src, universe)
                if v != ov:               # 与 oracle 完全同分 ⇒ 不算「错误实现」
                    live[name] = v
            self.live[sid] = live
        self._memo: dict[tuple[str, ...], tuple[int, dict[str, int]]] = {}

    # ── 查表 ────────────────────────────────────────────────────────────
    def _index(self, cases: Sequence[Case]) -> list[int]:
        idx = []
        for c in cases:
            k = tuple(c)
            if k not in self.pos:
                raise KeyError(f"用例不在宇宙里：{list(c)!r}（⛔ 不许临时加戏）")
            idx.append(self.pos[k])
        return idx

    def species(self, cases: Sequence[Case]) -> tuple[int, dict[str, int]]:
        """这套用例能分辨的「错误种类」数（越大越细）。"""
        key = tuple(tuple(c) for c in cases)
        if key in self._memo:
            return self._memo[key]
        idx = self._index(cases)
        per: dict[str, int] = {}
        for sid, live in self.live.items():
            profiles = {tuple(v[i] for i in idx) for v in live.values()}
            per[sid] = len(profiles)
        got = (sum(per.values()), per)
        self._memo[key] = got
        return got

    def ceiling(self) -> int:
        """定义空间上的种类数 —— 这套用例**最多**能表达多少种错误。"""
        return self.species([list(c) for c in self.space])[0]

    # ── 曲线 ────────────────────────────────────────────────────────────
    def saturation(self, cases: Sequence[Case], extra: Sequence[Case] = (), *,
                   steps: Sequence[int] = (0, 1, 2, 3, 6, 9, 14)) -> dict[str, Any]:
        """加用例的曲线 + 饱和点。`saturated_at` = 首次到天花板的用例总数。"""
        cap = self.ceiling()
        curve = []
        for k in steps:
            kk = min(k, len(extra))
            curve.append((len(cases) + kk,
                          self.species(list(cases) + list(extra[:kk]))[0]))
        hit = next((n for n, sp in curve if sp >= cap), None)
        return {"ceiling": cap, "curve": curve, "saturated_at": hit, "base": len(cases)}

    def greedy(self, base: Sequence[Case], pool: Sequence[Case], k: int) -> list[dict[str, Any]]:
        """每步选「新增种类数最多」的一条；并列取池中靠前的。

        ⛔ 目标是**涨种类**，不是「涨通过条数」。
        """
        chosen: list[Case] = []
        trail: list[dict[str, Any]] = []
        for _ in range(k):
            cur = list(base) + chosen
            base_sp = self.species(cur)[0]
            best_i, best_g = -1, -1
            for i, cand in enumerate(pool):
                g = self.species(cur + [list(cand)])[0] - base_sp
                if g > best_g:
                    best_i, best_g = i, g
            if best_i < 0 or best_g <= 0:
                break
            chosen.append(list(pool[best_i]))
            trail.append({"case": list(pool[best_i]), "new_species": best_g,
                          "total_cases": len(base) + len(chosen),
                          "species": base_sp + best_g})
        return trail


def _vec(spec: Mapping[str, Any], src: str, cases: Sequence[Case]) -> tuple[bool, ...]:
    """通过向量：逐条 (该实现与 oracle 相同? )。⛔ 输出本身不进来 —— 只看对错。"""
    fn = spec["fn"]
    want = [T.apply_pipeline(spec, list(c)) for c in cases]
    v: list[bool] = []
    for c, w in zip(cases, want):
        try:
            v.append(tuple(T.run_source(src, fn, list(c))) == tuple(w))
        except Exception:                                       # noqa: BLE001
            v.append(False)
    return tuple(v)


# ===========================================================================
# 检测器自带反证：四条，就地算、不写盘
# ===========================================================================


def reverse_proofs(pw: Power, base: Sequence[Case],
                   extra: Sequence[Case]) -> dict[str, Any]:
    """证明这把尺子**不是**「条数计数器」。

    ① 复制一条现有用例 ⇒ 条数 +1，种类数**不变**
    ② 把追加的用例再抄一遍（凑条数）⇒ 种类数**不变**
    ③ 追加的那几条确实有效 ⇒ 去掉它们种类数**严格下降**
    ④ 天花板是有限上界 ⇒ 种类数不会超过它

    参数是 `base`（原有那几条）与 `extra`（追加的那几条），当前用例集 = base + extra。
    ⚠️ 第一版把「当前全集」当 base 传进来，于是 ③ 退化成 `full < full`（恒假）——
       实测当场红。这类"参数含义错、断言还长得像对的"是最难查的一种。
    """
    cases = list(base) + list(extra)
    bare = pw.species(base)[0]
    full = pw.species(cases)[0]
    cap = pw.ceiling()
    dup1 = pw.species(list(cases) + [list(cases[0])])[0]
    dupall = pw.species(list(cases) + [list(c) for c in extra])[0]
    return {
        "bare": bare, "full": full, "ceiling": cap,
        "复制一条": dup1, "追加重抄一遍": dupall,
        "① 复制一条不涨": dup1 == full,
        "② 抄一遍不涨": dupall == full,
        "③ 去掉追加就掉": bare < full,
        "④ 没有超过天花板": full <= cap,
    }
