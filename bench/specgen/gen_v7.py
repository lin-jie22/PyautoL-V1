r"""v7 尺子：**拆掉约束冲突**（§77.8 的 V4‴）+ 三处同时修（V7 长度 / V8 建目录）。

## 上一轮卡在哪

§77 证明了两件事：

1. **机会可控**：`sk` 组（骨架故意缺 import）两臂都 **3/3 题触发**，
   12 份唯一交付**一行 import 都没补** ⇒ 机会 100%。
2. **兑现 0**：末答的名字集合与首答**逐题完全相同** ⇒ 闸报了名字，分数一点没动。

兑现为 0 有**两个各自充分、当轮无法分离**的原因：

- ① **约束冲突**：题面写死「**不要**添加任何 import」，闸说「未定义的名字 `os`」——
  模型服从题面 ⇒ "被允许的修法"与"闸要求的修法"互斥。
- ② **没有时间**：打回发生在预算耗尽前 11 秒（`23:06:22 报 os` → `23:06:33 预算 75.5/120`）。

## 这一轮的三处改动

| # | 改动 | 为什么 |
|---|---|---|
| **V4‴** | 题面从「**不许**加 import」改成「**允许**在文件最顶部添加 import」 | 拆掉 ① |
| **V7** | `V1_MAX_WALL_S` 120 → **240**（**两臂同抬**） | 拆掉 ②；§77.6.4 的"距预算 11 秒"必须消失 |
| **V8** | 交付物从 `out/t.txt` 移到 **cwd 根的 `t.txt`**，并**彻底不用 `os`** | §77.5.1：`bf` 组 5/6 次把 `makedirs` 写在 `open()` 之后 ⇒ `FileNotFoundError` ⇒ **主导失败模式跑偏** |

## 配对方式也换了（这是本轮最重要的方法学改动）

v6 的配对是「`bf` 从零写」vs「`sk` 给骨架」，两者**结构迥异**：
题面长度差 2.2 倍、`sk` 免建目录而 `bf` 不免 ⇒ 除了"有没有 import"之外还有**两个未控制变量**。

v7 的配对改成 **± 一行 import**：

| 组 | 骨架 | 差别 |
|---|---|---|
| `wi`（with import） | `import re` + 共同骨架 | **唯一**的差别就是这一行 |
| `ni`（no import） | 共同骨架 | 同上 |

S13 逐字节钉住 `wi.skeleton == "import <m>\n\n" + ni.skeleton`。
⇒ 两组的 tests / oracle / partial / spec_impl / artifact / 题面长度**全部相同**，
   差别只剩"文件顶部有没有那一行 import"。

`wi` 组的机会**结构上恒为 0**（import 在位），`ni` 组的机会**结构上恒为 100%**（S8/S9/S10 钉住）。
⇒ 这一轮不再问"机会能不能造出来"（§77 已回答），只问一件事：

> **闸报出名字以后，模型会不会补上那一行、分数会不会动。**

`wi` 组是**同一道题、同一个骨架**的干净对照（`need_eq` 用），不需要跨题可比性假设。

## 缺的名字：一题只缺一个

| 对 | 函数 | 缺的名字 | 交付物（cwd 根） |
|---|---|---|---|
| p1 | `tag_report` | `re` | `t.txt` |
| p2 | `save_roster` | `json` | `r.json` |
| p3 | `week_buckets` | `datetime` | `w.txt` |

v6 每组缺 **两个**（`os` + `re/json/datetime`），读数里说不清是谁拦路；v7 只缺一个。

## 骨架为什么变长

v6 的骨架 10~15 行，缺的名字写在 TODO 上方两行 ⇒ **一眼可见**，等于替模型做完了检查。
v7 的骨架 16~25 行，缺的名字**藏在已经写好的辅助函数里**（`_fold` / `_dump` / `_monday_of`），
TODO 只要求补主函数体。⇒ 只盯着 TODO 的模型不会注意到顶部缺 import。

⛔ 这不改变测量目标：机会仍是**给定状态**（S8/S9/S10 钉住），只是**不再白送**。

## 合法性边界

`ni` 组的机会仍是**人为造出来**的：真实用户不会交给你一段"顶部没有 import 的半成品"。
因此本尺子只能说到：

> **在"缺 import 是给定状态、且允许补 import"的条件下，闸报名字能不能把分数抬起来**

⛔ 不能说到"自然题面里机会有多少"，也不能说到"哪种题面更好"。

## 十六条自检（缺一条这把尺子就不许用）

  S1  oracle 满分                     —— 否则题目不可解
  S2  spec_impl（只照题面手写）满分   —— 否则题面不充分
  S3  partial 严格居中                —— 否则断言不区分对错
  S4  null（空串）零分                —— 否则空答案也能拿分
  S5  同种子重生成**逐字节相同**      —— 尺子不许漂
  S6  换种子必须**变**                —— 证明期望值不是写死的
  S7  期望值不许出现在题面里          —— 否则把答案印在卷子上
  S8  ⭐ `ni` 骨架里 `^\s*(import|from)\s` **一条都没有**
  S9  ⭐ 骨架必须**语法正确**         —— 否则 `undefined_names` 返回 None
  S10 ⭐⭐ `undefined_names(ni 骨架)` **恰好**等于预期名字集（**只有一个**），且 `score(ni 骨架)==0`
  S11 ⭐⭐ 可救反证：把 oracle 的 import **删掉**（逻辑全对）⇒ 分数仍为 0、名字仍缺
  S12 ⭐⭐⭐ 配对同一性：同 `pair` 的 tests/oracle/partial/null/spec_impl/artifact 逐字节相同
  S13 ⭐⭐⭐ **± 一行 import 的同一性**：`wi.skeleton == "import <m>\n\n" + ni.skeleton`（逐字节），
      且 `undefined_names(wi 骨架)` 为空、`undefined_names(ni 骨架)` 非空
      —— 这是本轮唯一的方法学主张，没有这条整轮不成立
  S14 ⭐⭐ **反证"冲突已拆除"**：`ni` 题面**不许**出现禁止 import 的字样，且**必须**出现允许字样
  S15 ⭐ **V8 反证**：三道题的 artifact 都**不含 `/`**，且每条断言写的都是同一个根路径
  S16 ⭐ **V7 配平**：`ni` / `wi` 题面字数比 ≤ 1.30（并印出实际字数）

## 跑法

    python3 bench/specgen/gen_v7.py            # 十六条自检
    python3 bench/specgen/gen_v7.py --emit     # 写 bench/specgen/tasks_v7.jsonl

⛔ `tasks_v7.jsonl` 一旦 `--emit` 就冻结，打分永远对着冻结的那份。
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

SEED = 20261030          #: 固定种子。换它 = 换尺子 = 旧读数失去对照。
CASES = 16               #: 每题断言条数（= 每格题的分母）
GROUPS = ("wi", "ni")    #: wi = 骨架顶部**有** import；ni = **没有**

_W = ["alpha", "beta", "gamma", "delta", "eps", "zeta", "eta", "theta"]
_CN = ["红", "蓝", "绿", "紫"]
_WS = ["  ", "\t", " ", "\t\t"]


# ===========================================================================
# 规格
# ===========================================================================
@dataclass(frozen=True)
class Spec:
    id: str
    pair: str
    fn: str
    group: str                   #: "wi" / "ni"
    body: tuple[str, ...]
    gen: Callable[[random.Random], Any]
    expect: Callable[[Any], bytes]
    oracle: str
    spec_impl: str
    partial: str
    artifact: str                #: **cwd 根**下的文件名（S15 钉住：不许含 `/`）
    skeleton_body: str = ""      #: 共同骨架（**零 import**）；ni 用它，wi 在它前面加一行 import
    expect_names: tuple[str, ...] = ()   #: ni 骨架缺的名字（S10 拿它做反证）


def skeleton_of(sp: Spec) -> str:
    """`wi` = 一行 import + 共同骨架；`ni` = 共同骨架。S13 钉住这个等式。"""
    if sp.group == "wi":
        return "".join(f"import {nm}\n" for nm in sp.expect_names) + "\n" + sp.skeleton_body
    return sp.skeleton_body


# ── 期望值用的手工原语（不调 re / 不调 oracle）────────────────────────────
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


def _fold_ws(s: str) -> str:
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
    if '"' in s or "\\" in s:
        raise AssertionError(f"❌ 生成的名字含需要转义的字符：{s!r}")
    return '"' + s + '"'


# ── p1：清标记 + 折叠空白 + 全局去重 + 编号（缺 re）──────────────────────
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
            line += " @"
        if rng.random() < 0.1:
            line = "@ " + line
        items.append(line)
    if len(items) >= 3 and rng.random() < 0.7:
        items.insert(0, items[-1])
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


_ORACLE_P1 = '''import re

_TAG = re.compile(r"@[A-Za-z0-9]+")


def tag_report(items, path):
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

_SPEC_P1 = '''import re


def tag_report(items, path):
    kept = []
    for line in items:
        t = re.sub(r"@[A-Za-z0-9]+", "", line)
        t = " ".join(t.replace("\\t", " ").split())
        if t == "":
            continue
        if t in kept:
            continue
        kept.append(t)

    handle = open(path, "w", encoding="utf-8")
    try:
        for n, t in enumerate(kept):
            handle.write(str(n + 1) + "." + t + "\\n")
    finally:
        handle.close()
    return None
'''

_PARTIAL_P1 = _ORACLE_P1.replace(
    "        if not s or s in index:\n            continue\n",
    "        if not s:\n            continue\n")

#: ⚠️ 缺的名字 `re` 只出现在**已经写好的** `_fold` 里 —— TODO 区块本身不碰 re。
_SKELETON_P1 = '''_SEP = " \\t"


def _fold(text):
    return re.sub(r"[ \\t]+", " ", text.strip(_SEP))


def _number(rows):
    return "".join(f"{i}.{s}\\n" for i, s in enumerate(rows, 1))


def tag_report(items, path):
    _tag = re.compile(r"@[A-Za-z0-9]+")
    seen = []
    # >>> BEGIN TODO
    # 补全：遍历 items，按规则 1~4 生成 seen（处理后的字符串列表）。
    # _tag.sub("", s) 删标记；_fold(text) 折叠空白；_number(rows) 拼输出文本。
    # >>> END TODO

    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        fh.write(_number(seen))
    return None
'''


# ── p2：合并成 JSON（缺 json）─────────────────────────────────────────────
def _gen_p2(rng: random.Random) -> list[str]:
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
            items.append(f"|{name}")
        else:
            items.append(f"{pad}{g}{pad}|{pad}{name}{pad}")
    if rng.random() < 0.5:
        items.append(items[0])
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


def _dump(payload):
    return json.dumps(payload, ensure_ascii=False)


def save_roster(items, path):
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
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_dump({"groups": ordered}))
    return None
'''

_SPEC_P2 = '''import json


def save_roster(items, path):
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

#: ⚠️ 缺的名字 `json` 只在已经写好的 `_dump` 里 —— 主函数体要用它但不必 import。
_SKELETON_P2 = '''def _dump(payload):
    return json.dumps(payload, ensure_ascii=False)


def save_roster(items, path):
    table = {}
    # >>> BEGIN TODO
    # 补全：遍历 items，按规则 1~4 填满 table（组名 -> 名字列表）。
    # 列表顺序与去重都由规则决定。_dump(payload) 负责序列化。
    # >>> END TODO

    ordered = {k: table[k] for k in sorted(table)}
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_dump({"groups": ordered}))
    return None
'''


# ── p3：按 ISO 周分桶（缺 datetime）───────────────────────────────────────
def _gen_p3(rng: random.Random) -> list[str]:
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
        key = _dt.date.fromordinal(d.toordinal() - d.weekday()).isoformat()
        bucket = buckets.setdefault(key, [])
        if ev not in bucket:
            bucket.append(ev)
    return "".join(k + "|" + ",".join(buckets[k]) + "\n"
                   for k in sorted(buckets)).encode("utf-8")


_ORACLE_P3 = '''import datetime


def _monday_of(day):
    return (day - datetime.timedelta(days=day.weekday())).isoformat()


def week_buckets(items, path):
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


def week_buckets(items, path):
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

#: ⚠️ 缺的名字 `datetime` 只在已经写好的两个辅助函数里。
_SKELETON_P3 = '''def _monday_of(day):
    return (day - datetime.timedelta(days=day.weekday())).isoformat()


def _read_day(stamp):
    return datetime.date.fromisoformat(stamp)


def week_buckets(items, path):
    buckets = {}
    # >>> BEGIN TODO
    # 补全：遍历 items，按规则 1~4 填满 buckets。
    # 键是那一周周一的 ISO 日期串，值是事件名列表。
    # _read_day(stamp) 解析日期（非法会抛 ValueError）；_monday_of(day) 取周一。
    # >>> END TODO

    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        for key in sorted(buckets):
            fh.write(key + "|" + ",".join(buckets[key]) + "\\n")
    return None
'''


# ===========================================================================
# 六个规格（三对 × ± import）
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
            "函数返回 None",
        ),
        gen=_gen_p1, expect=_expect_p1,
        oracle=_ORACLE_P1, spec_impl=_SPEC_P1, partial=_PARTIAL_P1,
        skeleton_body=_SKELETON_P1, expect_names=("re",),
        artifact="t.txt",
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
            "函数返回 None",
        ),
        gen=_gen_p2, expect=_expect_p2,
        oracle=_ORACLE_P2, spec_impl=_SPEC_P2, partial=_PARTIAL_P2,
        skeleton_body=_SKELETON_P2, expect_names=("json",),
        artifact="r.json",
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
            "函数返回 None",
        ),
        gen=_gen_p3, expect=_expect_p3,
        oracle=_ORACLE_P3, spec_impl=_SPEC_P3, partial=_PARTIAL_P3,
        skeleton_body=_SKELETON_P3, expect_names=("datetime",),
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
    "  - 断言比较的是**字节**：少一个尾随换行、多一个空格、`\\r\\n` 写成 `\\n` 都会判错；\n"
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
    "  - 顶部需要的 `import` 已经写好了，照原样留着即可。\n"
)

_EDIT_NI = (
    "\n编辑约定：\n"
    "  - 补全 `# >>> BEGIN TODO` 与 `# >>> END TODO` 之间的内容；\n"
    "  - 如果补全需要外部模块，**允许**你在**文件最顶部添加 `import` 语句**"
    "（加几条都行，加了不会被扣分）；\n"
    "  - 除了上面两件事，其余每一行必须**逐字保留**（缩进、空行、注释都一样）。\n"
)


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
    want = sp.expect(case)
    return ("import pathlib as _pp\n"
            f"{_call_src(sp, case)}\n"
            f"assert _pp.Path({sp.artifact!r}).read_bytes() == {want!r}\n")


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
                "tags": f"external-oracle,artifact,skeleton-{sp.group}",
                "level": "mid",
                "seed": seed,
                "cases": n,
                "family": "artifact",
                "group": sp.group,
                "pair": sp.pair,
                "oracle_source": "CPython stdlib 现算（re/json/datetime/encode 行为）",
                "expect_names": list(sp.expect_names),
                "import_allowed": sp.group == "ni",
            },
        })
    return out


# ===========================================================================
# 判分（与 bench/scorers/code_exec.py 同语义：每条 test 独立子进程 + 独立 cwd）
# ===========================================================================
def score(src: str, tests: list[str], *, timeout_s: float = 30.0) -> int:
    if not src.strip():
        return 0
    import shutil
    hit = 0
    for t in tests:
        code = f"{src}\n\n{t}\n"
        td = tempfile.mkdtemp(prefix="v7score_")
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
            shutil.rmtree(td, ignore_errors=True)
    return hit


# ===========================================================================
# S11 用的小工具：删掉「提供某个名字」的那一行 import
# ===========================================================================
def strip_binding(src: str, name: str) -> tuple[str, bool]:
    """删掉提供 `name` 的 import 语句。**删不到就抛**（§75.7.2 的教训）。"""
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
# 十六条自检
# ===========================================================================
_FORBID = ("不要添加", "不许加", "不许添加", "禁止", "由打包脚本统一注入", "不要写 import")


def self_check() -> int:
    n_ni = sum(1 for s in SPECS if s.group == "ni")
    print("=" * 88)
    print(f"  v7 尺子自检（seed={SEED}，每题 {CASES} 条断言，共 {len(SPECS) * CASES} 格；"
          f"wi={len(SPECS) - n_ni} 题 / ni={n_ni} 题 / {len(_SPECS_BASE)} 对）")
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

    # ── S8 / S9 / S10 ────────────────────────────────────────────────────
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
        bad.append("S10 骨架没缺名字 / 缺错了名字 / 没必然失败 / 缺的不止一个")

    # ── S11 可救反证 ─────────────────────────────────────────────────────
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
        names = undefined_names(src)
        got = sorted({nm for _fn, nm in (names or [])})
        ok = sc == 0 and got == sorted(sp.expect_names)
        s11 = s11 and ok
        print(f"  {sp.id:6s} 删 {','.join(sp.expect_names):10s} ⇒ 分数 {sc:2d}/{n}"
              f"（必须 0）  undefined_names={','.join(got) or '—'}  "
              f"{'✅ 拦路的只有 import' if ok else '❌'}")
    if not s11:
        bad.append("S11")

    # ── S12 配对同一性 ───────────────────────────────────────────────────
    print("\n[S12] 配对同一性：同 pair 的 wi/ni 必须在这些字段上逐字节相同")
    s12 = True
    for base in _SPECS_BASE:
        pid = base["pair"]
        x, y = by_id[f"{pid}wi"], by_id[f"{pid}ni"]
        same = all(x[k] == y[k] for k in ("tests", "oracle", "partial", "null"))
        sx = [sp for sp in SPECS if sp.id == f"{pid}wi"][0]
        sy = [sp for sp in SPECS if sp.id == f"{pid}ni"][0]
        same = same and (sx.spec_impl == sy.spec_impl) and (sx.artifact == sy.artifact)
        s12 = s12 and same
        print(f"  {pid}: tests={len(x['tests'])} 条 oracle={'同' if x['oracle'] == y['oracle'] else '异'}"
              f" partial={'同' if x['partial'] == y['partial'] else '异'}"
              f" spec_impl={'同' if sx.spec_impl == sy.spec_impl else '异'}"
              f" artifact={sx.artifact}  {'✅' if same else '❌'}")
    if not s12:
        bad.append("S12 配对两版其实不是同一道题")

    # ── S13 ⭐ ± 一行 import 的同一性 ────────────────────────────────────
    print("\n[S13] ⭐ ± 一行 import：wi 骨架 == 'import <m>\\n\\n' + ni 骨架（逐字节）")
    s13 = True
    for base in _SPECS_BASE:
        pid = base["pair"]
        sx = [sp for sp in SPECS if sp.id == f"{pid}wi"][0]
        sy = [sp for sp in SPECS if sp.id == f"{pid}ni"][0]
        want = "".join(f"import {nm}\n" for nm in sy.expect_names) + "\n" + skeleton_of(sy)
        same = skeleton_of(sx) == want
        wi_names = undefined_names(skeleton_of(sx))
        ni_names = undefined_names(skeleton_of(sy))
        wi_clean = not (wi_names or [])
        ni_dirty = bool(ni_names or [])
        ok = same and wi_clean and ni_dirty
        s13 = s13 and ok
        print(f"  {pid}: wi 骨架逐字节=ni+import {same}  "
              f"undefined(wi)={wi_names or '空'}  undefined(ni)={sorted({n for _f, n in ni_names or []})}  "
              f"{'✅' if ok else '❌'}")
    if not s13:
        bad.append("S13 wi 骨架不是 ni 骨架 + 一行 import")

    # ── S14 ⭐ 反证"冲突已拆除" ─────────────────────────────────────────
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
        if forb:
            print(f"          ⛔ 题面里还有「{forb[0]}」⇒ 与闸的修复指令仍然冲突")
    if not s14:
        bad.append("S14 ni 题面仍禁止 import / 没有允许字样")

    # ── S15 ⭐ V8 反证：artifact 不许含 `/` ──────────────────────────────
    print("\n[S15] ⭐ V8：artifact 不含路径分隔符（cwd 根），且断言里用的是同一个根路径")
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

    # ── S16 ⭐ V7 配平：题面字数比 ───────────────────────────────────────
    print("\n[S16] ⭐ V7 配平：ni / wi 题面字数比必须 ≤ 1.30")
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

    print()
    if bad:
        print(f"❌ {len(bad)} 条自检失败：{bad}")
        return 1
    print("✅ 十六条自检全绿")
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
    ap.add_argument("--emit", nargs="?", const="bench/specgen/tasks_v7.jsonl")
    args = ap.parse_args()
    rc = self_check()
    if args.emit and rc == 0:
        rc = emit(args.emit)
    sys.exit(rc)
