#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三种**等长** mapper —— 安慰剂的机制层。

★ 为什么这三条必须住在一起
  第十七轮为「抽象层」（§16.16）手搓了 `scramble / text` 两种，
  第十八轮为「假记忆」（§16.17）又手搓了 `cipher`。两次都从零写了一遍
  "等长 + 确定性 + 不等于原文" 的闸门 —— 那两份闸门迟早会漂。
  ⇒ 本轮把它们合成一份（DESIGN §17-16）。**合并的只有机制，不含判据**：
    判据仍然各自躺在 `probe_placebo.py` / `probe_fakemem.py` / `probe_shape.py` 里，
    因为第十八轮的真实教训是「判读实现有两份 ⇒ 立刻漂」。
     合并机制 = 少一处可能漂的实现；
     合并判据 = 把三个不同问题的答案硬塞进一个函数。

★ 三个 mapper 各自钉住一个因子（这是整轮实验的设计，不只是实现细节）

  | mapper    | 保 长度 | 保 行几何 | 保 字符身份 | 动的是什么 |
  |-----------|--------|----------|------------|-----------|
  | `cipher`  |   ✅   |    ✅    |     ❌     | **字符身份**（语义） |
  | `scramble`|   ✅   |    ❌    |     ❌     | 语序 + 行几何，全毁 |
  | `reshape` |   ✅   |    ❌    |     ✅     | **行几何**（形状） |

  ⇒ `cipher ≡ 真记忆`（第十八轮实测 5/5 逐字相同）说明**语义**贡献为 0；
    但那句话**证不了形状**：`cipher` 把字符数、逐行长度、缩进、标点分布
    全部原样保留 —— 一个"看起来完全像代码"的方块。
    要拆开"形状有没有贡献"，必须有一个**保字符、破几何**的臂：`reshape`
    （DESIGN §17-15）。它是本轮新增的那一条。

★ 三条硬约束（实现在这里，判据在契约里）
  1. 等长：`len(mapper(s)) == len(s)`。不等 ⇒ 多出来那几个字就是第二个自变量。
  2. 确定性：同一输入同一输出。mapper 里掺一点时间/随机数，两道题看到的
     就不是同一份"假记忆"。
  3. 有内容必相异：等于原文就不是安慰剂，是实验组本人。
     唯一的例外由**调用方**（`patch._check`）按条计数放行，且只放行两种情况：
     除空白外没有字符；或该字符串**没有任何可挪的结构**（见 `reshape`）。
"""

from __future__ import annotations

import hashlib
import random

#: CLI / runner 可选的 mapper 名。顺序即文档顺序。
MAPPER_NAMES = ("scramble", "cipher", "reshape")


def md5(s: str) -> str:
    """短哈希，只用于留痕与派生种子（不是安全用途）。"""
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]


def _rng(tag: str, text: str, seed: int) -> random.Random:
    """从 (tag, seed, 原文) 派生一个确定性 RNG。

    ⛔ 不用 `random.seed()` 全局种子：那会让"两次调用之间有没有别的调用"
       影响结果 —— 实验里这种隐式耦合等于每次跑都在换自变量。
    """
    h = hashlib.sha256(f"{tag}:{seed}:{md5(text)}:{len(text)}".encode("utf-8"))
    return random.Random(int.from_bytes(h.digest()[:8], "big"))


# ═══════════════════════════════════════════════════════════════════════════
# ① scramble —— 打乱（同字符多重集）
# ═══════════════════════════════════════════════════════════════════════════
def scramble(text: str, seed: int = 17) -> str:
    """把 `text` 的字符打乱 —— **必然等长**，且字符多重集不变。

    为什么打乱是合格的安慰剂：它保留了全部**表层统计量**
    （长度、字集、字频），只摧毁**语序承载的语义**。

    自己写 Fisher–Yates 而不用 `random.shuffle`：后者跨 Python 版本的行为
    没有承诺。这里连种子都用原文的 sha256 派生，给定输入必然同一输出。

    ⛔ 改这个函数会改变第十七/十八轮已冻结证据的可复现性 ——
       契约 AQ 组用 s18 冻结的 `examples` 逐字节回归锁住它。
    """
    chars = list(text)
    for attempt in range(1000):
        # ⛔ 这里的种子派生**必须逐字保持第十七轮的原式**。
        #   重构时我一度换成 `_rng(tag, text, seed)`，多拼了一段 `:len` ——
        #   于是同一个输入的排列变了，第十七轮冻结的 D1 读数当场不可复现，
        #   而它**不会报错**：只会让"重跑一次"和"当年那次"悄悄不是同一个臂。
        #   契约 AQ2 用 s17/s18 冻结的 examples 逐字节锁住这一条。
        r = random.Random(int.from_bytes(
            hashlib.sha256(f"{seed}:{attempt}:{md5(text)}".encode("utf-8")).digest()[:8],
            "big"))
        ys = list(chars)
        for i in range(len(ys) - 1, 0, -1):
            j = r.randrange(i + 1)
            ys[i], ys[j] = ys[j], ys[i]
        out = "".join(ys)
        if out != text:          # 概率极低也要挡：等于原文就不是安慰剂
            return out
    raise RuntimeError(f"打乱 1000 次都没能产生不同于原文的排列：{text!r}")


# ═══════════════════════════════════════════════════════════════════════════
# ② cipher —— 逐字符双射（保结构、灭语义）
# ═══════════════════════════════════════════════════════════════════════════
_DIGIT = str.maketrans("0123456789", "5274019683")
_LOWER = str.maketrans("abcdefghijklmnopqrstuvwxyz", "qwertyuiopasdfghjklzxcvbnm")
_UPPER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "QWERTYUIOPASDFGHJKLZXCVBNM")
_CJK_LO, _CJK_HI = 0x4E00, 0x9FFF
_CJK_SHIFT = 3019          # 与区间长度互素即可保证是双射


def cipher(text: str) -> str:
    """逐字符**双射**替换：长度与格式结构逐位不变，语义归零。

    为什么要逐字符而不是"另写一句等长的话"：
      那样会同时改变**换行位置、缩进、标点分布**。而此刻被替换的是前面
      几道题的答案 —— 那是**代码**，缩进和换行本身就是它的结构。
      逐字符替换保留了一切几何信息（第 7 行第 5 列仍是一个空格），
      只把每一个字换成另一个字 ⇒ 剩下唯一的区别就是"字本身"。

    CJK 在区间内循环移位（双射、确定性）；其余字符（含所有空白标点）
    **原样保留** —— 一旦连它们也换，"结构相同"这个承诺就破了。
    """
    out = []
    for ch in text:
        if ch.isdigit():
            out.append(ch.translate(_DIGIT))
        elif ch.isascii() and ch.isalpha():
            out.append(ch.translate(_LOWER if ch.islower() else _UPPER))
        elif _CJK_LO <= ord(ch) <= _CJK_HI:
            span = _CJK_HI - _CJK_LO + 1
            out.append(chr(_CJK_LO + (ord(ch) - _CJK_LO + _CJK_SHIFT) % span))
        else:
            out.append(ch)
    return "".join(out)


# ═══════════════════════════════════════════════════════════════════════════
# ③ reshape —— 保字符、破行几何（本轮新增的那一条）
# ═══════════════════════════════════════════════════════════════════════════
def _is_word(ch: str) -> bool:
    """"词内字符"。CJK 也算 —— 所以一整句中文是**一个词**，见 `reshape` 的注释。"""
    return ch.isalnum()


def reshape(text: str, seed: int = 17, stats: dict | None = None) -> str:
    """**保长度、保行数、保字符序列**，只把**换行位置**换掉（行几何）。

    这是"形状"这个因子的操纵臂：`cipher` 把每一行都变成等长等缩进的乱码，
    它保留了"这坨东西看起来像代码"的全部几何证据；
    `reshape` 反过来 —— 每个字都还在、顺序也没变，但行被重新切过，
    缩进、行宽、`def / return` 出现的列位置全部错位。

    ⇒ `E3 − F` 才是"形状有没有贡献"的读数（判据在 `probe_shape.py`）。

    ★ 切割点只落在**词边界**上（相邻两字符不都是词内字符）。
      为什么不许随便切：随便切会把 `return` 切成 `ret` + `urn`，
      那就在"破几何"的同时**破了 token** —— 两个因子一起动，
      这轮实验就又回到分不开的状态（那正是 §17-15 要修的病）。
      中文句子整句都是"词内字符" ⇒ 没有安全边界 ⇒ 退化为任意位置并**计数**
      （`shape_unsafe_cuts`）。这个退化必须看得见，不许悄悄发生。

    ★ 没有换行时（单行记录），行几何无定义 ⇒ 改为**挪动空白的位置**
      （保字符多重集、保非空白字符的相对顺序、保长度，只改缩进/空格的位置）。

    ★ 连空白都没有（如 `a=1&b=2`、无空格的中文短语）⇒ 没有任何可挪的结构，
      **原样返回**并让调用方计一次 `shape_identity`。
      这是本臂唯一的盲区，必须如实计入读数 —— 不能假装它也变了。

    `stats` 是调用方传进来的计数器（`patch._STATS`）；为 None 时只是不留痕。
    """
    def _bump(k: str, n: int = 1) -> None:
        if stats is not None:
            stats[k] = int(stats.get(k, 0) or 0) + n

    lines = text.split("\n")
    if len(lines) < 2:
        return _reshape_gaps(text, seed, _bump)

    body = "".join(lines)             # 去掉换行后的字符序列（顺序保持不变）
    total = len(body)
    old = [len(x) for x in lines]
    need = len(lines) - 1             # 需要切的刀数

    safe = [i for i in range(1, total)
            if not (_is_word(body[i - 1]) and _is_word(body[i]))]
    if len(safe) >= need:
        pool = safe
    else:
        pool = list(range(1, total))
        _bump("shape_unsafe_cuts")
    if len(pool) < need:              # 字符太少，连切 need 刀都做不到
        _bump("shape_identity")
        return text

    r = _rng("reshape", text, seed)
    for _ in range(200):
        cuts = sorted(r.sample(pool, need))       # need ≥ 1（len(lines) ≥ 2）
        lens = [cuts[0]]
        lens += [cuts[i] - cuts[i - 1] for i in range(1, need)]
        lens += [total - cuts[-1]]
        if lens != old:
            out, pos = [], 0
            for n in lens:
                out.append(body[pos:pos + n])
                pos += n
            new = "\n".join(out)
            # 自检：等长 + 等行数，两个都是硬约束（不是"应该"，是**必须**）
            assert len(new) == len(text), (len(new), len(text))
            assert len(new.split("\n")) == len(lines)
            _bump("shape_lines_moved")
            return new
    _bump("shape_identity")           # 200 次都没能切出不同的行宽
    return text


def _reshape_gaps(text: str, seed: int, bump) -> str:
    """单行记录：挪空白的位置（保长度、保非空白字符的相对顺序）。"""
    gaps = [i for i, ch in enumerate(text) if ch in " \t"]
    if not gaps or len(text) < 2:
        bump("shape_identity")
        return text
    r = _rng("shape-gaps", text, seed)
    for _ in range(200):
        new = sorted(r.sample(range(len(text)), len(gaps)))
        if new != gaps:
            ws = [text[i] for i in gaps]
            rest = [ch for ch in text if ch not in " \t"]
            out, wi, ri = [], 0, 0
            for pos in range(len(text)):
                if wi < len(new) and new[wi] == pos:
                    out.append(ws[wi])
                    wi += 1
                else:
                    out.append(rest[ri])
                    ri += 1
            s = "".join(out)
            assert len(s) == len(text), (len(s), len(text))
            bump("shape_spaces_moved")
            return s
    bump("shape_identity")
    return text


def fixed_text(new: str):
    """CLI 用：忽略原文，一律返回调用方给定的那段文本。

    ⛔ 它**不是** mapper 的第四种语义，只是把"人手写的安慰剂"接进同一个接口 ——
       等长仍然由调用方的闸门逐条检查（AO4 钉住）。
    """
    def fn(_old: str) -> str:
        return new
    return fn
