#!/usr/bin/env python3
"""外部锚层契约测试 —— 离线、零依赖、可独立运行。

这一套测试只关心一件事：**我们能不能相信一个分数。**
（V5 的 136 轮、99 轮故障全部集中在"反馈能不能被相信"，没有一条出自引擎。）

因此它的重心不是"功能对不对"，而是**反证组**：
   D 组用一个**没有分辨力的假打分器**去打真任务集，要求 calibrate **必须判红**。
   如果 D 组不红，说明"判别力检查"是死代码 —— 那么 C 组的绿也是假的。

三条从 V5 换来的纪律照旧：
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
check(RS, "A2 口径键只有这五个（供给侧运行时状态刻意不入闸）",
      tuple(B.CALIBER_KEYS) == ("scorer_sha", "suite_sha", "repeat", "budget_tag", "fresh"),
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
#    V5 的病灶之一就是"缺失被读成否定"，这条钉住反面。
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


# ===========================================================================
print()
print("=" * 74)
print("D 组  反证组（契约⑥ 核心）—— 假尺子必须被判红")
print("=" * 74)

# D1 恒定打分器：无论输入什么都给 0.5 —— 这就是 V5 那把"恒等于 0"的尺子的同构体
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
#    ⚠️ 必须挑一条**没有 partial** 的任务：有 partial 时 `null < partial < oracle`
#       在逻辑上蕴含 `oracle > null`，两条判据会重合，D5 就又失去判别力了
#       （第一版挑了 t01，正是踩了这个坑）。
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
                                       source=_partial_broken), SUITE)
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

# 判据自身的判别力自检：反证组不能是空的。
# ⛔ 不写成 `if "D1" in globals() or True` —— 那是恒真断言，零判别力。
#    这里断言的是**可证伪的事实**：事故版打分器文件确实被创建出来了。
_fake_scorers = sorted(p.name for p in SCORER_DIR.glob("*.py"))
check(RS, "I4 反证组已实例化：事故版打分器文件真实存在且 ≥5 个",
      len(_fake_scorers) >= 5, str(_fake_scorers))


# ===========================================================================
print()
print("=" * 74)
print(f"V1 外部锚层契约测试汇总  {RS.summary_line()}")
print("=" * 74)
if RS.fail_n:
    print(f"失败断言数 = {RS.fail_n}")
raise SystemExit(1 if RS.fail_n else 0)
