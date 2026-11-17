"""C 臂的算子模板 —— 判定**由模板算**，模型只填槽（探针输入 / decode / norm…）。

⛔ 模板里不出现任何任务名：不是 per-task 硬编码。不确定性全在「选哪个算子 + 填什么槽」。
"""
from __future__ import annotations

KINDS = {
    "ROUNDTRIP": "把实现的输出用 decode 解回来，必须等于输入（decode 用标准库）",
    "CONSERVE": "输出的叶子多重集必须等于输入的叶子多重集（信息守恒）",
    "EMPTY_IFF_EQUAL": "对两个输入的比较：差异为空 ⟺ 两输入相等",
    "SUBSEQ": "输出必须是输入的子序列，且按 norm 归一化后两两不等",
    "BOUND": "输出长度不超过 bound；输入本身不超长时必须原样返回",
    "TAIL": "输出必须以 keep 结尾",
}

_HEAD = '''{slots}
INPUTS = {inputs!r}


def _rel():
{body}
    return True
'''


def build(kind: str, slots: dict, inputs: list, fn: str) -> str:
    """把一个 (算子, 槽, 探针输入) 变成可跑的 `_rel()` 源码。⛔ 失败返回空串。"""
    parts = []
    for key in ("decode", "leaves_in", "leaves_out", "norm"):
        v = (slots.get(key) or "").strip()
        if v:
            parts.append(v)
    pre = "\n\n".join(parts)

    if kind == "ROUNDTRIP":
        body = f'''    for x in INPUTS:
        got = {fn}(x)
        try:
            back = decode(got)
        except Exception as e:
            raise AssertionError(f"ROUNDTRIP: {{x!r}} ⇒ {{got!r}} 解不开：{{e}}")
        if back != x:
            raise AssertionError(f"ROUNDTRIP: {{x!r}} ⇒ {{got!r}} 解回 {{back!r}}")'''
    elif kind == "CONSERVE":
        body = f'''    from collections import Counter
    for x in INPUTS:
        got = {fn}(x)
        try:
            have = Counter(v for v in leaves_out(got) if v is not None)
        except Exception as e:
            raise AssertionError(f"CONSERVE: {{x!r}} ⇒ {{got!r}} 取不出叶子：{{e}}")
        want = Counter(leaves_in(x))
        if have != want:
            raise AssertionError(f"CONSERVE: {{x!r}} ⇒ 叶子 {{dict(have)!r}} ≠ {{dict(want)!r}}")'''
    elif kind == "EMPTY_IFF_EQUAL":
        body = f'''    for a, b in INPUTS:
        got = {fn}(a, b)
        if (got == {{}}) != (a == b):
            raise AssertionError(f"EMPTY_IFF_EQUAL: {{a!r}} vs {{b!r}} ⇒ {{got!r}}")'''
    elif kind == "SUBSEQ":
        body = f'''    for xs in INPUTS:
        got = {fn}(list(xs))
        if not isinstance(got, list):
            raise AssertionError(f"SUBSEQ: {{xs!r}} ⇒ 非 list")
        keys = [norm(v) for v in got]
        if len(keys) != len(set(keys)):
            raise AssertionError(f"SUBSEQ: {{xs!r}} ⇒ {{got!r}} 归一化后仍有重复")
        it = iter(xs)
        for x in got:
            if not any(x == y for y in it):
                raise AssertionError(f"SUBSEQ: {{xs!r}} ⇒ {{got!r}} 不是子序列")'''
    elif kind == "BOUND":
        body = f'''    for s, n in INPUTS:
        got = {fn}(s, n)
        if len(s) <= n:
            if got != s:
                raise AssertionError(f"BOUND: {{(s, n)!r}} ⇒ {{got!r}} 改了短串")
        elif len(got) > ({slots.get("bound") or "n"}):
            raise AssertionError(f"BOUND: {{(s, n)!r}} ⇒ {{got!r}} 超界")'''
    elif kind == "TAIL":
        body = f'''    for s, n in INPUTS:
        got = {fn}(s, n)
        if len(s) > n and not got.endswith({slots.get("keep") or "s[-n:]"}):
            raise AssertionError(f"TAIL: {{(s, n)!r}} ⇒ {{got!r}}")'''
    else:
        return ""
    return _HEAD.format(slots=pre, inputs=inputs, body=body)


def call_for(kind: str) -> str:
    return "assert _rel()"


def validate(kind: str, spec: dict) -> str:
    """纯结构校验（⛔ 不执行任何用户代码）。通过 ⇒ `""`，否则给一句可行动的错误。"""
    if kind not in KINDS:
        return f"kind {kind!r} 不在可用算子里：{sorted(KINDS)}"
    inputs = spec.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        return "inputs 必须是非空 list"
    pair_kinds = ("EMPTY_IFF_EQUAL", "BOUND", "TAIL")
    if kind in pair_kinds:
        for i, el in enumerate(inputs):
            if not (isinstance(el, (list, tuple)) and len(el) == 2):
                return (f"inputs[{i}] = {el!r}：{kind} 的每个元素必须是长度为 2 的 "
                        f"[输入, 参数]（例：[[\"abcdef\", 3]]）")
            if kind in ("BOUND", "TAIL") and not isinstance(el[1], int):
                return f"inputs[{i}][1] = {el[1]!r} 必须是 int"
    need = {"ROUNDTRIP": ("decode",), "CONSERVE": ("leaves_in", "leaves_out"),
            "SUBSEQ": ("norm",)}
    for key in need.get(kind, ()):
        if not (spec.get(key) or "").strip():
            return f"{kind} 需要非空槽 `{key}`"
    return ""
