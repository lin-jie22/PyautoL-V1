#!/usr/bin/env python3
"""打回探针（第 108 轮 / tasks_v7）：数「闸咬合」的机会，两条路径**分开**。

## 为什么必须读日志而不是台账

`answers.jsonl` **只存每次尝试的最终交付**（§76.6.1），一次尝试内部的多次调用
（含被自检打回后的重写）只留下最后一份。拿它当"首答"会**系统性低估**机会，
而低估的幅度恰好等于机制效应。

日志在机制**触发的那一刻**独立记账，所以机会一律读日志。

## 两条路径（§77.6.1，⛔ 不许合并）

| 路径 | 日志标记 | 后果 |
|---|---|---|
| `rewrite` | `🧪 自检未过（第 N/2 次打回）` | 退回重写，**还有下一次** |
| `closeout` | `🧪 收尾交付**未过闸**` | 最后一脚，**没有下一次** |

⚠️ 只抓 `rewrite` 会读出 `ni` 组 **0 次触发** ⇒ 结论会**反向**（§77 自踩第 1 条）。

## 分组（`wi` / `ni`）

`▶ 任务开始:` 后面只印 `goal[:120]`（首行 + 一点），所以分组只能看首行里有没有
`import 已就位`：

    wi 首行：下面是一段**已定稿**的框架代码，import 已就位，请你把函数 `tag_report` 补全。
    ni 首行：下面是一段**已定稿**的框架代码，请你把函数 `tag_report` 补全。

配对映射靠函数名（`tag_report`→p1 / `save_roster`→p2 / `week_buckets`→p3）。

## 两种口径（原始 / `--strict`）

原始口径「闸报出任何未定义名 = 一次机会」。第 108 轮第一次跑完就发现它会把**散文答案**
算成机会：`wi` 组的 `p1wi` 收尾回落成 12 字符的 `(语义记忆中无相关条目)`，
`undefined_names` 把那个中文串读成「未定义的名字 '语义记忆中无相关条目'」（CJK 是合法标识符）
⇒ `D0_WI` 从 0 变 1，而 `wi` 组的骨架**本来就有 import**，结构上不可能有 import 机会。

`--strict` 只把本题 `meta.expect_names` 里的名字算成机会。两个数都报，⛔ 不用 strict 覆盖原始。

## 自证（12 条，含 5 条反证）

    S1 无打回的 wi 版不许被计入触发
    S2 ni 组触发集必须恰好是 {p1ni, p3ni}（p2ni 没打回）
    S3 p1ni 名字集合必须是 {re:1}
    S4 ⭐ **反证 S1~S3 的检测器没睡**：**只有** `收尾未过闸`、**没有** `自检未过` 的块
       也必须被算成触发（若只抓 rewrite ⇒ 这里会读出 0）
    S5 同函数名的 wi/ni 不许撞成一个 key
    S6 尝试数按块计数 = 4
    S7 ⭐ 用**真题面**（`tasks_v7.jsonl` 的 `prompt` 首行）验证分组与映射全对
    S9 ⭐ 非 strict 下 p1wi 的散文假阳性**确实**被算成触发（否则 S10 是空断言）
    S10 strict 把它剔掉 ⇒ wi 组 0 题
    S11 strict 保留真题机会（`json` 仍算）
    S12 ⭐ **反证 S10**：expect 表为空 ⇒ 全部不命中（证明 S10 不是恒真）
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
_WI_MARK = "import 已就位"

WI = ("p1wi", "p2wi", "p3wi")
NI = ("p1ni", "p2ni", "p3ni")
_FN2PAIR = {"tag_report": "p1", "save_roster": "p2", "week_buckets": "p3"}
#: 冻结题集（只读 `id` / `prompt` 首行 / `meta.expect_names`，不读 oracle）
SUITE = Path(__file__).resolve().parents[2] / "specgen" / "tasks_v7.jsonl"


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
    """从题集 `meta` 读「本题缺的那个模块名」，键是 `p{1,2,3}{wi,ni}`。

    ⭐ 存在的理由（第 108 轮实测，见 RESULTS §3.3）：`undefined_names` 对**散文答案**
    也会报名字 —— 一份 12 字符的 episodic 回落 `(语义记忆中无相关条目)` 被读成
    「未定义的名字 '语义记忆中无相关条目'」（CJK 是合法标识符），于是 `wi` 组凭空
    多出一题"触发"。那不是 **import 机会**，是假阳性。

    `--strict` 只把「本题 `expect_names` 里的名字」算成机会。
    """
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
    for b in blocks:
        group, tid = classify(b[0])
        if tid is None:
            unknown += 1
            continue
        rewrite = 0
        closeout = 0
        names: list[str] = []
        for line in b:
            if _REWRITE in line:
                rewrite += 1
                names.extend(_NAME.findall(line))
            elif _CLOSEOUT in line and _CLOSEOUT_FAIL in line:
                closeout += 1
                names.extend(_NAME.findall(line))
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
    return {"per": per, "unknown_blocks": unknown}


def report(raw: dict) -> dict:
    per = raw["per"]
    wi_hit = [t for t in WI if per.get(t, {}).get("hit_attempts", 0)]
    ni_hit = [t for t in NI if per.get(t, {}).get("hit_attempts", 0)]
    return {
        "wi_hit": wi_hit, "wi_n": len(wi_hit),
        "ni_hit": ni_hit, "ni_n": len(ni_hit),
        "total_hit_n": len(wi_hit) + len(ni_hit),
        "attempts": sum(d["attempts"] for d in per.values()),
        "rewrite": sum(d["rewrite"] for d in per.values()),
        "closeout": sum(d["closeout"] for d in per.values()),
        "unknown_blocks": raw["unknown_blocks"],
        "per": per,
    }


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
    if strict:
        print("  ⚠️ strict：只有本题 `meta.expect_names` 里的名字才算机会"
              "（剔掉散文答案的假阳性）。")
    print(f"D0_NI={rep['ni_n']}  D0_WI={rep['wi_n']}  D0_TOTAL={rep['total_hit_n']}")


# ===========================================================================
# 自证（7 条）
# ===========================================================================
def selftest() -> int:
    fake = """▶ 任务开始: 下面是一段**已定稿**的框架代码，import 已就位，请你把函数 `tag_report` 补全。
00:00:01   [1] 通道=深 候选=1
00:00:02   ✅ 收尾: ok
▶ 任务开始: 下面是一段**已定稿**的框架代码，请你把函数 `tag_report` 补全。
00:01:01   [1] 通道=深 候选=1
00:01:02       🧪 自检未过（第 1/2 次打回） ⇒ 退回重写：2/5 项未过：未定义的名字 're'（在 _fold 内被引用，模块里既没有 import 也没有赋值）
00:01:40   ✅ 收尾: ok
▶ 任务开始: 下面是一段**已定稿**的框架代码，请你把函数 `save_roster` 补全。
00:02:01   [1] 通道=深 候选=1
00:02:02   ✅ 收尾: ok
▶ 任务开始: 下面是一段**已定稿**的框架代码，请你把函数 `week_buckets` 补全。
00:03:01   [1] 通道=深 候选=1
00:03:20   ⛔ 全部候选被拦 (2 次连续失败)
00:03:30   ⏱ 触发收尾余量 ⇒ 转向交付（探索到此为止）
00:03:40   🧪 收尾交付**未过闸**：1/5 项未过：未定义的名字 'datetime'（在 week_buckets 内被引用）
00:03:41   📦 收尾交付 210 字符
"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "run.log"
        p.write_text(fake, encoding="utf-8")
        rep = report(collect(parse(str(p))))
    cases = [
        ("S1 无打回的 wi 版不许被计入触发",
         "p1wi" not in rep["wi_hit"], rep["wi_hit"]),
        ("S2 ni 组触发集必须恰好是 {p1ni, p3ni}（p2ni 没打回）",
         rep["ni_hit"] == ["p1ni", "p3ni"], rep["ni_hit"]),
        ("S3 p1ni 名字集合必须是 {re:1}",
         rep["per"]["p1ni"]["names"] == {"re": 1}, rep["per"]["p1ni"]["names"]),
        ("S4 ⭐ 只有收尾未过闸、没有自检未过的块也必须算触发（检测器没睡）",
         "p3ni" in rep["ni_hit"] and rep["per"]["p3ni"]["closeout"] == 1
         and rep["per"]["p3ni"]["rewrite"] == 0, rep["per"]["p3ni"]),
        ("S5 同函数名的 wi/ni 没撞成一个 key",
         "p1wi" in rep["per"] and "p1ni" in rep["per"], sorted(rep["per"])),
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
            g, t = classify("▶ 任务开始: " + head)
            got[tid] = t
        ok = all(got.get(k) == k for k in want)
        cases.append((f"S8 真题面首行必须全部归因正确（{len(want)} 题）", ok,
                      [k for k, v in got.items() if v != k] or "全对"))
    else:
        cases.append((f"S8 跳过（{suite} 不存在）", False, "缺 tasks_v7.jsonl"))

    # ---- strict 读数（S9~S12）：散文答案的假阳性必须被剔掉 -----------------
    fake2 = """▶ 任务开始: 下面是一段**已定稿**的框架代码，import 已就位，请你把函数 `tag_report` 补全。
00:00:01   🧪 收尾交付**未过闸**：2/5 项未过：缺少 def tag_report(...)；未定义的名字 '语义记忆中无相关条目'（在 top 内被引用）
▶ 任务开始: 下面是一段**已定稿**的框架代码，请你把函数 `save_roster` 补全。
00:01:01   🧪 自检未过（第 1/2 次打回） ⇒ 退回重写：1/5 项未过：未定义的名字 'json'（在 _dump 内被引用）
"""
    with tempfile.TemporaryDirectory() as td:
        p2 = Path(td) / "run2.log"
        p2.write_text(fake2, encoding="utf-8")
        raw = report(collect(parse(str(p2))))
        exp = {"p1wi": {"re"}, "p1ni": {"re"}, "p2ni": {"json"}, "p3ni": {"datetime"}}
        strict = report(collect(parse(str(p2)), expect=exp))
        empty = report(collect(parse(str(p2)), expect={}))
    cases += [
        ("S9 ⭐ 非 strict 下 p1wi 的散文假阳性**确实**被算成触发（S10 才有意义）",
         raw["wi_hit"] == ["p1wi"], raw["wi_hit"]),
        ("S10 ⭐ strict 把散文假阳性剔掉：p1wi 不再算触发",
         strict["wi_hit"] == [], strict["wi_hit"]),
        ("S11 strict 保留真题机会：p2ni 的 `json` 仍算触发",
         strict["ni_hit"] == ["p2ni"], strict["ni_hit"]),
        ("S12 ⭐ 反证：expect 为空表 ⇒ 全部不命中（证明 S10 不是恒真）",
         empty["ni_hit"] == [] and empty["wi_hit"] == [], empty["ni_hit"]),
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
