#!/usr/bin/env python3
"""V36 收尾：那 2220 字节是谁加的（零模型、零网络、只读冻结产物）

## 要回答的问题

V36 的 D1 给出一个**自相矛盾**的读数：
  · 21 个 (task,it) 的 state **逐字节相同**（cum_r2 vs rst_r2）；
  · 同样 34 条请求里却有 5 条 `req_sha` 不同，累计差 −2220 字节。

state 相同而请求不同 ⇒ 差额在 state 之外。而 `_state()` 是**唯一**被 dump 的东西
（`--state-dump` 只落它）⇒ 只要差额落在**另一条渲染路径**上，D1 结构上就看不见。
本轮就是去找那条路径。

## 结论（先给答案，后附证据）

那 5 条差异**全部是世界模型的预演调用**（`WorldModel.predict`，`max_tokens=200`），
而不是规划调用（1024）。差额精确等于它 `known` 段的 JSON 转义字节：

    known = "已知易错点：\\n" + "- " + 事实A + "\\n" + "- " + 事实B
            36 + 2 + 2 + 228 + 2 + 2 + 216 = 488      ← 两条事实都在
            36 + 2 + 2 + 228            = 268         ← 只有 A

而两臂的世界模型事实恰好是：累积臂 2 条、重置臂 1 条（且重置臂那一条在
该请求发生的时刻还没被学到）。**268 与 488 都被逐字节解释干净。**

## ⛔ 本探针推翻了 V36 自己的一条结论

RESULTS.md 原话是「累积的记忆一个字节都没进 prompt」。**这句话是错的。**
正确表述：**累积经 `recall(动作)` → 世界模型事实 → 预演 prompt 进去了
268~488 字节，但它一个决策也没有改变**（96 格分数一格未动、21 个 state 逐字节相同）。
原因见 §E：`pred` 在 `core.py` 里**只被 `observe()` 消费**，从不参与选动作。

## 用法

    python3 bench/evidence/v6_s126/probe_v36_tail.py > bench/evidence/v6_s126/p4_tail.txt

⚠️ 上面这条命令会**覆盖已冻结的证据**。纪律 34：重跑必须是有意为之 ——
先 `cp p4_tail.txt /tmp/p4_tail.old`，再跑，再 `diff`。脚本本身不写任何文件。

⛔ 只读 `/tmp/v36` 的冻结产物；不跑模型。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mw import planner as P            # noqa: E402
from mw import tools as T              # noqa: E402

RUN = Path("/tmp/v36")
#: 四轮按**时间**顺序（不是文件名字典序）：cum_r1 → rst_r1 → rst_r2 → cum_r2
ORDER = ["cum_r1", "rst_r1", "rst_r2", "cum_r2"]

#: 一轮里 state 块的开头（用来把 state 反解成 goal + 各块）
_BLOCK = re.compile(r"\n(?=\[(?:工作记忆|语义记忆|经验|外部评审|轨迹|第 [^\]\n]{1,12}步\]))")

#: `WorldModel.predict` 的 max_tokens（`worldmodel.py:398` 一带写死 200）；
#: 规划器与 answer 用 `limits.llm_max_tokens`（本轮 1024，见 run_v36.sh）。
PREDICT_TOKENS = 200

_CATALOG: str | None = None


def catalog() -> str:
    """与 `Core` **同源**的工具目录。

    ⛔ 第一版这里只调了 `register_core_tools(reg)`，漏了 `memory` 与 `allow_note`
    ⇒ 目录少了 `note` / `recall` 两行，base 比真实请求少 82 个**字符**
    ⇒ 每条请求都被算出一个假的 `+214`。这正是纪律 12：
    「取证方式先去装置里确认它存在」。Core 侧见 `core.py:566` 一带。
    """
    global _CATALOG
    if _CATALOG is None:
        reg = T.ToolRegistry()
        T.register_core_tools(reg, memory=T._StubMemory(), workspace=None,
                              allow_note=True)
        _CATALOG = reg.catalog()
    return _CATALOG


def body_bytes(user: str, system: str, max_tokens: int) -> int:
    body = {
        "model": "qwen2.5:7b",
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }
    return len(json.dumps(body).encode("utf-8"))


# ── 三种提示词形态的基座（尾部启发段全空时）──────────────────────────
def base_propose(goal: str, state: str, n: int) -> int:
    user = (f"目标：{goal}\n"
            f"当前状态：\n{state}\n"
            f"\n"                       # guidance 为空
            f"\n"                       # {avoid}{banned}{reseek} 全空
            f"可用工具：\n{catalog()}\n\n"
            f"请给出 {max(1, n)} 个**互不相同**的候选项。")
    return body_bytes(user, P._SYS, 1024)


def base_answer(goal: str, state: str) -> int:
    user = f"目标：{goal}\n"
    if state:
        user += f"当前状态：\n{state}\n" + P._ANSWER_FIDELITY + "\n"
    user += "请直接给出最终答案。"
    return body_bytes(user, P._ANSWER_SYS, 1024)


def base_answer_nostate(goal: str) -> int:
    return body_bytes(f"目标：{goal}\n请直接给出最终答案。", P._ANSWER_SYS, 1024)


# ── 冻结产物读取 ──────────────────────────────────────────────────
def load_proxy() -> list[dict]:
    return [json.loads(x) for x in
            (RUN / "proxy.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]


def load_states(name: str) -> list[dict]:
    return [json.loads(x) for x in
            (RUN / f"states_{name}.jsonl").read_text(encoding="utf-8").splitlines()
            if x.strip()]


def windows() -> dict[str, tuple[float, float]]:
    w = {}
    for name in ORDER:
        m = re.findall(r"(\d{9,})", (RUN / f"time_{name}.txt").read_text())
        w[name] = (float(m[0]), float(m[1]))
    return w


def facts_of(memdir: str) -> list[dict]:
    c = sqlite3.connect(f"{memdir}/memory.db")
    c.row_factory = sqlite3.Row
    out = []
    for r in c.execute("select id,text,source from facts order by id"):
        out.append({"id": r["id"], "source": r["source"], "text": r["text"],
                    "chars": len(r["text"]),
                    "esc": len(json.dumps(r["text"])) - 2})
    c.close()
    return out


def known_bytes(facts: list[dict]) -> int:
    """`guidance()` 渲染出的 `known` 段在请求体里的字节数（全空时为 0）。

    渲染规则来自 `worldmodel.py:686`：
        "已知易错点：\\n" + "\\n".join(f"- {f.text}" for f in wm)
    """
    if not facts:
        return 0
    txt = "已知易错点：\n" + "\n".join(f"- {f['text']}" for f in facts)
    return len(json.dumps(txt)) - 2      # 去掉两端引号


def main() -> int:
    proxy = load_proxy()
    win = windows()
    print("=" * 92)
    print("V36 收尾：那 2220 字节是谁加的（零模型、零网络、只读冻结产物）")
    print("=" * 92)
    print(f"proxy.jsonl 共 {len(proxy)} 行；catalog 长度 = {len(catalog())} 字符")
    print()

    rounds = {}
    for name in ORDER:
        lo, hi = win[name]
        rounds[name] = [p for p in proxy if lo <= p["t_wall"] < hi]

    # ── A. 台账按 max_tokens 分类：规划 1024 / 预演 200 ──────────────
    print("─" * 92)
    print("A. 请求分类（`max_tokens` 是内核写死的常量，不是模型参数）")
    print("─" * 92)
    print(f"  规划/收尾 `propose|replan|answer` ⇒ 1024；"
          f"预演 `WorldModel.predict` ⇒ {PREDICT_TOKENS}")
    print(f"  {'轮':8s} {'条数':>4} {'规划型':>6} {'预演型':>6}")
    for name in ORDER:
        ls = rounds[name]
        np_ = sum(1 for p in ls if p["max_tokens"] == PREDICT_TOKENS)
        print(f"  {name:8s} {len(ls):>4} {len(ls)-np_:>6} {np_:>6}")
    print()

    # ── B. 两臂逐条对齐：差异只出现在预演型上吗 ─────────────────────
    print("─" * 92)
    print("B. 累积臂 vs 重置臂，逐条比 `req_sha`（按轮内序号对齐）")
    print("─" * 92)
    a, b = rounds["cum_r2"], rounds["rst_r2"]
    print(f"  {'#':>3} {'max_tok':>7} {'类型':>6} {'req_cum':>8} {'req_rst':>8} {'Δ字节':>7}")
    diff_idx = []
    for i, (x, y) in enumerate(zip(a, b)):
        d = x["req_bytes"] - y["req_bytes"]
        kind = "预演" if x["max_tokens"] == PREDICT_TOKENS else "规划"
        mark = ""
        if x["req_sha"] != y["req_sha"]:
            diff_idx.append(i)
            mark = "  <<<"
        print(f"  {i:>3} {x['max_tokens']:>7} {kind:>6} {x['req_bytes']:>8} "
              f"{y['req_bytes']:>8} {d:>+7}{mark}")
    print()
    print(f"  ⇒ 不同的共 {len(diff_idx)} 条：{diff_idx}")
    kinds = {rounds['cum_r2'][i]['max_tokens'] for i in diff_idx}
    print(f"  ⇒ 它们的 max_tokens 取值集合 = {kinds} "
          f"（{'全部是预演调用' if kinds == {PREDICT_TOKENS} else '⚠️ 不止预演'})")
    print()

    # ── C. 字节算术：差额能不能被世界模型事实逐字节解释 ─────────────
    print("─" * 92)
    print("C. 字节算术（`worldmodel.py:686` 的渲染规则）")
    print("─" * 92)
    cum_f = facts_of("/tmp/v36/mem_cum")
    rst_f = facts_of("/tmp/v36/mem_rst")
    for tag, fs in (("cum", cum_f), ("rst", rst_f)):
        print(f"  [{tag}] worldmodel 事实 {len(fs)} 条")
        for f in fs:
            print(f"      id={f['id']} {f['chars']}字 转义后{f['esc']}字节  {f['text']}")
        for r in range(0, len(fs) + 1):
            print(f"      known={r}条 ⇒ {known_bytes(fs[:r])} 字节")
    print()
    print("  两臂 fact 集的关系：")
    cum_ids = {f["id"] for f in cum_f}
    rst_ids = {f["id"] for f in rst_f}
    print(f"    cum={sorted(cum_ids)}  rst={sorted(rst_ids)}  "
          f"cum 多出={sorted(cum_ids - rst_ids)}")
    print()
    obs = sorted({rounds["cum_r2"][i]["req_bytes"] - rounds["rst_r2"][i]["req_bytes"]
                  for i in diff_idx})
    print(f"  实测 Δ 取值 = {obs}")
    print("  可解释的组合：")
    print(f"    cum 两条 / rst 零条 ⇒ known = {known_bytes(cum_f)} 字节")
    print(f"    cum 一条 / rst 零条 ⇒ known = {known_bytes(cum_f[:1])} 字节")
    allowed = {known_bytes(cum_f), known_bytes(cum_f[:1])}
    hit = all(d in allowed for d in obs)
    print(f"  ⇒ 逐字节命中：{'是' if hit else '否'}")

    # ── D. 那条通路在代码里是怎么接上的 ─────────────────────────────
    print()
    print("─" * 92)
    print("D. 通路（全部行号可在本仓库复核）")
    print("─" * 92)
    print("  core.py:1036        pred = self.wm.predict(state=state, action=cand.as_line())")
    print("  worldmodel.py:391   known = self.guidance(action=action, k=3)")
    print("  worldmodel.py:683   facts = self.memory.recall(action, k=k)   ← 同一条 recall")
    print("  worldmodel.py:684   wm = [f for f in facts if f.source == 'worldmodel']")
    print("  ⇒ 检索词是**具体动作行**（`finish({...}) — 意图`），不是 1700 字的题面。")
    print("  ⇒ V36 的 D4 测的是 `guidance(action=goal)`，所以 84 行全是空 ——")
    print("     那不是「guidance 通道死了」，是「测错了检索词」。")
    print()
    print("─" * 92)
    print("E. 预演结果去了哪（`grep pred mw/core.py` 的全部 6 处）")
    print("─" * 92)
    print("  1034  pred = None")
    print("  1036  pred = self.wm.predict(...)")
    print("  1037  if not pred.trustworthy:            ← 只影响「记不记这条读数」")
    print("  1039      self.log(...)")
    print("  1099  if pred is not None and pred.trustworthy:")
    print("  1100      mm = self.wm.observe(pred, ..., ok=result.ok)   ← 只用于事后学习")
    print()
    print("  ⛔ 候选已在预演**之前**选定（1036 行用的是 cand，来自 plan.steps[:n]）；")
    print("     预演不参与选动作、不否决、不重排 ⇒ **它是纯观测，不是决策输入**。")
    print("  ⛔ 于是这条环是：预演 → 执行(不受影响) → 事后比对 → 沉淀成事实")
    print("     → 下一次预演的 known 更长 → 预演更「有依据」→ 仍然不影响执行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
