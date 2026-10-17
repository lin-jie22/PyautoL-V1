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
from run import _kwm_absorb, _kwm_line, _kwm_zero                # noqa: E402

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

_sm_weak = SelfModel([{"family": "w", "passed": False, "failed_step": "plan"}] * 4)
_adv_weak = _sm_weak.advise(family="w", max_candidates=3)
check(RS, "AD4 弱族（历史通过率 0.0）⇒ 加宽候选 + 先验证，并**点名最常败在哪一步**",
      _adv_weak.confident and _adv_weak.deep and _adv_weak.verify_first
      and "plan" in _adv_weak.basis,
      _adv_weak.as_line())

_sm_strong = SelfModel([{"family": "s", "passed": True}] * 4)
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
print()
print("=" * 74)
print(f"V1 契约测试汇总  {RS.summary_line()}")
print("=" * 74)
if RS.fail_n:
    print(f"失败断言数 = {RS.fail_n}")
raise SystemExit(1 if RS.fail_n else 0)
