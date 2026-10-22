#!/usr/bin/env python3
"""外部锚层契约测试 —— 离线、零依赖、可独立运行。

这一套测试只关心一件事：**我们能不能相信一个分数。**
（历史故障全部集中在"反馈能不能被相信"，没有一条出自引擎。）

因此它的重心不是"功能对不对"，而是**反证组**：
   D 组用一个**没有分辨力的假打分器**去打真任务集，要求 calibrate **必须判红**。
   如果 D 组不红，说明"判别力检查"是死代码 —— 那么 C 组的绿也是假的。

三条用事故换来的纪律照旧：
  ① 测试绝不碰生产目录（env 全部指向临时目录）
  ② 不许对可能不存在的属性做算术（一律 getattr / .get + 类型前置）
  ③ 汇总行格式 `PASS=n  FAIL=m`（可被外部 runner 正则解析）
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

# ① 先隔离环境，再 import 任何 mw 模块
_TMP = Path(tempfile.mkdtemp(prefix="v1_bench_"))
os.environ["V1_STATE_DIR"] = str(_TMP / "state")
os.environ["V1_LOG_DIR"] = str(_TMP / "logs")
os.environ["V1_WORKSPACE"] = str(_TMP / "ws")
os.environ["V1_LLM_OFFLINE"] = "1"

V1 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V1))

from mw import CORE_MODULES, NON_CORE_MODULES                # noqa: E402
from mw import bench as B                                   # noqa: E402
from mw.measure import Report, check, summarize             # noqa: E402

RS = Report()
MW = V1 / "mw"
SUITE_PATH = V1 / "bench" / "tasks" / "coding_small.jsonl"
REAL_SCORER = V1 / "bench" / "scorers" / "code_exec.py"
SCORER_DIR = _TMP / "scorers"
SCORER_DIR.mkdir(parents=True, exist_ok=True)


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def _write_scorer(name: str, body: str) -> Path:
    """写一个**事故版打分器**到临时目录（反证组用）。"""
    p = SCORER_DIR / name
    p.write_text(body, encoding="utf-8")
    return p


def _real_scorer(**kw) -> B.ExternalScorer:
    return B.ExternalScorer([sys.executable, str(REAL_SCORER)],
                            source=REAL_SCORER, **kw)


SUITE = B.Suite.load(SUITE_PATH)


# ===========================================================================
print("=" * 74)
print("A 组  口径签名（契约①）—— 尺子的身份")
print("=" * 74)

_empty = B.Caliber()
check(RS, "A1 缺省 Caliber 的每个字段都是哨兵，不是默认值",
      all(_empty.as_dict()[k] == "<MISSING>" for k in B.CALIBER_KEYS),
      str(_empty.as_dict()))
check(RS, "A2 口径键就是这十二个（供给侧运行时状态刻意不入闸）",
      # ⚠️ 第二十四轮加了第 12 个 `tool_catalog_sha` —— 它**不是**运行时状态，
      #    而是**提示词**的一部分（planner.py:182 把工具目录拼进提示词）。
      #    这条断言当场转红，说明"数一遍键"的写法确实能抓住新增字段。
      tuple(B.CALIBER_KEYS) == ("scorer_sha", "suite_sha", "repeat",
                                "budget_tag", "temperature", "fresh",
                                "memory_scope", "memory_seed",
                                "limits", "llm_cond", "max_tokens",
                                "tool_catalog_sha"),
      str(tuple(B.CALIBER_KEYS)))
# ⛔ 这一条是"口径混淆"的入口：把熔断/重试之类的运行时状态写进口径，
#    会让"供给侧崩了"的那一轮自动获得一把新尺子 —— 正是要防的事。
_runtime_words = ("fuse", "breaker", "retry", "health", "error", "fail", "circuit")
check(RS, "A3 口径键里没有任何供给侧运行时字段",
      not any(w in k.lower() for k in B.CALIBER_KEYS for w in _runtime_words),
      str([k for k in B.CALIBER_KEYS
           if any(w in k.lower() for w in _runtime_words)]))

_c1 = B.Caliber(scorer_sha="aaa", suite_sha="bbb", repeat=5, budget_tag="1x", fresh=True)
_c2 = B.Caliber(scorer_sha="aaa", suite_sha="bbb", repeat=5, budget_tag="1x", fresh=True)
_c3 = B.Caliber(scorer_sha="aaa", suite_sha="bbb", repeat=5, budget_tag="2x", fresh=True)
check(RS, "A4 相同口径 same_as 为真、签名相同",
      _c1.same_as(_c2) and _c1.signature() == _c2.signature())
check(RS, "A5 一个字段不同 ⇒ same_as 为假，且 diff **点名**该字段",
      (not _c1.same_as(_c3)) and _c1.diff(_c3) == ["budget_tag"], str(_c1.diff(_c3)))

# ---- 温度进闸（2026-10-17 由真模型实测逼出来的）--------------------------
# 实测事实：同尺子同模型同任务集，仅改温度
#     T=0.0 ⇒ 各次总分极差 0.0000（逐题逐次完全一致）
#     T=0.8 ⇒ 各次总分极差 0.1111（6 题中 2 题剧烈抖动）
# 0.1111 已超过登记的噪声上限。温度不进闸 ⇒ 采样抖动会被读成"改进"。
_c_t0 = B.Caliber(scorer_sha="aaa", suite_sha="bbb", repeat=5,
                  budget_tag="1x", temperature=0.0, fresh=True)
_c_t8 = B.Caliber(scorer_sha="aaa", suite_sha="bbb", repeat=5,
                  budget_tag="1x", temperature=0.8, fresh=True)
check(RS, "A6 仅温度不同 ⇒ 口径不同、签名不同，且 diff 点名 temperature",
      (not _c_t0.same_as(_c_t8)) and _c_t0.signature() != _c_t8.signature()
      and _c_t0.diff(_c_t8) == ["temperature"], str(_c_t0.diff(_c_t8)))
check(RS, "A7 缺省温度是哨兵而非 0.0（否则'没记温度'会被读成'T=0'）",
      B.Caliber().as_dict().get("temperature", "<闸里没有这个键>") == "<MISSING>",
      str(B.Caliber().as_dict().get("temperature", "<闸里没有这个键>")))

# ---- 记忆口径进闸（2026-10-17 由内核路径实测逼出来的）----------------------
# 实测事实：两轮"同口径"（1x / T=0 / --fresh）读数一次 0.8611、一次 0.7222。
# 原因：`--fresh` 只换 Core、不换 DB ⇒ 记忆跨运行累积，第二轮起跑时已有 27 条
# 语义记忆 ⇒ 召回不同 ⇒ 提示词不同 ⇒ **即使 T=0 输出也不同**。
# 记忆基线不进闸 ⇒ 会把"记忆多少"读成"候选好坏"。这是「尺子下移」的同类。
_c_mem_a = B.Caliber(scorer_sha="a", suite_sha="b", repeat=5, budget_tag="1x",
                     temperature=0.0, fresh=False,
                     memory_scope="shared_db", memory_seed=(0, 0))
_c_mem_b = B.Caliber(scorer_sha="a", suite_sha="b", repeat=5, budget_tag="1x",
                     temperature=0.0, fresh=False,
                     memory_scope="shared_db", memory_seed=(27, 51))
check(RS, "A8 只有记忆基线不同 ⇒ 口径不同、签名不同，且 diff 点名 memory_seed",
      (not _c_mem_a.same_as(_c_mem_b))
      and _c_mem_a.signature() != _c_mem_b.signature()
      and _c_mem_a.diff(_c_mem_b) == ["memory_seed"], str(_c_mem_a.diff(_c_mem_b)))
_c_scope = B.Caliber(scorer_sha="a", suite_sha="b", repeat=5, budget_tag="1x",
                     temperature=0.0, fresh=True,
                     memory_scope="per_task_fresh", memory_seed=(0, 0))
check(RS, "A9 记忆作用域不同（fresh vs shared）⇒ 口径不同、diff 点名 memory_scope",
      _c_scope.diff(_c_mem_a) == ["fresh", "memory_scope"],
      str(_c_scope.diff(_c_mem_a)))
check(RS, "A10 记忆字段是哨兵而非空值（'没记'不许被读成'空记忆'）",
      B.Caliber().as_dict()["memory_scope"] == "<MISSING>"
      and B.Caliber().as_dict()["memory_seed"] == "<MISSING>")
check(RS, "A11 as_line 里两个记忆字段都出现（日志里看得见才算进了闸）",
      all(k in B.Caliber().as_line() for k in ("memory_scope", "memory_seed")),
      B.Caliber().as_line())

# ---- 绝对值进闸（2026-10-17 补：`budget_tag` 只是倍数，乘在绝对值上）--------
# ⛔ 这是一类**同类漏洞的三个入口**。两次运行可以都写 `4x`，却因为
#    `V1_MAX_STEPS` 不同而跑出完全不同的预算；`--max-tokens` 改了截断阈；
#    `model`/`base_url` 换了就是换了尺子。**只要有一个不在签名里，
#    "同口径"这三个字就是假的** —— 这与温度那次同源：不是尺子坏了，
#    是尺子量错了对象。
_base = dict(scorer_sha="a", suite_sha="b", repeat=5, budget_tag="1x",
             temperature=0.0, fresh=True, memory_scope="per_task_fresh",
             memory_seed=(0, 0), limits={"max_steps": 12},
             llm_cond={"model": "m1"}, max_tokens=1024)
_c_lim = B.Caliber(**{**_base, "limits": {"max_steps": 32}})
check(RS, "A12 只有 limits 不同 ⇒ 口径不同，diff 点名 limits",
      B.Caliber(**_base).diff(_c_lim) == ["limits"], str(B.Caliber(**_base).diff(_c_lim)))
_c_model = B.Caliber(**{**_base, "llm_cond": {"model": "m2"}})
check(RS, "A13 只有模型条件不同 ⇒ 口径不同，diff 点名 llm_cond",
      B.Caliber(**_base).diff(_c_model) == ["llm_cond"],
      str(B.Caliber(**_base).diff(_c_model)))
_c_tok = B.Caliber(**{**_base, "max_tokens": 4096})
check(RS, "A14 只有 max_tokens 不同 ⇒ 口径不同（截断阈会直接改变分数）",
      B.Caliber(**_base).diff(_c_tok) == ["max_tokens"],
      str(B.Caliber(**_base).diff(_c_tok)))
check(RS, "A15 limits/llm_cond/max_tokens 缺省是哨兵（'没记'不许被读成'空配置'）",
      B.Caliber().as_dict()["limits"] == "<MISSING>"
      and B.Caliber().as_dict()["llm_cond"] == "<MISSING>"
      and B.Caliber().as_dict()["max_tokens"] == "<MISSING>",
      str({k: B.Caliber().as_dict()[k] for k in ("limits", "llm_cond", "max_tokens")}))
_line_wide = B.Caliber(**{**_base, "limits": {"max_steps": 12, "max_llm_calls": 24,
                                              "recall_k": 6, "max_candidates": 3}}).as_line()
check(RS, "A16 as_line 里三个新字段都出现，且结构化值打短哈希（一行放得下）",
      all(k in _line_wide for k in ("limits", "llm_cond", "max_tokens"))
      and len(_line_wide) < 400, f"len={len(_line_wide)} {_line_wide}")

from mw import config as cfg                                          # noqa: E402
_secret = cfg.LLMConfig(api_key="SECRET_XYZ", base_url="https://host.example/v1")
check(RS, "A17 llm_cond 里**没有** api_key（敏感字段绝不进口径、绝不落盘）",
      "api_key" not in _secret.cond() and "SECRET_XYZ" not in str(_secret.cond()),
      str(_secret.cond()))
check(RS, "A18 llm_cond 里**没有** temperature（同一事实只声明一处：它是独立字段）",
      "temperature" not in _secret.cond(), str(_secret.cond()))


# ===========================================================================
print()
print("=" * 74)
print("B 组  外部打分器边界（契约③）—— 崩溃 ≠ 0 分")
print("=" * 74)

_crash = _write_scorer("crash.py", "import sys\nsys.exit(1)\n")
_sleep = _write_scorer("sleep.py", "import time\ntime.sleep(10)\nprint('{}')\n")
_garbage = _write_scorer("garbage.py", "print('not json at all')\n")
_oob = _write_scorer("oob.py", "print('{\"score\": 7.5}')\n")
_nonum = _write_scorer("nonum.py", "print('{\"score\": \"high\"}')\n")
_nokey = _write_scorer("nokey.py", "print('{\"why\": \"x\"}')\n")
_zero = _write_scorer("zero.py", "print('{\"score\": 0.0, \"why\": \"low\"}')\n")
_blank = _write_scorer("blank.py", "import sys\nsys.stdout.write('')\n")

_t = SUITE.tasks[0].as_dict()


def _raises(fn) -> tuple[bool, str]:
    """跑一个应当抛 ScoreError 的调用。返回 (是否抛了 ScoreError, 说明)。"""
    try:
        fn()
    except B.ScoreError as e:
        return True, str(e)
    except Exception as e:  # noqa: BLE001 —— 别的异常类型也算不合格（类型必须精确）
        return False, f"抛了 {type(e).__name__} 而不是 ScoreError: {e}"
    return False, "没有抛异常"


_ok, _why = _raises(lambda: B.ExternalScorer([sys.executable, str(_crash)],
                                             source=_crash).score(_t, "x"))
check(RS, "B1 打分器非零退出 ⇒ ScoreError（**绝不降级成 0 分**）", _ok, _why)

_ok, _why = _raises(lambda: B.ExternalScorer([sys.executable, str(_sleep)],
                                             source=_sleep, timeout_s=1.0).score(_t, "x"))
check(RS, "B2 打分器超时 ⇒ ScoreError", _ok, _why)

_ok, _why = _raises(lambda: B.ExternalScorer([sys.executable, str(_garbage)],
                                             source=_garbage).score(_t, "x"))
check(RS, "B3 打分器输出非 JSON ⇒ ScoreError", _ok, _why)

_ok, _why = _raises(lambda: B.ExternalScorer([sys.executable, str(_blank)],
                                             source=_blank).score(_t, "x"))
check(RS, "B4 打分器无任何输出 ⇒ ScoreError", _ok, _why)

_ok, _why = _raises(lambda: B.ExternalScorer([sys.executable, str(_oob)],
                                             source=_oob).score(_t, "x"))
check(RS, "B5 score 越界 [0,1] ⇒ ScoreError", _ok, _why)

_ok, _why = _raises(lambda: B.ExternalScorer([sys.executable, str(_nonum)],
                                             source=_nonum).score(_t, "x"))
check(RS, "B6 score 非数值 ⇒ ScoreError", _ok, _why)

_ok, _why = _raises(lambda: B.ExternalScorer([sys.executable, str(_nokey)],
                                             source=_nokey).score(_t, "x"))
check(RS, "B7 缺 score 字段 ⇒ ScoreError", _ok, _why)

# ⛔ 契约③的关键分界：**0.0 是一个合法分数**，不是"没量到"。
#    病灶之一是"缺失被读成否定"，这条钉住反面。
try:
    _z = B.ExternalScorer([sys.executable, str(_zero)], source=_zero).score(_t, "x")
    check(RS, "B8 score=0.0 是**合法结果**，不抛异常（缺失≠否定的反面）",
          _z.score == 0.0, f"score={_z.score}")
except Exception as e:  # noqa: BLE001
    check(RS, "B8 score=0.0 是**合法结果**，不抛异常（缺失≠否定的反面）",
          False, f"错误地抛了 {type(e).__name__}: {e}")

check(RS, "B9 打分器名字取**脚本名**而非解释器名（否则日志里两把尺子分不开）",
      _real_scorer().name == "code_exec", _real_scorer().name)
check(RS, "B10 无 source 时 sha 返回显式哨兵，不是静默空串",
      B.ExternalScorer([sys.executable]).sha() == "<NO_SOURCE>")


# ===========================================================================
print()
print("=" * 74)
print("C 组  判别力自证（契约⑥）—— 先证明尺子能分辨，再用它量")
print("=" * 74)

_REAL = _real_scorer()
_CAL = B.calibrate(_REAL, SUITE)

check(RS, "C1 真打分器 + 真任务集 ⇒ 校准通过", _CAL.ok, _CAL.reason)
check(RS, "C2 每条任务都有校准记录（分母可对账）",
      len(_CAL.per_task) == len(SUITE), f"{len(_CAL.per_task)} vs {len(SUITE)}")
_mid_expected = sum(1 for t in SUITE.tasks if t.partial.strip())
check(RS, "C3 中段检查条数 == 提供 partial 的任务数",
      _CAL.middle_n == _mid_expected, f"{_CAL.middle_n} vs {_mid_expected}")
check(RS, "C4 中段检查条数 ≥1（否则'中段分辨力'是零判别力的空判据）",
      _CAL.middle_n >= 1, str(_CAL.middle_n))
check(RS, "C5 参考解全部满分、空答案全部零分（尺子两端不糊）",
      all(abs(r["oracle"] - 1.0) < 1e-9 and abs(r["null"]) < 1e-9 for r in _CAL.per_task),
      str([(r["id"], r["oracle"], r["null"]) for r in _CAL.per_task]))
check(RS, "C6 中段分数严格落在两端之间（不是二值尺子）",
      all(r["partial"] is None or r["null"] < r["partial"] < r["oracle"]
          for r in _CAL.per_task),
      str([(r["id"], r["partial"]) for r in _CAL.per_task if r["partial"] is not None]))
check(RS, "C7 margin 为正（好答案整体高于坏答案）", _CAL.margin > 0, f"{_CAL.margin:+.4f}")

# 空任务集必须显式报错，不能"零任务 ⇒ 零失败 ⇒ 绿"
_try_empty = False
try:
    B.calibrate(_REAL, B.Suite(name="empty", tasks=()))
    _try_empty = True
except Exception:  # noqa: BLE001
    _try_empty = False
check(RS, "C8 空任务集 ⇒ 报错（不许静默判绿）", not _try_empty)

# min_middle 提高 ⇒ 达不到就必须不 ok（钉住 min_middle 真的在起作用）
_cal_hi = B.calibrate(_REAL, SUITE, min_middle=999)
check(RS, "C9 min_middle 达不到时校准**不通过**（门槛真的在起作用）",
      (not _cal_hi.ok) and "中段" in _cal_hi.reason, _cal_hi.reason)

# 中段覆盖面：每条任务都应带一份参考性部分解。没有 partial 的任务 ⇒
# 那条上"中段分辨力"没被验证，只能靠 evidence.unknowns 登记，属于覆盖缺口。
# ⛔ 新增任务若忘了写 partial，这里会红 —— 那是提醒你补一个，不是让你放宽这条。
_missing_partial = [t.id for t in SUITE.tasks if not t.partial.strip()]
check(RS, "C10 每条任务都带参考性部分解（中段覆盖面 100%，不是抽查）",
      not _missing_partial, str(_missing_partial))
check(RS, "C11 中段分数**各不相同**（尺子能区分不同质量的部分解，非单点）",
      len({round(r["partial"], 6) for r in _CAL.per_task
           if r["partial"] is not None}) >= 2,
      str(sorted({round(r["partial"], 6) for r in _CAL.per_task
                  if r["partial"] is not None})))


# ===========================================================================
print()
print("=" * 74)
print("D 组  反证组（契约⑥ 核心）—— 假尺子必须被判红")
print("=" * 74)

# D1 恒定打分器：无论输入什么都给 0.5 —— 这就是一把"恒等于 0"的尺子的同构体
_const = _write_scorer("const.py",
                       "import sys, json\n"
                       "json.loads(sys.stdin.read())\n"
                       "print('{\"score\": 0.5}')\n")
_cal_const = B.calibrate(B.ExternalScorer([sys.executable, str(_const)], source=_const), SUITE)
check(RS, "D1 **恒定打分器**（永远 0.5）⇒ 校准必须判红",
      not _cal_const.ok, f"ok={_cal_const.ok} reason={_cal_const.reason[:90]}")

# D2 反相关打分器：含 def 的答案给 0，其余给 1 ⇒ 好坏颠倒
_inv = _write_scorer("inverse.py",
                     "import sys, json\n"
                     "req = json.loads(sys.stdin.read())\n"
                     "s = 0.0 if 'def ' in str(req.get('answer','')) else 1.0\n"
                     "print(json.dumps({'score': s}))\n")
_cal_inv = B.calibrate(B.ExternalScorer([sys.executable, str(_inv)], source=_inv), SUITE)
check(RS, "D2 **反相关打分器**（好坏颠倒）⇒ 校准必须判红",
      not _cal_inv.ok, f"ok={_cal_inv.ok} reason={_cal_inv.reason[:90]}")

# D3 二值打分器：判分逻辑与真打分器**同源**（import 复用），但把结果阈值化。
#    ⇒ 两极依然可分（1 vs 0），但中段不再有序。
#    ⛔ 这一条是整组的重心：它证明"只做两极检查"是不够的，
#       也证明 C3/C6 的中段检查**真的在起作用**，而不是装饰。
_bin = _write_scorer(
    "binary.py",
    "import sys, json, subprocess, os\n"
    f"sys.path.insert(0, {str(REAL_SCORER.parent)!r})\n"
    "import code_exec\n"
    "req = json.loads(sys.stdin.read())\n"
    "task = req.get('task') or {}\n"
    "code = code_exec.extract_code(str(req.get('answer','')))\n"
    "tests = task.get('tests') or []\n"
    "if not tests or not code:\n"
    "    print(json.dumps({'score': 0.0})); raise SystemExit(0)\n"
    "passed = sum(1 for t in tests if code_exec.run_one(code, str(t))[0])\n"
    "raw = passed / len(tests)\n"
    "print(json.dumps({'score': 1.0 if raw >= 0.5 else 0.0}))\n")
_cal_bin = B.calibrate(B.ExternalScorer([sys.executable, str(_bin)], source=_bin), SUITE)
_bin_two_pole_ok = all(r["oracle"] > r["null"] for r in _cal_bin.per_task)
check(RS, "D3a 二值打分器**两极仍可分**（说明它并非坏尺子）", _bin_two_pole_ok,
      str([(r["id"], r["oracle"], r["null"]) for r in _cal_bin.per_task]))
check(RS, "D3b 二值打分器 ⇒ 校准必须判红（中段无序，C3/C6 不是装饰）",
      not _cal_bin.ok, f"ok={_cal_bin.ok} reason={_cal_bin.reason[:90]}")

# D4 校准失败时必须抛异常（fail-closed），不是安静地返回 ok=False 让人忽略
_raised = None
try:
    B.calibrate_or_raise(B.ExternalScorer([sys.executable, str(_const)], source=_const), SUITE)
except B.CalibrationError as e:
    _raised = str(e)
except Exception as e:  # noqa: BLE001
    _raised = f"抛了 {type(e).__name__}"
check(RS, "D4 calibrate_or_raise 在尺子不合格时 fail-closed 抛异常",
      bool(_raised) and "CalibrationError" not in str(_raised), str(_raised)[:80])

# D5 —— 这一条是 **R13 撤回态抓出来的真缺陷**。
#    把 calibrate 里的 `good = so.score > sn.score` 撤成 `good = True` 后，
#    D1/D2 **依然全绿**：因为恒定尺子与反相关尺子的 margin 本来就不为正，
#    它们是被 `margin > 0` 拦下的，逐条判据当时**一次都没被触发**。
#    ⇒ 造一个"仅某一条任务上两极不可分、但整体 margin 仍显著为正"的假尺子，
#      让逐条判据单独承担判别责任。撤掉它，D5b 必红。
#    ⚠️ 必须让该条任务**没有 partial**：有 partial 时 `null < partial < oracle`
#       在逻辑上蕴含 `oracle > null`，两条判据会重合，D5 就又失去判别力了。
#       而主任务集现在**每条都有 partial**（C10 钉的），所以这里必须**就地构造**
#       一个探针任务集，把 t02 的 partial 摘掉。
#       ⇒ 顺带如实登记一件事：`good` 判据在主任务集上是**冗余**的，
#         它的价值在"任务集没有参考性部分解"的通用场景（Suite 是公开 API）。
_pb_tasks = tuple(
    B.Task(id=t.id, prompt=t.prompt, tests=t.tests, oracle=t.oracle,
           partial=("" if t.id == "t02_dedupe" else t.partial),
           null=t.null, meta=t.meta)
    for t in SUITE.tasks)
_pb_suite = B.Suite(name="probe_no_partial_on_t02", tasks=_pb_tasks)
check(RS, "D5 前置：探针任务集里恰好一条没有 partial",
      sum(1 for t in _pb_suite.tasks if not t.partial.strip()) == 1,
      str([t.id for t in _pb_suite.tasks if not t.partial.strip()]))
_partial_broken = _write_scorer(
    "partial_broken.py",
    "import sys, json\n"
    f"sys.path.insert(0, {str(REAL_SCORER.parent)!r})\n"
    "import code_exec\n"
    "req = json.loads(sys.stdin.read())\n"
    "task = req.get('task') or {}\n"
    "if task.get('id') == 't02_dedupe':\n"
    "    print(json.dumps({'score': 0.0})); sys.exit(0)\n"
    "code = code_exec.extract_code(str(req.get('answer','')))\n"
    "tests = task.get('tests') or []\n"
    "if not tests or not code:\n"
    "    print(json.dumps({'score': 0.0})); sys.exit(0)\n"
    "passed = sum(1 for t in tests if code_exec.run_one(code, str(t))[0])\n"
    "print(json.dumps({'score': round(passed/len(tests), 4)}))\n")
_cal_pb = B.calibrate(B.ExternalScorer([sys.executable, str(_partial_broken)],
                                       source=_partial_broken), _pb_suite)
check(RS, "D5a 单条失分辨的假尺子：整体 margin **仍为正**（排除被 margin 拦下的可能）",
      _cal_pb.margin > 0, f"margin={_cal_pb.margin:+.4f}")
check(RS, "D5b 逐条两极判据**单独**拦下了它（撤掉该判据则本条必红）",
      not _cal_pb.ok, f"ok={_cal_pb.ok} reason={_cal_pb.reason[:90]}")


# ===========================================================================
print()
print("=" * 74)
print("E 组  消费计数（契约④）—— 打在「跑完了」上，两个早退分开计")
print("=" * 74)

_good_solver = lambda t: t.oracle  # noqa: E731
_m, _rep = B.run_suite(_good_solver, _REAL, SUITE, repeat_n=1, budget_tag="1x",
                       candidate="oracle")

check(RS, "E1 账本平：attempted == recorded + skipped_no_answer + failed_scorer",
      _rep.balanced(), _rep.summary_line())
check(RS, "E2 全部跑完 ⇒ recorded == 任务数、两种早退均为 0",
      _rep.recorded == len(SUITE) and _rep.skipped_no_answer == 0
      and _rep.failed_scorer == 0, _rep.summary_line())
check(RS, "E3 trustworthy 仅当三种早退全为 0", _rep.trustworthy(), _rep.summary_line())
check(RS, "E4 参考解跑真任务集 ⇒ 满分 1.0（尺子与任务集自洽）",
      abs(float(getattr(_m, "mean", 0.0)) - 1.0) < 1e-9, getattr(_m, "as_line", lambda: "")())


def _boom(t):
    raise RuntimeError("solver 挂了")


_m2, _rep2 = B.run_suite(_boom, _REAL, SUITE, repeat_n=1, budget_tag="1x", candidate="boom")
check(RS, "E5 solver 异常 ⇒ 全部计入 skipped_no_answer，**不**计入 failed_scorer",
      _rep2.skipped_no_answer == len(SUITE) and _rep2.failed_scorer == 0
      and _rep2.recorded == 0, _rep2.summary_line())
check(RS, "E6 两个早退分支**分开计**（合并成一个 skipped 就再也分不清修法）",
      hasattr(_rep2, "skipped_no_answer") and hasattr(_rep2, "failed_scorer"))
check(RS, "E7 有早退时 trustworthy 为假（读数不得用于判定）",
      not _rep2.trustworthy(), _rep2.summary_line())


def _empty_ans(t):
    return ""


_m3, _rep3 = B.run_suite(_empty_ans, _REAL, SUITE, repeat_n=1, budget_tag="1x",
                         candidate="empty")
check(RS, "E8 空答案 ⇒ 计入 skipped_no_answer（不是记 0 分混进分母）",
      _rep3.skipped_no_answer == len(SUITE) and _rep3.recorded == 0, _rep3.summary_line())

_crash_scorer = B.ExternalScorer([sys.executable, str(_crash)], source=_crash)
_m4, _rep4 = B.run_suite(_good_solver, _crash_scorer, SUITE, repeat_n=1, budget_tag="1x",
                         candidate="crashscorer")
check(RS, "E9 打分器全崩 ⇒ 计入 failed_scorer（与 solver 早退区分开）",
      _rep4.failed_scorer == len(SUITE) and _rep4.skipped_no_answer == 0,
      _rep4.summary_line())
check(RS, "E10 打分器全崩时读数**不可信**（防止把'没量到'当成'0 分'拿去判定）",
      not _rep4.trustworthy())

# 分数口径保守：一半任务没量到 ⇒ 分母不缩，分数被压低（而不是虚高）
# ⚠️ 这里必须按 id 前缀匹配：id 形如 t01_parse_duration，`t.id[-1]` 取到的是
#    函数名末字母而不是序号（第一版就是这么写错的）。
_half = lambda t: t.oracle if t.id[:3] in ("t01", "t02", "t03") else ""  # noqa: E731
_m5, _rep5 = B.run_suite(_half, _REAL, SUITE, repeat_n=1, budget_tag="1x", candidate="half")
check(RS, "E11 没量到的记 0 而非从分母剔除（防止系统性高估）",
      abs(float(getattr(_m5, "mean", -1)) - 3 / 6) < 1e-9,
      f"mean={float(getattr(_m5, 'mean', -1)):.4f} 期望 0.5000")

_mr, _repr = B.run_suite(_good_solver, _REAL, SUITE, repeat_n=3, budget_tag="1x",
                         candidate="oracle3x")
check(RS, "E12 repeat_n=N ⇒ n 条读数（分布而非单点）",
      getattr(_mr, "n", 0) == 3 and _repr.attempted == 3 * len(SUITE),
      f"n={getattr(_mr, 'n', 0)} attempted={_repr.attempted}")

# ---- 噪声的**性质**：必须能区分"稳定地错"与"随机地抖" ---------------------
# 两者可以给出**相同**的总分，但修法完全相反：
#     稳定地错 ⇒ 改候选（它每次都错在同一处，是可修的缺陷）
#     随机地抖 ⇒ 固定测量条件（改候选没有意义，噪声会淹没任何改动）
# 实测依据（真模型 T=0.8）：t04 稳定地 0.6667（系统误差），
# 而 t01 在 0.1667 ~ 0.8333 之间跳（随机误差）。
# 只报一个总分极差，这两种情况会被压成同一个数字。
_WRONG_CODE = "def __nothing_useful():\n    return None"
_flaky_seq = {"n": 0}


def _flaky_solver(t):
    _flaky_seq["n"] += 1
    # ⚠️ 周期必须与"每轮题目数"**不整除**，坏位置才会跨轮移动。
    #    第一版用 `n % 3`，而每轮 6 题 ⇒ 坏位置每轮固定落在第 3、6 题
    #    ⇒ 每轮总分完全相同 ⇒ 极差 0，与"稳定地错"再也分不开（E13 当场判红）。
    #    这不是测试写错那么简单：它说明"抖动"的判据里必须包含
    #    **"坏的位置跨轮变化"**，否则一个固定重复的坏模式会被误读成噪声。
    return _WRONG_CODE if _flaky_seq["n"] % 4 == 0 else t.oracle


_m_fl, _rep_fl = B.run_suite(_flaky_solver, _REAL, SUITE, repeat_n=3,
                             budget_tag="1x", candidate="flaky")
check(RS, "E13 随机抖动被识别：极差 > 0（抖动是噪声，不是能力）",
      (_m_fl.hi - _m_fl.lo) > 1e-9, f"极差={_m_fl.hi - _m_fl.lo:.4f}")

_m_st, _rep_st = B.run_suite(lambda t: _WRONG_CODE, _REAL, SUITE, repeat_n=3,
                             budget_tag="1x", candidate="stable_wrong")
check(RS, "E14 稳定地错 ≠ 随机抖动：极差 = 0 但水平 < 1（两个维度各承载信息）",
      (_m_st.hi - _m_st.lo) < 1e-9 and _m_st.mean < 1.0,
      f"极差={_m_st.hi - _m_st.lo:.4f} mean={_m_st.mean:.4f}")


# ===========================================================================
print()
print("=" * 74)
print("F 组  基线口径闸（契约①）—— 尺子不能随退化下移")
print("=" * 74)

_store = B.BaselineStore(_TMP / "baseline" / "bench_baseline.json")
_mA = summarize([0.80, 0.82, 0.81], candidate="A", budget_tag="1x")
_mB = summarize([0.55, 0.54, 0.56], candidate="B", budget_tag="1x")

_w1, _why1 = _store.save(_mA, _c1)
check(RS, "F1 首次写入基线成功", _w1, _why1)
check(RS, "F2 读回的读数与写入一致",
      (lambda r: r is not None and abs(r.mean - _mA.mean) < 1e-9)(_store.load_measurement()))

_w2, _why2 = _store.save(_mA, _c2)
check(RS, "F3 同口径再写 ⇒ 覆盖成功", _w2, _why2)

# ⛔ 核心：口径不同 ⇒ **拒绝**，且盘面**一个字节都不变**
_before = _md5(_store.path)
_w3, _why3 = _store.save(_mB, _c3)          # _c3 的 budget_tag 是 2x
_after = _md5(_store.path)
check(RS, "F4 口径不一致 ⇒ 拒绝写入", not _w3, _why3[:100] if _why3 else "")
check(RS, "F5 被拒绝后基线文件**字节不变**（标准不可被改写）",
      _before == _after, f"{_before[:8]} → {_after[:8]}")
check(RS, "F6 拒绝时**点名**差异字段", "budget_tag" in _why3, _why3[:120])
check(RS, "F7 拒绝后读回的仍是旧口径读数",
      (lambda r: r is not None and abs(r.mean - _mA.mean) < 1e-9)(_store.load_measurement()))

_w4, _why4 = _store.save(_mB, _c3, allow_supersede=True)
check(RS, "F8 allow_supersede=True ⇒ 写入成功", _w4, _why4[:80])
_sups = list(_store.path.parent.glob("*_superseded_*.json"))
check(RS, "F9 旧值被**另存**为 superseded（不销毁）", len(_sups) == 1,
      str([p.name for p in _sups]))
if _sups:
    _supd = json.loads(_sups[0].read_text(encoding="utf-8"))
    check(RS, "F10 superseded 文件带 superseded=true 与原因",
          _supd.get("superseded") is True and "口径变更" in str(_supd.get("superseded_reason", "")))
    check(RS, "F11 superseded 文件仍保有旧读数",
          abs(float(_supd["measurement"]["mean"]) - _mA.mean) < 1e-9)
else:
    check(RS, "F10 superseded 文件带 superseded=true 与原因", False, "无 superseded 文件")
    check(RS, "F11 superseded 文件仍保有旧读数", False, "无 superseded 文件")

check(RS, "F12 原子写：目录内无 .tmp 残留",
      not list(_store.path.parent.glob("*.tmp")),
      str([p.name for p in _store.path.parent.glob("*.tmp")]))

_w5, _why5 = _store.save(_mB, _c3)
check(RS, "F13 口径已切换后，同口径再写是覆盖而非再另存",
      _w5 and len(list(_store.path.parent.glob("*_superseded_*.json"))) == 1)


# ===========================================================================
print()
print("=" * 74)
print("G 组  证据可追溯（契约⑤）—— 从哪来 / 有多确定 / 什么没查到")
print("=" * 74)

_e1 = B.Evidence(observations=["a"], inferences=["b"], unknowns=["c"], limitations=["d"])
_e2 = B.Evidence(observations=["a"], inferences=["b"], unknowns=["c"], limitations=["d"])
_e3 = B.Evidence(observations=["a"], inferences=["b"], unknowns=["c"], limitations=["e"])
check(RS, "G1 同内容 ⇒ 同 evidence_id（证据 ID 由内容决定）",
      _e1.evidence_id() == _e2.evidence_id(), _e1.evidence_id())
check(RS, "G2 改一个字 ⇒ evidence_id 变（证据不可被悄悄改写）",
      _e1.evidence_id() != _e3.evidence_id())

_ev = getattr(_CAL, "evidence", None)
check(RS, "G3 校准证据四类齐全（观察/推断/未知/局限）",
      _ev is not None and all([getattr(_ev, "observations", None),
                               getattr(_ev, "inferences", None),
                               getattr(_ev, "unknowns", None),
                               getattr(_ev, "limitations", None)]))
check(RS, "G4 校准证据**必须**登记未知（'没查到'不得被读成'不存在'）",
      _ev is not None and len(getattr(_ev, "unknowns", [])) >= 1,
      str(getattr(_ev, "unknowns", []))[:120])
check(RS, "G5 校准证据的局限里写明尺子身份（scorer_sha/suite_sha）",
      _ev is not None and any("scorer_sha" in x for x in getattr(_ev, "limitations", [])),
      str(getattr(_ev, "limitations", []))[:120])
check(RS, "G6 as_text 输出含 evidence_id 与四类标签",
      _e1.as_text().startswith("evidence_id=") and all(
          lb in _e1.as_text() for lb in ("[观察]", "[推断]", "[未知]", "[局限]")))


# ===========================================================================
print()
print("=" * 74)
print("H 组  结构约束 —— 尺子物理上在内核之外")
print("=" * 74)

_kernel_imports_bench = []
for _name in CORE_MODULES:                     # 内核边界 = mw.CORE_MODULES（唯一事实源）
    _p = MW / f"{_name}.py"
    if not _p.exists():
        continue
    for n in ast.walk(ast.parse(_p.read_text(encoding="utf-8"))):
        mods = []
        if isinstance(n, ast.Import):
            mods = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods = [n.module]
        if any("bench" in m for m in mods):
            _kernel_imports_bench.append(f"{_p.name}:{n.lineno}")
check(RS, "H1 内核模块（mw.CORE_MODULES）**不依赖** bench —— 尺子在内核的依赖图之外",
      not _kernel_imports_bench, str(_kernel_imports_bench))
check(RS, "H1b bench 被显式登记为「非内核模块」（边界声明而非巧合）",
      "bench" in NON_CORE_MODULES and "bench" not in CORE_MODULES,
      f"core={CORE_MODULES} non_core={NON_CORE_MODULES}")

_frozen_ok = False
try:
    SUITE.tasks[0].id = "x"  # type: ignore[misc]
except Exception:  # noqa: BLE001
    _frozen_ok = True
check(RS, "H2 Task 是 frozen ⇒ 运行时不可改写（内核拿不到写入口）", _frozen_ok)

_dup = _TMP / "dup.jsonl"
_dup.write_text(
    json.dumps({"id": "a", "prompt": "p", "tests": ["assert True"]}) + "\n"
    + json.dumps({"id": "a", "prompt": "q", "tests": ["assert True"]}) + "\n",
    encoding="utf-8")
try:
    B.Suite.load(_dup)
    check(RS, "H3 任务 id 重复 ⇒ 显式报错（唯一事实源，不静默覆盖）", False, "没报错")
except ValueError as e:
    check(RS, "H3 任务 id 重复 ⇒ 显式报错（唯一事实源，不静默覆盖）", "重复" in str(e), str(e)[:60])

_bad = _TMP / "bad.jsonl"
_bad.write_text("{not json}\n", encoding="utf-8")
try:
    B.Suite.load(_bad)
    check(RS, "H4 坏行 ⇒ 显式报错（不静默跳过）", False, "没报错")
except ValueError as e:
    check(RS, "H4 坏行 ⇒ 显式报错（不静默跳过）", "JSON" in str(e), str(e)[:60])

# sha 的稳定性：重排版（换行/顺序）不算换尺子
_r1 = _TMP / "r1.jsonl"
_r2 = _TMP / "r2.jsonl"
_rows = [json.dumps({"id": "a", "prompt": "p", "tests": ["assert True"], "oracle": "def a(): pass"}),
         json.dumps({"id": "b", "prompt": "q", "tests": ["assert True"], "oracle": "def b(): pass"})]
_r1.write_text("\n".join(_rows) + "\n", encoding="utf-8")
_r2.write_text("\n".join(reversed(_rows)) + "\n", encoding="utf-8")
check(RS, "H5 suite_sha 对任务顺序不敏感（重排版不算换尺子）",
      B.Suite.load(_r1).sha() == B.Suite.load(_r2).sha())

# 但改校准材料必须换尺子
_rows3 = [json.dumps({"id": "a", "prompt": "p", "tests": ["assert True"],
                      "oracle": "def a(): pass", "partial": "def a(): return 1"}),
          _rows[1]]
_r3 = _TMP / "r3.jsonl"
_r3.write_text("\n".join(_rows3) + "\n", encoding="utf-8")
check(RS, "H6 改 partial（校准材料）⇒ suite_sha 变（那是换了尺子）",
      B.Suite.load(_r1).sha() != B.Suite.load(_r3).sha())

check(RS, "H7 校准材料不进打分器载荷（打分器看不到答案）",
      "oracle" not in SUITE.tasks[0].as_dict()
      and "null" not in SUITE.tasks[0].as_dict()
      and "partial" not in SUITE.tasks[0].as_dict(),
      str(sorted(SUITE.tasks[0].as_dict())))


# ===========================================================================
print()
print("=" * 74)
print("I 组  落盘出口（契约④ 最后一步）—— 计数必须能被别的进程读到")
print("=" * 74)

_jsonl = _TMP / "out" / "runs.jsonl"
B.append_jsonl(_jsonl, {"candidate": "oracle", "mean": 1.0})
B.append_jsonl(_jsonl, _rep.as_dict())
_lines = [json.loads(x) for x in _jsonl.read_text(encoding="utf-8").splitlines() if x.strip()]
check(RS, "I1 append_jsonl 逐行可解析且条数正确", len(_lines) == 2, str(len(_lines)))
check(RS, "I2 BenchReport.as_dict 含对账所需的四个计数",
      all(k in _lines[1] for k in ("attempted", "recorded",
                                   "skipped_no_answer", "failed_scorer")),
      str(sorted(_lines[1])))
check(RS, "I3 落盘文件在指定目录（非生产目录）",
      str(_jsonl).startswith(str(_TMP)))
check(RS, "I4 as_dict 含逐题明细，且条数 == recorded（噪声归因的唯一依据不许丢）",
      isinstance(_lines[1].get("per_task"), list)
      and len(_lines[1]["per_task"]) == _lines[1]["recorded"],
      f"per_task={len(_lines[1].get('per_task') or [])} recorded={_lines[1]['recorded']}")
check(RS, "I5 逐题明细**不截断**（区别于 errors 只留前 20 条）",
      len(_lines[1]["per_task"]) == _rep.recorded,
      f"{len(_lines[1]['per_task'])} vs {_rep.recorded}")

# 判据自身的判别力自检：反证组不能是空的。
# ⛔ 不写成 `if "D1" in globals() or True` —— 那是恒真断言，零判别力。
#    这里断言的是**可证伪的事实**：事故版打分器文件确实被创建出来了。
_fake_scorers = sorted(p.name for p in SCORER_DIR.glob("*.py"))
check(RS, "I6 反证组已实例化：事故版打分器文件真实存在且 ≥5 个",
      len(_fake_scorers) >= 5, str(_fake_scorers))

# ── 血缘：让 candidate 那串自由文本不能撒谎 ────────────────────────────
# 实测事故：标签写着"真模型 qwen2.5:7b"，内核日志写着 `llm=stub(离线)`，
# 读数 mean=0.0000 还标着"可信=✅"，而**没有任何机制**发现这个矛盾。
# 修法就是把"实际用了什么"变成读数的一部分，并且**取不到就是 unknown，不是默认成 http**。
_rep_np = B.BenchReport(candidate="自称是真模型的候选", budget_tag="1x")
check(RS, "I7 无血缘时 llm_of_record() = 'unknown'（缺证据**不许**默认成 http）",
      _rep_np.llm_of_record() == "unknown", _rep_np.llm_of_record())
_m7, _r7 = B.run_suite(lambda t: t.oracle, _REAL, SUITE, repeat_n=1, budget_tag="1x",
                       candidate="c", provenance=lambda: {"llm_kind": "stub"})
check(RS, "I8 provenance 落进读数，且 as_dict 里同时留下 llm_of_record（可跨进程核验）",
      _r7.llm_of_record() == "stub" and _r7.as_dict()["llm_of_record"] == "stub",
      str(_r7.as_dict().get("llm_of_record")))


def _boom_prov():
    raise RuntimeError("血缘取不到")


_m8, _r8 = B.run_suite(lambda t: t.oracle, _REAL, SUITE, repeat_n=1, budget_tag="1x",
                       candidate="c", provenance=_boom_prov)
check(RS, "I9 血缘回调抛异常 ⇒ 不带走整轮，但必须留痕（否则静默变成无血缘）",
      _r8.llm_of_record() == "unknown" and any("provenance" in e for e in _r8.errors),
      str(_r8.errors[:1]))


# ===========================================================================
print()
print("=" * 74)
print(f"V1 外部锚层契约测试汇总  {RS.summary_line()}")
print("=" * 74)
if RS.fail_n:
    print(f"失败断言数 = {RS.fail_n}")
raise SystemExit(1 if RS.fail_n else 0)
