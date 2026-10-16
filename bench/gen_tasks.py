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


def main() -> int:
    root = Path(__file__).resolve().parent      # = .../V1/bench
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
