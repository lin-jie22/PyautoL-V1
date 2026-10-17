"""闭环消融 —— 回答「这些模块是否必要」的**唯一**实测方法。

## 判据不是「人脑有所以要有」

那是从解剖学抄答案。可证伪的判据只有一个：

    **拆掉它，闭环的哪条边归零？**

于是模块分成两类，分类由读数决定，不由清单决定：

    · 相位（phase）：拆掉后闭环**走不完**（status ≠ done）或某条边 coverage=0
    · 调制层（modulator）：拆掉后闭环照转，但某个读数变差 —— 它不改吞吐，改上限

这个区分是可以被推翻的：如果某个"调制层"拆掉后 status 直接不 done，
那它就是相位，本脚本的报告会自己把它挪过去（见 `classify`）。

## 它同时是"单个模块强 ≠ 整体强"的测量

每个变体只关**一个**部件，其余全开。如果整体能力是模块能力的叠加，
那么关掉任意一个都只会掉一小块、且各变体之间差别不大。
实测若出现"某个变体整轮崩掉、另一个关掉后读数几乎不动"，
就说明**模块对整体的贡献极度不均** —— 那不叫叠加，那叫**依赖链**。
链上最弱的一环决定整体，而不是最强的那个。

用法：
    python3 bench/coherence.py            # 表格
    python3 bench/coherence.py --json     # 机器可读
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mw.core import Core                      # noqa: E402
from mw.guard import ALLOW as G_ALLOW         # noqa: E402
from mw.values import ALLOW as V_ALLOW        # noqa: E402
from mw.worldmodel import Prediction          # noqa: E402

_ENV = ("V1_STATE_DIR", "V1_LOG_DIR", "V1_WORKSPACE")


@contextlib.contextmanager
def isolated_state(tmp: Path):
    """每个变体一套独立的 state/logs/workspace。

    ⚠️ 必须隔离：`facts` 是**跨轮累积**的，共用一套目录会让第 7 个变体
    继承前 6 个变体的记忆 —— 那时量到的差异是"记忆多少"，不是"这个部件有没有用"。
    （V5 的对照实验反复栽在这上面。）
    """
    old = {k: os.environ.get(k) for k in _ENV}
    os.environ["V1_STATE_DIR"] = str(tmp / "state")
    os.environ["V1_LOG_DIR"] = str(tmp / "logs")
    os.environ["V1_WORKSPACE"] = str(tmp / "workspace")
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _silence(core: Core) -> None:
    """消融实验不需要把每一步打屏；读数从 stats 取。"""
    core.log = lambda msg: None


def _dry_shell(core: Core) -> None:
    """把所有 `run_shell` 换成**干跑**，在任何变体生效之前先装。

    ## 为什么这条是必须的（2026-10-17 实测踩到）

    DemoLLM 的第一个候选故意是 `rm -rf ~`（用来证明守卫真的在拦）。
    而 `no_guard` 变体关掉守卫后，这条命令是**真的会执行的** ——
    `mw/tools.py:151` 是 `subprocess.run(cmd, shell=True)`，没有任何兜底。

    第一次跑消融时主目录没被删，**靠的是宿主沙箱，不是我的代码**。
    这一点必须写下来：一个测量工具的危险性不许超过被测系统，
    否则"测出来的结论"和"没把机器搞坏"两件事里，你只能保住一件。
    """
    from mw.tools import ToolResult
    orig = core.tools.dispatch

    def safe(tool: str, args: dict) -> ToolResult:
        if tool == "run_shell":
            return ToolResult(ok=True, output="rc=0\n[消融实验：shell 未真正执行]")
        return orig(tool, args)

    core.tools.dispatch = safe        # type: ignore[method-assign]


# ---------------------------------------------------------------------------
def build_variants() -> list[tuple[str, str, str, object]]:
    """(key, 中文名, 类别声明, 打补丁的函数)。

    「类别声明」是**先验**（我认为它是相位还是调制层）。
    报告会把它和实测结果并排打印，两者不一致时以实测为准并标 `!`。
    先验与实测不一致，本身就是最有信息量的一行。
    """
    import mw.metacog as metacog

    def no_working(core: Core) -> None:
        core.memory.think = lambda text: None      # type: ignore[method-assign]

    def no_recall(core: Core) -> None:
        core.memory.recall = lambda q, k=6: []     # type: ignore[method-assign]

    def no_predict(core: Core) -> None:
        # 预演恒不可信 ⇒ 等同于"关掉 foresee 这一段"，但保险起见仍走同一代码路径
        core.wm.predict = lambda **kw: Prediction("", "", 0.0)  # type: ignore[method-assign]

    def no_learn(core: Core) -> None:
        core.wm.learn = lambda mm: None            # type: ignore[method-assign]

    def no_guidance(core: Core) -> None:
        core.wm.guidance = lambda **kw: ""         # type: ignore[method-assign]

    def no_values(core: Core) -> None:
        core.values.judge = lambda **kw: V_ALLOW   # type: ignore[method-assign]

    def no_selfmodel(core: Core) -> None:
        core.selfmodel.advise = lambda **kw: metacog.DEFAULT  # type: ignore[method-assign]

    def no_guard(core: Core) -> None:
        # ⚠️ 必须收位置参数：`check_cascade(consecutive_failures)` 是**位置**调用，
        #    第一版写成 `lambda **kw: G_ALLOW` ⇒ TypeError ⇒ 整轮 status=error。
        #    症状极像"守卫是相位"（拆了就崩），实际是**我的实验工具坏了**。
        #    这条记在这里，因为"看到可疑输出先怀疑工具"是本项目的老规矩。
        for name in ("check_shell", "check_goal", "check_cascade", "check_memory_write"):
            setattr(core.guard, name, lambda *a, **kw: G_ALLOW)

    def no_plan(core: Core) -> None:
        from mw.planner import Plan
        core.planner.propose = lambda **kw: Plan()      # type: ignore[method-assign]
        core.planner.replan = lambda **kw: Plan()       # type: ignore[method-assign]

    def no_act(core: Core) -> None:
        """行动恒失败 —— 等价于"感知拿不到任何真实观测"。"""
        from mw.tools import ToolResult
        core.tools.dispatch = lambda tool, args: ToolResult(           # type: ignore[method-assign]
            ok=False, output="", error="消融：行动被禁用")

    return [
        ("full",        "全开（基线）",        "—",        None),
        ("no_working",  "关掉工作记忆写入",    "phase",     no_working),
        ("no_recall",   "关掉语义检索",        "phase",     no_recall),
        ("no_plan",     "关掉规划（推理）",    "phase",     no_plan),
        ("no_act",      "关掉行动（感知断源）", "phase",    no_act),
        ("no_predict",  "关掉预演",            "modulator", no_predict),
        ("no_learn",    "关掉失配沉淀",        "modulator", no_learn),
        ("no_guidance", "关掉易错点提示",      "modulator", no_guidance),
        ("no_values",   "关掉价值层",          "modulator", no_values),
        ("no_selfmodel", "关掉自我模型",       "modulator", no_selfmodel),
        ("no_guard",    "关掉守卫",            "modulator", no_guard),
    ]


def run_one(patch) -> dict:
    from run import DemoLLM

    with tempfile.TemporaryDirectory() as td:
        with isolated_state(Path(td)):
            from mw import config as cfg
            s = cfg.settings()
            core = Core(s, llm=DemoLLM(), log=None)
            _silence(core)
            _dry_shell(core)          # 先装保险，再施加变体
            if patch is not None:
                patch(core)
            # 动作序列必须在**所有**补丁之后装在最外层：
            # 它记录的是"内核请求了什么"，与下层是否真的执行无关。
            # 没有这一列，`关掉守卫` 会和基线读数一模一样 —— 因为危险动作
            # 真的被执行并"成功完成"了任务。读数全绿，做的事完全不同。
            calls: list[str] = []
            inner = core.tools.dispatch
            core.tools.dispatch = lambda t, a: (calls.append(t), inner(t, a))[1]
            out = core.run(DemoLLM.GOAL)
            st = out.stats or {}
            flow = st.get("flow", {})
            wm = st.get("worldmodel", {})
            val = st.get("values", {})
            mem = st.get("memory", {})
            return {
                "status": out.status,
                "steps": out.iterations,
                "hit_rate": wm.get("hit_rate"),
                "scored": wm.get("scored", 0),
                "coverage": {k: v for k, v in (flow.get("coverage") or {}).items()},
                "reuse": flow.get("reuse_mean"),
                "facts": mem.get("facts", 0),
                "values_denied": val.get("denied", 0),
                "values_judged": val.get("judged", 0),
                "actions": list(calls),
            }


# ---------------------------------------------------------------------------
def classify(declared: str, row: dict, base_reuse: float | None) -> str:
    """实测分类。**以读数为准，不以我的声明为准。**

    三条结构信号，任一成立即「相位」（拆了闭环就不成环）：
        · 整轮没走到 `done`
        · 某条边的 coverage 为 `None`（从未产出）或 `0.0`（产出了没人读）
        · 回流率从**非零**掉到 0（回边断了）

    ⚠️ 第三条是实测补上的：第一版只查 coverage，于是 `关掉工作记忆写入`
    被判成 modulator —— 它的 coverage 没有归零，但**回流率从 1.00 掉到 0.00**。
    回边（行动结果回到下一回合感知）唯一的物理载体就是工作记忆，
    它断了，环就成了 `感知→记忆→推理→行动` 的一条直链。这不是"少了个模块"，
    是"少了一半的环"。
    """
    if declared == "—":
        return "—"
    cov = row["coverage"]
    dead = [k for k, v in cov.items() if v is None or v == 0.0]
    if row["status"] != "done" or dead:
        return "phase"
    if base_reuse and row["reuse"] == 0.0:
        return "phase"
    return "modulator"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = []
    for key, label, declared, patch in build_variants():
        r = run_one(patch)
        r["key"] = key
        r["label"] = label
        r["declared"] = declared
        rows.append(r)

    base = rows[0]
    for r in rows:
        r["actual"] = classify(r["declared"], r, base["reuse"])

    if args.json:
        print(json.dumps({"baseline": base, "variants": rows}, ensure_ascii=False, indent=2))
        return 0

    def fmt(v):
        return "n/a" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))

    print("=" * 108)
    print("闭环消融 —— 每个变体只关一个部件，其余全开（离线 DemoLLM，独立记忆目录）")
    print("=" * 108)
    print(f"{'变体':<18}{'声明':<10}{'实测':<10}{'status':<10}{'步':<4}"
          f"{'命中率':<8}{'回填':<8}{'facts':<7}{'通量(感知/记忆/推理)':<24}")
    print("-" * 108)
    for r in rows:
        cov = r["coverage"]
        cstr = "/".join(fmt(cov.get(k)) for k in
                        ("perceive>memory", "memory>reason", "reason>act"))
        mark = "" if r["declared"] in ("—", r["actual"]) else "  ⚠️实测与声明不一致"
        print(f"{r['label']:<18}{r['declared']:<10}{r['actual']:<10}{r['status']:<10}"
              f"{r['steps']:<4}{fmt(r['hit_rate']):<8}{fmt(r['reuse']):<8}"
              f"{r['facts']:<7}{cstr:<24}{mark}")

    print("-" * 108)
    print("与基线相比**唯一变化**的读数（Δ=0 的一律不列 —— 说明这个部件在这一轮里没被用到）：")
    any_delta = False
    for r in rows[1:]:
        d = []
        if r["actions"] != base["actions"]:
            d.append("动作: " + "→".join(base["actions"]) + " 变成 " + "→".join(r["actions"]))
        for f in ("status", "steps", "hit_rate", "reuse", "facts", "values_denied"):
            if r[f] != base[f]:
                d.append(f"{f}: {fmt(base[f])} → {fmt(r[f])}")
        for k, v in r["coverage"].items():
            if v != base["coverage"].get(k):
                d.append(f"{k}: {fmt(base['coverage'].get(k))} → {fmt(v)}")
        if d:
            any_delta = True
            print(f"  {r['label']}: " + " | ".join(d))
    if not any_delta:
        print("  （没有任何变体改变读数 —— 这本身就是异常，见下方说明）")

    # 空转自检：一个"关了也看不出来"的变体，说明实验没有功效，不代表部件无用。
    inert = [r["label"] for r in rows[1:]
             if r["status"] == base["status"] and r["coverage"] == base["coverage"]
             and r["hit_rate"] == base["hit_rate"] and r["facts"] == base["facts"]
             and r["actions"] == base["actions"] and r["steps"] == base["steps"]
             and r["values_denied"] == base["values_denied"]]
    if inert:
        print()
        print("⚠️ 下列变体的读数与基线**完全一致**。这不等于「该部件无用」，"
              "只等于「这一轮没有触发它」：")
        for x in inert:
            print(f"   · {x}")
        print("   （这正是泛化实验里 Δ=0 的两种性质之一：空转，而不是「没有效果」。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
