#!/usr/bin/env python3
"""题面与 oracle 的**同一个源** —— 生成型题族的规格层（第七十一轮）。

## 为什么要有这个文件

7y 关掉判据层的那条读数不是"某道题难"，而是**题面与 oracle 是两份手写产物**：

    题面写「最多 n 个字符」，该题自己的 oracle 交付的答案是 `'...def'`（长 4 > n=3），
    而 5/5 个评分要求就是这个答案。

⇒ 「来源贴着题面」不等于「来源满足题面」。两份手写产物之间**没有任何约束**，
只能靠人事后逐条读；读过一遍，下一批题又从零开始。

所以这里把两者都做成 **spec 的函数**：

    spec ──render_prompt()──▶ 题面文本（人读的那份）
         └─compile_oracle()─▶ oracle 源码（机器跑的那份）

而 `admit()` 在**四条互不依赖的证据**上验，任一条不成立就拒绝这道题：

    A3 后置条件  题面里承诺的东西，输出必须满足（逐阶段、**局部**谓词判定）
    A4 三方对撞  生成的源码 == 独立解释器（两条实现，逐条用例对拍）
    A5 同源双射  改一个参数 ⇒ 题面**必须**变 且 行为**必须**变（双向）
    A6 确定性    同一 spec 两次编译/执行，逐字相同

A4 与 A5 刻意做两件事：一条防"代码没读题面"，一条防"题面没写代码读的东西"。

## 这套检查**不覆盖**什么（别把它读大）

· 它只覆盖**生成的**题。手写题没有 spec，`post()` 无从推导 —— 7y 那个缺陷
  在手写题上**依然只能靠人读**。这条限制是硬的，不许含糊。
· 测试期望由 oracle 自己生成（`tests()`），所以「oracle 得 1.0」是**恒真**的，
  它只证明代码能跑。oracle 的**正确性**由 A3/A4/A5 三条承重，不由分数承重。
  这是刻意的分工，写在这里防后人把 1.0 当证据。
"""

from __future__ import annotations

import contextlib
import random
from typing import Any, Callable

Row = str
Rule = dict[str, Any]
Spec = dict[str, Any]
Pred = Callable[[list[str]], bool]

#: 题面里的序号。⛔ 不引第三方包（本仓库零依赖），也不许写死成 1. 2. 3.
_NUM = "①②③④⑤⑥⑦⑧⑨⑩"


# ===========================================================================
# 算子表 —— 题面句子 / 源码行 / 独立解释器 / 后置条件 / 可替代参数
#
# ⛔ 一条 op 必须同时给出五样东西。**少给一样就是"题面写了却没人执行"的那类洞**，
#    A5（同源双射）会当场拒绝它 —— 这不是我勤奋，是表格完整性检查。
# ===========================================================================


def _key_expr(fold: bool) -> str:
    """去重键：是否忽略首尾空白与大小写。"""
    return "_x.strip().lower()" if fold else "_x"


def _op_strip(rule: Rule, naive: bool = False) -> dict[str, Any]:
    return {
        "render": "去掉每行首尾的空白字符",
        "code": "    out = [_x.strip() for _x in out]",
        "apply": lambda rows: [r.strip() for r in rows],
        "post": [("strip", lambda rows: all(r == r.strip() for r in rows))],
        "alts": [],
    }


def _op_lower(rule: Rule, naive: bool = False) -> dict[str, Any]:
    return {
        "render": "每行统一转成小写",
        "code": "    out = [_x.lower() for _x in out]",
        "apply": lambda rows: [r.lower() for r in rows],
        "post": [("lower", lambda rows: all(r == r.lower() for r in rows))],
        "alts": [],
    }


def _op_collapse(rule: Rule, naive: bool = False) -> dict[str, Any]:
    return {
        "render": "把每行内部连续的空白压成一个空格（制表符也算空白）",
        "code": "    out = [' '.join(_x.split()) for _x in out]",
        "apply": lambda rows: [" ".join(r.split()) for r in rows],
        "post": [("collapse",
                  lambda rows: all("  " not in r and "\t" not in r for r in rows))],
        "alts": [],
    }


def _op_dedupe(rule: Rule, naive: bool = False) -> dict[str, Any]:
    # 常识实现 = 参数取默认值（保第一次出现、精确比较）。⛔ 不是另一段代码 ——
    # 另一段代码会引入"两处实现"，而那正是本文件要消灭的东西。
    keep: str = "first" if naive else rule.get("keep", "first")
    fold: bool = False if naive else bool(rule.get("fold", False))
    which = "最后一次" if keep == "last" else "第一次"
    if keep == "last":
        code = ("    _pos = {}\n"
                "    for _i, _x in enumerate(out):\n"
                f"        _pos[{_key_expr(fold)}] = _i\n"
                "    _keep = set(_pos.values())\n"
                "    out = [_x for _i, _x in enumerate(out) if _i in _keep]")

        def apply(rows: list[str]) -> list[str]:
            # ⛔ 解释器不许照抄 code：两条实现要是同一份，A4 就成了自己对自己。
            #    这里换算法 —— 数每个键出现几次，只有"最后一次"才留下。
            keys = [(r.strip().lower() if fold else r) for r in rows]
            total = {k: keys.count(k) for k in set(keys)}
            seen: dict[str, int] = {}
            out: list[str] = []
            for i, r in enumerate(rows):
                seen[keys[i]] = seen.get(keys[i], 0) + 1
                if seen[keys[i]] == total[keys[i]]:
                    out.append(r)
            return out
    else:
        code = ("    _seen = set()\n"
                "    _kept = []\n"
                "    for _x in out:\n"
                f"        _k = {_key_expr(fold)}\n"
                "        if _k in _seen:\n"
                "            continue\n"
                "        _seen.add(_k)\n"
                "        _kept.append(_x)\n"
                "    out = _kept")

        def apply(rows: list[str]) -> list[str]:
            keys = [(r.strip().lower() if fold else r) for r in rows]
            first: dict[str, int] = {}
            for i, k in enumerate(keys):
                first.setdefault(k, i)
            return [rows[i] for i in sorted(first.values())]

    def post(rows: list[str]) -> bool:
        keys = [(r.strip().lower() if fold else r) for r in rows]
        return len(keys) == len(set(keys))

    alt_keep = dict(rule, keep=("first" if keep == "last" else "last"))
    alt_fold = dict(rule, fold=not fold)
    # ⚠️ `fold=False` 时**不提**保留哪一次：内容完全相同的两行，"哪一次"在输出里
    #    不可观测 —— 写了它就是一句无法被任何用例检验的话，正是 7y 那个洞。
    #    同时它也不进 `alts`：不可观测的参数会被 A5 判成"题面变了行为不变"。
    alt = [alt_fold] + ([alt_keep] if fold else [])
    if fold:
        render = (f"按去掉首尾空白并转成小写后相同的键去重，保留{which}出现的那一行"
                  f"（输出仍按原来的先后顺序，不重排）")
    else:
        render = "去掉内容完全相同的重复行（输出仍按原来的先后顺序，不重排）"
    return {
        "render": render,
        "code": code,
        "apply": apply,
        "post": [("dedupe 键唯一", post)],
        "alts": alt,
    }


def _op_sort(rule: Rule, naive: bool = False) -> dict[str, Any]:
    # 常识实现 = 按文本升序（`sorted()` 的默认行为）
    key: str = "text" if naive else rule.get("key", "text")
    desc: bool = False if naive else bool(rule.get("desc", False))
    if key == "len":
        code = ("    out = sorted(out, key=lambda _x: (-len(_x), _x))" if desc
                else "    out = sorted(out, key=lambda _x: (len(_x), _x))")
        render = ("按字符数" + ("降序" if desc else "升序")
                  + "排列；字符数相同的按文本字典序升序")

        def ok(a: str, b: str) -> bool:
            return ((-len(a), a) <= (-len(b), b)) if desc else ((len(a), a) <= (len(b), b))
    else:
        code = ("    out = sorted(out, reverse=True)" if desc
                else "    out = sorted(out)")
        render = "按文本字典序" + ("降序" if desc else "升序") + "排列"

        def ok(a: str, b: str) -> bool:
            return (a >= b) if desc else (a <= b)

    def post(rows: list[str]) -> bool:
        # 相邻对判定（O(n) 局部），**不**重跑 sorted() —— 换算法，才叫第二条证据
        return all(ok(rows[i], rows[i + 1]) for i in range(len(rows) - 1))

    def apply(rows: list[str]) -> list[str]:
        if key == "len":
            return sorted(rows, key=lambda s: (-len(s), s) if desc else (len(s), s))
        return sorted(rows, reverse=desc)

    return {
        "render": render,
        "code": code,
        "apply": apply,
        "post": [("sort 相邻有序", post)],
        "alts": [dict(rule, key=("text" if key == "len" else "len")),
                 dict(rule, desc=not desc)],
    }


def _op_truncate(rule: Rule, naive: bool = False) -> dict[str, Any]:
    n: int = int(rule["n"])
    # 常识实现：留 n-1 个字符再接一个省略号。长度同样是 n，所以它**长度合格、
    # 内容不合格** —— 这正是它该被扣分的地方，也是「后置条件」挡不住的洞。
    code = (f"    out = [(_x[:{n - 1}] + '…') if len(_x) > {n} else _x "
            f"for _x in out]" if naive else f"    out = [_x[:{n}] for _x in out]")
    return {
        "render": f"每行只保留前 {n} 个字符（超出的直接截掉，**不加省略号**）",
        "code": code,
        "apply": lambda rows: [r[:n] for r in rows],
        "post": [("truncate 长度上限", lambda rows: all(len(r) <= n for r in rows))],
        "alts": [dict(rule, n=n - 2)] if n - 2 >= 2 else [],
    }


def _op_prefix(rule: Rule, naive: bool = False) -> dict[str, Any]:
    text: str = rule["text"]
    return {
        "render": f"每行前面加上固定前缀「{text}」",
        "code": f"    out = [{text!r} + _x for _x in out]",
        "apply": lambda rows: [text + r for r in rows],
        "post": [("prefix 前缀", lambda rows: all(r.startswith(text) for r in rows))],
        "alts": [dict(rule, text=text + "x")],
    }


_OPS: dict[str, Callable[..., dict[str, Any]]] = {
    "strip": _op_strip,
    "lower": _op_lower,
    "collapse": _op_collapse,
    "dedupe": _op_dedupe,
    "sort": _op_sort,
    "truncate": _op_truncate,
    "prefix": _op_prefix,
}

OPS = tuple(_OPS)


# ===========================================================================
# 生成：spec → 题面 / 源码 / 解释器
# ===========================================================================


def render_prompt(spec: Spec) -> str:
    """题面**全部**由 spec 渲染。⛔ 这里不许出现 spec 里没有的字面事实。"""
    fn = spec["fn"]
    lines = [f"写一个函数 {fn}(rows)，参数是字符串列表。"
             "按**下面列出的先后顺序**依次处理，返回处理完的列表："]
    for i, rule in enumerate(spec["rules"]):
        lines.append(f"{_NUM[i]} {_OPS[rule['op']](rule)['render']}")
    lines.append(f"只输出代码，函数名必须是 {fn}。")
    return "\n".join(lines)


def compile_oracle(spec: Spec, *, naive: bool = False,
                   upto: int | None = None) -> str:
    """spec → 可执行源码。`naive=True` 时把参数换成**常识默认**（见各 op 的注释）。

    `upto=k` 只编前 k 条规则 —— 后置条件只在它自己那一步之后成立，
    所以 A3 必须能编译"流水线的前缀"，并且**在生成的源码上**判，
    而不是只在解释器上判（在解释器上判 = 拿另一条实现替它受审）。
    """
    body = [f"def {spec['fn']}(rows):", "    out = [str(_x) for _x in rows]"]
    for rule in spec["rules"][:upto if upto is not None else len(spec["rules"])]:
        body.append(_OPS[rule["op"]](rule, naive)["code"])
    body.append("    return out")
    return "\n".join(body)


def apply_pipeline(spec: Spec, rows: list[str], upto: int | None = None) -> list[str]:
    """独立解释器：逐条规则在进程内算，**不 exec 生成的源码**。

    与 `compile_oracle` 是同一 spec 的两条实现 —— A4 拿这两条对撞。
    分阶段（`upto`）是因为后置条件只在**它自己那一步之后**成立：
    「按字符数排序」之后再截断，最终输出当然不再有序，那不是题面不自洽。
    """
    out = [str(x) for x in rows]
    for rule in spec["rules"][:upto if upto is not None else len(spec["rules"])]:
        out = _OPS[rule["op"]](rule)["apply"](out)
    return out


def run_source(src: str, fn: str, rows: list[str]) -> list[str]:
    ns: dict[str, Any] = {}
    exec(compile(src, "<oracle>", "exec"), ns)                  # noqa: S102
    return list(ns[fn](list(rows)))


def tests_of(spec: Spec, cases: list[list[str]]) -> list[str]:
    """测试期望由 oracle 生成 ⇒ 「oracle 得 1.0」是恒真的，别把它当证据（见模块头）。"""
    fn = spec["fn"]
    src = compile_oracle(spec)
    return [f"assert {fn}({case!r}) == {run_source(src, fn, case)!r}"
            for case in cases]


# ===========================================================================
# 准入：四条证据，缺一条就拒绝
# ===========================================================================


def admit(spec: Spec, cases: list[list[str]]) -> list[str]:
    """返回失败清单；空 = 这道题合格。⛔ 不许"警告"绕过。"""
    fails: list[str] = []
    rid = spec.get("id", "?")

    # ── A0 结构：op 必须在这张表里，参数类型必须对 ────────────────────────
    if not spec.get("rules"):
        fails.append(f"{rid} A0 没有规则")
    ops = [r.get("op") for r in spec.get("rules", [])]
    for op in ops:
        if op not in _OPS:
            fails.append(f"{rid} A0 未知 op {op!r}")
    # ⛔ 结构禁令：`lower` 与 `dedupe` 不许同时出现。
    #    转小写之后，「比较时是否忽略大小写」这个参数**永远不可判定**
    #    （前有 lower ⇒ 键已全小写；后有 lower ⇒ 两种保留选择被压成同一行）
    #    ⇒ 写进题面就是一句无法被用例检验的话。这正是 A5 想抓的那个洞，
    #      只是它在**结构层**就能判出来，不必等到跑用例。
    if "lower" in ops and "dedupe" in ops:
        fails.append(f"{rid} A0 同时用了 lower 与 dedupe："
                     f"「比较是否忽略大小写」在这条流水线上不可判定")
    if fails:
        return fails

    src = compile_oracle(spec)
    nsrc = compile_oracle(spec, naive=True)

    # ── A1 源码必须是合法源码（⛔ 不许把 SyntaxError 当"答错了"）───────────
    for label, s in (("oracle", src), ("naive", nsrc)):
        try:
            compile(s, f"<{label}>", "exec")
        except SyntaxError as e:                                # noqa: PERF203
            fails.append(f"{rid} A1 {label} 不是合法源码：{e}")
    if fails:
        return fails

    # ── A2 两条实现必须都跑得起来 ────────────────────────────────────────
    try:
        outs = [run_source(src, spec["fn"], c) for c in cases]
    except Exception as e:                                      # noqa: BLE001
        return fails + [f"{rid} A2 oracle 执行失败：{type(e).__name__}: {e}"]

    # ── A3 后置条件：题面承诺的，每一步之后都必须成立 ────────────────────
    # 判的是**生成的源码**在"前 k 步"上的输出（不是解释器）。在解释器上判，
    # 等于让另一条实现替它受审 —— 7y 那个洞（题面写 n，oracle 交 n+1）
    # 就是这样被漏掉的：尺子和被量的东西不是同一件。
    for i, rule in enumerate(spec["rules"]):
        pre_src = compile_oracle(spec, upto=i + 1)
        for name, pred in _OPS[rule["op"]](rule)["post"]:
            for case in cases:
                got = run_source(pre_src, spec["fn"], case)
                if not pred(got):
                    fails.append(f"{rid} A3 第{i + 1}步「{name}」在 {case!r} 上不成立"
                                 f"：{got!r}")

    # ── A4 三方对撞：生成源码 vs 独立解释器，逐条用例逐字相同 ─────────────
    for case, got in zip(cases, outs):
        want = apply_pipeline(spec, case)
        if got != want:
            fails.append(f"{rid} A4 源码与解释器不一致：{case!r} ⇒ {got!r} != {want!r}")

    # ── A5 同源双射：改一个参数 ⇒ 题面必变 ∧ 行为必变 ─────────────────────
    # 只查一个方向是不够的：
    #   · 只查"题面变了" ⇒ 参数写进了字面，代码却没用它（题面在说谎）
    #   · 只查"行为变了" ⇒ 代码读了一个题面没写的东西（答案不可从题面推出）
    for rule_i, rule in enumerate(spec["rules"]):
        base_prompt = render_prompt(spec)
        for alt in _OPS[rule["op"]](rule)["alts"]:
            alt_rules = list(spec["rules"])
            alt_rules[rule_i] = alt
            alt_spec = dict(spec, rules=alt_rules)
            label = f"第{rule_i + 1}步 {rule['op']} {_brief(alt)}"
            if render_prompt(alt_spec) == base_prompt:
                fails.append(f"{rid} A5 {label}：改了参数题面不变（题面少写了东西）")
            alt_src = compile_oracle(alt_spec)
            alt_outs = [run_source(alt_src, spec["fn"], c) for c in cases]
            if alt_outs == outs:
                fails.append(f"{rid} A5 {label}：题面变了但行为不变"
                             f"（这个参数在用例上打不出来，或代码没读它）")

    # ── A6 确定性：同一 spec 两次编译 ⇒ 源码逐字相同，输出逐字相同 ─────────
    if compile_oracle(spec) != src:
        fails.append(f"{rid} A6 两次编译源码不同（生成不确定）")
    if [run_source(src, spec["fn"], c) for c in cases] != outs:
        fails.append(f"{rid} A6 两次执行结果不同（题不是函数）")

    return fails


def _brief(rule: Rule) -> str:
    parts = [f"{k}={v!r}" for k, v in sorted(rule.items()) if k != "op"]
    return "(" + ", ".join(parts) + ")" if parts else "()"


def naive_hits(spec: Spec, cases: list[list[str]]) -> tuple[int, int]:
    """常识实现在这几条用例上得了几分（严格居中 ⇒ 常识不够用）。

    ⛔ 期望值从 oracle 现算，**不**去解析 `tests_of()` 生成的字符串 ——
       解析断言文本是"量具自己造一个新解析器"，那正是本项目反复踩的坑。
    """
    want = [run_source(compile_oracle(spec), spec["fn"], c) for c in cases]
    ns: dict[str, Any] = {}
    exec(compile(compile_oracle(spec, naive=True), "<naive>", "exec"), ns)  # noqa: S102
    hit = 0
    for case, exp in zip(cases, want):
        try:
            hit += int(list(ns[spec["fn"]](list(case))) == exp)
        except Exception:                                       # noqa: BLE001
            pass
    return hit, len(cases)


# ===========================================================================
# 检测器自带反证（纪律 ⑩）
#
# 一个"从来只输出 ✅"的准入器和没有准入器**效果一样**，而它看起来更让人放心 ——
# 那更糟。所以四条证据各配一个坏样本，**就地**造出来、跑完立刻还原。
#
# 放在本文件里而不是探针里：契约测试也必须能在**没有探针**的情况下独立成立
# （两处各写一份坏样本，迟早会长成两份判据）。
# ===========================================================================


@contextlib.contextmanager
def _with_op(op: str, **over: Any):
    """临时替换某个 op 的生成结果。覆盖值可以是常量，(rule, 现结果, 原生成器) -> 新值。"""
    orig = _OPS[op]

    def bad(rule: Rule, naive: bool = False) -> dict[str, Any]:
        d = dict(orig(rule, naive))
        for k, v in over.items():
            # ⛔ 必须把**原生成器**传进去：否则覆盖函数里再调 `_OPS[op]`
            #    拿到的是被覆盖后的自己 —— 实测第一次跑就死在无限递归上。
            d[k] = v(rule, d, orig) if callable(v) else v
        return d

    _OPS[op] = bad
    try:
        yield
    finally:
        _OPS[op] = orig


def reverse_proofs(spec: Spec, cases: list[list[str]]) -> dict[str, list[str]]:
    """四条证据的坏样本各跑一遍，返回各自的失败清单（空清单 = 该证据是死的）。

    ⚠️ 传进来的 spec 必须同时含 `prefix` + `dedupe(fold=True, keep=last)` + `truncate`，
       否则对应的坏样本根本无处附着（实测拿 sg01 跑，A4/A5 两项直接是空清单 ——
       那不是"通过"，是"没测"）。现用 `sg05`。
    """
    out: dict[str, list[str]] = {}

    # A0：把「转小写」和「去重」塞进同一道题
    out["A0"] = admit(dict(spec, id="rp_a0",
                           rules=[{"op": "lower"},
                                  {"op": "dedupe", "keep": "first", "fold": True}]),
                      cases)

    # A3：题面承诺截断到 n，源码给 n+1 —— 这就是 7y 的实形
    with _with_op("truncate", code=lambda r, d, _o: d["code"].replace(
            f"[:{r['n']}]", f"[:{r['n'] + 1}]")):
        out["A3"] = admit(spec, cases)

    # A4：源码与解释器不一致（多打一次前缀），但**后置条件仍然成立**
    with _with_op("prefix", code=lambda r, d, _o: d["code"].replace(
            f"[{r['text']!r} + _x", f"[{r['text']!r} * 2 + _x")):
        out["A4"] = admit(spec, cases)

    # A5 方向一：题面漏写了参数
    with _with_op("prefix", render="每行前面加上一点东西"):
        out["A5题面"] = admit(spec, cases)

    # A5 方向二：代码不读参数（键仍然唯一，后置条件照样过）
    with _with_op("dedupe",
                  code=lambda r, d, o: o(dict(r, keep="first"))["code"]):
        out["A5代码"] = admit(spec, cases)

    return out


# ===========================================================================
# 按种子铸新题（"无限题集"那句话的落点：新题必须过同一条准入）
# ===========================================================================


def mint(seed: int, spec_id: str) -> Spec:
    """按种子铸一道新题。确定性：同 seed 同 id ⇒ 逐字相同的 spec。"""
    rng = random.Random(f"{seed}:{spec_id}")
    ops: list[Rule] = [{"op": "strip"}, {"op": "collapse"}]
    if rng.random() < 0.5:
        ops.append({"op": "lower"})
    # ⛔ 「转小写」与「折叠去重」在**任意顺序**下都互斥：转小写在前 ⇒ 键已全小写，
    #    折叠与精确比较等价；转小写在后 ⇒ 两种保留选择都被压成同一行。
    #    两种情况下 `fold` 都不可观测 ⇒ A5 当场拒绝（实测铸出来的题就这么红的）。
    _has_lower = any(o["op"] == "lower" for o in ops)
    if (not _has_lower) and rng.random() < 0.7:
        ops.append({"op": "dedupe", "keep": rng.choice(["first", "last"]),
                    "fold": rng.random() < 0.6})
    if rng.random() < 0.7:
        ops.append({"op": "sort", "key": rng.choice(["text", "len"]),
                    "desc": rng.random() < 0.5})
    if rng.random() < 0.6:
        ops.append({"op": "prefix", "text": rng.choice(["> ", "#", "[v2] ", "· "])})
    ops.append({"op": "truncate", "n": rng.choice([6, 7, 8, 9, 10])})
    rng.shuffle(ops)
    return {"id": spec_id, "fn": f"norm_{spec_id}", "rules": ops}
