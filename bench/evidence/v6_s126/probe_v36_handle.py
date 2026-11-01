"""V36 探针：把「句柄」从 `[工作记忆]` 的渲染里去掉之后，同一内容是否逐字节同渲染？

为什么必须先量这一条
--------------------
V35 把「累积到底动过 state 的哪一块」量到字符级，结论是：

    `v26/d2` 的 `e1wi` 两轮 it=1 的 state **逐字节相同**（1198 字），
    it=2 **只差 1 个字符** —— `[b#1 …]` → `[b#12 …]`（插了一个 `2`）。
    而分数 **0 → 16**。两条记忆的正文逐字节相同（都是
    `recall ⇒ ✔ (语义记忆中无相关条目)`）。

⇒ 那 +16 完全由**句柄数字**买来，与学到的东西无关。

本探针不跑模型，只回答一个物理问题：
**同一段观测，在「前面多捕获 k 条」的两种情境下，进 prompt 的那一行是否还相同？**

它答的不是「系统变强了没有」，而是「V25~V35 那 15 轮的 ±16 里，
有多少是句柄噪声」——后者是前者的前提（纪律 31：押注前先量载荷能不能到达押注的格子）。

⛔ 本探针只打印，不写任何文件（纪律 34：证据脚本不该写回自己的证据路径）。
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
sys.path.insert(0, str(ROOT))

from mw.core import Core                       # noqa: E402
from mw.llm import StubLLM                     # noqa: E402
from mw.memory import MemoryStore              # noqa: E402

#: 同一段观测正文。⛔ 不许换 —— 换了就不是「同一内容」的比较。
BODY = "recall ⇒ ✔ (语义记忆中无相关条目)"

#: 前置捕获条数：0 条 vs 11 条。选 11 是因为 V26 那条臂的历史正好是 1→12。
PRESEED = (0, 11)


def sha16(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def case(preseed: int) -> dict:
    """在「先有 preseed 条历史」的库上捕获 BODY，返回句柄与**渲染行**。

    ⛔ 渲染行必须从内核代码取，不许在这里另写一份 ——
       否则探针测的是探针自己，不是产物（纪律 12：取证方式先去装置里确认它存在）。
       这里的做法是走真实的 `Core`，把 capture 之后的那步 state 打出来。
    """
    td = Path(tempfile.mkdtemp(prefix="v36_"))
    mem = MemoryStore(td / "m.db")
    for i in range(preseed):
        mem.capture(f"历史观测 {i}")
    h = mem.capture(BODY)
    # 复算内核用的那一行（`core.py` 的 `index = ...`）—— 与产物逐字对照
    index = f"[{h.chars}字 sha={h.sha}] "
    # 旧口径（V35 及以前）：带 ref
    legacy = f"[{h.ref} {h.chars}字 sha={h.sha}] "
    mem.close()
    return {"preseed": preseed, "ref": h.ref, "sha": h.sha, "chars": h.chars,
            "index": index, "legacy": legacy}


def main() -> int:
    print("=" * 78)
    print("V36 / 判据 D1 的离线地基：同一内容、不同捕获次序，渲染行是否相同？")
    print("=" * 78)
    rows = [case(k) for k in PRESEED]

    print(f"\n{'前置条数':>8} {'ref':>8} {'sha':>14} {'chars':>6}")
    for r in rows:
        print(f"{r['preseed']:>8} {r['ref']:>8} {r['sha']:>14} {r['chars']:>6}")

    a, b = rows[0], rows[1]
    print("\n--- 新口径（V36：只放 chars + sha）---")
    print(f"  preseed=0 : {a['index']!r}")
    print(f"  preseed=11: {b['index']!r}")
    same_new = a["index"] == b["index"]
    print(f"  逐字节相同: {same_new}   sha16={sha16(a['index'])}")

    print("\n--- 旧口径（V25~V35：带 b#N）---")
    print(f"  preseed=0 : {a['legacy']!r}")
    print(f"  preseed=11: {b['legacy']!r}")
    same_old = a["legacy"] == b["legacy"]
    print(f"  逐字节相同: {same_old}   sha16={sha16(a['legacy'])}")

    delta = len(b["legacy"]) - len(a["legacy"])
    print(f"\n  旧口径的字符差 = {delta}（正是 V35 在 v26 里抓到的那 1 个字符）")

    # ── 第二层：跑真实 `core.run()`，从**产物**里取索引行 ──────────────
    # ⛔ 为什么不能用 `_state()` 空跑：那样 `working` 是空的，索引行不会出现，
    #    探针就只能拿自己手抄的 `index = ...` 自证 —— 测的是探针不是产物（纪律 12）。
    print("\n" + "=" * 78)
    print("第二层：走真实 core.run()，从产物里取索引行与 state_meta")
    print("=" * 78)
    td = Path(tempfile.mkdtemp(prefix="v36c_"))
    mem = MemoryStore(td / "m.db")
    core = Core(llm=StubLLM(), memory=mem, memory_mode="raw")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):          # 内核日志很吵，压掉
        core.run("把 read_text 改成显式 encoding", budget_factor=1)
    total_b = sum(st.count("b#") for _, st in core.state_log)
    print(f"  state_log 长度 = {len(core.state_log)}")
    print(f"  state_meta 长度 = {len(core.state_meta)}  （必须与 state_log 相等）")
    print(f"  全部 state 里 'b#' 出现次数 = {total_b}   （要求 0）")
    print(f"  state_meta = {json.dumps(core.state_meta, ensure_ascii=False)[:220]}")
    has_n = all("n" in m for m in core.state_meta)
    has_g = all("guidance" in m for m in core.state_meta)
    print(f"  每条 meta 都有 n={has_n} / guidance={has_g}")
    idx_lines = []
    for it, st in core.state_log:
        i = st.find("[工作记忆]")
        if i >= 0:
            print(f"  it={it} [工作记忆] = {st[i:i + 120]!r}")
            idx_lines.extend(re.findall(r"\[\d+字 sha=[0-9a-f]{12}\]", st))
    print(f"  从产物里抓到的索引行 {len(idx_lines)} 条：{idx_lines[:3]}")
    mem.close()

    ok = (same_new and not same_old and total_b == 0
          and len(core.state_meta) == len(core.state_log))
    print("\n" + "-" * 78)
    print(f"D1 离线地基: {'通过' if ok else '不通过'}"
          f"  （新口径同={same_new} / 旧口径同={same_old} / 产物无b#={total_b == 0}）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
