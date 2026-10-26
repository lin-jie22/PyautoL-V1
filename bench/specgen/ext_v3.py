"""v3 尺子：**期望值一行都不是我写的**。

⛔ 这把尺子要修的，是 §58 判定的那半个自证问题：
   `tasks_v1/v2` 的题面与 oracle **同源**（都由 `gen_spec_tasks.py` 里同一份 `spec` 渲染），
   所以我打的分本质上是「模型写的代码符不符合我脑子里的 spec」。
   v3 把「答案从哪来」换掉：**期望值由 CPython 现算，输入由固定种子的随机器生成**。

外部性分三层，逐层写清楚，不许混说：

| 层 | 谁给的 | 本文件 |
|---|---|---|
| 输入 | 随机器（种子固定，我不挑） | ✅ 外部 |
| 期望值 | **CPython 自己的实现现算** | ✅ 外部 |
| 规格文字 | 我写的（这是下一层要解的） | ❌ 仍是自证 |

⇒ 所以这把尺子**不能**声称「完全外部」。它能声称的是：
   我**没有**决定任何一条期望值，也**没有**挑选任何一个输入。

四条自检（缺一条这把尺子就不许用）：

  S1 oracle（直接调 CPython）必须满分 —— 否则题目本身不可解
  S2 spec_impl（只照规格文字手写，不看 CPython 源码）必须满分 —— 否则规格不充分
  S3 partial（spec_impl 故意漏一条规则）必须**严格居中** —— 否则断言不区分对错
  S4 null（空串）必须零分

  S5 同种子重生成必须**逐字节相同**（尺子不许漂）
  S6 换种子必须**变**（证明期望值不是写死的）
  S7 期望值不许出现在题面里（否则等于把答案印在卷子上）

跑法：

    python3 bench/specgen/ext_v3.py            # 校验四条自检并打印标定表
    python3 bench/specgen/ext_v3.py --emit     # 写 bench/specgen/tasks_v3.jsonl

⛔ 生成是**构建步骤**：`tasks_v3.jsonl` 一旦提交就冻结，
   打分永远对着冻结的那份，不许在跑的时候现算。
"""

from __future__ import annotations

import argparse
import base64
import heapq
import json
import posixpath
import random
import statistics
import sys
import textwrap
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable

SEED = 20261026          #: 固定种子。换它 = 换尺子 = 旧读数失去对照。
CASES = 16               #: 每题断言条数（= 每格题的分母）

_WS = [" ", "  ", "\t", " \t", "\n", "\n  ", "\t\t"]


# ===========================================================================
# 输入生成器：只负责造输入，**不负责造答案**
# ===========================================================================
def _words(rng: random.Random, n: int) -> list[str]:
    pool = "alpha beta gamma delta eps zeta eta theta iota kappa".split()
    return [rng.choice(pool) for _ in range(n)]


def gen_split_ws(rng: random.Random) -> tuple:
    """返回一个字符串；可能全是空白、可能首尾有空白。"""
    n = rng.randint(0, 4)
    parts: list[str] = []
    if rng.random() < 0.5:
        parts.append(rng.choice(_WS))
    for i, w in enumerate(_words(rng, n)):
        parts.append(w)
        parts.append(rng.choice(_WS))
    return ("".join(parts),)


def gen_normpath(rng: random.Random) -> tuple:
    """相对或单斜杠起头的 POSIX 路径（输入域里**不含**双斜杠起头）。

    ⚠️ 自踩：第一版写成 `head + "/".join(segs)`，当 head="/" 且首段为空时
       会造出 `//../b` —— 而 POSIX 里**两个斜杠起头要被原样保留**，
       于是 oracle 给 `//b`、我的手写实现给 `/b`。规格里明明写了输入域不含
       `//`，是**生成器**违反了规格。修法是把首段的空串禁掉，不是去改规格。
    """
    segs: list[str] = []
    head = "/" if rng.random() < 0.5 else ""
    for i in range(rng.randint(0, 5)):
        opts = ["a", "b", "c", ".", "..", "d.e", "x_y"]
        if i > 0 or not head:
            opts.append("")
        segs.append(rng.choice(opts))
    return (head + "/".join(segs),)


def gen_splitext(rng: random.Random) -> tuple:
    name = rng.choice(["a", ".bashrc", "a.b", "a.b.c", "a.", "x/y.tar.gz", "f", ".."])
    if rng.random() < 0.3:
        name = name + rng.choice([".", "..", ".z"])
    return (name,)


def gen_expandtabs(rng: random.Random) -> tuple:
    out: list[str] = []
    for _ in range(rng.randint(1, 12)):
        out.append(rng.choice(["a", "\t", "bc", "\n", "defg", "\t\t", "h"]))
    return ("".join(out), rng.randint(1, 8))


def gen_urlencode(rng: random.Random) -> tuple:
    pairs = []
    for _ in range(rng.randint(1, 4)):
        k = rng.choice(["a", "k 1", "名前", "x+y", "q&r", "z=z"])
        v = rng.choice(["v", "hello world", "日本", "a/b", "=", "", "#frag"])
        pairs.append((k, v))
    return (pairs,)


def gen_b32(rng: random.Random) -> tuple:
    n = rng.randint(0, 12)
    return (bytes(rng.randrange(256) for _ in range(n)),)


def gen_median_low(rng: random.Random) -> tuple:
    return ([rng.randint(-9, 9) for _ in range(rng.randint(1, 8))],)


def gen_nlargest(rng: random.Random) -> tuple:
    data = [rng.randint(-9, 9) for _ in range(rng.randint(1, 9))]
    return (rng.randint(1, len(data)), data)


def gen_dedent(rng: random.Random) -> tuple:
    pad = " " * rng.randint(0, 6)
    lines = []
    for _ in range(rng.randint(1, 4)):
        lines.append(pad + rng.choice(["x", "y z", ""]))
    if rng.random() < 0.4:
        lines.insert(rng.randrange(len(lines) + 1), "")
    return ("\n".join(lines) + ("\n" if rng.random() < 0.5 else ""),)


def gen_quote(rng: random.Random) -> tuple:
    return (rng.choice(["abc", "a b", "a/b", "名前", "a?b#c", "%20", "~._-", ""]),)


# ===========================================================================
# 规格：一段话 + 参考实现（参考实现用 CPython，**手写实现单独写**）
# ===========================================================================
@dataclass(frozen=True)
class Spec:
    id: str
    fn: str
    params: str
    body: str          #: 「要求」里的条目，一句一条
    gen: Callable[[random.Random], tuple]
    ref: Callable[..., Any]
    oracle: str        #: 直接调 CPython（证明可解）
    spec_impl: str     #: 只照规格手写（证明规格充分）
    partial: str       #: 故意漏一条规则（证明断言区分对错）
    level: str = "mid"


SPECS: list[Spec] = [
    Spec(
        id="ex01", fn="f_split_ws", params="s",
        body=[
            "把 s 按**任意空白字符**（空格、制表符、换行）切分成若干段",
            "连续出现的空白算**一个**分隔符，**不产生空串**",
            "首尾的空白直接忽略",
            "s 为空串或全是空白时，返回**空列表**",
        ],
        gen=gen_split_ws,
        ref=lambda s: s.split(),
        oracle="def f_split_ws(s):\n    return s.split()\n",
        spec_impl=(
            "def f_split_ws(s):\n"
            "    out, cur = [], ''\n"
            "    for ch in s:\n"
            "        if ch in ' \\t\\n\\r\\f\\v':\n"
            "            if cur:\n"
            "                out.append(cur); cur = ''\n"
            "        else:\n"
            "            cur += ch\n"
            "    if cur:\n"
            "        out.append(cur)\n"
            "    return out\n"
        ),
        partial=(
            "def f_split_ws(s):\n"
            "    return [x for x in s.split(' ') if x]\n"
        ),
    ),
    Spec(
        id="ex02", fn="f_normpath", params="p",
        body=[
            "把 POSIX 路径 p 规范化：合并连续的斜杠、去掉单个 `.` 段",
            "`..` 段吃掉它前面一个普通段；若它在开头（或在一串 `..` 的最前面）且路径不是绝对路径，就**保留**它",
            "绝对路径（以 `/` 开头）上的 `..` **不许**越过根，多余的直接丢掉",
            "结果若为空串，返回 `.`",
            "**输入域**：p 不会以两个斜杠开头",
        ],
        gen=gen_normpath,
        ref=lambda p: posixpath.normpath(p),
        oracle="import posixpath\ndef f_normpath(p):\n    return posixpath.normpath(p)\n",
        spec_impl=(
            "def f_normpath(p):\n"
            "    absp = p.startswith('/')\n"
            "    out = []\n"
            "    for seg in p.split('/'):\n"
            "        if seg == '' or seg == '.':\n"
            "            continue\n"
            "        if seg == '..':\n"
            "            if out and out[-1] != '..':\n"
            "                out.pop()\n"
            "            elif not absp:\n"
            "                out.append('..')\n"
            "            continue\n"
            "        out.append(seg)\n"
            "    body = '/'.join(out)\n"
            "    if absp:\n"
            "        return '/' + body if body else '/'\n"
            "    return body if body else '.'\n"
        ),
        partial=(
            "def f_normpath(p):\n"
            "    absp = p.startswith('/')\n"
            "    out = []\n"
            "    for seg in p.split('/'):\n"
            "        if seg in ('', '.'):\n"
            "            continue\n"
            "        if seg == '..':\n"
            "            out.append('..')\n"
            "            continue\n"
            "        out.append(seg)\n"
            "    body = '/'.join(out)\n"
            "    if absp:\n"
            "        return '/' + body if body else '/'\n"
            "    return body if body else '.'\n"
        ),
    ),
    Spec(
        id="ex03", fn="f_splitext", params="p",
        body=[
            "把 p 拆成 (根, 扩展名) 两元组，扩展名**带点**",
            "取该路径段里**最后一个点**；但如果这个点之前、在**同一段内**的字符全是点"
            "（或者根本没有字符，即点在段首），则视为没有扩展名",
            "没有可用的点，或整段都是点时，扩展名为空串，整个 p 作为根",
        ],
        gen=gen_splitext,
        ref=lambda p: posixpath.splitext(p),
        oracle="import posixpath\ndef f_splitext(p):\n    return posixpath.splitext(p)\n",
        spec_impl=(
            "def f_splitext(p):\n"
            "    i = p.rfind('.')\n"
            "    slash = p.rfind('/')\n"
            "    if i <= slash:\n"
            "        return (p, '')\n"
            "    if not p[slash + 1:i].strip('.'):\n"
            "        return (p, '')\n"
            "    return (p[:i], p[i:])\n"
        ),
        partial=(
            "def f_splitext(p):\n"
            "    i = p.rfind('.')\n"
            "    slash = p.rfind('/')\n"
            "    if i <= slash:\n"
            "        return (p, '')\n"
            "    return (p[:i], p[i:])\n"
        ),
    ),
    Spec(
        id="ex04", fn="f_expandtabs", params="s, n",
        body=[
            "把 s 里的制表符换成空格，使下一个字符落在**列号**为 n 的整数倍处",
            "列号从 0 开始计，换行符把列号**重置为 0**",
            "非制表符原样保留，并让列号加一",
            "至少补一个空格（本来就对齐也要补 n 个）",
        ],
        gen=gen_expandtabs,
        ref=lambda s, n: s.expandtabs(n),
        oracle="def f_expandtabs(s, n):\n    return s.expandtabs(n)\n",
        spec_impl=(
            "def f_expandtabs(s, n):\n"
            "    out, col = [], 0\n"
            "    for ch in s:\n"
            "        if ch == '\\t':\n"
            "            pad = n - (col % n)\n"
            "            out.append(' ' * pad)\n"
            "            col += pad\n"
            "        elif ch == '\\n':\n"
            "            out.append(ch)\n"
            "            col = 0\n"
            "        else:\n"
            "            out.append(ch)\n"
            "            col += 1\n"
            "    return ''.join(out)\n"
        ),
        partial=(
            "def f_expandtabs(s, n):\n"
            "    return s.replace('\\t', ' ' * n)\n"
        ),
    ),
    Spec(
        id="ex05", fn="f_urlencode", params="pairs",
        body=[
            "pairs 是若干 (键, 值) 二元组，都是字符串",
            "输出 `键=值` 用 `&` 连接，**顺序与输入一致**",
            "键和值都要做 URL 百分号编码：ASCII 字母数字和 `_.-~` 原样，**空格编成 `+`**，"
            "其余（含 `/`、`&`、`=`、`#`）按 UTF-8 的每个字节编成 `%XX` 大写",
        ],
        gen=gen_urlencode,
        ref=lambda pairs: urllib.parse.urlencode(pairs),
        oracle="import urllib.parse\ndef f_urlencode(pairs):\n    return urllib.parse.urlencode(pairs)\n",
        spec_impl=(
            "def f_urlencode(pairs):\n"
            "    def q(x):\n"
            "        out = []\n"
            "        for b in x.encode('utf-8'):\n"
            "            c = chr(b)\n"
            "            if c.isalnum() and b < 128 or c in '_.-~':\n"
            "                out.append(c)\n"
            "            elif c == ' ':\n"
            "                out.append('+')\n"
            "            else:\n"
            "                out.append('%%%02X' % b)\n"
            "        return ''.join(out)\n"
            "    return '&'.join(q(k) + '=' + q(v) for k, v in pairs)\n"
        ),
        partial=(
            "def f_urlencode(pairs):\n"
            "    import urllib.parse\n"
            "    return '&'.join(urllib.parse.quote(k) + '=' + urllib.parse.quote(v)\n"
            "                    for k, v in pairs)\n"
        ),
    ),
    Spec(
        id="ex06", fn="f_b32", params="b",
        body=[
            "b 是 bytes，用 RFC 4648 base32 编码，返回**大写** ASCII 字符串",
            "每 5 个字节编成 8 个字符，不足 5 字节的尾部按位补零",
            "输出长度必须是 8 的整数倍，不足用 `=` 补齐",
            "空输入返回空串",
        ],
        gen=gen_b32,
        ref=lambda b: base64.b32encode(b).decode("ascii"),
        oracle="import base64\ndef f_b32(b):\n    return base64.b32encode(b).decode('ascii')\n",
        spec_impl=(
            "def f_b32(b):\n"
            "    A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'\n"
            "    bits = 0\n"
            "    nbits = 0\n"
            "    out = []\n"
            "    for byte in b:\n"
            "        bits = (bits << 8) | byte\n"
            "        nbits += 8\n"
            "        while nbits >= 5:\n"
            "            out.append(A[(bits >> (nbits - 5)) & 31])\n"
            "            nbits -= 5\n"
            "    if nbits:\n"
            "        out.append(A[(bits << (5 - nbits)) & 31])\n"
            "    while len(out) % 8:\n"
            "        out.append('=')\n"
            "    return ''.join(out)\n"
        ),
        partial=(
            "def f_b32(b):\n"
            "    A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'\n"
            "    bits = 0\n"
            "    nbits = 0\n"
            "    out = []\n"
            "    for byte in b:\n"
            "        bits = (bits << 8) | byte\n"
            "        nbits += 8\n"
            "        while nbits >= 5:\n"
            "            out.append(A[(bits >> (nbits - 5)) & 31])\n"
            "            nbits -= 5\n"
            "    if nbits:\n"
            "        out.append(A[(bits << (5 - nbits)) & 31])\n"
            "    return ''.join(out)\n"
        ),
    ),
    Spec(
        id="ex07", fn="f_median_low", params="data",
        body=[
            "data 是非空的整数列表（可能有重复）",
            "返回**低中位数**：排序后，若长度是奇数取正中间那个；若是偶数取**偏小的那个**",
            "不许修改传入的列表",
        ],
        gen=gen_median_low,
        ref=lambda data: statistics.median_low(data),
        oracle="import statistics\ndef f_median_low(data):\n    return statistics.median_low(data)\n",
        spec_impl=(
            "def f_median_low(data):\n"
            "    s = sorted(data)\n"
            "    return s[(len(s) - 1) // 2]\n"
        ),
        partial=(
            "def f_median_low(data):\n"
            "    s = sorted(data)\n"
            "    return s[len(s) // 2]\n"
        ),
    ),
    Spec(
        id="ex08", fn="f_nlargest", params="k, data",
        body=[
            "data 是整数列表，k 是正整数且不大于 len(data)",
            "返回**前 k 大**的元素，按**降序**排好的列表",
            "重复元素按个数保留",
        ],
        gen=gen_nlargest,
        ref=lambda k, data: heapq.nlargest(k, data),
        oracle="import heapq\ndef f_nlargest(k, data):\n    return heapq.nlargest(k, data)\n",
        spec_impl=(
            "def f_nlargest(k, data):\n"
            "    return sorted(data, reverse=True)[:k]\n"
        ),
        partial=(
            "def f_nlargest(k, data):\n"
            "    return sorted(data)[:k]\n"
        ),
    ),
    Spec(
        id="ex09", fn="f_dedent", params="s",
        body=[
            "去掉 s 里**所有行共有**的前导空白（只认空格）",
            "空行（去掉空白后为空）**不参与**统计，也不阻挡",
            "去掉的量等于所有非空行前导空格数的**最小值**",
            "每行只在行首去掉这么多空格，行内空白不动；空行原样保留为空行",
        ],
        gen=gen_dedent,
        ref=lambda s: textwrap.dedent(s),
        oracle="import textwrap\ndef f_dedent(s):\n    return textwrap.dedent(s)\n",
        spec_impl=(
            "def f_dedent(s):\n"
            "    lines = s.split('\\n')\n"
            "    ind = []\n"
            "    for ln in lines:\n"
            "        if ln.strip():\n"
            "            ind.append(len(ln) - len(ln.lstrip(' ')))\n"
            "    m = min(ind) if ind else 0\n"
            "    out = []\n"
            "    for ln in lines:\n"
            "        if ln.strip():\n"
            "            out.append(ln[m:])\n"
            "        else:\n"
            "            out.append(ln[m:] if len(ln) >= m else '')\n"
            "    return '\\n'.join(out)\n"
        ),
        partial=(
            "def f_dedent(s):\n"
            "    lines = [ln for ln in s.split('\\n')]\n"
            "    ind = [len(ln) - len(ln.lstrip(' ')) for ln in lines]\n"
            "    m = min(ind) if ind else 0\n"
            "    return '\\n'.join(ln[m:] for ln in lines)\n"
        ),
    ),
    Spec(
        id="ex10", fn="f_quote", params="s",
        body=[
            "对 s 做 URL 百分号编码，返回字符串",
            "ASCII 字母数字和 `_.-~/` **原样保留**",
            "其余字符（含空格、`?`、`#`、`%`）按 UTF-8 的每个字节编成 `%XX` 大写",
            "空格编成 `%20`（**不是** `+`）",
        ],
        gen=gen_quote,
        ref=lambda s: urllib.parse.quote(s),
        oracle="import urllib.parse\ndef f_quote(s):\n    return urllib.parse.quote(s)\n",
        spec_impl=(
            "def f_quote(s):\n"
            "    out = []\n"
            "    for b in s.encode('utf-8'):\n"
            "        c = chr(b)\n"
            "        if (c.isalnum() and b < 128) or c in '_.-~/':\n"
            "            out.append(c)\n"
            "        else:\n"
            "            out.append('%%%02X' % b)\n"
            "    return ''.join(out)\n"
        ),
        partial=(
            "def f_quote(s):\n"
            "    out = []\n"
            "    for b in s.encode('utf-8'):\n"
            "        c = chr(b)\n"
            "        if (c.isalnum() and b < 128) or c in '_.-~':\n"
            "            out.append(c)\n"
            "        else:\n"
            "            out.append('%%%02X' % b)\n"
            "    return ''.join(out)\n"
        ),
    ),
]


# ===========================================================================
# 渲染与判分（判分器与内核无关：exec 一遍，再逐条跑断言）
# ===========================================================================
def render_prompt(sp: Spec) -> str:
    rules = "\n".join(f"  {i + 1}. {b}" for i, b in enumerate(sp.body))
    return (f"写一个函数 {sp.fn}({sp.params})，返回处理后的结果。"
            f"按下面列出的规则实现：\n{rules}\n"
            f"只输出代码，函数名必须是 {sp.fn}。")


def cases_of(sp: Spec, seed: int = SEED, n: int = CASES) -> list[tuple]:
    rng = random.Random(f"{seed}:{sp.id}")
    return [sp.gen(rng) for _ in range(n)]


def tests_of(sp: Spec, seed: int = SEED, n: int = CASES) -> list[str]:
    out = []
    for args in cases_of(sp, seed, n):
        want = sp.ref(*args)
        arg_s = ", ".join(repr(a) for a in args)
        out.append(f"assert {sp.fn}({arg_s}) == {want!r}")
    return out


def score(src: str, tests: list[str]) -> int:
    """与核无关的打分。空源码 = 0。"""
    if not src.strip():
        return 0
    ns: dict = {}
    try:
        exec(compile(src, "<v3>", "exec"), ns)          # noqa: S102
    except Exception:                                    # noqa: BLE001
        return 0
    hit = 0
    for t in tests:
        try:
            exec(compile(t, "<v3t>", "exec"), ns)        # noqa: S102
            hit += 1
        except Exception:                                # noqa: BLE001
            pass
    return hit


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
                "tags": "external-oracle,stdlib-reimpl",
                "level": sp.level,
                "seed": seed,
                "cases": n,
                "oracle_source": "CPython 标准库实现现算（不是 specgen 渲染）",
            },
        })
    return rows


# ===========================================================================
# 七条自检
# ===========================================================================
def self_check() -> int:
    print("=" * 74)
    print(f"  v3 尺子自检（seed={SEED}，每题 {CASES} 条断言，共 "
          f"{len(SPECS) * CASES} 格）")
    print("=" * 74)

    rows = rows_of()
    bad = 0

    print("\n[S1/S2/S3/S4] 逐题标定（oracle 满 / spec_impl 满 / partial 居中 / null 零）")
    print(f"  {'题':5s} {'oracle':>7s} {'spec':>5s} {'partial':>8s} {'null':>5s}  判定")
    for sp, r in zip(SPECS, rows):
        o = score(sp.oracle, r["tests"])
        s = score(sp.spec_impl, r["tests"])
        p = score(sp.partial, r["tests"])
        z = score(r["null"], r["tests"])
        n = len(r["tests"])
        ok = (o == n) and (s == n) and (0 < p < n) and (z == 0)
        if not ok:
            bad += 1
        print(f"  {sp.id:5s} {o:3d}/{n:<3d} {s:3d}/{n:<1d} {p:5d}/{n:<2d} {z:3d}/{n:<1d}  "
              f"{'✅' if ok else '❌'}")

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
            want = t.split("==", 1)[1].strip()
            if len(want) >= 4 and want in r["prompt"]:
                leaks.append(f"{sp.id}:{want[:24]}")
    s7 = not leaks
    print(f"[S7] 期望值泄漏进题面：{'✅ 无' if s7 else '❌ ' + str(leaks[:3])}")
    bad += 0 if s7 else 1

    print(f"\n{'✅ 七条自检全绿' if bad == 0 else f'❌ {bad} 条自检失败'}")
    return 0 if bad == 0 else 1


def emit(path: str) -> int:
    rows = rows_of()
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"已写 {path}（{len(rows)} 题 / {sum(len(r['tests']) for r in rows)} 格）")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", nargs="?", const="bench/specgen/tasks_v3.jsonl")
    a = ap.parse_args()
    rc = self_check()
    if a.emit and rc == 0:
        rc = emit(a.emit)
    sys.exit(rc)
