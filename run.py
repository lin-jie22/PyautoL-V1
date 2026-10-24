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
    if args.offline or not s.llm.usable():
        from mw.guard import Guard
        from mw.llm import StubLLM
        core = Core(s, llm=StubLLM(), critique_ledger=critique_ledger,
                    score_ledger=score_ledger, caliber_sig=caliber_sig,
                    memory_mode=mem_mode,
                    wire_external=not getattr(args, "no_external", False))
        core.guard = Guard(allow_shell=allow_shell, workspace=s.paths.workspace,
                           max_consecutive_failures=s.limits.max_consecutive_failures)
    else:
        core = Core(s, critique_ledger=critique_ledger,
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
                 states: list[tuple[int, str]] | None) -> int:
    """把内核本轮每次 `_state()` 的产物落到证据文件（第三十六轮 §17-7h）。

    ⛔ 为什么由 **runner** 写而不是内核写：内核不产生实验产物，也不该知道
       "这一轮是不是在跑实验"。它只把 state 留在内存里（有界），
       有 `--state-dump` 的调用方才取走 —— 没有就是零开销、零盘上痕迹。

    ⛔ 落盘的是**原文**，不做任何裁剪：R1 的第二层要按块切开比对，
       裁一刀就等于替判据先把可疑的部分删掉了。
    """
    if not path or not states:
        return 0
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for it, st in states:
            f.write(json.dumps({"task": task_id, "it": it, "state": st},
                               ensure_ascii=False) + "\n")
    return len(states)


def _kwm_zero() -> dict:
    """内核侧世界模型读数累加器的零点。"""
    return {"predictions": 0, "scored": 0, "hits": 0, "unscored": 0,
            "mismatches": 0, "mismatches_outcome": 0, "mismatches_text": 0,
            "text_compared": 0, "suppressed": 0,
            # —— 外部奖励通道：写了几条 / **读了几条** ——
            # ⛔ 只报"写入 N 条"而不报"读到几条"，正是"字段无消费者"：
            #    一个你永远看不出它有没有被用过的通道 = 装饰。
            "rewards_recorded": 0, "rewards_seen": 0, "rewards_skipped_self": 0,
            "rewards_rejected_source": 0, "rewards_rejected_missing": 0,
            # —— 读层：语义记忆检索到几条 / 其中几条属于抽象层（memory-mode=abstract 时）——
            "layer_recalled": 0, "layer_abstract": 0,
            "confusion": {"ok_ok": 0, "ok_fail": 0, "fail_ok": 0, "fail_fail": 0}}


#: ⛔ 累加键**由零点派生**，不许另写一份名单。
#: 本轮自踩：奖励通道的三个新字段没被搬进累加器（旧代码是一份硬编码的键名单），
#: 于是日志上"写了 5 条"而"读到 0 条"—— 一个你看不出有没有被用过的通道 = 装饰。
_KWM_NUM_KEYS: tuple[str, ...] = tuple(k for k in _kwm_zero() if k != "confusion")


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
              "skipped_no_budget"):
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
            "origin": {}, "by_reason": {"steps": 0, "wall": 0}}


def _kco_absorb(acc: dict, stats: dict) -> dict:
    """把一次内核运行的收尾读数并进累加器（就地修改并返回）。"""
    for k in ("triggered", "chars", "empty", "overrode_rejected", "same_as_rejected"):
        acc[k] = int(acc.get(k, 0)) + int(stats.get(k, 0) or 0)
    for k, v in (stats.get("origin") or {}).items():
        acc["origin"][k] = int(acc["origin"].get(k, 0)) + int(v or 0)
    for k, v in (stats.get("by_reason") or {}).items():
        acc["by_reason"][k] = int(acc["by_reason"].get(k, 0)) + int(v or 0)
    return acc


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


def _kwm_absorb(acc: dict, stats: dict, core: object = None) -> dict:
    """把一次内核运行的读数并进累加器（就地修改并返回）。

    ⚠️ `hit_rate` **不并** —— 它是比值，逐次平均会给出错的答案
    （100 次全对 + 1 次全错，平均 0.5；真值是 100/101）。只能由总数重算。
    ⚠️ 缺失的键按 0 并，**不炸**：老读数文件里没有 `scored` 是正常的，
    但"没有这个字段" ≠ "这个字段是 0" —— 所以 `hit_rate` 在 `scored==0` 时
    返回 `None`，让"没量到"在读数里显形。
    """
    for k in _KWM_NUM_KEYS:
        acc[k] = int(acc.get(k, 0)) + int(stats.get(k, 0) or 0)
    # 层读数住在 `Core.memory_layer_view` 里，**不在** worldmodel stats 里。
    # ⛔ 这里由 absorb **主动去 core 上取**，而不是要求每个调用点记着传：
    #    "记着传"这种约定在本项目已经失败过两次（详见 §15.2 的记录）。
    if core is not None:
        view = getattr(core, "memory_layer_view", None) or {}
        for kk in ("layer_recalled", "layer_abstract"):
            key = {"layer_recalled": "recalled", "layer_abstract": "abstract"}[kk]
            acc[kk] = int(acc.get(kk, 0)) + int(view.get(key, 0) or 0)
    cm = acc.setdefault("confusion", {})
    for k in ("ok_ok", "ok_fail", "fail_ok", "fail_fail"):
        cm[k] = int(cm.get(k, 0)) + int((stats.get("confusion") or {}).get(k, 0) or 0)
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
    print(json.dumps({"status": out.status, "answer": out.answer,
                      "reason": out.reason, "iterations": out.iterations,
                      "stats": out.stats}, ensure_ascii=False, indent=2))
    return 0 if out.status == "done" else 1


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
    _kco = _kco_zero()      # 收尾兜底（第二十二轮）：与 _kwm 同源的落盘读数
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
    _crit_path = (Path(args.critique_file).expanduser() if args.critique_file
                  else (root / "state" / "critique.jsonl"))
    _crit = CritiqueLedger(_crit_path)
    _crit_loaded = _crit.load()
    _crit_for_kernel = _crit if args.use_critique else None
    if _crit_loaded or args.use_critique:
        print(f"  评审账本  {_crit_path.name}  载入 {_crit_loaded} 条"
              f"  投喂={'开' if args.use_critique else '关（默认：保住测量一致性）'}")

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
                        _kco_absorb(_kco, out.stats.get("closeout") or {})
                        _kex_absorb(_kex, out.stats.get("external") or {})
                        _kex["tasks"] += 1
                        _ktx_absorb(_ktx, out.stats.get("transport") or {})
                        # ★state 证据出口（§17-7h）：只在 `--state-dump` 时写盘
                        _dump_states(getattr(args, "state_dump", ""),
                                     t.id, getattr(c, "state_log", None))
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
                _kco_absorb(_kco, out.stats.get("closeout") or {})
                _kex_absorb(_kex, out.stats.get("external") or {})
                _kex["tasks"] += 1
                _ktx_absorb(_ktx, out.stats.get("transport") or {})
                # ★state 证据出口（§17-7h）：同一内核连跑多题，`state_log` 在
                # `run()` 开头清空 ⇒ 每次 dump 出来的都是**本题**的 state。
                _dump_states(getattr(args, "state_dump", ""),
                             t.id, getattr(_core, "state_log", None))
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
                 f" 拒={_kw.get('rewards_rejected_source', 0)}"
                 f"+{_kw.get('rewards_rejected_missing', 0)}]"
                 if (_kw.get('rewards_recorded') or _kw.get('rewards_seen')
                     or _kw.get('rewards_skipped_self')) else "")
              # ⛔ 抽象臂必须把"检索到几条 / 其中几条是抽象层"念出来。
              #    只念 mode 的人分不清" abstract 层有用"和"abstract 层是空的"——
              #    后者会让整个实验变成一场没有变量的空转（第十五轮自踩过一次）。
              + (f" 记忆层[mode={getattr(args, 'memory_mode', 'raw')}"
                 f" 检索={_kw['layer_recalled']}"
                 f" 抽象={_kw['layer_abstract']}]"
                 if _kw["layer_recalled"] or _kw["layer_abstract"] else "")
              + (f" ⚠️未检验={_kw['unscored']}" if _kw["unscored"] else ""))

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

    append_jsonl(s.paths.bench / "runs.jsonl", {
        # ── 独立运行的身份证（第七十二轮，§36）────────────────────────────
        # 为什么必须落盘：噪声下限得在**独立进程之间**量，而"这次运行是不是
        # 另一次运行"在这之前**在数据里不存在** —— 只能靠人记"我跑了几次"。
        # 于是 `--repeat 2` 的两次重复与"跑了两个进程"在证据里长得一模一样，
        # 而前者系统性低估噪声（DESIGN §35 实测 0.2619 vs 0.0000）。
        # ⛔ pid 会重用，所以加上写盘时刻：`run_id` 才是判据，pid 只是它的一半。
        "run_id": f"{os.getpid()}@{int(time.time())}",
        "solver": label, "budget_tag": tag, "repeat": repeat_n,
        "caliber_sig": caliber.signature(), "caliber": caliber.as_dict(),
        "measurement": measurement_to_dict(m), "report": rep.as_dict(),
        # 内核侧读数（世界模型的**分辨力**）。不落盘的读数不可审计。
        "kernel_worldmodel": _kw,
        # 收尾兜底读数（第二十二轮）：**不落盘的读数不可审计**。
        "kernel_closeout": _kco,
        # 外部能力读数（第二十三轮）：**不落盘的读数不可审计**。
        "kernel_external": _kex,
        # 传输层 / 墙钟读数（第二十五轮）：重试链有没有超支预算，必须可事后审计。
        "kernel_transport": _ktx,
        "calibration": {"ok": cal.ok, "margin": cal.margin, "middle_n": cal.middle_n,
                        "evidence_id": (cal.evidence.evidence_id()
                                        if cal.evidence is not None else None)},
    })
    print(f"  读数已落盘 {s.paths.bench / 'runs.jsonl'}")
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
    p.add_argument("--critique-file", default="",
                   help="评审账本路径（默认 state/critique.jsonl）。"
                        "打分器给的理由会落盘到这里，供**下一轮**读取")
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
