"""判据准入闸（V183，§251）—— 挡住「期望值写成字面量」的判据。

## 为什么是这一条

V79 §148.3 的墙：模型自写的断言**编码的正是导致实现出错的那个理解**，于是
22 条断言在正确答案上只过 1/8 ⇒ 判别力 0/8。原话是「自写自检在原理上不构成
独立判据」。

那句话的可操作形态只有一个：**判据里写着「期望输出是什么」**。模型的读法一旦
错，期望值就跟着错，判据便与实现共享同一个误解。反过来，判据若只断言
`decode(f(x)) == x`（`decode` 由标准库提供），模型的格式误解**没有入口**进到判定里。

⇒ 本模块不生产判据，只做**准入**：把含「非空字面量期望值」的判据挡在门外。

## ⛔ 这不是新尺子

它**不给任何东西打分**，只回答「这条判据的判定依赖不依赖模型写的期望值」。
判分仍走 `mw.verify.run_example`（内核同一把尺）。
"""
from __future__ import annotations

import ast
from typing import Sequence

__all__ = ["literal_expectations", "admit", "run", "audit"]

#: 比较运算符里，「相等类」才算期望值；`<`/`>` 这类是结构界，不算。
_EQ_OPS = (ast.Eq, ast.NotEq, ast.Is, ast.IsNot)


def _nonempty_literal(node: ast.AST) -> str | None:
    """把「非空字面量」渲染成一小段文本；不是非空字面量 ⇒ None。

    ⛔ 空容器 / 空串 / None / 0 **不算**：`got == {}`、`to_ini({}) != ""` 这类是
       **结构/规格**断言（「没有差异」「空输入给空串」），换一种读法也照样成立。
       要挡的是「这个格子应当等于 `'a=1; b=2'`」那种**格式细节**。
    """
    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, str):
            return repr(v) if v else None
        if isinstance(v, bool):
            # ⚠️ `is True` 是**格式期望**（「哪些写法算真」正是模型读错的那一处，
            #    V79 的 g7e1 就是这条）。第一版把它当「结构」放过 ⇒ g7e1 漏网。
            return repr(v)
        if v is None:
            return None
        if isinstance(v, (int, float)) and v != 0:
            return repr(v)
        return None
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        if node.elts:
            try:
                return repr(ast.literal_eval(node))
            except (ValueError, SyntaxError):
                return f"<{type(node).__name__} {len(node.elts)} 元素>"
        return None
    if isinstance(node, ast.Dict):
        if node.keys:
            try:
                return repr(ast.literal_eval(node))
            except (ValueError, SyntaxError):
                return f"<dict {len(node.keys)} 键>"
        return None
    return None


def _param_names(tree: ast.AST) -> set[str]:
    """源码里所有 `FunctionDef` 的形参名（含 only/kw-only）。

    ⛔ 只认形参，不认模块级同名变量 —— 否则 `sep = " "` 这种自由赋值就能冒充规格参数。
    """
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            a = node.args
            for grp in (a.posonlyargs, a.args, a.kwonlyargs):
                for x in grp:
                    out.add(x.arg)
    return out


def literal_expectations(src: str, params: Sequence[str] = ()) -> list[str]:
    """返回判据里所有「拿非空字面量当期望值」的位置摘要（空列表 = 判据干净）。

    判定口径（写死，可撤回）：`Compare` 节点，运算符 ∈ {==, !=, is, is not}，
    两侧中一侧是**非空字面量**、另一侧是**调用或名字** ⇒ 记一条。

    ⛔ 不做文本匹配：`'==' in src` 会把 `(got == {}) != (a == b)` 也算上，而那是
       结构断言。用 AST 是为了让「算不算」有唯一答案。

    ## V188（§256）：`params` —— 「写死答案」与「写死规格」的分界

    V187 的 `NUMFMT` 判据含 `if neg == "paren"`，被旧口径判为「字面量期望值」⇒ 误伤 2 格。
    但那个字面量**不是模型写的**：它是装置按枚举 id 渲染进参数化参考实现的**规格参数**。
    模型能选的只有枚举 id，而 id 选得对不对由**同一把尺**在 oracle 上判（`AZ150a`）。

    ⇒ 若非字面量侧是 `Name`、该名字 ∈ `params`、且它确实是源码里某个函数的**形参**，
       这一条不计。⛔ `params` 由装置给定的算子表决定，判据本身无权声明。
    """
    hits: list[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"UNPARSABLE: {e.msg} (line {e.lineno})"]
    allow = {str(x) for x in (params or ())}
    formals = _param_names(tree) if allow else set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        sides = [node.left, *node.comparators]
        ops = node.ops
        for i, op in enumerate(ops):
            if not isinstance(op, _EQ_OPS):
                continue
            a, b = sides[i], sides[i + 1]
            for lit, other in ((a, b), (b, a)):
                txt = _nonempty_literal(lit)
                # ⚠️ `len(x) == 2` 是**结构计数**，不是格式期望 ⇒ 不算（第一版误伤）。
                if isinstance(other, ast.Call) and isinstance(other.func, ast.Name) \
                        and other.func.id == "len":
                    continue
                if not txt:
                    continue
                if isinstance(other, ast.Name) and other.id in allow and other.id in formals:
                    continue        # 规格参数的分派，不是期望值（V188）
                if isinstance(other, (ast.Call, ast.Name)):
                    hits.append(f"line {getattr(node, 'lineno', 0)}: "
                                f"{txt[:40]} vs {ast.unparse(other)[:40]}")
    return hits


def admit(src: str, params: Sequence[str] = ()) -> tuple[bool, str]:
    """准入判定：`(通过?, 理由)`。⛔ 不含「像不像」的启发式，只有一条结构规则。

    `params`：该判据所用算子的**规格参数名**（`critgen.spec_params(kind)`）。
    手写判据一律 `params=()` ⇒ 口径与 V183 逐字相同。
    """
    if not (src or "").strip():
        return False, "EMPTY"
    hits = literal_expectations(src, params)
    if not hits:
        return True, "OK"
    if hits[0].startswith("UNPARSABLE"):
        return False, hits[0]
    return False, f"LITERAL_EXPECTATION×{len(hits)}: {hits[0]}"


def run(code: str, crit_src: str, call: str = "assert _rel()") -> str:
    """把判据接到实现上跑一次（⛔ 不自己起进程：交 `mw.verify.run_example`）。"""
    from .verify import run_example
    return run_example(code.rstrip("\n") + "\n\n" + crit_src.strip() + "\n", call)


def audit(code: str, crits: Sequence[tuple[str, str]]) -> list[dict]:
    """逐条判据：准入 + 在实现上的判定。`crits = [(名字, 判据源码)]`。"""
    out = []
    for name, src in crits:
        ok, why = admit(src)
        out.append({"name": name, "admitted": ok, "reason": why,
                    "verdict": (run(code, src) if ok else "")})
    return out
