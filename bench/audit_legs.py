#!/usr/bin/env python3
"""§66 两臂对账：重复跳过那一步的「另一条腿」到底去哪了。

⛔ 为什么要单独量这件事：§65 的修法（把 `blocked_now` 加进分母）押在
   「混合轮 = 重复 + 被拦」这个假设上。只要真实数据里那条腿是**被执行**或
   **被预算掐掉**而不是**被拦**，这个修法就永远咬不到 —— 而"零咬合"在
   `stalled == 0` 这一个数上看不出来（它和"没机会"同形）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TS = re.compile(r"^(\d{2}):(\d{2}):(\d{2})")
STEP = re.compile(r"\[(\d+)\] 通道=\S+\s+候选=(\d+)")
SKIP_REPEAT = "重复动作已跳过"
EXECUTED = "↳ "
BLOCKED = ("✋ ", "⊘ ")
BUDGET = "预算耗尽"
TASK = re.compile(r"▶ 任务开始: 写一个函数 (\w+)\(")


def parse_text(text: str) -> list[dict]:
    """把一段日志文本切成 (任务, 步)，每步记录候选行与随后的归属。"""
    episodes: list[dict] = []
    cur_task = "?"
    cur_step: dict | None = None
    for line in text.splitlines():
        m = TASK.search(line)
        if m:
            cur_task = m.group(1)
            episodes.append({"task": cur_task, "steps": []})
            cur_step = None
            continue
        if not episodes:
            continue
        m = STEP.search(line)
        if m:
            cur_step = {"it": int(m.group(1)), "cands": int(m.group(2)),
                        "repeat": 0, "exec": 0, "blocked": 0, "budget": 0}
            episodes[-1]["steps"].append(cur_step)
            continue
        if cur_step is None:
            continue
        if SKIP_REPEAT in line:
            cur_step["repeat"] += 1
        elif EXECUTED in line:
            # ⚠️ 自踩（照登）：第一版写 `line.lstrip().split(" ", 1)[-1].startswith("↳")`，
            #    而 `split(" ", 1)[-1]` 保留了**前导空格** ⇒ 永远 False ⇒ `exec` 恒为 0
            #    ⇒ 所有混合轮都被归成「预算掐掉」。那个方向**对结论有利**
            #    （正好支持"修法永不开火"），所以必须先修再读数（§62：混口径往往是自我打脸）。
            #    A4 就是钉这一条的回归。
            cur_step["exec"] += 1
        elif any(k in line for k in BLOCKED):
            cur_step["blocked"] += 1
        elif BUDGET in line:
            cur_step["budget"] += 1
    return episodes


def parse(path: Path) -> list[dict]:
    return parse_text(path.read_text(encoding="utf-8", errors="replace"))


def _leg(s: dict) -> str:
    """那一步的「另一条腿」：非重复候选的去向。"""
    other = s["cands"] - s["repeat"]
    if s["blocked"] >= other:
        return "被拦(blocked_now>0)"
    if s["exec"] >= other:
        return "被执行"
    if s["budget"]:
        return "预算/墙钟掐掉"
    return "无记录(未知)"


def report(label: str, path: Path) -> dict:
    eps = parse(path)
    mixed = allrep = 0
    legs: dict[str, int] = {}
    for ep in eps:
        for s in ep["steps"]:
            if s["repeat"] == 0:
                continue
            if s["repeat"] == s["cands"]:
                allrep += 1
                continue
            mixed += 1
            # 那一步的「另一条腿」：非重复候选的去向
            key = _leg(s)
            legs[key] = legs.get(key, 0) + 1
    print(f"{label:6s} 步={sum(len(e['steps']) for e in eps):4d} "
          f"全等={allrep:3d} 混合={mixed:3d}  另一条腿: {legs}")
    return {"all_repeat": allrep, "mixed": mixed, "legs": legs}


_SELF_HEAD = "10:00:00 ▶ 任务开始: 写一个函数 norm_zz(rows)，参数是字符串列表。\n"


def _mk(*lines: str) -> str:
    return _SELF_HEAD + "\n".join(lines) + "\n"


def self_check() -> int:
    """A1~A5：分类器必须能**分出**三种腿 —— 否则「0 次被拦」是装置缺陷不是读数。

    ⛔ 为什么必须验「被拦」那一档：本轮的结论就是「被拦 = 0」。
       一个永远归不出「被拦」的分类器，给出的 0 和真实的 0 **同形**。
    """
    bad: list[str] = []

    def one(name: str, got: str, want: str) -> None:
        ok = got == want
        print(f"  {'✅' if ok else '❌'} {name}: {got}" + ("" if ok else f"（期望 {want}）"))
        if not ok:
            bad.append(name)

    # A1 重复 + 被执行 ⇒ 被执行
    ep = parse_text(_mk("10:00:01   [2] 通道=深 候选=2",
                        "10:00:01       ⏭ 重复动作已跳过（不产生新信息）: finish",
                        "10:00:05       ↳ recall ok=True 检查已有代码"))
    one("A1 重复+执行 ⇒ 被执行", _leg(ep[0]["steps"][0]), "被执行")

    # A2 重复 + 被拦 ⇒ 被拦（本轮结论的关键档）
    ep = parse_text(_mk("10:00:01   [2] 通道=深 候选=2",
                        "10:00:01       ⏭ 重复动作已跳过（不产生新信息）: finish",
                        "10:00:02       ⊘ [values:answer] 否决 — 空答案"))
    one("A2 重复+被拦 ⇒ 被拦", _leg(ep[0]["steps"][0]), "被拦(blocked_now>0)")

    # A3 全等（重复 2 / 候选 2）⇒ 不进混合
    ep = parse_text(_mk("10:00:01   [3] 通道=深 候选=2",
                        "10:00:01       ⏭ 重复动作已跳过（不产生新信息）: a",
                        "10:00:01       ⏭ 重复动作已跳过（不产生新信息）: b"))
    s = ep[0]["steps"][0]
    one("A3 全等不进混合", "全等" if s["repeat"] == s["cands"] else "混合", "全等")

    # A4 ★ 回归：真实日志格式（时间戳 + 前导空格 + ↳）必须被认成「执行」
    ep = parse_text(_mk("10:00:01   [2] 通道=深 候选=2",
                        "10:00:01       ⏭ 重复动作已跳过（不产生新信息）: finish",
                        "10:00:05       ↳ recall ok=True 检查已有代码是否满足所有要求"))
    one("A4 ★前导空格陷阱回归", str(ep[0]["steps"][0]["exec"]), "1")

    # A5 没有步骤头的重复行不许静默丢（要落到上一step或干脆不吞掉计数）
    ep = parse_text(_mk("10:00:01   [1] 通道=深 候选=1",
                        "10:00:01       ⏭ 重复动作已跳过（不产生新信息）: finish"))
    one("A5 步骤头后的重复被记账", str(ep[0]["steps"][0]["repeat"]), "1")

    print()
    if bad:
        print(f"❌ 自检失败 {len(bad)} 条：{bad}")
        return 1
    print("✅ A1~A5 全绿")
    return 0


def main(argv: list[str]) -> int:
    if "--self-check" in argv:
        return self_check()
    args = [a for a in argv[1:] if not a.startswith("-")]
    if not args:
        print("用法: analyze_blocked.py <log> [<log> ...] | --self-check")
        return 2
    out = {}
    for p in map(Path, argv[1:]):
        if not p.exists():
            print(f"缺 {p}")
            continue
        out[p.name] = report(p.name, p)
    print()
    print("判读：若 'legs' 里出现 `被拦(blocked_now>0)` 才算 §65.9 的修法有机会咬合；")
    print("      若尽是 `被执行` / `预算掐掉`，这个修法在真实分布上永不开火。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
