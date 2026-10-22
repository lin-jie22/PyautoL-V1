"""能力曲线装置的自检探针 —— 零模型、零网络。

判断的不是"曲线好不好看"，是**装置能不能说谎**：
  ① oracle 恒 1.0 吗（尺子知道自己答案时，曲线必须平）
  ② 趋势判据的边界对不对（每一条都配一个反例，否则断言是死的）
  ③ 运行中换尺子会被抓住吗（约束 1 的撤回态）
  ④ 口径变了会断线吗（不是过滤，是开新段）

跑法：python3 bench/loop_probe.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mw import bench, loop  # noqa: E402

RS: list[str] = []
fails = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global fails
    mark = "✅" if cond else "❌"
    if not cond:
        fails += 1
    RS.append(f"{mark} {name}" + (f" — {detail}" if detail else ""))


def _pt(rnd: float, calib: str, mean: float, trustworthy: bool = True) -> loop.CurvePoint:
    return loop.CurvePoint(round=int(rnd), ts=0.0, calib=calib, mean=mean,
                           n=1, spread=0.0, trustworthy=trustworthy)


def test_trend_borders() -> None:
    """趋势判据的每一条分支都要有正例**和**反例。"""
    # 1) 恒 0.5 → FLAT
    c = loop.Curve([_pt(i, "A", 0.5) for i in (1, 2, 3)])
    v, _ = c.trend(0.0)
    check("恒 0.5 三轮 ⇒ FLAT（不声称改进）", v == loop.FLAT, v)

    # 2) 0.5 → 0.9/0.9，噪声 0 → IMPROVED
    c = loop.Curve([_pt(1, "A", 0.5), _pt(2, "A", 0.9), _pt(3, "A", 0.9)])
    v, _ = c.trend(0.0)
    check("0.5→0.9→0.9，噪声 0 ⇒ IMPROVED", v == loop.IMPROVED, v)

    # 3) 同一组数据，噪声 0.5 → 不够 ⇒ 必须是 FLAT（断言 FLAT 而非「不是 IMPROVED」：
    #    后者在判据退化成 UNKNOWN 时也会通过，那是一条抓不住退化的松断言）
    v, why = c.trend(0.5)
    check("同一组数据噪声 0.5 ⇒ FLAT（不是「非改进」而是明确判平）", v == loop.FLAT, f"{v} / {why}")

    # 4) 中间涨过但又掉回基线以下 ⇒ UNKNOWN，不是 IMPROVED
    c = loop.Curve([_pt(1, "A", 0.5), _pt(2, "A", 0.9), _pt(3, "A", 0.4)])
    v, _ = c.trend(0.0)
    check("0.5→0.9→0.4 ⇒ UNKNOWN（最低一轮未超过基线）", v == loop.UNKNOWN, v)

    # 5) 轮数不足
    c = loop.Curve([_pt(1, "A", 0.5), _pt(2, "A", 0.9)])
    v, _ = c.trend(0.0)
    check("只有 2 轮 ⇒ UNKNOWN（样本不足）", v == loop.UNKNOWN, v)

    # 6) 没有噪声下限 ⇒ UNKNOWN，不许默认 0
    c = loop.Curve([_pt(i, "A", 0.5 + i * 0.2) for i in (1, 2, 3)])
    v, _ = c.trend(None)
    check("噪声下限 None ⇒ UNKNOWN（不默认 0）", v == loop.UNKNOWN, v)

    # 7) 不可信的点独占一段 ⇒ 后面的可信点不与它连
    c = loop.Curve([_pt(1, "A", 0.5), _pt(2, "A", 0.5, trustworthy=False),
                    _pt(3, "A", 0.9), _pt(4, "A", 0.9), _pt(5, "A", 0.9)])
    segs = c.segments()
    check("不可信点把曲线切断（不是被过滤掉）", len(segs) == 3, f"{[len(s) for s in segs]}")

    # 8) 口径变了 ⇒ 开新段
    c = loop.Curve([_pt(i, "A", 0.5) for i in (1, 2, 3)]
                   + [_pt(i, "B", 0.9) for i in (4, 5, 6)])
    segs = c.segments()
    check("口径变化 ⇒ 断成两段", len(segs) == 2 and len(segs[0]) == 3 and len(segs[1]) == 3,
          f"{[len(s) for s in segs]}")


def test_oracle_self_check() -> None:
    """真跑一遍 oracle：曲线必须恒 1.0，且不被判成改进。"""
    cal = bench.Caliber(scorer_sha="probe", suite_sha="probe", repeat=1,
                        budget_tag="1x", temperature=0.0, fresh=True,
                        memory_scope="per_task_fresh", memory_seed="",
                        limits={"probe": True}, llm_cond={"kind": "oracle"},
                        max_tokens=1024, tool_catalog_sha="probe")
    sc_path = ROOT / "bench" / "scorers" / "code_exec.py"
    scorer = bench.ExternalScorer([sys.executable, str(sc_path)], source=sc_path)
    suite = bench.Suite.load(ROOT / "bench" / "tasks" / "coding_small.jsonl")
    ok, notes = loop.oracle_self_check(scorer=scorer, suite=suite,
                                       caliber_factory=lambda: cal, rounds=3)
    for n in notes:
        RS.append("    " + n)
    check("oracle 自检通过（恒 1.0 且不被判成改进）", ok)

    # 反证的前提条件本身也要有牙：rounds<3 必须 raise，否则趋势反证恒真
    raised = False
    try:
        loop.oracle_self_check(scorer=scorer, suite=suite,
                               caliber_factory=lambda: cal, rounds=2)
    except ValueError as e:
        raised = "恒真断言" in str(e)
    check("rounds<3 被拒绝（防止趋势反证变成恒真断言）", raised)


def test_swap_detected() -> None:
    """约束 1 的撤回态：运行中换尺子必须 raise，而不是悄悄继续。"""
    cal = bench.Caliber(scorer_sha="probe", suite_sha="probe", repeat=1,
                        budget_tag="1x", temperature=0.0, fresh=True,
                        memory_scope="per_task_fresh", memory_seed="",
                        limits={"probe": True}, llm_cond={"kind": "oracle"},
                        max_tokens=1024, tool_catalog_sha="probe")
    tmp = Path(tempfile.mkdtemp(prefix="v1_loop_probe_"))
    try:
        sc = tmp / "scorer.py"
        shutil.copy(ROOT / "bench" / "scorers" / "code_exec.py", sc)
        sj = tmp / "suite.jsonl"
        first = json.loads((ROOT / "bench" / "tasks" / "coding_small.jsonl")
                           .read_text(encoding="utf-8").splitlines()[0])
        sj.write_text(json.dumps(first, ensure_ascii=False) + "\n", encoding="utf-8")

        scorer = bench.ExternalScorer([sys.executable, str(sc)], source=sc)
        suite = bench.Suite.load(sj)

        def mutate(_p: loop.CurvePoint) -> None:
            sc.write_text(sc.read_text(encoding="utf-8") + "\n# mutated\n", encoding="utf-8")

        raised = False
        try:
            loop.run_loop(solver=lambda t: t.oracle, scorer=scorer, suite=suite,
                          caliber_factory=lambda: cal, rounds=3,
                          log=lambda s: None, on_round=mutate)
        except RuntimeError as e:
            raised = "打分器文件在运行中被改动" in str(e)
        check("运行中改打分器 ⇒ 立即停止（撤回态）", raised)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_real_improvement_detected() -> None:
    """端到端反证：**每一轮真的变强时，曲线必须判成 IMPROVED**。

    为什么这条不能只靠上面第 2 条合成点：那一条证明的是 `trend()` 会算，
    证明不了 `run_loop` **把每轮的读数真的接上去了**。若 `run_loop` 复用首轮
    报告（比如把 `rep` 建在循环外），曲线会永远平 —— 而合成点测试照样全绿。
    这正是"装置看起来对、实际没接线"的经典形态（本仓库 7w 那一轮就是）。
    """
    cal = bench.Caliber(scorer_sha="probe", suite_sha="probe", repeat=1,
                        budget_tag="1x", temperature=0.0, fresh=True,
                        memory_scope="per_task_fresh", memory_seed="",
                        limits={"probe": True}, llm_cond={"kind": "oracle"},
                        max_tokens=1024, tool_catalog_sha="probe")
    sc_path = ROOT / "bench" / "scorers" / "code_exec.py"
    scorer = bench.ExternalScorer([sys.executable, str(sc_path)], source=sc_path)
    suite = bench.Suite.load(ROOT / "bench" / "tasks" / "coding_small.jsonl")

    n = len(suite.tasks)
    calls = {"k": 0}

    def improving(t) -> str:
        # 第 1 轮的 n 次调用全给错答案，之后全给正确答案。
        calls["k"] += 1
        return "N/A" if calls["k"] <= n else t.oracle

    curve = loop.run_loop(solver=improving, scorer=scorer, suite=suite,
                          caliber_factory=lambda: cal, rounds=3, log=lambda s: None)
    means = [p.mean for p in curve.points]
    check("首轮 0.0、后两轮 1.0（run_loop 每轮真的重新判分）",
          means == [0.0, 1.0, 1.0], f"{means}")
    v, why = curve.trend(0.0)
    check("真·逐轮变强 ⇒ IMPROVED（不是只会判平）", v == loop.IMPROVED, f"{v} / {why}")


def main() -> int:
    test_trend_borders()
    test_oracle_self_check()
    test_swap_detected()
    test_real_improvement_detected()
    print("=" * 62)
    for line in RS:
        print(line)
    print("=" * 62)
    print(f"结论：{'✅ 全部通过' if fails == 0 else f'❌ {fails} 项失败'}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
