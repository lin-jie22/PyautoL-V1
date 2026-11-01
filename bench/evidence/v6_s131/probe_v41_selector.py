#!/usr/bin/env python3
"""V41 探针：**候选之间到底有没有被比较过**（真 `Core`，零网络、零模型成本）。

## 为什么这一轮不跑批

V40 已经把「宽度进内核」证完了（`../v6_s130/p1_replay.txt`：三引号组 3/3、兄弟
`finish` 组 3/3）。现在要问的是**下一条边**：那 3 个候选进了 `core` 之后，
第 2/3 个是**怎么被读到**的。

这个问题**不需要模型**：它完全由 `core.py` 的执行器决定（`for cand in cands`
在什么条件下 `continue`、什么条件下 `break`/`return`）。拿模型去量它，只会
把"执行器语义"和"模型抖动"混成一个数（纪律 34）。

## 两个读数层（PREREG §一）

- **L1 静态**：`mw/core.py` 里 `cands` 的每一处读取点，逐条分类。
  ⛔ 读代码不是读数，但**证"通道不通"必须列出该通道的全部读取点**（纪律 24）。
- **L2 动态**：四个场景各跑一次真 `run()`，记录**按轮次分组、带内容摘要**的
  `dispatch` 序列。`dispatch` 是"这个候选真的被执行了"的唯一硬证据 ——
  日志措辞、`候选=N` 都可能只反映"数组长度"（V39 就是在这里栽的）。

## 装置的四条硬约束

1. **真 `Core`**：真 `Planner`、真 `Guard`、真 `WorldModel`、真 `verify.derive()`。
   ⛔ 不复刻执行器 —— 复刻出来的读数只证明探针自己自洽。
2. **桩只喂候选**：第 1 次 `complete()` 返回合法 JSON，其后返回 `""`。
   ⛔ 不测模型行为（V40 已证模型给得出 3 个），本轮只测**执行器怎么消费**。
3. **两处观测点都只读**：包装 `tools.dispatch` 与 `wm.predict`，不改任何分支。
   ⚠️ `predict` 比 `dispatch` **更细**：它在循环体内、任何闸之前 ⇒
   `predict` 次数能回答"第几个候选被走过了"，即使它随后被拦。
4. **题面是代码题**（`checks = ['非空','函数名 flatten','语言标签','可编译','结构检查']`）。
   为什么：V39/V40 那两份给出 3 个候选的原文**都是代码题**（`e1wi`/`e3ni`），
   而 `结构检查` 正是内核历史上真实打回 finish 的那条判据。
   ⚠️ 第一版用纯文字题面（只有 `非空`），结果 S3 的"空答案"在**规划阶段**
      就被 `propose()` 吃掉了（`blank` 过滤），根本到不了执行器 ⇒ 布景失效。
      改用代码题后，打回发生在**执行器**里（`_gate_finish`），这才是要测的位置。

⛔ 不写任何其他证据路径；读数走 stdout + 本文件同目录的 `p1_selector.txt`。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]                      # v6_s131 → evidence → bench → V1
sys.path.insert(0, str(ROOT))

#: ⚠️ **必须在 `import mw.config` 之前**：`settings()` 在 import 时求值
#:    （MEMORY 硬约束：`V1_LLM_TEMPERATURE` 那次就是这么漏的）。
#: S4 要吃 V39 的**原文**（三引号形态），只有 `salvage=3` 才解析得出 3 个候选。
#: S1~S3 的 JSON 本来就合法 ⇒ 设成 3 对它们**无影响**（不是变量）。
os.environ.setdefault("V1_PLAN_SALVAGE", "3")

from mw.core import Core                    # noqa: E402
from mw.memory import MemoryStore           # noqa: E402

CORE_SRC = (ROOT / "mw" / "core.py").read_text(encoding="utf-8")

#: 题面：> 120 字（`looks_complex` 的唯一入口）、多步、**代码语境**
#: ⇒ `verify.derive()` 产出 5 条判据，「打回」有一条真实的、与分数无关的确定性路径。
GOAL = (
    "写一个函数，函数名必须是 flatten，把嵌套列表压平，只输出代码。"
    "不允许使用递归，也不能引入任何第三方库，并且要处理空列表的情况，"
    "还要保证结果里保留原始顺序，最后不要写任何解释文字。"
)

#: 合法答案（过全部 5 条判据）—— 三个「不同实现」，模拟 V39 那三段 449/443/411。
BODY_A = (
    "```python\n"
    "def flatten(xs):\n"
    "    out = []\n"
    "    stack = list(xs)\n"
    "    while stack:\n"
    "        cur = stack.pop(0)\n"
    "        if isinstance(cur, list):\n"
    "            stack = list(cur) + stack\n"
    "        else:\n"
    "            out.append(cur)\n"
    "    return out\n"
    "```"
)
BODY_B = (
    "```python\n"
    "def flatten(xs):\n"
    "    out = []\n"
    "    for x in xs:\n"
    "        if isinstance(x, list):\n"
    "            out.extend(flatten(x))\n"
    "        else:\n"
    "            out.append(x)\n"
    "    return out\n"
    "```"
)
BODY_C = (
    "```python\n"
    "import itertools\n\n"
    "def flatten(xs):\n"
    "    out = []\n"
    "    for x in xs:\n"
    "        out = out + (flatten(x) if isinstance(x, list) else [x])\n"
    "    return out\n"
    "```"
)
#: 首候选**过不了**「函数名 flatten」这一条：它可编译、非空、标签对，只是名字不对。
BODY_BAD = "```python\ndef other(xs):\n    return xs\n```"


# ── 观测点：两处，都只读 ────────────────────────────────────────────────────
class RecReg:
    """包装 `tools.dispatch`，记录 `(轮次, 工具名, 载荷摘要)`。

    ⚠️ 载荷摘要**必须有**：只记工具名，`finish` 三次都是 `finish` ——
       那就退化成 V39 那个"数组长度当模型输出"的同构错误（纪律 27）。
    ⚠️ `__getattr__` 转发是必须的：`core` 会读 `spec.produces_answer` / `get()` /
       `names()`，漏一个就是 `AttributeError` —— 那测的是桩不是产物。
    """

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
        self.hits.append((int(self._ref[0].ledger.steps_used), str(name), body[:28]))
        return self._inner.dispatch(name, args)


class RecWM:
    """包装 `wm.predict`，只数调用次数（含随后被拦掉的那些）。"""

    def __init__(self, inner, ref):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_ref", ref)
        object.__setattr__(self, "hits", [])

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def predict(self, *, state, action, use_llm=True):
        self.hits.append((int(self._ref[0].ledger.steps_used), str(action)[:50]))
        return self._inner.predict(state=state, action=action, use_llm=use_llm)


class ScriptLLM:
    """第 1 次返回候选 JSON，其后返回 `""`。"""

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls: list = []

    def complete(self, messages, max_tokens=None):
        self.calls.append(max_tokens)
        return self.payload if len(self.calls) == 1 else ""


def load_task(tid: str) -> str:
    """从 `bench/specgen/tasks_v8.jsonl` 取**真实题面**（S4 用）。"""
    p = ROOT / "bench" / "specgen" / "tasks_v8.jsonl"
    for line in p.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("id") == tid:
            return str(d.get("prompt") or "")
    raise SystemExit(f"题集里没有 {tid}")


#: S4 的输入：V39 冻结的**规划器原文**（`n3_e1wi_c0`，三引号形态，3 个候选）。
#: ⚠️ 这是本项目第一次让"V40 修好的宽度"走**真题面 + 真执行器**：
#:    候选不是我编的，是模型当时真的给出的三段实现。
REAL_RAW = HERE.parent / "v6_s130" / "raw" / "n3_e1wi_c0.txt"


def _cands(*items) -> str:
    return json.dumps({"candidates": [
        {"intent": it, "tool": tool, "args": args} for it, tool, args in items
    ]}, ensure_ascii=False)


SCENES = {
    # S1：真实形态（V40 那两份原文的 tools 都是 finish×3），首候选**过闸**
    "S1 三finish·首候选过闸": (_cands(
        ("答案A", "finish", {"answer": BODY_A}),
        ("答案B", "finish", {"answer": BODY_B}),
        ("答案C", "finish", {"answer": BODY_C}),
    ), GOAL),
    # S2：首候选**必被跳过**（shell 未开启 ⇒ `check_shell` deny；也可能先被
    #     goal-hijack 拦。两者都是 `continue`，对本轮要问的问题等价）
    "S2 首候选被跳过": (_cands(
        ("跑个命令", "run_shell", {"cmd": "echo hi"}),
        ("答案A", "finish", {"answer": BODY_A}),
        ("答案B", "finish", {"answer": BODY_B}),
    ), GOAL),
    # S3：首候选**被执行且失败**（名字不对 ⇒ `_gate_finish` 打回 ⇒ result.ok=False）
    "S3 首候选失败": (_cands(
        ("答案X", "finish", {"answer": BODY_BAD}),
        ("答案A", "finish", {"answer": BODY_A}),
        ("答案B", "finish", {"answer": BODY_B}),
    ), GOAL),
    # S4：⭐ **真实形态**：V39 原文 + 真题面 + 真 salvage
    "S4 真原文·真题面": (REAL_RAW.read_text(encoding="utf-8"), load_task("e1wi")),
}


def run_scene(payload: str, goal: str = GOAL) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="v41_"))
    llm = ScriptLLM(payload)
    core = Core(llm=llm, memory=MemoryStore(tmp / "m.db"), memory_mode="raw")
    ref: list = [core]
    reg, wm = RecReg(core.tools, ref), RecWM(core.wm, ref)
    core.tools = reg
    core.wm = wm
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = core.run(goal, budget_factor=1)
    return {
        "status": out.status,
        "answer": out.answer or "",
        "hits": list(reg.hits),
        "preds": list(wm.hits),
        "log": buf.getvalue(),
        "checks": [c.name for c in getattr(core, "_checks", ())],
        "llm_calls": int(len(llm.calls)),
    }


# ── L1：静态枚举 `cands` 的全部读取点 ───────────────────────────────────────
#: 分类键：出现这些词 = 在**比较/选择**候选（那才是"选择器"）。
_CMP = re.compile(r"\bsort(ed)?\b|\bmax\(|\bmin\(|\bscore|choose|rank|比较|择优")


def l1_scan() -> list[tuple[int, str, str]]:
    rows = []
    for i, line in enumerate(CORE_SRC.split("\n"), 1):
        if not re.search(r"\bcands\b", line):
            continue
        s = line.strip()
        if _CMP.search(s):
            kind = "★比较/选择"
        elif s.startswith("for ") or "len(cands)" in s or "for c in cands" in s:
            kind = "消费(遍历/计数)"
        elif "=" in s and "cands" in s.split("=")[0]:
            kind = "赋值/拼装"
        else:
            kind = "其他"
        rows.append((i, kind, s))
    return rows


def main() -> None:
    print("=" * 100)
    print("L1 静态：`mw/core.py` 里 `cands` 的每一处读取点")
    print("=" * 100)
    rows = l1_scan()
    for i, kind, s in rows:
        print(f"  core.py:{i:<5} [{kind}] {s[:90]}")
    n_cmp = sum(1 for _, k, _ in rows if k.startswith("★"))
    print(f"  ── 合计 {len(rows)} 处；其中**比较/选择** {n_cmp} 处")

    print()
    print("=" * 100)
    print("L2 动态：每次真 run() 的 dispatch 序列（按轮次分组，带载荷摘要）")
    print("=" * 100)
    results = {}
    for name, (payload, goal) in SCENES.items():
        r = run_scene(payload, goal)
        results[name] = r
        by: dict[int, list[str]] = {}
        for step, tool, body in r["hits"]:
            by.setdefault(step, []).append(f"{tool}({body[:16]})")
        pby: dict[int, int] = {}
        for step, _ in r["preds"]:
            pby[step] = pby.get(step, 0) + 1
        print(f"\n■ {name}")
        print(f"  自检判据 = {r['checks']}")
        print(f"  状态     = {r['status']}  交付长度 = {len(r['answer'])}")
        print(f"  交付内容 = {r['answer'][:70]!r}")
        print(f"  dispatch = {by}")
        print(f"  predict  = {pby}（每轮走过几个候选）")
        print(f"  LLM 调用 = {r['llm_calls']} 次")

    print()
    print("=" * 100)
    print("D1~D5 判据")
    print("=" * 100)

    def first_round(r):
        return [t for s, t, _ in r["hits"] if s == 1]

    def who(ans: str) -> str:
        if ans == BODY_A:
            return "A(第2候选)"
        if ans == BODY_B:
            return "B(第3候选)"
        if ans == BODY_C:
            return "C"
        if ans == BODY_BAD:
            return "BAD(首候选)"
        return repr(ans[:30])

    d1 = first_round(results["S1 三finish·首候选过闸"])
    d2 = first_round(results["S2 首候选被跳过"])
    d3 = first_round(results["S3 首候选失败"])
    a1 = results["S1 三finish·首候选过闸"]["answer"]
    a2 = results["S2 首候选被跳过"]["answer"]
    a3 = results["S3 首候选失败"]["answer"]

    print(f"  D1 S1 第1轮 dispatch = {d1} ⇒ {'✅ 长度1' if len(d1) == 1 else '⛔'}"
          f" ｜交付={who(a1)}")
    print(f"  D2 S2 第1轮 dispatch = {d2} ⇒ 交付={who(a2)}"
          f"  {'✅ 让位到第2候选' if a2 == BODY_A else '⛔ 没让位'}")
    print(f"  D3 S3 第1轮 dispatch = {d3} ⇒ {'✅ 长度1（失败也 break）' if len(d3) == 1 else '⛔'}"
          f" ｜交付={who(a3)}  {'⛔ 第2候选被试了' if a3 in (BODY_A, BODY_B) else ''}")
    print(f"  D4 静态比较点 = {n_cmp} 处 ⇒ {'✅ 0' if n_cmp == 0 else '⛔ 有比较逻辑'}")
    diff = {n: (len(results[n]["preds"]) - len(results[n]["hits"])) for n in results}
    print(f"  D5 predict−dispatch 次数差 = {diff}")

    # ── S4：真实形态（V39 原文 + 真题面）─────────────────────────────────────
    r4 = results["S4 真原文·真题面"]
    by4: dict[int, list[str]] = {}
    for step, tool, body in r4["hits"]:
        by4.setdefault(step, []).append(f"{tool}({body[:14]})")
    print()
    print("=" * 100)
    print("S4 真实形态：V39 那份给出 3 个候选的原文，走真题面")
    print("=" * 100)
    print(f"  自检判据 = {r4['checks']}")
    print(f"  dispatch = {by4}")
    print(f"  交付长度 = {len(r4['answer'])}  状态 = {r4['status']}")
    print(f"  ⭐ 判读：第 1 轮只 dispatch 了 {len(by4.get(1, []))} 个候选；"
          f"共 {sum(len(v) for v in by4.values())} 次 dispatch、{len(by4)} 轮")


if __name__ == "__main__":
    main()
