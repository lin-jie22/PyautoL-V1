"""V232（§303）离线自测：`failed_actuals` 会不会编造实际值。

⛔ 为什么这一条必须单测：Reflector 的全部输入都靠它。
   如果它**编**一个实际值，Reflector 会被引向一个不存在的分支 ——
   那比「没有实际值」更糟（V231 已证：诊断错 ⇒ 模型忠实执行错诊断 ⇒ 分数不动）。

    A1 真失败给出**真的**实际值（`None` / `3600`），与手算一致
    A2 通过的断言**不出现**（它们没有诊断价值，还会撑长 prompt）
    A3 断言不是 `==` 形状 ⇒ 退回异常名，⛔ 不编值
    A4 `build_prompt` 带/不带 actuals 的差异**只有那一段**
    A5 代码本身抛异常 ⇒ 报异常名，不报一个假返回值

零 LLM、零网络。跑法：
    unset PYTHONPATH && python3 bench/evidence/l3_s302/selftest_v232.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.reflect import build_prompt, failed_actuals   # noqa: E402

#: V230 落盘的那版（`24c322d5d9b6`）：`1h30m` 只算小时段、`10m` 被 except 吞成 None。
CODE = '''def parse_duration(s):
    if not s:
        return None
    try:
        parts = s.lower().split('h')
        if len(parts) == 1:
            parts = s.lower().split('m')
            if len(parts) == 1:
                parts = s.lower().split('s')
                if len(parts) == 1:
                    return None
                else:
                    return int(parts[0])
            else:
                hours = int(parts[0])
                minutes = int(parts[1].split('m')[0])
                return hours * 3600 + minutes * 60
        else:
            hours = int(parts[0])
            return hours * 3600
    except ValueError:
        return None'''

TESTS = [
    "assert parse_duration('1h30m') == 5400",
    "assert parse_duration('45s') == 45",
    "assert parse_duration('2h') == 7200",
    "assert parse_duration('10m') == 600",
    "assert parse_duration('') is None",
    "assert parse_duration('abc') is None",
]

ok = 0
bad = 0


def chk(name: str, cond: bool, detail: str = "") -> None:
    global ok, bad
    if cond:
        ok += 1
        print(f"  ✅ {name}")
    else:
        bad += 1
        print(f"  ❌ {name}  {detail}")


print("== A1 真失败 ⇒ 真的实际值（手算：1h30m→3600、10m→None）")
act = failed_actuals(CODE, TESTS)
for a in act:
    print("   ", a)
chk("恰好两条（只有两条失败）", len(act) == 2, str(len(act)))
chk("1h30m 实得 3600", any("1h30m" in a and "3600" in a for a in act), str(act))
chk("10m 实得 None", any("10m" in a and "None" in a for a in act), str(act))
chk("期望值也带上（600 / 5400）",
    any("600" in a for a in act) and any("5400" in a for a in act), str(act))

print("== A2 通过的断言不出现")
chk("45s / 2h / 空串 / abc 一条都不出现",
    not any(k in " ".join(act) for k in ("45s", "'2h'", "parse_duration('')", "'abc'")),
    " ".join(act))

print("== A3 非 == 形状 ⇒ 退回异常名，不编值")
act2 = failed_actuals("def f(x):\n    return 1", ["assert f(1) is None"])
chk("给出现象但不编造值", "拿不到实际值" in " ".join(act2) or "Assertion" in " ".join(act2),
    str(act2))

print("== A4 prompt 两态的差异只有那一段")
p0 = build_prompt("G", "C", "W")
p1 = build_prompt("G", "C", "W", act)
chk("不带 actuals 时无该段", "【实测返回值】" not in p0)
chk("带 actuals 时有该段", "【实测返回值】" in p1)
chk("带 actuals 时逐条列出", all(a in p1 for a in act))
chk("两态都要求不输出代码", p0.count("不要输出任何代码") == p1.count("不要输出任何代码") == 1)

print("== A5 代码本身抛异常 ⇒ 报异常名")
act3 = failed_actuals("def f(x):\n    raise RuntimeError('boom')",
                      ["assert f(1) == 2"])
chk("报 RuntimeError 而不是假值", "RuntimeError" in " ".join(act3), str(act3))

print(f"\n{ok} ok / {bad} bad")
sys.exit(1 if bad else 0)
