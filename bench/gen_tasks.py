#!/usr/bin/env python3
"""任务集的**源** —— 产物是 bench/tasks/*.jsonl 与 bench/heldout/*.jsonl。

为什么源要留在仓库里：任务集是数据，但"partial 为什么恰好丢那几条 assert"是
**设计决定**。留下源，「这条任务为什么能分辨中段」就可审计、可重跑。
重跑：`python bench/gen_tasks.py`（幂等，覆盖同名文件）。


设计约束（全部来自 calibrate() 的判据，不是我的偏好）：
  · oracle  必须拿满分 1.0
  · null    必须拿 0.0
  · partial 必须**严格居中**（0 < score < 1）——否则"中段分辨力"是空判据
每道题我都得让 partial 恰好丢掉一部分 assert。
"""

import json
from pathlib import Path

TASKS = {}

# ===========================================================================
# 领域 A：文本模式处理（text_ops）
# ===========================================================================
TASKS["tasks/text_ops"] = [
    {
        "id": "x01_slugify",
        "prompt": ("写一个函数 slugify(s)，把标题转成 URL slug：转小写，"
                   "把所有非字母数字的连续片段替换成单个 '-'，去掉首尾 '-'。"
                   "只输出代码，函数名必须是 slugify。"),
        "tests": [
            "assert slugify('Hello World') == 'hello-world'",
            "assert slugify('  Foo   Bar  ') == 'foo-bar'",
            "assert slugify('A_B-C') == 'a-b-c'",
            "assert slugify('Foo--Bar') == 'foo-bar'",
            "assert slugify('') == ''",
            "assert slugify('!!!') == ''",
        ],
        "oracle": r'''import re
def slugify(s):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")''',
        "partial": r'''import re
def slugify(s):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]", "-", s)
    return s.strip("-")''',
        "null": "",
        "meta": {"tags": "text,regex", "level": "easy"},
    },
    {
        "id": "x02_mask_email",
        "prompt": ("写一个函数 mask_email(s)，掩码邮箱地址：保留 '@' 前的第一个字符，"
                   "其余局部部分每个字符替换成 '*'，域名原样保留。"
                   "若字符串里没有 '@'，原样返回。只输出代码，函数名必须是 mask_email。"),
        "tests": [
            "assert mask_email('alice@example.com') == 'a****@example.com'",
            "assert mask_email('bob@x.com') == 'b**@x.com'",
            "assert mask_email('a@b.com') == 'a@b.com'",
            "assert mask_email('no-at-sign') == 'no-at-sign'",
            "assert mask_email('') == ''",
        ],
        "oracle": r'''def mask_email(s):
    if not s or "@" not in s:
        return s
    local, domain = s.split("@", 1)
    if len(local) <= 1:
        return local + "@" + domain
    return local[0] + "*" * (len(local) - 1) + "@" + domain''',
        "partial": r'''def mask_email(s):
    local, domain = s.split("@", 1)
    return local[0] + "*" * (len(local) - 1) + "@" + domain''',
        "null": "",
        "meta": {"tags": "text,string", "level": "easy"},
    },
    {
        "id": "x03_word_freq",
        "prompt": ("写一个函数 word_freq(text, k)，统计文本里英文单词的出现次数："
                   "只取 [A-Za-z]+ 的片段、统一转小写。返回出现次数最高的 k 个 "
                   "(单词, 次数) 元组组成的列表，按次数降序；次数相同时按单词字典序升序。"
                   "只输出代码，函数名必须是 word_freq。"),
        "tests": [
            "assert word_freq('a b a c a', 2) == [('a', 3), ('b', 1)]",
            "assert word_freq('B b a', 2) == [('b', 2), ('a', 1)]",
            "assert word_freq('', 3) == []",
            "assert word_freq('x', 5) == [('x', 1)]",
            "assert word_freq('c a b', 3) == [('a', 1), ('b', 1), ('c', 1)]",
        ],
        "oracle": r'''import re
from collections import Counter
def word_freq(text, k):
    words = re.findall(r"[A-Za-z]+", (text or "").lower())
    counts = Counter(words)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:k]''',
        "partial": r'''import re
from collections import Counter
def word_freq(text, k):
    words = re.findall(r"[A-Za-z]+", (text or "").lower())
    return Counter(words).most_common(k)''',
        "null": "",
        "meta": {"tags": "text,counting", "level": "medium"},
    },
    {
        "id": "x04_truncate",
        "prompt": ("写一个函数 truncate(text, n)：若文本长度不超过 n，原样返回；"
                   "否则截取前 n 个字符并在末尾追加 '...'（三个点**不计入** n）。"
                   "只输出代码，函数名必须是 truncate。"),
        "tests": [
            "assert truncate('hello', 5) == 'hello'",
            "assert truncate('hello world', 5) == 'hello...'",
            "assert truncate('hi', 10) == 'hi'",
            "assert truncate('', 3) == ''",
            "assert truncate('abcd', 0) == '...'",
        ],
        "oracle": r'''def truncate(text, n):
    text = text or ""
    if len(text) <= n:
        return text
    return text[:n] + "..."''',
        "partial": r'''def truncate(text, n):
    text = text or ""
    if len(text) <= n:
        return text
    return text[:n - 3] + "..."''',
        "null": "",
        "meta": {"tags": "text,string", "level": "easy"},
    },
    {
        "id": "x05_normalize_ws",
        "prompt": ("写一个函数 normalize_ws(s)：把任意连续空白（含换行、制表符，"
                   "以及全角空格 U+3000）折叠成一个半角空格，并去掉首尾空白。"
                   "只输出代码，函数名必须是 normalize_ws。"),
        "tests": [
            "assert normalize_ws('  a  b  ') == 'a b'",
            "assert normalize_ws('a\u3000b') == 'a b'",
            "assert normalize_ws('') == ''",
            "assert normalize_ws('\\n\\t a \\r\\n') == 'a'",
            "assert normalize_ws('a\u3000\u3000b\u3000') == 'a b'",
        ],
        "oracle": r'''import re
def normalize_ws(s):
    return re.sub(r"\s+", " ", (s or "").replace("\u3000", " ")).strip()''',
        # ⚠️ 这个 partial 试过两版才对：第一版写 "不处理全角空格"（只 re.sub \s），
        # 结果校准闸判它**满分** —— 因为 Python str 模式下 `\s` 本身就匹配 U+3000
        # （Unicode 感知）。我的事实假设错了，是尺子抓到的，不是我自己想到的。
        "partial": r'''def normalize_ws(s):
    return (s or "").replace("\u3000", " ").strip()''',
        "null": "",
        "meta": {"tags": "text,whitespace", "level": "medium"},
    },
]

# ===========================================================================
# 领域 B：数值与逻辑（numeric_logic）
# ===========================================================================
TASKS["tasks/numeric_logic"] = [
    {
        "id": "y01_roman_to_int",
        "prompt": ("写一个函数 roman_to_int(s)，把罗马数字字符串转成整数"
                   "（支持 I V X L C D M，遵循减法规则如 IV=4、IX=9、MCMXCIV=1994）。"
                   "只输出代码，函数名必须是 roman_to_int。"),
        "tests": [
            "assert roman_to_int('III') == 3",
            "assert roman_to_int('IV') == 4",
            "assert roman_to_int('IX') == 9",
            "assert roman_to_int('LVIII') == 58",
            "assert roman_to_int('MCMXCIV') == 1994",
        ],
        "oracle": r'''def roman_to_int(s):
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for ch in reversed((s or "").upper()):
        v = vals[ch]
        if v < prev:
            total -= v
        else:
            total += v
            prev = v
    return total''',
        "partial": r'''def roman_to_int(s):
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    return sum(vals[ch] for ch in (s or "").upper())''',
        "null": "",
        "meta": {"tags": "numeric,parsing", "level": "medium"},
    },
    {
        "id": "y02_is_valid_brackets",
        "prompt": ("写一个函数 is_valid_brackets(s)，判断字符串中的三种括号 "
                   "() [] {} 是否**类型正确且嵌套合法**（其它字符忽略）。"
                   "返回 True/False。只输出代码，函数名必须是 is_valid_brackets。"),
        "tests": [
            "assert is_valid_brackets('()[]{}') is True",
            "assert is_valid_brackets('(]') is False",
            "assert is_valid_brackets('([)]') is False",
            "assert is_valid_brackets('') is True",
            "assert is_valid_brackets('(((') is False",
            "assert is_valid_brackets('a(b)c') is True",
        ],
        "oracle": r'''def is_valid_brackets(s):
    pairs = {")": "(", "]": "[", "}": "{"}
    stack = []
    for ch in s or "":
        if ch in "([{":
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack.pop() != pairs[ch]:
                return False
    return not stack''',
        "partial": r'''def is_valid_brackets(s):
    depth = 0
    for ch in s or "":
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if depth < 0:
            return False
    return depth == 0''',
        "null": "",
        "meta": {"tags": "logic,stack", "level": "medium"},
    },
    {
        "id": "y03_base_convert",
        "prompt": ("写一个函数 base_convert(s, from_base, to_base)，把字符串 s（按 "
                   "from_base 进制解释，可能带前导 '-'）转成 to_base 进制的字符串，"
                   "用大写字母表示 10~35。空串返回空串。只输出代码，函数名必须是 base_convert。"),
        "tests": [
            "assert base_convert('1010', 2, 10) == '10'",
            "assert base_convert('FF', 16, 2) == '11111111'",
            "assert base_convert('0', 10, 16) == '0'",
            "assert base_convert('-FF', 16, 10) == '-255'",
            "assert base_convert('777', 8, 10) == '511'",
            "assert base_convert('', 10, 2) == ''",
        ],
        "oracle": r'''def base_convert(s, from_base, to_base):
    s = (s or "").strip()
    if not s:
        return ""
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    sign = ""
    if s[0] == "-":
        sign, s = "-", s[1:]
    n = int(s, from_base)
    if n == 0:
        return "0"
    out = ""
    while n > 0:
        out = digits[n % to_base] + out
        n //= to_base
    return sign + out''',
        "partial": r'''def base_convert(s, from_base, to_base):
    s = (s or "").strip()
    if not s:
        return ""
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    n = int(s, from_base)
    if n == 0:
        return "0"
    out = ""
    while n > 0:
        out = digits[n % to_base] + out
        n //= to_base
    return out''',
        "null": "",
        "meta": {"tags": "numeric,radix", "level": "medium"},
    },
    {
        "id": "y04_gcd_lcm",
        "prompt": ("写一个函数 gcd_lcm(a, b)，返回二元组 (gcd, lcm)。"
                   "先对参数取绝对值；gcd(0, x) 规定为 x；gcd(0, 0) 规定为 (0, 0)。"
                   "只输出代码，函数名必须是 gcd_lcm。"),
        "tests": [
            "assert gcd_lcm(12, 18) == (6, 36)",
            "assert gcd_lcm(0, 5) == (5, 0)",
            "assert gcd_lcm(0, 0) == (0, 0)",
            "assert gcd_lcm(-4, 6) == (2, 12)",
            "assert gcd_lcm(7, 13) == (1, 91)",
        ],
        "oracle": r'''import math
def gcd_lcm(a, b):
    a, b = abs(a), abs(b)
    g = math.gcd(a, b)
    if g == 0:
        return (0, 0)
    return (g, a // g * b)''',
        "partial": r'''import math
def gcd_lcm(a, b):
    a, b = abs(a), abs(b)
    g = math.gcd(a, b)
    return (g, a // g * b)''',
        "null": "",
        "meta": {"tags": "numeric,math", "level": "easy"},
    },
    {
        "id": "y05_median",
        "prompt": ("写一个函数 median(seq)，返回序列的中位数：奇数个元素取中间那个，"
                   "偶数个元素取中间两个的平均值；空序列返回 None。只输出代码，"
                   "函数名必须是 median。"),
        "tests": [
            "assert median([3, 1, 2]) == 2",
            "assert median([1, 2, 3, 4]) == 2.5",
            "assert median([]) is None",
            "assert median([5]) == 5",
            "assert median([4, 4]) == 4",
        ],
        "oracle": r'''def median(seq):
    xs = sorted(seq)
    n = len(xs)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 == 1:
        return xs[mid]
    return (xs[mid - 1] + xs[mid]) / 2''',
        "partial": r'''def median(seq):
    xs = sorted(seq)
    n = len(xs)
    if n == 0:
        return None
    return xs[(n - 1) // 2]''',
        "null": "",
        "meta": {"tags": "numeric,statistics", "level": "easy"},
    },
]

# ===========================================================================
# 领域 C：结构化数据变换（struct_convert）
# ===========================================================================
TASKS["tasks/struct_convert"] = [
    {
        "id": "z01_flatten",
        "prompt": ("写一个函数 flatten(d)，把嵌套 dict 展平成单层：嵌套键用 '.' 连接，"
                   "列表元素用 'key[i]' 表示（列表里的 dict 继续向下展平）。"
                   "只输出代码，函数名必须是 flatten。"),
        "tests": [
            "assert flatten({'a': 1}) == {'a': 1}",
            "assert flatten({'a': {'b': 2}}) == {'a.b': 2}",
            "assert flatten({'a': [1, 2]}) == {'a[0]': 1, 'a[1]': 2}",
            "assert flatten({}) == {}",
            "assert flatten({'x': {'y': {'z': 3}}}) == {'x.y.z': 3}",
        ],
        "oracle": r'''def flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        key = prefix + "." + str(k) if prefix else str(k)
        if isinstance(v, dict):
            out.update(flatten(v, key))
        elif isinstance(v, list):
            for i, item in enumerate(v):
                ik = key + "[" + str(i) + "]"
                if isinstance(item, dict):
                    out.update(flatten(item, ik))
                else:
                    out[ik] = item
        else:
            out[key] = v
    return out''',
        "partial": r'''def flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        key = prefix + "." + str(k) if prefix else str(k)
        if isinstance(v, dict):
            out.update(flatten(v, key))
        else:
            out[key] = v
    return out''',
        "null": "",
        "meta": {"tags": "struct,recursion", "level": "medium"},
    },
    {
        "id": "z02_to_csv",
        "prompt": ("写一个函数 to_csv(rows, cols)：rows 是 dict 列表，cols 是列名列表。"
                   "输出 CSV 文本：第一行是表头，按 cols 顺序取值；缺失的键输出空串；"
                   "None 输出空串；单元格含逗号/双引号/换行时用双引号包裹，"
                   "内部的 '\"' 双写。行之间用 '\\n' 连接。只输出代码，函数名必须是 to_csv。"),
        "tests": [
            "assert to_csv([{'a': 1, 'b': 2}], ['a', 'b']) == 'a,b\\n1,2'",
            "assert to_csv([], ['a']) == 'a'",
            "assert to_csv([{'a': 'x,y', 'b': None}], ['a', 'b']) == 'a,b\\n\"x,y\",'",
            "assert to_csv([{'a': 'q\"z'}], ['a']) == 'a\\n\"q\"\"z\"'",
            "assert to_csv([{'a': 1}], ['a', 'b']) == 'a,b\\n1,'",
        ],
        "oracle": r'''def to_csv(rows, cols):
    def cell(v):
        s = "" if v is None else str(v)
        if any(c in s for c in ',"\n'):
            return '"' + s.replace('"', '""') + '"'
        return s
    lines = [",".join(cell(c) for c in cols)]
    for r in rows:
        lines.append(",".join(cell(r.get(c)) for c in cols))
    return "\n".join(lines)''',
        "partial": r'''def to_csv(rows, cols):
    def cell(v):
        s = "" if v is None else str(v)
        if any(c in s for c in ',"\n'):
            return '"' + s + '"'
        return s
    lines = [",".join(cell(c) for c in cols)]
    for r in rows:
        lines.append(",".join(cell(r.get(c)) for c in cols))
    return "\n".join(lines)''',
        "null": "",
        "meta": {"tags": "struct,csv", "level": "hard"},
    },
    {
        "id": "z03_parse_query",
        "prompt": ("写一个函数 parse_query(qs)，解析 URL 查询串（形如 'a=1&b=2'）"
                   "成 dict：按 '&' 切分，按第一个 '=' 切成键值；键和值都要做 "
                   "percent-decode，并且 '+' 解码为空格；同一个键出现多次时取**最后**一个；"
                   "忽略空片段。只输出代码，函数名必须是 parse_query。"),
        "tests": [
            "assert parse_query('a=1&b=2') == {'a': '1', 'b': '2'}",
            "assert parse_query('') == {}",
            "assert parse_query('a=hello+world') == {'a': 'hello world'}",
            "assert parse_query('a=%E4%B8%AD') == {'a': '\u4e2d'}",
            "assert parse_query('a=1&a=2') == {'a': '2'}",
        ],
        "oracle": r'''from urllib.parse import unquote_plus
def parse_query(qs):
    out = {}
    for part in (qs or "").split("&"):
        if not part:
            continue
        k, _, v = part.partition("=")
        out[unquote_plus(k)] = unquote_plus(v)
    return out''',
        "partial": r'''def parse_query(qs):
    out = {}
    for part in (qs or "").split("&"):
        if not part:
            continue
        k, _, v = part.partition("=")
        out[k] = v
    return out''',
        "null": "",
        "meta": {"tags": "struct,url", "level": "medium"},
    },
    {
        "id": "z04_deep_get",
        "prompt": ("写一个函数 deep_get(d, path, default=None)：path 形如 'a.b.0.c'，"
                   "按 '.' 逐段向下取；段落是数字时索引进 list；任何一段不存在、"
                   "或类型不匹配（例如往 int 里取键）时返回 default。"
                   "只输出代码，函数名必须是 deep_get。"),
        "tests": [
            "assert deep_get({'a': {'b': 1}}, 'a.b') == 1",
            "assert deep_get({'a': {'b': 1}}, 'a.c', 9) == 9",
            "assert deep_get({'a': [10, 20]}, 'a.1') == 20",
            "assert deep_get({'a': 1}, 'a.b', 'x') == 'x'",
            "assert deep_get({}, 'a') is None",
        ],
        "oracle": r'''def deep_get(d, path, default=None):
    cur = d
    for part in str(path).split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return default
            cur = cur[part]
        elif isinstance(cur, list):
            if not part.isdigit() or int(part) >= len(cur):
                return default
            cur = cur[int(part)]
        else:
            return default
    return cur''',
        "partial": r'''def deep_get(d, path, default=None):
    cur = d
    for part in str(path).split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur''',
        "null": "",
        "meta": {"tags": "struct,traversal", "level": "medium"},
    },
    {
        "id": "z05_group_by",
        "prompt": ("写一个函数 group_by(seq, key)，按 key(x) 的**原值**（不转字符串）"
                   "把元素分组，返回 dict：键是分组值，值是按原顺序排列的元素列表。"
                   "空序列返回 {}。只输出代码，函数名必须是 group_by。"),
        "tests": [
            "assert group_by([1, 2, 3, 4], lambda x: x % 2) == {1: [1, 3], 0: [2, 4]}",
            "assert group_by([], lambda x: x) == {}",
            "assert group_by(['a', 'bb', 'c'], len) == {1: ['a', 'c'], 2: ['bb']}",
            "assert group_by([1, 2, 3], lambda x: 'odd' if x % 2 else 'even') == "
            "{'odd': [1, 3], 'even': [2]}",
            "assert group_by(['x'], lambda s: s.upper()) == {'X': ['x']}",
        ],
        "oracle": r'''def group_by(seq, key):
    out = {}
    for x in seq:
        out.setdefault(key(x), []).append(x)
    return out''',
        "partial": r'''def group_by(seq, key):
    out = {}
    for x in seq:
        out.setdefault(str(key(x)), []).append(x)
    return out''',
        "null": "",
        "meta": {"tags": "struct,grouping", "level": "easy"},
    },
]


# ===========================================================================
# Held-out（泛化实验的测试集）：与 numeric_logic **同族**但从没见过的新题
#
# 为什么必须"同族"：泛化的判据是「在一个套件上学到的经验，对**同族新题**
# 有没有可测的增益」。跨领域的迁移是另一个（更难）的问题 —— 若一上来就跨领域，
# 效果为 0 时你分不清是"记忆没用"还是"任务不相干"。
# ===========================================================================
TASKS["heldout/numeric_logic_heldout"] = [
    {
        "id": "h01_int_to_roman",
        "prompt": ("写一个函数 int_to_roman(n)，把整数转成罗马数字字符串"
                   "（遵循减法规则，如 4=IV、9=IX、1994=MCMXCIV）。"
                   "只输出代码，函数名必须是 int_to_roman。"),
        "tests": [
            "assert int_to_roman(3) == 'III'",
            "assert int_to_roman(4) == 'IV'",
            "assert int_to_roman(9) == 'IX'",
            "assert int_to_roman(58) == 'LVIII'",
            "assert int_to_roman(1994) == 'MCMXCIV'",
        ],
        "oracle": r'''def int_to_roman(n):
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
            (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),
            (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for v, sym in vals:
        while n >= v:
            out += sym
            n -= v
    return out''',
        "partial": r'''def int_to_roman(n):
    vals = [(1000, "M"), (500, "D"), (100, "C"), (50, "L"),
            (10, "X"), (5, "V"), (1, "I")]
    out = ""
    for v, sym in vals:
        while n >= v:
            out += sym
            n -= v
    return out''',
        "null": "",
        "meta": {"tags": "numeric,roman", "level": "medium"},
    },
    {
        "id": "h02_merge_ranges",
        "prompt": ("写一个函数 merge_ranges(intervals)：输入是若干个闭区间 "
                   "[start, end] 的列表（可能无序），合并所有重叠或相邻接的区间，"
                   "返回按 start 升序排列的区间列表（每个区间用长度为 2 的 list 表示）。"
                   "只输出代码，函数名必须是 merge_ranges。"),
        "tests": [
            "assert merge_ranges([[1,3],[2,6],[8,10],[15,18]]) == [[1,6],[8,10],[15,18]]",
            "assert merge_ranges([]) == []",
            "assert merge_ranges([[1,4],[4,5]]) == [[1,5]]",
            "assert merge_ranges([[5,6],[1,2]]) == [[1,2],[5,6]]",
            "assert merge_ranges([[1,10],[2,3]]) == [[1,10]]",
        ],
        "oracle": r'''def merge_ranges(intervals):
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1] = [out[-1][0], max(out[-1][1], b)]
        else:
            out.append([a, b])
    return out''',
        "partial": r'''def merge_ranges(intervals):
    out = []
    for a, b in intervals:
        if out and a <= out[-1][1]:
            out[-1] = [out[-1][0], max(out[-1][1], b)]
        else:
            out.append([a, b])
    return out''',
        "null": "",
        "meta": {"tags": "logic,intervals", "level": "medium"},
    },
    {
        "id": "h03_eval_rpn",
        "prompt": ("写一个函数 eval_rpn(tokens)，对逆波兰表达式求值。tokens 是"
                   "字符串列表，可能是多位数整数或 '+ - * /' 四个运算符；"
                   "除法按**向零取整**（如 13/5 == 2，-7/2 == -3）。"
                   "只输出代码，函数名必须是 eval_rpn。"),
        "tests": [
            "assert eval_rpn(['2','1','+','3','*']) == 9",
            "assert eval_rpn(['4','13','5','/','+']) == 6",
            "assert eval_rpn(['3']) == 3",
            "assert eval_rpn(['10','3','-']) == 7",
            "assert eval_rpn(['5','1','2','+','4','*','+','3','-']) == 14",
        ],
        "oracle": r'''def eval_rpn(tokens):
    st = []
    for t in tokens:
        if t in "+-*/":
            b = st.pop()
            a = st.pop()
            if t == "+":
                st.append(a + b)
            elif t == "-":
                st.append(a - b)
            elif t == "*":
                st.append(a * b)
            else:
                st.append(int(a / b))
        else:
            st.append(int(t))
    return st[-1]''',
        "partial": r'''def eval_rpn(tokens):
    st = []
    for t in tokens:
        if t in "+-*/":
            b = st.pop()
            a = st.pop()
            if t == "+":
                st.append(a + b)
            elif t == "-":
                st.append(a - b)
            elif t == "*":
                st.append(a * b)
            else:
                st.append(a / b)
        else:
            st.append(int(t))
    return st[-1]''',
        "null": "",
        "meta": {"tags": "logic,stack", "level": "medium"},
    },
]


# ===========================================================================
# held-out（跨期考试集）：结构化数据变换 —— 与 tasks/struct_convert **同结构、
# 不同表面**
# ===========================================================================
#
# ⛔ **事前冻结**（2026-10-17 第十六轮，写在**跑实验之前**）：
#
#   上一轮实测出第一个正的跨任务增益 Δ=+0.2000，但排序对照证明它属于
#   「位置」（同套件内先跑完几题，后面那题检索到前面的**完整答案**）。
#   ⇒ 那是答案复用，不是泛化。要分辨二者，必须换一个**跨期**的考试集：
#
#   P1 同结构：每题递归遍历/序列化/反解析，与训练题同属「结构化数据变换」，
#              以便题面特征有重合（否则连"检索到什么"都无从谈起）。
#   P2 异表面：**函数名、数据格式、领域词汇都不与 struct_convert 重合** ——
#              否则 few-shot 可以照抄，实验答的就是"能不能抄"而不是"会不会迁移"。
#   P3 同源同粒度：每道题同样 5 条 assert、同样有 oracle/partial/null 三档，
#              与训练题完全同一把尺子（不引入新的评分口径）。
#   P4 定稿即冻结：题目一旦跑过一次，之后**只许**为修 bug 而改 oracle/tests，
#              不许为了让某一臂变好看而改题、改 assert、改顺序。
TASKS["heldout/struct_convert_heldout"] = [
    {
        "id": "k01_diff_tree",
        "prompt": ("写一个函数 diff_tree(a, b)：递归比较两个 dict 的**叶子**（标量）值，"
                   "差异路径用 '.' 连接嵌套键，返回 {路径: [a 里的值, b 里的值]}；"
                   "某一边缺这个键时那一边写 None；两边完全相同时返回空 dict。"
                   "只输出代码，函数名必须是 diff_tree。"),
        "tests": [
            "assert diff_tree({'a': 1}, {'a': 1}) == {}",
            "assert diff_tree({'a': 1}, {'a': 2}) == {'a': [1, 2]}",
            "assert diff_tree({'a': {'b': 1}}, {'a': {'b': 2}}) == {'a.b': [1, 2]}",
            "assert diff_tree({'a': 1}, {}) == {'a': [1, None]}",
            "assert diff_tree({}, {'a': 1}) == {'a': [None, 1]}",
        ],
        "oracle": r'''_MISS = object()


def diff_tree(a, b, prefix=""):
    out = {}
    for k in set(a) | set(b):
        key = prefix + "." + str(k) if prefix else str(k)
        va = a.get(k, _MISS)
        vb = b.get(k, _MISS)
        if isinstance(va, dict) and isinstance(vb, dict):
            out.update(diff_tree(va, vb, key))
        else:
            va = None if va is _MISS else va
            vb = None if vb is _MISS else vb
            if va != vb:
                out[key] = [va, vb]
    return out''',
        "partial": r'''def diff_tree(a, b, prefix=""):
    out = {}
    for k in set(a) | set(b):
        key = prefix + "." + str(k) if prefix else str(k)
        va = a.get(k)
        vb = b.get(k)
        if va != vb:
            out[key] = [va, vb]
    return out''',
        "null": "",
        "meta": {"tags": "struct,diff,recursion", "level": "hard"},
    },
    {
        "id": "k02_encode_cookie",
        "prompt": ("写一个函数 encode_cookie(d)：把 dict 编码成 Cookie 请求头字符串。"
                   "规则：键按字典序排序；值为 None 的键跳过；其余写成 'k=v'，"
                   "值必须 percent-encode —— 空格→%20，分号→%3B，逗号→%2C，"
                   "等号→%3D，百分号→%25；多个项用 '; ' 连接。只输出代码，"
                   "函数名必须是 encode_cookie。"),
        "tests": [
            "assert encode_cookie({'a': 1}) == 'a=1'",
            "assert encode_cookie({'a': None}) == ''",
            "assert encode_cookie({'b': 2, 'a': 1}) == 'a=1; b=2'",
            "assert encode_cookie({'a': 'x y'}) == 'a=x%20y'",
            "assert encode_cookie({'a': 'p%q'}) == 'a=p%25q'",
        ],
        "oracle": r'''def encode_cookie(d):
    # ⚠️ 必须是**单次**查表，不能链式 replace：
    #    chaining 会把第一步生成的 '%20' 再当成待转义的 '%' ⇒ 二次编码。
    #    （本轮被 gen_tasks 的自检当场捉住：oracle 只过 4/5）
    esc = {" ": "%20", ";": "%3B", ",": "%2C", "=": "%3D", "%": "%25"}
    parts = []
    for k, v in sorted(d.items()):
        if v is None:
            continue
        parts.append(str(k) + "=" + "".join(esc.get(c, c) for c in str(v)))
    return "; ".join(parts)''',
        "partial": r'''def encode_cookie(d):
    return "; ".join(f"{k}={v}" for k, v in sorted(d.items()) if v is not None)''',
        "null": "",
        "meta": {"tags": "struct,serialize,escape", "level": "medium"},
    },
    {
        "id": "k03_unflatten",
        "prompt": ("写一个函数 unflatten(flat)：把展平 dict 还原成嵌套结构。"
                   "键里的 '.' 表示嵌套 dict，'k[i]' 表示 k 是一个 list、第 i 个元素；"
                   "列表长度取该层出现过的最大下标 +1，未填的位置写 None。"
                   "返回还原后的顶层 dict。只输出代码，函数名必须是 unflatten。"),
        "tests": [
            "assert unflatten({'a': 1}) == {'a': 1}",
            "assert unflatten({'a.b': 1}) == {'a': {'b': 1}}",
            "assert unflatten({'a[0]': 1, 'a[1]': 2}) == {'a': [1, 2]}",
            "assert unflatten({'a.b[0]': 1}) == {'a': {'b': [1]}}",
            "assert unflatten({'a[2]': 3}) == {'a': [None, None, 3]}",
        ],
        "oracle": r'''import re


def unflatten(flat):
    def parts_of(key):
        out = []
        for m in re.finditer(r"\[(\d+)\]|([^.\[\]]+)", key):
            out.append(("i", int(m.group(1))) if m.group(1) is not None
                       else ("k", m.group(2)))
        return out

    def put(node, parts, value):
        kind, tok = parts[0]
        rest = parts[1:]
        if kind == "i":
            while len(node) <= tok:
                node.append(None)
            if rest:
                if not isinstance(node[tok], (dict, list)):
                    node[tok] = [] if rest[0][0] == "i" else {}
                put(node[tok], rest, value)
            else:
                node[tok] = value
        elif rest:
            if not isinstance(node.get(tok), (dict, list)):
                node[tok] = [] if rest[0][0] == "i" else {}
            put(node[tok], rest, value)
        else:
            node[tok] = value

    root = {}
    for flat_key, value in flat.items():
        put(root, parts_of(flat_key), value)
    return root''',
        "partial": r'''def unflatten(flat):
    root = {}
    for key, value in flat.items():
        parts = key.split(".")
        node = root
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value
    return root''',
        "null": "",
        "meta": {"tags": "struct,parse,index", "level": "hard"},
    },
    {
        "id": "k04_to_ini",
        "prompt": ("写一个函数 to_ini(d)：把嵌套 dict 转成 INI 文本。顶层键是 section，"
                   "值是一个 dict；每个 section 输出 '[section]' 一行，"
                   "接着每行 'key=value'（按 dict 的**出现顺序**）；"
                   "值为 None 输出空串，bool 输出小写的 'true'/'false'，其它用 str()；"
                   "最后一行的末尾也要有换行符；空 dict 返回空串。"
                   "只输出代码，函数名必须是 to_ini。"),
        "tests": [
            "assert to_ini({}) == ''",
            "assert to_ini({'s': {'a': 1}}) == '[s]\\na=1\\n'",
            "assert to_ini({'s': {'a': None}}) == '[s]\\na=\\n'",
            "assert to_ini({'s': {'a': True}}) == '[s]\\na=true\\n'",
            "assert to_ini({'s': {}, 't': {'b': 2}}) == '[s]\\n[t]\\nb=2\\n'",
        ],
        "oracle": r'''def to_ini(d):
    out = []
    for sec, body in d.items():
        out.append("[" + str(sec) + "]")
        for k, v in body.items():
            if v is None:
                s = ""
            elif isinstance(v, bool):
                s = "true" if v else "false"
            else:
                s = str(v)
            out.append(str(k) + "=" + s)
    return ("\n".join(out) + "\n") if out else ""''',
        "partial": r'''def to_ini(d):
    out = []
    for sec, body in d.items():
        out.append("[" + str(sec) + "]")
        for k, v in body.items():
            s = "" if v is None else str(v)
            out.append(str(k) + "=" + s)
    return ("\n".join(out) + "\n") if out else ""''',
        "null": "",
        "meta": {"tags": "struct,serialize,text", "level": "medium"},
    },
]


# ===========================================================================
# 领域 G7：§17-7 新题池（两侧都有提升空间 —— 登记见 DESIGN §17-7 ★7a）
# ===========================================================================
TASKS["tasks/g7_struct"] = [
    {
        "id": "g7s1_flatten_nested",
        "prompt": ("写一个函数 flatten_nested(d)：把嵌套结构展平成单层 dict。"
                   "嵌套 dict 用 '.' 连接键（如 {'a':{'b':1}} → {'a.b':1}）；"
                   "嵌套 list 用 '[i]' 下标（如 {'a':[10,20]} → {'a[0]':10,'a[1]':20}）；"
                   "值为 None 的条目整个跳过（不出现任何键）；"
                   "可以混合嵌套（{'a':{'b':[1]}} → {'a.b[0]':1}）。"
                   "只输出代码，函数名必须是 flatten_nested。"),
        "tests": [
            "assert flatten_nested({'a': 1}) == {'a': 1}",
            "assert flatten_nested({'a': {'b': 2}}) == {'a.b': 2}",
            "assert flatten_nested({'a': [10, 20]}) == {'a[0]': 10, 'a[1]': 20}",
            "assert flatten_nested({'a': {'b': None}}) == {}",
            "assert flatten_nested({'a': [None, 5]}) == {'a[1]': 5}",
        ],
        "oracle": r'''def flatten_nested(d):
    out = {}
    def walk(node, prefix):
        if isinstance(node, dict):
            children = [(v, prefix + "." + str(k) if prefix else str(k))
                        for k, v in node.items()]
        elif isinstance(node, list):
            children = [(v, f"{prefix}[{i}]") for i, v in enumerate(node)]
        else:
            out[prefix] = node
            return
        for v, key in children:
            if isinstance(v, (dict, list)):
                walk(v, key)
            elif v is not None:
                out[key] = v
    walk(d, "")
    return out''',
        "partial": r'''def flatten_nested(d):
    out = {}
    def walk(node, prefix):
        for k, v in node.items():
            key = prefix + "." + str(k) if prefix else str(k)
            if isinstance(v, dict):
                walk(v, key)
            elif v is not None:
                out[key] = v
    walk(d, "")
    return out''',
        "null": "",
        "meta": {"tags": "struct,flatten,recursion", "level": "hard"},
    },
    {
        "id": "g7s2_parse_headers",
        "prompt": ("写一个函数 parse_headers(s)，解析 'k1: v1; k2: v2' 形式的头字符串。"
                   "规则：条目用 ';' 分隔；键值用第一个 ':' 分隔；键和未加引号的值"
                   "都要去掉首尾空白；值若用**双引号包住**，则引号内的 ';' 与 ':' "
                   "不算分隔符、值取引号内原文（不去空白、不再去引号）；"
                   "空键跳过。只输出代码，函数名必须是 parse_headers。"),
        "tests": [
            "assert parse_headers('a: 1; b: 2') == {'a': '1', 'b': '2'}",
            "assert parse_headers('a: \"x;y\"') == {'a': 'x;y'}",
            "assert parse_headers('a: \"q:z\"; b: 3') == {'a': 'q:z', 'b': '3'}",
            "assert parse_headers('  k :  v  ') == {'k': 'v'}",
            "assert parse_headers('a: \"\"') == {'a': ''}",
        ],
        "oracle": r'''def parse_headers(s):
    out = {}
    i, n = 0, len(s)
    while i < n:
        j = s.find(':', i)
        if j < 0:
            break
        key = s[i:j].strip()
        k = j + 1
        while k < n and s[k] == ' ':
            k += 1
        quoted = k < n and s[k] == '"'
        if quoted:
            e = s.find('"', k + 1)
            if e < 0:
                val, i = s[k + 1:], n
            else:
                val = s[k + 1:e]
                i = e + 1
                while i < n and s[i] != ';':
                    i += 1
                i += 1
        else:
            e = s.find(';', k)
            if e < 0:
                val, i = s[k:], n
            else:
                val, i = s[k:e], e + 1
        if key:
            out[key] = val if quoted else val.strip()
    return out''',
        "partial": r'''def parse_headers(s):
    out = {}
    for part in s.split(';'):
        if ':' not in part:
            continue
        key, val = part.split(':', 1)
        key, val = key.strip(), val.strip()
        if key:
            out[key] = val.strip('"')
    return out''',
        "null": "",
        "meta": {"tags": "struct,parse,quoting", "level": "hard"},
    },
    {
        "id": "g7s3_csv_row",
        "prompt": ("写一个函数 csv_row(fields)，把字段列表编码成一行 CSV 文本。"
                   "规则：字段用 ',' 连接；字段若含 ','、'\"'、'\\n' 或 '\\r' 之一，"
                   "整个字段用双引号包住，且字段内的 '\"' 写成两个 '\"'；"
                   "其余字段原样。只输出代码，函数名必须是 csv_row。"),
        "tests": [
            "assert csv_row(['a', 'b']) == 'a,b'",
            "assert csv_row(['a,b', 'c']) == '\"a,b\",c'",
            "assert csv_row(['say \"hi\"', 'x']) == '\"say \"\"hi\"\"\",x'",
            "assert csv_row(['plain']) == 'plain'",
            "assert csv_row(['l1\\nl2', '']) == '\"l1\\nl2\",'",
        ],
        "oracle": r'''def csv_row(fields):
    out = []
    for f in fields:
        f = str(f)
        if any(c in f for c in ',"\n\r'):
            f = '"' + f.replace('"', '""') + '"'
        out.append(f)
    return ','.join(out)''',
        "partial": r'''def csv_row(fields):
    out = []
    for f in fields:
        f = str(f)
        if ',' in f:
            f = '"' + f + '"'
        out.append(f)
    return ','.join(out)''',
        "null": "",
        "meta": {"tags": "struct,serialize,csv", "level": "medium"},
    },
    {
        "id": "g7s4_xml_escape_attrs",
        "prompt": ("写一个函数 xml_escape_attrs(d)，把 dict 编码成 XML 属性串。"
                   "规则：按 dict 出现顺序输出 k=\"v\"，用单个空格连接；"
                   "值里的 '&' '<' '>' '\"' 分别转成 '&amp;' '&lt;' '&gt;' '&quot;'"
                   "（必须是**逐字符单次**替换 —— 不能链式 replace，否则会把前一步"
                   "生成的 '&amp;' 再改坏）；值为 None 的键跳过。"
                   "只输出代码，函数名必须是 xml_escape_attrs。"),
        "tests": [
            "assert xml_escape_attrs({'a': '1'}) == 'a=\"1\"'",
            "assert xml_escape_attrs({'a': 'x<y', 'b': 'z'}) == 'a=\"x&lt;y\" b=\"z\"'",
            "assert xml_escape_attrs({'a': 'x\"y&z'}) == 'a=\"x&quot;y&amp;z\"'",
            "assert xml_escape_attrs({'a': None, 'b': '2'}) == 'b=\"2\"'",
            "assert xml_escape_attrs({'a': 'q\"q'}) == 'a=\"q&quot;q\"'",
        ],
        "oracle": r'''def xml_escape_attrs(d):
    esc = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}
    parts = []
    for k, v in d.items():
        if v is None:
            continue
        parts.append(str(k) + '="' + ''.join(esc.get(c, c) for c in str(v)) + '"')
    return ' '.join(parts)''',
        "partial": r'''def xml_escape_attrs(d):
    parts = []
    for k, v in d.items():
        if v is None:
            continue
        s = (str(v).replace('"', '&quot;').replace('<', '&lt;')
             .replace('>', '&gt;').replace('&', '&amp;'))
        parts.append(str(k) + '="' + s + '"')
    return ' '.join(parts)''',
        "null": "",
        "meta": {"tags": "struct,escape,single-pass", "level": "medium"},
    },
]

TASKS["tasks/g7_numeric"] = [
    {
        "id": "g7n1_split_amount",
        "prompt": ("写一个函数 split_amount(total, n)：把非负整数 total 分成 n 份"
                   "非负整数，各份相差不超过 1，**较大的份排在前面**。"
                   "如 total=10, n=3 → [4, 3, 3]；分不完时末尾补 0"
                   "（total=3, n=5 → [1, 1, 1, 0, 0]）。"
                   "只输出代码，函数名必须是 split_amount。"),
        "tests": [
            "assert split_amount(10, 3) == [4, 3, 3]",
            "assert split_amount(7, 7) == [1, 1, 1, 1, 1, 1, 1]",
            "assert split_amount(3, 5) == [1, 1, 1, 0, 0]",
            "assert split_amount(0, 3) == [0, 0, 0]",
            "assert split_amount(9, 2) == [5, 4]",
        ],
        "oracle": r'''def split_amount(total, n):
    q, r = divmod(total, n)
    return [q + 1] * r + [q] * (n - r)''',
        "partial": r'''def split_amount(total, n):
    q, r = divmod(total, n)
    return [q] * (n - r) + [q + 1] * r''',
        "null": "",
        "meta": {"tags": "numeric,division,order", "level": "easy"},
    },
    {
        "id": "g7n2_next_permutation",
        "prompt": ("写一个函数 next_permutation(seq)：返回序列按字典序的**下一个**"
                   "排列（新列表，不改入参）；若已是最大排列，则回绕到升序最小排列。"
                   "如 [1,2,3] → [1,3,2]；[3,2,1] → [1,2,3]。"
                   "只输出代码，函数名必须是 next_permutation。"),
        "tests": [
            "assert next_permutation([1, 2, 3]) == [1, 3, 2]",
            "assert next_permutation([3, 2, 1]) == [1, 2, 3]",
            "assert next_permutation([1, 1, 5]) == [1, 5, 1]",
            "assert next_permutation([1, 3, 2]) == [2, 1, 3]",
            "assert next_permutation([2, 3, 1]) == [3, 1, 2]",
        ],
        "oracle": r'''def next_permutation(seq):
    a = list(seq)
    i = len(a) - 2
    while i >= 0 and a[i] >= a[i + 1]:
        i -= 1
    if i < 0:
        return sorted(a)
    j = len(a) - 1
    while a[j] <= a[i]:
        j -= 1
    a[i], a[j] = a[j], a[i]
    a[i + 1:] = reversed(a[i + 1:])
    return a''',
        "partial": r'''def next_permutation(seq):
    a = list(seq)
    a[-1], a[-2] = a[-2], a[-1]
    return a''',
        "null": "",
        "meta": {"tags": "numeric,permutation,algorithm", "level": "hard"},
    },
    {
        "id": "g7n3_spiral_order",
        "prompt": ("写一个函数 spiral_order(m)：按**顺时针螺旋**顺序返回矩阵 "
                   "（list of list）的所有元素；空矩阵返回 []。"
                   "如 [[1,2,3],[4,5,6],[7,8,9]] → "
                   "[1,2,3,6,9,8,7,4,5]。"
                   "只输出代码，函数名必须是 spiral_order。"),
        "tests": [
            "assert spiral_order([[1, 2, 3], [4, 5, 6], [7, 8, 9]]) == [1, 2, 3, 6, 9, 8, 7, 4, 5]",
            "assert spiral_order([[1]]) == [1]",
            "assert spiral_order([]) == []",
            "assert spiral_order([[1, 2], [3, 4]]) == [1, 2, 4, 3]",
            "assert spiral_order([[1, 2, 3]]) == [1, 2, 3]",
        ],
        "oracle": r'''def spiral_order(m):
    if not m:
        return []
    out = []
    top, bot = 0, len(m) - 1
    left, right = 0, len(m[0]) - 1
    while top <= bot and left <= right:
        for c in range(left, right + 1):
            out.append(m[top][c])
        top += 1
        for r in range(top, bot + 1):
            out.append(m[r][right])
        right -= 1
        if top <= bot:
            for c in range(right, left - 1, -1):
                out.append(m[bot][c])
            bot -= 1
        if left <= right:
            for r in range(bot, top - 1, -1):
                out.append(m[r][left])
            left += 1
    return out''',
        "partial": r'''def spiral_order(m):
    if not m:
        return []
    out = list(m[0])
    for row in m[1:]:
        out.append(row[-1])
    return out''',
        "null": "",
        "meta": {"tags": "numeric,matrix,traversal", "level": "hard"},
    },
    {
        "id": "g7n4_min_meeting_rooms",
        "prompt": ("写一个函数 min_meeting_rooms(intervals)：区间 [start, end) "
                   "表示一场会议（**左闭右开** —— end 等于下一个 start 时不冲突），"
                   "求最少需要多少间会议室；空列表返回 0。"
                   "如 [[0,30],[5,10],[15,20]] → 2。"
                   "只输出代码，函数名必须是 min_meeting_rooms。"),
        "tests": [
            "assert min_meeting_rooms([[0, 30], [5, 10], [15, 20]]) == 2",
            "assert min_meeting_rooms([[7, 10], [2, 4]]) == 1",
            "assert min_meeting_rooms([]) == 0",
            "assert min_meeting_rooms([[1, 5], [2, 6], [3, 7], [4, 8]]) == 4",
            "assert min_meeting_rooms([[1, 5], [5, 6]]) == 1",
        ],
        "oracle": r'''def min_meeting_rooms(intervals):
    if not intervals:
        return 0
    starts = sorted(s for s, e in intervals)
    ends = sorted(e for s, e in intervals)
    rooms = best = 0
    ei = 0
    for s in starts:
        while ei < len(ends) and ends[ei] <= s:
            ei += 1
            rooms -= 1
        rooms += 1
        best = max(best, rooms)
    return best''',
        "partial": r'''def min_meeting_rooms(intervals):
    if not intervals:
        return 0
    points = sorted(p for s, e in intervals for p in (s, e))
    rooms = best = 0
    for p in points:
        if any(s == p for s, e in intervals):
            rooms += 1
            best = max(best, rooms)
        else:
            rooms -= 1
    return best''',
        "null": "",
        "meta": {"tags": "numeric,intervals,sweep", "level": "medium"},
    },
]


def _self_check() -> None:
    """每一行都必须自己跑一遍：oracle=1.0 / partial 严格居中 / null=0.0。

    ⛔ 这不是锦上添花。上一轮的三个自踩都是"代码写完了但没人验"——
    其中 `--memory-dir 静默废掉 --fresh` 让我对一个假对照读了半天。
    任务集是尺子本身，**尺子必须先验自己**。
    """
    for name, tasks in TASKS.items():
        for t in tasks:
            ns: dict = {}
            exec(compile(t["oracle"], "<oracle>", "exec"), ns)      # noqa: S102
            n = len(t["tests"])
            hits = sum(1 for a in t["tests"] if _truthy(ns, a, t["oracle"]))
            assert hits == n, f"{t['id']} oracle 只过 {hits}/{n}"

            ns = {}
            exec(compile(t["partial"], "<partial>", "exec"), ns)    # noqa: S102
            hits = sum(1 for a in t["tests"] if _truthy(ns, a, t["partial"]))
            assert 0 < hits < n, f"{t['id']} partial={hits}/{n} 没严格居中"

            ns = {}
            exec(compile(t["null"], "<null>", "exec"), ns)          # noqa: S102
            hits = sum(1 for a in t["tests"] if _truthy(ns, a, t["null"]))
            assert hits == 0, f"{t['id']} null 竟然过了 {hits} 条"
    print(f"自检 OK：{sum(len(v) for v in TASKS.values())} 题，"
          f"oracle 全满分 / partial 严格居中 / null 全零")


def _truthy(ns: dict, assertion: str, src: str) -> bool:
    """跑一条 assert，返回有没有过。**异常一律算没过**，不静默。"""
    full = src + "\n" + assertion + "\n"
    try:
        exec(compile(full, "<check>", "exec"), dict(ns))            # noqa: S102
        return True
    except SyntaxError:
        # ⛔ **不许**把 SyntaxError 当成"没答对"。它说的是**题面坏了**，
        #    不是当事人答错了。混为一谈 = 用工具的失败冒充被测对象的失败。
        #    本轮实测：k04 的 assert 里写了真换行符 ⇒ 5 条全是 SyntaxError，
        #    若吞掉就会读成"oracle 只过 1/5"而不是"题面不是合法源码"。
        raise SyntaxError(f"断言本身不是合法源码（题面坏了）：{assertion!r}")
    except Exception:
        return False


def main() -> int:
    root = Path(__file__).resolve().parent      # = .../V1/bench
    _self_check()
    for name, tasks in TASKS.items():
        path = root / f"{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        seen = set()
        for t in tasks:
            assert t["id"] not in seen, f"重复 id: {t['id']}"
            seen.add(t["id"])
            for k in ("oracle", "partial", "null", "prompt", "tests"):
                assert k in t, f"{t['id']} 缺字段 {k}"
            assert t["tests"], f"{t['id']} 没有 test"
            lines.append(json.dumps(t, ensure_ascii=False))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"写入 {path}  题数={len(tasks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
