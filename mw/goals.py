"""自主目标提议器 —— L5 的**可判定**形态。

## 这个模块解决什么问题

在这之前，系统的每一个目标都是人给的（`--goal`，或题目集里的一道题）。
「自主目标」这句话在旧记录里不可证伪，所以一直记 0。

本模块把它换成四件可机检的事：

    propose()  —— 从**自己的账本**里挑出该做什么（零模型、零网络、纯函数）
    evaluate() —— 自己判「做成了没有」（三值之外的二值：MET / NOT_MET）
    record()   —— 把判定与读数写进目标账本（下一轮据此**不再重复提**）
    load()     —— 下一轮读回来

## ⛔ 三条边界（写在这里，是为了让后来的人一眼看见「它没做什么」）

1. **目标类型表是人写的**：`COVER` / `PROBE` / `RAISE` 三类由本文件定义。
   本模块不假装发明了新的目标类别 —— 它做的是「挑哪个格子、判成没成、判完换不换」。
   把这条写成边界而不是卖点，是因为「自主」这个词最容易在这里被偷偷放大。
2. **不读题面、不读人的目标**：`propose()` 的入参只有账本行与套件 id，
   ⛔ 没有 `goal` / `prompt` 参数。这不是洁癖：一旦它能读到题面，
   「它自己提出来的」就可能只是「把题目复述了一遍」，而那种东西测不出自主性。
3. **观测写独立文件**：`state/goal_obs.jsonl`，⛔ 不写 `score_ledger.jsonl`。
   后者带 `caliber_sig` 闸（口径一变就拒收），往里塞不同口径的观测
   等于**伪造履历** —— 自我模型会据此高估自己。

## 为什么排序用词典序而不是加权分数

因为「值多少 / 花多少」的加权就是一个**预算预测模型**，而预测模型本身需要被验证；
把它塞进提议器，等于用一个没验证的东西去决定先做哪件事，然后拿结果当它的证据。
所以这里只用**已量到的读数**做词典序：先补空白（0 次观测），再补不足（<3 次），
最后才是分数缺口（<1.0）。同类内部按读数升序 —— 缺口大的在前。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

# ⚠️ 依赖方向（PREREG_v175 §1.3）：goals → scoreinfo 是**消费侧**（拿二次读数做
#    诊断 overlay 与修复轴映射）；⛔ 反向不许 —— scoreinfo 不认 goals（AZ136g 原样）。
from . import scoreinfo as _SI

#: 判「这一格测得够不够」的最小观测次数。⛔ 不许调：它是判据的一部分，
#: 改了就等于换尺子（纪律 46：分母不同不许比大小）。
MIN_OBS = 3

#: 分数目标。取 1.0 是因为尺子的满分就是 1.0 —— 不设「进步 δ」，
#: 那会引入一个需要被单独验证的阈值。
TARGET = 1.0

#: 目标陈述里**允许**出现的字段：只有 id 与数字。
#: ⛔ 不允许拼接题面文本（见边界 2）。
KINDS = ("COVER", "PROBE", "RAISE")

# ⛔ 这里**没有** `ESCALATE_AFTER` 之类的第四个常量。
#    「试够 k 次」与「测够 3 次」用的是同一个数（`MIN_OBS`），
#    特意不做成独立参数：多一个可调阈值，就多一把需要单独验证的尺子
#    （V171 判据 Q6 就是钉这一条：模块级常量集合不许变）。


@dataclass(frozen=True)
class Goal:
    """一条目标。**不可变** —— 内容一变就是另一条目标（`gid` 随之变）。"""

    kind: str
    cell: str                      # 题 id（账本与套件的交集）
    statement: str
    criterion: tuple[tuple[str, str, float], ...]   # ((metric, op, value), ...)
    provenance: tuple[tuple[str, str, str], ...]    # ((check, key, rendered_value), ...)
    gid: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"未知目标类型: {self.kind!r}")
        if not self.gid:
            object.__setattr__(self, "gid", _gid(self.kind, self.cell, self.criterion))

    def as_dict(self) -> dict:
        return {
            "gid": self.gid,
            "kind": self.kind,
            "cell": self.cell,
            "statement": self.statement,
            "criterion": [[m, o, v] for m, o, v in self.criterion],
            "provenance": [[c, k, v] for c, k, v in self.provenance],
        }


def _gid(kind: str, cell: str, criterion: Sequence[tuple[str, str, float]]) -> str:
    """内容寻址：同内容同 id，改一个判据数值就是另一条目标。

    ⚠️ 为什么不用自增编号：自增编号会让「上一条目标做完了没有」
    与「这一条是不是上一条」变成两个问题。内容寻址下它们是同一个问题。
    """
    blob = json.dumps({"kind": kind, "cell": cell,
                       "criterion": [[m, o, float(v)] for m, o, v in criterion]},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ===========================================================================
# 观测：账本行 + 目标观测行，统一成同一种东西
# ===========================================================================
def coerce(row: dict) -> dict | None:
    """把一行（分数账本或目标观测）归一成 `{task_id, score}`，坏行返回 None。

    ⛔ 坏行**不静默丢弃**：由调用方数出来（`n_bad`），否则「0 次观测」
    与「有 3 行但都读不出来」会变成同一个读数。

    ## V181（§249）：`impl_sha` / `vec` **透传**

    归一是为了「判据只看见它认识的东西」，但它一度把 V175/V178 刚长出来的
    两个字段一起扔掉了 —— 账本行有 `impl_sha`+`vec`，`--goals` 的 obs 里却是 0，
    于是 `movability()` 全弃权、`beh_pool_from_rows()` 池恒空。**第三道闸，
    而且只有真账本 + 真 `--goals` 才看得见**（直接喂原始行的一切探针都绕过它）。

    透传规则（fail-open，⛔ 不在归一化层二次判）：
    - `impl_sha` 只在是**非空 str** 时透传（空串=缺席，不许冒充实现 "None"）；
    - `vec` 只在是 **list/tuple** 时透传（原样，不转 bool、不查长度、不查自洽）——
      形状坏的行由 `scoreinfo.beh_pool_from_rows()` 的行闸**计数**排除。
    """
    if not isinstance(row, dict):
        return None
    tid = str(row.get("task_id") or "").strip()
    if not tid:
        return None
    try:
        score = float(row.get("score"))
    except (TypeError, ValueError):
        return None
    out = {"task_id": tid, "score": score,
           "session": row.get("session"), "source": str(row.get("source") or "score_ledger")}
    _impl = row.get("impl_sha")
    if isinstance(_impl, str) and _impl:
        out["impl_sha"] = _impl
    _vec = row.get("vec")
    if isinstance(_vec, (list, tuple)):
        out["vec"] = list(_vec)
    return out


def coerce_all(rows: Iterable[dict]) -> tuple[list[dict], int]:
    out: list[dict] = []
    bad = 0
    for r in rows:
        c = coerce(r)
        if c is None:
            bad += 1
        else:
            out.append(c)
    return out, bad


def _mean(xs: Sequence[float]) -> float:
    """朴素左折叠 —— ⛔ 不用内置 `sum()`。

    理由与 AZ130a 同一条（V169 实测）：`sum()` 从 CPython 3.12 起对 float 走
    补偿求和，3.11 上就是朴素累加 ⇒ 同一个读数在两个版本上会差 1 ulp。
    目标判据要跨版本可比，所以把算法写死在这里。
    """
    t = 0.0
    for x in xs:
        t += x
    return t / len(xs)


def stats(obs: Sequence[dict], cell: str) -> dict:
    xs = [o["score"] for o in obs if o["task_id"] == cell]
    return {"n_obs": len(xs), "mean_score": (_mean(xs) if xs else 0.0), "scores": xs}


# ===========================================================================
# 提议
# ===========================================================================
def propose(
    obs: Sequence[dict],
    suite_ids: Sequence[str] = (),
    *,
    min_obs: int = MIN_OBS,
    target: float = TARGET,
    done: Sequence[str] = (),
    escalated: Sequence[str] = (),
) -> list[Goal]:
    """从读数里挑出目标。纯函数：同样的入参永远同样的输出。

    `suite_ids` 是**仓库里有哪些题**（不是「人要求做哪些题」）——
    两者的区别就是本模块存在的理由：前者是环境的边界，后者是人的指令。

    `done` 是**已经 MET 的 gid**。⛔ 不用「按 cell 去掉」：同一格可以有两代目标
    （补空白 → 补观测次数 → 提分数），前一代成了不该把后一代也掐掉。

    `escalated` 是**已判定「测量解决不了」的格**（`escalations()` 的输出）。
    这些格不再提 `RAISE` —— 但 ⛔ 不等于「这格被忘了」：
    调用方必须把它们单独渲染出来（许撤不许藏）。
    """
    done_set = set(done)
    esc_set = set(escalated)
    per_cell = {c: stats(obs, c) for c in {o["task_id"] for o in obs}}
    goals: list[Goal] = []

    for c in sorted(set(suite_ids)):
        st = per_cell.get(c) or {"n_obs": 0, "mean_score": 0.0}
        if st["n_obs"] == 0:
            goals.append(Goal(
                kind="COVER", cell=c,
                statement=f"补齐从未测过的题 {c}：至少测 1 次",
                criterion=(("n_obs", ">=", 1.0),),
                provenance=(("rows", c, "0"),),
            ))
        elif st["n_obs"] < min_obs:
            goals.append(Goal(
                kind="PROBE", cell=c,
                statement=f"把 {c} 的观测从 {st['n_obs']} 次提到 {min_obs} 次",
                criterion=(("n_obs", ">=", float(min_obs)),),
                provenance=(("rows", c, str(st["n_obs"])),),
            ))
        elif st["mean_score"] < target:
            if c in esc_set:
                # 这一格的 RAISE 判据（均分 >= 1.0）是**测量作用下的不动点**：
                # 同分布的再测只让均分更稳，不会让它上去。再提就是无限重复。
                continue
            goals.append(Goal(
                kind="RAISE", cell=c,
                statement=f"把 {c} 的均分从 {st['mean_score']:.4f} 提到 {target:g}",
                criterion=(("mean_score", ">=", float(target)),),
                provenance=(("mean", c, f"{st['mean_score']:.6f}"),),
            ))

    goals = [g for g in goals if g.gid not in done_set]
    # 词典序：先空白、再不足、最后分数缺口；同类内按缺口大小（读数升序）。
    order = {k: i for i, k in enumerate(KINDS)}
    goals.sort(key=lambda g: (order[g.kind], _gap(g, per_cell), g.cell))
    return goals


def _gap(g: Goal, per_cell: dict) -> float:
    st = per_cell.get(g.cell) or {"n_obs": 0, "mean_score": 0.0}
    if g.kind == "COVER":
        return 0.0
    if g.kind == "PROBE":
        return float(st["n_obs"])
    return float(st["mean_score"])


# ===========================================================================
# 判定
# ===========================================================================
def evaluate(goal: Goal, obs: Sequence[dict]) -> tuple[str, dict]:
    """判 MET / NOT_MET。**没有第三个值** —— 判不了说明判据写错了，不是数据的问题。

    ⛔ 不许出现 UNKNOWN：目标判据只引用账本里必然存在的量（出现次数、均分），
    所以「判不了」只可能是装置 bug。返回 UNKNOWN 会把装置 bug 伪装成数据缺口。
    """
    st = stats(obs, goal.cell)
    reading = {"cell": goal.cell, "n_obs": st["n_obs"],
               "mean_score": st["mean_score"], "criterion": [[m, o, v] for m, o, v in goal.criterion]}
    ok = True
    for metric, op, want in goal.criterion:
        got = st.get(metric)
        if got is None:
            raise KeyError(f"判据引用了不存在的量: {metric!r}（这是装置 bug，不是数据问题）")
        if op == ">=":
            ok = ok and (got >= want)
        elif op == "<=":
            ok = ok and (got <= want)
        else:
            raise ValueError(f"未知比较符: {op!r}")
    return ("MET" if ok else "NOT_MET"), reading


def verify_provenance(goal: Goal, obs: Sequence[dict]) -> list[str]:
    """自证 `provenance` 不是编的：按 `check` 重算，与写下的值逐字比。

    返回**不一致的条目**（空列表 = 全部对得上）。这是 P2 的判据实现 ——
    没有它，`provenance` 就只是一段随手写的注释。
    """
    bad: list[str] = []
    st = stats(obs, goal.cell)
    for check, key, want in goal.provenance:
        if check == "rows":
            got = str(st["n_obs"])
        elif check == "mean":
            got = f"{st['mean_score']:.6f}"
        else:
            bad.append(f"{check}:{key} 未知来源")
            continue
        if got != want:
            bad.append(f"{check}:{key} 写={want} 实测={got}")
    return bad


# ===========================================================================
# 账本
# ===========================================================================
def load(path: Path | str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"__bad__": line[:40]})
    return rows


def record(path: Path | str, goal: Goal, verdict: str, reading: dict, *,
           source: str = "") -> dict:
    """追加一条判定。**只追加，不覆盖** —— 判定的历史是它自己的证据。"""
    if verdict not in ("MET", "NOT_MET"):
        raise ValueError(f"verdict 只能是 MET/NOT_MET，收到 {verdict!r}")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {"gid": goal.gid, "kind": goal.kind, "cell": goal.cell,
           "verdict": verdict, "reading": reading,
           "criterion": [[m, o, v] for m, o, v in goal.criterion],
           "source": source}
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row


def done_gids(rows: Iterable[dict]) -> list[str]:
    """已经判成 MET 的 gid（下一轮不再提）。NOT_MET 的**要留着**重新提 ——
    这是「做不成的事会被反复提起」的那个正确行为。"""
    out: list[str] = []
    for r in rows:
        if isinstance(r, dict) and r.get("verdict") == "MET" and r.get("gid"):
            out.append(str(r["gid"]))
    return out


# ===========================================================================
# 「做不到」：把测量已经用尽这件事写进账本
# ===========================================================================
def unreachable(cell: str, obs: Sequence[dict], *,
                target: float = TARGET, min_obs: int = MIN_OBS) -> dict | None:
    """这一格的 `RAISE` 判据（均分 >= target）**按定义**达不到吗 —— 给证据或 `None`。

    ## 定理（V173 STAGE0，穷举 364 组 + 解析式）

    `stats()` 的均分是**全部历史**的均分，而观测**只追加、不替换**。
    分数上限就是 `target`，所以：

        均分 >= target  ⟺  每个分数都 >= target

    而 `propose()` 只在均分 `< target` 时才提 `RAISE`。两句合起来：

        **一条 `RAISE` 目标被提出时，它的判据已经不可能满足了。**

    追加 `k` 个满分之后：`均分 = (Σ_old + k·target)/(n + k)`，
    因 `Σ_old < n·target` ⇒ 恒 `< target`（`k → ∞` 时只**趋近**，永不等于）。
    换句话说：**过去的失败留在分母里，永远洗不掉。**

    ## ⛔ 这不是「模型不够强」

    前者要靠更好的模型；这一条是**判据不可满足**，换模型也没用
    （V173 STAGE0：`t06_rle` 离线连跑 8 轮，均分恒 0.0，5 条 `RAISE` 全是 `NOT_MET`）。
    两者的区别是本轮的全部内容 —— 把它写成「做不到」，不许写成「还差一点」。

    ## ⚠️ fail-open（宁可多提一条，也不许拿坏读数断言做不到）

    - `n_obs < min_obs` ⇒ `None`（还没到提 `RAISE` 的门槛，`PROBE` 还在跑）
    - 出现任何 `score > target` ⇒ `None`（尺子坏了/口径混了 ⇒ **弃权**）
    - 全部 `>= target` ⇒ `None`（已达成，不是做不到）
    """
    st = stats(obs, cell)
    if st["n_obs"] < min_obs:
        return None
    if any(s > target for s in st["scores"]):
        return None
    below = [s for s in st["scores"] if s < target]
    if not below:
        return None
    return {"reason": "CRITERION_UNREACHABLE",
            "cell": cell, "kind": "RAISE",
            "n_fail": 0, "means": [], "gids": [],
            "n_obs": st["n_obs"], "mean": st["mean_score"],
            "n_below": len(below), "min": min(below),
            "ceiling": (f"均分 = (Σ_old + Σ_new)/(n+k) < {target:g} 恒成立"
                        f"（Σ_old < n·{target:g}，因有 {len(below)} 个分数 < {target:g}）")}


def escalations(
    log_rows: Iterable[dict],
    obs: Sequence[dict] = (),
    *,
    after: int = MIN_OBS,
) -> dict[str, dict]:
    """判出「这一格的测量路径已经用尽」的格 —— 返回 `{cell: 证据}`。

    ## 为什么需要它

    `RAISE` 的判据是「均分 >= 1.0」。而**同分布的再测只让均分更稳，不会让它上去**
    （V171 STAGE0c 实测：`t06_rle` 连跑 5 轮，均分逐位相同 0.0，同一 gid 每轮都被重提）。
    所以 `RAISE` 是这个系统里**唯一一类「提得出、闭不了」的目标**：
    `COVER`/`PROBE` 的判据是出现次数，测量自己就能满足；`RAISE` 的判据是均值，
    测量**不改变**它。

    ## 触发条件（**两条路，任一条成立就转**）

    **A. 结构不可达**（V173，`reason=CRITERION_UNREACHABLE`）：
    有 `>= after` 次观测、且历史里存在 `< TARGET` 的分数 ⇒ 见 `unreachable()` 的定理。
    一轮读数就够：这不需要「试」，需要的是**看懂判据**。

    **B. 读数是测不动的**（V171，`reason=STUCK_MEANS`）：三条同时，缺一不转

    1. 同一 `gid` 被判 `NOT_MET` **≥ after** 次（试够了）
    2. 这 after 次的 `mean_score` **逐位相同**（读数没动）
       ⛔ 少了这条，`0.2 → 0.4 → 0.6` 这种明明在收敛的会被误判成做不到
    3. **当前仍未达标**（`fail-open`：分真的上去了，这条判定自动消失）

    ## ⛔ 它不是 `Goal`

    因为「需换能力」这件事没有可执行的判据 —— 写成 `Goal` 只能给空判据，
    而空判据恒真（`AZ132d` 那条撤回态锚的就是这个坏形态）。
    所以它是**账本的结论**，不是 `propose()` 的返回值。

    ## ⛔ 它不提高任何分数

    只是**停止重复**。这是「知道自己做不到」，不是「做到」。
    """
    by_gid: dict[str, list[dict]] = {}
    for r in log_rows:
        if not isinstance(r, dict) or not r.get("gid"):
            continue
        if r.get("verdict") not in ("MET", "NOT_MET"):
            continue
        by_gid.setdefault(str(r["gid"]), []).append(r)

    out: dict[str, dict] = {}
    # ⭐ V173：**结构**不可达 —— 一轮读数就够，而且给出为什么。
    #    ⛔ 它不是「试够了」的替代品：V171 那条只在读数**逐位相同**时触发，
    #    抓的是「模型确定性地重复」；这一条抓的是「判据本身闭不上」。
    #    两条共存 ⇒ 前者留给「读数在动但没到 1.0」的格（0.2→0.4→0.6 不许转，AZ133a）。
    for cell in sorted({str(o.get("task_id") or "") for o in obs}):
        if not cell:
            continue
        u = unreachable(cell, obs, min_obs=after)
        if u:
            out[cell] = u
    for gid, rows in by_gid.items():
        # ⚠️ 「**连**判 k 次 NOT_MET」的「连」是字面意思：从**最后一行往前数**，
        #    遇到 MET 就停。首版写成「取最近 k 次 NOT_MET」，
        #    于是 `NOT_MET, MET, NOT_MET, NOT_MET`（末尾只连 2 次）被判成「试了 3 次」——
        #    那是在数「失败总次数」，不是在数「连续失败」。V171 自踩第 2 条。
        tail: list[dict] = []
        for r in reversed(rows):
            if r.get("verdict") != "NOT_MET":
                break
            tail.append(r)
        tail.reverse()
        if len(tail) < after:
            continue
        tail = tail[-after:]
        means: list[float] = []
        for r in tail:
            m = (r.get("reading") or {}).get("mean_score")
            if m is None:
                break
            means.append(float(m))
        if len(means) != after:
            continue
        if len({repr(m) for m in means}) != 1:     # 读数在动 ⇒ 不转（这条是判据的牙）
            continue
        cell = str(tail[-1].get("cell") or "")
        kind = str(tail[-1].get("kind") or "")
        if not cell or kind != "RAISE":
            continue
        if obs:                                    # fail-open：已经达标就不该挂在这
            st = stats(obs, cell)
            if st["n_obs"] and st["mean_score"] >= TARGET:
                continue
        cur = out.get(cell)
        if cur is None:
            out[cell] = {"cell": cell, "kind": kind, "n_fail": len(tail),
                         "mean": means[-1], "means": list(means),
                         "gids": [gid], "reason": "STUCK_MEANS"}
        else:                                      # 同一格有两代 RAISE ⇒ 合并证据
            cur["n_fail"] += len(tail)
            cur["means"].extend(means)
            cur["gids"].append(gid)

    # ── 可动性 overlay（V175，PREREG_v175 §1.3）：**诊断字段，⛔ 不进 gate** ──
    # 覆盖定理（PREREG §0.2）：claim 能触发的格必已被上面的结构判据收进 esc
    # ⇒ 它改变不了「哪些格转运维」，只回答「该修哪条轴」。
    # 无池/缺身份/弃权 ⇒ None（fail-open：宁可不判，不许拿坏读数断言「尺子盲」）。
    for c, e in out.items():
        m = _SI.movability(obs, cell=c) if obs else None
        e["movability"] = (m if m and m.get("label") != _SI.KIND_UNKNOWN else None)
        # ── V180：行为级读数必须**到达**轴 ──
        # V179 实测：overlay 只由 `movability()` 产生，其 claim 值域里没有
        # `BEHAVIOR_UNRESOLVED` ⇒ `repair_axes` 里那条分支在部署上不可达。
        # 这里把行为级读数接进 overlay（`beh` 子字段），让「存在异行为同分」
        # 第一次有路走到轴。
        # ⛔ fail-open：无 vec / 弃权 ⇒ 不写 `beh`（⛔ 不许写 INJECTIVE 冒充
        #    「有分辨力」——缺席不是零，也不是好）。
        if e["movability"] is not None and obs:
            pool, _cnt = _SI.beh_pool_from_rows(obs, cell=c)
            b = _SI.behavioral(pool) if pool else None
            if b and b.get("kind") != _SI.KIND_UNKNOWN:
                e["movability"]["beh"] = {"kind": b["kind"],
                                          "n_behaviors": b.get("n_behaviors"),
                                          "claim": b.get("claim")}
    return out


# ===========================================================================
# 状态：「没有目标」有两种，⛔ 必须分开报
# ===========================================================================
def split_escalations(esc: dict[str, dict],
                      suite_ids: Sequence[str]) -> tuple[dict[str, dict], dict[str, dict]]:
    """把「已转运维」按**套件**切成 `(套件内, 套件外)`。

    为什么必须切（V173 P6 FAIL 暴露的真 bug）：账本里有套件之外的题，
    结构判据会把它们也判进来，而它们**不在提议范围**。`state()` 见到 `esc` 非空
    就报 `IDLE` ⇒ 套件里每一格都达标也会被报成「测量已用尽」
    （实测：套件 1 格全 1.0 + 账本外 1 格在 esc ⇒ `IDLE`，过滤后 ⇒ `DONE`）。

    ⛔ 两半都要返回：套件外的那半**不许静默丢掉**（许撤不许藏），
       调用方得把它单独打出来。
    """
    ids = set(suite_ids)
    inside = {c: e for c, e in esc.items() if c in ids}
    outside = {c: e for c, e in esc.items() if c not in ids}
    return inside, outside


def satisfied(obs: Sequence[dict], cell: str, *,
              min_obs: int = MIN_OBS, target: float = TARGET) -> bool:
    """这一格「该做的都做完了」吗 —— 按**读数**算，不按「提不出目标」算。

    ⛔ 两者的区别是本轮的全部内容：`propose()` 返回 0 条**不代表**做完了，
    它也可能是「全转运维」或「套件里根本没有题」。拿前者当后者，
    就是把「无事可做」报成「做完了」。
    """
    st = stats(obs, cell)
    return st["n_obs"] >= min_obs and st["mean_score"] >= target


def state(
    goals: Sequence[Goal],
    esc: dict[str, dict],
    suite_ids: Sequence[str],
    obs: Sequence[dict],
    *,
    min_obs: int = MIN_OBS,
    target: float = TARGET,
) -> tuple[str, dict]:
    """判「系统现在处于哪一种停/不停」—— 返回 `(名字, 证据)`。

    ## 五个名字（⛔ 不做成常量表：见下）

    | 名字 | 条件 | 它说的是 |
    |---|---|---|
    | `WORK`  | 提议 > 0 | 有活可干 |
    | `EMPTY` | 提议 0、**套件 0 题** | 无从提议（装置/口径问题） |
    | `IDLE`  | 提议 0、**转运维 > 0** | 测量已用尽，提不出目标了 |
    | `DONE`  | 提议 0、转运维 0、**每格读数都达标** | 真的做完了 |
    | `STALE` | 提议 0、转运维 0、**有格没达标** | 目标被已 MET 的 gid 挡住了 |

    ## 为什么顺序是 EMPTY → IDLE → DONE

    `EMPTY` 优先于 `IDLE`：套件为空时「转运维 > 0」也能成立（历史账本里的格
    不在套件里），此时说「测量已用尽」是拿一个读数冒充另一个
    （纪律 8：缺席冒充零）。**先报最具体的那条事实。**

    ## 为什么 `DONE` 要重算而不是看「提议 0 条」

    因为 `done_gids()` 是**单调**的：某格 `RAISE` 判过 MET 之后，若后来的观测把均分
    拉回阈值以下，`propose()` 会因为 gid 在 `done` 里而**不再提它** ⇒ 提议 0 条，
    但读数明明没达标。拿提议数当「做完」的判据，正好在这一格上谎报。
    所以 `DONE` 由 `satisfied()` 逐格算，`STALE` 是它的反证。

    ## ⛔ 不新增模块级常量

    五个名字刻意不写成 `STATES = (...)`：V171 的 Q6 钉的是「模块级常量集合不变」，
    而那份判据只有名字集合可查、分不出「阈值」与「名字表」。与其去改那份旧判据
    （⛔ 不许给旧记录补字段），不如让名字只活在**返回值与文档**里 ——
    值域由契约锚 `AZ134a` 钉（五个构造输入 ⇒ 五个不同的名字）。
    """
    ids = sorted(set(suite_ids))
    det: dict = {
        "n_goals": len(goals),
        "n_esc": len(esc),
        "n_cells": len(ids),
        "n_sat": sum(1 for c in ids if satisfied(obs, c, min_obs=min_obs, target=target)),
        "n_obs": len(obs),
        "escalated": sorted(esc),
        "blocked": [],
    }
    if goals:
        return "WORK", det
    if not ids:
        return "EMPTY", det
    if esc:
        return "IDLE", det
    if det["n_sat"] == len(ids):
        return "DONE", det
    # 走到这里：提议 0、转运维 0、却还有格没达标 ⇒ 一定是被 `done` 挡住了。
    # 把它**点名**而不是吞掉：这正是「提议 0 条 = 做完了」会谎报的那一格。
    # ⚠️ `propose()` 唯一会丢目标的地方就是 `done` 过滤，所以这里的差集
    #    必然等于「被 done 挡住的」；不许写成「大概是被挡」。
    done_set = {g.gid for g in goals}
    det["blocked"] = [{"cell": g.cell, "kind": g.kind, "gid": g.gid,
                       "statement": g.statement}
                      for g in propose(obs, ids, escalated=list(esc))
                      if g.gid not in done_set]
    return "STALE", det


def render_state(name: str, det: dict) -> str:
    """把状态渲染成一行 —— **读数在前，解释在后**（纪律 10）。"""
    if name == "WORK":
        return f"WORK（有 {det['n_goals']} 条可执行的目标）"
    if name == "IDLE":
        return (f"IDLE（提议 0 条，{det['n_esc']} 格已转运维：这 {det['n_esc']} 格"
                f"最后 {MIN_OBS} 次被判 NOT_MET 且均分逐位相同 ⇒ 测量没让它动）"
                f"　⛔ 不是做完了")
    if name == "DONE":
        return (f"DONE（套件 {det['n_cells']} 格全部达标：观测 ≥{MIN_OBS} 次"
                f"且均分 ≥{TARGET:g}）")
    if name == "EMPTY":
        return (f"EMPTY（套件里 0 题；账本 {det['n_obs']} 行对不上任何题）"
                f"　⛔ 不是做完了")
    if name == "STALE":
        cells = "、".join(b["cell"] for b in det["blocked"]) or "（未点名）"
        return (f"STALE（提议 0 条、转运维 0 格，但只有 {det['n_sat']}/{det['n_cells']} 格达标；"
                f"{len(det['blocked'])} 格的目标被已 MET 的 gid 挡住：{cells}）"
                f"　⛔ 账本说做完了，读数说没有")
    return f"UNKNOWN_STATE({name})"


def repair_axes(reason: str, movability: dict | None = None) -> tuple[str, ...]:
    """诊断 → 修复动作指向（PREREG_v175 §1.3）。纯函数，值域封闭 3 个名字。

    | 输入 | 轴 | 修复动作指向 |
    |---|---|---|
    | reason=CRITERION_UNREACHABLE | CRITERION | 改**判据**（目标定义/聚合） |
    | reason=STUCK_MEANS | MODEL | 改**模型/方法**（换实现路径） |
    | claim=SCORE_UNMOVABLE（叠加） | RULER | 改**尺子**（打分轴/分辨力） |
    | claim=BEHAVIOR_UNRESOLVED（叠加） | RULER | 同上（V177 行为面：加/换测试、改聚合） |
    | beh.kind=COARSE ∧ 行为类≥3（V180 叠加） | RULER | 同上（**存在异行为同分**） |

    ⛔ 这是**指向**不是处方：它说「该动哪一层」，不说「怎么改」。
    ⛔ 不叠加的情形：overlay 为 None / label 弃权 / 无 claim ⇒ 不给 RULER 轴
    （宁可不指，不许拿「没测出分辨力」冒充「尺子坏了」—— 后者是 V174 的
    RULER_BROKEN，形制不同）。

    ## V180：行为级 COARSE 也说话（`beh` 是 overlay 的子字段）

    V179 量出：`movability()` 的 `claim` 值域只有 `{SCORE_UNMOVABLE, None}`，
    而 `repair_axes` 的产品调用点只有一个、实参就是这个 overlay ⇒
    `BEHAVIOR_UNRESOLVED` 那条分支在**部署路径上不可达**。本轮把行为级读数
    接进 overlay（`beh`），并补一条以**行为**为单位的轴触发：

        行为级 COARSE ∧ n_behaviors ≥ MIN_BEHAVIORS ⇒ RULER

    ⛔ **不用分数级 COARSE**：真池 6/6 都是分数级 COARSE，但其中 5 格行为级
    INJECTIVE（多个实现**行为相同**、只是写法不同）⇒ 拿它当「尺子坏」的判据，
    假正例 5/6（`PREREG_v180 §2` A）。分辨力是相对于**行为**说的，不是相对于实现。
    """
    axes: list[str] = []
    if reason == "CRITERION_UNREACHABLE":
        axes.append("CRITERION")
    elif reason == "STUCK_MEANS":
        axes.append("MODEL")
    if movability and movability.get("claim") in (_SI.CLAIM_UNMOVABLE,
                                                  _SI.CLAIM_BEHAVIOR_UNRESOLVED):
        axes.append("RULER")
    beh = (movability or {}).get("beh") or {}
    if (beh.get("kind") == _SI.KIND_COARSE
            and int(beh.get("n_behaviors") or 0) >= _SI.MIN_BEHAVIORS
            and "RULER" not in axes):
        axes.append("RULER")
    return tuple(axes)


def render_escalations(esc: dict[str, dict]) -> str:
    """把「已转运维」渲染成一段 —— ⛔ 不许静默丢弃（许撤不许藏）。

    ⚠️ 措辞只许写**读数**：「3 次均分逐位相同」是事实；「再测也不会变」是外推，
    而这条外推正是本判定要停止重复的**理由**，不是它的**证据**。两者不许混写
    （纪律 10：数值对得上 ≠ 解释对）。

    V173 起有**两种理由**，⛔ 不许合成一句：
    - `CRITERION_UNREACHABLE`：判据按定义闭不上（过去的失败留在分母里）
    - `STUCK_MEANS`：连判若干次、均分逐位相同（读数是**测**不动，不是**证**不动）

    V175 起每格附**修复轴**（`repair_axes()`）与可动性 overlay 读数 ——
    ⛔ overlay 是诊断字段：⛔ 不改变哪些格出现在这里（覆盖定理，PREREG §0.2）。
    """
    if not esc:
        return "  （没有：没有任何一格的测量路径被用尽）"
    lines = []
    for cell in sorted(esc):
        e = esc[cell]
        if e.get("reason") == "CRITERION_UNREACHABLE":
            line = (f"  {cell:<24} 判据按定义不可满足：历史 {e['n_obs']} 次里 "
                    f"{e['n_below']} 次 <{TARGET:g}（最低 {e['min']:.6f}），"
                    f"现均分 {e['mean']:.6f} ⇒ {e['ceiling']}")
            if e.get("n_fail"):
                line += f"；另：连判 {e['n_fail']} 次均分逐位相同"
            lines.append(line)
        else:
            lines.append(f"  {cell:<24} RAISE 判 NOT_MET {e['n_fail']} 次，"
                         f"这 {len(e['means'])} 次的均分逐位相同 {e['mean']:.6f}"
                         f" ⇒ 测量没让它动；继续提同一条是重复")
        m = e.get("movability")
        axes = repair_axes(str(e.get("reason") or ""), m)
        gloss = {"CRITERION": "CRITERION（改判据：目标定义/聚合）",
                 "MODEL": "MODEL（改模型/方法：换实现路径）",
                 "RULER": "RULER（改尺子：打分轴/分辨力）"}
        axis_txt = "+".join(gloss[a] for a in axes) if axes else "（无）"
        if m:
            line = (f"{'':<26} 修复轴：{axis_txt}｜可动性：{m['label']}"
                    f"（{m['n_impls']} 实现 {m['n_scores']} 档"
                    f"｜bits={m['bits']:.4f}｜无身份行 {m['n_no_impl']}）")
            b = m.get("beh")
            if b:                        # V180：行为级读数到达这里才算「接了线」
                line += (f"｜行为级：{b['kind']}（{b['n_behaviors']} 行为类）")
                if b.get("kind") == _SI.KIND_COARSE:
                    line += " ⇐ 存在异行为同分"
            lines.append(line)
        else:
            lines.append(f"{'':<26} 修复轴：{axis_txt}｜可动性：无（缺身份/弃权 ⇒ 不判）")
    return "\n".join(lines)



def scan_suites(dirpath: Path | str) -> dict[str, list[str]]:
    """扫仓库里有哪些题：`{套件名: [题 id]}`。坏行**显式报错**，不静默跳过。

    ⛔ 只读 `id` 字段。题面 `prompt` 一个字都不读 —— 见模块头边界 2。
    """
    out: dict[str, list[str]] = {}
    for p in sorted(Path(dirpath).glob("*.jsonl")):
        ids: list[str] = []
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{p.name}:{i} 不是合法 JSON: {e}") from e
            tid = str(d.get("id") or "").strip()
            if not tid:
                raise ValueError(f"{p.name}:{i} 缺 id")
            ids.append(tid)
        if ids:
            out[p.stem] = ids
    return out


def render(goals: Sequence[Goal], obs: Sequence[dict], limit: int = 12) -> str:
    if not goals:
        # ⛔ 这里**不解释**为什么 0 条。旧文案写的是「账本与套件都对不上，
        #    或该做的都做完了」—— 把三种成因（空套件 / 转运维 / 真做完）
        #    合成一句，于是「无事可做」永远会被读成「做完了」。
        #    原因由 `state()` 单独报（V172）。
        return "（0 条 —— 原因见「状态」行；⛔ 不在这里猜）"
    lines = [f"  {'#':<2} {'类':<5} {'格子':<24} {'判据':<18} {'现状':<18} 目标"]
    for i, g in enumerate(goals[:limit], 1):
        verdict, rd = evaluate(g, obs)
        crit = " ".join(f"{m}{o}{v:g}" for m, o, v in g.criterion)
        now = f"n={rd['n_obs']} mean={rd['mean_score']:.4f}"
        lines.append(f"  {i:<2} {g.kind:<5} {g.cell[:24]:<24} {crit:<18} {now:<18} "
                     f"{g.statement}  [{verdict}]")
    if len(goals) > limit:
        lines.append(f"  … 另有 {len(goals) - limit} 条")
    return "\n".join(lines)
