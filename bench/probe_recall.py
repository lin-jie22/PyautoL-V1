#!/usr/bin/env python3
"""第二十轮结论探针：**被截掉的那部分，现在要得回来吗？**（零模型调用）

## 它回答的问题

`mw/core.py` 里曾经只有一行 `stored = full[:WORKING_CHAR_CAP]`：
工具返回 5000 字，进下一步上下文的只有前 300 字，**其余 4700 字当场消失**，
而且**没有任何路径能把它们找回来**（§17-9 实测覆盖率 0.06）。

第二十轮的修法**不是**把 300 调大（那会让一步吃掉整个上下文），而是：
  ① 原文整段进 `blobs` 表，工作记忆里留一个**可寻址的索引**；
  ② 只在**意外**发生时要回来（失败且报错被截断 / 世界模型失配）；
  ③ 要回来的正文**升级**工作记忆里的那一条 —— 不是另开一段，
     否则同一条观测会在上下文里出现两次（第一次实现就是这么写的，`AB7` 当场转红）。

## 它**不**回答的问题（边界，必须一起念）

- 它不测分数。这轮没有任何"模型因此答得更好"的主张 —— 前四轮的经验是：
  内容与形状的贡献都已被安慰剂臂测成 0，所以"多给点字"很可能也是 0。
  这里只主张一件结构性事实：**信息不再不可逆地消失**。
- 它不测真模型会不会用得上。触发是**内核规则**（不看模型输出），
  所以"会不会触发"是确定的；"触发了有没有用"仍然是 UNKNOWN。

## 判读（事前登记，写在跑之前；阈值只在本文件声明一处）

  R1  end-to-end 的 `expand.calls == 0`  ⇒ **机制未通电**，整组不认领
  R2  `expand` 回来的字节与原文不等          ⇒ **可回溯是假的**，整组不认领
  R3  回看正文在模型看到的 prompt 里被二次截断 ⇒ **取回了但没进上下文**，不认领
  三条全过 ⇒ 判「可回溯性成立（结构）」，且**只**主张结构。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V1))

import mw.config as cfg                                            # noqa: E402
from mw.core import (EXPAND_CHAR_CAP, EXPAND_MARK, EXPAND_MAX_PER_TASK,  # noqa: E402
                     WORKING_CHAR_CAP, Core, recall_trigger)
from mw.memory import MemoryStore                                  # noqa: E402
from mw.tools import ToolRegistry, ToolSpec                        # noqa: E402

EVID = V1 / "bench" / "evidence" / "recall_s20"

#: ★ 事前登记的规则 —— **唯一一处声明**（脚本/CI 都不再抄第二份）。
RULES: tuple[tuple[str, str], ...] = (
    ("R1", "end-to-end 的 expand.calls == 0  ⇒ 机制未通电，整组不认领"),
    ("R2", "expand 回来的字节与原文不等       ⇒ 可回溯是假的，整组不认领"),
    ("R3", "回看正文在 prompt 里被二次截断     ⇒ 取回了但没进上下文，不认领"),
    ("R4", "R1/R2/R3 全过 ⇒ 判「可回溯性成立（结构）」—— ⛔ 只主张结构，不主张分数"),
)

#: 探针用的长观测：5000 字，**决定性信息在最后一行**（模拟真实报错）。
LONG_TAIL = "最终失败原因：KeyError 'z02'（缺列 to_csv）"
LONG_BODY = "traceback...\n" + ("x" * 4900) + "\n" + LONG_TAIL


def print_rules() -> None:
    print("===================== 事前登记（不可事后修改）=====================")
    for tag, line in RULES:
        print(f"  {tag:3s} {line}")
    print("==================================================================")


# ---------------------------------------------------------------------------
class ScriptLLM:
    """脚本化模型。**唯一的职责**是让内核走完那几步，并在边上把 prompt 记下来。

    ⚠️ 它记下来的 `seen` 是**判定 R3 的唯一证据**：
      "拼进了某个字符串"不等于"模型看见了"。只有真正出现在 prompt 里才算。
    """

    GOAL = "分析并修好这个转换脚本，然后总结根因"

    def __init__(self, *, fail_rounds: int, tool: str = "bigfail") -> None:
        self.fail_rounds = fail_rounds
        self.tool = tool
        self.seen: list[str] = []
        self.planner_calls = 0

    def complete(self, messages, **kw):                            # noqa: ANN001
        blob = "\n".join(m.content for m in messages)
        # ⛔ 判别预演必须锚在**提示词模板的那一句**上，不能用 `"预演" in blob`：
        #    规划器的提示词里带着 `guidance`（世界模型沉淀下来的易错点），
        #    而那些句子本身就在讲"预演"（`DemoLLM` 的注释里也踩过同一个坑）。
        #    实测：第一次跑就因为这一条，规划被误判成预演、返回了 `expect_ok`，
        #    于是 `empty_plan +1`，整轮降级 —— 读数看起来像"内核坏了"。
        if "准备执行的动作：" in blob:
            # 让预演一律说"会成功"：这样失败那一步**必然**产生失配（另一条触发）。
            return json.dumps({"expect_ok": True, "expected": "会成功",
                               "confidence": 0.9}, ensure_ascii=False)
        self.seen.append(blob)
        self.planner_calls += 1
        if self.planner_calls <= self.fail_rounds:
            return json.dumps({"candidates": [{
                "intent": f"运行第 {self.planner_calls} 次转换",
                "tool": self.tool,
                "args": {"i": self.planner_calls},       # 每轮不同 ⇒ 不触发"重复"跳过
                "rationale": "先跑一遍看报错"}]}, ensure_ascii=False)
        return json.dumps({"candidates": [{
            "intent": "交付结论", "tool": "finish",
            "args": {"answer": "已根据报错尾部的 KeyError 定位到缺列"},
            "rationale": "收尾"}]}, ensure_ascii=False)


def _run(*, fail_rounds: int, memory_mode: str = "raw",
         dbname: str = "recall.db") -> tuple[Core, object, ScriptLLM]:
    tmp = Path(tempfile.mkdtemp())
    mem = MemoryStore(tmp / dbname)
    reg = ToolRegistry()

    def _bigfail(args: dict) -> str:
        # 抛异常 ⇒ `dispatch` 变成 `ToolResult(ok=False, error=<原文>)`。
        # 这正是真实工具失败的形态，也是 T1 触发的条件。
        raise RuntimeError(LONG_BODY)

    reg.register(ToolSpec(name="bigfail", description="故意失败的长报错",
                          params={"i": "轮次"}, handler=_bigfail))
    llm = ScriptLLM(fail_rounds=fail_rounds)
    core = Core(settings=cfg.Settings(limits=cfg.Limits(
        max_steps=14, max_llm_calls=40, max_candidates=2,
        max_consecutive_failures=10, max_stall=10)),
        llm=llm, memory=mem, tools=reg, wire_external=False,
        memory_mode=memory_mode)
    core.log = lambda m: None
    out = core.run(ScriptLLM.GOAL)
    return core, out, llm


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="第二十轮结论探针（零模型调用）")
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

    print("一、触发条件（纯函数，不碰模型）")
    cases = [
        ("长报错", dict(ok=False, error="x" * (WORKING_CHAR_CAP + 1), mismatched=False), "tail"),
        ("短报错", dict(ok=False, error="短", mismatched=False), ""),
        ("失配", dict(ok=True, error="", mismatched=True), "whole"),
        ("平静", dict(ok=True, error="", mismatched=False), ""),
        ("长报错+失配（两者都成立时先取尾部）",
         dict(ok=False, error="x" * 999, mismatched=True), "tail"),
    ]
    got = [(n, recall_trigger(**kw)) for n, kw, _ in cases]
    say("P1/触发可证伪", all(g == want for (_, g), (_, _, want) in zip(got, cases)),
        "4 个不动的情形 + 1 个两者都成立的情形，逐条对号",
        " ".join(f"{n}→{g or '不回看'}" for n, g in got))

    print("\n二、可回溯（磁盘层，逐字节）")
    tmp = Path(tempfile.mkdtemp())
    mem = MemoryStore(tmp / "b.db")
    h = mem.capture(LONG_BODY)
    back = mem.expand(h.ref, cap=10 ** 6)[0]
    say("P2/逐字节相同", back == LONG_BODY and h.chars == len(LONG_BODY),
        f"{h.chars} 字进，{len(back)} 字出，sha={h.sha}",
        f"尾部={'一致' if back.endswith(LONG_TAIL) else '不一致'}")
    tail, cut = mem.expand(h.ref, cap=EXPAND_CHAR_CAP, where="tail")
    say("P3/尾部窗口", tail == LONG_BODY[-EXPAND_CHAR_CAP:] and cut,
        f"窗={EXPAND_CHAR_CAP} 截断标记={cut}",
        f"决定性信息在窗内={'是' if LONG_TAIL in tail else '否'}")

    print("\n三、端到端（内核真的走这条路）")
    db = tmp / "e2e.db"
    core, out, llm = _run(fail_rounds=1, dbname="e2e.db")
    ex = (out.stats or {}).get("expand") or {}
    say("P4/机制通电", ex.get("calls", 0) >= 1,
        "一次长报错失败 ⇒ 必须取回一次",
        f"calls={ex.get('calls')} 因由={ex.get('by_trigger')} "
        f"跳过={ex.get('skipped')} 错误={ex.get('errors')}")
    prompt = "\n".join(llm.seen)
    say("P5/真的进了 prompt", EXPAND_MARK in prompt and LONG_TAIL in prompt,
        "判定用**模型看到的 prompt**，不是拼好的字符串",
        f"prompt 里出现回看标记={EXPAND_MARK in prompt} "
        f"尾部原文={LONG_TAIL in prompt}")
    idx = prompt.find(EXPAND_MARK)
    blk = prompt[idx:idx + EXPAND_CHAR_CAP + 200] if idx >= 0 else ""
    say("P6/没有二次截断", bool(blk) and LONG_TAIL in blk,
        "取回的正文不许在渲染阶段被再剪一次",
        f"回看块前缀={blk[:60]!r}")

    print("\n四、上限与只读")
    core5, out5, _ = _run(fail_rounds=6, dbname="cap.db")
    ex5 = (out5.stats or {}).get("expand") or {}
    say("P7/有上限", ex5.get("calls", 99) <= EXPAND_MAX_PER_TASK
        and (ex5.get("skipped") or {}).get("budget", 0) > 0,
        f"6 次连续失败也只能取回 {EXPAND_MAX_PER_TASK} 次，其余计入 skipped.budget",
        f"calls={ex5.get('calls')} skipped={ex5.get('skipped')}")
    # ⚠️ 这里原先是 `db = tmp / "e2e.db"`，而 `_run()` 自己新建了一个临时目录 ⇒
    #    md5 读的是一个从不存在的文件（探针自己抛 FileNotFound，不是被测对象的问题）。
    #    ⇒ 一律用**内核实际打开的那个库**（`core.memory.path`），不另拼路径。
    db = core.memory.path
    before = _md5(db)
    refs = [w.split("]", 1)[0].lstrip(EXPAND_MARK).split(" ")[0]
            for w in core.memory.working_view() if w.startswith(EXPAND_MARK)]
    for r in refs:
        core.memory.expand(r, cap=EXPAND_CHAR_CAP, where="whole")
        core.memory.expand(r, cap=EXPAND_CHAR_CAP, where="head")
    after = _md5(db)
    say("P8/取回是只读", bool(refs) and before == after,
        "expand 不许写库（md5 前后一致）；且必须真的取回过（否则这条断言恒真）",
        f"取回句柄={refs} md5 {before} / {after}")

    print("\n五、抽象臂必须读不到原文")
    coreA, outA, _ = _run(fail_rounds=1, memory_mode="abstract", dbname="abs.db")
    exA = (outA.stats or {}).get("expand") or {}
    say("P9/抽象臂干净", exA.get("calls", 1) == 0
        and (exA.get("skipped") or {}).get("mode", 0) >= 1,
        "abstract 臂允许回看原文，它就不再是对照臂（§16.15）",
        f"calls={exA.get('calls')} skipped={exA.get('skipped')}")

    # ── 判读（唯一实现） ────────────────────────────────────────────────
    r1 = ex.get("calls", 0) >= 1
    r2 = back == LONG_BODY
    r3 = bool(blk) and LONG_TAIL in blk
    if not r1:
        verdict, tag = "R1：机制未通电 ⇒ 整组不认领", "R1"
    elif not r2:
        verdict, tag = "R2：取回的字节与原文不等 ⇒ 可回溯是假的", "R2"
    elif not r3:
        verdict, tag = "R3：回看正文在 prompt 里被二次截断 ⇒ 不认领", "R3"
    else:
        verdict, tag = ("R4：可回溯性成立（结构）—— ⛔ 只主张结构，"
                        "不主张分数，也不主张真模型用得上"), "R4"

    out_json = {
        "rules": [list(r) for r in RULES],
        "trigger_cases": {n: g for n, g in got},
        "capture": {"chars": h.chars, "sha": h.sha, "byte_equal": r2},
        "e2e": ex, "e2e_cap": ex5, "e2e_abstract": exA,
        "prompt_has_mark": EXPAND_MARK in prompt,
        "prompt_has_tail": LONG_TAIL in prompt,
        "db_readonly": before == after,
        "verdict": verdict, "tag": tag,
        "claim": "结构：被截掉的观测不再不可逆地消失（可寻址 + 触发式取回 + 有上限）",
        "unknowns": [
            "真模型是否用得上取回的原文：UNKNOWN（前四轮实测：语义与形状的贡献都是 0）",
            "窗口选择（报错取尾 / 失配取头尾各半）是启发式，未做对照",
            "只测了 5000 字量级；更大观测下 cap 的取舍未测",
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
        back_json = json.loads(vp.read_text(encoding="utf-8"))
        say("P10/固化自检", back_json.get("tag") == tag,
            "写完**读回来**核对（只相信再读一遍拿到的）",
            f"回读 tag={back_json.get('tag')!r}")
    elif (EVID / "verdict.json").exists():
        frozen = json.loads((EVID / "verdict.json").read_text(encoding="utf-8"))
        say("P10/重算一致", frozen.get("tag") == tag,
            "落盘判定 = 现在重算的判定（没人改过数据也没改过阈值）",
            f"落盘={frozen.get('tag')!r} 重算={tag!r}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
