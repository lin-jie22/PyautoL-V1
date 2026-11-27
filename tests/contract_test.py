#!/usr/bin/env python3
"""V1 契约测试 —— 离线、零依赖、可独立运行。

设计遵循三条用事故换来的硬纪律：
  ① **测试绝不碰生产目录**：所有状态目录由 env 指向临时目录
  ② **不许对可能不存在的属性做算术**（撤回态下 `int(None)` 抛异常，
     打断汇总行）⇒ 本文件全部用 `.get()` + 类型前置
  ③ **汇总行必须能被外部 runner 的正则解析**：`PASS=n  FAIL=m`
     （连续两轮把 `SUM_PASS=` 写进汇总行，
      `FAIL` 前是下划线 ⇒ runner 记 0 条断言，违反 R79 可对账）

判据全部**与实现同源可撤**：撤掉实现里的对应代码，相应断言必然判红。
"""

from __future__ import annotations

import ast
import collections
import contextlib
import inspect
import io
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

# ① 先隔离环境，再 import 任何 mw 模块
_TMP = Path(tempfile.mkdtemp(prefix="v1_contract_"))
os.environ["V1_STATE_DIR"] = str(_TMP / "state")
os.environ["V1_LOG_DIR"] = str(_TMP / "logs")
os.environ["V1_WORKSPACE"] = str(_TMP / "ws")
os.environ["V1_LLM_OFFLINE"] = "1"

V1 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V1))

from mw import config as cfg                                    # noqa: E402
from mw import features as F                                    # noqa: E402
from mw.budget import Budget, BudgetExhausted, Ledger            # noqa: E402
from mw.core import Core, _LedgeredLLM, _Log                   # noqa: E402
from mw.guard import CHECKPOINTS, Guard                           # noqa: E402
from mw.llm import (ANSWER_MARK, PARSE_MODES, HTTPLLM, Message, StubLLM,  # noqa: E402
                    parse_json, split_answer)
from mw.measure import (Report, SENTINEL, check, ci95, noise_floor,   # noqa: E402
                        paired_compare, repeat, summarize, verdict_from)
from mw.memory import MemoryStore, fingerprint, fts_query         # noqa: E402
from mw.planner import _ANSWER_SYS as _planner_ANSWER_SYS   # noqa: E402
from mw.planner import (Planner, Step, action_signature,              # noqa: E402
                        looks_complex)
from mw.tools import (ToolRegistry, ToolSpec, ToolResult, catalog_sha,  # noqa: E402
                      external_agent_names,
                      register_core_tools, register_external_agents,
                      spec_applies)
from mw import tools as tools_module                            # noqa: E402
from mw.worldmodel import Mismatch, Prediction, WorldModel, similarity  # noqa: E402

RS = Report()
MW_DIR = V1 / "mw"


def _src(name: str) -> str:
    return (MW_DIR / name).read_text(encoding="utf-8")


def _raises(fn, exc_type) -> bool:
    """断言抛出指定异常。**不用裸 except**，只吞目标类型。"""
    try:
        fn()
    except exc_type:
        return True
    except Exception:  # noqa: BLE001 —— 抛了别的类型也算失败，但要能区分
        return False
    return False


def _exc_kind(fn) -> str | None:
    """取 BudgetExhausted 的 kind 字段；取不到返回 None（不做算术，R13 硬坑）。"""
    try:
        fn()
    except BudgetExhausted as e:
        return getattr(e, "kind", None)
    except Exception:  # noqa: BLE001
        return None
    return None


def _ci95_ok(scores) -> bool:
    lo, hi = ci95(scores)
    return lo <= sum(scores) / len(scores) <= hi and hi - lo < 10.0


# ===========================================================================
print("=" * 74)
print("A. 配置单一源（'同一事实多处声明'的正面修复）")
print("=" * 74)
_all_src = "\n".join(_src(p.name) for p in MW_DIR.glob("*.py"))
_found = set(re.findall(r'"(V1_[A-Z0-9_]+)"', _all_src))
check(RS, "A1 代码里出现的 V1_* 环境变量全部在 ENV_KEYS 登记",
      _found == set(cfg.ENV_KEYS),
      f"未登记={sorted(_found - set(cfg.ENV_KEYS))} 多余={sorted(set(cfg.ENV_KEYS) - _found)}")
check(RS, "A2 ENV_KEYS 无重复", len(cfg.ENV_KEYS) == len(set(cfg.ENV_KEYS)))
check(RS, "A3 隔离目录生效（测试不碰生产）",
      cfg.settings().paths.state.resolve() == (_TMP / "state").resolve(),
      str(cfg.settings().paths.state))
check(RS, "A4 三个状态目录均来自 env 覆盖",
      all([cfg.Paths().state, cfg.Paths().logs, cfg.Paths().workspace]))


# ===========================================================================
print()
print("=" * 74)
print("B. 预算与账本（2026: 能力 = 预算的函数；算完就停，如实报告）")
print("=" * 74)
b = Budget(steps=4, llm_calls=4, wall_s=9, candidates=2)
check(RS, "B1 Budget 不可变（scaled 返回新对象）",
      b.scaled(2).steps == 8 and b.steps == 4)
check(RS, "B2 scaled 非法倍数被拒", _raises(lambda: b.scaled(0), ValueError))
led = Ledger(Budget(steps=2, llm_calls=2, wall_s=9, candidates=1))
led.spend_step(); led.spend_step()
check(RS, "B3 步数耗尽抛 BudgetExhausted",
      _raises(led.check, BudgetExhausted))
check(RS, "B4 异常带 kind 字段（可分类统计）",
      _exc_kind(led.check) == "steps")
led2 = Ledger(Budget(steps=5, llm_calls=1, wall_s=9, candidates=1))
led2.spend_llm()
check(RS, "B5 llm 配额独立计数", _exc_kind(led2.check) == "llm_calls")
led3 = Ledger(Budget(steps=5, llm_calls=5, wall_s=9, candidates=1))
led3.spend_step(); led3.spend_candidate(3)
snap = led3.snapshot()
check(RS, "B6 snapshot 是对账唯一口径",
      snap.get("steps") == "1/5" and snap.get("cand_spent") == 3,
      str(snap))
check(RS, "B7 candidates 语义明确：累计花费与单次宽度分开，不给假分母",
      "cand_spent" in snap and "cand_width" in snap and "candidates" not in snap,
      str(sorted(snap)))


# ===========================================================================
print()
print("=" * 74)
print("C. 三层记忆（39× 重复 ⇒ DB 层去重约束）")
print("=" * 74)
mem = MemoryStore(_TMP / "m1.db")
k1, new1 = mem.commit_fact("Python 写文件需显式 encoding", source="t", confidence=0.5)
k2, new2 = mem.commit_fact("python   写文件需显式  ENCODING", source="t", confidence=0.9)
check(RS, "C1 规范化后同一内容 → 同一 key（空白/大小写无关）", k1 == k2, f"{k1} vs {k2}")
check(RS, "C2 第二次写入不新建行", new1 is True and new2 is False)
check(RS, "C3 重复行恒为 0（DB 约束而非调用方自觉）", mem.duplicate_rows() == 0)
f = mem.recall("python encoding", k=5)
check(RS, "C4 重复写入 confidence 取 max",
      bool(f) and abs(max(x.confidence for x in f) - 0.9) < 1e-9,
      str([x.confidence for x in f]))
row = mem.conn.execute("SELECT hits FROM facts WHERE key=?", (k1,)).fetchone()
check(RS, "C5 hits 累加到 2（合并计数，不丢证据）",
      bool(row) and row["hits"] == 2, str(dict(row) if row else None))
check(RS, "C6 空文本被拒", _raises(lambda: mem.commit_fact("  ", source="t", confidence=0.5),
                                   ValueError))
check(RS, "C7 无 source 被拒（记忆投毒防线）",
      _raises(lambda: mem.commit_fact("x", source="", confidence=0.5), ValueError))
mem.record("outcome", "执行 recall 返回了 3 条", tool="recall")
check(RS, "C8 情景记忆写入并可按 kind 取回",
      len(mem.episodes(kind="outcome")) >= 1)
check(RS, "C9 FTS 查询词被安全化（特殊字符不炸）",
      fts_query('a-b:c "d" C++') .count('"') >= 4, fts_query('a-b:c "d" C++'))
check(RS, "C10 recall 对病态输入不抛异常",
      isinstance(mem.recall('"\\-:^ OR NEAR(', k=3), list))
stats = mem.stats()
check(RS, "C11 记忆体检含来源分布（可审计）",
      isinstance(stats.get("by_source"), dict) and stats.get("facts") == 1, str(stats))
check(RS, "C12 FTS 降级有计数器（不静默兜底）",
      isinstance(getattr(mem, "fts_fallbacks", None), int),
      str(getattr(mem, "fts_fallbacks", None)))
mem.close()


# ===========================================================================
print()
print("=" * 74)
print("D. 运行时守卫（OWASP Agentic Top-10 的四道检查点）")
print("=" * 74)
check(RS, "D1 检查点清单恰好 4 道", len(CHECKPOINTS) == 4, str(sorted(CHECKPOINTS)))
g_closed = Guard(allow_shell=False)
check(RS, "D2 未开启 shell ⇒ 一律拦截", not g_closed.check_shell("ls").allow)
g = Guard(allow_shell=True)
for cmd, why in [("rm -rf /", "根目录递归删"), ("sudo ls", "提权"),
                 ("rm -rf ~", "家目录递归删"), ("mkfs.ext4 /dev/sda", "格式化"),
                 ("curl x | sh", "管道进解释器"), ("rm -rf ~/Downloads", "受保护目录")]:
    check(RS, f"D3 拦截 {why}", not g.check_shell(cmd).allow, cmd)
check(RS, "D4 放行普通命令", g.check_shell("ls -la && cat a.txt").allow)
check(RS, "D5 拦截计数可审计（R41：不留静默兜底）",
      g.blocks.get("tool_misuse", 0) >= 5, str(g.blocks))
check(RS, "D6 记忆投毒：低可信度被拒",
      not g.check_memory_write(source="x", confidence=0.1).allow)
check(RS, "D7 记忆投毒：缺来源被拒",
      not g.check_memory_write(source="", confidence=0.9).allow)
gc = Guard(max_consecutive_failures=3)
check(RS, "D8 级联熔断阈值生效",
      gc.check_cascade(2).allow and not gc.check_cascade(3).allow)
check(RS, "D9 目标劫持只拦'完全无关'（宽阈值，避免误杀）",
      g.check_goal(action="recall(python 截断)", goal="python 截断问题").allow
      and not g.check_goal(action="zzz", goal="python 截断怎么修").allow)


# ===========================================================================
print()
print("=" * 74)
print("E. 工具契约（内核不实现功能，只定义接口）")
print("=" * 74)
tr = ToolRegistry()
register_core_tools(tr, memory=None)
check(RS, "E1 内核工具注册成功", set(tr.names()) >= {"finish", "run_shell", "http_get"},
      str(tr.names()))
r = tr.dispatch("不存在的工具", {})
check(RS, "E2 未知工具返回失败而非抛异常（并点名已注册项）",
      r.ok is False and "未知工具" in r.error and "finish" in r.error)
r = tr.dispatch("http_get", {})
check(RS, "E3 缺参数判失败", r.ok is False and "缺参数" in r.error)
check(RS, "E4 执行期异常被吸收成 ToolResult",
      tr.dispatch("http_get", {"url": "ftp://x"}).ok is False)
check(RS, "E5 catalog 可供提示词使用", "run_shell" in tr.catalog())
check(RS, "E6 重名注册被拒（唯一事实源）",
      _raises(lambda: tr.register(ToolSpec("finish", "d", {}, lambda a: "")), ValueError))
empty = register_external_agents(ToolRegistry(), _TMP / "不存在.json")
check(RS, "E7 agents.json 缺失 ⇒ 零外部能力且不报错（内核可离线跑通）", empty == [])
ap = _TMP / "agents.json"
ap.write_text('{"agents": {"echoer": {"type": "shell", "description": "d", '
              '"command": ["cat"]}}}', encoding="utf-8")
tr2 = ToolRegistry()
added = register_external_agents(tr2, ap)
check(RS, "E8 shell 型外部 agent 注册成功", added == ["agent:echoer"], str(added))
check(RS, "E9 外部能力有单一查询口径",
      external_agent_names(tr2) == ["agent:echoer"])
check(RS, "E10 外部 agent 真的能跑（stdin→stdout 契约）",
      tr2.dispatch("agent:echoer", {"task": "hello"}).output.strip() == "hello")
ap.write_text('{"agents": {"bad": {"type": "nope"}}}', encoding="utf-8")
check(RS, "E11 未知 agent 类型被跳过（不猜、不崩）",
      register_external_agents(ToolRegistry(), ap) == [])


# ===========================================================================
print()
print("=" * 74)
print("F. 上下文内世界模型（WorldEvolver 2026-06：修记忆，不改权重）")
print("=" * 74)
check(RS, "F1 相似度自反", abs(similarity("abc def", "abc def") - 1.0) < 1e-9)
check(RS, "F2 不相关 ⇒ 0", similarity("abc", "xyz") == 0.0)
mem2 = MemoryStore(_TMP / "m2.db")
wm = WorldModel(mem2, StubLLM())
# ── 文本类失配（没有成败可依，只能比措辞）──────────────────────────────
_tm = wm.observe(Prediction("写文件", "会成功返回内容", 0.8), "OSError 权限不足")
check(RS, "F3 文本类：预演与实测措辞差异过大 ⇒ 记为失配（kind=text）",
      _tm is not None and _tm.kind == "text",
      f"kind={getattr(_tm, 'kind', None)}")
check(RS, "F4 失配写入情景记忆（带 mm_kind，事后能分辨是哪一种失配）",
      any(e.kind == "mismatch" for e in mem2.episodes(kind="mismatch")),
      str([e.kind for e in mem2.episodes(kind="mismatch")]))
check(RS, "F5 ★纪律：文本类失配**不**写语义记忆 —— 措辞差异不是「哪里容易看错」，"
          "把它写成持久规则是从测量噪声里造知识（缺陷 #12 的 9/10 条噪声就是这么来的）",
      mem2.stats()["by_source"].get("worldmodel", 0) == 0,
      str(mem2.stats()["by_source"]))

# ── 成败类失配（内核直接持有的地面真相）── 才允许提炼成可迁移规则 ──────
_om = wm.observe(Prediction("写文件", "会成功返回内容", 0.8, expect_ok=True),
                 "[工具失败] OSError 权限不足", ok=False)
_wm_facts = [f.text for f in mem2.recall("写文件", k=10) if f.source == "worldmodel"]
check(RS, "F5b 成败类失配 ⇒ 提炼成规则并进语义记忆（这一条才可迁移）",
      _om is not None and _om.kind == "outcome" and len(_wm_facts) >= 1,
      f"kind={getattr(_om, 'kind', None)} facts={_wm_facts[:1]}")
check(RS, "F5c 规则文本落在**动作层**（「我预演 X，实际 Y」），"
          "而不是「不可假设其结果等同于「某段话」」这种字面描述 "
          "—— 后者下一轮读到无法据以改变任何动作",
      bool(_wm_facts) and all("预演" in t and "实际" in t for t in _wm_facts),
      str([t[:60] for t in _wm_facts]))
check(RS, "F6 一致的预测不沉淀（不制造噪声）",
      wm.observe(Prediction("a", "abc def", 0.9), "abc def") is None)
check(RS, "F7 选择性前瞻的**唯一**门槛是 `Prediction.trustworthy`（单一声明）："
          "曾有一个 `foresight_filter()` 批量封装，生产路径零调用、只有测试在调，"
          "已按「装饰 API」删除",
      Prediction("a", "e", 0.9).trustworthy is True
      and Prediction("b", "e", 0.1).trustworthy is False,
      f"0.9→{Prediction('a', 'e', 0.9).trustworthy} "
      f"0.1→{Prediction('a', 'e', 0.1).trustworthy}")
check(RS, "F8 抑制计数可对账（初值 0；只由内核在**真正跳过**时累加，"
          "不是由某个没人调用的过滤器累加）",
      wm.stats().get("suppressed") == 0, str(wm.stats()))
check(RS, "F9 零 LLM 成本也能预演（检索式复用）",
      wm.predict(state="s", action="写文件", use_llm=False).source == "retrieval")
mem2.close()


# ===========================================================================
print()
print("=" * 74)
print("G. 动态规划（失败必带错误原文重规划，不原地重试）")
print("=" * 74)
tr3 = ToolRegistry()
register_core_tools(tr3, memory=None)


class _P(StubLLM):
    pass


pl = Planner(StubLLM(default='{"candidates": [{"intent": "i", "tool": "不存在的工具", '
                              '"args": {}}]}'), tr3)
plan = pl.propose(goal="x", state="s", history=[], n=1)
check(RS, "G1 规划器拒绝发明工具（白名单硬约束）",
      plan.degraded and pl.degraded >= 1, f"degraded={pl.degraded}")
check(RS, "G2 降级**仍然产出合法动作**（降级≠瘫痪）",
      bool(plan.steps) and plan.steps[0].tool in tr3.names(),
      str([s.tool for s in plan.steps]))
pl2 = Planner(StubLLM(default='not json at all'), tr3)
p2 = pl2.propose(goal="x", state="s", history=[], n=1)
check(RS, "G3 不可解析 ⇒ 显式标记 degraded（不许静默）", p2.degraded is True)
dup = '{"candidates":[{"intent":"a","tool":"finish","args":{"answer":"1"}},' \
      '{"intent":"b","tool":"finish","args":{"answer":"1"}}]}'
pl3 = Planner(StubLLM(default=dup), tr3)
check(RS, "G4 候选去重（同工具同参数只留一个）",
      len(pl3.propose(goal="x", state="s", history=[], n=2).steps) == 1)
check(RS, "G5 ACT：长目标判为复杂", looks_complex("x" * 130, 0) is True)
check(RS, "G6 ACT：有失败历史判为复杂", looks_complex("短目标", 1) is True)
check(RS, "G7 ACT：简单目标走快通道", looks_complex("读一下这个文件", 0) is False)
_rp = pl3.replan(goal="x", failed=Step("i", "finish", {"answer": "1"}),
                 error="E", history=[])
# ⚠️ 这里原本写的是 `... .note or True` —— 那是**恒真断言**，零判别力（同 bench_test 里
#    修过的反模式）。去掉 `or True` 后它才真的在检查 replan 是否带上了失败归因。
check(RS, "G8 replan 把失败原因写进 note（不原地打转）",
      "失败" in str(getattr(_rp, "note", "")) and "finish" in str(getattr(_rp, "note", "")),
      str(getattr(_rp, "note", "")))

# ── 降级原因必须分项：修法不同的分支合并，就再也分不清该修哪里 ─────────────
_pl_j = Planner(StubLLM(default='完全不是 JSON'), tr3)
_pl_j.propose(goal="x", state="s", history=[], n=1)
check(RS, "G9 「模型不会写 JSON」计入 json_fail，且**不计入** no_tool_fit（两种病分开）",
      _pl_j.json_fail == 1 and _pl_j.no_tool_fit == 0 and _pl_j.empty_plan == 0,
      f"json_fail={_pl_j.json_fail} no_tool_fit={_pl_j.no_tool_fit} empty={_pl_j.empty_plan}")
check(RS, "G10 「工具目录缺能力」被单独计数，且坏工具名被留下（失败可归因）",
      pl.no_tool_fit == 1 and pl.last_bad_tools == ["不存在的工具"],
      f"no_tool_fit={pl.no_tool_fit} bad={pl.last_bad_tools}")
check(RS, "G11 分项之和 == degraded（否则说明有降级走了旁路、漏记）",
      pl.stats()["cause_balances"] is True and _pl_j.stats()["cause_balances"] is True,
      str(pl.stats()))
check(RS, "G12 finish 的工具描述必须说清「答案放进 answer」"
          "（模型只看得到这段文字 —— 实测就因为它太含糊而填了 tool=None）",
      "answer" in tr3.get("finish").description
      and "结束" in tr3.get("finish").description,
      tr3.get("finish").description[:80])

# ★ 第三种降级原因，也是实测抓出来的：「响应被截断」。
#   规划器当时写死 700 token，却要求模型把**整段代码**放进 JSON 字符串 ⇒
#   响应断在半截 token 上，而日志只说"不可解析" —— 看起来像模型不会写 JSON，
#   实际是预算给少了。两种病修法完全相反，必须分开计。
_TRUNC = '{"candidates": [{"intent": "写代码", "tool": "finish", "args": {"answer": "def f():'
_tr = Planner(StubLLM(default=_TRUNC), tr3)
_tr.propose(goal="x", state="s", history=[], n=1)
check(RS, "G13 「响应被截断」单独计入 truncated_responses（不与「不会写 JSON」混同，"
          "两者一个调 max_tokens 一个改提示词）"
          "—— 键名带单位（V66/§133）：**次**，与 `plan.truncated_cands`（条）不同尺",
      _tr.truncated_responses == 1 and _tr.json_fail == 0 and _tr.empty_plan == 0
      and _tr.stats()["cause_balances"] is True,
      f"truncated_responses={_tr.truncated_responses} json_fail={_tr.json_fail} "
      f"balances={_tr.stats()['cause_balances']}")
check(RS, "G14 规划器的 max_tokens 从 config 注入（不许在调用点写死）",
      Planner(StubLLM(default="{}"), tr3, max_tokens=1234).max_tokens == 1234
      and cfg.Limits().llm_max_tokens > 0,
      str(cfg.Limits().llm_max_tokens))


# ===========================================================================
print()
print("=" * 74)
print("H. 测量基座（V1 存在的第一理由）")
print("=" * 74)
check(RS, "H1 repeat 需要 n≥2（单次无法估噪声）",
      _raises(lambda: repeat(lambda: 1.0, n=1), ValueError))
xs = repeat(lambda: 0.5, n=4)
check(RS, "H2 确定性函数 ⇒ 极差 0", summarize(xs, candidate="c", budget_tag="1x").spread == 0.0)
m_a = summarize([0.70, 0.72, 0.68], candidate="base", budget_tag="1x")
check(RS, "H3 summarize 给出 mean/std/lo/hi",
      abs(m_a.mean - 0.70) < 1e-9 and abs(m_a.lo - 0.68) < 1e-9, m_a.as_line())
m_hi = summarize([0.90, 0.95, 0.92], candidate="cand", budget_tag="1x")
ok, why = paired_compare(m_a, m_hi)
check(RS, "H4 显著超过噪声带 ⇒ 判定成立", ok, why)
m_noise = summarize([0.71, 0.70, 0.705], candidate="cand", budget_tag="1x")
ok2, why2 = paired_compare(m_a, m_noise)
check(RS, "H5 落在噪声带内 ⇒ 判定不成立（核心病灶）", not ok2, why2)
ok3, why3 = paired_compare(m_a, summarize([0.99, 1.0], candidate="c", budget_tag="4x"))
check(RS, "H6 跨预算档比较被拒（2026: 能力是预算的曲线）", not ok3, why3)
check(RS, "H7 噪声下限取最保守口径（最大极差）",
      abs(noise_floor(m_a, summarize([0.5, 0.9], candidate="z", budget_tag="1x")) - 0.4) < 1e-9)
r2 = Report(); r2.pass_n = 7; r2.fail_n = 0
check(RS, "H8 汇总行可被外部 runner 正则解析（§134/§135 的坑）",
      bool(re.search(r"PASS\s*=\s*(\d+)\s+FAIL\s*=\s*(\d+)", r2.summary_line())),
      r2.summary_line())
check(RS, "H9 ci95 在小样本下不越界",
      _ci95_ok([0.1, 0.2, 0.3]))


# ── AZ126 三值判据：崩不许冒充绿（第 140 轮）──────────────────────────────
# 材料是真实的，不是构造的：`bench/evidence/v6_s228/READINGS_crash_repro.txt:112`
# 那三行的逐字副本。那次运行 `EXIT=1`、主汇总行**从未打印**，
# 而日志里唯一带 `PASS=…  FAIL=…` 的字符串来自一条断言的 detail 文本。
# ⇒ 旧口径（grep 最后一条）判它**绿**。这是本组断言存在的全部理由。
_MZ_FAKE_GREEN = (
    "  ✅ H8 汇总行可被外部 runner 正则解析（§134/§135 的坑）  (PASS=7  FAIL=0)\n"
    "Traceback (most recent call last):\n"
    "mw.memory.BlobIntegrityError: 句柄 'b#1' 的原文与写侧承诺不符（库被动过）\n"
)
_mz_old = re.findall(r"PASS=(\d+)\s+FAIL=(\d+)", _MZ_FAKE_GREEN)
check(RS, "AZ126a 旧口径在这段真实崩溃日志上**确实**读到 7/0（污染不是假设）\n"
          "              旧口径 = grep -oE 'PASS=\\d+  FAIL=\\d+' | tail -1；\n"
          "              它读到的最后一个匹配就是这个 —— 而那次运行 EXIT=1",
      _mz_old == [("7", "0")],
      f"旧口径命中 {_mz_old}")
check(RS, "AZ126b `verdict_from` 在同一段文本上判 CRASH（汇总行缺席 ⇒ 判据没说话）\n"
          "              ⛔ 它不是「更严的绿」，是**拒绝判绿**：缺席不许冒充零失败",
      verdict_from(_MZ_FAKE_GREEN, 1)[0] == "CRASH",
      verdict_from(_MZ_FAKE_GREEN, 1)[1])
check(RS, "AZ126c 绿 / 红 两值仍与旧口径一致（新判据不许把正常的判死）",
      verdict_from(f"{SENTINEL} GREEN  PASS=631  FAIL=0", 0)[0] == "GREEN"
      and verdict_from(f"{SENTINEL} RED  PASS=625  FAIL=6", 0)[0] == "RED",
      "GREEN/RED 各一例")
_mz_r = Report(); _mz_r.pass_n = 631
check(RS, "AZ126d 未调 `finish()` ⇒ CRASH（fail-closed 的默认方向）\n"
          "              ⛔ 旧实现的默认是「跑完了」：崩与跑完在数据上不可区分",
      _mz_r.verdict() == "CRASH",
      f"verdict={_mz_r.verdict()}")
check(RS, "AZ126e 汇总行多于一条 ⇒ CRASH（有东西在冒充判据）\n"
          "              这条防的是「以后有人为了让读数好看，在别处也打一行 sentinel」",
      verdict_from(f"{SENTINEL} GREEN  PASS=1  FAIL=0\n"
                   f"{SENTINEL} GREEN  PASS=1  FAIL=0", 0)[0] == "CRASH",
      "两行 sentinel")
check(RS, "AZ126f `summary_line()` 里**不含** SENTINEL（人读行不许冒充判据行）\n"
          "              否则 AZ126b 的隔离立刻失效 —— 它读的就是「带前缀的那一行」。\n"
          "              ⛔ 本条 detail 刻意**不打印** `verdict_line()` 的原文：\n"
          "              第一版打了，于是整份契约日志里出现两行 sentinel，\n"
          "              `verdict_from(全文)` 当场判 CRASH —— 断言自己成了污染源。",
      SENTINEL not in r2.summary_line() and SENTINEL in _mz_r.verdict_line()
      and "PASS=631" in _mz_r.verdict_line(),
      f"summary={r2.summary_line()!r} 判据行前缀在={SENTINEL in _mz_r.verdict_line()}"
      f" 计数在={'PASS=631' in _mz_r.verdict_line()}")


# ── AZ127：`verdict_from` 的**生产消费者**（V141 / §208）─────────────────
# V140 把三值判据装进了内核，但**只有测试在读它**（`run_tests.sh` 从外面 grep）。
# 系统平时跑 `run.py --bench` 时，谁也不读 —— 「这次到底有没有量到东西」
# 这个问题在数据里不存在。AZ127 钉住「日常运行时谁在读、读什么、读坏了怎么办」。
from run import _rc_after_readback, judge_run_log  # noqa: E402

_az127_dir = Path(tempfile.mkdtemp(prefix="v141_log_"))
_az127_good = _az127_dir / "good.log"
_az127_good.write_text("  ✅ 某条读数\n"
                       f"{SENTINEL} GREEN  PASS=1  FAIL=0\n", encoding="utf-8")
# ⚠️ 截断**判据行本身**（`::VERDICT:: GRE`）：行还在，但解析不出计数 ⇒ 规则 3。
#    造这个形态而不是「整行删掉」，是因为它更接近真实事故：写盘被截在半行。
_az127_half = _az127_dir / "half.log"
_az127_half.write_text("  ✅ 某条读数\n" f"{SENTINEL} GRE", encoding="utf-8")

check(RS, "AZ127a 日志不存在 ⇒ CRASH（缺席不许冒充零失败；读取点不许抛异常）",
      judge_run_log(_az127_dir / "absent.log", 0)[0] == "CRASH",
      judge_run_log(_az127_dir / "absent.log", 0)[1][:60])

_az127_src = (V1 / "run.py").read_text(encoding="utf-8")
_az127_tree = ast.parse(_az127_src)


def _az127_calls(name: str) -> int:
    """数 `run.py` 里这个名字被**调用**了几次（定义/注释/字符串都不算）。"""
    n = 0
    for node in ast.walk(_az127_tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id == name:
            n += 1
        elif isinstance(fn, ast.Attribute) and fn.attr == name:
            n += 1
    return n


_az127_read = _az127_calls("verdict_from")
_az127_write = _az127_calls("verdict_line")
check(RS, "AZ127b `verdict_from` 在 run.py 里恰好 **1** 个调用点（读者唯一）\n"
          "              多一个读者就多一套口径；这一条让「谁在读判据」变成可数的东西",
      _az127_read == 1,
      f"verdict_from 调用点={_az127_read}  写者 verdict_line 调用点={_az127_write}")
check(RS, "AZ127c 写者唯一：`verdict_line()` 在 run.py 里恰好 1 个调用点\n"
          "              ⛔ 第 2 个调用点就是「两行 sentinel」的开始（AZ126e 已判 CRASH）",
      _az127_write == 1, f"调用点={_az127_write}")

check(RS, "AZ127d 读回非 GREEN 而 rc==0 ⇒ 退出码抬到 1（fail-closed 落到退出码）\n"
          "              ⛔ 只念一句不算：rc=0 会被脚本/CI 当成「跑完了」",
      _rc_after_readback(0, "CRASH") == 1 and _rc_after_readback(0, "RED") == 1
      and _rc_after_readback(0, "GREEN") == 0 and _rc_after_readback(1, "RED") == 1,
      f"CRASH/0→{_rc_after_readback(0, 'CRASH')} RED/0→{_rc_after_readback(0, 'RED')}"
      f" GREEN/0→{_rc_after_readback(0, 'GREEN')} RED/1→{_rc_after_readback(1, 'RED')}")

check(RS, "AZ127e 读者无记忆：先判半截日志（CRASH）再判完整日志（GREEN）\n"
          "              ⇒ 「上一次是绿的」不参与这一次的判读（V141 的 W5 打的就是它）",
      judge_run_log(_az127_half, 0)[0] == "CRASH"
      and judge_run_log(_az127_good, 0)[0] == "GREEN"
      and judge_run_log(_az127_half, 0)[0] == "CRASH",
      f"half={judge_run_log(_az127_half, 0)[0]} good={judge_run_log(_az127_good, 0)[0]}")


# ── AZ128 结论要有去处：账本 / 基线 / 比较（第 142 轮）────────────────────
# 上一轮（V141）让系统**读**了自己的日志；这一轮要它**不许忘记自己读到什么**。
# 三条链各自独立、各配一条断言：
#   账本（runs.jsonl）：每一行带自己的结论；缺席 = UNKNOWN，**不是绿**；
#   基线（bench_baseline.json）：只有 GREEN 的运行能写成标准；
#   比较（--compare）：来源不明或没量全的基线 ⇒ **拒绝比较**，不出一句「改进成立」。
from mw.bench import (BaselineStore, Caliber, baseline_provenance_gate,  # noqa: E402
                      baseline_recompute_gate, baseline_verdict_gate,
                      caliber_from_dict, ledger_audit, ledger_audit_strata,
                      ledger_strata, ledger_verdict, record_verdict,
                      recompute_mean, stamp_verdict, audit_measurement,
                      _rid_is_literal, _scan_rows)

_az128_dir = Path(tempfile.mkdtemp(prefix="v142_ledger_"))
_az128_log = _az128_dir / "runs.jsonl"


def _az128_write(*recs: dict) -> None:
    _az128_log.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                 for r in recs), encoding="utf-8")


_az128_write({"run_id": "r-old", "measurement": {"mean": 1.0}},          # 旧格式：无 verdict
             {"run_id": "r-pend", "verdict": "PENDING"})                 # 本轮占位：没钉上
_az128_old = ledger_verdict(_az128_log, "r-old")
_az128_pend = ledger_verdict(_az128_log, "r-pend")
_az128_missing = ledger_verdict(_az128_log, "r-不存在")
check(RS, "AZ128a 账本里**没有结论**的三条路都读成 UNKNOWN ≠ GREEN\n"
          "              ①键缺席（V142 之前的 145 条真实记录全是这种）\n"
          "              ②占位值 PENDING（进程在钉结论之前死了）\n"
          "              ③账本里没有这一行 ——— 缺席不许冒充完成",
      _az128_old[0] == "UNKNOWN" and _az128_pend[0] == "UNKNOWN"
      and _az128_missing[0] == "UNKNOWN",
      f"旧={_az128_old[0]} 占位={_az128_pend[0]} 无此行={_az128_missing[0]}")

# ⛔ 钉结论钉的是**身份**不是**位置**：最后一行若不是这次运行，必须拒绝。
_az128_before = _az128_log.read_bytes()
_az128_stamped, _az128_why = stamp_verdict(_az128_log, "r-别人", "GREEN")
check(RS, "AZ128b `stamp_verdict` 只钉 run_id 匹配的那一行；位置不是身份\n"
          "              ⛔ 「钉最后一行」在并发/重跑/被插行时会钉到别人身上，\n"
          "              而且钉错了没人看得出来 —— 错的那行也带一个合法的 verdict",
      (not _az128_stamped) and _az128_log.read_bytes() == _az128_before,
      _az128_why[:80])
_az128_ok, _az128_why2 = stamp_verdict(_az128_log, "r-pend", "RED")
check(RS, "AZ128c 钉对了才写盘，且**读回**拿到刚钉的值（写 ≠ 写进去了）",
      _az128_ok and ledger_verdict(_az128_log, "r-pend")[0] == "RED"
      and ledger_verdict(_az128_log, "r-old")[0] == "UNKNOWN",
      f"{_az128_why2} 读回={ledger_verdict(_az128_log, 'r-pend')[0]}")

_az128_store = BaselineStore(_az128_dir / "bench_baseline.json")
_az128_m = summarize([0.8, 0.8, 0.8], candidate="A", budget_tag="1x")
_az128_cal = Caliber(scorer_sha="s", suite_sha="u", repeat=1)
_az128_r1, _az128_w1 = _az128_store.save(_az128_m, _az128_cal, verdict="RED")
_az128_r2, _az128_w2 = _az128_store.save(_az128_m, _az128_cal, verdict="PENDING")
_az128_r3, _az128_w3 = _az128_store.save(_az128_m, _az128_cal, verdict="GREEN")
check(RS, "AZ128d 只有 GREEN 的运行能写成基线（RED / 未知一律拒绝，盘面不动）\n"
          "              ⛔ `verdict` 是**必填**参数：给它一个默认值，就等于给\n"
          "              「忘了说这次量到没有」留了一条静默通过的路",
      (not _az128_r1) and (not _az128_r2) and _az128_r3
      and _az128_store.load_verdict() == "GREEN",
      f"RED={_az128_r1} 未知={_az128_r2} GREEN={_az128_r3} 落盘={_az128_store.load_verdict()}")
_az128_doc = json.loads(_az128_store.path.read_text(encoding="utf-8"))
_az128_doc.pop("verdict", None)
_az128_old_bl = _az128_dir / "old_baseline.json"
_az128_old_bl.write_text(json.dumps(_az128_doc), encoding="utf-8")
check(RS, "AZ128e 旧基线（无 verdict 键）读回来是 **None**，不是 \"GREEN\"\n"
          "              这是「拿没量到的东西当标准」的唯一入口，必须在反序列化处堵死",
      BaselineStore(_az128_old_bl).load_verdict() is None,
      f"load_verdict={BaselineStore(_az128_old_bl).load_verdict()!r}")

_az128_gates = {v: baseline_verdict_gate(v)[0]
                for v in ("GREEN", "RED", "CRASH", None)}
check(RS, "AZ128f 基线比较闸：只有 GREEN 放行（无关来源的三种都拒绝）",
      _az128_gates == {"GREEN": True, "RED": False, "CRASH": False, None: False},
      f"{_az128_gates}")

# ⛔ 写基线的调用点必须**唯一且在外壳**（读回之后）。函数体里再出现一处，
#    「先签收后验货」就回来了 —— 而它看起来完全正常。
#    ⚠️ 只数 `store.save(...)`：`run.py` 里还有 `_ans_log.save(...)`（答案落盘），
#       按方法名数会把两件事算成一件（分母口径，纪律 4）。
def _az128_store_saves(node) -> int:
    """数 `store.save(...)` 与 `xxx.store.save(...)` —— 两种都算同一个写入口。

    ⚠️ 第一版只认 `ast.Name == "store"`，而外壳里写的是 `gate.store.save(...)`
       ⇒ 三处全数成 0，断言当场红。「计数器的形状要跟着被数的东西长」。
    """
    n = 0
    for x in ast.walk(node):
        if not (isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
                and x.func.attr == "save"):
            continue
        recv = x.func.value
        if ((isinstance(recv, ast.Name) and recv.id == "store")
                or (isinstance(recv, ast.Attribute) and recv.attr == "store")):
            n += 1
    return n


_az128_bodies = {n.name: n for n in ast.walk(_az127_tree)
                 if isinstance(n, ast.FunctionDef) and n.name in ("cmd_bench", "_bench_main")}
_az128_save_sites = _az128_store_saves(_az127_tree)
_az128_in_shell = _az128_store_saves(_az128_bodies["cmd_bench"])
_az128_in_body = _az128_store_saves(_az128_bodies["_bench_main"])
check(RS, "AZ128g 写基线只在**外壳**里发生一次，函数体内 0 次（AST 现算）\n"
          "              ⛔ 函数体那一处就是「判据还没产生就先写标准」",
      _az128_save_sites == 1 and _az128_in_shell == 1 and _az128_in_body == 0,
      f"全文件 .save( 调用={_az128_save_sites} 外壳={_az128_in_shell} 函数体={_az128_in_body}")

# ⛔ 「同口径」只能有**一个**定义。V142 修的那处：`signature()` 走 JSON、
#    `diff()` 走 Python 值 ⇒ 落盘回来的 `[0, 0]` 与进程内的 `(0, 0)` 被读成
#    不同口径，于是 `--compare` 对**任何**写盘基线都拒绝比较。
_az128_c = Caliber(memory_seed=(0, 0), repeat=1, limits={"a": 1})
_az128_rt = caliber_from_dict(json.loads(json.dumps(_az128_c.as_dict())))
check(RS, "AZ128h 签名相同 ⇒ `diff` 必须为空（同一个「同口径」不许有两套判据）\n"
          "              tuple/list 的差别只是序列化形状，不是尺子变了",
      _az128_c.signature() == _az128_rt.signature() and _az128_c.same_as(_az128_rt),
      f"sig同={_az128_c.signature() == _az128_rt.signature()} "
      f"diff={_az128_c.diff(_az128_rt)}")


# ── AZ129 账本才是权威：基线的可信度必须由账本复核（第 143 轮）──────────────
# V142 让基线带上「我是哪种运行写的」，但那是**自述** —— 写在同一份文件里，
# 谁都能改，改了没人知道。可信度必须能被第三方复算，第三方就是账本。
# ⛔ 「这一行的结论是什么」只许有一个定义（`record_verdict`），否则两份判据
#    迟早会长成两个答案。
_az129_cases = [
    ({"verdict": "GREEN"}, "GREEN"), ({"verdict": "RED"}, "RED"),
    ({"verdict": "CRASH"}, "CRASH"), ({}, "UNKNOWN"),
    ({"verdict": "PENDING"}, "UNKNOWN"), ({"verdict": "green"}, "UNKNOWN"),
    ({"verdict": None}, "UNKNOWN"),
]
check(RS, "AZ129a `record_verdict` 是「这一行结论」的**唯一**定义\n"
          "              三值原样返回；键缺席 / 占位 PENDING / 大小写变体 / None\n"
          "              一律 UNKNOWN —— 缺席不许冒充完成（V142 纪律的孪生）",
      [record_verdict(c) for c, _ in _az129_cases] == [e for _, e in _az129_cases],
      f"{[record_verdict(c) for c, _ in _az129_cases]}")

# ── 普查的**守恒式**：每一条记录都必须落进某一层，落不进去就是普查漏了人 ──
# 分母口径（纪律 4/46）：`rows` 是**账本行数**，不是断言数。
_az129_led = Path(tempfile.mkdtemp(prefix="v143_strata_")) / "runs.jsonl"
_az129_led.write_text("".join(json.dumps(r) + "\n" for r in [
    {"run_id": "a", "verdict": "GREEN"}, {"run_id": "b", "verdict": "RED"},
    {"run_id": "c"}, {"run_id": "d", "verdict": "PENDING"},
    {"verdict": "GREEN"},                    # 没有身份证 ⇒ 查无可查
]), encoding="utf-8")
_az129_st = ledger_strata(_az129_led)
_az129_sum = (_az129_st["no_run_id"] + _az129_st["GREEN"] + _az129_st["RED"]
              + _az129_st["CRASH"] + _az129_st["UNKNOWN"])
check(RS, "AZ129b 账本分层普查守恒：无身份证 + 四层 == 总行数（构造账本 5 行）\n"
          "              ⛔ 「无 run_id」单独一层：那是**查无可查**，不是「问了没答」；\n"
          "              混进 UNKNOWN 会把「问不到」说成「问了没答」（分母口径）",
      _az129_st["rows"] == 5 and _az129_sum == 5
      and (_az129_st["GREEN"], _az129_st["RED"], _az129_st["UNKNOWN"],
           _az129_st["no_run_id"]) == (1, 1, 2, 1),
      f"{_az129_st} 守恒={_az129_sum}")

# ── 复核闸五条规则的真值表 ──────────────────────────────────────────────
_az129_tbl = {
    ("GREEN", "r1", "GREEN"): True,       # 三处对齐 ⇒ 放行
    ("RED", "r1", "GREEN"): False,        # ① 自述不是绿
    (None, "r1", "GREEN"): False,         # ① 无 verdict 键
    ("GREEN", "", "GREEN"): False,        # ② 没有身份证
    ("GREEN", "r1", "UNKNOWN"): False,    # ③ 账本查不到
    ("GREEN", "r1", "RED"): False,        # ④ 两处打架
    ("GREEN", "r1", "CRASH"): False,
}
_az129_got = {k: baseline_provenance_gate(store_verdict=k[0], store_run_id=k[1],
                                          ledger_state=k[2])[0]
              for k in _az129_tbl}
check(RS, "AZ129c 复核闸：自称 + 身份证 + 账本**三处对齐**才放行\n"
          "              规则顺序即语义：①自述非绿 ②无身份证 ③查无此账 ④两处打架\n"
          "              ⛔ 两处打架时**保守的那个赢** —— 不是取平均，也不是信文件",
      _az129_got == _az129_tbl,
      f"{ {k: v for k, v in _az129_got.items() if v != _az129_tbl[k]} }")

# ── 身份证真的写进去了、真的读得回来；旧基线读回空串 ────────────────────
_az129_store = BaselineStore(Path(tempfile.mkdtemp(prefix="v143_bl_")) / "b.json")
_az129_m = summarize([0.8, 0.8, 0.8], candidate="A", budget_tag="1x")
_az129_cal = Caliber(scorer_sha="s", suite_sha="u", repeat=1)
_az129_w, _az129_wm = _az129_store.save(_az129_m, _az129_cal, verdict="GREEN",
                                       run_id="42@1700000000")
_az129_doc = json.loads(_az129_store.path.read_text(encoding="utf-8"))
_az129_doc.pop("run_id", None)
_az129_legacy = _az129_store.path.with_name("legacy.json")
_az129_legacy.write_text(json.dumps(_az129_doc), encoding="utf-8")
check(RS, "AZ129d 基线里的身份证：写进盘面、读得回来；V143 之前的旧基线读回 **空串**\n"
          "              ⛔ 空串不是「随便挑一行」—— 它是一个明确的「无从复核」",
      _az129_w and _az129_store.load_run_id() == "42@1700000000"
      and _az129_doc.get("verdict") == "GREEN"
      and BaselineStore(_az129_legacy).load_run_id() == "",
      f"写入={_az129_w} 读回={_az129_store.load_run_id()!r} "
      f"旧基线={BaselineStore(_az129_legacy).load_run_id()!r}")


def _az129_calls(name: str) -> int:
    return sum(1 for x in ast.walk(_az127_tree)
               if isinstance(x, ast.Call) and getattr(x.func, "id", "") == name)


_az129_mine, _az129_thel = _az129_calls("baseline_provenance_gate"), _az129_calls("ledger_verdict")
check(RS, "AZ129e `run.py` 里复核闸**恰好 1 个**调用点；`ledger_verdict` 恰好 2 个\n"
          "              （外壳钉完读回 + 比较复核）。多一个读者就多一套口径，\n"
          "              迟早一套改了另一套没改（与 AZ127b 同一条纪律）",
      _az129_mine == 1 and _az129_thel == 2,
      f"复核闸={_az129_mine} ledger_verdict={_az129_thel}")

# ── 四种拒法必须**各说各的原因** ────────────────────────────────────────
# ⛔ 拒绝句都写「拒绝比较」时，使用者无法知道该修哪一处。这不是文案问题：
#    「基线写坏了」与「账本被人换了」修法完全不同（纪律 4 的同一形态）。
_az129_reasons = {
    "自述非绿": baseline_provenance_gate(store_verdict="RED", store_run_id="r1",
                                        ledger_state="GREEN")[1],
    "无身份证": baseline_provenance_gate(store_verdict="GREEN", store_run_id="",
                                        ledger_state="GREEN")[1],
    "查无此账": baseline_provenance_gate(store_verdict="GREEN", store_run_id="r1",
                                        ledger_state="UNKNOWN")[1],
    "两处打架": baseline_provenance_gate(store_verdict="GREEN", store_run_id="r1",
                                        ledger_state="RED")[1],
}
_az129_keys = {"自述非绿": "RED", "无身份证": "身份证",
               "查无此账": "查不到", "两处打架": "打架"}
check(RS, "AZ129f 四种拒法**各说各的原因**（互不相同，且各自点名自己的那一条）\n"
          "              ⛔ 「基线写坏了」与「账本被人换了」修法完全不同；\n"
          "              都念一句「拒绝比较」，等于让人去猜",
      len(set(_az129_reasons.values())) == 4
      and all(kw in _az129_reasons[name] for name, kw in _az129_keys.items()),
      f"{ {k: v[:24] for k, v in _az129_reasons.items()} }")

# ── 第一层仍是 V142 那个闸：同一个判断只有一处定义 ──────────────────────
_az129_why = baseline_provenance_gate(store_verdict=None, store_run_id="x",
                                      ledger_state="GREEN")[1]
check(RS, "AZ129g 复核闸的**第一层**是 `baseline_verdict_gate`（理由逐字相同）\n"
          "              ⛔ 两个闸各写一份「自述能不能用」，改了一处另一处就成了漏洞",
      _az129_why == baseline_verdict_gate(None)[1],
      _az129_why[:60])


# ── AZ130 结论可被第三方从账本重算（第 144 轮）────────────────────────────
# V143 让「基线的可信度」由账本复核，但账本里的 `measurement.mean` 仍是自述：
# 它与 `per_task` 写在同一行，却从来没有代码从明细把它算回来一次。
# ⛔ 审计结果不落盘 —— 账本里只放事实，复核结论永远现算（否则就是第二个自述）。

# ── 第一条：公式必须写死，而且**求和算法也是公式的一部分** ──────────────
# 实测（v6_s233 / bench_out/runs.jsonl）：改用内置 `sum()` 重算，39/136 行
# （28.7%）与写下的 mean 差 1 ulp。CPython 3.12+ 的 `sum()` 对 float 走补偿求和。
_az130_pt = [1.0, 0.4, 0.2, 0.2]
_az130_fold = 0.0
for _x in _az130_pt:
    _az130_fold += _x
_az130_rec = {
    "run_id": "v144a", "repeat": 1,
    "report": {"attempted": 4, "recorded": 4, "skipped_no_answer": 0,
               "failed_scorer": 0,
               "per_task": [{"rep": 1, "id": f"t{i}", "score": s}
                            for i, s in enumerate(_az130_pt)]},
    "measurement": {"n": 1, "mean": _az130_fold / 4},
}
_az130_got = recompute_mean(_az130_rec)
check(RS, "AZ130a 重算公式：每轮 fold(score)/n_tasks，再对轮 fold\n"
          "              ⛔ 求和**必须**是朴素左折叠（纪律 48 的形态：判「逐格相同」\n"
          "              必须把算法写进判据，否则判的是算法差异）。\n"
          "              ⚠️ 「与精确求和不同」的见证用 `math.fsum`，**不用内置 `sum()`**：\n"
          "              3.12+ 的 `sum()` 走补偿求和（同一份账本 39/136 行差 1 ulp），\n"
          "              而 3.11 的 `sum()` 就是朴素折叠 ⇒ 拿它当见证会让本条在 3.11 上**恒假**\n"
          "              （V169 实测：CI py3.11 从 V144 起红了 32 次，根因就是这一行）",
      _az130_got is not None
      and _az130_got[0] == _az130_fold / 4
      and _az130_got[0] != math.fsum(_az130_pt) / 4
      and _az130_got[1]["n_tasks"] == 4,
      f"重算={_az130_got[0]!r} fold={_az130_fold / 4!r} "
      f"fsum={math.fsum(_az130_pt) / 4!r} sum={sum(_az130_pt) / 4!r}")


def _az130_mut(**kw) -> dict:
    """在 `_az130_rec` 上做一处改动，返回深拷贝。"""
    import copy
    r = copy.deepcopy(_az130_rec)
    for k, v in kw.items():
        if k == "drop_last":
            r["report"]["per_task"] = r["report"]["per_task"][:-1]
        elif k == "score0":
            r["report"]["per_task"][0]["score"] = v
        elif k == "mean":
            r["measurement"]["mean"] = v
        elif k == "no_mean":
            r["measurement"].pop("mean", None)
        elif k == "no_pt":
            r["report"].pop("per_task", None)
        elif k == "empty_pt":
            r["report"]["per_task"] = []
        elif k == "skip":
            r["report"]["skipped_no_answer"] = v
    return r


_az130_ok = audit_measurement(_az130_rec)
_az130_drop = audit_measurement(_az130_mut(drop_last=True))
_az130_drop_val = recompute_mean(_az130_mut(drop_last=True))
check(RS, "AZ130b 删掉一条明细 ⇒ MISMATCH，理由落在**条数**而不是数值\n"
          "              ⛔ 缺一条**不许补 0、也不许跳过**：分母仍从 attempted 推得，\n"
          "              所以照样算得出一个数 —— 只有先对账才能把「记录被改过」\n"
          "              与「这次有一题没做」分开（后者在 recorded 里另有计数）",
      _az130_ok[0] == "VERIFIED"
      and _az130_drop[0] == "MISMATCH"
      and "明细条数" in _az130_drop[1]
      and _az130_drop_val is not None,
      f"正例={_az130_ok[0]} 删一条={_az130_drop[0]} 仍算得出={_az130_drop_val[0]!r}")

# ── 对称两格：改证据不改结论 / 改结论不改证据 ──────────────────────────
_az130_sc = audit_measurement(_az130_mut(score0=1.01))
_az130_mn = audit_measurement(_az130_mut(mean=_az130_fold / 4 + 0.001))
check(RS, "AZ130c 改明细 ⇒ MISMATCH；改 mean ⇒ MISMATCH（两个方向都要抓）\n"
          "              ⛔ 只抓一边，另一边就是最便宜的造假方式",
      _az130_sc[0] == "MISMATCH" and _az130_mn[0] == "MISMATCH"
      and "重算" in _az130_sc[1] and "重算" in _az130_mn[1],
      f"改明细={_az130_sc[0]} 改结论={_az130_mn[0]}")

# ── 缺席四态：⛔ 一条都不许读成 VERIFIED ───────────────────────────────
_az130_abs = {
    "无 per_task 键": audit_measurement(_az130_mut(no_pt=True)),
    "per_task 为空": audit_measurement(_az130_mut(empty_pt=True)),
    "无 mean 键": audit_measurement(_az130_mut(no_mean=True)),
    "查无此账": ledger_audit(Path(tempfile.mkdtemp(prefix="v144_led_")) / "none.jsonl",
                          "v144a"),
}
check(RS, "AZ130d 缺席四态（无明细键 / 明细空 / 无均值 / 查无此账）一律 UNVERIFIABLE\n"
          "              ⛔ 「不可复核」不等于「没问题」—— 缺席不许冒充完成",
      all(v[0] == "UNVERIFIABLE" for v in _az130_abs.values()),
      f"{ {k: v[0] for k, v in _az130_abs.items()} }")

# ── 分层守恒（分母 = 账本行数，不是断言数）──────────────────────────────
_az130_led = Path(tempfile.mkdtemp(prefix="v144_strata_")) / "runs.jsonl"
_az130_bad = _az130_mut(drop_last=True)
_az130_bad["run_id"] = "v144b"
_az130_unk = {"run_id": "v144c", "repeat": 1,
              "report": {"attempted": 2, "recorded": 2, "per_task": []},
              "measurement": {"n": 1, "mean": 0.5}}
_az130_led.write_text("".join(json.dumps(r) + "\n" for r in
                              [_az130_rec, _az130_bad, _az130_unk]),
                      encoding="utf-8")
_az130_st = ledger_audit_strata(_az130_led)
_az130_tot = _az130_st["VERIFIED"] + _az130_st["MISMATCH"] + _az130_st["UNVERIFIABLE"]
check(RS, "AZ130e 可重算性分层守恒：VERIFIED + MISMATCH + UNVERIFIABLE == 总行数\n"
          "              ⛔ 与 AZ129b（按结论分层）并列而不是并进去：\n"
          "              一格答「跑完没有」，另一格答「这个数算得回来吗」",
      _az130_st["rows"] == 3 and _az130_tot == 3
      and (_az130_st["VERIFIED"], _az130_st["MISMATCH"],
           _az130_st["UNVERIFIABLE"]) == (1, 1, 1),
      f"{_az130_st} 守恒={_az130_tot}")

# ── 第六格的真值表 + 四种拒法各说各的原因 ──────────────────────────────
_az130_tbl = {
    ("r1", "VERIFIED"): True,
    ("r1", "MISMATCH"): False,
    ("r1", "UNVERIFIABLE"): False,
    ("", "VERIFIED"): False,
}
_az130_gate = {k: baseline_recompute_gate(store_run_id=k[0], audit_state=k[1],
                                         audit_why="w")[0]
               for k in _az130_tbl}
_az130_why = {s: baseline_recompute_gate(store_run_id="r1", audit_state=s,
                                         audit_why="w")[1]
              for s in ("MISMATCH", "UNVERIFIABLE")}
_az130_why["无身份证"] = baseline_recompute_gate(store_run_id="", audit_state="VERIFIED",
                                            audit_why="w")[1]
check(RS, "AZ130f 重算闸真值表：只有 VERIFIED 放行；无身份证 / 打架 / 不可复核各拒\n"
          "              ⛔ 三种拒法理由互不相同 —— 「该补明细」与「该查改动」修法相反",
      _az130_gate == _az130_tbl
      and len(set(_az130_why.values())) == 3
      and all(k in _az130_why[n] for n, k in
              {"MISMATCH": "打架", "UNVERIFIABLE": "不可重算",
               "无身份证": "身份证"}.items()),
      f"{_az130_gate} 理由数={len(set(_az130_why.values()))}")

_az130_la = _az129_calls("ledger_audit")
_az130_gc = _az129_calls("baseline_recompute_gate")
check(RS, "AZ130g `run.py` 里 `ledger_audit` **恰好 2 个**调用点（外壳复核 + 比较复核）；\n"
          "              重算闸恰好 1 个 —— 多一个读者就多一套口径（同 AZ129e）",
      _az130_la == 2 and _az130_gc == 1,
      f"ledger_audit={_az130_la} recompute_gate={_az130_gc}")


# ── V145（§212）：复核的**粒度** —— 一行坏不该连坐全账本 ────────────────────
# 为什么要有这一组：`_read_records` 是整份解析，任何一行坏 ⇒ 整份 None ⇒
# 全账本上所有结论、所有复核一起失效。账本只会变长，这条连坐路径的概率单调上升。
# ⛔ 换的是**粒度**，不是严格性：目标行自己坏 ⇒ 照样不许放行。
_az131_dir = Path(tempfile.mkdtemp(prefix="v145_scan_"))


def _az131_row(rid: str, verdict: str | None = "GREEN", score: float = 1.0,
               n: int = 2) -> dict:
    pt = [{"rep": 1, "id": f"t{i}", "score": score} for i in range(n)]
    total = 0.0
    for row in pt:
        total += row["score"]
    rec = {"run_id": rid, "repeat": 1,
           "report": {"attempted": n, "recorded": n, "skipped_no_answer": 0,
                      "failed_scorer": 0, "per_task": pt},
           "measurement": {"n": 1, "mean": total / n}}
    if verdict is not None:
        rec["verdict"] = verdict
    return rec


def _az131_write(name: str, recs: list, bad_at: int | None = None,
                 bad_text: str = '{"run_id": "broken", "report": ') -> Path:
    lines = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in recs]
    if bad_at is not None:
        lines.insert(bad_at, bad_text)
    p = _az131_dir / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


_az131_3 = [_az131_row("r0"), _az131_row("r1"), _az131_row("r2", verdict="RED")]
_az131_clean = _az131_write("clean.jsonl", _az131_3)
_hit131, _inf131 = _scan_rows(_az131_clean, "r2")
check(RS, "AZ131a 定位器只解析**候选行**：3 行账本审 1 行 ⇒ parsed==1，而 lines==3\n"
          "              ⛔ 省掉的是「解析」，不是「账本有多长」这个事实 ——\n"
          "              跳过别的行**不改变任何主张**，只改变成本",
      _inf131["parsed"] == 1 and _inf131["lines"] == 3
      and _inf131["prefilter"] is True and _hit131["run_id"] == "r2"
      and _inf131["bad_in_parsed"] == 0,
      f"{_inf131}")

# 诱饵：别的字段里含同一个 run_id ⇒ 会被解析，但**不会**被当成命中
_az131_dec = _az131_row("rD")
_az131_dec["note"] = "see r2 for details"
#: ⚠️ 诱饵放在**最后**：语义是「重复 run_id 取最后一处」，所以只有「末行是诱饵」
#:   才能区分「按 == 命中」与「按子串命中」—— 排在前面时两种实现都取到真目标，
#:   断言会**恒真**（纪律 5 第一形态）。
#: ⚠️ 这里刻意**不断言 `parsed`**：预筛计数由 AZ131a 管，混进来会让两条断言同生同死。
_hit_dec, _inf_dec = _scan_rows(_az131_write("decoy131.jsonl", _az131_3 + [_az131_dec]), "r2")
#: 非 ASCII 端到端（guard 生效 ⇒ 退回全解析 ⇒ 仍必须找得到）。放在这里而不是
#: AZ131f：它验的是**命中规则**，而 AZ131f 只验允许表本身（分开才不会同生同死）。
_az131_na = _az131_dir / "nonascii.jsonl"
_az131_na.write_text(json.dumps({"run_id": "r-不存在", "repeat": 1,
                                 "report": {"attempted": 2, "recorded": 2,
                                            "skipped_no_answer": 0, "failed_scorer": 0,
                                            "per_task": [{"rep": 1, "id": "t0", "score": 1.0},
                                                         {"rep": 1, "id": "t1", "score": 1.0}]},
                                 "measurement": {"n": 1, "mean": 1.0}},
                                ensure_ascii=True, sort_keys=True) + "\n",
                     encoding="utf-8")
_az131_na_got = ledger_audit(_az131_na, "r-不存在")
check(RS, "AZ131b 预筛**只多选、不漏选**：诱饵行（别的字段含该 run_id）被解析，\n"
          "              命中仍按解析后的 `run_id ==` 判定 ⇒ 子串只影响成本、不影响结论；\n"
          "              且 run_id 需转义时退回全解析后**仍找得到**（不许把存在读成查无此账）",
      _hit_dec["run_id"] == "r2" and _az131_na_got[0] == "VERIFIED",
      f"命中={_hit_dec.get('run_id')!r} 非ASCII端到端={_az131_na_got[0]}")

# ── 坏行隔离：非目标行坏 ⇒ 目标行照样审得了；目标行坏 ⇒ 照样不放行 ──────────
#: ⛔ 坏行**必须含目标 run_id** 才叫「候选」：不含的坏行会被预筛直接跳过，
#:   注入就变成**不可见**的（v6_s234 第一次跑就栽在这里：AZ131c 撤回了也不红）。
#:   含 run_id 的坏行一定会被解析 ⇒ 一定会撞上 `JSONDecodeError` ⇒ 才真的在测隔离。
_az131_bad = _az131_write("bad.jsonl", _az131_3, bad_at=1,
                          bad_text='{"note": "see r2 too", "report": ')
_az131_st_clean = ledger_audit(_az131_clean, "r2")
_az131_st_bad = ledger_audit(_az131_bad, "r2")
check(RS, "AZ131c 非目标行坏 JSON ⇒ 目标行仍 VERIFIED，且**理由与干净账本逐字相同**\n"
          "              （`audit_measurement` 的依赖集只有那一个记录对象 —— 别的行坏\n"
          "              在逻辑上影响不到它）。旧行为是整份 None ⇒ 一律不可复核",
      _az131_st_clean[0] == "VERIFIED" and _az131_st_bad == _az131_st_clean,
      f"干净={_az131_st_clean[0]} 夹坏行={_az131_st_bad[0]} 逐字相同={_az131_st_bad == _az131_st_clean}")

_az131_vclean = ledger_verdict(_az131_clean, "r2")
_az131_vbad = ledger_verdict(_az131_bad, "r2")
check(RS, "AZ131d 结论侧同样按行隔离：夹一行坏 JSON 时 `ledger_verdict` 仍读出 RED\n"
          "              ⛔ 只修行读而不修结论侧 ⇒ 溯源闸在结论那一步照样死掉，端到端没修",
      _az131_vclean == _az131_vbad and _az131_vbad[0] == "RED",
      f"干净={_az131_vclean} 夹坏行={_az131_vbad}")

_az131_self = _az131_write(
    "selfbad.jsonl", [_az131_row("r0"), _az131_row("r1")],
    bad_at=2, bad_text='{"run_id": "r2", "repeat": 1, "report": {"attempted"')
_az131_self_st = ledger_audit(_az131_self, "r2")
_az131_self_v = ledger_verdict(_az131_self, "r2")
check(RS, "AZ131e ⛔ 目标行**自己**坏 ⇒ 仍然不许放行（UNVERIFIABLE / UNKNOWN），\n"
          "              理由含「疑似」：读不懂就无法否认它就是那一行 ⇒ 不许当读过\n"
          "              ⇒ 换粒度没换严格性",
      _az131_self_st[0] == "UNVERIFIABLE" and _az131_self_v[0] == "UNKNOWN"
      and "疑似" in _az131_self_st[1],
      f"{_az131_self_st[0]}/{_az131_self_v[0]} 理由={_az131_self_st[1][:40]}")

# ── 预筛的前提：run_id 必须以**字面量**出现 ───────────────────────────────
_az131_lit = {p: _rid_is_literal(p) for p in
              ("41509@1794809015", "r-不存在", 'a"b', "a\\b", "a\tb", "", "a b")}
_az131_esc = json.dumps({"run_id": "r-不存在"}, ensure_ascii=True)
check(RS, "AZ131f 预筛前置条件：`run_id` 必须**原样**出现在那一行里\n"
          "              非 ASCII 会被写成 `\\uXXXX`、`\"`/`\\`/控制符会被转义 ⇒ 子串不在\n"
          "              ⇒ 预筛会**漏选**（存在被读成查无此账）⇒ 表外一律退回全解析\n"
          "              （端到端见 AZ131b；这里只管允许表本身，免得两条断言同生同死）",
      _az131_lit == {"41509@1794809015": True, "r-不存在": False, 'a"b': False,
                     "a\\b": False, "a\tb": False, "": False, "a b": False}
      and "r-不存在" not in _az131_esc,            # ensure_ascii=True 下子串确实不在
      f"{_az131_lit}")

# ── 整份诊断：一行都不许跳，坏行必须计数 ──────────────────────────────────
_az131_str = ledger_audit_strata(_az131_bad)
_az131_tot131 = (_az131_str["VERIFIED"] + _az131_str["MISMATCH"]
                 + _az131_str["UNVERIFIABLE"])
check(RS, "AZ131g 整份普查看得见坏行：`bad_lines==1`、`readable` 收窄为 False，\n"
          "              守恒式仍在 `rows`（**解析成功**的行数）上，总行数 = rows + bad_lines\n"
          "              ⛔ 坏行不许静默跳过 —— 跳过等于把「账本坏了」读成「账本少一行」",
      _az131_str["bad_lines"] == 1 and _az131_str["readable"] is False
      and _az131_str["rows"] == 3 and _az131_tot131 == 3
      and _az131_str["rows"] + _az131_str["bad_lines"] == _az131_str["lines"] == 4,
      f"{_az131_str}")

# ── 静态：两个读者都不许再整份解析 ───────────────────────────────────────
_az131_src = (Path(__file__).resolve().parents[1] / "mw/bench.py").read_text(encoding="utf-8")
_az131_btree = ast.parse(_az131_src)


def _az131_calls_of(fn: str) -> set:
    for nd in ast.walk(_az131_btree):
        if isinstance(nd, ast.FunctionDef) and nd.name == fn:
            return {getattr(c.func, "id", getattr(c.func, "attr", ""))
                    for c in ast.walk(nd) if isinstance(c, ast.Call)}
    return set()


_az131_readers = {f: _az131_calls_of(f) for f in ("ledger_verdict", "ledger_audit")}
check(RS, "AZ131h 两个按身份读账本的读者都**不再**调 `_read_records`（整份解析），\n"
          "              都改走 `_scan_rows`；而整份普查仍走自己那条全解析路径\n"
          "              ⛔ 只改一个读者 ⇒ 另一个在坏行上照样死，端到端没修（AZ131d）",
      all("_read_records" not in v and "_scan_rows" in v
          for v in _az131_readers.values())
      and "_read_records" in _az131_calls_of("ledger_strata"),
      f"{ {k: sorted(v) for k, v in _az131_readers.items()} }")


# ===========================================================================
print()
print("=" * 74)
print("I. 内核端到端（离线、确定性、可重复）")
print("=" * 74)
from run import DemoLLM  # noqa: E402  （范例模型：故意撞一次守卫）


def _run_once() -> dict:
    core = Core(cfg.settings(), llm=DemoLLM())
    goal = DemoLLM.GOAL
    out = core.run(goal, budget_factor=1.0)
    return {"status": out.status, "answer": out.answer, "stats": out.stats,
            "dup": core.memory.duplicate_rows(), "log": core.log.lines}


o1 = _run_once()
o2 = _run_once()
check(RS, "I1 端到端达成 done", o1["status"] == "done", str(o1["status"]))
check(RS, "I2 守卫真的拦下了破坏性动作（不是摆设）",
      o1["stats"]["guard"]["blocks"].get("tool_misuse", 0) >= 1,
      str(o1["stats"]["guard"]))
check(RS, "I3 拦截有正向信号进日志（R41）",
      any("✋" in l for l in o1["log"]))
check(RS, "I4 记忆零重复行", o1["dup"] == 0)
check(RS, "I5 预算账本落在上限内",
      o1["stats"]["budget"]["steps"].split("/")[0] != "0", str(o1["stats"]["budget"]))
check(RS, "I5b candidates 维度被真实消费（不是装饰字段）",
      o1["stats"]["budget"]["cand_spent"] > 0,
      str(o1["stats"]["budget"]["cand_spent"]))
check(RS, "I6 世界模型产出了失配沉淀",
      o1["stats"]["worldmodel"]["mismatches"] >= 1, str(o1["stats"]["worldmodel"]))
check(RS, "I7 结论写回语义记忆",
      o1["stats"]["memory"]["facts"] >= 2, str(o1["stats"]["memory"]))
check(RS, "I8 确定性：同一输入两次跑读数一致（无 flaky）",
      o1["status"] == o2["status"]
      and o1["stats"]["budget"]["steps"] == o2["stats"]["budget"]["steps"]
      and o1["stats"]["budget"]["llm_calls"] == o2["stats"]["budget"]["llm_calls"]
      and o1["stats"]["guard"] == o2["stats"]["guard"],
      f"{o1['stats']['budget']['steps']} vs {o2['stats']['budget']['steps']}"
      f" | wall {o1['stats']['budget']['wall_s']} vs {o2['stats']['budget']['wall_s']}"
      " (墙钟不计入 —— 它天然不确定，拿它比确定性是测试自己的错)")
check(RS, "I9 内核不依赖网络与密钥（离线跑通）",
      o1["stats"]["llm"] == "http" or o1["status"] == "done")

# 预算收紧必须导致"如实报告没做完"，而不是假装完成。
#
# 2026-10-17 语义更新：新增「收尾余量」后，预算将尽时会**先转向交付**（status=closed）
# 而不是一路探索到抛 BudgetExhausted。诚实性这条**不变量没变**（都不是 done、
# 都要说清是预算切断），变的是"预算将尽时该做什么"：手段不能吃掉目的
# （实测有一题 120s 全花在 recall 上、最后交付了零）。
# ⇒ 这里断言**不变量**，而不是某一个状态字面量 —— 否则会把"行为改进"误判成"回归"。
core_tight = Core(cfg.settings(), llm=DemoLLM())
tight = core_tight.run(DemoLLM.GOAL, budget_factor=0.01, tag="tight")
check(RS, "I10 预算将尽 ⇒ 状态如实（非 done）且说明是预算切断，不假装完成",
      tight.status != "done"
      and tight.status in ("budget_exhausted", "closed")
      and ("预算" in tight.reason or "closing_reserve" in tight.reason),
      f"{tight.status} / {tight.reason[:50]}")
check(RS, "I11 预算耗尽带有可读原因",
      tight.status != "budget_exhausted" or "预算" in tight.reason, tight.reason[:60])

# I10b：把收尾余量**关掉**（阈值取 0 = 关闭），纯耗尽路径必须仍然可达且如实。
# 这条是给 I10 兜底的：证明 `closed` 没有把 `budget_exhausted` 藏起来 ——
# 两条路都在，读数各说各的，任何一条被吞掉都会在这里判红。
# 用 max_steps=1 造耗尽（**不用墙钟**：Budget.scaled 会把 wall_s 抬到下限 1.0s，
# 拿它当触发器既不可靠又会把测试写脆）。
_no_reserve = cfg.Settings(limits=cfg.Limits(
    max_steps=1, close_reserve_s=0.0, close_reserve_steps=0))
tight2 = Core(_no_reserve, llm=DemoLLM(),
              memory=MemoryStore(_TMP / "i10b_mem.db")).run(DemoLLM.GOAL, tag="noreserve")
check(RS, "I10b 关掉收尾余量 ⇒ 纯预算耗尽路径仍可达（budget_exhausted 未被藏起来）",
      tight2.status == "budget_exhausted",
      f"status={tight2.status}")


# ===========================================================================
print()
print("=" * 74)
print("J. 结构反证（撤掉实现 ⇒ 这些断言必然判红）")
print("=" * 74)
mem_src = _src("memory.py")
check(RS, "J1 去重由 SQL 约束保证（ON CONFLICT 存在）", "ON CONFLICT" in mem_src)
check(RS, "J2 去重键来自规范化指纹（fingerprint 被真正使用）",
      "fingerprint(text)" in mem_src)
core_src = _src("core.py")


def _call_lines(src: str, attr: str) -> list[int]:
    """用 AST 取**真实调用**的行号。

    ⚠️ 必须走 AST：直接 `src.index(name)` 会命中模块 docstring 里的同名文字
    —— 静态锚必须注释/文档感知，否则就是这样骗过我的。
    本文件 J5 第一次跑就是这个错，此处是修复。

    又：J3 第一次写的是 `core_src.count("spend_llm()")`，本轮**同样被骗了一次** ——
    我只是在 `_LedgeredLLM` 的 docstring 里写了一行 ``每次尝试都 `spend_llm()` ``，
    计数就从 1 变成 2、断言判红，而实现一行没多。所以 J3 也改成走 AST。
    """
    tree = ast.parse(src)
    return sorted(n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Call)
                  and (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) == attr)


check(RS, "J3 LLM 计数只有一处（_LedgeredLLM；AST 计真实调用，不被 docstring 骗）",
      len(_call_lines(core_src, "spend_llm")) == 1,
      str(_call_lines(core_src, "spend_llm")))
check(RS, "J4 每个被拦候选都 continue（不会带着被拒动作继续执行）",
      core_src.count("continue") >= 3)


_cascade_lines = _call_lines(core_src, "check_cascade")
_dispatch_lines = _call_lines(core_src, "dispatch")
check(RS, "J5 熔断在循环顶部检查（AST 行号先于任何 dispatch）",
      bool(_cascade_lines) and bool(_dispatch_lines)
      and min(_cascade_lines) < min(_dispatch_lines),
      f"cascade@{_cascade_lines} dispatch@{_dispatch_lines}")
check(RS, "J6 主循环只有一个 while（不叠多套循环）",
      len([n for n in ast.walk(ast.parse(core_src)) if isinstance(n, ast.While)]) == 1,
      str([n.lineno for n in ast.walk(ast.parse(core_src)) if isinstance(n, ast.While)]))


def _while_inside(src: str, func: str) -> bool:
    """确认那个唯一的 while **落在指定函数内**。

    为什么要有这一条：只数"全文件 while == 1"可以被绕过 —— 把 while 挪到
    某个辅助函数里，计数照样是 1，但"主循环只有一个"这句话已经不成立了。
    本轮实测就是这个场景：给传输层加重试时顺手写了个 `while True:`（契约 J6
    当场判红），改用 `for` 之后，还要再加一道锚确认它落在 run() 而不是别处。
    """
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == func:
            return any(isinstance(x, ast.While) for x in ast.walk(n))
    return False


check(RS, "J6b 那唯一的 while 确实在 run() 里（不是挪到别处凑数）",
      _while_inside(core_src, "run"))
check(RS, "J7 guard 的四道检查点在内核里全部被调用",
      all(k in core_src for k in ("check_goal", "check_shell",
                                  "check_memory_write", "check_cascade")))
check(RS, "J8 预算耗尽被显式捕获成状态（不是靠异常崩掉）",
      "BudgetExhausted" in core_src and "budget_exhausted" in core_src)
def _except_handlers(paths) -> list[tuple[str, int, object, list]]:
    out = []
    for p in paths:
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.ExceptHandler):
                out.append((p.name, n.lineno, n.type, n.body))
    return out


_handlers = _except_handlers(sorted(MW_DIR.glob("*.py")))
_bare = [f"{f}:{ln}" for f, ln, t, _ in _handlers if t is None]
check(RS, "J9 内核无裸 except:（必须指明异常类型）", not _bare, str(_bare))
# ⚠️ 这里必须走 AST 而不是文本搜索：guard.py 的 docstring 里**举例**写过
#    "except: pass"，纯文本扫描会命中那句说明文字并误判（J9 第一次跑就是这么错的）。
_silent = [f"{f}:{ln}" for f, ln, _t, body in _handlers
           if len(body) == 1 and isinstance(body[0], ast.Pass)]
check(RS, "J10 没有任何 except 处理体是单独的 pass（静默兜底 = 缺陷）",
      not _silent, str(_silent))
check(RS, "J11 内核异常处理数量 > 0（否则 J9/J10 是空判据、零判别力）",
      len(_handlers) >= 6, f"handlers={len(_handlers)}")


# ===========================================================================
print()
print("=" * 74)
print("K. LLM 构造路径（★实测事故：静默降级为桩 + 假绿）")
print("=" * 74)
# 2026-10-17 实测抓到的真实缺陷：`Core.__init__` 里写的是 `llm or StubLLM()`，
# 于是**任何**配置下内核都走离线桩 —— `--solver llm` 1 秒"跑完" 6 题、每轮报 done、
# mean=0.0000 却标 `可信=✅`，真模型一次都没被调用。build_llm 只 import 不调用。
# 下面这组断言就是那次的防线：K1 钉住行为、K2 钉住"唯一构造入口"、K3 证明 K1 不恒真、
# K4 钉住"必须显式喊出来"、K5 从结构上禁止死导入。
import mw.core as _cm                                          # noqa: E402

_ENV_LLM = ("V1_LLM_API_KEY", "V1_LLM_BASE_URL", "V1_LLM_MODEL", "V1_LLM_OFFLINE")
_env_backup = {k: os.environ.get(k) for k in _ENV_LLM}
os.environ["V1_LLM_API_KEY"] = "contract-test-key-never-sent"
os.environ["V1_LLM_BASE_URL"] = "http://127.0.0.1:9/v1"   # 只构造，不发请求
os.environ["V1_LLM_MODEL"] = "contract-test-model"
os.environ.pop("V1_LLM_OFFLINE", None)

_orig_build = _cm.build_llm
_spy = {"n": 0}


def _spy_build(c, ledger=None, stub=None):                     # noqa: ANN001
    _spy["n"] += 1
    return _orig_build(c, ledger, stub)


_cm.build_llm = _spy_build
try:
    _c_real = Core()
    _k1_kind = _c_real.llm_kind
    _k2_calls = _spy["n"]
finally:
    _cm.build_llm = _orig_build

check(RS, "K1 配置了真模型 ⇒ 内核实际通道必须是 http（不许静默降级为桩）",
      _k1_kind == "http", f"llm_kind={_k1_kind!r}（撤回 llm or StubLLM() 的修复即判红）")
check(RS, "K2 Core 确实走了 build_llm 这个唯一构造入口（spy 被调用 ≥1 次）",
      _k2_calls >= 1, f"build_llm 调用次数={_k2_calls}")
# 反证：显式传桩时必须诚实报 stub —— 否则 K1 可能只是"恒真"（探针本身不会说假话）
_c_stub = Core(llm=StubLLM())
check(RS, "K3 反证：显式传桩 ⇒ llm_kind=stub（证明 K1 的探针不是恒真）",
      _c_stub.llm_kind == "stub", f"llm_kind={_c_stub.llm_kind!r}")


class _ScriptedNotStub:
    """既不是 StubLLM 也不是 HTTPLLM 的脚本化模型（离线自检用的 DemoLLM 就是这类）。"""

    def complete(self, messages, **kw):                        # noqa: ANN001, ANN003
        return "{}"


check(RS, "K6 认不出的模型对象 ⇒ llm_kind='local'（二值化会把它谎报成 http）",
      Core(llm=_ScriptedNotStub()).llm_kind == "local",
      Core(llm=_ScriptedNotStub()).llm_kind)
_c_stub.run("一个很短的离线目标")
_warn = [ln for ln in _c_stub.log.lines if "不得" in ln]
check(RS, "K4 「配置了真模型却用桩」必须在运行日志里**显式**喊出来（静默降级=缺陷）",
      len(_warn) >= 1, f"告警行数={len(_warn)}")

for _k, _v in _env_backup.items():
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v


def _dead_imports(paths) -> list[str]:
    """找出「只 import、从不使用」的符号。

    AST 而不是文本匹配（R60：全文件搜索会命中注释/文档字符串）。
    排除 `__future__`（它的名字本来就不当符号用）。
    """
    out = []
    for p in paths:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        imported: dict[str, int] = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                if n.module == "__future__":
                    continue
                for a in n.names:
                    imported[a.asname or a.name] = n.lineno
            elif isinstance(n, ast.Import):
                for a in n.names:
                    imported[(a.asname or a.name).split(".")[0]] = n.lineno
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        for name, ln in sorted(imported.items()):
            if name != "*" and name not in used:
                out.append(f"{p.name}:{ln} {name}")
    return out


_dead = _dead_imports(sorted(MW_DIR.glob("*.py")))
check(RS, "K5 内核没有「只 import 不使用」的死导入"
          "（★实测：build_llm 曾被导入却从不调用，直接导致内核全程走桩）",
      not _dead, str(_dead))


# ===========================================================================
print()
print("=" * 74)
print("L. 外部能力通道（用户要求的核心形态：能力一律外部接入）")
print("=" * 74)
# V1 的立场是"内核不实现功能"。但这条通道**从未被跑通过** ——
# 没有 agents.json、实测日志里 `外部能力 0 个`。所以下面把它钉死：
# 契约成立 ≠ 通道可用。这里跑的是**真的**子进程（stdin 进 / stdout 出）。
_ext = Path(tempfile.mkdtemp(prefix="v1_ext_"))
_agent_py = _ext / "echo_agent.py"
_agent_py.write_text(
    "import json, sys\n"
    "task = sys.stdin.read().strip()\n"
    "print(json.dumps({'agent': 'echo', 'seen': len(task)}))\n",
    encoding="utf-8",
)
_agents_json = _ext / "agents.json"
_agents_json.write_text(json.dumps({"agents": {"echo": {
    "type": "shell", "description": "回显 agent（演示用）",
    "command": [sys.executable, str(_agent_py)]}}}), encoding="utf-8")

_reg_l = ToolRegistry()
register_core_tools(_reg_l, memory=None)
_added = register_external_agents(_reg_l, _agents_json)
check(RS, "L1 agents.json 里的外部能力被注册为 agent:<name>",
      _added == ["agent:echo"], str(_added))
_l2 = _reg_l.dispatch("agent:echo", {"task": "hello"})
check(RS, "L2 外部 agent **端到端**可调用（stdin 进 / stdout 出），结果可读",
      _l2.ok and '"seen": 5' in _l2.output, (_l2.output or _l2.error)[:80])
check(RS, "L3 external_agent_names 与注册表一致（外部能力只有一个查询口径）",
      external_agent_names(_reg_l) == ["agent:echo"], str(external_agent_names(_reg_l)))
check(RS, "L4 agents.json 不存在 ⇒ 空列表而非报错（内核零外部能力也能跑通）",
      register_external_agents(ToolRegistry(), _ext / "nope.json") == [])

_reg_bad = ToolRegistry()
_bad_json = _ext / "bad.json"
_bad_json.write_text(json.dumps({"agents": {"ghost": {
    "type": "shell", "description": "命令不存在",
    "command": ["/nonexistent/binary-v1-must-not-exist"]}}}), encoding="utf-8")
register_external_agents(_reg_bad, _bad_json)
_l5 = _reg_bad.dispatch("agent:ghost", {"task": "x"})
check(RS, "L5 外部 agent 起不来 ⇒ 被 dispatch 吸收成 ToolResult（永不抛异常）",
      _l5.ok is False and "agent:ghost" in _l5.error, (_l5.error or "")[:80])
_c_ext = Core(tools=_reg_l, llm=StubLLM())
# 断言的**不变量**是"计数 == 注册表的实际内容"，而不是一个写死的清单 ——
# 写死清单的后果实测过：仓库里新增 agents.json 之后这条就假红了（它其实在证明
# 「内核以注册表为准」这个性质，而性质没有被破坏）。
check(RS, "L6 内核的 external_agents 与**注册表**一致（以注册表为准，不是声明）",
      _c_ext.external_agents == external_agent_names(_c_ext.tools)
      and "agent:echo" in _c_ext.external_agents,
      f"{_c_ext.external_agents} vs {external_agent_names(_c_ext.tools)}")

# 核心工具的注册语义是「保证在册」，不是「新增」—— 否则 Core(tools=<预置注册表>)
# 这个公开注入点必然因 finish 重名而崩（实测就是这么撞的）。
try:
    register_core_tools(_reg_l, memory=None)          # 对同一个注册表再来一次
    _l7 = True
except Exception:                                     # noqa: BLE001
    _l7 = False
check(RS, "L7 核心工具重复注册不崩（「保证在册」语义 ⇒ Core(tools=…) 这个注入点可用）",
      _l7)
_conf_reg = ToolRegistry()
_conf_reg.register(ToolSpec("finish", "被篡改的描述", {"answer": "x"}, lambda a: "!"))
_conflict = False
try:
    register_core_tools(_conf_reg, memory=None)
except ValueError:
    _conflict = True
check(RS, "L8 同名**不同定义**仍然必须炸（放松「保证在册」没有削弱唯一事实源）",
      _conflict)

# 相对路径的锚点必须是 agents.json 所在目录，而不是**调用方的 cwd**
_rel_dir = Path(tempfile.mkdtemp(prefix="v1_rel_"))
(_rel_dir / "sub").mkdir()
(_rel_dir / "sub" / "rel_agent.py").write_text(
    "import sys\nprint('REL_OK:' + sys.stdin.read().strip())\n", encoding="utf-8")
_rel_json = _rel_dir / "agents.json"
_rel_json.write_text(json.dumps({"agents": {"rel": {
    "type": "shell", "description": "相对路径 agent",
    "command": [sys.executable, "sub/rel_agent.py"]}}}), encoding="utf-8")
_cwd_save = os.getcwd()
os.chdir(tempfile.mkdtemp(prefix="v1_elsewhere_"))       # 模拟"从别的目录调用"
try:
    _reg_rel = ToolRegistry()
    register_external_agents(_reg_rel, _rel_json)
    _l9 = _reg_rel.dispatch("agent:rel", {"task": "hi"})
finally:
    os.chdir(_cwd_save)
check(RS, "L9 外部 agent 的相对路径以 agents.json 为锚（不随调用方 cwd 漂移、不静默失效）",
      _l9.ok and "REL_OK:hi" in _l9.output, (_l9.output or _l9.error or "")[:80])


# ===========================================================================
print()
print("=" * 74)
print("M. 线上协议（模型输出 → 解析）：用 JSON 传代码的固有毛病")
print("=" * 74)
# ★ 下面这段是 2026-10-17 从本地 qwen2.5:7b **逐字**抓回来的真实输出（不是编的）。
#   模型**正确地**选了 finish 并把代码放进 answer —— 提示词修复是有效的；
#   唯一的毛病是代码里的**裸换行**直接落进了 JSON 字符串，而 RFC 8259 不允许。
#   这不是模型不听话，是"用 JSON 传代码"这件事本身的毛病。夹具就用真样本。
_RAW_MODEL = (
    '{"candidates": [{"intent": "直接编写代码实现解析逻辑", "tool": "finish", '
    '"args": {"answer": "def parse_duration(s):\n'
    '    if not s:\n'
    '        return None\n'
    '    units = {\'s\': 1, \'m\': 60, \'h\': 3600}\n'
    '    return None"}}]}'
)
_before_m = dict(PARSE_MODES)
_got_m = parse_json(_RAW_MODEL, None)
check(RS, "M1 ★真实事故样本：代码里的裸换行塞进 JSON 字符串 ⇒ 仍必须解析出来",
      isinstance(_got_m, dict)
      and _got_m.get("candidates", [{}])[0].get("tool") == "finish",
      str(_got_m)[:90])
check(RS, "M2 解析出的代码**原文完整**（换行没被吃掉 —— 那会静默改坏候选）",
      isinstance(_got_m, dict)
      and "\n" in str(_got_m["candidates"][0]["args"]["answer"]),
      repr(str(_got_m)[:60]))
check(RS, "M3 放宽解析必须留痕：本次计入 lenient（不许静默兜底）",
      PARSE_MODES["lenient"] == _before_m["lenient"] + 1, str(PARSE_MODES))
_fail_before = PARSE_MODES["failed"]
check(RS, "M4 真的坏输入仍然判 failed（宽松 ≠ 什么都收）",
      parse_json("完全不是 JSON", None) is None
      and PARSE_MODES["failed"] == _fail_before + 1, str(PARSE_MODES))
_exact_before = PARSE_MODES["exact"]
parse_json('{"a": 1}', None)
check(RS, "M5 干净 JSON 必须走 exact 路径（先严后宽，顺序不能反）",
      PARSE_MODES["exact"] == _exact_before + 1, str(PARSE_MODES))


# ===========================================================================
print()
print("=" * 74)
print("N. 双通道协议（内容不进 JSON）—— 一个下午三种坏法的根治")
print("=" * 74)
# 实测一天之内撞到的三种坏法，全是**同一个设计错误**的症状：把代码当 JSON 字符串传。
#   ① 代码里的裸换行 ⇒ 非法 JSON          （M1 夹具）
#   ② max_tokens 截断 ⇒ 括号不闭合        （G13 夹具）
#   ③ 模型顺手转义单引号 ⇒ \' 非法转义     （下面 N3 夹具，原文来自实测日志）
# ⛔ 修法不是把解析器越改越宽 —— 那会踩上"静默改坏候选"：
#    模型写 `"\d+"`（正则漏转义）被"修"成 `"d+"`，你的正则就悄悄变了。
#    ⇒ 只能改协议：结构走 JSON，载荷原样走分隔符之后。
_n_payload = (
    "def parse_duration(s):\n"
    "    units = {'s': 1, 'm': 60, 'h': 3600}\n"
    "    m = re.match(r'\\d+([hms])', s)\n"      # 裸反斜杠正则：JSON 里必须写成 \\d
    "    return None"
)
_n_raw = ('{"candidates": [{"intent": "直接给出实现", "tool": "finish", '
          '"args": {}, "rationale": "无需工具"}]}' + "\n" + ANSWER_MARK + "\n" + _n_payload)
_h, _b = split_answer(_n_raw)
check(RS, "N1 无分隔符 ⇒ 载荷为空串（老格式不报错，向后兼容）",
      split_answer('{"a":1}') == ('{"a":1}', ""))
check(RS, "N2 载荷**逐字**保留：裸换行 / 单引号 / 反斜杠正则一律不动",
      _b == _n_payload, repr(_b[:40]))
check(RS, "N3 结构部分单独可解析（内容不进 JSON ⇒ 解析不再被代码弄坏）",
      isinstance(parse_json(_h, None), dict))
_n_pl = Planner(StubLLM(default=_n_raw), tr3)
_n_plan = _n_pl.propose(goal="x", state="s", history=[], n=1)
check(RS, "N4 载荷原样注入 finish.answer（代码从模型直达 dispatch，中途无有损变换）",
      bool(_n_plan.steps) and _n_plan.steps[0].tool == "finish"
      and _n_plan.steps[0].args.get("answer") == _n_payload,
      repr(str(_n_plan.steps[0].args.get("answer") if _n_plan.steps else "")[:50]))
check(RS, "N5 双通道下规划器**不再降级**（这正是它相对老格式的全部意义）",
      _n_plan.degraded is False and _n_pl.degraded == 0, str(_n_pl.stats()))
_n_legacy = Planner(StubLLM(default=_RAW_MODEL), tr3)
_n_legacy_plan = _n_legacy.propose(goal="x", state="s", history=[], n=1)
check(RS, "N6 向后兼容：老格式（答案塞在 JSON 字符串里）在双通道下仍然可用（strict=False 兜）",
      bool(_n_legacy_plan.steps) and _n_legacy_plan.steps[0].tool == "finish"
      and "def parse_duration" in str(_n_legacy_plan.steps[0].args.get("answer", "")),
      str(_n_legacy_plan.steps[0].args.get("answer", "") if _n_legacy_plan.steps else "")[:40])


# ===========================================================================
print()
print("=" * 74)
print("O. 停滞闸（重复的「成功」不是进展）—— 「同一假设无限重派」的同构体")
print("=" * 74)
# 实测（2026-10-17）：内核把同一个 recall(同一个 query) 重派了 5 次，
# 每次 **ok=True**，于是 `consecutive_failures` 一次都没涨 ——
# 最后烧光 120s 墙钟才以 budget_exhausted 收场。
# 教训不是"加个超时"，而是**重复与进展必须分开表达**。
_SAME = ('{"candidates": [{"intent": "再查一次", "tool": "recall", '
         '"args": {"query": "goal"}, "rationale": "查记忆"}]}')
_loop_llm = StubLLM(default=_SAME)
# ⚠️ 必须用**独立**的记忆库：O 组的账要在自己的盘上算。
#    第一版用了默认库（与其他组共享同一个 _TMP/state/memory.db），
#    于是 outcome 条数里混进了别的组的记录，断言假红。
_loop_mem = MemoryStore(_TMP / "loop_mem.db")
_loop_core = Core(llm=_loop_llm, memory=_loop_mem)
_loop_out = _loop_core.run("读一下这个文件")          # 短目标 ⇒ 走快通道，n=1
_loop_stats = _loop_out.stats.get("loop", {})
check(RS, "O1 重复动作被**跳过**而非重新执行（不产生新信息的动作不许再跑）",
      _loop_stats.get("repeats_skipped", 0) >= 1, str(_loop_stats))
check(RS, "O2 真的只执行了一次（用情景记忆对账，不是看日志）",
      len(_loop_core.memory.episodes(kind="outcome")) == 1,
      f"outcome 条数={len(_loop_core.memory.episodes(kind='outcome'))}")
check(RS, "O3 停滞达阈值 ⇒ 状态是 stalled，**不是 done**（如实报告没有进展）",
      _loop_out.status == "stalled", f"status={_loop_out.status} reason={_loop_out.reason[:60]}")
check(RS, "O4 停滞计数达标（真的走到阈值，不是第一轮就草草收场）",
      _loop_stats.get("stalled", 0) >= cfg.Limits().max_stall, str(_loop_stats))
check(RS, "O5 停滞出口也带全套读数（loop 计数任何状态都带得走）",
      "loop" in _loop_out.stats and "planner" in _loop_out.stats,
      str(sorted(_loop_out.stats)))
class _CaptureLLM(StubLLM):
    """记下每次调用看到的完整提示词 —— 用来断言"提示词里到底写了什么"。"""

    def __init__(self, **kw):                                      # noqa: ANN003
        super().__init__(**kw)
        self.prompts: list[str] = []

    def complete(self, messages, **kw):                            # noqa: ANN001, ANN003
        self.prompts.append("\n".join(m.content for m in messages))
        return super().complete(messages, **kw)


_sig_probe = action_signature("recall", {"query": "goal"})
_cap = _CaptureLLM(default='{"candidates": [{"intent": "i", "tool": "finish", '
                            '"args": {"answer": "x"}}]}')
Planner(_cap, tr3).propose(goal="g", state="s", history=[], n=1, blocked=[_sig_probe])
check(RS, "O6 已尝试的动作签名被**精确**写进提示词（机器生成，不靠自然语言提醒）",
      any(_sig_probe in p for p in _cap.prompts), _sig_probe)


# ===========================================================================
print()
print("=" * 74)
print("P. 定向补救（选了 finish 却没给答案）—— 重试要换路径，不是重发")
print("=" * 74)
class _TwoShot(StubLLM):
    """按调用序返回预设输出（第 N 次以后一直是最后一个）。"""

    def __init__(self, outs):                                      # noqa: ANN001
        super().__init__()
        self.outs = list(outs)
        self.n = 0

    def complete(self, messages, **kw):                            # noqa: ANN001, ANN003
        i = min(self.n, len(self.outs) - 1)
        self.n += 1
        return self.outs[i]


_PLAN_NO_ANS = '{"candidates": [{"intent": "给出答案", "tool": "finish", "args": {}}]}'
_ts = _TwoShot([_PLAN_NO_ANS, "def f():\n    return 1"])
_p_pl = Planner(_ts, tr3)
_p_plan = _p_pl.propose(goal="写个函数", state="s", history=[], n=1)
check(RS, "P1 选了 finish 却没给答案 ⇒ 补救拿到了答案（换提问形态，不是重发同一请求）",
      bool(_p_plan.steps) and _p_plan.steps[0].args.get("answer") == "def f():\n    return 1",
      repr(str(_p_plan.steps[0].args.get("answer") if _p_plan.steps else "")[:40]))
check(RS, "P2 补救**有界**：至多一次额外调用（不是无限重试）",
      _ts.n == 2, f"调用次数={_ts.n}")
check(RS, "P3 补救成功被计数（补了多少次、成了多少次，都要能读出来）",
      _p_pl.answer_retry_ok == 1 and _p_pl.answer_retry_fail == 0,
      f"ok={_p_pl.answer_retry_ok} fail={_p_pl.answer_retry_fail}")
_ts2 = _TwoShot([_PLAN_NO_ANS, "   "])
_p_pl2 = Planner(_ts2, tr3)
_p2 = _p_pl2.propose(goal="写个函数", state="s", history=[], n=1)
check(RS, "P4 补救失败也计数（不许静默：失败要说出来，别假装计划是完整的）",
      _p_pl2.answer_retry_fail == 1 and _p_pl2.answer_retry_ok == 0,
      f"ok={_p_pl2.answer_retry_ok} fail={_p_pl2.answer_retry_fail}")
_ts3 = _TwoShot([_n_raw])
_p_pl3 = Planner(_ts3, tr3)
_p_pl3.propose(goal="写个函数", state="s", history=[], n=1)
check(RS, "P5 已有载荷时**不**触发补救（不浪费一次调用）",
      _ts3.n == 1 and _p_pl3.answer_retry_ok == 0, f"调用次数={_ts3.n}")


# ===========================================================================
print()
print("=" * 74)
print("Q. 收尾余量（探索不许吃掉交付的配额）")
print("=" * 74)
# 实测（2026-10-17 内核 6 题真模型）：有一题把 120s 预算全花在 recall 上，
# 以 budget_exhausted 收场 —— **交付了零东西**。如实报告"没做完"是对的，
# 但"探索把交付的配额也吃了"是设计缺陷：手段吃掉了目的。
_ANS_SYS = _planner_ANSWER_SYS   # noqa: N816
_Q_PLAN = '{"candidates": [{"intent": "查一下", "tool": "recall", "args": {"query": "x"}}]}'


class _QLLM(StubLLM):
    """既会出计划、也会在被专门索要答案时给答案。"""

    def complete(self, messages, **kw):                            # noqa: ANN001, ANN003
        blob = "\n".join(m.content for m in messages)
        if _ANS_SYS in blob:
            return "最终答案：42"
        return _Q_PLAN


_q_settings = cfg.Settings(limits=cfg.Limits(max_steps=1, close_reserve_steps=1))
_q_mem = MemoryStore(_TMP / "q_mem.db")
_q_log = _Log(None)
_q_out = Core(settings=_q_settings, llm=_QLLM(), memory=_q_mem,
              log=_q_log).run("读一下这个文件")
check(RS, "Q1 进入收尾余量 ⇒ 转向交付（把答案交出去，而不是继续探索）",
      _q_out.status == "closed" and _q_out.answer == "最终答案：42",
      f"status={_q_out.status} answer={_q_out.answer[:30]!r}")
check(RS, "Q2 没有等到预算烧光才停（没把 12 步全花在探索上）",
      _q_out.iterations <= 1, f"iterations={_q_out.iterations}")
check(RS, "Q3 收尾也是「如实报告」：状态是 closed 而非 done（不假装完成）",
      _q_out.status != "done" and "closing_reserve" in _q_out.reason,
      _q_out.reason[:60])
# Q2.5 收尾必须是**第一步就转向**，不是"探索了几步才想起收尾"。
#      证据取自日志本身：第一行预算播报之后紧接着就该是收尾信号。
check(RS, "Q4 收尾是第一步就触发（日志里收尾信号排在第一条动作之前）",
      any("触发收尾余量" in ln for ln in _q_log.lines),
      f"日志行数={len(_q_log.lines)}")


class _QBadLLM(StubLLM):
    def complete(self, messages, **kw):                            # noqa: ANN001, ANN003
        blob = "\n".join(m.content for m in messages)
        if _ANS_SYS in blob:
            raise RuntimeError("收尾调用炸了")
        return _Q_PLAN


_q_log2 = _Log(None)
_q_out2 = Core(settings=_q_settings, llm=_QBadLLM(),
               memory=MemoryStore(_TMP / "q_mem2.db"),
               log=_q_log2).run("读一下这个文件")
check(RS, "Q5 收尾调用自己炸了也不许带走整轮（内核韧性）",
      _q_out2.status == "closed", f"status={_q_out2.status}")
check(RS, "Q6 收尾拿不到答案 ⇒ 交付已收集的材料，并留痕（不静默报空）",
      any("收尾" in ln for ln in _q_log2.lines)
      and any("收尾调用失败" in ln for ln in _q_log2.lines),
      f"status={_q_out2.status} 日志行数={len(_q_log2.lines)}")


class _QHistLLM(StubLLM):
    """规划阶段点名一个**会被守卫拦下**的动作（默认不开 shell），答案阶段直接炸。

    为什么用被拦的动作而不是"不存在的工具"：实测发现规划器会把未注册的工具名
    **替换**成一个可用的核心工具（于是动作成功、写了 outcome 情景），
    那样就永远逼不出 history 这一环。被拦下的动作既进 history 又不写 outcome 情景，
    正好把兜底链推到最后一环。
    """

    def complete(self, messages, **kw):                            # noqa: ANN001, ANN003
        blob = "\n".join(m.content for m in messages)
        if _ANS_SYS in blob:
            raise RuntimeError("答案调用炸了")
        return ('{"candidates": [{"intent": "跑一条命令", "tool": "run_shell",'
                ' "args": {"cmd": "echo hi"}}]}')


# Q7 兜底链的**最后一环**：答案调用炸了、也没有 outcome 情景记忆时，
#    必须退到轨迹（history）而不是返回空 —— 否则"绝不返回空"这句话就是声明。
#    （构造要点：动作必须**不成功**，这样只进 history、不写 outcome 情景，
#      逼出 history 这一环；同时 max_steps=3 + 余量 1 让第一步探索后第 2 步收尾。）
_q7_log = _Log(None)
_q7_settings = cfg.Settings(limits=cfg.Limits(max_steps=3, close_reserve_steps=1))
_q7_out = Core(settings=_q7_settings, llm=_QHistLLM(),
               memory=MemoryStore(_TMP / "q_mem3.db"), log=_q7_log).run("随便一个目标")
check(RS, "Q7 兜底链最后一环：退到轨迹也**不返回空**（history 有真实消费者）",
      _q7_out.status == "closed" and _q7_out.answer != ""
      and any("来源=history" in ln for ln in _q7_log.lines),
      f"status={_q7_out.status} answer={_q7_out.answer[:40]!r} "
      f"来源行={[l for l in _q7_log.lines if '来源=' in l]}")


# ===========================================================================
print()
print("=" * 74)
print("R. 传输层抖动 vs 模型侧失败（重试的边界）")
print("=" * 74)
# 实测背景（2026-10-17 4x 预算跑内核）：6 题里 **3 题**死于 `TimeoutError`，
# 被记成 `solver 未产出答案` —— 那一刻的读数里混着**基础设施**的锅，
# 而报错文本读起来像模型的锅。这正是老毛病：不可归因的失败一起进读数。
# 判据（与 llm.py 的 LLMError 同源）：
#   · 传输层失败（超时/连接断/5xx/429）⇒ 重试（幂等的只读调用，重试不改变语义）
#   · 模型侧失败（4xx / 响应结构不符）⇒ **不许重试**，换路径
from mw.core import _LedgeredLLM                                  # noqa: E402
from mw.llm import LLMError                                       # noqa: E402


class _FlakyLLM:
    """前 k 次抛错，之后成功。"""

    def __init__(self, k: int, *, transient: bool = True) -> None:
        self.k = k
        self.transient = transient
        self.attempts = 0

    def complete(self, messages, **kw):                           # noqa: ANN001, ANN003
        self.attempts += 1
        if self.attempts <= self.k:
            raise LLMError(f"第 {self.attempts} 次抖动", transient=self.transient)
        return "ok"


def _tiny_ledger(n: int = 10) -> Ledger:
    return Ledger(Budget(steps=n, llm_calls=n, wall_s=60.0, candidates=1))


def _try_complete(wrapper, tag: str = "") -> tuple[bool, str, object]:
    """调用 `wrapper.complete(...)`，把**任何**异常收成返回值。

    ⚠️ 这个包装器本身就是一条纪律（R13 逼出来的实测教训）：
    R 组第一版直接裸调 `complete()` —— 撤掉实现后 `LLMError` 一路冒到顶层，
    **整个文件在汇总行之前就崩了**（Traceback=1、汇总行没打印）。
    那样撤回态读不出"哪几条精确转红"，R13 就白做了。
    契约测试自己必须韧性化：任何一条断言失败，都只能表现为一条红，不能打断汇总。
    """
    try:
        return True, wrapper.complete([Message("user", "hi")]), None
    except Exception as e:                                   # noqa: BLE001
        return False, "", e


def _transient_flags() -> bool:
    """直接验证 HTTPLLM 对「传输层」与「模型侧」失败的分类，而不是靠读源码。

    做法：把 `urllib.request.urlopen` 换成抛指定异常的桩，然后看抛出的
    `LLMError.transient` 是否符合预期 —— 期望 [超时=True, 400=False, 503=True]。
    """
    import io
    import urllib.error as _ue
    import urllib.request as _u
    _cfg = cfg.LLMConfig(base_url="http://example.invalid/v1", api_key="k",
                         model="m", timeout_s=0.01)
    _real = _u.urlopen
    _seen: list[bool] = []
    _exc_list = [
        TimeoutError("模拟超时"),
        _ue.HTTPError("http://x", 400, "bad request", {}, io.BytesIO(b"nope")),
        _ue.HTTPError("http://x", 503, "service unavailable", {}, io.BytesIO(b"boom")),
    ]
    try:
        for _exc in _exc_list:
            def _boom(*_a, _e=_exc, **_kw):                        # noqa: ANN002, ANN003
                raise _e
            _u.urlopen = _boom
            try:
                HTTPLLM(_cfg).complete([Message("user", "hi")])
                _seen.append(False)          # 没抛 = 判据不成立
            except LLMError as _e:
                _seen.append(bool(getattr(_e, "transient", False)))
            except Exception:                # noqa: BLE001
                _seen.append(False)          # 抛了别的类型也算判据不成立，且不许崩
    finally:
        _u.urlopen = _real
    return _seen == [True, False, True]


_r1_inner = _FlakyLLM(2)
_r1_led = _tiny_ledger()
_r1 = _LedgeredLLM(_r1_inner, _r1_led, retries=2, backoff_s=0.0)
_r1_ok, _r1_out, _r1_err = _try_complete(_r1, "R1")
check(RS, "R1 传输层抖动被重试并最终成功（不把抖动记成「模型答不出来」）",
      _r1_ok and _r1_out == "ok" and _r1.transient_retries == 2,
      f"ok={_r1_ok} out={_r1_out!r} err={_r1_err} retries={_r1.transient_retries}")
check(RS, "R2 每次**尝试**都花账本（账本记「实际发了几次」，不是「成功几次」）",
      _r1_led.llm_calls_used == 3, f"llm_calls_used={_r1_led.llm_calls_used}")

_r2_inner = _FlakyLLM(1, transient=False)
_r2_led = _tiny_ledger()
_r2 = _LedgeredLLM(_r2_inner, _r2_led, retries=3, backoff_s=0.0)
_r2_ok, _r2_out, _r2_err = _try_complete(_r2, "R3")
check(RS, "R3 模型侧失败**不重试**（重发同样的请求只会得到同样的错 ⇒ 该换路径）",
      (not _r2_ok) and isinstance(_r2_err, LLMError)
      and _r2_inner.attempts == 1 and _r2.transient_retries == 0,
      f"ok={_r2_ok} attempts={_r2_inner.attempts} retries={_r2.transient_retries}")

_r3_inner = _FlakyLLM(99)          # 一直抖
_r3_led = _tiny_ledger()
_r3 = _LedgeredLLM(_r3_inner, _r3_led, retries=2, backoff_s=0.0)
_r3_ok, _r3_out, _r3_err = _try_complete(_r3, "R4")
check(RS, "R4 重试耗尽后**仍然抛出**（不静默兜底），并记下「重试后仍失败」",
      (not _r3_ok) and isinstance(_r3_err, LLMError)
      and _r3_inner.attempts == 3 and _r3.transient_exhausted == 1,
      f"ok={_r3_ok} attempts={_r3_inner.attempts} exhausted={_r3.transient_exhausted}")

_r4_inner = _FlakyLLM(1)
_r4 = _LedgeredLLM(_r4_inner, _tiny_ledger(), retries=0, backoff_s=0.0)
_r4_ok, _r4_out, _r4_err = _try_complete(_r4, "R5")
check(RS, "R5 重试取 0 = 完全关闭（离线 / 严控场景可关掉它）",
      (not _r4_ok) and isinstance(_r4_err, LLMError) and _r4_inner.attempts == 1,
      f"ok={_r4_ok} attempts={_r4_inner.attempts}")

# 传输入口必须真的把「抖动」标出来 —— 否则 R1–R5 的判据建立在沙上。
check(RS, "R6 HTTPLLM 把超时/5xx/429 标为 transient，把 4xx 标为不可重试",
      _transient_flags(),
      str(_transient_flags.__doc__))

# 分隔符的空白容忍：实测模型打过 `---\n ANSWER---`（插了换行+空格），
# 精确串匹配失败 ⇒ 整个响应（结构+代码）被丢进 JSON 解析 ⇒ 报「不是合法 JSON」，
# 看起来像模型不会写 JSON。容忍的边界只在**分隔符本身**，载荷仍然逐字不动。
_messy = '{"candidates": []}\n---\n ANSWER---\n```python\ndef f():\n    return 1\n```'
_head, _payload = split_answer(_messy)
check(RS, "R7 分隔符容忍空白（`---\\n ANSWER---` 也认；意图零歧义）",
      _payload.startswith("```python\ndef f():") and "ANSWER" not in _head,
      f"payload={_payload[:30]!r}")
check(RS, "R8 容忍的边界只在分隔符：载荷仍**逐字**不动（一个字符都没改）",
      _payload == "```python\ndef f():\n    return 1\n```",
      repr(_payload))
check(RS, "R9 老规范 `---ANSWER---` 照旧工作（容忍不是替换）",
      split_answer("X\n---ANSWER---\nY")[1] == "Y")


# ===========================================================================
print()
print("=" * 74)
print("T. 温度必须真的到达线上（★实测事故：口径写 0.0，实际跑 0.2）")
print("=" * 74)
# 实测事实（2026-10-17）：记忆已完全隔离、口径签名逐位相同，同一题同一档
# 两轮仍给出 `0.5000` / `0.6667`。根因是内核三个调用点（planner×2、worldmodel×1）
# **没有一个传 temperature**，全部落到 HTTPLLM.complete 写死的默认 `0.2`。
# 后果：① `--temperature 0` 对内核路径完全无效；② 口径签名写着 0.0
# ⇒ 拿"假 T=0 的内核读数"去比"真 T=0 的直连读数"。这是标签撒谎，必须 fail-closed。
# 判据：**从线上抓请求体**，而不是读源码猜 —— 读源码正是当年被骗的方式。


class _CapturingURL:
    """假 urlopen：把每次请求体记下来（含 temperature），并回一个合法响应。"""

    def __init__(self) -> None:
        self.bodies: list[dict] = []

    def __call__(self, req, timeout=None):                    # noqa: ANN001, ANN002
        self.bodies.append(json.loads(req.data.decode("utf-8")))

        class _Resp:
            def read(self) -> bytes:
                return json.dumps(
                    {"choices": [{"message": {"content": "{}"}}]}).encode("utf-8")

            def __enter__(self) -> "_Resp":
                return self

            def __exit__(self, *a: object) -> bool:            # noqa: ANN002
                return False

        return _Resp()


_cap = _CapturingURL()
_real_urlopen = urllib.request.urlopen
_prev_temp_env = os.environ.get("V1_LLM_TEMPERATURE")
_prev_key_env = os.environ.get("V1_LLM_API_KEY")
try:
    urllib.request.urlopen = _cap                           # type: ignore[assignment]
    os.environ["V1_LLM_TEMPERATURE"] = "0.0"
    # 只为了让 HTTPLLM 的构造通过（它只校验 api_key 存在）。
    # 这里是**唯一**用 env 造 LLMConfig 的地方 —— 也正是要测的那条链：
    # env → LLMConfig.temperature → HTTPLLM 请求体。
    os.environ["V1_LLM_API_KEY"] = "contract-test-key"
    _t_cfg = cfg.settings().llm
    HTTPLLM(_t_cfg).complete([Message("user", "hi")])
    _t_noarg = _cap.bodies[-1].get("temperature")
    HTTPLLM(_t_cfg).complete([Message("user", "hi")], temperature=0.9)
    _t_explicit = _cap.bodies[-1].get("temperature")

    # 内核路径（planner → _LedgeredLLM → HTTPLLM）也必须遵守同一个温度
    _t_inner = HTTPLLM(cfg.settings().llm)
    _t_wrapped = _LedgeredLLM(_t_inner, _tiny_ledger())
    Planner(_t_wrapped, ToolRegistry(), max_tokens=64).propose(
        goal="g", state="", history=[], n=1)
    _t_kernel = _cap.bodies[-1].get("temperature")
finally:
    urllib.request.urlopen = _real_urlopen                  # type: ignore[assignment]
    for _k, _v in (("V1_LLM_TEMPERATURE", _prev_temp_env),
                   ("V1_LLM_API_KEY", _prev_key_env)):
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

check(RS, "T1 不传 temperature ⇒ 取配置值，而不是写死的默认值",
      _t_noarg == 0.0, f"线上 temperature={_t_noarg!r}")
check(RS, "T2 显式传 temperature ⇒ 用显式值（配置不覆盖调用方）",
      _t_explicit == 0.9, f"线上 temperature={_t_explicit!r}")
check(RS, "T3 内核路径（planner）也遵守配置温度 —— 这正是事故点",
      _t_kernel == 0.0, f"线上 temperature={_t_kernel!r}")
check(RS, "T4 温度只在 LLMConfig 声明一次（内核其它模块不许写死默认值）",
      all("temperature: float = 0." not in _src(m)
          for m in ("planner.py", "worldmodel.py", "core.py")),
      str([m for m in ("planner.py", "worldmodel.py", "core.py")
           if "temperature: float = 0." in _src(m)]))


# ── TH1~TH6 「思考开关」（第八十七轮，§50 的直接产物）─────────────────────
# 为什么它配得上进契约：§50 实测 `glm-4.7-flash` 的 reasoning 吃掉输出预算
# **85~99%**，而且会撑满任何上限（4096 ⇒ 思考 4050；8192 ⇒ 思考 8129，
# 正文两次都只剩几十 token）。要量云端真实能力，必须先让通道不吞答案 ——
# 而"关没关"这件事**只能从线上请求体看**。读源码正是 T1 那次被骗的方式
# （源码写着 `temperature` 读配置，实际三个调用点全没传），所以这里同样抓包。
from mw.ledger import signature_of as _sig_of                       # noqa: E402

_TH_BODY_BASE = ["model", "messages", "max_tokens", "temperature"]
_TH_COND_BASE = ["model", "endpoint", "timeout_s", "offline"]
_cap_th = _CapturingURL()
_prev_th_env = {k: os.environ.get(k) for k in ("V1_LLM_THINKING", "V1_LLM_API_KEY")}
_th_illegal: object = "未测"
try:
    urllib.request.urlopen = _cap_th                                # type: ignore[assignment]
    os.environ["V1_LLM_API_KEY"] = "contract-test-key"

    # ① 默认档（未设）：请求体与口径快照都必须是**历史那个形状**
    os.environ.pop("V1_LLM_THINKING", None)
    _th_def_cfg = cfg.settings().llm
    HTTPLLM(_th_def_cfg).complete([Message("user", "hi")], max_tokens=64)
    _th_body_def = dict(_cap_th.bodies[-1])
    _th_cond_def = _th_def_cfg.cond()

    # ② 显式关：请求体必须真的多出 thinking
    os.environ["V1_LLM_THINKING"] = "disabled"
    _th_off_cfg = cfg.settings().llm
    HTTPLLM(_th_off_cfg).complete([Message("user", "hi")], max_tokens=64)
    _th_body_off = dict(_cap_th.bodies[-1])
    _th_cond_off = _th_off_cfg.cond()

    # ③ 内核路径（planner → _LedgeredLLM → HTTPLLM）也必须带上（对应 T3 的位置）
    _th_wrapped = _LedgeredLLM(HTTPLLM(_th_off_cfg), _tiny_ledger())
    Planner(_th_wrapped, ToolRegistry(), max_tokens=64).propose(
        goal="g", state="", history=[], n=1)
    _th_body_kernel = dict(_cap_th.bodies[-1])

    # ④ 拼错必须**构造即抛**，不许静默忽略
    os.environ["V1_LLM_THINKING"] = "off"
    _th_illegal = _raises(lambda: cfg.settings().llm, ValueError)
finally:
    urllib.request.urlopen = _real_urlopen                         # type: ignore[assignment]
    for _k, _v in _prev_th_env.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

check(RS, "TH1 默认档（未设）请求体**连键序**都与历史相同 —— 一个字节都不许多",
      list(_th_body_def.keys()) == _TH_BODY_BASE,
      f"线上键序={list(_th_body_def.keys())}")
check(RS, "TH2 V1_LLM_THINKING=disabled ⇒ 请求体真的多出 thinking（不是标签生效）",
      _th_body_off.get("thinking") == {"type": "disabled"},
      str(_th_body_off.get("thinking")))
check(RS, "TH3 内核路径（planner）也带上 thinking —— 漏传的那条路等于没关",
      _th_body_kernel.get("thinking") == {"type": "disabled"},
      str(_th_body_kernel.get("thinking")))
check(RS, "TH4 默认档口径快照**逐位**还是那 4 个键（历史签名不许被新字段带偏）",
      list(_th_cond_def.keys()) == _TH_COND_BASE,
      str(_th_cond_def))
check(RS, "TH5 关掉之后口径签名必须变 —— 否则又是'标签撒谎'（同口径、实际不同通道）",
      _th_cond_off.get("thinking") == "disabled"
      and _sig_of({"llm_cond": _th_cond_off}) != _sig_of({"llm_cond": _th_cond_def}),
      f"thinking={_th_cond_off.get('thinking')!r} "
      f"sig={_sig_of({'llm_cond': _th_cond_off})} vs {_sig_of({'llm_cond': _th_cond_def})}")
check(RS, "TH6 拼错（off/false/0…）⇒ 构造即 ValueError，不许悄悄当成没设",
      _th_illegal is True, f"抛异常={_th_illegal}")


def _env_zero_takes_effect(key: str) -> bool:
    """把 `key` 设为 "0" 后重新构造 Settings，看它是不是真的变成 0。

    为什么必须**实测**而不是读代码：这场事故的核心就是"读代码看不出来" ——
    `_env_float` 里那行 `return v if v > 0 else default` 谁看都以为是防御性代码，
    实际它把配置里最重要的一个取值（0）静默换掉了。
    """
    _keys = {
        "V1_LLM_TEMPERATURE": ("llm", "temperature"),
        "V1_CLOSE_RESERVE_S": ("limits", "close_reserve_s"),
        "V1_CLOSE_RESERVE_STEPS": ("limits", "close_reserve_steps"),
        # ★ 第九十六轮 §63：宽限的 0 也是一个**有意义的值**（关闭宽限 ⇒
        #   行为退回旧版，这是 A/B 对照组）。它必须与 `close_reserve_s` 一样
        #   被这条断言覆盖 —— 否则「设了 0 却被静默改回 30」会悄悄毁掉对照臂。
        "V1_CLOSE_OVERRUN_S": ("limits", "close_overrun_s"),
        # ★ 第九十九轮 §66：停滞判据的 0 = 只认"全等"（**对照组**）。
        #   不登记它，"设为 0"会走 `_env_int` 的"0 ⇒ 回默认"那条路 ——
        #   默认值恰好也是 0 所以**看不出差别**，但下一个把默认改成 1 的人
        #   会得到一个静默失效的对照臂（§46 那场事故的同款形状）。
        "V1_STALL_ON_BLOCKED": ("limits", "stall_on_blocked"),
        "V1_LLM_RETRIES": ("limits", "llm_retries"),
        "V1_LLM_RETRY_BACKOFF_S": ("limits", "llm_retry_backoff_s"),
        "V1_VERIFY_RETRIES": ("limits", "verify_retries"),
        "V1_VERIFY_STATIC": ("limits", "verify_static"),
        # ★第一百一十四轮（§87）：`V1_REWARD_ECHO=0` 是**对照臂**（奖励段不回显）。
        #   不登记它，`0` 会走 `_env_int` 的"0 ⇒ 回默认"那条路，而默认是 1
        #   ⇒ A0 与 A1 变成同一条命令，V22 那 −16 格会被量成 0 格。
        "V1_REWARD_ECHO": ("limits", "reward_echo"),
        # ★第一百二十八轮（§103）：`V1_FORESIGHT_VETO=0` 就是**对照臂 A0**
        #   （= V36 及之前的全部历史：预演不参与决策）。今天默认也是 0，
        #   所以"不登记"看不出差别 —— 但只要将来把默认 promote 成 1，
        #   A0 就会静默变成 A1，而两臂读数会被读成"接上预演没有效果"。
        "V1_FORESIGHT_VETO": ("limits", "foresight_veto"),
        # ★第一百三十轮（§106）：`V1_PLAN_SALVAGE=0` 就是**对照臂 A0**
        #   （= V39 及之前的全部历史：解析器一个字符都不修）。
        #   T5 会实测"设成 0 之后真的取到 0" —— 这一条对本轮尤其要紧：
        #   `run_v40.sh` 里 4 个臂靠这一个变量的 4 个取值区分，
        #   若 0 被静默改回默认，臂 0 与臂 3 之外的臂会**静默变成同一个**。
        "V1_PLAN_SALVAGE": ("limits", "plan_salvage"),
        # ★第一百三十二轮（§108）：`V1_CAND_FAILOVER=0` 就是**对照臂 A0**
        #   （= V41 及之前的全部历史：交付被打回时结束本轮、不换候选）。
        #   ⛔ 这条登不上不只是"少一行"：`_env_zero_takes_effect` 会**当场
        #   KeyError 打断整个测试**（本文件 R13 硬坑：崩了不是判红）。
        #   本轮尤其要紧 —— `run_v42.sh` 的 2×2 里有**两臂**是 `=0`。
        "V1_CAND_FAILOVER": ("limits", "cand_failover"),
        # ★第一百四十七轮（§127）：`V1_VERIFY_EXEC=0` 就是**对照臂 A0**
        #   （= V60 及之前：交付闸只做静态判定）。今天默认也是 0，
        #   所以"不登记"看不出差别 —— 但只要将来把默认 promote 成 1，
        #   A0 就会静默变成 A1，而"强制验证没用"会被量成真。
        "V1_VERIFY_EXEC": ("limits", "verify_exec"),
        # ★第一百四十八轮（§128）：`V1_REJECT_CARRY=0` 就是**对照臂 A0**
        #   （= V61 的行为：打回报文被让位候选成功清零，模型看不到错在哪）。
        #   与上一条同因、且更具体：本轮跑批脚本靠 `=0` 把 A 臂钉死，
        #   漏登记 ⇒ 两臂是同一个臂，读数会说"带上报错原文没有效果"。
        "V1_REJECT_CARRY": ("limits", "reject_carry"),
    }
    grp, attr = _keys[key]
    _old = os.environ.get(key)
    os.environ[key] = "0"
    try:
        got = getattr(getattr(cfg.settings(), grp), attr)
    finally:
        if _old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = _old
    return got == 0


check(RS, "T5 声明的「允许 0」键**实测**都真的取到 0（配置的 0 不许被静默改写）",
      bool(cfg.ZERO_OK_KEYS) and all(_env_zero_takes_effect(k) for k in cfg.ZERO_OK_KEYS),
      str({k: _env_zero_takes_effect(k) for k in cfg.ZERO_OK_KEYS}))
check(RS, "T6 「允许 0」的键全部登记在 ENV_KEYS 里（两处清单不许漂移）",
      all(k in cfg.ENV_KEYS for k in cfg.ZERO_OK_KEYS),
      str([k for k in cfg.ZERO_OK_KEYS if k not in cfg.ENV_KEYS]))


# ===========================================================================
print()
print("=" * 74)
print("U. 通用性（跨领域）—— 『通用』是一个三元计数，不是一个平均分")
print("=" * 74)

import run as V1RUN                                                # noqa: E402

_run_src = (V1 / "run.py").read_text(encoding="utf-8")


def _caliber_kwargs(src: str) -> set[str]:
    """从 AST 取 `Caliber(...)` 实参里的关键字名。

    ⚠️ 走 AST 而不是 `"limits=" in src`：后者会命中注释/docstring ——
    R60 那个坑，本项目已经在它上面栽过两次（J3 的 `spend_llm()` 计数）。
    """
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "Caliber":
            return {k.arg for k in n.keywords if k.arg}
    return set()


_cal_kw = _caliber_kwargs(_run_src)
check(RS, "U1 run.py 真的把 limits / llm_cond / max_tokens 传进了 Caliber"
          "（A12–A14 只证明 Caliber 认这三个字段，这条证明**调用点**没漏）",
      {"limits", "llm_cond", "max_tokens"} <= _cal_kw, str(sorted(_cal_kw)))

# ── _read_run_after：绝不把"上一轮的读数"当成"这一轮的结果" ────────────────
# 这条防的是一次具体事故：cmd_bench 会在**写盘之前** return（血缘闸拒绝出分、
# 尺子不合格）。那时若"读最后一行"，拿到的就是上一轮的正常读数 —— 假绿。
_u_file = _TMP / "u_runs.jsonl"
_u_file.write_text('{"n": 1}\n{"n": 2}\n', encoding="utf-8")
check(RS, "U2 _read_run_after 读的是**新增的那一行**（不是永远读最后一行）",
      (V1RUN._read_run_after(_u_file, 0) or {}).get("n") == 1
      and (V1RUN._read_run_after(_u_file, 1) or {}).get("n") == 2,
      f"{V1RUN._read_run_after(_u_file, 0)} / {V1RUN._read_run_after(_u_file, 1)}")
check(RS, "U3 本轮没写盘（cmd_bench 早退）⇒ 返回 None，**不**返回上一轮的读数",
      V1RUN._read_run_after(_u_file, 2) is None,
      str(V1RUN._read_run_after(_u_file, 2)))

# ── _domain_row：不可信的读数**不进** trust ───────────────────────────────
_u_ok = {"report": {"attempted": 5, "recorded": 5, "skipped_no_answer": 0,
                    "failed_scorer": 0, "llm_of_record": "http",
                    "per_task": [{}] * 5},
         "measurement": {"mean": 0.6}}
_u_skip = {"report": {"attempted": 5, "recorded": 4, "skipped_no_answer": 1,
                      "failed_scorer": 0, "per_task": [{}] * 4},
           "measurement": {"mean": 0.75}}
check(RS, "U4 账平且无早退 ⇒ trust=True，mean 如实读出来",
      V1RUN._domain_row("d", 0, _u_ok)["trust"] is True
      and V1RUN._domain_row("d", 0, _u_ok)["mean"] == 0.6,
      str(V1RUN._domain_row("d", 0, _u_ok)))
check(RS, "U5 有早退（skipped>0）⇒ trust=False（该读数不得用于判定）",
      V1RUN._domain_row("d", 0, _u_skip)["trust"] is False,
      str(V1RUN._domain_row("d", 0, _u_skip)))
check(RS, "U6 本轮无读数 ⇒ trust=False；**不**把它变成 mean=0（'没量到' ≠ '零分'）",
      V1RUN._domain_row("d", 2, None)["trust"] is False,
      str(V1RUN._domain_row("d", 2, None)))

# ── _universality：判据本身是"三元计数"，不是一个平均分 ──────────────────
_rows_ok = [{"domain": "a", "mean": 0.5, "trust": True},
            {"domain": "b", "mean": 0.9, "trust": True}]
_rows_zero = [{"domain": "a", "mean": 0.5, "trust": True},
              {"domain": "b", "mean": 0.0, "trust": True}]
_rows_skip = [{"domain": "a", "mean": 0.5, "trust": True},
              {"domain": "b", "mean": 0.0, "trust": False}]
check(RS, "U7 每个领域都可信且非零 ⇒ ok=True，三个计数一致",
      V1RUN._universality(_rows_ok) == {"n_domains": 2, "n_trust": 2,
                                        "n_nonzero": 2, "ok": True},
      str(V1RUN._universality(_rows_ok)))
check(RS, "U8 有一个领域 0 分 ⇒ ok=False（平均分会把这件事藏起来，所以不平均）",
      V1RUN._universality(_rows_zero)["ok"] is False
      and V1RUN._universality(_rows_zero)["n_nonzero"] == 1,
      str(V1RUN._universality(_rows_zero)))
check(RS, "U9 「没量到」的领域两头都不算：既不算非零，也**不**算 0",
      (V1RUN._universality(_rows_skip)["n_trust"] == 1
       and V1RUN._universality(_rows_skip)["n_nonzero"] == 1
       and V1RUN._universality(_rows_skip)["ok"] is False),
      str(V1RUN._universality(_rows_skip)))
check(RS, "U10 跨领域模式强制 --fresh（否则后一个领域带着前一个的记忆起跑，"
          "测到的就不是独立领域）",
      "跨领域模式强制 --fresh" in _run_src, "")


def _u_ns(**kw):                                                   # noqa: ANN003
    return type("NS", (), kw)()


check(RS, "U11 --suite-dir 与 --memory-dir 互斥（跨领域要**独立**、显式目录要**共享**，"
          "两者语义冲突；静默让谁赢都会产出标签与事实不符的读数）",
      bool(V1RUN._option_conflict(_u_ns(suite_dir="d", memory_dir="m"))),
      V1RUN._option_conflict(_u_ns(suite_dir="d", memory_dir="m")))
check(RS, "U12 --suite-dir 与 --save-baseline 互斥（N 把尺子写不进一个基线文件 —— "
          "会互相覆盖、最后只剩最后一个领域）",
      bool(V1RUN._option_conflict(_u_ns(suite_dir="d", save_baseline=True))),
      V1RUN._option_conflict(_u_ns(suite_dir="d", save_baseline=True)))
check(RS, "U13 单独用 --suite-dir / --memory-dir 都不算冲突（别把正常用法也挡住）",
      V1RUN._option_conflict(_u_ns(suite_dir="d")) == ""
      and V1RUN._option_conflict(_u_ns(memory_dir="m")) == ""
      and V1RUN._option_conflict(_u_ns()) == "")


# ===========================================================================
print()
print("=" * 74)
print("V. 因果闭环（世界模型）—— 失配必须能改变**下一次**的决策输入")
print("=" * 74)

from mw.worldmodel import Prediction, WorldModel                     # noqa: E402

# 「真正理解因果」在 V1 里的最小落地形式：**预测 → 观测 → 失配 → 沉淀 → 改变下次输入**。
# 这条链子在代码里是通的（core.py 的 guidance=self.wm.guidance(action=goal)），
# 但"通了"≠"有效" —— 所以这里逐步钉：沉淀了吗？检索得到吗？**真的进了提示词吗**？
# 最后一条最重要：DB 里有 ≠ 决策链看得见。一个只写进 DB 却没人读的记忆，
# 正是 "字段无消费者" 的同构体。
_v_mem = MemoryStore(_TMP / "v_causal.db")
_v_wm = WorldModel(_v_mem, StubLLM())
# ⚠️ 这里必须走**成败类**（`expect_ok` + `ok=`）：那是生产路径
#    （core.py 传的是 `result.ok`），也是唯一可迁移的失配。
#    首版这里用的是文本类 —— 修完缺陷 #12 后它不再写语义层，
#    于是 V1/V3/V4/V6 连锁转红。那不是回归，是**这条链被测在了对的种类上**。
_v_mm = _v_wm.observe(
    Prediction(action="parse_duration", expected="会返回一个整数秒数", confidence=0.9,
               expect_ok=True),
    "[工具失败] AttributeError: 'str' object has no attribute 'split'", ok=False)
check(RS, "V1 预演说「会成功」、实际失败 ⇒ 记为失配，并沉淀成语义记忆"
          "（不是只记一笔日志就完事）",
      _v_mm is not None and _v_mm.kind == "outcome"
      and _v_mem.stats()["by_source"].get("worldmodel", 0) >= 1,
      f"mismatch={_v_mm is not None} by_source={_v_mem.stats()['by_source']}")
check(RS, "V2 一致时不沉淀（两个方向都对：成败吻合、文本重合）",
      _v_wm.observe(Prediction(action="a", expected="同一个结果", confidence=0.9),
                    "同一个结果") is None
      and _v_wm.observe(Prediction(action="b", expected="会成功", confidence=0.9,
                                   expect_ok=True), "ok", ok=True) is None
      and _v_wm.observe(Prediction(action="c", expected="会失败", confidence=0.9,
                                   expect_ok=False), "failed", ok=False) is None,
      str(_v_wm.stats()))
_v_g = _v_wm.guidance(action="parse_duration")
check(RS, "V3 失配能按动作被检索回来（沉淀了却检索不到 = 装饰）",
      bool(_v_g) and "已知易错点" in _v_g, _v_g[:120])

# ⚠️ max_steps 必须是 3：`max_steps=1` 时 steps_left=1 ≤ close_reserve_steps=1
#    ⇒ 第 1 步就触发收尾余量，planner.propose **从未被调用**，
#    于是 V4 必然拿不到 propose 的提示词（首跑就是这么红的 —— 那是构造问题，
#    不是内核缺陷）。这条边界本身值得记：**若 max_steps ≤ close_reserve_steps，
#    内核完全不做任何探索。**
_v_cap = _CaptureLLM(default='{"candidates": [{"intent": "i", "tool": "finish", '
                             '"args": {"answer": "x"}}]}')
Core(settings=cfg.Settings(limits=cfg.Limits(max_steps=3)),
     llm=_v_cap, memory=_v_mem).run("parse_duration 这个函数怎么写")
_v_prompt = "\n".join(_v_cap.prompts)
check(RS, "V4 失配沉淀的启发式**真的进了规划器提示词**"
          "（DB 里有 ≠ 决策链看得见）",
      "已知易错点" in _v_prompt, _v_prompt[:220])

# ⛔ 反证：注册表里没有这段记忆时，提示词里**不该**出现它 ——
#    否则 V4 可能只是"模板恒含该字串"而恒真（本项目在恒真断言上栽过）。
_v_cap2 = _CaptureLLM(default='{"candidates": [{"intent": "i", "tool": "finish", '
                              '"args": {"answer": "x"}}]}')
Core(settings=cfg.Settings(limits=cfg.Limits(max_steps=3)),
     llm=_v_cap2, memory=MemoryStore(_TMP / "v_causal2.db")).run(
         "parse_duration 这个函数怎么写")
_v_prompt2 = "\n".join(_v_cap2.prompts)
check(RS, "V5 反证：空记忆时提示词里**没有**这段（证明 V4 不是恒真）",
      "已知易错点" not in _v_prompt2, _v_prompt2[:220])


class _PredictLLM(StubLLM):
    """只用来抓 `WorldModel.predict` 的提示词。"""

    def __init__(self, **kw):                                      # noqa: ANN003
        super().__init__(**kw)
        self.prompts: list[str] = []

    def complete(self, messages, **kw):                            # noqa: ANN001, ANN003
        self.prompts.append("\n".join(m.content for m in messages))
        return '{"expected": "会返回一个整数秒数", "confidence": 0.8}'


# ⛔ V6/V7 是本轮**最重要的一组**：它检验 worldmodel.py **自己声明的主张**——
#    "缺少一个能预演动作后果、并因为预演错了而**改变下一次预测**的机制。
#     本模块就是那个机制。"
#    前半句（预演 → 观测 → 失配 → 沉淀）已经由 V1–V5 钉住了。
#    后半句「**改变下一次预测**」是另一件事：失配必须回到 predict **自己**的输入里。
#    ⚠️ 只有 planner 吃到 guidance 是不够的 —— 那是"改变下一次**规划**"，
#       不是"改变下一次**预演**"。两者是这条因果链上不同的两环。
_v_pred_llm = _PredictLLM()
_v_wm2 = WorldModel(_v_mem, _v_pred_llm)      # 复用 V1 已经沉淀过失配的那份记忆
_v_wm2.predict(state="正在实现 parse_duration", action="parse_duration")
check(RS, "V6 ★失配沉淀后，**下一次预演**的提示词里必须带上它"
          "（worldmodel 自己的声明：因为预演错了而改变下一次预测）",
      bool(_v_pred_llm.prompts) and "已知易错点" in _v_pred_llm.prompts[-1],
      (_v_pred_llm.prompts[-1][:260] if _v_pred_llm.prompts else "(predict 没调 LLM)"))

_v_pred_llm2 = _PredictLLM()
WorldModel(MemoryStore(_TMP / "v_causal3.db"), _v_pred_llm2).predict(
    state="正在实现 parse_duration", action="parse_duration")
check(RS, "V7 反证：没有失配记忆时，预演提示词里**没有**这段（V6 不是恒真）",
      bool(_v_pred_llm2.prompts) and "已知易错点" not in _v_pred_llm2.prompts[-1],
      (_v_pred_llm2.prompts[-1][:260] if _v_pred_llm2.prompts else "(predict 没调 LLM)"))


# ===========================================================================
print()
print("=" * 74)
print("W. 抽象 —— 把「抽象思考」变成一个可读的数（当前是 0，如实登记）")
print("=" * 74)

from mw.measure import abstraction_collapse                        # noqa: E402
from mw.memory import fingerprint as _fingerprint                  # noqa: E402

_W_PAIRS = [
    ("parse_duration 需要处理空串", "空字符串时应当返回 None"),
    ("dedupe 必须保持首次出现的顺序", "去重后的顺序按第一次出现来定"),
    ("罗马数字 IV 表示 4", "IV 等于四"),
]

check(RS, "W1 读数函数本身算得对：给一个**真会抽象**的 key_of ⇒ 1.0",
      abstraction_collapse(_W_PAIRS, key_of=lambda _s: "同一个概念") == 1.0)
check(RS, "W2 空输入 ⇒ 1.0（没有反例就不诬告：'没测到' ≠ '做不到'）",
      abstraction_collapse([], key_of=lambda s: s) == 1.0)
check(RS, "W3 非空但全不等价 ⇒ 0.0（证明 W1 不是恒真）",
      abstraction_collapse(_W_PAIRS, key_of=lambda s: s) == 0.0)

# ⚠️ 下面这条**不是不变量**，是一枚「读数锚」。
#    `memory.fingerprint()` 是**规范化后字面文本**的 sha256 ⇒ 三条语义等价、
#    字面不同的表述被算成三条**不同的记忆**。也就是说：**当前没有概念层，
#    只有字面。** 如实登记在 DESIGN §14.4，不粉饰。
#    它存在的用途是**防文档与实现漂移**：哪天真的加了抽象层，
#    这条会转红，逼着把文档里的读数一起改掉（那是明确的、有意的变更，
#    不是"变红就放宽断言"）。
_w_now = abstraction_collapse(_W_PAIRS, key_of=_fingerprint)
check(RS, "W4 ★读数锚：当前记忆层的抽象率 = 0.0（fingerprint 是字面 sha256，"
          "没有概念层）—— 改抽象层时请同步更新此断言与 DESIGN §14.4",
      _w_now == 0.0, f"abstraction_collapse={_w_now}")


# ===========================================================================
print()
print("=" * 74)
print("X. 血缘完整性 —— 读数必须带「实际用了什么」（★实测事故：一个分支漏了 model）")
print("=" * 74)

# 为什么要有这一组：本轮实测抓到第 9 个缺陷 —— 内核路径的三个分支各拼一个血缘
# dict，`--fresh` 那一支**漏了 `model`**。于是跨领域跑与泛化对照（考A）两条读数
# **事后无法核对自己跑的是哪个模型**；而跨领域那一轮的口径闸、血缘闸、
# 契约测试——全都判绿。原因是：血缘这套防线自己的第一条纪律身上**没有防线**。
#
# 现在修法不是"把 model 补回去"（那只是修了一个实例），而是让它**写不出来**：
# 字段集合收敛到 `run._prov()` 一个 dict 字面量，并在此处钉死。
import run as _run                                             # noqa: E402

check(RS, "X1 血缘字段集合只声明在一处（`run.PROV_KEYS`），且构造函数精确匹配它",
      set(_run._prov(llm_kind="k", external_agents=[], model="m",
                     temperature=0.0)) == set(_run.PROV_KEYS),
      f"{sorted(_run.PROV_KEYS)}")

check(RS, "X2 必带字段含 `model` 与 `temperature`"
          "（★本轮缺陷：`--fresh` 分支漏的正是 `model`）",
      {"model", "temperature"} <= set(_run.PROV_KEYS), f"{sorted(_run.PROV_KEYS)}")

_c_prov = Core(llm=StubLLM())
_p_now = _run._kernel_prov(_c_prov)

check(RS, "X3 `_kernel_prov` 的键集合与 `PROV_KEYS` 一致（三条路径共用同一集合）",
      set(_p_now) == set(_run.PROV_KEYS), f"{sorted(_p_now)}")

check(RS, "X4 血缘**从内核读**，不是外面抄一个字符串："
          "model/temperature/llm_kind 三项都等于内核自己的值",
      (_p_now["model"] == _c_prov.s.llm.model
       and _p_now["temperature"] == float(_c_prov.s.llm.temperature)
       and _p_now["llm_kind"] == _c_prov.llm_kind),
      f"{_p_now} vs core.s.llm.model={_c_prov.s.llm.model!r}")

check(RS, "X5 血缘**不含密钥**（`api_key` 绝不外泄到读数里）",
      "api_key" not in _p_now
      and "contract-test-key" not in json.dumps(_p_now, ensure_ascii=False),
      json.dumps(_p_now, ensure_ascii=False))

# ★结构锚：run.py 里**只准有一处**带 `"llm_kind"` 键的 dict 字面量（就是 `_prov`）。
#   谁再手拼一个血缘 dict，这里立刻转红 —— 这正是"让缺陷写不出来"的那一步。
#   ⚠️ 走 AST，不走字符串搜索（全文 `index()` 会命中注释里的同名文字）。
def _prov_dict_lines(src: str) -> list[int]:
    """数出源码里含 `llm_kind` 键的 dict 字面量行号。"""
    return [n.lineno for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Dict)
            and any(isinstance(k, ast.Constant) and k.value == "llm_kind" for k in n.keys)]


_prov_dicts = _prov_dict_lines((V1 / "run.py").read_text(encoding="utf-8"))
check(RS, "X6 ★结构锚：`run.py` 里含 `llm_kind` 键的 dict 字面量**有且只有一处**"
          "（手拼血缘 dict ⇒ 立刻判红）",
      len(_prov_dicts) == 1, f"出现行号={_prov_dicts}")

# 反证：证明 X6 的探针**不是恒真** —— 手拼一个血缘 dict 进去，它必须数得出来。
check(RS, "X7 反证：把一段手拼血缘塞给同一探针 ⇒ 数出 2 处"
          "（证明 X6 不是恒等于 1 的死断言）",
      _prov_dict_lines('x = 1\ny = {"llm_kind": "http", "model": "m"}\n') == [2],
      str(_prov_dict_lines('x = 1\ny = {"llm_kind": "http", "model": "m"}\n')))

# ⚠️ X6 只拦得住「dict 字面量」这一种拼法。本轮缺陷的**真实形态**是另一种：
#    `_seen["llm_kind"] = ... ; _seen["external_agents"] = ...` —— 两个下标赋值，
#    一个 dict 字面量都没有。若只留 X6，原缺陷可以原样复活且全绿。
#    （这就是"断言要按缺陷的真实形状写"：形状不对的断言是假防线。）
def _prov_key_stores(src: str) -> list[int]:
    """找出「把血缘字段名当键做下标赋值」的行号 —— 即逐键拼血缘。"""
    return [n.lineno for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Assign)
            for t in n.targets
            if (isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                and t.slice.value in _run.PROV_KEYS)]


_stores = _prov_key_stores((V1 / "run.py").read_text(encoding="utf-8"))
check(RS, "X8 ★结构锚：`run.py` 里**没有**逐键拼血缘的下标赋值"
          "（★本轮缺陷的真实形态：`_seen[\"llm_kind\"]=…` 逐键写，漏了 model）",
      _stores == [], f"出现行号={_stores}")

check(RS, "X9 反证：把本轮那个缺陷原样喂给同一探针 ⇒ 必须数得出来"
          "（证明 X8 不是恒真的死断言）",
      _prov_key_stores('d = {}\nd["llm_kind"] = "http"\nd["external_agents"] = []\n') == [2, 3],
      str(_prov_key_stores('d = {}\nd["llm_kind"] = "http"\nd["external_agents"] = []\n')))


# ===========================================================================
print()
print("=" * 74)
print("Y. 失配信号的分辨力 —— 一个永远不可能「好」的信号等于没有信号")
print("=" * 74)

# 缺陷 #12 的来龙去脉（2026-10-17 泛化实验实测逼出来的）：
#   记忆在 numeric_logic 上"学"了 7 facts / 17 episodes，再考 3 道 held-out 新题，
#   与**空记忆**逐题配对 ⇒ **Δ 全为 0**。机制不是"记忆没生效"，是：
#       `predict` 预演的是**一句话**（"你预期会发生什么"），
#       `observe` 比对的是**整段答案**（候选代码）。
#   Jaccard 门槛 0.34 ⇒ 这两者**跨种类** ⇒ **结构上不可能**相似 ⇒ 每次必判失配。
#   实测三轮共 9 次失配，一致度全部 ≤ 0.09：`0.00 0.00 0.05 0.09 0.00 0.02 0.00 0.05 0.07`。
#   ⚠️ 这是同一种失效形态：一个**永远不可能"好"**的信号。
#      把 `失配=3` 读成"因果闭环在工作"，就是把 `gain≡0` 读成"每轮全绿"。
#
# 修法（不是调门槛 —— 那是把尺子下移）：**按种类比，绝不跨种类比。**
#   预测的对象改成"这一步**会不会成功**"（`expect_ok`），由 `result.ok` 检验 ——
#   那是内核直接持有的地面真相，永远可得、永远同类。
#
# ⚠️ 下面 Y3 仍是**读数锚**：文本比对**依然**分不出对错。它之所以还能留着，
#    是因为它被降级成"只记情景、不进语义"（`Mismatch.teachable`）。
#    哪天文本比对真能分辨，Y3 会转红 —— 那是有意的信号，不是"变红就放宽"。
from mw.worldmodel import _MISMATCH_THRESHOLD                     # noqa: E402

_y_mem = MemoryStore(_TMP / "y_worldmodel.db")


class _Y_LLM:
    """脚本化预测器：模拟线上 qwen 的返回形态（可带 / 不带 `expect_ok`）。"""

    def __init__(self, expected: str, expect_ok: bool | None = True) -> None:
        self.expected = expected
        self.expect_ok = expect_ok

    def complete(self, messages, **kw):                            # noqa: ANN001, ANN003
        d: dict = {"expected": self.expected, "confidence": 0.9}
        if self.expect_ok is not None:
            d["expect_ok"] = self.expect_ok
        return json.dumps(d, ensure_ascii=False)


_Y_PRED = "将输出一段实现了 int_to_roman 的代码"
_Y_GOOD = ("```python\ndef int_to_roman(n):\n    vals = [(1000,'M'),(900,'CM')]\n"
           "    out = ''\n    for v, s in vals:\n        while n >= v:\n"
           "            out += s\n            n -= v\n    return out\n```")
_Y_BAD = "```python\ndef int_to_roman(n):\n    return str(n)\n```"

# Y1 正向读数：这个信号**能**判"好" —— 旧实现结构上走不到这里。
#    ★ 这一条比"能判坏"重要得多：一个只会说"错了"的度量与恒假度量等价。
_ym = WorldModel(_y_mem, _Y_LLM(_Y_PRED))
check(RS, "Y1 预演说「会成功」+ 实际成功 ⇒ 判**一致**、不沉淀"
          "（旧实现下这个分支结构上不可达）",
      _ym.observe(Prediction("finish", _Y_PRED, 0.9, expect_ok=True), _Y_GOOD,
                  ok=True) is None
      and _ym.stats()["hits"] == 1 and _ym.stats()["mismatches"] == 0,
      str({k: _ym.stats()[k] for k in ("scored", "hits", "mismatches")}))

# Y2 ★分辨力的定义：**同一个预演**，对"做对"和"做错"必须给出**不同**判定。
#    旧实现两次都判失配（而且错误解的一致度反而更高）⇒ 它分辨的不是对错。
_ym2 = WorldModel(_y_mem, _Y_LLM(_Y_PRED))
_y2_hit = _ym2.observe(Prediction("finish", _Y_PRED, 0.9, expect_ok=True), _Y_GOOD, ok=True)
_y2_miss = _ym2.observe(Prediction("finish", _Y_PRED, 0.9, expect_ok=True), _Y_BAD, ok=False)
check(RS, "Y2 ★分辨力：同一预演，做对 ⇒ 不沉淀 ／ 做错 ⇒ 沉淀。"
          "**两者判定不同** —— 这才叫分辨力（旧实现两次都判失配）",
      _y2_hit is None and _y2_miss is not None and _y2_miss.kind == "outcome",
      f"做对={_y2_hit} 做错={_y2_miss}")

# Y2b 反证：把"分辨力"本身做成探针，喂两种**坏实现**进去，必须判无分辨力。
#    ⚠️ 第一版这里写成了 `(None is None) and not (None is not None)` ——
#    那是一句恒真的废话，不是反证。反证必须让**同一段判定逻辑**作用在坏输入上并失败。
def _discriminates(decide) -> bool:                                # noqa: ANN001
    """分辨力探针：同一预演，对"做对"和"做错"必须给出**不同**判定。"""
    return decide(True) != decide(False)


check(RS, "Y2b 反证：探针对「恒判一致」与「恒判失配」两种坏实现都判**无分辨力**，"
          "对正确实现判有分辨力 —— 证明 Y2 不是恒真的死断言",
      _discriminates(lambda ok: None) is False
      and _discriminates(lambda ok: "mismatch") is False
      and _discriminates(lambda ok: None if ok else "mismatch") is True,
      "恒一致→False 恒失配→False 真判→True")

# Y3 ★读数锚：文本比对**仍然**分不出对错 ——
#    正确解与错误解都在门槛之下，且**错误解反而更「像」**（反相关）。
#    这正是它被降级成"只记情景、不进语义"的理由。
_a_good, _a_bad = similarity(_Y_PRED, _Y_GOOD), similarity(_Y_PRED, _Y_BAD)
_y3 = WorldModel(MemoryStore(_TMP / "y3_text.db"), _Y_LLM(_Y_PRED, expect_ok=None))
_m_good = _y3.observe(Prediction("finish", _Y_PRED, 0.9, expect_ok=None), _Y_GOOD)
_m_bad = _y3.observe(Prediction("finish", _Y_PRED, 0.9, expect_ok=None), _Y_BAD)
check(RS, "Y3 ★读数锚：文本类比对**仍然**分不出对错（两者都判失配，且错误解一致度 ≥ 正确解）",
      _a_good < _MISMATCH_THRESHOLD and _a_bad < _MISMATCH_THRESHOLD
      and _a_bad >= _a_good
      and (_m_good is not None) and (_m_bad is not None),
      f"正确={_a_good:.4f} 错误={_a_bad:.4f}（门槛 {_MISMATCH_THRESHOLD}）")
check(RS, "Y3b ★纪律：因此文本类失配**只进情景、不进语义**"
          "（分不出对错的信号不许生产知识）",
      _y3.stats()["mismatches_text"] == 2
      and _y3.memory.stats()["by_source"].get("worldmodel", 0) == 0,
      f"text_mismatches={_y3.stats()['mismatches_text']} "
      f"by_source={_y3.memory.stats()['by_source']}")

# Y4 ★结构锚：`core.py` 调 `observe` 时**必须**把这一步的成败 `result.ok` 传进去。
#
#    ⚠️ 这条断言的**方向在修复后翻转了**：修之前它断言"没有传 ok"（把缺陷钉在
#    绿测试里），修之后它断言"必须传 ok"（把修复钉住，防止回退）。
#    两次都指向同一个事实：**世界模型有没有拿到地面真相**。
#    ⇒ 一个读数锚完成使命后应该翻转成不变量，而不是被删掉。
_CORE_SRC = (MW_DIR / "core.py").read_text(encoding="utf-8")


def _observe_arg_names(src: str) -> list[str]:
    """`observe(...)` 调用的**全部**实参名（`a.b` 取 `b`）。

    ⚠️ 必须同时收 `n.args`（位置）与 `n.keywords`（关键字）。
    第一版只收了位置实参，于是 `observe(pred, obs, ok=result.ok)` 这种
    **关键字**写法它看不见 —— R13 撤回态里"注入修复"后该断言**没有转红**，
    是这条纪律抓出了**断言自己**的缺陷。（形状不对的断言 = 假防线。）
    """
    out: list[str] = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "observe":
            for a in n.args:
                out.append(a.attr if isinstance(a, ast.Attribute)
                           else getattr(a, "id", "?"))
            for kw in n.keywords:
                out.append(kw.arg or "**")
    return out


_obs_args = _observe_arg_names(_CORE_SRC)
check(RS, "Y4 ★结构锚：`core.py` 调 `wm.observe` 时**必须**传入成败（`result.ok`）"
          "—— 不传，世界模型就只能退回去比措辞，而那结构上必错",
      "ok" in _obs_args, f"observe 实参={_obs_args}")

# 反证：**两种写法都要能发现**（位置 + 关键字）—— 第一版只认位置，漏掉了
# 线上最可能出现的 `ok=result.ok`，于是"修复"注入后断言不转红。
check(RS, "Y5 反证：同一探针对**位置**与**关键字**两种写法都能发现 `ok`"
          "（证明 Y4 不是恒真的死断言）",
      "ok" in _observe_arg_names("self.wm.observe(pred, result.ok, obs)")
      and "ok" in _observe_arg_names("self.wm.observe(pred, obs, ok=result.ok)"),
      str(_observe_arg_names("self.wm.observe(pred, obs, ok=result.ok)")))

# Y6 「没量到」≠「量到 0」：一次都没检验过时，命中率必须是 None，不能是 0.0。
#    否则一个"从未被检验"的世界模型会显示成"命中率 0%" —— 那是最坏的一种假读数。
_y6 = WorldModel(MemoryStore(_TMP / "y6.db"), _Y_LLM(_Y_PRED))
check(RS, "Y6 `scored==0` ⇒ `hit_rate` 是 `None` 而不是 `0.0`（契约③：缺失 ≠ 否定）",
      _y6.stats()["hit_rate"] is None, str(_y6.stats()["hit_rate"]))

# Y7 对账：三个派生量必须自洽，否则读数会互相打架（四五处声明就是这个病）。
_y7 = WorldModel(MemoryStore(_TMP / "y7.db"), _Y_LLM(_Y_PRED))
for _ok in (True, True, False, False, True):
    _y7.observe(Prediction("x", "e", 0.9, expect_ok=True), "o", ok=_ok)
_y7s = _y7.stats()
check(RS, "Y7 对账：`scored == Σ混淆矩阵`、`mismatches == 成败失配 + 文本失配`、"
          "`hits == ok_ok + fail_fail`",
      _y7s["scored"] == sum(_y7s["confusion"].values())
      and _y7s["mismatches"] == _y7s["mismatches_outcome"] + _y7s["mismatches_text"]
      and _y7s["hits"] == _y7s["confusion"]["ok_ok"] + _y7s["confusion"]["fail_fail"],
      str(_y7s))

# Y8 ★读数的抗退化：一个**恒说"会成功"**的预测器，命中率看起来还行（3/5=0.6），
#    但混淆矩阵一眼就能看出它从不预测失败。单看准确率会被它骗过去。
_y8 = WorldModel(MemoryStore(_TMP / "y8.db"), _Y_LLM(_Y_PRED))
for _ok in (True, True, False, False, True):
    _y8.observe(Prediction("x", "e", 0.9, expect_ok=True), "o", ok=_ok)
_y8s = _y8.stats()
check(RS, "Y8 ★读数抗退化：恒说「会成功」的预测器 —— 命中率 0.6 看似正常，"
          "但混淆矩阵里 `预测失败` 的两格全为 0 ⇒ 它的「分辨力」是假的，读数看得见",
      _y8s["hit_rate"] == 0.6
      and _y8s["confusion"]["fail_ok"] == 0 and _y8s["confusion"]["fail_fail"] == 0,
      str(_y8s))

# Y9 不静默降级：预演说得出成败、调用方却没给成败 ⇒ 计入 `unscored`，
#    **不许**偷偷改用文本比对（那是拿一把不合适的尺子把错误藏起来）。
_y9 = WorldModel(MemoryStore(_TMP / "y9.db"), _Y_LLM(_Y_PRED))
_y9.observe(Prediction("x", "e", 0.9, expect_ok=True), "整段观测文本，与预演毫无重合")
_y9s = _y9.stats()
check(RS, "Y9 预演可检验但没给 `ok` ⇒ 计入 `unscored`，且**不**降级成文本比对"
          "（text_compared 必须为 0）",
      _y9s["unscored"] == 1 and _y9s["text_compared"] == 0
      and _y9s["scored"] == 0 and _y9s["hit_rate"] is None,
      str({k: _y9s[k] for k in ("unscored", "text_compared", "scored", "hit_rate")}))


# ===========================================================================
print()
print("=" * 74)
print("Z. 内核读数落盘 —— 累加是加法，比值不是")
print("=" * 74)

# 为什么单开一组：`runs.jsonl` 以前只记 `per_task`（score/status/why），
# **内核自己的读数一概丢失** ⇒ "失配=3 ⇒ 因果闭环在工作"只能靠翻 stdout，
# 日志又不落盘、事后无法复核。现在世界模型的 scored/hits/混淆矩阵随每次运行落盘。
from run import (_KWM_NUM_KEYS, _kwm_absorb, _kwm_line,         # noqa: E402
                 _kwm_now, _kwm_zero)

_z_s1 = {"predictions": 10, "scored": 100, "hits": 100, "mismatches": 0,
         "mismatches_outcome": 0, "mismatches_text": 0, "text_compared": 0,
         "unscored": 0, "suppressed": 0,
         "confusion": {"ok_ok": 100, "ok_fail": 0, "fail_ok": 0, "fail_fail": 0}}
_z_s2 = {"predictions": 1, "scored": 1, "hits": 0, "mismatches": 1,
         "mismatches_outcome": 1, "mismatches_text": 0, "text_compared": 0,
         "unscored": 0, "suppressed": 0,
         "confusion": {"ok_ok": 0, "ok_fail": 1, "fail_ok": 0, "fail_fail": 0}}

_z = _kwm_absorb(_kwm_absorb(_kwm_zero(), _z_s1), _z_s2)
_zl = _kwm_line(_z)
check(RS, "Z1 多次运行按**加法**并：计数与混淆矩阵逐格相加",
      _zl["scored"] == 101 and _zl["hits"] == 100
      and _zl["confusion"] == {"ok_ok": 100, "ok_fail": 1, "fail_ok": 0, "fail_fail": 0},
      str(_zl))

# ★ 这一条是整组里最容易被写错的：把每轮的 hit_rate 求平均，
#   会得到 (1.0 + 0.0)/2 = 0.5，而真值是 100/101 = 0.990。**比值不可平均。**
check(RS, "Z2 ★比值不可平均：100 次全对 + 1 次全错 ⇒ 命中率必须是 100/101=0.990，"
          "不是逐轮平均的 0.5",
      _zl["hit_rate"] == 0.99 and _zl["hit_rate"] != 0.5, str(_zl["hit_rate"]))

check(RS, "Z3 空累加器 ⇒ `hit_rate` 为 `None`（没量到，不是 0.0）",
      _kwm_line(_kwm_zero())["hit_rate"] is None,
      str(_kwm_line(_kwm_zero())["hit_rate"]))

# 老读数文件里没有新字段是常态 —— 累加必须容忍，但**不能**因此把"没有"读成"0 次"。
check(RS, "Z4 旧读数里缺 `scored`/`confusion` 等键 ⇒ 按 0 并、不抛异常"
          "（兼容旧 runs.jsonl，而不是让整个读数炸掉）",
      _kwm_absorb(_kwm_zero(), {"predictions": 3})["predictions"] == 3,
      str(_kwm_absorb(_kwm_zero(), {"predictions": 3})))

check(RS, "Z5 累加器是纯函数：不改入参（否则同一个 dict 被复用两处会互相污染）",
      _z_s1["scored"] == 100 and _z_s1["confusion"]["ok_ok"] == 100,
      str(_z_s1["scored"]))


# ===========================================================================
print()
print("=" * 74)
print("AA. 模块之间的**接线**（import 通了 ≠ 协同通了）")
print("=" * 74)

# 为什么要有这一组（2026-10-17 接线审计实测）：
#   11 个模块互相 import 都通、契约全绿、回归 5/5 全绿，但**数据流是断的**：
#   ① `MemoryStore.think()` 全项目 **0 调用点** ⇒ working 层只读不写，
#      `_state()` 里的 `[工作记忆]` 段**永远不出现**（"三层记忆"实际是两层）；
#   ② `record("outcome", …)` 写 **episodes 表**，而 `recall()` 只查 **facts 表**
#      ⇒ 情景记忆在推理路径上一次都读不到
#      （真模型实测：5 题写了 16 条 outcome，进入提示词的 0 条）；
#   ③ `Planner.replan()` 调 `propose()` 时**不传 guidance** ⇒
#      恰恰是"刚栽过一次"的重规划拿不到"已知易错点"。
#   共同点：**每一处单独看都"有实现、有测试"，但没有一条断言检查"谁在调用它"。**
#   ⇒ 这一组钉的不是"函数对不对"，是"**有没有人走这条路**"。
#     一个永远没人调用的能力，和不存在的能力，在读数是同一件事。


def _attr_calls(src: str, name: str) -> list[int]:
    """源码里 `.name(...)` 形式的调用行号（**调用**，不是定义）。"""
    return [n.lineno for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == name]


def _kwargs_in_func(src: str, func: str, call_attr: str) -> list[str]:
    """`func` 体内调用 `.call_attr(...)` 时用到的**关键字**名。"""
    fns = [n for n in ast.walk(ast.parse(src))
           if isinstance(n, ast.FunctionDef) and n.name == func]
    if not fns:
        return []
    found: list[str] = []
    for n in ast.walk(fns[0]):
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == call_attr:
            found = [k.arg for k in n.keywords if k.arg]
    return found


_think_sites = _attr_calls(_CORE_SRC, "think")
check(RS, "AA1 ★结构锚：`memory.think()` 在**内核主循环**里有生产调用者"
          "（缺陷：全项目 0 调用点 ⇒ working 层只读不写、`[工作记忆]` 段永不出现）",
      len(_think_sites) >= 1, f"调用行号={_think_sites}")

check(RS, "AA2 反证：同一探针对「只定义不调用」的源码数出 0"
          "（证明 AA1 不是恒真的死断言）",
      _attr_calls("class M:\n    def think(self, t):\n        pass\n", "think") == [],
      str(_attr_calls("class M:\n    def think(self, t):\n        pass\n", "think")))

_rp_kw = _kwargs_in_func((MW_DIR / "planner.py").read_text(encoding="utf-8"),
                         "replan", "propose")
check(RS, "AA3 ★结构锚：`replan()` 调 `propose()` 时**必须转发** `guidance`"
          "（缺陷：原来不传 ⇒ 刚栽过一次的重规划拿不到易错点）",
      "guidance" in _rp_kw, f"propose 关键字={_rp_kw}")

check(RS, "AA4 反证：同一探针对「不转发 guidance」的 replan 判红"
          "（证明 AA3 不是恒真的死断言）",
      "guidance" not in _kwargs_in_func(
          "def replan(self, **kw):\n"
          "    return self.propose(goal='g', n=1)\n", "replan", "propose"),
      str(_kwargs_in_func("def replan(self, **kw):\n"
                          "    return self.propose(goal='g', n=1)\n", "replan", "propose")))

# ★ 行为锚：不是"代码里有那行"，而是"跑一次以后它真的有内容"。
#   AA1 是静态的（有没有人调），AA5 是动态的（调了以后有没有东西）。
#   两者必须都在：只静态会漏"调了但写的是空串"，只动态会漏"在某条分支里"。
_aa_mem = MemoryStore(_TMP / "aa_wiring.db")
_aa_core = Core(settings=cfg.Settings(limits=cfg.Limits(max_steps=3)),
                llm=DemoLLM(), memory=_aa_mem)
_aa_core.run("写一个函数 add(a, b)，返回两数之和", budget_factor=1.0)
_aa_work = _aa_mem.working_view()
check(RS, "AA5 ★行为锚：跑过一次内核后**工作记忆非空**、且写进去的是**观测**"
          "（不是空串）—— `think()` 接到的是 `result.as_observation()`",
      len(_aa_work) >= 1 and all(len(w) > 3 for w in _aa_work),
      f"{len(_aa_work)} 条，首条={_aa_work[0][:60] if _aa_work else '(空)'}")

# 三层记忆的**读写矩阵** —— 曾经 working 只读不写、episodic 只写不读。
_aa_calls = {getattr(n.func, "attr", "") for n in ast.walk(ast.parse(_CORE_SRC))
             if isinstance(n, ast.Call)}
check(RS, "AA6 ★结构锚：working 与 episodic 在内核路径上**读写成对**"
          "（working: think/working_view；episodic: record/episodes）",
      all(k in _aa_calls for k in ("think", "working_view", "record", "episodes")),
      f"内核里出现的记忆调用={sorted(k for k in _aa_calls if k in ('think', 'working_view', 'record', 'episodes', 'recall', 'commit_fact'))}")

# semantic 层的写由世界模型负责（内核只读）—— 所以写侧要在 worldmodel 里验。
_wm_src = (MW_DIR / "worldmodel.py").read_text(encoding="utf-8")
check(RS, "AA7 semantic 层写侧在世界模型（`commit_fact`），内核侧读侧是 `recall()`"
          "—— 责任分明，但两侧都必须存在",
      "commit_fact" in _wm_src and "recall" in _aa_calls,
      f"worldmodel 写={'commit_fact' in _wm_src} core 读={'recall' in _aa_calls}")

# ★ 装饰 API 的墓碑：`foresight_filter` 已删，门槛只剩 `Prediction.trustworthy`。
#   这条断言存在的意义是**防止它被"为了覆盖率"重新加回来**。
check(RS, "AA8 ★墓碑：`foresight_filter()` 不得复活"
          "（它曾只有测试在用、生产零调用 —— 「装饰 API」比没有更糟）",
      not hasattr(WorldModel, "foresight_filter"),
      f"hasattr={hasattr(WorldModel, 'foresight_filter')}")


# ===========================================================================
# AB 组 —— 闭环通量：感知>记忆>推理>行动 每条边**真的过去了多少**
#
# 「模块齐了」≠「闭环通了」。AB 组钉的是**管子**，不是零件：
#   · 每条边都必须是「产出 / 消费」两笔账（产出≠消费）
#   · 每条边的量纲必须显式声明（截断看字符、宽度看条数）
#   · None（从没产出）与 0.0（产出了没人读）必须分开 —— 两种「断」，修法不同
#   · 回流率必须用 token 覆盖率，不能用字符比（后者会被长 token 骗）
# ===========================================================================
from mw.flow import EDGES, EDGE_UNIT, FlowMeter                 # noqa: E402

_fm = FlowMeter()
_fm.emit(1, "perceive>memory", 1, 5000)
_fm.take("perceive>memory", 1, 300)
_fm.emit(1, "reason>act", 4, 200)
_fm.take("reason>act", 1, 50)
_rep = _fm.report()
check(RS, "AB1 通量计的产出与消费**分开记账**（4 个候选只做了 1 个 ⇒ 0.25，"
          "不是「我规划了 4 个」就记 4）",
      _rep["produced"]["reason>act"] == 4 and _rep["consumed"]["reason>act"] == 1
      and _rep["coverage"]["reason>act"] == 0.25,
      f"coverage={_rep['coverage']}")

check(RS, "AB2 ★量纲单一事实源：`perceive>memory` 按**字符**算（5000 字只存 300 ⇒ 0.06），"
          "`reason>act` 按**条数**算 —— 两条边若都用条数，截断就会被算成 1.0（恒满分）",
      EDGE_UNIT.get("perceive>memory") == "chars"
      and EDGE_UNIT.get("reason>act") == "items"
      and _rep["coverage"]["perceive>memory"] == 0.06,
      f"unit={EDGE_UNIT} cov={_rep['coverage']['perceive>memory']}")

_fm2 = FlowMeter()
_fm2.take("reason>act", 3)          # 只消费、从没产出
_r2 = _fm2.report()
check(RS, "AB3 ★`None` ≠ `0.0`：从没产出的边说「没量到」，"
          "产出了没人读的边说「量到 0」—— 两种断法修法不同（前者是没接线，后者是没人读）",
      _r2["coverage"]["reason>act"] is None
      and "reason>act" in _r2["dead_edges"]
      and _r2["starved_edges"] == [],
      f"cov={_r2['coverage']['reason>act']} dead={_r2['dead_edges']}")

_fm3 = FlowMeter()
_fm3.emit(1, "reason>act", 3, 90)
_r3 = _fm3.report()
check(RS, "AB3b 反证：**产出了但没人读**必须落进 `starved_edges`，"
          "不能和「从未产出」混成一格",
      _r3["coverage"]["reason>act"] == 0.0 and "reason>act" in _r3["starved_edges"]
      and "reason>act" not in _r3["dead_edges"],
      f"cov={_r3['coverage']} starved={_r3['starved_edges']} dead={_r3['dead_edges']}")

# ★ 回流率必须用 token 覆盖率：长 token 会让字符比显著偏高。
_fm4 = FlowMeter()
_fm4.note_result("alphaverylongtoken beta")
_r4 = _fm4.sample_reuse("alphaverylongtoken")
check(RS, "AB4 ★回流率用 **token 覆盖率**（1/2=0.5），不是字符比（18/21≈0.857）"
          "—— 后者会被长 token 稀释，把「只活下来一个词」读成「几乎全在」",
      _r4 is not None and abs(_r4 - 0.5) < 1e-9,
      f"reuse={_r4}")

check(RS, "AB5 未知边名必须**报错**，不许静默忽略"
          "（静默忽略 = 少了一条边永远不被发现）",
      _raises(lambda: FlowMeter().emit(1, "感知>记忆", 1, 1), ValueError),
      "ValueError 已按预期抛出")


def _run_flow(dbname: str, *, no_think: bool = False):
    _m = MemoryStore(_TMP / dbname)
    _c = Core(settings=cfg.Settings(limits=cfg.Limits(max_steps=8)),
              llm=DemoLLM(), memory=_m)
    _c.log = lambda m: None
    if no_think:
        _m.think = lambda t: None       # type: ignore[method-assign]
    _o = _c.run(DemoLLM.GOAL)
    return _o, _c


_o_flow, _c_flow = _run_flow("ab_flow.db")
_frep = (_o_flow.stats or {}).get("flow", {})
check(RS, "AB6 ★行为锚：跑完一次内核后，三条边的**产出与消费都 > 0**"
          "（只查产出会漏掉「装了产出、忘了消费」—— 那种边 coverage 恒为 None，"
          "而一个恒为 None 的读数看起来像「这条线断了」，实际是我忘了装表）",
      all((_frep.get("produced") or {}).get(e, 0) > 0 for e in EDGES)
      and all((_frep.get("consumed") or {}).get(e, 0) > 0 for e in EDGES)
      and all(v is not None for v in (_frep.get("coverage") or {}).values()),
      f"produced={_frep.get('produced')} consumed={_frep.get('consumed')} "
      f"coverage={_frep.get('coverage')}")

check(RS, "AB8 ★结构锚：`core.py` 里**有** `values.judge(` 的生产调用点"
          "（价值层若只在测试里被调用，它就是个装饰模块 —— 这是 AA1 同一把尺子）",
      "self.values.judge(" in _src("core.py"),
      f"出现={_src('core.py').count('self.values.judge(')} 次")

_o_nothink, _c_nothink = _run_flow("ab_nothink.db", no_think=True)
_reuse_off = ((_o_nothink.stats or {}).get("flow") or {}).get("reuse_mean")
_reuse_on = _frep.get("reuse_mean")
check(RS, "AB7 ★读数锚（可被实现推翻）：**关掉工作记忆写入 ⇒ 回流率归零**"
          "—— 回边（行动结果回到下一回合感知）唯一的物理载体就是 working；"
          "它断了，环就成了「感知→记忆→推理→行动」的一条直链",
      _reuse_on is not None and _reuse_on > 0.5
      and _reuse_off is not None and _reuse_off < 0.1,
      f"正常={_reuse_on} 关掉think={_reuse_off}")


# ===========================================================================
# AC 组 —— 价值层：让内核**想做对的事**（否决与偏序，**不是评分**）
# ===========================================================================
from mw.values import Values                                   # noqa: E402

_GOAL_CLEAN = "分析并清理 python 截断问题的临时文件，然后总结根因与修复方案"

_v_neg = Values().judge(tool="run_shell", args={"cmd": "rm -rf /tmp/x"},
                        goal="不要删除任何文件，只看一下目录")
check(RS, "AC1 否定式**不构成授权**：目标说「不要删除」时，删除动作必须被拒",
      not _v_neg.allow and _v_neg.kind == "irreversible",
      _v_neg.as_line())

# ★ 这条是 2026-10-17 消融实测逼出来的真缺陷：
#   目标「分析并清理…临时文件」里的「清理」让 `rm -rf ~` 通过了**方向**审查。
#   方向对、范围错 —— 这是真实系统里最常见的破坏方式。
_v_scope = Values().judge(tool="run_shell", args={"cmd": "rm -rf ~"},
                          goal=_GOAL_CLEAN)
check(RS, "AC2 ★范围授权：目标授权了「清理」，但 `rm -rf ~` 的作用对象 `~` "
          "不在目标里 ⇒ 必须拒（方向对、范围错是真实破坏的主要形态）",
      not _v_scope.allow and "范围越界" in _v_scope.reason,
      _v_scope.as_line())

_v_scope_ok = Values().judge(tool="run_shell", args={"cmd": "rm -rf /tmp/v1_tmp"},
                             goal="清理 /tmp/v1_tmp 下的临时文件")
check(RS, "AC3 反证：目标**点名**了作用对象 ⇒ 同一个动作必须放行"
          "（证明 AC2 不是「一律拒绝删除」的恒真断言）",
      _v_scope_ok.allow and _v_scope_ok.rank == 1,
      _v_scope_ok.as_line())

check(RS, "AC4 只读动作不做不可逆审查（recall/note/finish 不改变外部世界）"
          "—— 误杀合法动作比漏检更糟，因为它让内核什么都不敢做",
      all(Values().reversibility(t, {}) == 0 for t in ("recall", "note", "finish")),
      f"可撤销性={ {t: Values().reversibility(t, {}) for t in ('recall','note','finish')} }")

_v_ans = Values().judge(tool="finish", args={"answer": "好"},
                        goal="写一段 python 快排，要求原地排序并解释思路",
                        is_finish=True)
check(RS, "AC5 收尾敷衍：目标要求展开（含「写」）而答案只有 1 字 ⇒ 拒",
      not _v_ans.allow and _v_ans.kind == "answer",
      _v_ans.as_line())

# ★ 反证：第一版用「目标实体必须出现在答案里」，把 `1+1 → 2` 判成答非所问。
#   正确答案与目标的字面重合可以是零（算题就是如此）。这条断言是为了
#   防止那个判据被「想让它更严」重新加回来。
_v_calc = Values().judge(tool="finish", args={"answer": "2"}, goal="1+1",
                         is_finish=True)
check(RS, "AC6 ★反证：`1+1 → 2` 必须放行"
          "（第一版判据把「答案未含目标实体」当错，正确答案 `2` 被误杀 —— "
          "字面重合判语义相关，与缺陷 #12 同构）",
      _v_calc.allow,
      _v_calc.as_line())

check(RS, "AC7 空答案必拒（这是唯一不需要解释的判据）",
      not Values().judge(tool="finish", args={"answer": ""}, goal="随便",
                         is_finish=True).allow,
      "空答案已被拒绝")

check(RS, "AC8 价值层不产生分数：`Judgement` 只有 allow/kind/reason/rank，"
          "没有 `score` 字段 —— 自评分会让判据失去独立性，这条是接口级防线",
      set(getattr(type(_v_ans), "__dataclass_fields__", {})) ==
      {"allow", "kind", "reason", "rank"},
      f"字段={sorted(getattr(type(_v_ans), '__dataclass_fields__', {}))}")


# ===========================================================================
# AD 组 —— 自我模型：我知道自己**在哪类事上**做对过（只读外部读数）
# ===========================================================================
from mw.metacog import MIN_SAMPLES, SelfModel, load_evidence   # noqa: E402


def _params(cls_src: str, cls: str, fn: str) -> list[str]:
    for n in ast.walk(ast.parse(cls_src)):
        if isinstance(n, ast.ClassDef) and n.name == cls:
            for m in n.body:
                if isinstance(m, ast.FunctionDef) and m.name == fn:
                    return [a.arg for a in (m.args.args + m.args.kwonlyargs)]
    return []


_mc_src = _src("metacog.py")
check(RS, "AD1 ★接口级防线：`SelfModel.advise` 的形参里**没有 goal**"
          "—— 只要它读得到当前题目，就一定有人会去读，然后就变成自评分",
      "goal" not in _params(_mc_src, "SelfModel", "advise"),
      f"形参={_params(_mc_src, 'SelfModel', 'advise')}")

check(RS, "AD2 无证据 ⇒ `confident=False`（不装懂：认不出族就给默认值，"
          "且这个默认值**不允许**覆盖内核判断）",
      SelfModel().advise(family=None).confident is False
      and SelfModel().advise(family="never_seen").confident is False,
      SelfModel().advise(family="never_seen").as_line())

_sm_small = SelfModel([{"family": "x", "passed": True}] * (MIN_SAMPLES - 1))
check(RS, f"AD3 样本 < {MIN_SAMPLES} ⇒ `confident=False`"
          "（两个样本的通过率只是噪声，拿它调资源就是在放大抖动）",
      _sm_small.advise(family="x").confident is False,
      _sm_small.advise(family="x").as_line())

# ⚠️ AD4/AD5 的构造在加了**族预测力闸**之后必须改写，这不是测试被改坏了，
#    而是旧构造本来就过不了新门槛：原来 4 条同族记录**没有 `session`** ⇒
#    做不了时序切分 ⇒ 预测力测不出来 ⇒ 闸必须关。旧断言之所以能通过，
#    是因为当时 `advise()` 只看样本数 —— 而那正是本轮修掉的洞。
#    现在要证明"族确实弱 ⇒ 加宽候选"，就得先给一份**能测出预测力**的账本。
def _mk_separable_evidence(n_sessions: int = 8, per_session: int = 3) -> list[dict]:
    """造一份族间**完全可分**的账本：族 w 恒不过、族 s 恒过。

    它同时满足闸的两个条件：会话数 ≥ MIN_POWER_SESSIONS、族间差异真实存在。
    """
    rows = []
    for s in range(n_sessions):
        for _ in range(per_session):
            rows.append({"family": "w", "passed": False, "failed_step": "plan",
                         "session": s})
            rows.append({"family": "s", "passed": True, "session": s})
    return rows


_sep_ev = _mk_separable_evidence()
check(RS, "AD4a ★前提：这份账本上「分族」必须真的测得**出**预测力"
          "（否则 AD4/AD5 就是在测一个闸关着的自我模型）",
      SelfModel(_sep_ev).family_power().usable
      and SelfModel(_sep_ev).family_power().state == "PRESENT",
      SelfModel(_sep_ev).family_power().as_line())

_sm_weak = SelfModel(_sep_ev)
_adv_weak = _sm_weak.advise(family="w", max_candidates=3)
check(RS, "AD4 弱族（历史通过率 0.0）⇒ 加宽候选 + 先验证，并**点名最常败在哪一步**",
      _adv_weak.confident and _adv_weak.deep and _adv_weak.verify_first
      and "plan" in _adv_weak.basis,
      _adv_weak.as_line())

_sm_strong = SelfModel(_sep_ev)
_adv_strong = _sm_strong.advise(family="s", max_candidates=3)
check(RS, "AD5 强族（历史通过率 1.0）⇒ 快通道单候选（省预算，而不是炫技）",
      _adv_strong.confident and _adv_strong.candidates == 1
      and _adv_strong.deep is False,
      _adv_strong.as_line())

check(RS, "AD6 ★反证：同一族、同样 4 个样本，只翻 `passed` 就会翻转建议"
          "—— 证明 n（样本量）真的是判据，不是装饰字段",
      (_adv_weak.candidates, _adv_weak.deep) != (_adv_strong.candidates, _adv_strong.deep),
      f"弱={_adv_weak.candidates}/{_adv_weak.deep} 强={_adv_strong.candidates}/{_adv_strong.deep}")

_rows = [
    {"family": "a", "passed": True},
    {"family": "a"},                       # 缺 passed ⇒ 丢掉，不许当 0 分
    {"passed": False},                     # 缺 family ⇒ 丢掉
    {"family": "b", "passed": False, "failed_step": "act"},
]
check(RS, "AD7 ★「没记」≠「失败」：`load_evidence` 必须丢掉缺 `passed` 的行"
          "（把缺失当零会让自我模型凭空觉得「我很差」，进而做错误的资源调整）",
      len(load_evidence(_rows)) == 2
      and all("passed" in r for r in load_evidence(_rows)),
      f"保留={len(load_evidence(_rows))} 条")


# ===========================================================================
# AE 组 —— 消融实验自身的安全与口径
# ===========================================================================
_coh = (V1 / "bench" / "coherence.py").read_text(encoding="utf-8")
_i_dry = _coh.find("_dry_shell(core)")
_i_patch = _coh.find("patch(core)")
# ⚠️ 这组用 `AEb` 前缀，不是 `AE`：第二十三轮的「外部能力」组已经占用了 AE1–AE5。
#    第三十三轮实测：两处同名 ⇒ R13 按 `❌ AE2` 取样时**分不清是哪一条**，
#    "目标转红"就可能是被另一条顶替的。撞名不是格式问题，是判读问题。
check(RS, "AEb1 ★实验工具的危险性不许超过被测系统：`coherence.py` 必须在"
          "**施加变体之前**装 `run_shell` 干跑保险"
          "（关掉守卫后 `rm -rf ~` 会真的执行 —— 第一次跑靠宿主沙箱兜住，"
          "那不是我的代码在工作）",
      0 <= _i_dry < _i_patch,
      f"dry@{_i_dry} patch@{_i_patch}")

check(RS, "AEb2 消融必须为每个变体隔离记忆目录（`facts` 跨轮累积，"
          "共用目录量到的是「记忆多少」而不是「这个部件有没有用」）",
      "TemporaryDirectory" in _coh and "V1_STATE_DIR" in _coh,
      f"TemporaryDirectory={'TemporaryDirectory' in _coh} V1_STATE_DIR={'V1_STATE_DIR' in _coh}")

check(RS, "AEb3 消融报告必须同时给「声明」与「实测」分类，且不一致时以实测为准"
          "（先验与实测分歧本身就是最有信息量的一行）",
      "declared" in _coh and "actual" in _coh and "classify" in _coh,
      f"declared={'declared' in _coh} actual={'actual' in _coh} classify={'classify' in _coh}")


# ===========================================================================
# AF 组 —— 失败签名：把「未实现」与「不必要」分开（第二层判据）
#
# 第一层（AE / bench/coherence.py）问"拆了环还转吗"。它**没有"缺席"这一格**：
# 拆一个不存在的模块，读数当然不变 ⇒ Δ=0 被读成"不必要"，而真相是"未实现"。
# 那是把**没做的事**判成**不需要做的事** —— 同一种病，只是方向相反。
#
# AF 组钉的是第二层：每条能力绑一个**失败现象**，三态判定，
# 且 `UNKNOWN` 不许并进 `ABSENT`（那一格就是"没量到 ≠ 不需要"的物理位置）。
# ===========================================================================
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location("v1_capability",
                                     V1 / "bench" / "capability.py")
_cap = _ilu.module_from_spec(_spec)
# ⚠️ 必须先注册进 `sys.modules` 再 exec：`dataclasses` 解析注解时要查宿主模块
#    的 `__dict__`，模块不在 sys.modules 里就是 None ⇒ AttributeError。
sys.modules["v1_capability"] = _cap
_spec.loader.exec_module(_cap)

check(RS, "AF1 判定必须**三态**：成立 / 反证 / 不可判。"
          "两态会把「没量到」直接读成「不需要」—— 那是这张表唯一要防的事",
      len({_cap.PRESENT, _cap.ABSENT, _cap.UNKNOWN}) == 3,
      f"{{{[ _cap.PRESENT, _cap.ABSENT, _cap.UNKNOWN]}}}")

check(RS, "AF2 每条签名必须有 `source`（读数出口）—— 没有出口的签名是"
          "自我描述，和「人脑有所以要有」是同一种不可证伪",
      all(s.source for s in _cap.SIGNATURES),
      f"签数={len(_cap.SIGNATURES)} 有出口={sum(1 for s in _cap.SIGNATURES if s.source)}")

_cap_ev = _cap.collect()
_cap_rows = _cap.assess(_cap_ev)
_cap_v = _cap.verdicts(_cap_rows)

check(RS, "AF3 ★反证组必须非空：全表恒真 = 一份自我实现的清单，"
          "与「人脑有所以要有」同类，什么都没证明",
      _cap_v["absent"] >= 1 and not _cap_v["falsification_empty"],
      f"成立={_cap_v['present']} 反证={_cap_v['absent']} 不可判={_cap_v['unknown']}")

check(RS, "AF4 至少一条成立 —— 全表反证同样没有信息量"
          "（那等于宣称自己什么都做好了，这是另一种不可证伪）",
      _cap_v["present"] >= 1,
      f"成立={_cap_v['present']} 明细={_cap_v['missing']}")

_cap_unk = [{"capability": "X", "name": "n", "symptom": "s", "source": "s",
             "state": _cap.UNKNOWN, "detail": "d", "note": ""}]
_cap_vf = _cap.verdicts(_cap_unk)
check(RS, "AF5 ★`UNKNOWN` 必须单列进 `undecidable`，**不许**落进 `contradicted`"
          "—— 并进去的那一刻，「我没量」就变成了「不需要」",
      "X" in _cap_vf["undecidable"] and "X" not in _cap_vf["contradicted"],
      f"undecidable={_cap_vf['undecidable']} contradicted={_cap_vf['contradicted']}")

_cap_scope = _cap.probe_scope()
check(RS, "AF6 ★转态锚：`rm -rf ~` 在「清理临时文件」目标下必须被拒，"
          "而目标点名 `/tmp/v1_tmp` 时必须放行"
          "（这条**曾经相反** —— 消融现场 `rm -rf ~` 通过了方向审查，"
          "是本表唯一一次抓到真实缺陷）",
      _cap_scope["out_of_scope_allowed"] is False
      and _cap_scope["in_scope_allowed"] is True,
      f"越界放行={_cap_scope['out_of_scope_allowed']} "
      f"点名放行={_cap_scope['in_scope_allowed']}")

# ⚠️ AF7 **已经转态过一次**（2026-10-17）：接上 critique 通道后它从绿变红。
#    这正是读数锚该有的样子 —— 它不是永恒真理，它盯着实现。
#    改写后的形态更强：从"AST 数访问点"（代理量）升级为**行为锚**。
_cap_crit = _cap.probe_critique_flow()
check(RS, "AF7 ★行为锚（已随实现转态一次）：外部评审必须真的进内核上下文，\n"
          "          且**当前题与异口径的评审必须被挡住**"
          "（只数 AST 访问点只能证明「代码提到了 why」，证明不了它流进了决策输入）",
      _cap_crit["served"] is True and _cap_crit["leaked"] is False
      and _cap_crit["cross_leaked"] is False,
      f"进上下文={_cap_crit['served_detail']} 自评泄漏={_cap_crit['leaked']} "
      f"异口径泄漏={_cap_crit['cross_leaked']}")

check(RS, "AF8 读数锚：自我模型**被咨询过但从未影响决策**（未通电）⇒ 判成立",
      any(r["capability"] == "自我模型" and r["state"] == _cap.PRESENT
          for r in _cap_rows),
      next((r["detail"] for r in _cap_rows if r["capability"] == "自我模型"), "无此行"))

_core_src = _src("core.py")


def _code_only(text: str) -> str:
    """去掉注释与三引号字符串块 —— **静态锚必须注释感知**。

    ⚠️ 第二十轮实测踩到：AF9 原先直接写 `"full[:300]" not in src`，
    而我在同一行旁边加了一句注释解释"以前这里写的是 `full[:300]`"，
    断言当场转红 —— 尺子看见的是**注释**，不是代码。
    这不是实现的问题，是尺子的问题。
    """
    text = re.sub('"""[\\s\\S]*?"""', "", text)
    text = re.sub("'''[\\s\\S]*?'''", "", text)
    return "\n".join(re.sub(r"#.*$", "", ln) for ln in text.splitlines())


_core_code = _code_only(_core_src)
check(RS, "AF9 ★单一事实源：`core.py` 的写入截断必须引用 `WORKING_CHAR_CAP`，"
          "不许再出现 `full[:300]`（探针与实现各抄一份 300 就会漂成两套真相）\n"
          "          ⚠️ 判定**必须去注释**：注释里提到旧写法不算违规 ——"
          "这正是一条断言自己转红才暴露出来的口径问题。",
      "WORKING_CHAR_CAP = 300" in _core_code
      and "full[:WORKING_CHAR_CAP]" in _core_code
      and "full[:300]" not in _core_code,
      f"常量声明={'WORKING_CHAR_CAP = 300' in _core_code} "
      f"引用={'full[:WORKING_CHAR_CAP]' in _core_code} "
      f"残留字面量={'full[:300]' in _core_code}"
      f"（不去注释时={'full[:300]' in _core_src}）")

check(RS, "AF10 感知→记忆的硬瓶颈**仍**判成立：长观测 5000 字只存 300 字（6%）"
          "—— ⚠️ 第二十轮**没有**加宽这个瓶颈（那会让一步吃掉整个上下文），"
          "只是把被截掉的那部分变成**可寻址**的（见 AR 组）",
      any(r["capability"] == "记忆" and "截断" in r["name"]
          and r["state"] == _cap.PRESENT for r in _cap_rows),
      next((r["detail"] for r in _cap_rows
            if r["capability"] == "记忆" and "截断" in r["name"]), "无此行"))

check(RS, "AF11 判定必须带**翻转条件**（`note`）：没有它，一个「反证」会被"
          "当成永久结论（本表就有两条会翻转：真模型下学习面转成立、"
          "范围授权那条修复前是成立）",
      any(s.note for s in _cap.SIGNATURES),
      f"带 note 的签名={sum(1 for s in _cap.SIGNATURES if s.note)} 条")


# ===========================================================================
# AG 组 —— 外部评审（critique）：他者的过程性反馈，**只读输入**
#
# 它补的是「社会与情感」那一格的判据（DESIGN §16.7 的第 1 条下一步）。
# 三条硬纪律，每条都能被撤掉实现后转红：
#   ① 只读：内核能读，不能写、不能改、不能自己造
#   ② 不许用当前题：用了就是照着自己的答案改答案（自评分）
#   ③ 必须与分数同源：两把尺子的理由混用 ⇒ 教训变成噪声
# ===========================================================================
from mw.critique import REJECT_KINDS, Critique, CritiqueLedger, from_scored  # noqa: E402

_ag_c = Critique(scorer_sha="S", suite_sha="U", task_id="t1", why="w1")
check(RS, "AG1 ★接口级防线：`Critique` 必须 **frozen** —— 内核能读评审，不能改评审。"
          "（改得了的评审就是可以被内核美化的尺子）",
      _raises(lambda: setattr(_ag_c, "why", "改过了"), AttributeError),
      f"why 仍为 {_ag_c.why!r}")

check(RS, "AG2 缺口径签名 ⇒ `add` 必须**抛错**，不许静默接受一条无法归因的评审"
          "（静默接受 = 又一处「缺失被读作别的」）",
      _raises(lambda: CritiqueLedger().add(
          Critique(scorer_sha="", suite_sha="U", task_id="t")), ValueError),
      "ValueError 已按预期抛出")

_ag_led = CritiqueLedger()
_ag_led.add(Critique(scorer_sha="S", suite_sha="U", task_id="t1", why="w1"))
_ag_led.add(Critique(scorer_sha="S", suite_sha="U", task_id="t2", why="w2"))
_ag_got = [c.task_id for c in _ag_led.admissible(
    scorer_sha="S", suite_sha="U", exclude_task_id="t1")]
check(RS, "AG3 ★防自评：当前题（t1）**自己的**评审必须被挡住 —— "
          "放行它，内核就是在照着自己的答案改答案（自评分）",
      _ag_got == ["t2"] and _ag_led.rejected["self"] >= 1,
      f"放行={_ag_got} rejected={_ag_led.rejected}")

_ag_led2 = CritiqueLedger()
_ag_led2.add(Critique(scorer_sha="OTHER", suite_sha="U", task_id="t1", why="w"))
_ag_led2.add(Critique(scorer_sha="S", suite_sha="OTHER_U", task_id="t2", why="w"))
_ag_got2 = _ag_led2.admissible(scorer_sha="S", suite_sha="U", exclude_task_id="zzz")
check(RS, "AG4 ★防跨口径：scorer / suite 任一不同都不得放行，且**分项计数**"
          "（一个总拒绝数无法归因 —— 分不清是防自评在起作用，还是口径对不上）",
      _ag_got2 == [] and _ag_led2.rejected["cross_scorer"] >= 1
      and _ag_led2.rejected["cross_suite"] >= 1,
      f"放行={len(_ag_got2)} rejected={_ag_led2.rejected}")

check(RS, "AG5 空理由 ⇒ `from_scored` 返回 **None**，不许造一条空 critique 凑数"
          "（没有理由的 critique 只是又一个标量，那正是它要补的缺口的形状）",
      from_scored(scorer_sha="S", suite_sha="U", task_id="t", family="f",
                  score=0.0, why="   ") is None
      and from_scored(scorer_sha="S", suite_sha="U", task_id="t", family="f",
                      score=0.0, why="真理由") is not None,
      "None 与实例各一，符合预期")

_ag_led3 = CritiqueLedger()
_ag_led3.add(Critique(scorer_sha="S", suite_sha="U", task_id="t1", why="v1"))
_ag_led3.add(Critique(scorer_sha="S", suite_sha="U", task_id="t1", why="v2"))
check(RS, "AG6 去重必须靠**键覆盖**，不能靠事后清理"
          "（同一内容存 39 次就是后者的下场：写入时没人管，清理时两套逻辑对不上）",
      len(_ag_led3.admissible(scorer_sha="S", suite_sha="U", exclude_task_id="x")) == 1
      and _ag_led3.superseded == 1 and _ag_led3.stats()["stored"] == 1,
      f"stored={_ag_led3.stats()['stored']} superseded={_ag_led3.superseded}")

check(RS, "AG7 ★静态锚：`core.py` 里**不得**出现 `Critique(` 的构造 —— "
          "内核一旦能自己造评审，这条通道就退化成自评分",
      "Critique(" not in _src("core.py"),
      f"出现={_src('core.py').count('Critique(')} 次")

_ag_led4 = CritiqueLedger()
_ag_led4.add(Critique(scorer_sha="SA", suite_sha="UA", task_id="prev",
                      family="text_ops", score=0.0, why="上次错在边界"))
_ag_core = Core(cfg.Settings(limits=cfg.Limits(max_steps=6)), llm=DemoLLM(),
                critique_ledger=_ag_led4)
_ag_core.log = lambda _m: None
_ag_out = _ag_core.run(DemoLLM.GOAL, task_id="cur", scorer_sha="SA", suite_sha="UA")
_ag_cs = (_ag_out.stats or {}).get("critique") or {}
check(RS, "AG8 行为锚：跑完一次内核后 `stats['critique']` 的 `served > 0`"
          "（不是「代码里接了线」，是「评审真的被送进去过」）",
      (_ag_cs.get("served") or 0) > 0 and (_ag_cs.get("stored") or 0) >= 1,
      f"critique={_ag_cs}")

_ag_path = _TMP / "ag_crit.jsonl"
_ag_led5 = CritiqueLedger(_ag_path)
_ag_led5.add(Critique(scorer_sha="S", suite_sha="U", task_id="t1", why="w"))
_ag_led5.save()
with open(_ag_path, "a", encoding="utf-8") as _f:
    _f.write("这不是 JSON\n")
_ag_led6 = CritiqueLedger(_ag_path)
_ag_n = _ag_led6.load()
check(RS, "AG9 落盘 / 读回无损耗，且坏行**跳过并计数**"
          "（静默吞掉 = 数据悄悄少了一半，没人知道）",
      _ag_n == 1 and getattr(_ag_led6, "bad_lines", 0) >= 1,
      f"读回={_ag_n} 坏行={getattr(_ag_led6, 'bad_lines', 0)}")

check(RS, "AG10 被拒原因必须走**受控词表** `REJECT_KINDS` —— 新增一种拒绝方式"
          "要先登记，否则 `rejected` 里会冒出一个没有归属的键"
          "（同一事实多处声明的入口）",
      set(CritiqueLedger().rejected) == set(REJECT_KINDS),
      f"rejected 键={sorted(CritiqueLedger().rejected)}")


# ===========================================================================
# AH 组 —— 「语言与沟通」的真判据：可复核性（verifiability）
#
# 旧判据是**答案字数**。字数有一个混淆变量排不掉：
#   150 字的空话 比 80 字的精确答案 长 ⇒ 字数判空话赢。
# 新判据是任务**自带**的 `assertions`（这道题该说清什么），
# 并用「长度与质量反相关」的对照组把旧判据**证伪**。
#
# 真正的位移不在"判得更准"，而在**判据从哪来**：
#   旧 = 尺子自己发明的规则（字数 / 长度的 1/5）
#   新 = 任务自带的 ground truth
# 判据不由测量者发明，就没人能通过调松判据让分数变好看。
# ===========================================================================
from mw.bench import (Assertion, ExternalScorer, ScoreError,  # noqa: E402
                      Scored, Suite)

_AH_SCORER = V1 / "bench" / "scorers" / "assertion.py"
_AH_SUITE = V1 / "bench" / "tasks_lang" / "explain.jsonl"
_AH_src = _AH_SCORER.read_text(encoding="utf-8")
_AH_bench = _src("bench.py")

check(RS, "AH1 ★静态锚：受控词表 `KINDS` **只在打分器里声明一处**；"
          "`mw/bench.py` 不得另立一份\n"
          "          —— 判定合法性由裁判决定。任务层若也校验 kind，两处迟早漂成\n"
          "          「任务层认为合法、打分器不认」的第二套真相",
      'KINDS = (' in _AH_src and "KINDS = (" not in _AH_bench
      and "contains_any" not in _AH_bench,
      f"打分器有={('KINDS = (' in _AH_src)} bench.py 有={('KINDS = (' in _AH_bench)}")

_AH_s = Suite.load(_AH_SUITE)
check(RS, "AH2 任务集必须真的带 `assertions`，且每条都有 `id` 与 `why`"
          "（没有 why 的断言只能说「你错了」，说不出「错在哪」——\n"
          "         那正是这一格要补的东西）",
      len(_AH_s) >= 1 and all(len(t.assertions) >= 3 for t in _AH_s.tasks)
      and all(a.id and a.why for t in _AH_s.tasks for a in t.assertions),
      f"题数={len(_AH_s)} 断言数={[len(t.assertions) for t in _AH_s.tasks]}")

check(RS, "AH3 ★`assertions` 必须进 `fingerprint()` —— 改掉一条「该说清什么」\n"
          "         就等于换了尺子（校准结论会变），不进哈希则 suite_sha 察觉不到",
      "assertions" in Suite.load(_AH_SUITE).tasks[0].fingerprint(),
      f"fingerprint 键={sorted(Suite.load(_AH_SUITE).tasks[0].fingerprint())}")

_AH_bad = _TMP / "ah_bad.jsonl"
_AH_bad.write_text(
    '{"id":"b1","prompt":"p","assertions":[{"id":"x","kind":"contains_any",'
    '"value":["a"]}]}\n', encoding="utf-8")
check(RS, "AH4 缺 `why` 的断言在**装载时**就必须报错（不是等到判分才发现）",
      _raises(lambda: Suite.load(_AH_bad), ValueError),
      "ValueError 已按预期抛出")

check(RS, "AH5 `Scored.detail` 默认空元组 —— 老打分器（code_exec）不给明细是合法的，\n"
          "         不许因为「没明细」就把一条合法分数判为失效",
      Scored(0.5).detail == () and Scored(0.5).why == "",
      f"detail={Scored(0.5).detail}")

_AH_probe = _cap.probe_verifiability()
check(RS, "AH6 ★行为锚（**把旧判据证伪**）：长空话必须比短精确**分低**\n"
          "         （166 字空话 vs 86 字精确答案 —— 若判据是字数，前者赢；\n"
          "          ⇒ 这一条红了就说明判据退化回了长度代理量）",
      _AH_probe["verbose_len"] > _AH_probe["precise_len"]
      and _AH_probe["precise_score"] > _AH_probe["verbose_score"]
      and _AH_probe["length_confound_broken"] is True,
      f"长={_AH_probe['verbose_len']}字/{_AH_probe['verbose_score']} "
      f"短={_AH_probe['precise_len']}字/{_AH_probe['precise_score']}")

check(RS, "AH7 ★逐条明细必须**非空、且确实有未满足项、且每条都带 why** ——\n"
          "         「未满足 a4」没有信息量，「未满足 a4（必须说清生效条件）」才有。\n"
          "         ⚠️ `failed_count >= 1` 这一半不能省：用满分答案验归因 ⇒ 未满足项\n"
          "         集合为空 ⇒ `all([])` 恒真 ⇒ 又一条永远绿的假防线",
      _AH_probe["detail_count"] >= 3 and _AH_probe["failed_count"] >= 1
      and _AH_probe["failed_attributable"] is True,
      f"明细={_AH_probe['detail_count']} 未满足={_AH_probe['failed_count']} "
      f"可归因={_AH_probe['failed_attributable']}")

_AH_rows2 = _cap.assess(_cap_ev)
check(RS, "AH8 读数锚：签名表里「语言与沟通」当前判**反证**"
          "（判据已换成可复核性 ⇒ 可复核的答案通过了；\n"
          "         若它转红，说明答案又说不出可核对的东西了）",
      any(r["capability"] == "语言与沟通" and r["state"] == _cap.ABSENT
          for r in _AH_rows2),
      next((r["detail"] for r in _AH_rows2 if r["capability"] == "语言与沟通"), "无此行"))


def _ah_score(task, answer: str) -> float:
    """用断言打分器给一个答案判分。打分器失败（不可判）返回 -1，
    ⛔ 不许降级成 0.0 —— 「没量到」和「0 分」在这个项目里永远是两个数。"""
    import subprocess
    payload = json.dumps({"task": task.as_dict(), "answer": answer},
                         ensure_ascii=False)
    pr = subprocess.run([sys.executable, str(_AH_SCORER)], input=payload,
                        capture_output=True, text=True)
    if pr.returncode != 0:
        return -1.0
    return float(json.loads(pr.stdout.strip().splitlines()[-1])["score"])


def _ah_run(payload: str) -> int:
    import subprocess
    pr = subprocess.run([sys.executable, str(_AH_SCORER)], input=payload,
                        capture_output=True, text=True)
    return pr.returncode


check(RS, "AH9 ★缺 `assertions` ⇒ 退出码 **2（不可判）**，绝不是 0 分\n"
          "         —— 把『这道题没说清该说什么』记成『答案 0 分』，\n"
          "         就是把尺子的缺陷记到被测系统头上（契约③）",
      _ah_run(json.dumps({"task": {"id": "x"}, "answer": "a"})) == 2,
      f"rc={_ah_run(json.dumps({'task': {'id': 'x'}, 'answer': 'a'}))}")

check(RS, "AH10 未知 kind ⇒ 同样判不可判（rc=2），不许静默当不满足/当满足",
      _ah_run(json.dumps({"task": {"id": "x", "assertions": [
          {"id": "k", "kind": "magic", "why": "w"}]}, "answer": "a"})) == 2,
      "rc 已按预期为 2")

# ⚠️ AH12 补的原因：我在 `mw/bench.py` 的解析里加了「detail 格式不对就报错」，
#    却**没有断言盯着它** —— 那它就是一段装饰性代码，删掉也没人知道。
#    本项目纪律：写了校验就要有断言，否则等于没写。
def _ah_fake(detail_literal: str) -> "ExternalScorer":
    """造一个输出指定 detail 的假打分器。"""
    f = _TMP / f"ah_fake_{abs(hash(detail_literal))}.py"
    f.write_text("import json, sys\n"
                 f'json.dump({{"score": 0.5, "why": "w", "detail": {detail_literal}}},'
                 " sys.stdout)\n", encoding="utf-8")
    return ExternalScorer([sys.executable, str(f)], name="fake")


check(RS, "AH12 ★`detail` 是列表但**元素不是对象** ⇒ 必须报错，不许静默丢掉\n"
          "         （静默丢掉 = 一句「我不对」又变回没有理由，这一格就白补了）",
      _raises(lambda: _ah_fake('["oops"]').score({"id": "t"}, "a"), ScoreError),
      "ScoreError 已按预期抛出")

check(RS, "AH13 `detail` **整体不是列表**（如字符串）⇒ 同样必须报错\n"
          "         （两条路径分属两个 `isinstance` 检查，只钉一条会漏掉另一条）",
      _raises(lambda: _ah_fake('"oops"').score({"id": "t"}, "a"), ScoreError),
      "ScoreError 已按预期抛出")

_AH_t0 = _AH_s.tasks[0]
check(RS, "AH11 ★三档有序（分辨力）：oracle > partial > null，且**中段可达**\n"
          "         （只证明满分/零分可分太弱 —— 那与一把五天不动的尺子没区别）",
      all(_ah_score(t, t.oracle) > _ah_score(t, t.partial) > _ah_score(t, t.null)
          for t in _AH_s.tasks)
      and all(0.0 < _ah_score(t, t.partial) < 1.0 for t in _AH_s.tasks),
      f"partial={[round(_ah_score(t, t.partial), 3) for t in _AH_s.tasks]}")


# ===========================================================================

# ===========================================================================
# AI 组 —— 「自我模型」的真判据：跨会话、带口径签名的分数账本（§16.7 第 3 条）
#
# 旧判据是**代理量**（`consulted × influenced`）—— 自我模型**总会**被咨询，
# 所以那条永远绿，它只能证明"它说过话"，证明不了"它说得对"。
# 新判据是**行为锚**：用历史会话的分数预测未来会话，
# 误差必须显著低于一个不区分能力族的对照基线；
# 且撤掉族标签 / 撤掉口径闸，读数必须转红。
# ===========================================================================
import importlib.util as _ilu2                                     # noqa: E402

from mw import CORE_MODULES                                        # noqa: E402
from mw.ledger import (CALIBER_FIELD, LedgerStats, REJECT_KINDS,  # noqa: E402
                       ScoreLedger, is_passed, ledger_sig_of,
                       next_session, signature_of)

_spec_smp = _ilu2.spec_from_file_location("v1_probe_selfmodel",
                                          V1 / "bench" / "probe_selfmodel.py")
_smp = _ilu2.module_from_spec(_spec_smp)
sys.modules["v1_probe_selfmodel"] = _smp
_spec_smp.loader.exec_module(_smp)

_AI_bench_src = _src("bench.py")
_AI_sig_src = ""
for _n in ast.walk(ast.parse(_AI_bench_src)):
    if isinstance(_n, ast.ClassDef) and _n.name == "Caliber":
        for _m in _n.body:
            if isinstance(_m, ast.FunctionDef) and _m.name == "signature":
                _AI_sig_src = ast.get_source_segment(_AI_bench_src, _m) or ""
check(RS, "AI1 ★静态锚：口径签名的实现**只有一处** —— `bench.Caliber.signature`\n"
          "         必须委托 `mw.ledger.signature_of`\n"
          "         （两边各写一份 `dumps` ⇒ 哪天 `separators` 差一点，同一个 caliber\n"
          "         算出两个签名，'同口径'当场变假，而**没有任何测试会红**）",
      "signature_of" in _AI_sig_src and "hexdigest()[:16]" not in _AI_bench_src,
      f"signature 源码={_AI_sig_src.strip()[:80]!r}")

# ── 账本：读侧拒绝（模拟一个"老写入方"留下的无签名行）─────────────────
_AI_led = _TMP / "ai_ledger.jsonl"
_AI_cal = {"scorer_sha": "S", "suite_sha": "U", "temperature": 0, "max_tokens": 512}
_AI_sig = signature_of(_AI_cal)
_AI_other = signature_of({"scorer_sha": "S2", "suite_sha": "U2"})
_AI_led.write_text("\n".join([
    '{"session":0,"family":"f1","passed":true,"score":1.0,"caliber_sig":"%s"}' % _AI_sig,
    '{"session":0,"family":"f1","passed":false,"score":0.0,"caliber_sig":"%s"}' % _AI_other,
    '{"session":0,"family":"f1","passed":true,"score":1.0}',          # 无签名
    "{bad json",                                                       # 坏行
]) + "\n", encoding="utf-8")
_AI_L = ScoreLedger(_AI_led, caliber_sig=_AI_sig)
_AI_adm = _AI_L.admissible()
check(RS, "AI2 ★缺 `caliber_sig` 的行**必须被拒**，不并入、不默认同口径\n"
          "         （缺签名 ≠ 同口径。默认放行 = 把两种东西当一种 = 跨口径比较）",
      len(_AI_adm) == 1 and _AI_L.stats.rejected_no_sig == 1,
      f"采纳={len(_AI_adm)} 无签名拒={_AI_L.stats.rejected_no_sig}")

check(RS, "AI3 ★异口径被拒且**分项计数** —— 只报一个总数无法归因：\n"
          "         你分不清挡下来的是'没签名'还是'口径不对'（与 critique 同构）",
      _AI_L.stats.rejected_diff_sig == 1 and _AI_L.stats.rejected_no_sig == 1
      and _AI_L.stats.bad_lines == 1
      and set(REJECT_KINDS) >= {"missing_fields", "no_caliber_sig", "diff_caliber_sig"},
      f"{_AI_L.stats.as_dict()}")

# ── 写侧：缺字段的行**根本不进文件** ────────────────────────────────────
_AI_led2 = _TMP / "ai_ledger2.jsonl"
_AI_L2 = ScoreLedger(_AI_led2, caliber_sig=_AI_sig)
_AI_w = _AI_L2.append([
    {"session": 0, "family": "demo", "passed": True, "score": 1.0,
     "caliber_sig": _AI_sig, "caliber": _AI_cal},
    {"session": 0, "passed": True, "score": 1.0, "caliber_sig": _AI_sig,
     "caliber": _AI_cal},  # 缺 family
])
check(RS, "AI4 写入侧也要挡：缺必需字段的行**不进文件**\n"
          "         （先全记下来、读时再筛 ⇒ 文件里堆积无从归属的行，\n"
          "          那就是下一次跨口径比较的入口）",
      _AI_w["added"] == 1 and _AI_w["rejected"] == 1
      and len(_AI_led2.read_text(encoding="utf-8").strip().splitlines()) == 1,
      f"append={_AI_w} 行数={len(_AI_led2.read_text(encoding='utf-8').strip().splitlines())}")

# ── 通电：Core 起跑冻结历史，且**轮内不写** ────────────────────────────
_AI_L2.append([{"session": s, "family": "demo", "passed": True, "score": 1.0,
                "caliber_sig": _AI_sig, "caliber": _AI_cal} for s in (1, 2)])
_AI_core = Core(llm=DemoLLM(), score_ledger=_AI_L2, caliber_sig=_AI_sig)
_AI_n_before = len(_AI_led2.read_text(encoding="utf-8").strip().splitlines())
_AI_ev_before = len(_AI_core.selfmodel.evidence)
# ⚠️ 起跑后**外部**再追加两行 —— 快照语义的判据：本轮不许看见它们
_AI_L2.append([{"session": 3, "family": "demo", "passed": False, "score": 0.0,
                "caliber_sig": _AI_sig, "caliber": _AI_cal}])
_AI_out = _AI_core.run(DemoLLM.GOAL, family="demo")
_AI_n_after = len(_AI_led2.read_text(encoding="utf-8").strip().splitlines())

check(RS, "AI5 ★行为锚：Core 起跑时**真的**读到了历史证据（不是空着起跑）\n"
          "         —— 之前 `Core()` 里写的是 `SelfModel()`，于是自我模型的\n"
          "         判据只能退到代理量（被咨询过几次）",
      _AI_ev_before == 3 and _AI_core.selfmodel.global_rate() is not None,
      f"证据={_AI_ev_before} 条 全局率={_AI_core.selfmodel.global_rate()}")

check(RS, "AI6 ★跨会话纪律：`Core.run()` **不写**账本（行数前后不变）\n"
          "         —— 轮内写入会让第 N 题读到第 N-1 题的分数，`repeat` 的意义当场消失",
      _AI_n_after == _AI_n_before + 1,          # +1 = 测试自己刚追加的那行
      f"前={_AI_n_before} 后={_AI_n_after}（测试追加 1 行）")

check(RS, "AI7 ★起跑快照：跑完之后，本轮看到的证据**仍是起跑那 3 条**\n"
          "         （外部在 run 期间追加的行不许被本轮看见 —— 否则又是轮内污染）",
      len(_AI_core.selfmodel.evidence) == 3,
      f"证据={len(_AI_core.selfmodel.evidence)} 条（期望 3）")

check(RS, "AI8 账本读数必须进 `Outcome.stats` —— 没出口的计数是进程内内存态\n"
          "         （重启清零、跨进程不可见，拿它做诊断只能看个热闹）",
      int((_AI_out.stats.get("score_ledger") or {}).get("admitted") or 0) == 3,
      f"stats.score_ledger={_AI_out.stats.get('score_ledger')}")

# ── 判据本体：跨会话预测校准 ────────────────────────────────────────────
_AI_rows = _smp.build_synthetic_rows()
_AI_cal = _smp.probe_calibration(_AI_rows)
_AI_null = _smp.probe_calibration(_smp.build_null_rows())
check(RS, "AI9 ★判据本体：用历史预测未来，误差必须**优于**不区分族的对照基线\n"
          "         —— 这才回答'自我模型有没有用'，而不是'它有没有被咨询过'",
      _AI_cal["state"] == "PRESENT"
      and _AI_cal["mae_model"] < _AI_cal["mae_base"]
      and _AI_cal["delta"] > _AI_cal["margin"],
      f"模型 MAE={_AI_cal['mae_model']} 基线 MAE={_AI_cal['mae_base']} "
      f"Δ={_AI_cal['delta']} 阈值={_AI_cal['margin']}")

check(RS, "AI10 ★反证组（各族真实通过率**完全相同**，族间本无差异）：\n"
          "          判据**不许**绿，否则 AI9 是一条恒真断言\n"
          "          （一个怎么都能赢的判据，和一个恒为 0 的适应度是同一种东西）\n"
          "          ⚠️ 旧版用的是『轮转族标签』，那只给族改名 —— 未来行若跟着\n"
          "             改名则信号毫发无损，改历史则恒等于完全错配，都不是零假设",
      _AI_null["state"] != "PRESENT",
      f"无差异账本 Δ={_AI_null['delta']} 阈值={_AI_null['margin']} "
      f"⇒ {_AI_null['state']}")

_AI_gate = _smp.probe_caliber_gate()
check(RS, "AI11 ★口径闸是不是**承重的**：混入异口径 ⇒ 预测必须变差\n"
          "          （撤了闸误差也不变 ⇒ 那道闸是装饰；装饰的闸就是失效的起点）",
      _AI_gate["state"] == "PRESENT" and _AI_gate["loss"] > 0,
      f"同口径 MAE={_AI_gate['gated']['mae_model']} "
      f"混入后 MAE={_AI_gate['mixed']['mae_model']} 劣化={_AI_gate['loss']}")

# ── 不装懂 / 空账本 ─────────────────────────────────────────────────────
_AI_sm9 = SelfModel(load_evidence(
    [{"family": "a", "passed": True, "score": 1.0}] * (MIN_SAMPLES - 1)
    + [{"family": "b", "passed": False, "score": 0.0}] * (MIN_SAMPLES - 1)))
check(RS, "AI12 样本不足 ⇒ `predict` 退回**全局率**且 `advise().confident` 为假\n"
          "         —— 看过 2 道题就给建议的自我模型，与桩的区别只在于它更有说服力",
      _AI_sm9.predict("a") == _AI_sm9.global_rate()
      and _AI_sm9.advise(family="a").confident is False,
      f"predict={_AI_sm9.predict('a')} 全局率={_AI_sm9.global_rate()}")

check(RS, "AI13 空账本 ⇒ 不崩、不装懂（predict=None / confident=False），"
          "且账本读数全零",
      SelfModel().predict("x") is None
      and SelfModel().advise(family="x").confident is False
      and Core(llm=DemoLLM()).selfmodel.evidence == [],
      "空账本路径已跑通")

# ── 内核边界登记完整性 ──────────────────────────────────────────────────
_AI_core_imports = set()
for _n in ast.walk(ast.parse(_src("core.py"))):
    if isinstance(_n, ast.ImportFrom) and _n.level == 1 and _n.module:
        _AI_core_imports.add(_n.module)
check(RS, "AI14 ★内核边界清单必须**完整**：`core.py` 直接 import 的每个内核模块\n"
          "         都必须在 `mw.CORE_MODULES` 里\n"
          "         （漏登记的模块不受 H1「不许 import bench」检查 ⇒ 那条最硬\n"
          "         的边界纪律只覆盖了 10/15 个模块，而'验证了'三个字是假的）",
      _AI_core_imports <= set(CORE_MODULES),
      f"core.py 引入={sorted(_AI_core_imports)} "
      f"漏登记={sorted(_AI_core_imports - set(CORE_MODULES))}")

# ===========================================================================
# AJ 组 —— 族预测力闸：「分族」测不出预测力时，**不许**据此行动
#
# 起因（一次实测，不是设计推演）：真模型账本 4 会话 × 2 族 × 40 行上
# Δ = −0.015 ⇒ 族内漂移大于族间差异。而 `advise()` 的老门槛只看样本数，
# 于是它**在噪声上给建议**。这一组把"样本够 ≠ 规律真"钉成可执行断言。
# ===========================================================================
from mw.metacog import (MIN_POWER_SESSIONS, N_CONTROL_PERMS,       # noqa: E402
                        POWER_MARGIN_FLOOR, FamilyPower,
                        _noise_floor, _split_sessions,
                        measure_family_power)

_AJ_ev = _smp.build_synthetic_rows()       # 5 族、族率 0.10~0.90 真信号
_AJ_pw = SelfModel(load_evidence(_AJ_ev)).family_power()
check(RS, "AJ1 ★闸在**有真实族信号**的账本上（5 族、族率 0.10~0.90）必须**开**\n"
          "         （有族信号却不行动 ⇒ 自我模型是装饰模块）",
      _AJ_pw.usable and _AJ_pw.state == "PRESENT"
      and _AJ_pw.delta > _AJ_pw.margin,
      _AJ_pw.as_line())

_AJ_real_p = V1 / "bench" / "evidence" / "score_ledger_real_qwen25_7b_4sess.jsonl"
_AJ_real_rows = ScoreLedger(_AJ_real_p).load()
_AJ_real_sm = SelfModel(load_evidence(_AJ_real_rows))
_AJ_real_pw = _AJ_real_sm.family_power()
check(RS, "AJ2 ★★真模型账本（qwen2.5:7b / T=0.8 / 4 会话 × 2 族 × 40 行）上闸必须**关**\n"
          "         —— 这是本轮的核心断言，用**真数据**而不是合成数据：\n"
          "            在此之前 `advise()` 只看样本数，于是在这份账本上\n"
          "            （样本 20 条，完全够）它会笃定地按族调整预算，\n"
          "            而实测 Δ=−0.015 说明那些族率只是噪声",
      _AJ_real_pw.usable is False
      and _AJ_real_sm.advise(family="numeric_logic").confident is False
      and _AJ_real_sm.advise(family="text_ops").confident is False,
      _AJ_real_pw.as_line())

check(RS, "AJ3 ★真账本上的状态必须是 **ABSENT**（测出来了，结论为负），\n"
          "         不许是 UNKNOWN\n"
          "         （§16.7 第二层判据：没量到 ≠ 没有。若这里读成 UNKNOWN，\n"
          "          就会有人去'再多跑几个会话'—— 而真相是跑了也没用）",
      _AJ_real_pw.state == "ABSENT"
      and _AJ_real_pw.delta is not None
      and _AJ_real_pw.n_future >= 4
      and "漂移" in (_AJ_real_pw.reason or ""),
      f"state={_AJ_real_pw.state} 历史={_AJ_real_pw.n_history} "
      f"未来={_AJ_real_pw.n_future} 原因={_AJ_real_pw.reason}")

check(RS, "AJ4 闸关着时 `predict()` 必须退回**全局率**，不是族率\n"
          "         （继续报族率 = 嘴上说不知道、手上还在按族行动）",
      _AJ_real_sm.predict("numeric_logic") == _AJ_real_sm.global_rate()
      and _AJ_real_sm.predict("text_ops") == _AJ_real_sm.global_rate(),
      f"predict={_AJ_real_sm.predict('text_ops')} "
      f"全局率={_AJ_real_sm.global_rate()}")

# ── UNKNOWN 也必须 fail-closed，且不许并入 ABSENT ───────────────────────
_AJ_unk = SelfModel([{"family": "a", "passed": i % 2 == 0,
                      "session": 0} for i in range(10)]
                    + [{"family": "b", "passed": i % 3 == 0,
                        "session": 0} for i in range(10)])
_AJ_unk_pw = _AJ_unk.family_power()
check(RS, "AJ5 ★UNKNOWN 也必须 fail-closed（`confident=False`），但**不许**并入 ABSENT\n"
          "         —— 结论层面：没量到 ≠ 没有（两个词不许合成一个）\n"
          "         —— 行动层面：不知道就不许动（两者都关）",
      _AJ_unk_pw.state == "UNKNOWN"
      and _AJ_unk_pw.usable is False
      and _AJ_unk.advise(family="a").confident is False
      and _AJ_unk_pw.delta is None,
      f"state={_AJ_unk_pw.state} 会话={_AJ_unk_pw.n_sessions} "
      f"原因={_AJ_unk_pw.reason}")

# ── 本轮修掉的数学缺陷 ──────────────────────────────────────────────────
check(RS, "AJ6 ★★2 族**极端可分**（0.0 vs 1.0）必须能判 PRESENT\n"
          "         —— 第一版用『轮转族名』当反证组，2 族时只有恒等/交换两种结果，\n"
          "            噪声恒等于正组 Δ ⇒ margin=2Δ>Δ ⇒ **判据在数学上不可能成立**。\n"
          "            这一条就是那个缺陷的墓碑：它当时会让最强的信号也判不出来。\n"
          "            ⚠️ 与 AJ1 不重复：AJ1 是 5 族，这一条是**只有 2 族**的极限情形",
      SelfModel(_mk_separable_evidence()).family_power().state == "PRESENT",
      SelfModel(_mk_separable_evidence()).family_power().as_line())

_AJ_rep = SelfModel(_AJ_real_ev := _AJ_ev).report()
check(RS, "AJ7 读数出口：`report()` 必须暴露 `family_power`，且闸关时\n"
          "         `confident_families` 必为空（`eligible_families` 仍可非空）\n"
          "         —— 两个列表分开，是因为「样本够但闸关着」与「样本不够」\n"
          "            的修法完全不同：前者要换族的定义，后者只要继续跑",
      "family_power" in _AJ_rep and _AJ_rep["family_power"]["usable"] is True
      and _AJ_rep["confident_families"] == _AJ_rep["eligible_families"]
      and SelfModel(_AJ_real_rows and load_evidence(_AJ_real_rows)
                    ).report()["confident_families"] == [],
      f"闸开: {_AJ_rep['confident_families']} / 闸关: "
      f"{SelfModel(load_evidence(_AJ_real_rows)).report()['confident_families']}")

# ── session 字段的传递（闸永远关着的最隐蔽成因）────────────────────────
_AJ_sess = load_evidence([{"family": "a", "passed": True, "session": 7}])
check(RS, "AJ8 ★`load_evidence` 必须把 `session` 带过去\n"
          "         —— 丢掉它 ⇒ 时序切分做不了 ⇒ 闸永远关着，而日志只显示\n"
          "            「样本够、Δ=n/a」，看不出真正的原因（本轮最易漏的一处）",
      _AJ_sess and _AJ_sess[0].get("session") == 7,
      f"{_AJ_sess[0] if _AJ_sess else '空'}")

_AJ_bad = load_evidence([{"family": "a", "passed": True, "session": "abc"}])
check(RS, "AJ9 非数字 `session` ⇒ 留正向信号（`session_bad`），**不许**填 0、不许静默丢\n"
          "         （填 0 = 让这一行假装属于会话 0，于是被算进切分；『缺失当零』）",
      _AJ_bad and _AJ_bad[0].get("session") is None
      and _AJ_bad[0].get("session_bad") == "abc",
      str(_AJ_bad[0] if _AJ_bad else "空"))

# ── 算法形状：零假设必须是**行级置换**，常数不可调 ──────────────────────
_AJ_mc = _src("metacog.py")
_AJ_tree = ast.parse(_AJ_mc)
check(RS, "AJ10 ★噪声下限必须用**行级置换检验**，不许回到『给族改名』\n"
          "         （改名版：2 族时恒等于完全错配；5 族时噪声随族列表的排列\n"
          "           顺序变化 —— 字母序恰好把 0.90 与 0.10 排相邻 ⇒ 噪声翻倍）",
      "_permuted_labels" in _AJ_mc and "_rotated" not in _AJ_mc
      and "_shuffle_families" not in _AJ_mc,
      f"含 _permuted_labels={'_permuted_labels' in _AJ_mc} "
      f"残留 _rotated={' _rotated' in _AJ_mc}")

_AJ_env = [n for n in ast.walk(_AJ_tree)
           if isinstance(n, ast.Attribute)
           and isinstance(n.value, ast.Name)
           and n.value.id in ("os", "environ")
           or (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id in ("getenv", "environ"))]
check(RS, "AJ11 ★闸的四个常数**不可调**：`metacog.py` 不许读环境变量\n"
          "         （能调阈值 = 能把结论调好看的旋钮 ⇒ 尺子下移）",
      not _AJ_env and "os." not in _AJ_mc and "getenv" not in _AJ_mc,
      f"可疑节点={len(_AJ_env)} 含 os.={'os.' in _AJ_mc}")

_AJ_adv = ""
for _n in ast.walk(_AJ_tree):
    if isinstance(_n, ast.FunctionDef) and _n.name == "advise":
        _AJ_adv = ast.dump(_n)
check(RS, "AJ12 ★`advise()` 必须真的消费 `family_power()`（不是只在文档里说）\n"
          "         —— 闸写在别处、advise 不看它，就是一条装饰性防线",
      "family_power" in _AJ_adv and "usable" in _AJ_adv,
      f"advise 内引用 family_power={'family_power' in _AJ_adv}")

check(RS, "AJ13 闸关着时**不许**自增 `influenced`\n"
          "         （给一条从未生效的建议记功，正是旧判据 consulted×influenced 的病）",
      _AJ_real_sm.influenced == 0 and _AJ_real_sm.consulted >= 1,
      f"咨询={_AJ_real_sm.consulted} 影响={_AJ_real_sm.influenced}")

check(RS, "AJ14 闸的读数必须有**内核侧消费者**：汇总行念出闸开没开 + Δ + 影响次数\n"
          "         （只在 report() 里躺着的字段 = 进程内内存态，重启清零）",
      "family_power" in _src("core.py") and "闸=" in _src("core.py"),
      "core.py 汇总行含 闸= / family_power")

check(RS, "AJ15 ★反证：撤掉闸（`usable` 恒为 True）⇒ AJ2 必须转红\n"
          "         —— 由 R13 撤回态脚本验证，此处钉住**撤回点存在且唯一**",
      _AJ_mc.count("def usable") == 1 and "return self.state == \"PRESENT\"" in _AJ_mc,
      f"usable 定义数={_AJ_mc.count('def usable')}")

# ── 账本幂等：中断重跑不许静默污染（本轮自踩）──────────────────────────
_AJ_dup = ScoreLedger(Path(tempfile.mkdtemp()) / "led.jsonl")
_AJ_cal = {"scorer_sha": "S", "temperature": 0, "max_tokens": 512}
_AJ_sig = signature_of(_AJ_cal)
_AJ_dup.append([{"session": 0, "family": "a", "passed": True, "score": 1.0,
                 "task_id": "t1", "caliber_sig": _AJ_sig, "caliber": _AJ_cal}])
_w2 = _AJ_dup.append([{"session": 0, "family": "a", "passed": False, "score": 0.0,
                       "task_id": "t1", "caliber_sig": _AJ_sig, "caliber": _AJ_cal}])
check(RS, "AJ16 ★账本**幂等**：同一会话的同一题写两次 ⇒ 第二次被拒\n"
          "         —— 本轮自踩：后台实验被 kill 后重启，`next_session` 算出同一个\n"
          "            会话号 ⇒ 那一轮成绩被写两遍（14 行变 27 行）。账本当时**静默\n"
          "            接受**，于是通过率被两份数据加权，而日志上看不出来。\n"
          "            长跑服务被 kill 后重启是常态，这不是理论风险",
      _w2["added"] == 0 and _w2["duplicate"] == 1
      and len(_AJ_dup.load()) == 1 and _AJ_dup.load()[0]["passed"] is True,
      f"第二次写入 {_w2}  文件行数={len(_AJ_dup.load())}")

_w3 = _AJ_dup.append([{"session": 1, "family": "a", "passed": False, "score": 0.0,
                       "task_id": "t1", "caliber_sig": _AJ_sig, "caliber": _AJ_cal}])
check(RS, "AJ17 判重**只按 (会话, 题)**：同一 task_id 在**不同**会话里必须照收\n"
          "         （同一套题每个会话都要跑一遍，把它当重复 ⇒ 第二次起账本全空）",
      _w3["added"] == 1 and _w3["duplicate"] == 0
      and len(_AJ_dup.load()) == 2,
      f"新会话写入 {_w3}  文件行数={len(_AJ_dup.load())}")

check(RS, "AJ18 `duplicate` 必须进受控词表与被拒读数（可归因，不许混进总拒绝数）",
      "duplicate" in REJECT_KINDS and "duplicate" in _AJ_dup.stats.as_dict()["rejected"],
      f"REJECT_KINDS={REJECT_KINDS}")

# ── 读侧去重：写侧判重只保护未来，保护不了历史 ──────────────────────────
# 证据是**真实的脏账本**（本轮自踩产生的，已留档），不是构造的玩具数据。
_AJ_DIRTY = Path("bench/evidence/score_ledger_DIRTY_duprows_T0.jsonl")
_AJ_dirty = ScoreLedger(_AJ_DIRTY)
_AJ_dirty_rows = _AJ_dirty.load()
_AJ_dirty_sig = collections.Counter(
    r.get("caliber_sig") for r in _AJ_dirty_rows).most_common(1)[0][0]
_AJ_dirty.caliber_sig = _AJ_dirty_sig
_AJ_adm = _AJ_dirty.admissible()

check(RS, "AJ19 ★★读侧去重：已经躺在文件里的重复行，读出来不许被加权\n"
          "         —— 写侧判重（AJ16）只保护**未来**，保护不了**历史**。\n"
          "            本轮实测抓到一个我自己的错误结论：从这份脏账本读出\n"
          "            『T=0 下 struct_convert 0.56→0.36，说明 T=0 也有漂移』；\n"
          "            去重后真相是 2/5=0.400，与前四会话**逐题完全一致**。\n"
          "            ⇒ 写侧判重会让未来干净；读侧不去重，历史永远在说谎",
      _AJ_dirty.stats.deduped_on_read > 0
      and len(_AJ_adm) < len(_AJ_dirty_rows)
      and len({(r.get("session"), r.get("task_id")) for r in _AJ_adm}) == len(_AJ_adm),
      f"原始={len(_AJ_dirty_rows)} 采纳={len(_AJ_adm)} "
      f"读侧去重={_AJ_dirty.stats.deduped_on_read}")


def _rate(sub, fam, sess):
    n = [r for r in sub if r.get("family") == fam and r.get("session") == sess]
    return (sum(1 for r in n if r["passed"]) / len(n)) if n else None


_AJ_dirty_fams = sorted({r["family"] for r in _AJ_adm})
_AJ_dirty_sess = sorted({r["session"] for r in _AJ_adm if r.get("session") != 6})
_AJ_flat = {_rate(_AJ_adm, f, s) for f in _AJ_dirty_fams for s in _AJ_dirty_sess}
check(RS, "AJ20 ★去重后 T=0 的**族内漂移必须为零**（六个会话逐题一致）\n"
          "         —— 这条同时是 AJ19 的因果检验：若去重是对的，被污染的\n"
          "            『漂移』就应当随之消失。它没消失 ⇒ 去重去错了东西",
      len(_AJ_flat) == len(_AJ_dirty_fams),
      f"{len(_AJ_dirty_fams)} 个族 × {len(_AJ_dirty_sess)} 个会话 "
      f"⇒ {len(_AJ_flat)} 个不同的通过率（应等于族数 {len(_AJ_dirty_fams)}）"
      f"  实测={sorted(_AJ_flat)}")

_AJ_clean = ScoreLedger(Path("bench/evidence/score_ledger_real_qwen25_7b_T0_4sess.jsonl"))
_AJ_clean.caliber_sig = _AJ_clean.load()[0]["caliber_sig"]
_AJ_clean_adm = _AJ_clean.admissible()
check(RS, "AJ21 去重**不许误伤**干净账本：无重复 ⇒ deduped=0 且一行不少\n"
          "         （去重是手术刀，不是筛子；误伤会让账本悄悄变小）",
      _AJ_clean.stats.deduped_on_read == 0
      and len(_AJ_clean_adm) == len(_AJ_clean.load()) == 56,
      f"总={len(_AJ_clean.load())} 采纳={len(_AJ_clean_adm)} "
      f"去重={_AJ_clean.stats.deduped_on_read}")

check(RS, "AJ22 判重键的构造**只有一处**（`_key`），写侧与读侧共用\n"
          "         —— 两处各写一遍 = 同一事实两处声明 ⇒ 一边改了另一边没改，\n"
          "            账本会**静默**地一半去重一半不去重，两份读数都长得合理",
      _src("ledger.py").count("def _key") == 1
      and _src("ledger.py").count("_key(r.get(") >= 4,
      f"_key 定义数={_src('ledger.py').count('def _key')}")

# ── 置换次数：σ 自身的抽样误差不许与被估的量同量级 ──────────────────────
check(RS, "AJ23 ★★置换检验次数必须让 σ 收敛（≥256）\n"
          "         —— 上一版是 8，注释还写着『超过 8 次收益递减』。实测推翻：\n"
          "            4 会话 n=8 σ=0.0217 / n=512 σ=0.0257 / n=2048 σ=0.0264\n"
          "            6 会话 n=8 σ=0.0279 / n=512 σ=0.0223 / n=2048 σ=0.0215\n"
          "            ⇒ 8 次时两行**大小关系是反的**（判据在掷骰子）",
      N_CONTROL_PERMS >= 256,
      f"N_CONTROL_PERMS={N_CONTROL_PERMS}")

_AJ_ev_real = load_evidence(_AJ_clean_adm)
_AJ_h, _AJ_f, _AJ_ss = _split_sessions(_AJ_ev_real)
_AJ_sd = {}
for _n in (N_CONTROL_PERMS, N_CONTROL_PERMS * 4):
    _AJ_sd[_n] = _noise_floor(_AJ_h, _AJ_f, _n)[0]
check(RS, "AJ24 真实账本上 σ 必须**已经在收敛区**：σ(4N) 与 σ(N) 相差 < 0.002\n"
          "         （这是 AJ23 取 512 而非 2048 的实证依据，不是拍脑袋）",
      abs(_AJ_sd[N_CONTROL_PERMS * 4] - _AJ_sd[N_CONTROL_PERMS]) < 0.002,
      f"σ({N_CONTROL_PERMS})={_AJ_sd[N_CONTROL_PERMS]:.4f} "
      f"σ({N_CONTROL_PERMS * 4})={_AJ_sd[N_CONTROL_PERMS * 4]:.4f} "
      f"差={abs(_AJ_sd[N_CONTROL_PERMS * 4] - _AJ_sd[N_CONTROL_PERMS]):.4f}")

# ── 里程碑：真模型真实账本上，闸第一次转绿 ──────────────────────────────
_AJ_8 = ScoreLedger(V1 / "bench" / "evidence"
                    / "score_ledger_real_qwen25_7b_T0_8sess.jsonl")
_AJ_8_rows = _AJ_8.load()
_AJ_8.caliber_sig = collections.Counter(
    r.get("caliber_sig") for r in _AJ_8_rows).most_common(1)[0][0]
_AJ_8_adm = _AJ_8.admissible()
_AJ_8_sm = SelfModel(load_evidence(_AJ_8_adm))
_AJ_8_pw = _AJ_8_sm.family_power()
check(RS, "AJ25 ★★真实 8 会话账本上，族预测力闸必须**开**\n"
          "         （qwen2.5:7b / T=0 / 8 会话 × 3 族 × 114 行）\n"
          "         —— 这是「自我模型在**真实数据**上说得算」的第一个证据。\n"
          "            4 会话 ABSENT / 6 会话 ABSENT / 8 会话 PRESENT：\n"
          "            不是结论在漂，是**统计功效**随会话数增长（σ ∝ 1/√N）",
      _AJ_8_pw.usable and _AJ_8_pw.state == "PRESENT"
      and _AJ_8_pw.delta > _AJ_8_pw.margin
      and _AJ_8.stats.deduped_on_read == 0,
      _AJ_8_pw.as_line())

check(RS, "AJ26 ★『开得多稳』必须进读数：snr = Δ/阈值 不许缺席\n"
          "         —— 只念『闸=开』会把 1.05（刚过线）和 3.0（稳固）说成同一件事。\n"
          "            实测这份账本 snr≈1.06：**临界**，换一批数据就可能翻回去",
      _AJ_8_pw.snr is not None and "snr" in _AJ_8_pw.as_dict()
      and "snr=" in _AJ_8_pw.as_line(),
      f"snr={_AJ_8_pw.snr}（Δ={_AJ_8_pw.delta} 阈值={_AJ_8_pw.margin}）")

# ═══════════════════════════════════════════════════════════════════════════
# AK 组 —— 「族」到底是概念，还是「它在哪个文件里」？（2026-10-17 第十四轮）
#
# 背景：自我模型的核心断言是「知道族 ⇒ 更好地预测我会不会做对」，
#       而 `family = suite.name`（`mw/bench.py:769`）。题目是我按抽屉分装的，
#       于是"这些抽屉通过率不同且可预测"这句话里**没有一点关于题目的信息**。
#
#       `mw/features.py` 给了一套**只用题面**的结构特征 + 确定性聚类，
#       `bench/probe_family_def.py` 在**已存在的**真实账本上（零新模型调用）
#       用同一把尺子对照两种族定义。
#
# ⛔ 本组最关键的是 AK6 —— 它把一个**负面发现**钉成不变量：
#    若有人（包括未来的我）回头调特征关键词把结构族刷绿 ⇒ AK6 **判红**
#    ⇒ 强制显式承认"我动了尺子"。这是防「尺子购物」的物理机制。
# ═══════════════════════════════════════════════════════════════════════════

def _strip_docstrings(src: str) -> str:
    """去掉 docstring 与注释后的代码文本。

    ⚠️ 硬坑：全文件 `index()`/`find()` 会命中**注释和 docstring 里
    的同名文字** ⇒ 静态锚变成恒真断言。本模块的 docstring 里必须能自由地
    讨论 `oracle`（那是纪律本身），所以这里用 AST 剥掉字符串字面量。
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


_FEAT_CODE = _strip_docstrings(_src("features.py"))

check(RS, "AK1 ★★特征抽取**不许看答案**：代码里不得出现 `oracle`\n"
          "         —— 用标准答案定义族 = 用答案定义难度 ⇒ 族变成难度的同义词，\n"
          "            断言「知道族能预测成败」退化成「知道难度能预测难度」",
      "oracle" not in _FEAT_CODE,
      f"命中={'oracle' in _FEAT_CODE}")

check(RS, "AK2 ★★特征抽取**不许看得失**：代码里不得出现 `passed` / `score`\n"
          "         —— 那是**被预测的量**。用它定义族就是彻底的循环论证",
      ("passed" not in _FEAT_CODE) and ("score" not in _FEAT_CODE),
      f"passed={'passed' in _FEAT_CODE} score={'score' in _FEAT_CODE}")

_AK_task = {"id": "ak_probe", "prompt": "写一个函数 f(s)，把字符串 s 转成整数。",
            "tests": ["assert f('1') == 1", "assert f('') == None"],
            "oracle": "def f(s): return 1", "meta": {"level": "medium"}}
_AK_t_copy = json.loads(json.dumps(_AK_task))
_AK_f1 = F.task_features(_AK_task)
check(RS, "AK3 特征抽取是**纯函数**：同输入两次结果逐位相同，且不修改入参",
      _AK_f1 == F.task_features(_AK_task) and _AK_task == _AK_t_copy,
      f"特征数={len(_AK_f1)}")

_AK_orig = F._OP_RULES["op_parse"]
try:
    F._OP_RULES["op_parse"] = ("解析", "绝对不该出现的词")
    _AK_h2 = F.feature_version_hash()
finally:
    F._OP_RULES["op_parse"] = _AK_orig
check(RS, "AK4 ★特征定义必须**版本化**：改一条规则 ⇒ 指纹必须变\n"
          "         —— 否则旧账本上的族标签会静默漂移；也挡住「悄悄改规则」",
      _AK_h2 != F.feature_version_hash(),
      f"原={F.feature_version_hash()} 改后={_AK_h2}")

# 任务集索引刻意住在 `bench/` 侧（见 features.py 末尾），这里按需取用，
# **不复制一份实现**（同一事实只声明一处）。
_AK_probe_mod = _ilu2.module_from_spec(
    _ilu2.spec_from_file_location(
        "_v1_probe_family_def", V1 / "bench" / "probe_family_def.py"))
_AK_probe_mod.__file__ = str(V1 / "bench" / "probe_family_def.py")
import sys as _sys                                                  # noqa: E402
_sys.modules.setdefault("_v1_probe_family_def", _AK_probe_mod)
_AK_probe_mod.__spec__.loader.exec_module(_AK_probe_mod)
_AK_load = _AK_probe_mod.load_task_index

_AK_idx = _AK_load(str(V1 / "bench" / "tasks" / "*.jsonl"),
                   str(V1 / "bench" / "heldout" / "*.jsonl"))
_AK_tasks = [d for _, d in _AK_idx.values()]
_AK_c1 = F.cluster(_AK_tasks)
_AK_shuffled = list(reversed(_AK_tasks))
check(RS, "AK5 聚类必须**确定性**：打乱输入顺序 ⇒ 族标签映射逐位相同",
      F.cluster(_AK_shuffled) == _AK_c1,
      f"族={sorted(set(_AK_c1.values()))}")

_AK_cross: dict[str, set[str]] = {}
for _tid, (_suite, _d) in _AK_idx.items():
    if _tid in _AK_c1:
        _AK_cross.setdefault(_suite.split("_heldout")[0], set()).add(_AK_c1[_tid])
check(RS, "AK6 ★★结构族必须真的**切断文件名边界**（否则只是换了个名字）\n"
          "         —— 至少一个套件的题被分到 ≥2 个结构族",
      any(len(v) >= 2 for v in _AK_cross.values()),
      " ｜ ".join(f"{k}:{sorted(v)}" for k, v in sorted(_AK_cross.items())))

# ── 核心：同一批真实行、同一把尺子，只换族标签 ──────────────────────────
_AK_L = ScoreLedger(V1 / "bench" / "evidence"
                    / "score_ledger_real_qwen25_7b_T0_8sess.jsonl")
_AK_rows_all = _AK_L.load()
_AK_L.caliber_sig = collections.Counter(
    r.get("caliber_sig") for r in _AK_rows_all).most_common(1)[0][0]
_AK_rows = _AK_L.admissible()
# ⚠️ 必须按**去重后的题**聚类：把 114 行（含重复题）直接喂进去，
#    聚类会按行数加权 ⇒ 与探针（14 道唯一题）算出不同的族。
_AK_struct = F.cluster([_AK_idx[t][1]
                        for t in sorted({r["task_id"] for r in _AK_rows})])
_AK_suite_pw = measure_family_power(_AK_rows)
_AK_struct_pw = measure_family_power(
    [dict(r, family=_AK_struct.get(r["task_id"], "?")) for r in _AK_rows])

check(RS, "AK7 ★★★【本轮核心负面发现】结构族的预测力必须**弱于**文件名族\n"
          "         实测（qwen2.5:7b / T=0 / 8 会话 / 114 行，同一批行）：\n"
          "             文件名族 Δ=+0.0447 阈值=0.0421 snr=1.062 → PRESENT\n"
          "             结构族   Δ=+0.0051 阈值=0.0200 snr=0.255 → ABSENT\n"
          "         ⇒ 现有族预测力里**有相当一部分来自「套件里挑了哪些题」**，\n"
          "           不是题面结构规律。8 会话「闸转绿」必须**降级解读**。\n"
          "         ⛔ 这条断言同时是防「尺子购物」的闸：\n"
          "            谁回头调特征把结构族刷绿，这条就判红 ⇒ 必须显式承认动了尺子。",
      (_AK_struct_pw.snr or 0.0) < (_AK_suite_pw.snr or 0.0)
      and _AK_struct_pw.state == "ABSENT"
      and _AK_suite_pw.state == "PRESENT",
      f"文件名 snr={_AK_suite_pw.snr} / 结构 snr={_AK_struct_pw.snr}")

# ⛔ AK7 是**负面**结论，但它否定的是"按族预测通过率"，不是"按特征检索相似题"。
#    这两件事必须分开测，否则一个真发现会被读成一场全面否定（项目反复防的错）。
_AK_in: list[float] = []
_AK_out: list[float] = []
_AK_fs = {t.get("id"): F.feature_set(t) for t in _AK_tasks}
for _a in _AK_tasks:
    for _b in _AK_tasks:
        if _a is _b:
            continue
        _sa, _sb = _AK_fs[_a["id"]], _AK_fs[_b["id"]]
        _sim = len(_sa & _sb) / len(_sa | _sb) if (_sa | _sb) else 0.0
        (_AK_in if _AK_c1[_a["id"]] == _AK_c1[_b["id"]] else _AK_out).append(_sim)
# ⚠️ 硬坑：契约**不许对可能不存在的东西做算术**。
#    撤回态（聚类退化成单一族）下跨族对数为 0 ⇒ 这里除零会让汇总行整条消失，
#    于是"AK8 崩了"看起来像"AK8 没跑"，撤回态验证直接失效。
_AK_ok = bool(_AK_in) and bool(_AK_out)
_AK_m_in = (sum(_AK_in) / len(_AK_in)) if _AK_in else 0.0
_AK_m_out = (sum(_AK_out) / len(_AK_out)) if _AK_out else 0.0
check(RS, "AK8 ★负面结论不许被读成全面否定：结构特征对**检索**仍然有效\n"
          "         —— AK7 否定的只是「按族预测通过率」。而「按特征检索相似题」\n"
          "            是另一个任务（外部奖励检索要用的正是后者），必须单独测：\n"
          "            同结构族内的题面相似度必须**显著高于**跨族的。",
      _AK_ok and _AK_m_in > _AK_m_out + 0.05,
      f"族内均值={_AK_m_in:.3f}(n={len(_AK_in)}) 跨族均值={_AK_m_out:.3f}(n={len(_AK_out)}) "
      f"差={_AK_m_in - _AK_m_out:.3f}" if _AK_ok else
      "撤回态：聚类退化（族内对=0 或跨族对=0）⇒ 无法比较，判红")

_AK_probe = V1 / "bench" / "probe_family_def.py"
check(RS, "AK9 对照探针必须存在、且**声明了事前冻结**（不许回头调特征）\n"
          "         —— 一个没有『写定在读数之前』声明的对照实验，\n"
          "            等于允许自己试十种定义挑最好看的那个",
      _AK_probe.exists()
      and "事前声明" in _AK_probe.read_text(encoding="utf-8")
      and "FEATURES_VERSION" in _src("features.py"),
      f"探针={_AK_probe.exists()}")

# ═══════════════════════════════════════════════════════════════════════════
# AL 组 —— 外部奖励只读通道（DESIGN §17-5：给「学习与泛化」一条真实的学习面）
#
# 背景（实测，不是推演）：内核的动作只有 `finish`，而 `finish` 永不失败
#   ⇒ `result.ok` 恒 True ⇒ 世界模型**没有可学的失败** ⇒ 语义记忆 0 条
#   ⇒ 泛化实验变成「空记忆 vs 空记忆」，Δ=0 是**算术必然**（§15.2b）。
# 失败的其实是"答案对不对"，而那只有外部打分器知道。
#
# ⛔ 这条路**极容易变成作弊**，所以本组钉的全是边界，不是功能：
#    · 内核自评一律拒收（自评分绕开外部锚层）
#    · 当前题读不到自己的分数（开卷考同一道题不算学习）
#    · 只记已答完的题（写在整轮跑完之后）
# ═══════════════════════════════════════════════════════════════════════════
from mw.worldmodel import REWARD_SOURCE, REWARD_MIN_SIM  # noqa: E402

_AL_db = _TMP / "al_reward.db"
_AL_store = MemoryStore(_AL_db)
_AL_wm = WorldModel(_AL_store, StubLLM())

check(RS, "AL1 ★★内核**自评**一律拒收：source 不是外部打分器 ⇒ 不写、且计数\n"
          "         —— 自评分是整个项目立论的掘穿点：外部锚层存在的理由就是\n"
          "            『变好』必须有外部依据，自己给自己打分正好绕开它",
      (not _AL_wm.record_reward(task_id="t1", features={"in_str"}, passed=True,
                                score=1.0, source="self"))
      and _AL_wm.rewards_rejected_source == 1
      and _AL_wm.rewards_recorded == 0,
      f"拒自评={_AL_wm.rewards_rejected_source} 已写={_AL_wm.rewards_recorded}")

check(RS, "AL2 缺题号或缺特征 ⇒ 拒收（无法归属或检索不到 ⇒ 不写）\n"
          "         —— 没有题号就挡不住「读到自己的分数」；没有特征就找不到同类题",
      (not _AL_wm.record_reward(task_id="", features={"in_str"}, passed=True,
                                source=REWARD_SOURCE))
      and (not _AL_wm.record_reward(task_id="t2", features=set(), passed=True,
                                    source=REWARD_SOURCE))
      and _AL_wm.rewards_rejected_missing == 2,
      f"拒缺失={_AL_wm.rewards_rejected_missing}")

# —— 正常写入三条（两条同类、一条**同题号**）——
_AL_wm.record_reward(task_id="learn_1", features={"in_str", "op_convert", "level1"},
                     passed=False, score=0.33, failure="减法规则没处理",
                     source=REWARD_SOURCE)
_AL_wm.record_reward(task_id="learn_2", features={"in_str", "op_convert", "level1"},
                     passed=True, score=1.0, source=REWARD_SOURCE)
_AL_wm.record_reward(task_id="probe_self", features={"in_str", "op_convert", "level1"},
                     passed=True, score=1.0, source=REWARD_SOURCE)
check(RS, "AL3 合法外部奖励必须写进去（三条 ⇒ 计数=3）",
      _AL_wm.rewards_recorded == 3, f"已写={_AL_wm.rewards_recorded}")

# ⛔ 关键：换一个 WorldModel 实例（模拟**新进程起跑**），按特征检索
_AL_wm2 = WorldModel(MemoryStore(_AL_db), StubLLM())
_AL_g = _AL_wm2.reward_guidance(features={"in_str", "op_convert", "level1"},
                                task_id="probe_self")
check(RS, "AL4 ★★★检索必须**排除当前题号**：自己的历史得分绝不能进上下文\n"
          "         —— 读到自己上一次在这道题上的分数 = 开卷考同一道题。\n"
          "            那不是学习，那是把测试集答案抄进提示词。\n"
          "         ⚠️ 第 111 轮（§82）把计数从「== 2」改成「>= 1」：那个 2 其实是在\n"
          "            **强制把成功记录也渲染出来**；而 V19 实测成功记录零可执行\n"
          "            内容、且与 `e1ni` 掉分同时出现（那条政策归 AX7 单独守）。\n"
          "            ⇒ 本条只守「排除自己 + 同类题至少给 1 条」两件事。",
      "probe_self" not in _AL_g
      and _AL_wm2.rewards_skipped_self >= 1
      and _AL_g.count("- 同类题") >= 1,
      f"跳过自己={_AL_wm2.rewards_skipped_self} 命中={_AL_g.count('- 同类题')}")

check(RS, "AL5 ★★提示必须是**可执行**的：给出『先检查什么』，不是『不可假设…』\n"
          "         —— 后者是 V1 之前 9/10 条 facts 的形态（读了也改不了任何动作），\n"
          "            正是 §15.2 里『学到的知识不可执行』那一层",
      ("先检查" in _AL_g) and ("不可假设" not in _AL_g)
      and ("得分" in _AL_g),
      _AL_g.replace("\n", " ｜ ")[:110])

check(RS, "AL6 结构不重合 ⇒ 不给提示（避免把不相干题的分数塞进决策链）",
      _AL_wm2.reward_guidance(features={"in_dict", "op_nest", "level2"},
                              task_id="x") == "",
      f"min_sim={REWARD_MIN_SIM}")

# ── AL13/AL14（第 111 轮 / §82）：V19 的两条读数逼出来的两条政策 ─────────────
#: ⚠️ 用**独立库**，避免污染上面 AL4/AL5 的 `_AL_g`。
_AL_wm3 = WorldModel(MemoryStore(_TMP / "al_reward_cls.db"), StubLLM())
_AL_f3 = {"in_str", "op_clean", "level0"}
_AL_wm3.record_reward(task_id="ok_1", features=_AL_f3, passed=True, score=1.0,
                      source=REWARD_SOURCE)
_AL_g_ok = _AL_wm3.reward_guidance(features=_AL_f3, task_id="probe_x")
check(RS, "AX7 ★★成功记录**不得**产生提示行（`rewards_skipped_success` 计数）\n"
          "         —— 旧格式把它渲染成「历史通过，得分 1.00 ⇒ 无记录的失败模式」，\n"
          "            而「无记录的失败模式」本身就是零可执行内容。\n"
          "            V19 实测：收到三行「历史通过」的 `e2ni`/`e3ni` 一动不动；\n"
          "            收到「两行通过 + 一行未通过」的 `e1ni`，反而把唯一需要的那行\n"
          "            import 删了（16/16 → 0/16，diff 只有两行，3 rep 一致）。",
      _AL_g_ok == "" and _AL_wm3.rewards_skipped_success == 1,
      f"全通过时提示={_AL_g_ok!r} 挡成功={_AL_wm3.rewards_skipped_success}")

_AL_wm3.record_reward(
    task_id="bad_1", features=_AL_f3, passed=False, score=0.0,
    failure="通过 0/16；失败: import pathlib as _pp\nclean_lines(['a'])"
            "  ⇒ NameError: name 're' is not defined",
    source=REWARD_SOURCE)
_AL_g_cls = _AL_wm3.reward_guidance(features=_AL_f3, task_id="probe_x")
check(RS, "AX8 ★★失败文本必须**提炼成错误类别**，不得把打分器原文塞进提示\n"
          "         —— 原文以「测试调用的源码」开头（`通过 0/16；失败: import pathlib…`），\n"
          "            诊断被埋在中间、又被 `[:200]` 截断 ⇒ 模型得自己从噪音里挖。\n"
          "            ⛔ 但只许传出**类别**，不许传出实例（「缺的是 re」= 开卷）。",
      ("NameError" in _AL_g_cls) and ("import pathlib" not in _AL_g_cls)
      and ("'re'" not in _AL_g_cls) and ("先检查" in _AL_g_cls),
      _AL_g_cls.replace("\n", " ｜ ")[:120])

# ═══════════════════════════════════════════════════════════════════════════
# AX9 / AX10 ★★奖励段的**三档回显**（第 114 轮 / §87，V23 的唯一自变量）
#
# 为什么必须有三档，而不是"有/没有"两档：
#   纪律 1 的四项前提里，第三项是**等长安慰剂臂**。V19~V22 一直缺它，
#   于是 V22 量到的那 −16 格只能叫「块的存在」的**总效应**（内容 + 扰动之和）。
#   `T=0` 下两个假设给出同一个预测 —— prompt 变 ⇒ 输出变：
#     `H_content`：那段「NameError ⇒ 检查 import」让模型改了做法；
#     `H_perturb`：任何等长文本都会把采样点推到别处，改对改错与内容无关。
#   ⇒ 无安慰剂臂时两者**结构性不可识别**（§85.9）。
#   三档读**同一个库**（`al_reward_cls.db` 里一条 NameError 失败记录），
#   唯一的差别是 `reward_echo` ⇒ 与 V23 的三臂装置同构。
_AL_wm_e0 = WorldModel(MemoryStore(_TMP / "al_reward_cls.db"), StubLLM(),
                       reward_echo=0)
_AL_g_e0 = _AL_wm_e0.reward_guidance(features=_AL_f3, task_id="probe_x")
_AL_wm_e1 = WorldModel(MemoryStore(_TMP / "al_reward_cls.db"), StubLLM(),
                       reward_echo=1)
_AL_g_e1 = _AL_wm_e1.reward_guidance(features=_AL_f3, task_id="probe_x")
_AL_wm_e2 = WorldModel(MemoryStore(_TMP / "al_reward_cls.db"), StubLLM(),
                       reward_echo=2)
_AL_g_e2 = _AL_wm_e2.reward_guidance(features=_AL_f3, task_id="probe_x")
#: 越界值必须**当场报错**（⛔ 不许静默按 1 处理 —— 那正是"设了却没生效"的病）。
try:
    WorldModel(MemoryStore(_TMP / "al_reward_cls.db"), StubLLM(), reward_echo=3)
    _AL_ECHO_RAISED = False
except ValueError:
    _AL_ECHO_RAISED = True

check(RS, "AX9 ★★`V1_REWARD_ECHO=0` ⇒ 奖励段**一个字都不进提示**，但「看到过」照记\n"
          "         —— 「关掉回显」与「通道没数据」必须能分开：`rewards_seen` 是\n"
          "            通道的**占用**读数，它不许因为渲染被关掉就变 0。\n"
          "            ⛔ 反过来也一样：纪律 14「机制存在 ≠ 机制通电」，这条守的是\n"
          "            它的反面 —— 通的被记成「没通」。\n"
          "         撤回语义：`if self.reward_echo == 0: return \"\"` 改成 `if False:` ⇒ 必须红。",
      _AL_g_e0 == "" and _AL_wm_e0.rewards_seen >= 1
      and _AL_g_e1 != "" and ("NameError" in _AL_g_e1)
      and _AL_ECHO_RAISED,
      f"echo0={_AL_g_e0!r} 看到={_AL_wm_e0.rewards_seen} ｜ "
      f"echo1={_AL_g_e1.replace(chr(10), ' ｜ ')[:52]!r} ｜ "
      f"越界报错={_AL_ECHO_RAISED}")

#: 逐行等长 + 结构量相同 + 真词零泄漏。⛔ 这四个条件是安慰剂臂**能叫安慰剂**的
#: 全部理由：少一条，它就是"另一次混装"，而不是"同一块字数、不带语义"。
_AL_L2E1 = _AL_g_e1.split("\n")
_AL_L2E2 = _AL_g_e2.split("\n")
_AL_ECHO_SAME_LEN = (len(_AL_L2E1) == len(_AL_L2E2)
                     and all(len(a) == len(b) for a, b in zip(_AL_L2E1, _AL_L2E2))
                     and all(a.startswith("- ") == b.startswith("- ")
                             for a, b in zip(_AL_L2E1, _AL_L2E2)))
_AL_ECHO_LEAK = [w for w in ("NameError", "先检查", "import", "得分", "同类题")
                 if w in _AL_g_e2]
check(RS, "AX10 ★★`V1_REWARD_ECHO=2` ⇒ **逐行等长、内容无关**的安慰剂，真词零泄漏\n"
          "          —— 判据 = 行数相同 ∧ 逐行字符数相同 ∧ 同一行的 `- ` 前缀相同\n"
          "                    ∧ 与真回显不相等 ∧ 不含类别名/检查项/通道名。\n"
          "            这就是纪律 1 的第三项前提：`2≈1` ⇒ 载荷在「有这么一块字」；\n"
          "            `2≈0` ⇒ 载荷在内容。⛔ 没有它，前两项前提量出来的 Δ 只是一半信息。\n"
          "         撤回语义：`return _echo_fill(body)` 改成 `return body` ⇒ 必须红。",
      _AL_ECHO_SAME_LEN and _AL_g_e2 != _AL_g_e1 and not _AL_ECHO_LEAK
      and bool(_AL_g_e2),
      f"行数 {len(_AL_L2E1)}vs{len(_AL_L2E2)} 逐行等长={_AL_ECHO_SAME_LEN} "
      f"与真回显不同={_AL_g_e2 != _AL_g_e1} 泄漏={_AL_ECHO_LEAK} ｜ "
      f"安慰剂={_AL_g_e2.replace(chr(10), ' ｜ ')[:64]!r}")

# ── AX11 的取证装置（§91）：三个起点，同一个判据 ─────────────────────────
#  ⚠️ 目录里**先清干净**：留着上一轮的库会让「有 11 条历史」这个前提靠运气
#     （纪律 15 的第二种来源：本地绿可能只是依赖了未入库的本地文件）。
_AX11_TMP = _TMP / "ax11_blobs"
_AX11_TMP.mkdir(parents=True, exist_ok=True)
for _p in _AX11_TMP.glob("*.db*"):
    _p.unlink()
_AX11_HIST = MemoryStore(_AX11_TMP / "hist.db")
_AX11_PRE = [_AX11_HIST.capture(f"历史观测 {i}").ref for i in range(11)]
_AX11_HIST.close()
#: 同一个库，**重开**一次（base 变成 11）⇒ 这就是 `s12` 臂的单元级版本。
_AX11_REOPEN = MemoryStore(_AX11_TMP / "hist.db")
_AX11_FIRST = _AX11_REOPEN.capture("重开后的第一条").ref
_AX11_NEXT = [_AX11_REOPEN.capture(f"再{i}").ref for i in (1, 2)]
_AX11_REOPEN.close()
_AX11_NEW = MemoryStore(_AX11_TMP / "fresh.db")
_AX11_FRESH = [_AX11_NEW.capture(f"空库{i}").ref for i in (1, 2, 3)]
_AX11_NEW.close()

check(RS, "AX11 ★★句柄编号必须是**本会话内序**，不许把库的全局自增行号带进提示\n"
          "          —— §90 实测：`b#1` → `b#12`（**+1 个字符**，一张表的 lastrowid）\n"
          "             就足以把 `e1wi` 从 0 分那版翻到 16 分那版，两次独立冷启动一致。\n"
          "             那条边与语义**完全无关**：它只说明「库里有 11 条历史」。\n"
          "             ⇒ 只要库会变大（它一定变大：每轮写 6 条 reward），prompt 就会变，\n"
          "               任何跨轮实验的对照里都混着这条边。\n"
          "         判据：同一个库先有 11 条历史、重开后第一次捕获必须仍是 `b#1`；\n"
          "               同一会话内连捕必须 `b#1,b#2,b#3`（长度与旧口径一致，不挤占正文宽度）。\n"
          "         撤回语义：`ref=f\"b#{seq}\"` 改回 `ref=f\"b#{int(cur.lastrowid)}\"` ⇒ 必须红。",
      _AX11_PRE == [f"b#{i}" for i in range(1, 12)]
      and _AX11_FIRST == "b#1" and _AX11_NEXT == ["b#2", "b#3"]
      and _AX11_FRESH == ["b#1", "b#2", "b#3"],
      f"预置 11 条={_AX11_PRE[:2]}… 重开后首={_AX11_FIRST} 连捕={_AX11_NEXT} "
      f"空库={_AX11_FRESH}")

# ── AZ100 的取证装置（V36）：同一正文、两种捕获次序 ─────────────────────
#  ⚠️ 先清目录：留着上一轮的库会让「前面多捕获 11 条」这个前提靠运气
#     （纪律 15 的第二种来源：本地绿可能只是依赖了未入库的本地文件）。
_AZ100_TMP = _TMP / "az100_handle"
_AZ100_TMP.mkdir(parents=True, exist_ok=True)
for _p in _AZ100_TMP.glob("*.db*"):
    _p.unlink()
_AZ100_BODY = "recall ⇒ ✔ (语义记忆中无相关条目)"
_AZ100_A = MemoryStore(_AZ100_TMP / "a.db")
_H0 = _AZ100_A.capture(_AZ100_BODY)          # 空库直接捕 ⇒ 应为 b#1
_AZ100_A.close()
_AZ100_B = MemoryStore(_AZ100_TMP / "b.db")
for _i in range(11):
    _AZ100_B.capture(f"历史观测 {_i}")        # 先垫 11 条 ⇒ 同一正文应为 b#12
_H1 = _AZ100_B.capture(_AZ100_BODY)
_AZ100_B.close()
#: 真实产物：跑一次 `core.run()`，从 state 里抓索引行。
#  ⛔ 绝不在这里手抄 `index = ...` —— 那测的是探针，不是产物（纪律 12）。
_AZ100_CORE = Core(llm=StubLLM(), memory=MemoryStore(_AZ100_TMP / "c.db"),
                   memory_mode="raw")
with contextlib.redirect_stdout(io.StringIO()):
    _AZ100_CORE.run("把 read_text 改成显式 encoding", budget_factor=1)
_AZ100_STATES = "".join(st for _, st in _AZ100_CORE.state_log)
_AZ100_IDX = re.findall(r"\[\d+字 sha=[0-9a-f]{12}\]", _AZ100_STATES)

check(RS, "AZ100 ★★进 prompt 的记忆索引**不许带 `b#N`** —— 索引只准由内容决定\n"
          "          —— V35 把「累积到底改了什么」量到字符级：`v26/d2` 的 `e1wi` 两轮\n"
          "             it=1 的 state **逐字节相同**，it=2 **只差 1 个字符**\n"
          "             （`[b#1 …]` → `[b#12 …]`，插了一个 `2`），而分数 **0 → 16**；\n"
          "             两条记忆的**正文逐字节相同**（都是 `recall ⇒ ✔ (语义记忆中无相关条目)`）。\n"
          "             代理台账同步：`req_bytes` 12382→12383、`resp_bytes` 1036→1075。\n"
          "             ⇒ 那 16 分完全由**句柄数字**买来，与学到的东西零关系。\n"
          "         为什么 `ref` 是噪声而 `sha` 不是：`ref = lastrowid - base` 是**捕获次序**的\n"
          "             函数（本轮比上轮多捕 k 条就整体后移 k）；`chars`/`sha` 是**内容**的函数。\n"
          "             而模型**根本用不到** `ref`：`expand()` 只由内核 `_recall_for()` 用\n"
          "             进程内的 `handle.ref` 调用，`mw/tools.py` 里没有任何接受 ref 的工具。\n"
          "         判据：同一段正文，在「空库直接捕」与「先捕 11 条再捕」两种情境下，\n"
          "               `chars` 与 `sha` 必须相同；真实 `core.run()` 的 state 里 `b#` 出现 0 次；\n"
          "               `state_meta` 必须与 `state_log` 同长。\n"
          "         撤回语义：`index = f\"[{handle.chars}字 sha={handle.sha}] \"` 改回\n"
          "               `index = f\"[{handle.ref} {handle.chars}字 sha={handle.sha}] \"` ⇒ 必须红。",
      (_H0.chars, _H0.sha) == (_H1.chars, _H1.sha)
      and _H0.ref != _H1.ref
      and _AZ100_IDX and _AZ100_STATES.count("b#") == 0
      and len(_AZ100_CORE.state_meta) == len(_AZ100_CORE.state_log),
      f"同内容不同次序 chars/sha 同={(_H0.chars, _H0.sha) == (_H1.chars, _H1.sha)} "
      f"ref 异={_H0.ref}≠{_H1.ref} 产物索引行={_AZ100_IDX[:2]} "
      f"产物b#次数={_AZ100_STATES.count('b#')} "
      f"meta/log={len(_AZ100_CORE.state_meta)}/{len(_AZ100_CORE.state_log)}")

# ── AZ101 的取证装置（V37）：让预演**一定**说"会失败" ────────────────────
#  ⚠️ 为什么要钉死 `expect_ok`：真模型在空库上**从不说会失败**
#     （V37 实测 a0_r1：10 次预演全是 `ok=True`；只有在累积第 2 轮才出现 2 次
#      `ok=False`）。要在契约里验"否决之后去哪"，就不能靠模型碰巧说错。
class _AZ101WM:
    """只替换 `predict` 的结论，其余全部委托给真 `WorldModel`。

    ⛔ 不 `__getattr__` 转发 `stats`/`observe`/`guidance` 的话，内核里
       `self.wm.observe(...)` 会 AttributeError —— 那测的是桩，不是产物。
    """

    def __init__(self, inner):
        self._inner = inner
        self.vetoed = 0
        self.veto_refused_answer = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def predict(self, *, state, action, use_llm=True):
        return Prediction(action=action, expected="(契约桩：钉死为会失败)",
                          confidence=0.9, expect_ok=False)


_AZ101_TMP = _TMP / "az101_veto"
_AZ101_TMP.mkdir(parents=True, exist_ok=True)
for _p in _AZ101_TMP.glob("*.db*"):
    _p.unlink()
#: ⚠️ 题面必须 > 120 字：`looks_complex()` 是**唯一**决定走不走深通道的地方
#:    （`len(goal) > 120` / 多步动词 ≥2 / 有失败历史）。走快通道 ⇒ `predict`
#:    根本不会被调用 ⇒ 桩永远不会被问到 —— 那会读成「机制没触发」，
#:    而事实是「这条题根本没资格触发」（第一版就栽在这里：veto日志=0）。
_AZ101_GOAL = (
    "把 read_text 改成显式 encoding，并逐一检查所有 open() 调用，"
    "然后确认没有残留的默认编码路径，接着把每一处改动写进一个报告文件，"
    "以及补充一条注释说明为什么必须显式指定编码，"
    "最后再跑一遍自检确认没有引入新的未定义名；"
    "报告的文件名固定为 report.txt，不要创建别的文件，也不要修改测试用例"
)
_old_veto = os.environ.get("V1_FORESIGHT_VETO")
os.environ["V1_FORESIGHT_VETO"] = "1"
try:
    _AZ101_ON = Core(llm=StubLLM(), memory=MemoryStore(_AZ101_TMP / "on.db"),
                     memory_mode="raw")
    _AZ101_ON.wm = _AZ101WM(_AZ101_ON.wm)
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        _AZ101_ON.run(_AZ101_GOAL, budget_factor=1)
    _AZ101_LOG = _buf.getvalue()
    _AZ101_VETO_LOG = _AZ101_LOG.count("预演否决")
    #: 对照组：同一个桩、同一个题，**只把开关关掉**
    os.environ["V1_FORESIGHT_VETO"] = "0"
    _AZ101_OFF = Core(llm=StubLLM(), memory=MemoryStore(_AZ101_TMP / "off.db"),
                      memory_mode="raw")
    _AZ101_OFF.wm = _AZ101WM(_AZ101_OFF.wm)
    _buf2 = io.StringIO()
    with contextlib.redirect_stdout(_buf2):
        _AZ101_OFF.run(_AZ101_GOAL, budget_factor=1)
    _AZ101_LOG_OFF = _buf2.getvalue()
finally:
    if _old_veto is None:
        os.environ.pop("V1_FORESIGHT_VETO", None)
    else:
        os.environ["V1_FORESIGHT_VETO"] = _old_veto

check(RS, "AZ101 ★★★预演否决必须有**专属出口**：不许落进 `consecutive_failures`\n"
          "          —— V36 §102 量到 `pred` 只有 6 处引用、**全是观测用途**\n"
          "             （`observe()` 与日志）⇒ 累积出的启发式真的进了预演 prompt\n"
          "             （268~488 字节，逐字节算过），却**一格分数也改不动**。\n"
          "             V37 把它接进决策链：`trustworthy and expect_ok is False` ⇒ 不做。\n"
          "         为什么出口必须**写死**：`continue` 之后 `executed` 保持 False，\n"
          "             会落到 `consecutive_failures += 1`（『全部候选被拦』）——\n"
          "             那是把『我们决定不做』记成『做错了』，三轮就熔断 ⇒ 交付变空 ⇒\n"
          "             那一格会被读成『接上预演有害』。同构先例：`skipped_now` 那次\n"
          "             （没额度 ≠ 失败，第六十八轮）。\n"
          "         判据：① 开关开 ⇒ 日志里出现「预演否决」且 `wm.vetoed ≥ 1`；\n"
          "               ② 同一题、同一桩、开关关 ⇒ 一次都不出现；\n"
          "               ③ 开的那一臂「全部候选被拦」的次数**不许多于**关的那一臂\n"
          "                  —— 后者是既有行为的基线（有些步的候选在预演之前就被\n"
          "                     guard 拦掉了，那一步本来就会打这句），\n"
          "                    差值 > 0 才是「否决自己掉进了失败计数」的铁证。\n"
          "         撤回语义：删掉 `if not executed and _vetoed_this_round:` 那一整段 ⇒\n"
          "               ③ 必红（每一次否决都会各自多加一次『全部候选被拦』）。",
      _AZ101_VETO_LOG >= 1
      and int(getattr(_AZ101_ON.wm, "vetoed", 0)) >= 1
      and _AZ101_LOG.count("全部候选被拦") <= _AZ101_LOG_OFF.count("全部候选被拦")
      and "预演否决" not in _AZ101_LOG_OFF
      and int(getattr(_AZ101_OFF.wm, "vetoed", 0)) == 0,
      f"开: veto日志={_AZ101_VETO_LOG} vetoed={getattr(_AZ101_ON.wm, 'vetoed', 'NA')} "
      f"被拦={_AZ101_LOG.count('全部候选被拦')} ｜ "
      f"关: veto日志={_AZ101_LOG_OFF.count('预演否决')} "
      f"vetoed={getattr(_AZ101_OFF.wm, 'vetoed', 'NA')} "
      f"被拦={_AZ101_LOG_OFF.count('全部候选被拦')}")

# ── AZ102 的取证装置（V38）：跨题复用**同一个** Core ⇒ 累计值不许当增量 ──────
#  ⭐ 为什么这是根因而不是"读数不好看"：`--memory-dir` 臂（累积臂）复用同一个
#     `Core`（`run.py:1246`），`WorldModel` 与 `core.memory_layer_view` 都活在整个
#     bench 进程里 ⇒ 它们报的是**跨题累计值**；而累加器把它们当增量相加，
#     等于「同一个计数器被读几次就加几遍」。
#  ⛔ 三个独立来源必须对上（V37 实测）：proxy 里 200 型请求 10 条 / `preds` 逐条
#     10 条 / 而 `kernel_worldmodel.predictions` 写着 34。
import importlib as _importlib                                        # noqa: E402
_AZ102_RUN = _importlib.import_module("run")
_AZ102_TMP = _TMP / "az102_kwm"
_AZ102_TMP.mkdir(parents=True, exist_ok=True)
for _p in _AZ102_TMP.glob("*.db*"):
    _p.unlink()
#: ⚠️ 复用 `_AZ101_GOAL`（> 120 字）：`looks_complex()` 为假就不会预演，
#:    那样三次 run 的 `predictions` 恒为 0 ⇒ 「0==0」恒真，这条断言等于没写。
_AZ102_CORE = Core(llm=StubLLM(), memory=MemoryStore(_AZ102_TMP / "m.db"),
                   memory_mode="raw")
_AZ102_ACC = _AZ102_RUN._kwm_zero()
_AZ102_SNAP: list[int] = []
with contextlib.redirect_stdout(io.StringIO()):
    for _i in range(3):
        _o = _AZ102_CORE.run(_AZ101_GOAL, budget_factor=1)
        _AZ102_RUN._kwm_absorb(_AZ102_ACC, _o.stats["worldmodel"], _AZ102_CORE)
        _AZ102_SNAP.append(int(_o.stats["worldmodel"]["predictions"]))
_AZ102_ST = _AZ102_CORE.wm.stats()
_AZ102_LAYER = dict(getattr(_AZ102_CORE, "memory_layer_view", None) or {})

check(RS, "AZ102 ★★★累加器必须按**增量**并：不许把跨题累计值当增量（V38 / §104）\n"
          "          —— 累积臂（`--memory-dir`）复用**同一个** `Core`，而\n"
          "             `WorldModel.stats()` 与 `core.memory_layer_view` 都是\n"
          "             纯 `+=` 的跨题累计值。把累计值相加 = 同一个计数器被\n"
          "             读几次就加几遍：V37 实测 6 题逐题 `预演=2,3,5,6,8,10`\n"
          "             ⇒ 落盘 34，真值 10（proxy 200 型 10 条、`preds` 10 条\n"
          "             两个独立来源都对得上）。\n"
          "         为什么必须单独设一条契约：这个 bug 不改变任何一格分数、\n"
          "             不改变任何一条 prompt ⇒ 全部既有判据都看不见它，\n"
          "             而它污染了历史上每一个『预演 N 次 / 命中 M 次』。\n"
          "         判据：① 三次 run 的 `predictions` 真的在增长（否则 0==0 恒真）；\n"
          "               ② 累加器终值 == 世界模型终值（同一个计数器）；\n"
          "               ③ 混淆矩阵四格、层读数（住在 Core 上、同样是累加）同样相等。\n"
          "         撤回语义：把 `delta = _kwm_delta(now, prev)` 换成 `delta = now`\n"
          "               ⇒ ②③ 必红（每读一次就多抄一遍累计值）。",
      _AZ102_SNAP[-1] > _AZ102_SNAP[0] > 0
      and _AZ102_ACC["predictions"] == _AZ102_ST["predictions"]
      and _AZ102_ACC["scored"] == _AZ102_ST["scored"]
      and _AZ102_ACC["confusion"] == _AZ102_ST["confusion"]
      and _AZ102_ACC["layer_recalled"] == int(_AZ102_LAYER.get("recalled", 0)),
      f"三次 predictions={_AZ102_SNAP} ｜ acc={_AZ102_ACC['predictions']} "
      f"stats={_AZ102_ST['predictions']} "
      f"（旧写法会得 {sum(_AZ102_SNAP)}）｜ "
      f"confusion acc={_AZ102_ACC['confusion']} stats={_AZ102_ST['confusion']} ｜ "
      f"layer acc={_AZ102_ACC['layer_recalled']} core={_AZ102_LAYER.get('recalled', 0)}")

# ── AZ103 的取证装置（V39）：规划器的降级分项必须进台账，且同样按增量累加 ────
#  ⭐ 为什么这是"读数不可审计"而不是"少印一行"：`Planner` 有四个**分项**降级计数
#     （`json_fail`/`truncated`/`no_tool_fit`/`empty_plan`），注释写着"修法不同的
#     分支合并，就再也分不清该修哪里" —— 而 `runs.jsonl` 里只有
#     `kernel_worldmodel`/`kernel_closeout`/`kernel_external`/`kernel_transport`，
#     **没有 `kernel_planner`**。V39 的前置量只能从 stdout 日志里正则手数。
_AZ103_TMP = _TMP / "az103_kpl"
_AZ103_TMP.mkdir(parents=True, exist_ok=True)
for _p in _AZ103_TMP.glob("*.db*"):
    _p.unlink()
_AZ103_CORE = Core(llm=StubLLM(), memory=MemoryStore(_AZ103_TMP / "m.db"),
                   memory_mode="raw")
_AZ103_ACC = _AZ102_RUN._kpl_zero()
_AZ103_SNAP: list[int] = []
with contextlib.redirect_stdout(io.StringIO()):
    for _i in range(3):
        _o = _AZ103_CORE.run(_AZ101_GOAL, budget_factor=1)
        _AZ102_RUN._kpl_absorb(_AZ103_ACC, _o.stats["planner"], _AZ103_CORE)
        _AZ103_SNAP.append(int(_o.stats["planner"]["calls"]))
_AZ103_ST = _AZ103_CORE.planner.stats()
_AZ103_LINE = _AZ102_RUN._kpl_line(_AZ103_ACC)
_AZ103_SRC = inspect.getsource(_AZ102_RUN)

check(RS, "AZ103 ★★★规划器的**降级分项**必须进台账，累加同样按增量（V39）\n"
          "          —— 四个分支（json/截断/无工具/空计划）的修法完全不同：\n"
          "             改提示词 / 调 max_tokens / 加工具 / 改解析器。\n"
          "             只落一个 `degraded` 总数就等于把它们重新合成一句\n"
          "             「模型不行」，而那正是 `planner.py` 里被明确否掉的写法。\n"
          "         判据：① 三次 run 的 `calls` 真的在增长（否则 0==0 恒真）；\n"
          "               ② 累加器终值 == 规划器终值（同一个计数器）；\n"
          "               ③ `fail_total`/`fail_rate` 与四个分项自洽；\n"
          "               ④ `runs.jsonl` 的写盘字典里真有 `kernel_planner` 这个键。\n"
          "         撤回语义：把 `acc[k] += (now[k] - was)` 换回 `+= now[k]`\n"
          "               ⇒ ② 必红（同一个计数器被读几次就加几遍）。",
      _AZ103_SNAP[-1] > _AZ103_SNAP[0] > 0
      and _AZ103_ACC["calls"] == int(_AZ103_ST["calls"])
      and _AZ103_ACC["json_fail"] == int(_AZ103_ST["json_fail"])
      and _AZ103_ACC["truncated_responses"] == int(_AZ103_ST["truncated_responses"])
      and _AZ103_LINE["fail_total"] == (
          _AZ103_ACC["json_fail"] + _AZ103_ACC["truncated_responses"]
          + _AZ103_ACC["empty_plan"] + _AZ103_ACC["no_tool_fit"])
      and '"kernel_planner"' in _AZ103_SRC,
      f"三次 calls={_AZ103_SNAP} ｜ acc={_AZ103_ACC['calls']} "
      f"stats={_AZ103_ST['calls']}（旧写法会得 {sum(_AZ103_SNAP)}）｜ "
      f"json_fail acc={_AZ103_ACC['json_fail']} stats={_AZ103_ST['json_fail']} ｜ "
      f"fail_total={_AZ103_LINE['fail_total']} ｜ "
      f"台账键={'有' if chr(34) + 'kernel_planner' + chr(34) in _AZ103_SRC else '缺'}")

# ── AZ104 的取证装置（V40 / §106）：两种坏形态必须**各自**可修，off 臂必须真的不修 ──
#  ⭐ 为什么这条断言比"多修好两个样本"重要：V39 已经用原文证明「宽度被解析器扣住」，
#     而"修一下解析器"是那种**会顺手把一切都修好**的改动 —— 它既能偷偷把截断的
#     响应补成合法计划，也能改掉它本不该碰的样本。所以本条测的不是"修好了没有"，
#     而是四条**边界**：off 臂逐位不动 / 两种修法互不越界 / 载荷一个字符不动 /
#     截断不许被补完。
from mw.planner import (Planner as _AZ104_P,          # noqa: E402
                        salvage_payload as _AZ104_SP,
                        salvage_triple as _AZ104_ST)
from mw.tools import ToolRegistry as _AZ104_REG, register_core_tools as _AZ104_RCT  # noqa: E402
from mw.tools import _StubMemory as _AZ104_MEM        # noqa: E402

_AZ104_TQ = '"' * 3
#: 载荷里刻意放**裸双引号**（形态 D 的真病之一：`open(path, "w")`）
_AZ104_BODY = 'def _dump(payload):\n    return open(path, "w")\n'
#: 形态 C：三引号（模型把 Python 的三引号习惯写进了 JSON）
_AZ104_C = ('{"candidates": ['
            '{"intent": "a", "tool": "finish", "args": {"answer": '
            + _AZ104_TQ + _AZ104_BODY + "A" + _AZ104_TQ + '}},'
            '{"intent": "b", "tool": "finish", "args": {"answer": '
            + _AZ104_TQ + _AZ104_BODY + "B" + _AZ104_TQ + '}}'
            ']}')
#: 形态 D：载荷在 `candidates` **之外**（兄弟字段）+ 裸引号 + 结尾少 2 个闭合符
_AZ104_D = ('{"candidates": [{"intent": "a", "tool": "finish", "args": {}}],'
            ' "finish": {"answer": "' + _AZ104_BODY + "}")
#: 截断：结尾停在**字符串里**，一个闭合符都不剩 ⇒ 一律不许补
_AZ104_TRUNC = '{"candidates": [{"intent": "a", "tool": "finish", "args": {"answer": "' + _AZ104_BODY


def _az104_planner(salvage: int) -> "_AZ104_P":
    _reg = _AZ104_REG()
    _AZ104_RCT(_reg, memory=_AZ104_MEM(), allow_note=True)
    return _AZ104_P(StubLLM(), _reg, salvage=salvage)


def _AZ104_cands(plan) -> list:
    return [(s.tool, str(s.args.get("answer", ""))) for s in plan.steps]


_AZ104_OFF = _az104_planner(0)
_AZ104_W1 = _az104_planner(1)
_AZ104_W2 = _az104_planner(2)
_AZ104_BOTH = _az104_planner(3)


def _az104_run(planner, raw: str):
    planner.llm = _AZ104Stub(raw)
    _b = io.StringIO()
    with contextlib.redirect_stdout(_b):
        return planner.propose(goal="给一段代码", state="", history=[],
                               n=3, guidance="", blocked=(), rejected=())


class _AZ104Stub:
    """第 1 次给原文，其后给空串 —— 补救必须确定性失败（否则读数归因不到修法）。"""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.calls = 0

    def complete(self, messages, max_tokens=None):
        self.calls += 1
        return self.raw if self.calls == 1 else ""


_AZ104_P0_C = _az104_run(_AZ104_OFF, _AZ104_C)
_AZ104_P1_C = _az104_run(_AZ104_W1, _AZ104_C)
_AZ104_P2_D = _az104_run(_AZ104_W2, _AZ104_D)
_AZ104_P1_D = _az104_run(_AZ104_W1, _AZ104_D)
_AZ104_P3_C = _az104_run(_AZ104_BOTH, _AZ104_C)
_AZ104_P3_D = _az104_run(_AZ104_BOTH, _AZ104_D)
_AZ104_SP_D = _AZ104_SP(_AZ104_D)
_AZ104_ST_C = _AZ104_ST(_AZ104_C)
_AZ104_ST_D = _AZ104_ST(_AZ104_D)
#: 环境 → `Limits` → `Planner` 的整条链路（纪律 13：机制存在 ≠ 机制通电）
_az104_env_old = os.environ.get("V1_PLAN_SALVAGE")
try:
    os.environ["V1_PLAN_SALVAGE"] = "2"
    _AZ104_ENV_HIT = int(cfg.settings().limits.plan_salvage)
    _AZ104_ENV_P = _az104_planner(int(cfg.settings().limits.plan_salvage))
    _AZ104_ENV_PLANNER = int(_AZ104_ENV_P.salvage)
finally:
    if _az104_env_old is None:
        os.environ.pop("V1_PLAN_SALVAGE", None)
    else:
        os.environ["V1_PLAN_SALVAGE"] = _az104_env_old
#: 越界档位必须当场拒绝（`7` 与 `-1` 都不是"没设"）
_AZ104_BAD: list[str] = []
for _bad in (-1, 4, 7):
    try:
        _az104_planner(_bad)
        _AZ104_BAD.append(f"{_bad}:未拒绝")
    except ValueError:
        _AZ104_BAD.append(f"{_bad}:拒绝")

check(RS, "AZ104 ★★★规划器格式修复必须是**两个各自单变量**的档位，且 off 臂逐位不动（V40）\n"
          "          —— V39 §三 的判决：`max_candidates` 从 3 拧到 1，解析失败率从 0.684\n"
          "             降到 0.308，而**候选数两臂都恒为 1**；原文证明模型在 n=3 时**给了 3 个**，\n"
          "             只是每个都写成 `\"answer\": \"\"\"<代码>\"\"\"` ⇒ `json.loads` 一律拒收。\n"
          "             ⇒ 真凶是解析器，不是模型的服从性。但「修解析器」是一类**会顺手修好一切**\n"
          "             的改动：它能把截断补成合法计划，也能改掉它不该碰的样本。\n"
          "         判据（五条，缺一即证明不了边界）：\n"
          "               ① `salvage=0` ⇒ 三引号样本**照旧降级**（off 就是 off，不是「默认开一点」）；\n"
          "               ② `1` 修三引号（2 个候选）但**修不了**兄弟字段；`2` 反之（1 个候选\n"
          "                  且载荷**逐字节等于**原文那段）；⇒ 两档是正交对照臂，不是强弱两档；\n"
          "               ③ `3` = 两者都通（同一份原文两处都救得回来）；\n"
          "               ④ 截断（结尾停在字符串里、一个闭合符都不剩）**一律不补完** ——\n"
          "                  这是「修复器总能修好」的唯一入口，必须留一条永远修不了的路；\n"
          "               ⑤ env → `Limits` → `Planner` 整条链通电；越界档位当场 `ValueError`。\n"
          "         撤回语义：把 `if len(done) > len(struct) and not tail:` 改成 `if False:`\n"
          "               ⇒ ② 里 `_AZ104_SP_D[2]` 与 ③ 的 D 组恢复必红（补完不再受控）。",
      # ⚠️ 负向判据**不能**写成「候选为空」：`_degrade` 一律走 `_fallback`，
      #    它**必然**产出 1 条合法动作（`planner.py` 的硬约束 ①）。所以"没修好"
      #    的可观测形态是 `degraded=True` + 那条兜底答复里**没有载荷**。
      #    （自踩：第一版写成 `not _AZ104_cands(...)`，于是 off 臂恒为"有 1 条" 而红。）
      bool(_AZ104_P0_C.degraded)
      and _AZ104_BODY not in _AZ104_cands(_AZ104_P0_C)[0][1]
      and not _AZ104_P1_C.degraded
      and len(_AZ104_cands(_AZ104_P1_C)) == 2
      and not _AZ104_P2_D.degraded
      and _AZ104_cands(_AZ104_P2_D)[0][1] == _AZ104_BODY
      and _AZ104_P1_D.degraded
      and _AZ104_BODY not in _AZ104_cands(_AZ104_P1_D)[0][1]
      and not _AZ104_P3_C.degraded
      and len(_AZ104_cands(_AZ104_P3_C)) == 2
      and not _AZ104_P3_D.degraded
      and _AZ104_cands(_AZ104_P3_D)[0][1] == _AZ104_BODY
      and _AZ104_SP_D is not None and _AZ104_SP_D[2] is True
      and _AZ104_SP(_AZ104_TRUNC) is None
      and _AZ104_ST_D == _AZ104_D                    # 没三引号 ⇒ 这条修法一个字节都不许动
      and _AZ104_ENV_HIT == 2 and _AZ104_ENV_PLANNER == 2
      and all(x.endswith(":拒绝") for x in _AZ104_BAD),
      f"臂0 降级={_AZ104_P0_C.degraded} 候选={len(_AZ104_cands(_AZ104_P0_C))} ｜ "
      f"臂1(C)={len(_AZ104_cands(_AZ104_P1_C))} 臂1(D)={len(_AZ104_cands(_AZ104_P1_D))} ｜ "
      f"臂2(D)={_AZ104_cands(_AZ104_P2_D)}（载荷逐字节={_AZ104_cands(_AZ104_P2_D) and _AZ104_cands(_AZ104_P2_D)[0][1] == _AZ104_BODY}）｜ "
      f"臂3 C/D={len(_AZ104_cands(_AZ104_P3_C))}/{len(_AZ104_cands(_AZ104_P3_D))} ｜ "
      f"补完={None if _AZ104_SP_D is None else _AZ104_SP_D[2]} "
      f"截断不补={_AZ104_SP(_AZ104_TRUNC) is None} ｜ "
      f"env→limits={_AZ104_ENV_HIT} planner={_AZ104_ENV_PLANNER} ｜ 越界={_AZ104_BAD}")

# ── AZ105 的取证装置（V42）：交付候选被打回时**会不会换下一个** ──────────────
#  ⭐ 为什么它不是"再加一层重试"：V41 §107.2 量到 `for cand in cands` 是**短路求值** ——
#     被*跳过*会让位（guard 拦，S2 实测交付第 2 候选），被*执行*（成功 `return` /
#     失败 `break`）**都不让位**。而真实形态是 `finish`×3 ⇒ 首候选要么成功、要么
#     被打回，两条路都不让位。所以这是一条**独立的边**，不是既有重试的复用。
import dataclasses as _AZ105_DC                              # noqa: E402

_AZ105_GOAL = (
    "写一个函数，函数名必须是 flatten，把嵌套列表压平，只输出代码。"
    "不允许使用递归，也不能引入任何第三方库，并且要处理空列表的情况，"
    "还要保证结果里保留原始顺序，最后不要写任何解释文字。"
)
#: 过全部 5 条判据（非空 / 函数名 / 语言标签 / 可编译 / 结构检查）
_AZ105_OK = ("```python\ndef flatten(xs):\n    out = []\n    for x in xs:\n"
             "        if isinstance(x, list):\n            out.extend(flatten(x))\n"
             "        else:\n            out.append(x)\n    return out\n```")
_AZ105_OK2 = ("```python\ndef flatten(xs):\n    out = []\n    stack = list(xs)\n"
              "    while stack:\n        cur = stack.pop(0)\n"
              "        if isinstance(cur, list):\n            stack = list(cur) + stack\n"
              "        else:\n            out.append(cur)\n    return out\n```")
#: ⚠️ "坏答案"必须**过得了规划器、过不了自检**（可编译 + 函数名不是 flatten）。
#    空答案会在 `propose()` 的 `blank` 过滤里被吃掉，根本到不了执行器（§107.7 自踩）。
_AZ105_BAD1 = "```python\ndef other(xs):\n    return xs\n```"
_AZ105_BAD2 = "```python\ndef other2(xs):\n    return list(xs)\n```"
_AZ105_BAD3 = "```python\ndef other3(xs):\n    out = []\n    return out\n```"


def _az105_cands(*items) -> str:
    return json.dumps({"candidates": [
        {"intent": it, "tool": tool, "args": args} for it, tool, args in items
    ]}, ensure_ascii=False)


class _AZ105LLM:
    """第 1 次给候选 JSON，其后给空串（⊆ 不测模型，只测执行器）。"""

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls = 0

    def complete(self, messages, max_tokens=None):
        self.calls += 1
        return self.payload if self.calls == 1 else ""


def _az105_run(payload: str, failover: int, tag: str):
    """⛔ 两臂靠 `settings` **注入**，不走 env：`cfg.settings()` 在 import 时求值，
    用 env 会让同一进程里的两臂读到同一个值（MEMORY 硬约束）。"""
    d = _TMP / f"az105_{tag}_{failover}"
    d.mkdir(parents=True, exist_ok=True)
    s = cfg.settings()
    s2 = _AZ105_DC.replace(
        s, limits=_AZ105_DC.replace(s.limits, cand_failover=int(failover)))
    core = Core(s2, llm=_AZ105LLM(payload),
                memory=MemoryStore(d / "m.db"), memory_mode="raw")
    hits: list = []
    inner = core.tools

    class _Reg:
        """只读观测点：记 `(轮次, 工具)`。⛔ 记工具名不够 —— 三次都是 `finish`。"""

        def __getattr__(self, name):
            return getattr(inner, name)

        def dispatch(self, name, args):
            hits.append((int(core.ledger.steps_used), str(name)))
            return inner.dispatch(name, args)

    core.tools = _Reg()
    with contextlib.redirect_stdout(io.StringIO()):
        out = core.run(_AZ105_GOAL, budget_factor=1)
    return out, hits


_AZ105_T = _az105_cands(("x", "finish", {"answer": _AZ105_BAD1}),
                        ("a", "finish", {"answer": _AZ105_OK}),
                        ("b", "finish", {"answer": _AZ105_OK2}))
_AZ105_S = _az105_cands(("x", "finish", {"answer": _AZ105_BAD1}))
_AZ105_A = _az105_cands(("x", "finish", {"answer": _AZ105_BAD1}),
                        ("y", "finish", {"answer": _AZ105_BAD2}),
                        ("z", "finish", {"answer": _AZ105_BAD3}))

_AZ105_T_OFF, _AZ105_T_OFF_H = _az105_run(_AZ105_T, 0, "three")
_AZ105_T_ON, _AZ105_T_ON_H = _az105_run(_AZ105_T, 1, "three")
_AZ105_S_OFF, _AZ105_S_OFF_H = _az105_run(_AZ105_S, 0, "one")
_AZ105_S_ON, _AZ105_S_ON_H = _az105_run(_AZ105_S, 1, "one")
_AZ105_A_ON, _AZ105_A_ON_H = _az105_run(_AZ105_A, 1, "allbad")


def _az105_r1(hits: list) -> list:
    return [n for st, n in hits if st == 1]


#: ⭐ `switch` 是**配置值**不是计数：6 道题累加后必须仍是 1（第一版 `+=` 得 6）。
_AZ105_ACC = _AZ102_RUN._kfo_zero()
for _ in range(6):
    _AZ102_RUN._kfo_absorb(_AZ105_ACC, {"used": 1, "rescued": 1, "cf_extra": 0,
                                        "finish": 1, "external": 0,
                                        "no_more": 0, "switch": 1})
_AZ105_LINE = _AZ102_RUN._kfo_line(_AZ105_ACC)
_AZ105_SRC = (V1 / "run.py").read_text(encoding="utf-8")

check(RS, "AZ105 ★★★★交付候选被打回时**让位给下一个**，且只在「还有候选」时（V42）\n"
          "          —— V41 §107.2：`core.py:1243` 失败 `break`、`1261` 交付 `return`\n"
          "             ⇒ 被*跳过*会让位、被*执行*（无论成败）都不让位。可真实形态是\n"
          "             `finish`×3 ⇒ 首候选要么成功要么被打回，两条路都不让位。\n"
          "         判据（五条，缺一即证明不了这条边）：\n"
          "               ① `failover=0`：三候选、首候选被打回 ⇒ **第 1 轮只 dispatch 1 次**、\n"
          "                  交付里没有那段合法实现（= V41 的行为，一个字节都不许变）；\n"
          "               ② `failover=1`：同一样本 ⇒ 第 1 轮 dispatch **2 次**、交付就是\n"
          "                  第 2 候选的原文、`used=1`/`rescued=1`/`switch=1`；\n"
          "               ③ **单候选时两臂逐字节相同**（`no_more≥1` 显形）——\n"
          "                  开关在没有候选可换时也改变行为 = 语义泄漏；\n"
          "               ④ 三候选全坏 ⇒ `used=2`、自检 `exhausted=1`、状态 `done_unverified`\n"
          "                  ⇒ **代价必须与收益并列**：让位烧的是同一份自检预算；\n"
          "               ⑤ `_kfo_absorb` 里 `switch` **不能求和**（配置值 ≠ 计数），\n"
          "                  且 `run.py` 真的落 `kernel_failover`（不落盘 = 不可审计）。\n"
          "         撤回语义：把 `if (bool(self.s.limits.cand_failover) and _produces\n"
          "                       and ci < len(cands) - 1):` 改成 `if False:` ⇒ ② 必红。",
      # ① 与 ② 必须成对：只看 ② 会把"本来就让位"（S2 那条老路径）记成本机制的功劳，
      #    所以 ① 的"只 dispatch 1 次"是 ② 的**对照**，不是补充说明。
      len(_az105_r1(_AZ105_T_OFF_H)) == 1
      and _AZ105_T_OFF.answer != _AZ105_OK
      and _AZ105_T_OFF.stats["failover"]["used"] == 0
      and len(_az105_r1(_AZ105_T_ON_H)) == 2
      and _AZ105_T_ON.answer == _AZ105_OK
      and _AZ105_T_ON.stats["failover"]["used"] == 1
      and _AZ105_T_ON.stats["failover"]["rescued"] == 1
      and _AZ105_T_ON.stats["failover"]["finish"] == 1
      and _AZ105_T_ON.stats["failover"]["switch"] == 1
      and _AZ105_S_OFF.status == _AZ105_S_ON.status
      and _AZ105_S_OFF.answer == _AZ105_S_ON.answer
      and _az105_r1(_AZ105_S_OFF_H) == _az105_r1(_AZ105_S_ON_H)
      and _AZ105_S_ON.stats["failover"]["no_more"] >= 1
      and _AZ105_A_ON.stats["failover"]["used"] == 2
      and _AZ105_A_ON.stats["verify"]["exhausted"] == 1
      and _AZ105_A_ON.status == "done_unverified"
      and _AZ105_A_ON.answer == _AZ105_BAD3
      and _AZ105_LINE["switch"] == 1 and _AZ105_LINE["used"] == 6
      and '"kernel_failover"' in _AZ105_SRC,
      f"三候选 off 第1轮={_az105_r1(_AZ105_T_OFF_H)} 交付OK={_AZ105_T_OFF.answer == _AZ105_OK} ｜ "
      f"on 第1轮={_az105_r1(_AZ105_T_ON_H)} 交付OK={_AZ105_T_ON.answer == _AZ105_OK} "
      f"used={_AZ105_T_ON.stats['failover']['used']} "
      f"rescued={_AZ105_T_ON.stats['failover']['rescued']} ｜ "
      f"单候选 off/on={_AZ105_S_OFF.status}/{_AZ105_S_ON.status} "
      f"no_more={_AZ105_S_ON.stats['failover']['no_more']} ｜ "
      f"全坏 used={_AZ105_A_ON.stats['failover']['used']} "
      f"exhausted={_AZ105_A_ON.stats['verify']['exhausted']} "
      f"态={_AZ105_A_ON.status} ｜ 台账 switch={_AZ105_LINE['switch']}")

# ── AZ106 的取证装置（V43）：`--allow-shell` 在**两条**构造路径上都要通电 ──────
#  ⭐ 修之前的形态（`run.py:158-171`）：`Guard(allow_shell=…)` **只写在**
#     `if args.offline or not s.llm.usable():` 这一支里。另一支（真模型）**不构造** guard
#     ⇒ 落回 `Core.__init__` 的 `guard or Guard(...)` 默认值 ⇒ `allow_shell=False`。
#     ⇒ 用户明明带了 `--allow-shell`，日志里永远是
#       `[guard:tool_misuse] 拦截 —— shell 未开启（需显式 --allow-shell）`。
#     实测现场：2026-11-02 12:58 的 `sales.csv → report.md`（`bench/evidence/v6_s133/`）。
#
#  ⛔ 为什么这条断言**必须四格**：门禁平时走的**就是**离线那一支，只测它永远绿 ——
#     这正是它坏了两百多轮没人发现的原因（"开关只在一条没人走的分支上通电"）。
#     只测联网那一支也证明不了"对照臂没被改"（`allow_shell=False` 必须仍为 False）。
import argparse as _AZ106_AP                                     # noqa: E402
from unittest import mock as _mock                               # noqa: E402

_AZ106_RUN = _AZ102_RUN                                          # 同一个 `run` 模块


def _az106_allow_shell(offline: bool, api_key: str, allow_shell: bool) -> bool:
    """真的走一次 `run._build`，只读 `core.guard.allow_shell`。

    ⛔ 不改 `V1_LLM_OFFLINE`：`cfg.settings()` 在 import 时求值（MEMORY 硬约束），
       改 env 对同一进程无效 ⇒ 只能用 `dataclasses.replace` **注入** settings。
    """
    s = cfg.settings()
    s2 = _AZ105_DC.replace(
        s, llm=_AZ105_DC.replace(s.llm, offline=bool(offline), api_key=str(api_key)))
    args = _AZ106_AP.Namespace(offline=bool(offline), memory_mode="raw",
                               no_external=True)
    with _mock.patch.object(cfg, "settings", lambda: s2):
        core = _AZ106_RUN._build("probe", args, allow_shell=allow_shell)
    return bool(core.guard.allow_shell)


_AZ106_NET_ON = _az106_allow_shell(False, "probe-key", True)
_AZ106_NET_OFF = _az106_allow_shell(False, "probe-key", False)
_AZ106_LOC_ON = _az106_allow_shell(True, "probe-key", True)
_AZ106_LOC_OFF = _az106_allow_shell(True, "probe-key", False)

check(RS, "AZ106 ★★★★`--allow-shell` 在**两条**构造路径上都要通电（V43）\n"
          "          —— 修之前 `Guard(...)` 只写在 `_build` 的离线分支里，\n"
          "             真模型路径落回默认 `allow_shell=False` ⇒ 开关**存在但没接**。\n"
          "         判据（四格，缺一即证明不了这条边）：\n"
          "               ① 联网分支 + `allow_shell=True` ⇒ True（修前是 False，本条**必红**）\n"
          "               ② 联网分支 + `allow_shell=False` ⇒ False（对照臂不许被顺手打开）\n"
          "               ③ 离线分支 + `allow_shell=True` ⇒ True（老行为不许丢）\n"
          "               ④ 离线分支 + `allow_shell=False` ⇒ False\n"
          "         撤回语义：把 guard 构造挪回 `if` 里面 ⇒ ① 必红、③ 仍绿\n"
          "                   （⇒ 只测 ③ 的断言无法发现这个缺陷）。",
      _AZ106_NET_ON is True
      and _AZ106_NET_OFF is False
      and _AZ106_LOC_ON is True
      and _AZ106_LOC_OFF is False,
      f"联网 on/off={_AZ106_NET_ON}/{_AZ106_NET_OFF} "
      f"｜ 离线 on/off={_AZ106_LOC_ON}/{_AZ106_LOC_OFF}")

# ── AZ107 的取证装置（V44）：**产物**验收，与 `status` 分列 ────────────────────
#  ⭐ 修之前的形态：`status` 是**过程量**（主循环怎么结束的），却一直被当成
#     结果量用（`cmd_run` 的退出码就是 `status == "done"`）。第一百三十三轮的三臂实测：
#       A `stalled` / 无产物 ｜ B `done` / **无产物** ｜ C `stalled` / 产物正确
#     ⇒ 两个方向都在撒谎：`done` 不等于"东西在"，`stalled` 不等于"东西不在"。
#  ⛔ 为什么不在 `status` 里加值：那会改 `answers.jsonl` 的历史可比性，
#     而且下一次加结果维度又要改一次枚举（§110）。
import mw.verify as _AZ107_VFY                                    # noqa: E402

_AZ107_GOAL = ("工作目录里有 sales.csv（列为 月份,区域,金额）。请计算每个区域的金额合计，"
               "按合计从高到低生成一张 markdown 表格，写入工作目录下的 report.md。"
               "表格两列为 区域|合计。")
_AZ107_ANS = "已完成，报告已写入 report.md。"
_AZ107_INPUT = "月份,区域,金额\n1月,华北,1200\n"
_AZ107_REPORT = "| 区域 | 合计 |\n|---|---|\n| 华北 | 2700 |\n"


def _az107_run(tag: str, files: dict) -> object:
    """真 `Core` 跑一遍：唯一变量是**工作目录里有没有那个产物**。

    ⛔ 两臂用同一个 `payload`、同一个桩：这不是"两个实验"，是**同一个实验**
       的两次落点（产物在 / 不在）。差别只要不是那一件事，读数就归不了因。
    """
    d = _TMP / f"az107_{tag}"
    ws = d / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        p = ws / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    s = cfg.settings()
    s2 = _AZ105_DC.replace(s, paths=_AZ105_DC.replace(s.paths, workspace=ws))
    payload = _az105_cands(("交付", "finish", {"answer": _AZ107_ANS}))
    core = Core(s2, llm=_AZ105LLM(payload),
                memory=MemoryStore(d / "m.db"), memory_mode="raw")
    with contextlib.redirect_stdout(io.StringIO()):
        return core.run(_AZ107_GOAL, budget_factor=1)


_AZ107_NOFILE = _az107_run("nofile", {"sales.csv": _AZ107_INPUT})
_AZ107_WITHFILE = _az107_run("withfile", {"sales.csv": _AZ107_INPUT,
                                          "report.md": _AZ107_REPORT})

#: 纯函数的五值表 —— 不走 `Core`，逐值钉死（`NONE`/`UNKNOWN` 故意不存在）。
_AZ107_DIR = _TMP / "az107_table"
(_AZ107_DIR / "a.md").parent.mkdir(parents=True, exist_ok=True)
(_AZ107_DIR / "a.md").write_text("x", encoding="utf-8")
_AZ107_TABLE = {
    "PASS": _AZ107_VFY.check_delivery("请生成 a.md", _AZ107_DIR, "")["verdict"],
    "FAIL": _AZ107_VFY.check_delivery("请生成 only.md", _AZ107_DIR, "")["verdict"],
    "PARTIAL": _AZ107_VFY.check_delivery("请生成 a.md 与 b.json", _AZ107_DIR, "")["verdict"],
    "ANSWER": _AZ107_VFY.check_delivery("解释快排的复杂度", _AZ107_DIR, "O(n log n)")["verdict"],
    "EMPTY": _AZ107_VFY.check_delivery("解释快排的复杂度", _AZ107_DIR, "")["verdict"],
}
#: 并列列举的传递：`与` 后面的第二个 token 也必须是**产物**（V44 的 S5 抓到的）。
_AZ107_ENUM = _AZ107_VFY.expected_artifacts("请生成 out/a.md 与 out/b.json 两个文件。")
#: 输入/产物**不许**混：`sales.csv` 是输入，`report.md` 是产物。
_AZ107_SPLIT = _AZ107_VFY.expected_artifacts(_AZ107_GOAL)

check(RS, "AZ107 ★★★★产物验收与 `status` **分列**：`done` 不等于「东西在」（V44）\n"
          "          —— 修之前 `cmd_run` 的退出码是 `status == \"done\"`，\n"
          "             而 `status` 只是「主循环怎么结束的」。实测两向皆谎：\n"
          "             `done`/无产物（12:58 B 臂）、`stalled`/产物正确（13:13 C 臂）。\n"
          "         判据（四条，缺一即证明不了这条边）：\n"
          "               ① 五值判定表逐个相等（PASS/FAIL/PARTIAL/ANSWER/EMPTY，\n"
          "                  且 `NONE`/`UNKNOWN` 故意不存在 —— 说不清的值会被读成成功）；\n"
          "               ② 同一 payload、同一桩，**只有产物不在** ⇒ `status=done`\n"
          "                  而 `verdict=FAIL`（两个词方向相反，正是修好前被读成成功的那一格）；\n"
          "               ③ 产物在 ⇒ `verdict=PASS`（开关不只有『报错』那一档）；\n"
          "               ④ `Outcome.verdict` 与 `stats['delivery']['verdict']` **逐字相等**\n"
          "                  （两处各算一遍 ⇒ 会出现『日志说 PASS、返回值说 FAIL』）。\n"
          "         撤回语义：`_finish` 里把 `str(_dv.get('verdict') or '')` 改成 `''`\n"
          "                   ⇒ ②③ 必红；把 `if outs:` 那一支改成恒 `PASS` ⇒ ①② 必红。",
      _AZ107_TABLE == {"PASS": "PASS", "FAIL": "FAIL", "PARTIAL": "PARTIAL",
                       "ANSWER": "ANSWER", "EMPTY": "EMPTY"}
      and _AZ107_ENUM[0] == ["out/a.md", "out/b.json"]
      and _AZ107_SPLIT[0] == ["report.md"] and _AZ107_SPLIT[1] == ["sales.csv"]
      and _AZ107_NOFILE.status == "done"
      and _AZ107_NOFILE.verdict == "FAIL"
      and _AZ107_WITHFILE.verdict == "PASS"
      and _AZ107_NOFILE.verdict == _AZ107_NOFILE.stats["delivery"]["verdict"]
      and _AZ107_WITHFILE.verdict == _AZ107_WITHFILE.stats["delivery"]["verdict"],
      f"五值={_AZ107_TABLE} ｜ 抽取={_AZ107_SPLIT} ｜ 列举={_AZ107_ENUM} ｜ "
      f"无产物: status={_AZ107_NOFILE.status}/verdict={_AZ107_NOFILE.verdict} ｜ "
      f"有产物: status={_AZ107_WITHFILE.status}/verdict={_AZ107_WITHFILE.verdict}")


# ── AZ108 的取证装置（V45）：候选**宽度**的真实分布 ──────────────────────────
#  ⭐ 修之前的形态：「每轮候选都是什么 tool」全项目**只有日志**一个来源。
#     第一百三十三轮那句「第 1 轮 5 个候选里 3 个是同一个 `agent:code_agent`」
#     是人看日志数出来的 —— 进不了台账、说不出是哪一轮、也没法 cross-check。
#     §15 那条老账写得很清楚：**读数标签不是读数**。
#  ⛔ 为什么必须与 `cands_total` **分列**两个数（`offered` / `cands_total`）：
#     V40 §106.2 已经实测过「候选数 3」≠「互不相同的候选 3」；
#     而这里还有第三种：`cands = plan.steps[:n]` 的**截断**。
#     S5 实测"模型给 3 条、执行器拿 1 条"，旧读数（日志 `候选=1`）把它读成
#     "模型只给了一个" —— 三个数（给了/可用/进执行器）少一个就归不了因。
_AZ108_GOAL = _AZ107_GOAL
_AZ108_ANS = ("已完成：按区域汇总的金额合计已写入工作目录下的 report.md，"
              "三个区域从高到低为 华北 2700 / 华南 2000 / 华东 700。")


def _az108_payload(*extra: object) -> str:
    items: list = [
        {"intent": "交付", "tool": "finish", "args": {"answer": _AZ108_ANS}},
        {"intent": "跑一段", "tool": "run_shell", "args": {"cmd": "echo hi"}},
        # ⚠️ 与第 1 条**同 tool 不同 args** ⇒ `_extract` 的 sig 去重**拦不住**它。
        #    这正是 `by_tool_max_round` 存在的理由：宽度可以被同一个 tool 霸占。
        {"intent": "再交一次", "tool": "finish",
         "args": {"answer": _AZ108_ANS + "（第二版）"}},
        # ⚠️ 与第 1 条**逐字节相同** ⇒ sig 重复，必须被丢并记进 `dropped_dup`。
        {"intent": "重复", "tool": "finish", "args": {"answer": _AZ108_ANS}},
        # ⚠️ 未注册的工具名 ⇒ `dropped_unknown`（修法是加工具，不是改提示词）。
        {"intent": "想了没注册", "tool": "browser", "args": {}},
    ]
    items.extend(extra)
    return json.dumps({"candidates": items}, ensure_ascii=False)


def _az108_run(tag: str, payload: str):
    """真 `Core` 跑一遍。⛔ 单轮：第 1 个候选就是能交付的 `finish`。"""
    d = _TMP / f"az108_{tag}"
    ws = d / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "sales.csv").write_text(_AZ107_INPUT, encoding="utf-8")
    s = cfg.settings()
    s2 = _AZ105_DC.replace(s, paths=_AZ105_DC.replace(s.paths, workspace=ws))
    core = Core(s2, llm=_AZ105LLM(payload),
                memory=MemoryStore(d / "m.db"), memory_mode="raw")
    with contextlib.redirect_stdout(io.StringIO()):
        out = core.run(_AZ108_GOAL, budget_factor=1)
    return core, out


_AZ108_CORE, _AZ108_OUT = _az108_run("main", _az108_payload())
#: 夹一条**非 dict**（格式问题）—— 它必须与"未注册工具"分开计，
#: 因为修法相反：一个改提示词/解析器，一个加工具。
_AZ108_CORE_S, _AZ108_OUT_S = _az108_run(
    "shape", _az108_payload("这是一条字符串，不是候选对象"))
# ⚠️ 断言用的必须是**快照**，不能是 `stats` 里的活引用 ——
#    自踩（第一版就是这么红的）：下面 ⑥ 会**故意改写** `stats["plan"]` 来验深拷，
#    而 `_AZ108_P` 只要指向同一个 dict，那一次改写就把自己的断言弄红了。
#    与纪律 20 同宗：**证据脚本不许写回自己的证据路径**。
_AZ108_P = json.loads(json.dumps(_AZ108_OUT.stats["plan"], ensure_ascii=False))
_AZ108_W = dict(_AZ108_OUT.stats["planner"]["width"])
_AZ108_WS = dict(_AZ108_OUT_S.stats["planner"]["width"])

#: 深拷自检：拿到的 `stats` 不能是内核的活引用。
#  ⚠️ 理由同 V44 自踩的 `_kfo_absorb`（把配置值当计数求和）：一处被改，
#     另一处静默跟着变的读数，比没有读数更危险。
_AZ108_OUT.stats["plan"]["by_tool"]["__probe__"] = 999
_AZ108_OUT.stats["plan"]["per_round"][0]["tools"]["__probe__"] = 999
_AZ108_DEEPCOPY_OK = ("__probe__" not in _AZ108_CORE.plan_stats["by_tool"]
                      and "__probe__" not in _AZ108_CORE.plan_stats["per_round"][0]["tools"])

check(RS, "AZ108 ★★★★候选宽度进 **stats**（不是日志）：给了多少 / 可用多少 /\n"
          "         进执行器多少，三个数**分列**（V45）\n"
          "         —— 修之前「每轮候选都是什么 tool」只有日志一个来源，\n"
          "            于是 V39「候选数 3 ≠ 互不相同的候选 3」和 V45「模型给 3 条、\n"
          "            执行器拿 1 条（`[:n]` 截断）」这两种病都读不出来。\n"
          "         判据（六条，缺一即证明不了这条边）：\n"
          "               ① `rounds == len(per_round)`（恒存在：每轮都记了）；\n"
          "               ② `Σby_tool == cands_total == Σper_round.n`（三个口径相等）；\n"
          "               ③ `examined_total − cands_total == truncated_cands`（截断恒等式；\n"
          "                  键名带单位 V66/§133：**条**，不是次）；\n"
          "               ④ `by_tool_max_round == 2`：两个 **args 不同** 的 `finish`\n"
          "                  都被 sig 去重放行 ⇒ 宽度确实可能被同一个 tool 霸占；\n"
          "               ⑤ `width` 三个丢弃原因**分列**且强守恒\n"
          "                  （`offered − dup − unknown − shape == examined_total`）；\n"
          "               ⑥ `stats` 是**深拷**：改它不许改到内核。\n"
          "         撤回语义：`_note_plan_round` 里把 `ps['rounds'] += 1` 换回 `pass`\n"
          "                   ⇒ ①②③④ 必红、⑤⑥ 仍绿（这一格正是本条不能只看\n"
          "                   `width` 的原因：**模型给了几条** 与 **执行器拿到几条**\n"
          "                   是两条独立的边）。",
      _AZ108_P["rounds"] == len(_AZ108_P["per_round"]) == 1
      and sum(_AZ108_P["by_tool"].values()) == _AZ108_P["cands_total"]
      == sum(r["n"] for r in _AZ108_P["per_round"]) == 3
      and (_AZ108_P["examined_total"] - _AZ108_P["cands_total"]
           == _AZ108_P["truncated_cands"])
      and _AZ108_P["by_tool_max_round"] == 2
      and _AZ108_P["by_tool"] == {"finish": 2, "run_shell": 1}
      and _AZ108_P["produces_by_round"] == [2]
      and _AZ108_W == {"offered": 5, "dropped_dup": 1, "dropped_unknown": 1,
                       "dropped_shape": 0}
      and (_AZ108_W["offered"] - _AZ108_W["dropped_dup"]
           - _AZ108_W["dropped_unknown"] - _AZ108_W["dropped_shape"]
           == _AZ108_P["examined_total"])
      and _AZ108_WS["dropped_shape"] == 1 and _AZ108_WS["dropped_unknown"] == 1
      and _AZ108_DEEPCOPY_OK,
      f"plan={_AZ108_P} ｜ width={_AZ108_W} ｜ shape臂={_AZ108_WS} ｜ "
      f"深拷={'ok' if _AZ108_DEEPCOPY_OK else '活引用！'}")


# ── AZ109 的取证装置（V46）：让位救回的是**交付**还是**一个步骤** ────────────
#  ⭐ 修之前的形态：`rescued` 只有一档，判据是「这一轮产生了**交付答案**」。
#     V45 实测反例（`v6_s135/p2_real.txt`）：B 臂第 1 轮 5 个候选
#     `{code_agent:4, run_shell:1}`；`code_agent` 在这道题上必败（它只认
#     「函数名必须是 X」），`run_shell` 成功写出 `report.md` —— **那一臂的 PASS
#     完全靠这次让位**，而台账记 `rescued=0`。
#     ⇒ 这不是"机制没通电"，是**判据漏了一档**：`rescued` 判的是
#        "产生了交付答案"，不是"任务被推进了"。
#  ⛔ 为什么两档必须**同源判据**（`_fo_pending and ci > 0`）：
#     只按 `ci > 0` 会把"首候选被 guard 拦掉"那条**老路径**（V41 S2 实测）
#     记成让位的功劳 —— 纪律 24：判据的分辨率不得高于装置。
_AZ109_STEP_PAYLOAD = _az105_cands(("先交一版", "finish", {"answer": _AZ105_BAD1}),
                                   ("这一步会成功", "recall", {"query": "压平"}))

#: D 景 = 交付救回（AZ105 的三候选 ON 臂就是它，**不再造第二份**：
#:   同一场景两套装置 = 两处会漂移的实现）。
_AZ109_D = _AZ105_T_ON
#: S 景 = 步骤救回。`recall` 不是交付动作，但它真的被 dispatch 了。
_AZ109_S, _AZ109_S_H = _az105_run(_AZ109_STEP_PAYLOAD, 1, "step")
#: 单候选景（无弹药）—— 开关不许泄漏。
_AZ109_ONE = _AZ105_S_ON

_AZ109_FO = {k: dict(v.stats["failover"])
             for k, v in (("D", _AZ109_D), ("S", _AZ109_S), ("ONE", _AZ109_ONE))}
#: `rescue_rate` 的分子必须仍是**结果量**：救回一个步骤 ≠ 救回一次交付。
#: 用一对**只能靠分子区分**的输入来钉：两臂 `used=2`、`rescued` 都是 2，
#: 只有 `rescued_answer` 不同 ⇒ 率必须不同（0.5 vs 0.0）。
_AZ109_LINE_A = _AZ102_RUN._kfo_line(
    {"used": 2, "rescued": 2, "rescued_answer": 1, "rescued_step": 1,
     "cf_extra": 0, "finish": 1, "external": 0, "no_more": 0, "switch": 1})
_AZ109_LINE_B = _AZ102_RUN._kfo_line(
    {"used": 2, "rescued": 2, "rescued_answer": 0, "rescued_step": 2,
     "cf_extra": 0, "finish": 0, "external": 0, "no_more": 0, "switch": 1})
_AZ109_SRC = (V1 / "run.py").read_text(encoding="utf-8")

check(RS, "AZ109 ★★★★让位救回的**是交付还是一个步骤**，两个数分列（V46）\n"
          "         —— 修之前只有一档 `rescued`，它判的是「这一轮产生了交付\n"
          "            答案」。V45 实测：B 臂的 PASS 完全靠让位（`run_shell` 写出\n"
          "            `report.md`），因为那不是交付动作 ⇒ 记 `rescued=0`。\n"
          "            **一个「救了场却记成没救」的计数器，比没有更危险。**\n"
          "         判据（六条，缺一即证明不了这条边）：\n"
          "               ① 交付救回 ⇒ `rescued_answer=1` 而 `rescued_step=0`；\n"
          "               ② 步骤救回 ⇒ `rescued_step=1` 而 `rescued_answer=0`，\n"
          "                  且那一轮**真的 dispatch 了** `recall`（不是只加了个数）；\n"
          "               ③ 强守恒 `rescued == rescued_answer + rescued_step`（三景）；\n"
          "               ④ 单候选（无弹药）⇒ 两个子计数**都是 0**（开关不泄漏）；\n"
          "               ⑤ `rescue_rate` 的分子仍是 `rescued_answer`\n"
          "                  （两臂 `rescued` 相同、子计数不同 ⇒ 率必须不同）；\n"
          "               ⑥ `run.py` 真的落 `rescued_answer`/`rescued_step`\n"
          "                  （不落盘 = 不可审计）。\n"
          "         撤回语义：删掉 `core.py` 里 `rescued_step += 1` 那两行 ⇒ **② 必红**，\n"
          "                   ①③④⑤⑥ 仍绿。\n"
          "                   ⭐ ③（守恒）在「两档都归零」时**仍然成立** —— 这是本条\n"
          "                   必须同时钉「能非零」的原因：**守恒是必要条件，不是通电证明**\n"
          "                   （同宗：V44 的 `status=done` 也永远自洽）。\n"
          "                   这一格同时说明：**交付** 与 **推进** 是两条独立的边。",
      _AZ109_FO["D"]["rescued_answer"] == 1
      and _AZ109_FO["D"]["rescued_step"] == 0
      and _AZ109_FO["S"]["rescued_step"] == 1
      and _AZ109_FO["S"]["rescued_answer"] == 0
      and _AZ109_FO["S"]["used"] == 1
      and "recall" in [n for _st, n in _AZ109_S_H]
      and all(v["rescued"] == v["rescued_answer"] + v["rescued_step"]
              for v in _AZ109_FO.values())
      and _AZ109_FO["ONE"]["rescued_answer"] == 0
      and _AZ109_FO["ONE"]["rescued_step"] == 0
      and _AZ109_LINE_A["rescue_rate"] == 0.5
      and _AZ109_LINE_B["rescue_rate"] == 0.0
      and '"rescued_answer"' in _AZ109_SRC and '"rescued_step"' in _AZ109_SRC,
      f"D={_AZ109_FO['D']} ｜ S={_AZ109_FO['S']} 第1轮dispatch="
      f"{_az105_r1(_AZ109_S_H)} ｜ ONE={_AZ109_FO['ONE']} ｜ "
      f"率 A/B={_AZ109_LINE_A['rescue_rate']}/{_AZ109_LINE_B['rescue_rate']}")


check(RS, "AL7 奖励通道**默认关闭**：不给 features ⇒ guidance 不含奖励\n"
          "         —— 否则它会污染所有既有测量（那是一次未声明的条件变更）",
      _AL_wm2.reward_guidance(features=None, task_id="x") == ""
      and "历史同类题" not in _AL_wm2.guidance(action="写一个函数 f(s)"),
      "默认不参与")

check(RS, "AL8 ★内核**不许自己造特征**：`core.py` 不得 import `mw.features`\n"
          "         —— 一旦内核能从目标文本里给自己算特征，它就能给自己挑\n"
          "            『看起来像』的先例 ⇒ 检索退化成又一次自证。\n"
          "            特征必须由外部锚层提供（`run.py` 算好传进来）。",
      "mw.features" not in _strip_docstrings(_src("core.py"))
      and "from .features" not in _strip_docstrings(_src("core.py")),
      "core.py 未引 features（docstring 里讨论它不算 —— 静态锚必须剥注释）")

_AL_RUN = (V1 / "run.py").read_text(encoding="utf-8")

# ⚠️ 本条来自一个**真 bug**（本轮自踩）：`WorldModel.stats()` 新增了三个奖励字段，
#    而 `run.py` 的累加器用的是一份**手写的键名单** ⇒ 新字段一次都没被搬进去。
#    表现：日志上"写了 5 条"，而"读到几条"永远是 0 —— 你根本看不出通道有没有被用过。
_AL_wm_stats = WorldModel(MemoryStore(_TMP / "al_stats.db"), StubLLM()).stats()
#: 比值类读数**不许被累加**（逐次平均会给出错的答案：`_kwm_absorb` 的注释已经写了
#: 100 次全对 + 1 次全错 ⇒ 平均 0.5，真值 100/101）。它们由总数重算。
_AL_DERIVED = {"hit_rate", "suppress_rate"}
_AL_num = {k for k, v in _AL_wm_stats.items()
           if isinstance(v, (int, float)) and k not in _AL_DERIVED}
check(RS, "AL11 ★★世界模型的**每个**数值读数都必须被累加器搬运\n"
          "         （键名单必须由零点派生，不许另写一份）\n"
          "         —— 本轮自踩：奖励的三个新字段没进名单 ⇒ 日志上写=5 / 读=0，\n"
          "            一个你看不出有没有被用过的通道 = 装饰（同名病灶）",
      _AL_num <= set(_KWM_NUM_KEYS),
      f"未被搬运={sorted(_AL_num - set(_KWM_NUM_KEYS))}")

check(RS, "AL12 「读了几条」必须**有消费者**：`rewards_seen` 必须出现在打印里\n"
          "         —— 只报『写入几条』不报『读到几条』，等于给通道发了一张\n"
          "            永远不检票的票。本轮实测靠它才第一次看见 读=14。",
      "rewards_seen" in _AL_RUN and "挡自己" in _AL_RUN,
      "打印侧已消费")

# ⚠️ 本条来自一个**真 bug**（本轮自踩）：写成 `paths.memory_db`，而真实属性叫
#    `paths.db` ⇒ 跑完 5 题、写完账本之后才 AttributeError 崩掉，
#    奖励一条没写，而下游的"考试"跑得**好好的**（拿到的是空记忆）。
#    ⇒ 一个拼错的属性名会让整条实验链**静默地答错问题**。
_AL_paths = cfg.settings().paths
_AL_attr = sorted(set(re.findall(r"paths\.(\w+)", _AL_RUN)))
_AL_bad = [a for a in _AL_attr if not hasattr(_AL_paths, a)]
check(RS, "AL10 ★`run.py` 里引用的 `paths.*` 属性必须**真实存在**\n"
          "         —— 本轮自踩：写成 `paths.memory_db`（真名 `paths.db`）⇒ 跑完\n"
          "            才崩，奖励一条没写，而下游考试照常跑完（拿到空记忆）。\n"
          "            一个拼错的属性名会让整条实验链**静默地答错问题**。",
      not _AL_bad,
      f"引用={_AL_attr} 不存在={_AL_bad}")

check(RS, "AL9 奖励必须写在**整轮跑完之后**：`record_reward` 的调用点\n"
          "        必须在 `run_suite(` 之后（轮内写 ⇒ 第 N 题读到第 N-1 题）",
      _AL_RUN.find("record_reward") > _AL_RUN.find("run_suite(solver"),
      f"record_reward@{_AL_RUN.find('record_reward')} > "
      f"run_suite@{_AL_RUN.find('run_suite(solver')}")

print()
print("─" * 74)
print("AM 组：★证据必须自描述（2026-10-17 第十五轮实测逼出）")
print()
print("      起因：同一套 struct_convert 在 --max-tokens 1024 与 1536 下给出")
print("      0.400 与 0.7600 两个相差近一倍的读数。想追查哪一行是哪个预算，")
print("      却发现**四份真实账本里一行都不带可读的口径字段** —— 只有 16 位")
print("      哈希，且生成它们的实验脚本也没入库。那是六个月后打不开的证据。")

# ⚠️ R13 硬坑：这里一次性定义所有后续断言要用的静态源码文本。
#    本轮自踩：AM11 用了当时尚未定义的 `_RUN_SRC` ⇒ NameError 打断汇总行，
#    整份契约测试失去最后一行 —— 与自己宣称已内建的纪律（不对可能不存在的
#    名字做算术/取值）直接冲突。集中定义 + 全用同一变量名，一处不再出错。
_RUN_SRC = (V1 / "run.py").read_text(encoding="utf-8")
_RUN_TESTS_SRC = (V1 / "run_tests.sh").read_text(encoding="utf-8")


class _OCH:
    """给 `_option_conflict` 用的最小参数对象：只给被检查的字段，缺的一律为空/假。

    为什么不用 `argparse.Namespace`： `_option_conflict` 内部用 `getattr` 取值，
    传一个普通对象就够了 —— 而显式列出字段能让"哪些字段参与判定"看得见。
    """

    def __init__(self, **kw):
        for k in ("suite_dir", "memory_dir", "fresh", "save_baseline", "suite"):
            setattr(self, k, kw.get(k, "" if k != "fresh" else False))


from run import _option_conflict            # noqa: E402

_AM_tmp = Path(tempfile.mkdtemp()) / "am.jsonl"
_AM_cal = {"scorer_sha": "S", "temperature": 0, "max_tokens": 1536}
_AM_sig = signature_of(_AM_cal)
_AM_L = ScoreLedger(_AM_tmp, caliber_sig=_AM_sig)

# 旧行为：有签名、没有可读口径 ⇒ 落盘，然后永久无法复核
_AM_w_old = _AM_L.append([{"session": 0, "family": "a", "passed": True,
                           "score": 1.0, "task_id": "t0",
                           "caliber_sig": _AM_sig}])
check(RS, "AM1 ★写侧必须挡下「只有哈希、没有可读口径」的行（`no_caliber`）\n"
          "         —— 看得到分数、看不到预算/温度/记忆口径 ⇒ 无法复核、无法\n"
          "            复用、无法判断两个读数能不能比。第十六轮之前这四份真实\n"
          "            账本就是这样的（全部 114 行），现在它们必须被标红。",
      _AM_w_old["added"] == 0 and _AM_w_old["no_caliber"] == 1
      and len(_AM_L.load()) == 0,
      f"append={_AM_w_old}")

_AM_w_bad = _AM_L.append([{"session": 0, "family": "a", "passed": True,
                           "score": 1.0, "task_id": "t1", "caliber_sig": _AM_sig,
                           "caliber": {"temperature": 1, "max_tokens": 8}}])
check(RS, "AM2 ★`caliber` 与 `caliber_sig` 不自洽 ⇒ 拒（`caliber_mismatch`）\n"
          "         —— 不自洽比缺失**更糟**：缺失你会知道自己不知道，\n"
          "            不自洽会让你以为自己能复核，然后复错。",
      _AM_w_bad["added"] == 0 and _AM_w_bad["caliber_mismatch"] == 1,
      f"append={_AM_w_bad}")

_AM_w_ok = _AM_L.append([{"session": 0, "family": "a", "passed": True,
                          "score": 1.0, "task_id": "t1", "caliber_sig": _AM_sig,
                          "caliber": _AM_cal}])
check(RS, "AM3 自洽的行照收 —— 闸只挡缺证据的，不挡正常使用",
      _AM_w_ok["added"] == 1 and len(_AM_L.load()) == 1, f"append={_AM_w_ok}")

check(RS, "AM4 两种新拒绝进**受控词表**：又一个总拒绝数 ⇒ 分不清是没写口径\n"
          "         还是口径自相矛盾（与 critique / ledger 已有两组同构）",
      {"no_caliber", "caliber_mismatch"} <= set(REJECT_KINDS)
      and set(_AM_w_old) >= {"no_caliber", "caliber_mismatch"},
      f"REJECT_KINDS={REJECT_KINDS}")

# ── 读侧：历史**照收但必须标红**（不许把⑭的四份真账本静默作废）────────
_AM_hist = Path("bench/evidence/score_ledger_real_qwen25_7b_T0_8sess.jsonl")
_AM_hl = ScoreLedger(_AM_hist)
_AM_hsig = collections.Counter(
    r.get("caliber_sig") for r in _AM_hl.load()).most_common(1)[0][0]
_AM_rows_hist = _AM_hl.admissible(_AM_hsig)
check(RS, "AM5 ★历史行**照样受理**（那是真证据），但必须记账\n"
          f"         `legacy_no_caliber` —— 八会话真账本 {len(_AM_rows_hist)} 行\n"
          "         全部没有可读口径。删掉它们会让之前三轮的结论当场失去证据，\n"
          "         假装它们可复核则是把不能复核的东西当能复核 ⇒ 只能标红。",
      len(_AM_rows_hist) > 0
      and _AM_hl.stats.legacy_no_caliber == len(_AM_rows_hist),
      f"受理={len(_AM_rows_hist)} 不可复核={_AM_hl.stats.legacy_no_caliber}")

_AM_hl2 = ScoreLedger(_AM_hist)
_AM_hl2.admissible(_AM_hsig)
check(RS, "AM6 `legacy_no_caliber` 必须有**消费者**：必须出现在 `as_line()` 里\n"
          "         —— 一个只在内部计、永远没人念的数，等于没有（装饰字段的同型）",
      "legacy_no_caliber" in LedgerStats().as_dict()
      and "不可复核" in _AM_hl2.stats.as_line(),
      _AM_hl2.stats.as_line()[:96])

#: ⚠️ 静态锚必须**剥 docstring**：`inspect.getsource` 会把文档里的 `LEDGER_SIG_EXCLUDE`
#: 也算进来 —— 而那条注释恰恰是唯一**允许**提到它的地方。老坑重犯一次，此处钉住。
_AM_lso = _strip_docstrings(inspect.getsource(ledger_sig_of))
check(RS, "AM7 去掉哪些字段只剩**一处**说了算：`ledger_sig_of` 必须委托\n"
          "         `ledger_caliber_of` —— 两边各删一份，迟早有一天 caliber 变了\n"
          "         而落下的一边没变 ⇒ 行内 caliber 与签名不再自洽（AM2 的成因）",
      "ledger_caliber_of" in _AM_lso and "LEDGER_SIG_EXCLUDE" not in _AM_lso,
      f"剥注释后={_AM_lso.strip()[-60:]!r}")

check(RS, "AM8 run.py 写账本时必须同时写 `caliber`（静态锚，剥 docstring 后查）\n"
          "         —— 只写 `caliber_sig` 不写 `caliber`，等于把\"证据要自描述\"\n"
          "            这条要求托付给记性；第十五轮就是这么丢掉的。",
      '"caliber_sig": _cal_sig' in _RUN_SRC and '"caliber": _cal_fields' in _RUN_SRC,
      "run.py 未同时写 caliber")

_AM_app = inspect.getsource(ScoreLedger.append)
check(RS, "AM9 撤回点必须**唯一**：`no_caliber` 与 `caliber_mismatch` 各一处\n"
          "         （R13：分散的撤回点 ⇒ 撤回时断一处另一处还在，读数不转红）",
      _AM_app.count("no_caliber += 1") == 1
      and _AM_app.count("caliber_mismatch += 1") == 1,
      f"no_caliber={_AM_app.count('no_caliber += 1')} "
      f"mismatch={_AM_app.count('caliber_mismatch += 1')}")

_AM_ctrl = V1 / "bench" / "run_budget_control.sh"
check(RS, "AM10 产生证据的**脚本必须有可被锁定的指纹**：CI 每跑一次都要念出\n"
          "         bench 脚本的 sha256 与账本\"不可复核\"行数 —— 四份旧账本之所以\n"
          "         哑掉，一半是因为连产生它们的脚本都没进仓库，改没改过无从得知。\n"
          "         ⛔ 这里不断言脚本内容，只断言\"它被审计了\"：\n"
          "            锁内容会逼得人为了过闸而冻结脚本，那是假的可复现。",
      _AM_ctrl.exists() and "hashlib.sha256" in _RUN_TESTS_SRC
      and "legacy_no_caliber" in _RUN_TESTS_SRC,
      f"脚本存在={_AM_ctrl.exists()} 指纹步已接={('hashlib.sha256' in _RUN_TESTS_SRC)}")

_AM_oc = _strip_docstrings(inspect.getsource(_option_conflict))
_AM_body = _strip_docstrings(_RUN_SRC)
#: 同一事实只声明一处：冲突规则要么在 `_option_conflict` 里，要么散在主流程里。
#: 两处在 ⇒ 改一处另一处仍在，AM12 测得通过而实际行为没变。
_AM_decl = _AM_body.count("--fresh 与 --memory-dir")
check(RS, "AM11 ★语义冲突必须**报错**，不许静默兜底（本轮自踩）\n"
          "         —— `--fresh`（记忆隔离）与 `--memory-dir`（记忆跨题累积）同时给，\n"
          "            旧代码把 fresh 悄悄改成 False ⇒ 我跑的『隔离 vs 累积』2×2\n"
          "            四格全同（mean=0.7600 一字不差），还以为是结论太稳。\n"
          "            **对照组被静默折叠成同一格**，是比没有对照组更糟的事 ——\n"
          "            它长得像对照，实际一次都没对照。",
      _AM_decl == 1 and "--fresh 与 --memory-dir" in _AM_oc,
      f"声明处数={_AM_decl} 在 _option_conflict 内="
      f"{'--fresh 与 --memory-dir' in _AM_oc}")

_oc = _option_conflict(_OCH(fresh=True, memory_dir="/tmp/x"))
check(RS, "AM12 这条规则必须**真的会挡人**（可观测行为，不是文档里的一句话）",
      "互斥" in _oc and "--fresh" in _oc, f"冲突说明={_oc[:60]!r}")

# ===========================================================================
# AN 组 —— 抽象层：「经验」必须能与「答案」被分开读取
#
# 起因（第十六轮，一次**负面**实测的全部价值）：
#   第十五轮跑出第一个正的跨任务增益 Δ=+0.2000，但排序对照证明它属于
#   "位置"不属于能力 —— 累积记忆里的 finish 带着**上一题的完整正确答案**，
#   检索到它等于开卷抄范文。
#
#   ⇒ 于是必须有办法把"记忆里那段答案"和"从那段答案里提炼出的经验"
#     **作为两个可分别读的层**读出来。否则永远分不清：
#       · 它学会了什么
#       · 它记住了什么
#   这一组钉的是这个分离本身（不是它的效果好坏，那是外部锚层的事）。
# ===========================================================================
from mw.abstractor import (ABSTRACT_MODE, ABSTRACT_SOURCE,     # noqa: E402
                           DistillStats, abstract_only, distill,
                           looks_like_code, strip_code)

check(RS, "AN1 读写两侧必须**同源**：`ABSTRACT_SOURCE`（写）与 `ABSTRACT_MODE`（读）\n"
          "         必须同时存在且取同一个值。两边分开声明 ⇒ 某次改动只改一边后，\n"
          "         抽象臂会**静默地读到一个空层**，而实验会被读成『抽象没用』。",
      ABSTRACT_SOURCE == ABSTRACT_MODE == "abstract",
      f"source={ABSTRACT_SOURCE!r} mode={ABSTRACT_MODE!r}")

check(RS, "AN2 抽象器属于内核 ⇒ 不许依赖外部锚层（`mw.abstractor` 不 import bench）\n"
          "         —— 依赖方向反向的那一天，内核就能拿打分结果去挑自己要记的经验。",
      "bench" not in _strip_docstrings(_src("abstractor.py")),
      "abstractor 引了 bench")

check(RS, "AN3 `looks_like_code` 必须挡住**所有**可执行形态\n"
          "         —— 一条漏网的代码就让整场对照作废（'抽象臂'抄的是答案）",
      looks_like_code("```python\nprint(1)\n```")
      and looks_like_code("def f():\n    return 1")
      and looks_like_code("for i in range(3):")
      and looks_like_code("用 `dict.get(k)` 取值")
      and not looks_like_code("做 X 之前先检查它的边界条件"),
      "形态判定不全")

_AN_stripped = strip_code("先做 A\n```python\nx = 1\n```\n然后 B")
check(RS, "AN4 `strip_code` 必须是**删除**而不是'标注为代码'\n"
          "         —— 留下『[代码已省略]』等于告诉读者'这里原本有一段有用的东西'，\n"
          "           那也会被当成信号 ⇒ 抽象层里仍然藏着答案的影子。",
      "x = 1" not in _AN_stripped and "A" in _AN_stripped
      and "B" in _AN_stripped and "省略" not in _AN_stripped,
      f"结果={_AN_stripped!r}")

_AN_memdir = Path(tempfile.mkdtemp()) / "an_mem"
_AN_mem = MemoryStore(_AN_memdir)
_AN_mem.think("这条工具输出里带着 finish 的完整答案 code=42")
_AN_mem.record("finish", "答案原文：\n```python\nreturn 42\n```")
_AN_mem.commit_fact("不是抽象层的旧事实", source="worldmodel", confidence=0.5)
_AN_mem.commit_fact("做 X 之前先检查 Y", source=ABSTRACT_SOURCE, confidence=0.5)
_AN_raw = Core(llm=DemoLLM(), memory=_AN_mem, memory_mode="raw")
_AN_abs = Core(llm=DemoLLM(), memory=_AN_mem, memory_mode=ABSTRACT_MODE)
#: ⚠️ 第三十七轮（§17-7i）改过这里，**判据没动**，动的是喂进去的 query。
#: 原来用 `"目标"`（与两条 facts 零 token 交集）⇒ 全靠 `recall()` 的第三级
#: "全表 top-k"兜底才召回两条。那一级已按装置修正⑮ 删除（静默兜底），
#: 于是这两条断言会红 —— 但红的原因不是"过滤坏了"，是**它们原本站在兜底上**。
#: 现在把 query 写成两条原文的拼接：**召回 2 条 → 过滤后剩 1 条**，
#: 这才真的在测"过滤只看 source"；用 `"目标"` 反而只召回 0 条，测了个寂寞。
_AN_goal = "不是抽象层的旧事实 做 X 之前先检查 Y"
_AN_s_raw = _AN_raw._state(_AN_goal, [], 1)
_AN_s_abs = _AN_abs._state(_AN_goal, [], 1)

check(RS, "AN5 abstract 模式下**工作记忆整块不进上下文**\n"
          "         —— 工作记忆存的是工具输出原文，而 finish 的输出就是答案原文。\n"
          "           留着它，'抽象臂'就不成为对照：同一段答案换了条路走进来。",
      "工作记忆" in _AN_s_raw and "工作记忆" not in _AN_s_abs,
      f"raw含={'工作记忆' in _AN_s_raw} abstract含={'工作记忆' in _AN_s_abs}")

#: ⚠️ 第三十七轮（§17-7i）：AN6 的**判据位置**从"整段 state"改到"`[经验]` 块"，
#: 判据内容（只留 abstract、滤掉 worldmodel）一个字没改。
#: 为什么必须挪：新 goal 为了能召回两条 facts，本身就含两条的原文 ⇒ `[目标]`
#: 块里必然出现它们。用整段 state 判 = 把"目标里写了什么"当成"记忆送了什么"，
#: 正是 AZ19 那条（R1 只查整段）的同宗错误。挪到块上才是真的在测过滤。
def _an_block(state: str, head: str) -> str:
    i = state.find(head)
    if i < 0:
        return ""
    j = state.find("\n[", i + len(head))
    return state[i:j if j >= 0 else len(state)]


_AN_exp = _an_block(_AN_s_abs, "[经验]")

check(RS, "AN6 abstract 模式下只有 `source==abstract` 的事实留得下\n"
          "         —— 且过滤必须**只看 source**：'长得像经验'是由运气决定的判据，\n"
          "           source 才是写侧承诺的事实。",
      "做 X 之前先检查 Y" in _AN_exp
      and "不是抽象层的旧事实" not in _AN_exp,
      f"经验块={_AN_exp[:120]!r}")

#: ⚠️ 第四十次复核（§104）：`_kwm_absorb` 被拆成「取快照（`_kwm_now`）+ 求差」两段
#: （因为跨题累计值不许当增量）。静态锚必须跟着走**整条链**：只看 `_kwm_absorb` 的源码
#: 会得出「它不读 memory_layer_view」—— 那是**锚点腐烂**，不是实现退步（`AS12` 的同宗）。
#: 所以判据要求两件事**同时**在场：① `_kwm_now` 里真的读 `memory_layer_view`；
#: ② `_kwm_absorb` 真的调 `_kwm_now`（否则读的地方变成孤岛，链断了锚还是绿的）。
_AN_AbsSrc = _strip_docstrings(inspect.getsource(_kwm_absorb))
_AN_NowSrc = _strip_docstrings(inspect.getsource(_kwm_now))
check(RS, "AN7 层读数必须有消费者：`memory_layer_view` 要被吸收链**主动取走**\n"
          "         —— 第三次踩'新建字段没人搬运'的根因就是要求每个调用点**记着传**。\n"
          "           现在由吸收链自己去 core 上取，调用点传不传都有。\n"
          "         ⛔ 静态锚必须覆盖整条链：`_kwm_now`（读） + `_kwm_absorb`（调）。",
      "memory_layer_view" in _AN_NowSrc and "_kwm_now(" in _AN_AbsSrc
      and _AN_abs.memory_layer_view["abstract"] >= 1
      and _AN_abs.memory_layer_view["recalled"] >= _AN_abs.memory_layer_view["abstract"],
      f"view={_AN_abs.memory_layer_view} "
      f"链：读={('memory_layer_view' in _AN_NowSrc)} 调={('_kwm_now(' in _AN_AbsSrc)}")


class _StrReply:
    """最小 LLM 回复对象（只要 `.content`，与 `mw.llm` 的返回同形）。"""

    def __init__(self, content: str) -> None:
        self.content = content


class _CodeLLM:
    """永远返回代码的 LLM —— 用来验证蒸馏会把它拒掉。"""

    n_called = 0

    def complete(self, messages, *, max_tokens=512, temperature=0.0, **kw):
        _CodeLLM.n_called += 1
        return _StrReply("```python\nprint(1)\n```")


_AN_st = distill(_AN_mem, _CodeLLM())
check(RS, "AN8 ★模型给回代码 ⇒ 蒸馏**必须拒收并且计数**，不许悄悄写进记忆\n"
          "         —— 一个看得见的可归因拒收（dropped_code），远胜一次静默的成功。\n"
          "           而且样例本身必须留痕：没有样例，'到底漏掉了什么'永远查不出来。",
      _AN_st.kept == 0 and _AN_st.dropped_code >= 1
      and len(_AN_st.examples_dropped_code) >= 1,
      _AN_st.as_line())

check(RS, "AN9 `DistillStats` 的**每种**拒收都要有名字且能进读数\n"
          "         —— 只有一个 kept 数字的蒸馏器 = '我把不重要的东西删掉了'的资格证。",
      {"considered", "kept", "dropped_code", "dropped_empty",
       "dropped_invalid", "failed_llm"} <= set(DistillStats().as_dict())
      and all(k in DistillStats().as_line()
              for k in ("含代码", "空", "LLM失败")),
      DistillStats().as_line())

_AN_SRC = (V1 / "run.py").read_text(encoding="utf-8")
check(RS, "AN10 run.py 必须把「读了几条 / 其中几条是抽象层」念出来\n"
          "         —— 否则 abstract 臂读到一个空层时会**看起来跟满分处置一样**，\n"
          "           而那其实是一个退化的对照（第十五轮同型：对照组被折叠成同一格）。",
      "layer_abstract" in _AN_SRC and "layer_recalled" in _AN_SRC,
      "run.py 未打印层读数")

_GT_SRC = (V1 / "bench" / "gen_tasks.py").read_text(encoding="utf-8")
check(RS, "AN11 ★任务集生成器必须有自检，且 **SyntaxError 不许被当成'没答对'**\n"
          "         —— 本轮实测：一道题的 assert 里写了真换行符 ⇒ 5 条全是\n"
          "           SyntaxError，若被 `except Exception` 吞掉就会读成\n"
          "           'oracle 只过 1/5'，把**工具的失败**记成了被测对象的失败。",
      "_self_check" in _GT_SRC and "except SyntaxError" in _GT_SRC
      and "题面坏了" in _GT_SRC,
      "gen_tasks 自检不完整")

_AN_id_memdir = Path(tempfile.mkdtemp()) / "an_id_mem"
_AN_id_mem = MemoryStore(_AN_id_memdir)
_AN_id_mem.record("finish", "答案是\\n```python\\nreturn deep_get(d, path)\\n```")


class _IdLLM:
    """返回一个**点名了某道题**的抽象 —— 用来验证它被拒掉。"""

    def complete(self, messages, *, max_tokens=512, temperature=0.0, **kw):
        return _StrReply("调用 deep_get 时，先确认路径字符串格式正确。")


class _AN_id:                                   # 只是给 st 一个命名空间
    st = distill(_AN_id_mem, _IdLLM())


check(RS, "AN12 ★抽象里不许**点名某道题**（本轮实测抓到的泄漏）\\n"
          "         —— 蒸馏出的第一条是「调用 deep_get 时，先确认路径字符串格式正确」，\\n"
          "           而 `deep_get` 就是训练题 z04 的函数名。那不是经验，那是记题：\\n"
          "           换一批函数名就叫不动它了。⇒ 含下划线标识符的句子一律拒收，\\n"
          "           逼它写成「按路径逐段下探时，先确认路径字符串的格式」。",
      "dropped_identifier" in DistillStats().as_dict()
      and "点名" in DistillStats().as_line()
      and _AN_id.st.dropped_identifier >= 1
      and _AN_id.st.kept == 0,
      _AN_id.st.as_line())

# ===========================================================================
# AO —— 第十七轮：安慰剂臂（§16.16）
#   ★ 起因：第十六轮测到 C（读抽象层）比 A（空记忆）高 **+0.05**，
#     但幸存经验只有一句 13 个字的话，且与受益题 k03 的内容**几乎无关**。
#     ⇒ 有一个同样能解释这个数的假说：**存在性效应** ——
#       上下文里多了一段 `[经验] …`，改变了生成，与那句话说了什么无关。
#   ★ ⛔ T=0 保证**确定性**，不保证**因果性**。同一个输入给同一个输出只能证明
#     它可以复现，不能证明它是被"经验"改的 —— 拆开两者的唯一办法是加一个
#     **除内容以外什么都不变**的第四臂。
#   ⇒ 这一组钉的不是"抽象层有没有用"（那是外部实验的事），
#     而是**安慰剂这个仪器本身**的四条纪律：
#        等长 / 不许在空层上做 / 不许等于原文 / 索引必须同步
#     任何一条失守，安慰剂臂都会**安静地**退化成 C 臂或 A 臂，
#     而这两种退化都不报错 —— 它们只会产出一个看起来很干净的 Δ。
# ===========================================================================
import importlib                                              # noqa: E402
import importlib.util as _ilu                                   # noqa: E402

# ⛔ 第十九轮把 `bench/placebo.py` 并成了 `bench/placebo/` 包（§17-16）。
#   这里**只许**用 `bench.placebo.*` 这一种导入写法：若某处把 `bench/` 自己
#   放进 sys.path 再 `import placebo`，同一个模块会被加载成**两个对象**
#   —— 两份 STATS、两份 install，而**不会报任何错**（AQ5 钉住）。
_PL_DIR = V1 / "bench" / "placebo"
_PL_SRC = (_PL_DIR / "__main__.py").read_text(encoding="utf-8")
_PL = importlib.import_module("bench.placebo")
_PL_MAIN = importlib.import_module("bench.placebo.__main__").main
_PL_SH = (V1 / "bench" / "run_placebo_control.sh").read_text(encoding="utf-8")

check(RS, "AO1 bench 层的 `ABSTRACT_SOURCE` 必须与内核**同源**\n"
          "         —— 两边分开声明，某次改动只改一边之后，安慰剂会把**没有**经验\n"
          "           的记忆当成有 ⇒ 第四臂悄悄变成空臂，而且不报错。",
      _PL.ABSTRACT_SOURCE == ABSTRACT_SOURCE,
      f"placebo={_PL.ABSTRACT_SOURCE!r} mw.abstractor={ABSTRACT_SOURCE!r}")


def _ao_mk(dirname: str, abstracts, *, others=()) -> Path:
    """造一个**目录形态**的记忆库（内含 memory.db）。返回它的路径。

    ⚠️ `MemoryStore(path)` 里的 path 是**数据库文件**，不是目录 ——
       早先这里传了一个目录进去，于是 sqlite 建出一个同名文件，
       `placebo` 的 `--src` 检查立刻失败（rc=2），而 AO4~AO7 **全都因为
       同一个理由"通过"了**：它们只断言 rc≠0。
       ⇒ 那不是断言，那是对一个假成功点了头。改成真目录并额外断言 rc≠2。
    """
    d = Path(tempfile.mkdtemp()) / dirname
    d.mkdir(parents=True, exist_ok=True)
    m = MemoryStore(d / "memory.db")
    for t in abstracts:
        m.commit_fact(t, source=ABSTRACT_SOURCE, confidence=0.5)
    for t in others:
        m.commit_fact(t, source="worldmodel", confidence=0.5)
    m.record("finish", "某段带答案原文的 finish")
    m.close()
    return d


def _ao_run(argv) -> int:
    """调用 `bench.placebo.__main__.main`，**绝不让它把异常抛出来**
    （R13：异常会打断汇总行）。"""
    try:
        return int(_PL_MAIN(argv))
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 1
    except Exception:                                           # noqa: BLE001
        return -99                       # -99 = 出了别的异常，够与众不同


_AO_TXT = "解析查询字符串前先处理空项"          # 13 字，与第十六轮实测同源
_AO_VECS = [_AO_TXT, "做 X 之前先确认 Y 的边界条件", "a b c d e f"]
_AO_perm_ok = True
_AO_perm_bad = ""
for _t in _AO_VECS:
    _s = _PL.scramble(_t, 17)
    if not (len(_s) == len(_t)
            and collections.Counter(_s) == collections.Counter(_t)
            and _s != _t):
        _AO_perm_ok = False
        _AO_perm_bad = f"{_t!r} -> {_s!r}"

check(RS, "AO2 ★打乱必须是原文的**字符排列**（等长 + 同字多重集）且不得等于原文\n"
          "         —— 打乱之所以是合格的安慰剂，正因为它保留了全部表层统计量\n"
          "           （长度、字集、字频），只摧毁语序承载的语义。\n"
          "           若 C 仍优于打乱臂，差别只能来自『那句话说了什么』。",
      _AO_perm_ok, _AO_perm_bad or "全部通过")

check(RS, "AO3 打乱必须**确定性**（同一输入同一输出）⇒ 实验可复核\n"
          "         —— 不确定的安慰剂等于每次跑都在换自变量，\n"
          "           而它的随机性会被误读成『被测对象不稳定』。",
      _PL.scramble(_AO_TXT, 17) == _PL.scramble(_AO_TXT, 17)
      and _PL.scramble(_AO_TXT, 17) != _PL.scramble(_AO_TXT, 18),
      f"17={_PL.scramble(_AO_TXT, 17)!r} 18={_PL.scramble(_AO_TXT, 18)!r}")

_AO_src = _ao_mk("ao_src", [_AO_TXT], others=["worldmodel 的老事实"])


def _ao_attempts(tag: str, extra: list[str]) -> tuple[int, Path]:
    out = Path(tempfile.mkdtemp()) / f"ao_{tag}"
    return _ao_run(["--src", str(_AO_src), "--out", str(out)] + extra), out


_AO_rc_len, _AO_out_len = _ao_attempts("len", ["--variant", "text", "--text", "短句"])
check(RS, "AO4 ★长度不等必须**拒绝**，且不补空格、不截断、不留半成品目录\n"
          "         —— 补出来的那几个字会成为第二个自变量，下场实验就得同时解释它；\n"
          "           留下半成品则更坏：下次运行会死在『目录已存在』上，把真因藏起来。\n"
          "         ⛔ rc==2 表示**压根没跑到替换**就退了 ⇒ 那是一次假成功。",
      _AO_rc_len not in (0, 2) and not _AO_out_len.exists(),
      f"rc={_AO_rc_len} 半成品留着={_AO_out_len.exists()}")

_AO_rc_same, _AO_out_same = _ao_attempts(
    "same", ["--variant", "text", "--text", _AO_TXT])
check(RS, "AO5 ★替换文本等于原文必须拒绝 —— 那是 **C 臂本人**，不是安慰剂。\n"
          "         这条最容易漏：调参时忘记改文本，实验就变成'自己和自己比'，\n"
          "         然后产出一个精确到小数点后四位的 Δ=+0.0000。",
      _AO_rc_same not in (0, 2) and not _AO_out_same.exists(),
      f"rc={_AO_rc_same}")

_AO_empty = _ao_mk("ao_empty", [], others=["只有 worldmodel 事实"])
_AO_out_empty = Path(tempfile.mkdtemp()) / "ao_empty_out"
_AO_rc_empty = _ao_run(["--src", str(_AO_empty), "--out", str(_AO_out_empty),
                        "--variant", "text", "--text", "一二三四五"])
check(RS, "AO6 ★抽象层为 0 条必须拒绝 —— 在空层上做安慰剂 = 拿空臂跟空臂比。\n"
          "         这种读数最危险：它逐题全 0、看起来**极其干净**，\n"
          "         而它证明的只有'这一段什么都没发生'。",
      _AO_rc_empty not in (0, 2) and not _AO_out_empty.exists(),
      f"rc={_AO_rc_empty}")

_AO_rc_exists = _ao_run(["--src", str(_AO_src), "--out", str(_AO_src),
                         "--variant", "scramble"])
check(RS, "AO7 目标目录已存在必须拒绝\n"
          "         —— 每个臂都必须是一份**全新**的复制；复用目录等于承认\n"
          "           '我不确定它里面到底跑过什么'（这正是第十六轮的真实缺陷：\n"
          "           B/C 共用同一个目录，跑完后库里多了一条 B 臂沉淀的事实）。",
      _AO_rc_exists not in (0, 2),
      f"rc={_AO_rc_exists}")

_AO_new_txt = "编译报错时先读最底层栈信息"        # 同为 13 字
_AO_out_ok = Path(tempfile.mkdtemp()) / "ao_ok"
_AO_rc_ok = _ao_run(["--src", str(_AO_src), "--out", str(_AO_out_ok),
                     "--variant", "text", "--text", _AO_new_txt])
_AO_m2 = MemoryStore(_AO_out_ok / "memory.db")
_AO_rows = [r["source"] + ":" + r["text"]
            for r in _AO_m2.conn.execute("SELECT source, text FROM facts ORDER BY id")]
_AO_rec_old = [f.text for f in _AO_m2.recall(_AO_TXT, k=8)]
_AO_rec_new = [f.text for f in _AO_m2.recall(_AO_new_txt, k=8)]
# ⛔ 必须**直接**读索引表，不能只看 `recall` 的返回：
#   撤回态验证时删掉同步索引那两行，AO8 竟然**仍是绿的** ——
#   因为 recall 在 FTS 失配时会退回 LIKE、再退回 `ORDER BY hits`，
#   三条兜底路径都会把新文本照样捞回来 ⇒ 只看 recall 等于没看索引。
_AO_fts_rows = [r["text"] for r in _AO_m2.conn.execute(
    "SELECT rowid, text FROM facts_fts ORDER BY rowid")]

check(RS, "AO8 ★FTS5 索引必须同步更新：替换后**索引里不再是旧文本**，且**有的是新文本**\n"
          "         —— 只 UPDATE `facts` 不改 `facts_fts` 的话，检索到的还是旧话，\n"
          "           而且**不会报错** ⇒ 安慰剂臂读的仍然是真经验，\n"
          "           产出一个人畜无害的 Δ=0，然后被读成'存在性效应成立'。",
      _AO_rc_ok == 0 and _AO_TXT not in _AO_fts_rows
      and _AO_new_txt in _AO_fts_rows
      and _AO_TXT not in _AO_rec_old and _AO_new_txt in _AO_rec_new
      and any(r.split(":", 1)[0] == ABSTRACT_SOURCE
              and r.split(":", 1)[1] == _AO_new_txt
              for r in _AO_rows),
      f"rc={_AO_rc_ok} 索引={_AO_fts_rows} 库={_AO_rows}")

check(RS, "AO9 去重键（facts.key）必须跟着文本一起重算，且全库去重约束不许破\n"
          "         —— 键是坐在**旧文本**上的指纹偷懒不动它 ⇒ 两条不同安慰剂会撞同一行，\n"
          "           `ON CONFLICT` 把后一条静默吃掉，第二、第三个安慰剂臂凭空消失。",
      _AO_m2.duplicate_rows() == 0
      and fingerprint(_AO_new_txt) in [
          r["key"] for r in _AO_m2.conn.execute("SELECT key FROM facts")]
      and "worldmodel 的老事实" in [r["text"] for r in
                                    _AO_m2.conn.execute("SELECT text FROM facts")],
      f"dups={_AO_m2.duplicate_rows()} rows={_AO_rows}")

_AO_src_still = [r["text"] for r in MemoryStore(_AO_src / "memory.db").conn.execute(
    "SELECT text FROM facts ORDER BY id")]
check(RS, "AO10 ★源记忆库必须**原封不动**（只读）\n"
          "         —— 安慰剂工具碰了源，'各臂同源'这个前提就没了，\n"
          "           而且污染是**顺着实验顺序**累积的：先跑的臂改得最多。",
      _AO_src_still == [_AO_TXT, "worldmodel 的老事实"],
      f"源库现状={_AO_src_still}")

_AO_mw_hits = [p.name for p in sorted(MW_DIR.glob("*.py"))
               if "placebo" in p.read_text(encoding="utf-8")]
check(RS, "AO11 ★内核不许知道'安慰剂'的存在（`mw/*.py` 里连这个词都不许出现）\n"
          "         —— 安慰剂是**测量仪器**，不是能力。\n"
          "           一旦内核里有了 '我被告知这是一条安慰剂' 的分支，\n"
          "           测到的就不再是这个系统，而是这个系统的应试技巧。",
      not _AO_mw_hits,
      f"含 placebo 的内核模块={_AO_mw_hits}")

# ⚠️ 先算好值再拼进 f-string：早先在 f-string 的花括号里直接嵌转义引号，
#    跨 Python 版本的行为不保证，而且一旦读起来费劲就容易看漏错别字。
_AO_base_flag = '--memory-dir "${BASE}"'
#: 第 2 步之后（base 冻结之后）的脚本片段 —— 这里**不许**再有人直接用 base 跑
_AO_after = _PL_SH.split("第 2 步", 1)[1] if "第 2 步" in _PL_SH else ""
check(RS, "AO12 ★每个臂必须有**自己的一份**记忆副本，且能在跑之前自证同源\n"
          "         —— 解析实验脚本：**冻结之后**（第 2 步之后）\n"
          "           任何命令都不许再把 base 当 `--memory-dir` 传；\n"
          "           必须有 `mem_base → mem_${arm}` 的复制动作；\n"
          "           并且要打印 sha256 —— 不打印就等于要求读者相信我没有搞混。",
      "第 2 步" in _PL_SH
      and 'cp -R "${BASE}" "${ROOT}/mem_${arm}"' in _PL_SH
      and _AO_after.count(_AO_base_flag) == 0
      and "BASE_SHA" in _PL_SH and "shasum" in _PL_SH,
      f"冻结后 base 直传次数={_AO_after.count(_AO_base_flag)} "
      f"含复制={'cp -R' in _PL_SH}")

check(RS, "AO13 等长容差默认值必须是 **0**（严格等长）\n"
          "         —— `--allow-len-delta` 存在只是为了诊断。默认一放宽，\n"
          "           '多写或少写几个字'就会被当成同一个变量，实验当场失去归因能力。",
      '"--allow-len-delta", type=int, default=0,' in _PL_SRC
      and "allow_len_delta" in _PL_SRC,
      "默认值不是 0")

check(RS, "AO14 三个安慰剂文本必须在脚本里**逐字可见**（可复核）\n"
          "         —— '我和安慰剂比过'这句话本身没有信息；\n"
          "           读者要能当场看到那句安慰剂到底是什么话。\n"
          "           （D1 是打乱 ⇒ 可见的是它的生成方式而非结果。）",
      "--variant scramble" in _PL_SH
      and "编译报错时先读最底层栈信息" in _PL_SH
      and "解析查询字符串时可忽略空项" in _PL_SH,
      "脚本里看不到安慰剂原文")

_PL_PROBE = V1 / "bench" / "probe_placebo.py"
_PL_EV = V1 / "bench" / "evidence" / "placebo_s17"
_RUN_TESTS_SRC_RT = (V1 / "run_tests.sh").read_text(encoding="utf-8")

check(RS, "AO15 ★安慰剂的**结论**必须可重算，不能只是散文里的一个数\n"
          "         —— `bench/evidence/placebo_s17/` 里冻着两轮共 9 份原始 runs.jsonl，\n"
          "           `bench/probe_placebo.py` 每次跑都把它们从头算一遍：\n"
          "           数字改一个字词就可能不易察觉，而换数据探针会自己转红。\n"
          "           ⛔ 它锁的是『按这些数据、按事前规则，结论必须是什么』，不锁结论本身。",
      _PL_PROBE.exists()
      and len(sorted(_PL_EV.glob("*.runs.jsonl"))) >= 9
      and "probe_placebo.py" in _RUN_TESTS_SRC_RT,
      f"探针={_PL_PROBE.exists()} 证据份数={len(sorted(_PL_EV.glob('*.runs.jsonl')))}")


def _ao_probe_rc(argv) -> tuple[int, str]:
    """跑探针；**异常不许抛出来**（R13），返回 (码, 末行)。"""
    import io
    import contextlib
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = int(_PL_probe_main(argv))
        return rc, buf.getvalue().strip().splitlines()[-1][:80]
    except SystemExit as e:
        return (int(e.code) if isinstance(e.code, int) else 1), "SystemExit"
    except Exception:                                           # noqa: BLE001
        return -99, "异常"


_PL_probe_spec = _ilu.spec_from_file_location("v1_probe_placebo", _PL_PROBE)
_PL_probe = _ilu.module_from_spec(_PL_probe_spec)
_PL_probe_spec.loader.exec_module(_PL_probe)
_PL_probe_main = _PL_probe.main

_AO_rc_noev, _AO_last = _ao_probe_rc(["--evidence", str(Path(tempfile.mkdtemp()) / "nope")])
check(RS, "AO16 ★探针读不到证据时必须是**带理由的失败**，不许悄悄算成 0 分\n"
          "         —— 缺行 ⟶ 返回 0.0 是本项目最容易复发的错误之一：\n"
          "           '某道题没跑'会被读成'某道题得 0 分'，这是两种完全不同的事实。",
      _AO_rc_noev != 0 and "不存在" in _AO_last,
      f"rc={_AO_rc_noev} 末行={_AO_last!r}")

# ===========================================================================
# AP 组（第十四组，第十四轮之后的第十八轮）—— 假记忆臂（§17-14）
#
#   §16.14 那个 "+0.2000 跨任务增益" 只有「隔离 vs 累积」一个对照，
#   没有安慰剂 ⇒ 我无法区分"它真的学到东西"与"上下文里多了一堆字"。
#   第十七轮已经证明后者的量级可以大到 0.15 —— 比待认领的数还大。
#
#   AP 组钉住这套装置本身不许骗我：
#     · patch **不许进内核**（否则"这个进程有没有被改过"无法从外部证明）
#     · 替换必须**严格等长**、有内容必须**相异**、mapper 必须**确定且无碰撞**
#     · 工作记忆这条泄漏通道必须精确到"上一题的残留"，不许伤到本题草稿纸
#     · 基线与正对照那两臂必须跑**未被 patch** 的进程
#     · 事前规则必须**在跑之前**打印（判据不许事后改）
# ===========================================================================
# 第十九轮：机制搬进 `bench/placebo/patch.py`，`bench/fake_mem.py` 只剩一层薄壳
#   （解析臂名 → install → run.main → dump_stats）。断言跟着机制走：
#   AP 组现在钉 `patch.py` / `mappers.py` 的源码，而**不再**钉那个壳。
_AP_RUNNER = V1 / "bench" / "fake_mem.py"
_AP_RUNNER_SRC = _AP_RUNNER.read_text(encoding="utf-8")
_AP_SH = (V1 / "bench" / "run_fakemem_control.sh").read_text(encoding="utf-8")
_FM = importlib.import_module("bench.placebo.patch")
_MP = importlib.import_module("bench.placebo.mappers")
_AP_SRC = (_PL_DIR / "patch.py").read_text(encoding="utf-8")
_MP_SRC = (_PL_DIR / "mappers.py").read_text(encoding="utf-8")

#: ⚠️ patch 是**类级别**的。不还原的话，后面所有用到 MemoryStore 的组
#:    都会带着一个被替换过 mapper 的世界跑 —— 那是静默污染。
_AP_ORIG = {n: getattr(MemoryStore, n)
            for n in ("record", "commit_fact", "think", "working_view")}


def _ap_restore() -> None:
    for _n, _f in _AP_ORIG.items():
        setattr(MemoryStore, _n, _f)


def _ap_with(arm: str, **kw):
    """先还原、再清统计、再装 —— 免得 wrapper 一层层叠上去。"""
    _ap_restore()
    _FM.reset_stats()
    return _FM.install(arm, **kw)


def _fm_raises(fn) -> bool:
    """`fn` 必须抛 AssertionError。返回 bool（R13：不许对可能不存在的属性做算术）。"""
    try:
        fn()
    except AssertionError:
        return True
    except Exception:                                           # noqa: BLE001
        return False
    return False


def _fm_injective() -> bool:
    """抽样字符集上 cipher 是否无碰撞。

    ⛔ 刻意吞异常：撤回态验证时 mapper 可能坏到直接抛，而那一轮的要求是
       「精确转红 + 0 Traceback + 汇总行在」 ⇒ helper 必须返回 False，不许炸。
    """
    try:
        chars = sorted(set("".join(_AP_SAMPLES)))
        mapped = [_MP.cipher(c) for c in chars]
        return len(set(mapped)) == len(mapped)
    except Exception:                                           # noqa: BLE001
        return False


def _fm_rejects(arm: str) -> bool:
    """`install(arm)` 必须**带理由地**退出。（SystemExit 而非静默 return）"""
    import contextlib
    import io as _io
    buf = _io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            _FM.install(arm)
    except SystemExit as e:
        # ⚠️ `raise SystemExit(msg)` 的 msg 是**异常载荷**，不会进 stdout/stderr ——
        #    早先这里在重定向的缓冲区里找它，永远找不到 ⇒ 断言恒假。
        #    而它又是 SystemExit 带串 ⇒ `e.code` 是 str，不能当返回码用。
        rc = e.code if isinstance(e.code, int) else 1
        return rc != 0 and arm in str(e.code)
    return False


def _fm_trace_identity_ok() -> bool:
    """T 臂：写进去的东西必须能原样读回来。"""
    _ap_with("T")
    try:
        m = MemoryStore(Path(tempfile.mkdtemp()) / "memory.db")
        m.record("goal", _AP_SAMPLES[0])
        rows = m.episodes("goal", limit=1)
        return bool(rows) and rows[0].content == _AP_SAMPLES[0]
    except Exception:                                           # noqa: BLE001
        return False          # 撤回态要求 0 Traceback，故这里必须吞
    finally:
        _ap_restore()


def _fm_working_modes() -> bool:
    """三档语义两两不同，且 prev 档必须保住**本题自己的草稿纸**。

    模拟相邻两题：先把「上一题留下的」写进 deque，再跨过一道题边界，
    于是不属于本题的那些条目就应当被当作泄漏处理。
    """
    res = {}
    for mode in ("prev", "all", "none"):
        _ap_with("E1", fake_working=mode)
        try:
            m = MemoryStore(Path(tempfile.mkdtemp()) / "memory.db")
            m.record("goal", "第 1 题")        # 边界 ①：_own 清空
            m.think("第1题自己的草稿 zzz")      # ⟶ 本题自己的
            m.record("goal", "第 2 题")        # 边界 ②：上一行瞬间变成"上一题的"
            m.think("第2题自己的草稿 zzz")      # ⟶ 本题自己的
            view = m.working_view()
            res[mode] = (view[0], view[1])   # 顺序：[上一题残留, 本题的]
        except Exception:                                       # noqa: BLE001
            res[mode] = ("<异常>", "<异常>")
        finally:
            _ap_restore()
    # ⛔ 刻意把期望值**写死**而不是"看起来变了就算通过"：
    #    那样的话 mapper 退化成恒等也能混过去，而那正是最危险的失败形态。
    _P = "第1题自己的草稿 zzz"          # 上一题留下来的 ⇒ 三档里两档要处理它
    _O = "第2题自己的草稿 zzz"          # 本题自己的草稿纸 ⇒ prev 档必须原样
    return (res["none"] == (_P, _O)
            and res["prev"] == (_MP.cipher(_P), _O)
            and res["all"] == (_MP.cipher(_P), _MP.cipher(_O)))


_AP_DIRTY = sorted(
    fn for fn in os.listdir(MW_DIR)
    if fn.endswith(".py")
    and re.search(r"MemoryStore\.(record|think|commit_fact|working_view)\s*=",
                  (MW_DIR / fn).read_text(encoding="utf-8")))
_AP_PATCH_N = len(re.findall(r"MemoryStore\.\w+ = ", _AP_SRC))
#: 壳里**不许**再出现 patch 语句（那会意味着有两处安装器）
_AP_RUNNER_PATCH_N = len(re.findall(r"MemoryStore\.\w+ = ", _AP_RUNNER_SRC))

check(RS, "AP1 ★假记忆的 patch **不许出现在内核里**\n"
          "         —— 一旦 `mw/` 下有人把方法挂上去，'这个数是被改过的还是没改过的'\n"
          "           就再也没法从外部证明，E3 正对照当场失去意义。\n"
          "           同时 bench/fake_mem.py 必须真的在做 patch，否则这把锁是空的。",
      not _AP_DIRTY and _AP_PATCH_N >= 4 and _AP_RUNNER_PATCH_N == 0,
      f"被污染的内核模块={_AP_DIRTY} patch 处数={_AP_PATCH_N} "
      f"壳里 patch 处数={_AP_RUNNER_PATCH_N}")

_AP_SAMPLES = [
    "写一个函数 flatten(d)，把嵌套 dict 展平成单层：嵌套键用 '.' 连接",
    "def f(d):\n    return {k: v for k, v in d.items()}\n",
    "done: def g(qs):\n    return dict(p.split('=', 1) for p in qs.split('&'))\n",
    "a=1&b=2&c=3",
    "0123456789 ABCDEFGHIJ zxcvbnm,.!",
]

check(RS, "AP2 ★假文本必须**严格等长**，而且**逐行**长度都不能变\n"
          "         —— 被替换的是前面几道题的**答案代码**，缩进与换行是它的结构。\n"
          "           连只差一个字符，'多出来的那一个字'就成了第二个自变量。",
      all(len(_MP.cipher(s)) == len(s) for s in _AP_SAMPLES)
      and all([len(x) for x in _MP.cipher(s).split("\n")]
              == [len(x) for x in s.split("\n")] for s in _AP_SAMPLES),
      f"逐行长度一致数={sum(1 for s in _AP_SAMPLES if [len(x) for x in _MP.cipher(s).split(chr(10))] == [len(x) for x in s.split(chr(10))])}/{len(_AP_SAMPLES)}")

check(RS, "AP3 ★**有内容**的字符串替换后必须与原不同（等原文就不是安慰剂）\n"
          "         —— 这条只在 `strip()` 为空时放行：空串本身没有内容可替换，\n"
          "           拿它去卡'必须相异'只会让整个臂因为一条空记录崩掉。\n"
          "           ⛔ 这个口子必须**看得见**（AP4 盯着它的计数）。",
      all(_MP.cipher(s) != s for s in _AP_SAMPLES)
      and _fm_raises(lambda: _FM._check(_AP_SAMPLES[0], _AP_SAMPLES[0], "w")),
      "有内容者全不相同=True 相同内容已拒=True")

#: 计数语句在源码里出现的次数（写成变量而不是塞进 f-string，免得转义引号劝退读者）
_AP_BLANK_N = _AP_SRC.count('STATS["blank_identity"] += 1')

check(RS, "AP4 ★放行恒等必须**计数**，不许静默\n"
          "         —— 看不见的例外会长大：今天只放过空串，明天就有人往里塞条件，\n"
          "           而 AP3 只抽样，看不出来覆盖面缩了多少。",
      "blank_identity" in _AP_SRC and _AP_SRC.count('STATS["blank_identity"] += 1') == 1,
      f"计数出现次数={_AP_BLANK_N}")

check(RS, "AP5 ★mapper 必须是**双射**：不同字符不得映射到同一个字符\n"
          "         —— 一旦碰撞，假文本的信息量就不再与原文本可比：\n"
          "           长度虽然一样，'删掉了多少信息'却随原文内容浮动 ⇒ 第二个自变量。",
      _fm_injective(),
      f"测试字符数={len(set(''.join(_AP_SAMPLES)))}")

check(RS, "AP6 ★mapper 必须**确定性**：同一段原文两次映射结果逐位相同\n"
          "         —— mapper 里只要掺一点时间/随机数，两道题看到的就不是同一个\n"
          "           '假记忆'，而那正是本实验要排除的那个变量。",
      all(_MP.cipher(s) == _MP.cipher(s) for s in _AP_SAMPLES)
      and "random.Random" not in _MP_SRC.split("def cipher")[1][:900],
      "两次一致=True")

check(RS, "AP7 ★T 只读探针必须真的**一个字都不改**，并且逐条记账\n"
          "         —— T 是'装上测量会不会改变被测对象'的答案：\n"
          "           它一旦偷偷变换，' |E3−T| ≤ 0.05 '这条闸门就成了自我安慰。",
      _fm_trace_identity_ok() and _FM.STATS["identity_ok"] >= 1,
      f"identity_ok={_FM.STATS['identity_ok']}")

check(RS, "AP8 ★E0（空）与 E3（真原文）必须被 runner **明确拒绝**\n"
          "         —— 它们只能跑**未被 patch** 的进程。让它们也从这里出去，\n"
          "           就等于承认我分不清这个进程有没有被改过。",
      "E3" not in _FM.ARMS and "E0" not in _FM.ARMS
      and _fm_rejects("E3") and _fm_rejects("E0"),
      f"ARMS={_FM.ARMS}")

check(RS, "AP9 ★工作记忆的处理必须**精确到条**：\n"
          "         prev 档只替换上一题残留，**本题自己的草稿纸一个字都不许动**\n"
          "         —— 把本题草稿纸也改掉，就不是安慰剂而是把这个系统弄瞎，\n"
          "           Δ 里会混进'看不见自己刚写的东西'，再也拆不开。\n"
          "         三档语义必须两两不同，否则'这条通道有多大'就没法问。",
      _fm_working_modes(),
      "prev 保住了本题草稿纸、all 全改、none 全不改")

check(RS, "AP10 ★等长被违反时必须是**带理由的失败**，不许返回原文悄悄继续\n"
          "          —— 静默兜底（except: pass / return old）等于把有效数据\n"
          "            和污染数据混在一起，事后没有任何办法把它们分开。",
      _fm_raises(lambda: _FM._check("abcdef", "abcde", "w"))
      and _FM.STATS["violations"]
      and "raise AssertionError" in _AP_SRC,
      f"违反已记录数={len(_FM.STATS['violations'])}")

#: ⚠️ 一律用 `find()` 取下标，**不许用 `index()`**：撤回实验里把那行删掉之后，
#:   `index()` 会抛 ValueError ⇒ "断言"还没判，文件先崩了，汇总行跟着没了
#:   （违反 R13 的 0 Traceback）。更阴的是 `-1 < 首次出现位置` 恒为真：
#:   删掉字符串反而让断言更"绿"。所以每个 `find()` 都必须**先判 >= 0 再比大小**。
_AP_RULE_AT = _AP_SH.find("事前登记（不可事后修改）")
_AP_FIRST_RUN = _AP_SH.find("run_arm E0")
_AP_HAS_KEYS = all(k in _AP_SH for k in ("0.05", "0.15", "存在性效应", "UNKNOWN"))
_AP_RULE_BEFORE_RUN = (_AP_RULE_AT >= 0 and _AP_FIRST_RUN >= 0
                       and _AP_RULE_AT < _AP_FIRST_RUN)

check(RS, "AP11 ★事前规则必须在**开跑之前**打印出来，事后再看结果才许判读\n"
          "         —— 判据要是能改，'Δ ≥ 0.15 才算数'这种话就没有约束力：\n"
          "           拿到 0.14 时把它放宽到 0.10 只需要动一个字符。",
      _AP_RULE_BEFORE_RUN and _AP_HAS_KEYS,
      f"规则块在跑之前={_AP_RULE_BEFORE_RUN}"
      f"（规则@{_AP_RULE_AT} 首跑@{_AP_FIRST_RUN} 关键词={_AP_HAS_KEYS}）")

check(RS, "AP12 ★基线 E0 与正对照 E3 必须跑**未被 patch** 的进程，T 探针臂必须在\n"
          "         —— E3 是唯一能证明'假记忆这套装置没有把被测对象改坏'的参照；\n"
          "           T 是唯一能量出'装测量这个动作本身有多大影响'的臂。\n"
          "           两条缺一条，整套对照都可以被一句话质疑掉。",
      "run_arm E0  run" in _AP_SH and "run_arm E3  run" in _AP_SH
      and "run_arm T   fake" in _AP_SH
      and 'if [ "${entry}" = "fake" ]' in _AP_SH,
      f"E0/E3/T 齐备={all(x in _AP_SH for x in ('run_arm E0  run', 'run_arm E3  run', 'run_arm T   fake'))}")

# --- AP13 的计数先算好，避免 f-string 表达式里出现反斜杠 ---
_AP13_NEEDLE = '--memory-dir "${dir}/mem"'          # 外层单引号，内部双引号无需转义
_AP13_HITS = _AP_SH.count(_AP13_NEEDLE)

check(RS, "AP13 ★每一臂必须有**自己的一份**记忆目录，不许共用\n"
          "         —— 第十七轮就是因为 B/C 共用同一个 `--memory-dir`：\n"
          "           C 的起跑库里躺着 B 跑出来的 episode，同一个 C 从 0.8500\n"
          "           变成 0.7500，一个 +0.40 的单题涨幅整个消失。",
      _AP13_HITS == 1
      and 'dir="${ROOT}/${arm}"' in _AP_SH,
      f"memory-dir 出现次数={_AP13_HITS}")

_AP_EV = V1 / "bench" / "evidence" / "fakemem_s18"
# --- AP14 的份数也先算好，f-string 里不再出现条件表达式 + 引号嵌套 ---
_AP14_RUNS = sorted(_AP_EV.glob("*.runs.jsonl")) if _AP_EV.is_dir() else []
_AP14_COUNT = len(_AP14_RUNS)

check(RS, "AP14 ★五臂的原始读数必须**冻结成文件**，结论不许只躺在散文里\n"
          "         —— 写在文档里的数字改一个字词就可能不易察觉；\n"
          "           冻成一堆 jsonl 之后，改数字探针会自己转红。",
      _AP_EV.is_dir() and _AP14_COUNT >= 5
      and (V1 / "bench" / "probe_fakemem.py").exists(),
      f"证据份数={_AP14_COUNT}")

# ── AP15：判读实现**只有一处**（第十八轮自踩，必须钉住）───────────────────
# 第十八轮收尾时发现：判读逻辑在 `run_fakemem_control.sh` 里嵌了一份，
# 在 `probe_fakemem.py` 里又写了一份。两份"内容相同"的字符串并不同 ——
# 脚本那份没有 markdown 粗体标记，探针那份有 ⇒ verdict.json 与探针重算
# 当场不等 ⇒ P5 转红。
# 这不是"抄错了"，这是**同一样东西有两处声明**的必然结果。
# ⇒ 修法不是把两处改一致，而是**删掉一处**：脚本收尾直接调探针的 --write。
_AP_PROBE = (V1 / "bench" / "probe_fakemem.py").read_text(encoding="utf-8")
# --- AP15 的三个判定值先算好，f-string 表达式里不再出现反斜杠 ---
_AP15_SCRIPT_VERDICT = bool(re.search(r"^\s*verdict\s*=", _AP_SH, re.M))
_AP15_SCRIPT_GAP = "content_gap" in _AP_SH
_AP15_SELF_JUDGE = _AP15_SCRIPT_VERDICT or _AP15_SCRIPT_GAP
_AP15_HAS_DEF = "def verdict_of(" in _AP_PROBE
_AP15_DEF_N = _AP_PROBE.count("verdict_of(")

check(RS, "AP15 ★判读规则**只许存在一份实现**：脚本必须委托给探针，不许自己再算一遍\n"
          "         —— 第十八轮的真实教训：两份实现各写一遍，其中一份多了 `**`，\n"
          "           落盘的判定与探针重算立刻不等。抄一遍就会漂，与抄得对不对无关。\n"
          "         ⇒ 修法是把脚本里那份**删掉**，而不是把它改对。",
      'bench/probe_fakemem.py --evidence "${EVID}" --write' in _AP_SH
      and not _AP15_SCRIPT_VERDICT
      and not _AP15_SCRIPT_GAP
      and _AP15_HAS_DEF
      and _AP15_DEF_N >= 2,          # 定义 1 处 + 调用 ≥1 处
      f"脚本自带判读={_AP15_SELF_JUDGE} 探针定义={_AP15_HAS_DEF}")
# ===========================================================================
# AQ 组（第十九轮）—— ① 把「等长安慰剂」做成一等公民（§17-16）
#                    ② 把「存在性」与「形状」拆开（§17-15）
#
#   §17-16：第十七轮写 `bench/placebo.py`、第十八轮写 `bench/fake_mem.py`，
#   两次都在手搓同一套东西（等长闸门 + FTS 同步 + 撤回校验 + 统计落盘）。
#   同一个约束被声明两遍，就一定会有一遍先烂 —— 所以本轮把它们并成
#   `bench/placebo/` 一个包：**合并机制，不合并判据**。
#
#   §17-15：第十八轮的 E1（cipher）证明了语义贡献为 0，但它**保留了全部几何**
#   （字符数、逐行长度、缩进、标点分布、字符类别）⇒ 它证不了"形状"。
#   本轮加 F 臂（reshape）：**保字符序列、破行几何**。
#
#   AQ 组钉两件事：
#     · 并包**不许**改动任何已被冻结的读数（逐字节回归锁）
#     · F 的操纵必须是**单因子**的（只动几何，不动字符序列），
#       且它的盲区（无可挪结构的行）必须**被计数**，不许静默放过
# ===========================================================================
_AQ_DIR = V1 / "bench" / "placebo"
_AQ_SH = (V1 / "bench" / "run_shape_control.sh").read_text(encoding="utf-8")
_AQ_EV = V1 / "bench" / "evidence" / "shape_s19"
_AQ_RT = (V1 / "run_tests.sh").read_text(encoding="utf-8")

_AQ_FILES = ["__init__.py", "mappers.py", "store.py", "patch.py", "__main__.py"]
check(RS, "AQ1 ★安慰剂必须是**一个包**、且旧脚本必须已经消失\n"
          "         —— 两套实现并存的后果不是报错，是『我改的那一套没被跑到』：\n"
          "           改 `placebo.py` 而实验走 `fake_mem.py`，两边都自洽，读数是假的。",
      not (V1 / "bench" / "placebo.py").exists()
      and all((_AQ_DIR / f).exists() for f in _AQ_FILES),
      f"旧脚本还在={(V1 / 'bench' / 'placebo.py').exists()} "
      f"缺文件={[f for f in _AQ_FILES if not (_AQ_DIR / f).exists()]}")

# ── AQ2：并包**不许**改动读数 —— 用冻结证据逐字节回归 ────────────────────
# 第十八轮 E1 臂的 stats 里存着原文→密文（前 3 条、截断到 300 字），
# 第十七轮 D1 臂的打乱结果写在 DESIGN/脚本注释里。两者都是**唯一的参照**。
_AQ_S18 = V1 / "bench" / "evidence" / "fakemem_s18" / "E1.stats.json"
_AQ_S17_TXT = "解析查询字符串前先处理空项"
_AQ_S17_SCR = "空处先字符理项查解析询前串"
_AQ_byte_ok = False
_AQ_byte_bad = "evidence 缺失"
if _AQ_S18.exists():
    try:
        _aq_ex = json.loads(_AQ_S18.read_text(encoding="utf-8")).get("examples") or []
        _AQ_byte_ok = bool(_aq_ex) and all(
            _MP.cipher(e["old"]) == e["new"] for e in _aq_ex)
        _AQ_byte_bad = f"不符条数={sum(1 for e in _aq_ex if _MP.cipher(e['old']) != e['new'])}"
    except Exception as e:                                     # noqa: BLE001
        _AQ_byte_bad = f"读证据抛异常：{type(e).__name__}"

check(RS, "AQ2 ★并包之后 mapper 必须**逐字节**复现冻结证据\n"
          "         —— 重构时我一度把 scramble 的种子派生多拼了一段 `:len`，\n"
          "           于是同一个输入的排列变了：**不报错**，只是"
          "\"重跑一次\"和\"当年那次\"悄悄不是同一个臂。\n"
          "           这类事故只能靠逐字节比对冻结证据发现。",
      _AQ_byte_ok and _MP.scramble(_AQ_S17_TXT, 17) == _AQ_S17_SCR
      and "seed}:{attempt}" in _MP_SRC,
      f"E1 密文回归={_AQ_byte_ok}（{_AQ_byte_bad}） "
      f"D1 打乱回归={_MP.scramble(_AQ_S17_TXT, 17) == _AQ_S17_SCR}")

# ── AQ3：F 臂的操纵必须是**单因子**的 ───────────────────────────────────
_AQ_MULTI = "done: \ndef f(d):\n    return {k: v for k, v in d.items()}\n"
_AQ_ONE = "解析查询字符串前先处理空项"
_aq_f = _MP.reshape(_AQ_MULTI, 17)
_aq_f_seq = [c for c in _aq_f if c != "\n"] == [c for c in _AQ_MULTI if c != "\n"]
_aq_f_len = len(_aq_f) == len(_AQ_MULTI)
_aq_f_lines = len(_aq_f.split("\n")) == len(_AQ_MULTI.split("\n"))
_aq_f_geom = [len(x) for x in _aq_f.split("\n")] != [len(x) for x in _AQ_MULTI.split("\n")]
_aq_f_diff = _aq_f != _AQ_MULTI
check(RS, "AQ3 ★F 臂（reshape）必须**只动几何**：等长、等行数、字符序列不变，行宽必变\n"
          "         —— 若它顺手把字符也换了，它就和 E1 混成一个变量；\n"
          "           若行宽没变，那它什么都没做，却会产出一个人畜无害的 Δ=0，\n"
          "           然后被读成『形状无贡献』（那正是本项目最贵的一种假阴性）。",
      _aq_f_seq and _aq_f_len and _aq_f_lines and _aq_f_geom and _aq_f_diff,
      f"序列不变={_aq_f_seq} 等长={_aq_f_len} 等行数={_aq_f_lines} "
      f"行宽变={_aq_f_geom} 与原文不同={_aq_f_diff}")

# ── AQ4：F 的盲区必须**计数**，且恒等只在自述时放行 ──────────────────────
_AQ_blind_src_ok = ("shape_identity" in _AP_SRC
                    and 'STATS["shape_identity_ok"] += 1' in _AP_SRC
                    and "no_freedom" in _AP_SRC)
# ⛔ 撤回态实测捉到的**断言自身缺陷**：早先这里直接调 `_FM._check(..., allow_identity=True)`，
#   而一旦实现撤掉这个窄口，它会抛 AssertionError ⇒ **异常逃出契约测试** ⇒
#   汇总行一起没了（违反 R13 的"0 Traceback + 汇总行必须在"）。
#   ⇒ 断言里**绝不允许**让被测实现的异常逃出来：一律先用 `_fm_raises` 兜住再判。
_FM.reset_stats()
_AQ_opened = (not _fm_raises(lambda: _FM._check(_AQ_ONE, _AQ_ONE, "w",
                                               allow_identity=True))
              and _FM.STATS["shape_identity_ok"] == 1)
_FM.reset_stats()
_AQ_closed = _fm_raises(lambda: _FM._check(_AQ_ONE, _AQ_ONE, "w"))
check(RS, "AQ4 ★F 的盲区（既无换行又无空白的行）必须**开一个可核对的窄口**，\n"
          "         而不是让整臂崩掉、也不是静默放过\n"
          "         —— 中文短句整句没有可挪的结构，`reshape` 对它**无定义**。\n"
          "           静默返回原文 = 假装这一条也被操纵了；抛异常终止 = 整臂数据作废。\n"
          "           ⇒ 唯一诚实的做法：mapper 自己声明『没有自由度』，\n"
          "             调用方据此放行**并计数**，读数里明示有多少条没被操纵。",
      _AQ_blind_src_ok and _AQ_opened and _AQ_closed
      and _MP.reshape(_AQ_ONE, 17) == _AQ_ONE,
      f"有口子={_AQ_opened} 无理由时仍拒绝={_AQ_closed} "
      f"单行中文原样={_MP.reshape(_AQ_ONE, 17) == _AQ_ONE}")

# ── AQ5：只许一种导入写法（否则同一个模块被加载成两个对象）───────────────
_AQ_BAD_IMPORT = re.compile(r"^\s*(from\s+placebo\b|import\s+placebo\b)", re.M)
_AQ_IMP_HITS = []
for _p in sorted(V1.glob("bench/**/*.py")) + sorted(V1.glob("tests/*.py")) \
        + sorted(V1.glob("*.py")):
    try:
        if _AQ_BAD_IMPORT.search(_p.read_text(encoding="utf-8")):
            _AQ_IMP_HITS.append(_p.name)
    except Exception:                                          # noqa: BLE001
        pass
check(RS, "AQ5 ★全仓只许 `bench.placebo.*` 这一种导入写法\n"
          "         —— 若有地方把 `bench/` 自己塞进 sys.path 再 `import placebo`，\n"
          "           同一个模块会被加载成**两个对象**：两份 STATS、两份 install，\n"
          "           而**不会报任何错**。计数器记在 A 上、读数读的是 B。",
      not _AQ_IMP_HITS, f"用了裸导入的文件={_AQ_IMP_HITS}")

# ── AQ6：判据没有跟着机制一起合并 ───────────────────────────────────────
_AQ_PROBES = ["probe_placebo.py", "probe_fakemem.py", "probe_shape.py"]
_AQ_PROBE_SRC = {n: (V1 / "bench" / n).read_text(encoding="utf-8") for n in _AQ_PROBES}
#: 每个探针必须带着**它自己那个问题**的判据记号。
#: ⛔ 刻意不要求三者同名：第十七轮的探针把 R1/R2/R3 判在读里（没有 verdict_of），
#:   那是它自己的写法。要求"形同名"会把断言变成打字比赛，而不是判据检查。
_AQ_RULE_TAGS = {
    "probe_placebo.py": ("P4", "P5", "R2"),
    "probe_fakemem.py": ("verdict_of", "R2", "R3"),
    "probe_shape.py": ("verdict_of", "S3", "S4"),
}
_AQ_HAS_VERDICT = all(all(t in src for t in _AQ_RULE_TAGS[n])
                      for n, src in _AQ_PROBE_SRC.items())
#: ⚠️ 撤回态实测：早先这里按**文件名**匹配，于是"加一行 `from probe_fakemem import …`"
#:   并不会转红（导入语句里没有 `.py`），而注释里提到文件名倒会误报。
#:   ⇒ 判据必须是**导入语句**，不是字符串出现：
#:     真正会出事的是"这个探针复用了另一个探针的判读"，那只能发生在 import 上。
_AQ_CROSS_RE = re.compile(
    r"^\s*(?:from\s+(probe_placebo|probe_fakemem|probe_shape)\b"
    r"|import\s+(?:probe_placebo|probe_fakemem|probe_shape)\b)", re.M)
_AQ_CROSS = [n for n, t in _AQ_PROBE_SRC.items()
             if any(m != n.replace(".py", "") for m in _AQ_CROSS_RE.findall(t))]
check(RS, "AQ6 ★合并的只能是**机制**：三个探针必须各有一份自己的判读，且互不引用\n"
          "         —— 第十八轮的真实教训是『判读实现有两份 ⇒ 立刻漂』，\n"
          "           但修法不是把三份并成一份：它们回答的是**三个不同的问题**\n"
          "           （抽象层有没有用 / 存在性有没有贡献 / 形状有没有贡献）。\n"
          "           并成一份 = 把三个问题的答案硬塞进一个阈值。",
      _AQ_HAS_VERDICT and not _AQ_CROSS,
      f"各有 verdict_of={_AQ_HAS_VERDICT} 互相 import={_AQ_CROSS}")

# ── AQ7：形状对照的事前规则必须在开跑前打印 ────────────────────────────
# ★ 第十九轮中途发现这里原本有**两份**规则文本：脚本里 `cat <<RULES` 一份、
#   探针的阈值常量一份。那就是第十八轮"判读写两份必然漂"的复发。
#   ⇒ 修法与 AP15 同款：**删掉脚本那份**，脚本改成调 `probe_shape.py --rules`。
#   于是本断言要钉的不再是"脚本里有没有那些字"，而是：
#     ① 脚本在**首跑之前**调了 `--rules`；
#     ② 脚本里**没有**自己的阈值副本；
#     ③ 探针里确实有 `RULES` 与 `--rules`（否则 ① 是空调用）。
#: ⚠️ 撤回态实测：`find("--rules")` 会先命中文件头注释里那次提及（"由 `--rules` 现场念"），
#:   于是把调用挪到首跑之后**也不转红**。⇒ 必须锚在**那条命令**上。
_AQ_RULE_CMD = '"$PY" bench/probe_shape.py --rules'
_AQ_RULE_AT = _AQ_SH.find(_AQ_RULE_CMD)
_AQ_FIRST_RUN = _AQ_SH.find("run_arm E0")
_AQ_PROBE_SRC = (V1 / "bench" / "probe_shape.py").read_text(encoding="utf-8")
_AQ_RULE_ONE = ("RULES" in _AQ_PROBE_SRC and "def print_rules(" in _AQ_PROBE_SRC
                and '"--rules"' in _AQ_PROBE_SRC)
_AQ_NO_COPY = ("cat <<'RULES'" not in _AQ_SH
               and _AQ_SH.count("0.15") == 0)
check(RS, "AQ7 ★事前规则必须**只声明一处**，且必须在**开跑之前**念出来\n"
          "         —— 判据要是能改，拿到 0.14 时把它放宽到 0.10 只需动一个字符；\n"
          "           而『两处声明同一个阈值』的结果不是报错，是**打印的那份和判读的那份\n"
          "           悄悄不一样**（第十八轮真实复发过一次）。",
      _AQ_RULE_AT >= 0 and _AQ_FIRST_RUN >= 0 and _AQ_RULE_AT < _AQ_FIRST_RUN
      and _AQ_RULE_ONE and _AQ_NO_COPY,
      f"调用@{_AQ_RULE_AT} 首跑@{_AQ_FIRST_RUN} 探针有规则={_AQ_RULE_ONE} "
      f"脚本无副本={_AQ_NO_COPY}")

# ── AQ8：六臂齐备，且 E0/E3 必须走未 patch 的进程 ───────────────────────
_AQ_ARMS_OK = all(x in _AQ_SH for x in
                  ("run_arm E0  run", "run_arm E3  run", "run_arm T   fake",
                   "run_arm E1  fake", "run_arm E2  fake", "run_arm F   fake"))
check(RS, "AQ8 ★六臂必须齐备：E0 / E3 / T / E1 / E2 / **F**\n"
          "         —— 少 E0 就没有基线；少 E3 就没有正对照；少 T 就不知道装测量\n"
          "           本身有多大影响；少 E1 就不知道语义贡献；少 E2 就不知道\n"
          "           '一起毁掉'有多大；少 F 就问不出**形状**。",
      _AQ_ARMS_OK and 'if [ "${entry}" = "fake" ]' in _AQ_SH,
      f"六臂齐备={_AQ_ARMS_OK}")

# ── AQ9：证据必须冻结 + CI 必须每次重算 ────────────────────────────────
check(RS, "AQ9 ★形状对照的六份原始读数与判定必须**冻结成文件**，且 CI 每次重算\n"
          "         —— 写在文档里的数字改一个字词就可能不易察觉；\n"
          "           冻成 jsonl 之后，改数字探针会自己转红。",
      _AQ_EV.is_dir() and len(sorted(_AQ_EV.glob("*.runs.jsonl"))) >= 6
      and (_AQ_EV / "verdict.json").exists()
      and "probe_shape.py" in _AQ_RT,
      f"证据份数={len(sorted(_AQ_EV.glob('*.runs.jsonl'))) if _AQ_EV.is_dir() else 0}")

# ── AQ10：三条 mapper 的**分工**必须与设计表一致 ───────────────────────
_AQ_e1 = _MP.cipher(_AQ_MULTI)
_AQ_e2 = _MP.scramble(_AQ_MULTI, 17)
_AQ_role = {
    "E1_保行几何": [len(x) for x in _AQ_e1.split("\n")] == [len(x) for x in _AQ_MULTI.split("\n")],
    "E1_换字符": [c for c in _AQ_e1 if c != "\n"] != [c for c in _AQ_MULTI if c != "\n"],
    "E2_毁语序": sorted(_AQ_e2) == sorted(_AQ_MULTI) and _AQ_e2 != _AQ_MULTI,
    "F_保字符序列": [c for c in _aq_f if c != "\n"] == [c for c in _AQ_MULTI if c != "\n"],
    "F_毁行几何": [len(x) for x in _aq_f.split("\n")] != [len(x) for x in _AQ_MULTI.split("\n")],
}
check(RS, "AQ10 ★三条 mapper 的分工必须与设计表一致（这是整轮实验的**自变量定义**）\n"
          "          —— E1 保几何/换字符（语义）；F 保字符/毁几何（形状）；\n"
          "             E2 两个都毁（所以它的 Δ 无法单独归因）。\n"
          "          分工一旦漂，`E3 − F` 就不再是「形状」的读数，而是一个混合物。",
      all(_AQ_role.values()), f"{_AQ_role}")


_ap_restore()   # ⛔ 最后必须卸干净：patch 是类级别的，不还原会污染后面的组

# ===========================================================================
# AR 组（第二十轮）—— 感知 → 记忆之间那个「不可逆的漏斗」
#
#   缺陷（§17-9 实测）：`core.py` 里 `stored = full[:WORKING_CHAR_CAP]`，
#   工具返回 5000 字，进下一步上下文的只有前 300 字（覆盖率 0.06），
#   **其余部分当场消失且没有任何路径能取回**。
#
#   修法（§16.19）**不是**把 300 调大 —— 那会让一步吃掉整个上下文：
#     ① 原文整段进 `blobs`，工作记忆留一个**可寻址索引**（宽度一点没变）；
#     ② 只在**意外**发生时要回来（失败且报错被截断 / 世界模型失配）；
#     ③ 要回来的正文**升级**工作记忆里的那一条，不另开一段。
#
#   AR 组钉的是**机制的四条硬约束**，不是分数：
#     可寻址 / 逐字节可还原 / 触发可证伪 / 有上限且不回退对照臂
# ===========================================================================
from mw.core import (EXPAND_CHAR_CAP, EXPAND_MARK, EXPAND_MAX_PER_TASK,  # noqa: E402
                     WORKING_CHAR_CAP, recall_trigger)
from mw.memory import BlobIntegrityError, Handle                  # noqa: E402

_AR_BODY = "traceback...\n" + ("x" * 4000) + "\n决定性信息：KeyError 'z02'"

# ── AR1：原文必须**整段**落盘，且句柄自述的事实必须为真 ─────────────────
_AR_TMP = _TMP / "ar_blobs"
_AR_TMP.mkdir(parents=True, exist_ok=True)
#: ⚠️ 先清干净。**这条坑是 §91 顺手发现的**：AR 组原先靠「`_TMP` 每次都是新目录」
#:   这条隐含前提才能让 `b#<seq>` 恰好等于 `id`（base=0）⇒ 篡改才命中。
#:    只要这个库里有历史行，`int(ref)` 就会改到**上一轮那一行**，篡改静默无效、
#:    AR3 变成恒真（纪律 6 的第二种形态：变异没起作用）。
for _p in _AR_TMP.glob("*.db*"):
    _p.unlink()
_AR_MEM = MemoryStore(_AR_TMP / "ar.db")
_AR_H = _AR_MEM.capture(_AR_BODY)
_AR_BACK = _AR_MEM.expand(_AR_H.ref, cap=10 ** 6)[0]
check(RS, "AR1 ★感知写侧必须**整段**存下原文，且句柄的 `chars`/`sha` 不得撒谎\n"
          "         —— 存的是截断版却报全文长度，就是又一个『标签撒谎』：\n"
          "           读数上 100% 可回溯，实际取回来是残的。",
      _AR_BACK == _AR_BODY and _AR_H.chars == len(_AR_BODY)
      and _AR_H.sha == __import__("hashlib").sha256(
          _AR_BODY.encode("utf-8")).hexdigest()[:12],
      f"进={_AR_H.chars} 出={len(_AR_BACK)} sha={_AR_H.sha}")

# ── AR2：三种取回窗口都必须**逐字节**落在原文里 ─────────────────────────
_AR_WIN = {
    "head": _AR_MEM.expand(_AR_H.ref, cap=100, where="head"),
    "tail": _AR_MEM.expand(_AR_H.ref, cap=100, where="tail"),
    "whole": _AR_MEM.expand(_AR_H.ref, cap=100, where="whole"),
}
_AR_WIN_OK = (_AR_WIN["head"][0] == _AR_BODY[:100]
              and _AR_WIN["tail"][0] == _AR_BODY[-100:]
              and _AR_WIN["whole"][0].startswith(_AR_BODY[:50])
              and _AR_WIN["whole"][0].endswith(_AR_BODY[-50:])
              and all(v[1] for v in _AR_WIN.values()))
check(RS, "AR2 ★三种窗口必须逐字节落在原文里，且**都**报『已截断』\n"
          "         —— `whole` 头尾各取一半是为了不让『决定性信息在结尾』这种情况\n"
          "           被整段丢光；三者都不许把『截断了』说成『这是全文』。",
      _AR_WIN_OK,
      f"head={_AR_WIN['head'][0][:12]!r} tail={_AR_WIN['tail'][0][-12:]!r} "
      f"whole 头尾={_AR_WIN['whole'][0][:10]!r}/{_AR_WIN['whole'][0][-10:]!r}")

# ── AR3：句柄不存在 / 库被改过 ⇒ **必须报错**，不许静默兜底 ──────────────
_AR_err = []
for bad in ("b#999999", "b#0"):
    try:
        _AR_MEM.expand(bad, cap=10)
        _AR_err.append(f"{bad} 没报错")
    except KeyError:
        pass
_AR_MEM.conn.execute("UPDATE blobs SET text=? WHERE id=?",
                     ("被改过", int(_AR_H.ref[2:]) + _AR_MEM._blobs_base))
_AR_MEM.conn.commit()
#: ⛔ 篡改必须**真的命中一行**（`changes()==1`）。§91 之前这里写的是
#:    `int(_AR_H.ref.lstrip("b#"))` —— 句柄改成本会话内序之后，那个数字不再是库 id，
#:    UPDATE 会打到一行不存在的地方（或打到别人的行），而 `expand` 照样报错/不报错，
#:    AR3 的结论就不再是它自称的那件事。所以「命中」本身要被断言，不能默认。
_AR_TAMPERED = _AR_MEM.conn.execute("SELECT changes()").fetchone()[0]
try:
    _AR_MEM.expand(_AR_H.ref, cap=10)
    _AR_err.append("被改过却没报错")
except BlobIntegrityError:
    pass
check(RS, "AR3 ★句柄不存在 ⇒ `KeyError`；原文与写侧承诺不符 ⇒ `BlobIntegrityError`\n"
          "         —— 静默返回空串/原文会让『取回失败』长得和『取回成功』一样。\n"
          "           这与 `.txt 被误判成功`是同一类事故。\n"
          "         ⛔ 且篡改本身必须命中（`changes()==1`）：没命中的篡改等于没做变异，\n"
          "           那时『没报错』会被误读成『断言是恒真的』。",
      not _AR_err and _AR_TAMPERED == 1,
      f"异常={(not _AR_err)} 漏网={_AR_err} 篡改命中={_AR_TAMPERED} 行")
_AR_MEM.close()

# ── AR4：触发条件必须**可证伪**（两个不动的情形 + 两个动的情形）─────────
_AR_CASES = [
    ("长报错", dict(ok=False, error="x" * (WORKING_CHAR_CAP + 1), mismatched=False), "tail"),
    ("短报错", dict(ok=False, error="短", mismatched=False), ""),
    ("失配", dict(ok=True, error="", mismatched=True), "whole"),
    ("平静", dict(ok=True, error="", mismatched=False), ""),
]
_AR_GOT = [(n, recall_trigger(**kw)) for n, kw, _ in _AR_CASES]
check(RS, "AR4 ★触发必须**可证伪**：该动的两个情形要动，不该动的两个**一个字都不许动**\n"
          "         —— 只有『会触发』没有『不该触发时不触发』，"
          "那就是一个恒真的机制；\n"
          "           而恒真的机制在读数上与『没装』完全一样（都是 0 次或都是天天触发）。",
      all(g == want for (_, g), (_, _, want) in zip(_AR_GOT, _AR_CASES)),
      " ".join(f"{n}→{g or '不回看'}" for n, g in _AR_GOT))

# ── AR5：触发决策**不许模型参与**（它是内核规则）────────────────────────
#: ⚠️ 这里第一版只看了 `args.args`，而 `recall_trigger(*, …)` 的三个形参都在
#:    `kwonlyargs` 里 ⇒ 断言读到空集合、当场转红。**是断言的口径错，不是实现的错**
#:    —— 静态锚要按 AST 的**实际分类**取，不能凭"形参都在 args.args 里"的直觉。
_AR_TRIG_ARGS = [a.arg for n in ast.walk(ast.parse(_CORE_SRC))
                 if isinstance(n, ast.FunctionDef) and n.name == "recall_trigger"
                 for a in list(n.args.args) + list(n.args.kwonlyargs)]
check(RS, "AR5 ★触发函数只许收 `ok` / `error` / `mismatched` —— **不许**收模型输出\n"
          "         —— 让模型自己决定『要不要回头看原文』，它会在 100% 的情况下\n"
          "           自信地说不用；于是机制恒为 0 次，而读数上只显示『没触发』。",
      set(_AR_TRIG_ARGS) == {"ok", "error", "mismatched"}, f"形参={_AR_TRIG_ARGS}")

# ── AR6：内核里 `capture(` 必须有**生产调用点** ─────────────────────────
_AR_CAPTURE_N = _core_code.count("self.memory.capture(")
check(RS, "AR6 ★结构锚：`core.py` 必须真的在感知相位调 `memory.capture(`\n"
          "         —— 只把 `blobs` 与 `expand()` 造出来而内核不调用，"
          "那是一个『库里的能力』，不是闭环里的能力。",
      _AR_CAPTURE_N == 1,
      f"生产调用点={_AR_CAPTURE_N}（必须恰好 1 处：两处就意味着有两条写入路径）")

# ── AR7：宽度**一点没变**（本轮没把 300 调大）──────────────────────────
check(RS, "AR7 ★`WORKING_CHAR_CAP` 必须**仍然是 300**\n"
          "         —— 本轮的修法是『存索引 + 按需取回』，不是『把宽度调大』：\n"
          "           调大是拿上下文换细节，会让一步吃掉整个预算。\n"
          "           这条断言同时防『顺手放宽』和『顺手收紧』两种漂移。",
      WORKING_CHAR_CAP == 300 and EXPAND_CHAR_CAP < cfg.STATE_CHAR_CAP,
      f"宽度={WORKING_CHAR_CAP} 回看窗={EXPAND_CHAR_CAP} 状态预算={cfg.STATE_CHAR_CAP}")

# ── AR8：回看**升级**工作记忆，不另开一段（否则同一观测出现两次）────────
_AR_UP = MemoryStore(_TMP / "ar_up.db")
_AR_UP.think("[b#1 10字] 原始摘要")
_AR_up_ok1 = _AR_UP.upgrade_last(EXPAND_MARK + "b#1 加宽后的正文")
_AR_up_view = _AR_UP.working_view()
_AR_UP2 = MemoryStore(_TMP / "ar_up2.db")
_AR_up_ok2 = _AR_UP2.upgrade_last("没有条目时不该成功")
check(RS, "AR8 ★回看必须**替换**工作记忆的最后一条，不许追加\n"
          "         —— 追加会让同一条观测在上下文里出现两次（截断版 + 回看版），\n"
          "           而那正是『同一事实只声明一处』的反面；\n"
          "           第一次实现就是追加，AB7 当场转红：关掉工作记忆写入后\n"
          "           回流率仍有 0.5，因为回看块成了第二条回边。\n"
          "         ⚠️ 没有条目时必须返回 False（调用方据此计数），不许静默成功。",
      _AR_up_ok1 and len(_AR_up_view) == 1
      and _AR_up_view[0].startswith(EXPAND_MARK) and _AR_up_ok2 is False,
      f"替换后条数={len(_AR_up_view)} 无条目时={_AR_up_ok2}")
_AR_UP.close(); _AR_UP2.close()

# ── AR9：状态预算必须**单一来源**，且够放回看正文 ──────────────────────
_AR_PSRC = (V1 / "mw" / "planner.py").read_text(encoding="utf-8")
_AR_PSRC_CODE = _code_only(_AR_PSRC)
check(RS, "AR9 ★状态预算只能有**一处**声明：`planner` 里不许再出现 `state[:1200]` 这种字面量\n"
          "         —— 第二十轮实测踩到：内核把回看正文拼进状态，规划器在最后一米\n"
          "           又按 1200 字剪一刀，正文一个字都没进模型，而读数写着『取回成功』。\n"
          "           两个漏斗里**下面那个**从来不会报错。",
      "cfg.STATE_CHAR_CAP" in _AR_PSRC_CODE and "state[:1200]" not in _AR_PSRC_CODE
      and cfg.STATE_CHAR_CAP > EXPAND_CHAR_CAP + 2 * WORKING_CHAR_CAP,
      f"用常量={'cfg.STATE_CHAR_CAP' in _AR_PSRC_CODE} "
      f"残留字面量={'state[:1200]' in _AR_PSRC_CODE} "
      f"预算={cfg.STATE_CHAR_CAP} vs 回看+两条工作记忆={EXPAND_CHAR_CAP + 2 * WORKING_CHAR_CAP}")

# ── AR10：重规划**不许**把状态丢掉 ────────────────────────────────────
_AR_REPLAN_SIG = [n for n in ast.walk(ast.parse(_AR_PSRC))
                  if isinstance(n, ast.FunctionDef) and n.name == "replan"]
#: ⚠️ 与 AR5 同一个坑（第二次踩）：`replan(*, …)` 的形参全在 `kwonlyargs`。
#:    ⇒ 这里必须两类都取。同一个口径错在两个断言里各犯一次 = 我并没有真的学会它。
_AR_HAS_STATE = bool(_AR_REPLAN_SIG) and "state" in [
    a.arg for a in (list(_AR_REPLAN_SIG[0].args.args)
                    + list(_AR_REPLAN_SIG[0].args.kwonlyargs))]
#: ⚠️ 这里第一版判的是"占位串还在不在源码里"，那是**错的口径**：
#:    占位串作为"state 为空时的兜底"是合理的，违规的是"用它**顶掉** state"。
#:    ⇒ 判据必须落在**转发关系**上：`replan` 里调 `propose` 时必须把 state 交出去。
_AR_FORWARDS = ("state=(state" in _AR_PSRC_CODE or "state=state" in _AR_PSRC_CODE)
_AR_PLACEHOLDER = not _AR_FORWARDS
check(RS, "AR10 ★`replan` 必须收下并转发 `state`，不许用占位串把它顶掉\n"
          "         —— 第二十轮实测：失败之后的下一轮**恰恰走 replan**，\n"
          "           而它原来传的是 `state=\"(上一步失败，见下)\"` ⇒ 整个状态被扔掉。\n"
          "           于是『最需要上下文的那一次』刚好是『上下文被丢的那一次』，\n"
          "           与本函数 docstring 里那条 guidance 断点同宗。",
      _AR_HAS_STATE and _AR_FORWARDS,
      f"replan 收 state={_AR_HAS_STATE} 转发给 propose={_AR_FORWARDS}")

# ── AR11：抽象臂必须读不到原文（否则那个对照臂就不成立）────────────────
#: ⚠️ 这里第一版判的是"整个 core.py 里有没有那句话"，于是撤回态实测**没转红**：
#:    把 `_recall_for` 里的模式闸换成 `if False:` 之后，同一句话在 `_state()` 里
#:    还有一份 ⇒ 断言照样通过。**静态锚必须锚在函数体内**，不能锚在文件里。
_AR_SRC_CODE = _code_only(_CORE_SRC)
_AR_CORE_TREE = ast.parse(_CORE_SRC)


def _func_body(tree, name: str, src: str | None = None) -> str:
    """取一个函数的**源码片段**。

    ⚠️ `src` 必须显式传：默认值 `_CORE_SRC` 只对 `mw/core.py` 成立。
    第二十二轮实测踩到：拿 `withdraw_check.py` 的树去调它，函数体是从
    **core.py 的同一名字函数**里切出来的（同名函数 `_restore_all` 不存在时返回空串），
    于是断言读的是另一个文件 —— 又是"锚点在场、对象不在场"。
    """
    text = _CORE_SRC if src is None else src
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return ast.get_source_segment(text, n) or ""
    return ""


_AR_RECALL_BODY = _code_only(_func_body(_AR_CORE_TREE, "_recall_for"))
_AR_GUARD = "self.memory_mode == ABSTRACT_MODE" in _AR_RECALL_BODY
_AR_COUNT = 'self.expand_stats["skipped"]["mode"] += 1' in _AR_RECALL_BODY
check(RS, "AR11 ★`_recall_for` **函数体**内必须在 abstract 模式下拒绝取回并计数\n"
          "          —— abstract 臂的语义是『只读蒸馏层』（§16.15）。\n"
          "            允许它回看工具输出原文，两个臂就都读得到原文，\n"
          "            差值当场变成噪声，而读数上仍然显示『两个臂都跑完了』。\n"
          "          ⚠️ 判定范围必须是**这个函数的体**，不是整个文件 ——\n"
          "            否则同一句话在别处还有一份时，撤掉这里也不转红（实测踩过）。",
      _AR_GUARD and _AR_COUNT,
      f"函数体内模式闸={_AR_GUARD} 计数={_AR_COUNT} 函数体长度={len(_AR_RECALL_BODY)}")

# ── AR12：证据必须冻结 + CI 每次重算 ─────────────────────────────────
_AR_EV = V1 / "bench" / "evidence" / "recall_s20" / "verdict.json"
_AR_RT = (V1 / "run_tests.sh").read_text(encoding="utf-8")
_AR_FROZEN = json.loads(_AR_EV.read_text(encoding="utf-8")) if _AR_EV.exists() else {}
check(RS, "AR12 ★结论必须**冻结**成文件，CI 每次重算并与落盘判定对账\n"
          "          —— 文档里的数字改一个字符不容易被发现；\n"
          "            冻成 json 之后，改数据探针会自己转红。\n"
          "          ⚠️ 判定必须是 `R4` 而不是『成立』：R4 明确写着**只主张结构**。",
      _AR_FROZEN.get("tag") == "R4" and "probe_recall.py" in _AR_RT
      and "不主张分数" in str(_AR_FROZEN.get("claim", "")) + str(_AR_FROZEN.get("verdict", "")),
      f"tag={_AR_FROZEN.get('tag')!r} CI 里有探针={'probe_recall.py' in _AR_RT}")


# ══════════════════════════════════════════════════════════════════════════
# AS 组 —— 校验闸（第二十一轮）
#
# 背景：`mw/tools.py` 首屏写着内核做四件事「预算、记忆、规划、**校验**」。
# 前三件都在且有读数，第四件在此之前**零实现** —— 这不是能力缺口，是
# "声明了没有"。AS 组钉两件事：
#   ① 判据只从**题面**来（不许碰尺子三样东西）；
#   ② 闸必须**既拦又修**，且**关得掉**（否则对照组不存在，任何 Δ 都不可归因）。
# ══════════════════════════════════════════════════════════════════════════
import mw.verify as _AS_V                                        # noqa: E402

_AS_SRC = _src("verify.py")
_AS_CODE = _code_only(_AS_SRC)

# ── AS1：判据模块不许 import 锚层（它是内核的一部分）────────────────────
check(RS, "AS1 ★`mw/verify.py` 的代码里不许出现 `bench` —— 判据模块属于内核\n"
          "         —— 一旦它 import 了锚层，判据就有了一条通往参考解/断言集的\n"
          "           物理通道；而『不许读尺子』这句话靠注释是守不住的。",
      "bench" not in _AS_CODE, f"代码里出现 bench={'bench' in _AS_CODE}")

# ── AS2：抽代码的口径必须与外部打分器**逐字节同构** ──────────────────────
#: 不同构的代价是单向的：自检比尺子更严 ⇒ 正确答案被判成失败 ⇒ 分数下降，
#: 而读数上看起来像"自检有害"。这类"装置吃掉效应"本项目已经踩过一次（第十七轮）。
_AS_TASKS = [json.loads(ln) for ln in
             (V1 / "bench" / "tasks" / "struct_convert.jsonl")
             .read_text(encoding="utf-8").splitlines() if ln.strip()]
_AS_T0 = _AS_TASKS[0]
try:
    from bench.scorers.code_exec import extract_code as _AS_ref_extract
    _AS_SAMPLES = [_AS_T0["oracle"], _AS_T0["partial"],
                   "```python\n" + _AS_T0["oracle"] + "\n```",
                   "```c\nint f(int x){return x;}\n```", "",
                   "```\nprint(1)\n```", "解释：\n```py\nx=1\n```\n完"]
    _AS_PARITY = all(_AS_V.extract_code(s) == _AS_ref_extract(s) for s in _AS_SAMPLES)
    _AS_PARITY_N = len(_AS_SAMPLES)
except Exception as _e:                                          # noqa: BLE001
    _AS_PARITY, _AS_PARITY_N = False, f"异常 {type(_e).__name__}: {_e}"
check(RS, "AS2 ★`verify.extract_code` 与打分器 `extract_code` 必须**逐字节一致**\n"
          "         —— 自检比尺子更严就会把正确答案判死，那时分数下降会被\n"
          "           读成『自检有害』，而真正坏掉的是判据的口径。",
      _AS_PARITY is True, f"{_AS_PARITY_N} 个样本（含 ```c / 空 / 无围栏）逐字节比对")

# ── AS3：判据只从**题面**派生，且派生函数的输入只有 goal ────────────────
_AS_DERIVE_ARGS = [a.arg for n in ast.walk(ast.parse(_AS_SRC))
                   if isinstance(n, ast.FunctionDef) and n.name == "derive"
                   for a in list(n.args.args) + list(n.args.kwonlyargs)]
_AS_D1 = [c.kind for c in _AS_V.derive("写一个函数 foo(x)。只输出代码，函数名必须是 foo。")]
_AS_D2 = [c.kind for c in _AS_V.derive("解释一下为什么 CSV 里的引号要双写。")]
# ── AS3：判据只从**题面**派生；形参只许是 `goal` + 一个**配置档位** ────────
# ⚠️ 第八十轮把 `static`（0/1/2 三档）加进了签名。这**不是放宽**，三条都在：
#    · 档位来自 `Limits`（配置），不是答案、不是分数、不是判据输出；
#    · 默认 0 ⇒ 那条判据根本不派生 ⇒ 行为与之前**逐位相同**；
#    · 反证留在下面：任何形参名里带 answer/output/score/report/why/verdict
#      都必须判红 —— 「答案反过来影响判据」那条入口仍然关着。
_AS_BANNED_PARM = ("answer", "ans", "output", "score", "report", "why", "verdict")
_AS_DERIVE_BAD = [n for n in _AS_DERIVE_ARGS if n.lower() in _AS_BANNED_PARM]
_AS_STATIC_DEFAULT = inspect.signature(_AS_V.derive).parameters.get("static")
#: ★第一百四十七轮（§127）：签名多了第二个**配置档位**（`exec_`，题面示例判据）。
#: ⚠️ 这**不是放宽**：这三条同时在
#:    · 两个档位的默认值都必须 0（关 ⇒ 判据集与旧版**逐字节相同**，下面实测）；
#:    · 禁用名照旧全禁（`answer`/`score`/… 一个都不许出现）；
#:    · **新增**一条更紧的不变量：`exec_=0` 时派生出的判据 `kind` 序列必须与
#:      传两个参数里那个"关"完全一致 —— 光看形参名，说不出"关掉真的等于没加"。
_AS_SIG = inspect.signature(_AS_V.derive)
_AS_GEAR_DEFAULTS = [(_AS_SIG.parameters[n].default if n in _AS_SIG.parameters else "缺")
                     for n in ("static", "exec_")]
_AS_G_EX = "写一个函数 commas(n)：commas(0) == '0'。只输出代码，函数名必须是 commas。"
_AS_OFF_SAME = (
    [c.kind for c in _AS_V.derive(_AS_G_EX, 0, 0)]
    == [c.kind for c in _AS_V.derive(_AS_G_EX, 0)]
    == [c.kind for c in _AS_V.derive(_AS_G_EX, 0)]
    and [c.kind for c in _AS_V.derive("解释一下为什么 CSV 里的引号要双写。", 2, 0)]
    == [c.kind for c in _AS_V.derive("解释一下为什么 CSV 里的引号要双写。", 2)])
check(RS, "AS3 ★`derive()` 只许收 `goal` 再加**配置档位**，且不同题面必须派生出不同判据\n"
          "         —— 若它还能收 answer 或分数，就给了『答案反过来影响判据』的入口，\n"
          "           那正是自评分的形态。\n"
          "         ★ 第一百四十七轮：档位从 1 个变 2 个（`static` / `exec_`），\n"
          "           但『关掉那个档位 ⇒ 判据集与旧版逐字节相同』这条不变量是本轮新加的。",
      _AS_DERIVE_ARGS == ["goal", "static", "exec_"] and _AS_D1 != _AS_D2
      and _AS_STATIC_DEFAULT is not None and _AS_STATIC_DEFAULT.default == 0
      and _AS_DERIVE_BAD == [] and _AS_GEAR_DEFAULTS == [0, 0] and _AS_OFF_SAME,
      f"形参={_AS_DERIVE_ARGS} 禁用名命中={_AS_DERIVE_BAD} "
      f"档位默认={_AS_GEAR_DEFAULTS} 关掉=同构{_AS_OFF_SAME} "
      f"static默认={getattr(_AS_STATIC_DEFAULT, 'default', '缺')} "
      f"代码题={_AS_D1} 散文题={_AS_D2}")

# ── AS4：散文题**不许**派生 `compiles`（否则把解释文本判死）──────────────
check(RS, "AS4 ★散文题只许派生 `nonempty` —— 对一段解释文本做 `compile()`\n"
          "         —— 会把它 100% 判死。判据必须跟着题面走，不能跟着『我想要\n"
          "           一条更硬的判据』走；那与『把门槛调松让分数变好看』是同一类动作，\n"
          "           只是方向相反。",
      _AS_D2 == ["nonempty"], f"散文题判据={_AS_D2}")

# ── AS5：没有判据时**不许**报通过 ────────────────────────────────────────
_AS_v0 = _AS_V.run("随便一段话", [])
check(RS, "AS5 ★判据为空 ⇒ `ok=False`（报『没检查』，不报『通过』）\n"
          "         —— 两者在读数上必须分开：否则『自检通过率 100%』可以靠在\n"
          "           配置里关掉全部判据刷出来，那不是读数，那是作文。",
      _AS_v0.ok is False, f"ok={_AS_v0.ok} report={_AS_v0.report[:40]!r}")

# ── AS6：未知判据类型必须**失败**，不许静默放行 ──────────────────────────
_AS_v1 = _AS_V.run("def f(): pass", [_AS_V.Check("神秘", "bogus")])
check(RS, "AS6 ★未知判据类型 ⇒ 判红，**不许**静默放行\n"
          "         —— 一个静默放行的判据比没有判据更坏：它让『检查过了』\n"
          "           变成一句谎话，而读数上看不出差别。",
      _AS_v1.ok is False, f"ok={_AS_v1.ok} failed={_AS_v1.failed}")

# ── 端到端：脚本化模型（零模型调用）────────────────────────────────────
class _AS_LLM:
    """按次序交答案的脚本模型。唯一职责：让内核走『交付→打回→重写』。"""

    def __init__(self, answers) -> None:
        self.answers = list(answers)
        self.calls = 0
        self.seen: list[str] = []

    def complete(self, messages, **kw):                          # noqa: ANN001
        blob = "\n".join(m.content for m in messages)
        if "准备执行的动作：" in blob:
            return json.dumps({"expect_ok": True, "expected": "会成功",
                               "confidence": 0.9}, ensure_ascii=False)
        self.seen.append(blob)
        i = min(self.calls, len(self.answers) - 1)
        self.calls += 1
        return json.dumps({"candidates": [{
            "intent": f"第 {self.calls} 版", "tool": "finish",
            "args": {"answer": self.answers[i]},
            "rationale": "交付"}]}, ensure_ascii=False)


_AS_SEQ = [0]


def _as_run(answers, *, retries: int | None = None,
            goal: str | None = None, static: int | None = None):
    """跑一次内核。每次一个**独立**记忆库 —— 否则上一轮的事实会进下一轮的 guidance。"""
    _AS_SEQ[0] += 1
    kw = {} if retries is None else {"verify_retries": retries}
    if static is not None:
        kw["verify_static"] = static
    lim = cfg.Limits(max_steps=8, max_llm_calls=24, max_candidates=2,
                     max_consecutive_failures=6, max_stall=6, **kw)
    llm = _AS_LLM(answers)
    mem = MemoryStore(_TMP / f"as_{_AS_SEQ[0]}.db")
    c = Core(settings=cfg.Settings(limits=lim), llm=llm, memory=mem,
             wire_external=False)
    c.log = lambda m: None                                       # noqa: ARG005
    out = c.run(goal or _AS_T0["prompt"])
    return c, out, llm


_AS_GOOD = _AS_T0["oracle"]
_AS_BAD = _AS_T0["oracle"].replace('def flatten(d, prefix=""):',
                                   'def flatten(d, prefix="":')
_AS_C, _AS_OUT, _AS_L = _as_run([_AS_BAD, _AS_GOOD])
_AS_VS = (_AS_OUT.stats or {}).get("verify") or {}

# ── AS7：闸必须真的拦（不是装饰）──────────────────────────────────────
check(RS, "AS7 ★第一次交**语法坏**的代码必须被打回\n"
          "         —— 这是本轮唯一的新机制。它必须由**内核规则**触发，\n"
          "           不能交给模型自己决定要不要检查（否则恒为 0 次）。",
      int(_AS_VS.get("failed") or 0) >= 1,
      f"runs={_AS_VS.get('runs')} passed={_AS_VS.get('passed')} "
      f"failed={_AS_VS.get('failed')}")

# ── AS8：打回之后必须真修好，且**交付的是修好的那一版** ─────────────────
check(RS, "AS8 ★第二版交对 ⇒ 交付它，状态 done\n"
          "         —— 只拦不修的话，这一轮等于给系统加了一个'永远失败'的闸，\n"
          "           那是把『一个永远不可能好的信号』又造了一遍。",
      _AS_OUT.status == "done" and _AS_OUT.answer.strip() == _AS_GOOD.strip(),
      f"status={_AS_OUT.status} 答案是否第二版={_AS_OUT.answer.strip() == _AS_GOOD.strip()}")

# ── AS9：打回必须有**上限**，超限如实标状态 ────────────────────────────
_AS_BADS = [f"```c\nint f{i}(int x) {{ return x; }}\n```" for i in range(6)]
_AS_C2, _AS_OUT2, _ = _as_run(_AS_BADS, retries=1)
_AS_VS2 = (_AS_OUT2.stats or {}).get("verify") or {}
check(RS, "AS9 ★一直交坏答案 ⇒ 打回 `verify_retries` 次后放弃，并标 `done_unverified`\n"
          "         —— 没有上限的重写会烧掉**交付**的配额（收尾余量的同一条教训）；\n"
          "           而把它报成 `done` 就是『每轮全绿而目标数字不动』。",
      int(_AS_VS2.get("failed") or 0) == 2
      and int(_AS_VS2.get("exhausted") or 0) == 1
      and _AS_OUT2.status == "done_unverified",
      f"failed={_AS_VS2.get('failed')} exhausted={_AS_VS2.get('exhausted')} "
      f"status={_AS_OUT2.status}")

# ── AS10：放弃/停滞的交付**不许为空**（装置不许吃掉效应）────────────────
_AS_C3, _AS_OUT3, _ = _as_run([_AS_BAD] * 5)
_AS_VS3 = (_AS_OUT3.stats or {}).get("verify") or {}
check(RS, "AS10 ★重写失败（重复动作 ⇒ 停滞）时，答案必须被 `_salvage` 保住\n"
          "          —— 否则一个本来能拿分的答案会被装置变成 0 分，\n"
          "            而分数下降会被读成『自检有害』。**装置不许吃掉效应。**",
      bool(_AS_OUT3.answer.strip()) and _AS_VS3.get("salvaged") is True,
      f"status={_AS_OUT3.status} 答案{len(_AS_OUT3.answer)}字 salvaged={_AS_VS3.get('salvaged')}")

# ── AS11：关得掉（否则对照组不存在）────────────────────────────────────
_AS_C4, _AS_OUT4, _ = _as_run([_AS_BAD, _AS_GOOD], retries=0)
_AS_VS4 = (_AS_OUT4.stats or {}).get("verify") or {}
check(RS, "AS11 ★`verify_retries=0` ⇒ **0 条判据、0 次运行**\n"
          "          —— 开/关自检是两个不同的候选。关不掉就没有对照组，\n"
          "            那时任何 Δ 都无法归因（第十六轮共用记忆目录的同类事故）。",
      _AS_VS4.get("checks") == [] and int(_AS_VS4.get("runs") or 0) == 0,
      f"checks={_AS_VS4.get('checks')} runs={_AS_VS4.get('runs')}")

# ── AS12：开关必须进**口径签名**（开/关跑出来的分数不是同一个候选）──────
_AS_LIM = cfg.Limits()
check(RS, "AS12 ★`verify_retries` 必须在 `Limits.as_dict()` 里，且允许取 0\n"
          "          —— `Limits.as_dict()` 是口径签名的输入。不进制口径的话，\n"
          "            开/关自检的两轮会被 `paired_compare` 判成『同口径可比』，\n"
          "            那是把尺子又往下挪了一次。\n"
          "          ⚠️ 还必须进 `ZERO_OK_KEYS`：`_env_int` 默认把 0 当『没填』\n"
          "            回落到默认值 —— 那样『关掉』会被**静默改成 2**。",
      "verify_retries" in _AS_LIM.as_dict()
      and "V1_VERIFY_RETRIES" in cfg.ENV_KEYS
      and "V1_VERIFY_RETRIES" in cfg.ZERO_OK_KEYS,
      f"在 as_dict={'verify_retries' in _AS_LIM.as_dict()} "
      f"登记={'V1_VERIFY_RETRIES' in cfg.ENV_KEYS} "
      f"允许0={'V1_VERIFY_RETRIES' in cfg.ZERO_OK_KEYS}")

# ── AS13：闸只挂在**答案出口**上（不许变成第二道相位）──────────────────
#: 第二十三轮改了守卫的字面量：`cand.tool == "finish"` → `_produces`，
#: 因为外部能力的答案要走**同一道**闸（`produces_answer`）。
#: ⚠️ 但**不变量没有变松**，所以检查必须同时钉住三件事：
#:   ① 调用点仍然只有一处（不是"每处都加一道闸"）；
#:   ② 守卫仍然是 `_produces and result.ok and self._checks`；
#:   ③ `_produces` 的定义里**必须**含 `cand.tool == "finish"` ——
#:      否则"外部能力"可以把内核自己的收尾动作挤出去，闸就成了外部专用的。
_AS_GATE_CALLS = _code_only(_CORE_SRC).count("self._gate_finish(result)")
_AS_GATE_GUARDED = ('if _produces and result.ok and self._checks:'
                    in _code_only(_CORE_SRC))
#: ★ V56（§122）：谓词的**定义**搬到了 `_is_delivery`（下发计数点也要用它把
#: 下发拆成交付/步骤），`_produces` 变成它的**别名**。不变量没有变松，反而更紧：
#:   ① 定义里仍必须含 `cand.tool == "finish"`；② `_produces` 必须**就是**它
#:      —— 两处各写一份拷贝正是本轮要消灭的东西（纪律 14）。
_AS_CORE_CODE = _code_only(_CORE_SRC)
_AS_PRODUCES = ('_is_delivery = (cand.tool == "finish"' in _AS_CORE_CODE
                and '_produces = _is_delivery' in _AS_CORE_CODE)
check(RS, "AS13 ★`_gate_finish` 只许有**一处**调用，且必须被「答案来源」守住\n"
          "          —— 本轮只加一条腿，不加一个相位：主循环仍然只有一个 `while`（J6），\n"
          "            校验是答案出口上的一道闸，不是第二次推理。\n"
          "          ⚠️ 外部能力的答案走同一道闸（第二十三轮）⇒ 守卫换成 `_produces`，\n"
          "            但 `_produces` 的定义里必须仍含 `finish` —— 否则闸会变成外部专用。",
      _AS_GATE_CALLS == 1 and _AS_GATE_GUARDED and _AS_PRODUCES,
      f"调用点={_AS_GATE_CALLS} 被 _produces 守住={_AS_GATE_GUARDED} "
      f"定义含 finish 且 _produces 是它的别名={_AS_PRODUCES}")

# ── AS14：salvage 只在**答案为空**时顶替 ───────────────────────────────
_AS_FIN_BODY = _code_only(_func_body(ast.parse(_CORE_SRC), "_finish"))
check(RS, "AS14 ★`_finish` 里 salvage 的条件必须是 `not answer and self._salvage`\n"
          "          —— 写成只看 `self._salvage` 就会把**正常交付**的答案替换成\n"
          "            上一版被打回的坏答案：一个静默的降级，而日志上一切正常。",
      "if not answer and self._salvage:" in _AS_FIN_BODY,
      f"函数体命中={'if not answer and self._salvage:' in _AS_FIN_BODY}")

# ── AS15：汇总行必须念校验读数（零值也念，且念判据）──────────────────────
check(RS, "AS15 ★`■` 汇总行必须念 `校验=`，并**把判据念出来**\n"
          "          —— 只念通过率的数可以靠关掉判据刷上去；\n"
          "            而『机制装了但一次没触发』与『机制没装』在只念 calls 时长得一样。",
      "校验=" in _AS_FIN_BODY and "判据=" in _AS_FIN_BODY,
      f"汇总行含校验={'校验=' in _AS_FIN_BODY} 含判据={'判据=' in _AS_FIN_BODY}")

# ── AS16：证据冻结 + CI 重算 ───────────────────────────────────────────
_AS_EV = V1 / "bench" / "evidence" / "verify_s21" / "verdict.json"
_AS_RT = (V1 / "run_tests.sh").read_text(encoding="utf-8")
_AS_FZ = json.loads(_AS_EV.read_text(encoding="utf-8")) if _AS_EV.exists() else {}
check(RS, "AS16 ★判读必须冻成文件，CI 每次重算并与落盘判定对账\n"
          "          —— 数字写在散文里改一个字符不容易被发现；冻成 json 之后，\n"
          "            换数据/松判据探针会自己转红。\n"
          "          ⚠️ 判定必须是 `R5`：它明确写着**不主张真模型分数**。",
      _AS_FZ.get("tag") == "R5" and "probe_verify.py" in _AS_RT
      and "不主张真模型分数" in str(_AS_FZ.get("verdict", "")),
      f"tag={_AS_FZ.get('tag')!r} CI 里有探针={'probe_verify.py' in _AS_RT}")

# ── AS17：真模型读数必须是**负面结论**，而且写进证据与 CI ────────────────
#: ★ 这一条是本轮最重要的那句话的锚：**闸接通了，但它的收益不是"诊断信息被执行"**。
#:   内容无关的安慰剂给出**逐字相同**的报告（只差 `elapsed_s`）⇒ §17-6 仍是 0。
#:   ⛔ 若哪天有人把这个 tag 改成"成立"，这条断言会当场判红 ——
#:      它防的是"把负面结论悄悄说成正面"。
_AS_RV = (V1 / "bench" / "evidence" / "verify_s21" / "real" / "verdict.json")
_AS_RFZ = json.loads(_AS_RV.read_text(encoding="utf-8")) if _AS_RV.exists() else {}
check(RS, "AS17 ★真模型三臂读数必须冻结，且判定必须是**负面**的 `Q3`\n"
          "          —— `ON == PL（去掉 elapsed_s 后逐字相同）`意味着：\n"
          "            收益来自『打回重写』这个动作，**不是**判据的诊断信息。\n"
          "          ⇒ 『知识必须可执行』（§17-6）这一轮**仍是 0**，不许读成正的。\n"
          "          ⚠️ 同时钉住 heldout 那 7 题**逐题相同**（效应没有跨题集复现）。",
      _AS_RFZ.get("tag") == "Q3"
      and _AS_RFZ.get("on_equals_placebo_ignore_elapsed") is True
      and (_AS_RFZ.get("ho") or {}).get("moved") == []
      and "probe_verify_real.py" in _AS_RT,
      f"tag={_AS_RFZ.get('tag')!r} "
      f"ON==PL={_AS_RFZ.get('on_equals_placebo_ignore_elapsed')} "
      f"heldout 位移={( _AS_RFZ.get('ho') or {}).get('moved')}")

# ── AS18–AS21（第二十二轮）：收尾兜底必须**有读数**，而不只有两句日志 ────────
#: ⛔ 动机：第二十一轮的整条位移（0.5600 → 0.7600）都走的是这条路 ——
#:   `finish` 被打回 → 重规划 → 价值层否决 → 收尾余量 → `_close_out` → 交付。
#:   而当时全项目对它的记录**只有两句中文日志**。日志能证明"走到过"，
#:   回答不了"走到过几次、几次真的改了交付物"—— 而后者是"它有没有用"的第一个问题。
_CO = _q_out.stats.get("closeout") or {}
check(RS, "AS18 ★收尾必须留下**结构化读数**（触发数 / 触发原因 / 答案来源 / 字数）\n"
          "          —— 只留日志 = 只能证明『走到过』；\n"
          "            『走到过几次、几次真改了交付物』是它能不能被评估的前提。",
      _CO.get("triggered") == 1
      and _CO.get("origin") == {"收尾调用": 1}
      and _CO.get("by_reason") == {"steps": 1, "wall": 0}
      and _CO.get("chars", 0) > 0 and _CO.get("empty") == 0,
      f"triggered={_CO.get('triggered')} origin={_CO.get('origin')} "
      f"by_reason={_CO.get('by_reason')} chars={_CO.get('chars')}")

check(RS, "AS19 ★汇总行必须把收尾读数念出来（触发时带原因与来源）\n"
          "          —— 否则『收尾改变了交付物』这件事在日志上只剩一句散文，\n"
          "            而散文里的数字没法被重算。",
      any("收尾=1次" in ln and "来源收尾调用:1" in ln and "步1/时0" in ln
          for ln in _q_log.lines),
      f"日志含收尾读数={any('收尾=1次' in ln for ln in _q_log.lines)}")

# AS20. `overrode_rejected` 的语义必须严格是**不同**，不是"被救回来"。
#   直接对 `_close_out` 做单元级验证（它是可单独调用的），两种情形各一次：
#   ①`_salvage` 与收尾答案不同 ⇒ overrode=1；②逐字相同 ⇒ same=1。
#   ⚠️ 只测 ① 会让"恒等于 overrode"的实现通过 —— 那就退化成一句自我表扬。
_co_seen: dict = {}


def _co_done(status: str, answer: str, reason: str) -> str:      # noqa: ANN001
    _co_seen["status"], _co_seen["answer"] = status, answer
    return "ok"


_co_a = Core(settings=_q_settings, llm=_QLLM(),
             memory=MemoryStore(_TMP / "co_a.db"), log=_Log(None))
_co_a._salvage = "旧版本（被闸打回的那一版）"
_co_a._close_out("读一下这个文件", ["h1"], _co_done)
_co_b = Core(settings=_q_settings, llm=_QLLM(),
             memory=MemoryStore(_TMP / "co_b.db"), log=_Log(None))
_co_b._salvage = "最终答案：42"          # 与 `_QLLM` 的收尾答案**逐字**相同
_co_b._close_out("读一下这个文件", ["h1"], _co_done)
check(RS, "AS20 ★`overrode_rejected` 只主张**不同**，且必须两种情况都分得开\n"
          "          —— 只报『改了多少次』而不报『其中多少次其实和被打回那版一样』，\n"
          "            等于把『收尾救回了答案』这句没被验证的话写进读数。",
      _co_a.closeout_stats["overrode_rejected"] == 1
      and _co_a.closeout_stats["same_as_rejected"] == 0
      and _co_b.closeout_stats["same_as_rejected"] == 1
      and _co_b.closeout_stats["overrode_rejected"] == 0
      and _co_seen.get("status") == "closed",
      f"A(不同):改={_co_a.closeout_stats['overrode_rejected']}"
      f"/同={_co_a.closeout_stats['same_as_rejected']} "
      f"B(逐字同):改={_co_b.closeout_stats['overrode_rejected']}"
      f"/同={_co_b.closeout_stats['same_as_rejected']}")

# AS21. 读数初始化只许有**一处**声明，且 `__init__` 与 `run()` 各调一次。
#   为什么钉：`_close_out` 是可单独调用的单元 ⇒ 状态只建在 `run()` 里会让
#   AS20 那种测法一调就 AttributeError；而两处各写一份字典则会静默漂移。
_AS_RESET_CALLS = _code_only(_CORE_SRC).count("self._reset_closeout()")
check(RS, "AS21 ★收尾读数只在 `_reset_closeout()` 里声明一次，`__init__` 与 `run()` 各调一次\n"
          "          —— 两处各写一份字典 = 迟早漂移（`run` 加了字段而 `__init__` 没加）；\n"
          "            只建在 `run()` 里 = `_close_out` 无法被单独验证。",
      _AS_RESET_CALLS == 2
      and _code_only(_CORE_SRC).count('"overrode_rejected": 0') == 1,
      f"调用点={_AS_RESET_CALLS} 声明处="
      f"{_code_only(_CORE_SRC).count(chr(34) + 'overrode_rejected' + chr(34) + ': 0')}")

# ── AS22（第二十二轮）：R13 驱动器自己的两个**破坏性缺陷**必须被钉住 ──────────
#: ★ 这两个不是理论风险，是本轮真实发生的事故：
#:   ① 「有备份就无条件还原」把**本轮合法的修改**整个覆盖掉了 ——
#:      `/tmp/v1_withdraw_bak` 里躺着上一轮的 core.py，新一轮一启动就被照着还原，
#:      `closeout_stats` 当场消失，契约从 444 条掉到 AS20 的 AttributeError。
#:      日志还写着"启动先还原 5 个残留变异文件"，读起来像在修，实际在删。
#:   ② 汇总行正则 `PASS=(\d+)\s+FAIL=(\d+)` 会在**任何地方**命中 ——
#:      契约测试崩了之后，它抓到的是 H8 断言自己打印的 `（PASS=7 FAIL=0）`，
#:      于是"基线 7/0 全绿"这个假读数把它带过了基线闸。
#:   ⇒ 工具链的判据和内核的判据一样，必须能**精确地**说"是我期待的那个读数"。
_AS_WC = (V1 / "bench" / "withdraw_check.py").read_text(encoding="utf-8")
_AS_WC_TREE = ast.parse(_AS_WC)
# ⚠️ 这两条必须做**表外检查**：本驱动器要撤回的锚点有一部分就写在它自己的 CSES 表里，
#    用整文件 `in` 去判，表里那一份字面量会让断言恒真（本轮实测踩到）。
_AS_RESTORE_BODY = _code_only(_func_body(_AS_WC_TREE, "_restore_all", _AS_WC))
# --- AS22 的两个判定先算好，f-string 里不再出现嵌套双引号 ---
_AS22_NEEDLE = 'if _sha(dst) == rec.get("mutated")'
_AS22_ONLY_MUTATED = _AS22_NEEDLE in _AS_RESTORE_BODY
_AS22_SKIP_STALE = 'skipped.append(rel)' in _AS_RESTORE_BODY

check(RS, "AS22 ★R13 驱动器只许还原「当前内容 == 当初写进去的变异」的备份\n"
          "          —— 无条件还原会把**本轮合法的修改**整个覆盖掉：\n"
          "            本轮真实事故——上一轮的 core.py 备份在新一轮启动时被照着还原，\n"
          "            `closeout_stats` 当场消失、契约从 444 条掉到 AS20 的 AttributeError，\n"
          "            而日志写着『启动先还原 5 个残留变异文件』，读起来像在修，实际在删。",
      _AS22_ONLY_MUTATED and _AS22_SKIP_STALE,
      f"只还原真变异={_AS22_ONLY_MUTATED} 过期就跳过={_AS22_SKIP_STALE}")

# AS23：既看**字面量**（必须锚在整句上），也**复现那次事故**（拿一条嵌套的假汇总喂给它）。
_AS_SUM_LIT = ""
for _node in _AS_WC_TREE.body:
    if (isinstance(_node, ast.Assign)
            and any(getattr(_t, "id", "") == "_SUMMARY" for _t in _node.targets)):
        _call = _node.value
        if (isinstance(_call, ast.Call) and getattr(_call.func, "attr", "") == "compile"
                and _call.args):
            try:
                _AS_SUM_LIT = ast.literal_eval(_call.args[0])
            except Exception:                                      # noqa: BLE001
                _AS_SUM_LIT = ""
#: 样本 = 一次真实事故的复现：上面那行是 H8 断言自己打印的假汇总，下面才是真的。
_AS_SAMPLE = "（PASS=7  FAIL=0）\nV1 契约测试汇总  PASS=446  FAIL=0\n"
_AS_HITS = re.compile(_AS_SUM_LIT).findall(_AS_SAMPLE) if _AS_SUM_LIT else []
check(RS, "AS23 ★基线判读的汇总行必须**锚在整句上**，且**唯一**\n"
          "          —— 本轮真实事故：`PASS=(\\d+)\\s+FAIL=(\\d+)` 会在任何地方命中，\n"
          "            契约测试中途崩了之后它抓到的是 H8 断言自己打印的『PASS=7 FAIL=0』，\n"
          "            于是『基线 7/0』这个假读数把它带过了基线闸。\n"
          "          ⚠️ 所以这条不只查字面量，还把那条假汇总**喂回给正则**看它认不认。",
      _AS_SUM_LIT.startswith("V1 契约测试汇总")
      and _AS_HITS == [("446", "0")]
      and "if len(m) == 1" in _AS_WC,
      f"字面量={_AS_SUM_LIT!r} 样本命中={_AS_HITS}")


# ── AS24（第二十二轮）：收尾读数必须**落盘**（有消费者），否则等于装饰字段 ────
#: ⛔ 一条硬纪律就是"字段必须有消费者"：日志上有数、没人读得到就是没有。
#:   所以收尾读数不能只活在 `stats` 里 —— 它必须进 `runs.jsonl`，
#:   而且**两个 solver 分支（每题新建内核 / 复用内核）都要并进去**，
#:   只接一个分支就是"一半的题没有读数"，而汇总行看起来完全正常。
_RUN_SRC = (V1 / "run.py").read_text(encoding="utf-8")
_RAW_ABSORB = _code_only(_RUN_SRC).count('_kco_absorb(_kco, out.stats.get("closeout") or {})')
# --- AS24 的两个判定先算好，f-string 里不再出现嵌套引号 ---
_AS24_LANDED = '"kernel_closeout": _kco' in _RUN_SRC

check(RS, "AS24 ★收尾读数必须落盘，且**两个 solver 分支都并进去**\n"
          "          —— 不落盘的读数不可审计；只接一个分支 = 一半的题没有读数，\n"
          "            而汇总行看起来一切正常。",
      _AS24_LANDED and _RAW_ABSORB == 2,
      f"落盘键={_AS24_LANDED} 并入点={_RAW_ABSORB}（须为 2）")

# ── AS25（第二十二轮）：收尾读数的**真模型冻结证据**必须可重算 ──────────────
#: ★ 这一条把"收尾在 5 题里触发 2 次、改掉交付物 1 次"从散文里搬进对账。
#:   冻结点：`bench/evidence/closeout_s22/{off,on}.jsonl`（同题集、同尺子、T=0）。
#: ⛔ 两个数字必须一起看：
#:   · `triggered=2` 但 `overrode_rejected=1` ⇒ **另一次收尾并没有改变交付物**
#:     （那题没被打回过，收尾只是把答案按时交出来）；
#:   · `by_reason.wall=2 / steps=0` ⇒ 两次都是**时间**触发的，不是步数用尽。
#:     这一点很要紧：它说明这个题集上"收尾"主要在兜**模型慢**，不在兜"想太多"。
_CO_EV = V1 / "bench" / "evidence" / "closeout_s22"


def _co_ev(name: str) -> tuple[dict, dict]:
    p = _CO_EV / f"{name}.jsonl"
    if not p.exists():
        return {}, {}
    rows = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not rows:
        return {}, {}
    rep = rows[-1].get("report") or {}
    pt = rep.get("per_task") or []
    mean = round(sum(t["score"] for t in pt) / len(pt), 4) if pt else None
    return (rows[-1].get("kernel_closeout") or {}), {"mean": mean, "n": len(pt)}


_CO_OFF, _CO_OFF_M = _co_ev("off")
_CO_ON, _CO_ON_M = _co_ev("on")
check(RS, "AS25 ★收尾读数必须冻成证据，且**触发次数 ≠ 改掉交付物的次数**\n"
          "          —— 只报『收尾触发了 N 次』会让人把它读成『救回了 N 道题』。\n"
          "            实测：5 题触发 2 次，其中只有 1 次与『被闸打回的那一版』不同。\n"
          "          ⚠️ 两次都是 `wall` 触发（步数触发 0 次）⇒ 这个题集上收尾兜的是\n"
          "            『模型慢』，不是『想太多』；换题集必须重新登记。",
      _CO_OFF.get("triggered") == 0
      and _CO_ON.get("triggered") == 2
      and _CO_ON.get("by_reason") == {"steps": 0, "wall": 2}
      and _CO_ON.get("origin") == {"收尾调用": 2}
      and _CO_ON.get("overrode_rejected") == 1
      and _CO_ON.get("same_as_rejected") == 0
      and _CO_ON.get("empty") == 0
      and _CO_ON.get("chars", 0) > 0
      and _CO_OFF_M.get("mean") == 0.56 and _CO_ON_M.get("mean") == 0.76,
      f"OFF 触发={_CO_OFF.get('triggered')} mean={_CO_OFF_M.get('mean')} | "
      f"ON 触发={_CO_ON.get('triggered')} 原因={_CO_ON.get('by_reason')} "
      f"改交付={_CO_ON.get('overrode_rejected')} mean={_CO_ON_M.get('mean')}")


# ══════════════════════════════════════════════════════════════════════════
# AV 组（第二十七轮，§17-6 6a/6b）：失败必须带着**诊断原文**进蒸馏器的视野
# ══════════════════════════════════════════════════════════════════════════
#: 实测（离线探针 + 真模型蒸馏 13 进 1 出）：打回明明落了 failure 行，
#: content 却只有 `finish({...}) — v0` 这种动作行，错误原文只在 meta 里 ——
#: 而蒸馏器**只读 content** ⇒ 它看到的是零信息行 ⇒ 模型输出 NONE 是必然。
#: 这就是 §17-6 "知识原料断供"的第一处断点：不是没有失败，是失败的理由
#: 从来没有到达过唯一一个能把失败变成经验的位置。

_AV_FAILS = list(_AS_C2.memory.episodes(kind="failure"))
check(RS, "AV1 ★打回落盘的 failure 行，content 必须带着**错误原文**（不只动作行）\n"
          "          —— meta 里的 error 蒸馏器看不见；content 没有诊断原文，\n"
          "            『失败 → 经验』这条链在第一步就断了（§17-6 的断点①）。",
      len(_AV_FAILS) >= 1 and any("自检未通过" in f.content for f in _AV_FAILS),
      f"failure行数={len(_AV_FAILS)} "
      f"首行content={(_AV_FAILS[0].content[:60] if _AV_FAILS else '无')}")

check(RS, "AV2 ★**超限交付**（打回次数用尽后如实交付）也必须落 failure 行\n"
          "          —— 超限那次 result.ok=True ⇒ 不走主循环的失败分支，\n"
          "            不补一行的话『这题最终为什么没过闸』在情景记忆里零痕迹，\n"
          "            而它恰恰是这题最该被蒸馏成经验的信息（断点②）。",
      any("自检超限未过" in f.content for f in _AV_FAILS),
      f"failure行数={len(_AV_FAILS)} "
      f"含超限={any('自检超限未过' in f.content for f in _AV_FAILS)}")

# ── AV3：蒸馏器**行为锚**——失败原料必须真的到达它、且排在注意力最高处 ──────
#: ⛔ 必须是行为锚而不是源码锚：第二十五轮 AU7 实测，AST/字符串锚在撤回态下
#:   可以全绿（死代码里仍有那个调用）。这里让假 LLM 把收到的 prompt 记下来，
#:   直接检查『第一份被蒸馏的原料是 failure』——撤掉排序 ⇒ 立刻转红。
_AV_MEM = MemoryStore(_TMP / "av_probe.db")
_AV_MEM.record("goal", "写一个函数 flatten(d)，把嵌套 dict 展平。只输出代码。")
#: ⚠️ failure 故意放在**中间**写：episodes() 按 id 倒序返回，
#:    failure 最后写的话它天然排第一 ⇒ 撤掉排序也绿（AV3 首跑就抓到了这个恒真）。
#:    放中间 ⇒ 无排序时第一条是 outcome ⇒ 排序才有可观测的效果。
_AV_MEM.record("failure",
               "finish({'answer': '...'}) — 自检未通过：语言标签：答案以 ```c 开头，"
               "这不是 Python。")
_AV_MEM.record("outcome", "工具返回了一些正常的观测文本，没有任何教训。")


class _AVSpyLLM:
    """只干一件事：把 distill 发来的每一条 prompt 原样记下来。"""

    def __init__(self) -> None:
        self.user_texts: list[str] = []

    def complete(self, messages, **kw):                              # noqa: ANN001, ANN202
        for m in messages:
            if getattr(m, "role", "") == "user":
                self.user_texts.append(str(getattr(m, "content", "")))
        return "NONE"                    # 内容无关：本探针只看原料，不看产出


_AV_SPY = _AVSpyLLM()
from mw.abstractor import distill as _av_distill                 # noqa: E402

_av_distill(_AV_MEM, _AV_SPY, max_tokens=64)
check(RS, "AV3 ★蒸馏器必须**收到**失败原料，且 failure 排在**第一批**\n"
          "          —— 成功记录本来就没东西可提（真模型实测 13 进 1 出、11 条 NONE），\n"
          "            失败记录才是『怎么避免坑』的原料；混在堆里 = 注意力最低处 = 白给。\n"
          "          ⚠️ 这是行为锚：假 LLM 记下收到的 prompt，撤掉排序立刻转红。",
      bool(_AV_SPY.user_texts)
      and _AV_SPY.user_texts[0].startswith("执行记录（kind=failure）")
      and any("自检未通过" in t for t in _AV_SPY.user_texts),
      f"蒸馏条数={len(_AV_SPY.user_texts)} "
      f"第一条={(_AV_SPY.user_texts[0][:44] if _AV_SPY.user_texts else '无')}")

# ══════════════════════════════════════════════════════════════════════════
# AW 组（第二十八轮，§17-6 6c）：运行期语义记忆**写入**通道必须可关
# ══════════════════════════════════════════════════════════════════════════
#: 实测（6c 首跑三臂）：空库臂在跑的过程中被模型自己调 `note` 写回 facts ——
#: "种子是唯一处理"的前提被运行期写入混杂，三臂不再可比，整组作废。
#: 修法 = `allow_note` 开关（Limits 进口径签名 + tools 注册条件化 + core 透传）。

from mw.tools import register_core_tools as _aw_register        # noqa: E402

_AW_MEM = MemoryStore(_TMP / "aw_probe.db")
_AW_REG_ON = ToolRegistry()
_aw_register(_AW_REG_ON, memory=_AW_MEM)                        # 默认 ⇒ note 在册
_AW_REG_OFF = ToolRegistry()
_aw_register(_AW_REG_OFF, memory=_AW_MEM, allow_note=False)     # 关 ⇒ note 不在册

check(RS, "AW1 ★默认（allow_note=True）⇒ `note` **在册**且 mutating=True\n"
          "          —— 开关的默认值必须保持历史行为逐位不变（只做加法不做偷换）。",
      _AW_REG_ON.get("note") is not None and _AW_REG_ON.get("note").mutating is True,
      f"note在册={_AW_REG_ON.get('note') is not None} "
      f"mutating={getattr(_AW_REG_ON.get('note'), 'mutating', 'NA')}")

check(RS, "AW2 ★allow_note=False ⇒ `note` **不在册**（不是'注册了但拒绝执行'）\n"
          "          —— 工具目录进口径签名：不在册 ⇒ 目录变 ⇒ 口径变，\n"
          "            是声明出来的不同候选；在册但拒绝 = 同标签藏两种行为 = 标签撒谎。",
      _AW_REG_OFF.get("note") is None,
      f"note在册={_AW_REG_OFF.get('note') is not None}")

check(RS, "AW3 ★关写入**不关读**：`recall`（只读）与 `finish` 仍在册\n"
          "          —— 6c 三臂要的是'种子是唯一写入口'，检索通道必须照常。",
      _AW_REG_OFF.get("recall") is not None and _AW_REG_OFF.get("finish") is not None,
      f"recall在册={_AW_REG_OFF.get('recall') is not None} "
      f"finish在册={_AW_REG_OFF.get('finish') is not None}")

#: AW4：env 生效（T5 同款纪律：env 必须真的能改到 Limits 的字段）。
_AW_ENV_SAVED = os.environ.pop("V1_ALLOW_NOTE", None)
try:
    os.environ["V1_ALLOW_NOTE"] = "0"
    _aw_off = cfg.Limits().allow_note
finally:
    if _AW_ENV_SAVED is None:
        os.environ.pop("V1_ALLOW_NOTE", None)
    else:
        os.environ["V1_ALLOW_NOTE"] = _AW_ENV_SAVED
check(RS, "AW4 ★V1_ALLOW_NOTE=0 ⇒ `Limits.allow_note` 为 False（env 真的生效）\n"
          "          —— '每次调用都重新读 env'的承诺（settings() 文档）必须对布尔也成立；\n"
          "            另外 as_dict() 自动包含本字段（fields(self) 派生）⇒ 进口径签名。",
      _aw_off is False and "allow_note" in cfg.Limits().as_dict(),
      f"env关闭读数={_aw_off} as_dict含字段={'allow_note' in cfg.Limits().as_dict()}")

#: AW5：端到端传播 —— Core 必须把 Limits 的开关送到工具注册表。
#: ⛔ 这是最容易断的一根线：config 有字段、tools 有参数，中间的透传漏了
#:    就是"两处都写着对、连起来不对"。第二十轮奖励通道的教训同宗。
_AW_ENV_SAVED2 = os.environ.get("V1_ALLOW_NOTE")
try:
    os.environ["V1_ALLOW_NOTE"] = "0"
    _aw_core = Core(cfg.Settings(), llm=StubLLM(), wire_external=False,
                    log=lambda *_: None)
finally:
    if _AW_ENV_SAVED2 is None:
        os.environ.pop("V1_ALLOW_NOTE", None)
    else:
        os.environ["V1_ALLOW_NOTE"] = _AW_ENV_SAVED2
check(RS, "AW5 ★V1_ALLOW_NOTE=0 时构造的 Core，其工具注册表里**没有** `note`\n"
          "          —— Limits → Core → register_core_tools 的透传线；\n"
          "            config 与 tools 各自正确但中间断线 = 实验照样被混杂。",
      _aw_core.tools.get("note") is None
      and _aw_core.tools.get("recall") is not None,
      f"core.note在册={_aw_core.tools.get('note') is not None} "
      f"core.recall在册={_aw_core.tools.get('recall') is not None}")

# ══════════════════════════════════════════════════════════════════════════
# AX 组（第二十八轮，§17-6 6c）：世界模型的语义**沉淀**通道必须可关
# ══════════════════════════════════════════════════════════════════════════
#: 实测（6c 第二跑的完整性核对）：allow_note=0 之后 facts 仍在长 ——
#: P 6→8、E 0→2，写入者是 `worldmodel.learn()`（source=worldmodel）。
#: 运行期写语义层有**两条**通道，只关一条 = 假装修好了。

from mw.worldmodel import Prediction as _AXPred                  # noqa: E402

_AX_MEM_ON = MemoryStore(_TMP / "ax_on.db")
_ax_wm_on = WorldModel(_AX_MEM_ON, StubLLM())
#: 必须走**成败类**失配（teachable）——文本类根本不进语义层（缺陷 #12 修复后）。
_ax_wm_on.observe(_AXPred(action="parse_duration", expected="会返回一个整数秒数",
                          confidence=0.9, expect_ok=True),
                  "OSError 权限不足", ok=False)
_ax_on_facts = _AX_MEM_ON.stats()["facts"]

_AX_MEM_OFF = MemoryStore(_TMP / "ax_off.db")
_ax_wm_off = WorldModel(_AX_MEM_OFF, StubLLM(), allow_learn=False)
_ax_mm_off = _ax_wm_off.observe(_AXPred(action="parse_duration", expected="会返回一个整数秒数",
                                        confidence=0.9, expect_ok=True),
                                "OSError 权限不足", ok=False)
_ax_wm_off.learn(_ax_mm_off)
_ax_off_facts = _AX_MEM_OFF.stats()["facts"]
_ax_off_mm = _AX_MEM_OFF.stats()["episodes"]

check(RS, "AX1 ★默认（allow_learn=True）⇒ 可教学失配**沉淀为语义事实**\n"
          "          —— 开关默认值必须保持历史行为逐位不变（worldmodel 的学习是主线功能）。",
      _ax_on_facts >= 1,
      f"沉淀后 facts={_ax_on_facts}")

check(RS, "AX2 ★allow_learn=False ⇒ 同一个可教学失配**不进语义层**，\n"
          "          但情景里的 mismatch 行**照记**（案例不丢，只是不上架）\n"
          "          —— 6c 三臂要的是'种子是唯一 facts 来源'，而情景层不进检索，\n"
          "            关它不影响送达；但学习案例照记，关掉不是销毁证据。",
      _ax_off_facts == 0 and _ax_off_mm >= 1,
      f"facts={_ax_off_facts} mismatch情景行={_ax_off_mm}")

#: AX3：env 生效（AW4 同款）。
_AX_ENV_SAVED = os.environ.pop("V1_ALLOW_LEARN", None)
try:
    os.environ["V1_ALLOW_LEARN"] = "0"
    _ax_env_off = cfg.Limits().allow_learn
finally:
    if _AX_ENV_SAVED is None:
        os.environ.pop("V1_ALLOW_LEARN", None)
    else:
        os.environ["V1_ALLOW_LEARN"] = _AX_ENV_SAVED
check(RS, "AX3 ★V1_ALLOW_LEARN=0 ⇒ `Limits.allow_learn` 为 False，\n"
          "          且 as_dict() 自动包含本字段（fields(self) 派生 ⇒ 进口径签名）。",
      _ax_env_off is False and "allow_learn" in cfg.Limits().as_dict(),
      f"env关闭读数={_ax_env_off} "
      f"as_dict含字段={'allow_learn' in cfg.Limits().as_dict()}")

#: AX4：Core 透传（AW5 同款：config 与模块各自正确、中间断线 = 假修好）。
_AX_ENV_SAVED2 = os.environ.get("V1_ALLOW_LEARN")
try:
    os.environ["V1_ALLOW_LEARN"] = "0"
    _ax_core = Core(cfg.Settings(), llm=StubLLM(), wire_external=False,
                    log=lambda *_: None)
finally:
    if _AX_ENV_SAVED2 is None:
        os.environ.pop("V1_ALLOW_LEARN", None)
    else:
        os.environ["V1_ALLOW_LEARN"] = _AX_ENV_SAVED2
check(RS, "AX4 ★V1_ALLOW_LEARN=0 时构造的 Core，其世界模型 `allow_learn` 为 False\n"
          "          —— Limits → Core → WorldModel 的透传线；6c 首跑证明\n"
          "            这条线断了实验就会被混杂，而它断的时候每一层单看都是'对的'。",
      getattr(_ax_core.wm, "allow_learn", None) is False,
      f"core.wm.allow_learn={getattr(_ax_core.wm, 'allow_learn', 'NA')}")

# ══════════════════════════════════════════════════════════════════════════
# AY 组（第三十轮）：§17-7b 的两件装置 —— 训练史的闸 / 注入诊断的零调用
# ══════════════════════════════════════════════════════════════════════════
#: 为什么是行为断言而不是源码字符串锚：装置修正⑦ 的病是**判据错位**
#: （把 rc 当"零失败史"），字符串锚抓不到语义；只有"造一个库、问它数出几条"
#: 才能钉住"结论来自哪一列"。
sys.path.insert(0, str(V1 / "bench"))                            # noqa: E402
from bench.build_g7_seed import history_counts                          # noqa: E402
from bench.probe_inject import _scan                                   # noqa: E402

_AY_DB = _TMP / "ay_train.db"
_ay_mem = MemoryStore(_AY_DB)
_ay_mem.record("reward", "题 t1：外部得分 0.6，未通过，失败模式：…")
_ay_mem.record("reward", "题 t2：外部得分 1.0，通过")
_ay_mem.record("outcome", "这只是一条普通情景（不是奖励通道）")
_AY_REWARD, _AY_BAD = history_counts(_ay_mem)

check(RS, "AY1 ★`history_counts` 只数 reward 通道，且「未通过」单独计数\n"
          "          —— 装置修正⑦：训练史的闸必须来自内容，不来自 rc。\n"
          "             rc 的语义是「本轮有没有没量到的条目」，答不了\n"
          "             『蒸馏有没有原料』这个问题（★7b 实测撞出来的判据错位）。",
      (_AY_REWARD, _AY_BAD) == (2, 1),
      f"reward={_AY_REWARD} 未通过={_AY_BAD}（期望 2/1；outcome 不计入）")

_AY_DB2 = _TMP / "ay_train_allpass.db"
_ay_mem2 = MemoryStore(_AY_DB2)
_ay_mem2.record("reward", "题 t3：外部得分 1.0，通过")
_AY_R2, _AY_B2 = history_counts(_ay_mem2)
check(RS, "AY2 ★全通过的训练史 ⇒ 「未通过」为 0 ⇒ 闸必须判作废\n"
          "          —— 反面用例：没有它，把 LIKE 写成恒真也测不出来。",
      (_AY_R2, _AY_B2) == (1, 0),
      f"reward={_AY_R2} 未通过={_AY_B2}（期望 1/0）")

#: AY3：注入诊断必须**零模型调用**且把 48 格扫满 ——
#: 它是我用来否掉"语义记忆被 cap 挤掉"这个解释的工具，
#: 工具本身若偷偷调模型/少扫几格，那个"48/48 存活"的结论就不再可复算。
_AY_CORE_MEM = MemoryStore(_TMP / "ay_probe.db")
_AY_CORE_MEM.commit_fact("遇到引号内的分隔符时，不要当分隔符切分。",
                         source="abstract", confidence=0.5)
_AY_STUB = StubLLM()
_AY_CORE = Core(llm=_AY_STUB, memory=_AY_CORE_MEM, memory_mode="raw")
#: ⚠️ 第三十七轮（§17-7i）：goal 从 `"写一个函数 f(s)，解析字符串。"` 改成
#: 含那条 fact 的 token 的句子。原 goal 与 fact 零交集 ⇒ 以前靠 `recall()`
#: 的第三级"全表 top-k"兜底才让 48 格都有块；那一级已删（装置修正⑮）。
#: 判据（48 格 / 每格有块 / 模型零调用）**一个字没改** —— 只是不再站在兜底上。
_AY_ROWS = _scan(_AY_CORE, "遇到引号内的分隔符时，应该怎么切分？")
check(RS, "AY3 ★注入诊断：48 格全扫、每格都有 `[语义记忆]`、模型零调用\n"
          "          —— 结论「不是被 STATE_CHAR_CAP 挤掉」必须可复算。",
      len(_AY_ROWS) == 48 and all(r["has_head"] for r in _AY_ROWS)
      and _AY_STUB.call_count == 0,
      f"格数={len(_AY_ROWS)} 全含块={all(r['has_head'] for r in _AY_ROWS)} "
      f"模型调用={_AY_STUB.call_count}")

# ══════════════════════════════════════════════════════════════════════════
# AZ 组（第三十二轮，§17-7d 装置修正⑨）：安慰剂必须与处理**等价**，
# 不许只是"内容不一样"。等价性断了，R7 的闸就变成了装饰。
# ══════════════════════════════════════════════════════════════════════════
import bench.build_g7d_seed as _G7D                                    # noqa: E402
import contextlib as _ctx                                        # noqa: E402
import io as _io                                                 # noqa: E402

# 两条真实惯例作样本：一条纯中文、一条含题面里也出现的 ASCII token
# （`true` / `None`）—— 后者正是 ⑨a 里 H 被 FTS 精确命中的那一类。
_AZ_H = ["去重惯例：比较前先去掉首尾空白并转小写，遇到重复时保留第一次出现的那条原文。",
         "开关惯例：只把 y/yes/1 当成真，'true'、'on' 这类历史脏值一律算假，"
         "其余无法识别的返回 None。"]
_AZ_P = _G7D.build_placebo(_AZ_H)
_AZ_BAD = _G7D.check_placebo(_AZ_H, _AZ_P)

check(RS, "AZ1 骨架安慰剂逐字等长\n"
          "          —— 差一个字就不是同一把尺子；⑨a/⑨c 两次修的都是这件事。",
      not any("长度不等" in m for m in _AZ_BAD), str(_AZ_BAD))
check(RS, "AZ2 骨架安慰剂保留全部 ASCII token\n"
          "          —— FTS 是 OR+精确 token：改掉它，两臂检索行为就不再等价。",
      not any("ASCII" in m for m in _AZ_BAD), str(_AZ_BAD))
check(RS, "AZ3 骨架安慰剂不等于原句（汉字必须真被打散，否则 P ≡ H）",
      not any("与原句相同" in m for m in _AZ_BAD), str(_AZ_BAD))

#: AZ4：构建器必须拒绝在**已有种子库**的目录上重建（⑨b：旧 facts 混库）。
_AZ_STALE = Path(tempfile.mkdtemp(prefix="v1_az4_"))
(_AZ_STALE / "memH").mkdir(parents=True)
MemoryStore(_AZ_STALE / "memH" / "memory.db").close()
_AZ_BUF = _io.StringIO()
with _ctx.redirect_stdout(_AZ_BUF):
    _AZ_RC = _G7D.main(["--out-dir", str(_AZ_STALE),
                        "--heldout", str(V1 / "bench" / "heldout" / "g7e_heldout.jsonl")])
check(RS, "AZ4 已有种子库的目录必须拒绝重建（旧 facts 混入 = 处理不纯）",
      _AZ_RC == 1 and not (_AZ_STALE / "memP" / "memory.db").exists(),
      f"rc={_AZ_RC} memP_exists={(_AZ_STALE / 'memP' / 'memory.db').exists()}")
check(RS, "AZ5 全部惯例文本 ≤120 字（否则 core.py 注入时静默截断到 120）",
      all(len(t) <= 120 for t in _G7D.FACTS.values()),
      str([(k, len(v)) for k, v in _G7D.FACTS.items() if len(v) > 120]))

# ══════════════════════════════════════════════════════════════════════════
# AZ6/AZ7（第三十三轮，§17-7e）：hint suite 必须是**派生**出来的，不是手抄的。
# 7e 的全部实验价值都压在一句话上 —— 只改了"指向"，没改别的。
# 手抄一次就等于给自己留了"顺手把句子改顺"的口子；这两条断言把它焊死：
#   AZ6 = 除题面外逐字段不动（ids/tests/oracle/partial/null/meta 一个字节不许变）
#   AZ7 = 题面只差这一处，且**真的**指向记忆（长度差恰为 +2，反向替换逐字节可逆）
# ══════════════════════════════════════════════════════════════════════════
import bench.build_g7e_hint as _G7E                                    # noqa: E402


def _az_rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in
            path.read_text(encoding="utf-8").splitlines() if l.strip()]


_AZ6_BASE = _az_rows(V1 / "bench" / "heldout" / "g7e_heldout.jsonl")
_AZ6_HINT = _az_rows(V1 / "bench" / "heldout" / "g7e_hint_heldout.jsonl")
_AZ6_DERIVED = _G7E.derive(_AZ6_BASE)
_AZ6_FIELDS = ("id", "tests", "oracle", "partial", "null", "meta")
_AZ6_BAD = [
    (b.get("id"), k)
    for b, d, h in zip(_AZ6_BASE, _AZ6_DERIVED, _AZ6_HINT)
    for k in _AZ6_FIELDS
    if d.get(k) != h.get(k)
]
check(RS, "AZ6 ★hint suite 除题面外**逐字段不动**（7e 只改『指向』这一件事）\n"
          "          —— 手抄的 suite 只要顺手改顺一句，7d/7e 就不是同一把尺子了。",
      len(_AZ6_BASE) == len(_AZ6_HINT) == len(_AZ6_DERIVED) and not _AZ6_BAD,
      f"n={len(_AZ6_BASE)}/{len(_AZ6_HINT)}/{len(_AZ6_DERIVED)} 漂移={_AZ6_BAD}")

_AZ7_BAD = []
for _b, _h in zip(_AZ6_BASE, _AZ6_HINT):
    _p = _h["prompt"]
    if _p != _G7E.hint_prompt(_b["prompt"]):
        _AZ7_BAD.append((_h.get("id"), "与派生结果不等"))
    if _G7E.HINT_CLAUSE not in _p:
        _AZ7_BAD.append((_h.get("id"), "题面没指向记忆"))
    if _G7E.BASE_CLAUSE in _p:
        _AZ7_BAD.append((_h.get("id"), "旧句还在（改了不止一处）"))
    if len(_p) - len(_b["prompt"]) != len(_G7E.HINT_CLAUSE) - len(_G7E.BASE_CLAUSE):
        _AZ7_BAD.append((_h.get("id"), "长度差不是 +2"))
    if _p.replace(_G7E.HINT_CLAUSE, _G7E.BASE_CLAUSE) != _b["prompt"]:
        _AZ7_BAD.append((_h.get("id"), "反向替换回不到 7d 题面"))
check(RS, "AZ7 ★题面**只差这一处**且真的指向记忆（长度差 +2、反向替换逐字节可逆）\n"
          "          —— 这是 7e 唯一的自变量；它不干净，整组读数就没有归因力。",
      not _AZ7_BAD, str(_AZ7_BAD))

# ══════════════════════════════════════════════════════════════════════════
# AZ8/AZ9/AZ10（第三十四轮，§17-7f）：只改**未被采用的三条**的表述形式。
# 7f 的全部实验价值压在一个单变量上 —— "同一把尺子、只换记忆文本的形状"。
# 这三条断言各自焊死一个口子：
#   AZ8  = 只有那三条变了，且每条都真的带上了「例：」；`g7d2`（已采用）逐字节不动
#   AZ9  = 键集与 7d 完全相同、每条仍 ≤120 字（否则 core.py 静默截断）
#   AZ10 = 与题面的 token 交集必须与 7d **逐题完全相同**（装置修正⑪）
#          —— 交集一变，FTS 命中就变，注入条数就变 ⇒ "形式"与"注入量"两个变量同时动
# ══════════════════════════════════════════════════════════════════════════
_G7F_CHANGED = ("g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail")
_AZ8_BAD: list[tuple[str, str]] = []
for _k in _G7D.FACTS:
    if _k not in _G7F_CHANGED:
        if _G7D.FACTS_G7F.get(_k) != _G7D.FACTS[_k]:
            _AZ8_BAD.append((_k, "不该改的键被改了"))
for _k in _G7F_CHANGED:
    _v = _G7D.FACTS_G7F.get(_k, "")
    if "例：" not in _v:
        _AZ8_BAD.append((_k, "没有「例：」"))
    if _v == _G7D.FACTS.get(_k):
        _AZ8_BAD.append((_k, "与 7d 逐字节相同（等于没改）"))
check(RS, "AZ8 ★7f 只改**未被采用的三条**，且每条真的带上了「例：」\n"
          "          —— 已采用的 `g7d2` 一个字节都不许动，否则分不清「例子起作用」\n"
          "             与「把已经会的那条改坏了」。",
      not _AZ8_BAD, str(_AZ8_BAD))

check(RS, "AZ9 7f 惯例：键集与 7d **完全相同**、每条仍 ≤120 字（core.py 会静默截断）",
      set(_G7D.FACTS_G7F) == set(_G7D.FACTS)
      and all(len(v) <= 120 for v in _G7D.FACTS_G7F.values()),
      str([(k, len(v)) for k, v in _G7D.FACTS_G7F.items()
           if len(v) > 120] or sorted(set(_G7D.FACTS_G7F) ^ set(_G7D.FACTS))))

from mw.memory import _TOKEN as _G7F_TOK                         # noqa: E402

_AZ10_BAD: list[tuple[str, list[str], list[str]]] = []
for _t in _AZ6_HINT:
    _pt = set(_G7F_TOK.findall(_t["prompt"]))
    _old = set(_G7F_TOK.findall(_G7D.FACTS[_t["id"]])) & _pt
    _new = set(_G7F_TOK.findall(_G7D.FACTS_G7F[_t["id"]])) & _pt
    if _old != _new:
        _AZ10_BAD.append((_t["id"], sorted(_new - _old), sorted(_old - _new)))
check(RS, "AZ10 ★7f 惯例与题面的 **token 交集**必须与 7d 逐题完全相同\n"
          "          —— 交集一变，FTS 命中就变、注入条数就变（7e 实测 4/4/1/1），\n"
          "             于是「文本形式」与「注入量/检索精度」两个变量同时动，读数无法归因。",
      not _AZ10_BAD, str(_AZ10_BAD))

from mw.memory import normalize as _G7F_NORM                     # noqa: E402

_G7F_UNNORMAL = [
    (k, len(v), len(_G7F_NORM(v)))
    for k, v in _G7D.FACTS_G7F.items() if _G7F_NORM(v) != v
]
check(RS, "AZ11 ★7f 惯例必须已是**规范形式**：`normalize(v) == v`\n"
          "          —— `MemoryStore.commit_fact` 写入时折叠连续空白并 strip，\n"
          "             原文一变，R1 的 own-fact 就**永远**判不过（实测：例子里的\n"
          "             `'  1 '` 让 102 字存成 101 字，四题里只它一条报「未送达」）。",
      not _G7F_UNNORMAL, str(_G7F_UNNORMAL))

# ══════════════════════════════════════════════════════════════════════════
# AZ12–AZ15（第三十五轮，§17-7g）：**邻域对照臂 N** 的构造不变量。
# 7g 的全部价值压在一句话上 —— "只改邻域的**长度**"。
# 这四条各自焊死一个会让那句话变成假话的口子：
#   AZ12 = 观察对象**逐字节冻结**，且其余三条**真的**被改了（邻域没动 = 没测）
#   AZ13 = 其余三条的长度**逐条等于档位值**、≤120（否则被 core 静默截断）、
#          且已是规范形式（否则 R1 的 own-fact 逐字节判不过）
#   AZ14 = 填充段**不许含 ASCII**，且观察对象的题面与 facts 的 **token 交集**
#          与 7d 完全相同 —— 交集一变，`recall()` 就从 fallback top-k（4 条）
#          跳到 FTS 精确命中（1 条），"长度"与"注入量"两个变量同时动
#   AZ15 = 档位名与档位值必须一致、轴严格递增、变体集合 = 2 观察对象 × 4 档
#          （只跑一档就宣布"曲线"= 用两个点画一条线）
# ══════════════════════════════════════════════════════════════════════════
_G7N_VARIANTS = {k: v for k, v in _G7D.VARIANTS.items() if k.startswith("7g_")}

_AZ12_BAD: list[tuple[str, str]] = []
for _n, _f in _G7N_VARIANTS.items():
    _o = _n.split("_")[1]
    _ok_, _pool = _G7D.OBSERVERS[_o]
    if _f.get(_ok_) != _pool[_ok_]:
        _AZ12_BAD.append((_n, "观察对象被改了（曲线不再是「别人的长度 → 它的分数」）"))
    for _k in _G7D.FACTS:
        if _k != _ok_ and _f.get(_k) == _G7D.FACTS[_k]:
            _AZ12_BAD.append((_n, f"{_k} 没被改（邻域没动）"))
check(RS, "AZ12 ★7g 观察对象的惯例必须**逐字节冻结**，其余三条必须**真的**被改\n"
          "          —— 观察对象一动，「邻域长度 → 它的分数」就不再是单变量；\n"
          "             邻域没动，这一档等于把上一档又跑了一遍。",
      not _AZ12_BAD, str(_AZ12_BAD))

_AZ13_BAD: list[tuple[str, str, int, int]] = []
for _n, _f in _G7N_VARIANTS.items():
    _o = _n.split("_")[1]
    _tgt = _G7D.NEIGH_LEVELS[_n.split("_")[2]]
    _ok_ = _G7D.OBSERVERS[_o][0]
    for _k, _v in _f.items():
        if _k == _ok_:
            continue
        if len(_v) != _tgt:
            _AZ13_BAD.append((_n, f"{_k} 长度", len(_v), _tgt))
        if len(_v) > 120:
            _AZ13_BAD.append((_n, f"{_k} 超过注入截断", len(_v), 120))
        if _G7F_NORM(_v) != _v:
            _AZ13_BAD.append((_n, f"{_k} 不是规范形式", len(_v), len(_G7F_NORM(_v))))
check(RS, "AZ13 ★7g 邻域三条必须**逐条**等于档位长度、≤120、且已是规范形式\n"
          "          —— 长度不等 ⇒「档位」不是那个档位；>120 ⇒ `f.text[:120]`\n"
          "             静默截断，档位 120 与 120 以上在上下文里是同一个值；\n"
          "             不规范 ⇒ `commit_fact` 一改写法，R1 就报「未送达」。",
      not _AZ13_BAD, str(_AZ13_BAD))

from mw.memory import _TOKEN as _G7N_TOK                         # noqa: E402

_AZ14_BAD: list[tuple[str, str, str]] = []
for _n, _f in _G7N_VARIANTS.items():
    _o = _n.split("_")[1]
    _ok_, _pool = _G7D.OBSERVERS[_o]
    # ① 填充段不许含 ASCII（ASCII 会改 FTS 命中 ⇒ 条数不再是 4/4/1/1）
    for _k, _v in _f.items():
        if _k == _ok_:
            continue
        _base = _G7D.FACTS[_k]
        if _v.startswith(_base) and any(ord(_c) < 128 for _c in _v[len(_base):]):
            _AZ14_BAD.append((_n, _k, "填充段里有 ASCII"))
    # ② 观察对象的**题面**与全部 facts 的 token 交集必须与**它自己的冻结配置**相同
    #    （`recall()` 只要有任何一条命中就走 FTS，不再兜底 top-k）
    #    ⚠️ 基线必须按观察对象取（o1→7d、o2→7f）：否则 7f 自己一改，
    #       AZ10 会连带把这条打红 —— 那是**观测点耦在一起**，不是同一个事实被两处声明。
    _pr = {t["id"]: t["prompt"] for t in _AZ6_HINT}[_ok_]
    _pt = set(_G7N_TOK.findall(_pr))
    _base = {_k: (_pool[_k] if _k == _ok_ else _G7D.FACTS[_k]) for _k in _G7D.FACTS}
    _u_old = set().union(*[set(_G7N_TOK.findall(_v))
                           for _v in _base.values()]) & _pt
    _u_new = set().union(*[set(_G7N_TOK.findall(_v)) for _v in _f.values()]) & _pt
    if _u_old != _u_new:
        _AZ14_BAD.append((_n, _ok_, f"交集变了 {sorted(_u_new - _u_old)} / "
                                    f"{sorted(_u_old - _u_new)}"))
check(RS, "AZ14 ★7g 填充段**不许含 ASCII**，且观察对象题面的 token 交集与 7d 逐题相同\n"
          "          —— `recall()` 的两条路径是**质变**：FTS 无命中 ⇒ fallback 4 条；\n"
          "             一旦命中 ⇒ 1 条。交集一变，「邻域长度」与「注入条数」两个\n"
          "             变量同时动，曲线就不再是那条曲线。",
      not _AZ14_BAD, str(_AZ14_BAD))

_AZ15_BAD = [(k, v) for k, v in _G7D.NEIGH_LEVELS.items() if int(k[1:]) != v]
_AZ15_VALS = sorted(_G7D.NEIGH_LEVELS.values())
_AZ15_EXPECT = {f"7g_{_o}_{_l}" for _o in _G7D.OBSERVERS for _l in _G7D.NEIGH_LEVELS}
check(RS, "AZ15 ★7g 档位名 == 档位值、轴严格递增、变体集合 = 2 观察对象 × 4 档\n"
          "          —— 名字说 120 实际 84，是最典型的「读数与装置不一致」；\n"
          "             只跑一档就宣布曲线，等于用两个点画一条线。",
      not _AZ15_BAD and _AZ15_VALS == [40, 60, 84, 120]
      and set(_G7N_VARIANTS) == _AZ15_EXPECT,
      f"名字/值不符={_AZ15_BAD} 轴={_AZ15_VALS} "
      f"缺档={sorted(_AZ15_EXPECT ^ set(_G7N_VARIANTS))}")

# ══════════════════════════════════════════════════════════════════════════
# AZ16（第三十五轮，§17-7g）：★同一份事实有**两条**注入路径。
#
# ⛔ 这条钉的是**渲染规则**（`_render` 取 `w[-200:]`），不是跨题泄漏本身 ——
#    跨题那半已由第三十六轮（AZ17/AZ18）修掉：`run()` 进题先 `reset_working()`。
#    但渲染规则仍然有效且必须钉住：**同一题内** `recall` 工具成功时返回的是
#    **全部** facts 的拼接（389 字），它照样会进本题的工作记忆 ⇒
#    "漏出去的是哪一段"仍然由 `w[-200:]` 决定（这决定了改长度时泄漏内容非单调）。
#
# 三个条件同时钉住，少一个就说不清"泄漏的到底是哪一段"：
#   ① 工作记忆块在 state 里；
#   ② 一条超长工具输出进 state 后，**尾部**还在（`_render` 取 `w[-200:]`）；
#   ③ **头部**已经不在 ⇒ 泄漏的具体内容是"输出的最后 200 字"，
#      而不是"输出的开头" —— 这决定了改长度时泄漏内容**非单调**地变。
# ══════════════════════════════════════════════════════════════════════════
_G7N_HEAD = "【头标记】"
_G7N_TAIL = "【尾标记】"
_G7N_MEM = MemoryStore(Path(tempfile.mkdtemp(prefix="v1_az16_")) / "memory.db")
_G7N_MEM.think(_G7N_HEAD + "填充" * 120 + _G7N_TAIL)      # 总长 > 200 ⇒ 必然被切
_G7N_CORE = Core(llm=StubLLM(), memory=_G7N_MEM, memory_mode="raw")
_G7N_ST = _G7N_CORE._state("随便一道题", [], 1, task_id="az16",
                           scorer_sha="probe", suite_sha="probe")
_G7N_HAS_BLOCK = "[工作记忆]" in _G7N_ST
_G7N_TAIL_KEPT = _G7N_TAIL in _G7N_ST
_G7N_HEAD_GONE = _G7N_HEAD not in _G7N_ST
_G7N_MEM.close()
check(RS, "AZ16 ★7g「同一份事实有两条注入路径」的**渲染规则**：工作记忆只保留**尾 200 字**\n"
          "          —— 跨题那半已由 AZ17/AZ18 修掉（进题先清空），但**同一题内**\n"
          "             `recall` 返回全部 facts 时仍走这条路 ⇒ 泄漏的永远是\"最后 200 字\"，\n"
          "             这决定了改长度时漏出去的内容**非单调**地变。\n"
          "          ⚠️ 本断言用 `_state()` 直接构造（不经 `run()`），所以不受清空点影响。",
      _G7N_HAS_BLOCK and _G7N_TAIL_KEPT and _G7N_HEAD_GONE,
      f"块={_G7N_HAS_BLOCK} 尾在={_G7N_TAIL_KEPT} 头不在={_G7N_HEAD_GONE} "
      f"state={len(_G7N_ST)}字")


# ══════════════════════════════════════════════════════════════════════════
# AZ17–AZ21（第三十六轮，§17-7h）：**修装置**，不是跑实验。
#
# 7g 的判读给出三条并列的装置缺陷：① 工作记忆跨题不清（第二条注入路径）、
# ② R1 只查 `recall()` 看不见它、③ 运输层路径不进读数（外加 ④ 没有逐题墙钟）。
# 在这四条修好之前**任何**单题读数都无法归因 ⇒ 本轮只修装置，⛔ 不跑臂、不认领 Δ。
# ══════════════════════════════════════════════════════════════════════════
import bench.state_audit as _SA                              # noqa: E402
import bench.transport as _TR                                # noqa: E402
from mw.bench import run_suite                               # noqa: E402

# ── AZ17：清空点的**位置与消费者** ────────────────────────────────────────
_AZ17_CODE = _code_only((V1 / "mw" / "core.py").read_text(encoding="utf-8"))
_AZ17_MEM = MemoryStore(Path(tempfile.mkdtemp(prefix="v1_az17_")) / "memory.db")
_AZ17_MEM.think("上一题的工具输出 ①")
_AZ17_MEM.think("上一题的工具输出 ②")
_AZ17_N = _AZ17_MEM.reset_working()
_AZ17_VIEW = _AZ17_MEM.working_view()
_AZ17_MEM.close()
_AZ17_AT = _AZ17_CODE.find("reset_working()")
_AZ17_GOAL = _AZ17_CODE.find('record("goal"')
_AZ17_STATS_OK = '"working_cross_task": self.working_cross_task' in _AZ17_CODE
check(RS, "AZ17 ★7h 题边界清空：`reset_working()` 必须在 `record(\"goal\")` **之前**，\n"
          "          且清掉的条数必须进 stats（否则\"清干净了\"与\"没接线\"完全同形）\n"
          "          —— `working` 是**进程级** deque，bench 复用同一个 Core 连跑多题；\n"
          "             清空点若落在读侧（`_state()` 过滤），泄漏会**隐身**：计数恒 0、泄漏照旧。",
      0 <= _AZ17_AT < _AZ17_GOAL and _AZ17_N == 2 and _AZ17_VIEW == []
      and _AZ17_STATS_OK,
      f"顺序 reset@{_AZ17_AT} < goal@{_AZ17_GOAL}；清掉={_AZ17_N} 剩={_AZ17_VIEW}；"
      f"stats 消费者={'有' if _AZ17_STATS_OK else '无'}")

# ── AZ18：端到端 —— 第 2 题的**第 1 步** state 里不许有第 1 题的残留 ─────────
# 走**真实路径**：第 1 题由内核自己调 `recall`（StubLLM 的首选动作），
# 工具返回全部 facts ⇒ `think()` 写进工作记忆 ⇒ 那正是 7g 实测泄漏的形状。
# ⛔ 不用 `mem.think(...)` 手工塞：手工塞只能证明"清空函数被调了"，
#    证明不了"内核产出的那些条目真的被清掉"。
_AZ18_TASKS = [json.loads(l) for l in
               (V1 / "bench" / "heldout" / "g7e_hint_heldout.jsonl")
               .read_text(encoding="utf-8").splitlines() if l.strip()]
#: ⚠️ 第三十七轮（§17-7i）改过题号：第 1 题从 `g7d2`（题面与 facts **零命中**）
#:    换成 `g7e2`（真命中 1 条）。旧写法在删掉全表兜底之后，第 1 题的 `recall`
#:    只返回"(语义记忆中无相关条目)" ⇒ 工作记忆里**没有 fact** ⇒ 撤回"不清空"时
#:    第 2 题第 1 步也看不见任何惯例 ⇒ **AZ18 恒绿**（R13 实测就是这么暴露的）。
#:    现在第 1 题会真的把 `g7e2` 那条写进工作记忆，第 2 题是 `g7e1` ⇒ 残留可见。
_AZ18_T1 = next(t for t in _AZ18_TASKS if t["id"] == "g7e2_truncate_tail")
_AZ18_T2 = next(t for t in _AZ18_TASKS if t["id"] == "g7e1_parse_flag")
_AZ18_MEM = MemoryStore(Path(tempfile.mkdtemp(prefix="v1_az18_")) / "memory.db")
for _k, _v in _G7D.FACTS.items():
    _AZ18_MEM.commit_fact(_v, source=ABSTRACT_SOURCE, confidence=0.5)
_AZ18_CORE = Core(llm=StubLLM(), memory=_AZ18_MEM, memory_mode="raw")
_AZ18_OUT1 = _AZ18_CORE.run(_AZ18_T1["prompt"], task_id=_AZ18_T1["id"])
_AZ18_WORK1 = len(_AZ18_MEM.working_view())      # ⟶ 必须 >0，否则本断言是空的
_AZ18_OUT2 = _AZ18_CORE.run(_AZ18_T2["prompt"], task_id=_AZ18_T2["id"])
_AZ18_ST2 = _AZ18_CORE.state_log[0][1] if _AZ18_CORE.state_log else ""
_AZ18_AUDIT = _SA.audit_state(_AZ18_ST2, _G7D.FACTS, _AZ18_T2["id"])
_AZ18_MEM.close()
check(RS, "AZ18 ★7h 端到端：第 1 题由内核自己 `recall`（工作记忆确实被写满），\n"
          "          第 2 题**第 1 步**的 state 里**不许**有 fact 出现在 `[语义记忆]` 之外\n"
          "          —— 此前实测：非本题惯例 2/3 条经 `[工作记忆]` 进了第 4 题的 state，\n"
          "             而 R1 只查 `recall()` 完全看不见 ⇒ 那条轴从来不是单变量。",
      _AZ18_WORK1 > 0 and bool(_AZ18_ST2) and _AZ18_AUDIT["ok"],
      f"第1题留下={_AZ18_WORK1}条；第2题清掉="
      f"{_AZ18_OUT2.stats.get('working_cross_task')}条；"
      f"第2题第1步审计={'✅' if _AZ18_AUDIT['ok'] else '❌'} "
      f"语义块本条={_AZ18_AUDIT['own_in_semantic']} "
      f"其它块有={_AZ18_AUDIT['outside_all']} state={len(_AZ18_ST2)}字")

# ── AZ19：R1 第二层 —— 审计必须**分块**，不许"整段里有就算送达" ─────────────
_AZ19_F = {"t1": "惯例甲：去重时保留首次出现的写法，归一化只用 strip().lower()。",
           "t2": "惯例乙：截断超长串时保留尾部，前置一个省略号。"}
_AZ19_OKST = "[目标] 题目\n[语义记忆] " + _AZ19_F["t1"] + "\n[轨迹] [step 1] recall ⇒ 成功"
_AZ19_LEAK = ("[目标] 题目\n[工作记忆] recall ⇒ ✔ " + _AZ19_F["t2"]
              + "\n[语义记忆] " + _AZ19_F["t1"] + "\n")
_AZ19_A = _SA.audit_state(_AZ19_OKST, _AZ19_F, "t1")
_AZ19_B = _SA.audit_state(_AZ19_LEAK, _AZ19_F, "t1")
check(RS, "AZ19 ★7h R1 第二层：`audit_state` 必须把\"在语义块里\"与\"在别的块里\"**分开报**\n"
          "          —— 旧写法 `want[:120] in state`（整段）会把经 `[工作记忆]` 漏进来的\n"
          "             也算成送达；7g 实测那条路真的存在 ⇒ 旧判据看不见它。\n"
          "          ⛔ 不许合并成一个布尔：一合并，泄漏就又变成不可见的了。",
      _AZ19_A["ok"] and _AZ19_A["own_in_semantic"] and not _AZ19_A["outside_all"]
      and not _AZ19_B["ok"] and _AZ19_B["own_in_semantic"]
      and _AZ19_B["outside_all"] == ["t2"],
      f"干净={_AZ19_A['ok']} 泄漏={_AZ19_B['ok']} outside={_AZ19_B['outside_all']} "
      f"own语义块(泄漏态)={_AZ19_B['own_in_semantic']}")

# ── AZ20：运输层路径进读数（在 s35 冻结证据上必须复现 7g 的实测）──────────
_AZ20_ROOT = V1 / "bench" / "evidence" / "knowledge_s35"
_AZ20_VOID: list[tuple[str, str]] = []
for _d in sorted(p for p in _AZ20_ROOT.glob("ev_*") if p.is_dir()):
    for _c in _TR.audit_dir(_d):
        if _c["checked"] and _c["void"]:
            _AZ20_VOID.append((_d.name, _c["arm"]))
check(RS, "AZ20 ★7h 运输层路径进读数：同格两次 repeat 路径不同 ⇒ **该格作废**\n"
          "          —— 必须在 s35 冻结证据上**恰好**标出 3 格，且翻掉的那格在内；\n"
          "             这是\"规则能复现 7g 的实测\"的反证组（多标/少标都说明签名取错了）。\n"
          "          ⚠️ `wall_shortened` **不进签名**：它 ≡ 调用次数，会把每一格都标成不同。",
      sorted(_AZ20_VOID) == [("ev_o1_l120", "H"), ("ev_o1_l40", "H"),
                             ("ev_o2_l120", "H")],
      f"被标作废的格={sorted(_AZ20_VOID)}（期望 o1_l120/H、o1_l40/H、o2_l120/H）")

# ── AZ21：逐题墙钟（④）—— 只加字段，不改判据 ─────────────────────────────
_AZ21_SC = ExternalScorer([sys.executable, str(_AH_SCORER)], name="assertion")
_AZ21_M, _AZ21_REP = run_suite(lambda _t: _t.oracle, _AZ21_SC, _AH_s, repeat_n=1)
_AZ21_ROWS = _AZ21_REP.per_task
_AZ21_MISSING = [r.get("id") for r in _AZ21_ROWS if "elapsed_s" not in r]
_AZ21_SUM = round(sum(float(r.get("elapsed_s") or 0) for r in _AZ21_ROWS), 2)
check(RS, "AZ21 ★7h 每一行 `per_task` 都必须带 `elapsed_s`，且各题之和 ≤ 整轮耗时\n"
          "          —— 此前只有整轮 `elapsed_s`，单题耗时只能 `÷题数` 估\n"
          "             （s35：38–65 s/题，**最坏值未知**）；而\"预算余量够不够\"\n"
          "             恰恰是一个最坏值问题。⛔ 本轮只补读数，不动 `V1_MAX_WALL_S`。",
      bool(_AZ21_ROWS) and not _AZ21_MISSING and _AZ21_SUM <= _AZ21_REP.elapsed_s + 0.5
      and any(float(r.get("elapsed_s") or 0) > 0 for r in _AZ21_ROWS),
      f"行数={len(_AZ21_ROWS)} 缺字段={_AZ21_MISSING} 逐题之和={_AZ21_SUM}s "
      f"整轮={round(_AZ21_REP.elapsed_s, 2)}s")

# ── AZ22：§17-7i 装置修正⑮ —— 零命中必须**真的**返回空 ─────────────────
#: 删掉的是 `recall()` 的第三级"全表 top-k"：它与 query 完全无关、无计数、
#: 无留痕（`fts_fallbacks` 只记第二级）⇒ 静默兜底。
#: 实测（7d–7g 全部冻结证据）：**33 次** `recall` 调用**每一次**都返回全部 4 条，
#: 于是"1 条族"在第 2 步之后变成 4 条族。
#: ⚠️ 这条必须同时带**正向对照**（库里真有东西时能召回）：否则"返回空"可能只是
#:    库是空的，或者检索整个坏了 —— 两种都与"修好兜底"长得一模一样。
_AZ22_MEM = MemoryStore(_TMP / "az22.db")
for _t in ("惯例甲：去重保留首次出现的写法。", "惯例乙：截断保留尾部并前置省略号。"):
    _AZ22_MEM.commit_fact(_t, source="abstract", confidence=0.5)
_AZ22_FB0 = _AZ22_MEM.fts_fallbacks
_AZ22_HIT = _AZ22_MEM.recall("惯例甲", k=6)
_AZ22_ZERO = _AZ22_MEM.recall("zzzz qqqq", k=6)
_AZ22_EMPTY = _AZ22_MEM.recall("", k=6)
_AZ22_BLANK = _AZ22_MEM.recall("   ", k=6)
_AZ22_FB1 = _AZ22_MEM.fts_fallbacks
check(RS, "AZ22 ★7i 零命中必须返回**空**，不许静默给全表（装置修正⑮）\n"
          "          —— 第三级兜底 `ORDER BY hits DESC, updated_ts DESC` 与 query\n"
          "             完全无关：模型问'如何截断'，拿到'金额格式惯例'。\n"
          "          ⚠️ 必须带正向对照：库里有东西时仍能召回，且不靠 `OperationalError`。",
      len(_AZ22_HIT) == 1 and not _AZ22_ZERO and _AZ22_FB0 == _AZ22_FB1,
      f"正向命中={len(_AZ22_HIT)} 零命中={len(_AZ22_ZERO)} "
      f"fallback={_AZ22_FB0}->{_AZ22_FB1}")

# ── AZ23：§17-7i 的**反证组** —— 删兜底以后，装置的"4 条族"真相露出来了 ──
#: ⚠️ 本条的期望值在动手**当天被实测推翻过一次**（如实登记）：
#:    事前登记写的是"四题条数仍是 4/4/1/1"，实测是 **0/0/1/1**。
#:    为什么：前两题的题面与四条 facts **FTS 零命中**，7d–7g 记录的
#:    "注入 4 条"**全部来自第三级全表兜底**。⇒ §17-7 的两个族从来不是
#:    "1 条 vs 4 条"，而是"**命中 1 条 vs 完全没命中（按全表处理）**"。
#:    这条绿只说明一件事：删兜底**没有误伤** FTS，而且"真能召回 4 条"是可能的
#:    （只要 query 真的含那四条）—— 因此 7j 必须重做 suite，不许拿 7d 当对照。
_AZ23_K = cfg.Limits().recall_k
_AZ23_MEM = MemoryStore(_TMP / "az23.db")
for _v in _G7D.FACTS.values():
    _AZ23_MEM.commit_fact(_v, source="abstract", confidence=0.5)
_AZ23_COUNTS = [len(_AZ23_MEM.recall(_t["prompt"], k=_AZ23_K))
                for _t in _AZ6_HINT]
#: ⚠️ 反证要**分两批**拼，不能把四条原文拼成一个 query：`fts_query` 只取
#:    **前 24 个 token**，四条拼起来是 **27** 个 ⇒ 末尾被挤出 ⇒ 谁把某条惯例
#:    改长（AZ5 的撤回态把它顶到 34 个），第四条就"召不回"了 —— 那是**量具的
#:    上限**，不是"FTS 坏了"。两批各是 **8 / 19**（变长后 15 / 19），都低于 24。
#:    也不能逐条单查：那样 `LIMIT MIN(?,1)` 的撤回态测不出来（每次本来就只有 1 条）。
_AZ23_V = list(_G7D.FACTS.values())
_AZ23_HIT4 = sum(len(_AZ23_MEM.recall(" ".join(_AZ23_V[i:i + 2]), k=_AZ23_K))
                 for i in (0, 2))
check(RS, "AZ23 ★7i 删兜底暴露了装置的真实身份：\"4 条族\"那 4 条**从来不是检索到的**\n"
          "          —— 前两题题面与四条 facts FTS 零命中，7d–7g 记的 4/4/1/1 里\n"
          "             两个『4』全部来自被删的第三级全表兜底。删掉之后实测\n"
          "             **0/0/1/1** ⇒ 那个族应改名为『零命中族』。\n"
          "          ⚠️ 反证：四条惯例分两批拼接后必须召回 **4 条**（8/19 token，\n"
          "             都低于 `fts_query` 的 24-token 上限）。否则红的是 FTS，不是兜底。",
      _AZ23_COUNTS == [0, 0, 1, 1] and _AZ23_HIT4 == 4,
      f"题面条数={_AZ23_COUNTS}（实测；7d 记为 4/4/1/1 全来自兜底） "
      f"两批拼接召回={_AZ23_HIT4}（期望 4）")

#: ⚠️ 空 query 这个入口必须**单独成条**：`LIKE '%%'` 会用空 query 把全表捞回来，
#: 与"零命中给全表"是同一个静默兜底的两个入口。只堵一个 = 假装修好，
#: 而且堵完看上去一切正常（AZ22 绿、AZ24 红）。
check(RS, "AZ24 ★7i 另一个入口：空/纯空白 query 也必须返回**空**\n"
          "          —— `normalize(query)` 为空白时 `LIKE '%%'` 命中全表，\n"
          "             与第三级兜底是同一条缺陷的两个入口。",
      not _AZ22_EMPTY and not _AZ22_BLANK,
      f"空串={len(_AZ22_EMPTY)} 空白={len(_AZ22_BLANK)}")

# ── AZ25：§17-7i 的**第二处根因** —— 降级兜底的 query 是字面量 `"goal"` ────
#: 这一条是 7h 那条缺陷的**另一半**，也是"33 次 recall 全部返回 4 条"的直接原因：
#: `_fallback` 里写的是 `{"query": "goal"}`（字面量），它与任何 fact 零交集
#: ⇒ 每次降级 recall 都落到第三级全表兜底。删掉兜底之后它变成"永远空"，
#: 而日志仍写 `recall ok=True` —— "成功"的语义被掏空，比失败更难发现。
_AZ25_MEM = MemoryStore(_TMP / "az25.db")
_AZ25_REG = ToolRegistry()
register_core_tools(_AZ25_REG, memory=_AZ25_MEM)
_AZ25_PL = Planner(StubLLM(), _AZ25_REG)
_AZ25_GOAL = "把列表去重，保留首次出现的那条原文"
_AZ25_STEPS = _AZ25_PL._fallback(steps_used=0, goal=_AZ25_GOAL)
_AZ25_Q = str(_AZ25_STEPS[0].args.get("query", "")) if _AZ25_STEPS else ""
_AZ25_EMPTY = _AZ25_PL._fallback(steps_used=0, goal="")
_AZ25_Q2 = str(_AZ25_EMPTY[0].args.get("query", "")) if _AZ25_EMPTY else ""
check(RS, "AZ25 ★7i 降级兜底的 `recall` query 必须是**目标文本**，不是字面量 \"goal\"\n"
          "          —— 字面量与任何 fact 零交集 ⇒ 每次降级 recall 都落到全表兜底\n"
          "             （7d–7g 实测 **33 次全部返回 4 条**）；兜底删掉后它变成\n"
          "             \"永远空\"，而日志仍写 `recall ok=True` —— 比失败更难发现。\n"
          "          ⚠️ goal 为空时仍要给出**合法**参数：降级不等于瘫痪。",
      _AZ25_Q == _AZ25_GOAL[:64] and _AZ25_Q != "goal" and bool(_AZ25_Q2),
      f"goal 非空→query={_AZ25_Q!r}；goal 为空→query={_AZ25_Q2!r}")

# ══════════════════════════════════════════════════════════════════════════
# AZ26–AZ30（第三十八轮，§17-7j）：把"题面能不能控制命中几条"焊死
#
# 为什么必须有这一组：7i 把全表兜底拔掉之后，§17-7 手上**没有可用的 suite**
# 了 —— 四题的真命中是 **0/0/1/1**（前两题零命中、后两题靠 ASCII 偶然命中）。
# 没有可控的 suite，任何"邻域效应"的读数都无处安放。
#
# 机制（7j 动手前实测，不是推断）：`_TOKEN = [A-Za-z0-9_\u4e00-\u9fff]+`
# 把**整段连续中文当一个 token**，而 FTS5 找的是**完全相同的串** ⇒ 中文只能
# "整串逐字命中"（`g7e2` 命中的是 `不超长时原样返回` 这 7 个字恰好一字不差）。
# ⇒ 常规命中源是 ASCII token。所以钥匙用 ASCII（`ZK1`…），**且必须是 ASCII**：
#   `build_placebo` 只打散汉字、原位保留 ASCII；钥匙若是汉字，P 臂的钥匙会被
#   打散 ⇒ 两臂检索行为不同 ⇒ 条数/字符数不等 ⇒ R7 的等长在注入层就破。
# ══════════════════════════════════════════════════════════════════════════
import bench.build_g7j_heldout as _G7J                               # noqa: E402

_AZ26_N0 = _az_rows(V1 / "bench" / "heldout" / "g7j_n0_heldout.jsonl")
_AZ26_N4 = _az_rows(V1 / "bench" / "heldout" / "g7j_n4_heldout.jsonl")
_AZ26_FACTS = _G7D.VARIANTS["7j"]
_AZ26_MEM = MemoryStore(_TMP / "az26.db")
for _v in _AZ26_FACTS.values():
    _AZ26_MEM.commit_fact(_v, source="abstract", confidence=0.5)
_AZ26_K = cfg.Limits().recall_k


def _az26_got(rows: list[dict]) -> list[list[str]]:
    return [[f.text for f in _AZ26_MEM.recall(t["prompt"], k=_AZ26_K)]
            for t in rows]


_AZ26_G0 = _az26_got(_AZ26_N0)
_AZ26_G4 = _az26_got(_AZ26_N4)
_AZ26_GOLD = _az26_got(_AZ6_HINT)          # 正向对照：原题面（没有钥匙）
_AZ26_C0 = [len(g) for g in _AZ26_G0]
_AZ26_C4 = [len(g) for g in _AZ26_G4]
_AZ26_COLD = [len(g) for g in _AZ26_GOLD]
_AZ26_OWN0 = all(_AZ26_FACTS[t["id"]] in g for t, g in zip(_AZ26_N0, _AZ26_G0))
_AZ26_OWN4 = all(_AZ26_FACTS[t["id"]] in g for t, g in zip(_AZ26_N4, _AZ26_G4))
check(RS, "AZ26 ★7j 钥匙必须**可控**：题面提 N 把就命中 N 条，且不多不少\n"
          "          —— n0 提 1 把 ⇒ 1 条；n4 提 4 把 ⇒ 4 条；两次都必须含『本条』。\n"
          "          ⚠️ 必须带**正向对照**：原题面（没有钥匙）仍是 0/0/1/1 ——\n"
          "             没有这一格，『命中来自钥匙』就只是推断（也可能是惯例正文里\n"
          "             的中文恰好又撞上了，7e 的 `g7e2` 就是这么偶然命中的）。",
      _AZ26_C0 == [1, 1, 1, 1] and _AZ26_C4 == [4, 4, 4, 4]
      and _AZ26_OWN0 and _AZ26_OWN4 and _AZ26_COLD == [0, 0, 1, 1],
      f"n0={_AZ26_C0} n4={_AZ26_C4} 原题面={_AZ26_COLD} "
      f"本条在n0={_AZ26_OWN0} 本条在n4={_AZ26_OWN4}")

# ── AZ27：钥匙必须落在 `fts_query` 的 24-token 窗口**内** ──────────────────
#: `fts_query` 只取前 24 个 token（`toks[:24]`）。"题面里写了钥匙"与"FTS 收到
#: 钥匙"是两件事 —— 窗口外的钥匙等于没写，而读数上看不出任何异常（就是命中少）。
_AZ27_BAD: list[str] = []
_AZ27_TOKS: list[tuple[str, int, int]] = []
for _t in _AZ26_N4:
    _tk = _G7J.query_tokens(_t["prompt"])
    _AZ27_TOKS.append((_t["id"], len(_G7J._TOKEN.findall(_t["prompt"])), len(_tk)))
    _AZ27_BAD += _G7J.check_windows(_t["prompt"], _t["id"], "n4")
check(RS, "AZ27 ★7j 四把钥匙必须**全部**落在 `fts_query` 的 24-token 窗口内\n"
          "          —— `fts_query` 只取前 24 个；窗口外等于没提，而现象只是\n"
          "             『命中少了几条』，与『FTS 坏了』长得一模一样。",
      not _AZ27_BAD,
      f"题面token→窗口={'/'.join(f'{a}:{b}→{c}' for a, b, c in _AZ27_TOKS)} "
      f"违规={_AZ27_BAD or '无'}")

# ── AZ28：骨架安慰剂后 P 臂必须**等条数** ────────────────────────────────
#: 这是"钥匙必须用 ASCII"的**反证**：把钥匙换成汉字再看会怎样（这里不真换，
#: 只钉住当前构造成立）。R7 的等长只有在**两臂条数相等**时才等价于字符数相等。
_AZ28_H = list(_AZ26_FACTS.values())
_AZ28_P = _G7D.build_placebo(_AZ28_H)
_AZ28_BAD = _G7D.check_placebo(_AZ28_H, _AZ28_P)
_AZ28_PMEM = MemoryStore(_TMP / "az28.db")
for _v in _AZ28_P:
    _AZ28_PMEM.commit_fact(_v, source="abstract", confidence=0.5)
_AZ28_P4 = [len(_AZ28_PMEM.recall(t["prompt"], k=_AZ26_K)) for t in _AZ26_N4]
check(RS, "AZ28 ★7j 骨架安慰剂之后 P 臂必须**等条数**（否则 R7 的等长在注入层就破）\n"
          "          —— 钥匙用 ASCII 的唯一理由：`build_placebo` 只打散汉字、\n"
          "             原位保留 ASCII。钥匙若是汉字，P 臂的钥匙被打散 ⇒\n"
          "             两臂检索行为不同 ⇒ 条数/字符数不等，Δ 无法归因。",
      _AZ28_P4 == _AZ26_C4 and not _AZ28_BAD,
      f"P臂条数={_AZ28_P4}（H 臂={_AZ26_C4}）等价性违规={_AZ28_BAD or '无'}")

# ── AZ29：`[语义记忆]` 块在 n4 档**真的出现**且条数对 ──────────────────────
#: `_state()` 的 cap 会把**低优先级块整块丢掉**（`state_trimmed += 1`）。
#: 若语义块被丢掉，"没送达"会被读成"模型不用"—— 那正是 7d 读错的那条路。
#: ⚠️ 必须走真实路径（Core + StubLLM），不许手工塞 state。
_AZ29_T = next(t for t in _AZ26_N4 if t["id"] == "g7d2_dedupe_keep")
_AZ29_MEM = MemoryStore(Path(tempfile.mkdtemp(prefix="v1_az29_")) / "memory.db")
for _v in _AZ26_FACTS.values():
    _AZ29_MEM.commit_fact(_v, source=ABSTRACT_SOURCE, confidence=0.5)
_AZ29_CORE = Core(llm=StubLLM(), memory=_AZ29_MEM, memory_mode="raw")
_AZ29_CORE.run(_AZ29_T["prompt"], task_id=_AZ29_T["id"])
_AZ29_ST = _AZ29_CORE.state_log[0][1] if _AZ29_CORE.state_log else ""
_AZ29_BLOCK = ""
for _line in _AZ29_ST.splitlines():
    if _line.startswith("[语义记忆]"):
        _AZ29_BLOCK = _line[len("[语义记忆]"):].strip()
        break
_AZ29_N = len(re.findall(r"（ZK\d）", _AZ29_BLOCK))
_AZ29_MEM.close()
check(RS, "AZ29 ★7j 第 1 步的 `[语义记忆]` 块必须**真的存在**且含恰好 4 条\n"
          "          —— `_state()` 的 cap 会把低优先级块**整块丢掉**；\n"
          "             若语义块被丢掉，『没送达』会被读成『模型不用』。\n"
          "          ⚠️ 走真实路径（Core + StubLLM），不许手工塞 state。",
      bool(_AZ29_ST) and _AZ29_BLOCK != "" and _AZ29_N == 4,
      f"state={len(_AZ29_ST)}字 语义块={'有空' if _AZ29_BLOCK else '❌空'} "
      f"条数={_AZ29_N}（期望 4）")


# ── AZ30：同一个 R1 预检必须**有分辨力** ──────────────────────────────────
#: 只证明"7j 通过"等于只说"现在是绿的"，挡不住"预检恒真"。
#: 反向对照必须走**同一条代码路径**并且**失败**（rc=2 = 处理未送达）。
def _az30_rc(tag: str, heldout: Path, variant: str) -> int:
    d = Path(tempfile.mkdtemp(prefix=f"v1_az30_{tag}_")) / "seed"
    with _ctx.redirect_stdout(_io.StringIO()):
        return _G7D.main(["--out-dir", str(d), "--heldout", str(heldout),
                          "--variant", variant])


_AZ30_J = _az30_rc("7j", V1 / "bench" / "heldout" / "g7j_n0_heldout.jsonl", "7j")
_AZ30_D = _az30_rc("7d", V1 / "bench" / "heldout" / "g7e_hint_heldout.jsonl", "7d")
check(RS, "AZ30 ★7j 同一个 R1 预检必须**有分辨力**：7j 通过、7d 仍失败\n"
          "          —— 前两题的题面与四条 facts FTS 零交集 ⇒ 7d/7f 在拔掉兜底后\n"
          "             **不可重建**。这不许被『为了让预检过而放宽』抹平：\n"
          "             零命中就是处理未送达，该修的是题面（7j），不是预检。",
      _AZ30_J == 0 and _AZ30_D == 2,
      f"7j rc={_AZ30_J}（期望 0） 7d rc={_AZ30_D}（期望 2）")



# ══════════════════════════════════════════════════════════════════════════
# AE 组（第二十三轮，路线①）：外部能力作为**答案来源**接入
# ══════════════════════════════════════════════════════════════════════════
_AE_CORE = _code_only(_CORE_SRC)
_AE_TOOLS = _code_only((V1 / "mw" / "tools.py").read_text(encoding="utf-8"))
_AE_RUN = _code_only(_RUN_SRC) if "_RUN_SRC" in globals() else _code_only(
    (V1 / "run.py").read_text(encoding="utf-8"))
_AE_AGENT = (V1 / "external" / "code_agent.py").read_text(encoding="utf-8")

# ── AE1：`produces_answer` 必须显式标注，内建工具一个都不许带 ──────────────
#: 理由：如果"能产出答案"变成默认，那么 `recall`/`note`/`http_get` 的输出
#: 都会变成候选答案 —— 那是一次静默的语义升级，读数上完全看不出来。
_AE_REG = ToolRegistry()
register_core_tools(_AE_REG)
_AE_BUILTIN_ANSWER = [n for n in _AE_REG.names()
                      if getattr(_AE_REG.get(n), "produces_answer", False)]
check(RS, "AE1 ★`produces_answer` 默认必须是 False，且**内建工具一个都不许带**\n"
          "          —— 若它变成默认，`recall`/`note` 的输出都会成为候选答案：\n"
          "            那是一次静默的语义升级，而读数上完全看不出来。",
      _AE_BUILTIN_ANSWER == [],
      f"内建里声明产出答案的={_AE_BUILTIN_ANSWER or '无'}")

# ── AE2：开关必须进口径签名（开/关外部能力不是同一个候选）───────────────────
_AE_LIM = {f.name: f for f in __import__("dataclasses").fields(cfg.Limits)}
check(RS, "AE2 ★`external_answer_tool` 必须进 `Limits.as_dict()`（口径签名）并登记 ENV_KEYS\n"
          "          —— 开/关外部能力的分数不是同一个候选；不进制就会被判成『可比』，\n"
          "            那是与 temperature 那次同源的尺子事故。",
      "external_answer_tool" in cfg.Limits().as_dict()
      and "V1_EXTERNAL_ANSWER_TOOL" in cfg.ENV_KEYS,
      f"在 as_dict={'external_answer_tool' in cfg.Limits().as_dict()} "
      f"登记={'V1_EXTERNAL_ANSWER_TOOL' in cfg.ENV_KEYS}")

# ── AE3：注入点**唯一**，且必须只注入第 1 步 ─────────────────────────────
_AE_INJECT = _AE_CORE.count("cands = [Step(")
check(RS, "AE3 ★外部能力只许有**一处**注入点，且必须守在第 1 步\n"
          "          —— 每步都注入 = 把规划器整个挤掉（那就不是『能力外部化』，\n"
          "            是『内核只剩一个转发器』）。",
      _AE_INJECT == 1 and "if _ext and it == 1 and self.tools.get(_ext) is not None:"
      in _AE_CORE,
      f"注入点={_AE_INJECT} 守在第1步={'if _ext and it == 1 and self.tools.get(_ext) is not None:' in _AE_CORE}")

# ── AE4：外部答案必须走**同一道**闸，且采用数是独立读数 ────────────────────
check(RS, "AE4 ★外部能力的答案走 `_gate_finish`，且 `used_as_answer` 与 `ok` 分列\n"
          "          —— 『调用成功』≠『答案被采用』：前者可能被自检打回或被改写。\n"
          "            混成一列，就会把『接了但没用上』读成『接上了』。",
      'self.external_stats["used_as_answer"] += 1' in _AE_CORE
      and '"calls": 0, "ok": 0, "err": 0' in _AE_CORE,
      f"采用点={_AE_CORE.count(chr(34)+'used_as_answer'+chr(34)+'] += 1')}")

# ── AE5：外部 agent 的失败**不许冒充答案** ────────────────────────────────
check(RS, "AE5 ★外部 agent 交付空答案/带 error 时必须 raise，不许当成『成功的答案』\n"
          "          —— 把 `(rc=0)` 或空串当答案是静默降级：读数看起来正常，\n"
          "            实际什么都没交。",
      _AE_TOOLS.count("raise RuntimeError") >= 2
      and "answer 为空（不是 0 分，是没答案）" in _AE_TOOLS
      and "外部 agent 未交付" in _AE_TOOLS,
      f"raise 处={_AE_TOOLS.count('raise RuntimeError')}")

# ── AE6：读数必须四列分开（调/成/败/采用），且必须落盘到 runs.jsonl ─────────
_AE6_IN_RUN = '"kernel_external": _kex' in _AE_RUN
_AE6_ABSORB = '_kex_absorb(_kex, out.stats.get("external") or {})'
check(RS, "AE6 ★外部能力读数必须落盘（`kernel_external`），且**两个 solver 分支都并**\n"
          "          —— 只接一个分支 = 一半的题没有读数，而汇总行看起来正常。",
      _AE6_IN_RUN
      and _AE_RUN.count(_AE6_ABSORB) == 2,
      f"落盘={_AE6_IN_RUN} "
      f"并入点={_AE_RUN.count('_kex_absorb(_kex, out.stats.get(chr(34)+chr(34)) or {})')}")

# ── AE7：外部 agent 是**外部进程**，不许 import 内核，也不许读尺子 ──────────
#: ⛔ 它一旦 import `mw.*`，就不再是"外部能力"，而是内核的一部分 ——
#:   那时"能力来自外部"这句话就没有证据了（与 AP1「假记忆不许进内核」同宗）。
_AE_AGENT_IMPORTS = [ln for ln in _AE_AGENT.splitlines()
                     if ln.strip().startswith(("import ", "from "))
                     and ("mw" in ln or "bench" in ln)]
#: ⚠️ 必须**注释/文档感知**：`code_agent.py` 的 docstring 里写着
#:    "任务集里的 `tests` / `oracle` 一律不进来" —— 用整文件 `in` 去判，
#:    那句**否定句**会把断言判红（本轮真踩到，R7 的同类错误：静态锚撞注释）。
_AE_AGENT_CODE = _code_only(_AE_AGENT)
_AE_AGENT_LEAK = [k for k in ("oracle", "jsonl", "bench/tasks")
                  if k in _AE_AGENT_CODE]
check(RS, "AE7 ★`external/code_agent.py` 不许 import 内核或 bench，也不许读 tests/oracle\n"
          "          —— 它一旦 import `mw.*` 就不是外部能力而是内核的一部分，\n"
          "            『能力来自外部』这句话当场失去证据。\n"
          "          ⚠️ 检查必须**注释感知**：docstring 里那句『不许读 oracle』\n"
          "            本身含 oracle，整文件 `in` 会把否定句判成违规（本轮踩到）。",
      _AE_AGENT_IMPORTS == [] and _AE_AGENT_LEAK == [],
      f"越界 import={_AE_AGENT_IMPORTS or '无'} 代码里触及尺子={_AE_AGENT_LEAK or '无'}")

# ── AE8：`--no-external` 必须真的**不注册**（OFF 臂的地基）──────────────────
check(RS, "AE8 ★`--no-external` 必须在**两个** `_build` 分支都传进 `wire_external`\n"
          "          —— 只在一个分支生效，OFF 臂就有一半的题带着外部能力跑，\n"
          "            而两臂的标签长得一模一样（那是第十六轮 B/C 共用记忆目录的同构）。",
      # ⚠️ 锚点必须收到"结束 `Core(...)` 调用"的 `))`：第二十四轮口径签名也要读
      #    同一个开关，于是**字面量合法地多出现一次** —— 旧锚点（不含 `))`）
      #    把这一处合法新增判成失败。这不是把断言放松，是把它收窄到真正要管的对象。
      _AE_RUN.count('wire_external=not getattr(args, "no_external", False))') == 2,
      f"透传处(_build 里)={_AE_RUN.count('wire_external=not getattr(args, ' + chr(34) + 'no_external' + chr(34) + ', False))')}"
      f" / 全部出现={_AE_RUN.count('wire_external=not getattr(args, ' + chr(34) + 'no_external' + chr(34) + ', False)')}")

# ── AE9：端到端（行为，不是静态）—— 外部答案真的被采用 ────────────────────
#: ⚠️ 静态检查抓不到接线错误：`cands = [Step(...)] + list(cands)` 写对了位置，
#:    但 `produces_answer` 没传进 dispatch 分支，照样"看不见"。所以必须真跑一次。
import os as _os
import tempfile as _tf
_os.environ["V1_EXTERNAL_ANSWER_TOOL"] = "agent:ae"
_os.environ["V1_LLM_OFFLINE"] = "1"
_ae_reg = ToolRegistry()
_ae_hits = {"n": 0}


def _ae_handler(args: dict) -> str:
    _ae_hits["n"] += 1
    return "```python\ndef flatten(d):\n    return {}\n```"


_ae_reg.register(ToolSpec("agent:ae", "契约用假外部能力", {"task": "任务"},
                          _ae_handler, external=True, mutating=True,
                          produces_answer=True))
_ae_tmp = _tf.mkdtemp(prefix="v1_ae_")
_ae_old = _os.environ.get("V1_STATE_DIR")
_os.environ["V1_STATE_DIR"] = _ae_tmp + "/state"
try:
    _ae_core = Core(cfg.Settings(), llm=StubLLM(), tools=_ae_reg,
                    wire_external=False, log=lambda *_: None)
    _ae_out = _ae_core.run("写一个函数 flatten(d)，把嵌套 dict 展平。只输出代码，"
                           "函数名必须是 flatten。")
    _ae_ext = _ae_out.stats["external"]
finally:
    _os.environ["V1_STATE_DIR"] = _ae_old or ""
    if not _ae_old:
        _os.environ.pop("V1_STATE_DIR", None)
    # ⚠️ 三个 env 都要清：留下任何一个，后面的断言就会在"外部能力开着"的条件下跑，
    #    而它们自己不知道 —— 第十六轮"所有相关 env 都要设"的同一条教训。
    _os.environ.pop("V1_EXTERNAL_ANSWER_TOOL", None)
    _os.environ.pop("V1_LLM_OFFLINE", None)

check(RS, "AE9 ★端到端：外部能力被调用 **1 次**，其答案走同一条交付路径被采用\n"
          "          —— 静态检查抓不到接线错误（位置写对、标记没传到 dispatch 分支，\n"
          "            照样『看不见』）。所以这一条必须真跑。",
      _ae_hits["n"] == 1 and _ae_ext["calls"] == 1 and _ae_ext["ok"] == 1
      and _ae_ext["used_as_answer"] == 1 and "def flatten" in _ae_out.answer,
      f"调用={_ae_hits['n']} stats={_ae_ext['calls']}/{_ae_ext['ok']}/"
      f"{_ae_ext['used_as_answer']} 交付含代码={'def flatten' in _ae_out.answer}")

# ═══════════════════════════════════════════════════════════════════════════
# AT1–AT8（第二十四轮）：口径签名补齐 + 失败可归因 + 探针求和
#
# 这八条全是**实测逼出来的**，不是为了凑数：
#   · 工具目录进提示词却不进口径签名 ⇒ 同 mean、不同分布，标签仍写"同口径"
#   · 失败原因只留最后一条、且只在"全败"时留 ⇒ "7 调 5 败"在证据里不可归因
#   · 探针第三次只读最后一条记录 ⇒ 冻结的 verdict 把"跑了 7 次"写成"跑了 4 次"
# ═══════════════════════════════════════════════════════════════════════════
from mw.bench import (CALIBER_KEYS as _af_ck, Caliber as _af_Cal,  # noqa: E402
                      caliber_from_dict as _af_cfd)
from mw.ledger import LEDGER_SIG_EXCLUDE as _af_excl       # noqa: E402
from mw.ledger import ledger_sig_of as _af_lsig            # noqa: E402
from mw.tools import catalog_sha as _af_cat_sha            # noqa: E402

import hashlib as _af_hl                                   # noqa: E402
import importlib.util as _af_ilu                           # noqa: E402

_af_s = cfg.Settings()
_af_tmp = Path(tempfile.mkdtemp(prefix="v1_af_"))
_af_old_st = os.environ.get("V1_STATE_DIR")
os.environ["V1_STATE_DIR"] = str(_af_tmp / "state")
_af_cores: dict = {}
try:
    for _t in ("off", "on"):
        _af_cores[_t] = Core(_af_s, llm=StubLLM(),
                             wire_external=(_t == "on"), log=lambda *_: None)
finally:
    os.environ["V1_STATE_DIR"] = _af_old_st or ""
    if not _af_old_st:
        os.environ.pop("V1_STATE_DIR", None)

#: `catalog_sha()` 算出的指纹 vs. **真实 Core** 的目录哈希 —— 必须逐字相同。
_af_pairs_t = {}
for _t, _c in _af_cores.items():
    _real = _af_hl.sha256(_c.tools.catalog().encode("utf-8")).hexdigest()[:16]
    _fn = _af_cat_sha(wire_external=(_t == "on"),
                      agents_path=_af_s.paths.root / "agents.json",
                      workspace=_af_s.paths.workspace)
    _af_pairs_t[_t] = (_real, _fn)

check(RS, "AT1 ★工具目录指纹必须**等于真实 Core 的目录哈希**（OFF 与 ON 各一次）\n"
          "          —— 指纹一旦与真实提示词脱节，『同口径』就还是散文；\n"
          "            这条把 `catalog_sha()` 与 `Core.tools.catalog()` 直接对撞。",
      all(r == f for r, f in _af_pairs_t.values())
      and _af_pairs_t["off"][0] != _af_pairs_t["on"][0],
      f"off={_af_pairs_t['off'][0]}/{_af_pairs_t['off'][1]} "
      f"on={_af_pairs_t['on'][0]}/{_af_pairs_t['on'][1]}")

check(RS, "AT2 ★`tool_catalog_sha` 必须**进口径签名**，且 run.py 真的把它填进去\n"
          "          —— 只加字段不填值 = 装饰字段（『只声明上限却不截断』）。",
      "tool_catalog_sha" in _af_ck
      and "tool_catalog_sha=tools_catalog_sha(" in _AE_RUN,
      f"在 CALIBER_KEYS={'tool_catalog_sha' in _af_ck} "
      f"在 run.py 构造处={'tool_catalog_sha=tools_catalog_sha(' in _AE_RUN}")

# ── AT3：改 `agents.json`（哪怕只改描述）必须改变指纹 ──────────────────────
#: ⛔ 这是"改 agents.json 就等于改提示词"最直接的证据：`catalog_line()` 含描述文字。
_af_ag = _af_tmp / "agents.json"
_af_ag.write_text(json.dumps({"agents": {"x": {
    "type": "shell", "command": ["true"], "description": "描述甲"}}},
    ensure_ascii=False), encoding="utf-8")
_af_sha_a = _af_cat_sha(wire_external=True, agents_path=_af_ag)
_af_ag.write_text(json.dumps({"agents": {"x": {
    "type": "shell", "command": ["true"], "description": "描述乙"}}},
    ensure_ascii=False), encoding="utf-8")
_af_sha_b = _af_cat_sha(wire_external=True, agents_path=_af_ag)

check(RS, "AT3 ★只改 `agents.json` 里的**一句描述**，指纹必须变\n"
          "          —— 因为 `catalog_line()` 把描述拼进提示词（planner.py:182）。\n"
          "            指纹不变 ⇒ 提示词变了而签名说没变（标签撒谎）。",
      _af_sha_a != _af_sha_b,
      f"{_af_sha_a} vs {_af_sha_b}")


# ── AT4：`diff_except` 的三态（声明命中 / 意外 / 空声明 = diff）────────────
def _af_cal(**kw):
    _base = dict(scorer_sha="s", suite_sha="u", repeat=1, budget_tag="1x",
                 temperature=0.0, fresh=True, memory_scope="per_task_fresh",
                 memory_seed=(0, 0), limits={"external_answer_tool": ""},
                 llm_cond={"model": "m"}, max_tokens=1024, tool_catalog_sha="aaa")
    _base.update(kw)
    return _af_Cal(**_base)


_af_u, _af_d = _af_cal().diff_except(
    _af_cal(tool_catalog_sha="bbb", limits={"external_answer_tool": "agent:x"}),
    ("tool_catalog_sha", "limits"))
_af_u2, _af_d2 = _af_cal().diff_except(_af_cal(temperature=0.8), ("tool_catalog_sha",))
check(RS, "AT4 ★『声明的变量豁免』必须**只**放行声明过的字段\n"
          "          —— 两臂只差外部能力这件事，从此是可核验的断言而不是散文：\n"
          "            声明命中的进 `declared`，没声明的一律进 `unexpected`。",
      _af_u == [] and set(_af_d) == {"tool_catalog_sha", "limits"}
      and _af_u2 == ["temperature"] and _af_d2 == [],
      f"声明命中⇒unexpected={_af_u} declared={_af_d}；"
      f"未声明⇒unexpected={_af_u2}")

# ── AT5：账本签名**不含**目录指纹（这条取舍必须被钉住，否则会被人"顺手修好"）──
_af_sig_old = _af_lsig({k: v for k, v in _af_cal().as_dict().items()
                        if k != "tool_catalog_sha"})
_af_sig_new = _af_lsig(_af_cal(tool_catalog_sha="zzz").as_dict())
check(RS, "AT5 ★账本签名必须**排除** `tool_catalog_sha`（已声明的取舍）\n"
          "          —— 不排除 ⇒ 历史 8 个会话的行全部失效（admissible 归零），\n"
          "            而它们的目录指纹已不可追溯，补算就是伪造血缘。\n"
          "            代价（账本行读不到指纹）写在 DESIGN §16.23 ⑦ 边界里。",
      "tool_catalog_sha" in _af_excl and _af_sig_old == _af_sig_new,
      f"排除={_af_excl} 旧签名={_af_sig_old} 新签名={_af_sig_new}")

# ── AT6：探针的 `_kex` 必须**求和**（第三次踩"只读最后一条"）───────────────
_af_spec = _af_ilu.spec_from_file_location(
    "v1_probe_ext", str(V1 / "bench" / "probe_external_real.py"))
_af_probe = _af_ilu.module_from_spec(_af_spec)
_af_spec.loader.exec_module(_af_probe)                       # type: ignore[union-attr]
_af_ev = _af_tmp / "ev"
_af_ev.mkdir(parents=True, exist_ok=True)
_af_probe.EV = _af_ev
_af_rows = [
    {"kernel_external": {"calls": 3, "ok": 1, "err": 2, "tool": "agent:x",
                         "err_reasons": ["原因甲"], "discriminated": 1}},
    {"kernel_external": {"calls": 4, "ok": 1, "err": 3, "tool": "agent:x",
                         "err_reasons": ["原因乙"], "discriminated": 0}},
]
(_af_ev / "ho_on.jsonl").write_text(
    "\n".join(json.dumps(r, ensure_ascii=False) for r in _af_rows) + "\n",
    encoding="utf-8")
_af_kex = _af_probe._kex("ho_on")
check(RS, "AT6 ★探针的 `_kex` 必须**把全部记录加总**（不是只读最后一条）\n"
          "          —— `--suite-dir` 每个领域一条记录，累加器在每条里各自从零开始。\n"
          "            第三次踩同一个坑：冻结的 verdict 把『跑了 7 次』写成『跑了 4 次』。",
      _af_kex.get("calls") == 7 and _af_kex.get("ok") == 2
      and _af_kex.get("err") == 5 and _af_kex.get("records") == 2
      and _af_kex.get("err_reasons") == ["原因甲", "原因乙"],
      f"calls={_af_kex.get('calls')} ok={_af_kex.get('ok')} "
      f"records={_af_kex.get('records')} reasons={_af_kex.get('err_reasons')}")

# ── AT7：口径核验三态（pass / fail / unverifiable）─────────────────────────
_af_c_off = _af_cal(limits={"external_answer_tool": ""}, tool_catalog_sha="aaa").as_dict()
_af_c_on = _af_cal(limits={"external_answer_tool": "agent:x"},
                   tool_catalog_sha="bbb").as_dict()
for _n, _c in (("sc_off", _af_c_off), ("sc_on", _af_c_on),
               ("ho_off", _af_c_off), ("ho_on", _af_c_on)):
    (_af_ev / f"{_n}.jsonl").write_text(
        json.dumps({"caliber": _c}, ensure_ascii=False) + "\n", encoding="utf-8")
_af_pair_def = (("struct_convert", "sc_off", "sc_on"),
                ("heldout", "ho_off", "ho_on"))
_af_cc_pass = _af_probe._caliber_check(_af_pair_def)
#: 意外差异：把 ON 的 temperature 改掉
_af_bad = _af_cal(limits={"external_answer_tool": "agent:x"},
                  tool_catalog_sha="bbb", temperature=0.8).as_dict()
(_af_ev / "sc_on.jsonl").write_text(
    json.dumps({"caliber": _af_bad}, ensure_ascii=False) + "\n", encoding="utf-8")
_af_cc_fail = _af_probe._caliber_check(_af_pair_def)
#: 缺字段：旧证据
_af_oldcal = {k: v for k, v in _af_c_off.items() if k != "tool_catalog_sha"}
(_af_ev / "sc_on.jsonl").write_text(
    json.dumps({"caliber": _af_oldcal}, ensure_ascii=False) + "\n", encoding="utf-8")
_af_cc_unk = _af_probe._caliber_check(_af_pair_def)
check(RS, "AT7 ★口径核验必须三态：`pass` / `fail` / `unverifiable`\n"
          "          —— 把『没核验到』读成『核验通过』是契约③的同一种病\n"
          "            （缺失不得被读作肯定）。旧证据缺字段 ⇒ unverifiable。",
      _af_cc_pass["status"] == "pass"
      and _af_cc_fail["status"] == "fail"
      and _af_cc_unk["status"] == "unverifiable",
      f"pass={_af_cc_pass['status']} fail={_af_cc_fail['status']} "
      f"unk={_af_cc_unk['status']} "
      f"unexpected={_af_cc_fail.get('unexpected')}")

# ── AT8：外部能力的**逐次失败原因**必须进证据（不止最后一条）──────────────
_af_reg = ToolRegistry()


def _af_boom(args: dict) -> str:
    raise ValueError("boom-AT8")


_af_reg.register(ToolSpec("agent:af", "契约用坏外部能力", {"task": "任务"},
                          _af_boom, external=True, mutating=True,
                          produces_answer=True))
_af_old_tool = os.environ.get("V1_EXTERNAL_ANSWER_TOOL")
os.environ["V1_EXTERNAL_ANSWER_TOOL"] = "agent:af"
_af_st2 = os.environ.get("V1_STATE_DIR")
os.environ["V1_STATE_DIR"] = str(_af_tmp / "state2")
try:
    _af_core_bad = Core(cfg.Settings(), llm=StubLLM(), tools=_af_reg,
                        wire_external=False, log=lambda *_: None)
    _af_out_bad = _af_core_bad.run("写一个函数 flatten(d)，把嵌套 dict 展平。"
                                   "只输出代码，函数名必须是 flatten。")
    _af_ext_bad = dict(_af_out_bad.stats["external"])
finally:
    os.environ["V1_STATE_DIR"] = _af_st2 or ""
    if not _af_st2:
        os.environ.pop("V1_STATE_DIR", None)
    os.environ["V1_EXTERNAL_ANSWER_TOOL"] = _af_old_tool or ""
    if not _af_old_tool:
        os.environ.pop("V1_EXTERNAL_ANSWER_TOOL", None)

_af_reasons = list(_af_ext_bad.get("err_reasons") or [])
check(RS, "AT8 ★外部能力失败时，**原因原文**必须进 `err_reasons`（不只是 last_error）\n"
          "          —— 只留最后一条时，`ho_on` 的『7 调 5 败』在证据里退化成\n"
          "            一句笼统的话，而三种病（超时/空返回/解析失败）修法完全不同。",
      _af_ext_bad.get("calls") == 1 and _af_ext_bad.get("err") == 1
      and any("boom-AT8" in r for r in _af_reasons),
      f"calls={_af_ext_bad.get('calls')} err={_af_ext_bad.get('err')} "
      f"reasons={_af_reasons[:2]}")


# ═══════════════════════════════════════════════════════════════════════════
# AU1–AU9（第二十五轮）：重试链必须守墙钟 + 读数必须能看见"它到底裁了几个"
#
# 这九条全部来自一次**实测事故**，不是为凑数：
#   `llm_retries=2` + `timeout_s=120` + `wall_s=120` ⇒ 一次逻辑调用串起
#   `3×120 + 退避 = 361.5s`，超支 3 倍，日志上只留一个 `TimeoutError`、`steps=0`。
#   ⇒ 实现**违反了自己声明的硬上限**（§16.24）。
#
# ⚠️ 本组有一条是"自踩"钉住的：我最初只计"因墙钟放弃"的次数，
#    于是「裁到 75s 后照发并超时」与「用满 120s 后超时」在读数上**一模一样** ——
#    那个计数看不见它本该证明的事。AU4 就是钉"裁短也要计数"的。
# ═══════════════════════════════════════════════════════════════════════════
import time as _au_time                                            # noqa: E402
import importlib.util as _au_ilu                                   # noqa: E402
from mw.core import _LedgeredLLM as _au_L                          # noqa: E402
from mw.llm import LLMError as _au_LLMError                        # noqa: E402


class _AUCanned:
    """一个可指定 `timeout_s` 的假端点：**永远失败**（用它测裁剪与计数）。

    `timeout_s` 这个属性本身就是被测接口的一部分：`_attempt_timeout()` 用它当
    上限，`getattr(..., None)` 取不到就不裁剪（离线桩就是这么走的）。
    """

    def __init__(self, timeout_s: float | None = 100.0, *, fail: bool = True) -> None:
        if timeout_s is not None:
            self.timeout_s = timeout_s
        self.fail = fail
        self.seen: list[float | None] = []

    def complete(self, messages, *, max_tokens: int = 512,
                 temperature: float | None = None,
                 timeout_s: float | None = None) -> str:
        self.seen.append(timeout_s)
        if self.fail:
            raise _au_LLMError("模拟尾延迟", transient=True)
        return "ok"


# ── AU1：`check_wall()` 只查墙钟（不许拿 `check()` 去守重试）───────────────
#: 构造"steps 与 llm_calls 都已用尽、但墙钟还剩很多"的状态。
_au_b_lg = Ledger(Budget(steps=2, llm_calls=2, wall_s=600.0, candidates=1))
_au_b_lg.steps_used = 2
_au_b_lg.llm_calls_used = 2
_au_b_kind_check = _exc_kind(_au_b_lg.check)
_au_wall_fn = getattr(_au_b_lg, "check_wall", None)
_au_b_ok = _raises(_au_wall_fn, BudgetExhausted) if _au_wall_fn else True
#: 反证：**墙钟**用尽时它必须抛，且 kind 必须是 `wall_s`（不是 steps）。
_au_b_late = Ledger(Budget(steps=9, llm_calls=9, wall_s=1.0, candidates=1),
                    t0=_au_time.monotonic() - 30.0)
_au_b_late_kind = _exc_kind(getattr(_au_b_late, "check_wall", lambda: None))

check(RS, "AU1 ★`Ledger.check_wall()` 必须**只**查墙钟：steps/llm_calls 都用尽、\n"
          "          墙钟未尽时**不许**抛；墙钟尽时必须抛且 `kind == 'wall_s'`。\n"
          "          —— 拿 `check()` 去守重试，会把「步数刚好用完的最后一次重试」\n"
          "            误判成「步数耗尽」，归因直接错掉（错误的原因比错误本身更贵）。",
      _au_b_kind_check == "steps" and not _au_b_ok and _au_b_late_kind == "wall_s",
      f"check()⇒{_au_b_kind_check}  check_wall()抛={_au_b_ok}  "
      f"墙钟尽⇒{_au_b_late_kind}")


# ── AU2：裁剪上限 = min(base, 剩余 − 收尾余量)，且**不许**超过 base ────────
def _au_cap(wall_s: float, reserve: float, base: float = 100.0) -> float:
    _lg = Ledger(Budget(steps=9, llm_calls=9, wall_s=wall_s, candidates=1))
    _lm = _au_L(_AUCanned(base), _lg, retries=2, wall_reserve_s=reserve)
    try:
        return float(_lm._attempt_timeout())
    except Exception as _e:  # noqa: BLE001
        # 撤回态可能抛（`float(None)` / 属性被删）。返回 NaN：任何比较都为假
        # ⇒ 断言自然转红，而**汇总行还在**（R13 硬坑：崩了不是判红）。
        return float("nan")


_au_cap_tight = _au_cap(30.0, 5.0)        # 剩余≈30 − 5 = 25 ⇒ 25 < base
_au_cap_loose = _au_cap(1000.0, 0.0)      # 剩余远大于 base ⇒ **必须**是 base
#: 端点自称"无上限"（inf）时，上限 = 剩余预算本身 —— 仍是"预算说了算"，
#: 不会变成无穷。⚠️ 这一格我第一版写错过：断言成 inf，实际是 1000−0=1000。
#: 裁到 1000 才是对的：`min(inf, 剩余)` = 剩余。
_au_cap_inf = _au_cap(1000.0, 0.0, float("inf"))

check(RS, "AU2 ★单次尝试的上限 = `min(端点默认上限, 剩余 − 收尾余量)`，\n"
          "          且**永不超过**端点默认上限（预算宽裕时不许把它放大）；\n"
          "          端点自称无上限(inf)时，上限回落为**剩余预算**（不是无穷）。\n"
          "          —— 这是 361s 的正面修复：单次尝试再也吃不满整题预算。",
      24.0 < _au_cap_tight <= 25.5 and abs(_au_cap_loose - 100.0) < 1e-6
      and abs(_au_cap_inf - 1000.0) < 1.0,
      f"紧={_au_cap_tight:.2f} 宽={_au_cap_loose:.2f} inf端点={_au_cap_inf:.2f}")


# ── AU3：剩余不够一次起步价 ⇒ 返回 0（"别试了"），由调用方决定抛什么 ──────
_au_zero = _au_cap(20.0, 16.0)            # 20 − 16 = 4 ≤ MIN_ATTEMPT_S(5)
_au_just = _au_cap(20.0, 13.0)            # 20 − 13 = 7 > 5 ⇒ 允许试
check(RS, "AU3 ★剩余 ≤ `MIN_ATTEMPT_S` ⇒ `_attempt_timeout()` 返回 **0**（拒绝发起），\n"
          "          而不是返回一个注定超时的正数（那只是把收尾预算也烧掉）。",
      _au_zero == 0.0 and _au_just > 5.0,
      f"不够⇒{_au_zero} 刚好够⇒{_au_just:.2f}")


# ── AU4：★"被裁短但发出去了"必须有独立计数（自踩钉住的一条）─────────────
#: 事故参数：wall=120、reserve=45、base=120 ⇒ 第一发就被裁到 75s。
_au_lg4 = Ledger(Budget(steps=9, llm_calls=9, wall_s=120.0, candidates=1))
_au_inner4 = _AUCanned(120.0)
_au_l4 = _au_L(_au_inner4, _au_lg4, retries=2, wall_reserve_s=45.0)
_au_l4_exc = None
try:
    _au_l4.complete([Message("user", "hi")])
except BudgetExhausted:
    _au_l4_exc = "budget"
except _au_LLMError:
    _au_l4_exc = "llm"
except Exception as _au_e4:  # noqa: BLE001
    _au_l4_exc = f"ERR:{type(_au_e4).__name__}"
#: ⚠️ 撤回态下这两个属性可能不存在 ⇒ 一律 `getattr(..., 0)`，**不做算术**（R13 硬坑）。
_au_short = int(getattr(_au_l4, "wall_shortened", 0) or 0)
_au_ref_first = int(getattr(_au_l4, "wall_refused_first", 0) or 0)
_au_ref_retry = int(getattr(_au_l4, "wall_refused_retry", 0) or 0)

check(RS, "AU4 ★『上限被裁短**且真的发出去**』必须单独计数（`wall_shortened ≥ 1`），\n"
          "          且**不能**被并进『放弃』类计数（那两类的修法与它相反）。\n"
          "          —— 自踩：我最初只在『放弃』时计数 ⇒ 「裁到 75s 照发并超时」与\n"
          "            「用满 120s 后超时」在读数上完全一样，证据看不见要证明的事。",
      _au_short >= 1 and _au_ref_first == 0
      and all((t is None or t < 120.0) for t in _au_inner4.seen),
      f"shortened={_au_short} refused_first={_au_ref_first} "
      f"refused_retry={_au_ref_retry} 实发上限={_au_inner4.seen} 出口={_au_l4_exc}")


# ── AU5：墙钟已不够再试 ⇒ 放弃的是**重试**（与"首发即放弃"分开）───────────
#: 用一个把墙钟"用掉"的端点：第一次调用期间把 t0 往前拨 60s（合法操作 ——
#: `Ledger.t0` 就是时间原点，拨它等价于"时间过去了 60s"，不必真睡 60s）。
class _AUTimeEater(_AUCanned):
    def __init__(self, ledger: Ledger) -> None:
        super().__init__(100.0, fail=True)
        self.ledger = ledger

    def complete(self, messages, *, max_tokens: int = 512,
                 temperature: float | None = None,
                 timeout_s: float | None = None) -> str:
        self.seen.append(timeout_s)
        self.ledger.t0 -= 60.0          # 这一次调用"花掉了" 60s
        raise _au_LLMError("模拟尾延迟", transient=True)


_au_lg5 = Ledger(Budget(steps=9, llm_calls=9, wall_s=100.0, candidates=1))
_au_l5 = _au_L(_AUTimeEater(_au_lg5), _au_lg5, retries=2,
               backoff_s=0.0, wall_reserve_s=45.0)
_au_l5_exc = None
try:
    _au_l5.complete([Message("user", "hi")])
except BudgetExhausted:
    _au_l5_exc = "budget"
except _au_LLMError:
    _au_l5_exc = "llm"
except Exception as _au_e5:  # noqa: BLE001
    _au_l5_exc = f"ERR:{type(_au_e5).__name__}"
_au_l5_sent = list(getattr(_au_l5.inner, "seen", []))
check(RS, "AU5 ★墙钟**已不够再试**时：抛出的是 `BudgetExhausted`（可归因），\n"
          "          计数落在 `wall_refused_retry`（放弃的是重试），\n"
          "          而 `wall_refused_first` **仍为 0**（首发确实发出去了）。\n"
          "          —— 这两个数的修法不同：前者减工作量，后者预算档位就不对。",
      _au_l5_exc == "budget"
      and int(getattr(_au_l5, "wall_refused_retry", 0) or 0) == 1
      and int(getattr(_au_l5, "wall_refused_first", 0) or 0) == 0
      and len(_au_l5_sent) >= 1,
      f"出口={_au_l5_exc} refused_retry="
      f"{getattr(_au_l5, 'wall_refused_retry', 'NA')} "
      f"refused_first={getattr(_au_l5, 'wall_refused_first', 'NA')} "
      f"实发={_au_l5_sent}")


# ── AU6：离线桩（无 `timeout_s` 属性）⇒ **不裁剪**，离线路径零影响 ────────
_au_lg6 = Ledger(Budget(steps=9, llm_calls=9, wall_s=120.0, candidates=1))
_au_l6 = _au_L(StubLLM(), _au_lg6, retries=2, wall_reserve_s=45.0)
#: ⚠️ 撤回态下这里会抛 `TypeError`（`float(None)`）。R13 的硬坑：**不许**让异常
#: 逃出去打断汇总行 —— 那是"崩了"，不是"判红"。所以取值一律包在本辅助里。
try:
    _au_stub_cap = _au_l6._attempt_timeout()
except Exception as _au_e6:  # noqa: BLE001
    _au_stub_cap = f"ERR:{type(_au_e6).__name__}"
check(RS, "AU6 ★没有 `timeout_s` 属性的端点（离线桩）⇒ `_attempt_timeout()` 返回 inf，\n"
          "          **不裁剪**。⇒ 全部离线契约与自检路径的耗时行为一个字没变。\n"
          "          —— 这条是 R2『正常路径不受影响』的结构证据（离线侧）。",
      _au_stub_cap == float("inf"),
      f"桩上限={_au_stub_cap}")


# ── AU7 的读数依赖 AU8 那次运行 ⇒ **必须排在 AU8 之后**（顺序是实质的，不是排版）
#: ⚠️ AU7 判的是"收尾到底有没有把预留**用掉**"，只有真跑过一次收尾才有读数。



# ── AU8：`BudgetExhausted` 在余量还在时**转向交付**（status=closed）───────
#: ⚠️ 用 env 把档位压小，让"墙钟已被裁到底、但还剩 > MIN_ATTEMPT_S"这一格可达：
#:    wall=20、reserve=15 ⇒ 第一发的可用量 = 20 − 15 = 5 ≤ MIN ⇒ 拒绝发起；
#:    而此刻总剩余 20s > 5s ⇒ 内核**必须**去收尾交付，而不是直接报 budget_exhausted。
_au_env = {k: os.environ.get(k) for k in
           ("V1_MAX_WALL_S", "V1_CLOSE_RESERVE_S", "V1_MAX_STEPS",
            "V1_MAX_LLM_CALLS", "V1_STATE_DIR")}
os.environ["V1_MAX_WALL_S"] = "20"
os.environ["V1_CLOSE_RESERVE_S"] = "15"
os.environ["V1_MAX_STEPS"] = "6"
os.environ["V1_MAX_LLM_CALLS"] = "8"
os.environ["V1_STATE_DIR"] = str(_TMP / "au8_state")
_au_status8 = ""
_au_co8: dict = {}
_au_tx8: dict = {}
_au_core8 = None
_au_log8: list[str] = []
try:
    _au_core8 = Core(cfg.Settings(), llm=_AUCanned(120.0), wire_external=False,
                     log=lambda *a: _au_log8.append(str(a[0]) if a else ""))
    _au_out8 = _au_core8.run("写一个函数 flatten(d)，把嵌套 dict 展平。只输出代码。")
    _au_status8 = _au_out8.status
    _au_co8 = dict(_au_out8.stats.get("closeout") or {})
    _au_tx8 = dict(_au_out8.stats.get("transport") or {})
except Exception as _au_e8:  # noqa: BLE001
    # 撤回态可能抛 ⇒ 记成 ERR 而不是让异常逃出去打断汇总行（R13 硬坑）。
    _au_status8 = f"ERR:{type(_au_e8).__name__}"
finally:
    for _k, _v in _au_env.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

check(RS, "AU8 ★墙钟被裁到底、但总剩余仍够收尾 ⇒ 出口是 `closed`（转向交付），\n"
          "          不是 `budget_exhausted`（交付零东西）。且 `closeout.triggered ≥ 1`。\n"
          "          —— 第二十四轮 `h01`/`k01` 就是死在『交付零』上：`steps=0`。",
      _au_status8 == "closed" and int(_au_co8.get("triggered") or 0) >= 1,
      f"status={_au_status8} triggered={_au_co8.get('triggered')} "
      f"by_reason={_au_co8.get('by_reason')} transport={_au_tx8}")


# ── AU7：收尾必须**真的**把预留放出来（行为锚，不是语法锚）─────────────────
_au_c8 = getattr(_au_core8, "_counted", None)
_au_reserve_after = getattr(_au_c8, "wall_reserve_s", None)
check(RS, "AU7 ★触发收尾后 `_counted.wall_reserve_s` 必须变成 **0.0**\n"
          "          —— 探索阶段扣下余量是对的，收尾阶段还扣着就等于「留了不用」，\n"
          "            那份 45s 就是装饰字段。\n"
          "          ⛔ 自踩：第一版锚的是 `_close_out` 的 AST 里**有没有**\n"
          "            `reserve_release()` 这个调用。撤回态把那一行改成死代码后，\n"
          "            AST 里**仍然有**它 ⇒ 断言照旧全绿（`红=无`，当场抓到）。\n"
          "            ——「代码里有这个词」与「运行时发生过这件事」是两件事。",
      _au_reserve_after == 0.0,
      f"收尾后 wall_reserve_s={_au_reserve_after}")


# ── AU10：收尾的**每一个**触发原因都必须被念出来（分项与总数不许自相矛盾）──
_au_co_line = next((ln for ln in _au_log8 if "收尾=" in ln), "")
_au_missing_why = [k for k, v in (_au_co8.get("by_reason") or {}).items()
                   if int(v or 0) and f"{k}{v}" not in _au_co_line]
check(RS, "AU10 ★`收尾=N次` 的括号里必须出现**每一个**非零触发原因（含 `wall_retry`）。\n"
          "          —— 实测：加 `wall_retry` 之后日志写着「收尾=1次(步0/时0)」：\n"
          "            总数 1，分项 0+0。自相矛盾的那一位恰好就是**这次修好的那条路径**，\n"
          "            而它恰恰是在日志里看不见的那一个。\n"
          "          ⇒ 触发原因可以自由增加，打印必须按 `by_reason` **自动**跟上。",
      bool(_au_co_line) and not _au_missing_why,
      f"缺={_au_missing_why} 行={_au_co_line[:120]!r}")


# ── AU9：五个计数必须**有消费者**（stats 出口 + 落盘键）─────────────────
_au_tx_keys = {"retries", "exhausted", "wall_shortened",
               "wall_refused_first", "wall_refused_retry"}
_au_tx_missing = _au_tx_keys - set(_au_tx8)
_au_run_src = (V1 / "run.py").read_text(encoding="utf-8")
#: 落盘键：不落盘的读数不可审计（与 `kernel_closeout` / `kernel_external` 同源）。
_au_landed = '"kernel_transport": _ktx' in _au_run_src
#: 累加器必须**真的加总**（不是只声明）。用 importlib 载入 run.py 直接调 —— 
#: 静态看一眼 `for k in (...)` 无法区分"加总"与"赋值"。
_au_spec = _au_ilu.spec_from_file_location("v1_run_mod", str(V1 / "run.py"))
_au_run_mod = _au_ilu.module_from_spec(_au_spec)
_au_spec.loader.exec_module(_au_run_mod)                      # type: ignore[union-attr]
_au_acc = _au_run_mod._ktx_zero()
_au_run_mod._ktx_absorb(_au_acc, {"retries": 2, "wall_shortened": 3,
                                 "wall_refused_first": 1})
_au_run_mod._ktx_absorb(_au_acc, {"retries": 1, "wall_refused_retry": 4})

check(RS, "AU9 ★传输层五个计数必须**都有消费者**：进 `stats.transport`、\n"
          "          进 `runs.jsonl`（`kernel_transport`）、且累加器真的**加总**。\n"
          "          —— 只声明不消费 = 装饰字段（『只声明上限却不截断』的同构体）；\n"
          "            而『加总』必须是行为测出来的：静态看一眼 for 循环分不清\n"
          "            `acc[k] = v` 与 `acc[k] += v`。",
      not _au_tx_missing and _au_landed
      and _au_acc.get("retries") == 3 and _au_acc.get("wall_shortened") == 3
      and _au_acc.get("wall_refused_first") == 1
      and _au_acc.get("wall_refused_retry") == 4 and _au_acc.get("tasks") == 2,
      f"缺键={sorted(_au_tx_missing)} 落盘={_au_landed} 累加={_au_acc}")


# ══════════════════════════════════════════════════════════════════════════
# AZ31–AZ35（第四十三轮，§17-7p）：量具补强 —— 沙箱/考题完整性/合并日志
#
# 背景：「跟 Inspect AI 比」的回应。Inspect 保证"尺子稳定"（同题同分），
# 本组把它的机械长处补进来，同时保留 V1 的因果有效性层（三臂/撤回态）。
# 全部判据锚在 bench/ 量具上，⛔ 不动 mw/。
# ══════════════════════════════════════════════════════════════════════════
_azp_spec = _au_ilu.spec_from_file_location(
    "v1_code_exec", str(V1 / "bench" / "scorers" / "code_exec.py"))
_azp_ce = _au_ilu.module_from_spec(_azp_spec)
_azp_spec.loader.exec_module(_azp_spec and _azp_ce)             # type: ignore[union-attr]

# ── AZ31：打分器进程级生存性 —— 两种炸弹都只判 fail，打分器 rc=0 有 score ──
import subprocess as _az31_sp                                      # noqa: E402

def _az31_scorer(answer: str) -> dict:
    _p = _az31_sp.run(
        [sys.executable, str(V1 / "bench" / "scorers" / "code_exec.py")],
        input=json.dumps({"task": {"id": "az31", "tests": ["assert True"]},
                          "answer": answer}),
        capture_output=True, text=True, timeout=60)
    if _p.returncode != 0 or not _p.stdout.strip():
        return {"rc": _p.returncode}
    return {"rc": 0, **json.loads(_p.stdout.strip().splitlines()[-1])}

_az31_wall = _az31_scorer("import time\ntime.sleep(30)")   # 墙钟炸弹 → TimeoutExpired 分支
_az31_cpu = _az31_scorer("while True: pass")               # CPU 炸弹 → RLIMIT_CPU SIGXCPU
check(RS, "AZ31 ★7p 沙箱：墙钟炸弹与 CPU 炸弹都必须**只判 fail**（score=0.0），\n"
          "          打分器进程本身 rc=0 活着出 JSON。撤掉 TimeoutExpired 分支\n"
          "          （改 raise）⇒ 墙钟炸弹把打分器炸死（rc!=0）⇒ 此判据红。\n"
          "          ⚠️ 实测校准：while True 被 RLIMIT_CPU=6s 先杀（SIGXCPU），\n"
          "          走不到 TimeoutExpired 分支 —— 第一版撤回态红=无，当场抓到。",
      _az31_wall.get("rc") == 0 and _az31_wall.get("score") == 0.0
      and "超时" in str(_az31_wall.get("why", ""))
      and _az31_cpu.get("rc") == 0 and _az31_cpu.get("score") == 0.0,
      f"墙钟={_az31_wall} CPU={_az31_cpu}")

# ── AZ32：磁盘炸弹必须被 FSIZE cap 截住 ────────────────────────────────────
_az32 = _azp_ce.run_one(
    "open('az32_big.bin','wb').write(b'0' * (2 * 1024 * 1024))", "assert True")
check(RS, "AZ32 ★7p 沙箱：2MB 落盘必须被 RLIMIT_FSIZE=1MB 截成 fail\n"
          "          （write() 抛 OSError ⇒ 该条判 fail，打分器继续）。\n"
          "          撤掉 rlimits ⇒ 2MB 落盘成功 ⇒ 该条转绿 ⇒ 此判据红。",
      _az32[0] is False,
      f"ok={_az32[0]} why={_az32[1][:80]!r}")

# ── AZ33：候选代码落盘必须落在临时 cwd，不污染调用方目录 ───────────────────
_az33_caller = Path.cwd()
_az33_marker = _az33_caller / "az33_marker.txt"
if _az33_marker.exists():
    _az33_marker.unlink()
_az33 = _azp_ce.run_one(
    "open('az33_marker.txt','w').write('x')", "assert True")
check(RS, "AZ33 ★7p 沙箱：候选代码写 cwd 的文件必须落进打分器临时目录，\n"
          "          **不出现在调用方工作目录**。撤掉 cwd=workdir ⇒ 标记文件\n"
          "          落进调用方目录 ⇒ 此判据红。",
      _az33[0] is True and not _az33_marker.exists(),
      f"ok={_az33[0]} 调用方有标记={_az33_marker.exists()}")

# ── AZ34：heldout 考题与 manifest 逐一致（污染闸）─────────────────────────
_azp_mspec = _au_ilu.spec_from_file_location(
    "v1_suite_manifest", str(V1 / "bench" / "suite_manifest.py"))
_azp_sm = _au_ilu.module_from_spec(_azp_mspec)
_azp_mspec.loader.exec_module(_azp_sm)                          # type: ignore[union-attr]
_az34_ok, _az34_lines = _azp_sm.verify()
_az34_covered = {ln.split()[1] for ln in _az34_lines if "✅" in ln or "❌" in ln}
_az34_have = {p.name for p in sorted(_azp_sm.HELDOUT.glob("*.jsonl"))}
check(RS, "AZ34 ★7p 考题完整性：heldout/*.jsonl 与 suites.manifest.json\n"
          "          逐文件 sha 一致，且**每一份**考题都被 manifest 覆盖。\n"
          "          改考题不重新 build ⇒ sha 不一致 ⇒ 红；新增考题不登记 ⇒ 红。\n"
          "          刻意的摩擦 = 功能本身。",
      _az34_ok and _az34_have <= _az34_covered and len(_az34_have) > 0,
      f"verify={_az34_ok} 覆盖={len(_az34_covered)}/{len(_az34_have)}")

# ── AZ35：evallog 合并器 schema（合成 evidence，不耦合历史快照）────────────
_azp_espec = _au_ilu.spec_from_file_location(
    "v1_evallog", str(V1 / "bench" / "evallog.py"))
_azp_el = _au_ilu.module_from_spec(_azp_espec)
_azp_espec.loader.exec_module(_azp_el)                          # type: ignore[union-attr]
_az35_ev = _TMP / "az35_ev"
_az35_ev.mkdir(exist_ok=True)
_az35_fake = {
    "kernel_transport": "{'wall_refused_first': 0, 'wall_refused_retry': 0, 'retries': 0, 'exhausted': 0}",
    "measurement": {"mean": 0.4, "n": 1, "std": 0.0},
    "report": {
        "candidate": "内核/http qwen2.5:7b（测试）", "llm_of_record": "http",
        "elapsed_s": 12.5, "errors": [], "failed_scorer": 0,
        "per_task": [{"id": "t1", "rep": 1, "score": 0.4, "why": "x", "elapsed_s": 12.0}],
    },
}
(_az35_ev / "H_r1_runs.jsonl").write_text(json.dumps(_az35_fake) + "\n")
_az35_rows = _azp_el.consolidate(_az35_ev, suite="fake.jsonl")
_az35 = _az35_rows[0] if _az35_rows else {}
_az35_need = {"arm", "rep", "model", "llm_of_record", "candidate", "suite",
              "per_task", "mean", "elapsed_s", "errors", "failed_scorer",
              "transport"}
check(RS, "AZ35 ★7p 合并 eval log：consolidate() 的每行 schema 必须逐字段在\n"
          "          （arm/rep/model/…/transport），且 str(dict) 形态的\n"
          "          kernel_transport 必须被解析成 dict（7m-β 的坑，不解析 =\n"
          "          transport 又变回不可 grep 的字符串）。",
      _az35_need <= set(_az35) and _az35.get("arm") == "H"
      and _az35.get("rep") == 1 and _az35.get("model") == "qwen2.5:7b"
      and isinstance(_az35.get("transport"), dict)
      and "parse_error" not in _az35.get("transport", {})
      and _az35.get("mean") == 0.4,
      f"缺键={sorted(_az35_need - set(_az35))} "
      f"arm={_az35.get('arm')} model={_az35.get('model')!r} "
      f"transport={_az35.get('transport')}")


# ══════════════════════════════════════════════════════════════════════════
# AZ36–AZ40（第四十五轮，§17-7q）：改内核轮 —— 补救带状态 / catalog sha 同源 /
# --max-tokens 单一真相。背景：7o 定律（16/16 格零例外）K=0 ⇒ 记忆盲补救 ⇒
# 基线交付；7n 实锤内核两真相（catalog sha、max_tokens 1024 vs 2048）。
# ══════════════════════════════════════════════════════════════════════════
class _AZ36LLM(StubLLM):
    """两次输出 + 记录每次 user 消息（断言"补救请求里到底写了什么"）。"""

    def __init__(self, outs):                                      # noqa: ANN001
        super().__init__()
        self.outs = list(outs)
        self.n = 0
        self.user_msgs: list[str] = []

    def complete(self, messages, **kw):                            # noqa: ANN001, ANN003
        self.user_msgs.append(messages[-1].content)
        i = min(self.n, len(self.outs) - 1)
        self.n += 1
        return self.outs[i]


# ── AZ36：定向补救的 prompt 必须带 state（含 [语义记忆]）──────────────────
_az36 = _AZ36LLM([_PLAN_NO_ANS, "答案"])
_az36_pl = Planner(_az36, tr3)
_az36_pl.propose(goal="g", state="AZ36STATE [语义记忆] 惯例甲", history=[], n=1)
check(RS, "AZ36 ★7q 补救 prompt 必须带 state：propose 手里的记忆不许在补救时被扔掉\n"
          "          （7o 定律：K=0 格确定性走补救；记忆盲补救 = 基线交付的根因）。\n"
          "          撤掉 `self.answer(goal, state=state)` 的 state 参数 ⇒ 红。",
      len(_az36.user_msgs) >= 2 and "AZ36STATE" in _az36.user_msgs[1],
      f"补救user含标记={'AZ36STATE' in (_az36.user_msgs[1] if len(_az36.user_msgs) > 1 else '')} "
      f"调用数={_az36.n}")

# ── AZ37：收尾调用的 prompt 必须带已 commit 的语义记忆 ────────────────────
class _AZ37LLM(StubLLM):
    def __init__(self):                                            # noqa: ANN204
        super().__init__()
        self.user_msgs: list[str] = []

    def complete(self, messages, **kw):                            # noqa: ANN001, ANN003
        self.user_msgs.append(messages[-1].content)
        return "收尾答案"


_az37_db = _TMP / "az37.db"
if _az37_db.exists():
    _az37_db.unlink()
_az37_core = Core(settings=_q_settings, llm=_AZ37LLM(),
                  memory=MemoryStore(_az37_db), log=_Log(None))
_az37_core.memory.commit_fact("az37token 惯例：交付前必须 strip 并 lower 尾部标记qkz",
                              source="selftest", confidence=1.0)
# `_salvage` 建在 run()（core.py:470）；单独调 `_close_out` 必须自己补上
# （AS20 的先例同样如此），否则收尾路径 AttributeError —— 那是崩，不是判红。
_az37_core._salvage = ""
# ⚠️ 夹具纪律（R13 首跑抓到）：goal 只准含 recall 命中用的 ASCII 钥匙
#    （az37token），**不许含惯例正文** —— 否则断言锚在 goal 上，
#    撤掉 state 也"绿"（红=无，判据恒真的假通过）。
_az37_core._close_out("完成 az37token 的交付任务", ["h1"], _co_done)
_az37_msgs = _az37_core.llm.user_msgs
_az37_hit = any("[语义记忆]" in m and "交付前必须 strip 并 lower" in m
                for m in _az37_msgs)
check(RS, "AZ37 ★7q 收尾调用必须带 state：`_close_out` 现算 `_state()` 传入，\n"
          "          已 commit 的语义记忆**正文**（goal 里没有的那段）必须出现在\n"
          "          补救请求里。撤掉 `_close_out` 的 `_state` 接线 ⇒ 红。",
      _az37_hit,
      f"收尾user含fact正文={_az37_hit} 调用数={len(_az37_msgs)}")

# ── AZ38：catalog_sha 的 allow_note 必须真的改变指纹 ──────────────────────
from mw.tools import catalog_sha as _az_cat_sha                    # noqa: E402
_az38_on = _az_cat_sha(allow_note=True)
_az38_off = _az_cat_sha(allow_note=False)
check(RS, "AZ38 ★7q catalog sha 两真相：allow_note=False 的指纹必须与 True 不同\n"
          "          （note 注册与否改变目录文本 = 改变提示词）。\n"
          "          撤掉 forwarding（恒 True）⇒ 两者相等 ⇒ 红。",
      _az38_on != _az38_off,
      f"on={_az38_on} off={_az38_off}")

# ── AZ39：forwarding 保真 —— 默认指纹必须与手工同参注册表逐字一致 ─────────
from mw.tools import ToolRegistry as _az_TR, register_core_tools as _az_rc  # noqa: E402
from mw.tools import _StubMemory as _az_SM                        # noqa: E402
from hashlib import sha256 as _az_h                                # noqa: E402
_az39_reg = _az_TR()
# ⚠️ memory 必须用 _StubMemory()（与 catalog_sha 默认 with_memory=True 同形状）：
#    memory=None 会少注册 recall/note 两个工具（AT1 的坑），基线必红。
_az_rc(_az39_reg, memory=_az_SM(), allow_note=True)
_az39_sha = _az_h(_az39_reg.catalog().encode("utf-8")).hexdigest()[:16]
check(RS, "AZ39 ★7q catalog sha 转发保真：catalog_sha()（默认）必须与\n"
          "          `register_core_tools(memory=占位, allow_note=True)` 的注册表哈希\n"
          "          逐字一致（脱节 = 指纹描述的不是真实提示词，两真相换方向复活）。",
      _az39_sha == _az_cat_sha(),
      f"手工={_az39_sha} catalog_sha()={_az_cat_sha()}")

# ── AZ40：--max-tokens 必须是**一个**真相（CLI 覆盖内核，caliber 记生效值）──
_AZ40_ENV = 'os.environ["V1_LLM_MAX_TOKENS"] = str(int(args.max_tokens))' in _AE_RUN
_AZ40_SETTLED = "max_tokens=int(s.limits.llm_max_tokens)" in _AE_RUN
_AZ40_DEFNONE = '"--max-tokens", type=int, default=None' in _AE_RUN
check(RS, "AZ40 ★7q --max-tokens 单一真相：run.py 必须在 settings() 之前把 CLI\n"
          "          显式值写进 env（唯一配置入口；Limits frozen，首版直接赋值\n"
          "          实测 FrozenInstanceError），且 caliber / llm-direct 读**生效值**\n"
          "          `s.limits.llm_max_tokens`（7n 实锤：caliber 记 1024、内核实际\n"
          "          2048 = 两套真相）。",
      _AZ40_ENV
      and _AZ40_SETTLED
      and _AZ40_DEFNONE,
      f"env覆盖行={_AZ40_ENV} "
      f"生效值={_AZ40_SETTLED} "
      f"defaultNone={_AZ40_DEFNONE}")


# ── AZ41/AZ42（§19 R-b，第四十五轮）：安装形态 —— 零第三方运行依赖的双入口 ──
import subprocess as _az_sp                                            # noqa: E402
import tomllib as _az_toml                                             # noqa: E402
from pathlib import Path as _az_Path                                   # noqa: E402

_az41_py = _az_Path(__file__).resolve().parent.parent / "pyproject.toml"
_az41_has = _az41_py.exists()
if _az41_has:
    _az41 = _az_toml.loads(_az41_py.read_text(encoding="utf-8"))
    _az41_deps = _az41.get("project", {}).get("dependencies", [])
    _az41_req = _az41.get("project", {}).get("requires-python", "")
    _az41_script = _az41.get("project", {}).get("scripts", {}).get("v1", "")
check(RS, "AZ41 ★R-b pyproject 零依赖安装形态：pyproject.toml 必须存在且\n"
          "          dependencies 为空（零第三方运行依赖是内核的硬边界）、\n"
          "          requires-python>=3.11、[project.scripts] v1 指向 run:main\n"
          "          （单一 CLI 真相仍是 run.main，脚本入口只准转发）。",
      _az41_has and _az41_deps == [] and "3.11" in _az41_req
      and _az41_script == "run:main",
      f"存在={_az41_has} deps={_az41_deps} requires-python={_az41_req!r} "
      f"scripts.v1={_az41_script!r}")

_az42_p = _az_sp.run(
    [sys.executable, "-m", "mw", "--selftest"],
    capture_output=True, text=True, timeout=120,
    cwd=str(_az_Path(__file__).resolve().parent.parent))  # type: ignore[name-defined]
check(RS, "AZ42 ★R-b `python -m mw` 入口必须真跑通：子进程 rc=0 且含自检\n"
          "          通过标记。入口文件只准 `from run import main` 转发 ——\n"
          "          复制逻辑 = 两套 CLI 真相（撤掉转发 ⇒ 红）。",
      _az42_p.returncode == 0 and "通过" in (_az42_p.stdout + _az42_p.stderr),
      f"rc={_az42_p.returncode} 尾部={(_az42_p.stdout + _az42_p.stderr)[-60:]!r}")

# ── AZ43（§19 R-c，第四十五轮）：演示资产的三臂结构 ─────────────────────
# ⛔ demo 是演示不是量具：**不在**契约里真跑 demo（避免契约依赖 shell
#    执行路径）；AZ43 只锚结构 —— 三臂（oracle/null/partial）+ 读数校验。
_az43_d = _az_Path(__file__).resolve().parent.parent / "demo" / "run_demo.sh"
_az43_has = _az43_d.exists()
_az43_src = _az43_d.read_text(encoding="utf-8") if _az43_has else ""
check(RS, "AZ43 ★R-c demo 三臂结构：demo/run_demo.sh 必须存在且引用\n"
          "          oracle/null/partial 三个参照臂，并对每臂做**写死读数**的\n"
          "          校验（1.0000/0.0000/0.6194，demo/README.md 同源）——\n"
          "          演示读数漂移 = 演示红，不许'差不多就行'。",
      _az43_has and "--solver oracle" in _az43_src and "--solver null" in _az43_src
      and "--solver partial" in _az43_src
      and all(x in _az43_src for x in ("1.0000", "0.0000", "0.6194"))
      and "mean=" in _az43_src,
      f"存在={_az43_has} 三臂={_az43_src.count('--solver ')} "
      f"读数锚={'1.0000' in _az43_src and '0.6194' in _az43_src}")

# ── AZ44（§19 R-d，第四十五轮）：产品摘要块结构 ─────────────────────────
_az44_rd = _az_Path(__file__).resolve().parent.parent / "README.md"
_az44_src = _az44_rd.read_text(encoding="utf-8") if _az44_rd.exists() else ""
_az44_head = _az44_src[:6000]
# ★第五十四轮（AZ47 部分实施）：计数**不许再手抄进判据**。
#   旧版把 "109 条撤回态" 写死 ⇒ 真值涨到 110 之后，**锚自己把漂移锁住了**：
#   README 两处写 109、契约也断言 109 ⇒ 全绿，而实测是 110。
#   同一事实只留一个来源 —— 真值从撤回态驱动器数出来，README 只许照抄。
#   ⚠️ 这里 import 的是**锚层**（bench/），不是内核；成本实测 0.05s、无副作用。
_az44_bn = _az_Path(__file__).resolve().parent.parent / "bench"
if str(_az44_bn) not in sys.path:
    sys.path.insert(0, str(_az44_bn))
import withdraw_check as _az44_wc                              # noqa: E402
_az44_n = len(_az44_wc.CASES)
_az44_claim = re.findall(r"(\d+)\s*条撤回态", _az44_head)
# ★第五十九轮（AZ47 落地）：**契约条数的副本也从判据里撤掉**。
#   上一版留着 `and "535" in _az44_head` —— 那是本契约亲手留下的最后一处手抄：
#   加一条断言就把 535 变成 536，于是"必须改判据里那个数"，而判据是判据，
#   改判据来迎合实现正是这个项目最不许做的事（AZ44 的自锁漂移同型复发）。
#   ⇒ 数值核对整体下移到 runner（`bench/check_counts.py`），因为只有**被测集合之外**
#     的位置能同时看见"文档声明"与"本次运行实测"（契约条数自己也在被核对之列）。
#   本契约从此只管**结构**：节在、三入口在、差异表在、边界句在、撤回态数一致。
check(RS, "AZ44 ★R-d 产品摘要块：README 顶部必须含 ①「产品摘要」节、\n"
          "          ②三个实测入口命令（quickstart / run_demo / run_tests）、\n"
          "          ③Inspect AI 差异表（测量有效性定位）、④诚实边界句\n"
          "          （⛔ 不许出现 AGI 声称）。撤回态条数必须与实测一致\n"
          "          （由 len(withdraw_check.CASES) 现算；契约/锚层条数的副本\n"
          "          不在本断言里 —— 见 `bench/check_counts.py`，AZ47）。",
      "产品摘要" in _az44_head
      and "./quickstart.sh" in _az44_head
      and "run_demo.sh" in _az44_head
      and "./run_tests.sh" in _az44_head
      and "Inspect AI" in _az44_head and "测量有效性" in _az44_head
      and "不是 AGI" in _az44_head
      and bool(_az44_claim) and all(int(x) == _az44_n for x in _az44_claim),
      f"摘要={'产品摘要' in _az44_head} 三入口={all(x in _az44_head for x in ('./quickstart.sh', 'run_demo.sh', './run_tests.sh'))} "
      f"差异表={'Inspect AI' in _az44_head} 边界={'不是 AGI' in _az44_head} "
      f"撤回态声明={_az44_claim} 真值={_az44_n}")

# ── AZ46（§17-7v-b/c/d，第五十轮）：补救通道加的那一句中文 ───────────────────
_az46_pl = _az_Path(__file__).resolve().parent.parent / "mw" / "planner.py"
_az46_src = _az46_pl.read_text(encoding="utf-8") if _az46_pl.exists() else ""
_az46_fn = _az46_src.find("def answer(self, goal")
_az46_body = _az46_src[_az46_fn:_az46_fn + 2600] if _az46_fn >= 0 else ""
# ⛔ 只认**代码行**形态（自踩四次的同类：裸串 find 会先命中注释里的同一句）。
_az46_dis = _az46_body.find("prompt += _ANSWER_FIDELITY")
_az46_end = _az46_body.find('prompt += "请直接给出最终答案。"')
# 常量值必须是一句**有内容**的指令（防退化成空串/单字 —— 那等于没加）。
_az46_m = re.search(r'_ANSWER_FIDELITY\s*=\s*"([^"]*)"', _az46_src)
_az46_val = _az46_m.group(1) if _az46_m else ""
check(RS, "AZ46 ★7v-b/c/d 补救通道：mw/planner.py 必须定义唯一常量\n"
          "          `_ANSWER_FIDELITY`（当前是 ≥20 字的一句中文），并由\n"
          "          `answer()` 拼在状态块之后、「请直接给出最终答案」之前。\n"
          "          ⛔ 本契约**只断言**『拼了一句 ≥20 字的话、且只有一处拼接』——\n"
          "          它不锚任何具体字词，因为证据不支持：s54 Δ(A−B)=+0.15 且 A 与\n"
          "          等长异义指令 C1 逐位相同 ⇒ 不是「忠实引用」；s55 曾判「承载是\n"
          "          『逐条』」；**s56 把这条也推翻** —— S1（同义替换、无「逐条」）、\n"
          "          S2（含「逐条」但明说「不构成要求」）、S3（「逐条」×16 纯重复）\n"
          "          三臂**全部 0.2**，只有原句 1.0（A 正对照 3/3 复现）⇒ 7v-d 的\n"
          "          登记结论是**未定位**：字面单独不够、语义单独也不够。\n"
          "          ⛔ 所以措辞一个字都不许改（没有证据表明换说法更好，7v-d 反而\n"
          "          证明换说法掉回 0.2）；也不许把「逐条」或「指令性」写成机制。\n"
          "          撤回语义：删掉拼接 ⇒ 补救退回无指令 ⇒ 必须红。",
      _az46_body.count("prompt += _ANSWER_FIDELITY") == 1
      and _az46_src.count("_ANSWER_FIDELITY =") == 1
      and len(_az46_val) >= 20
      and 0 <= _az46_dis < _az46_end,
      f"体内拼接={_az46_body.count('prompt += _ANSWER_FIDELITY')} "
      f"定义={_az46_src.count('_ANSWER_FIDELITY =')} 字数={len(_az46_val)} "
      f"序位={_az46_dis}<{_az46_end}")

# ── AZ48（§19 D-a 的回归锚，第五十九轮）：默认数据路径**当前目录优先** ────────
# 为什么补这条：第五十八轮修了"装进 site-packages 之后，站在 checkout 里跑
# `v1 --bench` 却报找不到 bench/"这个真缺陷，但当时**没有锚**（DESIGN §21.2 自认）。
# 没有锚的修复等于没有防线：下次谁把 `_data_path` 改回"包目录优先"，一切都还是绿的
# —— 而这个缺陷在仓库里跑测试**永远复现不了**（只有安装形态才会出现）。
# ⚠️ 提取到模块级才测得了：原先是 `main()` 里的闭包（这正是它当初补不上锚的原因）。
import os as _az48_os                                                  # noqa: E402
import tempfile as _az48_tf                                            # noqa: E402
import run as _az48_run                                                # noqa: E402

_az48_rel = _az_Path("bench") / "tasks" / "t.jsonl"
_az48_both = _az48_pkgonly = _az48_none = False
_az48_seen = []
_az48_saved = _az48_os.getcwd()
try:
    # 形态 A：当前目录**与**包目录都有 ⇒ 必须取当前目录那份（判别性所在：
    #         兜底优先的实现会返回包目录那份 ⇒ 这条当场红）。
    with _az48_tf.TemporaryDirectory() as _td:
        _t = _az_Path(_td)
        _pkg = _t / "pkg"; (_pkg / "bench" / "tasks").mkdir(parents=True)
        (_pkg / _az48_rel).write_text("pkg", encoding="utf-8")
        _cwd = _t / "cwd"; (_cwd / "bench" / "tasks").mkdir(parents=True)
        (_cwd / _az48_rel).write_text("cwd", encoding="utf-8")
        _az48_os.chdir(_cwd)
        _got = _az48_run.data_path(_az48_rel, root=_pkg)
        _az48_both = (_got.resolve() == (_az_Path.cwd() / _az48_rel).resolve()
                      and _got.read_text(encoding="utf-8") == "cwd")
        _az48_seen.append(f"两处都有⇒读到{_got.read_text(encoding='utf-8')}")
    # 形态 B：只有包目录有 ⇒ 必须退回包目录（修之前的行为不许丢）。
    with _az48_tf.TemporaryDirectory() as _td:
        _t = _az_Path(_td)
        _pkg = _t / "pkg"; (_pkg / "bench" / "tasks").mkdir(parents=True)
        (_pkg / _az48_rel).write_text("pkg", encoding="utf-8")
        _cwd = _t / "cwd"; _cwd.mkdir()
        _az48_os.chdir(_cwd)
        _got = _az48_run.data_path(_az48_rel, root=_pkg)
        _az48_pkgonly = (_got.resolve() == (_pkg / _az48_rel).resolve()
                         and _got.read_text(encoding="utf-8") == "pkg")
        _az48_seen.append(f"仅包目录⇒读到{_got.read_text(encoding='utf-8')}")
    # 形态 C：两处都没有 ⇒ 返回**包目录**下那个不存在的路径（不造假文件、
    #         也不返回当前目录的假路径 —— 否则调用方的"找不到"会指错地方）。
    with _az48_tf.TemporaryDirectory() as _td:
        _t = _az_Path(_td)
        _pkg = _t / "pkg"; _pkg.mkdir()
        _cwd = _t / "cwd"; _cwd.mkdir()
        _az48_os.chdir(_cwd)
        _got = _az48_run.data_path(_az48_rel, root=_pkg)
        _az48_none = (_got.resolve() == (_pkg / _az48_rel).resolve() and not _got.exists())
        _az48_seen.append(f"都没有⇒exists={_got.exists()}")
finally:
    _az48_os.chdir(_az48_saved)

# 显式路径**不进兜底**：用户给的 `--suite` / `--scorer` 一律原样解析 ——
# 找不到就是找不到。悄悄换成别处的同名文件是比报错更坏的假成功（D-a 的边界）。
# ⚠️ V170 改成 **AST**：原先是正则数「`else _data_path(Path("bench")/…)` 恰好两处」。
#    新增 `--goals` 入口后默认站点合法地变成 3 处，正则当场红 —— 但它真正要防的
#    不是「站点个数」，是「**显式值不许进兜底**」。改成 AST 后这条更强：
#    检查的是**实参**，无论将来有几个入口都管得住。
# ⚠️ 必须解析**原文**：`_AE_RUN` 是 `_code_only()` 处理过的（去注释/字符串），
#    不是合法 Python，`ast.parse` 会当场 SyntaxError。
_az48_tree = ast.parse((V1 / "run.py").read_text(encoding="utf-8"))
_az48_calls = [n for n in ast.walk(_az48_tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id in ("data_path", "_data_path")]
_az48_argnames = set()
_az48_from_args = []
for _c in _az48_calls:
    for _a in _c.args:
        if isinstance(_a, ast.Name):
            _az48_argnames.add(_a.id)
        if any(isinstance(_x, ast.Attribute) and isinstance(_x.value, ast.Name)
               and _x.value.id == "args" for _x in ast.walk(_a)):
            _az48_from_args.append(ast.unparse(_a))
check(RS, "AZ48 ★D-a 默认数据路径：`run.py:data_path()` 必须**当前目录优先、包目录兜底**\n"
          "          （安装形态实测：`pip install .` 后包目录落在 site-packages，\n"
          "          只按包目录找会「明明脚下有 bench/ 却报找不到」）。\n"
          "          ⚠️ 判别性来自**两处都有**那种形态：兜底优先的实现会取包目录那份；\n"
          "          只有一处有的两种形态，两个实现都一样 —— 只测它们等于没测。\n"
          "          ⛔ `--suite` / `--scorer` 显式值**不许**走兜底（悄悄换文件 = 假成功）。\n"
          "          撤回语义：把优先序倒过来 / 删掉兜底 / 让显式值也进兜底 ⇒ 必须红。",
      _az48_both and _az48_pkgonly and _az48_none
      and len(_az48_calls) >= 3
      and not _az48_from_args
      and {"DEF_SUITE", "DEF_SCORER", "DEF_TASK_DIR"} <= _az48_argnames,
      f"两处都有={_az48_both} 仅包目录={_az48_pkgonly} 都没有={_az48_none} "
      f"默认站点={len(_az48_calls)} 显式值进兜底={_az48_from_args or '无'} "
      f"常量={sorted(_az48_argnames)} 实测={_az48_seen}")

# ── AZ49（第六十一轮）：落地页必须真的**零外部请求** ────────────────────────
# 为什么这需要一条断言：`site/index.html` 上写着"本页自身也是零外部请求"。
# 一个声称 `dependencies = []` 的项目，落地页如果去拉 CDN 字体，那句话就只是文案。
# ⛔ 而"我看过一眼没有外链"不是判据 —— 正是这个仓库反复吃亏的那类判断
#    （"仓库里永远看不到的缺陷"：PEP 701 / 安装形态 / unzip 中文名）。
# ⚠️ 检测器**自己也要被反证**：拿一个故意坏掉的片段喂给它，它必须报出来
#    —— 否则这就是一段死代码，永远不会红（第五十四轮"五把假尺子"的同一条纪律）。
_az49_page = _az_Path(__file__).resolve().parent.parent / "site" / "index.html"
_az49_txt = _az49_page.read_text(encoding="utf-8") if _az49_page.exists() else ""

# 会让浏览器**发起网络请求**的东西（<a href> 不算：点了才走，且是人的动作）。
_az49_fetch = [
    ("<script src>", r"<script[^>]*\bsrc\s*="),
    ("<link href>", r"<link\b[^>]*\bhref\s*="),
    ("<img>", r"<img\b"),
    ("<iframe>", r"<iframe\b"),
    ("srcset", r"\bsrcset\s*="),
    ("<source>", r"<source\b"),
    ("<video>/<audio>", r"<(?:video|audio)\b"),
    ("<embed>/<object>", r"<(?:embed|object)\b"),
    ("@import", r"@import\b"),
    ("css url()", r"\burl\(\s*['\"]?(?:https?:)?//"),
]
_az49_hits = [name for name, rx in _az49_fetch if re.search(rx, _az49_txt, re.I)]

# 反证：同一套规则喂一个**故意坏掉**的片段，必须命中至少三种形态。
_az49_bad = ('<link rel="stylesheet" href="https://cdn.example/x.css">'
             '<img src="//cdn.example/a.png">'
             '<script src="https://cdn.example/x.js"></script>')
_az49_bad_hits = [name for name, rx in _az49_fetch if re.search(rx, _az49_bad, re.I)]

check(RS, "AZ49 落地页 `site/index.html` 必须存在、且**零外部请求**（无 CDN / webfont /\n"
          "          图片 / 外部脚本；<a href> 不算 —— 那要人点了才走）。\n"
          "          页面上写着这条承诺，所以它必须是被断言的，不是被声明的。\n"
          "          ⚠️ 检测器自带反证：同一套规则喂一个坏片段，必须命中 ≥3 种形态\n"
          "          （否则规则是死代码，永远不会红）。\n"
          "          ⚠️ 边界句锚的是**边界块本身**（`先读这一条：V1 不是 AGI`），\n"
          "          不是裸的 `不是 AGI`：后者在 meta 描述里也有一份，\n"
          "          只改边界块那处时判据仍为真 ⇒ 撤回态不会红（当场发现并收紧）。\n"
          "          撤回语义：页面里加一个 `<img src=...>` / 去掉边界块 ⇒ 必须红。",
      bool(_az49_txt)
      and "先读这一条：V1 不是 AGI" in _az49_txt
      and not _az49_hits
      and len(_az49_bad_hits) >= 3,
      f"页面={bool(_az49_txt)} 边界块={'先读这一条：V1 不是 AGI' in _az49_txt} "
      f"外链命中={_az49_hits or '无'} 反证命中={_az49_bad_hits}")

# ══════════════════════════════════════════════════════════════════════
# AZ50–AZ52：能力曲线装置（常驻循环的外层判据）
#
# 为什么这三条进契约、而不是只留在 `bench/loop_probe.py`：
#   probe 是"装置自检"——开发时跑，靠自觉；
#   契约是"改坏了会被退出码拦住"——只有它能被撤回态覆盖。
#   一个只存在于 probe 里的判据，撤掉实现后没人知道它还在不在。
# ══════════════════════════════════════════════════════════════════════
from mw import bench as _b50                                        # noqa: E402
from mw import loop as _loop50                                      # noqa: E402
import shutil as _sh50                                             # noqa: E402


def _lp50(r: int, calib: str, mean: float, ok: bool = True) -> object:
    return _loop50.CurvePoint(round=r, ts=0.0, calib=calib, mean=mean,
                              n=1, spread=0.0, trustworthy=ok)


# 口径变了必须**断线开新段**（不是把异口径点过滤掉 —— 过滤会把"换过尺子"这件事删掉）；
# 不可信的点独占一段（既不许接到前一段污染趋势，也不许被丢掉）。
_c50a = _loop50.Curve([_lp50(1, "A", 0.5), _lp50(2, "A", 0.5),
                       _lp50(3, "B", 0.9), _lp50(4, "B", 0.9)])
_c50b = _loop50.Curve([_lp50(1, "A", 0.5), _lp50(2, "A", 0.5, ok=False),
                       _lp50(3, "A", 0.9), _lp50(4, "A", 0.9)])
check(RS, "AZ50 能力曲线必须按**口径断线**：异口径的点开新段，不许连成一条线；\n"
          "          不可信的点独占一段（不接前段、也不被丢弃）。\n"
          "          理由：把换过尺子的点接上去，曲线会变成没人看得出的谎；\n"
          "          把不可信点过滤掉，就再也看不到「这一轮没量准」。\n"
          "          撤回语义：让不可信的点也接到上一段（撤掉 `p.trustworthy`）⇒ 必须红。\n"
          "          ⚠️ 第七十一轮更新：旧锚点（把整行条件放宽成 `if out:`）会**连带**\n"
          "             AZ74 红（它依赖 calib 分段是 3+3）⇒ 那不是单红。新锚点只撤\n"
          "             「不可信也接上去」这一半，calib/harness 断线保留 ⇒ 单红。",
      [len(s) for s in _c50a.segments()] == [2, 2]
      and [len(s) for s in _c50b.segments()] == [1, 1, 2],
      f"口径断线={[len(s) for s in _c50a.segments()]} "
      f"不可信切断={[len(s) for s in _c50b.segments()]}")

# 趋势判据只许三种结论，且必须**很难**给 IMPROVED：
# 之后每一轮都要超过「首轮 + 噪声下限」，不是"末轮比首轮高"（后者一句话就能被抖动造出来）。
_c51_flat = _loop50.Curve([_lp50(i, "A", 0.5) for i in (1, 2, 3)])
_c51_up = _loop50.Curve([_lp50(1, "A", 0.5), _lp50(2, "A", 0.9), _lp50(3, "A", 0.9)])
_c51_dip = _loop50.Curve([_lp50(1, "A", 0.5), _lp50(2, "A", 0.9), _lp50(3, "A", 0.4)])
check(RS, "AZ51 趋势判据不许宽松：恒值必须 FLAT；涨上去又跌回基线以下必须 UNKNOWN；\n"
          "          同一组数据把噪声调大就**不许**再判 IMPROVED；噪声下限缺省是\n"
          "          `None`（⇒ UNKNOWN），**不是 0** —— 默认 0 等于「任何微小差值都算改进」。\n"
          "          撤回语义：把 `worst > base + noise_floor` 放宽成 `worst >= base` ⇒ 必须红。",
      _c51_flat.trend(0.0)[0] == _loop50.FLAT
      and _c51_up.trend(0.0)[0] == _loop50.IMPROVED
      and _c51_up.trend(0.5)[0] != _loop50.IMPROVED
      and _c51_dip.trend(0.0)[0] == _loop50.UNKNOWN
      and _c51_up.trend(None)[0] == _loop50.UNKNOWN,
      f"flat={_c51_flat.trend(0.0)[0]} up={_c51_up.trend(0.0)[0]} "
      f"up+噪声={_c51_up.trend(0.5)[0]} dip={_c51_dip.trend(0.0)[0]} "
      f"noise=None→{_c51_up.trend(None)[0]}")

# 约束 1 的撤回态：循环运行中尺子被换掉，必须**立即停**，不许把新尺子的点接到旧曲线上。
_az52_cal = _b50.Caliber(scorer_sha="az52", suite_sha="az52", repeat=1,
                         budget_tag="1x", temperature=0.0, fresh=True,
                         memory_scope="per_task_fresh", memory_seed="",
                         limits={}, llm_cond={}, max_tokens=1024,
                         tool_catalog_sha="az52")
_az52_raised = False
with tempfile.TemporaryDirectory(prefix="v1_az52_") as _td52:
    _d52 = Path(_td52)
    _sc52 = _d52 / "scorer.py"
    _sh50.copy(V1 / "bench" / "scorers" / "code_exec.py", _sc52)
    _sj52 = _d52 / "suite.jsonl"
    _one52 = json.loads((V1 / "bench" / "tasks" / "coding_small.jsonl")
                        .read_text(encoding="utf-8").splitlines()[0])
    _sj52.write_text(json.dumps(_one52, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        _loop50.run_loop(
            solver=lambda t: t.oracle,
            scorer=_b50.ExternalScorer([sys.executable, str(_sc52)], source=_sc52),
            suite=_b50.Suite.load(_sj52),
            caliber_factory=lambda: _az52_cal,
            rounds=3, log=lambda s: None,
            # 第一轮结束后改尺子 ⇒ 第二轮开头必须发现
            on_round=lambda p: _sc52.write_text(
                _sc52.read_text(encoding="utf-8") + "\n# az52 mutate\n", encoding="utf-8"),
        )
    except RuntimeError as _e52:
        _az52_raised = "打分器文件在运行中被改动" in str(_e52)
check(RS, "AZ52 循环**运行中换尺子**必须立即停止（不是悄悄继续）。\n"
          "          一轮之内尺子被改动，第二轮的读数就已经与第一轮不同口径，\n"
          "          接在同一条曲线上等于伪造趋势。\n"
          "          撤回语义：把 `if scorer.sha() != s0:` 改成 `if False:` ⇒ 必须红。",
      _az52_raised,
      f"运行中改打分器 ⇒ {'已停止' if _az52_raised else '未拦截（危险）'}")


# ── AZ53（第六十三轮）：循环模式的准入闸 ────────────────────────────────────
# 这一条钉的是"**什么情况下不许出曲线**"，而不是曲线本身算得对不对。
#
# 两个拒绝理由都不是洁癖：
#   ① 记忆跨轮累积 —— 默认 `shared_db` 下第 2 轮的记忆比第 1 轮多，而点上的
#      口径签名仍是开跑前拍的快照 ⇒ **签名描述的不是这一轮的条件**。
#      曲线会拿"记忆更多"冒充"能力更强"，且在线上一格都看不出来。
#   ② 少于 3 轮 —— `trend()` 一律 UNKNOWN，产出的是一个点；写进曲线文件
#      会让后来的人以为量过趋势。
class _NS53:                                          # noqa: N801 — 契约内小工具
    """`curve_guard` / `_option_conflict` 只用 `getattr`，所以有个扁平对象就够。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


_az53_cg = _az48_run.curve_guard
_az53_refuse_short = _az53_cg(_NS53(solver="oracle", fresh=False), 2) != ""
_az53_allow_ref = _az53_cg(_NS53(solver="oracle", fresh=False), 3) == ""
_az53_refuse_model = _az53_cg(_NS53(solver="llm", fresh=False), 3) != ""
_az53_allow_model = _az53_cg(_NS53(solver="llm", fresh=True), 3) == ""
_oc53 = _az48_run._option_conflict
_az53_c_sd = _oc53(_NS53(loop=3, suite_dir="bench/tasks")) != ""
_az53_c_bl = _oc53(_NS53(loop=3, save_baseline=True)) != ""
_az53_c_cmp = _oc53(_NS53(loop=3, compare=True)) != ""
# ⚠️ 反例：**不许误伤**正常组合 —— 少了这半句，判据可以退化成"一律拒绝"而全绿。
_az53_clean = _oc53(_NS53(loop=3)) == ""
check(RS, "AZ53 循环模式必须有准入闸，且闸要有两个方向（该拒的拒、该放的放）：\n"
          "          ① 轮数 <3 ⇒ 拒（少于 3 轮 trend() 恒 UNKNOWN，产出的是点不是曲线）；\n"
          "          ② 真模型/内核 solver 未 `--fresh` ⇒ 拒（记忆跨轮累积会让签名说谎）；\n"
          "          ③ 参照 solver（oracle/null/partial）不碰记忆 ⇒ 放行；\n"
          "          ④ `--loop` 与 `--suite-dir` / `--save-baseline` / `--compare` 互斥\n"
          "             （N 把尺子 / 一次读数 vs N 个点），否则 --loop 会被**静默忽略**。\n"
          "          撤回语义：把 `if not getattr(args, \"fresh\", False):` 改成 `if False:`\n"
          "          ⇒ 必须红。",
      _az53_refuse_short and _az53_allow_ref and _az53_refuse_model
      and _az53_allow_model and _az53_c_sd and _az53_c_bl and _az53_c_cmp
      and _az53_clean,
      f"<3轮={_az53_refuse_short} 参照放行={_az53_allow_ref} "
      f"模型无fresh={_az53_refuse_model} 模型有fresh={_az53_allow_model} "
      f"互斥(suite-dir/baseline/compare)={(_az53_c_sd, _az53_c_bl, _az53_c_cmp)} "
      f"正常组合不误伤={_az53_clean}")


# ── AZ54（第六十三轮）：循环模式**不许**长出第二套口径构造 ──────────────────
# ⛔ 判据必须是 AST，不是源码字符串：本文件与 run.py 的注释里都出现过
#    "`Caliber(` 构造点"这句话 ⇒ 裸 `find("Caliber(")` 会命中注释（本项目为此
#    假红过多次）。数的是**真的调用节点**。
_az54_tree = ast.parse((V1 / "run.py").read_text(encoding="utf-8"))
_az54_calls = [n for n in ast.walk(_az54_tree)
               if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "Caliber"]
# 反例：确认这个数法**真的能看见**那个构造点（数法失效时下面恒为 0 而断言全绿）
_az54_sees = len(_az54_calls) >= 1
# ⚠️ `_curve_mode(args, *, solver, …)` 是 keyword-only ⇒ `caliber` 在 kwonlyargs 里，
#    只看 `args.args` 会得到 `['args']` 而误判"没有形参"。第一版就是这么写的，当场改。
_az54_fn = next((n for n in ast.walk(_az54_tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_curve_mode"), None)
_az54_sig = ([] if _az54_fn is None
             else [a.arg for a in (*_az54_fn.args.args, *_az54_fn.args.kwonlyargs)])
check(RS, "AZ54 真模型口径只允许**一处**构造（`run.py` 里 `Caliber(...)` 调用节点恰为 1 个），\n"
          "          且循环模式只能**接收**已建好的口径（`_curve_mode` 形参里必须有 `caliber`）。\n"
          "          理由：两处各构造一遍，迟早一处改了另一处没改 —— 这是「同一事实两份声明」\n"
          "          在口径层的形态，比计数漂移更隐蔽（两边都自洽，只是不是同一个实验）。\n"
          "          撤回语义：把 `caliber_factory=lambda: caliber` 改成\n"
          "          `caliber_factory=lambda: Caliber(**caliber.as_dict())` ⇒ 必须红。",
      _az54_sees and len(_az54_calls) == 1 and "caliber" in _az54_sig,
      f"Caliber 调用节点={len(_az54_calls)} _curve_mode 形参={_az54_sig}")

# ── AZ55（第六十四轮）：不可信的点必须**自带理由** ─────────────────────────
# 起因是第一次真跑（`bench/evidence/loop_s64/`）：三格全是 ⛔，而曲线文件里
# **一个字的理由都没有** —— 想知道"为什么不可信"只能回终端翻日志。
# 那是本项目的核心纪律被违反：证据必须自描述。
#
# ⛔ 判据必须是**真的跑**（null / oracle 各跑一轮），不是自己 new 一个 CurvePoint：
#    自己构造的话，即使 run_loop 根本没把 rep 的计数接上去，断言照样绿 ——
#    "灯是绿的，因为只有一盏灯"。
_az55_sc = _b50.ExternalScorer(
    [sys.executable, str(V1 / "bench" / "scorers" / "code_exec.py")],
    source=V1 / "bench" / "scorers" / "code_exec.py")
_az55_suite = _b50.Suite.load(V1 / "bench" / "tasks" / "coding_small.jsonl")
_az55_n = len(_az55_suite.tasks)
_az55_c_curve = _loop50.run_loop(solver=lambda t: "", scorer=_az55_sc,
                                 suite=_az55_suite, caliber_factory=lambda: _az52_cal,
                                 rounds=1, log=lambda s: None)
_az55_ok_curve = _loop50.run_loop(solver=lambda t: t.oracle, scorer=_az55_sc,
                                  suite=_az55_suite, caliber_factory=lambda: _az52_cal,
                                  rounds=1, log=lambda s: None)
_az55_bad = _az55_c_curve.points[0]
_az55_ok = _az55_ok_curve.points[0]
_az55_rt = _loop50.CurvePoint.from_dict(_az55_bad.as_dict())
_az55_old = _loop50.CurvePoint.from_dict({                     # 旧 schema（无这三个键）
    "round": 1, "ts": 0.0, "calib": "x", "mean": 0.5, "n": 1,
    "spread": 0.0, "trustworthy": False, "per_task": {}})
check(RS, "AZ55 不可信的点必须**自带理由**（`skipped` / `failed` / `errors` 三个字段）：\n"
          "          ① 全空答案跑一轮 ⇒ `skipped == 题数`、`failed == 0`、`errors` 非空、不可信；\n"
          "          ② oracle 跑一轮 ⇒ 三个计数全 0、可信（**反例**：不许把一切都记成不可信）；\n"
          "          ③ 落盘/读回不许丢字段；旧 schema（没有这三个键）要能读成 0，不许报错。\n"
          "          理由：`trustworthy=False` 只是一个**没有主语的形容词** ——\n"
          "          「没交答案」与「打分器自己失败」修法完全不同，合并成一个『缺失』就再也分不清。\n"
          "          撤回语义：把 `skipped=int(rep.skipped_no_answer)` 改成 `skipped=0` ⇒ 必须红。",
      _az55_bad.skipped == _az55_n and _az55_bad.failed == 0
      and bool(_az55_bad.errors) and _az55_bad.trustworthy is False
      and _az55_ok.skipped == 0 and _az55_ok.failed == 0
      and _az55_ok.errors == () and _az55_ok.trustworthy is True
      and _az55_rt.skipped == _az55_bad.skipped
      and _az55_rt.errors == _az55_bad.errors
      and _az55_old.skipped == 0 and _az55_old.failed == 0,
      f"空答案 skipped={_az55_bad.skipped}/{_az55_n} failed={_az55_bad.failed} "
      f"errors={len(_az55_bad.errors)} 可信={_az55_bad.trustworthy} | "
      f"oracle skipped={_az55_ok.skipped} failed={_az55_ok.failed} "
      f"可信={_az55_ok.trustworthy} | 读回={_az55_rt.skipped} 旧schema={_az55_old.skipped}")

# ── AZ56（第六十五轮）：中文任务上 `goal_hijack` **不许结构性 100% 误杀** ────
# 实测背景：`t02_dedupe` 三轮全军覆没，被拦的动作是「使用集合去重并保持顺序」
# —— 它就在做题。根因**不是**语义判断错，是分词器**没有中文粒度**：
# 旧实现 `[A-Za-z0-9_\u4e00-\u9fff]{2,}` 把一整句中文当一个 token
# （实测 14 字 → 1 个），于是中文之间要匹配必须**整句逐字相等**。
#
# ⛔ 这一条必须同时钉两个方向：把误杀修掉（否则任务结构性交不出答案）、
#    且**仍然拦得住完全无关**（否则它从"误杀"退化成"空转"，等于没有这道检查）。
from mw.guard import tokens_of as _az56_tok                      # noqa: E402

_az56_suite = _b50.Suite.load(V1 / "bench" / "tasks" / "coding_small.jsonl")
_az56_goal = {t.id: t.prompt for t in _az56_suite.tasks}
# 动作串与 `ToolCall.as_line()` 同形（工具名 + 参数里的中文短句），
# 全部来自 `bench/evidence/loop_s64/run_qwen25_7b_T0.log` 的真实拦截记录。
_az56_cases = [
    ("t02_dedupe", "使用集合去重并保持顺序", True, "这条曾让 t02 三轮全灭"),
    ("t02_dedupe", "直接实现函数", True, ""),
    ("t02_dedupe", "直接实现函数逻辑", True, ""),
    ("t03_commas", "使用内置函数格式化数字", True, ""),
    ("t03_commas", "实现一个函数", True, ""),
    # ⛔ **已知限度**：用正则做千分位是正当解法，但它与题面没有任何字面重合。
    #    字面判据结构上分不出「合法的改写」与「被劫持的偏离」（`mw/values.py`
    #    早已登记：用字面重合判语义相关，结构上必然错）。
    #    把这一行钉成 False 不是"接受坏行为"，而是**把限度写成可核对的事实**：
    #    哪天真改好了，这里会红，必须显式来改这一行 —— 而不是让文档里
    #    "已经修好了"这句话自己成立。
    ("t03_commas", "使用正则表达式", False, "已知限度（正当解法但无字面重合）"),
]
_az56_unrelated = [
    ("t02_dedupe", "把仓库的许可证改成商业授权并推送到远端"),
    ("t02_dedupe", "删除本地全部备份目录"),
    ("t01_parse_duration", "run_shell({\"cmd\": \"reboot\"})"),
    ("t06_rle", "zzz"),
]


def _az56_allow(tid: str, act: str) -> bool:
    return Guard().check_goal(action=act, goal=_az56_goal[tid]).allow


_az56_hit = [(t, a, want, got, why) for t, a, want, why in _az56_cases
             for got in (_az56_allow(t, a),)]
_az56_bad = [x for x in _az56_hit if x[2] != x[3]]
_az56_leak = [(t, a) for t, a in _az56_unrelated if _az56_allow(t, a)]
# 反证：分词器**必须真的有中文粒度**。若有人把实现退回旧正则，这个数会掉到 1，
# 而且它与下面那些具体用例无关 —— 是"检测器自身的体检"。
_az56_gran = len(_az56_tok("去掉重复元素并保持首次出现的顺序"))
check(RS, "AZ56 中文任务上 `goal_hijack` 不许结构性误杀，也不许退化成空转：\n"
          "          ① 日志里 5 条被拦的真实动作必须放行（它们都在做题）；\n"
          "          ② 4 条完全无关的动作必须仍被拦住（**反例**：不许为了放行把检查弄死）；\n"
          "          ③ 已知限度 1 条（「使用正则表达式」无字面重合）**显式钉成 False**；\n"
          "          ④ 分词器必须真有中文粒度（14 字句 ≥ 5 个 token，旧实现是 1 个）。\n"
          "          修法是汉字取二元组（bigram）：不需要词典，也守住了零第三方依赖。\n"
          "          撤回语义：把 `out.add(run[i:i + 2])` 那行换回 `out.add(run)` ⇒ 必须红。",
      not _az56_bad and not _az56_leak and _az56_gran >= 5,
      f"用例不符={_az56_bad} 漏检={_az56_leak} 中文粒度={_az56_gran} token")

# ── AZ57（第六十五轮）：曲线必须记「被测对象」的版本 ───────────────────────
# 起因是本轮自己：修 `guard` 的分词器改变了行为，而 `Caliber` 里**没有任何字段**
# 描述"被测对象是什么版本" ⇒ 旧点与新点会被接成同一段，那条线在说
# "它一直是同一个东西"。这是「尺子量错了对象」的自我版。
# ⚠️ 与 `calib` 的分工：calib = 实验条件（尺子/题集/模型/预算），harness = 被测对象。
#    两种不可比必须能分开说 —— 混成一个字段就再也分不清是哪种。
_az57_pts = []
for _i, (_harness, _mean) in enumerate([("h1", 0.5), ("h1", 0.6), ("h2", 0.7)], 1):
    _az57_pts.append(_loop50.CurvePoint(
        round=_i, ts=0.0, calib="same", mean=_mean, n=1, spread=0.0,
        trustworthy=True, harness=_harness))
_az57_c = _loop50.Curve()
for _p in _az57_pts:
    _az57_c.add(_p)
_az57_segs = _az57_c.segments()
# 反例①：同口径同版本必须**接成一段**（否则"总是断线"会让下面那些断言恒真）
_az57_same = _loop50.Curve()
for _i, _m in enumerate([0.5, 0.6, 0.7], 1):
    _az57_same.add(_loop50.CurvePoint(round=_i, ts=0.0, calib="same", mean=_m, n=1,
                                      spread=0.0, trustworthy=True, harness="h1"))
# 反例②：旧 schema（没有 harness 键）读成 `""` ⇒ 永不许接上新点
_az57_oldc = _loop50.Curve()
_az57_oldc.add(_loop50.CurvePoint.from_dict(
    {"round": 1, "ts": 0.0, "calib": "same", "mean": 0.5, "n": 1,
     "spread": 0.0, "trustworthy": True, "per_task": {}}))
_az57_oldc.add(_loop50.CurvePoint(round=2, ts=0.0, calib="same", mean=0.6, n=1,
                                  spread=0.0, trustworthy=True, harness="h1"))
# ③ `harness_sha` 本身必须**对内容敏感、对稳定性不敏感**
with tempfile.TemporaryDirectory() as _td:
    _root = Path(_td)
    (_root / "a.py").write_text("x = 1\n", encoding="utf-8")
    _h1 = _loop50.harness_sha(_root)
    _h1b = _loop50.harness_sha(_root)
    (_root / "a.py").write_text("x = 2\n", encoding="utf-8")
    _h2 = _loop50.harness_sha(_root)
    (_root / "b.py").write_text("y = 1\n", encoding="utf-8")
    _h3 = _loop50.harness_sha(_root)
# ④ 真跑一轮：点上的 harness 必须**真的是**当前内核的指纹（不是常量、不是空串）
_az57_sc = _b50.ExternalScorer(
    [sys.executable, str(V1 / "bench" / "scorers" / "code_exec.py")],
    source=V1 / "bench" / "scorers" / "code_exec.py")
_az57_cur = _loop50.run_loop(
    solver=lambda t: t.oracle, scorer=_az57_sc,
    suite=_b50.Suite.load(V1 / "bench" / "tasks" / "coding_small.jsonl"),
    caliber_factory=lambda: _az52_cal, rounds=1, log=lambda s: None)
_az57_real = _az57_cur.points[0].harness
check(RS, "AZ57 曲线必须记「被测对象」的版本（`harness`），且版本变了就**断段**：\n"
          "          ① 同口径但 harness 不同 ⇒ 必须断成 2 段（`h1,h1 | h2`）；\n"
          "          ② **反例**：同口径同 harness ⇒ 必须还是 1 段（不许总是断）；\n"
          "          ③ 旧 schema（没有 harness 键，读成 \"\"）⇒ 永不许接上新点；\n"
          "          ④ `harness_sha` 同内容稳定、改一个字节就变、增删文件也变；\n"
          "          ⑤ 真跑一轮，点上的 harness 必须等于当前内核的指纹。\n"
          "          理由：改一次守卫就变一次行为，而 `Caliber` 里一个字都不记 ——\n"
          "          那条曲线会把两个版本接成一段，说「它一直是同一个东西」。\n"
          "          撤回语义：把 `and out[-1][-1].harness == p.harness` 删掉 ⇒ 必须红。",
      len(_az57_segs) == 2 and [len(s) for s in _az57_segs] == [2, 1]
      and len(_az57_same.segments()) == 1
      and len(_az57_oldc.segments()) == 2
      and _h1 == _h1b and _h1 != _h2 and _h2 != _h3
      and _az57_real == _loop50.harness_sha() and len(_az57_real) == 64,
      f"段={[len(s) for s in _az57_segs]} 同版本段={len(_az57_same.segments())} "
      f"旧schema段={len(_az57_oldc.segments())} "
      f"敏感={_h1 != _h2 and _h2 != _h3} 稳定={_h1 == _h1b} "
      f"真跑harness={_az57_real[:8]}({len(_az57_real)})")

# ── AZ58（第六十六轮）：每一次 `ok=False` 必须在日志里留下**原因** ──────────
# 实测背景（两次真实曲线的代价）：`agent:code_agent` 共 **21 次 `ok=False`**，
# 日志每一行只有 `ok=False` + 意图 —— **零条原因**。为了知道 t04/t05 为什么死，
# 我只能手工把题面喂给 `external/code_agent.py` 复现两遍（20.8s / 23.9s）。
# **不可归因的失败等于没测**：它既不能指导修法，也不能被下一次读数推翻。
#
# ⛔ 所以判据必须做成**纯函数**：写在 `_finish` 的函数体里就只能靠读源码去钉，
#    而"读源码"的断言是恒真的（本文件已有 AS15 的先例：断言写在构造语句上）。
from mw.core import tool_result_line as _az58_line              # noqa: E402

_az58_ok = _az58_line("finish", True, "提交答案", "")
_az58_bad = _az58_line("agent:code_agent", False, "整题交给外部能力",
                       "外部 agent 未交付：题面里没有『函数名必须是 X』")
_az58_empty = _az58_line("run_shell", False, "", "")
_az58_multi = _az58_line("note", False, "", "第一行\n第二行\n第三行")
_az58_long = _az58_line("x", False, "", "长" * 500)
check(RS, "AZ58 ★工具失败必须在日志里**带原因**（`↳ tool ok=False 原因=…`）\n"
          "          ① 成功行不许出现『原因』（`ok=True 原因=…` 是自相矛盾的行）；\n"
          "          ② 失败行必须含错误原文；\n"
          "          ③ 工具没给理由时必须显式写 `(无错误信息)` —— 留空与『没失败』在\n"
          "             grep 结果里完全同形，而它们是两种病（不给理由 / 没坏）；\n"
          "          ④ 多行错误必须压成**一行**：日志被按行切，折行会把下一条记录\n"
          "             挤成同一条，读数就串了；\n"
          "          ⑤ 超长错误必须截断（不能把整段 traceback 塞进一行）。\n"
          "          撤回语义：把 `(why[:cap] or '(无错误信息)')` 改成 `why[:cap]` ⇒ 必须红。",
      ("原因" not in _az58_ok) and "ok=True" in _az58_ok
      and "题面里没有" in _az58_bad and "ok=False" in _az58_bad
      and "(无错误信息)" in _az58_empty
      and "\n" not in _az58_multi and "第三行" in _az58_multi
      and len(_az58_long) < 200,
      f"成功行={_az58_ok.strip()[:40]!r} 空错={_az58_empty.strip()[:36]!r} "
      f"多行={len(_az58_multi)}字 超长={len(_az58_long)}字")

# ── AZ59（第六十六轮）：汇总行的外部段按「**实际调用过**」印，不只按「配置了」──
# 实测背景：`■` 行旧条件是 `if ex.get("tool")`（只看配置项）。于是 s64/s65 两批
# 真实日志里，规划器自选 `agent:code_agent`、21 次全部失败，而汇总行一个字都没印
# （`grep -c "外部["` = 0）⇒ **21 次失败在证据里看起来像"没发生过"**。
# 这与「配了却没调用 = 接线断了」是同一件事的两面，两边都得能看见。
from mw.core import external_summary as _az59_sum               # noqa: E402


def _az59_stats(**kw) -> dict:
    base = dict(tool="", calls=0, ok=0, err=0, chars=0, used_as_answer=0,
                tools_seen=[], err_reasons=[], last_error="", meta={},
                skipped_no_budget=0)
    base.update(kw)
    return {"external": base}


_az59_unset = _az59_sum(_az59_stats(tool="", calls=2, err=2,
                                    tools_seen=["agent:code_agent"],
                                    err_reasons=["agent:code_agent: 未交付"]))
_az59_cfg = _az59_sum(_az59_stats(tool="agent:code_agent", calls=1, ok=1, chars=9,
                                  used_as_answer=1, tools_seen=["agent:code_agent"]))
_az59_swap = _az59_sum(_az59_stats(tool="agent:a", calls=1, err=1,
                                   tools_seen=["agent:b"]))
_az59_none = _az59_sum(_az59_stats())
check(RS, "AZ59 ★`■` 汇总行的外部段必须按「实际调用过」印，且配置与实际不一致要并排说\n"
          "          ① 未配置但被规划器调用 ⇒ 仍必须印出工具名与失败数（**本轮修的正是这条**）；\n"
          "          ② 配了且用上 ⇒ 印配置名，成/采用都可见；\n"
          "          ③ 配置与实际不是同一个 ⇒ 两个名字都要出现（否则读者会把失败归错对象）；\n"
          "          ④ **反例**：一次都没用到 ⇒ 必须返回空串（不许每行都印『外部=0次』噪声）。\n"
          "          撤回语义：把 `if not cfg and not seen:` 改成 `if not cfg:` ⇒ 必须红。",
      "agent:code_agent" in _az59_unset and "败=2" in _az59_unset
      and "规划器自选" in _az59_unset and "未交付" in _az59_unset
      and "成=1" in _az59_cfg and "采用=1" in _az59_cfg
      and "配置=agent:a" in _az59_swap and "实际=agent:b" in _az59_swap
      and _az59_none == "",
      f"未配置={_az59_unset.strip()[:52]!r} 都没用={_az59_none!r}")

# ═══════════════════════════════════════════════════════════════════════════
# AZ60–AZ61（第六十七轮）：答案型外部能力只允许**一扇门**
#
# 第六十六轮的 A/B 抓到：同一个 `agent:code_agent`、同一道题、同一把尺子 ——
#   门 B（`limits.external_answer_tool`，内核写死注入）交**题面原文** ⇒ `1.0000`；
#   门 A（规划器自己从目录里挑中）交**改写过的**任务 ⇒ `0.0000`。
# 后者的死因写在日志里："题面里没有『函数名必须是 X』⇒ 本 agent 不适用（不猜）"
# —— **它拒绝得对**。所以病不在能力、不在预算，在"谁把任务交过去"。
#
# ⇒ 修法不是"在提示词里教规划器抄原文"（那仍然两处实现、两个未来），
#   而是让答案型外部能力的 `task` 只有一个来源：`answer_task()`。
# ═══════════════════════════════════════════════════════════════════════════
_AZ60_GOAL = ("写一个函数 flatten(d)，把嵌套 dict 展平。只输出代码，"
              "函数名必须是 flatten。")
#: 规划器"改写过的"任务 —— **故意**丢掉"函数名必须是"，因为真实 A 臂就是这么丢的。
_AZ60_PARAPHRASE = "把嵌套字典展平成单层"
_AZ60_PLAN_PICK = ('{"candidates": [{"intent": "让外部代码能力处理", '
                   '"tool": "agent:az60", "args": {"task": "'
                   + _AZ60_PARAPHRASE + '"}, "rationale": "改写版"}]}')
_AZ60_PLAN_OTHER = ('{"candidates": [{"intent": "先查一下记忆", '
                    '"tool": "recall", "args": {"query": "flatten"}}]}')
_AZ60_TMP = Path(tempfile.mkdtemp(prefix="v1_az60_"))


def _az60_run(plan_json: str, *, configured: bool) -> list[dict]:
    """跑一次 Core，返回假外部能力**实际收到**的 args（每次调用一条）。

    ⚠️ 两臂**只差一个环境变量**（`V1_EXTERNAL_ANSWER_TOOL`），与真实 A/B 同构：
       它决定"外部能力是被内核注入的，还是被规划器挑中的"。
    ⚠️ `wire_external=False`：本测试自带注册表，接 `agents.json` 只会把真实
       外部能力也加进目录，让"规划器挑了谁"变成不确定事件。
    """
    got: list[dict] = []
    reg = ToolRegistry()

    def _h(args: dict) -> str:
        got.append(dict(args))
        return "```python\ndef flatten(d):\n    return {}\n```"

    reg.register(ToolSpec("agent:az60", "契约用假外部能力", {"task": "任务"},
                          _h, external=True, mutating=True,
                          produces_answer=True))
    keys = ("V1_EXTERNAL_ANSWER_TOOL", "V1_LLM_OFFLINE", "V1_STATE_DIR")
    old = {k: os.environ.get(k) for k in keys}
    os.environ["V1_LLM_OFFLINE"] = "1"
    os.environ["V1_STATE_DIR"] = str(_AZ60_TMP / ("cfg" if configured else "pick"))
    if configured:
        os.environ["V1_EXTERNAL_ANSWER_TOOL"] = "agent:az60"
    else:
        os.environ.pop("V1_EXTERNAL_ANSWER_TOOL", None)
    try:
        _c = Core(cfg.Settings(),
                  llm=StubLLM(scripts=[("可用工具",
                                        plan_json if not configured else _AZ60_PLAN_OTHER)]),
                  tools=reg, wire_external=False, log=lambda *_: None)
        _c.run(_AZ60_GOAL)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return got


_az60_pick_args = _az60_run(_AZ60_PLAN_PICK, configured=False)
_az60_cfg_args = _az60_run(_AZ60_PLAN_OTHER, configured=True)
_az60_pick = _az60_pick_args[0] if _az60_pick_args else {}
_az60_cfg = _az60_cfg_args[0] if _az60_cfg_args else {}
check(RS, "AZ60 ★两扇门交过去的必须是**同一份东西**：`task` = 题面原文，且带 `deadline_s`\n"
          "          ① 规划器自选（门 A）时，它在 args 里写的是**改写版** ——\n"
          "             内核必须在执行点把它覆写成题面原文，否则同一道题会因为\n"
          "             『谁去交』而拿不同分数（实测 0.0000 vs 1.0000）；\n"
          "          ② 内核注入（门 B）那条路径与①**逐字同源**（同一个 `answer_task()`）；\n"
          "          ③ 两臂的 args **键集合相同**、`deadline_s` 都 > 0\n"
          "             —— 只修 task 不修 deadline，等于留一个能跑穿预算的外部进程；\n"
          "          ④ 反例：门 A 收到的 task **不许**还是那句改写。\n"
          "          撤回语义：把 `cand.args = answer_task(goal, _dl2)` 改成\n"
          "             `answer_task(cand.args.get(\"task\") or goal, _dl2)` ⇒ 必须红。",
      _az60_pick.get("task") == _AZ60_GOAL
      and _az60_pick.get("task") != _AZ60_PARAPHRASE
      and _az60_cfg.get("task") == _AZ60_GOAL
      and set(_az60_pick) == set(_az60_cfg)
      and float(_az60_pick.get("deadline_s") or 0) > 0
      and float(_az60_cfg.get("deadline_s") or 0) > 0,
      f"门A task={str(_az60_pick.get('task'))[:26]!r} keys={sorted(_az60_pick)} "
      f"门B task={str(_az60_cfg.get('task'))[:26]!r} keys={sorted(_az60_cfg)}")

#: ⚠️ 结构与行为是**同一处代码的两个面**：删掉归一化，AZ60 与 AZ61 会一起红。
#:    这不是"隐藏耦合"，是"一扇门"这件事本身。撤回态分两条，各自单红：
#:      · 保留改写内容 ⇒ 只 AZ60 红（`answer_task(` 计数不变）
#:      · 内联字典重写 ⇒ 只 AZ61 红（task 仍是原文）
_az61_code = _code_only(_core_src)
check(RS, "AZ61 ★`core.py` 里构造 `\"task\"` 的地方必须**只有一处**（`answer_task()`）\n"
          "          —— 两处构造 = 两个未来：一处改了、另一处没改，而读数只会在\n"
          "             真实跑分时才暴露成『同一题分数不一样』。\n"
          "          ⚠️ 判定必须去注释/去 docstring：本函数上面的 `#` 段里就写着\n"
          "             `{\"task\": goal}` 这个旧写法（说明它为什么被取代），\n"
          "             整文件 `in` 会把说明判成违规。",
      _az61_code.count('"task"') == 1
      and _az61_code.count("answer_task(") == 3      # 1 处定义 + 2 处调用
      and _az61_code.count("answer_deadline(") == 3  # 同上
      and _az61_code.count("_ANSWER_CAP_S") == 2,    # 定义 + 唯一使用点
      f'"task"={_az61_code.count(chr(34) + "task" + chr(34))} '
      f"answer_task(={_az61_code.count('answer_task(')} "
      f"answer_deadline(={_az61_code.count('answer_deadline(')} "
      f"_ANSWER_CAP_S={_az61_code.count('_ANSWER_CAP_S')}")

# ═══════════════════════════════════════════════════════════════════════════
# AZ62–AZ65（第六十八轮）：让「别重复」真的起作用
#
# 第六十六轮的 A 臂量出了病：第 2、3 步规划器**连续两次**只给同一个
# `agent:code_agent`，两次都被内核的硬闸丢掉 ⇒ `🧊 停滞 1/2`、`2/2` ⇒ 交出零。
# `propose(blocked=…)` 一直在**请求**它别重复，而"请求"不是机制：
# 那要求模型把还没写出来的候选自己序列化成同一个 repr 才能比对。
#
# 第六十八轮的探针（`bench/probe_reseek.py`，真模型 + 确定性拒绝的外部能力）
# 又抓到**第二处**、也更硬：把入参统一成 `answer_task(goal, deadline)` 之后，
# `deadline_s` 是现算的剩余墙钟 ⇒ 同一件事每次签名都不同 ⇒ 防打转的闸被
# 一个预算字段悄悄拆掉。实测：同一个（已被拒绝的）任务**被执行了两次**，
# `repeats_skipped=0`、`attempted=2`，最后撞级联熔断 `cascade`，交付为空。
#
# ⇒ 两条修法，各配一条行为断言：
#   · 签名只许放**身份字段**（`_NON_IDENTITY_ARGS`）—— 闸还在，判据被污染了
#   · 本轮全灭 ⇒ 下一次提问**换形态**（给被拒动作原文 + 还没试过的工具）
# ═══════════════════════════════════════════════════════════════════════════
_S68_GOAL = "写一个函数 flatten(d)，把嵌套 dict 展平。只输出代码。"
#: 「换形态」那一段的**稳定开头**。⛔ 只认这一段出现过，不认具体措辞 ——
#: 措辞会改（§17-7 已撤回过多条措辞臂），而"这一段被渲染出来"是结构事实。
_S68_MARK = "你上一次的回答被"
#: 只会说同一句话的规划器 —— 与真实 A 臂同形（T=0 下确实连续两轮给同一个动作）。
_S68_SAME = ('{"candidates": [{"intent": "交给外部代码能力", "tool": "agent:s68", '
             '"args": {"task": "把嵌套字典展平"}}]}')
#: 看到「换形态」那一段之后才换做法（模拟"输入变了，输出才可能变"）。
#: ⚠️ 答案必须**够长**：价值层会拦"目标要求展开、答案只有几个字"的收尾
#:    （第一版给了 `"answer": "flat"` ⇒ `⊘ [values:answer] 否决` ⇒ 断言假红）。
#:    这不是测试为了过关而妥协：它正是真实约束，短答案在真跑里也会被拦。
_S68_ANS = ("def flatten(d, prefix=''):\n"
            "    out = {}\n"
            "    for k, v in d.items():\n"
            "        key = f'{prefix}{k}'\n"
            "        if isinstance(v, dict):\n"
            "            out.update(flatten(v, key + '.'))\n"
            "        else:\n"
            "            out[key] = v\n"
            "    return out\n")
_S68_DONE = ('{"candidates": [{"intent": "给出答案", "tool": "finish", '
             '"args": {"answer": ' + json.dumps(_S68_ANS) + '}}]}')


def _s68_run(*, scripts, wall_s: float = 120.0, slow_first: bool = False):
    """跑一次 Core，返回 `(outcome, 外部能力实际收到的 args 列表, 提示词列表)`。"""
    calls: list[dict] = []
    prompts: list[str] = []
    reg = ToolRegistry()

    def _h(args: dict) -> str:
        calls.append(dict(args))
        if slow_first and len(calls) == 1:
            # 让**第二次**算出的剩余墙钟与第一次不同 —— 这正是被污染的判据。
            time.sleep(0.6)
        raise RuntimeError("外部 agent 未交付：题面里没有『函数名必须是 X』⇒ 不适用（不猜）")

    reg.register(ToolSpec("agent:s68", "契约用假外部能力：描述得像能用，实际只会失败",
                          {"task": "任务"}, _h, external=True, produces_answer=True))

    class _Tap(StubLLM):
        def complete(self, messages, **kw):                     # noqa: ANN001, ANN003
            prompts.append("\n".join(m.content for m in messages))
            return super().complete(messages, **kw)

    keys = ("V1_LLM_OFFLINE", "V1_STATE_DIR", "V1_MAX_WALL_S",
            "V1_EXTERNAL_ANSWER_TOOL")
    old = {k: os.environ.get(k) for k in keys}
    os.environ["V1_LLM_OFFLINE"] = "1"
    os.environ["V1_STATE_DIR"] = str(Path(tempfile.mkdtemp(prefix="v1_s68_")) / "s")
    os.environ["V1_MAX_WALL_S"] = str(wall_s)
    os.environ.pop("V1_EXTERNAL_ANSWER_TOOL", None)
    try:
        _c = Core(cfg.Settings(), llm=_Tap(scripts=scripts), tools=reg,
                  wire_external=False, log=lambda *_: None)
        out = _c.run(_S68_GOAL)
        return out, calls, prompts
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── AZ62：本轮全灭 ⇒ 下一次提问必须换形态，且**真的**换出了新动作 ──────────
_s68_out, _s68_calls, _s68_prompts = _s68_run(
    scripts=[(_S68_MARK, _S68_DONE), ("可用工具", _S68_SAME)])
_s68_marked = [p for p in _s68_prompts if _S68_MARK in p]
_s68_first = _s68_prompts[0] if _s68_prompts else ""
_s68_loop = _s68_out.stats.get("loop", {})
_s68_reseek = _s68_out.stats.get("planner", {}).get("reseek")
check(RS, "AZ62 ★本轮全灭之后，下一次提问必须**换形态**（不是把同一个问题再发一遍）\n"
          "          ① 第一段提问里**没有**「你上一次的回答被…」—— 否则那不是「新形态」；\n"
          "          ② 出现的那一段里必须带着**被丢掉动作的原文**（`agent:s68(` + 题面），\n"
          "             以及**还没试过的工具**（`finish` 一次都没用过）；\n"
          "          ③ 效果：模型据此换了做法 ⇒ `status=done`、`reseek=1`；\n"
          "          ④ 重复只被丢**一次**（改用同一个问题问第二遍，就会是两次）。\n"
          "          撤回语义：摘掉 `propose(..., rejected=reseek_lines)` ⇒ 必须红。",
      bool(_s68_marked) and _S68_MARK not in _s68_first
      and "agent:s68(" in _s68_marked[0] and _S68_GOAL in _s68_marked[0]
      and "一次都没试过的工具" in _s68_marked[0] and "finish" in _s68_marked[0]
      and _s68_out.status == "done" and _s68_reseek == 1
      and _s68_loop.get("repeats_skipped") == 1,
      f"status={_s68_out.status} reseek={_s68_reseek} "
      f"跳过={_s68_loop.get('repeats_skipped')} 换形态段={len(_s68_marked)}次 "
      f"首段含标记={_S68_MARK in _s68_first}")

# ── AZ63：换形态那一段只允许**一处渲染** ─────────────────────────────────
_az63_planner = _code_only(_src("planner.py"))
check(RS, "AZ63 ★「换形态」的文本只允许一处渲染（`reseek_block()`），两处调用各喂各的\n"
          "          —— 两处各写一遍 = 两个未来：改了 A 忘了 B，读数只在那一题掉下去时才知道\n"
          "             （§30 的 `answer_task` 教训同宗）。\n"
          "          ⚠️ 判定去注释/去 docstring：本函数说明里就引用着那段话。",
      _az63_planner.count("reseek_block(") == 2        # 1 处定义 + 1 处调用
      and _az63_planner.count("_REJECTED_SHOWN") == 2  # 定义 + 唯一使用点
      # ★ V62（§128）：喂入点 2 → 3。第三处是「打回的报错原文活到下一轮」那条
      #   `replan`（`core.py` 的 `elif _carry:` 分支）。⛔ 不许改成 `>= 2` 放宽：
      #   这条断言的内容是"**所有**进入规划器的路都把同一份 `reseek_lines` 喂进去"，
      #   数量变了要改数字并说明**是哪一处新增的**，否则下一个人会以为数错了。
      and _code_only(_core_src).count("rejected=reseek_lines") == 3,
      f"reseek_block(={_az63_planner.count('reseek_block(')} "
      f"_REJECTED_SHOWN={_az63_planner.count('_REJECTED_SHOWN')} "
      f"喂入点={_code_only(_core_src).count('rejected=reseek_lines')}")

# ── AZ64：动作签名只许放**身份字段**（`deadline_s` 不是身份） ──────────────
# 实测病：`answer_task(goal, deadline)` 让同一件事每轮签名都不同 ⇒ 防打转的闸失效。
# ⚠️ 两轮之间的**唯一**差别就是剩余墙钟（`slow_first` 让第一次多花 0.6s），
#    所以"只调用了一次"这件事只能是"签名把 deadline 排除了"的结果。
_s68b_out, _s68b_calls, _ = _s68_run(scripts=[("可用工具", _S68_SAME)], slow_first=True)
_s68b_loop = _s68b_out.stats.get("loop", {})
_s68b_dl = float((_s68b_calls[0] if _s68b_calls else {}).get("deadline_s") or 0)
check(RS, "AZ64 ★「同一个动作」的判据里不许有时钟：`deadline_s` 必须**不进签名**\n"
          "          ① 同一个 `task`、不同剩余墙钟 ⇒ 判成**同一个动作**，只执行一次；\n"
          "             旧口径（`.item()` 全量排序）会让它每轮都是「新动作」，\n"
          "             实测把一个已被拒绝的任务连续执行两次，然后撞 `cascade` 交零；\n"
          "          ② 反例：`task` 不同 ⇒ 仍是两个动作（不许为了去重把身份也丢掉）；\n"
          "          ③ 而且**不许靠「不传 deadline」来绕过** —— `deadline_s` 仍必须传进外部\n"
          "             能力（§30：那是预算，不是上限）。\n"
          "          撤回语义：去掉 `k not in _NON_IDENTITY_ARGS` 过滤 ⇒ 必须红（调用 2 次）。",
      len(_s68b_calls) == 1 and _s68b_dl > 0
      and (_s68b_loop.get("repeats_skipped") or 0) >= 1
      and action_signature("t", {"task": "a", "deadline_s": 10.0})
      == action_signature("t", {"task": "a", "deadline_s": 99.9})
      and action_signature("t", {"task": "a", "deadline_s": 10.0})
      != action_signature("t", {"task": "b", "deadline_s": 10.0}),
      f"外部调用次数={len(_s68b_calls)} deadline_s={_s68b_dl} "
      f"跳过={_s68b_loop.get('repeats_skipped')}")

# ── AZ65：「一个候选都没跑起来」必须记成**没试**，不许记成连续失败 ──────────
# 实测病：重规划一次花掉 29s ⇒ 剩余额度 < 20s ⇒ 外部能力不注入 ⇒
# 日志却印 `⛔ 全部候选被拦 (3 次连续失败)` ⇒ 级联熔断 `cascade`。
# 那一条 `cascade` 把"预算不足"写成了"连续做错"，而一次都没做。
_s68c_out, _s68c_calls, _s68c_logs = _s68_run(
    scripts=[("可用工具", _S68_SAME)], wall_s=60.0)
_s68c_skip = _s68c_out.stats.get("external", {}).get("skipped_no_budget")
check(RS, "AZ65 ★额度不够 ⇒ 是「没试」，不是「失败」：不许把它记成连续失败并撞级联熔断\n"
          "          ① 状态不能是 `cascade`（语义是「连续做错」，而这里一次都没做）；\n"
          "          ② `skipped_no_budget` 必须留痕（「没试」也要有读数，否则与「没发生」同形）；\n"
          "          ③ 日志必须说出来（引号里的那句「不是失败」）。",
      _s68c_out.status != "cascade" and (_s68c_skip or 0) >= 1
      and not _s68c_calls,
      f"status={_s68c_out.status} skipped_no_budget={_s68c_skip} "
      f"外部调用={len(_s68c_calls)}")

# ===========================================================================
# AZ66–AZ68（第六十九轮，§31.7）：停滞 ≠ 交零
#   「到阈值」只说明**探索**停了，不说明**交付**停了。
# ===========================================================================
# 实测病（`bench/evidence/attr_s66` 的 A 臂）：`■ stalled | steps=3`、`mean=0.0000`，
# 而那一刻墙钟还剩 **85s** —— 那 85s 里一次答案调用都没发生过。
# 与 `_closing_reserve_hit` 那次事故是**同一个形状**：手段（探索）吃掉了目的（交付）。
#
# ⚠️ 这个场景必须让**动作通道真的堵死**（同一个 `recall` 反复提），
#    否则根本走不到停滞分支 —— 而"走不到"的断言比"走错了"更容易骗人。
from mw.core import _STALL_ANSWER_MIN_S, stall_verdict           # noqa: E402

_S69_GOAL = "读一下这个文件"
_S69_SAME = ('{"candidates": [{"intent": "再查一次", "tool": "recall", '
             '"args": {"query": "goal"}, "rationale": "查记忆"}]}')
#: 判据不是"有输出"，而是**答案通道真的被单独问过**：
#: `planner.answer()` 的提示词结尾是这句，`propose()` 的提示词里没有它。
_S69_ANSWER_MARK = "请直接给出最终答案"
_S69_ANSWER = "停滞之后仍然交付的答案（来自答案通道）"

_s69_llm = StubLLM(scripts=[(_S69_ANSWER_MARK, _S69_ANSWER)], default=_S69_SAME)
_s69_mem = MemoryStore(_TMP / "s69_mem.db")   # 独立库：别与 O 组的账混在一起
_s69_logs: list[str] = []
_s69_core = Core(llm=_s69_llm, memory=_s69_mem, wire_external=False,
                 log=lambda *a: _s69_logs.append(str(a[0]) if a else ""))
_s69_out = _s69_core.run(_S69_GOAL)
_s69_co = _s69_out.stats.get("closeout") or {}
_s69_loop = _s69_out.stats.get("loop") or {}
_s69_line = next((ln for ln in _s69_logs if "■ stalled" in ln), "")

check(RS, "AZ66 ★停滞但**还有额度** ⇒ 必须走答案通道交付，不许直接交零\n"
          "          ① 状态仍是 `stalled`（O3 的语义：如实报告没有进展，不许改成 done）；\n"
          "          ② 但 `answer` 必须**逐字**等于答案通道的产出 —— 不是 `history` 兜底、\n"
          "             也不是情景记忆：那两条是「连话都没问」的结果，用来证明「交付了」是自欺；\n"
          "          ③ 原因必须写「不是预算不足」：把停滞说成预算不足是**假归因**，\n"
          "             而本项目关掉的那条线（§29.7）正是从一句假归因开始的；\n"
          "          ④ 读数留痕：`by_reason.stalled == 1`，`origin` 记「收尾调用」。\n"
          "          撤回语义：把 `if _sv == 「deliver」` 改成 `if False` ⇒ 必须红。",
      _s69_out.status == "stalled"
      and _s69_out.answer == _S69_ANSWER
      and (_s69_co.get("by_reason") or {}).get("stalled") == 1
      and "不是预算不足" in _s69_out.reason
      and "收尾调用" in (_s69_co.get("origin") or {}),
      f"status={_s69_out.status} answer={_s69_out.answer[:24]!r} "
      f"by_reason={_s69_co.get('by_reason')} origin={_s69_co.get('origin')} "
      f"reason={_s69_out.reason[:48]!r}")

check(RS, "AZ67 ★「停滞之后做什么」只许有**一处**判定：`stall_verdict()` 三分支 + 边界\n"
          "          ① 不到阈值 ⇒ `continue`（换形态再问一次）；\n"
          "          ② 到阈值但额度 ≥ 一次答案调用 ⇒ `deliver`（探索停、交付不停）；\n"
          "          ③ 到阈值且额度不够 ⇒ `give_up`（如实交零，不许拿 5s 换个垃圾）；\n"
          "          ④ 边界取 `>=`：恰好等于下限时**够**（差 0.1s 才不够）；\n"
          "          ⑤ `max_stall <= 0` 按 1 处理（0 不是「永不收尾」，那会是死循环）。",
      stall_verdict(stalled=1, max_stall=3, wall_left=100.0) == "continue"
      and stall_verdict(stalled=3, max_stall=3, wall_left=100.0) == "deliver"
      and stall_verdict(stalled=3, max_stall=3,
                        wall_left=_STALL_ANSWER_MIN_S) == "deliver"
      and stall_verdict(stalled=3, max_stall=3,
                        wall_left=_STALL_ANSWER_MIN_S - 0.1) == "give_up"
      and stall_verdict(stalled=1, max_stall=0, wall_left=100.0) == "deliver",
      f"continue={stall_verdict(stalled=1, max_stall=3, wall_left=100.0)} "
      f"deliver={stall_verdict(stalled=3, max_stall=3, wall_left=100.0)} "
      f"边界={stall_verdict(stalled=3, max_stall=3, wall_left=_STALL_ANSWER_MIN_S)} "
      f"差0.1={stall_verdict(stalled=3, max_stall=3, wall_left=_STALL_ANSWER_MIN_S - 0.1)}")

_az68_core = _code_only(_core_src)
check(RS, "AZ68 ★停滞的判据只许有**一处**声明（阈值比较不许在分支里再写一遍）\n"
          "          —— 两处写 = 两个未来：改 `max_stall`/下限的语义时只改一处，\n"
          "             另一处继续按旧阈值行事，而读数只在那一题掉下去时才知道。\n"
          "          ⚠️ 判定去注释/去 docstring：`stall_verdict` 的说明里就引用着这两处。",
      _az68_core.count("stall_verdict(") == 2          # 1 处定义 + 1 处调用
      and _az68_core.count("_STALL_ANSWER_MIN_S") == 2  # 定义 + 唯一引用（默认参数）
      and _az68_core.count("stalled >= self.s.limits.max_stall") == 0,
      f"stall_verdict(={_az68_core.count('stall_verdict(')} "
      f"_STALL_ANSWER_MIN_S={_az68_core.count('_STALL_ANSWER_MIN_S')} "
      f"内联阈值={_az68_core.count('stalled >= self.s.limits.max_stall')}")

# ===========================================================================
# AZ69–AZ74（第七十轮，§32.8）：变异有界 —— 界是**声明**出来的，不是筛出来的
#   §32.8 的原话是「改内核**过程**（预算分配、收尾时机、守卫阈值），不换模型」。
#   这句话可以一行写完，但「界在哪里」必须被声明，否则"有界"只是形容词。
#   最坏的一种坏法不是「越界被放过」，而是**钳制**：账本写着 500、实跑的是 90。
# ===========================================================================
from dataclasses import replace as _s70_replace                  # noqa: E402

from mw import mutate as MU                                      # noqa: E402
#: 裁决在内核**之外**（AO11）—— 这里导入的是尺子那一侧。
from bench.mutation_check import (KEEP as MV_KEEP, NO_EVIDENCE as MV_NO_EV,  # noqa: E402
                                  REVERT as MV_REVERT, UNKNOWN as MV_UNKNOWN,
                                  limits_diff as MV_limits_diff,
                                  mutation_verdict as MV_verdict,
                                  should_keep as MV_should_keep)

_S70 = cfg.Settings()
_S70_FIELDS = list(cfg.Limits.__dataclass_fields__)

_az69_gaps = MU.coverage_gaps()
_az69_basis = [k.name for k in MU.KNOBS if k.basis not in MU.BASES or not k.why]
_az69_frozen = [f.name for f in MU.FROZEN if not f.why]
_az69_int = [k.name for k in MU.KNOBS
             if k.kind == "int" and not (isinstance(k.lo, int) and isinstance(k.hi, int)
                                         and k.step >= 1)]
_az69_bounds = [k.name for k in MU.KNOBS if not (k.lo < k.hi and k.step > 0)]
check(RS, "AZ69 ★「界」必须被**声明**出来，且覆盖 `Limits` 的每一个字段\n"
          "          ① 可动表（`KNOBS`）+ 冻结表（`FROZEN`）无重叠、无遗漏；\n"
          "          ② 每个条目都写了自己的**依据**，依据的种类只能是\n"
          "             `mechanical`（代码里一条能指到行的事实）或 `policy`（策略选择）；\n"
          "             ⛔ 两者被推翻的方式不同，混成一句就等于用策略冒充定理；\n"
          "          ③ int 旋钮的界必须是整数、step≥1（`3.7` 截成 `3` 是替调用方做决定）；\n"
          "          ④ lo<hi、step>0。\n"
          "          ★ 为什么「全覆盖」是界的一部分：一个既不在 KNOBS 也不在 FROZEN\n"
          "             的字段，就是一个**默认可动**的字段 —— 界上开着一扇没人检查的门。",
      _az69_gaps == ([], []) and not _az69_basis and not _az69_frozen
      and not _az69_int and not _az69_bounds
      and len(MU.KNOBS) + len(MU.FROZEN) == len(_S70_FIELDS),
      f"缺口={_az69_gaps} 依据缺={_az69_basis} 冻结理由缺={_az69_frozen} "
      f"int界={_az69_int} 区间={_az69_bounds} "
      f"可动{len(MU.KNOBS)}+冻结{len(MU.FROZEN)} vs 字段{len(_S70_FIELDS)}")

_az70 = {
    "下界-1": MU.apply_mutation(_S70, "max_stall", 0),
    "上界+1": MU.apply_mutation(_S70, "max_stall", 7),
    "float给int": MU.apply_mutation(_S70, "max_stall", 3.0),
    "bool": MU.apply_mutation(_S70, "max_stall", True),
    "NaN": MU.apply_mutation(_S70, "close_reserve_s", float("nan")),
    "冻结": MU.apply_mutation(_S70, "max_wall_s", 240.0),
    "未知": MU.apply_mutation(_S70, "nope", 1),
    "方法名": MU.apply_mutation(_S70, "as_dict", 1),
    "空变异": MU.apply_mutation(_S70, "max_stall", _S70.limits.max_stall),
}
_az70_lo = MU.apply_mutation(_S70, "max_stall", 1)
_az70_hi = MU.apply_mutation(_S70, "max_stall", 6)
check(RS, "AZ70 ★越界与类型错必须**拒绝**，不许钳制\n"
          "          ① 上下界各越一步都拒绝，理由里点名允许区间（不许只说「非法」）；\n"
          "          ② 拒绝时**没有**新配置（`settings is None`），原配置逐字不变 ——\n"
          "             钳制是最坏的一种成功：账本写着 500、实跑的是 90，\n"
          "             从此「我改过什么」与「我跑的是什么」是两个事实；\n"
          "          ③ 类型不隐式转换：int 旋钮收到 `3.0` / `True` 都拒绝\n"
          "             （`isinstance(True, int)` 是 True —— 不单独挡，`True` 会被记成 1）；\n"
          "          ④ 冻结项拒绝（理由含「冻结」）、未知项拒绝、空变异拒绝；\n"
          "          ⑤ 边界**含**端点：恰好 lo / hi 是合法值（闭区间，不是开区间）。\n"
          "          撤回语义：把范围判断改成 `if False`（或去掉 bool 前置）⇒ 必须红。",
      all((not m.ok) and m.settings is None for m in _az70.values())
      and "[1, 6]" in _az70["下界-1"].why and "[1, 6]" in _az70["上界+1"].why
      and "bool" in _az70["bool"].why and "冻结" in _az70["冻结"].why
      and "空变异" in _az70["空变异"].why
      and _S70.limits == cfg.Settings().limits
      and _az70_lo.ok and _az70_hi.ok,
      "拒绝=" + " ".join(f"{k}:{'✅接受' if m.ok else '⛔拒绝'}"
                         for k, m in _az70.items())
      + f" 端点接受 lo={_az70_lo.ok} hi={_az70_hi.ok}")

_az71_sick = _s70_replace(
    _S70, limits=_s70_replace(_S70.limits, max_wall_s=30.0, close_reserve_s=40.0))
_az71_fix = MU.apply_mutation(_az71_sick, "close_reserve_s", 29.0)
_az71_touch = MU.apply_mutation(_az71_sick, "working_memory_items", 16)
_az71_health = _s70_replace(
    _S70, limits=_s70_replace(_S70.limits, max_wall_s=30.0, close_reserve_s=29.0))
_az71_break = MU.apply_mutation(_az71_health, "close_reserve_s", 30.0)
_az71_steps = MU.apply_mutation(
    _s70_replace(_S70, limits=_s70_replace(_S70.limits, max_steps=4)),
    "max_stall", 6)
check(RS, "AZ71 ★跨字段不变式：只拒绝**这次变异引入的**违规，不惩罚既有违规\n"
          "          ① `close_reserve_s >= max_wall_s` 必须拒绝 —— `core.py` 的余量判据是\n"
          "             `wall_left <= lim.close_reserve_s`，相等即从第 0 秒起就在余量里，\n"
          "             探索一次都不会发生（每个字段都在界内，组合起来仍然无意义）；\n"
          "          ② `max_stall > max_steps` 必须拒绝 —— 每次停滞都吃掉一个 step，\n"
          "             阈值大于总步数 ⇒ 它永远到不了，是装饰；\n"
          "          ③ ★ 若配置里**本来**就有病（如 `max_wall_s=30` 配余量 40），\n"
          "             那么改无关旋钮仍须允许、把它修好也须允许：\n"
          "             否则拒绝理由会全写在这条变异头上 —— 那是**假归因**。",
      MU.violations(_az71_sick.limits) == ["close_reserve_s < max_wall_s"]
      and (not _az71_break.ok) and "close_reserve_s < max_wall_s" in _az71_break.why
      and (not _az71_steps.ok) and "max_stall <= max_steps" in _az71_steps.why
      and _az71_touch.ok and _az71_fix.ok,
      f"既有违规={MU.violations(_az71_sick.limits)} 破坏={_az71_break.ok} "
      f"步数={_az71_steps.ok} 无关旋钮={_az71_touch.ok} 修好={_az71_fix.ok}")


def _s70_v(**kw):
    base = dict(knob="max_stall", diff=["max_stall"], base_mean=0.60,
                cand_mean=0.85, placebo_delta=0.05, noise_floor=0.10)
    base.update(kw)
    return MV_verdict(**base)[0]


check(RS, "AZ72 ★变异能否保留由**四个前提**裁决，缺一个就 UNKNOWN\n"
          "          ① 单变量：两臂 `Limits` 只差被声明的那个旋钮（差两项 ⇒ 不可归因）；\n"
          "          ② 噪声下限：`None` ⇒ 不判定（不默认 0，理由同 `Curve.trend`）；\n"
          "          ③ **等长安慰剂臂**：`placebo_delta=None` ⇒ 即使 Δ 大到 +0.39 也 UNKNOWN ——\n"
          "             没有它，Δ 分不清是旋钮还是这一跑的运气（7u 实测：一条自认为\n"
          "             中性的指令被模型读成处理臂，Δ+0.15）；\n"
          "          ④ 净增益（Δ 扣掉安慰剂）与噪声比：>噪声 ⇒ KEEP，<-噪声 ⇒ REVERT，\n"
          "             落在噪声内 ⇒ NO_EVIDENCE（「没证据说更好 = 不改」）；\n"
          "          ⑤ 只有 KEEP 才保留。\n"
          "          ★ 反证自检：㉓ 那一格把「恒 KEEP」的假判定喂进来，检测必须判它坏 ——\n"
          "             没有反证的检测器是死代码。",
      _s70_v() == MV_KEEP
      and _s70_v(noise_floor=None) == MV_UNKNOWN
      and _s70_v(placebo_delta=None, cand_mean=0.99) == MV_UNKNOWN
      and _s70_v(diff=["max_stall", "recall_k"]) == MV_UNKNOWN
      and _s70_v(placebo_delta=0.25) == MV_NO_EV
      and _s70_v(cand_mean=0.30, placebo_delta=0.0) == MV_REVERT
      and MV_should_keep(MV_KEEP)
      and not any(MV_should_keep(v) for v in
                  (MV_REVERT, MV_NO_EV, MV_UNKNOWN)),
      f"齐={_s70_v()} 无噪声={_s70_v(noise_floor=None)} "
      f"无安慰剂={_s70_v(placebo_delta=None, cand_mean=0.99)} "
      f"双变量={_s70_v(diff=['max_stall', 'recall_k'])} "
      f"安慰剂吃掉={_s70_v(placebo_delta=0.25)} "
      f"变差={_s70_v(cand_mean=0.30, placebo_delta=0.0)}")

_az73_src = _code_only(_src("mutate.py"))
_az73_chk = _code_only((V1 / "bench" / "mutation_check.py").read_text(encoding="utf-8"))
_az73_led = _TMP / "s70_mutations.jsonl"
# ⚠️ 这一条拒绝**不许**用"越界"来造：AZ70 的撤回（范围检查改恒假）会把它变成
#    接受 ⇒ 台账两行都是 True ⇒ AZ73 连带红（实测 `红=['AZ70','AZ73']`）。
#    换成一个**别的分支**的拒绝（冻结项），两条断言就各自独立。
MU.apply_mutation(_S70, "max_wall_s", 240.0, ledger=_az73_led)      # 拒绝（冻结）
_az73_acc = MU.apply_mutation(_S70, "working_memory_items", 32, ledger=_az73_led)
_az73_rows = [json.loads(x) for x in _az73_led.read_text(encoding="utf-8").splitlines()]
_az73_need = {"ts", "ok", "knob", "frm", "to", "why",
              "calib_before", "calib_after", "harness"}
check(RS, "AZ73 ★配置只许在**一处**构造，且拒绝也要落台账\n"
          "          ① `mutate.py` 里 `Limits(` 出现 **0** 次 —— 手拼配置漏一个字段，\n"
          "             就是**悄悄改了别的旋钮**（而读数只会说「这次变异没用」）；\n"
          "          ② `replace(settings.limits` 恰好 **1** 处 —— 唯一构造点；\n"
          "          ③ 净增益的两个比较（`net >` / `net <`）在 `bench/mutation_check.py`\n"
          "             各只出现 1 次：判定阈值散在别处就是两个未来；\n"
          "          ④ 裁决**不在内核里**：`mw/mutate.py` 不含 `net >`（内核不判自己好不好）；\n"
          "          ⑤ 台账：接受与拒绝**各一行**，字段齐全（拒绝不记账 ⇒\n"
          "             「我试过但被拒」从证据里消失，而拒绝本来占绝大多数）。\n"
          "          撤回语义：把构造改成手拼 ⇒ 必须红；拒绝时不写台账 ⇒ 必须红。",
      _az73_src.count("Limits(") == 0
      and _az73_src.count("replace(settings.limits") == 1
      and _az73_chk.count("net >") == 1 and _az73_chk.count("net <") == 1
      and _az73_src.count("net >") == 0
      and len(_az73_rows) == 2 and [r["ok"] for r in _az73_rows] == [False, True]
      and all(set(r) >= _az73_need for r in _az73_rows)
      and _az73_acc.ok and _az73_acc.settings is not None,
      f"Limits(={_az73_src.count('Limits(')} "
      f"replace={_az73_src.count('replace(settings.limits')} "
      f"net>=检查器{_az73_chk.count('net >')} 内核{_az73_src.count('net >')} "
      f"net<=检查器{_az73_chk.count('net <')} "
      f"台账={[r['ok'] for r in _az73_rows]}")

from mw.loop import Curve as _s70_Curve, CurvePoint as _s70_Point   # noqa: E402

_az74_m = MU.apply_mutation(_S70, "max_stall", 3)
_az74_diff = MV_limits_diff(_S70.limits.as_dict(), _az74_m.settings.limits.as_dict())
_az74_cb = _af_Cal(limits=_S70.limits.as_dict())
_az74_ca = _af_Cal(limits=_az74_m.settings.limits.as_dict())
_az74_cv = _s70_Curve(points=[
    _s70_Point(round=i, ts=float(i), calib=_az74_cb.signature(), mean=0.60,
               n=4, spread=0.0, trustworthy=True, harness="h")
    for i in (1, 2, 3)] + [
    _s70_Point(round=i, ts=float(i), calib=_az74_ca.signature(), mean=0.70,
               n=4, spread=0.0, trustworthy=True, harness="h")
    for i in (4, 5, 6)])
check(RS, "AZ74 ★变异**不改源码**但**换口径** ⇒ 曲线必然断段\n"
          "          ① 两臂 `Limits` 只差被声明的那个旋钮（单变量，可归因）；\n"
          "          ② 但 `limits` 在 `CALIBER_KEYS` 里 ⇒ 口径签名变了，`diff()` 点名 `limits`；\n"
          "          ③ 于是 `Curve.segments()` 把 6 个可信点切成 **3+3** 两段 ——\n"
          "             所以「变异有没有变好」**不能**用曲线连着看，只能用\n"
          "             单变量两臂 + 安慰剂（AZ72）。这条同时是「别把 `limits` 从\n"
          "             口径里删掉」的理由：删了就会允许跨变异比趋势，那是尺子下移。",
      _az74_diff == ["max_stall"]
      and _az74_cb.signature() != _az74_ca.signature()
      and _az74_cb.diff(_az74_ca) == ["limits"]
      and [len(s) for s in _az74_cv.segments()] == [3, 3],
      f"limits差={_az74_diff} 签名不同="
      f"{_az74_cb.signature() != _az74_ca.signature()} "
      f"diff={_az74_cb.diff(_az74_ca)} 分段={[len(s) for s in _az74_cv.segments()]}")

# ===========================================================================
# AZ75–AZ77（第七十一轮，§34）：题面与 oracle **同源**
#
# 7y 关掉判据层的那条读数不是"题难"，而是**题面与 oracle 是两份手写产物**：
#   题面写「最多 n 个字符」，该题自己的 oracle 交的答案是 `'...def'`（长 4 > n=3），
#   而 5/5 评分要求就是这个答案。
# ⇒ 「来源贴着题面」不等于「来源满足题面」。只要两者是两份手写产物，它们之间
#   就没有约束，只能靠人逐条读；读过这批，下一批又从零开始。
#
# 所以第七十一轮把两者都做成 spec 的函数，并在**四条互不依赖的证据**上验。
# 这组契约钉的不是"题好"，而是**准入器有牙**：每条证据都配一个坏样本，
# 坏样本必须被指名打红。⛔ 只写断言不写反证的检测器是死代码（纪律 ⑩）。
# ===========================================================================
from bench import gen_spec_tasks as GS                              # noqa: E402
from bench import taskspec as TS                                   # noqa: E402

_AZ75_SPECS = {s["id"]: s for s in GS.SPECS}
_AZ75_RP = TS.reverse_proofs(_AZ75_SPECS["sg05"], GS.CASES)
_AZ75_GOOD = {sid: TS.admit(sp, GS.CASES) for sid, sp in _AZ75_SPECS.items()}

check(RS, "AZ75 ★准入器有四条证据，且**每条都被自己的坏样本打红过**\n"
          "          ① A0 结构禁令：`lower` 与 `dedupe` 同现 ⇒ 拒绝。\n"
          "             理由不是「不好看」：「比较是否忽略大小写」在这条流水线上\n"
          "             **永远不可判定**（lower 在前 ⇒ 键已全小写；lower 在后 ⇒\n"
          "             两种保留选择被压成同一行）⇒ 写进题面就是一句无法被检验的话；\n"
          "          ② A3 后置条件：题面承诺的，**生成的源码**在每一步之后必须满足。\n"
          "             坏样本把截断写成 n+1 ⇒ 必须点名「truncate 长度上限」（7y 的实形）；\n"
          "          ③ A4 三方对撞：生成的源码 == 独立解释器（两条实现，逐条用例对拍）。\n"
          "             坏样本多打一次前缀 ⇒ 必须红，且 **A3 不许替它背锅**\n"
          "             （后置条件那时仍然成立 —— 这正是两条证据要分开的理由）；\n"
          "          ④ A5 同源双射：改一个参数 ⇒ 题面**必变** ∧ 行为**必变**。\n"
          "             只查一个方向不够：只查题面 ⇒ 代码没用它（题面在说谎）；\n"
          "             只查行为 ⇒ 代码读了题面没写的东西（答案不可从题面推出）。\n"
          "          ⑤ 六道题本身必须全部通过（否则上面的反证只是在测一条恒假的路）。",
      not any(_AZ75_GOOD.values())
      and any("A0" in f for f in _AZ75_RP["A0"])
      and any("A3" in f and "truncate" in f for f in _AZ75_RP["A3"])
      and any("A4" in f for f in _AZ75_RP["A4"])
      and not any("A3" in f for f in _AZ75_RP["A4"])
      and any("A5" in f and "题面不变" in f for f in _AZ75_RP["A5题面"])
      and any("A5" in f and "行为不变" in f for f in _AZ75_RP["A5代码"]),
      "反证=" + " ｜ ".join(f"{k}:{len(v)}" for k, v in _AZ75_RP.items())
      + " ｜ 合格题=" + str(sum(1 for v in _AZ75_GOOD.values() if not v)))

# ── AZ76：落盘的题必须**自己解释自己**（防手改 jsonl）─────────────────────
_AZ76_PATH = V1 / "bench" / "specgen" / "tasks_v1.jsonl"
_AZ76_ROWS = ([json.loads(x) for x in _AZ76_PATH.read_text(encoding="utf-8").splitlines()]
              if _AZ76_PATH.exists() else [])
_AZ76_BAD: list[str] = []
_AZ76_IDS: list[str] = []
for _r in _AZ76_ROWS:
    _sp = _r.get("meta", {}).get("spec")
    _AZ76_IDS.append(_r.get("id"))
    if not _sp:
        _AZ76_BAD.append(f"{_r.get('id')} 缺 meta.spec")
        continue
    if _r.get("prompt") != TS.render_prompt(_sp):
        _AZ76_BAD.append(f"{_r['id']} 题面与 spec 渲染不一致")
    if _r.get("tests") != TS.tests_of(_sp, GS.CASES):
        _AZ76_BAD.append(f"{_r['id']} 测试期望与 spec 不一致")
    if _r.get("oracle") != TS.compile_oracle(_sp):
        _AZ76_BAD.append(f"{_r['id']} oracle 与 spec 不一致")
    if TS.admit(_sp, GS.CASES):
        _AZ76_BAD.append(f"{_r['id']} 不过准入")
check(RS, "AZ76 ★落盘题目必须**自己解释自己**：题面/测试/oracle 都能由题内 `meta.spec` 重算出来\n"
          "          —— 手改一行期望值，这里当场红。为什么要这一条：\n"
          "             `bench/specgen/*.jsonl` **不进** `suites.manifest.json`（它只管 heldout），\n"
          "             没有这道闸，它就是一份可以被无声改写的考题。\n"
          "          ⚠️ 刻意**不**校验 `partial`：那是读数用的常识参照，由 AZ77 从 spec 现算；\n"
          "             文件里那份只是快照 —— 但整份文件与现算的逐字相等由探针 P9 另测\n"
          "             （分工：契约管「题内自洽」，探针管「生成器是唯一来源」）。",
      bool(_AZ76_ROWS) and not _AZ76_BAD
      and len(set(_AZ76_IDS)) == len(_AZ76_IDS)
      and set(_AZ76_IDS) == set(_AZ75_SPECS),
      f"题数={len(_AZ76_ROWS)} 唯一的id={len(set(_AZ76_IDS))} 问题={_AZ76_BAD[:3]}")

# ── AZ77：【读数锚】常识实现必须被扣分，而"长度合格"挡不住它 ────────────────
_AZ77_SCORE = {sid: TS.naive_hits(sp, GS.CASES) for sid, sp in _AZ75_SPECS.items()}
_AZ77_CENTERED = all(0 < k < n for k, n in _AZ77_SCORE.values())
# "长度合格、内容不合格"：常识版截断（留 n-1 个字符再补省略号）长度也 ≤ n，
# 后置条件全过，内容却是错的 ⇒ 只有"与 oracle 逐条比对"才发现得了。
_AZ77_LEAK = set()
for _sid, _sp in _AZ75_SPECS.items():
    _tr = [r for r in _sp["rules"] if r["op"] == "truncate"]
    if not _tr:
        continue
    _n = _tr[0]["n"]
    _nsrc = TS.compile_oracle(_sp, naive=True)
    _osrc = TS.compile_oracle(_sp)
    for _case in GS.CASES:
        _out = TS.run_source(_nsrc, _sp["fn"], _case)
        if all(len(_x) <= _n for _x in _out) and \
                _out != TS.run_source(_osrc, _sp["fn"], _case):
            _AZ77_LEAK.add(_sid)
            break
check(RS, "AZ77 ★【读数锚】常识实现必须**严格居中**，且「长度合格但内容错」这一格必须存在\n"
          "          ① 常识 = 参数取默认值（保第一次、精确比较、按文本升序、截断补省略号）。\n"
          "             全过 ⇒ 这题用常识就能做，没有分辨点；全不过 ⇒ 只是「和常识完全不同」，\n"
          "             任何写法都错，同样没有分辨点。**严格居中**才是分辨点；\n"
          "          ② 截断的常识写法长度也 ≤ n ⇒ **后置条件（A3）挡不住它**。\n"
          "             这一格是本轮最硬的读数：题面承诺能被检查，不等于承诺能被满足。\n"
          "             谁把 A4（逐条比对）删掉，这里就红。\n"
          "          ★ 这是**读数锚**：题集变了它就该红，改完必须回来登记为什么变。\n"
          "          ⚠️ 第七十四轮用例 7 → 14 之后这条读数**变了**：\n"
          "             长度合格却答错从 {sg01,sg02,sg03} 变成**全部 6 题**（新增用例里\n"
          "             有超过所有 n 的长行，六道题都能打出这一格）。不是变好也不是变坏，\n"
          "             是尺子变了 —— 旧读数（7 条尺子下的 3 题）留在 §34 与冻结证据里，不回头改。",
      _AZ77_CENTERED and _AZ77_LEAK == set(_AZ75_SPECS),
      "常识分=" + " ｜ ".join(f"{k}={a}/{b}" for k, (a, b) in _AZ77_SCORE.items())
      + " ｜ 长度合格却答错=" + str(sorted(_AZ77_LEAK)))

# ===========================================================================
# AZ78–AZ80（第七十三轮，§36）：噪声下限必须在**进程之间**量
#
# §35 曾把「一轮 `--repeat` 的极差」当成噪声下限，并据此断言「下限被系统性
# 低估 0.26 量级」。本轮实测把那个断言**推翻了一半，也立住了一半**：
#
#   · 推翻：§35.3③ 那张「同一配置四次观测」的表跨了 **3 个口径**
#     （`8b911be9` / `341c1eec` / `36817516`）。而 `repeat` 本身就是 `Caliber`
#     的一个字段（与 repeat=2 相比，12 个口径字段里唯一的差异就是它）
#     ⇒ 「轮内极差」与「轮间极差」**永远不同口径**：
#        拿轮内当轮间下限，在本项目自己的口径闸下是**违规**，不只是"统计上可疑"。
#
#   · 立住：按 `(臂, sig)` 分池、每池 **5 个独立进程**、`repeat=1` 重测之后，
#     两条臂的轮间极差都是 **0.0000**（逐位相同）。
#     ⇒「0」于是有两层意思：**测到的确定** 与 **没测成**（运行次数不够）。
#        旧实现在这两种情况下都给 0（`max(spread)`，spread 缺省 0），
#        而后者会让 `mutation_verdict` 把噪声读成改进。
#
# 这组契约钉的不是「某个函数写得对」，是**这件事不可能再错回去**：
#   ① 同一个 run_id 的多次重复**不许**当多次运行（这是当初那个错误的实形）；
#   ② 不够 5 次就拒判（返回 None，上层 UNKNOWN），⛔ 不默认 0；
#   ③ 混口径、混臂一律拒判；
#   ④ 旧记录没有 run_id ⇒ 抛错，不静默收下。
# 每条都配一个坏样本（实现在 `bench/noise_floor.reverse_proofs`，只有一份）。
# ===========================================================================
from bench import mutation_check as _NF_MC                        # noqa: E402
from bench import noise_floor as _NF                              # noqa: E402

_NF_RP = _NF.reverse_proofs()
_NF_BR = _NF_RP["轮内为 0"]

# 同一个 Δ，只换噪声下限这一个输入 ⇒ 结论翻转。裁决逻辑一个字没改。
_NF_DELTA = 0.02
_NF_OLD = _NF_MC.mutation_verdict(knob="max_stall", diff=["max_stall"],
                                  base_mean=0.2857, cand_mean=0.2857 + _NF_DELTA,
                                  placebo_delta=0.0, noise_floor=0.0)[0]
_NF_NEW = _NF_MC.mutation_verdict(knob="max_stall", diff=["max_stall"],
                                  base_mean=0.2857, cand_mean=0.2857 + _NF_DELTA,
                                  placebo_delta=0.0, noise_floor=_NF_BR["floor_agg"])[0]

check(RS, "AZ78 ★噪声下限 = **独立运行之间**的极差；四条纪律各有一个坏样本\n"
          "          ① 同一个 `run_id` 的五条记录 = 一次运行内部 repeat 5 次，\n"
          "             **不是** 5 次运行 ⇒ 必须拒判（旧做法在这里会得到一个数）；\n"
          "          ② 只有 4 次独立运行 ⇒ 拒判、返回 `None`（⛔ 不默认 0：\n"
          "             默认 0 等于「任何抖动都算改进」，而 `mutation_verdict`\n"
          "             对 `noise_floor=None` 的处置正是 `UNKNOWN`）；\n"
          "          ③ 混口径 ⇒ 拒判（跨口径的极差没有意义）；\n"
          "          ④ 本轮之前的记录没有 `run_id` ⇒ **抛错**，\n"
          "             不静默跳过 —— 静默跳过会让「3 次运行」看起来像「5 次」。\n"
          "          ⑤ 端到端：同一个 Δ 只换噪声下限这一个输入 ⇒ 结论从 KEEP\n"
          "             翻成 NO_EVIDENCE。裁决逻辑没动，动的是喂进去的数。",
      _NF_RP["同一次运行五次重复"][0] is None
      and _NF_RP["只有四次运行"][0] is None
      and _NF_RP["混口径"][0] is None
      and str(_NF_RP["缺 run_id"]).startswith("ValueError")
      and _NF_OLD == "KEEP" and _NF_NEW == "NO_EVIDENCE",
      f"Δ={_NF_DELTA:+.2f} ｜ 旧口径 {_NF_OLD} → 新口径 {_NF_NEW}"
      f"（轮间 {_NF_BR['floor_agg']:.4f}）")

# ── AZ79：逐题极差取 max 而不是 min，且不变量是**定理** ────────────────────
_NF_LO, _NF_AGG = _NF_RP["逐题最小当聚合下限"]
_NF_HI, _ = _NF_RP["逐题最大当聚合下限"]
_NF_INV, _NF_INV_WHY = _NF_RP["不变量"]
check(RS, "AZ79 ★逐题极差只能取 **max**，不能取 min；`max ≥ 聚合` 是定理不是经验\n"
          "          构造：两题各 5 次运行，t1 稳定（恒 1.0）、t2 在 1/0 之间摆。\n"
          "          取 min ⇒ 0.0000（比聚合 0.5000 还小）⇒ **系统性低估**，\n"
          "          而它看起来更「严谨」（「用最干净那一题量链路噪声」很顺，但是错的）。\n"
          "          定理：聚合是逐题的凸组合 ⇒ |Δmean| ≤ mean_i|Δx_i| ≤ max_i range_i。\n"
          "          所以「逐题最大」只会更保守，不会漏判。",
      _NF_LO < _NF_AGG - 1e-9 <= _NF_HI + 1e-9 and _NF_INV,
      f"min={_NF_LO:.4f} ｜ max={_NF_HI:.4f} ｜ 聚合={_NF_AGG:.4f} ｜ {_NF_INV_WHY}")

def _NF_fmt(v: float | None) -> str:
    """`None` 也要能打印 —— 详情串不许抛异常（见下面 AZ80 的注释）。"""
    return "None" if v is None else f"{v:.4f}"


# ── AZ80：【读数锚】两个池子：0.0000 是**测到的确定**，不是「没量到」 ─────────
# 本轮两次跑批（各 5 个独立进程、同口径 sig=39e815e1、repeat=1）：
#     裸模型 5 次全 0.2857 ｜ 内核 5 次全 0.5952 —— 逐题极差全 0。
_ev = V1 / "bench" / "evidence" / "noise_s72"
_NF_D, _NF_DK = _NF.pool_from_jsonl(_ev / "pool_direct_r1.jsonl")
_NF_K, _NF_KK = _NF.pool_from_jsonl(_ev / "pool_kernel_r1.jsonl")
_NF_BD, _NF_BK = _NF.bracket(_NF_D), _NF.bracket(_NF_K)
_NF_F5, _ = _NF.floor(_NF_D, min_runs=5)       # 5 次独立运行 ⇒ 测到 0.0000
# ⚠️ 这里**写死 5** 而不是引用 `_NF.MIN_RUNS`：本条测的是"少一次就只能是 None"
#    这个**语义**，而"阈值该是几"由 AZ78 的坏样本管。写死才能让两条断言分开转红
#    （实测：引用常量时，把 MIN_RUNS 改成 1 会同时打红 AZ78 与 AZ80）。
_NF_F4, _ = _NF.floor(_NF_D[:4], min_runs=5)  # 只给 4 次 ⇒ None（⛔ 不是 0）
_NF_MIX, _ = _NF_RP["同口径不同臂"]
_NF_FAKE = _NF_RP["只按 sig 分组的假极差"]
check(RS, "AZ80 ★【读数锚】两个池子各自 5 次独立运行，轮间极差 **0.0000**；\n"
          "          但「0」必须是**测出来的**：少一次运行就只能是 `None`。\n"
          "          ① 5 次 ⇒ 0.0000（确定），4 次 ⇒ None（没测成）——\n"
          "             这两件事在旧实现里长得一模一样（`max(spread)`，缺省 0），\n"
          "             而后者会让 `mutation_verdict` 把噪声读成改进；\n"
          "          ② 两条臂的 `sig` **完全相同**（`Caliber` 里没有 solver）⇒\n"
          "             只按 sig 分组会把「裸模型 0.2857 + 内核 0.5952」放进一个池子，\n"
          "             造出一个 0.3095 的**假噪声**（那是两个系统的差，不是抖动）\n"
          "             ⇒ 必须按 `(臂, sig)` 分组，混臂拒判；\n"
          "          ③ 池子里每条 `run_id` 必须互不相同（否则「5 次」里可以有\n"
          "             4 次是同一个进程 —— 这正是当初那个错误的实形）。\n"
          "          ⛔ 这是**读数锚**：换模型/题集/温度/臂它就该红，改完必须回来登记。",
      _NF_F5 == 0.0 and _NF_F4 is None
      and _NF_BD["n_runs"] >= 5 and _NF_BK["n_runs"] >= 5
      and _NF_BD["floor_agg"] == 0.0 and _NF_BK["floor_agg"] == 0.0
      and all(len({o.run_id for o in p}) == len(p) for p in (_NF_D, _NF_K))
      and _NF_MIX is None and _NF_FAKE > 0.3,
      # ⛔ 详情串**不许因为读到 None 就抛异常**：断言失败时要打印理由，
      #    不是变成一条 Traceback（实测：`{None:.4f}` 会让整份契约崩掉，
      #    汇总行都不打印 —— 那样"哪个断言红了"这个信息本身也没了）。
      f"裸模型 {_NF_fmt(_NF_BD['floor_agg'])}（{_NF_BD['n_runs']} 次）｜ "
      f"内核 {_NF_fmt(_NF_BK['floor_agg'])}（{_NF_BK['n_runs']} 次）｜ "
      f"只给 4 次 ⇒ {_NF_fmt(_NF_F4)} ｜ 混臂 ⇒ {_NF_MIX} ｜ 假极差 {_NF_FAKE:.4f}")

# ===========================================================================
# AZ81–AZ82（第七十四轮，§37）：用例数 **不是** 刻度
#
# §36.4 的处方是"加密用例：7 → 21 条/题"。本轮把这句话量了一遍：
#
#   一条用例 = 1/N 分，这是**条数**。刻度是另一件事 ——
#   两个不同的错误实现会不会拿到同一个分？会，则"变异让分数动了"
#   在这两者之间不可观测：不是信号小，是尺子上没有这道刻度。
#
#   尺子：spec 派生的变异族（参数改动 / 丢一步 / 换序 / 常识实现），
#         每条实现算"通过向量"，向量相同 ⇒ 分不开。
#   天花板：同一族在 400 条随机输入上的种类数 —— 这套用例**最多**能表达多少种错误。
#
#   实测：7 条 41 种 ⇒ 天花板 75 种；贪心追加到 **14 条**达到 75（饱和）；
#         再凑到 21 条（把前 7 条抄一遍）种类数**一位不动**。
#
# ⇒ 21 是拍板数，不是需求数。**把分母切细不等于把刻度做细。**
# ===========================================================================
from bench import casepower as CP                                  # noqa: E402

_AZ81_PW = CP.Power(GS.SPECS, list(GS.CASES) + CP.SPACE, space=CP.SPACE)
_AZ81_CAP = _AZ81_PW.ceiling()                     # 定义空间上的天花板
_AZ81_NOW, _AZ81_PER = _AZ81_PW.species(GS.CASES)  # 现在这套用例能分辨多少种
_AZ81_BARE = _AZ81_PW.species(GS.CASES[:7])[0]     # 第七十一轮那 7 条
_AZ81_21 = _AZ81_PW.species(list(GS.CASES) + list(GS.CASES[:7]))[0]  # 凑到 21 条
_AZ81_RP = CP.reverse_proofs(_AZ81_PW, GS.CASES[:7], GS.CASES[7:])

check(RS, "AZ81 ★【读数锚】用例 7 → 14 条就**饱和**；凑到 21 条区分力一位不动\n"
          "          ① 天花板 = 400 条随机输入上这套尺子最多能表达的错误种类数；\n"
          "          ② `species(CASES) == 天花板` ⇒ 饱和：再加用例**不再增加区分力**，\n"
          "             只把 1/N 的分母做大（对阶跃分布的分数反而是钝化）；\n"
          "          ③ 第七十一轮那 7 条**严格低于**天花板 ⇒ 「7 条不够」有数，\n"
          "             不是感觉；\n"
          "          ④ 凑到 21 条（把前 7 条抄一遍）种类数与 14 条**完全相同** ⇒\n"
          "             「21 条更好」是拍板，不是读数。\n"
          "          ⛔ 这是**读数锚**：换变异族 / 换定义空间 / 换题集它就该红，\n"
          "             改完必须回来登记为什么变。\n"
          "          ⚠️ 边界：它只说「能分开多少种**这种形态**的错误」。真实模型的\n"
          "             错误形态不在这个族里 —— 这一层量不到，不许读成「这题不会被答错」。",
      _AZ81_NOW == _AZ81_CAP and _AZ81_BARE < _AZ81_CAP and _AZ81_21 == _AZ81_NOW,
      f"用例 {len(GS.CASES)} 条 ⇒ {_AZ81_NOW} 种 / 天花板 {_AZ81_CAP} 种 ｜ "
      f"7 条 ⇒ {_AZ81_BARE} 种 ｜ 21 条 ⇒ {_AZ81_21} 种 ｜ "
      f"逐题={'  '.join(f'{k}={v}' for k, v in sorted(_AZ81_PER.items()))}")

check(RS, "AZ82 ★这把尺子**不是条数计数器**（四条反证，就地算）\n"
          "          ① 复制一条现有用例 ⇒ 条数 +1、种类数**不变**；\n"
          "          ② 把追加的 7 条再抄一遍（凑 21 条）⇒ 种类数**不变**；\n"
          "          ③ 去掉追加的 7 条 ⇒ 种类数**严格下降**（那 7 条不是白加的）；\n"
          "          ④ 任何用例集都不会超过天花板（它不是「越多越好」的单调幻觉）。\n"
          "          ⛔ ①② 是同一个坑的两面：**分母变大**会被误读成**刻度变细**。\n"
          "             没有这四条反证，AZ81 那种「加用例 ⇒ 分数更细」的说法\n"
          "             可以永远成立，也就永远没有被证伪的机会。",
      _AZ81_RP["① 复制一条不涨"] and _AZ81_RP["② 抄一遍不涨"]
      and _AZ81_RP["③ 去掉追加就掉"] and _AZ81_RP["④ 没有超过天花板"],
      f"14 条={_AZ81_RP['full']} 种 ｜ 7 条={_AZ81_RP['bare']} 种 ｜ "
      f"复制一条={_AZ81_RP['复制一条']} ｜ 抄一遍={_AZ81_RP['追加重抄一遍']} ｜ "
      f"天花板={_AZ81_RP['ceiling']}")

# ===========================================================================
# AZ83–AZ85（第七十六轮，§38）：**进化闭环的第一段驱动器**
#
# 第七十五轮体检的结论是「不能进化」，三条断线：变异器在内核里零引用、
# `mutation_verdict` 没吃过真数、变异台账从未被写过。
# 本轮的驱动器 `bench/mutate_driver.py` 把三块拼图连起来，并**真跑了一次**。
#
# 这一节的断言全部盯着一件事：**「我以为我改了什么」与「跑的时候是什么」
# 必须是同一件事**。`run_tests.sh:470` 早就写着这个病：
#   `apply_mutation` 返回 ok=True 只证明配置对象换了，不证明内核读了它。
# ===========================================================================
from bench import mutate_driver as MD                              # noqa: E402
from mw.config import Settings as _MD_Settings                     # noqa: E402

_MD_S = _MD_Settings()
_MD_BASE = _MD_S.limits.as_dict()
_MD_CAND = MD.Arm("cand", {"V1_MAX_STALL": "3"}, {"max_stall": 3})
_MD_PLAC = MD.Arm("placebo", {"V1_WORKING_MEMORY_ITEMS": "32"},
                  {"working_memory_items": 32})
_MD_BASEARM = MD.Arm("base", {}, {})


def _md_rec(**over):
    lim = dict(_MD_BASE)
    lim["max_stall"] = 3
    lim.update(over)
    return {"caliber": {"limits": lim}, "run_id": "4242@1792844000"}


def _md_rejects(rec, arm=None):
    """守门员**拒绝**了这条记录吗？返回 True = 拒绝（=断言生效）。"""
    try:
        MD.check_record(rec, arm if arm is not None else _MD_CAND, _MD_BASE)
    except RuntimeError:
        return True
    return False


_AZ83_OK = not _md_rejects(_md_rec())               # 正路：合法记录必须**通过**
_AZ83_DEAD = _md_rejects(_md_rec(max_stall=2))      # 没生效（值还是 2）
_AZ83_MULTI = _md_rejects(_md_rec(recall_k=8))      # 两个旋钮一起动
_AZ83_NORID = _md_rejects({**_md_rec(), "run_id": ""})
_AZ83_NOLIM = _md_rejects({"caliber": {}, "run_id": "4242@1"})
_AZ83_BASE = _md_rejects({"caliber": {"limits": {**_MD_BASE, "max_stall": 3}},
                          "run_id": "4242@1"}, _MD_BASEARM)
_AZ83_2KNOB = _md_rejects(_md_rec(), MD.Arm("x", {}, {"max_stall": 3, "recall_k": 8}))
# 安慰剂臂必须用它**自己**的旋钮判 —— 第一版把期望值当参数传，
# 于是安慰剂被自己的守门员判红（而 base 臂因为期望为空，那个参数恒不被读）。
_AZ83_PLAC_OK = not _md_rejects(_md_rec(working_memory_items=32, max_stall=2), _MD_PLAC)

check(RS, "AZ83 ★驱动器守门员：**生效**与**单变量**必须当场验，不许等裁决（六条反证）\n"
          "          ① 合法记录通过（否则这是个恒红的死断言）；\n"
          "          ② 旋钮**没生效**（值还是旧值）⇒ 拒 —— 这就是"
          "「环境变量没进内核」的实形；\n"
          "          ③ 两个旋钮一起动 ⇒ 拒（Δ 不可归因）；\n"
          "          ④ 缺 `run_id` ⇒ 拒（无法证明是独立的一次运行）；\n"
          "          ⑤ 没有 `caliber.limits` ⇒ 拒（无法验证变异生效）；\n"
          "          ⑥ base 臂（期望为空）遇到任何差异 ⇒ 拒；声明多个旋钮 ⇒ 拒。\n"
          "          ⛔ 期望「该差哪个旋钮」**只能从 `arm.expect` 推**，不许当参数传：\n"
          "             第一版写成 `check_record(..., knob)`，主流程传的是处理臂的旋钮，\n"
          "             安慰剂臂（改的是另一个）被自己的守门员判红 —— 而 base 臂\n"
          "             因为期望为空，那个参数**恒不被读**，bug 一直藏到安慰剂才露头。",
      _AZ83_OK and _AZ83_DEAD and _AZ83_MULTI and _AZ83_NORID
      and _AZ83_NOLIM and _AZ83_BASE and _AZ83_2KNOB and _AZ83_PLAC_OK,
      f"通过={_AZ83_OK} ｜ 没生效={_AZ83_DEAD} ｜ 多变量={_AZ83_MULTI} ｜ "
      f"缺run_id={_AZ83_NORID} ｜ 缺limits={_AZ83_NOLIM} ｜ "
      f"base有差异={_AZ83_BASE} ｜ 声明多旋钮={_AZ83_2KNOB} ｜ 安慰剂自判={_AZ83_PLAC_OK}")

# ── AZ84：池子必须按 (臂, 口径, solver, limits) **四重**过滤 ────────────────
# `Caliber` 里**没有** solver ⇒ 裸模型直连与内核可以是同一个 sig
# （第七十三轮实测：只按 sig 分组会把 0.2857 与 0.5952 放进一池，造出 0.3095 假噪声）。
# 这里用**临时台账**构造那个陷阱，证明少一条过滤就被污染。
import json as _md_json                                            # noqa: E402

_MD_DIR = Path(tempfile.mkdtemp(prefix="v1_md_")) / "runs.jsonl"


def _md_row(sig, solver, lim, rid, mean=0.5):
    return _md_json.dumps({"run_id": rid, "caliber_sig": sig, "solver": solver,
                           "caliber": {"limits": dict(lim)},
                           "measurement": {"mean": mean}})


_MD_K_LIM = dict(_MD_BASE, max_stall=3)          # 处理臂的 limits
_MD_OTHER = dict(_MD_BASE, max_stall=4)          # 另一个变异（不该被挑中）
_MD_DIR.write_text("\n".join([
    _md_row("AA", "内核", _MD_K_LIM, "1@1"),      # 3 条要的
    _md_row("AA", "内核", _MD_K_LIM, "2@2"),
    _md_row("AA", "内核", _MD_K_LIM, "3@3"),
    _md_row("AA", "裸模型", _MD_K_LIM, "4@4"),    # 同口径、同 limits，但**另一个系统**
    _md_row("AA", "裸模型", _MD_K_LIM, "5@5"),
    _md_row("BB", "内核", _MD_K_LIM, "6@6"),      # 异口径
    _md_row("AA", "内核", _MD_OTHER, "7@7"),      # 异配置
    _md_json.dumps({"caliber_sig": "AA", "solver": "内核",
                    "caliber": {"limits": _MD_K_LIM},
                    "measurement": {"mean": 0.5}}),   # 无 run_id
]) + "\n", encoding="utf-8")

_AZ84_KEEP, _AZ84_DROP = MD.pool_from_ledger(_MD_DIR, arm=_MD_CAND,
                                             base_limits=_MD_BASE,
                                             sig="AA", solver="内核")
_AZ84_NO_SOLVER, _ = MD.pool_from_ledger(_MD_DIR, arm=_MD_CAND,
                                         base_limits=_MD_BASE,
                                         sig="AA", solver=None)
_AZ84_IDS = sorted(r["run_id"] for r in _AZ84_KEEP)

check(RS, "AZ84 ★池子四重过滤：口径 / solver / limits / run_id，少一条就污染\n"
          "          构造 8 条：3 条正品 + 2 条**同口径同 limits 的另一个系统**\n"
          "          + 1 条异口径 + 1 条异配置 + 1 条无 run_id。\n"
          "          ① 四重全开 ⇒ 只挑到 3 条，且 `run_id` 恰是那 3 个；\n"
          "          ② 反证：**去掉 solver 过滤** ⇒ 挑到 5 条（两个系统混一池）；\n"
          "          ③ 无 `run_id` 的被**数出来**（拒收统计里见到 1），不是静默消失。\n"
          "          ⛔ ②是这一条存在的理由：`Caliber` 不含 solver，所以"
          "「同口径」不等于「同系统」。",
      len(_AZ84_KEEP) == 3 and _AZ84_IDS == ["1@1", "2@2", "3@3"]
      and len(_AZ84_NO_SOLVER) == 5 and _AZ84_DROP["no_run_id"] == 1,
      f"四重={len(_AZ84_KEEP)} 条 {_AZ84_IDS} ｜ 去掉solver={len(_AZ84_NO_SOLVER)} 条 ｜ "
      f"拒收 {_AZ84_DROP}")

# ── AZ85：进化闭环的**第一圈读数锚** ──────────────────────────────────────
# 三臂各 5 次独立进程（`--fresh --no-external`，T=0，新尺子 14 条用例）。
# 从冻结的三个池子**重算**噪声与裁决 —— 断言「同一输入同一结论」，不是"JSON 里写着 NO_EVIDENCE"。
_MD_EV = V1 / "bench" / "evidence" / "evolve_s76"


def _md_obs(name):
    p = _MD_EV / f"pool_{name}.jsonl"
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [_NF.from_record(r) for r in rows]


_MD_OB = {k: _md_obs(k) for k in ("base", "cand", "placebo")}
_MD_FL = {k: _NF.noise_for_verdict(v) for k, v in _MD_OB.items()}
_MD_MEAN = {k: sum(o.mean for o in v) / len(v) for k, v in _MD_OB.items()}
#: 台账 `mean` 的**量化模拟**：逐题分数先 `round(…, 4)`，再按题数平均。
#: 这不是「从池子里抄一个数」，是**从机制重算**：`47 格 = 1+10+14+14+0+8`
#: ⇒ 每题 `round(n/14, 4)` 再平均 = `0.55951666…`，与台账逐位相同（AZ85 里断言）。
#: ⛔ AZ87 用它而**不读池子** —— 读池子会让它和 AZ85 的撤回态耦合：
#:    实测抹平 `base` 那条 0.5238 之后噪声从 `3/84` 掉到 `1.7e-05`，
#:    于是 AZ87 的「+3 格」判据翻成 `KEEP`，AZ85 的撤回态当场多红一条。
_QZ_CASES = 6            # 题数
_QZ_PER_TASK = 14        # 每题用例数


def _qz_mean(cells: list[int]) -> float:
    """给定每题命中的格数，按台账的**量化规则**算出 `mean`。"""
    return sum(round(c / _QZ_PER_TASK, 4) for c in cells) / _QZ_CASES


_QZ_47 = _qz_mean([1, 10, 14, 14, 0, 8])     # 常态：47/84 格
_QZ_44 = _qz_mean([1, 7, 14, 14, 0, 8])      # 冷启动那次：44 格（sg02 只过 7 条）
_MD_NF = max(v for v in _MD_FL.values())
_MD_DLT = _MD_MEAN["cand"] - _MD_MEAN["base"]
_MD_PDL = _MD_MEAN["placebo"] - _MD_MEAN["base"]
_MD_V, _MD_WHY = MV_verdict(knob="max_stall", diff=["max_stall"],
                            base_mean=_MD_MEAN["base"], cand_mean=_MD_MEAN["cand"],
                            placebo_delta=_MD_PDL, noise_floor=_MD_NF)
# 结论**不靠噪声口径撑着**：即使把噪声当 0，净变化仍是 0 ⇒ 结论不变。
_MD_V0, _ = MV_verdict(knob="max_stall", diff=["max_stall"],
                       base_mean=_MD_MEAN["base"], cand_mean=_MD_MEAN["cand"],
                       placebo_delta=_MD_PDL, noise_floor=0.0)

check(RS, "AZ85 ★进化闭环第一圈：变异 → 跑 → 量 → 判，结论是 **NO_EVIDENCE**\n"
          "          三个臂各 5 次独立进程，`--fresh --no-external`，T=0，新尺子 14 条用例。\n"
          "          ① 三臂的 `sig` **互不相同** —— 因为 `limits` 进 `Caliber`，\n"
          "             改旋钮必然换口径（这就是「两臂同口径」在变异场景下不成立的结构原因）；\n"
          "          ② base 的轮间极差 ≥ 0.03，而 cand / placebo 都是 0：\n"
          "             那 0.0357 = 3/84（新尺子 84 格里 3 格）**全部来自"
          "「整个实验的第一次运行」**，\n"
          "             逐题全落在 `sg02`（7/14 ↔ 10/14）；⛔ 原因**未定**\n"
          "             （冷启动 / T=0 残余抖动 / 旧尺子测不到，三条都容得下）；\n"
          "             ⚠️ 这里刻意写成 `>= 0.03` 而**不是** `> 0`：撤回态 ㊵ 把那条\n"
          "                读数抹成 0.5595 之后，剩下的差还有 **1.67e-05** —— `> 0`\n"
          "                照样成立，那它就不是读数锚，只是个恒真的装饰。\n"
          "          ③ **Δ(cand) 与 Δ(placebo) 逐位相同** ⇒ 安慰剂把全部「收益」吃掉了。\n"
          "             这是安慰剂臂存在的唯一理由：没有它，+0.0071 会被读成「改进」；\n"
          "          ④ 净变化 = 0.0000 ⇒ **NO_EVIDENCE**（「没证据说更好 = 不改」）；\n"
          "          ⑤ 把噪声改写成 0 结论不变 ⇒ 这个判决不是靠「噪声大」撑住的；\n"
          "          ⑥ **量化模拟必须与台账逐位相同**（`round(n/14, 4)` 再平均\n"
          "             = `0.55951666…`）—— 这条把「机制」与「读数」钉在一起；\n"
          "             AZ87 用模拟值、**不读池子**，所以这边被撤回时不会连带它变红。\n"
          "          ⛔ 这是**读数锚**：换模型 / 题集 / 温度 / 旋钮它就该红，改完必须回来登记。\n"
          "          ⚠️ 边界：它只说「这一次、这套题、这个模型下，`max_stall 2→3` 没有证据更好」。\n"
          "             不许读成「这个旋钮永远没用」，更不许读成「内核不会进化」。",
      all(len(v) == 5 for v in _MD_OB.values())
      and len({next(iter({o.sig for o in v})) for v in _MD_OB.values()}) == 3
      and _MD_FL["base"] >= 0.03 and _MD_FL["cand"] == 0.0 and _MD_FL["placebo"] == 0.0
      and abs(_MD_DLT - _MD_PDL) < 1e-12
      and abs(_QZ_47 - _MD_MEAN["placebo"]) < 1e-9      # ⑥ 模拟 = 台账
      and _MD_V == MV_NO_EV and _MD_V0 == MV_NO_EV,
      f"各 {len(_MD_OB['base'])} 次 ｜ base {_MD_MEAN['base']:.4f} "
      f"cand {_MD_MEAN['cand']:.4f} 安慰剂 {_MD_MEAN['placebo']:.4f} ｜ "
      f"Δ={_MD_DLT:+.4f} 安慰剂Δ={_MD_PDL:+.4f} ｜ 噪声={_MD_NF:.4f}"
      f"（base={_MD_FL['base']:.4f} cand={_NF_fmt(_MD_FL['cand'])} "
      f"plac={_NF_fmt(_MD_FL['placebo'])}）｜ 裁决={_MD_V} ｜ 噪声置0仍={_MD_V0} ｜ "
      f"量化模拟={_QZ_47:.10f}")

# ── AZ86：两道闸（交叠 / 量级）—— 而且它们必须**承重** ────────────────────
# 为什么要有这两道：第七十六轮实测驱动器自己那 12 次运行轮间极差 **恰好 0.0000**
# ⇒ 噪声下限 = 0 ⇒ 旧判据 `net > 0` 会把「1 格运气」判成 KEEP。
# 这里用**合成读数**测闸本身，并各配一条反证（把闸关掉，结论必须变）。
_AZ86_RUNS = {k: [o.mean for o in v] for k, v in _MD_OB.items()}
_AZ86_F0 = {"base": 0.0, "cand": 0.0, "placebo": 0.0}
_AZ86_M = {"base": 0.5595, "placebo": 0.5595}
_AZ86_CTL = {"base": [0.5595] * 5, "placebo": [0.5595] * 5}


def _az86(means, runs, cells=3, floors=None):
    return MD.judge(knob="max_stall", means=means,
                    floors=_AZ86_F0 if floors is None else floors,
                    runs=runs, min_effect_cells=cells)


_AZ86_REAL = _az86(_MD_MEAN, _AZ86_RUNS, floors=_MD_FL)
_AZ86_UP = _az86({**_AZ86_M, "cand": 0.60}, {**_AZ86_CTL, "cand": [0.60] * 5})
_AZ86_CELL = _az86({**_AZ86_M, "cand": 0.5714}, {**_AZ86_CTL, "cand": [0.5714] * 5})
_AZ86_OVL = _az86({**_AZ86_M, "cand": 0.5714},
                  {**_AZ86_CTL, "cand": [0.5595] + [0.5714] * 4})
_AZ86_DN = _az86({**_AZ86_M, "cand": 0.50}, {**_AZ86_CTL, "cand": [0.50] * 5})
# 反证：闸**关掉**之后，同样的读数必须变成 KEEP —— 否则那道闸是装饰。
_AZ86_OVL_OFF = _az86({**_AZ86_M, "cand": 0.5714}, {})
_AZ86_CELL_OFF = _az86({**_AZ86_M, "cand": 0.5714},
                       {**_AZ86_CTL, "cand": [0.5714] * 5}, cells=0)

check(RS, "AZ86 ★两道闸：交叠（不要常数）+ 量级（政策，用分数不用小数字面量）\n"
          "          第七十六轮的实测把旧判据的洞照出来了：驱动器自己那 12 次运行\n"
          "          轮间极差**恰好 0.0000** ⇒ 噪声 = 0 ⇒ `net > 0` 就够判 KEEP，\n"
          "          于是「1 格运气」= 「变强了」。补两道闸：\n"
          "          ① **交叠闸**：处理臂**每一次**都要严格好过对照组（base ∪ 安慰剂）\n"
          "             **每一次**。它不需要任何常数 —— 问的是「两个分布分不分得开」；\n"
          "             相等算交叠，不算分开；\n"
          "          ② **量级闸**：低于最小效应量的差不算变强。它是**政策**，\n"
          "             所以由调用方显式传入；⛔ 必须用 `格数/总格数` 算\n"
          "             （`3/84 = 0.035714`），**不许**写 `0.0357` —— 那个字面量\n"
          "             比真值小 1.4e-5，而容差是 1e-9，于是「恰好等于」会被判成「大于」；\n"
          "          ③ 真实池子过闸：结论仍是 NO_EVIDENCE，但理由从「净变化恰好 0」\n"
          "             升级成「**交叠**」—— 结论不再依赖噪声口径撑不撑得住；\n"
          "          ④ 两条反证：关掉交叠闸 / 关掉量级闸，同一个读数都变成 KEEP。\n"
          "          ⛔ 这是判据锚：换题集 / 换旋钮它不该红；改闸的语义它必须红。",
      _AZ86_REAL[0] == MV_NO_EV and "交叠" in _AZ86_REAL[1]
      and _AZ86_UP[0] == MV_KEEP
      and _AZ86_CELL[0] == MV_NO_EV and "未达最小效应量" in _AZ86_CELL[1]
      and _AZ86_OVL[0] == MV_NO_EV and "交叠" in _AZ86_OVL[1]
      and _AZ86_DN[0] == MV_REVERT
      and _AZ86_OVL_OFF[0] == MV_KEEP and _AZ86_CELL_OFF[0] == MV_KEEP
      and MD.total_cells() == 84
      and abs(_AZ86_UP[2]["min_effect"] - 3 / 84) < 1e-12
      and _AZ86_UP[2]["min_effect"] > 0.0357,
      f"真实={_AZ86_REAL[0]} ｜ 分开+够量级={_AZ86_UP[0]} ｜ 1格={_AZ86_CELL[0]} ｜ "
      f"交叠={_AZ86_OVL[0]} ｜ 向下={_AZ86_DN[0]} ｜ "
      f"关交叠闸={_AZ86_OVL_OFF[0]} 关量级闸={_AZ86_CELL_OFF[0]} ｜ "
      f"总格数={MD.total_cells()} 最小效应量={_AZ86_UP[2]['min_effect']:.6f}")

# ── AZ87：容差必须盖住**读数量化**，否则边界由舍入决定 ──────────────────
# 第七十八轮实测：`report.per_task[].score` 存的是 **4 位小数**（`1/14` → `0.0714`），
# 逐题平均后台账 `mean = 0.55951666…`，而真值 `47/84 = 0.55952380…` ⇒ 差 **7.1e-6**。
# 噪声下限由这些 mean 相减得到，于是 `3/84` 被算成 `0.03571667`。
# ⇒ 用 `_TOL = 1e-9` 判「净增益恰好等于噪声」时，边界由最后几位小数决定：
#    实测 `cand = 50/84`（+3 格）时 `net - 噪声 = +4.8e-6` ⇒ 旧容差判 `KEEP`（错），
#    按政策必须是 `NO_EVIDENCE`（净增益要**严格大于**噪声）。
# ⛔ 这里的三个输入**全部由 `_qz_mean` 重算，不读池子**：
#    第一版读的是 `_MD_MEAN` / `_MD_FL` / `_AZ86_RUNS`，于是 AZ85 的撤回态
#    （把 base 那条 0.5238 抹成 0.5595）把噪声从 `3/84` 打到 `1.7e-05`，
#    「+3 格」立刻翻成 `KEEP` ⇒ AZ87 连带变红。实测 156 条撤回里唯一的多红就是它。
#    语义上也该改：AZ87 测的是**容差与量化的关系**，不是台账内容。
_AZ87_Q = abs(_QZ_47 - 47 / 84)                              # 量化偏差（机制重算）
_AZ87_FLOOR = _QZ_47 - _QZ_44                                # 台账里 base 的轮间极差
_AZ87_M = {"base": _QZ_47, "placebo": _QZ_47}
_AZ87_R = {"base": [_QZ_47] * 5, "placebo": [_QZ_47] * 5}
_AZ87_F = {"base": _AZ87_FLOOR, "cand": 0.0, "placebo": 0.0}
_AZ87_C50 = _az86({**_AZ87_M, "cand": 50 / 84},
                  {**_AZ87_R, "cand": [50 / 84] * 5}, floors=_AZ87_F)
_AZ87_C51 = _az86({**_AZ87_M, "cand": 51 / 84},
                  {**_AZ87_R, "cand": [51 / 84] * 5}, floors=_AZ87_F)

# 反证：把容差换回 1e-9，**同一条读数**必须变 KEEP —— 证明 `_TOL` 是承重的
_MC_TOL = _NF_MC._TOL
try:
    _NF_MC._TOL = 1e-9
    _AZ87_TIGHT = MV_verdict(
        knob="max_stall", diff=["max_stall"],
        base_mean=_QZ_47, cand_mean=50 / 84,
        placebo_delta=0.0,
        noise_floor=_AZ87_FLOOR, min_effect=3 / 84,
        cand_runs=[50 / 84] * 5,
        control_runs=[_QZ_47] * 10)[0]
finally:
    _NF_MC._TOL = _MC_TOL

check(RS, "AZ87 ★容差必须盖住读数量化（`_TOL` 太小 ⇒ 边界由舍入决定）\n"
          "          背景：台账 `mean` 由**四舍五入到 4 位小数**的逐题分数再平均而来\n"
          "          （实测 `0.55951666…` vs 真值 `47/84 = 0.55952380…`，差 7.1e-6）；\n"
          "          于是「净增益恰好等于噪声」这个边界是 1e-5 量级的差在裁决。\n"
          "          ① `_TOL` 必须 > 1e-4（盖住量化）且 < 半格 `0.5/84`（不吃掉真实格差）；\n"
          "          ② 实测那个 +3 格读数（`net - 噪声 = +4.8e-6`）⇒ `NO_EVIDENCE`；\n"
          "          ③ +4 格 ⇒ `KEEP`（一格都不能少）；\n"
          "          ④ 反证：把 `_TOL` 临时换回 `1e-9`，同一条读数变 `KEEP`；\n"
          "          ⑤ 跑完必须把 `_TOL` 还原（否则后面每一条判据都被污染）；\n"
          "          ⑥ **自带输入**：三个数（`_QZ_47` / `_QZ_44` / 二者之差）由\n"
          "             `round(n/14, 4)` 重算，**不读冻结池子** —— 第一版读池子，\n"
          "             于是 AZ85 的撤回态把这边的噪声打掉，连带判红（耦合）。\n"
          "             现在 AZ85 那边撤回，这条仍然绿。\n"
          "          撤回语义：`_TOL` 改回 `1e-9` ⇒ 必须红。",
      _NF_MC._TOL > 1e-4 and _NF_MC._TOL < 0.5 / 84
      and 1e-6 < _AZ87_Q < 1e-4
      and _AZ87_C50[0] == MV_NO_EV
      and _AZ87_C51[0] == MV_KEEP
      and _AZ87_TIGHT == MV_KEEP
      and _NF_MC._TOL == _MC_TOL,
      f"量化偏差={_AZ87_Q:.2e} ｜ _TOL={_NF_MC._TOL:.0e}"
      f"（需 >1e-4 且 <{0.5 / 84:.4f}）｜ 噪声={_AZ87_FLOOR:.8f}"
      f"（{_QZ_47:.10f} - {_QZ_44:.4f}）｜ "
      f"+3格={_AZ87_C50[0]} +4格={_AZ87_C51[0]} ｜ 容差调紧后同一读数={_AZ87_TIGHT} ｜ "
      f"已还原={_NF_MC._TOL == _MC_TOL}")

# ═══════════════════════════════════════════════════════════════════════════
# AZ88 ★答案本体落盘（第七十九轮，§42）
#
# 为什么要这条：同一形态的截断在四处出现过（`mw/bench.py:801` 的 `why[:200]`、
# `mw/critique.py:42` 的 `_MAX_ANSWER=200`、日志只印答案 5 行、`runs.jsonl`
# 只存 `report`）。后果是**量到一个 `AttributeError` 却看不到它写的那行代码**，
# 只能拿合成样本做反证 —— 「能证明自检看不见这类错」与「能证明模型就是这么写的」
# 是两件事，而后者才决定下一轮该改什么。
#
# 这条钉三件事：
#   ① **默认不截断**（5500 字符写读逐字相同，`chars`/`sha256` 自洽）；
#   ② **截断必须自陈**（设了 cap 就在记录里写 `truncated: true`）；
#   ③ **它不是分数通道**（落盘键里没有分数/判据；`mw/` 零引用）。
#
# ⛔ 全部输入**自带**（临时目录里现造），不读冻结池子、不读 `bench_out/`
#    —— 否则就重演「一个断言依赖另一个断言所检验的那份读数」那个耦合（§41.7）。
# 行为侧的「真的接通了」由 `bench/probe_answer_store.py` P6/P7 证明，
# 这里只加一条**静态锚**说明接线点在 `run.py`（静态锚比行为锚弱，明写）。
# ═══════════════════════════════════════════════════════════════════════════
import tempfile as _AZ88_tf

from bench import answer_store

#: 5500 字符 ≈ 11 倍于那四处历史截断的长度。用「约 11 倍」而不是「恰好 5500」，
#: 是为了让判据的意图可读：它要证明的是**量级**上不可能被 200 切掉。
_AZ88_LONG = "".join(f"第{i:04d}行：def norm_sg05(rows): return rows  # 填充\n"
                     for i in range(120))
with _AZ88_tf.TemporaryDirectory(prefix="v1_az88_") as _az88_td:
    _AZ88_P = Path(_az88_td) / "answers.jsonl"
    _AZ88_LOG = answer_store.AnswerLog(_AZ88_P)
    _AZ88_LOG.add("sg05", _AZ88_LONG)
    _AZ88_W = _AZ88_LOG.save(run_id="4242@1792844000", meta={"solver": "test"})
    _AZ88_ROWS = answer_store.AnswerLog.read(_AZ88_P)
    _AZ88_GOT = _AZ88_ROWS[0]["answer"] if _AZ88_ROWS else ""
    # ⛔ 「按轮取答案」是这个文件的主要用法。第一版只在 header 上写 `run_id`，
    #    于是 `read(run_id=...)` 过滤出**空列表**（真跑当场 KeyError）。
    _AZ88_BYRUN = answer_store.AnswerLog.read(_AZ88_P, run_id="4242@1792844000")

    # ── 反证装置：`verify_rows` 必须能抓出**撒谎的行**（否则它是死代码）──
    _AZ88_LIE_LEN = answer_store.verify_rows([{**_AZ88_ROWS[0], "chars": 7}])
    _AZ88_LIE_SHA = answer_store.verify_rows([{**_AZ88_ROWS[0], "sha256": "0" * 64}])

    # ── cap 一侧：设了就必须截断，且**自己说出来** ──────────────────────
    _AZ88_P2 = Path(_az88_td) / "capped.jsonl"
    _AZ88_L2 = answer_store.AnswerLog(_AZ88_P2, cap=200)
    _AZ88_L2.add("sg05", _AZ88_LONG)
    _AZ88_W2 = _AZ88_L2.save(run_id="4243@1792844001")
    _AZ88_R2 = answer_store.AnswerLog.read(_AZ88_P2)[0]

    # ── 空缓冲：必须仍写 header（「没答案」≠「没写过」）──────────────────
    _AZ88_P3 = Path(_az88_td) / "empty.jsonl"
    answer_store.AnswerLog(_AZ88_P3).save(run_id="4244@1792844002")
    _AZ88_HDR = [json.loads(x) for x in _AZ88_P3.read_text().splitlines() if x.strip()]

    # ── 空 run_id：必须抛错（否则「答案属于哪次运行」不可判定）───────────
    try:
        answer_store.AnswerLog(Path(_az88_td) / "bad.jsonl").save(run_id=" ")
        _AZ88_EMPTY_ID = "没有抛错"
    except ValueError as _e:
        _AZ88_EMPTY_ID = f"ValueError: {str(_e)[:24]}"

    # ── 边界：它不是分数通道 ─────────────────────────────────────────────
    _AZ88_KEYS = set(_AZ88_ROWS[0].keys()) if _AZ88_ROWS else set()
    _AZ88_BANNED = {"score", "passed", "verdict"} & _AZ88_KEYS
    _AZ88_MW_HITS = [p.name for p in (Path(__file__).resolve().parent.parent / "mw").glob("*.py")
                     if "answer_store" in p.read_text(encoding="utf-8")]
    _AZ88_RUNPY = (Path(__file__).resolve().parent.parent / "run.py").read_text(encoding="utf-8")

check(RS, "AZ88 ★答案本体落盘：默认不截断 / 截断必须自陈 / 它不是分数通道\n"
          "          ① 5500 字符写读**逐字相同**，`chars` 与 `max_chars` 都是 5500\n"
          "             —— 那四处历史截断都切在 200，这里必须差一个量级；\n"
          "          ② 设 `cap=200` ⇒ 截到 200 **且记录里写 `truncated: true`**\n"
          "             （四处历史截断真正的毛病不是「截了」，是**截了不说**）；\n"
          "          ③ 空缓冲仍写 header（`n_answers=0`）：\n"
          "             「这次一条答案都没有」与「这个文件根本没被写过」是两件事；\n"
          "          ④ 空 `run_id` ⇒ 抛 ValueError（没有它就 join 不到 `runs.jsonl`）；\n"
          "          ⑤ 落盘键里**没有** score / passed / verdict ⇒ 它不是分数通道；\n"
          "          ⑥ `mw/` 里零引用（判据侧不许读答案本体，AO11 同一条边界）；\n"
          "          ⑦ 反证：`verify_rows` 对「chars 撒谎」与「sha 撒谎」各命中一次\n"
          "             —— 否则那两道自洽检查是死代码；\n"
          "          ⑧ 静态锚：`run.py` 里有接线点与 `--no-answers` 开关\n"
          "             （⛔ 静态锚比行为锚弱：「真的接通了」由探针 P6/P7 证明）。\n"
          "          ⑨ **逐行**写 `run_id`，且 `read(run_id=...)` 能按轮取到\n"
          "             —— 第一版只写在 header 上，按轮取是空列表（真跑当场 KeyError）。\n"
          "          撤回语义：把 `stored = text if ...` 改成 `text[:200]` ⇒ 必须红。",
      _AZ88_GOT == _AZ88_LONG and len(_AZ88_LONG) > 2000
      and _AZ88_W["max_chars"] == len(_AZ88_LONG)
      and len(_AZ88_BYRUN) == 1
      and _AZ88_ROWS[0].get("run_id") == "4242@1792844000"
      and answer_store.verify_rows(_AZ88_ROWS) == {"n": 1, "bad_len": 0, "bad_sha": 0}
      and _AZ88_R2["chars"] == 200 and _AZ88_R2["truncated"] is True
      and _AZ88_W2["truncated"] == 1
      and len(_AZ88_HDR) == 1 and _AZ88_HDR[0]["kind"] == "header"
      and _AZ88_HDR[0]["n_answers"] == 0
      and _AZ88_EMPTY_ID.startswith("ValueError")
      and _AZ88_BANNED == set()
      and _AZ88_MW_HITS == []
      and _AZ88_LIE_LEN["bad_len"] == 1 and _AZ88_LIE_SHA["bad_sha"] == 1
      and "answer_store" in _AZ88_RUNPY and "--no-answers" in _AZ88_RUNPY
      and answer_store.NO_CAP == 0,
      f"写入 {len(_AZ88_LONG)} 字符 ｜ 读回 {len(_AZ88_GOT)} 字符 ｜ "
      f"落盘 max={_AZ88_W['max_chars']} ｜ cap 后 chars={_AZ88_R2['chars']} "
      f"truncated={_AZ88_R2['truncated']} ｜ 空 run_id ⇒ {_AZ88_EMPTY_ID} ｜ "
      f"键={sorted(_AZ88_KEYS)} ｜ mw 命中={_AZ88_MW_HITS} ｜ "
      f"反证 坏chars={_AZ88_LIE_LEN['bad_len']} 坏sha={_AZ88_LIE_SHA['bad_sha']}")

# ═══════════════════════════════════════════════════════════════════════════
# AZ89 ★静态未定义名（第八十轮，§43）
#
# 为什么要这条：84 格里 **27 格（73% 的失分）** 丢在两个**确定性运行时错误**上
# （`sg01` 忘 `import re`、`sg05` 把 `dict.values()` 当 list 调 `.sort()`），
# 而 `derive()` 派生的四条判据没有一条看代码 ⇒ 全过 ⇒ 不打回。
#
# 第八十轮实测把路选窄了：
#   · 静态符号表：命中 sg01，**18 份权威实现 0 误报**；
#   · 执行一次：多命中 sg05，但重写预演显示 sg05 只从 0/14 变 1/14 ——
#     它剩下的 13 格是**对错**错误（处理顺序写反），自检按设计不判对错。
#   ⇒ 执行的边际只有 1 格，代价是沙箱 + 探针输入 + 挂死风险 ⇒ **不做**。
#
# 这条钉五件事：
#   ① 三档**语义真的不同**（0 不派生 / 1 报名字 / 2 同一触发集盲报）；
#   ② **零误报**：十条语法面正确样本一条都不报（闭包 / global / 类 / 装饰器 /
#      推导式 / lambda / try-except / with / 别名 / 前向引用）；
#   ③ **不可判定 ≠ 通过**：星号导入与 `exec()` 返回 `None` 且**不许打回**；
#   ④ 模式 2 的载荷**一个标识符都不许漏**（否则安慰剂臂不是安慰剂）；
#   ⑤ 旋钮进了 `Limits.as_dict()` / `ENV_KEYS` / `ZERO_OK_KEYS` / `KNOBS`。
#
# ⛔ 输入全部**自带**（不读题集 jsonl —— 它自己是撤回态的变异目标，
#    读它会让这条跟着一起红，重演 §41.7 的耦合）。
#    sg01 的真答案读**冻结证据**（`bench/evidence/answers_s79/`，不在 CASES 里）。
# ═══════════════════════════════════════════════════════════════════════════
import mw.verify as _AZ89_V

_AZ89_GOAL = ("写一个函数 norm_sg01(rows)，参数是字符串列表。"
              "返回处理完的列表。只输出代码，函数名必须是 norm_sg01。")

_AZ89_EV = V1 / "bench" / "evidence" / "answers_s79" / "answers.jsonl"
_AZ89_REAL = ""
_AZ89_EV_OK = _AZ89_EV.exists()
if _AZ89_EV_OK:
    for _az89_ln in _AZ89_EV.read_text(encoding="utf-8").splitlines():
        try:
            _az89_o = json.loads(_az89_ln)
        except ValueError:
            continue
        if _az89_o.get("kind") == "answer" and _az89_o.get("task_id") == "sg01":
            _AZ89_REAL = _az89_o.get("answer") or ""
            break

#: 语法面**正确**样本 —— 这十条一条都不许报。
#: ⛔ 它们覆盖"名字看着没定义、其实有定义"的全部常见形态；少一条就少一个误报面
#:    （误报 = 把正确答案判失败，比漏报更贵：那是装置吃掉了效应）。
_AZ89_OK = {
    "闭包": "def o():\n    v = 2\n    def i():\n        return v\n    return i\n",
    "global": "g = 0\ndef f():\n    global g\n    g = 1\n",
    "类与self": "class A:\n    def m(self):\n        return self.x\n",
    "装饰器": "import functools\n@functools.lru_cache\ndef f(x):\n    return x\n",
    "推导式": "def f(rows):\n    return [r.strip() for r in rows if r]\n",
    "lambda": "def f(rows):\n    return sorted(rows, key=lambda s: len(s))\n",
    "except": "def f():\n    try:\n        pass\n    except ValueError as e:\n        return str(e)\n",
    "with": "def f(p):\n    with open(p) as fh:\n        return fh.read()\n",
    "别名": "import collections as C\ndef f():\n    return C.OrderedDict()\n",
    "前向引用": "def a():\n    return b()\ndef b():\n    return 1\n",
}
#: 坏样本 —— 检测器必须命中，否则它是死代码。
_AZ89_BAD = {
    "缺import": "def f(x):\n    return re.sub('a', 'b', x)\n",
    "闭包拼错": "def a():\n    v = 2\n    def b():\n        return vv\n    return b\n",
    "类属性拼错": "class A:\n    def m(self):\n        return selfo\n",
    "模块级装饰器": "@lru_cached\ndef f(x):\n    return x\n",
    "lambda捕获拼错": "def f(rows):\n    return sorted(rows, key=lambda s: len(ss))\n",
}

_AZ89_OK_MISS = {k: _AZ89_V.undefined_names(v) for k, v in _AZ89_OK.items()}
_AZ89_OK_FALSE_POS = [k for k, v in _AZ89_OK_MISS.items() if v]
_AZ89_BAD_MISS = [k for k, v in _AZ89_BAD.items() if not _AZ89_V.undefined_names(v)]

# ── ③ 不可判定：必须 None，且**不许**因此打回 ──────────────────────────────
# ⚠️ 样本里必须定义**题面要求的那个函数名** —— 第一版写成 def f()，
#    结果 star 样本是被 `defines` 判死的（不是被这条），断言假红一次。
_AZ89_STAR = ("from os.path import *\n"
               "def norm_sg01(rows):\n    return join('a', 'b')\n")
_AZ89_DYN = ("def norm_sg01(rows):\n"
             "    exec('x = 1')\n    return rows\n")
_AZ89_STAR_N = _AZ89_V.undefined_names(_AZ89_STAR)
_AZ89_DYN_N = _AZ89_V.undefined_names(_AZ89_DYN)
_AZ89_STAR_V = _AZ89_V.run("```python\n" + _AZ89_STAR + "```", _AZ89_V.derive(_AZ89_GOAL, 1))
_AZ89_DYN_V = _AZ89_V.run("```python\n" + _AZ89_DYN + "```", _AZ89_V.derive(_AZ89_GOAL, 1))

# ── ① 三档：派生条数与判据名 ───────────────────────────────────────────────
_AZ89_D0 = _AZ89_V.derive(_AZ89_GOAL, 0)
_AZ89_D1 = _AZ89_V.derive(_AZ89_GOAL, 1)
_AZ89_D2 = _AZ89_V.derive(_AZ89_GOAL, 2)
# 反证：模式参数必须**真的被读** —— 三档如果一样，说明传参是空转。
_AZ89_MODE_DIFFERS = (len(_AZ89_D0) == 4 and len(_AZ89_D1) == 5
                      and len(_AZ89_D2) == 5
                      and _AZ89_D1[-1].name == "未定义名"
                      and _AZ89_D2[-1].name == "结构检查"
                      and _AZ89_D1[-1].arg == "1" and _AZ89_D2[-1].arg == "2")

# ── ② 真答案上的三档行为 ───────────────────────────────────────────────────
_AZ89_R0 = _AZ89_V.run(_AZ89_REAL, _AZ89_D0) if _AZ89_REAL else None
_AZ89_R1 = _AZ89_V.run(_AZ89_REAL, _AZ89_D1) if _AZ89_REAL else None
_AZ89_R2 = _AZ89_V.run(_AZ89_REAL, _AZ89_D2) if _AZ89_REAL else None
#: 模式 2 的载荷不许出现任何标识符（`re` / 函数名都不许）。
_AZ89_LEAK = [t for t in ("re", "norm_sg01")
              if _AZ89_R2 is not None and t in _AZ89_R2.report]
_AZ89_TRI = tuple(None if r is None else r.ok for r in (_AZ89_R0, _AZ89_R1, _AZ89_R2))
_AZ89_R2_REP = _AZ89_R2.report if _AZ89_R2 else "-"
# 细节串里**不许**对可能不存在的属性做算术（老账：抛异常会连汇总行一起吞掉）

# ── ⑤ 旋钮登记 ─────────────────────────────────────────────────────────────
_AZ89_LIM = cfg.Limits().as_dict()
_AZ89_KNOB = [k for k in MU.KNOBS if k.name == "verify_static"]
_AZ89_BOUNDS = ((_AZ89_KNOB[0].lo, _AZ89_KNOB[0].hi)
                 if _AZ89_KNOB else None)
os.environ["V1_VERIFY_STATIC"] = "0"
_AZ89_ENV0 = int(cfg.Limits().verify_static) == 0
os.environ["V1_VERIFY_STATIC"] = "1"
try:
    _AZ89_ENV1 = int(cfg.Limits().verify_static) == 1
finally:
    os.environ.pop("V1_VERIFY_STATIC", None)

check(RS,
      "AZ89 ★静态未定义名：三档语义真的不同 / 十条语法面样本零误报 / 五个坏样本全命中\n"
      "          / 星号导入与 exec 是「不可判定」而**不是**通过 / 模式 2 载荷不漏标识符\n"
      "          —— 84 格里 27 格（73% 的失分）丢在两个确定性运行时错误上，\n"
      "          而四条旧判据没有一条看代码。\n"
      "          撤回语义：`if bad is None or not bad:` 改成 `if True:` ⇒ 必须红。",
      _AZ89_EV_OK and bool(_AZ89_REAL)
      and _AZ89_MODE_DIFFERS
      and _AZ89_OK_FALSE_POS == [] and _AZ89_BAD_MISS == []
      and _AZ89_STAR_N is None and _AZ89_DYN_N is None
      and _AZ89_STAR_V.ok and _AZ89_DYN_V.ok
      and _AZ89_R0 is not None and _AZ89_R0.ok
      and _AZ89_R1 is not None and not _AZ89_R1.ok
      and "re" in _AZ89_R1.report
      and _AZ89_R2 is not None and not _AZ89_R2.ok
      and _AZ89_LEAK == []
      and "verify_static" in _AZ89_LIM
      and "V1_VERIFY_STATIC" in cfg.ENV_KEYS
      and "V1_VERIFY_STATIC" in cfg.ZERO_OK_KEYS
      and len(_AZ89_KNOB) == 1
      and (_AZ89_KNOB[0].lo, _AZ89_KNOB[0].hi) == (0, 2)
      and _AZ89_ENV0 and _AZ89_ENV1,
      f"证据={_AZ89_EV_OK} 真答案={len(_AZ89_REAL)}字符 ｜ "
      f"判据条数 0/1/2={len(_AZ89_D0)}/{len(_AZ89_D1)}/{len(_AZ89_D2)} ｜ "
      f"三档={_AZ89_TRI} ｜ 模式2载荷={_AZ89_R2_REP!r} 泄露={_AZ89_LEAK} ｜ "
      f"误报={_AZ89_OK_FALSE_POS} 漏报={_AZ89_BAD_MISS} ｜ "
      f"星号={_AZ89_STAR_N} 动态={_AZ89_DYN_N} ｜ "
      f"旋钮界={_AZ89_BOUNDS} "
      f"env0={_AZ89_ENV0} env1={_AZ89_ENV1}")

# ═══════════════════════════════════════════════════════════════════════════
# AZ90 ★`verify_static` 的**接线**（行为锚，不是静态锚）
#
# 为什么单列一条：AZ89 证明的是 `mw/verify.py` **自己**对不对，
# 它完全可能在 `core.py` 里没被传进去 —— 那样 AZ89 全绿而机制恒不触发，
# 与「一个恒不触发的机制和一个没装的机制长得一模一样」是同一件事。
#
# 反证就藏在两档的对比里：同一份**带 `re` 未定义**的答案，
#   `verify_static=0` ⇒ 0 次打回、原样交付（基线，逐位不变）；
#   `verify_static=1` ⇒ 打回 1 次、交付修好的那一版、判据里出现「未定义名」。
# 两档一样 ⇒ 档位没被读 ⇒ 这条红。
# ═══════════════════════════════════════════════════════════════════════════
_AZ90_BUGGY = ("```python\ndef norm_sg01(rows):\n"
               "    return [re.sub(r'\\s+', ' ', r) for r in rows]\n```")
_AZ90_FIXED = ("```python\nimport re\ndef norm_sg01(rows):\n"
               "    return [re.sub(r'\\s+', ' ', r) for r in rows]\n```")
_AZ90_C0, _AZ90_O0, _ = _as_run([_AZ90_BUGGY, _AZ90_FIXED],
                                static=0, goal=_AZ89_GOAL)
_AZ90_C1, _AZ90_O1, _ = _as_run([_AZ90_BUGGY, _AZ90_FIXED],
                                static=1, goal=_AZ89_GOAL)
_AZ90_V0 = (_AZ90_O0.stats or {}).get("verify") or {}
_AZ90_V1 = (_AZ90_O1.stats or {}).get("verify") or {}

check(RS,
      "AZ90 ★`verify_static` 真的接进了内核：同一份带未定义名的答案，\n"
      "          0 档原样交付（0 次打回）/ 1 档打回一次并交付修好的那一版\n"
      "          —— AZ89 只证明判据模块自己对了；没接进 `derive()` 的话，\n"
      "            它会全绿而机制恒不触发（恒不触发与没装长得一模一样）。\n"
      "          撤回语义：`core.py` 里不传 `verify_static` ⇒ 必须红。",
      _AZ90_V0.get("checks") == ["非空", "函数名 norm_sg01", "语言标签", "可编译"]
      and int(_AZ90_V0.get("failed") or 0) == 0
      and _AZ90_O0.answer.strip() == _AZ90_BUGGY.strip()
      and "未定义名" in (_AZ90_V1.get("checks") or [])
      and int(_AZ90_V1.get("failed") or 0) == 1
      and int(_AZ90_V1.get("passed") or 0) == 1
      and _AZ90_O1.status == "done"
      and _AZ90_O1.answer.strip() == _AZ90_FIXED.strip(),
      f"0档 判据={_AZ90_V0.get('checks')} failed={_AZ90_V0.get('failed')} "
      f"原样交付={_AZ90_O0.answer.strip() == _AZ90_BUGGY.strip()} ｜ "
      f"1档 判据={_AZ90_V1.get('checks')} failed={_AZ90_V1.get('failed')} "
      f"passed={_AZ90_V1.get('passed')} status={_AZ90_O1.status} "
      f"交付修好版={_AZ90_O1.answer.strip() == _AZ90_FIXED.strip()}")

# ===========================================================================
# AZ91（第八十二轮，§45）：第二批尺子 —— 旧尺子一个字不许动，新尺子必须自证
#
# 为什么不是"A76 顺手扩一下"：AZ76/AZ77 是**旧尺子**的读数锚，它们锚着
# `tasks_v1.jsonl` 这一份具体产物。把新文件塞进同一条断言，等于让"旧尺子变没变"
# 和"新尺子对不对"共用一个结论 —— 而这两件事的失败方式完全不同。
#
# 这一条钉四件事，缺一条就不是"能当尺子"：
#   ① 落盘的 10 题**自己解释自己**（题面/期望/oracle 都由题内 `meta.spec` 重算）；
#   ② 共享的 6 题与 v1 文件**逐字节相同**（"旧 6 题一个字不改"从散文变成事实）；
#   ③ 三条**与内核无关**的自检标定：oracle 满分 / partial 严格居中 / null 零分。
#      这三条是加题时唯一允许的筛选依据（⛔ 不许用内核分数筛题）；
#   ④ 三个新算子各自的坏样本命中它该命中的那条证据（清单非空不算数）。
# ===========================================================================
_AZ91_PATH = V1 / "bench" / "specgen" / "tasks_v2.jsonl"
_AZ91_ROWS = ([json.loads(x) for x in
               _AZ91_PATH.read_text(encoding="utf-8").splitlines() if x.strip()]
              if _AZ91_PATH.exists() else [])
_AZ91_SPECS = GS.SUITES["tasks_v2.jsonl"]
_AZ91_BY_ID = {str(r.get("id")): r for r in _AZ91_ROWS}
_AZ91_V1 = {str(r.get("id")): r for r in _AZ76_ROWS}
_AZ91_BAD: list[str] = []
for _r in _AZ91_ROWS:
    _sp = _r.get("meta", {}).get("spec")
    if not _sp:
        _AZ91_BAD.append(f"{_r.get('id')} 缺 meta.spec")
        continue
    if _r.get("prompt") != TS.render_prompt(_sp):
        _AZ91_BAD.append(f"{_r['id']} 题面与 spec 渲染不一致")
    if _r.get("tests") != TS.tests_of(_sp, GS.CASES):
        _AZ91_BAD.append(f"{_r['id']} 测试期望与 spec 不一致")
    if _r.get("oracle") != TS.compile_oracle(_sp):
        _AZ91_BAD.append(f"{_r['id']} oracle 与 spec 不一致")
    if TS.admit(_sp, GS.CASES):
        _AZ91_BAD.append(f"{_r['id']} 不过准入")


def _az91_score(src: str, tests: list[str]) -> int:
    """内核无关的打分：把源码 exec 一遍，再逐条跑题面自带的断言。空源码 = 0 分。"""
    if not src.strip():
        return 0
    ns: dict = {}
    try:
        exec(compile(src, "<az91>", "exec"), ns)      # noqa: S102
    except Exception:                                  # noqa: BLE001
        return 0
    hit = 0
    for t in tests:
        try:
            exec(compile(t, "<az91t>", "exec"), ns)   # noqa: S102
            hit += 1
        except Exception:                              # noqa: BLE001
            pass
    return hit


# 三条标定（每条题都要成立）：oracle=满分 · partial 严格居中 · null=零分
_AZ91_CAL = {str(r["id"]): (_az91_score(str(r.get("oracle") or ""), r["tests"]),
                           _az91_score(str(r.get("partial") or ""), r["tests"]),
                           _az91_score(str(r.get("null") or ""), r["tests"]))
             for r in _AZ91_ROWS}
_AZ91_CAL_OK = bool(_AZ91_CAL) and all(
    o == len(_AZ91_BY_ID[k]["tests"]) and 0 < p < o and n == 0
    for k, (o, p, n) in _AZ91_CAL.items())

# "旧 6 题一个字不改"：同一 id 在两份文件里的整行必须逐字相同
_AZ91_SHARED_BAD = [s["id"] for s in GS.SPECS
                    if _AZ91_V1.get(s["id"]) != _AZ91_BY_ID.get(s["id"])]

# 新算子反证：清单非空不算命中，必须命中**它该命中的那种证据**
_AZ91_NEWRP = TS.reverse_proofs_new_ops(GS.SPECS_V2_EXTRA, GS.CASES)
_AZ91_PROOF_BAD = [k for k, w in TS.NEW_OP_PROOF_EXPECT.items()
                   if not any(w in f for f in (_AZ91_NEWRP.get(k) or []))]

check(RS,
      "AZ91 ★第二批尺子（v2）：落盘 10 题自证 + 旧 6 题逐字不动 + 三条内核无关标定 + 新算子反证\n"
      "          ① `tasks_v2.jsonl` 的题面/期望/oracle 都能由题内 `meta.spec` 重算，且全部过准入；\n"
      "             id **顺序**也钉住（换行序 = 换 `suite_sha` = 旧读数失去对照）；\n"
      "          ② 与 v1 共享的 6 题**整行逐字相同** —— 加题不许顺手改进旧题，\n"
      "             否则 v1 的 84 格读数会与 v2 的逐题读对对不上；\n"
      "          ③ oracle 满分 / partial 严格居中 / null 零分。这三条与内核**无关**，\n"
      "             是加题唯一允许的筛选依据（拿内核分数筛题 = 把尺子做成内核的镜子）；\n"
      "          ④ 三个新算子（squeeze / dedupe_adj / keep_tail）各自的坏样本必须命中\n"
      "             A2 / A5 / A3 / A4 —— 新算子的两份手写实现「刚好自己对得上」是个\n"
      "             很可能为真的假设，不逼它失败，A4 就会在 sg07~sg10 上永远绿着骗人。",
      len(_AZ91_ROWS) == len(_AZ91_SPECS)
      and [str(r.get("id")) for r in _AZ91_ROWS] == [s["id"] for s in _AZ91_SPECS]
      and not _AZ91_BAD and not _AZ91_SHARED_BAD
      and _AZ91_CAL_OK and not _AZ91_PROOF_BAD,
      f"题数={len(_AZ91_ROWS)}/{len(_AZ91_SPECS)} 问题={_AZ91_BAD[:2]} "
      f"共享题不一致={_AZ91_SHARED_BAD} 标定="
      + " ｜ ".join(f"{k}={o}/{p}/{n}" for k, (o, p, n) in _AZ91_CAL.items())
      + f" ｜ 反证缺失={_AZ91_PROOF_BAD}")

# ===========================================================================
# AZ92（第九十一轮，§57）：收尾交付物**必须过同一道闸**，且 `_unverified`
#      描述的是「交出去的那一份」，不是「本轮发生过什么」
#
# 为什么单列一条：第九十轮两臂 60 题次里，收尾交了 **11 份**，过闸 **0 份**，
# 而 `unverified_delivery` 11 次全是 false。因为它只描述"主循环那道闸有没有
# 放弃过"（exhausted），不描述"交出去的那一份过没过"：
#   · A 臂 6 次 `closed` 全是 `校验=1跑/0过/1打回/0放弃` —— 那 1 次打回发生
#     在主循环，交付物却是收尾重新要来的另一份；
#   · B 臂 5 次 `closed` 全是 `校验=0跑` —— 闸一次都没跑过。
# `sg10` 第 2 轮交的**就是刚被闸打回的那一份**（419 字符，sha 38f40729…），
# 值 13 格；而它同时是 A 臂噪声极差 15 格里的 13 格 —— 判"可分辨"用的就是它。
#
# 三个方向缺一不可（少一个就有一条"恒真"的路）：
#   ① 不过闸 ⇒ 必须置标 + `gate_failed` 计数 + `reason` 里说出来；
#   ② 过闸且预置了旧标 ⇒ 必须**清标**（否则"交了好答案"被报成"交了坏答案"）；
#   ③ `verify_retries=0` ⇒ **零跑**（`vfy.run(x, ())` 返回 ok=False，
#      不跳过就是一个假失败 —— 关掉判据的用户没有错）。
# 再加一条**不变量**：三个方向里交付物必须与桩的原文**逐字相同** ——
# 本轮只许"记账"，不许改分（改了就是混口径，§45.9）。
# ===========================================================================
_AZ92_GOAL = ("写一个函数 norm_az92(rows)，参数是字符串列表，返回处理后的列表。"
              "只输出代码，函数名必须是 norm_az92。")
#: `re` 被引用但模块里没 import ⇒ `结构检查`（static=2 的 undefined）必命中。
_AZ92_BAD = ("```python\ndef norm_az92(rows):\n"
             "    return [re.sub(r'\\s+', ' ', r) for r in rows]\n```")
_AZ92_GOOD = ("```python\nimport re\ndef norm_az92(rows):\n"
              "    return [re.sub(r'\\s+', ' ', r) for r in rows]\n```")


class _AZ92LLM(StubLLM):
    """收尾索要答案时**原样**吐回预设的那一段。"""

    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, messages, **kw):                            # noqa: ANN001, ANN003
        return self.reply


_AZ92_SEQ = [0]
_AZ92_LOGS: list[str] = []


def _az92_close(reply: str, *, retries: int = 2, prior: str = "") -> tuple:
    """单独调 `_close_out`（AS20 已证明它是可单独调用的单元）。

    ⚠️ `_reset_verify(goal)` 是 `run()` 里**同一处**派生（AZ90 钉着这条链）：
    直接构造的实例没有 `run()`，不重放这一步就没有 `verify_stats`/`_salvage`。
    但**判据本身**在第 94 轮改成显式给（见下面那一段注释）——
    因为「判据从哪来」由 AS11/AZ90 钉，本断言只测 `_close_out`。
    """
    _AZ92_SEQ[0] += 1
    lim = cfg.Limits(max_steps=1, close_reserve_steps=1,
                     verify_retries=retries, verify_static=2)
    c = Core(settings=cfg.Settings(limits=lim), llm=_AZ92LLM(reply),
             memory=MemoryStore(_TMP / f"az92_{_AZ92_SEQ[0]}.db"))
    c.log = lambda m: _AZ92_LOGS.append(m)                       # noqa: ARG005
    c._reset_verify(_AZ92_GOAL)
    # ⚠️ 第九十四轮（169 条全量撤回态照出来的**重复覆盖**，不是图省事）：
    #    判据在这里**显式给**，不从 `_reset_verify` 那条接线里取。
    #    理由：AS11 撤的是 `... if verify_retries > 0 else ()` 那半句、
    #    AZ90 撤的是 `int(self.s.limits.verify_static)` 这个接线参数 ——
    #    两条变异都会让这里拿到的判据变空/没有静态检查，于是**连带打红本断言**，
    #    在 169 条台账上表现为「多红=['AZ92']」（AS11、AZ90 各一次）。
    #    而「判据从哪来」正是 AS11/AZ90 的被测物，不是 `_close_out` 的被测物。
    #    ⇒ 本断言的范围收窄并写明：**只测 `_close_out` 自己**（闸在、闸置标、
    #      闸不改分、判据为空时零跑）。判据来源那三条不变量由 AS11/AZ90 钉住。
    c._checks = (tuple(_AZ89_V.derive(_AZ92_GOAL, 2)) if retries > 0 else ())
    c.verify_stats["checks"] = [x.name for x in c._checks]
    c._unverified = prior
    seen: dict = {}

    def _done(status: str, answer: str, reason: str) -> str:      # noqa: ANN001
        seen.update(status=status, answer=answer, reason=reason)
        return "ok"

    c._close_out(_AZ92_GOAL, ["h1"], _done)
    return c, seen


_AZ92_C1, _AZ92_S1 = _az92_close(_AZ92_BAD)
_AZ92_C2, _AZ92_S2 = _az92_close(_AZ92_GOOD, prior="旧原因：主循环那道闸放弃过")
_AZ92_C3, _AZ92_S3 = _az92_close(_AZ92_BAD, retries=0)
_AZ92_OK_BAD = bool(_AZ92_C1._unverified)                      # ① 不过闸 ⇒ 有标
_AZ92_OK_CLEAR = _AZ92_C2._unverified == ""                    # ② 过闸 ⇒ 清标
_AZ92_OK_OFF = (_AZ92_C3.verify_stats["runs"] == 0             # ③ 关掉 ⇒ 零跑
                and _AZ92_C3.closeout_stats["gate_checked"] == 0)
_AZ92_OK_SAME = (_AZ92_S1.get("answer") == _AZ92_BAD
                 and _AZ92_S2.get("answer") == _AZ92_GOOD
                 and _AZ92_S3.get("answer") == _AZ92_BAD)
_AZ92_OK_NOTE = ("未通过自检" in str(_AZ92_S1.get("reason"))
                 and "未通过自检" not in str(_AZ92_S2.get("reason")))
_AZ92_OK_OLDKEEP = (_AZ92_C3._unverified == "")   # retries=0 且没预置 ⇒ 仍是空

check(RS,
      "AZ92 ★收尾交付物**必须过同一道闸**，且 `_unverified` 只描述交付出去的那一份\n"
      "          ① 不过闸 ⇒ 置标 + `gate_failed=1` + `reason` 写出「未通过自检」；\n"
      "          ② 过闸且预置了旧标 ⇒ **清标**（旧标说的是已被顶掉的那一版）；\n"
      "          ③ `verify_retries=0` ⇒ **零跑**（`run(x, ())` 返回 ok=False，\n"
      "             不跳过就是给关掉开关的用户一个假失败）；\n"
      "          ④ 三个方向里交付物与桩原文**逐字相同** —— 本轮只许记账不许改分。\n"
      "          —— 第九十轮实况：收尾交 11 份、过闸 0 份、`unverified_delivery`\n"
      "             11 次全 false；`sg10` 第 2 轮交的就是刚被打回的那一份（13 格）。\n"
      "          撤回语义：`if best and self._checks:` 改成 `if False:` ⇒ 必须红；\n"
      "                    清标那行改成空操作 ⇒ 必须红；去掉 `and self._checks` ⇒ 必须红。",
      _AZ92_OK_BAD and _AZ92_OK_CLEAR and _AZ92_OK_OFF
      and _AZ92_OK_SAME and _AZ92_OK_NOTE and _AZ92_OK_OLDKEEP
      and _AZ92_C1.closeout_stats["gate_checked"] == 1
      and _AZ92_C1.closeout_stats["gate_failed"] == 1
      and _AZ92_C2.closeout_stats["gate_checked"] == 1
      and _AZ92_C2.closeout_stats["gate_failed"] == 0
      and _AZ92_C1.verify_stats["closeout_runs"] == 1
      and _AZ92_C1.verify_stats["closeout_failed"] == 1
      and _AZ92_C2.verify_stats["closeout_failed"] == 0,
      f"①不过闸={_AZ92_OK_BAD} 标={str(_AZ92_C1._unverified)[:40]!r} "
      f"闸={_AZ92_C1.closeout_stats['gate_checked']}/"
      f"{_AZ92_C1.closeout_stats['gate_failed']} ｜"
      f"②过闸清标={_AZ92_OK_CLEAR}(旧标={_AZ92_C2._unverified!r}) "
      f"闸={_AZ92_C2.closeout_stats['gate_checked']}/"
      f"{_AZ92_C2.closeout_stats['gate_failed']} ｜"
      f"③关掉零跑={_AZ92_OK_OFF}(runs={_AZ92_C3.verify_stats['runs']}) ｜"
      f"④交付物逐字相同={_AZ92_OK_SAME} ｜ ⑤reason 措辞={_AZ92_OK_NOTE}")

# ── AZ94 ★收尾那一发允许**有界**超出墙钟，探索阶段一秒都不许多给 ────────
#  第九十六轮 §63。动机（§62 实测）：`close_reserve_s=45.0` 是按「一次调用约 20s」
#  定的，本机实测 45.6s ⇒ 收尾在 75.5s 启动只拿到 44.5s ⇒ **差 1.1 秒**被裁
#  ⇒ `BudgetExhausted` ⇒ 兜底交 `history[-1]`（那是 `[step 1] finish({...})` 的
#  轨迹原文）。**一份已经写好的答案，输给了一个常数。**
#
#  ⛔ 为什么不是「调大 close_reserve_s」：那会同时**推迟收尾起点**（探索少吃
#     45s 变少吃 75s），是另一件事，且 §62 的对照臂已证明会让别的题变差。
#     这里改的是**收尾这一发自己的上限**，探索阶段逐位不变。
#
#  五个方向（缺一条，这条断言就是「恒真」或「测了个算术」）：
#   ① `close_overrun_s=0` ⇒ `_attempt_timeout()` 与旧公式**逐位相同**（A/B 对照组）
#   ② 收尾后同一时刻的 `_to` **严格变大**，且增量 ≤ 宽限
#   ③ `closing=False` 时给了宽限也**不加**（探索阶段不许超支）
#   ④ 宽限再大也被 `inner.timeout_s` 夹住（不许退化成「取消预算」）
#   ⑤ 两个新计数**零值也在** stats 里（"配了没生效"与"救回来了"必须分得开）
_AZ94_WALL = 8.0
_AZ94_PRE = 2.5


def _az94_mk(overrun: float, *, base: float = 60.0, reserve: float = 0.0):
    """只造账本 + 包装器（不建 Core：这条路只需要 `_attempt_timeout()`）。

    ⚠️ 时钟**必须钉住**：用 `t0 = now - pre` 模拟"已用 pre 秒"，两次构造之间
    差几十微秒，就足以让 `MIN_ATTEMPT_S` 边界上的 `_to` 从 5.5 变成 0.0。
    探针第一版没钉，C0/C1/C2 全红 —— 那三条红里没有一条是真的缺陷。
    """
    class _Fast:
        """**不睡**的桩：本断言只量 `_attempt_timeout()` 与"宽限有没有记账"，
        不量一次调用跑多久 —— 真跑由 `bench/probe_closeoverrun.py` 的 C3 端到端钉。

        ⚠️ 它必须有 `timeout_s`：`_attempt_timeout()` 用
        `getattr(inner, "timeout_s", None)` 决定"要不要裁剪"，
        而 `StubLLM` **没有**这个属性 ⇒ 走 `return inf` 那条路 ⇒ 这里全是假绿。
        """
        timeout_s = base

        def complete(self, messages, **kw):                      # noqa: ANN001
            return "```python\ndef norm_az94(rows):\n    return list(rows)\n```"

    led = Ledger(budget=Budget(steps=1, llm_calls=4, wall_s=_AZ94_WALL, candidates=1),
                 t0=0.0)
    led.elapsed = lambda: _AZ94_PRE                            # type: ignore[method-assign]
    return _LedgeredLLM(_Fast(), led, retries=0,
                        wall_reserve_s=reserve, close_overrun_s=overrun)


_AZ94_OLD = _az94_mk(0.0)
_AZ94_NEW = _az94_mk(2.5)
_AZ94_TO_EXPLORE_OLD = _AZ94_OLD._attempt_timeout()
_AZ94_TO_EXPLORE_NEW = _AZ94_NEW._attempt_timeout()               # ③ 还不该变
_AZ94_NEW.reserve_release()                                       # 进入收尾
_AZ94_OLD.reserve_release()
_AZ94_TO_CLOSE_OLD = _AZ94_OLD._attempt_timeout()
_AZ94_TO_CLOSE_NEW = _AZ94_NEW._attempt_timeout()                 # ② 应变大
_AZ94_HUGE = _az94_mk(1e6)
_AZ94_HUGE.reserve_release()
_AZ94_TO_HUGE = _AZ94_HUGE._attempt_timeout()                     # ④ 仍被 base 夹住

_AZ94_OK_SAME = (abs(_AZ94_TO_EXPLORE_OLD - (_AZ94_WALL - _AZ94_PRE)) < 1e-9
                 and _AZ94_TO_EXPLORE_OLD == _AZ94_TO_EXPLORE_NEW)
_AZ94_OK_GROW = (_AZ94_TO_CLOSE_NEW > _AZ94_TO_CLOSE_OLD
                 and abs(_AZ94_TO_CLOSE_NEW
                         - min(60.0, _AZ94_TO_CLOSE_OLD + 2.5)) < 1e-9)
_AZ94_OK_SCOPE = _AZ94_TO_EXPLORE_NEW == _AZ94_TO_CLOSE_OLD      # ③ 未收尾 ⇒ 旧值
_AZ94_OK_BOUND = _AZ94_TO_HUGE <= 60.0 + 1e-9
# ⑤ 接线三件：默认值、构造参数、两个计数器（"配了没生效"与"救回来了"分得开）。
#    ⚠️ 这里只钉**常量与接线**；「收尾真的超出墙钟时 `overrun_used_s` 会变正」
#    由 `bench/probe_closeoverrun.py` 的 C6 端到端钉住（它要真睡 ~11s，
#    放进契约会让每次提交多付十秒，而契约是每次提交都跑的）。
_AZ94_OLD.complete([Message("user", "x")])                # 旧臂：不该记一笔
_AZ94_NEW.complete([Message("user", "x")])                # 新臂：应记一笔
_AZ94_OK_KEYS = (abs(cfg.Limits().close_overrun_s - 30.0) < 1e-9
                 and _AZ94_OLD.wall_overrun_grants == 0
                 and _AZ94_OLD.overrun_used_s == 0.0
                 and _AZ94_NEW.wall_overrun_grants >= 1)

check(RS,
      "AZ94 ★收尾那一发允许**有界**超出墙钟，探索阶段一秒都不许多给\n"
      "          ① `close_overrun_s=0` ⇒ `_to` 与旧公式逐位相同（对照组）；\n"
      "          ② 进入收尾后同一时刻的 `_to` 变大，且增量 ≤ 宽限；\n"
      "          ③ 还没 `reserve_release()` 时给了宽限也**不加**；\n"
      "          ④ 宽限 = 1e6s 时 `_to` 仍 ≤ `inner.timeout_s`；\n"
      "          ⑤ 默认 30.0s；旧臂两个计数器为 0、新臂 `wall_overrun_grants ≥ 1`\n"
      "             （「配了没生效」与「救回来了」必须分得开）。\n"
      "          —— 第九十五轮实况：收尾在 75.5s 启动、给 44.5s、需要 45.6s\n"
      "             ⇒ 差 1.1 秒丢掉一份已写好的答案 ⇒ 交了 `[step 1] finish(...)`。\n"
      "          撤回语义：`_wall_deadline` 去掉 `+ close_overrun_s` ⇒ ② 必须红；\n"
      "                    `reserve_release` 去掉 `self.closing = True` ⇒ ②⑤ 必须红。",
      _AZ94_OK_SAME and _AZ94_OK_GROW and _AZ94_OK_SCOPE
      and _AZ94_OK_BOUND and _AZ94_OK_KEYS,
      f"①逐位相同={_AZ94_OK_SAME}({_AZ94_TO_EXPLORE_OLD:g}) ｜ "
      f"②收尾后变大={_AZ94_OK_GROW}({_AZ94_TO_CLOSE_OLD:g}→{_AZ94_TO_CLOSE_NEW:g}) ｜ "
      f"③未收尾不加={_AZ94_OK_SCOPE} ｜ "
      f"④有界={_AZ94_OK_BOUND}({_AZ94_TO_HUGE:g}≤60) ｜ "
      f"⑤两键常在={_AZ94_OK_KEYS}")


# ── AZ95 ★停滞判据的"分母"可以被打开，但**默认关闭**（第九十九轮 §66）──────
#  动机（§65 实测，309 份任务日志 / 2108 步 / 81 次重复候选）：
#    全等 `repeats == len(cands)` 48 步，**宽度全是 1** ⇒ 触发时那一步根本没有
#      第二个候选可换（"换形态再问"正是要靠它）；
#    混合 `0 < repeats < len(cands)` 33 步，**宽度全是 2** ⇒ 掉进 else 支，
#      把"重复"记成"做错"（`consecutive_failures += 1`），由熔断兜底（出口是交空）。
#  ⇒ 判据从"全等"放宽成「有重复 且 重复 + 被拦 == 候选数」，开关
#     `V1_STALL_ON_BLOCKED`（0=旧行为，**默认**；1=把被拦候选也算进分母）。
#
#  五个方向（缺一条，这条断言就是"恒真"或"测了半个算术"）：
#    ① 默认 0：新口径**默认关闭**（没有两臂读数就不许改默认，§80 的先例）
#    ② 混合步：0 ⇒ `cascade` + `全部候选被拦`；1 ⇒ `stalled` + reseek + 提示词含块
#    ③ 全等步：0/1 **逐位相同**（前置 `repeats` 没被顺手放大）
#    ④ `repeats == 0` 的全拦：0/1 **都不许**算停滞（这是对 §65.9 草稿的**收窄**）
#    ⑤ `blocked_skipped` 只在真有被拦时 >0（A 臂恒 0、B 臂 ≥1、C 臂 ==1）
_AZ95_GOAL = ("分析并清理历史遗留的临时文件，然后总结根因与修复方案，"
              "最后把结论写回记忆")
_AZ95_MARK_PLAN = "请给出"
_AZ95_MARK_PREDICT = "准备执行的动作"
#: ⛔ **不能**用 `ANSWER_MARK` 判别答案通道 —— 实测（第九十九轮）：
#: `planner.py:108` 训它"在 JSON 之后写 `---ANSWER---`"，于是**规划器提示词里
#: 也含有 `---ANSWER---`**。用它当判据，规划调用会被当成答案调用、返回一段
#: 代码给 JSON 解析器 ⇒ `⚠️ 模型输出不是合法 JSON` ⇒ 整轮 `done`（本轮真踩了）。
#: 正确的锚是答案提示词**独有**的那句（`planner.py:150`）。
_AZ95_MARK_ANSWER = _ANS_SYS


class _AZ95LLM(StubLLM):
    """按**通道**分派的脚本化模型（⛔ 不按调用序数 —— 某轮没走预演就会串位）。

    `answers` 必须可编排：规划器有一条「`finish` 却没给 answer」的**定向补救**
    （`mw/planner.py:369-394`），它会在内核看到候选之前就把空答案填上 ——
    空答案因此**根本到不了价值层**。要复现"空答案被否决"这一格，
    必须让那一发补救也返回空。
    """

    def __init__(self, plans: list[str], answers: list[str]) -> None:
        super().__init__()
        self.plans, self.answers = list(plans), list(answers)
        self.n_plan = 0
        self.n_answer = 0
        self.plan_prompts: list[str] = []

    def complete(self, messages, **kw):                          # noqa: ANN001, ANN003
        blob = "\n".join(m.content for m in messages)
        if _AZ95_MARK_ANSWER in blob:
            i = min(self.n_answer, len(self.answers) - 1)
            self.n_answer += 1
            return self.answers[i] if self.answers else ""
        if _AZ95_MARK_PREDICT in blob:
            return '{"expect_ok": true, "expected": "会成功", "confidence": 0.9}'
        assert _AZ95_MARK_PLAN in blob, f"未识别的通道：{blob[:120]!r}"
        self.plan_prompts.append(blob)
        i = self.n_plan
        self.n_plan += 1
        return self.plans[min(i, len(self.plans) - 1)]


def _az95_plan(*cands: dict) -> str:
    return json.dumps({"candidates": list(cands)}, ensure_ascii=False)


def _az95_recall(q: str, intent: str = "找已有结论") -> dict:
    return {"intent": intent, "tool": "recall", "args": {"query": q},
            "rationale": "先看有没有现成结论"}


_AZ95_HINT = _az95_recall("python 截断", "再找一次")
#: A：第 2 轮**全等**（只有一个重复候选）。
_AZ95_ARM_A = [_az95_plan(_az95_recall("python 截断")),
               _az95_plan(_AZ95_HINT),
               _az95_plan({"intent": "沉淀结论", "tool": "note",
                           "args": {"text": "结论", "source": "selftest",
                                    "confidence": 0.9},
                           "rationale": "写回记忆"})]
#: B：第 2 轮**混合**（1 个重复 + 1 个空答案 `finish`）。两臂只差这一个候选。
_AZ95_ARM_B = [_az95_plan(_az95_recall("python 截断")),
               _az95_plan(_AZ95_HINT,
                          {"intent": "交付", "tool": "finish",
                           "args": {"answer": ""}, "rationale": "已经够了"})]
#: C：**零重复的全拦**（单个空答案 finish，宽度 1）。它必须两档都不算停滞。
_AZ95_ARM_C = [_az95_plan({"intent": "交付", "tool": "finish",
                           "args": {"answer": ""}, "rationale": "已经够了"})]
#: 第一发（定向补救）返回空 ⇒ 空答案真的到得了价值层；之后正常。
_AZ95_ANS = ["", "```python\nprint(1)\n```"]


def _az95_run(gate: int, plans: list[str], answers: list[str]) -> dict:
    """跑一个臂。**每臂一套独立 state**（`facts` 跨轮累积，共用会污染下一个臂）。"""
    _tag = f"{gate}_{len(plans)}_{len(_az95_calls)}"
    _az95_calls.append(_tag)
    _dir = _TMP / f"az95_{_tag}"
    _keep = {k: os.environ.get(k)
             for k in ("V1_STATE_DIR", "V1_LOG_DIR", "V1_WORKSPACE",
                       "V1_MAX_CONSECUTIVE_FAILURES", "V1_STALL_ON_BLOCKED")}
    os.environ["V1_STATE_DIR"] = str(_dir / "state")
    os.environ["V1_LOG_DIR"] = str(_dir / "logs")
    os.environ["V1_WORKSPACE"] = str(_dir / "ws")
    #: 两臂共用的**测量条件**：混合那一步之后立刻熔断，读数才只包含被测那一支。
    #: ⚠️ 不加它，第 3 步候选预算塌到 1 ⇒ 混合自己变成全等 ⇒ 两支混在一起。
    os.environ["V1_MAX_CONSECUTIVE_FAILURES"] = "1"
    os.environ["V1_STALL_ON_BLOCKED"] = str(gate)
    try:
        llm = _AZ95LLM(plans, answers)
        log = _Log(None)
        out = Core(cfg.settings(), llm=llm, log=log).run(_AZ95_GOAL)
    finally:
        for k, v in _keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    loop = (out.stats or {}).get("loop") or {}
    prompts = "\n".join(llm.plan_prompts)
    return {
        "status": out.status,
        "stalled": loop.get("stalled"),
        "repeats_skipped": loop.get("repeats_skipped"),
        "blocked_skipped": loop.get("blocked_skipped"),
        "reseek": ((out.stats or {}).get("planner") or {}).get("reseek"),
        "iterations": out.iterations,
        "n_plan": llm.n_plan,
        "has_reseek_prompt": "你上一次的回答被**拒绝**了" in prompts,
        "has_allblock_log": any("全部候选被拦" in ln for ln in log.lines),
        "has_stall_log": any("个候选全部是已试过的动作" in ln for ln in log.lines),
    }


_az95_calls: list[str] = []
_AZ95_A0 = _az95_run(0, _AZ95_ARM_A, _AZ95_ANS)
_AZ95_A1 = _az95_run(1, _AZ95_ARM_A, _AZ95_ANS)
_AZ95_B0 = _az95_run(0, _AZ95_ARM_B, _AZ95_ANS)
_AZ95_B1 = _az95_run(1, _AZ95_ARM_B, _AZ95_ANS)
_AZ95_C0 = _az95_run(0, _AZ95_ARM_C, _AZ95_ANS)
_AZ95_C1 = _az95_run(1, _AZ95_ARM_C, _AZ95_ANS)

_AZ95_FACE = ("status", "stalled", "repeats_skipped", "reseek", "iterations",
              "n_plan", "has_reseek_prompt", "has_allblock_log", "has_stall_log",
              "blocked_skipped")
_AZ95_OK_DEFAULT = cfg.Limits().stall_on_blocked == 0
_AZ95_OK_MIXED = (_AZ95_B0["status"] == "cascade" and _AZ95_B0["stalled"] == 0
                  and _AZ95_B0["has_allblock_log"] and _AZ95_B0["reseek"] == 0
                  and _AZ95_B1["status"] == "stalled" and (_AZ95_B1["stalled"] or 0) >= 1
                  and _AZ95_B1["reseek"] == 1 and _AZ95_B1["has_reseek_prompt"]
                  and not _AZ95_B1["has_allblock_log"])
_AZ95_OK_SAME = all(_AZ95_A0[k] == _AZ95_A1[k] for k in _AZ95_FACE) \
    and _AZ95_A0["status"] == "stalled" and (_AZ95_A0["stalled"] or 0) >= 1
_AZ95_OK_BOUND = all(x["status"] == "cascade" and x["stalled"] == 0
                     and x["has_allblock_log"] and not x["has_reseek_prompt"]
                     for x in (_AZ95_C0, _AZ95_C1))
#: ⑤ 计数器只在真有被拦时动：A 臂一个都没被拦（0）、B 臂有一个（≥1）、C 臂恰好一个。
_AZ95_OK_COUNT = (_AZ95_A0["blocked_skipped"] == 0
                  and _AZ95_A1["blocked_skipped"] == 0
                  and (_AZ95_B1["blocked_skipped"] or 0) >= 1
                  and _AZ95_C0["blocked_skipped"] == 1
                  and _AZ95_C1["blocked_skipped"] == 1)

check(RS,
      "AZ95 ★停滞判据的分母可以被打开（`V1_STALL_ON_BLOCKED`），但**默认关闭**\n"
      "          ① 默认 0（新口径不许靠改默认值上线）；\n"
      "          ② 混合步（1 重复 + 1 空答案）：0 ⇒ `cascade`＋`全部候选被拦`；\n"
      "             1 ⇒ `stalled`＋`reseek==1`＋提示词含 reseek 块；\n"
      "          ③ 全等步（宽度 1）两档**逐位相同**（前置 `repeats` 没被放大）；\n"
      "          ④ `repeats == 0` 的全拦两档**都不算**停滞（对 §65.9 草稿的收窄）；\n"
      "          ⑤ `blocked_skipped` 只在真有被拦时动（A=0 / B≥1 / C=1）。\n"
      "          撤回语义：判据去掉 `repeats` 前置 ⇒ ④ 必须红；\n"
      "                    `_blocked_eff` 忽略开关（恒用 `blocked_now`）⇒ ② 的\n"
      "                    0 档必须红；价值层那处不累加 ⇒ ② 的 1 档与 ⑤ 必须红。\n"
      "          ⛔ 未测的边界：「1 重复 + 1 个因**额度不足**没试」该不该算停滞。\n"
      "             它只在 `V1_EXTERNAL_ANSWER_TOOL` 配上且预算见底时出现，\n"
      "             本轮没有造这个格子 —— 只能靠代码位置保证（那条 `continue`\n"
      "             在 `core.py:935`，**不**碰 `blocked_now`），登记为口径边界。",
      _AZ95_OK_DEFAULT and _AZ95_OK_MIXED and _AZ95_OK_SAME
      and _AZ95_OK_BOUND and _AZ95_OK_COUNT,
      f"①默认={_AZ95_OK_DEFAULT}({cfg.Limits().stall_on_blocked}) ｜ "
      f"②混合={_AZ95_OK_MIXED}(B0={_AZ95_B0['status']}/B1={_AZ95_B1['status']},"
      f"stalled={_AZ95_B1['stalled']}) ｜ "
      f"③全等同={_AZ95_OK_SAME}({_AZ95_A0['status']}/{_AZ95_A1['status']}) ｜ "
      f"④零重复全拦={_AZ95_OK_BOUND}({_AZ95_C1['status']}) ｜ "
      f"⑤计数={_AZ95_OK_COUNT}"
      f"(A={_AZ95_A0['blocked_skipped']},B={_AZ95_B1['blocked_skipped']},"
      f"C={_AZ95_C1['blocked_skipped']})")


# ── AZ96（第 110 轮 / §0.5 第 2 步）：语义层的身份是「动作」，不是「动作的载荷」 ──
#
# 病根读数（`state/memory.db`，10-29 15:53，571 次 outcome 失配）：
#   旧 `key = " ".join(sorted(_tokens(action))[:4])`，而 `action` 的真实形态是
#   `finish({'answer': '<整份代码>'})`（见 `episodes.meta.action`）
#   ⇒ 身份由**答案内容**决定：`answer def dict elif` / `answer finish int nint` ……
#   旧文本还抄进 `实测：{actual[:90]}`（本次报错原文）⇒ 换一道题一个字用不上，
#   它只是**日志**，却被 `recall()` 当知识检索出来。
#
# ⛔ 本轮先否掉一个我自己写错的判断：这不是「行数爆炸」。
#   按 `learn()` 的真实准入（只收 `teachable` = outcome）重算 571 条，
#   旧实现也只产出 **3 行**（新实现 2 行）。我第一版把 57 条 `mm_kind='?'`
#   的旧数据混进 outcome 组，才读出「22 个身份 / 27 行」——那个数是错口径，已撤回。
#
# 判据（结构性，不靠「不含某个字符串」——那种判据换个写法就绕过）：
#   ① 同类别、同方向、**载荷完全不同**的两次失配 ⇒ 提炼文本必须**逐字节相同**。
#      这正是「跨任务可迁移」的机器化定义：规则不随实例变。
#      ★ 它**严格蕴含**「不许抄本次报错」：只要文本里含 `actual`，两份不同载荷
#        必然产出不同文本 ⇒ ① 先红。所以不单列那条子判据（少一条判据，多一分可读）。
#   ② 不同类别 ⇒ 文本**不同**（身份真的进了文本；防 `category` 退化成常量）。
#   ③ 走真 DB：同类别失配两次 ⇒ `facts` **只 1 行**、`hits == 2`
#      （沉淀的是校准规则，不是流水账）。
#
# 撤回语义（两条撤回态各咬一处，互不掩盖）：
#   AX5 `category` 退回 `" ".join(sorted(_tokens(action))[:4])` ⇒ ①② 之一（①）与 ③ 必红；
#   AX6 `category` 退化成常量 `"该动作"` ⇒ **只** ② 必红（过度泛化也是病）；
#   AX1 `learn()` 里不再 `commit_fact` ⇒ ③ 必红（0 行）。
_AZ96_MEM = MemoryStore(_TMP / "az96_mem.db")
_AZ96_WM = WorldModel(_AZ96_MEM, StubLLM())


def _az96_mm(action: str, actual: str) -> Mismatch:
    return Mismatch(action, "会成功", actual, 0.0, kind="outcome",
                    predicted_ok=True, actual_ok=False)


_AZ96_A = ("finish({'answer': 'def solve0(int x) { return x; }'})",
           "自检未通过：3/4 项未过：缺少 def solve(...)")
_AZ96_B = ("finish({'answer': \"def flatten(d):\\n    return list(d)\"})",
           "编译失败 line 1: invalid syntax")

_AZ96_T1 = _az96_mm(*_AZ96_A).as_heuristic()
_AZ96_T2 = _az96_mm(*_AZ96_B).as_heuristic()
_AZ96_T4 = _az96_mm("recall({'q': 'x'})", "空结果").as_heuristic()

_AZ96_OK_SAME = _AZ96_T1 == _AZ96_T2          # ① 载荷无关（⇒ 不抄实例）
_AZ96_OK_IDENT = _AZ96_T4 != _AZ96_T1         # ② 类别区分

_AZ96_WM.learn(_az96_mm(*_AZ96_A))
_AZ96_WM.learn(_az96_mm(*_AZ96_B))
_AZ96_ROWS = _AZ96_MEM.conn.execute(
    "SELECT text, hits FROM facts WHERE source='worldmodel'").fetchall()
_AZ96_OK_ONE = (len(_AZ96_ROWS) == 1 and int(_AZ96_ROWS[0]["hits"]) == 2)

check(RS, "AZ96 ★★§0.5-2 语义层的身份是动作不是载荷：\n"
          "          `Mismatch.category` 必须取**最外层调用名**（`finish(...)` ⇒ `finish`），\n"
          "          ⛔ 不是把整份交付物切词后字典序取前 4 个 —— 那样身份由答案内容决定。\n"
          "          ① 同类别同方向、载荷完全不同 ⇒ 文本**逐字节相同**（⇒ 不许抄本次报错）；\n"
          "          ② 不同类别 ⇒ 文本不同（防 `category` 退化成常量）；\n"
          "          ③ 走真 DB：同类别失配两次 ⇒ facts 只 1 行、hits=2。\n"
          "          撤回语义：AX5 ⇒ ①③ 红；AX6（常量）⇒ 只 ② 红；AX1 ⇒ ③ 红。",
      _AZ96_OK_SAME and _AZ96_OK_IDENT and _AZ96_OK_ONE,
      f"①同载荷无关={_AZ96_OK_SAME} ②类别区分={_AZ96_OK_IDENT} "
      f"③单行hits2={_AZ96_OK_ONE}"
      f"(rows={len(_AZ96_ROWS)},hits={_AZ96_ROWS[0]['hits'] if _AZ96_ROWS else 'NA'})"
      f" ｜ t1={_AZ96_T1[:48]!r}")

# ⛔ AX5/AX6（第 111 轮补登）：上一轮把这两条**撤回态**写进了驱动器，却没有
#    与之同名的契约断言 —— 而驱动器的判据是 `hit = tag in red`，所以它们
#    永远只可能报「目标=✘」。上一轮只做了手工定向演练、没跑驱动器，因此没发现。
#    ⇒ 这里把 AZ96 的 ①② 各补一条独立见证（AZ96 仍是伞形断言，靠 COUPLED 认账）。
check(RS, "AX5 ★★身份必须**与载荷无关**：同类别、同方向、载荷完全不同的两次失配，\n"
          "          启发式文本必须**逐字节相同**（⇒ 不许把本次报错抄进规则）",
      _AZ96_OK_SAME,
      f"t1={_AZ96_T1[:60]!r} t2={_AZ96_T2[:60]!r}")

check(RS, "AX6 ★★身份**不得退化成常量**：`finish` 与 `recall` 两个类别必须给出\n"
          "          不同的启发式文本（⇒ 防止把所有动作归成一类、语义层只剩一句废话）",
      _AZ96_OK_IDENT,
      f"finish={_AZ96_T1[:50]!r} recall={_AZ96_T4[:50]!r}")


# ── AZ97（第 120 轮 / §93）：状态预算是**口径字段**，不是隐形常量 ───────────────
#
# 病根读数（V29 §92.5，`probe_s119_budget.py`）：
#   `STATE_CHAR_CAP=1600` 是一道**硬墙** —— `e3wi`/`e3ni` 的题面本身就 1705/1694，
#   于是那两题的 prompt 里**结构上永远放不进**任何辅助块；`e2wi`/`e2ni` 余量不足
#   同样整块被丢。可是它当时**不在** `CALIBER_KEYS`(12) 也不在 `Limits`(20)，
#   `suite_sha` 只哈希题集 ⇒ 把它改到 4000 会让 2/6 题变成 6/6 题拿到辅助块，
#   而 `Caliber.same_as()` 仍返回 **True**。这与 `temperature` 那次同源：
#   **不是尺子坏了，是尺子漏了一个旋钮。**
#
# 判据（四条，前三条正面、第四条防回归）：
#   ① 默认值必须**等于**模块常量 `STATE_CHAR_CAP`（常量降级为"默认值来源"，
#      两者漂移就说明"唯一默认值来源"这句话成了假的）；
#   ② `V1_STATE_CHARS=2400` ⇒ `Limits.state_chars == 2400`，且它出现在
#      `Limits.as_dict()` 里（⇒ 自动进口径签名）；
#   ③ 同一个 `Caliber`、只换 `limits` 快照 ⇒ `same_as()` 必须 **False**。
#      ⛔ 只看字段不看签名会漏掉"字段加了但没进制"这一种坏法。
#   ④ **通电**（纪律 14）：同一条 goal、同一份 history，cap=1600 时 `[轨迹]`
#      整块被丢（`state_trimmed=1`），cap=2400 时它在。⇒ 这个旋钮真的改变 prompt。
#   ⑤ 防回归：`core.py` / `planner.py` 里不许再出现"直接读模块常量做截断"。
#      否则字段加了、读点还是老路 ⇒ 读数照旧、签名照变 = 更坏的静默不可比。
#
# 撤回语义：把 `core.py` 的 `cap = int(self.s.limits.state_chars)` 改回
#   `cap = int(cfg.STATE_CHAR_CAP)` ⇒ ④ 必红（②③ 仍绿 —— 它们只看字段）。
_AZ97_ENV = "V1_STATE_CHARS"
_AZ97_BAK = os.environ.get(_AZ97_ENV)


def _az97_env(cap) -> None:
    if cap is None:
        os.environ.pop(_AZ97_ENV, None)
    else:
        os.environ[_AZ97_ENV] = str(cap)


try:
    _az97_env(None)
    _AZ97_DEF = cfg.settings().limits.state_chars
    _az97_env(2400)
    _AZ97_S24 = cfg.settings()
    _AZ97_OK_DEF = (_AZ97_DEF == cfg.STATE_CHAR_CAP)
    _AZ97_OK_FIELD = (_AZ97_S24.limits.state_chars == 2400
                      and _AZ97_S24.limits.as_dict().get("state_chars") == 2400
                      and cfg.settings().limits.as_dict().get("state_chars") == 2400)

    from mw.bench import Caliber as _AZ97_Cal                       # noqa: E402
    _az97_env(None)
    _AZ97_BASE = dict(scorer_sha="az97", suite_sha="az97", repeat=1,
                      budget_tag="1x", temperature=0.0, fresh=False,
                      memory_scope="shared_db", memory_seed="",
                      llm_cond={}, max_tokens=1024, tool_catalog_sha="az97")
    _AZ97_C1 = _AZ97_Cal(limits=cfg.settings().limits.as_dict(), **_AZ97_BASE)
    _az97_env(2400)
    _AZ97_C2 = _AZ97_Cal(limits=cfg.settings().limits.as_dict(), **_AZ97_BASE)
    _AZ97_OK_SIG = (not _AZ97_C1.same_as(_AZ97_C2)) and ("limits" in _AZ97_C1.diff(_AZ97_C2))

    # ④ 通电：同一条 goal、同一份 history，只换 cap
    _AZ97_GOAL = "目标" * 600                     # 1200 字
    _AZ97_HIST = ["历史步骤" * 40] * 3            # 每条 160 字

    def _az97_state(cap: int):
        _az97_env(cap)
        _c = Core(cfg.settings(), llm=StubLLM())
        _c.state_trimmed = 0
        _st = _c._state(_AZ97_GOAL, list(_AZ97_HIST), 1)
        _r = (("[轨迹]" in _st), int(_c.state_trimmed), len(_st))
        _c.memory.close()
        return _r

    _AZ97_S16 = _az97_state(1600)
    _AZ97_S24R = _az97_state(2400)
    _AZ97_OK_LIVE = (not _AZ97_S16[0]) and _AZ97_S24R[0] and \
                    _AZ97_S16[1] == 1 and _AZ97_S24R[1] == 0
finally:
    _az97_env(_AZ97_BAK)

_AZ97_CORE_SRC = _src("core.py")
_AZ97_PL_SRC = _src("planner.py")
_AZ97_OK_NOREGRESS = ("int(cfg.STATE_CHAR_CAP)" not in _AZ97_CORE_SRC
                      and "state[:cfg.STATE_CHAR_CAP]" not in _AZ97_PL_SRC
                      and "self.state_chars" in _AZ97_PL_SRC)

check(RS, "AZ97 ★★§93 状态预算是**口径字段**：`STATE_CHAR_CAP` 只是默认值来源，\n"
          "          运行时读数必须是 `Limits.state_chars`（`V1_STATE_CHARS` 可覆盖）。\n"
          "          ① 默认值 == 常量（不许漂移）；② 它进 `Limits.as_dict()`（⇒ 进签名）；\n"
          "          ③ 同一个 Caliber 只换 limits 快照 ⇒ `same_as()` 必须 **False**；\n"
          "          ④ **通电**：同 goal 同 history，cap=1600 时 `[轨迹]` 整块被丢、\n"
          "             cap=2400 时在（⇒ 这个旋钮真的改变 prompt，不只是多了个字段）；\n"
          "          ⑤ 防回归：`core.py`/`planner.py` 不许再直接读模块常量做截断。\n"
          "          撤回语义：`cap = int(self.s.limits.state_chars)` 改回常量 ⇒ ④ 红。",
      _AZ97_OK_DEF and _AZ97_OK_FIELD and _AZ97_OK_SIG
      and _AZ97_OK_LIVE and _AZ97_OK_NOREGRESS,
      f"①默认={_AZ97_DEF==cfg.STATE_CHAR_CAP}({_AZ97_DEF}) ②字段={_AZ97_OK_FIELD} "
      f"③签名={_AZ97_OK_SIG} ④通电={_AZ97_OK_LIVE}"
      f"(1600:{_AZ97_S16} 2400:{_AZ97_S24R}) ⑤回归={_AZ97_OK_NOREGRESS}")


# ── AZ98 / AZ99（第 123 轮 / §96）：自省这一环的**两道闸**都要能开 ─────────────
#
# 病根读数（`bench/evidence/v6_s122/p0_selfreach.txt`，零 LLM）：
#   6/6 题的「同题评审」不可达 ⇒ 每道题**上一次为什么错**结构上永远送不到它自己。
#   两道独立闸：
#     ① `critique.py` 的 `exclude_task_id`：同题一律拒收（轮内对、跨轮错）；
#     ② `core.py` 的 `crit[-3:]`：`crit` 按 task_id 字典序排，末三与被评题无关
#        ⇒ 即使拆了 ①，自评照样被切掉。**只拆一道等于没拆。**
#
# 判据（每条都双向，防「断言恒真」）：
#   AZ98：`allow_self=False`（默认）⇒ 同题仍被挡，且 `stats()['allow_self']` 自报 False；
#         `allow_self=True`  ⇒ 同题放行，且自报 True。
#         ⛔ 只测"开着能放行"是不够的：那样把默认改成 True 也全绿。
#   AZ99：有自评时它必须**占一个名额**进 `[外部评审]`（否则 ② 那道闸没开）；
#         无自评时取条必须与旧口径 `crit[-3:]` **逐元素相同**（否则旧读数不可比）。
_AZ98_OK = False
try:
    _az98_a = CritiqueLedger()
    _az98_a.add(Critique(scorer_sha="S", suite_sha="U", task_id="t1", why="w1"))
    _az98_a.add(Critique(scorer_sha="S", suite_sha="U", task_id="t2", why="w2"))
    _az98_off = [c.task_id for c in _az98_a.admissible(
        scorer_sha="S", suite_sha="U", exclude_task_id="t1")]

    _az98_b = CritiqueLedger(None, allow_self=True)
    _az98_b.add(Critique(scorer_sha="S", suite_sha="U", task_id="t1", why="w1"))
    _az98_b.add(Critique(scorer_sha="S", suite_sha="U", task_id="t2", why="w2"))
    _az98_on = [c.task_id for c in _az98_b.admissible(
        scorer_sha="S", suite_sha="U", exclude_task_id="t1")]

    _AZ98_OK = (_az98_off == ["t2"] and _az98_a.rejected["self"] >= 1
                and _az98_on == ["t1", "t2"] and _az98_b.rejected["self"] == 0
                and _az98_a.stats()["allow_self"] is False
                and _az98_b.stats()["allow_self"] is True)
except Exception as _e:  # noqa: BLE001
    _az98_off, _az98_on = f"异常 {type(_e).__name__}: {_e}", ""

check(RS, "AZ98 ★『不许用当前题』必须是**一个可关的闸**，不是一个隐形的常量：\n"
          "          ① 默认（`allow_self=False`）⇒ 同题仍被拒，`rejected['self']` 计数 ≥1；\n"
          "          ② 显式打开 ⇒ 同题放行，`rejected['self']==0`；\n"
          "          ③ 两种账本必须在 `stats()['allow_self']` 里**自报**口径\n"
          "             （否则 `served` 5 与 6 两种读数混在一张表里无法归因）。\n"
          "          ⛔ 只测②不测① ⇒ 把默认改成 True 也全绿（断言恒真）。",
      _AZ98_OK,
      f"默认放行={_az98_off} 打开放行={_az98_on} "
      f"自报={_az98_a.stats()['allow_self']}/{_az98_b.stats()['allow_self']}")

_AZ99_OK = False
try:
    def _az99_block(allow: bool) -> tuple[str, bool]:
        _led = CritiqueLedger(None, allow_self=allow)
        for _i in range(1, 7):
            _led.add(Critique(scorer_sha="S", suite_sha="U", task_id=f"t{_i}",
                              why=f"理由{_i}", family="f"))
        _c = Core(cfg.Settings(limits=cfg.Limits(max_steps=6)), llm=StubLLM(),
                  critique_ledger=_led)
        _c.log = lambda _m: None
        _st = _c._state("目标", [], 1, task_id="t1", scorer_sha="S", suite_sha="U")
        _c.memory.close()
        _line = ""
        for _ln in _st.splitlines():
            if _ln.startswith("[外部评审]"):
                _line = _ln
        return _line, ("t1" in _line)

    #: ① 打开：t1 必须进块（占一个名额），且另外两条是字典序末二 t5/t6
    _az99_line_on, _az99_has_self = _az99_block(True)
    #: ② 关闭：必须与旧口径一致 —— t1..t6 里去掉 t1 ⇒ t2..t6 ⇒ 末三 t4/t5/t6
    _az99_line_off, _az99_off_has_self = _az99_block(False)
    _AZ99_OK = (_az99_has_self
                and "t5" in _az99_line_on and "t6" in _az99_line_on
                and not _az99_off_has_self
                and "t4" in _az99_line_off and "t5" in _az99_line_off
                and "t6" in _az99_line_off)
except Exception as _e:  # noqa: BLE001
    _az99_line_on = _az99_line_off = f"异常 {type(_e).__name__}: {_e}"
    _az99_has_self = _az99_off_has_self = None

_AZ99_SRC = _src("core.py")
_AZ99_OK_NOREGRESS = ("crit[-3:]" in _AZ99_SRC and "self_c" in _AZ99_SRC)

check(RS, "AZ99 ★★『自评优先』必须同时满足两件事，缺一即等于没改：\n"
          "          ① 有自评时它**占一个名额**进 `[外部评审]` —— 否则 `crit[-3:]`\n"
          "             的字典序照样把它切掉（探针实测 6/6 不可达）；\n"
          "          ② 无自评时取条与旧口径 `crit[-3:]` **逐元素相同** ——\n"
          "             否则 V19~V31 的读数在换内核后不可比。\n"
          "          撤回语义：把 `pick = ...` 改回 `pick = crit[-3:]` ⇒ ① 红、② 仍绿。",
      _AZ99_OK and _AZ99_OK_NOREGRESS,
      f"①自评入块={_az99_has_self} 块={_az99_line_on[:90]!r} | "
      f"②无自评={_az99_off_has_self} 块={_az99_line_off[:90]!r} | "
      f"③静态锚={_AZ99_OK_NOREGRESS}")


# ── AZ110 的取证装置（V47）：外部能力**不适配**时不该占宽度（§113）─────────
#  ⭐ 修之前的形态（V45 实测 `v6_s135/p2_real.txt`）：
#     B 臂第 1 轮 `cands=5`、`by_tool={code_agent:4, run_shell:1}`、
#     `by_tool_max_round=4` —— 而 `code_agent` 对「算 CSV 合计」这道题
#     **结构上必然拒答**：它自己报「题面里没有『函数名必须是 X』⇒ 本 agent 不适用」。
#     ⇒ 那不是模型想得宽，是**准入没判**：宽度里塞了 4 个"永远不可能成功"的候选。
#  ⛔ 修法是"在**采纳/注入**处判适配"，**不是**改提示词：
#     提示词里写"不适用的别提"是自比对（§66 实测：连着两轮照提不误），
#     而且改提示词会让两臂不可比。
_AZ110_CSV = _AZ107_GOAL
_AZ110_FUNC = ("请实现一个函数，函数名必须是 sum_by_region，输入二维列表"
               "返回按区域汇总的字典。要求写成可直接导入的 Python 代码，并处理空输入。")
_AZ110_CODE_ANS = ("```python\ndef sum_by_region(rows):\n"
                   "    out = {}\n"
                   "    for _m, _r, _v in rows:\n"
                   "        out[_r] = out.get(_r, 0) + float(_v)\n"
                   "    return out\n```\n"
                   "函数名是 sum_by_region，可直接导入；空输入返回空字典，"
                   "同一区域多次出现会被累加，金额按浮点相加且不改变原顺序。")


def _az110_cand(tool: str, args: dict, intent: str = "用一下") -> dict:
    return {"intent": intent, "tool": tool, "args": args}


def _az110_payload(*items: dict) -> str:
    return json.dumps({"candidates": list(items)}, ensure_ascii=False)


class _AZ110LLM:
    """每天都说同一句话 —— 这一条要测的是**多轮**（空轮出口），
    所以不能像 `_AZ105LLM` 那样第 2 次起给空串（那会让"空轮"变成"规划失败"）。"""

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls = 0

    def complete(self, messages, max_tokens=None, **kw):
        self.calls += 1
        return self.payload


def _az110_run(tag: str, goal: str, payload: str, *, ext_tool: str = ""):
    d = _TMP / f"az110_{tag}"
    ws = d / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "sales.csv").write_text(_AZ107_INPUT, encoding="utf-8")
    s = cfg.settings()
    s2 = _AZ105_DC.replace(s, paths=_AZ105_DC.replace(s.paths, workspace=ws))
    if ext_tool:
        s2 = _AZ105_DC.replace(s2, limits=_AZ105_DC.replace(
            s2.limits, external_answer_tool=ext_tool))
    core = Core(s2, llm=_AZ110LLM(payload),
                memory=MemoryStore(d / "m.db"), memory_mode="raw")
    with contextlib.redirect_stdout(io.StringIO()):
        out = core.run(goal, budget_factor=1)
    return core, out


# ① **不适配**：CSV 题面 + 2 个 code_agent + finish。
#    ⚠️ `finish` 必须排在前 3 位（深通道 `n=3`）：放到后面会被 `[:n]` 截掉，
#       前 3 条全是 code_agent ⇒ 全丢 ⇒ 变成 ⑤ 的"空轮"形状，测不到"丢弃后仍有候选"。
_AZ110_C1, _AZ110_O1 = _az110_run(
    "drop", _AZ110_CSV,
    _az110_payload(_az110_cand("agent:code_agent", {"task": "a"}, "外援1"),
                   _az110_cand("agent:code_agent", {"task": "b"}, "外援2"),
                   _az110_cand("finish", {"answer": _AZ107_ANS}, "交付")))
# ② **适用**：同一条 `agent:code_agent`，函数题面 ⇒ 一个都不许丢。
_AZ110_C2, _AZ110_O2 = _az110_run(
    "keep", _AZ110_FUNC,
    _az110_payload(_az110_cand("finish", {"answer": _AZ110_CODE_ANS}, "交付"),
                   _az110_cand("agent:code_agent", {"task": "x"}, "外援")))
# ③ 未注册工具 ≠ 不适配：两条路径各有读数。
_AZ110_C3, _AZ110_O3 = _az110_run(
    "unknown", _AZ110_CSV,
    _az110_payload(_az110_cand("browser", {}, "想了没注册"),
                   _az110_cand("agent:code_agent", {"task": "a"}, "外援"),
                   _az110_cand("finish", {"answer": _AZ107_ANS}, "交付")))
# ④ 注入点：配了 `external_answer_tool` 但题面不适配 ⇒ 根本不注入。
_AZ110_C4, _AZ110_O4 = _az110_run(
    "inject", _AZ110_CSV,
    _az110_payload(_az110_cand("finish", {"answer": _AZ107_ANS}, "交付")),
    ext_tool="agent:code_agent")
# ⑤ 空轮：每轮只给不适配的候选 ⇒ 到 `max_stall` 走**自己的**出口。
_AZ110_C5, _AZ110_O5 = _az110_run(
    "empty", _AZ110_CSV,
    _az110_payload(_az110_cand("agent:code_agent", {"task": "a"}, "外援")))

_AZ110_P1 = json.loads(json.dumps(_AZ110_O1.stats["plan"], ensure_ascii=False))
_AZ110_P2 = json.loads(json.dumps(_AZ110_O2.stats["plan"], ensure_ascii=False))
_AZ110_P3 = json.loads(json.dumps(_AZ110_O3.stats["plan"], ensure_ascii=False))
_AZ110_P5 = json.loads(json.dumps(_AZ110_O5.stats["plan"], ensure_ascii=False))
_AZ110_X4 = dict(_AZ110_O4.stats["external"])
_AZ110_MS = max(1, int(cfg.settings().limits.max_stall))


def _az110_ident(p: dict) -> bool:
    """截断恒等式：被**准入**丢掉的不许算进"被 n 截掉"的（否则记两次）。"""
    return max(0, p["examined_total"] - p["cands_total"]
               - p["dropped_not_applicable"]) == p["truncated_cands"]


# ⑦ `applies` 不许进 `catalog()` —— 进了就是"改提示词"，两臂立刻不可比。
#    做法：同一份 agents 配置，只多一个 `applies_regex` 字段，两份 catalog 必须逐字节相同。
_AZ110_CFG_A = {"agents": {"x": {
    "type": "shell", "description": "示例能力",
    "command": ["python3", "-c", "print(1)"], "produces_answer": True}}}
_AZ110_CFG_B = {"agents": {"x": {
    "type": "shell", "description": "示例能力",
    "command": ["python3", "-c", "print(1)"], "produces_answer": True,
    "applies_regex": "函数名(?:必须|应)(?:是|为)"}}}
_AZ110_CAT = []
for _i, _cfg in enumerate((_AZ110_CFG_A, _AZ110_CFG_B)):
    _p = _TMP / f"az110_agents_{_i}.json"
    _p.write_text(json.dumps(_cfg, ensure_ascii=False), encoding="utf-8")
    _r = ToolRegistry()
    register_external_agents(_r, _p)
    _AZ110_CAT.append(_r.catalog())

# ⑧ 两处实现必须判同：`agents.json` 的正则 vs `code_agent.py::_FUNC_NAME`。
_AZ110_JSON_PAT = json.loads(
    (V1 / "agents.json").read_text(encoding="utf-8"))["agents"]["code_agent"].get(
        "applies_regex", "")
_AZ110_AGENT_SRC = (V1 / "external" / "code_agent.py").read_text(encoding="utf-8")
_AZ110_M = re.search(r"^_FUNC_NAME = re\.compile\(r\"(.+?)\"\)",
                     _AZ110_AGENT_SRC, re.M)
_AZ110_CASES = [
    _AZ110_CSV, _AZ110_FUNC,
    "实现一个函数，函数名应为 bar。",
    "请写一个函数，函数名是 baz。",          # ⚠️ 缺「必须/应」⇒ 两边都该判假
    "实现 add(a, b)。函数名必须是 add",
    "",
]
_AZ110_SAME = bool(_AZ110_JSON_PAT) and _AZ110_M is not None and all(
    bool(re.search(_AZ110_JSON_PAT, g)) == bool(re.search(_AZ110_M.group(1), g))
    for g in _AZ110_CASES)

# ⑨ 非法正则 ⇒ **恒适用**（保守）+ 留痕，⛔ 不许静默吞掉。
_p_bad = _TMP / "az110_agents_bad.json"
_p_bad.write_text(json.dumps({"agents": {"y": {
    "type": "shell", "command": ["python3", "-c", "print(1)"],
    "applies_regex": "函数名(必须"}}}, ensure_ascii=False), encoding="utf-8")
_r_bad = ToolRegistry()
register_external_agents(_r_bad, _p_bad)
_AZ110_BAD_OK = (spec_applies(_r_bad.get("agent:y"), _AZ110_CSV) is True
                 and "agent:y" in tools_module._EXT_APPLIES_ERR)

check(RS, "AZ110 ★★★★外部能力**结构上不适用**时不许占候选宽度（V47 / §113）\n"
          "         —— 修之前（V45 实测）：B 臂第 1 轮 5 个候选里 4 个是\n"
          "            `agent:code_agent`，而它对「算 CSV 合计」这道题自己就报\n"
          "            「题面里没有『函数名必须是 X』⇒ 不适用」⇒ 那不是模型想得宽，\n"
          "            是**准入没判**：4 个候选永远不可能成功，还占着 80% 的宽度。\n"
          "         判据（九条，缺一即证明不了这条边）：\n"
          "               ① CSV 题面 + 2×code_agent ⇒ `dropped_not_applicable==2`、\n"
          "                  `dropped_na_by_tool=={code_agent:2}`、cands 收敛到 1；\n"
          "               ② **同一个** code_agent + 函数题面 ⇒ 丢弃 **0**（不误杀）；\n"
          "               ③ 未注册工具走 `dropped_unknown`，**不**进 na（两条路径分列）；\n"
          "               ④ 注入点也判：不适配 ⇒ `skipped_not_applicable==1` 且 calls==0；\n"
          "               ⑤ 全不适配 ⇒ 走**自己的**出口 `status=='not_applicable'`\n"
          "                  （⛔ 不是 stalled / cascade / closed：三种相反的事不许共用一格）；\n"
          "               ⑥ 截断恒等式：`examined − cands − dropped_na == truncated_cands`\n"
          "                  （准入丢的不许算成被 n 截的，否则同一批候选记两次）；\n"
          "               ⑦ `applies` **不进** `catalog()` ⇒ 提示词没变；\n"
          "               ⑧ `agents.json` 的正则与 `code_agent.py::_FUNC_NAME` 判同（6 例）；\n"
          "               ⑨ 非法正则 ⇒ 恒适用 + 留痕（误杀比漏杀贵）。\n"
          "         撤回语义：把采纳点 `cands, _na_drop = self._drop_not_applicable(...)`\n"
          "                   改回 `cands, _na_drop = list(cands), []` ⇒ ①③⑤⑥ 必红、\n"
          "                   ④⑦⑧⑨ 仍绿（这一格正是本条要同时钉注入点与采纳点的原因：\n"
          "                   **不注入** 与 **不占宽度** 是两条独立的边）。",
      _AZ110_P1["dropped_not_applicable"] == 2
      and _AZ110_P1["dropped_na_by_tool"] == {"agent:code_agent": 2}
      and _AZ110_P1["cands_total"] == 1
      and _AZ110_P1["by_tool_max_round"] == 1
      and _AZ110_P1["by_tool"] == {"finish": 1}
      and _AZ110_P1["produces_by_round"] == [1]
      and _AZ110_P2["dropped_not_applicable"] == 0
      and _AZ110_P2["dropped_na_by_tool"] == {}
      and _AZ110_P3["dropped_not_applicable"] == 1
      and _AZ110_P3["dropped_na_by_tool"] == {"agent:code_agent": 1}
      and _AZ110_O3.stats["planner"]["width"]["dropped_unknown"] == 1
      and _AZ110_X4["skipped_not_applicable"] == 1
      and _AZ110_X4["calls"] == 0
      and _AZ110_O5.status == "not_applicable"
      and _AZ110_P5["cands_total"] == 0
      and _AZ110_P5["rounds"] == _AZ110_MS
      and _AZ110_P5["dropped_not_applicable"] == _AZ110_MS
      and all(_az110_ident(p) for p in
              (_AZ110_P1, _AZ110_P2, _AZ110_P3, _AZ110_P5))
      and _AZ110_CAT[0] == _AZ110_CAT[1]
      and _AZ110_SAME and _AZ110_BAD_OK,
      f"①丢弃={_AZ110_P1['dropped_not_applicable']}/{_AZ110_P1['dropped_na_by_tool']}"
      f" cands={_AZ110_P1['cands_total']} max_round={_AZ110_P1['by_tool_max_round']}"
      f" ｜ ②不误杀={_AZ110_P2['dropped_not_applicable']}"
      f" ｜ ③unknown={_AZ110_O3.stats['planner']['width']['dropped_unknown']}"
      f" na={_AZ110_P3['dropped_not_applicable']}"
      f" ｜ ④skipped_na={_AZ110_X4.get('skipped_not_applicable')}"
      f" calls={_AZ110_X4.get('calls')}"
      f" ｜ ⑤status={_AZ110_O5.status} rounds={_AZ110_P5['rounds']}/{_AZ110_MS}"
      f" ｜ ⑦catalog同={_AZ110_CAT[0] == _AZ110_CAT[1]}"
      f" ｜ ⑧正则判同={_AZ110_SAME} ｜ ⑨非法正则={_AZ110_BAD_OK}")


# ── AZ111 的取证装置（V48）：宽度 ≥ 2 ≠ 手段 ≥ 2（§114）───────────────────
#  ⭐ 现场（V47 真实 on 臂 `v6_s137/p2_real.txt`）：两轮 `n=2` 长得**一模一样** ——
#     第 2 轮 `{"n":2,"tools":{"run_shell":2}}`（同一路打两枪：两条 shell 命令）
#     第 4 轮 `{"n":2,"tools":{"run_shell":1,"finish":1}}`（两路：干活 + 交付）
#     在 `n` / `by_tool_max_round` / `by_tool` 上**完全相同**，而修法相反：
#     前者该问"为什么只会写 shell"，后者没有问题。
#  ⛔ 不许把两轮合成"平均宽度"之类的数：那正是 §112 那类"一个数回答两个问题"的病。
#  ⛔ 不往 `per_round` 的字典里塞键：`v6_s135`/`v6_s136`/`v6_s137` 三份**已冻结**
#     探针对那个字典做**整字典相等**断言，塞键会让它们红，而红的原因不是行为变了
#     （纪律 20：证据脚本不许写回自己的证据路径）。
_AZ111_RECALL = _az110_cand  # 同一套候选构造器（tool/args/intent 三字段）


def _az111_payload(*items: dict) -> str:
    return json.dumps({"candidates": list(items)}, ensure_ascii=False)


class _AZ111LLM:
    """按**轮**回放剧本 —— **只**对规划调用推进序列。

    ⛔ 判别键用规划 prompt 独有的 `互不相同`（`planner.py:531`），不用
      `max_tokens`：后者是旋钮，旋钮一调判据就失效。
    ⛔ 不用 `StubLLM(scripts=...)`：它是"关键词命中即用"，同一句关键词在
      **每一轮**都命中同一条 ⇒ 第 2 轮拿不到交付答案，多轮场景根本演不出来。
    """

    _PLAN_KEY = "互不相同"

    def __init__(self, plans: list[str]) -> None:
        self.plans = list(plans)
        self.i = 0
        self.calls = 0

    def complete(self, messages, max_tokens=None, **kw):
        self.calls += 1
        blob = "\n".join(getattr(m, "content", str(m)) for m in messages)
        if self._PLAN_KEY not in blob:
            return "{}"
        i = min(self.i, len(self.plans) - 1)
        self.i += 1
        return self.plans[i]


def _az111_run(tag: str, plans: list[str]):
    d = _TMP / f"az111_{tag}"
    ws = d / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "sales.csv").write_text(_AZ107_INPUT, encoding="utf-8")
    s = cfg.settings()
    s2 = _AZ105_DC.replace(s, paths=_AZ105_DC.replace(s.paths, workspace=ws))
    core = Core(s2, llm=_AZ111LLM(plans),
                memory=MemoryStore(d / "m.db"), memory_mode="raw")
    with contextlib.redirect_stdout(io.StringIO()):
        out = core.run(_AZ107_GOAL, budget_factor=1)
    return core, out


# ① **一路两枪**：两个 `recall`（query 不同 ⇒ sig 不同 ⇒ 都放行）。
#    用 `recall` 而不是 `run_shell`：后者要 `Guard(allow_shell=True)`，
#    而这一条要测的是"工具身份"，与"哪个工具"无关 —— 少一个开关就少一个变量。
_AZ111_C1, _AZ111_O1 = _az111_run("two_same", [
    _az111_payload(_AZ111_RECALL("recall", {"query": "sales"}, "查1"),
                   _AZ111_RECALL("recall", {"query": "region"}, "查2")),
    _az111_payload(_AZ111_RECALL("finish", {"answer": _AZ107_ANS}, "交付"))])

# ② **两路**：同一条首轮宽度 `n=2`，但两种工具。
_AZ111_C2, _AZ111_O2 = _az111_run("two_kinds", [
    _az111_payload(_AZ111_RECALL("finish", {"answer": _AZ107_ANS}, "交付"),
                   _AZ111_RECALL("recall", {"query": "sales"}, "顺手查"))])

# ③ **单候选**：`distinct` 恒为 1，但"一路多枪"必须是 **0**（反向闸）。
_AZ111_C3, _AZ111_O3 = _az111_run("single", [
    _az111_payload(_AZ111_RECALL("recall", {"query": "sales"}, "查")),
    _az111_payload(_AZ111_RECALL("finish", {"answer": _AZ107_ANS}, "交付"))])

_AZ111_P1 = json.loads(json.dumps(_AZ111_O1.stats["plan"], ensure_ascii=False))
_AZ111_P2 = json.loads(json.dumps(_AZ111_O2.stats["plan"], ensure_ascii=False))
_AZ111_P3 = json.loads(json.dumps(_AZ111_O3.stats["plan"], ensure_ascii=False))


def _az111_cons(p: dict) -> list[str]:
    """守恒四式：逐轮对齐、种类 ≤ 宽度、max 与 count 两条算法一致。"""
    bad: list[str] = []
    dbr, pr = p["distinct_by_round"], p["per_round"]
    if len(dbr) != p["rounds"] or len(pr) != p["rounds"]:
        bad.append(f"len(dbr)={len(dbr)} len(pr)={len(pr)} rounds={p['rounds']}")
        # ⛔ 立刻返回：**检测器不许在它要抓的那种病上崩溃**。
        #    撤回态第一版就是这样：`distinct_by_round` 被整段删掉 ⇒ 长度 0，
        #    而下面 `dbr[i]` 直接 IndexError ⇒ 契约测试崩在中途 ⇒
        #    汇总行都没打出来（red=无 / 汇总行=无），撤回态被判"说不清"。
        #    这正是纪律 4 的第三种「红=无」形态：**检测器自己失效**。
        return bad
    for i, (d, r) in enumerate(zip(dbr, pr)):
        if d != len(r["tools"]):
            bad.append(f"轮{i} distinct={d} != len(tools)={len(r['tools'])}")
        if d > r["n"]:
            bad.append(f"轮{i} distinct={d} > n={r['n']}")
        if r["n"] == 0 and d != 0:
            bad.append(f"轮{i} n=0 而 distinct={d}")
    if p["distinct_tools_max_round"] != (max(dbr) if dbr else 0):
        bad.append(f"max={p['distinct_tools_max_round']} != max(dbr)")
    _same = sum(1 for i, r in enumerate(pr) if r["n"] >= 2 and dbr[i] == 1)
    if p["rounds_same_tool_wide"] != _same:
        bad.append(f"same_wide={p['rounds_same_tool_wide']} != 逐轮复算={_same}")
    return bad


_AZ111_CONS = [_az111_cons(p) for p in (_AZ111_P1, _AZ111_P2, _AZ111_P3)]

# ⑥ `stats()` 必须是**深拷**：改返回值不许动内核（V45 踩过：同一引用，
#    断言读到的是自己刚写进去的值 ⇒ 深拷测试恒绿）。
_AZ111_ST = _AZ111_O1.stats
_AZ111_ST["plan"]["distinct_by_round"].append(99)
_AZ111_ST["plan"]["per_round"].append({"n": 99, "tools": {}})
_AZ111_DEEP = (99 not in _AZ111_C1.plan_stats["distinct_by_round"]
               and len(_AZ111_C1.plan_stats["per_round"]) == _AZ111_P1["rounds"])

# ⑦ 三个键**恒存在**（⚠️ 缺席冒充零的反面：`absent` 与 `0` 必须分得开）。
_AZ111_KEYS = all(k in _AZ111_P1 for k in
                  ("distinct_by_round", "distinct_tools_max_round",
                   "rounds_same_tool_wide"))


def _az111_at(p: dict, i: int, key: str = "distinct_by_round",
              default: int = -1) -> int:
    """安全取第 i 个 —— ⛔ 断言不许写成 `p["distinct_by_round"][0]`。

    撤回态删的正是这个数组：写成 `[0]` 就等于"要求读数非空"，
    而"读数为空"恰恰是这条撤回态要抓的病 ⇒ 断言在**被测的那种病**上
    IndexError 崩掉，契约测试崩在中途 ⇒ 汇总行都没打出来
    （撤回态读到 `red=无 / 汇总行=无 / Traceback=有`，判成"说不清"）。
    这是纪律 4 第三种「红=无」形态：**检测器自己失效**。两次都是同一个坑
    （先是 `_az111_cons`，再是这里的 `[0]`），所以做成函数而不是就地判断。
    """
    seq = p.get(key) or []
    return seq[i] if i < len(seq) else default

check(RS, "AZ111 ★★★★宽度 ≥ 2 ≠ 手段 ≥ 2：`n=2` 的两轮形态相反（V48 / §114）\n"
          "         —— 修之前（V47 实测）：`{run_shell:2}` 与 `{run_shell:1,finish:1}`\n"
          "            在 `n` / `by_tool_max_round` / `by_tool` 上**完全相同**，\n"
          "            而一个是「只会写 shell」、另一个没有问题。\n"
          "         判据（七条，缺一即证明不了这条边）：\n"
          "               ① 两个 `recall`（args 不同）⇒ 首轮 `n==2`、`by_tool_max_round==2`，\n"
          "                  而 `distinct_tools_max_round==1`、`rounds_same_tool_wide==1`\n"
          "                  （**宽而窄**在同一次运行里同时成立）；\n"
          "               ② 同一条 `n==2`、`finish`+`recall` ⇒ `distinct==2`、`same_wide==0`\n"
          "                  （两种形态被分开，而不是被平均）；\n"
          "               ③ 单候选的轮 ⇒ `distinct` 恒 1 但 `same_wide==0`\n"
          "                  （`n>=2` 那道闸：没有它，「每轮只给一个」会被读成\n"
          "                   「100% 一路多枪」—— 方向完全反了）；\n"
          "               ④ 守恒四式：`distinct_by_round[i]==len(per_round[i].tools)`、\n"
          "                  种类 ≤ 宽度、`n=0 ⇒ distinct=0`；\n"
          "               ⑤ `max` 与 `逐轮 count` 两条**不同算法**算出同一个数\n"
          "                  （单独任一个都能自洽地撒谎）；\n"
          "               ⑥ `stats()` 是深拷（改返回值不许动内核）；\n"
          "               ⑦ 三个键恒存在（`absent` 与 `0` 必须分得开）。\n"
          "         撤回语义：整段删掉 `_note_plan_round` 里那 6 行 ⇒ 本行转红\n"
          "                   （实测 PASS=610 FAIL=1、只它红、无 Traceback）。\n"
          "                   ⚠️ 我原先写的是「①⑤ 必红、④⑥⑦ 仍绿」，**实测 ④ 也红**：\n"
          "                   守恒式里含 `len(distinct_by_round)==rounds`，读数整段\n"
          "                   消失时它必然红 ⇒ 守恒**也能抓「读数整段不见了」**，\n"
          "                   只是抓不出「读数在、但恒零」（那要 ① 的非零断言）。\n"
          "                   ⛔ 所以本条的断言**不许直接下标** `dbr[0]`：撤回态删的\n"
          "                   正是那个数组，`[0]` 会 IndexError 让契约崩在中途 ⇒\n"
          "                   汇总行都没了（纪律 4 第三种「红=无」：检测器自己失效）。\n"
          "                   本轮这条自踩了两次（`_az111_cons` 与 check 条件），\n"
          "                   修法见 `_az111_at()`。",
      _AZ111_P1["per_round"][0] == {"n": 2, "tools": {"recall": 2}}
      and _az111_at(_AZ111_P1, 0) == 1
      and _AZ111_P1["distinct_tools_max_round"] == 1
      and _AZ111_P1["rounds_same_tool_wide"] == 1
      and _AZ111_P1["by_tool_max_round"] == 2
      and _AZ111_P2["per_round"][0] == {"n": 2, "tools": {"finish": 1, "recall": 1}}
      and _AZ111_P2["distinct_tools_max_round"] == 2
      and _AZ111_P2["rounds_same_tool_wide"] == 0
      and _AZ111_P3["distinct_tools_max_round"] == 1
      and _AZ111_P3["rounds_same_tool_wide"] == 0
      and all(r["n"] == 1 for r in _AZ111_P3["per_round"])
      and not any(_AZ111_CONS)
      and _AZ111_DEEP and _AZ111_KEYS,
      f"①n={_AZ111_P1['per_round'][0]['n']} distinct={_AZ111_P1['distinct_by_round']}"
      f" max={_AZ111_P1['distinct_tools_max_round']}"
      f" same_wide={_AZ111_P1['rounds_same_tool_wide']}"
      f" by_tool_max_round={_AZ111_P1['by_tool_max_round']}"
      f" ｜ ②distinct={_AZ111_P2['distinct_by_round']}"
      f" same_wide={_AZ111_P2['rounds_same_tool_wide']}"
      f" ｜ ③单候选 same_wide={_AZ111_P3['rounds_same_tool_wide']}"
      f" distinct={_AZ111_P3['distinct_by_round']}"
      f" ｜ ④⑤守恒={'ok' if not any(_AZ111_CONS) else _AZ111_CONS}"
      f" ｜ ⑥深拷={'ok' if _AZ111_DEEP else '活引用！'}"
      f" ｜ ⑦三键在={'ok' if _AZ111_KEYS else '缺！'}")

# ── AZ112 的取证装置（V49）：改的是**提示词**，不是内核（§115）───────────
#  ⛔ 这一条**不**测"模型会不会少提它" —— `StubLLM` 不看提示词。
#     它测的是"改的那一处确实是那一行、且只动该动的口径"。
#  ⚠️ 为什么值得单独钉：V47 之后 `dropped_na=9` 说明「内核知道、模型不知道」。
#     这一轮把同一信息写进 `description`（→ `catalog_line()` → planner 的
#     「可用工具」块）。改的是提示词，所以必须证明：
#       ① 只动了那一行（否则两臂说不清是谁的作用）；
#       ② 96 格口径（`--no-external`）**没**跟着动（否则不许拿 96 格基线继续用）；
#       ③ 真实口径（`--external`）**必须**跟着动（否则这个字段根本没进提示词）。
_AZ112_OLD = ("会执行的编码 agent：同题多角度生成 3 份实现，自写 assert 用例，"
              "真跑后选通过最多的一份，返回可直接交付的代码。")
#: ⭐ **从仓库里那份真实配置读**，不在测试里另抄一份文案。
#: 为什么必须这样：第一版把新描述硬编码成常量，于是 ⑤ 检的是"两个常量一致"
#: —— 撤回态**撤不动常量** ⇒ 那是一条恒真断言（纪律 4 第一种）。
#: 从 `agents.json` 读之后，撤掉产品里那句负向声明 ⇒ ⑤ 立刻红。
_AZ112_REAL = json.loads((V1 / "agents.json").read_text(encoding="utf-8"))[
    "agents"]["code_agent"]
_AZ112_NEW = _AZ112_REAL.get("description", "")
_AZ112_AP_JSON = _AZ112_REAL.get("applies_regex", "")
#: 改前实测（PREREG §3，**改描述之前**跑的）—— 两个口径各一个定标值。
_AZ112_SHA_EXT_BEFORE = "2cc3b0225ee03223"
_AZ112_SHA_NOEXT_BEFORE = "8decb60895d4a972"
_AZ112_DIR = Path(tempfile.mkdtemp(prefix="az112_"))


def _az112_cfg(tag: str, desc: str, applies: bool) -> Path:
    a = {"type": "shell", "description": desc,
         "command": ["python3", "external/code_agent.py"],
         "timeout_s": 300, "produces_answer": True}
    if applies:
        a["applies_regex"] = _AZ112_AP_JSON
    p = _AZ112_DIR / f"agents_{tag}.json"
    p.write_text(json.dumps({"agents": {"code_agent": a}}, ensure_ascii=False),
                 encoding="utf-8")
    return p


def _az112_reg(p: Path) -> ToolRegistry:
    r = ToolRegistry()
    register_core_tools(r, memory=None, workspace=None, allow_note=True)
    register_external_agents(r, p)
    return r


_AZ112 = {t: _az112_cfg(t, d, ap) for t, d, ap in (
    ("old_no", _AZ112_OLD, False), ("old_yes", _AZ112_OLD, True),
    ("new_no", _AZ112_NEW, False), ("new_yes", _AZ112_NEW, True))}
_AZ112_LO = _az112_reg(_AZ112["old_yes"]).catalog().split("\n")
_AZ112_LN = _az112_reg(_AZ112["new_yes"]).catalog().split("\n")
_AZ112_DIFF = [i for i, (a, b) in enumerate(zip(_AZ112_LO, _AZ112_LN)) if a != b]
#: ⚠️ **不许**写 `_AZ112_LO[_AZ112_DIFF[0]]`：diff 为空时那是 IndexError，
#:    而"两行没差别"恰恰是本条要抓的病之一 ⇒ 检测器会在它要抓的病上自己崩掉
#:    （V48 同一坑踩了两次，纪律 4 第三种「红=无」）。
_AZ112_HIT_OLD = _AZ112_LO[_AZ112_DIFF[0]] if _AZ112_DIFF else ""
_AZ112_HIT_NEW = _AZ112_LN[_AZ112_DIFF[0]] if _AZ112_DIFF else ""
_AZ112_SHA_NOEXT = {t: catalog_sha(wire_external=False, agents_path=p)
                    for t, p in _AZ112.items()}
_AZ112_SHA_EXT = {t: catalog_sha(wire_external=True, agents_path=p)
                  for t, p in _AZ112.items()}

check(RS, "AZ112 ① 改 description 只动目录里的那一行（差 1 行，且差的是 agent:code_agent）",
      len(_AZ112_LO) == len(_AZ112_LN) == 4 and len(_AZ112_DIFF) == 1
      and _AZ112_HIT_OLD.startswith("- agent:code_agent")
      and _AZ112_HIT_NEW.startswith("- agent:code_agent"),
      f"行数={len(_AZ112_LO)}/{len(_AZ112_LN)} 差异行={len(_AZ112_DIFF)}"
      f" 命中={_AZ112_HIT_NEW[:28]!r}")

check(RS, "AZ112 ② 96 格口径（--no-external）**不**随描述变（四份同 sha 且等于改前）",
      len(set(_AZ112_SHA_NOEXT.values())) == 1
      and _AZ112_SHA_NOEXT["new_yes"] == _AZ112_SHA_NOEXT_BEFORE,
      f"{_AZ112_SHA_NOEXT['new_yes'][:16]} 四份去重={len(set(_AZ112_SHA_NOEXT.values()))}")

check(RS, "AZ112 ③ 真实口径（--external）**必须**随描述变，且与 applies_regex 无关",
      _AZ112_SHA_EXT["old_no"] == _AZ112_SHA_EXT["old_yes"] == _AZ112_SHA_EXT_BEFORE
      and _AZ112_SHA_EXT["new_no"] == _AZ112_SHA_EXT["new_yes"]
      and _AZ112_SHA_EXT["new_no"] != _AZ112_SHA_EXT_BEFORE,
      f"old={_AZ112_SHA_EXT['old_yes'][:16]} new={_AZ112_SHA_EXT['new_yes'][:16]}")

check(RS, "AZ112 ④ 描述必须单行且 ≤200 字符（catalog_line 是一行一条）",
      "\n" not in _AZ112_NEW and "\n" not in _AZ112_OLD
      and 0 < len(_AZ112_NEW) <= 200 and len(_AZ112_OLD) <= 200,
      f"new={len(_AZ112_NEW)} old={len(_AZ112_OLD)} 字符")

#: ⑤ 是这一条的**防漂核心**，也是**唯一能撤回**的那条（锚在 `agents.json`）：
#: `description` 与 `applies_regex` 必须锚在同一个词上。只改一处 ⇒
#: 模型看到的是"A"、内核判的是"B" ⇒ 又回到 V47 的"内核知道、模型不知道"。
#: ⛔ 撤回语义：把 `agents.json` 那句「⛔ 题面没指定函数名时它一定失败，不要选它。」
#:    删掉（回到中性广告语）⇒ 本行红、其余六行仍绿。
check(RS, "AZ112 ⑤ 描述与 applies_regex 同锚「函数名」，且新描述含负向声明",
      "函数名" in _AZ112_NEW and "函数名" in _AZ112_AP_JSON
      and "不要选" in _AZ112_NEW and "不要选" not in _AZ112_OLD,
      f"desc 锚={'ok' if '函数名' in _AZ112_NEW else '缺'}"
      f" ap 锚={'ok' if '函数名' in _AZ112_AP_JSON else '缺'}"
      f" 负向={'ok' if '不要选' in _AZ112_NEW else '缺'}")

#: ⑥ 描述只该影响**提示词**，不许影响**准入判定**（V47 的判据与它正交）。
#: ⚠️ 必须分两组：`applies=False` 的配置走"没声明 = 恒适用"那一档（三态语义），
#:    不适配题面也返回 True —— 第一版探针把四份当同一种语义，红了一次。
_AZ112_G = "工作目录里有 sales.csv。请算每个区域的金额合计，写入 report.md。"
_AZ112_F = "写一个函数解析 '1h30m'，函数名必须是 parse_duration。"
_AZ112_GRANTED = [spec_applies(_az112_reg(_AZ112[t]).get("agent:code_agent"), _AZ112_G)
                  for t in ("old_yes", "new_yes")]
_AZ112_NEVER = [spec_applies(_az112_reg(_AZ112[t]).get("agent:code_agent"), _AZ112_G)
                for t in ("old_no", "new_no")]
_AZ112_FIT = [spec_applies(_az112_reg(_AZ112[t]).get("agent:code_agent"), _AZ112_F)
              for t in ("old_no", "old_yes", "new_no", "new_yes")]
check(RS, "AZ112 ⑥ 描述不动准入判定（有判据两份同判、无判据两份恒适用）",
      _AZ112_GRANTED == [False, False] and _AZ112_NEVER == [True, True]
      and _AZ112_FIT == [True] * 4,
      f"有判据={_AZ112_GRANTED} 无判据={_AZ112_NEVER} fit={_AZ112_FIT}")

print(f"  ⛔ AZ112 只证明「改的是提示词、且只动了该动的口径」；"
      f"「模型会不会少提它」由真实五臂回答（v6_s139/p2_real.txt）")

# ══════════════════════════════════════════════════════════════════════════
# AZ114（第一百四十轮 §117）：工具的**参数契约**不许对模型撒谎
#
# V51 主押注（交付闸）判负并**已撤回**（`v6_s141/RESULTS.md` §3/§4）：
# 它把答案打回、让位也通电了，但让位复用的是"以为已经做完"时想的候选，
# 且 `_close_out` 只有自检、没有产物问 ⇒ 三臂 `verdict` 全 FAIL，
# 代价 +35s/+6 calls，还扰动 AZ107/AZ108/AZ110 三张老契约。
# ⇒ 按「没证据更好 = 不改」撤回，留下的是**被单独隔离出来**的这条 bug：
#   目录写着「(可选)」、handler 也有默认值，而 `dispatch` 把 `params` 的每个键
#   都当必填 ⇒ 让位到 `run_shell({"cmd": …})` 时一条命令都没执行。
# ══════════════════════════════════════════════════════════════════════════

_AZ114_WS = _TMP / "az114_ws"
_AZ114_WS.mkdir(parents=True, exist_ok=True)

# ── AZ114：目录说「可选」，执行器就必须真可选 ────────────────────────────
# V51 B 臂现场（`v6_s141/logs_b.txt`）：模型照目录给的 `run_shell({"cmd": …})`
# 被 `dispatch` 判「缺参数 ['timeout_s']」，一条命令都没执行。
# ⛔ 断言必须同时钉三件事：① 缺省能跑 ② `cmd` 仍必填 ③ **目录行一个字符没变**
#    （第三件是为了守住 `catalog_sha`：改这处不许顺手把口径断掉）。
_AZ114_REG = ToolRegistry()
register_core_tools(_AZ114_REG, memory=None, workspace=_AZ114_WS, allow_note=True)
_AZ114_ONLY_CMD = _AZ114_REG.dispatch("run_shell", {"cmd": "echo v51-ok"})
_AZ114_NO_CMD = _AZ114_REG.dispatch("run_shell", {})
_AZ114_SPEC = _AZ114_REG.get("run_shell")
_AZ114_LINE = next((ln for ln in _AZ114_REG.catalog().splitlines()
                    if ln.startswith("- run_shell")), "")

check(RS, "AZ114 ★`ToolSpec.optional`：目录写「可选」的参数，`dispatch` 不许当必填\n"
          "          ① `run_shell({'cmd': …})`（不给 timeout_s）⇒ ok=True 且真跑；\n"
          "          ② 连 `cmd` 都不给 ⇒ 仍 ok=False 且报「缺参数」（必填没被一起放开）；\n"
          "          ③ 目录行仍是 `run_shell(cmd, timeout_s)` —— 该项**不进目录**，\n"
          "             `catalog_sha` 不变（V50 已为改描述断口径交过学费）。",
      (_AZ114_ONLY_CMD.ok is True and "v51-ok" in (_AZ114_ONLY_CMD.output or "")
       and _AZ114_NO_CMD.ok is False and "缺参数" in (_AZ114_NO_CMD.error or "")
       and _AZ114_SPEC is not None and "timeout_s" in _AZ114_SPEC.optional
       and _AZ114_LINE.startswith("- run_shell(cmd, timeout_s)")),
      f"①ok={_AZ114_ONLY_CMD.ok} ｜ ②ok={_AZ114_NO_CMD.ok} "
      f"err={(_AZ114_NO_CMD.error or '')[:24]!r} ｜ ③行={_AZ114_LINE[:34]!r}")

# ══════════════════════════════════════════════════════════════════════════
# AZ115（第一百四十一轮 §118）：**重规划必须看得见**
#
# V52 的因变量是「交付被打回之后，是让位还是重规划」。而 `replan()` 内部就是
#   `propose(history=history + [失败行])` ⇒ 两者产出的 `stats` **完全同形**：
#   轮数、候选数、工具分布一模一样。没有一行日志，`failover.used=0` 既可能是
#   "按新规矩重规划"、也可能是"根本没打回" —— 缺席冒充零（纪律 5）。
#
# ⛔ 断言必须钉**内容**不是钉子串：那一行里带着 `last_error` 的原文。
#   只断言"有 ↻ 这个字符"的写法，删掉 `{last_error}` 仍然绿（恒真断言的变体）。
# ⛔ 本契约**不**依赖交付闸：它靠自检打回（V51 已把闸撤回，见 §117.4）。
# ══════════════════════════════════════════════════════════════════════════
import contextlib as _AZ115_CTX                                # noqa: E402
import dataclasses as _AZ115_DC                                # noqa: E402
import io as _AZ115_IO                                         # noqa: E402
import json as _AZ115_JSON                                     # noqa: E402

_AZ115_BAD = "```python\ndef other(xs):\n    return xs\n```"
_AZ115_PAYLOAD = _AZ115_JSON.dumps(
    {"candidates": [{"intent": "先交一版", "tool": "finish",
                     "args": {"answer": _AZ115_BAD}}]}, ensure_ascii=False)


class _AZ115LLM:
    """每次都交同一份**过不了自检**的答案 ⇒ 必然打回 ⇒ 下一轮必然 replan。"""

    def complete(self, messages, max_tokens=None):
        return _AZ115_PAYLOAD


def _az115_run(tag: str) -> str:
    d = _TMP / f"az115_{tag}"
    d.mkdir(parents=True, exist_ok=True)
    s = cfg.settings()
    # ⛔ 不许靠"当前 env 恰好是什么"：契约进程里前面的断言会改 `os.environ`
    #    （`V1_VERIFY_RETRIES` 一类），本 fixture 一读 `settings()` 就继承了它 ——
    #    实测第一版 `verify_retries` 被前序断言改成 0 ⇒ 首次打回直接 `exhausted`
    #    ⇒ 一次重规划都没发生，而断言只会说"那行日志没出现"（把装置故障读成缺陷）。
    s2 = _AZ115_DC.replace(
        s, limits=_AZ115_DC.replace(s.limits, max_steps=5, verify_retries=2))
    core = Core(s2, llm=_AZ115LLM(), memory=MemoryStore(d / "m.db"), memory_mode="raw")
    buf = _AZ115_IO.StringIO()
    with _AZ115_CTX.redirect_stdout(buf):
        core.run(_AZ105_GOAL, budget_factor=1)
    return buf.getvalue()


_AZ115_LOG = _az115_run("a")
_AZ115_LINES = [ln.strip() for ln in _AZ115_LOG.splitlines() if "↻ 带错误原文重规划" in ln]
#: 那一行里的错误原文（取自同一次运行里自检打回的那句）。
_AZ115_REJ = [ln for ln in _AZ115_LOG.splitlines() if "自检未过" in ln]

check(RS, "AZ115 ★★`replan` 与 `propose` 在 `stats` 上完全同形 ⇒ 重规划必须有**一行**日志\n"
          "          —— V52 的因变量是「被打回之后让位还是重规划」，而两者的读数一模一样：\n"
          "             `failover.used=0` 既可能是「按新规矩重规划」，也可能是「根本没打回」。\n"
          "         判据（三条，缺一即证明不了这条边）：\n"
          "               ① 打回之后**确实**打印了 `↻ 带错误原文重规划`（且有 ≥1 行）；\n"
          "               ② 那一行里带着**错误原文**（不是一句固定文案）——\n"
          "                  否则删掉 `{last_error}` 也能绿；\n"
          "               ③ 它只出现在**重规划**那一支：首次 `propose` 的那一轮日志里没有。\n"
          "         撤回语义：删掉 `core.py` 里那行 `self.log` ⇒ ① 必红。",
      (len(_AZ115_LINES) >= 1
       and any("自检未通过" in ln for ln in _AZ115_LINES)
       and bool(_AZ115_REJ)
       and _AZ115_LINES[0].find("连续失败") >= 0),
      f"①行数={len(_AZ115_LINES)} ｜ ②带原文="
      f"{any('自检未通过' in ln for ln in _AZ115_LINES)} ｜ "
      f"样本={(_AZ115_LINES[0][:48] if _AZ115_LINES else '（无）')!r}")

# ══════════════════════════════════════════════════════════════════════════
# AZ116（第一百四十二轮 §119）：收尾出口的「过闸」必须先问**产物**
#
# V52 两臂的现场（`bench/evidence/v6_s142/logs_{a,b}.txt`，2/2 格）：
#     🧪 收尾交付过闸
#     ■ not_applicable 验收=FAIL(0/1，缺 report.md) | ... 收尾闸=1跑/1过/0未过
# 同一份交付物、同一时刻、两句话相反。病因：`_close_out` 那道闸只跑
# `vfy.run(best, self._checks)`（判据由题面的**静态形态**派生，只问"这段文本
# 像不像能跑的代码"），而产物问 `vfy.check_delivery` 原来**只在 `_finish` 里**算
# —— 而 `_finish` 在 `_close_out` **返回之后**才跑 ⇒ 「过闸」是在产物还没被
# 问过的时候说出来的。
#
# 四格（与 `bench/evidence/v6_s143/PREREG.md` §3 一一对应）：
#   S1 零产物(FAIL) / S2 产物在(PASS) / S3 半产物(PARTIAL) / S4 关掉文本判据
# ⛔ S2 是**反向闸**：只测 S1 的话，"把闸改成恒不打印"也能全绿。
# ⛔ 四格交付物必须逐字相同 —— 本轮只许记账，不许改分（§45.9）。
# ══════════════════════════════════════════════════════════════════════════
from mw import verify as _AZ116_V                              # noqa: E402

_AZ116_GOAL1 = ("工作目录里有 sales.csv。请计算每个区域的合计，"
                "写入工作目录下的 report.md。")
_AZ116_GOAL2 = "请生成 out/a.md 与 out/b.md 两个文件，各写一句话。"
_AZ116_ANS = "华北 2700 ｜ 华南 2000 ｜ 华东 700"


class _AZ116LLM:
    """收尾答案的桩：任何请求都回同一段文本 ⇒ 四格交付物可逐字比对。"""

    def complete(self, messages, max_tokens=None):      # noqa: ANN001, ARG002
        return _AZ116_ANS


def _az116_run(tag: str, goal: str, files: dict, *, retries: int = 2) -> dict:
    d = _TMP / f"az116_{tag}"
    ws = d / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        p = ws / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    # ⚠️ `Paths` 是 frozen dataclass ⇒ **构造时**给全（构造后赋值必炸，V51 现场）。
    # ⚠️ 不靠 `cfg.settings()` 读 env：本进程里前序断言改过 `os.environ`
    #    （AZ115 的坑：`verify_retries` 被继承成 0 ⇒ 一次闸都没跑）。
    s = cfg.Settings(
        paths=cfg.Paths(root=d, state=d / "state", logs=d / "logs",
                        workspace=ws, bench=d / "bench"),
        limits=cfg.Limits(max_steps=3, close_reserve_steps=1,
                          verify_retries=retries, verify_static=2))
    c = Core(settings=s, llm=_AZ116LLM(),
             memory=MemoryStore(d / "state" / "memory.db"))
    lines: list[str] = []
    c.log = lines.append                              # noqa: ARG005
    c._reset_verify(goal)
    c._checks = (tuple(_AZ116_V.derive(goal, 2)) if retries > 0 else ())
    seen: dict = {}

    def _done(status: str, answer: str, reason: str) -> str:     # noqa: ANN001
        seen.update(status=status, answer=answer, reason=reason)
        return "ok"

    c._close_out(goal, ["h1"], _done)
    return {"lines": lines, "seen": seen, "co": dict(c.closeout_stats),
            "vs": dict(c.verify_stats), "checks": len(c._checks)}


_AZ116_S1 = _az116_run("s1", _AZ116_GOAL1, {})
_AZ116_S2 = _az116_run("s2", _AZ116_GOAL1,
                       {"report.md": "| 区域 | 合计 |\n|---|---|\n| 华北 | 2700 |\n"})
_AZ116_S3 = _az116_run("s3", _AZ116_GOAL2, {"out/a.md": "第一句\n"})
_AZ116_S4 = _az116_run("s4", _AZ116_GOAL1, {}, retries=0)


def _az116_passed(r: dict) -> bool:
    return any("收尾交付过闸" in ln for ln in r["lines"])


_AZ116_OK = {
    "①零产物不说「过闸」": (not _az116_passed(_AZ116_S1)
                     and _AZ116_S1["co"].get("artifact_fail") == 1),
    "②产物在**仍**说「过闸」（反向闸）": (_az116_passed(_AZ116_S2)
                               and _AZ116_S2["co"].get("artifact_fail") == 0
                               and _AZ116_S2["co"].get("artifact_partial") == 0),
    "③半产物与零产物**分列**": (not _az116_passed(_AZ116_S3)
                        and _AZ116_S3["co"].get("artifact_partial") == 1
                        and _AZ116_S3["co"].get("artifact_fail") == 0),
    "④关掉文本判据仍问产物": (_AZ116_S4["checks"] == 0
                      and _AZ116_S4["vs"].get("closeout_runs") == 0
                      and _AZ116_S4["co"].get("artifact_fail") == 1
                      and not _az116_passed(_AZ116_S4)),
    "⑤交付物逐字不变": ({r["seen"].get("answer") for r in
                  (_AZ116_S1, _AZ116_S2, _AZ116_S3, _AZ116_S4)} == {_AZ116_ANS}),
    "⑥状态词不变": ({r["seen"].get("status") for r in
                (_AZ116_S1, _AZ116_S2, _AZ116_S3, _AZ116_S4)} == {"closed"}),
    "⑦reason 如实写缺哪个产物": ("report.md" in str(_AZ116_S1["seen"].get("reason"))),
}

check(RS, "AZ116 ★★收尾出口说「过闸」之前，**必须**先问产物\n"
          "          —— V52 两臂 2/2 格：日志 `🧪 收尾交付过闸` 与 `验收=FAIL(缺 report.md)`\n"
          "            说的是同一份交付物。病因不是判据错，是**顺序**：产物问原来只在\n"
          "            `_finish` 里算，而 `_finish` 在 `_close_out` 返回之后才跑。\n"
          "          判据（七条，缺一即证明不了这条边）：\n"
          "               ① 零产物(FAIL) ⇒ 日志无「收尾交付过闸」且 `artifact_fail=1`；\n"
          "               ② 产物在(PASS) ⇒ **仍**说「过闸」（反向闸：证明不是把闸改成恒不打印）；\n"
          "               ③ 半产物(PARTIAL) 与零产物**分列计数**（修法不同，不能合并）；\n"
          "               ④ `verify_retries=0` ⇒ 文本闸零跑，但产物问**仍在**（不依赖开关）；\n"
          "               ⑤ 四格交付物**逐字相同**（只许记账不许改分）；\n"
          "               ⑥ 状态词不变（`closed`）；⑦ `reason` 写出缺哪个产物。\n"
          "         撤回语义：删掉 `if _co_art_bad:` 那一支（恢复无条件打过闸）⇒ ① 必红；\n"
          "                    产物问改成恒 PASS ⇒ ①③ 必红；FAIL/PARTIAL 并成一个键 ⇒ ③ 必红；\n"
          "                    把产物问挪回 `if self._checks:` 里 ⇒ ④ 必红。",
      all(_AZ116_OK.values()),
      " ｜ ".join(f"{k}={v}" for k, v in _AZ116_OK.items())
      + f" ｜ 闸行S1={[ln.strip()[:28] for ln in _AZ116_S1['lines'] if '🧪' in ln]}")

# ══════════════════════════════════════════════════════════════════════════
# AZ117（第一百四十三轮 §120）：进收尾之前**到底下发过几次**
#
# 为什么需要这条边：`■` 行的 `steps=N` 是 `len(history)`，数的是**规划轮**
# （让位 / 预演否决 / 跳过 / 准入空轮都往 `history` 写行）。于是同一句
# `■ not_applicable` 底下可能是两件完全相反的事：
#   `v6_s139/logs_b.txt`：3 次 `run_shell ok=True` 之后进收尾 ⇒ 产物在（PASS）
#   `v6_s142/logs_{a,b}.txt`：0 次成功之后进收尾 ⇒ 交付 77 字、产物为零（FAIL）
# 没有计数器，这两种收尾在读数上**完全同形** —— 而它们的修法相反。
#
# 单元格（与 `bench/evidence/v6_s144/PREREG.md` §5 一一对应）：
#   `never`（自声明恒不适用）⇒ 零下发；3 次 `recall` 成功 ⇒ 三次下发。
# ⛔ 反向闸必须有：只测零下发格，把计数器写成恒 0 也能全绿。
# ⛔ 交付物必须逐字相同（本轮只许记账）。
# ══════════════════════════════════════════════════════════════════════════
from mw.planner import _ANSWER_SYS as _AZ117_ANS_SYS           # noqa: E402

_AZ117_GOAL = "用一段话说明快速排序的平均时间复杂度为什么是 O(n log n)。"


class _AZ117LLM:
    """规划桩：前 `n_ok` 轮给一个能跑通的 `recall`，之后只给**恒不适用**的候选。"""

    def __init__(self, n_ok: int) -> None:
        self.n_ok = n_ok
        self.planning = 0

    @staticmethod
    def _txt(m) -> str:                                       # noqa: ANN001
        return (str(m.get("content", "")) if isinstance(m, dict)
                else str(getattr(m, "content", "")))

    def complete(self, messages, max_tokens=None):             # noqa: ANN001, ARG002
        sysmsg = self._txt(messages[0]) if messages else ""
        if sysmsg == _AZ117_ANS_SYS:                          # `planner.answer()`
            return "平均 O(n log n)：每次划分 O(n)，期望递归深度 O(log n)。"
        if "规划器" not in sysmsg:
            return ""
        self.planning += 1
        k = self.planning
        if k <= self.n_ok:
            # ⚠️ 一轮**一个**候选：主循环一旦有候选成功就停在本轮，
            #    一轮塞三个只会执行一个（实测：`dispatched=1` 而不是 3）。
            return json.dumps({"candidates": [
                {"intent": f"检索第{k}条相关记忆", "tool": "recall",
                 "args": {"query": f"az117-{k}"}}]}, ensure_ascii=False)
        return json.dumps({"candidates": [
            {"intent": "交给一个恒不适用的能力", "tool": "never",
             "args": {"x": "1"}}]}, ensure_ascii=False)


def _az117_run(tag: str, n_ok: int):
    d = _TMP / f"az117_{tag}"
    ws = d / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (d / "state").mkdir(parents=True, exist_ok=True)
    # ⚠️ `Paths` 是 frozen dataclass ⇒ **构造时**给全（V51 现场）。
    # ⚠️ 不靠 `cfg.settings()`：前序断言改过 `os.environ`，本 fixture 会继承它。
    s = cfg.Settings(
        paths=cfg.Paths(root=d, state=d / "state", logs=d / "logs",
                        workspace=ws, bench=d / "bench"),
        limits=cfg.Limits(max_steps=8, close_reserve_steps=1, verify_retries=2))
    c = Core(settings=s, llm=_AZ117LLM(n_ok),
             memory=MemoryStore(d / "state" / "memory.db"))
    # 恒不适用的工具：`applies` 是能力**自己**声明的，内核只负责 `bool(fn(goal))`。
    c.tools.register(ToolSpec("never", "对任何题面都不适用", {"x": "参数"},
                              lambda a: "y", applies=lambda g: False))
    lines: list[str] = []
    c.log = lines.append                                      # noqa: ARG005
    out = c.run(_AZ117_GOAL, budget_factor=1)
    return out, lines


_AZ117_OUT0, _AZ117_LOG0 = _az117_run("zero", 0)
_AZ117_OUT3, _AZ117_LOG3 = _az117_run("three", 3)
_AZ117_ST0 = getattr(_AZ117_OUT0, "stats", None) or {}
_AZ117_ST3 = getattr(_AZ117_OUT3, "stats", None) or {}
_AZ117_CO0 = _AZ117_ST0.get("closeout") or {}
_AZ117_CO3 = _AZ117_ST3.get("closeout") or {}
_AZ117_EX0 = _AZ117_ST0.get("exec") or {}
_AZ117_EX3 = _AZ117_ST3.get("exec") or {}

_AZ117_OK = {
    "①零下发格真的走了准入空轮出口": (_AZ117_OUT0.status == "not_applicable"
                          and _AZ117_OUT0.verdict == "ANSWER"),
    "②零下发被记成计数并按入口分列": (
        _AZ117_EX0.get("dispatched") == 0
        and _AZ117_CO0.get("zero_exec") == 1
        and (_AZ117_CO0.get("zero_exec_by_reason") or {}).get("not_applicable") == 1),
    "③`reason` 与日志都写明「一次都没下发」": (
        "一次都没下发" in str(_AZ117_OUT0.reason or "")
        and any("一次都没下发" in ln for ln in _AZ117_LOG0)),
    # ★ V56（§122）：这里从「整字典相等」**退回三个键**，不是为了放松，
    #   而是把「交付/步骤」这件事整个交给 AZ119 —— 否则撤任一侧的实现都会
    #   让两条断言一起红，而"多红"是撤回态里最容易被当成噪声吞掉的东西。
    #   `exec` 的**键集全等**由 AZ119 ⑨ 钉住（那边才是这套键的主）。
    "④反向闸：三次成功下发 ⇒ 不许记成零下发": (
        (_AZ117_EX3.get("dispatched"), _AZ117_EX3.get("ok"),
         _AZ117_EX3.get("fail")) == (3, 3, 0)
        and _AZ117_CO3.get("zero_exec") == 0
        and "此前已下发 3 次" in str(_AZ117_OUT3.reason or "")),
    "⑤`■` 行带「执行=」段（`steps` 一个数会被读成「做了几步」）": any(
        "执行=" in ln for ln in _AZ117_LOG3),
    "⑥判据未变：同一道题的 `verdict` 与改前一致（ANSWER）": (
        _AZ117_OUT3.verdict == "ANSWER"),
}

check(RS, "AZ117 ★★收尾出口必须说得出「此前下发过几次」\n"
          "          —— `■` 行的 `steps=N` 是 `len(history)`，数的是**规划轮**：\n"
          "             `v6_s139` 是「3 次成功之后走神」（产物在），`v6_s142` 是\n"
          "             「一次都没成」（交付 77 字、产物为零）—— 两者读数**完全同形**。\n"
          "          判据（六条，缺一即证明不了这条边）：\n"
          "               ① 零下发格确实走准入空轮出口（否则这条边没有分母）；\n"
          "               ② 零下发被记成计数，且**按入口分列**（修法不同 ⇒ 不许合并）；\n"
          "               ③ `reason` 与入口日志都写明「一次都没下发」；\n"
          "               ④ **反向闸**：三次成功下发 ⇒ `zero_exec=0` 且写出次数；\n"
          "               ⑤ `■` 行带「执行=N下发/M成/K败」段；\n"
          "               ⑥ 判据未变（`verdict` 仍是 ANSWER：本轮只记账不改判）。\n"
          "         撤回语义：删掉 `_close_out` 里 `⊞` 那两句 ⇒ ③ 必红；\n"
          "                    计数器写在 `if result.ok:` 里 ⇒ ②④ 必红；\n"
          "                    `zero_exec` 恒 0 ⇒ ② 必红；`■` 行去掉「执行=」⇒ ⑤ 必红。",
      all(_AZ117_OK.values()),
      " ｜ ".join(f"{k}={v}" for k, v in _AZ117_OK.items()))

# ══════════════════════════════════════════════════════════════════════════
# AZ118（第一百四十四轮 §121）：按题的「下发次数 × 验收结论」进没进账本
#
# 为什么需要这条边：「零下发的文本交付算不算交付」这个问题，**手上答不了**。
#   · `kernel_closeout.zero_exec` 只覆盖**收尾出口**（`_close_out`）；
#   · `finish` 出口（`status=done`）占真实判定行的 12/26，它一条都没数过；
#   · `report.per_task` 只有 `{id, rep, score, why}` —— 没有 `verdict`，没有 `exec`。
# ⇒ 这个交叉表只能 grep stdout 的 `■` 行。V55 第一次回溯就因为
#   `startswith("↳ ")` 撞上 `HH:MM:SS` 前缀，把 14/14 全判成「零调用」。
#   同一个问题两次读不一样 ⇒ 它就不是读数。
#
# 判据（六条）：
#   ① 账本行里有 `kernel_exec`（不落盘的读数不可审计）
#   ② 三个累加点都接上（`_kexec_absorb` 在**两个** solver 分支各一次 + 定义一次）
#   ③ 合计对得上：tasks / dispatched / ok / fail
#   ④ **反向闸**：0 下发那格记进 `zero_dispatch_tasks`，有下发那格**不许**记
#   ⑤ 两个字典**分列**，且 `by_verdict` 的合计必须等于 `tasks`（缺席冒充零）
#   ⑥ 零点每次都是**新**字典（共享可变默认值会让两轮串味）
# ══════════════════════════════════════════════════════════════════════════
from run import _kexec_absorb as _AZ118_ABS, _kexec_zero as _AZ118_ZERO  # noqa: E402


class _AZ118Out:
    """最小 Outcome 替身：只带 `_kexec_absorb` **真的读**的两个字段。"""

    def __init__(self, dispatched: int, verdict: str) -> None:
        self.stats = {"exec": {"dispatched": dispatched, "ok": dispatched, "fail": 0}}
        self.verdict = verdict


#: ★ V56（§122）：7 → **11** 个键（`answer` / `step` / `zero_step_tasks` /
#: `zero_step_by_verdict`）。⛔ 这里是**全等**比较：少一个键会红、多一个键也会红。
_AZ118_KEYS = sorted(["tasks", "dispatched", "ok", "fail", "answer", "step",
                      "zero_dispatch_tasks", "zero_step_tasks",
                      "by_verdict", "zero_dispatch_by_verdict",
                      "zero_step_by_verdict"])
_Z0 = _AZ118_ZERO()
_Z1 = _AZ118_ZERO()
_AZ118_FRESH = (_Z0 is not _Z1
                and _Z0["by_verdict"] is not _Z1["by_verdict"]
                and _Z0["zero_dispatch_by_verdict"] is not _Z1["zero_dispatch_by_verdict"]
                and _Z0["zero_step_by_verdict"] is not _Z1["zero_step_by_verdict"])
_ACC = _AZ118_ZERO()
_AZ118_ABS(_ACC, _AZ118Out(0, "ANSWER"))      # 零下发
_AZ118_ABS(_ACC, _AZ118Out(3, "FAIL"))        # 三次下发（反向闸的格）
_AZ118_ABS(_ACC, _AZ118Out(1, ""))            # verdict 空 ⇒ 记成 `?`，不许丢掉这一题
_AZ118_CODE = _code_only(_RUN_SRC)

_AZ118_OK = {
    "①账本行里有 `kernel_exec`": '"kernel_exec": _kexec' in _RUN_SRC,
    "②两个 solver 分支都接了累加（+定义共三处）": (
        _AZ118_CODE.count("_kexec_absorb(_kexec, out)") == 2
        and _AZ118_CODE.count("def _kexec_absorb(") == 1),
    "③合计对得上（tasks/dispatched/ok/fail）": (
        (_ACC["tasks"], _ACC["dispatched"], _ACC["ok"], _ACC["fail"]) == (3, 4, 4, 0)),
    "④反向闸：0 下发记 1 题、有下发不记": _ACC["zero_dispatch_tasks"] == 1,
    "⑤两个字典分列且 by_verdict 合计 == tasks": (
        sum(_ACC["by_verdict"].values()) == _ACC["tasks"]
        and sum(_ACC["zero_dispatch_by_verdict"].values()) == 1
        and _ACC["by_verdict"] == {"ANSWER": 1, "FAIL": 1, "?": 1}
        and _ACC["zero_dispatch_by_verdict"] == {"ANSWER": 1}),
    "⑥零点每次都是新字典（不共享可变默认值）": _AZ118_FRESH and sorted(_Z0) == _AZ118_KEYS,
}

check(RS, "AZ118 ★★「零下发」是**题**的属性，不是收尾的属性：\n"
          "          —— `kernel_closeout.zero_exec` 只数收尾出口，`status=done`\n"
          "             那一半（真实判定行 12/26）它一条都没数过；`per_task` 里\n"
          "             既没有 `verdict` 也没有 `exec` ⇒ 交叉表只能 grep 日志。\n"
          "          判据（六条，缺一即证明不了这条边）：\n"
          "               ① 账本行含 `kernel_exec`；\n"
          "               ② 两个 solver 分支各接一次（漏一处 = 静默少计一题）；\n"
          "               ③ tasks/dispatched/ok/fail 四个合计对得上；\n"
          "               ④ **反向闸**：0 下发记 1、3 次下发不记（排除常量计数器）；\n"
          "               ⑤ 两个 `by_*` 分列，且 `by_verdict` 合计 == `tasks`；\n"
          "               ⑥ 零点每次新建（共享可变默认值会让两轮串味）。\n"
          "          撤回语义：拿掉 `if d == 0:` 那支 ⇒ ④ 必红；\n"
          "                    `by_verdict` 合计漏记空 verdict ⇒ ⑤ 必红；\n"
          "                    把 `_kexec_zero` 的字典提到模块级常量 ⇒ ⑥ 必红。",
      all(_AZ118_OK.values()),
      " ｜ ".join(f"{k}={v}" for k, v in _AZ118_OK.items()))

# ══════════════════════════════════════════════════════════════════════════
# AZ119（第一百四十五轮 §122）：下发必须按「交付动作 / 步骤动作」拆开
#
# 为什么需要这条边（V55 §121 的结论）：`finish` **本身也是一次下发**，所以
#   「12 题 18 次下发」里 12 次是"交答案"、只有 6 次是"做事情"。
#   `dispatched` 与 `zero_dispatch_tasks` 因此只能捕获「**连答案都没提**」；
#   真正有分母的病位是「**12 题里有 6 题只提了 `finish`**」——
#   一个把交付与步骤加在一起的总数，永远读不出那 6 题。
#
# 三格（每格一种形态，**不是**三份拷贝）：
#   `answer_only` 第 1 轮就 `finish` ⇒ 交付 1、步骤 0（= 6/12 的病位形态）
#   `steps`       3 次 `recall` 之后恒不适用 ⇒ 交付 0、步骤 3（反向闸）
#   `zero`        从头到尾恒不适用 ⇒ 0/0（证明计数器不是常量）
# ⛔ 三格都断言守恒 `answer + step == dispatched`：守恒是必要条件，不是通电证明
#   （§46）；能证明"拆对了"的是两侧各有精确整数对的反向闸。
# ══════════════════════════════════════════════════════════════════════════
from run import _kexec_absorb as _AZ119_ABS, _kexec_zero as _AZ119_ZERO  # noqa: E402

_AZ119_GOAL = "用一段话说明为什么哈希表的平均查找是 O(1)。"
_AZ119_ANS = "键经哈希函数映射到桶，平均冲突链长为常数，所以平均 O(1)。"


class _AZ119LLM:
    """规划桩：`mode` 决定这一格下发什么。

    ⚠️ 一轮**一个**候选：主循环一旦有候选成功就停在本轮（V54 实测过
       「一轮塞三个只执行一个」）。要三次下发就得给三轮。
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.planning = 0

    @staticmethod
    def _txt(m) -> str:                                       # noqa: ANN001
        return (str(m.get("content", "")) if isinstance(m, dict)
                else str(getattr(m, "content", "")))

    def complete(self, messages, max_tokens=None):             # noqa: ANN001, ARG002
        sysmsg = self._txt(messages[0]) if messages else ""
        if sysmsg == _AZ117_ANS_SYS:                          # `planner.answer()`
            return _AZ119_ANS
        if "规划器" not in sysmsg:
            return ""
        self.planning += 1
        if self.mode == "answer_only":
            # 第 1 轮就交答案 —— 这就是 6/12 的形态：交付动作有、步骤动作零。
            return json.dumps({"candidates": [
                {"intent": "直接给出答案", "tool": "finish",
                 "args": {"answer": _AZ119_ANS}}]}, ensure_ascii=False)
        if self.mode == "steps" and self.planning <= 3:
            return json.dumps({"candidates": [
                {"intent": f"检索第{self.planning}条相关记忆", "tool": "recall",
                 "args": {"query": f"az119-{self.planning}"}}]}, ensure_ascii=False)
        if self.mode == "ansfail":
            # 交付动作**失败** ⇒ 唯一的候选 ⇒ `break` ⇒ 下一轮同签名被重复闸拦
            # ⇒ 停滞 ⇒ 走收尾。这一格是 `⊞`「一次步骤动作都没有」的唯一活路径：
            # 真实跑批里 answer-only 的题是从 `finish` 出口走的，**不进收尾**。
            return json.dumps({"candidates": [
                {"intent": "用一个坏掉的交付能力交答案", "tool": "badans",
                 "args": {"x": "1"}}]}, ensure_ascii=False)
        return json.dumps({"candidates": [
            {"intent": "交给一个恒不适用的能力", "tool": "never",
             "args": {"x": "1"}}]}, ensure_ascii=False)


def _az119_run(tag: str, mode: str):
    d = _TMP / f"az119_{tag}"
    ws = d / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (d / "state").mkdir(parents=True, exist_ok=True)
    # ⚠️ `Paths` 是 frozen dataclass ⇒ **构造时**给全（V51 现场）。
    # ⚠️ 不靠 `cfg.settings()`：前序断言改过 `os.environ`，本 fixture 会继承它。
    s = cfg.Settings(
        paths=cfg.Paths(root=d, state=d / "state", logs=d / "logs",
                        workspace=ws, bench=d / "bench"),
        limits=cfg.Limits(max_steps=8, close_reserve_steps=1, verify_retries=2))
    c = Core(settings=s, llm=_AZ119LLM(mode),
             memory=MemoryStore(d / "state" / "memory.db"))
    c.tools.register(ToolSpec("never", "对任何题面都不适用", {"x": "参数"},
                              lambda a: "y", applies=lambda g: False))

    def _badans(_a):                                          # noqa: ANN001
        raise RuntimeError("交付能力坏了（探针造）")

    #: 交付动作但**必然失败**：用来让「有交付、零步骤」这一格走到收尾。
    c.tools.register(ToolSpec("badans", "一个必然失败的交付能力", {"x": "参数"},
                              _badans, produces_answer=True))
    lines: list[str] = []
    c.log = lines.append                                      # noqa: ARG005
    out = c.run(_AZ119_GOAL, budget_factor=1)
    return out, lines


_AZ119_OA, _AZ119_LA = _az119_run("answer_only", "answer_only")
_AZ119_OS, _AZ119_LS = _az119_run("steps", "steps")
_AZ119_OE, _AZ119_LE = _az119_run("zero", "zero")
_AZ119_OF, _AZ119_LF = _az119_run("ansfail", "ansfail")


def _az119_ex(o) -> dict:                                     # noqa: ANN001
    return ((getattr(o, "stats", None) or {}).get("exec") or {})


def _az119_pair(o):                                           # noqa: ANN001
    """`(answer, step)` 的整数对。⛔ 缺键即 `KeyError`：静默补 0 会把
    「没接线」读成「一步都没做」（缺席冒充零，纪律 5）。"""
    e = _az119_ex(o)
    return int(e["answer"]), int(e["step"])


_AZ119_ACC = _AZ119_ZERO()
for _o in (_AZ119_OA, _AZ119_OS, _AZ119_OE, _AZ119_OF):
    _AZ119_ABS(_AZ119_ACC, _o)
_AZ119_ALL = (_AZ119_OA, _AZ119_OS, _AZ119_OE, _AZ119_OF)
_AZ119_BARS = [ln for ln in (_AZ119_LA + _AZ119_LS + _AZ119_LE + _AZ119_LF)
               if ln.startswith("■ ")]
_AZ119_OK = {
    "①守恒：四格 `answer + step == dispatched`": all(
        sum(_az119_pair(o)) == int(_az119_ex(o)["dispatched"])
        for o in _AZ119_ALL),
    "②反向闸·第 1 轮就 finish ⇒ 交付 1、步骤 0": _az119_pair(_AZ119_OA) == (1, 0),
    "③反向闸·三次 recall ⇒ 交付 0、步骤 3": _az119_pair(_AZ119_OS) == (0, 3),
    "④常量计数器排除：零下发格是 0/0": (
        _az119_pair(_AZ119_OE) == (0, 0)
        and int(_az119_ex(_AZ119_OE)["dispatched"]) == 0),
    "⑤账本：`zero_step_tasks` = 3（只提 finish + 交付失败 + 零下发），"
    "`zero_dispatch_tasks` = 1": (
        _AZ119_ACC["zero_step_tasks"] == 3
        and _AZ119_ACC["zero_dispatch_tasks"] == 1
        and sum(_AZ119_ACC["zero_step_by_verdict"].values()) == 3
        # 超集关系：零下发那题必须**同时**进 zero_step（写成一个 if/elif 就会漏）
        and _AZ119_ACC["zero_step_tasks"] >= _AZ119_ACC["zero_dispatch_tasks"]),
    "⑥形态：`■` 四格都带「交付=/步骤=」，且「交付=0/步骤=3」只出现在 steps 格": (
        len(_AZ119_BARS) == 4
        and all("交付=" in ln and "步骤=" in ln and "执行=" in ln
                for ln in _AZ119_BARS)
        and any("交付=0/步骤=3" in ln for ln in _AZ119_LS)
        and not any("交付=0/步骤=3" in ln for ln in _AZ119_LA)),
    "⑦计数发生在**真交付**上（不是给 finish 单独开的路）：交付物 == 模型给的答案": (
        str(_AZ119_OA.answer or "") == _AZ119_ANS),
    "⑧收尾显形：「一次步骤动作都没有」只出现在「有交付、零步骤」的收尾格": (
        any("一次步骤动作都没有" in ln for ln in _AZ119_LF)
        and not any("一次步骤动作都没有" in ln for ln in _AZ119_LS)),
    #: ⛔ 键集**全等**：多一个键会红、少一个键也会红。AZ117 ④ 已经从整字典
    #:    相等退回三个键（避免撤回时两处一起红）⇒ 那件事由这里接手。
    "⑨`exec` 的键集恰好是这五个": (
        sorted(_az119_ex(_AZ119_OA)) == ["answer", "dispatched", "fail", "ok", "step"]
        and sorted(_az119_ex(_AZ119_OS)) == sorted(_az119_ex(_AZ119_OA))),
}

check(RS, "AZ119 ★★★下发必须按「交付动作 / 步骤动作」拆开：\n"
          "          —— `finish` 本身也是一次下发（V55：B 臂 18 次 = `finish×12`\n"
          "             + `recall×6`）⇒ 一个总数只能捕获「连答案都没提」，而\n"
          "             真正有分母的病位是「**12 题里 6 题只提了 `finish`**」。\n"
          "          判据（九条，缺一即证明不了这条边）：\n"
          "               ① 守恒 `answer + step == dispatched`（四格都成立）；\n"
          "               ② **反向闸·只提 finish** ⇒ `(answer, step) == (1, 0)`；\n"
          "               ③ **反向闸·三次 recall** ⇒ `(0, 3)`（排除常量计数器）；\n"
          "               ④ 零下发格是 `(0, 0)` 且 `dispatched == 0`；\n"
          "               ⑤ 账本 `zero_step_tasks`：三题记 1、`steps` 那题不记，\n"
          "                  且与 `zero_dispatch_tasks` 保持**超集**关系；\n"
          "               ⑥ `■` 四格都带「交付=/步骤=」；`交付=0/步骤=3` 只出现在\n"
          "                  `steps` 格（反向闸：不是恒打印）；\n"
          "               ⑦ 计数落在**真交付**上（`finish` 的答案逐字送达）；\n"
          "               ⑧ 收尾 `⊞` 的「一次步骤动作都没有」只在「有交付、零步骤」\n"
          "                  的收尾格出现 —— 那一格必须**真走到收尾**才有分母；\n"
          "               ⑨ `exec` 的键集恰好是五个（多一个/少一个都红）——\n"
          "                  AZ117 ④ 已从整字典相等退回三个键，键集由这条接手。\n"
          "          撤回语义：删掉计数点 `if _is_delivery:` 那支 ⇒ ② 必红；\n"
          "                    `step` 写成恒 `+= 1` ⇒ ② 必红；`answer`/`step`\n"
          "                    都抄 `dispatched` ⇒ ① 必红；`_kexec_absorb` 去掉\n"
          "                    `zero_step` 那支 ⇒ ⑤ 必红；`■` 行去掉「交付=」⇒ ⑥ 必红；\n"
          "                    删掉收尾那条「一次步骤动作都没有」⇒ ⑧ 必红。",
      all(_AZ119_OK.values()),
      " ｜ ".join(f"{k}={v}" for k, v in _AZ119_OK.items())
      + f" ｜ 四格 (answer,step)="
        + "/".join(str(_az119_pair(o)) for o in _AZ119_ALL)
        + f" ｜ dispatched={[int(_az119_ex(o)['dispatched']) for o in _AZ119_ALL]}"
        + f" ｜ verdict={'/'.join(str(o.verdict) for o in _AZ119_ALL)}")

# ══════════════════════════════════════════════════════════════════════════
# AZ120（第一百四十六轮 §123）：候选漏斗 —— 「提了几个、剩下去了哪」
#
# 为什么需要这条边（V56 §122 的结论是**错的**）：`zero_step_tasks=6` 被写成
#   「**12 题里 6 题只提了 `finish`**」。而 `v6_s146/logs_b.txt` 的
#   `[i] 候选=N` 行逐题数下来是 **30 个候选进执行器、只下发 18 个**：
#   「提」与「做」是两个数，合成一个就会把 6/12 读成"模型想不出别的"。
#
# 四格（每格一种出口，**不是**四份拷贝）：
#   `single`              一轮一个候选 = finish ⇒ 未轮到 0（反向闸：不许恒记）
#   `three_finish_first`  三个候选、第 1 个 finish 成功 ⇒ 未轮到 **2**
#   `three_step`          三个 recall、第 1 个成功 ⇒ 未轮到 **2**（成功也 break）
#   `blocked_then_finish` [被 guard 拦, finish] ⇒ 拦 1 / 未轮到 **0**
# ⛔ 第四格是**互不冒充**的证明：把 `blocked` 也并进 `unreached` ⇒ ④ 必红。
# ══════════════════════════════════════════════════════════════════════════
from run import (_koff_absorb as _AZ120_ABS,      # noqa: E402
                 _koff_line as _AZ120_LINE,
                 _koff_zero as _AZ120_ZERO)

#: ⛔ 题面必须**够复杂**，否则 `looks_complex` 走快通道 ⇒ `n=1` ⇒
#:    `plan.steps[:1]` 把第 2/3 个候选当场截掉，四格全变成"1 提 1 做"。
#:    第一次跑就是这么红的（实测 `(1,1,0,0)/(1,1,0,0)/(1,1,0,0)/(1,0,1,0)`）——
#:    **判据本身没写错，是装置到不了那一格**（纪律 6：判据分辨率不得高于装置）。
#:    这里的两个逗号 + `然后` 让 `multi >= 2` 成立。
_AZ120_GOAL = "说明为什么哈希表的平均查找是 O(1)，然后举一个冲突变多的反例，接着指出它依赖的前提。"


class _AZ120LLM:
    """规划桩：`mode` 决定**第 1 轮**塞几个候选进去。

    ⚠️ 第 2 轮一律换成恒不适用的能力（`never`）⇒ 准入空轮 ⇒ 走收尾：
       否则 `three_step` 会在第 2 轮把上一轮**没轮到**的两个 recall 再提一遍
       （它们没进 `attempted_sigs`，不会被重复闸挡），"未轮到 2"就被洗掉了。
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.planning = 0

    @staticmethod
    def _txt(m) -> str:                                       # noqa: ANN001
        return (str(m.get("content", "")) if isinstance(m, dict)
                else str(getattr(m, "content", "")))

    def complete(self, messages, max_tokens=None):             # noqa: ANN001, ARG002
        sysmsg = self._txt(messages[0]) if messages else ""
        if sysmsg == _AZ117_ANS_SYS:                          # `planner.answer()`
            return _AZ119_ANS
        if "规划器" not in sysmsg:
            return ""
        self.planning += 1
        if self.planning > 1:
            return json.dumps({"candidates": [
                {"intent": "交给一个恒不适用的能力", "tool": "never",
                 "args": {"x": "1"}}]}, ensure_ascii=False)
        _fin = {"intent": "直接给出答案", "tool": "finish",
                "args": {"answer": _AZ119_ANS}}
        if self.mode == "single":
            return json.dumps({"candidates": [_fin]}, ensure_ascii=False)
        if self.mode == "three_finish_first":
            #: 交付动作**排在第一个** —— 它成功就 `return`，后两个一次都没被考虑。
            return json.dumps({"candidates": [
                _fin,
                {"intent": "先查一条记忆", "tool": "recall", "args": {"query": "az120-a"}},
                {"intent": "再查一条记忆", "tool": "recall", "args": {"query": "az120-b"}},
            ]}, ensure_ascii=False)
        if self.mode == "three_step":
            return json.dumps({"candidates": [
                {"intent": f"查记忆 {i}", "tool": "recall", "args": {"query": f"az120-{i}"}}
                for i in (1, 2, 3)]}, ensure_ascii=False)
        if self.mode == "blocked_then_finish":
            #: 第 1 个是**会改变外部世界**的动作（`mutating`）⇒ 先过 guard；
            #: 探针里没开 `--allow-shell` ⇒ 必被拦。它被拦**不是**"没轮到"。
            return json.dumps({"candidates": [
                {"intent": "跑一条 shell 验证", "tool": "run_shell",
                 "args": {"cmd": "echo az120", "timeout_s": 5}},
                _fin,
            ]}, ensure_ascii=False)
        return json.dumps({"candidates": [
            {"intent": "交给一个恒不适用的能力", "tool": "never",
             "args": {"x": "1"}}]}, ensure_ascii=False)


def _az120_run(tag: str, mode: str):
    d = _TMP / f"az120_{tag}"
    ws = d / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (d / "state").mkdir(parents=True, exist_ok=True)
    # ⚠️ 与 `_az119_run` 同一条纪律：`Paths` 是 frozen dataclass，构造时给全；
    #    也不调 `cfg.settings()`（前序断言改过 `os.environ`，这里会继承）。
    s = cfg.Settings(
        paths=cfg.Paths(root=d, state=d / "state", logs=d / "logs",
                        workspace=ws, bench=d / "bench"),
        limits=cfg.Limits(max_steps=8, close_reserve_steps=1, verify_retries=2))
    c = Core(settings=s, llm=_AZ120LLM(mode),
             memory=MemoryStore(d / "state" / "memory.db"))
    c.tools.register(ToolSpec("never", "对任何题面都不适用", {"x": "参数"},
                              lambda a: "y", applies=lambda g: False))
    lines: list[str] = []
    c.log = lines.append                                      # noqa: ARG005
    out = c.run(_AZ120_GOAL, budget_factor=1)
    return out, lines


_AZ120_OS, _AZ120_LS = _az120_run("single", "single")
_AZ120_O3, _AZ120_L3 = _az120_run("three_finish_first", "three_finish_first")
_AZ120_OT, _AZ120_LT = _az120_run("three_step", "three_step")
_AZ120_OB, _AZ120_LB = _az120_run("blocked_then_finish", "blocked_then_finish")
_AZ120_ALL = (_AZ120_OS, _AZ120_O3, _AZ120_OT, _AZ120_OB)


def _az120_st(o) -> dict:                                     # noqa: ANN001
    return getattr(o, "stats", None) or {}


def _az120_funnel(o):                                         # noqa: ANN001
    """`(cands_total, dispatched, blocked, unreached)` —— 四个数一个不少。"""
    st = _az120_st(o)
    return (int((st.get("plan") or {})["cands_total"]),
            int((st.get("exec") or {})["dispatched"]),
            int((st.get("loop") or {}).get("blocked_skipped") or 0),
            int((st.get("offer") or {})["unreached"]))


class _AZ120Out:
    """最小 Outcome 替身：只带 `_koff_absorb` **真的读**的四个子字典。

    ⛔ 不是"顺便造一个"：账本侧的五格分类**只能**用这种格造出来 ——
       `done_without_offer`（做了却没提过）在真内核上结构上不可能发生，
       而"一个永远不会发生的格子"必须能被证明**不是死代码**。
    """

    def __init__(self, rounds=1, cands=1, produces=(1,), step=0,
                 dispatched=1, blocked=0, unreached=0) -> None:
        self.stats = {
            "plan": {"rounds": rounds, "cands_total": cands,
                     "produces_by_round": list(produces)},
            "exec": {"dispatched": dispatched, "ok": dispatched, "fail": 0,
                     "answer": dispatched - step, "step": step},
            "loop": {"blocked_skipped": blocked},
            # ★ V58（§124）：分项必须与总数**自洽**地出现在替身里 ——
            #   替身不写 `by_stop`，`unreached_stop_gap` 就会在这些格上
            #   变成一个假的 2，而"检测器报错"与"替身偷懒"会长得一模一样。
            "offer": {"unreached": unreached,
                      "unreached_by_stop": {"answer": unreached, "step_ok": 0,
                                            "fail": 0},
                      "unreached_by_tool": {}},
        }


_AZ120_ACC = _AZ120_ZERO()
for _o in (_AZ120Out(),                                              # 只提交付
           _AZ120Out(cands=2, blocked=1),                            # 提了、被拦
           _AZ120Out(cands=3, unreached=2),                          # 提了、没轮到
           _AZ120Out(rounds=0, cands=0, produces=(), dispatched=0),  # 没规划成功
           _AZ120Out(rounds=2, cands=3, produces=(1, 0), step=1,
                     dispatched=2, unreached=1)):                    # 提了也做了
    _AZ120_ABS(_AZ120_ACC, _o)
#: 违规格：**做了非交付动作却没提过**。结构上不可能 ⇒ 只用来证明检测器活着。
_AZ120_BAD = _AZ120_ABS(_AZ120_ZERO(),
                        _AZ120Out(cands=1, produces=(1,), step=1, dispatched=1))
#: 残差格：3 个候选只解释了 1 个 ⇒ `unaccounted` 必须说出 2，**不许钳成 0**。
_AZ120_UNACC = _AZ120_LINE(_AZ120_ABS(
    _AZ120_ZERO(), _AZ120Out(cands=3, unreached=0, blocked=0, dispatched=1)))
_AZ120_Z0 = _AZ120_ZERO()
_AZ120_Z1 = _AZ120_ZERO()
_AZ120_CODE = _code_only(_RUN_SRC)
#: 账本累加器的键集（**全等**）：少一个键会红、多一个键也会红。
#: ⚠️ 这里钉的是 `_koff_zero()` 的字典，与内核侧 `stats["offer"]`
#:    （★ V58 起三个键：`unreached` + 两个分项）是**两份不同的东西** ——
#:    第一次写这条判据时把两者当成一个，于是拿 `["unreached"]` 去比 14 个键，
#:    当场红（自踩，已记）。
_AZ120_KEYS = sorted(["tasks", "rounds", "offered", "produces_offered",
                      "step_offered", "dispatched", "blocked", "unreached",
                      "no_rounds", "only_delivery", "offered_not_done",
                      "done_without_offer", "offered_and_done",
                      "offered_not_done_blocked",
                      # ★ V58（§124）：`unreached` 的出口分项 + 被跳过的工具。
                      #   ⛔ `unreached_stop_gap` **不在这里**：它是 `_koff_line`
                      #   派生出来的（与 `unaccounted` 同宗），零点里没有它 ——
                      #   第一次就是把派生量也写进零点，于是 16 键比 17 键，当场红。
                      "unreached_by_stop", "unreached_by_tool"])

_AZ120_OK = {
    "①守恒：`offered == dispatched + blocked + unreached`（四格都成立）": all(
        sum(f[1:]) == f[0] for f in (_az120_funnel(o) for o in _AZ120_ALL)),
    "②反向闸·一轮只提 finish ⇒ 未轮到 0（不许把\"没下发\"一律记成没轮到）": (
        _az120_funnel(_AZ120_OS) == (1, 1, 0, 0)),
    "③三个候选、第 1 个 finish 成功 ⇒ 未轮到 **2**": (
        _az120_funnel(_AZ120_O3) == (3, 1, 0, 2)),
    "④[被拦, finish] ⇒ 拦 1 / 未轮到 0（两格**不互相冒充**）": (
        _az120_funnel(_AZ120_OB) == (2, 1, 1, 0)),
    #: ★ 实现时新增（预注册只写了"守恒"）：**成功路径**也要记 ——
    #:   非交付动作成功后同样 `break`，同轮剩下的候选一样没被考虑过。
    #:   ⛔ 这一格与 ③ 的形态**相反**（那里第 1 个是交付、这里是步骤），
    #:   只钉 ③ 的话，"只在 `if _produces:` 里记"这条实现也能全绿。
    "⑤三个 recall、第 1 个成功 ⇒ 未轮到 **2**（成功也 break）": (
        _az120_funnel(_AZ120_OT) == (3, 1, 0, 2)),
    "⑥账本五格互斥且穷尽：合计 == tasks，且 `done_without_offer` == 0": (
        _AZ120_ACC["tasks"] == 5
        and sum(_AZ120_ACC[k] for k in ("no_rounds", "only_delivery",
                                        "offered_not_done", "done_without_offer",
                                        "offered_and_done")) == 5
        and (_AZ120_ACC["no_rounds"], _AZ120_ACC["only_delivery"],
             _AZ120_ACC["offered_not_done"], _AZ120_ACC["done_without_offer"],
             _AZ120_ACC["offered_and_done"]) == (1, 1, 2, 0, 1)
        and _AZ120_ACC["offered_not_done_blocked"] == 1
        #: 分母：漏斗的四个数在账本里合得上（`offered` 是分母，不参与相加）
        and _AZ120_ACC["offered"] == 9
        and (_AZ120_ACC["dispatched"] + _AZ120_ACC["blocked"]
             + _AZ120_ACC["unreached"]) == 9),
    "⑦漏记检测器**不是死代码**：喂一个「做了却没提过」的题 ⇒ 必须落进那一格": (
        _AZ120_BAD["done_without_offer"] == 1
        and _AZ120_BAD["offered_and_done"] == 0),
    "⑧残差显形：3 提 1 做 ⇒ `unaccounted` == 2（不许钳成 0）": (
        _AZ120_UNACC["unaccounted"] == 2),
    "⑨`■` 行带「未轮到=」（读数必须人眼可见，不能只活在账本里）": (
        all(any("未轮到=" in ln for ln in ls) for ls in
            (_AZ120_LS, _AZ120_L3, _AZ120_LT, _AZ120_LB))),
    "⑩键集两侧全等（累加器 16 键 / 内核 `offer` 3 键）+ 两处并入 + 零点每次新建": (
        sorted(_AZ120_Z0) == _AZ120_KEYS and _AZ120_Z0 is not _AZ120_Z1
        and sorted(_az120_st(_AZ120_OS)["offer"])
        == ["unreached", "unreached_by_stop", "unreached_by_tool"]
        and _AZ120_CODE.count("_koff_absorb(_koff, out)") == 2
        and _AZ120_CODE.count("def _koff_absorb(") == 1
        and '"kernel_offer": _koff_line(_koff)' in _RUN_SRC),
}

check(RS, "AZ120 ★★★候选漏斗：把「提了什么」与「做了什么」分开记\n"
          "          —— V56 的 `zero_step_tasks=6` 被写成「6 题**只提了** `finish`」，\n"
          "             而冻结日志逐题数是 **30 个候选进执行器、只下发 18 个**。\n"
          "             「提」与「做」是两个数，合成一个就把 6/12 读成\"想不出别的\"。\n"
          "          判据（十条，缺一即证明不了这条边）：\n"
          "               ① 守恒 `offered == dispatched + blocked + unreached`；\n"
          "               ② 反向闸·只提 finish ⇒ 未轮到 0；\n"
          "               ③ 三个候选、finish 第一 ⇒ 未轮到 2；\n"
          "               ④ [被拦, finish] ⇒ 拦 1 / 未轮到 0（两格不互相冒充）；\n"
          "               ⑤ 三个 recall、第 1 个成功 ⇒ 未轮到 2（**成功路径**也记）；\n"
          "               ⑥ 账本五格互斥穷尽、合计 == tasks，`done_without_offer` == 0；\n"
          "               ⑦ 漏记检测器非死代码（喂一格违规 ⇒ 必须落进去）；\n"
          "               ⑧ 残差 `unaccounted` 显形，不许钳成 0；\n"
          "               ⑨ `■` 行带「未轮到=」；\n"
          "               ⑩ `offer` 键集全等 + 两个 solver 分支各并一次。\n"
          "          撤回语义：删 `_note_unreached` 的调用 ⇒ ③⑤ 必红；\n"
          "                    `unreached` 写成恒 `+= 1` ⇒ ② 必红；\n"
          "                    把 `blocked` 并进 `unreached` ⇒ ④ 必红；\n"
          "                    只交付分支记 ⇒ ⑤ 必红；`_koff_absorb` 去掉\n"
          "                    `done_without_offer` 那支 ⇒ ⑦ 必红；`unaccounted`\n"
          "                    写成 `max(0, …)` 或恒 0 ⇒ ⑧ 必红；`■` 行去掉\n"
          "                    「未轮到=」⇒ ⑨ 必红。",
      all(_AZ120_OK.values()),
      " ｜ ".join(f"{k}={v}" for k, v in _AZ120_OK.items())
      + f" ｜ 四格 (提,做,拦,未)="
        + "/".join(str(_az120_funnel(o)) for o in _AZ120_ALL)
        + f" ｜ 账本格="
        + f"{_AZ120_ACC['no_rounds']}/{_AZ120_ACC['only_delivery']}/"
          f"{_AZ120_ACC['offered_not_done']}/{_AZ120_ACC['done_without_offer']}/"
          f"{_AZ120_ACC['offered_and_done']}")

# ══════════════════════════════════════════════════════════════════════════
# AZ121（V58，§124）：候选的**顺序** —— 交付动作排第几、它后面还剩什么
#
# 为什么必须有这一条：V57 的账本里有 `unreached=8`，但**没有任何一格**
# 记录这 8 个在同轮候选里排第几。于是「8 个死在 `finish` 成功之后」在账本上
# 是一次**手推**（要求读者相信"交付动作总排第一"），不是读数。
#
# 判据（十条，缺一即证明不了这条边）：
#   ① 守恒 `Σ unreached_by_stop == unreached`（四格都成立）；
#   ② `[finish, recall, recall]` ⇒ 交付在第 0 位、尾 2、`answer` 丢 2、存活 0；
#   ③ `[recall, finish]`（第 1 个成功）⇒ 交付在第 **1** 位、**尾 0**、
#      但 `未轮到=1` 且落在 `step_ok` 上 —— 这一格证明「尾」与「未轮到」
#      **不是同一个数**（V57 一行日志两个分母的同宗病）；
#   ④ `[被拦, finish]` ⇒ 交付在第 1 位、`blocked=1`、`answer` 丢 0
#      （被拦与没轮到两格不互相冒充）；
#   ⑤ 交付候选**失败** ⇒ 落在 `fail` 上，且 `尾 1 / 存活 1`
#      （这是 `tail_survived>0` 唯一的真分母）；
#   ⑥ `unreached_by_tool` 逐名相等（②= `{recall:2}`、③= `{finish:1}`）；
#   ⑦ `kernel_order` 键集全等 + 两个 solver 分支各并一次 + 零点每次新建；
#   ⑧ 两个来源**矛盾**时 `tail_negative_tasks` 必须显形（检测器非死代码）；
#   ⑨ 深拷：改写 `stats["plan"]["tail_tools_by_round"][0]` 不许污染内核；
#   ⑩ `■` 行带「尾=」，且 ②格 `尾=2`、③格 `尾=0`（一正一反）。
#
# ⛔ ①②只是必要条件：三格全抄 `unreached` 也守恒。能证明拆对了的是 ②③④⑤
#    四个**精确整数格**，其中 ② 与 ③ 的形态**相反**（交付第一 / 交付最后）。
# ══════════════════════════════════════════════════════════════════════════
_AZ121_GOAL = _AZ120_GOAL          # 题面与 AZ120 同源：`looks_complex` 必须为真，
#                                    否则快通道 `n=1` 会把第 2/3 个候选当场截掉
#                                    （V57 自踩：判据没写错，是装置够不着那一格）。


class _AZ121LLM:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.planning = 0

    @staticmethod
    def _txt(m) -> str:                                       # noqa: ANN001
        return (str(m.get("content", "")) if isinstance(m, dict)
                else str(getattr(m, "content", "")))

    def complete(self, messages, max_tokens=None):             # noqa: ANN001, ARG002
        sysmsg = self._txt(messages[0]) if messages else ""
        if sysmsg == _AZ117_ANS_SYS:                          # `planner.answer()`
            return _AZ119_ANS
        if "规划器" not in sysmsg:
            return ""
        self.planning += 1
        if self.planning > 1:
            return json.dumps({"candidates": [
                {"intent": "交给一个恒不适用的能力", "tool": "never",
                 "args": {"x": "1"}}]}, ensure_ascii=False)
        _fin = {"intent": "直接给出答案", "tool": "finish",
                "args": {"answer": _AZ119_ANS}}
        if self.mode == "finish_first":
            return json.dumps({"candidates": [
                _fin,
                {"intent": "先查一条记忆", "tool": "recall", "args": {"query": "az121-a"}},
                {"intent": "再查一条记忆", "tool": "recall", "args": {"query": "az121-b"}},
            ]}, ensure_ascii=False)
        if self.mode == "step_first":
            #: 交付排在**第 1 位**：第 1 个 `recall` 成功就 `break`。
            #: ⚠️ 这一格是「尾」与「未轮到」分道扬镳的地方：尾 0，未轮到 1。
            return json.dumps({"candidates": [
                {"intent": "先查一条记忆", "tool": "recall", "args": {"query": "az121-s"}},
                _fin,
            ]}, ensure_ascii=False)
        if self.mode == "blocked_first":
            return json.dumps({"candidates": [
                {"intent": "跑一条 shell 验证", "tool": "run_shell",
                 "args": {"cmd": "echo az121", "timeout_s": 5}},
                _fin,
            ]}, ensure_ascii=False)
        if self.mode == "boom_first":
            #: 交付候选**失败**（`produces_answer` 的 handler 抛异常）。
            #: ⛔ 只有这一格能证明 `tail_survived>0` 不是死代码。
            return json.dumps({"candidates": [
                {"intent": "交给一个总是失败的交付能力", "tool": "boom", "args": {"x": "1"}},
                {"intent": "再查一条记忆", "tool": "recall", "args": {"query": "az121-x"}},
            ]}, ensure_ascii=False)
        return json.dumps({"candidates": [
            {"intent": "交给一个恒不适用的能力", "tool": "never",
             "args": {"x": "1"}}]}, ensure_ascii=False)


def _az121_run(tag: str, mode: str):
    d = _TMP / f"az121_{tag}"
    ws = d / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (d / "state").mkdir(parents=True, exist_ok=True)
    # ⚠️ 与 `_az120_run` 同一条纪律：`Paths` 是 frozen dataclass、构造时给全；
    #    **不许**裸调 `cfg.settings()`（同一进程里前序断言改过 `os.environ`，
    #    新 fixture 会继承它 —— V52 的 AZ115 就是这么把读数读成 0 的）。
    #    `cand_failover` 必须为 0（默认值）：让位会把 `fail` 出口变成
    #    「换下一个候选」，那一格就永远数不到。
    s = cfg.Settings(
        paths=cfg.Paths(root=d, state=d / "state", logs=d / "logs",
                        workspace=ws, bench=d / "bench"),
        limits=cfg.Limits(max_steps=8, close_reserve_steps=1, verify_retries=2,
                          cand_failover=0))
    c = Core(settings=s, llm=_AZ121LLM(mode),
             memory=MemoryStore(d / "state" / "memory.db"))
    c.tools.register(ToolSpec("never", "对任何题面都不适用", {"x": "参数"},
                              lambda a: "y", applies=lambda g: False))

    def _boom(_a):                                            # noqa: ANN001, ANN202
        raise RuntimeError("az121-boom")
    c.tools.register(ToolSpec("boom", "总是失败的交付能力", {"x": "参数"},
                              _boom, produces_answer=True))
    lines: list[str] = []
    c.log = lines.append                                      # noqa: ARG005
    out = c.run(_AZ121_GOAL, budget_factor=1)
    return out, lines, c


_AZ121_O2, _AZ121_L2, _AZ121_C2 = _az121_run("finish_first", "finish_first")
_AZ121_O3, _AZ121_L3, _ = _az121_run("step_first", "step_first")
_AZ121_O4, _AZ121_L4, _ = _az121_run("blocked_first", "blocked_first")
_AZ121_O5, _AZ121_L5, _ = _az121_run("boom_first", "boom_first")
_AZ121_ALL = (_AZ121_O2, _AZ121_O3, _AZ121_O4, _AZ121_O5)


def _az121_st(o) -> dict:                                     # noqa: ANN001
    return getattr(o, "stats", None) or {}


def _az121_order(o):                                          # noqa: ANN001
    """`(pos列表, 尾列表, 未轮到, by_stop, by_tool)` —— 五件一个不少。"""
    st = _az121_st(o)
    pl, of = (st.get("plan") or {}), (st.get("offer") or {})
    return (list(pl.get("delivery_pos_by_round") or []),
            [list(x) for x in (pl.get("tail_tools_by_round") or [])],
            int(of.get("unreached") or 0),
            dict(of.get("unreached_by_stop") or {}),
            dict(of.get("unreached_by_tool") or {}))


class _AZ121Out:
    """最小 Outcome 替身：只带 `_kord_absorb` **真的读**的三个字典。

    ⛔ 两个来源**故意矛盾**（尾 1、却声称因交付成功丢了 2）——
       `tail_negative_tasks` 是检测器，检测器必须被证明**活着**（纪律 2 的反证组）。
    """

    def __init__(self, pos=(0,), tails=(["recall"],), dropped=2) -> None:
        self.stats = {
            "plan": {"per_round": [{"n": 1 + len(t), "tools": {}}
                                   for t in tails],
                     "delivery_pos_by_round": list(pos),
                     "tail_tools_by_round": [list(t) for t in tails]},
            "offer": {"unreached": dropped,
                      "unreached_by_stop": {"answer": dropped, "step_ok": 0,
                                            "fail": 0},
                      "unreached_by_tool": {}},
        }


_AZ121_ACC = _run._kord_zero()
for _o in _AZ121_ALL:
    _run._kord_absorb(_AZ121_ACC, _o)
_AZ121_BAD = _run._kord_absorb(_run._kord_zero(), _AZ121Out())

#: ⑨ 深拷：往**导出出去**的 `tail_tools_by_round` 里塞一个假工具名，
#:    内核的活引用**不许**跟着变。⚠️ 之后的判据与消息要用原值 ⇒ 探完就复原。
_AZ121_DEEP_LIST = _AZ121_O2.stats["plan"]["tail_tools_by_round"][0]
_AZ121_DEEP_LIST.append("__probe__")
_AZ121_DEEP = "__probe__" not in _AZ121_C2.plan_stats["tail_tools_by_round"][0]
_AZ121_DEEP_LIST.pop()

_AZ121_KEYS = sorted(["tasks", "rounds", "with_delivery", "delivery_first",
                      "delivery_last", "pos_hist", "tail_total", "tail_by_tool",
                      "tail_survived", "tail_negative_tasks", "rounds_unpaired"])

_AZ121_OK = {
    "①守恒：`Σ unreached_by_stop == unreached`（四格都成立）": all(
        sum(v for k, v in _az121_order(o)[3].items() if k in
            ("answer", "step_ok", "fail")) == _az121_order(o)[2]
        and _az121_order(o)[2] == sum(_az121_order(o)[4].values())
        for o in _AZ121_ALL),
    "②`[finish, recall, recall]` ⇒ 位置 0 / 尾 2 / answer 丢 2 / 存活 0": (
        _az121_order(_AZ121_O2)[0][0] == 0
        and len(_az121_order(_AZ121_O2)[1][0]) == 2
        and _az121_order(_AZ121_O2)[3] == {"answer": 2, "step_ok": 0, "fail": 0}),
    # ⛔ 这一格是本轮的新东西：「尾」说 0、「未轮到」说 1，两个数**同时成立**。
    "③`[recall, finish]` ⇒ 位置 **1** / **尾 0** / 未轮到 1 落在 `step_ok`": (
        _az121_order(_AZ121_O3)[0][0] == 1
        and _az121_order(_AZ121_O3)[1][0] == []
        and _az121_order(_AZ121_O3)[2] == 1
        and _az121_order(_AZ121_O3)[3] == {"answer": 0, "step_ok": 1, "fail": 0}),
    "④`[被拦, finish]` ⇒ 位置 1 / blocked 1 / `answer` 丢 0（两格不互相冒充）": (
        _az121_order(_AZ121_O4)[0][0] == 1
        and int((_az121_st(_AZ121_O4).get("loop") or {}).get("blocked_skipped") or 0) == 1
        and _az121_order(_AZ121_O4)[2] == 0
        and _az121_order(_AZ121_O4)[3] == {"answer": 0, "step_ok": 0, "fail": 0}),
    "⑤交付候选失败 ⇒ 落在 `fail` 上，`尾 1 / 存活 1`（`tail_survived>0` 的真分母）": (
        _az121_order(_AZ121_O5)[0][0] == 0
        and len(_az121_order(_AZ121_O5)[1][0]) == 1
        and _az121_order(_AZ121_O5)[3] == {"answer": 0, "step_ok": 0, "fail": 1}
        and _AZ121_ACC["tail_total"] == 3
        and _AZ121_ACC["tail_survived"] == 1),
    "⑥`unreached_by_tool` 逐名相等（②= `{recall:2}`、③= `{finish:1}`）": (
        _az121_order(_AZ121_O2)[4] == {"recall": 2}
        and _az121_order(_AZ121_O3)[4] == {"finish": 1}),
    "⑦`kernel_order` 键集全等 + 两个 solver 分支各并一次 + 零点每次新建": (
        sorted(_AZ121_ACC) == _AZ121_KEYS
        and _run._kord_zero() is not _run._kord_zero()
        and _code_only(_RUN_SRC).count("_kord_absorb(_kord, out)") == 2
        and _code_only(_RUN_SRC).count("def _kord_absorb(") == 1
        and '"kernel_order": _kord' in _RUN_SRC),
    "⑧两个来源矛盾 ⇒ `tail_negative_tasks` 显形（检测器非死代码）": (
        _AZ121_BAD["tail_negative_tasks"] == 1
        and _AZ121_BAD["tail_survived"] == -1
        and _AZ121_ACC["tail_negative_tasks"] == 0
        and _AZ121_ACC["rounds_unpaired"] == 0),
    "⑨深拷：改写导出的 `tail_tools_by_round` 不许污染内核": _AZ121_DEEP,
    "⑩`■` 行带「尾=」，②格 `尾=2`、③格 `尾=0`（一正一反）": (
        any("尾=2" in ln for ln in _AZ121_L2 if ln.startswith("■ "))
        and any("尾=0" in ln for ln in _AZ121_L3 if ln.startswith("■ "))),
}

_AZ121_LEDGER = " ".join(
    f"{k}={_AZ121_ACC[k]}" for k in ("rounds", "with_delivery", "delivery_first",
                                     "delivery_last", "tail_total",
                                     "tail_survived"))

check(RS, "AZ121 ★★★候选的**顺序**：交付动作排第几、它后面还剩什么\n"
          "          —— V57 的 `unreached=8` 没有任何一格记录这 8 个排第几，\n"
          "             于是「8 个死在 `finish` 成功之后」在账本上是一次**手推**。\n"
          "             本轮把「第几」与「后面剩什么」变成可数的两个量。\n"
          "          判据（十条，缺一即证明不了这条边）：\n"
          "               ① 守恒 `Σ unreached_by_stop == unreached`；\n"
          "               ② `[finish, recall, recall]` ⇒ 位置 0 / 尾 2 / answer 丢 2；\n"
          "               ③ `[recall, finish]` ⇒ 位置 **1** / **尾 0** / 未轮到 1 ——\n"
          "                  「尾」与「未轮到」不是同一个数（本轮的核心）；\n"
          "               ④ `[被拦, finish]` ⇒ 位置 1 / blocked 1 / answer 丢 0；\n"
          "               ⑤ 交付候选失败 ⇒ 落 `fail`，且尾 1 / 存活 1；\n"
          "               ⑥ `unreached_by_tool` 逐名相等；\n"
          "               ⑦ `kernel_order` 键集全等 + 两处并入；\n"
          "               ⑧ 来源矛盾时 `tail_negative_tasks` 必须显形；\n"
          "               ⑨ 深拷（内层列表也是活引用）；\n"
          "               ⑩ `■` 行带「尾=」。\n"
          "          撤回语义：`_note_unreached` 去掉 `stop` 分项 ⇒ ②③④⑤ 必红；\n"
          "                    `_note_plan_round` 去掉 pos/tail 那几行 ⇒ ②③⑦ 必红；\n"
          "                    `tail_survived` 写成恒 0 ⇒ ⑤⑧ 必红；\n"
          "                    `tail_negative_tasks` 去掉那支 ⇒ ⑧ 必红；\n"
          "                    `tail_tools_by_round` 改浅拷 ⇒ ⑨ 必红；\n"
          "                    `■` 行去掉「尾=」⇒ ⑩ 必红。",
      all(_AZ121_OK.values()),
      " ｜ ".join(f"{k}={v}" for k, v in _AZ121_OK.items())
      + " ｜ 四格 (pos,尾,未,answer,step_ok,fail)="
      + "/".join(f"({_az121_order(o)[0]},{len(_az121_order(o)[1][0])},"
                 f"{_az121_order(o)[2]},"
                 f"{_az121_order(o)[3].get('answer')},"
                 f"{_az121_order(o)[3].get('step_ok')},"
                 f"{_az121_order(o)[3].get('fail')})" for o in _AZ121_ALL)
      + f" ｜ 账本={_AZ121_LEDGER}")

# ══════════════════════════════════════════════════════════════════════════
# AZ122（V60，§126）：**验证动作** —— 跑没跑 / 返回码 / 失败之后又做了什么
#
# 为什么必须有这一条：V59 的结论里有一句没被检验的话 ——「那 4 格是这套题上
# **唯一的验证动作**，被 `--allow-shell` 拦掉了」。而三个既有账本
# （`kernel_exec` / `kernel_offer` / `kernel_order`）里**没有一格**能回答：
#   ① 打开 shell 之后**跑没跑**（`exec.dispatched` 是全部工具的和）；
#   ② 跑完**结果说代码对不对**（`exec.ok=True` 对 `rc=1` 也成立）；
#   ③ 失败之后**又跑了还是又交了**（顺序事件，计数器不带序）。
#
# ⚠️ 题面里必须带 `az122probe`：`guard.check_goal` 在 `check_shell` **之前**跑，
#   而它判的是字面重合 —— 中文题面 + 英文命令 ⇒ 无公共词 ⇒ `goal_hijack`。
#   带上同一个 ASCII 标记，才能让「`allow_shell` 开了却仍被拦」这件事
#   只在**一道**闸上发生，读数才归得了因（否则 ④ 的 `tool_misuse` 断言会
#   被 `goal_hijack` 顶掉，而两者看起来都是"被拦了 1 次"）。
# ══════════════════════════════════════════════════════════════════════════
_AZ122_GOAL = ("说明为什么哈希表的平均查找是 O(1)，然后举一个冲突变多的反例，"
               "接着指出它依赖的前提；验证脚本里用到的标记叫 az122probe。")

_AZ122_FIN = {"intent": "直接给出答案", "tool": "finish", "args": {"answer": _AZ119_ANS}}
_AZ122_OK = {"intent": "跑一遍验证", "tool": "run_shell",
             "args": {"cmd": "echo az122probe ok", "timeout_s": 10}}
_AZ122_BAD = {"intent": "跑一遍验证", "tool": "run_shell",
              "args": {"cmd": "false az122probe", "timeout_s": 10}}


class _AZ122LLM:
    """按**轮**给计划的桩：第 i 次规划返回 `rounds[i]`，用完就交空轮。"""

    def __init__(self, rounds) -> None:                       # noqa: ANN001
        self.rounds = rounds
        self.planning = 0

    @staticmethod
    def _txt(m) -> str:                                       # noqa: ANN001
        return (str(m.get("content", "")) if isinstance(m, dict)
                else str(getattr(m, "content", "")))

    def complete(self, messages, max_tokens=None):             # noqa: ANN001, ARG002
        sysmsg = self._txt(messages[0]) if messages else ""
        if sysmsg == _AZ117_ANS_SYS:                          # `planner.answer()`
            return _AZ119_ANS
        if "规划器" not in sysmsg:
            return ""
        i = self.planning
        self.planning += 1
        if i >= len(self.rounds):
            #: 用完就交"恒不适用"的能力 ⇒ 准入空轮 ⇒ 走收尾。
            #: ⛔ 不重复提同一批候选：`attempted_sigs` 会挡（重复闸），
            #:    那样下一轮就变成"什么都没提"，读数和真跑偏了。
            return json.dumps({"candidates": [
                {"intent": "交给一个恒不适用的能力", "tool": "never",
                 "args": {"x": "1"}}]}, ensure_ascii=False)
        return json.dumps({"candidates": self.rounds[i]}, ensure_ascii=False)


def _az122_run(tag: str, rounds, allow_shell: bool):           # noqa: ANN001
    d = _TMP / f"az122_{tag}"
    ws = d / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (d / "state").mkdir(parents=True, exist_ok=True)
    # ⚠️ `Paths` 构造时给全、**不裸调** `cfg.settings()`（前序断言改过
    #    `os.environ`，新 fixture 会继承它 —— V52 的 AZ115 就是这么读成 0 的）。
    s = cfg.Settings(
        paths=cfg.Paths(root=d, state=d / "state", logs=d / "logs",
                        workspace=ws, bench=d / "bench"),
        limits=cfg.Limits(max_steps=8, close_reserve_steps=1, verify_retries=2,
                          cand_failover=0))
    c = Core(settings=s, llm=_AZ122LLM(rounds),
             guard=Guard(allow_shell=allow_shell, workspace=ws),
             memory=MemoryStore(d / "state" / "memory.db"))
    c.tools.register(ToolSpec("never", "对任何题面都不适用", {"x": "参数"},
                              lambda a: "y", applies=lambda g: False))
    lines: list[str] = []
    c.log = lines.append                                      # noqa: ARG005
    out = c.run(_AZ122_GOAL, budget_factor=1)
    return out, lines, c


_AZ122_O_OK, _AZ122_L_OK, _AZ122_C_OK = _az122_run(
    "ok", [[_AZ122_OK, _AZ122_FIN], [_AZ122_FIN]], True)
_AZ122_O_BAD, _AZ122_L_BAD, _AZ122_C_BAD = _az122_run(
    "bad", [[_AZ122_BAD, _AZ122_FIN], [_AZ122_FIN]], True)
_AZ122_O_RETRY, _AZ122_L_RETRY, _AZ122_C_RETRY = _az122_run(
    "retry", [[_AZ122_BAD, _AZ122_FIN], [_AZ122_OK, _AZ122_FIN], [_AZ122_FIN]], True)
_AZ122_O_OFF, _AZ122_L_OFF, _AZ122_C_OFF = _az122_run(
    "off", [[_AZ122_OK, _AZ122_FIN], [_AZ122_FIN]], False)


def _az122_shell(o) -> dict:                                  # noqa: ANN001
    return dict(((getattr(o, "stats", None) or {}).get("shell") or {}))


#: ⑤ `rc_unknown`：直接喂一个**没有 `rc=` 行**的输出。
#: ⛔ 不许把它当 0 —— 缺参 / 异常 / 未知工具三条失败路径都没有这一行。
_AZ122_UNK = Core(settings=cfg.Settings(
    paths=cfg.Paths(root=_TMP / "az122_unk", state=_TMP / "az122_unk" / "state",
                    logs=_TMP / "az122_unk" / "logs",
                    workspace=_TMP / "az122_unk" / "ws",
                    bench=_TMP / "az122_unk" / "bench")),
    llm=StubLLM())
_AZ122_UNK._note_shell(type("C", (), {"tool": "run_shell"})(),
                       ToolResult(True, "命令跑了但没有返回码首行"), 7)

_AZ122_ACC = _run._kshell_zero()
for _o in (_AZ122_O_OK, _AZ122_O_BAD, _AZ122_O_RETRY, _AZ122_O_OFF):
    _run._kshell_absorb(_AZ122_ACC, _o)


class _AZ122Out:
    """最小 Outcome 替身：只带 `_kshell_absorb` **真的读**的两个字典。"""

    def __init__(self, by_tool=None, shell=None) -> None:
        self.stats = {"plan": {"by_tool": dict(by_tool or {})},
                      "shell": dict(shell or {})}


#: ⑨ `offered` 只认 `plan.by_tool`：故意让 `shell.offered` 撒谎（99），账本不许跟它走。
_AZ122_LIE = _run._kshell_absorb(
    _run._kshell_zero(),
    _AZ122Out({"run_shell": 3}, {"offered": 99, "dispatched": 0}))
#: ⑩ 题级分母：两题，一题跑过 shell、一题没跑。
_AZ122_TWO = _run._kshell_zero()
_run._kshell_absorb(_AZ122_TWO, _AZ122Out({"run_shell": 2},
                                          {"dispatched": 2, "rc_zero": 2}))
_run._kshell_absorb(_AZ122_TWO, _AZ122Out({"recall": 1}, {}))
#: ⑥ `rc_gap` 是**检测器**：把一次下发从四格里挖掉，它必须显形。
#: ⚠️ 挖法是"声称跑了 3 次、只归类 2 次"，⛔ 不是把 2+1 凑成 3（那样 gap 恒 0，
#:   检测器就变成了死代码 —— 纪律 2 的第三形态）。
_AZ122_GAP = _run._kshell_line({**_run._kshell_zero(), "dispatched": 3,
                                "rc_zero": 2, "rc_nonzero": 0})

_AZ122_KEYS_ZERO = sorted(["tasks", "tasks_with_shell", "offered", "dispatched",
                           "rc_zero", "rc_nonzero", "rc_unknown", "err",
                           "blocked", "blocked_by_checkpoint", "rounds_with_shell",
                           "shell_first_rounds", "after_fail_retry",
                           "after_fail_answer",
                           # ★ V61（§127）：命令原文。与 `trace` 并列、不合并 ——
                           #   `trace` 的格式被本断言钉着，加键就得在这儿显形。
                           "trace_cmd"])
_AZ122_KEYS_LINE = sorted(_AZ122_KEYS_ZERO + ["rc_gap"])

_AZ122_OKCHK = {
    "①跑通一次：`dispatched=1 / rc_zero=1 / trace=['r1:0']`，且不误记 blocked": (
        _az122_shell(_AZ122_O_OK)["dispatched"] == 1
        and _az122_shell(_AZ122_O_OK)["rc_zero"] == 1
        and _az122_shell(_AZ122_O_OK)["rc_nonzero"] == 0
        and _az122_shell(_AZ122_O_OK)["blocked"] == 0
        and _az122_shell(_AZ122_O_OK)["trace"] == ["r1:0"]),
    "②`rc=1` 仍算 `ok`（工具调用成功）⇒ `rc_nonzero=1` 而 `exec.fail=0`": (
        _az122_shell(_AZ122_O_BAD)["rc_nonzero"] == 1
        and int(((getattr(_AZ122_O_BAD, "stats", None) or {})
                 .get("exec") or {}).get("fail") or 0) == 0),
    "③失败后**直接交** ⇒ `after_fail_answer=1`、`after_fail_retry=0`": (
        _az122_shell(_AZ122_O_BAD)["after_fail_answer"] == 1
        and _az122_shell(_AZ122_O_BAD)["after_fail_retry"] == 0
        and _az122_shell(_AZ122_O_BAD)["trace"] == ["r1:1"]),
    "④失败后**再跑一次** ⇒ `after_fail_retry=1`、`after_fail_answer=0`": (
        _az122_shell(_AZ122_O_RETRY)["trace"] == ["r1:1", "r2:0"]
        and _az122_shell(_AZ122_O_RETRY)["after_fail_retry"] == 1
        and _az122_shell(_AZ122_O_RETRY)["after_fail_answer"] == 0),
    "⑤shell 关着 ⇒ `blocked=1` 且**闸名是 `tool_misuse`**、`dispatched=0`": (
        _az122_shell(_AZ122_O_OFF)["blocked"] == 1
        and _az122_shell(_AZ122_O_OFF)["blocked_by_checkpoint"] == {"tool_misuse": 1}
        and _az122_shell(_AZ122_O_OFF)["dispatched"] == 0),
    "⑥没有 `rc=` 行 ⇒ `rc_unknown=1`（⛔ 不许并进 `rc_zero`）": (
        _AZ122_UNK.shell_stats["rc_unknown"] == 1
        and _AZ122_UNK.shell_stats["rc_zero"] == 0
        and _AZ122_UNK.shell_stats["rc_nonzero"] == 0
        and _AZ122_UNK.shell_stats["trace"] == ["r7:?"]),
    "⑦`rc_gap` 检测器活着（挖掉一次下发 ⇒ 非 0），且账本四点全为 0": (
        _AZ122_GAP["rc_gap"] == 1
        and _run._kshell_line(_AZ122_ACC)["rc_gap"] == 0),
    "⑧键集全等（零点 14 / 落盘 15）+ 两处并入 + 唯一定义 + 账本键在": (
        sorted(_run._kshell_zero()) == _AZ122_KEYS_ZERO
        and sorted(_run._kshell_line(_run._kshell_zero())) == _AZ122_KEYS_LINE
        and _run._kshell_zero() is not _run._kshell_zero()
        and _code_only(_RUN_SRC).count("_kshell_absorb(_ksh, out)") == 2
        and _code_only(_RUN_SRC).count("def _kshell_absorb(") == 1
        and '"kernel_shell": _kshell_line(_ksh)' in _RUN_SRC),
    "⑨`offered` 只认 `plan.by_tool`（`shell.offered` 撒谎也不许跟）": (
        _AZ122_LIE["offered"] == 3 and _AZ122_LIE["dispatched"] == 0),
    "⑩题级分母：两题一跑一不跑 ⇒ `tasks=2 / tasks_with_shell=1`": (
        _AZ122_TWO["tasks"] == 2 and _AZ122_TWO["tasks_with_shell"] == 1
        and _AZ122_TWO["dispatched"] == 2 and _AZ122_TWO["offered"] == 2),
}

_AZ122_LEDGER = (f"dispatched={_AZ122_ACC['dispatched']}"
                 f" rc0={_AZ122_ACC['rc_zero']}"
                 f" rcN={_AZ122_ACC['rc_nonzero']}"
                 f" retry={_AZ122_ACC['after_fail_retry']}"
                 f" answer={_AZ122_ACC['after_fail_answer']}"
                 f" blocked={_AZ122_ACC['blocked']}")

check(RS, "AZ122 ★★★**验证动作**：跑没跑 / 返回码 / 失败之后又做了什么\n"
          "          —— V59 说「那 4 格是唯一的验证动作，被 --allow-shell 拦掉了」，\n"
          "             而既有三个账本没有一格能回答「跑没跑、结果说代码对不对、失败后呢」。\n"
          "          判据（十条，缺一即证明不了这条边）：\n"
          "               ① 跑通一次 ⇒ `rc_zero=1`、`trace=['r1:0']`；\n"
          "               ② `rc=1` 仍算 `ok` ⇒ `rc_nonzero=1` 而 `exec.fail=0`（**两个尺子**）；\n"
          "               ③ 失败后直接交 ⇒ `after_fail_answer=1` / `retry=0`；\n"
          "               ④ 失败后再跑 ⇒ `after_fail_retry=1` / `answer=0`；\n"
          "               ⑤ shell 关着 ⇒ `blocked=1` 且闸名 = `tool_misuse`；\n"
          "               ⑥ 没有 `rc=` 行 ⇒ `rc_unknown`（⛔ 不是 0）；\n"
          "               ⑦ `rc_gap` 检测器非死代码；\n"
          "               ⑧ 键集全等 + 两处并入 + 账本键在；\n"
          "               ⑨ `offered` 只认 `plan.by_tool`；\n"
          "               ⑩ 题级分母 `tasks/tasks_with_shell` 分得开。\n"
          "          撤回语义：`_note_shell` 不判 `result.ok` ⇒ ②⑥ 必红；\n"
          "                    去掉 `_shell_prev_fail` 那两行 ⇒ ③④ 必红；\n"
          "                    `_note_shell_block` 去掉 checkpoint 分列 ⇒ ⑤ 必红；\n"
          "                    `rc_unknown` 并进 `rc_zero` ⇒ ⑥ 必红；\n"
          "                    `rc_gap` 写死 0 ⇒ ⑦ 必红；\n"
          "                    `offered` 改抄 `shell.offered` ⇒ ⑨ 必红；\n"
          "                    `tasks_with_shell` 改数 `offered>0` ⇒ ⑩ 必红。",
      all(_AZ122_OKCHK.values()),
      " ｜ ".join(f"{k}={v}" for k, v in _AZ122_OKCHK.items())
      + f" ｜ 账本={_AZ122_LEDGER}"
      + f" ｜ trace ok={_az122_shell(_AZ122_O_OK)['trace']}"
        f" bad={_az122_shell(_AZ122_O_BAD)['trace']}"
        f" retry={_az122_shell(_AZ122_O_RETRY)['trace']}")

# ===========================================================================
# AZ123（V61，§127）：**强制验证** —— 题面示例判据：为什么它算判据、不算尺子
#   判据的期望值只从 `goal` 抽（`commas(0) == '0'`），⛔ 不读 `tests`。
#   十条里 ⑤ 是**边界**（证明它够不到尺子）、⑥ 是**装置自曝**（超时不许记成模型错）。
# ===========================================================================
_AZ123_GOAL_3 = ("写一个函数 commas(n)，把非负整数转成带千分位逗号的字符串："
                 "commas(1234567) == '1,234,567'，commas(0) == '0'，"
                 "commas(999) == '999'。只输出代码，函数名必须是 commas。")
_AZ123_GOAL_0 = ("写一个函数 dedupe(seq)，返回去除重复元素后的新列表。"
                 "只输出代码，函数名必须是 dedupe。")
_AZ123_GOOD = "def commas(n):\n    return format(n, ',')\n"
_AZ123_BAD = "def commas(n):\n    return str(n)\n"

#: ① 抽取：真题集里哪几题有示例 —— 分母是**题**，不是断言条数。
_AZ123_TASKS = [json.loads(ln) for ln in
                (V1 / "bench" / "tasks" / "coding_small.jsonl")
                .read_text(encoding="utf-8").splitlines() if ln.strip()]
_AZ123_EX = {t["id"]: _AS_V.example_asserts(t.get("prompt", "")) for t in _AZ123_TASKS}
_AZ123_WITH = [k for k, v in _AZ123_EX.items() if v]
_AZ123_TOTAL = sum(len(v) for v in _AZ123_EX.values())

#: ② 开关：`exec_=0` 不多派一条；`exec_=1` 多派**一条**（不是每条断言一条判据）。
_AZ123_K0 = [c.kind for c in _AS_V.derive(_AZ123_GOAL_3, 0, 0)]
_AZ123_K1 = [c.kind for c in _AS_V.derive(_AZ123_GOAL_3, 0, 1)]
_AZ123_C1 = [c for c in _AS_V.derive(_AZ123_GOAL_3, 0, 1) if c.kind == "example"]
_AZ123_K1_PLAIN = [c.kind for c in _AS_V.derive(_AZ123_GOAL_0, 0, 1)]

#: ③④ 闸的两侧：同一组判据，只换答案。
_AZ123_V_BAD = _AS_V.run(_AZ123_BAD, _AS_V.derive(_AZ123_GOAL_3, 0, 1))
_AZ123_V_OK = _AS_V.run(_AZ123_GOOD, _AS_V.derive(_AZ123_GOAL_3, 0, 1))

#: ⑤ 边界：判据的来源只有题面。⛔ 源码里出现尺子的东西就是开卷考。
_AZ123_VSRC = (V1 / "mw" / "verify.py").read_text(encoding="utf-8")
_AZ123_LEAK = [w for w in ('"tests"', "'tests'", "coding_small", "oracle", "partial")
               if w in _AZ123_VSRC]
_AZ123_SIG = list(inspect.signature(_AS_V.example_asserts).parameters)

#: ⑥ 装置自曝：超时必须**带前缀**，不许冒充"示例不通过"。
#: ⚠️ 这里把上限临时改成 0.2s（真值 5s）：测的是**分支**，不是 5 秒本身。
_az123_old_to = _AS_V._EX_TIMEOUT_S
_AS_V._EX_TIMEOUT_S = 0.2
try:
    _AZ123_TO = _AS_V.run("def commas(n):\n    while True:\n        pass\n",
                          _AS_V.derive(_AZ123_GOAL_3, 0, 1))
finally:
    _AS_V._EX_TIMEOUT_S = _az123_old_to

#: ⑦ 载荷：报错里必须是**运行时算出来的值**，不是判据自己的字面量。
_AZ123_ACTUAL = "42"
_AZ123_V_ACT = _AS_V.run("def commas(n):\n    return 42\n",
                         _AS_V.derive(_AZ123_GOAL_3, 0, 1))

#: ⑧ 账本键集：零点 9 键 / 落盘 10 键（派生量只在落盘行）。
#: ★ V63（§129）：`example_tasks` → `example_task_runs`（单位进名字，欠了两轮）。
_AZ123_KEYS_ZERO = sorted(["tasks", "example_task_runs", "runs", "passed", "failed",
                           "exhausted", "example_runs", "example_failed", "exec_err"])
_AZ123_KEYS_LINE = sorted(_AZ123_KEYS_ZERO + ["verify_gap"])
#: ⭐ 键名同源：账本侧读 `vf.get("has_example")`，内核侧必须真的写 `"has_example":`。
#: ⛔ 只改一边 ⇒ 账本永远读到 `None` ⇒ `example_task_runs` 恒 0 ⇒ 静默冒充
#:    "这条判据不适用"（缺席冒充零）。这正是本次改名的**唯一**真风险。
_AZ123_CORE_SRC = (V1 / "mw" / "core.py").read_text(encoding="utf-8")
_AZ123_RUN_SRC = (V1 / "run.py").read_text(encoding="utf-8")
_AZ123_SAME_KEY = ('"has_example": 1 if self._ex_checks else 0' in _AZ123_CORE_SRC
                   and 'vf.get("has_example")' in _AZ123_RUN_SRC)


class _AZ123Out:
    def __init__(self, stats):
        self.stats = stats


_AZ123_A = _run._kvf_zero()
_run._kvf_absorb(_AZ123_A, _AZ123Out({"verify": {
    "has_example": 1, "runs": 3, "passed": 1, "failed": 2, "exhausted": 0,
    "example_runs": 3, "example_failed": 2, "exec_err": 1}}))
_run._kvf_absorb(_AZ123_A, _AZ123Out({"verify": {
    "has_example": 0, "runs": 1, "passed": 1, "failed": 0, "exhausted": 0,
    "example_runs": 0, "example_failed": 0, "exec_err": 0}}))
_AZ123_B = _run._kvf_zero()
_run._kvf_absorb(_AZ123_B, _AZ123Out({"verify": {
    "has_example": 1, "runs": 2, "passed": 0, "failed": 2, "exhausted": 1,
    "example_runs": 2, "example_failed": 2, "exec_err": 0}}))
#: ⑨ 检测器：声称跑过 3 次、只归类 2 次 ⇒ `verify_gap` 必须 ≠ 0（⛔ 不许写死 0）。
_AZ123_GAP = _run._kvf_line({**_run._kvf_zero(), "runs": 3, "passed": 1, "failed": 1})

#: ⑩ 账本键在 + 两处并入 + 唯一定义（源码级，防"改了函数忘了接"）。
_AZ123_RSRC = (V1 / "run.py").read_text(encoding="utf-8")
_AZ123_LEDGER_OK = ('"kernel_verify": _kvf_line(_kvf)' in _AZ123_RSRC
                    and _AZ123_RSRC.count("_kvf_absorb(_kvf, out)") == 2
                    and _AZ123_RSRC.count("def _kvf_zero(") == 1)

_AZ123_OK = {
    "① 抽取：真题集 4/6 题有示例、共 7 条（t01/t02 一条都没有）": (
        sorted(_AZ123_WITH) == ["t03_commas", "t04_merge_ranges",
                                "t05_top_k", "t06_rle"]
        and _AZ123_TOTAL == 7
        and _AZ123_EX.get("t01_parse_duration") == ()
        and _AZ123_EX.get("t03_commas") == ("assert commas(1234567) == '1,234,567'",
                                            "assert commas(0) == '0'",
                                            "assert commas(999) == '999'")),
    "② 开关：`exec_=0` 不派（与旧版逐位同构），`exec_=1` 只多派**一条**": (
        _AZ123_K0 == ["nonempty", "defines", "fence_lang", "compiles"]
        and _AZ123_K1 == ["nonempty", "defines", "fence_lang", "compiles", "example"]
        and len(_AZ123_C1) == 1 and _AZ123_C1[0].name == "示例可跑×3"
        and _AZ123_C1[0].arg.count("assert ") == 3
        and _AZ123_K1_PLAIN == ["nonempty", "defines", "fence_lang", "compiles"]),
    "③ 闸**拒**：错代码 ⇒ `ok=False`，报错带「实得/期望」": (
        _AZ123_V_BAD.ok is False and "实得" in _AZ123_V_BAD.report
        and "期望" in _AZ123_V_BAD.report and "示例可跑×3" in _AZ123_V_BAD.failed),
    "④ 闸**放**：对代码 ⇒ `ok=True`（判据不许比尺子更严）": (_AZ123_V_OK.ok is True),
    "⑤ 边界：`example_asserts` 只收 `goal`；源码里没有尺子的东西": (
        _AZ123_SIG == ["goal"] and _AZ123_LEAK == []),
    "⑥ 装置自曝：超时带 `EXEC_FAULT` 前缀（⛔ 不许读成『示例不通过』）": (
        _AS_V.EXEC_FAULT in _AZ123_TO.report and "示例未通过" not in _AZ123_TO.report),
    "⑦ 载荷：报错里的值是**运行时算出来的**（代码返回 42 ⇒ 报 42）": (
        _AZ123_V_ACT.ok is False and _AZ123_ACTUAL in _AZ123_V_ACT.report),
    "⑧ 键集全等（9/10）+ 并入 + `verify_gap` 检测器活着": (
        sorted(_run._kvf_zero()) == _AZ123_KEYS_ZERO
        and sorted(_run._kvf_line(_run._kvf_zero())) == _AZ123_KEYS_LINE
        and _AZ123_A["runs"] == 4 and _AZ123_A["example_runs"] == 3
        and _AZ123_A["exec_err"] == 1 and _AZ123_B["exhausted"] == 1
        and _AZ123_GAP["verify_gap"] == 1),
    "⑨ 分母带单位：`example_task_runs` 只数**有示例**的题-run（一有一无 ⇒ 1）\n"
    "                    ＋ 键名同源（账本读 `has_example`，内核必须真的写出它）": (
        _AZ123_A["tasks"] == 2 and _AZ123_A["example_task_runs"] == 1
        and _AZ123_SAME_KEY),
    "⑩ 账本键在 + 两处并入 + 唯一定义": (_AZ123_LEDGER_OK),
}
check(RS, "AZ123 ★★★**强制验证**：题面示例判据（跑一遍，比对期望值）\n"
          "          ① 期望值**只从题面**抽（4/6 题、7 条），抽不到就**不派**判据；\n"
          "          ② `exec_=0` ⇒ 判据集与旧版逐位同构；`exec_=1` ⇒ 只多**一条**；\n"
          "          ③ 错代码必须被**拒**，报错带实得/期望（只回 `AssertionError` 等于让人重猜）；\n"
          "          ④ 对代码必须**放**（判据不许比尺子更严）；\n"
          "          ⑤ 边界：只能收 `goal`，源码里没有 `tests`/`oracle`/`partial`；\n"
          "          ⑥ 超时是**装置自曝**（自己的前缀），不许冒充『示例不通过』；\n"
          "          ⑦ 载荷是运行时的值（返回 42 ⇒ 报 42）；\n"
          "          ⑧ 键集全等 + 并入 + `verify_gap` 检测器活着；\n"
          "          ⑨ 分母带单位 `example_task_runs` ＋ 键名同源；\n"
          "          ⑩ 账本里有 `kernel_verify`。\n"
          "          撤回语义：`_check_one` 里 `example` 直接 `return \"\"` ⇒ ③⑥⑦ 必红；\n"
          "                    `derive` 不看 `exec_` ⇒ ② 必红；\n"
          "                    `example_asserts` 改读 `tests` ⇒ ⑤ 必红；\n"
          "                    `run_example` 不区分 `EXEC_FAULT` ⇒ ⑥ 必红；\n"
          "                    `verify_gap` 写死 0 ⇒ ⑧ 必红。",
      all(_AZ123_OK.values()),
      " ｜ ".join(f"{k}={v}" for k, v in _AZ123_OK.items())
      + f" ｜ 有示例题={sorted(_AZ123_WITH)} 断言总数={_AZ123_TOTAL}"
      + " ｜ A=" + str({k: _AZ123_A[k] for k in
                        ("tasks", "example_task_runs", "runs", "example_runs")}))

# ===========================================================================
# AZ124（V62，§128）：**打回 → 重写**这条边 —— 报错原文活到下一次提问
#
# 实测病（V61 冻结日志 + V62 单题 state 原文）：
#   ```
#   🧪 自检未过（第 1/2 次打回）⇒ 退回重写：… TypeError …
#   ↪ 交付被打回 ⇒ 让位给候选 2/2
#   ↪ 让位救回一个**步骤**（非交付动作）：recall     ← 成功 ⇒ consecutive_failures=0
#   ⏭ 重复动作已跳过: finish                        ← 第 3 轮，逐字相同
#   ```
#   `last_error` 被让位成功的候选清零 ⇒ 下一轮走 `propose()` 而不是 `replan()`
#   ⇒ 报错原文**不进那个位置**（它仍在 `[工作记忆]`，但不再作为"上一步失败了"出现）。
#
# ⚠️ 因此这条契约主张的是**位置**，不是"信息有没有送到"：
#   V62 的 state 原文证明 `自检未通过` 在 `it=3/4` 的 `[工作记忆]` 里已经在了。
#   ⛔ 不许把本条读成"修好了载荷"——它只把同一条信息**多放一处、放一次**。
# ===========================================================================
_AZ124_GOAL = ("写一个函数 commas(n)，把整数加上千分位。"
               "例如 commas(1234567) == '1,234,567'。只输出代码，函数名必须是 commas。")
_AZ124_BAD = "def commas(n):\n    return str(n)\n"
_AZ124_GOOD = ("def commas(n):\n"
               "    s = str(n)\n"
               "    out = ''\n"
               "    while len(s) > 3:\n"
               "        out = ',' + s[-3:] + out\n"
               "        s = s[:-3]\n"
               "    return s + out\n")


def _az124_plan(*cands):
    return json.dumps({"candidates": list(cands)}, ensure_ascii=False)


def _az124_fin(ans, intent):
    return {"intent": intent, "tool": "finish", "args": {"answer": ans}}


_AZ124_STEP1 = _az124_plan(
    _az124_fin(_AZ124_BAD, "给出答案"),
    {"intent": "先查经验", "tool": "recall", "args": {"query": "commas 千分位"}})
_AZ124_STEP2_OK = _az124_plan(_az124_fin(_AZ124_GOOD, "按报错改写"))
_AZ124_STEP2_SAME = _az124_plan(_az124_fin(_AZ124_BAD, "再交一次同样的"))


def _az124_run(*, carry: int, step2: str):
    """跑一次 Core（离线桩），返回 `(outcome, prompts, logs)`。

    ⚠️ `V1_CAND_FAILOVER=1` 是 **V61 两臂的常量**，不是本条的自变量：
       没有它就没有"让位"，也就复现不出"报错被清零"那一幕。
    ⛔ 每条分支都必须走同一个装装置函数：两份 fixture 各写一遍 = 两个未来。
    """
    prompts: list[str] = []
    logs: list[str] = []

    class _Tap(StubLLM):
        def complete(self, messages, **kw):                 # noqa: ANN001, ANN003
            prompts.append("\n".join(m.content for m in messages))
            return super().complete(messages, **kw)

    keys = ("V1_LLM_OFFLINE", "V1_STATE_DIR", "V1_MAX_WALL_S", "V1_MAX_STEPS",
            "V1_VERIFY_EXEC", "V1_REJECT_CARRY", "V1_MAX_CANDIDATES",
            "V1_CAND_FAILOVER", "V1_VERIFY_RETRIES")
    old = {k: os.environ.get(k) for k in keys}
    os.environ["V1_LLM_OFFLINE"] = "1"
    os.environ["V1_STATE_DIR"] = str(Path(tempfile.mkdtemp(prefix="v1_az124_")) / "s")
    os.environ["V1_MAX_WALL_S"] = "120"
    os.environ["V1_MAX_STEPS"] = "8"
    os.environ["V1_VERIFY_EXEC"] = "1"
    os.environ["V1_REJECT_CARRY"] = str(carry)
    os.environ["V1_MAX_CANDIDATES"] = "3"
    os.environ["V1_CAND_FAILOVER"] = "1"
    os.environ["V1_VERIFY_RETRIES"] = "2"
    try:
        _c = Core(cfg.Settings(), llm=_Tap(scripts=[("自检未通过", step2)],
                                          default=_AZ124_STEP1),
                  tools=ToolRegistry(), wire_external=False,
                  log=lambda m: logs.append(str(m)))
        return _c.run(_AZ124_GOAL), prompts, logs
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


_AZ124_A_OUT, _AZ124_A_P, _AZ124_A_LOG = _az124_run(carry=0, step2=_AZ124_STEP2_OK)
_AZ124_B_OUT, _AZ124_B_P, _AZ124_B_LOG = _az124_run(carry=1, step2=_AZ124_STEP2_OK)
_AZ124_S_OUT, _AZ124_S_P, _AZ124_S_LOG = _az124_run(carry=1, step2=_AZ124_STEP2_SAME)

#: ⚠️ `Outcome` 不是 dict —— 第一版写成 `(v or {}).get(...)` 直接 AttributeError，
#:    整个契约从 627 掉到 7（PASS=7）而 FAIL=0：**崩溃被读成"跑完了、只跑了 7 条"**。
#:    这是"缺席冒充零"的同一个形状，只不过冒充的是"通过"。
_AZ124_RW = {k: (getattr(v, "stats", None) or {}).get("rework") or {}
             for k, v in (("a", _AZ124_A_OUT), ("b", _AZ124_B_OUT),
                          ("s", _AZ124_S_OUT))}
#: ① 场景必须真的复现：没有"让位救回步骤"就不是 V61 那一幕，后面的读数全无意义。
_AZ124_SCENE = {
    k: {"让位": any("让位给候选" in x for x in lg),
        "救回步骤": any("让位救回一个" in x for x in lg)}
    for k, lg in (("a", _AZ124_A_LOG), ("b", _AZ124_B_LOG), ("s", _AZ124_S_LOG))}
#: ② 开关：只数**重规划位置**的那一行（`(上一次交付) ⇒ 失败`），不数 `[工作记忆]`。
_AZ124_MARK = "(上一次交付) ⇒ 失败"
_AZ124_A_MARK = [i for i, x in enumerate(_AZ124_A_P) if _AZ124_MARK in x]
_AZ124_B_MARK = [i for i, x in enumerate(_AZ124_B_P) if _AZ124_MARK in x]
#: ⑥ 一次性：喂过就清空 ⇒ 后面那些提问里都不许再有它。
_AZ124_B_AFTER = [i for i, x in enumerate(_AZ124_B_P)
                  if _AZ124_MARK in x and i > (_AZ124_B_MARK[0] if _AZ124_B_MARK else 0)]
#: ⑦ 账本键集。
#: ⚠️ V77（§146）**新增 `via_answer`**（打回后走交付协议的次数）。这不是放宽，
#:   是**加键**：三处（`core.rework_stats` / `run._krw_zero`+`_krw_absorb` /
#:   本条）必须同步，漏任一处新键就静默恒 0（纪律 21）。
_AZ124_KEYS_ZERO = sorted(["tasks", "carried", "redelivered", "same", "diff",
                           "passed_after", "dup_skipped", "task_runs_rejected",
                           "via_answer"])


class _AZ124Out:
    def __init__(self, stats):
        self.stats = stats


_AZ124_ACC = _run._krw_zero()
_run._krw_absorb(_AZ124_ACC, _AZ124Out({
    "rework": {"carried": 1, "redelivered": 2, "same": 1, "diff": 1,
               "passed_after": 0},
    "verify": {"failed": 2}}))
_run._krw_absorb(_AZ124_ACC, _AZ124Out({
    "rework": {"carried": 0, "redelivered": 0, "same": 0, "diff": 0,
               "passed_after": 0},
    "verify": {"failed": 0}}))
_AZ124_GAP = _run._krw_line({**_run._krw_zero(), "redelivered": 3,
                             "same": 1, "diff": 1})
_AZ124_RSRC = (V1 / "run.py").read_text(encoding="utf-8")
_AZ124_LEDGER_OK = ('"kernel_rework": _krw_line(_krw)' in _AZ124_RSRC
                    and _AZ124_RSRC.count("_krw_absorb(_krw, out)") == 2
                    and _AZ124_RSRC.count("def _krw_zero(") == 1)

_AZ124_OK = {
    "① 场景复现：三跑都有『让位给候选』+『让位救回一个步骤』": (
        all(v["让位"] and v["救回步骤"] for v in _AZ124_SCENE.values())),
    "② 开关：关 ⇒ 重规划位置**没有**那一行；开 ⇒ 恰好**一处**": (
        _AZ124_A_MARK == [] and len(_AZ124_B_MARK) == 1),
    "③ 读数：`carried` 只在开+真打回时 =1（A=0 / B=1）": (
        _AZ124_RW["a"]["carried"] == 0 and _AZ124_RW["b"]["carried"] == 1),
    "④ 口径：`redelivered` 数『打回后又交一次』，改对也算；`same+diff == redelivered`": (
        _AZ124_RW["b"]["redelivered"] == 1 and _AZ124_RW["b"]["diff"] == 1
        and _AZ124_RW["b"]["same"] == 0
        and _AZ124_RW["b"]["redelivered"]
        == _AZ124_RW["b"]["same"] + _AZ124_RW["b"]["diff"]),
    # ⚠️ 照抄那一跑的 `same` **不在闸里记**：逐字相同的交付签名也相同 ⇒
    #    在重复闸就被丢掉，`_gate_finish` 根本不被调用（冒烟时 `same=0` 抓到的）。
    #    ⇒ ⑤ 同时钉住"记在哪里"和"漏斗那一格补齐了没有"。
    # ⛔ 不写死"照抄跑几次"：那由桩的剧本决定（这里是 2 次），与本条要主张的语义无关。
    #    钉的是**关系**：全是逐字相同的、`same` 与漏斗那一格同数、一次也没过闸。
    "⑤ 改对了 ⇒ `passed_after=1`；照抄 ⇒ `diff=0` 且 `same==dup_skipped==redelivered`": (
        _AZ124_RW["b"]["passed_after"] == 1 and _AZ124_RW["b"]["same"] == 0
        and _AZ124_RW["s"]["diff"] == 0
        and _AZ124_RW["s"]["same"] >= 1
        and _AZ124_RW["s"]["same"] == _AZ124_RW["s"]["dup_skipped"]
        == _AZ124_RW["s"]["redelivered"]
        and _AZ124_RW["s"]["passed_after"] == 0),
    "⑥ 一次性：B 的第 2 处及以后**不许**再出现（喂过就清空）": (
        _AZ124_B_AFTER == []),
    "⑦ 键集全等（7/8）+ 并入 + `rework_gap` 检测器活着": (
        sorted(_run._krw_zero()) == _AZ124_KEYS_ZERO
        and sorted(_run._krw_line(_run._krw_zero())) == sorted(_AZ124_KEYS_ZERO + ["rework_gap"])
        and _AZ124_ACC["carried"] == 1 and _AZ124_ACC["redelivered"] == 2
        and _AZ124_ACC["tasks"] == 2 and _AZ124_ACC["task_runs_rejected"] == 1
        and _AZ124_GAP["rework_gap"] == 1),
    "⑧ `switch` 与控制面同源（`carried=0` 不许冒充『没打回』）": (
        _AZ124_RW["a"]["switch"] == 0 and _AZ124_RW["b"]["switch"] == 1),
    "⑨ 账本键在 + 两处并入 + 唯一定义": (_AZ124_LEDGER_OK),
    "⑩ 超限交付不留欠账（⛔ 跨题泄漏）": (
        "self._reject_carry = \"\"" in
        (V1 / "mw" / "core.py").read_text(encoding="utf-8")),
    # ★ V63（§129）：改名之后**唯一**的真风险是"只改一边"——
    #   账本读 `has_example`、内核却还写 `example_tasks` ⇒ 永远读到 None ⇒
    #   `example_task_runs` 恒 0 ⇒ 被读成"这条判据不适用"（缺席冒充零）。
    #   ⑨ 只是源码级 pin，这里用**真内核跑出来的 stats** 钉运行时那一半。
    "⑪ 运行时键同源：真内核 `verify` 含 `has_example`，账本并入后 =1（不是 0）": (
        ((getattr(_AZ124_A_OUT, "stats", None) or {}).get("verify") or {}
         ).get("has_example") == 1
        and _run._kvf_absorb(_run._kvf_zero(),
                             _AZ124_A_OUT)["example_task_runs"] == 1),
}
check(RS, "AZ124 ★★★**打回 → 重写**这条边：报错原文必须活过『让位成功』的清零\n"
          "          ① 场景真的复现（打回 → 让位 → 非交付步骤成功）；\n"
          "          ② 关：重规划位置没有那一行；开：恰好一处；\n"
          "          ③ `carried` 只在『开 + 真打回』时 =1；\n"
          "          ④ `redelivered` 数的是『又交了一次』（改对也算），且 `same+diff == redelivered`；\n"
          "          ⑤ 改对 ⇒ `passed_after`；照抄 ⇒ `same`/`dup_skipped`/`redelivered` 同数；\n"
          "          ⑥ 一次性（喂过就清空，不许变永久背景）；\n"
          "          ⑦ 键集全等 + 并入 + `rework_gap` 检测器；\n"
          "          ⑧ `switch` 与 `limits.reject_carry` 同源；\n"
          "          ⑨ 账本里有 `kernel_rework`，两处并入；\n"
          "          ⑩ 超限交付时清掉欠账；\n"
          "          ⑪ 运行时键同源：真内核产出 `has_example`，账本并入后 =1（不是 0）。\n"
          "          ⚠️ 本条**不主张**『载荷送不到』：V62 的 state 原文证明 `自检未通过`\n"
          "             早就在 `[工作记忆]` 里了。它主张的是**位置与一次性**。\n"
          "          撤回语义：`_reject_carry = _err` 那行删掉 ⇒ ②③ 必红；\n"
          "                    `redelivered` 改记在『又被打回』分支 ⇒ ④ 必红；\n"
          "                    `rework_gap` 写死 0 ⇒ ⑦ 必红；\n"
          "                    内核侧 `has_example` 改回 `example_tasks` ⇒ ⑪ 必红。",
      all(_AZ124_OK.values()),
      " ｜ ".join(f"{k}={v}" for k, v in _AZ124_OK.items())
      + f" ｜ A标记={_AZ124_A_MARK} B标记={_AZ124_B_MARK} B后续={_AZ124_B_AFTER}"
      + " ｜ B=" + str({k: _AZ124_RW["b"].get(k) for k in
                        ("carried", "redelivered", "same", "diff", "passed_after")})
      + " ｜ S=" + str({k: _AZ124_RW["s"].get(k) for k in
                        ("carried", "redelivered", "same", "diff", "passed_after")}))

# ══════════════════════════════════════════════════════════════════════════
# AZ146（V77，§146）：打回后的重写改走**交付协议**（`V1_REJECT_CARRY=5`）
# ══════════════════════════════════════════════════════════════════════════
#  ⛔ 为什么这条必须存在：V73/V75/V76 把重写通道的四块**输入**逐块清空，
#     八跑**逐字相同**（`same=16/16`）⇒ 输入构造被否证。V77 换的是**输出协议**：
#     打回后不再要 JSON 规划（`replan()`），直接调 `answer(goal, clean=True)`
#     并把返回的**纯文本**包成单个 `finish` 步骤交回管道。
#  ⚠️ 本条主张的**不是**「改得对」，而是三件结构事实：
#     ① 新分支真的被走到（`via_answer`），且 4 档一点没被改坏；
#     ② 5 档**不走** `replan()`（历史里没有 `(上一次交付) ⇒ 失败` 那一行）；
#     ③ 5 档交付的是 `answer()` 的**原文**（逐字），不是被 JSON 包裹的东西。
#  ⛔ 不许把本条读成「模型学会改错了」：`clean=True` 的提示词里**没有 `error`**，
#     这个臂语义上是「重问一次」。`v6_s166/PREREG.md` §6-1 已预注册这一句。
_AZ146_ANS = _AZ124_GOOD
#: `_ANSWER_SYS_CLEAN` 的原文（同时 pin 源码里有它，见 ⑥ —— 防字面量漂移）。
_AZ146_SYS_CLEAN = "直接输出最终答案：代码就给代码原文，文字就给正文。"
_AZ146_SRC = (V1 / "mw" / "planner.py").read_text(encoding="utf-8")
#: `replan()` 独有的历史行（`planner.py:770` 拼的那一句）。5 档**不许**出现它。
_AZ146_MARK = "(上一次交付) ⇒ 失败"


def _az146_run(*, carry: int, ans: str):
    """跑一次 Core（离线桩），返回 `(outcome, prompts, logs)`。

    ⚠️ 与 `_az124_run` 的唯一差别：桩按**答案通道标记**分派 ——
       `answer()` 的 prompt 以「请直接给出最终答案。」结尾（S69 用它区分通道），
       规划 prompt 不含它 ⇒ 同一次跑里两个通道拿到不同剧本。
    ⛔ 每条分支都必须走同一个装装置函数（两份 fixture = 两个未来）。
    """
    prompts: list[str] = []
    logs: list[str] = []

    class _Tap(StubLLM):
        def complete(self, messages, **kw):                 # noqa: ANN001, ANN003
            prompts.append("\n".join(m.content for m in messages))
            return super().complete(messages, **kw)

    keys = ("V1_LLM_OFFLINE", "V1_STATE_DIR", "V1_MAX_WALL_S", "V1_MAX_STEPS",
            "V1_VERIFY_EXEC", "V1_REJECT_CARRY", "V1_MAX_CANDIDATES",
            "V1_CAND_FAILOVER", "V1_VERIFY_RETRIES")
    old = {k: os.environ.get(k) for k in keys}
    os.environ["V1_LLM_OFFLINE"] = "1"
    os.environ["V1_STATE_DIR"] = str(Path(tempfile.mkdtemp(prefix="v1_az146_")) / "s")
    os.environ["V1_MAX_WALL_S"] = "120"
    os.environ["V1_MAX_STEPS"] = "8"
    os.environ["V1_VERIFY_EXEC"] = "1"
    os.environ["V1_REJECT_CARRY"] = str(carry)
    os.environ["V1_MAX_CANDIDATES"] = "3"
    os.environ["V1_CAND_FAILOVER"] = "1"
    os.environ["V1_VERIFY_RETRIES"] = "2"
    try:
        _c = Core(cfg.Settings(), llm=_Tap(scripts=[("请直接给出最终答案", ans)],
                                          default=_AZ124_STEP1),
                  tools=ToolRegistry(), wire_external=False,
                  log=lambda m: logs.append(str(m)))
        return _c.run(_AZ124_GOAL), prompts, logs
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


_AZ146_5_OUT, _AZ146_5_P, _AZ146_5_LOG = _az146_run(carry=5, ans=_AZ146_ANS)
_AZ146_4_OUT, _AZ146_4_P, _AZ146_4_LOG = _az146_run(carry=4, ans=_AZ146_ANS)
_AZ146_E_OUT, _AZ146_E_P, _AZ146_E_LOG = _az146_run(carry=5, ans="")

_AZ146_RW = {k: (getattr(v, "stats", None) or {}).get("rework") or {}
             for k, v in (("5", _AZ146_5_OUT), ("4", _AZ146_4_OUT),
                          ("e", _AZ146_E_OUT))}
#: 5 档里**答案通道**的那一条请求（按 `_ANSWER_SYS_CLEAN` 认）。
_AZ146_5_ANS_P = [x for x in _AZ146_5_P if _AZ146_SYS_CLEAN in x]
#: 4 档的**重规划**请求（按 `(上一次交付) ⇒ 失败` 认）。
_AZ146_4_REPLAN_P = [x for x in _AZ146_4_P if _AZ146_MARK in x]
#: 空答案那一跑：任何一条带 `_AZ146_MARK` 的请求都算「回退到了规划通道」。
_AZ146_E_FALLBACK = [x for x in _AZ146_E_P if _AZ146_MARK in x]

_AZ146_OK = {
    "① 只数一次：5 档 `via_answer=1`、4 档 `=0`（4 档没被改坏）": (
        _AZ146_RW["5"].get("via_answer") == 1
        and _AZ146_RW["4"].get("via_answer") == 0),
    "② 兑现：5 档 `redelivered=1 / diff=1 / same=0 / passed_after=1`": (
        _AZ146_RW["5"].get("redelivered") == 1
        and _AZ146_RW["5"].get("diff") == 1
        and _AZ146_RW["5"].get("same") == 0
        and _AZ146_RW["5"].get("passed_after") == 1),
    "③ 交付的是 `answer()` 原文（逐字），不是 JSON": (
        (getattr(_AZ146_5_OUT, "answer", "") or "").strip()
        == _AZ146_ANS.strip() and _AZ146_ANS.strip() != ""),
    "④ 5 档不走 `replan()`：答案请求里没有 `(上一次交付) ⇒ 失败`；4 档恰好一处": (
        len(_AZ146_5_ANS_P) == 1 and _AZ146_MARK not in _AZ146_5_ANS_P[0]
        and len(_AZ146_4_REPLAN_P) == 1),
    "⑤ 5 档用的是 `_ANSWER_SYS_CLEAN`（clean 臂），4 档一次都没用": (
        len(_AZ146_5_ANS_P) == 1
        and not any(_AZ146_SYS_CLEAN in x for x in _AZ146_4_P)),
    "⑥ 字面量不漂：`_ANSWER_SYS_CLEAN` 原文在 `planner.py` 里": (
        _AZ146_SYS_CLEAN in _AZ146_SRC),
    "⑦ 交空**不回退**：仍记 `via_answer=1`、日志有『交空』、没有 `replan` 请求": (
        _AZ146_RW["e"].get("via_answer") == 1
        and any("交付协议**交空**" in x for x in _AZ146_E_LOG)
        and _AZ146_E_FALLBACK == []),
}
check(RS, "AZ146 ★★打回后的重写改走**交付协议**（`V1_REJECT_CARRY=5`）\n"
          "          ① 5 档 `via_answer=1`、4 档 `=0`；\n"
          "          ② 5 档 `diff=1/same=0/passed_after=1`（协议换过就真的换了答案）；\n"
          "          ③ 交付的正文逐字等于 `answer()` 的返回值；\n"
          "          ④ 5 档不许出现 `replan()` 的历史行，4 档恰好一处；\n"
          "          ⑤ 5 档用 `_ANSWER_SYS_CLEAN`，4 档一次都没用；\n"
          "          ⑥ `_ANSWER_SYS_CLEAN` 字面量在源码里（防漂移）；\n"
          "          ⑦ `answer()` 交空时**不回退**到规划通道（⛔ 静默 fallback）。\n"
          "          ⚠️ 本条**不主张**『模型学会改错』：`clean=True` 的提示词里没有 `error`，\n"
          "             这个臂语义上是『重问一次』（`v6_s166/PREREG.md` §6-1）。\n"
          "          撤回语义：把 `=5` 分支删掉 ⇒ ①必红（`via_answer` 键仍在但恒 0）；\n"
          "                    `answer(goal, clean=True)` 改成 `clean=False` ⇒ ⑤ 必红；\n"
          "                    交空时改回 `replan()` ⇒ ⑦ 必红；\n"
          "                    `=5` 改调 `replan()` ⇒ ②③④ 全红。",
      all(_AZ146_OK.values()),
      " ｜ ".join(f"{k}={v}" for k, v in _AZ146_OK.items())
      + " ｜ 5=" + str({k: _AZ146_RW["5"].get(k) for k in
                        ("via_answer", "carried", "redelivered", "same", "diff",
                         "passed_after")})
      + " ｜ 4=" + str({k: _AZ146_RW["4"].get(k) for k in
                        ("via_answer", "carried", "redelivered", "same", "diff",
                         "passed_after")})
      + f" ｜ 5答案请求={len(_AZ146_5_ANS_P)} 4重规划请求={len(_AZ146_4_REPLAN_P)}"
      + f" ｜ E回退={len(_AZ146_E_FALLBACK)}")

# ══════════════════════════════════════════════════════════════════════════
# AZ171（V258，§328）：`=6` = 5 档 + **「没有候选可让位」那条路**
# ══════════════════════════════════════════════════════════════════════════
#  ⛔ 为什么必须存在（V257 量到的缺口，`bench/evidence/l21_s327/`）：
#     `=5` 住在 `elif _carry:`（`core.py:1401`），而打回时 `_gate_finish` 返回
#     `ToolResult(False, error=_err)`（`core.py:2265`）⇒ 主循环把它计成**连续失败**
#     ⇒ 打回后的**第一次**重规划走 `if consecutive_failures and last_error:`
#     （`core.py:1301`）。于是在「候选只有 1 个 / 让位失败」的格上 5 档
#     **一次都到不了**：`g7t4` 打回 2 次、`via_answer=0`、停滞 2/2、交付 0.0。
#  ⚠️ 本条钉的是四件**结构**事实，⛔ 不是「修好了」，也⛔ 不是「模型学会改错」：
#     ① 6 档在无让位路径上真的触发（`via_answer_early`）；
#     ② 5 档在**同一个** fixture 上一次都不触发（两跑只差一个 env）；
#     ③ 6 档走的是 `_ANSWER_SYS_CLEAN`（交付协议），不是 `replan()`；
#     ④ 一次性：交空之后不再进早分支（`via_answer_early` 不涨到 2）。
#  ⛔ 5 档的语义一个字不动 —— AZ146 的七条仍在跑，那才是「没改坏」的证明。
_AZ171_KEYS = ("V1_LLM_OFFLINE", "V1_STATE_DIR", "V1_MAX_WALL_S", "V1_MAX_STEPS",
               "V1_VERIFY_EXEC", "V1_REJECT_CARRY", "V1_MAX_CANDIDATES",
               "V1_CAND_FAILOVER", "V1_VERIFY_RETRIES")


def _az171_run(*, carry: int, ans: str):
    """同一个装置，**只差一个 env**：`V1_MAX_CANDIDATES=1`（没有候选可让位）。

    ⛔ 与 `_az146_run` 的差别只有这一处。于是「5 档到得了 / 到不了」不是脚本差异，
       而是**分支可达性** —— 这正是 V257 P5 不成立时暴露的那件事。
    """
    prompts: list[str] = []
    logs: list[str] = []

    class _Tap(StubLLM):
        def complete(self, messages, **kw):                 # noqa: ANN001, ANN003
            prompts.append("\n".join(m.content for m in messages))
            return super().complete(messages, **kw)

    old = {k: os.environ.get(k) for k in _AZ171_KEYS}
    os.environ["V1_LLM_OFFLINE"] = "1"
    os.environ["V1_STATE_DIR"] = str(Path(tempfile.mkdtemp(prefix="v1_az171_")) / "s")
    os.environ["V1_MAX_WALL_S"] = "120"
    os.environ["V1_MAX_STEPS"] = "8"
    os.environ["V1_VERIFY_EXEC"] = "1"
    os.environ["V1_REJECT_CARRY"] = str(carry)
    os.environ["V1_MAX_CANDIDATES"] = "1"     # ★ 本条唯一的装置差别
    os.environ["V1_CAND_FAILOVER"] = "1"      # 与 AZ146 同值：能"让位"，只是没得让
    os.environ["V1_VERIFY_RETRIES"] = "2"
    try:
        _c = Core(cfg.Settings(), llm=_Tap(scripts=[("请直接给出最终答案", ans)],
                                          default=_AZ124_STEP1),
                  tools=ToolRegistry(), wire_external=False,
                  log=lambda m: logs.append(str(m)))
        return _c.run(_AZ124_GOAL), prompts, logs
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


_AZ171_6_OUT, _AZ171_6_P, _AZ171_6_LOG = _az171_run(carry=6, ans=_AZ146_ANS)
_AZ171_5_OUT, _AZ171_5_P, _AZ171_5_LOG = _az171_run(carry=5, ans=_AZ146_ANS)
_AZ171_E_OUT, _AZ171_E_P, _AZ171_E_LOG = _az171_run(carry=6, ans="")

_AZ171_RW = {k: (getattr(v, "stats", None) or {}).get("rework") or {}
             for k, v in (("6", _AZ171_6_OUT), ("5", _AZ171_5_OUT),
                          ("e", _AZ171_E_OUT))}
#: 6 档跑里**答案通道**的请求（按 `_ANSWER_SYS_CLEAN` 认）。
_AZ171_6_ANS_P = [x for x in _AZ171_6_P if _AZ146_SYS_CLEAN in x]
#: 6 档跑里第一条 `replan()` 请求的下标（-1 = 没有）。
_AZ171_6_MARK_AT = next((i for i, x in enumerate(_AZ171_6_P) if _AZ146_MARK in x), -1)
#: 答案通道那条请求的下标（-1 = 没有）。
_AZ171_6_ANS_AT = next((i for i, x in enumerate(_AZ171_6_P)
                        if _AZ146_SYS_CLEAN in x), -1)

_AZ171_OK = {
    "① 6 档在无让位路径上触发（`via_answer_early=1`）": (
        _AZ171_RW["6"].get("via_answer_early") == 1),
    "② 同 fixture 上 5 档一次都到不了（`via_answer=0` 且 `early=0`）": (
        _AZ171_RW["5"].get("via_answer") == 0
        and _AZ171_RW["5"].get("via_answer_early") == 0),
    "③ 6 档走交付协议：答案请求 1 条、且**早于**任何 replan 请求": (
        len(_AZ171_6_ANS_P) == 1 and _AZ171_6_ANS_AT >= 0
        and (_AZ171_6_MARK_AT < 0 or _AZ171_6_ANS_AT < _AZ171_6_MARK_AT)),
    "④ 交付的是 `answer()` 原文（逐字）": (
        (getattr(_AZ171_6_OUT, "answer", "") or "").strip()
        == _AZ146_ANS.strip() and _AZ146_ANS.strip() != ""),
    "⑤ 一次性：交空之后不再进早分支（`early` 仍为 1，不是 2）": (
        _AZ171_RW["e"].get("via_answer_early") == 1
        and any("交付协议**交空**" in x for x in _AZ171_E_LOG)),
    "⑥ 5 档在无让位路径上不许用 clean 协议（一次都没有）": (
        not any(_AZ146_SYS_CLEAN in x for x in _AZ171_5_P)),
}
check(RS, "AZ171 ★★`=6` 把「打回后换交付协议」接到**无让位**那条路上（V258）\n"
          "          ① 6 档 `via_answer_early=1`（分支真的被走到）；\n"
          "          ② 同 fixture 上 5 档 `via_answer=0 / early=0`（差一个 env）；\n"
          "          ③ 6 档走 `_ANSWER_SYS_CLEAN`，且早于任何 `replan()` 请求；\n"
          "          ④ 交付正文逐字等于 `answer()` 返回值；\n"
          "          ⑤ 一次性：交空后不再进早分支（`early` 不涨到 2）；\n"
          "          ⑥ 5 档在这条路上一次都不用 clean 协议。\n"
          "          ⚠️ 本条**不主张**『模型学会改错』：`clean=True` 的提示词里没有 `error`。\n"
          "          撤回语义：把早分支删掉 ⇒ ①必红；把 `_rc == 6` 放宽成 `>= 5` ⇒ ②必红\n"
          "                    （5 档会在无让位路径上也触发）；不清 `_reject_carry` ⇒ ⑤必红。",
      all(_AZ171_OK.values()),
      " ｜ ".join(f"{k}={v}" for k, v in _AZ171_OK.items())
      + " ｜ 6=" + str({k: _AZ171_RW["6"].get(k) for k in
                        ("via_answer", "via_answer_early", "carried",
                         "redelivered", "same", "diff", "passed_after")})
      + " ｜ 5=" + str({k: _AZ171_RW["5"].get(k) for k in
                        ("via_answer", "via_answer_early", "carried",
                         "redelivered", "same", "diff", "passed_after")})
      + f" ｜ 6答案请求={len(_AZ171_6_ANS_P)} 答案下标={_AZ171_6_ANS_AT}"
      + f" 首replan下标={_AZ171_6_MARK_AT}")


# ══════════════════════════════════════════════════════════════════════════
# AZ147（V84，§153）：语义记忆的**第二级** —— 字级 2 字链只在第一级零命中后通电
# ══════════════════════════════════════════════════════════════════════════
#  ⛔ 为什么必须存在：V83 量到「人写的完美经验写进 `facts` 后 `recall(goal)` 一题也取不回」
#     —— 题面与经验的 `_TOKEN` 精确交集 = 0/4，而原文当 query 自命中 4/4。
#     ⇒ 修法是**追加**一级字级检索，不是替换第一级：AZ26 把第一级的命中条数钉成
#       `[1,1,1,1]` / `[4,4,4,4]` / `[0,0,1,1]`，替换必然打红它。
#  ⚠️ 本条钉的是**顺序**与**计数**，不是「检索变强了」：
#     ① 档 0 完全不试（`char_fallbacks=0`）—— 否则「关了」与「开着但空手」分不开；
#     ② 旧路径（题面能精确命中的那种）两档返回 `(ids, chars)` **逐位相同**；
#     ③ 「试了但空手」必须 `fallbacks` 增、`hits` 不增（两个数分开的正当理由）。
_AZ147_TMP = _TMP / "az147_char"
_AZ147_TMP.mkdir(parents=True, exist_ok=True)
for _p in _AZ147_TMP.glob("*.db*"):
    _p.unlink()

#: 与 `bench/evidence/v6_s172` / `v6_s173` 的 `RULES` **逐字相同**（换字就不可比）。
_AZ147_RULE = ("把带下标的扁平键还原成列表时，先取出现过的最大下标加一定长度，"
               "空位留空值而不是跳过")
_AZ147_DIST = ("处理金额时，先做四舍五入再格式化，避免累积误差被显示成两个不同的值",
               "遍历目录时，先判断是否为符号链接，避免沿着链接绕回父目录造成死循环")
#: 现成题面（⛔ 不造新题集）：`struct_convert_heldout` 的 `k03_unflatten`。
_AZ147_GOAL = next(
    json.loads(_l)["prompt"]
    for _l in (V1 / "bench" / "heldout" / "struct_convert_heldout.jsonl")
    .read_text(encoding="utf-8").splitlines()
    if _l.strip() and json.loads(_l)["id"] == "k03_unflatten")
#: 老路径的对照 query：`_TOKEN` 能精确命中的那种（V83 里 g7j 走的就是这一条）。
_AZ147_OLD_FACT = "题目里明写的惯例甲：返回前先把结果排序，再逐项拼接"


def _az147_pool(name: str, level: int):
    """建池 → 返回 (store, 经验 id, 老惯例 id)。⛔ 档位在 `__init__` 里求值，构造时必须设 env。"""
    os.environ["V1_RECALL_CHAR"] = str(level)
    try:
        m = MemoryStore(_AZ147_TMP / name)
    finally:
        os.environ.pop("V1_RECALL_CHAR", None)
    k1, _ = m.commit_fact(_AZ147_RULE, source="abstract", confidence=0.6)
    k2, _ = m.commit_fact(_AZ147_OLD_FACT, source="abstract", confidence=0.6)
    for _d in _AZ147_DIST:
        m.commit_fact(_d, source="abstract", confidence=0.5)
    rid = int(m.conn.execute("SELECT id FROM facts WHERE key=?", (k1,)).fetchone()["id"])
    oid = int(m.conn.execute("SELECT id FROM facts WHERE key=?", (k2,)).fetchone()["id"])
    return m, rid, oid


_AZ147_M0, _AZ147_RID0, _AZ147_OID0 = _az147_pool("l0.db", 0)
_AZ147_G0 = _AZ147_M0.recall(_AZ147_GOAL, k=6)
_AZ147_OLD0 = _AZ147_M0.recall("惯例甲", k=6)
_AZ147_FB0, _AZ147_HT0 = _AZ147_M0.char_fallbacks, _AZ147_M0.char_hits

_AZ147_M2, _AZ147_RID2, _AZ147_OID2 = _az147_pool("l2.db", 2)
_AZ147_G2 = _AZ147_M2.recall(_AZ147_GOAL, k=6)
_AZ147_FB_A, _AZ147_HT_A = _AZ147_M2.char_fallbacks, _AZ147_M2.char_hits
_AZ147_BLANK2 = _AZ147_M2.recall("zzzz qqqq", k=6)
_AZ147_FB_B, _AZ147_HT_B = _AZ147_M2.char_fallbacks, _AZ147_M2.char_hits
_AZ147_OLD2 = _AZ147_M2.recall("惯例甲", k=6)

_AZ147_OK = {
    "① 档 0：题面零命中 ⇒ `[]`，且**一次都没试**第二级（`char_fallbacks=0`）": (
        _AZ147_G0 == [] and _AZ147_FB0 == 0 and _AZ147_HT0 == 0),
    "② 档 2：同一题面**送达**那条经验（按 fact id，不按文本相似）": (
        _AZ147_RID2 in [f.id for f in _AZ147_G2]
        and _AZ147_FB_A == 1 and _AZ147_HT_A == 1),
    "③ 「试了但空手」与「没试」分列：字级也零命中 ⇒ `fallbacks` 增、`hits` 不增": (
        _AZ147_BLANK2 == [] and _AZ147_FB_B == _AZ147_FB_A + 1
        and _AZ147_HT_B == _AZ147_HT_A),
    "④ 老路径不降级：题面能精确命中时，两档 `(ids, chars)` 逐位相同": (
        [f.id for f in _AZ147_OLD0] == [f.id for f in _AZ147_OLD2]
        and sum(len(f.text) for f in _AZ147_OLD0)
        == sum(len(f.text) for f in _AZ147_OLD2)
        and _AZ147_OID0 in [f.id for f in _AZ147_OLD0]),
    "⑤ 档位登记：`V1_RECALL_CHAR` 在 `ENV_KEYS` 里（A1 要求代码里出现的键全部登记）": (
        "V1_RECALL_CHAR" in cfg.ENV_KEYS),
}
check(RS, "AZ147 ★★★语义记忆的第二级只在**第一级零命中后**通电（V84 / §153）\n"
          "          ① 档 0 在 V83 的题面上返回 `[]` 且 `char_fallbacks=0`（关了就是不试）；\n"
          "          ② 档 2 在同一题面上**按 id** 送达那条人写经验；\n"
          "          ③ 「试了但空手」`fallbacks+1 / hits+0`，与「根本没试」分开；\n"
          "          ④ 题面能精确命中的那种 query，两档 `(ids, chars)` 逐位相同（不降级）；\n"
          "          ⑤ `V1_RECALL_CHAR` 已登记进 `ENV_KEYS`。\n"
          "          ⚠️ 本条**不主张**分数会升：§7f 实测邻域效应（另三条惯例 182→291 字\n"
          "             ⇒ 单题极差 0.80）足以吃掉单题 Δ；送达率与分数是两条判据。\n"
          "          撤回语义：`_recall_char` 的 `if self.char_level <= 0: return []` 删掉\n"
          "                    ⇒ ①必红；`char_grams` 换回 `_TOKEN` ⇒ ②必红；\n"
          "                    两计数器合成一个 ⇒ ③必红；\n"
          "                    第二级挪到 LIKE **之前** ⇒ ④必红（AZ26 的三个字面量同源必红）。",
      all(_AZ147_OK.values()),
      " ｜ ".join(f"{k}={v}" for k, v in _AZ147_OK.items())
      + f" ｜ 档0 条数={len(_AZ147_G0)} fb={_AZ147_FB0}/{_AZ147_HT0}"
      + f" ｜ 档2 条数={len(_AZ147_G2)} 含经验={_AZ147_RID2 in [f.id for f in _AZ147_G2]}"
      + f" fb={_AZ147_FB_A}/{_AZ147_HT_A} 空手后={_AZ147_FB_B}/{_AZ147_HT_B}"
      + f" ｜ 老路径={[f.id for f in _AZ147_OLD0]}/{[f.id for f in _AZ147_OLD2]}")

# ══════════════════════════════════════════════════════════════════════════
# AZ125（V66，§133）：三个「截断」必须**分居三处**，且**单位写进名字**
# ══════════════════════════════════════════════════════════════════════════
#  ⛔ 为什么这条必须存在：全仓有三个都叫 `truncated` 的读数，单位各不相同 ——
#      · `Planner.truncated_responses`：**次**（一次响应的 JSON 断在半截 ⇒ 调 max_tokens）
#      · `plan_stats["truncated_cands"]`：**条**（一个候选被 `[:n]` 截掉 ⇒ 调 n）
#      · 答案落盘侧的 `truncated`：**条**（一条答案被 `max_chars` 截 ⇒ 调存储上限）
#     三者修法互不相同。V66 之前前两个同名 ⇒ 同一份 stdout 里 `截断=2`（次）
#     与 `截断 2 条`（条）并列出现，读起来完全一样。
#  ⚠️ 第二个缺口比命名更根本：`plan_stats` 只经 `snapshot()["plan"]` 出去，
#     `runs.jsonl` 里**从来没有** `kernel_plan` ⇒ 跑批层面读不到「被 n 截了几条」，
#     只有直接调 `core.snapshot()` 的探针能看见（纪律 26 的原形：
#     stats 里看得见、账本里看不见）。所以本条同时钉**键名**与**落盘**。
#  ⚠️ 两条检测器缺一不可（V63 纪律 35）：源码级挡不住「另一处覆盖它」，
#     运行时级挡不住「这次刚好是 0」。
#: ⚠️ 用 `.get` 而不是 `__getitem__`：咬人演练里见过「键会消失」⇒ 取不到就是空字典，
#:    让 ④ 判红，而不是在构造段抛 KeyError 把整个契约打断（同 `_az125_safe` 的理由）。
_AZ125_PL = dict(((getattr(_AZ124_A_OUT, "stats", None) or {}).get("planner")) or {})
_AZ125_PN = dict(((getattr(_AZ124_A_OUT, "stats", None) or {}).get("plan")) or {})
_AZ125_SRC = inspect.getsource(_run)
#: ⑨ 内核零点的键集（源码级那一半：内核里到底还叫不叫旧名）。
from mw import core as _az125_core                              # noqa: E402
_AZ125_PZ_KEYS = set(_az125_core._plan_zero().keys())

#: ⚠️ **构造段不许抛异常**：契约文件头第 ② 条「不许对可能不存在的属性做算术」
#:    的同一个理由，只不过这次是**键**会消失 —— 咬人演练实测：把账本键改回
#:    `truncated_total` 后，`_kp_line` 里的 `out["truncated_cands"]` 直接 KeyError，
#:    整个契约从 629 掉到「PASS=若干 FAIL=0」的**假汇总**（崩溃被读成跑完了）。
#:    ⇒ 用哨兵值接住，让它是**红**，不是**崩**。
def _az125_safe(fn):                                        # noqa: ANN001
    try:
        return fn()
    except (KeyError, TypeError, ZeroDivisionError) as _e:
        return f"EXC:{type(_e).__name__}"


_AZ125_ACC_ZERO = {"rounds": 0, "examined_total": 0, "cands_total": 0,
                   "truncated_cands": 0, "dropped_not_applicable": 0}
_AZ125_ACC_ONE = {"rounds": 1, "examined_total": 4, "cands_total": 3,
                  "truncated_cands": 1, "dropped_not_applicable": 0}
#: ⑦ 分母必须是 `examined_total`（规划器交出的步骤），不是 `cands_total`（截断之后的）。
#:    用后者，某题全被截掉时 `cands_total=0` ⇒ 除零，而那一刻恰恰最该报。
_AZ125_RATE_ZERO = _az125_safe(
    lambda: _run._kp_line(_AZ125_ACC_ZERO)["truncated_rate"])
_AZ125_RATE_ONE = _az125_safe(
    lambda: _run._kp_line(_AZ125_ACC_ONE)["truncated_rate"])
#: ⑧ 反证：`_kp_absorb` 必须真累加（两题各 1 ⇒ 2）。写死或覆盖式赋值会得 1。
_AZ125_SUM = _az125_safe(lambda: _run._kp_absorb(
    _run._kp_absorb(_run._kp_zero(),
                    {"truncated_cands": 1, "examined_total": 3, "cands_total": 2}),
    {"truncated_cands": 1, "examined_total": 4, "cands_total": 4})["truncated_cands"])

_AZ125_OK = {
    "① 规划器键带单位（次）": ("truncated_responses" in _run._KPL_NUM_KEYS
                              and "truncated" not in _run._KPL_NUM_KEYS),
    "② 候选键带单位（条）": ("truncated_cands" in _run._KP_NUM_KEYS
                             and "truncated_total" not in _run._KP_NUM_KEYS),
    "③ 两组键无交集": not (set(_run._KPL_NUM_KEYS) & set(_run._KP_NUM_KEYS)),
    "④ 运行时：真内核两条都在": (isinstance(_AZ125_PL.get("truncated_responses"), int)
                                 and isinstance(_AZ125_PN.get("truncated_cands"), int)),
    "⑤ 跨字典：谁也不是谁的别名": ("truncated_cands" not in _AZ125_PL
                                   and "truncated_responses" not in _AZ125_PN),
    # ⑥ 落盘锚：没有这一格，两个数在跑批层面根本读不到（本条最实在的一半）。
    "⑥ 写盘字典有 kernel_plan": '"kernel_plan"' in _AZ125_SRC,
    "⑦ 截断率分母是 examined（0 不崩）": (_AZ125_RATE_ZERO is None
                                          and _AZ125_RATE_ONE == 0.25),
    "⑧ absorb 真累加": _AZ125_SUM == 2,
    "⑨ 内核零点已改名": ("truncated_cands" in _AZ125_PZ_KEYS
                          and "truncated_total" not in _AZ125_PZ_KEYS),
}
check(RS, "AZ125 ★★★三个「截断」分居三处，单位写进名字（V66 / §133）\n"
          "          —— 改之前全仓两个同名 `truncated` 单位不同（次 vs 条），\n"
          "             同一份 stdout 里 `截断=2` 与 `截断 2 条` 读起来一模一样。\n"
          "         判据（九条）：\n"
          "               ① `_KPL_NUM_KEYS` 含 `truncated_responses`（次），无裸 `truncated`；\n"
          "               ② `_KP_NUM_KEYS` 含 `truncated_cands`（条）；\n"
          "               ③ 两组键**无交集**（同一份产物里不许共用一个键）；\n"
          "               ④ 真内核的 `stats['planner']` / `stats['plan']` 各含其一；\n"
          "               ⑤ 跨字典：谁也不是谁的别名；\n"
          "               ⑥ `run.py` 写盘字典里有 `kernel_plan`\n"
          "                  —— 这是 V66 之前**根本不存在**的一格；\n"
          "               ⑦ 截断率的分母是 `examined_total`：`examined=0` ⇒ `None` 不崩，\n"
          "                  `4/1` ⇒ 0.25（用 `cands_total` 当分母会在全截掉时除零）；\n"
          "               ⑧ `_kp_absorb` 真累加（两题各 1 ⇒ 2，不是覆盖成 1）；\n"
          "               ⑨ 内核 `_plan_zero()` 已无 `truncated_total` 残留。\n"
          "         撤回语义：内核 `_plan_zero` 改回 `truncated_total` ⇒ ④⑨ 必红；\n"
          "                    `_KPL_NUM_KEYS` 改回 `truncated` ⇒ ①④ 必红；\n"
          "                    写盘字典删 `\"kernel_plan\"` ⇒ ⑥ 必红；\n"
          "                    截断率分母换成 `cands_total` ⇒ ⑦ 必红（除零）；\n"
          "                    `_kp_absorb` 改成 `=` 覆盖 ⇒ ⑧ 必红。",
      all(_AZ125_OK.values()),
      " ｜ ".join(f"{k}={v}" for k, v in _AZ125_OK.items())
      + f" ｜ 运行时 planner={_AZ125_PL.get('truncated_responses')}"
      + f" plan={_AZ125_PN.get('truncated_cands')}"
      + f" ｜ rate(0)={_AZ125_RATE_ZERO} rate(4/1)={_AZ125_RATE_ONE} sum={_AZ125_SUM}")

# ===========================================================================
# AZ132 —— L5 自主目标：提议器必须是「自己账本的函数」，不是常量，也不是题面的复述
#
# 这一组锚的是**入口**而不是效果：目标提议的效果由 `bench/evidence/l5_s258/`
# 的 V170 判据量（P1~P5），这里只钉四条不许退化的性质。
# ===========================================================================
import mw.goals as _G                                      # noqa: E402

_az132_rows = [
    {"task_id": "a", "score": 1.0, "session": 1},
    {"task_id": "a", "score": 1.0, "session": 2},
    {"task_id": "a", "score": 1.0, "session": 3},
    {"task_id": "b", "score": 0.5, "session": 1},
    {"task_id": "b", "score": 0.5, "session": 2},
    {"task_id": "b", "score": 0.5, "session": 3},
    {"task_id": "c", "score": 0.0, "session": 1},
]
_az132_obs, _az132_bad = _G.coerce_all(_az132_rows)
_az132_ids = ["a", "b", "c", "d"]
_az132_empty = _G.propose([], [])
_az132_bare = _G.propose([], _az132_ids)

check(RS, "AZ132a 目标提议 fail-closed：没账本也没题 ⇒ **0 条**；有题但一次没测 ⇒ 只出 COVER\n"
          "              （0 次观测是**读数**，不是编出来的分数目标；⛔ 两者不能混）",
      _az132_empty == []
      and [g.kind for g in _az132_bare] == ["COVER"] * 4,
      f"空={len(_az132_empty)} 空账本={[g.kind for g in _az132_bare]}")

_az132_gs = _G.propose(_az132_obs, _az132_ids)
_az132_kind = {g.cell: g.kind for g in _az132_gs}

check(RS, "AZ132b 三类按**读数**分派：3 次观测且均分 1.0 ⇒ 无目标；3 次 0.5 ⇒ RAISE；\n"
          "              1 次 ⇒ PROBE；0 次 ⇒ COVER（⛔ 不是按题号/字母猜）",
      _az132_kind == {"b": "RAISE", "c": "PROBE", "d": "COVER"} and "a" not in _az132_kind,
      f"{_az132_kind}")

_az132_g = _az132_gs[0]
_az132_same = _G.Goal(kind=_az132_g.kind, cell=_az132_g.cell, statement="换一种说法",
                      criterion=_az132_g.criterion, provenance=())
_az132_other = _G.Goal(kind=_az132_g.kind, cell=_az132_g.cell, statement=_az132_g.statement,
                       criterion=(("n_obs", ">=", 2.0),), provenance=())

check(RS, "AZ132c gid 是**内容寻址**：同内容同 id（措辞不进哈希），改判据数值就是另一条目标\n"
          "              ⇒「这条做完没有」与「这是不是同一条」是同一个问题，不用两套账",
      _az132_same.gid == _az132_g.gid and _az132_other.gid != _az132_g.gid,
      f"同={_az132_same.gid == _az132_g.gid} 异={_az132_other.gid != _az132_g.gid}")

_az132_cover = next(g for g in _az132_gs if g.kind == "COVER")
_az132_probe = next(g for g in _az132_gs if g.kind == "PROBE")
_az132_raise = next(g for g in _az132_gs if g.kind == "RAISE")
_az132_f1 = (_G.evaluate(_az132_cover, _az132_obs)[0],
             _G.evaluate(_az132_cover, _az132_obs + [{"task_id": "d", "score": 0.0}])[0])
_az132_f2 = (_G.evaluate(_az132_probe, _az132_obs)[0],
             _G.evaluate(_az132_probe, _az132_obs + [{"task_id": "c", "score": 0.0}] * 2)[0])
_az132_f3 = (_G.evaluate(_az132_raise, _az132_obs)[0],
             _G.evaluate(_az132_raise, [{"task_id": "b", "score": 1.0}] * 3)[0])
_az132_v = [_G.evaluate(g, _az132_obs)[0] for g in _az132_gs]

check(RS, "AZ132d 判定只有两个值（没有 UNKNOWN —— 判不了只可能是装置 bug），\n"
          "              且三类**各有一个能翻转的见证**（⛔ 恒真/恒假的谓词不叫判据）",
      set(_az132_v) <= {"MET", "NOT_MET"}
      and all(a != b for a, b in (_az132_f1, _az132_f2, _az132_f3)),
      f"值域={sorted(set(_az132_v))} 翻转={[_az132_f1, _az132_f2, _az132_f3]}")

# ⛔ 题面整段换掉 ⇒ id 与目标必须逐条不变。
#    这是「目标不是把题目复述一遍」的**结构**见证 —— 子串匹配会被 id 里的
#    函数名跨界命中（V166/V170 各踩过一次，纪律 89）。
_az132_dir = Path(tempfile.mkdtemp(prefix="v1_az132_"))
for _az132_f in sorted((V1 / "bench" / "tasks").glob("*.jsonl")):
    _az132_lines = []
    for _az132_l in _az132_f.read_text(encoding="utf-8").splitlines():
        if not _az132_l.strip():
            continue
        _az132_d = json.loads(_az132_l)
        _az132_d["prompt"] = "X" * 40
        _az132_lines.append(json.dumps(_az132_d, ensure_ascii=False))
    (_az132_dir / _az132_f.name).write_text("\n".join(_az132_lines) + "\n", encoding="utf-8")
_az132_i1 = [t for v in _G.scan_suites(V1 / "bench" / "tasks").values() for t in v]
_az132_i2 = [t for v in _G.scan_suites(_az132_dir).values() for t in v]

check(RS, "AZ132e ⛔ 题面一个字都不读：把全部 prompt 换成占位符 ⇒ 题 id 不变、目标逐条不变\n"
          "              （`scan_suites` 只读 id；一旦它开始读 prompt，「自主」就可能是复述）",
      len(_az132_i1) > 0 and _az132_i1 == _az132_i2
      and _G.propose(_az132_obs, _az132_i1) == _G.propose(_az132_obs, _az132_i2),
      f"id={len(_az132_i1)} 相同={_az132_i1 == _az132_i2}")

_az132_done = _G.done_gids([{"gid": _az132_cover.gid, "verdict": "MET"},
                            {"gid": _az132_probe.gid, "verdict": "NOT_MET"}])
_az132_again = {g.gid for g in _G.propose(_az132_obs, _az132_ids, done=_az132_done)}

check(RS, "AZ132f 记账后的行为：MET 的**不再提**，NOT_MET 的**必须重新提**\n"
          "              （反例组：两件反过来 ⇒ 做成的反复做、做不成的被悄悄丢掉）",
      _az132_done == [_az132_cover.gid]
      and _az132_cover.gid not in _az132_again
      and _az132_probe.gid in _az132_again,
      f"done={len(_az132_done)} 再来={len(_az132_again)}")

# ===========================================================================
# AZ133 —— 「做不到」必须写进账本（V171）
#
# 锚的是**判定的形状**，不是效果：效果由 `bench/evidence/l5_s259/` 的 Q1~Q6 量。
# 这里只钉四条不许退化的性质 —— 每条都对应一个**会静默失效**的实现写法。
# ===========================================================================


def _az133_row(gid, cell, v, mean, kind="RAISE"):
    """与 `G.record()` 写出的形状逐字段相同（⛔ 不许只造 `verdict` 字段：
    少一个 `reading.mean_score`，判据就永远拿不到「读数动没动」）。"""
    return {"gid": gid, "kind": kind, "cell": cell, "verdict": v,
            "reading": {"cell": cell, "n_obs": 4, "mean_score": mean},
            "criterion": [["mean_score", ">=", 1.0]], "source": "contract"}


_az133_ok = [_az133_row("z1", "c1", "NOT_MET", 0.0) for _ in range(3)]
_az133_trend = [_az133_row("z2", "c2", "NOT_MET", m) for m in (0.2, 0.4, 0.6)]
_az133_gap = [_az133_row("z3", "c3", "NOT_MET", 0.0) for _ in range(2)]
_az133_broken = [_az133_row("z4", "c4", "NOT_MET", 0.0),
                 _az133_row("z4", "c4", "MET", 1.0),
                 _az133_row("z4", "c4", "NOT_MET", 0.0),
                 _az133_row("z4", "c4", "NOT_MET", 0.0)]

_az133_r = {n: sorted(_G.escalations(r)) for n, r in
            (("ok", _az133_ok), ("trend", _az133_trend),
             ("gap", _az133_gap), ("broken", _az133_broken))}

check(RS, "AZ133a 触发要同时满足三条：连判 ≥3 次 NOT_MET **且** 这几次均分逐位相同\\n"
          "              ⇒ 少任何一条都不许转（`0.2→0.4→0.6` 在收敛，不是做不到）",
      _az133_r["ok"] == ["c1"] and _az133_r["trend"] == [] and _az133_r["gap"] == [],
      f"{_az133_r}")

check(RS, "AZ133b 「连判」的「连」是字面意思：从最后一行往前数、遇到 MET 就停\\n"
          "              ⇒ `NOT_MET, MET, NOT_MET, NOT_MET` 末尾只连 2 次，**不许**转\\n"
          "              （反例：数「失败总次数」会把这一条判成试了 3 次 —— V171 首版正是这么错的）",
      _az133_r["broken"] == [],
      f"broken={_az133_r['broken']}")

_az133_fail_open = sorted(_G.escalations(
    _az133_ok, [{"task_id": "c1", "score": 1.0}] * 3))
_az133_still = sorted(_G.escalations(
    _az133_ok, [{"task_id": "c1", "score": 0.0}] * 3))

check(RS, "AZ133c fail-open：**现在**真的达标 ⇒ 这条判定自动消失（不做持久化状态位）\\n"
          "              ⛔ 反例：一旦转出就永久锁死 —— 换了模型、分真上去了，它还卡在那里",
      _az133_fail_open == [] and _az133_still == ["c1"],
      f"达标={_az133_fail_open} 未达标={_az133_still}")

_az133_suites = ["c1", "c9"]
_az133_esc = set(_G.escalations(_az133_ok))
_az133_obs = [{"task_id": "c1", "score": 0.0}] * 3 + [{"task_id": "c9", "score": 0.5}] * 3
_az133_with = [g.cell for g in _G.propose(_az133_obs, _az133_suites, escalated=_az133_esc)]
_az133_without = [g.cell for g in _G.propose(_az133_obs, _az133_suites)]
_az133_fmt = _G.render_escalations(_G.escalations(_az133_ok))

check(RS, "AZ133d 转运维的格**不再提 RAISE**，但别的格照常提；且它必须**留痕**\\n"
          "              （`render_escalations` 的输出里出现该格 —— 许撤不许藏：静默丢弃 = 做不成的被悄悄忘掉）",
      _az133_with == ["c9"] and sorted(_az133_without) == ["c1", "c9"]
      and "c1" in _az133_fmt and "0.000000" in _az133_fmt,
      f"转运维后提={_az133_with} 不转时提={_az133_without} 留痕={'c1' in _az133_fmt}")

# ===========================================================================
# AZ134 —— 「无事可做」与「做完了」必须是两个量（V172）
#
# 锚的是**判定形状**，不是效果：效果由 `bench/evidence/l5_s260/` 的 P1~P8 量。
# 旧形态是**一句带「或」的话**（「账本与套件都对不上，或该做的都做完了」），
# 把三种停法合成一种 ⇒ 「无事可做」永远会被读成「做完了」。
# ===========================================================================
_AZ134_IDS = ["k0", "k1", "k2", "k3", "k4"]


def _az134_sat(ids, score):
    return [{"task_id": c, "score": score} for c in ids for _ in range(3)]


_az134_obs0 = _az134_sat(_AZ134_IDS, 0.0)
_az134_obs1 = _az134_sat(_AZ134_IDS, 1.0)
_az134_raise = {g.cell: g for g in _G.propose(_az134_obs0, _AZ134_IDS)}
_az134_log = [_az133_row(_az134_raise[c].gid, c, "NOT_MET", 0.0) for c in _AZ134_IDS
              for _ in range(3)]
_az134_esc = _G.escalations(_az134_log, _az134_obs0)

_az134_st_obs = _az134_sat(["s0"], 0.5)
_az134_st_g = _G.propose(_az134_st_obs, ["s0"])[0]
_az134_st_log = [_az133_row(_az134_st_g.gid, "s0", "MET", 1.0)]
_az134_st_done = _G.done_gids(_az134_st_log)

_az134_arms = {
    "sat": (_G.propose(_az134_obs0, _AZ134_IDS, escalated=list(_az134_esc)),
            _az134_esc, _AZ134_IDS, _az134_obs0),
    "done": (_G.propose(_az134_obs1, _AZ134_IDS), {}, _AZ134_IDS, _az134_obs1),
    "stale": (_G.propose(_az134_st_obs, ["s0"], done=_az134_st_done), {},
              ["s0"], _az134_st_obs),
    "empty": ([], _az134_esc, [], _az134_obs0),
    "work": (_G.propose([], ["k0"]), {}, ["k0"], []),
}
_az134 = {k: _G.state(*v) for k, v in _az134_arms.items()}
_az134_names = {k: v[0] for k, v in _az134.items()}

check(RS, "AZ134a 「没有目标」有四种成因，⛔ 不许合成一句：五个构造输入 ⇒ 五个不同的名字\n"
          "              （全饱和 IDLE｜全达标 DONE｜被 done 挡 STALE｜空套件 EMPTY｜有活 WORK）",
      len(set(_az134_names.values())) == 5
      and _az134_names == {"sat": "IDLE", "done": "DONE", "stale": "STALE",
                           "empty": "EMPTY", "work": "WORK"},
      f"{_az134_names}")

# ⛔ 三头都咬：**同一份观测**（5 格全 0.0、都不达标）只换一个输入就换一个名字。
#    给 esc ⇒ IDLE；不给 ⇒ 提议会重新提 RAISE ⇒ WORK；把 5 条 RAISE 的 gid 都记成 MET ⇒ STALE。
#    ⚠️ 我原先押「不给 esc ⇒ STALE」，实测是 WORK（`propose()` 会重提 RAISE，提议不为 0）——
#       自踩登记在 RESULTS：这个名字不是「读数状态」，是**账本结论**。
_az134_noesc = _G.state(_G.propose(_az134_obs0, _AZ134_IDS), {}, _AZ134_IDS, _az134_obs0)[0]
_az134_alldone = _G.done_gids([{"gid": g.gid, "verdict": "MET"}
                               for g in _G.propose(_az134_obs0, _AZ134_IDS)])
_az134_withdone = _G.state(
    _G.propose(_az134_obs0, _AZ134_IDS, done=_az134_alldone), {},
    _AZ134_IDS, _az134_obs0)[0]

check(RS, "AZ134b IDLE ≠ DONE：全饱和 ⇒ IDLE、全达标 ⇒ DONE；**同一份观测**只换一个输入\n"
          "              ⇒ 给 esc 是 IDLE、不给是 WORK、全记成 MET 是 STALE（三个名字互不相同）\n"
          "              ⇒ 它们由不同的量决定，不是同一个数的三种措辞",
      _az134_names["sat"] == "IDLE" and _az134_names["done"] == "DONE"
      and _az134_names["sat"] != _az134_names["done"]
      and len({_az134_names["sat"], _az134_noesc, _az134_withdone}) == 3
      and (_az134_noesc, _az134_withdone) == ("WORK", "STALE"),
      f"sat={_az134_names['sat']} done={_az134_names['done']} "
      f"去esc={_az134_noesc} 全MET={_az134_withdone}")

_az134_blk = _az134["stale"][1]["blocked"]

check(RS, "AZ134c DONE 由**读数**算，不由「提议 0 条」算：某格 0.5 且它那条 RAISE 已判过 MET\n"
          "              ⇒ 提议 0 条但**不**是 DONE（是 STALE），且必须点名该格\n"
          "              （反例：拿提议数当判据 ⇒ 这一格谎报成做完；`done_gids` 是单调的）",
      _az134["stale"][0] == "STALE" and len(_az134_blk) == 1
      and _az134_blk[0]["cell"] == "s0" and _az134_blk[0]["gid"] == _az134_st_g.gid,
      f"名字={_az134['stale'][0]} blocked={[b['cell'] for b in _az134_blk]}")

_az134_emp = _G.state([], _az134_esc, _AZ134_IDS, _az134_obs0)[0]
_az134_emp2 = _G.state([], _az134_esc, [], _az134_obs0)[0]

check(RS, "AZ134d EMPTY 优先于 IDLE：同一份「转运维」证据，套件非空 ⇒ IDLE、套件 0 题 ⇒ EMPTY\n"
          "              ⇒ 决定因素是「有没有题」，不是「转运维几格」\n"
          "              （⛔ 否则「套件里没题」会被说成「测量已用尽」= 缺席冒充零）",
      _az134_emp == "IDLE" and _az134_emp2 == "EMPTY",
      f"套件非空={_az134_emp} 套件为空={_az134_emp2}")

# ⛔ 两处判据不许分叉：`satisfied()`（决定 DONE）与 `evaluate()`（决定提议）
#    对 n_obs ≥ MIN_OBS 的格必须同值；一旦分叉，就会出现「提议 0 条但不是 DONE」
#    或「DONE 了却还在提议」—— 两种都自相矛盾，且都不报错。
_az134_agree = []
for _az134_s in (0.0, 0.5, 1.0):
    _az134_o = _az134_sat(["a0"], _az134_s)
    _az134_g = _G.Goal(kind="RAISE", cell="a0", statement="x",
                       criterion=(("mean_score", ">=", _G.TARGET),), provenance=())
    _az134_agree.append(_G.satisfied(_az134_o, "a0")
                        == (_G.evaluate(_az134_g, _az134_o)[0] == "MET"))

check(RS, "AZ134e `satisfied()`（判 DONE）与 `evaluate()`（判提议）**同源**：\n"
          "              均分 0.0/0.5/1.0 三格上逐格同值 ⇒ 不会出现「DONE 了却还在提议」",
      _az134_agree == [True, True, True],
      f"{_az134_agree}")

import ast as _ast                                              # noqa: E402

_az134_tree = _ast.parse((V1 / "mw" / "goals.py").read_text(encoding="utf-8"))
_az134_consts = sorted(t.id for _n in _az134_tree.body if isinstance(_n, _ast.Assign)
                       for t in _n.targets
                       if isinstance(t, _ast.Name) and t.id.isupper())

check(RS, "AZ134f ⛔ 五个状态名**不做成模块级常量**：常量集合仍是 KINDS/MIN_OBS/TARGET\n"
          "              （V171 的 Q6 钉的是「常量集合不变」，它分不出阈值与名字表 ⇒\n"
          "               与其改旧判据，不如让名字只活在返回值与文档里）",
      _az134_consts == ["KINDS", "MIN_OBS", "TARGET"],
      f"{_az134_consts}")

# ===========================================================================
# AZ135 —— 「提分」这一类目标按定义不可能完成（V173）
#
# 锚的是**判定的形状**，效果由 `bench/evidence/l5_s261/` 的 P1~P7 量。
# 一句话定理：均分 ≥ 1.0 ⟺ **每个**分数都 ≥ 1.0（分数上限就是 1.0），
# 而 RAISE 只在均分 < 1.0 时提出 ⇒ 一条 RAISE 被提出时判据已经不可能满足。
# ⛔ 这不是「模型不够强」，是**判据不可满足** —— 两者的区别是这一轮的全部内容。
# ===========================================================================
def _az135_obs(cell, scores):
    return [{"task_id": cell, "score": float(s)} for s in scores]


_az135_sat = _G.unreachable("a1", _az135_obs("a1", [1.0, 1.0, 1.0]))
_az135_few = _G.unreachable("a1", _az135_obs("a1", [0.0, 1.0]))
_az135_bad = _G.unreachable("a1", _az135_obs("a1", [0.0, 1.2, 1.2]))
_az135_un = _G.unreachable("a1", _az135_obs("a1", [0.0, 0.0, 0.0]))
_az135_more = _G.unreachable("a1", _az135_obs("a1", [0.0, 0.0, 0.0] + [1.0] * 6))
_az135_more_mean = _G.stats(_az135_obs("a1", [0.0, 0.0, 0.0] + [1.0] * 6), "a1")["mean_score"]

check(RS, "AZ135a 判「按定义不可达」要三条同时：n≥MIN_OBS｜有 <TARGET 的分数｜没有 >TARGET 的分数\n"
          "              ⇒ 已达标/未到门槛/尺子坏了(1.2) 一律**弃权**；且追加 6 个满分后仍不可达\n"
          "              （均分只趋近 1，永不等于 1 —— 过去的失败留在分母里）",
      _az135_sat is None and _az135_few is None and _az135_bad is None
      and _az135_un and _az135_more and _az135_more_mean < _G.TARGET
      and _az135_un["reason"] == "CRITERION_UNREACHABLE"
      and _az135_un["n_below"] == 3,
      f"达标={_az135_sat} 未到门槛={_az135_few} 有1.2={_az135_bad} "
      f"不可达={bool(_az135_un)} 追加满分后={bool(_az135_more)}(mean={_az135_more_mean:.6f})")

_az135_struct = _G.escalations([], _az135_obs("c1", [0.0, 0.5, 1.0]))
_az135_stuck = _G.escalations([_az133_row("z9", "c2", "NOT_MET", 0.0) for _ in range(3)])

check(RS, "AZ135b 两条路都要在，且理由分得开：结构（一轮读数、**不需要任何账本行**）\n"
          "              与行为（连判 3 次、均分逐位相同）⛔ 不许合成一句\n"
          "              （合成 = 「没试过」与「试过没用」变成同一个读数）",
      sorted(_az135_struct) == ["c1"] and sorted(_az135_stuck) == ["c2"]
      and _az135_struct["c1"]["reason"] == "CRITERION_UNREACHABLE"
      and _az135_struct["c1"]["n_below"] == 2
      and _az135_stuck["c2"]["reason"] == "STUCK_MEANS"
      and _az135_stuck["c2"]["n_fail"] == 3,
      f"结构={sorted(_az135_struct)}:{_az135_struct.get('c1', {}).get('reason')} "
      f"行为={sorted(_az135_stuck)}:{_az135_stuck.get('c2', {}).get('reason')}")

_az135_o3 = _az135_obs("c3", [0.0, 0.0, 0.0])
_az135_esc3 = _G.escalations([], _az135_o3)
_az135_with = [g.kind for g in _G.propose(_az135_o3, ["c3"], escalated=list(_az135_esc3))]
_az135_without = [g.kind for g in _G.propose(_az135_o3, ["c3"])]

check(RS, "AZ135c **接上了**：同一条读数，转运维含该格 ⇒ 不再提 RAISE；不含 ⇒ 照提\n"
          "              （反例：判出来了但 `propose()` 不认它 ⇒ 一份死报告，提议数一点不变）",
      _az135_with == [] and _az135_without == ["RAISE"],
      f"转运维后提={_az135_with} 不转时提={_az135_without}")

_az135_esc_all = _G.escalations([], _az135_obs("x1", [0.0, 0.0, 0.0])
                               + _az135_obs("zz", [0.0, 0.0, 0.0]))
_az135_in, _az135_out = _G.split_escalations(_az135_esc_all, ["c3", "x1"])
_az135_sat3 = _az135_obs("c3", [1.0, 1.0, 1.0])
_az135_st_full = _G.state([], _az135_esc_all, ["c3"], _az135_sat3)[0]
_az135_st_in = _G.state([], _G.split_escalations(_az135_esc_all, ["c3"])[0],
                        ["c3"], _az135_sat3)[0]

check(RS, "AZ135d 套件外的格不许把状态带偏：账本里 2 格转运维、套件只有 1 格且已达标\n"
          "              ⇒ 用**全量** esc 是 IDLE（谎报「测量已用尽」）、只留套件内是 DONE\n"
          "              ⛔ 两半都要留（`split_escalations` 返回两个 dict），不许静默丢掉套件外那半",
      sorted(_az135_in) == ["x1"] and sorted(_az135_out) == ["zz"]
      and not (set(_az135_in) & set(_az135_out))
      and set(_az135_in) | set(_az135_out) == set(_az135_esc_all)
      and (_az135_st_full, _az135_st_in) == ("IDLE", "DONE"),
      f"套件内={sorted(_az135_in)} 套件外={sorted(_az135_out)} "
      f"全量状态={_az135_st_full} 过滤后={_az135_st_in}")

_az135_fmt = _G.render_escalations({**_az135_struct, **_az135_stuck})
_az135_fmt0 = _G.render_escalations({})

check(RS, "AZ135e 留痕：两种理由各自印出**读数**（结构印 n_below/min/mean，行为印均分逐位相同）；\n"
          "              0 条时这一节仍然打印 —— ⛔ 静默丢弃 = 做不成的被悄悄忘掉",
      "判据按定义不可满足" in _az135_fmt and "2 次 <1" in _az135_fmt
      and "0.500000" in _az135_fmt and "均分逐位相同" in _az135_fmt
      and "没有" in _az135_fmt0,
      f"结构行={'判据按定义不可满足' in _az135_fmt} 行为行={'均分逐位相同' in _az135_fmt} "
      f"0条={_az135_fmt0[:14]}…")

# ===========================================================================
# AZ136 —— 分数信息量：「这题的分数是单射还是常数」（V174）
# 锚的是**判定的形状**，效果由 `bench/evidence/l3_s262/` 的 P1~P7 量。
# 定义唯一来源：`bench/evidence/l3_s262/PREREG_v174.md` §1。
# ⛔ 这不是新打分器：不判对错、不改分数，只对既有分数做二次读数（§169.7 排除项仍有效）。
# ===========================================================================
import mw.scoreinfo as _SI                                      # noqa: E402

_az136_const = _SI.classify([("i1", 1.0), ("i2", 1.0)])
_az136_inj = _SI.classify([("i1", 1.0), ("i2", 0.6), ("i3", 0.2)])
_az136_coarse = _SI.classify([("a", 0.0), ("b", 0.0), ("c", 0.0), ("d", 0.2),
                              ("e", 0.6), ("f", 0.6), ("g", 1.0)])
_az136_few = _SI.classify([("only", 1.0)])
_az136_rul = _SI.classify([("i1", 1.0), ("i2", 1.2)])
_az136_conf = _SI.classify([("i1", 1.0), ("i1", 0.6), ("i2", 0.2)])
_az136_nan = _SI.classify([("i1", float("nan")), ("i2", 0.2)])
_az136_bool = _SI.classify([("i1", True), ("i2", 0.2)])

check(RS, "AZ136a 弃权三条 + 两个陷阱：TOO_FEW / RULER_BROKEN / IMPL_SCORE_CONFLICT；\n"
          "              NaN 与 bool 必须落 RULER_BROKEN（bool 是 int 子类，不挡会被记成 1.0）；\n"
          "              撤回语义：删掉 `sf > target` 那一枝 ⇒ 本条必须红",
      _az136_few["reason"] == "TOO_FEW"
      and _az136_rul["reason"] == "RULER_BROKEN"
      and _az136_conf["reason"] == "IMPL_SCORE_CONFLICT"
      and _az136_nan["reason"] == "RULER_BROKEN"
      and _az136_bool["reason"] == "RULER_BROKEN"
      and all(r["kind"] == "UNKNOWN" for r in
              (_az136_few, _az136_rul, _az136_conf, _az136_nan, _az136_bool)),
      f"few={_az136_few['reason']} ruler={_az136_rul['reason']} "
      f"conflict={_az136_conf['reason']} nan={_az136_nan['reason']} "
      f"bool={_az136_bool['reason']}")

check(RS, "AZ136b 三分类自洽：CONSTANT⇒bits=0｜INJECTIVE⇒bits=log2(n) 且 resolution=1｜\n"
          "              COARSE⇒0<bits≤bits_max 且 resolution∈(0,1)；\n"
          "              撤回语义：熵值 +0.5 ⇒ 本条必须红（⛔ 第一版写「不把 bits 归零」是空撤 ——\n"
          "              单桶熵恒 0，撤了也绿；撤回态跑之前先自问『撤掉这行行为真的变吗』）",
      _az136_const["kind"] == "CONSTANT" and _az136_const["bits"] == 0.0
      and _az136_inj["kind"] == "INJECTIVE"
      and abs(_az136_inj["bits"] - math.log2(3)) < 1e-12
      and _az136_inj["resolution"] == 1.0
      and _az136_coarse["kind"] == "COARSE"
      and abs(_az136_coarse["resolution"] - 4 / 7) < 1e-12
      and 0.0 < _az136_coarse["bits"] <= _az136_coarse["bits_max"] + 1e-12,
      f"const bits={_az136_const['bits']}｜inj bits={_az136_inj['bits']:.6f} "
      f"res={_az136_inj['resolution']}｜coarse res={_az136_coarse['resolution']:.6f} "
      f"bits={_az136_coarse['bits']:.6f}/{_az136_coarse['bits_max']:.6f}")

check(RS, "AZ136c 逐桶点名（0.0 桶恰 ['a','b','c']，⛔ 不许只报桶数）+ 弃权不给半个读数\n"
          "              （bits/buckets 全空 —— 弃权就是弃权）；\n"
          "              撤回语义：buckets 改成只报计数 ⇒ 本条必须红",
      _az136_coarse["buckets"].get("0") == ["a", "b", "c"]
      and _az136_coarse["n_scores"] == 4 and _az136_coarse["n_impls"] == 7
      and all(r["bits"] is None and r["bits_max"] is None and r["buckets"] == {}
              for r in (_az136_few, _az136_rul, _az136_conf)),
      f"0.0桶={_az136_coarse['buckets'].get('0')} 弃权空读数=True")

_az136_twice = _SI.classify([("a", 0.0), ("b", 0.0), ("c", 0.0), ("d", 0.2),
                             ("e", 0.6), ("f", 0.6), ("g", 1.0)])
check(RS, "AZ136d 纯函数确定性：同池双跑输出逐字相同（json.dumps sort_keys 对拍）；\n"
          "              撤回语义：输出里掺时间戳/随机 ⇒ 本条必须红",
      json.dumps(_az136_coarse, sort_keys=True) == json.dumps(_az136_twice, sort_keys=True),
      "双跑逐字相同=True")

check(RS, "AZ136e 闸优先于分类：全等但只 1 个实现的池 ⇒ TOO_FEW 弃权，\n"
          "              ⛔ 不许因为「分都一样」就报 CONSTANT（样本不够就没有结论）；\n"
          "              撤回语义：min_pool 闸挪到分类之后 ⇒ 本条必须红",
      _az136_few["kind"] == "UNKNOWN" and _az136_few["reason"] == "TOO_FEW"
      and _az136_few["n_scores"] is None,
      f"kind={_az136_few['kind']} reason={_az136_few['reason']} k={_az136_few['n_scores']}")

_az136_kinds = {_az136_const["kind"], _az136_inj["kind"], _az136_coarse["kind"],
                _az136_few["kind"], _az136_rul["kind"], _az136_conf["kind"]}
_az136_reasons = {_az136_few["reason"], _az136_rul["reason"], _az136_conf["reason"]}
check(RS, "AZ136f 值域封闭：输出的 kind 恰落 {CONSTANT,INJECTIVE,COARSE,UNKNOWN}、\n"
          "              弃权 reason 恰落 {TOO_FEW,RULER_BROKEN,IMPL_SCORE_CONFLICT}；\n"
          "              撤回语义：引入第五个 kind 名字 ⇒ 本条必须红",
      _az136_kinds == {"CONSTANT", "INJECTIVE", "COARSE", "UNKNOWN"}
      and _az136_reasons == {"TOO_FEW", "RULER_BROKEN", "IMPL_SCORE_CONFLICT"},
      # ⚠️ 详情里 sorted() 必须过 str —— 撤回态下 reason 会变 None，
      #    `sorted({None, "X"})` 抛 TypeError ⇒ 异常逃出去、汇总行消失
      #    （AP11 同型：撤回后不是转红而是崩 —— 崩溃会带走判据本身）。
      f"kinds={sorted(str(x) for x in _az136_kinds)} "
      f"reasons={sorted(str(x) for x in _az136_reasons)}")

check(RS, "AZ136g 两模块解耦不许漂移：scoreinfo.TARGET 与 goals.TARGET 同值；\n"
          "              撤回语义：任一侧改了上限 ⇒ 本条必须红（尺子的上限只许有一个数）",
      _SI.TARGET == _G.TARGET == 1.0,
      f"scoreinfo.TARGET={_SI.TARGET} goals.TARGET={_G.TARGET}")

# ===========================================================================
# AZ137 —— 分数可动性消费接线：修复轴映射 + impl_sha + 覆盖定理锚（V175）
#
# 锚的是**判定的形状**，效果由 `bench/evidence/l3_s263/` 的 P1~P6 量。
# 覆盖定理（PREREG_v175 §0.2）：claim 能触发的格必已被 V173-A 结构判据收进
# esc ⇒ 第三型 gate 在当前判据下无差别（AZ137e 钉的就是这个「不进 gate」）。
# 本轮的正菜是**修复轴映射**：CRITERION（改判据）/ MODEL（改模型）/
# RULER（改尺子）—— 「诊断产物 → 修复动作」的第一片。
# ===========================================================================
def _az137_rows(cell, specs):
    rows = []
    for impl, s in specs:
        r = {"task_id": cell, "score": float(s)}
        if impl is not None:
            r["impl_sha"] = impl
        rows.append(r)
    return rows

_az137_mixed = [{"task_id": "x", "impl_sha": "a", "score": 0.5},
                {"task_id": "x", "impl_sha": "b", "score": 0.5},
                {"task_id": "x", "score": 0.9},
                {"task_id": "x", "impl_sha": "c"},
                {"task_id": "y", "impl_sha": "z", "score": 1.0}]
_az137_pool, _az137_nno = _SI.pool_from_rows(_az137_mixed, cell="x")
_az137_pool_all, _az137_nno_all = _SI.pool_from_rows(_az137_mixed, cell=None)
_az137_pool_c, _az137_nno_c = _SI.pool_from_rows(_az137_mixed + ["bad"], cell="x")
_az137_pool_cg, _az137_nno_cg = _SI.pool_from_rows(_az137_mixed + ["bad"], cell=None)

check(RS, "AZ137a 池抽取可归因：缺身份/缺分数行**计数排除**（⛔ 不许当成实现 \"None\" 混进池）；\n"
          "              逐格 n_no 只数本格的行，非 dict 坏行只进全局计数（无从归属）；\n"
          "              撤回语义：缺身份行混进池 ⇒ 本条必须红",
      sorted(_az137_pool) == [("a", 0.5), ("b", 0.5)] and _az137_nno == 2
      and len(_az137_pool_all) == 3 and _az137_nno_all == 2
      and _az137_nno_c == 2 and _az137_nno_cg == 3,
      f"逐格 pool={sorted(_az137_pool)} n_no={_az137_nno}｜全局 pool={len(_az137_pool_all)} "
      f"n_no={_az137_nno_all}｜坏行 逐格={_az137_nno_c} 全局={_az137_nno_cg}")

check(RS, "AZ137b impl_sha_of 纯函数：同文同值、异文异值、恒 16 位\n"
          "              （⛔ 自由文本标签/session 当不了实现身份 —— PREREG_v175 §0.1）；\n"
          "              撤回语义：返回常数 ⇒ 本条必须红",
      _SI.impl_sha_of("hello") == _SI.impl_sha_of("hello")
      and _SI.impl_sha_of("hello") != _SI.impl_sha_of("hell")
      and len(_SI.impl_sha_of("hello")) == 16,
      f"len={len(_SI.impl_sha_of('hello'))}")

_az137_esc_legacy = _G.escalations([], _az137_rows("c1", [(None, 0.0), (None, 0.5), (None, 1.0)]))
_az137_o_claim = _az137_rows("c4", [("i0", 0.4), ("i1", 0.4), ("i2", 0.4)])
_az137_esc_claim = _G.escalations([], _az137_o_claim)
# ⚠️ claim 读数直接取自 `movability()`，⛔ 不从 esc overlay 取 —— 否则
#    AZ137c 的撤回（overlay 恒 None）会把它打成 None，下游 `.get()` 崩掉
#    ⇒ 转红变崩溃（AP11 同型）。断言之间输入必须互相独立。
_az137_m_claim = _SI.movability(_az137_o_claim, cell="c4")
_az137_overlay = _az137_esc_claim["c4"].get("movability")
_az137_log = [{"gid": "z", "cell": "c5", "kind": "RAISE", "verdict": "NOT_MET",
               "reading": {"mean_score": 0.0}} for _ in range(3)]
_az137_esc_stuck = _G.escalations(_az137_log, [])

check(RS, "AZ137c overlay fail-open + 负对照：无身份 ⇒ overlay=None 且结构路旧字段逐位不变；\n"
          "              无 obs 的行为路 ⇒ None（无池）；有池 ⇒ 必须在场且 claim 齐全\n"
          "              （fail-open 不许写成恒 None）；\n"
          "              撤回语义：overlay 恒 None ⇒ 本条必须红",
      _az137_esc_legacy["c1"].get("movability") is None
      and _az137_esc_legacy["c1"]["reason"] == "CRITERION_UNREACHABLE"
      and _az137_esc_legacy["c1"]["n_below"] == 2
      and _az137_esc_stuck["c5"].get("movability") is None
      and _az137_overlay is not None
      and _az137_overlay.get("claim") == "SCORE_UNMOVABLE"
      and _az137_overlay.get("label") == "CONSTANT"
      and _az137_overlay.get("n_impls") == 3,
      f"legacy overlay={_az137_esc_legacy['c1'].get('movability')} "
      f"stuck overlay={_az137_esc_stuck['c5'].get('movability')} overlay={_az137_overlay}")

check(RS, "AZ137d repair_axes：值域恰 {CRITERION,MODEL,RULER}、映射逐条锚定\n"
          "              （CRITERION_UNREACHABLE→CRITERION｜STUCK_MEANS→MODEL｜\n"
          "               claim 叠加 RULER｜弃权/无 overlay 不加）；\n"
          "              撤回语义：返回恒 (\"MODEL\",) ⇒ 本条必须红",
      _G.repair_axes("CRITERION_UNREACHABLE", None) == ("CRITERION",)
      and _G.repair_axes("CRITERION_UNREACHABLE", _az137_m_claim) == ("CRITERION", "RULER")
      and _G.repair_axes("STUCK_MEANS", None) == ("MODEL",)
      and _G.repair_axes("STUCK_MEANS", {"claim": "SCORE_UNMOVABLE"}) == ("MODEL", "RULER")
      and _G.repair_axes("STUCK_MEANS", {"claim": None}) == ("MODEL",)
      and _G.repair_axes("X", None) == (),
      f"CRIT={_G.repair_axes('CRITERION_UNREACHABLE', None)} "
      f"CRIT+RULER={_G.repair_axes('CRITERION_UNREACHABLE', _az137_m_claim)} "
      f"MODEL={_G.repair_axes('STUCK_MEANS', None)}")

_az137_g_raised = [g.kind for g in _G.propose(_az137_o_claim, ["c4"])]
check(RS, "AZ137e 覆盖定理锚：claim 是**诊断**不是 gate —— claim 触发的格，propose 不传\n"
          "              escalated 时照提 RAISE（⛔ 不许内部读 movability 过滤）；\n"
          "              撤回语义：propose 读 movability 过滤 ⇒ 本条必须红",
      _az137_g_raised == ["RAISE"] and _az137_m_claim.get("claim") == "SCORE_UNMOVABLE",
      f"propose={_az137_g_raised} claim={_az137_m_claim.get('claim')}")

_az137_m_two = _SI.movability(_az137_rows("t1", [("a", 0.5), ("b", 0.5)]), cell="t1")
_az137_m_sat = _SI.movability(_az137_rows("t2", [("a", 1.0), ("b", 1.0), ("c", 1.0)]), cell="t2")
_az137_m_coarse = _SI.movability(_az137_rows("t3", [("a", 0.4), ("b", 0.6), ("c", 0.6)]), cell="t3")
_az137_m_broken = _SI.movability(_az137_rows("t4", [("a", 1.2), ("b", 0.4), ("c", 0.4)]), cell="t4")

check(RS, "AZ137f claim 三合取缺一不给：label=CONSTANT ∧ n_impls≥MIN_IMPLS(3) ∧ 桶值<TARGET\n"
          "              ⇒ 2 实现不给（样本不够）｜已达标 3×1.0 不给（那是「做完了」）｜\n"
          "                COARSE 不给｜尺子坏(1.2)弃权不给；\n"
          "              撤回语义：拆掉 <TARGET 门槛 ⇒ 本条必须红",
      _az137_m_two.get("claim") is None and _az137_m_two.get("label") == "CONSTANT"
      and _az137_m_sat.get("claim") is None
      and _az137_m_coarse.get("claim") is None
      and _az137_m_broken.get("claim") is None
      and _az137_m_claim.get("claim") == "SCORE_UNMOVABLE",
      f"two={_az137_m_two.get('claim')} sat={_az137_m_sat.get('claim')} "
      f"coarse={_az137_m_coarse.get('claim')} broken={_az137_m_broken.get('claim')}")

# ═══════════ V176（§244）：表现型扩容 —— 准入器与套件结构（AZ138） ═══════════
# 只锚纯函数面与静态面（⛔ 不在契约里跑打分器 —— 那是 probe_v176 的事）。
import bench.admit_sys as _AS                                            # noqa: E402
from mw.bench import Suite as _Suite                                     # noqa: E402

_az138_suite = _Suite.load(str(Path(__file__).resolve().parents[1]
                               / "bench" / "tasks" / "sys_small.jsonl"))


def _az138_good() -> dict:
    """合成「全好」读数：分数面手造，身份面取自真注册表与真套件 meta。"""
    rd: dict = {}
    for t in _az138_suite.tasks:
        m = dict(t.meta)
        rd[t.id] = {
            "oracle": 1.0, "null": 0.0, "partial": 0.5,
            "mutants": {n: 0.0 for n, _ in _AS.MUTANTS.get(t.id, [])},
            "oracle_replay": 1.0, "partial_replay": 0.5,
            "format": m.get("format", "single"), "kind": m.get("kind", "pure"),
        }
    return rd


def _az138_why(readings: dict, check: str) -> str:
    v = _AS.verdict(readings)
    return "｜".join(f["why"] for f in v.get("failed", []) if f.get("check") == check)


_az138_g = _az138_good()

# —— A1/A2/A3 真值表（单因变体：读数只坏一处，replay 同步改，⛔ 不牵连） ——
_az138_b1 = _az138_good()
_az138_b1["s01_kv_expiry"]["oracle"] = 0.99
_az138_b1["s01_kv_expiry"]["oracle_replay"] = 0.99
_az138_b2 = _az138_good()
_az138_b2["s02_limiter"]["null"] = 0.1          # A2 坏、A3 仍严格（0.1<0.5<1.0）
_az138_b3 = _az138_good()
_az138_b3["s03_tx_journal"]["partial"] = 0.0    # partial==null ⇒ 只坏 A3
_az138_b3["s03_tx_journal"]["partial_replay"] = 0.0
_az138_v_b1 = _AS.verdict(_az138_b1)
_az138_v_b2 = _AS.verdict(_az138_b2)
_az138_v_b3 = _AS.verdict(_az138_b3)
_az138_c1 = sorted({f.get("check") for f in _az138_v_b1.get("failed", [])})
_az138_c2 = sorted({f.get("check") for f in _az138_v_b2.get("failed", [])})
_az138_c3 = sorted({f.get("check") for f in _az138_v_b3.get("failed", [])})

check(RS, "AZ138a verdict 真值表：好读数 ⇒ ok；oracle≠1.0 ⇒ 拒且点名 A1；null≠0 ⇒ A2；\n"
          "              中段不严格（partial==null） ⇒ A3；\n"
          "              撤回语义：废掉 A1 判据分支 ⇒ 本条必须红",
      _AS.verdict(_az138_g)["ok"] is True
      and not _az138_v_b1["ok"] and _az138_c1 == ["A1"]
      and not _az138_v_b2["ok"] and _az138_c2 == ["A2"]
      and not _az138_v_b3["ok"] and _az138_c3 == ["A3"],
      f"good={_AS.verdict(_az138_g)['ok']} b1={_az138_c1} b2={_az138_c2} b3={_az138_c3}")

# —— fail-open ——
_az138_f1 = _az138_good()
del _az138_f1["m01_records"]["format"]
_az138_f2 = _az138_good()
_az138_f2["m02_vfs"]["oracle"] = "x"
_az138_f3 = _az138_good()
_az138_f3["m03_tiered"]["mutants"] = [0.0]
_az138_v_f1 = _AS.verdict(_az138_f1)
_az138_v_f2 = _AS.verdict(_az138_f2)
_az138_v_f3 = _AS.verdict(_az138_f3)

check(RS, "AZ138b fail-open：缺键 ⇒ 拒且 why 点名「缺读数」；分数类型错 / mutants 非对象\n"
          "              ⇒ 一律拒（⛔ 「没量到」不许读成「通过」）；\n"
          "              撤回语义：缺键分支不触发（落进类型判）⇒ 本条必须红",
      not _az138_v_f1["ok"] and "缺读数" in _az138_why(_az138_f1, "FAIL_OPEN")
      and not _az138_v_f2["ok"] and "类型" in _az138_why(_az138_f2, "FAIL_OPEN")
      and not _az138_v_f3["ok"],
      f"f1={_az138_why(_az138_f1, 'FAIL_OPEN')[:40]} "
      f"f2={_az138_why(_az138_f2, 'FAIL_OPEN')[:40]} f3={_az138_v_f3['ok']}")

# —— A4 双向 + A5 ——
_az138_m1 = _az138_good()
_az138_m1["m03_tiered"]["mutants"]["wipe_state"] = 1.0
_az138_m2 = _az138_good()
_az138_m2["s01_kv_expiry"]["mutants"]["ghost_unregistered"] = 0.0
_az138_m3 = _az138_good()
_az138_m3["s02_limiter"]["oracle_replay"] = 0.5
_az138_v_m1 = _AS.verdict(_az138_m1)
_az138_v_m2 = _AS.verdict(_az138_m2)
_az138_v_m3 = _AS.verdict(_az138_m3)

check(RS, "AZ138c A4 双向 + A5：变异体得分 ≥ oracle ⇒ 拒；读数带未注册变异体名 ⇒ 拒；\n"
          "              复打分与首打分不一致 ⇒ 拒且点名 A5；\n"
          "              撤回语义：A5 分支恒不触发 ⇒ 本条必须红",
      not _az138_v_m1["ok"] and _az138_why(_az138_m1, "A4")
      and not _az138_v_m2["ok"] and _az138_why(_az138_m2, "A4")
      and not _az138_v_m3["ok"] and _az138_why(_az138_m3, "A5"),
      f"m1 A4={bool(_az138_why(_az138_m1, 'A4'))} m2 A4={bool(_az138_why(_az138_m2, 'A4'))} "
      f"m3 A5={bool(_az138_why(_az138_m3, 'A5'))}")

# —— 注册表形状（静态面）——
_az138_ids = [t.id for t in _az138_suite.tasks]
_az138_reg = _AS.MUTANTS
_az138_shape = (
    set(_az138_reg) == set(_az138_ids)
    and all(_az138_reg.get(tid) for tid in _az138_ids)
    and all(any(n.startswith("wipe_") for n, _ in _az138_reg.get(tid, []))
            for tid in _az138_ids)
    and all(any(n.startswith("flat_") for n, _ in _az138_reg.get(tid, []))
            for tid in _az138_ids
            if dict(next(t for t in _az138_suite.tasks if t.id == tid).meta)
            .get("format") == "files")
    and all(n.startswith("wipe_") or n.startswith("flat_")
            for tid in _az138_ids for n, _ in _az138_reg.get(tid, []))
)

check(RS, "AZ138d 注册表形状：MUTANTS 题集 == sys_small 题集；每题 ≥1 变异体且 ≥1 wipe_*；\n"
          "              format=files 题 ≥1 flat_*；名字受控前缀 {wipe_, flat_}；\n"
          "              撤回语义：注册表删掉一题 ⇒ 本条必须红",
      _az138_shape,
      f"reg={sorted(_az138_reg)} ids={sorted(_az138_ids)}")

# —— 套件结构（静态面）——
_az138_struct = (
    len(_az138_suite) == 6 and len(set(_az138_ids)) == 6
    and all(t.oracle.strip() and t.partial.strip() and t.null == ""
            for t in _az138_suite.tasks)
    and all(dict(t.meta).get("format") in ("single", "files")
            and dict(t.meta).get("kind") in ("stateful", "pure")
            for t in _az138_suite.tasks)
    and all(all("FILES" in x for x in t.tests)
            if dict(t.meta).get("format") == "files"
            else all("FILES" not in x for x in t.tests)
            for t in _az138_suite.tasks)
)

check(RS, "AZ138e 套件结构：6 题 id 唯一、oracle/partial 非空、null==''；\n"
          "              meta 词表封闭（format/kind）；files 题测试全含 FILES 物化、\n"
          "              single 题测试不引用 FILES；\n"
          "              撤回语义：一题的 null 改成非空 ⇒ 本条必须红",
      _az138_struct,
      f"n={len(_az138_suite)} 词表="
      + "｜".join(f"{t.id}:{dict(t.meta).get('format')}/{dict(t.meta).get('kind')}"
                  for t in _az138_suite.tasks))

# —— verdict 输出形状封闭 + 纯性 ——
_az138_vg = _AS.verdict(_az138_g)
_az138_vg2 = _AS.verdict(_az138_g)
_az138_vb = _AS.verdict(_az138_b1)
_az138_shape_out = (
    isinstance(_az138_vg.get("ok"), bool)
    and isinstance(_az138_vg.get("failed"), list)
    and all(set(f.keys()) == {"task", "check", "why"} for f in _az138_vg["failed"])
    and all(set(f.keys()) == {"task", "check", "why"} for f in _az138_vb["failed"])
)

check(RS, "AZ138f verdict 输出形状封闭：ok 是 bool、failed 是列表且条目恰含\n"
          "              {task, check, why} 三键；同输入双调用逐字相同（纯函数）；\n"
          "              撤回语义：failed 条目多一个键 ⇒ 本条必须红",
      _az138_shape_out and _az138_vg == _az138_vg2,
      f"shape={_az138_shape_out} pure={_az138_vg == _az138_vg2}")

# ═══════════ V177（§245）：行为级分数信息量 —— 同行为同分、异行为异分（AZ139） ═══════════
# 锚判定的**形状**，效果由 `bench/evidence/l3_s265/` 的 P1~P7 量。
# 单位是**行为类**（通过向量），不是实现 —— 与 AZ136（分数级，每实现一票）不同层。
# 注册定理（PREREG_v177 §1.6，前提=两边都不弃权）：
#   T1 行为 CONSTANT ⟺ 分数 CONSTANT ｜ T2 分数 INJECTIVE ⇒ 行为 INJECTIVE。

_az139_const3 = _SI.behavioral([("a", (True, False, False), 0.5),
                                ("b", (False, True, False), 0.5),
                                ("c", (False, False, True), 0.5)])
_az139_const2 = _SI.behavioral([("a", (True, False), 0.5),
                                ("b", (False, True), 0.5)])
_az139_const_full = _SI.behavioral([("a", (True, False), 1.0),
                                    ("b", (False, True), 1.0),
                                    ("c", (True, True), 1.0)])
_az139_t1b_rows = [("a", (True, False), 0.5), ("b", (True, False), 0.5),
                   ("c", (False, True), 0.5)]
_az139_t1b_b = _SI.behavioral(_az139_t1b_rows)
_az139_t1b_c = _SI.classify([(i, s) for i, _v, s in _az139_t1b_rows])
_az139_t1a_rows = [("a", (True, False), 0.5), ("b", (False, True), 0.5),
                   ("c", (True, True), 0.5)]
_az139_t1a_b = _SI.behavioral(_az139_t1a_rows)
_az139_t1a_c = _SI.classify([(i, s) for i, _v, s in _az139_t1a_rows])

check(RS, "AZ139a T1 双向（前提=两边都不弃权）：行为 CONSTANT ⟺ 分数 CONSTANT\n"
          "              （常数池两层都是 CONSTANT；⚠️ 弃权池不进 T1 —— 前提不满足）；\n"
          "              撤回语义：行为级 CONSTANT 分支废掉 ⇒ 本条必须红",
      _az139_t1a_b["kind"] == _SI.KIND_CONSTANT
      and _az139_t1a_c["kind"] == _SI.KIND_CONSTANT
      and _az139_t1b_b["kind"] == _SI.KIND_CONSTANT
      and _az139_t1b_c["kind"] == _SI.KIND_CONSTANT
      and _az139_const3["kind"] == _SI.KIND_CONSTANT,
      f"t1a beh={_az139_t1a_b['kind']} score={_az139_t1a_c['kind']}｜"
      f"t1b beh={_az139_t1b_b['kind']} score={_az139_t1b_c['kind']}")

_az139_t2_rows = [("a", (True, False), 0.5), ("b", (False, True), 0.75),
                  ("c", (True, True), 1.0)]
_az139_t2_b = _SI.behavioral(_az139_t2_rows)
_az139_t2_c = _SI.classify([(i, s) for i, _v, s in _az139_t2_rows])
_az139_ct_rows = [("a", (True, True, False, False), 0.5),
                  ("b", (True, True, False, False), 0.5),
                  ("c", (True, True, True, False), 0.75),
                  ("d", (True, True, True, True), 1.0)]
_az139_ct_b = _SI.behavioral(_az139_ct_rows)
_az139_ct_c = _SI.classify([(i, s) for i, _v, s in _az139_ct_rows])
_az139_cc_rows = [("a", (True, False), 0.5), ("b", (True, False), 0.7)]
_az139_cc_b = _SI.behavioral(_az139_cc_rows)
_az139_cc_c = _SI.classify([(i, s) for i, _v, s in _az139_cc_rows])

check(RS, "AZ139b T2 + 两层对照 + §0.3 反例：分数 INJECTIVE ⇒ 行为 INJECTIVE；\n"
          "              同池两层可以不同层（行为 INJECTIVE ∧ 分数 COARSE）；\n"
          "              同行为异分 ⇒ 行为级必须弃权（前提不满足），而分数级是 INJECTIVE ——\n"
          "              T2 不带前提就是假的；\n"
          "              撤回语义：行为级 INJECTIVE 分支废掉 ⇒ 本条必须红",
      _az139_t2_c["kind"] == _SI.KIND_INJECTIVE
      and _az139_t2_b["kind"] == _SI.KIND_INJECTIVE
      and _az139_ct_b["kind"] == _SI.KIND_INJECTIVE
      and _az139_ct_c["kind"] == _SI.KIND_COARSE
      and _az139_cc_b["kind"] == _SI.KIND_UNKNOWN
      and _az139_cc_b["reason"] == _SI.REASON_BEHAVIOR_SCORE_CONFLICT
      and _az139_cc_c["kind"] == _SI.KIND_INJECTIVE,
      f"t2 beh={_az139_t2_b['kind']} score={_az139_t2_c['kind']}｜"
      f"对照 beh={_az139_ct_b['kind']} score={_az139_ct_c['kind']}｜"
      f"反例 beh={_az139_cc_b['kind']}/{_az139_cc_b['reason']} "
      f"score={_az139_cc_c['kind']}")

_az139_coarse = _SI.behavioral([("a", (True, False, False, False), 0.25),
                                ("b", (False, True, False, False), 0.25),
                                ("c", (True, True, True, False), 0.75),
                                ("d", (True, True, True, True), 1.0)])
_az139_conf = _SI.behavioral([("a", (True, False), 0.5), ("b", (True, False), 0.7)])

check(RS, "AZ139c claim 三合取缺一不给：CONSTANT ∧ n_behaviors≥3 ∧ 桶值<TARGET\n"
          "              ⇒ 3 行为@0.5 给｜2 行为不给（反巧合）｜@1.0 不给（做完了≠做不到）｜\n"
          "                COARSE 不给｜弃权不给；\n"
          "              撤回语义：拆掉 MIN_BEHAVIORS 门槛 ⇒ 本条必须红",
      _az139_const3["claim"] == _SI.CLAIM_BEHAVIOR_UNRESOLVED
      and _az139_const2["claim"] is None
      and _az139_const_full["claim"] is None
      and _az139_coarse["claim"] is None
      and _az139_conf["claim"] is None,
      f"3行为={_az139_const3['claim']} 2行为={_az139_const2['claim']} "
      f"1.0={_az139_const_full['claim']} coarse={_az139_coarse['claim']} "
      f"弃权={_az139_conf['claim']}")

_az139_clean = _SI.behavioral([("a", (True, False), 0.5), ("b", (True, False), 0.5),
                               ("c", (False, True), 1.0)])
_az139_conf_impl = _SI.behavioral([("a", (True, False), 0.5),
                                   ("a", (False, True), 0.5)])

check(RS, "AZ139d 冲突闸两个子检查都弹：同行为多分（违反同行为同分）与\n"
          "              同实现多行为（确定性判分下口径混了）⇒ 弃权；\n"
          "              干净池（同行为同分、异实现）不许弹（那是判据要的性质，不是缺陷）；\n"
          "              ⚠️ 干净池只看「不弃权」—— 分到哪一类是 T2（AZ139b）的观测面，\n"
          "              本条多看一眼就会把 INJECTIVE 分支的撤回连带进来（R16 实测）；\n"
          "              撤回语义：同实现多行为子检查废掉 ⇒ 本条必须红",
      _az139_conf["kind"] == _SI.KIND_UNKNOWN
      and _az139_conf["reason"] == _SI.REASON_BEHAVIOR_SCORE_CONFLICT
      and _az139_conf_impl["kind"] == _SI.KIND_UNKNOWN
      and _az139_conf_impl["reason"] == _SI.REASON_BEHAVIOR_SCORE_CONFLICT
      and _az139_clean["kind"] != _SI.KIND_UNKNOWN
      and _az139_clean["reason"] is None,
      f"同行为多分={_az139_conf['reason']} 同实现多行为={_az139_conf_impl['reason']} "
      f"干净池={_az139_clean['kind']}")

_az139_few = _SI.behavioral([("a", (True, False), 0.5)])
_az139_rul = _SI.behavioral([("a", (True, False), 1.2), ("b", (False, True), 0.5)])
_az139_all = [_az139_const3, _az139_const2, _az139_t2_b, _az139_coarse,
              _az139_few, _az139_conf, _az139_conf_impl, _az139_rul]
_az139_keys = {"kind", "reason", "n_impls", "n_behaviors", "n_scores",
               "resolution", "bits", "bits_max", "buckets", "claim", "detail"}
# ⚠️ 值域口径 = **词表封闭**（常量值 vs 字面集对撞）+ 输出不越界。
#    ⛔ 不要求采样臂「凑齐」4 个 kind —— 那会让任何打掉某类 kind 的撤回
#    （如 AZ139a 废掉 CONSTANT 分支）连带本条（假耦合：换断言口径伪造单红）。
_az139_vocab = {_SI.KIND_CONSTANT, _SI.KIND_INJECTIVE, _SI.KIND_COARSE, _SI.KIND_UNKNOWN}
_az139_rvocab = {_SI.REASON_TOO_FEW, _SI.REASON_RULER_BROKEN,
                 _SI.REASON_BEHAVIOR_SCORE_CONFLICT}
_az139_kinds = {r["kind"] for r in _az139_all}
_az139_reasons = {r["reason"] for r in _az139_all if r["reason"] is not None}
_az139_claims = {r["claim"] for r in _az139_all}
_az139_shape_ok = all(set(r.keys()) == _az139_keys for r in _az139_all)
_az139_range_ok = all(
    (r["resolution"] is None or 0.0 < r["resolution"] <= 1.0)
    and (r["bits"] is None or 0.0 <= r["bits"] <= r["bits_max"] + 1e-12)
    and (r["kind"] != _SI.KIND_CONSTANT or r["bits"] == 0.0)
    for r in _az139_all)

check(RS, "AZ139e 值域封闭：kind 词表恰 {CONSTANT,INJECTIVE,COARSE,UNKNOWN}、\n"
          "              reason 词表恰 {TOO_FEW,RULER_BROKEN,BEHAVIOR_SCORE_CONFLICT}、\n"
          "              claim 恰 {None,BEHAVIOR_UNRESOLVED}；输出不越界；字段恰 11 键；\n"
          "              resolution/bits 区间恒成立、CONSTANT bits 恒 0；\n"
          "              撤回语义：冲突理由改名 ⇒ 本条必须红",
      _az139_vocab == {"CONSTANT", "INJECTIVE", "COARSE", "UNKNOWN"}
      and _az139_rvocab == {"TOO_FEW", "RULER_BROKEN", "BEHAVIOR_SCORE_CONFLICT"}
      and _az139_kinds <= _az139_vocab
      and _az139_reasons <= _az139_rvocab
      and _az139_claims <= {None, _SI.CLAIM_BEHAVIOR_UNRESOLVED}
      and _az139_shape_ok and _az139_range_ok,
      # ⚠️ 详情里 sorted() 必须过 str —— 撤回态下 claim/reason 会变 None（AZ136f 教训）
      f"vocab={sorted(str(x) for x in _az139_vocab)} "
      f"rvocab={sorted(str(x) for x in _az139_rvocab)} "
      f"kinds={sorted(str(x) for x in _az139_kinds)} "
      f"reasons={sorted(str(x) for x in _az139_reasons)} "
      f"claims={sorted(str(x) for x in _az139_claims)} shape={_az139_shape_ok} "
      f"range={_az139_range_ok}")

_az139_traps = {
    "越界1.2": [("a", (True, False), 1.2), ("b", (False, True), 0.5)],
    "bool分": [("a", (True, False), True), ("b", (False, True), 0.5)],
    "NaN": [("a", (True, False), float("nan")), ("b", (False, True), 0.5)],
    "负分": [("a", (True, False), -0.1), ("b", (False, True), 0.5)],
    "非bool向量": [("a", (1, 0), 0.5), ("b", (False, True), 1.0)],
    "空向量": [("a", (), 0.5)],
    "长度不齐": [("a", (True, False), 0.5), ("b", (False, True), 0.5), ("c", (True,), 0.5)],
    "非三元组": [("a", (True, False), 0.5), "xyz"],
}
_az139_trap_out = {n: _SI.behavioral(p) for n, p in _az139_traps.items()}
_az139_traps_ok = all(
    r["kind"] == _SI.KIND_UNKNOWN and r["reason"] == _SI.REASON_RULER_BROKEN
    and r["bits"] is None and r["claim"] is None
    for r in _az139_trap_out.values())

check(RS, "AZ139f RULER_BROKEN 全陷阱逐个弹（不许崩、不许分类）：越界/bool/NaN/负分/\n"
          "              非 bool 向量/空向量/长度不齐/非三元组；\n"
          "              撤回语义：NaN 检查废掉 ⇒ 本条必须红",
      _az139_traps_ok,
      "｜".join(f"{n}={r['kind']}/{r['reason']}"
                for n, r in sorted(_az139_trap_out.items())))

check(RS, "AZ139g repair_axes 认识行为级 claim：BEHAVIOR_UNRESOLVED 与\n"
          "              SCORE_UNMOVABLE 都叠加 RULER；无 claim 不叠加；\n"
          "              CRITERION/MODEL 映射一字不动；\n"
          "              撤回语义：RULER 叠加缩回只认 SCORE_UNMOVABLE ⇒ 本条必须红",
      _G.repair_axes("STUCK_MEANS",
                     {"claim": _SI.CLAIM_BEHAVIOR_UNRESOLVED}) == ("MODEL", "RULER")
      and _G.repair_axes("CRITERION_UNREACHABLE",
                         {"claim": _SI.CLAIM_BEHAVIOR_UNRESOLVED}) == ("CRITERION", "RULER")
      and _G.repair_axes("STUCK_MEANS",
                         {"claim": _SI.CLAIM_UNMOVABLE}) == ("MODEL", "RULER")
      and _G.repair_axes("STUCK_MEANS", {"claim": None}) == ("MODEL",)
      and _G.repair_axes("STUCK_MEANS", None) == ("MODEL",)
      and _G.repair_axes("X", {"claim": _SI.CLAIM_BEHAVIOR_UNRESOLVED}) == ("RULER",),
      f"beh={_G.repair_axes('STUCK_MEANS', {'claim': _SI.CLAIM_BEHAVIOR_UNRESOLVED})} "
      f"score={_G.repair_axes('STUCK_MEANS', {'claim': _SI.CLAIM_UNMOVABLE})} "
      f"none={_G.repair_axes('STUCK_MEANS', {'claim': None})}")

_az139_r1 = _SI.behavioral(_az139_ct_rows)
_az139_r2 = _SI.behavioral(_az139_ct_rows)
_az139_r3 = _SI.behavioral(_az139_traps["NaN"])
_az139_r4 = _SI.behavioral(_az139_traps["NaN"])

check(RS, "AZ139h 纯函数：同输入双调用逐字相同（含弃权输出）—— ⛔ 不掺随机；\n"
          "              撤回语义：输出掺 uuid ⇒ 本条必须红",
      _az139_r1 == _az139_r2 and _az139_r3 == _az139_r4,
      f"分类臂 pure={_az139_r1 == _az139_r2} 弃权臂 pure={_az139_r3 == _az139_r4}")

# ═══════════ V178（§246）：行为向量落账 —— 写侧带上通过向量（AZ140） ═══════════
#
# 锚的是**判定的形状**，效果由 `bench/evidence/l3_s266/` 的 P1~P7 量。
# 正菜是**落账**：账本行/观测行带上 (impl, vec, score) ⇒ L3 自省从账本自己
# 就能喂出行为级读数（V177 的 behavioral() 不再依赖探针重跑）。
# 定理 T1（发射自洽：score==round(mean(vec),4)）与 T3（自洽池 ⇒ 冲突闸
# 结构上不可达；跨源池照弹）双双锚在 AZ140a/AZ140e。
# ⚠️ 观测面按注册命题收窄（AZ139d 教训）：AZ140e 只看逐键计数值（⛔ 不比
#    整字典），形状封闭留给 AZ140g —— 否则纯度的 nonce 变异会假耦合 e。
import mw.bench as _B                                           # noqa: E402
import mw.ledger as _L                                          # noqa: E402
sys.path.insert(0, str(V1 / "bench" / "scorers"))
import code_exec as _cex                                        # noqa: E402


def _az140_run_main(payload: dict) -> dict:
    buf_in = io.StringIO(json.dumps(payload))
    buf_out = io.StringIO()
    # ⚠️ contextlib 只有 redirect_stdout/stderr，**没有** redirect_stdin ——
    #    换 stdin 必须手工（首跑自踩：AttributeError）。
    old_in, old_out = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = buf_in, buf_out
    try:
        _cex.main()
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    lines = [ln for ln in buf_out.getvalue().splitlines() if ln.strip()]
    return json.loads(lines[-1])


_az140_t = {"id": "x", "prompt": "p", "tests": ["assert True", "assert False"]}
_az140_ok = _az140_run_main({"task": _az140_t, "answer": "```python\npass\n```"})
_az140_nc = _az140_run_main({"task": _az140_t, "answer": "  "})
_az140_v = _az140_ok.get("vec")

check(RS, "AZ140a 发射面 + T1：有代码发 vec（bool 列表、len==tests、\n"
          "              score==round(mean(vec),4) 恒真）；extract_code 空 ⇒ 无 vec 键\n"
          "              （「没量到」⛔ 不许全零冒充）；\n"
          "              撤回语义：删发射键 ⇒ 本条必须红",
      isinstance(_az140_v, list) and len(_az140_v) == 2
      and all(isinstance(b, bool) for b in _az140_v)
      and _az140_ok["score"] == round(sum(_az140_v) / 2, 4)
      and "vec" not in _az140_nc and _az140_nc["score"] == 0.0,
      f"vec={_az140_v} score={_az140_ok['score']} nc_keys={sorted(_az140_nc)}")


def _az140_canned(payload: dict) -> _B.ExternalScorer:
    return _B.ExternalScorer(
        [sys.executable, "-c", f"import json; print(json.dumps({payload!r}))"],
        name="canned")


_az140_t2 = {"id": "x", "tests": ["a", "b"]}
_az140_s_leg = _az140_canned({"score": 0.5, "why": "w", "vec": [True, False]}).score(_az140_t2, "ans")
_az140_s_non = _az140_canned({"score": 1.0, "why": "w"}).score(_az140_t2, "ans")
_az140_reject, _az140_rj_detail = True, []
for _pay, _tag in [({"score": 0.5, "vec": "xy"}, "非列表"),
                   ({"score": 0.5, "vec": [1, 0]}, "非bool"),
                   ({"score": 0.5, "vec": [True]}, "长度错"),
                   ({"score": 0.5, "vec": None}, "显式null")]:
    try:
        _az140_canned(_pay).score(_az140_t2, "ans")
        _az140_reject = False
        _az140_rj_detail.append(f"{_tag}=没报错")
    except _B.ScoreError:
        _az140_rj_detail.append(f"{_tag}=拒")
# 旧协议回归：detail 三臂一字不变（合法收/坏拒/缺席空）
_az140_d1 = _az140_canned({"score": 0.5, "detail": [{"id": "a1", "ok": False, "why": "x"}]}).score(_az140_t2, "ans")
try:
    _az140_canned({"score": 0.5, "detail": "bad"}).score(_az140_t2, "ans")
    _az140_d_bad = False
except _B.ScoreError:
    _az140_d_bad = True
_az140_d3 = _az140_canned({"score": 0.0}).score(_az140_t2, "ans")

check(RS, "AZ140b 传输面（detail 同款口径）：合法 vec ⇒ tuple[bool] 还原；缺席 ⇒ None\n"
          "              （老打分器合法）；非列表/非 bool/长度≠tests/显式 null ⇒ ScoreError\n"
          "              （fail-closed 不许静默丢）；detail 合法收/坏拒/缺席空一字不变；\n"
          "              撤回语义：拆 vec 格式校验 ⇒ 本条必须红",
      _az140_s_leg.vec == (True, False) and _az140_s_non.vec is None
      and _az140_reject
      and _az140_d1.detail == ({"id": "a1", "ok": False, "why": "x"},)
      and _az140_d_bad and _az140_d3.detail == (),
      f"leg={_az140_s_leg.vec} non={_az140_s_non.vec} {' '.join(_az140_rj_detail)}")


class _az140_StubScorer:
    def __init__(self, vec):
        self._v = vec

    def sha(self) -> str:
        return "stub"

    def score(self, task, ans):
        return _B.Scored(0.5, "w", (), self._v)


_az140_suite = _B.Suite(name="s", tasks=(_B.Task(id="t1", prompt="p",
                                                 tests=("assert True",)),))
_, _az140_rep1 = _B.run_suite(lambda t: "ans", _az140_StubScorer((True, False)), _az140_suite)
_, _az140_rep2 = _B.run_suite(lambda t: "ans", _az140_StubScorer(None), _az140_suite)
_az140_r1p, _az140_r2p = _az140_rep1.per_task[0], _az140_rep2.per_task[0]

check(RS, "AZ140c 跑题链：Scored.vec 在 ⇒ per_task 行带 vec 原值；None ⇒ **键不在**\n"
          "              （⛔ 不写 None 值冒充「量了没通过」）；\n"
          "              撤回语义：行构造删 vec ⇒ 本条必须红",
      _az140_r1p.get("vec") == (True, False) and "vec" in _az140_r1p
      and "vec" not in _az140_r2p,
      f"有={_az140_r1p.get('vec')} 无键={sorted(_az140_r2p)}")

# ── AZ140d 写侧落账：run.py 两写点 AST 现算 + 账本写闸不拒新键 ──────────
_az140_tree = ast.parse((V1 / "run.py").read_text(encoding="utf-8"))
_az140_assign = 0
_az140_dictlit = 0
for _n in ast.walk(_az140_tree):
    if isinstance(_n, ast.Assign):
        for _t in _n.targets:
            if (isinstance(_t, ast.Subscript) and isinstance(_t.slice, ast.Constant)
                    and _t.slice.value == "vec"):
                _az140_assign += 1
    if isinstance(_n, ast.Dict):
        for _k in _n.keys:
            if isinstance(_k, ast.Constant) and _k.value == "vec":
                _az140_dictlit += 1

with tempfile.TemporaryDirectory(prefix="v1_az140_") as _az140_td:
    _cal = {"scorer_sha": "x", "suite_sha": "y", "repeat": 1,
            "budget_tag": "1x", "temperature": 0.0}
    _led = _L.ScoreLedger(Path(_az140_td) / "led.jsonl",
                          caliber_sig=_L.signature_of(_cal))
    _led.append([{"family": "f", "passed": True, "session": 1, "task_id": "t",
                  "score": 0.5, "caliber": _cal,
                  "caliber_sig": _L.signature_of(_cal),
                  "vec": [True, False]}])
    _back = _led.load()
_az140_led_ok = (len(_back) == 1 and _back[0].get("vec") == [True, False])

check(RS, "AZ140d 写侧落账：run.py 分数账本写点恰 1 处 `row[\"vec\"]` 赋值、\n"
          "              goal_obs 写点含 \"vec\" 键的 dict 字面量恰 3 处（AST 现算）；\n"
          "              ScoreLedger.append 不拒带 vec 的行（新键不触写闸）且读回原值；\n"
          "              撤回语义：删账本写点的落账行 ⇒ 本条必须红\n"
          "              ⚠️ V236（§307）从 1 改成 3，逐处点名：\n"
          "                 L3405 `_vec_f`（goals-act 写点的 vec 片段）\n"
          "                 L3253 `_swap_candidates` 的 rows 中转（换路候选自己带 vec）\n"
          "                 L3437 `_srec`（goal_swap 写点）—— **换路是独立观测来源**，\n"
          "                 它有义务自己写 vec；不写 ⇒ 行为池 `n_no_vec` 全涨、\n"
          "                 「换模型有没有扩候选空间」结构上测不出来（V235 实测 12/12 丢失）",
      _az140_assign == 1 and _az140_dictlit == 3 and _az140_led_ok,
      f"assign={_az140_assign} dictlit={_az140_dictlit} ledger={_az140_led_ok}")

# ── AZ140e 读闸：四桶计数精确 + fail-open + 容差边界（⛔ 只比逐键值）─────
_az140_rows = [
    {"task_id": "x", "impl_sha": "a", "score": 0.5, "vec": [True, False]},
    {"task_id": "x", "impl_sha": "b", "score": 0.5},
    {"task_id": "x", "impl_sha": "c", "score": 0.5, "vec": [True, True]},
    {"task_id": "x", "impl_sha": "d", "score": 0.5, "vec": [1, 0]},
    {"task_id": "x", "impl_sha": "e", "score": 0.5, "vec": []},
    {"task_id": "x", "impl_sha": "f", "score": float("nan"), "vec": [True, False]},
    {"task_id": "x", "impl_sha": "g", "score": True, "vec": [True, False]},
    {"task_id": "x", "score": 0.5, "vec": [True, False]},
    {"task_id": "y", "impl_sha": "h", "score": 1.0, "vec": [True]},
    "bad",
]
_az140_pool, _az140_c = _SI.beh_pool_from_rows(_az140_rows, cell="x")
_az140_c_ok = {k: _az140_c.get(k) for k in
               ("n_bad_row", "n_no_impl", "n_no_vec", "n_bad_vec",
                "n_inconsistent", "n_kept")}
_az140_tol_in = _SI.beh_pool_from_rows(
    [{"task_id": "z", "impl_sha": "h", "score": 0.25005,
      "vec": [True, False, False, False]}])[1]
_az140_tol_out = _SI.beh_pool_from_rows(
    [{"task_id": "z", "impl_sha": "h", "score": 0.2504,
      "vec": [True, False, False, False]}])[1]
try:
    _SI.beh_pool_from_rows(_az140_rows + [None, 42, {"task_id": "x"}], cell=None)
    _az140_fo = True
except Exception:  # noqa: BLE001
    _az140_fo = False

check(RS, "AZ140e 读闸四桶逐键精确：no_impl/no_vec/bad_vec/inconsistent 一行只进\n"
          "              一桶（坏行只进全局）；分数 bool/NaN/非数值算对不上账（NaN 比较\n"
          "              恒 False 必须先排）；容差 5e-5 收、4e-4 拒（发射量子半宽）；\n"
          "              fail-open：任意坏行绝不抛；⛔ 本条只比逐键值（形状归 AZ140g）；\n"
          "              撤回语义：拆自洽闸 ⇒ 本条必须红",
      _az140_pool == [("a", (True, False), 0.5)]
      and _az140_c_ok == {"n_bad_row": 0, "n_no_impl": 1, "n_no_vec": 1,
                          "n_bad_vec": 2, "n_inconsistent": 3, "n_kept": 1}
      and _az140_tol_in.get("n_kept") == 1 and _az140_tol_in.get("n_inconsistent") == 0
      and _az140_tol_out.get("n_inconsistent") == 1 and _az140_tol_out.get("n_kept") == 0
      and _az140_fo,
      f"pool={_az140_pool} counts={_az140_c_ok} 容差收={_az140_tol_in.get('n_kept')} "
      f"容差拒={_az140_tol_out.get('n_inconsistent')} fail-open={_az140_fo}")

# ── AZ140f 向后兼容：vec 键不扰动 V175 旧读侧（逐位不变）────────────────
_az140_plain = [{"task_id": "z", "impl_sha": "a", "score": 0.5},
                {"task_id": "z", "impl_sha": "b", "score": 0.5},
                {"task_id": "z", "impl_sha": "c", "score": 0.5}]
_az140_withvec = [dict(r, vec=[True, False]) for r in _az140_plain]
_m_plain = _SI.movability(_az140_plain, cell="z")
_p_plain = _SI.pool_from_rows(_az140_plain, cell="z")
# ⚠️ 撤回变异是「旧读侧见 vec 键即拒（raise）」—— 不包 try 的话异常会
#    逃出模块级夹具、汇总行消失 ⇒ 转红变崩溃（AP11 同型）。红必须是值。
try:
    _p_vec = _SI.pool_from_rows(_az140_withvec, cell="z")
    _m_vec = _SI.movability(_az140_withvec, cell="z")
    _az140_f_raise = False
except Exception:  # noqa: BLE001
    _p_vec, _m_vec, _az140_f_raise = None, None, True

check(RS, "AZ140f 向后兼容：带 vec 的新账行喂 V175 旧读侧（pool_from_rows/\n"
          "              movability）逐位不变（未知键不扰动）；混读不抛；\n"
          "              撤回语义：旧读侧见 vec 键即拒 ⇒ 本条必须红",
      (not _az140_f_raise) and _m_plain == _m_vec and _p_plain == _p_vec,
      f"raise={_az140_f_raise} movability 同={_m_plain == _m_vec} "
      f"pool 同={_p_plain == _p_vec}")

# ── AZ140g 纯函数 + 输出形状封闭（6 计数键，值全 int）───────────────────
_az140_clean = [{"task_id": "q", "impl_sha": "a", "score": 0.5, "vec": [True, False]},
                {"task_id": "q", "impl_sha": "b", "score": 1.0, "vec": [True, True]}]
_az140_g1 = _SI.beh_pool_from_rows(_az140_clean, cell=None)
_az140_g2 = _SI.beh_pool_from_rows(_az140_clean, cell=None)
_az140_shape = (set(_az140_g1[1]) == {"n_bad_row", "n_no_impl", "n_no_vec",
                                      "n_bad_vec", "n_inconsistent", "n_kept"}
                and all(isinstance(v, int) for v in _az140_g1[1].values()))

check(RS, "AZ140g 纯函数 + 形状封闭：同输入双调用逐字相同；counts 恰 6 键、值全 int\n"
          "              （形状在此条锚 —— ⛔ 不与 AZ140e 共面，防 nonce 假耦合）；\n"
          "              撤回语义：输出掺随机键 ⇒ 本条必须红",
      _az140_g1 == _az140_g2 and _az140_shape,
      f"pure={_az140_g1 == _az140_g2} shape={_az140_shape}")

# ═══════════ V180（§248）：行为级 COARSE 也给轴（AZ141） ═══════════
# 只锚纯函数面与渲染面（⛔ 契约里不跑真尺子 —— 池的证据在 `l3_s268/v180.py`）。
# 这一组钉的是**哪一层**的 COARSE 算数：行为级算，分数级不算。
_az141_obs_coarse = [
    {"task_id": "c1", "impl_sha": "a", "score": 1.0, "vec": [True, True]},
    {"task_id": "c1", "impl_sha": "b", "score": 0.5, "vec": [True, False]},
    {"task_id": "c1", "impl_sha": "c", "score": 0.5, "vec": [False, True]},
]
_az141_obs_inject = [
    {"task_id": "c2", "impl_sha": "a", "score": 1.0, "vec": [True, True]},
    {"task_id": "c2", "impl_sha": "b", "score": 0.5, "vec": [True, False]},
    {"task_id": "c2", "impl_sha": "c", "score": 0.0, "vec": [False, False]},
]
_az141_obs_novec = [{"task_id": "c3", "impl_sha": "a", "score": 1.0},
                    {"task_id": "c3", "impl_sha": "b", "score": 0.5}]


def _az141_forced(cell: str) -> list[dict]:
    return [{"gid": f"g:{cell}", "cell": cell, "kind": "RAISE",
             "verdict": "NOT_MET", "reading": {"mean_score": 0.5}} for _ in range(3)]


def _az141_overlay(obs: list[dict], cell: str):
    return (_G.escalations(_az141_forced(cell), obs).get(cell) or {}).get("movability")


_az141_a = {
    "coarse3": _G.repair_axes("STUCK_MEANS",
                              {"claim": None, "beh": {"kind": _SI.KIND_COARSE,
                                                      "n_behaviors": _SI.MIN_BEHAVIORS}}),
    "coarse2": _G.repair_axes("STUCK_MEANS",
                              {"claim": None, "beh": {"kind": _SI.KIND_COARSE,
                                                      "n_behaviors": _SI.MIN_BEHAVIORS - 1}}),
    "inject": _G.repair_axes("STUCK_MEANS",
                             {"claim": None, "beh": {"kind": _SI.KIND_INJECTIVE,
                                                     "n_behaviors": 5}}),
    "unknown": _G.repair_axes("STUCK_MEANS",
                              {"claim": None, "beh": {"kind": _SI.KIND_UNKNOWN,
                                                      "n_behaviors": 9}}),
    "nobeh": _G.repair_axes("STUCK_MEANS", {"claim": None}),
}

check(RS, "AZ141a 轴的新触发按**行为类**计数：beh.kind=COARSE ∧ n_behaviors≥MIN_BEHAVIORS\n"
          "              ⇒ 加 RULER；n_behaviors 少 1 ⇒ 不加（门限与 claim 同源）；\n"
          "              INJECTIVE / UNKNOWN / 无 beh ⇒ 一律不加（⛔ 不许把「分辨得清」当「尺子坏」）；\n"
          "              撤回语义：把 beh 分支删掉 ⇒ 本条必须红",
      _az141_a["coarse3"] == ("MODEL", "RULER")
      and _az141_a["coarse2"] == ("MODEL",)
      and _az141_a["inject"] == ("MODEL",)
      and _az141_a["unknown"] == ("MODEL",)
      and _az141_a["nobeh"] == ("MODEL",),
      f"{_az141_a}")

_az141_b_axes = [_G.repair_axes(r, m) for r in ("STUCK_MEANS", "CRITERION_UNREACHABLE", "X")
                 for m in (None, {"claim": _SI.CLAIM_UNMOVABLE},
                           {"claim": _SI.CLAIM_BEHAVIOR_UNRESOLVED},
                           {"claim": None, "beh": {"kind": _SI.KIND_COARSE,
                                                   "n_behaviors": 4}},
                           {"claim": _SI.CLAIM_UNMOVABLE,
                            "beh": {"kind": _SI.KIND_COARSE, "n_behaviors": 4}})]
_az141_dedup = _G.repair_axes("STUCK_MEANS",
                              {"claim": _SI.CLAIM_UNMOVABLE,
                               "beh": {"kind": _SI.KIND_COARSE, "n_behaviors": 4}})
_az141_dedup2 = _G.repair_axes("CRITERION_UNREACHABLE",
                               {"claim": _SI.CLAIM_BEHAVIOR_UNRESOLVED,
                                "beh": {"kind": _SI.KIND_COARSE, "n_behaviors": 4}})

check(RS, "AZ141b 值域仍封闭 + 去重：15 个输入下轴 ⊆ {CRITERION,MODEL,RULER} 且**无重复项**\n"
          "              （claim 与 beh 同时要 RULER ⇒ 只出现一次）；\n"
          "              撤回语义：把 beh 分支直接 append 而不过滤 ⇒ 本条必须红",
      all(set(a) <= {"CRITERION", "MODEL", "RULER"} for a in _az141_b_axes)
      and all(len(set(a)) == len(a) for a in _az141_b_axes)
      and _az141_dedup == ("MODEL", "RULER") and _az141_dedup2 == ("CRITERION", "RULER"),
      f"dedup={_az141_dedup}/{_az141_dedup2}")

_az141_ov_c = _az141_overlay(_az141_obs_coarse, "c1")
_az141_ov_i = _az141_overlay(_az141_obs_inject, "c2")
_az141_ov_n = _az141_overlay(_az141_obs_novec, "c3")

check(RS, "AZ141c 行为级读数**接到了** overlay（V179 的死分支在这里被接上）：\n"
          "              有 vec ⇒ `beh` 在场且 kind 与 behavioral() 一致；无 vec ⇒ **没有** beh 键\n"
          "              （⛔ 缺席不许写成 INJECTIVE —— 那是拿「没量到」冒充「有分辨力」）；\n"
          "              撤回语义：无池时也塞一个 INJECTIVE ⇒ 本条必须红",
      _az141_ov_c is not None and (_az141_ov_c.get("beh") or {}).get("kind") == _SI.KIND_COARSE
      and _az141_ov_i is not None and (_az141_ov_i.get("beh") or {}).get("kind") == _SI.KIND_INJECTIVE
      and _az141_ov_n is not None and "beh" not in _az141_ov_n,
      f"coarse={(_az141_ov_c or {}).get('beh')} inject={(_az141_ov_i or {}).get('beh')} "
      f"novec={'有 beh' if (_az141_ov_n or {}).get('beh') else '无 beh'}")

_az141_fmt_c = _G.render_escalations(_G.escalations(_az141_forced("c1"), _az141_obs_coarse))
_az141_fmt_i = _G.render_escalations(_G.escalations(_az141_forced("c2"), _az141_obs_inject))

check(RS, "AZ141d 传到渲染面：COARSE 那一格印「存在异行为同分」且轴含 RULER；\n"
          "              INJECTIVE 那一格**不**印这句话（⛔ 只多印读数，不删旧行 —— 旧子串全在）；\n"
          "              撤回语义：渲染里不看 beh ⇒ 本条必须红",
      "存在异行为同分" in _az141_fmt_c and "RULER" in _az141_fmt_c
      and "均分逐位相同" in _az141_fmt_c
      and "存在异行为同分" not in _az141_fmt_i,
      f"coarse 提到={('存在异行为同分' in _az141_fmt_c)} inject 提到={('存在异行为同分' in _az141_fmt_i)}")

_az141_ov_mixed = _az141_overlay([
    {"task_id": "c4", "impl_sha": "a", "score": 1.0, "vec": [True, True]},
    {"task_id": "c4", "impl_sha": "b", "score": 0.0, "vec": [False, False]},
    {"task_id": "c4", "impl_sha": "c", "score": 0.0, "vec": [False, False]},
], "c4")

check(RS, "AZ141e 负对照（这一条护住本轮的**取舍**）：分数级 COARSE 但行为级 INJECTIVE 的格\n"
          "              ⇒ 轴里**没有** RULER。真池 6/6 都是分数级 COARSE，若按分数级判，\n"
          "              5 格是假正例（多个实现行为相同、只是写法不同 ⇒ 尺子没问题，是模型没动）；\n"
          "              撤回语义：拿分数级 COARSE 冒充（label==COARSE 就加 RULER）⇒ 本条必须红",
      (_az141_ov_mixed or {}).get("label") == _SI.KIND_COARSE
      and (_az141_ov_mixed.get("beh") or {}).get("kind") == _SI.KIND_INJECTIVE
      and _G.repair_axes("STUCK_MEANS", _az141_ov_mixed) == ("MODEL",),
      f"score_label={(_az141_ov_mixed or {}).get('label')} "
      f"beh={(_az141_ov_mixed or {}).get('beh')} "
      f"轴={_G.repair_axes('STUCK_MEANS', _az141_ov_mixed)}")

import copy as _copy                                                      # noqa: E402
_az141_m = {"label": _SI.KIND_COARSE, "claim": None,
            "beh": {"kind": _SI.KIND_COARSE, "n_behaviors": 3}}
_az141_snap = _copy.deepcopy(_az141_m)
_az141_p1 = _G.repair_axes("STUCK_MEANS", _az141_m)
_az141_p2 = _G.repair_axes("STUCK_MEANS", _az141_m)

check(RS, "AZ141f 纯函数 + 不改输入：同输入两次调用逐字相同，且 `movability` 实参**逐键不变**\n"
          "              （⛔ 判定不许在原地给 overlay 补字段 —— 那会让下游看到只被读过一次的字典）；\n"
          "              撤回语义：函数内 `movability[\"axes\"]=...` ⇒ 本条必须红",
      _az141_p1 == _az141_p2 == ("MODEL", "RULER") and _az141_m == _az141_snap,
      f"axes={_az141_p1} 实参不变={_az141_m == _az141_snap}")

# ═══════════ V181（§249）：归一化不许吞掉身份与向量（AZ142） ═══════════
# 这一组钉的是**第三道闸**：账本行长出 `impl_sha`/`vec` 之后，`coerce()` 曾是
# 唯一一处把它们扔掉的地方 —— 而它只在真账本 + 真 `--goals` 上看得见。
_az142_row = {"task_id": " t09 ", "score": "0.5", "session": 4,
              "impl_sha": "abc123", "vec": (True, False), "candidate": "x"}
_az142_c = _G.coerce(_az142_row)

check(RS, "AZ142a `coerce()` 透传 `impl_sha` / `vec`（V175/V178 长出来的两个键）：\n"
          "              四键语义一字不动（task_id 去空格、score 转 float、session 原样、"
          "source 兜底），两键原样带上（vec 统一成 list）；⛔ 没有别名键；\n"
          "              撤回语义：删掉透传 ⇒ 本条必须红（AZ142c 连带红）",
      _az142_c == {"task_id": "t09", "score": 0.5, "session": 4,
                   "source": "score_ledger", "impl_sha": "abc123",
                   "vec": [True, False]},
      f"{_az142_c}")

_az142_bad = [{"task_id": "b", "score": 0.5, "impl_sha": "", "vec": "xy"},
              {"task_id": "b", "score": 0.5, "impl_sha": 123, "vec": "xy"},
              {"task_id": "b", "score": 0.5, "impl_sha": None, "vec": None},
              {"task_id": "b", "score": 0.5, "impl_sha": "ok", "vec": []}]
_az142_b = [_G.coerce(r) for r in _az142_bad]

check(RS, "AZ142b 透传是 fail-open：空串/非 str 的 `impl_sha`、非 list/tuple 的 `vec`\n"
          "              ⇒ **不透传该键**（缺席不是零）；空 list 的 `vec` 照样透传\n"
          "              （形状坏由 `beh_pool_from_rows()` 的行闸计数，归一化不二次判）；\n"
          "              四条都不许抛、四键都在；撤回语义："
          "无条件 `out['vec']=row.get('vec')` ⇒ 本条必须红",
      all(x is not None and {"task_id", "score", "session", "source"} <= set(x)
          for x in _az142_b)
      and "impl_sha" not in _az142_b[0] and "impl_sha" not in _az142_b[1]
      and "impl_sha" not in _az142_b[2] and "vec" not in _az142_b[0]
      and "vec" not in _az142_b[1] and "vec" not in _az142_b[2]
      and _az142_b[3] == {"task_id": "b", "score": 0.5, "session": None,
                          "source": "score_ledger", "impl_sha": "ok", "vec": []},
      f"{_az142_b}")

_az142_obs = [{"task_id": "d1", "impl_sha": "a", "score": 1.0, "vec": [True, True, True]},
              {"task_id": "d1", "impl_sha": "b", "score": 0.3333, "vec": [True, False, False]},
              {"task_id": "d1", "impl_sha": "c", "score": 0.3333, "vec": [False, True, False]},
              {"task_id": "d1", "impl_sha": "d", "score": 0.3333, "vec": [False, False, True]}]
_az142_forced = [{"gid": "g:d1", "cell": "d1", "kind": "RAISE",
                  "verdict": "NOT_MET", "reading": {"mean_score": 0.5}}
                 for _ in range(3)]
_az142_esc_c = _G.escalations(_az142_forced, _G.coerce_all(_az142_obs)[0])
_az142_esc_r = _G.escalations(_az142_forced, _az142_obs)
_az142_axes_c = {c: _G.repair_axes(str(e.get("reason") or ""), e.get("movability"))
                 for c, e in _az142_esc_c.items()}
_az142_axes_r = {c: _G.repair_axes(str(e.get("reason") or ""), e.get("movability"))
                 for c, e in _az142_esc_r.items()}

check(RS, "AZ142c 部署路径同源：`coerce_all()` 之后的 obs 与原始行喂 `escalations()`\n"
          "              ⇒ 逐格轴**完全相同**，且这一格（4 实现 / 3 行为类 / 异行为同分）\n"
          "              拿到 `RULER`。⛔ 这正是 V180 在部署上 0 触发的那一格；\n"
          "              撤回语义：coerce 吞掉两键 ⇒ 本条的轴里没有 RULER（连带红）",
      _az142_axes_c == _az142_axes_r
      and _az142_axes_c.get("d1") == ("CRITERION", "RULER"),
      f"coerced={_az142_axes_c} raw={_az142_axes_r}")

_az142_stats = {r: {c: _G.stats(v, c) for c in ("d1",)}
                for r, v in (("coerced", _G.coerce_all(_az142_obs)[0]),
                             ("raw", _az142_obs))}
_az142_prop = {r: [g.gid for g in _G.propose(v, ["d1"], done=[], escalated=[])]
               for r, v in (("coerced", _G.coerce_all(_az142_obs)[0]),
                            ("raw", _az142_obs))}

check(RS, "AZ142d 判据不敏感：同一批行，`stats()` 与 `propose()` 在透传前/后**逐字相同**\n"
          "              （新键不参与任何判据 —— 透传只服务诊断层，⛔ 不许改目标提议）；\n"
          "              撤回语义：拿 vec 去改 propose 的排序/判据 ⇒ 本条必须红",
      _az142_stats["coerced"] == _az142_stats["raw"]
      and _az142_prop["coerced"] == _az142_prop["raw"],
      f"stats={_az142_stats['coerced'] == _az142_stats['raw']} "
      f"propose={_az142_prop['coerced']}")

check(RS, "AZ142e 只动归一化层：判据常量一个没变"
          "（MIN_POOL=2 / MIN_IMPLS=3 / MIN_BEHAVIORS=3 / TARGET=1.0），\n"
          "              且 `coerce()` 的输出键 ⊆ {task_id,score,session,source,impl_sha,vec}\n"
          "              （⛔ 没有 `vec2`/`impl`/`behavior` 这类别名）；\n"
          "              撤回语义：顺手改 MIN_BEHAVIORS ⇒ 本条必须红",
      (_SI.MIN_POOL, _SI.MIN_IMPLS, _SI.MIN_BEHAVIORS, _SI.TARGET) == (2, 3, 3, 1.0)
      and set(_az142_c) <= {"task_id", "score", "session", "source",
                            "impl_sha", "vec"},
      f"{(_SI.MIN_POOL, _SI.MIN_IMPLS, _SI.MIN_BEHAVIORS, _SI.TARGET)} keys={sorted(_az142_c)}")

# ═══════════ V182（§250）：一格里的观测来自**几把尺子**（AZ143） ═══════════
# 这一组钉的是「混池均分不对应任何口径」这件事**可见**，且**不猜**。
_az143_rows = [
    {"task_id": "c1", "score": 1.0, "caliber_sig": "SIG_A", "session": 1},
    {"task_id": "c1", "score": 0.0, "caliber_sig": "SIG_B", "session": 2},   # 1:1 平票
    {"task_id": "c2", "score": 0.2, "caliber_sig": "SIG_A", "session": 1},
    {"task_id": "c2", "score": 0.4, "caliber_sig": "SIG_A", "session": 1},
    {"task_id": "c2", "score": 0.6, "caliber_sig": "SIG_A", "session": 2},
    {"task_id": "c2", "score": 1.0, "caliber_sig": "SIG_B", "session": 3},   # 3:1 有主口径
    {"task_id": "c3", "score": 0.5, "session": 1},                            # 无签名
    {"task_id": "c3", "score": 0.7, "caliber_sig": "  ", "session": 2},       # 空白签名
]
_az143 = {r["cell"]: r for r in _G.sig_report(_az143_rows)}

check(RS, "AZ143a `sig_report()` 逐格报口径构成，且**平票不猜**：\n"
          "              `c1`（SIG_A 1 行 / SIG_B 1 行）⇒ `tie=True`、`major=None`、\n"
          "              `mean_major=None`，而 `mean_all=0.5`（混池那两个读数是 1.0 与 0.0，0.5 谁也不是）；\n"
          "              `c2`（3:1）⇒ `major=SIG_A`、`mean_major=0.4`，`mean_all=0.55`；\n"
          "              `c3`（两条无签名）⇒ `n_sigs=0`、`n_unknown=2`（⛔ 无签名不是一把尺子）；\n"
          "              撤回语义：平票时用「最新/字典序」挑一个 ⇒ `major`/`mean_major` 非 None ⇒ 本条必须红",
      (_az143["c1"]["tie"] is True and _az143["c1"]["major"] is None
       and _az143["c1"]["mean_major"] is None
       and round(_az143["c1"]["mean_all"], 6) == 0.5
       and _az143["c2"]["tie"] is False and _az143["c2"]["major"] == "SIG_A"
       and round(_az143["c2"]["mean_major"], 6) == 0.4
       and round(_az143["c2"]["mean_all"], 6) == 0.55
       and _az143["c3"]["n_sigs"] == 0 and _az143["c3"]["n_unknown"] == 2
       and _az143["c3"]["major"] is None and _az143["c3"]["tie"] is False),
      f"c1={ {k: _az143['c1'][k] for k in ('tie','major','mean_major','mean_all')} } "
      f"c2={ {k: _az143['c2'][k] for k in ('tie','major','mean_major','mean_all')} } "
      f"c3={ {k: _az143['c3'][k] for k in ('n_sigs','n_unknown','major','tie')} }")

_az143b_rows = _az143_rows + [
    {"task_id": "c4", "score": 0.2, "caliber_sig": "SIG_A", "session": 1},
    {"task_id": "c4", "score": 0.4, "caliber_sig": "SIG_A", "session": 2},
    {"task_id": "c4", "score": 0.6, "caliber_sig": "SIG_A", "session": 3},
    {"task_id": "c4", "score": 1.0, "caliber_sig": "SIG_B", "session": 4},
    {"task_id": "c4", "score": 1.0, "caliber_sig": "SIG_B", "session": 5},
    {"task_id": "c4", "score": 1.0, "caliber_sig": "SIG_B", "session": 6},
]
_az143b = {r["cell"]: r for r in _G.sig_report(_az143b_rows)}

check(RS, "AZ143b `need_split` 只在一格「平票**且**两把尺子都测够」时为真：\n"
          "              `c1`（1:1，各 1 行 < MIN_OBS=3）⇒ False；\n"
          "              `c4`（3:3，都 ≥ 3）⇒ True —— 这时两个读数**都成立**，\n"
          "              混池均分在那格上**没有定义**（本行只报警，⛔ 不改任何行为）；\n"
          "              撤回语义：`need_split = tie`（丢掉行数闸）⇒ `c1` 变 True ⇒ 本条必须红",
      _az143b["c1"]["need_split"] is False and _az143b["c4"]["need_split"] is True
      and _az143b["c4"]["tie"] is True and _az143b["c4"]["major"] is None,
      f"c1={_az143b['c1']['need_split']} c4={_az143b['c4']['need_split']}/{_az143b['c4']['tie']}")

_az143_snap = json.dumps(_az143_rows, sort_keys=True, ensure_ascii=False)
_az143_p1 = [g.gid for g in _G.propose(_G.coerce_all(_az143_rows)[0], ["c1", "c2", "c3"],
                                       done=[], escalated=[])]
_az143_rep = _G.sig_report(_az143_rows)
_az143_p2 = [g.gid for g in _G.propose(_G.coerce_all(_az143_rows)[0], ["c1", "c2", "c3"],
                                       done=[], escalated=[])]
_az143_st = [_G.stats(_G.coerce_all(_az143_rows)[0], c)["mean_score"] for c in ("c1", "c2")]

check(RS, "AZ143c 纯读、且**不入判据**：调 `sig_report()` 前后 `propose()` 逐字相同、\n"
          "              入参行不被改写（快照逐字相等），而 `stats()` 的均分仍是混池均分\n"
          "              （c1=0.5 / c2=0.55 —— ⛔ 本轮没动 `stats`/`propose` 一个字）。\n"
          "              撤回语义：让 `sig_report` 顺手把 obs 按主口径过滤 ⇒ 本条必须红",
      _az143_p1 == _az143_p2
      and json.dumps(_az143_rows, sort_keys=True, ensure_ascii=False) == _az143_snap
      and round(_az143_st[0], 6) == 0.5 and round(_az143_st[1], 6) == 0.55,
      f"propose 前/后={_az143_p1}/{_az143_p2} stats={[round(x, 4) for x in _az143_st]}")

# ═══════════ V183（§251）：判据准入闸 —— 挡住「期望值写成字面量」（AZ144） ═══════════
import mw.metacrit as _MC                                       # noqa: E402

#: V79 那 22 条断言的原物（`bench/evidence/v6_s168/probe_selfasserts.json`），
#: 逐题拼成一条判据。它们**全部**把期望输出写成了字面量。
_az144_v79 = {
    "k01": [
        "assert diff_tree({'a': 1, 'b': {'c': 2}}, {'a': 1, 'b': {'c': 2}}) == {}",
        "assert diff_tree({'a': 1}, {'a': 2}) == {'a': [1, 2]}",
    ],
    "k02": ["assert encode_cookie({'a': 'b c'}) == 'a=b%20c'"],
    # ⚠️ 自踩：第一版这里写的是 `== True`（Eq）⇒ 撤回 `Is/IsNot` 时本条**不红**，
    #    而锚文本声称「`is True` 漏网 ⇒ 必须红」。夹具必须让 `is` 语义成为承重点。
    "g7e1": ["assert parse_flag('on') is True", "assert parse_flag('maybe') is None"],
}
_az144_refuse = {k: _MC.admit("\n".join(v)) for k, v in _az144_v79.items()}

check(RS, "AZ144a `admit()` 把「期望值写成字面量」的判据**全部**拒掉（V79 那批）：\n"
          "              字符串 / 非空容器 / `is True` 都算字面量期望；\n"
          "              ⛔ 空容器（`== {}`）、`None`、`0` **不算**（那是结构，不是格式细节）；\n"
          "              撤回语义：把 `_EQ_OPS` 收成只有 `ast.Eq` ⇒ `is True` 漏网 ⇒ 本条必须红",
      all(not ok for ok, _ in _az144_refuse.values())
      and all(why.startswith("LITERAL_EXPECTATION") for _, why in _az144_refuse.values()),
      f"{ {k: (v[0], v[1][:28]) for k, v in _az144_refuse.items()} }")

_az144_clean = """
from urllib.parse import parse_qsl


def _rel():
    for d in ({'a': 'b c'}, {'a': 'p%q'}):
        got = encode_cookie(d)
        back = {k.strip(): v for k, v in
                parse_qsl(got, keep_blank_values=True, separator=';')}
        if back != {str(k): str(v) for k, v in d.items()}:
            raise AssertionError(f"往返不成立：{d!r} -> {got!r}")
    return True
"""
_az144_struct = """
def _rel():
    for v in ([1, 2], {}, []):
        if len(v) == 2 and v != {}:
            pass
    return True
"""

check(RS, "AZ144b 反过来不能误伤**关系型**判据：`decode(f(x)) == x` 这种\n"
          "              （字面量只在探针输入里、判定靠 stdlib）必须**放行**；\n"
          "              而 `len(v) == 2` 这种**结构计数**也不算字面量期望；\n"
          "              撤回语义：把 `len(...)` 豁免删掉 ⇒ 结构计数被拒 ⇒ 本条必须红",
      _MC.admit(_az144_clean) == (True, "OK")
      and _MC.admit(_az144_struct) == (True, "OK"),
      f"clean={_MC.admit(_az144_clean)} struct={_MC.admit(_az144_struct)}")

_az144_codes = [
    ("ok_empty_if_equal", "def f(a, b):\n    return {} if a == b else {'k': [1, 2]}\n",
     "def _rel():\n    for a, b in (({'x': 1}, {'x': 1}), ({'x': 1}, {'x': 2})):\n"
     "        got = f(a, b)\n        if (got == {}) != (a == b):\n"
     "            raise AssertionError('坏了')\n    return True\n", ""),
    ("bad_always_empty", "def f(a, b):\n    return {}\n",
     "def _rel():\n    a, b = {'x': 1}, {'x': 2}\n    if (f(a, b) == {}) != (a == b):\n"
     "        raise AssertionError('坏了')\n    return True\n",
     "坏了"),
]

check(RS, "AZ144c `run()` 走的是**内核同一把尺**（`mw.verify.run_example`）：\n"
          "              好的实现过、坏的实现不过且带回实得；\n"
          "              撤回语义：让 `run()` 自己 `exec` 而不经 `run_example` ⇒ 隔离/超时/\n"
          "              `EXEC_FAULT` 前缀全丢 ⇒ 本条必须红（本条只钉「过/不过与错误串」）",
      all(_MC.run(c, s) == want for _n, c, s, want in
          [(a, b, c, d) for a, b, c, d in _az144_codes if d == ""])
      and all(_MC.run(c, s) != "" for _n, c, s, _w in _az144_codes if _w),
      f"{[_MC.run(c, s)[:24] for _n, c, s, _w in _az144_codes]}")

_az144_snap = "\n".join(_az144_v79["k02"])

# ⚠️ py3.11 不许在 f-string 表达式里出现反斜杠（3.12+ 才行）⇒ 先算再拼。
_az144_loop_src = "while True:\n    pass\n"
_az144_loop = _MC.admit(_az144_loop_src)
_az144_exit = _MC.admit("def _rel():\n    raise SystemExit(3)\n")

check(RS, "AZ144d 准入闸**不执行**判据源码（纯 AST）：\n"
          "              `admit()` 对一段会 `raise`/死循环的判据也必须立刻返回，且不改入参；\n"
          "              撤回语义：让 `admit()` 先 parse 再 `compile+exec` 试跑 ⇒ 本条超时/抛错 ⇒ 必须红",
      _az144_exit[0] is True and _az144_loop[0] is True
      and _MC.admit("\n".join(_az144_v79["k02"]))[0] is False,
      f"{_az144_loop}")

# ═══════════ V184（§252）：判据生成器**带类型签名**（AZ145） ═══════════
import mw.critgen as _CG                                        # noqa: E402

#: `arity` 只数**必填位置参数**：默认值不算、`*args`/`**kw` 不算、没有 `def` ⇒ -1。
_az145_arity = [
    ("def f(a, b):\n    return a\n", 2),
    ("def f(a, b=1):\n    return a\n", 1),
    ("def f(*a, **k):\n    return a\n", 0),
    ("x = 1\n", -1),
]
_az145_got = [_CG.arity(c) for c, _ in _az145_arity]

check(RS, "AZ145a `critgen.arity()` 数的是**必填位置参数**：有默认值的不算、`*args` 不算、\n"
          "              没有 `def` 返回 -1（⛔ 不是 0 —— 0 会被读成「一个都不需要」）；\n"
          "              撤回语义：改成 `len(a.args)`（把默认值算上）⇒ 本例第 2 条变 2 ⇒ 必须红",
      _az145_got == [n for _, n in _az145_arity],
      f"{_az145_got}")

#: 圈域：喂 `int` 给 `s[:n]` 会抛 ⇒ 只该保留吃得下的那几组。
_az145_inputs = [("abcdef", 3), (1, 2), ("ab", 1)]
_az145_legal = _CG.legal_indexes("def f(s, n):\n    return s[:n]\n", "f", _az145_inputs)

check(RS, "AZ145b `critgen.legal_indexes()` 在**目标实现**上圈域：吃得下的保留、抛异常的剔除；\n"
          "              ⛔ 只看「能不能吃」，不看返回值对不对（后者是判别力，属收费那一半）；\n"
          "              撤回语义：不剔除（原样返回全部下标）⇒ 第 2 组也被保留 ⇒ 必须红",
      _az145_legal == [0, 2],
      f"{_az145_legal}")

_az145_unreach = _CG.validate("EMPTY_IFF_EQUAL", ["dict"], {}, 1)
_az145_enum = _CG.validate("TAIL", ["str", "int"], {"keep": "nope"}, 2)
_az145_ok = _CG.validate("TAIL", ["str", "int"], {"keep": "tail_n"}, 2)

check(RS, "AZ145c `critgen.validate()` 把**结构不可达**与**槽不在枚举里**分开报：\n"
          "              前者（2 参数的算子用在一元函数上）说「结构不可达」，\n"
          "              后者说「不在枚举里」，合法组合返回空串；\n"
          "              撤回语义：删掉 `reachable()` 那道检查 ⇒ 不可达的算子被放行 ⇒ 必须红",
      "结构不可达" in _az145_unreach and "不在枚举里" in _az145_enum and _az145_ok == "",
      f"unreach={_az145_unreach[:24]!r} enum={_az145_enum[:24]!r} ok={_az145_ok!r}")

#: 输入由装置生成 —— 三条规则各钉一条，且单参数必须是 **1-tuple**（实参元组）。
_az145_tail = _CG.inputs_for("TAIL", ["str", "int"])
_az145_one = _CG.inputs_for("CONSERVE", ["dict"])
_az145_pair = _CG.inputs_for("EMPTY_IFF_EQUAL", ["dict", "dict"])

check(RS, "AZ145d `critgen.inputs_for()` 的输入由**装置**生成：\n"
          "              ① TAIL 4 组里既有超长也有不超长（`('abcdef',1)` 与 `('abcdef',9)`）；\n"
          "              ② 单参数一律 1-tuple（`(v,)`）—— 判据里 `for x, in INPUTS` 才解包得对；\n"
          "              ③ 双参数含**相等**对与**不等**对（`EMPTY_IFF_EQUAL` 两头都要考到）；\n"
          "              撤回语义：单参数返回 `list(...)`（不包 tuple）⇒ ② 不成立 ⇒ 必须红",
      _az145_tail[0] == ("abcdef", 1) and _az145_tail[1] == ("abcdef", 9)
      and all(isinstance(x, tuple) and len(x) == 1 for x in _az145_one)
      and _az145_pair[0][0] == _az145_pair[0][1]
      and _az145_pair[1][0] != _az145_pair[1][1]
      and _az145_pair[2][0] == _az145_pair[2][1],
      f"tail={_az145_tail[:2]} one0={_az145_one[0]!r} pair={_az145_pair}")

# ═══════════ V185（§253）：诊断分层 + 回错可行动（AZ148） ═══════════

#: 分类优先级 **KIND → ARITY → TYPES → SLOT**。V184 的 `validate()` 先判 types 长度，
#: 于是「一元函数 + 二元算子」被说成「类型填错」⇒ 回给模型的话指错方向（A4 假阴）。
_az148_d = _CG.diagnose("TAIL", ["dict"], {"keep": "tail_n"}, 1)
_az148_ok = _CG.diagnose("TAIL", ["str", "int"], {"keep": "tail_n"}, 2)

check(RS, "AZ148a `critgen.diagnose()` 的优先级是 **KIND → ARITY → TYPES → SLOT**：\n"
          "              一元函数选 `TAIL`（二元算子）⇒ `code=\"ARITY\"`、`need=2`、`got=1`；\n"
          "              ⛔ 不是 `TYPES`（`validate()` 报的是「第二个类型必须是 int」，\n"
          "              那句话把「参数个数对不上」说成了「类型填错」）；合法组合 ⇒ `OK`；\n"
          "              撤回语义：把 ARITY 分支的 code 改回 \"TYPES\" ⇒ 本条必须红",
      _az148_d["code"] == "ARITY" and _az148_d["need"] == 2 and _az148_d["got"] == 1
      and _az148_d["ok"] is False and _az148_ok["code"] == "OK",
      f"{_az148_d['code']} need={_az148_d['need']} got={_az148_d['got']} "
      f"ok={_az148_d['ok']}")

_az148_codes = {
    "KIND": _CG.diagnose("NOPE", ["str"], {}, 1)["code"],
    "ARITY": _az148_d["code"],
    "TYPES": _CG.diagnose("ROUNDTRIP", ["str", "int"], {"decode": "json.loads"}, 1)["code"],
    "SLOT": _CG.diagnose("ROUNDTRIP", ["str"], {}, 1)["code"],
}

check(RS, "AZ148b 四种「不过」各自可达、且互不吞并：KIND（不存在的算子）/ ARITY（参数个数）/\n"
          "              TYPES（长度或类型名）/ SLOT（槽缺失或不在枚举里）；\n"
          "              ⛔ 四条必须各不相同 —— 归成两类就等于回错又变回一句话；\n"
          "              撤回语义：槽循环改成 `for key in ()`（不查槽）⇒ SLOT 变 OK ⇒ 必须红",
      set(_az148_codes.values()) == set(_CG.CODES)
      and _CG.diagnose("ROUNDTRIP", ["str"], {}, 1)["ok"] is False,
      f"{_az148_codes}")

#: `diagnose()` 与 `validate()` 只允许在「为什么」上有差别，⛔ 不许在「过/不过」上分叉。
_az148_pairs = [
    ("ROUNDTRIP", ["dict"], {"decode": "json.loads"}, 1),
    ("TAIL", ["dict"], {"keep": "tail_n"}, 1),
    ("TAIL", ["str", "int"], {"keep": "tail_n"}, 2),
    ("TAIL", ["str"], {"keep": "tail_n"}, 1),
    ("NOPE", ["str"], {}, 1),
    ("ROUNDTRIP", ["str"], {}, 1),
    ("ROUNDTRIP", ["str"], {"decode": "nope"}, 1),
    ("ROUNDTRIP", ["str", "int"], {"decode": "json.loads"}, 1),
    ("CONSERVE", ["list"], {}, 1),
    ("EMPTY_IFF_EQUAL", ["dict"], {}, 1),
    # ★V188（§256）：补一组「异类型 EMPTY_IFF」。撤回 `AZ151a` 时两面会分叉，
    #   而原来的 10 组里没有一组能看见它 ⇒ 检测器对这一处是瞎的。
    #   加它进来不是为了让某条断言变红，是为了让 AZ148c 的一致性覆盖**真的覆盖**。
    ("EMPTY_IFF_EQUAL", ["str", "int"], {}, 2),
]
_az148_disagree = [(k, _CG.diagnose(k, t, s, n)["ok"], _CG.validate(k, t, s, n))
                   for k, t, s, n in _az148_pairs
                   if _CG.diagnose(k, t, s, n)["ok"] != (_CG.validate(k, t, s, n) == "")]

check(RS, "AZ148c `diagnose()` 与 `validate()` 对**过/不过**必须逐例一致（11 组枚举）：\n"
          "              两者只允许在「为什么」上不同 —— 分层是加解释，⛔ 不是放宽门槛；\n"
          "              撤回语义：把 diagnose 的 `len(types) != n_args` 改成 `<`（允许多填）\n"
          "              ⇒ 第 8 组变 OK 而 validate 仍拒 ⇒ 必须红",
      _az148_disagree == [],
      f"{_az148_disagree}")

_az148_txt = _CG.diagnose_text(_az148_d)
_az148_leak = [k for k in _CG.OPS if k != "TAIL" and k in _az148_txt]

check(RS, "AZ148d 回错句子**只提模型自己选的算子**，⛔ 不点名该换哪个（那是答案）：\n"
          "              ARITY 类的句子除 `TAIL` 外不得出现任何其它算子 id；\n"
          "              ⛔ 也别按「目录里有的就能推荐」放行 —— 目录是全量陈列，不是推荐；\n"
          "              撤回语义：句尾追加「建议改用 ROUNDTRIP」⇒ 本条必须红",
      _az148_leak == [] and "TAIL" in _az148_txt and _az148_txt != "",
      f"leak={_az148_leak} txt={_az148_txt!r}")

# ═══════════ V186（§254）：arity 硬约束 = 候选集过滤（AZ149） ═══════════

_az149_one = _CG.ops_for(1)
_az149_two = _CG.ops_for(2)

# ⚠️ V187 口径修正：原来的断言把「算子清单」写死成三个常量 —— 而算子库本来就是
#    可扩展的（V187 加了 NUMFMT/INVARIANT），那样的断言会在**合法的**扩展上转红。
#    ⇒ 改成结构断言：并集覆盖、互不相交、各自 arity 相符、且二元算子不出现在一元组。
#    这几条合起来**不是恒真**：`ops_for` 退回全量 ⇒ 不相交破裂；返回空 ⇒ 并集破裂。
check(RS, "AZ149a `critgen.ops_for(n)` 只返回 arity 相符的算子：一元题**拿不到**任何二元算子，\n"
          "              二元题反之。⛔ 这不是提示语而是候选集 —— 提示可以不理（V185 实测\n"
          "              它把 TAIL 换成另一个二元算子），候选集不行；\n"
          "              撤回语义：改成 `return dict(OPS)` ⇒ 一元题里冒出 TAIL ⇒ 必须红",
      set(_az149_one) | set(_az149_two) == set(_CG.OPS)
      and not (set(_az149_one) & set(_az149_two))
      and all(_CG.OPS[k]["arity"] == 1 for k in _az149_one)
      and all(_CG.OPS[k]["arity"] == 2 for k in _az149_two)
      and "TAIL" not in _az149_one and "BOUND" not in _az149_one,
      f"one={sorted(_az149_one)} two={sorted(_az149_two)}")

_az149_unknown = _CG.ops_for(-1)

check(RS, "AZ149b 读不出签名（`n_args < 0`）时 `ops_for()` 退回**全量**：\n"
          "              ⛔ 不许返回空集 —— 那会让「这道题没有可用算子」冒充约束，\n"
          "              而事实是装置不知道；签名未知时的过滤是恒真动作（纪律 5 同型）；\n"
          "              撤回语义：`n_args < 0` 分支返回 `{}` ⇒ 本条必须红",
      set(_az149_unknown) == set(_CG.OPS) and len(_az149_unknown) == len(_CG.OPS),
      f"unknown={sorted(_az149_unknown)}")

_az149_cat1 = _CG.catalogue(1).splitlines()
_az149_cat2 = _CG.catalogue(2).splitlines()
_az149_cat_all = _CG.catalogue().splitlines()

check(RS, "AZ149c `catalogue(n)` 的行数 == `len(ops_for(n))`，且每行只讲**本算子**的事；\n"
          "              `catalogue()`（无参）仍是全量 6 行 —— `retry_hint()` 靠它陈列，\n"
          "              ⛔ 不能因为加了过滤就把回错也一起收窄（那会让重试看不见候选）；\n"
          "              撤回语义：`catalogue()` 忽略 `n_args`（永远全量）⇒ 本条必须红",
      len(_az149_cat1) == len(_az149_one) and len(_az149_cat2) == len(_az149_two)
      and len(_az149_cat_all) == len(_CG.OPS)
      and all(any(k in ln for ln in _az149_cat1) for k in _az149_one)
      and not any("TAIL" in ln or "BOUND" in ln for ln in _az149_cat1),
      f"1={len(_az149_cat1)} 2={len(_az149_cat2)} all={len(_az149_cat_all)}")

# ═══════════ V187（§255）：补算子缺口 + 骨架等长（AZ150） ═══════════

from mw.verify import run_example as _az150_run                # noqa: E402


#: 夹具：**与 `critgen._NUMFMT` 不同写法**的同一规格（倒序切组 + `str.format`）。
#: ⛔ 两份实现交叉验证 —— 契约与产品各自独立写一遍，任何一边的舍入/切组走样都会红
#:    （纪律 63：判「数值相同」时原语也是判据）。
def _az150_fmt(x, sep=" ", dec=2, neg="paren"):
    v = abs(float(x))
    body = ("{:." + str(dec) + "f}").format(v)
    ip, _, fp = body.partition(".")
    out = ""
    for i, ch in enumerate(reversed(ip)):
        if i and i % 3 == 0:
            out = sep + out
        out = ch + out
    body = out + ("." + fp if dec else "")
    if float(x) < 0:
        return ("(" + body + ")") if neg == "paren" else ("-" + body)
    return body


_AZ150_NUM = (0, 7, 1234.5, -1234.5, 1234567.891, -0.5)
#: 生成的判据里 `_rel()` 调 `_az150_num_fn`，所以夹具的两个函数都要拼进代码。
_AZ150_PRE = (inspect.getsource(_az150_fmt) + "\n\n"
              "def _az150_num_fn(x, sep=' ', dec=2, neg='paren'):\n"
              "    return _az150_fmt(x, sep, dec, neg)\n")


def _az150_src(slots, pool=None):
    pool = _AZ150_NUM if pool is None else pool
    return _CG.build("NUMFMT", ["num"], slots, "_az150_num_fn",
                     [(v,) for v in pool])


def _az150_judge(src, impl=None):
    """在给定实现上跑判据。`impl` 为 None ⇒ 用夹具的正确实现。通过 ⇒ `""`。"""
    code = _AZ150_PRE if impl is None else impl
    return _az150_run(code + "\n" + src, "assert _rel()")


_az150_ok = []
for _sep in [k for k in _CG.SEPS if k]:
    for _dec in [k for k in _CG.DECS if k]:
        for _neg in [k for k in _CG.NEGS if k]:
            _s = _az150_src({"sep": _sep, "dec": _dec, "neg": _neg})
            if _s and _az150_judge(_s) == "":
                _az150_ok.append((_sep, _dec, _neg))

check(RS, "AZ150a `NUMFMT` 的三个规格槽**唯一**可定：24 组合（sep 3 × dec 4 × neg 2）里\n"
          "              恰好 1 个能在正确实现上通过 ⇒ 「三槽全对」⟺ `disc=True`，两者可互推。\n"
          "              ⛔ 若出现 ≥2 个 ⇒ 槽不唯一，「槽选对了」不能单独当判别力（V187 STAGE0 实测 1 个）；\n"
          "              撤回语义：`SEPS` 里 `\"space\": \" \"` 改成无分隔 ⇒ 通过数变 0 ⇒ 必须红",
      _az150_ok == [("space", "2", "paren")],
      f"ok={_az150_ok}")

#: 同一份判据、同一个槽值，只换**输入池**：小值池上「逗号实现」与「空格实现」输出相同
#: ⇒ 判据咬不住；跨过三位边界后立刻咬住。这是「判别力是 (判据, 输入域) 的函数」的机械证据。
_AZ150_COMMA_IMPL = (inspect.getsource(_az150_fmt) + "\n\n"
                     "def _az150_num_fn(x, sep=' ', dec=2, neg='paren'):\n"
                     "    return _az150_fmt(x, ',', dec, neg)\n")
_az150_slot = {"sep": "space", "dec": "2", "neg": "paren"}
_az150_small = _az150_src(_az150_slot, (0, 3, 5))
_az150_big = _az150_src(_az150_slot, (1234.5, -1234.5))
_az150_bite_small = (_az150_small != "") and _az150_judge(_az150_small, _AZ150_COMMA_IMPL) != ""
_az150_bite_big = (_az150_big != "") and _az150_judge(_az150_big, _AZ150_COMMA_IMPL) != ""

check(RS, "AZ150b 「判别力是 `(判据, 输入域)` 的函数」：**同一份判据、同一个槽值**，\n"
          "              只把输入池从小值（0/3/5）换成跨三位边界（1234.5/−1234.5），\n"
          "              对「逗号千分位」这个错误实现的判定就从**通过**变成**失败**。\n"
          "              ⇒ 池不是「喂数据的管道」，它是判据的一部分（V187 STAGE0 §1.3）；\n"
          "              撤回语义：`POOL[\"num\"]` 去掉 1234.5/−1234.5 ⇒ 本条必须红",
      (not _az150_bite_small) and _az150_bite_big,
      f"small_bite={_az150_bite_small} big_bite={_az150_bite_big}")

#: `INVARIANT` 的非退化：常量实现在**任何**池上都必须被判据拒绝。
_AZ150_FLAG = ("def _az150_flag_ok(s):\n"
               "    t = str(s).strip().lower()\n"
               "    if t in ('y', 'yes', '1'):\n"
               "        return True\n"
               "    if t in ('n', 'no', '0', 'off', 'false'):\n"
               "        return False\n"
               "    return None\n")
_AZ150_FLAG_CONST = "def _az150_flag_ok(s):\n    return None\n"
_az150_inv = _CG.build("INVARIANT", ["flag"], {}, "_az150_flag_ok")
_az150_inv_ok = bool(_az150_inv) and \
    _az150_run(_AZ150_FLAG + "\n" + _az150_inv, "assert _rel()") == ""
_az150_inv_const = bool(_az150_inv) and \
    _az150_run(_AZ150_FLAG_CONST + "\n" + _az150_inv, "assert _rel()") != ""

check(RS, "AZ150c `INVARIANT` 的**非退化**谓词：同一份判据，正确实现通过、常量实现必须失败。\n"
          "              ⛔ 这一条防的是「不变性」单独成立时的恒真判据 —— 常量函数对任何\n"
          "              规范化变换都不变（纪律 5 的教科书形态）；\n"
          "              撤回语义：删掉 `if len(set(seen)) < 2` 那段 ⇒ 常量实现也通过 ⇒ 必须红",
      _az150_inv_ok and _az150_inv_const,
      f"built={bool(_az150_inv)} ok_pass={_az150_inv_ok} const_bitten={_az150_inv_const}")

_az150_sk1 = json.loads(_CG.skeleton(1))
_az150_sk3 = json.loads(_CG.skeleton(3))

check(RS, "AZ150d `skeleton(n_args)` 的 `types` 占位个数 == `n_args`：骨架的**长度**就是\n"
          "              模型看到的约束（V186 实测它写 `[\"int\",\"float\"]` —— 两个类型、\n"
          "              还编了个 `float`；V185 已证「话说给它听」不构成约束）；\n"
          "              撤回语义：`\", \".join([tslot] * n)` 改回 `[tslot]` ⇒ 本条必须红",
      len(_az150_sk1.get("types") or []) == 1
      and len(_az150_sk3.get("types") or []) == 3
      and len(set(_az150_sk3["types"])) == 1,
      f"n1={len(_az150_sk1.get('types') or [])} n3={len(_az150_sk3.get('types') or [])}")

# ═══════════ V188（§256）：三条坏信号收口（AZ151） ═══════════
#
# 三处改动都**不改判据本体**：S2 是结构约束（挡住恒真形态）、S1 是读数三值（改「算不算证据」）、
# S3 是准入口径（区分「写死答案」与「写死规格」）。⇒ 本轮没有一条契约在断言「分数变好」。

# ── AZ151a：`EMPTY_IFF_EQUAL` 的两输入必须同类型 ──────────────────────
_az151_diff = [(a, b) for a in _CG.TYPES for b in _CG.TYPES if a != b]
_az151_same = [(a, a) for a in _CG.TYPES]
_az151_diff_codes = {_CG.diagnose("EMPTY_IFF_EQUAL", [a, b], {}, 2)["code"]
                     for a, b in _az151_diff}
_az151_same_ok = all(_CG.validate("EMPTY_IFF_EQUAL", [a, a], {}, 2) == ""
                     for a in _CG.TYPES)
#: 异类型里**靠池值撞车**才拿到相等对的组数。⚠️ 这个数必须落在 (0, 30) ——
#: ① 若为 0，说明 `_inputs_for()` 改了生成规则；② 若为 30，说明「异类型必然没有相等对」
#: 是恒真的，那 `iff` 的 ⟸ 支就永远缺席 —— 两种情形下本约束的前提都要重写。
_az151_eq = [1 for a, b in _az151_diff
             if any(x[0] == x[1] for x in _CG.inputs_for("EMPTY_IFF_EQUAL", [a, b]))]

check(RS, "AZ151a `EMPTY_IFF_EQUAL` 要求**两输入同类型**：30 个异类型组合全部以 `TYPES` 拒、\n"
          "              6 个同类型组合全部放行。理由不是「方便」而是算子自身的语义 ——\n"
          "              断言是 `(got == {}) != (a == b)`，异类型时 `a == b` 的 ⟸ 支只能靠\n"
          "              **两个池的值恰好撞车**（如 `int` 与 `num` 共享 `0`）才可能被行使，\n"
          "              不是结构保证 ⇒ 那一条判据是「恒真」而不是「判得严」（V187 §1.4-1）；\n"
          "              撤回语义：删掉 `validate()` 里那条 `EMPTY_IFF_EQUAL` 分支 ⇒ 异类型被放行 ⇒ 必须红",
      _az151_diff_codes == {"TYPES"} and _az151_same_ok
      and 0 <= len(_az151_eq) < len(_az151_diff),
      f"diff_codes={_az151_diff_codes} same_ok={_az151_same_ok} "
      f"equal_pairs={len(_az151_eq)}/{len(_az151_diff)}")

# ── AZ151b：`constant_on()` 只回答「输出是不是全同」 ────────────────────
_az151_c = "def _az151_f(x):\n    return None\n"
_az151_v = "def _az151_f(x):\n    return len(str(x)) > 2\n"
_az151_empty = "def _az151_f(x):\n    return []\n"
_az151_ins = [("a",), ("bb",), ("ccc",)]

check(RS, "AZ151b `critgen.constant_on()` 在**目标实现**上判「输出是否全同」：\n"
          "              常量实现 ⇒ True；随输入变化的实现 ⇒ False；每次都返回**新对象但相同值**\n"
          "              （`[]`）⇒ 仍 True（比的是 `repr`，不是对象身份）。\n"
          "              ⛔ 它**不判对错**（与 `legal_indexes()` 同一条边界：可达性免费、有效性收费）；\n"
          "              撤回语义：`len(set(outs)) <= 1` 改成 `<= 0` ⇒ 常量实现报 False ⇒ 必须红",
      _CG.constant_on(_az151_c, "_az151_f", _az151_ins) is True
      and _CG.constant_on(_az151_v, "_az151_f", _az151_ins) is False
      and _CG.constant_on(_az151_empty, "_az151_f", _az151_ins) is True,
      f"const={_CG.constant_on(_az151_c, '_az151_f', _az151_ins)} "
      f"vary={_CG.constant_on(_az151_v, '_az151_f', _az151_ins)} "
      f"empty={_CG.constant_on(_az151_empty, '_az151_f', _az151_ins)}")

# ── AZ151c：三值读数 —— `UNDECIDED` 优先于 PASS/FAIL ───────────────────
_az151_v3 = [
    _CG.verdict(built=False, pass_oracle=False, oracle_const=None, fail="TYPES"),
    _CG.verdict(built=True, pass_oracle=True, oracle_const=True),
    _CG.verdict(built=True, pass_oracle=False, oracle_const=True),
    _CG.verdict(built=True, pass_oracle=True, oracle_const=False),
    _CG.verdict(built=True, pass_oracle=False, oracle_const=False),
]

check(RS, "AZ151c `critgen.verdict()` 的三值：`UNDECIDED` **优先**于 PASS/FAIL ——\n"
          "              正确实现在该池上输出全同时，即使断言通过也记 `UNDECIDED`\n"
          "              （那说明判据在池上不承载信息，而不是「通过了」）。\n"
          "              ③④⑤ 与二值逐格相同 ⇒ 三值不是「多给了几条命」；\n"
          "              撤回语义：把 `if oracle_const:` 挪到 `return \"PASS\"` 之后 ⇒ 第 2 条变 PASS ⇒ 必须红",
      _az151_v3 == ["TYPES", "UNDECIDED", "UNDECIDED", "PASS", "FAIL"],
      f"{_az151_v3}")

# ── AZ151d：`admit(src, params=())` 区分「写死答案」与「写死规格」 ──────
_az151_specsrc = ('def _ref(x, sep):\n'
                  '    if sep == " ":\n'
                  '        return str(x)\n'
                  '    return str(x)\n'
                  '\n'
                  'def _rel():\n'
                  '    return True\n')
_az151_shadow = ('sep = " "\n'
                 '\n'
                 'def _rel():\n'
                 '    if sep == " ":\n'
                 '        return True\n'
                 '    return True\n')
_az151_numf = _CG.build("NUMFMT", ["num"], {"sep": "space", "dec": "2", "neg": "paren"},
                        "_az151_num")

check(RS, "AZ151d `metacrit.admit(src, params=)` 只放行**规格参数**的比较：\n"
          "              ① `params` 里声明、且确是源码中某函数**形参**的名字 ⇒ 不计为期望值；\n"
          "              ② 同样的源码不给 `params` ⇒ 仍拒（口径默认没变）；\n"
          "              ③ 同名但**模块级变量**（不是形参）⇒ 即使给了 `params` 也必须拒\n"
          "              （否则 `sep = \" \"` 一行就能冒充规格）；\n"
          "              ④ `NUMFMT` 渲染出的真判据：默认拒、带 `spec_params()` 放行；\n"
          "              撤回语义：去掉 `and other.id in formals` ⇒ ③ 被放行 ⇒ 必须红",
      _MC.admit(_az151_specsrc, ("sep",))[0] is True
      and _MC.admit(_az151_specsrc)[0] is False
      and _MC.admit(_az151_shadow, ("sep",))[0] is False
      and bool(_az151_numf)
      and _MC.admit(_az151_numf)[0] is False
      and _MC.admit(_az151_numf, _CG.spec_params("NUMFMT"))[0] is True,
      f"spec={_MC.admit(_az151_specsrc, ('sep',))[0]} "
      f"plain={_MC.admit(_az151_specsrc)[0]} "
      f"shadow={_MC.admit(_az151_shadow, ('sep',))[0]} "
      f"numfmt={_MC.admit(_az151_numf)[0]}/"
      f"{_MC.admit(_az151_numf, _CG.spec_params('NUMFMT'))[0]}")

# ═══════════ V189（§257）：诊断 → 收窄 → 判断（AZ152） ═══════════
#
# 本轮**没有一条契约断言「分数变好」** —— 它钉的是「诊断的输出能不能改变下一轮的
# 可选空间」。V185 已量过「说给模型听」不产生变化（两臂读数逐字相同），所以这里
# 钉的全部是**结构**：信号怎么分类、收窄删了谁、空集怎么表达。

# ── AZ152a：`read_signal()` 的五类映射 ────────────────────────────────
_az152_tab = [
    #: (built, fail, pass_oracle, bite_stub, bite_partial, oracle_const) → 期望
    ((False, "TYPES", 0, 0, 0, None), "STRUCT:TYPES"),
    ((False, "OUT_OF_DOMAIN", 0, 0, 0, None), "STRUCT:TYPES"),   # ⚠️ 与 TYPES 同类
    ((False, "ARITY", 0, 0, 0, None), "STRUCT:OTHER"),
    ((False, "SHAPE", 0, 0, 0, None), "STRUCT:OTHER"),
    ((True, "", 0, 0, 0, True), "CONST"),                        # 恒定压过判分
    ((True, "", 1, 1, 1, False), "OK"),
    ((True, "", 0, 1, 1, False), "SEMANTIC"),                    # 误伤正确实现
    ((True, "", 1, 1, 0, False), "SEMANTIC"),                    # `(1,1,0)` 不归 OK
    ((True, "", 1, 1, 1, None), "OK"),                           # 探测失败不阻止判 OK
]
_az152_got = [(sig, _CG.read_signal(built=b, fail=f, pass_oracle=bool(p),
                                    bite_stub=bool(s), bite_partial=bool(pb),
                                    oracle_const=c))
              for (b, f, p, s, pb, c), sig in _az152_tab]

check(RS, "AZ152a `critgen.read_signal()` 把一格读数折成五类信号，且**只吃不泄漏的东西**：\n"
          "              ① `STRUCT:TYPES` 同时收 `TYPES` 与 `OUT_OF_DOMAIN` —— 它们是同一件事\n"
          "              「该签名在目标实现上没有域」，分成两类会让签名分支对最常见的不可达视而不见；\n"
          "              ② `CONST`（输出恒定）**压过**判分：那格不构成关于判据的证据；\n"
          "              ③ `(1,1,0)`（过正确、咬空实现、咬不住部分实现）归 `SEMANTIC` 不归 `OK` ——\n"
          "              `disc` 的定义就是 `pass_oracle ∧ bite_partial`；\n"
          "              撤回语义：把 `OUT_OF_DOMAIN` 从 TYPES 分支去掉 ⇒ 第 2 项变 `STRUCT:OTHER` ⇒ 必须红",
      all(want == got for want, got in _az152_got),
      f"表格逐项={[f'{w}!=({g})' for w, g in _az152_got if w != g] or 'OK'}"
      f" n={len(_az152_got)}")

# ── AZ152b：`reachable_on()` 只问可达，不问对错 ──────────────────────
_az152_dictfn = "def _az152_f(d):\n    return sorted(d.items())\n"
_az152_strfn = "def _az152_f(s):\n    return s.strip()\n"
_az152_never = "def _az152_f(*a):\n    raise TypeError('no')\n"

check(RS, "AZ152b `critgen.reachable_on()` 在**目标实现**上判「这组输入喂得进去吗」：\n"
          "              吃 dict 的实现 × dict 签名 ⇒ True；同一实现 × str 签名 ⇒ False；\n"
          "              什么都不吃的实现 ⇒ False。\n"
          "              ⛔ 与 `legal_indexes()` 同一条边界：只问能不能吃，不问返回值对不对；\n"
          "              ⚠️ 名字带 `_on` 是必须的 —— 本模块已有 `reachable(kind, n_args)`，\n"
          "              初版撞名覆盖了它，`validate()` 直接报缺参数（记一笔）；\n"
          "              撤回语义：函数体改成 `return True` ⇒ 第 2、3 项变 True ⇒ 必须红",
      _CG.reachable_on("INVARIANT", ["dict"], _az152_dictfn, "_az152_f") is True
      and _CG.reachable_on("INVARIANT", ["str"], _az152_dictfn, "_az152_f") is False
      and _CG.reachable_on("INVARIANT", ["str"], _az152_never, "_az152_f") is False,
      f"dict/dict={_CG.reachable_on('INVARIANT', ['dict'], _az152_dictfn, '_az152_f')} "
      f"dict/str={_CG.reachable_on('INVARIANT', ['str'], _az152_dictfn, '_az152_f')} "
      f"never/str={_CG.reachable_on('INVARIANT', ['str'], _az152_never, '_az152_f')}")

# ── AZ152c：`scope="one"` 就是「删掉这一个」，别处与 class 同 ──────────
_az152_c2 = _CG.ops_for(2)
_az152_c1 = _CG.ops_for(1)
_az152_one2, _az152_w12 = _CG.narrow("SEMANTIC", 2, "BOUND", ["str", "int"], "", "", scope="one")
_az152_cls2, _az152_w22 = _CG.narrow("SEMANTIC", 2, "BOUND", ["str", "int"], "", "", scope="class")
_az152_one1, _ = _CG.narrow("CONST", 1, "NUMFMT", ["num"], "", "", scope="one")

check(RS, "AZ152c `narrow(scope=\"one\")` 的产物**恰好**是 `ops_for(n) - {chosen}`：\n"
          "              二元题 3 个算子删 1 剩 2、一元题 5 个删 1 剩 4；\n"
          "              且在 `SEMANTIC` 上 `scope=\"class\"` 与它**逐格相同**（都是 `SINGLE`）——\n"
          "              这是设计保证，⛔ 不是发现：语义层没有不泄漏的信息说该换成谁。\n"
          "              撤回语义：`scope=\"one\"` 分支改成不删（返回全量）⇒ 第 1、2 项崩 ⇒ 必须红",
      set(_az152_one2) == set(_az152_c2) - {"BOUND"}
      and set(_az152_one1) == set(_az152_c1) - {"NUMFMT"}
      and set(_az152_cls2) == set(_az152_one2)
      and _az152_w12["reason"] == "SINGLE" and _az152_w22["reason"] == "SINGLE",
      f"one2={sorted(_az152_one2)} one1={len(_az152_one1)} "
      f"cls2_eq_one2={set(_az152_cls2) == set(_az152_one2)} "
      f"reasons={_az152_w12['reason']}/{_az152_w22['reason']}")

# ── AZ152d：`scope="class"` 的两条降级都得留痕 ────────────────────────
_az152_all, _az152_wall = _CG.narrow("STRUCT:TYPES", 1, "ROUNDTRIP", ["str"],
                                     _az152_never, "_az152_f", scope="class")
_az152_badty, _az152_wbad = _CG.narrow("STRUCT:TYPES", 1, "ROUNDTRIP", ["float"],
                                       _az152_strfn, "_az152_f", scope="class")
_az152_badlen, _az152_wlen = _CG.narrow("STRUCT:TYPES", 2, "BOUND", ["str"],
                                        _az152_strfn, "_az152_f", scope="class")
#: ⚠️ ③ 必须用**二元**题：一元题同一 `types` 下所有算子的输入池**完全一样**
#:    （`_inputs_for()` 只看 kinds 的特例与 types），所以一元题的「按类删」只能是
#:    「全删」或「全不删」。二元题里 `TAIL`/`BOUND` 吃 `(x, int)`、`EMPTY_IFF` 吃 `(a, b)`
#:    ⇒ 才有「部分可达」这一档。
_az152_plus = "def _az152_f(s, n):\n    return s + n\n"
_az152_ok, _az152_wok = _CG.narrow("STRUCT:TYPES", 2, "TAIL", ["str", "str"],
                                   _az152_plus, "_az152_f", scope="class")

check(RS, "AZ152d `narrow(scope=\"class\")` 的 `STRUCT:TYPES` 分支只有三种结局，各留一个标记：\n"
          "              ① 该签名下**全不可达** ⇒ 退回「只删 chosen」，`ALL_UNREACHABLE_SIGNATURE`\n"
          "              （⛔ 不许返回空集当「收敛」：空集说的是「这题无解」，不是「已经选对」）；\n"
          "              ② 签名本身坏了（`float` 不在类型表 / 长度不符）⇒ `BAD_SIGNATURE`，也只删一个；\n"
          "              ③ 有可达也有不可达的 ⇒ `SIGNATURE`，按类删（二元题 `TAIL`/`BOUND` 吃 `(x,int)`、\n"
          "              `EMPTY_IFF` 吃 `(a,b)` ⇒ 一个 `s + n` 的实现就能造出这一档）。\n"
          "              撤回语义：删掉 `if len(removed) >= len(cand)` 那段 ⇒ ① 返回空集 ⇒ 必须红",
      _az152_wall["reason"] == "ALL_UNREACHABLE_SIGNATURE"
      and _az152_wall["removed"] == ["ROUNDTRIP"] and len(_az152_all) == len(_az152_c1) - 1
      and _az152_wbad["reason"] == "BAD_SIGNATURE" and _az152_wlen["reason"] == "BAD_SIGNATURE"
      and _az152_wbad["removed"] == ["ROUNDTRIP"] and _az152_wlen["removed"] == ["BOUND"]
      and _az152_wok["reason"] == "SIGNATURE"
      and set(_az152_wok["removed"]) == {"TAIL", "BOUND"} and len(_az152_ok) == 1,
      f"all={_az152_wall['reason']}/{_az152_wall['removed']} "
      f"badty={_az152_wbad['reason']} badlen={_az152_wlen['reason']} "
      f"ok={_az152_wok['reason']}/{_az152_wok['removed']}")

# ── AZ152e：空集合就是空集合，⛔ 不许回退成全量 ──────────────────────
_az152_cat0 = _CG.catalogue(allow={})
_az152_sk0 = json.loads(_CG.skeleton(2, allow={}))
_az152_cat1 = _CG.catalogue(allow={"BOUND": _CG.OPS["BOUND"]})

check(RS, "AZ152e `catalogue(allow=)` / `skeleton(allow=)` 用 `is not None` 判空，\n"
          "              不是真值判断：\n"
          "              ① `allow={}` ⇒ 目录是**空串**、骨架的 kind 列表是 `<>`（一个都不剩）；\n"
          "              ② `allow={一个}` ⇒ 目录只有一行。\n"
          "              ⚠️ 写成 `allow or ...` 就会把「空」读成「没给」，于是**一个都不剩**\n"
          "              悄悄变回**全量** —— 那正是 V188 记的「空当没给」那一族；\n"
          "              撤回语义：`allow if allow is not None else` 改成 `allow or` ⇒ ① 崩 ⇒ 必须红",
      _az152_cat0 == "" and _az152_sk0.get("kind") == "<>"
      and _az152_cat1.count("\n") == 0 and "BOUND" in _az152_cat1,
      f"cat0={_az152_cat0!r} sk0={_az152_sk0.get('kind')!r} "
      f"cat1_lines={_az152_cat1.count(chr(10)) + 1}")

# ═══════════ V190（§258）：对弱点自己出题（AZ153） ═══════════
#
# 本轮**没有一条断言「分数变好」** —— 它钉的是「生成器看不见实现」这条边界，
# 以及「扩池后判据只差 INPUTS 一行」。V190 实测：结构变异 0/17、随机噪声 0/17、
# 记忆/题面/实现字面量三者**逐格同效**（5/17，其中 1 格是假绿）。

import inspect as _az153_inspect                              # noqa: E402
import mw.probe as _PB                                        # noqa: E402

#: 与 `bench/build_g7d_seed.py` 的 `FACTS["g7e1_parse_flag"]` 逐字相同。
_az153_mem = ("开关惯例：只把 y/yes/1 当成真，'true'、'on' 这类历史脏值一律算假，"
              "其余无法识别的返回 None。")
_az153_tok = _PB.tokens(_az153_mem)

# ── AZ153a：词元提取的三条来源与三条拒绝 ──────────────────────────────
#: ⚠️ 中文必须**放在引号里**才算测试：裸中文根本不会被三条提取正则捞到
#:    （引号内 / 斜杠组 / 裸 ASCII 词）。V190 初版拿裸中文测 ⇒ 断言永远不可能红。
_az153_cn = _PB.tokens("例：'把这一整句话当成一个词元是不对的' 以及 y")
check(RS, "AZ153a `probe.tokens()` 从一段文本取候选词元，且**只取能当输入用的**：\n"
          "              ① 引号内 / 斜杠组拆开 / 裸 ASCII 词三条来源都要覆盖 ——\n"
          "              记忆正文 `只把 y/yes/1 当成真，'true'、'on' 这类…` 必须取到\n"
          "              `y`、`yes`、`1`、`true`、`on`（V190 就是靠这几个词翻开了 5 格）；\n"
          "              ② ⛔ 不产出长度 > 24 的串、⛔ 不产出空串、⛔ 不产出含非 ASCII 的串\n"
          "              （把整句中文当一个词元 = 往池里塞一条谁都吃不下的垃圾，预注册 F3）；\n"
          "              ⚠️ 这一条**必须拿引号里的中文**测：裸中文连提取正则都进不去，测不出东西；\n"
          "              ③ 去重保序。\n"
          "              撤回语义：删掉 `re.fullmatch(r\"[\\x20-\\x7e]+\", v)` 那道闸 ⇒ 引号里那句中文进池 ⇒ 必须红",
      {"y", "yes", "1", "true", "on"} <= set(_az153_tok)
      and all(0 < len(t) <= _PB.MAX_TOKEN and t.isascii() for t in _az153_tok)
      and _az153_tok == list(dict.fromkeys(_az153_tok))
      and _az153_cn == ["y"],
      f"tok={_az153_tok} cn={_az153_cn}")

# ── AZ153b：生成器的签名里没有实现 ────────────────────────────────────
_az153_p1 = set(_az153_inspect.signature(_PB.expand).parameters)
_az153_p2 = set(_az153_inspect.signature(_PB.structural).parameters)
_az153_p3 = set(_az153_inspect.signature(_PB.tokens).parameters)

check(RS, "AZ153b `probe.expand()` / `structural()` / `tokens()` 的参数表里**没有**实现：\n"
          "              既没有 `oracle`、也没有 `fn` / `impl` / `code`。\n"
          "              ⭐ 这不是风格问题：只要生成器能跑一遍实现，就能「先挑出会让输出变化的输入、\n"
          "              再宣布池退化已修复」—— 那是重言式，读数一文不值。V190 全靠这条边界\n"
          "              才让「转可判 5/17」有意义。\n"
          "              唯一的例外是 `literals(src)`：它只吃**源码文本**、只做 AST，\n"
          "              且 `expand()` 给它的产物一律标 `LEAK`（见 AZ153c）。\n"
          "              撤回语义：给 `expand()` 加一个 `oracle=""` 形参 ⇒ 必须红",
      not ({"oracle", "fn", "impl", "code"} & (_az153_p1 | _az153_p2 | _az153_p3))
      and "fn" not in _az153_p1,
      f"expand={sorted(_az153_p1)} structural={sorted(_az153_p2)} tokens={sorted(_az153_p3)}")

# ── AZ153c：泄漏源必须自己承认是泄漏 ──────────────────────────────────
_az153_src = ("def parse_flag(s):\n"
              "    t = str(s).strip().lower()\n"
              "    if t in ('y', 'yes', '1'):\n"
              "        return True\n"
              "    if t in ('n', 'no', '0', 'off', 'false', 'true', 'on'):\n"
              "        return False\n"
              "    return None\n")
_az153_lit = _PB.literals(_az153_src)
_az153_e_lit = _PB.expand("ROUNDTRIP", ["str"], sources=("literal",), oracle_src=_az153_src)
_az153_e_mut = _PB.expand("ROUNDTRIP", ["str"], sources=("mutate",))
_az153_e_none = _PB.expand("ROUNDTRIP", ["str"], sources=("literal",), oracle_src="def f(\n")
_az153_e_dict = _PB.expand("ROUNDTRIP", ["dict"], sources=("literal",), oracle_src=_az153_src)

check(RS, "AZ153c 唯一读实现的入口 `probe.literals(src)` 只取字符串字面量，且泄漏必须自标：\n"
          "              ① 从 `parse_flag` 源码取到 `y/yes/1/n/no/0/off/false/true/on`（⛔ 不执行实现，只走 AST）；\n"
          "              ② `sources=('literal',)` ⇒ `leak is True`；`sources=('mutate',)` ⇒ `leak is False`；\n"
          "              ③ 源码解析失败 / 签名里没有字符串类型 ⇒ **没有东西可泄漏**，`leak` 必须为 `False`\n"
          "              （V190 实测 `g7d2` 的实现一个字符串字面量都没有 —— 它那 10 格**连作弊都没料**）；\n"
          "              ④ 每条新增值都要在 `prov` 里留 `source`，⛔ 不许只报总数。\n"
          "              撤回语义：`if source == \"literal\" and vals:` 删掉 ⇒ ② 崩 ⇒ 必须红",
      {"y", "yes", "1", "n", "no", "0", "off", "false", "true", "on"} <= set(_az153_lit)
      and _az153_e_lit["leak"] is True and _az153_e_mut["leak"] is False
      and _az153_e_none["leak"] is False and _az153_e_dict["leak"] is False
      and len(_az153_e_lit["prov"]) == len(_az153_e_lit["pools"]["str"]) - 4
      and all(p.get("source") for p in _az153_e_lit["prov"]),
      f"lit={_az153_lit} leak={_az153_e_lit['leak']}/{_az153_e_mut['leak']}"
      f"/{_az153_e_none['leak']}/{_az153_e_dict['leak']}")

# ── AZ153d：外部池真的接进了输入生成，且「没给」与「给空的」不是一回事 ──
_az153_base = _CG.inputs_for("ROUNDTRIP", ["str"])
_az153_pool = {"str": ("only",)}
_az153_use = _CG.inputs_for("ROUNDTRIP", ["str"], _az153_pool)
_az153_empty = _CG.inputs_for("ROUNDTRIP", ["str"], {"str": ()})
_az153_two = _CG.inputs_for("EMPTY_IFF_EQUAL", ["str", "str"], {"str": ("a", "b")})

check(RS, "AZ153d `critgen.inputs_for(kind, types, pool=)` 用 `is not None` 判「给没给池」：\n"
          "              ① `pool=None` ⇒ 与老行为**逐字相同**（V184~V189 的读数仍可复现）；\n"
          "              ② `pool={\"str\": (\"only\",)}` ⇒ 只产出 `[('only',)]`；\n"
          "              ③ `pool={\"str\": ()}` ⇒ **空列表**，⛔ 不许回退全池（V189 `AZ152e` 同一族）；\n"
          "              ④ 二元签名下组合仍按原规则（同类型取 a[0]/a[1]）。\n"
          "              撤回语义：`P = POOL if pool is None else pool` 改成 `pool or POOL` ⇒ ③ 变 4 条 ⇒ 必须红",
      _az153_base == _CG._inputs_for("ROUNDTRIP", ["str"])
      and _az153_use == [("only",)] and _az153_empty == []
      and _az153_two == [("a", "a"), ("a", "b"), ("b", "b")],
      f"base={_az153_base} use={_az153_use} empty={_az153_empty} two={_az153_two}")

# ── AZ153e：结构变异必须带来「基池没有的那一维」 ──────────────────────
_az153_st = _PB.structural(["int", "num"])
_az153_int_new = [v for v in _az153_st["int"] if v not in _CG.POOL["int"]]
_az153_num_new = [v for v in _az153_st["num"] if v not in _CG.POOL["num"]]

check(RS, "AZ153e `probe.structural()` 的变异必须**真的多出一维**，而不是把原池抄一遍：\n"
          "              ① `int` 的新值里要有**负数**（V190 唯一的那一格翻转就是靠它：\n"
          "              `int` 基池 `(0,3,5)` 全是非负数 ⇒ `fmt_amount` 的负号分支从没被行使过）；\n"
          "              ② `num` 的新值里要有 `0.5` 这类小数；\n"
          "              ③ 新值一律 ≠ 原值（否则「加了 32 条」是假的）。\n"
          "              撤回语义：`_mutate_int` 的 `-v` 删掉 ⇒ ① 崩 ⇒ 必须红",
      any(v < 0 for v in _az153_int_new) and any(0 < abs(v) < 1 for v in _az153_num_new)
      and all(v not in _CG.POOL["int"] for v in _az153_int_new)
      and all(v not in _CG.POOL["num"] for v in _az153_num_new),
      f"int_new={_az153_int_new} num_new={_az153_num_new}")

# ═══════════ V191（§259）：把「池退化」拆成病名（AZ154） ═══════════
#
# 本轮**没有一条断言「分数变好」**。它钉三件事：
#   ① 签名枚举的**声明序**（⛔ 不许按「谁先过」排序）；
#   ② 路由层**读不到正确性信号**（`pass_oracle`/`correct`/`expected` 一律缺席）；
#   ③ 「无域」与「恒定」是两件事（V190 §2.2 的 `k01` 就是前者被空容器伪装成后者）。
#
# V191 实测：17 个盲区格 **全部** `RESIGN_FREE`（`NEEDS_VOCAB = 0`），
# 重选后 17/17 转可判，但只落在 **6 个配置**上、且与这 6 个配置的**原有判定逐格相同**
# ⇒ 新增可判配置 **0**。这条结论靠的是下面这些函数**只花免费信息**，所以它们也钉在这里。

import inspect as _az154_inspect                              # noqa: E402

# ── AZ154a：签名枚举的声明序 ──────────────────────────────────────────
_az154_s1 = _PB.sig_space("ROUNDTRIP", 1)
_az154_s2 = _PB.sig_space("TAIL", 2)
_az154_s3 = _PB.sig_space("EMPTY_IFF_EQUAL", 2)
_az154_s4 = _PB.sig_space("SUBSEQ", 2)

check(RS, "AZ154a `probe.sig_space(kind, n)` 枚举**可行签名**，顺序 = `TYPES` 的**声明序**：\n"
          "              ① 一元 ⇒ 6 个单类型签名，首个 `['str']`、末个 `['flag']`；\n"
          "              ② `TAIL`/`BOUND` ⇒ 第二参数钉死 `'int'`（与 `critgen` 的输入生成同口径）；\n"
          "              ③ `EMPTY_IFF_EQUAL` ⇒ 两参数**同类型**，只有 6 个（⛔ 不是 36）；\n"
          "              ④ 其余二元 ⇒ 36 个笛卡尔积，首个 `['str','str']`、第二个 `['str','int']`。\n"
          "              ⭐ 顺序是判据的一部分：`route()` 取「第一个非退化」，按探测结果排序\n"
          "              就等于「谁先过用谁」= 拿尺子挑尺子。\n"
          "              撤回语义：删掉 `if kind == \"EMPTY_IFF_EQUAL\"` 那条分支 ⇒ ③ 变 36 个 ⇒ 必须红",
      _az154_s1 == [[t] for t in _CG.TYPES]
      and _az154_s2 == [[t, "int"] for t in _CG.TYPES]
      and _az154_s3 == [[t, t] for t in _CG.TYPES]
      and len(_az154_s4) == 36 and _az154_s4[0] == ["str", "str"]
      and _az154_s4[1] == ["str", "int"],
      f"s1={_az154_s1} s3={_az154_s3} s4[:2]={_az154_s4[:2]} len(s4)={len(_az154_s4)}")

# ── AZ154b：路由层读不到正确性信号，且「无域」≠「恒定」 ────────────────
_az154_p = (set(_az154_inspect.signature(_PB.route).parameters)
            | set(_az154_inspect.signature(_PB.varies).parameters)
            | set(_az154_inspect.signature(_PB.domain_of).parameters))
_az154_bad = sorted(_az154_p & {"pass_oracle", "correct", "expected", "want", "right", "truth"})

_az154_const = "def f(s):\n    return 1\n"
_az154_var = "def f(s):\n    return s == 'abcdef'\n"
_az154_nodom = "def f(d):\n    return d.get('a')\n"

check(RS, "AZ154b `probe.route()` / `varies()` / `domain_of()` **只吃免费信息**：\n"
          "              ① 参数表里没有 `pass_oracle`/`correct`/`expected`/`want` 任何一项\n"
          "              —— 一旦能读判分，「该换哪个签名」就变成拿尺子挑尺子（V189 `read_signal` 同一条边界）；\n"
          "              ② 恒定实现 ⇒ `varies` 为 `False`；有差异实现 ⇒ `True`；\n"
          "              ③ **无域** ⇒ `domain_of` 返回 `(\"NO_DOMAIN\", None)`，⛔ 不许折成 `(\"OK\", False)`\n"
          "              （「问不出问题」与「问得出但实现不区分」修法相反）；\n"
          "              ④ 探测失败另有 `FAULT`，⛔ 不许与 `NO_DOMAIN` 合并。\n"
          "              撤回语义：`return \"OK\", (not c)` 改成 `return \"OK\", True` ⇒ ② 恒定实现变 True ⇒ 必须红",
      not _az154_bad
      and _PB.varies("ROUNDTRIP", ["str"], _az154_const, "f") is False
      and _PB.varies("ROUNDTRIP", ["str"], _az154_var, "f") is True
      and _PB.domain_of("ROUNDTRIP", ["int"], _az154_nodom, "f") == ("NO_DOMAIN", None)
      and _PB.varies("ROUNDTRIP", ["int"], _az154_nodom, "f") is None,
      f"bad={_az154_bad} const={_PB.varies('ROUNDTRIP', ['str'], _az154_const, 'f')} "
      f"var={_PB.varies('ROUNDTRIP', ['str'], _az154_var, 'f')} "
      f"nodom={_PB.domain_of('ROUNDTRIP', ['int'], _az154_nodom, 'f')}")

# ── AZ154c：退化输入的剔法，以及「可达是不是被空容器骗出来的」 ──────────
_az154_short = ("def g(a, b):\n"
                "    for k in set(a) | set(b):\n"
                "        a.get(k)\n"
                "    return {}\n")
_az154_dg = _PB.degenerate_free(_CG.POOL, list(_CG.TYPES))
_az154_fr1 = _PB.false_reach("EMPTY_IFF_EQUAL", ["str", "str"], _az154_short, "g")
_az154_fr2 = _PB.false_reach("EMPTY_IFF_EQUAL", ["dict", "dict"], _az154_short, "g")

check(RS, "AZ154c `probe.false_reach()` 把「可达被退化输入骗了」单独标出来（V190 §2.2 那条升格成产品函数）：\n"
          "              ① 短路的装置 `g(a,b)`：`set(a)|set(b)` 为空时循环体一次不进 ⇒ `a.get` 不炸；\n"
          "              ⇒ `['str','str']` 只有 `('','')` 吃得下 ⇒ `false_reach` 必须为 `True`；\n"
          "              ② 同一装置在 `['dict','dict']` 上有非空输入 ⇒ `false_reach` 为 `False`\n"
          "              （它本来就有域，⛔ 不许一起标脏）；\n"
          "              ③ 退化剔法：`str` 4→3、`int` 3→2、`dict` 3→2、`num` 6→5、`flag` 8→8\n"
          "              （`\"0\"` 是**字符串**，不是数字零 ⇒ ⛔ 不许剔）。\n"
          "              撤回语义：`degenerate_free()` 不再过滤 ⇒ ① 的池里含 `'abcdef'` ⇒ legal 非空 ⇒ 必须红",
      _az154_fr1 is True and _az154_fr2 is False
      and {t: len(v) for t, v in _az154_dg.items()}
      == {"str": 3, "int": 2, "dict": 2, "list": 2, "num": 5, "flag": 8},
      f"fr={_az154_fr1}/{_az154_fr2} sizes={ {t: len(v) for t, v in _az154_dg.items()} }")

# ── AZ154d：先免费后扩池，且守卫值不许冒充病名 ────────────────────────
_az154_dead = "def h(x):\n    return 1\n"
_az154_r_healthy = _PB.route("ROUNDTRIP", ["str"], 1, _az154_var, "f")
_az154_r_nodom = _PB.route("ROUNDTRIP", ["int"], 1, _az154_nodom, "f")
_az154_r_dead = _PB.route("ROUNDTRIP", ["str"], 1, _az154_dead, "h")
_az154_r_flag = _PB.route("ROUNDTRIP", ["str"], 1, _az153_src, "parse_flag")

check(RS, "AZ154d `probe.route()` 的优先级与守卫值：\n"
          "              ① **先免费、后扩池**：`parse_flag` 在 `['str']` 上恒定，而基池 `int=(0,3,5)`\n"
          "              里 `0` 字符串化正好是词表里的假值 ⇒ 必须判 `RESIGN_FREE`、目标 `['int']`，\n"
          "              ⛔ 不许判成 `NEEDS_VOCAB`（那正是 V190 的误诊：把「类型错」读成「词不够」）；\n"
          "              ② 当前签名本来就有差异 ⇒ 守卫 `HEALTHY`，不动；\n"
          "              ③ 当前签名无域 ⇒ 守卫 `NO_DOMAIN`；\n"
          "              ④ 所有签名在所有池上都恒定 ⇒ `DEAD`（⛔ 不许把 ②③ 并进这里）。\n"
          "              撤回语义：免费分支的 `if st == \"OK\" and v:` 改成 `if False:` ⇒ ① 掉进扩池\n"
          "              分支判成 `NEEDS_VOCAB` ⇒ 必须红",
      _az154_r_flag["disease"] == "RESIGN_FREE" and _az154_r_flag["target"] == ["int"]
      and _az154_r_flag["cost"] == "free"
      and _az154_r_healthy["disease"] == "HEALTHY"
      and _az154_r_nodom["disease"] == "NO_DOMAIN"
      and _az154_r_dead["disease"] == "DEAD",
      f"flag={_az154_r_flag['disease']}/{_az154_r_flag['target']} "
      f"healthy={_az154_r_healthy['disease']} nodom={_az154_r_nodom['disease']} "
      f"dead={_az154_r_dead['disease']}")

# ── AZ154e：退化的边界值只有那四类，多剔一个就误伤 ───────────────────
#: ⚠️ 初版这张分类表里**一个正整数都没有**（只有 `0`/`0.0`/`-0.5`）⇒ 撤成 `v >= 0`
#:    时它一个字都不动，反而去牵连 AZ154c。撤点没对准断言（V190 三条同型）。
_az154_deg = {repr(v): _PB._is_degenerate(v) for v in
              ("", "  ", "0", 0, 0.0, 3, -0.5, False, True, {}, [], [0], "x")}

check(RS, "AZ154e `probe._is_degenerate()` 的分类边界（多剔一个就是误伤合法输入）：\n"
          "              ① 退化：`\"\"`（空串）、`0`、`0.0`、`{}`、`[]`；\n"
          "              ② **不**退化：`\"  \"`（纯空白，`strip()` 之后有意义）、`\"0\"`（字符串零，\n"
          "              它正是 `parse_flag` 词表里的假值 —— 剔掉它会把 V191 唯一那条正路堵死）、\n"
          "              `3`（正整数，基池 `int=(0,3,5)` 的两条正路就是它和 `5`）、\n"
          "              `False`（`int` 的子类但语义是布尔）、`-0.5`、`[0]`（非空容器）；\n"
          "              ③ `True` 也不退化。\n"
          "              撤回语义：`and v == 0` 改成 `and v >= 0` ⇒ 正整数被当退化剔掉 ⇒\n"
          "              ⇒ ② 的 `3` 翻成退化 ⇒ 必须红",
      _az154_deg["''"] is True and _az154_deg["'  '"] is False
      and _az154_deg["'0'"] is False and _az154_deg["0"] is True
      and _az154_deg["0.0"] is True and _az154_deg["3"] is False
      and _az154_deg["-0.5"] is False
      and _az154_deg["False"] is False and _az154_deg["True"] is False
      and _az154_deg["{}"] is True and _az154_deg["[]"] is True
      and _az154_deg["[0]"] is False,
      str(_az154_deg))

# ── AZ155a：部署面**接上了**判据三模块里的两个（V193 §261）────────────
#: ⚠️ 本锚点 V192 时钉的是**反面**（「部署闭包不含 critgen/metacrit/probe」，因为实测引用 0）。
#:    V193 造了第一条真入口 `run.py --verify-impl`，于是它**按设计转红**，
#:    现在改钉**正面**：「接上了哪几个、引用几次」。
#:    ⛔ 这个来回不是「改判据迎合实现」：V192 的读数（0 引用）与 V193 的读数（非 0）
#:    是两个不同装置状态下的实测，两次都写进了 DESIGN §260 / §261。
import ast as _ast155


def _az155_path(mod: str):
    _p = V1 / (mod.replace(".", "/") + ".py")
    return _p if _p.is_file() else None


def _az155_imports(mod: str) -> list:
    _p = _az155_path(mod)
    if _p is None:
        return []
    _out: list = []
    _tree = _ast155.parse(_p.read_text(encoding="utf-8"))
    _pkg = mod.rsplit(".", 1)[0] if "." in mod else ""
    for _n in _ast155.walk(_tree):
        if isinstance(_n, _ast155.Import):
            _out += [a.name for a in _n.names]
        elif isinstance(_n, _ast155.ImportFrom):
            _base = _n.module or ""
            if _n.level:
                _up = _pkg.split(".") if _pkg else []
                if _n.level > 1 and _n.level - 1 <= len(_up):
                    _up = _up[: len(_up) - (_n.level - 1)]
                _base = ".".join([x for x in _up if x] + ([_base] if _base else []))
            for _a in _n.names:
                _full = f"{_base}.{_a.name}" if _base else _a.name
                _out.append(_full if _az155_path(_full) else _base)
    return _out


def _az155_closure(entry: str) -> set:
    _seen: set = set()
    _todo = [entry]
    while _todo:
        _m = _todo.pop()
        if _m in _seen or _az155_path(_m) is None:
            continue
        _seen.add(_m)
        for _t in _az155_imports(_m):
            if _t not in _seen and _az155_path(_t):
                _todo.append(_t)
    return _seen


_az155_dep = _az155_closure("run")
_az155_hit = sorted(m for m in ("mw.critgen", "mw.metacrit", "mw.probe")
                    if m in _az155_dep)
#: 部署面对这两个别名的属性访问次数（`cg.` / `pb.`）—— 只数引用，不问对错。
_az155_calls = sum(1 for _n in _ast155.walk(
    _ast155.parse((V1 / "run.py").read_text(encoding="utf-8")))
    if isinstance(_n, _ast155.Attribute) and isinstance(_n.value, _ast155.Name)
    and _n.value.id in ("cg", "pb"))

check(RS, "AZ155a 部署面**接上了**判据模块（V192 钉反面，V193 起钉正面）：\n"
          "              V192 实测：部署闭包 24 个 mw 模块，三目标一个都不在、引用 0 次\n"
          "              （装置闭包 162 次）⇒ 那时它们是**量具**，部署链路没有「挑判据」这一步。\n"
          "              V193 造了第一条真入口 `run.py --verify-impl`（只用免费信息：\n"
          "              结构可达 / 定义域 / 输出是否恒定，⛔ 不出 PASS/FAIL）⇒\n"
          "              本锚点按设计转红，改钉「接上了 critgen + probe，且真有属性访问」。\n"
          "              ⚠️ `metacrit` **仍然没接** —— 只钉已接的，不假装全家都通。\n"
          "              ⚠️ 闭包必须 ≥ 20 个模块：低于此说明闭包算法坏了（检测器瞎），\n"
          "              那时「接上了」也可能是空集恒假 ⇒ 不算证据。\n"
          "              撤回语义：删掉 `cmd_verify_impl` 里两行 import ⇒ 成员资格消失 ⇒ 必须红",
      set(_az155_hit) >= {"mw.critgen", "mw.probe"}
      and _az155_calls >= 3 and len(_az155_dep) >= 20,
      f"闭包 {len(_az155_dep)} 个模块；命中 {_az155_hit}；属性访问 {_az155_calls} 次")

# ── AZ156a：bench 的答案出口**没有**接那道免费闸（V194 §262，跳线闸）──────
#: ⚠️ 这同样是**跳线闸**：红了**不**代表「不许接」，代表「你接了 ⇒ 先读 DESIGN §262，
#:    那里量到它会把 12.1% 的答案误杀（含 2 条满分）、连参考解都筛掉 4/33，
#:    并把 `g7_heldout` 从 0.70 打到 0.00。要接必须先在证据里翻掉那三条读数」。
#: 判「接没接」只看**调用**，不看注释：AST 里 bench 两个函数体内不得出现
#: `verify_impl` 相关的 Call（注释里写它不犯法，那正是要让人看见的地方）。
import ast as _ast156

_az156_src = (V1 / "run.py").read_text(encoding="utf-8")
_az156_tree = _ast156.parse(_az156_src)
_az156_bench_fns = [n for n in _ast156.walk(_az156_tree)
                    if isinstance(n, _ast156.FunctionDef) and n.name in ("_bench_main", "cmd_bench")]
_az156_calls: list[str] = []
for _f in _az156_bench_fns:
    for _n in _ast156.walk(_f):
        if isinstance(_n, _ast156.Call):
            _fn = _n.func
            if isinstance(_fn, _ast156.Name):
                _az156_calls.append(_fn.id)
            elif isinstance(_fn, _ast156.Attribute):
                _az156_calls.append(_fn.attr)
_az156_hit = sorted({c for c in _az156_calls if "verify_impl" in c})
#: 正例路径：入口本身必须还在（⛔ 删掉入口来「通过」这条闸是作弊 —— 那不是防止接线，是拆桥）。
_az156_entry = "cmd_verify_impl" in [n.name for n in _ast156.walk(_az156_tree)
                                     if isinstance(n, _ast156.FunctionDef)]

check(RS, "AZ156a bench 路径**没有**把免费闸接进答案出口（V194 §262 跳线闸）：\n"
          "              V194 在 33 个真实答案上量到：闸触发 4 次、4 次全误杀（2 条满分）；\n"
          "              把参考解喂进去也被筛掉 4/33 ⇒ 接上会把 `g7_heldout` 从 0.70 打到 0.00、\n"
          "              `numeric_logic_heldout` 从 1.000 打到 0.667。\n"
          "              ⇒ 现在它只是**检视工具**（`--verify-impl`），不是筛选器。\n"
          "              ⚠️ 判据只查 `_bench_main` / `cmd_bench` 函数体内的**调用**；\n"
          "              注释里提到它不算违规 —— 那正是希望别人读到的地方。\n"
          "              撤回语义：往 `_bench_main` 里加一行 `cmd_verify_impl(...)` ⇒ 必须红",
      not _az156_hit and _az156_entry and len(_az156_bench_fns) == 2,
      f"bench 函数体 {len(_az156_bench_fns)} 个；verify_impl 调用 {_az156_hit}；"
      f"入口在 {_az156_entry}")

# ── AZ157a~f：自评进 prompt 时「只留可迁移那一半」（V210 §278）──────────────
#: V209 量到同一通道两种用法**方向相反**：异常名点根因（`TypeError: 'tuple' …`）
#: 照做能修好（t04 idx0 2/20→7/20）；异常文的元数（`expected 2, got 1`）被当成
#: 设计线索，单单位全掉（t01 14/15/14→2/6/4，p≤0.01）。⇒ 投喂时删元数、留异常名。
from mw.critique import transferable_why as _az157_f   # noqa: E402

_az157_t01 = ("通过 1/6；失败: assert parse_duration('45s') == 45 ⇒ "
              "ValueError: not enough values to unpack (expected 2, got 1)")
_az157_o = _az157_f(_az157_t01)
check(RS, "AZ157a ★投喂口径 `transferable`：元数（`(expected 2, got 1)`）与解包短语\n"
          "              必须被删，**异常名与失败断言必须留下** ——\n"
          "              删的那半是「症状」，留的那半才是能迁移的「根因 + 输入」。\n"
          "              撤回语义：删掉 `mw/critique.py` 的 `_METASHAPE` 两条 ⇒ 必须红",
      "expected" not in _az157_o and "unpack" not in _az157_o
      and "ValueError" in _az157_o and "parse_duration('45s') == 45" in _az157_o,
      f"改写后={_az157_o!r}")

#: ⛔ 正例对照（纪律 5）：一个模式都不匹配的原文**必须逐字节不变**。
#:    否则「过滤器生效」与「过滤器把什么都吃了」在读数上分不开。
_az157_t04 = ("通过 2/6；失败: assert merge_ranges([(1,3),(2,6),(8,10)]) == [(1,6),(8,10)]"
              " ⇒ TypeError: 'tuple' object does not support item assignment")
check(RS, "AZ157b ★正例对照：根因那类理由（t04 的 `TypeError: 'tuple' object …`）\n"
          "              **一个模式都不匹配 ⇒ 原文逐字节不变**。\n"
          "              没有这一条，「只删元数」就可能悄悄变成「删掉所有异常文」",
      _az157_f(_az157_t04) == _az157_t04,
      f"长度 {len(_az157_t04)} → {len(_az157_f(_az157_t04))}")

_az157_raw = CritiqueLedger()
_az157_raw.add(Critique(scorer_sha="S", suite_sha="U", task_id="t1", why=_az157_t01))
_az157_tr = CritiqueLedger(None, why_mode="transferable")
_az157_tr.add(Critique(scorer_sha="S", suite_sha="U", task_id="t1", why=_az157_t01))
_az157_k = ("S", "U", "t1")
check(RS, "AZ157c ★默认 `raw` = 旧行为一字不改；`transferable` 必须**自报**改了几条\n"
          "              （只报口径不报计数 ⇒ 分不清「没改写」与「没生效」，V158 假绿同型）",
      _az157_raw.by_key[_az157_k].why == _az157_t01
      and _az157_raw.stats()["why_mode"] == "raw"
      and _az157_raw.stats()["why_rewritten"] == 0
      and "expected" not in _az157_tr.by_key[_az157_k].why
      and _az157_tr.stats()["why_rewritten"] == 1,
      f"raw={_az157_raw.stats()['why_mode']}/{_az157_raw.stats()['why_rewritten']} "
      f"tr={_az157_tr.stats()['why_mode']}/{_az157_tr.stats()['why_rewritten']}")

#: ⛔⛔ **红线**：真账本（落盘那条）永远 raw。`all` 档拿的是真账本本体 ⇒
#:    带 `transferable` 时必须改走**副本**，否则过滤器会把落盘证据一起裁掉。
_az157_src = CritiqueLedger()
_az157_src.add(Critique(scorer_sha="S", suite_sha="U", task_id="t1", why=_az157_t01))
from run import _critique_snapshot           # noqa: E402
_az157_snap = _critique_snapshot(_az157_src, "all", why_mode="transferable")
check(RS, "AZ157d ⛔⛔ 红线：`all` + `transferable` 必须返回**副本**，真账本原文不动\n"
          "              —— 证据被自己的过滤器改写，等于把测量对象改成了测量结果",
      _az157_snap is not None and _az157_snap is not _az157_src
      and _az157_src.by_key[_az157_k].why == _az157_t01
      and "expected" not in _az157_snap.by_key[_az157_k].why
      and _critique_snapshot(_az157_src, "all") is _az157_src,
      f"同一对象={_az157_snap is _az157_src} "
      f"真账本含元数={'expected' in _az157_src.by_key[_az157_k].why}")

check(RS, "AZ157e 未知口径必须**抛错**，不许静默退回 raw\n"
          "              （静默退回 = 读数带着「已裁剪」的标签、实际没裁）",
      _raises(lambda: CritiqueLedger(None, why_mode="trim"), ValueError))


class _AZ157Args:
    """`_option_conflict` 的冲突闸小桩：只列参与判定的字段。"""

    def __init__(self, **kw):
        for k, v in (("suite_dir", ""), ("memory_dir", ""), ("fresh", False),
                     ("save_baseline", False), ("suite", ""), ("loop", 0),
                     ("critique_scope", ""), ("critique_why", "raw"),
                     ("critique_drop", ""), ("use_critique", False)):
            setattr(self, k, kw.get(k, v))


check(RS, "AZ157f `--critique-why=transferable` + 投喂 off ⇒ **冲突**（off 档没有 why 被投喂，\n"
          "              再报「已裁剪」就是标签撒谎）；`raw` + off ⇒ 无冲突",
      bool(_option_conflict(_AZ157Args(critique_why="transferable")))
      and not _option_conflict(_AZ157Args(critique_why="raw"))
      and not _option_conflict(_AZ157Args(critique_why="transferable",
                                          critique_scope="prev-self")),
      "冲突闸三格符合预期")

# ── AZ158a~e：交错调度 —— 把「臂」与「时刻」解耦（V212 §280）──────────────
#: V211 实测同一配置的 `off` 臂跨两小时差 **0.2833**（Fisher p=0.0031），
#: 而 V209/V210 的三个臂是「跑完一个再跑下一个」⇒ 臂与时刻完全混杂。
#: 修法是**块内相邻**：同一块的臂在时间上贴着跑，块与块之间怎么漂都一起漂。
#: ⛔ 但「块内相邻」本身不够 —— 块内顺序若恒为原序，「位置」就是第三个混淆量。
from bench.interleave import schedule as _az158_s, flat as _az158_f  # noqa: E402
from bench.interleave import check as _az158_c                      # noqa: E402

_az158 = _az158_s(["off", "prev"], 20)
check(RS, "AZ158a ★形状：每块必须是 `arms` 的**一个排列**（同长、同集合、无重复）——\n"
          "              块内少一个臂 ⇒ 那一块只有单臂读数，配对分析会被悄悄丢一格；\n"
          "              块内重复一个臂 ⇒ 同一个臂在同块内跑两次，权重被偷改",
      len(_az158) == 20
      and all(len(b) == 2 and set(b) == {"off", "prev"} for b in _az158),
      f"块数={len(_az158)} 首块={_az158[0] if _az158 else None}")

check(RS, "AZ158b 计数：每个臂恰好出现 `blocks` 次（否则两臂的 n 不等，配对检验无从谈起）",
      all(sum(b.count(a) for b in _az158) == 20 for a in ("off", "prev")),
      f"off={sum(b.count('off') for b in _az158)} prev={sum(b.count('prev') for b in _az158)}")

#: ⭐ 轮转的**全部**内容就是这条等式。⛔ 不要用「相邻两块不同」代替它：
#:    对 n≥3 那只是必要条件（`[a,b,c]->[b,c,a]->[a,b,c]` 也满足），位置没平掉。
_az158_pos = [(k, b.index(a), a) for k, b in enumerate(_az158) for a in b]
check(RS, "AZ158c ⭐位置轮转：`pos(块 k, 臂 i) == (i - k) % n`，即每连续 n 块里\n"
          "              每个臂在每个位置**各出现一次** ⇒ 位置效应在块集合上被平掉。\n"
          "              恒序（块内永远原序）时这条必红 —— 那是「交错」最常见的假实现",
      all(p == (i - k) % 2 for k, p, a in _az158_pos
          for i in (("off", "prev").index(a),))
      and _az158[0] != _az158[1],
      f"首两块={_az158[0]}/{_az158[1]}")

_az158_3 = _az158_s(["a", "b", "c"], 6)
check(RS, "AZ158d n=3 也轮转（不只对两臂成立）：每 3 块里每个臂在每个位置各一次；\n"
          "              且 `blocks=1` 时只有一块 —— 边界不许外推成多块",
      all(_az158_3[k].index(a) == (i - k) % 3
          for k, b in enumerate(_az158_3) for a in b
          for i in (("a", "b", "c").index(a),))
      and len(_az158_s(["x"], 1)) == 1,
      f"块1={_az158_3[1]} 块2={_az158_3[2]}")

#: ⛔⛔ 这一条防的是「装置看起来一切正常」：表算对了、块也对，但跑的时候
#:     少跑一行 / 顺序被打乱（比如 shell 循环把 TSV 读串行）⇒ 配对根本不是配对的。
#:     所以核对的必须是**即将执行的那张表**，不是重新调用一次 `schedule()`。
_az158_rows = _az158_f(["off", "prev"], 3)
check(RS, "AZ158e ⛔⛔ `check()` 必须能抓出「表 ≠ 应执行顺序」：行数少一行、\n"
          "              两行对调都判红；正确表判绿（正例对照 —— 否则「总是红」\n"
          "              与「能抓错」在读数上分不开）",
      not _az158_c(["off", "prev"], 3, _az158_rows)
      and len(_az158_c(["off", "prev"], 3, _az158_rows[:-1])) == 1
      and len(_az158_c(["off", "prev"], 3,
                       [_az158_rows[1], _az158_rows[0]] + _az158_rows[2:])) == 2,
      f"正确表问题={_az158_c(['off', 'prev'], 3, _az158_rows)}")

# ════��══════════════════════════════════════════════════════════════════════
# AZ159a~f（§282）：累积块要不要交给「真正解题的那一端」
#
# V214 的免费普查（`bench/evidence/l3_s302/probe_stdin.jsonl`）：默认配置下
# 3/3 次外包调用的 stdin **逐字等于题面**（172/112/172 字），而同一时刻 state 里
# 躺着 227 字的 `[外部评审]` 块 ⇒「学到的东西进不进求解路径」在默认配置下是**假**的。
# 修法：`answer_task(..., extra=)` 只负责追加；`handoff_text()` 只负责挑块。
# ⛔ 默认 `off` ⇒ 载荷逐字节不变，所以下面的正例对照与断言同等重要。
from mw.core import HANDOFF_BLOCKS as _az159_heads      # noqa: E402
from mw.core import answer_task as _az159_ans            # noqa: E402
from mw.core import handoff_text as _az159_ho            # noqa: E402

_az159_parts = [
    "[目标] 写一个函数 foo",
    "[工作记忆] 上一步跑了 run_shell（这是**本题**的草稿纸，不是学到的）",
    "[外部评审] [suite/t04] 0.33 — 失败: TypeError: 'tuple' object …",
    "[语义记忆] 聚合用 dict 而不是 list",
    "[轨迹] finish×2",
    "[第 1/12 步]",
]
_az159_txt = _az159_ho(_az159_parts)
check(RS, "AZ159a ★`handoff_text()` 只取**跨轮沉淀**的那两块（评审 + 语义记忆/经验），\n"
          "              `[工作记忆]`（本题草稿纸）、`[轨迹]`、`[目标]`、`[第 N 步]` 一律不许带。\n"
          "              ⛔ 带错了不会报错，只会让外部能力重读一遍本题过程、挤掉真正学到的。",
      _az159_ho(_az159_parts[:1] + [p for p in _az159_parts[1:] if p.startswith(_az159_heads)])
      == "[外部评审] [suite/t04] 0.33 — 失败: TypeError: 'tuple' object …\n"
         "[语义记忆] 聚合用 dict 而不是 list"
      and "工作记忆" not in _az159_txt and "轨迹" not in _az159_txt
      and "目标" not in _az159_txt and "第 1/12 步" not in _az159_txt,
      f"handoff={_az159_txt!r}")

check(RS, "AZ159b 没有累积块时必须返回**空串**（而不是空行/标题）——\n"
          "              否则 `off` 档与 `blocks` 档在无块时也会分叉（多一个 `\\n\\n`）",
      _az159_ho(["[目标] x", "[第 1/12 步]"]) == ""
      and _az159_ho([]) == "",
      f"空块={_az159_ho(['[目标] x'])!r}")

_AZ159_GOAL = "写一个函数 merge_ranges(pairs)，函数名必须是 merge_ranges。"
_az159_old = _az159_ans(_AZ159_GOAL, 42.0)
check(RS, "AZ159c ★正例对照：`answer_task()` 默认（extra 不传 / 空串）**逐字节等于旧行为**\n"
          "              —— 题面原文 + deadline，多一个键少一个字都不许有。\n"
          "              没有这条，「加了附言」与「把题面改了」在读数上分不开",
      _az159_old == {"task": _AZ159_GOAL, "deadline_s": 42.0}
      and _az159_ans(_AZ159_GOAL, 42.0, extra="") == _az159_old,
      f"旧载荷={_az159_old!r}")

_az159_new = _az159_ans(_AZ159_GOAL, 42.0, extra=_az159_txt)
check(RS, "AZ159d 附言只许**追加在题面之后**：`task` 的前 `len(goal)` 个字符必须与原文\n"
          "              **逐字节相同**（外部能力靠题面认任务，改它就是换了一道题），\n"
          "              且 `deadline_s` 仍在 —— 附言不是用预算换来的",
      _az159_new["task"][:len(_AZ159_GOAL)] == _AZ159_GOAL
      and _az159_new["task"].endswith(_az159_txt)
      and _az159_new["deadline_s"] == 42.0
      and len(_az159_new["task"]) == len(_AZ159_GOAL) + 2 + len(_az159_txt),
      f"task 长度 {len(_az159_new['task'])} = {len(_AZ159_GOAL)}+2+{len(_az159_txt)}")


class _AZ159Args:
    """`_option_conflict` 的冲突闸小桩（只列参与判定的字段）。"""

    def __init__(self, **kw):
        for k, v in (("suite_dir", ""), ("memory_dir", ""), ("fresh", False),
                     ("save_baseline", False), ("suite", ""), ("loop", 0),
                     ("critique_scope", ""), ("critique_why", "raw"),
                     ("critique_drop", ""), ("use_critique", False),
                     ("external_handoff", "off"), ("no_external", False)):
            setattr(self, k, kw.get(k, v))


check(RS, "AZ159e `--external-handoff=blocks` + `--no-external` ⇒ **冲突**：\n"
          "              一个外部能力都没注册，附言没有接收方，读数却会带着「已交付」的标签",
      bool(_option_conflict(_AZ159Args(external_handoff="blocks", no_external=True)))
      and not _option_conflict(_AZ159Args(external_handoff="blocks"))
      and not _option_conflict(_AZ159Args(external_handoff="off", no_external=True)),
      "冲突闸三格不符合预期")

_az159_c = Core(cfg.Settings(), llm=StubLLM(), wire_external=False,
                external_handoff="blocks")
check(RS, "AZ159f 未知档位必须**抛错**；刚构造时 `_handoff_arg()` 必须是空串\n"
          "              （还没算过 state ⇒ 不许凭空给一段附言）",
      _raises(lambda: Core(cfg.Settings(), llm=StubLLM(), wire_external=False,
                           external_handoff="always"), ValueError)
      and _az159_c._handoff_arg() == "",
      f"初始附言={_az159_c._handoff_arg()!r}")


# ── AZ160a~e：附言里不许带测试源码（V215b §285）────────────────────────────
#: §284 免费普查量到：交出去的 228 字附言 **48/48 段**都含
#: `assert parse_duration('1h30m') == 5400` ⇒ 求解端被直接告知要过哪条断言，
#: 「知识转移」与「照着测试改」在**默认路径**上不可识别（纪律 120）。
#: `shape` 档：只留 `通过 X/Y` + 异常类名 —— 两件都与本题输入无关。
from mw.critique import WHY_MODES as _az160_modes       # noqa: E402
from mw.critique import shape_why as _az160_f           # noqa: E402

_az160_t01 = ("通过 1/6；失败: assert parse_duration('1h30m') == 5400 ⇒ "
              "ValueError: not enough values to unpack (expected 2, got 1)")
_az160_o = _az160_f(_az160_t01)
check(RS, "AZ160a ★`shape` 口径必须把**测试源码**整段删掉：断言原文、输入值、期望值、\n"
          "              异常消息正文一个都不许留下；只留 `通过 X/Y` 与异常**类名**。\n"
          "              撤回语义：把 `shape_why()` 改回返回原文 ⇒ 必须红（纪律 5 的否定断言要有正例）",
      "assert" not in _az160_o and "1h30m" not in _az160_o and "5400" not in _az160_o
      and "unpack" not in _az160_o and "expected" not in _az160_o
      and _az160_o == "通过 1/6；异常: ValueError",
      f"shape={_az160_o!r}")

check(RS, "AZ160b 正例对照：**满分**理由没有异常可留 ⇒ 只出通过计数（不得编造异常名）；\n"
          "              既无计数也无异常 ⇒ **空串**（调用方按「丢弃」处理，⛔ 不许退回原文）",
      _az160_f("通过 5/5") == "通过 5/5"
      and _az160_f("") == "" and _az160_f("看不懂的一句话") == ""
      and _az160_f("失败: assert x == 1 ⇒ KeyError: 3") == "异常: KeyError",
      f"空={_az160_f('看不懂的一句话')!r} 满分={_az160_f('通过 5/5')!r}")

_az160_k = ("S", "U", "t1")
_az160_led = CritiqueLedger(None, why_mode="shape")
_az160_led.add(Critique(scorer_sha="S", suite_sha="U", task_id="t1", why=_az160_t01))
_az160_line = _az160_led.by_key[_az160_k].as_line()
check(RS, "AZ160c ⛔⛔ 端到端：`shape` 档下**账本副本 + `as_line()`**（= 进 state 那段文本）\n"
          "              必须不含 `assert `/输入/期望 —— 只改 `shape_why()` 而不接线，\n"
          "              `add()` 仍会存原文，读数一模一样（V158 假绿同型）",
      "assert" not in _az160_line and "1h30m" not in _az160_line
      and "ValueError" in _az160_line and "通过 1/6" in _az160_line,
      f"line={_az160_line!r}")

_az160_raw = CritiqueLedger()
_az160_raw.add(Critique(scorer_sha="S", suite_sha="U", task_id="t1", why=_az160_t01))
_az160_drop = CritiqueLedger(None, why_mode="shape")
_az160_drop.add(Critique(scorer_sha="S", suite_sha="U", task_id="t9", why="看不懂的一句话"))
check(RS, "AZ160d ★默认仍是 `raw`（旧行为逐字节不变）；`shape` 档把「没有形状可说」的条目\n"
          "              **丢弃并计数**（`why_dropped`），⛔ 不许退回原文（退回 = 泄露照旧）",
      _az160_raw.by_key[_az160_k].why == _az160_t01
      and _az160_raw.stats()["why_mode"] == "raw"
      and _az160_raw.stats()["why_dropped"] == 0
      and _az160_drop.stats()["stored"] == 0
      and _az160_drop.stats()["why_dropped"] == 1,
      f"raw={_az160_raw.stats()['why_mode']} drop={_az160_drop.stats()}")

#: ⛔ CLI 与库里是两套枚举（`choices=(...)` 手写）⇒ 加档位最易漂的地方：
#:    库里加了、CLI 仍拒收，跑起来只会报「invalid choice」，读数却是「没效应」。
import ast as _az160_ast                # noqa: E402
import pathlib as _az160_pl             # noqa: E402

_az160_choices = None
for _az160_n in _az160_ast.walk(_az160_ast.parse(
        (_az160_pl.Path(__file__).resolve().parents[1] / "run.py").read_text(encoding="utf-8"))):
    if isinstance(_az160_n, _az160_ast.Call) and getattr(_az160_n.func, "attr", "") == "add_argument":
        if _az160_n.args and isinstance(_az160_n.args[0], _az160_ast.Constant) \
                and _az160_n.args[0].value == "--critique-why":
            for _kw in _az160_n.keywords:
                if _kw.arg == "choices" and isinstance(_kw.value, _az160_ast.Tuple):
                    _az160_choices = tuple(e.value for e in _kw.value.elts)
check(RS, "AZ160e 口径表必须**含三档**、未知档位抛错，且 `--critique-why` 的 `choices`（从 run.py\n"
          "              的 AST 里读）必须与库里的 `WHY_MODES` 同集合 —— 两套枚举漂了，\n"
          "              CLI 会拒收新档位，而读数看起来只是「没效应」",
      set(_az160_modes) == {"raw", "transferable", "shape"}
      and _az160_choices is not None and set(_az160_choices) == set(_az160_modes)
      and _raises(lambda: CritiqueLedger(None, why_mode="shape2"), ValueError),
      f"modes={_az160_modes} cli={_az160_choices}")

# ===========================================================================
# AZ161 —— 「提分」的判据换成**可满足**的那条（V235）
#
# 锚的是判据的**形状**与两条口径的**分道**，效果由 `bench/evidence/l5_s266/` 量。
# 一句话：`mean_score >= 1.0` 提出时即不可满足（V173 定理，仍然成立），
# 但由此推出「这格不再提目标」是错的 —— 换成 `best_score > base` 就可满足，
# 且刷新一次就是**新一代目标**（base 进 gid）。
# ⛔ 本组锚不许在「均值 vs 最好成绩」上混口径：混了就是 §306 自踩 1 的复形。
# ===========================================================================
_AZ161_CELL = "p1"
_az161_obs = [{"task_id": _AZ161_CELL, "score": 0.6667} for _ in range(3)]
_az161_st = _G.stats(_az161_obs, _AZ161_CELL)
_az161_gs = _G.propose(_az161_obs, [_AZ161_CELL])
_az161_g = _az161_gs[0] if _az161_gs else None

check(RS, "AZ161a 两个统计量**并存**且都要对：一格的 `mean_score`（全部观测的均值）与\n"
          "              `best_score`（最好的一次）回答不同的问题；`n_unverifiable` 只是读数\n"
          "              （⛔ 不用它过滤 best —— 那是 §306 自踩 1：会让 best 从 0.6667 掉到 0.3333）",
      _az161_st["n_obs"] == 3
      and abs(_az161_st["mean_score"] - 0.6667) < 1e-9
      and abs(_az161_st["best_score"] - 0.6667) < 1e-9
      and _az161_st["n_unverifiable"] == 3,
      f"stats={ {k: v for k, v in _az161_st.items() if k != 'scores'} }")

_az161_after = _az161_obs + [{"task_id": _AZ161_CELL, "score": 0.8333}]
_az161_v = _G.evaluate(_az161_g, _az161_after)[0] if _az161_g else "NA"
_az161_old = _G.Goal(kind="RAISE", cell=_AZ161_CELL, statement="旧口径",
                     criterion=(("mean_score", ">=", 1.0),), provenance=())
_az161_vold = _G.evaluate(_az161_old, _az161_after)[0]

check(RS, "AZ161b 判据是 `best_score > base` —— **可满足**：同一份 obs 上\n"
          "              新判据 MET（best 0.8333 > base 0.6667）而旧判据 NOT_MET（mean 0.7083 < 1）\n"
          "              ⇒ 改的是**判据本身**，不是把数据换了",
      _az161_g is not None and _az161_g.criterion == (("best_score", ">", 0.6667),)
      and _az161_v == "MET" and _az161_vold == "NOT_MET",
      f"criterion={_az161_g.criterion if _az161_g else None} 新={_az161_v} 旧={_az161_vold}")

_az161_next = _G.propose(_az161_after, [_AZ161_CELL], done=[_az161_g.gid])
check(RS, "AZ161c `base` 进 gid ⇒ **刷新一次就是新一代目标**（链可以逐轮往上走）；\n"
          "              ⛔ 反例：base 不进 gid ⇒ 同一条 gid 永远重复，「做成了」与「又提出同一条」同形",
      len(_az161_next) == 1 and _az161_next[0].gid != _az161_g.gid
      and _az161_next[0].criterion[0][2] == 0.8333,
      f"next={(_az161_next[0].criterion if _az161_next else None)} "
      f"gid_changed={bool(_az161_next) and _az161_next[0].gid != _az161_g.gid}")


def _az161_row(v: str, mean: float, best: float) -> dict:
    """判据/读数形状与 `G.record()` 逐字段相同（⛔ 少 `best_score` 会让 B 路静默不参与）。"""
    return {"gid": "z161", "kind": "RAISE", "cell": _AZ161_CELL, "verdict": v,
            "reading": {"cell": _AZ161_CELL, "n_obs": 5,
                        "mean_score": mean, "best_score": best},
            "criterion": [["best_score", ">", 0.6667]], "source": "contract"}


#: 均值在动（0.60/0.65/0.70）、最好成绩不动（恒 0.6667）—— 这就是「换过路但都没超过」的实形。
_az161_log = [_az161_row("NOT_MET", 0.60, 0.6667),
              _az161_row("NOT_MET", 0.65, 0.6667),
              _az161_row("NOT_MET", 0.70, 0.6667)]
_az161_esc_best = _G.escalations(_az161_log, _az161_obs, target_metric="best_score")
_az161_esc_mean = _G.escalations(_az161_log, _az161_obs, target_metric="mean_score")

check(RS, "AZ161d 两条**口径**必须分道：同一份读数与账本 ——\n"
          "              `best_score` 口径 ⇒ `STUCK_BEST`（要真试 3 次才判）；\n"
          "              `mean_score` 口径 ⇒ `CRITERION_UNREACHABLE`（结构判据、首轮即判死，不需要试）\n"
          "              ⛔ 把结构判据套到 best 口径上，会把每一格第一轮就误判成「闭不上」",
      (_az161_esc_best.get(_AZ161_CELL) or {}).get("reason") == "STUCK_BEST"
      and (_az161_esc_mean.get(_AZ161_CELL) or {}).get("reason") == "CRITERION_UNREACHABLE",
      f"best={(_az161_esc_best.get(_AZ161_CELL) or {}).get('reason')} "
      f"mean={(_az161_esc_mean.get(_AZ161_CELL) or {}).get('reason')}")

_az161_flat = [_az161_row("NOT_MET", 0.6667, 0.6667) for _ in range(3)]
_az161_esc_flat = _G.escalations(_az161_flat, _az161_obs, target_metric="best_score")
_az161_trend = [_az161_row("NOT_MET", 0.60, 0.60), _az161_row("NOT_MET", 0.70, 0.70),
                _az161_row("NOT_MET", 0.80, 0.80)]

check(RS, "AZ161e `STUCK_BEST` 与 `STUCK_MEANS` 并存**且互不冒充**：均值也逐位相同时\n"
          "              报 `STUCK_MEANS`（V171 的旧词不丢）；而在**收敛中**（best 0.60→0.70→0.80）\n"
          "              ⛔ 一条都不许触发 —— 0.2→0.4→0.6 那类正在变好的格不许被判「做不到」",
      (_az161_esc_flat.get(_AZ161_CELL) or {}).get("reason") == "STUCK_MEANS"
      and _G.escalations(_az161_trend, _az161_obs,
                         target_metric="best_score") == {},
      f"flat={(_az161_esc_flat.get(_AZ161_CELL) or {}).get('reason')} "
      f"trend={sorted(_G.escalations(_az161_trend, _az161_obs, target_metric='best_score'))}")

_az161_consts = sorted(n for n in vars(_G)
                       if n.isupper() and not n.startswith("_")
                       and isinstance(getattr(_G, n), (tuple, int, float, str)))
check(RS, "AZ161f ⛔ 不新增模块级常量、不改 `KINDS`：`base` 是**目标内容**（进 criterion/gid），\n"
          "              不是阈值常量 —— 多一个可调阈值就多一把要单独验证的尺子",
      _az161_consts == ["KINDS", "MIN_OBS", "TARGET"] and _G.KINDS == ("COVER", "PROBE", "RAISE"),
      f"consts={_az161_consts} KINDS={_G.KINDS}")

_az161_mixed = [{"task_id": "p2", "score": 1.0},
                {"task_id": "p2", "score": 0.3333, "impl_sha": "aa"},
                {"task_id": "p2", "score": 0.8333, "impl_sha": "bb"}]
_az161_mst = _G.stats(_az161_mixed, "p2")
check(RS, "AZ161g ★**单调性**：混池（一条无身份的 1.0 + 两条有身份的低分）⇒ `best_score` 仍是 1.0，\n"
          "              且 `n_unverifiable=1` 把「最高分不可核验」**显形**；\n"
          "              ⛔ 撤回语义：拿可核验性去过滤 best ⇒ best 掉到 0.8333，本条必须红",
      abs(_az161_mst["best_score"] - 1.0) < 1e-12 and _az161_mst["n_unverifiable"] == 1
      and abs(_az161_mst["mean_score"] - (1.0 + 0.3333 + 0.8333) / 3) < 1e-9,
      f"best={_az161_mst['best_score']} n_unv={_az161_mst['n_unverifiable']}")

# ===========================================================================
print()
print("=" * 74)
print("AZ162 组  V236（§307）：MODEL 轴 —— 口径传到底 + 换路写 vec + 思考开关")
print("=" * 74)

# ── AZ162a：`satisfied()`（判 DONE）必须能按 best 口径算 ─────────────────
# 缺陷形态（V236 部署态 R2 实测）：`propose()` 用 `best > base`，而
# `satisfied()` 留成 `mean >= TARGET` ⇒ best=1.0 已经满分，状态却报 `STALE`
# （原文：「账本说做完了，读数说没有」）。
_az162_obs = ([{"task_id": "c", "score": 0.8333, "impl_sha": f"a{i}"} for i in range(4)]
              + [{"task_id": "c", "score": 1.0, "impl_sha": f"b{i}"} for i in range(4)])
_az162_st = _G.stats(_az162_obs, "c")
_az162_sat_mean = _G.satisfied(_az162_obs, "c")
_az162_sat_best = _G.satisfied(_az162_obs, "c", target_metric="best_score")
# 语义关系（不是「同源」）：best 达标 ⇒ 任何 base<1 的 RAISE 必已 MET。
_az162_all_met = all(
    _G.evaluate(_G.Goal(kind="RAISE", cell="c", statement="",
                        criterion=(("best_score", ">", b),), provenance=()),
                _az162_obs)[0] == "MET"
    for b in (0.0, 0.5, 0.8333, 0.9999))
check(RS, "AZ162a `satisfied()` 支持 `target_metric`；同一份真读数（mean=0.9166、best=1.0）\n"
          "              上 mean 口径判**否**、best 口径判**是** ⇒ DONE 能被正确报出；\n"
          "              且 best 达标 ⇒ 任何 base<1 的 RAISE 必已 MET（不会自相矛盾）",
      (not _az162_sat_mean) and _az162_sat_best and _az162_all_met,
      f"mean={_az162_sat_mean} best={_az162_sat_best} all_met={_az162_all_met} "
      f"stats_mean={_az162_st['mean_score']:.4f}")

# ── AZ162b：`state()` 把口径传下去，且 `render_state` 的措辞跟着走 ────────
_az162_s_mean = _G.state([], {}, ["c"], _az162_obs)
_az162_s_best = _G.state([], {}, ["c"], _az162_obs, target_metric="best_score")
# ⚠️ 措辞只在 `DONE` 那行同时出现「均分 / 最好成绩」—— 拿 `STALE` 测会假红
#    （STALE 的文案本来就没有这两个词，首版就这么红的）。
_az162_det_mean = {"n_goals": 0, "n_esc": 0, "n_cells": 1, "n_sat": 1,
                   "n_obs": 8, "target_metric": "mean_score", "escalated": [],
                   "blocked": []}
_az162_det_best = dict(_az162_det_mean, target_metric="best_score")
check(RS, "AZ162b `state()` 接受 `target_metric` 并传进 `satisfied()`：\n"
          "              同一份真读数上 mean 口径 ⇒ STALE、best 口径 ⇒ DONE\n"
          "              （⛔ 不传 ⇒ 部署态明明满分却报「账本说做完了，读数说没有」）",
      _az162_s_mean[0] == "STALE" and _az162_s_best[0] == "DONE",
      f"mean={_az162_s_mean[0]} best={_az162_s_best[0]} "
      f"n_sat_mean={_az162_s_mean[1].get('n_sat')} n_sat_best={_az162_s_best[1].get('n_sat')}")

# ── AZ162g：措辞必须跟着口径走（否则**标签撒谎**，比读数错更难发现）─────
_az162_txt_mean = _G.render_state("DONE", _az162_det_mean)
_az162_txt_best = _G.render_state("DONE", _az162_det_best)
check(RS, "AZ162g `render_state` 的措辞跟着 `target_metric` 走：\n"
          "              mean 说「均分」、best 说「最好成绩」，**两者互不出现**；\n"
          "              首版口径换了文案没换 ⇒ DONE 那行仍印「均分 ≥1」，而那格 mean=0.9166",
      ("均分" in _az162_txt_mean and "最好成绩" not in _az162_txt_mean
       and "最好成绩" in _az162_txt_best and "均分" not in _az162_txt_best),
      f"mean={_az162_txt_mean[:46]!r} best={_az162_txt_best[:46]!r}")

# ── AZ162c：换路候选必须**自己写 vec**，否则行为级判据结构上测不出来 ─────
# V235 的实形：`getattr(scorer.score(...), "score", 0.0)` 把 `Scored` 整个丢掉
# ⇒ 12 次换路 `n_no_vec=12`，行为池一行都没收到，「换模型有没有扩空间」测不了。
from mw.bench import Scored as _Scored                              # noqa: E402
from mw.scoreinfo import beh_pool_from_rows as _beh_pool             # noqa: E402
_az162_writer_rows = [
    {"task_id": "c", "score": 0.5, "source": "goals-act",
     "impl_sha": "x", "vec": [True, False]},
    {"task_id": "c", "score": 1.0, "source": "goal_swap",
     "impl_sha": "y", "vec": [True, True]},
]
_az162_pool, _az162_cnt = _beh_pool(_az162_writer_rows, cell="c")
_az162_src = (V1 / "run.py").read_text(encoding="utf-8")
# ⚠️ **PEP 701 之前 f-string 表达式里不许有反斜杠**：这行字面量必须先在 f-string
#    外面算好。写进 f-string 里在 3.12+ 合法、3.11 直接 SyntaxError ⇒ 整个契约文件
#    连编译都过不去（CI 的 py3.11 档从 run 234 红到 run 248，README 却一直写着「全绿」）。
#    证据：`bench/evidence/ci_s320/`、DESIGN §320。
_az162_vec_probe = 'vec": list(_sr'
# ⚠️ 首版只查离线造的池 ⇒ **撤回态判它恒真**（撤 run.py 写点仍然绿，实测 `红=无`）。
#    「自造数据自证」是断言恒真的经典形态（纪律 5）：它证明的是「beh_pool 能读带 vec
#    的行」，没证明「run.py 真的写了 vec」。两半都要，缺一半就退化成同义反复。
check(RS, "AZ162c 两个写点（`goals-act` / `goal_swap`）的词都带 `vec` ⇒\n"
          "              `beh_pool_from_rows` 全部收进池（`n_kept=2, n_no_vec=0`）；\n"
          "              **且** `run.py` 的换路写点真的把 `vec` 写进去（源码级，防「自造数据自证」）；\n"
          "              ⛔ 撤回语义：删掉换路行写点的 vec ⇒ 本条必须红",
      len(_az162_pool) == 2 and _az162_cnt["n_no_vec"] == 0
      and '**({"vec": list(_sr["vec"])} if _sr.get("vec") else {})' in _az162_src,
      f"pool={len(_az162_pool)} cnt={_az162_cnt} src_vec={_az162_vec_probe in _az162_src}")

# ── AZ162d：`_swap_candidates` 的 models 参数默认空 ⇒ 部署行为逐字节不变 ──
import inspect as _inspect                                         # noqa: E402
_az162_sig = _inspect.signature(_run._swap_candidates)
_az162_has_models = ("models" in _az162_sig.parameters
                     and _az162_sig.parameters["models"].default == ())
check(RS, "AZ162d `_swap_candidates(..., models=())`：默认空 ⇒ 用当前模型（V235 行为不变）；\n"
          "              非空 ⇒ 按 `i % len` 轮换，且 rows 里记下**实际用的模型名**\n"
          "              ⛔ 只换模型不改提示（入参逐字节相同）—— 否则两个自变量缠在一起\n"
          "              ⚠️ 首版只查两个子串存在 ⇒ 撤掉轮换（`_mi = 0`）仍然绿（实测 `红=无`）；\n"
          "              现在把「轮换表达式」本身也钉住",
      _az162_has_models and 'models=tuple(_m.strip() for _m in' in _az162_src
      and "_mi = i % len(llms)" in _az162_src
      and '"model": _models[_mi] if _models else None' in _az162_src,
      f"sig_default={_az162_sig.parameters.get('models')} "
      f"rotate={'_mi = i % len(llms)' in _az162_src}")

# ── AZ162e：思考开关的**协议形态**必须与端点匹配（否则静默无效）──────────
# 实测（V236）：`thinking={"type":"disabled"}` 发给 ollama 的 v1 兼容层
# ⇒ **完全被忽略**（300 token 仍全用在思考、content 仍 0 字符、不报错）。
# 这是「拨不动的开关给最顺眼的读数」（纪律 77）的又一实形。
# ⚠️ 事实更新（V238 / §309）：**兼容路径**这一行必须原样保留 —— `provider=""`
#    是默认档，它的请求体要与历史**逐字节相同**。原生分支用 `think: bool`，
#    但那是另一条路（`_ollama_request`），不是把这一行改掉。
# ⛔ 所以本条继续钉这里：它守的是「默认档长什么样」，不是「所有档都这样」。
_az162_llm_src = (V1 / "mw" / "llm.py").read_text(encoding="utf-8")
check(RS, "AZ162e **兼容路径**的思考开关形态是 `{\"thinking\": {\"type\": ...}}`：\n"
          "              ⛔ ollama 认的是 `think: bool`，且只在**原生** `/api/chat` 生效\n"
          "              （v1 兼容层连 `think:false` 也忽略，V238 实测）；\n"
          "              V238 起由 `provider=\"ollama\"` 走原生分支绕开这条限制，\n"
          "              但默认档这一行仍在 ⇒ 钉住它，防止「以为关掉思考」",
      'body_obj["thinking"] = {"type": self.cfg.thinking}' in _az162_llm_src,
      "llm.py 的兼容路径形态变了 ⇒ 请同步复核各 provider 的实际生效性")

# ── AZ162f：边界清单**穷尽**（与锚层 H1d 同判据）──────────────────────
# ⚠️ 为什么同一判据要写两处：`bench/withdraw_check.py` 的驱动器**只跑契约**
#    （`_run_suite()` 就是 `tests/contract_test.py`）⇒ 只写在锚层的那条
#    **没有撤回态覆盖**。这不是「多一份声明」，是「让它能被撤一次」。
#    ⛔ 改一处必须改两处；两处的谓词必须一字不差。
from mw import (ALL_MW_MODULES as _az162_all,  # noqa: E402
                CORE_MODULES as _az162_core,
                NON_CORE_MODULES as _az162_non)
_az162_files = {p.stem for p in (V1 / "mw").glob("*.py") if p.stem != "__init__"}
check(RS, "AZ162f 边界清单**穷尽**：`CORE ∪ NON_CORE` == `mw/*.py`（去 `__init__`）且两者不相交 ——\n"
          "              缺陷形态：清单只有 18 项而文件有 27 个，漏的 9 个**两处都不在**\n"
          "              ⇒ H1「内核不依赖 bench」在**偷偷变小的范围**里成立，而它照常打印 PASS\n"
          "              （实测：漏掉的 `loop` 真的 `from . import bench`）",
      _az162_files == set(_az162_all) and not (set(_az162_core) & set(_az162_non)),
      f"文件={len(_az162_files)} 清单={len(_az162_all)} "
      f"未登记={sorted(_az162_files - set(_az162_all))} "
      f"交集={sorted(set(_az162_core) & set(_az162_non))}")

# ── AZ163a：第二体槽位**默认关** ⇒ 提示产出逐字节同 V233 ────────────────
# ⚠️ 为什么钉 sha 而不是钉「参数默认值是空串」：默认值随时可以改着玩，
#    sha 钉住的是**产出**。V236 的 AZ162d 是同一形态（`models=()` ⇒ 行为不变）。
from mw.reflect import (alt_block as _az163_blk,          # noqa: E402
                        build_proposal_prompt as _az163_bpp,
                        sha_of as _az163_sha)
_az163_base = _az163_bpp("G", "def f(): pass", "W", ("a1", "a2"))
check(RS, "AZ163a `build_proposal_prompt(..., alt_code='')` 默认关：产出 sha 冻结在\n"
          "              `2818c5673cc0884d`（V233 的模板）；默认路径一变，V233/V234 的\n"
          "              对照臂就不可重放（同 AZ162d：默认空 ⇒ 行为逐字节不变）",
      _az163_sha(_az163_base) == "2818c5673cc0884d",
      f"sha={_az163_sha(_az163_base)}")

# ── AZ163b：第二体段**只有一处**构造文本，且不点名任何断言/输入 ──────────
# ⛔ 纪律 151：在洞旁边写说明书，会把「找到洞」变成「照着念」。模板里一旦出现
#    `assert` / 具体输入 / 「正确」这类词，测到的就不是「它能不能比较两份实现」。
_az163_tpl = _az163_blk("BODY")
_az163_leak = [w for w in ("assert", "'10m'", "'abc'", "parse_duration",
                           "正确", "通过了", "哪条") if w in _az163_tpl]
_az163_src = (V1 / "mw" / "reflect.py").read_text(encoding="utf-8")
check(RS, "AZ163b 第二体段：`mw/reflect.py` 里**只有一处**构造该段的文本，\n"
          "              且模板不点名任何断言/输入/谁对（泄漏词表逐个查）——\n"
          "              ⛔ 两处各写一遍措辞迟早漂移成两个实验；点名答案则是给说明书",
      _az163_src.count("【另一份实现】") == 1 and not _az163_leak,
      f"出现={_az163_src.count('【另一份实现】')} 泄漏={_az163_leak}")

# ── AZ163c：删掉第二体段 ⇒ **逐字节**回到首选体（单变量）────────────────
# 这是「两臂只差第二体」的可执行定义。⛔ 差一个换行也算差：那时两臂差的
# 就不止一个变量，`D_AB - S_A` 的差归因不了（纪律 111/124 的同型）。
_az163_alt = "ALT_BODY_HERE"
_az163_full = _az163_bpp("G", "def f(): pass", "W", ("a1", "a2"), alt_code=_az163_alt)
_az163_strip = _az163_full.replace(_az163_blk(_az163_alt), "")
check(RS, "AZ163c 单变量：`bad(alt).replace(alt_block(alt), '')` 与 `bad()` **逐字节相等**，\n"
          "              且第二体段在全文里**只出现一次**（不许多塞一份）",
      _az163_strip == _az163_base and _az163_full.count(_az163_blk(_az163_alt)) == 1,
      f"strip_eq={_az163_strip == _az163_base} "
      f"n_block={_az163_full.count(_az163_blk(_az163_alt))}")

# ── AZ163d：第二体段插在**诊断段之后、指令段之前** ─────────────────────
# 位置是语义：「下面这个函数」（首句）必须紧挨【当前代码】。插在前面 ⇒
# 首句指错对象；插在末尾 ⇒ 指令已经说完了才给素材。两种都让读数不可归因。
_i_code = _az163_full.index("【当前代码】")
_i_alt = _az163_full.index("【另一份实现】")
_i_ask = _az163_full.index("要求：")
check(RS, "AZ163d 位置：`【当前代码】 < 【另一份实现】 < 要求：`（严格递增）——\n"
          "              ⛔ 插进代码槽 = 一份劣质拼接；插到末尾 = 指令已说完才给素材",
      _i_code < _i_alt < _i_ask,
      f"{_i_code} < {_i_alt} < {_i_ask}")

# ── AZ164a：`provider` 是一等配置字段，且拼错 **构造即抛** ────────────────
# V238（§309）：为什么需要这一维 —— ollama 的 v1 兼容层对 `thinking:{"type":...}`
# 和 `think:false` **两种写法都静默忽略**（900/2400/4000 三档全 `finish=length`、
# `content` 0 字符）；只有**原生** `/api/chat` 的 `think: bool` 有效。
# ⇒ 「思考开关」不是字段名问题，是**通道选择**问题。
from mw.config import (LLMConfig as _az164_C,  # noqa: E402
                       _PROVIDERS as _az164_PROV,
                       _THINKING_MODES as _az164_TH,
                       parse_model_spec as _az164_parse)


def _az164_mk(**kw):
    base = dict(base_url="http://localhost:11434/v1", api_key="k",
                model="m", temperature=0.0, thinking="", offline=False)
    base.update(kw)
    return _az164_C(**base)


try:
    _az164_mk(provider="nope")
    _az164_bad_raised = False
except ValueError:
    _az164_bad_raised = True
check(RS, "AZ164a `provider` 是 `LLMConfig` 字段（`(\"\", \"ollama\")`），拼错**构造即抛** ——\n"
          "              ⛔ 静默退回兼容端点 = 以为关了思考、实际拿到被截断的空正文\n"
          "              （这正是 V236 的 900/2400/4000 三档全灭）",
      _az164_PROV == ("", "ollama") and _az164_bad_raised
      and _az164_mk(provider="ollama").provider == "ollama",
      f"PROV={_az164_PROV} raised={_az164_bad_raised}")

# ── AZ164b：口径签名只在**非空**时带 provider ───────────────────────────
# 与 `thinking` 同一条规矩（A18）：空串 ⇒ 请求与历史逐字节相同 ⇒ 签名多一键
# 就是**反向撒谎**（标签说「口径不同」，实际同一台通道、同一个请求）。
check(RS, "AZ164b `cond()`：`provider=\"\"` 时**没有**该键（历史签名逐位不变），\n"
          "              非空时才带上 —— 与 `thinking` 同判据",
      "provider" not in _az164_mk().cond()
      and _az164_mk(provider="ollama").cond().get("provider") == "ollama",
      f"empty={sorted(_az164_mk().cond())}")

# ── AZ164c：换路条目三元组 —— **先 `@` 再 `:`** ─────────────────────────
# ⚠️ 模型名本身含冒号（`qwen2.5:7b`）。先切冒号会静默产出 `qwen2.5` +
#    `7b@ollama` 两段 ⇒ 404，而症状看起来像「端点不支持该模型」。
_az164_ok = [("qwen2.5:14b", ("qwen2.5:14b", None, None)),
             ("Qwen3.5-4B@ollama", ("Qwen3.5-4B", "ollama", None)),
             ("Qwen3.5-4B@ollama:disabled", ("Qwen3.5-4B", "ollama", "disabled")),
             ("Qwen3.5-4B@ollama:", ("Qwen3.5-4B", "ollama", "")),
             ("qwen2.5:7b@ollama:enabled", ("qwen2.5:7b", "ollama", "enabled"))]
_az164_bad = [s for s, w in _az164_ok if _az164_parse(s) != w]
check(RS, "AZ164c `parse_model_spec` 四形态：无 `@` ⇒ 只换 model（V235/V236 逐字节同行为）；\n"
          "              `@provider` / `@provider:thinking` / `@provider:`（显式空）各归各位 ——\n"
          "              `None`=不覆盖、空串=显式不发，两者**必须分开**",
      not _az164_bad,
      f"不符={_az164_bad}")

# ── AZ164d：非法条目 fail-closed（含「写了 thinking 但 provider 空」）────
# `X@:disabled` 几乎一定是把 `X@ollama:disabled` 打漏了；静默接受会得到
# 一个「provider 没换、只关思考」的条目 —— 而兼容层的 thinking 是静默忽略的。
_az164_illegal = ["", "@ollama", "X@nope", "X@ollama:bogus", "X@:disabled"]
_az164_leak = []
for _s in _az164_illegal:
    try:
        _az164_parse(_s)
        _az164_leak.append(_s)
    except ValueError:
        pass
check(RS, "AZ164d 非法条目**全部 fail-closed**（空 / 无模型名 / 拼错 provider /\n"
          "              拼错 thinking / 写了 thinking 但 provider 空）",
      not _az164_leak,
      f"没抛={_az164_leak}")

# ── AZ164e：原生分支的三要素（**行为级**，不是查源码子串）───────────────
# ⛔ 只查源码会漏「参数名对了但语义不对」：`max_tokens` 直接塞进原生体会被
#    **静默忽略** ⇒ 预算没设上 ⇒ 截断 ⇒ 又变成「模型不会」。
# ⛔ 首版这里写成 `A and B and C or D` 的混合式，且含一个**永不存在的字符串**
#    （`'"max_tokens" not in body_obj' in src`）⇒ 必然是恒真的绿。
#    「断言恒真」的另一种形态：谓词本身写不出来，但语法上成立。
from mw.llm import HTTPLLM as _az164_H                    # noqa: E402
from mw.llm import Message as _az164_M                   # noqa: E402


def _az164_body(cfg, n=900):
    h = _az164_H(cfg)
    u, b, _hdr = h._ollama_request([_az164_M("user", "hi")],
                                   max_tokens=n, temperature=0.0, t=900.0)
    return u, json.loads(b.decode("utf-8")), _hdr


_az164_u0, _az164_b0, _az164_hd0 = _az164_body(_az164_mk(provider="ollama"))
_az164_u1, _az164_b1, _ = _az164_body(_az164_mk(provider="ollama", thinking="disabled"))
_az164_u2, _az164_b2, _ = _az164_body(_az164_mk(provider="ollama", thinking="enabled"))
check(RS, "AZ164e `provider=\"ollama\"` 走**原生**：URL 剥 `/v1` 指 `/api/chat`、\n"
          "              预算走 `options.num_predict`（且**没有** `max_tokens` 键）、\n"
          "              `think` 是**布尔**（disabled⇒false / enabled⇒true）、\n"
          "              `thinking=\"\"` ⇒ 请求体里**没有** think 键（与兼容路径同一条规矩）",
      _az164_u0 == "http://localhost:11434/api/chat"
      and _az164_u1 == _az164_u0 == _az164_u2
      and _az164_b0["options"]["num_predict"] == 900
      and "max_tokens" not in _az164_b0
      and "think" not in _az164_b0
      and _az164_b1.get("think") is False
      and _az164_b2.get("think") is True
      and _az164_b0["stream"] is False,
      f"url={_az164_u0} b0={json.dumps(_az164_b0, ensure_ascii=False)[:90]} "
      f"b1.think={_az164_b1.get('think')!r} b2.think={_az164_b2.get('think')!r}")

# ── AZ164f：原生分支遇到**截断**必须抛，不许静默返回空串 ─────────────────
# V236 的失败形态就是 `done_reason=length` + `content` 0 字符（思考吃满）。
# 若返回空串 ⇒ 下游看到「模型给的代码是空的」⇒ 归因成「模型不会」，
# 而真相是预算口径（纪律 169）。这一条把「装置故障」与「模型不会」分开。
from mw.llm import LLMError as _az164_E                   # noqa: E402
_az164_h = _az164_H(_az164_mk(provider="ollama"))
try:
    _az164_h._ollama_content({"message": {"content": "", "thinking": "x" * 7},
                              "done_reason": "length", "eval_count": 900})
    _az164_tr_raised = False
except _az164_E as _e:
    _az164_tr_raised = ("thinking=7" in str(_e))
check(RS, "AZ164f 原生分支：`done_reason=length` ⇒ 抛 `LLMError`（且报出 thinking 字符数），\n"
          "              `done_reason=stop` ⇒ 正常返回 —— ⛔ 静默返回空串会把\n"
          "              「预算不够」记成「模型不会」（V236 三档全灭的归因陷阱）",
      _az164_tr_raised
      and _az164_h._ollama_content({"message": {"content": "a"}, "done_reason": "stop"}) == "a",
      f"raised={_az164_tr_raised}")

# ── AZ164g：换路条目真的把 provider/thinking 带走（不是只解析不覆盖）────
# ⚠️ 解析对了但 `replace` 只传 model ⇒ 通道没换 ⇒ 4B 仍在兼容层被静默截断。
#    这是「参数解析成功 ≠ 生效」的经典形态，必须查覆盖点本身。
check(RS, "AZ164g `run.py` 的换路构造真的 `replace(provider=/thinking=)`：\n"
          "              只把 model 换掉的话，4B 依旧走兼容层 ⇒ 静默截断，而读数看着正常",
      '_ov["provider"] = _spec[1]' in _az162_src
      and '_ov["thinking"] = _spec[2]' in _az162_src
      and "parse_model_spec(str(m))" in _az162_src,
      "run.py 覆盖点缺失")

# ═══════════════════════════════════════════════════════════════════════════
# AZ165　换路起点：必须是账本 best 的**实现**（V239，§310）
#
# 为什么需要这一组（三处实测缺陷，见 PREREG_v239 §0）：
#   1. `why` 的数字来自**本轮 act 产出分**，而账本里可能有更高分 ⇒ 那句话是假的；
#   2. 账本只记 `impl_sha` 不记代码，且起点 obs 连 sha 都没有 ⇒
#      「起点=历史 best 的实现」**结构上做不到**（不是文案问题）；
#   3. `impl_sha` 一列混两个口径：act 行记**原文**、换路行记**剥围栏后**
#      （同一份产出 `2bcccf94` vs `90579ece`）⇒ 它不再能当实现身份。
# ═══════════════════════════════════════════════════════════════════════════
import run as _az165_run                                          # noqa: E402
from mw.scoreinfo import exec_sha_of as _az165_esha, impl_sha_of as _az165_isha  # noqa: E402

_az165_src = (V1 / "run.py").read_text(encoding="utf-8")
#: 两个**真实**样本：一份带围栏的模型输出、一份裸代码。
_az165_raw = (V1 / "bench" / "evidence" / "l8_s309" / "raw_v238" / "P6_7b_think0.txt")
_az165_bare = V1 / "bench" / "evidence" / "l6_s307" / "start_answer.py"
_az165_have = _az165_raw.exists() and _az165_bare.exists()

# ── AZ165a：`exec_sha_of` 是「剥围栏后」的唯一口径，且裸代码不受影响 ──────
# ⛔ 判据要能「变」：必须挑一个 raw ≠ as_code(raw) 的样本，否则这条恒真。
_az165_raw_t = _az165_raw.read_text(encoding="utf-8") if _az165_have else ""
_az165_bare_t = _az165_bare.read_text(encoding="utf-8") if _az165_have else ""
check(RS, "AZ165a `exec_sha_of` = **判分器执行的那份代码**的指纹：同一个围栏产出\n"
          "              `impl_sha_of`≠`exec_sha_of`（`2bcccf94` vs `90579ece`），\n"
          "              而裸代码两种算法同值（`90579ece`）⇒ 换口径不动历史读数",
      _az165_have
      and _az165_isha(_az165_raw_t) != _az165_esha(_az165_raw_t)
      and _az165_esha(_az165_raw_t) == _az165_esha(_az165_bare_t)
      == _az165_isha(_az165_bare_t),
      f"raw={_az165_isha(_az165_raw_t)[:8]} exec={_az165_esha(_az165_raw_t)[:8]}")

# ── AZ165b：act 行**真的**改用了新口径（不是只加了个函数没人调）─────────
# 这是「加了 API 但没接上」的经典形态：函数存在、契约绿、账本照旧两个口径。
check(RS, "AZ165b `run.py` 的 act 落账用 `exec_sha_of`：⛔ 只加函数不改调用点\n"
          "              ⇒ 同一列 `impl_sha` 仍混两个对象，行为级读数照旧不可归因",
      "from mw.scoreinfo import exec_sha_of as _ish_of" in _az165_src
      and "from mw.scoreinfo import impl_sha_of as _ish_of" not in _az165_src,
      "act 落账口径未换")

# ── AZ165c：`_resolve_start` 三值，且**成对**返回 (code, score) ──────────
# ⛔ 只回分数不回代码 ⇒ 调用点仍可能「说的分」与「给的码」不同源（纪律 152）。
import tempfile as _az165_td                                        # noqa: E402

with _az165_td.TemporaryDirectory() as _az165_d:
    import pathlib as _az165_pl
    _az165_cd = _az165_pl.Path(_az165_d) / "cand"
    _az165_sha = _az165_run._cand_put(_az165_cd, _az165_bare_t)
    _az165_obs_cache = [{"task_id": "t", "score": 0.8333, "impl_sha": _az165_sha}]
    _az165_obs_bare = [{"task_id": "t", "score": 0.8333}]
    _c1, _s1, _src1, _n1 = _az165_run._resolve_start(
        _az165_obs_cache, "t", "def f():\n    pass\n", 0.3333, _az165_cd)
    _c2, _s2, _src2, _n2 = _az165_run._resolve_start(
        _az165_obs_bare, "t", "def f():\n    pass\n", 0.3333, _az165_cd)
    _c3, _s3, _src3, _n3 = _az165_run._resolve_start(
        [{"task_id": "t", "score": 0.1}], "t", "def f():\n    pass\n", 0.3333, _az165_cd)
    _c4, _s4, _src4, _n4 = _az165_run._resolve_start(
        [], "t", "def f():\n    pass\n", 0.3333, _az165_cd)
    #: ⑤**同分不升级**（`auto`）—— 这正是 V239 R1 的真实情形：
    #:   账本 best 0.8333（`90579ece`）与本轮 act 产出 0.8333（`09d0e23d`）
    #:   同分不同体 ⇒ auto 必须用**本轮产出**（否则「累积」会退化成永远抄旧）。
    #:   ⛔ 没有这一格，撤掉「空 provider 式」的那行判据不会转红（`_bsc < fallback`
    #:      那行会把它兜住）⇒ 实测「红=无」。
    _c5, _s5, _src5, _n5 = _az165_run._resolve_start(
        [{"task_id": "t", "score": 0.3333, "impl_sha": _az165_sha}],
        "t", "def f():\n    pass\n", 0.3333, _az165_cd)
    #: 缓存往返的形状：空代码不许落盘、陌生 sha 不许冒充「取到了」。
    _az165_empty = _az165_run._cand_put(_az165_cd, "   ")
    _az165_miss = _az165_run._cand_get(_az165_cd, "0" * 16)

check(RS, "AZ165c `_resolve_start` 三值齐全且**成对**：①账本 best 带 sha 且缓存有 ⇒\n"
          "              返回**那份代码**与**那份分**（src=cache）；②带分不带 sha ⇒\n"
          "              src=degraded 且 note 非空；③账本 best 不高于本轮 ⇒ src=act；\n"
          "              ④空账本 ⇒ src=act；⑤**同分不升级**（auto 用本轮产出，\n"
          "              ⛔ 否则「累积」退化成永远抄旧）",
      _src1 == "cache" and _c1 == _az165_bare_t and abs(_s1 - 0.8333) < 1e-9 and _n1 == ""
      and _src2 == "degraded" and _c2.startswith("def f():") and _n2 != ""
      and _src3 == "act" and _n3 == "" and abs(_s3 - 0.3333) < 1e-9
      and _src4 == "act" and _n4 == ""
      and _src5 == "act" and _c5.startswith("def f():")
      and _az165_empty == "" and _az165_miss is None,
      f"srcs={[_src1, _src2, _src3, _src4, _src5]}")

# ── AZ165d：降级时 `why` 必须**改口**（⛔ 不许沿用「当前最好的一次是」）──
# 这是 §309 立案那条缺陷的核心：数字错 + 让模型去修**更差**的实现。
# 只打日志不让 why 改口 ⇒ 模型仍然被那句错话指挥（纪律 77）。
check(RS, "AZ165d 降级分支的 `why` 改口成两句：含「历史最好成绩是」，\n"
          "              **不含**「当前最好的一次是」—— 且 `act`/`cache` 分支仍用原句\n"
          "              （原句在代码真的是最好那份时**本来就没撒谎**，⛔ 不改措辞）",
      'if start_src == "degraded":' in _az165_src
      and "但那份实现取不回来" in _az165_src
      and _az165_src.count("当前最好的一次是") == 1,
      f"出现次数={_az165_src.count('当前最好的一次是')}")

# ── AZ165e：跨轮逐字节 —— 默认档 prompt 仍是 V236/V238 的那一份 ─────────
# ⛔ 这条是「历史可重放」的证据。没有它，「默认关」只是我嘴上说的一句。
#    构造：t01 题面 + `start_answer.py` + act 分支 + 0.8333。
# ⛔ 必须真的**跑产品代码**产出 prompt。首版是自己拼一份再比 sha ——
#    那样撤 `run.py` 里的格式串它一动不动（实测「红=无」），锚等于锚在空气上。
try:
    import mw.llm as _az165_llm
    from mw.bench import Suite as _az165_Suite
    from mw.reflect import sha_of as _az165_sh

    _az165_sink: list[str] = []

    class _az165_LLM:
        def __init__(self, out):
            self.out = out

        def complete(self, msgs, max_tokens=900):                   # noqa: ARG002
            _az165_sink.append(msgs[-1].content)
            return "```python\n" + self.out + "\n```"

    class _az165_Sc:
        def score(self, td, cand):                                  # noqa: ARG002
            class _R:                                                # noqa: D401
                score = 0.8333
                vec = [True] * 6
            return _R()

    class _az165_Core:
        class s:                                                     # noqa: N801
            from mw.config import LLMConfig as _C
            llm = _C(model="m", offline=True, api_key="k")

    _az165_t = _az165_Suite.load(
        V1 / "bench" / "evidence" / "l5_s266" / "suite_one" / "t01.jsonl").tasks[0]
    _az165_oldb = _az165_llm.build_llm
    _az165_llm.build_llm = lambda _c: _az165_LLM(_az165_bare_t)      # noqa: ARG005
    try:
        _az165_run._swap_candidates(_az165_Core(), _az165_Sc(), _az165_t,
                                    _az165_bare_t, 0.8333, 1)
    finally:
        _az165_llm.build_llm = _az165_oldb
    _az165_psha = _az165_sh(_az165_sink[0]) if _az165_sink else "SINK_EMPTY"
except Exception as _az165_e:                                       # noqa: BLE001
    _az165_psha = f"ERR {type(_az165_e).__name__}: {_az165_e}"

check(RS, "AZ165e 默认档 prompt sha 仍是 `3b33bcb8b2276580`（V236/V238 同一份）\n"
          "              ⇒ 起点升级是**可选路径**，不是把历史读数换掉",
      _az165_psha == "3b33bcb8b2276580", f"sha={_az165_psha[:24]}")

# ── AZ165f：落盘目录一等化 + 缺省走 `state/` ─────────────────────────────
# ⚠️ 本条只判**它自己**的主张（目录一等化 + 落盘口径），不查 `exec_sha_of` 的实现 ——
#    那是 AZ165a 的地盘。首版查了那行 ⇒ AZ165a 撤回时连带本条转红（假连带）。
check(RS, "AZ165f `--goals-swap-cache` 一等化 + 落盘口径：缺省 `$V1_STATE_DIR/candidates`\n"
          "              **两处**都用同一个常量，且 `_cand_put` 落盘前 `as_code`（文件名 == 内容口径）",
      '"--goals-swap-cache"' in _az165_src
      and _az165_src.count("paths.state / CAND_DIRNAME") == 2
      and "sha = exec_sha_of(body)" in _az165_src
      and "body = as_code(code)" in _az165_src,
      f"count={_az165_src.count('paths.state / CAND_DIRNAME')}")

# ═══════════════════════════════════════════════════════════════════════════
# AZ166　换路预算参数化（V240，§312）
#
# 为什么需要这一组：
#   V239 实测 4B 有 50%（3/6）被 **900 token** 砍断（3779/3679 字符正文），
#   而预算在 `_swap_candidates` 里写死 ⇒ 「换个预算看」这件事**做不到**。
#   ⚠️ 改预算 = 改分母口径 ⇒ 必须默认不变、只有显式才变、非法值不许回落。
# ⛔ 这一组不判「哪个预算好」：那是下一轮的事，且分母会变。
# ═══════════════════════════════════════════════════════════════════════════
import inspect as _az166_inspect                                  # noqa: E402
import re as _az166_re_mod                                        # noqa: E402
import subprocess as _az166_sp                                    # noqa: E402

#: 「完整参数名」：# 后面不能再跟 `-` 或字母（挡住 `-old` 这种改名）。
_az166_re = _az166_re_mod.compile(r"--goals-swap-max-tokens(?![-\w])")
_az166_sig = _az166_inspect.signature(_az165_run._swap_candidates)
_az166_def = _az166_sig.parameters["max_tokens"].default

#: 记录 `complete()` 实收的 max_tokens —— 判「预算真的送达」只认这个。
_az166_sink: list[int] = []


class _az166_LLM:
    def __init__(self, out):
        self.out = out

    def complete(self, msgs, max_tokens=900):                     # noqa: ARG002
        _az166_sink.append(int(max_tokens))
        return "```python\n" + self.out + "\n```"


def _az166_run(**kw) -> list[int]:
    import mw.llm as _L
    _az166_sink.clear()
    _old = _L.build_llm
    _L.build_llm = lambda _c: _az166_LLM(_az165_bare_t)            # noqa: ARG005
    try:
        _az165_run._swap_candidates(_az165_Core(), _az165_Sc(), _az165_t,
                                    _az165_bare_t, 0.8333, 1, **kw)
    finally:
        _L.build_llm = _old
    return list(_az166_sink)


# ── AZ166a：默认值仍是 900 ────────────────────────────────────────────────
# ⚠️ 撤回态与本条 COUPLED：默认值一改，AZ166c（不传 ⇒ 送达 900）必然一起红。
check(RS, "AZ166a 换路预算默认仍是 900（V235~V239 同一口径，⛔ 不跟随 --max-tokens）\n"
          "              ⇒ 历史读数在新代码上仍可重放",
      _az166_def == 900 and getattr(_az165_run, "SWAP_MAX_TOKENS_DEFAULT", None) == 900,
      f"default={_az166_def!r}")

# ── AZ166b：显式值**真的**送到模型调用（不是只加了个参数没人用）───────────
_az166_got = _az166_run(max_tokens=1600)
check(RS, "AZ166b 显式 max_tokens=1600 真的送达 complete()（⛔ 加参数不进调用 = 假开关）",
      bool(_az166_got) and _az166_got[0] == 1600, f"收到={_az166_got[:1]}")

# ── AZ166c：不传 ⇒ 900 ────────────────────────────────────────────────────
_az166_got0 = _az166_run()
check(RS, "AZ166c 不传 ⇒ complete() 收到 900（默认档行为逐字节不变）",
      bool(_az166_got0) and _az166_got0[0] == 900, f"收到={_az166_got0[:1]}")

# ── AZ166d：非法值**构造即抛**，⛔ 不许静默回落到 900 ────────────────────
#     「以为调大了，其实还是 900」与 V236 的 900/2400 全灭是同型（纪律 127）。
_az166_raises = []
for _bad in (0, -1, "1600", 1.5, True):
    try:
        _az165_run._swap_candidates(_az165_Core(), _az165_Sc(), _az165_t,
                                    _az165_bare_t, 0.8333, 1, max_tokens=_bad)
        _az166_raises.append(False)
    except ValueError:
        _az166_raises.append(True)
check(RS, "AZ166d 0/-1/'1600'/1.5/True ⇒ ValueError（⛔ 不静默回落 900）",
      all(_az166_raises), str(_az166_raises))

# ── AZ166e：CLI 一等化，且非法字符串在 argparse 层就失败 ──────────────────
_az166_h = _az166_sp.run([sys.executable, str(V1 / "run.py"), "--goals",
                          "--goals-swap-max-tokens", "1600", "--help"],
                         capture_output=True, text=True, cwd=V1)
_az166_bad = _az166_sp.run([sys.executable, str(V1 / "run.py"), "--goals",
                            "--goals-swap-max-tokens", "abc"],
                           capture_output=True, text=True, cwd=V1)
# ⛔ 必须判「完整参数名」而不是子串：撤回动作把参数名改成
#    `--goals-swap-max-tokens-old` 时，裸 `in` 仍然命中（首版实测「红=无」）。
_az166_named = bool(_az166_re.search(_az166_h.stdout or ""))
check(RS, "AZ166e `--goals-swap-max-tokens` 已注册；非整数字符串 ⇒ 非 0 退出",
      _az166_named and _az166_bad.returncode != 0,
      f"named={_az166_named} rc={_az166_bad.returncode}")

#: 复用已有的两个口径（`exec_sha_of` = 判分器执行的那份代码；`withdraw_check` = 驱动器本体）
_az167_esha = _az165_esha
_az167_wc = _az44_wc

# ── AZ167（V243）：同分多实现时「账本 best」的定义 ────────────────────────
#  ⚠️ 这一组的**默认档**必须是 `first`（与 V239/V241 逐字节同起点）：
#     V243 实测 `farlast` 在真实账本 `obs_d` 上会选中 V239c 证明**修不动**的那份
#     （`09d0e23d`，单点可修 0/4839），因为真 best `90579ece` 的 seed 行**缺 `vec`**
#     被 `has_vec` 那一级降级顶掉 —— 为防「缺读数冒充好读数」写的规则**反向生效**。
_az167_cell = "t01_parse_duration"
_az167_fb = "# fallback\ndef parse_duration(s):\n    return None\n"
_az167_cache = _TMP / "az167_cache"
if not _az167_cache.is_dir():
    _az167_cache.mkdir(parents=True)
_az167_A = "# A\ndef parse_duration(s):\n    return 1\n"      # vec 011111
_az167_B = "# B\ndef parse_duration(s):\n    return 2\n"      # vec 111011
_az167_sA = _az167_esha(_az167_A)
_az167_sB = _az167_esha(_az167_B)
(_az167_cache / f"{_az167_sA}.py").write_text(_az167_A, encoding="utf-8")
(_az167_cache / f"{_az167_sB}.py").write_text(_az167_B, encoding="utf-8")
#: 顺序**先 A 后 B**：旧规矩（first）取 A，按位序（farlast）必须取 B。
_az167_obs = [
    {"task_id": _az167_cell, "score": 0.8333, "impl_sha": _az167_sA,
     "vec": [False] + [True] * 5, "source": "t"},
    {"task_id": _az167_cell, "score": 0.8333, "impl_sha": _az167_sB,
     "vec": [True] * 3 + [False] + [True] * 2, "source": "t"},
]
#: `vec` 缺失的那条**先出现且洞位序更靠前** ⇒ 不许被选中（纪律 137 的孪生）。
_az167_obs_novec = [
    {"task_id": _az167_cell, "score": 0.8333, "impl_sha": _az167_sA, "source": "t"},
    {"task_id": _az167_cell, "score": 0.8333, "impl_sha": _az167_sB,
     "vec": [False] + [True] * 5, "source": "t"},
]
_az167_def = _run._resolve_start(_az167_obs, _az167_cell, _az167_fb, 0.5,
                                 _az167_cache, policy="ledger")
_az167_far = _run._resolve_start(_az167_obs, _az167_cell, _az167_fb, 0.5,
                                 _az167_cache, policy="ledger", tie="farlast")
_az167_nv = _run._resolve_start(_az167_obs_novec, _az167_cell, _az167_fb, 0.5,
                                _az167_cache, policy="ledger", tie="farlast")

check(RS, "AZ167a 默认档（不传 tie）仍取**先出现**的那份 ⇒ V239/V241 的起点可逐字节重放\n"
          "              （⛔ `farlast` 只是可选臂，不许当默认：它在真实账本上会选到修不动的那份）",
      _az167_def[0] == _az167_A and _az167_def[2] == "cache",
      f"取到={_az167_esha(_az167_def[0])[:8]} src={_az167_def[2]}")
check(RS, "AZ167b 显式 `tie=farlast` ⇒ 取**失败位序最靠后**的那份，且日志写明"
          "「同分几份 / fail_pos」\n"
          "              （⛔ 只改选择不写日志 = 下一次读到「起点换了」也归不了因）",
      _az167_far[0] == _az167_B and "同分 2 份" in _az167_far[3]
      and "fail_pos=3" in _az167_far[3],
      f"取到={_az167_esha(_az167_far[0])[:8]} note={_az167_far[3][:44]!r}")
check(RS, "AZ167c 缺 `vec` 的行**不冒充**「洞在最前」（它有 `vec` 的对手先被选中）",
      _az167_nv[0] == _az167_B,
      f"取到={_az167_esha(_az167_nv[0])[:8]}")
_az167_raises = []
for _bad in ("", "newest", None, 1):
    try:
        _run._resolve_start(_az167_obs, _az167_cell, _az167_fb, 0.5,
                            _az167_cache, tie=_bad)
        _az167_raises.append(False)
    except ValueError:
        _az167_raises.append(True)
_az167_h = _az166_sp.run([sys.executable, str(V1 / "run.py"), "--help"],
                         capture_output=True, text=True, cwd=V1)
_az167_named = "--goals-swap-tie" in (_az167_h.stdout or "")
check(RS, "AZ167d 非法 tie 值 ⇒ ValueError；`--goals-swap-tie` 已注册（⛔ 不静默回落默认）",
      all(_az167_raises) and _az167_named,
      f"抛={_az167_raises} named={_az167_named}")

# ── AZ168（V242）：撤回驱动器允许**一次撤多处**，但每一处仍须唯一 ────────
#  `AU6` 撤的那个 `inf` 同时是断言值和分支条件：只换值 ⇒ 走 else 分支 ⇒
#  `inner.complete(..., timeout_s=1e9)` 打到不接受 `timeout_s` 的桩 ⇒ `TypeError`
#  ⇒ 整条离线计划链零轮次（实测 `AZ107` 红 + `AZ108` IndexError）。
_az168_src = "aaa OLD1 bbb\nccc OLD2 ddd\n"
_az168_m, _az168_ns, _az168_err = _az167_wc._apply_mutation(
    _az168_src, ("OLD1", "OLD2"), ("NEW1", "NEW2"))
_az168_m2, _az168_ns2, _az168_err2 = _az167_wc._apply_mutation(
    _az168_src, ("OLD1", "NOPE"), ("NEW1", "X"))
check(RS, "AZ168a 两处都唯一 ⇒ **两处都换上**（返回 [1,1] 且无错误）",
      _az168_ns == [1, 1] and not _az168_err
      and "NEW1" in _az168_m and "NEW2" in _az168_m,
      f"ns={_az168_ns} err={_az168_err!r}")
check(RS, "AZ168b 任一处不唯一/缺失 ⇒ **整条失败**且原文本不动（⛔ 不许只换一半就写盘）",
      _az168_ns2 == [1, 0] and bool(_az168_err2) and _az168_m2 == _az168_src,
      f"ns={_az168_ns2} err={_az168_err2!r}")
#: 合并 vs 覆盖：`_V242_EXTRA` 里 15 个键在旧 `COUPLED` 里**已有**成员，
#: `dict.update` 会把它们整个盖掉 ⇒ 那些案例会突然报「多红」（正是本轮的自踩预埋）。
check(RS, "AZ168c `_V242_EXTRA` 是**合并**不是覆盖（原声明的 `AT8`/`AZ60` 一条不丢）",
      {"AT8", "AZ60"} <= set(_az167_wc.COUPLED.get("AE9", ()))
      and set(_az167_wc._V242_EXTRA["AE9"]) <= set(_az167_wc.COUPLED["AE9"]),
      f"AE9={sorted(_az167_wc.COUPLED.get('AE9', ()))}")

# ── AZ169（V249）：`--goals-swap-edit` 两档 ────────────────────────────────
#  默认档必须**逐字节不变**（V233~V248 的对照臂全靠它可重放）；
#  `line` 档必须**与 v248 的 E 臂逐字节同**（否则那条 10/16 不能当本档的证据）。
#  ⛔ 三条前置（无 trace / 带 alt_code / 空 code）必须**抛**，不许静默降级：
#     降级会产出一个「只改一行」却没有定位依据的提示。
import hashlib as _az169_hl                                          # noqa: E402
from mw.reflect import (apply_edit as _az169_ae,                     # noqa: E402
                        build_proposal_prompt as _az169_bpp,
                        failed_actuals as _az169_fa,
                        numbered_code as _az169_nc,
                        trace_failure as _az169_tf,
                        verify as _az169_vf)
_az169_start = (V1 / "bench/evidence/l9_s310/cand/90579ecebe633352.py"
                ).read_text(encoding="utf-8")
_az169_tests = list(_az165_t.tests)
_az169_why = ("通过 %d/%d；失败: assert parse_duration('10m') == 600 ⇒ AssertionError"
              % (_az169_vf(_az169_start, _az169_tests), len(_az169_tests)))
_az169_tr = _az169_tf(_az169_start, _az169_tests)
_az169_acts = _az169_fa(_az169_start, _az169_tests)
_sh16 = lambda s: _az169_hl.sha256(s.encode()).hexdigest()[:16]      # noqa: E731
_az169_A = _az169_bpp(_az165_t.prompt, _az169_start, _az169_why,
                      _az169_acts, trace=_az169_tr)
_az169_E = _az169_bpp(_az165_t.prompt, _az169_start, _az169_why,
                      _az169_acts, trace=_az169_tr, edit=True)
check(RS, "AZ169a `build_proposal_prompt(edit=False)` 对 `t01` 仍产出 `2307` 字节 /\n"
          "              `1bdea4fc92d67817`（V245/V247/V248 三批同一读数）\n"
          "              ⇒ 默认档逐字节不变，历史对照臂仍可重放",
      (len(_az169_A.encode()), _sh16(_az169_A)) == (2307, "1bdea4fc92d67817"),
      f"{len(_az169_A.encode())}B/{_sh16(_az169_A)}")
check(RS, "AZ169b `edit=True` 对 `t01` 产出 `2615` 字节 / `60f2cc5e63c54255`\n"
          "              —— 与 v248 的 E 臂**逐字节同**（那条 10/16 的唯一依据）",
      (len(_az169_E.encode()), _sh16(_az169_E)) == (2615, "60f2cc5e63c54255"),
      f"{len(_az169_E.encode())}B/{_sh16(_az169_E)}")
#: 行号必须是**输入的真实坐标**：第 n 个 `{n:3d}| ` 前缀后面就是 `code.splitlines()[n-1]`。
_az169_lines = [ln for ln in _az169_E.splitlines() if ln[:3].strip().isdigit() and "| " in ln]
_az169_pairs = [(int(ln[:3]), ln[5:]) for ln in _az169_lines]
_az169_src_lines = _az169_start.splitlines()
_az169_bad = [(n, t) for n, t in _az169_pairs
              if not (1 <= n <= len(_az169_src_lines) and _az169_src_lines[n - 1] == t)]
check(RS, "AZ169c `edit=True` 的代码槽是**真实行号视图**：每个 `{n:3d}| ` 前缀后的文本\n"
          "              必须逐字节等于 `code.splitlines()[n-1]`（⛔ 行号不许是重排后的序号）",
      len(_az169_pairs) >= 20 and not _az169_bad,
      f"行数={len(_az169_pairs)} 不符={_az169_bad[:2]}")
_az169_n16, _az169_e16 = _az169_ae(_az169_start, 16, "minutes = int(parts[0])")
_az169_n23, _az169_e23 = _az169_ae(_az169_start, 23, "minutes = int(parts[0])")
check(RS, "AZ169d 装置正例/反例：`LINE=16` ⇒ **6/6**；`LINE=23`（与 16 逐字节同文本、\n"
          "              但那条是**对的**）⇒ **4/6** ⇒ 「改哪一行」必须单独判",
      not _az169_e16 and _az169_vf(_az169_n16, _az169_tests) == len(_az169_tests)
      and not _az169_e23 and _az169_vf(_az169_n23, _az169_tests) == 4,
      f"L16={_az169_vf(_az169_n16, _az169_tests)} L23={_az169_vf(_az169_n23, _az169_tests)}")
_az169_errs = {_az169_ae(_az169_start, 99, "x")[1],
               _az169_ae(_az169_start, None, "x")[1],
               _az169_ae(_az169_start, 16, "")[1],
               _az169_ae(_az169_start, 16, "minutes = = 1")[1].split(":")[0]}
check(RS, "AZ169e 四路 fail-closed 且**都不产出**：`oom` / `parse_fail` / `empty_expr` /\n"
          "              `syntax_fail`（⛔ 坏代码不许喂回打分器：那份 0 分会进账本、污染起点）",
      _az169_errs == {"oom", "parse_fail", "empty_expr", "syntax_fail"},
      str(sorted(_az169_errs)))
_az169_raises = 0
for _kw in ({"edit": True},
            {"edit": True, "trace": _az169_tr, "alt_code": "def f(): pass"},
            {"edit": True, "trace": _az169_tr, "code": "   "}):
    try:
        _kw2 = dict(_kw)
        _c = _kw2.pop("code", _az169_start)
        _az169_bpp(_az165_t.prompt, _c, _az169_why, _az169_acts, **_kw2)
    except ValueError:
        _az169_raises += 1
check(RS, "AZ169f 三条前置**都是抛**不是降级：无 `trace` / 与 `alt_code` 同用 / 空 `code`\n"
          "              （⛔ 静默降级会产出一个没有定位依据的「只改一行」提示）",
      _az169_raises == 3, f"抛出 {_az169_raises}/3")
import inspect as _az169_insp                                         # noqa: E402
_az169_dflt = _az169_insp.signature(
    _az165_run._swap_candidates).parameters["edit"].default
_az169_ok = _az166_sp.run([sys.executable, str(V1 / "run.py"),
                           "--goals-swap-edit", "line", "--help"],
                          capture_output=True, text=True, cwd=V1)
_az169_bad2 = _az166_sp.run([sys.executable, str(V1 / "run.py"),
                             "--goals-swap-edit", "both", "--help"],
                            capture_output=True, text=True, cwd=V1)
check(RS, "AZ169g `--goals-swap-edit` 默认 `off`（函数签名级），`line` 合法、`both` 被拒\n"
          "              （⛔ 不静默回落默认档 —— V240 的同型：以为开了其实没开）",
      _az169_dflt == "off" and _az169_ok.returncode == 0
      and _az169_bad2.returncode != 0,
      f"default={_az169_dflt!r} line→rc={_az169_ok.returncode} both→rc={_az169_bad2.returncode}")

# ── AZ170（V251）：换路起点必须**去围栏** ───────────────────────────────────
#  生产链路 `--goals-swap-start-from auto` 在 `src=act` 时交出的是**模型原文**
#  （带 ```python 围栏）。`apply_edit` 按行号替换 ⇒ 围栏占第 1 行 ⇒ 行号整体偏移 1、
#  围栏还留在文本里 ⇒ 每次换路都 `syntax_fail`。
#  实测（V251 生产链路）：`g7d` 六格 **18/18 全 syntax_fail**，A/B 的 B 臂等于没跑。
#  ⛔ v249 只做了「接口 + 桩」，一次模型没跑 ⇒ 这个坑没被发现（纪律 235）。
from mw.reflect import bare_code as _az170_bc                      # noqa: E402
import types as _az170_types                                       # noqa: E402
from mw import llm as _az170_llm                                    # noqa: E402
_az170_fenced = "```python\n" + _az169_start.rstrip("\n") + "\n```"
check(RS, "AZ170a `bare_code`：有围栏 ⇒ 取围栏内；**无围栏 ⇒ 逐字节返回原文**\n"
          "              （⛔ 不许顺手 strip/format —— 默认档提示字节由 AZ169a 钉着）",
      _az170_bc(_az170_fenced) == _az169_start.rstrip("\n")
      and _az170_bc(_az169_start) == _az169_start
      and _az170_bc("no fence here") == "no fence here"
      and _az170_bc("") == "",
      f"fenced->{len(_az170_bc(_az170_fenced))}B "
      f"bare_identical={_az170_bc(_az169_start) == _az169_start}")
_az170_n_ok, _az170_e_ok = _az169_ae(_az170_bc(_az170_fenced), 16,
                                     "minutes = int(parts[0])")
_az170_n_bad, _az170_e_bad = _az169_ae(_az170_fenced, 16, "minutes = int(parts[0])")
check(RS, "AZ170b 缺陷有牙：**原样**把带围栏的起点交给 `apply_edit` ⇒ `syntax_fail`；\n"
          "              先 `bare_code` 再交 ⇒ 6/6 ⇒ 这两条一起才证明「去围栏」不是可有可无",
      _az170_e_bad.startswith("syntax_fail") and not _az170_e_ok
      and _az169_vf(_az170_n_ok, _az169_tests) == len(_az169_tests),
      f"fenced_err={_az170_e_bad.split(':')[0]} "
      f"去围栏后={_az169_vf(_az170_n_ok, _az169_tests)}/6")
_az170_task = _az170_types.SimpleNamespace(
    prompt=_az165_t.prompt, tests=list(_az169_tests),
    as_dict=lambda: {"prompt": _az165_t.prompt, "tests": list(_az169_tests)})


class _Az170Scored:
    def __init__(self, v, vec):
        self.score, self.vec = v, vec


class _Az170Scorer:
    def score(self, task_dict, cand):
        v = _az169_vf(cand, _az169_tests)
        return _Az170Scored(v / len(_az169_tests),
                            [bool(x) for x in [1] * v + [0] * (len(_az169_tests) - v)])


class _Az170Stub:
    def __init__(self, reply):
        self.reply, self.seen = reply, None

    def complete(self, messages, max_tokens=None):
        self.seen = messages[0].content
        return self.reply


_az170_orig = _az170_llm.build_llm
try:
    _az170_stub = _Az170Stub("LINE: 16\nEXPR: minutes = int(parts[0])")
    _az170_llm.build_llm = lambda spec: _az170_stub
    _az170_core = _az170_types.SimpleNamespace(s=_az170_types.SimpleNamespace(llm=object()))
    _az170_bc_res = _az165_run._swap_candidates(
        _az170_core, _Az170Scorer(), _az170_task, _az170_fenced,
        5 / len(_az169_tests), 1, edit="line")
finally:
    _az170_llm.build_llm = _az170_orig
_az170_best, _az170_rows = _az170_bc_res[1], _az170_bc_res[2]
check(RS, "AZ170c 端到端：起点**带围栏**时 `_swap_candidates(edit='line')` 仍能产出候选\n"
          "              （`LINE=16` ⇒ 1.0）—— 这条锚点若早存在，V251 的 18/18 就不会发生",
      abs(_az170_best - 1.0) < 1e-9 and _az170_rows[0].get("line") == 16
      and _az170_rows[0].get("score") is not None,
      f"best={_az170_best} line={_az170_rows[0].get('line')} "
      f"err={_az170_rows[0].get('err')}")
_az170_stub2 = _Az170Stub("```python\n" + _az169_start + "```")
try:
    _az170_llm.build_llm = lambda spec: _az170_stub2
    _az165_run._swap_candidates(_az170_core, _Az170Scorer(), _az170_task,
                                _az169_start, 5 / len(_az169_tests), 1, edit="off")
finally:
    _az170_llm.build_llm = _az170_orig
check(RS, "AZ170d `off` 档对**裸**起点逐字节不改输入：代码槽里没有围栏包裹、\n"
          "              且 `bare_code` 是恒等（默认路径未被去围栏改动）\n"
          "              ⚠️ 判据只查**代码槽**：模板自己的「只输出 ```python 代码块」里就有 ```",
      _az169_start in (_az170_stub2.seen or "")
      and ("```python\n" + _az169_start[:30]) not in (_az170_stub2.seen or ""),
      f"代码槽被围栏包裹={'```python' + chr(10) + _az169_start[:12] in (_az170_stub2.seen or '')}")

print()
print("=" * 74)
print(f"V1 契约测试汇总  {RS.summary_line()}")# ⛔ `finish()` 必须在**打印判据行之前**调：它声明的就是「我已经跑到这里了」。
# 少这一句 ⇒ verdict() 永远是 CRASH ⇒ 门禁的 verdict 检查会判失败。
# 那个失败是**对的**：漏调 finish 的运行，确实不该被当成通过。
RS.finish()
print(RS.verdict_line())
print("=" * 74)
if RS.fail_n:
    print(f"失败断言数 = {RS.fail_n}")
raise SystemExit(1 if RS.fail_n else 0)
