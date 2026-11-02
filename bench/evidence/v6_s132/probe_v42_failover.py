#!/usr/bin/env python3
"""V42 探针：**交付候选被打回时，会不会换下一个**（真 `Core`，零网络、零模型）。

## 它测的是哪一条边

V41 §107 已量到：`for cand in cands` 是**短路求值** —— 被*跳过*会让位（S2），
被*执行*（成功 `return` / 失败 `break`）都不让位。本轮的**唯一改动**是把
`if not result.ok:` 里的 `break` 换成「还有候选 ⇒ `continue`」，
开关 `Limits.cand_failover`（env `V1_CAND_FAILOVER`，默认 0）。

## 两臂怎么来的（⛔ 这一条错了整轮就白跑）

用 `Core(settings=...)` **注入**两臂，**不用 env**：同一进程里要跑两臂，
而 `cfg.settings()` 在 import 时求值（MEMORY 硬约束，`V1_LLM_TEMPERATURE` 那次）
⇒ 用 env 会让两臂读到同一个值，而读数会显示"开关无效"。

## 三条装置约束（与 V41 同源）

1. **真 `Core`**：真 `Planner` / `Guard` / `WorldModel` / `verify.derive()`。
   ⛔ 不复刻执行器 —— 复刻出来的读数只证明探针自己自洽。
2. **桩只喂候选**：第 1 次 `complete()` 返回 JSON，其后返回 `""`。
   ⛔ 不测模型行为（V40 已证模型给得出 3 个），只测**执行器怎么消费**。
3. **观测点只读**：包装 `tools.dispatch`，记 `(轮次, 工具, 载荷摘要)`。
   ⚠️ 载荷摘要必须有：`finish` 三次都是 `finish`，只记工具名就退化成
   V39 那个"数组长度当模型输出"的同构错误。

⚠️ "坏答案"必须是**过得了规划器、过不了自检**的形态（可编译 + 函数名不对）。
空答案会在 `propose()` 的 `blank` 过滤里被吃掉，根本到不了执行器（§107.7 第 1 条）。
"""
from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import re
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]                      # v6_s132 → evidence → bench → V1
sys.path.insert(0, str(ROOT))

from mw import config as cfg                # noqa: E402
from mw.core import Core                    # noqa: E402
from mw.memory import MemoryStore           # noqa: E402

GOAL = (
    "写一个函数，函数名必须是 flatten，把嵌套列表压平，只输出代码。"
    "不允许使用递归，也不能引入任何第三方库，并且要处理空列表的情况，"
    "还要保证结果里保留原始顺序，最后不要写任何解释文字。"
)
BODY_A = ("```python\ndef flatten(xs):\n    out = []\n    stack = list(xs)\n"
          "    while stack:\n        cur = stack.pop(0)\n"
          "        if isinstance(cur, list):\n            stack = list(cur) + stack\n"
          "        else:\n            out.append(cur)\n    return out\n```")
BODY_B = ("```python\ndef flatten(xs):\n    out = []\n    for x in xs:\n"
          "        if isinstance(x, list):\n            out.extend(flatten(x))\n"
          "        else:\n            out.append(x)\n    return out\n```")
BODY_C = ("```python\ndef flatten(xs):\n    out = []\n    for x in xs:\n"
          "        out = out + (flatten(x) if isinstance(x, list) else [x])\n"
          "    return out\n```")
#: 过得了规划器（非空、可编译、语言标签对），过不了自检（函数名不是 flatten）
BAD1 = "```python\ndef other(xs):\n    return xs\n```"
BAD2 = "```python\ndef other2(xs):\n    return list(xs)\n```"
BAD3 = "```python\ndef other3(xs):\n    out = []\n    return out\n```"

REAL_RAW = HERE.parent / "v6_s130" / "raw" / "n3_e1wi_c0.txt"


class RecReg:
    """只读观测点：包 `tools.dispatch`，记 `(轮次, 工具, 载荷摘要)`。"""

    def __init__(self, inner, ref):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_ref", ref)
        object.__setattr__(self, "hits", [])

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def dispatch(self, name, args):
        try:
            body = str((args or {}).get("answer", "")) or str((args or {}).get("cmd", ""))
        except Exception:                                   # noqa: BLE001
            body = ""
        body = re.sub(r"\s+", " ", body).strip()
        self.hits.append((int(self._ref[0].ledger.steps_used), str(name), body[:26]))
        return self._inner.dispatch(name, args)


class ScriptLLM:
    """第 1 次返回候选 JSON，其后返回 `""`（⊆ 不测模型）。"""

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls: list = []

    def complete(self, messages, max_tokens=None):
        self.calls.append(max_tokens)
        return self.payload if len(self.calls) == 1 else ""


def load_task(tid: str) -> str:
    p = ROOT / "bench" / "specgen" / "tasks_v8.jsonl"
    for line in p.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("id") == tid:
            return str(d.get("prompt") or "")
    raise SystemExit(f"题集里没有 {tid}")


def _cands(*items) -> str:
    return json.dumps({"candidates": [
        {"intent": it, "tool": tool, "args": args} for it, tool, args in items
    ]}, ensure_ascii=False)


SCENES: list[tuple[str, str, str]] = [
    ("S1 三finish·首候选过闸", _cands(
        ("答案A", "finish", {"answer": BODY_A}),
        ("答案B", "finish", {"answer": BODY_B}),
        ("答案C", "finish", {"answer": BODY_C})), GOAL),
    ("S2 首候选被跳过", _cands(
        ("跑个命令", "run_shell", {"cmd": "echo hi"}),
        ("答案A", "finish", {"answer": BODY_A}),
        ("答案B", "finish", {"answer": BODY_B})), GOAL),
    ("S3 首候选被打回", _cands(
        ("答案X", "finish", {"answer": BAD1}),
        ("答案A", "finish", {"answer": BODY_A}),
        ("答案B", "finish", {"answer": BODY_B})), GOAL),
    ("S5 三候选全坏", _cands(
        ("答案X", "finish", {"answer": BAD1}),
        ("答案Y", "finish", {"answer": BAD2}),
        ("答案Z", "finish", {"answer": BAD3})), GOAL),
    ("S6 单候选（H0 等价臂）", _cands(
        ("答案X", "finish", {"answer": BAD1})), GOAL),
    ("S7 前两坏·第三个好", _cands(
        ("答案X", "finish", {"answer": BAD1}),
        ("答案Y", "finish", {"answer": BAD2}),
        ("答案B", "finish", {"answer": BODY_B})), GOAL),
    ("S4 真原文·真题面", REAL_RAW.read_text(encoding="utf-8"), load_task("e1wi")),
]

WHO = {BODY_A: "A", BODY_B: "B", BODY_C: "C",
       BAD1: "BAD1", BAD2: "BAD2", BAD3: "BAD3", "": "（空）"}


#: S4 必须 `plan_salvage=3` 才解析得出 3 个候选（V40 §106.1）。
#: ⚠️ 只在 S4 上开它：其余场景的载荷本来就是合法 JSON ⇒ 开了也是零作用，
#:   但"零作用"与"没开"在口径上必须分得开 ⇒ 其余场景**保持 0**。
SALVAGE = {"S4 真原文·真题面": 3}


def arm_settings(flag: int, salvage: int = 0):
    s = cfg.settings()
    return dataclasses.replace(
        s, limits=dataclasses.replace(s.limits, cand_failover=int(flag),
                                      plan_salvage=int(salvage)))


def run_scene(payload: str, goal: str, flag: int, salvage: int = 0) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix=f"v42_{flag}_"))
    llm = ScriptLLM(payload)
    core = Core(settings=arm_settings(flag, salvage), llm=llm,
                memory=MemoryStore(tmp / "m.db"), memory_mode="raw")
    ref: list = [core]
    reg = RecReg(core.tools, ref)
    core.tools = reg
    buf = io.StringIO()
    t0 = time.time()
    with contextlib.redirect_stdout(buf):
        out = core.run(goal, budget_factor=1)
    dt = time.time() - t0
    return {
        "status": out.status,
        "answer": out.answer or "",
        "hits": list(reg.hits),
        "verify": dict(out.stats.get("verify") or {}),
        "failover": dict(out.stats.get("failover") or {}),
        "llm_calls": len(llm.calls),
        "wall": dt,
        "log": buf.getvalue(),
    }


def by_round(hits: list) -> dict:
    d: dict[int, list] = {}
    for step, tool, body in hits:
        d.setdefault(step, []).append(f"{tool}({body[:12]})")
    return d


def main() -> None:
    print("=" * 104)
    print("V42 探针：交付候选被打回 ⇒ 换下一个？  两臂 = Limits.cand_failover ∈ {0,1}")
    print("=" * 104)
    s0 = cfg.settings()
    print(f"  装置：verify_retries={s0.limits.verify_retries} "
          f"max_consecutive_failures={s0.limits.max_consecutive_failures} "
          f"max_candidates={s0.limits.max_candidates} "
          f"max_steps={s0.limits.max_steps}")
    print(f"  两臂：off={arm_settings(0).limits.cand_failover} "
          f"on={arm_settings(1).limits.cand_failover}"
          "（`Core(settings=…)` 注入，⛔ 不走 env）")

    rows = {}
    for name, payload, goal in SCENES:
        rows[name] = {}
        for flag in (0, 1):
            rows[name][flag] = run_scene(payload, goal, flag,
                                         SALVAGE.get(name, 0))

    for name, _, _ in SCENES:
        print()
        print("■ " + name)
        for flag in (0, 1):
            r = rows[name][flag]
            br = by_round(r["hits"])
            v = r["verify"]
            f = r["failover"]
            print(f"  [{'on ' if flag else 'off'}] 状态={r['status']:<17}"
                  f" 交付={len(r['answer']):>4}字 是谁={WHO.get(r['answer'], '真原文/其他'):<8}"
                  # ⛔ 不印墙钟：桩 LLM 的耗时是噪声（两次跑出 0.08/0.09），
                  #    而"代价"在这一层是**自检次数 + dispatch 次数**，不是秒。
                  f" LLM={r['llm_calls']}")
            print(f"        dispatch={br}")
            print(f"        自检 runs={v.get('runs')} passed={v.get('passed')} "
                  f"failed={v.get('failed')} exhausted={v.get('exhausted')}"
                  f" ｜让位 用={f.get('used')} 救回={f.get('rescued')}"
                  f" 额外失败={f.get('cf_extra')} 无弹药={f.get('no_more')}")

    print()
    print("=" * 104)
    print("D1~D5 判据")
    print("=" * 104)

    def r1(name, flag):
        return by_round(rows[name][flag]["hits"]).get(1, [])

    # D1 装置没变：off 臂的 dispatch 序列必须与 V41 `p1_selector.txt` 同形
    #    （V41 的读数：S1 第1轮只 dispatch 第1个 / S2 让位到第2候选 /
    #      S3 只 dispatch 第1个）。⛔ 这里比的是**形态**，不是字节 ——
    #    载荷摘要长度 26 是本探针的格式，V41 是 28。
    print(f"  D1 off 臂复现 V41：S1 第1轮={r1('S1 三finish·首候选过闸', 0)}")
    print(f"                    S2 第1轮={r1('S2 首候选被跳过', 0)}"
          f" 交付={WHO.get(rows['S2 首候选被跳过'][0]['answer'])}")
    print(f"                    S3 第1轮={r1('S3 首候选被打回', 0)}"
          f" 交付={WHO.get(rows['S3 首候选被打回'][0]['answer'])}")
    print(f"     ⇒ off 臂 S3 只 dispatch 1 次、无交付 "
          f"{'✅' if len(r1('S3 首候选被打回', 0)) == 1 else '⛔'}")

    # D2 主判据：机制通电
    s3 = rows["S3 首候选被打回"][1]
    ok2 = (len(r1("S3 首候选被打回", 1)) == 2
           and s3["answer"] == BODY_A
           and s3["failover"].get("used") == 1
           and s3["failover"].get("rescued") == 1)
    print(f"  D2 on 臂 S3：第1轮={r1('S3 首候选被打回', 1)} 交付={WHO.get(s3['answer'])}"
          f" 让位={s3['failover'].get('used')} 救回={s3['failover'].get('rescued')}"
          f"  ⇒ {'✅ 通电' if ok2 else '⛔ 没通电'}")

    # D3 代价面
    s5 = rows["S5 三候选全坏"][1]
    s5off = rows["S5 三候选全坏"][0]
    print(f"  D3 代价面 S5：off 状态={s5off['status']} 第1轮={r1('S5 三候选全坏', 0)}"
          f" ｜on 状态={s5['status']} 第1轮={r1('S5 三候选全坏', 1)}")
    print(f"      on 自检 failed={s5['verify'].get('failed')} "
          f"exhausted={s5['verify'].get('exhausted')}"
          f" 额外失败={s5['failover'].get('cf_extra')}"
          f" ⇒ {'⚠️ 熔断' if s5['status'] == 'cascade' else '（未熔断，见 RESULTS）'}")

    # D4 H0 等价：单候选时两臂逐字节相同
    a, b = rows["S6 单候选（H0 等价臂）"][0], rows["S6 单候选（H0 等价臂）"][1]
    same = (a["status"] == b["status"] and a["answer"] == b["answer"]
            and by_round(a["hits"]) == by_round(b["hits"]))
    print(f"  D4 S6 单候选两臂：off={a['status']}/{len(a['answer'])}字 "
          f"on={b['status']}/{len(b['answer'])}字 ⇒ {'✅ 逐字节相同' if same else '⛔ 泄漏'}")

    # D5 走多步
    s7 = rows["S7 前两坏·第三个好"][1]
    print(f"  D5 S7 on 臂：第1轮={r1('S7 前两坏·第三个好', 1)}"
          f" 交付={WHO.get(s7['answer'])} 让位={s7['failover'].get('used')}"
          f" 救回={s7['failover'].get('rescued')}"
          f" ⇒ {'✅ 走到第3个' if len(r1('S7 前两坏·第三个好', 1)) == 3 else '⛔'}")

    # S4 真原文（不在 D1~D5 里，单独报：它是唯一"候选不是编的"那一条）
    print()
    print("=" * 104)
    print("S4 真原文（V39 的 449/443/411，走 e1wi 真题面）")
    print("=" * 104)
    for flag in (0, 1):
        r = rows["S4 真原文·真题面"][flag]
        print(f"  [{'on ' if flag else 'off'}] 状态={r['status']} 交付={len(r['answer'])}字"
              f" 让位={r['failover'].get('used')} 救回={r['failover'].get('rescued')}"
              f" 自检 passed={r['verify'].get('passed')} failed={r['verify'].get('failed')}")
        print(f"        dispatch={by_round(r['hits'])}")


if __name__ == "__main__":
    main()
