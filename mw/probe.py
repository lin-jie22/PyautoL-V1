"""弱点驱动的自出题：把「输入域太窄」这句诊断，翻成一组新输入（V190，§258）。

## 这个模块补的是哪一步

`critgen.constant_on()` 能说出「这一格没有证据」，但说不出**怎么补**。
`critgen.reachable_on()` 能说出「这组输入喂得进去吗」，但喂得进去不等于**行使到实现**
（V190 §2.2 的实证：空容器让循环体一次不进，于是 `AttributeError` 也不发生 ⇒ 误报可达）。

本模块补的是「补」：给定签名，**造出更多输入**。

## 铁律：生成器看不见实现

`expand()` / `structural()` / `tokens()` 的签名里**没有** `oracle`、也没有 `fn`。
⛔ 否则「池退化已修复」就是重言式 —— 先挑出会让输出变化的输入，再宣布它变了。
⛔ 也不许在这里做「试几条、留下变了的」这种筛选：那是把测量结果当生成依据。

唯一读实现的是 `literals(src)`：只从**源码文本**取字符串字面量，⛔ 不跑实现；
产物一律标 `LEAK`，只用于量「盲区里有多少是词表」，⛔ 不许进可部署臂。
"""
from __future__ import annotations

import ast
import hashlib
import re
from typing import Iterable

#: 词元长度上限。中文整句会被切成碎片或直接丢掉 —— 预注册 F3 钉着这条。
MAX_TOKEN = 24

#: 噪声臂的字母表（固定，⛔ 不许掺进有意义的词）。
_NOISE_ALPHA = "abcdefghijklmnopqrstuvwxyz0123456789_-."

_QUOTED = re.compile(r"'([^']*)'|\"([^\"]*)\"|`([^`]*)`")
_ASCII = re.compile(r"[A-Za-z0-9_]+")
_SLASHED = re.compile(r"[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)+")


def tokens(text: str, limit: int = MAX_TOKEN) -> list[str]:
    """从一段文本里取候选词元：引号内 → 斜杠组拆开 → 裸 ASCII 词。

    三条来源都在**同一段文本**里，⛔ 不读实现、⛔ 不读判分。
    ⛔ 不产出长度 > `limit` 的串（把整句中文当一个词元会让池里多一条没人吃得下的垃圾，
       预注册 F3）；⛔ 不产出空串。
    """
    out: list[str] = []
    seen: set[str] = set()

    def push(v: str) -> None:
        v = (v or "").strip()
        #: 纯数字/带符号的短串也算（`1` 在 `parse_flag` 的词表里）。
        if not v or len(v) > limit or v in seen:
            return
        if not re.fullmatch(r"[\x20-\x7e]+", v):
            return
        seen.add(v)
        out.append(v)

    for m in _QUOTED.finditer(text or ""):
        push(next(g for g in m.groups() if g is not None))
    for m in _SLASHED.finditer(text or ""):
        for part in m.group(0).split("/"):
            push(part)
    for m in _ASCII.finditer(text or ""):
        push(m.group(0))
    return out


def literals(src: str) -> list[str]:
    """⚠️ **泄漏入口**：从实现源码取非空字符串字面量（只做 AST，⛔ 不执行）。

    它存在的唯一理由是量一个上限：**若词表直接给出，盲区还剩多少**。
    调用方拿到的东西一律标 `LEAK`（`expand()` 里钉死），⛔ 不许进可部署臂。
    """
    try:
        tree = ast.parse(src or "")
    except SyntaxError:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if v and len(v) <= MAX_TOKEN and v not in seen:
                seen.add(v)
                out.append(v)
    return out


# ── 结构变异：每个类型一套，⛔ 不看实现 ────────────────────────────────

def _mutate_str(v: str) -> list[str]:
    out = [v.upper(), v.lower(), v.strip(), " " + v + " ", v.swapcase()]
    out += [v + "x", v[:-1] if v else "x", (v * 2)[:MAX_TOKEN]]
    #: 分隔符与首尾：这两族在 V187 已被量过是「换池就换判定」的来源之一。
    out += [v.replace(";", ","), v.replace(",", ";"), v.replace(" ", "_"),
            " " + v, v + " ", "\t" + v]
    return out


def _mutate_int(v: int) -> list[int]:
    return [v + 1, v - 1, -v, v * 2, v * 10, 0, 1]


def _mutate_num(v: float) -> list[float]:
    return [v + 0.5, -v, v * 2, v + 1, round(v, 2), 0.5, -0.5]


def _mutate_dict(v: dict) -> list[dict]:
    out = [dict(v)]
    out.append({**v, "_z": 0})                       # 多一个键
    out.append({k: v[k] for k in list(v)[:1]})       # 只留第一个键
    out.append({"nest": {k: v[k] for k in list(v)[:1]}} if v else {"nest": {}})
    out.append(dict(reversed(list(v.items()))))      # 键序反转
    return out


def _mutate_list(v: list) -> list[list]:
    out = [list(v)]
    out.append(list(reversed(v)))
    out.append(list(v) + list(v[:1]))                # 尾部重复
    out.append([str(x).upper() for x in v])
    out.append([str(x).strip().lower() for x in v])
    out.append(sorted(v, key=str))
    return out


_MUTATORS = {"str": _mutate_str, "flag": _mutate_str, "int": _mutate_int,
             "num": _mutate_num, "dict": _mutate_dict, "list": _mutate_list}


def structural(types: Iterable[str], base: dict | None = None) -> dict[str, tuple]:
    """按类型把池变异一遍（去重保序，原值排在最前）。⛔ 不看实现。"""
    from mw.critgen import POOL

    src = base or POOL
    out: dict[str, tuple] = {}
    for t in types:
        vals = list(src.get(t) or ())
        mut = _MUTATORS.get(t)
        if mut:
            for v in list(vals):
                for w in mut(v):
                    if w not in vals:
                        vals.append(w)
        out[t] = tuple(vals)
    return out


def _rng(seed: int, salt: str):
    """确定性伪随机：`hashlib` 派生初值 + 自写 LCG。

    ⛔ 不用 `random`：① 门禁的依赖白名单里没有它（本仓库零第三方、标准库也走白名单）；
       ② 噪声臂的读数必须**跨进程可复现** —— `random.Random(str)` 虽然也确定性，
          但把「可复现」这件事交给一个不在白名单里的模块，等于给门禁开了个洞。
    """
    h = hashlib.sha256(f"{seed}:{salt}".encode()).digest()
    state = int.from_bytes(h[:8], "big") | 1

    def nxt(n: int) -> int:
        nonlocal state
        state = (state * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        return (state >> 33) % max(1, n)

    return nxt


def _noise(t: str, n: int, seed: int) -> list:
    """同尺寸随机噪声（对照臂）：类型对、数量对、内容无结构。"""
    nxt = _rng(seed, t)
    out = []
    for _ in range(max(0, n)):
        if t in ("str", "flag"):
            k = nxt(7)
            out.append("".join(_NOISE_ALPHA[nxt(len(_NOISE_ALPHA))] for _ in range(k)))
        elif t == "int":
            out.append(nxt(200) - 100)
        elif t == "num":
            out.append(round((nxt(2_000_000) - 1_000_000) / 100.0, 3))
        elif t == "list":
            k = nxt(4)
            out.append(["".join(_NOISE_ALPHA[nxt(len(_NOISE_ALPHA))] for _ in range(3))
                        for _ in range(k)])
        elif t == "dict":
            k = nxt(3)
            out.append({"".join(_NOISE_ALPHA[nxt(len(_NOISE_ALPHA))] for _ in range(2)):
                        nxt(19) - 9 for _ in range(k)})
        else:
            out.append("")
    return out


def expand(kind: str, types: Iterable[str], *, sources: tuple[str, ...] = ("mutate",),
           texts: Iterable[str] = (), noise_budget: int = 0, seed: int = 190,
           oracle_src: str = "") -> dict:
    """造池。`sources` 是**加性**的：`("mutate","text")` = 两者都加。

    返回 `{"pools": {type: tuple}, "prov": [...], "leak": bool, "sizes": {...}}`。
    ⚠️ `prov` 逐条记录「这条值从哪个源来」—— 结论里要按源报命中，⛔ 不许只报总数。
    ⚠️ `oracle_src` 只在 `sources` 含 `"literal"` 时被读，且产物一律 `LEAK`。
    """
    from mw.critgen import POOL

    types = list(types)
    pools = {t: list(POOL.get(t) or ()) for t in types}
    prov: list[dict] = []
    leak = False

    def add(t: str, vals: Iterable, source: str) -> None:
        nonlocal leak
        if t not in pools:
            return
        #: ⚠️ 先落成 list：`vals` 可能是生成器，`for` 迭代一遍就空了，
        #:    后面那句 `vals` 判空会**永远为假** ⇒ 泄漏旗标静默不置位（本轮自踩 4）。
        vals = list(vals)
        for v in vals:
            if v in pools[t]:
                continue
            pools[t].append(v)
            prov.append({"type": t, "value": repr(v), "source": source})
        if source == "literal" and vals:
            leak = True

    if "mutate" in sources:
        mut = structural(types)
        for t in types:
            add(t, [v for v in mut[t] if v not in POOL.get(t, ())], "mutate")
    if "text" in sources:
        toks = [w for txt in texts for w in tokens(txt)]
        for t in types:
            if t in ("str", "flag"):
                add(t, toks, "text")
    if "literal" in sources:
        lits = literals(oracle_src)
        for t in types:
            if t in ("str", "flag"):
                add(t, lits, "literal")
    if "noise" in sources:
        for t in types:
            add(t, _noise(t, noise_budget, seed), "noise")

    return {"pools": {t: tuple(v) for t, v in pools.items()},
            "prov": prov, "leak": leak,
            "sizes": {t: len(v) for t, v in pools.items()},
            "base_sizes": {t: len(POOL.get(t) or ()) for t in types}}


def constants(rows: Iterable[dict], *, sig: str = "CONST") -> list[dict]:
    """从读数里挑出弱点格（默认 `sig == "CONST"`）。系统**自己发现**弱点就是这一步。

    ⛔ 不在这里判「该用哪个源」：那要看结果才知道，属于选择后效（会变成重言式）。
    """
    return [r for r in rows if r.get("sig") == sig]
