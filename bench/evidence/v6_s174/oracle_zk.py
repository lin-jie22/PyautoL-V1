#!/usr/bin/env python3
"""离线闸 G0：**按 ZK1~ZK4 惯例**写参考实现，逐题跑题面 tests。

问句：如果模型 100% 照做记忆里那 4 条惯例，能不能拿满分？
  不能 ⇒ B 臂的上限不在模型，而在「知识本身不足」 ⇒ 这一轮测不了「会不会用」。
⛔ 零 LLM：这里只验「知识够不够」，不验「模型会不会」。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SUITE = ROOT / "bench/heldout/g7j_n4_heldout.jsonl"


# ── ZK1 去重：比较前 strip+lower，保留**第一次**出现的原文 ──────────────
def dedupe_keep(xs):
    seen = set()
    out = []
    for x in xs:
        k = x.strip().lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


# ── ZK2 金额：千分位空格，两位小数，负数用圆括号不写负号 ────────────────
def fmt_amount(x):
    neg = x < 0
    s = f"{abs(float(x)):.2f}"
    head, _, tail = s.partition(".")
    groups = []
    while len(head) > 3:
        groups.insert(0, head[-3:])
        head = head[:-3]
    groups.insert(0, head)
    s = " ".join(groups) + "." + tail
    return f"({s})" if neg else s


# ── ZK3 开关：y/yes/1 为真；'true'/'on' 等算假；其余 None ───────────────
def parse_flag(s):
    t = (s or "").strip().lower()
    if t in ("y", "yes", "1"):
        return True
    if t in ("true", "on", "t", "0", "no", "n", "false", "off"):
        return False
    return None


# ── ZK4 截断：超长保留**最后** n 个字符，前面加 '…' ────────────────────
def truncate_tail(s, n):
    if len(s) <= n:
        return s
    return "…" + s[-n:]


IMPL = {"dedupe_keep": dedupe_keep, "fmt_amount": fmt_amount,
        "parse_flag": parse_flag, "truncate_tail": truncate_tail}


def main() -> int:
    bad = 0
    for line in SUITE.read_text(encoding="utf-8").splitlines():
        task = json.loads(line)
        tid = task["id"]
        fn = next(iter(IMPL))
        for name, f in IMPL.items():
            if name in task["tests"][0]:
                fn = name
        ns = dict(IMPL)
        ok = 0
        fails = []
        for expr in task["tests"]:
            try:
                exec(expr, ns)             # noqa: S102 — 题面 tests，同打分器口径
                ok += 1
            except Exception as e:         # noqa: BLE001
                fails.append(f"{expr}  ⇒ {type(e).__name__}: {e}")
        print(f"{tid:22s} {ok}/{len(task['tests'])}  {'' if ok == len(task['tests']) else 'FAIL'}")
        for f in fails:
            print("    ", f[:160])
        bad += ok != len(task["tests"])
    print(f"\n⇒ 参考实现满分题数 = {4 - bad}/4")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
