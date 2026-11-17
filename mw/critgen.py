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
    # ── V187 新增两个域。⛔ 它们只回答「喂什么」，⛔ 不回答「该返回什么」。 ──
    # `num`：必须跨过三位一组的**边界**（999 < 1000 ≤ 1234.5），否则千分位槽在
    #        判据里是**平坦**的 —— 小值池上 `sep="space"` 与 `sep="comma"` 产出同一个串，
    #        那样「槽选对了」这件事根本不可能被看见（纪律 51）。
    #        必须含负数与小数：负号写法槽同理。
    "num": (0, 7, 1234.5, -1234.5, 1234567.891, -0.5),
    # `flag`：短小的配置字面量 —— 大小写混合、带首尾空白、单字符、无法识别的串。
    #         ⛔ 池里没有一处写着「哪个算真」。
    "flag": ("y", " YES ", "0", "on", "  ", "xyz", "1", "OFF"),
}

#: 长度池（nat⁺）。⛔ 不含 0：`s[-0:]` 是整串，`n=0` 会让 TAIL 自相矛盾 ——
#: 那是装置坑，不是模型错（V183 的 `TAIL` 就踩过这个边）。
LEN_POOL: tuple[int, ...] = (1, 3, 9)

TYPES: tuple[str, ...] = ("str", "int", "dict", "list", "num", "flag")

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

#: `NUMFMT`（V187）的三个规格槽。⛔ 表里没有任何一项被标成「正确」——
#: 正确答案在**惯例**里（记忆），不在装置里。
SEPS: dict[str, str] = {"": "", "space": " ", "comma": ",", "us": "_"}
DECS: dict[str, int] = {"": -1, "0": 0, "1": 1, "2": 2, "3": 3}
NEGS: dict[str, str] = {"": "", "paren": "paren", "minus": "minus"}

#: 槽 key → 该槽的枚举表。V187 起槽不止两张表，用分发表取代「decode 还是不 decode」的三元判断。
SLOT_TABLES: dict[str, dict] = {
    "decode": DECODERS, "keep": KEEPS,
    "sep": SEPS, "dec": DECS, "neg": NEGS,
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
    # ── V187 新增 ────────────────────────────────────────────────────────
    # ⚠️ `NUMFMT` 与上面五个**不同质**：上面是关系，这个是**规格**（装置渲染一份
    #    参数化的参考格式化再逐字比对）。判别力口径要分开报，⛔ 不许混一起当「关系型能力」。
    "NUMFMT": {"arity": 1, "slots": ("sep", "dec", "neg"),
               "what": "输出必须等于按 (sep, dec, neg) 渲染的规范数字串"},
    "INVARIANT": {"arity": 1, "slots": (),
                  "what": "输出在大小写/首尾空白规范化下不变，且不是常量（无槽：关系来自题面）"},
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
    if kind == "EMPTY_IFF_EQUAL" and len(types) == 2 and types[0] != types[1]:
        # V188（§256）：iff 的两端必须可比 —— 异类型时 `a == b` 恒假，
        # 断言 `(got == {}) != (a == b)` 的 ⟸ 支永远走不到 ⇒ 判据**恒真**。
        return (f"{kind} 比较的是两个输入之间的关系，两个输入必须**同类型**；"
                f"拿到的是 {types[0]!r} 与 {types[1]!r}")
    if not reachable(kind, n_args):
        return f"{kind} 需要 {spec['arity']} 个输入，本题函数有 {n_args} 个 ⇒ 结构不可达"
    for key in spec["slots"]:
        table = SLOT_TABLES[key]
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
    if k == "EMPTY_IFF_EQUAL" and len(types) == 2 and types[0] != types[1]:
        # V188（§256）：与 `validate()` 同一条件 —— 两边只允许在「为什么」上不同（`AZ148c`）。
        out["code"] = "TYPES"
        out["msg"] = (f"{k} 比较的是两个输入之间的关系，两个输入必须**同类型**；"
                      f"拿到的是 {types[0]!r} 与 {types[1]!r}")
        return out
    for key in spec["slots"]:
        table = SLOT_TABLES[key]
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


#: 诊断信号（V189，§257）：结构两类 + 语义两类 + 通过。顺序即优先级。
SIGNALS: tuple[str, ...] = ("OK", "STRUCT:TYPES", "STRUCT:OTHER", "CONST", "SEMANTIC")


def read_signal(*, built: bool, fail: str = "", pass_oracle: bool = False,
                bite_stub: bool = False, bite_partial: bool = False,
                oracle_const: bool | None = None) -> str:
    """把**一格读数**折成一个信号，供 `narrow()` 决定下一轮的候选集。

    ## ⛔ 最要紧的边界：这里不许看「哪个算子更容易过」

    装置手里有 oracle，用 `pass_oracle` 反推「该换成谁」就是拿尺子挑尺子 = 答案泄漏。
    本函数只吃两样**不泄漏**的东西：

    1. 结构事实（`built` / `fail`）—— 来自 `diagnose()`，只问「吃得下吗」；
    2. **输出是否恒定**（`oracle_const`）—— 来自 `constant_on()`，只问「有没有产出差异」。

    ⚠️ `pass_oracle`/`bite_stub`/`bite_partial` 只用来区分 `OK` 与 `SEMANTIC`（**是否还要动作**），
       ⛔ 不参与「换成哪个」。两者的区别是本轮设计的全部依据。
    """
    if not built:
        #: ⚠️ `OUT_OF_DOMAIN` 与 `TYPES` 是同一件事（该签名在目标实现上没有域），
        #: 归到 `OTHER` 会让 `narrow()` 的签名分支对最常见的不可达视而不见（PREREG §2.4）。
        return "STRUCT:TYPES" if (fail or "") in ("TYPES", "OUT_OF_DOMAIN") else "STRUCT:OTHER"
    if oracle_const is True:
        return "CONST"
    if pass_oracle and bite_stub and bite_partial:
        return "OK"
    #: `(1,1,0)`（过正确、咬空实现、咬不住部分实现）落在这里，不是 `OK`：
    #: `disc` 的定义就是 `pass_oracle ∧ bite_partial`，算成 OK 会让该题第 2 轮不动作（§2.4）。
    return "SEMANTIC"


def reachable_on(kind: str, types: list, oracle: str, fn: str) -> bool:
    """这个 (算子, 签名) 在**目标实现**上有没有至少一组合法输入。⛔ 不问对错。

    ⚠️ 名字带 `_on` 是必须的：本模块**已有** `reachable(kind, n_args)`（只按 arity 判结构
       可达，`validate()` 在用、`AZ145` 钉着）。初版撞名把它覆盖了 ⇒ `validate()` 报
       `missing 2 required positional arguments`。加函数前没查重名，记一笔（纪律 19 同型）。

    ⚠️ 探测失败（子进程超时/没吐标记）⇒ 返回 `True`（不排除）。与 `ops_for(n_args<0)`
       同一条口径：签名未知或探测失败时**不许假装有约束**（纪律 5：恒真动作 = 假绿）。
    """
    if kind not in OPS or not (oracle or "").strip() or not fn:
        return False
    try:
        ins = _inputs_for(kind, list(types or []))
    except Exception:
        #: 签名里出现了 `TYPES` 之外的名字（模型瞎写）⇒ 生成不出输入 ⇒ 不知道，**不删**。
        #: 与探测失败同一条口径：不确定就不许假装有约束（fail-open，纪律 5）。
        return True
    if not ins:
        return False
    legal = legal_indexes(oracle, fn, ins)
    if legal is None:
        return True
    return bool(legal)


def narrow(signal: str, n_args: int, chosen: str, types: list,
           oracle: str, fn: str, *, scope: str = "one",
           from_ops: dict | None = None) -> tuple[dict, dict]:
    """**诊断信号 → 下一轮的候选集**。这是 V189 的全部改动本体（§257）。

    ## 为什么是这个形状

    V185 已量过「诊断 → 说给模型听 → 重选」：两臂读数**逐字相同**（模型把 `TAIL` 换成
    另一个二元算子 `BOUND`）。⇒ 文本不是约束。V186 的结论是「**选不到**才算约束」。

    所以「L2（对失败成因的判断）→ L5（下一轮的可选域）」通电的唯一形态是：
    **诊断的输出直接进候选集**，而不是进 prompt 的措辞。

    ## `scope` 就是 B/C 两臂的唯一差别

    - `scope="one"`：只删 `chosen` —— **不看信号**。这是对照臂：它证明「多跑一轮」本身
      不产生变化（`F4`）。
    - `scope="class"`：按 `signal` 决定删除**范围**（见 §2.1 表）。

    ⇒ 两臂共用同一个函数、同一份删后重建逻辑，差别只有一个参数（纪律 65）。

    ⚠️ `scope="class"` 下 `STRUCT:TYPES` 可能把候选集删光（该签名根本不可达）⇒ 退回
       「只删 `chosen`」并标 `ALL_UNREACHABLE_SIGNATURE`。⛔ 不许返回空集当「收敛」：
       空集是「这题无解」，不是「已经选对」（纪律 67：分母不许混进结构不可达成员）。
    """
    if scope not in ("one", "class"):
        raise ValueError(f"scope 只能是 one/class，拿到 {scope!r}")
    #: ⚠️ **必须**能从上一轮的结果继续删。初版每次都从 `ops_for(n)` 重来 ⇒ 第二轮删掉的
    #: 算子在第三轮又回到候选集（实测 `k02` 的序列 `INVARIANT → CONSERVE → INVARIANT`），
    #: 「收窄」变成「抖动」。⛔ 收窄必须是**单调不增**的，否则它不叫收窄。
    cand = dict(from_ops) if from_ops is not None else ops_for(n_args)
    why = {"signal": signal, "scope": scope, "removed": [], "reason": "", "probes": 0,
           "cand_before": len(cand)}
    if not cand:
        why["reason"] = "NO_CANDIDATE_AT_ARITY"
        return {}, why

    if scope == "one":
        if chosen in cand:
            why["removed"] = [chosen]
            why["reason"] = "SINGLE"
        else:
            why["reason"] = "CHOSEN_NOT_IN_CANDIDATE"
        out = {k: v for k, v in cand.items() if k not in why["removed"]}
        why["cand_after"] = len(out)
        return out, why

    if signal == "OK":
        why["reason"] = "NO_ACTION"
        why["cand_after"] = len(cand)
        return dict(cand), why

    removed: list[str] = []
    #: ⚠️ 签名本身不合法（长度不对 / 有 `TYPES` 外的名字）⇒ `STRUCT:TYPES` 的「按类删」
    #: 无从下手（要按签名测可达，而签名是坏的）⇒ 退回只删 `chosen`，并标 `BAD_SIGNATURE`。
    #: ⛔ 不许拿坏签名去 `_inputs_for()` 里碰运气 —— 那会把装置异常读成「不可达」。
    _st = [str(x) for x in (types or [])]
    _types_ok = bool(_st) and len(_st) == max(n_args, 0) and all(x in TYPES for x in _st)
    if signal == "STRUCT:TYPES" and not _types_ok:
        why["reason"] = "BAD_SIGNATURE"
        removed = [chosen] if chosen in cand else []
    elif signal == "STRUCT:TYPES":
        for k in cand:
            why["probes"] += 1
            if not reachable_on(k, list(types or []), oracle, fn):
                removed.append(k)
        if len(removed) >= len(cand):
            why["reason"] = "ALL_UNREACHABLE_SIGNATURE"
            removed = [chosen] if chosen in cand else []
        else:
            why["reason"] = "SIGNATURE"
    else:
        #: `STRUCT:OTHER` / `CONST` / `SEMANTIC`：没有不泄漏的信息说该换成谁
        #: ⇒ 只能删掉**已被否证的那一个**（`CONST` 的「同签名下恒定者」已降级，见 PREREG §2.1）。
        why["reason"] = "SINGLE"
        removed = [chosen] if chosen in cand else []
    why["removed"] = removed
    out = {k: v for k, v in cand.items() if k not in removed}
    why["cand_after"] = len(out)
    return out, why


def _inputs_for(kind: str, types: list, pool: dict | None = None) -> list:
    """按类型生成输入（PREREG §2.2 的三条规则，逐字实现）。

    `pool` 非 None ⇒ 用外部池（V190 的自出题臂）；`None` ⇒ 模块级 `POOL`。
    ⚠️ 必须用 `is not None` 判断：`{}` 是有效输入（一个值都没有），
       ⛔ 不许被读成「没给」而回退全池（V189 `AZ152e` 同一族）。

    ⚠️ 单参数时元素**就是值本身**（不是 1-tuple）：判据里 `for x in INPUTS: fn(x)`。
       V184 自踩：初版写成 `[(v,) for v in ...]` ⇒ 把 tuple 塞进 `dedupe_keep`，
       8 题一起报 `'tuple' object has no attribute ...` —— 那是装置焊错了，不是能力。
    """
    P = POOL if pool is None else pool
    if len(types) == 1:
        return [(v,) for v in P[types[0]]]
    if kind in ("TAIL", "BOUND"):
        p = P[types[0]]
        if types[0] == "str":
            return [(p[0], LEN_POOL[0]), (p[0], LEN_POOL[2]),
                    (p[2], LEN_POOL[1]), (p[1], LEN_POOL[0])]
        return [(p[0], LEN_POOL[0]), (p[0], LEN_POOL[2]), (p[1], LEN_POOL[0])]
    a, b = P[types[0]], P[types[1]]
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

#: `NUMFMT` 的**参数化**参考渲染（V187）。⚠️ 这里没有一处写着「空格 / 2 / 括号」是答案 ——
#: 它们是形参。答案在惯例里，由槽值送进来（`SEPS`/`DECS`/`NEGS` 三张表都含多项）。
_NUMFMT = '''def _numfmt(x, sep, dec, neg):
    s = "%.*f" % (dec, abs(float(x)))
    ip, _, fp = s.partition(".")
    g = []
    while len(ip) > 3:
        g.insert(0, ip[-3:])
        ip = ip[:-3]
    g.insert(0, ip)
    body = sep.join(g) + ("." + fp if dec else "")
    if float(x) < 0:
        return ("(" + body + ")") if neg == "paren" else ("-" + body)
    return body
'''

#: `INVARIANT` 的规范化变换（无槽 ⇒ 全部五种都要过）。
_NORMS: dict[str, str] = {
    "upper": "s.upper()", "lower": "s.lower()", "strip": "s.strip()",
    "strip_upper": "s.strip().upper()", "strip_lower": "s.strip().lower()",
}
_NORM = ("def _norm_v187(s, how):\n"
         "    s = str(s)\n"
         "    return {" + ", ".join(f"{k!r}: {v}" for k, v in _NORMS.items()) + "}[how]\n")


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


def inputs_for(kind: str, types: list, pool: dict | None = None) -> list:
    """全池输入（未圈域）。臂先拿它，再用 `legal_indexes()` 收敛后回填 `build()`。

    `pool` 给了 ⇒ 用外部池（V190 自出题）；没给 ⇒ `POOL`。⚠️ 空 dict 不算「没给」。
    """
    return _inputs_for(kind, types, pool)


def constant_on(code: str, fn: str, inputs: list) -> bool | None:
    """在**目标实现**上跑一遍池，回答「输出是不是全同」。`None` = 探测失败。

    ## 为什么需要它（V188，§256）

    V187 C 臂在 `g7e1` 上 `INVARIANT` 判红：模型选了 `str` 池，而 `parse_flag` 对
    `"abcdef"` / `""` / `"  a b  "` 一律返回 `None` ⇒ 判据的非退化检查失败。
    **正确实现被判红**，可读数里那一格看起来和「判据写错」一模一样。

    装置能分辨的只有一件事：**这个实现在这组输入上有没有产出差异**。
    没有 ⇒ 这一格**不构成关于判据的证据**（`UNDECIDED`），而不是「判据错」。

    ⛔ 边界与 `legal_indexes()` 相同：只回答「恒定吗」，⛔ 不问「对不对」。
       正确与否仍是收费那一半（`mw.verify`）。
    ⛔ 不可分辨的已知情形：常量实现（stub）与「正确实现落在池外」在装置眼里同一个现象
       （输出全同）⇒ 本函数只声明「不可归因」，⛔ 不声明「判据没问题」。
    """
    if not (code or "").strip() or not inputs:
        return None
    lines = [code.rstrip("\n"), "", "_OUT = []", "",
             "def _v188_probe(*a):",
             "    try:",
             f"        _OUT.append(repr({fn}(*a)))",
             "    except Exception as e:",
             "        _OUT.append('EXC:' + type(e).__name__)"]
    for a in inputs:
        lines.append("_v188_probe(*%r)" % (tuple(a),))
    lines.append(f"print({_MARK!r} + __import__('json').dumps(_OUT))")
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
                outs = json.loads(ln[len(_MARK):])
            except json.JSONDecodeError:
                return None
            return isinstance(outs, list) and len(set(outs)) <= 1
    return None


#: 哪些算子的字面量是**装置渲染的规格参数**（不是模型写下的期望值）。
#: 键 = 算子，值 = 渲染出来的参考实现里那些**形参名**。
#: ⛔ 这张表由装置维护：模型无权声明某个名字是「规格」。
SPEC_PARAMS: dict[str, tuple[str, ...]] = {
    "NUMFMT": ("sep", "dec", "neg"),
}


def spec_params(kind: str) -> tuple[str, ...]:
    """该算子的规格参数名（`metacrit.admit(..., params=...)` 的入参）。"""
    return SPEC_PARAMS.get(str(kind or ""), ())


def verdict(*, built: bool, pass_oracle: bool, oracle_const: bool | None,
            fail: str = "") -> str:
    """一格判据读数的**三值**（V188，§256）。`UNDECIDED` 优先于 PASS/FAIL。

    ## 为什么要有第三值

    二值的问题不是「不准」，是**把两种不同的东西记成同一种**：

    · 「判据在正确实现上失败」—— 关于判据的结论
    · 「正确实现在这组输入上没有任何产出差异」—— 关于**池**的结论，判据无话可说

    V187 C 臂 `g7e1`：模型给 `parse_flag` 选了 `str` 池，池里每个值都返回 `None`。
    判据的断言确实失败了，但失败的原因不在断言里。二值口径把这一格记成
    「判据写错」，而那是不成立的归因（纪律 10：数值对得上 ≠ 解释对）。

    ⛔ 本函数**不判对错**、不进 `mw.verify`：判分仍是同一把尺，这里只改
       「这次判定算不算关于判据的证据」。
    ⚠️ 已知不可分辨：常量实现（stub）与「正确实现落在池外」在装置眼里同一个现象。
       所以 `UNDECIDED` 只声明**不可归因**，⛔ 不声明「判据没问题」。
    """
    if not built:
        return fail or "NOT_BUILT"
    if oracle_const:
        return "UNDECIDED"
    return "PASS" if pass_oracle else "FAIL"



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
    elif kind == "NUMFMT":
        sep = SEPS[str(slots.get("sep") or "")]
        dec = DECS[str(slots.get("dec") or "")]
        neg = NEGS[str(slots.get("neg") or "")]
        body = (
            "    for x, in INPUTS:\n"
            f"        got = {fn}(x)\n"
            f"        want = _numfmt(x, {sep!r}, {dec!r}, {neg!r})\n"
            "        if got != want:\n"
            '            raise AssertionError(f"NUMFMT: {x!r} ⇒ {got!r} ≠ {want!r}")'
        )
        pre = _NUMFMT.strip("\n")
    elif kind == "INVARIANT":
        body = (
            "    seen = []\n"
            "    for x, in INPUTS:\n"
            f"        got = {fn}(x)\n"
            "        for how in " + repr(tuple(_NORMS)) + ":\n"
            f"            alt = {fn}(_norm_v187(x, how))\n"
            "            if repr(got) != repr(alt):\n"
            '                raise AssertionError('
            'f"INVARIANT: {x!r} ⇒ {got!r} 但 {how} 后 ⇒ {alt!r}")\n'
            "        seen.append(repr(got))\n"
            "    if len(set(seen)) < 2:\n"
            '        raise AssertionError("INVARIANT: 所有输入同一个输出 —— 判据不承载信息")'
        )
        pre = _NORM.strip("\n")
    else:  # pragma: no cover - validate 已拦住
        return ""
    return _render(pre, inputs, body)


def skeleton(n_args: int = 1, kind: str | None = None,
             allow: dict | None = None) -> str:
    """给模型看的 JSON 骨架。⛔ 不含任何题目信息。

    ⭐ V187：`types` 的占位**重复 `n_args` 次** —— V186 实测模型写
    `types: ["int","float"]`（两个类型、还编了个 `float`），骨架里只有一个占位时它不会
    自己数参数。骨架的**长度**就是它看到的约束（V185 已证：话不是约束）。
    ⛔ 槽的可选值全部列出，但那只是陈列（与 `catalogue()` 同一批事实），不是推荐。
    """
    #: `allow` 非 None ⇒ 只列剩下的（V189 收窄臂）。⚠️ 用 `is not None` 而不是真值判断：
    #: 空集也要能表达 —— 「一个都不剩」是有效状态（`EXHAUSTED`），⛔ 不是「没给约束」。
    kinds = [kind] if kind else (sorted(allow) if allow is not None else sorted(OPS))
    n = n_args if isinstance(n_args, int) and n_args >= 1 else 1
    tslot = '"<' + "|".join(TYPES) + '>"'
    return ('{"kind": "<' + "|".join(kinds) + '>", '
            '"types": [' + ", ".join([tslot] * n) + '], '
            '"decode": "<' + "|".join(k for k in DECODERS if k) + '>", '
            '"keep": "<' + "|".join(k for k in KEEPS if k) + '>", '
            '"sep": "<' + "|".join(k for k in SEPS if k) + '>", '
            '"dec": "<' + "|".join(k for k in DECS if k) + '>", '
            '"neg": "<' + "|".join(k for k in NEGS if k) + '>"}')


def ops_for(n_args: int) -> dict:
    """**候选集过滤**：只留下 arity 相符的算子（V186 的 arity 硬约束本体）。

    ⛔ 与「告诉模型它有几个参数」的区别：那是提示，模型可以不理（V185 实测：它把二元算子
       换成了另一个二元算子）；这里是**它选不到**。
    ⚠️ `n_args < 0`（读不出 `def`）⇒ 退回全量 —— 签名未知时不许假装有约束，
       否则「过滤」变成了一个恒真动作（纪律 5：断言恒真 = 假绿）。
    """
    if n_args < 0:
        return dict(OPS)
    return {k: v for k, v in OPS.items() if v["arity"] == n_args}


def catalogue(n_args: int | None = None, allow: dict | None = None) -> str:
    """算子目录（给 prompt 用）：每个算子一行，含 arity 与槽的可选值。

    `n_args` 给了 ⇒ 只列该 arity 的算子（V186 臂用）；`None` ⇒ 全量（`retry_hint` 用）。
    `allow` 非 None ⇒ **只列这个集合**（V189 的收窄臂）—— 它压过 `n_args`。
    ⚠️ 空 dict 是有效输入（一个都不剩），⛔ 不许 fallback 回全量。
    """
    src = allow if allow is not None else (OPS if n_args is None else ops_for(n_args))
    out = []
    for k, spec in src.items():
        extra = ""
        for key in spec["slots"]:
            table = SLOT_TABLES[key]
            extra += f"；{key} 可选 {[x for x in table if x]}"
        out.append(f"- {k}（{spec['arity']} 个参数）：{spec['what']}{extra}")
    return "\n".join(out)
