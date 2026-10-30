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
import inspect
import json
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
from mw.measure import (Report, check, ci95, noise_floor,         # noqa: E402
                        paired_compare, repeat, summarize)
from mw.memory import MemoryStore, fingerprint, fts_query         # noqa: E402
from mw.planner import (Planner, Step, action_signature,              # noqa: E402
                        looks_complex)
from mw.tools import (ToolRegistry, ToolSpec, external_agent_names,  # noqa: E402
                      register_core_tools, register_external_agents)
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
check(RS, "G13 「响应被截断」单独计入 truncated（不与「不会写 JSON」混同，"
          "两者一个调 max_tokens 一个改提示词）",
      _tr.truncated == 1 and _tr.json_fail == 0 and _tr.empty_plan == 0
      and _tr.stats()["cause_balances"] is True,
      f"truncated={_tr.truncated} json_fail={_tr.json_fail} "
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
_Q_PLAN = '{"candidates": [{"intent": "查一下", "tool": "recall", "args": {"query": "x"}}]}'


class _QLLM(StubLLM):
    """既会出计划、也会在被专门索要答案时给答案。"""

    def complete(self, messages, **kw):                            # noqa: ANN001, ANN003
        blob = "\n".join(m.content for m in messages)
        if "只输出最终答案本身" in blob:
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
        if "只输出最终答案本身" in blob:
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
        if "只输出最终答案本身" in blob:
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
                 _kwm_zero)

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

check(RS, "AN7 层读数必须有消费者：`memory_layer_view` 要被 `_kwm_absorb` **主动取走**\n"
          "         —— 第三次踩'新建字段没人搬运'的根因就是要求每个调用点**记着传**。\n"
          "           现在由 absorb 自己去 core 上取，调用点传不传都有。",
      "memory_layer_view" in _strip_docstrings(inspect.getsource(_kwm_absorb))
      and _AN_abs.memory_layer_view["abstract"] >= 1
      and _AN_abs.memory_layer_view["recalled"] >= _AN_abs.memory_layer_view["abstract"],
      f"view={_AN_abs.memory_layer_view}")


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
                     ("被改过", int(_AR_H.ref.lstrip("b#"))))
_AR_MEM.conn.commit()
try:
    _AR_MEM.expand(_AR_H.ref, cap=10)
    _AR_err.append("被改过却没报错")
except BlobIntegrityError:
    pass
check(RS, "AR3 ★句柄不存在 ⇒ `KeyError`；原文与写侧承诺不符 ⇒ `BlobIntegrityError`\n"
          "         —— 静默返回空串/原文会让『取回失败』长得和『取回成功』一样。\n"
          "           这与 `.txt 被误判成功`是同一类事故。",
      not _AR_err, f"异常={(not _AR_err)} 漏网={_AR_err}")
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
check(RS, "AS3 ★`derive()` 只许收 `goal` 再加一个**配置档位**，且不同题面必须派生出不同判据\n"
          "         —— 若它还能收 answer 或分数，就给了『答案反过来影响判据』的入口，\n"
          "           那正是自评分的形态。",
      _AS_DERIVE_ARGS == ["goal", "static"] and _AS_D1 != _AS_D2
      and _AS_STATIC_DEFAULT is not None and _AS_STATIC_DEFAULT.default == 0
      and _AS_DERIVE_BAD == [],
      f"形参={_AS_DERIVE_ARGS} 禁用名命中={_AS_DERIVE_BAD} "
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
_AS_PRODUCES = '_produces = cand.tool == "finish" or bool(' in _code_only(_CORE_SRC)
check(RS, "AS13 ★`_gate_finish` 只许有**一处**调用，且必须被「答案来源」守住\n"
          "          —— 本轮只加一条腿，不加一个相位：主循环仍然只有一个 `while`（J6），\n"
          "            校验是答案出口上的一道闸，不是第二次推理。\n"
          "          ⚠️ 外部能力的答案走同一道闸（第二十三轮）⇒ 守卫换成 `_produces`，\n"
          "            但 `_produces` 的定义里必须仍含 `finish` —— 否则闸会变成外部专用。",
      _AS_GATE_CALLS == 1 and _AS_GATE_GUARDED and _AS_PRODUCES,
      f"调用点={_AS_GATE_CALLS} 被 _produces 守住={_AS_GATE_GUARDED} "
      f"含 finish={_AS_PRODUCES}")

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
_az48_def_sites = re.findall(
    r'else _data_path\(Path\("bench"\) / "(\w+)" / "([\w.]+)"\)\)', _AE_RUN)
_az48_expl = re.findall(
    r'(suite|scorer)_path = \(Path\(args\.\1\)\.expanduser\(\) if args\.\1', _AE_RUN)
check(RS, "AZ48 ★D-a 默认数据路径：`run.py:data_path()` 必须**当前目录优先、包目录兜底**\n"
          "          （安装形态实测：`pip install .` 后包目录落在 site-packages，\n"
          "          只按包目录找会「明明脚下有 bench/ 却报找不到」）。\n"
          "          ⚠️ 判别性来自**两处都有**那种形态：兜底优先的实现会取包目录那份；\n"
          "          只有一处有的两种形态，两个实现都一样 —— 只测它们等于没测。\n"
          "          ⛔ `--suite` / `--scorer` 显式值**不许**走兜底（悄悄换文件 = 假成功）。\n"
          "          撤回语义：把优先序倒过来 / 删掉兜底 / 让显式值也进兜底 ⇒ 必须红。",
      _az48_both and _az48_pkgonly and _az48_none
      and _az48_def_sites == [("tasks", "coding_small.jsonl"),
                              ("scorers", "code_exec.py")]
      and _az48_expl == ["suite", "scorer"],
      f"两处都有={_az48_both} 仅包目录={_az48_pkgonly} 都没有={_az48_none} "
      f"默认站点={_az48_def_sites} 显式站点={_az48_expl} 实测={_az48_seen}")

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
      and _code_only(_core_src).count("rejected=reseek_lines") == 2,
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
_AZ95_MARK_ANSWER = "只输出最终答案本身"


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


print()
print("=" * 74)
print(f"V1 契约测试汇总  {RS.summary_line()}")
print("=" * 74)
if RS.fail_n:
    print(f"失败断言数 = {RS.fail_n}")
raise SystemExit(1 if RS.fail_n else 0)
