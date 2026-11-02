"""V50 离线探针 —— 改的是 `planner._SYS` 那一段（§116）。

⛔ 这个探针**证明不了**"模型会先去取材料"：`StubLLM` 不看提示词。
   它只证明四类装置事实，每一条都必须可复算（两次跑 sha 相同）：

  ① 两版 `_SYS` 的差异**恰好**是那一段（多一个字都不行）——
     否则"提示词变了"这句话里混进了别的变化，两臂就说不清是谁的作用；
  ② 改后的句子带的是**可核对的条件**（材料/见过），不是**主观自信**（"你能完成"）；
  ③ 工具面口径（`catalog_sha` 两个口径）**一个都没变** —— 这一轮不动 `agents.json`；
  ④ `_SYS` 真的作为 system message 进了 `propose()` 的那一发（不是死常量）。

用法：`python3 bench/evidence/v6_s140/probe_v50_sys.py`
"""

from __future__ import annotations

import ast
import difflib
import subprocess
import sys
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
sys.path.insert(0, str(ROOT))

from mw.llm import StubLLM                                    # noqa: E402
from mw.planner import Planner, _SYS as NEW_SYS               # noqa: E402
from mw.tools import (ToolRegistry, catalog_sha,              # noqa: E402
                      register_core_tools)

_bad: list[str] = []

#: 改前实测（V49 收尾、`agents.json` = 新描述 + applies_regex）
SHA_EXT_BEFORE = "29091404"      # 前缀即可，完整值见 v6_s139/RESULTS.md
SHA_NOEXT_BEFORE = "8decb60895d4a972"


def _old_sys_from_head() -> str:
    """从 **HEAD 的 blob** 取旧 `_SYS`。

    ⛔ 为什么不用 `git stash` / 不用工作区副本：那要求"跑探针时仓库恰好处于某一状态"。
       从 `HEAD:mw/planner.py` 读 blob，读的是**已提交的事实**，与工作区改没改无关。
    """
    src = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", "HEAD:mw/planner.py"], text=True)
    from mw.planner import ANSWER_MARK
    for node in ast.parse(src).body:
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if getattr(t, "id", None) == "_SYS":
                # ⚠️ 不能 `literal_eval`：最后一项是 f-string（`{ANSWER_MARK}`），
                #    是 `JoinedStr` 而不是常量 ⇒ 只**执行这一条赋值**，
                #    命名空间里只给它需要的那一个常量。源码来自本仓库的 blob。
                mod = ast.Module(body=[node], type_ignores=[])
                ns: dict = {"ANSWER_MARK": ANSWER_MARK}
                exec(compile(mod, "<HEAD:mw/planner.py>", "exec"), ns)  # noqa: S102
                return ns["_SYS"]
    raise SystemExit("⛔ HEAD 的 planner.py 里找不到 _SYS")


def main() -> int:
    old = _old_sys_from_head()

    print("V50 离线探针 —— 改 `_SYS` 动了什么、没动什么")
    print("=" * 74)

    # ── S1 差异**恰好**是那一段 ─────────────────────────────────────────
    lo, ln = old.split("\n"), NEW_SYS.split("\n")
    ud = list(difflib.unified_diff(lo, ln, lineterm="", n=0))
    removed = [x[1:] for x in ud if x.startswith("-") and not x.startswith("---")]
    added = [x[1:] for x in ud if x.startswith("+") and not x.startswith("+++")]
    ok1 = (len(removed) == 3 and len(added) == 5)
    print(f"S1 恰好删 3 行、加 5 行  {'✅' if ok1 else '❌'}  "
          f"删={len(removed)} 加={len(added)}")
    print(f"   旧 _SYS {len(old)} 字符 / {len(lo)} 行；新 {len(NEW_SYS)} 字符 / {len(ln)} 行"
          f"  （Δ={len(NEW_SYS) - len(old):+d} 字符）")
    if not ok1:
        _bad.append(f"S1 差异不是恰好 3 删 5 加（删={len(removed)} 加={len(added)}）")
        for x in ud:
            print("   ", x[:130])

    # ── S2 旧句的判据是"主观自信"，新句是"可核对的条件" ────────────────
    # 这是**改动意图**的唯一可机检形式：不检"意思对不对"（检不了），
    # 只检"这两类词各自在哪一版里"。
    ok2 = (("能直接完成任务" in old) and ("能直接完成任务" not in NEW_SYS)
           and all(m in NEW_SYS for m in
                   ("材料已在手", "亲眼见过", "没有材料就没有答案"))
           and all(m not in old for m in
                   ("材料已在手", "没有材料就没有答案")))
    print(f"S2 旧句含主观判据且已删除；新句三个 marker 齐全  {'✅' if ok2 else '❌'}")
    if not ok2:
        _bad.append("S2 marker 不齐：主观判据未删净，或新材料条件缺席")

    # ── S3 不含软措辞（T=0 下与噪声无法区分）────────────────────────────
    soft = [w for w in ("可选", "建议", "视情况", "尽量", "酌情", "合适时") if w in NEW_SYS]
    ok3 = not soft
    print(f"S3 新句不含软措辞  {'✅' if ok3 else '❌'}  {soft or '（无）'}")
    if not ok3:
        _bad.append(f"S3 新句含软措辞: {soft}")

    # ── S4 工具面口径一个都没变 ─────────────────────────────────────────
    # 本轮不动 `agents.json` ⇒ 两个口径都应等于 V49 收尾值。
    s_noext = catalog_sha(wire_external=False, agents_path=ROOT / "agents.json")
    s_ext = catalog_sha(wire_external=True, agents_path=ROOT / "agents.json")
    ok4 = (s_noext == SHA_NOEXT_BEFORE) and s_ext.startswith(SHA_EXT_BEFORE)
    print(f"S4 catalog_sha 两个口径未变  {'✅' if ok4 else '❌'}  "
          f"noext={s_noext[:16]} ext={s_ext[:16]}")
    if not ok4:
        _bad.append(f"S4 工具面口径变了（本轮不该动）：noext={s_noext} ext={s_ext}")

    # ── S5 `_SYS` 真的进了 propose 的那一发（不是死常量）────────────────
    stub = StubLLM(default=(
        '{"candidates": [{"intent": "交答案", "tool": "finish", '
        '"args": {"answer": "ok"}, "rationale": "材料已在手"}]}'))
    reg = ToolRegistry()
    register_core_tools(reg, memory=None, workspace=None, allow_note=True)
    pl = Planner(stub, reg, max_tokens=512, state_chars=800, salvage=0)
    pl.propose(goal="把 1 加到 2", state="（空）", history=[], n=1)
    sys_msg = stub.calls[0][0].content if stub.calls and stub.calls[0] else ""
    ok5 = (sys_msg == NEW_SYS) and stub.calls[0][0].role == "system"
    print(f"S5 `_SYS` 逐字节进了 propose 的 system message  {'✅' if ok5 else '❌'}  "
          f"role={stub.calls[0][0].role if stub.calls else '-'}")
    if not ok5:
        _bad.append("S5 _SYS 没进 propose 的 system message（改了常量但没接上）")

    # ── S6 新句仍然**保留**"直接 finish"这条路 ──────────────────────────
    # 删掉它 ⇒ 模型对单函数题也先去 list_dir ⇒ 96 格必塌（PREREG §2 的边界）。
    ok6 = ("直接 finish" in NEW_SYS) and ("不必绕路" in NEW_SYS)
    print(f"S6 保留「不必绕路，直接 finish」这条路  {'✅' if ok6 else '❌'}")
    if not ok6:
        _bad.append("S6 把直接 finish 这条路删了 —— 96 格会塌")

    print()
    print(f"结论：{'✅ 六景全绿' if not _bad else '❌ ' + str(len(_bad)) + ' 景红'}")
    for b in _bad:
        print("  ⛔", b)
    print()
    print("⛔ 本探针**不**回答「模型会不会先去取材料」—— `StubLLM` 不看提示词。")
    print("   那个问题只能由真实两臂回答（见 PREREG §5 J1~J4）。")
    return 0 if not _bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
