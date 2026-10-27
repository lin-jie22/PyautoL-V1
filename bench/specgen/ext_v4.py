"""v4 尺子：**答案的交付物不是返回值，是它留下的东西。**

这把尺子要修的，是 v1~v3 共同的**形态局限**。

    26 题（v1 6 + v2 10 + v3 10）的答案全是「一个纯函数」，
    断言全是 `assert f(x) == y`。
    ⇒ 它们测的是「算得对不对」，一条都没有测「做成了没有」。

而 agent 在真实工作里交付的是**文件系统状态**（改了哪些文件、内容对不对、
重复跑会不会翻倍、失败时有没有留下半成品），不是返回值。
§69 把这条登记为 S1 台阶：「尺子上至少要有一题需要副作用/多步」。

## 外部性分几层（比 v3 弱一层，不许混说）

| 层 | 谁给的 | 本文件 |
|---|---|---|
| 输入 | 随机器（种子固定，我不挑） | ✅ 外部 |
| 期望值 | **CPython stdlib 现算**（`json.dumps` / `.encode` / 换行符行为） | ✅ 外部 |
| 规格文字 | 我写的 | ❌ 仍是自证 |
| 「该产出什么文件」 | 我指定的 | ❌ 仍是自证 |

⛔ 所以这把尺子**不能**声称「期望值不是我决定的」：
   我决定了「产物长什么样」，只没有决定「具体字节」（那由 stdlib 现算）。
   这与 v3 的差别要说清：v3 的期望值是 CPython **自己的实现**（`str.split`），
   v4 的期望值是 CPython **自己的序列化行为** + 我的格式规定。
   ⇒ 弱一层。登记在案，不许当成 v3 的加强版来引用。

## 十条自检（缺一条这把尺子就不许用）

  S1 oracle（参考解）必须满分                —— 否则题目不可解
  S2 spec_impl（只照规格文字手写）必须满分    —— 否则规格不充分
  S3 partial（故意漏一条规则）必须**严格居中** —— 否则断言不区分对错
  S4 null（空串）必须零分                    —— 否则空答案也能拿分
  S5 同种子重生成必须**逐字节相同**          —— 尺子不许漂
  S6 换种子必须**变**                        —— 证明期望值不是写死的
  S7 期望值不许出现在题面里                  —— 否则把答案印在卷子上
  S8 ⭐ **pure（只算对、不落盘）必须低分**     —— 这是本尺子存在的理由：
       一个返回值完全正确但什么都没写出去的实现，在 v1~v3 上会拿满分，
       在 v4 上必须接近零分。**证明这把尺子测的是另一件事。**
  S9 题面必须**显式给出签名**（参数名与顺序）—— §71 实测的缺陷：第一版只让
       模型「从规则里看名字」，at03/at05 的规则顺序与测试调用顺序相反
       ⇒ 8 题里 2 题、32/128 格**不可归因**（那是题面欠定，不是能力读数）。
  S10 ⭐ **反证**：把签名拿掉、只按「参数名在规则里首现的顺序」猜，必须至少错 1 条
       —— 若 0 条 ⇒ 欠定根本不存在 ⇒ S9 是装饰，这道闸该删。

## 跑法

    python3 bench/specgen/ext_v4.py            # 十条自检 + 标定表
    python3 bench/specgen/ext_v4.py --emit     # 写 bench/specgen/tasks_v4.jsonl

⛔ 生成是**构建步骤**：`tasks_v4.jsonl` 一旦提交就冻结，
   打分永远对着冻结的那份，不许在跑的时候现算。

## 与「内核调工具」的界限（不许混淆）

本尺子测的是「模型写出正确产生副作用的代码」。
它**不测**「内核的 `run_shell` 用得好不好」—— 那需要打分器看到**轨迹**，
而 `ExternalScorer` 协议只吃 `{task, answer}`（`mw/bench.py:301`）。
⇒ 要测工具调用，必须先改协议 ⇒ 单独预注册，不在本轮。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

SEED = 20261027          #: 固定种子。换它 = 换尺子 = 旧读数失去对照。
CASES = 16               #: 每题断言条数（= 每格题的分母）

_W = ["alpha", "beta", "gamma", "delta", "eps", "zeta", "eta", "theta"]
_WS = ["  ", "\t", " ", "\t\t"]


def _words(rng: random.Random, n: int) -> list[str]:
    return [rng.choice(_W) for _ in range(n)]


# ===========================================================================
# 期望值来源：在**隔离临时目录**里用 stdlib 现算，读回产物的字节
# ===========================================================================
def expected_bytes(build: Callable[[], str], artifact: str) -> bytes:
    """跑一遍 `build()`，把 `artifact` 的**字节**读回来。

    ⛔ 只读字节不读文本：`read_text` 会把 `\\r\\n` 悄悄归一成 `\\n`，
       BOM 也会被吃掉 —— 那两样正是本尺子要抓的东西之一。
    """
    cwd = os.getcwd()
    td = tempfile.mkdtemp(prefix="v4exp_")
    try:
        os.chdir(td)
        build()
        return Path(artifact).read_bytes()
    finally:
        os.chdir(cwd)
        import shutil
        shutil.rmtree(td, ignore_errors=True)


# ===========================================================================
# 八个规格
# ===========================================================================
@dataclass(frozen=True)
class Spec:
    id: str
    fn: str
    body: tuple[str, ...]
    gen: Callable[[random.Random], Any]
    oracle: str
    spec_impl: str
    partial: str
    pure: str
    #: 在临时目录里把产物造出来（期望值来源）。参数 = gen 的产物。
    build: Callable[..., Any]
    #: 产物路径（相对 cwd），断言读它
    artifact: str
    #: 给 test 用的调用表达式模板（`{args}` 会被替换成 repr）
    call: str


# ── at01 写文本产物（跳空行、原始下标）──────────────────────────────────
def _gen_at01(rng: random.Random) -> list[str]:
    n = rng.randint(1, 6)
    rows = []
    for _ in range(n):
        k = rng.randint(0, 3)
        if k == 0:
            rows.append("")
        elif k == 1:
            rows.append(rng.choice(_WS))
        else:
            rows.append(rng.choice(_WS) + rng.choice(_W) + rng.choice(_WS))
    return rows


def _expect_at01(rows: list[str]) -> bytes:
    line = "".join(f"{i}:{r.strip()}\n" for i, r in enumerate(rows) if r.strip())
    return line.encode("utf-8")


AT01 = Spec(
    id="at01", fn="write_lines",
    body=(
        "对 rows 的**每一个原始下标** i（从 0 开始），取 rows[i] 并去掉首尾空白",
        "去掉后为空的项**跳过**，不产生任何输出行",
        "每一项输出一行，格式严格为 `i:内容`（i 是原始下标，不是输出序号）",
        "**每一行**都以 `\\n` 结尾 —— 包括最后一行",
        "用 UTF-8 编码写（不带 BOM），换行符必须是 `\\n` 而不是 `\\r\\n`",
        "`path` 的父目录不存在时要先创建",
        "函数返回 None",
    ),
    gen=_gen_at01,
    oracle='''import os
def write_lines(rows, path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    buf = []
    for i, r in enumerate(rows):
        s = r.strip()
        if not s:
            continue
        buf.append("%d:%s\\n" % (i, s))
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        fh.write("".join(buf))
''',
    spec_impl='''import os
def write_lines(rows, path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    parts = []
    for idx, row in enumerate(rows):
        stripped = row.strip()
        if stripped == "":
            continue
        parts.append(f"{idx}:{stripped}" + chr(10))
    with open(path, "w", encoding="utf-8", newline=chr(10)) as fh:
        fh.write("".join(parts))
''',
    # partial：漏掉「空行跳过」⇒ 含空行的 case 错，不含的对
    partial='''import os
def write_lines(rows, path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    buf = []
    for i, r in enumerate(rows):
        buf.append("%d:%s\\n" % (i, r.strip()))
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        fh.write("".join(buf))
''',
    # pure：内容完全正确，但什么都不写
    pure='''def write_lines(rows, path):
    return "".join("%d:%s\\n" % (i, r.strip()) for i, r in enumerate(rows) if r.strip())
''',
    artifact="out/o.txt",
    build=lambda rows: (
        os.makedirs("out", exist_ok=True),
        Path("out/o.txt").write_bytes(_expect_at01(rows)),
    ),
    call="write_lines({args}, 'out/o.txt')",
)


# ── at02 写 JSON 产物（键排序、无空格、词频）────────────────────────────
def _gen_at02(rng: random.Random) -> list[str]:
    n = rng.randint(1, 4)
    rows = []
    for _ in range(n):
        k = rng.randint(0, 2)
        w = _words(rng, rng.randint(1, 3))
        if k == 0:
            rows.append(" ".join(w))
        elif k == 1:
            rows.append(" ".join(x.upper() for x in w))
        else:
            rows.append("  " + " \t ".join(w) + " ")
    return rows


def _expect_at02(rows: list[str]) -> bytes:
    d: dict[str, int] = {}
    for r in rows:
        for tok in r.strip().lower().split():
            d[tok] = d.get(tok, 0) + 1
    return json.dumps(d, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


AT02 = Spec(
    id="at02", fn="write_counts",
    body=(
        "把 rows 里每一行去掉首尾空白后转成**小写**",
        "按任意空白切词（连续空白算一个分隔符）",
        "统计每个词出现的总次数",
        "写成 JSON 对象：**键按字典序排序**、分隔符是最紧凑的 `,` 与 `:`（无空格）、"
        "非 ASCII 字符原样输出不转义",
        "用 UTF-8 编码写，不带 BOM，文件末尾不加换行",
        "`path` 的父目录不存在时要先创建",
    ),
    gen=_gen_at02,
    oracle='''import json, os
def write_counts(rows, path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    counts = {}
    for row in rows:
        for tok in row.strip().lower().split():
            counts[tok] = counts.get(tok, 0) + 1
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(counts, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":")))
''',
    spec_impl='''import json, os
def write_counts(rows, path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tally = {}
    for row in rows:
        for token in row.strip().lower().split():
            tally[token] = tally.get(token, 0) + 1
    text = json.dumps(tally, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
''',
    # partial：不转小写 ⇒ 含大写词的 case 错
    partial='''import json, os
def write_counts(rows, path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    counts = {}
    for row in rows:
        for tok in row.strip().split():
            counts[tok] = counts.get(tok, 0) + 1
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(counts, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":")))
''',
    pure='''def write_counts(rows, path):
    counts = {}
    for row in rows:
        for tok in row.strip().lower().split():
            counts[tok] = counts.get(tok, 0) + 1
    return counts
''',
    artifact="out/c.json",
    build=lambda rows: (
        os.makedirs("out", exist_ok=True),
        Path("out/c.json").write_bytes(_expect_at02(rows)),
    ),
    call="write_counts({args}, 'out/c.json')",
)


# ── at03 幂等追加（同一个调用跑两次，内容不许翻倍）──────────────────────
def _gen_at03(rng: random.Random) -> list[str]:
    n = rng.randint(1, 4)
    rows = []
    for _ in range(n):
        k = rng.randint(0, 2)
        if k == 0:
            rows.append("")
        elif k == 1:
            rows.append(" " + rng.choice(_W).upper() + " ")
        else:
            rows.append(rng.choice(_W))
    return rows


def _expect_at03(rows: list[str]) -> bytes:
    seen: list[str] = []
    for r in rows:
        s = r.strip()
        if s and s not in seen:
            seen.append(s)
    return "".join(s + "\n" for s in seen).encode("utf-8")


AT03 = Spec(
    id="at03", fn="append_dedup",
    body=(
        "`path` 是文件路径，可能已存在也可能不存在（不存在就创建）",
        "把 rows 里每一项去掉首尾空白，空项跳过",
        "**按原顺序**把非空项追加到文件末尾",
        "追加前先读已有内容：**已经在文件里的行不再写入**（按 strip 后的原样比较，"
        "区分大小写）",
        "每一行以 `\\n` 结尾，UTF-8 编码",
        "⇒ 对同一个输入连续调用两次，文件内容必须与只调用一次**完全相同**（幂等）",
    ),
    gen=_gen_at03,
    oracle='''import os
def append_dedup(rows, path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    existing = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            existing = [x.rstrip("\\n") for x in fh]
    known = set(existing)
    with open(path, "a", encoding="utf-8", newline="\\n") as fh:
        for row in rows:
            s = row.strip()
            if not s or s in known:
                continue
            known.add(s)
            fh.write(s + "\\n")
''',
    spec_impl='''import os
def append_dedup(rows, path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    seen = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                seen.add(line.rstrip(chr(10)))
    with open(path, "a", encoding="utf-8", newline=chr(10)) as fh:
        for row in rows:
            item = row.strip()
            if item == "" or item in seen:
                continue
            seen.add(item)
            fh.write(item + chr(10))
''',
    # partial：去重用小写比较 ⇒ 大小写不同的 case 上误判
    partial='''import os
def append_dedup(rows, path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    existing = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            existing = [x.rstrip("\\n") for x in fh]
    known = {x.lower() for x in existing}
    with open(path, "a", encoding="utf-8", newline="\\n") as fh:
        for row in rows:
            s = row.strip()
            if not s or s.lower() in known:
                continue
            known.add(s.lower())
            fh.write(s + "\\n")
''',
    pure='''def append_dedup(rows, path):
    seen = []
    for row in rows:
        s = row.strip()
        if s and s not in seen:
            seen.append(s)
    return seen
''',
    artifact="log.txt",
    build=lambda rows: (
        Path("log.txt").write_bytes(_expect_at03(rows)),
    ),
    call="append_dedup({args}, 'log.txt')",
)


# ── at04 原子失败（校验失败不许留下半成品）──────────────────────────────
def _gen_at04(rng: random.Random) -> tuple:
    n = rng.randint(1, 4)
    rows = [_words(rng, 1)[0] for _ in range(n)]
    bad = rng.random() < 0.5
    if bad:
        pos = rng.randrange(len(rows))
        rows[pos] = rows[pos] + "#" + _words(rng, 1)[0]
    return (rows, bad)


def _expect_at04(rows: list[str], bad: bool) -> bytes:
    if bad:
        return b""            # 非法 ⇒ 不产生产物
    return "".join(r.strip() + "\n" for r in rows).encode("utf-8")


AT04 = Spec(
    id="at04", fn="write_or_fail",
    body=(
        "先**完整校验**：只要 rows 中任意一项包含字符 `#`，就抛 `ValueError`"
        "（消息任意）",
        "校验失败时**不许创建目标文件**，也不许留下任何空文件或临时文件"
        "（必须在校验全部通过之后才打开文件）",
        "校验通过：每一项去掉首尾空白，按原顺序写出，每项一行、以 `\\n` 结尾",
        "UTF-8 编码写；`path` 的父目录不存在时先创建",
        "函数返回 None",
    ),
    gen=_gen_at04,
    oracle='''import os
def write_or_fail(rows, path):
    for r in rows:
        if "#" in r:
            raise ValueError("bad row")
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        for r in rows:
            fh.write(r.strip() + "\\n")
''',
    spec_impl='''import os
def write_or_fail(rows, path):
    cleaned = []
    for row in rows:
        if "#" in row:
            raise ValueError("row contains #")
        cleaned.append(row.strip())
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline=chr(10)) as fh:
        for item in cleaned:
            fh.write(item + chr(10))
''',
    # partial：先建目录+开文件，再校验 ⇒ 非法时留下空文件
    partial='''import os
def write_or_fail(rows, path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        for r in rows:
            if "#" in r:
                raise ValueError("bad row")
            fh.write(r.strip() + "\\n")
''',
    pure='''def write_or_fail(rows, path):
    for r in rows:
        if "#" in r:
            raise ValueError("bad row")
    return [r.strip() for r in rows]
''',
    artifact="out/o.txt",
    build=lambda rows, bad: (
        os.makedirs("out", exist_ok=True) if not bad else None,
        Path("out/o.txt").write_bytes(_expect_at04(rows, bad)) if not bad else None,
    ),
    call="write_or_fail({args}, 'out/o.txt')",
)


# ── at05 多级目录 + 多产物分片 ──────────────────────────────────────────
def _gen_at05(rng: random.Random) -> list[str]:
    n = rng.randint(1, 10)
    return [rng.choice(_WS) + rng.choice(_W) + rng.choice(_WS) for _ in range(n)]


def _expect_at05(rows: list[str]) -> dict[str, bytes]:
    groups: dict[str, list[str]] = {}
    for i, r in enumerate(rows):
        groups.setdefault("p%02d.txt" % (i // 4), []).append(r.strip())
    return {k: "".join(x + "\n" for x in v).encode("utf-8")
            for k, v in groups.items()}


AT05 = Spec(
    id="at05", fn="save_nested",
    body=(
        "目标目录是 `root/a/b/`（root 是参数）—— 三级目录不存在时逐级创建，"
        "已存在时**不许报错**",
        "把 rows 按**原始下标每 4 项一组**分片：下标 0~3 进 `p00.txt`，"
        "4~7 进 `p01.txt`，依此类推（文件名两位补零）",
        "每片里的每一项去掉首尾空白后写一行、以 `\\n` 结尾；空项**不跳过**"
        "（它会变成空行，仍占一行）",
        "UTF-8 编码写",
        "函数返回 None",
    ),
    gen=_gen_at05,
    oracle='''import os
def save_nested(rows, path=None):
    root = path if path is not None else ""
    raise NotImplementedError
''',
    spec_impl='''import os
def save_nested(rows, root):
    base = os.path.join(root, "a", "b")
    os.makedirs(base, exist_ok=True)
    buckets = {}
    for i, r in enumerate(rows):
        key = "p%02d.txt" % (i // 4)
        buckets.setdefault(key, []).append(r.strip())
    for name, items in buckets.items():
        with open(os.path.join(base, name), "w", encoding="utf-8",
                  newline="\\n") as fh:
            for it in items:
                fh.write(it + "\\n")
''',
    partial='''import os
def save_nested(rows, root):
    base = os.path.join(root, "a", "b")
    os.makedirs(base, exist_ok=True)
    buckets = {}
    for i, r in enumerate(rows):
        key = "p%02d.txt" % (i // 5)
        buckets.setdefault(key, []).append(r.strip())
    for name, items in buckets.items():
        with open(os.path.join(base, name), "w", encoding="utf-8",
                  newline="\\n") as fh:
            for it in items:
                fh.write(it + "\\n")
''',
    pure='''def save_nested(rows, root):
    return {"p%02d.txt" % (i // 4): r.strip() for i, r in enumerate(rows)}
''',
    artifact="root/a/b",
    build=lambda rows: _build_at05(rows),
    call="save_nested({args}, 'root')",
)


def _build_at05(rows: list[str]) -> Any:
    base = Path("root/a/b")
    base.mkdir(parents=True, exist_ok=True)
    for name, data in _expect_at05(rows).items():
        (base / name).write_bytes(data)
    return None


# at05 的 oracle 需要真实现（上面占位是为了让 Spec 结构统一 —— 这里覆盖）
AT05 = Spec(
    id=AT05.id, fn=AT05.fn, body=AT05.body, gen=AT05.gen,
    oracle='''import os
def save_nested(rows, root):
    base = os.path.join(root, "a", "b")
    os.makedirs(base, exist_ok=True)
    buckets = {}
    for i, r in enumerate(rows):
        buckets.setdefault("p%02d.txt" % (i // 4), []).append(r.strip())
    for name, items in buckets.items():
        with open(os.path.join(base, name), "w", encoding="utf-8",
                  newline="\\n") as fh:
            for it in items:
                fh.write(it + "\\n")
''',
    spec_impl=AT05.spec_impl, partial=AT05.partial, pure=AT05.pure,
    build=AT05.build, artifact=AT05.artifact, call=AT05.call,
)


# ── at06 读-改-写（两次调用后的最终状态）───────────────────────────────
def _gen_at06(rng: random.Random) -> tuple:
    first = _words(rng, rng.randint(1, 4))
    second = _words(rng, rng.randint(1, 4))
    return (first, second)


def _expect_at06(first: list[str], second: list[str]) -> bytes:
    merged = sorted(set(first) | set(second))
    return "".join(x + "\n" for x in merged).encode("utf-8")


AT06 = Spec(
    id="at06", fn="merge_sorted",
    body=(
        "`path` 里可能已有若干行（文件不存在 ⇒ 视为空）",
        "把 `new` 里的每一项去掉首尾空白、跳过空项",
        "与已有内容合并：**去重**（同一项只保留一份）后按**字典序升序**排序",
        "把结果整体**覆盖写回** `path`，每行以 `\\n` 结尾，UTF-8 编码",
        "⇒ 连续用两批数据调用两次，最终内容必须等于「两批合并去重排序」的结果"
        "（不是追加）",
    ),
    gen=_gen_at06,
    oracle='''import os
def merge_sorted(path, new):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    old = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            old = [x.rstrip("\\n") for x in fh]
    items = set(x for x in old if x)
    for n in new:
        s = n.strip()
        if s:
            items.add(s)
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        for x in sorted(items):
            fh.write(x + "\\n")
''',
    spec_impl='''import os
def merge_sorted(path, new):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    existing = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            existing = [line.rstrip(chr(10)) for line in fh]
    pool = {x for x in existing if x != ""}
    for item in new:
        value = item.strip()
        if value != "":
            pool.add(value)
    with open(path, "w", encoding="utf-8", newline=chr(10)) as fh:
        for value in sorted(pool):
            fh.write(value + chr(10))
''',
    partial='''import os
def merge_sorted(path, new):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    old = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            old = [x.rstrip("\\n") for x in fh]
    items = set(x for x in old if x)
    for n in new:
        s = n.strip()
        if s:
            items.add(s)
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        for x in items:
            fh.write(x + "\\n")
''',
    pure='''def merge_sorted(path, new):
    return sorted({x.strip() for x in new if x.strip()})
''',
    artifact="s.txt",
    build=lambda first, second: Path("s.txt").write_bytes(
        _expect_at06(first, second)),
    call="merge_sorted('s.txt', {first!s}) or merge_sorted('s.txt', {second!s})",
)


# ── at07 目录遍历（顶层 *.txt 合并）────────────────────────────────────
def _gen_at07(rng: random.Random) -> dict[str, list[str]]:
    files: dict[str, list[str]] = {}
    nf = rng.randint(1, 3)
    for k in range(nf):
        n = rng.randint(1, 3)
        lines = []
        for _ in range(n):
            j = rng.randint(0, 2)
            lines.append("" if j == 0 else rng.choice(_WS) + rng.choice(_W))
        files["f%d.txt" % k] = lines
    if rng.random() < 0.4:            # 放一个**非 .txt**，必须被忽略
        files["skip.log"] = [_words(rng, 1)[0]]
    return files


def _expect_at07(files: dict[str, list[str]]) -> bytes:
    pool: set[str] = set()
    for name, lines in files.items():
        if not name.endswith(".txt"):
            continue
        for ln in lines:
            s = ln.strip()
            if s:
                pool.add(s)
    return "".join(x + "\n" for x in sorted(pool)).encode("utf-8")


AT07 = Spec(
    id="at07", fn="merge_txt",
    body=(
        "`indir` 目录下**只有顶层**的 `.txt` 文件参与合并（子目录不递归，"
        "扩展名不是 `.txt` 的文件忽略）",
        "读每个文件的所有行，去掉首尾空白，空行跳过",
        "所有行合并后**去重**并按字典序升序排序",
        "写到 `out`（覆盖），每行以 `\\n` 结尾，UTF-8 编码",
        "`out` 的父目录不存在时先创建",
    ),
    gen=_gen_at07,
    oracle='''import os
def merge_txt(indir, out):
    d = os.path.dirname(out)
    if d:
        os.makedirs(d, exist_ok=True)
    pool = set()
    for name in sorted(os.listdir(indir)):
        if not name.endswith(".txt"):
            continue
        p = os.path.join(indir, name)
        if not os.path.isfile(p):
            continue
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if s:
                    pool.add(s)
    with open(out, "w", encoding="utf-8", newline="\\n") as fh:
        for x in sorted(pool):
            fh.write(x + "\\n")
''',
    spec_impl='''import os
def merge_txt(indir, out):
    parent = os.path.dirname(out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    pool = set()
    for name in os.listdir(indir):
        if not name.endswith(".txt"):
            continue
        full = os.path.join(indir, name)
        if not os.path.isfile(full):
            continue
        with open(full, "r", encoding="utf-8") as fh:
            for line in fh:
                value = line.strip()
                if value != "":
                    pool.add(value)
    with open(out, "w", encoding="utf-8", newline=chr(10)) as fh:
        for value in sorted(pool):
            fh.write(value + chr(10))
''',
    # partial：递归进子目录（本题 test 不打子目录 ⇒ 用「忽略扩展名」这条替代）
    partial='''import os
def merge_txt(indir, out):
    d = os.path.dirname(out)
    if d:
        os.makedirs(d, exist_ok=True)
    pool = set()
    for name in sorted(os.listdir(indir)):
        p = os.path.join(indir, name)
        if not os.path.isfile(p):
            continue
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if s:
                    pool.add(s)
    with open(out, "w", encoding="utf-8", newline="\\n") as fh:
        for x in sorted(pool):
            fh.write(x + "\\n")
''',
    pure='''def merge_txt(indir, out):
    import os
    return sorted({ln.strip() for n in os.listdir(indir) if n.endswith(".txt")
                   for ln in open(os.path.join(indir, n), encoding="utf-8")
                   if ln.strip()})
''',
    artifact="dst/m.txt",
    build=lambda files: _build_at07(files),
    call="merge_txt('src', 'dst/m.txt')",
)


def _build_at07(files: dict[str, list[str]]) -> Any:
    Path("src").mkdir(parents=True, exist_ok=True)
    for name, lines in files.items():
        Path("src/" + name).write_text("".join(x + "\n" for x in lines),
                                       encoding="utf-8")
    Path("dst").mkdir(parents=True, exist_ok=True)
    Path("dst/m.txt").write_bytes(_expect_at07(files))
    return None


# ── at08 字节精确（= 转义）────────────────────────────────────────────
def _gen_at08(rng: random.Random) -> list[tuple[str, str]]:
    n = rng.randint(1, 4)
    pairs = []
    for _ in range(n):
        k = rng.choice(_W)
        if rng.random() < 0.35:
            v = rng.choice(_W) + "=" + rng.choice(_W)   # 含 = 号
        else:
            v = rng.choice(_W)
        pairs.append((k, v))
    return pairs


def _expect_at08(pairs: list[tuple[str, str]]) -> bytes:
    # ⚠️ 自踩（本轮）：第一版这里忘了转义，于是 **partial（不转义的错误实现）拿了 16/16、
    #    oracle 只拿 5/16** —— 期望值比错误答案还错。S3「partial 必须严格居中」
    #    当场红出来。这正是 S3 存在的理由：它抓的是"期望值写错"，不是"实现写错"。
    _BS = chr(92)
    return "".join(f"{k}={v.replace('=', _BS + '=')}\n"
                   for k, v in pairs).encode("utf-8")


AT08 = Spec(
    id="at08", fn="write_pairs",
    body=(
        "把 pairs 里的每个 (键, 值) 写成一行 `键=值`，按原顺序，每行以 `\\n` 结尾",
        "如果**值**里本身含有 `=`，把它替换成 `\\=`（反斜杠 + 等号）——"
        "键里出现 `=` 时不处理",
        "用 UTF-8 编码写，**不带 BOM**",
        "`path` 的父目录不存在时先创建",
        "函数返回 None",
    ),
    gen=_gen_at08,
    oracle='''import os
def write_pairs(pairs, path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        for k, v in pairs:
            fh.write("%s=%s\\n" % (k, v.replace("=", "\\\\=")))
''',
    spec_impl='''import os
def write_pairs(pairs, path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    chunk = []
    for key, value in pairs:
        chunk.append(key + "=" + value.replace("=", "\\\\=") + chr(10))
    with open(path, "w", encoding="utf-8", newline=chr(10)) as fh:
        fh.write("".join(chunk))
''',
    # partial：不转义值里的 =
    partial='''import os
def write_pairs(pairs, path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\\n") as fh:
        for k, v in pairs:
            fh.write("%s=%s\\n" % (k, v))
''',
    pure='''def write_pairs(pairs, path):
    return "".join("%s=%s\\n" % (k, v.replace("=", "\\\\=")) for k, v in pairs)
''',
    artifact="out/p.txt",
    build=lambda pairs: (
        os.makedirs("out", exist_ok=True),
        Path("out/p.txt").write_bytes(_expect_at08(pairs)),
    ),
    call="write_pairs({args}, 'out/p.txt')",
)


SPECS: list[Spec] = [AT01, AT02, AT03, AT04, AT05, AT06, AT07, AT08]


# ===========================================================================
# 渲染题面与断言
# ===========================================================================
def sig_of(sp: Spec) -> str:
    """从 oracle 抽出**签名**（参数名 + 顺序），作为题面里唯一的真相来源。

    ⛔ 第一百零一轮 §71 实测：第一版题面只写「参数见下面每一条规则里提到的名字」，
       而 at03/at05 的规则里 `path`/`root` **先出现**、测试却按 `(rows, path)` 调用
       ⇒ 模型照题面顺序写 `append_dedup(path, rows)` ⇒ `TypeError: ... not list` ⇒ 0 分。
       那不是能力读数，是**题面欠定**（8 题里 2 题、32/128 格不可归因）。
       修法：把签名印在题面上，顺序不再靠猜。S9 钉住它，S10 反证它不是装饰。
    """
    m = re.search(rf"def\s+{re.escape(sp.fn)}\s*\(([^)]*)\)", sp.oracle)
    if not m:
        raise SystemExit(f"❌ {sp.id}: oracle 里找不到 def {sp.fn}(...)")
    return m.group(1).strip()


def render_prompt(sp: Spec) -> str:
    rules = "\n".join(f"  {i + 1}. {b}" for i, b in enumerate(sp.body))
    extra = ""
    if sp.id == "at03":
        extra = "\n把同一批输入连续调用两次后，文件里不许出现重复行。"
    if sp.id == "at08":
        extra = "\n注意：值里的 `=` 要转义，键里的不需要。"
    sig = sig_of(sp)
    return (f"写一个函数 `{sp.fn}`，函数签名**必须**是 `def {sp.fn}({sig})`"
            f"（参数名与顺序照此写，不要改）。按下面列出的规则实现：\n{rules}{extra}\n"
            f"只输出代码，函数名必须是 {sp.fn}。")


def _args_repr(case: Any) -> str:
    if isinstance(case, tuple):
        return ", ".join(repr(x) for x in case)
    return repr(case)


def tests_of(sp: Spec, seed: int = SEED, n: int = CASES) -> list[str]:
    """每题 n 条断言。**每条自带 setup**（打分器每条 test 一个独立子进程/临时目录）。

    ⛔ 断言之间不许共享状态：所以 setup 必须写进每条 test 里，
       而不是"第一条 test 建好、后面的用"。
    """
    rng = random.Random(f"{seed}:{sp.id}")
    out: list[str] = []
    for _ in range(n):
        case = sp.gen(rng)
        out.append(_one_test(sp, case))
    return out


def _one_test(sp: Spec, case: Any) -> str:
    head = "import pathlib as _pp\n"
    call = sp.call.format(args=_args_repr(case),
                          first=repr(case[0]) if isinstance(case, tuple) else "None",
                          second=repr(case[1]) if isinstance(case, tuple)
                          and len(case) > 1 else "None")

    if sp.id == "at01":
        want = _expect_at01(case)
        return (head + f"{call}\n"
                f"assert _pp.Path('out/o.txt').read_bytes() == {want!r}\n")

    if sp.id == "at02":
        want = _expect_at02(case)
        return (head + f"{call}\n"
                f"assert _pp.Path('out/c.json').read_bytes() == {want!r}\n")

    if sp.id == "at03":
        want = _expect_at03(case)
        return (head + f"{call}\n{call}\n"
                f"assert _pp.Path('log.txt').read_bytes() == {want!r}\n")

    if sp.id == "at04":
        rows, bad = case
        # ⚠️ 自踩（本轮）：`case` 是 `(rows, bad)` 二元组，通用 `_args_repr` 会把
        #    `bad` 也当参数展开成 `write_or_fail(rows, True, 'out/o.txt')` ⇒ 全部 0/16。
        #    `bad` 是**判分侧的标记**，不是被测函数的参数 —— 必须显式只传 rows。
        real_call = f"write_or_fail({rows!r}, 'out/o.txt')"
        if bad:
            return (head + "_ok = False\n"
                    f"try:\n    {real_call}\nexcept ValueError:\n    _ok = True\n"
                    "assert _ok is True, 'must raise ValueError'\n"
                    "assert not _pp.Path('out/o.txt').exists(), "
                    "'must not leave a file behind'\n")
        want = _expect_at04(rows, bad)
        return (head + f"{real_call}\n"
                f"assert _pp.Path('out/o.txt').read_bytes() == {want!r}\n")

    if sp.id == "at05":
        want = _expect_at05(case)
        lines = [head + f"{call}\n"]
        for name in sorted(want):
            lines.append(
                f"assert _pp.Path('root/a/b/{name}').read_bytes() == "
                f"{want[name]!r}\n")
        lines.append("assert sorted(p.name for p in _pp.Path('root/a/b').iterdir()"
                     f") == {sorted(want)!r}\n")
        return "".join(lines)

    if sp.id == "at06":
        first, second = case
        want = _expect_at06(first, second)
        return (head + f"merge_sorted('s.txt', {first!r})\n"
                f"merge_sorted('s.txt', {second!r})\n"
                f"assert _pp.Path('s.txt').read_bytes() == {want!r}\n")

    if sp.id == "at07":
        setup = ["import pathlib as _p2\n", "_p2.Path('src').mkdir(parents=True, "
                 "exist_ok=True)\n"]
        for name, lines_ in case.items():
            txt = "".join(x + "\n" for x in lines_)
            setup.append(f"_p2.Path('src/{name}').write_bytes({txt.encode()!r})\n")
        want = _expect_at07(case)
        setup.append(f"{call}\n")
        setup.append(f"assert _pp.Path('dst/m.txt').read_bytes() == {want!r}\n")
        return head + "".join(setup)

    if sp.id == "at08":
        want = _expect_at08(case)
        return (head + f"{call}\n"
                f"assert _pp.Path('out/p.txt').read_bytes() == {want!r}\n")

    raise AssertionError(f"未知 spec {sp.id}")


def rows_of(seed: int = SEED, n: int = CASES) -> list[dict]:
    rows = []
    for sp in SPECS:
        rows.append({
            "id": sp.id,
            "prompt": render_prompt(sp),
            "tests": tests_of(sp, seed, n),
            "oracle": sp.oracle,
            "partial": sp.partial,
            "null": "",
            "meta": {
                "tags": "external-oracle,artifact,side-effect",
                "level": "mid",
                "seed": seed,
                "cases": n,
                "oracle_source": "CPython stdlib 现算（json/encode/newline 行为）",
                "family": "artifact",     # ← 与 norm_sg*/f_* 不同的**族**
            },
        })
    return rows


# ===========================================================================
# 与打分器同语义的判分（每条 test 独立子进程 + 独立临时目录）
# ===========================================================================
def score(src: str, tests: list[str], *, timeout_s: float = 30.0) -> int:
    """**逐条 test 一个独立子进程**，与 `bench/scorers/code_exec.py` 同语义。

    ⛔ 不许用共享 namespace 的 exec 来省钱：那样
       at03 的「跑两次」会被前一条 test 的残留污染，
       而"断言之间不许共享读数"是本项目的硬纪律。
    """
    import subprocess
    if not src.strip():
        return 0
    hit = 0
    for t in tests:
        code = f"{src}\n\n{t}\n"
        td = tempfile.mkdtemp(prefix="v4score_")
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
# 十条自检
# ===========================================================================
def self_check() -> int:
    print("=" * 78)
    print(f"  v4 尺子自检（seed={SEED}，每题 {CASES} 条断言，共 "
          f"{len(SPECS) * CASES} 格）")
    print("=" * 78)

    rows = rows_of()
    bad = 0

    print("\n[S1/S2/S3/S4/S8] 逐题标定")
    print(f"  {'题':5s} {'oracle':>7s} {'spec':>6s} {'partial':>8s} "
          f"{'null':>6s} {'pure':>6s}  判定")
    for sp, r in zip(SPECS, rows):
        n = len(r["tests"])
        o = score(sp.oracle, r["tests"])
        s = score(sp.spec_impl, r["tests"])
        p = score(sp.partial, r["tests"])
        z = score(r["null"], r["tests"])
        pu = score(sp.pure, r["tests"])
        ok = (o == n) and (s == n) and (0 < p < n) and (z == 0) and (pu < n)
        if not ok:
            bad += 1
        print(f"  {sp.id:5s} {o:3d}/{n:<3d} {s:3d}/{n:<2d} {p:5d}/{n:<2d} "
              f"{z:3d}/{n:<2d} {pu:4d}/{n:<2d}  {'✅' if ok else '❌'}")

    # S5 同种子重生成逐字节相同
    a = json.dumps(rows_of(), ensure_ascii=False, sort_keys=True)
    b = json.dumps(rows_of(), ensure_ascii=False, sort_keys=True)
    s5 = a == b
    print(f"\n[S5] 同种子重生成逐字节相同：{'✅' if s5 else '❌ 尺子会漂'}")
    bad += 0 if s5 else 1

    # S6 换种子必须变
    c = json.dumps(rows_of(seed=SEED + 1), ensure_ascii=False, sort_keys=True)
    s6 = a != c
    print(f"[S6] 换种子后题集必须变：{'✅ 变了' if s6 else '❌ 期望值是写死的'}")
    bad += 0 if s6 else 1

    # S7 期望值不许出现在题面里
    leaks = []
    for sp, r in zip(SPECS, rows):
        for t in r["tests"]:
            tail = t.rsplit("==", 1)[-1].strip() if "==" in t else ""
            if len(tail) >= 6 and tail in r["prompt"]:
                leaks.append(f"{sp.id}:{tail[:20]}")
    s7 = not leaks
    print(f"[S7] 期望值泄漏进题面：{'✅ 无' if s7 else '❌ ' + str(leaks[:3])}")
    bad += 0 if s7 else 1

    # ── S9 每条题面必须**显式给出签名**（参数名与顺序）────────────────────
    #   §71 实测的缺陷：第一版只让模型「从规则里看名字」，at03/at05 的规则顺序
    #   与测试调用顺序相反 ⇒ 2/8 题不可解（32/128 格）。S9 钉住修法。
    miss9 = [sp.id for sp in SPECS
             if f"def {sp.fn}({sig_of(sp)})" not in render_prompt(sp)]
    s9 = not miss9
    print(f"[S9] 题面显式给出签名：{'✅ 8/8' if s9 else '❌ 缺 ' + str(miss9)}")
    bad += 0 if s9 else 1

    # ── S10 **反证**：若把签名从题面拿掉，只按「参数名在规则里首现的顺序」猜，
    #   必须**至少错 1 条** —— 否则 S9 是装饰（说明欠定根本不存在，这道闸可以删）。
    #   ⛔ 这是「断言自带反证」：一条恒真的闸等于没有闸。
    body_text = {sp.id: "\n".join(sp.body) for sp in SPECS}
    guess_bad = []
    for sp in SPECS:
        true_params = [p.strip() for p in sig_of(sp).split(",") if p.strip()]
        firsts = [(body_text[sp.id].find(p), p) for p in true_params]
        mentioned = [(pos, p) for pos, p in firsts if pos >= 0]
        mentioned.sort()
        guessed = [p for _, p in mentioned]
        wanted = [p for p in true_params if p in guessed]
        if guessed != wanted:
            guess_bad.append(f"{sp.id}: 猜={','.join(guessed)} 实际={','.join(wanted)}")
    s10 = bool(guess_bad)
    print(f"[S10] 反证「按规则首现顺序猜参数」：{len(guess_bad)} 条会猜错 "
          f"{'✅' if s10 else '❌ 0 条 ⇒ S9 是装饰，可以删'}")
    for g in guess_bad:
        print(f"      {g}")
    bad += 0 if s10 else 1

    print(f"\n{'✅ 十条自检全绿' if bad == 0 else f'❌ {bad} 条自检失败'}")
    if bad == 0:
        print("⚠️ S8 的含义：pure 是「返回值完全正确、但什么都没写出去」的实现。")
        print("   它在 v1~v3 上能拿满分，在这把尺子上拿不到分 ⇒ 形态确实变了。")
    return 0 if bad == 0 else 1


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
    ap.add_argument("--emit", nargs="?", const="bench/specgen/tasks_v4.jsonl")
    a = ap.parse_args()
    rc = self_check()
    if a.emit and rc == 0:
        rc = emit(a.emit)
    sys.exit(rc)
