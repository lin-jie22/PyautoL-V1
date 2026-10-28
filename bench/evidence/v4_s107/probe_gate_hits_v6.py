#!/usr/bin/env python3
"""打回日志探针 v6（第 107 轮 §77）：按**题面前缀**分组，再按函数名归到 pair。

## 为什么不能只按函数名归因

本轮的配对设计是"同一道题两个题面形态"：`p1bf` 与 `p1sk` 的
`fn` 都叫 `tag_report`。日志里 `▶ 任务开始:` 后面只印 `goal[:120]`，
**光看函数名分不出 bf 与 sk**。

修法：**先分组、再取函数名**。

    bf 版题面："写一个函数 `tag_report`，函数签名**必须**是 …"
    sk 版题面："下面是一段**已定稿**的框架代码，请你把函数 `tag_report` 补全。…"

⇒ 含「已定稿」= sk，否则 = bf。然后用函数名映射到 pair。

⚠️ 开工时的第一版 sk 题面写的是「请把它补全」，**第一行没有函数名**
   ⇒ 探针一个 sk 块都归不了因（自证当场炸）。题面因此改成「请把函数 `X` 补全」。
   这条写进 RESULTS 的自踩表：**探针的第一行依赖，要先在真题面上验过再起跑批**。

## 为什么用打回日志而不是 `answers.jsonl`

`answers.jsonl` **只存每次尝试的最终交付**（§74 的 `a1_v4` = 24 行 = 24 次尝试）。
"反馈前的第一次交付"在本装置里没有独立记录（§76.6.1 自踩）。
机制被触发的**那一刻**在 `run.log` 里独立记账，那才是"机会"的可信来源。

## 定义

    机会(题)   = 该题**至少有一次**交付被判「未定义名」
    机会(尺)   = 机会为真的题数 / 该组题数
    兑现(题)   = 该题最终分 > 0（零模型，读台账；只在报告里用，不参与判决）

⚠️ `=2` 臂**看不见名字**（盲报的载荷就是不给细节）⇒ 名字归因只来自 `=1` 臂；
   两臂的**触发题数**若相同，则触发集等价（§73 的等长安慰剂臂正是靠这一点成立）。

用法：
    python3 bench/evidence/v4_s107/probe_gate_hits_v6.py --log /tmp/v1_s107_run_a1_v6.log
    python3 bench/evidence/v4_s107/probe_gate_hits_v6.py --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_BLOCK = "▶ 任务开始"
_REJ_REWRITE = "自检未过"              #: 主循环候选被闸拦下 ⇒ 退回重写（还有机会改）
_REJ_CLOSEOUT = "收尾交付**未过闸**"    #: 收尾那一脚被闸拦下（**没有下一次**）
_NAME = re.compile(r"未定义的名字 '([^']+)'")
_SK_MARK = "已定稿"

BF = ("p1bf", "p2bf", "p3bf")
SK = ("p1sk", "p2sk", "p3sk")
_FN2PAIR = {"tag_report": "p1", "save_roster": "p2", "week_buckets": "p3"}


def classify(head: str) -> tuple[str | None, str | None]:
    """从 `▶ 任务开始:` 那一行判 (组, 题 id)。判不出返回 (None, None)。

    ⚠️ 不要求 `def ` 前缀：`mw/core.py:729` 只印 `goal[:120]`，而 sk 版题面的
       第一行是「下面是一段**已定稿**的框架代码，请你把函数 `tag_report` 补全。」
       —— 函数名在，`def ` 不在。第一版要求 `def (\\w+)` ⇒ sk 版全部判不出（自证当场炸）。
    """
    names = [n for n in _FN2PAIR if n in head]
    if len(names) != 1:          # 0 个 = 判不出；2 个以上 = 题面里混进了别的函数名
        return None, None
    pair = _FN2PAIR[names[0]]
    group = "sk" if _SK_MARK in head else "bf"
    return group, f"{pair}{group}"


def parse(log_path: str) -> dict:
    blocks: list[list[str]] = []
    cur: list[str] = []
    for line in Path(log_path).read_text(errors="replace").splitlines():
        if _BLOCK in line:
            if cur:
                blocks.append(cur)
            cur = [line]
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)

    per: dict[str, dict] = {}
    unknown = 0
    for b in blocks:
        group, tid = classify(b[0])
        if tid is None:
            unknown += 1
            continue
        rw = 0      #: 主循环打回次数
        co = 0      #: 收尾未过闸次数
        names: list[str] = []
        for line in b:
            if _REJ_REWRITE in line:
                rw += 1
                names.extend(_NAME.findall(line))
            elif _REJ_CLOSEOUT in line:
                co += 1
                names.extend(_NAME.findall(line))
        d = per.setdefault(tid, {"group": group, "attempts": 0,
                                 "hit_attempts": 0, "rewrite": 0, "closeout": 0,
                                 "names": {}})
        d["attempts"] += 1
        d["rewrite"] += rw
        d["closeout"] += co
        if rw or co:
            d["hit_attempts"] += 1
        for nm in names:
            d["names"][nm] = d["names"].get(nm, 0) + 1
    return {"per": per, "unknown_blocks": unknown}


def report(raw: dict) -> dict:
    per = raw["per"]
    bf_hit = [t for t in BF if per.get(t, {}).get("hit_attempts", 0)]
    sk_hit = [t for t in SK if per.get(t, {}).get("hit_attempts", 0)]
    return {
        "bf_hit": bf_hit, "bf_n": len(bf_hit),
        "sk_hit": sk_hit, "sk_n": len(sk_hit),
        "total_hit_n": len(bf_hit) + len(sk_hit),
        "attempts": sum(d["attempts"] for d in per.values()),
        "rewrite": sum(d["rewrite"] for d in per.values()),
        "closeout": sum(d["closeout"] for d in per.values()),
        "unknown_blocks": raw["unknown_blocks"],
        "per": per,
    }


def selftest() -> int:
    """反证：构一份真格式的假日志，探针必须分别判出 bf/sk、两条闸路径、有名字/没名字。"""
    fake = """▶ 任务开始: 写一个函数 `tag_report`，函数签名**必须**是 `def tag_report(items, path)`（参数名与顺序照此写，不要改）。按下面列出的规则实现：
22:00:01   [1] 通道=深 候选=1
22:00:02   ✅ 收尾: ok
▶ 任务开始: 下面是一段**已定稿**的框架代码，请你把函数 `tag_report` 补全。
22:01:01   [1] 通道=深 候选=1
22:01:02       🧪 自检未过（第 1/2 次打回） ⇒ 退回重写：2/5 项未过：未定义的名字 'os'（在 tag_report 内被引用，模块里既没有 import 也没有赋值）；未定义的名字 're'（在 …）
22:01:40   ✅ 收尾: ok
▶ 任务开始: 下面是一段**已定稿**的框架代码，请你把函数 `save_roster` 补全。
22:01:50   [1] 通道=深 候选=1
22:01:51       ⛔ 全部候选被拦 (2 次连续失败)
22:01:52   🧪 收尾交付**未过闸**：1/5 项未过：未定义的名字 'json'（在 top 内被引用，模块里既没有 import 也没有赋值）；另有 1 个未定义名
22:01:53   📦 收尾交付 669 字符（来源=收尾调用）
▶ 任务开始: 下面是一段**已定稿**的框架代码，请你把函数 `week_buckets` 补全。
22:02:01   [1] 通道=深 候选=1
22:02:02       🧪 自检未过（第 1/2 次打回） ⇒ 退回重写：1/5 项未过：自检未通过（不提供细节）
22:02:30   ✅ 收尾: ok
▶ 任务开始: 写一个函数 `week_buckets`，函数签名**必须**是 `def week_buckets(items, path)`
22:03:01   [1] 通道=深 候选=1
22:03:02   ✅ 收尾: ok
"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "run.log"
        p.write_text(fake, encoding="utf-8")
        rep = report(parse(str(p)))
    cases = [
        ("S1 无打回的 bf 版不许被计入触发",
         "p1bf" not in rep["bf_hit"], rep["bf_hit"]),
        ("S2 sk 组触发集必须是三题全中（含**收尾未过闸**那条路径）",
         rep["sk_hit"] == ["p1sk", "p2sk", "p3sk"], rep["sk_hit"]),
        ("S2' p1sk 名字集合必须是 {os:1, re:1}",
         rep["per"]["p1sk"]["names"] == {"os": 1, "re": 1},
         rep["per"]["p1sk"]["names"]),
        ("S3 ⭐ p2sk 的触发来源必须是 `closeout`（收尾），不是 `rewrite`",
         rep["per"]["p2sk"]["closeout"] == 1 and rep["per"]["p2sk"]["rewrite"] == 0,
         {"rewrite": rep["per"]["p2sk"]["rewrite"],
          "closeout": rep["per"]["p2sk"]["closeout"]}),
        ("S3' p2sk 从收尾行里也要取到名字 json",
         rep["per"]["p2sk"]["names"] == {"json": 1},
         rep["per"]["p2sk"]["names"]),
        ("S4 盲报的打回算触发但**不给名字**（p3sk）",
         "p3sk" in rep["sk_hit"] and rep["per"]["p3sk"]["names"] == {},
         rep["per"]["p3sk"]),
        ("S5 同函数名的 bf/sk 没有撞成一个 key",
         "p1bf" in rep["per"] and "p1sk" in rep["per"],
         sorted(rep["per"])),
        ("S6 尝试数按块计数 = 5", rep["attempts"] == 5, rep["attempts"]),
        ("S7 全部 5 块都归因成功（unknown_blocks == 0）",
         rep["unknown_blocks"] == 0, rep["unknown_blocks"]),
        ("S8 两种闸路径分别计数：rewrite=2 / closeout=1",
         (rep["rewrite"], rep["closeout"]) == (2, 1),
         (rep["rewrite"], rep["closeout"])),
    ]

    # ── S9 ⭐ 用**真题面**验证分类（零模型，只读 prompt 字段，不读 oracle）──
    suite = Path(__file__).resolve().parents[2] / "specgen" / "tasks_v6.jsonl"
    if suite.exists():
        got = {}
        for line in suite.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            head = "▶ 任务开始: " + d["prompt"].splitlines()[0]
            g, tid = classify(head)
            got[d["id"]] = tid
        want = {t: t for t in BF + SK}
        ok = got == want
        cases.append((f"S9 真题面首行必须全部归因正确（{len(want)} 题）", ok,
                      [k for k, v in got.items() if v != k] or "全对"))
    else:
        cases.append((f"S9 跳过（{suite} 不存在）", False, "缺 tasks_v6.jsonl"))

    bad = 0
    for why, ok, got_ in cases:
        print(f"  {'✅' if ok else '❌'} {why}  实测={got_}")
        bad += 0 if ok else 1
    print(f"{'✅ 自证 %d/%d' % (len(cases), len(cases)) if bad == 0 else f'❌ {bad} 条自证失败'}")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.log:
        print("缺 --log（或 --selftest）", file=sys.stderr)
        return 2
    rep = report(parse(a.log))
    print(f"  {'题':6s} {'组':4s} {'尝试':>4s} {'触发尝试':>8s} {'退回重写':>8s} "
          f"{'收尾未过闸':>10s} 名字")
    for tid in BF + SK:
        d = rep["per"].get(tid)
        if not d:
            print(f"  {tid:6s} {tid[2:]:4s}    —        —        —          —  （无记录）")
            continue
        print(f"  {tid:6s} {d['group']:4s} {d['attempts']:4d} "
              f"{d['hit_attempts']:8d} {d['rewrite']:8d} {d['closeout']:10d} "
              f"{d['names'] or '—'}")
    other = [t for t in rep["per"] if t not in BF + SK]
    for tid in sorted(other):
        d = rep["per"][tid]
        print(f"  {tid:6s} {'其他':4s} {d['attempts']:4d} "
              f"{d['hit_attempts']:8d} {d['rewrite']:8d} {d['closeout']:10d} "
              f"{d['names'] or '—'}   ← 不属本轮的组")
    print(f"bf 组触发过的题：{rep['bf_n']}/3 {rep['bf_hit']}")
    print(f"sk 组触发过的题：{rep['sk_n']}/3 {rep['sk_hit']}")
    print(f"总尝试 {rep['attempts']} 次 / 退回重写 {rep['rewrite']} 次 / "
          f"收尾未过闸 {rep['closeout']} 次 / 未归因块 {rep['unknown_blocks']}")
    print("  ⚠️ 两条闸路径**必须分开报**：`退回重写` 还有下一次机会，"
          "`收尾未过闸` 是最后一脚。")
    print(f"D0_SK={rep['sk_n']}  D0_BF={rep['bf_n']}  D0_TOTAL={rep['total_hit_n']}")
    if a.json:
        Path(a.json).write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
