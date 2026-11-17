"""判据生成器（V184，§252）：**带类型签名**的算子库。

V183 把「系统能不能自己造出判别型判据」拆成 W2（形态能否独立）与 W3（能否自造）。
W3 的三条臂全 0/8，逐条看失败：**5/6 死在槽形状**（`inputs` 被平铺成
`["abcdef", 3]`、单参数函数给了两个实参）。那不是「想不出关系」，是
**装置让模型去写结构**。

本模块把结构从模型手里拿走：

    · 参数个数 —— 从目标代码的 `def` 读（`ast`，⛔ 不执行）
    · 输入      —— 按参数**类型**从固定池生成（模型碰不到 inputs）
    · 槽        —— 只能是枚举 id（⛔ 不许写任意表达式）
    · 判定逻辑  —— 全由本模块渲染

⇒ 模型只填**值**：算子 id + 类型名 list + 三个枚举 id。

⛔ 本模块**不**判断「判据好不好」：`OPS` 里没有一个字段写着某道题该用哪个算子。
   选错算子是本轮要量的能力，不是要被装置兜住的东西。

⛔ 本模块**不**读打分器的任何东西（参考解 / 断言集 / 分数）：输入池是**通用**的
   平凡值（`"abcdef"`、`{}`、`3`），不含任何一道题的输出形态。
   与 `verify.py` 的立论一致：装置提供的是「结构」，不是「答案」。
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────
# 类型池（V184 PREREG §2.2 逐字写死）
#
# ⚠️ 池的取值原则：**平凡、合法、互不相等**。它只负责把函数喂进一个可判的输入域，
#    ⛔ 不承载任何一道题的惯例（那在记忆里，不在这里）。
# ⚠️ `str` 池里刻意留了 `""` 与 `"  a b  "`：空串与带空白串是绝大多数
#    「边界条件写成恒真」的判据最先露馅的地方。
# ──────────────────────────────────────────────────────────────────────────
POOL: dict[str, tuple] = {
    "str": ("abcdef", "", "  a b  ", "A;B=1,2"),
    "int": (0, 3, 5),
    "dict": ({"b": 1, "a": 2}, {}, {"x": {"y": 1}}),
    "list": (["b", "a", "b"], [], ["A", "a"]),
}

#: 长度池（nat⁺）。⛔ 不含 0：`s[-0:]` 是整串，`n=0` 会让 TAIL 自相矛盾 ——
#: 那是装置坑，不是模型错（V183 的 `TAIL` 就踩过这个边）。
LEN_POOL: tuple[int, ...] = (1, 3, 9)

TYPES: tuple[str, ...] = ("str", "int", "dict", "list")

#: 槽的枚举表。键 = 模型写的 id，值 = 渲染进判据的表达式（空串 = 未填）。
DECODERS: dict[str, str] = {
    "": "",
    "json.loads": "import json\ndecode = json.loads",
    "parse_qsl": "from urllib.parse import parse_qsl\ndecode = lambda s: dict(parse_qsl(s))",
    "int": "decode = int",
    "str": "decode = str",
    "list": "decode = list",
}

KEEPS: dict[str, str] = {
    "": "",
    "tail_n": "s[-n:]",
    "ellipsis_tail_n": "'\u2026' + s[-n:]",
    "head_n": "s[:n]",
}

#: 算子签名。`arity` 是**结构要求**（几个位置参数）；`slots` 是该算子要模型填的槽。
OPS: dict[str, dict] = {
    "ROUNDTRIP": {"arity": 1, "slots": ("decode",),
                  "what": "把输出用 decode 解回来必须等于输入"},
    "CONSERVE": {"arity": 1, "slots": (),
                 "what": "输出的叶子多重集必须等于输入的叶子多重集"},
    "EMPTY_IFF_EQUAL": {"arity": 2, "slots": (),
                        "what": "两个输入之间的差异为空 ⟺ 两输入相等"},
    "SUBSEQ": {"arity": 1, "slots": (),
               "what": "输出必须是输入的子序列，且归一化后两两不等"},
    "BOUND": {"arity": 2, "slots": (),
              "what": "输出长度不超过 n；输入不超长时必须原样返回"},
    "TAIL": {"arity": 2, "slots": ("keep",),
             "what": "超长输出的结尾必须是 keep 指定的形状"},
}


def arity(code: str, fn: str = "") -> int:
    """目标代码里**必填位置参数**的个数（有默认值的不算）。⛔ 纯 `ast`，不执行。

    ⛔ 为什么不数 `*args`/`**kwargs`：那是「装置允许任意实参」，不是「函数要几个输入」。
       数进来就等于把「猜结构」又还给模型。
    """
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return -1
    best = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            if fn and node.name == fn:
                best = node
                break
            if best is None:
                best = node
    if best is None:
        return -1
    a = best.args
    return len(a.args) - len(a.defaults)


def reachable(kind: str, n_args: int) -> bool:
    """该算子在 arity=`n_args` 的函数上是否**结构可达**。

    ⛔ 只判结构（参数个数对不对），⛔ 不判「这个算子在这道题上语义上对不对」——
       后者是 A 臂要量的东西，装置不许提前替它答。
    """
    spec = OPS.get(kind)
    if spec is None or n_args < 0:
        return False
    return spec["arity"] == n_args


def validate(kind: str, types: list, slots: dict, n_args: int) -> str:
    """纯结构校验。通过 ⇒ `""`，否则一句**可行动**的错误。

    ⛔ 错误里不许出现「你应该用 X 算子」——那是答案。
    """
    spec = OPS.get(kind)
    if spec is None:
        return f"kind {kind!r} 不在可用算子里：{sorted(OPS)}"
    if not isinstance(types, list) or not types:
        return "types 必须是类型名组成的非空 list"
    if len(types) != n_args:
        return (f"types 长度 {len(types)} 与函数必填参数个数 {n_args} 不符；"
                f"每个参数一个类型名，可选：{list(TYPES)}")
    for i, t in enumerate(types):
        if t not in TYPES:
            return f"types[{i}] = {t!r} 不是类型名；可选：{list(TYPES)}"
    if kind in ("TAIL", "BOUND") and (len(types) != 2 or types[1] != "int"):
        return f"{kind} 需要一个输入加一个长度；types 的第二个必须是 \"int\""
    if not reachable(kind, n_args):
        return f"{kind} 需要 {spec['arity']} 个输入，本题函数有 {n_args} 个 ⇒ 结构不可达"
    for key in spec["slots"]:
        table = DECODERS if key == "decode" else KEEPS
        v = str((slots or {}).get(key) or "")
        if not v:
            return f"{kind} 需要槽 `{key}`；可选：{[k for k in table if k]}"
        if v not in table:
            return f"槽 `{key}` = {v!r} 不在枚举里；可选：{[k for k in table if k]}"
    return ""


#: `diagnose()` 的四种「不过」原因 + `OK`。顺序即优先级（V185 §252.8 第 2 条）。
CODES: tuple[str, ...] = ("KIND", "ARITY", "TYPES", "SLOT")


def diagnose(kind: str, types: list, slots: dict, n_args: int) -> dict:
    """**为什么**不过 —— 比 `validate()` 多两个字段：`code`（哪一类）与 `need/got`（差几个参数）。

    ⛔ 与 `validate()` 的分工：`validate()` 只回答「过/不过」，且**逐字不动**（`AZ145c` 钉着
       它的原串）；本函数回答「哪一类、差多少」，供**回错**用。
    ⚠️ 优先级 **KIND → ARITY → TYPES → SLOT** 是 V185 的改动本体：V184 的 `validate()`
       先判 `len(types)==n_args`，于是「一元函数选了二元算子」被报成**类型**问题
       （`g7d2` + `TAIL`：`len(["dict"])==1==n_args` 先过，再撞上 TAIL 的 `types[1]` 特判）
       ⇒ 回给模型的话指错了方向，A4 因此假阴。
    ⛔ 句子本身不许点名「该换哪个算子」（那是答案）；`AZ146d` 钉这条。
    """
    k = str(kind or "")
    spec = OPS.get(k)
    out = {"kind": k, "need": None, "got": n_args, "ok": False, "code": "KIND", "msg": ""}
    if spec is None:
        out["msg"] = f"kind {kind!r} 不在可用算子里：{sorted(OPS)}"
        return out
    out["need"] = spec["arity"]
    if n_args < 0:
        out["code"] = "ARITY"
        out["msg"] = "读不出本题函数的必填参数个数（没有 def 或语法错）"
        return out
    if spec["arity"] != n_args:
        out["code"] = "ARITY"
        out["msg"] = (f"{k} 要 {spec['arity']} 个位置参数，本题函数只有 {n_args} 个"
                      f" ⇒ 参数个数对不上，这个算子在这道题上结构不可达")
        return out
    if not isinstance(types, list) or not types:
        out["code"] = "TYPES"
        out["msg"] = "types 必须是类型名组成的非空 list"
        return out
    if len(types) != n_args:
        out["code"] = "TYPES"
        out["msg"] = (f"types 长度 {len(types)} 与函数必填参数个数 {n_args} 不符；"
                      f"每个参数一个类型名，可选：{list(TYPES)}")
        return out
    for i, t in enumerate(types):
        if t not in TYPES:
            out["code"] = "TYPES"
            out["msg"] = f"types[{i}] = {t!r} 不是类型名；可选：{list(TYPES)}"
            return out
    if k in ("TAIL", "BOUND") and types[1] != "int":
        out["code"] = "TYPES"
        out["msg"] = f"{k} 的第二个类型必须是 \"int\"（长度），拿到的是 {types[1]!r}"
        return out
    for key in spec["slots"]:
        table = DECODERS if key == "decode" else KEEPS
        v = str((slots or {}).get(key) or "")
        if not v:
            out["code"] = "SLOT"
            out["msg"] = f"{k} 需要槽 `{key}`；可选：{[x for x in table if x]}"
            return out
        if v not in table:
            out["code"] = "SLOT"
            out["msg"] = f"槽 `{key}` = {v!r} 不在枚举里；可选：{[x for x in table if x]}"
            return out
    out.update({"ok": True, "code": "OK", "msg": ""})
    return out


def diagnose_text(d: dict) -> str:
    """把 `diagnose()` 的结果说成**一句人话**（不含目录、不含其它算子名）。"""
    code, k, need, got = d.get("code"), d.get("kind"), d.get("need"), d.get("got")
    if code == "ARITY":
        if got is not None and got < 0:
            return f"你选的是 {k}，它要 {need} 个位置参数；{d.get('msg')}"
        return f"你选的是 {k}，它要 {need} 个位置参数；本题函数只有 {got} 个。"
    if code == "KIND":
        return f"你选的 {k!r} 不是一个可用算子。"
    if code == "OK":
        return ""
    return f"你选的是 {k}（它要 {need} 个位置参数，本题也是 {got} 个），但：{d.get('msg')}"


def retry_hint(d: dict) -> str:
    """**一次重选**时回给用户的整段话：结构事实 + 目录 + 同一份 JSON 要求。

    ⛔ 只给结构事实，⛔ 不给「该换哪个」 —— 目录是全量陈列，与首轮 system 里那份**同一份**，
       陈列不等于推荐。
    """
    return (diagnose_text(d) + "\n\n"
            "可用算子：\n" + catalogue() + "\n"
            f"本题函数的必填位置参数：{d.get('got')} 个。\n"
            "⛔ 仍然不要写 inputs。请只输出一个 JSON。")


def _inputs_for(kind: str, types: list) -> list:
    """按类型生成输入（PREREG §2.2 的三条规则，逐字实现）。

    ⚠️ 单参数时元素**就是值本身**（不是 1-tuple）：判据里 `for x in INPUTS: fn(x)`。
       V184 自踩：初版写成 `[(v,) for v in ...]` ⇒ 把 tuple 塞进 `dedupe_keep`，
       8 题一起报 `'tuple' object has no attribute ...` —— 那是装置焊错了，不是能力。
    """
    if len(types) == 1:
        return [(v,) for v in POOL[types[0]]]
    if kind in ("TAIL", "BOUND"):
        p = POOL[types[0]]
        if types[0] == "str":
            return [(p[0], LEN_POOL[0]), (p[0], LEN_POOL[2]),
                    (p[2], LEN_POOL[1]), (p[1], LEN_POOL[0])]
        return [(p[0], LEN_POOL[0]), (p[0], LEN_POOL[2]), (p[1], LEN_POOL[0])]
    a, b = POOL[types[0]], POOL[types[1]]
    if types[0] == types[1]:
        return [(a[0], a[0]), (a[0], a[1]), (a[1], a[1])]
    return [(a[0], b[0]), (a[0], b[1]), (a[1], b[0])]


_LEAVES = '''def _leaf_values(v):
    if isinstance(v, dict):
        for k in v:
            yield from _leaf_values(v[k])
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from _leaf_values(x)
    else:
        yield v
'''


def _render(pre: str, inputs: list, body: str) -> str:
    head = []
    if pre:
        head.append(pre)
    head.append("INPUTS = " + repr(inputs))
    head.append("")
    head.append("")
    head.append("def _rel():")
    return "\n".join(head + [body, "    return True"]) + "\n"


#: 与 `mw.verify._EX_TIMEOUT_S` 同值：圈域探测也是一种执行，超时也要兜住。
EX_TIMEOUT_S = 5.0
_MARK = "@@V184"


def legal_indexes(code: str, fn: str, inputs: list) -> list | None:
    """在**目标实现**上把池收敛到合法域：只保留「函数吃得下」的组。

    ⛔ 只问「能不能吃」（抛不抛异常），⛔ 不问「返回值对不对」——后者是判别力（收费那一半）。
    ⛔ 故障一律折成 `None`，绝不抛。

    为什么必须有这一步（V184 STAGE0 实测）：通用池对某些函数是**越界**的
    （`to_ini` 要求顶层值是 dict、`fmt_amount` 要数值）⇒ 越界组会让判据在
    **正确实现**上也失败，读成「模型判据差」而其实是装置喂错了（纪律 88 同型）。
    这是「可达性免费」的一部分（纪律 66）：圈域不收费，只问输入在不在域里。
    """
    if not (code or "").strip() or not inputs:
        return None
    lines = [code.rstrip("\n"), "", "_RES = []", "", "",
             "def _v184_probe(*a):", "    try:", f"        {fn}(*a)", "        _RES.append(1)",
             "    except Exception:", "        _RES.append(0)"]
    for a in inputs:
        lines.append("_v184_probe(*%r)" % (tuple(a),))
    lines.append(f"print({_MARK!r} + __import__('json').dumps(_RES))")
    tmp = tempfile.mkdtemp(prefix="v1cg-")
    try:
        p = Path(tmp) / "probe.py"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        r = subprocess.run([sys.executable, "-I", "-B", str(p)], cwd=tmp,
                           capture_output=True, text=True, timeout=EX_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for ln in (r.stdout or "").splitlines():
        if ln.startswith(_MARK):
            try:
                flags = json.loads(ln[len(_MARK):])
            except json.JSONDecodeError:
                return None
            return [i for i, v in enumerate(flags) if v]
    return None


def inputs_for(kind: str, types: list) -> list:
    """全池输入（未圈域）。臂先拿它，再用 `legal_indexes()` 收敛后回填 `build()`。"""
    return _inputs_for(kind, types)


def build(kind: str, types: list, slots: dict, fn: str, inputs: list | None = None) -> str:
    """把 (算子, 类型, 槽) 渲染成可跑的 `_rel()` 源码。⛔ 校验不过 ⇒ 空串。

    `inputs` 为空 ⇒ 用全池（V183 同型）；给了 ⇒ 就是圈域后的合法组（推荐）。
    """
    n = len(types) if isinstance(types, list) else -1
    if validate(kind, types, slots, n):
        return ""
    if inputs is None:
        inputs = _inputs_for(kind, types)
    slots = slots or {}
    if kind == "ROUNDTRIP":
        body = (
            "    for x, in INPUTS:\n"
            f"        got = {fn}(x)\n"
            "        try:\n"
            "            back = decode(got)\n"
            "        except Exception as e:\n"
            '            raise AssertionError(f"ROUNDTRIP: {x!r} ⇒ {got!r} 解不开：{e}")\n'
            "        if back != x:\n"
            '            raise AssertionError(f"ROUNDTRIP: {x!r} ⇒ {got!r} 解回 {back!r}")'
        )
        pre = DECODERS[str(slots.get("decode") or "")]
    elif kind == "CONSERVE":
        body = (
            "    from collections import Counter\n"
            "    for x, in INPUTS:\n"
            f"        got = {fn}(x)\n"
            "        try:\n"
            "            have = Counter(_leaf_values(got))\n"
            "        except Exception as e:\n"
            '            raise AssertionError(f"CONSERVE: {x!r} ⇒ {got!r} 取不出叶子：{e}")\n'
            "        want = Counter(_leaf_values(x))\n"
            "        if have != want:\n"
            '            raise AssertionError(f"CONSERVE: {x!r} ⇒ 叶子 {dict(have)!r} ≠ {dict(want)!r}")'
        )
        pre = _LEAVES.strip("\n")
    elif kind == "EMPTY_IFF_EQUAL":
        body = (
            "    for a, b in INPUTS:\n"
            f"        got = {fn}(a, b)\n"
            "        if (got == {}) != (a == b):\n"
            '            raise AssertionError(f"EMPTY_IFF_EQUAL: {a!r} vs {b!r} ⇒ {got!r}")'
        )
        pre = ""
    elif kind == "SUBSEQ":
        body = (
            "    for xs, in INPUTS:\n"
            f"        got = {fn}(list(xs))\n"
            "        if not isinstance(got, list):\n"
            '            raise AssertionError(f"SUBSEQ: {xs!r} ⇒ 非 list")\n'
            "        keys = [str(v).strip().lower() for v in got]\n"
            "        if len(keys) != len(set(keys)):\n"
            '            raise AssertionError(f"SUBSEQ: {xs!r} ⇒ {got!r} 归一化后仍有重复")\n'
            "        it = iter(xs)\n"
            "        for x in got:\n"
            "            if not any(x == y for y in it):\n"
            '                raise AssertionError(f"SUBSEQ: {xs!r} ⇒ {got!r} 不是子序列")'
        )
        pre = ""
    elif kind == "BOUND":
        body = (
            "    for s, n in INPUTS:\n"
            f"        got = {fn}(s, n)\n"
            "        if len(s) <= n:\n"
            "            if got != s:\n"
            '                raise AssertionError(f"BOUND: {(s, n)!r} ⇒ {got!r} 改了短串")\n'
            "        elif len(got) > n:\n"
            '            raise AssertionError(f"BOUND: {(s, n)!r} ⇒ {got!r} 超界")'
        )
        pre = ""
    elif kind == "TAIL":
        body = (
            "    for s, n in INPUTS:\n"
            f"        got = {fn}(s, n)\n"
            f"        if len(s) > n and not got.endswith({KEEPS[str(slots.get('keep') or '')]}):\n"
            '            raise AssertionError(f"TAIL: {(s, n)!r} ⇒ {got!r}")'
        )
        pre = ""
    else:  # pragma: no cover - validate 已拦住
        return ""
    return _render(pre, inputs, body)


def skeleton(kind: str | None = None) -> str:
    """给模型看的 JSON 骨架（`kind` 为空 ⇒ 通用版）。⛔ 不含任何题目信息。"""
    kinds = [kind] if kind else sorted(OPS)
    return ('{"kind": "<' + "|".join(kinds) + '>", '
            '"types": ["<' + "|".join(TYPES) + '>"], '
            '"decode": "<' + "|".join(k for k in DECODERS if k) + '>", '
            '"keep": "<' + "|".join(k for k in KEEPS if k) + '>"}')


def catalogue() -> str:
    """算子目录（给 prompt 用）：每个算子一行，含 arity 与槽的可选值。"""
    out = []
    for k, spec in OPS.items():
        extra = ""
        for key in spec["slots"]:
            table = DECODERS if key == "decode" else KEEPS
            extra += f"；{key} 可选 {[x for x in table if x]}"
        out.append(f"- {k}（{spec['arity']} 个参数）：{spec['what']}{extra}")
    return "\n".join(out)
