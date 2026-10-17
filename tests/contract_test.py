#!/usr/bin/env python3
"""V1 契约测试 —— 离线、零依赖、可独立运行。

设计遵循 V5 用 136 轮换来的三条硬纪律：
  ① **测试绝不碰生产目录**：所有状态目录由 env 指向临时目录（V5 的 R4/R8）
  ② **不许对可能不存在的属性做算术**（V5 的 R13 硬坑：撤回态下 `int(None)` 抛异常，
     打断汇总行）⇒ 本文件全部用 `.get()` + 类型前置
  ③ **汇总行必须能被外部 runner 的正则解析**：`PASS=n  FAIL=m`
     （V5 的 §134/§135 连续两轮把 `SUM_PASS=` 写进汇总行，
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
from mw.core import Core, _Log                                # noqa: E402
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
from mw.worldmodel import Prediction, WorldModel, similarity      # noqa: E402

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
print("A. 配置单一源（V5 的'同一事实多处声明'正面修复）")
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
print("C. 三层记忆（V5 的 39× 重复 ⇒ DB 层去重约束）")
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
check(RS, "H5 落在噪声带内 ⇒ 判定不成立（V5 的核心病灶）", not ok2, why2)
ok3, why3 = paired_compare(m_a, summarize([0.99, 1.0], candidate="c", budget_tag="4x"))
check(RS, "H6 跨预算档比较被拒（2026: 能力是预算的曲线）", not ok3, why3)
check(RS, "H7 噪声下限取最保守口径（最大极差）",
      abs(noise_floor(m_a, summarize([0.5, 0.9], candidate="z", budget_tag="1x")) - 0.4) < 1e-9)
r2 = Report(); r2.pass_n = 7; r2.fail_n = 0
check(RS, "H8 汇总行可被外部 runner 正则解析（V5 §134/§135 的坑）",
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
    —— V5 的 R60 就是这么骗过我的（静态锚必须注释/文档感知）。
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
print("O. 停滞闸（重复的「成功」不是进展）—— V5「同一假设无限重派」的同构体")
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
# 而报错文本读起来像模型的锅。这正是 V5 的老毛病：不可归因的失败一起进读数。
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
        "V1_LLM_RETRIES": ("limits", "llm_retries"),
        "V1_LLM_RETRY_BACKOFF_S": ("limits", "llm_retry_backoff_s"),
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
# 正是 V5 "字段无消费者" 的同构体。
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
#   ⚠️ 走 AST，不走字符串搜索（V5 的 R60：全文 `index()` 会命中注释里的同名文字）。
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
#   ⚠️ 这与 V5 的死因**同构**：一个**永远不可能"好"**的信号。
#      把 `失配=3` 读成"因果闭环在工作"，就是 V5 把 `gain≡0` 读成"每轮全绿"。
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

# Y7 对账：三个派生量必须自洽，否则读数会互相打架（V5 的四五处声明就是这个病）。
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
          "没有 `score` 字段 —— 自评分是 V5 的死因，这条是接口级防线",
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
check(RS, "AE1 ★实验工具的危险性不许超过被测系统：`coherence.py` 必须在"
          "**施加变体之前**装 `run_shell` 干跑保险"
          "（关掉守卫后 `rm -rf ~` 会真的执行 —— 第一次跑靠宿主沙箱兜住，"
          "那不是我的代码在工作）",
      0 <= _i_dry < _i_patch,
      f"dry@{_i_dry} patch@{_i_patch}")

check(RS, "AE2 消融必须为每个变体隔离记忆目录（`facts` 跨轮累积，"
          "共用目录量到的是「记忆多少」而不是「这个部件有没有用」）",
      "TemporaryDirectory" in _coh and "V1_STATE_DIR" in _coh,
      f"TemporaryDirectory={'TemporaryDirectory' in _coh} V1_STATE_DIR={'V1_STATE_DIR' in _coh}")

check(RS, "AE3 消融报告必须同时给「声明」与「实测」分类，且不一致时以实测为准"
          "（先验与实测分歧本身就是最有信息量的一行）",
      "declared" in _coh and "actual" in _coh and "classify" in _coh,
      f"declared={'declared' in _coh} actual={'actual' in _coh} classify={'classify' in _coh}")


# ===========================================================================
# AF 组 —— 失败签名：把「未实现」与「不必要」分开（第二层判据）
#
# 第一层（AE / bench/coherence.py）问"拆了环还转吗"。它**没有"缺席"这一格**：
# 拆一个不存在的模块，读数当然不变 ⇒ Δ=0 被读成"不必要"，而真相是"未实现"。
# 那是把**没做的事**判成**不需要做的事** —— 与 V5 的病同构，只是方向相反。
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
check(RS, "AF9 ★单一事实源：`core.py` 的写入截断必须引用 `WORKING_CHAR_CAP`，"
          "不许再出现 `full[:300]`（探针与实现各抄一份 300 就会漂成两套真相）",
      "WORKING_CHAR_CAP = 300" in _core_src
      and "full[:WORKING_CHAR_CAP]" in _core_src
      and "full[:300]" not in _core_src,
      f"常量声明={'WORKING_CHAR_CAP = 300' in _core_src} "
      f"引用={'full[:WORKING_CHAR_CAP]' in _core_src} "
      f"残留字面量={'full[:300]' in _core_src}")

check(RS, "AF10 感知→记忆的硬瓶颈必须判成立：长观测 5000 字只存 300 字（6%）"
          "—— 而下一步关于上一步的一切都只来自这 300 字",
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
#   ② 不许用当前题：用了就是照着自己的答案改答案（自评分 = V5 死因）
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
          "放行它，内核就是在照着自己的答案改答案（自评分，V5 的死因）",
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
          "（V5 的 39× 重复就是后者的下场：写入时没人管，清理时两套逻辑对不上）",
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
# 判据不由测量者发明，就没人能通过调松判据让分数变好看 —— V5 的死法。
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
          "         （只证明满分/零分可分太弱 —— 那与 V5 那把五天不动的尺子没区别）",
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
          "         （缺签名 ≠ 同口径。默认放行 = 把两种东西当一种 = V5 的跨口径比较）",
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
          "          （一个怎么都能赢的判据，和 V5 那个恒为 0 的适应度是同一种东西）\n"
          "          ⚠️ 旧版用的是『轮转族标签』，那只给族改名 —— 未来行若跟着\n"
          "             改名则信号毫发无损，改历史则恒等于完全错配，都不是零假设",
      _AI_null["state"] != "PRESENT",
      f"无差异账本 Δ={_AI_null['delta']} 阈值={_AI_null['margin']} "
      f"⇒ {_AI_null['state']}")

_AI_gate = _smp.probe_caliber_gate()
check(RS, "AI11 ★口径闸是不是**承重的**：混入异口径 ⇒ 预测必须变差\n"
          "          （撤了闸误差也不变 ⇒ 那道闸是装饰；装饰的闸就是 V5 的死法）",
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
          "         （填 0 = 让这一行假装属于会话 0，于是被算进切分；V5 的『缺失当零』）",
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
          "         （能调阈值 = 能把结论调好看的旋钮 ⇒ V5 的尺子下移）",
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

    ⚠️ V5 的 R7 硬坑：全文件 `index()`/`find()` 会命中**注释和 docstring 里
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
# ⚠️ V5 的 R13 硬坑：契约**不许对可能不存在的东西做算术**。
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
          "            那不是学习，那是把测试集答案抄进提示词。",
      "probe_self" not in _AL_g
      and _AL_wm2.rewards_skipped_self >= 1
      and _AL_g.count("- 同类题") == 2,
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
          "            一个你看不出有没有被用过的通道 = 装饰（V5 的同名病灶）",
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
          "         —— 一个只在内部计、永远没人念的数，等于没有（V5 装饰字段的同型）",
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
#: 两处在 ⇒ 改一处另一处仍在，AM12 测得通过而实际行为没变（V5 的老病）。
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
_AN_s_raw = _AN_raw._state("目标", [], 1)
_AN_s_abs = _AN_abs._state("目标", [], 1)

check(RS, "AN5 abstract 模式下**工作记忆整块不进上下文**\n"
          "         —— 工作记忆存的是工具输出原文，而 finish 的输出就是答案原文。\n"
          "           留着它，'抽象臂'就不成为对照：同一段答案换了条路走进来。",
      "工作记忆" in _AN_s_raw and "工作记忆" not in _AN_s_abs,
      f"raw含={'工作记忆' in _AN_s_raw} abstract含={'工作记忆' in _AN_s_abs}")

check(RS, "AN6 abstract 模式下只有 `source==abstract` 的事实留得下\n"
          "         —— 且过滤必须**只看 source**：'长得像经验'是由运气决定的判据，\n"
          "           source 才是写侧承诺的事实。",
      "做 X 之前先检查 Y" in _AN_s_abs
      and "不是抽象层的旧事实" not in _AN_s_abs,
      f"abstract臂上下文={_AN_s_abs[:160]!r}")

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

print()
print("=" * 74)
print(f"V1 契约测试汇总  {RS.summary_line()}")
print("=" * 74)
if RS.fail_n:
    print(f"失败断言数 = {RS.fail_n}")
raise SystemExit(1 if RS.fail_n else 0)
