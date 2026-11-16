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

#: 判「这一格测得够不够」的最小观测次数。⛔ 不许调：它是判据的一部分，
#: 改了就等于换尺子（纪律 46：分母不同不许比大小）。
MIN_OBS = 3

#: 分数目标。取 1.0 是因为尺子的满分就是 1.0 —— 不设「进步 δ」，
#: 那会引入一个需要被单独验证的阈值。
TARGET = 1.0

#: 目标陈述里**允许**出现的字段：只有 id 与数字。
#: ⛔ 不允许拼接题面文本（见边界 2）。
KINDS = ("COVER", "PROBE", "RAISE")


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
    return {"task_id": tid, "score": score,
            "session": row.get("session"), "source": str(row.get("source") or "score_ledger")}


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
) -> list[Goal]:
    """从读数里挑出目标。纯函数：同样的入参永远同样的输出。

    `suite_ids` 是**仓库里有哪些题**（不是「人要求做哪些题」）——
    两者的区别就是本模块存在的理由：前者是环境的边界，后者是人的指令。

    `done` 是**已经 MET 的 gid**。⛔ 不用「按 cell 去掉」：同一格可以有两代目标
    （补空白 → 补观测次数 → 提分数），前一代成了不该把后一代也掐掉。
    """
    done_set = set(done)
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
        return "（没有可提的目标：账本与套件都对不上，或该做的都做完了）"
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
