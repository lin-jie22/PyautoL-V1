"""V49 离线探针 —— 改的是**提示词**，不是内核（§115）。

⛔ 这个探针**证明不了**"模型会少提它"：`StubLLM` 不看提示词。
   它只证明四类装置事实，每一条都必须可复算（两次跑 sha 相同）。

为什么这些事实值得单独钉：V47 之后 `dropped_na=9` 说明"内核知道、模型不知道"。
这一轮把同样的信息写进 `description`（进 `catalog_line()` ⇒ 进 planner 的
「可用工具」块）。改的是提示词，所以：

  ① 改的东西必须**只**是那一行 —— 否则"提示词变了"这句话里混进了别的变化，
     两臂就说不清是谁的作用；
  ② 96 格口径（`--no-external`）**不许**跟着变 —— 变了就说明目录组装路径
     与读法不符，本轮必须停下来查，不许拿 96 格基线继续用；
  ③ 真实任务口径（`--external`）**必须**跟着变 —— 不变就说明这个字段
     根本没进提示词（V36 那种"改了模板但没生效"）。

用法：`python3 bench/evidence/v6_s139/probe_v49_desc.py`
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/Users/linshaojie/V1")

from mw.tools import (ToolRegistry, register_core_tools,  # noqa: E402
                      register_external_agents, catalog_sha, spec_applies)

ROOT = Path("/Users/linshaojie/V1")
AP_REAL = ROOT / "agents.json"

#: 旧描述（V48 及之前，`agents.json` 里那一句）—— 逐字冻结，不许改。
OLD_DESC = ("会执行的编码 agent：同题多角度生成 3 份实现，自写 assert 用例，"
            "真跑后选通过最多的一份，返回可直接交付的代码。")

#: 新描述（V49）—— **必须**与 `agents.json` 逐字相同，S6 会检。
NEW_DESC = ("只做「题面里明确指定了函数名」的编程题（如『函数名必须是 foo』）："
            "生成 3 份实现、自写 assert、真跑后返回通过最多的一份。"
            "⛔ 题面没指定函数名时它一定失败，不要选它。")

#: 改前实测（PREREG §3，改描述**之前**跑的）
SHA_EXT_BEFORE = "2cc3b0225ee03223"
SHA_NOEXT_BEFORE = "8decb60895d4a972"

APPLIES = r"函数名(?:必须|应)(?:是|为)\s*`?\s*[A-Za-z_][A-Za-z0-9_]*"

GOAL_FIT = "请写一个函数，把 '1h30m'、'45s' 解析为总秒数（int）。函数名必须是 parse_duration。只输出代码。"
GOAL_UNFIT = ("工作目录里有 sales.csv（列为 月份,区域,金额）。请计算每个区域的金额合计，"
              "按合计从高到低生成一张 markdown 表格，写入工作目录下的 report.md。")

_TMP = Path(tempfile.mkdtemp(prefix="v49probe_"))
_bad: list[str] = []


def _cfg(*, desc: str, applies: bool) -> Path:
    """按两个维度造一份 agents.json，返回路径。"""
    agent = {
        "type": "shell",
        "description": desc,
        "command": ["python3", "external/code_agent.py"],
        "timeout_s": 300,
        "produces_answer": True,
    }
    if applies:
        agent["applies_regex"] = APPLIES
    p = _TMP / f"agents_{'new' if desc is NEW_DESC else 'old'}_{int(applies)}.json"
    p.write_text(json.dumps({"agents": {"code_agent": agent}},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _cat(path: Path) -> tuple[str, ToolRegistry]:
    reg = ToolRegistry()
    register_core_tools(reg, memory=None, workspace=None, allow_note=True)
    register_external_agents(reg, path)
    return reg.catalog(), reg


def _sha(*, external: bool, path: Path) -> str:
    return catalog_sha(wire_external=external, agents_path=path)


def main() -> int:
    old_a = _cfg(desc=OLD_DESC, applies=False)
    old_y = _cfg(desc=OLD_DESC, applies=True)
    new_a = _cfg(desc=NEW_DESC, applies=False)
    new_y = _cfg(desc=NEW_DESC, applies=True)

    cat_oy, _ = _cat(old_y)
    cat_ny, _ = _cat(new_y)
    _, reg_oa = _cat(old_a)
    _, reg_oy = _cat(old_y)
    _, reg_na = _cat(new_a)
    _, reg_ny = _cat(new_y)

    print("V49 离线探针 —— 改 description 动了什么、没动什么")
    print("=" * 74)

    # ── S1 两份配置的目录**只在 code_agent 那一行**不同 ──────────────────
    lo, ln = cat_oy.split("\n"), cat_ny.split("\n")
    diff = [(i, a, b) for i, (a, b) in enumerate(zip(lo, ln)) if a != b]
    ok1 = (len(lo) == len(ln) == 4 and len(diff) == 1
           and diff[0][1].startswith("- agent:code_agent")
           and diff[0][2].startswith("- agent:code_agent"))
    print(f"S1 目录恰好差 1 行、且差的那行是 agent:code_agent  "
          f"{'✅' if ok1 else '❌'}  行数={len(lo)}/{len(ln)} 差异={len(diff)}")
    if not ok1:
        _bad.append("S1 目录差异不是恰好一行 agent:code_agent")
        for i, a, b in diff[:4]:
            print(f"   [{i}] - {a[:110]}")
            print(f"   [{i}] + {b[:110]}")

    # ── S2 96 格口径（--no-external）**不许**跟着变 ──────────────────────
    s2 = {k: _sha(external=False, path=p) for k, p in
          (("old_no", old_a), ("old_yes", old_y), ("new_no", new_a), ("new_yes", new_y))}
    ok2 = len(set(s2.values())) == 1 and s2["new_yes"] == SHA_NOEXT_BEFORE
    print(f"S2 --no-external 口径四份同一个 sha、且等于改前  {s2['new_yes'][:16]}  "
          f"{'✅' if ok2 else '❌'}  四份去重={len(set(s2.values()))}")
    if not ok2:
        _bad.append(f"S2 96 格口径变了: {s2}")

    # ── S3 真实任务口径**必须**跟着变（且与 applies 无关）────────────────
    s3 = {k: _sha(external=True, path=p) for k, p in
          (("old_no", old_a), ("old_yes", old_y), ("new_no", new_a), ("new_yes", new_y))}
    ok3 = (s3["old_no"] == s3["old_yes"] == SHA_EXT_BEFORE
           and s3["new_no"] == s3["new_yes"]
           and s3["new_no"] != SHA_EXT_BEFORE)
    print(f"S3 --external 口径 old={s3['old_yes'][:16]} new={s3['new_yes'][:16]}  "
          f"{'✅' if ok3 else '❌'}  （old 必等于改前的 {SHA_EXT_BEFORE[:16]}，new 必不同）")
    if not ok3:
        _bad.append(f"S3 真实口径没按预期变: {s3}")

    # ── S4 描述必须单行、且不超长（catalog_line 是一行一条）──────────────
    ok4 = (("\n" not in NEW_DESC) and ("\n" not in OLD_DESC)
           and len(NEW_DESC) <= 200)
    print(f"S4 描述单行且 ≤200 字符  {'✅' if ok4 else '❌'}  "
          f"new={len(NEW_DESC)} 字符 old={len(OLD_DESC)} 字符")
    if not ok4:
        _bad.append("S4 描述含换行或超长")

    # ── S5 两处判据同源（都锚在「函数名」上）+ 方向确实是"负向" ──────────
    ok5 = (("函数名" in NEW_DESC) and ("函数名" in APPLIES)
           and ("不要选" in NEW_DESC) and ("不要选" not in OLD_DESC))
    print(f"S5 新描述与 applies_regex 同锚「函数名」，且新增负向声明  "
          f"{'✅' if ok5 else '❌'}")
    if not ok5:
        _bad.append("S5 描述与 applies_regex 不同源，或方向不是负向")

    # ── S6 仓库里的 agents.json 必须与 NEW_DESC 逐字相同（防漂）────────
    real = json.loads(AP_REAL.read_text(encoding="utf-8"))["agents"]["code_agent"]
    ok6 = real.get("description") == NEW_DESC and real.get("applies_regex") == APPLIES
    print(f"S6 仓库 agents.json 与探针常量逐字相同  {'✅' if ok6 else '❌'}")
    if not ok6:
        _bad.append("S6 agents.json 与 NEW_DESC/APPLIES 不一致（有一处改了另一处没改）")

    # ── S7 描述不影响准入判定（改的是提示词，不是判据）──────────────────
    # ⚠️ 必须**分两组**比：`applies=False` 的配置本来就没有判据，
    #    `spec_applies` 走"没声明 = 恒适用"那一档 ⇒ 不适配题面也返回 True。
    #    第一版把四份当成同一种语义（期望 [False]*4），红了一次 —— 那是期望写错，
    #    不是判据坏了。三态语义在 V47 就定了（§113.2），这里要验的是
    #    **同一 granted 条件下改描述不改变判定**，不是"四份都该判 False"。
    granted = [spec_applies(reg.get("agent:code_agent"), GOAL_UNFIT)
               for reg in (reg_oy, reg_ny)]
    never = [spec_applies(reg.get("agent:code_agent"), GOAL_UNFIT)
             for reg in (reg_oa, reg_na)]
    fit = [spec_applies(reg.get("agent:code_agent"), GOAL_FIT)
           for reg in (reg_oy, reg_ny, reg_na, reg_oa)]
    ok7 = (granted == [False, False] and never == [True, True]
           and fit == [True] * 4)
    print(f"S7 有判据的两份：不适配=False/适配=True；无判据的两份：恒适用=True  "
          f"{'✅' if ok7 else '❌'}")
    print(f"   有判据 unfit={granted}   无判据 unfit={never}   全部 fit={fit}")
    if not ok7:
        _bad.append(f"S7 准入判定被描述改动影响: granted={granted} never={never} fit={fit}")

    print()
    print(f"结论：{'✅ 七景全绿' if not _bad else '❌ ' + str(len(_bad)) + ' 景红'}")
    for b in _bad:
        print("  ⛔", b)
    print()
    print("⛔ 本探针**不**回答「模型会不会少提它」—— `StubLLM` 不看提示词。")
    print("   那个问题只能由真实两臂回答（见 PREREG §4/§5 J1~J4、J8）。")
    return 0 if not _bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
