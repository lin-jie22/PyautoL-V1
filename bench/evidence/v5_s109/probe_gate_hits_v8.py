#!/usr/bin/env python3
"""打回探针（第 109 轮 / tasks_v8 / easy 档）：数「闸咬合」的机会，两条路径**分开**。

## 为什么必须读日志而不是台账

`answers.jsonl` **只存每次尝试的最终交付**（§76.6.1），一次尝试内部的多次调用
（含被自检打回后的重写）只留下最后一份。拿它当"首答"会**系统性低估**机会，
而低估的幅度恰好等于机制效应。

## 两条路径（§77.6.1，⛔ 不许合并）

| 路径 | 日志标记 | 后果 |
|---|---|---|
| `rewrite` | `🧪 自检未过（第 N/2 次打回）` | 退回重写，**还有下一次** |
| `closeout` | `🧪 收尾交付**未过闸**` | 最后一脚，**没有下一次** |

⚠️ 只抓 `rewrite` 会读出 `ni` 组 **0 次触发** ⇒ 结论会**反向**（§77 自踩第 1 条）。

## 本轮新增（§78.9 的 V13）：「有时间修」的正确操作化

§78 实测：A 臂 16 次 `rewrite` 里 **8 次之后 0 次模型调用**
（`guard:cascade` 在墙钟还剩 130s 时把尝试掐死）。
⇒ 预注册把"有时间修"写成 `rewrite > 0` 是**操作化太弱** ——
那是"闸把答案踢回去了"，不是"模型还有机会改"。

本探针按 `[N] 通道=` 标记数每次打回**之后还有几次模型调用**，打印分布与
`still_open`（`after_calls > 0` 的次数）。**这才是"可修"的操作化定义。**

## 两种口径（原始 / `--strict`）

原始口径「闸报出任何未定义名 = 一次机会」。§77.6.3 与 §78.4.1 两次踩过同一个坑：
`undefined_names` 会把**散文答案**读成代码（CJK 是合法标识符），
一份 12 字符的 episodic 回落 `(语义记忆中无相关条目)` 让 `D0_WI` 凭空 +1。

`--strict` 只把本题 `meta.expect_names` 里的名字算成机会。两个数都报，
⛔ 不用 strict 覆盖原始（判决用原始，strict 只用来解释假阳性来源）。

## 自证（14 条，含 6 条反证）

    S1 无打回的 wi 版不许被计入触发
    S2 ni 组触发集必须恰好是 {e1ni, e3ni}（e2ni 没打回）
    S3 e1ni 名字集合必须是 {re:1}
    S4 ⭐ **反证 S1~S3 的检测器没睡**：**只有** `收尾未过闸`、**没有** `自检未过` 的块
       也必须被算成触发（若只抓 rewrite ⇒ 这里会读出 0）
    S5 同函数名的 wi/ni 不许撞成一个 key
    S6 尝试数按块计数 = 4
    S7 全部块归因成功（unknown_blocks == 0）
    S8 ⭐ 用**真题面**（`tasks_v8.jsonl` 的 `prompt` 首行）验证分组与映射全对
    S9 ⭐ 非 strict 下散文假阳性**确实**被算成触发（否则 S10 是空断言）
    S10 strict 把它剔掉 ⇒ wi 组 0 题
    S11 strict 保留真题机会（`json` 仍算）
    S12 ⭐ **反证 S10**：expect 表为空 ⇒ 全部不命中
    S13 ⭐ V13：`rewrite` 之后还有 3 次调用 ⇒ after_calls == 3（读数对得起日志）
    S14 ⭐ **反证 S13**：`rewrite` 之后 0 次调用 ⇒ after_calls == 0（不是恒正）
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_BLOCK = "▶ 任务开始"
_REWRITE = "自检未过"
_CLOSEOUT = "收尾交付"          # 后面紧跟 `**未过闸**` / `过闸`
_CLOSEOUT_FAIL = "未过闸"
_NAME = re.compile(r"未定义的名字 '([^']+)'")
_FN = re.compile(r"def\s+`?(\w+)`?|函数\s*`(\w+)`")
_CALL = re.compile(r"\[\d+\]\s*通道=")      #: 一次模型调用的开始行（V13 用来数"还剩几次"）
_WI_MARK = "import 已就位"

WI = ("e1wi", "e2wi", "e3wi")
NI = ("e1ni", "e2ni", "e3ni")
_FN2PAIR = {"clean_lines": "e1", "group_names": "e2", "week_of": "e3"}
#: 冻结题集（只读 `id` / `prompt` 首行 / `meta.expect_names`，不读 oracle）
SUITE = Path(__file__).resolve().parents[2] / "specgen" / "tasks_v8.jsonl"


def classify(head: str) -> tuple[str | None, str | None]:
    """从 `▶ 任务开始:` 那一行判 (组, 题 id)。判不出返回 (None, None)。"""
    m = _FN.search(head)
    if not m:
        return None, None
    fn = m.group(1) or m.group(2)
    pair = _FN2PAIR.get(fn)
    if pair is None:
        return None, None
    if _WI_MARK in head:
        return "wi", f"{pair}wi"
    if "已定稿" in head:
        return "ni", f"{pair}ni"
    return None, None


def parse(log_path: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    cur: list[str] | None = None
    for line in Path(log_path).read_text(errors="replace").splitlines():
        if _BLOCK in line:
            cur = [line]
            blocks.append(cur)
        elif cur is not None:
            cur.append(line)
    return blocks


def load_expect(suite: Path) -> dict[str, set[str]]:
    """从题集 `meta` 读「本题缺的那个模块名」，键是 `e{1,2,3}{wi,ni}`。"""
    out: dict[str, set[str]] = {}
    if not suite.exists():
        return out
    for line in suite.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        m = r.get("meta") or {}
        pair, group = m.get("pair"), m.get("group")
        if pair and group:
            out[f"{pair}{group}"] = set(m.get("expect_names") or ())
    return out


def collect(blocks: list[list[str]], expect: dict[str, set[str]] | None = None) -> dict:
    per: dict[str, dict] = {}
    unknown = 0
    after: dict[str, list[int]] = {"rewrite": [], "closeout": []}
    for b in blocks:
        group, tid = classify(b[0])
        if tid is None:
            unknown += 1
            continue
        rewrite = 0
        closeout = 0
        names: list[str] = []
        # 每个模型调用开始行的下标（V13）
        calls = [i for i, line in enumerate(b) if _CALL.search(line)]
        for i, line in enumerate(b):
            kind = None
            if _REWRITE in line:
                kind = "rewrite"
                rewrite += 1
            elif _CLOSEOUT in line and _CLOSEOUT_FAIL in line:
                kind = "closeout"
                closeout += 1
            if kind is not None:
                names.extend(_NAME.findall(line))
                # ⭐ V13：这次打回**之后**还有几次模型调用
                after[kind].append(sum(1 for c in calls if c > i))
        # `strict` 只改「什么算命中」，不改 rewrite/closeout 的原始计数
        hit_names = names if expect is None else [n for n in names if n in expect.get(tid, set())]
        d = per.setdefault(tid, {"group": group, "attempts": 0, "hit_attempts": 0,
                                 "rewrite": 0, "closeout": 0, "names": {},
                                 "hit_names": {}})
        d["attempts"] += 1
        d["rewrite"] += rewrite
        d["closeout"] += closeout
        if hit_names:
            d["hit_attempts"] += 1
        for nm in names:
            d["names"][nm] = d["names"].get(nm, 0) + 1
        for nm in hit_names:
            d["hit_names"][nm] = d["hit_names"].get(nm, 0) + 1
    return {"per": per, "unknown_blocks": unknown, "after": after}


def report(raw: dict) -> dict:
    per = raw["per"]
    wi_hit = [t for t in WI if per.get(t, {}).get("hit_attempts", 0)]
    ni_hit = [t for t in NI if per.get(t, {}).get("hit_attempts", 0)]
    after = raw["after"]
    out = {
        "wi_hit": wi_hit, "wi_n": len(wi_hit),
        "ni_hit": ni_hit, "ni_n": len(ni_hit),
        "total_hit_n": len(wi_hit) + len(ni_hit),
        "attempts": sum(d["attempts"] for d in per.values()),
        "rewrite": sum(d["rewrite"] for d in per.values()),
        "closeout": sum(d["closeout"] for d in per.values()),
        "unknown_blocks": raw["unknown_blocks"],
        "per": per,
    }
    for k in ("rewrite", "closeout"):
        v = after[k]
        out[f"{k}_after"] = {
            "n": len(v),
            "still_open": sum(1 for x in v if x > 0),      #: ⭐ "还有机会改"的次数
            "dist": {str(j): v.count(j) for j in sorted(set(v))},
        }
    return out


def print_report(rep: dict, strict: bool = False) -> None:
    print(f"  {'题':6s} {'组':3s} {'尝试':>4s} {'触发尝试':>8s} {'退回重写':>8s} "
          f"{'收尾未过闸':>10s} 名字（原始） / 计入机会的名字")
    for tid in WI + NI:
        d = rep["per"].get(tid)
        if not d:
            print(f"  {tid:6s} {tid[2:]:3s}    —        —        —          —  （无记录）")
            continue
        hit = d.get("hit_names") if strict else d["names"]
        print(f"  {tid:6s} {d['group']:3s} {d['attempts']:4d} "
              f"{d['hit_attempts']:8d} {d['rewrite']:8d} {d['closeout']:10d} "
              f"{d['names'] or '—'} / {hit or '—'}")
    print(f"wi 组触发过的题：{rep['wi_n']}/3 {rep['wi_hit']}")
    print(f"ni 组触发过的题：{rep['ni_n']}/3 {rep['ni_hit']}")
    print(f"总尝试 {rep['attempts']} 次 / 退回重写 {rep['rewrite']} 次 / "
          f"收尾未过闸 {rep['closeout']} 次 / 未归因块 {rep['unknown_blocks']}")
    print("  ⚠️ 两条闸路径**必须分开报**：`退回重写` 还有下一次机会，"
          "`收尾未过闸` 是最后一脚。")
    print("\n  ⭐ V13「打回之后还有几次模型调用」（= 「有时间修」的操作化定义）：")
    for k, label in (("rewrite", "退回重写"), ("closeout", "收尾未过闸")):
        s = rep[f"{k}_after"]
        if not s["n"]:
            print(f"    {label}: 0 次")
            continue
        rate = s["still_open"] / s["n"]
        print(f"    {label}: {s['n']} 次 → 之后仍有 ≥1 次调用 "
              f"{s['still_open']} 次（{rate:.0%}）  分布 {s['dist']}")
    print("    ⛔ 「`rewrite` 存在」≠「还有下一次调用」—— 只有 `still_open` 才算「有时间修」")
    if strict:
        print("\n  ⚠️ strict：只有本题 `meta.expect_names` 里的名字才算机会"
              "（剔掉散文答案的假阳性）。")
    print(f"D0_NI={rep['ni_n']}  D0_WI={rep['wi_n']}  D0_TOTAL={rep['total_hit_n']}")


# ===========================================================================
# 自证（14 条）
# ===========================================================================
def selftest() -> int:
    fake = """▶ 任务开始: 下面是一段**已定稿**的框架代码，import 已就位，请你把函数 `clean_lines` 补全。
00:00:01   [1] 通道=深 候选=1
00:00:02   ✅ 收尾: ok
▶ 任务开始: 下面是一段**已定稿**的框架代码，请你把函数 `clean_lines` 补全。
00:01:01   [1] 通道=深 候选=1
00:01:02       🧪 自检未过（第 1/2 次打回） ⇒ 退回重写：2/5 项未过：未定义的名字 're'（在 _strip_tags 内被引用，模块里既没有 import 也没有赋值）
00:01:40   ✅ 收尾: ok
▶ 任务开始: 下面是一段**已定稿**的框架代码，请你把函数 `group_names` 补全。
00:02:01   [1] 通道=深 候选=1
00:02:02   ✅ 收尾: ok
▶ 任务开始: 下面是一段**已定稿**的框架代码，请你把函数 `week_of` 补全。
00:03:01   [1] 通道=深 候选=1
00:03:20   ⛔ 全部候选被拦 (2 次连续失败)
00:03:30   ⏱ 触发收尾余量 ⇒ 转向交付（探索到此为止）
00:03:40   🧪 收尾交付**未过闸**：1/5 项未过：未定义的名字 'datetime'（在 week_of 内被引用）
00:03:41   📦 收尾交付 210 字符
"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "run.log"
        p.write_text(fake, encoding="utf-8")
        rep = report(collect(parse(str(p))))
    cases = [
        ("S1 无打回的 wi 版不许被计入触发",
         "e1wi" not in rep["wi_hit"], rep["wi_hit"]),
        ("S2 ni 组触发集必须恰好是 {e1ni, e3ni}（e2ni 没打回）",
         rep["ni_hit"] == ["e1ni", "e3ni"], rep["ni_hit"]),
        ("S3 e1ni 名字集合必须是 {re:1}",
         rep["per"]["e1ni"]["names"] == {"re": 1}, rep["per"]["e1ni"]["names"]),
        ("S4 ⭐ 只有收尾未过闸、没有自检未过的块也必须算触发（检测器没睡）",
         "e3ni" in rep["ni_hit"] and rep["per"]["e3ni"]["closeout"] == 1
         and rep["per"]["e3ni"]["rewrite"] == 0, rep["per"]["e3ni"]),
        ("S5 同函数名的 wi/ni 没撞成一个 key",
         "e1wi" in rep["per"] and "e1ni" in rep["per"], sorted(rep["per"])),
        ("S6 尝试数按块计数 = 4", rep["attempts"] == 4, rep["attempts"]),
        ("S7 全部 4 块都归因成功（unknown_blocks == 0）",
         rep["unknown_blocks"] == 0, rep["unknown_blocks"]),
    ]

    # S8 ⭐ 用真题面验证分组（零模型，只读 prompt 字段，不读 oracle）
    suite = SUITE
    if suite.exists():
        want = {}
        for line in suite.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            want[r["id"]] = r["prompt"].splitlines()[0]
        got = {}
        for tid, head in want.items():
            _g, t = classify("▶ 任务开始: " + head)
            got[tid] = t
        ok = all(got.get(k) == k for k in want)
        cases.append((f"S8 真题面首行必须全部归因正确（{len(want)} 题）", ok,
                      [k for k, v in got.items() if v != k] or "全对"))
    else:
        cases.append((f"S8 跳过（{suite} 不存在）", False, "缺 tasks_v8.jsonl"))

    # ---- strict 读数（S9~S12）：散文答案的假阳性必须被剔掉 -----------------
    fake2 = """▶ 任务开始: 下面是一段**已定稿**的框架代码，import 已就位，请你把函数 `clean_lines` 补全。
00:00:01   [1] 通道=深 候选=1
00:00:01   🧪 收尾交付**未过闸**：2/5 项未过：缺少 def clean_lines(...)；未定义的名字 '语义记忆中无相关条目'（在 top 内被引用）
▶ 任务开始: 下面是一段**已定稿**的框架代码，请你把函数 `group_names` 补全。
00:01:01   [1] 通道=深 候选=1
00:01:02       🧪 自检未过（第 1/2 次打回） ⇒ 退回重写：1/5 项未过：未定义的名字 'json'（在 _dump 内被引用）
"""
    with tempfile.TemporaryDirectory() as td:
        p2 = Path(td) / "run2.log"
        p2.write_text(fake2, encoding="utf-8")
        raw = report(collect(parse(str(p2))))
        exp = {"e1wi": {"re"}, "e1ni": {"re"}, "e2ni": {"json"}, "e3ni": {"datetime"}}
        strict = report(collect(parse(str(p2)), expect=exp))
        empty = report(collect(parse(str(p2)), expect={}))
    cases += [
        ("S9 ⭐ 非 strict 下 e1wi 的散文假阳性**确实**被算成触发（S10 才有意义）",
         raw["wi_hit"] == ["e1wi"], raw["wi_hit"]),
        ("S10 ⭐ strict 把散文假阳性剔掉：e1wi 不再算触发",
         strict["wi_hit"] == [], strict["wi_hit"]),
        ("S11 strict 保留真题机会：e2ni 的 `json` 仍算触发",
         strict["ni_hit"] == ["e2ni"], strict["ni_hit"]),
        ("S12 ⭐ 反证：expect 为空表 ⇒ 全部不命中（证明 S10 不是恒真）",
         empty["ni_hit"] == [] and empty["wi_hit"] == [], empty["ni_hit"]),
    ]

    # ---- V13 读数（S13~S14）：打回之后还剩几次模型调用 ----------------------
    fake3 = """▶ 任务开始: 下面是一段**已定稿**的框架代码，请你把函数 `week_of` 补全。
00:00:01   [1] 通道=深 候选=1
00:00:02       🧪 自检未过（第 1/2 次打回） ⇒ 退回重写：未定义的名字 'datetime'
00:00:30   [2] 通道=深 候选=1
00:01:00   [3] 通道=深 候选=1
00:02:00   [4] 通道=深 候选=1
00:02:01   ✅ 收尾: ok
▶ 任务开始: 下面是一段**已定稿**的框架代码，请你把函数 `group_names` 补全。
00:03:01   [1] 通道=深 候选=1
00:03:02       🧪 自检未过（第 1/2 次打回） ⇒ 退回重写：未定义的名字 'json'
00:03:03   ⛔ 全部候选被拦 (3 次连续失败)
"""
    with tempfile.TemporaryDirectory() as td:
        p3 = Path(td) / "run3.log"
        p3.write_text(fake3, encoding="utf-8")
        rep3 = report(collect(parse(str(p3))))
    dist3 = rep3["rewrite_after"]
    # ⚠️ 期望**按构造推**，不凭印象：fake3 有两块 ——
    #    week_of      ：rewrite 之后有 [2][3][4] ⇒ 3
    #    group_names  ：rewrite 之后无调用   ⇒ 0
    #    ⇒ dist = {"3": 1, "0": 1}，still_open = 1（只有一块还有机会）
    cases += [
        ("S13 ⭐ V13：rewrite 之后有 3 次调用的块 ⇒ dist 里出现 3；still_open == 1",
         dist3["n"] == 2 and dist3["still_open"] == 1 and dist3["dist"] == {"0": 1, "3": 1},
         dist3),
        ("S14 ⭐ 反证 S13：rewrite 之后 0 次调用的块必须记成 0（不是恒正）",
         dist3["dist"].get("0") == 1, dist3["dist"]),
    ]

    bad = 0
    for why, ok, got in cases:
        print(f"  {'✅' if ok else '❌'} {why}  实测={got}")
        bad += 0 if ok else 1
    print(f"{'✅ 自证 %d/%d' % (len(cases), len(cases)) if bad == 0 else f'❌ {bad} 条自证失败'}")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log")
    ap.add_argument("--json")
    ap.add_argument("--strict", action="store_true",
                    help="只把本题 meta.expect_names 里的名字算成机会（剔散文假阳性）")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.log:
        print("缺 --log（或 --selftest）")
        return 2
    rep = report(collect(parse(a.log), expect=load_expect(SUITE) if a.strict else None))
    print_report(rep, strict=a.strict)
    if a.json:
        Path(a.json).write_text(
            json.dumps({k: v for k, v in rep.items() if k != "per"}, ensure_ascii=False,
                       indent=1) + "\n" + json.dumps({"per": rep["per"]},
                                                     ensure_ascii=False, indent=1),
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
