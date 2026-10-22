"""常驻循环与能力曲线 —— 让"有没有变强"变成一条可读的线。

为什么这一个文件在内核之外
--------------------------
`core.py` 一次只跑一个目标，跑完就返回。这是刻意的：**内核不该知道"第几轮"**。
一旦它知道，它就有动机为了让曲线好看而调整行为 —— 那正是自评分，
是整个测量层花了两百多条断言去堵的那条路。

所以"一直跑"这个属性只放在外层：
    loop 管循环 ／ core 管单次 ／ curve 管判定
三者互不知道对方的内部，谁也不能替谁下结论。

三条硬约束（少一条这个文件就退化成装饰）
----------------------------------------
1. **判据只读，且每轮校验。** 打分器与题集在启动时快照 (sha)，之后每轮重算。
   文件变了就**立即停止**，不写这个点 —— 换尺子之后的点若接到旧曲线上，
   那条曲线就在说谎，而且是没人能看出来的那种谎。
2. **每个点带口径签名。** 签名走 `Caliber.signature()`（唯一实现），不自己算。
   口径变了就**断线开新段**，不是把异口径的点过滤掉 ——
   断线保留"这里换过尺子"这个事实，过滤会把它删掉。
3. **不可信的读数不许参与趋势。** `BenchReport.trustworthy()` 为假 ⇒ 该点
   独占一段（长度 1）⇒ 趋势判定自动退化为 UNKNOWN。
   "没量准"和"量准了但分数低"是两件事，混在一起就再也分不清。

趋势只有三种结论
----------------
`IMPROVED` / `FLAT` / `UNKNOWN`。**UNKNOWN 不是失败，是"还没有资格说话"。**
判定条件刻意定得很难满足：之后**每一轮**都必须超过"首轮 + 噪声下限"，
才能说 IMPROVED —— 比"末轮比首轮高"严格得多，因为后者一句话就能被抖动造出来。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import bench

#: 趋势判定的三个取值。**不允许出现第四种**（比如 `MAYBE`）——
#: 一个含糊的档位会让"没证据"看起来像"有一点证据"。
IMPROVED = "IMPROVED"
FLAT = "FLAT"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class CurvePoint:
    """曲线上一个点。字段不可省 —— 少一个就无法复核这个点凭什么在这条线上。"""

    round: int
    ts: float
    calib: str
    mean: float
    n: int
    spread: float
    trustworthy: bool
    per_task: dict[str, float] = field(default_factory=dict)
    #: 早退计数与理由。**不可信的点必须能自证"为什么不可信"** ——
    #: 第六十四轮第一次真跑就撞上了这件事：三个点全是 ⛔，而证据文件里
    #: 一个字的理由都没有，只能回终端翻日志。那是"证据不自描述"。
    #: `skipped` = 没产出答案（`skipped_no_answer`），`failed` = 打分器自己失败
    #: （`failed_scorer`）。两者修法完全不同，所以分开记、不许合并成一个"缺失"。
    skipped: int = 0
    failed: int = 0
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "round": self.round,
            "ts": self.ts,
            "calib": self.calib,
            "mean": self.mean,
            "n": self.n,
            "spread": self.spread,
            "trustworthy": self.trustworthy,
            "per_task": dict(self.per_task),
            "skipped": self.skipped,
            "failed": self.failed,
            "errors": list(self.errors),
        }

    def as_line(self) -> str:
        if self.trustworthy:
            flag = ""
        else:
            why = []
            if self.skipped:
                why.append(f"未交答案 {self.skipped}")
            if self.failed:
                why.append(f"打分失败 {self.failed}")
            flag = "  ⛔不可信（不参与趋势" + ("：" + "、".join(why) if why else "") + "）"
        return (f"第 {self.round} 轮  mean={self.mean:.4f}  n={self.n}  "
                f"极差={self.spread:.4f}  口径={self.calib[:12]}{flag}")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CurvePoint":
        # 旧文件（第六十三轮那三格）没有这三个键 ⇒ 默认 0/空，不许因为缺键就丢掉整点。
        return cls(round=int(d["round"]), ts=float(d["ts"]), calib=str(d["calib"]),
                   mean=float(d["mean"]), n=int(d["n"]), spread=float(d["spread"]),
                   trustworthy=bool(d.get("trustworthy", False)),
                   per_task={str(k): float(v) for k, v in (d.get("per_task") or {}).items()},
                   skipped=int(d.get("skipped", 0) or 0),
                   failed=int(d.get("failed", 0) or 0),
                   errors=tuple(str(x) for x in (d.get("errors") or ())))


@dataclass
class Curve:
    """能力曲线：按口径切成若干**连续段**，每段内部才允许比较。"""

    points: list[CurvePoint] = field(default_factory=list)

    def add(self, p: CurvePoint) -> None:
        self.points.append(p)

    def __len__(self) -> int:
        return len(self.points)

    def segments(self) -> list[list[CurvePoint]]:
        """按「口径相同 且 上一个点可信」切段。

        不可信的点**独占一段**：它既不能被接到前一段上（会污染趋势），
        也不该被丢掉（那样就看不到"这一轮没量准"这个事实）。
        """
        out: list[list[CurvePoint]] = []
        for p in self.points:
            if out and p.trustworthy and out[-1][-1].calib == p.calib and out[-1][-1].trustworthy:
                out[-1].append(p)
            else:
                out.append([p])
        return out

    def latest(self) -> list[CurvePoint]:
        segs = self.segments()
        return segs[-1] if segs else []

    def trend(self, noise_floor: float | None) -> tuple[str, str]:
        """判断当前这一段有没有在变强。返回 (verdict, why)。

        `noise_floor=None` 一律 UNKNOWN —— **不是**默认 0。
        默认 0 等于"任何微小差值都算改进"，那是最容易被抖动骗到的写法。
        """
        seg = self.latest()
        if len(seg) < 3:
            return UNKNOWN, f"同口径只有 {len(seg)} 轮（要求 ≥ 3）⇒ 不判定趋势"
        if noise_floor is None:
            return UNKNOWN, "没有噪声下限 ⇒ 任何差值都不可判定（不默认 0）"
        base = seg[0].mean
        later = [p.mean for p in seg[1:]]
        worst = min(later)
        # ⛔ 浮点容差不是"保险"，是必需：oracle 三轮都是 1.0，但 `1.0 - 1.0`
        #    在 0.7/0.2 这类均值上会留下 1e-10 量级的残差。没有容差时
        #    `worst > base + 0` 对一条**恒等**的曲线为真 ⇒ 恒 1.0 被判成 IMPROVED。
        #    这个 bug 是 `oracle_self_check` 第一次真跑时自己抓出来的，
        #    不是在推理里发现的 —— 这就是"反证必须能真的失败"的价值。
        _tol = 1e-9
        if worst > base + noise_floor + _tol:
            return IMPROVED, (f"之后每一轮都 > 首轮 {base:.4f} + 噪声 {noise_floor:.4f}"
                              f"（最低一轮 {worst:.4f}）⇒ 这是唯一够格的证据")
        if all(abs(m - base) <= noise_floor + _tol for m in later):
            return FLAT, f"后续各轮与首轮 {base:.4f} 的差都 ≤ 噪声 {noise_floor:.4f}"
        return UNKNOWN, (f"有超出噪声的波动，但最低一轮 {worst:.4f} 未超过"
                         f"首轮+噪声 {base + noise_floor:.4f} ⇒ 不声称改进")

    def as_lines(self) -> list[str]:
        out: list[str] = []
        for i, seg in enumerate(self.segments(), 1):
            out.append(f"── 第 {i} 段（{len(seg)} 轮，口径 {seg[0].calib[:12]}）")
            for p in seg:
                out.append("   " + p.as_line())
        return out

    def save_jsonl(self, path: Path) -> int:
        """追加写（不是覆盖）：曲线是**过程记录**，同一文件横跨多次运行。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for p in self.points:
                f.write(json.dumps(p.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        return len(self.points)

    @classmethod
    def load_jsonl(cls, path: Path) -> "Curve":
        c = cls()
        if not path.exists():
            return c
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                c.add(CurvePoint.from_dict(json.loads(line)))
        return c


def run_loop(
    *,
    solver: Callable[[Any], str],
    scorer: bench.ExternalScorer,
    suite: bench.Suite,
    caliber_factory: Callable[[], bench.Caliber],
    rounds: int,
    repeat_n: int = 1,
    candidate: str = "loop",
    budget_tag: str = "?",
    log: Callable[[str], None] = print,
    on_round: Callable[[CurvePoint], None] | None = None,
) -> Curve:
    """跑 `rounds` 轮，每轮产出一个曲线点。

    `caliber_factory` 每轮**重新调用**（不是复用一个构造好的 Caliber）：
    口径是"文件 + 配置"的函数，文件可能在中途被改，必须每轮现算。
    """
    if rounds < 1:
        raise ValueError("rounds 必须 ≥ 1")
    curve = Curve()
    s0, q0 = scorer.sha(), suite.sha()
    for rnd in range(1, rounds + 1):
        # ── 每轮校验判据未被改动。这是约束 1 的落点，不是可选的体检 ──
        if scorer.sha() != s0:
            raise RuntimeError(
                f"第 {rnd} 轮：打分器文件在运行中被改动 ⇒ 立即停止。"
                "换尺子之后的点不许接到旧曲线上（那条线会变成没人看得出的谎）。")
        if suite.sha() != q0:
            raise RuntimeError(f"第 {rnd} 轮：题集文件在运行中被改动 ⇒ 立即停止。")
        cal = caliber_factory()
        m, rep = bench.run_suite(solver, scorer, suite, repeat_n=repeat_n,
                                 budget_tag=budget_tag, candidate=candidate)
        per = {str(r.get("id")): float(r.get("score") or 0.0) for r in rep.per_task}
        p = CurvePoint(round=rnd, ts=time.time(), calib=cal.signature(),
                       mean=float(m.mean), n=int(m.n), spread=float(m.spread),
                       trustworthy=bool(rep.trustworthy()), per_task=per,
                       # 早退原因必须跟着点一起落盘：否则"不可信"只是一个没有主语的形容词
                       skipped=int(rep.skipped_no_answer),
                       failed=int(rep.failed_scorer),
                       # 只留前 3 条：够看出"缺了哪几题、为什么"，又不把 JSONL 撑成日志
                       errors=tuple(list(rep.errors)[:3]))
        curve.add(p)
        log("  " + p.as_line())
        if on_round is not None:
            on_round(p)
    return curve


def oracle_self_check(
    *,
    scorer: bench.ExternalScorer,
    suite: bench.Suite,
    caliber_factory: Callable[[], bench.Caliber],
    rounds: int = 3,
) -> tuple[bool, list[str]]:
    """反证：`oracle` 的曲线必须**逐轮恒等于 1.0**。

    这是整套装置唯一的自我证明：如果连一把**知道正确答案**的尺子都画不出
    恒 1.0 的线，那这条线量到的任何"趋势"都不成立。
    同时它也是趋势判据的反证 —— 恒 1.0 被判成 IMPROVED，就说明判据写成
    宽松比较（比如 `>=`），那么它对真实改进也不会有分辨力。
    """
    notes: list[str] = []
    # ⛔ 必须 ≥ 3：`trend()` 在轮数 < 3 时一律返回 UNKNOWN（样本不足）。
    #    所以用 2 轮做"不会被判成改进"的反证，那条反证**先天不可能失败** ——
    #    它不是证明了判据对，只是证明了轮数不够。反证必须保证它能失败。
    if rounds < 3:
        raise ValueError(f"oracle_self_check 需要 rounds ≥ 3（收到 {rounds}）；"
                         "否则趋势反证会被『样本不足』掩盖，成为恒真断言")
    curve = run_loop(solver=lambda t: t.oracle, scorer=scorer, suite=suite,
                     caliber_factory=caliber_factory, rounds=rounds,
                     candidate="oracle", log=lambda s: None)
    off = [p for p in curve.points if abs(p.mean - 1.0) > 1e-9]
    if off:
        notes.append("❌ oracle 出现非 1.0 的点：" + "，".join(
            f"第{p.round}轮={p.mean:.4f}" for p in off))
        return False, notes
    notes.append(f"✅ oracle {len(curve.points)} 轮全部 1.0000 ⇒ 装置本身可信")
    verdict, why = curve.trend(noise_floor=0.0)
    if verdict == IMPROVED:
        notes.append(f"❌ 恒 1.0 的曲线被判成 IMPROVED ⇒ 趋势判据过松（{why}）")
        return False, notes
    notes.append(f"✅ 趋势判据对恒 1.0 曲线给出 {verdict}（{why}）")
    return True, notes


# ---------------------------------------------------------------------------
# CLI —— 把"一直跑"变成一个能被 Ctrl-C 停下来的命令
# ---------------------------------------------------------------------------
#
# ⛔ 这个 CLI **只跑零模型参照**（oracle / 一半对一半错 / 空答案）。
#    真模型的能力读数走 `run.py --bench`，不在这里开第二套口径构造。
#    理由：真口径要 `limits` / `llm_cond` / `tool_catalog_sha` 与 Core 实建的
#    注册表逐字一致（`run.py:888` 是唯一构造点，契约 AF1 拿它对撞）。
#    在这里再写一份，就是"同一事实两处声明"—— 本仓库在这条上翻过车。

_REPO_SUITE = Path("bench") / "tasks" / "coding_small.jsonl"
_REPO_SCORER = Path("bench") / "scorers" / "code_exec.py"
_DEFAULT_OUT = Path("bench") / "evidence" / "loop" / "curve.jsonl"


def _make_partial(suite: bench.Suite) -> Callable[[Any], str]:
    """参照 solver：**按 id 排序后的位置**，偶数位给正确答案、奇数位给 "N/A"。

    它存在的唯一理由：**证明这条线有分辨力**。oracle 恒 1.0 只能证明装置不自伤，
    证明不了它能停在别的位置 —— 一把永远画 1.0 的量具，和一把坏掉的量具长得一样。

    ⚠️ 第一版写成 `sum(map(ord, id)) % 2`，实测 6 题里 5 题落在偶数 ⇒ 读数 0.8333，
    而它的说明写着"一半对一半错"。**说明是假的**，虽然判据没坏。
    改成按排序位置取，读数才真的等于 0.5；位置与题序无关（先排序），
    所以换一次题序读数也不变。
    """
    good = {tid for i, tid in enumerate(sorted(suite.ids())) if i % 2 == 0}
    return lambda t: (t.oracle if str(t.id) in good else "N/A")


def _solver_for(kind: str, suite: bench.Suite) -> Callable[[Any], str]:
    """把 CLI 的档位名变成 solver。`partial` 需要题集，所以不是一张静态表。"""
    if kind == "partial":
        return _make_partial(suite)
    if kind == "oracle":
        return lambda t: t.oracle
    # 空答案 ⇒ `skipped_no_answer` ⇒ 这一点不可信。它专门用来**点亮** ⛔ 那条路：
    # 如果 `--solver null` 画出来的点仍然是"可信"，那 `trustworthy()` 就是装饰。
    if kind == "null":
        return lambda t: ""
    raise ValueError(f"未知 solver 档位：{kind}")


#: CLI 的 `--solver` 取值。**不许有第四个** —— 每多一个档位就多一种"读数不好看
#: 就换个参照"的余地，而参照的意义恰恰是它不能因结果而改。
_SOLVER_KINDS = ("oracle", "partial", "null")


def _reference_caliber(scorer: bench.ExternalScorer, suite: bench.Suite,
                       solver: str, repeat_n: int, budget_tag: str) -> bench.Caliber:
    """参照口径。**不是**能与 `run.py --bench` 对账的真模型口径。

    12 个字段一个不少地写满，且把无法真实提供的部分**写成标签**而不是留空：
    `llm_cond={"kind": "reference:<solver>"}`、`tool_catalog_sha="reference"`、
    `max_tokens=0`（参照 solver 不生成任何东西）。留空会变成哨兵 `_MISSING`，
    那等于让"我不知道"冒充"没有"。
    """
    return bench.Caliber(
        scorer_sha=scorer.sha(), suite_sha=suite.sha(), repeat=repeat_n,
        budget_tag=budget_tag, temperature=0.0, fresh=True,
        memory_scope="per_task_fresh", memory_seed="",
        limits={"reference": True},
        llm_cond={"kind": f"reference:{solver}"},
        max_tokens=0,
        tool_catalog_sha="reference",
    )


def _append_jsonl(path: Path, p: CurvePoint) -> None:
    """单点追加。**逐轮**写，不等整轮结束。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(p.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m mw.loop",
        description="常驻循环 + 能力曲线（零模型参照）。真模型读数请用 `run.py --bench`。")
    ap.add_argument("--rounds", type=int, default=3, help="跑多少轮（默认 3）")
    ap.add_argument("--forever", action="store_true", help="一直跑到 Ctrl-C")
    ap.add_argument("--interval", type=float, default=0.0, help="轮间隔秒数")
    ap.add_argument("--solver", choices=_SOLVER_KINDS, default="oracle",
                    help="参照 solver（oracle / partial / null）")
    ap.add_argument("--suite", default=str(_REPO_SUITE))
    ap.add_argument("--scorer", default=str(_REPO_SCORER))
    ap.add_argument("--repeat", type=int, default=1, help="每轮每题重复次数")
    ap.add_argument("--budget-tag", default="1x")
    ap.add_argument("--out", default=str(_DEFAULT_OUT))
    ap.add_argument("--noise-floor", type=float, default=None,
                    help="噪声下限。不给**就不判定趋势**（不默认 0）")
    ap.add_argument("--self-check", action="store_true", help="只跑 oracle 反证并退出")
    args = ap.parse_args(argv)

    suite_p, scorer_p = Path(args.suite), Path(args.scorer)
    for p, what in ((suite_p, "题集"), (scorer_p, "打分器")):
        if not p.exists():
            # 明确报错，不去别处找同名文件 —— "找不到用户的路径"被悄悄换成
            # 别处的同名文件，是比报错更坏的假成功（`run.data_path` 的注释里
            # 定的同一条规矩，这里只对**默认值**适用兜底，本 CLI 没有兜底）。
            print(f"❌ 找不到{what}：{p}", file=sys.stderr)
            return 2

    scorer = bench.ExternalScorer([sys.executable, str(scorer_p)], source=scorer_p)
    suite = bench.Suite.load(suite_p)

    def factory() -> bench.Caliber:
        # ★ 每轮现算（不是复用一个构造好的对象）：口径是"文件 + 配置"的函数。
        return _reference_caliber(scorer, suite, args.solver, args.repeat, args.budget_tag)

    cal0 = factory()
    if args.self_check:
        ok, notes = oracle_self_check(scorer=scorer, suite=suite,
                                      caliber_factory=factory,
                                      rounds=max(3, args.rounds))
        for n in notes:
            print("  " + n)
        return 0 if ok else 1

    rounds = 10 ** 9 if args.forever else args.rounds
    if args.forever and args.interval <= 0:
        print("⚠️ --forever 但 --interval=0 ⇒ 会全速空转（按 Ctrl-C 停）。")

    out = Path(args.out)
    kept = Curve()          # main 自己留一份：run_loop 的返回值在 Ctrl-C 时拿不到

    def on_round(p: CurvePoint) -> None:
        kept.add(p)
        _append_jsonl(out, p)   # 逐轮落盘，理由见 `_append_jsonl`
        if args.forever and args.interval > 0:
            time.sleep(args.interval)

    print(f"循环开始：solver={args.solver}  题集={suite_p.name}({len(suite)} 题)  "
          f"重复={args.repeat}  口径={cal0.signature()[:12]}")
    stopped: str | None = None
    try:
        run_loop(solver=_solver_for(args.solver, suite), scorer=scorer, suite=suite,
                 caliber_factory=factory, rounds=rounds, repeat_n=args.repeat,
                 budget_tag=args.budget_tag, candidate=f"loop:{args.solver}",
                 on_round=on_round)
    except KeyboardInterrupt:
        print("\n（Ctrl-C：已跑的轮次都已落盘，下面只按已有数据判定）")
    except RuntimeError as e:
        stopped = str(e)
        print(f"\n⛔ 已停止：{e}")

    print("=" * 62)
    for line in kept.as_lines():
        print(line)
    print("=" * 62)
    verdict, why = kept.trend(args.noise_floor)
    print(f"趋势：{verdict} —— {why}")
    if args.noise_floor is None:
        print("（未给 --noise-floor ⇒ UNKNOWN 是设计上的默认，不是失败）")
    print(f"曲线已追加到 {out}（本次 {len(kept)} 个点）")
    return 2 if stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
