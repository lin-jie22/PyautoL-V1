#!/usr/bin/env python3
"""V1 入口。

零第三方依赖。三种用法：

  1) 离线自检（不需要 API key，几秒钟跑完，最常用）
       python3 run.py --selftest

  2) 真跑一个任务
       V1_LLM_API_KEY=sk-... V1_LLM_MODEL=gpt-4o-mini python3 run.py --goal "..."

  3) 测量模式（V1 存在的理由 —— 不要用单个数字描述一个候选）
       python3 run.py --selftest --repeat 5
       python3 run.py --goal "..." --sweep 1,2,4
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mw import config as cfg                      # noqa: E402
from mw.core import Core                          # noqa: E402
from mw.critique import CritiqueLedger            # noqa: E402
from mw.ledger import (CALIBER_FIELD, ScoreLedger, is_passed,  # noqa: E402
                       ledger_caliber_of, ledger_sig_of,
                       next_session)
from mw.llm import Message                        # noqa: E402
#: 能力曲线的判据全在 `mw/loop.py`（内核之外）。这里**只接线，不重写判据** ——
#: 一旦 run.py 自己算一遍趋势，就有两套口径，迟早一套改了另一套没改。
from mw.loop import Curve as _Curve               # noqa: E402
from mw.loop import run_loop as _run_curve        # noqa: E402
from mw.measure import (Measurement, budget_curve, noise_floor,  # noqa: E402
                        paired_compare, repeat, summarize)
#: 工具目录指纹的**唯一**实现（契约 AF1 拿它与真实 `Core.tools.catalog()` 对撞）。
from mw.tools import catalog_sha as tools_catalog_sha     # noqa: E402

#: 包所在目录 = "装在哪儿"。⛔ 它**不是**仓库根：`pip install .` 之后这里是
#: site-packages（第五十八轮实测，见 `data_path()` 的说明）。
_PKG_DIR = Path(__file__).resolve().parent


def data_path(rel: Path, root: Path | None = None) -> Path:
    """仓库里的**数据文件**（任务集 / 打分器）放哪儿：**当前目录优先，包目录兜底**。

    ★ 第五十八轮（产品化 D-a，实测证据 `bench/evidence/knowledge_s61/install_probe_*.txt`）：
    `pip install .` 之后 `run.py` 落在 `site-packages`，包目录随之落到那里 ⇒
    实测 `v1 --bench --compare` 报「找不到 …/site-packages/bench/tasks/coding_small.jsonl」
    **即便此刻就站在仓库根目录里**（先装、再从 checkout 跑是最常见的形态）。

    ⇒ 先看当前目录有没有这份数据，有就用；没有才退回包目录（与修之前的行为一致）。

    ⛔ 只对**默认值**生效：用户显式给的 `--suite` / `--scorer` 一律原样解析
    （相对路径按当前目录、绝对路径按绝对路径），不进这个兜底逻辑 ——
    否则"用户的路径找不到"会被悄悄换成别处的同名文件，那是比报错更坏的假成功。

    ⚠️ 第五十九轮把它从 `main()` 里提到模块级：嵌在函数里的闭包**测不了**，
    这正是它曾经"改了但没有回归锚"的原因（AZ48 现在钉它）。`root` 显式传入
    只为让契约能在临时目录里造两种形态，生产路径一步不变。
    """
    base = _PKG_DIR if root is None else root
    cand = Path.cwd() / rel
    return cand if cand.exists() else (base / rel)


# ---------------------------------------------------------------------------
# 离线演示模型：让 loop 真的从 start 走到 finish，并且**故意撞一次守卫**
# ---------------------------------------------------------------------------
class DemoLLM:
    """按调用序推进的脚本化模型。

    它不是玩具 —— 它是内核可验证性的地基：证明"在没有任何外部依赖、
    没有网络、没有钱包"的情况下，主循环能完整走完 perceive→plan→gate→
    foresee→act→observe→persist 七段，并留下可对账的读数。
    """

    # 目标刻意写成"多子句"形态：这样 ACT 会判为复杂任务，走深通道，
    # 于是 perceive→plan→gate→**foresee**→act→observe→persist 七段全部被跑到。
    # 用一个短目标会让世界模型那一段**永远不被执行**却仍显示"全绿"——
    # 这正是最常见的"假绿"。契约测试 I6/I7 就是钉这一点。
    GOAL = "分析并清理 python 截断问题的临时文件，然后总结根因与修复方案，最后把结论写回记忆"

    def __init__(self) -> None:
        self.planner_calls = 0
        self.total = 0

    def complete(self, messages, **kw) -> str:  # type: ignore[no-untyped-def]
        self.total += 1
        blob = "\n".join(m.content for m in messages)

        if "预演" in blob:      # 世界模型预演
            # `expect_ok` 是预演里**唯一会被事后检验**的字段（缺陷 #12 的修复）：
            # 说"会成功"就一定会被 `result.ok` 对照。这里**故意**让 note 那一步
            # 预演错（说"会失败"、实际成功）—— 与第一个候选故意破坏性同理：
            # 证明失配链路真的会跑并沉淀，而不是一个永远不会触发的摆设。
            # 一个在自检里永远"全对"的世界模型，和"每轮全绿"是同一种谎。
            # ⚠️ 必须匹配**动作行**，不能用 `"note" in blob`：
            #    预演提示词里还带着 `guidance`（上一轮的教训原文），
            #    而那条教训的正文里就写着 `note` —— 于是 finish 那一步会被误判成 note。
            #    第一次自检就是这么抓到的（finish 被冤枉成"预演失败"）。
            if "准备执行的动作：note(" in blob:
                return json.dumps({"expect_ok": False,
                                   "expected": "写回记忆时会因守卫拒绝而失败",
                                   "confidence": 0.72}, ensure_ascii=False)
            return json.dumps({"expect_ok": True,
                               "expected": "会返回与截断相关的历史结论条目",
                               "confidence": 0.72}, ensure_ascii=False)

        self.planner_calls += 1
        if self.planner_calls == 1:
            # 第一个候选**故意**是破坏性动作 —— 用来证明守卫真的在拦，
            # 而不是一个"永远不会触发"的摆设（假绿的常见形态）。
            return json.dumps({"candidates": [
                {"intent": "直接清理 python 截断问题的临时文件", "tool": "run_shell",
                 "args": {"cmd": "rm -rf ~"}, "rationale": "一步到位（应被守卫拦下）"},
                {"intent": "先在记忆里找已有的 python 截断相关结论", "tool": "recall",
                 "args": {"query": "python 截断 问题"}, "rationale": "避免重复劳动"},
            ]}, ensure_ascii=False)
        if self.planner_calls == 2:
            return json.dumps({"candidates": [
                {"intent": "把本轮发现写回语义记忆", "tool": "note",
                 "args": {"text": "python 写文件时应显式指定 encoding=utf-8，"
                                  "否则默认编码会在非 ASCII 上静默截断",
                          "source": "selftest", "confidence": 0.9},
                 "rationale": "沉淀结论"},
            ]}, ensure_ascii=False)
        return json.dumps({"candidates": [
            {"intent": "给出结论并收尾", "tool": "finish",
             "args": {"answer": "根因是未指定 encoding 导致默认编码静默截断；"
                                "修复：所有 open() 显式 encoding='utf-8'。"},
             "rationale": "已经拿到足够信息"},
        ]}, ensure_ascii=False)


#: 直连答题的系统提示词。刻意**只约束输出形态**，不教解题 ——
#: 一旦开始教解题，量到的就不是模型能力，而是提示词工程的水平。
DIRECT_SYSTEM = (
    "你是代码助手。只输出一个 python 代码块，不要任何解释、不要示例调用。"
    "函数名与签名必须与题目要求完全一致。"
)


def _build(goal: str, args, *, allow_shell: bool = False,
           critique_ledger: "CritiqueLedger | None" = None,
           score_ledger: "ScoreLedger | None" = None,
           caliber_sig: str = "") -> Core:
    s = cfg.settings()
    mem_mode = str(getattr(args, "memory_mode", "raw") or "raw")
    # ── ★ 第一百三十三轮（§109）：guard 必须在**两条分支之前**统一构造 ──────
    # 修之前它只写在离线分支里（`if args.offline or not s.llm.usable():`），
    # 于是**真模型路径上 `--allow-shell` 完全无效** —— 命令行带着
    # `--allow-shell`，日志里照样是 `[guard:tool_misuse] shell 未开启`。
    #
    # 实测（2026-11-02 12:58，`sales.csv → report.md`）：这就是让位拿到的第 2 个
    # 候选被拦掉的原因。它是「机制存在 ≠ 机制通电」的**第五种形态**：
    #   前四种（§87/§99/§107）是"开关没开 / 载荷送不到 / 检测器失效 / 输出接在
    #   记录仪上"，这一种是 **开关只在一条没人走的分支上通电** ——
    #   而且那条分支恰好是"离线桩"，也就是**每一次门禁都在走它**，
    #   所以契约测试永远是绿的，只有真模型路径是坏的。
    #
    # ⛔ 不改成"构造后 `core.guard = …` 覆盖"：`Core.__init__` 已经把 guard 传给了
    #    内部的 tools/planner 路径（`self.guard = guard or Guard(...)`），
    #    事后替换会让"谁拿着哪个 guard"取决于构造顺序 —— 那正是本轮在修的病。
    from mw.guard import Guard
    _guard = Guard(allow_shell=allow_shell, workspace=s.paths.workspace,
                   max_consecutive_failures=s.limits.max_consecutive_failures)
    if args.offline or not s.llm.usable():
        from mw.llm import StubLLM
        core = Core(s, llm=StubLLM(), guard=_guard,
                    critique_ledger=critique_ledger,
                    score_ledger=score_ledger, caliber_sig=caliber_sig,
                    memory_mode=mem_mode,
                    wire_external=not getattr(args, "no_external", False))
    else:
        core = Core(s, guard=_guard, critique_ledger=critique_ledger,
                    score_ledger=score_ledger, caliber_sig=caliber_sig,
                    memory_mode=mem_mode,
                    wire_external=not getattr(args, "no_external", False))
    return core


def _memory_seed(s) -> tuple[int, int]:
    """读**起跑前**的记忆基线 (facts, episodes)。

    为什么必须有它：`shared_db` 模式下记忆跨题、**跨运行**累积，两轮读数只有在
    起跑基线相同时才是同口径。不读它，比的就是"记忆多少"而不是"候选好坏"。
    ⚠️ 必须在跑之前读 —— 跑完再读拿到的是**结果**，不是条件。
    """
    from mw.memory import MemoryStore
    try:
        ms = MemoryStore(s.paths.db)
    except Exception:                                          # noqa: BLE001
        return (0, 0)
    try:
        st = ms.stats()
        return (int(st.get("facts", 0)), int(st.get("episodes", 0)))
    finally:
        ms.close()


def _prov(*, llm_kind: str, external_agents, model, temperature) -> dict:
    """血缘 dict 的**唯一构造入口** —— 字段集合只声明在这一处。

    ⛔ 为什么做成函数而不是"各分支各拼一个 dict"：本轮实测抓到第 9 个缺陷 ——
    内核路径的三个分支各自拼血缘，`--fresh` 那一支**漏了 `model`**，于是
    跨领域跑和泛化对照（考A）两条读数**事后无法核对自己跑的是哪个模型**。
    而**没有任何断言会发现**这件事：血缘这套防线自己的第一条纪律
    （"读数必须带实际用了什么"）身上没有防线。

    收敛成一个 dict 字面量之后，"漏字段"从"需要测试发现"变成"写不出来"。
    契约 X 组同时钉住：① 返回的键集合恒等于 `PROV_KEYS`；
    ② run.py 里**只有这里**出现带 `llm_kind` 键的 dict 字面量。
    """
    return {"llm_kind": llm_kind, "external_agents": list(external_agents),
            "model": model, "temperature": float(temperature)}


#: 血缘必须带的字段（**唯一声明处**）。加/删字段只改这里。
PROV_KEYS: tuple[str, ...] = ("external_agents", "llm_kind", "model", "temperature")


def _kernel_prov(core) -> dict:
    """从**实际构造出来的内核**读血缘。事实，不是声明。

    ⚠️ 三个值全部取自内核自己（`core.llm_kind` / `core.external_agents` /
       `core.s.llm`），不是外面传进来的字符串 —— 与 K5/L6 同一条纪律：
       标签可以撒谎，血缘不能。
    ⚠️ `temperature` 同源：§13⑨ 的事故（口径写 0.0、实际跑 0.2）只有把内核
       **自己**的温度记进血缘，才可能被事后审计出来。
    """
    return _prov(llm_kind=core.llm_kind,
                 external_agents=core.external_agents,
                 model=core.s.llm.model,
                 temperature=core.s.llm.temperature)


def _dump_states(path: str, task_id: str,
                 states: list[tuple[int, str]] | None,
                 metas: list[dict] | None = None) -> int:
    """把内核本轮每次 `_state()` 的产物落到证据文件（第三十六轮 §17-7h）。

    ⛔ 为什么由 **runner** 写而不是内核写：内核不产生实验产物，也不该知道
       "这一轮是不是在跑实验"。它只把 state 留在内存里（有界），
       有 `--state-dump` 的调用方才取走 —— 没有就是零开销、零盘上痕迹。

    ⛔ 落盘的是**原文**，不做任何裁剪：R1 的第二层要按块切开比对，
       裁一刀就等于替判据先把可疑的部分删掉了。

    ★ V36：除 `state` 外再落 `n` 与 `guidance`（内核 `state_meta`，同长同序）。
      ⛔ 为什么非落不可：V35 实测 `v26/d2` 的 `e1wi` 两轮 **state 只差 1 个字符**，
         而 proxy 台账里 `req_bytes` 差 **488**。差额落在 state 之外，而
         `wm.guidance()` 与候选数 `n` 从来不进 `_state()` ⇒「读数为什么变」无法归因。
         V34 的纪律 43 已经写明：**`state` 不是 `prompt` 的身份**。这两个字段是把它补上。
      ⚠️ 两份列表长度不等 = 证据错位。⛔ 不许静默按 idx 对齐（那是"看起来有值"），
         必须当场炸 —— 错位的证据比没有证据更糟。
    """
    if not path or not states:
        return 0
    metas = list(metas or [])
    if metas and len(metas) != len(states):
        raise RuntimeError(
            f"state 与 state_meta 长度不等（{len(states)} vs {len(metas)}）⇒ "
            f"证据已错位，拒绝落盘。这是内核 `state_meta` 裁剪不同步的 bug。")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for idx, (it, st) in enumerate(states):
            meta = metas[idx] if idx < len(metas) else {}
            f.write(json.dumps({"task": task_id, "it": it, "state": st,
                                "n": meta.get("n"),
                                "guidance": meta.get("guidance"),
                                # ★ §103：本步**逐候选**的预演读数
                                #   （`ok` 三态 / `conf` / `trust` / `veto` / `src`）。
                                #   ⛔ 不以"有预演就非空"为前提：快通道没有预演，
                                #     这里的空列表就是"这一步没预演"，不是"没落盘"。
                                "preds": meta.get("preds") or []},
                               ensure_ascii=False) + "\n")
    return len(states)


def _kwm_zero() -> dict:
    """内核侧世界模型读数累加器的零点。"""
    return {"predictions": 0, "scored": 0, "hits": 0, "unscored": 0,
            "mismatches": 0, "mismatches_outcome": 0, "mismatches_text": 0,
            "text_compared": 0, "suppressed": 0,
            # —— §103：预演接进决策链的两个读数 ——
            #: `vetoed` = 真的决定不做的次数（"机制通电了吗"的唯一读数）；
            #: `veto_refused_answer` = 条件命中但被"交付路径豁免"放行的次数。
            #: ⛔ 两个必须并列：只有前一个，`vetoed=0` 会读成"模型没预测过失败"。
            "vetoed": 0, "veto_refused_answer": 0,
            # —— 外部奖励通道：写了几条 / **读了几条** ——
            # ⛔ 只报"写入 N 条"而不报"读到几条"，正是"字段无消费者"：
            #    一个你永远看不出它有没有被用过的通道 = 装饰。
            "rewards_recorded": 0, "rewards_seen": 0, "rewards_skipped_self": 0,
            "rewards_skipped_success": 0,
            "rewards_rejected_source": 0, "rewards_rejected_missing": 0,
            # —— 读层：语义记忆检索到几条 / 其中几条属于抽象层（memory-mode=abstract 时）——
            "layer_recalled": 0, "layer_abstract": 0,
            "confusion": {"ok_ok": 0, "ok_fail": 0, "fail_ok": 0, "fail_fail": 0}}


#: ⛔ 累加键**由零点派生**，不许另写一份名单。
#: 本轮自踩：奖励通道的三个新字段没被搬进累加器（旧代码是一份硬编码的键名单），
#: 于是日志上"写了 5 条"而"读到 0 条"—— 一个你看不出有没有被用过的通道 = 装饰。
_KWM_NUM_KEYS: tuple[str, ...] = tuple(k for k in _kwm_zero() if k != "confusion")


#: 规划器读数的累加键（V39）。⛔ 同样由零点派生 —— 理由见 `_KWM_NUM_KEYS`。
_KPL_NUM_KEYS: tuple[str, ...] = ("calls", "degraded", "json_fail", "truncated_responses",
                                  "no_tool_fit", "empty_plan", "reseek",
                                  "answer_retry_ok", "answer_retry_fail",
                                  # ★§106：格式修复读数。**必须进这个元组**，
                                  # 否则 `salvage_ok` 只活在 `Planner.stats()` 里、
                                  # 不进 `runs.jsonl` ⇒ 又一次"机制有牙但没读数"。
                                  "salvage_ok", "salvage_fail",
                                  "salvage_completed")


def _kpl_zero() -> dict:
    """规划器读数的零点（第三十九轮 / V39）。

    ⛔ 为什么要落盘：`Planner` 一直有 `json_fail` / `truncated` / `no_tool_fit` /
    `empty_plan` 四个**分项**降级计数（`mw/planner.py`，注释写着"修法不同的分支
    合并，就再也分不清该修哪里"）—— 但它们**从来没进过 `runs.jsonl`**：
    台账里只有 `kernel_worldmodel` / `kernel_closeout` / `kernel_external` /
    `kernel_transport`，没有 `kernel_planner`。

    后果是具体的：V39 的前置量要靠这四项才看出「请求 n=3 时 6 题全坏」，
    而那时候它们是**从 stdout 日志里手数的**（`⚠️` 行 + 正则）——
    手数不进台账 ⇒ 不可审计，下一轮只能再数一遍。

    ⚠️ `degrade_rate` / `cause_balances` **不进累加键**：一个是派生比值
    （§104.3 已证明比值同样会被重复累加污染），一个是单轮不变量。
    """
    return {k: 0 for k in _KPL_NUM_KEYS}


def _kpl_absorb(acc: dict, stats: dict, core: object = None) -> dict:
    """把一次内核运行的规划器读数并进累加器（就地修改并返回）。

    ⚠️ **必须求差，不能直接累加**：`--memory-dir` 臂复用同一个 `Core`
    （`run.py` 的 `_core` 分支），而 `Planner` 的计数是生命周期内纯 `+=`
    ⇒ `stats()` 给的是**跨题累计值**。V38 已经为 `_kwm` 修过一次同一个病
    （6 题 `2,3,5,6,8,10` 被加成 34），这里不许重演：基线挂 `core._kpl_seen`。

    ⛔ 基线**不挂 `acc`**：`_kpl_line` 对每个键做 `int(v)`，把 dict 塞进
    `acc` 会把落盘打断 —— 这正是 V38 记录过的"顺手放一下会炸掉的地方"。
    ⛔ 也不做 `max(0, …)` 钳位：负数只可能意味着"有人改了会清零的计数器"，
    读数要显形，不许被钳掉（同 §104.2 第 3 条）。
    """
    now = {k: int(stats.get(k, 0) or 0) for k in _KPL_NUM_KEYS}
    prev = getattr(core, "_kpl_seen", None) if core is not None else None
    for k in _KPL_NUM_KEYS:
        was = int((prev or {}).get(k, 0) or 0)
        acc[k] = int(acc.get(k, 0)) + (now[k] - was)
    if core is not None:
        setattr(core, "_kpl_seen", now)
    return acc


def _kpl_line(acc: dict) -> dict:
    """落盘前的派生量。**分母同时给** —— 只报 `json_fail=7` 无法判断严重程度。

    `fail_total` 把四种"这次规划回答用不了"的分支合成一个数，
    但它**不替代分项**：四个分支的修法完全不同（改提示词 / 调 max_tokens /
    加工具 / 改解析器），合起来只剩一个数就归不了因。
    """
    out = {k: int(acc.get(k, 0) or 0) for k in _KPL_NUM_KEYS}
    out["fail_total"] = (out["json_fail"] + out["truncated_responses"]
                         + out["empty_plan"] + out["no_tool_fit"])
    out["fail_rate"] = (round(out["fail_total"] / out["calls"], 3)
                        if out["calls"] else None)
    return out


#: ★ V66（§133）：**候选截断**的累加键。⛔ 与 `_KPL_NUM_KEYS` 分属两个字典 ——
#:   那个数的是「一次响应被截」（单位：**次**），这个数的是「一个候选被截」
#:   （单位：**条**）。同名不同义是这一轮要修的病，不是要沿用的写法。
_KP_NUM_KEYS: tuple[str, ...] = ("rounds", "examined_total", "cands_total",
                                 "truncated_cands", "dropped_not_applicable")


def _kp_zero() -> dict:
    """候选宽度读数的零点（V66 / §133）。

    ⛔ 为什么要落盘：`Core.plan_stats` 从 §111 起就有 `examined_total` /
    `truncated_cands`，但它**只经 `snapshot()["plan"]` 出去** ——
    `runs.jsonl` 的写盘字典里从来没有 `kernel_plan` 这个键。
    ⇒ 跑批层面读不到「规划器给了几条、被 `[:n]` 截掉几条」，只有直接调
    `core.snapshot()` 的探针（V45/V47）能看见。这是纪律 26 的原形：
    **stats 里看得见、账本里看不见** —— 而"看不见"正是这个缺口欠了六轮的原因
    （不是名字不好，是**没有任何一份产物同时含这两个截断读数**）。

    ⚠️ 不复用 `_KPL_NUM_KEYS`：那个字典里已经有 `truncated_responses`（次），
    并进同一个字典就等于把「该调 `max_tokens`」和「该调 `n`」重新合成一格 ——
    那正是 `mw/planner.py` 里被明确否掉的写法（"修法不同的分支合并，就再也
    分不清该修哪里"）。

    ⚠️ **不需要求差**（与 `_kpl_absorb` 不同，与 `_kfo_absorb` 相同）：
    `plan_stats` 在每次 `run()` 开头由 `_prime_counters` 重建
    （`mw/core.py:988`）⇒ `stats()["plan"]` 天然是**本题**值。
    照抄 `_kpl_absorb` 的求差写法会把它减成 0。
    """
    return {k: 0 for k in _KP_NUM_KEYS}


def _kp_absorb(acc: dict, stats: dict) -> dict:
    """把**一题**的 `stats["plan"]` 并进累加器（就地修改并返回）。"""
    for k in _KP_NUM_KEYS:
        acc[k] = int(acc.get(k, 0)) + int(stats.get(k, 0) or 0)
    return acc


def _kp_line(acc: dict) -> dict:
    """落盘前的派生量。**分母一起给** —— 只报 `truncated_cands=3` 读不出严重程度。

    ⚠️ `truncated_rate` 的分母是 `examined_total`（规划器**交出**的步骤数），
    不是 `cands_total`（截断之后进执行器的）：
    用后者，一旦某题全被截掉（`cands_total=0`）就除零，而那一刻恰恰最该报。
    """
    out = {k: int(acc.get(k, 0) or 0) for k in _KP_NUM_KEYS}
    out["truncated_rate"] = (round(out["truncated_cands"] / out["examined_total"], 3)
                             if out["examined_total"] else None)
    return out


def _kfo_zero() -> dict:
    """候选让位读数的累加器零点（第一百三十二轮 / §108）。

    ⛔ 为什么要落盘：`Core.failover_stats` 是**单轮**读数（与预算同源，
    每轮清零）⇒ 不并入 `runs.jsonl` 的话，它只活在一行 stdout 里，
    事后无法审计 —— 而本轮**全部判决**都压在这五个数上。

    ⚠️ 与 `_kpl` 不同，这里**不用求差**：`failover_stats` 在每次 `run()` 开头
    重建（`self.failover_stats = {...}`），所以 `stats()` 给的天然是**本轮**值。
    V38 那个"累计值当增量"的坑不在这里 —— 但**必须在注释里说清为什么不是它**，
    否则下一个读代码的人会照抄 `_kpl_absorb` 的求差写法，把本轮值减成 0。
    """
    return {"used": 0, "rescued_answer": 0, "rescued_step": 0, "rescued": 0,
            "cf_extra": 0,
            "finish": 0, "external": 0, "no_more": 0, "switch": 0}


def _kfo_line(acc: dict) -> dict:
    """派生量：`rescue_rate` 的分母是 `used`（**让位次数**），不是任务数。

    ⛔ 只报 `rescued=1` 而不给分母 ⇒ 读者无法分开"让位 1 次救回 1 次"与
    "让位 40 次救回 1 次"。V39 的 `hit_rate` 就是栽在分母上（§104.3）。
    """
    out = {k: int(acc.get(k, 0) or 0) for k in _kfo_zero()}
    # ★ 第一百三十六轮（§112）：`rescued` 拆两档之后，**判决入口只认结果量**。
    # ⛔ 分子不许改成 `rescued_answer + rescued_step`：那样"救回一个中间步骤"
    #    会被算成"救回一次交付" —— 正是 V39 `hit_rate` 栽过的分母病（§104.3）。
    #    步骤那一档单独印，但**不进率**：它的分母根本不是"交付机会"。
    out["rescue_rate"] = (round(out["rescued_answer"] / out["used"], 3)
                          if out["used"] else None)
    return out


def _kfo_absorb(acc: dict, stats: dict) -> dict:
    """把一次内核运行的让位读数并进累加器（就地修改并返回）。

    ⚠️ **`switch` 不能求和**：它是**配置值**，不是计数。6 道题求和会印出
    `开关=6` —— 而"开关"这个数在读者眼里是"0/1"。这正是本文件反复防的那类
    混用（V38：把累计值当增量）。这里取 `max`，所以它恒等于"本轮跑过的最大档位"。
    ⭐ 这条是**实测撞出来的**：第一版对所有键一视同仁地 `+=`，
    单元自测立刻给出 `switch: 2`（两次 absorb 各 1）。
    """
    stats = stats or {}
    for k in _kfo_zero():
        if k == "switch":
            acc["switch"] = max(int(acc.get("switch", 0) or 0),
                                int(stats.get("switch", 0) or 0))
            continue
        acc[k] = int(acc.get(k, 0)) + int(stats.get(k, 0) or 0)
    return acc


def _kex_zero() -> dict:
    """外部能力读数的累加器零点（第二十三轮，路线①）。

    ⛔ 为什么要落盘：`stats["external"]` 只在**内存里**，而 bench 的读数是从
    `runs.jsonl` 读的。不落盘 ⇒ "外部能力配了没用上"这件事在证据里不可见，
    只能靠我回忆 —— 那就是不可审计。
    ⚠️ `calls` 与 `used_as_answer` 必须分列：调用成功 ≠ 答案被采用。
    """
    return {"tool": "", "tasks": 0, "calls": 0, "ok": 0, "err": 0,
            "used_as_answer": 0, "chars": 0,
            "samples": 0, "tests": 0, "fallback": 0, "discriminated": 0,
            #: 因为**额度不够**而根本没被注入（不是失败，是没试）
            "skipped_no_budget": 0,
            #: ★ §113：因为**题面不满足它自己声明的适用条件**而没被注入。
            #: ⛔ 与上一项分开：那个是"再等等"，这个是"永远不行"。
            "skipped_not_applicable": 0,
            #: 理论要跑筛选、实际因为时间不够**没跑成**的次数
            "exec_skipped": 0,
            #: 逐次失败原因（去重、上限 6 条）。⛔ 只留 `last_error` 会让
            #: "7 调 5 败"退化成一句笼统的话 —— 不可归因的失败等于没测。
            "err_reasons": [],
            "last_error": ""}


def _kex_absorb(acc: dict, stats: dict) -> dict:
    """把一次内核运行的外部能力读数并进累加器（就地修改并返回）。"""
    tool = str(stats.get("tool") or "")
    if tool:
        acc["tool"] = tool
    for k in ("calls", "ok", "err", "used_as_answer", "chars",
              "skipped_no_budget", "skipped_not_applicable"):
        acc[k] = int(acc.get(k, 0)) + int(stats.get(k, 0) or 0)
    if int(stats.get("calls") or 0) and not int(stats.get("ok") or 0):
        acc["last_error"] = str(stats.get("last_error") or "")[:200]
    # ★ 逐次失败原因进证据：**不是**只在"全败"时留一条。
    #   ⛔ 一个臂 7 调 2 成 5 败时，`ok>0` ⇒ 旧写法根本不记原因；
    #      而"哪几种病"恰恰是下一步该修什么的唯一依据。
    for _why in (stats.get("err_reasons") or []):
        _w = str(_why)[:240]
        if _w and _w not in acc["err_reasons"] and len(acc["err_reasons"]) < 6:
            acc["err_reasons"].append(_w)
    meta = stats.get("meta") or {}
    #: 外部进程的自述：采了几份、写了几个用例、有没有退到"能编译的第一份"。
    #: ⚠️ 自述不是判据，只做"它到底做了什么"的审计。
    #: ⛔ 第二十三轮实测抓到的谎报：原来写的是 `+= meta["k"]` —— 那是**配置**的
    #:    k，不是**实际生成的份数**。于是"k=3 但时间不够只生成 1 份"会被记成
    #:    `samples=3`，读数看起来"采了 3 份、筛过了"，实际只交了第一份。
    pc = meta.get("pass_counts") or []
    acc["samples"] = int(acc.get("samples", 0)) + len(pc)
    acc["tests"] = int(acc.get("tests", 0)) + int(meta.get("tests") or 0)
    if meta.get("fallback"):
        acc["fallback"] = int(acc.get("fallback", 0)) + 1
    if meta.get("exec_skipped"):
        acc["exec_skipped"] = int(acc.get("exec_skipped", 0)) + 1
    # ★ 选择器**到底有没有做选择**：三份实现打成平手时，"按通过数排序"等于
    #   按生成顺序交付第一份 —— 读数上必须能看出"筛选是空转的"。
    #   ⛔ 只报 "k=3 采样" 会让人以为筛选起了作用（第二十二轮"触发≠救回"的同构错误）。
    if len(set(pc)) > 1:
        acc["discriminated"] = int(acc.get("discriminated", 0)) + 1
    return acc


def _kco_zero() -> dict:
    """收尾兜底读数的累加器零点（第二十二轮）。

    ⛔ 为什么要落盘：第二十一轮的全部位移都走 `_close_out`，而它当时只有两句日志。
    日志能证明"走到过"，回答不了"几道题走到过 / 几次真的改掉了交付物" ——
    **不落盘的读数不可审计**，这一条与 `kernel_worldmodel` 同源。
    """
    return {"triggered": 0, "chars": 0, "empty": 0,
            "overrode_rejected": 0, "same_as_rejected": 0,
            #: ★V53（§119）：收尾交付的**产物问**。不并进上面两个数：
            #: 「答案改没改」与「东西在不在」是两个独立的边（§110 分列的延续）。
            "artifact_fail": 0, "artifact_partial": 0, "artifact_error": 0,
            #: ★ V54（§120）：进收尾之前**一次都没下发**的次数（按入口分列）。
            #: ⛔ 与 `triggered` 分列：`triggered` 说"进过几次收尾"，
            #:    这个说"其中几次连一个动作都没做" —— 后者原来在落盘产物里不可见。
            "zero_exec": 0, "zero_exec_by_reason": {},
            "origin": {}, "by_reason": {"steps": 0, "wall": 0}}


def _kco_absorb(acc: dict, stats: dict) -> dict:
    """把一次内核运行的收尾读数并进累加器（就地修改并返回）。"""
    for k in ("triggered", "chars", "empty", "overrode_rejected", "same_as_rejected",
              "artifact_fail", "artifact_partial", "artifact_error", "zero_exec"):
        acc[k] = int(acc.get(k, 0)) + int(stats.get(k, 0) or 0)
    for k, v in (stats.get("origin") or {}).items():
        acc["origin"][k] = int(acc["origin"].get(k, 0)) + int(v or 0)
    for k, v in (stats.get("by_reason") or {}).items():
        acc["by_reason"][k] = int(acc["by_reason"].get(k, 0)) + int(v or 0)
    for k, v in (stats.get("zero_exec_by_reason") or {}).items():
        acc["zero_exec_by_reason"][k] = (
            int(acc["zero_exec_by_reason"].get(k, 0)) + int(v or 0))
    return acc


def _kexec_zero() -> dict:
    """按题的「下发次数 × 验收结论」累加器零点（V55，§121）。

    ## 为什么必须落盘

    「零下发的文本交付算不算交付」这个问题，**手上 26 条真实判定行答不了**：
    `kernel_closeout.zero_exec` 只覆盖**收尾出口**，而 `finish` 出口
    （`status=done`，占真实判定行的 12/26）一条都没被它数过；`report.per_task`
    只有 `{id, rep, score, why}`，既没有 `verdict` 也没有 `exec`。
    于是这个交叉表只能靠 grep stdout 的 `■` 行 —— 而 V55 第一次回溯就因为
    `startswith("↳ ")` 撞上 `HH:MM:SS` 前缀，把 14/14 全判成"零调用"。
    **同一个问题两次读不一样**，那它就不是读数。

    ## 与 `kernel_closeout.zero_exec` 的区别（⛔ 两个分母，不许合并）

    | 字段 | 分母 | 回答 |
    |---|---|---|
    | `kernel_closeout.zero_exec` | 进过收尾的**次数** | 收尾之前动过手没有 |
    | `kernel_exec.zero_dispatch_tasks` | **题数**（全部出口） | 这题一次都没下发就交了 |
    | `kernel_exec.zero_step_tasks` | **题数**（全部出口） | 这题**一次非交付动作都没有**（V56） |

    前两者是"收尾这个出口"与"这一题"的属性。合成一个数，
    `done` 出口的零下发就会被永久藏起来 —— 而那正是 §116/§118 的病位。

    ⛔ 第三个（V56）**不是**第二个的同义词：`finish` 本身也是一次下发，所以
    「零下发」只等于"连答案都没提"；真正有分母的病位是「**12 题里有 6 题只提了
    `finish`**」，那个数只有 `zero_step_tasks` 答得了（V55 的 `finish×12 + recall×6`）。
    """
    return {"tasks": 0, "dispatched": 0, "ok": 0, "fail": 0,
            #: ★ V56（§122）：`answer` = 交付动作（`finish` / `produces_answer`）
            #: 的下发次数，`step` = 其余。⛔ 两个都要：`finish` 本身也是一次下发，
            #: 只报总数会把「12 题 18 次」读成"做了十八件事"，其实是 12 次交答案
            #: + 6 次做事（V55 实测）。
            "answer": 0, "step": 0,
            "zero_dispatch_tasks": 0,
            #: ★ V56（§122）：**这题一次非交付动作都没有**的题数。分母是**题数**
            #: （与 `zero_dispatch_tasks` 同分母）⇒ 可以直接对着 V55 的 6/12。
            #: ⚠️ 两者是**超集**关系（零下发 ⇒ 零步骤）：差值 = 「只提了交付动作」
            #: 的题数。⛔ 不另存派生字段：派生量要么由一处算式产生、要么不存。
            "zero_step_tasks": 0,
            #: 三个字典**分列**：`by_verdict` 是"每题验收结论的分布"，
            #: `zero_dispatch_by_verdict` / `zero_step_by_verdict` 是两个**子集**的
            #: 分布。只看子集会读成"零下发只发生在 ANSWER 上"（样例里确实如此），
            #: 但那是**交集**，不是全集 —— 并列才有分母。
            "by_verdict": {}, "zero_dispatch_by_verdict": {},
            "zero_step_by_verdict": {}}


def _kexec_absorb(acc: dict, out) -> dict:
    """把**一道题**的下发读数并进累加器（就地修改并返回）。

    ⚠️ `tasks` 在**这里**加，不由调用方另写一句：调用点有两处（每题新建内核 /
        复用内核），两处各写一遍就是"同一事实声明两处"，漏一处即静默少计一题
        —— 与 `_ktx_absorb` 同一条理由。
    """
    ex = (getattr(out, "stats", None) or {}).get("exec") or {}
    d = int(ex.get("dispatched") or 0)
    acc["tasks"] = int(acc.get("tasks", 0)) + 1
    acc["dispatched"] = int(acc.get("dispatched", 0)) + d
    acc["ok"] = int(acc.get("ok", 0)) + int(ex.get("ok") or 0)
    acc["fail"] = int(acc.get("fail", 0)) + int(ex.get("fail") or 0)
    # ★ V56（§122）：交付/步骤两个分项按题累加。
    # ⚠️ 用 `.get(..., 0)` 而不是 `ex[...]`：这里的 `ex` 可能来自**旧**的冻结产物
    #    （V54 之前的账本没有这两个键），缺键就炸会把"读旧数据"变成"跑不动"。
    #    ⛔ 但这与 `core.py` 里 `■` 行的写法**故意不同**：那里缺键必须炸
    #    （缺席冒充零），这里缺键只可能是旧产物（兼容）。
    acc["answer"] = int(acc.get("answer", 0)) + int(ex.get("answer") or 0)
    acc["step"] = int(acc.get("step", 0)) + int(ex.get("step") or 0)
    _s = int(ex.get("step") or 0)
    #: `verdict` 空串表示"没算过"（例如 `_finish` 之前异常）⇒ 记成 `?` 而不是丢掉：
    #: 丢一题会让 `by_verdict` 的合计对不上 `tasks`，而那正是"缺席冒充零"。
    _v = str(getattr(out, "verdict", "") or "?")
    _bv = acc.get("by_verdict")
    if not isinstance(_bv, dict):
        _bv = acc["by_verdict"] = {}
    _bv[_v] = int(_bv.get(_v, 0)) + 1
    if d == 0:
        acc["zero_dispatch_tasks"] = int(acc.get("zero_dispatch_tasks", 0)) + 1
        _zv = acc.get("zero_dispatch_by_verdict")
        if not isinstance(_zv, dict):
            _zv = acc["zero_dispatch_by_verdict"] = {}
        _zv[_v] = int(_zv.get(_v, 0)) + 1
    # ★ V56（§122）：零**步骤**动作（**不是**零下发）—— 分母仍是题数。
    # 两者必须是**两个** `if`，不许写成 `if d == 0: ... elif _s == 0:`：
    # 零下发的题必然同时零步骤，写成一个分支就会把它从 `zero_step` 里漏掉
    # ⇒ 超集关系断掉，而"差 6 题"正是这一轮要看的东西（缺席冒充零）。
    if _s == 0:
        acc["zero_step_tasks"] = int(acc.get("zero_step_tasks", 0)) + 1
        _zs = acc.get("zero_step_by_verdict")
        if not isinstance(_zs, dict):
            _zs = acc["zero_step_by_verdict"] = {}
        _zs[_v] = int(_zs.get(_v, 0)) + 1
    return acc


def _koff_zero() -> dict:
    """候选漏斗读数的累加器零点（V57，§123）。

    ## 为什么必须有这一行

    V56 把下发拆成 `answer`/`step` 之后，`zero_step_tasks=6` 被写成一句话：
    「**12 题里有 6 题只提了 `finish`**」。而 `v6_s146/logs_b.txt` 的
    `[i] 候选=N` 行逐题数下来是 **30 个候选进执行器、只下发 18 个** ——
    「提」与「做」是两个数。把它们合成一个，`6/12` 就会被读成
    "模型想不出别的"，而真相是"提了，但没轮到/被拦"。

    ⛔ 这四个数**不许**合成一个"效率"：`offered` 是分母，
    `dispatched` / `blocked` / `unreached` 是三条**修法相反**的出口
    （调宽度 / 放宽闸 / 别在一轮里塞多个候选）。

    ## 五个格子（必须合起来等于 `tasks`）

    | 格 | 含义 | 结构约束 |
    |---|---|---|
    | `no_rounds` | 一次规划都没拿到候选 | 不是"只提了交付"，是**没有分母** |
    | `only_delivery` | 提的全是交付动作 | 真的"只想到 finish" |
    | `offered_not_done` | 提过非交付动作、一次没做成 | **病位** |
    | `done_without_offer` | 做了非交付动作却没提过 | **结构上不可能** ⇒ 漏记检测器 |
    | `offered_and_done` | 提了也做了 | — |

    ⛔ `no_rounds` 单独一格不是洁癖：没有它，一道"压根没规划成功"的题会被
    算进 `only_delivery`，而后者正是本轮要解释的那 2/12 —— 分母就被污染了。
    """
    return {"tasks": 0, "rounds": 0,
            # ── 生成端（分母）──
            "offered": 0,            # Σ 进执行器的候选数（截断 + 准入之后）
            "produces_offered": 0,   # 其中交付动作
            "step_offered": 0,       # 其中非交付动作（= offered - produces_offered）
            # ── 执行端的三条出口 ──
            "dispatched": 0,         # 真的调用了
            "blocked": 0,            # 被 guard / 价值层拦下
            "unreached": 0,          # 同轮提前退出、再没被考虑过
            # ── 五格（分母 = 题数）──
            "no_rounds": 0, "only_delivery": 0, "offered_not_done": 0,
            "done_without_offer": 0, "offered_and_done": 0,
            #: `offered_not_done` 里**同时**有候选被 guard 拦的题数。
            #: ⚠️ 这是**子集**，不是互补：没有它，`offered_not_done=4` 无法区分
            #:    "被拦掉了 2 题"与"没轮到 4 题"—— 而两者修法相反。
            "offered_not_done_blocked": 0,
            #: ★ V58（§124）：`unreached` 按**出口**分列。三格合起来必须等于
            #:    `unreached`（守恒）。⛔ 它不是"更细的同一件事"：
            #:    `answer` 是**损失候选**（交付已经成了，被跳过的可能是更好的答案），
            #:    `step_ok` 两条大概率冗余、`fail` 是预算问题。修法三样。
            "unreached_by_stop": {"answer": 0, "step_ok": 0, "fail": 0},
            #: 被跳过的候选是什么工具。⛔ 与 `blocked` 的 `by_tool` 分列：
            #:    同名合并会让"被闸拦"和"没轮到"重新混成一格。
            "unreached_by_tool": {}}


def _koff_absorb(acc: dict, out) -> dict:
    """把**一道题**的候选漏斗并进累加器（就地修改并返回）。

    ⚠️ 四个来源**都是已有的**，本轮只新增 `offer.unreached`：
        `plan`（生成端，§111 就有）/ `exec`（下发，§120）/ `loop`（被拦/重复，
        §66 就有）/ `offer`（未轮到，V57）。⛔ 不在这里重新数一遍 ——
        抄一份就是同一事实两处声明，而两处迟早对不上（§122 的两个 `by_verdict`）。

    ⚠️ `tasks` 也在这里加：调用点有两处（每题新建内核 / 复用内核），
        两处各写一句就是"漏一处静默少计一题"（与 `_kexec_absorb` 同因）。
    """
    st = getattr(out, "stats", None) or {}
    pl = st.get("plan") or {}
    ex = st.get("exec") or {}
    lp = st.get("loop") or {}
    of = st.get("offer") or {}
    offered = int(pl.get("cands_total") or 0)
    prod = sum(int(x or 0) for x in (pl.get("produces_by_round") or []))
    step_off = offered - prod
    disp = int(ex.get("dispatched") or 0)
    #: ⛔ `blocked` 只认 `loop.blocked_skipped`，**不认** `guard.report()["total"]`：
    #:    后者是 `Guard` 的**生命周期累计**，复用内核时会跨题相加
    #:    （V56 的 `■` 行实测 `guard_blocked=1,2,2,2,2,2,3,4`）。
    #:    用错这一个键，"每题拦了几个"就会读成"到这一题为止一共拦了几个"。
    blk = int(lp.get("blocked_skipped") or 0)
    unr = int(of.get("unreached") or 0)
    step_done = int(ex.get("step") or 0)
    rounds = int(pl.get("rounds") or 0)

    acc["tasks"] = int(acc.get("tasks", 0)) + 1
    acc["rounds"] = int(acc.get("rounds", 0)) + rounds
    acc["offered"] = int(acc.get("offered", 0)) + offered
    acc["produces_offered"] = int(acc.get("produces_offered", 0)) + prod
    acc["step_offered"] = int(acc.get("step_offered", 0)) + step_off
    acc["dispatched"] = int(acc.get("dispatched", 0)) + disp
    acc["blocked"] = int(acc.get("blocked", 0)) + blk
    acc["unreached"] = int(acc.get("unreached", 0)) + unr
    # ★ V58（§124）：分项并入。⛔ 三个键**逐个**并，不 `update()` 覆盖 ——
    #   覆盖会让第二题的分项顶掉第一题的（`_kfo_absorb` 踩过同形的坑）。
    _bs = of.get("unreached_by_stop") or {}
    _bst = acc["unreached_by_stop"]
    for _sk in ("answer", "step_ok", "fail"):
        _bst[_sk] = int(_bst.get(_sk, 0)) + int(_bs.get(_sk) or 0)
    _but = acc["unreached_by_tool"]
    for _tk, _tn in (of.get("unreached_by_tool") or {}).items():
        _but[_tk] = int(_but.get(_tk, 0)) + int(_tn or 0)

    #: 五格互斥且穷尽。⛔ `rounds == 0` 必须**先**判：没有候选的题
    #:    `step_off == 0 and step_done == 0` 也成立，落进 `only_delivery`
    #:    就是一次"缺席冒充零"（§15）—— 那 2/12 的分母当场被污染。
    if rounds == 0:
        _cell = "no_rounds"
    elif step_off <= 0 and step_done <= 0:
        _cell = "only_delivery"
    elif step_off > 0 and step_done <= 0:
        _cell = "offered_not_done"
        if blk > 0:
            acc["offered_not_done_blocked"] = (
                int(acc.get("offered_not_done_blocked", 0)) + 1)
    elif step_off <= 0:
        _cell = "done_without_offer"
    else:
        _cell = "offered_and_done"
    acc[_cell] = int(acc.get(_cell, 0)) + 1
    return acc


def _koff_line(acc: dict) -> dict:
    """落盘前的**唯一**派生量：残差 `unaccounted`。

    `unaccounted = offered - dispatched - blocked - unreached`

    ⛔ 它是**检测器**，不是"其它"垃圾桶：三个已知出口之外只要还有候选消失，
       它就会 > 0，而"被跳过的那些候选去哪了"必须能归到某一条上。

    ⛔ 必须为 0 的三条理由（缺一条它就不该是 0）：
       ① `skipped`（没额度）与 `repeats`（重复动作）与 `vetoed`（预演否决）
          在这条流水线里都能发生 —— 真发生了，它就是正数，
          **不许**为了好看去钳成 0（§104.2 第 3 条：读数要显形）。
       ② 所以本轮的真实断言只有一条：**它现在应当是 0，且如果不为 0 就必须有人解释**。
    """
    out = dict(acc)
    out["unaccounted"] = (int(out.get("offered", 0)) - int(out.get("dispatched", 0))
                          - int(out.get("blocked", 0)) - int(out.get("unreached", 0)))
    # ★ V58（§124）：**分项**的检测器。与 `unaccounted` 同一思路、不同分母：
    #   `unaccounted` 问「三个大出口之外还有没有候选消失」，
    #   这一条问「`unreached` 的分项有没有漏一个出口」。
    # ⛔ 必须为 0：加进第四个出口却忘了在 `_note_unreached` 里登记，
    #   就会在这里显形，而不是让分项和长期小于总数（V56 `by_verdict` 的老病）。
    _bs_sum = sum(int(v or 0) for v in (out.get("unreached_by_stop") or {}).values())
    out["unreached_stop_gap"] = int(out.get("unreached", 0)) - _bs_sum
    return out


def _kord_zero() -> dict:
    """**顺序**读数的累加器零点（V58，§124）。

    ## 为什么需要它

    V57 的账本里有 `unreached=8`，但**没有任何一格**记录这 8 个在同轮候选里
    排在第几个、在什么之后。于是 V57 那句「8 个死在 `finish` 成功之后」
    在账本上是一次**手推**：它需要读者相信"交付动作总排在第一个"。
    本轮把这件事变成两个可数的量：

    | 量 | 分母 | 回答 |
    |---|---|---|
    | `delivery_first` / `delivery_last` / `pos_hist` | **轮** | 交付动作排第几 |
    | `tail_total` / `tail_by_tool` | **候选** | 它后面还剩几个、是什么 |
    | `tail_survived` | **候选** | 尾里**没有**被「交付成功就 return」丢掉的 |

    ⛔ `tail_survived` 不是"浪费"的补集：尾里被 guard 拦下、被跳过、
    被真的执行到，都会落进它。"没被丢"与"被执行"是两件事 ——
    这正是 V57 那个 `■` 行里两个分母的教训。

    ## 与 `_koff` 的分工（不许合并）

    `_koff` 的主分母是**题**（`offered` 已按题累加），`_kord` 的主分母是**轮**。
    合成一个字典就会出现"轮数被当成题数"的读数 —— 而两者相差 18 vs 12。
    """
    return {
        "tasks": 0,            # 题数（与 `_koff.tasks` 必须相等）
        "rounds": 0,           # 轮数（与 `_koff.rounds` 必须相等）
        "with_delivery": 0,    # 有**交付候选**的轮数
        "delivery_first": 0,   # 交付候选在**第 0 位**的轮数
        "delivery_last": 0,    # 交付候选在**最后一位**的轮数（`n=1` 时两者同时成立）
        "pos_hist": {},        # 下标 -> 轮数（字符串键，JSON 稳定）
        "tail_total": 0,       # 交付候选**之后**的候选总数
        "tail_by_tool": {},    # 那些候选是什么工具
        #: 尾里**没有**死在「交付动作成功 ⇒ `return`」上的候选数。
        #: ⛔ 结构上 `0 <= tail_survived <= tail_total`：前者越界说明两个来源
        #:    （plan 的位置 / offer 的出口）至少有一个算错，红比好看重要。
        "tail_survived": 0,
        #: 尾比"被交付成功丢掉的"还少的题数（两个来源互相矛盾的检测器）。
        #: ⛔ 必须为 0；非 0 时 `tail_survived` 整列不可信（正负互相抵消）。
        "tail_negative_tasks": 0,
        #: 两个平行列表**长度不等**的轮数。⛔ 它是装置检测器，不是业务量：
        #: `delivery_pos_by_round` 与 `per_round` 靠同一个 append 对齐，
        #: 一旦有一处漏 append，按下标对读会**静默错位**（读到别的轮）。
        #: 必须为 0；不为 0 时 `pos_hist` 整张表都不可信。
        "rounds_unpaired": 0,
    }


def _kord_absorb(acc: dict, out) -> dict:
    """把**一道题**的顺序读数并进累加器（就地修改并返回）。

    ⚠️ 两个来源都在 `out.stats` 里，各有唯一的声明处：
        `plan.delivery_pos_by_round` / `plan.tail_tools_by_round`（位置与尾）
        `offer.unreached_by_stop["answer"]`（尾里**真的**被丢了多少）
    ⛔ 不在这里重新判断"哪个是交付动作"：谓词在内核 `_note_plan_round` 里
       只声明一处（`c.tool == "finish" or spec.produces_answer`）。抄一份过来，
       两份迟早对不上，而这两个数在判据里是要相减的。
    """
    st = getattr(out, "stats", None) or {}
    pl = st.get("plan") or {}
    of = st.get("offer") or {}
    pr = pl.get("per_round") or []
    pos = pl.get("delivery_pos_by_round") or []
    tails = pl.get("tail_tools_by_round") or []

    acc["tasks"] = int(acc.get("tasks", 0)) + 1
    acc["rounds"] = int(acc.get("rounds", 0)) + len(pr)
    acc["rounds_unpaired"] = int(acc.get("rounds_unpaired", 0)) + (
        1 if len(pos) != len(pr) or len(tails) != len(pr) else 0)

    _tail_task = 0
    for i, r in enumerate(pr):
        # ⚠️ 越界就是装置问题（上面那格已经记账），这里**跳过**而不是
        #    猜一个位置：猜出来的 `pos` 会让 `pos_hist` 整张表带毒。
        if i >= len(pos) or i >= len(tails):
            continue
        p = int(pos[i])
        if p < 0:
            continue
        n = int(r.get("n") or 0)
        acc["with_delivery"] = int(acc.get("with_delivery", 0)) + 1
        if p == 0:
            acc["delivery_first"] = int(acc.get("delivery_first", 0)) + 1
        if p == n - 1:
            acc["delivery_last"] = int(acc.get("delivery_last", 0)) + 1
        _ph = acc["pos_hist"]
        _pk = str(p)
        _ph[_pk] = int(_ph.get(_pk, 0)) + 1
        _tl = list(tails[i])
        acc["tail_total"] = int(acc.get("tail_total", 0)) + len(_tl)
        _tail_task += len(_tl)
        _bt = acc["tail_by_tool"]
        for t in _tl:
            _bt[t] = int(_bt.get(t, 0)) + 1

    #: 尾里**没被**「交付成功就 return」丢掉的候选数。
    #: ⚠️ 只在**这一题**内相减（两边的分母都是"这一题"）：跨题相减会让
    #:    一题的负值与另一题的正值互相抵消 —— 而负值本身就是装置故障。
    _dropped_by_answer = int((of.get("unreached_by_stop") or {}).get("answer") or 0)
    _surv = _tail_task - _dropped_by_answer
    acc["tail_survived"] = int(acc.get("tail_survived", 0)) + _surv
    #: 这一题的尾**比**"被交付成功丢掉的"还少 —— 两个来源（plan 的位置 /
    #: offer 的出口）互相矛盾。⛔ 必须为 0；它一旦非 0，`tail_survived`
    #: 的正值就是另一题的负值抵消出来的，整列不可信（不许让它悄悄抵消）。
    if _surv < 0:
        acc["tail_negative_tasks"] = int(acc.get("tail_negative_tasks", 0)) + 1
    return acc


def _kshell_zero() -> dict:
    """**验证动作**读数的累加器零点（V60，§126）。

    ## 为什么必须有这一行

    V59 的结论里有一句没被检验的话：那 4 格 `run_shell` 是**这套题上唯一的验证动作**，
    被 `--allow-shell` 拦掉了。要检验它，至少得能回答三件事，而现有三个账本
    （`kernel_exec` / `kernel_offer` / `kernel_order`）**一件都答不了**：

    | 问题 | 为什么答不了 |
    |---|---|
    | 打开 shell 后跑没跑 | `exec.dispatched` 是**全部工具**的和 |
    | 跑完结果说代码对不对 | `exec.ok=True` 对 `rc=1` 也成立（工具调用成功 ≠ 命令成功） |
    | 失败之后又做了什么 | 那是**顺序**事件，任何计数器都不带序 |

    ## 分母（三个，不许合成一个）

    · `tasks` / `tasks_with_shell` —— **题**
    · `rounds_with_shell` / `shell_first_rounds` —— **轮**（计划侧："提了"）
    · `dispatched` / `rc_*` —— **次**（执行侧："做了"）

    ⛔ `tasks_with_shell` 与 `dispatched` 相差多少，就是"一题跑了几次" ——
    合成一个数就再也分不开"12 题各跑一次"与"1 题跑了 12 次"。
    """
    return {
        "tasks": 0,
        #: 至少**执行过**一次 `run_shell` 的题数（不是"提过"）。
        "tasks_with_shell": 0,
        #: 计划里出现过 `run_shell` 的候选数（= `plan.by_tool["run_shell"]` 的另一处声明）。
        #: ⚠️ 与 `dispatched` 的差就是"提了没做"（被拦 / 未轮到 / 重复跳过）。
        "offered": 0,
        "dispatched": 0,
        "rc_zero": 0,
        "rc_nonzero": 0,
        #: 调用成功但输出里没有 `rc=` 行。⛔ **必须单独一格**：
        #: 并进 `rc_zero` 就等于"缺席冒充零"——那样一次都没真跑会读成"全跑通了"。
        "rc_unknown": 0,
        "err": 0,                 # 调用本身失败（缺参 / 异常 / 未知工具）
        "blocked": 0,             # 被 guard 拦下的 `run_shell` 候选
        "blocked_by_checkpoint": {},  # 哪道闸拦的（`tool_misuse` / `goal_hijack` …）
        "rounds_with_shell": 0,
        "shell_first_rounds": 0,
        "after_fail_retry": 0,    # 上一条 shell 非 0 之后又跑了一次
        "after_fail_answer": 0,   # 上一条 shell 非 0 之后交付了答案
        #: ★ V61（§127）：**命令原文**（`sha8:单行前 120 字`），每题最多 40 条。
        #: ⚠️ 它是**顺序**证据且带载荷 —— V60 那轮 4 条命令一条都不可审计，
        #:    只能从 `ws/` 残留反推（反推出来的是一条 `echo` 写文件的残次品）。
        #: ⛔ 与 `trace` 并列不合并：`trace` 的格式被 `AZ122` 钉着。
        "trace_cmd": [],
        # ⛔ **不在这里放 `rc_gap`**：它是派生量，只出现在 `_kshell_line` 的落盘行。
        #   V58 已经把这条踩过一遍（把 `unreached_stop_gap` 写进零点键集 ⇒
        #   契约里 16 比 17 当场红）—— 这里是同一个坑的第二次。
    }


def _kshell_absorb(acc: dict, out) -> dict:
    """把**一道题**的验证读数并进累加器（就地修改并返回）。

    ⚠️ 与 `_kexec_absorb` / `_koff_absorb` 同一条理由必须**两处调用点各接一次**：
        漏一处 = 静默少计一题的验证动作（V55/V57/V58 都在这里踩过）。
    ⛔ 不在这里重新判断"哪个是验证动作"：`cand.tool == "run_shell"` 只在内核里
        声明（`_note_shell` 的唯一调用点）；抄一份过来，两个数迟早对不上。
    """
    st = getattr(out, "stats", None) or {}
    sh = st.get("shell") or {}
    pl = st.get("plan") or {}

    #: `offered` **从 plan 侧取**（`by_tool` 是候选层的事实），不抄 `shell.offered`：
    #: 同一个数在内核与账本各存一份，迟早分叉（§122 的 `by_verdict` 同因）。
    _off = int((pl.get("by_tool") or {}).get("run_shell") or 0)
    _disp = int(sh.get("dispatched") or 0)

    acc["tasks"] = int(acc.get("tasks", 0)) + 1
    acc["offered"] = int(acc.get("offered", 0)) + _off
    if _disp > 0:
        acc["tasks_with_shell"] = int(acc.get("tasks_with_shell", 0)) + 1
    for _k in ("dispatched", "rc_zero", "rc_nonzero", "rc_unknown", "err",
               "blocked", "rounds_with_shell", "shell_first_rounds",
               "after_fail_retry", "after_fail_answer"):
        acc[_k] = int(acc.get(_k, 0)) + int(sh.get(_k) or 0)
    _bc = acc["blocked_by_checkpoint"]
    for _cp, _n in (sh.get("blocked_by_checkpoint") or {}).items():
        _bc[_cp] = int(_bc.get(_cp, 0)) + int(_n or 0)
    #: ★ V61（§127）：命令原文按题**顺序拼接**。⛔ 不能只搬条数：
    #:   条数已经有 `dispatched`，这一格存在的唯一理由是**看得见内容**。
    #:   ⚠️ 每题自己的 `r{n}` 会重号 ⇒ 前置题序号，否则跨题对不上"哪一条是第几轮"。
    _tc = acc.setdefault("trace_cmd", [])
    for _line in (sh.get("trace_cmd") or []):
        if len(_tc) >= 80:
            break
        _tc.append(f"[{acc['tasks']}]{_line}")
    return acc


def _kshell_line(acc: dict) -> dict:
    """落盘前的**唯一**派生量：`rc_gap`。

    `rc_gap = dispatched - (rc_zero + rc_nonzero + rc_unknown + err)`

    ⛔ 它是**检测器**，不是"其它"垃圾桶：四格之外只要还有一次下发没被归类，
       它就会 ≠ 0，而"这一次去哪了"必须能归到某一条上。
    ⚠️ 它只在**落盘那一行**出现，**不进零点**（V58 的 `unreached_stop_gap` 同例：
       派生量写进零点键集，契约里 16 比 17 当场红）。
    """
    out = dict(acc)
    out["rc_gap"] = (int(out.get("dispatched", 0))
                     - int(out.get("rc_zero", 0)) - int(out.get("rc_nonzero", 0))
                     - int(out.get("rc_unknown", 0)) - int(out.get("err", 0)))
    return out


def _kvf_zero() -> dict:
    """**交付闸**读数的累加器零点（V61，§127）。

    ## 为什么必须有这一格

    V60 的结论是「验证只落在**已经做对**的题上」。要检验"把它挪进闸里会不会不同"，
    至少得能回答三件事，而 `_kshell` 一件都答不了：

    | 问题 | 为什么答不了 |
    |---|---|
    | 闸**跑没跑**示例判据 | `_kshell` 只数 `run_shell`，那是**模型**发起的 |
    | 跑了**几次**、拒了几次 | `verify_stats` 只活在进程里（`run.py` 里 0 处引用） |
    | 拒的是**哪一类**判据 | `verify_stats.failed` 是全部判据的和 |

    ## 分母（三层，不许合成一个 —— 而且**单位必须进字段名**）

    · `tasks` —— **题-run**（每题每 rep 计一次 ⇒ `--repeat R` 时是 R 倍）
    · `example_task_runs` —— **题-run**（题面抽到示例的 ⇒ 判据的**适用面**，同样 R 倍）
    · `runs` / `passed` / `failed` / `exhausted` —— **次**（闸被调用）
    · `example_runs` / `example_failed` —— **次**（示例判据真跑 / 被它拒）

    ★ V63（§129）：`example_tasks` → `example_task_runs`，`tasks_rejected` →
      `task_runs_rejected`，内核侧 `example_tasks` → `has_example`（0/1 标记）。
      理由是**同一个坑押错三次**：
        · V61 P1 押 `example_tasks=4`（读成"题"）实测 8 = 4 题 × 2 rep；
        · V62 P9 押 `tasks_rejected=1`（读成"题"）实测 2 = 1 题 × 2 rep；
        · 纪律 30 早就写了"修法只能是字段名带单位"，而字段名一直没真带。
      ⇒ `tasks=12`（6 题 × 2 rep）**是对的**，只是名字让它像"12 题"。
      ⛔ 不改 `tasks` 的名字：它在 7 个 kernel 字典里同名同义，单改一处会造出
         "同名不同义"——比"名字含糊"更难查。改为在 docstring 里钉住乘数关系。

    ⛔ `example_task_runs=0` 不是"通过了"，是**这条判据不适用** ——
    把它读成通过，"判据缺席"就会冒充"全绿"（缺席冒充零的第 N 种形态）。
    """
    return {
        #: ⚠️ **题-run**，不是题：每题每 rep 计一次（`--repeat R` ⇒ R 倍）。
        "tasks": 0,
        #: 题面里抽到示例的**题-run**数（= 这条判据的适用面，同样 R 倍）。
        "example_task_runs": 0,
        "runs": 0,
        "passed": 0,
        "failed": 0,
        #: 打回超上限、如实交付的**次数** —— 这是"强制验证"的代价面。
        "exhausted": 0,
        "example_runs": 0,
        "example_failed": 0,
        #: **装置自曝**：示例执行超时 / 子进程起不来。
        #: ⛔ 并进 `example_failed` 就是把装置的锅记到被测对象头上（V60 同宗）。
        "exec_err": 0,
        # ⛔ **不在这里放 `verify_gap`**：派生量只在 `_kvf_line` 出现。
        #   V58（`unreached_stop_gap`）与 V60（`rc_gap`）已经各踩过一次。
    }


def _kvf_absorb(acc: dict, out) -> dict:
    """把**一道题**的闸读数并进累加器（就地修改并返回）。

    ⚠️ 与 `_kshell_absorb` 同一条理由必须**两处调用点各接一次**：
        漏一处 = 静默少计一题的闸（V55/V57/V58/V60 都在这里踩过）。
    ⛔ 不在这里重新判断"哪条判据是示例判据"：那由 `_checks` 里 `kind=="example"` 定，
        而它只在 `Core._reset_verify` 声明（抄一份过来迟早对不上）。
    """
    st = getattr(out, "stats", None) or {}
    vf = st.get("verify") or {}
    acc["tasks"] = int(acc.get("tasks", 0)) + 1
    #: ⚠️ `example_task_runs` 取**内核**那一格（`_reset_verify` 由 `_checks` 派生），
    #:    不在这里另算一次 `example_asserts(goal)`：同一个事实两处推导必然分叉。
    #: ★ V63（§129）：内核键名是 `has_example`（0/1 标记）—— 它回答"**本题**有没有"，
    #:    累加出来才是"题-run 数"。⛔ 两侧键名必须同时改：只改一边 ⇒ 这里永远读到
    #:    `None` ⇒ `example_task_runs=0` 静默冒充"这条判据不适用"（缺席冒充零）。
    acc["example_task_runs"] = int(acc.get("example_task_runs", 0)) + (
        1 if int(vf.get("has_example") or 0) else 0)
    for _k in ("runs", "passed", "failed", "exhausted",
               "example_runs", "example_failed", "exec_err"):
        acc[_k] = int(acc.get(_k, 0)) + int(vf.get(_k) or 0)
    return acc


def _kvf_line(acc: dict) -> dict:
    """落盘前的**唯一**派生量：`verify_gap`。

    `verify_gap = runs - (passed + failed)`

    ⛔ 它是**检测器**，不是"其它"垃圾桶：闸每跑一次，结果必须落进 `passed` 或
       `failed` 之一；只要还有一次没被归类，它就 ≠ 0。
    ⚠️ 它只在**落盘那一行**出现，**不进零点**（V58/V60 同例）。
    """
    out = dict(acc)
    out["verify_gap"] = (int(out.get("runs", 0))
                         - int(out.get("passed", 0)) - int(out.get("failed", 0)))
    return out


def _krw_zero() -> dict:
    """**打回 → 重写**这条边的读数零点（V62，§128）。

    ## 为什么 `_kvf` 不够

    V61 的判决是「强制验证治好了 V60 的病，Δ 却仍然 = 0」。从 `_kvf` 能读出的只有
    `runs=14 / failed=4` —— 它证明闸在拒，**证明不了"拒完之后发生了什么"**：

    | 问题 | `_kvf` 能不能答 |
    |---|---|
    | 报错原文送达模型了吗 | ⛔ `failed+1` 只说明闸返回了一段文本，不说明它进了 prompt |
    | 拒完之后**又交了一次**吗 | ⛔ `runs` 含**第一次**交付，混在一起就看不见 |
    | 那一次和被打回那版相同吗 | ⛔ 只有一个"失败"计数 |

    ⇒ 于是 V61 的 `exhausted=0`（配额一次没用满）只能靠人工翻日志才发现。
    这一格把"打回之后"那一段直接变成可查询的数。

    ## 分母（全部是**次**，不是题）

    · `carried`     —— 被打回的报错原文**真的进了下一次提问**（装置通不通）
    · `redelivered` —— 打回之后**又交了一次**（这条边存不存在）
    · `same` / `diff` —— 那一次与被打回那版**逐字**相同 / 不同（模型改没改）
    · `passed_after` —— 那一次**过闸**（兑现率）⇒ 它是 `diff` 的**子集**，
      因为"逐字相同"的答案不可能过同一套判据。

    ⛔ `redelivered` 不许与 `verify.runs` 合并：后者的分母含**第一次**交付。
    ⛔ `redelivered = same + diff` 是**恒等式**（每次重交恰好落一格）
      ⇒ 落盘行里的 `rework_gap` 就是它的检测器。
    ⚠️ 第一版把 `redelivered` 记在"又被打回"分支里 ⇒ 一份**改对了**的重写会让
      它少 1，而 `passed_after` 多 1 —— 检测器于是把"改对了"报成"漏计"。
      这就是"检测器自己失效"（"红=无"第三形态），冒烟时当场抓到。
    """
    return {
        "tasks": 0,
        "carried": 0,
        "redelivered": 0,
        "same": 0,
        "diff": 0,
        "passed_after": 0,
        #: 逐字重提被重复闸吃掉的次数 —— 它就是 V61 `unaccounted` 残差的那一类出口。
        "dup_skipped": 0,
        #: 打回过**至少一次**的**题-run**数 —— 上面五项的分母（`carried`/`redelivered`
        #: 都是次，两者之比才有意义）。
        #: ⚠️ 没有它，"carried=0" 会被读成"载荷送不到"，而真相可能是"这题没被打回"。
        #: ★ V63（§129）：原名 `tasks_rejected` ⇒ 被读成"1 道题"（V62 P9 押 1 实测 2）。
        #:    它按 absorb 计次 ⇒ `--repeat 2` 时同一道题记 2（同 `tasks` 的乘数）。
        "task_runs_rejected": 0,
        # ⛔ **不在这里放 `rework_gap`**：派生量只在 `_krw_line` 出现（V58/V60 各踩一次）。
    }


def _krw_absorb(acc: dict, out) -> dict:
    """把**一道题**的 rework 读数并进累加器（就地修改并返回）。

    ⚠️ 与 `_kvf_absorb` 同一条理由必须**两处调用点各接一次**：
        漏一处 = 静默少计一题的这条边（V55/V57/V58/V60/V61 都在这里踩过）。
    ⛔ `task_runs_rejected` 取内核的 `verify.failed > 0`，**不在这里**另判一次：
        同一个事实两处推导必然分叉（`_kvf_absorb` 的 `example_task_runs` 同因）。
    """
    st = getattr(out, "stats", None) or {}
    rw = st.get("rework") or {}
    vf = st.get("verify") or {}
    acc["tasks"] = int(acc.get("tasks", 0)) + 1
    if int(vf.get("failed") or 0) > 0:
        acc["task_runs_rejected"] = int(acc.get("task_runs_rejected", 0)) + 1
    for _k in ("carried", "redelivered", "same", "diff", "passed_after",
               "dup_skipped"):
        acc[_k] = int(acc.get(_k, 0)) + int(rw.get(_k) or 0)
    return acc


def _krw_line(acc: dict) -> dict:
    """落盘前的**唯一**派生量：`rework_gap`。

    `rework_gap = redelivered - (same + diff)`

    ⛔ 它是**检测器**，不是"其它"垃圾桶：每发生一次"打回之后的交付"，它必然落在
       `same` 或 `diff` 之一；只要还有一次没被归类，它就 ≠ 0。
    ⚠️ 它只在**落盘那一行**出现，**不进零点**（V58/V60/V61 同例）。
    """
    out = dict(acc)
    out["rework_gap"] = (int(out.get("redelivered", 0))
                         - int(out.get("same", 0)) - int(out.get("diff", 0)))
    return out


def _ktx_zero() -> dict:
    """传输层 / 墙钟读数的累加器零点（第二十五轮，DESIGN §16.24）。

    ⛔ 为什么要落盘：`stats["transport"]` 只在**内存里**。而"这次修复有没有
    误伤正常路径"（P3：正常端点上 `wall_refused_*` 恒为 0）是一个**必须被
    事后审计**的事实 —— 只留在运行时日志里，就得靠我回忆，那等于没测。
    与 `kernel_closeout` / `kernel_external` 同源同理由。

    ⚠️ 五个计数必须分列，不能合成一个"重试次数"：
      · `retries` / `exhausted` = **端点的锅**（抖动 / 一直失败）⇒ 修端点
      · `wall_shortened` = **预算紧**（本次尝试的上限被裁短，但发出去了）⇒ 减工作量/升档
      · `wall_refused_first` / `wall_refused_retry` = **预算尽**（根本没发/没重试）
    合成一个数字，就等于把三种修法混成一种。
    """
    return {"tasks": 0, "retries": 0, "exhausted": 0,
            "wall_shortened": 0, "wall_refused_first": 0,
            "wall_refused_retry": 0}


def _ktx_absorb(acc: dict, stats: dict) -> dict:
    """把一次内核运行的传输层读数并进累加器（就地修改并返回）。

    ⚠️ `tasks` 在**这里**加，不由调用方另写一句 `_ktx["tasks"] += 1`：
       调用点已经有两处（每题新建内核 / 复用内核），两处各写一遍就是
       "同一事实声明两处"，漏一处即静默少计一次。
    """
    for k in ("retries", "exhausted", "wall_shortened",
              "wall_refused_first", "wall_refused_retry"):
        acc[k] = int(acc.get(k, 0)) + int(stats.get(k, 0) or 0)
    acc["tasks"] = int(acc.get("tasks", 0)) + 1
    return acc


def _kwm_now(stats: dict, core: object = None) -> dict:
    """把「世界模型 + 层读数」取成一张**累计快照**（只含单调递增的计数）。

    ⛔ 为什么需要快照（V37 §103.4② 的真 bug）：`--memory-dir` 臂（累积臂）
       **复用同一个 `Core`**（`run.py:1246`），而 `WorldModel` 与
       `core.memory_layer_view` 都活在整个 bench 进程里 ⇒ 它们报的是**跨题累计值**。
       把累计值当增量相加，等于「同一个计数器被读几次就加几遍」：
       V37 实测 6 题逐题 `预演=2,3,5,6,8,10`（这就是累计值）
       ⇒ 落盘写的是 `2+3+5+6+8+10 = 34`，真值只有 **10**（proxy 200 型 10 条、
       `preds` 逐条 10 条两个独立来源都对得上）。

    ⚠️ 层读数（`layer_recalled` / `layer_abstract`）住在 `Core` 上、不在
       `stats()` 里，但**同样是 `+=` 累计**（`core.py:1555`）⇒ 必须一起做差分，
       否则它俩会继续重复累加（同一个病，两个病灶）。
    """
    cur = {k: int(stats.get(k, 0) or 0) for k in _KWM_NUM_KEYS}
    if core is not None:
        view = getattr(core, "memory_layer_view", None) or {}
        cur["layer_recalled"] = int(view.get("recalled", 0) or 0)
        cur["layer_abstract"] = int(view.get("abstract", 0) or 0)
    else:
        cur["layer_recalled"] = 0
        cur["layer_abstract"] = 0
    cm = stats.get("confusion") or {}
    cur["confusion"] = {k: int(cm.get(k, 0) or 0)
                        for k in ("ok_ok", "ok_fail", "fail_ok", "fail_fail")}
    return cur


def _kwm_delta(now: dict, prev: dict | None) -> dict:
    """`now - prev`；`prev is None`（第一次读）⇒ 全量。

    ⚠️ 不做 `max(0, …)` 钳位：所有计数在 `mw/worldmodel.py` 里都是纯 `+=`
       （`predictions`/`scored`/`hits` 三处，无清零）⇒ 负数只可能意味着
       「有人加了会清零的计数器」。**读数要显形，不许被钳位藏掉。**
    """
    if prev is None:
        return now
    out = {k: int(now.get(k, 0) or 0) - int(prev.get(k, 0) or 0)
           for k in _KWM_NUM_KEYS}
    out["confusion"] = {k: int(now["confusion"].get(k, 0) or 0)
                        - int((prev.get("confusion") or {}).get(k, 0) or 0)
                        for k in now["confusion"]}
    return out


def _kwm_absorb(acc: dict, stats: dict, core: object = None) -> dict:
    """把一次内核运行的读数并进累加器（就地修改并返回）。

    ⚠️ 并进去的是**增量**，不是 `stats()` 的原始值 —— 见 `_kwm_now` 的注释。
    ⚠️ `hit_rate` **不并** —— 它是比值，逐次平均会给出错的答案
    （100 次全对 + 1 次全错，平均 0.5；真值是 100/101）。只能由总数重算。
    ⚠️ 缺失的键按 0 并，**不炸**：老读数文件里没有 `scored` 是正常的，
    但"没有这个字段" ≠ "这个字段是 0" —— 所以 `hit_rate` 在 `scored==0` 时
    返回 `None`，让"没量到"在读数里显形。
    """
    now = _kwm_now(stats, core)
    #: 基线挂在**产生这些计数的对象**上（而不是挂在 `acc` 里）：
    #:   · `--fresh` 每题新建 Core ⇒ 基线天然不存在 ⇒ 第一次即全量；
    #:   · 累积臂复用同一个 Core ⇒ 基线就是上一次的快照 ⇒ 差分为本题增量。
    #: ⛔ 不许把基线塞进 `acc`：`_kwm_line` 会对每个非 `confusion` 的键做 `int(v)`，
    #:    一个 dict 会把落盘打断（这正是一个"顺手放一下"会炸掉的地方）。
    prev = getattr(core, "_kwm_seen", None) if core is not None else None
    delta = _kwm_delta(now, prev)
    if core is not None:
        setattr(core, "_kwm_seen", now)
    for k in _KWM_NUM_KEYS:
        acc[k] = int(acc.get(k, 0)) + int(delta.get(k, 0) or 0)
    cm = acc.setdefault("confusion", {})
    for k in ("ok_ok", "ok_fail", "fail_ok", "fail_fail"):
        cm[k] = int(cm.get(k, 0)) + int((delta.get("confusion") or {}).get(k, 0) or 0)
    return acc


def _kwm_line(acc: dict) -> dict:
    """产出落盘/打印用的读数。派生量**只在这里算一次**（同一事实只声明一处）。"""
    d = {k: int(v) for k, v in acc.items() if k != "confusion"}
    d["confusion"] = {k: int(v) for k, v in (acc.get("confusion") or {}).items()}
    d["hit_rate"] = (round(d["hits"] / d["scored"], 3) if d["scored"] else None)
    return d


def cmd_selftest(args) -> int:
    """离线端到端。返回进程码：0 = 内核按预期工作。"""
    print("=" * 74)
    print("V1 离线自检 —— 主循环端到端（零网络 / 零密钥 / 零第三方依赖）")
    print("=" * 74)
    s = cfg.settings()
    demo = DemoLLM()
    core = Core(s, llm=demo)
    goal = args.goal or DemoLLM.GOAL
    out = core.run(goal, budget_factor=args.budget)

    print()
    print("--- 读数 ---")
    print(f"  状态      : {out.status}")
    print(f"  答案      : {out.answer[:100]}")
    print(f"  预算      : {out.stats['budget']}")
    print(f"  守卫拦截  : {out.stats['guard']}")
    print(f"  世界模型  : {out.stats['worldmodel']}")
    print(f"  规划器    : {out.stats['planner']}")
    print(f"  记忆      : {out.stats['memory']}")
    print(f"  重复行    : {core.memory.duplicate_rows()}  (去重约束的自证)")

    ok = (out.status == "done"
          and out.stats["guard"]["blocks"]["tool_misuse"] >= 1
          and core.memory.duplicate_rows() == 0)
    print()
    print(f"自检结论: {'✅ 通过' if ok else '❌ 不符合预期'}")
    return 0 if ok else 1


def cmd_run(args) -> int:
    core = _build(args.goal, args, allow_shell=args.allow_shell)
    out = core.run(args.goal, budget_factor=args.budget)
    dv = (out.stats or {}).get("delivery") or {}
    # ★ §110：给用户看的**第一行**是 verdict，不是 status。
    # 第一百三十三轮实测：`status=done` 而 `report.md` 不存在、`status=stalled`
    # 而产物三行全对 —— 用户唯一能看的那个词两个方向都在撒谎。
    # ⛔ 两个都印，但顺序写死：结果量在前、过程量在后。
    print(f"■ 验收 {out.verdict or '?'}"
          + (f"（产物 {len(dv.get('found') or [])}/{len(dv.get('outputs') or [])}"
             + (f"，缺 {','.join(dv.get('missing') or [])}" if dv.get("missing") else "")
             + "）" if dv.get("outputs") else "（题面未约定产物，仅验了答案非空）")
          + f" ｜ 过程 status={out.status}")
    if dv.get("inputs_missing"):
        print(f"⚠️ 题面点名的输入文件不存在：{','.join(dv['inputs_missing'])}")
    print(json.dumps({"status": out.status, "verdict": out.verdict,
                      "answer": out.answer,
                      "reason": out.reason, "iterations": out.iterations,
                      "delivery": dv,
                      "stats": out.stats}, ensure_ascii=False, indent=2))
    # 退出码由**结果量**决定：只有"东西在"或"交了文本"才算成功。
    return 0 if out.verdict in ("PASS", "ANSWER") else 1


def cmd_measure(args) -> int:
    """测量模式。**这是 V1 的核心价值所在**，不是附庸。"""
    print("=" * 74)
    print(f"测量模式：同一目标重复 {args.repeat} 次（预算档 {args.budget:g}x）")
    print("=" * 74)
    rows: list[Measurement] = []

    for b in [float(x) for x in str(args.sweep or args.budget).split(",")]:
        tag = f"{b:g}x"
        scores: list[float] = []

        def sample(_b=b) -> float:
            core = _build(args.goal, args, allow_shell=args.allow_shell)
            out = core.run(args.goal, budget_factor=_b)
            return 1.0 if out.status == "done" else 0.0

        scores = repeat(sample, n=args.repeat)
        m = summarize(scores, candidate="selftest", budget_tag=tag)
        rows.append(m)
        print(f"  {m.as_line()}")

    print()
    print("--- 预算曲线（2026：不要用一个数描述一个候选） ---")
    print(budget_curve(rows))
    print()
    print(f"--- 噪声下限（保守口径 = 最大极差）: {noise_floor(*rows):.4f} ---")
    if len(rows) >= 2:
        ok, why = paired_compare(rows[0], rows[-1])
        print(f"  {rows[0].budget_tag} vs {rows[-1].budget_tag}: {'变好' if ok else '不成立'} — {why}")
    print()
    print("说明：离线桩是确定性的 ⇒ 极差应为 0.0000。")
    print("      这本身是有用读数：它证明测量管线**不引入**噪声。")
    print("      真实候选的噪声必须用同样方式在真模型上测出来，不许假设。")
    return 0


def _noise_report(rep, suite, m, repeat_n: int) -> list[str]:
    """逐题 × 逐次 的分数矩阵 —— 噪声下限实验的核心输出。

    为什么必须**逐题**看，不能只看总分：
        两次总分相同，可能是一题从 0 涨到 1、另一题从 1 跌到 0 相互抵消。
        只盯总分，这种抖动会被误读成"稳定"。极差必须按分项量，
        但没人看过它是**哪些题**在抖 —— 于是既不知道噪声从哪来，也无法判断
        「某个改进」是否大过了噪声。
    """
    if repeat_n < 2:
        return []
    cell: dict[tuple[str, int], float] = {}
    for row in rep.per_task:
        cell[(row["id"], row["rep"])] = row["score"]

    ids = [t.id for t in suite.tasks]
    out = ["", "--- 噪声明细（逐题 × 逐次）---"]
    out.append("  " + "任务".ljust(20) + "".join(f"r{r+1}".rjust(8) for r in range(repeat_n)) + "   极差")
    unstable: list[str] = []
    for tid in ids:
        vals = [cell.get((tid, r + 1)) for r in range(repeat_n)]
        shown = "".join(("—" if v is None else f"{v:.4f}").rjust(8) for v in vals)
        known = [v for v in vals if v is not None]
        spread = (max(known) - min(known)) if len(known) >= 2 else 0.0
        if spread > 1e-9:
            unstable.append(tid)
        out.append(f"  {tid.ljust(20)}{shown}   {spread:.4f}" + ("  ⚠️" if spread > 1e-9 else ""))

    spread_all = m.hi - m.lo
    out.append("")
    out.append(f"  各次总分极差（噪声下限）= {spread_all:.4f}")
    out.append(f"  逐题不稳定的任务 = {len(unstable)}/{len(ids)}"
               + (f"  {unstable}" if unstable else ""))
    if spread_all <= 1e-9 and not unstable:
        out.append("  ⇒ 逐题逐次**完全一致** ⇒ 该温度下链路是确定的，噪声下限 = 0")
    else:
        out.append(f"  ⚠️ 抖动存在 ⇒ **任何 ≤ {spread_all:.4f} 的『改进』都不可判定**")
        out.append("     （失败正发生在这里：极差常常大于它声称的一切改进）")
    return out


# ===========================================================================
# 跨领域跑法 —— 「通用」的可判定形式
# ===========================================================================
def _count_lines(path: Path) -> int:
    try:
        return len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])
    except OSError:
        return 0


def _read_run_after(path: Path, before: int) -> dict | None:
    """读「本轮新追加的那一行」读数。

    为什么要按行号而不是「读最后一行」：cmd_bench 有可能在写盘**之前**就 return
    （血缘闸拒绝出分、尺子不合格……）。那时最后一行是**上一轮**的，
    读它会得到一个"看起来正常"的读数 —— 正是本项目最忌讳的假绿。
    """
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return None
    if len(lines) <= before:
        return None
    try:
        return json.loads(lines[before])
    except json.JSONDecodeError:
        return None


def _domain_row(name: str, rc: int, rec: dict | None) -> dict:
    if rec is None:
        return {"domain": name, "n": 0, "mean": 0.0, "trust": False,
                "llm": "-", "note": f"无读数（rc={rc}）"}
    rep = rec.get("report") or {}
    a = int(rep.get("attempted", 0))
    r = int(rep.get("recorded", 0))
    sk = int(rep.get("skipped_no_answer", 0))
    fs = int(rep.get("failed_scorer", 0))
    trust = (a == r + sk + fs) and sk == 0 and fs == 0
    return {
        "domain": name,
        "n": len(rep.get("per_task") or []),
        "mean": float((rec.get("measurement") or {}).get("mean", 0.0)),
        "trust": trust,
        "llm": str(rep.get("llm_of_record", "-")),
        "note": "" if trust else f"skipped_no_answer={sk} failed_scorer={fs}",
    }


def _critique_snapshot(crit: "CritiqueLedger", scope: str,
                       dropped: set[str] | None = None) -> "CritiqueLedger | None":
    """构造「内核拿到的那份评审账本」（**纯函数**，无 I/O、不落盘）。

    为什么不把它留在 `cmd_bench` 里：证据探针要能**调用同一份实现**。
    自己抄一遍 4 行看起来更省事，但那 4 行正是被测对象 ——
    抄出来的探针会证明「抄写正确」，而不是「实现正确」（V31 自踩 ⑩ 的同宗）。

    - `off`        → `None`（内核不投喂，V19~V28 全部如此）
    - `prev`       → 只读快照副本，`allow_self=False`（同题被挡，旧口径）
    - `prev-self`  → 同上但 `allow_self=True`（跨轮自省，§96）
    - `all`        → **不在本函数处理**（调用方直接用真账本；轮内投喂 = 接线验证）
    - `dropped`    → 只在**副本**里跳过这些题（§98 的等长安慰剂：
                      丢一条 31 字的满分条目，`crit[-3:]` 的窗口就换成三条
                      140 字的真实失败行，块长与 `prev-self` 逐字节相同）
    """
    if scope == "all":
        return crit
    if scope not in ("prev", "prev-self"):
        return None
    #: ⚠️ 必须是一个**新**账本且不落盘：`path=None` ⇒ `save()` 返回 0，
    #:  绝不会把快照写回磁盘（`prev` 的隔离性就靠这一句）。
    snap = CritiqueLedger(None, allow_self=(scope == "prev-self"))
    for c in crit.by_key.values():
        if dropped and c.task_id in dropped:
            continue
        snap.add(c)
    return snap


def _option_conflict(args) -> str:
    """互斥/危险选项组合检查。返回说明（空串 = 无冲突）。

    ⚠️ 为什么这些必须**显式禁止**而不是"随便取一个赢"：
    它们各自都改变了"这次读数**测量的是什么**"，而组合起来的语义是**自相矛盾**的。
    静默让某一个赢，产出的就是一个**标签与事实不符**的读数 —— 那正是本项目
    第一条教训的原始形态（标签写"真模型"，实际跑桩）。

    另外这条检查本身是**纯函数**：这样它可以被契约测试直接钉住，
    而不是埋在 cmd_bench 的分支里等到某天真的有人这么用时才炸。
    """
    sd = getattr(args, "suite_dir", "")
    md = getattr(args, "memory_dir", "")
    if sd and md:
        return ("--suite-dir 与 --memory-dir 互斥：跨领域要求每个领域**独立**记忆"
                "（否则后一个领域继承前一个的经验，测到的就不是独立领域），"
                "而显式记忆目录意味着**共享**。两者语义冲突。")
    # ── ★ 本轮自踩（2026-10-17 第十五轮）──────────────────────────────
    # 旧代码在 `--memory-dir` 分支里写了一句 `args.fresh = False`：
    # 于是 `--fresh --memory-dir DIR` 会**静默**退化成"累积"模式。
    # 结果我跑了一整组『隔离 vs 累积』2×2 对照，四格全同（mean=0.7600
    # 一字不差），还以为结论稳 —— 其实是**对照组被折叠成了同一格**。
    # 长得像对照却一次没对照，比没有对照更糟，因为它会让你安心。
    if md and getattr(args, "fresh", False):
        return ("--fresh 与 --memory-dir 互斥：--fresh 要求每题**记忆隔离**"
                "（一把尺子量孤立的一次），而 --memory-dir 恰恰指定的是"
                "**共用**记忆目录（学/考范式）。两者只能取其一。")
    if sd and getattr(args, "save_baseline", False):
        return ("--suite-dir 与 --save-baseline 互斥：跨领域有 N 把尺子"
                "（N 个 suite_sha），单一基线文件装不下 —— 每个领域都会写同一个"
                " bench_baseline.json 并互相覆盖，最后只剩最后一个领域。"
                " 请去掉 --save-baseline，或逐领域单独跑（--suite 一个一个来）。")
    # ── 循环模式（第六十三轮）────────────────────────────────────────────
    # ⛔ 这两条都是"静默忽略"型：不挡的话 --loop 会被悄悄丢掉，
    #    而用户看到的仍是一份看起来正常的单轮读数 —— 那正是标签撒谎。
    if int(getattr(args, "loop", 0) or 0) > 0:
        if sd:
            return ("--loop 与 --suite-dir 互斥：循环模式跑的是**一个**套件的一条曲线，"
                    "而 --suite-dir 是 N 个领域各跑一次（N 把尺子）。"
                    " 曲线只能对同一把尺子有意义 —— 请用 --suite 指定单套件。")
        if getattr(args, "save_baseline", False) or getattr(args, "compare", False):
            return ("--loop 与 --save-baseline / --compare 互斥：基线存的是**一次**读数，"
                    "而循环产出的是 N 个点的曲线 —— 拿哪一轮当基线都得靠人挑，"
                    "那等于在曲线里事后选一个好看的。要基线请用单轮 --bench。")
    # ── 外部评审投喂口径（V29）──────────────────────────────────────────
    # `--use-critique` 是一个二值开关，而它把两件代价完全不同的事绑在了一起：
    # 轮内投喂（第 1 题的理由喂给第 6 题 ⇒ 第 6 题不再是同一条件的测量）
    # 与跨轮投喂（上一轮的理由给本轮 ⇒ 整轮 6 题看到同一份起点，可比性完整）。
    # 所以有了 `--critique-scope`。两个入口同时给且语义不一致时，静默让某个赢
    # 就会产出一个标签与事实不符的读数 —— 与 `--fresh --memory-dir` 同一条纪律。
    _cs = getattr(args, "critique_scope", "")
    if _cs and getattr(args, "use_critique", False) and _cs != "all":
        return (f"--use-critique 等价于 --critique-scope=all，与 --critique-scope={_cs} "
                "冲突：前者要求轮内也投喂（接线验证），后者要求本轮新增的评审"
                "不进本轮（保住同轮内每题的可比性）。只留一个。")
    #: `--critique-drop` 只改**快照**；off 档根本不建快照 ⇒ 它会**静默失效**。
    #: 静默失效正是本项目最贵的一类 bug（读数是「按 A 做的」，实际按 B 做的）。
    _cd = getattr(args, "critique_drop", "") or ""
    if _cd.strip():
        _sc = _cs or ("all" if getattr(args, "use_critique", False) else "off")
        if _sc == "off":
            return (f"--critique-drop={_cd} 与 投喂=off 冲突：off 档内核拿 None，"
                    "根本不构造快照，丢弃什么都不会发生 —— 跑出来的是一份"
                    "「看着像消融臂、其实是关着」的读数。")
    return ""


def _universality(rows: list[dict]) -> dict:
    """『通用』的判据 —— **不是一个平均分，是一个三元计数**。

    n_domains : 一共几个领域
    n_trust   : 其中有几个是**可信**读数（没量到的不算零、也不算非零）
    n_nonzero : 可信读数里，有几个严格大于 0
    ok        : 三者相等 ⇒ 每个领域都非零

    ⛔ 为什么不返回一个平均数：平均会把「某领域 = 0」抹成一个好看的 0.75。
    而"某个领域完全做不了"正是『通用』要暴露的第一件事。
    ⛔ 也不把"没量到"读成 0 分：那违反契约③（缺失 ≠ 否定）。
    所以不可信的领域**两头都不算**，只算"没量到"。
    """
    trust = [r for r in rows if r.get("trust")]
    nonzero = [r for r in trust if float(r.get("mean", 0.0)) > 0]
    return {"n_domains": len(rows), "n_trust": len(trust),
            "n_nonzero": len(nonzero),
            "ok": len(rows) == len(trust) == len(nonzero)}


def _cross_domain(args, suite_dir: Path) -> int:
    """在多个**领域**上跑同一个内核 + 同一把打分器，并列汇总。

    ⛔ 刻意**不**把各领域分数平均成一个数。平均会把「某个领域 = 0」这件事
    抹掉 —— 而「每个领域都非零」恰恰是『通用』的**最小判据**。
    这里给的是分布 + 可信计数 + 非零计数，不是一个漂亮的总分。

    每个领域是一把**独立的尺子**（suite_sha 不同），所以各自完整走一遍
    （校准 → 跑 → 口径闸），最后并列；绝不跨领域平均，也绝不跨领域判定。
    """
    files = sorted(p for p in suite_dir.glob("*.jsonl"))
    if not files:
        print(f"❌ {suite_dir} 下没有任何 .jsonl")
        return 2

    if not args.fresh:
        # 不隔离的话，第 2 个领域会带着第 1 个领域的记忆起跑 ——
        # 那测的就不是"独立领域"，而是"跨领域迁移"。本轮要的是前者。
        print("  ℹ️ 跨领域模式强制 --fresh（每题独立记忆目录）：")
        print("     否则后一个领域会继承前一个领域的记忆，测到的就不是独立领域。")
        args = copy.copy(args)
        args.fresh = True

    # ── 会话号：**一次 `--suite-dir` 调用 = 一个会话** ────────────────────
    #
    # ⚠️ 不能让每个领域各自取 `next_session()`：那样一轮 2 个领域就变成
    #    会话 0 与会话 1，而它们其实是**同一次测量**的两个部分。
    #    后果是自我模型做时序切分时，每个族只剩 1 个历史样本
    #    （< MIN_SAMPLES ⇒ 一律退回全局率 ⇒ 永远读不出族信号）。
    #    会话号的语义必须落在"**一次完整评测**"上，而不是"一次追加写入"上。
    _sess_id: "int | None" = None
    if not getattr(args, "no_ledger", False):
        _lp = (Path(args.ledger).expanduser() if getattr(args, "ledger", "")
               else cfg.settings().paths.score_ledger)
        _sess_id = next_session(ScoreLedger(_lp).load())

    runs_path = cfg.settings().paths.bench / "runs.jsonl"
    rows: list[dict] = []
    for f in files:
        sub = copy.copy(args)
        sub.suite = str(f)
        sub.suite_dir = ""            # 防止递归
        sub.session = _sess_id
        print()
        print("█" * 74)
        print(f"█ 领域  {f.stem}   （{f.name}）")
        print("█" * 74)
        before = _count_lines(runs_path)
        rc = cmd_bench(sub)
        rows.append(_domain_row(f.stem, rc, _read_run_after(runs_path, before)))

    print()
    print("=" * 74)
    print("跨领域汇总 —— 同一个内核 + 同一把打分器，逐个领域独立跑")
    print("=" * 74)
    print(f"  {'领域':<18}{'题数':>4}{'mean':>10}   {'可信':<6}{'血缘':<10}备注")
    for r in rows:
        print(f"  {r['domain']:<18}{r['n']:>4}{r['mean']:>10.4f}   "
              f"{'✅' if r['trust'] else '❌':<6}{r['llm']:<10}{r['note']}")
    trust = [r for r in rows if r["trust"]]
    uni = _universality(rows)
    print("  " + "-" * 72)
    print(f"  可信领域 {uni['n_trust']}/{uni['n_domains']}   "
          f"其中**非零** {uni['n_nonzero']}/{uni['n_trust']}"
          f"   ← 『通用』的最小判据：每个领域都非零")
    if len(trust) != len(rows):
        print("  ⚠️ 有领域的读数不可信 ⇒ 它既不算非零也不算零，只能算「没量到」。"
              " 把没量到的领域读成 0 分，就是契约③ 被违反。")

    out = cfg.settings().paths.bench / "cross_domain.json"
    try:
        from mw.bench import append_jsonl
        append_jsonl(out, {"ts": time.time(), "n_domains": len(rows), "rows": rows})
        print(f"  汇总已落盘 {out}")
    except Exception as e:  # noqa: BLE001 — 落盘失败不该吞掉结论，但必须留痕
        print(f"  ⚠️ 汇总落盘失败 {type(e).__name__}: {e}")

    return 0 if uni["ok"] else 1


# ---------------------------------------------------------------------------
# 循环模式（--loop N）的准入检查 —— **纯函数**，所以它能被契约直接钉住
# ---------------------------------------------------------------------------
def curve_guard(args, rounds: int) -> str:
    """循环模式能不能开跑。返回空串 = 放行，否则返回拒绝理由。

    ## 为什么必须挡"记忆跨轮累积"

    `Caliber.memory_seed` 是**开跑之前**拍的快照（facts, episodes）。默认作用域
    `shared_db` 下，第 2 轮起库里的记忆已经比第 1 轮多，而每个点上的签名
    **仍然写着第 1 轮的基线** —— 那个签名描述的不是这一轮的条件。
    于是曲线会拿"记忆更多"冒充"能力更强"，**而这件事在曲线上完全看不出来**：
    它会显示成一条平滑上升的线，每一格都带着看似合法的口径签名。

    这正是本项目反复栽的那一类错：不是尺子坏了，是尺子量错了对象。
    所以要么 `--fresh`（每题一个新库，既不跨题也不跨轮），要么用不碰记忆的参照 solver。

    ## 为什么挡 <3 轮

    `Curve.trend()` 在轮数 <3 时一律 UNKNOWN（样本不足）。少于 3 轮跑出来的
    不是一个"判不了的曲线"，是**一个点**；把它写进曲线文件会让后来的人
    以为曾经量过趋势。
    """
    if int(rounds) < 3:
        return (f"--loop 至少 3 轮（收到 {rounds}）：少于 3 轮 `trend()` 一律 UNKNOWN，"
                "产出的不是曲线而是一个点 —— 写进曲线文件会让后来的人以为量过趋势。")
    # 参照 solver 不碰记忆（oracle 直接抄答案、null 交空、partial 固定错一半）。
    if getattr(args, "solver", "") in ("oracle", "null", "partial"):
        return ""
    if not getattr(args, "fresh", False):
        return ("循环模式下真模型/内核 solver 必须加 `--fresh`：默认的 `shared_db` 让记忆"
                "跨轮累积，而点上的口径签名仍是首轮快照 ⇒ 那条曲线会拿"
                "『记忆更多』冒充『能力更强』，且在曲线上完全看不出来。")
    return ""


def _curve_mode(args, *, solver, scorer, suite, caliber, repeat_n: int,
                tag: str, label: str, root: Path) -> int:
    """跑 N 轮，把每轮读数写成一条能力曲线。**不写跨会话账本**（理由在末尾）。

    接线原则：solver / scorer / suite / caliber 全部由 `cmd_bench` **已经建好的**
    那一套传进来 —— 循环模式不加第二套口径构造。真模型的口径只有
    `cmd_bench` 里那一处（契约 AZ53 钉住 run.py 里只有一个 `Caliber(` 构造点）。
    """
    rounds = int(args.loop)
    why = curve_guard(args, rounds)
    if why:
        print(f"❌ {why}")
        return 2

    out = (Path(args.loop_out).expanduser() if args.loop_out
           else (root / "state" / "curve.jsonl"))
    print()
    print(f"--- 第 2 步（循环模式）  连跑 {rounds} 轮  solver={label}  预算档={tag} ---")
    print(f"  曲线落盘  {out}（**追加**写：同一个文件横跨多次运行）")
    print(f"  口径签名  {caliber.signature()}")
    print("  ⚠️ 每轮开头都会重算打分器与题集的 sha；**文件变了立即停机**，"
          "不把换尺子之后的点接到旧曲线上。")

    kept = _Curve()

    def on_round(p) -> None:
        kept.add(p)
        # 逐轮落盘，不等整轮跑完：`--loop` 也可能被 Ctrl-C 打断，
        # 写到内存再统一保存等于把"跑到一半"变成"什么都没留下"。
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(p.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")

    stopped = ""
    try:
        _run_curve(solver=solver, scorer=scorer, suite=suite,
                   caliber_factory=lambda: caliber, rounds=rounds,
                   repeat_n=repeat_n, budget_tag=tag, candidate=label,
                   on_round=on_round)
    except KeyboardInterrupt:
        print("\n（Ctrl-C：已跑的轮次都已落盘，下面只按已有数据判定）")
    except RuntimeError as e:
        stopped = str(e)
        print(f"\n⛔ 已停止：{e}")

    print()
    print("=" * 74)
    for line in kept.as_lines():
        print(line)
    print("=" * 74)
    verdict, why = kept.trend(args.noise_floor)
    print(f"趋势：{verdict} —— {why}")
    if args.noise_floor is None:
        print("（未给 --noise-floor ⇒ UNKNOWN 是**设计上的默认**，不是失败："
              "没有噪声下限时任何差值都不可判定）")
    print(f"本次 {len(kept)} 个点已追加到 {out}")
    print()
    print("ℹ️ 循环模式**不写**跨会话账本：多轮 = 多个会话，而账本的语义是"
          "『一次完整评测 = 一个会话』。要账本请用单轮 `--bench`。")
    return 2 if stopped else 0


def cmd_bench(args) -> int:
    """外部锚层模式 —— 系统与"分数自娱自乐"的分界线就在这个函数里。

    一个再大的自演化系统，也可能没有任何地方能回答
    "这次改动让系统变好了吗" —— 尺子
    分数恒等于 0，而每轮都交付"全绿"。

    这个模式强制三件事按顺序发生：
        1. **先校准**：尺子必须自证有分辨力（参考解 > 部分解 > 空答案），
           否则 fail-closed 拒用，一个分数都不许出；
        2. **再跑**：打分器在独立子进程里真跑，内核只能拿到一个 float；
        3. **后闸**：写基线前校验口径签名，口径变了就拒绝，旧值另存。
    """
    from mw.bench import (BaselineStore, Caliber, ExternalScorer, Suite,
                          append_jsonl, calibrate, measurement_to_dict,
                          run_suite, verdict_against_baseline)

    # ⚠️ 把 CLI 的温度**写进环境**，让内核（planner / worldmodel）也遵守它。
    #
    # 实测事故（2026-10-17）：内核的三个调用点没有一个传 temperature，
    # 全部落到写死的默认 0.2 ⇒ `--temperature 0` 对内核路径**无效**，
    # 而口径签名却写着 `temperature=0.0`。症状是"同口径、记忆已隔离、
    # 签名逐位相同，同一题两轮仍给 0.5000 / 0.6667"。
    # 现在：CLI 是唯一入口，写进 env ⇒ HTTPLLM 统一从 LLMConfig 取。
    # 这样"读数里写的温度"与"实际发给模型的温度"是同一个事实。
    os.environ["V1_LLM_TEMPERATURE"] = repr(float(args.temperature))

    # ── 互斥/危险组合：先挡住，再谈测量 ──────────────────────────────────
    _conflict = _option_conflict(args)
    if _conflict:
        print(f"❌ {_conflict}")
        return 2

    # ── 记忆目录：让「记忆」成为一个**可持有的实验变量** ──────────────────
    # 为什么需要它：`--fresh`（每题隔离）与默认（共享 state/memory.db）都不足以
    # 表达「先在一个套件上学、再到另一个套件上考」—— 而那正是『泛化』的测法。
    # 给了它就不再每题隔离：显式目录本身就意味着"这些任务共用同一份记忆"。
    _mem_dir = getattr(args, "memory_dir", "")
    if _mem_dir:
        # ⛔ 不再在这里折叠 `--fresh`：静默把 fresh 改成 False 会让『隔离』
        #    对照悄悄变成『累积』（详见 `_option_conflict` 里那条规则的注释）。
        #    语义冲突一律**说出来**，走 `_option_conflict` 统一出口。
        _d = Path(_mem_dir).expanduser()
        _d.mkdir(parents=True, exist_ok=True)
        os.environ["V1_STATE_DIR"] = str(_d)
        args = copy.copy(args)
        # `--fresh` 到达这里**必然是 False**：真冲突时 `_option_conflict` 已经返回 2。
        # 这句赋值只处理"用户根本没给 --fresh"的情形，不再是静默覆盖。
        args.fresh = False

    root = Path(__file__).resolve().parent

    def _data_path(rel: Path) -> Path:
        """默认数据路径：实现见模块级 `data_path()`（第五十九轮提出去 —— 闭包测不了）。

        ★ 第五十八轮（产品化 D-a）：`pip install .` 之后包目录落在 `site-packages`，
          只按包目录找就会「明明脚下有 bench/ 却报找不到」。规则与实测见 `data_path()`。
        """
        return data_path(rel, root=root)

    _suite_dir = getattr(args, "suite_dir", "")
    if _suite_dir and args.suite:
        print("❌ --suite 与 --suite-dir 互斥：前者指定一个套件，后者跑一整目录。")
        return 2
    if _suite_dir:
        d = Path(_suite_dir).expanduser()
        if not d.is_dir():
            print(f"❌ {d} 不是目录（--suite-dir 需要一个目录）")
            return 2
        return _cross_domain(args, d)
    suite_path = (Path(args.suite).expanduser() if args.suite
                  else _data_path(Path("bench") / "tasks" / "coding_small.jsonl"))
    scorer_path = (Path(args.scorer).expanduser() if args.scorer
                   else _data_path(Path("bench") / "scorers" / "code_exec.py"))
    for p in (suite_path, scorer_path):
        if not p.exists():
            print(f"❌ 找不到 {p}")
            #: 只有**兜底到包目录**这一条路才会撞上"wheel 不带数据"这件事；
            #: 用户显式给的路径不存在就是不存在，不该被这段解释带偏。
            if root not in p.parents or root == Path.cwd():
                continue
            print("   ⇒ 这是 `pip install .` 之后的形态：wheel 只装 `mw/` 与 `run.py`，")
            print("     不携带 `bench/` 的任务集与打分器。两种解法：")
            print("     · clone 仓库后在**项目根目录**运行（`v1` 会优先用当前目录里的数据）；")
            print("     · 或用绝对路径显式指定 --suite / --scorer。")
            return 2

    suite = Suite.load(suite_path)
    scorer = ExternalScorer([sys.executable, str(scorer_path)], source=scorer_path)
    # ★ 第四十五轮（§17-7q）：`--max-tokens` 必须是**一个**真相。
    #   7n 实测：caliber 记 1024（CLI 默认）而内核 Planner 实际用 2048
    #   （limits.llm_max_tokens）—— 口径签名描述的调用与真实调用不是同一个。
    #   修法：CLI 显式给 ⇒ 在 settings() 之前写进 env（**唯一配置入口**，
    #   Limits 是 frozen dataclass —— 首版直接赋值实测 FrozenInstanceError；
    #   绕 frozen 用 object.__setattr__ 是私改配置层，不干）；
    #   不给 ⇒ 沿用 limits 默认（2048，行为不变）。下面 caliber / llm-direct
    #   一律读**生效值** s.limits.llm_max_tokens。
    if args.max_tokens is not None:
        os.environ["V1_LLM_MAX_TOKENS"] = str(int(args.max_tokens))
    s = cfg.settings()

    print("=" * 74)
    print("V1 外部锚层 —— 先证明尺子能分辨，再用它量")
    print("=" * 74)
    print(f"  任务集  {suite.name}  n={len(suite)}  suite_sha={suite.sha()}")
    print(f"  打分器  {scorer.name}  scorer_sha={scorer.sha()[:32]}")

    # ── 第 1 步：校准。fail-closed，不通过就不许出任何结论 ----------------
    print()
    print("--- 第 1 步  校准：尺子自证有分辨力（最常缺的就是这道闸）---")
    cal = calibrate(scorer, suite)
    print(cal.as_lines())
    if not cal.ok:
        print()
        print("❌ 尺子不合格 ⇒ **拒绝出分**。")
        print("   这正是最常缺失的一步：尺子从不自证，所以恒等于 0 也没人发现。")
        return 2
    if cal.evidence is not None:
        print()
        print(cal.evidence.as_text())

    # ── 第 2 步：选 solver -----------------------------------------------
    repeat_n = max(1, int(args.repeat or 1))
    tag = f"{args.budget:g}x"
    use_llm = (not args.offline) and s.llm.usable() and args.solver in ("auto", "llm")

    #: 血缘回调（无参 → dict）。**绝不用自由文本标签代替它** —— 见下面预检的注释。
    prov: Callable[[], dict] | None = None

    #: 内核侧世界模型读数的累加器。
    #:
    #: ⚠️ 为什么要它：`per_task` 只记 `score/status/why`，**内核自己的读数一概丢失**。
    #: 于是"失配=3 ⇒ 因果闭环在工作"这类判断只能靠翻 stdout 日志，
    #: 而日志不进 `runs.jsonl`、事后无法审计 —— 这正是本项目反复防的
    #: "读数不可复核"。世界模型的分辨力（scored/hits/混淆矩阵）现在随每次运行落盘。
    #: 累加/派生逻辑在模块级（`_kwm_zero/_kwm_absorb/_kwm_line`），**不在这里重写一遍**。
    _kwm = _kwm_zero()
    _kpl = _kpl_zero()      # 规划器读数（V39）：降级四个分项必须可审计
    _kp = _kp_zero()        # ★ V66（§133）：**候选截断**。⛔ 与 `_kpl` 分列：
    #                         `_kpl.truncated_responses` 数「响应被截几次」（次），
    #                         这里数「候选被 `[:n]` 截几条」（条）。两者修法相反
    #                         （调 `max_tokens` vs 调 `n`）⇒ 合成一格就归不了因。
    _kfo = _kfo_zero()      # 候选让位（§108）：救回与代价必须并列报
    _kco = _kco_zero()      # 收尾兜底（第二十二轮）：与 _kwm 同源的落盘读数
    _kexec = _kexec_zero()  # 下发 × 验收（V55）：零下发是「题」的属性，不是收尾的属性
    _koff = _koff_zero()    # ★ V57（§123）：候选漏斗。与 `_kexec` **并列**：
    #                         `exec` 说"做了几次"，`offer` 说"提了几个、剩下去了哪"。
    #                         ⛔ 合成一个字典那一格就再也分不开"没提"与"提了没做"。
    _kord = _kord_zero()    # ★ V58（§124）：**顺序**。`offer` 说"剩下几个"，
    #                         它说"剩下那些在第几个交付动作之后"——
    #                         ⛔ 与 `_koff` 分列：位置是**轮**的属性，
    #                         而 `_koff` 的主分母是**题**（V57 的 `offered` 已按题计）。
    _ksh = _kshell_zero()   # ★ V60（§126）：**验证动作**。前三个字典都只回答
    #                         "提了/做了/排在谁后面"，没有一格带**返回码**——
    #                         于是"跑过一遍、结果说代码错了"在账本上不可见。
    #                         ⛔ 与 `_kexec` 分列：`exec.ok` 对 `rc=1` 也是 True。
    _kvf = _kvf_zero()      # ★ V61（§127）：**交付闸**。`_ksh` 说"模型自己跑了几次"，
    #                         这里说"闸强制跑了几次、拒了几次"——
    #                         ⛔ 前者是**模型的选择**（V60 实测：只在会做的题上跑），
    #                         后者才是"验证有没有落到错的题上"的那个数。
    _krw = _krw_zero()      # ★ V62（§128）：**打回 → 重写**。`_kvf` 说"拒了几次"，
    #                         这里说"拒完之后报错有没有送达、模型改没改"——
    #                         ⛔ 三个数必须分开：`failed>0` 只证明闸在工作，
    #                         不证明"打回"这条边通到了一次**新的交付**。
    _kex = _kex_zero()      # 外部能力（第二十三轮）：配了没用上必须可见
    _ktx = _ktx_zero()      # 传输层 / 墙钟（第二十五轮）：361s 那类事故必须可见

    # ── 外部评审账本 ────────────────────────────────────────────────
    # 打分器给的**理由**（`Scored.why`）此前只有一个去处：写进日志给人看。
    # 现在它进账本，成为下一轮可以被内核读取的**只读输入** ——
    # 这是「社会与情感」那一格的判据从代理量升级为行为锚之后的通电动作。
    #
    # ⚠️ 默认**只落盘、不投喂**：同一轮里把第 1 题的理由喂给第 6 题，
    #    第 6 题就不再是同一条件下的测量了 —— `repeat` 的意义当场消失。
    #    `--use-critique` 打开的是**接线验证**，不是能力测量。
    # ⛔⛔ 第一百一十二轮（§84）真 bug：这里原来是 `root / "state" / "critique.jsonl"`，
    #    而 `root = Path(__file__).parent`（模块目录）—— **完全不看 `V1_STATE_DIR`**。
    #    于是门禁步骤 5/5b、`--fresh` 的 TemporaryDirectory、以及任何
    #    `V1_STATE_DIR=/tmp/... python3 run.py` 的隔离调用，**评审账本照样写进仓库 `state/`**。
    #    ⭐ 为什么 60 多轮没被发现：本地写入是**幂等**的（题集不变时重写后 sha 不变
    #      `54e89407`），所以「评审账本被改动」这条门禁检查在本地**恒真**；
    #      而 CI 每次 fresh clone 都读到 absent，于是**恒假**。
    #      两边都没测到它想测的那件事（隔离有没有做到），只是在 CI 上意外撞见。
    #    ⇒ 与 `score_ledger` 同口径：默认走 `s.paths.state`（`mw/config.py:86`
    #      已按 `V1_STATE_DIR` 解析）。纪律「设了 env ≠ 全部隔离」由此落地。
    _crit_path = (Path(args.critique_file).expanduser() if args.critique_file
                  else (s.paths.state / "critique.jsonl"))
    _crit = CritiqueLedger(_crit_path)
    _crit_loaded = _crit.load()
    # ── 投喂口径三档（V29）──────────────────────────────────────────────
    # 为什么必须有第三档：旧装置是 全开/全关 两档，而「全开」把跨轮累积和
    # 轮内投喂绑死 —— 想测「上一轮学到的东西对本轮有没有用」，就不得不接受
    # 「同一轮里第 6 题看到第 1 题的评审」这个污染。于是**主线通道结构上无法被测量**。
    #   off  : 内核拿 None（默认，V19~V28 全部如此）
    #   prev : 内核拿**加载时的快照副本**；本轮 `run_suite` 新增的评审不进本轮
    #   all  : 内核拿真账本本体（= 旧 `--use-critique`，接线验证用）
    # `prev` 的实现是只读快照：真账本继续负责落盘 ⇒ `mw/*.py` 零改动
    # （`harness_sha` 不断段 ⇒ 读数可与 V28 的 48 直接比）。
    _crit_scope = (getattr(args, "critique_scope", "")
                   or ("all" if args.use_critique else "off"))
    #: `--critique-drop` 的解析放在**快照构造之前**：它描述的正是「快照里没有谁」。
    _crit_dropped = {t.strip() for t in (getattr(args, "critique_drop", "") or "").split(",")
                     if t.strip()}
    if _crit_scope == "all":
        _crit_for_kernel = _crit
    else:
        _crit_for_kernel = _critique_snapshot(_crit, _crit_scope, _crit_dropped)
    if _crit_loaded or _crit_scope != "off":
        print(f"  评审账本  {_crit_path.name}  载入 {_crit_loaded} 条"
              f"  投喂={_crit_scope}"
              f"（off=关 / prev=只给**加载时**已有的且排除同题 / "
              f"prev-self=同上但**放行同题**（跨轮自省）/ all=轮内也给）"
              f"  本轮赠送 {len(_crit_for_kernel.by_key) if _crit_for_kernel else 0} 条"
              + (f"  快照丢弃={sorted(_crit_dropped)}（消融：真账本不受影响）"
                 if _crit_dropped else ""))

    # 记忆基线必须在**跑之前**读（跑完再读拿到的是结果，不是条件）。
    # 三种作用域都要能被签名区分：隔离 / 共享默认库 / **显式持有的目录**。
    # 第三种是泛化实验的条件变量 —— 它必须可复现，所以目录由调用方持有，
    # 不由 run.py 随便生成（生成的话下次就找不到同一份记忆了）。
    _scope = ("explicit_dir" if _mem_dir
              else ("per_task_fresh" if args.fresh else "shared_db"))
    _seed = (0, 0) if args.fresh else _memory_seed(s)

    # ── 口径签名必须在**跑之前**算出来 ──────────────────────────────────
    # 自我模型要在起跑时拿"当前口径"去账本里筛历史，所以签名不能等到第 3 步
    # （口径闸）才构造 —— 那时任务已经跑完了，自我模型早就是空的了。
    # 这也顺带消灭了一处"同一事实两处构造"：以前只有第 3 步有 Caliber，
    # 若自我模型另算一份，两边字段漂移一次就会静默筛错历史。
    caliber = Caliber(scorer_sha=scorer.sha(), suite_sha=suite.sha(),
                      repeat=repeat_n, budget_tag=tag,
                      temperature=float(args.temperature), fresh=bool(args.fresh),
                      memory_scope=_scope, memory_seed=_seed,
                      # 这三项补进来的理由见 bench.CALIBER_KEYS 的注释：
                      # budget_tag 只是倍数，乘在绝对值上 —— 不含绝对值的"同口径"是假的。
                      limits=s.limits.as_dict(),
                      llm_cond=s.llm.cond(),
                      # ★ 第四十五轮：一律记**生效值**（limits 已被 CLI 覆盖过），
                      #   不再读 args.max_tokens —— 两套真相在这里出过实测事故。
                      max_tokens=int(s.limits.llm_max_tokens),
                      # ★ 第二十三轮补上的漏洞：`planner.py:182` 把工具目录拼进提示词，
                      #   于是"改 agents.json 就等于改提示词"。实测后果见
                      #   `bench.Caliber.tool_catalog_sha` 的注释（同 mean、不同分布）。
                      # ⚠️ 必须与 Core 真实建的注册表**逐字一致** —— 契约 AF1 拿
                      #   `Core(...).tools.catalog()` 的哈希与它直接对撞。
                      # ★ 第四十五轮（§17-7q）：allow_note 必须同源 —— note 注册与否
                      #   改变目录文本；7n 实锤"口径 True / 活注册表 False"的两真相。
                      tool_catalog_sha=tools_catalog_sha(
                          wire_external=not getattr(args, "no_external", False),
                          agents_path=s.paths.root / "agents.json",
                          workspace=s.paths.workspace,
                          allow_note=s.limits.allow_note))
    # 账本签名与基线签名**不是同一把**（见 mw.ledger.LEDGER_SIG_EXCLUDE）：
    # 基线要钉死题目集（"同一个候选有没有变好"）；
    # 自我模型要跨题目集（"我在哪些族上弱"）—— 钉死了就没这个问题可问。
    _cal_sig = ledger_sig_of(caliber.as_dict())

    # ── 跨会话分数账本：自我模型的证据来源 ──────────────────────────────
    # ⚠️ 内核**只读**它（起跑冻结，见 mw/ledger.py 模块头）；写入发生在
    #    本轮拿到外部打分器的分数**之后**（本函数末尾），由外部执行。
    _ledger: "ScoreLedger | None" = None
    if not args.no_ledger:
        _led_path = (Path(args.ledger).expanduser() if args.ledger
                     else s.paths.score_ledger)
        _ledger = ScoreLedger(_led_path, caliber_sig=_cal_sig)
        _led_hist = _ledger.load()
        _led_adm = _ledger.admissible(_cal_sig)
        if _led_hist:
            print(f"  分数账本  {_led_path.name}  载入 {len(_led_hist)} 行"
                  f"  同口径采纳 {len(_led_adm)} 行  （自我模型据此起跑）")

    if args.solver == "null":
        solver: Callable[[object], str] = lambda t: ""            # noqa: E731
        label = "null（下界参照）"
    elif args.solver == "oracle":
        solver = lambda t: t.oracle                                # noqa: E731
        label = "oracle（上界参照）"
    elif args.solver == "partial":
        # 任务集自带的部分解 —— 用来演示"锚层也能**确认**一个改进"，
        # 而不只是会拒绝。过去从未做过一次这样的实验。
        solver = lambda t: t.partial                               # noqa: E731
        label = "partial（中段参照）"
    elif args.solver == "llm-direct":
        # 模型**直连**答题：不经内核、不重规划、不检索记忆。
        # 存在的理由：噪声下限必须在**最短的链路**上先测出来。
        # 若连"一次调用"都不确定，那内核再稳也没意义 —— 噪声是底噪，不是内核产生的。
        if args.offline or not s.llm.usable():
            print("❌ llm-direct 需要真模型：请设 V1_LLM_BASE_URL / V1_LLM_API_KEY / V1_LLM_MODEL")
            return 2
        from mw.llm import HTTPLLM, Message
        _llm = HTTPLLM(s.llm)

        def solver(t):                                             # type: ignore[misc]
            return _llm.complete(
                [Message("system", DIRECT_SYSTEM), Message("user", t.prompt)],
                max_tokens=int(s.limits.llm_max_tokens),
                temperature=float(args.temperature),
            )

        label = f"真模型直连 {s.llm.model} T={args.temperature:g}"
        # 直连**没有内核**，所以走的是同一构造入口的显式取值版（字段集合依旧唯一）。
        prov = lambda: _prov(llm_kind="http-direct", external_agents=[],  # noqa: E731
                             model=s.llm.model, temperature=args.temperature)
    elif use_llm:
        # ── 预检：内核**实际**会用哪条通道？这是事实，不是声明。 ──────────────
        # 血泪教训（2026-10-17 实测）：这里曾把标签写成"真模型 qwen2.5:7b"，
        # 而 core.py 里的 `llm or StubLLM()` 让内核**全程用离线桩** ——
        # 1 秒"跑完" 6 题、每轮报 done、mean=0.0000 却标 `可信=✅`，
        # 真模型一次都没被调用，且标签与事实矛盾而**无任何机制发现**。
        # 所以出分之前先问内核一次；拿不到真通道就 fail-closed，一个分都不出。
        _probe = _build("", args, allow_shell=args.allow_shell,
                        score_ledger=_ledger, caliber_sig=_cal_sig)
        _kind = str(getattr(_probe, "llm_kind", "unknown"))
        if _kind != "http":
            print(f"❌ 内核实际通道 = {_kind}，不是真模型 ⇒ **拒绝出分**。")
            print("   静默降级是缺陷：那样的读数看起来像模型成绩，实际什么也没测。")
            return 2
        label = f"内核/{_kind} {s.llm.model}"
        if args.fresh:
            _seen: dict = {}

            def solver(t):                                         # type: ignore[misc]
                # ⚠️ 每题必须换**记忆目录**，不只是换一个 Core。
                #
                # 实测事故（2026-10-17）：原先这里只 `_build` 一个新 Core，
                # 而 DB 路径仍是共享的 `state/memory.db` ⇒ 记忆跨题、**跨运行**累积。
                # 后果：两轮"同口径"（1x / T=0 / --fresh）读数一次 `0.8611`、
                # 一次 `0.7222`；差额全部来自起跑时已存在的语义记忆（第二轮已有 27 条）。
                # 而标签当时写着"记忆不跨题" —— 标签在撒谎，口径闸也判"可比"。
                # 现在：每题一个临时 state 目录，跑完即删；口径里如实记 `per_task_fresh`。
                with tempfile.TemporaryDirectory(prefix="v1_fresh_") as td:
                    prev = os.environ.get("V1_STATE_DIR")
                    os.environ["V1_STATE_DIR"] = str(Path(td) / "state")
                    try:
                        c = _build("", args, allow_shell=args.allow_shell,
                                   critique_ledger=_crit_for_kernel,
                                   score_ledger=_ledger, caliber_sig=_cal_sig)
                        out = c.run(t.prompt, budget_factor=args.budget,
                                    task_id=t.id, family=suite.name,
                                    scorer_sha=scorer.sha(), suite_sha=suite.sha(),
                                    features=_feat_of.get(t.id))
                        _kwm_absorb(_kwm, out.stats["worldmodel"], c)
                        _kpl_absorb(_kpl, out.stats.get("planner") or {}, c)
                        # ★ V66（§133）：候选截断。⛔ 紧挨 `_kpl_absorb` ——
                        #   两个截断读数放在一起，读的时候才不会被名字骗过去。
                        _kp_absorb(_kp, out.stats.get("plan") or {})
                        _kfo_absorb(_kfo, out.stats.get("failover") or {})
                        _kco_absorb(_kco, out.stats.get("closeout") or {})
                        # ★ V55（§121）：按题记「下发次数 × 验收结论」。
                        #   与 `_kco_absorb` 并列放，不并进去：那个按**收尾次数**数，
                        #   这个按**题**数 —— 两个分母（见 `_kexec_zero` 的文档）。
                        _kexec_absorb(_kexec, out)
                        # ★ V57（§123）：候选漏斗。⛔ 必须与 `_kexec_absorb` 紧挨着 ——
                        #   两个数共用"这一题"这个分母，分开写会有一处漏掉。
                        _koff_absorb(_koff, out)
                        # ★ V58（§124）：顺序。**必须与 `_koff_absorb` 紧挨** ——
                        #   它读的是同一份 `plan`/`offer`，分开写就会有一处漏。
                        _kord_absorb(_kord, out)
                        # ★ V60（§126）：验证动作。**两个 solver 分支各接一次** ——
                        #   漏一处 = 静默少计一题的"跑没跑/结果对不对"。
                        _kshell_absorb(_ksh, out)
                        # ★ V61（§127）：交付闸。与 `_kshell_absorb` 紧挨着：同一个
                        #   问题的两侧 —— 它数"模型自己跑了几次"，这里数"闸有没有强制跑"。
                        _kvf_absorb(_kvf, out)
                        # ★ V62（§128）：打回 → 重写。与 `_kvf_absorb` 紧挨着：
                        #   它说"拒了几次"，这里说"拒完之后报错有没有送到、模型改没改"。
                        _krw_absorb(_krw, out)
                        _kex_absorb(_kex, out.stats.get("external") or {})
                        _kex["tasks"] += 1
                        _ktx_absorb(_ktx, out.stats.get("transport") or {})
                        # ★state 证据出口（§17-7h）：只在 `--state-dump` 时写盘
                        _dump_states(getattr(args, "state_dump", ""),
                                     t.id, getattr(c, "state_log", None),
                                     getattr(c, "state_meta", None))
                        # 血缘**从内核读**（llm_kind / external_agents / model / temperature
                        # 四个字段一次到位）—— 曾经这里手写两个键，漏了 model。
                        _seen.update(_kernel_prov(c))
                        return out.answer
                    finally:
                        if prev is None:
                            os.environ.pop("V1_STATE_DIR", None)
                        else:
                            os.environ["V1_STATE_DIR"] = prev

            label += "（每题新建内核 + 记忆隔离）"
            prov = lambda: dict(_seen)                             # noqa: E731
        else:
            _core = _build("", args, allow_shell=args.allow_shell,
                           critique_ledger=_crit_for_kernel,
                           score_ledger=_ledger, caliber_sig=_cal_sig)

            def solver(t):                                         # type: ignore[misc]
                out = _core.run(t.prompt, budget_factor=args.budget,
                                task_id=t.id, family=suite.name,
                                scorer_sha=scorer.sha(), suite_sha=suite.sha(),
                                features=_feat_of.get(t.id))
                _kwm_absorb(_kwm, out.stats["worldmodel"], _core)
                _kpl_absorb(_kpl, out.stats.get("planner") or {}, _core)
                # ★ V66（§133）：与 `--fresh` 那支同一条理由 —— 两处各写一遍
                #   "同一事实"，漏一处就静默少计一题的候选截断。
                #   ⚠️ 这里**仍然不用传 `_core`**（不求差）：`plan_stats` 由
                #   `run()` 开头的 `_prime_counters` 每题重建，与 `Planner`
                #   那种生命周期累计不同。
                _kp_absorb(_kp, out.stats.get("plan") or {})
                _kfo_absorb(_kfo, out.stats.get("failover") or {})
                _kco_absorb(_kco, out.stats.get("closeout") or {})
                # ★ V55（§121）：与 `--fresh` 那支同一条理由 —— 两处各写一遍
                #   "同一事实"，漏一处就会静默少计一题的零下发。
                _kexec_absorb(_kexec, out)
                # ★ V57（§123）：两处都接 —— 漏一处 = 静默少计一题的漏斗。
                _koff_absorb(_koff, out)
                # ★ V58（§124）：与 `--fresh` 那支同一条理由。
                _kord_absorb(_kord, out)
                # ★ V60（§126）：与 `--fresh` 那支同一条理由。
                _kshell_absorb(_ksh, out)
                # ★ V61（§127）：与 `--fresh` 那支同一条理由。
                _kvf_absorb(_kvf, out)
                # ★ V62（§128）：与 `--fresh` 那支同一条理由（漏一处 = 少计一题）。
                _krw_absorb(_krw, out)
                _kex_absorb(_kex, out.stats.get("external") or {})
                _kex["tasks"] += 1
                _ktx_absorb(_ktx, out.stats.get("transport") or {})
                # ★state 证据出口（§17-7h）：同一内核连跑多题，`state_log` 在
                # `run()` 开头清空 ⇒ 每次 dump 出来的都是**本题**的 state。
                _dump_states(getattr(args, "state_dump", ""),
                             t.id, getattr(_core, "state_log", None),
                             getattr(_core, "state_meta", None))
                return out.answer

            label += "（复用同一内核，记忆跨题累积）"
            prov = lambda: _kernel_prov(_core)                     # noqa: E731
    else:
        solver = lambda t: t.oracle                                # noqa: E731
        label = "oracle（上界参照）"
        print()
        print("  ℹ️ 未配置真模型（V1_LLM_API_KEY）⇒ 本轮跑的是**参照点**，不是模型成绩。")

    # ── 题面结构特征：**外部**算好，交给内核做奖励检索用 ──────────────
    # ⛔ 内核**不**自己算它（见 `Core.run` 的说明）：一旦内核能从目标文本里
    #    给自己造特征，它就能给自己挑"看起来像"的先例 ⇒ 检索退化成自证。
    from mw.features import feature_set as _feature_set
    _feat_of = {t.id: _feature_set(t.as_dict()) for t in suite.tasks}

    # ── 循环模式：同一套 solver / 尺子 / 口径连跑 N 轮 ────────────────────
    # 干净地在这里分叉：下面是单轮路径（含账本、基线、奖励回灌），
    # 而循环模式跑完就返回 —— 它**不写**账本（多轮 = 多个会话，语义不同），
    # 也不碰基线（一份 baseline 装不下一条曲线）。
    if int(getattr(args, "loop", 0) or 0) > 0:
        return _curve_mode(args, solver=solver, scorer=scorer, suite=suite,
                           caliber=caliber, repeat_n=repeat_n, tag=tag,
                           label=label, root=root)

    print()
    print(f"--- 第 2 步  跑任务集  solver={label}  预算档={tag}  repeat={repeat_n} ---")

    # ── 答案本体落盘（第七十九轮 §42）────────────────────────────────────
    #
    # ⚠️ 为什么不在 `run_suite` 里写：那会改 `mw/*.py` ⇒ `harness_sha()` 断段。
    #    而这一轮是**量具**变更，不是能力变更 —— 断段会把旧曲线点全部作废，
    #    代价远大于收益。所以包在 solver 外面：`run_suite` 一行都不用动。
    #
    # ⚠️ 为什么在 `--loop` 之后：循环模式有自己的逐轮落盘，语义不同
    #    （一轮一条曲线点，不是一次运行）。**本版只覆盖单轮 bench 路径** ——
    #    这是缺口，明写在这里，不当成"已经全覆盖"。
    #
    # ⛔ 三条边界见 `bench/answer_store.py` 模块头：只写答案文本、跑完才写、
    #    任何判据都不读它。
    _ans_log = None
    if not getattr(args, "no_answers", False):
        from bench.answer_store import AnswerLog, make_run_id, records_path
        _ans_log = AnswerLog(records_path(s.paths.bench))
        _solver_raw = solver

        def solver(t):                                             # type: ignore[misc]
            a = _solver_raw(t)
            _ans_log.add(t.id, a)
            return a

    m, rep = run_suite(solver, scorer, suite, repeat_n=repeat_n,
                       budget_tag=tag, candidate=label, provenance=prov,
                       critique_ledger=_crit)
    _crit_saved = _crit.save()

    # ── 跨会话账本：**只在跑完之后**追加（外部侧写入）─────────────────
    #
    # ⚠️ 为什么这里**排除参照点**（oracle / null / partial）：
    #    账本是自我模型的"我在哪些族上做对过"的记忆。把 oracle 的满分写进去，
    #    自我模型会"记得"自己每个族都是 1.0 —— 那不是记忆，是**伪造履历**。
    #    参照点是尺子的校准材料，不是系统的能力读数；两者的混用
    #    正是"把校准材料当结论"的同一类错误。
    #
    # ⚠️ 为什么写在**跑完之后**：轮内写入会让第 N 题读到第 N-1 题的分数
    #    （`mw/ledger.py` 模块头纪律①）。这里已经是外部，且本轮已结束。
    if _ledger is not None and args.solver not in ("oracle", "null", "partial"):
        # 跨领域模式下会话号由 `_cross_domain` 统一分配（一次评测 = 一个会话）；
        # 单套件模式下一轮就是一次追加，自己取下一个号即可 —— 两种情形
        # 都落在"一次完整评测 = 一个会话"这个语义上。
        _sess = getattr(args, "session", None)
        _sess = next_session(_ledger.load()) if _sess is None else int(_sess)
        # ⚠️ `caliber` 是**可读的口径字典**（预算/温度/helper…），不是只有哈希。
        #    第十五轮实测逼出：没有它，六个月后的我看得到分数却看不到这组
        #    分数是在什么预算下跑的 —— 而同一套 struct_convert 在 1024 与
        #    1536 tokens 下能差出近一倍（0.400 vs 0.7600）。证据必须自描述。
        _cal_fields = ledger_caliber_of(caliber.as_dict())
        _rows = [{"session": _sess, "family": suite.name, "task_id": r["id"],
                  "score": r["score"], "passed": is_passed(r["score"]),
                  "caliber_sig": _cal_sig, "caliber": _cal_fields,
                  "candidate": label}
                 for r in rep.per_task]
        _w = _ledger.append(_rows)
        # 重复数**单独念**：它意味着"这一轮的成绩账本里已经有了"
        # （典型成因：上次跑到一半被 kill，重启后算到同一个会话号）。
        # 混进"拒绝"里就再也分不清是字段没记全还是重跑了一遍。
        if _w["added"] or _w["duplicate"] or _w["no_caliber"]:
            _dup = f"  ⚠️重复={_w['duplicate']}" if _w["duplicate"] else ""
            _nc = (f"  ⚠️不可复核={_w['no_caliber']}（无 {CALIBER_FIELD}）"
                   if _w["no_caliber"] else "")
            print(f"  分数账本  会话#{_sess} 追加 {_w['added']} 行"
                  f"（拒绝 {_w['rejected']} 行{_dup}{_nc}）"
                  f" ⇒ 下一次起跑时自我模型可读")

    # ── 外部奖励：把**外部打分器**给的分数写进记忆（只读通道，§17-5）────
    #
    # 为什么需要它：内核的动作只有 `finish`，`finish` 永不失败 ⇒ `result.ok`
    # 恒为 True ⇒ 世界模型**没有可学的失败** ⇒ 语义记忆 0 条 ⇒ 泛化实验
    # 变成"空记忆 vs 空记忆"，Δ=0 是算术必然（DESIGN §15.2b 实测）。
    # 失败的其实是"答案对不对"，而那只有外部打分器知道。
    #
    # ⛔ 三条边界，缺一条这条路就变成作弊：
    #   ① 来源必须是外部（`REWARD_SOURCE`），内核自评一律拒收；
    #   ② 写在**整轮跑完之后**（与账本同理）：轮内写会让第 N 题读到第 N-1 题；
    #   ③ 检索时排除**同一题号**（`reward_guidance`）⇒ 开卷考同一道题不算学习。
    if getattr(args, "use_reward", False) and args.solver not in ("oracle", "null", "partial"):
        from mw.llm import StubLLM
        from mw.memory import MemoryStore
        from mw.worldmodel import REWARD_SOURCE, WorldModel
        _rwm = WorldModel(MemoryStore(cfg.settings().paths.db), StubLLM())
        _rw_ok = _rw_rej = 0
        for r in rep.per_task:
            f = _feat_of.get(r.get("id"))
            if f is None:
                continue
            _passed = is_passed(r.get("score"))
            _fail = "" if _passed else str(r.get("why") or "")[:160]
            if _rwm.record_reward(task_id=str(r.get("id")), features=f,
                                  passed=_passed, score=r.get("score"),
                                  failure=_fail, source=REWARD_SOURCE):
                _rw_ok += 1
            else:
                _rw_rej += 1
        print(f"  外部奖励  写入 {_rw_ok} 条（拒 {_rw_rej}）"
              f" ⇒ 下一次起跑时内核可按结构特征检索同类题的历史得分")

    print(f"  {m.as_line()}")
    print(f"  {rep.summary_line()}")
    if _crit_saved:
        _cs = _crit.stats()
        # 分母同时给：`rejected` 是累计的，离开 `calls` 无法解释。
        print(f"  评审     落盘={_crit_saved} 条  投喂={_cs['served']}"
              f"（{_cs['calls']} 次筛选）  拒绝={_cs['rejected']}")
    if prov is not None:
        print(f"  血缘      llm_of_record={rep.llm_of_record()}"
              f" 外部能力={len(rep.provenance.get('external_agents') or [])} 个")
    _kw = _kwm_line(_kwm)
    if _kw["predictions"]:
        # 只印"失配=N"是假读数：N 次预演里错 N 次、和 3N 次里错 N 次是两件事。
        # 所以**必须**同时印分母（检验了几次）与混淆矩阵 —— 一个恒说"会成功"的
        # 预测器，`fail_*` 两格恒为 0，单看准确率看不出来。
        print(f"  世界模型  预演={_kw['predictions']} 检验={_kw['scored']} 命中={_kw['hits']}"
              f" 命中率={'n/a' if _kw['hit_rate'] is None else _kw['hit_rate']}"
              f" 失配={_kw['mismatches']}(成败{_kw['mismatches_outcome']}"
              f"/文本{_kw['mismatches_text']})"
              f" 混淆={_kw['confusion']}"
              + (f" 奖励[写={_kw.get('rewards_recorded', 0)}"
                 f" 读={_kw.get('rewards_seen', 0)}"
                 f" 挡自己={_kw.get('rewards_skipped_self', 0)}"
                 f" 挡成功={_kw.get('rewards_skipped_success', 0)}"
                 f" 拒={_kw.get('rewards_rejected_source', 0)}"
                 f"+{_kw.get('rewards_rejected_missing', 0)}]"
                 if (_kw.get('rewards_recorded') or _kw.get('rewards_seen')
                     or _kw.get('rewards_skipped_self')
                     or _kw.get('rewards_skipped_success')) else "")
              # ⛔ 抽象臂必须把"检索到几条 / 其中几条是抽象层"念出来。
              #    只念 mode 的人分不清" abstract 层有用"和"abstract 层是空的"——
              #    后者会让整个实验变成一场没有变量的空转（第十五轮自踩过一次）。
              + (f" 记忆层[mode={getattr(args, 'memory_mode', 'raw')}"
                 f" 检索={_kw['layer_recalled']}"
                 f" 抽象={_kw['layer_abstract']}]"
                 if _kw["layer_recalled"] or _kw["layer_abstract"] else "")
              + (f" ⚠️未检验={_kw['unscored']}" if _kw["unscored"] else ""))

    # ── 规划器降级分项（V39）──────────────────────────────────────────────
    # ⛔ **无条件**印：`json_fail=0` 与"根本没统计"在读数上必须分得开
    #    （同 `blocked_skipped` 的理由）。分母 `calls` 一起印 ——
    #    只报 `失败=3` 无法判断是 3/3 还是 3/60。
    _kpl_p = _kpl_line(_kpl)
    # ⚠️ 「截断**响应**」：单位是**次**。同一条 stdout 后面还有一句
    #    「截断 N 条答案」（`answer_store`，单位**条**）—— 两个都不写清，
    #    `截断=2` 与 `截断 2 条` 读起来一模一样（V66/§133 要修的就是这个）。
    print(f"  规划器    调用={_kpl_p['calls']} 失败={_kpl_p['fail_total']}"
          f"（json={_kpl_p['json_fail']} 截断响应={_kpl_p['truncated_responses']}"
          f" 无工具={_kpl_p['no_tool_fit']} 空计划={_kpl_p['empty_plan']}）"
          f" 率={'n/a' if _kpl_p['fail_rate'] is None else _kpl_p['fail_rate']}"
          f" 重问={_kpl_p['reseek']}"
          f" 补救={_kpl_p['answer_retry_ok']}/{_kpl_p['answer_retry_fail']}"
          # ★§106：修复读数**必须**无条件印 —— `0/0` 表示"没叫到修复器"，
          # 与"叫到了但修不动（`0/N`）"是两件事（纪律 13：机制存在 ≠ 机制通电）。
          f" 修复={_kpl_p['salvage_ok']}/{_kpl_p['salvage_fail']}"
          f"（补完={_kpl_p['salvage_completed']}）")

    # ── 候选截断（V66 / §133）──────────────────────────────────────────────
    # ⛔ **无条件**印：`truncated_cands=0` 与"没统计"在读数上必须分得开。
    # ⚠️ 单位是**条**，与上面那行的「截断响应」（次）是两把尺子 ——
    #    这一行回答"规划器交了 N 条、被 `[:n]` 截掉几条"，
    #    上面那行回答"有几次响应的 JSON 断在半截"。
    _kp_p = _kp_line(_kp)
    print(f"  候选宽度  收={_kp_p['examined_total']} 条"
          f" 截断={_kp_p['truncated_cands']} 条"
          f" 进执行器={_kp_p['cands_total']} 条"
          f"（截断率={'n/a' if _kp_p['truncated_rate'] is None else _kp_p['truncated_rate']}"
          f" 轮数={_kp_p['rounds']}"
          f" 不适配={_kp_p['dropped_not_applicable']}）")

    # ── 候选让位（§108）──────────────────────────────────────────────────
    # ⛔ **无条件**印，且**收益与代价同一行**：
    #    `救回=N` 单独印会被读成"机制有用"；只印 `用=N` 会被读成"没效果"。
    #    两者的比 `rescue_rate` 才是这一轮唯一的判决入口。
    #    `无弹药=M` 必须显形 —— V41 量到的 8/64 窗口全是这一形态，
    #    不印它，`用=0` 就等于"机制没通电"（而真相是"没有第二个候选可换"）。
    _kfo_p = _kfo_line(_kfo)
    # ★ §112：**两档必须分开印在同一行**。`救回=1` 单独印，读者分不出
    #   "救回一次交付"与"救回一个中间步骤" —— 而这两件事的下一步完全不同
    #   （一个该找交付动作，一个该问"救回的一步为什么没被接成交付"）。
    print(f"  候选让位  开关={_kfo_p['switch']} 用={_kfo_p['used']}"
          f" 救回=答案{_kfo_p['rescued_answer']}/步骤{_kfo_p['rescued_step']}"
          f"（finish={_kfo_p['finish']} 外部={_kfo_p['external']}）"
          f" 答案率={'n/a' if _kfo_p['rescue_rate'] is None else _kfo_p['rescue_rate']}"
          f" ｜代价：额外失败={_kfo_p['cf_extra']} 无弹药={_kfo_p['no_more']}")

    # ── 传输层读数（第二十五轮）：**只在出过事时**才占一行 ────────────────
    # 为什么要印：R2 主张"这次修复不改变正常路径"，而它的证据是
    # 正常端点上"放弃"类计数全程为 0（而"上限被裁短"会是 N ≡ 调用次数的常数
    # —— 默认 wall=120 / reserve=45 ⇒ 每次尝试的上限都是 75s，这个数**必然**非零，
    # 把它误当成"出事了"就会把正常路径读成故障）。
    # 一个不打印的 0 无法与"根本没统计"区分开 —— 所以这一行同时印分母 `tasks`。
    if _ktx["retries"] or _ktx["exhausted"] or _ktx["wall_shortened"] \
            or _ktx["wall_refused_first"] or _ktx["wall_refused_retry"]:
        print(f"  传输层    抖动重试={_ktx['retries']} 重试后仍失败={_ktx['exhausted']}"
              f" 上限被裁短={_ktx['wall_shortened']}"
              f" 墙钟放弃首发={_ktx['wall_refused_first']}"
              f"/重试={_ktx['wall_refused_retry']}"
              f" （共 {_ktx['tasks']} 题）")

    # ── 血缘闸：标签可以撒谎，血缘不能。 ────────────────────────────────
    # `candidate` 是一串自由文本；只有血缘是**从内核读出来的事实**。
    # 要求真模型却拿到桩 ⇒ 拒绝出分、拒绝判定、拒绝写基线（fail-closed）。
    _wants_model = args.solver in ("llm", "llm-direct", "auto")
    if _wants_model and rep.llm_of_record() not in ("http", "http-direct"):
        print()
        print(f"  ⛔ 血缘显示本轮**不是**真模型（llm_of_record={rep.llm_of_record()}）"
              " ⇒ 拒绝出分／判定／写基线。")
        return 2

    if not rep.trustworthy():
        print("  ⚠️ 本轮存在早退（没量到的条目）⇒ 这个读数**不得**用于判定。")
        for e in rep.errors[:5]:
            print(f"     · {e}")
    for line in _noise_report(rep, suite, m, repeat_n):
        print(line)

    # ── 第 3 步：口径闸 ------------------------------------------------
    # `caliber` 已在第 2 步之前构造（自我模型起跑时就要用它筛历史），
    # 这里**不再重算** —— 同一轮里两处构造同一个签名，是漂移的入口。
    bl_path = (Path(args.baseline).expanduser() if args.baseline
               else s.paths.bench / "bench_baseline.json")
    store = BaselineStore(bl_path)

    print()
    print("--- 第 3 步  口径闸（尺子不能随退化下移）---")
    print(f"  本次口径  {caliber.as_line()}")

    old = store.load_caliber()
    if args.compare and old is None:
        print("  无基线可比（这是第一次）。")
    elif args.compare:
        base = store.load_measurement()
        if base is None:
            print("  基线文件存在但读数不可解析 ⇒ 拒绝比较。")
        elif not old.same_as(caliber):
            print(f"  ⛔ 口径不一致（差异字段 {old.diff(caliber)}）⇒ **拒绝比较**。")
            print("     跨口径的两个数字放在一张表里，结论就是作废的。")
        else:
            ok, why = verdict_against_baseline(base, m)
            print(f"  基线 {base.as_line()}")
            print(f"  判定 {'改进成立' if ok else '不成立'} — {why}")

    if args.save_baseline:
        ok, why = store.save(m, caliber, allow_supersede=args.allow_supersede)
        print(f"  {'✅' if ok else '⛔'} {why}")

    # ── 这一轮的身份证：**先算一次，两处共用** ───────────────────────────
    # 第七十九轮：`answers.jsonl` 要能与 `runs.jsonl` 靠 `run_id` 直接 join。
    # 两处各造一次 = 两份 id 对不上，而"这次答案属于哪次运行"就又只能靠人记。
    # ⛔ pid 会重用，所以加上写盘时刻：`run_id` 才是判据，pid 只是它的一半。
    _run_id = make_run_id() if _ans_log is not None else f"{os.getpid()}@{int(time.time())}"

    append_jsonl(s.paths.bench / "runs.jsonl", {
        # ── 独立运行的身份证（第七十二轮，§36）────────────────────────────
        # 为什么必须落盘：噪声下限得在**独立进程之间**量，而"这次运行是不是
        # 另一次运行"在这之前**在数据里不存在** —— 只能靠人记"我跑了几次"。
        # 于是 `--repeat 2` 的两次重复与"跑了两个进程"在证据里长得一模一样，
        # 而前者系统性低估噪声（DESIGN §35 实测 0.2619 vs 0.0000）。
        "run_id": _run_id,
        "solver": label, "budget_tag": tag, "repeat": repeat_n,
        "caliber_sig": caliber.signature(), "caliber": caliber.as_dict(),
        "measurement": measurement_to_dict(m), "report": rep.as_dict(),
        # 内核侧读数（世界模型的**分辨力**）。不落盘的读数不可审计。
        "kernel_worldmodel": _kw,
        # 内核侧读数（规划器的**降级分项**，V39）。同一条理由：
        # 四个分项的修法完全不同，只报 `degraded` 一个总数就归不了因。
        "kernel_planner": _kpl_line(_kpl),
        # ★ V66（§133）：**候选截断**（单位：**条**）。⛔ 与 `kernel_planner`
        #   分列而不是并进去 —— 那个数的是「响应被截几次」（次），
        #   这个数的是「候选被 `[:n]` 截掉几条」（条）。两者修法相反
        #   （调 `max_tokens` vs 调 `n`），合成一格就再也分不清该改哪个。
        #   ⚠️ 这个键是 V66 之前**根本不存在**的：`plan_stats` 只经
        #   `snapshot()["plan"]` 出去，账面上一格都没有 ⇒ 跑批层面读不到，
        #   只有直接调 `core.snapshot()` 的探针能看见（纪律 26 的原形）。
        "kernel_plan": _kp_line(_kp),
        # 内核侧读数（候选让位，§108）：**救回与代价必须并列** ——
        # 只落 `rescued` 而不落 `cf_extra`，等于把"多打几次换一次交付"藏起来。
        "kernel_failover": _kfo_line(_kfo),
        # 收尾兜底读数（第二十二轮）：**不落盘的读数不可审计**。
        "kernel_closeout": _kco,
        # ★ V55（§121）：按题的「下发次数 × 验收结论」。与 `kernel_closeout`
        #    **并列**而不是并进去 —— 那个按收尾次数数，这个按题数（两个分母）。
        "kernel_exec": _kexec,
        # ★ V57（§123）：候选漏斗。⛔ 与 `kernel_exec` **并列**、不并进去：
        #   `exec.dispatched` 回答"做了几次"，`offer.offered` 回答"提了几个"，
        #   差值 `unaccounted` 必须为 0 —— 不为 0 就说明还有第三个出口没被数到。
        "kernel_offer": _koff_line(_koff),
        # ★ V58（§124）：**顺序**。⛔ 与 `kernel_offer` 并列、不并进去：
        #   `offer` 的分母是**题**，这里的主分母是**轮**（18 vs 12）；
        #   合成一个字典，`pos_hist` 的合计就会被当成题数读。
        #   `tail_survived` 与 `unreached_by_stop.answer` 合起来必须等于 `tail_total`
        #   （两个来源独立累加，对不上就是装置故障 —— `tail_negative_tasks` 兜底）。
        "kernel_order": _kord,
        # ★ V60（§126）：**验证动作**。⛔ 与 `kernel_exec` 并列、不并进去：
        #   `exec.ok=True` 对 `rc=1` 也成立（工具调用成功 ≠ 命令成功），
        #   而本轮问的正是"跑过一遍、结果说代码是错的"这件事。
        #   分母三档（题/轮/次）在 `_kshell_zero` 的 docstring 里写死。
        "kernel_shell": _kshell_line(_ksh),
        # ★ V61（§127）：**交付闸**。⛔ 与 `kernel_shell` 并列、不并进去：
        #   那个数的是**模型发起**的验证（V60 实测只在会做的题上跑），
        #   这个数的是**闸强制**的验证 —— 「由谁发起」正是本轮唯一的变量。
        #   `example_task_runs=0` 不是"通过"，是这条判据**不适用**（见 `_kvf_zero`）。
        "kernel_verify": _kvf_line(_kvf),
        # ★ V62（§128）：打回 → 重写。`_kvf` 的 `failed` 只回答"闸在不在拒"，
        #   这一格回答"拒完之后那条边通没通"。⛔ 两格不许合并（分母不同）。
        "kernel_rework": _krw_line(_krw),
        # 外部能力读数（第二十三轮）：**不落盘的读数不可审计**。
        "kernel_external": _kex,
        # 传输层 / 墙钟读数（第二十五轮）：重试链有没有超支预算，必须可事后审计。
        "kernel_transport": _ktx,
        "calibration": {"ok": cal.ok, "margin": cal.margin, "middle_n": cal.middle_n,
                        "evidence_id": (cal.evidence.evidence_id()
                                        if cal.evidence is not None else None)},
    })
    print(f"  读数已落盘 {s.paths.bench / 'runs.jsonl'}")

    # ── 答案本体：跑完之后写一次（与账本同理，轮内写会污染下一题）────────
    if _ans_log is not None:
        _w = _ans_log.save(
            run_id=_run_id,
            meta={"solver": label, "suite": suite.name, "repeat": repeat_n,
                  "caliber_sig": caliber.signature(), "budget_tag": tag})
        # ⚠️ 一句必须念出来的是 `max_chars`：它一旦停在 200 附近，
        #    就说明某处又偷偷截断了（DESIGN §42 四处截断的共同形态）。
        print(f"  答案已落盘 {_w['path']}  {_w['n_answers']} 条  "
              f"共 {_w['chars']} 字符  最长 {_w['max_chars']} 字符  "
              f"截断 {_w['truncated']} 条答案")
    return 0 if rep.trustworthy() else 1


def main() -> int:
    p = argparse.ArgumentParser(description="V1 —— 一个可测量的 agent 内核")
    p.add_argument("--goal", default="", help="任务目标")
    p.add_argument("--selftest", action="store_true", help="离线端到端自检")
    p.add_argument("--repeat", type=int, default=0, help="测量模式：重复次数（≥2）")
    p.add_argument("--sweep", default="", help="预算档扫描，如 1,2,4")
    p.add_argument("--budget", type=float, default=1.0, help="预算倍数")
    p.add_argument("--offline", action="store_true", help="强制离线（用确定性桩）")
    p.add_argument("--allow-shell", action="store_true", help="开启 shell 工具（谨慎）")

    # ── 外部锚层（V1 存在的第一理由：让"变好了"这句话有外部依据）──
    p.add_argument("--bench", action="store_true",
                   help="外部锚层模式：校准尺子 → 跑任务集 → 过口径闸")
    p.add_argument("--suite", default="", help="任务集 JSONL（默认 bench/tasks/coding_small.jsonl）")
    p.add_argument("--suite-dir", default="",
                   help="**跨领域模式**：跑一个目录下的每个 .jsonl 各一遍，并列汇总"
                        "（『通用』的可判定形式）。与 --suite 互斥。")
    p.add_argument("--scorer", default="", help="外部打分器可执行文件（stdin/stdout 走 JSON）")
    p.add_argument("--ledger", default="",
                   help="跨会话分数账本路径（默认 $V1_STATE_DIR/score_ledger.jsonl）")
    p.add_argument("--no-ledger", action="store_true",
                   help="本轮不读写分数账本（自我模型空着起跑，用于对照）")
    p.add_argument("--no-answers", action="store_true",
                   help="不落答案本体（默认落 `bench_out/answers.jsonl`）。"
                        "⛔ 关掉它就等于放弃「错在哪一行」的复核能力 —— 见 "
                        "DESIGN §42 与 bench/answer_store.py 的表")
    p.add_argument("--critique-file", default="",
                   help="评审账本路径（默认 state/critique.jsonl）。"
                        "打分器给的理由会落盘到这里，供**下一轮**读取")
    p.add_argument("--critique-scope", default="", choices=("", "off", "prev", "prev-self", "all"),
                   help="外部评审的**投喂口径**（缺省 = 跟随 --use-critique）："
                        "off=不投喂；"
                        "prev=只投喂**账本加载时**已有的条目（跨轮累积：上一轮的评审"
                        "给本轮，本轮新增的不给本轮，同轮各题条件仍然一致），"
                        "**且排除同题**（旧口径）；"
                        "prev-self=同上，但**放行同题**那条 —— 它讲的是上一轮该题的"
                        "答案与分数，不是自评分（跨轮自省；见 DESIGN §96）；"
                        "all=轮内也投递（等价 --use-critique，接线验证用）")
    p.add_argument("--critique-drop", default="",
                   help="**消融开关**（DESIGN §98）：从**快照副本**里丢弃这些题的评审"
                        "（逗号分隔，如 e2wi）。真账本照常落盘、磁盘上一条不少。"
                        "为什么需要它：`crit[-3:]` 按 task_id 字典序切尾三，丢一条"
                        "31 字的满分条目就能让窗口换成「全是真的、全是别人的失败」，"
                        "从而构造**块长逐字节相同**的等长安慰剂臂（V33）。"
                        "与 off 档同给视为冲突（它什么也不会做）。")
    p.add_argument("--use-reward", action="store_true",
                   help="把外部打分器的分数作为**只读**奖励写进记忆（DESIGN §17-5）")
    p.add_argument("--memory-mode", default="raw", choices=("raw", "abstract"),
                   help="本次允许读到记忆的哪一层（DESIGN §15.2e）："
                        "raw=原样读（含 episodic 原文）；"
                        "abstract=只读由 mw.abstractor 蒸馏出的自然语言经验")
    p.add_argument("--use-critique", action="store_true",
                   help="把账本里的评审投喂给内核（**接线验证用，不是能力测量**）。"
                        "轮内投喂会让后面的题不再是同一条件下的测量 —— repeat 的意义当场消失")
    p.add_argument("--solver", default="auto",
                   choices=["auto", "llm", "llm-direct", "oracle", "partial", "null"],
                   help="谁来答题：auto/llm=走完整内核（多段调用）；"
                        "llm-direct=模型直连答题（不经内核，测最细链路）；"
                        "oracle/partial/null=参照点")
    p.add_argument("--temperature", type=float, default=0.0,
                   help="真模型采样温度。**噪声实验的核心旋钮**：0=贪心（应当确定）")
    p.add_argument("--max-tokens", type=int, default=None,
                   help="单次调用 token 上限。不给 ⇒ 沿用 limits.llm_max_tokens"
                        "（env V1_LLM_MAX_TOKENS，默认 2048）；给了 ⇒ **覆盖内核**"
                        "（第四十五轮起真正进 Planner，此前只进口径签名 = 两套真相）。"
                        "思考型模型会先花 token 在 reasoning 上，"
                        "给太小会得到空 content（这是实测踩过的坑）")
    p.add_argument("--baseline", default="", help="基线文件（默认 $V1_BENCH_DIR/bench_baseline.json）")
    p.add_argument("--save-baseline", action="store_true", help="把本次读数写入基线（过口径闸）")
    p.add_argument("--compare", action="store_true", help="与基线比较并给出判定")
    p.add_argument("--allow-supersede", action="store_true",
                   help="口径变更时允许替换基线（旧值会另存 superseded）")
    p.add_argument("--no-external", action="store_true",
                   help="**不注册任何外部能力**（`agents.json` 视同不存在）。"
                        "对照实验的 OFF 臂用它：外部能力的存在本身就是一个变量，"
                        "不拔掉它，'开外部能力'与'多了一个工具'就分不开。")
    p.add_argument("--fresh", action="store_true",
                   help="每题新建内核（记忆不跨题）；同时进入口径签名")
    p.add_argument("--memory-dir", default="",
                   help="显式指定记忆目录（**记忆 = 可持有的实验变量**）："
                        "用于『先在一个套件上学、再到另一个套件上考』的泛化实验。"
                        "给了它就不再每题隔离（口径 scope=explicit_dir）。")
    p.add_argument("--state-dump", default="",
                   help="把每次 `_state()` 的产物追加到该文件（JSONL，**只读证据**）。"
                        "R1 的第二层用它：按块切开、查 `[语义记忆]` 之外有没有事实文本。"
                        "不给 ⇒ 内核一次都不写盘（零开销）。")
    p.add_argument("--loop", type=int, default=0, metavar="N",
                   help="**循环模式**：用同一套 solver / 尺子 / 口径连跑 N 轮，"
                        "把每轮读数写成一条能力曲线（≥3 轮才判趋势）。"
                        "曲线判据在 `mw/loop.py`：每轮校验尺子未被改动、"
                        "口径变了断线开新段、不可信点不参与趋势。")
    p.add_argument("--loop-out", default="",
                   help="曲线落盘文件（默认 $V1_STATE_DIR/curve.jsonl，追加写）。")
    p.add_argument("--noise-floor", type=float, default=None,
                   help="趋势判定用的噪声下限。**不给就不判趋势**（不默认 0）——"
                        "默认 0 等于『任何微小差值都算改进』。")
    args = p.parse_args()

    if args.bench:
        return cmd_bench(args)
    if args.repeat:
        if not args.goal:
            args.goal = DemoLLM.GOAL
        return cmd_measure(args)
    if args.selftest or not args.goal:
        return cmd_selftest(args)
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
