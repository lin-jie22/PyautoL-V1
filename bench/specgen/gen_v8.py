#!/usr/bin/env python3
"""v8 尺子（第 109 轮 · V10）：把难度降到模型够得着，其余一切与 v7 保持一致。

────────────────────────────────────────────────────────────────────────────
为什么要有这把尺子
────────────────────────────────────────────────────────────────────────────
§78 的 `P0` 前提闸第一次咬合：`M_wi(A) = 1/16 < 12/16`
⇒ 按预注册，`P1~P4` 一律 `None`，整轮**没有测出任何 Δ**。
§78.6.2 登记了 `P0` 红的两个成因**不可分离**：① 题偏难 ② `max_tokens=1024` 掐断。

V10 只动**难度**这一个自变量，装置一字不改（`max_tokens` 仍 1024 / 双 1 retry /
`wall 240` / 端口 8812），把成因①单独摘出来：
若 v8 的 `M_wi ≥ 12/16` ⇒ 难度是主因（且 v7 的 `Δ` 从此可以解释）；
若 v8 的 `M_wi` 仍 < 12/16 ⇒ 不是难度问题，转 V11（预算两臂同抬）。

────────────────────────────────────────────────────────────────────────────
降难度**只允许**从这三条通道走（每条都在自检里可量化，S17/S18/S19）
────────────────────────────────────────────────────────────────────────────
1. **规则条数** 8~9 → **≤ 4**（S17）。
   删掉的都是"写了也不计分"或"只影响字节"的规则 —— 见第 2 条。
2. **判分从字节级降到逐行 / 解析级**（S18）。
   v7 断言 `read_bytes() == want`：尾换行、`ensure_ascii`、分隔符、BOM 全在计分。
   v8 断言 `read_text().splitlines() == want_list`（e1/e3）或
   `json.loads(read_text()) == want_dict`（e2）：格式自由度不计分。
   ⚠️ 由此**必须**同步删掉不可测的规则：e2 的"组名 ASCII 升序"在 dict 比较下不计分，
   e3 的"桶按周一升序"同理 ⇒ 两条都删掉。**留着一条不计分的规则 = 骗模型多做事。**
3. **易错的 API 用法搬进骨架**（S19：oracle 主函数体行数 13/16/20 → ≤ 14）。
   v7 的 `p3wi` 死在 `AttributeError: 'list' object has no attribute 'strip'`
   （把 `row.split("|")[0]` 的结果当字符串），`p2wi` 死在没逐项 `strip`。
   v8 把 `_split` / `_strip_tags` / `_read_day` / `_monday_of` / `_add` **写成已定稿代码**，
   TODO 段只剩"遍历 + 跳过 + 归桶"。

⛔ **不许**从这些通道降难度：改 `DIRECT_SYSTEM`、把答案写进题面、把 TODO 段的代码给全、
   给示例调用、放宽断言（除了上面写明的"字节级 → 逐行/解析级"）。

────────────────────────────────────────────────────────────────────────────
与 v7 完全相同的结构性质（一条都不许松）
────────────────────────────────────────────────────────────────────────────
- 三对题 × `{wi, ni}`，**只差骨架顶部一行** `import <m>\\n\\n`（S13 逐字节）。
- `ni` 骨架**零** import（S8）、语法正确（S9）、恰好缺**一个**名字且它使满分变 0（S10/S11）。
- 配对两版的 tests / oracle / partial / null / spec_impl / artifact **逐字节相同**（S12）。
- `ni` 题面**允许**加 import、无禁止字样（S14）。
- 交付物写在 cwd 根，不含路径分隔符（S15）。
- 题面字数比 ≤ 1.30（S16）。

⛔ `tasks_v8.jsonl` 一旦 `--emit` 就冻结。换 `suite_sha` ⇒ 与 v7 的读数**不可比**。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "bench" / "specgen") not in sys.path:
    sys.path.insert(0, str(ROOT / "bench" / "specgen"))

#: 判分器与 S11 的反证工具与题目无关，**借用 v7 的**（不复制，避免两份实现漂移）。
#: S20 把 `gen_v7.py` 的 sha 钉住：那边一改，这里立刻红。
from gen_v7 import score, strip_binding          # noqa: E402
from mw.bench import Suite                       # noqa: E402
from mw.verify import undefined_names            # noqa: E402

SEED = 20261031          #: ⚠️ 与 v7（20261030）不同 —— 但用例其实由 `pair` 名字决定，见下。
CASES = 16               #: 每题断言条数（= 每格题的分母）
GROUPS = ("wi", "ni")    #: wi = 骨架顶部**有** import；ni = **没有**

_V7_SHA16 = "56453220546ebdce"   #: S20 用。改 `gen_v7.py` ⇒ 这里必须同步改并登记（否则 v7/v8 分数不可比）

#: S21 用：**装置真打印的那个** `Suite.sha()`（进 `caliber.sig` 的那个口径）。
#: ⛔ 第 109 轮踩过：PREREG §1.1 登记成 `sha256(json.dumps(rows, sort_keys=True))[:16]`
#: = `aece03f6dec738fc` —— 那是**另一个函数**的输出，同一份文件两个数（见 PREREG §12）。
_SUITE_SHA16 = "519dad774a366461"
_SUITE_FILE = ROOT / "bench" / "specgen" / "tasks_v8.jsonl"


# ===========================================================================
# 规格
# ===========================================================================
@dataclass(frozen=True)
class Spec:
    id: str
    pair: str
    fn: str
    group: str                   #: "wi" / "ni"
    body: tuple[str, ...]        #: 规则（S17 断言 ≤ 4 条）
    gen: Callable[[random.Random], Any]
    expect: Callable[[Any], Any]  #: ⚠️ 返回**可放进 test 源码的比较值**（list / dict），不再是 bytes
    oracle: str
    spec_impl: str
    partial: str
    artifact: str                #: **cwd 根**下的文件名（S15 钉住：不许含 `/`）
    skeleton_body: str = ""      #: 共同骨架（**零 import**）
    expect_names: tuple[str, ...] = ()   #: `ni` 骨架缺的名字（S10 拿它做反证）


def skeleton_of(sp: Spec) -> str:
    """`wi` = 一行 import + 共同骨架；`ni` = 共同骨架。S13 钉住这个等式。"""
    if sp.group == "wi":
        return "".join(f"import {nm}\n" for nm in sp.expect_names) + "\n" + sp.skeleton_body
    return sp.skeleton_body


def body_lines(src: str, fn: str) -> int:
    """主函数体的非空行数 —— S19 的降难度读数（机械可数，不看感觉）。"""
    lines = src.splitlines()
    try:
        i = next(k for k, l in enumerate(lines) if l.startswith(f"def {fn}("))
    except StopIteration:
        raise AssertionError(f"❌ 找不到 def {fn}(")
    return len([l for l in lines[i + 1:] if l.strip()])


# ===========================================================================
# 输入生成（与 v7 同一族数据，难度与输入无关）
# ===========================================================================
_W = ["alpha", "beta", "gamma", "delta", "eps", "zeta", "eta", "theta"]
_CN = ["红", "蓝", "绿", "紫"]
_WS = ["  ", "\t", " ", "\t\t"]


def _gen_e1(rng: random.Random) -> list[str]:
    items: list[str] = []
    for _ in range(rng.randint(3, 6)):
        words = [rng.choice(_W) for _ in range(rng.randint(0, 3))]
        for _ in range(rng.randint(0, 2)):
            words.insert(rng.randint(0, len(words)),
                         "@" + "".join(rng.choice("abz019_")
                                       for _ in range(rng.randint(1, 3))))
        sep = rng.choice(_WS)
        line = sep.join(words) + (rng.choice(_WS) if rng.random() < 0.5 else "")
        if rng.random() < 0.15:
            line += " @"
        if rng.random() < 0.1:
            line = "@ " + line
        items.append(line)
    if len(items) >= 3 and rng.random() < 0.7:
        items.insert(0, items[-1])
    return items


def _gen_e2(rng: random.Random) -> list[str]:
    items: list[str] = []
    groups = _W[:rng.randint(2, 4)]
    for _ in range(rng.randint(4, 8)):
        g = rng.choice(groups)
        name = rng.choice(_W + _CN)
        pad = rng.choice(["", " "])
        roll = rng.random()
        if roll < 0.08:
            items.append(name)
        elif roll < 0.16:
            items.append(f"{g}|")
        elif roll < 0.22:
            items.append("|" + name)
        else:
            items.append(f"{pad}{g}{pad}|{pad}{name}{pad}")
    if rng.random() < 0.5:
        items.append(items[0])
    return items


def _gen_e3(rng: random.Random) -> list[str]:
    base = ["2026-03-02", "2026-03-05", "2026-03-08", "2026-03-09", "2026-03-15",
            "2026-02-23", "2026-03-01", "2026-04-01", "2026-03-07", "2026-03-16"]
    items: list[str] = []
    for _ in range(rng.randint(4, 8)):
        items.append(f"{rng.choice(base)}|{rng.choice(_W + _CN)}")
    if rng.random() < 0.25:
        items.append("2026-02-30|坏日期")
    if rng.random() < 0.25:
        items.append(rng.choice(base))
    if rng.random() < 0.25:
        items.append("2026-03-04|")
    if rng.random() < 0.5:
        items.append(items[0])
    return items


# ── 期望值（手工原语，不调 re / json / datetime）─────────────────────────
def _strip_at(s: str) -> str:
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "@" and i + 1 < n and s[i + 1].isascii() and s[i + 1].isalnum():
            j = i + 1
            while j < n and s[j].isascii() and s[j].isalnum():
                j += 1
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _expect_e1(items: list[str]) -> list[str]:
    out: list[str] = []
    for raw in items:
        s = _strip_at(raw).strip(" \t")
        if not s or s in out:
            continue
        out.append(s)
    return out


def _expect_e2(items: list[str]) -> dict:
    table: dict[str, list[str]] = {}
    for raw in items:
        if "|" not in raw:
            continue
        head, _, name = raw.partition("|")
        head, name = head.strip(), name.strip()
        if not head or not name:
            continue
        bucket = table.setdefault(head, [])
        if name not in bucket:
            bucket.append(name)
    return {"groups": {k: table[k] for k in sorted(table)}}


def _expect_e3(items: list[str]) -> dict:
    import datetime as _dt

    buckets: dict[str, list[str]] = {}
    for raw in items:
        if "|" not in raw:
            continue
        head, _, ev = raw.partition("|")
        head, ev = head.strip(), ev.strip()
        if not head or not ev:
            continue
        try:
            d = _dt.date.fromisoformat(head)
        except ValueError:
            continue
        key = _dt.date.fromordinal(d.toordinal() - d.weekday()).isoformat()
        bucket = buckets.setdefault(key, [])
        if ev not in bucket:
            bucket.append(ev)
    return {k: buckets[k] for k in sorted(buckets)}


# ===========================================================================
# 三对题（规则 ≤ 4 条；易错 API 已在骨架里）
# ===========================================================================
_ORACLE_E1 = '''import re


def _strip_tags(text):
    return re.sub(r"@[A-Za-z0-9]+", "", text)


def clean_lines(items, path):
    kept = []
    for raw in items:
        s = _strip_tags(raw).strip(" \\t")
        if s and s not in kept:
            kept.append(s)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(s + "\\n" for s in kept))
    return None
'''

_SPEC_E1 = '''import re


def _strip_tags(text):
    return re.sub("@[A-Za-z0-9]+", "", text)


def clean_lines(items, path):
    kept = []
    for raw in items:
        text = _strip_tags(raw)
        text = text.strip(" \\t")
        if text == "":
            continue
        if text in kept:
            continue
        kept.append(text)

    handle = open(path, "w", encoding="utf-8")
    try:
        for one in kept:
            handle.write(one + "\\n")
    finally:
        handle.close()
    return None
'''

_PARTIAL_E1 = _ORACLE_E1.replace(
    "        if s and s not in kept:\n            kept.append(s)\n",
    "        if s:\n            kept.append(s)\n")

#: ⚠️ 缺的名字 `re` 只在**已经写好的** `_strip_tags` 里 —— TODO 段本身不碰 re。
_SKELETON_E1 = '''def _strip_tags(text):
    return re.sub(r"@[A-Za-z0-9]+", "", text)


def clean_lines(items, path):
    kept = []
    # >>> BEGIN TODO
    # 遍历 items，按规则 1~3 生成 kept（处理后的字符串列表）。
    # _strip_tags(raw) 删标记；去首尾空白用 .strip(" \\t")。
    # >>> END TODO

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(s + "\\n" for s in kept))
    return None
'''


_ORACLE_E2 = '''import json


def _dump(payload):
    return json.dumps(payload, ensure_ascii=False)


def _split(raw):
    head, sep, name = raw.partition("|")
    if not sep:
        return None
    head, name = head.strip(), name.strip()
    if not head or not name:
        return None
    return head, name


def _add(table, head, name):
    bucket = table.setdefault(head, [])
    if name not in bucket:
        bucket.append(name)


def group_names(items, path):
    table = {}
    for raw in items:
        pair = _split(raw)
        if pair is not None:
            _add(table, pair[0], pair[1])
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_dump({"groups": table}))
    return None
'''

_SPEC_E2 = '''import json


def _split(raw):
    if "|" not in raw:
        return None
    left, right = raw.split("|", 1)
    left = left.strip()
    right = right.strip()
    if left == "" or right == "":
        return None
    return (left, right)


def group_names(items, path):
    acc = {}
    for row in items:
        pair = _split(row)
        if pair is None:
            continue
        key, name = pair
        acc.setdefault(key, [])
        if name not in acc[key]:
            acc[key].append(name)

    handle = open(path, "w", encoding="utf-8")
    try:
        handle.write(json.dumps({"groups": acc}, ensure_ascii=False))
    finally:
        handle.close()
    return None
'''

_PARTIAL_E2 = _ORACLE_E2.replace(
    "    bucket = table.setdefault(head, [])\n    if name not in bucket:\n        bucket.append(name)\n",
    "    table.setdefault(head, []).append(name)\n")

#: ⚠️ 缺的名字 `json` 只在已经写好的 `_dump` 里。
_SKELETON_E2 = '''def _dump(payload):
    return json.dumps(payload, ensure_ascii=False)


def _split(raw):
    head, sep, name = raw.partition("|")
    if not sep:
        return None
    head, name = head.strip(), name.strip()
    if not head or not name:
        return None
    return head, name


def _add(table, head, name):
    bucket = table.setdefault(head, [])
    if name not in bucket:
        bucket.append(name)


def group_names(items, path):
    table = {}
    # >>> BEGIN TODO
    # 遍历 items：pair = _split(raw)（切分 / strip / 空判都已做好，
    # 空项返回 None），非 None 时用 _add(table, pair[0], pair[1]) 归组。
    # >>> END TODO

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_dump({"groups": table}))
    return None
'''


_ORACLE_E3 = '''import datetime


def _split(raw):
    head, sep, event = raw.partition("|")
    if not sep:
        return None
    head, event = head.strip(), event.strip()
    if not head or not event:
        return None
    return head, event


def _read_day(stamp):
    return datetime.date.fromisoformat(stamp)


def _monday_of(day):
    return (day - datetime.timedelta(days=day.weekday())).isoformat()


def _add(table, key, value):
    bucket = table.setdefault(key, [])
    if value not in bucket:
        bucket.append(value)


def week_of(items, path):
    buckets = {}
    for raw in items:
        pair = _split(raw)
        if pair is None:
            continue
        try:
            key = _monday_of(_read_day(pair[0]))
        except ValueError:
            continue
        _add(buckets, key, pair[1])
    with open(path, "w", encoding="utf-8") as fh:
        for key in sorted(buckets):
            fh.write(key + "|" + ",".join(buckets[key]) + "\\n")
    return None
'''

_SPEC_E3 = '''import datetime


def _split(raw):
    parts = raw.split("|")
    if len(parts) < 2:
        return None
    stamp = parts[0].strip()
    label = parts[1].strip()
    if stamp == "" or label == "":
        return None
    return (stamp, label)


def week_of(items, path):
    acc = {}
    for row in items:
        pair = _split(row)
        if pair is None:
            continue
        try:
            day = datetime.date(*[int(x) for x in pair[0].split("-")])
        except (ValueError, TypeError):
            continue
        monday = day - datetime.timedelta(days=day.weekday())
        key = monday.isoformat()
        acc.setdefault(key, [])
        if pair[1] not in acc[key]:
            acc[key].append(pair[1])

    handle = open(path, "w", encoding="utf-8")
    try:
        for key in sorted(acc.keys()):
            handle.write(key + "|" + ",".join(acc[key]) + "\\n")
    finally:
        handle.close()
    return None
'''

_PARTIAL_E3 = _ORACLE_E3.replace(
    "    bucket = table.setdefault(key, [])\n    if value not in bucket:\n        bucket.append(value)\n",
    "    table.setdefault(key, []).append(value)\n")

#: ⚠️ 缺的名字 `datetime` 只在已经写好的两个辅助函数里。
_SKELETON_E3 = '''def _split(raw):
    head, sep, event = raw.partition("|")
    if not sep:
        return None
    head, event = head.strip(), event.strip()
    if not head or not event:
        return None
    return head, event


def _read_day(stamp):
    return datetime.date.fromisoformat(stamp)


def _monday_of(day):
    return (day - datetime.timedelta(days=day.weekday())).isoformat()


def _add(table, key, value):
    bucket = table.setdefault(key, [])
    if value not in bucket:
        bucket.append(value)


def week_of(items, path):
    buckets = {}
    # >>> BEGIN TODO
    # 遍历 items：pair = _split(raw)，空项返回 None；
    # _read_day(pair[0]) 解析日期（非法抛 ValueError，跳过该行）；
    # _monday_of(day) 取那一周的周一；_add(buckets, 周一, 事件) 归桶。
    # >>> END TODO

    with open(path, "w", encoding="utf-8") as fh:
        for key in sorted(buckets):
            fh.write(key + "|" + ",".join(buckets[key]) + "\\n")
    return None
'''


_SPECS_BASE = (
    dict(
        pair="e1", fn="clean_lines",
        body=(
            "删掉每项里所有**形如 `@` 后紧跟 1 个以上 ASCII 字母或数字**的标记"
            "（例如 `@ab`、`@9`）；`@` 后面不是这类字符时（例如行尾单个 `@`、`@ foo`），"
            "那个 `@` **原样保留**",
            "删完之后去掉该项首尾的空格与制表符；结果为空串的项**跳过**，不产生输出行",
            "内容相同的行只保留**第一次**出现的那一条（不相邻的重复也要去掉）",
            "把结果写进 `path`：**一行一项**，行内就是处理后的内容（不加序号、不加前缀）；"
            "函数返回 None",
        ),
        gen=_gen_e1, expect=_expect_e1,
        oracle=_ORACLE_E1, spec_impl=_SPEC_E1, partial=_PARTIAL_E1,
        skeleton_body=_SKELETON_E1, expect_names=("re",),
        artifact="t.txt",
    ),
    dict(
        pair="e2", fn="group_names",
        body=(
            "每项用**第一个** `|` 切成两段：左边组名、右边名字；没有 `|` 的项**跳过**",
            "两段各自去掉首尾空白；任一为空串的项**跳过**",
            "同一个组里的名字按**首次出现顺序**排列，重复的名字只保留**第一次**",
            "结果写进 `path` 一个 JSON **对象**，恰好一个顶层键 `\"groups\"`，"
            "它的值是 组名 → 名字列表；函数返回 None",
        ),
        gen=_gen_e2, expect=_expect_e2,
        oracle=_ORACLE_E2, spec_impl=_SPEC_E2, partial=_PARTIAL_E2,
        skeleton_body=_SKELETON_E2, expect_names=("json",),
        artifact="r.json",
    ),
    dict(
        pair="e3", fn="week_of",
        body=(
            "每项用**第一个** `|` 切成两段：左边日期 `YYYY-MM-DD`、右边事件；没有 `|`、"
            "两段有空、或日期不是合法公历日期的项**跳过**",
            "把事件归入它所在**周**的桶，桶的键是那一周的**周一**日期"
            "（一周从周一开始、到周日结束）",
            "同一个桶里事件按**首次出现顺序**排列，重复的事件只保留**第一次**",
            "每个桶输出一行，格式 `周一日期|事件1,事件2`（逗号分隔、逗号后不加空格），"
            "用 `\\n` 分行；函数返回 None",
        ),
        gen=_gen_e3, expect=_expect_e3,
        oracle=_ORACLE_E3, spec_impl=_SPEC_E3, partial=_PARTIAL_E3,
        skeleton_body=_SKELETON_E3, expect_names=("datetime",),
        artifact="w.txt",
    ),
)


def _build_specs() -> tuple[Spec, ...]:
    out: list[Spec] = []
    for base in _SPECS_BASE:
        pid = base["pair"]
        kw = {k: v for k, v in base.items() if k != "pair"}
        for g in GROUPS:
            out.append(Spec(id=f"{pid}{g}", pair=pid, group=g, **kw))
    return tuple(out)


SPECS: tuple[Spec, ...] = _build_specs()


# ===========================================================================
# 题面与断言
# ===========================================================================
def sig_of(sp: Spec) -> str:
    m = re.search(rf"def\s+{re.escape(sp.fn)}\s*\(([^)]*)\)", sp.oracle)
    if not m:
        raise SystemExit(f"❌ {sp.id}: oracle 里找不到 def {sp.fn}(...)")
    return m.group(1).strip()


def render_rules(sp: Spec) -> str:
    return "\n".join(f"  {i + 1}. {b}" for i, b in enumerate(sp.body))


_COMMON_TAIL = (
    "\n关于判分：\n"
    "  - 每条断言都在**全新的空目录**里单独执行你这份源码，然后读你写出的那个文件；\n"
    "  - 同一份源码会被执行 16 次（输入不同），不要缓存跨次状态，也不要读环境变量；\n"
    "  - 文本类按**逐行**比较，JSON 类按**解析后的对象**比较"
    "（缩进、空格、末尾换行这些格式差异**不计分**）；\n"
    "  - 只写 `path` 指向的那个文件，不要创建目录、也不要写别的文件；\n"
    "  - 规则没有提到的行为不作要求；规则之间若看起来有冲突，以更具体的那条为准。\n"
)

#: `wi` 首行带「import 已就位」—— 这是**日志探针唯一的分组依据**（`goal[:120]` 只印首行）。
_HEAD_WI = "下面是一段**已定稿**的框架代码，import 已就位，请你把函数 `{fn}` 补全。"
_HEAD_NI = "下面是一段**已定稿**的框架代码，请你把函数 `{fn}` 补全。"

_EDIT_WI = (
    "\n编辑约定：\n"
    "  - 补全 `# >>> BEGIN TODO` 与 `# >>> END TODO` 之间的内容；\n"
    "  - 除了这一段，其余每一行必须**逐字保留**（缩进、空行、注释、顶部已有的 import 都一样）；\n"
    "  - 顶部需要的 `import` 已经写好了，照原样留着即可；"
    "**骨架里已经写好的辅助函数直接调用即可，不必重写。**\n"
)

_EDIT_NI = (
    "\n编辑约定：\n"
    "  - 补全 `# >>> BEGIN TODO` 与 `# >>> END TODO` 之间的内容；\n"
    "  - 如果补全需要外部模块，**允许**你在**文件最顶部添加 `import` 语句**"
    "（加几条都行，加了不会被扣分）；\n"
    "  - 除了上面两件事，其余每一行必须**逐字保留**（缩进、空行、注释都一样）；"
    "**骨架里已经写好的辅助函数直接调用即可，不必重写。**\n"
)

_FORBID = ("不要加 import", "不要 import", "不许加 import", "禁止 import",
           "不要写 import", "不得 import", "不要引入")


def render_prompt(sp: Spec) -> str:
    rules = render_rules(sp)
    tail = f"只输出代码，函数名必须是 {sp.fn}。不要写示例调用或测试代码。"
    head = (_HEAD_WI if sp.group == "wi" else _HEAD_NI).format(fn=sp.fn)
    edit = _EDIT_WI if sp.group == "wi" else _EDIT_NI
    return (f"{head}\n{edit}\n"
            f"```python\n{skeleton_of(sp)}```\n\n"
            f"补全后，函数 `{sp.fn}` 必须满足下列规则：\n{rules}\n"
            f"{_COMMON_TAIL}{tail}")


def _call_src(sp: Spec, case: Any) -> str:
    return f"{sp.fn}({case!r}, {sp.artifact!r})"


def _one_test(sp: Spec, case: Any) -> str:
    """v8 的**判分方式**：逐行 / 解析级（S18 钉住这里不许退回 `read_bytes`）。"""
    want = sp.expect(case)
    head = "import pathlib as _pp\n"
    if sp.pair == "e2":
        return (head + "import json as _js\n"
                f"{_call_src(sp, case)}\n"
                f"assert _js.loads(_pp.Path({sp.artifact!r}).read_text(encoding='utf-8'))"
                f" == {want!r}\n")
    if sp.pair == "e3":
        return (head +
                f"{_call_src(sp, case)}\n"
                f"_text = _pp.Path({sp.artifact!r}).read_text(encoding='utf-8')\n"
                "_got = {}\n"
                "for _line in _text.splitlines():\n"
                "    _key, _sep, _rest = _line.partition('|')\n"
                "    assert _sep, 'bad line: %r' % _line\n"
                "    assert _key not in _got, 'bucket repeated: %r' % _key\n"
                "    _got[_key] = _rest.split(',')\n"
                f"assert _got == {want!r}\n")
    return (head +
            f"{_call_src(sp, case)}\n"
            f"assert _pp.Path({sp.artifact!r}).read_text(encoding='utf-8')"
            f".splitlines() == {want!r}\n")


def tests_of(sp: Spec, seed: int = SEED, n: int = CASES) -> list[str]:
    # ⚠️ 种子用 **pair** 而不是 id —— 否则配对两版生成不同用例，S12 就不成立。
    rng = random.Random(f"{seed}:{sp.pair}")
    return [_one_test(sp, sp.gen(rng)) for _ in range(n)]


def rows_of(seed: int = SEED, n: int = CASES) -> list[dict]:
    out = []
    for sp in SPECS:
        out.append({
            "id": sp.id,
            "prompt": render_prompt(sp),
            "tests": tests_of(sp, seed, n),
            "oracle": sp.oracle,
            "partial": sp.partial,
            "null": "",
            "meta": {
                "tags": f"external-oracle,artifact,skeleton-{sp.group},easy",
                "level": "easy",
                "seed": seed,
                "cases": n,
                "family": "artifact",
                "group": sp.group,
                "pair": sp.pair,
                "oracle_source": "CPython stdlib 现算（re/json/datetime 行为）",
                "expect_names": list(sp.expect_names),
                "import_allowed": sp.group == "ni",
                "scoring": "lines" if sp.pair != "e2" else "parsed-json",
                "rules": len(sp.body),
                "oracle_body_lines": body_lines(sp.oracle, sp.fn),
            },
        })
    return out


# ===========================================================================
# 自检
# ===========================================================================
def _v7_sha16() -> str:
    p = Path(__file__).parent / "gen_v7.py"
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def self_check() -> int:
    n_ni = sum(1 for s in SPECS if s.group == "ni")
    print("=" * 88)
    print(f"  v8 尺子自检（easy 档，seed={SEED}，每题 {CASES} 条断言，共 "
          f"{len(SPECS) * CASES} 格；wi={len(SPECS) - n_ni} / ni={n_ni} / "
          f"{len(_SPECS_BASE)} 对）")
    print("=" * 88)

    rows = rows_of()
    by_id = {r["id"]: r for r in rows}
    bad: list[str] = []

    print("\n[S1/S2/S3/S4] 逐题标定")
    print(f"  {'题':6s} {'组':3s} {'oracle':>8s} {'spec':>7s} {'partial':>9s} "
          f"{'null':>6s}  判定")
    for sp, r in zip(SPECS, rows):
        n = len(r["tests"])
        o = score(sp.oracle, r["tests"])
        s = score(sp.spec_impl, r["tests"])
        p = score(sp.partial, r["tests"])
        z = score(r["null"], r["tests"])
        ok = (o == n) and (s == n) and (0 < p < n) and (z == 0)
        if not ok:
            bad.append(f"{sp.id} 标定")
        print(f"  {sp.id:6s} {sp.group:3s} {o:4d}/{n:<3d} {s:4d}/{n:<2d} "
              f"{p:6d}/{n:<2d} {z:4d}/{n:<2d}  {'✅' if ok else '❌'}")

    a = json.dumps(rows_of(), ensure_ascii=False, sort_keys=True)
    b = json.dumps(rows_of(), ensure_ascii=False, sort_keys=True)
    s5 = a == b
    print(f"\n[S5] 同种子重生成逐字节相同：{'✅' if s5 else '❌ 尺子会漂'}")
    if not s5:
        bad.append("S5")
    c = json.dumps(rows_of(seed=SEED + 1), ensure_ascii=False, sort_keys=True)
    s6 = a != c
    print(f"[S6] 换种子后题集必须变：{'✅ 变了' if s6 else '❌ 期望值是写死的'}")
    if not s6:
        bad.append("S6")

    # S7 期望值泄漏（v8 的期望值不再是字节串，按 repr 尾巴查）
    leaks = []
    for sp, r in zip(SPECS, rows):
        for t in r["tests"]:
            tail = t.rsplit("==", 1)[-1].strip() if "==" in t else ""
            if len(tail) >= 6 and tail in r["prompt"]:
                leaks.append(f"{sp.id}:{tail[:20]}")
    s7 = not leaks
    print(f"[S7] 期望值泄漏进题面：{'✅ 无' if s7 else '❌ ' + str(leaks[:3])}")
    if not s7:
        bad.append("S7")

    print("\n[S8/S9/S10] `ni` 骨架：零 import / 语法正确 / 恰好缺**一个**名字且必然 0 分")
    print(f"  {'题':6s} {'import 行':>9s} {'语法':>6s} {'undefined_names':>16s} "
          f"{'分数':>7s}  判定")
    s8 = s9 = s10 = True
    for sp in SPECS:
        if sp.group != "ni":
            continue
        sk = skeleton_of(sp)
        n = len(by_id[sp.id]["tests"])
        imp = re.findall(r"(?m)^\s*(?:import|from)\s", sk)
        try:
            compile(sk, "<skel>", "exec")
            syn = "✅"
        except SyntaxError as exc:
            syn = f"❌{exc.lineno}"
        names = undefined_names(sk)
        got = sorted({nm for _fn, nm in (names or [])})
        want = sorted(sp.expect_names)
        sc = score(sk, by_id[sp.id]["tests"])
        ok = (not imp) and syn == "✅" and got == want and sc == 0 and len(want) == 1
        s8 = s8 and not imp
        s9 = s9 and syn == "✅"
        s10 = s10 and got == want and sc == 0 and len(want) == 1
        print(f"  {sp.id:6s} {len(imp):9d} {syn:>6s} {','.join(got) or '—':>16s} "
              f"{sc:3d}/{n:<3d}  {'✅' if ok else '❌'}")
    if not s8:
        bad.append("S8 ni 骨架里有 import")
    if not s9:
        bad.append("S9 骨架语法错")
    if not s10:
        bad.append("S10 骨架没缺名字 / 缺错了 / 没必然失败 / 缺的不止一个")

    print("\n[S11] 可救反证：把 oracle 的 import 删掉（逻辑一字不改）⇒ 必须仍 0 分")
    s11 = True
    for sp in SPECS:
        if sp.group != "ni":
            continue
        n = len(by_id[sp.id]["tests"])
        src = sp.oracle
        for name in sp.expect_names:
            src, _ = strip_binding(src, name)
        sc = score(src, by_id[sp.id]["tests"])
        got = sorted({nm for _fn, nm in (undefined_names(src) or [])})
        ok = sc == 0 and got == sorted(sp.expect_names)
        s11 = s11 and ok
        print(f"  {sp.id:6s} 删 {','.join(sp.expect_names):10s} ⇒ 分数 {sc:2d}/{n}"
              f"（必须 0）  undefined_names={','.join(got) or '—'}  "
              f"{'✅ 拦路的只有 import' if ok else '❌'}")
    if not s11:
        bad.append("S11")

    print("\n[S12] 配对同一性：同 pair 的 wi/ni 必须逐字节相同")
    s12 = True
    for base in _SPECS_BASE:
        pid = base["pair"]
        x, y = by_id[f"{pid}wi"], by_id[f"{pid}ni"]
        same = all(x[k] == y[k] for k in ("tests", "oracle", "partial", "null"))
        sx = [sp for sp in SPECS if sp.id == f"{pid}wi"][0]
        sy = [sp for sp in SPECS if sp.id == f"{pid}ni"][0]
        same = same and (sx.spec_impl == sy.spec_impl) and (sx.artifact == sy.artifact)
        s12 = s12 and same
        print(f"  {pid}: tests={len(x['tests'])} 条 "
              f"oracle={'同' if x['oracle'] == y['oracle'] else '异'}"
              f" partial={'同' if x['partial'] == y['partial'] else '异'}"
              f" spec_impl={'同' if sx.spec_impl == sy.spec_impl else '异'}"
              f" artifact={sx.artifact}  {'✅' if same else '❌'}")
    if not s12:
        bad.append("S12 配对两版其实不是同一道题")

    print("\n[S13] ⭐ ± 一行 import：wi 骨架 == 'import <m>\\n\\n' + ni 骨架（逐字节）")
    s13 = True
    for base in _SPECS_BASE:
        pid = base["pair"]
        sx = [sp for sp in SPECS if sp.id == f"{pid}wi"][0]
        sy = [sp for sp in SPECS if sp.id == f"{pid}ni"][0]
        want = "".join(f"import {nm}\n" for nm in sy.expect_names) + "\n" + skeleton_of(sy)
        same = skeleton_of(sx) == want
        wi_clean = not (undefined_names(skeleton_of(sx)) or [])
        ni_names = sorted({n for _f, n in (undefined_names(skeleton_of(sy)) or [])})
        ok = same and wi_clean and bool(ni_names)
        s13 = s13 and ok
        print(f"  {pid}: wi 骨架逐字节=ni+import {same}  "
              f"undefined(wi)={undefined_names(skeleton_of(sx)) or '空'}  "
              f"undefined(ni)={ni_names}  {'✅' if ok else '❌'}")
    if not s13:
        bad.append("S13 wi 骨架不是 ni 骨架 + 一行 import")

    print("\n[S14] ⭐ 反证冲突已拆除：ni 题面**不许**出现禁止 import 的字样，且**必须**出现允许字样")
    s14 = True
    for sp in SPECS:
        if sp.group != "ni":
            continue
        p = by_id[sp.id]["prompt"]
        forb = [w for w in _FORBID if w in p]
        allow = ("允许" in p) and ("import" in p)
        ok = (not forb) and allow
        s14 = s14 and ok
        print(f"  {sp.id:6s} 禁止字样={forb or '无'}  允许字样={allow}  {'✅' if ok else '❌'}")
    if not s14:
        bad.append("S14 ni 题面仍禁止 import / 没有允许字样")

    print("\n[S15] ⭐ artifact 不含路径分隔符（cwd 根），且断言里用的是同一个根路径")
    s15 = True
    for sp, r in zip(SPECS, rows):
        clean = ("/" not in sp.artifact) and ("\\" not in sp.artifact)
        in_tests = all(f"'{sp.artifact}'" in t for t in r["tests"])
        ok = clean and in_tests
        s15 = s15 and ok
        print(f"  {sp.id:6s} artifact={sp.artifact:8s} 无分隔符={clean} "
              f"断言全用同一路径={in_tests}  {'✅' if ok else '❌'}")
    if not s15:
        bad.append("S15 artifact 还在子目录里（建目录变量回来了）")

    print("\n[S16] ⭐ 配平：ni / wi 题面字数比必须 ≤ 1.30")
    print(f"  {'组':3s} {'题':6s} {'题面字数':>8s} {'行数':>6s} {'规则条数':>8s} "
          f"{'骨架行数':>8s} {'缺的名字':>10s}")
    lens: dict[str, list[int]] = {"wi": [], "ni": []}
    for sp in SPECS:
        p = render_prompt(sp)
        lens[sp.group].append(len(p))
        print(f"  {sp.group:3s} {sp.id:6s} {len(p):8d} {len(p.splitlines()):6d} "
              f"{len(sp.body):8d} {len(skeleton_of(sp).splitlines()):8d} "
              f"{','.join(sp.expect_names) if sp.group == 'ni' else '—':>10s}")
    mw = sum(lens["wi"]) / len(lens["wi"])
    mn = sum(lens["ni"]) / len(lens["ni"])
    ratio = mn / mw
    s16 = ratio <= 1.30
    print(f"  平均字数：wi={mw:.0f}  ni={mn:.0f}  比值={ratio:.3f} "
          f"{'✅ ≤1.30' if s16 else '❌ >1.30'}")
    if not s16:
        bad.append(f"S16 题面长度没配平（比值 {ratio:.3f}）")

    # ── S17 ⭐ 降难度通道 1：规则条数 ─────────────────────────────────────
    print("\n[S17] ⭐ 降难度①：每题规则条数 ≤ 4（v7 是 8/9/9）")
    s17 = True
    for base, sp in zip(_SPECS_BASE, [s for s in SPECS if s.group == "ni"]):
        n = len(sp.body)
        ok = n <= 4
        s17 = s17 and ok
        print(f"  {sp.pair}: {n} 条规则  {'✅' if ok else '❌'}")
    if not s17:
        bad.append("S17 规则条数没降到 ≤4")

    # ── S18 ⭐ 降难度通道 2：判分方式 ────────────────────────────────────
    print("\n[S18] ⭐ 降难度②：判分不许是字节比较（不含 read_bytes；含 splitlines/json.loads）")
    s18 = True
    for sp, r in zip(SPECS, rows):
        blob = "\n".join(r["tests"])
        no_bytes = "read_bytes" not in blob
        parsed = ("splitlines()" in blob) or ("_js.loads" in blob)
        ok = no_bytes and parsed
        s18 = s18 and ok
        print(f"  {sp.id:6s} 无 read_bytes={no_bytes} 逐行/解析={parsed}  "
              f"scoring={r['meta']['scoring']}  {'✅' if ok else '❌'}")
    if not s18:
        bad.append("S18 判分退回了字节比较")
    # ⭐ 反证：v7 的断言**必须**含 read_bytes —— 否则 S18 是恒真的空断言
    import gen_v7 as _g7
    v7_blob = "\n".join(_g7.rows_of()[0]["tests"])
    rev = "read_bytes" in v7_blob
    print(f"  ⭐ 反证：v7 断言含 read_bytes = {rev}"
          f"（必须 True，否则 S18 恒真）  {'✅' if rev else '❌'}")
    if not rev:
        bad.append("S18 反证失效：v7 也不用 read_bytes，S18 恒真")

    # ── S19 ⭐ 降难度通道 3：oracle 主函数体行数 ─────────────────────────
    print("\n[S19] ⭐ 降难度③：oracle 主函数体行数（v7 只计 ni 组 = 13+16+20 = 49）")
    s19 = True
    tot8 = 0
    tot7 = 0
    for base, sp in zip(_SPECS_BASE, [s for s in SPECS if s.group == "ni"]):
        bl = body_lines(sp.oracle, sp.fn)
        tot8 += bl
        v7 = [s for s in _g7.SPECS if s.pair == f"p{sp.pair[-1]}" and s.group == "ni"][0]
        bl7 = body_lines(v7.oracle, v7.fn)
        tot7 += bl7
        ok = bl <= 14 and bl < bl7
        s19 = s19 and ok
        print(f"  {sp.pair}: v7 {bl7:2d} → v8 {bl:2d} 行  {'✅' if ok else '❌'}")
    print(f"  合计：v7 {tot7} → v8 {tot8} 行  "
          f"{'✅ 严格变短' if tot8 < tot7 else '❌ 没变短'}")
    if not (tot8 < tot7):
        bad.append("S19 oracle body 没变短")
    if not s19:
        bad.append("S19 有题没降下来")

    # ── S20 ⭐ 冻结检查：借来的判分器不许漂 ──────────────────────────────
    got = _v7_sha16()
    s20 = got == _V7_SHA16
    print(f"\n[S20] ⭐ 冻结：gen_v7.py 的 sha16 = {got}（登记值 {_V7_SHA16}）  "
          f"{'✅' if s20 else '❌ 判分器漂了 ⇒ v7/v8 的分数不再可比'}")
    if not s20:
        bad.append(f"S20 gen_v7.py 变了（{got}）—— 改它必须同时 --rebind-sha 并登记")

    # ── S21 ⭐ 冻结检查：印出来的必须是**装置真用的那个 sha** ────────────────
    #   ⛔ 第 109 轮踩过：登记了 `sha256(json.dumps(rows, sort_keys=True))[:16]`
    #   = `aece03f6dec738fc`，而 runner / `caliber.sig` 用的是 `Suite.sha()`
    #   = `519dad774a3664612e6611b220b33f4a`。同一份文件两个数，登记错了那一个。
    #   ⇒ 本条断言的语义是"生成器自己印的 sha == 装载器算的 sha"，
    #     这是把 §71.9「读数只许锚真口径」变成机器可判的一条。
    if not _SUITE_FILE.exists():
        print(f"\n[S21] ⭐ 冻结：{_SUITE_FILE.name} 还没 emit ⇒ 跳过比对"
              f"（期望 Suite.sha()[:16] = {_SUITE_SHA16}）")
    else:
        real16 = Suite.load(_SUITE_FILE).sha()[:16]
        s21 = real16 == _SUITE_SHA16
        print(f"\n[S21] ⭐ 冻结：Suite.sha()[:16] = {real16}（登记值 {_SUITE_SHA16}）  "
              f"{'✅' if s21 else '❌ 尺子漂了 ⇒ 与 v8 的任何读数不可比'}")
        if not s21:
            bad.append(f"S21 Suite.sha() 变了（{real16}）—— 改题必须登记并作废本轮读数")

    print()
    if bad:
        print(f"❌ {len(bad)} 条自检失败：{bad}")
        return 1
    print("✅ 二十一条自检全绿")
    return 0


def emit(path: str) -> int:
    rows = rows_of()
    Path(path).write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8")
    blob = Path(path).read_bytes()
    print(f"已写 {path}（{len(rows)} 题 / "
          f"{sum(len(r['tests']) for r in rows)} 格）")
    print(f"文件 sha256 = {hashlib.sha256(blob).hexdigest()}")
    # ⭐ S21 的另一半：这里印的必须是**装载器算的那个**，不是自制公式（第 109 轮 §12 的坑）
    print(f"suite_sha   = {Suite.load(path).sha()}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", nargs="?", const="bench/specgen/tasks_v8.jsonl")
    args = ap.parse_args()
    rc = self_check()
    if args.emit and rc == 0:
        rc = emit(args.emit)
    sys.exit(rc)
