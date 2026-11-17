"""关系型判据库（人写）—— V183 的**天花板**。

⛔ 每一条都守同一条纪律：**判据里不许出现「期望输出」的字面量**。
   判定全部由通用代码/标准库算：把实现的输出喂给 parse/unquote/configparser/
   Counter/子序列判定，看它是否与**输入侧**自洽。

   这就是与 V79 断言的结构差别：
     V79 断言  =  f(x) == '<模型读出来的那个串>'      ← 期望值是模型的读法
     关系判据  =  decode(f(x)) == x                  ← 期望值是**输入的变换**

   前者的判定依赖模型对格式的理解；后者一旦 decode 由 stdlib 提供，模型的
   格式误解**没有入口**进到判定里。

⛔ 探针输入全部是本文件另写的，⛔ 不取题集 `tests`（那是隐藏判分例）。
"""
from __future__ import annotations

#: 一个关系 = 一段 `_rel()`：通过返回 True，不通过抛 AssertionError（带实得）。
#: 组合方式：`impl_code + "\n\n" + src`，判分仍走 `run_example(..., "assert _rel()")`。
RELATIONS: list[dict] = [
    # ─────────────────────────── k01 diff_tree ───────────────────────────
    {
        "task": "k01_diff_tree",
        "name": "EMPTY_IFF_EQUAL",
        "why": "差异集为空 ⟺ 两个输入相等（把「相等」交给 == 判，不交给模型读题）",
        "src": '''
def _rel():
    cases = [
        ({'a': 1}, {'a': 1}),
        ({'a': 1}, {'a': 2}),
        ({'a': {'b': 1}}, {'a': {'b': 1}}),
        ({'a': {'b': 1}}, {'a': {'b': 2}}),
        ({'x': {'y': {'z': 1}}}, {'x': {'y': {'z': 1}}}),
        ({}, {}),
        ({'k': None}, {'k': None}),
    ]
    for a, b in cases:
        got = diff_tree(a, b)
        if (got == {}) != (a == b):
            raise AssertionError(f"EMPTY_IFF_EQUAL: {a!r} vs {b!r} ⇒ {got!r}")
    return True
''',
    },
    {
        "task": "k01_diff_tree",
        "name": "DIFF_PAIR_SHAPE",
        "why": "每个差异值必须是 [va, vb] 两元、且两值不等（报告了「没差异」= 假差异）",
        "src": '''
def _rel():
    cases = [({'a': 1}, {'a': 2}), ({'p': {'q': 1}}, {'p': {'q': 9}}),
             ({'m': [1]}, {'m': [2]})]
    for a, b in cases:
        got = diff_tree(a, b)
        if not got:
            raise AssertionError(f"DIFF_PAIR_SHAPE: {a!r} vs {b!r} ⇒ 空，但输入不等")
        for k, v in got.items():
            if not (isinstance(v, list) and len(v) == 2):
                raise AssertionError(f"DIFF_PAIR_SHAPE: 路径 {k!r} 的值 {v!r} 不是两元")
            if v[0] == v[1]:
                raise AssertionError(f"DIFF_PAIR_SHAPE: 路径 {k!r} 报了两边相同的值 {v!r}")
    return True
''',
    },
    {
        "task": "k01_diff_tree",
        "name": "LEAF_ONLY",
        "rev": 2,
        "why": "报告的必须是**叶子**差异：值不许是容器（把整棵子树报出来 = 没递归）",
        "src": '''
def _rel():
    cases = [({'a': {'b': 1}}, {'a': {'b': 2}}),
             ({'x': {'y': {'z': 1}}}, {'x': {'y': {'z': 2}}})]
    for a, b in cases:
        got = diff_tree(a, b)
        if not got:
            raise AssertionError(f"LEAF_ONLY: {a!r} vs {b!r} ⇒ 空，但输入不等")
        for k, v in got.items():
            a_, b_ = v[0], v[1]
            if isinstance(a_, (dict, list)) or isinstance(b_, (dict, list)):
                raise AssertionError(f"LEAF_ONLY: 路径 {k!r} 报的是子树 {v!r}")
    return True
''',
    },
    # ───────────────────────── k02 encode_cookie ─────────────────────────
    {
        "task": "k02_encode_cookie",
        "name": "ROUNDTRIP_PARSE_QSL",
        "why": "stdlib 解回来必须等于输入（`unquote` 是外部实现 ⇒ 二次编码无处可藏）",
        "src": '''
from urllib.parse import parse_qsl

def _rel():
    cases = [
        {'a': 'b c'},
        {'a': 'p%q'},
        {'a': 'x;y'},
        {'a': 'm,n'},
        {'a': 'k=v'},
        {'b': 2, 'a': 1},
        {'z': 'last', 'a': 'first', 'm': 'mid'},
    ]
    for d in cases:
        got = encode_cookie(d)
        if not isinstance(got, str):
            raise AssertionError(f"ROUNDTRIP_PARSE_QSL: {d!r} ⇒ 非 str {got!r}")
        back = {k.strip(): v for k, v in
                parse_qsl(got, keep_blank_values=True, separator=';')}
        want = {str(k): str(v) for k, v in d.items()}
        if back != want:
            raise AssertionError(f"ROUNDTRIP_PARSE_QSL: {d!r} ⇒ {got!r} 解回 {back!r} ≠ {want!r}")
    return True
''',
    },
    {
        "task": "k02_encode_cookie",
        "name": "SORTED_AND_SKIP_NONE",
        "why": "键按字典序、None 键不出现 —— 两条都能从输出结构直接判",
        "src": '''
from urllib.parse import parse_qsl

def _rel():
    d = {'z': 'zz', 'a': 'aa', 'm': 'mm', 'skip': None}
    got = encode_cookie(d)
    names = [k.strip() for k, _ in
             parse_qsl(got, keep_blank_values=True, separator=';')]
    if names != sorted(names):
        raise AssertionError(f"SORTED_AND_SKIP_NONE: 键序 {names!r} 不是字典序")
    if 'skip' in names:
        raise AssertionError(f"SORTED_AND_SKIP_NONE: 值为 None 的键出现了：{got!r}")
    return True
''',
    },
    # ─────────────────────────── k03 unflatten ───────────────────────────
    {
        "task": "k03_unflatten",
        "name": "LEAF_MULTISET_CONSERVED",
        "why": "输出叶子值的多重集必须等于输入值的多重集（信息守恒，不需要知道路径规则）",
        "src": '''
def _leaves(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from _leaves(v)
    elif isinstance(x, list):
        for v in x:
            yield from _leaves(v)
    else:
        yield x

from collections import Counter

def _rel():
    cases = [
        {'a': 1},
        {'a.b': 1, 'c': 2},
        {'k[0]': 7, 'k[1]': 8},
        {'m.n.o': 'x'},
        {'p[2]': 5},
        {'a.b': 1, 'a.c': 2, 'd': 3},
    ]
    for flat in cases:
        got = unflatten(flat)
        have = Counter(v for v in _leaves(got) if v is not None)
        want = Counter(flat.values())
        if have != want:
            raise AssertionError(f"LEAF_MULTISET_CONSERVED: {flat!r} ⇒ 叶子 {dict(have)!r} ≠ {dict(want)!r}")
    return True
''',
    },
    {
        "task": "k03_unflatten",
        "name": "ROUNDTRIP_FLATTEN",
        "why": "外部写的 flatten 回展必须逐键等于输入（往返，期望值是输入的变换）",
        "src": '''
def _flat(x, prefix=""):
    out = {}
    if isinstance(x, dict):
        for k, v in x.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            out.update(_flat(v, key))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            if v is None:
                continue
            out.update(_flat(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = x
    return out

def _rel():
    cases = [{'a': 1}, {'a.b': 1, 'c': 2}, {'k[0]': 7, 'k[1]': 8},
             {'m.n.o': 'x'}, {'a.b': 1, 'a.c': 2, 'd': 3}]
    for flat in cases:
        back = _flat(unflatten(flat))
        if back != flat:
            raise AssertionError(f"ROUNDTRIP_FLATTEN: {flat!r} ⇒ 回展 {back!r}")
    return True
''',
    },
    {
        "task": "k03_unflatten",
        "name": "NO_PATH_CHARS_IN_KEYS",
        "rev": 2,
        "why": "还原后的 dict 键里不许再出现 '.' 或 '['（路径分隔符必须被**兑现**成结构）",
        "src": '''
def _walk(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield str(k)
            yield from _walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from _walk(v)

def _rel():
    cases = [{'a.b': 1}, {'k[0]': 7, 'k[1]': 8}, {'m.n.o': 'x'}, {'p[2]': 5}]
    for flat in cases:
        got = unflatten(flat)
        for k in _walk(got):
            if '.' in k or '[' in k:
                raise AssertionError(f"NO_PATH_CHARS_IN_KEYS: {flat!r} ⇒ 键 {k!r} 里还有路径字符")
    return True
''',
    },
    # ──────────────────────────── k04 to_ini ─────────────────────────────
    {
        "task": "k04_to_ini",
        "name": "ROUNDTRIP_CONFIGPARSER",
        "why": "configparser 读回来必须等于输入（None→空串、bool→true/false 由外部解析器认）",
        "src": '''
import configparser

def _rel():
    cases = [
        {'s': {'a': 1}},
        {'s': {'a': None}},
        {'s': {'a': True, 'b': False}},
        {'s': {}, 't': {'b': 2}},
        {'alpha': {'x': 1}, 'beta': {'y': 'z'}},
    ]
    for d in cases:
        got = to_ini(d)
        if not isinstance(got, str):
            raise AssertionError(f"ROUNDTRIP_CONFIGPARSER: {d!r} ⇒ 非 str")
        cp = configparser.ConfigParser()
        cp.optionxform = str
        try:
            cp.read_string(got)
        except Exception as e:
            raise AssertionError(f"ROUNDTRIP_CONFIGPARSER: {d!r} ⇒ {got!r} 解不开：{e}")
        back = {s: dict(cp[s]) for s in cp.sections()}
        want = {str(s): {str(k): ("" if v is None else ("true" if v is True
                 else "false" if v is False else str(v))) for k, v in body.items()}
                for s, body in d.items()}
        if back != want:
            raise AssertionError(f"ROUNDTRIP_CONFIGPARSER: {d!r} ⇒ {got!r} 解回 {back!r} ≠ {want!r}")
    return True
''',
    },
    {
        "task": "k04_to_ini",
        "name": "TRAILING_NEWLINE",
        "why": "非空输出必须以换行结尾（题面明写，且与「空 dict 返回空串」可同时判）",
        "src": '''
def _rel():
    for d in ({'s': {'a': 1}}, {'s': {'a': None}}, {'alpha': {'x': 1}, 'beta': {'y': 'z'}}):
        got = to_ini(d)
        if not got.endswith("\\n"):
            raise AssertionError(f"TRAILING_NEWLINE: {d!r} ⇒ {got!r} 没以换行结尾")
        if got.endswith("\\n\\n"):
            raise AssertionError(f"TRAILING_NEWLINE: {d!r} ⇒ {got!r} 末尾多了一个空行")
    if to_ini({}) != "":
        raise AssertionError(f"TRAILING_NEWLINE: 空 dict ⇒ {to_ini({})!r}，应为空串")
    return True
''',
    },
    # ─────────────────────── g7d2 dedupe_keep ────────────────────────────
    {
        "task": "g7d2_dedupe_keep",
        "name": "NORMALIZED_UNIQUE_AND_SUBSEQ",
        "why": "输出无归一化重复（strip+lower 后两两不等）且是输入的子序列 —— 都是结构判定",
        "src": '''
def _rel():
    cases = [['A', 'a'], ['b', ' a ', 'A'], ['x', 'y', 'X', 'y '], ['a', 'b', 'A'],
             ['Q', 'q', 'r'], []]
    for xs in cases:
        got = dedupe_keep(list(xs))
        if not isinstance(got, list):
            raise AssertionError(f"NORMALIZED_UNIQUE_AND_SUBSEQ: {xs!r} ⇒ 非 list")
        keys = [str(x).strip().lower() for x in got]
        if len(keys) != len(set(keys)):
            raise AssertionError(f"NORMALIZED_UNIQUE_AND_SUBSEQ: {xs!r} ⇒ {got!r} 归一化后仍有重复")
        it = iter(xs)
        for x in got:
            if not any(x == y for y in it):
                raise AssertionError(f"NORMALIZED_UNIQUE_AND_SUBSEQ: {xs!r} ⇒ {got!r} 不是子序列")
    return True
''',
    },
    # ─────────────────────── g7d3 fmt_amount ─────────────────────────────
    {
        "task": "g7d3_fmt_amount",
        "name": "DECIMAL_VALUE_PRESERVED",
        "why": "剥掉分隔符/括号后必须还原成同一个两位小数（数量级与精度守恒）",
        "src": '''
from decimal import Decimal, InvalidOperation

def _rel():
    for x in (1234.5, 0, 1234567.891, -0.5, 12, 999.999):
        got = fmt_amount(x)
        if not isinstance(got, str):
            raise AssertionError(f"DECIMAL_VALUE_PRESERVED: {x!r} ⇒ 非 str")
        digits = "".join(c for c in got if c.isdigit())
        if not digits:
            raise AssertionError(f"DECIMAL_VALUE_PRESERVED: {x!r} ⇒ {got!r} 里没有数字")
        try:
            val = Decimal(digits) / Decimal(100)
        except InvalidOperation:
            raise AssertionError(f"DECIMAL_VALUE_PRESERVED: {x!r} ⇒ {got!r} 解不出数")
        if abs(float(val) - abs(float(x))) > 0.005:
            raise AssertionError(f"DECIMAL_VALUE_PRESERVED: {x!r} ⇒ {got!r} 还原 {val}")
        neg = float(x) < 0
        if neg and not ("(" in got or "-" in got):
            raise AssertionError(f"DECIMAL_VALUE_PRESERVED: {x!r} ⇒ {got!r} 丢了负号")
        if (not neg) and ("(" in got or "-" in got):
            raise AssertionError(f"DECIMAL_VALUE_PRESERVED: {x!r} ⇒ {got!r} 多出负号")
    return True
''',
    },
    # ─────────────────────── g7e1 parse_flag ─────────────────────────────
    {
        "task": "g7e1_parse_flag",
        "name": "CASE_AND_SPACE_INSENSITIVE",
        "why": "题面明写「大小写不敏感、首尾空白忽略」⇒ 同一语义的三种写法必须同值",
        "src": '''
def _rel():
    for s in ('y', 'n', 'yes', 'off', 'maybe'):
        a, b, c = parse_flag(s), parse_flag(s.upper()), parse_flag('  ' + s + ' ')
        if not (a == b == c):
            raise AssertionError(f"CASE_AND_SPACE_INSENSITIVE: {s!r} ⇒ {a!r}/{b!r}/{c!r}")
        if a not in (True, False, None):
            raise AssertionError(f"CASE_AND_SPACE_INSENSITIVE: {s!r} ⇒ {a!r} 不是三值之一")
    return True
''',
    },
    {
        "task": "g7e2_truncate_tail",
        "name": "TAIL_PRESERVED",
        "rev": 2,
        "why": "超长时输出必须以**原文的尾部**结尾（只判尾，不写字面期望值）",
        "src": '''
def _rel():
    for s, n in (('abcdef', 3), ('0123456789', 4), ('日志第二段现场', 4), ('abcdefg', 2)):
        got = truncate_tail(s, n)
        if not got.endswith(s[-n:]):
            raise AssertionError(f"TAIL_PRESERVED: {(s, n)!r} ⇒ {got!r} 不以 {s[-n:]!r} 结尾")
    return True
''',
    },
    # ───────────────────── g7e2 truncate_tail ────────────────────────────
    {
        "task": "g7e2_truncate_tail",
        "name": "LEN_BOUND_AND_SHORT_IDENTITY",
        "why": "不超长原样返回；超长时长度不超过 n+1（标记位）—— 都是题面写明的结构约束",
        "src": '''
def _rel():
    for s, n in (('abc', 5), ('abcdef', 3), ('0123456789', 4), ('日志第二段现场', 4), ('abcdefg', 2)):
        got = truncate_tail(s, n)
        if not isinstance(got, str):
            raise AssertionError(f"LEN_BOUND_AND_SHORT_IDENTITY: {(s, n)!r} ⇒ 非 str")
        if len(s) <= n:
            if got != s:
                raise AssertionError(f"LEN_BOUND_AND_SHORT_IDENTITY: {(s, n)!r} ⇒ {got!r} 改了短串")
        elif len(got) > n + 1:
            raise AssertionError(f"LEN_BOUND_AND_SHORT_IDENTITY: {(s, n)!r} ⇒ {got!r} 长 {len(got)} > {n + 1}")
    return True
''',
    },
]
