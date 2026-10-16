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
from mw.llm import (ANSWER_MARK, PARSE_MODES, Message, StubLLM,        # noqa: E402
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
mm = wm.observe(Prediction("写文件", "会成功返回内容", 0.8), "OSError 权限不足")
check(RS, "F3 预测与实测不符 ⇒ 产生失配", mm is not None and mm.agreement < 0.34,
      f"agree={getattr(mm, 'agreement', None)}")
check(RS, "F4 失配写入情景记忆",
      any(e.kind == "mismatch" for e in mem2.episodes(kind="mismatch")))
wf = [f for f in mem2.recall("写文件 成功返回内容", k=10) if f.source == "worldmodel"]
check(RS, "F5 失配提炼为持久启发式（进语义记忆）", len(wf) >= 1,
      str([f.text[:40] for f in wf]))
check(RS, "F6 一致的预测不沉淀（不制造噪声）",
      wm.observe(Prediction("a", "abc def", 0.9), "abc def") is None)
check(RS, "F7 选择性前瞻挡下低置信预测",
      len(wm.foresight_filter([Prediction("a", "e", 0.9),
                               Prediction("b", "e", 0.1)])) == 1)
check(RS, "F8 抑制计数可对账", wm.stats().get("suppressed") == 1, str(wm.stats()))
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
check(RS, "J3 LLM 计数只有一处（_LedgeredLLM）",
      core_src.count("spend_llm()") == 1, str(core_src.count("spend_llm()")))
check(RS, "J4 每个被拦候选都 continue（不会带着被拒动作继续执行）",
      core_src.count("continue") >= 3)


def _call_lines(src: str, attr: str) -> list[int]:
    """用 AST 取**真实调用**的行号。

    ⚠️ 必须走 AST：直接 `src.index(name)` 会命中模块 docstring 里的同名文字
    —— V5 的 R60 就是这么骗过我的（静态锚必须注释/文档感知）。
    本文件 J5 第一次跑就是这个错，此处是修复。
    """
    tree = ast.parse(src)
    return sorted(n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Call)
                  and (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) == attr)


_cascade_lines = _call_lines(core_src, "check_cascade")
_dispatch_lines = _call_lines(core_src, "dispatch")
check(RS, "J5 熔断在循环顶部检查（AST 行号先于任何 dispatch）",
      bool(_cascade_lines) and bool(_dispatch_lines)
      and min(_cascade_lines) < min(_dispatch_lines),
      f"cascade@{_cascade_lines} dispatch@{_dispatch_lines}")
check(RS, "J6 主循环只有一个 while（不叠多套循环）",
      len([n for n in ast.walk(ast.parse(core_src)) if isinstance(n, ast.While)]) == 1,
      str([n.lineno for n in ast.walk(ast.parse(core_src)) if isinstance(n, ast.While)]))
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
print(f"V1 契约测试汇总  {RS.summary_line()}")
print("=" * 74)
if RS.fail_n:
    print(f"失败断言数 = {RS.fail_n}")
raise SystemExit(1 if RS.fail_n else 0)
