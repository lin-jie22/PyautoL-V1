#!/usr/bin/env python3
"""第二十一轮结论探针：**「校验」这条腿到底通没通？**（零模型调用）

## 它回答的问题

`mw/tools.py` 首屏写着内核只做四件事：**预算、记忆、规划、校验**。
前三件都在，而且都有读数。第四件在此之前是**零实现** —— 全项目搜不到一个
校验入口。所以内核缺了一条腿，而所有读数都在为另外三条腿作证。

这一轮把它补上（`mw/verify.py` + `core._gate_finish`）。本探针回答的是：

  **这条腿是"装上了"还是"通电了"？**

## 判据从哪来（这是全模块最容易做歪的地方）

从**题面文本本身**派生，不碰尺子三样东西（参考解 / 断言集 / 分数）。
实测的代码题清一色写着"只输出代码，函数名必须是 `flatten`" ——
那是**出题人交给做题人的要求**，不是判分器事后才有的信息。

## 它**不**回答的问题（边界，必须一起念）

- 它**不**测分数改进。它测的是"闸会不会真的把一个坏答案换成一个能拿分的答案"，
  用的是**脚本化模型**（我们让它第二遍写对）。真模型会不会写对，是另一件事，
  见 `bench/evidence/verify_s21/` 里的真模型配对读数。
- "过自检 ≠ 答案对"是**设计意图**，不是缺陷。P8 专门证明这一点。

## 判读（事前登记，写在跑之前；阈值只在本文件声明一处）

  R1 判据不许比尺子严：**21 道题的参考解必须 21/21 全过**。
     任一不过 ⇒ 判据本身错（它在制造假失败），整组不认领。
  R2 闸必须**既拦又修**：先交坏答案必须被打回，改对之后必须 `done`
     且**外部打分器**从 0 变 1。只拦不修 ⇒ 记"只挡不修"，不得认领"校验→能力"。
  R3 上限与保命：打回有上限；超限/停滞的交付**不许是空的**（`_salvage`）。
  R4 关掉必须真的关掉：`verify_retries=0` ⇒ 0 条判据、0 次运行。
     否则对照组不存在，任何 Δ 都不可归因。
  R5 R1–R4 全过 ⇒ 判「校验这条腿通电（结构 + 一次端到端修复）」。
     ⛔ 不主张真模型分数，不主张"自检让系统变聪明"。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tempfile
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V1))

import mw.config as cfg                                            # noqa: E402
from mw import verify as vfy                                       # noqa: E402
from mw.bench import ExternalScorer, Suite                         # noqa: E402
from mw.core import Core                                           # noqa: E402

EVID = V1 / "bench" / "evidence" / "verify_s21"
SCORER = V1 / "bench" / "scorers" / "code_exec.py"
TASKS = V1 / "bench" / "tasks" / "struct_convert.jsonl"

#: ★ 事前登记的规则 —— **唯一一处声明**（脚本/CI 都不许再抄第二份）。
RULES: tuple[tuple[str, str], ...] = (
    ("R1", "参考解 21/21 全过 —— 任一不过 ⇒ 判据本身错（在造假失败），整组不认领"),
    ("R2", "先交坏答案必须被打回，改对后必须 done 且**外部打分器**从 0 变 1"),
    ("R3", "打回有上限；超限与停滞的交付**不许为空**（答案被保住）"),
    ("R4", "`verify_retries=0` ⇒ 0 条判据、0 次运行（对照组必须真实存在）"),
    ("R5", "R1–R4 全过 ⇒ 判「校验这条腿通电（结构 + 一次端到端修复）」"),
)

#: 结构上合法、语义上错误的一份答案（用来证明"过自检 ≠ 对"）。
PARTIAL_TASK = "z01_flatten"

#: `verify.py` 的**代码**（去注释与 docstring）里绝不许出现这三类字样。
FORBIDDEN = ("oracle", "tests", "score")


def print_rules() -> None:
    print("===================== 事前登记（不可事后修改）=====================")
    for tag, line in RULES:
        print(f"  {tag:3s} {line}")
    print("==================================================================")


# ---------------------------------------------------------------------------
def _code_only(src: str) -> str:
    """去掉注释与 docstring 后的源码。

    ⚠️ 必须去：`verify.py` 的 docstring 里**故意**写着"不许读参考解/断言集/分数"
    —— 直接搜字符串，这条禁令自己会把自己判红（AF9 已经栽过一次这个坑）。
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body[0].value.value = ""
                body[0].value.kind = ""
                body.pop(0)
    out = ast.unparse(tree)
    return re.sub(r"#[^\n]*", "", out)


class ScriptLLM:
    """脚本化模型：**第 n 次规划**给第 n 个答案。

    它的唯一职责是让内核走完"交付 → 被打回 → 重写"这条路，
    并把**每一次规划实际看到的提示词**记下来 —— 判定"打回带没带原文"只有这一份证据。
    """

    GOAL_TPL = ("写一个函数 {name}(x)，返回 x。只输出代码，函数名必须是 {name}。")

    def __init__(self, answers: list[str], *, name: str = "solve") -> None:
        self.answers = list(answers)
        self.name = name
        self.calls = 0
        self.seen: list[str] = []

    def complete(self, messages, **kw):                            # noqa: ANN001
        blob = "\n".join(m.content for m in messages)
        # ⛔ 锚**提示词模板那一句**，不用 `"预演" in blob`（真值会被 guidance 污染）。
        if "准备执行的动作：" in blob:
            return json.dumps({"expect_ok": True, "expected": "会成功",
                               "confidence": 0.9}, ensure_ascii=False)
        self.seen.append(blob)
        i = min(self.calls, len(self.answers) - 1)
        self.calls += 1
        return json.dumps({"candidates": [{
            "intent": f"第 {self.calls} 版答案",
            "tool": "finish",
            "args": {"answer": self.answers[i]},
            "rationale": "交付"}]}, ensure_ascii=False)


def _run(answers: list[str], *, retries: int | None = None,
         goal: str | None = None) -> tuple[Core, object, ScriptLLM, list[str]]:
    tmp = Path(tempfile.mkdtemp())
    lim = cfg.Limits(max_steps=10, max_llm_calls=30, max_candidates=2,
                     max_consecutive_failures=6, max_stall=6)
    if retries is not None:
        lim = cfg.Limits(max_steps=10, max_llm_calls=30, max_candidates=2,
                         max_consecutive_failures=6, max_stall=6,
                         verify_retries=retries)
    llm = ScriptLLM(answers)
    core = Core(settings=cfg.Settings(limits=lim), llm=llm,
                wire_external=False, memory_mode="raw")
    logs: list[str] = []
    core.log = logs.append
    out = core.run(goal or ScriptLLM.GOAL_TPL.format(name="solve"))
    return core, out, llm, logs


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="第二十一轮结论探针（零模型调用）")
    ap.add_argument("--write", action="store_true",
                    help="把判读结果写回证据目录（实现只有这一处）")
    ap.add_argument("--rules", action="store_true",
                    help="只打印事前登记的规则（脚本在开跑之前调它）")
    args = ap.parse_args(argv)
    if args.rules:
        print_rules()
        return 0

    ok = True

    def say(name: str, good: bool, why: str, detail: str = "") -> bool:
        nonlocal ok
        ok = ok and good
        print(f"  {'✅' if good else '❌'} {name}  {why}")
        if detail:
            print(f"       {detail}")
        return good

    # ── 一、判据派生：可证伪，且不许误伤散文 ------------------------------
    print("一、判据派生（纯函数，不碰模型、不碰尺子）")
    cases = [
        ("代码题", "写一个函数 flatten(d)。只输出代码，函数名必须是 flatten。"),
        ("散文题", "解释一下为什么 CSV 里的引号要双写。"),
        ("只点名不算代码题", "函数名必须是 foo，但我只想要一句话说明。"),
        ("英文代码题", "Write a function bar. Only output code."),
    ]
    derived = {n: [c.kind for c in vfy.derive(g)] for n, g in cases}
    p1 = (derived["代码题"] == ["nonempty", "defines", "fence_lang", "compiles"]
          and derived["散文题"] == ["nonempty"]
          and derived["只点名不算代码题"] == ["nonempty", "defines"]
          # ⚠️ 英文题只说 "only output code"，没说 python ⇒ 不派生 `fence_lang`
          #    （语言标签判据得有"这是 Python 题"的依据），但**仍要**能编译。
          and derived["英文代码题"] == ["nonempty", "compiles"])
    say("P1/派生可证伪", p1,
        "散文题**不许**派生 `compiles`（否则会把解释文本判死）；代码题四项齐全",
        " ".join(f"{n}={v}" for n, v in derived.items()))

    # ── 二、校准：不许比尺子更严（R1） ───────────────────────────────────
    print("\n二、校准（R1：自检不许制造假失败）")
    suite = Suite.load(TASKS)          # 第三/四节用这一个套件跑端到端
    # ⚠️ 校准必须覆盖**全部**代码题（21 道），不是只跑手上那一个套件：
    #    R1 写的是"参考解 21/21"，跑 5 道只证明 5 道 —— 那是把判据的条件偷偷放宽。
    all_tasks = []
    for f in sorted((V1 / "bench" / "tasks").glob("*.jsonl")):
        all_tasks.extend(Suite.load(f).tasks)
    rows = []
    for t in all_tasks:
        ch = vfy.derive(t.prompt)
        rows.append((t.id, vfy.run(t.oracle, ch).ok, vfy.run(t.null, ch).ok,
                     vfy.run(t.partial, ch).ok,
                     "" if vfy.run(t.oracle, ch).ok else vfy.run(t.oracle, ch).report))
    n_or = sum(1 for r in rows if r[1])
    n_nul = sum(1 for r in rows if not r[2])
    say("P2/参考解全过", n_or == len(rows),
        f"{n_or}/{len(rows)} —— 任一不过就说明判据在造**假失败**，比没有判据更坏",
        "；".join(f"{r[0]}:{r[4]}" for r in rows if not r[1]) or "无假失败")
    say("P3/空答案全红", n_nul == len(rows),
        f"{n_nul}/{len(rows)} 份空答案被判红（判据得有牙，否则它只是装饰）")

    # ── 三、闸：既拦又修（R2） ───────────────────────────────────────────
    print("\n三、端到端：闸真的拦、真的修")
    # ⚠️ 用**真任务**跑，并且用同一把外部尺子给两个版本打分。
    #    只报"我打回了/我改对了"是自证；"外部尺子从 0 变 1"才是位移。
    # ⚠️ 坏答案要选**结构坏、语义对**的那一版（删一个右括号）：`partial` 是语义错、
    #    结构对，它当不了"被打回"的样本 —— 拿它测闸只会测到闸没拦（那是设计，不是 bug）。
    t0 = next(t for t in suite.tasks if t.id == PARTIAL_TASK)
    good = t0.oracle
    bad = t0.oracle.replace('def flatten(d, prefix=""):', 'def flatten(d, prefix="":')
    core, out, llm, logs = _run([bad, good], goal=t0.prompt)
    vs = (out.stats or {}).get("verify") or {}
    p4 = vs.get("failed", 0) >= 1 and out.status == "done"
    say("P4/闸真拦", vs.get("failed", 0) >= 1,
        "第一次交**语法坏**的代码必须被打回（不是靠模型自觉）",
        f"runs={vs.get('runs')} passed={vs.get('passed')} failed={vs.get('failed')}")
    say("P5/真修好", p4,
        "打回后第二版交对 ⇒ 交付它，且状态是 done",
        f"status={out.status} answer={out.answer[:40]!r}")
    # 打回的错误必须**带得动动作**：行号/语言标签，而不是一句"自检未通过"。
    second = llm.seen[1] if len(llm.seen) > 1 else ""
    p6 = ("自检未通过" in second) and ("语言标签" in second or "编译失败" in second)
    say("P6/打回带原文", p6,
        "重新规划时看到的错误原文必须含**可据以改动作**的信息（标签/行号）",
        f"第二版规划器看到的报错片段={second[second.find('自检未通过'):][:90]!r}")
    # ★ 唯一有说服力的那句：**外部打分器**说它从 0 变成了 1。
    scorer = ExternalScorer([sys.executable, str(SCORER)], source=SCORER)
    task = t0.as_dict()
    s_bad = scorer.score(task, bad).score
    s_good = scorer.score(task, out.answer).score
    say("P7/分数真的有位移", s_bad == 0.0 and s_good > s_bad,
        "打回**不是**自娱自乐：同一把外部尺子，坏答案 0.0 → 交付的答案 > 0",
        f"{PARTIAL_TASK}: 坏={s_bad} → 交付={s_good}")

    # ── 四、上限与保命（R3） ─────────────────────────────────────────────
    print("\n四、上限与保命（R3：装置不许吃掉效应）")
    # ⚠️ 每次给**不同**的坏答案：完全相同会被"重复动作"闸先挡下，
    #    那时测到的是停滞闸，不是自检上限 —— 两个机制必须分别观测。
    bads = [f"```c\nint solve{i}(int x) {{ return x; }}\n```" for i in range(6)]
    core2, out2, llm2, _ = _run(bads)
    vs2 = (out2.stats or {}).get("verify") or {}
    cap = int(cfg.Limits().verify_retries)
    say("P8/上限有效", vs2.get("failed", 0) == cap + 1 and vs2.get("exhausted") == 1,
        f"一直交坏答案 ⇒ 打回 {cap} 次后放弃并**如实标状态**（不是无限重写）",
        f"failed={vs2.get('failed')} exhausted={vs2.get('exhausted')} "
        f"runs={vs2.get('runs')} status={out2.status}")
    say("P9/放弃也交付", out2.status == "done_unverified" and bool(out2.answer.strip()),
        "未通过自检的交付**不许**报 done，也**不许**是空的",
        f"status={out2.status} 答案 {len(out2.answer)} 字")
    # 同一份坏答案连交 ⇒ 先撞"重复动作"闸 ⇒ 走停滞出口 ⇒ 只有 salvage 能救回来。
    core3, out3, llm3, _ = _run([bad] * 5)
    vs3 = (out3.stats or {}).get("verify") or {}
    say("P10/停滞保命", vs3.get("failed", 0) >= 1 and bool(out3.answer.strip()),
        "重写失败走 stalled 出口时，答案必须被 `_salvage` 保住（否则能拿分的答案变 0 分）",
        f"status={out3.status} failed={vs3.get('failed')} "
        f"答案 {len(out3.answer)} 字 salvaged={vs3.get('salvaged')}")

    # ── 五、对照组必须真实存在（R4） ─────────────────────────────────────
    print("\n五、对照组（R4：关掉必须真的关掉）")
    core4, out4, _, _ = _run([bad, good], retries=0)
    vs4 = (out4.stats or {}).get("verify") or {}
    say("P11/关掉即无", vs4.get("checks") == [] and vs4.get("runs", 0) == 0,
        "`verify_retries=0` ⇒ 0 条判据、0 次运行（开/关可以是两个可比的臂）",
        f"checks={vs4.get('checks')} runs={vs4.get('runs')} status={out4.status}")

    # ── 六、它**不许**变成打分器 ─────────────────────────────────────────
    print("\n六、边界：过自检 ≠ 对")
    t = next(x for x in suite.tasks if x.id == PARTIAL_TASK)
    v_partial = vfy.run(t.partial, vfy.derive(t.prompt))
    s_partial = scorer.score(t.as_dict(), t.partial).score
    say("P12/不冒充分数", v_partial.ok and 0.0 < s_partial < 1.0,
        "结构全过、分数只有一半 ⇒ 自检只说结构，不产生分数（否则就是自评分）",
        f"{t.id}: 自检={'过' if v_partial.ok else '不过'} 外部分数={s_partial}")
    src = (V1 / "mw" / "verify.py").read_text(encoding="utf-8")
    code = _code_only(src)
    hit = [w for w in FORBIDDEN if re.search(rf"\b{w}\b", code)]
    say("P13/不越界（静态）", not hit,
        "`verify.py` 的**代码**里不许出现参考解/断言集/分数 —— 判据只认题面",
        f"命中的字样={hit or '无'}")
    # 与外部打分器**同构**：抽取口径不一致会让自检比尺子更严。
    from bench.scorers.code_exec import extract_code as ref_extract   # noqa: E402
    samples = [t.oracle, t.partial, f"```python\n{t.oracle}\n```", bad, good,
               "", "```\nprint(1)\n```", "解释：\n```py\nx=1\n```\n完"]
    same = all(vfy.extract_code(s) == ref_extract(s) for s in samples)
    say("P14/抽取同构", same,
        "自检抽代码的口径必须与打分器逐字节一致，否则自检比尺子严 ⇒ 假失败",
        f"{len(samples)} 个样本（含 ```c / 空 / 无围栏）逐字节比对")

    # ── 判读（唯一实现） ────────────────────────────────────────────────
    r1 = n_or == len(rows)
    r2 = vs.get("failed", 0) >= 1 and out.status == "done" and s_good > s_bad
    r3 = (vs2.get("exhausted") == 1 and bool(out2.answer.strip())
          and bool(out3.answer.strip()))
    r4 = vs4.get("checks") == [] and vs4.get("runs", 0) == 0
    if not r1:
        verdict, tag = "R1：判据在造**假失败**（参考解没全过）⇒ 整组不认领", "R1"
    elif not r2:
        verdict, tag = "R2：闸只拦不修（或不拦）⇒ 不得认领「校验→能力」", "R2"
    elif not r3:
        verdict, tag = "R3：上限/保命不成立 ⇒ 装置会吃掉效应，不认领", "R3"
    elif not r4:
        verdict, tag = "R4：关不掉 ⇒ 没有对照组，任何 Δ 都不可归因", "R4"
    else:
        verdict, tag = ("R5：校验这条腿通电（结构 + 一次端到端修复）—— "
                        "⛔ 不主张真模型分数"), "R5"

    out_json = {
        "rules": [list(r) for r in RULES],
        "derive": derived,
        "calibration": {"total": len(rows), "oracle_pass": n_or, "null_fail": n_nul,
                        "false_negatives": [r[0] for r in rows if not r[1]]},
        "e2e_reject_then_fix": {"verify": vs, "status": out.status,
                                "score_bad": s_bad, "score_delivered": s_good},
        "cap": {"verify": vs2, "status": out2.status},
        "stall_salvage": {"verify": vs3, "status": out3.status,
                          "answer_chars": len(out3.answer)},
        "control_off": {"verify": vs4, "status": out4.status},
        "not_a_scorer": {"selfcheck_ok": v_partial.ok, "external_score": s_partial},
        "verdict": verdict, "tag": tag,
        "claim": "结构：内核终于有一个可证伪的、确定性的交付前校验闸，"
                 "并且它能把一个结构坏的答案换成结构好的答案",
        "unknowns": [
            "真模型是否因此变好：见真模型配对读数（开/关自检），本探针不主张",
            "判据只覆盖题面**显式**写出的约束；没写出的错（逻辑错）一律放过",
            "只有 5 个代码套件 21 题是「题面点名函数名」的形态；散文题只有 nonempty 一条",
        ],
    }

    print("\n" + "=" * 74)
    print(f"  {tag}  ⇒ 判读：{verdict}")
    print("=" * 74)

    if args.write:
        EVID.mkdir(parents=True, exist_ok=True)
        vp = EVID / "verdict.json"
        vp.write_text(json.dumps(out_json, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  已固化到 {vp}")
        back = json.loads(vp.read_text(encoding="utf-8"))
        say("P15/固化自检", back.get("tag") == tag,
            "写完**读回来**核对（只相信再读一遍拿到的）",
            f"回读 tag={back.get('tag')!r}")
    elif (EVID / "verdict.json").exists():
        frozen = json.loads((EVID / "verdict.json").read_text(encoding="utf-8"))
        say("P15/重算一致", frozen.get("tag") == tag,
            "落盘判定 = 现在重算的判定（没人改过数据也没改过阈值）",
            f"落盘={frozen.get('tag')!r} 重算={tag!r}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
