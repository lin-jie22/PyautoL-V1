#!/usr/bin/env python3
"""分数信息量：「这题的分数是单射还是常数」—— 逐题可量的二次读数。

预注册：`bench/evidence/l3_s262/PREREG_v174.md`（§1 定义是判据的唯一来源）。
判据实现效果由 `bench/probe_scoreinfo.py` 量，契约形状由 `AZ136` 钉。

## 这一层要回答的问题

§169.5 的桶化表说明「分数有多少信息」**逐题不同**：有的题分数是单射（看得见实现），
有的题是常数（永远同一个分）。但那张表是人读的 —— 系统自己读不了，
L3 的自省通道就接不上（「这个读数值多少」没有答案）。

本模块把「读桶化表」做成**纯函数**：给一格的 `(实现, 分数)` 池，
返回 `CONSTANT` / `INJECTIVE` / `COARSE` / `UNKNOWN` 之一，附证据。

## ⛔ 这不是新打分器

它不判任何答案的对错、不改任何一格分数 —— 只对**既有分数**做二次读数。
§169.7 的排除项仍然有效（换题面不是异源判据）：**这是给分数称重，不是新秤。**

## 弃权三条（fail-open，宁可不判不许错判）

1. `TOO_FEW` —— 去重实现数 < MIN_POOL
2. `RULER_BROKEN` —— 出现 > TARGET / < 0 / 非数值的分（尺子坏了 ⇒ 弃权）
3. `IMPL_SCORE_CONFLICT` —— 同一实现多个分（确定性判分下只能是口径混了）

与 V173 `unreachable()` 的三条闸同构：⛔ 任何一条都不许崩、不许静默降级成 CONSTANT。
"""

from __future__ import annotations

import hashlib
import math
from typing import Iterable, Sequence

#: 一格里至少要有几个**去重实现**才够判（§1：2 是 §169.5 里 g7e2 形态的下限）。
MIN_POOL = 2
#: 分数上限（与 `mw/goals.TARGET` 同值同义；⛔ 不 import goals —— 两模块解耦，
#: 值不一致由 AZ136g 对撞钉住）。
TARGET = 1.0

#: kind 值域 —— ⛔ 不做成模块级可变集合；值域由 AZ136f 钉（恰 4 个名字）。
KIND_CONSTANT = "CONSTANT"
KIND_INJECTIVE = "INJECTIVE"
KIND_COARSE = "COARSE"
KIND_UNKNOWN = "UNKNOWN"

#: 弃权理由值域（同上，AZ136f 钉）。
REASON_TOO_FEW = "TOO_FEW"
REASON_RULER_BROKEN = "RULER_BROKEN"
REASON_IMPL_SCORE_CONFLICT = "IMPL_SCORE_CONFLICT"


def _h2(n: int) -> float:
    return math.log2(n) if n > 0 else 0.0


def _entropy_bits(bucket_sizes: Sequence[int]) -> float:
    """桶分布的香农熵（bit）。均匀时 == log2(桶数)。"""
    total = sum(bucket_sizes)
    if total <= 0:
        return 0.0
    out = 0.0
    for c in bucket_sizes:
        if c > 0:
            p = c / total
            out -= p * math.log2(p)
    return out


def classify(pool: Iterable[tuple[str, float]], *,
             min_pool: int = MIN_POOL,
             target: float = TARGET) -> dict:
    """给一格的 `(impl_id, score)` 池做二次读数。

    返回（字段固定，缺字段即 bug，AZ136f 钉值域与自洽）：

        kind        CONSTANT | INJECTIVE | COARSE | UNKNOWN
        reason      UNKNOWN 时给弃权理由；其余为 None
        n_impls     去重实现数（UNKNOWN 闸在去重前触发时仍尽力报）
        n_scores    不同分数数
        resolution  n_scores / n_impls ∈ (0, 1]（UNKNOWN 时 None）
        bits        实得信息（桶分布香农熵，UNKNOWN 时 None）
        bits_max    信息上界 log2(n_scores)（UNKNOWN 时 None）
        buckets     {分数: [impl_id, ...]} 逐桶点名（UNKNOWN 时 {}）

    ⛔ 纯函数：同样输入 ⇒ 逐字相同输出（P6 钉）。
    ⛔ 判定顺序：弃权闸 → CONSTANT → INJECTIVE → COARSE（穷尽且互斥）。
    """
    rows = list(pool)

    # ---- 弃权闸 2：尺子坏了（先于去重，坏分不许被去重悄悄吃掉） ----
    scores_raw: list[float] = []
    for impl, s in rows:
        if isinstance(s, bool) or not isinstance(s, (int, float)):
            return _unknown(REASON_RULER_BROKEN, rows, detail=f"非数值分数 {impl}={s!r}")
        sf = float(s)
        if math.isnan(sf) or math.isinf(sf):
            return _unknown(REASON_RULER_BROKEN, rows, detail=f"非有限分数 {impl}={s!r}")
        if sf > target or sf < 0.0:
            return _unknown(REASON_RULER_BROKEN, rows,
                            detail=f"分数越界 {impl}={sf}（target={target}）")
        scores_raw.append(sf)

    # ---- 弃权闸 3：同一实现多个分 ----
    by_impl: dict[str, set[float]] = {}
    for (impl, _s), sf in zip(rows, scores_raw):
        by_impl.setdefault(str(impl), set()).add(sf)
    conflict = {i: sorted(s) for i, s in by_impl.items() if len(s) > 1}
    if conflict:
        first = next(iter(conflict.items()))
        return _unknown(REASON_IMPL_SCORE_CONFLICT, rows,
                        detail=f"{first[0]} 有 {len(first[1])} 个分 {first[1]}")

    # ---- 去重实现（每实现一格一票，取其唯一分数） ----
    impl_score = {i: next(iter(s)) for i, s in by_impl.items()}
    n = len(impl_score)

    # ---- 弃权闸 1：样本太少 ----
    if n < min_pool:
        return _unknown(REASON_TOO_FEW, rows, detail=f"n={n} < MIN_POOL={min_pool}")

    # ---- 分桶 ----
    buckets: dict[float, list[str]] = {}
    for i, s in sorted(impl_score.items()):
        buckets.setdefault(s, []).append(i)
    k = len(buckets)
    sizes = [len(v) for v in buckets.values()]

    bits = _entropy_bits(sizes)
    bits_max = _h2(k)
    resolution = k / n

    if k == 1:
        kind = KIND_CONSTANT
        # ⛔ 常数的信息就是 0，不许熵算出 -0.0 或 0.0 以外的数（自洽，P7）
        bits = 0.0
    elif k == n:
        kind = KIND_INJECTIVE
    else:
        kind = KIND_COARSE

    return {"kind": kind, "reason": None, "n_impls": n, "n_scores": k,
            "resolution": resolution, "bits": bits, "bits_max": bits_max,
            "buckets": {f"{s:g}": v for s, v in sorted(buckets.items())}}


def _unknown(reason: str, rows: Sequence[tuple[str, float]], *, detail: str) -> dict:
    """弃权：⛔ 不给分类、不给 bits —— 弃权就是弃权，不许半个读数冒充结论。"""
    return {"kind": KIND_UNKNOWN, "reason": reason, "n_impls": None,
            "n_scores": None, "resolution": None, "bits": None, "bits_max": None,
            "buckets": {}, "detail": detail}


def classify_pool_rows(rows: Iterable[dict], *,
                       impl_key: str = "impl", score_key: str = "score",
                       min_pool: int = MIN_POOL,
                       target: float = TARGET) -> dict:
    """dict 行的便利入口（探针/调用方各自适配自己的行形态，核心只认 (impl, score)）。"""
    return classify(((str(r[impl_key]), r[score_key]) for r in rows),
                    min_pool=min_pool, target=target)


# ===========================================================================
# 可动性（V175，§243 / PREREG_v175 §1）—— 「读数动没动过」的二次读数
# ===========================================================================

#: claim 的实现数下限（PREREG_v175 §1.1）。2 个同分是巧合的常见形态，
#: 3 个才够说「池上没分辨过」；且 3 与 `mw/goals.MIN_OBS` 同值同义（观测下限）。
#: ⛔ 覆盖定理（PREREG §0.2）依赖这个数 ≥ 3：取 2 会留一个 gate 窗口（n_obs=2）。
MIN_IMPLS = 3

#: 可动性判负的 claim 名字（⛔ 不进 `reason` 值域 —— 那是 goals 的地盘）。
CLAIM_UNMOVABLE = "SCORE_UNMOVABLE"


def impl_sha_of(text) -> str:
    """答案文本的实现指纹（`sha256[:16]`）。同文同值、异文异值；纯函数。

    为什么是答案文本的哈希而不是自由文本标签：产品账本的 `candidate` 字段是
    自由文本（「内核/http qwen2.5:7b…」），且 T=0 下 session 是同一实现的
    重复测量 —— 两者都当不了实现身份（PREREG_v175 §0.1）。
    """
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


def pool_from_rows(rows: Iterable[dict], *, cell: str | None = None,
                   impl_key: str = "impl_sha", score_key: str = "score",
                   cell_key: str = "task_id") -> tuple[list[tuple[str, float]], int]:
    """从观测行抽 `(impl_sha, score)` 池。返回 `(pool, n_no_impl)`。

    - `cell` 给定 ⇒ 只取该格的行。
    - **缺 `impl_sha`（或值为空）的行计数后排除** —— ⛔ 不许当成实现 "None"
      混进池：那样「没记身份」会被读成「一个叫 None 的实现」，池的 n 直接失真。
    - 坏行（非 dict / 缺分数键）同样计数进 `n_no_impl`（可归因，不静默）。
    """
    pool: list[tuple[str, float]] = []
    n_no = 0
    for r in rows:
        if not isinstance(r, dict):
            # 无从归属的坏行：只进**全局**计数（cell=None），⛔ 不计进任何格
            if cell is None:
                n_no += 1
            continue
        if cell is not None and r.get(cell_key) != cell:
            continue
        impl = r.get(impl_key)
        if not impl or score_key not in r:
            n_no += 1
            continue
        pool.append((str(impl), r[score_key]))
    return pool, n_no


#: vec–score 自洽闸容差（V178，PREREG_v178 §0.3/§1.5）：发射量子 1e-4 的
#: 半宽（`round(x, 4)` 最大偏差恰 5e-5）+ 1e-12 浮点余量。精确发射器同过；
#: 翻转一个向量元素的偏差（≥ 1/n_tests）远超此值。
VEC_SCORE_TOL = 5e-5 + 1e-12


def beh_pool_from_rows(rows: Iterable[dict], *, cell: str | None = None,
                       impl_key: str = "impl_sha", score_key: str = "score",
                       vec_key: str = "vec",
                       cell_key: str = "task_id") -> tuple[list[tuple], dict]:
    """从账本行抽 `(impl_sha, vec, score)` 池（V178，PREREG_v178 §1.5）。

    供 `behavioral()` 的账本喂入路径。逐行闸（顺序固定，一行只进一个桶）：

    1. 非 dict ⇒ `n_bad_row`（仅 `cell=None` 全局口径计 —— 逐格无从归属不计）；
    2. `cell` 给定且不匹配 ⇒ 跳过不计；
    3. 缺 `impl_sha`/缺 `score` 键 ⇒ `n_no_impl`（与 `pool_from_rows` 同义）；
    4. `vec` 键缺席或 None ⇒ `n_no_vec`；
    5. vec 非 list/tuple / 元素非全 bool / 空 ⇒ `n_bad_vec`；
    6. 分数非数值/bool/NaN/inf，或 `|score − mean(vec)| > VEC_SCORE_TOL`
       ⇒ `n_inconsistent`（分数坏到没法对账也算对不上账 —— 细分归因属
       `behavioral()` 的 RULER_BROKEN，本适配器只保证「过闸的行可信」）；
    7. 过闸 ⇒ 入池，`n_kept`。

    **fail-open**：任何坏行只计数排除，绝不抛。纯函数。
    ⛔ 不校验 `len(vec) == 测试条数`（行里没有测试条数；长度闸只在传输面）。
    ⛔ per-row 自洽闸**不放进** `behavioral()` —— 冲突闸的「同行为异分」
    夹具恰恰含行内不自洽行（PREREG_v178 §0.5）；本适配器管账本行
    （vec 与 score 是同一测量的两种渲染），分类器管跨源裸观测。
    """
    pool: list[tuple] = []
    counts = {"n_bad_row": 0, "n_no_impl": 0, "n_no_vec": 0,
              "n_bad_vec": 0, "n_inconsistent": 0, "n_kept": 0}
    for r in rows:
        if not isinstance(r, dict):
            # 无从归属的坏行：只进**全局**计数（cell=None），⛔ 不计进任何格
            if cell is None:
                counts["n_bad_row"] += 1
            continue
        if cell is not None and r.get(cell_key) != cell:
            continue
        impl = r.get(impl_key)
        if not impl or score_key not in r:
            counts["n_no_impl"] += 1
            continue
        raw_vec = r.get(vec_key)
        if raw_vec is None:
            counts["n_no_vec"] += 1
            continue
        if (not isinstance(raw_vec, (list, tuple)) or not raw_vec
                or any(not isinstance(b, bool) for b in raw_vec)):
            counts["n_bad_vec"] += 1
            continue
        sf = r.get(score_key)
        # 分数坏到没法对账也算对不上账：bool/非数值/NaN/inf 一律进
        # n_inconsistent（⚠️ NaN 的比较恒 False，必须先排再比差）。
        if isinstance(sf, bool):
            counts["n_inconsistent"] += 1
            continue
        try:
            sf = float(sf)
        except (TypeError, ValueError):
            counts["n_inconsistent"] += 1
            continue
        if not math.isfinite(sf):
            counts["n_inconsistent"] += 1
            continue
        vec = tuple(bool(b) for b in raw_vec)
        mean = sum(vec) / len(vec)
        if abs(sf - mean) > VEC_SCORE_TOL:
            counts["n_inconsistent"] += 1
            continue
        pool.append((str(impl), vec, sf))
        counts["n_kept"] += 1
    return pool, counts


def movability(rows: Iterable[dict], *, cell: str | None = None,
               min_impls: int = MIN_IMPLS, target: float = TARGET) -> dict:
    """一格的「读数可动性」二次读数。输出字段固定：

        label       CONSTANT | INJECTIVE | COARSE | UNKNOWN（`classify()` 的 kind）
        reason      label=UNKNOWN 时给弃权理由；其余 None
        n_impls / n_scores / bits   同 `classify()`
        n_rows      供池的行数（含被排除的）
        n_no_impl   因缺身份/坏行被排除的行数（可归因）
        claim       "SCORE_UNMOVABLE" | None —— 当且仅当
                    label==CONSTANT ∧ n_impls ≥ min_impls ∧ 桶值 < target

    ⛔ claim 说的是「**已试**实现池上分数从未分辨过任何一个实现」，
    **不是**「不可能改进」—— 新实现拉开分差时 label 翻、claim 自动消失
    （每轮从账本重算，不做持久化状态位）。
    """
    # cell 给定时只看该格的行（坏行无从归属 ⇒ 不计进任何格的 n_no_impl，
    # 全局口径 `cell=None` 才计它们 —— 两口径分开，不许混）。
    all_rows = [r for r in rows
                if cell is None or (isinstance(r, dict) and r.get("task_id") == cell)]
    pool, n_no = pool_from_rows(all_rows, cell=None, impl_key="impl_sha",
                               score_key="score")
    # cell 已在上面过滤过 ⇒ cell=None 传给 pool_from_rows，避免二次过滤丢计数
    j = classify(pool, min_pool=MIN_POOL, target=target)
    out = {"label": j["kind"], "reason": j.get("reason"),
           "n_impls": j.get("n_impls"), "n_scores": j.get("n_scores"),
           "bits": j.get("bits"),
           "n_rows": len(all_rows), "n_no_impl": n_no,
           "claim": None}
    if (j["kind"] == KIND_CONSTANT
            and isinstance(j.get("n_impls"), int) and j["n_impls"] >= min_impls):
        # 桶值（CONSTANT ⇒ 恰一桶）必须 < target 才是「追分追不动」；
        # 常年 1.0 的格是「做完了」，不是「做不到」。
        # ⛔ 桶值从池里取（`buckets` 的键是格式化串，不许当数值用）。
        if pool and float(pool[0][1]) < target:
            out["claim"] = CLAIM_UNMOVABLE
    return out


# ===========================================================================
# 行为级（V177，§245 / PREREG_v177 §1）—— 「同行为同分、异行为异分」的二次读数
# ===========================================================================

#: claim 的行为类数下限（PREREG_v177 §1.4）。2 个同权重向量撞分是巧合的常见形态
#: （均分尺子上太常见），3 个才够说「池上没分辨过」。
#: ⚠️ 量词单位是**行为类**（claim 说的是行为），故不沿用 MIN_IMPLS=3。
MIN_BEHAVIORS = 3

#: 行为级可动性判负的 claim 名字（V175 SCORE_UNMOVABLE 的行为面对应物）。
CLAIM_BEHAVIOR_UNRESOLVED = "BEHAVIOR_UNRESOLVED"

#: 行为级冲突闸的弃权理由（⛔ 与 V174 的 IMPL_SCORE_CONFLICT 不同义：
#: 那是「同实现多分」，这是「同行为多分 / 同实现多行为」）。
REASON_BEHAVIOR_SCORE_CONFLICT = "BEHAVIOR_SCORE_CONFLICT"


def _beh_vec_str(vec: tuple) -> str:
    """通过向量的展示形态：True→1（`"1010"`）。"""
    return "".join("1" if b else "0" for b in vec)


def _behavioral_unknown(reason: str, detail: str) -> dict:
    """行为级弃权：⛔ 不给分类、不给 bits、不给 claim —— 弃权就是弃权。"""
    return {"kind": KIND_UNKNOWN, "reason": reason, "n_impls": None,
            "n_behaviors": None, "n_scores": None, "resolution": None,
            "bits": None, "bits_max": None, "buckets": {}, "claim": None,
            "detail": detail}


def behavioral(rows: Iterable[tuple]) -> dict:
    """一池的「行为级分数信息量」二次读数。`rows` = `(impl, behavior, score)` 三元组。

    - `behavior` = **通过向量**：非空 tuple/list of **bool**（⛔ 不收 0/1 整数），
      全池等长；展示形态 `"1010"`。
    - 单位是**行为类**（向量取值相同的行归一类），不是实现 —— 与 `classify()`
      的「每实现一票」不同层（PREREG_v177 §1.1）。

    弃权闸顺序（PREREG_v177 §1.2）：尺子坏 → 冲突 → 样本少。过闸后按行为类分桶：

        k == 1       CONSTANT（bits 强制 0.0）
        k == n       INJECTIVE（同行为同分 ∧ 异行为异分）
        1 < k < n    COARSE（存在异行为同分）

    claim（§1.4）：`BEHAVIOR_UNRESOLVED` ⟺ CONSTANT ∧ n_behaviors ≥ MIN_BEHAVIORS
    ∧ 桶值 < TARGET。⛔ 说的是「已试行为类上分数从未分辨过任何两种行为」，
    **不是**「不可能改进」。

    输出字段固定（含 detail —— 无事为 None）：kind / reason / n_impls /
    n_behaviors / n_scores / resolution / bits / bits_max / buckets / claim / detail。
    ⛔ 纯函数：同样输入 ⇒ 逐字相同输出（AZ139h 钉）。
    """
    # ---- 归一 + 去重（完全相同的三元组只算一行） ----
    seen: set[tuple] = set()
    norm: list[tuple[str, tuple, float]] = []
    for idx, r in enumerate(rows, 1):
        try:
            impl, beh, s = r
        except (TypeError, ValueError):
            return _behavioral_unknown(REASON_RULER_BROKEN,
                                       f"第 {idx} 行不是三元组: {r!r}")
        impl_s = str(impl)
        # ---- 尺子坏闸：向量形状（先于任何分组，坏行不许被悄悄吃掉） ----
        if not isinstance(beh, (tuple, list)) or not beh:
            return _behavioral_unknown(REASON_RULER_BROKEN,
                                       f"第 {idx} 行向量非非空序列: {beh!r}")
        if not all(isinstance(b, bool) for b in beh):
            return _behavioral_unknown(REASON_RULER_BROKEN,
                                       f"第 {idx} 行向量含非 bool 元素: {beh!r}")
        vec = tuple(beh)
        # ---- 尺子坏闸：分数形状（V174 同款；bool 是 int 子类，必须先挡） ----
        if isinstance(s, bool) or not isinstance(s, (int, float)):
            return _behavioral_unknown(REASON_RULER_BROKEN,
                                       f"非数值分数 {impl_s}={s!r}")
        sf = float(s)
        if math.isnan(sf) or math.isinf(sf):
            return _behavioral_unknown(REASON_RULER_BROKEN,
                                       f"非有限分数 {impl_s}={s!r}")
        if sf > TARGET or sf < 0.0:
            return _behavioral_unknown(REASON_RULER_BROKEN,
                                       f"分数越界 {impl_s}={sf}（target={TARGET}）")
        key = (impl_s, vec, sf)
        if key in seen:
            continue
        seen.add(key)
        norm.append((impl_s, vec, sf))

    # ---- 尺子坏闸：全池向量等长（长度不齐 = 混了不同测试集） ----
    if norm:
        lens = {len(v) for _, v, _ in norm}
        if len(lens) > 1:
            show = sorted({(len(v), _beh_vec_str(v)) for _, v, _ in norm})
            return _behavioral_unknown(REASON_RULER_BROKEN,
                                       f"向量长度不齐: {show}")

    # ---- 冲突闸：同行为多分（违反同行为同分）→ 同实现多行为（口径混了） ----
    by_beh: dict[tuple, set[float]] = {}
    by_impl: dict[str, set[tuple]] = {}
    for impl_s, vec, sf in norm:
        by_beh.setdefault(vec, set()).add(sf)
        by_impl.setdefault(impl_s, set()).add(vec)
    for vec, ss in sorted(by_beh.items()):
        if len(ss) > 1:
            return _behavioral_unknown(
                REASON_BEHAVIOR_SCORE_CONFLICT,
                f"同行为 {_beh_vec_str(vec)} 有 {len(ss)} 个分 {sorted(ss)}")
    for impl_s, vs in sorted(by_impl.items()):
        if len(vs) > 1:
            return _behavioral_unknown(
                REASON_BEHAVIOR_SCORE_CONFLICT,
                f"同实现 {impl_s} 有 {len(vs)} 种行为 "
                f"{sorted(_beh_vec_str(v) for v in vs)}")

    # ---- 样本少闸：行为类 < 2 ----
    beh_score: dict[tuple, float] = {vec: next(iter(ss)) for vec, ss in by_beh.items()}
    n_impls = len(by_impl)
    n = len(beh_score)
    if n < 2:
        return _behavioral_unknown(REASON_TOO_FEW,
                                   f"n_behaviors={n} < 2")

    # ---- 按行为类分桶（单位是行为类，不是实现 —— 与 classify() 不同层） ----
    buckets: dict[float, list[str]] = {}
    for vec, s in sorted(beh_score.items(), key=lambda kv: (kv[1], kv[0])):
        buckets.setdefault(s, []).append(_beh_vec_str(vec))
    k = len(buckets)
    sizes = [len(v) for v in buckets.values()]

    bits = _entropy_bits(sizes)
    bits_max = _h2(k)
    resolution = k / n

    if k == 1:
        kind = KIND_CONSTANT
        bits = 0.0            # 常数信息恒 0（防 -0.0 进 JSON）
    elif k == n:
        kind = KIND_INJECTIVE
    else:
        kind = KIND_COARSE

    claim = None
    if (kind == KIND_CONSTANT and n >= MIN_BEHAVIORS
            and float(next(iter(beh_score.values()))) < TARGET):
        claim = CLAIM_BEHAVIOR_UNRESOLVED

    return {"kind": kind, "reason": None, "n_impls": n_impls,
            "n_behaviors": n, "n_scores": k,
            "resolution": resolution, "bits": bits, "bits_max": bits_max,
            "buckets": {f"{s:g}": v for s, v in sorted(buckets.items())},
            "claim": claim, "detail": None}
