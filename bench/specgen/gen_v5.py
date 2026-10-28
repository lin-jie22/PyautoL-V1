r"""v5 尺子：把「名字类机会」做成**自变量**（§75.9 的 V4′）。

## 这把尺子要回答的问题

§72/§73/§74 量的是「`verify_static` 取几更好」，§75 把那个理由判成**欠定**：

> 「价值 = 题集里有多少失败能被一个名字说清」——换分母就反号。

§75.5 把出路写成 **价值 = 机会 × 兑现**，并要求下一步**同时控制两个因子**。
本尺子只控制第一个：**名字类机会的占比**。

## 怎么把「机会」做成自变量

尺子分两组，**交付形态完全相同**（都是"写一个文件、断言字节"），只差一条：

| 组 | 题 | 交付时**需不需要**一个外部模块 |
|---|---|---|
| `nc`（name-class） | nc01 / nc02 / nc03 | **需要**（`re`+`os` / `json`+`os` / `datetime`+`os`） |
| `ct`（control） | ct01 / ct02 / ct03 | **不需要**（纯 `str`/`dict`/`int` 运算 + `open`） |

⇒ 控制组的「名字没绑」机会**结构上是 0**（oracle 里一个 `import` 都没有，S8 钉住它）。
两组的规则条数、题面长度、断言条数刻意配平（自检会打印这张对照表），
所以「名字机会」是这两组之间**唯一的结构差异**。

⛔ 这**不**证明控制组「更难/更易」：难度没有被匹配，也没有被声称匹配。
  它只保证一件事：`undefined_names` 在控制组上**无事可做**。

## 十一条自检（缺一条这把尺子就不许用）

  S1  oracle 必须满分                 —— 否则题目不可解
  S2  spec_impl（只照题面手写）必须满分 —— 否则题面不充分
  S3  partial 必须**严格居中**         —— 否则断言不区分对错
  S4  null（空串）必须零分             —— 否则空答案也能拿分
  S5  同种子重生成**逐字节相同**       —— 尺子不许漂
  S6  换种子必须**变**                 —— 证明期望值不是写死的
  S7  期望值不许出现在题面里           —— 否则把答案印在卷子上
  S8  ⭐ **结构性对照**：`nc` 组的 oracle 里，删掉提供某个模块的那一行 import
      ⇒ 分数必须下降，且 `mw.verify.undefined_names` 必须**恰好**报出那个名字；
      `ct` 组的 oracle 里 `^\s*(import|from)\s` **一条都不许有**（机会 = 0 是结构的）
  S9  题面必须**显式给出签名**（参数名与顺序）—— §71 的教训，v4 S9 同源
  S10 ⭐ **反证**：按「参数名在规则里首现的顺序」猜签名，必须至少错 1 条
      —— 若 0 条 ⇒ 欠定不存在 ⇒ S9 是装饰，这道闸该删
  S11 ⭐ **反证 S8**：往控制组 oracle 里**人为插入**一个没 import 的 `os.path.dirname`
      ⇒ 检测器必须报 `os`、分数必须掉 —— 否则 S8 控制组那半是「检测器睡着了」

S11 是本人第九次犯「没有反证」的病（§70.4③/§73.5.2/§75.7.2）之后加的硬规定：
**一条恒真的闸等于没有闸，而一条只在甲组有效的闸等于把乙组当摆设。**

## 跑法

    python3 bench/specgen/gen_v5.py            # 十一条自检 + 对照表
    python3 bench/specgen/gen_v5.py --emit     # 写 bench/specgen/tasks_v5.jsonl

⛔ 生成是**构建步骤**：`tasks_v5.jsonl` 一旦提交就冻结，打分永远对着冻结的那份。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import tempfile
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mw.verify import undefined_names  # noqa: E402

SEED = 20261028          #: 固定种子。换它 = 换尺子 = 旧读数失去对照。
CASES = 16               #: 每题断言条数（= 每格题的分母）

_W = ["alpha", "beta", "gamma", "delta", "eps", "zeta", "eta", "theta"]
_CN = ["红", "蓝", "绿"]
_WS = ["  ", "\t", " ", "\t\t"]


# ===========================================================================
# 规格
# ===========================================================================
@dataclass(frozen=True)
class Spec:
    id: str
    fn: str
    group: str                   #: "nc" = 名字类机会 > 0；"ct" = 结构上为 0
    body: tuple[str, ...]
    gen: Callable[[random.Random], Any]
    expect: Callable[[Any], bytes]   #: **独立**算期望值（不许调用 oracle）
    oracle: str
    spec_impl: str
    partial: str
    artifact: str                #: 产物路径（相对 cwd），断言读它的字节
    #: 该题的 oracle 需要哪些**外部模块名**（S8 拿它做删 import 反证）；ct 组必须为空
    modules: tuple[str, ...] = ()


# ── nc01 清标记 + 折叠空白 + 相邻去重 ──────────────────────────────────────
_TAG = re.compile(r"#[A-Za-z0-9_]+")


def _gen_nc01(rng: random.Random) -> list[str]:
    rows: list[str] = []
    for _ in range(rng.randint(3, 6)):
        words = [rng.choice(_W) for _ in range(rng.randint(0, 3))]
        for _ in range(rng.randint(0, 2)):
            words.insert(rng.randint(0, len(words)),
                         "#" + "".join(rng.choice("abz019_")
                                       for _ in range(rng.randint(1, 3))))
        sep = rng.choice(_WS)
        line = sep.join(words) + (rng.choice(_WS) if rng.random() < 0.5 else "")
        if rng.random() < 0.15:
            line += " #"                      # 光秃秃的 `#`：必须保留
        if rng.random() < 0.1:
            line = "# " + line                # `#` 后跟空格：`#` 保留
        rows.append(line)
    if rng.random() < 0.5:                    # 造一个**相邻**重复，逼出规则 4
        i = rng.randrange(len(rows))
        rows.insert(i + 1, rows[i])
    return rows


def _expect_nc01(rows: list[str]) -> bytes:
    kept: list[str] = []
    for row in rows:
        s = _TAG.sub("", row)
        s = " ".join(p for p in re.split(r"[ \t]+", s.strip(" \t")) if p)
        if not s:
            continue
        if kept and kept[-1] == s:
            continue
        kept.append(s)
    return ("".join(x + "\n" for x in kept)).encode("utf-8")


_ORACLE_NC01 = '''import os
import re

_TAG = re.compile(r"#[A-Za-z0-9_]+")


def scrub_tags(rows, path):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    kept = []
    for row in rows:
        s = _TAG.sub("", row)
        s = " ".join(s.split())
        if not s:
            continue
        if kept and kept[-1] == s:
            continue
        kept.append(s)
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        for s in kept:
            fh.write(s + "\\n")
'''

_SPEC_NC01 = '''import pathlib
import re


def scrub_tags(rows, path):
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for r in rows:
        t = re.sub(r"#[A-Za-z0-9_]+", "", r)
        t = re.sub(r"[ \\t]+", " ", t).strip()
        if t == "":
            continue
        if lines and lines[-1] == t:
            continue
        lines.append(t)
    p.write_bytes("".join(x + "\\n" for x in lines).encode("utf-8"))
'''

#: 漏掉规则 4（相邻去重）—— 没有相邻重复的用例照样过 ⇒ 分数居中
_PARTIAL_NC01 = _ORACLE_NC01.replace(
    "        if kept and kept[-1] == s:\n            continue\n", "")


# ── nc02 词频 → JSON ─────────────────────────────────────────────────────
def _gen_nc02(rng: random.Random) -> list[str]:
    pool = _W + _CN
    rows = []
    for _ in range(rng.randint(1, 3)):
        segs = [rng.choice(pool) for _ in range(rng.randint(2, 5))]
        for _ in range(rng.randint(0, 2)):
            segs.insert(rng.randint(0, len(segs)), rng.choice(["", "  "]))
        rows.append(",".join(segs))
    return rows


def _expect_nc02(rows: list[str]) -> bytes:
    counts: dict[str, int] = {}
    total = 0
    for row in rows:
        for w in row.split(","):
            w = w.strip()
            if not w:
                continue
            counts[w] = counts.get(w, 0) + 1
            total += 1
    return json.dumps({"counts": counts, "total": total},
                      ensure_ascii=False, sort_keys=True).encode("utf-8")


_ORACLE_NC02 = '''import json
import os


def save_counts(rows, path):
    counts = {}
    total = 0
    for row in rows:
        for word in row.split(","):
            word = word.strip()
            if word:
                counts[word] = counts.get(word, 0) + 1
                total += 1
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    payload = json.dumps({"counts": counts, "total": total},
                         ensure_ascii=False, sort_keys=True)
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        fh.write(payload)
'''

_SPEC_NC02 = '''import collections
import json
import pathlib


def save_counts(rows, path):
    words = []
    for row in rows:
        words.extend(w.strip() for w in row.split(",") if w.strip())
    ordered = dict(sorted(collections.Counter(words).items()))
    body = json.dumps({"counts": ordered, "total": len(words)},
                      ensure_ascii=False)
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body.encode("utf-8"))
'''

#: 漏掉 `ensure_ascii=False` —— 纯 ASCII 用例照样过 ⇒ 分数居中
_PARTIAL_NC02 = _ORACLE_NC02.replace("ensure_ascii=False, ", "")


# ── nc03 日期跨度 ────────────────────────────────────────────────────────
_LABELS = ["a", "b", "c", "log-1", "x2"]


def _gen_nc03(rng: random.Random) -> list[str]:
    labels = rng.sample(_LABELS, rng.randint(1, 3))
    recs = []
    for lab in labels:
        base = rng.randint(1, 200)
        n = rng.randint(1, 3)
        offs = [base + rng.randint(0, 60) for _ in range(n)]
        if rng.random() < 0.5:          # 一半用例打乱日期顺序，逼出「取最早/最晚」
            rng.shuffle(offs)
        for off in offs:
            # 2026-01-01 起第 off 天（用 stdlib 现算，不手写日历）
            import datetime as _dt
            d = _dt.date(2026, 1, 1) + _dt.timedelta(days=off)
            recs.append(f"{d.isoformat()}={lab}")
    rng.shuffle(recs)
    return recs


def _expect_nc03(records: list[str]) -> bytes:
    import datetime as _dt
    first: dict[str, Any] = {}
    last: dict[str, Any] = {}
    for rec in records:
        day, label = rec.split("=", 1)
        d = _dt.date.fromisoformat(day)
        if label not in first or d < first[label]:
            first[label] = d
        if label not in last or d > last[label]:
            last[label] = d
    return ("".join(f"{k}:{(last[k] - first[k]).days}\n"
                    for k in sorted(last))).encode("utf-8")


_ORACLE_NC03 = '''import os
from datetime import date


def span_days(records, path):
    lo = {}
    hi = {}
    for rec in records:
        day, label = rec.split("=", 1)
        d = date.fromisoformat(day)
        if label not in lo or d < lo[label]:
            lo[label] = d
        if label not in hi or d > hi[label]:
            hi[label] = d
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        for label in sorted(hi):
            fh.write(f"{label}:{(hi[label] - lo[label]).days}\\n")
'''

_SPEC_NC03 = '''import pathlib
from datetime import date as _d


def span_days(records, path):
    boxes = {}
    for rec in records:
        day, label = rec.split("=", 1)
        y, m, dd = day.split("-")
        got = _d(int(y), int(m), int(dd))
        a, b = boxes.get(label, (got, got))
        boxes[label] = (min(a, got), max(b, got))
    lines = [f"{k}:{(v[1] - v[0]).days}\\n" for k, v in sorted(boxes.items())]
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes("".join(lines).encode("utf-8"))
'''

#: 只记「最后一条」当最晚 —— 日期恰好递增的用例照样过 ⇒ 分数居中
_PARTIAL_NC03 = _ORACLE_NC03.replace(
    "        if label not in hi or d > hi[label]:\n            hi[label] = d\n",
    "        hi[label] = d\n")


# ── ct01 排序 + 编号（**零 import**）─────────────────────────────────────
def _gen_ct01(rng: random.Random) -> list[str]:
    rows = []
    for _ in range(rng.randint(3, 6)):
        w = rng.choice(_W)
        pad_l = rng.choice(["", " ", "  ", "\t"])
        pad_r = rng.choice(["", " ", "\t"])
        rows.append(pad_l + w + pad_r)
    if rng.random() < 0.4:
        rows.append("   ")                  # 纯空白：必须跳过
    return rows


def _expect_ct01(rows: list[str]) -> bytes:
    clean = [r.strip() for r in rows if r.strip()]
    order = sorted(range(len(clean)), key=lambda i: (len(clean[i]), clean[i], i))
    return ("".join(f"{n}.{clean[i]}\n" for n, i in enumerate(order, 1))
            ).encode("utf-8")


_ORACLE_CT01 = '''def write_ranked(rows, path):
    clean = []
    for row in rows:
        s = row.strip()
        if s:
            clean.append(s)
    order = sorted(range(len(clean)), key=lambda i: (len(clean[i]), clean[i], i))
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        for n, i in enumerate(order, 1):
            fh.write(str(n) + "." + clean[i] + "\\n")
'''

_SPEC_CT01 = '''def write_ranked(rows, path):
    clean = [r.strip() for r in rows if r.strip()]
    pairs = sorted(enumerate(clean), key=lambda p: (len(p[1]), p[1]))
    lines = [f"{n}.{v}\\n" for n, (_, v) in enumerate(pairs, 1)]
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        fh.write("".join(lines))
'''

#: 漏掉「先按长度」—— 只按字典序；长度序恰好与字典序一致的用例照样过
_PARTIAL_CT01 = _ORACLE_CT01.replace(
    'key=lambda i: (len(clean[i]), clean[i], i)',
    'key=lambda i: (clean[i], len(clean[i]), i)')


# ── ct02 合并映射（**零 import**）────────────────────────────────────────
def _gen_ct02(rng: random.Random) -> tuple[dict[str, str], dict[str, str]]:
    keys = ["a", "b", "k1", "k2", "z"]
    rng.shuffle(keys)
    ka = keys[:rng.randint(1, 3)]
    kb = keys[rng.randint(1, 3):]
    a = {k: rng.choice(["v1", "v2"]) for k in ka}
    b = {k: rng.choice(["", "w1", "w2"]) for k in kb}
    return a, b


def _expect_ct02(case: tuple[dict[str, str], dict[str, str]]) -> bytes:
    a, b = case
    out = dict(a)
    for k, v in b.items():
        if k in out and v == "":
            continue
        out[k] = v
    return ("".join(f"{k}={out[k]}\n" for k in sorted(out))).encode("utf-8")


_ORACLE_CT02 = '''def write_cfg(a, b, path):
    merged = dict(a)
    for key, val in b.items():
        if key in merged and val == "":
            continue
        merged[key] = val
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        for key in sorted(merged):
            fh.write(key + "=" + merged[key] + "\\n")
'''

_SPEC_CT02 = '''def write_cfg(a, b, path):
    merged = dict(a)
    merged.update({k: v for k, v in b.items() if v != "" or k not in a})
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        fh.write("".join(f"{k}={merged[k]}\\n" for k in sorted(merged)))
'''

#: 漏掉「b 是空串时用 a」—— b 里没有空串的用例照样过
_PARTIAL_CT02 = _ORACLE_CT02.replace(
    '        if key in merged and val == "":\n            continue\n', '')


# ── ct03 整数均分（**零 import**）────────────────────────────────────────
def _gen_ct03(rng: random.Random) -> tuple[int, int, bool]:
    total = rng.randint(-14, 60)
    parts = rng.choice([1, 2, 3, 4, 5, 1, 2])
    bad = rng.random() < 0.12
    if bad:
        return total, 0, True
    return total, parts, False


def _expect_ct03(case: tuple[int, int, bool]) -> bytes:
    total, parts, _bad = case
    q, r = divmod(total, parts)
    arr = [q + 1] * r + [q] * (parts - r)
    return ("".join(f"{x}\n" for x in arr)).encode("utf-8")


_ORACLE_CT03 = '''def write_split(total, parts, path):
    if parts < 1:
        raise ValueError("parts must be >= 1")
    q, r = divmod(total, parts)
    arr = [q + 1] * r + [q] * (parts - r)
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        for x in arr:
            fh.write(f"{x}\\n")
'''

_SPEC_CT03 = '''def write_split(total, parts, path):
    if parts < 1:
        raise ValueError("need positive parts")
    base = total // parts
    extra = total % parts
    lines = [f"{base + (1 if i < extra else 0)}\\n" for i in range(parts)]
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        fh.write("".join(lines))
'''

#: 把余数补给**最后** r 份 —— r == 0 的用例照样过
_PARTIAL_CT03 = _ORACLE_CT03.replace(
    "arr = [q + 1] * r + [q] * (parts - r)",
    "arr = [q] * (parts - r) + [q + 1] * r")


# ===========================================================================
# 六个规格的登记
# ===========================================================================
SPECS: tuple[Spec, ...] = (
    Spec(
        id="nc01", fn="scrub_tags", group="nc",
        body=(
            "对每一行，删掉所有**形如 `#` 后紧跟 1 个以上 ASCII 字母、数字或下划线**的标记"
            "（例如 `#ab`、`#9`、`#a_1`）；`#` 后面不是这类字符时（例如行尾单个 `#`、`# foo`），"
            "那个 `#` **保留**",
            "删完后，把连续的空白（空格与制表符）折叠成**一个空格**，再去掉首尾空白",
            "处理完为空串的行**跳过**，不产生输出行",
            "**相邻**两行内容完全相同时只保留第一条；不相邻的重复**保留**",
            "每项输出一行，内容就是处理后的那一行；**每一行**都以 `\\n` 结尾 —— 包括最后一行",
            "用 UTF-8 编码写（不带 BOM），换行符必须是 `\\n` 而不是 `\\r\\n`",
            "`path` 的父目录不存在时要先创建",
            "函数返回 None",
        ),
        gen=_gen_nc01, expect=_expect_nc01,
        oracle=_ORACLE_NC01, spec_impl=_SPEC_NC01, partial=_PARTIAL_NC01,
        artifact="out/s.txt", modules=("os", "re"),
    ),
    Spec(
        id="nc02", fn="save_counts", group="nc",
        body=(
            "每一项按 `,` 切分成词；每一段去掉首尾空白，去掉后为空的段**忽略**",
            "统计每个词出现的次数（**区分大小写**）；同一个词在多行出现要累加",
            "结果写成一个 JSON **对象**，恰好两个键：`\"counts\"` 是一个对象（词 → 次数），"
            "`\"total\"` 是词的总个数（含重复）",
            "所有对象的键都按**字典序升序**排列；不做缩进；分隔符用默认的 `\", \"` 与 `\": \"`",
            "`ensure_ascii=False`（非 ASCII 字符原样写出，不转成 `\\uXXXX`）；**文件末尾不加换行**",
            "用 UTF-8 编码写（不带 BOM）",
            "`path` 的父目录不存在时要先创建",
            "函数返回 None",
        ),
        gen=_gen_nc02, expect=_expect_nc02,
        oracle=_ORACLE_NC02, spec_impl=_SPEC_NC02, partial=_PARTIAL_NC02,
        artifact="out/c.json", modules=("os", "json"),
    ),
    Spec(
        id="nc03", fn="span_days", group="nc",
        body=(
            "每一项用**第一个** `=` 切成两段：左边是日期 `YYYY-MM-DD`，右边是标签（标签里不含 `=`）",
            "对每个标签，取它所有记录里**最早**与**最晚**的日期，算出两者相差的**日历天数**"
            "（最晚 − 最早；同一天为 0）",
            "每个标签输出一行 `标签:天数`，天数是十进制整数（不带前导零）",
            "行按标签的**字典序（ASCII）升序**排列",
            "每一行都以 `\\n` 结尾 —— 包括最后一行；用 UTF-8 编码写",
            "`path` 的父目录不存在时要先创建",
            "函数返回 None",
        ),
        gen=_gen_nc03, expect=_expect_nc03,
        oracle=_ORACLE_NC03, spec_impl=_SPEC_NC03, partial=_PARTIAL_NC03,
        artifact="out/d.txt", modules=("os", "date"),
    ),
    Spec(
        id="ct01", fn="write_ranked", group="ct",
        body=(
            "每一项去掉首尾空白；去掉后为空串的**跳过**",
            "排序：先按**长度**（字符个数）升序；长度相同按**原值**的 ASCII 字典序升序；"
            "前两条都相同的项，**较早出现的排在前面**（稳定）",
            "每项输出一行，格式 `序号.值`，序号从 `1` 开始、按输出顺序递增",
            "每一行都以 `\\n` 结尾 —— 包括最后一行",
            "用 UTF-8 编码写（不带 BOM），换行符必须是 `\\n`",
            "不需要创建目录：`path` 的父目录**总是已存在**",
            "函数返回 None",
        ),
        gen=_gen_ct01, expect=_expect_ct01,
        oracle=_ORACLE_CT01, spec_impl=_SPEC_CT01, partial=_PARTIAL_CT01,
        artifact="r.txt",
    ),
    Spec(
        id="ct02", fn="write_cfg", group="ct",
        body=(
            "合并两个映射：键只在一边出现 ⇒ 用那一边的值",
            "两边都有同一个键 ⇒ 若 `b` 的值是**空串**，用 `a` 的值；否则用 `b` 的值",
            "每个键输出一行 `键=值`，按键的 ASCII 字典序升序排列",
            "值为空串的键**照样输出**（例如 `k=`，等号后面什么都没有）",
            "每一行都以 `\\n` 结尾 —— 包括最后一行；用 UTF-8 编码写",
            "不需要创建目录：`path` 的父目录**总是已存在**",
            "函数返回 None",
        ),
        gen=_gen_ct02, expect=_expect_ct02,
        oracle=_ORACLE_CT02, spec_impl=_SPEC_CT02, partial=_PARTIAL_CT02,
        artifact="c.txt",
    ),
    Spec(
        id="ct03", fn="write_split", group="ct",
        body=(
            "`parts` 小于 1 时抛 `ValueError`（消息任意），且**不许**留下任何文件",
            "把 `total`（整数，可为负）分成 `parts` 份整数，使各份之和**恰好**等于 `total`",
            "先每份取 `total // parts`（向下取整）；余数 `r = total % parts`（`0 <= r < parts`）；"
            "**前 `r` 份**各加 1",
            "每份输出一行，十进制整数（负数带 `-`），按份的顺序（第 1 份在最前）",
            "每一行都以 `\\n` 结尾；用 UTF-8 编码写",
            "不需要创建目录：`path` 的父目录**总是已存在**",
            "函数返回 None",
        ),
        gen=_gen_ct03, expect=_expect_ct03,
        oracle=_ORACLE_CT03, spec_impl=_SPEC_CT03, partial=_PARTIAL_CT03,
        artifact="s.txt",
    ),
)


# ===========================================================================
# 题面与断言
# ===========================================================================
def sig_of(sp: Spec) -> str:
    m = re.search(rf"def\s+{re.escape(sp.fn)}\s*\(([^)]*)\)", sp.oracle)
    if not m:
        raise SystemExit(f"❌ {sp.id}: oracle 里找不到 def {sp.fn}(...)")
    return m.group(1).strip()


def render_prompt(sp: Spec) -> str:
    rules = "\n".join(f"  {i + 1}. {b}" for i, b in enumerate(sp.body))
    return (f"写一个函数 `{sp.fn}`，函数签名**必须**是 "
            f"`def {sp.fn}({sig_of(sp)})`（参数名与顺序照此写，不要改）。"
            f"按下面列出的规则实现：\n{rules}\n"
            f"只输出代码，函数名必须是 {sp.fn}。")


def _call_src(sp: Spec, case: Any) -> str:
    if isinstance(case, tuple):
        return f"{sp.fn}(" + ", ".join(repr(x) for x in case) + f", {sp.artifact!r})"
    return f"{sp.fn}({case!r}, {sp.artifact!r})"


def _one_test(sp: Spec, case: Any) -> str:
    head = "import pathlib as _pp\n"
    if sp.id == "ct03":
        # ⚠️ 自踩：`case` 是 `(total, parts, bad)`，`bad` 是**判分侧标记**，
        #    不是被测函数的参数 —— 通用展开会多传一个位置参数（16/16 全 0）。
        #    与 v4 `at04` 的 `bad` 同族，写清楚免得下次再犯。
        total, parts, bad = case
        if bad:
            return head + (
                "_ok = False\n"
                f"try:\n    {sp.fn}({total!r}, {parts!r}, {sp.artifact!r})\n"
                "except ValueError:\n    _ok = True\n"
                "assert _ok is True, 'must raise ValueError'\n"
                f"assert not _pp.Path({sp.artifact!r}).exists(), "
                "'must not leave a file'\n")
        call = f"{sp.fn}({total!r}, {parts!r}, {sp.artifact!r})"
    else:
        call = _call_src(sp, case)
    want = sp.expect(case)
    return (head + f"{call}\n"
            f"assert _pp.Path({sp.artifact!r}).read_bytes() == {want!r}\n")


def tests_of(sp: Spec, seed: int = SEED, n: int = CASES) -> list[str]:
    rng = random.Random(f"{seed}:{sp.id}")
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
                "tags": "external-oracle,artifact,name-class" if sp.group == "nc"
                        else "external-oracle,artifact,control",
                "level": "mid",
                "seed": seed,
                "cases": n,
                "family": "artifact",
                "group": sp.group,
                "oracle_source": "CPython stdlib 现算（re/json/datetime/encode 行为）",
                "modules": list(sp.modules),
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
        td = tempfile.mkdtemp(prefix="v5score_")
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
# S8 用的小工具：删掉「提供某个名字」的那一行 import
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
# 十一条自检
# ===========================================================================
def self_check() -> int:
    print("=" * 82)
    print(f"  v5 尺子自检（seed={SEED}，每题 {CASES} 条断言，"
          f"共 {len(SPECS) * CASES} 格；"
          f"nc={sum(1 for s in SPECS if s.group == 'nc')} 题 / "
          f"ct={sum(1 for s in SPECS if s.group == 'ct')} 题）")
    print("=" * 82)

    rows = rows_of()
    bad: list[str] = []

    print("\n[S1/S2/S3/S4] 逐题标定")
    print(f"  {'题':5s} {'组':3s} {'oracle':>8s} {'spec':>7s} {'partial':>9s} "
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
        print(f"  {sp.id:5s} {sp.group:3s} {o:4d}/{n:<3d} {s:4d}/{n:<2d} "
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

    # ── S8 结构性对照 ───────────────────────────────────────────────────
    print("\n[S8] 结构性对照：nc 组「删 import ⇒ 分数掉 + 恰好报出这个名字」；"
          "ct 组「零 import」")
    s8 = True
    for sp, r in zip(SPECS, rows):
        n = len(r["tests"])
        if sp.group == "ct":
            got = re.findall(r"(?m)^\s*(?:import|from)\s", sp.oracle)
            if got:
                s8 = False
                print(f"  {sp.id}: ❌ oracle 里有 import ⇒ 控制组的名字机会不为 0")
            else:
                print(f"  {sp.id}: ✅ 零 import（机会结构上为 0）")
            continue
        for name in sp.modules:
            stripped, _ = strip_binding(sp.oracle, name)
            sc = score(stripped, r["tests"])
            names = undefined_names(stripped)
            hit = [nm for _fn, nm in (names or [])]
            ok = sc < n and hit == [name]
            s8 = s8 and ok
            print(f"  {sp.id} 去掉 {name:8s} ⇒ 分数 {sc:2d}/{n}"
                  f"（必须 < {n}）  undefined_names={hit}  "
                  f"{'✅' if ok else '❌'}")
    if not s8:
        bad.append("S8")

    # ── S9 题面显式给出签名 ─────────────────────────────────────────────
    miss9 = [sp.id for sp in SPECS
             if f"def {sp.fn}({sig_of(sp)})" not in render_prompt(sp)]
    s9 = not miss9
    print(f"\n[S9] 题面显式给出签名：{'✅ 6/6' if s9 else '❌ 缺 ' + str(miss9)}")
    if not s9:
        bad.append("S9")

    # ── S10 反证：按规则首现顺序猜签名，必须至少错 1 条 ──────────────────
    body_text = {sp.id: "\n".join(sp.body) for sp in SPECS}
    guess_bad = []
    for sp in SPECS:
        true_params = [p.strip() for p in sig_of(sp).split(",") if p.strip()]
        firsts = [(body_text[sp.id].find(p), p) for p in true_params]
        mentioned = sorted((pos, p) for pos, p in firsts if pos >= 0)
        guessed = [p for _pos, p in mentioned]
        wanted = [p for p in true_params if p in guessed]
        if guessed != wanted:
            guess_bad.append(f"{sp.id}: 猜={','.join(guessed)} "
                             f"实际={','.join(wanted)}")
    s10 = bool(guess_bad)
    print(f"[S10] 反证「按规则首现顺序猜参数」：{len(guess_bad)} 条会猜错 "
          f"{'✅' if s10 else '❌ 0 条 ⇒ S9 是装饰，可以删'}")
    for g in guess_bad:
        print(f"      {g}")
    if not s10:
        bad.append("S10")

    # ── S11 反证 S8：往控制组里人为注入一个没绑的名字 ────────────────────
    print("\n[S11] 反证 S8：控制组 ct01 里插入未 import 的 `os.path.dirname`")
    injected = _ORACLE_CT01.replace(
        "    order = sorted",
        '    os.path.dirname(path)\n    order = sorted')
    if injected == _ORACLE_CT01:
        raise AssertionError("❌ 注入没生效（模式不匹配）—— 与 §75.7.2 同族")
    t1 = [r for sp, r in zip(SPECS, rows) if sp.id == "ct01"][0]
    sc_inj = score(injected, t1["tests"])
    hit_inj = [nm for _fn, nm in (undefined_names(injected) or [])]
    s11 = sc_inj < len(t1["tests"]) and hit_inj == ["os"]
    print(f"  注入后 分数 {sc_inj}/{len(t1['tests'])}（必须 < "
          f"{len(t1['tests'])}）  undefined_names={hit_inj}  "
          f"{'✅ 检测器没睡' if s11 else '❌ 检测器对控制组无感 ⇒ S8 那半是摆设'}")
    if not s11:
        bad.append("S11")

    # ── 组间可比的**形态**对照（不声称难度匹配）──────────────────────────
    print("\n[形态对照] 两组的题面/断言规模（只作可比性陈述，**不声称难度匹配**）")
    print(f"  {'题':5s} {'组':3s} {'规则条数':>8s} {'题面字数':>8s} {'断言数':>7s} "
          f"{'须创建目录':>10s} {'需外部模块':>10s}")
    for sp, r in zip(SPECS, rows):
        need_mkdir = "是" if sp.artifact.startswith("out/") else "否"
        print(f"  {sp.id:5s} {sp.group:3s} {len(sp.body):8d} "
              f"{len(render_prompt(sp)):8d} {len(r['tests']):7d} "
              f"{need_mkdir:>10s} {','.join(sp.modules) or '—':>10s}")

    print()
    if bad:
        print(f"❌ {len(bad)} 条自检失败：{bad}")
        return 1
    print("✅ 十一条自检全绿")
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
    ap.add_argument("--emit", nargs="?", const="bench/specgen/tasks_v5.jsonl")
    args = ap.parse_args()
    rc = self_check()
    if args.emit and rc == 0:
        rc = emit(args.emit)
    sys.exit(rc)
