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


# ══════════════════════════════════════════════════════════════════════════
# V191（§259）：把「池退化」拆成病名 —— 诊断会不会误诊
#
# V190 的结论是「5 格缺词表、12 格签名错」，并打算用「签名级重选」吃掉后 12 格。
# 本节先检验那个二分本身：它可能**不是两个病**，而是同一个病的两种外观。
#
# ## 铁律（与 V190 那条并列，方向相反）
#
# `expand()` / `structural()` / `tokens()` 看不见实现（`oracle` 不在参数表里）。
# `route()` / `varies()` **必须**看得见实现 —— 它要回答「这个签名有没有域、有没有产出差异」。
# 所以边界换成另一条：**参数表里不许出现任何正确性信号**
# （`pass_oracle` / `correct` / `expected` / `want`）。⛔ 一旦能读判分，
# 「该换哪个签名」就变成拿尺子挑尺子 —— 那正是 V189 `read_signal()` 里那条注释的意思。
#
# ## 为什么这条路是「免费」的
#
# 纪律 66：可达性免费（问「吃得下吗」），有效性收费（问「答得对吗」）。
# 本节全程只花免费那一半，所以它能**免费修签名**；也正因为免费，
# 它不携带任何关于「判据好不好」的新信息 —— 见 `RESULTS_v191.md` §3 的 collapse 账。
# ══════════════════════════════════════════════════════════════════════════

#: 路由结论（顺序即判据优先级）。三个守卫值不是病名，见 `route()`。
DISEASES: tuple[str, ...] = ("RESIGN_FREE", "RESIGN_VOCAB", "NEEDS_VOCAB", "DEAD")
GUARDS: tuple[str, ...] = ("HEALTHY", "NO_DOMAIN", "FAULT")

#: 二元算子里「第二参数必须是长度」的两个（与 `critgen._inputs_for()` 同一口径）。
_LEN_SECOND = ("TAIL", "BOUND")


def _types() -> tuple[str, ...]:
    """`critgen.TYPES` 的延迟取用，本模块一贯的写法（`structural`/`expand` 也都是函数内 import）。

    ⚠️ 如实记两件事，⛔ 不许把这段写成「踩过坑才改的」：
       ① **本轮没有踩到**循环 import —— 已经查过，`mw/critgen.py` 没有 `import mw.probe`；
       ② 写成延迟的真实理由只有一条：`probe` 这一层故意**不依赖** `critgen` 的模块级状态，
          这样 `probe` 被单独 import 时不会把 `critgen` 的 import 期副作用一起带进来。
    """
    from mw.critgen import TYPES as _T
    return _T


def sig_space(kind: str, n: int) -> list[list[str]]:
    """该 (算子, arity) 下的**可行签名**，按 `TYPES` 的**声明序**。

    ⚠️ 声明序是判据的一部分：`route()` 取「第一个非退化」的，若改成按探测结果排序
       （比如「谁先过用谁」），选目标就变成拿尺子挑尺子（F4）。
    ⚠️ 规则与 `bench/evidence/l3_s277/space_v189.py:type_combos()` 逐条相同 ——
       那边是**已冻结**的证据脚本，这边是产品。两处必须一致，`AZ154a` 钉着。
    """
    T = list(_types())
    if n == 1:
        return [[t] for t in T]
    if kind in _LEN_SECOND:
        return [[t, "int"] for t in T]
    if kind == "EMPTY_IFF_EQUAL":
        return [[t, t] for t in T]
    return [[a, b] for a in T for b in T]


def _is_degenerate(v) -> bool:
    """空串 / 空容器 / `0` / `0.0` —— 这四类能让循环体一次不进（V190 §2.2）。

    ⚠️ 纯空白的串（`"  "`）**不算**退化：它是合法的、会被 `strip()` 之后有意义的输入。
    ⚠️ `False` 不算：它是 `int` 的子类但语义是布尔，剔掉它会误伤 `flag` 域。
    """
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)) and v == 0:
        return True
    return isinstance(v, (str, list, tuple, dict, set)) and len(v) == 0


def degenerate_free(pool: dict, types: Iterable[str]) -> dict[str, tuple]:
    """剔掉退化输入后的池。只保留 `types` 里的键。"""
    return {t: tuple(v for v in (pool.get(t) or ()) if not _is_degenerate(v))
            for t in types}


def domain_of(kind: str, types: list, oracle: str, fn: str, *,
              pool: dict | None = None) -> tuple[str, bool | None]:
    """`("OK", 输出是否不恒定)` / `("NO_DOMAIN", None)` / `("FAULT", None)`。

    ⛔ 「无域」与「恒定」必须分开：前者是「这个签名问不出问题」，后者是
       「这个签名问得出问题但实现不区分」—— 两者修法相反（V190 §2.2 的 `k01` 就是前者
       被空容器伪装成后者）。
    """
    from mw.critgen import inputs_for, legal_indexes, constant_on

    try:
        ins = inputs_for(kind, list(types or []), pool)
    except Exception:
        return "FAULT", None
    if not ins:
        return "NO_DOMAIN", None
    legal = legal_indexes(oracle, fn, ins)
    if legal is None:
        return "FAULT", None
    if not legal:
        return "NO_DOMAIN", None
    c = constant_on(oracle, fn, [ins[i] for i in legal])
    if c is None:
        return "FAULT", None
    return "OK", (not c)


def varies(kind: str, types: list, oracle: str, fn: str, *,
           pool: dict | None = None) -> bool | None:
    """该签名在这组池上「输出是不是不恒定」。`None` = 无域 / 探测失败。

    ⛔ 只回答「有没有产出差异」，⛔ 不问「答得对不对」（与 `constant_on()` 同一条边界）。
    """
    st, v = domain_of(kind, list(types or []), oracle, fn, pool=pool)
    return v if st == "OK" else None


def false_reach(kind: str, types: list, oracle: str, fn: str) -> bool:
    """「可达」是不是被**退化输入**骗出来的（V190 §2.2 那条，升格成产品函数）。

    `reachable_on()` 答的是「这组池上喂得进吗」。空容器让循环体一次不进 ⇒
    `AttributeError` 也不发生 ⇒ 它报「可达」，而其实这个签名在**非退化**输入上一个都吃不下。
    ⇒ ⛔ `reachable_on()` 答不了「这个签名有没有域」。本函数把那个缺口单独标出来。

    ⚠️ 判据是「剔掉退化输入后 legal 为空」。⛔ 探测失败一律返回 `False`（不排除），
       与 `reachable_on()` 的 fail-open 同口径。
    """
    from mw.critgen import POOL, inputs_for, legal_indexes, reachable_on

    if not reachable_on(kind, list(types or []), oracle, fn):
        return False
    try:
        ins = inputs_for(kind, list(types or []), degenerate_free(POOL, types))
    except Exception:
        return False
    if not ins:
        return False
    legal = legal_indexes(oracle, fn, ins)
    return legal is not None and not legal


def route(kind: str, types: list, n: int, oracle: str, fn: str, *,
          texts: Iterable[str] = (), noise_budget: int = 0) -> dict:
    """**一格 `UNDECIDED` → 下一步动作**。这是 V191 的本体（§259）。

    返回 `{"disease", "target", "cost", "probes", "varied_sigs", "no_domain"}`。

    ## 为什么先换签名、后换词表

    V190 把 17 个盲区分成「5 格缺词表 + 12 格签名错」。若 `parse_flag` 这类实现的第一句是
    `str(s)`，那它**任何类型都收**、基池里的 `0` 在字符串化之后正好落在词表里 ⇒
    「缺词表」只是「类型错」的外观。本轮就是量这件事：先问「有没有免费签名」，
    有就不必造词（`RESIGN_FREE`）；没有才允许扩池（`RESIGN_VOCAB` / `NEEDS_VOCAB`）。

    ## 三个守卫值

    - `HEALTHY`：当前签名本来就不恒定 ⇒ 这格不该被路由（调用方给错了格）。
    - `NO_DOMAIN` / `FAULT`：当前签名连域都没有，或探测失败。⛔ 都不算 `DEAD`（F1）。
    """
    cur = [str(x) for x in (types or [])]

    st, v = domain_of(kind, cur, oracle, fn)
    if st == "FAULT":
        return {"disease": "FAULT", "target": None, "cost": None, "probes": 1,
                "varied_sigs": [], "no_domain": []}
    if st == "NO_DOMAIN":
        return {"disease": "NO_DOMAIN", "target": None, "cost": None, "probes": 1,
                "varied_sigs": [], "no_domain": cur}
    if v:
        return {"disease": "HEALTHY", "target": None, "cost": None, "probes": 1,
                "varied_sigs": [], "no_domain": []}

    probes = 1
    varied: list[list[str]] = []
    no_domain: list[list[str]] = []
    for s in sig_space(kind, n):
        if s == cur:
            continue
        st, v = domain_of(kind, s, oracle, fn)
        probes += 1
        if st == "OK" and v:
            return {"disease": "RESIGN_FREE", "target": list(s), "cost": "free",
                    "probes": probes, "varied_sigs": varied + [list(s)],
                    "no_domain": no_domain}
        if st == "NO_DOMAIN":
            no_domain.append(list(s))

    #: 免费签名一个都没有 ⇒ 才允许动池。扩池的源只用**可部署**的两个（⛔ 不含 literal）。
    exp = expand(kind, cur, sources=("mutate", "text", "noise"),
                 texts=tuple(texts or ()), noise_budget=noise_budget)["pools"]
    st, v = domain_of(kind, cur, oracle, fn, pool=exp)
    probes += 1
    if st == "OK" and v:
        return {"disease": "NEEDS_VOCAB", "target": None, "cost": "vocab",
                "probes": probes, "varied_sigs": varied, "no_domain": no_domain}

    for s in sig_space(kind, n):
        if s == cur:
            continue
        #: ⚠️ 扩池必须按**目标签名自己的类型**重建：`_inputs_for()` 只读 `types` 里的键，
        #:    拿 `cur` 造出来的池喂给别的签名会 `KeyError`（V190 那批格只有 str 池，掩盖了这点）。
        exp_s = expand(kind, s, sources=("mutate", "text", "noise"),
                       texts=tuple(texts or ()), noise_budget=noise_budget)["pools"]
        st, v = domain_of(kind, s, oracle, fn, pool=exp_s)
        probes += 1
        if st == "OK" and v:
            return {"disease": "RESIGN_VOCAB", "target": list(s), "cost": "vocab",
                    "probes": probes, "varied_sigs": varied + [list(s)],
                    "no_domain": no_domain}

    return {"disease": "DEAD", "target": None, "cost": None, "probes": probes,
            "varied_sigs": varied, "no_domain": no_domain}
