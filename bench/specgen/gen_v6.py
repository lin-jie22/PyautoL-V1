r"""v6 尺子：把「缺 import」做成**题面的给定状态**（§76.8 的 V4″）。

## 上一轮堵死的那条路

§76 造了 6 题去逼模型用 `re`/`json`/`datetime`，模型 **18 次交付一次都没忘 import**
⇒ 名字类机会 **0/3 题**，D0 触发，第二臂没跑。
零模型成因：**缺名字只出现在「整份答案一行 import 都没有」的交付物里（7/7）**，
有 import 行的 49 份里 **0 份**。

⇒ 结论：**机会不是题集的属性，是答案形态的函数**。
   题面能决定「这一题用不用外部模块」，决定不了「模型写不写 import 行」。

## 这一轮怎么绕过去

唯一还剩的杠杆：**把「没有 import」写进题面**，让它成为**给定状态**，而不是模型的自由选择。

于是这把尺子用**配对设计**：同一道题两个题面形态。

| 组 | 题面 | 缺 import 从哪来 |
|---|---|---|
| `bf`（bare function） | 传统形态：「写一个函数 `f`，满足下列规则」 | 模型自己决定（§76 已证 ⇒ 机会 ≈ 0） |
| `sk`（skeleton） | **给一段已定稿的框架代码**，`import` 由打包脚本注入所以不出现；只许补 `# >>> BEGIN TODO` / `# >>> END TODO` 之间，区块之外**逐字保留**，**不许加 import** | **题面给定**（结构上 100%） |

⚠️ 同一 `pair` 的 `sk` 与 `bf` **共用** `tests` / `oracle` / `spec_impl` / `partial` / `artifact`
（S12 逐字节钉住）。⇒ 两版的差别**只有题面文本**，分数差只能来自这个差别。

⛔ 这**不**证明 `sk` 组「更难」：难度没有被匹配，也没有被声称匹配。
  它只保证一件事：`undefined_names` 在 `sk` 组上**必然有活干**。

## 这条构造的诚实边界（写在这里，免得下一轮被当成"自然题面"引用）

`sk` 组的机会是**人为造出来的**：真实用户在需求里不会说「不许 import」。
因此本尺子的结论只能说到：

> **「骨架补全型题面」能不能把机会做成自变量**（以及兑现时两臂差多少）

⛔ 不能说到「自然题面里机会有多少」，也不能说到「哪种题面更好」。

## 十三条自检（缺一条这把尺子就不许用）

  S1  oracle 满分                    —— 否则题目不可解
  S2  spec_impl（只照题面手写）满分  —— 否则题面不充分
  S3  partial 严格居中               —— 否则断言不区分对错
  S4  null（空串）零分               —— 否则空答案也能拿分
  S5  同种子重生成**逐字节相同**     —— 尺子不许漂
  S6  换种子必须**变**               —— 证明期望值不是写死的
  S7  期望值不许出现在题面里         —— 否则把答案印在卷子上
  S8  ⭐ `sk` 组骨架里 `^\s*(import|from)\s` **一条都没有** —— 给定状态必须是空的
  S9  ⭐ 骨架必须**语法正确**        —— 否则 `undefined_names` 返回 None，机会不可判
  S10 ⭐⭐ 骨架反证：`undefined_names(骨架)` **恰好**等于预期名字集，且 `score(骨架)==0`
      —— 证明"缺 import"是真的拦路虎，不是装饰
  S11 ⭐⭐ 可救反证：把 oracle 的 import **删掉**（逻辑全对）⇒ 分数仍为 0、名字仍缺
      —— 证明拦路的**只有** import 这一处
  S12 ⭐⭐⭐ 配对同一性：同 `pair` 的 `sk`/`bf` 的 tests/oracle/spec/partial/artifact **逐字节相同**
      —— 否则"两版是同一道题"这句话是假的，Δ 不可归因
  S13 ⭐ 反证 S12：`sk` 的 prompt 里**有** TODO 标记，`bf` 的 prompt 里**没有**
      —— 否则两版题面其实一样，S12 只证明了"我写了两遍了"

S13 是 S12 的反证：**一条只在一边成立的性质，必须在另一边被证伪**，否则那条性质
可能是两份文件共同的属性（§76 S11 同源：控制组"零 import"是"不存在的证据"）。

## 跑法

    python3 bench/specgen/gen_v6.py            # 十三条自检 + 配对对照表
    python3 bench/specgen/gen_v6.py --emit     # 写 bench/specgen/tasks_v6.jsonl

⛔ 生成是**构建步骤**：`tasks_v6.jsonl` 一旦提交就冻结，打分永远对着冻结的那份。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mw.verify import undefined_names  # noqa: E402

SEED = 20261029          #: 固定种子。换它 = 换尺子 = 旧读数失去对照。
CASES = 16               #: 每题断言条数（= 每格题的分母）

_W = ["alpha", "beta", "gamma", "delta", "eps", "zeta", "eta", "theta"]
_CN = ["红", "蓝", "绿", "紫"]
_WS = ["  ", "\t", " ", "\t\t"]


# ===========================================================================
# 规格
# ===========================================================================
@dataclass(frozen=True)
class Spec:
    id: str
    pair: str                    #: 配对 id —— 同 pair 的 sk/bf 共用用例与 oracle
    fn: str
    group: str                   #: "bf" = 传统题面；"sk" = 骨架补全题面
    body: tuple[str, ...]
    gen: Callable[[random.Random], Any]
    expect: Callable[[Any], bytes]   #: **独立**算期望值（不许调用 oracle）
    oracle: str
    spec_impl: str
    partial: str
    artifact: str                #: 产物路径（相对 cwd），断言读它的字节
    skeleton: str = ""           #: 只有 sk 组有；**必须零 import 且语法正确**
    expect_names: tuple[str, ...] = ()   #: 骨架里缺的名字（S10 拿它做反证）


# ── 期望值用的手工原语（不调 re / 不调 oracle）────────────────────────────
def _strip_at(s: str) -> str:
    """删掉所有 `@` 后紧跟 1+ 个 ASCII 字母/数字的标记。手工扫描，不用正则。"""
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


def _fold_ws(s: str) -> str:
    """把连续的空格/制表符折叠成一个空格并去首尾。手工扫描。"""
    parts: list[str] = []
    cur: list[str] = []
    for c in s:
        if c in " \t":
            if cur:
                parts.append("".join(cur))
                cur = []
        else:
            cur.append(c)
    if cur:
        parts.append("".join(cur))
    return " ".join(parts)


def _jstr(s: str) -> str:
    """题面生成的名字只含 ASCII 字母与中文 ⇒ 直接加引号就是合法 JSON 字符串。"""
    if '"' in s or "\\" in s:
        raise AssertionError(f"❌ 生成的名字含需要转义的字符：{s!r}")
    return '"' + s + '"'


# ── p1：清标记 + 折叠空白 + 全局去重 + 编号（需要 re 与 os）───────────────
_ASSIGN = re.compile(r"@[A-Za-z0-9]+")


def _gen_p1(rng: random.Random) -> list[str]:
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
            line += " @"                      # 光秃的 `@`：必须保留
        if rng.random() < 0.1:
            line = "@ " + line                # `@` 后跟空格：`@` 保留
        items.append(line)
    if len(items) >= 3 and rng.random() < 0.7:
        items.insert(0, items[-1])            # 首尾重复 ⇒ **非相邻**重复（规则 4）
    return items


def _expect_p1(items: list[str]) -> bytes:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        s = _fold_ws(_strip_at(raw))
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return "".join(f"{i}.{s}\n" for i, s in enumerate(out, 1)).encode("utf-8")


_ORACLE_P1 = '''import os
import re

_TAG = re.compile(r"@[A-Za-z0-9]+")


def tag_report(items, path):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    seen = []
    index = set()
    for raw in items:
        s = _TAG.sub("", raw)
        s = re.sub(r"[ \\t]+", " ", s.strip(" \\t"))
        if not s or s in index:
            continue
        index.add(s)
        seen.append(s)
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        for i, s in enumerate(seen, 1):
            fh.write(f"{i}.{s}\\n")
    return None
'''

_SPEC_P1 = '''import os
import re


def tag_report(items, path):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)

    kept = []
    for line in items:
        t = re.sub(r"@[A-Za-z0-9]+", "", line)
        t = " ".join(t.replace("\\t", " ").split())
        if t == "":
            continue
        if t in kept:
            continue
        kept.append(t)

    with open(path, "w", encoding="utf-8") as fh:
        for n, t in enumerate(kept):
            fh.write(str(n + 1) + "." + t + "\\n")
    return None
'''

_PARTIAL_P1 = _ORACLE_P1.replace(
    "        if not s or s in index:\n            continue\n",
    "        if not s:\n            continue\n")

_SKELETON_P1 = '''_TAG = re.compile(r"@[A-Za-z0-9]+")


def tag_report(items, path):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)

    seen = []
    # >>> BEGIN TODO
    # 在这里补全：遍历 items，按规则 1~4 生成 seen（处理后的字符串列表）。
    # 上面的 _TAG 是一个编译好的正则，可以直接用。
    # >>> END TODO

    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        for i, s in enumerate(seen, 1):
            fh.write(f"{i}.{s}\\n")
    return None
'''


# ── p2：合并成 JSON（需要 json 与 os）──────────────────────────────────────
def _gen_p2(rng: random.Random) -> list[str]:
    items: list[str] = []
    groups = _W[:rng.randint(2, 4)]
    for _ in range(rng.randint(4, 8)):
        g = rng.choice(groups)
        name = rng.choice(_W + _CN)
        pad = rng.choice(["", " "])
        roll = rng.random()
        if roll < 0.08:
            items.append(name)                        # 没有 `|`：忽略
        elif roll < 0.16:
            items.append(f"{g}|")                     # 空人名：忽略
        elif roll < 0.22:
            items.append(f"|{name}")                  # 空组名：忽略
        else:
            items.append(f"{pad}{g}{pad}|{pad}{name}{pad}")
    if rng.random() < 0.5:
        items.append(items[0])                        # 完全重复
    return items


def _expect_p2(items: list[str]) -> bytes:
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
    body = ", ".join(
        f"{_jstr(k)}: [" + ", ".join(_jstr(n) for n in table[k]) + "]"
        for k in sorted(table))
    return ('{"groups": {' + body + '}}').encode("utf-8")


_ORACLE_P2 = '''import json
import os


def save_roster(items, path):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    table = {}
    for raw in items:
        head, sep, name = raw.partition("|")
        if not sep:
            continue
        head = head.strip()
        name = name.strip()
        if not head or not name:
            continue
        bucket = table.setdefault(head, [])
        if name not in bucket:
            bucket.append(name)
    ordered = {k: table[k] for k in sorted(table)}
    blob = json.dumps({"groups": ordered}, ensure_ascii=False)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(blob)
    return None
'''

_SPEC_P2 = '''import json
import os


def save_roster(items, path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    acc = {}
    for row in items:
        if row.count("|") < 1:
            continue
        left, right = row.split("|", 1)
        g = left.strip()
        n = right.strip()
        if g == "" or n == "":
            continue
        acc.setdefault(g, [])
        if n not in acc[g]:
            acc[g].append(n)
    out = {"groups": {k: acc[k] for k in sorted(acc.keys())}}
    text = json.dumps(out, ensure_ascii=False)
    handle = open(path, "w", encoding="utf-8")
    try:
        handle.write(text)
    finally:
        handle.close()
    return None
'''

_PARTIAL_P2 = _ORACLE_P2.replace(
    "        if name not in bucket:\n            bucket.append(name)\n",
    "        bucket.append(name)\n")

_SKELETON_P2 = '''def save_roster(items, path):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)

    table = {}
    # >>> BEGIN TODO
    # 在这里补全：遍历 items，按规则 1~4 填满 table（组名 -> 名字列表）。
    # 注意列表顺序与去重都由规则决定。
    # >>> END TODO

    payload = {"groups": table}
    blob = json.dumps(payload, ensure_ascii=False)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(blob)
    return None
'''


# ── p3：按 ISO 周分桶（需要 datetime 与 os）────────────────────────────────
def _gen_p3(rng: random.Random) -> list[str]:
    base = ["2026-03-02", "2026-03-05", "2026-03-08", "2026-03-09", "2026-03-15",
            "2026-02-23", "2026-03-01", "2026-04-01", "2026-03-07", "2026-03-16"]
    items: list[str] = []
    for _ in range(rng.randint(4, 8)):
        items.append(f"{rng.choice(base)}|{rng.choice(_W + _CN)}")
    if rng.random() < 0.25:
        items.append("2026-02-30|坏日期")        # 无效日期：忽略
    if rng.random() < 0.25:
        items.append(rng.choice(base))           # 没有 `|`：忽略
    if rng.random() < 0.25:
        items.append("2026-03-04|")              # 空事件：忽略
    if rng.random() < 0.5:
        items.append(items[0])                   # 重复
    return items


def _expect_p3(items: list[str]) -> bytes:
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
            d = _dt.date(int(head[0:4]), int(head[5:7]), int(head[8:10]))
        except (ValueError, IndexError):
            continue
        # 独立算周一：用序数 − 星期几，不用 timedelta
        key = _dt.date.fromordinal(d.toordinal() - d.weekday()).isoformat()
        bucket = buckets.setdefault(key, [])
        if ev not in bucket:
            bucket.append(ev)
    return "".join(k + "|" + ",".join(buckets[k]) + "\n"
                   for k in sorted(buckets)).encode("utf-8")


_ORACLE_P3 = '''import datetime
import os


def _monday_of(day):
    return (day - datetime.timedelta(days=day.weekday())).isoformat()


def week_buckets(items, path):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    buckets = {}
    for raw in items:
        head, sep, event = raw.partition("|")
        if not sep:
            continue
        head = head.strip()
        event = event.strip()
        if not head or not event:
            continue
        try:
            day = datetime.date.fromisoformat(head)
        except ValueError:
            continue
        bucket = buckets.setdefault(_monday_of(day), [])
        if event not in bucket:
            bucket.append(event)
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        for key in sorted(buckets):
            fh.write(key + "|" + ",".join(buckets[key]) + "\\n")
    return None
'''

_SPEC_P3 = '''import datetime
import os


def week_buckets(items, path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    acc = {}
    for row in items:
        parts = row.split("|")
        if len(parts) != 2:
            continue
        stamp = parts[0].strip()
        label = parts[1].strip()
        if stamp == "" or label == "":
            continue
        try:
            day = datetime.date(*[int(x) for x in stamp.split("-")])
        except (ValueError, TypeError):
            continue
        monday = day - datetime.timedelta(days=day.weekday())
        key = monday.strftime("%Y-%m-%d")
        acc.setdefault(key, [])
        if label not in acc[key]:
            acc[key].append(label)

    handle = open(path, "w", encoding="utf-8", newline="\\n")
    try:
        for key in sorted(acc.keys()):
            handle.write(key + "|" + ",".join(acc[key]) + "\\n")
    finally:
        handle.close()
    return None
'''

_PARTIAL_P3 = _ORACLE_P3.replace(
    "        if event not in bucket:\n            bucket.append(event)\n",
    "        bucket.append(event)\n")

_SKELETON_P3 = '''def _monday_of(day):
    return (day - datetime.timedelta(days=day.weekday())).isoformat()


def week_buckets(items, path):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)

    buckets = {}
    # >>> BEGIN TODO
    # 在这里补全：遍历 items，按规则 1~4 填满 buckets。
    # 键是那一周周一的 ISO 日期串（"YYYY-MM-DD"），值是事件名列表。
    # 上面的 _monday_of(day) 接受一个日期对象，返回该周周一的 ISO 串，可以直接用。
    # >>> END TODO

    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        for key in sorted(buckets):
            fh.write(key + "|" + ",".join(buckets[key]) + "\\n")
    return None
'''


# ===========================================================================
# 六个规格（三对）
# ===========================================================================
_SPECS_BASE = (
    dict(
        pair="p1", fn="tag_report",
        body=(
            "对每一项，删掉所有**形如 `@` 后紧跟 1 个以上 ASCII 字母或数字**的标记"
            "（例如 `@ab`、`@9`、`@a_1`）；`@` 后面不是这类字符时（例如行尾单个 `@`、"
            "`@ foo`），那个 `@` **保留**",
            "删完之后，把连续的空格与制表符折叠成**一个空格**，再去掉首尾空白"
            "（换行不在处理范围内）",
            "处理完为空串的项**跳过**，不产生输出行",
            "**全局**去重：内容相同的行只保留**第一次**出现的那一条（不相邻的重复也去掉）",
            "每项输出一行，格式 `序号.内容`，序号从 `1` 开始按输出顺序递增，十进制不补零",
            "每一行都以 `\\n` 结尾 —— 包括最后一行",
            "用 UTF-8 编码写（不带 BOM），换行符必须是 `\\n` 而不是 `\\r\\n`",
            "`path` 的父目录不存在时要先创建",
            "函数返回 None",
        ),
        gen=_gen_p1, expect=_expect_p1,
        oracle=_ORACLE_P1, spec_impl=_SPEC_P1, partial=_PARTIAL_P1,
        skeleton=_SKELETON_P1, expect_names=("os", "re"),
        artifact="out/t.txt",
    ),
    dict(
        pair="p2", fn="save_roster",
        body=(
            "每项用**第一个** `|` 切成两段：左边是组名，右边是人名（人名里不含 `|`）",
            "没有 `|` 的项**忽略**；组名或人名去掉首尾空白后为空串的项**忽略**",
            "同一个组里，名字按**首次出现顺序**排列；同一组里重复的名字只保留**第一次**",
            "组名按 **ASCII 字典序升序**排列",
            "结果写成一个 JSON **对象**，恰好一个顶层键 `\"groups\"`，它的值是 "
            "组名 → 名字列表 的对象",
            "`ensure_ascii=False`（非 ASCII 字符原样写出，不转成 `\\uXXXX`）；"
            "不做缩进；分隔符用默认的 `\", \"` 与 `\": \"`",
            "**文件末尾不加换行**",
            "用 UTF-8 编码写（不带 BOM）",
            "`path` 的父目录不存在时要先创建",
            "函数返回 None",
        ),
        gen=_gen_p2, expect=_expect_p2,
        oracle=_ORACLE_P2, spec_impl=_SPEC_P2, partial=_PARTIAL_P2,
        skeleton=_SKELETON_P2, expect_names=("json", "os"),
        artifact="out/r.json",
    ),
    dict(
        pair="p3", fn="week_buckets",
        body=(
            "每项用**第一个** `|` 切成两段：左边是日期 `YYYY-MM-DD`，右边是事件名",
            "没有 `|` 的项**忽略**；日期或事件名去掉首尾空白后为空串的项**忽略**；"
            "日期不是合法公历日期的项**忽略**",
            "把事件归入它所在**周**的桶，桶的键是那一周的**周一**日期"
            "（一周从周一开始，到周日结束）",
            "同一个桶里，事件按**首次出现顺序**排列；同一桶里重复的事件只保留**第一次**",
            "每个桶输出一行，格式 `周一日期|事件1,事件2,...`，日期用 `YYYY-MM-DD`",
            "各行按周一日期**升序**排列",
            "每一行都以 `\\n` 结尾 —— 包括最后一行",
            "用 UTF-8 编码写（不带 BOM），换行符必须是 `\\n`",
            "`path` 的父目录不存在时要先创建",
            "函数返回 None",
        ),
        gen=_gen_p3, expect=_expect_p3,
        oracle=_ORACLE_P3, spec_impl=_SPEC_P3, partial=_PARTIAL_P3,
        skeleton=_SKELETON_P3, expect_names=("datetime", "os"),
        artifact="out/w.txt",
    ),
)


def _build_specs() -> tuple[Spec, ...]:
    out: list[Spec] = []
    for base in _SPECS_BASE:
        pid = base["pair"]
        kw = {k: v for k, v in base.items() if k != "pair"}
        out.append(Spec(id=f"{pid}bf", pair=pid, group="bf", **kw))
        out.append(Spec(id=f"{pid}sk", pair=pid, group="sk", **kw))
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


def render_prompt(sp: Spec) -> str:
    sig = sig_of(sp)
    rules = render_rules(sp)
    tail = f"只输出代码，函数名必须是 {sp.fn}。不要写示例调用或测试代码。"
    if sp.group == "bf":
        return (f"写一个函数 `{sp.fn}`，函数签名**必须**是 "
                f"`def {sp.fn}({sig})`（参数名与顺序照此写，不要改）。"
                f"按下面列出的规则实现：\n{rules}\n{tail}")
    return (
        # ⚠️ 函数名必须出现在**第一行**：`mw/core.py:729` 只印 `goal[:120]`，
        #    而日志探针只能看块的第一行。第一行没有函数名 ⇒ sk 版无法归到具体哪一对。
        #    这是第 107 轮开工时踩到的（见 evidence/v4_s107/RESULTS.md 自踩条）。
        f"下面是一段**已定稿**的框架代码，请你把函数 `{sp.fn}` 补全。\n\n"
        "约束（违反任意一条都会被退回重写）：\n"
        "  - 只允许修改 `# >>> BEGIN TODO` 与 `# >>> END TODO` 之间的内容；\n"
        "  - 区块**之外**的每一行必须**逐字保留**（缩进、空行、注释都一样）；\n"
        "  - **不要**添加任何 `import` 语句 —— 这个模块的 `import` 由打包脚本"
        "统一注入，源文件里不出现 import 行。\n\n"
        f"```python\n{sp.skeleton}```\n\n"
        f"补全后，函数 `{sp.fn}` 必须满足下列规则：\n{rules}\n{tail}")


def _call_src(sp: Spec, case: Any) -> str:
    return f"{sp.fn}({case!r}, {sp.artifact!r})"


def _one_test(sp: Spec, case: Any) -> str:
    want = sp.expect(case)
    return ("import pathlib as _pp\n"
            f"{_call_src(sp, case)}\n"
            f"assert _pp.Path({sp.artifact!r}).read_bytes() == {want!r}\n")


def tests_of(sp: Spec, seed: int = SEED, n: int = CASES) -> list[str]:
    # ⚠️ 种子用 **pair** 而不是 id —— 否则配对两版会生成不同用例，S12 就不成立了。
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
                "tags": "external-oracle,artifact,skeleton" if sp.group == "sk"
                        else "external-oracle,artifact,bare",
                "level": "mid",
                "seed": seed,
                "cases": n,
                "family": "artifact",
                "group": sp.group,
                "pair": sp.pair,
                "oracle_source": "CPython stdlib 现算（re/json/datetime/encode 行为）",
                "expect_names": list(sp.expect_names),
            },
        })
    return out


# ===========================================================================
# 判分（与 bench/scorers/code_exec.py 同语义：每条 test 独立子进程 + 独立 cwd）
# ===========================================================================
def score(src: str, tests: list[str], *, timeout_s: float = 30.0) -> int:
    if not src.strip():
        return 0
    hit = 0
    for t in tests:
        code = f"{src}\n\n{t}\n"
        td = tempfile.mkdtemp(prefix="v6score_")
        try:
            try:
                p = subprocess.run([sys.executable, "-I", "-c", code],
                                   capture_output=True, text=True,
                                   timeout=timeout_s, cwd=td)
            except subprocess.TimeoutExpired:
                continue
            if p.returncode == 0:
                hit += 1
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)
    return hit


# ===========================================================================
# S11 用的小工具：删掉「提供某个名字」的那一行 import
# ===========================================================================
def strip_binding(src: str, name: str) -> tuple[str, bool]:
    """删掉提供 `name` 的 import 语句。返回 (新源码, 是否真的删到)。

    ⛔ 学 §75.7.2 的教训：**删不到就抛**，不许静默返回原串 ——
       否则「分数没掉」会被读成「这个名字不影响分数」，
       而真相是「我没删到」。反证失效比没有反证更贵。
    """
    out: list[str] = []
    hit = False
    for line in src.splitlines():
        s = line.strip()
        if not hit:
            m = re.fullmatch(rf"import\s+([\w.]+)(?:\s+as\s+(\w+))?", s)
            if m:
                bound = m.group(2) or m.group(1).split(".")[0]
                if bound == name and not m.group(2):
                    hit = True
                    out.append("# <stripped>")
                    continue
            m = re.fullmatch(r"from\s+[\w.]+\s+import\s+(.+)", s)
            if m:
                parts = [p.strip() for p in m.group(1).split(",")]
                names = [p.split(" as ")[-1].strip() for p in parts]
                if name in names:
                    hit = True
                    keep = [p for p, n in zip(parts, names) if n != name]
                    out.append(f"from {s.split()[1]} import {', '.join(keep)}"
                               if keep else "# <stripped>")
                    continue
        out.append(line)
    if not hit:
        raise AssertionError(f"❌ 源码里没有提供 {name!r} 的 import —— 反证没生效")
    return "\n".join(out) + "\n", hit


# ===========================================================================
# 十三条自检
# ===========================================================================
def self_check() -> int:
    n_sk = sum(1 for s in SPECS if s.group == "sk")
    print("=" * 84)
    print(f"  v6 尺子自检（seed={SEED}，每题 {CASES} 条断言，共 {len(SPECS) * CASES} 格；"
          f"bf={len(SPECS) - n_sk} 题 / sk={n_sk} 题 / {len(_SPECS_BASE)} 对）")
    print("=" * 84)

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

    # S5 / S6
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

    # S7 期望值泄漏
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

    # ── S8 / S9 / S10 骨架三条 ───────────────────────────────────────────
    print("\n[S8/S9/S10] 骨架：零 import / 语法正确 / 恰好缺这些名字且必然 0 分")
    print(f"  {'题':6s} {'import 行':>9s} {'语法':>6s} {'undefined_names':>26s} "
          f"{'分数':>7s}  判定")
    s8 = s9 = s10 = True
    for sp in SPECS:
        if sp.group != "sk":
            continue
        n = len(by_id[sp.id]["tests"])
        imp = re.findall(r"(?m)^\s*(?:import|from)\s", sp.skeleton)
        try:
            compile(sp.skeleton, "<skel>", "exec")
            syn = "✅"
        except SyntaxError as exc:
            syn = f"❌{exc.lineno}"
        names = undefined_names(sp.skeleton)
        got = sorted({nm for _fn, nm in (names or [])})
        want = sorted(sp.expect_names)
        sc = score(sp.skeleton, by_id[sp.id]["tests"])
        ok = (not imp) and syn == "✅" and got == want and sc == 0
        s8 = s8 and not imp
        s9 = s9 and syn == "✅"
        s10 = s10 and got == want and sc == 0
        print(f"  {sp.id:6s} {len(imp):9d} {syn:>6s} {','.join(got) or '—':>26s} "
              f"{sc:3d}/{n:<3d}  {'✅' if ok else '❌'}")
    if not s8:
        bad.append("S8 骨架里有 import")
    if not s9:
        bad.append("S9 骨架语法错")
    if not s10:
        bad.append("S10 骨架没缺名字 / 缺错了名字 / 没必然失败")

    # ── S11 可救反证：逻辑全对、只删 import ⇒ 仍 0 分 ────────────────────
    print("\n[S11] 可救反证：把 oracle 的 import 删掉（逻辑一字不改）⇒ 必须仍 0 分")
    s11 = True
    for sp in SPECS:
        if sp.group != "sk":
            continue
        n = len(by_id[sp.id]["tests"])
        src = sp.oracle
        for name in sp.expect_names:
            src, _ = strip_binding(src, name)
        sc = score(src, by_id[sp.id]["tests"])
        names = undefined_names(src)
        got = sorted({nm for _fn, nm in (names or [])})
        ok = sc == 0 and got == sorted(sp.expect_names)
        s11 = s11 and ok
        print(f"  {sp.id:6s} 删 {','.join(sp.expect_names):18s} ⇒ 分数 {sc:2d}/{n}"
              f"（必须 0）  undefined_names={','.join(got) or '—'}  "
              f"{'✅ 拦路的只有 import' if ok else '❌'}")
    if not s11:
        bad.append("S11")

    # ── S12 配对同一性 ───────────────────────────────────────────────────
    print("\n[S12] 配对同一性：同 pair 的 bf/sk 必须在这些字段上逐字节相同")
    s12 = True
    for base in _SPECS_BASE:
        pid = base["pair"]
        x, y = by_id[f"{pid}bf"], by_id[f"{pid}sk"]
        same = all(x[k] == y[k] for k in ("tests", "oracle", "partial", "null"))
        sx = [sp for sp in SPECS if sp.id == f"{pid}bf"][0]
        sy = [sp for sp in SPECS if sp.id == f"{pid}sk"][0]
        same = same and (sx.spec_impl == sy.spec_impl) and (sx.artifact == sy.artifact)
        s12 = s12 and same
        print(f"  {pid}: tests={len(x['tests'])} 条 oracle={'同' if x['oracle'] == y['oracle'] else '异'}"
              f" partial={'同' if x['partial'] == y['partial'] else '异'}"
              f" spec_impl={'同' if sx.spec_impl == sy.spec_impl else '异'}"
              f" artifact={sx.artifact}  {'✅' if same else '❌'}")
    if not s12:
        bad.append("S12 配对两版其实不是同一道题")

    # ── S13 反证 S12：两版题面真的不同 ──────────────────────────────────
    print("\n[S13] 反证 S12：sk 的题面里**有** TODO 标记，bf 的题面里**没有**")
    tok = "# >>> BEGIN TODO"
    s13 = True
    for base in _SPECS_BASE:
        pid = base["pair"]
        in_sk = tok in by_id[f"{pid}sk"]["prompt"]
        in_bf = tok in by_id[f"{pid}bf"]["prompt"]
        ok = in_sk and not in_bf
        s13 = s13 and ok
        print(f"  {pid}: sk={in_sk} bf={in_bf}  {'✅' if ok else '❌'}")
    if not s13:
        bad.append("S13 两版题面其实一样 ⇒ S12 只证明「我写了两遍」")

    # ── 配对对照表（形态，不声称难度匹配）────────────────────────────────
    print("\n[形态对照] 三对的题面/断言规模（只作可比性陈述，**不声称难度匹配**）")
    print(f"  {'题':6s} {'组':3s} {'规则条数':>8s} {'题面字数':>8s} {'题面行数':>8s} "
          f"{'断言数':>7s} {'点名':>8s}")
    for sp, r in zip(SPECS, rows):
        p = render_prompt(sp)
        print(f"  {sp.id:6s} {sp.group:3s} {len(sp.body):8d} {len(p):8d} "
              f"{len(p.splitlines()):8d} {len(r['tests']):7d} "
              f"{','.join(sp.expect_names) if sp.group == 'sk' else '—':>8s}")

    print()
    if bad:
        print(f"❌ {len(bad)} 条自检失败：{bad}")
        return 1
    print("✅ 十三条自检全绿")
    return 0


def emit(path: str) -> int:
    rows = rows_of()
    Path(path).write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8")
    print(f"已写 {path}（{len(rows)} 题 / "
          f"{sum(len(r['tests']) for r in rows)} 格）")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", nargs="?", const="bench/specgen/tasks_v6.jsonl")
    args = ap.parse_args()
    rc = self_check()
    if args.emit and rc == 0:
        rc = emit(args.emit)
    sys.exit(rc)
